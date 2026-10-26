from __future__ import annotations

import json
import logging
import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .openai_client import ChatModel
from .prompts import SYSTEM_PROMPT
from .errors import new_error_id, user_error_text
from .activity import tool_label

logger = logging.getLogger(__name__)


class AgentTurnPaused(RuntimeError):
    pass


@dataclass(frozen=True)
class ToolContext:
    user_message: str
    turn_index: int


class AgentSession:
    def __init__(self, model: ChatModel, toolbox, *, max_tool_rounds: int = 8, classifier_model: ChatModel | None = None, accepts_images: bool = False, instructions: str = ""):
        self._accepts_images = accepts_images
        self._model = model
        self._classifier_model = classifier_model
        self._toolbox = toolbox
        self._max_tool_rounds = max_tool_rounds
        self._system_prompt = SYSTEM_PROMPT
        if instructions.strip():
            self._system_prompt += "\n\n用户的写作偏好：\n" + instructions.strip()
        if getattr(toolbox, "allow_text_submission", True) is False:
            self._system_prompt += (
                "\n\n当前网页仅允许用户在提示词面板点击提交按钮来提交生成任务。"
                "聊天中的确认、同意或生成要求只能用于准备或调整方案，不能提交任务。"
                "准备方案后提示用户在面板点击提交；禁止调用任何 submit 工具或声称任务已经提交。"
            )
        self._messages: list[dict[str, Any]] = [
            {"role": "system", "content": self._system_prompt}
        ]
        pending = getattr(toolbox, "pending", None)
        pending_job_id = str(getattr(pending, "submitted_job_id", "") or "").strip()
        if pending_job_id:
            self._messages.append({
                "role": "system",
                "content": (
                    "内部任务上下文：本对话最近已提交的真实诗词任务 job_id："
                    f"{pending_job_id}。用户询问该任务的进度或生成正文时，"
                    "必须调用 get_poetry_job 查询，不要要求用户再次提供 job_id。"
                ),
            })
        self._turn_index = 0
        self._submitted_jobs: list[dict[str, Any]] = []

    @property
    def messages(self) -> tuple[dict[str, Any], ...]:
        return tuple(self._messages)

    @property
    def submitted_jobs(self) -> tuple[dict[str, Any], ...]:
        return tuple(self._submitted_jobs)

    def reset(self) -> None:
        self._messages = [{"role": "system", "content": self._system_prompt}]
        self._turn_index = 0
        self._toolbox.reset()

    def load_history(self, messages: list[Mapping[str, Any]]) -> None:
        """恢复已持久化的用户/助手消息，系统提示仍由当前版本控制。"""
        self._messages = [{"role": "system", "content": self._system_prompt}]
        submitted_jobs: list[str] = []
        for message in messages:
            role = message.get("role")
            content = message.get("content")
            if role == "user" and message.get("images"):
                if self._accepts_images:
                    self._messages.append({"role": role, "content": self._image_content(str(content or ""), message["images"])})
                    continue
                content = str(content or "") + "\n图片识别资料：\n" + str(message.get("image_description") or "此前图片未完成识别。")
            if role in {"user", "assistant"} and isinstance(content, str) and content:
                self._messages.append({"role": role, "content": content})
            job_id = str(message.get("job_id") or "").strip()
            if job_id:
                submitted_jobs.append(job_id)
        if submitted_jobs:
            # 网页按钮提交任务不经过当前 Agent turn；把持久化的任务标识
            # 作为内部上下文恢复，模型才能在后续追问时查询真实成稿。
            ids = ", ".join(dict.fromkeys(submitted_jobs))
            self._messages.append({
                "role": "system",
                "content": (
                    "内部任务上下文：以下是本对话中最近已提交的真实诗词任务 job_id："
                    f"{ids}。用户询问刚才生成的作品、进度、点评或核对时，"
                    "必须优先调用 get_poetry_job 查询对应任务；不要要求用户再次提供 job_id。"
                ),
            })
        pending = getattr(self._toolbox, "pending", None)
        pending_job_id = str(getattr(pending, "submitted_job_id", "") or "").strip()
        if pending_job_id and pending_job_id not in submitted_jobs:
            self._messages.append({
                "role": "system",
                "content": (
                    "内部任务上下文：本对话最近已提交的真实诗词任务 job_id："
                    f"{pending_job_id}。用户询问该任务的进度或生成正文时，"
                    "必须调用 get_poetry_job 查询，不要要求用户再次提供 job_id。"
                ),
            })
        self._turn_index = sum(1 for item in self._messages if item["role"] == "user")

    def respond(self, user_message: str) -> str:
        text = user_message.strip()
        if not text:
            raise ValueError("用户消息不能为空")
        self._turn_index += 1
        self._submitted_jobs = []
        self._last_tool_error: dict[str, str] | None = None
        context = ToolContext(text, self._turn_index)
        self._messages.append({"role": "user", "content": text})
        self._append_task_classification(text)
        _, prepared_reply = self._prepare_confirmed_rewrite_from_context(text, context)
        if prepared_reply:
            return prepared_reply

        for _ in range(self._max_tool_rounds):
            message = self._model.complete(self._messages, self._toolbox.schemas())
            assistant = self._normalize_assistant_message(message)
            assistant = self._expand_query_calls(assistant)
            self._messages.append(assistant)
            tool_calls = assistant.get("tool_calls") or []
            if not tool_calls:
                if self._retry_private_reply(assistant):
                    continue
                content = assistant.get("content")
                submit_fallback = getattr(
                    self._toolbox, "submit_pending_if_confirmed", None
                )
                fallback = submit_fallback(context) if submit_fallback else None
                if fallback:
                    self._submitted_jobs.append(fallback)
                    job_id = fallback.get("job_id")
                    status = fallback.get("status", "queued")
                    suffix = f"\n\n任务已实际提交，job_id：{job_id}，当前状态：{status}。"
                    content = (content or "") + suffix
                content = content if isinstance(content, str) and content else user_error_text("AGT-TURN-EMPTY")
                content = self._attach_tool_error_reference(content)
                return content
            for call in tool_calls:
                result = self._execute_tool_call(call, context)
                self._messages.append(self._model_tool_result(result))
                self._append_tool_error_guidance(call, result)
        return self._unfinished_reply()

    @staticmethod
    def _image_content(text, images):
        return [{"type": "text", "text": text}] + [
            {"type": "image_url", "image_url": {"url": image["data_url"]}} for image in images
        ]

    def respond_stream(self, user_message: str, cancellation_event=None, images=None):
        """逐轮消费上游 SSE；工具只在参数完整后执行。"""
        text = user_message.strip()
        if not text:
            raise ValueError("用户消息不能为空")
        self._turn_index += 1
        self._submitted_jobs = []
        self._last_tool_error = None
        context = ToolContext(text, self._turn_index)
        self._messages.append({"role": "user", "content": self._image_content(text, images) if images else text})
        self._append_task_classification(text)

        prepared_reply = yield from self._iter_confirmed_rewrite_from_context(text, context)
        if prepared_reply:
            yield {"event_type": "assistant.delta", "payload": {"text": prepared_reply}}
            yield {"event_type": "turn.completed", "payload": {"reply": prepared_reply, "jobs": []}}
            return

        for _ in range(self._max_tool_rounds):
            if cancellation_event is not None and cancellation_event.is_set():
                raise AgentTurnPaused("Agent turn paused")
            stream_method = getattr(self._model, "stream", None)
            content_parts: list[str] = []
            reasoning_parts: list[str] = []
            tool_name_parts: dict[int, str] = {}
            announced_indexes: set[int] = set()
            announced_calls: list[tuple[str, str]] = []
            # Never buffer a whole model round after a tool failure. Suppress
            # only text that actually matches an internal operational message.
            suppress_previews = False
            if callable(stream_method):
                tool_calls: list[dict[str, Any]] = []
                try:
                    model_events = stream_method(self._messages, self._toolbox.schemas(), cancellation_event=cancellation_event)
                except TypeError:
                    model_events = stream_method(self._messages, self._toolbox.schemas())
                for event in model_events:
                    if cancellation_event is not None and cancellation_event.is_set():
                        raise AgentTurnPaused("Agent turn paused")
                    event_type = event.get("type")
                    if event_type == "assistant.delta":
                        piece = str(event.get("text") or "")
                        content_parts.append(piece)
                        if not suppress_previews and self._contains_private_reply("".join(content_parts)):
                            suppress_previews = True
                            yield {"event_type": "assistant.preview.reset", "payload": {}}
                        if piece and not suppress_previews:
                            yield {"event_type": "assistant.preview.delta", "payload": {"text": piece}}
                    elif event_type == "assistant.reasoning.delta":
                        reasoning_parts.append(str(event.get("text") or ""))
                    elif event_type in {"tool.name.delta", "tool.arguments.delta"}:
                        if event_type == "tool.name.delta":
                            index = int(event.get("index", 0))
                            tool_name_parts[index] = tool_name_parts.get(index, "") + str(event.get("name") or "")
                            name = tool_name_parts[index]
                            if name in {"prepare_generation", "prepare_partial_generation", "prepare_rewrite"} and index not in announced_indexes:
                                call_id = str(uuid.uuid4())
                                announced_indexes.add(index)
                                announced_calls.append((call_id, name))
                                yield {"event_type": "tool.started", "payload": {
                                    "tool_call_id": call_id, "name": name, "label": tool_label(name)}}
                        yield {"event_type": "tool.delta", "payload": dict(event)}
                    elif event_type == "turn.finished":
                        tool_calls = list(event.get("tool_calls") or [])
                    elif event.get("error"):
                        raise RuntimeError(str(event["error"]))
                message = {"role": "assistant", "content": "".join(content_parts) or None}
                if reasoning_parts:
                    message["reasoning_content"] = "".join(reasoning_parts)
                if tool_calls:
                    message["tool_calls"] = tool_calls
            else:
                message = self._model.complete(self._messages, self._toolbox.schemas())

            assistant = self._normalize_assistant_message(message)
            assistant = self._expand_query_calls(assistant)
            if cancellation_event is not None and cancellation_event.is_set():
                raise AgentTurnPaused("Agent turn paused")
            self._messages.append(assistant)
            calls = assistant.get("tool_calls") or []
            if not calls:
                if self._retry_private_reply(assistant):
                    if content_parts:
                        yield {"event_type": "assistant.preview.reset", "payload": {}}
                    continue
                content = assistant.get("content")
                content = content if isinstance(content, str) else ""
                if not content:
                    content = user_error_text("AGT-TURN-EMPTY")
                raw_content = content
                content = self._attach_tool_error_reference(content)
                # Stream-capable models already showed these pieces as
                # previews. Commit them without replaying the text; only send
                # a fallback/guard suffix that was added after streaming.
                pieces = ([] if content_parts and not suppress_previews else [raw_content]) if callable(stream_method) else [raw_content]
                appended_reference = content[len(raw_content):] if content.startswith(raw_content) else ""
                if appended_reference:
                    pieces.append(appended_reference)
                for piece in pieces:
                    yield {"event_type": "assistant.delta", "payload": {"text": piece}}
                submit_fallback = getattr(self._toolbox, "submit_pending_if_confirmed", None)
                fallback = submit_fallback(context) if submit_fallback else None
                if fallback:
                    self._submitted_jobs.append(fallback)
                    yield {"event_type": "job.submitted", "payload": fallback}
                    suffix = f"\n\n任务已实际提交，job_id：{fallback.get('job_id')}，当前状态：{fallback.get('status', 'queued')}。"
                    content = (content or "") + suffix
                    yield {"event_type": "assistant.delta", "payload": {"text": suffix}}
                yield {"event_type": "turn.completed", "payload": {"reply": content, "jobs": list(self._submitted_jobs)}}
                return

            narration = assistant.get("content")
            if isinstance(narration, str) and narration.strip() and not self._contains_private_reply(narration):
                yield {"event_type": "assistant.activity", "payload": {"text": narration}}
            if content_parts:
                yield {"event_type": "assistant.preview.reset", "payload": {}}
            for call in calls:
                if cancellation_event is not None and cancellation_event.is_set():
                    raise AgentTurnPaused("Agent turn paused")
                function = call.get("function") or {}
                name = str(function.get("name") or "")
                announced = next((item for item in announced_calls if item[1] == name), None)
                call_id = announced[0] if announced else str(uuid.uuid4())
                if announced:
                    announced_calls.remove(announced)
                label = tool_label(name, function.get("arguments"))
                if not announced:
                    yield {"event_type": "tool.started", "payload": {"tool_call_id": call_id, "name": name, "label": label}}
                submitted_before = len(self._submitted_jobs)
                result = self._execute_tool_call(call, context)
                try:
                    output = json.loads(result["content"])
                except (KeyError, TypeError, json.JSONDecodeError):
                    error_id = new_error_id()
                    self._last_tool_error = {"error_code": "AGT-TOOL-OUTPUT", "error_id": error_id}
                    logger.exception("Agent tool output malformed [AGT-TOOL-OUTPUT] error_id=%s", error_id)
                    output = {"ok": False, "error_code": "AGT-TOOL-OUTPUT", "error_id": error_id, "error": "工具结果无法解析"}
                yield {"event_type": "tool.completed", "payload": {"tool_call_id": call_id, "name": name, "label": label, "output": output}}
                for job in self._submitted_jobs[submitted_before:]:
                    yield {"event_type": "job.submitted", "payload": job}
                self._messages.append(self._model_tool_result(result))
                self._append_tool_error_guidance(call, result)
                if output.get("ok") is False:
                    yield {"event_type": "assistant.activity", "payload": {"text": "正在核对查询条件并继续处理。"}}
        reply = self._unfinished_reply()
        yield {"event_type": "assistant.delta", "payload": {"text": reply}}
        yield {"event_type": "turn.completed", "payload": {"reply": reply, "jobs": list(self._submitted_jobs)}}

    def _retry_private_reply(self, assistant: Mapping[str, Any]) -> bool:
        """Keep operational instructions out of both persisted and streamed replies."""
        content = str(assistant.get("content") or "")
        if not self._contains_private_reply(content):
            return False
        self._messages.pop()
        self._messages.append({
            "role": "system",
            "content": "继续完成当前用户请求。回复仅包含已核实的结果，或完成请求所缺少的具体信息。",
        })
        return True

    @staticmethod
    def _contains_private_reply(content: str) -> bool:
        return bool(re.search(
            r"内部(?:指令|排障|处理指令|任务路由)|排障过程|工具(?:报错|调用失败)|"
            r"修正参数.{0,8}(?:调用|重试)|重新调用准备工具|严禁引用|禁止.{0,12}复述",
            content,
        ))

    def _unfinished_reply(self) -> str:
        reply = user_error_text("AGT-TURN-LIMIT")
        self._messages.append({"role": "assistant", "content": reply})
        return reply

    def _attach_tool_error_reference(self, content: str) -> str:
        if self._last_tool_error and re.search(r"失败|未能|无法|错误|不可用|没能|不成功", content):
            code = self._last_tool_error["error_code"]
            error_id = self._last_tool_error["error_id"]
            return f"{content.rstrip()}\n\n错误码：{code}，追踪号：{error_id}。"
        return content

    def _prepare_confirmed_rewrite_from_context(self, user_message: str, context: ToolContext):
        stream = self._iter_confirmed_rewrite_from_context(user_message, context)
        events = []
        while True:
            try:
                events.append(next(stream))
            except StopIteration as result:
                return events, result.value

    def _iter_confirmed_rewrite_from_context(self, user_message: str, context: ToolContext):
        """Resolve an explicit confirmation of a quoted rewrite target without another model turn."""
        # 未提交的方案应保持为可编辑草稿。后续消息可能是在讨论、补充要求，
        # 不能再被历史中的确认语境截走并重复返回“已准备好”。
        pending = getattr(self._toolbox, "pending", None)
        if pending is not None and not getattr(pending, "submitted_job_id", None):
            return None
        current_index = next((
            index for index in range(len(self._messages) - 1, -1, -1)
            if self._messages[index].get("role") == "user"
            and self._messages[index].get("content") == user_message
        ), len(self._messages))
        previous = [
            item for item in self._messages[:current_index]
            if item.get("role") in {"user", "assistant"} and isinstance(item.get("content"), str)
        ]
        normalized_message = re.sub(r"\s+", "", user_message)
        if not any(word in normalized_message for word in ("改写", "重写", "修改")):
            return None

        uuid_pattern = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
        current_ids = list(dict.fromkeys(re.findall(uuid_pattern, user_message)))
        if len(current_ids) > 1:
            return None
        source_id = current_ids[0] if len(current_ids) == 1 else None

        def quoted_targets(text: str) -> list[str]:
            values = []
            for match in re.finditer(r"“([^”]{2,160})”|「([^」]{2,160})」|\"([^\"]{2,160})\"", text):
                value = next((part for part in match.groups() if part is not None), "").strip()
                if re.search(r"[，,。.！？!?；;]", value):
                    values.append(value)
            return list(dict.fromkeys(values))

        targets = quoted_targets(user_message)
        matched_context = None
        for item in reversed(previous):
            content = item["content"]
            ids = list(dict.fromkeys(re.findall(uuid_pattern, content)))
            quotes = quoted_targets(content)
            if ids:
                matched_context = (ids, quotes, content)
                break

        if source_id is None and matched_context and len(matched_context[0]) == 1:
            source_id = matched_context[0][0]
        if not targets and matched_context and len(matched_context[1]) == 1:
            targets = matched_context[1]
        if not targets and re.search(r"这(?:两|几|些|一)句|刚才|上面", user_message):
            for item in reversed(previous):
                quotes = quoted_targets(item["content"])
                if len(quotes) == 1:
                    targets = quotes
                    break

        affirmative = bool(re.search(r"(?:^|[，。！？、\s])(?:对(?:啊|呀)?|是(?:的)?|就是|没错|正是)", normalized_message))
        explicit_target = bool(targets and source_id and (current_ids or quoted_targets(user_message)))
        referenced_target = bool(
            source_id and len(targets) == 1
            and re.search(r"这(?:两|几|些|一)句|刚才|上面", user_message)
        )
        confirmed_context = bool(
            affirmative and matched_context and len(matched_context[0]) == 1
            and len(matched_context[1]) == 1
            and any(mark in matched_context[2] for mark in ("？", "?", "是否", "还是"))
        )
        if not source_id or not targets or len(targets) != 1 or not (explicit_target or confirmed_context or referenced_target):
            return None

        target_text = targets[0]
        target_parts = [part.strip() for part in re.split(r"[，,。.！？；;!?\r\n]+", target_text) if part.strip()]
        if not target_parts:
            return None

        def run_tool(name: str, arguments: dict[str, Any]):
            call_id = str(uuid.uuid4())
            call = {
                "id": call_id,
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(arguments, ensure_ascii=False)},
            }
            self._messages.append({"role": "assistant", "content": None, "tool_calls": [call]})
            label = tool_label(name, arguments)
            yield {"event_type": "tool.started", "payload": {"tool_call_id": call_id, "name": name, "label": label}}
            result = self._execute_tool_call(call, context)
            try:
                output = json.loads(result["content"])
            except (KeyError, TypeError, json.JSONDecodeError):
                error_id = new_error_id()
                self._last_tool_error = {"error_code": "AGT-TOOL-OUTPUT", "error_id": error_id}
                logger.exception("Agent tool output malformed [AGT-TOOL-OUTPUT] error_id=%s", error_id)
                output = {"ok": False, "error_code": "AGT-TOOL-OUTPUT", "error_id": error_id, "error": "工具结果无法解析"}
            yield {"event_type": "tool.completed", "payload": {"tool_call_id": call_id, "name": name, "label": label, "output": output}}
            self._messages.append(self._model_tool_result(result))
            self._append_tool_error_guidance(call, result)
            return output

        source_output = yield from run_tool("get_poem_source", {"resource_id": source_id})
        if source_output.get("ok") is not True:
            return None
        source_result = source_output.get("result") or {}
        sources = source_result.get("sources") if isinstance(source_result, dict) else None
        candidates = sources if isinstance(sources, list) else [source_result]

        def normalize_line(value: Any) -> str:
            return re.sub(r"[\s，,。.！？；;!?、]", "", str(value or ""))

        matches = []
        for source in candidates:
            lines = source.get("lines") if isinstance(source, dict) else None
            if not isinstance(lines, list):
                continue
            selected = []
            for part in target_parts:
                found = [
                    line for line in lines
                    if normalize_line(part) in {
                        normalize_line(line.get("text")),
                        normalize_line(line.get("source_text")),
                    }
                ]
                if len(found) != 1:
                    selected = []
                    break
                selected.append(found[0])
            if selected and len({line["line_id"] for line in selected}) == len(selected):
                matches.append((source, selected))
        if len(matches) != 1:
            return None

        source, selected = matches[0]
        prep_output = yield from run_tool("prepare_rewrite", {
            "source_ref": source["source_ref"],
            "target_line_ids": [line["line_id"] for line in selected],
            "expected_target_lines": [
                {"line_id": line["line_id"], "text": line["text"]}
                for line in selected
            ],
            "requirement": self._confirmed_rewrite_requirement(previous + [{"content": user_message}]),
            "candidate_count": 1,
        })
        if prep_output.get("ok") is not True:
            return None
        reply = "已按您确认的作品和原句准备好改写方案，可在面板中检查并提交。"
        self._messages.append({"role": "assistant", "content": reply})
        return reply

    @staticmethod
    def _confirmed_rewrite_requirement(previous: list[dict[str, Any]]) -> str:
        history = "\n".join(item["content"] for item in previous)
        directions = []
        if "精炼" in history or "简练" in history:
            directions.append("表达精炼")
        if "画面感" in history or "画面" in history:
            directions.append("增强动作画面感")
        if "犹豫" in history or "留恋" in history or "欲走还留" in history:
            directions.append("体现人物欲走还留时的犹豫与留恋")
        direction = "；".join(directions) if directions else "遵循本对话中已经明确的修改意图"
        return (
            f"{direction}。只改写指定原句；保持原作词牌、韵脚、情感上下文及所有非目标句不变。"
        )

    @staticmethod
    def _model_tool_result(result):
        if result.get("name") not in {"get_poem_source", "number_external_poem", "prepare_generation", "prepare_partial_generation", "prepare_rewrite"}:
            return result
        body = json.loads(result["content"])
        hidden = {"source_poem_id", "source_version", "proposal_id", "submitted_job_id", "source_arguments", "error_id"}
        def project(value):
            if isinstance(value, dict):
                return {key: project(item) for key, item in value.items() if key not in hidden}
            if isinstance(value, list):
                return [project(item) for item in value]
            return value
        return {**result, "content": json.dumps(project(body), ensure_ascii=False)}

    def _append_task_classification(self, user_message: str) -> None:
        """Classify only the current request before the main model sees tools."""
        if self._classifier_model is None:
            return
        messages = [
            {"role": "system", "content": (
                "你是诗词任务路由器。只根据下一条用户消息判断任务类型。"
                "用户提供一两句作为固定内容、要求补全其他句子时输出 partial_generate；"
                "用户要求改写、替换已存在作品中的指定句子时输出 rewrite；"
                "用户要求从零创作整首作品时输出 generate。不得把 rewrite 归为 partial_generate。只能输出 JSON："
                "{\"task_type\":\"generate\"}、{\"task_type\":\"partial_generate\"} 或 {\"task_type\":\"rewrite\"}。"
            )},
            {"role": "user", "content": user_message},
        ]
        try:
            result = self._classifier_model.complete(messages, [])
            raw = str(result.get("content") or "").strip()
            try:
                parsed = json.loads(raw)
                task_type = parsed.get("task_type")
            except json.JSONDecodeError:
                match = re.search(r'"task_type"\s*:\s*"(generate|partial_generate|rewrite)"', raw)
                task_type = match.group(1) if match else None
        except (Exception,):
            task_type = None
        if task_type not in {"generate", "partial_generate", "rewrite"}:
            return
        self._messages.append({
            "role": "system",
            "content": (
                f"内部任务路由结果：{task_type}。必须使用对应工具；"
                "partial_generate 仅表示补完：只将用户提供并要求保留的句子写入 fixed_lines，空缺句由模型补齐；"
                "rewrite 表示修改已有作品中的指定句，先读来源再调用 prepare_rewrite。"
            ),
        })

    @staticmethod
    def _normalize_assistant_message(message: Mapping[str, Any]) -> dict[str, Any]:
        allowed = {
            "content",
            "tool_calls",
            "function_call",
            "reasoning_content",
            "refusal",
            "annotations",
            "audio",
        }
        normalized: dict[str, Any] = {"role": "assistant"}
        normalized.update({key: value for key, value in message.items() if key in allowed})
        normalized.setdefault("content", None)
        return normalized

    def _expand_query_calls(self, assistant):
        planner = getattr(self._toolbox, "query_prerequisites", None)
        calls = assistant.get("tool_calls") or []
        if not planner or not calls:
            return assistant
        expanded, scheduled = [], set()
        for call in calls:
            function = call.get("function") or {}
            name = function.get("name")
            try:
                arguments = function.get("arguments") or {}
                arguments = json.loads(arguments) if isinstance(arguments, str) else arguments
                prerequisites = planner(name, arguments) if isinstance(arguments, dict) else []
            except (TypeError, ValueError):
                prerequisites = []
            for tool_name, payload in prerequisites:
                key = (tool_name, json.dumps(payload, ensure_ascii=False, sort_keys=True))
                if key not in scheduled:
                    expanded.append({"id": str(uuid.uuid4()), "type": "function", "function": {
                        "name": tool_name, "arguments": json.dumps(payload, ensure_ascii=False)}})
                    scheduled.add(key)
            expanded.append(call)
            if isinstance(arguments, dict):
                scheduled.add((name, json.dumps(arguments, ensure_ascii=False, sort_keys=True)))
        return {**assistant, "tool_calls": expanded}

    def _execute_tool_call(self, call: Mapping[str, Any], context: ToolContext) -> dict:
        call_id = str(call.get("id") or "missing-tool-call-id")
        function = call.get("function") or {}
        name = str(function.get("name") or "")
        raw_arguments = function.get("arguments") or "{}"
        try:
            arguments = (
                json.loads(raw_arguments)
                if isinstance(raw_arguments, str)
                else dict(raw_arguments)
            )
            if not isinstance(arguments, dict):
                raise ValueError("工具参数必须是 JSON 对象")
            output = self._toolbox.execute(name, arguments, context)
            self._last_tool_error = None
            if name in {"submit_generation", "submit_partial_generation", "submit_rewrite"} and isinstance(output, dict) and output.get("job_id"):
                self._submitted_jobs.append(output)
            body = {"ok": True, "result": output}
        except json.JSONDecodeError as exc:
            error_code = "AGT-TOOL-ARGUMENTS"
            error_id = new_error_id()
            self._last_tool_error = {"error_code": error_code, "error_id": error_id}
            logger.warning("Agent tool arguments rejected [%s] error_id=%s tool=%s: %s", error_code, error_id, name, exc)
            body = {"ok": False, "error_code": error_code, "error_id": error_id, "error": str(exc)}
        except ValueError as exc:
            error_code = "AGT-TOOL-NOT-FOUND" if str(exc).startswith("未知工具") else "AGT-TOOL-VALIDATION"
            error_id = new_error_id()
            self._last_tool_error = {"error_code": error_code, "error_id": error_id}
            logger.warning("Agent tool rejected [%s] error_id=%s tool=%s: %s", error_code, error_id, name, exc)
            body = {"ok": False, "error_code": error_code, "error_id": error_id, "error": str(exc)}
        except Exception as exc:
            error_code = "AGT-TOOL-EXECUTION"
            error_id = new_error_id()
            self._last_tool_error = {"error_code": error_code, "error_id": error_id}
            logger.exception("Agent tool crashed [%s] error_id=%s tool=%s", error_code, error_id, name)
            body = {"ok": False, "error_code": error_code, "error_id": error_id, "error": str(exc)}
        return {
            "role": "tool",
            "tool_call_id": call_id,
            "name": name,
            "content": json.dumps(body, ensure_ascii=False),
        }

    def _append_tool_error_guidance(self, call: Mapping[str, Any], result: Mapping[str, Any]) -> None:
        name = str((call.get("function") or {}).get("name") or "")
        if name not in {"prepare_generation", "prepare_partial_generation", "prepare_rewrite"}:
            return
        try:
            output = json.loads(str(result["content"]))
        except (KeyError, TypeError, json.JSONDecodeError):
            return
        if output.get("ok") is False:
            self._messages.append({
                "role": "system",
                "content": (
                    "依据上一条工具结果继续准备方案。"
                    "partial_generate 仅补完空缺，只传固定句 fixed_lines；rewrite 必须使用 prepare_rewrite，"
                    "传 source_ref、target_line_ids、expected_target_lines，从编号原作复制短引用和目标原句；"
                    "不得把已有作品重写请求交给 prepare_partial_generation。"
                    "补完必须传 meter_type、form_name、fixed_lines，并留出至少一句空缺。不要重复上一轮缺少参数的调用。"
                    "排律的 form_name 只能是五言排律或七言排律，句数单独放入整数 num_lines。"
                    "只有准备成功后才告知方案已准备。"
                ),
            })

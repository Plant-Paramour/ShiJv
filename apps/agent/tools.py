from __future__ import annotations

import json
import re
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from shiju.contracts import GeneratePoemRequest, RewritePoemRequest
from shiju.data import MeterTemplateRepository, RhymeLexicon
from shiju.rewriting.parser import parse_poem
from shiju.rewriting.validation import validate_rewrite_context
from shiju.rewriting.source import numbered_source, select_rewrite_lines, poem_body
from shiju.generation.validation import validate_partial_generation_request

from .framework import ToolContext
from .jobs import PoetryApiError, PoetryJobs


RHYME_BOOKS = {
    "Xinyun": "中华新韵",
    "Pinshui": "平水韵",
    "Cilin": "词林正韵",
    "Tongyun": "中华通韵",
}


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[[Mapping[str, Any], ToolContext], Any]

    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


@dataclass
class PendingProposal:
    id: str
    kind: str
    payload: dict[str, Any]
    prepared_turn: int
    submitted_job_id: str | None = None


class AgentToolbox:
    def __init__(self, project_root: str | Path, jobs: PoetryJobs, *, allow_text_submission: bool = True):
        self._root = Path(project_root)
        self._jobs = jobs
        self.allow_text_submission = allow_text_submission
        self._lexicons: dict[str, RhymeLexicon] = {}
        self._discovered_meters: set[str] = set()
        self._discovered_parts: set[tuple[str, str]] = set()
        self._rewrite_sources: dict[str, dict] = {}
        self._source_refs: dict[str, dict] = {}
        self._pending: PendingProposal | None = None
        loader = getattr(jobs, "load_pending_proposal", None)
        saved = loader() if loader else None
        if saved:
            self._pending = PendingProposal(**saved)
        self._tools = {tool.name: tool for tool in self._build_tools()}

    @property
    def pending(self) -> PendingProposal | None:
        return self._pending

    def reset(self) -> None:
        self._pending = None

    def _save_pending(self) -> None:
        saver = getattr(self._jobs, "save_pending_proposal", None)
        if saver and self._pending:
            saver(
                {
                    "id": self._pending.id,
                    "kind": self._pending.kind,
                    "payload": self._pending.payload,
                    "prepared_turn": self._pending.prepared_turn,
                    "submitted_job_id": self._pending.submitted_job_id,
                }
            )

    def submit_pending_if_confirmed(self, context: ToolContext) -> dict | None:
        """在模型漏调 submit 工具时，由宿主按同一确认门禁兜底提交。"""
        if not self.allow_text_submission:
            return None
        proposal = self._pending
        if proposal is None or proposal.submitted_job_id:
            return None
        later_confirmation = (
            proposal.prepared_turn < context.turn_index
            and _is_explicit_confirmation(context.user_message)
        )
        if not later_confirmation:
            return None
        if proposal.kind == "generate":
            return self._submit_generation({"proposal_id": proposal.id}, context)
        if proposal.kind == "partial_generate":
            return self._submit_partial_generation({"proposal_id": proposal.id}, context)
        if proposal.kind == "rewrite":
            return self._submit_rewrite({"proposal_id": proposal.id}, context)
        return None

    def schemas(self) -> list[dict[str, Any]]:
        schemas = [
            tool.schema() for tool in self._tools.values()
            if self.allow_text_submission or tool.name not in {"submit_generation", "submit_partial_generation", "submit_rewrite"}
        ]
        for schema in schemas:
            function = schema["function"]
            if function["name"] == "get_poem_source":
                function["parameters"] = _object_schema({
                    "resource_id": {"type": "string", "description": "用户本轮或此前消息中给出的、与当前作品请求对应的作品ID或任务ID；服务端识别类型。已有上下文ID时传入，不要要求用户重复提供；未指定且上下文没有ID时省略，读取当前对话最近任务"},
                    "candidate_ordinal": {"type": "integer", "minimum": 1, "maximum": 100, "description": "第几首候选，第二首填2；省略时返回全部候选"},
                })
                function["description"] = "读取用户指定的已保存作品正文及真实句子编号。当前消息引用此前对话中的作品时，复用此前用户消息给出的对应作品ID或任务ID，传resource_id读取；不要要求用户重复提供ID。用户明确说作品ID，或ID来自#poem/view/{ID}页面时，也必须优先传resource_id；点评、查看全文和改写均可使用。resource_id也支持任务ID：先查作品，失败后查任务。用户给出目标原句时，根据返回的lines定位真实line_id，不要追问句号。当前对话第二首仅传candidate_ordinal=2。返回source_ref短引用；准备改写使用该引用及句子ID。多个候选返回sources，按ordinal选择。"
            elif function["name"] in {"submit_generation", "submit_partial_generation", "submit_rewrite"}:
                function["parameters"] = _object_schema({})
        if self._rewrite_sources:
            for schema in schemas:
                if schema["function"]["name"] == "prepare_rewrite":
                    parameters = dict(schema["function"]["parameters"])
                    parameters["required"] = ["requirement", "source_ref", "target_line_ids", "expected_target_lines"]
                    properties = dict(parameters["properties"])
                    properties["source_ref"] = {**properties["source_ref"], "enum": list(self._source_refs)}
                    line_ids = sorted({line["line_id"] for source in self._rewrite_sources.values() for line in source["lines"]})
                    properties["target_line_ids"] = {**properties["target_line_ids"], "items": {"type": "string", "enum": line_ids}}
                    parameters["properties"] = properties
                    schema["function"]["parameters"] = parameters
        return schemas

    def execute(self, name: str, arguments: Mapping[str, Any], context: ToolContext) -> Any:
        # Compatibility for persisted/older callers; obsolete aliases stay out of schemas().
        name = {
        }.get(name, name)
        try:
            tool = self._tools[name]
        except KeyError as exc:
            raise ValueError(f"未知工具: {name}") from exc
        return tool.handler(arguments, context)

    def _build_tools(self) -> tuple[ToolSpec, ...]:
        rhyme_enum = list(RHYME_BOOKS)
        return (
            ToolSpec(
                "list_rhyme_parts", "查询指定韵书实际收录的韵部名称及各韵部的平仄分类。按韵部查询前先调用；查询具体汉字时先用 lookup_rhyme。韵书数据只分平、仄：Xinyun、Tongyun没有入声；Cilin、Pinshui的入声字归入仄声。",
                _object_schema({"rhyme_book": {"type": "string", "enum": rhyme_enum}}, ["rhyme_book"]),
                self._list_rhyme_parts,
            ),
            ToolSpec(
                "list_rhyme_books",
                "列出诗矩本地可查询的韵书。",
                _object_schema({}),
                self._list_rhyme_books,
            ),
            ToolSpec(
                "lookup_rhyme",
                "查询一个或多个汉字在指定韵书中的平仄和韵部。涉及具体字音、韵脚、韵部或韵脚候选时必须调用；历史中有作品ID时先读作品来源及格律，再据实际韵位核查。",
                _object_schema(
                    {
                        "text": {"type": "string", "description": "要查询的汉字，最多 32 字"},
                        "rhyme_book": {"type": "string", "enum": rhyme_enum},
                    },
                    ["text", "rhyme_book"],
                ),
                self._lookup_rhyme,
            ),
            ToolSpec(
                "check_pingze",
                "按指定韵书判定整段内容的平仄。凡涉及一句或多句的平仄、平仄是否正确、对仗或格律核验，必须调用；不要凭模型记忆判断。遇到返回‘中’的多音字，必须结合完整句意、词性和上下文选择实际读音，不能把‘中’直接当作错误；调用时应填写 semantic_context。标准句式中的‘中’是该位置平仄皆可，也不能误判为错误。宋词查询时同时传 stanza_number（阕编号）和 sentence_number（句编号），工具会返回定位信息；词牌格式另用 get_ci_meter 查询。",
                _object_schema(
                    {
                        "rhyme_book": {"type": "string", "enum": rhyme_enum},
                        "content": {"type": "string", "description": "待判定的汉字内容，最多 64 字，可含标点和换行"},
                        "semantic_context": {"type": "string", "description": "句意、词性和上下文，用于判断多音字实际读音；出现多音字时必填，最多 300 字"},
                        "stanza_number": {"type": "integer", "minimum": 1, "description": "宋词阕编号，从 1 开始"},
                        "sentence_number": {"type": "integer", "minimum": 1, "description": "宋词句编号，从 1 开始"},
                    },
                    ["rhyme_book", "content"],
                ),
                self._check_pingze,
            ),
            ToolSpec(
                "get_rhyme_part",
                "按韵书和韵部查询收录的字。用户要具体韵脚候选时必须调用；只要平声字传 tone=\"平\"，只要仄声字传 tone=\"仄\"，不得凭记忆列字或省略声调筛选。韵部应先由作品韵脚的lookup_rhyme结果或用户明确指定确定。数据仅区分平、仄；传统韵书的入声字计入仄声。",
                _object_schema(
                    {
                        "rhyme_book": {"type": "string", "enum": rhyme_enum, "description": "韵书ID，如 Pinshui 表示平水韵"},
                        "rhyme_part": {"type": "string", "description": "韵部名称，如平水韵的一东"},
                        "tone": {"type": "string", "enum": ["全部", "平", "仄"], "description": "声调筛选。用户指定只查平声字时传平，只查仄声字时传仄；未指定时传全部或省略"},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 200, "description": "最多返回字数，默认 80，最大 200"},
                    },
                    ["rhyme_book", "rhyme_part"],
                ),
                self._get_rhyme_part,
            ),
            ToolSpec(
                "list_ci_meters",
                "查询宋词词牌有哪些变体；提供 name 查询指定词牌，不提供则列出全部词牌及变体。具体变体的格律要求使用 get_ci_meter。",
                _object_schema({"name": {"type": "string", "description": "可选词牌名，如浣溪沙"}}),
                self._list_ci_meters,
            ),
            ToolSpec(
                "get_ci_meter",
                "查询一个词牌的逐句平仄、句读、阕结构和押韵位置。必须先调用 list_ci_meters(name) 查看变体，variant_name 原样使用返回名称，例如贺铸，不得自行添加体或变体。用户讨论作品时先读取来源。",
                _object_schema(
                    {
                        "name": {"type": "string", "description": "词牌名，如浣溪沙"},
                        "variant_name": {"type": "string", "description": "可选变体名"},
                    },
                    ["name"],
                ),
                self._get_ci_meter,
            ),
            ToolSpec(
                "list_supported_forms",
                "查询诗矩支持的唐诗、宋词、汉俳和排律形式及参数约束。",
                _object_schema({}),
                self._list_supported_forms,
            ),
            ToolSpec(
                "prepare_generation",
                "准备并保存整首格律诗词生成方案。调用前先按实际押韵句规划韵书、韵组、平仄和适合的韵脚语义；用户未指定时优先新韵，仿宋词选词林正韵，仿唐诗或排律选平水韵，并用查询工具核实韵部/字。排律必须用 meter_type='排律'、form_name='五言排律'或'七言排律'，并单独传 num_lines；不要把句数写进 form_name。返回的 editable_prompt 和完整配置会由界面展示并直接提交；本轮不要调用 submit_generation。",
                _generation_schema(),
                self._prepare_generation,
            ),
            ToolSpec(
                "submit_generation",
                "提交当前整首生成方案。只能用于用户在后续纯文本消息中明确确认的场景；Web 按钮不经过此工具。",
                _object_schema({"proposal_id": {"type": "string"}}, ["proposal_id"]),
                self._submit_generation,
            ),
            ToolSpec(
                "prepare_partial_generation",
                "仅准备诗词补完方案：用户提供一两句作为固定句，传入 fixed_lines；其余句子留空，由生成模型补齐。禁止用于修改、替换已存在作品中的句子；那类请求必须调用 prepare_rewrite。至少保留一句固定句并至少留出一个空句。本轮不要提交任务。",
                _partial_generation_schema(),
                self._prepare_partial_generation,
            ),
            ToolSpec(
                "prepare_rewrite",
                "准备已保存或已生成作品的指定句子重写方案。必须先调用 get_poem_source 取得 source_ref 和带编号原句，再传 target_line_ids、expected_target_lines 与修改要求。只能修改指定目标，其他句子保留。不得用于给残稿补空句；补完残稿请调用 prepare_partial_generation。本轮不要提交任务。",
                _rewrite_schema(),
                self._prepare_rewrite,
            ),
            ToolSpec(
                "get_poem_source",
                "读取已保存/已生成作品的真实原文和格律元数据。改写前必须调用；用户在跨轮对话中讨论某作品的韵脚、韵部、平仄或格律时，历史中有作品ID也必须调用，以读取篇式、词牌和韵书信息。例：指定作品调用 {resource_id:真实作品ID}。省略序号且有多个候选时，一次返回 sources 中全部候选。source_ref 必须使用本工具返回的值。顿号不拆句。",
                _object_schema({
                    "poem_id": {"type": "string", "description": "已保存作品的真实ID；不要填写任务job_id"},
                    "job_id": {"type": "string", "description": "生成任务ID；省略时读取当前对话最近诗词任务"},
                    "candidate_ordinal": {"type": "integer", "minimum": 1, "maximum": 100, "description": "第几首候选，从1开始：第二首填2。省略时返回全部候选，可直接从sources选择"},
                }), self._get_poem_source,
            ),
            ToolSpec(
                "submit_partial_generation",
                "提交当前诗词补完方案；只能用于后续明确确认，Web 按钮直接提交。",
                _object_schema({"proposal_id": {"type": "string"}}, ["proposal_id"]),
                self._submit_partial_generation,
            ),
            ToolSpec(
                "submit_rewrite",
                "提交当前指定句子重写方案；只能用于后续明确确认，Web 按钮直接提交。",
                _object_schema({"proposal_id": {"type": "string"}}, ["proposal_id"]),
                self._submit_rewrite,
            ),
            ToolSpec(
                "get_poetry_job",
                "按生成任务ID读取任务状态和真实成稿，包括网页按钮提交的任务。用户明确提供任务ID/job_id时使用；用户提供作品ID或#poem/view/{ID}页面中的ID时，优先用get_poem_source(resource_id=作品ID)。本工具先查任务，失败后查同ID作品，此时返回编号原作而非任务状态。任务查询成功后读取generated_poems中的标题和正文，未完成时只报告状态。",
                _object_schema({"job_id": {"type": "string", "description": "生成任务ID；作品页面#poem/view/后的ID是作品ID，应优先传给get_poem_source的resource_id"}}, ["job_id"]),
                self._get_poetry_job,
            ),
            ToolSpec(
                "get_latest_poetry_job",
                "读取当前用户本对话最近提交的诗词任务，无需 job_id，也包括网页按钮提交的任务。用户询问刚生成的作品、全文、点评、核对或修改时，回答前必须主动调用；成功后从 generated_poems 读取真实标题和正文，不要要求用户重新粘贴。",
                _object_schema({}),
                self._get_latest_poetry_job,
            ),
            ToolSpec(
                "list_poetry_jobs",
                "列出当前用户当前对话中的全部诗词生成、改写和补全任务，自动遍历所有分页。用户询问本对话全部/所有批次、总共生成多少首、此前每批作品或要求汇总时必须调用；随后对每个已完成任务调用 get_poetry_job 读取 generated_poems，再按真实正文汇总。只返回任务清单，不代表作品正文已读取。",
                _object_schema({}),
                self._list_poetry_jobs,
            ),
        )

    def _list_rhyme_books(self, arguments, context) -> dict:
        return {
            "rhyme_books": [
                {"id": key, "name": value} for key, value in RHYME_BOOKS.items()
            ]
        }

    def query_prerequisites(self, name, arguments):
        if name == "get_ci_meter":
            meter = str(arguments.get("name") or "").strip()
            if meter and meter not in self._discovered_meters:
                return [("list_ci_meters", {"name": meter})]
        if name == "get_rhyme_part":
            book, part = arguments.get("rhyme_book"), arguments.get("rhyme_part")
            if (book, part) not in self._discovered_parts:
                return [("list_rhyme_parts", {"rhyme_book": book})]
        return []

    def _list_rhyme_parts(self, arguments, context):
        book = self._book_name(arguments.get("rhyme_book"))
        parts = {}
        for _, part, tone in self._lexicon(book).iter_rhyme_entries():
            parts.setdefault(part, set()).add(tone)
            self._discovered_parts.add((book, part))
        return {"rhyme_book": book, "rhyme_parts": [
            {"name": part, "tones": sorted(tones)} for part, tones in parts.items()
        ]}

    def _lookup_rhyme(self, arguments, context) -> dict:
        text = str(arguments.get("text", "")).strip()
        if not text:
            raise ValueError("text 不能为空")
        chars = list(dict.fromkeys(char for char in text if "\u3400" <= char <= "\u9fff"))
        if not chars:
            raise ValueError("text 中没有可查询的汉字")
        if len(chars) > 32:
            raise ValueError("单次最多查询 32 个不同汉字")
        book = self._book_name(arguments.get("rhyme_book"))
        lexicon = self._lexicon(book)
        entries = []
        for char in chars:
            tones = lexicon.get_pingze(char)
            self._discovered_parts.update((book, part) for part in lexicon.get_rhyme_part(char))
            entries.append(
                {
                    "char": char,
                    "tones": tones,
                    "rhyme_parts": lexicon.get_rhyme_part(char),
                    "parts_by_tone": {
                        tone: lexicon.get_rhyme_part_by_tone(char, tone) for tone in tones
                    },
                    "found": bool(tones),
                }
            )
        return {"rhyme_book": book, "entries": entries}

    def _check_pingze(self, arguments, context) -> dict:
        content = str(arguments.get("content", "")).strip()
        if not content:
            raise ValueError("content 不能为空")
        chars = [char for char in content if "\u3400" <= char <= "\u9fff"]
        if not chars:
            raise ValueError("content 中没有可查询的汉字")
        if len(chars) > 64:
            raise ValueError("单次最多查询 64 个汉字")
        book = self._book_name(arguments.get("rhyme_book"))
        lexicon = self._lexicon(book)
        entries = []
        for char in chars:
            tones = lexicon.get_pingze(char)
            entries.append({"char": char, "tones": tones, "found": bool(tones)})
        return {
            "rhyme_book": book,
            "content": content,
            "pingze": "".join(
                entry["tones"][0] if len(entry["tones"]) == 1 else "中" if entry["tones"] else "?"
                for entry in entries
            ),
            "entries": entries,
            "uncertain_chars": [entry["char"] for entry in entries if len(entry["tones"]) != 1],
            "stanza_number": arguments.get("stanza_number"),
            "sentence_number": arguments.get("sentence_number"),
            "semantic_context": str(arguments.get("semantic_context") or "").strip() or None,
            "note": "返回的‘中’表示该字在此韵书中兼有平、仄，必须结合句意、词性和上下文从 entries[tones] 中选择实际读音；它不是错误。标准句式里的‘中’表示该位置平仄皆可。?表示韵书未收录。这里只判定字音，不代替词牌格律核验。",
        }

    def _get_rhyme_part(self, arguments, context) -> dict:
        book = self._book_name(arguments.get("rhyme_book"))
        part = str(arguments.get("rhyme_part", "")).strip()
        if not part:
            raise ValueError("rhyme_part 不能为空")
        tone = str(arguments.get("tone", "全部"))
        if tone not in {"全部", "平", "仄"}:
            raise ValueError("tone 只能是全部、平或仄")
        limit = int(arguments.get("limit", 80))
        if not 1 <= limit <= 200:
            raise ValueError("limit 必须在 1 到 200 之间")
        matches = []
        available_tones = set()
        for char, entry_part, entry_tone in self._lexicon(book).iter_rhyme_entries():
            if entry_part == part:
                available_tones.add(entry_tone)
                if tone == "全部" or entry_tone == tone:
                    matches.append({"char": char, "tone": entry_tone})
        if not available_tones:
            raise ValueError(f"{book} 中未找到韵部 {part}")
        return {
            "rhyme_book": book,
            "rhyme_part": part,
            "tone": tone,
            "available_tones": sorted(available_tones),
            "total": len(matches),
            "entries": matches[:limit],
            "truncated": len(matches) > limit,
        }

    def _list_ci_meters(self, arguments, context) -> dict:
        meters = []
        for path in sorted((self._root / "Songci_Meter").glob("*.json")):
            root = json.loads(path.read_text(encoding="utf-8"))
            # index.json is a directory-level list, not a meter definition.
            if not isinstance(root, dict):
                continue
            requested = str(arguments.get("name") or "").strip()
            if requested and requested != (root.get("name") or path.stem):
                continue
            meters.append(
                {
                    "name": root.get("name") or path.stem,
                    "default_variant": root.get("default_variant"),
                    "variants": [item.get("name") for item in root.get("variants", [])],
                }
            )
        self._discovered_meters.update(item["name"] for item in meters)
        return {"meters": meters}

    def _get_ci_meter(self, arguments, context) -> dict:
        name = str(arguments.get("name", "")).strip()
        if not name:
            raise ValueError("name 不能为空")
        variant_name = str(arguments.get("variant_name") or "").strip() or None
        if variant_name:
            catalog = self._list_ci_meters({"name": name}, context)["meters"]
            variants = catalog[0]["variants"] if catalog else []
            if variant_name not in variants:
                for suffix in ("变体", "体"):
                    candidate = variant_name.removesuffix(suffix).strip()
                    if candidate in variants:
                        variant_name = candidate
                        break
        template = MeterTemplateRepository(self._root / "Songci_Meter").get(
            name, variant_name
        )
        return {
            "name": template.name,
            "variant_name": template.variant_name,
            "rhyme_type": template.rhyme_type,
            "stanzas": [
                {
                    "index": stanza.index + 1,
                    "lines": [
                        {
                            "pattern": line.raw_pattern,
                            "characters": len(line.tone_options),
                            "rhyme_group": line.rhyme_group,
                        }
                        for line in stanza.lines
                    ],
                }
                for stanza in template.stanzas
            ],
        }

    def _list_supported_forms(self, arguments, context) -> dict:
        return {
            "唐诗": {
                "forms": ["五言绝句", "七言绝句", "五言律诗", "七言律诗"],
                "note": "对应形式可注明仄韵；默认平韵，默认不启用拗救。",
            },
            "宋词": {
                "forms": [item["name"] for item in self._list_ci_meters({}, context)["meters"]],
                "note": "按本地词牌模板逐字约束。",
            },
            "汉俳": {
                "forms": ["汉俳"],
                "line_patterns": ["5-7-5", "3-5-3"],
            },
            "排律": {
                "forms": ["五言排律", "七言排律"],
                "note": "句数必须是不少于十句的偶数，默认启用拗救。",
            },
        }

    def _prepare_generation(self, arguments, context: ToolContext) -> dict:
        payload = _generation_payload(arguments)
        if "candidate_count" not in arguments:
            payload["candidate_count"] = _requested_candidate_count(context.user_message)
        request = GeneratePoemRequest.from_mapping(payload)
        self._validate_form(
            request.meter_type,
            request.form_name,
            request.num_lines,
            request.task_options,
        )
        self._validate_rhyme_selection(
            request.rhyme_dict_name,
            request.rhyme_mode,
            request.rhyme_parts,
            request.meter_type,
            request.form_name,
        )
        return self._store_proposal("generate", request.to_dict(), context)

    def _get_poem_source(self, arguments, context: ToolContext) -> dict:
        if arguments.get("resource_id"):
            arguments = {**arguments, "poem_id": arguments["resource_id"]}
        if arguments.get("original_text"):
            raise ValueError("改写必须提供作品 ID，请先读取已保存作品；不能粘贴正文登记原作")
        else:
            poem_id = arguments.get("poem_id")
            if isinstance(poem_id, str):
                poem_id = poem_id.strip()
                if poem_id.lower() in {"null", "none"}:
                    poem_id = None
            if poem_id:
                try:
                    source = self._jobs.get_poem_source(poem_id)
                except (ValueError, KeyError, PoetryApiError) as source_error:
                    # A model can put a real task ID in the poem ID field. Resolve
                    # its resource type through the same authorized job lookup.
                    try:
                        job = self._jobs.get_job(poem_id)
                    except (ValueError, KeyError, PoetryApiError):
                        raise source_error
                    return self._get_poem_source({"job_id": job["job_id"],
                                                 "candidate_ordinal": arguments.get("candidate_ordinal")}, context)
            if not poem_id:
                job_id = arguments.get("job_id")
                if isinstance(job_id, str) and job_id.strip().lower() in {"", "null", "none"}:
                    job_id = None
                if job_id:
                    try:
                        job = self._jobs.get_job(job_id)
                    except (ValueError, KeyError, PoetryApiError) as job_error:
                        try:
                            source = self._jobs.get_poem_source(job_id)
                        except (ValueError, KeyError, PoetryApiError):
                            raise job_error
                        self._rewrite_sources[source["source_version"]] = source
                        return self._expose_source(source)
                else:
                    job = self._jobs.get_latest_job()
                candidates = self._format_poetry_snapshot(job)["generated_poems"]
                ordinal = arguments.get("candidate_ordinal")
                if isinstance(ordinal, str) and ordinal.strip().lower() in {"", "null", "none"}:
                    ordinal = None
                if not candidates:
                    raise ValueError("该任务尚无生成完成的候选，请等待完成后再读取原作")
                ordinals = [item["ordinal"] for item in candidates]
                if ordinal is not None and (isinstance(ordinal, bool) or not isinstance(ordinal, int) or ordinal not in ordinals):
                    raise ValueError(f"候选序号不存在；可用 candidate_ordinal：{ordinals}")
                if ordinal is None and len(candidates) > 1:
                    sources = []
                    for candidate in candidates:
                        value = self._jobs.get_candidate_poem(job["job_id"], candidate["ordinal"])
                        source = self._jobs.get_poem_source(value["id"])
                        source = {**source, "ordinal": candidate["ordinal"], "job_id": job["job_id"]}
                        self._rewrite_sources[source["source_version"]] = source
                        sources.append(self._expose_source(source))
                    return {"job_id": job["job_id"], "sources": sources,
                            "instruction": "全部候选的真实编号原作已返回。按ordinal选择用户指定候选，直接用该项source_ref和lines准备改写，无需再次读取。上阕第3句是s1-l3，按sentence_number定位，不按句号重数；无法确定哪首时询问用户。"}
                value = self._jobs.get_candidate_poem(job["job_id"], ordinal if ordinal is not None else ordinals[0])
                poem_id = value["id"]
                source = self._jobs.get_poem_source(poem_id)
        self._rewrite_sources[source["source_version"]] = source
        return {**self._expose_source(source), "instruction": "准备改写只传source_ref及目标句，不回填作品ID或版本。改写面板中勾选表示重写，未勾选表示保留；target_line_ids只包含用户明确指定的重写句，不包含保留句。补完面板的固定句勾选另表示原样保留，不能与改写模式混用。定位以lines中的sentence_number为准：上阕第3句是s1-l3，下阕第3句是s2-l3；不能按句号重新计数，顿号不分句。"}

    def _expose_source(self, source: dict) -> dict:
        ref = next((ref for ref, saved in self._source_refs.items()
                    if saved.get("source_poem_id") == source.get("source_poem_id")
                    and saved["source_version"] == source["source_version"]), None)
        if ref is None:
            ref = f"source-{len(self._source_refs) + 1}"
        self._source_refs[ref] = source
        return {**source, "source_ref": ref}

    def _prepare_partial_generation(self, arguments, context: ToolContext) -> dict:
        forbidden = {"original_text", "source_poem_id", "source_version", "source_ref", "target_line_ids", "target_line_numbers", "expected_target_lines"}
        supplied = forbidden.intersection(arguments)
        if supplied:
            raise ValueError("诗词补完只接受 fixed_lines；已有作品的指定句重写请使用 prepare_rewrite")
        payload = _generation_payload(arguments)
        request = GeneratePoemRequest.from_mapping(payload)
        self._validate_form(request.meter_type, request.form_name, request.num_lines, request.task_options)
        self._validate_rhyme_selection(
            request.rhyme_dict_name, request.rhyme_mode, request.rhyme_parts,
            request.meter_type, request.form_name,
        )
        validate_partial_generation_request(request, self._root)
        return self._store_proposal("partial_generate", request.to_dict(), context)

    def _prepare_rewrite(self, arguments, context: ToolContext) -> dict:
        arguments = dict(arguments)
        requested_task_options = dict(arguments.get("task_options") or {})
        if arguments.get("source_ref"):
            saved_source = self._source_refs.get(arguments["source_ref"])
            if not saved_source:
                raise ValueError("来源引用已失效，请重新读取编号原作；不要猜测引用")
            if arguments.get("source_poem_id") or arguments.get("source_version"):
                raise ValueError("使用source_ref时无需传作品ID或版本")
            arguments.update({key: saved_source.get(key) for key in ("source_poem_id", "source_version")})
        source = None
        if arguments.get("source_version") or arguments.get("source_poem_id") or arguments.get("target_line_ids"):
            if arguments.get("source_poem_id"):
                source = self._jobs.get_poem_source(arguments["source_poem_id"])
            else:
                source = self._rewrite_sources.get(arguments.get("source_version"))
            if not source:
                raise ValueError("请先调用 get_poem_source 读取原作")
            if arguments.get("fixed_lines") or arguments.get("original_text"):
                raise ValueError("来源重写不能手工填写原文或保留句")
            expected = arguments.get("expected_target_lines") or {}
            if isinstance(expected, list):
                expected_map = {}
                for entry in expected:
                    if not isinstance(entry, dict) or entry.get("line_id") in expected_map:
                        raise ValueError("原句核对列表包含无效或重复编号")
                    expected_map[entry.get("line_id")] = entry.get("text")
                expected = expected_map
            selection = select_rewrite_lines(source, arguments.get("source_version"),
                arguments.get("target_line_ids") or [], expected)
            arguments.update({key: source.get(key) for key in (
                "original_text", "source_poem_id", "source_version", "meter_type", "form_name", "variant_name", "num_lines", "task_options",
            )})
            arguments["task_options"] = {
                **dict(source.get("task_options") or {}),
                **requested_task_options,
            }
            arguments.update(selection)
            arguments.setdefault("rhyme_dict_name", source.get("rhyme_dict_name") or "Xinyun")
        payload = _rewrite_payload(arguments)
        if "candidate_count" not in arguments:
            payload["candidate_count"] = _requested_candidate_count(context.user_message)
        if str(payload.get("original_text") or "").strip():
            if not payload.get("source_poem_id"):
                raise ValueError("改写必须提供作品 ID，请先调用 get_poem_source 读取作品")
            request = RewritePoemRequest.from_mapping(payload)
        else:
            raise ValueError("改写必须先读取已保存作品，并指定需要重写的句子")
        self._validate_form(
            request.meter_type,
            request.form_name,
            request.num_lines,
            request.task_options,
            rewrite_options=True,
        )
        self._validate_rhyme_selection(
            request.rhyme_dict_name,
            request.rhyme_mode,
            request.rhyme_parts,
            request.meter_type,
            request.form_name,
        )
        poem = parse_poem(request.original_text)
        if request.target_line_numbers[-1] > len(poem.lines):
            raise ValueError(
                f"目标句号超出原文范围；原文解析为 {len(poem.lines)} 句"
            )
        payload = request.to_dict()
        targets = set(request.target_line_numbers)
        payload["fixed_lines"] = {
            str(line.number): line.text for line in poem.lines if line.number not in targets
        }
        if not payload.get("fixed_lines"):
            raise ValueError("部分生成必须至少指定一句需要原样保留的句子")
        prepared = self._store_proposal("rewrite", payload, context)
        if source:
            prepared["proposal"]["source_lines"] = source["lines"]
            self._rewrite_sources.clear()
        return prepared

    def _store_proposal(self, kind: str, payload: dict, context: ToolContext) -> dict:
        proposal = PendingProposal(
            id=str(uuid.uuid4()),
            kind=kind,
            payload=payload,
            prepared_turn=context.turn_index,
        )
        self._pending = proposal
        self._save_pending()
        preview_fields = (
            "meter_type",
            "form_name",
            "variant_name",
            "theme",
            "rhyme_dict_name",
            "rhyme_mode",
            "rhyme_parts",
            "requirement",
            "num_lines",
            "target_line_numbers",
            "original_text",
            "fixed_lines",
            "source_poem_id",
            "source_version",
            "strict_polyphonic",
            "candidate_count",
            "task_type",
            "task_options",
        )
        preview = {key: payload[key] for key in preview_fields if key in payload}
        if payload.get("original_text"):
            preview["source_lines"] = numbered_source(payload["original_text"], payload, self._root)["lines"]
        result = {
            "proposal_id": proposal.id,
            "kind": proposal.kind,
            "proposal": preview,
            "editable_prompt": payload.get("requirement", ""),
            "candidate_count": payload.get("candidate_count", 1),
            "can_submit_now": False,
            "instruction": "完整展示 editable_prompt 和配置；Web 界面会提供直接提交按钮，本轮不要调用 submit 工具。",
        }
        if kind == "partial_generate":
            result["sentence_selection"] = {
                "checked": "保留",
                "unchecked_with_text": "不允许；补完只能留空",
                "unchecked_empty": "补完",
                "checked_line_numbers": sorted(int(number) for number in payload.get("fixed_lines", {})),
            }
        elif kind == "rewrite":
            result["sentence_selection"] = {
                "checked": "重写",
                "unchecked": "保留",
                "checked_line_numbers": list(payload["target_line_numbers"]),
                "unchecked_line_numbers": sorted(int(number) for number in payload.get("fixed_lines", {})),
            }
        if kind in {"partial_generate", "rewrite"}:
            result["instruction"] = (
                "改写方案已准备，Web 面板只重写明确指定的目标句，其余原句保留。"
                if kind == "rewrite" else
                "补完方案已准备，固定句保留，空缺句由模型补齐；不得重写已有句子。"
            ) + "用户可在面板检查配置并提交；本轮不要调用 submit 工具，也不要复述完整配置。"
        return result

    def _submit_generation(self, arguments, context: ToolContext) -> dict:
        proposal = self._confirmed_proposal(arguments, context, "generate")
        result = self._jobs.submit_generate(proposal.payload, proposal.id)
        proposal.submitted_job_id = str(result.get("job_id") or "") or None
        self._save_pending()
        return result

    def _submit_rewrite(self, arguments, context: ToolContext) -> dict:
        proposal = self._confirmed_proposal(arguments, context, "rewrite")
        validate_rewrite_context(
            RewritePoemRequest.from_mapping(proposal.payload), self._root
        )
        result = self._jobs.submit_rewrite(proposal.payload, proposal.id)
        proposal.submitted_job_id = str(result.get("job_id") or "") or None
        self._save_pending()
        return result

    def _submit_partial_generation(self, arguments, context: ToolContext) -> dict:
        proposal = self._confirmed_proposal(arguments, context, "partial_generate")
        submitter = getattr(self._jobs, "submit_partial_generate", None)
        if not callable(submitter):
            # Older in-process job adapters use the legacy transport name.
            submitter = self._jobs.submit_rewrite
        result = submitter(proposal.payload, proposal.id)
        proposal.submitted_job_id = str(result.get("job_id") or "") or None
        self._save_pending()
        return result

    def _confirmed_proposal(
        self,
        arguments: Mapping[str, Any],
        context: ToolContext,
        expected_kind: str,
    ) -> PendingProposal:
        if not self.allow_text_submission:
            raise ValueError("请在提示词面板点击提交按钮；聊天消息不能提交生成任务")
        proposal_id = str(arguments.get("proposal_id") or (self._pending.id if self._pending else ""))
        proposal = self._pending
        if proposal is None or proposal.id != proposal_id:
            active = proposal.id if proposal else "无"
            raise ValueError(f"proposal_id 不是当前待确认方案；当前方案为 {active}")
        if proposal.kind != expected_kind:
            raise ValueError(f"当前方案类型是 {proposal.kind}，不能作为 {expected_kind} 提交")
        if proposal.submitted_job_id:
            raise ValueError(f"该方案已经提交，job_id={proposal.submitted_job_id}")
        if proposal.prepared_turn >= context.turn_index:
            raise ValueError("方案准备后应由界面提交，不能在同一轮调用 submit 工具")
        if not _is_explicit_confirmation(context.user_message):
            raise ValueError("用户当前消息没有要求执行该方案")
        return proposal

    def _get_poetry_job(self, arguments, context) -> dict:
        job_id = str(arguments.get("job_id", "")).strip()
        if not job_id:
            raise ValueError("job_id 不能为空")
        try:
            job = self._jobs.get_job(job_id)
        except (ValueError, KeyError, PoetryApiError) as job_error:
            try:
                source = self._jobs.get_poem_source(job_id)
            except (ValueError, KeyError, PoetryApiError):
                raise job_error
            self._rewrite_sources[source["source_version"]] = source
            return {**self._expose_source(source),
                    "agent_instruction": "该ID对应已保存作品，已返回真实正文及编号原作。点评或展示使用original_text；准备改写使用source_ref和lines。"}
        snapshot = self._format_poetry_snapshot(job)
        if not isinstance(snapshot, dict):
            return snapshot
        if snapshot.get("status") == "succeeded":
            snapshot["agent_instruction"] = (
                "任务已完成。以上 generated_poems 是本次真实生成正文；回答时只能引用这些正文并进行散文分析，禁止自行补写诗句。"
            )
        return snapshot

    def _get_latest_poetry_job(self, arguments, context) -> dict:
        getter = getattr(self._jobs, "get_latest_job", None)
        if not callable(getter):
            raise ValueError("当前任务服务不支持按本对话查询最近任务")
        snapshot = getter()
        return self._format_poetry_snapshot(snapshot)

    def _list_poetry_jobs(self, arguments, context) -> dict:
        lister = getattr(self._jobs, "list_conversation_jobs", None)
        if not callable(lister):
            raise ValueError("当前任务服务不支持列出本对话全部诗词任务")
        items = lister()
        return {
            "items": items,
            "count": len(items),
            "agent_instruction": "这是当前对话的完整诗词任务清单。若用户问总首数、全部作品或所有批次，对每个 succeeded 任务调用 get_poetry_job 读取真实 generated_poems 后再汇总；queued/running/failed 任务单独报告，不得猜测其正文或数量。",
        }

    def _format_poetry_snapshot(self, snapshot: dict) -> dict:
        if not isinstance(snapshot, dict):
            return snapshot
        candidates = list(snapshot.get("candidates") or [])
        result = snapshot.get("result") if isinstance(snapshot.get("result"), dict) else {}
        if not candidates:
            candidates = list(result.get("candidates") or [])
        generated = []
        for index, candidate in enumerate(candidates, start=1):
            if isinstance(candidate, Mapping):
                content = str(candidate.get("content") or candidate.get("text") or "").strip()
                if content:
                    generated.append({"ordinal": candidate.get("ordinal", index), "title": str(candidate.get("title") or "").strip(), "content": content})
        if not generated:
            fallback = result.get("full_text") or result.get("content") or result.get("text")
            if fallback:
                generated.append({"ordinal": 1, "title": "", "content": str(fallback).strip()})
        for poem in generated:
            poem["content"] = poem_body(poem["content"])
            poem["source_arguments"] = {"resource_id": snapshot.get("job_id"), "candidate_ordinal": poem["ordinal"]}
        # Raw output, partial text and final result often repeat each poem several times.
        compact = {key: value for key, value in snapshot.items() if key not in {"candidates", "result"}}
        compact["generated_poems"] = generated
        compact["candidate_states"] = [{"ordinal": item.get("ordinal", index), "status": item.get("status")}
                                       for index, item in enumerate(candidates, 1) if isinstance(item, Mapping)]
        return compact

    def _book_name(self, value: Any) -> str:
        name = str(value or "")
        if name not in RHYME_BOOKS:
            raise ValueError(f"不支持的韵书: {name}")
        return name

    def _lexicon(self, book: str) -> RhymeLexicon:
        if book not in self._lexicons:
            self._lexicons[book] = RhymeLexicon(self._root / "Rhyme" / f"{book}.json")
        return self._lexicons[book]

    def _validate_rhyme_selection(
        self,
        book: str,
        mode: str,
        parts: Mapping[str, str],
        meter_type: str,
        form_name: str,
    ) -> None:
        if mode not in {"auto", "fixed", "random"}:
            raise ValueError("rhyme_mode 必须是 auto、fixed 或 random")
        entries = list(self._lexicon(book).iter_rhyme_entries())
        available = {part for _, part, _ in entries}
        expected_tone = None
        valid_groups: set[str] | None = None
        if meter_type == "唐诗":
            expected_tone = "仄" if "仄韵" in form_name else "平"
            valid_groups = {"1"}
        elif meter_type == "排律":
            expected_tone = "平"
            valid_groups = {"1"}
        elif meter_type == "宋词":
            template = MeterTemplateRepository(self._root / "Songci_Meter").get(form_name)
            # 平仄转换韵/通押按词谱各句的实际句尾声调判定，不能
            # 因名称包含“仄”或“平”就把整阕强制成单一声调。
            expected_tone = (
                "仄" if template.rhyme_type == "仄韵"
                else "平" if template.rhyme_type == "平韵"
                else None
            )
            valid_groups = {
                str(line.rhyme_group)
                for line in template.lines
                if line.rhyme_group is not None
            }
        elif meter_type == "汉俳":
            valid_groups = {"1"}
        for group, part in parts.items():
            group = str(group)
            if str(part).lower() != "random" and part not in available:
                raise ValueError(f"{book} 中未找到韵组 {group} 指定的韵部 {part}")
            if valid_groups is not None and group not in valid_groups:
                raise ValueError(f"{form_name} 不存在韵组 {group}")
            if expected_tone and str(part).lower() != "random":
                if not any(item_part == part and tone == expected_tone for _, item_part, tone in entries):
                    raise ValueError(
                        f"{form_name} 是{expected_tone}韵，韵部 {part} 没有可用的{expected_tone}声韵脚"
                    )

    def _validate_form(
        self,
        meter_type: str,
        form_name: str,
        num_lines: int | None,
        task_options: Mapping[str, Any],
        *,
        rewrite_options: bool = False,
    ) -> None:
        rewrite_only = {"omit_original_target_from_prompt"} if rewrite_options else set()
        if meter_type == "唐诗":
            _reject_unknown_options(task_options, {"allow_aojiu", "skip_planning"} | rewrite_only, meter_type)
            normalized = form_name.replace("仄韵", "").strip()
            if normalized not in {"五言绝句", "七言绝句", "五言律诗", "七言律诗"}:
                raise ValueError(f"不支持的唐诗篇式: {form_name}")
            return
        if meter_type == "宋词":
            _reject_unknown_options(task_options, {"skip_planning"} | rewrite_only, meter_type)
            MeterTemplateRepository(self._root / "Songci_Meter").get(form_name)
            return
        if meter_type == "汉俳":
            _reject_unknown_options(
                task_options,
                {
                    "line_pattern",
                    "season_word",
                    "season_words",
                    "season",
                    "forbid_isolated_level",
                    "allow_aojiu",
                    "forbid_three_same_ending",
                    "rhyme_scheme",
                    "skip_planning",
                } | rewrite_only,
                meter_type,
            )
            if form_name != "汉俳":
                raise ValueError("汉俳的 form_name 必须是“汉俳”")
            line_pattern = (
                str(task_options.get("line_pattern", "5-7-5"))
                .strip()
                .replace("－", "-")
                .replace("—", "-")
                .replace("×", "-")
                .replace(" ", "")
            )
            if line_pattern not in {"5-7-5", "575", "五七五", "3-5-3", "353", "三五三"}:
                raise ValueError(f"汉俳格式仅支持 5-7-5 或 3-5-3，收到: {line_pattern}")
            return
        if meter_type == "排律":
            _reject_unknown_options(task_options, {"allow_aojiu", "skip_planning"} | rewrite_only, meter_type)
            if form_name not in {"五言排律", "七言排律"}:
                raise ValueError(f"不支持的排律篇式: {form_name}")
            if num_lines is None or num_lines < 10 or num_lines % 2 != 0:
                raise ValueError("排律句数必须是不少于十句的偶数")
            return
        raise ValueError(f"不支持的生成类型: {meter_type}")


def _object_schema(
    properties: Mapping[str, Any], required: list[str] | None = None
) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": dict(properties),
        "required": required or [],
        "additionalProperties": False,
    }


def _generation_schema() -> dict[str, Any]:
    return _object_schema(
        {
            "meter_type": {"type": "string", "enum": ["唐诗", "宋词", "汉俳", "排律"]},
            "form_name": {"type": "string", "description": "篇式名称；排律只能填五言排律或七言排律，句数另填 num_lines，不能填七言排律十二句"},
            "variant_name": {"type": "string", "description": "宋词变体名称；不指定时使用默认变体"},
            "theme": {"type": "string", "description": "简洁主题"},
            "rhyme_dict_name": {"type": "string", "enum": list(RHYME_BOOKS)},
            "rhyme_mode": {"type": "string", "enum": ["auto", "fixed", "random"], "description": "韵部策略：auto 自动锁韵，fixed 使用 rhyme_parts，random 随机选韵"},
            "rhyme_parts": {"type": "object", "additionalProperties": {"type": "string"}, "description": "按韵组编号指定韵部，如 {\"1\":\"一东\"}；值为 random 可对该组随机选韵"},
            "requirement": {
                "type": "string",
                "description": "细化后的创作提示，包含意象、情绪、章法和特殊要求；不同要点使用真实换行符分段，保留用户指定的换行",
            },
            "num_lines": {"type": "integer", "minimum": 10, "description": "排律必填，十句以上的偶数；十二句填数字 12。其他诗体不填"},
            "strict_polyphonic": {"type": "boolean"},
            "candidate_count": {
                "type": "integer",
                "minimum": 1,
                "maximum": 20,
                "description": "候选版本数量，支持 1 至 20；用户说一首时必须填 1",
            },
            "task_options": {
                "type": "object",
                "properties": {
                    "skip_planning": {"type": "boolean", "description": "设为 true 时直接输出 [title] 和 [content]，不输出创作构思；仅用于完整生成"},
                    "allow_aojiu": {"type": "boolean", "description": "唐诗或排律是否允许拗救"},
                    "line_pattern": {"type": "string", "enum": ["5-7-5", "3-5-3"], "description": "汉俳句式"},
                    "season_word": {"type": "string"},
                    "season_words": {"type": "array", "items": {"type": "string"}},
                    "season": {"type": "string"},
                    "forbid_isolated_level": {"type": "boolean"},
                    "forbid_three_same_ending": {"type": "boolean"},
                    "rhyme_scheme": {"type": "string", "enum": ["AAA", "ABA", "BAA", "none", "random"]},
                },
                "additionalProperties": False,
            },
        },
        ["meter_type", "form_name", "theme", "requirement"],
    )


def _rewrite_schema() -> dict[str, Any]:
    properties = dict(_generation_schema()["properties"])
    properties["task_options"] = {
        **properties["task_options"],
        "properties": {
            key: value for key, value in properties["task_options"]["properties"].items()
            if key != "skip_planning"
        },
    }
    task_options = properties["task_options"]
    properties["task_options"] = {
        **task_options,
        "properties": {
            **task_options["properties"],
            "omit_original_target_from_prompt": {
                "type": "boolean",
                "description": "设为 true 时，重写提示词不包含目标句原文；适用于局部 rewrite。",
            },
        },
    }
    properties["candidate_count"] = {
        "type": "integer", "minimum": 1, "maximum": 50,
        "description": "rewrite 最多 50 个候选；默认 1",
    }
    properties["requirement"] = {
        "type": "string",
        "description": "开放的局部重写目标；用户只指定情绪时，可要求从任何合乎上下文的细节入手，不预设描写类别、词语或句法",
    }
    properties.update(
        {
            "source_ref": {"type": "string", "description": "必须来自 get_poem_source 的 source_ref"},
            "target_line_ids": {
                "type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 64,
                "description": "用户明确要求重写的句子 ID，对应改写面板勾选的重写句，不是未勾选的保留句",
            },
            "expected_target_lines": {
                "type": "array",
                "items": _object_schema({"line_id": {"type": "string"}, "text": {"type": "string"}}, ["line_id", "text"]),
                "minItems": 1,
                "description": "从 get_poem_source 原样复制目标句的 line_id 和 text",
            },
        }
    )
    return _object_schema(
        properties,
        ["source_ref", "target_line_ids", "expected_target_lines", "requirement"],
    )


def _partial_generation_schema() -> dict[str, Any]:
    properties = dict(_generation_schema()["properties"])
    properties["task_options"] = {
        **properties["task_options"],
        "properties": {
            key: value for key, value in properties["task_options"]["properties"].items()
            if key != "skip_planning"
        },
    }
    properties["fixed_lines"] = {
        "type": "object",
        "minProperties": 1,
        "additionalProperties": {"type": "string", "minLength": 1},
        "description": "用户提供并要求保留的原句；只传固定句，未提供的位置由模型补完",
    }
    return _object_schema(
        properties,
        ["meter_type", "form_name", "requirement", "fixed_lines"],
    )


def _generation_payload(arguments: Mapping[str, Any]) -> dict[str, Any]:
    meter_type = arguments.get("meter_type")
    task_options = dict(arguments.get("task_options") or {})
    variant_name = arguments.get("variant_name")
    # Older prompts and model tool calls sometimes put this Songci-only field
    # inside task_options. Canonicalize it before the strict option validator.
    if meter_type == "宋词" and "variant_name" in task_options:
        variant_name = variant_name or task_options["variant_name"]
        task_options.pop("variant_name")
    if meter_type in {"唐诗", "排律"}:
        task_options.setdefault("allow_aojiu", True)
    return {
        "meter_type": arguments.get("meter_type"),
        "form_name": arguments.get("form_name"),
        "variant_name": variant_name,
        "theme": arguments.get("theme"),
        "rhyme_dict_name": arguments.get("rhyme_dict_name", "Xinyun"),
        "rhyme_mode": arguments.get("rhyme_mode", "auto"),
        "rhyme_parts": dict(arguments.get("rhyme_parts") or {}),
        "fixed_lines": {str(k): str(v) for k, v in (arguments.get("fixed_lines") or {}).items()},
        "requirement": arguments.get("requirement", ""),
        "num_lines": arguments.get("num_lines"),
        "strict_polyphonic": arguments.get("strict_polyphonic", False),
        "candidate_count": arguments.get("candidate_count", 1),
        "task_options": task_options,
    }


def _rewrite_payload(arguments: Mapping[str, Any]) -> dict[str, Any]:
    payload = _generation_payload(arguments)
    payload.pop("theme", None)
    payload.update(
        {
            "original_text": arguments.get("original_text"),
            "target_line_numbers": arguments.get("target_line_numbers") or [],
            "source_poem_id": arguments.get("source_poem_id"),
            "source_version": arguments.get("source_version"),
            "theme": arguments.get("theme", "局部改写"),
        }
    )
    return payload


def _is_explicit_confirmation(text: str) -> bool:
    normalized = re.sub(r"[\s，。！？、,.!?]", "", text).lower()
    if not normalized:
        return False
    if any(word in normalized for word in (
        "不要", "不行", "先别", "等等", "取消", "别生成", "别提交",
        "暂不", "暂时不", "先不", "先聊", "聊聊", "讨论", "商量",
        "再想想", "再看看", "改写要求", "调整要求", "补充要求",
    )):
        return False
    modification_words = ("改成", "改为", "修改", "调整", "换成", "加上", "去掉")
    if any(word in normalized for word in modification_words):
        return False
    exact = {
        "确认",
        "好的",
        "好",
        "可以",
        "可以了",
        "没问题",
        "就这样",
        "按这个来",
        "开始吧",
        "提交吧",
        "生成吧",
    }
    if normalized in exact:
        return True
    phrases = (
        "确认生成",
        "确认提交",
        "开始生成",
        "按此生成",
        "按这个生成",
        "就按这个",
        "按这个来",
        "可以生成",
        "生成吧",
        "提交吧",
        "开始吧",
        "没问题",
    )
    return any(phrase in normalized for phrase in phrases)


def _requests_poetry_action(text: str) -> bool:
    normalized = re.sub(r"[\s，。！？、,.!?]", "", text).lower()
    preview_only = (
        "先看方案",
        "先整理方案",
        "先给方案",
        "先讨论",
        "暂不生成",
        "暂时不生成",
        "不要生成",
        "别生成",
        "只看提示词",
        "看看提示词",
    )
    if any(phrase in normalized for phrase in preview_only):
        return False
    if _is_explicit_confirmation(text):
        return True
    if re.search(r"(?:写|生成|创作)[一二三四五1-5]首", normalized):
        return True
    action_phrases = (
        "写一首",
        "写首",
        "生成一首",
        "生成诗",
        "生成词",
        "创作一首",
        "创作诗",
        "创作词",
        "重写",
        "改写",
        "润色",
        "修改第",
        "改第",
    )
    return any(phrase in normalized for phrase in action_phrases)


def _requested_candidate_count(text: str) -> int:
    normalized = re.sub(r"\s", "", text)
    match = re.search(r"(100|[1-9]\d?|[一二三四五六七八九十百]+)(?:首|个候选|个版本)", normalized)
    if not match:
        return 1
    values = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    token = match.group(1)
    if token.isdigit():
        return int(token)
    if token in {"十", "百", "一百"}:
        return 10 if token == "十" else 100
    if token.startswith("十"):
        return 10 + values.get(token[1:], 0)
    if token.endswith("十"):
        return values.get(token[:-1], 0) * 10
    if "十" in token:
        tens, ones = token.split("十", 1)
        return values.get(tens, 0) * 10 + values.get(ones, 0)
    return values.get(token, 1)


def _reject_unknown_options(
    options: Mapping[str, Any], allowed: set[str], meter_type: str
) -> None:
    unknown = set(options).difference(allowed)
    if unknown:
        raise ValueError(f"{meter_type} 不支持 task_options: {sorted(unknown)}")

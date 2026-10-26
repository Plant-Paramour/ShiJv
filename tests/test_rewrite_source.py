from pathlib import Path

import pytest

from apps.agent.framework import ToolContext
from apps.agent.tools import AgentToolbox
from shiju.rewriting.source import numbered_source, select_rewrite_lines, validate_rewrite_result

ROOT = Path(__file__).resolve().parents[1]
POEM = "空山新雨后，天气晚来秋。\n明月松间照，清泉石上流。"
CONTEXT = {"meter_type": "唐诗", "form_name": "五言绝句", "rhyme_dict_name": "Xinyun"}


class SavedPoemJobs:
    def get_poem_source(self, poem_id):
        return numbered_source(POEM, CONTEXT, ROOT, poem_id=poem_id)


def test_rewrite_tool_marks_checked_targets_and_preserves_other_sentences():
    from apps.agent.prompts import SYSTEM_PROMPT

    toolbox = AgentToolbox(ROOT, SavedPoemJobs(), allow_text_submission=False)
    context = ToolContext("勾选第2、第3句重写，其余保留", 1)
    source = toolbox.execute("get_poem_source", {"resource_id": "saved-poem"}, context)
    schema = next(tool["function"] for tool in toolbox.schemas() if tool["function"]["name"] == "prepare_rewrite")
    assert "改写面板中勾选表示重写，未勾选表示保留" in SYSTEM_PROMPT
    assert "改写面板中勾选表示重写，未勾选表示保留" in source["instruction"]
    assert "不是未勾选的保留句" in schema["parameters"]["properties"]["target_line_ids"]["description"]
    targets = [line for line in source["lines"] if line["number"] in (2, 3)]
    result = toolbox.execute("prepare_rewrite", {
        "source_ref": source["source_ref"], "target_line_ids": [line["line_id"] for line in targets],
        "expected_target_lines": [{"line_id": line["line_id"], "text": line["text"]} for line in targets],
        "requirement": "重写第二、三句，其余保留",
    }, context)
    assert result["sentence_selection"] == {
        "checked": "重写", "unchecked": "保留",
        "checked_line_numbers": [2, 3], "unchecked_line_numbers": [1, 4],
    }
    assert result["kind"] == "rewrite"
    assert result["proposal"]["target_line_numbers"] == (2, 3)
    assert result["proposal"]["fixed_lines"] == {"1": "空山新雨后", "4": "清泉石上流"}


def test_partial_completion_tool_marks_only_supplied_fixed_sentences_as_checked():
    toolbox = AgentToolbox(ROOT, object(), allow_text_submission=False)
    result = toolbox.execute("prepare_partial_generation", {
        **CONTEXT, "fixed_lines": {"1": "空山新雨后", "2": "天气晚来秋"}, "requirement": "保留前两句，补完其余诗句",
    }, ToolContext("保留前两句，补完其余诗句", 1))
    assert result["sentence_selection"]["checked_line_numbers"] == [1, 2]
    assert result["sentence_selection"]["checked"] == "保留"
    assert result["sentence_selection"]["unchecked_empty"] == "补完"
    assert "original_text" not in result["proposal"]


def test_edited_fixed_sentences_are_saved_against_the_original_source():
    request = {"original_text": POEM, "target_line_numbers": [2, 4],
               "sentence_inputs": {"1": "青山新雨后", "2": "天气晚来秋", "3": "明月松间照", "4": ""},
               "fixed_lines": {"1": "青山新雨后", "3": "明月松间照"}}
    generated = "青山新雨后，秋色满江洲。明月松间照，清风入画楼。"
    saved = validate_rewrite_result(request, generated)
    assert saved == POEM.replace("空山", "青山").replace("天气晚来秋", "秋色满江洲").replace("清泉石上流", "清风入画楼")
    with pytest.raises(ValueError, match="保留的第 1 句"):
        validate_rewrite_result(request, generated.replace("青山", "空山"))


def test_numbered_source_and_selection_require_matching_original_text():
    source = numbered_source(POEM, CONTEXT, ROOT, poem_id="source")
    selected = select_rewrite_lines(source, source["source_version"], ["s1-l2"], {"s1-l2": "天气晚来秋"})
    assert selected["target_line_numbers"] == [2]
    assert selected["fixed_lines"] == {"1": "空山新雨后", "3": "明月松间照", "4": "清泉石上流"}
    with pytest.raises(ValueError, match="编号与原句不一致"):
        select_rewrite_lines(source, source["source_version"], ["s1-l2"], {"s1-l2": "明月松间照"})
    with pytest.raises(ValueError, match="版本不一致"):
        select_rewrite_lines(source, "stale", ["s1-l2"], {"s1-l2": "天气晚来秋"})
    with pytest.raises(ValueError, match="不重复"):
        select_rewrite_lines(source, source["source_version"], ["s1-l2", "s1-l2"], {"s1-l2": "天气晚来秋"})


def test_songci_numbering_keeps_caesuras_inside_sentences():
    text = "柳丝蘸晚。烟波外、轻舟一叶斜岸。曾共画楼，倚栏听雨？当时笑眼。风前鬓畔。却无奈春深梦断。忆那年花下盟言？竟似空传雁。\n暮色沉云散，寒更滴窗？旧约如弹。谁将素手？重拾罗、已成霜满。心事难酬？泪湿锦笺犹浅点。悔教人错认天涯？误了半。"
    source = numbered_source(text, {"meter_type": "宋词", "form_name": "凄凉犯", "variant_name": "姜夔"}, ROOT)
    assert len(source["lines"]) == 18
    assert source["lines"][1]["source_text"] == "烟波外、轻舟一叶斜岸"
    assert source["lines"][16]["line_id"] == "s2-l8"
    selected = select_rewrite_lines(source, source["source_version"], ["s2-l8"], {"s2-l8": "悔教人错认天涯"})
    assert selected["target_line_numbers"] == [17]
    assert len(selected["fixed_lines"]) == 17


def test_result_validation_rejects_changed_locked_lines_and_keeps_formatting():
    request = {"original_text": POEM, "target_line_numbers": [2]}
    generated = "空山新雨后。秋色满江洲，明月松间照。清泉石上流。"
    assert validate_rewrite_result(request, generated) == POEM.replace("天气晚来秋", "秋色满江洲")
    with pytest.raises(ValueError, match="保留的第 1 句"):
        validate_rewrite_result(request, generated.replace("空山", "青山"))
    with pytest.raises(ValueError, match="句数"):
        validate_rewrite_result(request, "空山新雨后。")


def test_songci_rewrite_validation_keeps_template_stanza_boundary_newline():
    request = {
        "original_text": "春花，明月。江水，归舟。远山，斜阳。秋风，落叶。长天，孤雁。",
        "target_line_numbers": [2],
        "meter_type": "宋词",
        "form_name": "一剪梅",
        "variant_name": "周邦彦",
    }
    generated = request["original_text"]

    result = validate_rewrite_result(request, generated)

    assert result.count("\n") == 1
    assert result.splitlines() == ["春花，明月。江水，归舟。远山，斜阳。", "秋风，落叶。长天，孤雁。"]


def test_rewrite_source_tool_avoids_model_copying_original_or_fixed_lines():
    class Jobs:
        def get_poem_source(self, poem_id):
            return numbered_source(POEM, CONTEXT, ROOT, poem_id=poem_id)

    toolbox = AgentToolbox(ROOT, Jobs(), allow_text_submission=False)
    context = ToolContext("只改第二句", 1)
    source = toolbox.execute("get_poem_source", {"poem_id": "source"}, context)
    schema = next(tool["function"] for tool in toolbox.schemas() if tool["function"]["name"] == "prepare_rewrite")
    assert {"source_ref", "target_line_ids", "expected_target_lines"} <= set(schema["parameters"]["required"])
    args = {"source_ref": source["source_ref"],
            "target_line_ids": ["s1-l2"], "expected_target_lines": {"s1-l2": "天气晚来秋"},
            "requirement": "描写秋日江景"}
    proposal = toolbox.execute("prepare_rewrite", args, context)["proposal"]
    assert proposal["original_text"] == POEM
    assert proposal["source_poem_id"] == "source"
    assert len(proposal["fixed_lines"]) == 3
    assert proposal["source_lines"][1]["number"] == 2
    assert not toolbox._rewrite_sources
    array_proposal = toolbox.execute("prepare_rewrite", {
        **args, "expected_target_lines": [{"line_id": "s1-l2", "text": "天气晚来秋"}],
    }, context)["proposal"]
    assert array_proposal["fixed_lines"] == proposal["fixed_lines"]
    with pytest.raises(ValueError, match="不能手工填写"):
        toolbox.execute("prepare_rewrite", {**args, "fixed_lines": {"1": "幻觉原文"}}, context)
    schema = next(tool["function"] for tool in toolbox.schemas() if tool["function"]["name"] == "prepare_rewrite")
    assert "original_text" not in schema["parameters"]["properties"]


def test_external_poem_source_requires_saved_poem_id():
    toolbox = AgentToolbox(ROOT, object(), allow_text_submission=False)
    context = ToolContext("修改第二句", 1)
    with pytest.raises(ValueError, match="作品 ID"):
        toolbox.execute("get_poem_source", {"original_text": POEM, **CONTEXT}, context)
    with pytest.raises(ValueError, match="作品 ID"):
        toolbox.execute("prepare_rewrite", {
            "original_text": POEM, **CONTEXT, "target_line_numbers": [2], "requirement": "改写",
        }, context)


def test_legacy_generation_headers_are_not_numbered_as_poem_sentences():
    source = numbered_source("构思：秋景。\n[title]秋景\n[content]" + POEM, CONTEXT, ROOT)
    assert source["original_text"] == POEM
    assert len(source["lines"]) == 4


def test_nondefault_songci_variant_is_in_proposal_preview():
    text = "柳丝蘸晚。烟波外、轻舟一叶斜岸。曾共画楼，倚栏听雨？当时笑眼。风前鬓畔。却无奈春深梦断。忆那年花下盟言？竟似空传雁。\n暮色沉云散，寒更滴窗？旧约如弹。谁将素手？重拾罗、已成霜满。心事难酬？泪湿锦笺犹浅点。悔教人错认天涯？误了半。"
    toolbox = AgentToolbox(ROOT, object(), allow_text_submission=False)
    result = toolbox._store_proposal("partial_generate", {
        "original_text": text, "meter_type": "宋词", "form_name": "凄凉犯", "variant_name": "姜夔",
    }, ToolContext("改写", 1))
    assert result["proposal"]["variant_name"] == "姜夔"


def test_source_schema_separates_saved_candidates_from_external_text():
    toolbox = AgentToolbox(ROOT, object(), allow_text_submission=False)
    schemas = {tool["function"]["name"]: tool["function"]["parameters"] for tool in toolbox.schemas()}
    assert set(schemas["get_poem_source"]["properties"]) == {"resource_id", "candidate_ordinal"}
    assert "number_external_poem" not in schemas


def test_job_query_returns_one_body_and_explicit_source_arguments():
    snapshot = {"job_id": "job", "status": "succeeded", "candidates": [
        {"ordinal": 2, "status": "succeeded", "title": "秋景", "content": POEM, "raw_text": "说明" + POEM},
    ], "result": {"full_text": POEM}}
    class Jobs:
        def get_job(self, job_id):
            return snapshot
    toolbox = AgentToolbox(ROOT, Jobs(), allow_text_submission=False)
    result = toolbox.execute("get_poetry_job", {"job_id": "job"}, ToolContext("第二首", 1))
    assert "candidates" not in result and "result" not in result
    assert result["generated_poems"][0]["source_arguments"] == {"resource_id": "job", "candidate_ordinal": 2}
    assert snapshot["candidates"][0]["raw_text"] == "说明" + POEM


@pytest.mark.parametrize("tool,arguments", [
    ("get_poetry_job", {"job_id": "saved-poem"}),
    ("get_poem_source", {"job_id": "saved-poem"}),
])
def test_task_lookup_falls_back_to_saved_poem(tool, arguments):
    class Jobs:
        def get_job(self, resource_id):
            assert resource_id == "saved-poem"
            raise ValueError("找不到当前用户的任务")

        def get_poem_source(self, resource_id):
            assert resource_id == "saved-poem"
            return numbered_source(POEM, CONTEXT, ROOT, poem_id=resource_id)

    toolbox = AgentToolbox(ROOT, Jobs(), allow_text_submission=False)
    result = toolbox.execute(tool, arguments, ToolContext("点评这个作品ID", 1))
    assert result["original_text"] == POEM
    assert result["source_poem_id"] == "saved-poem"
    assert result["source_ref"] in toolbox._source_refs


def test_poem_lookup_falls_back_to_task():
    class Jobs:
        def get_poem_source(self, resource_id):
            if resource_id == "task":
                raise ValueError("找不到可访问的来源作品")
            return numbered_source(POEM, CONTEXT, ROOT, poem_id=resource_id)

        def get_job(self, resource_id):
            assert resource_id == "task"
            return {"job_id": "task", "candidates": [{"ordinal": 1, "content": POEM}]}

        def get_candidate_poem(self, job_id, ordinal):
            assert (job_id, ordinal) == ("task", 1)
            return {"id": "saved-poem"}

    toolbox = AgentToolbox(ROOT, Jobs(), allow_text_submission=False)
    result = toolbox.execute("get_poem_source", {"resource_id": "task"}, ToolContext("读取作品", 1))
    assert result["source_poem_id"] == "saved-poem"


def test_failed_task_and_poem_lookups_stop_and_preserve_task_error():
    calls = []

    class Jobs:
        def get_job(self, resource_id):
            calls.append("task")
            raise ValueError("找不到当前用户的任务")

        def get_poem_source(self, resource_id):
            calls.append("poem")
            raise ValueError("找不到可访问的来源作品")

    toolbox = AgentToolbox(ROOT, Jobs(), allow_text_submission=False)
    with pytest.raises(ValueError, match="找不到当前用户的任务"):
        toolbox.execute("get_poetry_job", {"job_id": "missing"}, ToolContext("读取", 1))
    assert calls == ["task", "poem"]


def test_only_completed_candidate_preserves_its_actual_ordinal():
    class Jobs:
        def get_latest_job(self):
            return {"job_id": "job", "candidates": [{"ordinal": 2, "content": POEM}]}
        def get_candidate_poem(self, job_id, ordinal):
            assert ordinal == 2
            return {"id": "candidate2"}
        def get_poem_source(self, poem_id):
            return numbered_source(POEM, CONTEXT, ROOT, poem_id=poem_id)
    toolbox = AgentToolbox(ROOT, Jobs(), allow_text_submission=False)
    result = toolbox.execute("get_poem_source", {}, ToolContext("修改", 1))
    assert result["source_poem_id"] == "candidate2"


def test_source_ref_binds_version_without_model_copying_ids():
    class Jobs:
        def get_poem_source(self, poem_id):
            return numbered_source(POEM, CONTEXT, ROOT, poem_id=poem_id)
    toolbox = AgentToolbox(ROOT, Jobs(), allow_text_submission=False)
    context = ToolContext("改第二句", 1)
    source = toolbox.execute("get_poem_source", {"resource_id": "source"}, context)
    result = toolbox.execute("prepare_rewrite", {
        "source_ref": source["source_ref"], "target_line_ids": ["s1-l2"],
        "expected_target_lines": [{"line_id": "s1-l2", "text": "天气晚来秋"}], "requirement": "描写秋江",
    }, context)
    assert result["proposal"]["source_poem_id"] == "source"
    assert result["proposal"]["source_version"] == source["source_version"]
    with pytest.raises(ValueError, match="失效"):
        toolbox.execute("prepare_rewrite", {"source_ref": "source-999"}, context)


@pytest.mark.parametrize("field,value", [
    ("original_text", POEM), ("source_poem_id", "source"), ("source_version", "old"),
    ("source_ref", "source-1"), ("target_line_ids", ["s1-l2"]),
    ("target_line_numbers", [2]), ("expected_target_lines", [{"line_id": "s1-l2", "text": "天气晚来秋"}]),
])
def test_partial_completion_rejects_rewrite_source_fields(field, value):
    toolbox = AgentToolbox(ROOT, object(), allow_text_submission=False)
    with pytest.raises(ValueError, match="补完只接受 fixed_lines"):
        toolbox.execute("prepare_partial_generation", {
            **CONTEXT, "fixed_lines": {"1": "空山新雨后"}, "requirement": "补完残稿", field: value,
        }, ToolContext("补完残稿", 1))


def test_rewrite_source_ref_detects_changes_after_reading():
    class Jobs:
        content = POEM

        def get_poem_source(self, poem_id):
            return numbered_source(self.content, CONTEXT, ROOT, poem_id=poem_id)

    jobs = Jobs()
    toolbox = AgentToolbox(ROOT, jobs, allow_text_submission=False)
    context = ToolContext("改第二句", 1)
    source = toolbox.execute("get_poem_source", {"resource_id": "source"}, context)
    jobs.content = POEM.replace("天气晚来秋", "秋色满江洲")

    with pytest.raises(ValueError, match="版本不一致"):
        toolbox.execute("prepare_rewrite", {
            "source_ref": source["source_ref"], "target_line_ids": ["s1-l2"],
            "expected_target_lines": [{"line_id": "s1-l2", "text": "天气晚来秋"}],
            "requirement": "描写秋江",
        }, context)


def test_model_projection_hides_internal_pointers_but_ui_retains_them():
    import json
    from apps.agent.framework import AgentSession
    body = {"ok": True, "result": {"sources": [{"source_ref": "source-1", "source_poem_id": "private-id", "source_version": "hash", "lines": []}]}}
    raw = {"name": "get_poem_source", "content": json.dumps(body)}
    projected = json.loads(AgentSession._model_tool_result(raw)["content"])
    assert projected["result"]["sources"] == [{"source_ref": "source-1", "lines": []}]
    assert "source_poem_id" in json.loads(raw["content"])["result"]["sources"][0]

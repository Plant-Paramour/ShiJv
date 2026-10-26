from pathlib import Path

from shiju.tasks import (
    HanpaiOptions,
    PailvOptions,
    TangOptions,
    TaskContext,
    TaskRequest,
    default_task_registry,
    parse_pailv_format,
)
from shiju.policies import RepetitionPenaltyPolicy

from conftest import FakeLexicon, FakeTokenizer, FakeVocab


def test_tang_task_does_not_read_meter_source(tmp_path):
    tokenizer = FakeTokenizer()
    lexicon = FakeLexicon()
    vocab = FakeVocab(tokenizer, lexicon)
    missing_source = tmp_path / "missing-songci-meter"
    context = TaskContext(tokenizer, vocab, lexicon, missing_source, 50.0)
    request = TaskRequest(
        meter_type="唐诗",
        form_name="七律",
        theme="秋夜",
        rhyme_dict_name="Pinshui",
        use_thinking=False,
    )

    runtime = default_task_registry().create(request, context)

    assert len(runtime.profile.layout.lines) == 8
    assert not missing_source.exists()


def test_tang_task_propagates_aojiu_option_to_profile_policy_and_prompt(tmp_path):
    tokenizer = FakeTokenizer()
    lexicon = FakeLexicon()
    vocab = FakeVocab(tokenizer, lexicon)
    context = TaskContext(tokenizer, vocab, lexicon, tmp_path / "missing", 50.0)
    request = TaskRequest(
        meter_type="唐诗",
        form_name="五言绝句",
        theme="秋夜",
        rhyme_dict_name="Pinshui",
        tang=TangOptions(allow_aojiu=True),
    )

    runtime = default_task_registry().create(request, context)
    prompt = "\n".join(message["content"] for message in runtime.messages)

    assert runtime.profile.allow_aojiu is True
    assert runtime.policy_tiers[0].policies[0]._allow_aojiu is True
    assert "特拗交换" in prompt


def test_template_task_reuses_resolved_default_variant_in_prompt():
    tokenizer = FakeTokenizer()
    lexicon = FakeLexicon()
    vocab = FakeVocab(tokenizer, lexicon)
    meter_source = Path(__file__).resolve().parents[1] / "Songci_Meter"
    context = TaskContext(tokenizer, vocab, lexicon, meter_source, 50.0)
    request = TaskRequest(
        meter_type="宋词",
        form_name="浣溪沙",
        theme="春日",
        rhyme_dict_name="Xinyun",
        use_thinking=False,
    )

    runtime = default_task_registry().create(request, context)
    prompt = "\n".join(message["content"] for message in runtime.messages)
    output_template = "[title]浣溪沙·作品名\n[content]正文"

    assert runtime.profile.template.variant_name == "韩偓"
    assert "采用变体：韩偓" in prompt
    assert output_template in runtime.messages[0]["content"]
    assert output_template in runtime.messages[-1]["content"]
    assert "[title]浣溪沙·春思\n[content]柳色含烟" in prompt
    assert "两个标记均不可省略、修改或替换" in prompt


def test_hanpai_task_composes_format_season_prosody_and_rhyme_options(tmp_path):
    tokenizer = FakeTokenizer()
    lexicon = FakeLexicon()
    vocab = FakeVocab(tokenizer, lexicon)
    missing_source = tmp_path / "missing-songci-meter"
    context = TaskContext(tokenizer, vocab, lexicon, missing_source, 50.0)
    request = TaskRequest(
        meter_type="汉俳",
        form_name="汉俳",
        theme="初秋离别",
        rhyme_dict_name="Xinyun",
        use_thinking=False,
        hanpai=HanpaiOptions(
            line_pattern="3-5-3",
            season_words=("寒蝉", "雁影"),
            forbid_isolated_level=True,
            allow_aojiu=True,
            forbid_three_same_ending=True,
            rhyme_scheme="baa",
        ),
    )

    runtime = default_task_registry().create(request, context)
    prompt = "\n".join(message["content"] for message in runtime.messages)

    assert [line.length for line in runtime.profile.layout.lines] == [3, 5, 3]
    assert runtime.profile.rhyme_scheme == "BAA"
    assert runtime.profile.rhyme_lines == frozenset({1, 2})
    assert "必须且只能从候选季语 “寒蝉”、“雁影” 中选择一个" in prompt
    assert "允许使用邻位平声完成拗救" in prompt
    assert "句尾不得出现三连平或三连仄" in prompt
    assert "采用 BAA 式" in prompt
    assert "正文中不得输出顿号、逗号、句号等句内标点" in prompt
    transformed = runtime.process_output(
        "规划、保留\n[title]汉俳·秋思\n[content]寒蝉、声渐远\n学姐、去何、方云鬓"
    )
    assert transformed == (
        "规划、保留\n[title]汉俳·秋思\n[content]寒蝉声渐远\n学姐去何方云鬓"
    )
    assert not missing_source.exists()


def test_hanpai_defaults_require_season_word_and_consistent_random_rhyme(tmp_path, monkeypatch):
    monkeypatch.setattr("shiju.constraints.random.choice", lambda choices: "ABA")
    tokenizer = FakeTokenizer()
    lexicon = FakeLexicon()
    vocab = FakeVocab(tokenizer, lexicon)
    context = TaskContext(tokenizer, vocab, lexicon, tmp_path / "missing", 50.0)
    request = TaskRequest(
        meter_type="汉俳",
        form_name="汉俳",
        theme="山居",
        rhyme_dict_name="Xinyun",
        use_thinking=False,
    )

    runtime = default_task_registry().create(request, context)
    prompt = "\n".join(message["content"] for message in runtime.messages)

    assert [line.length for line in runtime.profile.layout.lines] == [5, 7, 5]
    assert "仍必须自行选择并使用一个明显、具体的季语" in prompt
    assert "禁止未获补救的孤平" in prompt
    assert "每行句尾不得出现三连平或三连仄" in prompt
    assert "闲寂古池旁" in prompt
    assert runtime.profile.rhyme_scheme == "ABA"
    assert "采用 ABA 式" in prompt
    assert "第一、三行句尾押同一韵部，第二行不押" in prompt
    assert "不设置押韵要求" not in prompt


def test_hanpai_explicit_none_keeps_profile_and_prompt_without_rhyme(tmp_path):
    tokenizer = FakeTokenizer()
    lexicon = FakeLexicon()
    context = TaskContext(tokenizer, FakeVocab(tokenizer, lexicon), lexicon, tmp_path / "missing", 50.0)
    request = TaskRequest(meter_type="汉俳", form_name="汉俳", theme="山居",
                          rhyme_dict_name="Xinyun", hanpai=HanpaiOptions(rhyme_scheme=None))

    runtime = default_task_registry().create(request, context)
    prompt = "\n".join(message["content"] for message in runtime.messages)

    assert runtime.profile.rhyme_scheme is None
    assert "不设置押韵要求" in prompt
    assert "采用 AAA 式" not in prompt and "采用 ABA 式" not in prompt and "采用 BAA 式" not in prompt


def test_task_factories_receive_configured_boundary_penalty(tmp_path):
    tokenizer = FakeTokenizer()
    lexicon = FakeLexicon()
    vocab = FakeVocab(tokenizer, lexicon)
    context = TaskContext(tokenizer, vocab, lexicon, tmp_path / "missing", 12.5)
    request = TaskRequest(
        meter_type="汉俳",
        form_name="汉俳",
        theme="秋夜",
        rhyme_dict_name="Xinyun",
    )

    runtime = default_task_registry().create(request, context)
    boundary_policy = runtime.policy_tiers[0].policies[-1]

    assert boundary_policy._penalty == 12.5


def test_pailv_task_supports_twelve_lines_and_strict_prompt(tmp_path):
    tokenizer = FakeTokenizer()
    lexicon = FakeLexicon()
    vocab = FakeVocab(tokenizer, lexicon)
    context = TaskContext(tokenizer, vocab, lexicon, tmp_path / "missing", 50.0)
    request = TaskRequest(
        meter_type="排律",
        form_name="五言排律",
        num_lines=16,
        theme="秋江怀远",
        rhyme_dict_name="Pinshui",
        use_thinking=False,
        pailv=PailvOptions(allow_aojiu=True),
    )

    runtime = default_task_registry().create(request, context)
    prompt = "\n".join(message["content"] for message in runtime.messages)

    assert len(runtime.profile.layout.lines) == 16
    assert all(line.length == 5 for line in runtime.profile.layout.lines)
    assert "严格遵守替、对、粘" in prompt
    assert "首句可押可不押" in prompt
    assert "不换韵、不通押邻韵" in prompt
    assert "中间各联必须逐联对仗" in prompt
    assert runtime.policy_tiers[0].name == "pailv-strict"
    assert isinstance(runtime.policy_tiers[0].policies[1], RepetitionPenaltyPolicy)


def test_pailv_task_propagates_permissive_polyphonic_mode(tmp_path):
    tokenizer = FakeTokenizer()
    lexicon = FakeLexicon()
    vocab = FakeVocab(tokenizer, lexicon)
    context = TaskContext(tokenizer, vocab, lexicon, tmp_path / "missing", 50.0)
    request = TaskRequest(
        meter_type="排律",
        form_name="七言排律",
        num_lines=12,
        theme="秋夜",
        rhyme_dict_name="Xinyun",
        strict_polyphonic=False,
    )

    runtime = default_task_registry().create(request, context)
    verifier = runtime.policy_tiers[0].policies[0]

    assert runtime.processor_config.strict_polyphonic is False
    assert verifier._strict_polyphonic is False


def test_parse_pailv_format_uses_a_separate_line_count():
    assert parse_pailv_format("七言排律", 20) == (7, 20)
    assert parse_pailv_format("五言排律", 12) == (5, 12)
    assert parse_pailv_format("五言排律", 100) == (5, 100)


def test_parse_pailv_format_rejects_short_or_odd_counts():
    import pytest

    with pytest.raises(ValueError):
        parse_pailv_format("五言排律", 8)
    with pytest.raises(ValueError):
        parse_pailv_format("七言排律", 11)
    with pytest.raises(ValueError):
        parse_pailv_format("七言排律", None)
    with pytest.raises(ValueError):
        parse_pailv_format("五言排律16句", 16)

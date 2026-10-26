from pathlib import Path

import pytest

from shiju.constraints import ContextConstraintError, RelationalConstraintProfile, TemplateConstraintProfile
from shiju.data import MeterTemplateRepository, RhymeLexicon
from shiju.domain import RhymeMode, StepKind
from shiju.state import GenerationController, GenerationStateMachine

from conftest import FakeLexicon


ROOT = Path(__file__).resolve().parents[1]


def test_template_session_expands_zhong_and_locks_rhyme():
    template = MeterTemplateRepository(ROOT / "Songci_Meter").get("浣溪沙")
    lexicon = RhymeLexicon(ROOT / "Rhyme" / "Xinyun.json")
    profile = TemplateConstraintProfile(template, lexicon)
    session = profile.create_session()
    controller = GenerationController(GenerationStateMachine(profile.layout), session)

    tones = {item.tones for item in controller.allowed_patterns(max_length=1)}
    assert tones == {"平", "仄"}
    controller.advance("山水清风入画春")
    assert controller.snapshot().step is StepKind.PUNCTUATION
    assert session.locked_rhyme_parts.get(1) in lexicon.get_rhyme_part_by_tone("春", "平")


def test_shaobian_fixed_rhymes_allow_level_and_oblique_in_same_part():
    template = MeterTemplateRepository(ROOT / "Songci_Meter").get("哨遍", "苏轼")
    lexicon = RhymeLexicon(ROOT / "Rhyme" / "Xinyun.json")
    # 苏轼体第七句“露未晞”是平声叶韵，与第九句共用同一韵组。
    assert template.lines[6].rhyme_group == 1
    assert template.lines[8].rhyme_group == 1
    session = TemplateConstraintProfile(template, lexicon).create_session()
    session.prime_fixed_lines({6: "那年三", 8: "共寻溪畔石阶漫"}, strict_polyphonic=False)
    assert session.locked_rhyme_parts == {1: "八寒"}


def test_shaobian_mixed_tones_still_require_same_rhyme_part():
    template = MeterTemplateRepository(ROOT / "Songci_Meter").get("哨遍", "苏轼")
    lexicon = RhymeLexicon(ROOT / "Rhyme" / "Xinyun.json")
    session = TemplateConstraintProfile(template, lexicon).create_session()
    with pytest.raises(ContextConstraintError, match="不属于同一韵部"):
        session.prime_fixed_lines({5: "语细情初见些慢", 8: "共踏溪畔石阶梦"}, strict_polyphonic=False)


def test_shaobian_oblique_first_rhyme_locks_part_during_generation():
    template = MeterTemplateRepository(ROOT / "Songci_Meter").get("哨遍", "苏轼")
    lexicon = RhymeLexicon(ROOT / "Rhyme" / "Xinyun.json")
    profile = TemplateConstraintProfile(template, lexicon)
    session = profile.create_session()
    controller = GenerationController(GenerationStateMachine(profile.layout), session)
    controller.advance("柳色未开，花影半遮，旧巷曾逢面")
    assert session.locked_rhyme_parts == {1: "八寒"}


def test_relational_session_infers_base_tone_and_first_line_rhyme():
    lexicon = FakeLexicon()
    profile = RelationalConstraintProfile(5, 4, "平韵", lexicon)
    session = profile.create_session()
    controller = GenerationController(GenerationStateMachine(profile.layout), session)

    controller.advance("山雨")
    info = controller.candidate_context()
    assert info.base_tone == 1
    controller.advance("山雨山")
    info = controller.candidate_context()
    assert info.line0_rhymes is True
    assert info.locked_rhyme_parts == frozenset({"一"})


def test_relational_even_line_uses_locked_rhyme():
    lexicon = FakeLexicon()
    profile = RelationalConstraintProfile(5, 4, "平韵", lexicon)
    session = profile.create_session()
    controller = GenerationController(GenerationStateMachine(profile.layout), session)
    controller.advance("山雨山雨山，")
    controller.advance("雨山雨山")

    final_patterns = controller.allowed_patterns(max_length=1)
    rhyme_patterns = [item for item in final_patterns if item.rhyme.mode is RhymeMode.PARTS]
    assert rhyme_patterns
    assert all(item.rhyme.parts == frozenset({"一"}) for item in rhyme_patterns)


def test_relational_first_line_oblique_end_does_not_constrain_later_rhyme_parts():
    lexicon = FakeLexicon()
    profile = RelationalConstraintProfile(5, 4, "平韵", lexicon)
    controller = GenerationController(
        GenerationStateMachine(profile.layout),
        profile.create_session(),
    )

    controller.advance("山雨山雨雨")
    info = controller.candidate_context()

    assert info.line0_rhymes is False
    assert info.excluded_rhyme_parts is None


def test_relational_first_line_polyphonic_end_obeys_selected_mode():
    lexicon = FakeLexicon()
    lexicon.tones["重"] = ["平", "仄"]
    lexicon.parts["重"] = ["一"]

    strict = RelationalConstraintProfile(
        5, 4, "平韵", lexicon, strict_polyphonic=True
    ).create_session()
    permissive = RelationalConstraintProfile(
        5, 4, "平韵", lexicon, strict_polyphonic=False
    ).create_session()
    strict_controller = GenerationController(
        GenerationStateMachine(RelationalConstraintProfile(5, 4, "平韵", lexicon).layout),
        strict,
    )
    permissive_controller = GenerationController(
        GenerationStateMachine(RelationalConstraintProfile(5, 4, "平韵", lexicon).layout),
        permissive,
    )

    strict_controller.advance("山雨山雨重")
    permissive_controller.advance("山雨山雨重")

    assert strict_controller.candidate_context().line0_rhymes is False
    assert permissive_controller.candidate_context().line0_rhymes is True


def test_relational_session_repeats_dual_and_sticky_pattern_for_pailv():
    lexicon = FakeLexicon()
    profile = RelationalConstraintProfile(5, 12, "平韵", lexicon)
    assert len(profile.layout.lines) == 12

    session = profile.create_session()
    controller = GenerationController(GenerationStateMachine(profile.layout), session)
    controller.advance("山雨")
    first_base = controller.candidate_context().base_tone

    controller.advance("山雨山，")
    assert controller.snapshot().line_index == 1
    assert controller.candidate_context().base_tone == 1 - first_base

    controller.advance("山雨山雨山。")
    assert controller.snapshot().line_index == 2
    assert controller.candidate_context().base_tone == 1 - first_base

    controller.advance("山雨山雨山，山雨山雨山。")
    assert controller.snapshot().line_index == 4
    assert controller.candidate_context().base_tone == first_base


def test_relational_fixed_middle_couplet_locks_rhyme_and_global_pattern():
    lexicon = FakeLexicon()
    profile = RelationalConstraintProfile(5, 8, "平韵", lexicon)
    session = profile.create_session()

    session.prime_fixed_lines(
        {
            4: "山山山雨雨",
            5: "雨雨雨山春",
        }
    )

    controller = GenerationController(GenerationStateMachine(profile.layout), session)
    context = controller.candidate_context()
    assert context.global_base_tone == 0
    assert context.base_tone == 0
    assert context.locked_rhyme_parts == frozenset({"一"})

    controller.advance("山山山雨")
    endings = controller.allowed_patterns(max_length=1)
    level = [item for item in endings if item.tones == "平"]
    oblique = [item for item in endings if item.tones == "仄"]
    assert level and all(item.rhyme.mode is RhymeMode.PARTS for item in level)
    assert all(item.rhyme.parts == frozenset({"一"}) for item in level)
    assert oblique and all(item.rhyme.mode is RhymeMode.NONE for item in oblique)


def test_pailv_only_opens_major_ao_position_on_non_rhyming_lines():
    lexicon = FakeLexicon()
    profile = RelationalConstraintProfile(7, 12, "平韵", lexicon, allow_aojiu=True)
    session = profile.create_session()
    controller = GenerationController(GenerationStateMachine(profile.layout), session)

    controller.advance("山山")
    assert set(session.allowed_tones_at(controller.snapshot(), 5)) == {"平", "仄"}

    controller.advance("山山山山山，")
    assert controller.snapshot().line_index == 1
    assert session.allowed_tones_at(controller.snapshot(), 5) == ("仄",)

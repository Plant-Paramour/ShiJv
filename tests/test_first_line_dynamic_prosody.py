from pathlib import Path

import pytest

from shiju.candidates import has_viable_tang_completion, first_line_reading_is_viable
from shiju.constraints import RelationalConstraintProfile
from shiju.constraints import ContextConstraintError
from shiju.data import RhymeLexicon
from shiju.generation.engine import GenerationEngine
from shiju.state import GenerationController, GenerationStateMachine
from types import SimpleNamespace


@pytest.fixture
def lexicon():
    return RhymeLexicon(Path(__file__).resolve().parents[1] / "Rhyme" / "Pinshui.json")


@pytest.mark.parametrize("strict", [False, True])
@pytest.mark.parametrize("allow_aojiu", [False, True])
def test_ambiguous_first_ending_rejects_when_both_readings_fail(lexicon, strict, allow_aojiu):
    assert not has_viable_tang_completion(
        "圆腹短翘戏石横", 7, 1, 2, lexicon, allow_aojiu=allow_aojiu,
        strict_polyphonic=strict, is_first_line=True, rhyme_tone=0,
    )


def test_each_reading_is_evaluated_with_its_own_first_line_pattern(lexicon):
    options = dict(allow_aojiu=True, strict_polyphonic=False)
    assert first_line_reading_is_viable("圆腹春翘戏石横", 7, 1, 0, lexicon, "平", **options)
    assert not first_line_reading_is_viable("圆腹春翘戏石横", 7, 1, 0, lexicon, "仄", **options)
    assert first_line_reading_is_viable("圆腹短翘风石横", 7, 1, 0, lexicon, "仄", **options)
    assert not first_line_reading_is_viable("圆腹短翘风石横", 7, 1, 0, lexicon, "平", **options)


def test_generated_first_line_uses_viable_oblique_reading_without_locking_rhyme(lexicon):
    profile = RelationalConstraintProfile(7, 4, "平韵", lexicon,
                                          allow_aojiu=True, strict_polyphonic=False)
    controller = GenerationController(GenerationStateMachine(profile.layout), profile.create_session())
    controller.advance("圆腹短翘风石横")
    context = controller.candidate_context()
    assert not context.line0_rhymes
    assert context.locked_rhyme_parts is None


def test_fixed_first_line_with_only_oblique_reading_does_not_lock_rhyme(lexicon):
    profile = RelationalConstraintProfile(7, 4, "平韵", lexicon,
                                          allow_aojiu=True, strict_polyphonic=False)
    session = profile.create_session()
    session.prime_fixed_lines({0: "圆腹短翘风石横"}, strict_polyphonic=False)
    assert not session._line0_rhymes
    assert session._locked_rhyme_parts is None


@pytest.mark.parametrize("num_lines", [4, 8, 10])
def test_first_line_output_recheck_applies_to_tang_and_pailv(lexicon, num_lines):
    profile = RelationalConstraintProfile(7, num_lines, "平韵", lexicon,
                                          allow_aojiu=True, strict_polyphonic=False)
    runtime = SimpleNamespace(profile=profile)
    with pytest.raises(ContextConstraintError, match="首句"):
        GenerationEngine._validate_generated_first_line(runtime, "圆腹短翘戏石横")
    GenerationEngine._validate_generated_first_line(runtime, "圆腹春翘戏石横")
    GenerationEngine._validate_generated_first_line(runtime, "圆腹短翘风石横")

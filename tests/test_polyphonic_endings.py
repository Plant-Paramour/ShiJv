from types import SimpleNamespace

import pytest

from shiju.constraints import ContextConstraintError
from shiju.generation.engine import GenerationEngine
from shiju.policies import TangVerifierPolicy


@pytest.mark.parametrize("strict", [False, True])
@pytest.mark.parametrize("mode", ["full", "critical"])
def test_ending_option_is_independent_of_tone_mode(strict, mode):
    from conftest import FakeLexicon
    lexicon = FakeLexicon()
    lexicon.tones["重"] = ["仄"]
    lexicon.parts["重"] = ["一", "二"]
    # An oblique first-line ending does not trigger the separate rhyme rules.
    info = SimpleNamespace(rhyme_type="平韵", is_rhyming=False,
                           current_line=0, locked_rhyme_parts=None)
    forbidden = TangVerifierPolicy(lexicon, mode, strict_polyphonic=strict)
    allowed = TangVerifierPolicy(lexicon, mode, strict_polyphonic=strict,
                                 allow_polyphonic_ending=True)
    assert not forbidden._check_rhyme("重", info)
    assert allowed._check_rhyme("重", info)
    assert not forbidden.without_repetition_penalty()._check_rhyme("重", info)
    info.current_line = 2
    assert forbidden._check_rhyme("重", info)


def test_output_check_covers_generated_lines_and_preserves_fixed_lines():
    from conftest import FakeLexicon
    lexicon = FakeLexicon()
    lexicon.tones["重"] = ["平", "仄"]
    lexicon.parts["重"] = ["一"]
    runtime = SimpleNamespace(profile=SimpleNamespace(lexicon=lexicon))
    content = "山雨山雨重，\n山雨山雨春。"
    with pytest.raises(ContextConstraintError, match="第 1 句"):
        GenerationEngine._validate_generated_endings(runtime, content, {})
    GenerationEngine._validate_generated_endings(runtime, content, {}, {1})
    GenerationEngine._validate_generated_endings(runtime, content, {"allow_polyphonic_ending": True})
    GenerationEngine._validate_generated_endings(runtime, "山雨山雨春，\n山雨山雨重。", {})


def test_same_tone_multiple_rhyme_parts_are_ambiguous():
    from conftest import FakeLexicon
    from shiju.data import is_polyphonic_end
    lexicon = FakeLexicon()
    lexicon.parts["山"] = ["一", "二"]
    assert is_polyphonic_end(lexicon, "山")

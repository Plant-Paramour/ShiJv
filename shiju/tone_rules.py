from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Protocol


class ToneRule(Protocol):
    def validate(self, tones: tuple[str, ...]) -> bool: ...


@dataclass(frozen=True)
class RejectThreeSameEndingRule:
    """拒绝任何存在三连平或三连仄解释的候选。"""

    def validate(self, tones: tuple[str, ...]) -> bool:
        return len(tones) < 3 or len(set(tones[-3:])) != 1


@dataclass(frozen=True)
class HanpaiIsolatedLevelRule:
    """汉俳使用的孤平判断，可选用邻位平声作为拗救。"""

    allow_aojiu: bool = False

    def validate(self, tones: tuple[str, ...]) -> bool:
        for index in range(1, len(tones) - 1):
            if tones[index - 1 : index + 2] != ("仄", "平", "仄"):
                continue
            if self.allow_aojiu:
                left_rescue = index >= 2 and tones[index - 2] == "平"
                right_rescue = index + 2 < len(tones) and tones[index + 2] == "平"
                if left_rescue or right_rescue:
                    continue
            return False
        return True


@dataclass(frozen=True)
class ToneRuleSet:
    """按严格或放行模式校验多音字的全部平仄组合。"""

    reject_any: tuple[ToneRule, ...] = ()
    accept_any: tuple[ToneRule, ...] = ()
    strict_polyphonic: bool = True

    def validate(self, combinations: Iterable[tuple[str, ...]]) -> bool:
        combinations = tuple(combinations)
        if not combinations:
            return False

        def is_valid(tones: tuple[str, ...]) -> bool:
            return all(
                rule.validate(tones)
                for rule in self.reject_any + self.accept_any
            )

        if self.strict_polyphonic:
            return all(is_valid(tones) for tones in combinations)
        return any(is_valid(tones) for tones in combinations)

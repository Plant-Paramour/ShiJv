from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Protocol, Tuple

from .domain import GenerationLayout, LineLayout


class RhymeLookup(Protocol):
    def get_pingze(self, char: str) -> List[str]: ...

    def get_rhyme_part(self, char: str) -> List[str]: ...

    def get_rhyme_part_by_tone(self, char: str, tone: str) -> List[str]: ...

    def iter_rhyme_entries(self) -> Iterable[Tuple[str, str, str]]: ...


def is_polyphonic_end(lexicon: RhymeLookup, char: str) -> bool:
    """判断韵书中可区分的多个平仄或韵部。"""
    tones = set(lexicon.get_pingze(char))
    return len(tones) > 1 or len(set(lexicon.get_rhyme_part(char))) > 1


class RhymeLexicon:
    """只负责韵书加载与查询，不感知格律模板。"""

    def __init__(self, rhyme_dict_path: str | Path):
        self.path = Path(rhyme_dict_path)
        self._char_to_rhyme_tone: Dict[str, List[Tuple[str, str]]] = {}
        self._rhyme_tone_to_chars: Dict[str, Dict[str, List[str]]] = {}
        self._load()
        self._pingze_options = {
            char: tuple(sorted({tone for _, tone in entries}))
            for char, entries in self._char_to_rhyme_tone.items()
        }

    @staticmethod
    def _tone_to_pingze(tone: str) -> str:
        return "平" if "平" in tone else "仄"

    def _load(self) -> None:
        with self.path.open("r", encoding="utf-8") as stream:
            data = json.load(stream)
        if not isinstance(data, dict):
            raise ValueError(f"韵书根节点必须是对象: {self.path}")

        for rhyme_part, tones in data.items():
            if not isinstance(tones, dict):
                raise ValueError(f"韵部 {rhyme_part} 的内容必须是对象")
            self._rhyme_tone_to_chars[rhyme_part] = {}
            for tone_name, chars in tones.items():
                if not isinstance(chars, list):
                    raise ValueError(f"韵部 {rhyme_part}/{tone_name} 必须是字列表")
                pingze = self._tone_to_pingze(tone_name)
                self._rhyme_tone_to_chars[rhyme_part].setdefault(pingze, []).extend(chars)
                for char in chars:
                    self._char_to_rhyme_tone.setdefault(char, []).append((rhyme_part, pingze))

    def get_pingze(self, char: str) -> List[str]:
        return list(self.pingze_options(char))

    def pingze_options(self, char: str) -> tuple[str, ...]:
        """Immutable instance-local lookup; public list results remain independent."""
        return self._pingze_options.get(char, ())

    def get_rhyme_part(self, char: str) -> List[str]:
        return sorted({part for part, _ in self._char_to_rhyme_tone.get(char, ())})

    def get_rhyme_part_by_tone(self, char: str, tone: str) -> List[str]:
        return sorted(
            {
                part
                for part, item_tone in self._char_to_rhyme_tone.get(char, ())
                if item_tone == tone
            }
        )

    def iter_rhyme_entries(self) -> Iterable[Tuple[str, str, str]]:
        for char, entries in self._char_to_rhyme_tone.items():
            for part, tone in entries:
                yield char, part, tone

    def rhyme_parts(self, tone: str | None = None) -> List[str]:
        return [
            part for part, tones in self._rhyme_tone_to_chars.items()
            if tone is None or tones.get(tone)
        ]


@dataclass(frozen=True)
class MeterLine:
    raw_pattern: str
    tone_options: Tuple[frozenset[str], ...]
    layout: LineLayout
    rhyme_group: int | None


@dataclass(frozen=True)
class MeterStanza:
    index: int
    lines: Tuple[MeterLine, ...]


@dataclass(frozen=True)
class MeterTemplate:
    name: str
    variant_name: str
    rhyme_type: str
    stanzas: Tuple[MeterStanza, ...]
    repetition_groups: Tuple[Mapping[str, object], ...] = ()

    @property
    def lines(self) -> Tuple[MeterLine, ...]:
        return tuple(line for stanza in self.stanzas for line in stanza.lines)

    @property
    def layout(self) -> GenerationLayout:
        return GenerationLayout(tuple(line.layout for line in self.lines))


class MeterTemplateRepository:
    """加载并校验宋词格律，调用方只接收已解析的默认变体。"""

    _RHYME_KEY = re.compile(r"^rhyme_(\d+)_positions$")

    def __init__(self, source: str | Path):
        self.source = Path(source)

    def get(self, name: str, variant_name: str | None = None) -> MeterTemplate:
        root = self._load_root(name)
        variants = root.get("variants")
        if not isinstance(variants, list) or not variants:
            raise ValueError(f"词牌 {name} 没有变体数据")

        selected_name = variant_name or root.get("default_variant") or variants[0].get("name")
        selected = next((item for item in variants if item.get("name") == selected_name), None)
        if selected is None:
            raise ValueError(f"词牌 {name} 不存在变体 {selected_name}")
        return self._parse_variant(root.get("name") or name, selected)

    def _load_root(self, name: str) -> Mapping[str, object]:
        if self.source.is_dir():
            path = self.source / f"{name}.json"
            if not path.exists():
                raise ValueError(f"词牌 {name} 的文件不存在: {path}")
            with path.open("r", encoding="utf-8") as stream:
                root = json.load(stream)
        else:
            with self.source.open("r", encoding="utf-8") as stream:
                data = json.load(stream)
            if name not in data:
                raise ValueError(f"词牌 {name} 未在 {self.source} 中找到")
            entry = dict(data[name])
            variant_name = entry.pop("variant", name)
            root = {
                "name": name,
                "default_variant": variant_name,
                "variants": [{"name": variant_name, **entry}],
            }
        if not isinstance(root, dict):
            raise ValueError(f"词牌文件根节点必须是对象: {name}")
        return root

    def _parse_variant(self, name: str, variant: Mapping[str, object]) -> MeterTemplate:
        stanza_count = variant.get("number_of_stanzas")
        if not isinstance(stanza_count, int) or stanza_count <= 0:
            raise ValueError(f"词牌 {name} 的 number_of_stanzas 无效")

        stanzas = []
        rhyme_endpoints: set[tuple[int, int, int]] = set()
        line_lengths: dict[tuple[int, int], int] = {}
        for stanza_index in range(stanza_count):
            raw_stanza = variant.get(f"stanza{stanza_index + 1}")
            if not isinstance(raw_stanza, dict):
                raise ValueError(f"词牌 {name} 缺少 stanza{stanza_index + 1}")
            patterns = raw_stanza.get("lines")
            if not isinstance(patterns, list) or not patterns:
                raise ValueError(f"词牌 {name} 第 {stanza_index + 1} 阕缺少 lines")
            if raw_stanza.get("num_lines") is not None and raw_stanza.get("num_lines") != len(patterns):
                raise ValueError(f"词牌 {name} 第 {stanza_index + 1} 阕 num_lines 与 lines 不一致")
            expected_lengths = raw_stanza.get("chars_per_line")
            if expected_lengths is not None and (
                not isinstance(expected_lengths, list) or len(expected_lengths) != len(patterns)
            ):
                raise ValueError(
                    f"词牌 {name} 第 {stanza_index + 1} 阕 chars_per_line 与 lines 不一致"
                )

            rhyme_groups: Dict[int, int] = {}
            for key, positions in raw_stanza.items():
                match = self._RHYME_KEY.match(key)
                if not match:
                    continue
                if not isinstance(positions, list):
                    raise ValueError(f"{name}/{key} 必须是位置列表")
                group = int(match.group(1))
                for position in positions:
                    if not isinstance(position, int) or position < 1 or position > len(patterns):
                        raise ValueError(f"{name}/{key} 包含越界位置 {position}")
                    rhyme_groups[position - 1] = group

            parsed_lines = []
            for line_in_stanza, raw_pattern in enumerate(patterns):
                if not isinstance(raw_pattern, str):
                    raise ValueError(f"词牌 {name} 的格律行必须是字符串")
                tones, breaks, caesuras = self._parse_pattern(raw_pattern)
                if isinstance(expected_lengths, list) and expected_lengths[line_in_stanza] != len(tones):
                    raise ValueError(
                        f"词牌 {name} 第 {stanza_index + 1} 阕第 {line_in_stanza + 1} 句字数不一致"
                    )
                layout = LineLayout(
                    length=len(tones),
                    break_positions=frozenset(breaks | caesuras),
                    caesura_positions=frozenset(caesuras),
                    stanza_index=stanza_index,
                    line_in_stanza=line_in_stanza,
                    stanza_end=line_in_stanza == len(patterns) - 1,
                )
                parsed_lines.append(
                    MeterLine(
                        raw_pattern=raw_pattern,
                        tone_options=tuple(tones),
                        layout=layout,
                        rhyme_group=rhyme_groups.get(line_in_stanza),
                    )
                )
                line_lengths[(stanza_index + 1, line_in_stanza + 1)] = len(tones)
                if line_in_stanza in rhyme_groups:
                    rhyme_endpoints.add((stanza_index + 1, line_in_stanza + 1, len(tones)))
            expected_total = raw_stanza.get("num_chars")
            actual_total = sum(line.layout.length for line in parsed_lines)
            if expected_total is not None and expected_total != actual_total:
                raise ValueError(
                    f"词牌 {name} 第 {stanza_index + 1} 阕 num_chars 与格律行不一致"
                )
            stanzas.append(MeterStanza(index=stanza_index, lines=tuple(parsed_lines)))

        repetition_groups = []
        for group in variant.get("repetition_groups") or ():
            if not isinstance(group, dict):
                continue
            positions = group.get("positions") or []
            includes_rhyme = False
            covers_complete_sentence = True
            for position in positions:
                if not isinstance(position, dict):
                    continue
                start = position.get("start") or []
                end = position.get("end") or []
                if len(start) < 2 or len(end) < 2:
                    continue
                stanza_no = int(position.get("stanza", 0))
                for line_no in range(int(start[0]), int(end[0]) + 1):
                    line_index = line_no - 1
                    line_length = next((length for s, l, length in rhyme_endpoints if s == stanza_no and l == line_no), None)
                    if line_length is None:
                        continue
                    first_char = int(start[1]) if line_no == start[0] else 1
                    last_char = int(end[1]) if line_no == end[0] else line_length
                    if first_char <= line_length <= last_char and (stanza_no, line_no, line_length) in rhyme_endpoints:
                        includes_rhyme = True
                if int(start[1]) != 1 or int(end[1]) != line_lengths.get((stanza_no, int(end[0])), -1):
                    covers_complete_sentence = False
            enriched = dict(group)
            enriched["kind"] = "叠韵" if includes_rhyme else ("叠句" if covers_complete_sentence else "叠字")
            repetition_groups.append(enriched)

        return MeterTemplate(
            name=name,
            variant_name=str(variant.get("name") or name),
            rhyme_type=str(variant.get("rhyme_type") or "平韵").strip(),
            stanzas=tuple(stanzas),
            repetition_groups=tuple(repetition_groups),
        )

    @staticmethod
    def _parse_pattern(pattern: str) -> tuple[list[frozenset[str]], set[int], set[int]]:
        tones: list[frozenset[str]] = []
        breaks: set[int] = set()
        caesuras: set[int] = set()
        for char in pattern:
            if char == "平":
                tones.append(frozenset({"平"}))
            elif char == "仄":
                tones.append(frozenset({"仄"}))
            elif char == "中":
                tones.append(frozenset({"平", "仄"}))
            elif char == "/":
                breaks.add(len(tones))
            elif char == "、":
                caesuras.add(len(tones))
            elif "\u4e00" <= char <= "\u9fff":
                # 部分词谱以示例字占位；占位字不限定平仄，但仍计入字数。
                tones.append(frozenset({"平", "仄"}))
            else:
                raise ValueError(f"未知格律符号: {char!r}")
        if not tones:
            raise ValueError("格律行不能为空")
        # 新版词牌数据暂未写入句读斜杠。保留状态机所需的节奏边界，
        # 待数据补上显式 / 或 、 后，上面的解析结果会优先覆盖这些默认值。
        if not breaks:
            defaults = {
                4: (2,),
                5: (2,),
                6: (3,),
                7: (2, 4),
                8: (3, 5),
            }
            breaks.update(defaults.get(len(tones), ()))
        return tones, breaks, caesuras

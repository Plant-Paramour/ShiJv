from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Mapping


_SEPARATOR = re.compile(r"[，,。.？！；;!?\r\n]+")
_TEXT_CHAR = re.compile(r"[\u4e00-\u9fa5A-Za-z]")
_SIGNIFICANT = re.compile(r"[\u4e00-\u9fa5A-Za-z、]")


class PoemParseError(ValueError):
    pass


@dataclass(frozen=True)
class ParsedLine:
    number: int
    text: str
    source_text: str
    start: int
    end: int
    separator: str


@dataclass(frozen=True)
class ParsedPoem:
    source: str
    lines: tuple[ParsedLine, ...]

    def replace_lines(self, replacements: Mapping[int, str]) -> str:
        unknown = set(replacements).difference(line.number for line in self.lines)
        if unknown:
            raise PoemParseError(f"替换句号越界: {sorted(unknown)}")
        result = self.source
        by_number = {line.number: line for line in self.lines}
        for number in sorted(replacements, reverse=True):
            line = by_number[number]
            replacement = replacements[number].strip()
            if not replacement:
                raise PoemParseError(f"第 {number} 句替换内容不能为空")
            result = result[: line.start] + replacement + result[line.end :]
        return result


def parse_poem(text: str) -> ParsedPoem:
    if not text or not text.strip():
        raise PoemParseError("诗文不能为空")

    lines: list[ParsedLine] = []
    cursor = 0
    matches = list(_SEPARATOR.finditer(text))
    for match in matches + [None]:
        boundary = len(text) if match is None else match.start()
        segment = text[cursor:boundary]
        significant = list(_SIGNIFICANT.finditer(segment))
        if significant:
            local_start = significant[0].start()
            local_end = significant[-1].end()
            source_text = segment[local_start:local_end]
            clean = "".join(_TEXT_CHAR.findall(source_text))
            if clean:
                separator = "" if match is None else match.group(0)
                lines.append(
                    ParsedLine(
                        number=len(lines) + 1,
                        text=clean,
                        source_text=source_text,
                        start=cursor + local_start,
                        end=cursor + local_end,
                        separator=separator,
                    )
                )
        if match is not None:
            cursor = match.end()

    if not lines:
        raise PoemParseError("未从输入中解析到诗句")
    return ParsedPoem(source=text, lines=tuple(lines))


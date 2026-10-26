import test from "node:test";
import assert from "node:assert/strict";

import { layoutSongciScroll } from "../scroll-layout.js";

const variant = {
  number_of_stanzas: 2,
  stanza1: {
    rhyme_1_positions: [2],
    lines: ["中平/中仄", "中平平仄"],
  },
  stanza2: {
    rhyme_1_positions: [1],
    lines: ["平平平", "中仄"],
  },
};

test("Song Ci scroll layout remains horizontal and keeps line order", () => {
  const result = layoutSongciScroll({
    variant,
    lines: ["春山明月", "把酒问天", "江南春", "何处"],
    width: 1000,
    height: 600,
    options: { charWidth: 20, charHeight: 20, lineGap: 10, stanzaGap: 30 },
  });

  assert.equal(result.direction, "horizontal");
  assert.deepEqual(result.lines.map((line) => line.text), [
    "春山明月", "把酒问天", "江南春", "何处",
  ]);
  assert.deepEqual(result.lines[0].glyphs.map((glyph) => glyph.x), [
    140, 160, 198, 218,
  ]);
  assert.equal(result.lines[1].glyphs.at(-1).char, "天");
  assert.equal(result.lines[1].glyphs.at(-1).isRhyme, true);
  assert.equal(result.lines[2].y - result.lines[1].y, 60);
});

test("句读只增加横排字符间距，且不拆句", () => {
  const result = layoutSongciScroll({
    variant: { number_of_stanzas: 1, stanza1: {
      rhyme_1_positions: [], lines: ["中平/平仄"],
    } },
    lines: ["春山风雨"],
    options: { charWidth: 10, charHeight: 20, lineGap: 0, breakGap: 5 },
  });

  assert.deepEqual(result.lines[0].glyphs.map((glyph) => glyph.x), [168, 178, 193, 203]);
  assert.equal(result.lines.length, 1);
  assert.deepEqual(result.issues, []);
});

test("实际句长不符合词谱时只报告问题，不破坏坐标输出", () => {
  const result = layoutSongciScroll({
    variant,
    lines: ["春山"],
  });

  assert.equal(result.lines.length, 1);
  assert.deepEqual(result.issues, [{
    lineIndex: 0,
    expectedLength: 4,
    actualLength: 2,
  }]);
});

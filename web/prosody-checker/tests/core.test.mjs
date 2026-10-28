import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import {
  buildLexicon,
  evaluateBestTangForm,
  evaluateHaiku,
  evaluatePailv,
  evaluateSongci,
  evaluateTang,
  flattenMeter,
  formatMeterTemplate,
  parsePoem,
  scoreTangTones,
} from "../core.js";

const lexicon = buildLexicon({
  一东: {
    平声: ["山", "春", "花", "风", "重"],
  },
  二冬: {
    仄声: ["雨", "夜", "月", "重"],
  },
});

const pinshuiLexicon = buildLexicon(JSON.parse(readFileSync(
  new URL("../../../Rhyme/Pinshui.json", import.meta.url),
  "utf8",
)));

test("首句多音韵脚按韵书显示两种读音的条件", () => {
  const text = "圆腹短翘戏石横，\n顽痴自乐拾微轻。\n扑腾滑跌声含涩，\n憨拙方知趣未平。";
  const result = evaluateTang(text, { charCount: 7, form: "jueju", polyphonicMode: "automatic", allowAoJiu: true }, pinshuiLexicon);
  const decision = result.polyphonicDecisions.find((item) => item.lineIndex === 0 && item.char === "横");
  assert.ok(decision);
  assert.equal(decision.message, "第 1 句第 7 字“横”：若为仄声，第 5 字应平；若为平声，第 3 字应平。");
  const levelBranch = result.firstLineAlternatives.find((branch) => branch.tone === "平").result;
  const obliqueBranch = result.firstLineAlternatives.find((branch) => branch.tone === "仄").result;
  assert.equal(levelBranch.stats.globalBase, "仄");
  assert.equal(obliqueBranch.stats.globalBase, "仄");
  assert.deepEqual(levelBranch.violations.map(({ lineIndex, charIndex, char, rule }) => [lineIndex, charIndex, char, rule]), [[0, 3, "翘", "孤平"]]);
  assert.deepEqual(obliqueBranch.violations.map(({ lineIndex, charIndex, char }) => [lineIndex, charIndex, char]), [[0, 4, "戏"]]);
  assert.deepEqual(levelBranch.rhymeViolations, []);
  assert.deepEqual(obliqueBranch.rhymeViolations, []);
  assert.equal(levelBranch.tonalScore, 96.43);
  assert.equal(obliqueBranch.tonalScore, 96.43);
  assert.equal(result.tonalScore, 96.43);
  assert.equal(result.rhymeScore, 100);
  assert.equal(result.violations[0], result.polyphonicDecisions[0]);
  assert.equal(result.violations[0].message, decision.message);
  assert.equal(result.violations.length, 1);
  assert.deepEqual(result.lines[0].characters.filter((item) => item.error).map((item) => item.char), ["横"]);
  assert.deepEqual(result.lines[0].characters.filter((item) => item.conditionalError).map((item) => [item.char, item.conditionalError]),
    [["短", "affected"], ["戏", "affected"], ["横", "polyphonic"]]);
  const suffix = "，顽痴自乐拾微轻。扑腾滑跌声含涩，憨拙方知趣未平。";
  const options = { charCount: 7, form: "jueju", polyphonicMode: "automatic", allowAoJiu: true };
  const level = evaluateTang("圆腹春翘戏石横" + suffix, options, pinshuiLexicon);
  const oblique = evaluateTang("圆腹短翘风石横" + suffix, options, pinshuiLexicon);
  assert.equal(level.tonalScore, 100);
  assert.ok(level.lines[0].characters.every((item) => !item.conditionalError));
  assert.equal(level.stats.firstLineEndTone, "平");
  assert.equal(oblique.tonalScore, 100);
  assert.ok(oblique.lines[0].characters.every((item) => !item.conditionalError));
  assert.ok(oblique.firstLineAlternatives.every((branch) => branch.result.violations.length === 0));
  assert.ok(oblique.firstLineAlternatives.find((branch) => branch.tone === '平').result.aoJiu.details.some((item) => item.rule === '孤平自救'));
  assert.deepEqual(oblique.firstLineAlternatives.find((branch) => branch.tone === '仄').result.rhymeGroups[0].positions, [1, 3]);
  const xinyun = buildLexicon(JSON.parse(readFileSync(new URL("../../../Rhyme/Xinyun.json", import.meta.url), "utf8")));
  const xinyunResult = evaluateTang(text, options, xinyun);
  assert.equal(xinyunResult.polyphonicDecisions[0].message,
    "第 1 句第 7 字“横”：若为仄声，第 4 字应平；若为平声，第 4 字应平。");
});

test("多音提示的修改条件来自重新校验，适用于五七言、平仄起和不同末字", () => {
  const book = buildLexicon({ 一: { 平声: ['山', '春', '行', '重'], 仄声: ['雨', '夜', '行', '重'] } });
  let checked = 0;
  let multipleFailures = 0;
  for (const length of [5, 7]) {
    for (const base of ['平', '仄']) {
      for (const ending of ['行', '重']) {
        for (let mask = 0; mask < (1 << (length - 2)); mask += 1) {
          const chars = Array.from({ length: length - 1 }, (_, index) => (mask >> index) & 1 ? '山' : '雨');
          chars[1] = base === '平' ? '山' : '雨';
          const lines = [chars.join('') + ending, '山'.repeat(length)];
          const result = evaluateTang(lines.join('，'), { charCount: length, form: 'jueju', polyphonicMode: 'automatic', allowAoJiu: true }, book);
          const firstFaults = result.firstLineAlternatives.map(({ result: branch }) =>
            [...branch.violations, ...branch.rhymeViolations].filter((item) => item.lineIndex === 0));
          const allFailed = firstFaults.every((faults) => faults.length > 0);
          assert.equal(result.conditionalConflicts?.length ?? 0, Number(allFailed));
          const visibleFirstFaults = [...result.violations, ...result.rhymeViolations].filter((item) => item.lineIndex === 0);
          if (allFailed) {
            if (firstFaults.some((faults) => faults.length > 1)) multipleFailures += 1;
            assert.equal(visibleFirstFaults.length, 1);
            assert.equal(visibleFirstFaults[0], result.polyphonicDecisions[0]);
            assert.equal(visibleFirstFaults[0].char, ending);
            assert.equal(visibleFirstFaults[0].charIndex, length - 1);
            assert.deepEqual(result.lines[0].characters.filter((item) => item.error).map((item) => item.char), [ending]);
          } else {
            assert.deepEqual(visibleFirstFaults, []);
            assert.ok(result.lines[0].characters.every((item) => !item.error));
          }
          for (const branch of result.firstLineAlternatives) {
            const resolved = evaluateTang(lines[0].slice(0, -1) + (branch.tone === '平' ? '春' : '夜') + '，' + lines[1],
              { charCount: length, form: 'jueju', polyphonicMode: 'automatic', allowAoJiu: true }, book);
            assert.deepEqual(branch.result.violations.map((item) => [item.lineIndex, item.charIndex, item.required]),
              resolved.violations.map((item) => [item.lineIndex, item.charIndex, item.required]));
            for (const plan of branch.result.firstLineToneRepairs) {
              const changed = [...lines[0]];
              for (const repair of plan) {
                assert.ok(!book.lookup(changed[repair.charIndex]).tones.has(repair.tone));
                changed[repair.charIndex] = repair.tone === '平' ? '山' : '雨';
              }
              const rescored = scoreTangTones([changed.join(''), ...lines.slice(1)], length,
                branch.result.stats.globalBase, branch.result.stats.rhymeTone, book,
                { polyphonicMode: 'automatic', allowAoJiu: true, firstLineEndTone: branch.tone });
              assert.ok(rescored.violations.every((item) => item.lineIndex !== 0));
              checked += 1;
            }
          }
        }
      }
    }
  }
  assert.ok(checked > 50);
  assert.ok(multipleFailures > 0);
});

test("同等字数的修复建议优先恢复常式，不并列推荐新增拗救", () => {
  const book = buildLexicon({ 一: { 平声: ['山', '春', '行', '重'], 仄声: ['雨', '夜', '行', '重'] } });
  for (const [first, position] of [['雨山雨雨重', 0], ['山雨雨山雨雨行', 2]]) {
    for (const allowAoJiu of [false, true]) {
      const options = { charCount: first.length, form: 'jueju', polyphonicMode: 'automatic', allowAoJiu };
      const suffix = '，' + '山'.repeat(first.length);
      const result = evaluateTang(first + suffix, options, book);
      const level = result.firstLineAlternatives.find((branch) => branch.tone === '平').result;
      assert.deepEqual(level.firstLineToneRepairs, [[{ charIndex: position, tone: '平' }]]);
      // 另一位置用平仍可形成合法自救，但不应混入恢复常式的直接建议。
      const selfRescued = [...first];
      selfRescued[position + 2] = '山';
      const accepted = evaluateTang(selfRescued.join('') + suffix, { ...options, allowAoJiu: true }, book);
      const acceptedLevel = accepted.firstLineAlternatives.find((branch) => branch.tone === '平').result;
      assert.ok(!acceptedLevel.violations.some((item) => item.lineIndex === 0));
      assert.ok(acceptedLevel.aoJiu.details.some((item) => item.rule === '孤平自救'));
      assert.deepEqual(acceptedLevel.firstLineToneRepairs, []);
    }
  }
});

test("parsePoem accepts punctuation, newlines, and generated output markers", () => {
  assert.deepEqual(
    parsePoem("[title]测试\n[content]春山，夜雨。\n花月"),
    ["春山", "夜雨", "花月"],
  );
});

test("buildLexicon marks characters with multiple tone/rhyme readings", () => {
  assert.deepEqual([...lexicon.lookup("重").tones].sort(), ["仄", "平"]);
  assert.equal(lexicon.lookup("重").polyphonic, true);
  assert.equal(lexicon.lookup("山").polyphonic, false);
});

test("Songci caesuras do not split text or local Qiliangfan meter lines", () => {
  const data = JSON.parse(readFileSync(
    new URL("../../../Songci_Meter/凄凉犯.json", import.meta.url), "utf8",
  ));
  const variant = data.variants.find((item) => item.name === "姜夔");
  const meter = flattenMeter(variant);
  assert.equal(meter.clauses.length, 18);
  assert.equal(meter.clauses[1].pattern, "平平仄平平仄仄平仄");
  assert.equal(meter.clauses[1].rawPattern, variant.stanza1.lines[1]);
  assert.deepEqual(meter.stanzaEnds, [8, 17]);
  assert.deepEqual(meter.rhymeGroups[0].positions, [0, 1, 4, 5, 6, 8, 11, 13, 15, 17]);
  assert.deepEqual(parsePoem("绿杨巷陌秋风起，边城一片离索。马嘶渐远，人归甚处，戍楼吹角。情怀正恶，更衰草寒烟淡薄。似当时、将军部曲，迤逦度沙漠。"), [
    "绿杨巷陌秋风起", "边城一片离索", "马嘶渐远", "人归甚处", "戍楼吹角",
    "情怀正恶", "更衰草寒烟淡薄", "似当时将军部曲", "迤逦度沙漠",
  ]);
  const text = Object.keys(variant).filter((key) => /^stanza\d+$/.test(key))
    .map((key) => variant[key].lines.map((line) => line.replaceAll("/", "")).join("，"))
    .join("\n\n");
  const result = evaluateSongci(text, variant, lexicon);
  const withoutCaesuras = evaluateSongci(text.replaceAll("、", ""), variant, lexicon);
  assert.equal(result.structureScore, 100);
  assert.equal(result.lines.length, 18);
  assert.deepEqual(result.issues, []);
  assert.deepEqual(result, withoutCaesuras);
});

test("Qiliangfan Jiang Kui user poem passes all Xinyun checks", () => {
  const data = JSON.parse(readFileSync(
    new URL("../../../Songci_Meter/凄凉犯.json", import.meta.url), "utf8",
  ));
  const xinyun = buildLexicon(JSON.parse(readFileSync(
    new URL("../../../Rhyme/Xinyun.json", import.meta.url), "utf8",
  )));
  const text = "柳丝蘸晚。烟波外、轻舟一叶斜岸。曾共画楼，倚栏听雨？当时笑眼。风前鬓畔。却无奈春深梦断。忆那年花下盟言？竟似空传雁。  \n暮色沉云散，寒更滴窗？旧约如弹。谁将素手？重拾罗、已成霜满。心事难酬？泪湿锦笺犹浅点。悔教人错认天涯？误了半。";
  const result = evaluateSongci(text, data.variants.find((v) => v.name === "姜夔"),
    xinyun, { polyphonicMode: "automatic" });
  assert.equal(result.lines.length, 18);
  assert.equal(result.structureScore, 100);
  assert.equal(result.tonalScore, 100);
  assert.equal(result.rhymeScore, 100);
  assert.deepEqual(result.issues, []);
  assert.equal(result.errors.size, 0);
});

test("polyphonic characters pass when at least one reading is valid", () => {
  const variant = {
    name: "测试体",
    stanza1: { lines: ["平"] },
  };
  const result = evaluateSongci("重", variant, lexicon);
  assert.equal(result.tonalScore, 100);
  assert.equal(result.lines[0].characters[0].polyphonic, true);
  assert.equal(result.lines[0].characters[0].error, false);
});

test("strict polyphonic mode rejects a character with an illegal reading", () => {
  const variant = {
    name: "测试体",
    stanza1: { lines: ["平"] },
  };
  const result = evaluateSongci("重", variant, lexicon, { strictPolyphonic: true });
  assert.equal(result.tonalScore, 0);
  assert.equal(result.lines[0].characters[0].error, true);
});

test("strict polyphonic mode requires every reading to share the rhyme part", () => {
  const variant = {
    name: "测试体",
    stanza1: {
      rhyme_1_positions: [1, 2],
      lines: ["中", "中"],
    },
  };
  const permissive = evaluateSongci("山，重", variant, lexicon);
  const strict = evaluateSongci(
    "山，重",
    variant,
    lexicon,
    { strictPolyphonic: true },
  );
  assert.equal(permissive.rhymeScore, 100);
  assert.equal(strict.rhymeScore, 50);
});

test("automatic polyphonic mode resolves tone and rhyme part from context", () => {
  const variant = {
    name: "测试体",
    stanza1: {
      rhyme_1_positions: [1, 2],
      lines: ["平", "中"],
    },
  };
  const result = evaluateSongci(
    "重，山",
    variant,
    lexicon,
    { polyphonicMode: "automatic" },
  );
  const character = result.lines[0].characters[0];

  assert.equal(result.tonalScore, 100);
  assert.equal(result.rhymeScore, 100);
  assert.equal(character.tone, "平");
  assert.equal(character.selectedTone, "平");
  assert.equal(character.selectedPart, "一东");
  assert.equal(character.decisionReason, "若属一东韵部，则符合本组押韵要求");
});

test("Songci follows the paper tonal and dominant-rhyme formulas", () => {
  const variant = {
    name: "测试体",
    number_of_stanzas: 1,
    stanza1: {
      num_lines: 2,
      rhyme_1_positions: [1, 2],
      lines: ["中平", "仄中"],
    },
  };
  const result = evaluateSongci("雨夜，山春", variant, lexicon);
  assert.equal(result.structureScore, 100);
  assert.equal(result.tonalScore, 50);
  assert.equal(result.rhymeScore, 50);
  assert.equal(result.errors.size, 2);
  assert.equal(result.violations.length, 2);
  assert.equal(result.rhymeViolations.length, 1);
  assert.deepEqual(
    [result.rhymeViolations[0].lineIndex, result.rhymeViolations[0].charIndex],
    [1, 1],
  );
});

test("哨遍苏轼体 reports 凝 as an out-of-rhyme ending", () => {
  const meter = JSON.parse(readFileSync(
    new URL("../../../Songci_Meter/哨遍.json", import.meta.url), "utf8",
  ));
  const xinyun = buildLexicon(JSON.parse(readFileSync(
    new URL("../../../Rhyme/Xinyun.json", import.meta.url), "utf8",
  )));
  const variant = meter.variants.find((item) => item.source_id === 2273);
  const result = evaluateSongci(
    "柳色未开，花影半遮，轻絮拂窗畔。素手捻。鬓角一痕烟。细步趋前语呢舔。浅笑凝",
    variant,
    xinyun,
  );
  assert.equal(result.rhymeScore, 80);
  assert.equal(result.rhymeViolations.some((item) =>
    item.lineIndex === 6 && item.charIndex === 2 && item.char === "凝"), true);
});

test("Songci structure score uses correct clauses over template clauses", () => {
  const variant = {
    name: "测试体",
    stanza1: { lines: ["中平", "仄中"] },
  };
  assert.equal(evaluateSongci("山春", variant, lexicon).structureScore, 50);
  assert.equal(evaluateSongci("山春，夜", variant, lexicon).structureScore, 50);
});

test("Songci templates preserve flexible 中 positions", () => {
  const variant = {
    name: "测试体",
    number_of_stanzas: 1,
    stanza1: { lines: ["中平/中仄"] },
  };
  assert.deepEqual(formatMeterTemplate(variant)[0].lines, ["中平中仄"]);
  assert.equal(flattenMeter(variant).clauses[0].pattern, "中平中仄");
});

test("Tang evaluator uses the requested length and form", () => {
  const result = evaluateTang(
    "山春山夜山春花，雨夜雨山雨夜山。山春山夜山春花，雨夜雨山雨夜山。",
    { charCount: 7, form: "jueju" },
    lexicon,
  );
  assert.equal(result.lines.length, 4);
  assert.equal(result.stats.charCount, 7);
  assert.equal(result.structureScore, 100);
  assert.equal(result.issues.length, 0);
  assert.ok(result.tonalScore >= 0 && result.tonalScore <= 100);
  assert.ok(result.rhymeScore >= 0 && result.rhymeScore <= 100);
});

test("Tang structure score follows the paper line-count cap", () => {
  const result = evaluateTang(
    "山春山夜山春花，雨夜雨山雨夜山。",
    { charCount: 7, form: "jueju" },
    lexicon,
  );
  assert.equal(result.structureScore, 50);
});

test("Tang exposes rhyme deductions with the ending character position", () => {
  const rhymeLexicon = buildLexicon({
    一东: { 平声: ["风", "冬"] },
    三江: { 平声: ["江"] },
    仄声: { 仄声: ["月", "雨", "夜"] },
  });
  const result = evaluateTang(
    "风中风雨风中风，中雨中风中雨江。",
    { charCount: 7, form: "jueju", allowAoJiu: false },
    rhymeLexicon,
  );
  assert.ok(result.rhymeViolations.length > 0);
  for (const item of result.rhymeViolations) {
    assert.equal(item.charIndex, 6);
    assert.equal(item.char, result.lines[item.lineIndex].characters[6].char);
    assert.match(item.required, /韵部$/);
  }
});

test("smart form selection chooses jueju, lvshi, and pailv by comprehensive score", () => {
  const fiveJueju = Array(4).fill("春山夜雨花").join("\n");
  const sevenLvshi = Array(8).fill("山春山夜山春花").join("\n");
  const fivePailv = Array(10).fill("春山夜雨花").join("\n");

  const jueju = evaluateBestTangForm(fiveJueju, lexicon);
  const lvshi = evaluateBestTangForm(sevenLvshi, lexicon, {}, { type: "pailv" });
  const pailv = evaluateBestTangForm(fivePailv, lexicon, {}, {
    type: "tang",
    charCount: 5,
    form: "lvshi",
  });

  assert.deepEqual([jueju.type, jueju.charCount, jueju.form], ["tang", 5, "jueju"]);
  assert.deepEqual([lvshi.type, lvshi.charCount, lvshi.form], ["tang", 7, "lvshi"]);
  assert.deepEqual([pailv.type, pailv.result.stats.charCount], ["pailv", 5]);
});

test("Haiku checks the fixed 5-7-5 structure", () => {
  const result = evaluateHaiku("春山夜雨花\n春山夜雨花月风\n夜雨花月山", lexicon);
  assert.equal(result.issues.length, 0);
  assert.equal(result.lines.length, 3);
  assert.equal(result.structureScore, 100);
  assert.ok(Array.isArray(result.violations));
  assert.ok(Array.isArray(result.rhymeViolations));
});

test("Pailv accepts ten equal-length lines without extra options", () => {
  const text = Array(10).fill("春山夜雨花").join("\n");
  const result = evaluatePailv(text, lexicon);
  assert.equal(result.stats.charCount, 5);
  assert.equal(result.lines.length, 10);
  assert.equal(result.issues.length, 0);
  assert.equal(result.structureScore, 100);
});

test("Pailv reports the even-position error without cascading a false isolated level", () => {
  const lines = Array(15).fill("山春山夜山春花");
  lines.push("夜春雨花月山风");
  const result = evaluatePailv(lines.join("\n"), lexicon);
  const lastLine = result.lines.at(-1).characters;

  assert.equal(lastLine[1].tone, "平");
  assert.equal(lastLine[1].expected, "平");
  assert.equal(lastLine[1].error, false);
  assert.equal(lastLine[3].tone, "平");
  assert.equal(lastLine[3].expected, "仄");
  assert.equal(lastLine[3].error, true);
  assert.deepEqual(
    result.violations
      .filter((item) => item.lineIndex === 15)
      .map((item) => [item.charIndex, item.rule]),
    [[3, "二四六分明"]],
  );
});

test("Pailv never treats a rhyming-line even position as isolated-level rescue", () => {
  const lines = Array(15).fill("山春山夜山春花");
  lines.push("夜春雨花月山风");
  const result = evaluatePailv(lines.join("\n"), lexicon, { allowAoJiu: true });
  const lastLine = result.lines.at(-1).characters;

  assert.equal(lastLine[1].error, false);
  assert.equal(lastLine[3].error, true);
});

const toneLine = (pattern) => pattern.replaceAll("P", "山").replaceAll("Z", "雨");
const pailvPatterns = [
  "PPZZPPZ", "PZZPPZP", "PZZPPZZ", "PPZZZPP",
  "PPZZPPZ", "PZZPPZP", "PZZPPZZ", "PPZZZPP",
  "PPZZPPZ", "PZZPPZP", "PZZPPZZ", "PPZZZPP",
  "PPZZPPZ", "PZZPPZP", "PZZPPZZ", "PPZZZPP",
];

test("Pailv still reports an isolated fourth-position level when it is the only non-rhyme level", () => {
  const patterns = [...pailvPatterns];
  patterns[1] = "ZZZPZZP";
  const result = evaluatePailv(
    patterns.map(toneLine).join("\n"),
    lexicon,
  );

  assert.equal(
    result.violations.some((item) => item.lineIndex === 1 && item.rule === "孤平"),
    true,
  );
});

test("Pailv accepts six-ao fifth-position self rescue and non-rhyming triple oblique", () => {
  const patterns = [...pailvPatterns];
  patterns[12] = "PPZZPZZ";
  const result = evaluatePailv(
    patterns.map(toneLine).join("\n"),
    lexicon,
    { allowAoJiu: true },
  );

  assert.equal(result.violations.some((item) => item.lineIndex === 12), false);
});

test("Pailv requires the following line to rescue unresolved major ao", () => {
  const rescued = [...pailvPatterns];
  rescued[12] = "PPZZZZZ";
  rescued[13] = "PZZPPZP";
  const accepted = evaluatePailv(
    rescued.map(toneLine).join("\n"),
    lexicon,
    { allowAoJiu: true },
  );
  assert.equal(accepted.violations.some((item) => [12, 13].includes(item.lineIndex)), false);

  const unrescued = [...rescued];
  unrescued[13] = "PZZPZZP";
  const rejected = evaluatePailv(
    unrescued.map(toneLine).join("\n"),
    lexicon,
    { allowAoJiu: true },
  );
  assert.equal(
    rejected.violations.some(
      (item) => item.lineIndex === 13 && item.charIndex === 4 && item.rule === "对句相救",
    ),
    true,
  );
});

test("Pailv accepts the special fifth-sixth position exchange only on non-rhyming lines", () => {
  const patterns = [...pailvPatterns];
  patterns[14] = "PZPPZPZ";
  const result = evaluatePailv(
    patterns.map(toneLine).join("\n"),
    lexicon,
    { allowAoJiu: true },
  );

  assert.equal(result.violations.some((item) => item.lineIndex === 14), false);
});

test("月夜 marks both five-character self-rescue exchanges", () => {
  const result = evaluateTang(
    "今夜鄜州月，闺中只独看。遥怜小儿女，未解忆长安。"
      + "香雾云鬟湿，清辉玉臂寒。何时倚虚幌，双照泪痕干。",
    { charCount: 5, form: "lvshi", allowAoJiu: true },
    pinshuiLexicon,
  );

  assert.equal(result.violations.length, 0);
  assert.deepEqual(
    result.aoJiu.details
      .filter((item) => item.rule === "特拗交换")
      .map((item) => item.message),
    [
      "第 3 句“小儿”平仄互换，符合本句自救。",
      "第 7 句“倚虚”平仄互换，符合本句自救。",
    ],
  );
  assert.deepEqual(result.lines[2].characters[2].roles, ["ao"]);
  assert.deepEqual(result.lines[2].characters[3].roles, ["rescue"]);
});

test("江南春 marks 十 and 烟 as a cross-line rescue pair", () => {
  const result = evaluateTang(
    "千里莺啼绿映红，水村山郭酒旗风。南朝四百八十寺，多少楼台烟雨中。",
    { charCount: 7, form: "jueju", allowAoJiu: true },
    pinshuiLexicon,
  );
  const crossRescue = result.aoJiu.details.find((item) => item.rule === "对句相救");

  assert.equal(result.violations.length, 0);
  assert.equal(
    crossRescue.message,
    "第 3 句第 6 字“十”拗，第 4 句第 5 字“烟”救，符合对句相救。",
  );
  assert.deepEqual(result.lines[2].characters[5].roles, ["ao"]);
  assert.deepEqual(result.lines[3].characters[4].roles, ["rescue"]);
});

test("夜泊水村 marks 无 as both isolated-level and cross-line rescue", () => {
  const result = evaluateTang(
    "腰间羽箭久凋零，太息燕然未勒铭。老子犹堪绝大漠，诸君何至泣新亭。"
      + "一身报国有万死，双鬓向人无再青。记取江湖泊船处，卧闻新雁落寒汀。",
    { charCount: 7, form: "lvshi", allowAoJiu: true },
    pinshuiLexicon,
  );

  assert.equal(result.violations.length, 0);
  assert.equal(
    result.aoJiu.details.some((item) => item.rule === "一字两救"
      && item.message.includes("“万”") && item.message.includes("“无”")),
    true,
  );
  assert.equal(
    result.aoJiu.details.some((item) => item.rule === "一字两救"),
    true,
  );
  assert.deepEqual(result.lines[5].characters[2].roles, ["ao"]);
  assert.equal(
    result.aoJiu.details.some((item) => item.rule === "特拗交换"
      && item.message.includes("“泊船”")),
    true,
  );
  assert.deepEqual(result.lines[4].characters[5].roles, ["ao"]);
  assert.deepEqual(result.lines[5].characters[4].roles, ["rescue"]);
  assert.deepEqual(result.lines[6].characters[4].roles, ["ao"]);
  assert.deepEqual(result.lines[6].characters[5].roles, ["rescue"]);
});

test("孤平检查末五字并允许本句自救，不强制入韵句的某个字用平", () => {
  const book = buildLexicon({ 一: { 平声: ['山'], 仄声: ['雨'] } });
  const cases = [
    { length: 5, base: '平', broken: 'ZPZZP', restored: 'PPZZP', rescued: 'ZPPZP' },
    { length: 7, base: '仄', broken: 'PZZPZZP', restored: 'PZPPZZP', rescued: 'PZZPPZP' },
  ];
  for (const item of cases) {
    const score = (pattern) => scoreTangTones([pattern.replaceAll('P', '山').replaceAll('Z', '雨')],
      item.length, item.base, '平', book, { allowAoJiu: true, firstLineEndTone: '平' });
    assert.deepEqual(score(item.broken).violations.map((fault) => fault.rule), ['孤平']);
    assert.deepEqual(score(item.restored).violations, []);
    const rescued = score(item.rescued);
    assert.deepEqual(rescued.violations, []);
    assert.equal(rescued.aoJiuDetails[0].rule, '孤平自救');
    assert.deepEqual(rescued.aoJiuDetails[0].positions.map((position) => position.charIndex),
      [item.length - 5, item.length - 3]);
  }
  // 两种起式的入韵句，都存在第三字用仄而合法的情况。
  for (const [pattern, length, base] of [['ZZZPP', 5, '仄'], ['PPZZZPP', 7, '平']]) {
    const result = scoreTangTones([pattern.replaceAll('P', '山').replaceAll('Z', '雨')],
      length, base, '平', book, { allowAoJiu: true, firstLineEndTone: '平' });
    assert.deepEqual(result.violations, []);
  }
});

test("登高按多音字读法判断长字造成的孤平", () => {
  const text = "风急天高猿啸哀，渚清沙白鸟飞回。"
    + "无边落木萧萧下，不尽长江滚滚来。";
  const permissive = evaluateTang(
    text,
    { charCount: 7, form: "jueju", allowAoJiu: false },
    pinshuiLexicon,
  );
  const strict = evaluateTang(
    text,
    { charCount: 7, form: "jueju", allowAoJiu: false, strictPolyphonic: true },
    pinshuiLexicon,
  );

  assert.equal(permissive.violations.length, 0);
  assert.equal(permissive.lines[3].characters[2].polyphonic, true);
  assert.equal(
    strict.violations.some((item) => item.lineIndex === 3
      && item.charIndex === 3 && item.rule === "孤平"),
    true,
  );

  const automatic = evaluateTang(
    text,
    {
      charCount: 7,
      form: "jueju",
      allowAoJiu: false,
      polyphonicMode: "automatic",
    },
    pinshuiLexicon,
  );
  assert.equal(automatic.lines[3].characters[2].selectedTone, "平");
  assert.equal(automatic.lines[3].characters[2].decisionReason, "若为仄声，则本句犯孤平");
});

test("闺怨 marks 数花 as a seven-character self-rescue exchange", () => {
  const result = evaluateTang(
    "新妆宜面下朱楼，深锁春光一院愁。"
      + "行到中庭数花朵，蜻蜓飞上玉搔头。",
    { charCount: 7, form: "jueju", allowAoJiu: true },
    pinshuiLexicon,
  );

  assert.equal(result.violations.length, 0);
  assert.equal(result.lines[2].characters[2].polyphonic, true);
  assert.equal(result.lines[2].characters[2].tone.includes("平"), true);
  assert.equal(
    result.aoJiu.details.some((item) => item.rule === "特拗交换"
      && item.message === "第 3 句“数花”平仄互换，符合本句自救。"),
    true,
  );
  assert.deepEqual(result.lines[2].characters[4].roles, ["ao"]);
  assert.deepEqual(result.lines[2].characters[5].roles, ["rescue"]);

  const automatic = evaluateTang(
    "新妆宜面下朱楼，深锁春光一院愁。"
      + "行到中庭数花朵，蜻蜓飞上玉搔头。",
    {
      charCount: 7,
      form: "jueju",
      allowAoJiu: true,
      polyphonicMode: "automatic",
    },
    pinshuiLexicon,
  );
  assert.equal(automatic.lines[2].characters[2].selectedTone, "平");
  assert.equal(
    automatic.lines[2].characters[2].decisionReason,
    "若为仄声，则本句特拗自救不成立",
  );
});


test("Hanpai respects 353, optional rules, and the selected rhyme scheme", () => {
  const text = "山山山\n山山山山雨\n山山山";
  const options = { linePattern: "3-5-3", forbidIsolatedLevel: false, forbidThreeSameEnding: false };
  const automatic = evaluateHaiku(text, lexicon, options);
  assert.equal(automatic.structureScore, 100);
  assert.equal(automatic.tonalScore, 100);
  assert.equal(automatic.rhymeScore, 100);
  assert.equal(automatic.stats.rhymeScheme, "ABA");
  assert.ok(evaluateHaiku(text, lexicon, { ...options, rhymeScheme: "AAA" }).rhymeScore < 100);
  assert.equal(evaluateHaiku(text, lexicon, { ...options, rhymeScheme: "none" }).rhymeScore, 100);
  assert.equal(evaluateHaiku(text, lexicon).structureScore, 100);
});

test("Hanpai enables rescue and ending restrictions by default", () => {
  const text = "山雨山雨山\n山雨山雨山雨山\n山雨山雨山";
  assert.equal(evaluateHaiku(text, lexicon).tonalScore, 100);
  assert.ok(evaluateHaiku(text, lexicon, { allowAoJiu: false }).tonalScore < 100);
  assert.ok(evaluateHaiku("山山山\n山山山山山\n山山山", lexicon).tonalScore < 100);
});

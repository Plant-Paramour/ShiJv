const HAN_CHAR = /[\u3400-\u9fff]/u;
const CLAUSE_SEPARATOR = /[，,。.！!？?；;：:\n\r]+/u;

export const TONE_PING = "平";
export const TONE_ZE = "仄";
export const TONE_FLEX = "中";

const POLYPHONIC_AUTO = "automatic";
const POLYPHONIC_STRICT = "strict";
const POLYPHONIC_PERMISSIVE = "permissive";

function polyphonicMode(options = {}) {
  if ([POLYPHONIC_AUTO, POLYPHONIC_STRICT, POLYPHONIC_PERMISSIVE]
    .includes(options.polyphonicMode)) {
    return options.polyphonicMode;
  }
  return options.strictPolyphonic ? POLYPHONIC_STRICT : POLYPHONIC_PERMISSIVE;
}

function isStrictPolyphonic(options = {}) {
  return polyphonicMode(options) === POLYPHONIC_STRICT;
}

function readingsOf(entry) {
  return [...entry.readings].map((reading) => {
    const separator = reading.indexOf(":");
    return { tone: reading.slice(0, separator), part: reading.slice(separator + 1) };
  });
}

export function buildLexicon(rawData) {
  const characters = new Map();
  for (const [part, toneGroups] of Object.entries(rawData)) {
    for (const [toneName, values] of Object.entries(toneGroups)) {
      const tone = toneName.includes("平") ? TONE_PING : TONE_ZE;
      for (const char of values) {
        let entry = characters.get(char);
        if (!entry) {
          entry = {
            tones: new Set(),
            parts: new Set(),
            partsByTone: new Map([[TONE_PING, new Set()], [TONE_ZE, new Set()]]),
            readings: new Set(),
          };
          characters.set(char, entry);
        }
        entry.tones.add(tone);
        entry.parts.add(part);
        entry.partsByTone.get(tone).add(part);
        entry.readings.add(tone + ":" + part);
      }
    }
  }
  return {
    lookup(char) {
      const entry = characters.get(char);
      if (!entry) {
        return {
          tones: new Set(),
          parts: new Set(),
          partsByTone: new Map([[TONE_PING, new Set()], [TONE_ZE, new Set()]]),
          readings: new Set(),
          polyphonic: false,
        };
      }
      return {
        tones: entry.tones,
        parts: entry.parts,
        partsByTone: entry.partsByTone,
        readings: entry.readings,
        polyphonic: entry.readings.size > 1,
      };
    },
  };
}

export function parsePoem(text) {
  const markerIndex = text.indexOf("[content]");
  const content = markerIndex >= 0 ? text.slice(markerIndex + 9) : text;
  return content
    .trim()
    .split(CLAUSE_SEPARATOR)
    .map((part) => [...part].filter((char) => HAN_CHAR.test(char)).join(""))
    .filter(Boolean);
}

function percentage(value) {
  return Math.round(value * 10000) / 100;
}

function key(lineIndex, charIndex) {
  return lineIndex + ":" + charIndex;
}

function toneLabel(tones) {
  if (tones.size === 0) return TONE_FLEX;
  if (tones.size === 1) return [...tones][0];
  return TONE_PING + "/" + TONE_ZE;
}

function annotateLines(
  lines,
  lexicon,
  errors,
  expectedByPosition = new Map(),
  rolesByPosition = new Map(),
  readingDecisions = new Map(),
) {
  return lines.map((text, lineIndex) => ({
    text,
    characters: [...text].map((char, charIndex) => {
      const entry = lexicon.lookup(char);
      const roles = [...(rolesByPosition.get(key(lineIndex, charIndex)) ?? [])];
      const decision = readingDecisions.get(key(lineIndex, charIndex));
      return {
        char,
        tone: decision?.tone ?? toneLabel(entry.tones),
        tones: [...entry.tones],
        polyphonic: entry.polyphonic,
        error: errors.has(key(lineIndex, charIndex)),
        expected: expectedByPosition.get(key(lineIndex, charIndex)) ?? null,
        roles,
        selectedTone: decision?.tone ?? null,
        selectedPart: decision?.part ?? null,
        decisionReason: decision?.reason ?? null,
      };
    }),
  }));
}

function buildAutomaticReadingDecisions(
  lines,
  lexicon,
  expected = new Map(),
  roles = new Map(),
  rhymeGroups = [],
  context = {},
) {
  if (polyphonicMode(context.options) !== POLYPHONIC_AUTO) return new Map();
  const decisions = new Map();

  function choose(lineIndex, charIndex, predicate, reason, priority) {
    const position = key(lineIndex, charIndex);
    if ((decisions.get(position)?.priority ?? -1) > priority) return;
    const line = lines[lineIndex];
    if (!line) return;
    const char = [...line][charIndex];
    const entry = lexicon.lookup(char);
    if (!entry.polyphonic) return;
    const reading = readingsOf(entry).find(predicate);
    if (!reading) return;
    decisions.set(position, { ...reading, reason, priority });
  }

  for (let lineIndex = 0; lineIndex < lines.length; lineIndex += 1) {
    const chars = [...lines[lineIndex]];
    for (let charIndex = 0; charIndex < chars.length; charIndex += 1) {
      const position = key(lineIndex, charIndex);
      const required = expected.get(position);
      if (required && required !== TONE_FLEX) {
        choose(
          lineIndex,
          charIndex,
          (reading) => reading.tone === required,
          "若为" + required + "声，则符合此处平仄要求",
          1,
        );
      }
      const positionRoles = roles.get(position) ?? new Set();
      if (positionRoles.has("rescue")) {
        choose(
          lineIndex,
          charIndex,
          (reading) => reading.tone === TONE_PING,
          "若为仄声，则本句拗救不成立",
          2,
        );
      } else if (positionRoles.has("ao") && required) {
        const aoTone = opposite(required);
        choose(
          lineIndex,
          charIndex,
          (reading) => reading.tone === aoTone,
          "若为" + required + "声，则本句拗救不成立",
          2,
        );
      }
    }
  }

  if (context.rhymeTone && context.globalBase && context.charCount) {
    for (let lineIndex = 0; lineIndex < lines.length; lineIndex += 1) {
      const chars = [...lines[lineIndex]];
      const isEven = (lineIndex + 1) % 2 === 0;
      const endEntry = lexicon.lookup(chars.at(-1));
      const isRhyming = isEven
        || (lineIndex === 0 && endEntry.tones.has(context.rhymeTone));
      if (!isRhyming || context.rhymeTone !== TONE_PING) continue;
      const lineBase = expectedLineBase(context.globalBase, lineIndex);
      const isolatedIndex = lineBase === TONE_PING ? 1
        : context.charCount === 7 ? 3 : -1;
      if (isolatedIndex < 0) continue;
      for (let charIndex = 0; charIndex < chars.length - 1; charIndex += 1) {
        const entry = lexicon.lookup(chars[charIndex]);
        if (!entry.polyphonic || !entry.tones.has(TONE_PING)) continue;
        const pingWithoutCurrent = chars.slice(0, -1)
          .map((char, index) => ({ index, entry: lexicon.lookup(char) }))
          .filter(({ index, entry: item }) => index > 0 && index !== charIndex
            && item.tones.has(TONE_PING))
          .map(({ index }) => index);
        if (pingWithoutCurrent.length === 1 && pingWithoutCurrent[0] === isolatedIndex) {
          choose(
            lineIndex,
            charIndex,
            (reading) => reading.tone === TONE_PING,
            "若为仄声，则本句犯孤平",
            2,
          );
        }
      }
    }
  }

  for (const detail of context.aoJiuDetails ?? []) {
    if (detail.rule !== "特拗交换") continue;
    const ao = detail.positions.find((position) => position.role === "ao");
    if (!ao) continue;
    const supportIndex = ao.charIndex - 2;
    if (supportIndex < 0) continue;
    choose(
      ao.lineIndex,
      supportIndex,
      (reading) => reading.tone === TONE_PING,
      "若为仄声，则本句特拗自救不成立",
      2,
    );
  }

  for (const group of rhymeGroups) {
    if (!group.dominantPart) continue;
    for (const ending of group.endings) {
      const line = lines[ending.lineIndex];
      if (!line) continue;
      const charIndex = [...line].length - 1;
      const required = expected.get(key(ending.lineIndex, charIndex));
      choose(
        ending.lineIndex,
        charIndex,
        (reading) => reading.part === group.dominantPart
          && (!required || reading.tone === required),
        "若属" + group.dominantPart + "韵部，则符合本组押韵要求",
        3,
      );
    }
  }

  return decisions;
}

function polyphonicDecisionDetails(lines, decisions) {
  return [...decisions.entries()].map(([position, decision]) => {
    const [lineIndex, charIndex] = position.split(":").map(Number);
    const char = [...lines[lineIndex]][charIndex];
    return {
      lineIndex,
      charIndex,
      char,
      tone: decision.tone,
      part: decision.part,
      reason: decision.reason,
      message: "第 " + (lineIndex + 1) + " 句第 " + (charIndex + 1)
        + " 字“" + char + "”：" + decision.reason + "。",
    };
  }).sort((left, right) => left.lineIndex - right.lineIndex
    || left.charIndex - right.charIndex);
}

function scoreRhymeGroups(
  lines,
  groups,
  lexicon,
  toneByGroup = new Map(),
  options = {},
) {
  const strictPolyphonic = isStrictPolyphonic(options);
  const details = [];
  for (let groupIndex = 0; groupIndex < groups.length; groupIndex += 1) {
    const positions = groups[groupIndex];
    const expectedTone = toneByGroup.get(groupIndex);
    const endings = [];
    const counts = new Map();
    for (const lineIndex of positions) {
      const line = lines[lineIndex];
      if (!line) continue;
      const char = [...line].at(-1);
      const entry = lexicon.lookup(char);
      let parts;
      if (strictPolyphonic) {
        const readings = readingsOf(entry);
        const uniqueParts = new Set(readings.map((reading) => reading.part));
        const allTonesMatch = !expectedTone
          || readings.every((reading) => reading.tone === expectedTone);
        parts = readings.length && allTonesMatch && uniqueParts.size === 1
          ? uniqueParts
          : new Set();
      } else {
        parts = expectedTone
          ? entry.partsByTone.get(expectedTone) ?? new Set()
          : entry.parts;
      }
      endings.push({ lineIndex, char, parts: [...parts] });
      for (const part of parts) counts.set(part, (counts.get(part) ?? 0) + 1);
    }
    let dominantPart = null;
    let dominantCount = 0;
    for (const [part, count] of counts) {
      if (count > dominantCount) {
        dominantPart = part;
        dominantCount = count;
      }
    }
    const invalidEndings = endings.filter((ending) => dominantPart
      && !ending.parts.includes(dominantPart)).map((ending) => ({
      lineIndex: ending.lineIndex,
      charIndex: [...lines[ending.lineIndex]].length - 1,
      char: ending.char,
      rule: "押韵",
      required: dominantPart + "韵部",
    }));
    const rejectedEndings = endings.filter((ending) => dominantPart
      && (!ending.parts.includes(dominantPart)
        || (strictPolyphonic && ending.parts.length === 0))).map((ending) => ({
      lineIndex: ending.lineIndex,
      charIndex: [...lines[ending.lineIndex]].length - 1,
      char: ending.char,
      rule: "押韵",
      required: dominantPart + "韵部",
    }));
    details.push({
      positions,
      endings,
      dominantPart,
      matchCount: dominantCount,
      totalCount: endings.length,
      score: endings.length ? dominantCount / endings.length : 0,
      invalidEndings,
      rejectedEndings,
    });
  }
  const score = details.length
    ? details.reduce((sum, group) => sum + group.score, 0) / details.length
    : 0;
  return { score, details };
}

function stanzaKeys(variant) {
  return Object.keys(variant)
    .filter((name) => /^stanza\d+$/.test(name))
    .sort((a, b) => Number(a.slice(6)) - Number(b.slice(6)));
}

function normalizeMeterPattern(pattern) {
  return [...pattern].map((char) => "平仄中/、".includes(char) ? char : (/[\u4e00-\u9fff]/u.test(char) ? "中" : char)).join("");
}

export function flattenMeter(variant) {
  const clauses = [];
  const rhymeGroups = new Map();
  const stanzaEnds = [];
  for (const stanzaKey of stanzaKeys(variant)) {
    const stanza = variant[stanzaKey];
    const lineToClauses = [];
    for (const rawPattern of stanza.lines ?? []) {
      const normalizedPattern = normalizeMeterPattern(rawPattern);
      const clauseIndices = [];
      const pattern = normalizedPattern.replace(/[\/、\s]/g, "");
      if (pattern) {
        clauseIndices.push(clauses.length);
        clauses.push({ pattern, rawPattern: normalizedPattern });
      }
      lineToClauses.push(clauseIndices);
    }
    for (const [name, positions] of Object.entries(stanza)) {
      const match = /^rhyme_(\d+)_positions$/.exec(name);
      if (!match || !Array.isArray(positions)) continue;
      const groupId = Number(match[1]);
      const group = rhymeGroups.get(groupId) ?? [];
      for (const position of positions) {
        const mapped = lineToClauses[position - 1];
        if (mapped?.length) group.push(mapped.at(-1));
      }
      rhymeGroups.set(groupId, group);
    }
    if (clauses.length) stanzaEnds.push(clauses.length - 1);
  }
  return {
    clauses,
    rhymeGroups: [...rhymeGroups.entries()]
      .sort(([left], [right]) => left - right)
      .map(([id, positions]) => ({ id, positions })),
    stanzaEnds,
  };
}

export function formatMeterTemplate(variant) {
  return stanzaKeys(variant).map((stanzaKey) => ({
    name: stanzaKey,
    lines: (variant[stanzaKey].lines ?? []).map((line) => normalizeMeterPattern(line).replaceAll("/", "")),
  }));
}

export function evaluateSongci(text, variant, lexicon, options = {}) {
  const strictPolyphonic = isStrictPolyphonic(options);
  const lines = parsePoem(text);
  const meter = flattenMeter(variant);
  const errors = new Set();
  const expected = new Map();
  const issues = [];
  let matching = 0;
  let total = 0;
  const common = Math.min(lines.length, meter.clauses.length);
  let structurallyCorrect = 0;

  for (let lineIndex = 0; lineIndex < common; lineIndex += 1) {
    const line = [...lines[lineIndex]];
    const pattern = [...meter.clauses[lineIndex].pattern];
    if (line.length !== pattern.length) {
      issues.push("第 " + (lineIndex + 1) + " 句应为 " + pattern.length
        + " 字，实际为 " + line.length + " 字");
      continue;
    }
    structurallyCorrect += 1;
    for (let charIndex = 0; charIndex < line.length; charIndex += 1) {
      const required = pattern[charIndex];
      const positionKey = key(lineIndex, charIndex);
      expected.set(positionKey, required);
      total += 1;
      const tones = lexicon.lookup(line[charIndex]).tones;
      const toneMatches = strictPolyphonic
        ? tones.size > 0 && [...tones].every((tone) => tone === required)
        : tones.has(required);
      if (required === TONE_FLEX || toneMatches) matching += 1;
      else errors.add(positionKey);
    }
  }
  if (lines.length < meter.clauses.length) {
    issues.push("缺少 " + (meter.clauses.length - lines.length) + " 句");
  } else if (lines.length > meter.clauses.length) {
    issues.push("多出 " + (lines.length - meter.clauses.length) + " 句");
  }

  const rhyme = scoreRhymeGroups(
    lines,
    meter.rhymeGroups.map((group) => group.positions),
    lexicon,
    new Map(),
    options,
  );
  const violations = [];
  for (let lineIndex = 0; lineIndex < common; lineIndex += 1) {
    const line = [...lines[lineIndex]];
    const pattern = [...meter.clauses[lineIndex].pattern];
    if (line.length !== pattern.length) continue;
    for (let charIndex = 0; charIndex < line.length; charIndex += 1) {
      const required = pattern[charIndex];
      const tones = lexicon.lookup(line[charIndex]).tones;
      const toneMatches = strictPolyphonic
        ? tones.size > 0 && [...tones].every((tone) => tone === required)
        : tones.has(required);
      if (required !== TONE_FLEX && tones.size > 0 && !toneMatches) {
        violations.push({ lineIndex, charIndex, char: line[charIndex], rule: "词谱平仄", required });
      }
    }
  }
  const rhymeViolations = rhyme.details.flatMap((group) => group.invalidEndings);
  const readingDecisions = buildAutomaticReadingDecisions(
    lines,
    lexicon,
    expected,
    new Map(),
    rhyme.details,
    { options },
  );
  return {
    kind: "songci",
    structureScore: percentage(meter.clauses.length
      ? structurallyCorrect / meter.clauses.length
      : 0),
    tonalScore: percentage(total ? matching / total : 0),
    rhymeScore: percentage(rhyme.score),
    lines: annotateLines(lines, lexicon, errors, expected, new Map(), readingDecisions),
    errors,
    issues,
    rhymeGroups: rhyme.details,
    violations: violations.map((item) => ({ ...item, message: `第 ${item.lineIndex + 1} 句第 ${item.charIndex + 1} 字“${item.char}”：${item.rule}，要求${item.required}声。` })),
    rhymeViolations,
    polyphonicDecisions: polyphonicDecisionDetails(lines, readingDecisions),
    stats: { matchingTones: matching, totalTones: total },
    meter,
  };
}

function expectedLineBase(globalBase, lineIndex) {
  return lineIndex % 4 === 1 || lineIndex % 4 === 2
    ? (globalBase === TONE_PING ? TONE_ZE : TONE_PING)
    : globalBase;
}

function opposite(tone) {
  return tone === TONE_PING ? TONE_ZE : TONE_PING;
}

function forcedTone(entry, tone) {
  return entry.tones.size === 1 && entry.tones.has(tone);
}

function tonesFit(entry, allowedTones, strictPolyphonic) {
  if (entry.tones.size === 0) return false;
  return strictPolyphonic
    ? [...entry.tones].every((tone) => allowedTones.has(tone))
    : [...entry.tones].some((tone) => allowedTones.has(tone));
}

function ruleCanUseTone(entry, tone, strictPolyphonic) {
  return strictPolyphonic ? entry.tones.has(tone) : forcedTone(entry, tone);
}

function rescueHasTone(entry, tone, strictPolyphonic) {
  if (entry.tones.size === 0) return false;
  return strictPolyphonic
    ? [...entry.tones].every((item) => item === tone)
    : entry.tones.has(tone);
}

function detectRhymeTone(lines, lexicon) {
  const endings = lines
    .filter((line, index) => (index + 1) % 2 === 0 && line)
    .map((line) => lexicon.lookup([...line].at(-1)).tones);
  const ping = endings.filter((tones) => tones.has(TONE_PING)).length;
  const ze = endings.filter((tones) => tones.has(TONE_ZE)).length;
  return ping >= ze ? TONE_PING : TONE_ZE;
}

export function scoreTangTones(lines, charCount, globalBase, rhymeTone, lexicon, options = {}) {
  const strictPolyphonic = isStrictPolyphonic(options);
  const allowAoJiu = Boolean(options.allowAoJiu);
  const errors = new Set();
  const expected = new Map();
  const violations = [];
  const roles = new Map();
  const aoJiuDetails = [];
  let total = 0;

  function reject(lineIndex, charIndex, rule, required, actualChar) {
    errors.add(key(lineIndex, charIndex));
    violations.push({ lineIndex, charIndex, rule, required, char: actualChar });
  }

  function markRole(lineIndex, charIndex, role) {
    const position = key(lineIndex, charIndex);
    if (!roles.has(position)) roles.set(position, new Set());
    roles.get(position).add(role);
  }

  function recordAoJiu(rule, type, positions, message) {
    for (const position of positions) {
      markRole(position.lineIndex, position.charIndex, position.role);
    }
    aoJiuDetails.push({ rule, type, positions, message });
  }

  for (let lineIndex = 0; lineIndex < lines.length; lineIndex += 1) {
    const chars = [...lines[lineIndex]];
    if (chars.length !== charCount) continue;
    total += chars.length;
    const lineBase = expectedLineBase(globalBase, lineIndex);
    const isEven = (lineIndex + 1) % 2 === 0;
    const endTone = isEven ? rhymeTone : lineIndex === 0 ? null : opposite(rhymeTone);
    const endEntry = lexicon.lookup(chars.at(-1));
    const isRhyming = endTone === rhymeTone
      || (lineIndex === 0 && rescueHasTone(endEntry, rhymeTone, strictPolyphonic));
    const keyIndex = charCount - 2;
    const rescueIndex = keyIndex - 1;
    const checks = [[1, lineBase], [3, opposite(lineBase)]];
    if (charCount === 7) checks.push([5, lineBase]);

    for (const [charIndex, required] of checks) {
      const entry = lexicon.lookup(chars[charIndex]);
      expected.set(key(lineIndex, charIndex), required);
      const allowedTones = new Set([required]);
      const canUseMajorAo = allowAoJiu
        && rhymeTone === TONE_PING
        && !isRhyming
        && charIndex === keyIndex;
      if (canUseMajorAo && required === TONE_PING) {
        allowedTones.add(TONE_ZE);
      } else if (
        canUseMajorAo
        && required === TONE_ZE
        && ruleCanUseTone(
          lexicon.lookup(chars[rescueIndex]),
          TONE_ZE,
          strictPolyphonic,
        )
        && rescueHasTone(
          lexicon.lookup(chars[rescueIndex - 2]),
          TONE_PING,
          strictPolyphonic,
        )
      ) {
        allowedTones.add(TONE_PING);
      }
      if (entry.tones.size && !tonesFit(entry, allowedTones, strictPolyphonic)) {
        reject(lineIndex, charIndex, "二四六分明", required, chars[charIndex]);
      }
    }

    if (allowAoJiu && rhymeTone === TONE_PING && !isRhyming) {
      const keyExpected = checks.find(([charIndex]) => charIndex === keyIndex)?.[1];
      const keyEntry = lexicon.lookup(chars[keyIndex]);
      const rescueEntry = lexicon.lookup(chars[rescueIndex]);
      if (keyExpected === TONE_PING
        && ruleCanUseTone(keyEntry, TONE_ZE, strictPolyphonic)
        && rescueHasTone(rescueEntry, TONE_PING, strictPolyphonic)) {
        const rule = charCount === 5 ? "四拗三救" : "六拗五救";
        recordAoJiu(
          rule,
          "本句自救",
          [
            { lineIndex, charIndex: keyIndex, role: "ao" },
            { lineIndex, charIndex: rescueIndex, role: "rescue" },
          ],
          "第 " + (lineIndex + 1) + " 句第 " + (keyIndex + 1) + " 字“"
            + chars[keyIndex] + "”拗，第 " + (rescueIndex + 1) + " 字“"
            + chars[rescueIndex] + "”救，符合" + rule + "。",
        );
      } else if (keyExpected === TONE_ZE
        && ruleCanUseTone(keyEntry, TONE_PING, strictPolyphonic)
        && ruleCanUseTone(rescueEntry, TONE_ZE, strictPolyphonic)
        && rescueHasTone(
          lexicon.lookup(chars[rescueIndex - 2]),
          TONE_PING,
          strictPolyphonic,
        )) {
        recordAoJiu(
          "特拗交换",
          "本句自救",
          [
            { lineIndex, charIndex: rescueIndex, role: "ao" },
            { lineIndex, charIndex: keyIndex, role: "rescue" },
          ],
          "第 " + (lineIndex + 1) + " 句“" + chars[rescueIndex]
            + chars[keyIndex] + "”平仄互换，符合本句自救。",
        );
      }
    }

    const endingEntries = chars.slice(-3).map((char) => lexicon.lookup(char));
    if (isRhyming && endingEntries.every(
      (entry) => ruleCanUseTone(entry, rhymeTone, strictPolyphonic),
    )) {
      reject(
        lineIndex,
        chars.length - 1,
        rhymeTone === TONE_PING ? "三平尾" : "三仄尾",
        "非三连" + rhymeTone,
        chars.at(-1),
      );
    }

    if (endTone) {
      const endIndex = chars.length - 1;
      const entry = lexicon.lookup(chars[endIndex]);
      expected.set(key(lineIndex, endIndex), endTone);
      if (entry.tones.size
        && !tonesFit(entry, new Set([endTone]), strictPolyphonic)) {
        reject(lineIndex, endIndex, "末字收束", endTone, chars[endIndex]);
      }
    }

    if (isRhyming && rhymeTone === TONE_PING) {
      const nonRhymePingPositions = chars
        .slice(0, -1)
        .map((char) => lexicon.lookup(char))
        .map((entry, charIndex) => ({ entry, charIndex }))
        .filter(({ entry }) => rescueHasTone(entry, TONE_PING, strictPolyphonic))
        .map(({ charIndex }) => charIndex);
      if (lineBase === TONE_PING) {
        if (nonRhymePingPositions.length === 1
          && nonRhymePingPositions[0] === 1) {
          reject(lineIndex, 1, "孤平", "第三字用平声自救", chars[1]);
        }
      } else if (charCount === 7) {
        if (nonRhymePingPositions.length === 1
          && nonRhymePingPositions[0] === 3) {
          reject(lineIndex, 3, "孤平", "第五字用平声自救", chars[3]);
        }
      }
    }

    if (allowAoJiu && isEven && lineIndex > 0) {
      const previous = [...lines[lineIndex - 1]];
      const previousBase = expectedLineBase(globalBase, lineIndex - 1);
      const previousKeyExpected = charCount === 7
        ? previousBase
        : opposite(previousBase);
      const previousKey = lexicon.lookup(previous[keyIndex]);
      const previousRescue = lexicon.lookup(previous[rescueIndex]);
      const needsCrossRescue = previousKeyExpected === TONE_PING
        && ruleCanUseTone(previousKey, TONE_ZE, strictPolyphonic)
        && ruleCanUseTone(previousRescue, TONE_ZE, strictPolyphonic);
      if (needsCrossRescue) {
        const rescue = lexicon.lookup(chars[rescueIndex]);
        expected.set(key(lineIndex, rescueIndex), TONE_PING);
        if (!rescueHasTone(rescue, TONE_PING, strictPolyphonic)) {
          reject(
            lineIndex,
            rescueIndex,
            "对句相救",
            "以平声救出句大拗",
            chars[rescueIndex],
          );
        } else {
          const selfRescueIndex = aoJiuDetails.findIndex((detail) =>
            detail.rule === "孤平自救"
              && detail.positions.some((position) => position.lineIndex === lineIndex
                && position.charIndex === rescueIndex
                && position.role === "rescue"));
          if (selfRescueIndex >= 0) {
            const [selfRescue] = aoJiuDetails.splice(selfRescueIndex, 1);
            const selfAo = selfRescue.positions.find((position) => position.role === "ao");
            recordAoJiu(
              "一字两救",
              "本句自救、对句相救",
              [
                { lineIndex: lineIndex - 1, charIndex: keyIndex, role: "ao" },
                selfAo,
                { lineIndex, charIndex: rescueIndex, role: "rescue" },
              ],
              "第 " + (lineIndex + 1) + " 句第 " + (rescueIndex + 1) + " 字“"
                + chars[rescueIndex] + "”一字两救：既救本句第 "
                + (selfAo.charIndex + 1) + " 字“" + chars[selfAo.charIndex]
                + "”造成的孤平，也救第 " + lineIndex + " 句第 "
                + (keyIndex + 1) + " 字“" + previous[keyIndex] + "”之拗。",
            );
          } else {
            recordAoJiu(
              "对句相救",
              "对句相救",
              [
                { lineIndex: lineIndex - 1, charIndex: keyIndex, role: "ao" },
                { lineIndex, charIndex: rescueIndex, role: "rescue" },
              ],
              "第 " + lineIndex + " 句第 " + (keyIndex + 1) + " 字“"
                + previous[keyIndex] + "”拗，第 " + (lineIndex + 1) + " 句第 "
                + (rescueIndex + 1) + " 字“" + chars[rescueIndex]
                + "”救，符合对句相救。",
            );
          }
        }
      }
    }
  }

  return {
    score: total ? (total - errors.size) / total : 0,
    errors,
    expected,
    violations,
    roles,
    aoJiuDetails,
    total,
  };
}

function selectGlobalBase(lines, charCount, rhymeTone, lexicon, options) {
  const firstSecond = lines[0]?.[1];
  let candidates = firstSecond ? [...lexicon.lookup(firstSecond).tones] : [];
  if (!candidates.length) candidates = [TONE_PING, TONE_ZE];
  let best = null;
  for (const candidate of candidates) {
    const result = scoreTangTones(lines, charCount, candidate, rhymeTone, lexicon, options);
    if (!best || result.score > best.result.score) best = { tone: candidate, result };
  }
  return best;
}

function evaluateTangLike(text, charCount, expectedLineCount, lexicon, kind, options = {}) {
  const lines = parsePoem(text);
  const issues = [];
  let structureMismatches = 0;
  for (let index = 0; index < lines.length; index += 1) {
    if ([...lines[index]].length !== charCount) {
      structureMismatches += 1;
      issues.push("第 " + (index + 1) + " 句应为 " + charCount
        + " 字，实际为 " + [...lines[index]].length + " 字");
    }
  }
  if (expectedLineCount !== null && lines.length !== expectedLineCount) {
    issues.push("应为 " + expectedLineCount + " 句，实际为 " + lines.length + " 句");
  }
  if (kind === "pailv" && (lines.length < 10 || lines.length % 2 !== 0)) {
    issues.push("排律应为不少于十句的偶数句");
  }

  let structure = (lines.length - structureMismatches) / Math.max(lines.length, 1);
  const invalidLineCount = expectedLineCount !== null
    ? lines.length !== expectedLineCount
    : lines.length < 10 || lines.length % 2 !== 0;
  if (invalidLineCount) structure = Math.min(structure, 0.5);

  const rhymeTone = detectRhymeTone(lines, lexicon);
  const selected = selectGlobalBase(lines, charCount, rhymeTone, lexicon, options);
  const tonal = selected?.result ?? {
    score: 0,
    errors: new Set(),
    expected: new Map(),
    violations: [],
    roles: new Map(),
    aoJiuDetails: [],
    total: 0,
  };
  const rhymePositions = [];
  for (let index = 1; index < lines.length; index += 2) rhymePositions.push(index);
  if (lines[0]) {
    const firstEnd = lexicon.lookup([...lines[0]].at(-1));
    if (firstEnd.tones.has(rhymeTone)) rhymePositions.unshift(0);
  }
  const rhyme = scoreRhymeGroups(
    lines,
    [rhymePositions],
    lexicon,
    new Map([[0, rhymeTone]]),
    options,
  );
  const rhymeViolations = rhyme.details.flatMap((group) => group.invalidEndings);
  const readingDecisions = buildAutomaticReadingDecisions(
    lines,
    lexicon,
    tonal.expected,
    tonal.roles,
    rhyme.details,
    {
      options,
      rhymeTone,
      globalBase: selected?.tone,
      charCount,
      aoJiuDetails: tonal.aoJiuDetails,
    },
  );

  return {
    kind,
    structureScore: percentage(structure),
    tonalScore: percentage(tonal.score),
    rhymeScore: percentage(rhyme.score),
    lines: annotateLines(
      lines,
      lexicon,
      tonal.errors,
      tonal.expected,
      tonal.roles,
      readingDecisions,
    ),
    errors: tonal.errors,
    issues,
    rhymeGroups: rhyme.details,
    polyphonicDecisions: polyphonicDecisionDetails(lines, readingDecisions),
    violations: tonal.violations,
    rhymeViolations,
    aoJiu: {
      enabled: Boolean(options.allowAoJiu),
      details: tonal.aoJiuDetails,
    },
    stats: {
      matchingTones: tonal.total - tonal.errors.size,
      totalTones: tonal.total,
      globalBase: selected?.tone ?? null,
      rhymeTone,
      charCount,
    },
  };
}

export function evaluateTang(text, options, lexicon) {
  const charCount = Number(options.charCount);
  const lineCount = options.form === "jueju" ? 4 : 8;
  return evaluateTangLike(text, charCount, lineCount, lexicon, "tang", options);
}

export function evaluatePailv(text, lexicon, options = {}) {
  const lines = parsePoem(text);
  const validLengths = lines
    .map((line) => [...line].length)
    .filter((length) => length === 5 || length === 7);
  const charCount = validLengths.length
    ? (validLengths.filter((length) => length === 5).length > validLengths.length / 2 ? 5 : 7)
    : 7;
  return evaluateTangLike(text, charCount, null, lexicon, "pailv", options);
}

function comprehensiveScore(result) {
  return (result.structureScore + result.tonalScore + result.rhymeScore) / 3;
}

export function evaluateBestTangForm(text, lexicon, options = {}, current = {}) {
  const candidates = [
    { type: "tang", charCount: 5, form: "jueju" },
    { type: "tang", charCount: 7, form: "jueju" },
    { type: "tang", charCount: 5, form: "lvshi" },
    { type: "tang", charCount: 7, form: "lvshi" },
    { type: "pailv" },
  ].map((candidate) => {
    const result = candidate.type === "pailv"
      ? evaluatePailv(text, lexicon, options)
      : evaluateTang(text, { ...options, ...candidate }, lexicon);
    const isCurrent = candidate.type === current.type
      && (candidate.type === "pailv"
        || (candidate.charCount === Number(current.charCount)
          && candidate.form === current.form));
    return { ...candidate, result, score: comprehensiveScore(result), isCurrent };
  });
  candidates.sort((left, right) => right.score - left.score
    || Number(right.isCurrent) - Number(left.isCurrent)
    || right.result.structureScore - left.result.structureScore);
  return { ...candidates[0], candidates };
}

export function evaluateHaiku(text, lexicon, options = {}) {
  const strictPolyphonic = isStrictPolyphonic(options);
  const allowAoJiu = options.allowAoJiu !== false;
  const lines = parsePoem(text);
  const pattern = String(options.linePattern || "auto");
  const expectedLengths = ["3-5-3", "353", "三五三"].includes(pattern)
    || (pattern === "auto" && lines.length === 3 && lines.every((line, i) => [...line].length === [3, 5, 3][i]))
    ? [3, 5, 3] : [5, 7, 5];
  const errors = new Set();
  const issues = [];
  let total = 0;
  let structurallyCorrect = 0;

  for (let lineIndex = 0; lineIndex < lines.length; lineIndex += 1) {
    const chars = [...lines[lineIndex]];
    const expectedLength = expectedLengths[lineIndex];
    if (expectedLength === undefined) {
      issues.push("多出第 " + (lineIndex + 1) + " 行");
      continue;
    }
    if (chars.length !== expectedLength) {
      issues.push("第 " + (lineIndex + 1) + " 行应为 " + expectedLength
        + " 字，实际为 " + chars.length + " 字");
      continue;
    }
    structurallyCorrect += 1;
    total += chars.length;
    const ending = chars.slice(-3).map((char) => lexicon.lookup(char));
    for (const tone of options.forbidThreeSameEnding === false ? [] : [TONE_PING, TONE_ZE]) {
      if (ending.every((entry) => ruleCanUseTone(entry, tone, strictPolyphonic))) {
        errors.add(key(lineIndex, chars.length - 1));
      }
    }
    for (let index = 1; index < chars.length - 1; index += 1) {
      const left = lexicon.lookup(chars[index - 1]);
      const center = lexicon.lookup(chars[index]);
      const right = lexicon.lookup(chars[index + 1]);
      const isolated = ruleCanUseTone(left, TONE_ZE, strictPolyphonic)
        && ruleCanUseTone(center, TONE_PING, strictPolyphonic)
        && ruleCanUseTone(right, TONE_ZE, strictPolyphonic);
      const leftRescue = index >= 2
        && rescueHasTone(lexicon.lookup(chars[index - 2]), TONE_PING, strictPolyphonic);
      const rightRescue = index + 2 < chars.length
        && rescueHasTone(lexicon.lookup(chars[index + 2]), TONE_PING, strictPolyphonic);
      if (options.forbidIsolatedLevel !== false && isolated && !(allowAoJiu && (leftRescue || rightRescue))) {
        errors.add(key(lineIndex, index));
      }
    }
  }
  if (lines.length < 3) issues.push("缺少 " + (3 - lines.length) + " 行");
  const schemes = { AAA: [0, 1, 2], ABA: [0, 2], BAA: [1, 2] };
  const scheme = String(options.rhymeScheme || "auto").toUpperCase();
  const candidates = Object.entries(schemes).filter(([name]) => !schemes[scheme] || name === scheme)
    .map(([name, positions]) => {
      const scored = [TONE_PING, TONE_ZE].map((tone) =>
        scoreRhymeGroups(lines, [positions], lexicon, new Map([[0, tone]]), options));
      return { ...scored.sort((a, b) => b.score - a.score)[0], scheme: name };
    });
  const rhyme = ["NONE", "不押韵"].includes(scheme)
    ? { score: 1, details: [], scheme: "none" }
    : candidates.sort((a, b) => b.score - a.score)[0];
  const rhymeViolations = rhyme.details.flatMap((group) => group.invalidEndings);
  const readingDecisions = buildAutomaticReadingDecisions(
    lines,
    lexicon,
    new Map(),
    new Map(),
    rhyme.details,
    { options },
  );
  return {
    kind: "haiku",
    structureScore: percentage(structurallyCorrect / expectedLengths.length),
    tonalScore: percentage(total ? (total - errors.size) / total : 0),
    rhymeScore: percentage(rhyme.score),
    lines: annotateLines(lines, lexicon, errors, new Map(), new Map(), readingDecisions),
    errors,
    issues,
    rhymeGroups: rhyme.details,
    violations: [...errors].map((position) => {
      const [lineIndex, charIndex] = position.split(":").map(Number);
      const char = [...lines[lineIndex]][charIndex];
      const ending = charIndex === [...lines[lineIndex]].length - 1;
      return { lineIndex, charIndex, char, rule: ending ? "三连同" : "孤平", required: ending ? "避免句尾三连平或三连仄" : "邻位用平声自救" };
    }).map((item) => ({ ...item, message: `第 ${item.lineIndex + 1} 行第 ${item.charIndex + 1} 字“${item.char}”：${item.rule}，${item.required}。` })),
    rhymeViolations,
    polyphonicDecisions: polyphonicDecisionDetails(lines, readingDecisions),
    stats: { matchingTones: total - errors.size, totalTones: total, rhymeScheme: rhyme.scheme, linePattern: expectedLengths.join("-") },
  };
}

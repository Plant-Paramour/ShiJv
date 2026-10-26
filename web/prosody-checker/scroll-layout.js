const HAN_CHAR = /[\u3400-\u9fff]/u;

function characters(text) {
  return [...String(text ?? "")].filter((char) => HAN_CHAR.test(char));
}

function parseBreaks(pattern) {
  const breakPositions = new Set();
  const caesuraPositions = new Set();
  let charCount = 0;

  for (const char of String(pattern ?? "")) {
    if (char === "/") breakPositions.add(charCount);
    else if (char === "、") caesuraPositions.add(charCount);
    else if (HAN_CHAR.test(char) || char === "平" || char === "仄" || char === "中") {
      charCount += 1;
    }
  }

  return { breakPositions, caesuraPositions, length: charCount };
}

function lineMetrics(line, index, variant) {
  const stanza = variant[`stanza${line.stanzaIndex + 1}`];
  if (!stanza || !Array.isArray(stanza.lines) || stanza.lines[line.lineInStanza] === undefined) {
    return {
      ...line,
      index,
      expectedLength: 0,
      breakPositions: new Set(),
      caesuraPositions: new Set(),
      rhymeGroup: null,
    };
  }
  const pattern = stanza.lines[line.lineInStanza];
  const parsed = parseBreaks(pattern);
  let rhymeGroup = null;
  for (const [key, positions] of Object.entries(stanza)) {
    const match = key.match(/^rhyme_(\d+)_positions$/);
    if (match && Array.isArray(positions) && positions.includes(line.lineInStanza + 1)) {
      rhymeGroup = Number(match[1]);
      break;
    }
  }
  return {
    ...line,
    index,
    expectedLength: parsed.length,
    breakPositions: parsed.breakPositions,
    caesuraPositions: parsed.caesuraPositions,
    rhymeGroup,
  };
}

function getLineGap(line, position, metrics) {
  if (line.breakPositions.has(position)) return metrics.breakGap;
  if (line.caesuraPositions.has(position)) return metrics.caesuraGap;
  return 0;
}

/**
 * Creates horizontal Song Ci glyph coordinates for a scroll-shaped canvas.
 * The scroll affects only the canvas proportions; text remains left-to-right.
 */
export function layoutSongciScroll({
  lines,
  variant,
  width = 1200,
  height = 720,
  options = {},
} = {}) {
  if (!variant || !Array.isArray(lines)) {
    throw new TypeError("layoutSongciScroll requires variant and lines");
  }

  const metrics = {
    charWidth: options.charWidth ?? 42,
    charHeight: options.charHeight ?? 42,
    lineGap: options.lineGap ?? 24,
    stanzaGap: options.stanzaGap ?? 56,
    caesuraGap: options.caesuraGap ?? 8,
    breakGap: options.breakGap ?? 18,
    left: options.left ?? Math.round(width * 0.14),
    top: options.top ?? Math.round(height * 0.22),
  };

  const meterLines = [];
  for (let stanzaIndex = 0; stanzaIndex < (variant.number_of_stanzas ?? 0); stanzaIndex += 1) {
    const stanzaData = variant[`stanza${stanzaIndex + 1}`];
    for (let lineInStanza = 0; lineInStanza < stanzaData.lines.length; lineInStanza += 1) {
      meterLines.push({ stanzaIndex, lineInStanza });
    }
  }

  const enriched = lines.map((text, index) => {
    const position = meterLines[index] ?? { stanzaIndex: 0, lineInStanza: index };
    const chars = characters(text);
    const line = lineMetrics({ text, ...position }, index, variant);
    return {
      ...line,
      chars,
      isRhyme: line.rhymeGroup !== null,
      rhymeChar: line.rhymeGroup !== null ? chars.at(-1) ?? null : null,
      lengthValid: chars.length === line.expectedLength,
    };
  });

  const glyphLines = [];
  const stanzaLayouts = [];
  let y = metrics.top;
  let previousStanza = null;
  let maxRight = metrics.left;

  for (const line of enriched) {
    if (previousStanza !== null && line.stanzaIndex !== previousStanza) {
      y += metrics.stanzaGap;
    }

    let x = metrics.left;
    const glyphs = line.chars.map((char, charIndex) => {
      const glyph = {
        char,
        x,
        y,
        charIndex,
        isRhyme: line.isRhyme && charIndex === line.chars.length - 1,
        rhymeGroup: line.isRhyme ? line.rhymeGroup : null,
      };
      x += metrics.charWidth + getLineGap(line, charIndex + 1, metrics);
      return glyph;
    });

    const layoutLine = {
      ...line,
      x: metrics.left,
      y,
      width: Math.max(0, x - metrics.left - metrics.charWidth),
      glyphs,
    };
    glyphLines.push(layoutLine);
    maxRight = Math.max(maxRight, x);
    y += metrics.charHeight + metrics.lineGap;
    previousStanza = line.stanzaIndex;
  }

  for (const line of glyphLines) {
    let stanza = stanzaLayouts.find((item) => item.index === line.stanzaIndex);
    if (!stanza) {
      stanza = { index: line.stanzaIndex, lines: [] };
      stanzaLayouts.push(stanza);
    }
    stanza.lines.push(line);
  }

  return {
    direction: "horizontal",
    width,
    height,
    contentBounds: {
      left: metrics.left,
      top: metrics.top,
      right: maxRight,
      bottom: Math.max(metrics.top, y - metrics.lineGap),
    },
    metrics,
    stanzas: stanzaLayouts,
    lines: glyphLines,
    issues: glyphLines
      .filter((line) => !line.lengthValid)
      .map((line) => ({
        lineIndex: line.index,
        expectedLength: line.expectedLength,
        actualLength: line.chars.length,
      })),
  };
}

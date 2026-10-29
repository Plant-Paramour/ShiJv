import { buildLexicon, flattenMeter, scoreTangTones } from "./core.js?v=22";
import { getDatasetJson, getMeterBundle } from "./dataset-cache.js?v=4";

const books = new Set(["Xinyun", "Pinshui", "Cilin", "Tongyun"]);
const lexicons = new WeakMap();
const intersect = (a, b) => new Set([...a].filter((value) => b.has(value)));
const fits = (tones, allowed, strict) => tones.size > 0
  && (strict ? [...tones].every((tone) => allowed.has(tone)) : [...tones].some((tone) => allowed.has(tone)));

async function loadBook(name = "Xinyun") {
  if (!books.has(name)) throw new Error("不支持的韵书");
  const data = await getDatasetJson("rhyme", `${name}.json`);
  if (!lexicons.has(data)) lexicons.set(data, buildLexicon(data));
  return { data, lexicon: lexicons.get(data) };
}

export async function getLocalMeterCatalog() {
  const catalog = await getDatasetJson("meter", "index.json");
  const files = (catalog.items || catalog).map((item) => typeof item === "string" ? item : item.file).filter(Boolean);
  return (await getMeterBundle(files)).map(({ file, data }) => ({
    name: data.name || file.replace(/\.json$/, ""), aliases: data.aliases || [],
    default_variant: data.default_variant || data.variants?.[0]?.name || "",
    variants: (data.variants || []).map((item) => item.name),
  }));
}

async function shape(request) {
  const type = request.meter_type || "唐诗";
  const form = String(request.form_name || "五言绝句");
  const options = request.task_options || {};
  if (type === "宋词") {
    const meter = await getDatasetJson("meter", `${form}.json`);
    const name = request.variant_name || options.variant_name || meter.default_variant;
    const variant = meter.variants.find((item) => item.name === name) || (!name ? meter.variants[0] : null);
    if (!variant) throw new Error("词牌变体不存在");
    const flat = flattenMeter(variant);
    return { lengths: flat.clauses.map((line) => [...line.pattern].length),
      groups: flat.rhymeGroups, patterns: flat.clauses.map((line) => [...line.pattern]), variant, tone: null };
  }
  if (type === "汉俳") {
    const pattern = String(options.line_pattern || "5-7-5").replaceAll("五", "5").replaceAll("七", "7").replaceAll("－", "-");
    if (!["5-7-5", "3-5-3"].includes(pattern)) throw new Error("汉俳格式仅支持 5-7-5 或 3-5-3");
    const scheme = String(options.rhyme_scheme || "").trim().toUpperCase();
    const positions = { AAA: [0, 1, 2], ABA: [0, 2], BAA: [1, 2] };
    if (scheme && !["NONE", "RANDOM"].includes(scheme) && !positions[scheme]) throw new Error("不支持的汉俳押韵格式");
    return { lengths: pattern.split("-").map(Number), groups: positions[scheme] ? [{ id: 1, positions: positions[scheme] }] : [], tone: null };
  }
  if (!["唐诗", "排律"].includes(type)) throw new Error("不支持的诗体");
  const count = type === "排律" ? Number(request.num_lines ?? 10) : form.includes("绝") ? 4 : 8;
  const minimum = type === "排律" ? 10 : 4;
  if (!Number.isSafeInteger(count) || count < minimum || count > 128 || count % 2) throw new Error(`句数必须为${minimum}至128之间的偶数`);
  const width = form.includes("五") ? 5 : 7;
  return { lengths: Array(count).fill(width), tone: type === "唐诗" && form.includes("仄") && form.includes("韵") ? "仄" : "平",
    groups: [{ id: 1, positions: Array.from({ length: count }, (_, i) => i).filter((i) => i === 0 || i % 2 === 1) }] };
}

export async function getLocalRhymeOptions(request = {}) {
  const [{ data }, layout] = await Promise.all([loadBook(request.rhyme_dict_name || request.rhyme_book), shape(request)]);
  const parts = Object.entries(data).filter(([, tones]) => !layout.tone || Object.entries(tones)
    .some(([name, chars]) => (name.includes("平") ? "平" : "仄") === layout.tone && chars.length))
    .map(([part]) => part);
  return { groups: layout.groups.map((group) => group.id), parts, line_lengths: layout.lengths };
}

export async function lookupLocalRhymeParts(text, book = "Xinyun") {
  const { lexicon } = await loadBook(book);
  const character = [...String(text).trim()].reverse().find((char) => /[\u4e00-\u9fff]/u.test(char)) || "";
  return { character, parts: character ? [...lexicon.lookup(character).parts].sort() : [] };
}

function checkRepeats(fixed, variant) {
  const stanzas = Object.keys(variant).filter((name) => /^stanza\d+$/.test(name))
    .sort((a, b) => Number(a.slice(6)) - Number(b.slice(6)));
  const offsets = new Map(); let offset = 0;
  for (const name of stanzas) { offsets.set(Number(name.slice(6)), offset); offset += variant[name].lines.length; }
  for (const group of variant.repetition_groups || []) {
    const known = new Map();
    for (const position of group.positions || []) {
      const start = position.start, end = position.end;
      if (!start || !end || !offsets.has(position.stanza)) continue;
      let index = 0;
      for (let line = start[0]; line <= end[0]; line++) {
        const number = offsets.get(position.stanza) + line;
        const raw = variant[`stanza${position.stanza}`].lines[line - 1];
        const width = [...raw.replace(/[\/、\s]/g, "")].length;
        for (let column = line === start[0] ? start[1] : 1; column <= (line === end[0] ? end[1] : width); column++) {
          const char = fixed[number]?.[column - 1];
          if (char && known.has(index) && known.get(index) !== char) throw new Error("固定句与叠字、叠句要求冲突");
          if (char) known.set(index, char);
          index++;
        }
      }
    }
  }
}

// Interactive preflight uses only downloaded public datasets. Task submission
// still receives authoritative server validation; this result is not a credential.
export async function validateLocalFixedLines(request = {}) {
  const [{ lexicon }, layout] = await Promise.all([loadBook(request.rhyme_dict_name), shape(request)]);
  const fixed = Object.fromEntries(Object.entries(request.fixed_lines || {}).map(([number, text]) => [number, [...String(text).trim()]]));
  const strict = Boolean(request.strict_polyphonic);
  const check = (values) => {
    let bases;
    for (const [number, chars] of Object.entries(values)) {
      const i = Number(number) - 1;
      if (!/^\d+$/.test(number) || !Number.isInteger(i) || i < 0 || i >= layout.lengths.length) throw new Error("固定句序号超出范围");
      if (chars.length !== layout.lengths[i]) throw new Error(`第${number}句应为${layout.lengths[i]}字，实际为${chars.length}字`);
      for (const [column, char] of chars.entries()) {
        const required = layout.patterns?.[i]?.[column];
        const tones = lexicon.lookup(char).tones;
        if (!fits(tones, new Set(required && required !== "中" ? [required] : ["平", "仄"]), strict)) {
          throw new Error(`第${number}句第${column + 1}字不符合所选韵书或平仄要求`);
        }
      }
    }
    if (layout.tone) {
      const lines = layout.lengths.map((_, i) => values[i + 1]?.join("") || "");
      bases = ["平", "仄"].filter((base) => scoreTangTones(lines, layout.lengths[0], base, layout.tone, lexicon,
        { strictPolyphonic: strict, allowAoJiu: request.task_options?.allow_aojiu !== false }).errors.size === 0);
      if (!bases.length) throw new Error("固定诗句无法共同确定合法的替、对、粘格式");
    }
    if (layout.variant) checkRepeats(values, layout.variant);
    const locked = new Map();
    for (const group of layout.groups) {
      let common = null;
      for (const i of group.positions) {
        const chars = values[i + 1];
        if (!chars) continue;
        const entry = lexicon.lookup(chars.at(-1));
        if (layout.tone && i === 0 && !fits(entry.tones, new Set([layout.tone]), strict)) continue;
        const pattern = layout.patterns?.[i]?.at(-1);
        const allowed = new Set(layout.tone ? [layout.tone] : pattern && pattern !== "中" ? [pattern] : ["平", "仄"]);
        if (layout.variant && group.id === 1 && ["平韵", "仄韵"].includes(layout.variant.rhyme_type)) {
          allowed.clear(); allowed.add(layout.variant.rhyme_type[0]);
        }
        if (!fits(entry.tones, allowed, strict)) throw new Error(`第${i + 1}句韵脚声调不符`);
        const readings = new Set([...entry.readings].filter((reading) => allowed.has(reading.split(":")[0])));
        const parts = new Set([...readings].map((reading) => reading.slice(reading.indexOf(":") + 1)));
        if (strict && parts.size !== 1) throw new Error(`第${i + 1}句韵脚存在歧义`);
        const choices = request.meter_type === "汉俳" ? readings : parts;
        common = common === null ? choices : intersect(common, choices);
        if (!common.size) throw new Error("同组固定韵脚不存在共同韵部");
      }
      if (common !== null) {
        let parts = request.meter_type === "汉俳"
          ? new Set([...common].map((reading) => reading.slice(reading.indexOf(":") + 1))) : common;
        const selected = request.rhyme_mode === "fixed" ? request.rhyme_parts?.[group.id] : null;
        if (selected) parts = intersect(parts, new Set([selected]));
        if (!parts.size) throw new Error("固定韵脚与指定韵部冲突");
        locked.set(group.id, parts);
      }
    }
    const detail = {};
    if (bases?.length === 1) detail.base_tone = bases[0] === "平" ? 0 : 1;
    if (locked.has(1)) detail.rhyme_parts = [...locked.get(1)].sort();
    return detail;
  };
  const lines = Object.entries(fixed).map(([number, chars]) => {
    try { check({ [number]: chars }); return { number, valid: true }; }
    catch (error) { return { number, valid: false, error: error.message }; }
  });
  try { return { valid: true, lines, ...check(fixed) }; }
  catch (error) { return { valid: false, lines, error: error.message }; }
}

import {
  buildLexicon,
  evaluateBestTangForm,
  evaluateHaiku,
  evaluatePailv,
  evaluateSongci,
  flattenMeter,
} from "./core.js?v=22";
import { getDatasetJson } from "./dataset-cache.js?v=3";

export const RHYME_BOOKS = {
  Xinyun: { file: "Xinyun.json", name: "中华新韵" },
  Pinshui: { file: "Pinshui.json", name: "平水韵" },
  Cilin: { file: "Cilin.json", name: "词林正韵" },
  Tongyun: { file: "Tongyun.json", name: "中华通韵" },
};

const lexiconCache = new WeakMap();

export async function getLexicon(bookId) {
  const book = RHYME_BOOKS[bookId];
  if (!book) throw new Error(`不支持的韵书：${bookId}`);
  const data = await getDatasetJson("rhyme", book.file);
  if (!lexiconCache.has(data)) lexiconCache.set(data, buildLexicon(data));
  return lexiconCache.get(data);
}

async function getMeter(formName) {
  return getDatasetJson("meter", `${formName}.json`);
}

export async function getSongciStanzaEnds(request = {}) {
  const formName = String(request.form_name || "").split(/\s*[·|]\s*/u)[0].trim();
  if (!formName || (request.meter_type && request.meter_type !== "宋词")) return [];
  const meter = await getMeter(formName);
  const variantName = request.variant_name
    || request.task_options?.variant_name
    || meter.default_variant;
  const variant = meter.variants.find((item) => item.name === variantName)
    || meter.variants[0];
  return variant ? flattenMeter(variant).stanzaEnds : [];
}

function comprehensiveScore(result) {
  return (result.structureScore + result.tonalScore + result.rhymeScore) / 3;
}

function summary(item) {
  return {
    rhyme_book_id: item.rhymeBookId,
    rhyme_book_name: RHYME_BOOKS[item.rhymeBookId].name,
    type: item.type,
    form_name: item.formName,
    score: Number(item.score.toFixed(2)),
    structure_score: item.result.structureScore,
    tonal_score: item.result.tonalScore,
    rhyme_score: item.result.rhymeScore,
  };
}

function selectBest(candidates, preferredBook) {
  candidates.sort((left, right) => right.score - left.score
    || Number(right.rhymeBookId === preferredBook) - Number(left.rhymeBookId === preferredBook)
    || right.result.structureScore - left.result.structureScore);
  return candidates[0];
}

function compactCharacterAnnotations(result) {
  if (!Array.isArray(result.lines)) return result;
  result.lines = result.lines.map((line) => ({
    ...line,
    characters: (line.characters || []).map((character) => {
      const compact = { ...character };
      if (!compact.roles?.length) delete compact.roles;
      if (compact.selectedTone == null) delete compact.selectedTone;
      if (compact.selectedPart == null) delete compact.selectedPart;
      if (compact.decisionReason == null) delete compact.decisionReason;
      return compact;
    }),
  }));
  return result;
}

function finalEvaluation(best, candidates) {
  compactCharacterAnnotations(best.result);
  best.result.stats = best.result.stats || {};
  best.result.stats.comprehensiveScore = best.score;
  best.result.stats.autoMatched = true;
  return {
    version: 8,
    evaluated_at: Date.now() / 1000,
    rhyme_book_id: best.rhymeBookId,
    rhyme_book_name: RHYME_BOOKS[best.rhymeBookId].name,
    type: best.type,
    form_name: best.formName,
    comprehensive_score: Number(best.score.toFixed(2)),
    structure_score: best.result.structureScore,
    tonal_score: best.result.tonalScore,
    rhyme_score: best.result.rhymeScore,
    result: best.result,
    alternatives: candidates.map(summary),
  };
}

function currentTangForm(formName = "") {
  if (/排律/.test(formName)) return { type: "pailv" };
  return {
    type: "tang",
    charCount: /五言/.test(formName) ? 5 : 7,
    form: /律诗/.test(formName) ? "lvshi" : "jueju",
  };
}

export async function evaluateBestTangAcrossBooks(text, options = {}, current = {}) {
  const candidates = [];
  const books = Object.keys(RHYME_BOOKS);
  const lexicons = await Promise.all(books.map(getLexicon));
  for (const [index, rhymeBookId] of books.entries()) {
    const lexicon = lexicons[index];
    const best = evaluateBestTangForm(text, lexicon, options, current);
    for (const candidate of best.candidates) {
      candidates.push({
        ...candidate,
        rhymeBookId,
        formName: candidate.type === "pailv"
          ? `${candidate.result.stats.charCount === 5 ? "五" : "七"}言排律`
          : `${candidate.charCount === 5 ? "五" : "七"}言${candidate.form === "lvshi" ? "律诗" : "绝句"}`,
      });
    }
  }
  const best = selectBest(candidates, current.rhymeBookId);
  return { ...best, candidates, evaluation: finalEvaluation(best, candidates) };
}

export async function evaluateGeneratedPoem(text, request = {}, overrides = {}) {
  const options = {
    strictPolyphonic: false,
    polyphonicMode: "automatic",
    allowAoJiu: request.task_options?.allow_aojiu !== false,
    linePattern: request.task_options?.line_pattern || "5-7-5",
    forbidIsolatedLevel: request.task_options?.forbid_isolated_level !== false,
    forbidThreeSameEnding: request.task_options?.forbid_three_same_ending !== false,
    rhymeScheme: request.task_options?.rhyme_scheme || "none",
    ...overrides,
  };
  const preferredBook = request.rhyme_dict_name || "Xinyun";
  const meterType = request.meter_type || "唐诗";

  if (meterType === "唐诗" || meterType === "排律") {
    const best = await evaluateBestTangAcrossBooks(
      text,
      options,
      { ...currentTangForm(request.form_name), rhymeBookId: preferredBook },
    );
    return best.evaluation;
  }

  const candidates = [];
  const books = Object.keys(RHYME_BOOKS);
  const [lexicons, meter] = await Promise.all([
    Promise.all(books.map(getLexicon)),
    meterType === "汉俳" ? null : getMeter(request.form_name),
  ]);
  for (const [index, rhymeBookId] of books.entries()) {
    const lexicon = lexicons[index];
    let result;
    let formName = request.form_name || meterType;
    let type = meterType === "汉俳" ? "haiku" : "songci";
    if (meterType === "汉俳") {
      result = evaluateHaiku(text, lexicon, options);
    } else {
      const variantName = request.variant_name || request.task_options?.variant_name || meter.default_variant;
      const variant = meter.variants.find((item) => item.name === variantName) || meter.variants[0];
      result = evaluateSongci(text, variant, lexicon, options);
      formName = `${meter.name} · ${variant.name}`;
    }
    candidates.push({
      type,
      formName,
      rhymeBookId,
      result,
      score: comprehensiveScore(result),
    });
  }
  const best = selectBest(candidates, preferredBook);
  return finalEvaluation(best, candidates);
}

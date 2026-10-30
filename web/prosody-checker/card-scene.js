/**
 * 诗卡工作台的数据模型与 SVG 场景入口。
 * 这一层只负责把表单状态整理成稳定的卡片规格，并选择布局渲染器。
 */
import { renderLayout } from "./card-layouts.js";
import { createCardDocument, flattenCardDocument, CARD_TEMPLATE_CATALOG, CARD_THEME_CATALOG } from "./card-schema.js";

export { CARD_TEMPLATE_CATALOG, CARD_THEME_CATALOG };

export const CARD_FORMATS = {
  portrait: { width: 1080, height: 1440 },
  landscape: { width: 1440, height: 1080 },
};

export const LETTERFORM_PROFILES = {
  song: { family: '"ShijuSong", "汇文仿宋", "FangSong", "STFangsong", serif', label: "汇文仿宋" },
  ancient: { family: '"ShijuAncient", "文悦古体仿宋", "FangSong", serif', label: "古体仿宋" },
  idea: { family: '"ShijuIdea", "又又意宋", "STSong", serif', label: "意宋" },
  sans: { family: '"Noto Sans SC", "Microsoft YaHei", sans-serif', label: "简洁黑体" },
};

const DEFAULT_TEXT = {
  title: "春日偶成",
  body: "云淡风轻近午天\n傍花随柳过前川\n时人不识余心乐\n将谓偷闲学少年",
  author: "",
};

export function createDefaultVerseCardSpec() {
  return normalizeVerseCardSpec({
    ...DEFAULT_TEXT,
    layout: "classic-frame",
    font: "song",
    titleFont: "ancient",
    authorFont: "song",
    fontSize: 42,
    titleSize: 34,
    leading: 1.8,
    letterSpacing: 1,
    align: "middle",
    background: "#eee6d7",
    imageUrl: "",
    imageFit: "cover",
    imageOpacity: 0.55,
    imageX: 50,
    imageY: 50,
    imageScale: 1,
    imageFilter: "none",
    theme: "paper",
    showAuthor: false,
    showSeal: true,
  });
}

export function normalizeVerseCardSpec(input = {}) {
  if (input.content && input.layout?.templateId) input = flattenCardDocument(input);
  const allowedLayouts = new Set(["classic-frame", "vertical-note", "horizontal-verse", "image-accent"]);
  const allowedAlign = new Set(["start", "middle", "end"]);
  const layout = allowedLayouts.has(input.layout) ? input.layout : "classic-frame";
  const background = /^#[0-9a-f]{6}$/i.test(String(input.background || "")) ? String(input.background) : "#eee6d7";
  const fontSize = clamp(Number(input.fontSize) || 42, 24, 72);
  const leading = clamp(Number(input.leading) || 1.8, 1.35, 2.4);
  return {
    title: cleanText(input.title, 80),
    body: cleanText(input.body, 4000),
    author: cleanText(input.author, 40),
    layout,
    font: LETTERFORM_PROFILES[input.font] ? input.font : "song",
    titleFont: LETTERFORM_PROFILES[input.titleFont] ? input.titleFont : "ancient",
    authorFont: LETTERFORM_PROFILES[input.authorFont] ? input.authorFont : "song",
    fontSize,
    titleSize: clamp(Number(input.titleSize) || 34, 24, 64),
    leading,
    letterSpacing: clamp(Number(input.letterSpacing) || 0, 0, 12),
    align: allowedAlign.has(input.align) ? input.align : "middle",
    background,
    imageUrl: typeof input.imageUrl === "string" ? input.imageUrl : "",
    imageFit: input.imageFit === "contain" ? "contain" : "cover",
    imageOpacity: clamp(Number(input.imageOpacity) || 0, 0, 1),
    imageX: clamp(Number(input.imageX) || 50, 0, 100),
    imageY: clamp(Number(input.imageY) || 50, 0, 100),
    imageScale: clamp(Number(input.imageScale) || 1, 1, 2.5),
    imageFilter: ["none", "warm", "mono", "soft"].includes(input.imageFilter) ? input.imageFilter : "none",
    theme: ["paper", "vermilion", "ink", "night"].includes(input.theme) ? input.theme : "paper",
    showAuthor: input.showAuthor !== false,
    showSeal: input.showSeal !== false,
  };
}

export function createDefaultCardDocument() {
  return createCardDocument(createDefaultVerseCardSpec());
}

export function toCardDocument(spec) {
  return createCardDocument(normalizeVerseCardSpec(spec));
}

export function getCardFormat(layout, aspect = "auto") {
  if (aspect === "square") return { width: 1200, height: 1200 };
  if (aspect === "story") return { width: 1080, height: 1920 };
  if (aspect === "social") return { width: 1080, height: 1350 };
  if (aspect === "landscape") return CARD_FORMATS.landscape;
  if (aspect === "portrait") return CARD_FORMATS.portrait;
  return layout === "horizontal-verse" ? CARD_FORMATS.landscape : CARD_FORMATS.portrait;
}

export function renderCardScene(specInput, options = {}) {
  const spec = normalizeVerseCardSpec(specInput);
  const format = options.format || getCardFormat(spec.layout, options.aspect);
  const profile = LETTERFORM_PROFILES[spec.font];
  const titleProfile = LETTERFORM_PROFILES[spec.titleFont];
  const authorProfile = LETTERFORM_PROFILES[spec.authorFont];
  const bodyLines = wrapVerseText(spec.body, {
    maxWidth: options.maxWidth || (spec.layout === "horizontal-verse" ? 980 : 700),
    fontSize: spec.fontSize,
    fontFamily: profile.family,
    letterSpacing: spec.layout === "vertical-note" ? 4 : spec.letterSpacing,
  });
  const markup = renderLayout(spec, { ...format, profile, titleProfile, authorProfile, bodyLines });
  return `<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="${format.width}" height="${format.height}" viewBox="0 0 ${format.width} ${format.height}" role="img" aria-label="${escapeXml(spec.title || "诗卡")}">${markup}</svg>`;
}

export function wrapVerseText(value, options = {}) {
  const text = String(value || "").replace(/\r\n?/g, "\n");
  const paragraphs = text.split("\n");
  const maxWidth = Number(options.maxWidth) || 700;
  const fontSize = Number(options.fontSize) || 42;
  const letterSpacing = Number(options.letterSpacing) || 0;
  const measure = getTextMeasurer(fontSize, options.fontFamily, letterSpacing);
  const segmenter = typeof Intl !== "undefined" && Intl.Segmenter ? new Intl.Segmenter("zh", { granularity: "grapheme" }) : null;
  const result = [];
  for (const paragraph of paragraphs) {
    if (!paragraph) { result.push(""); continue; }
    let line = "";
    const parts = segmenter ? [...segmenter.segment(paragraph)].map((item) => item.segment) : Array.from(paragraph);
    for (const part of parts) {
      const candidate = line + part;
      if (line && measure(candidate) > maxWidth) {
        result.push(line);
        line = part;
      } else {
        line = candidate;
      }
    }
    if (line) result.push(line);
  }
  return result.length ? result : [""];
}

function getTextMeasurer(fontSize, family, letterSpacing) {
  let context = null;
  if (typeof document !== "undefined") {
    const canvas = document.createElement("canvas");
    context = canvas.getContext("2d");
    if (context) context.font = `${fontSize}px ${family || "serif"}`;
  }
  return (value) => {
    if (context) return context.measureText(value).width + Math.max(0, value.length - 1) * letterSpacing;
    return Array.from(value).reduce((total, char) => total + (/[\u0000-\u00ff]/.test(char) ? fontSize * 0.55 : fontSize) + letterSpacing, 0);
  };
}

export function escapeXml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&apos;" }[char]));
}

export function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value));
}

function cleanText(value, maxLength) {
  return String(value ?? "").replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f]/g, "").slice(0, maxLength).trim();
}

/** 四种诗卡布局的 SVG 结构。每个布局只返回 SVG 子节点，尺寸由场景层统一管理。 */
import { escapeXml, wrapVerseText } from "./card-scene.js";

export function renderLayout(spec, metrics) {
  switch (spec.layout) {
    case "vertical-note": return renderVerticalNote(spec, metrics);
    case "horizontal-verse": return renderHorizontalVerse(spec, metrics);
    case "image-accent": return renderImageAccent(spec, metrics);
    default: return renderClassicFrame(spec, metrics);
  }
}

export function renderClassicFrame(spec, { width, height, profile, titleProfile, authorProfile, bodyLines }) {
  const palette = themeColors(spec.theme);
  const ink = palette.ink;
  const anchor = spec.align === "start" ? "start" : spec.align === "end" ? "end" : "middle";
  const x = anchor === "start" ? 180 : anchor === "end" ? width - 180 : width / 2;
  const lines = bodyLines.map((line, index) => textNode(line, x, 560 + index * spec.fontSize * spec.leading, { anchor, size: spec.fontSize, family: profile.family, fill: ink })).join("");
  const author = spec.showAuthor ? (spec.author ? `—— ${spec.author}` : "") : "";
  const seal = spec.showSeal ? `<circle cx="${width - 190}" cy="${height - 170}" r="46" fill="${palette.accent}" opacity=".88"/><text x="${width - 190}" y="${height - 158}" text-anchor="middle" fill="#fff7ec" font-family="serif" font-size="32">诗矩</text>` : "";
  return `<defs>${paperTexture("classic", spec.background)}</defs><rect width="${width}" height="${height}" fill="url(#paper-classic)"/><rect width="${width}" height="${height}" fill="url(#grain-classic)"/><rect x="78" y="78" width="${width - 156}" height="${height - 156}" rx="4" fill="none" stroke="${palette.accent}" stroke-width="3"/><rect x="96" y="96" width="${width - 192}" height="${height - 192}" rx="2" fill="none" stroke="#b89175" stroke-width="1"/><path d="M150 300h120M${width - 270} 300h120" stroke="${palette.accent}" stroke-width="2"/><text x="${width / 2}" y="250" text-anchor="middle" fill="${palette.accent}" font-family="${escapeXml(titleProfile.family)}" font-size="${spec.titleSize}" letter-spacing="8">${escapeXml(spec.title || "无题")}</text>${lines}<text x="${width / 2}" y="${height - 220}" text-anchor="middle" fill="#776c61" font-family="${escapeXml(authorProfile.family)}" font-size="24">${escapeXml(author)}</text>${seal}`;
}

export function renderVerticalNote(spec, { width, height, profile, titleProfile, authorProfile }) {
  const lines = wrapVerseText(spec.body, { maxWidth: 520, fontSize: spec.fontSize, fontFamily: profile.family, letterSpacing: 4 }).filter(Boolean);
  const columnGap = spec.fontSize * spec.leading;
  const columnCenter = spec.align === "start" ? width * 0.35 : spec.align === "end" ? width * 0.65 : width / 2;
  const startX = columnCenter + ((lines.length - 1) * columnGap) / 2;
  const columns = lines.map((line, index) => verticalText(line, startX - index * columnGap, 520, spec, profile)).join("");
  const author = spec.showAuthor ? (spec.author || "") : "";
  const seal = spec.showSeal ? `<circle cx="${width - 170}" cy="${height - 190}" r="34" fill="none" stroke="#a94439" stroke-width="3"/><text x="${width - 170}" y="${height - 180}" text-anchor="middle" fill="#a94439" font-family="serif" font-size="24">印</text>` : "";
  const title = verticalText(spec.title || "无题", width - 160, 210, { ...spec, fontSize: spec.titleSize }, titleProfile);
  return `<defs>${paperTexture("vertical", spec.background)}</defs><rect width="${width}" height="${height}" fill="url(#paper-vertical)"/><path d="M130 140h${width - 260}M130 ${height - 140}h${width - 260}" stroke="#aa6d5d" stroke-width="2"/><g aria-label="标题">${title}</g>${columns}<text x="150" y="${height - 190}" fill="#655c54" font-family="${escapeXml(authorProfile.family)}" font-size="25">${escapeXml(author)}</text>${seal}`;
}

export function renderHorizontalVerse(spec, { width, height, profile, titleProfile, authorProfile, bodyLines }) {
  const lineGap = spec.fontSize * spec.leading;
  const blockHeight = Math.max(0, (bodyLines.length - 1) * lineGap);
  const y = height / 2 - blockHeight / 2;
  const anchor = spec.align === "start" ? "start" : spec.align === "end" ? "end" : "middle";
  const x = anchor === "start" ? 150 : anchor === "end" ? width - 150 : width / 2;
  const lines = bodyLines.map((line, index) => textNode(line, x, y + index * lineGap, { anchor, size: spec.fontSize, family: profile.family, fill: "#302b26" })).join("");
  const author = spec.showAuthor ? (spec.author ? `—— ${spec.author}` : "") : "";
  return `<defs>${paperTexture("horizontal", spec.background)}</defs><rect width="${width}" height="${height}" fill="url(#paper-horizontal)"/><rect x="70" y="70" width="${width - 140}" height="${height - 140}" fill="none" stroke="#a36b5d" stroke-width="2"/><text x="100" y="170" fill="#9a584c" font-family="${escapeXml(titleProfile.family)}" font-size="${spec.titleSize}" letter-spacing="8">${escapeXml(spec.title || "无题")}</text><path d="M100 205h${width - 200}" stroke="#bc9075" stroke-width="2"/>${lines}<text x="${width - 110}" y="${height - 130}" text-anchor="end" fill="#74685c" font-family="${escapeXml(authorProfile.family)}" font-size="24">${escapeXml(author)}</text>`;
}

export function renderImageAccent(spec, { width, height, profile, titleProfile, authorProfile, bodyLines }) {
  const image = spec.imageUrl ? `<image href="${escapeXml(spec.imageUrl)}" x="0" y="0" width="${width}" height="${height}" preserveAspectRatio="xMidYMid ${spec.imageFit === "contain" ? "meet" : "slice"}" opacity="${spec.imageOpacity}" filter="url(#image-filter)" transform="translate(${(50 - spec.imageX) * width / 100} ${(50 - spec.imageY) * height / 100}) scale(${spec.imageScale})"/>` : "";
  const lineGap = spec.fontSize * spec.leading;
  const bodyStart = 570 - ((bodyLines.length - 1) * lineGap) / 2;
  const anchor = spec.align === "start" ? "start" : spec.align === "end" ? "end" : "middle";
  const textX = anchor === "start" ? 150 : anchor === "end" ? width - 150 : width / 2;
  const lines = bodyLines.map((line, index) => textNode(line, textX, bodyStart + index * lineGap, { anchor, size: spec.fontSize, family: profile.family, fill: "#fffdf7" })).join("");
  const author = spec.showAuthor ? (spec.author ? `—— ${spec.author}` : "") : "";
  return `<defs><linearGradient id="image-shade" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#17120f" stop-opacity=".18"/><stop offset=".6" stop-color="#17120f" stop-opacity=".54"/><stop offset="1" stop-color="#17120f" stop-opacity=".82"/></linearGradient><pattern id="image-grain" width="18" height="18" patternUnits="userSpaceOnUse"><path d="M0 4h18M4 13h11" stroke="#fff" stroke-opacity=".08"/></pattern><filter id="image-filter"><feColorMatrix type="matrix" values="${filterMatrix(spec.imageFilter)}"/></filter></defs><rect width="${width}" height="${height}" fill="${escapeXml(spec.background)}"/>${image}<rect width="${width}" height="${height}" fill="url(#image-shade)"/><rect width="${width}" height="${height}" fill="url(#image-grain)"/><rect x="74" y="74" width="${width - 148}" height="${height - 148}" fill="none" stroke="#fffaf1" stroke-opacity=".78" stroke-width="2"/><text x="${width / 2}" y="220" text-anchor="middle" fill="#fffaf1" font-family="${escapeXml(titleProfile.family)}" font-size="${spec.titleSize}" letter-spacing="10">${escapeXml(spec.title || "无题")}</text>${lines}<text x="${width / 2}" y="${height - 180}" text-anchor="middle" fill="#fffaf1" font-family="${escapeXml(authorProfile.family)}" font-size="24">${escapeXml(author)}</text>`;
}

function textNode(value, x, y, options) {
  return `<text x="${x}" y="${y}" text-anchor="${options.anchor || "start"}" fill="${options.fill || "#302a25"}" font-family="${escapeXml(options.family)}" font-size="${options.size}" letter-spacing="${options.letterSpacing || 1}">${escapeXml(value)}</text>`;
}

function verticalText(value, x, y, spec, profile) {
  const glyphs = Array.from(String(value || ""));
  const step = spec.fontSize * 1.18;
  const tspans = glyphs.map((glyph, index) => `<tspan x="${x}" dy="${index === 0 ? 0 : step}">${escapeXml(glyph)}</tspan>`).join("");
  return `<text x="${x}" y="${y}" text-anchor="middle" fill="#302a25" font-family="${escapeXml(profile.family)}" font-size="${spec.fontSize}" letter-spacing="0">${tspans}</text>`;
}

function paperTexture(name, color) {
  return `<linearGradient id="paper-${name}" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="${escapeXml(color)}"/><stop offset="1" stop-color="#faf7ef"/></linearGradient><pattern id="grain-${name}" width="12" height="12" patternUnits="userSpaceOnUse"><path d="M0 3h12M2 9h8" stroke="#8b745f" stroke-opacity=".06"/></pattern>`;
}

function themeColors(theme) {
  if (theme === "night") return { ink: "#eee8dc", accent: "#c77f68" };
  if (theme === "ink") return { ink: "#272725", accent: "#4f5b57" };
  if (theme === "vermilion") return { ink: "#3c2926", accent: "#a94439" };
  return { ink: "#302a25", accent: "#9f5b4f" };
}

function filterMatrix(filter) {
  if (filter === "mono") return "0.33 0.33 0.33 0 0 0.33 0.33 0.33 0 0 0.33 0.33 0.33 0 0 0 0 0 1 0";
  if (filter === "warm") return "1 0 0 0.05 0 0 0.92 0 0.03 0 0 0 0.76 0.02 0 0 0 0 1 0";
  if (filter === "soft") return "0.9 0 0 0.05 0 0 0.9 0 0.05 0 0 0 0.9 0.05 0 0 0 0 1 0";
  return "1 0 0 0 0 0 1 0 0 0 0 0 1 0 0 0 0 0 1 0";
}

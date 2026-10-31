/** 本地图片导出器：等待字体、绘制 SVG，再写出 PNG 文件。 */
import { normalizeVerseCardSpec, renderCardScene, getCardFormat, LETTERFORM_PROFILES } from "./card-scene.js";

const fontFiles = {
  song: { family: 'ShijuSong', file: '汇文仿宋.ttf', format: 'truetype' },
  ancient: { family: 'ShijuAncient', file: '文悦古体仿宋.otf', format: 'opentype' },
  idea: { family: 'ShijuIdea', file: '又又意宋.ttf', format: 'truetype' },
};
const embeddedFonts = new Map();

async function embedFont(key) {
  if (!fontFiles[key]) return '';
  if (!embeddedFonts.has(key)) {
    const font = fontFiles[key];
    const pending = (async () => {
      const response = await fetch(new URL(`./assets/fonts/${font.file}`, import.meta.url));
      if (!response.ok) throw new Error(`无法读取导出字体：${font.file}`);
      const blob = await response.blob();
      const data = await new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(reader.result);
        reader.onerror = () => reject(new Error(`无法嵌入字体：${font.file}`));
        reader.readAsDataURL(blob);
      });
      return `@font-face{font-family:'${font.family}';src:url('${data}') format('${font.format}');font-weight:400;font-style:normal;}`;
    })();
    embeddedFonts.set(key, pending);
    pending.catch(() => embeddedFonts.delete(key));
  }
  return embeddedFonts.get(key);
}

export async function downloadCardPng(specInput, options = {}) {
  const spec = normalizeVerseCardSpec(specInput);
  const scale = Math.max(1, Math.min(4, Number(options.scale) || 2));
  const fontKeys = [...new Set([spec.font, spec.titleFont, ...(spec.showAuthor && spec.author ? [spec.authorFont] : [])])];
  await Promise.all(fontKeys.map(key => document.fonts.load(`42px ${LETTERFORM_PROFILES[key].family}`, spec.body + spec.title + spec.author)));
  if (document.fonts?.ready) await document.fonts.ready;
  const fontCss = (await Promise.all(fontKeys.map(embedFont))).join('\n');
  let svg = renderCardScene(spec, { aspect: options.aspect }).replace(/(<svg\b[^>]*>)/, '$1<defs><style>' + fontCss + '</style></defs>');
  // 独立 SVG 图片不读取外部资源，导出前将背景嵌入。
  const resources=[...svg.matchAll(/<image\b[^>]*\bhref="(https?:[^"<>]+)"/g)].map(match=>match[1]);
  for(const resource of new Set(resources)) {
    const response=await fetch(resource.replace(/&amp;/g,'&'));
    if(!response.ok) throw new Error('背景图片加载失败，请重试');
    const resourceBlob=await response.blob();
    const data=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result);reader.onerror=reject;reader.readAsDataURL(resourceBlob);});
    svg=svg.split(resource).join(data);
  }
  const format = getCardFormat(spec.layout, options.aspect);
  const image = await loadSvgImage(svg);
  const canvas = document.createElement("canvas");
  canvas.width = format.width * scale;
  canvas.height = format.height * scale;
  const context = canvas.getContext("2d");
  context.fillStyle = spec.background;
  context.fillRect(0, 0, canvas.width, canvas.height);
  context.drawImage(image, 0, 0, canvas.width, canvas.height);
  const blob = await new Promise((resolve, reject) => canvas.toBlob((value) => value ? resolve(value) : reject(new Error("PNG 导出失败")), "image/png"));
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = options.filename || `shiju-card-${Date.now()}.png`;
  anchor.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  return { width: canvas.width, height: canvas.height, blob };
}

function loadSvgImage(svg) {
  return new Promise((resolve, reject) => {
    const image = new Image();
    image.onload = () => resolve(image);
    image.onerror = () => reject(new Error("SVG 预览无法加载"));
    image.src = `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;
  });
}

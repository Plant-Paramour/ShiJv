/** 本地图片导出器：等待字体、绘制 SVG，再写出 PNG 文件。 */
import { normalizeVerseCardSpec, renderCardScene, getCardFormat } from "./card-scene.js";

export async function downloadCardPng(specInput, options = {}) {
  const spec = normalizeVerseCardSpec(specInput);
  const scale = Math.max(1, Math.min(4, Number(options.scale) || 2));
  if (document.fonts?.ready) await document.fonts.ready;
  const svg = renderCardScene(spec, { aspect: options.aspect });
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

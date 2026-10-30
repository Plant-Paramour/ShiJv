/** 诗词卡片的分层文档模型，供编辑器、模板和导出器共享。 */
export const CARD_TEMPLATE_CATALOG = Object.freeze([
  { id: "classic-frame", label: "框间留白", description: "竖幅留白与细框", orientation: "portrait", supportsImage: false },
  { id: "vertical-note", label: "一笺竖意", description: "逐字竖排与落款", orientation: "portrait", supportsImage: false },
  { id: "horizontal-verse", label: "横向成章", description: "横幅正文与分隔线", orientation: "landscape", supportsImage: false },
  { id: "image-accent", label: "映景成诗", description: "图片遮罩与正文", orientation: "portrait", supportsImage: true },
]);

export const CARD_THEME_CATALOG = Object.freeze([
  { id: "paper", label: "素纸", tone: "warm" },
  { id: "vermilion", label: "朱砂", tone: "warm" },
  { id: "ink", label: "墨色", tone: "neutral" },
  { id: "night", label: "夜青", tone: "cool" },
]);

export function createCardDocument(input = {}) {
  return {
    content: {
      title: input.title ?? "",
      body: input.body ?? "",
      signature: input.author ?? input.signature ?? "",
      showSignature: input.showAuthor ?? input.showSignature ?? false,
      showSeal: input.showSeal ?? true,
    },
    layout: {
      templateId: input.layout ?? "classic-frame",
      align: input.align ?? "middle",
      titleFont: input.titleFont ?? "ancient",
      bodyFont: input.font ?? "song",
      signatureFont: input.authorFont ?? "song",
      titleSize: input.titleSize ?? 34,
      bodySize: input.fontSize ?? 42,
      lineHeight: input.leading ?? 1.8,
      letterSpacing: input.letterSpacing ?? 1,
    },
    visual: {
      theme: input.theme ?? "paper",
      background: input.background ?? "#eee6d7",
      imageUrl: input.imageUrl ?? "",
      imageFit: input.imageFit ?? "cover",
      imageOpacity: input.imageOpacity ?? 0.55,
      imageX: input.imageX ?? 50,
      imageY: input.imageY ?? 50,
      imageScale: input.imageScale ?? 1,
      imageFilter: input.imageFilter ?? "none",
    },
    export: {
      aspect: input.aspect ?? "auto",
      scale: input.exportScale ?? 2,
    },
  };
}

export function flattenCardDocument(document) {
  const doc = document || createCardDocument();
  return {
    title: doc.content.title,
    body: doc.content.body,
    author: doc.content.signature,
    showAuthor: doc.content.showSignature,
    showSeal: doc.content.showSeal,
    layout: doc.layout.templateId,
    align: doc.layout.align,
    titleFont: doc.layout.titleFont,
    font: doc.layout.bodyFont,
    authorFont: doc.layout.signatureFont,
    titleSize: doc.layout.titleSize,
    fontSize: doc.layout.bodySize,
    leading: doc.layout.lineHeight,
    letterSpacing: doc.layout.letterSpacing,
    ...doc.visual,
  };
}

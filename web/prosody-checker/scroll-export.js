/** 保存当前 DOM 卷轴，保留屏幕上的排版而不重新套用卡片模板。 */
let renderer;
// 逻辑属性与物理属性同时写回会相互覆盖（例如 margin-inline:auto
// 覆盖已经计算为像素的 margin-left）。快照只保留物理属性。
function snapshotProperties(style) {
  return [...style].filter(property => !property.startsWith('--') && !/(?:^|-)(?:inline|block)(?:-|$)/.test(property));
}
function loadRenderer() {
  if (window.htmlToImage) return Promise.resolve(window.htmlToImage);
  if (!renderer) {
    renderer = new Promise((resolve, reject) => {
      const script = document.createElement('script');
      script.src = new URL('./vendor/html-to-image.js', import.meta.url).href;
      script.onload = () => resolve(window.htmlToImage);
      script.onerror = () => { script.remove(); renderer = null; reject(new Error('图片导出组件加载失败，请重试')); };
      document.head.append(script);
    });
  }
  return renderer;
}

export async function downloadScrollPng(article, title) {
  const capture = await loadRenderer();
  await document.fonts.ready;
  // 浏览器原生 SVG foreignObject 渲染 DOM 快照，不解析和重画 CSS。
  // 外层留出轴头、阴影的位置；保留实际宽度，不重新触发响应式排版。
  const rect = article.getBoundingClientRect();
  const padding = 18;
  const wrapper = document.createElement('div');
  wrapper.style.cssText = `position:fixed;left:0;top:0;pointer-events:none;z-index:-2147483647;box-sizing:content-box;width:${rect.width}px;padding:${padding}px;`;
  wrapper.style.backgroundColor = getComputedStyle(document.body).backgroundColor;
  const clone = article.cloneNode(true);
  wrapper.append(clone);
  document.body.append(wrapper);
  try {
    const sources = [article, ...article.querySelectorAll('*')];
    const targets = [clone, ...clone.querySelectorAll('*')];
    // 克隆尚未搬离现场时，冻结继承、容器单位和父级选择器的计算结果。
    sources.forEach((source, index) => {
      const computed = getComputedStyle(source);
      for (const property of snapshotProperties(computed)) targets[index].style.setProperty(property, computed.getPropertyValue(property));
    });
    // Grid 内的 auto margin 在 computedStyle 中可能读成 0，复制后会
    // 重新排版。固定已布局的盒子坐标，让标题、正文、评分和按钮保持原位。
    // 行内文字仍保留原来的文本流，避免破坏标注及标点排版。
    sources.forEach((source, index) => {
      const computed = getComputedStyle(source);
      if (index === 0 || computed.display === 'none' || computed.display === 'inline' || computed.display === 'contents') return;
      const bounds = source.getBoundingClientRect();
      let parent = source.parentElement;
      while (parent !== article && ['inline', 'contents'].includes(getComputedStyle(parent).display)) parent = parent.parentElement;
      const parentBounds = parent.getBoundingClientRect();
      const parentStyle = getComputedStyle(parent);
      const style = targets[index].style;
      style.position = 'absolute';
      style.gridArea = 'auto';
      style.alignSelf = 'start';
      style.justifySelf = 'start';
      style.left = `${bounds.left - parentBounds.left - parseFloat(parentStyle.borderLeftWidth)}px`;
      style.top = `${bounds.top - parentBounds.top - parseFloat(parentStyle.borderTopWidth)}px`;
      style.right = 'auto';
      style.bottom = 'auto';
      style.margin = '0';
      style.boxSizing = 'border-box';
      style.width = `${bounds.width}px`;
      style.height = `${bounds.height}px`;
      style.minWidth = '0';
      style.minHeight = '0';
      style.maxWidth = 'none';
      style.maxHeight = 'none';
      style.transform = 'none';
      if (source.tagName === 'BUTTON') style.whiteSpace = 'nowrap';
    });
    clone.style.setProperty('width', `${rect.width}px`);
    clone.style.setProperty('max-width', 'none');
    clone.style.setProperty('margin', '0');
    const blob = await capture.toBlob(wrapper, {
      pixelRatio: 2,
      width: Math.ceil(rect.width + padding * 2),
      height: Math.ceil(rect.height + padding * 2),
      skipAutoScale: true,
      // html-to-image 默认将字号向下取整后再减 0.1px；保留上面冻结的
      // 原始字号，不让它第二次改写。逻辑属性也不能在其内部重新引入。
      includeStyleProperties: snapshotProperties(getComputedStyle(wrapper)).filter(property => property !== 'font-size'),
    });
    if (!blob) throw new Error('图片生成失败，请重试');
    saveScrollBlob(blob, title);
  } finally { wrapper.remove(); }
}

function saveScrollBlob(blob, title) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = `${String(title || '诗词卷轴').replace(/[<>:"/\\|?*\x00-\x1f]/g, '_')}.png`;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 10000);
}

export function openScrollExportDialog(article, title, editCard) {
  const dialog = document.createElement('dialog');
  dialog.className = 'scroll-export-dialog';
  dialog.setAttribute('aria-labelledby', 'scroll-export-title');
  dialog.setAttribute('aria-describedby', 'scroll-export-description');
  dialog.innerHTML = `<div class="scroll-export-dialog__shell">
    <p class="eyebrow">诗矩 · 留存诗意</p>
    <h2 id="scroll-export-title">这一卷，如何珍藏？</h2>
    <p id="scroll-export-description">直接留存眼前的卷轴，或移步诗词卡片，细细雕琢。</p>
    <div class="scroll-export-dialog__choices">
      <button type="button" data-export-scroll><strong>导出卷轴图片</strong><span>保留当前卷轴样式，保存为 PNG 图片</span></button>
      <button type="button" data-edit-card><strong>前往诗词卡片</strong><span>继续修改排版、字体与背景</span></button>
    </div>
    <p class="scroll-export-dialog__status" role="status" aria-live="polite"></p>
    <form method="dialog"><button class="quiet-button" type="submit">暂且收起</button></form>
  </div>`;
  const exportButton = dialog.querySelector('[data-export-scroll]');
  exportButton.addEventListener('click', async () => {
    exportButton.disabled = true;
    const status = dialog.querySelector('[role=status]');
    status.textContent = '正在生成卷轴图片…';
    article.setAttribute('data-scroll-export-active', '');
    try {
      await downloadScrollPng(article, title);
      if (dialog.open) dialog.close();
    } catch (error) { status.textContent = error.message || '导出失败，请重试'; }
    finally { article.removeAttribute('data-scroll-export-active'); exportButton.disabled = false; }
  });
  dialog.querySelector('[data-edit-card]').addEventListener('click', () => { dialog.close(); editCard(); });
  dialog.addEventListener('click', event => {
    if (event.target !== dialog) return;
    const bounds = dialog.getBoundingClientRect();
    if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) dialog.close();
  });
  dialog.addEventListener('close', () => dialog.remove(), { once: true });
  document.body.append(dialog);
  dialog.showModal();
}

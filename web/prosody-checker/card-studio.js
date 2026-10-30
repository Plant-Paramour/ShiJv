import { createDefaultVerseCardSpec, normalizeVerseCardSpec, renderCardScene, getCardFormat } from "./card-scene.js";
import { downloadCardPng } from "./card-export.js";

const state = { ...loadDraft(), imageObjectUrl: "" };
const history = { past: [], future: [], applying: false };
let initialized = false;

function initCardStudio() {
  if (initialized || !document.querySelector("[data-view=card-studio]")) return;
  initialized = true;
  activateCardRoute();
  syncControlsFromState();
  bindControls();
  refreshPreview();
}

function activateCardRoute() {
  if (window.location.hash !== "#card-studio") return;
  document.querySelectorAll("[data-view]").forEach((view) => { view.hidden = view.dataset.view !== "card-studio"; });
  document.body.dataset.currentView = "card-studio";
  document.title = "诗词卡片 · 诗矩";
}

function syncControlsFromState() {
  const values = {
    "#card-title-input": state.title,
    "#card-body-input": state.body,
    "#card-author-input": state.author,
    "#card-font-input": state.font,
    "#card-title-font-input": state.titleFont,
    "#card-author-font-input": state.authorFont,
    "#card-size-input": state.fontSize,
    "#card-leading-input": state.leading,
    "#card-title-size-input": state.titleSize,
    "#card-letter-spacing-input": state.letterSpacing,
    "#card-color-input": state.background,
    "#card-image-opacity-input": state.imageOpacity,
    "#card-image-fit-input": state.imageFit,
    "#card-image-x-input": state.imageX,
    "#card-image-y-input": state.imageY,
    "#card-image-scale-input": state.imageScale,
    "#card-image-filter-input": state.imageFilter,
    "#card-theme-input": state.theme,
  };
  for (const [selector, value] of Object.entries(values)) {
    const control = document.querySelector(selector);
    if (control) control.value = String(value);
  }
  document.querySelector(`input[name="card-layout"][value="${state.layout}"]`)?.click();
  document.querySelector(`input[name="card-align"][value="${state.align}"]`)?.click();
  for (const [selector, value] of [["#card-show-author-input", state.showAuthor], ["#card-show-seal-input", state.showSeal]]) {
    const control = document.querySelector(selector);
    if (control) control.checked = Boolean(value);
  }
}

function bindControls() {
  const form = document.querySelector("#card-studio-form");
  const textMap = [["#card-title-input", "title"], ["#card-body-input", "body"], ["#card-author-input", "author"], ["#card-font-input", "font"], ["#card-title-font-input", "titleFont"], ["#card-author-font-input", "authorFont"], ["#card-color-input", "background"], ["#card-image-opacity-input", "imageOpacity"], ["#card-image-fit-input", "imageFit"], ["#card-theme-input", "theme"], ["#card-image-filter-input", "imageFilter"]];
  for (const [selector, key] of textMap) {
    const apply = (event) => updateState(key, event.target.value);
    document.querySelector(selector)?.addEventListener("input", apply);
    document.querySelector(selector)?.addEventListener("change", apply);
  }
  for (const [selector, key] of [["#card-size-input", "fontSize"], ["#card-leading-input", "leading"], ["#card-title-size-input", "titleSize"], ["#card-letter-spacing-input", "letterSpacing"], ["#card-image-x-input", "imageX"], ["#card-image-y-input", "imageY"], ["#card-image-scale-input", "imageScale"]]) document.querySelector(selector)?.addEventListener("input", (event) => { updateState(key, Number(event.target.value)); updateOutputs(); });
  document.querySelectorAll("input[name=card-layout]").forEach((input) => input.addEventListener("change", (event) => updateState("layout", event.target.value)));
  document.querySelectorAll("input[name=card-align]").forEach((input) => input.addEventListener("change", (event) => updateState("align", event.target.value)));
  for (const [selector, key] of [["#card-show-author-input", "showAuthor"], ["#card-show-seal-input", "showSeal"]]) document.querySelector(selector)?.addEventListener("change", (event) => updateState(key, event.target.checked));
  document.querySelector("#card-image-input")?.addEventListener("change", onImageSelected);
  document.querySelector("#card-export-button")?.addEventListener("click", exportPng);
  document.querySelector("#card-undo-button")?.addEventListener("click", () => restoreSnapshot(history.past, history.future));
  document.querySelector("#card-redo-button")?.addEventListener("click", () => restoreSnapshot(history.future, history.past));
  form?.addEventListener("submit", (event) => event.preventDefault());
  updateOutputs();
}

function updateState(key, value) {
  if (state[key] === value) return;
  if (!history.applying) history.past.push(snapshot());
  history.future = [];
  state[key] = value;
  saveDraft();
  refreshPreview();
  updateHistoryButtons();
}

function snapshot() {
  const copy = { ...state };
  delete copy.imageObjectUrl;
  return copy;
}

function saveDraft() {
  try { localStorage.setItem("shiju-card-draft-v12", JSON.stringify(snapshot())); } catch (_) {}
}

function loadDraft() {
  try {
    const saved = JSON.parse(localStorage.getItem("shiju-card-draft-v12") || "null");
    return saved ? { ...createDefaultVerseCardSpec(), ...saved } : createDefaultVerseCardSpec();
  } catch (_) { return createDefaultVerseCardSpec(); }
}

function restoreSnapshot(source, target) {
  const next = source.pop();
  if (!next) return;
  target.push(snapshot());
  history.applying = true;
  Object.assign(state, next);
  syncControlsFromState();
  history.applying = false;
  refreshPreview();
  updateOutputs();
  updateHistoryButtons();
}

function updateHistoryButtons() {
  document.querySelector("#card-undo-button")?.toggleAttribute("disabled", history.past.length === 0);
  document.querySelector("#card-redo-button")?.toggleAttribute("disabled", history.future.length === 0);
}

function refreshPreview() {
  const spec = normalizeVerseCardSpec(state);
  const format = getCardFormat(spec.layout);
  const preview = document.querySelector("#card-scene-preview");
  if (preview) preview.innerHTML = renderCardScene(spec);
  document.querySelector("#card-preview-dimensions")?.replaceChildren(document.createTextNode(`${format.width} × ${format.height}`));
  document.querySelector("#card-size-hint")?.replaceChildren(document.createTextNode(`${format.width} × ${format.height}`));
  document.querySelectorAll(".card-layout-option").forEach((option) => option.classList.toggle("is-selected", option.querySelector("input")?.value === spec.layout));
  document.querySelector("#card-color-output")?.replaceChildren(document.createTextNode(spec.background.toUpperCase()));
}

function updateOutputs() {
  document.querySelector("#card-size-output")?.replaceChildren(document.createTextNode(String(state.fontSize)));
  document.querySelector("#card-leading-output")?.replaceChildren(document.createTextNode(Number(state.leading).toFixed(2)));
  document.querySelector("#card-image-opacity-output")?.replaceChildren(document.createTextNode(`${Math.round(Number(state.imageOpacity) * 100)}%`));
  document.querySelector("#card-title-size-output")?.replaceChildren(document.createTextNode(String(state.titleSize)));
  document.querySelector("#card-letter-spacing-output")?.replaceChildren(document.createTextNode(String(state.letterSpacing)));
  document.querySelector("#card-image-x-output")?.replaceChildren(document.createTextNode(`${Math.round(state.imageX)}%`));
  document.querySelector("#card-image-y-output")?.replaceChildren(document.createTextNode(`${Math.round(state.imageY)}%`));
  document.querySelector("#card-image-scale-output")?.replaceChildren(document.createTextNode(`${Number(state.imageScale).toFixed(2)}×`));
  updateHistoryButtons();
}

function onImageSelected(event) {
  const file = event.target.files?.[0];
  if (!file) return;
  if (state.imageObjectUrl) URL.revokeObjectURL(state.imageObjectUrl);
  state.imageObjectUrl = URL.createObjectURL(file);
  state.imageUrl = state.imageObjectUrl;
  const label = document.querySelector("#card-image-name");
  if (label) label.textContent = file.name;
  refreshPreview();
}

async function exportPng() {
  const button = document.querySelector("#card-export-button");
  const status = document.querySelector("#card-export-status");
  button.disabled = true;
  if (status) status.textContent = "正在生成……";
  try {
    const aspect = document.querySelector("#card-export-aspect-input")?.value || "auto";
    const result = await downloadCardPng(state, { scale: 2, aspect });
    if (status) status.textContent = `已导出 ${result.width} × ${result.height}`;
  } catch (error) {
    if (status) status.textContent = error.message || "导出失败";
  } finally {
    button.disabled = false;
  }
}

window.addEventListener("shiju:route", (event) => {
  if (event.detail === "card-studio") initCardStudio();
});
window.addEventListener("hashchange", activateCardRoute);
if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", initCardStudio, { once: true });
else initCardStudio();

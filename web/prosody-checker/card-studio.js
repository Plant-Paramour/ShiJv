import { createDefaultVerseCardSpec, normalizeVerseCardSpec, renderCardScene, getCardFormat } from "./card-scene.js";
import { downloadCardPng } from "./card-export.js";
import { CARD_TEMPLATE_CATALOG } from './card-schema.js';
import { organizeCardPanels } from './card-panels.js';
import { installCardGuides } from './card-guides.js';
import { installStampEditor } from './card-stamps.js';
import { resolveCardDisplay } from './card-display-layout.js';
import { recommendCardTypography } from './card-auto-layout.js';

const state = { ...normalizeVerseCardSpec(loadDraft()), imageObjectUrl: "" };
const history = { past: [], future: [], applying: false };
let initialized = false;
let displayRevision=0,displayTimer;
function applyRecommendedTypography(record=true){
  if(record){history.past.push(snapshot());history.future=[];}
  Object.assign(state,recommendCardTypography(state));
  syncControlsFromState();saveDraft();refreshPreview();updateOutputs();
}
async function applyDisplayLayout(formName='',variantName='',recommend=false){
  const revision=++displayRevision,body=state.body,title=state.title;
  try{const arranged=await resolveCardDisplay(body,title,formName,variantName);
    if(revision!==displayRevision||body!==state.body||title!==state.title||arranged===null||arranged===body)return;
    history.past.push(snapshot());history.future=[];state.body=arranged;
    const input=document.querySelector('#card-body-input');if(input)input.value=arranged;
    if(recommend)applyRecommendedTypography(false);
    saveDraft();refreshPreview();updateHistoryButtons();
  }catch{/* 数据不可用时保留用户正文。 */}
}

function initCardStudio() {
  if (initialized || !document.querySelector("[data-view=card-studio]")) return;
  initialized = true;
  activateCardRoute();
  buildTemplatePicker();
  syncControlsFromState();
  bindControls();
  organizeCardPanels();
  const autoArrange=document.createElement('button');autoArrange.type='button';autoArrange.className='quiet-button';autoArrange.textContent='一键整理排版';
  document.querySelector('.card-text-tabs').before(autoArrange);autoArrange.addEventListener('click',()=>applyRecommendedTypography());
  if(!state.stamps)state.stamps=state.showSeal?[{id:'initial',text:state.sealText,style:state.sealStyle,image:state.sealImage,instances:[{x:.8,y:.88,size:.035}]}]:[];
  installStampEditor(state,updateState);
  document.querySelector('#card-seal-style').value=state.sealStyle;
  document.querySelector('#card-seal-text').value=state.sealText;
  document.querySelector('#card-seal-style').addEventListener('change',event=>updateState('sealStyle',event.target.value));
  document.querySelector('#card-seal-text').addEventListener('input',event=>updateState('sealText',event.target.value));
  document.querySelector('#card-seal-clear').addEventListener('click',()=>{updateState('sealImage','');document.querySelector('#card-seal-file').value='';document.querySelector('#card-seal-status').textContent='已使用内置印记。';});
  document.querySelector('#card-seal-file').addEventListener('change',async event=>{
    const file=event.target.files?.[0];if(!file)return;
    const status=document.querySelector('#card-seal-status');
    if(!['image/png','image/jpeg','image/webp'].includes(file.type)||file.size>5*1024*1024){status.textContent='请选择 5MB 以内的 PNG、JPG 或 WebP 图片。';return;}
    try{const data=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result);reader.onerror=reject;reader.readAsDataURL(file);});updateState('sealImage',data);status.textContent=`已导入：${file.name}`;}catch{status.textContent='图案读取失败，请重试。';}
  });
  installCardGuides((key,box)=>{const regions=structuredClone(state.regions||{});regions[state.layout]??={};if(box)regions[state.layout][key]=box;else delete regions[state.layout][key];updateState('regions',regions);});
  refreshPreview();
}

function buildTemplatePicker() {
  const picker = document.querySelector('.card-layout-picker');
  picker.replaceChildren();
  for (const recipe of CARD_TEMPLATE_CATALOG) {
    const label=document.createElement('label');
    label.className='card-layout-option';
    const input=document.createElement('input');
    input.type='radio'; input.name='card-layout'; input.value=recipe.id;
    const sample=document.createElement('span'); sample.className='card-template-thumbnail';
    sample.innerHTML=renderCardScene({...createDefaultVerseCardSpec(),layout:recipe.id});
    sample.setAttribute('aria-hidden','true');
    const caption=document.createElement('span');
    const title=document.createElement('strong'); title.textContent=recipe.label;
    const description=document.createElement('small'); description.textContent=recipe.description;
    caption.append(title,description); label.append(input,sample,caption); picker.append(label);
  }
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
    "#card-sequence-input": state.sequence,
    "#card-author-input": state.author,
    "#card-font-input": state.font,
    "#card-title-font-input": state.titleFont,
    "#card-author-font-input": state.authorFont,
    "#card-author-size-input": state.authorSize,
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
  document.querySelectorAll('input[name="card-layout"]').forEach(input=>{input.checked=input.value===state.layout;});
  document.querySelectorAll('input[name="card-align"]').forEach(input=>{input.checked=input.value===state.align;});
  for (const [selector, value] of [["#card-show-author-input", state.showAuthor], ["#card-show-seal-input", state.showSeal]]) {
    const control = document.querySelector(selector);
    if (control) control.checked = Boolean(value);
  }
}

function bindControls() {
  const form = document.querySelector("#card-studio-form");
  const textureLabel=document.createElement('label');textureLabel.className='field';
  const textureCaption=document.createElement('span');textureCaption.textContent='纸面纹理';
  const textureSelect=document.createElement('select');textureSelect.id='card-texture-input';
  for(const [value,name] of [['none','无纹理'],['fine','细宣'],['bark','皮纸'],['laid','帘纹纸'],['silk','古绢'],['cotton','棉纸'],['aged','旧笺'],['gold','洒金']]){const option=document.createElement('option');option.value=value;option.textContent=name;textureSelect.append(option);}
  textureSelect.value=state.texture;
  textureLabel.append(textureCaption,textureSelect);
  const strengthLabel=document.createElement('label');strengthLabel.className='field';
  const strengthCaption=document.createElement('span');strengthCaption.textContent='纹理强度';
  const strength=document.createElement('input');strength.id='card-texture-strength';strength.type='range';strength.min='0';strength.max='0.5';strength.step='0.01';strength.value=String(state.textureOpacity);
  const strengthOutput=document.createElement('output');strengthOutput.id='card-texture-strength-output';
  strengthLabel.append(strengthCaption,strength,strengthOutput);
  document.querySelector('#card-color-input').closest('label').after(textureLabel,strengthLabel);
  textureSelect.addEventListener('change',()=>updateState('texture',textureSelect.value));
  strength.addEventListener('input',()=>updateState('textureOpacity',Number(strength.value)));
  const signatureFont=document.querySelector('#card-author-font-input');
  signatureFont.closest('label').querySelector('span').textContent='落款字体';
  const sizeLabel=document.createElement('label');
  sizeLabel.className='field';
  const sizeCaption=document.createElement('span'); sizeCaption.textContent='落款字号';
  const sizeInput=document.createElement('input');
  sizeInput.type='range';sizeInput.id='card-author-size-input';sizeInput.min='12';sizeInput.max='50';sizeInput.step='1';sizeInput.value=String(state.authorSize);
  const sizeOutput=document.createElement('output');sizeOutput.id='card-author-size-output';sizeOutput.textContent=String(state.authorSize);
  sizeLabel.append(sizeCaption,sizeInput,sizeOutput);
  signatureFont.closest('.card-font-grid').after(sizeLabel);
  sizeInput.addEventListener('input',()=>{updateState('authorSize',Number(sizeInput.value));updateOutputs();});
  const fitting=document.createElement('label');
  fitting.className='card-align-fieldset';
  const fitInput=document.createElement('input');
  fitInput.type='checkbox'; fitInput.id='card-auto-fit-input'; fitInput.checked=state.autoFit===true;
  fitting.append(fitInput,document.createTextNode('自动缩小正文以适应构图'));
  document.querySelector('#card-size-input').closest('.card-range-row').after(fitting);
  fitInput.addEventListener('change',()=>updateState('autoFit',fitInput.checked));
  const textMap = [["#card-title-input", "title"], ["#card-body-input", "body"], ["#card-author-input", "author"], ["#card-font-input", "font"], ["#card-title-font-input", "titleFont"], ["#card-author-font-input", "authorFont"], ["#card-color-input", "background"], ["#card-image-opacity-input", "imageOpacity"], ["#card-image-fit-input", "imageFit"], ["#card-theme-input", "theme"], ["#card-image-filter-input", "imageFilter"]];
  textMap.push(['#card-sequence-input', 'sequence']);
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
  document.querySelector('#card-export-aspect-input')?.addEventListener('change',refreshPreview);
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
  if(key==='body'||key==='title'){++displayRevision;clearTimeout(displayTimer);displayTimer=setTimeout(()=>applyDisplayLayout(),600);}
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
  saveDraft();
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
  const sealStyle=document.querySelector('#card-seal-style');if(sealStyle)sealStyle.value=state.sealStyle;
  const sealText=document.querySelector('#card-seal-text');if(sealText)sealText.value=state.sealText;
  const sealEditor=document.querySelector('.card-seal-editor');if(sealEditor)sealEditor.hidden=!state.showSeal;
  const textureControl=document.querySelector('#card-texture-input');if(textureControl)textureControl.value=state.texture;
  const textureStrength=document.querySelector('#card-texture-strength');if(textureStrength){textureStrength.value=String(state.textureOpacity);textureStrength.closest('label').hidden=state.texture==='none';}
  const textureOutput=document.querySelector('#card-texture-strength-output');if(textureOutput)textureOutput.textContent=`${Math.round(state.textureOpacity*100)}%`;
  const spec = normalizeVerseCardSpec(state);
  const aspect=document.querySelector('#card-export-aspect-input')?.value || 'auto';
  const format = getCardFormat(spec.layout,aspect);
  const preview = document.querySelector("#card-scene-preview");
  if (preview) preview.innerHTML = renderCardScene(spec,{aspect});
  const body=preview?.querySelector('[data-effective-size]');
  const note=document.querySelector('.card-preview-note');
  if(note) note.textContent=body?.dataset.overflow==='true'?'正文超出排版区域：请减小字号、减少内容，或开启自动缩小。':Number(body?.dataset.effectiveSize)<spec.fontSize?`已自动缩小：设定 ${spec.fontSize}，实际 ${body.dataset.effectiveSize}。`:'正文按设定字号排版；图片在本地处理，默认 2 倍尺寸导出。';
  const autoFitControl=document.querySelector('#card-auto-fit-input');
  if(autoFitControl) autoFitControl.checked=spec.autoFit;
  document.querySelector("#card-preview-dimensions")?.replaceChildren(document.createTextNode(`${format.width} × ${format.height}`));
  document.querySelector("#card-size-hint")?.replaceChildren(document.createTextNode(`${format.width} × ${format.height}`));
  document.querySelectorAll(".card-layout-option").forEach((option) => option.classList.toggle("is-selected", option.querySelector("input")?.value === spec.layout));
  document.querySelector("#card-color-output")?.replaceChildren(document.createTextNode(spec.background.toUpperCase()));
}

function updateOutputs() {
  document.querySelector('#card-author-size-output')?.replaceChildren(document.createTextNode(String(state.authorSize)));
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

async function onImageSelected(event) {
  const file = event.target.files?.[0];
  if (!file) return;
  if (!['image/jpeg','image/png','image/webp'].includes(file.type)) return;
  const data = await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result);reader.onerror=reject;reader.readAsDataURL(file);});
  updateState('imageUrl',data);
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
window.addEventListener('shiju:card-content', event => {
  const content=event.detail;
  if(!content || typeof content.body!=='string')return;
  initCardStudio();
  history.past.push(snapshot());history.future=[];
  Object.assign(state,normalizeVerseCardSpec({...state,title:content.title||'',body:content.body,sequence:content.sequence||'',author:content.author||'',diagnostic:content.diagnostic||null,showAuthor:Boolean(content.author),regions:{}}));
  applyRecommendedTypography(false);
  syncControlsFromState();saveDraft();refreshPreview();updateOutputs();
  document.querySelector('#card-step-tab-0')?.click();
  clearTimeout(displayTimer);applyDisplayLayout(content.formName,content.variantName,true);
});
window.addEventListener("hashchange", activateCardRoute);
if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", initCardStudio, { once: true });
else initCardStudio();

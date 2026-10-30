function missingToneMessages(result) {
  return (result.lines||[]).flatMap((line,li)=>(line.characters||[]).flatMap((c,ci)=>{
    if(!(c.missingTone||Array.isArray(c.tones)&&c.tones.length===0))return [];
    const required=c.expected;
    const condition=required==='平'||required==='仄'?`若为${required}声，则符合此处平仄要求。`:'此处平仄不限。';
    return [`第 ${li+1} 句第 ${ci+1} 字“${c.char}”：当前韵书未查得平仄，${condition}`];
  }));
}
import { evaluateCombinations } from './polyphonic-combinations.js';
import { paintRhyme } from "./meter-book.js?v=18";
import {
  evaluateHaiku,
  evaluatePailv,
  evaluateSongci,
  evaluateTang,
  evaluateBestTangForm,
  formatMeterTemplate,
} from "./core.js?v=22";
import {
  getLexicon,
} from "./prosody-evaluation.js?v=17";
import { getDatasetJson } from "./dataset-cache.js?v=4";

let METER_FILES = [];
let meterCatalog = [];
let meterCatalogPromise = null;

const elements = {
  workspace: document.querySelector("#workspace"),
  form: document.querySelector("#checker-form"),
  controlGrid: document.querySelector(".control-grid"),
  ruleOptions: document.querySelector(".rule-options"),
  rhymeBookField: document.querySelector("#rhyme-book-field"),
  typeInputs: [...document.querySelectorAll("[name='poem-type']")],
  rhymeBook: document.querySelector("#rhyme-book"),
  tangOptions: document.querySelector("#tang-options"),
  songciOptions: document.querySelector("#songci-options"),
  cipaiLength: document.querySelector("#cipai-length"),
  cipaiSearch: document.querySelector("#cipai-search"),
  cipaiSort: document.querySelector("#cipai-sort"),
  cipaiFilterStatus: document.querySelector("#cipai-filter-status"),
  tangLength: document.querySelector("#tang-length"),
  tangForm: document.querySelector("#tang-form"),
  cipai: document.querySelector("#cipai"),
  variant: document.querySelector("#variant"),
  polyphonicInputs: [...document.querySelectorAll("[name='polyphonic-mode']")],
  polyphonicControl: document.querySelector("#polyphonic-control"),
  smartForm: document.querySelector("#smart-form"),
  smartFormControl: document.querySelector("#smart-form-control"),
  allowAoJiu: document.querySelector("#allow-aojiu"),
  aoJiuControl: document.querySelector("#aojiu-control"),
  meterTemplate: document.querySelector("#meter-template"),
  templateContent: document.querySelector("#template-content"),
  poemText: document.querySelector("#poem-text"),
  button: document.querySelector("#check-button"),
  status: document.querySelector("#form-status"),
  resultPanel: document.querySelector("#result-panel"),
  resultMeta: document.querySelector("#result-meta"),
  structureScore: document.querySelector("#structure-score"),
  tonalScore: document.querySelector("#tonal-score"),
  rhymeScore: document.querySelector("#rhyme-score"),
  issues: document.querySelector("#issues"),
  aoJiuLegend: document.querySelector("#aojiu-legend"),
  detectionSummary: document.querySelector("#detection-summary"),
  lineResults: document.querySelector("#line-results"),
  rhymeResults: document.querySelector("#rhyme-results"),
};

const meterCache = new Map();
const cipaiList = document.querySelector('#cipai-listbox');
const cipaiPicker = document.querySelector('#cipai-picker');
const cipaiToggle = document.querySelector('#cipai-picker-toggle');
let pickerOpen = false;
let pickerQuery = '';
let pickerActive = -1;
let pickerMatches = [];
const inputPage = document.querySelector('#prosody-input-page');
const inputTab = document.querySelector('#prosody-input-tab');
const resultTab = document.querySelector('#prosody-result-tab');
const previousPage = document.querySelector('#prosody-prev');
const nextPage = document.querySelector('#prosody-next');
const recheck = document.querySelector('#result-recheck');
const freshness = document.querySelector('#result-freshness');
const resultStatus = document.querySelector('#result-check-status');
let checkedText = null;
let checkedOptions = null;
let checking = false;
let activeEditor = null;
let draftRevision = 0;
let checkedRows = [];

function optionSignature() {
  return JSON.stringify([...elements.form.querySelectorAll('input, select')].filter((control) => control !== elements.cipaiSearch).map((control) =>
    [control.id || control.name, control.type === 'checkbox' || control.type === 'radio' ? control.checked : control.value]));
}

function showPage(page, { scroll = false } = {}) {
  if (page === 'result' && checkedText === null) return;
  activeEditor?.save();
  if (activeEditor) return;
  const result = page === 'result';
  elements.workspace.dataset.page = page;
  inputPage.hidden = result;
  elements.resultPanel.hidden = !result;
  inputTab.setAttribute('aria-selected', String(!result));
  resultTab.setAttribute('aria-selected', String(result));
  inputTab.tabIndex = result ? -1 : 0;
  resultTab.tabIndex = result ? 0 : -1;
  previousPage.disabled = !result;
  nextPage.disabled = result || checkedText === null;
  // Keep keyboard focus out of the page or arrow that was just hidden/disabled.
  if (document.activeElement?.closest('[hidden]') || document.activeElement?.disabled) {
    (result ? resultTab : inputTab).focus({ preventScroll: true });
  }
  if (scroll) {
    const top = elements.workspace.getBoundingClientRect().top;
    if (top < 76 || top > window.innerHeight / 2) elements.workspace.scrollIntoView({
      block: 'start', behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth',
    });
  }
  if (!result) refreshDraft();
}

function sourceClauses() {
  const text = elements.poemText.value;
  const marker = text.indexOf('[content]');
  const offset = marker < 0 ? 0 : marker + 9;
  return [...text.slice(offset).matchAll(/[^，,。.！!？?；;：:\n\r]+/gu)]
    .filter((match) => /[\u3400-\u9fff]/u.test(match[0]))
    .map((match) => ({ text: match[0], start: offset + match.index, end: offset + match.index + match[0].length }));
}

function resizePoemInput() {
  elements.poemText.style.height = 'auto';
  if (!elements.poemText.getClientRects().length) return;
  elements.poemText.style.height = `${elements.poemText.scrollHeight + 2}px`;
}

function refreshDraft() {
  resizePoemInput();
  if (checkedText === null) return;
  const dirty = checkedText !== elements.poemText.value || checkedOptions !== optionSignature();
  elements.resultPanel.classList.toggle('is-stale', dirty);
  freshness.textContent = dirty ? '正文或选项已修改，请重新检查。上次评分与说明暂不适用。' : '点击诗句即可修改，与编辑页同步。';
  // After a full-text edit, old line indexes and annotations no longer describe the draft.
  if (checkedText !== elements.poemText.value) renderDraftLines();
  else if (checkedRows.length) elements.lineResults.replaceChildren(...checkedRows);
}

function makeEditable(row, index) {
  const poem = row.querySelector('.poem-line');
  poem.tabIndex = 0;
  poem.setAttribute('role', 'button');
  poem.setAttribute('aria-label', `编辑第 ${index + 1} 句：${poem.textContent}`);
  const edit = () => {
    if (checking) return;
    activeEditor?.save();
    const clause = sourceClauses()[index];
    if (!clause) return;
    const editor = document.createElement('div');
    editor.className = 'line-editor';
    const input = document.createElement('input');
    input.type = 'text';
    input.value = clause.text.trim();
    input.setAttribute('aria-label', `编辑第 ${index + 1} 句`);
    const done = document.createElement('button');
    done.type = 'button'; done.textContent = '完成';
    const hint = document.createElement('small');
    hint.textContent = 'Enter 保存 · Esc 取消';
    const message = document.createElement('span');
    message.setAttribute('role', 'status');
    let finished = false;
    const finish = (save) => {
      if (finished) return;
      const value = input.value.trim();
      if (save && (!/[\u3400-\u9fff]/u.test(value) || /[，,。.！!？?；;：:\n\r]/u.test(value))) {
        message.textContent = '请输入一句诗词，不含分句标点；整首修改请前往编辑页。';
        input.focus(); return;
      }
      finished = true; activeEditor = null;
      row.classList.remove('is-editing'); editor.remove();
      if (save && value !== clause.text.trim()) {
        const leading = clause.text.match(/^\s*/u)[0];
        const trailing = clause.text.match(/\s*$/u)[0];
        elements.poemText.value = elements.poemText.value.slice(0, clause.start) + leading + value + trailing + elements.poemText.value.slice(clause.end);
        elements.poemText.dispatchEvent(new Event('input', { bubbles: true }));
        if (!elements.resultPanel.hidden) void runCheck({ preventDefault() {} });
      } else {
        row.classList.remove('is-editing'); editor.remove();
      }
    };
    activeEditor = { save: () => finish(true) };
    input.addEventListener('keydown', (event) => {
      if (event.isComposing) return;
      if (event.key === 'Enter') { event.preventDefault(); finish(true); elements.lineResults.querySelectorAll('.poem-line')[index]?.focus(); }
      if (event.key === 'Escape') { event.preventDefault(); finish(false); poem.focus(); }
    });
    editor.addEventListener('focusout', (event) => { if (!editor.contains(event.relatedTarget)) finish(true); });
    done.addEventListener('click', () => { finish(true); elements.lineResults.querySelectorAll('.poem-line')[index]?.focus(); });
    editor.append(input, done, hint, message); row.append(editor); row.classList.add('is-editing'); input.focus();
  };
  poem.addEventListener('click', edit);
  poem.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); edit(); }
  });
}

function renderDraftLines() {
  elements.lineResults.replaceChildren();
  sourceClauses().forEach((clause, index) => {
    const row = document.createElement('div'); row.className = 'result-line is-pending';
    appendCharacterRow(row, [...clause.text].filter((char) => /[\u3400-\u9fff]/u.test(char)).map((char) => ({ char })), 'char');
    const note = document.createElement('small'); note.className = 'pending-note'; note.textContent = '待重新检查'; row.append(note);
    makeEditable(row, index); elements.lineResults.append(row);
  });
}

inputTab.addEventListener('click', () => showPage('input'));
resultTab.addEventListener('click', () => showPage('result'));
previousPage.addEventListener('click', () => showPage('input', { scroll: true }));
nextPage.addEventListener('click', () => showPage('result', { scroll: true }));
document.querySelector('#result-edit-all').addEventListener('click', () => { showPage('input', { scroll: true }); elements.poemText.focus({ preventScroll: true }); refreshDraft(); });
for (const tab of [inputTab, resultTab]) tab.addEventListener('keydown', (event) => {
  if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
  event.preventDefault();
  const result = event.key === 'End' || event.key === 'ArrowRight';
  showPage(result ? 'result' : 'input');
  (result && checkedText !== null ? resultTab : inputTab).focus();
});
elements.poemText.addEventListener('input', () => { draftRevision++; refreshDraft(); });
elements.form.addEventListener('change', (event) => { if (event.target === elements.cipaiSearch) return; draftRevision++; refreshDraft(); });

async function getMeter(filename) {
  if (!meterCache.has(filename)) meterCache.set(filename, getDatasetJson("meter", filename));
  return meterCache.get(filename);
}

function selectedType() {
  return elements.typeInputs.find((input) => input.checked).value;
}

function evaluationOptions() {
  const polyphonicMode = elements.polyphonicInputs.find((input) => input.checked).value;
  return {
    polyphonicMode,
    strictPolyphonic: polyphonicMode === "strict",
    allowAoJiu: elements.allowAoJiu.checked,
  };
}

async function loadMeterCatalog() {
  const index = await getDatasetJson("meter", "index.json");
  meterCatalog = index.map((meter) => ({
    filename: meter.file,
    name: meter.name,
    searchText: [meter.name, ...(meter.aliases ?? [])].join(" ").toLocaleLowerCase(),
    lengthCategory: meter.length_category,
    length: meter.char_count,
  }));
  METER_FILES = meterCatalog.map((meter) => meter.filename);
  renderMeterOptions();
  await updateVariants();
}

function ensureMeterCatalog() {
  if (!meterCatalogPromise) {
    meterCatalogPromise = loadMeterCatalog().catch((error) => {
      meterCatalogPromise = null;
      throw error;
    });
  }
  return meterCatalogPromise;
}

function renderMeterOptions() {
  const category = elements.cipaiLength.value;
  const sort = elements.cipaiSort.value;
  const current = elements.cipai.value;
  const filtered = meterCatalog.filter((meter) =>
    category === "all" || meter.lengthCategory === category);
  filtered.sort((left, right) => {
    if (sort === "name-asc" || sort === "name-desc") {
      const result = left.name.localeCompare(right.name, "zh-Hans");
      return sort === "name-asc" ? result : -result;
    }
    const result = left.length - right.length || left.name.localeCompare(right.name, "zh-Hans");
    return sort === "length-asc" ? result : -result;
  });
  elements.cipai.replaceChildren();
  filtered.forEach((meter) => elements.cipai.add(new Option(`${meter.name}　${meter.length}字`, meter.filename)));
  if (filtered.some((meter) => meter.filename === current)) elements.cipai.value = current;
  else if (filtered.length) elements.cipai.selectedIndex = 0;
  renderCipaiList();
  if (!pickerOpen) syncPickerLabel();
}

function syncPickerLabel() {
  elements.cipaiSearch.value = elements.cipai.selectedOptions[0]?.text || '';
}

function renderCipaiList() {
  const query = pickerQuery.trim().toLocaleLowerCase();
  pickerMatches = [...elements.cipai.options].filter((option) => {
    const meter = meterCatalog.find((item) => item.filename === option.value);
    return !query || meter?.searchText.includes(query);
  });
  cipaiList.replaceChildren();
  pickerActive = -1;
  elements.cipaiSearch.removeAttribute('aria-activedescendant');
  elements.cipaiFilterStatus.textContent = pickerMatches.length ? `共 ${pickerMatches.length} 个词牌` : '没有符合条件的词牌';
  pickerMatches.forEach((option, index) => {
    const item = document.createElement('div');
    item.id = `cipai-choice-${index}`;
    item.className = 'cipai-choice';
    item.setAttribute('role', 'option');
    item.setAttribute('aria-selected', String(option.value === elements.cipai.value));
    item.textContent = option.text;
    item.addEventListener('mousedown', (event) => event.preventDefault());
    item.addEventListener('click', () => chooseCipai(index));
    cipaiList.append(item);
  });
  if (!pickerMatches.length) {
    const empty = document.createElement('p'); empty.className = 'cipai-empty';
    empty.textContent = '没有匹配的词牌，请换个关键词。'; cipaiList.append(empty);
  }
}

function openCipaiPicker() {
  if (pickerOpen) return;
  pickerOpen = true; pickerQuery = '';
  elements.cipaiSearch.value = '';
  cipaiList.hidden = false;
  elements.cipaiSearch.setAttribute('aria-expanded', 'true');
  cipaiToggle.setAttribute('aria-label', '收起词牌列表');
  renderCipaiList();
}

function closeCipaiPicker() {
  pickerOpen = false; pickerQuery = ''; pickerActive = -1;
  cipaiList.hidden = true;
  elements.cipaiSearch.setAttribute('aria-expanded', 'false');
  elements.cipaiSearch.removeAttribute('aria-activedescendant');
  cipaiToggle.setAttribute('aria-label', '展开词牌列表');
  syncPickerLabel();
  elements.cipaiFilterStatus.textContent = elements.cipai.options.length ? `共 ${elements.cipai.options.length} 个词牌` : '没有符合条件的词牌';
}

function chooseCipai(index) {
  const option = pickerMatches[index];
  if (!option) return;
  elements.cipai.value = option.value;
  closeCipaiPicker();
  elements.cipai.dispatchEvent(new Event('change', { bubbles: true }));
  elements.cipaiSearch.focus({ preventScroll: true });
  // focus may open the picker when selection follows a pointer click.
  closeCipaiPicker();
}

elements.cipaiSearch.addEventListener('focus', openCipaiPicker);
elements.cipaiSearch.addEventListener('click', openCipaiPicker);
elements.cipaiSearch.addEventListener('input', () => {
  if (!pickerOpen) openCipaiPicker();
  pickerQuery = elements.cipaiSearch.value; renderCipaiList();
});
elements.cipaiSearch.addEventListener('keydown', (event) => {
  if (event.isComposing) return;
  if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); closeCipaiPicker(); }
  if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
    event.preventDefault(); openCipaiPicker();
    if (!pickerMatches.length) return;
    pickerActive = pickerActive < 0
      ? (event.key === 'ArrowDown' ? 0 : pickerMatches.length - 1)
      : (pickerActive + (event.key === 'ArrowDown' ? 1 : -1) + pickerMatches.length) % pickerMatches.length;
    [...cipaiList.children].forEach((item, index) => item.classList.toggle('is-active', index === pickerActive));
    elements.cipaiSearch.setAttribute('aria-activedescendant', `cipai-choice-${pickerActive}`);
    cipaiList.children[pickerActive]?.scrollIntoView({ block: 'nearest' });
  }
  if (event.key === 'Enter') {
    event.preventDefault();
    if (pickerOpen && pickerMatches.length) chooseCipai(Math.max(0, pickerActive));
    else openCipaiPicker();
  }
});
cipaiToggle.addEventListener('click', () => {
  if (pickerOpen) closeCipaiPicker();
  else { elements.cipaiSearch.focus(); openCipaiPicker(); }
});
cipaiPicker.addEventListener('focusout', (event) => {
  if (!cipaiPicker.contains(event.relatedTarget)) closeCipaiPicker();
});
document.addEventListener('pointerdown', (event) => {
  if (pickerOpen && !cipaiPicker.contains(event.target)) closeCipaiPicker();
});

async function updateVariants() {
  const selectedFile = elements.cipai.value;
  if (!selectedFile) {
    elements.variant.replaceChildren();
    elements.templateContent.replaceChildren();
    return;
  }
  const meter = await getMeter(selectedFile);
  if (elements.cipai.value !== selectedFile) return;
  elements.variant.replaceChildren();
  for (const variant of meter.variants ?? []) {
    elements.variant.add(new Option(variant.name, variant.name));
  }
  if (meter.default_variant) elements.variant.value = meter.default_variant;
  renderTemplate();
}

function currentVariant() {
  const meterPromise = meterCache.get(elements.cipai.value);
  return Promise.resolve(meterPromise).then((meter) =>
    meter.variants.find((variant) => variant.name === elements.variant.value));
}

async function renderTemplate() {
  if (selectedType() !== "songci") return;
  const variant = await currentVariant();
  elements.templateContent.replaceChildren();
  if (!variant) return;
  for (const stanza of formatMeterTemplate(variant)) {
    const stanzaElement = document.createElement("div");
    stanzaElement.className = "template-stanza";
    for (const line of stanza.lines) {
      const lineElement = document.createElement("span");
      lineElement.className = "template-line";
      lineElement.textContent = line;
      stanzaElement.append(lineElement);
    }
    elements.templateContent.append(stanzaElement);
  }
}

async function updateTypeView() {
  if (document.body.dataset.currentView !== "prosody") return;
  const type = selectedType();
  elements.form.classList.toggle("is-songci", type === "songci");
  elements.controlGrid.dataset.type = type;
  elements.tangOptions.hidden = type !== "tang";
  elements.songciOptions.hidden = type !== "songci";
  elements.meterTemplate.hidden = type !== "songci";
  const aoJiuDisabled = type === "songci";
  elements.allowAoJiu.disabled = aoJiuDisabled;
  elements.allowAoJiu.checked = aoJiuDisabled ? false : true;
  elements.aoJiuControl.hidden = aoJiuDisabled;
  elements.aoJiuControl.classList.toggle("disabled", aoJiuDisabled);
  const canSmartSelect = type === "tang" || type === "pailv";
  elements.smartForm.disabled = !canSmartSelect;
  elements.smartFormControl.hidden = !canSmartSelect;
  elements.smartFormControl.classList.toggle("disabled", !canSmartSelect);
  elements.status.textContent = "";
  if (type === "songci") {
    elements.status.textContent = "正在加载词牌模板……";
    try {
      await ensureMeterCatalog();
      elements.status.textContent = "";
    } catch (error) {
      elements.status.textContent = error instanceof Error ? error.message : "词牌模板加载失败";
    }
  }
}

function appendCharacterRow(container, characters, property) {
  container.style.setProperty('--line-length', Math.max(1, characters.length));
  const row = document.createElement("div");
  row.className = property === "char" ? "poem-line" : "tone-line";
  for (const item of characters) {
    const span = document.createElement(item.error || item.polyphonic || item.conditionalError || item.roles?.length ? "mark" : "span");
    span.className = property;
    if (item.polyphonic) span.classList.add("polyphonic");
    if (item.roles?.includes("ao")) span.classList.add("ao");
    if (item.roles?.includes("rescue")) span.classList.add("rescue");
    if (item.error && item.conditionalError !== "affected") span.classList.add("error");
    if (item.conditionalError) span.classList.add("conditional-" + item.conditionalError);
    if (property === "tone" && item.tone.includes("/")) span.classList.add("multi-label");
    span.textContent = property === "char" ? item.char : (item.missingTone || item.tones?.length === 0 ? "缺" : item.polyphonic ? "多" : item.tone);
    if(property==='tone'&&item.rhymeGroup&&!item.error&&!item.conditionalError){span.classList.add('meter-book-rhyme');paintRhyme(span,item.rhymeGroup,{avoidWarning:true});span.title='第 '+item.rhymeGroup+' 组韵';const badge=document.createElement('sup');badge.className='meter-book-rhyme-number';badge.textContent=String(item.rhymeGroup);badge.setAttribute('aria-label','第 '+item.rhymeGroup+' 组韵');span.append(badge);}
    if(item.repeatGroup){span.classList.add('meter-book-repeat');span.style.borderStyle=['solid','double','dashed','dotted'][(item.repeatGroup-1)%4];span.title='叠 '+item.repeatGroup;}
    const hints = [];
    if (item.roles?.includes("ao")) hints.push("拗字");
    if (item.roles?.includes("rescue")) hints.push("救字");
    if (item.expected) {
      hints.push((item.roles?.length ? "标准句式此处要求：" : "此处要求：") + item.expected);
    }
    if (item.decisionReason) hints.push("自动判断：" + item.decisionReason);
    if (hints.length) span.title = hints.join("；");
    row.append(span);
  }
  container.append(row);
}

function appendSummaryGroup(container, title, items, emptyText, className) {
  const group = document.createElement("details");
  group.className = "detection-group " + className;
  const heading = document.createElement("summary");
  heading.textContent = title;
  group.append(heading);

  if (!items.length) {
    const empty = document.createElement("p");
    empty.className = "summary-empty";
    empty.textContent = emptyText;
    group.append(empty);
  } else {
    const list = document.createElement("ul");
    for (const item of items) {
      const row = document.createElement("li");
      row.textContent = item;
      list.append(row);
    }
    group.append(list);
  }
  container.append(group);
}

function polyphonicSummaryMessages(result) {
  const decisions=result.polyphonicDecisions||[];
  const messages=[];
  for(const [lineIndex,line] of (result.lines||[]).entries())for(const [charIndex,character] of (line.characters||[]).entries()){
    if(!character.polyphonic)continue;
    if((result.polyphonicCombinations||[]).some(message=>message.startsWith(`第 ${lineIndex+1} 句：`)))continue;
    const decision=decisions.find(item=>item.lineIndex===lineIndex&&item.charIndex===charIndex);
    const required=character.expected;
    const prefix=`第 ${lineIndex+1} 句第 ${charIndex+1} 字“${character.char}”：`;
    if(required==='平'||required==='仄')messages.push(prefix+`若为${required}声，则符合此处平仄要求。`);
    else if(decision?.message?.includes('若为'))messages.push(decision.message);
    else messages.push(prefix+'此处平仄不拘，请根据语境自行核对读音。');
  }
  messages.push(...(result.polyphonicCombinations||[]));
  return messages.map(message=>String(message).replaceAll('平/仄','多'));
}

function renderDetectionSummary(result) {
  const aoJiu = result.aoJiu;
  const polyphonicMessages = polyphonicSummaryMessages(result);
  const hasPolyphonic = polyphonicMessages.length > 0;
  const missingMessages = missingToneMessages(result);
  const violations = (result.violations ?? []).map((item) => item.message ||
    "第 " + (item.lineIndex + 1) + " 句第 " + (item.charIndex + 1)
      + " 字“" + item.char + "”：“" + item.rule + "”，应" + item.required + "。"
  );
  elements.detectionSummary.replaceChildren();
  const rhymeMessages = (result.rhymeViolations ?? []).map((item) => item.message ||
    "第 " + (item.lineIndex + 1) + " 句第 " + (item.charIndex + 1)
      + " 字“" + item.char + "”：" + (item.actualParts?.length ? "属于“" + item.actualParts.map(part => part + "韵部").join("、") + "”，" : "当前韵书未查得所属韵部，") + "未与本组主韵部“" + item.required + "”相合。"
  );
  const rhymeViolations=rhymeMessages.filter(message=>!message.includes('当前韵书未查得'));
  const missingRhyme=rhymeMessages.filter(message=>message.includes('当前韵书未查得'));
  const missing=[...missingMessages,...missingRhyme];
  const hasDetails = Boolean(aoJiu || hasPolyphonic || missing.length || violations.length || rhymeViolations.length);
  elements.detectionSummary.hidden = !hasDetails;
  elements.aoJiuLegend.hidden = !aoJiu?.enabled;
  if (!hasDetails) return;
  elements.detectionSummary.classList.toggle(
    "has-two-groups",
    Boolean(aoJiu && hasPolyphonic),
  );

  if (hasPolyphonic) {
    appendSummaryGroup(
      elements.detectionSummary,
      "多音字判定",
      polyphonicMessages,
      "未发现需要单独判断的多音字。",
      "polyphonic-summary",
    );
  }

  if (aoJiu) {
    appendSummaryGroup(
      elements.detectionSummary,
      "拗救判定",
      aoJiu.details.map((item) => item.message),
      aoJiu.enabled ? "未识别到需要拗救的句式。" : "未启用拗救。",
      "aojiu-summary",
    );
  }

  const detectionGroups = [...elements.detectionSummary.querySelectorAll(":scope > details")];
  detectionGroups.forEach((group) => {
    group.open = false;
    group.addEventListener("toggle", () => {
      for (const other of detectionGroups) {
        if (other !== group && other.open !== group.open) other.open = group.open;
      }
    });
  });

  if(missing.length){
    const section=document.createElement('div');section.className='standard-summary';
    const heading=document.createElement('h4');heading.textContent='韵书缺字说明';
    const list=document.createElement('ul');list.className='standard-error-list';
    missing.forEach(message=>{const li=document.createElement('li');li.textContent=message;list.append(li);});
    section.append(heading,list);elements.detectionSummary.append(section);
  }
  const standard = document.createElement("div");
  standard.className = "standard-summary";
  const standardHeading = document.createElement("h4");
  standardHeading.textContent = "平仄与押韵错误";
  standard.append(standardHeading);
  const allErrors = [...violations, ...rhymeViolations];
  if (allErrors.length) {
    const list = document.createElement("ul");
    list.className = "standard-error-list";
    for (const message of allErrors) {
      const item = document.createElement("li"); item.textContent = message; list.append(item);
    }
    standard.append(list);
  } else {
    const empty = document.createElement("p");
    empty.className = "is-clear";
    empty.textContent = "未发现平仄或押韵错误。";
    standard.append(empty);
  }
  elements.detectionSummary.append(standard);
}

function charCountLabel(charCount) {
  return { 5: "五", 7: "七" }[Number(charCount)] ?? String(charCount);
}

function resultMeta(result, type, variant) {
  const rhymeBookName = elements.rhymeBook.selectedOptions[0]?.text || "未标注韵书";
  const smartMeta = result.stats.autoMatched
    ? " · 智能选式 · 综合 " + result.stats.comprehensiveScore.toFixed(2).replace(/\.00$/, "")
    : "";
  if (type === "songci") {
    return elements.cipai.selectedOptions[0].text + " · " + variant.name
      + " · " + (variant.rhyme_type || "未标注韵式") + " · " + rhymeBookName + smartMeta;
  }
  if (type === "tang") {
    const form = elements.tangForm.value === "jueju" ? "绝句" : "律诗";
    const firstRhyme = result.stats.firstLineEndTone === result.stats.rhymeTone ? "首句押韵" : "";
    return charCountLabel(result.stats.charCount) + "言" + form + " · "
      + (result.stats.globalBase ?? "未定") + "起" + firstRhyme + " · "
      + result.stats.rhymeTone + "韵 · " + rhymeBookName + smartMeta;
  }
  if (type === "pailv") {
    return charCountLabel(result.stats.charCount) + "言排律 · "
      + (result.stats.globalBase ?? "未定") + "起 · "
      + result.stats.rhymeTone + "韵 · " + rhymeBookName + smartMeta;
  }
  return "五七五俳句 · " + rhymeBookName + smartMeta;
}

function renderResult(result, type, variant) {
  elements.structureScore.textContent = result.structureScore.toFixed(2).replace(/\.00$/, "");
  elements.tonalScore.textContent = result.tonalScore.toFixed(2).replace(/\.00$/, "");
  elements.rhymeScore.textContent = result.rhymeScore.toFixed(2).replace(/\.00$/, "");
  elements.resultMeta.textContent = resultMeta(result, type, variant);

  elements.issues.replaceChildren();
  elements.issues.hidden = result.issues.length === 0;
  if (result.issues.length) {
    for (const issue of result.issues) {
      const div = document.createElement("div");
      div.textContent = issue;
      elements.issues.append(div);
    }
  }

  renderDetectionSummary(result);

  elements.lineResults.replaceChildren();
  elements.lineResults.dataset.poemType=type;
  elements.lineResults.parentElement.querySelector('.prosody-rhyme-legend')?.remove();
  const rhymeLegend=document.createElement('div');rhymeLegend.className='meter-book-legend prosody-rhyme-legend';
  rhymeLegend.append(document.createTextNode(type==='songci'?'标准模板 · 中：可平可仄 · 韵脚：':'韵脚标记（仅平仄）：'));
  const legendGroups=type==='songci'&&variant?[...new Set(Object.entries(variant).filter(([key])=>/^stanza\d+$/.test(key)).flatMap(([,stanza])=>Object.keys(stanza).filter(key=>/^rhyme_\d+_positions$/.test(key)).map(key=>Number(key.match(/\d+/)[0]))))].sort((a,b)=>a-b):(result.rhymeGroups||[]).map((_,index)=>index+1);
  legendGroups.forEach(group=>{
    const index=group-1;
    const label=document.createElement('span');label.className='meter-book-rhyme';label.textContent='韵 '+(index+1);paintRhyme(label,index+1,{avoidWarning:true});const badge=document.createElement('sup');badge.className='meter-book-rhyme-number';badge.textContent=String(index+1);badge.setAttribute('aria-label','第 '+(index+1)+' 组韵');label.append(badge);rhymeLegend.append(label);
  });
  const errorLegend=document.createElement('span');errorLegend.className='prosody-legend-pair prosody-legend-pair--error';
  const errorLabel=document.createElement('mark');errorLabel.className='prosody-legend-error';errorLabel.textContent='错误';
  const errorRequirement=document.createElement('span');errorRequirement.className='prosody-legend-outline prosody-legend-outline--error';errorRequirement.textContent='格律要求';errorLegend.append(errorLabel,errorRequirement);rhymeLegend.append(errorLegend);
  const polyphonicLegend=document.createElement('span');polyphonicLegend.className='prosody-legend-pair';
  const polyphonicLabel=document.createElement('mark');polyphonicLabel.className='prosody-legend-polyphonic';polyphonicLabel.textContent='多音字';
  const polyphonicRequirement=document.createElement('span');polyphonicRequirement.className='prosody-legend-outline';polyphonicRequirement.textContent='自行核对';polyphonicLegend.append(polyphonicLabel,polyphonicRequirement);rhymeLegend.append(polyphonicLegend);
  (variant?.repetition_groups||[]).forEach((group,index)=>{const label=document.createElement('span');label.className='meter-book-repeat-key';label.textContent='叠 '+(index+1);label.style.borderStyle=['solid','double','dashed','dotted'][index%4];rhymeLegend.append(label);});
  elements.lineResults.before(rhymeLegend);
  const errorPositions = new Set([
    ...(result.violations ?? []),
    ...(result.rhymeViolations ?? []),
  ].map((item) => `${item.lineIndex}:${item.charIndex}`));
  result.lines.forEach((line, lineIndex) => {
    const row = document.createElement("div");
    row.className = "result-line";
    let stanzaNumber=1,stanzaLine=lineIndex+1,offset=0;
    if(variant)for(const [key,stanza] of Object.entries(variant).filter(([key])=>/^stanza\d+$/.test(key))){
      if(lineIndex<offset+stanza.lines.length){stanzaNumber=Number(key.slice(6));stanzaLine=lineIndex-offset+1;break;}offset+=stanza.lines.length;
    }
    const characters=line.characters.map((character,charIndex)=>{
      const rhymeGroup=(result.rhymeGroups||[]).findIndex(group=>(group.endings||[]).some(ending=>ending.lineIndex===lineIndex));
      const repeatGroup=(variant?.repetition_groups||[]).findIndex(group=>(group.positions||[]).some(p=>Number(p.stanza)===stanzaNumber&&(stanzaLine>p.start[0]||stanzaLine===p.start[0]&&charIndex+1>=p.start[1])&&(stanzaLine<p.end[0]||stanzaLine===p.end[0]&&charIndex+1<=p.end[1])));
      return {...character,error:character.error||errorPositions.has(`${lineIndex}:${charIndex}`),rhymeGroup:charIndex===line.characters.length-1?rhymeGroup+1:0,repeatGroup:repeatGroup+1};
    });
    appendCharacterRow(row,characters,"char");
    if(type==='songci'&&variant){
      const stanza=variant['stanza'+stanzaNumber];
      const pattern=stanza?.lines?.[stanzaLine-1];
      if(pattern!=null){
        const symbols=[...pattern.replace(/[、/\s]/gu,'')];
        const rhymeGroup=Object.keys(stanza).filter(key=>/^rhyme_\d+_positions$/.test(key)).find(key=>stanza[key].includes(stanzaLine));
        const group=rhymeGroup?Number(rhymeGroup.match(/\d+/)[0]):0;
        const template=symbols.map((tone,index)=>({tone,char:'',rhymeGroup:index===symbols.length-1?group:0,repeatGroup:characters[index]?.repeatGroup||0}));
        appendCharacterRow(row,template,"tone");
        row.style.setProperty('--line-length',Math.max(characters.length,template.length,1));
        row.querySelector('.tone-line').setAttribute('aria-label','标准格律模板');
      }else appendCharacterRow(row,characters,"tone");
    }else appendCharacterRow(row,characters,"tone");
    makeEditable(row, lineIndex);
    elements.lineResults.append(row);
  });

  if(type==='songci'&&variant) {
    const rows=[...elements.lineResults.children];
    const layoutRevision=draftRevision;
    getDatasetJson('meter','display-layouts.json').then(layouts=>{
      if(elements.lineResults.firstElementChild!==rows[0]||draftRevision!==layoutRevision)return;
      const layout=Object.values(layouts.items||{}).map(item=>item[String(variant.source_id)]).find(Boolean);
      let offset=0;
      const fragment=document.createDocumentFragment();
      for(const [key,stanza] of Object.entries(variant).filter(([key])=>/^stanza\d+$/.test(key))) {
        const sourceRows=layout?.stanzas?.[key] || stanza.lines.map((_,i)=>({sentences:[{line:i+1}]}));
        for(const sourceRow of sourceRows) {
          const group=document.createElement("div");group.className="prosody-sentence-group";
          sourceRow.sentences.forEach((sentence,column)=>{
            const row=rows[offset+sentence.line-1];if(!row)return;
            group.append(row);
          });fragment.append(group);
        }
        offset+=stanza.lines.length;
      }
      rows.filter(row=>!fragment.contains(row)).forEach(row=>fragment.append(row));
      elements.lineResults.replaceChildren(fragment);checkedRows=[...elements.lineResults.children];
    }).catch(()=>{});
  }

  elements.rhymeResults.replaceChildren();
  result.rhymeGroups.forEach((group, index) => {
    const line = document.createElement("div");
    line.className = "rhyme-group";
    const endings = group.endings.map((ending) => ending.char).join("、") || "无";
    const label = document.createElement("span");
    label.textContent = "第 " + (index + 1) + " 韵组：" + endings + "；主韵部 ";
    const part = document.createElement("strong");
    part.textContent = group.dominantPart ?? "未识别";
    line.append(label, part);
    elements.rhymeResults.append(line);
  });

  try {localStorage.setItem('shiju-prosody-result',JSON.stringify({result,type,variant,text:elements.poemText.value,controls:[...elements.form.querySelectorAll('input,select,textarea')].filter(el=>el.id||el.name).map(el=>({id:el.id,name:el.name,value:el.value,checked:el.checked}))}));}catch{}
  checkedText = elements.poemText.value;
  checkedRows = [...elements.lineResults.children];
  checkedOptions = optionSignature();
  elements.resultPanel.classList.remove('is-stale');
  freshness.textContent = '点击诗句即可修改，与编辑页同步。';
  resultTab.disabled = false;
  elements.workspace.classList.add("has-result");
  showPage('result', { scroll: true });
}

async function runCheck(event) {
  event.preventDefault();
  activeEditor?.save();
  if (activeEditor || checking) return;
  const revision = draftRevision;
  elements.status.textContent = "";
  const text = elements.poemText.value.trim();
  if (!text) {
    elements.status.textContent = "请先粘贴诗词正文";
    elements.poemText.focus();
    return;
  }

  elements.button.disabled = true;
  checking = true;
  recheck.disabled = true;
  elements.workspace.setAttribute('aria-busy', 'true');
  resultStatus.textContent = '正在检查……';
  if (!elements.resultPanel.hidden) freshness.textContent = '正在检查修改后的诗句……';
  elements.button.textContent = "检查中";
  try {
    let type = selectedType();
    const options = evaluationOptions();
    let result;
    let variant = null;
    if ((type === "tang" || type === "pailv") && elements.smartForm.checked) {
      const rhymeBookId = elements.rhymeBook.value;
      const lexicon = await getLexicon(rhymeBookId);
      const best = evaluateBestTangForm(text, lexicon, options, {
        type,
        charCount: elements.tangLength.value,
        form: elements.tangForm.value,
      });
      result = best.result;
      result.stats = { ...(result.stats || {}), comprehensiveScore: best.score, autoMatched: true };
      type = best.type;
      elements.typeInputs.find((input) => input.value === type).checked = true;
      if (type === "tang") {
        elements.tangLength.value = String(best.charCount);
        elements.tangForm.value = best.form;
      }
      updateTypeView();
    } else if (type === "songci") {
      const lexicon = await getLexicon(elements.rhymeBook.value);
      variant = await currentVariant();
      result = evaluateSongci(text, variant, lexicon, options);
    } else if (type === "tang") {
      const lexicon = await getLexicon(elements.rhymeBook.value);
      result = evaluateTang(text, {
        charCount: Number(elements.tangLength.value),
        form: elements.tangForm.value,
        ...options,
      }, lexicon);
    } else if (type === "pailv") {
      const lexicon = await getLexicon(elements.rhymeBook.value);
      result = evaluatePailv(text, lexicon, options);
    } else {
      const lexicon = await getLexicon(elements.rhymeBook.value);
      result = evaluateHaiku(text, lexicon, options);
    }
    result.polyphonicCombinations=await evaluateCombinations(result,type,variant,await getLexicon(elements.rhymeBook.value),options);
    if (revision !== draftRevision) throw new Error('正文或选项已改变，请重新检查。');
    renderResult(result, type, variant);
  } catch (error) {
    elements.status.textContent = error instanceof Error ? error.message : "检查失败";
    resultStatus.textContent = elements.status.textContent;
  } finally {
    checking = false;
    recheck.disabled = false;
    elements.workspace.removeAttribute('aria-busy');
    if (!elements.status.textContent) resultStatus.textContent = '';
    elements.button.disabled = false;
    elements.button.textContent = "开始检查";
  }
}

elements.typeInputs.forEach((input) => input.addEventListener("change", updateTypeView));
window.addEventListener("shiju:route", (event) => { if (event.detail === "prosody") { updateTypeView(); requestAnimationFrame(resizePoemInput); } });
window.addEventListener('resize', resizePoemInput);
requestAnimationFrame(resizePoemInput);
elements.cipai.addEventListener("change", updateVariants);
elements.cipaiLength.addEventListener("change", async () => { renderMeterOptions(); await updateVariants(); });
elements.cipaiSort.addEventListener("change", async () => { renderMeterOptions(); await updateVariants(); });
elements.variant.addEventListener("change", renderTemplate);
elements.form.addEventListener("submit", runCheck);

(async()=>{
  try{
    const saved=JSON.parse(localStorage.getItem('shiju-prosody-result'));
    if(!saved?.result?.lines||!saved.type){await updateTypeView();return;}
    await updateTypeView();
    for(const control of saved.controls||[]){
      const el=control.id?document.getElementById(control.id):[...elements.form.elements].find(el=>el.name===control.name&&el.value===control.value);
      if(el){el.value=control.value;if(typeof control.checked==='boolean')el.checked=control.checked;}
    }
    elements.poemText.value=saved.text||'';
    await updateTypeView();
    for(const control of saved.controls||[]){
      const el=control.id?document.getElementById(control.id):null;
      if(el?.tagName==='SELECT')el.value=control.value;
    }
    if(saved.type==='songci')await updateVariants();
    for(const control of saved.controls||[])if(control.id==='variant')elements.variant.value=control.value;
    renderResult(saved.result,saved.type,saved.variant);
    requestAnimationFrame(resizePoemInput);
  }catch{}
})();

import { paintRhyme } from "./meter-book.js?v=19";
import { getDatasetJson } from "./dataset-cache.js?v=4";

function missingToneMessages(result) {
  return (result.lines||[]).flatMap((line,li)=>(line.characters||[]).flatMap((c,ci)=>{
    if(!(c.missingTone||Array.isArray(c.tones)&&c.tones.length===0))return [];
    const required=c.expected;
    const condition=required==='平'||required==='仄'?`若为${required}声，则符合此处平仄要求。`:'此处平仄不限。';
    return [`第 ${li+1} 句第 ${ci+1} 字“${c.char}”：当前韵书未查得平仄，${condition}`];
  }));
}
export function appendCharacterRow(container, characters, property) {
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
    if(item.repeatGroup){
      span.classList.add('meter-book-repeat');
      span.style.borderStyle=['solid','double','dashed','dotted'][(item.repeatGroup-1)%4];
      const previous = characters[characters.indexOf(item) - 1];
      const next = characters[characters.indexOf(item) + 1];
      if (!previous || previous.repeatGroup !== item.repeatGroup) span.classList.add('is-repeat-start');
      if (!next || next.repeatGroup !== item.repeatGroup) span.classList.add('is-repeat-end');
      span.title='叠 '+item.repeatGroup;
      if (property === 'char' && (!previous || previous.repeatGroup !== item.repeatGroup)) {
        const badge = document.createElement('sup');
        badge.className = 'meter-book-repeat-number';
        badge.textContent = String(item.repeatGroup);
        badge.setAttribute('aria-label', `叠 ${item.repeatGroup}`);
        span.append(badge);
      }
    }
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

function renderDetectionSummary(elements, result, summaryOpen) {
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
    group.open = summaryOpen;
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


const revisions = new WeakMap();
export function renderProsodyResult(elements, result, type, variant, { makeEditable = () => {}, summaryOpen = false, onLayout = () => {} } = {}) {
  const revision = (revisions.get(elements.lineResults) || 0) + 1;
  revisions.set(elements.lineResults, revision);
  elements.structureScore.textContent = result.structureScore.toFixed(2).replace(/\.00$/, "");
  elements.tonalScore.textContent = result.tonalScore.toFixed(2).replace(/\.00$/, "");
  elements.rhymeScore.textContent = result.rhymeScore.toFixed(2).replace(/\.00$/, "");

  elements.issues.replaceChildren();
  elements.issues.hidden = result.issues.length === 0;
  if (result.issues.length) {
    for (const issue of result.issues) {
      const div = document.createElement("div");
      div.textContent = issue;
      elements.issues.append(div);
    }
  }

  renderDetectionSummary(elements, result, summaryOpen);

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
    }else if(type==='tang'||type==='pailv'){
      const template=characters.map(character=>({
        char:'',tone:character.expected||character.tone,
        rhymeGroup:character.rhymeGroup,repeatGroup:character.repeatGroup,
      }));
      appendCharacterRow(row,template,"tone");
      row.querySelector('.tone-line').setAttribute('aria-label','标准格律模板');
    }else appendCharacterRow(row,characters,"tone");
    makeEditable(row, lineIndex);
    elements.lineResults.append(row);
  });

  if(type==='songci'&&variant) {
    const rows=[...elements.lineResults.children];
    const layoutRevision=revision;
    getDatasetJson('meter','display-layouts.json').then(layouts=>{
      if(elements.lineResults.firstElementChild!==rows[0]||revisions.get(elements.lineResults)!==layoutRevision)return;
      const layout=Object.values(layouts.items||{}).map(item=>item[String(variant.source_id)]).find(Boolean);
      let offset=0;
      const focused = elements.lineResults.contains(document.activeElement) ? document.activeElement : null;
      const selection = focused?.tagName === 'TEXTAREA' || focused?.tagName === 'INPUT' ? [focused.selectionStart, focused.selectionEnd] : null;
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
      elements.lineResults.replaceChildren(fragment);
      if (focused) { focused.focus({preventScroll:true}); if (selection) focused.setSelectionRange(...selection); }
      onLayout([...elements.lineResults.children]);
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

}

import { getDatasetJson } from './dataset-cache.js?v=4';

const palette = ['#D52B3F','#0064D8','#00834A','#A33AD1','#F4C400','#00BED0'];
const rhymeInk = ['#fff','#fff','#fff','#fff','#25231f','#25231f'];
export function paintRhyme(el,group,{avoidWarning=false}={}){const index=avoidWarning?1+(group-1)%(palette.length-1):(group-1)%palette.length;el.style.background=palette[index];el.style.setProperty('--rhyme-group-color',palette[index]);el.style.setProperty('color',rhymeInk[index],'important');}
const launcher = document.createElement('button');
launcher.className = 'meter-book-launch'; launcher.textContent = '词谱'; launcher.title = '点击查询词谱，拖动调整位置'; launcher.setAttribute('aria-label','打开词谱查询，可拖动调整位置');
const launcherLabel=document.createElement('span');launcherLabel.textContent='词谱';launcher.replaceChildren(launcherLabel);
const dialog = document.createElement('dialog'); dialog.className = 'meter-book'; dialog.setAttribute('aria-labelledby','meter-book-title');
dialog.innerHTML = `<header><div><small class="meter-book-eyebrow">诗矩 · 随身词谱</small><h2 id="meter-book-title">词谱查询</h2></div><button class="meter-book-close" aria-label="关闭词谱">×</button></header><div class="meter-book-layout"><section class="meter-book-directory" aria-label="词谱目录"><div class="meter-book-toolbar"><input type="search" aria-label="搜索词牌或别名" placeholder="输入词牌名或别名"><div class="meter-book-categories" role="group" aria-label="词牌分类"></div></div><p class="meter-book-count" role="status"></p><nav class="meter-book-list" aria-label="词牌目录"></nav></section><div class="meter-book-main" hidden><button type="button" class="meter-book-back">← 返回目录</button><p class="meter-book-status" role="status"></p><div class="meter-book-content"></div></div></div>`;
document.body.append(launcher,dialog);
const $ = s => dialog.querySelector(s);
const backButton=$('.meter-book-back');
$('header>div').prepend(backButton);
backButton.hidden=true;
const status = $('.meter-book-status'), content = $('.meter-book-content');
let catalog = [], current = '', serial = 0, loaded = false;
let displayLayouts = null;
let searchIndex=null, searchIndexPromise=null;
function ensureSearchIndex(){
  if(!searchIndexPromise)searchIndexPromise=getDatasetJson('meter','search-index.json').then(data=>{searchIndex=data.items;list();}).catch(()=>{searchIndexPromise=null;if($('input').value.trim())$('.meter-book-count').textContent+=' · 说明索引加载失败，暂仅检索名称和别名';});
  return searchIndexPromise;
}
let category='all', directoryScroll=0, lastEntry=null, page='directory', selectedVariant=null;
const categories={all:'全部',short:'小令',medium:'中调',long:'长调'};
const drawerOpen={short:true,medium:false,long:false};
let savedPage=null;
try{savedPage=JSON.parse(localStorage.getItem('shiju-meter-book-page'));}catch{}
if(savedPage&&typeof savedPage==='object'){
  if(Object.hasOwn(categories,savedPage.category))category=savedPage.category;
  if(typeof savedPage.query==='string')$('input').value=savedPage.query;
  directoryScroll=Number.isFinite(savedPage.directoryScroll)?savedPage.directoryScroll:0;
  for(const key of Object.keys(drawerOpen))if(typeof savedPage.drawers?.[key]==='boolean')drawerOpen[key]=savedPage.drawers[key];
}
function savePage(){
  if(!loaded)return;
  if(page==='directory')directoryScroll=$('.meter-book-directory').scrollTop;
  try{localStorage.setItem('shiju-meter-book-page',JSON.stringify({category,query:$('input').value,drawers:drawerOpen,directoryScroll,page,file:current,variant:selectedVariant,detailScroll:$('.meter-book-main').scrollTop}));}catch{}
}
const node = (tag, text, cls) => { const el=document.createElement(tag); if(text!=null)el.textContent=text; if(cls)el.className=cls; return el; };
function appendSearchResults(q,box){
  const names=catalog.filter(item=>[item.name,...(item.aliases||[])].some(name=>name.includes(q)))
    .sort((a,b)=>a.char_count-b.char_count||a.name.localeCompare(b.name,'zh'));
  const descriptions=[];
  for(const item of catalog){
    const indexed=searchIndex?.[item.file];if(!indexed)continue;
    if(indexed.description?.includes(q))descriptions.push({item,text:indexed.description,label:'词牌简介'});
    for(const variant of indexed.variants||[])if(variant.description?.includes(q))descriptions.push({item,text:variant.description,label:'体式说明',variant:variant.source_id});
  }
  const open=(item,variant)=>{directoryScroll=$('.meter-book-directory').scrollTop;lastEntry=item.file;load(item,variant==null?null:{variant});};
  if(names.length){
    const section=node('div',null,'meter-book-search-section');
    const grid=node('div',null,'meter-book-catalog-grid');
    for(const item of names){const aliases=item.name.includes(q)?[]:(item.aliases||[]).filter(name=>name.includes(q));const button=node('button',null,'meter-book-entry');button.type='button';button.dataset.file=item.file;button.append(node('span',item.name+(aliases.length?`（${aliases.join('、')}）`:'')),node('small',`${item.char_count}字`));button.onclick=()=>open(item);grid.append(button);}
    section.append(grid);box.append(section);
  }
  if(descriptions.length){
    if(names.length){const divider=node('hr',null,'meter-book-ink-divider');box.append(divider);}
    const section=node('div',null,'meter-book-search-section');
    const rows=node('div',null,'meter-book-search-rows');
    for(const hit of descriptions){
      const button=node('button',null,'meter-book-search-row');button.type='button';button.dataset.file=hit.item.file;
      const identity=node('span',null,'meter-book-entry meter-book-search-identity');identity.append(node('span',hit.item.name),node('small',`${hit.item.char_count}字`));
      const index=hit.text.indexOf(q),start=Math.max(0,index-18),end=Math.min(hit.text.length,index+q.length+38);
      const snippet=node('span',null,'meter-book-search-snippet');snippet.append(document.createTextNode('…'+hit.text.slice(start,index).replace(/\s+/g,' ')),node('mark',q),document.createTextNode(hit.text.slice(index+q.length,end).replace(/\s+/g,' ')+'…'));
      button.title=`${hit.label}：${hit.text}`;button.append(identity,snippet);button.onclick=()=>open(hit.item,hit.variant);rows.append(button);
    }section.append(rows);box.append(section);
  }
  $('.meter-book-count').textContent=`找到 ${names.length+descriptions.length} 条结果${!searchIndex?' · 正在检索说明……':''}`;
  if(!names.length&&!descriptions.length&&searchIndex)box.append(node('p','未找到符合条件的词牌。','meter-book-empty'));
}
function list() {
  const q=$('input').value.trim(); const box=$('.meter-book-list'); box.replaceChildren();
  if(q){appendSearchResults(q,box);return;}
  const items=catalog.filter(x=>(category==='all'||category==='aliases'||x.length_category===category)&&[x.name,...(x.aliases||[]),searchIndex?.[x.file]?.description||'',...(searchIndex?.[x.file]?.variants||[]).map(v=>v.description)].some(v=>v.includes(q)));
  const sections=category==='all'?['short','medium','long']: [category]; let total=0;
  for(const key of sections){
    const entries=(category==='aliases'?items.flatMap(item=>(item.aliases||[]).filter(alias=>!q||alias.includes(q)||item.name.includes(q)).map(alias=>({item,alias}))):items.filter(x=>category!=='all'||x.length_category===key).map(item=>({item})))
      .sort((a,b)=>category==='aliases'?(a.alias.localeCompare(b.alias,'zh')):(a.item.char_count-b.item.char_count||a.item.name.localeCompare(b.item.name,'zh')));
    if(!entries.length&&(category!=='all'||q))continue;total+=entries.length;
    const section=node(category==='all'?'details':'section',null,'meter-book-catalog-section');
    if(category==='all'){section.open=!!q||drawerOpen[key];section.addEventListener('toggle',()=>{if(!q&&section.isConnected){drawerOpen[key]=section.open;savePage();}});}
    const heading=node(category==='all'?'summary':'h3',categories[key]);heading.append(node('small',` ${entries.length} 个词牌`));section.append(heading);
    const grid=node('div',null,'meter-book-catalog-grid');
    for(const {item,alias} of entries){
      const matchedAliases=q&&!item.name.includes(q)?(item.aliases||[]).filter(name=>name.includes(q)):[];
      const displayName=matchedAliases.length?`${item.name}（${matchedAliases.join('、')}）`:item.name;
      const b=node('button',null,'meter-book-entry');b.type='button';b.append(node('span',alias||displayName),node('small',alias?`（${item.name}）`:`${item.char_count}字`));
      if(q&&!matchedAliases.length&&!item.name.includes(q))b.title=searchIndex?.[item.file]?.description?.includes(q)?'词牌简介匹配':'体式说明匹配';
      b.setAttribute('aria-current',String(current===item.file));b.onclick=()=>{directoryScroll=$('.meter-book-directory').scrollTop;lastEntry=item.file;load(item);};b.dataset.file=item.file;grid.append(b);
    }section.append(grid);box.append(section);
  }
  $('.meter-book-count').textContent=`${categories[category]} · ${total} ${category==='aliases'?'个别名':'个词牌'}${q?' · 搜索“'+q+'”':''}`;
  if(!total)box.append(node('p','未找到符合条件的词牌，请尝试其他名称或分类。','meter-book-empty'));
}
for(const [key,label] of Object.entries(categories)){
  const button=node('button',label);button.type='button';button.setAttribute('aria-pressed',String(key===category));
  button.onclick=()=>{if(category!==key)$('input').value='';category=key;$('.meter-book-categories').querySelectorAll('button').forEach(b=>b.setAttribute('aria-pressed',String(b===button)));list();$('.meter-book-directory').scrollTop=0;savePage();};$('.meter-book-categories').append(button);
}
function directory(){serial++;page='directory';backButton.hidden=true;$('.meter-book-main').hidden=true;$('.meter-book-directory').hidden=false;$('.meter-book-directory').scrollTop=directoryScroll;const target=[...$('.meter-book-list').querySelectorAll('button')].find(b=>b.dataset.file===lastEntry);target?.focus({preventScroll:true});savePage();}
$('.meter-book-back').onclick=directory;
function chars(raw) { const out=[]; for(const c of raw||'') { if(c==='/'||c==='、'){if(out.length)out.at(-1)[c==='/'?'break':'read']=true;}else if(!/\s/u.test(c))out.push({text:c}); } return out; }
function chineseNumber(value) {
  const digits='零一二三四五六七八九';
  if(value<10)return digits[value];
  if(value<100)return (value<20?'':digits[Math.floor(value/10)])+'十'+(value%10?digits[value%10]:'');
  return String(value);
}
function render(data, examples, variant, preview=null) {
  const content=preview?.target || $(".meter-book-content");
  if(!preview)selectedVariant=variant.source_id;
  content.dataset.variantSourceId=variant.source_id;
  content.replaceChildren(); content.append(node('h3',data.name),node('p',data.description,'meter-book-intro'));
  const variants=node('div',null,'meter-book-variants');
  variants.setAttribute('role','group'); variants.setAttribute('aria-label','选择词谱体式');
  data.variants.forEach((v,i)=>{
    const button=node('button',v.name+(v.source_id===data.default_variant_source_id?' · 默认体':''));
    button.type='button'; button.setAttribute('aria-pressed',String(v===variant));
    button.onclick=()=>{render(data,examples,v,preview);content.querySelector('.meter-book-variants').children[i].focus({preventScroll:true});if(!preview)savePage();};
    variants.append(button);
  });
  content.append(variants);
  const example=examples?.variants?.find(v=>String(v.source_id)===String(variant.source_id));
  const stanzas=Object.entries(variant).filter(([k])=>/^stanza\d+$/.test(k)).sort((a,b)=>Number(a[0].slice(6))-Number(b[0].slice(6)));
  const count=stanzas.reduce((n,[,s])=>n+s.lines.reduce((a,l)=>a+chars(l).length,0),0);
  content.append(node('p',[`${count} 字`,stanzas.map(([,s])=>s.lines.length+'句').join(' / '),variant.rhyme_type].filter(Boolean).join(' · '),'meter-book-summary'));
  const legend=node('div',null,'meter-book-legend'); legend.append(node('span','中：可平可仄'));
  const groups=[...new Set(stanzas.flatMap(([,s])=>Object.keys(s).filter(k=>/^rhyme_\d+_positions$/.test(k)).map(k=>Number(k.split('_')[1]))))].sort((a,b)=>a-b);
  groups.forEach(g=>{const el=node('span',`韵 ${g}`,'meter-book-rhyme');paintRhyme(el,g);legend.append(el);});
  (variant.repetition_groups||[]).forEach((g,i)=>{const el=node('span',`叠 ${i+1}`,'meter-book-repeat-key');el.style.borderStyle=['solid','double','dashed','dotted'][i%4];legend.append(el);}); content.append(legend);
  if(!example)content.append(node('p','此体暂无配套例文，显示平仄谱。','meter-book-note'));
  for(const [key,s] of stanzas) {
    const layout=displayLayouts?.items?.[data.name]?.[String(variant.source_id)]?.stanzas?.[key];
    const rowEnds=new Set(layout?.map(row=>row.sentences.at(-1).line));
    const punctuationByLine=new Map(layout?.flatMap(row=>row.sentences.map(sentence=>[sentence.line,sentence.punctuation])));
    const sn=Number(key.slice(6)), section=node('section',null,'meter-book-stanza');
    if(stanzas.length>1)section.append(node('h4',stanzas.length===2?(sn===1?'上阙':'下阙'):`第${chineseNumber(sn)}叠`));
    let row=node('div',null,'meter-book-line'); section.append(row);
    s.lines.forEach((raw,li)=>{
      const tones=chars(raw), words=chars(example?.[key]?.lines?.[li]);const aligned=words.length===tones.length;
      const sentence=node('span',null,'meter-book-sentence');
      if(preview?.hit?.stanzaKey===key&&li>=preview.hit.lineIndex&&li<preview.hit.lineIndex+preview.hit.partCount)sentence.classList.add('is-search-hit');
      if(words.length&&!aligned)section.append(node('p',words.map(x=>x.text).join('')+'（例文与词谱字数不同，分开展示）','meter-book-note'));
      tones.forEach((tone,ci)=>{
        const cell=node('span',null,'meter-book-char'); if(tone.read)cell.classList.add('is-read');
        const word=node('span',aligned?words[ci].text:''); const symbol=node('span',tone.text,'meter-book-tone');
        (variant.repetition_groups||[]).forEach((g,gi)=>{const position=(g.positions||[]).find(p=>p.stanza===sn&&(li+1>p.start[0]||li+1===p.start[0]&&ci+1>=p.start[1])&&(li+1<p.end[0]||li+1===p.end[0]&&ci+1<=p.end[1]));if(position){cell.classList.add('meter-book-repeat');cell.style.borderStyle=['solid','double','dashed','dotted'][gi%4];cell.title=`叠 ${gi+1}`;if(ci===0||li+1===position.start[0]&&ci+1===position.start[1])cell.classList.add('is-repeat-start');if(ci===tones.length-1||li+1===position.end[0]&&ci+1===position.end[1])cell.classList.add('is-repeat-end');if(li+1===position.start[0]&&ci+1===position.start[1]){const badge=node('sup',String(gi+1),'meter-book-repeat-number');badge.setAttribute('aria-label',`叠 ${gi+1}`);cell.append(badge);}}});
        if(ci===tones.length-1)for(const g of groups)if(s[`rhyme_${g}_positions`]?.includes(li+1)){symbol.classList.add('meter-book-rhyme');paintRhyme(symbol,g);symbol.title=`第 ${g} 组韵`;const badge=node('sup',String(g),'meter-book-rhyme-number');badge.setAttribute('aria-label',`第 ${g} 组韵`);symbol.append(badge);}
        cell.append(word,symbol);sentence.append(cell);
      });
      const endsPhrase=layout?rowEnds.has(li+1):groups.some(g=>s[`rhyme_${g}_positions`]?.includes(li+1))||li===s.lines.length-1;
      const punctuation=node('span',null,'meter-book-char meter-book-punctuation');
      const punctuationText=layout?(punctuationByLine.get(li+1)??''):(endsPhrase?'。':'，');
      punctuation.append(node('span',punctuationText),node('span',punctuationText,'meter-book-tone'));
      if(punctuationText)sentence.append(punctuation); row.append(sentence);
      if(endsPhrase&&li<s.lines.length-1){row=node('div',null,'meter-book-line');section.append(row);}
    });content.append(section);
  }
  if(variant.variants_description){const description=node('section',null,'meter-book-description');description.append(node('h4','体式说明'),node('p',variant.variants_description));content.append(description);}
}
// 抽屉与完整词谱共用字体、例词对齐、原谱分行、韵色和重叠标记。
export async function renderMeterPreview(target, data, variant, hit) {
  const token={}; target.previewToken=token;
  const [layouts, examples]=await Promise.all([
    getDatasetJson('meter','display-layouts.json').catch(()=>null),
    getDatasetJson('meter',`examples/${data.name}-例文.json`).catch(()=>null),
  ]);
  if(target.previewToken!==token)return;
  if(layouts)displayLayouts=layouts;
  render(data,examples,variant,{target,hit});
}
export async function openMeterBook(name, sourceId) {
  if(!dialog.open)dialog.showModal();
  try {
    if(!loaded){catalog=await getDatasetJson('meter','index.json');loaded=true;}
    const item=catalog.find(item=>item.name===name||(item.aliases||[]).includes(name));
    if(!item)throw new Error('词牌不存在');
    await load(item,{variant:sourceId});
  }catch(e){status.textContent='词谱加载失败，请重新选择词牌重试。';}
}
async function load(item,restore=null) {
  const run=++serial; current=item.file;page='detail';backButton.hidden=false;list();$('.meter-book-directory').hidden=true;$('.meter-book-main').hidden=false;status.textContent='正在翻阅词谱……';content.replaceChildren();
  try {
    const data=await getDatasetJson('meter',item.file);
    if(!displayLayouts)try{displayLayouts=await getDatasetJson('meter','display-layouts.json');}catch{}
    let examples=null;try{examples=await getDatasetJson('meter',`examples/${data.name}-例文.json`);}catch{}
    if(run!==serial)return;
    render(data,examples,data.variants.find(v=>String(v.source_id)===String(restore?.variant??data.default_variant_source_id))||data.variants[0]);status.textContent='';$('.meter-book-main').scrollTop=restore?.detailScroll||0;savePage();
  }catch(e){if(run===serial)status.textContent='词谱加载失败，请重新选择词牌重试。';}
}
launcher.onclick=async()=>{dialog.showModal();if(loaded)return;$('.meter-book-count').textContent='正在载入词牌目录……';try{catalog=await getDatasetJson('meter','index.json');loaded=true;list();const item=savedPage?.page==='detail'&&catalog.find(x=>x.file===savedPage.file);if(item){await load(item,savedPage);}else{directory();}}catch{$('.meter-book-count').textContent='目录加载失败，请关闭后重新打开重试。';}};
$('.meter-book-close').onclick=()=>dialog.close();dialog.addEventListener('close',()=>{savePage();launcher.focus();});$('input').oninput=()=>{category='all';$('.meter-book-categories').querySelectorAll('button').forEach(b=>b.setAttribute('aria-pressed',String(b.textContent===categories.all)));list();if($('input').value.trim())ensureSearchIndex();$('.meter-book-directory').scrollTop=0;savePage();};
if($('input').value.trim())ensureSearchIndex();
$('input').placeholder='搜索词牌、别名、简介或体式说明';$('input').setAttribute('aria-label','搜索词牌、别名、简介或体式说明');
let saveTimer;
for(const selector of ['.meter-book-directory','.meter-book-main'])$(selector).addEventListener('scroll',()=>{clearTimeout(saveTimer);saveTimer=setTimeout(savePage,180);});
window.addEventListener('pagehide',savePage);
function place(x,y){launcher.style.left=Math.max(8,Math.min(innerWidth-launcher.offsetWidth-8,x))+'px';launcher.style.top=Math.max(8,Math.min(innerHeight-launcher.offsetHeight-8,y))+'px';launcher.style.right=launcher.style.bottom='auto';}
try{const saved=JSON.parse(localStorage.getItem('shiju-meter-book-position'));if(saved)place(saved.x,saved.y);}catch{}
let drag;
launcher.addEventListener('pointerdown',e=>{if(e.button!==0)return;const r=launcher.getBoundingClientRect();drag={id:e.pointerId,x:e.clientX,y:e.clientY,left:r.left,top:r.top,moved:false};launcher.setPointerCapture(e.pointerId);});
launcher.addEventListener('pointermove',e=>{if(!drag||drag.id!==e.pointerId)return;const dx=e.clientX-drag.x,dy=e.clientY-drag.y;if(Math.hypot(dx,dy)>6)drag.moved=true;if(drag.moved)place(drag.left+dx,drag.top+dy);});
launcher.addEventListener('pointerup',()=>{if(drag?.moved){const r=launcher.getBoundingClientRect();place(r.left,r.top);try{localStorage.setItem('shiju-meter-book-position',JSON.stringify({x:parseFloat(launcher.style.left),y:parseFloat(launcher.style.top)}));}catch{}launcher.addEventListener('click',e=>{e.stopImmediatePropagation();e.preventDefault();},{capture:true,once:true});}drag=null;});
launcher.addEventListener('pointercancel',()=>{drag=null;});window.addEventListener('resize',()=>{const r=launcher.getBoundingClientRect();place(r.left,r.top);});

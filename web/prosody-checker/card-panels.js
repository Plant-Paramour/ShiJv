/** 将创作控件按使用步骤组织，保留原有控件和状态绑定。 */
import { renderCardTexture } from './card-textures.js';
export function organizeCardPanels() {
  const form=document.querySelector('#card-studio-form');
  const groups=[...form.querySelectorAll(':scope > .card-control-group')];
  const [content,composition,typography,background]=groups;
  const label=id=>document.querySelector(id).closest('label');
  const tabs=document.createElement('div');tabs.className='card-panel-tabs';tabs.setAttribute('role','tablist');tabs.setAttribute('aria-label','卡片编辑步骤');
  const host=document.createElement('div');host.className='card-panel-scroll';
  form.prepend(tabs,host);
  groups.forEach((panel,i)=>{
    const name=['内容','构图','文字','背景'][i];
    panel.id=`card-step-${i}`;panel.setAttribute('role','tabpanel');panel.hidden=i!==0;
    panel.querySelector('h2').textContent=name;
    const button=document.createElement('button');button.type='button';button.id=`card-step-tab-${i}`;button.textContent=name;button.setAttribute('role','tab');button.setAttribute('aria-controls',panel.id);button.setAttribute('aria-selected',String(i===0));button.tabIndex=i===0?0:-1;panel.setAttribute('aria-labelledby',button.id);
    const activate=()=>{groups.forEach((p,j)=>{p.hidden=j!==i;const b=tabs.children[j];b.setAttribute('aria-selected',String(j===i));b.tabIndex=j===i?0:-1;});host.scrollTop=0;};
    button.addEventListener('click',activate);
    button.addEventListener('keydown',event=>{if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;event.preventDefault();const n=event.key==='Home'?0:event.key==='End'?3:(i+(event.key==='ArrowRight'?1:3))%4;tabs.children[n].click();tabs.children[n].focus();});
    tabs.append(button);host.append(panel);
  });
  const signature=label('#card-author-input');
  const signatureToggle=document.querySelector('#card-show-author-input');
  const sealToggle=document.querySelector('#card-show-seal-input');
  const switches=signatureToggle.closest('fieldset');switches.querySelector('legend')?.remove();content.append(switches,signature);
  const syncSignature=()=>{signature.hidden=!signatureToggle.checked;};
  signatureToggle.addEventListener('change',syncSignature);
  const syncObserver=new MutationObserver(syncSignature);syncObserver.observe(document.querySelector('#card-scene-preview'),{childList:true});syncSignature();
  composition.prepend(document.querySelector('.card-export-size'));document.querySelector('.card-export-size > span').textContent='画幅比例';
  const textTabs=document.createElement('div');textTabs.className='card-text-tabs';textTabs.setAttribute('role','group');textTabs.setAttribute('aria-label','文字编辑对象');typography.append(textTabs);
  const bodyPanel=document.createElement('div'),titlePanel=document.createElement('div'),signaturePanel=document.createElement('div');
  bodyPanel.append(label('#card-font-input'),label('#card-size-input'),label('#card-leading-input'),document.querySelector('input[name="card-align"]').closest('fieldset'));
  const advanced=document.createElement('details');const summary=document.createElement('summary');summary.textContent='更多排版';advanced.append(summary,label('#card-letter-spacing-input'),label('#card-auto-fit-input'));bodyPanel.append(advanced);
  titlePanel.append(label('#card-title-font-input'),label('#card-title-size-input'));
  signaturePanel.append(signature,label('#card-author-font-input'),label('#card-author-size-input'));
  signaturePanel.append(switches);
  const sealEditor=document.createElement('div');sealEditor.className='card-seal-editor';
  sealEditor.innerHTML='<label class="field"><span>印记式样</span><select id="card-seal-style"><option value="solid">朱砂实印</option><option value="outline">朱文线印</option><option value="round">圆形印</option></select></label><label class="field"><span>印文（最多四字）</span><input id="card-seal-text" maxlength="8" placeholder="笺"></label><label class="field"><span>导入印记图案</span><input id="card-seal-file" type="file" accept="image/png,image/jpeg,image/webp"></label><button class="quiet-button" id="card-seal-clear" type="button">使用内置印记</button><p class="field-hint" id="card-seal-status" role="status">建议使用透明背景 PNG。</p>';
  signaturePanel.append(sealEditor);
  const syncSeal=()=>{sealEditor.hidden=!sealToggle.checked;};sealToggle.addEventListener('change',syncSeal);syncSeal();
  [bodyPanel,titlePanel,signaturePanel].forEach((panel,i)=>{panel.hidden=i!==0;const b=document.createElement('button');b.type='button';b.textContent=['正文','标题','落款'][i];b.setAttribute('aria-pressed',String(i===0));b.addEventListener('click',()=>{[bodyPanel,titlePanel,signaturePanel].forEach((p,j)=>{p.hidden=i!==j;textTabs.children[j].setAttribute('aria-pressed',String(i===j));});});textTabs.append(b);typography.append(panel);});
  typography.querySelectorAll('.card-font-grid,.card-range-row').forEach(el=>el.remove());
  background.querySelector('.card-control-group__title').after(label('#card-theme-input'));
  const colorInput=document.querySelector('#card-color-input');
  const colorField=colorInput.closest('label');
  colorField.querySelector('span').textContent='自定义底色';
  const palette=document.createElement('fieldset');palette.className='card-color-presets';
  const legend=document.createElement('legend');legend.textContent='推荐底色';palette.append(legend);
  const presets=[['宣纸','#eee6d7'],['月白','#f4f5ef'],['杏笺','#f0dfc7'],['藕荷','#e9dce3'],['竹青','#dce5d7'],['天青','#dce8e8'],['雾蓝','#dce2eb'],['暖灰','#dedbd5'],['朱砂','#c98678'],['古绢','#d9c8a3']];
  for(const [name,color] of presets){
    const button=document.createElement('button');button.type='button';button.className='card-color-preset';button.dataset.color=color;
    const swatch=document.createElement('span');swatch.style.backgroundColor=color;swatch.setAttribute('aria-hidden','true');
    button.append(swatch,document.createTextNode(name));button.setAttribute('aria-label',`${name} ${color}`);
    button.addEventListener('click',()=>{colorInput.value=color;colorInput.dispatchEvent(new Event('input',{bubbles:true}));});palette.append(button);
  }
  colorField.before(palette);
  const textureSelect=document.querySelector('#card-texture-input');
  const texturePicker=document.createElement('fieldset');texturePicker.className='card-color-presets card-texture-presets';
  const textureLegend=document.createElement('legend');textureLegend.textContent='纸面纹理';texturePicker.append(textureLegend);
  const syncTextures=()=>texturePicker.querySelectorAll('button').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.texture===textureSelect.value)));
  for(const option of textureSelect.options){
    const button=document.createElement('button');button.type='button';button.className='card-color-preset card-texture-preset';button.dataset.texture=option.value;
    const sample=document.createElement('span');sample.className='card-texture-sample';sample.setAttribute('aria-hidden','true');
    const prefix=`swatch-${option.value}-`;
    const texture=renderCardTexture({texture:option.value,textureOpacity:0.5,theme:'paper'},240,144).replace(/id="([^"]+)"/g,(_,id)=>`id="${prefix}${id}"`).replace(/url\(#([^)]+)\)/g,(_,id)=>`url(#${prefix}${id})`);
    sample.innerHTML=`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 240 144"><rect width="240" height="144" fill="#eee6d7"/>${texture}</svg>`;
    button.append(sample,document.createTextNode(option.textContent.replace(' · 无纹理','')));
    button.addEventListener('click',()=>{textureSelect.value=option.value;textureSelect.dispatchEvent(new Event('change',{bubbles:true}));syncTextures();});texturePicker.append(button);
  }
  textureSelect.closest('label').hidden=true;textureSelect.closest('label').after(texturePicker);
  textureSelect.addEventListener('change',syncTextures);
  new MutationObserver(syncTextures).observe(document.querySelector('#card-scene-preview'),{childList:true});syncTextures();
  const syncColors=()=>{palette.querySelectorAll('button').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.color===colorInput.value.toLowerCase())));};
  colorInput.addEventListener('input',syncColors);
  new MutationObserver(syncColors).observe(document.querySelector('#card-scene-preview'),{childList:true});syncColors();
  const imageSettings=document.createElement('details');imageSettings.className='card-image-settings';const imageSummary=document.createElement('summary');imageSummary.textContent='精细调整';imageSettings.append(imageSummary);
  ['#card-image-opacity-input','#card-image-fit-input','#card-image-x-input','#card-image-y-input','#card-image-scale-input','#card-image-filter-input'].forEach(id=>imageSettings.append(label(id)));
  background.append(imageSettings);background.querySelectorAll('.card-range-row').forEach(el=>el.remove());
  const syncImage=()=>{const id=document.querySelector('input[name="card-layout"]:checked')?.value;const supported=['image-accent','moon-window','side-landscape'].includes(id);document.querySelector('.card-upload-row').hidden=!supported;imageSettings.hidden=!supported||!document.querySelector('#card-scene-preview image');imageHint.hidden=supported;};
  new MutationObserver(syncImage).observe(document.querySelector('#card-scene-preview'),{childList:true});syncImage();
  document.querySelector('#card-export-button').textContent='导出图片';
}

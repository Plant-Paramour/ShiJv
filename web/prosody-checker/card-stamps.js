import {renderSeal} from './card-seal.js';
export function renderStamps(spec,w,h){return (spec.stamps||[]).map((stamp,stampIndex)=>(stamp.instances||[]).map((copy,copyIndex)=>`<g data-stamp-index="${stampIndex}" data-stamp-copy="${copyIndex}" class="card-draggable-stamp"><rect x="${copy.x*w}" y="${copy.y*h}" width="${copy.size*Math.min(w,h)}" height="${copy.size*Math.min(w,h)}" fill="transparent"/>${renderSeal({...spec,showSeal:true,sealStyle:stamp.style,sealText:stamp.text,sealImage:stamp.image,sealCrop:stamp.crop},copy.x*w,copy.y*h,copy.size*Math.min(w,h))}</g>`).join('')).join('');}
export function installStampEditor(state,commit){
 const preview=document.querySelector('#card-scene-preview');
 const addHandles=()=>{
  const svg=preview.querySelector('svg');if(!svg)return;
  const unit=Math.min(svg.viewBox.baseVal.width,svg.viewBox.baseVal.height);
  svg.querySelectorAll('[data-stamp-index]').forEach(target=>{
   const copy=state.stamps?.[Number(target.dataset.stampIndex)]?.instances?.[Number(target.dataset.stampCopy)];if(!copy)return;
   const w=svg.viewBox.baseVal.width,h=svg.viewBox.baseVal.height,side=copy.size*unit;
   const handle=document.createElementNS('http://www.w3.org/2000/svg','rect');handle.dataset.stampResize='true';handle.setAttribute('x',String(copy.x*w+side-8));handle.setAttribute('y',String(copy.y*h+side-8));handle.setAttribute('width','16');handle.setAttribute('height','16');handle.setAttribute('fill','#fff4df');handle.setAttribute('stroke','#b94332');handle.setAttribute('vector-effect','non-scaling-stroke');handle.style.cursor='nwse-resize';target.append(handle);
  });
 };
 new MutationObserver(addHandles).observe(preview,{childList:true});addHandles();
 preview.addEventListener('pointerdown',event=>{
  const target=event.target.closest('[data-stamp-index]');if(!target)return;
  const svg=target.ownerSVGElement,matrix=svg.getScreenCTM()?.inverse();if(!matrix)return;
  const stampIndex=Number(target.dataset.stampIndex),copyIndex=Number(target.dataset.stampCopy);
  const initial=state.stamps?.[stampIndex]?.instances?.[copyIndex];if(!initial)return;
  event.preventDefault();event.stopPropagation();
  const start=new DOMPoint(event.clientX,event.clientY).matrixTransform(matrix);
  const {width:w,height:h}=svg.viewBox.baseVal,size=initial.size*Math.min(w,h);
  const resizing=event.target.hasAttribute('data-stamp-resize');
  let x=initial.x*w,y=initial.y*h,nextSize=initial.size;
  target.setPointerCapture(event.pointerId);target.style.cursor='grabbing';
  const move=ev=>{if(ev.pointerId!==event.pointerId)return;const point=new DOMPoint(ev.clientX,ev.clientY).matrixTransform(matrix);
   if(resizing){const delta=((point.x-start.x)+(point.y-start.y))/2;const side=Math.max(12,Math.min(Math.min(w,h)*.3,w-x,h-y,size+delta));nextSize=side/Math.min(w,h);const scale=side/size;target.setAttribute('transform',`translate(${x} ${y}) scale(${scale}) translate(${-x} ${-y})`);}
   else{x=Math.max(0,Math.min(w-size,initial.x*w+point.x-start.x));y=Math.max(0,Math.min(h-size,initial.y*h+point.y-start.y));target.setAttribute('transform',`translate(${x-initial.x*w} ${y-initial.y*h})`);}
  };
  const cleanup=()=>{target.removeEventListener('pointermove',move);target.removeEventListener('pointerup',end);target.removeEventListener('pointercancel',cancel);target.style.cursor='';if(target.hasPointerCapture(event.pointerId))target.releasePointerCapture(event.pointerId);};
  const end=ev=>{if(ev.pointerId!==event.pointerId)return;cleanup();if(x!==initial.x*w||y!==initial.y*h||nextSize!==initial.size){const list=structuredClone(state.stamps);list[stampIndex].instances[copyIndex]={...initial,x:x/w,y:y/h,size:nextSize};commit('stamps',list);}};
  const cancel=()=>{cleanup();target.removeAttribute('transform');};
  target.addEventListener('pointermove',move);target.addEventListener('pointerup',end);target.addEventListener('pointercancel',cancel);
 });
 const tabs=document.querySelector('.card-text-tabs'),panel=document.createElement('div');panel.hidden=true;panel.className='card-stamp-editor';
 const oldEditor=document.querySelector('.card-seal-editor');oldEditor.hidden=true;oldEditor.style.display='none';
 const oldSwitch=document.querySelector('#card-show-seal-input').closest('label');oldSwitch.hidden=true;
 const button=document.createElement('button');button.type='button';button.textContent='印章';button.setAttribute('aria-pressed','false');tabs.append(button);tabs.parentElement.append(panel);
 let open=false;
 tabs.addEventListener('click',event=>{if(event.target.closest('button')!==button){open=false;panel.hidden=true;button.setAttribute('aria-pressed','false');}});
 button.addEventListener('click',()=>{open=true;[...tabs.children].forEach(b=>b.setAttribute('aria-pressed',String(b===button)));for(const child of tabs.parentElement.children)if(child!==tabs&&child!==panel&&child.tagName==='DIV'&&!child.classList.contains('card-control-group__title'))child.hidden=true;panel.hidden=false;});
 const save=list=>{commit('stamps',list);draw();};
 const draw=()=>{
  panel.replaceChildren();
  const add=document.createElement('button');add.type='button';add.className='quiet-button card-stamp-add';add.textContent='＋ 新建印章';add.onclick=()=>save([...(state.stamps||[]),{id:crypto.randomUUID(),text:'印',style:'solid',image:'',instances:[{x:.8,y:.85,size:.04}]}]);panel.append(add);
  if(!state.stamps?.length){const empty=document.createElement('p');empty.className='field-hint';empty.textContent='添一枚印章，为诗笺落印。';panel.append(empty);}
  (state.stamps||[]).forEach((stamp,index)=>{
   const section=document.createElement('section');section.className='card-stamp-card';
   const header=document.createElement('header');header.className='card-stamp-card__header';
   const sample=document.createElement('div');sample.className='card-stamp-card__preview';sample.setAttribute('aria-hidden','true');sample.innerHTML=`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">${renderSeal({showSeal:true,sealStyle:stamp.style,sealText:stamp.text,sealImage:stamp.image,sealCrop:stamp.crop},8,8,48)}</svg>`;
   const heading=document.createElement('h3');heading.textContent=stamp.image?'自定义图案':stamp.text||'印';
   const count=document.createElement('small');count.textContent=`${stamp.instances.length} 处落印`;
   const identity=document.createElement('div');identity.append(heading,count);header.append(sample,identity);section.append(header);
   const change=(key,value)=>{const list=structuredClone(state.stamps);list[index][key]=value;save(list);};
   const field=(name,control)=>{const label=document.createElement('label');label.className='field';const span=document.createElement('span');span.textContent=name;label.append(span,control);section.append(label);};
   if(!stamp.image){const text=document.createElement('input');text.value=stamp.text;text.maxLength=8;text.onchange=()=>change('text',text.value);field('印文',text);}
   const style=document.createElement('select');
   const choices=stamp.image?[['original','原图比例'],['square','方形'],['rounded','圆角方形'],['circle','圆形']]:[['solid','朱砂实印'],['outline','朱文线印'],['round','圆形印']];
   for(const [value,label] of choices){const option=document.createElement('option');option.value=value;option.textContent=label;style.append(option);}style.value=stamp.image?(stamp.crop||'original'):stamp.style;style.onchange=()=>change(stamp.image?'crop':'style',style.value);field(stamp.image?'形状裁剪':'式样',style);
   const upload=document.createElement('input');upload.type='file';upload.className='card-stamp-upload-input';upload.id=`stamp-upload-${index}`;upload.accept='image/png,image/jpeg,image/webp';
   const uploadLabel=document.createElement('label');uploadLabel.className='quiet-button card-stamp-upload';uploadLabel.htmlFor=upload.id;uploadLabel.textContent=stamp.image?'更换图案':'导入图案';section.append(uploadLabel,upload);
   const status=document.createElement('p');status.className='field-hint';status.setAttribute('role','status');section.append(status);
   upload.onchange=async()=>{const f=upload.files[0];if(!f)return;if(f.size>5*1024*1024||!['image/png','image/jpeg','image/webp'].includes(f.type)){status.textContent='请选择 5MB 以内的 PNG、JPG 或 WebP。';return;}try{const data=await new Promise((resolve,reject)=>{const r=new FileReader();r.onload=()=>resolve(r.result);r.onerror=reject;r.readAsDataURL(f);});change('image',data);}catch{status.textContent='图案读取失败，请重试。';}};
   const action=(name,fn)=>{const b=document.createElement('button');b.type='button';b.className='quiet-button';b.textContent=name;b.onclick=fn;section.append(b);};
   if(stamp.image)action('使用内置式样',()=>change('image',''));
   stamp.instances.forEach((copy,i)=>{
    const row=document.createElement('div');row.className='card-stamp-copy';
    const title=document.createElement('label');title.textContent=`落印 ${i+1}`;
    const control=document.createElement('input');control.type='range';control.min='.015';control.max='.2';control.step='.005';control.value=copy.size;control.setAttribute('aria-label',`落印 ${i+1} 大小`);control.onchange=()=>{const instances=structuredClone(stamp.instances);instances[i].size=Number(control.value);change('instances',instances);};title.append(control);
    const remove=document.createElement('button');remove.type='button';remove.className='card-stamp-remove';remove.textContent='×';remove.setAttribute('aria-label',`移除落印 ${i+1}`);remove.onclick=()=>change('instances',stamp.instances.filter((_,j)=>j!==i));row.append(title,remove);section.append(row);
   });
   action('＋ 再盖一处',()=>change('instances',[...stamp.instances,{x:Math.min(.9,.75+stamp.instances.length*.04),y:.85,size:.04}]));
   action('删除此印章',()=>save(state.stamps.filter((_,j)=>j!==index)));panel.append(section);
  });panel.hidden=!open;
 };
 draw();
 new MutationObserver(draw).observe(document.querySelector('#card-scene-preview'),{childList:true});
}

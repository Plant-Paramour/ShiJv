/** 编辑辅助层只附着于预览 DOM，不参与图片导出。 */
export function installCardGuides(onChange) {
  const preview=document.querySelector('#card-scene-preview');
  let activeKey='body';
  const reset=document.createElement('button');reset.type='button';reset.className='quiet-button';reset.textContent='恢复默认位置';document.querySelector('.card-text-tabs').after(reset);reset.addEventListener('click',()=>onChange(activeKey,null));
  const update=()=>{
    const svg=preview.querySelector('svg');if(!svg)return;
    svg.querySelector('[data-edit-guide]')?.remove();
    if(document.querySelector('#card-step-2')?.hidden!==false)return;
    const selected=[...document.querySelectorAll('.card-text-tabs button')].findIndex(b=>b.getAttribute('aria-pressed')==='true');
    if(selected===3)return;
    activeKey=['body','title','signature'][selected]||'body';
    const {width:w,height:h}=svg.viewBox.baseVal;
    const body=svg.querySelector('[data-effective-size]');
    let box;
    if(selected===0&&body){const d=body.dataset;box={x:Number(d.bodyX??w*.16),y:Number(d.bodyTop??d.bodyY??h*.28),width:Number(d.bodyWidth??w*.68),height:Number(d.bodyHeight??h*.5)};}
    else {
      const selector=selected===2?'[data-card-signature] text':'[data-card-title] text';
      const nodes=[...svg.querySelectorAll(selector)];
      const bounds=nodes.map(n=>n.getBBox()).filter(b=>b.width&&b.height);
      if(bounds.length){const x=Math.min(...bounds.map(b=>b.x)),y=Math.min(...bounds.map(b=>b.y));box={x:x-12,y:y-12,width:Math.max(...bounds.map(b=>b.x+b.width))-x+24,height:Math.max(...bounds.map(b=>b.y+b.height))-y+24};}
      else box=selected===2?{x:w*.62,y:h*.81,width:w*.29,height:h*.08}:{x:w*.15,y:h*.1,width:w*.7,height:h*.12};
    }
    const custom=svg.querySelector(`[data-region="${activeKey}"]`);
    if(custom){const d=custom.dataset;box={x:Number(d.regionX),y:Number(d.regionY),width:Number(d.regionWidth),height:Number(d.regionHeight)};}
    if(!box)return;
    const ns='http://www.w3.org/2000/svg',group=document.createElementNS(ns,'g');group.dataset.editGuide='true';group.setAttribute('pointer-events','none');
    const rect=document.createElementNS(ns,'rect');for(const [key,value] of Object.entries(box))rect.setAttribute(key,String(value));
    const dark=document.querySelector('#card-theme-input')?.value==='night';
    const color=dark?'#ed9682':'#b94332';
    rect.setAttribute('fill',color);rect.setAttribute('fill-opacity',dark?'.14':'.09');
    rect.setAttribute('stroke',color);rect.setAttribute('stroke-opacity','.9');rect.setAttribute('stroke-width','1.5');rect.setAttribute('vector-effect','non-scaling-stroke');
    rect.setAttribute('stroke-dasharray','7 5');
    const corners=document.createElementNS(ns,'path');
    const x=box.x,y=box.y,r=x+box.width,b=y+box.height;
    const arm=Math.min(22,box.width*.12,box.height*.22);
    corners.setAttribute('d',`M${x} ${y+arm}V${y}H${x+arm} M${r-arm} ${y}H${r}V${y+arm} M${r} ${b-arm}V${b}H${r-arm} M${x+arm} ${b}H${x}V${b-arm}`);
    corners.setAttribute('fill','none');corners.setAttribute('stroke',color);corners.setAttribute('stroke-opacity','.8');corners.setAttribute('stroke-width','1');corners.setAttribute('vector-effect','non-scaling-stroke');
    group.setAttribute('pointer-events','all');group.style.touchAction='none';rect.style.cursor='move';
    const begin=(event,corner)=>{
      event.preventDefault();event.stopPropagation();
      const matrix=svg.getScreenCTM()?.inverse();if(!matrix)return;
      const point=ev=>new DOMPoint(ev.clientX,ev.clientY).matrixTransform(matrix);
      const start=point(event),initial={...box};let next={...box};
      const move=ev=>{const p=point(ev),dx=p.x-start.x,dy=p.y-start.y;
        if(!corner){next.x=Math.max(0,Math.min(w-initial.width,initial.x+dx));next.y=Math.max(0,Math.min(h-initial.height,initial.y+dy));}
        else{let l=initial.x,r=l+initial.width,t=initial.y,b=t+initial.height;if(corner.includes('w'))l=Math.max(0,Math.min(r-30,l+dx));else r=Math.min(w,Math.max(l+30,r+dx));if(corner.includes('n'))t=Math.max(0,Math.min(b-30,t+dy));else b=Math.min(h,Math.max(t+30,b+dy));next={x:l,y:t,width:r-l,height:b-t};}
        for(const [k,v] of Object.entries(next))rect.setAttribute(k,String(v));
        handles.forEach(({node,corner:c})=>{node.setAttribute('x',String((c.includes('w')?next.x:next.x+next.width)-handle/2));node.setAttribute('y',String((c.includes('n')?next.y:next.y+next.height)-handle/2));});
      };
      const end=()=>{window.removeEventListener('pointermove',move);window.removeEventListener('pointerup',end);window.removeEventListener('pointercancel',cancel);if(JSON.stringify(next)!==JSON.stringify(initial))onChange(activeKey,{x:next.x/w,y:next.y/h,width:next.width/w,height:next.height/h});};
      const cancel=()=>{window.removeEventListener('pointermove',move);window.removeEventListener('pointerup',end);window.removeEventListener('pointercancel',cancel);update();};
      window.addEventListener('pointermove',move);window.addEventListener('pointerup',end,{once:true});window.addEventListener('pointercancel',cancel,{once:true});
    };
    rect.addEventListener('pointerdown',ev=>begin(ev,null));
    const handle=Math.max(w,h)*.012,handles=[];
    group.append(rect);
    for(const corner of ['nw','ne','sw','se']){const node=document.createElementNS(ns,'rect');node.setAttribute('x',String((corner.includes('w')?box.x:box.x+box.width)-handle/2));node.setAttribute('y',String((corner.includes('n')?box.y:box.y+box.height)-handle/2));node.setAttribute('width',String(handle));node.setAttribute('height',String(handle));node.setAttribute('fill','#fff5e8');node.setAttribute('stroke',color);node.style.cursor=corner==='nw'||corner==='se'?'nwse-resize':'nesw-resize';node.addEventListener('pointerdown',ev=>begin(ev,corner));handles.push({node,corner});group.append(node);}
    svg.append(group);
  };
  document.querySelector('.card-panel-tabs').addEventListener('click',update);
  document.querySelector('.card-text-tabs').addEventListener('click',update);
  new MutationObserver(update).observe(preview,{childList:true});
  document.fonts?.ready.then(update);
  update();
}

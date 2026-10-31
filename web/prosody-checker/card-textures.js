/** 可缩放纸面纹理，在正文下方绘制。 */
export function renderCardTexture(spec,width,height) {
  if(spec.texture==='none'||spec.textureOpacity<=0)return '';
  const kind=spec.texture;
  if(['fine','bark','laid','cotton','aged','gold'].includes(kind)||kind==='silk') {
    let seed=173;
    const random=()=>{seed=(seed*16807)%2147483647;return (seed-1)/2147483646;};
    const tile=384,color=spec.theme==='night'?'#f5ecd8':'#685a46';
    let marks='';
    if(['fine','bark'].includes(kind))for(let i=0;i<(kind==='bark'?150:420);i++){
      const x=random()*tile,y=random()*tile,length=kind==='bark'?12+random()*34:3+random()*10;
      const dx=(random()-.5)*length,dy=(random()-.5)*length;
      marks+=`<path d="M${x} ${y}q${dx*.4} ${dy*.8} ${dx} ${dy}" fill="none" stroke="${color}" stroke-width="${.5+random()*.9}" opacity="${.4+random()*.5}"/>`;
    }
    if(kind==='silk'||kind==='laid'){
      const step=kind==='silk'?5:4;
      for(let y=1;y<tile;y+=step)marks+=`<path d="M0 ${y}h${tile}" stroke="${color}" stroke-width="${kind==='silk'?.6+random()*.4:.15+random()*.25}" opacity=".65"/>`;
      for(let x=2;x<tile;x+=kind==='silk'?5:96)marks+=`<path d="M${x} 0v${tile}" stroke="${color}" stroke-width="${kind==='silk'?.5:.65}" opacity="${kind==='silk'?.5:.45}"/>`;
    }
    if(kind==='cotton'||kind==='fine'||kind==='aged')for(let i=0;i<650;i++)marks+=`<circle cx="${random()*tile}" cy="${random()*tile}" r="${kind==='cotton'?.5+random()*1.3:.25+random()*.8}" fill="${color}" opacity="${.2+random()*.5}"/>`;
    if(kind==='aged')for(let i=0;i<24;i++)marks+=`<ellipse cx="${random()*tile}" cy="${random()*tile}" rx="${3+random()*18}" ry="${2+random()*9}" fill="#9f7847" opacity=".045"/>`;
    if(kind==='gold')for(let i=0;i<55;i++){
      const x=random()*tile,y=random()*tile,r=2+random()*5;
      marks+=`<path d="M${x} ${y}l${r} ${-r*.3} ${r*.4} ${r} ${-r*1.4} ${r*.3}Z" fill="${i%2?'#bd9146':'#e1bf72'}" opacity=".9"/>`;
    }
    const edge=kind==='aged'?`<defs><radialGradient id="paper-age"><stop offset=".6" stop-color="#997044" stop-opacity="0"/><stop offset="1" stop-color="#997044" stop-opacity="${spec.textureOpacity*.3}"/></radialGradient></defs><rect width="${width}" height="${height}" fill="url(#paper-age)"/>`:'';
    const scale=kind==='silk'?1.35:kind==='bark'?1.25:1;
    const opacity=Math.min(1,spec.textureOpacity*(kind==='gold'?3:kind==='fine'||kind==='cotton'||kind==='silk'||kind==='bark'?2:1));
    return `<defs><pattern id="card-paper-texture" width="${tile}" height="${tile}" patternUnits="userSpaceOnUse" patternTransform="scale(${scale})">${marks}</pattern></defs><rect width="${width}" height="${height}" fill="url(#card-paper-texture)" opacity="${opacity}" pointer-events="none"/>${edge}`;
  }
  const patterns={
    fiber:'<path d="M2 5l12 2m8 16l20 3M8 39l15-2m19-25l7 1M30 49l17-3" stroke-width=".6"/><path d="M11 3l2 11m22 14l-1 8" stroke-width=".35"/>',
    silk:'<path d="M0 2h12M0 6h12M0 10h12M2 0v12M6 0v12M10 0v12" stroke-width=".3"/>',
    grain:'<circle cx="3" cy="4" r=".6"/><circle cx="12" cy="15" r=".5"/><circle cx="20" cy="7" r=".7"/><circle cx="7" cy="23" r=".4"/>',
    ripple:'<path d="M-20 10Q0-2 20 10T60 10M-20 30Q0 18 20 30T60 30" stroke-width=".5" fill="none"/>',
  };
  const pattern=patterns[spec.texture];if(!pattern)return '';
  const tile=spec.texture==='fiber'?52:spec.texture==='silk'?12:spec.texture==='grain'?28:40;
  const color=spec.theme==='night'?'#f8f0dc':'#63523d';
  return `<defs><pattern id="card-paper-texture" width="${tile}" height="${tile}" patternUnits="userSpaceOnUse"><g stroke="${color}" fill="${color}">${pattern}</g></pattern></defs><rect width="${width}" height="${height}" fill="url(#card-paper-texture)" opacity="${spec.textureOpacity}" pointer-events="none"/>`;
}

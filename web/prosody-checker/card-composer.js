/** 按模板安全区域测量正文，统一预览与导出构图。 */
import { escapeXml as e, wrapVerseText } from './card-scene.js';
import { refinedCard } from './card-refined.js';
import { renderSeal } from './card-seal.js';
import { composePoetryScroll } from './card-scroll.js';
export function composeCard(s,m) {
 if(s.layout==='poetry-scroll')return composePoetryScroll(s,m);
 const {width:w,height:h,profile:p,titleProfile:t,authorProfile:a}=m;
 const dark=s.theme==='night',ink=dark?'#f2e9d7':'#343e36',red=dark?'#d8ad82':'#a45346';
 let b={x:w*.18,y:h*.32,w:w*.64,h:h*.43},ty=h*.2,tx=w/2,deco='';
 const rect=(x,y,rw,rh)=>`<rect x="${x}" y="${y}" width="${rw}" height="${rh}" fill="none" stroke="${red}"/>`;
 const photo=(x,y,rw,rh,round=false)=>`<defs><clipPath id="photo-clip">${round?`<circle cx="${x+rw/2}" cy="${y+rh/2}" r="${Math.min(rw,rh)/2}"/>`:`<rect x="${x}" y="${y}" width="${rw}" height="${rh}"/>`}</clipPath><filter id="photo-filter"><feColorMatrix type="saturate" values="${s.imageFilter==='mono'?0:s.imageFilter==='warm'?.5:1}"/>${s.imageFilter==='soft'?'<feGaussianBlur stdDeviation="2"/>':''}</filter></defs><g clip-path="url(#photo-clip)"><rect x="${x}" y="${y}" width="${rw}" height="${rh}" fill="#b8c5bb"/>${s.imageUrl?`<image href="${e(s.imageUrl)}" x="${x-(s.imageScale-1)*rw*s.imageX/100}" y="${y-(s.imageScale-1)*rh*s.imageY/100}" width="${rw*s.imageScale}" height="${rh*s.imageScale}" preserveAspectRatio="xMidYMid ${s.imageFit==='contain'?'meet':'slice'}" opacity="${s.imageOpacity}" filter="url(#photo-filter)"/>`:`<circle cx="${x+rw*.7}" cy="${y+rh*.27}" r="${rw*.12}" fill="#f0e4c9"/><path d="M${x} ${y+rh}L${x+rw*.4} ${y+rh*.35}L${x+rw} ${y+rh}Z" fill="#566d62" opacity=".35"/><path d="M${x+rw*.2} ${y+rh}L${x+rw*.8} ${y+rh*.5}L${x+rw*1.3} ${y+rh}Z" fill="#778c7b"/>`}</g>`;
 if(['classic-frame','vertical-note','moon-window','side-landscape','willow-poet'].includes(s.layout)) return refinedCard(s,m,photo);
 if(s.layout==='classic-frame')deco=rect(64,64,w-128,h-128)+rect(79,79,w-158,h-158);
 if(s.layout==='horizontal-verse')deco=rect(w*.08,h*.09,w*.84,h*.82)+`<path d="M${w*.15} ${h*.25}h${w*.7}" stroke="${red}"/>`;
 if(s.layout==='quiet-page'){b.x=w*.29;b.w=w*.55;tx=w*.29;deco=`<path d="M${w*.18} ${h*.15}V${h*.86}" stroke="${red}" stroke-width="3"/>`;}
 if(s.layout==='ruled-letter')deco=rect(85,85,w-170,h-170)+`<path d="M${w*.13} 85V${h-85}" stroke="${red}"/>`;
 if(s.layout==='moon-window'){
  const diameter=Math.min(w*.34,h*.24);
  const pictureTop=h*.055;
  deco=photo((w-diameter)/2,pictureTop,diameter,diameter,true);
  ty=pictureTop+diameter+Math.max(s.titleSize*1.5,h*.045);
  const bodyTop=ty+Math.max(s.titleSize*1.5,h*.045);
  b={x:w*.12,y:bodyTop,w:w*.76,h:Math.max(0,h*.80-bodyTop)};
 }
 if(s.layout==='side-landscape'){
  const pictureWidth=w*.31;
  deco=photo(w*.055,h*.085,pictureWidth,h*.83);
  b={x:w*.43,y:h*.26,w:w*.50,h:h*.53};
  tx=b.x+b.w/2;ty=h*.17;
  deco+=`<path d="M${w*.397} ${h*.13}V${h*.86}" stroke="${red}" opacity=".3"/>`;
 }
 if(s.layout==='image-accent')deco=photo(0,0,w,h)+`<rect width="${w}" height="${h}" fill="#142326" opacity=".7"/>`;
 const fg=s.layout==='image-accent'?'#fff8e9':ink;
 const text=(v,x,y,z,f,anchor='middle',spacing=0)=>`<text x="${x}" y="${y}" font-size="${z}" font-family="${e(f)}" fill="${fg}" text-anchor="${anchor}" letter-spacing="${spacing}">${e(v)}</text>`;
 let size=s.fontSize,lines=[],body='';
 if(s.layout==='vertical-note') {
  let n;
  do{n=Math.max(1,Math.floor(h*.54/(size+s.letterSpacing+5)));lines=s.body.split('\n').flatMap(v=>{let c=Array.from(v);return Array.from({length:Math.max(1,Math.ceil(c.length/n))},(_,i)=>c.slice(i*n,(i+1)*n).join(''));});if(!s.autoFit||lines.length*size*s.leading<=w*.57||size<=10)break;size--;}while(size>0);
  const gap=size*s.leading,total=(lines.length-1)*gap,start=s.align==='start'?w*.17+total:s.align==='end'?w*.73:(w*.9+total)/2;
  body=lines.map((v,i)=>Array.from(v).map((c,j)=>text(c,start-i*gap,h*.31+j*(size+s.letterSpacing+5),size,p.family)).join('')).join('');
  deco=`<path d="M${w*.12} ${h*.1}h${w*.76}M${w*.12} ${h*.9}h${w*.76}" stroke="${red}"/>`;
 }else{
  do{lines=wrapVerseText(s.body,{maxWidth:b.w,fontSize:size,fontFamily:p.family,letterSpacing:s.letterSpacing});if(!s.autoFit||lines.length*size*s.leading<=b.h||size<=10)break;size--;}while(size>0);
  const step=size*s.leading,top=b.y+Math.max(0,(b.h-lines.length*step)/2)+size,x=s.align==='start'?b.x:s.align==='end'?b.x+b.w:b.x+b.w/2;
  body=lines.map((v,i)=>(s.layout==='ruled-letter'?`<path d="M${b.x} ${top+i*step+step*.3}h${b.w}" stroke="${red}" opacity=".3"/>`:'')+text(v,x,top+i*step,size,p.family,s.align,s.letterSpacing)).join('');
 }
 const signature=s.showAuthor&&s.author?text(s.author,w*.8,h*.85,s.authorSize,a.family,'end',2):'';
 const seal=renderSeal(s,w*.81,h*.88,36);
 const overflow=s.layout==='vertical-note'?lines.length*size*s.leading>w*.57:lines.length*size*s.leading>b.h;
 return `<rect width="${w}" height="${h}" fill="${e(dark?'#263f41':s.background)}"/>${deco}<g data-card-title="true">${text(s.title,tx,ty,s.titleSize,t.family,s.layout==='quiet-page'?'start':'middle',5)}</g><g data-body-x="${b.x}" data-body-y="${b.y}" data-body-width="${b.w}" data-body-height="${b.h}" data-effective-size="${size}" data-overflow="${overflow}" data-body-lines="${lines.length}">${body}</g><g data-card-signature="true">${signature}</g>${seal}`;
}

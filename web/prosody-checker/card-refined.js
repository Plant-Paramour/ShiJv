/** 四种主模板的内容测量与构图规则。 */
import { escapeXml as e, wrapVerseText } from './card-scene.js';
import { renderSeal } from './card-seal.js';
export function refinedCard(s,m,photo) {
 const {width:w,height:h,profile:p,titleProfile:t,authorProfile:a}=m;
 const unit=Math.min(w,h),margin=unit*.09,ink=s.theme==='night'?'#eee5d4':'#353d35',accent=s.theme==='night'?'#c6a078':'#a05b4c';
 const txt=(v,x,y,z,f,anchor='middle',spacing=0)=>`<text x="${x}" y="${y}" font-size="${z}" font-family="${e(f)}" fill="${ink}" text-anchor="${anchor}" letter-spacing="${spacing}">${e(v)}</text>`;
 let left=margin*1.6,right=w-margin*1.6,top=h*.24,bottom=h*.79,deco='',title='',body='',size=s.fontSize,lines=[],overflow=false;
 let signatureY=h*.86,sealY=h*.89,bodyStep=0;
 let titleTop=h*.18;
 if(s.layout==='classic-frame') {
  deco=`<rect x="${margin*.7}" y="${margin*.7}" width="${w-margin*1.4}" height="${h-margin*1.4}" fill="none" stroke="${accent}" stroke-width="2"/><rect x="${margin*.7+10}" y="${margin*.7+10}" width="${w-margin*1.4-20}" height="${h-margin*1.4-20}" fill="none" stroke="${accent}" opacity=".3"/>`;
 }
 if(s.layout==='moon-window') {
  left=margin*1.3;right=w-margin*1.3;
  const count=wrapVerseText(s.body,{maxWidth:right-left,fontSize:size,fontFamily:p.family,letterSpacing:s.letterSpacing}).length;
  const diameter=Math.min(w*.32,h*(count>8?.17:.23));
  deco=photo((w-diameter)/2,margin*.65,diameter,diameter,true);
  titleTop=margin*.65+diameter+unit*.06;
 }
 if(s.layout==='side-landscape') {
  const dense=Array.from(s.body).length>100;
  const imageW=w*(dense?.27:.33);
  deco=photo(margin*.65,margin,imageW,h-2*margin);
  left=margin*.65+imageW+unit*.07;right=w-margin;
  titleTop=h*.17;
 }
 if(s.layout==='willow-poet') {
  const url=new URL('./assets/cards/backgrounds/willow-poet.png',import.meta.url).href;
  deco=`<image href="${e(url)}" x="0" y="0" width="${w}" height="${h}" preserveAspectRatio="none"/>`;
  left=w*.415;right=w*.925;
  titleTop=h*.105+s.titleSize;
  sealY=h*.905;
  const signatureLines=wrapVerseText(s.author,{maxWidth:right-left,fontSize:s.authorSize,fontFamily:a.family,letterSpacing:1});
  const footerHeight=s.showAuthor&&s.author?signatureLines.length*s.authorSize*1.4:0;
  signatureY=sealY-(s.showSeal?unit*.045:0)-Math.max(0,footerHeight-s.authorSize);
  bottom=signatureY-(footerHeight?s.authorSize*.35:0)-unit*.05;
 }
 const titles=wrapVerseText(s.title,{maxWidth:right-left,fontSize:s.titleSize,fontFamily:t.family,letterSpacing:3});
 top=titleTop+(s.title?titles.length*s.titleSize*1.4:0)+unit*.045;
 if(s.layout==='willow-poet')top=s.title?titleTop+(titles.length-1)*s.titleSize*1.4+unit*.045:h*.12;
 if(s.layout==='vertical-note') {
  left=margin*1.5;right=w-margin*2.6;top=h*.2;bottom=h*.79;
  let capacity;
  do{capacity=Math.max(1,Math.floor((bottom-top)/(size+s.letterSpacing+5)));lines=s.body.split('\n').flatMap(v=>{const c=Array.from(v);return Array.from({length:Math.max(1,Math.ceil(c.length/capacity))},(_,i)=>c.slice(i*capacity,(i+1)*capacity).join(''));});if(!s.autoFit||lines.length*size*s.leading<=right-left||size<=10)break;size--;}while(size>0);
  const gap=size*s.leading,total=(lines.length-1)*gap;
  const start=s.align==='start'?left+total:s.align==='end'?right:(left+right+total)/2;
  body=lines.map((line,i)=>Array.from(line).map((c,j)=>txt(c,start-i*gap,top+size+j*(size+s.letterSpacing+5),size,p.family)).join('')).join('');
  title=Array.from(s.title).map((c,i)=>txt(c,w-margin*1.3,top+s.titleSize+i*s.titleSize*1.3,s.titleSize,t.family)).join('');
  overflow=lines.length*gap>right-left||Array.from(s.title).length*s.titleSize*1.3>bottom-top;
  deco=`<path d="M${w-margin*2} ${top}V${bottom}" stroke="${accent}" opacity=".4"/>`;
 } else {
  const occupied=z=>z+Math.max(0,lines.length-1)*z*s.leading;
  do{lines=wrapVerseText(s.body,{maxWidth:right-left,fontSize:size,fontFamily:p.family,letterSpacing:s.letterSpacing});if(!s.autoFit||occupied(size)<=bottom-top||size<=10)break;size--;}while(size>0);
  const step=size*s.leading,total=size+Math.max(0,lines.length-1)*step;
  bodyStep=step;
  let y=top+Math.max(0,(bottom-top-total)*.35)+size;
  if(s.autoFit && ['classic-frame','side-landscape'].includes(s.layout)) {
   const heading=s.title?titles.length*s.titleSize*1.4+unit*.045:0;
   const footer=s.showAuthor&&s.author?s.authorSize*1.4+unit*.04:0;
   const groupHeight=heading+total+footer;
   const groupTop=Math.max(h*.14,(h-groupHeight)/2-unit*.025);
   titleTop=groupTop+s.titleSize;
   top=groupTop+heading;
   y=top+size;
  }
  if(s.autoFit && s.layout!=='willow-poet') {
   signatureY=Math.min(h*.87,y+Math.max(0,lines.length-1)*step+Math.max(unit*.055,size*s.leading));
   sealY=signatureY+unit*.035;
  }
  const x=s.align==='start'?left:s.align==='end'?right:(left+right)/2;
  body=lines.map((v,i)=>txt(v,x,y+i*step,size,p.family,s.align,s.letterSpacing)).join('');
  title=titles.map((v,i)=>txt(v,(left+right)/2,titleTop+i*s.titleSize*1.4,s.titleSize,t.family,'middle',3)).join('');
  overflow=total>bottom-top;
 }
 let signatureSize=s.authorSize;
 let signatureLines=wrapVerseText(s.author,{maxWidth:right-left,fontSize:signatureSize,fontFamily:a.family,letterSpacing:1});
 if(s.autoFit&&s.layout!=='willow-poet')while(signatureLines.length*signatureSize*1.4>h*.08&&signatureSize>12){signatureSize--;signatureLines=wrapVerseText(s.author,{maxWidth:right-left,fontSize:signatureSize,fontFamily:a.family,letterSpacing:1});}
 const signature=s.showAuthor&&s.author?signatureLines.map((line,i)=>txt(line,right,signatureY+i*signatureSize*1.4,signatureSize,a.family,'end',1)).join(''):'';
 const seal=renderSeal(s,right-36,sealY,36);
 const clip=s.layout==='willow-poet'?`<defs><clipPath id="willow-body"><rect x="${left-2}" y="${top}" width="${right-left+4}" height="${Math.max(0,bottom-top)+size*.25}"/></clipPath></defs>`:'';
 return `<rect width="${w}" height="${h}" fill="${e(s.theme==='night'?'#263f41':s.background)}"/>${deco}<g data-card-title="true">${title}</g>${clip}<g ${s.layout==='willow-poet'?'clip-path="url(#willow-body)"':''} data-body-x="${left}" data-body-top="${top}" data-body-bottom="${bottom}" data-line-step="${bodyStep}" data-effective-size="${size}" data-overflow="${overflow}" data-body-lines="${lines.length}" data-body-width="${right-left}" data-body-height="${bottom-top}">${body}</g><g data-card-signature="true">${signature}${seal}</g>`;
}

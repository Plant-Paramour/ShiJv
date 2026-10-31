import {escapeXml as e,wrapVerseText,LETTERFORM_PROFILES} from './card-scene.js';
export function applyCardRegions(markup,s,format){
 const regions=s.regions?.[s.layout]||{};
 for(const key of ['body','title','signature']){
  const box=regions[key];if(!box)continue;
  const x=box.x*format.width,y=box.y*format.height,w=box.width*format.width,h=box.height*format.height;
  const value=key==='body'?s.body:key==='title'?s.title:s.showAuthor?s.author:'';
  const size=key==='body'?s.fontSize:key==='title'?s.titleSize:s.authorSize;
  const family=LETTERFORM_PROFILES[key==='body'?s.font:key==='title'?s.titleFont:s.authorFont].family;
  const lines=wrapVerseText(value,{maxWidth:w,fontSize:size,fontFamily:family,letterSpacing:s.letterSpacing});
  const step=size*(key==='body'?s.leading:1.4),anchor=key==='body'?s.align:'middle';
  const tx=anchor==='start'?x:anchor==='end'?x+w:x+w/2;
  let content=lines.map((line,i)=>`<text x="${tx}" y="${y+size+i*step}" font-family="${e(family)}" font-size="${size}" fill="${s.theme==='night'?'#eee5d4':'#353d35'}" text-anchor="${anchor}" letter-spacing="${s.letterSpacing}">${e(line)}</text>`).join('');
  if(s.layout==='vertical-note'&&key!=='signature'){
    const capacity=Math.max(1,Math.floor(h/(size+s.letterSpacing+5)));
    const columns=value.split('\n').flatMap(line=>{const chars=Array.from(line);return Array.from({length:Math.max(1,Math.ceil(chars.length/capacity))},(_,i)=>chars.slice(i*capacity,(i+1)*capacity));});
    content=columns.map((column,i)=>column.map((char,j)=>`<text x="${x+w-size/2-i*step}" y="${y+size+j*(size+s.letterSpacing+5)}" font-family="${e(family)}" font-size="${size}" fill="${s.theme==='night'?'#eee5d4':'#353d35'}" text-anchor="middle">${e(char)}</text>`).join('')).join('');
  }
  const attr=key==='body'?'data-effective-size':key==='title'?'data-card-title':'data-card-signature';
  const replacement=`<g ${attr}="${key==='body'?size:'true'}" data-region="${key}" data-region-x="${x}" data-region-y="${y}" data-region-width="${w}" data-region-height="${h}" data-overflow="${size+Math.max(0,lines.length-1)*step>h}"><defs><clipPath id="custom-${key}"><rect x="${x}" y="${y}" width="${w}" height="${h}"/></clipPath></defs><g clip-path="url(#custom-${key})">${content}</g></g>`;
  markup=markup.replace(new RegExp(`<g\\b[^>]*${attr}="[^"]*"[^>]*>[\\s\\S]*?<\\/g>`),()=>replacement);
 }
 return markup;
}

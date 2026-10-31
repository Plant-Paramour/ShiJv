/** 卷轴的装裱、诗文与真实格律诊断共用同一 SVG 场景。 */
import { escapeXml as e, wrapVerseText } from './card-scene.js';
import { renderSeal } from './card-seal.js';

const plain=v=>String(v||'').replace(/[\p{P}\p{Z}\s]/gu,'');
export function composePoetryScroll(s,m) {
 const {width:w,height:h,profile:p,titleProfile:t,authorProfile:a}=m;
 const u=Math.min(w,h),ink='#393d33',red='#a2473c';
 const x=w*.055,y=h*.085,pw=w*.89,ph=h*.83;
 const left=w*.14,right=w*.86,col=w*.70,cw=w*.20;
 const text=(value,tx,ty,size,family=p.family,anchor='start',fill=ink)=>`<text x="${tx}" y="${ty}" font-size="${size}" font-family="${e(family)}" text-anchor="${anchor}" fill="${fill}">${e(value)}</text>`;
 const rect=(rx,ry,rw,rh,fill,extra='')=>`<rect x="${rx}" y="${ry}" width="${rw}" height="${rh}" fill="${fill}" ${extra}/>`;
 const defs=`<defs>
 <clipPath id="scroll-paper-clip"><rect x="${x+u*.026}" y="${y+u*.025}" width="${pw-u*.052}" height="${ph-u*.05}"/></clipPath>
 <linearGradient id="scroll-silk"><stop stop-color="#77705b"/><stop offset=".06" stop-color="#b7ac8a"/><stop offset=".5" stop-color="#c9bd99"/><stop offset=".95" stop-color="#aa9d7c"/><stop offset="1" stop-color="#6c6350"/></linearGradient>
 <linearGradient id="scroll-paper"><stop stop-color="#756245" stop-opacity=".28"/><stop offset=".06" stop-color="#fff" stop-opacity=".08"/><stop offset=".5" stop-color="#fff" stop-opacity=".15"/><stop offset=".94" stop-color="#fff" stop-opacity=".04"/><stop offset="1" stop-color="#756245" stop-opacity=".3"/></linearGradient>
 <linearGradient id="scroll-wood"><stop stop-color="#302b25"/><stop offset=".24" stop-color="#635142"/><stop offset=".46" stop-color="#8c7258"/><stop offset=".61" stop-color="#594939"/><stop offset="1" stop-color="#292722"/></linearGradient>
 <pattern id="scroll-weave" width="8" height="8" patternUnits="userSpaceOnUse"><path d="M0 1H8M1 0V8" stroke="#665b40" stroke-width=".5" opacity=".22"/></pattern>
 </defs>`;
 let decor=rect(0,0,w,h,'#e8e4da')+rect(x-4,y+7,pw+8,ph+9,'#514b3e','opacity=".09" rx="4"')+rect(x,y,pw,ph,'url(#scroll-silk)')+rect(x,y,pw,ph,'url(#scroll-weave)')+rect(x+u*.026,y+u*.025,pw-u*.052,ph-u*.05,s.background)+`<g clip-path="url(#scroll-paper-clip)"><!--card-paper-texture--></g>`+rect(x+u*.026,y+u*.025,pw-u*.052,ph-u*.05,'url(#scroll-paper)');
 for(const rx of [x-u*.012,x+pw-u*.012]) {
  decor+=rect(rx,y-u*.022,u*.024,ph+u*.044,'url(#scroll-wood)','rx="7"');
  for(const ry of [y-u*.029,y+ph+u*.019])decor+=`<ellipse cx="${rx+u*.012}" cy="${ry}" rx="${u*.018}" ry="${u*.008}" fill="#50463b" stroke="#9c8665" stroke-width="1"/>`;
 }
 const titleWidth=w*.36;
 let titleSize=s.titleSize;
 let titles=wrapVerseText(s.title,{maxWidth:titleWidth,fontSize:titleSize,fontFamily:t.family});
 while(s.autoFit&&titles.length>2&&titleSize>18){titleSize--;titles=wrapVerseText(s.title,{maxWidth:titleWidth,fontSize:titleSize,fontFamily:t.family});}
 const titleHeight=s.title?titleSize+(titles.length-1)*titleSize*1.3:0;
 const titleTop=h*.165+titleSize;
 const heading=s.title?titles.map((v,i)=>text(v,w/2,titleTop+i*titleSize*1.3,titleSize,t.family,'middle')).join(''):'';
 let authorSize=s.authorSize;
 let authors=wrapVerseText(s.author,{maxWidth:titleWidth,fontSize:authorSize,fontFamily:a.family});
 while(s.autoFit&&authors.length>2&&authorSize>12){authorSize--;authors=wrapVerseText(s.author,{maxWidth:titleWidth,fontSize:authorSize,fontFamily:a.family});}
 const signatureY=h*.165+titleHeight+u*.020+authorSize;
 const signature=s.showAuthor&&s.author?authors.map((v,i)=>text(v,w/2,signatureY+i*authorSize*1.4,authorSize,a.family,'middle')).join(''):'';
 const headerBottom=Math.max(h*.32,h*.165+titleHeight+(signature?u*.020+authors.length*authorSize*1.4:0)+u*.042);
 decor+=`<path d="M${x+u*.026} ${headerBottom}H${x+pw-u*.026}" stroke="#aa9271" opacity=".5"/>`;
 const logoSize=u*.076,logoX=x+u*.052,logoY=h*.18;
 decor+=rect(logoX,logoY,logoSize,logoSize,'none',`stroke="${red}" stroke-width="1.2"`)+text('诗矩',logoX+logoSize/2,logoY+logoSize*.65,logoSize*.32,t.family,'middle',red);
 const top=headerBottom+u*.070,bottom=h*.805;
 let size=s.fontSize,lines;
 do {
  lines=wrapVerseText(s.body,{maxWidth:right-left,fontSize:size,fontFamily:p.family,letterSpacing:s.letterSpacing});
  if(!s.autoFit||size+Math.max(0,lines.length-1)*size*s.leading<=bottom-top||size<=10)break;
  size--;
 }while(size>0);
 const d=s.diagnostic,valid=d&&plain(d.body)===plain(s.poemBody??s.body);
 const ev=valid?d.evaluation:null;
 const occupied=size+Math.max(0,lines.length-1)*size*s.leading;
 const start=top+Math.max(0,(bottom-top-occupied)*.45)+size;
 const anchor=s.align,tx=anchor==='start'?left:anchor==='end'?right:(left+right)/2;
 const body=lines.map((line,i)=>`<text x="${tx}" y="${start+i*size*s.leading}" font-size="${size}" font-family="${e(p.family)}" text-anchor="${anchor}" letter-spacing="${s.letterSpacing}">${e(line)}</text>`).join('');
 const score=v=>v!==null&&v!==undefined&&v!==''&&Number.isFinite(Number(v))?String(Math.round(Number(v))):'—';
 const scoreFamily='"Microsoft YaHei", "PingFang SC", "Noto Sans CJK SC", sans-serif';
 const book=ev?.rhyme_book_name||ev?.rhyme_book_id;
 const scoreSize=Math.min(24,u*.024);
 const scoreY=h*.19+scoreSize;
 const badgeWidth=scoreSize*5.3;
 let diagnosis=rect(col,scoreY-scoreSize*1.15,badgeWidth,scoreSize*1.8,'none',`stroke="${red}" stroke-width="1.2" opacity=".7"`)+`<g font-weight="600">${text('格律评分',col+badgeWidth/2,scoreY+scoreSize*.15,scoreSize,scoreFamily,'middle',red)}</g>`;
 const books=wrapVerseText(book||'韵书未标注',{maxWidth:Math.max(scoreSize*2,cw-badgeWidth-scoreSize),fontSize:scoreSize*.9,fontFamily:scoreFamily});
 diagnosis+=books.map((v,i)=>text(v,col+badgeWidth+scoreSize,scoreY+i*scoreSize*1.35,scoreSize*.9,scoreFamily,'start','#686b62')).join('');
 const scoreTop=scoreY+Math.max(scoreSize*2.6,books.length*scoreSize*1.35+scoreSize);
 [['结构',ev?.structure_score],['平仄',ev?.tonal_score],['押韵',ev?.rhyme_score]].forEach(([label,value],i)=>{
  const cell=cw/3,center=col+(i+.5)*cell;
  if(cell<scoreSize*4.1)diagnosis+=text(label,center,scoreTop,scoreSize*.9,scoreFamily,'middle','#73756d')+text(score(value),center,scoreTop+scoreSize*1.4,scoreSize,scoreFamily,'middle');
  else diagnosis+=text(label,center-cell*.43,scoreTop,scoreSize*.9,scoreFamily,'start','#73756d')+text(score(value),center+cell*.43,scoreTop,scoreSize,scoreFamily,'end');
 });
 if(!ev)diagnosis+=text(d?'正文已修改':'暂无评分',col,scoreTop+scoreSize*(cw/3<scoreSize*4.1?3:1.8),scoreSize*.85,scoreFamily);
 return `${defs}${decor}<g data-card-title="true">${heading}</g><g data-effective-size="${size}" data-body-x="${left}" data-body-top="${top}" data-body-width="${right-left}" data-body-height="${bottom-top}" data-overflow="${occupied>bottom-top}" data-body-lines="${lines.length}">${body}</g><g data-card-signature="true">${signature}</g>${renderSeal(s,logoX+logoSize+u*.015,logoY+logoSize*.2,u*.025)}<g data-card-diagnostic="true">${diagnosis}</g>`;
}

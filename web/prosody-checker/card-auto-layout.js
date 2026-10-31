import { getCardFormat, LETTERFORM_PROFILES, wrapVerseText } from './card-scene.js';

/** 根据画幅、诗行长度与内容密度选取初始字号，保留诗句的自然分行。 */
export function recommendCardTypography(spec) {
  const {width, height}=getCardFormat(spec.layout);
  const compact=['willow-poet','moon-window','side-landscape'].includes(spec.layout);
  const widthRatio=spec.layout==='poetry-scroll'?.72:spec.layout==='willow-poet'?.51:spec.layout==='side-landscape'?.50:.70;
  const availableWidth=width*widthRatio;
  const availableHeight=height*(spec.layout==='moon-window'?.39:.52);
  const family=LETTERFORM_PROFILES[spec.font]?.family || LETTERFORM_PROFILES.song.family;
  const paragraphs=[spec.sequence,spec.body].filter(Boolean).join('\n\n');
  const leading=1.65,letterSpacing=1;
  let fontSize=compact?32:36;
  for(;fontSize>24;fontSize--){
    const lines=wrapVerseText(paragraphs,{maxWidth:availableWidth,fontSize,fontFamily:family,letterSpacing});
    const poemLines=String(spec.body||'').split('\n').filter(Boolean);
    const splits=poemLines.some(line=>Array.from(line).length<=24 && wrapVerseText(line,{maxWidth:availableWidth,fontSize,fontFamily:family,letterSpacing}).length>1);
    const fits=spec.layout==='vertical-note'
      ? lines.length*fontSize*leading<=availableWidth
      : fontSize+Math.max(0,lines.length-1)*fontSize*leading<=availableHeight*.88;
    if(fits&&!splits)break;
  }
  const titleFamily=LETTERFORM_PROFILES[spec.titleFont]?.family || family;
  let titleSize=Math.min(44,Math.round(fontSize*1.22));
  while(titleSize>24 && wrapVerseText(spec.title,{maxWidth:availableWidth,fontSize:titleSize,fontFamily:titleFamily,letterSpacing:3}).length>2)titleSize--;
  return {fontSize,titleSize,authorSize:Math.max(16,Math.round(fontSize*.62)),leading,letterSpacing,autoFit:true,regions:{}};
}

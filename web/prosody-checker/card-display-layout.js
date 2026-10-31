import {getDatasetJson} from './dataset-cache.js';
const plain=value=>Array.from(value.replace(/[\p{P}\p{Z}\s]/gu,''));
/** 结构完全匹配时才使用词谱显示行，保留正文全部字词。 */
export function arrangeCardText(body,variant,layout){
 const chars=plain(body),keys=Object.keys(variant).filter(k=>/^stanza\d+$/.test(k)).sort((a,b)=>Number(a.slice(6))-Number(b.slice(6)));
 const lengths=keys.flatMap(k=>variant[k].lines.map(line=>Array.from(line.replace(/[^平仄中]/gu,'')).length));
 if(lengths.reduce((a,b)=>a+b,0)!==chars.length)return null;
 let offset=0;const sections=[];
 for(const key of keys){
  const clauses=variant[key].lines.map(line=>{const n=Array.from(line.replace(/[^平仄中]/gu,'')).length;const text=chars.slice(offset,offset+n).join('');offset+=n;return text;});
  const rows=layout.stanzas?.[key];if(!rows)return null;
  const used=rows.flatMap(row=>row.sentences.map(s=>s.line));
  if(used.length!==clauses.length||used.some((n,i)=>n!==i+1))return null;
  sections.push(rows.map(row=>row.sentences.map(s=>clauses[s.line-1]+(s.punctuation||'')).join('')).join('\n'));
 }
 const result=sections.join('\n\n');
 return plain(result).join('')===chars.join('')?result:null;
}
export async function resolveCardDisplay(body,title,formName='',variantName=''){
 const index=await getDatasetJson('meter','index.json');
 const name=String(formName||title).split(/[·・（(\s]/u)[0];
 const entry=index.find(item=>item.name===name||(item.aliases||[]).includes(name));if(!entry)return null;
 const [meter,layouts]=await Promise.all([getDatasetJson('meter',entry.file),getDatasetJson('meter','display-layouts.json')]);
 const variants=[...meter.variants].sort((a,b)=>Number(b.name===variantName||String(b.source_id)===String(variantName))-Number(a.name===variantName||String(a.source_id)===String(variantName))||Number(b.source_id===meter.default_variant_source_id)-Number(a.source_id===meter.default_variant_source_id));
 for(const variant of variants){if(variantName&&variant.name!==variantName&&String(variant.source_id)!==String(variantName))continue;const layout=layouts.items?.[meter.name]?.[String(variant.source_id)];if(!layout)continue;const result=arrangeCardText(body,variant,layout);if(result!==null)return result;}
 return null;
}

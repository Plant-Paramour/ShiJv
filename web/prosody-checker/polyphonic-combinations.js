import {evaluateTang,evaluatePailv,evaluateSongci,evaluateHaiku} from './core.js?v=22';

// 每个字位使用独立的汉字占位，允许同一个字在一句中分别取不同声调。
export async function evaluateCombinations(result,type,variant,lexicon,options) {
  const output=[];
  for(const [li,line] of result.lines.entries()) {
    const positions=line.characters.map((c,i)=>({c,i})).filter(({c})=>c.polyphonic&&c.tones.length>1);
    if(!positions.length)continue;
    const count=positions.reduce((n,{c})=>n*c.tones.length,1);
    if(count>1024){output.push(`第 ${li+1} 句多音字组合超过 1024 种，未展开，请逐字核对。`);continue;}
    const aliases=new Map();let code=0x3400;
    const encoded=result.lines.map((l,lineIndex)=>l.characters.map((c,ci)=>{
      const token=String.fromCodePoint(code++);aliases.set(token,{char:c.char,lineIndex,ci});return token;
    }));
    let validCount=0;
    for(let number=0;number<count;number++) {
      let cursor=number;const forced=new Map();
      for(const {c,i} of positions){forced.set(i,c.tones[cursor%c.tones.length]);cursor=Math.floor(cursor/c.tones.length);}
      const book={lookup(token){const at=aliases.get(token);const entry=lexicon.lookup(at?.char||token);const tone=at?.lineIndex===li?forced.get(at.ci):null;if(!tone)return entry;
        const parts=entry.partsByTone.get(tone)||new Set();return {...entry,tones:new Set([tone]),parts,partsByTone:new Map([[tone,parts]]),readings:new Set([...parts].map(p=>tone+':'+p)),polyphonic:false};}};
      const text=encoded.map(l=>l.join('')).join('，');
      const opts={...options,charCount:result.stats.charCount,form:result.lines.length===4?'jueju':'lvshi',firstLineEndTone:result.stats.firstLineEndTone};
      const checked=type==='songci'?evaluateSongci(text,variant,book,opts):type==='pailv'?evaluatePailv(text,book,opts):type==='haiku'?evaluateHaiku(text,book,opts):evaluateTang(text,opts,book);
      const faults=[...(checked.violations||[]),...(checked.rhymeViolations||[])].filter(v=>v.lineIndex===li);
      const label=positions.map(({c,i})=>`第${i+1}字“${c.char}”为${forced.get(i)}声`).join('、');
      const reasons=[...new Set(faults.map(v=>`第${v.charIndex+1}字：${v.rule||'押韵不符'}${v.required?'（要求'+v.required+'）':''}`))];
      if(!reasons.length){validCount++;output.push(`第 ${li+1} 句：${label}，符合此句平仄及押韵检查要求。`);}
      if(number%32===31)await new Promise(resolve=>setTimeout(resolve,0));
    }
    if(!validCount)output.push(`第 ${li+1} 句：多音字的所有平仄组合均不符合当前格律要求。`);
  }
  return output;
}

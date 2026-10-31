import {escapeXml as e} from './card-scene.js';
let cropSequence=0;
export function renderSeal(s,x,y,size=36){
 if(!s.showSeal)return '';
 if(s.sealImage){
  const crop=s.sealCrop||'original';
  const image=`<image href="${e(s.sealImage)}" x="${x}" y="${y}" width="${size}" height="${size}" preserveAspectRatio="xMidYMid ${crop==='original'?'meet':'slice'}"/>`;
  if(crop==='original')return image;
  const clip=crop==='circle'?`<circle cx="${size/2}" cy="${size/2}" r="${size/2}"/>`:`<rect width="${size}" height="${size}" rx="${crop==='rounded'?size*.18:0}"/>`;
  const id=`seal-crop-${++cropSequence}`;
  return `<svg x="${x}" y="${y}" width="${size}" height="${size}" viewBox="0 0 ${size} ${size}" overflow="hidden"><defs><clipPath id="${id}" clipPathUnits="userSpaceOnUse">${clip}</clipPath></defs><image href="${e(s.sealImage)}" width="${size}" height="${size}" preserveAspectRatio="xMidYMid slice" clip-path="url(#${id})"/></svg>`;
 }
 const red='#ac4234',outline=s.sealStyle==='outline',round=s.sealStyle==='round';
 const shape=round?`<circle cx="${x+size/2}" cy="${y+size/2}" r="${size/2-1}"/>`:`<rect x="${x+1}" y="${y+1}" width="${size-2}" height="${size-2}" rx="2"/>`;
 const chars=Array.from(s.sealText||'印').slice(0,4),n=chars.length;
 const glyphs=chars.map((c,i)=>{const col=n>2?i%2:0,row=n>2?Math.floor(i/2):i;return `<text x="${n>2?x+size*(.29+col*.42):x+size/2}" y="${n===1?y+size*.72:n>2?y+size*(.43+row*.42):y+size*(.43+row*.42)}" text-anchor="middle" font-family="serif" font-size="${size*(n===1?.65:.39)}" fill="${outline?red:'#fff4df'}">${e(c)}</text>`;}).join('');
 return `<g fill="${outline?'none':red}" stroke="${red}" stroke-width="1.5">${shape}</g>${glyphs}`;
}

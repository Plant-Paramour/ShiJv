import test from 'node:test';
import assert from 'node:assert/strict';
import {getDatasetJson,getMeterBundle} from '../dataset-cache.js';

test('each read requests fresh local data, and a failed read can retry',async()=>{
  const original=globalThis.fetch;
  let value=1, fail=false; const calls=[];
  globalThis.fetch=async(url,options)=>{calls.push({url:String(url),options});return {ok:!fail,status:500,json:async()=>({value})};};
  try{
    assert.deepEqual(await getDatasetJson('meter','钗头凤.json'),{value:1});
    value=2;
    assert.deepEqual(await getDatasetJson('meter','钗头凤.json'),{value:2});
    fail=true;await assert.rejects(getDatasetJson('meter','钗头凤.json'));
    fail=false;
    const bundle=await getMeterBundle(['水调歌头.json','钗头凤.json']);
    assert.deepEqual(bundle.map(x=>x.file),['水调歌头.json','钗头凤.json']);
    assert.ok(bundle.every(x=>x.data.value===2));
    assert.equal(calls.length,5);
    assert.ok(calls.every(x=>x.options.cache==='no-store'&&x.url.includes('/Songci_Meter/')));
  }finally{globalThis.fetch=original;}
});

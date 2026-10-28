const test=require('node:test'), assert=require('node:assert/strict'), fs=require('node:fs'), vm=require('node:vm'), path=require('node:path');
const {webcrypto}=require('node:crypto');
const source=fs.readFileSync(path.join(__dirname,'../static/opencode-manager.js'),'utf8');
const A={backend:'opencode',sessionId:'ses_managerAAAA',witness:'a'.repeat(64)}, B={...A,sessionId:'ses_managerBBBB',witness:'b'.repeat(64)};
const native='msg_123456789abcABCDEFGHIJKLMN';
function setup({store=new Map(),lose=false,conflict=false}={}) {
  const calls=[],records=new Map(); let fail=false;
  const storage={get length(){return store.size},key:i=>[...store.keys()][i],getItem:k=>store.get(k)??null,
    setItem:(k,v)=>{if(fail)throw Error('quota');store.set(k,v)}};
  const env=x=>({schemaVersion:1,backend:'opencode',...x});
  const fetch=async(url,options={})=>{
    const body=options.body&&JSON.parse(options.body); calls.push({url,body});
    let data;
    if (url.endsWith('/intent')) {
      const saved=[...store.values()].map(v=>JSON.parse(v)).find(r=>r.operationId===body.operationId);
      assert.equal(saved.intent.original,body.original); assert.equal(saved.phase,'preparing');
      data=env({operationId:body.operationId,kind:body.kind,intent:body,nativeId:native,state:'prepared',acknowledged:false}); records.set(body.operationId,data);
    } else if (url.endsWith('/send')) {
      const saved=[...store.values()].map(v=>JSON.parse(v)).find(r=>r.operationId===body.operationId);
      assert.equal(saved.nativeId,native); assert.equal(saved.phase,'posting');
      data={...records.get(body.operationId),state:conflict?'conflict':'pending',acknowledged:!conflict}; records.set(body.operationId,data);
      if(lose)throw Error('lost reply after native admission');
    } else if (url.includes('/receipt?')) data=records.get(new URL(url,'http://fixture').searchParams.get('operationId'));
    else throw Error('unexpected route '+url);
    return {ok:true,status:200,json:async()=>data};
  };
  const ctx={crypto:webcrypto,TextEncoder,Date,URLSearchParams,fetch,localStorage:storage}; vm.createContext(ctx); vm.runInContext(source,ctx);
  const create=()=>ctx.opcOpenCodeManager.create({prefix:'fixture',storage,fetch});
  return {api:create(),create,store,calls,records,fail:()=>fail=true};
}
const fields=(target=A,original='  first 😀\nlast Ω  ')=>( {target,original,submitted:original.trim(),delivery:'steer'} );

test('complete long Unicode original and native id are saved/read back before their one POST',async()=>{
  const f=setup(), original=' \n'+('Dictation 😀中\n'.repeat(110000))+'TAIL Ω\n ';
  const record=await f.api.begin('send',fields(A,original),{page:'page-one'});
  assert.equal(record.intent.original,original); assert.equal(record.nativeId,native);
  const result=await f.api.execute(record);
  assert.equal(f.api.acknowledged(result),true); assert.equal(result.intent.original,original);
  assert.equal(f.api.pending(B),null); assert.equal(f.calls.filter(c=>c.url.endsWith('/send')).length,1);
});
test('reply loss, another tab and reload only query the same receipt; no second POST',async()=>{
  const f=setup({lose:true});
  let record=await f.api.begin('send',fields()); record=await f.api.execute(record);
  assert.equal(f.api.acknowledged(record),true);
  const reloaded=f.create(), recovered=reloaded.entries('send',A)[0];
  await reloaded.execute(recovered); await reloaded.poll(recovered); await f.api.execute(record);
  assert.equal(f.calls.filter(c=>c.url.endsWith('/send')).length,1);
  assert.ok(f.calls.filter(c=>c.url.includes('/receipt?')).every(c=>c.url.endsWith(record.operationId)));
});
test('even a recovered prepared intent cannot automatically execute after a reload',async()=>{
  const f=setup(); await f.api.begin('send',fields());
  const reload=f.create(); await reload.execute(reload.entries('send',A)[0]);
  assert.equal(f.calls.filter(c=>c.url.endsWith('/send')).length,0);
});
test('a native first-admission conflict is never an ACK and holds only that exact target',async()=>{
  const f=setup({conflict:true}); const r=await f.api.execute(await f.api.begin('send',fields()));
  assert.equal(f.api.acknowledged(r),false); assert.equal(f.api.pending(A).operationId,r.operationId);
  assert.equal(f.api.pending(B),null); assert.equal(f.api.pending({...A,backend:'claude'}),null);
  f.api.release(r); assert.equal(f.api.pending(A),null);
  await f.api.poll(r); assert.equal(f.api.pending(A),null); // an old tab cannot undo a release
  assert.equal(f.api.entries('send',A)[0].intent.original,fields().original);
});
test('storage failure is before preparation or execution, never an unjournaled Send',async()=>{
  const f=setup(); f.fail(); await assert.rejects(()=>f.api.begin('send',fields()),/quota/); assert.equal(f.calls.length,0);
});
test('receipt target/body/id mismatch is uncertainty, never ACK or a resend',async()=>{
  const f=setup(); const r=await f.api.begin('send',fields());
  f.records.set(r.operationId,{...f.records.get(r.operationId),intent:{...r.intent,submitted:'different body'},acknowledged:true,state:'delivered'});
  const result=await f.api.poll(r);
  assert.equal(result.phase,'uncertain'); assert.equal(f.api.acknowledged(result),false);
  assert.match(result.error,/exact intent/); assert.equal(f.calls.filter(c=>c.url.endsWith('/send')).length,0);
});

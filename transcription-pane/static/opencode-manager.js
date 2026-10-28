/* Shared protocol/journal mechanics only. app.js and m.js retain target, draft and UI ownership. */
'use strict';
(function(root) {
  const same=(a,b)=>!!a && !!b && a.backend === 'opencode' && b.backend === 'opencode' &&
    ['sessionId','witness'].every(k=>a[k] === b[k]);
  const canonical=value=>JSON.stringify(value && typeof value === 'object' ?
    Array.isArray(value) ? value.map(v=>JSON.parse(canonical(v))) : Object.fromEntries(Object.keys(value).sort().map(k=>[k,JSON.parse(canonical(value[k]))])) : value);
  const valid=t=>t?.backend === 'opencode' && /^ses_[A-Za-z0-9]{8,64}$/.test(t.sessionId || '') && /^[a-f0-9]{64}$/.test(t.witness || '');
  async function request(path,body,signal,fetcher=root.fetch.bind(root)) {
    const response=await fetcher('/api/opencode/'+path,{method:body === undefined ? 'GET' : 'POST',cache:'no-store',signal,
      ...(body === undefined ? {} : {headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})});
    const data=await response.json();
    if (!response.ok) { const e=new Error(data.error || 'OpenCode HTTP '+response.status); e.status=response.status; throw e; }
    if (data?.schemaVersion !== 1 || data.backend !== 'opencode') throw new Error('OpenCode response schema/backend mismatch');
    return data;
  }
  // @beacon[
  //   id=opc-pane@createOpenCodeManagerJournal,
  //   slice_labels=opc-pane,
  //   kind=ast,
  //   comment=Shared device-local OpenCode intent journal: unique backend/target/operation slots retain full originals; persist/read back before preparation and the one execute POST; reload/lost replies only query the same native id. UI and draft revisions stay in the existing device adapters.,
  // ]
  function createOpenCodeManagerJournal({prefix,storage=root.localStorage,fetch:fetcher=root.fetch.bind(root)}={}) {
    const base=prefix+':opencode:', req=(path,body,signal)=>request(path,body,signal,fetcher);
    const executable=new Set(); // Only this page's successful begin may make its one execute POST.
    function key(record) { return base+record.kind+':'+(record.intent.target?.sessionId || 'new')+':'+record.operationId; }
    function save(record) {
      const name=key(record), old=storage.getItem(name), prior=old ? JSON.parse(old) : null;
      if (prior) {
        if (canonical(prior.intent) !== canonical(record.intent)) throw new Error('OpenCode journal intent conflict; original retained');
        record={...record,...(prior.releasedAt ? {releasedAt:prior.releasedAt} : {}),
          ...(prior.handledAck ? {handledAck:true} : {}),...(prior.adopted ? {adopted:true} : {})};
      }
      const raw=JSON.stringify(record);
      storage.setItem(name,raw);
      if (storage.getItem(name) !== raw) throw new Error('OpenCode journal readback failed; full draft retained');
      return record;
    }
    function entries(kind=null,target=null) {
      const result=[];
      for (let i=0;i<storage.length;i++) {
        const name=storage.key(i); if (!name?.startsWith(base)) continue;
        if (kind && !name.startsWith(base+kind+':')) continue;
        if (target && !(kind ? [kind] : ['send','control','launch']).some(k=>name.startsWith(base+k+':'+target.sessionId+':'))) continue;
        const record=JSON.parse(storage.getItem(name));
        if (!record?.operationId || !record.intent || !['send','control','launch'].includes(record.kind))
          throw new Error('OpenCode recovery record unreadable; preserve/export browser data before releasing it');
        if ((!kind || record.kind === kind) && (!target || same(record.intent.target,target))) result.push(record);
      }
      return result.sort((a,b)=>(a.createdAt || 0)-(b.createdAt || 0));
    }
    function verify(record,data) {
      if (data.operationId !== record.operationId || data.kind !== record.kind || canonical(data.intent) !== canonical(record.intent) ||
          (record.nativeId && data.nativeId !== record.nativeId) ||
          (record.kind === 'send' && !/^msg_[a-f0-9]{12}[A-Za-z0-9]{14}$/.test(data.nativeId || '')) ||
          (record.kind === 'launch' && !/^ses_[a-f0-9]{12}[A-Za-z0-9]{14}$/.test(data.nativeId || '')))
        throw new Error('OpenCode receipt does not match the exact intent/native id; not acknowledged');
      return data;
    }
    function compact(data) {
      const {intent,...receipt}=data;
      return {...receipt,intentRef:data.operationId}; // Exact body was verified; retain it ONCE in record.intent.
    }
    function observed(record,data) {
      verify(record,data);
      return save({...record,nativeId:data.nativeId,receipt:compact(data),phase:'observed',error:null});
    }
    async function begin(kind,fields,witness={}) {
      const operationId=root.crypto.randomUUID();
      const intent={schemaVersion:1,backend:'opencode',operationId,kind,...fields};
      let record=save({operationId,kind,intent,...witness,createdAt:Date.now(),phase:'preparing'});
      try {
        const data=verify(record,await req('intent',intent));
        record=save({...record,nativeId:data.nativeId,receipt:compact(data),phase:'prepared'});
        executable.add(operationId);
      } catch (e) {
        save({...record,phase:'uncertain',error:e.message}); throw e;
      }
      return record;
    }
    async function poll(record) {
      try { return observed(record,await req('receipt?'+new URLSearchParams({operationId:record.operationId}))); }
      catch (e) { return save({...record,error:e.message,phase:'uncertain'}); }
    }
    async function execute(record) {
      // A recovered/observed prepared record never executes automatically. Only this begin's caller may do it.
      if (record.phase !== 'prepared' || !executable.has(record.operationId)) return poll(record);
      executable.delete(record.operationId);
      record=save({...record,phase:'posting'});
      try {
        const path={send:'send',launch:'launch-manager',control:'control'}[record.kind];
        return observed(record,await req(path,{schemaVersion:1,backend:'opencode',operationId:record.operationId}));
      } catch (e) {
        record=save({...record,phase:'uncertain',error:e.message});
        return poll(record);  // a missing receipt is uncertainty, never a second POST
      }
    }
    function acknowledged(record) {
      const r=record?.receipt;
      return !!r && r.acknowledged === true && !['conflict','uncertain','prepared','accepted'].includes(r.state) &&
        (r.intentRef === record.operationId || r.intent && canonical(r.intent) === canonical(record.intent)) && r.nativeId === record.nativeId;
    }
    function pending(target) {
      return entries('send',target).find(r=>!r.releasedAt && !acknowledged(r) && r.receipt?.state !== 'failed') || null;
    }
    function release(record) { return save({...record,releasedAt:Date.now()}); }
    async function validate(sid,signal) {
      const data=await req('manager?'+new URLSearchParams({id:sid}),undefined,signal);
      if (!valid(data.target) || data.target.sessionId !== sid || data.capabilities?.send !== true)
        throw new Error('OpenCode Manager identity/authority did not validate');
      return data;
    }
    async function context(target,mode='whole',budget=51200,signal) {
      const q=new URLSearchParams({id:target.sessionId,witness:target.witness,historyMode:mode});
      if (mode === 'budgeted') q.set('budgetBytes',String(budget));
      const data=await req('context?'+q,undefined,signal);
      if (!same(data.target,target) || data.historyMode !== mode || typeof data.text !== 'string' ||
          data.bytes !== new TextEncoder().encode(data.text).length || !Array.isArray(data.historyBoundaries))
        throw new Error('OpenCode Refine context identity/schema mismatch; original retained');
      return {...data,turns:data.historyBoundaries.length,lastTokens:[],at:new Date()};
    }
    return {request:req,save,entries,begin,execute,poll,acknowledged,pending,release,validate,context,same,valid};
  }
  function contextLabel(c) {
    if (!c || c.usedTokens == null) return 'OpenCode context unknown';
    return 'OpenCode context '+(c.window ? (100*c.usedTokens/c.window).toFixed(1)+'% · ' : '')+
      c.usedTokens.toLocaleString()+(c.window ? ' / '+c.window.toLocaleString() : ' · window unknown')+
      ' · '+c.basis+(c.measuredAt ? ' · as of '+new Date(c.measuredAt).toLocaleString() : '');
  }
  const api={create:createOpenCodeManagerJournal,request,same,valid,contextLabel};
  root.opcOpenCodeManager=api;
  if (typeof module !== 'undefined') module.exports=api;
})(globalThis);

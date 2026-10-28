// Incidents panel (Resolve…), the settings-unverified warning and the pane-activity leases,
// on the real static/app.js in the offline VM double. Never any live server or network.
const test = require('node:test');
const assert = require('node:assert/strict');
const {pane} = require('./pane-fixture.cjs');
const ref = c=>c.repeat(32);
const uuid='11111111-1111-4111-8111-111111111111';
const target=()=>({schemaVersion:1,backend:'claude',targetRef:ref('T'),storeRef:ref('S'),sessionRef:ref('J'),scopeRef:ref('M'),
  leafRef:ref('L'),fileEpoch:ref('E'),uuid,leafUuid:null,source:'host',scope:'main',title:'Registered Manager',
  executionId:'cc-'+uuid,generation:1,role:'Manager',capabilities:{nativeInput:true,controls:true}});
const settle=async()=>{for(let i=0;i<30;i++)await new Promise(r=>setImmediate(r));};
// The DOM double has no createElement/replaceChildren/append; add just enough for the incident rows.
function node(tag){
  return {tagName:tag.toUpperCase(),className:'',textContent:'',title:'',type:'',children:[],dataset:{},onclick:null,
    append(...n){this.children.push(...n);},replaceChildren(...n){this.children=[...n];}};
}
function setup({provider}={}) {
  const f=pane(provider?{provider}:{}),t=target();f.api.state.backend='claude';f.api.state.claudeTarget=t;
  f.ctx.document.createElement=node;
  Object.assign(f.el('claude-incidents'),{hidden:true,dataset:{},children:[],append(...n){this.children.push(...n);},replaceChildren(...n){this.children=[...n];}});
  f.el('claude-effective').dataset={};
  const live={state:'ready',owner:'live',controls_effective:{model:'claude-sonnet-5-5',effort:'high'},pending_message_uuids:[]};
  const receipts=new Map(),calls=[],beacons=[];
  const server={live,receipts,calls,beacons,openIncidents:[],resolveStatus:200};
  // paneActivity's leases go by navigator.sendBeacon (text/plain Blob); record them in call order.
  f.ctx.Blob=Blob;
  f.ctx.navigator.sendBeacon=(url,blob)=>{const entry={url,blob};calls.push(entry);beacons.push(entry);return true;};
  const env=body=>({schemaVersion:1,backend:'claude',targetRef:t.targetRef,executionId:t.executionId,generation:t.generation,uuid:t.uuid,...body});
  const response=(data,status=200)=>({ok:status<400,status,text:async()=>JSON.stringify(data)});
  f.ctx.fetch=async(url,options)=>{
    const body=options?.body&&JSON.parse(options.body);calls.push({url,body});
    if(url.startsWith('/api/claude/state?'))return response(env({liveState:live,openIncidents:server.openIncidents}));
    if(url==='/api/claude/send'){
      const receipt={execution_id:t.executionId,generation:t.generation,message_uuid:body.messageUuid,original:body.original,submitted:body.submitted,
        durable:true,acknowledged:false,started:false,completed:false,status:'accepted'};
      receipts.set(body.messageUuid,receipt);return response(env({receipt}));
    }
    if(url.startsWith('/api/claude/receipt?'))return response(env({receipt:receipts.get(new URLSearchParams(url.split('?')[1]).get('messageUuid'))||null}));
    if(url==='/api/claude/resolve-incident'){
      if(server.resolveStatus!==200)return response({error:'that incident is no longer open for this Manager'},server.resolveStatus);
      return response(env({status:'resolved',incidentId:body.incidentId,disposition:body.disposition}));
    }
    if(provider&&url.startsWith('https://'))return f.providerResponse(url,options);
    throw new Error('unexpected '+url);
  };
  return Object.assign(f,{t,server});
}
const posts=(f,url)=>f.server.calls.filter(c=>c.url===url);
const leases=async f=>Promise.all(f.server.beacons.map(async b=>({url:b.url,type:b.blob.type,body:JSON.parse(await b.blob.text())})));

test('settings unverified: the badge warns with the host disclosure and the observation says Send goes through',async()=>{
  const f=setup();
  Object.assign(f.server.live,{settings_unverified:true,settings_unverified_disclosure:'get_settings readback timed out'});
  await f.api.pollClaudeManager();
  const badge=f.el('claude-effective');
  assert.equal(badge.textContent,'claude-sonnet-5-5 · thinking high · ⚠ settings unverified');
  assert.equal(badge.dataset.state,'unverified');
  assert.match(badge.title,/get_settings readback timed out/);
  assert.match(f.el('claude-manager-observation').textContent,/Settings unverified.*Send goes through/);
  // Without a disclosure the title still says why, generically.
  delete f.server.live.settings_unverified_disclosure;await f.api.pollClaudeManager();
  assert.match(badge.title,/no usable readback/);assert.equal(badge.dataset.state,'unverified');
});

test('a positive settings mismatch is never shown as merely unverified',async()=>{
  const f=setup();
  Object.assign(f.server.live,{settings_unverified:true,settings_mismatch:true,settings_unverified_disclosure:'readback timed out'});
  await f.api.pollClaudeManager();
  const badge=f.el('claude-effective');
  assert.doesNotMatch(badge.textContent,/unverified/);assert.match(badge.textContent,/ · pending$/);
  assert.equal(badge.dataset.state,'pending');
  assert.doesNotMatch(f.el('claude-manager-observation').textContent,/Send goes through, warned/);
  // Verified readback wins over a stale unverified flag.
  const g=setup();Object.assign(g.server.live,{settings_unverified:true,setting_control_state:'applied'});
  await g.api.pollClaudeManager();
  assert.equal(g.el('claude-effective').textContent,'claude-sonnet-5-5 · thinking high · applied');
  assert.equal(g.el('claude-effective').dataset.state,'applied');
});

test('open incidents render one row each with Resolve…, and Resolve posts exactly the identity, id and disposition',async()=>{
  const f=setup();
  f.server.live.pending_escalations=['inc-a','inc-b'];
  f.server.openIncidents=[{incidentId:'inc-a',category:'capacity_error',time:1760000000,summary:'quota reached'},
                          {incidentId:'inc-b',category:'protocol_attention',time:null,summary:null}];
  await f.api.pollClaudeManager();
  const box=f.el('claude-incidents');
  assert.equal(box.hidden,false);
  assert.equal(box.children.length,2);
  for(const row of box.children){
    assert.equal(row.className,'incident');
    const button=row.children.find(c=>c.tagName==='BUTTON');
    assert.equal(button.textContent,'Resolve…');
  }
  assert.match(box.children[0].children[0].textContent,/⚠ capacity error · .* · quota reached/);
  assert.match(box.children[1].children[0].textContent,/⚠ protocol attention · time unknown$/);
  assert.match(f.el('claude-manager-observation').textContent,/2 open incident\(s\) for you/);
  const prompts=[];f.ctx.prompt=(message,initial)=>{prompts.push([message,initial]);return '  looked at it  ';};
  box.children[0].children.find(c=>c.tagName==='BUTTON').onclick();await settle();
  assert.equal(prompts.length,1);assert.match(prompts[0][0],/capacity error/);
  const resolves=posts(f,'/api/claude/resolve-incident');
  assert.equal(resolves.length,1);
  assert.deepEqual(Object.keys(resolves[0].body).sort(),
    ['backend','disposition','executionId','generation','incidentId','schemaVersion','targetRef']);
  assert.deepEqual(resolves[0].body,{schemaVersion:1,backend:'claude',targetRef:f.t.targetRef,executionId:f.t.executionId,
    generation:1,incidentId:'inc-a',disposition:'looked at it'});
  assert.match(f.el('status').textContent,/Incident resolved \(capacity error\)/);
  // The resolve held a 'resolve' lease around its work.
  const held=await leases(f);
  assert.deepEqual(held.map(l=>[l.body.kind,l.body.active]),[['resolve',true],['resolve',false]]);
  // A cancelled prompt posts nothing; a blank disposition posts nothing.
  f.ctx.prompt=()=>null;box.children[1].children.find(c=>c.tagName==='BUTTON').onclick();await settle();
  f.ctx.prompt=()=>'   ';box.children[1].children.find(c=>c.tagName==='BUTTON').onclick();await settle();
  assert.equal(posts(f,'/api/claude/resolve-incident').length,1);
  assert.match(f.el('status').textContent,/a disposition is required/);
  // A refused resolve says so.
  f.server.resolveStatus=409;f.ctx.prompt=()=>'done';box.children[1].children.find(c=>c.tagName==='BUTTON').onclick();await settle();
  assert.equal(posts(f,'/api/claude/resolve-incident').length,2);
  assert.match(f.el('status').textContent,/Incident not resolved: that incident is no longer open/);
});

test('no open incidents: the incidents box is hidden',async()=>{
  const f=setup();await f.api.pollClaudeManager();
  assert.equal(f.el('claude-incidents').hidden,true);
  assert.equal(f.el('claude-incidents').children.length,0);
});

test('a Send to a Manager whose settings are unverified still POSTs, and the status line warns',async()=>{
  const f=setup();Object.assign(f.server.live,{settings_unverified:true,settings_unverified_disclosure:'readback timed out'});
  f.box.value='go ahead';await f.api.send();
  assert.equal(posts(f,'/api/claude/send').length,1);
  assert.equal(posts(f,'/api/claude/send')[0].body.submitted,'go ahead');
  assert.match(f.el('status').textContent,/settings unverified/);
  assert.match(f.el('status').textContent,/Send went through/);
  // With a positive mismatch the Send line carries no unverified warning.
  const g=setup();Object.assign(g.server.live,{settings_unverified:true,settings_mismatch:true});
  g.box.value='go';await g.api.send();
  assert.doesNotMatch(g.el('status').textContent,/settings unverified/);
});

test('send is wrapped by paneActivity: an active lease before the work, a release after',async()=>{
  const f=setup();f.box.value='leased';await f.api.send();
  const order=f.server.calls.map(c=>c.url.split('?')[0]);
  const first=order.indexOf('/api/pane-activity'),last=order.lastIndexOf('/api/pane-activity');
  assert.ok(first>=0&&first<order.indexOf('/api/claude/state'),'lease precedes the work: '+order.join(','));
  assert.ok(last>order.indexOf('/api/claude/send'),'release follows the work: '+order.join(','));
  const held=await leases(f);
  assert.deepEqual(held.map(l=>[l.url,l.body.kind,l.body.active]),
    [['/api/pane-activity','send',true],['/api/pane-activity','send',false]]);
  assert.equal(held[0].body.id,held[1].body.id);
  assert.deepEqual(Object.keys(held[0].body).sort(),['active','id','kind','schemaVersion']);
  assert.equal(held[0].body.schemaVersion,1);
  assert.match(held[0].body.id,/^[A-Za-z0-9_-]{8,64}$/);   // the server's lease id pattern
  assert.match(held[0].type,/^text\/plain/);
});

test('a failed send still releases its lease',async()=>{
  const f=setup();f.box.value='will fail';
  const fetch=f.ctx.fetch;f.ctx.fetch=async(url,options)=>{if(url==='/api/claude/send')throw new Error('offline');return fetch(url,options);};
  await f.api.send();
  const held=await leases(f);
  assert.deepEqual(held.map(l=>[l.body.kind,l.body.active]),[['send',true],['send',false]]);
});

test('refine is wrapped by paneActivity: an active lease before the provider call, a release after',async()=>{
  const {streamResponse,messageEvents}=require('./pane-fixture.cjs');
  const f=setup({provider:'anthropic'});f.api.state.backend='opencode';
  f.providerResponse=()=>streamResponse(messageEvents({content:[{type:'text',text:'cleaned'}],stop_reason:'end_turn'}));
  f.storage.set('refine_anthropic_model','claude-opus-5-5');f.box.value='original brain dump';
  await f.api.refine();
  const order=f.server.calls.map(c=>c.url.startsWith('https://')?'provider':c.url.split('?')[0]);
  const provider=order.indexOf('provider');
  assert.ok(provider>0,'the refine reached its provider: '+order.join(','));
  assert.ok(order.indexOf('/api/pane-activity')<provider&&order.lastIndexOf('/api/pane-activity')>provider,order.join(','));
  const held=await leases(f);
  assert.deepEqual(held.map(l=>[l.body.kind,l.body.active]),[['refine',true],['refine',false]]);
  assert.equal(f.box.value,'cleaned');
});

test('without sendBeacon the work still runs, unleased',async()=>{
  const f=setup();delete f.ctx.navigator.sendBeacon;f.box.value='no beacon';await f.api.send();
  assert.equal(posts(f,'/api/claude/send').length,1);assert.equal(f.server.beacons.length,0);
});

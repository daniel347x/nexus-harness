/* README capture data. All sessions, identities, prose and measurements below are invented.
 * The capture server injects this transport before the app's unchanged production scripts.
 * No production server, provider, Workflowy account or browser profile is consulted. */
(()=>{
  'use strict';
  // The header's Claude account switcher is left out of the shots (the README shows no account names).
  const hide=document.createElement('style');
  hide.textContent='#claude-account{display:none!important}';
  (document.head||document.documentElement).appendChild(hide);
  const NativeDate=Date, NOW=1793216280000;
  class ShotDate extends NativeDate {constructor(...a){super(...(a.length?a:[NOW]));}static now(){return NOW;}}
  window.Date=ShotDate;
  const now=NOW/1000, bytes=s=>new TextEncoder().encode(s).length;
  const env=x=>({schemaVersion:1,backend:'claude',...x});
  const uid=n=>'10000000-0000-4000-8000-'+String(n).padStart(12,'0');
  const context=tokens=>({state:'measured',occupancy_tokens:tokens,window_tokens:1000000,measured_at:now-35});
  const seat=(n,title,role='worker',extra={})=>({source:'host',role,displayRole:role,uuid:uid(n),executionId:'cc-'+uid(n),generation:1,
    sessionRef:'session-'+n,targetRef:'target-'+String(n).padStart(26,'0'),scopes:[{scopeRef:'scope-'+n,label:'main'}],title,sessionLabel:title,
    cwd:'opencode-companion',createdAt:now-2400+n*10,lifecycle:'live',state:'ready',capabilities:{history:true},
    liveState:{state:'ready',native_session_state:'idle',primary_turn_open:false,pending_message_uuids:[],background_tasks:{},activity:{state:'idle',lastEventAt:now-8}},
    turnFacts:{status:'complete',modelsComplete:true,models:['claude-opus-5-5'],levelsComplete:true,levels:['high'],firstAt:now-2400+n*10,lastAt:now-60},...extra});
  const busy={state:'ready',native_session_state:'running',primary_turn_open:true,pending_message_uuids:[],background_tasks:{}};
  const manager=seat(1,'Manager-Session-228','Manager',{displayRole:'manager',capabilities:{history:true,nativeInput:true,controls:true,reconnect:true},lastContext:context(284000)});
  const target=(row,authority=false)=>({schemaVersion:1,backend:'claude',...row,storeRef:'store-readme',scopeRef:row.scopes[0].scopeRef,scope:'main',
    leafRef:authority?null:'leaf-readme',fileEpoch:authority?null:'epoch-readme',leafUuid:null,contextIdentity:null,status:authority?'authority':'complete'});
  const older=seat(2,'Manager-Session-227','Manager',{displayRole:'manager',lifecycle:'sleeping',capabilities:{history:true,reconnect:true},lastContext:context(419000)});
  const core='MCI012';
  const prog=(n,title,role,conductorN,extra={})=>seat(n,title,role,{socket:'[ap:MC'+String(n).padStart(4,'0')+']',
    lineage:{program:core,kind:role==='conductor'?'conductor':'seat',conductorN,managerSession:'228',title,
      ...(role==='helper'?{parentSeat:'cc-'+uid(12)}:{})},...extra});
  const sessions=[manager,older,
    prog(10,'[ap:MCI012]-001 · MCI · Mutation Coherence Infrastructure','conductor',1,{lifecycle:'ended',createdAt:now-3*86400}),
    prog(11,'Wave 1 · mutation receipts · complete','worker',1,{lifecycle:'ended',createdAt:now-3*86400+100}),
    prog(20,'[ap:MCI012]-002 · MCI · Mutation Coherence Infrastructure','conductor',2,{liveState:{...busy,primary_turn_open:false,background_tasks:{doorbell:{task_type:'local_bash',tool_input:{command:'bash scripts/oc_seat.sh wait cc-'+uid(12)+' --max 540',description:'Wait for wave 2'}}}}}),
    prog(12,'Wave 2 · coherent reads','worker',2,{liveState:busy,createdAt:now-1800}),
    prog(13,'Receipt edge cases','helper',2,{liveState:busy,createdAt:now-1700}),
    prog(14,'Audit A · mutation ordering · PASS','worker',2,{lifecycle:'ended',createdAt:now-1600}),
    prog(15,'Audit B · reader coherence','worker',2,{liveState:busy,createdAt:now-1500}),
    prog(16,'HALT · choose the rollout order','worker',2,{createdAt:now-1400}),
    seat(30,'[ap:READM1]-001 · Companion README','conductor',{liveState:busy,
      lineage:{program:'READM1',kind:'conductor',conductorN:1,quickProgram:true,managerSession:'228',title:'[ap:READM1]-001 · Companion README'}}),
    seat(31,'Screenshots and public examples','worker',{liveState:busy,socket:'[ap:SHOTS1]',lineage:{program:'READM1',kind:'seat',conductorN:1,quick:true,title:'Screenshots and public examples'}}),
    seat(40,'[ap:PHONE1]-001 · Phone thinking blocks','conductor',{lifecycle:'ended',createdAt:now-86400,
      lineage:{program:'PHONE1',kind:'conductor',conductorN:1,quickProgram:true,managerSession:'227',title:'[ap:PHONE1]-001 · Phone thinking blocks'}}),
    ...['Sharper tool previews','Favorite programs','Read Aloud headings'].map((title,i)=>seat(50+i,'[ap:QUICK'+i+']-001 · '+title,'conductor',{
      lifecycle:'ended',createdAt:now-5000-i*100,lineage:{program:'QUICK'+i,kind:'conductor',conductorN:1,quickProgram:true,managerSession:'228',title:'[ap:QUICK'+i+']-001 · '+title}}))];
  const str=v=>({kind:'string',characters:Array.from(String(v)).length,jsonCharacters:JSON.stringify(String(v)).length,head:String(v).slice(0,128),tail:String(v).slice(-128)});
  const val=v=>v&&typeof v==='object'?{kind:'object',entries:Object.entries(v).map(([k,x])=>[k,val(x)])}:typeof v==='boolean'?{kind:String(v)}:str(v);
  const preview=entries=>({schemaVersion:1,status:'available',value:{kind:'object',entries:entries.map(([k,v])=>[k,val(v)])}});
  const call=(id,name,entries,size=2300)=>({id,block:0,callRef:'call-'+id,name,status:'stored',previewData:preview(entries),
    inputBytes:bytes(JSON.stringify(Object.fromEntries(entries))),outputBytes:size,time:now-360,resultTime:now-358.6});
  const calls={
    peek:call('peek','mcp__workflowy__anchor',[['verb','dock.peek'],['params',{dock_handle:'[ap:MCI012]',recursive:true}]]),
    receipt:call('receipt','Bash',[['command','bash scripts/oc.sh seat info cc-'+uid(12)],['description','Read the wave 2 worker receipt']],814),
    node:call('node','mcp__workflowy__node',[['verb','read'],['params',{node_handle:'[ap:AUDIT1]'}]],1850),
    write:call('write','mcp__workflowy__node',[['verb','append_note'],['params',{node_handle:'[ap:HALT01]',text:'HALT: choose the rollout order. Both audits and the rollback plan are attached.'}]],143),
  };
  const event=(seq,kind,text,extra={})=>({seq,uuid:'event-'+seq,recordRef:'record-'+seq,scope:'main',kind,status:'stored',text,
    calls:[],thinking:[],time:now-900+seq*55,model:kind==='assistant'?'claude-opus-5-5':null,effort:kind==='assistant'?'high':null,...extra});
  const events=[
    event(0,'user','Show me where MCI stands. Keep the independent audits separate, and bring me the decision before the rollout.'),
    event(1,'assistant','I’ll check the Conductor’s records and the worker receipts, then bring you the decision with its evidence.'),
    event(2,'assistant','',{calls:[calls.peek,calls.receipt]}),
    event(3,'assistant','',{calls:[calls.node]}),
    event(4,'assistant','## MCI · wave 2 is in flight\n\nThe Conductor has two independent audit tracks. **Mutation ordering passed**; reader coherence is still running.\n\n| Track | State |\n| --- | --- |\n| Coherent reads | implementing |\n| Audit A · mutation ordering | passed |\n| Audit B · reader coherence | reviewing |\n\nThe worker’s helper is checking receipt edge cases. Every result lands in its own socket.'),
    event(5,'user','Good. What will you need from me?'),
    event(6,'assistant','',{calls:[calls.write]}),
    event(7,'assistant','## HALT · your rollout decision\n\n**Reader first, or writer first?**\n\nI recommend **reader first**: it understands both receipt formats while the writer changes.\n\nThe independent audits and rollback plan are in **[ap:HALT01]**. The workers can finish their checks while you choose.')
  ];
  const doc='# MCI · Mutation Coherence Infrastructure\n\nOne program, many focused workers. The Conductor keeps the contract, waves and audits together; each worker leaves a durable result.\n\n## At a glance\n\n| Wave | Work | State |\n| --- | --- | --- |\n| 1 | Mutation receipts and ordering | complete |\n| 2 | Coherent reads and edge cases | running |\n| 3 | Rollout and follow-through | waiting for decision |\n\n## Independent audits\n\n- **Audit A · mutation ordering — PASS.** Retries keep one receipt; readers never see a half-published mutation.\n- **Audit B · reader coherence — reviewing.** Two readers, one writer, delayed and repeated replies.\n\n## HALT · choose the rollout order\n\n**Recommendation: reader first.** Let readers understand both receipt formats before switching the writer.\n\nThe alternative is writer first, keeping the compatibility reader in place. Both routes have a rollback point.\n\n> The human chooses the direction. The Conductor keeps the work moving and the evidence together.\n\n## Durable records\n\nEvery unit has a socket, a result and its checks. A later Conductor can pick up the program without reconstructing it from chat.\n';
  const rowNames=[[0,'MCI · Mutation Coherence Infrastructure'],[1,'SkyView · purpose and scale'],[1,'MasterSpec · LOCKED'],[1,'Wave 1 · mutation receipts · complete'],[1,'Wave 2 · coherent reads · running'],[2,'Audit A · mutation ordering · PASS'],[2,'Audit B · reader coherence · reviewing'],[1,'HALT · choose the rollout order']];
  const rows=rowNames.map(([depth,name],i)=>({uuid:uid(100+i),ancestor_path:[],occurrence:0,depth,name,created:now-86400+i*600,modified:now-600+i*60}));
  const identity={storeRef:'store-readme',sessionRef:manager.sessionRef,scopeRef:'scope-1',leafRef:'leaf-readme',recordRef:'record-2',sessionUuid:manager.uuid,
    scope:'main',leafUuid:'event-7',recordUuid:'event-2',recordHash:'a'.repeat(64),blockIndex:0,callId:'peek',callRef:calls.peek.callRef,fileEpoch:'epoch-readme'};
  const selection={backend:'claude',source:'host',toolName:calls.peek.name,callRef:calls.peek.callRef,fileEpoch:'epoch-readme',identity};
  const rawInput=JSON.stringify({verb:'dock.peek',params:{dock_handle:'[ap:MCI012]',recursive:true}},null,2);
  const rawOutput='# MCI · wave 2\n\n## Running\n\n- Conductor -002 · waiting on workers\n- Coherent reads · implementing\n- Receipt edge cases · checking\n- Audit B · reader coherence\n\n## Complete\n\n- Wave 1 · mutation receipts\n- Audit A · mutation ordering\n\n## Human decision\n\n**HALT:** choose the rollout order.\n\n[ap:HALT01] · reader first or writer first\n';
  const snapshot=env({callRef:calls.peek.callRef,fileEpoch:'epoch-readme',provenance:'host-durable-feed',input:rawInput,output:rawOutput,
    inputBytes:bytes(rawInput),outputBytes:bytes(rawOutput),inputAvailability:'stored',outputAvailability:'stored',status:'stored',live:false,terminal:true,inputFinal:true,time:now-360,resultTime:now-358.6});
  const pins={'program:MCI012':{pinned_at:now},'program:PHONE1':{pinned_at:now-86400},'program:READM1':{pinned_at:now-900}};
  const tele=Object.fromEntries(sessions.filter(s=>s.lineage).map((s,i)=>{
    const implementer=s.role==='worker'&&!/Audit|HALT/.test(s.title),live=s.liveState.primary_turn_open;
    const short=implementer?'42.1 (0) → 78.4 (1) → (164 KT, 7 min) → —':'context 35.6 → '+(92+i*13)+' KT · '+(6+i)+' min';
    return [s.executionId,{sid:s.executionId,backend:'claude',role:implementer?'IMPLEMENTER':s.role.toUpperCase(),roleSource:'title',phases:implementer,
      segments:[{key:s.executionId+'#0',segment:0,kind:'fresh',label:'fresh',full:short,short,line:short,flags:[],notes:[],live}]}];
  }));
  const usage={schemaVersion:1,backend:'usage',now,status:'ready',refreshing:false,observedAt:now-40,ageSeconds:40,
    claude:{ok:true,windows:[{label:'five-hour',kind:'session',percent:34,resets_at:now+10800},{label:'weekly',kind:'weekly_all',percent:52,resets_at:now+3*86400},{label:'Fable weekly',kind:'weekly_scoped',percent:23,resets_at:now+3*86400}]},
    chatgpt:{ok:true,windows:[{label:'weekly',percent:39,resets_at:now+4*86400}],stale:false,observed_at:now-40,age_seconds:40}};
  const store=new Map([['pane_backend_v1','claude'],['pane_main_split_v1','28'],['pane_tool_split_v1','48'],['pane_sidebar_width','47'],
    ['pane_draft','Reader first sounds right. Keep the two audit results separate and let me know when both are complete.'],
    ['pane_lineage_open',JSON.stringify({'p:MCI012':true,['s:cc-'+uid(10)]:false})]]);
  Object.defineProperty(window,'localStorage',{value:{get length(){return store.size;},key:i=>[...store.keys()][i]??null,
    getItem:k=>store.get(k)??null,setItem:(k,v)=>store.set(k,String(v)),removeItem:k=>store.delete(k)}});
  const requests=[],unknown=[],errors=[];
  window.readmeFixture={sessions,manager,events,requests,unknown,errors,pins,doc,rows};
  addEventListener('error',e=>errors.push(String(e.message||'resource error')));
  addEventListener('unhandledrejection',e=>errors.push(String(e.reason?.message||e.reason)));
  const realFetch=fetch.bind(window),json=d=>new Response(JSON.stringify(d),{headers:{'Content-Type':'application/json'}});
  window.fetch=async(url,options={})=>{
    const u=new URL(String(url),location.href),q=Object.fromEntries(u.searchParams),p=u.pathname;requests.push([p,options.method||'GET']);
    if(!p.startsWith('/api/')&&!p.startsWith('/oc/'))return realFetch(url,options);
    let data;
    if(p==='/api/claude/sessions')data=env({sessions:q.role==='Manager'?sessions.filter(s=>s.role==='Manager'):sessions,status:'complete',hasMore:false,listWindowDays:7,diagnostics:[]});
    else if(p==='/api/claude/turns')data=env({uuid:q.uuid,turnFacts:sessions.find(s=>s.uuid===q.uuid)?.turnFacts??null});
    else if(p==='/api/pins')data={schemaVersion:1,pins};
    else if(p==='/api/usage')data=usage;
    else if(p==='/api/opencode/programs')data={schemaVersion:1,backend:'opencode',status:'complete',observedAt:now,sessions:[],error:null};
    else if(p==='/api/opencode/activity')data={schemaVersion:1,backend:'opencode',status:'complete',observedAt:now,active:[],sessions:[]};
    else if(p==='/api/claude/subagents')data=env({sessionRef:q.sessionRef,sessions:[],hasMore:false,status:'complete'});
    else if(p==='/api/telemetry')data={schemaVersion:1,backend:'telemetry',status:'complete',asOf:now,sessions:tele};
    else if(p==='/api/claude/events'){
      const target=sessions.find(s=>s.sessionRef===q.sessionRef)||manager;
      data=env({sessionRef:target.sessionRef,scopeRef:target.scopes[0].scopeRef,fileEpoch:'epoch-readme',leafRef:'leaf-readme',targetRef:target.targetRef,
        source:'host',role:target.role,executionId:target.executionId,generation:1,targetInfo:{schemaVersion:1,backend:'claude',...target,storeRef:'store-readme',scopeRef:target.scopes[0].scopeRef,scope:'main',leafRef:'leaf-readme',fileEpoch:'epoch-readme',leafUuid:null,contextIdentity:null},events:q.cursor||Number(q.fromSeq)>7?[]:events,
        eventUpdates:[],branches:[],traces:[],diagnostics:[],status:'complete',change:q.cursor?'append':'snapshot',hasMore:false,deltaCursor:'delta-readme',
        liveState:target.liveState,contextReadout:context(284000)});
    }
    else if(p==='/api/claude/target')data=env({status:'authority',targetRef:manager.targetRef,sessionRef:manager.sessionRef,scopeRef:'scope-1',targetInfo:target(manager,true)});
    else if(p==='/api/claude/state')data=env({...manager,liveState:{...manager.liveState,controls_effective:{model:'claude-opus-5-5',effort:'high'},setting_control_state:'applied',setting_control_pending:false},openIncidents:[]});
    else if(p==='/api/claude/context'){
      const prose=events.filter(e=>e.text),text=prose.map(e=>'['+e.kind.toUpperCase()+']\n'+e.text).join('\n\n---\n\n');
      data=env({status:'stored',targetRef:q.targetRef,targetRevision:Number(q.targetRevision),source:'host',executionId:manager.executionId,generation:1,
        storeRef:'store-readme',sessionRef:manager.sessionRef,scopeRef:'scope-1',leafRef:'leaf-'+String(1).padStart(26,'0'),
        fileEpoch:'epoch-'+String(1).padStart(26,'0'),contextIdentity:'context-'+String(1).padStart(26,'0'),leafUuid:null,
        turns:prose.length,users:prose.filter(e=>e.kind==='user').length,readAt:NOW,bytes:bytes(text),text});
    }
    else if(p==='/api/tool-selection')data={epoch:'readme-mail',revision:1,selection};
    else if(p==='/api/tool-record')data=env({epoch:'readme-mail',revision:1,selection,identity,provenance:'host-durable-feed',snapshot});
    else if(p==='/api/claude/tool-record')data=snapshot;
    else if(p==='/api/claude/accounts')data=env({available:true,selected:{label:'personal',identity:{plan:'Max'}},accounts:[{label:'personal',enrolled:true},{label:'work',enrolled:true}],operation:null});
    else if(p==='/api/chatgpt/accounts')data={schemaVersion:1,backend:'chatgpt-accounts',available:true,accounts:[{id:'account-a',label:'OpenAI',method:'oauth',active:true,quota:{state:'mapped',label:'primary'}}],active:{known:true,credential_id:'account-a',credential_label:'OpenAI'},operation:null};
    else if(p==='/api/chatgpt/usage')data={schemaVersion:1,backend:'chatgpt-usage',credentialId:'account-a',quotaState:'mapped',now,observedAt:now,ageSeconds:40,refreshing:false,chatgpt:usage.chatgpt};
    else if(p==='/api/claude/launch-defaults')data=env({name:'Manager-Session-229',model:'claude-opus-5-5',effort:'high'});
    else if(p==='/api/claude/first-messages')data=env({manager:'Manager-Session-{N}',sub:'Manager-Session-{N}-{letter}'});
    else if(p==='/api/claude/glance')data=env({status:'complete',model:'claude-opus-5-5',lastAt:now-30,messages:events.filter(e=>e.text).slice(-3).map(e=>({role:e.kind,time:e.time,text:e.text,chars:e.text.length})),call:null});
    else if(p==='/api/glimpse/catalog')data={schemaVersion:1,docks:[{handle:'[ap:MCI012]',name:'MCI · Mutation Coherence Infrastructure',depth:0}],groups:[],errors:[]};
    else if(p==='/api/glimpse/render'){
      const body=JSON.parse(options.body),markdown=doc;
      data={schemaVersion:1,markdown,bytes:bytes(markdown),renderedAt:'2026-10-28T19:38:00Z',request:body,rows,
        render_spec:{kind:'raw_root',root_uuid:uid(100),group_id:null,group_name:null,focus:null,recursive:true,descend_policy:'stop_at_child_anchors',headings_only:false,
          sort_by:'none',sort_direction:'asc',since:null,recent_by:null,ancestor_notes:false,full_uuids:false,as_markdown:true,content_format:'markdown',tree_mode:'render_tree'}};
    }
    else if(p==='/api/view/roots')data={schemaVersion:1,limit:30,roots:[]};
    else if(p==='/api/pane-activity')data={ok:true};
    else {unknown.push(p);throw Error('No capture fixture for '+p);}
    return json(data);
  };
})();

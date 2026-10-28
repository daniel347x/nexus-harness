// Reuse the existing inert DOM/transport harness, without registering its tests.
const {test}=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const old=fs.readFileSync(path.join(__dirname,'claude-history-gap.test.cjs'),'utf8');
const mod={exports:{}};
vm.runInNewContext(old.slice(0,old.indexOf('// The resident rows'))+'\nmodule.exports={server,fixture};',
  {require,__dirname,module:mod,process,TextEncoder,TextDecoder,AbortController,URLSearchParams,Response,
   setImmediate,queueMicrotask});
const {server,fixture}=mod.exports;
function tailFixture(count=500,host=true) {
  const srv=server();srv.add(count);const handle=srv.handle,requests=[];
  srv.handle=url=>{
    if(url.startsWith('/api/claude/events')) {
      requests.push(url);const q=new URLSearchParams(url.split('?')[1]);
      if(host && !q.has('cursor') && !q.has('fromSeq') && !q.has('leafRef'))q.set('fromSeq',String(Math.max(0,count-100)));
      url='/api/claude/events?'+q;
    }
    return handle(url);
  };
  const row=srv.row();if(!host)row.source='native';
  return {srv,f:fixture(srv),row,requests};
}
test('tail open leaves the start unknown; Load earlier reads fromSeq 300; seq zero is the true start',async()=>{
  const {f,row,requests}=tailFixture();await f.api.browse(row);
  assert.equal(f.api.state.rows.get(f.api.state.order[0]).seq,400);
  assert.equal(f.api.atStart(),false);assert.equal(f.api.state.startKey,undefined);
  await f.api.loadOlder();assert.ok(requests.some(u=>new URLSearchParams(u.split('?')[1]).get('fromSeq')==='300'));
  assert.equal(f.api.state.rows.get(f.api.state.order[0]).seq,300);
  const small=tailFixture(50);await small.f.api.browse(small.row);assert.equal(small.f.api.atStart(),true);
});
test('First in a host tail view issues one fromSeq zero read, never a cursor or leaf pin',async()=>{
  const {f,row,requests}=tailFixture();await f.api.browse(row);requests.length=0;
  await f.api.toFirst();assert.equal(requests.length,1);
  const q=new URLSearchParams(requests[0].split('?')[1]);
  assert.equal(q.get('fromSeq'),'0');assert.equal(q.has('cursor'),false);assert.equal(q.has('leafRef'),false);
  assert.equal(f.api.atStart(),true);assert.equal(f.api.state.rows.get(f.api.state.order[0]).seq,0);
  assert.equal(f.api.state.lastJump.key,f.api.state.order[0]);
});
test('native fromStart retains its branch leaf and the no-cursor address',async()=>{
  const {f,row,requests}=tailFixture(50,false);await f.api.browse(row);requests.length=0;
  const leaf=f.api.state.leaf;await f.api.fromStart();
  const q=new URLSearchParams(requests[0].split('?')[1]);
  assert.equal(q.has('fromSeq'),false);assert.equal(q.has('cursor'),false);assert.equal(q.get('leafRef'),leaf);
});
test('durable tail resync re-adopts every resident node after a handle-only restart',async()=>{
  const {srv,f,row}=tailFixture();await f.api.list();await f.api.browse(row);
  f.api.setFollow(false,{byScroll:true});const nodes=f.api.state.order.map(k=>f.api.state.rows.get(k).node);
  srv.restart();await f.tick(5);
  assert.equal(f.api.state.resync,null);assert.equal(f.api.state.session.sessionRef,'sess2');
  assert.deepEqual(f.api.state.order.map(k=>f.api.state.rows.get(k).node),nodes);
  assert.doesNotMatch(f.el('sessions-notice').textContent,/Reloaded/);
});

// Timer-focused: the one shared elapsed-time helper both conversation pages and the opened inspector use.
// Offline only: the production tool-reading.js in a bare VM (no DOM, network, host or browser).
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const reading=fs.readFileSync(path.join(__dirname,'../static/tool-reading.js'),'utf8');
function fixture(){const ctx={};vm.runInNewContext(reading,ctx);return ctx.opcToolReading;}

test('elapsedText: whole seconds while a call runs, never negative or invented; hours stay readable',()=>{
  const t=fixture(), at=start=>t.elapsedText(start,start);
  assert.equal(at(1000),'0s');
  assert.equal(t.elapsedText(1000,1999),'0s');
  assert.equal(t.elapsedText(1000,2000),'1s');
  assert.equal(t.elapsedText(1000,60000),'59s');
  assert.equal(t.elapsedText(1000,61000),'1m 00s');
  assert.equal(t.elapsedText(1000,3600000),'59m 59s');
  assert.equal(t.elapsedText(1000,3601000),'1h 00m 00s');
  assert.equal(t.elapsedText(1000,3724000),'1h 02m 03s');
  // Ticking once a second moves the text once; a sub-second repaint may not.
  assert.equal(t.elapsedText(5000,5500),'0s');
  assert.equal(t.elapsedText(5000,6000),'1s');
  assert.equal(t.elapsedText(5000,7000),'2s');
  // Unknown, zero, negative and a clock reading before the stored start are unknown, not a negative number.
  for(const [start,now] of [[null,1000],[undefined,1000],[NaN,1000],[0,1000],[-5,1000],[1000,999],[1000,NaN],[1000,Infinity]])
    assert.equal(t.elapsedText(start,now),'',JSON.stringify([start,now]));
});

test('isRunningStatus: the conversation running/streaming states and a live record\'s running; terminal states never',()=>{
  const t=fixture();
  for(const status of ['running','pending','pending-or-missing','not-final-fragment','streaming'])assert.equal(t.isRunningStatus(status),true,status);
  for(const status of ['stored','error','ambiguous','completed','cancelled','aborted',null,undefined,'future-status'])assert.equal(t.isRunningStatus(status),false,String(status));
});

test('callDuration is unchanged: a sane stored start and end only, never a call without an end',()=>{
  const t=fixture(), start=Date.UTC(2026,0,2,3,4,5);
  assert.equal(t.callDuration(start,start+400),'0.4s');
  assert.equal(t.callDuration(start,start+42000),'42s');
  assert.equal(t.callDuration(start,start+65000),'1m 05s');
  assert.equal(t.callDuration(start,start+3723000),'1h 02m 03s');
  assert.equal(t.callDuration(start,NaN),'');
  assert.equal(t.callDuration(start,start-1),'');
  assert.equal(t.callDuration(null,start),'');
  assert.equal(t.callTimeText(start,NaN),'2026-01-01 22:04:05 ET','an opened running call still has its start stamp alone');
});

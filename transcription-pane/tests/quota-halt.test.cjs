process.env.TZ='America/New_York'; // local-time wording, pinned before any Date is made
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
// The usage-limit halt's one line (quota-halt.js), run without a page: the desktop banner, row markers and the phone use it.
const source=fs.readFileSync(path.join(__dirname,'../static/quota-halt.js'),'utf8');
const ctx={};vm.runInNewContext(source,ctx);
const {active,line,windowName,recoveryLine}=ctx.opcQuotaHalt;
const NOW=Date.parse('2026-10-22T13:20:00-04:00'),at=iso=>Date.parse(iso)/1000;
const halt=(over={})=>({status:'halted',generation:2,halt_id:'h1',rate_limit_type:'five_hour',resets_at:at('2026-10-22T14:50:00-04:00'),...over});
const check=(over={})=>({episode_id:'1792829400.000000',resets_at:at('2026-10-22T14:50:00-04:00'),
  due_at:at('2026-10-22T14:55:00-04:00'),status:'scheduled',recipient:null,
  message_uuid:'22222222-2222-4222-8222-222222222222',...over});
const withCheck=(metadata,generation=2)=>active({quota_halt:halt(),quota_recovery_check:metadata},generation);

test('a halted Manager: the window in words, the local reset time, and Send reconnects it',()=>{
  assert.equal(line(halt(),{manager:true,now:NOW}),
    '⛔ Halted: Max usage limit reached (five-hour), resets 14:50. Nothing resumes by itself. Switch accounts or wait for the reset, then Send: it reconnects on the current login.');
});

test('a halted Conductor, seat or worker: its Conductor (or the Manager) resumes it',()=>{
  assert.equal(line(halt(),{now:NOW}),'⛔ Halted: Max usage limit reached (five-hour), resets 14:50. Nothing resumes by itself; its Conductor (or the Manager) resumes it.');
});

test('a reset already passed reads "reset since"; another day adds its weekday; an unknown one says so',()=>{
  assert.match(line(halt({resets_at:at('2026-10-22T12:05:00-04:00')}),{now:NOW}),/\(five-hour\), reset since 12:05\. /);
  assert.match(line(halt({rate_limit_type:'seven_day',resets_at:at('2026-10-27T09:00:00-04:00')}),{now:NOW}),/\(weekly\), resets Tue 09:00\. /);
  assert.match(line(halt({resets_at:null}),{now:NOW}),/\(five-hour\), reset time unknown\. /);
});

test('every Claude Code window type has words; a newer one is still readable',()=>{
  assert.deepEqual(['five_hour','seven_day','seven_day_opus','seven_day_sonnet','seven_day_overage_included','overage','seven_day_fable','',null].map(windowName),
    ['five-hour','weekly','Opus weekly','Sonnet weekly','weekly','overage','weekly fable','usage','usage']);
});

test('only this generation\'s halt counts; a cleared one (null, last_quota_halt kept) shows nothing',()=>{
  assert.equal(active({quota_halt:halt()},2).halt_id,'h1');
  assert.equal(active({quota_halt:halt()},null).halt_id,'h1'); // an unknown generation still shows it
  assert.equal(active({quota_halt:halt({generation:1})},2),null); // resumed or reconnected since
  assert.equal(active({quota_halt:null,last_quota_halt:halt()},2),null);
  assert.equal(active({},2),null);assert.equal(active(null,2),null);assert.equal(active({quota_halt:'halted'},2),null);
  assert.equal(line(null),'');
});

test('a scheduled five-hour notice appends its due line and names Manager-coordinated recovery',()=>{
  const projected=withCheck(check());
  assert.equal(projected.recoveryCheck.status,'scheduled');
  assert.equal(projected.halt_id,'h1');
  assert.equal(line(projected,{manager:true,now:NOW}),
    '⛔ Halted: Max usage limit reached (five-hour), resets 14:50. The host notifies the current Manager to coordinate recovery. You can also switch accounts and Send to reconnect.\nManager recovery check due at 14:55');
  assert.equal(line(projected,{now:NOW}),
    '⛔ Halted: Max usage limit reached (five-hour), resets 14:50. Its Conductor (or the Manager) resumes it; no interrupted turn is replayed.\nManager recovery check due at 14:55');
  assert.equal(recoveryLine(check(),{now:NOW}),'Manager recovery check due at 14:55');
  assert.equal(recoveryLine(check({due_at:null}),{now:NOW}),'Manager recovery check due at time unknown');
});

test('preparing and sending still read as due; sent says sent; a delivery problem names the host reason',()=>{
  for(const status of ['preparing','sending'])
    assert.equal(line(withCheck(check({status})),{now:NOW}).split('\n')[1],'Manager recovery check due at 14:55');
  assert.equal(line(withCheck(check({status:'sent',sent_at:NOW/1000})),{now:NOW}).split('\n')[1],'Manager recovery check sent');
  assert.equal(line(withCheck(check({status:'delivery_problem',failure:'no registered Manager; alarm retained\nuntil one is available'})),{now:NOW}).split('\n')[1],
    'Manager recovery check delivery problem: no registered Manager; alarm retained until one is available');
  assert.equal(line(withCheck(check({status:'delivery_problem'})),{now:NOW}).split('\n')[1],'Manager recovery check delivery problem');
  assert.equal(recoveryLine(check({status:'sent'}),{now:NOW}),'Manager recovery check sent');
});

test('only this halt\'s own episode reset gets a notice line; malformed or foreign metadata never attaches',()=>{
  const other=check({resets_at:at('2026-10-22T19:10:00-04:00')});
  assert.equal(withCheck(other).recoveryCheck,undefined);
  assert.equal(line(withCheck(other),{now:NOW}).includes('Manager recovery check'),false);
  for(const bad of [null,'scheduled',[],{}, {status:'future'}, {status:'scheduled',resets_at:null},
      {status:'scheduled',resets_at:'1792829400'}])
    assert.equal(withCheck(bad).recoveryCheck,undefined);
  // An unknown reset on both sides is the host's own per-session episode key, already associated there.
  assert.equal(active({quota_halt:halt({resets_at:null}),quota_recovery_check:check({resets_at:null})},2).recoveryCheck.status,'scheduled');
  assert.equal(active({quota_halt:halt({resets_at:null}),quota_recovery_check:check({resets_at:at('2026-10-22T14:50:00-04:00')})},2).recoveryCheck.status,'scheduled');
  assert.equal(active({quota_halt:halt({generation:1}),quota_recovery_check:check()},2),null); // another generation
  assert.equal(active({quota_halt:null,quota_recovery_check:check()},2),null); // cleared halt: no line
  assert.equal(line(active({quota_halt:null,quota_recovery_check:check()},2),{now:NOW}),'');
});

test('the projection never mutates the live halt or any other row\'s shared read',()=>{
  const live={quota_halt:halt(),quota_recovery_check:check()},halted=live.quota_halt;
  const projected=active(live,2);
  assert.equal(projected.recoveryCheck.status,'scheduled');
  assert.equal(halted.recoveryCheck,undefined);
  const plain=active({quota_halt:halt()},2);
  assert.equal(Object.hasOwn(plain,'recoveryCheck'),false); // no notice: exactly the old halt shape
  assert.deepEqual(Object.keys(plain),Object.keys(halt()));
  assert.equal(plain.resets_at,halt().resets_at);
  assert.equal(line(halt(),{now:NOW}),'⛔ Halted: Max usage limit reached (five-hour), resets 14:50. Nothing resumes by itself; its Conductor (or the Manager) resumes it.');
});

test('exact operator deferral/cancellation is visible and never promises an armed wake',()=>{
  for (const status of ['deferred_by_operator','cancelled_by_operator']) {
    const projected=withCheck(check({status,due_at:null}));
    assert.match(line(projected,{manager:true,now:NOW}),/Nothing resumes by itself/);
    assert.match(recoveryLine(projected.recoveryCheck),/by operator — not armed/);
    assert.doesNotMatch(line(projected,{manager:true,now:NOW}),/The host notifies/);
    assert.match(recoveryLine(check({status,due_at:null,send_admitted:true,delivery_status:'sent'})),/already admitted \(sent\), never replayed/);
  }
});

'use strict';
// The Manager-list order (static/manager-order.js), run without a page: the desktop Manager Sessions, the phone
// Programs pane and the /m chip strip all sort with it. The sidebar's own use is in opencode-manager-target.test.cjs,
// the chip strip's in phone-opencode-managers.test.cjs.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../static/manager-order.js'),'utf8');
const ctx={};vm.createContext(ctx);vm.runInContext(source,ctx);
const order=ctx.opcManagerOrder;
const plain=v=>JSON.parse(JSON.stringify(v));

test('the parser reads every Manager-Session name form in Dan\'s real data, and nothing else',()=>{
  assert.deepEqual(plain(order.parse('Manager-Session-218')),{index:218,sub:''});
  assert.deepEqual(plain(order.parse('Manager-Session-217-d')),{index:217,sub:'d'});
  assert.deepEqual(plain(order.parse('xxx-Manager-Session-184')),{index:184,sub:''});
  assert.deepEqual(plain(order.parse('Manager-Session-168 Anchor Dock instructions')),{index:168,sub:''});
  assert.deepEqual(plain(order.parse('Manager Session 157-a Setup')),{index:157,sub:'a'});
  assert.deepEqual(plain(order.parse('Manager-Session-1000-b')),{index:1000,sub:'b'},'no three-digit assumption');
  assert.deepEqual(plain(order.parse('Manager-Session-217-description')),{index:217,sub:''},'a word after the number is not a sub-session');
  assert.equal(order.parse('f8c6f542-789f-48db-9447-1a7ca3ac01bc'),null);
  assert.equal(order.parse('Manager-20261013-184158-1c819d'),null);
  assert.equal(order.parse(null,undefined,''),null);
  // The first name that parses wins: an old Claude row's label when its title is only a UUID.
  assert.deepEqual(plain(order.parse('Manager-Session-182','f8c6f542-789f-48db-9447-1a7ca3ac01bc')),{index:182,sub:''});
  assert.deepEqual(plain(order.parse(null,'Manager-Session-181')),{index:181,sub:''});
});

test('index highest first, each primary before its sub-sessions (a first), backends mixed, open ignored',()=>{
  const row=(id,name,{open=false,created=0,backend='claude'}={})=>({id,backend,open,names:[name],created});
  const rows=[
    row('c-216','Manager-Session-216',{created:50}),
    row('o-216-a','Manager-Session-216-a',{backend:'opencode',created:51}),
    row('c-218','Manager-Session-218',{open:true,created:80}),
    row('c-1000','Manager-Session-1000',{created:1}),
    row('o-217-e','Manager-Session-217-e',{backend:'opencode',created:70}),
    row('c-217-a','Manager-Session-217-a',{created:61}),
    row('o-216-b','Manager-Session-216-b',{backend:'opencode',created:52}),
    row('c-217','Manager-Session-217',{open:true,created:60}),
    row('c-uuid-new','a4dfde44-748a-488c-9f8c-d46bad861f04',{open:true,created:90}),
    row('c-218-a','Manager-Session-218-a',{open:true,created:81}),
    row('c-uuid-old','c9ea532e-6086-4a3d-ae09-c402189e7d30',{created:10}),
    row('c-217-d','Manager-Session-217-d',{open:true,created:66}),
    row('o-217-d','Manager-Session-217-d',{backend:'opencode',created:67}),
    row('o-157-a','Manager Session 157-a Setup',{backend:'opencode',created:5}),
    row('c-218-b','Manager-Session-218-b',{created:82}),
  ];
  const sorted=[...rows].sort(order.compare).map(r=>r.id);
  assert.deepEqual(sorted,[
    // index highest first, each primary before its sub-sessions, open or not, both backends together;
    // the same name on two backends: newest created first
    'c-1000','c-218','c-218-a','c-218-b','c-217','c-217-a','o-217-d','c-217-d','o-217-e','c-216','o-216-a','o-216-b','o-157-a',
    // no number: bottom, newest first
    'c-uuid-new','c-uuid-old']);
  assert.deepEqual([...rows].reverse().sort(order.compare).map(r=>r.id),sorted,'the order does not depend on the input order');
});

test('a primary comes first, its sub-sessions right after it, a first (two letters after z), the newest primary leading',()=>{
  const names=['Manager-Session-218','Manager-Session-218-a','Manager-Session-218-c','Manager-Session-218-b','Manager-Session-219','Manager-Session-218-aa','Manager-Session-218-z','Manager-Session-219-a'];
  const sorted=names.map((n,i)=>({names:[n],id:String(i)})).sort(order.compare).map(r=>r.names[0]);
  assert.deepEqual(sorted,['Manager-Session-219','Manager-Session-219-a','Manager-Session-218','Manager-Session-218-a','Manager-Session-218-b','Manager-Session-218-c','Manager-Session-218-z','Manager-Session-218-aa']);
});

// ↳ New sub-session's name: the newest primary (the highest number listed) and the letter after its highest one.
test('nextSub: the newest primary and the next letter after its highest one, a skipped letter staying skipped',()=>{
  const next=rows=>plain(order.nextSub(rows));
  assert.deepEqual(next([['Manager-Session-219'],['Manager-Session-218'],['Manager-Session-218-a']]),
    {index:219,letter:'a',id:'219-a',parent:'Manager-Session-219',name:'Manager-Session-219-a'},'no sub-session yet: a');
  assert.deepEqual(next([['Manager-Session-219'],['Manager-Session-219-a'],['Manager-Session-219-c'],['Manager-Session-218-f']]).id,'219-d',
    'after the highest letter, never filling b');
  // The names a row is known by (a Claude label, then its title), as compare reads them; bare strings count too.
  assert.equal(next([[null,'f8c6f542-789f-48db-9447-1a7ca3ac01bc'],['Manager-Session-219-b','9e2d1c4b-1111-4111-8111-111111111111'],'Manager-Session-219']).name,
    'Manager-Session-219-c');
  assert.equal(next([['Manager-Session-220-a']]).parent,'Manager-Session-220','a listed sub-session names its primary\'s number');
  assert.equal(next([['Manager-Session-84']]).name,'Manager-Session-084-a','three digits, as oc_session.py writes them');
  assert.equal(order.nextSub([['Manager-Session-219'],['Manager-Session-219-z']]),null,'no letter after z');
  assert.equal(order.nextSub([['a4dfde44-748a-488c-9f8c-d46bad861f04']]),null,'no numbered Manager');
  assert.equal(order.nextSub([]),null);
});

test('opening fills the number as written, the letter and the name; a name without a number fills {N} with itself',()=>{
  const template='this is going to be Manager-Session-{N} · sub-session {N}-{letter} · {name} · $& $1';
  assert.equal(order.opening(template,'Manager-Session-220'),'this is going to be Manager-Session-220 · sub-session 220- · Manager-Session-220 · $& $1');
  assert.equal(order.opening(template,'Manager-Session-219-b'),'this is going to be Manager-Session-219 · sub-session 219-b · Manager-Session-219-b · $& $1');
  assert.equal(order.opening('to be {N}.','Scratch $&'),'to be Scratch $&.','replacement patterns in a name stay literal');
  assert.equal(order.opening(null,'Manager-Session-1'),'');
});

// The /m strip's cutoff: last activity within STRIP_DAYS, else open, kept (selected) or the newest number.
test('recent: a week of last activity, open and kept always, the newest primary and its sub-sessions always, order kept',()=>{
  const now=1793000000,day=86400,row=(id,name,days,extra={})=>({id,names:[name],lastActive:days===null?null:now-days*day,...extra});
  const rows=[
    row('219-a','Manager-Session-219-a',30),               // newest number: shown however old
    row('219','Manager-Session-219',20),
    row('218','Manager-Session-218',6.9),                  // closed but recent: stays (Send reopens it)
    row('217','Manager-Session-217',8,{open:true}),        // old but open: stays
    row('216','Manager-Session-216',8),                    // old and closed: gone
    row('215','Manager-Session-215',9,{keep:true}),        // the selected one: stays
    row('214','Manager-Session-214',null),                 // no timestamp: never taken for old
    row('213','Manager-Session-213',7.01),
    row('uuid','a4dfde44-748a-488c-9f8c-d46bad861f04',10)];
  assert.equal(order.STRIP_DAYS,7);
  assert.deepEqual(order.recent(rows,now).map(r=>r.id),['219-a','219','218','217','215','214']);
  assert.deepEqual(order.recent(rows,now,30).map(r=>r.id),['219-a','219','218','217','216','215','214','213','uuid'],'the window is a parameter');
  assert.deepEqual(plain(order.recent([],now)),[]);
  assert.deepEqual(order.recent([row('x','a4dfde44',20)],now).map(r=>r.id),[],'no numbered Manager: nothing is newest');
});

test('index: the number and sub-session letter a chip shows beside its gauge, from the first numbered name',()=>{
  assert.equal(order.index('Manager-Session-219'),'219');
  assert.equal(order.index('Manager-Session-219-a'),'219-a');
  assert.equal(order.index(null,'Manager-Session-168 Anchor Dock instructions'),'168');
  assert.equal(order.index('Claude Manager'),null);
  assert.equal(order.index(),null);
});

// View's history (static/view-history.js): its pure rules in a VM. view-history.html runs the actual page (the strip,
// the sheet, the root's pop-up, storage and reload) with the actual shared renderer.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../static/view-history.js'),'utf8');
const ctx={console};ctx.globalThis=ctx;vm.createContext(ctx);vm.runInContext(source,ctx);
const H=ctx.opcViewHistory;

test('one identity per handle: an Anchor Point by its core (any case), a UUID by its first 8 hex',()=>{
  assert.equal(H.handleKey('[ap:3AAES4]'),'ap:3AAES4');
  assert.equal(H.handleKey(' [ap:3aaes4] '),'ap:3AAES4');
  assert.equal(H.handleKey('fe6e40dc'),'hex:fe6e40dc');
  assert.equal(H.handleKey('FE6E40DC-1111-4222-8333-444455556666'),'hex:fe6e40dc');
  assert.equal(H.handleKey('fe6e40dc-1111-4222-8333-444455556666'),H.handleKey('fe6e40dc'));
  for(const no of ['','[ap:]','ap:3AAES4','fe6e40d','fe6e40dcz','[ap:ABC] x',null,undefined])assert.equal(H.handleKey(no),null,String(no));
});

test('a card shows an Anchor Point as written and a whole UUID as its 8 hex',()=>{
  assert.equal(H.shortHandle('[ap:BPQUKH]'),'[ap:BPQUKH]');
  assert.equal(H.shortHandle('3f3754e2-1111-4222-8333-444455556666'),'3f3754e2');
  assert.equal(H.shortHandle('3f3754e2'),'3f3754e2');
});

test('an entry is named by the node\'s full name, tags and all: the daemon\'s first row, else the first heading',()=>{
  const md='# [ap:XGEX2E] ✅ ⚡ QUICK · Build: clickable handles #sift-programs--expand\n\n## Section\n\ntext';
  assert.equal(H.nameFrom({markdown:md}),'[ap:XGEX2E] ✅ ⚡ QUICK · Build: clickable handles #sift-programs--expand');
  assert.equal(H.nameFrom({markdown:md,rows:[{name:'Row name #tag'}]}),'Row name #tag');
  assert.equal(H.nameFrom({markdown:'plain text, no heading',rows:[]}),null);
  assert.equal(H.nameFrom({markdown:'# '+'x'.repeat(5000)}).length,H.NAME_MAX);
});

test('a position from the page around is reduced to what the cards show, bounded, and refused when empty',()=>{
  assert.equal(H.cleanPosition(null),null);
  assert.equal(H.cleanPosition({cards:[]}),null);
  const line=i=>({id:'cc-'+i,key:'s:cc-'+i,role:'Helper',handle:'[ap:SOCK01]',name:'n'.repeat(900),status:'Claude Code',extra:'dropped'});
  const pos=H.cleanPosition({kind:'socket',total:20,program:null,cards:Array.from({length:20},(_,i)=>({id:'cc-'+i,active:i===0,program:{key:'p:TREE01',core:'TREE01',title:'T',kind:'quick',count:3},
    chain:Array.from({length:12},(_,j)=>line(i+'-'+j))}))});
  assert.equal(pos.cards.length,12);
  assert.equal(pos.total,20);
  assert.equal(pos.cards[0].active,true);
  assert.equal(pos.cards[0].chain.length,8,'the deepest eight lines of a branch');
  assert.equal(pos.cards[0].chain[0].name.length,300);
  assert.equal('extra' in pos.cards[0].chain[0],false);
  assert.equal(H.cleanPosition({kind:'program',cards:[{id:'cc-1',chain:[{role:'Conductor'}]}]}).kind,'program');
});

test('one Recent-roots key per root: how a handle is read and its identity; a Dock picked or pasted is one root; other picks by a short hash',()=>{
  assert.equal(H.rootKey({handle:'[ap:k7m2qf]',how:'node'}),'node:ap:K7M2QF');
  assert.equal(H.rootKey({handle:'3F3754E2-1111-4222-8333-444455556666'}),'node:hex:3f3754e2');
  assert.equal(H.rootKey({handle:'3f3754e2',how:'subtree'}),'subtree:hex:3f3754e2');
  assert.equal(H.rootKey({pick:'dock',selection:{kind:'parcel',value:{dock:'[ap:DOCK01]'}}}),H.rootKey({handle:'[ap:DOCK01]',how:'dock'}));
  const group=H.rootKey({pick:'group',selection:{kind:'parcel',value:{group:'grp-1a2b3c4d'}}});
  assert.match(group,/^pick:group:[0-9a-z]{1,7}$/);
  assert.equal(group,H.rootKey({pick:'group',selection:{kind:'parcel',value:{group:'grp-1a2b3c4d'}}}),'stable');
  assert.notEqual(group,H.rootKey({pick:'group',selection:{kind:'parcel',value:{group:'grp-ffff0000'}}}));
  assert.match(H.rootKey({pick:'estate',selection:{kind:'estate',value:'[ap:ESTAT1]'}}),/^pick:estate:/);
  for(const no of [null,{},{handle:'not a handle'},{handle:'[ap:]'}])assert.equal(H.rootKey(no),null);
});

test('a card says how deep the digging went and when the root was last used',()=>{
  assert.deepEqual([1,2,4,5,9,10,50].map(H.depthOf),[0,1,1,2,2,3,3]);
  const now=new Date(2026,9,27,15,0).getTime(),at=(d,h)=>new Date(2026,9,27-d,h,5).getTime()/1000;
  assert.match(H.whenText(at(0,9),now),/^\d{1,2}:05|^0?9:05/);
  assert.match(H.whenText(at(1,9),now),/^yesterday /);
  assert.doesNotMatch(H.whenText(at(3,9),now),/yesterday|Oct/);
  assert.match(H.whenText(at(20,9),now),/Oct|10/);
  assert.equal(H.whenText(null,now),'');
});

test('names have a 256-character middle ellipsis, preserving both ends and whole emoji',()=>{
  const text='FIRST '+ '😀'.repeat(600)+' LAST #tag',shown=H.middle(text);
  assert.equal(H.TITLE_MAX,256);assert.equal(H.DESC_MAX,256);
  assert.equal(Array.from(shown).length,256);
  assert.ok(shown.startsWith('FIRST '));assert.ok(shown.endsWith(' LAST #tag'));
  assert.equal(shown.split('…').length,2);assert.doesNotMatch(shown,/[\uD800-\uDBFF](?![\uDC00-\uDFFF])/);
  assert.equal(H.middle('  short  name  '),'short name');
});

test('middle cuts prefer nearby whole words, keep trailing AP-IDs/tags, and fall back for unbroken text',()=>{
  const text='INFORMATIVE START '+Array.from({length:100},(_,i)=>'word'+i).join(' ')+' [ap:ABC123] #root-tag';
  const shown=H.middle(text),edges=shown.split('…');
  assert.ok(Array.from(shown).length<=256&&Array.from(shown).length>=206);
  assert.match(edges[0],/^INFORMATIVE START (?:word\d+ ?)+$/);
  assert.match(edges[1],/^word\d+(?: word\d+)* \[ap:ABC123\] #root-tag$/);
  assert.equal(H.middle('a'.repeat(600)),'a'.repeat(128)+'…'+'a'.repeat(127));
  assert.equal(H.middle('x'.repeat(256)),'x'.repeat(256));
});

test('description is the first meaningful note line, not provenance/fences/dividers or another rendered node',()=>{
  assert.equal(H.descriptionLine('\n↩ moved from somewhere\n<!-- hidden -->\n---\n## A useful description\nLater'),'A useful description');
  assert.equal(H.descriptionLine('<!--\nhidden\n-->\n> About this root'),'About this root');
  assert.equal(H.descriptionLine('## Task\n\nBuild useful root names'),'Build useful root names');
  const held={markdown:'# Root name\n\nRoot description\n\n## Child name\n\nChild description',rows:[{name:'Root name'},{name:'Child name'}]};
  assert.equal(H.descriptionFrom(held),'Root description');
  held.markdown='# Root name\n\n## Child name\n\nChild description';assert.equal(H.descriptionFrom(held),'');
  const long=H.descriptionLine('START '+'x'.repeat(1000)+' END');assert.equal(long.length,320,'metadata stays searchable at its existing bound');assert.ok(long.endsWith(' END'));assert.ok(long.includes('…'));
  assert.ok(Array.from(H.middle(long,H.DESC_MAX)).length<=256,'the displayed copy is shorter');
});

test('other parcels offer their actual tags or symbols rather than the word Parcel',()=>{
  assert.equal(H.parcelName({tags:['opc-pane','views'],root:'25a49a99'}),'Tags · opc-pane · views · 25a49a99');
  assert.equal(H.parcelName({symbols:['render','paint'],root:'25a49a99'}),'Symbols · render · paint · 25a49a99');
});

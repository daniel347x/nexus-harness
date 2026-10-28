// The clickable-handle rule (static/handle-links.js findHandles) in a VM: which tokens are handles and exactly where.
// handle-links.html runs the actual module on actual pane markup in Chrome (decoration, hit-test, gestures, menu).
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../static/handle-links.js'),'utf8');

function api() {
  const ctx={document:{body:null,addEventListener(){}},setTimeout,clearTimeout};
  ctx.globalThis=ctx;
  vm.runInNewContext(source,ctx);
  return ctx.opcHandleLinks;
}
const handles=text=>Array.from(api().findHandles(text),f=>f.handle);
const UUID='25a49a99-1dbc-4f66-9cd3-d8a9f60ec976';

test('handles: Anchor Points always, whole UUIDs, standalone 8-hex short UUIDs (digits-only included)',()=>{
  const yes=[
    ['[ap:X8TYWJ]','[ap:X8TYWJ]'],['see [ap:abc] here','[ap:abc]'],['x[ap:K7M2QF]y','[ap:K7M2QF]'],
    ['adee3c89','adee3c89'],['`adee3c89`','adee3c89'],['(adee3c89),','adee3c89'],['end adee3c89.','adee3c89'],
    ['Socket: 3f3754e2 ok','3f3754e2'],['**9e210387**','9e210387'],['"74bd1a7a"','74bd1a7a'],['[25a49a99]','25a49a99'],
    ['20261022','20261022'],['an all-digit 12345678 one','12345678'],['deadbeef','deadbeef'],
    [UUID,UUID],['root '+UUID+', then',UUID],['adee3c89\n','adee3c89'],['tree 2d10d8e0','2d10d8e0'],
  ];
  for (const [text,want] of yes) assert.deepEqual(handles(text),[want],JSON.stringify(text));
});

test('not handles: prefixes, paths, URLs, assignments, extensions, other lengths, uppercase, plain words',()=>{
  const no=['[ap:ab]','[ap:]','5433067','123456789','1287025a9a11',
    'a'.repeat(40),'#3fcf7aff','0x1234abcd','https://example.com/adee3c89/x','path/adee3c89','adee3c89.js','id=adee3c89',
    'sha256:adee3c89','opc-pane@adee3c89','ses_adee3c89','cc-1ebeaf49-0d70-4a64','carto-refresh-folder-da9953ab',
    'ADEE3C89','E:\\x\\adee3c89','adee3c89x','xadee3c89','adee3c89-1234','adee3c89_b','abcdefgh','database','feedback','.adee3c89',
    UUID+'.json','x'+UUID,UUID.toUpperCase()];
  for (const text of no) assert.deepEqual(handles(text),[],JSON.stringify(text));
});

test('offsets and several handles in one text, in order',()=>{
  const text='Read [ap:X8TYWJ] then 9e210387 and '+UUID+'.';
  const found=Array.from(api().findHandles(text),f=>({...f}));
  assert.deepEqual(found.map(f=>f.handle),['[ap:X8TYWJ]','9e210387',UUID]);
  for (const f of found) assert.equal(text.slice(f.start,f.end),f.handle);
});

test('loads without a page: no decoration, no listeners, the API frozen',()=>{
  const a=api();
  assert.equal(a.stats().decorated,false);
  assert.ok(Object.isFrozen(a));
  assert.equal(a.HOLD_MS<500,true);
});

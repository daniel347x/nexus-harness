// View's slice-group picker shares the Chrome extension's own grammar (static/view-pickers.js copies parseSliceGroupName
// and buildNestedDisplayItems verbatim from workflowy-glimpse-cache/glimpse-cache-client.js). This drift alarm takes the
// extension's two functions out of the NEXUS checkout the pane serves its renderer from, runs both copies on one corpus,
// and compares their code: an edit on either side fails here until the other follows. Without a NEXUS checkout on this
// machine the comparison is skipped (said so), and the pane copy is still checked on its own.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),os=require('node:os'),path=require('node:path'),vm=require('node:vm');

const pickersSource=fs.readFileSync(path.join(__dirname,'../static/view-pickers.js'),'utf8');
const ours=(()=>{const ctx={globalThis:null,document:null};ctx.globalThis=ctx;vm.createContext(ctx);vm.runInContext(pickersSource,ctx);return ctx.opcViewPickers;})();

// The checkout: NEXUS_CHECKOUT, else the pane's own setting (pane-glimpse.json), else NEXUS main beside this repository.
function nexusCheckout(){
  if(process.env.NEXUS_CHECKOUT)return process.env.NEXUS_CHECKOUT;
  try{const c=JSON.parse(fs.readFileSync(path.join(os.homedir(),'.config/opencode-companion/pane-glimpse.json'),'utf8'));if(c.nexus_checkout)return c.nexus_checkout;}catch(_){/* default */}
  return path.resolve(__dirname,'../../../workflowy-nexus-mcp-tools');
}
// One function's source, by brace matching from its declaration line (never a beacon's role= mention; these two hold
// no braces in strings).
function extract(source,name){
  const found=new RegExp('^[ \\t]*function '+name+'\\(','m').exec(source);if(!found)return null;
  const start=source.indexOf('function',found.index);
  let depth=0,i=source.indexOf('{',start);
  for(;i<source.length;i++){if(source[i]==='{')depth++;else if(source[i]==='}'&&--depth===0)break;}
  return source.slice(start,i+1);
}
const normalized=code=>code.replace(/\/\/[^\n]*/g,'').replace(/\s+/g,' ').trim();
const extensionFile=path.join(nexusCheckout(),'workflowy-glimpse-cache','glimpse-cache-client.js');
const extensionSource=fs.existsSync(extensionFile)?fs.readFileSync(extensionFile,'utf8'):null;
const theirs=(()=>{
  if(!extensionSource)return null;
  const parse=extract(extensionSource,'parseSliceGroupName'),build=extract(extensionSource,'buildNestedDisplayItems');
  if(!parse||!build)return null;
  const ctx={};vm.createContext(ctx);vm.runInContext(parse+'\n'+build+'\nthis.api={parseSliceGroupName,buildNestedDisplayItems};',ctx);
  return {...ctx.api,parse,build};
})();

// Every corner of the grammar: [n] left (dummy expanders) and right (order), negatives, none, ties, spaces round pipes,
// a category with no expander of its own, duplicates, an empty expander, '[2]' alone, a literal '[foo]', a middle [n].
const CORPUS=['Work|Recruiting|RECRUITING[0]','[-5]Work','[50]Work|Recruiting','Work|Recruiting|RECRUITING - FULL[1]','Nexus|CORE[0]','[1]Nexus',
  'Nexus | LAST-MILE — TODOs [50]','Loose','Misc|LEGAL[0]','[100]Work|Misc','[2]Nexus','[2]','[foo]nexus','Mid[3]|leaf','Work|Conversational|B[-10]',
  'Work|Conversational|A[-10]','Work|Conversational|C','[-100]Work|Conversational','OpenCode | Manager Sessions[-2000]','[-100]OpenCode','Zed','alpha',
  'Nexus | Transcription Extension BEACON SLICE','[-200]Research Lines','Research Lines|🔬📈 LINES[0]','  [7]  Spaced | Path  '];
const groups=CORPUS.map((name,i)=>({name,handle:'h'+i}));
const project=items=>JSON.parse(JSON.stringify(items.map(e=>({kind:e.kind,depth:e.depth,name:e.name,group:e.group?e.group.name:null,duplicate:!!e.duplicate,synthetic:!!e.synthetic,collapsed:!!e.collapsed,path:e.pathStr||null}))));

test('the extension\'s own parseSliceGroupName and buildNestedDisplayItems and the pane\'s copy agree, code and output',{skip:theirs?false:'no NEXUS checkout with the extension at '+extensionFile},()=>{
  assert.equal(normalized(extract(pickersSource,'parseSliceGroupName')),normalized(theirs.parse),'parseSliceGroupName drifted from the extension');
  assert.equal(normalized(extract(pickersSource,'buildNestedDisplayItems')),normalized(theirs.build),'buildNestedDisplayItems drifted from the extension');
  const plain=v=>JSON.parse(JSON.stringify(v));  // two vm realms: compare the data, not the prototypes
  for(const name of CORPUS)assert.deepEqual(plain(ours.parseSliceGroupName(name)),plain(theirs.parseSliceGroupName(name)),name);
  for(const [expanded,all] of [[new Set(),false],[new Set(['Work','Work|Recruiting','Nexus']),false],[new Set(),true]])
    assert.deepEqual(project(ours.buildNestedDisplayItems(groups,ours.parseSliceGroupName,expanded,all)),
      project(theirs.buildNestedDisplayItems(groups,theirs.parseSliceGroupName,expanded,all)));
});

test('the grammar as Dan described it: leaf order by trailing [n], dummy expanders by leading [n], unindexed last and alphabetical',()=>{
  const all=project(ours.buildNestedDisplayItems(groups,ours.parseSliceGroupName,new Set(),true));
  const top=all.filter(e=>e.depth===0).map(e=>(e.kind==='expander'?'▸':'')+e.name+(e.duplicate?' (dup)':''));
  // '[7]  Spaced | Path' defines the expander Path inside Spaced, so Spaced itself is an unindexed category (alphabetical, last).
  assert.deepEqual(top,['▸Research Lines','▸OpenCode','▸Work','▸Nexus','▸Nexus (dup)','[2]','[foo]nexus','alpha','Loose','▸Mid[3]','▸Misc','▸Spaced','Zed']);
  const work=all.slice(all.findIndex(e=>e.name==='Work'&&e.depth===0)+1);
  assert.deepEqual(work.slice(0,8).map(e=>'  '.repeat(e.depth-1)+e.name),
    ['Conversational','  A','  B','  C','Recruiting','  RECRUITING','  RECRUITING - FULL','Misc']);
  assert.deepEqual(JSON.parse(JSON.stringify(ours.parseSliceGroupName('Nexus | LAST-MILE — TODOs [50]'))),{isExpander:false,segments:['Nexus','LAST-MILE — TODOs'],index:50});
  assert.equal(ours.groupLabel('Work|Recruiting|RECRUITING[0]'),'RECRUITING');
  assert.equal(ours.groupLabel('[2]'),'[2]');
});

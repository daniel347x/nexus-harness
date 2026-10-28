const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
// The desktop sidebar's quota row: its gauge rule, run without a page (no document: nothing polls).
const source=fs.readFileSync(path.join(__dirname,'../static/sessions-quota.js'),'utf8');
const ctx={};vm.runInNewContext(source,ctx);
const {quotaGauges,shortLabel,leftText}=ctx.opcQuotaBar;
const NOW=1792438401;
const reading=(over={})=>({schemaVersion:1,backend:'usage',now:NOW,status:'ready',refreshing:false,observedAt:NOW-20,ageSeconds:20,error:null,chatgptAgeSeconds:20,
  claude:{ok:true,plan:'default_claude_max_20x',windows:[
    {label:'five-hour',kind:'session',percent:56,resets_at:new Date((NOW+3600)*1000).toISOString()},
    {label:'weekly',kind:'weekly_all',percent:3,resets_at:new Date((NOW+6*86400)*1000).toISOString()},
    {label:'Fable weekly',kind:'weekly_scoped',percent:4,resets_at:new Date((NOW+6*86400)*1000).toISOString(),allowance_percent_of_weekly:50,used_percent_of_weekly:2}],
    opus_separate:false,extra_usage_enabled:false},
  chatgpt:{ok:true,plan:'promax',windows:[{label:'weekly',percent:70,resets_at:NOW+5*86400}],models:{'gpt-6-astra':{available:true,available_at:null}},
    limit_reached:false,stale:false,notes:[]},claudeFrameLine:null,...over});
// The vm's arrays have their own prototypes: compare plain copies.
const plain=value=>JSON.parse(JSON.stringify(value));
const brief=gauges=>plain(gauges.map(g=>[g.provider,g.label,g.percent,g.state]));

test('a ready reading shows one gauge per reported window, used share, left and reset on hover',()=>{
  const gauges=quotaGauges(reading(),NOW);
  assert.deepEqual(brief(gauges),[['claude','5h',56,'known'],['claude','wk',3,'known'],['claude','Fable',4,'known'],['chatgpt','wk',70,'known']]);
  assert.match(gauges[0].title,/^Claude Max 20× · five-hour window\n44% left · 56% used\nresets .* · in 1h\nread just now\nClick the row/);
  assert.match(gauges[2].title,/up to 50% of weekly · 2% of weekly used/);
  assert.match(gauges[3].title,/^GPT promax · weekly window\n30% left · 70% used\n[\s\S]*gpt-6-astra: available now/);
});

test('no reading yet, or a failed read, is a grey ? in every expected place, never zero',()=>{
  for (const [u,why] of [[null,/reading the allotments/],[null,/allotments unavailable: the pane did not answer/]]) {
    const gauges=quotaGauges(u,NOW,String(why).includes('pane') ? 'the pane did not answer' : null);
    assert.deepEqual(brief(gauges),[['claude','5h',null,'unknown'],['claude','wk',null,'unknown'],['claude','Fable',null,'unknown'],['chatgpt','wk',null,'unknown']]);
    assert.match(gauges[0].title,why);
  }
});

test('one provider unavailable greys only its own gauges',()=>{
  const gauges=quotaGauges(reading({claude:{ok:false,error:'HTTP 429'}}),NOW);
  assert.deepEqual(brief(gauges),[['claude','5h',null,'unknown'],['claude','wk',null,'unknown'],['claude','Fable',null,'unknown'],['chatgpt','wk',70,'known']]);
  assert.match(gauges[0].title,/Claude allotments unavailable: HTTP 429/);
  const gpt=quotaGauges(reading({chatgpt:{ok:false,stale:false,error:'no login'}}),NOW);
  assert.deepEqual(brief(gpt).slice(3),[['chatgpt','wk',null,'unknown']]);
});

test('stale and old readings stay visible but marked; a window that reset after the reading is ?',()=>{
  const stale=quotaGauges(reading({chatgpt:{...reading().chatgpt,ok:false,stale:true,error:'timeout'},chatgptAgeSeconds:4000}),NOW);
  assert.deepEqual(brief(stale)[3],['chatgpt','wk',70,'stale']);
  assert.match(stale[3].title,/stale: the current reading is unavailable[\s\S]*read 1.1 h ago/);
  const old=quotaGauges(reading({ageSeconds:900,chatgptAgeSeconds:900,error:'usage tool exited 1'}),NOW);
  assert.deepEqual(plain(old.map(g=>g.state)),['stale','stale','stale','stale']);
  assert.match(old[0].title,/stale: this reading is old\nread 15 min ago · last refresh failed: usage tool exited 1/);
  const reset=reading();reset.claude.windows[0].resets_at=new Date((NOW-60)*1000).toISOString();
  const passed=quotaGauges(reset,NOW);
  assert.deepEqual(brief(passed)[0],['claude','5h',null,'unknown']);
  assert.match(passed[0].title,/reset after the reading \(56% used before the reset\)/);
});

test('a window without a figure is ?, and any further ChatGPT window gets its own gauge',()=>{
  const u=reading();u.claude.windows[1].percent=null;u.chatgpt.windows.unshift({label:'five-hour',percent:12,resets_at:NOW+7200});
  assert.deepEqual(brief(quotaGauges(u,NOW)),[['claude','5h',56,'known'],['claude','wk',null,'unknown'],['claude','Fable',4,'known'],
    ['chatgpt','5h',12,'known'],['chatgpt','wk',70,'known']]);
  assert.deepEqual(['five-hour','weekly','Fable weekly','Opus weekly','five-hour burst',''].map(l=>shortLabel(l)),['5h','wk','Fable','Opus','5h burst','?']);
});

test('each known gauge carries the time to its reset: h m under a day, d h beyond; passed or unknown is nothing',()=>{
  const left=gauges=>plain(gauges.map(g=>g.left&&[g.left.major,g.left.minor]));
  assert.deepEqual(left(quotaGauges(reading(),NOW)),[['1h',''],['6d',''],['6d',''],['5d','']]);
  const at=s=>plain(leftText(NOW+s,NOW));
  assert.deepEqual([at(4*3600+50*60),at(5*86400+3*3600),at(2*3600),at(20*3600+5*60),at(30),at(42*60)],
    [{major:'4h',minor:'50m'},{major:'5d',minor:'3h'},{major:'2h',minor:''},{major:'20h',minor:'5m'},{major:'1m',minor:''},{major:'42m',minor:''}]);
  assert.deepEqual([leftText(NOW,NOW),leftText(NOW-60,NOW),leftText(NaN,NOW)],[null,null,null]);
  const u=reading();u.claude.windows[0].resets_at=new Date((NOW-60)*1000).toISOString();delete u.claude.windows[1].resets_at;u.claude.windows[2].percent=null;
  assert.deepEqual(left(quotaGauges(u,NOW)),[null,null,null,['5d','']]);
  assert.deepEqual(left(quotaGauges(null,NOW)),[null,null,null,null]);
  const stale=quotaGauges(reading({chatgpt:{...reading().chatgpt,ok:false,stale:true}}),NOW);
  assert.deepEqual(left(stale)[3],['5d','']);
});

test('a stale Claude last-good reading keeps its windows with the provider age and names the retry time',()=>{
  const base=reading().claude;
  const u=reading({ageSeconds:700,retryAt:NOW+600,
    claude:{...base,ok:false,stale:true,rate_limited:true,retry_at:NOW+600,note:'waiting for the provider retry time',error:'HTTP 429'}});
  const gauges=quotaGauges(u,NOW);
  assert.deepEqual(brief(gauges).slice(0,3),[['claude','5h',56,'stale'],['claude','wk',3,'stale'],['claude','Fable',4,'stale']]);
  assert.match(gauges[0].title,/stale: last good reading · waiting for the provider retry time/);
  assert.match(gauges[0].title,/rate limited: next read in 10m/);
  assert.match(gauges[0].title,/read 12 min ago/);
  const none=quotaGauges(reading({claude:{ok:false,stale:true,rate_limited:true,retry_at:NOW+60,note:'waiting',windows:[]}}),NOW);
  assert.deepEqual(brief(none).slice(0,3),[['claude','5h',null,'unknown'],['claude','wk',null,'unknown'],['claude','Fable',null,'unknown']]);
  assert.match(none[0].title,/unknown: waiting/);
  const noGood=quotaGauges(reading({claude:{ok:false,rate_limited:true,note:'waiting for the provider retry time',retry_at:NOW+300,error:'HTTP 429'}}),NOW);
  assert.deepEqual(brief(noGood).slice(0,3),[['claude','5h',null,'unknown'],['claude','wk',null,'unknown'],['claude','Fable',null,'unknown']]);
  assert.match(noGood[0].title,/unknown: Claude allotments unavailable: waiting for the provider retry time · next read in 5m/);
  assert.doesNotMatch(noGood[0].title,/HTTP 429/);
});

test('narrow rows give up the bracketed time first (short, then gone), then the provider words, then the window names',()=>{
  const css=fs.readFileSync(path.join(__dirname,'../static/sessions.css'),'utf8');
  const width=sel=>+new RegExp('@container \\(max-width:(\\d+)px\\) \\{ \\.'+sel+' \\{ display:none').exec(css)[1];
  const order=['quota-left-minor','quota-left','quota-provider','quota-label'].map(width);
  assert.deepEqual(order,[...order].sort((a,b)=>b-a));
  assert.equal(new Set(order).size,4);
});

test('GPT hover identifies the native login and configured quota label without treating legacy Codex as active proof',()=>{
  const u=reading();u.chatgpt.account={label:'quota-b',opencode_credential_id:'cred_b',active_credential_label:'OpenAI second ☀',
    binding:'operator-configured Codex → native ID; not an independent provider-identity check'};
  const current=quotaGauges(u,NOW).find(g=>g.provider==='chatgpt');
  assert.match(current.title,/Native login: OpenAI second ☀ · quota label: quota-b/);
  assert.match(current.title,/not an independent provider-identity check/);
  u.chatgpt.windows[0].resets_at=NOW-1;
  assert.match(quotaGauges(u,NOW).find(g=>g.provider==='chatgpt').title,/Native login: OpenAI second/);
  u.chatgpt.account={label:null,opencode_credential_id:null,binding:'default Codex login; UNVERIFIED'};
  assert.match(quotaGauges(u,NOW).find(g=>g.provider==='chatgpt').title,/Default Codex quota · UNVERIFIED against the active OpenCode subscription/);
});

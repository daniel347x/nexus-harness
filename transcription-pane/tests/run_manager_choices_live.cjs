// Read-only production smoke check. Unlike the fixture suites, load the real startup scripts,
// sessions iframe and API catalog together. Never click Send/Start/Launch or select a conversation.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {spawn} = require('node:child_process');

function verifyChoices(snapshot, {phone = false, runtime, selected = false} = {}) {
  const ids = phone ? ['m-new-runtime','m-new-agent','m-new-oc-model','m-new-oc-variant','m-new-effort'] :
    ['manager-new-runtime','opencode-new-agent','opencode-new-model','opencode-new-variant','claude-new-effort'];
  const [r, agent, model, variant, effort] = ids.map(id => snapshot.selects.find(s => s.id === id));
  const values = s => s.options.map(o => o.value);
  assert.deepEqual(values(r), ['claude','opencode'], 'both runtimes must exist');
  assert.equal(r.value, runtime);
  assert.ok(r.visible, 'runtime must be visible in the open box');
  if (runtime === 'claude') {
    assert.ok(snapshot.claudeModel, 'Claude model must be populated');
    assert.ok(values(effort).includes('xhigh') && values(effort).includes(effort.value), 'Claude effort must be populated');
    assert.ok(effort.visible && !agent.visible && !model.visible && !variant.visible, 'only Claude controls must show');
  } else {
    assert.deepEqual(values(agent).sort(), ['manager','manager-gpt']);
    assert.ok(model.options.length > 1, 'catalog must be populated, not a loading placeholder');
    assert.ok(model.options.some(o => o.value === 'openai/gpt-6.1-sol-fast' || o.text === 'GPT-6.1 Sol Fast · openai'), 'real Sol Fast catalog entry must exist');
    assert.ok([agent,model,variant].every(s => s.visible), 'all OpenCode controls must be visible');
    assert.ok(!effort.visible, 'Claude effort must be hidden');
    if (selected) {
      assert.equal(agent.value, 'manager-gpt');
      assert.equal(model.options.find(o => o.value === model.value)?.text,
        phone ? 'GPT-6.1 Sol Fast · openai' : 'openai/gpt-6.1-sol-fast · GPT-6.1 Sol Fast');
      assert.ok(values(variant).includes('xhigh'), 'Sol Fast must offer xhigh');
      assert.equal(variant.value, 'xhigh');
    }
  }
}

// @beacon[
//   id=opc-pane-tests@managerChoicesLive,
//   slice_labels=opc-pane,
//   kind=ast,
//   comment=Cold real-origin desktop and phone Manager-choice smoke check: real iframe/startup/catalog, exact manager-gpt/Sol Fast/xhigh and shared New Manager choices; records options console errors and failed requests, blocks every non-GET/HEAD and never launches or sends.,
// ]
async function managerChoicesLive(origin, receipt, chromePath) {
  const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
  const until = async (test, why) => {
    const end = Date.now() + 30000;
    while (Date.now() < end) { const value = await test(); if (value) return value; await sleep(50); }
    throw Error('Observer timeout: ' + why);
  };
  fs.mkdirSync(receipt, {recursive:true});
  const profile = path.join(receipt, 'chrome-profile-' + Date.now());
  const report = {at:new Date().toISOString(), origin, passed:false, pages:[]};
  const chrome = spawn(chromePath, ['--headless=new','--disable-gpu','--no-first-run','--no-default-browser-check',
    '--user-data-dir='+profile,'--remote-debugging-port=0','--window-size=1600,1100','about:blank'], {stdio:'ignore'});
  let ws, sequence = 0, page;
  const pending = new Map(), requests = new Map();
  const call = (method, params = {}) => new Promise((resolve,reject) => {
    const id = ++sequence; pending.set(id, {resolve,reject}); ws.send(JSON.stringify({id,method,params}));
  });
  const evaluate = async expression => {
    const result = await call('Runtime.evaluate', {expression,returnByValue:true,awaitPromise:true});
    if (result.exceptionDetails) throw Error(JSON.stringify(result.exceptionDetails));
    return result.result.value;
  };
  try {
    const port = await until(() => {
      try { return Number(fs.readFileSync(path.join(profile,'DevToolsActivePort'),'utf8').split('\n')[0]); } catch { return null; }
    }, 'Chrome startup');
    const target = (await (await fetch('http://127.0.0.1:'+port+'/json')).json()).find(t => t.type === 'page');
    ws = new WebSocket(target.webSocketDebuggerUrl);
    await new Promise(resolve => ws.addEventListener('open',resolve,{once:true}));
    ws.addEventListener('message', event => {
      const message = JSON.parse(event.data), p = pending.get(message.id);
      if (p) { pending.delete(message.id); message.error ? p.reject(Error(JSON.stringify(message.error))) : p.resolve(message.result); return; }
      const {method,params} = message;
      if (!page) return;
      if (method === 'Network.requestWillBeSent') {
        const request = {id:params.requestId,url:params.request.url,method:params.request.method};
        requests.set(params.requestId, request); page.requests.push(request);
      }
      if (method === 'Network.responseReceived' && params.response.status >= 400)
        page.failedRequests.push({url:params.response.url,status:params.response.status});
      if (method === 'Network.loadingFailed') page.failedRequests.push({...requests.get(params.requestId),...params});
      if (method === 'Runtime.consoleAPICalled') page.console.push(params);
      if (method === 'Runtime.exceptionThrown') page.exceptions.push(params);
      if (method === 'Fetch.requestPaused') {
        const blocked = !['GET','HEAD'].includes(params.request.method);
        if (blocked) page.blockedWrites.push({url:params.request.url,method:params.request.method});
        void call(blocked ? 'Fetch.failRequest' : 'Fetch.continueRequest', {requestId:params.requestId,
          ...(blocked ? {errorReason:'BlockedByClient'} : {})});
      }
    });
    for (const domain of ['Page','Runtime','Network']) await call(domain+'.enable');
    await call('Network.setCacheDisabled',{cacheDisabled:true});
    await call('Fetch.enable',{patterns:[{urlPattern:'*',requestStage:'Request'}]});
    for (const phone of [false,true]) {
      page = {url:origin+(phone?'/m':'/'),phone,snapshots:[],console:[],exceptions:[],failedRequests:[],requests:[],blockedWrites:[]};
      report.pages.push(page);
      const doc = phone ? 'document' : "document.getElementById('claude-sessions-frame').contentDocument";
      const id = (desktop,mobile) => JSON.stringify(phone ? 'm-'+mobile : desktop);
      const click = (desktop,mobile) => evaluate(`${doc}.getElementById(${id(desktop,mobile)}).click()`);
      const choose = (desktop,mobile,value) => evaluate(`(()=>{const s=${doc}.getElementById(${id(desktop,mobile)});s.value=${JSON.stringify(value)};s.dispatchEvent(new s.ownerDocument.defaultView.Event('change',{bubbles:true}));})()`);
      const snapshot = async (stage, runtime, selected = false) => {
        const s = await evaluate(`(()=>{const d=${doc}, root=d.getElementById('${phone?'m-new-choices':'manager-new-choices'}');return {
          stage:${JSON.stringify(stage)},choicesParent:root.parentElement.id,
          claudeModel:d.getElementById('${phone?'m-new-model':'claude-new-model'}').value,
          title:d.getElementById('${phone?'m-sub-title':'claude-new-sub-title'}').textContent,
          status:[...d.querySelectorAll('${phone?'#m-sub-status,#m-new-status':'#claude-new-status'}')].map(n=>n.textContent),
          selects:[...root.querySelectorAll('select,datalist')].map(n=>({id:n.id,value:n.value,visible:!!n.getClientRects().length,
            options:[...n.options].map(o=>({value:o.value,text:o.textContent}))}))};})()`);
        page.snapshots.push(s); verifyChoices(s,{phone,runtime,selected}); return s;
      };
      await call('Emulation.setDeviceMetricsOverride',{width:phone?390:1600,height:phone?844:1100,deviceScaleFactor:1,mobile:phone});
      await call('Page.navigate',{url:page.url});
      await until(() => evaluate("document.readyState==='complete'"), 'page load');
      if (!phone) await evaluate("(()=>{const s=document.getElementById('backend-select');s.value='claude';s.dispatchEvent(new Event('change',{bubbles:true}));})()");
      await until(() => evaluate(phone ? "typeof phoneManager !== 'undefined' && phoneManager.state.managers.length>0" :
        "!!document.getElementById('claude-sessions-frame')?.contentDocument?.getElementById('claude-new-sub')?.onclick && document.getElementById('claude-sessions-frame').contentWindow.opcSidebar?.managerNames().length>0"), 'Manager controls and names');
      const started = Date.now();
      await click('claude-new-sub','new-sub');
      if (phone) await evaluate("document.getElementById('m-new-options').open=true");
      await snapshot('sub-session Claude','claude');
      await choose('manager-new-runtime','new-runtime','opencode');
      await until(() => evaluate(`${doc}.getElementById(${id('opencode-new-model','new-oc-model')}).options.length>1`), 'real OpenCode catalog');
      await choose('opencode-new-agent','new-agent','manager-gpt');
      const sol = await evaluate(`[...${doc}.getElementById(${id('opencode-new-model','new-oc-model')}).options].find(o=>o.value==='openai/gpt-6.1-sol-fast'||o.textContent==='GPT-6.1 Sol Fast · openai')?.value`);
      assert.ok(sol, 'Sol Fast must exist');
      await choose('opencode-new-model','new-oc-model',sol);
      await choose('opencode-new-variant','new-oc-variant','xhigh');
      page.choiceReadyMs = Date.now() - started;
      const sub = await snapshot('sub-session OpenCode · manager-gpt · Sol Fast · xhigh','opencode',true);
      await call('Page.captureScreenshot').then(r => fs.writeFileSync(path.join(receipt,phone?'phone.png':'desktop.png'),Buffer.from(r.data,'base64')));
      await click('claude-new-manager','new');
      const fresh = await snapshot('New Manager OpenCode (same choices)','opencode',true);
      assert.deepEqual(fresh.selects,sub.selects,'New Manager and sub-session must share identical populated controls');
      await choose('manager-new-runtime','new-runtime','claude');
      await snapshot('New Manager Claude','claude');
      assert.deepEqual(page.exceptions,[], 'no browser exceptions');
      assert.deepEqual(page.console.filter(c=>c.type==='error'),[], 'no console errors');
      assert.deepEqual(page.blockedWrites,[], 'opening and choosing must make no write requests');
      page.passed = true;
      console.log(JSON.stringify({url:page.url,passed:true,choiceReadyMs:page.choiceReadyMs,
        options:sub.selects.map(s=>({id:s.id,count:s.options.length,value:s.value})),failedRequests:page.failedRequests}));
    }
    report.passed = true;
  } catch (error) { report.error = error.stack; throw error;
  } finally {
    fs.writeFileSync(path.join(receipt,'live.json'),JSON.stringify(report,null,2)+'\n');
    try { if (ws) await call('Browser.close'); else chrome.kill(); } catch { chrome.kill(); }
    ws?.close(); await sleep(1200);
    fs.rmSync(profile,{recursive:true,force:true,maxRetries:30,retryDelay:200});
  }
  return report;
}

module.exports = {verifyChoices,managerChoicesLive};
if (require.main === module) {
  const [origin,receipt,chromePath='C:/Program Files/Google/Chrome/Application/chrome.exe'] = process.argv.slice(2);
  if (!origin || !receipt) { console.error('Usage: node run_manager_choices_live.cjs ORIGIN RECEIPT_DIR [CHROME_PATH]'); process.exitCode=2; }
  else managerChoicesLive(origin.replace(/\/$/,''),receipt,chromePath).catch(e=>{console.error(e.stack);process.exitCode=1;});
}

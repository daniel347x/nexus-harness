// Windows Node + a fresh, extension-free Chrome profile. No live Manager App navigation.
const fs=require('node:fs'),path=require('node:path'),{spawn}=require('node:child_process');
const [origin,out,scratch,...shots]=process.argv.slice(2);
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
async function capture(shot){
  const phone=shot.startsWith('phone-'),width=phone?390:1600,height=phone?844:1000,scale=phone?3:2;
  const profile=path.join(scratch,'profile-'+shot),debug=10000+Math.floor(Math.random()*10000);
  const chrome=spawn('C:/Program Files/Google/Chrome/Application/chrome.exe',[
    '--headless=new','--disable-gpu','--no-first-run','--no-default-browser-check','--disable-extensions',
    '--hide-scrollbars','--user-data-dir='+profile,'--remote-debugging-port='+debug,'about:blank'],{stdio:'ignore'});
  let ws,seq=0;const pending=new Map();
  const call=(method,params={})=>new Promise((resolve,reject)=>{const id=++seq;pending.set(id,{resolve,reject});ws.send(JSON.stringify({id,method,params}));});
  const evaluate=async expression=>{const r=await call('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true});if(r.exceptionDetails)throw Error(JSON.stringify(r.exceptionDetails));return r.result?.value;};
  const until=async(expression,label)=>{for(let i=0;i<300;i++){try{if(await evaluate(expression))return;}catch{}await sleep(50);}throw Error('capture wait: '+label);};
  try{
    let target;for(let i=0;i<100;i++){try{target=(await(await fetch('http://127.0.0.1:'+debug+'/json')).json()).find(t=>t.type==='page');}catch{}if(target)break;await sleep(100);}
    if(!target)throw Error('Chrome did not open');
    ws=new WebSocket(target.webSocketDebuggerUrl);await new Promise(r=>ws.addEventListener('open',r,{once:true}));
    ws.addEventListener('message',e=>{const x=JSON.parse(e.data),p=pending.get(x.id);if(p){pending.delete(x.id);x.error?p.reject(Error(JSON.stringify(x.error))):p.resolve(x.result);}});
    await call('Page.enable');await call('Runtime.enable');
    await call('Emulation.setDeviceMetricsOverride',{width,height,deviceScaleFactor:scale,mobile:phone,screenWidth:width,screenHeight:height});
    await call('Emulation.setTimezoneOverride',{timezoneId:'America/New_York'});
    if(phone)await call('Emulation.setTouchEmulationEnabled',{enabled:true,maxTouchPoints:5});
    await call('Page.navigate',{url:origin+(phone?'/m':'/')});
    if(phone){
      await until('window.readmePhone&&document.querySelectorAll("#m-managers button").length>=2','phone Managers');
      await evaluate('readmePhone.selectManager(readmeFixture.manager.executionId)');
      await until('document.querySelector("#m-messages .m-final")','phone conversation');
      if(shot==='phone-programs'){
        await evaluate('document.getElementById("m-view-programs").click()');
        await until('document.getElementById("m-programs-frame").contentWindow?.readmeHistory?.state.inventory.length>5','phone Programs');
        await evaluate(`(()=>{const w=document.getElementById('m-programs-frame').contentWindow,h=w.readmeHistory;for(const core of ['MCI012','READM1','PHONE1'])h.state.lineageOpen.set('p:'+core,false);h.inventory();})()`);
      }else{
        await evaluate(`(()=>{const b=document.getElementById('m-draft');b.value='Reader first. Keep both audit results attached to the decision.';b.dispatchEvent(new Event('input',{bubbles:true}));})()`);
      }
    }else{
      await until('document.getElementById("claude-sessions-frame")?.contentWindow?.readmeHistory?.state.inventory.length>5','desktop sessions');
      await evaluate(`(()=>{const w=document.getElementById('claude-sessions-frame').contentWindow;return w.readmeHistory.browse(w.readmeFixture.manager);})()`);
      await until('document.getElementById("claude-sessions-frame").contentDocument.querySelector(".turn-final")','desktop conversation');
      await until('document.getElementById("target-pill").textContent.includes("Manager-Session-228")','validated desktop target');
      await until('document.getElementById("inspector-output").textContent.includes("Running")','selected tool output');
      await evaluate('document.getElementById("ctx-refresh").click()');
      await until('document.getElementById("ctx-info").textContent.includes("prose turns")','desktop Refine context');
      await evaluate(`(()=>{const w=document.getElementById('claude-sessions-frame').contentWindow,h=w.readmeHistory;h.setSideWidth(47);h.state.lineageOpen.set('s:cc-10000000-0000-4000-8000-000000000010',false);h.inventory();})()`);
      if(shot==='program-view'){
        await evaluate(`document.getElementById('claude-sessions-frame').contentWindow.opcOpenInView('[ap:MCI012]')`);
        await until(`document.getElementById('claude-sessions-frame').contentDocument.getElementById('desktop-view-frame')?.contentWindow?.opcGlimpseView?.state.held`,'program View');
        await evaluate(`(()=>{const w=document.getElementById('claude-sessions-frame').contentDocument.getElementById('desktop-view-frame').contentWindow;w.document.getElementById('g-rows-box').open=false;})()`);
      }
    }
    await sleep(1800);
    if(!phone){
      await evaluate('document.querySelector(".pane-toast-dismiss")?.click()');
      await sleep(700);
      await until('getComputedStyle(document.querySelector(".pane-toast")).opacity==="0"','dismissed desktop status');
    }
    const receipt=await evaluate(`(()=>{const all=[];function walk(w){const f=w.readmeFixture;if(f)all.push({page:w.location.pathname,unknown:f.unknown,errors:f.errors,text:w.document.body.innerText});for(const frame of w.document.querySelectorAll('iframe')){try{if(frame.contentDocument?.body)walk(frame.contentWindow);}catch{}}}walk(window);return all;})()`);
    fs.writeFileSync(path.join(scratch,shot+'.json'),JSON.stringify(receipt,null,2));
    if(receipt.some(r=>r.errors.length||r.unknown.length))throw Error('CAPTURE DIAGNOSTICS '+shot+' '+JSON.stringify(receipt.map(({page,unknown,errors})=>({page,unknown,errors}))));
    const png=await call('Page.captureScreenshot',{format:'png',fromSurface:true,captureBeyondViewport:false});
    fs.writeFileSync(path.join(out,shot+'.png'),Buffer.from(png.data,'base64'));
    const summary={shot,width:width*scale,height:height*scale,cssWidth:width,cssHeight:height,scale,source:phone?'m.html':'index.html',diagnostics:receipt.map(({page,unknown,errors})=>({page,unknown,errors}))};
    fs.writeFileSync(path.join(scratch,shot+'-receipt.json'),JSON.stringify(summary,null,2)+'\n');
    console.log(JSON.stringify(summary));
  }finally{
    try{if(ws)await call('Browser.close');else chrome.kill();}catch{chrome.kill();}ws?.close();
    await sleep(300);
  }
}
(async()=>{for(const shot of shots)await capture(shot);})().catch(e=>{console.error(e.stack);process.exitCode=1;});

// Windows Node + isolated Chrome CDP for a REAL phone/tablet layout: Emulation.setDeviceMetricsOverride (390×844,
// 744×1133, rotated 844×390) and touch emulation, with focus enabled so a synthetic focus behaves like the real page.
// The fixture asks for a layout with window.deviceRequest {width, height, mobile}; this applies it and resolves the
// page's deviceRequest handshake. No screenshots (the headless surface lagged a state); the fixture's numbered JSON
// checks are the receipt. Called by run_browser_checks.py; all page IO is the inert fixture server.
const fs=require('node:fs'),path=require('node:path'),{spawn}=require('node:child_process');
const [url,receipt,profile,chromePath,suite]=process.argv.slice(2),debug=9900+Math.floor(Math.random()*400);
const chrome=spawn(chromePath,['--headless=new','--disable-gpu','--no-first-run','--no-default-browser-check',
  '--user-data-dir='+profile,'--remote-debugging-port='+debug,'--window-size=1400,1000','about:blank'],{stdio:'ignore'});
const sleep=ms=>new Promise(r=>setTimeout(r,ms));let ws,seq=0;const pending=new Map();
async function until(test){const end=Date.now()+30000;while(Date.now()<end){const value=await test();if(value)return value;await sleep(50);}throw Error('fixture observer timeout');}
function call(method,params={}){return new Promise((resolve,reject)=>{const id=++seq;pending.set(id,{resolve,reject});ws.send(JSON.stringify({id,method,params}));});}
async function evaluate(expression){const r=await call('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true});if(r.exceptionDetails)throw Error(JSON.stringify(r.exceptionDetails));return r.result?.value;}
const metrics=(width,height)=>({width,height,deviceScaleFactor:2,mobile:true,screenWidth:width,screenHeight:height,
  screenOrientation:{type:width>=height?'landscapePrimary':'portraitPrimary',angle:width>=height?90:0}});
(async()=>{
  let finished=false;
  try{
    const target=await until(async()=>{try{return (await (await fetch('http://127.0.0.1:'+debug+'/json')).json()).find(t=>t.type==='page');}catch{return null;}});
    ws=new WebSocket(target.webSocketDebuggerUrl);await new Promise(r=>ws.addEventListener('open',r,{once:true}));
    ws.addEventListener('message',e=>{const x=JSON.parse(e.data),p=pending.get(x.id);if(p){pending.delete(x.id);x.error?p.reject(Error(JSON.stringify(x.error))):p.resolve(x.result);}});
    await call('Page.enable');await call('Runtime.enable');
    try{await call('Emulation.setFocusEmulationEnabled',{enabled:true});}catch(e){console.error('focus emulation unavailable: '+e.message);}
    await call('Emulation.setDeviceMetricsOverride',metrics(390,844));
    await call('Emulation.setTouchEmulationEnabled',{enabled:true,maxTouchPoints:5});
    await call('Page.navigate',{url});
    const deadline=Date.now()+90000;
    while(Date.now()<deadline){
      if(await evaluate('document.body?.dataset.testPassed||""')==='true'){break;}
      if(await evaluate('document.body?.dataset.testPassed==="false"')){
        const failed=await evaluate(`(document.getElementById('results')?.textContent||'').length>2`);
        if(failed)break;
      }
      const request=await evaluate('window.deviceRequest?JSON.stringify(window.deviceRequest):""');
      if(request){
        const {width,height,token}=JSON.parse(request);
        await call('Emulation.setDeviceMetricsOverride',metrics(width,height));
        await sleep(120);
        await evaluate('window.deviceRequest=null;window.deviceApplied='+JSON.stringify(token));
      }
      await sleep(40);
    }
    const dom=await evaluate('document.documentElement.outerHTML');
    const raw=await evaluate('document.getElementById("results")?.textContent||"{}"');
    const checks=JSON.parse(raw);
    fs.writeFileSync(path.join(receipt,suite+'.dom'),dom);
    fs.writeFileSync(path.join(receipt,suite+'.json'),JSON.stringify(checks,null,2)+'\n');
    console.log(JSON.stringify(checks));finished=true;
    if(checks.failed!==0)process.exitCode=1;
  }catch(e){
    console.error(e.stack);
    try{
      const raw=await evaluate('document.getElementById("results")?.textContent||"{}"');
      fs.writeFileSync(path.join(receipt,suite+'.json'),raw+'\n');
      fs.writeFileSync(path.join(receipt,suite+'.dom'),await evaluate('document.documentElement.outerHTML'));
    }catch(_){}
    process.exitCode=1;
  }finally{
    if(!finished)console.error('phone-device driver did not see a finished fixture');
    try{if(ws)await call('Browser.close');else chrome.kill();}catch{chrome.kill();}ws?.close();
  }
})();

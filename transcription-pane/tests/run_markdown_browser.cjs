// Windows Node + isolated Chrome CDP: real wall-clock timings, no virtual time.
// Called by run_browser_checks.py; all page IO is the inert fixture server.
const fs=require('node:fs'),path=require('node:path'),{spawn}=require('node:child_process');
const [url,receipt,profile,chromePath]=process.argv.slice(2),debug=9400+Math.floor(Math.random()*500);
const chrome=spawn(chromePath,['--headless=new','--disable-gpu','--no-first-run','--no-default-browser-check',
  '--user-data-dir='+profile,'--remote-debugging-port='+debug,'--window-size=1400,1000','about:blank'],{stdio:'ignore'});
const sleep=ms=>new Promise(r=>setTimeout(r,ms));let ws,seq=0;const pending=new Map();
async function until(test){const end=Date.now()+30000;while(Date.now()<end){const value=await test();if(value)return value;await sleep(50);}throw Error('fixture observer timeout');}
function call(method,params={}){return new Promise((resolve,reject)=>{const id=++seq;pending.set(id,{resolve,reject});ws.send(JSON.stringify({id,method,params}));});}
async function evaluate(expression){const r=await call('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});if(r.exceptionDetails)throw Error(JSON.stringify(r.exceptionDetails));return r.result.value;}
(async()=>{
  try{
    const target=await until(async()=>{try{return (await (await fetch('http://127.0.0.1:'+debug+'/json')).json()).find(t=>t.type==='page');}catch{return null;}});
    ws=new WebSocket(target.webSocketDebuggerUrl);await new Promise(r=>ws.addEventListener('open',r,{once:true}));
    ws.addEventListener('message',e=>{const x=JSON.parse(e.data),p=pending.get(x.id);if(p){pending.delete(x.id);x.error?p.reject(Error(JSON.stringify(x.error))):p.resolve(x.result);}});
    await call('Page.enable');await call('Runtime.enable');await call('Page.navigate',{url});
    await until(()=>evaluate('document.body?.dataset.testPassed'));
    const dom=await evaluate('document.documentElement.outerHTML'),checks=await evaluate('JSON.parse(document.getElementById("results").textContent)');
    fs.writeFileSync(path.join(receipt,'message-markdown.dom'),dom);
    fs.writeFileSync(path.join(receipt,'message-markdown.json'),JSON.stringify(checks,null,2)+'\n');
    console.log(JSON.stringify(checks));if(checks.failed!==0)process.exitCode=1;
  }catch(e){console.error(e.stack);process.exitCode=1;}
  finally{try{if(ws)await call('Browser.close');else chrome.kill();}catch{chrome.kill();}ws?.close();}
})();

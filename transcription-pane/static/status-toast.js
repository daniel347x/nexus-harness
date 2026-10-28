'use strict';

// @beacon[
//   id=opc-pane@statusToast,
//   slice_labels=opc-pane,
//   kind=ast,
//   comment=Presentation-only upgrade of the phone and desktop's existing status owners: a zero-layout toast fades after three seconds (identical polling notes never renew it), errors persist until dismissed/replaced, and full page-local notes remain in Status messages. The phone toast occupies only visible conversation space and respects the frame's safe-area padding; a maximized draft gets a badge in its non-editable thumb margin's content width, never over dictation or action buttons. Existing status text/classes/subjects and receipt logic are untouched.,
// ]
function createStatusToast(node, doc = document) {
  const win=doc.defaultView, phone=node.id === 'm-status', recall=doc.getElementById(phone ? 'm-status-history' : 'status-history');
  const make=(tag,cls,text)=>{const n=doc.createElement(tag); if (cls) n.className=cls; if (text) n.textContent=text; return n;};
  const toast=make('aside','pane-toast'), open=make('button','pane-toast-open','History'), dismiss=make('button','pane-toast-dismiss','×');
  const log=make('section','pane-status-log'), head=make('header'), close=make('button','','Close'), entries=make('div','pane-status-entries');
  open.type=dismiss.type=close.type='button'; dismiss.setAttribute('aria-label','Dismiss status');
  log.hidden=true; log.setAttribute('role','dialog'); log.setAttribute('aria-label','Status messages');
  head.append(make('strong','','Status messages · this page'),close); log.append(head,entries);
  // Move the SAME node: notice()/status()/resolveStatus() and their exact text still belong to the page.
  toast.append(node,open,dismiss); doc.body.append(toast,log);
  node.setAttribute('role','status'); node.setAttribute('aria-live','polite'); node.setAttribute('aria-atomic','true');
  let last='', timer=0, frame=0, visible=false, empty=true, error=false, priorFocus=null, movedFocus=false;
  const clear=()=>{win.clearTimeout(timer); timer=0;};
  const hide=()=>{clear(); visible=false; toast.dataset.visible='false';};
  const expire=()=>{clear(); if (!error && visible) timer=win.setTimeout(hide,3000);};
  const screen=()=>({top:win.visualViewport?.offsetTop || 0, height:win.visualViewport?.height || win.innerHeight,
    left:win.visualViewport?.offsetLeft || 0, width:win.visualViewport?.width || win.innerWidth});
  function position() {
    frame=0; const s=screen();
    // Reuse the shell's computed safe-area padding: a Home Screen app must not put a toast under the notch.
    const app=phone && doc.getElementById('m-app'), safeTop=app ? parseFloat(win.getComputedStyle(app).paddingTop) || 8 : 8;
    log.style.setProperty('--status-screen-top',s.top+'px'); log.style.setProperty('--status-screen-height',s.height+'px');
    const compact=phone && doc.body.dataset.maximized === 'draft';
    toast.dataset.compact=String(compact);
    open.textContent=compact ? (error ? '!' : 'i') : 'History';
    open.setAttribute('aria-label','Read full status messages'+(empty ? '' : ': '+node.textContent));
    open.title=node.textContent;
    let left=s.left+12, top=s.top+safeTop, width=Math.min(480,s.width-24);
    if (phone) {
      const region=doc.getElementById(compact ? 'm-draft-margin' : 'm-conversation'), area=region?.getBoundingClientRect();
      if (compact && area?.width) {
        // Its right padding extends into the safe-area inset; the badge belongs in the 44px content strip, not that inset.
        const usable=area.width-(parseFloat(win.getComputedStyle(region).paddingRight) || 0);
        left=area.left+Math.max(0,(usable-34)/2); top=Math.max(s.top+safeTop,area.top+8); width=34;
      }
      else if (area?.width) {
        left=Math.max(s.left+8,area.left+8); width=Math.min(area.right-8,s.left+s.width-8)-left;
        top=Math.max(s.top+safeTop,area.top+8);
        // Bound even a very short visible conversation: never leak onto the textarea or its buttons.
        toast.style.maxHeight=Math.max(0,Math.min(area.bottom,s.top+s.height)-top-4)+'px';
      }
    }
    if (compact || !phone) toast.style.maxHeight='';
    toast.style.setProperty('--toast-left',left+'px'); toast.style.setProperty('--toast-top',top+'px');
    toast.style.setProperty('--toast-width',Math.max(0,width)+'px');
    // With another phone view selected there is no conversation to overlay. Its Status menu retains the notes.
    toast.style.display=phone && doc.getElementById('m-chat-view')?.hidden ? 'none' : '';
  }
  const schedule=()=>{if (!frame) frame=win.requestAnimationFrame(position);};
  function capture() {
    const text=[...node.childNodes].map(n=>n.textContent).join('\n'), failed=node.dataset.error === 'true' || node.classList.contains('err');
    const signature=JSON.stringify([text,failed]); if (signature === last) return;
    last=signature; empty=!text.trim(); error=failed; clear();
    toast.dataset.error=String(error); if (recall) recall.dataset.error=String(error && !empty);
    if (empty) { hide(); schedule(); return; }
    const entry=make('div','pane-status-entry'), time=make('time','',new Date().toLocaleTimeString());
    entry.dataset.error=String(error); time.dateTime=new Date().toISOString();
    entry.append(time,doc.createTextNode((error ? 'Error · ' : '')+text)); entries.append(entry);
    visible=true; toast.dataset.visible='true'; position(); expire();
    if (!log.hidden) entries.scrollTop=entries.scrollHeight;
  }
  function showLog(event) {
    priorFocus=doc.activeElement; log.hidden=false; position(); entries.scrollTop=entries.scrollHeight; clear();
    // Deliberate keyboard activation gets a focusable way back; a phone tap must not discard dictation focus/range.
    movedFocus=!phone || event?.detail === 0;
    if (movedFocus) close.focus({preventScroll:true});
  }
  function closeLog() {
    if (log.hidden) return;
    log.hidden=true; if (movedFocus) priorFocus?.focus?.({preventScroll:true}); expire();
  }
  open.addEventListener('click',showLog); recall?.addEventListener('click',showLog);
  dismiss.addEventListener('click',()=>{hide(); if (recall) recall.dataset.error='false';});
  close.addEventListener('click',closeLog);
  // Never blur the dictation box as a toast is tapped; normal keyboard activation still works.
  for (const control of [open,dismiss,close,recall].filter(Boolean)) control.addEventListener('pointerdown',e=>e.preventDefault());
  doc.addEventListener('keydown',e=>{if (e.key === 'Escape' && !log.hidden) { e.preventDefault(); e.stopImmediatePropagation(); closeLog(); }},true);
  toast.addEventListener('pointerenter',clear); toast.addEventListener('pointerleave',expire);
  toast.addEventListener('focusin',clear); toast.addEventListener('focusout',expire);
  const observer=new win.MutationObserver(capture);
  observer.observe(node,{childList:true,subtree:true,characterData:true,attributes:true,attributeFilter:['class','data-error']});
  new win.MutationObserver(schedule).observe(doc.body,{attributes:true,attributeFilter:['class','data-maximized']});
  if (typeof win.ResizeObserver === 'function') {
    const resize=new win.ResizeObserver(schedule);
    for (const n of [doc.getElementById('m-conversation'),doc.getElementById('m-draft-margin'),doc.getElementById('m-composer')].filter(Boolean)) resize.observe(n);
  }
  win.addEventListener('resize',schedule); win.addEventListener('scroll',schedule,true);
  win.visualViewport?.addEventListener('resize',schedule); win.visualViewport?.addEventListener('scroll',schedule);
  capture(); position();
  return {showLog,closeLog,dismiss:hide};
}

globalThis.opcStatusToast={create:createStatusToast};
const paneStatus=document.getElementById('m-status') || document.getElementById('status');
if (paneStatus) createStatusToast(paneStatus);

'use strict';

// Outdoor glare mode for the phone pages: /m and the Programs page in its frame (see setGlare).
(() => {
  const KEY = 'pane_m_v1:glare', root = document.documentElement, follower = root.hasAttribute('data-glare-follow');
  function glareSaved() {
    if (follower) { try { return window.parent !== window && window.parent.opcGlare?.on?.() === true; } catch (_) { return false; } }
    try { return localStorage.getItem(KEY) === 'on'; } catch (_) { return false; }
  }
  function glareOn() { return root.dataset.glare === 'on'; }
  // This page only: the <html> attribute, the toggles' pressed state, and an 'opc-glare' event for anything that
  // measures its text (the Programs list re-fits its telemetry rows; a following conversation stays at its newest).
  function applyGlare(value) {
    const next = value === true, changed = next !== glareOn();
    if (next) root.dataset.glare = 'on'; else delete root.dataset.glare;
    for (const button of document.querySelectorAll('[data-glare-toggle]')) button.setAttribute('aria-pressed', String(next));
    if (changed) dispatchEvent(new CustomEvent('opc-glare', {detail: {on: next}}));
    return next;
  }
  // This page and its same-origin frames, at once (a frame loaded later reads it from here as it starts).
  function showGlare(value) {
    const next = applyGlare(value);
    for (const frame of document.querySelectorAll('iframe')) { try { frame.contentWindow?.opcGlare?.apply?.(next); } catch (_) { /* not ours */ } }
    return next;
  }
  // @beacon[
  //   id=opc-pane@setGlare,
  //   slice_labels=opc-pane,
  //   kind=ast,
  //   comment=Outdoor glare mode (glare.js, loaded in the phone pages' <head>): data-glare="on" on <html> before the first paint, and glare.css brightens every dim grey and lifts the small print through the pages' own theme variables. The ☀ button ([data-glare-toggle], /m) flips it here: /m keeps the choice (pane_m_v1:glare) and passes it to its same-origin frames at once; a page marked data-glare-follow (the Programs sidebar in /m's frame) never touches storage and starts from the page around it. Other open /m tabs follow through the storage event.,
  // ]
  function setGlare(value) {
    const next = value === true;
    if (!follower) { try { if (next) localStorage.setItem(KEY, 'on'); else localStorage.removeItem(KEY); } catch (_) { /* still shown on this page */ } }
    return showGlare(next);
  }
  applyGlare(glareSaved());
  if (!follower) addEventListener('storage', event => { if (event.key === KEY || event.key === null) showGlare(glareSaved()); });
  // This script runs in <head>; the toggles are wired once the page is parsed.
  const wire = () => { for (const button of document.querySelectorAll('[data-glare-toggle]')) {
    button.setAttribute('aria-pressed', String(glareOn())); button.addEventListener('click', () => setGlare(!glareOn())); } };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', wire, {once: true}); else wire();
  globalThis.opcGlare = Object.freeze({on: glareOn, set: setGlare, apply: applyGlare, toggle: () => setGlare(!glareOn()), key: KEY});
})();

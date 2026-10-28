const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const context = { TextEncoder };
vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../tool-record-text.js'), 'utf8'), context);
const api = context.opcToolText;
function label() {
  return { style: '', writes: 0, getAttribute() { return this.style; }, setAttribute(k, v) { this.style = v; this.writes++; } };
}
const values = value => {
  const el = label(); api.emphasizeToolSize(el, value);
  return [...el.style.matchAll(/:([0-9.]+)/g)].map(match => Number(match[1]));
};
test('unknown/known zero neutral; log-ish warmth progresses smoothly and caps at 1 MiB', () => {
  assert.deepEqual(values(null), [0, 0]); assert.deepEqual(values(undefined), [0, 0]);
  assert.deepEqual(values(0), [0, 0]);
  assert.equal(api.size(null), '—'); assert.equal(api.size(0), '0 KiB');
  let last = 0;
  for (const n of [1, 1024, 16*1024, 64*1024, 256*1024, 512*1024, 1024*1024]) {
    const [warmth, lift] = values(n); assert.ok(warmth > last); last = warmth;
    assert.ok(lift >= 0 && lift <= 4);
  }
  assert.ok(values(64*1024)[0] > 30 && values(256*1024)[0] > 60);
  assert.deepEqual(values(1024*1024), [100, 4]); assert.deepEqual(values(8*1024*1024), [100, 4]);
  assert.deepEqual(values(256*1024), [values(256*1024)[0], 0]);
});
test('emphasis is idempotent, resets reused labels and does not alter numeric/Unicode KiB', () => {
  const el = label(); el.textContent = 'In: 1024.0 KiB';
  api.emphasizeToolSize(el, 1024*1024); api.emphasizeToolSize(el, 1024*1024);
  assert.equal(el.writes, 1); assert.equal(el.textContent, 'In: 1024.0 KiB');
  api.emphasizeToolSize(el, null); assert.equal(el.writes, 2); assert.match(el.style, /warmth:0.000%/);
  assert.equal(api.bytes('A😀é'), 7); assert.equal(api.size(api.bytes('A😀é')), '0.007 KiB');
});
test('entire foreground gradient has >=4.5:1 contrast on actual light/dark CodeNomad and Pane surfaces', () => {
  const rgb = hex => hex.match(/\w\w/g).map(x => parseInt(x, 16));
  const luminance = rgb => rgb.map(x => x/255).map(x => x <= .04045 ? x/12.92 : ((x+.055)/1.055)**2.4).reduce((s,x,i) => s+x*[.2126,.7152,.0722][i],0);
  const mix = (a,b,t) => a.map((x,i) => x*(1-t)+b[i]*t);
  const palettes = [
    ['59616e','b91c2c','111827',['ffffff','f8f8f8','f5f5f5']],
    ['a6adb9','ff8080','cfd4dc',['1a1a1a','212529','2a2a2a']],
    ['a6adb9','ff8080','e6e8ec',['1d2027']]
  ];
  for (const [gray,red,primary,surfaces] of palettes) {
    for (const surface of surfaces) for (const selected of [false,true]) {
      const background = selected ? mix(rgb(surface),rgb(primary),.1) : rgb(surface);
      for (let step = 0; step <= 1024; step++) {
        const [warmth] = values(step*1024), color = mix(rgb(gray),rgb(red),warmth/100);
        const [lo,hi] = [luminance(color),luminance(background)].sort((a,b)=>a-b);
        assert.ok((hi+.05)/(lo+.05) >= 4.5, `${surface} selected=${selected} ${step} KiB`);
      }
    }
  }
  const css = fs.readFileSync(path.join(__dirname, '../tool-previews.css'),'utf8');
  for (const hex of ['59616e','b91c2c','a6adb9','ff8080']) assert.ok(css.includes('#'+hex));
});

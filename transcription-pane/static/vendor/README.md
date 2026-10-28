# Local Markdown and code-highlighting dependencies

Unmodified npm release assets; no build step or runtime CDN. Licenses beside them.

| Asset | Version / upstream | SHA-256 |
|---|---|---|
| `marked.umd.js` | marked 18.1.1, https://registry.npmjs.org/marked/-/marked-18.1.1.tgz | `b82b9334406d8bdd16b6236c08de1cb65b2a859873ca9503d9cc0b292ca3c12a` |
| `purify.min.js` | DOMPurify 3.4.17, https://registry.npmjs.org/dompurify/-/dompurify-3.4.17.tgz | `cd37898f9782adf54820c413191b5691803f0f137c799d6edcfd38523b4c2c31` |
| `highlight.min.js` | highlight.js 11.11.1 (@highlightjs/cdn-assets), https://registry.npmjs.org/@highlightjs/cdn-assets/-/cdn-assets-11.11.1.tgz | `c4a399dd6f488bc97a3546e3476747b3e714c99c57b9473154c6fb8d259b9381` |

`message-markdown.js` owns the display policy. HTML is escaped by the parser and
the result is sanitized with a small tag/attribute allow-list. Images are text,
not remote loads. Updates must retain those guarantees and rerun the Markdown
browser fixture (including malicious HTML/links) and all retained pane suites.

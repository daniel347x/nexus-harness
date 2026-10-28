# Ordinary long Markdown: investigated alternative, not a shipped fix

2026-10-10 · installed CodeNomad **0.20.1** · extension remains **0.1.7**.

## Conclusion

The message is stored whole; CodeNomad's shared Markdown component applies the
**tool-output** 10,000-character rendering cap to ordinary user/assistant text too.
The native Markdown parser itself renders the entire long outline successfully.

Do **not** import the installed renderer into the extension's isolated world as a
quick repair. Its compiled dependency graph includes the application entry point,
which boots another client against the same document. Also, `escapeRawHtml: true`
sanitizes raw HTML fragments, **not all generated HTML**: a Markdown link with a
`javascript:` URL survives. It is not a sufficient security boundary for new HTML
injection by our extension.

No production extension fix is shipped here. The concrete preferred alternative
is a small, maintained **CodeNomad source patch/rebuild**, submitted upstream or
kept in a version-pinned source fork. Do not patch the installed npm bundles.
That keeps rendering, streaming, exact part identity, copy actions, layout
notifications and cache ownership in CodeNomad's existing components rather than
building a second transcript renderer/bridge in the extension.

## Evidence

The task supplied read-only stored-message verification: assistant
`msg_1281bfd38001qDLN10kiT8KFDQ` in `ses_ed89e199bffebgxP7i0NJlBvQG` has one text
part, 18,237 characters / 18,391 UTF-8 bytes, finish `stop`, no error, and the complete
closing paragraph ending “No doctrine or program behaviour has been changed.”
The user's `msg_127fbd19d001BBqoThQ6GXdqJc` has 19,750 characters. This establishes
display truncation, not whether the user's original input suffered any earlier
pre-submission loss; that comparison still requires the original input.

Independently inspected installed `package.json` (version 0.20.1) and original
TS/TSX `sourcesContent`, without editing the installation:

- `App-CHtzrtYl.js.map`: `components/markdown.tsx` exports
  `getMarkdownTextForRender(content)`, returning `limitToolOutputForRender(content)`.
  `Markdown.resolved` always applies it before rendering. The extra source-copy
  button does not make the remaining text readable in the conversation.
- `components/message-part.tsx`: ordinary text uses `escapeRawHtml`; user text
  additionally uses `literalRawHtml`. Message items identify instance/session/message;
  text containers identify exact part ID and `data-part-type="text"`. User pasted
  sections can have derived `:display:<index>` renderer IDs and disclosure controls;
  these cannot safely be treated as a stored part with that ID.
- `markdown-B3IPZgpj.js.map`: `lib/markdown.ts` exposes `renderMarkdown` with no
  text cap, native lists/tables/math/code rendering, and raw-fragment sanitization.
- Compiled `markdown-B3IPZgpj.js` imports `main-CmyWOck6.js` (entity decoding/escaping),
  `index-wJBJOVOC.js` (translations/preload), `monaco-viewer-DJNtcp6e.js` (logger), and
  vendor bundles. `main-CmyWOck6.js.map` includes `main.tsx`, whose top-level
  `void bootstrap()` initializes client state and renders into `#root`.

An **isolated real Chrome fixture with the unmodified installed renderer** confirms:

1. >10k user and assistant outlines retain their exact tail, all 150 headings,
   tables, lists and code including blank lines.
2. Raw assistant scripts/event attributes are removed; user HTML remains literal.
3. A generated Markdown `javascript:` link remains in the inert parsed output.
4. Import alone starts application initialization, including `/api/events`,
   workspace/storage/meta/update reads. Fetch and EventSource are blocked/mocked
   in the fixture, so no real application APIs or external hosts are contacted.

This proves the standalone import hazard. It does **not** claim that a MAIN-world
import in an already-running page would necessarily bootstrap twice: that world
can share the page's module registry. A production MAIN-world solution still needs
a deliberately designed bridge, version/CSP compatibility tests, whole-output
sanitization, native settings/control/cache coordination, and stale-result guards.
None of that is implemented or claimed tested here. No Chrome isolated-world
dynamic-import/CSP compatibility claim is made; the fixture is a standalone page.

## Concrete source patch to commission

Patch original source, build and test in a pinned CodeNomad source checkout:

1. Add a `truncateForRender?: boolean` prop to `Markdown`, default **true** so all
   tool-output uses retain today's cap. Change `getMarkdownTextForRender` to accept
   that flag: capped content when true, original content when false.
2. Explicitly pass `truncateForRender={false}` from **ordinary text** Markdown
   call sites in `MessagePart`, including inline user display segments and pasted
   text disclosure Markdown. Do not blindly disable truncation for tool/reasoning
   renderers or globally change `TOOL_OUTPUT_RENDER_CHARACTER_LIMIT`.
3. Include capped/full mode in snapshot request identity, render-cache key **and
   cache mode matching**. A cached cropped entry must never satisfy a full request
   just because the stored part's version is unchanged. Audit local `renderCache`
   and global cache matching, not only the text hash fallback.
4. Keep the existing native renderer, user-literal/assistant-escaped policy,
   code wrap/copy delegation, message actions and `onRendered` notification.
   The truncated-only extra source-copy affordance can be hidden in full mode;
   native message copy stays intact. No stored record or prompt text changes.
5. For exceptionally large ordinary text, use an explicit native in-page
   “Render full message” disclosure above a chosen rendering budget. Never silently
   append a cropped suffix and call that complete. The current ~18k answer should
   render automatically without clicking or external copying. The budget needs a
   measured performance decision, not a new unexplained arbitrary limit.
6. Separately harden native Markdown link/image URL handling with an allowlist and
   security tests. Do not treat raw-fragment sanitization as whole-output safety.

Required **follow-up implementation** tests: >10k user/assistant tails; all display
segments; preexisting capped cache entries; code/tables/lists/math; streaming and
completion/remount; exact native message/part actions; unchanged tool caps; unsafe
raw HTML and generated URLs; huge-message in-page expansion/performance. This
investigation has **not** implemented or run those native component regressions.

If extension-only ownership is mandatory, a separately scoped alternative is to
bundle a standalone audited renderer and sanitizer locally (no CDN/runtime remote
code), or refactor/build a standalone native renderer with its dependencies. Then
reuse exact authenticated record association and bounded reads while implementing
streaming, delayed records, cancellation/remount, settings and control delegation.
This is feasible in principle but materially larger than the current three-module
extension and is not a faithful quick cap bypass.

## Reproduce the diagnostic safely

From the extension directory, in a terminal used only for this fixture:

```sh
python3 tests/serve-native-markdown-investigation.py \
  --assets /home/daniel347x/.local/codenomad/node_modules/@neuralnomads/codenomad/public/assets
```

Use an isolated Chrome profile at
`http://127.0.0.1:18990/tests/native-markdown-investigation.html`, **not port 9899**.
For headless checks use `--virtual-time-budget=8000 --dump-dom`. The body must
report `data-test-passed="true"` (11 diagnostic checks). This server serves only
static fixture/asset files, is loopback-only, and never proxies APIs. Stop it
after testing. The fixture parses renderer output in an inert template, never
mounting untrusted message HTML. It is absent from the extension manifest.

Checks run: native renderer fixture 11/11; existing Node extension tests 22/22;
existing browser spacing 10/10, tool previews 20/20, thinking watcher 10/10.
An initial browser regression run used a misconfigured scratch static server
that omitted extension CSS/JS and failed; after correcting static routing, every
fixture passed. No live tab injection, prompts, installed-package edits, service
restarts or map refreshes. No activation step: runtime code/version are unchanged.

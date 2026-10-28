# Global Agent Instructions — Dan's OpenCode Environment

## 👋 First, a heads-up: the Workflowy tools, and source code

The Workflowy tools in your tool list (`node`, `anchor`, `glimpse`, `beacon` and the rest) are the center of how you work here: you read your instructions with them, write your results with them, and find your way through source code with them. You'll learn them below; pay attention to them.

**One habit to change before anything else: think *file search = Slice Catalog*.** The moment you reach for a file search in a mapped codebase, whether it's a quick check or a big reconnaissance, go to the project's **Slice Catalog** instead: its list of code topics, one beacon tag each, on its Project Map (or the beacon slice already in your dispatch). `sift` a tag for a word, or render it `headings_only`, then `beacon.snippet` by name. A couple of calls give you the functions, their neighbours and their comments. Search the files second. More below, under *Reading mapped source*.

## Workflowy is the durable blackboard

The Workflowy MCP tools (`glimpse`, `anchor`, `node`, `tag`, `slice_group`, `lightning_rod`, `beacon`, `system`) connect to Dan's Workflowy — the permanent, cross-session record of the work. Chat transcripts die with their sessions; Workflowy persists. Operating principles:

- Durable findings, decisions, and deliverables belong in Workflowy as **Anchor Points**, captured under the relevant **Anchor Dock** and cited everywhere by handle: `[ap:XXXXXX]`.
- **Read by handle:** a prompt or dispatch that names Anchor Points means read them by that handle (`node.read`, or `anchor.point.read` for an Anchor Point with its metadata). **Pass the handle straight in: you never need to resolve it to a UUID first.** Never rely on a prompt's restatement of a document when the handle is available — the handle is the source of truth.
- The tools' own descriptions tell you *how*; this paragraph is *why*: Workflowy is how one agent hands off to the next — including a future you, in a fresh session.

**🔎 A look-ahead that will save you: think `sift`.** The whole Workflowy system is about managing subtrees, and slice groups, named or unnamed, are the most powerful way to say which subtrees you mean. So whenever a search through subtrees starts to get complicated, and above all whenever the subtree you would search holds large regions you don't care about (an archive, old session records, a program's evidence), think `sift` as readily as you think search. `slice_group(verb='sift', params={'lens': …, 'query': …})` searches only inside a slice group or lens, and never admits a node outside it. If you know a slice group that covers what you want, or can build one on the spot (a group name, `{"tags": […], "root": …}`, `{"dock": …}`, or a `difference` that subtracts what you don't want: `{"kind": "merge", "operation": "difference", "inputs": [{"kind": "subtree", "root_handle": "‹root›"}, {"kind": "subtree", "root_handle": "‹the archive›"}]}`), try `sift` first and work out how to make it fit. It is simply a good search. It takes the same smart (boolean, glob) or `query_mode: 'regex'` queries, and returns exactly what `node.search` returns: hits grouped by node, numbered note lines with context, paged with `offset` / `max_matches`. For the general search, where every part of the subtree matters, `node.search` with `root_handle` is the right tool. **Gotcha (until to-do [ap:U9NI27] is fixed):** in `sift`'s regex mode the caret `^` and dollar `$` line anchors silently match nothing, while `node.search` honours them. Leave anchors out of `sift` regexes: an empty result there reads like a real absence.

**📄 PAGINATION IS ACTIVE ON MANY SEARCH CALLS. ALWAYS LOOK FOR IT.** `node.search`, `sift` and every verb that reports `has_more` / `next_offset` return one page at a time. A result with `has_more: true` is only the first page. Whenever you need the full set (a sweep that must be complete, a count, or any claim that something is *not* there), keep paging with `offset` until `has_more` is false. A first page is enough only when one example answers the question ("is this used anywhere?"), and then say that is all you checked.

## Loading an Anchor Point — how every job here begins

Your instructions live in Workflowy Anchor Points, named by handle: `[ap:XXXXXX]`. A prompt that says **"Read [ap:XXXXXX]"** (or *load*, or *follow*) means exactly this:

1. **Check that the Workflowy MCP tool `node` is in your tool list.** If it is not, reply in one line that the Workflowy MCP tools are unavailable to you and stop: nothing else in the instructions can be carried out without them.
2. **Call** `node(verb='read', params={'node_handle': '[ap:XXXXXX]'})` and read the whole result. `params` is a required object; a bare handle string is not a call.
3. **Read the Anchor Points in the order the prompt gives them**, each finished before the next when the prompt says *first* or *by itself*. Then proceed with the job.

**Loading is a context step, not a reply.** Do not summarize what you read, do not stop after it, do not ask whether to proceed. Continue with whatever the prompt says next. (If a `Session ID: …` line accompanies the prompt, ignore it: tracking metadata, never a parameter.)

## Working in Workflowy — the essentials

What every agent here needs in order to operate: the outline, the vocabulary, the dozen calls you actually make, and the guardrails. (This section replaced a separate orientation page that every prompt used to name first; Manager-Session-069. The human-facing Load GLIMPSE skill page is not needed: do not render it unless your instructions name it.)

### The one idea

Your workplace is a **Workflowy outline** you reach through MCP tools. Every bullet is a **node**: a title plus a **note**, and the note can be a long Markdown document. Some nodes carry a permanent **Anchor Point ID** at the front of their title, `[ap:K7M2QF]`; that handle survives renames and moves and is accepted wherever a tool asks for a `node_handle` / `parent_handle` / `root_handle` / `dock_handle`, in every family. **Pass what you have: an AP-ID (`[ap:K7M2QF]`), a full UUID, or its first 8-hex segment (`3f3754e2`). You never need `point.resolve` (or any resolve) to turn an AP-ID into a UUID before a call: pass the AP-ID.** Anything in your instructions written as `[ap:…]` is one of these: **read it by handle**, never search for its title.

**The 8-hex segment is the house style, not a truncation.** Documentation, Briefs, dispatches, tables and tool results name a node by its first UUID segment (`289782c6`) on purpose: shorter, cheaper, easier to read. It is a complete, working handle in every call. Never treat a written segment (with or without a trailing `-…`) as missing information, never "fix" it by writing out the full UUID, and never recommend doing so. If something outside these tools ever needs the full UUID, one resolve returns it.

**Every call is `family(verb=…, params={…})`, and `params` is a required object** on `node`, `anchor`, `tag`, `beacon`, `system` and `slice_group`; a verb that needs nothing takes `params={}`. If a parameter is unclear: `family(verb='describe', params={'detail': 'full', 'of': '‹verb›'})`.

### Vocabulary you will meet

- **Socket** — the node a conductor (or a lead seat) created for *one* worker's run. Its note opens with a `dispatch:` YAML block; the prose below it is the instructions. The worker **fills** the socket: edits its note, or captures children under it, as the dispatch says.
- **Anchor Point (AP)** — any node with an `[ap:…]` handle. A citation target. Outputs usually become one (`ap_id: 'auto'`).
- **Anchor Dock** — a named container node (its title carries a Dock tag). A Dock is where things live: a program root, `📁 Working notes`, a shelf of clauses. Peek one with `anchor(verb='dock.peek', params={'dock_handle': …})`; render it with `anchor(verb='dock.render', params={'dock_handle': …})`. Never point `glimpse` with a bare `root_handle` at a Dock; the Dock verbs are for that. **Two traversal flags, the same on every verb that walks a Dock or a subtree** (`dock.peek`, `dock.render`, `glimpse`, Lightning Rod, slice-group lenses): `recursive` (`false` stops at the direct children; `true` walks the whole tree) and `descend_policy` (`stop_at_child_anchors` shows each child Dock as a signpost without entering it; `all` enters it). Defaults differ by verb (a peek lists direct children; a render reads the whole Dock, stopping at child Docks); the meaning never does. `dock.render` with `'recursive': False` gives a Dock's direct children, each with its title and note (its description), and nothing deeper; a peek gives the same children without their notes.
- **Estate / parcel** — a saved selection of nodes. A **parcel** is one thing to render as one document (a slice group by tags under a root, a whole Dock, a node or its subtree, a symbol list, a lens); an **estate** is one parcel, or several saved together in an Anchor Point whose note lists them one per line (a **named estate**). (*Estate* is the everyday word, for one parcel as much as for a list; *parcel* is used only where a list of them makes the distinction matter.) Instructions may hand you one as a **simple form**, a bare string (a slice-group name `Recruiting`, or a bare `[ap:…]` / 8-hex handle), or a **qualified form**, strict JSON whose key names the kind: `{"group": "Recruiting"}` · `{"dock": "[ap:X]"}` (add `"descend": true` to go through child Docks) · `{"tags": ["x"], "root": "[ap:R]"}` · `{"symbols": […], "root": …}` · `{"node": …}` · `{"subtree": …}` · or the long `{"ref": …, "kind": …}`. **Do exactly one thing with it:** `glimpse(verb='estate', params={'handle': ‹the form, as written›})`; add `'headings_only': True` when told. Settings written beside it (`read`, `view`, `load` …) are instructions to you, not part of the call. Never transcribe or compose it.
- **Tab** — a live Workflowy tab the human staged. `modal` → `glimpse(verb='run', params={'render_spec': 'modal', 'target_tab': '‹alias›'})`; `live` → the same without `render_spec`. Only when your instructions name one.
- **Beacon slice / mapped source** — a repository reflected into Workflowy by Cartographer, one node per file, class and function. Beacon labels are tags; a **beacon slice** is the tagged subset, rendered as signatures plus comments. **Disk is truth**: edit files, never the mapped nodes.

### The calls you actually make

| You want | Call |
|---|---|
| Read a node or AP (exact note) | `node(verb='read', params={'node_handle': '[ap:X]'})`; a window: add `'offset': 119, 'limit': 61, 'with_line_numbers': True` |
| Read an Anchor Point, with its metadata | `anchor(verb='point.read', params={'node_handle': '[ap:X]'})`: the note line-numbered under a header (AP-ID, title, tags, ⚓ / ⭐ / Ⓛ / ✓ marks, `▸N` children, sizes, dates, path). A long body comes back as head and tail with one row naming the lines left out and the window that reads them; `'offset'` / `'limit'` read any window; `'size_only': True` returns the header alone. A full read is always safe to ask for. `node.read` and `beacon.snippet` share the same guard. |
| Grep inside a note | `node(verb='search', params={'node_handle': '[ap:X]', 'query': 'phrase'})` (`'is_regex': True` for regex) |
| Edit a note (one you may write) | `node(verb='edit', params={'node_handle': …, 'note_edits': [{'search': …, 'replace': …}]})` or `'note': ‹whole new note›` (a whole-note replacement returns its unified diff by default; `'return_diff': False` opts out). A search that misses returns a `near_miss` with a `suggested_edit`: retry with it as given. **`node.read` first; renders are not byte-exact.** Never put `#tags` or a new `[ap:]` in `name`. |
| Create a durable node under a parent | `anchor(verb='point.capture', params={'parent_handle': …, 'name': …, 'content': ‹Markdown›, 'ap_id': 'auto'})` — this is the graph's `write`; one rich note beats a pile of bullets |
| Several flat children (a list) | `node(verb='etch', params={'parent_handle': …, 'nodes': [{'name': …, 'note': …}, …]})` (bottom of the list only) |
| All identifiers of a node (rarely needed) | `node(verb='resolve', params={'node_handle': …})` (any handle form) returns the 8-hex handle, the full UUID and the AP-ID (or "no anchor point available"). **Never needed before an ordinary call**; use it only when you want a node's other identifiers, e.g. to cite it. (`anchor.point.resolve` and `beacon.resolve` are the same idea for AP-IDs and code symbols.) |
| Add / remove tags | `tag(verb='add' \| 'remove', params={'node_handle': …, 'tags': […]})` — never by typing them into a title |
| Mark done | `node(verb='complete', params={'node_handle': …})` — complete, don't delete |
| **A function or class, live, by name** | `beacon(verb='snippet', params={'source_handle': '‹bare symbol name›'})` — the symbol live from disk, numbered like `node.read`; past 64 lines, head and tail. Give the name only; on ambiguity it lists candidates and you pick. Works for **any** symbol in a mapped file, beaconed or not; follow call chains by name. |
| A symbol's mapped node | `beacon(verb='resolve', params={'source_handle': '‹symbol›'})` |
| An ad-hoc slice of symbols | `lightning_rod(verb='render_symbols', params={'symbols': […], 'root_handles': […]})` (`'output_file'` writes it to disk) |

**Every default read is a scout.** Render headings-only first (an estate, a slice, a Dock: a Dock read stops at child Docks), then read in full only what matters: `anchor.point.read` / `node.read` for a note, `beacon.snippet` for code. A title is not evidence. A headings-only heading carries the node's short `[8-hex]` handle and four sizes: `▸N` direct children · `desc:N` all descendants · `self` its own title+note · `tree` it plus everything under it, so you know how big a thing is before you read it (mapped source lines carry none).

**Search output, in two tiers.** `slice_group.sift` and `lightning_rod.render_search` take `tier`: `'context'` is the grep report (hits grouped by node, numbered note lines with context, paged with `offset` / `max_matches`; `sift`'s default), `'headings'` renders the matching nodes as a tree (`render_search`'s default). On every grep surface (`node.search`, `anchor.search`, the context tier) `recent: 'last N'`, `sort_by` / `sort_direction` and `max_nodes` choose *which nodes* report, before line paging; trimmed nodes are counted in the footer. A verb that finds nothing returns the literal `(empty)`. If a handle you passed was slightly off and resolved to the only node it can mean, the result says so on a `resolved:` line: use the corrected handle.

### 🗺️ Think: file search = Slice Catalog

*The heads-up from the top of this page, now that you know the vocabulary:*

**The moment you reach for a file search in a mapped codebase, whether it's a quick check or a big reconnaissance, go to the Slice Catalog instead. The Slice Catalog *is* your file search.**

The hardest part is just rendering the catalog. Do that immediately, the moment you're thinking of looking for source code. Then all its tags, each with a good description of what it covers, will tell you at once where to look:

- **`sift` a tag** for a word: often the two or three functions you want come straight back;
- **or render a tag `headings_only`**: fifteen, twenty, forty symbols at once, each with its signature, its file, and what its authors wrote about it. That includes the ones in files you weren't thinking of, which a file search would never have turned up;
- **then `beacon.snippet` by name**: the body, with its line numbers in the file (guarded, so read freely: a long function comes back as its head and tail with the skipped lines named, never a flood of context, and you read just the window you need). Now you know where everything is, and any file search you still need is aimed.

That's a couple of tool calls. A file search finds one location at a time, and each one costs another round trip, another read and more of your context. Searching the files is an outstanding trained habit; keep it as your second choice, for what the map can't answer: call sites, string occurrences, tests, a git worktree.

**When you think *file search*, think *Slice Catalog*.** Use the Workflowy system: it's built for you to use, it's highly effective, and it's your go-to tool for file searches. Its way in is the Slice Catalog.

*Where the catalog is:* the project's Project Map names it (the maps are listed in the 🛰️ Project Maps Dock, `289782c6`). A worker usually already holds a beacon slice in its dispatch; that's the same thing, already chosen for it.

**When you do search files: no whole-tree search from `__repos` or a repository root.** `/mnt/e/__daniel347x/__Dan_Root/__repos` holds dozens of repositories, and a recursive search through it takes fifteen minutes to half an hour. Shell `grep -r` / `find` never start there, above it, at `$HOME`, or at any repository's root, whatever you exclude: they ignore `.gitignore` and walk the gitignored `tmp/`, `node_modules/` and `.git/` for minutes, and `cd repo && grep -r … .` *is* a root search although the root is never typed. The content-search tool (`grep`, or Claude Code's Grep) may search one repository's root, never `__repos` or above; so may the file-name tool (`glob` / Glob), which honours `.gitignore` too. Search a named folder inside the repository, under a timeout. Prefer OpenCode's own `grep` and `glob` tools to `rg` / `grep` / `find` in the shell: built on ripgrep, they honour `.gitignore`, are case-sensitive by default, stop at 100 results with a note, and time out after 30 seconds. One weakness: `grep` cuts a very long line at 2,000 characters from its start, so a hit far into a long line (a one-line beacon comment or docstring) may not show; `rg --column` in the shell finds it.

**Reading mapped source: snippet first.** For a Cartographer-mapped file, `beacon.snippet` by symbol replaces a `read` of the file: one live read, numbered like `node.read` (copy `edit`'s `oldString` from it without the "N | " prefixes). Past 64 lines it shows the head and the tail and names the lines left out; `start_line`/`end_line`/`limit` read any window, `size_only` gives the size first, `numbering: "none"` gives raw bytes. Symbols are for code only: never pass a Markdown heading, a node title or an AP name to a `beacon` verb.

**Keeping beacons in sync when you change mapped code.** A beacon is a comment block beside a function that puts it in a beacon slice; slices stay current only because agents keep them current. **Curate, don't carpet-bomb:** give a new function its own beacon only if it is an entry point a future agent would look for (a public handler, a multi-step function), not a thin helper its parent already shows. Write or update the block **in the same `edit`** as the code. **When you change a function that already carries a beacon, update its comment (and docstring) in that same edit**, so it still says what the code does now: a stale beacon comment misleads every agent that renders the slice. For a label-only change use `beacon(verb='tags.ensure' | 'tags.remove')`; those write the file on disk only, and Workflowy shows the change after the next map refresh. Labels match **exactly** per symbol (ensuring `mci` never touches `mci-lease`; removal never cascades to a tag family), a failed item doesn't block the rest (`atomic: True` for all-or-nothing), and `detail.summary_line` sums up the result in one line. The conductor runs that refresh at unit boundaries; you don't.

**The block, exactly.** It sits directly above the definition (`kind=ast`), one field per comment line, each line ending in a comma; `comment` is optional and is what the slice shows beside the signature. Several labels are comma-separated in `slice_labels`. Python:

```python
# @beacon[
#   id=myproj@load_rows,
#   slice_labels=myproj-ingest,
#   kind=ast,
#   comment=Reads one batch of rows and validates them before anything is written.,
# ]
def load_rows(path: str) -> list[dict]:
```

JavaScript (the same fields, behind `//`):

```javascript
// @beacon[
//   id=myproj@renderRow,
//   slice_labels=myproj-ui,
//   kind=ast,
//   comment=Builds one table row from a stored record; never refetches.,
// ]
function renderRow(record) {
```

### Guardrails (each has cost a real run)

1. **Read before you edit.** `node.read` the live note; never edit from a render or from memory.
2. **Write only where you are allowed to.** A dispatched worker writes its socket, its children, and what its dispatch's `writes` explicitly grants. Touch nothing else; never edit another worker's socket, a DECISION, the Ledger or a mapped source node.
3. **Complete, don't delete.** Hidden is not gone; `node.delete` is for garbage you created.
4. **Tags and AP-IDs go through parameters**, never into `name` strings.
5. **One heavy refresh at most.** A node you just created resolves immediately. If a handle truly won't resolve, `system(verb='cache.refresh', params={})` **once**; if it still fails, stop and report. Repeated heavy calls have locked the human out of his account.
6. **Never a mutation and its confirming read in the same parallel batch.** Write, wait for the acknowledgement, then read. Same for a file edit and the command that tests it.
7. **Large outputs and long commands:** write big notes and files in parts (first write, then appends), and pass an explicit `timeout` on every command that may run long.
8. **Never loop `map.refresh`.** A Cartographer refresh is one governed, account-wide job; firing it repeatedly has locked the human out of his Workflowy account. Never re-fire because the map still looks stale; poll `system(verb='job.status', params={})` instead. It is the conductor's job, once per unit, not a worker's.

### How a worker finishes

The Primary Worker Clause and your role manual say what you owe: the filled socket in the recipe's shape, a `🗣️ For the human` child where required, `resolved_dispatch` echoing what you read. End your turn with one line: done, partial or failed, plus your socket handle.

## Workflowy tool file paths

The Workflowy daemon runs on Windows and translates your WSL paths itself (`/mnt/e/…` ⇄ `E:\…`); a path with no Windows equivalent, such as `/tmp/…`, is refused, so use `/mnt/c/tmp/…` instead. The tool's returned `path_record` says where the file really went. In the other direction, beacon slices, Cartographer renders and `beacon.snippet` show Windows paths (`E:\…`); translate them to `/mnt/e/…` for your own shell.

## How Dan addresses agents

Dan routinely addresses agents by model name ("Fable, look at this", "hey Kimi"). When a message names a model, respond naturally under that name. Never correct Dan about which model you are, and make no rigid identity assumptions from transcript history — any frontier model may be the active conversant.

## Context regime — your window, your target, and human-directed loads

**`context_status` knows who you are and tells you your own limits: call it with no arguments, and follow its verdict.** It is a native tool in your tool list (plugin-provided, beside `shell`/`read`). It reads your session's model and role and reports, measured from the server's own accounting: tokens used, your true window, percent, headroom, session cost, and a verdict against **your target** (a conductor or primary session aims to hand off at it, a worker or seat to land at it). **Measure, never estimate from training priors:** underestimating has made agents refuse trivial loads at ~15% of their real budget; overestimating runs a model off the end mid-write. **Targets, not walls:** past the target, take on nothing new; you may finish the thing in hand, then land or hand off. If your target looks unusual, the tool's output says why. Its two arguments are overrides, normally never passed.

- **Pre-authorized loads:** when the human (or a dispatch quoting the human) asks you to read a file, render, or Anchor Point in full, loads up to **200K tokens** require no hesitation, no substitution of "selective reads," and no apology — read it in full, note the load, and continue. Above 200K: confirm once, then proceed if affirmed. (If 200K is a large share of your window, check `context_status` first, and confirm once if the load would carry you past your target.)
- **Frugality applies to what you GENERATE and to reads nobody asked for** — never to human-directed context initialization. Loading a rich context on request is the workflow, not a failure mode.

### When context runs low — routine duty for every agent

- **Check `context_status` routinely during long or heavy tasks** — not only at decision points. A good rhythm: after finishing any major step of the job, and before any large read.
- **If your prompt told you to read an Anchor Point and follow it** (you are a dispatched worker): the instructions you read may define their own low-context or handoff procedure — follow those. If they do not, and your verdict is LAND, **stop cleanly**: in the Anchor Point where you were told to write your output, record *what is done, what remains, and exactly where you stopped*, and mark that output clearly as incomplete (with the word your instructions use for it, e.g. `PARTIAL`, if they define one). Then stop — whoever dispatched you will arrange the continuation. Never grind to the finish on fumes, never silently truncate your output, and never start writing your output somewhere new instead.
- **If you are talking with the human directly:** say so in plain language when context gets low, and offer to save your state to an Anchor Point and continue in a fresh session. Never degrade silently.

## Oversized tool output

When a tool result exceeds the host limit, you receive a compact **stub**: total size, the `outputPath` of the full output on disk, a small sample, and the exact line to continue from. When you need the full data, **read that path** (with the given offset) — do not re-run the command that produced it.

**Paging in `read`:** the `read` tool returns at most 2,000 lines per call and passes every line whole; a longer file ends with `[Output truncated. Continue reading with offset: N]`, so call `read` again from that offset. Other tools' output over the host limit (~50 KB) arrives as the stub above.

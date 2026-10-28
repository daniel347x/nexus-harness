# The Manager App `[TODO Dan: repository name]`

### An opinionated harness for agent swarms, where context is managed as a graph of nodes rather than a pile of files

This repository is a working agentic harness, in daily use. It runs **swarms of coding agents** and a **thinking partner for the human who directs them**, over **Claude Code** and **OpenCode**. Its memory is not a folder of Markdown files. It is a **node graph that the human and every agent read and write together**: the human sees it as an outline, and each agent sees exactly the set of nodes it was handed.

It is built on **Workflowy**, for reasons this page explains. The Workflowy side has a repository of its own, **NEXUS** `[TODO Dan: link to the Workflowy MCP repository]`: the MCP tooling, the code maps and the Chrome extension. This repository is the harness on top of it: the app, the session host, the scripts and the agent files.

One person designed it, and agents wrote every line of its code, using the system they were building. That is not a confession. It is the point: **this is a fully agentic supporting system, built by the agents it supports.**

> 📸 **SCREENSHOT — hero, the Manager App on desktop:** the Manager App open full-screen in Chrome on desktop, with a real program running. Left: the sidebar with the Manager conversation selected. Centre: the Manager's conversation, with at least one expanded tool call visible. Right (or a second pane): the **Programs** tree, showing one program with its Conductor and three or more workers, at least two of them with their **active spinners** and context/wall-clock numbers visible. Use the open-source portfolio program, so no private topic is on screen. Crop to the browser window; no OS taskbar, no other tabs.

---

## The problem: context is the job

Anyone who has run agents at length learns the same thing. **The model is rarely the bottleneck; what the model is given to read is.** Anthropic now calls the discipline *context engineering*: "curating and maintaining the optimal set of tokens" (Anthropic, *Effective context engineering for AI agents*, 2025-09). It names *context rot* as the reason: recall degrades as the window fills.

The industry has converged on one answer: **curated Markdown files, disclosed progressively.** That means CLAUDE.md and AGENTS.md hierarchies, rules folders, skills that load on demand, memory folders with an index file, and compaction when the window fills. That answer is right about the *what*: curate the material. The trouble is the medium. Practitioners and the vendors' own documentation describe the same pains:

- **Files grow and go stale.** "The LLM never trims, only amends." "Stale docs (they all get stale) are a constant drag." (Hacker News practitioners, October 2026.)
- **Limits cut silently.** Codex stops reading AGENTS.md files past 32 KiB by default. Claude Code's memory index is cut at 200 lines. Claude Code ships an audit command just to find outdated and contradictory instruction files.
- **Compaction loses things.** By Claude Code's own documentation, path-scoped rules and nested CLAUDE.md files are "summarized away" when a session compacts.
- **Selection is coarse.** Today an agent's context is chosen by the file path it touches, the folder it sits in, the model's guess from a description, or a similarity search. A human has no way to say *"this agent sees exactly these forty things, from six different places,"* and see that selection as one document.

The pile of Markdown files becomes a sprawl: hard to know which files to include, hard to keep current, and hard to tag.

## The approach: nodes are units of memory

This harness treats context as a **graph of nodes in one shared document**:

- **Every node is a Markdown document** (a title plus a note). Every durable node carries a permanent handle such as `[ap:K7M2QF]`, which survives renames and moves. Prompts, results and instructions cite each other by that handle, never by a path that breaks.
- **Tags cut across the tree.** Add a tag and the node joins a *slice*: a saved selection that renders, later, as one Markdown document, without anyone creating a new file.
- **Order is free.** Move a bullet and the rendered order changes. Indent, outdent and regroup take a fraction of a second.
- **Thousands of nodes is normal.** Thousands of nodes in an outline is par for the course; thousands of Markdown files is a sprawl.
- **The human sees exactly what the agents see.** The memory the agent reads is the page the human reads. Nothing is hidden in a vector store or a synthesized profile.

The picture Dan keeps coming back to:

> *"If the nodes were out in outer space, and a bunch of them lit up for one agent, and then another set of them lit up for another agent… You just see all these little dots of light, kind of, like, lighting up and vanishing. That is context management."*

A swarm is exactly that. A Conductor hands each worker a small, precise set of nodes: its instructions, its inputs, the slice of code it needs. The worker writes its result back as one more node, which the next agent receives by handle.

### Why Workflowy

A node-based, tag-based, human-visible store is what agent context management needs. When we looked for one, we found that **Workflowy already is that store**, and it comes with the visualization layer for free:

- **One graph, one document.** It is not a file system: "a file system is too powerful. It's impossible to manage." And it is not a bare database, which "becomes a database project" and then demands a visualization tool built on top of it. *"Sometimes the constraints are as powerful as the flexibility."*
- **A subtree is a folder you can read as one page.** Click a node and every document beneath it renders as one continuous document, filtered by tags if you like.
- **The view is the store.** "You can't separate the database from the way the humans view the database." Workflowy is a visualization layer over its own data layer in the cloud, maintained by someone else.

So Workflowy is our choice for the substrate, and we are proud of it. It is **the best tool we know that supports this paradigm**, and we think the paradigm is the right one for agentic harnesses. *"If Workflowy didn't exist, it would have to be invented."*

To be fair to the field: Workflowy itself now offers a Desktop MCP server, Tana has an official MCP over its nodes and supertags, and Letta keeps agent memory as git-versioned Markdown shared across agents. Letting an agent read your outliner is not the new part. **The new part is the system on top:** permanent handles, slices that select context per agent, source code mapped into the same canvas, and a harness that runs whole programs of agents through it. We have no controlled benchmark showing this beats curated files. What we have is that it works, every day, at the scale described below.

> 📸 **SCREENSHOT — the same memory, two views:** Workflowy in Chrome, with the extension's docked Markdown modal open on the right. In the outline (left), zoom into the program root of the open-source portfolio program, with a few worker sockets expanded so their `[ap:…]` handles and tags are visible. In the modal (right), the same program rendered as one Markdown document: apply its slice group, or have an agent push it. Caption idea: *"Left: what the human edits. Right: what an agent reads. Same nodes."* Keep any Manager Sessions Dock and any private Dock out of frame.

---

## Two halves, mirrored

The tooling underneath (NEXUS) is half knowledge work and half source code. This harness has the same two halves:

| | Knowledge work | Source code |
|---|---|---|
| **The tooling (NEXUS)** | Anchor Docks, Anchor Points, slice groups, rendering into the docked modal | Cartographer code maps, beacons, beacon slices, live snippets by symbol name |
| **The harness (this repository plus two Workflowy subtrees)** | **Manager Sessions:** an agent that helps you think, plan, remember and curate | **The Program Conductor Toolkit:** a Conductor agent that runs swarms of workers to write, audit and fix code |

**Manager Sessions** is where the human thinks out loud with an agent. Each conversation gets its own container in the outline. Whatever is worth keeping is captured as a node and later *curated* into topic containers, so the next session picks up the thread without the human reconstructing it. More than two hundred sessions have run on it so far.

**The Program Conductor Toolkit** is where work gets done. A **Conductor** plans a program. For each worker it writes a **socket**: a node holding a small dispatch (role, inputs, what the worker may write, the contract it is measured against) and the instructions. It launches the workers, reads their results, sends their work to independent auditors, dispatches fixes, and hands off between its own sessions when its context fills. All of it goes through the shared graph. It is built for code, and it runs knowledge-work programs just as well.

The two halves meet: the Manager seeds programs, and the Conductor reports back into the same tree. *"In order to do effective source code, you have to also be able to strategize the work."* This is for architects, planners, founders, and the non-technical people who end up having to run technical projects.

> 📸 **SCREENSHOT — a socket:** one worker socket rendered in the docked Markdown modal (or in the Manager App's View pane): its `dispatch:` YAML block at the top (role, agent, inputs, writes, contract), followed by the Conductor's prose. Pick a socket from the portfolio program, for example a README author's socket. Make sure no hostname, token or personal path is visible in the YAML. Crop to the rendered document.

### The skill is a Workflowy subtree

The Manager instructions and the Toolkit together are **a well-developed skill for agentic swarm management**. That skill is not a document on paper, and not a Markdown file in this repository. **It is a Workflowy subtree.** The Toolkit's own front page says it plainly:

> *"There is only one prompt and it never changes … `You are the CONDUCTOR. Read [ap:QE4RXD] and follow it.`"*

Every agent in a program starts the same way: *read this node and follow it.* The node it is pointed at decides everything else, and it pulls in more pages as the job needs them, by progressive disclosure. Workers are started the same way, with their own socket.

**What this means for you, honestly stated:**

- **The plan:** a skill file of about five lines, saying *"load the following publicly available Anchor Point."* You could also copy the whole subtree into your own Workflowy and change it to suit you.
- **Where it stands today:** that is a recorded design decision, not a shipped artifact. The skill file is not written yet, and the public copies of the subtrees do not exist yet. `[TODO Dan: link to the public Manager Sessions copy]` `[TODO Dan: link to the public Toolkit copy]`
- **One practical point:** a Workflowy share is a live web page. A stranger's agent can't load it by handle through Workflowy's official API, which only reads the key owner's own account. So each public subtree will also be committed here as an exported Markdown copy. Any agent can read that copy, and it is what search engines index.
- **The instructions are written for one person's setup today.** They name a particular cast of models and a particular machine. A generic edition is planned.

---

## What running a program looks like

A program is a tree of nodes, and so is its history:

1. **The Conductor writes a socket per worker.** Each worker gets precisely what its job needs: the program's one-page *SkyView* of purpose, the inputs by handle, a code slice if it touches code, and a statement of what it may change.
2. **Workers start warm.** A **seat base** is a parked session already loaded with a program's shared reading. Lead workers and their helpers are **forked** from it instead of re-reading everything. The design rule, from the notes it grew out of: *"Fork to carry the whole mind; anchor to bring the conclusion home."* The fork carries the context, and the result node, the *anchor*, is what survives the fork.
3. **Every agent knows how full it is.** A `context_status` tool reports each agent's real window occupancy against its own target. At the target, an agent lands its work cleanly, as a PARTIAL if it has to, instead of running dry. Automatic compaction is switched off on purpose: **handoffs are the reliable path; summarization is not.**
4. **Work is audited by fresh eyes.** Results go to auditors who did not write them, often from a different model family, measured against a stated bar. Fixes are dispatched the same way.
5. **The human watches it live.** On desktop or phone, the Manager App shows every program, its Conductor and its workers with status, context fill and elapsed time. One command, `oc.sh view`, pushes a program's state into Workflowy's docked modal.

**This README itself came out of such a program.** Its drafts were written by three independent agents working blind, given identical inputs at three thinking levels. Its facts came from earlier workers' inventory and research nodes, and its plan was reviewed by others. `[TODO Dan: adjust once the final README is chosen or merged]`

> 📸 **SCREENSHOT — the Programs pane, live:** the Manager App's **Programs** view during a running program: the program row expanded to its Conductor and workers; spinners on the active ones; each row's context-fill and wall-clock telemetry visible; one completed worker showing its finished state. Use the portfolio program. Full pane, readable at README width (about 1000 px wide).

> 📸 **SCREENSHOT — the phone:** the Manager App's phone page (`/m`) on an actual phone, in two shots side by side if possible: the **Programs** tab with a program running (spinners visible), and the **Chat** tab mid-conversation with the Manager. Portrait, status bar cropped or neutral.

> 🎬 **OPTIONAL VIDEO (60–90 s) — "a program, start to finish":** dictate a request into the Manager App, press **Refine** and then **Send**; the Manager answers; cut to the Programs pane as the Conductor fans out workers (spinners appear); cut to Workflowy, where the workers' result nodes land and render in the docked modal; end on the Manager summarizing in the app. No narration needed; captions only.

---

## What is in this repository

A short orientation. The full list, with status for every component, is in [`COMPONENTS.md`](COMPONENTS.md) `[TODO Dan: final path]`.

| Component | What it does | Status |
|---|---|---|
| **The Manager App** (`transcription-pane/`) | Desktop and phone web app. Dictate into a persistent editor, **Refine** the text with a frontier model that reads the conversation as context, and **Send** it to the Manager. Watch the Manager's conversation live (Claude Code or OpenCode) and inspect any tool call. See every program and worker as a live tree. Open Workflowy documents in a **View** pane, rendered as the extension's modal would render them. | Working, used daily |
| **Claude Code headless host** (`scripts/claude_headless/`) | Starts and supervises official Claude Code CLI sessions (the Manager, Conductors, lead workers) on the user's own subscription, with exact send receipts, native controls (interrupt, model, effort) and an honest halt at usage limits: "Nothing resumes by itself." | Working, used daily; young (October 2026) |
| **The `oc_*` scripts and the seat system** (`scripts/`) | The Manager's command line: the program view, steering a running agent, one-command quick programs, session search, archiving, per-worker telemetry, plus seat bases and forking. | Working, used daily |
| **OpenCode integration** (`proxy/`, `plugins/`, `config/`) | A payload proxy (prompt-cache control, rate-limit backoff of about 25 minutes before OpenCode sees an error), five OpenCode plugins (including `context_status` and oversized-output handling), a master config, and safe restart and upgrade. | Working, used daily |
| **Agent files** (`agents/`) | The global worker instructions, one file per model and thinking level, the Manager's system prompt, and the Conductor's. The Claude Code prompts are composed from the same sources at launch. | Working; specific to this setup |
| **CodeNomad extension** (`chrome-extension/`) | Tells the Manager App which OpenCode session is on screen in the CodeNomad client. | Working; used less and less |

**By the numbers** (tracked files, October 2026): about 147,000 lines; roughly 1,800 Python test functions plus 38 JavaScript test files; 659 commits since 2026-09-10, 594 of them in the last 30 days.

**How it touches Workflowy:** lightly in the app, firmly in the program machinery.

- **The app:** the View pane is an MCP client of the NEXUS daemon. Everything else in the app (dictation, chat, telemetry) runs without Workflowy.
- **The program machinery:** seats, quick programs and map refreshes call the daemon directly, and the Toolkit's instructions are written in NEXUS calls.
- **The OpenCode proxy:** has no Workflowy coupling at all.

> 📸 **SCREENSHOT — the View pane:** the Manager App's **View** pane showing a Workflowy render, for example the portfolio program's root, or a beacon slice of this repository rendered headings-only (function signatures plus their beacon comments). It shows Workflowy reaching into the harness. Crop to the View pane, with the app's tab bar visible so it reads as the Manager App.

## Can I run it?

**Not easily, yet, and we would rather say so up front.** Today this repository is a portfolio to read, not a product to install:

- It assumes **Windows with WSL**, the **NEXUS** Workflowy daemon running on Windows, the **Claude Code** CLI (with a Claude subscription) and/or an **OpenCode** service, **Chrome**, and, for the phone, a private network such as **Tailscale**.
- Many scripts still contain **paths specific to the author's machine** (in 42 non-test files at last count).
- The Manager App's own server is standard-library Python and starts with one script, but most of what it shows depends on the pieces above.

Making it installable by a stranger (configurable paths, a minimal mode without Workflowy, generic instructions) is on the list for the next phase. `[TODO Dan: link to roadmap or issue, if any]` In the meantime, the code, the tests and the documents are all here to read.

---

## Who made this

Not a company. One person: **Dan Nissenbaum** `[CONFIRM Dan: name as you want it shown]`, a former principal at Microsoft, five years there as an individual contributor. Dan has never been very active in open source, and has been incredibly active in private source. He is not a marketer either: he has been holed up, working non-stop with these agentic systems, finding the ways to make them work best, and this is where he landed.

On authorship, the precise version: **agents wrote the code; Dan drove every idea and insisted on good design.** There are no other humans in it, and it wraps no agent framework. It stands on Workflowy and its API, on OpenCode, Claude Code and CodeNomad as hosts, and, on the Workflowy side, on a small example Workflowy MCP wrapper that served as the very first starting point `[CONFIRM Dan: vladzima/workflowy-mcp]`. Everything else was designed here and built by agents, with the system itself, over months of non-stop work.

Version one is, in Dan's words, *"locked and sealed."* If you have read this far and have something to say about it, that is exactly the conversation he hopes to have. `[TODO Dan: contact link]`

## Related

- **NEXUS, the Workflowy MCP tooling:** `[TODO Dan: link]`. The eight tool families, Cartographer code maps and beacons, and the GLIMPSE Chrome extension with its docked Markdown modal. Start there if you use Workflowy and want it agent-enabled.
- **The Manager Sessions and Program Conductor Toolkit subtrees:** `[TODO Dan: public share links + exported Markdown copies]`.
- **Components and status:** [`COMPONENTS.md`](COMPONENTS.md).

## License

`[TODO Dan: license for this repository]`

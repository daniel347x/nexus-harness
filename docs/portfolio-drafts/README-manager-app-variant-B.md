# The Manager App

`[TODO Dan: repository name — the task plan proposes one; "the Manager App" is used throughout as a placeholder]`

**An opinionated harness for agent swarms on OpenCode and Claude Code, where every instruction, hand-off, result and memory is a node in one shared outline that the human reads too.**

*Built by one person, Daniel Nissenbaum, working with agents: the agents wrote the code, and every idea and design decision in it is his. [More below.](#who-built-this)*

> 📸 **SCREENSHOT — hero: the cockpit beside the outline.** Full desktop, about 1920×1080, two windows side by side (or one Chrome split view). **Left:** the Manager App desktop page (`http://127.0.0.1:9911`, Claude mode). Its sidebar shows **Running now** with one program expanded: the program row, its Conductor, and at least three worker or lead-seat rows under it, with at least one spinner turning and the telemetry line visible on the rows. The conversation column on the right of the app shows a Manager turn containing two or three blue Workflowy tool bars (`anchor`, `glimpse`, `node`). **Right:** Workflowy in Chrome, zoomed into the same program's root node and expanded one level, so the worker sockets show as children (titles beginning `⚖️ DISPATCHED ·` and `⚖️ RESULT ·`). Before capturing, make sure no visible sidebar name, conversation text or Workflowy title mentions paid work. The conversation should be about the program itself (this portfolio program [ap:JF1AS0], or MCI). Do not show the Manager Sessions Dock's own first screen.

---

## The problem it is built around

Agents are now good enough that the hard part has moved. The question is no longer *can the model do this task*. It is ***does the model have the right context when it starts: not too much, not too little?*** Memory, prompt engineering, hand-offs between agents and the material each one is given: context management is the defining problem of agentic work, and it is the force underneath every agent harness.

The industry agrees on the problem. Anthropic calls it *context engineering*: ["the set of strategies for curating and maintaining the optimal set of tokens"](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents). It also names the failure mode, *context rot*: recall degrades as the window fills. And the industry has converged on one answer: **curated Markdown files** (`CLAUDE.md`, `AGENTS.md`, rules folders, memory banks) loaded in stages, plus compaction when a session runs long.

That answer works, up to a point. Then the files sprawl:

- **They grow and never shrink.** As one practitioner [put it](https://news.ycombinator.com/item?id=49945933): *"the LLM never trims, only amends."*
- **They go stale and contradict each other.** Claude Code now ships an audit command for outdated and conflicting instruction files. Codex silently stops reading instructions past 32 KiB, and GitHub Copilot expires its agent memories after 28 days.
- **Selection is coarse.** Today an agent's context is chosen by the file path it touches, the folder it starts in, the model's own guess from a description, or a similarity search. No mainstream tool lets a person hand an agent *exactly this set* of memory and see that set rendered as one document.
- **Compaction loses things.** When a session summarises itself, the vendors' own documentation lists what gets summarised away.
- **More context isn't better context.** An [ETH Zurich study](https://arxiv.org/abs/2602.11988) found that context files *"do not generally improve task success rates"* while raising inference cost by over 20% on average. Curation and selection matter more than volume.

The real solution isn't better summarisation. **It's curation: deciding, deliberately, what each agent sees.** Curation at the scale of a swarm needs a substrate built for it.

## The idea: memory as nodes, context as a selection

Treat every unit of memory as a **node**: a plan, a decision, a worker's instructions, a finished report, a gotcha someone already hit. A node is lightweight, it has a permanent handle (`[ap:K7M2QF]`) that survives renames and moves, and it can hold a full Markdown document. Then an agent's context is not a pile of files. It is **a selection of nodes**, chosen by tags that cut across the whole tree, rendered as one Markdown document that the human sees too.

> If the nodes were out in outer space, a bunch of them would light up for one agent, and then another set would light up for another agent. You'd see all these little dots of light lighting up and vanishing. **That is context management.**

This works only on a substrate with particular properties:

- one document of nodes, not a file system;
- tags that select across it without creating a new file;
- any subtree rendered as one Markdown document;
- order you set by moving a bullet;
- structure that costs nothing to change;
- a human view of all of it, always.

That is a description of **Workflowy**.

### Why Workflowy

The choice follows from those requirements, not from habit: a node-based, tag-based outline is the best structure we know for agentic context management, and Workflowy is the best tool we know that supports it:

- **A graph of nodes in one document.** It is hierarchical like a file system, but you see the content while you see where it lives, and you can indent, outdent or reorder in a fraction of a second.
- **Tags filter that one document dynamically.** No new document is created, and a tag can pull nodes together from anywhere in the tree.
- **Order is whatever you set by moving a bullet.** A file system can only sort by name or date.
- **Scale without sprawl.** Thousands of nodes in Workflowy is par for the course; thousands of Markdown files is a sprawl.
- **The visualization layer comes free.** You can't separate the database from the way the humans view it. A memory store with no view needs one built on top. Workflowy is already both.

> **If Workflowy didn't exist, it would have to be invented.**

The Workflowy side lives in its own repository: **NEXUS**, the MCP server that turns a Workflowy account into structured, shared agent memory, together with its Chrome extension and Cartographer, which maps source code into the same outline. → `[TODO Dan: link to the NEXUS / Workflowy MCP repository README]`. **This README is about what runs on top of it.**

## Two halves, one system

The NEXUS tooling is *half knowledge work and half source code*. The harness built on it mirrors that split exactly:

| | **Knowledge work** | **Source code** |
|---|---|---|
| **The tools (NEXUS)** | Anchor Docks and Anchor Points, slice groups, rendered views | Cartographer maps, beacon slices, live code snippets |
| **The harness (this repository's skill)** | **Manager Sessions**: an agent that helps you think, plan, remember and curate | **The Program Conductor Toolkit**: agent swarms that implement, audit and remediate |
| **The cockpit** | The Manager App: dictate, refine and send to the Manager; read any Workflowy view | The Manager App: every program, Conductor and worker, live |

To run code well you also have to be able to strategize the work, so the two halves are built as one. It is for architects, planners and founders, and for non-technical people who find themselves running technical projects. **And all of it is Workflowy-centric.**

### Manager Sessions: the thinking half

*"This is where the human thinks out loud with an agent."* The **Manager** is a long-running thinking partner:

- **Every conversation gets its own Anchor Dock.** Anything durable is captured as an Anchor Point, then promoted into topic Docks in a later curation pass.
- **A Residue page and a Board carry what is active**, so the next session picks up the thread from curated records rather than from a summary of a chat.
- **The Manager also operates the swarms.** It seeds programs, watches them and steers them.

There have been 211 Manager sessions so far. As the instructions themselves say: *"There is absolutely nothing trivial about keeping organized when there's hundreds of sessions and ten different topics."*

### The Program Conductor Toolkit: the building half

Doctrine for running **agentic swarms**, mostly to write code, though it runs knowledge-work programs too (the README you are reading came out of one). A **Conductor** agent plans a program, writes the instructions for each worker into Workflowy, launches the workers on whichever model the job calls for, reads their results, runs audits and remediation, and hands off between its own sessions when its context fills.

## How a program runs

1. **The human talks it through with the Manager.** The Manager writes the program's root: a *SkyView* (what the human is actually trying to get, how they will use it, at what scale and stakes) and the contract the work is measured against.
2. **One prompt starts the Conductor, and it never changes:**
   ```
   You are the CONDUCTOR. Read [ap:QE4RXD] and follow it.
   ```
   Everything else the Conductor knows, it reads from Workflowy.
3. **Every worker gets a socket.** A socket is an Anchor Point the Conductor writes for one worker's run. It opens with a *dispatch*: the role, the model, what to read and in what order, what the worker may write, what it is forbidden to read, and what it will be measured against. The worker's own prompt is one line: read the worker clause, then read your socket and follow it.
4. **Workers start warm.** A **seat base** is a parked session that has already read the program's shared material. Lead seats and their helpers are *forked* from it, so each one starts with the whole picture instead of a summary of it. From the design notes: *"Carry the whole mind."*
5. **Results come home as nodes.** Each worker writes its report into its own socket, plus a separate `🗣️ For the human` account tagged so that one render shows the human every account in the program. In the design notes' words: *"The anchor is what survives the fork."*
6. **Audit, then remediate.** Other agents, often on other models, audit the work against an explicit bar. Findings go back as remediation, and a Conductor DECISION records what was accepted.
7. **Agents watch their own context.** Every agent can call `context_status` to measure how full its window is against its own target. At the target it lands cleanly, with what's done, what's left and exactly where it stopped, and the Conductor arranges the continuation. Auto-compaction is off by rule: *handoffs are the reliable path; summarisation is not.*
8. **Code stays mapped.** Workers find code through beacon slices and read it by name with live snippets. Once per unit of work, the Conductor runs one governed refresh of the code map.

Here is a real dispatch (trimmed, paths shortened): the socket that commissioned this README draft.

```yaml
dispatch:
  role: PROSE_WORKER
  agent: opus55                      # Claude Opus 5.5; this fork thinks at "xhigh"
  role_manual: "[ap:N04R7I]"
  mode: CREATE
  contract: ["this socket's prose", "[ap:5UJ2W5]", "[ap:JGZ0HL]"]
  inputs: ["[ap:J8XFUQ]", "[ap:5UJ2W5]", "[ap:JGZ0HL]"]    # the SkyView first, always
  worker_clauses:
    - {handle: "[ap:TQPZKC]", load: before}                  # UNMETERED SEAT
  reads_forbidden: ["the other five README drafts and their sockets"]
  writes:
    - {where: "this socket", how: fill}                      # report + 🗣️ For the human
    - {where: "docs/portfolio-drafts/README-manager-app-variant-B.md", how: edit}
```

Every `[ap:…]` in it is a node. In the Manager App, clicking one opens it.

> 📸 **SCREENSHOT — a socket, as the agent and the human both see it.** Workflowy in Chrome with the NEXUS extension's docked Markdown panel open on the right, rendering the pre-pass socket **[ap:PV5ZM9]** (*"Pre-pass · README authors (both trios …)"*). It is a good choice because its dispatch contains no machine paths. The panel must show the whole `dispatch:` YAML block (`worker_clauses`, `attachments` with their notes, `reads_forbidden`, `writes`) and the first lines of its `## Result` below. The left side shows the program root [ap:JF1AS0] expanded one level, with the socket highlighted. Crop to the browser window.

> 📸 **SCREENSHOT — "For the human": every account in a program, in one view.** The same docked panel, rendering every node tagged `#prog-human` under one program root (the Manager's `oc.sh view` boom in its default mode pushes exactly this; or `lightning_rod render_tags ['prog-human']` at the root with `push_to_modal`). It shows a chronological run of `🗣️ For the human · …` accounts, each under its socket's title as context. **Use MCI or another program whose accounts mention no paid work, not this portfolio program**: its audit accounts discuss the private material they were checking for. Crop to the panel plus a sliver of the outline.

### The principles behind it

- **Point at context; never re-narrate it.** A worker is handed handles, not paragraphs, and a handle is never a lossy copy of the thing it points at.
- **Forking is solved; merging back isn't, so the Anchor Point is the merge-back.** Fork to carry the whole mind; anchor to bring the conclusion home.
- **Calibrate trust to information, not to role.** The deep-context agent that wrote a plan is great at completing it, not at certifying it. A fresh agent audits.
- **Write only where you're allowed.** Read access is not write authority: every dispatch lists exactly what a worker may change.
- **A powerful ship cannot be operated blind.** Every role writes its own plain-language account for the human, decision first. In the Toolkit's words: *"The human-readable layer is half the job. Not less; not more."*

## The Manager App: the cockpit

The web app sits beside the agents on the desktop and travels on the phone. It began life as a dictation pane, and its folder is still called `transcription-pane/`.

**Talk to the Manager.**
- Dictate into a persistent editor; any dictation tool that types into the focused box works.
- ✨ **Refine** cleans the dictation with a frontier model that reads the open conversation as context.
- 📤 **Send** delivers it to the Manager's Claude Code or OpenCode session.

**Read every agent.**
- The Manager's conversation streams live, and any tool call opens on its exact stored input and output, with a diff for every source edit.
- Any other session, whether a Conductor, a worker or a helper, opens read-only.

**See every program.**
- Each program is one live tree: program → Conductors → the seats each one started → their helpers.
- Live status on every row, and a green dot on any program with an agent at work.
- Context and wall-clock telemetry on every row (per phase, for implementers).
- Each program row shows its size, duration and age at a glance, and quota gauges show how much of each subscription window is used and when it resets.

**Read Workflowy in place.**
- 📄 **View** renders any Anchor Dock, slice group, node or saved render exactly as the NEXUS extension's Markdown panel does, using the extension's own rendering code.
- Every `[ap:…]` handle or short node ID anywhere in the app (in a conversation, a tool call, the sidebar) is a link into View.

**From the phone.** `/m`, over a private network (Tailscale), offers Chat · Programs · Context · View, with a one-line strip of Manager chips and an outdoor glare mode for reading in the sun.

**Escalations.** When an agent escalates to the human, the incident stays on screen until it is resolved with a typed disposition.

> 📸 **SCREENSHOT — the program tree, close up.** Desktop sidebar only, about 600 px wide and full height, with one large program expanded (MCI is ideal). It must show:
> - the program row, with its blue count blocks, yellow duration bar and age;
> - two Conductor rows (`-001`, `-002 · resumed`);
> - lead seats with helpers nested under them;
> - at least two spinners active;
> - the per-row telemetry lines (context first → last, wall clock).
>
> Use the **Show** filter so that only this program is listed.

> 📸 **SCREENSHOT — an agent writing to Workflowy, inspected.** The desktop conversation column with one Workflowy tool call opened in the inspector: ideally `anchor` · `point.capture` (a worker capturing its report) or `glimpse` · `estate`, in the **Readable** view, showing the call's parameters and the returned acknowledgement or Markdown. Keep the status line (`✅ ‹date time› · took ‹n›s`) in frame.

> 📸 **SCREENSHOT — View: the outline inside the cockpit.** Desktop, Claude mode, with the conversation column switched to **📄 View** (the `💬 Conversation | 📄 View` switch). Use **Choose → Favorite Docks** to render the Program Conductor Toolkit Dock. The **Rows** list must be visible above the rendered document, with the document's first headings below it. The sidebar stays visible on the left, so the reader sees that it is one app.

> 📸 **SCREENSHOT — the phone, three panels side by side.** iPhone, `/m`, normal (not glare) mode, three portrait screenshots placed in one row:
> 1. **Chat**: the Manager-chip strip at the top, a Manager reply rendered as Markdown and one blue tool bar;
> 2. **Programs**: a program tree expanded with at least one spinner;
> 3. **View**: a rendered Anchor Point (e.g. one worker's `🗣️ For the human` account).
>
> Make sure no chip name or message mentions paid work.

> 🎬 **OPTIONAL VIDEO (60–90 s).** One take, desktop:
> 1. Dictate a short request to the Manager to start a small quick program, then ✨ Refine and 📤 Send.
> 2. The program row appears in the sidebar and its tree fills in as workers launch.
> 3. A worker's report lands in Workflowy (show the outline on the right).
> 4. Click the report's handle in the conversation, and it opens in 📄 View.
>
> No narration needed; captions at each step.

### Under the hood

| Piece | What it does |
|---|---|
| **Claude Code host** | Owns the *official* Claude Code CLI: not a proxy, not a terminal driver. It starts and supervises the Manager, Conductors and lead seats on the user's own subscription, with exact send receipts, native controls (interrupt, model, effort) confirmed by readback, and an honest halt at a usage limit (*"Nothing resumes by itself"*). |
| **OpenCode layer** | A payload proxy in front of every model provider: it keeps prompt caching under control and absorbs rate limits for about 25 minutes before OpenCode ever sees an error. Five OpenCode plugins: `context_status`, a tool-output guard, an MCP timeout fix, a cache-affinity header and a usage tap. A master config with auto-compaction off by rule, a restart that tests every candidate config on a throwaway server first, and upgrades checked against the new OpenCode source, with rollback. |
| **The Manager CLI** (`oc_*` scripts) | *The boom* (`oc_view`): every running program, Conductor and worker at a glance, and a program's state pushed into Workflowy's Markdown panel. Steering a running agent, one-command quick programs, search across every session of both runtimes, per-phase telemetry, archiving finished waves. |
| **Seats** (`oc_seat` on `oc_pack`) | Builds seat bases (sessions pre-loaded with a program's shared reading) and forks lead seats and helpers from them. |
| **Agent files** | The *cast*: one file per model and thinking level, across several model families (Claude, GPT, DeepSeek and others). Also the global worker prompt and the Manager's prompt. Claude Code prompts are composed from the same sources at launch, with a test that refuses to build them if the two have drifted. |

## The skill is a Workflowy subtree

The Manager instructions and the Program Conductor Toolkit together are **a well-developed skill for agentic swarm management**. That skill is not a document on disk. It is a Workflowy subtree:

- **The Manager's instructions** are one Anchor Dock: a 182 KiB root description (the Manager's skill), plus about 559 KiB of permanent pages it reaches by handle.
- **The Toolkit** is another Dock of 145 nodes, about 1.7 MiB: the Conductor's initialization, the dispatch schema, the report recipes, a shelf of worker clauses, and the doctrine on audit bars and human-readable reporting.

Nobody reads all of it at once. An agent reads the root, then follows handles by **progressive disclosure**, only when the work calls for them. The loading mechanism is already universal here: every prompt in the system is some form of *"Read [ap:X] and follow it."*

**Where this is going (designed, not yet shipped).**
- **The design:** the public skill will be a tiny file, a handful of lines that say *load the following publicly available Anchor Point*, plus the subtree itself, which anyone can copy into their own Workflowy and customize. The public copies are not published yet.
- **One practical wrinkle:** a Workflowy share is a live, view-only web page, and another person's agent cannot read it through Workflowy's API.
- **So the release shape is two things:** a curated public copy of each subtree, shared view-only (to browse as it lives in Workflowy, and to add to your own account in one click), and a Markdown export of each, committed to this repository (for any agent, and for search engines).
   - Manager instructions: `[TODO Dan: public share link]` · `[TODO Dan: committed export path]`
   - Program Conductor Toolkit: `[TODO Dan: public share link]` · `[TODO Dan: committed export path]`

> 📸 **SCREENSHOT — the skill, as a subtree.** Workflowy zoomed into the **Program Conductor Toolkit** Dock (`f382b87e`) with its direct children visible (INIT, DISPATCH SCHEMA, PROGRAM INTERFACE, KEY & GLOSSARY, QUICK PROGRAM, the BLOCKS and WORKER CLAUSES shelves …). On the right, the docked Markdown panel renders the same Dock **headings-only**, so the size metrics (`▸N · desc · self · tree`) show how much lives under each page. Do **not** use the Manager Sessions Dock for this shot.

## In daily use

The numbers come from the repositories and the outline as of October 2026:

- **This repository:** about 147,000 tracked lines; roughly 1,800 Python test functions plus 38 JavaScript test files, including headless-Chrome tests of the web app's real page; 659 commits since 2026-09-10, 594 of them in the last 30 days. `[TODO Dan: keep the commit counts only if the public repository keeps its history]`
- **The Manager:** 211 sessions. The session stream is 7 MiB of live outline, and 137 MiB counting the finished subtrees exported to archive files, which agents can still read and search.
- **Programs:** one long-running program, MCI, has run 338 agent sessions so far. `[TODO Dan: one plain line on what MCI is]`
- **NEXUS, the Workflowy side:** about 168,000 lines of first-party code, 5,402 test functions and 1,292 commits since January 2026.
- **This README was drafted by a program run on this harness.**
   1. Fact-finding agents inventoried both repositories and researched the landscape.
   2. One agent read every brief and fact once.
   3. The Conductor forked that agent six times: three blind authors per README, each at a different thinking level.
   `[TODO Dan: keep this paragraph only if it stays true of the published version]`

## Who built this

I'm **Daniel Nissenbaum**. This isn't a company or a team: it's one person's system.

I was a principal at Microsoft for five years, as an individual contributor. I've never been that active in open source, but I've been incredibly active in private source. I'm not a good marketer either. I've just been holed up, working non-stop with these agentic systems and finding ways to make them work the best, and this is what I've landed on. I'd like to share it now.

**How it was built is the point.** Agents wrote the code: this repository, the NEXUS server, the Chrome extension, all of it. I drove every idea, insisted on good design and decided what got built. In terms of the humans involved, it is a hundred percent me, even though I did probably zero percent of the actual programming. That's not a disclaimer. **This is a fully agentic system, built by agents running on the harness it describes.**

Nothing here wraps an agent framework. OpenCode, Claude Code, CodeNomad and Workflowy are other people's products; the harness around them is mine. The one piece of code I started from is a small example Workflowy MCP wrapper `[CONFIRM Dan: vladzima/workflowy-mcp]`, about one percent of what the NEXUS server is now, and none of it is in this repository. This app also grew out of a dictation widget I first built for TypingMind `[TODO Dan: link the public typingmind_extension repository, or drop this sentence]`.

If you are looking for someone who has actually built an agent harness and lives inside it every day, rather than someone who lists it on a résumé: this is what that looks like.

## How it compares, honestly

- **Agents reading an outliner is not new.** Workflowy now ships its own desktop MCP, and Tana has an official (alpha) one over its nodes and supertags. What is here is the system on top: permanent handles, slice groups, sockets, programs, audits and the cockpit.
- **Others treat memory as shared Markdown.** Letta's context repositories keep agent memory as git-versioned Markdown shared across agents. That is close in spirit, and it has git history where Workflowy has its own.
- **Knowledge-graph memory servers** (the official MCP memory server, Graphiti and others) model memory as nodes and relations. What they leave out is a human-first view and selection curated by a person.
- **Progressive disclosure, subagent isolation and index-plus-topics** are industry-standard patterns now. This is a substrate for them, not their invention.
- **No benchmark claims.** Nobody, the author included, has published numbers showing that tag-selected context beats curated files. The claim here is narrower: this is how one person runs agentic work every day, and it holds up.
- **The trade-offs are real:** the memory lives in a cloud, proprietary outliner rather than in local files and git; Workflowy's API has rate limits; and everything depends on the NEXUS server.

## Status: a portfolio to read, not yet a product to install

Everything above is **working and in daily use**: the Manager App, the Claude Code host, the `oc_*` scripts and seats, the OpenCode layer, and both Workflowy subtrees. The Claude Code host is the youngest piece (October 2026). The CodeNomad Chrome extension, which follows the OpenCode client, is used less and less as the Manager moves to Claude Code. The public skill subtree and the skill file are **planned**.

**Running it yourself today: honestly, not yet.**
- It assumes Windows with WSL, OpenCode running as a WSL service and/or the official Claude Code CLI with a subscription login, the NEXUS server with a Workflowy account, and Chrome (Tailscale for the phone page).
- About 42 non-test files still carry paths from the author's machine.

Next on the list: easier installation and short videos, and probably generic versions of the instructions, which today are written for one person's setup. Until then, it is meant to be read: the code, the tests, and the instructions in Workflowy.

## What's in this repository

| Folder | What it holds |
|---|---|
| `transcription-pane/` | The Manager App web app (Python server on loopback, desktop and phone pages, tests) |
| `scripts/claude_headless/` | The Claude Code host |
| `scripts/` | The Manager CLI (`oc.sh ‹name›`), the seat system, telemetry, restart and upgrade scripts |
| `proxy/` · `plugins/` · `config/` | The OpenCode layer: payload proxy, plugins, master config |
| `agents/` | Agent files: the cast, the global worker prompt, the Manager prompt |
| `chrome-extension/` | The CodeNomad session-watcher extension |
| `docs/` | Running the Claude Code host and the Manager, server lifecycle |

Each component's status, what it needs and how it couples to Workflowy: **[COMPONENTS.md](COMPONENTS.md)** `[TODO Dan: link once the components file lands]`.

## The other half

The tools this harness stands on (the MCP server, its eight tool families, Cartographer and beacons, the Chrome extension and its Markdown panel) are documented in the **NEXUS** repository: `[TODO Dan: link]`. That README tells the same story from the Workflowy side: why an outline is the right substrate for agents, and what it looks like to make one agent-enabled.

*License: `[TODO Dan: license for this repository]`*

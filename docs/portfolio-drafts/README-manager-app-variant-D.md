# The Manager App

> `[TODO Dan: repository name]` — the public name of this repository is still being chosen. This README calls it **the Manager App**.

**An opinionated harness for agent swarms, built around one shared memory that a human can read.**

A human **Manager** thinks out loud with an agent. **Conductors** run swarms of worker agents on [OpenCode](https://opencode.ai) and [Claude Code](https://code.claude.com/docs). A desktop-and-phone **cockpit** drives all of it by voice. And everything that matters, every plan, every dispatch, every result, every decision, lives in one place the human can read, search and rearrange by hand: a [Workflowy](https://workflowy.com) outline, reached through the Workflowy MCP tools in the sister repository, **NEXUS** (`[TODO Dan: link to the Workflowy MCP repository README]`).

Agents wrote the code. One person drove every idea, insisted on the design, and used the system to build the system. That is not a disclaimer. It is the credential: **this is a fully agentic supporting system**, and this repository is what one looks like after months of daily use. The first draft of this very README was written by a worker in a program run by this harness, from a socket a Conductor wrote, on facts other workers had gathered.

> 📸 **SCREENSHOT — hero, the cockpit at work:** the Manager App on a desktop browser, full window, 16:9. Left: the programs pane with one live program expanded as a tree (a Conductor row and several worker rows, at least two spinners active, each row showing its context percentage and wall clock). Center: a Manager conversation in Claude mode with a recent reply visible and one tool call expanded in the inspector (a Workflowy call: family, verb and params readable). Right: the View pane showing a Workflowy render (a program Dock rendered as one Markdown document). No private topics on screen; use the MCI program or this portfolio program.

---

## The problem is context

Every serious attempt to work with agents hits the same wall, and it is not what the model can do. It is what the model can be told. Context management, prompt engineering, memory, agents communicating the relevant material to one another, not too much and not too little: that is the foundational challenge of using agents effectively, and it is the force behind every agentic harness.

The industry now says so in nearly these words. Anthropic calls the discipline *context engineering*, "curating and maintaining the optimal set of tokens", and names *context rot*, the way recall degrades as a window fills, as the reason the discipline exists. And everyone has converged on the same answer: curated Markdown files (`CLAUDE.md`, `AGENTS.md`, rules folders, memory folders), loaded in stages, with compaction when the window fills anyway.

It works, until it is the problem. The files grow and nobody trims them ("the LLM never trims, only amends", as one practitioner put it). They go stale and contradict each other; Claude Code now ships an audit command for outdated and conflicting instruction files. Compaction summarizes path-scoped rules away. Codex stops reading instructions past 32 KiB. GitHub Copilot expires its memories after 28 days. A 2026 study from ETH Zurich found that context files often do not raise task success at all, while costing over 20% more tokens.

The deepest limit is selection. Which memory does *this* agent get, for *this* job? Every mainstream mechanism answers with one of five things: always-on, by file path, by folder position, by the model's reading of a description, or by similarity search. As far as we have found, none lets a human hand an agent an exact, cross-cutting set of memory units and see that set rendered as one document. Auto-summarization is not the real solution. Curating what each agent sees is. And curation needs a unit smaller than a file, an address that survives moves and renames, tags that cut across the hierarchy, an order you set by hand, and a view the human reads too.

## Nodes, not files

Our approach uses a node structure. The unit of memory is a **node**: a bullet with a title and a note, and the note can be a full Markdown document. Nodes nest, so a subtree is a folder with subfolders. There the resemblance to a file system ends, in the ways that matter:

- **A subtree renders as one document.** Click what corresponds to a folder and read every "file" inside it as one continuous page. No Markdown tool we know of does this.
- **Tags cut across the tree.** A tag selects nodes from anywhere in the outline, and the selection renders as one document too, dynamically, without creating a new document. That selection *is* an agent's context.
- **Order is yours.** Move a bullet and the render order changes. A file system sorts by name or by date; you cannot just set an arbitrary order.
- **Restructuring is free.** Indent, outdent, drag: a fraction of a second, and you see the "files" and where they are stored at the same time.
- **Addresses survive.** A node can carry a permanent handle, `[ap:K7M2QF]`, that survives renames, moves, sorting and completion. A prompt, a result or a page cites memory by handle, and the citation never rots.
- **Thousands of nodes is par for the course.** It is a sign of a good system. Thousands of Markdown files is a sprawl.

Picture the nodes as points in space. For one agent, a set of them lights up: its instructions, the decisions that bear on its job, the code it may touch. For another agent, a different set. They light up and vanish. **That is context management.** You cannot do it with files.

We chose **Workflowy** as the substrate because it is the best tool we know that supports this paradigm. It is one document, so everything is of a piece. It is a graph of nodes, which works well for knowledge work and for source code alike when agents and people work together. And it is already the visualization layer for its own data, which comes for free: you cannot separate the database from the way humans view it, and a store with no human view just becomes a database project that demands a viewer built on top. A file system is too powerful to manage. **Sometimes the constraints are as powerful as the flexibility.** If Workflowy did not exist, something very like it would have had to be invented to build this.

> 📸 **SCREENSHOT — dots of light:** a Workflowy tab, two panels. Left: the outline zoomed into a program Dock (use the MCI program), several bullets carrying a visible tag such as `#prog-human`, others untagged. Right: the docked Markdown panel showing the slice group for that tag rendered as one document, so the reader sees the same tagged bullets become one continuous page. Crop to the browser content area; make sure both the tags on the left and the rendered headings on the right are legible.

**What this claim is, and is not.** It is not that an agent can read and write an outliner. Workflowy ships its own Desktop MCP, Tana has an official one over its nodes and supertags, Letta keeps agent memory in shared git-backed Markdown, and Obsidian can query notes into views. The claim is about what sits on top: permanent citable handles; containers (**Anchor Docks**) and captured notes (**Anchor Points**); saved tag selections (**slice groups**) whose render is an agent's context; a codebase mapped into the same outline; and a harness that runs on all of it. Nobody, us included, has published a controlled measurement that node-and-tag selection beats curated files. What follows is a working system in daily use, not a benchmark.

The tools that do this, eight MCP verb families over Workflowy, live in the sister repository, **NEXUS** (`[TODO Dan: link]`). This README is about the harness built on them.

## One system, two halves, twice

The tooling is fifty percent knowledge work and fifty percent source code, both deeply integrated with Workflowy. On the knowledge side: Anchor Docks, Anchor Points, slice groups, and **GLIMPSE**, the render that lets an agent read exactly what the human has expanded on screen, and render back into a Markdown panel docked beside the outline, where any subtree, Dock or slice reads as one document. On the code side: **Cartographer**, which maps a repository into the outline, one node per file, class and function; **beacons**, small comment blocks that agents keep above the functions that matter, whose labels become tags; and **beacon slices**, the tagged subset rendered as signatures plus the comments their authors left, with any function readable live from disk by name.

The harness in this repository mirrors those two halves exactly.

| | Knowledge work | Source code |
|---|---|---|
| **The tooling** (NEXUS) | Docks, Points, slice groups, GLIMPSE | Cartographer, beacons, beacon slices, live snippets |
| **The harness** (this repository) | **Manager Sessions**: an agent that helps you think, plan, remember and curate | **The Program Conductor Toolkit**: Conductors running swarms of workers that write the code |

Because to do effective source code you also have to be able to strategize the work. This is for architects, planners, founders, and non-technical people who then have to do technical projects. It is all of a piece, and all of it is Workflowy-centric.

> 📸 **SCREENSHOT — the code half, a beacon slice:** the Workflowy Markdown panel showing a headings-only beacon slice render from NEXUS or from this repository: a dozen or more function signatures, each marked 🔱, each followed by its beacon comment, grouped under their file paths. Crop to the panel. Pick a slice with readable, well-written comments (the seat or telemetry code is a good candidate).

## What is in the box

Three things, and the machinery under them.

### Manager Sessions: the agent you think with

The **Manager** is an agent with a standing role: the human's thinking partner and the operator of programs. Its instructions are a Workflowy subtree, read by progressive disclosure at the start of every session. Its own opening line: *"This is where the human thinks out loud with an agent."*

Each conversation gets its own Anchor Dock. What is durable gets captured as Anchor Points, in rich Markdown, and later **promoted** into topic Docks, each promoted node carrying a trail line back to where it came from. A Residue page and a Board carry what is active from one session to the next. Curation, deciding what comes along, is a deliberate pass, not an accident of summarization. So a conversation becomes a record and the next session picks up the thread: a living record, not a lost chat log. In the author's words, written into the instructions themselves: *"There is absolutely nothing trivial about keeping organized when there's hundreds of sessions and ten different topics."*

The Manager also runs programs from beside them: seeds a program's Brief, launches its Conductor, watches it in the boom (below), steers a running agent with one command, and reads every result as it lands.

### The Program Conductor Toolkit: the swarm

The Toolkit is a Workflowy subtree of doctrine for running agentic swarms. From its root: *"Reusable, program-independent initialization for a CONDUCTOR (the meta agent) that runs implement → audit → remediate programs through Workflowy Anchor Points, with worker agents dispatched by socket."*

**There is only one prompt, and it never changes:**

```
You are the CONDUCTOR. Read [ap:QE4RXD] and follow it.
```

The Conductor reads its initialization page, then the program's Brief, and from there everything is Anchor Points. For each worker it writes a **socket**: an Anchor Point whose note opens with a dispatch block, then the Conductor's prose for that one run. The dispatch names the role and the model, the inputs to read in order, the estates to render, what the worker is forbidden to read, exactly what it may write, and the contract it is measured against. Then the Conductor sends the worker one line:

```
Read [ap:6EKDRI] (the Primary Worker Clause), then read [ap:X] and follow it exactly.
```

The worker fills the socket in place: renames it to its verdict, writes its report beneath the dispatch, and adds a `🗣️ For the human` child in plain language. A worker dispatch, abridged and illustrative:

```yaml
dispatch:
  role: IMPLEMENTER
  agent: opus55-xhigh
  mode: CREATE
  target: {ap: "src/launch/retry.py", version: "@a1b2c3d"}
  contract: "[ap:SPEC01]"                     # the spec this work is measured against
  inputs: ["[ap:SKYVW1]", {ap: "[ap:RECON1]", how: subtree}]
  estates:
    - {"tags": ["launch-retry", "launch-core"], "root": "3f3754e2", view: headings_only}
  reads_forbidden: ["[ap:AUDIT1]"]            # blinded: this worker must not see the audit
  writes:
    - {where: "this socket", how: fill}
    - {where: "src/launch/", how: edit}
  working_directory: "/path/to/the-repository"
```

Workers find code through **beacon slices**, not file searches: the estate above renders every function labelled `launch-retry` or `launch-core` as signatures plus the comments their authors left, across every file at once, and `beacon.snippet` then reads any one of them live from disk by name. Audits are run by fresh agents, usually a different model, against a stated **bar** that decides what blocks. Remediation chains fix what an audit found. Between units the Conductor runs one governed refresh of the code map. And when a Conductor's own context runs low, it hands off to a fresh Conductor through Workflowy. The program never depends on one window.

> 📸 **SCREENSHOT — a socket in Workflowy:** the Workflowy outline zoomed into one worker socket from the MCI program, note expanded, showing the `dispatch:` YAML block (role, inputs, estates, reads_forbidden, writes all visible) with the Conductor's prose beginning below it, and beneath the socket its `🗣️ For the human` child bullet. Crop to the content area; the whole dispatch block should be readable.

**Where the Toolkit came from.** Its mechanics began as design notes in mid-2026 and were tested heavily before being adopted. Three of them explain the shape of everything here:

- *Carry the whole mind.* A fork clones a session's full context at the fork point, and forking is the right primitive because design and implementation are coupled. Today that is **seat bases**: parked sessions pre-loaded with a program's shared reading, then forked, so lead seats and helpers start warm.
- *The anchor is what survives the fork.* Forking is solved; merging back is not. So the merge-back is a node in Workflowy: today the socket, the result, the decision, each an Anchor Point.
- *Calibrate trust to information, not to role.* The agent with deep context is great for completing a plan, not for certifying it. So a fresh session audits what a deep one wrote, and the implementer gets a sufficiently exhaustive slice, a pre-audited plan, and an explicit stop-don't-decide rule.

### The Manager App: the cockpit

A web app, desktop and phone, beside the agents. You **dictate** into a persistent editor; a frontier model **refines** the dictation, reading the conversation as context; **Send** delivers it to the Manager. You read the Manager's conversation live, whether it runs on Claude Code or on OpenCode, and open any tool call to inspect it. Every program, Conductor and worker appears as a tree with live status, and every row shows its context usage and wall clock per phase. A **View** pane opens Workflowy documents, a Dock, a slice group, a render, exactly as the Workflowy extension's Markdown panel would show them. The phone page (Chat · Programs · Context · View) works over a private network (Tailscale), so a program can be watched, and steered, from anywhere.

It began as a transcription pane, and the folder still says so.

> 📸 **SCREENSHOT — the phone, two frames side by side:** (1) the phone page's Programs tab with a running program's tree expanded, spinners active, context and wall-clock figures visible on the rows; (2) the Chat tab mid-conversation with the dictation editor showing a refined message ready to send. Real phone screenshots, portrait, placed side by side; no private topics on screen.

> 📸 **SCREENSHOT — dictate, refine, send (optional short GIF):** the desktop editor with a rough dictated paragraph, then the same paragraph after Refine, then the Send control. If a still: a before/after pair of the editor, cropped tight. Keep the content about the MCI program or this portfolio program.

### The machinery underneath

- **The Claude Code headless host** (`scripts/claude_headless/`): a local owner of the official Claude Code CLI, in its own words "not an inference proxy, terminal driver, pane server or replacement OpenCode client". It starts and supervises official Claude Code sessions, the Manager, Conductors and lead seats, on a subscription login; keeps exact send receipts and replayable records; offers interrupt, model and effort controls with applied readback; recovers once after an explicit capacity error; and at a usage limit halts honestly: *"⛔ Halted: Max usage limit reached … Nothing resumes by itself."*
- **The Manager's command line** (`scripts/oc_*.py`, through one launcher, `oc.sh`): the **boom** (`view`), every running program, Conductor and worker at a glance, with a program's state pushed into the Workflowy Markdown panel; `steer` to message a running agent; `quick` to run a whole quick program in one command; `peek` and `search` across every session's history, OpenCode or Claude; `telemetry` for context and wall clock per phase; `archive_cut` to move a finished wave of work out of Workflowy into a verified export file that stays readable, searchable and renderable.
- **Seats** (`oc_seat` on `oc_pack`): seat bases, forks, helpers, and `measure` to size a seat's reading before launch.
- **The OpenCode layer** (`proxy/`, `plugins/`, `config/`): a payload proxy in front of every model provider (prompt-cache lifetime under control, 429 backoff for about 25 minutes before OpenCode ever sees an error, a request log); five plugins, among them **`context_status`**, the tool every agent calls with no arguments to learn how full it is and what its own target is; the master config, with **auto-compaction off** by rule; a restart script that checks a candidate config on a throwaway server before touching the live one; an upgrade script with contract checks against the new OpenCode source, and rollback.
- **Agents files** (`agents/`): the global worker instructions, whose first half is in effect a Workflowy skill, and a cast of model files, one per model and thinking level across several providers, so a Conductor can cast a program. Claude Code gets no `CLAUDE.md`: its prompts are composed at launch from the same sources, with a drift test that refuses to build if the two runtimes have diverged.
- **A Chrome extension for CodeNomad**, a third-party OpenCode web client, that reports which session is on screen to the Manager App and shows tool calls with previews. Used less now that the Manager runs on Claude Code.

> 📸 **SCREENSHOT — the boom:** a terminal running `oc.sh view "[ap:…]"` on a live program (the MCI program): the program tree with the Conductor, its workers and their states, and beside it the Workflowy tab where the same state has been pushed into the Markdown panel. One wide capture or two stacked crops; the tree must be legible.

## A program, start to finish

Say you want a retry policy added to a launcher, properly: specified, implemented, audited.

1. **In a Manager session** you say so, out loud. The Manager captures the idea as an Anchor Point in the right Dock and, when you are ready, seeds a program: a Brief, a SkyView (what you are trying to get and how you will actually use it), and a program Dock to hold everything.
2. **The Manager launches a Conductor** with the one prompt. The Conductor reads the Brief and plans the units.
3. **Reconnaissance first.** A RECON worker renders the project's **Slice Catalog**, a page listing the codebase's topics as beacon tags with descriptions, picks the tags that bear on launching, renders that slice headings-only, snippets the functions that matter, and writes what it found into its socket, ending with a `🗣️ For the human` child you can read on your phone.
4. **A spec author writes the spec** as an Anchor Point. **An auditor** with no access to the author's reasoning checks it against the bar.
5. **Implementers** work from the spec and a slice, in a git worktree, keeping the beacons beside the functions they change current in the same edit. Each lands a result: what changed, every symbol touched, the tests run, the judgment calls, the warnings for the next audit.
6. **Audit, remediation, acceptance.** A fresh agent audits the result; a remediation chain fixes its findings; the Conductor accepts, consolidates, refreshes the code map once, and moves on, or hands off to a fresh Conductor when its own context runs low.
7. **You watched the whole thing** in the Manager App's program tree, read each For-the-human account as it landed, and steered once from the kitchen.

Every artifact in that story is a node in one outline. Nothing lived only in chat.

> 📸 **SCREENSHOT — the human view:** the Manager App's View pane (or the Workflowy panel) showing the program's `#prog-human` slice: a chronological list of `🗣️ For the human` accounts from the MCI program, each nested under the socket it explains, a few expanded enough to read. Crop to the pane.

## The skill is a Workflowy subtree

Taken together, the Manager instructions and the Program Conductor Toolkit are **a well-developed skill for agentic swarm management**. It is not a document on paper and it is not a Markdown file. It is a Workflowy subtree, read by progressive disclosure: an agent loads one page and follows its references as the work calls for them. What exists today, honestly:

- **The MCP tool descriptions are the first skill.** Load the Workflowy tools and you need nothing else: the server description, eight always-on family descriptions and per-verb `describe` text teach the whole system. Eight tools, about 44,500 characters on the wire, roughly 11K tokens, 61% smaller than the 43-tool surface they replaced.
- **The Manager instructions**: a Dock whose description, about 180 KiB, is the Manager's skill; eleven permanent canonical pages, about 560 KiB with the active-state pages; 211 sessions run on it so far.
- **The Toolkit**: about 1.7 MiB in 145 nodes. The Conductor's initialization, the dispatch schema, the program interface, a shelf of recipes for every block a program is made of, 25 worker clauses, and doctrine on bars and on human-readable reporting.
- **The five-line skill file is the plan, not yet the artifact.** The recorded decision: the release ships a small public Workflowy subtree and recommends that the first prompt of any session that will use the tools is to load it. Something like:

  ```
  Load the following publicly available Anchor Point and follow it: [ap:…]
  ```

  Copy the whole subtree into your own Workflowy and customize it for your purposes. That file does not exist yet, and there is a wrinkle we checked: a stranger's agent cannot read a Workflowy share through the official API or a plain page fetch, so the robust route is a curated public copy of each subtree **plus a committed Markdown export beside this README**. Both subtrees are written for their author's setup today; making them generic is the next phase. `[TODO Dan: public share links and committed export paths for the Manager instructions and the Toolkit]`

## Design notes

The rules that fell out of daily use. Each cost a real run to learn.

- **Handoffs, not summaries.** Auto-compaction is off in every session. Repeated summarization produced confident false positives that got written into source as durable, wrong documentation; a handoff through Workflowy did not. Every agent calls `context_status` and gets its own **target**: a goal to land by, not a wall to hit.
- **Point, don't describe.** Human: you never describe context; you point at it. Agent: you never guess context; you look.
- **Purpose first.** Every worker reads the program's SkyView before its contract, and rereads it before its decisive step. The human's actual use decides what matters, not a literal reading of a spec.
- **Fresh eyes certify.** Deep context writes; a blinded agent, usually another model, audits against a stated bar.
- **Disk is truth.** The code map in Workflowy is a projection. Edits happen in files, and beacons travel with the code in the same edit. Curate, don't carpet-bomb.
- **Complete, don't delete.** History survives; clutter leaves the view. A finished wave is archived to a verified file and stays readable.
- **One person, their machines, their agents.** This is not a team product. Teams share through git.
- **Visible by default.** Every worker ends with a plain-language account for the human, as its own node, and the cockpit shows it as it lands.

## Status, honestly

**This is a portfolio to read, not yet a product to install.** The system runs daily on one machine: Windows with WSL, OpenCode as a background service, the official Claude Code CLI on a subscription, Chrome, a Workflowy account and the NEXUS daemon. Forty-two files carry paths specific to that machine, and the web app imports the scripts directly. Making it installable elsewhere is a later phase; this phase makes it readable. No install promises here that the code cannot keep.

What a visitor will find, measured on 2026-10-23:

| | |
|---|---|
| Tracked lines in this repository | about 147,000 |
| Tests | about 1,800 Python test functions plus 38 JavaScript test files, including headless-Chrome real-DOM fixtures for the web app |
| Commits | 659 since 2026-09-10, 594 of them in the last 30 days |
| The web app (`transcription-pane/`) | 181 files, about 71,600 lines, 42,400 of them tests |
| The Claude Code host | 31 files, about 13,900 lines, plus about 1,700 lines of documentation |
| The Manager's CLI and seats (`scripts/`) | 33 files, about 20,300 lines |
| The sister repository, NEXUS | about 168,000 lines of first-party code, 5,402 test functions, 1,292 commits since 2026-01-23 |

By component: the Manager App, the Claude Code host, the scripts and seats, the OpenCode layer, the agents files and both Workflowy subtrees are **working and in daily use**. The CodeNomad extension is working but used less as the Manager moved to Claude Code. The public skill file is **planned**. The full inventory with public status is in [`COMPONENTS.md`](COMPONENTS.md) `[TODO Dan: placeholder; the components file is being written separately]`.

## Who made this

Not a company. One person: an ex-principal at Microsoft for five years, an individual contributor, never that active in open source and incredibly active in private source. Not a good marketer, but with something to share now.

In terms of the humans involved, it is a hundred percent me, even though I did probably zero percent of the actual programming. Agents wrote the code. I drove every idea, insisted on good design, and kept the project moving; the harness you have been reading about is what we built to do it. Nothing here wraps an existing agent framework: the harness drives two off-the-shelf coding agents, OpenCode and Claude Code, and the memory lives in Workflowy; everything in between is original. (The one starting point not mine, in the sister repository, was a minimal example Workflowy MCP wrapper, about one percent of what is there now. `[CONFIRM Dan: vladzima/workflowy-mcp]`)

I have just been holed up, non-stop, working with these agentic systems and finding ways to make them work the best, and this is what I've landed on. If you are looking for someone who has actually built and lived inside an agentic harness, rather than described one, this is what that looks like.

## Components, briefly

| Folder | What it holds |
|---|---|
| `transcription-pane/` | The Manager App: a stdlib-only Python server, the desktop and phone pages, the View pane's MCP client of the Workflowy daemon, and its tests |
| `scripts/claude_headless/` and `scripts/claude_host*`, `scripts/oc_claude*` | The Claude Code headless host and the Claude seat commands |
| `scripts/oc_*.py`, `scripts/oc.sh` | The Manager's command line and the seat/package system |
| `proxy/`, `plugins/`, `config/` | The OpenCode layer: payload proxy, five plugins, the master config, restart and upgrade scripts |
| `agents/` | The global worker instructions and the model cast |
| `chrome-extension/` | The CodeNomad session-watcher extension |
| `docs/` | The host's documentation, server lifecycle, unattended execution |

Where to start reading: **start with the Manager; the rest is behind it.** The Manager instructions and the Toolkit live in Workflowy (`[TODO Dan: links]`); the tools they run on are in NEXUS (`[TODO Dan: link]`); this repository is the runtime between them. A component-by-component account with public status is in [`COMPONENTS.md`](COMPONENTS.md).

## The sister repository

**NEXUS, the Workflowy MCP tools** (`[TODO Dan: link]`): the daemon and the eight verb families, Cartographer and beacons, GLIMPSE and the Chrome extension, and the Agent Kit, a standalone code-map skill that gives any repository beacon slices from disk alone, with no Workflowy at all. If you came here for agent swarms, that README is where the memory they run on is explained from the other side. The two READMEs reflect one another on purpose.

## License

`[TODO Dan: license for this repository]`

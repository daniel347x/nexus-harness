# The Manager App

**Run a swarm. Keep the thinking.**

An opinionated agentic harness over **OpenCode and Claude Code**, with a desktop-and-phone cockpit and a shared, visible memory in Workflowy.

Starting more agents is only part of the problem. The harder part is deciding what each one should know, preserving what it discovers, and keeping the human able to understand and steer the work.

The Manager App connects those things. Talk through an idea with a Manager. Turn it into a program with a Conductor and workers. Watch the agents work, inspect their conversations, and read their conclusions in the same outline that holds the plan. When a session ends, the work does not disappear into its transcript.

**The conversation can end. The project keeps its memory.**

> 📸 **SCREENSHOT — A swarm you can see:** Capture the desktop Manager App during an active open-source program. Show the Manager conversation beside the expanded program tree, with its Conductor and several workers visible. Include live status/spinners, context figures and elapsed-time telemetry. Crop to the app; use an open-source-only conversation and program, not the Manager Sessions Dock's overview.

This is a working system used daily by its creator, not a mockup. The app, the agent harness and the context-management tools were themselves built with agents. Today the repository is a portfolio to explore; a portable, clean-machine installation is still ahead.

## From a thought to a program—and back

The **Manager** is the agent you think with. It helps investigate a question, develop a plan and keep track of what matters across conversations. When the work needs a swarm, a **Conductor** runs the program. Its workers receive explicit assignments and write durable results, rather than leaving their conclusions buried in a chat thread.

```text
You ↔ Manager: think, plan, remember
         │
         └─ Conductor: run a program
                ├─ worker → result
                ├─ worker → result
                └─ audit → remediation

       Plans, assignments and results: one shared Workflowy tree
       Execution: OpenCode + Claude Code
       Human view: Manager App on desktop and phone
```

In the app you can:

- **Dictate, refine, send.** A persistent editor holds your words; Refine uses the conversation as context to clean up the dictation before you send it to the Manager.
- **Read the actual conversation.** Follow the Manager live and inspect tool calls, on either runtime.
- **See the program, not just a row of chat tabs.** Conductors and workers appear as a tree, with live status, context and wall-clock telemetry.
- **Read the work where it lives.** The View pane renders Workflowy documents and selected views through NEXUS, the same engine used by its browser extension.
- **Take the cockpit with you.** The phone interface has Chat, Programs, Context and View tabs; the current setup reaches it over Tailscale.

> 📸 **SCREENSHOT — The phone is a working cockpit:** Capture the phone's Programs tab with an open-source program expanded to its Conductor and worker rows, including status and context telemetry. Place a second, equally sized capture alongside it of Chat with the persistent editor visible. Crop to the phone viewport; show the tab bar in both images.

The app is the visible surface. Beneath it is a well-developed skill for agentic swarm management—and a deliberate answer to the context problem.

## Memory is a set of nodes, not one enormous conversation

Imagine the project's knowledge as points of light. One set lights up for the architect. Another lights up for an implementer. An auditor gets the material needed to question the result. All three work against the same record, without all three loading everything.

That is the model here. **A node is a unit of memory. A selected set of nodes is context.**

NEXUS gives those units a few concrete properties:

| Primitive | What it buys you |
|---|---|
| **Anchor Points** | Markdown notes with stable citation handles. Move or rename a note without breaking the reference a worker received. |
| **Anchor Docks** | Named containers for a session, project or topic. Capture first; curate durable material into its longer-term home. |
| **Slice groups** | Saved, tag-selected views that cut across the outline. Select the relevant material without relocating or copying it. |
| **GLIMPSE** | Render the selected nodes as a continuous Markdown document, or let an agent read the view the human has open. |
| **Cartographer and beacons** | Bring the codebase into that same navigable outline, with source files remaining authoritative on disk. |

An invented example: a **Project launch** view can collect the product decisions, technical constraints and current implementation notes from their different homes. A worker gets the relevant selection. The Manager can return to the broader view. Adding a note to the selection does not require making another briefing document.

The human can inspect and edit the memory itself—not merely ask the agent what it remembers. And an agent's result becomes another addressable part of the record.

> 📸 **SCREENSHOT — A context selection made visible:** In Workflowy, use a neutral demonstration project named “Project launch.” Show a tag-selected slice of notes from at least two branches and the docked Markdown panel beside it, rendering those same notes as one document. Keep the matching headings and one stable Anchor Point handle readable. Crop out unrelated branches. This is a capture brief for a demonstration, not a claim that a prebuilt sample ships.

## Why Workflowy?

Because the structure of memory and the way a person sees it should be the same thing.

Workflowy is a node-based outline inside one document. A node can hold a full Markdown note. Nesting provides structure; tags cut across it; moving a bullet sets an arbitrary reading order. You can reshape the record by indenting, outdenting or moving nodes while still seeing their contents and where they belong.

NEXUS makes that structure usable as agent context. It renders a branch or a tag-selected view as one document without creating another document to maintain. The Markdown panel is simply a comfortable reading surface beside the outline; the Manager App's View pane brings those renders into the cockpit.

That combination is why Workflowy was chosen—not because this began as a request to add AI to a notes app. Build a node-based context system from scratch and you would still need a human interface for browsing, editing, ordering and selecting it. Workflowy supplies that interface along with the structure.

**If Workflowy didn't exist, this is the sort of thing we would have to invent.**

This is not a claim that memory, Markdown tags or progressive disclosure are new. Claude Code and Codex use instruction and memory files; Letta has shared Markdown context repositories; Obsidian supports tags and queried views. Workflowy itself has a Desktop MCP, and Tana has an official MCP integration. The distinctive choice here is the combination: **human-visible memory, stable citations, curated context selections, a mapped codebase and a swarm's working record in one canvas.** The case is in the design and daily use, not a claimed benchmark win over curated files.

## Two halves, all the way down

Planning and programming need different views of the same undertaking. The system mirrors that at both layers:

| | Knowledge work | Source-code work |
|---|---|---|
| **The tools: NEXUS** | Capture, read, search, render and curate notes | Map repositories, select beacon slices, read live symbols |
| **The harness** | **Manager Sessions:** think, plan, remember and maintain continuity | **Program Conductor Toolkit:** dispatch workers, implement, audit and remediate |

The Toolkit can run knowledge-work programs too. The split describes the emphasis, not a wall between them.

On the code side, **Cartographer** maps folders, files, classes and functions into Workflowy. **Beacons** are small comments beside selected source definitions: labels and descriptions that agents maintain as the code changes. A beacon slice brings related symbols across files into one view of signatures and comments. When the agent needs a body, `beacon.snippet` reads the symbol live from disk by name.

Source code stays in git. Workflowy provides its map and the surrounding reasoning. The plan, the implementation's context and the result can refer to one another without pretending they are the same artifact.

For the tools themselves: **NEXUS / Workflowy MCP README — [TODO Dan: public repository link].**

## Carry the context. Bring the conclusion home.

The swarm machinery makes that philosophy operational:

- **Seat bases** are parked sessions preloaded with shared reading. Forking them lets workers begin with context already in place.
- A **socket** is an Anchor Point containing one worker's dispatch: its role, inputs, contract and permitted writes.
- **Results and decisions** are durable nodes. A worker writes its conclusion back to the shared tree, where the Conductor can use it and the human can read it.
- **Audits and remediation** are explicit work, rather than the implementing agent simply declaring itself finished.

The idea behind the fork is to carry the understanding into the work. The idea behind the Anchor Point is to bring the conclusion back. **The anchor is what survives the fork.**

The `oc_*` command-line tools support the runtime side: launching and steering sessions, managing seats, reading histories, measuring context, observing programs and archiving finished work. The Claude Code headless host supervises official Claude Code CLI sessions; the OpenCode layer supplies its own integration, plugins and payload handling. Both participate in the same Workflowy-based record.

## The skill lives in Workflowy

**Manager Sessions and the Program Conductor Toolkit are Workflowy subtrees.** They are living instruction systems, not just a long prompt pasted into every worker. Agents load an entry point and follow the relevant pages as the task requires.

The proposed public on-ramp is correspondingly small: a short skill file pointing to the entry point, with a subtree people can copy and customize. **That packaging is planned, not shipped.** The underlying Manager and Conductor systems already run daily.

Public reading also needs a straightforward path. A Workflowy share is browsable by people without an account, but a stranger's agent cannot simply load that share through the official API. The publication approach is a public subtree accompanied by a Markdown export for direct reading.

- **Manager Sessions:** [TODO Dan: public Workflowy subtree and Markdown export links].
- **Program Conductor Toolkit:** [TODO Dan: public Workflowy subtree and Markdown export links].

For NEXUS itself, the MCP tool descriptions already provide the instruction layer: the server introduction, eight tool-family descriptions and per-operation `describe` documentation. A separate skill is not required to begin using those tools.

## What is here today

| Component | Status and where to look |
|---|---|
| **Manager App** | Working, used daily on desktop and phone. Source remains in [`transcription-pane/`](../../transcription-pane/) from its earlier name. |
| **Claude Code headless host** | Working, used daily; official CLI sessions, supervision and conversation records. [`scripts/claude_headless/`](../../scripts/claude_headless/) and [`docs/claude-headless-host.md`](../claude-headless-host.md). |
| **Manager CLI and seats** | Working, used daily. Program observation, steering, warm seat bases and worker launches in [`scripts/`](../../scripts/). |
| **OpenCode integration** | Working, used daily. Payload proxy, context/output plugins, configuration and agent prompts. |
| **CodeNomad extension** | Working; used less as the Manager moves into the app's Claude Code interface. |
| **Manager Sessions and Toolkit** | Working Workflowy-native instruction systems; public copies and skill packaging still to be prepared. |
| **NEXUS and its Chrome extension** | Working companion tools. The extension currently loads unpacked; public installation polish and its UI redesign are ahead. |
| **Agent Kit** | En route: standalone, disk-based beacon tooling without Workflowy. Built and locally tested, not yet an established external-user release. Lives with NEXUS. |

Full component guide: [COMPONENTS.md](../../COMPONENTS.md) — placeholder for the companion inventory.

### Running it

**This is currently a portfolio to read, not yet a product to install unchanged.** The daily setup uses Windows + WSL, Chrome, Workflowy/NEXUS and OpenCode and/or Claude Code, with Tailscale for phone access. Machine-specific paths remain in the app's surrounding tooling. The existing operational documentation describes that setup; portable installation is future work.

[TODO Dan: repository name]. The public name is not yet settled; “Manager App” is used here throughout.

## Who made this

I'm Dan, an individual contributor and a former Principal at Microsoft, where I spent five years. I've been much more active in private source than open source. I'm not much of a marketer; I've been heads-down working with agentic systems and finding ways to make them work well. This is what I've landed on, and what I want to share.

**Agents wrote the code. I drove the ideas, the architecture and the insistence on good design.** That is an advertising point: an agentic supporting system built with agents, used to build itself.

This is not a company or a wrapper around someone else's swarm framework. Workflowy, OpenCode and Claude Code are the platforms it builds on. The NEXUS side began with a minimal example Workflowy MCP wrapper—[CONFIRM Dan: vladzima/workflowy-mcp]—and grew into the context-management layer described here.

If you're interested in how a person can think with agents, direct a swarm and keep a coherent record of both, that's the conversation I'd like this project to start.

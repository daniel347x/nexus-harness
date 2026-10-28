# The Manager App

## Run a swarm. Keep the thread.

**An opinionated agentic harness over OpenCode and Claude Code, built around a shared memory you can see.**

Starting another agent is easy. The harder questions are what it should know, where its conclusions should go, and how you stay able to understand and steer the work.

The Manager App brings those pieces together: a desktop-and-phone cockpit, a system for sustained knowledge work, and a toolkit for running conductors and workers. Their shared working record is a Workflowy outline. Plans, instructions, source-code maps, assignments and results have addresses in the same place—not just appearances in separate conversations.

**Think with a Manager. Turn a plan into a program. Bring the results back into the thinking.**

> 📸 **SCREENSHOT — A swarm at work:** Capture the Manager App on desktop, in Claude mode, with the sidebar's Running now section expanded through one real program → Conductor → lead seat → helper. Keep both Claude Code and OpenCode rows visible, with active spinners, model labels and context/wall-clock telemetry legible. Show the selected worker's conversation beside the tree. Use an open-source program; crop to the app, roughly 16:9.

## From a conversation to a program—and back

The app is the control surface. **Manager** and **Conductor** are agent roles. OpenCode and Claude Code run the sessions; Workflowy holds the durable work between them.

1. **Think out loud.** Dictate or type to your Manager. Refine cleans up the dictation using the conversation as context; Send puts it into the Manager's conversation. The Manager helps reason through a project and captures decisions worth keeping.
2. **Give the work a shape.** When a conversation needs a swarm, the Manager helps set up a program. Its Conductor dispatches workers with a contract, selected reading, explicit write scope and a place to report.
3. **Watch the work, not a wall of terminals.** The app shows programs, conductors, workers and helpers as a live tree. Open a conversation, inspect a tool call, see context consumption and elapsed time. The Manager's command-line tools can inspect and steer running sessions.
4. **Bring conclusions home.** Workers write results into the shared outline. The Conductor runs the program's review and remediation process; the human gets a readable account alongside the technical record. A later session can read the actual decisions and results instead of asking you to reconstruct the project.

The same loop supports research and planning as well as programming. You can keep talking with your Manager while delegated work runs.

> 📸 **SCREENSHOT — Away from the desk:** On a phone, open the Manager App's `/m` page over the existing Tailscale connection. Select Programs and expand the same program used in the desktop shot. Show the Conductor, workers and at least one running row with its context/time figures; keep the Chat · Programs · Context · View tabs visible. Portrait crop, with names large enough to read.

## The important design choice: context is a selection

A useful agent needs the right history, not necessarily all the history. A swarm makes that distinction unavoidable: the architect, implementer and reviewer need different views of the same project.

Here, a unit of memory is a **node**. It can hold a short decision or an entire Markdown document. An **Anchor Point** gives it a stable citation handle that survives moves and renames. **Anchor Docks** organize related material. **Slice groups** select across that organization by tags.

For an illustrative “Project launch,” the planning view might contain the purpose, constraints and unresolved choices. An implementation view adds the relevant source symbols and technical requirements. A release view brings together the finished results and release notes. They are selections of the underlying material, not three copied briefing documents to keep synchronized.

**Docks are where things go. Slice groups are what you look at.**

Human and agent can render a selected subtree or slice as one continuous Markdown document. A saved selection can be used again as the work evolves. The agent can search within that selection, read a particular note by handle, or open a source function by name.

Imagine the nodes as points of light: one set lights up for the planner, another for the implementer, another for the reviewer. The record stays put. What changes is what each agent sees.

### Why Workflowy?

Because the memory store and the human's view of it belong together.

Workflowy already gives us a node-based outline in one document, tags that cut across it, and an immediately usable way to browse and reshape it. Move a bullet to change the reading order. Indent or outdent to change the structure. Open the relevant branch and see the notes where they live.

NEXUS, the companion Workflowy MCP tooling, turns that into a shared context-management surface. An agent can read your expanded view; it can also render a selected view back to you. The extension's docked Markdown panel is simply the reading pane for that selection. The Manager App's **View** pane brings those documents into the app on desktop and phone.

If we started with a bare database, we would still need to build this human-facing layer. Workflowy supplies it with the data structure. **For this approach to agentic work, it is the best substrate we know.**

> 📸 **SCREENSHOT — The memory, and its reading view:** In Workflowy, use an invented “Project launch” Dock containing Purpose, Decisions and Release notes. Show the relevant tagged nodes in the outline and their slice rendered in the docked Markdown panel alongside it. Keep a stable Anchor Point handle and the selected slice's name visible. Crop out unrelated branches; frame the outline and document at equal, readable widths.

This builds on, rather than denies, the progress elsewhere: curated instruction files and progressive disclosure are established techniques; Letta has shared Markdown memory, Obsidian has tags and query views, and Workflowy and Tana have their own MCP integrations. The proposition here is the combination: **human-visible, addressable memory; explicit cross-cutting context selection; code in the same outline; and a working swarm harness over it.** This is a design demonstrated in daily use, not a claim of benchmarked superiority over curated files.

## Two halves, reflected all the way down

Effective programming starts before a worker touches a file. Someone has to decide what to build, remember why, and recognize when the result changes the plan. This system gives that work the same standing as implementation.

| What you are doing | The agentic system | The NEXUS tooling beneath it |
|---|---|---|
| **Thinking, planning, remembering** | **Manager Sessions:** an ongoing thinking partner, session records and deliberate curation into durable knowledge | Anchor Docks and Points, tags, slice groups and Markdown views |
| **Building, reviewing, improving** | **Program Conductor Toolkit:** conductors dispatch workers, collect results, arrange audits and run remediation | Cartographer code maps, beacon slices and live source snippets |

Both halves use the same outline. A decision does not have to be reintroduced as an unrelated file before it can become a worker's input. A worker's finding can come back into the planning record by reference.

### The code stays code

**Cartographer** maps a repository into Workflowy, with nodes for folders, files, classes and functions. **Beacons** are small comments in the source that label important symbols and explain their intent. A beacon slice brings the relevant signatures and comments together across files; `beacon.snippet` opens a mapped symbol live from disk.

Agents maintain beacon comments with the code: curate the useful entry points, rather than label everything. The map is a projection, refreshed from the repository. **Disk remains the source of truth.**

That gives a planning agent a cross-cutting view of the architecture and an implementer a direct route to the functions it needs. The same selection machinery that organizes knowledge also organizes code context.

### Fork the context. Anchor the result.

The seat system can prepare a **seat base**: a parked session already loaded with the program's shared reading. Workers fork from it, starting with that context in place. Reviews can receive their own separately chosen reading.

Each worker also has a **socket** in Workflowy: its assignment and its report home. That is where the result comes back—not merely into another chat branch that the human must hunt down.

**“The anchor is what survives the fork.”** The mechanism matters as much for a delegated research question as for a code change. Context travels into a session; conclusions return to the shared record.

> 📸 **SCREENSHOT — The hand-back:** In the Manager App's View pane, render one finished result from an open-source program, with its readable human account and technical result visible. Keep the program/worker tree beside it so the reader can connect the report to the worker that produced it. Choose a short result with a clear outcome and a visible citation handle; crop to that branch and the View pane, roughly 16:9.

## The skill is a Workflowy subtree

The Manager instructions and the Program Conductor Toolkit form **a well-developed skill for agentic swarm management**. They are living Workflowy subtrees, not a single Markdown prompt pasted into every session. Agents read the entry point and follow the relevant instructions as the work calls for them.

The public on-ramp is designed to be tiny: a short skill file that says, in effect, “load this entry point.” **That packaging is planned, not shipped.** The working instruction systems already exist and are used daily; public copies and the small loader are still being prepared.

The intended reading routes are a view-only Workflowy share for people and a committed Markdown export for reliable access by outside agents. A public share can be browsed without an account, but it is not directly loadable by a stranger through the official API. Loading an Anchor Point through NEXUS requires the content to be accessible in the connected account; the export avoids making that a prerequisite for reading the skill.

- **Manager Sessions:** [TODO Dan: public Workflowy share and Markdown export links]
- **Program Conductor Toolkit:** [TODO Dan: public Workflowy share and Markdown export links]
- **NEXUS / Workflowy MCP:** [TODO Dan: companion repository README URL]

For the NEXUS tools themselves, the descriptions already teach their use: eight verb families, with `describe` revealing each operation's full instructions. You do not need a separate skill just to use the tooling. The Manager and Toolkit add the working method on top.

## What is here today

The app, hosts, scripts and instruction systems are **working and used daily**. This repository is currently a portfolio to inspect, not a turnkey installation: machine-specific paths and setup assumptions remain. The daily environment uses Windows and WSL, Chrome, OpenCode and the official Claude Code CLI, with NEXUS connected to Workflowy; phone access uses Tailscale.

**Repository name:** [TODO Dan: repository name]

| Component | What it contributes | Status |
|---|---|---|
| **Manager App** — `transcription-pane/` | Dictation, context-aware Refine, Manager conversations, tool inspection, program trees and View on desktop and phone | Working, daily use |
| **Claude Code headless host** — `scripts/claude_headless/` | Starts and supervises official Claude Code sessions, with send receipts, controls and durable conversation records | Working, daily use |
| **Manager CLI and seats** — `scripts/oc_*` | Launch, inspect, steer and resume work; prepare and fork seat bases; report context/time telemetry | Working, daily use |
| **OpenCode integration** — `proxy/`, `plugins/`, `config/` | Provider transport, context measurement, tool-output handling, configuration and restart/upgrade support | Working, daily use |
| **Agent files** — `agents/` | Model wrappers and prompts; Claude prompt builders reuse the shared instruction sources | Working; tailored to the current setup |
| **CodeNomad Chrome extension** — `chrome-extension/codenomad-session-watcher/` | Session following and display improvements for the separate CodeNomad OpenCode client | Working; used less as the Manager moves into the app |

The companion NEXUS repository holds the MCP server, Cartographer, beacons and the GLIMPSE Chrome extension. Its disk-only **Agent Kit** brings beacon slices to repositories without Workflowy; it is **en route**, with an implemented and tested first pass. A packaged Workflowy skill and Workflowy agent remain planned.

See [the components and status guide](COMPONENTS.md) for the full inventory. [TODO Dan: connect this placeholder to the published components file.]

## Who made this

This is one person's project. I'm Dan. I spent five years as a principal at Microsoft, as an individual contributor. I've been much more active in private source than in open source. I'm not much of a marketer; I've been working continuously with agentic systems, finding ways to make them work better, and this is what I've landed on.

I drove the ideas, architecture and design decisions. Agents wrote the code, using the evolving system described here. **This is an agentic supporting system that helped build itself.**

Workflowy, OpenCode, Claude Code and CodeNomad are independent projects. NEXUS began from a minimal example Workflowy MCP wrapper [CONFIRM Dan: vladzima/workflowy-mcp]; the context-management system and swarm harness grew from that starting point, rather than wrapping an existing swarm framework.

What I want to share is the whole loop: a person thinking with agents, agents carrying out the work, and a visible record that lets both keep going.

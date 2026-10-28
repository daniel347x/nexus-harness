"""Body-only native Manager prompt composition.

The Manager's doctrine is the body of ``agents/agent/manager.md`` — the OpenCode primary agent whose
body is the whole Manager system prompt. The persistent headless host needs the same doctrine as a
plain prompt body for Claude Code, with OpenCode-only mechanics replaced by the native harness's,
terminal-only guidance dropped, and the doctrine itself otherwise untouched:

- the YAML frontmatter is removed, so no ``mode``/``model``/``permissions`` lines reach a native
  prompt;
- the exact doctrine passages that describe OpenCode-only mechanics are replaced (see
  :data:`BODY_OVERRIDES`) with native equivalents: the native Workflowy tool names and connection
  reporting, launch-bound measured context, Claude Code's own oversight of oversized results, and its
  own Grep/Glob tools;
- an adapted native runtime section (:data:`NATIVE_RUNTIME`) closes the prompt. It is derived from
  the generic WSL manager guidance but with its terminal-only directions removed — no ``/context``
  command, no Remote Control, no pane history. The shared native context overlay follows it.

Composition is deterministic: every override anchor must appear exactly once in the source. Doctrine
drift never stops a launch, and is never silent: the drifted override is skipped with a loud DOCTRINE
DRIFT warning (stderr and the launch record), and the suite's drift test fails on it. This module
performs no inference and never talks to the host. The composer is read-only; the separate
publication helper atomically writes an owner-readable latest-prompt preview.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import tempfile

MANAGER_PATH = Path(__file__).resolve().parents[2] / "agents" / "agent" / "manager.md"
"""The Manager doctrine source; its frontmatter-stripped body is the whole system prompt."""

_FRONTMATTER = re.compile(r"\A---\r?\n.*?\r?\n---\r?\n", re.DOTALL)

NATIVE_RUNTIME = """\
---

# RUNTIME: NATIVE HEADLESS CLAUDE CODE (overrides the OpenCode and pane assumptions above)

You are the Manager in the official Claude Code CLI on **WSL/Linux**, launched by the persistent
local headless host under Dan's own Max subscription and driven with no terminal and no browser pane
in the loop. Everything above about the Manager role, Workflowy, the Manager Sessions Dock, the
Toolkit and initiative applies unchanged; this section adapts the harness.

- **Shell and file tools** are Claude Code's Bash, Read, Write, Edit, Grep and Glob. Windows drive
  paths translate to `/mnt/e/…`; use WSL paths for Bash. The root-search rule above applies to these
  tools as written. The host refuses a root search it is certain of, before anything runs: a
  whole-tree walk (`grep -r`, `find`, `ls -R`, `du`, `tree`, `rg -u` …) from a repository root or
  above, `cd repo && … grep -r … .` included, or a Grep, Glob or `rg` across the repositories under a
  folder above them (`__repos`, `$HOME`); its message names the subfolders to search instead. What it
  cannot be sure of passes, so the rule is still yours to keep.
- **Native Workflowy names** under the MCP server key `workflowy` are `mcp__workflowy__node`,
  `mcp__workflowy__anchor`, `mcp__workflowy__beacon`, `mcp__workflowy__glimpse`,
  `mcp__workflowy__lightning_rod`, `mcp__workflowy__slice_group`, `mcp__workflowy__tag` and
  `mcp__workflowy__system` (each with the same prefix), not doubled `workflowy_*` names. Calls keep
  the `{verb, params}` form; use each family's `describe` before guessing nested payloads. Report
  missing native discovery/connection in one line; never substitute an OpenCode MCP receipt.
- **Programs and seats run through the bridge**:
  `bash /mnt/e/__daniel347x/__Dan_Root/__repos/opencode-companion/scripts/oc.sh ‹name› [args…]` —
   use `fresh` / `scouted` dispatch from this native Manager. The existing OpenCode `fork` route
   still requires a real OpenCode source session; this native conversation is never its source.
   Launch a quick program (`oc.sh quick …`) with Bash `run_in_background: true` and answer Dan at
   once; its completion notification carries the root, its sockets and ids: read them then and arm
   the doorbells. Never launch it again while one launch is still unresolved.
  The same scripts start and manage **Claude Code** Conductors and seats on Dan's Max
  subscription (an `anthropic/*` agent or model routes there): `oc.sh quick … --model
  anthropic/claude-opus-5-5 --variant ‹level›` seats a Claude Conductor, `--worker
  quick-opus55-‹level›` (or `quick-fable51-‹level›`) a Claude worker, and a Conductor's `oc_seat.sh dispatch` starts Claude cast
  workers and lead seats. Each prints its own `cc-…` id, which goes back to the same scripts
  (`oc.sh seat wait · info · socket · resume · cancel · resolve`, `oc.sh steer`, `oc.sh seal`); they
  route it to the Claude host. **This conversation's own id is never passed to them:** never pass
  this conversation's native Claude session UUID (or its `cc-…` form), or a native transcript id, to
  any `oc.sh` command or through OpenCode's fuzzy `ses_` resolvers,
  and never ask OpenCode to drive, resume or fork this conversation. The one exception takes no id:
  `oc.sh seat quick --fork-self` (below) finds this conversation from its own environment.
- **Manager-controlled quick work** has three ways here, chosen as the Manager Sessions
  description's *⚡ Quick workers straight from the Manager* says: a native child (this bullet's
  first half), a top-level quick seat (its second half), or a fork of this conversation (the next
  bullet). For a native child, use the **Agent** tool with
  `subagent_type: "manager-quick"`, your socket in `prompt`, a short `description`, and
  `run_in_background: true` when the conversation should go on. Do not wait in foreground
  merely to collect it: native task completion notifications are your doorbell. A later Manager
  Session rings its own on the child by its socket (`oc.sh seat wait --socket "[ap:JOB]" --max
  3000`). Read its socket's
  `## Result`; a completed task is not task acceptance. It inherits your current model unless
  you explicitly override `model` on the call. Thinking effort is chosen by agent type, not on
  the call: `manager-quick` uses the session default, and `manager-quick-medium`,
  `manager-quick-high`, `manager-quick-xhigh` and `manager-quick-max` are the same worker at that
  effort (pick by the job's difficulty). Never use another agent type (for example
  `general-purpose`) for real work; these native children belong to this Manager. **A top-level
  quick seat** (preferred when the job may well outlive this conversation; for the strongest
  tracking, a Conductor-owned quick program, `oc.sh quick`) is a quick worker as a session of its own, on Claude Code
  (`oc.sh seat quick --agent quick-opus55-‹level› --socket "[ap:JOB]" --no-wait` prints a `cc-…`
  id; `quick-fable51-‹level›` for Fable 5.1) or OpenCode (`quick-sol61-…`, a `ses_…` id); collect it with a background
  `oc.sh seat wait ‹id› --max 3000` (Bash `run_in_background: true`, `timeout: 3600000`; 2 still
  running: start it again; 4 needs attention (`cc-…` or `--socket`) and 5 UNKNOWN, never done; 9 handed over
  to a newer Manager Session: write nothing about it and stop). Such a
  doorbell wakes only this conversation: at curation, TaskStop it and list it in the doorbell
  Anchor Point (Manager Sessions description, *⏰ Doorbells and the handoff*). There is no OpenCode `subagent` tool in Claude Code. A `manager-quick`
  child's authorized helpers use `oc.sh seat quick --helper --no-wait` (OpenCode `quick-ds41-flash`
  or Claude Code `quick-opus55-‹level›` · `quick-fable51-‹level›`); never pass this native identity as `--parent-seat`.
  `--fork-from` is OpenCode's: a Claude conversation cannot be forked into OpenCode. Watch programs with
  `oc.sh view` (OpenCode and Claude Code sessions alike;
  `oc.sh peek --sid` reads either's history), `oc.sh steer` and `oc.sh session list`.
- **A fork of this conversation** is the quick worker that needs no job written: it holds what Dan said
  and everything you read, this turn included, so its job is simply what was just asked. Two kinds, both
  started by one background call (Bash `run_in_background: true`, `timeout: 3600000`):
  `oc.sh seat quick --fork-self [--forklift] [--doorbell none|launch|program] [--variant V] --title "‹what›"`;
  a sub-session Manager (say `084-b`) passes its own number first, `oc.sh seat --session 084-b quick
  --fork-self …`, so the fork, its title and a forklift's program carry that session. Nothing else goes
  in: no job text, no socket, no Dock; `--variant` changes only the fork's thinking level, cache kept
  (otherwise it inherits yours). The title (a few plain words, its own for each fork) names it in
  Dan's sidebar and lets the script find the call; before several calls in one message, say in one
  line which title does which part (a title that
  names its part is best): what you write after the call is yours, never the fork's job. A **quick fork
  worker** (no `--forklift`) does one job and launches no program; use it when the job rests on this
  conversation (a fix, a reconnaissance, a job you would otherwise spend minutes briefing), including
  reconnaissance before you seed a full program (its report is a local Anchor Point in the session
  Dock, `🍴 Result · ‹title›`, `ap_id: auto`; its final message names that AP-ID first: read it and carry
  what the program needs into the Brief). A **forklift** (`--forklift`; Dan says "forklift …") prepares and
  launches one program for you (a quick program, fresh or scouted, or a full program from Dan's spine),
  writing the program's records as you would; "kick off" or "launch" without it means you launch it
  yourself. On "forklift X", zero preparation: one background call with a title naming its part,
  back to Dan in under 15 s; any check the forklift can do, you do not. It is the Manager for that one
  job: quick reads of both providers' quota (`oc.sh usage`), network / modem trouble, Claude host and
  OpenCode health, running programs touching the same repository or files, Project Maps, Residue and
  Board for conflicts or anything urgent, then route and launch when called for. A real blocker rings at once without launching;
  minor issues go in its final message, not a launch gate. Its writes stay its own program's records
  and one Board-row insert; no other existing AP, no Residue. Useful new local APs in the session Dock
  are allowed, slightly discouraged but encouraged when of clear value; each opens with its forklift
  title and `cc-…` id as creator, and its final message lists every AP it created. Residue, curation,
  other steering and the rest of the Board remain yours. Both fork kinds read 🚦 PARALLEL PROGRAMS
  [ap:8TRTVQ] and route by it before launching or editing code/shared documentation: fresh
  `oc.sh view --running --records`, `oc.sh view --recent 30 --records` and git, not copied observations.
  The records line gives SkyView, current Briefs, StageConfiguration, quick job and seal from the cached
  root note; when routing/conflict is doubtful, the fork reads the related SkyView/Brief and job/config
  as needed. Spending its throwaway context is intended. Small independent
  overlaps usually PROCEED; small requests prefer piggyback STEER into an already-launched, unmerged
  small quick job in the same repository/area, with one writer for a sentence or passage. The clause
  separates that offer's fork-owned decline/timeout fallback from an ordinary handover's
  Conductor-owned declined launch. The one exception to their no-steering rule is to relay this
  request once and recorded to the Conductor of a running program that owns its subject, instead
  of launching, and wait a few minutes for its answer; no other management or HALT/restart authority.
  Their final message puts ROUTE and one sentence of comparison after its first line. A forklift's
  STEERED, WAITING or ASK DAN exit-4 ring is normal: relay it in one line, without your own reconnaissance.
  Its start (the proof, the fork; normally under
  10 s) runs in the background before it waits, inside the task's 1 h ceiling. No id: the script finds
  this conversation by the call itself and forks you; the first message says it is a fork doing one
  job for you: a quick fork worker, or the Manager for its one forklift job. **The call is the doorbell;
  you set none.** A quick fork worker's rings when it finishes and prints its final message, AP-ID
  first: read the result Anchor Point and report it to Dan. A forklift's rings
  once, when the forklift is done: with its program's launch line, or at once if it launched nothing
  (its final message says why, or asks Dan's decision); `--doorbell program` (Dan asks to hear when a
  quick program is done) then waits on that program's Conductor and rings when it stops; `--doorbell
  none` rings nothing. Only your own process ending (a restart, a crash) stops a doorbell unheard, as it
  does every doorbell; the fork runs on, and `oc.sh seat info cc-… --tail 20000` reads its result. Exit 2:
  run the re-arm it prints (it never forks); 4 needs attention and 5 UNKNOWN, never done; 9 handed over.
  Refused with `Nothing started`: a call it cannot find in your own conversation (a subagent's), past
  55% of your window, a thirteenth live fork (12 already live), a call that already made a fork, and any fork (a fork never
  forks). When it is refused, tell Dan in a line and fall back: a fresh quick worker with the context
  written into its socket, or launch the program yourself. Claude Code's own native fork subagent is
  pinned off for this Manager; never use it.
- **Steering:** `oc.sh steer` reaches Conductors and seats on either backend; a steer reaches a busy
  Claude session at its next tool step; one long-running command delays it until that command ends.
  Your own native `manager-quick` children are steered with **SendMessage**, addressed by the name or
  id their `Agent` result gives: a running child gets the message at its next tool round, a finished
  one is resumed with it. Use SendMessage only for your own native subagents, never for a Conductor or
  seat (those go through `oc.sh steer`, which records them).
- **Toolkit workers and Conductors** belong to the scripts, on either backend: a Conductor starts
  every program worker and lead seat with `oc_seat.sh dispatch` (or `fork`, `quick`), never a
  subagent (Dan's rule: no subagent inside a program). `manager-quick` is only the
  Manager-controlled route above, never a substitute for Conductor-owned seats/programs.
- **Model and effort:** the launch requests one explicit model and effort; the request is not proof
  of what is effective. Never claim an effective model or effort change you did not observe, and do
   not alter global native model/effort defaults silently.
   Native switches are Anthropic models only, through the host's correlated controls and applied
   readback. Cross-provider identity discussion above does not enable GPT or other-provider live
   switching in this conversation. A model switch can invalidate cache; no simulated confirmation
   screen is required.
- **Oversized output:** read a saved or truncated result from its file rather than re-running its
  producer — saved artifacts may be limited and an irreversibly lost excerpt cannot be restored.
  Capture consequential command output in authorized files before formatting, and report actual
  exits and receipts; a raised output limit or a timeout is not completion proof.
- **Compaction and handoff:** automatic and idle compaction are disabled; there is no automatic
  summary or promise of endless context. Follow the Manager continuity/handoff doctrine, preserve
  the baton in Workflowy and hand off at Dan's context boundary. UNKNOWN native context is not
  permission to run to the window limit; Dan supervises this Manager's context.
- **Launch-local, not global:** repository role policy makes this composed prompt the Manager persona
  (replacement, not a coding-prompt appendix), with private per-run settings and MCP configuration;
  nothing is written to `~/.claude/settings.json` or the login. A100 SSH and OpenCode plugin
  equivalents are absent or unqualified; do not claim they migrated with this prompt.
"""

BODY_OVERRIDES = (
    (
        "native-workflowy-tools",
        "**The Workflowy tools (`glimpse`, `node`, `anchor`, …) must be in your tool list.**",
        "**The native Workflowy tools (`mcp__workflowy__glimpse`, `mcp__workflowy__node`, "
        "`mcp__workflowy__anchor`, …) must be in your tool list.**",
    ),
    (
        "native-mcp-discovery",
        "(A known OpenCode gotcha: the first turn in a folder OpenCode hasn't used since its last "
        "restart can arrive without its MCP tools. Say so, and Dan will retry.)",
        "(A native session can come up without its Workflowy MCP tools or discovery: report the "
        "missing native connection in one line and stop — never substitute an OpenCode MCP receipt "
        "or fabricate a tool result.)",
    ),
    (
        "native-context",
        "**Context:** Dan watches the session's context himself; during a long operation you can "
        "check your own with the `context_status` tool, which reports how full you are and "
        "recommends when to wrap up.",
        "**Context:** Dan watches the session's context himself. Call the launch-bound native "
        "`mcp__context__context_status` with no arguments for measured input as of your previous "
        "completed model call, with its timestamp/age. This is not exact tool-time occupancy. "
        "UNKNOWN means no completed call yet in this generation, not zero. Claude Conductors and "
        "seats measure the same way (Dan's ruling). "
        "Never fabricate an unavailable or stale reading.",
    ),
    (
        "native-oversized-output",
        "**Oversized tool output.** Workflowy results reach you whole (all but `node scry` / "
        "`export`). Any other tool's result over about 50 KB arrives as a compact stub: the total "
        "size, the `outputPath` of the full output on disk, a small sample, and the line to "
        "continue from. When you need the full data, read that path from the given offset; don't "
        "re-run the command. OpenCode's `read` tool returns at most 2,000 lines per call and ends a "
        "longer file with `[Output truncated. Continue reading with offset: N]`.",
        "**Oversized tool output.** Inspect what was actually delivered; Workflowy whole-result "
        "delivery requires the deployed catalog metadata and its live qualification. Native "
        "500K-character/transport caps remain; use honest note windows or saved artifacts beyond "
        "them. When a result says it was truncated or saved to a file, read the rest from that "
        "file instead of re-running the producer. Claude Code's Read returns at most 2,000 lines "
        "per call; continue a longer file with an offset window.",
    ),
    (
        "native-grep-glob",
        "When searching files, prefer OpenCode's own `grep` and `glob` tools over `rg` / `grep` / "
        "`find` in the shell:\n"
        "\n"
        "- **Searching file contents:** `grep` (parameters: `pattern`, `path`, optional `include` "
        "glob, `literal`, `caseSensitive`, `limit`). Built on ripgrep: it honours `.gitignore`, is "
        "case-sensitive by default, returns 100 results by default with a truncation note, and "
        "times out after 30 seconds; results come grouped by file, with line numbers.\n"
        "- **Searching file names / paths:** `glob` (parameters: `pattern`, `path`, optional "
        "`hidden`, `limit`).\n"
        "\n"
        "The shell's `rg` remains available when specialized flags are needed (context lines with "
        "`-C`, columns with `--column`, piping), but `grep` and `glob` are the go-to default for "
        "files the map doesn't cover and for what it can't answer (call sites, string occurrences, "
        "tests). One known weakness of `grep`: it cuts a very long line at 2,000 characters from "
        "the start, so a hit far into a long line (a one-line beacon comment or docstring) may not "
        "show; `rg --column` in the shell finds it.",
        "When searching files, prefer Claude Code's own **Grep** tool over `rg` / `grep` / `find` "
        "in the shell, and **Glob** for file names:\n"
        "\n"
        "- **Searching file contents:** the **Grep** tool (pattern, path, an optional include "
        "glob, case sensitivity, result limit). Built on ripgrep: it honours `.gitignore` and "
        "returns results grouped by file, with line numbers; one repository's root is fine.\n"
        "- **Searching file names / paths:** the **Glob** tool. It honours `.gitignore` and stops "
        "after 60 seconds, so one repository's root is fine; never `__repos` or above.\n"
        "\n"
        "The shell's `rg` remains available when specialized flags are needed (context lines with "
        "`-C`, columns with `--column`, piping), aimed like the rest, but Grep is the go-to default "
        "for files the map doesn't cover and for what it can't answer (call sites, string "
        "occurrences, tests). A hit far into a very long line (a one-line beacon comment or "
        "docstring) can be cropped in a preview; `rg --column` in the shell finds it.",
    ),
)
"""The exact OpenCode-only passages replaced for the native harness: (name, anchor, replacement).

Each anchor must match the source body exactly once. One that is missing or repeated (doctrine drift)
is skipped with a loud DOCTRINE DRIFT warning, never a refused launch; ``test_claude_doctrine_drift.py``
fails on it, so whoever edits ``manager.md`` sees it at edit time.
"""


def _without_frontmatter(source: str, path: Path) -> str:
    match = _FRONTMATTER.match(source)
    if match is None:
        raise ValueError(f"the Manager source has no YAML frontmatter to strip: {path}")
    return source[match.end():]


def _apply_overrides(body: str, path: Path) -> tuple[str, list[str], list[str], list[str]]:
    """(body, applied names, skipped names, drift warnings): a drifted anchor skips only its own override."""
    applied: list[str] = []
    skipped: list[str] = []
    drift: list[str] = []
    for name, anchor, replacement in BODY_OVERRIDES:
        found = body.count(anchor)
        if found != 1:
            skipped.append(name)
            drift.append(
                f"DOCTRINE DRIFT: the Manager override {name!r} was skipped: its anchor must match exactly once in "
                f"{path} (found {found}), so this prompt carries that passage in its OpenCode wording. Update the "
                f"anchor in scripts/claude_headless/manager.py to the template's new wording "
                f"(test_claude_doctrine_drift.py names every one).")
            continue
        body = body.replace(anchor, replacement, 1)
        applied.append(name)
    return body, applied, skipped, drift


RESIDUAL_OPENCODE_MARKERS = (
    ('OpenCode-owned tool guidance', re.compile(r"OpenCode['’]s own", re.IGNORECASE)),
    ('OpenCode connection gotcha', re.compile(r'OpenCode gotcha', re.IGNORECASE)),
    ('OpenCode subagent tool', re.compile(r'`subagent`|\bsubagent tool\b', re.IGNORECASE)),
    ('OpenCode Manager quick agent', re.compile(r'\bmanager-quick\b', re.IGNORECASE)),
    ('OpenCode fork-from route (a Claude Manager forks itself with --fork-self)', re.compile(r'--fork-from\b')),
)
"""Deliberately short watchlist, not a ban on legitimate OpenCode bridge documentation."""


def residual_opencode_warnings(body: str) -> list[str]:
    """Scan adapted doctrine/context only; native runtime mentions these markers on purpose."""
    warnings = []
    for label, pattern in RESIDUAL_OPENCODE_MARKERS:
        for match in pattern.finditer(body):
            line = body.count('\n', 0, match.start()) + 1
            warnings.append(f'Residual OpenCode-only text: {label} at adapted line {line}: '
                            f'{match.group(0)!r}; review the native replacement')
    return warnings


def latest_prompt_path() -> Path:
    return Path.home() / '.local/state/opencode-companion/claude-headless/manager-prompt-latest.md'


# @beacon[
#   id=opc@publish_manager_prompt,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=Atomically publishes the exact prepared or launched Manager prompt at a stable owner-readable state path; never edits Claude settings.,
# ]
def publish_manager_prompt(text: str, path: Path | None = None) -> str:
    """Publish a complete UTF-8 prompt atomically, without following an existing target symlink."""
    path = latest_prompt_path() if path is None else Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.manager-prompt-', delete=False) as out:
            temporary = Path(out.name)
            out.write(text.encode('utf-8'))
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return str(path)


# @beacon[
#   id=opc@compose_manager_prompt,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=Composes native Manager doctrine; a drifted anchor skips its override with a loud DOCTRINE DRIFT warning (never a refusal), residual OpenCode text warns; strict mode rejects both.,
# ]
def compose_manager_prompt(*, strict: bool = False) -> dict:
    """Compose the body-only native Manager prompt; doctrine drift warns loudly, never refuses.

    Reads :data:`MANAGER_PATH`, strips its frontmatter, applies every :data:`BODY_OVERRIDES`
    replacement whose anchor still matches exactly once, :data:`NATIVE_RUNTIME`, and the shared
    native context overlay.

    Returns ``{"text", "source_path", "source_sha256", "prompt_sha256", "bytes", "overrides", "warnings",
    "drifted", "drift"}``: the composed system prompt as text, the doctrine file's path and byte SHA-256,
    the composed text's byte SHA-256 and size, the names of the overrides that were applied, every warning
    (drift first, then residual OpenCode text), the override names a drift skipped and their DOCTRINE DRIFT
    lines. Raises ``ValueError`` for a missing frontmatter, or for any warning in strict mode; it never
    writes, launches or infers anything. The intentional NATIVE_RUNTIME is excluded from the residual
    scan; adapted doctrine and the context overlay are checked.
    """
    raw_bytes = MANAGER_PATH.read_bytes()
    source = raw_bytes.decode("utf-8")
    body = _without_frontmatter(source, MANAGER_PATH)
    body, applied, skipped, drift = _apply_overrides(body, MANAGER_PATH)
    # Restart's mandatory composition preflight catches a missing/invalid quick doctrine
    # BEFORE contacting or closing the host. The actual launch regenerates its snapshot.
    from .quick_agent import manager_quick_definition
    _, quick_provenance = manager_quick_definition()
    from .context import RUNTIME
    warnings = drift + residual_opencode_warnings(body.rstrip() + '\n\n' + RUNTIME)
    if strict and warnings:
        raise ValueError('\n'.join(warnings))
    text = body.rstrip() + "\n\n" + NATIVE_RUNTIME.strip() + "\n\n" + RUNTIME
    return {
        "text": text,
        "source_path": str(MANAGER_PATH),
        "source_sha256": hashlib.sha256(raw_bytes).hexdigest(),
        "prompt_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "bytes": len(text.encode("utf-8")),
        "overrides": applied,
        "warnings": warnings,
        "drifted": skipped,
        "drift": drift,
        "manager_quick": quick_provenance,
    }

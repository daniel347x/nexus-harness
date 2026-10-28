"""System prompts for Claude Code seats started by the ``oc_*`` scripts (overnight build U1b, plan [ap:0OP1FK]).

A Claude seat is a top-level Claude Code session on Dan's Max subscription that a script launches
through the local host (``oc_claude.start``). It gets the same doctrine an OpenCode session of the same
kind gets, as an appendix to Claude Code's own coding prompt (role policy ``append``), with the
OpenCode-only mechanics replaced by native ones:

- **quick worker / quick helper** (:func:`compose_quick_worker_prompt`): a short top-level preamble,
  the quick agent file's body, the quick-worker folder's ``AGENTS.md`` (the quick workers' whole
  doctrine), the shared native seat runtime and the native context binding;
- **dispatched worker or fresh lead seat** (:func:`compose_cast_worker_prompt`, ``oc_seat
  dispatch``): a short top-level preamble, the cast agent file's body, ``agents/AGENTS.md`` (the
  worker instructions OpenCode reads from ``~/AGENTS.md``), the native cast runtime (the shared seat
  bullets plus helpers forked from a seat base or started quick, ``--parent-seat cc-…``) and the
  binding.
- **Conductor** (:func:`compose_conductor_prompt`, U2a, for ``oc_launch``): the ``conductor`` agent
  body, the same adapted ``agents/AGENTS.md`` (an OpenCode Conductor reads it too) and the native
  CONDUCTOR runtime: dispatch by script only, wait in background Bash and end the turn. Role policy
  ``replace``: Claude Code's own coding prompt is dropped, so this prompt stands on its own.

Composition is deterministic, as in :mod:`claude_headless.manager`: every override anchor must match its
source exactly once. Doctrine drift (an anchor missing or repeated) never stops a launch: that one
override is skipped, the prompt carries the passage in its OpenCode wording, and a loud DOCTRINE DRIFT
warning names it (stderr and the seat's ``prompt_drift`` label); ``test_claude_doctrine_drift.py`` fails on
it, so whoever edits a doctrine sees it at edit time. A short residual watchlist reports OpenCode-only
text that a new doctrine edit brought in (``strict`` refuses it, and drift). The seat's own ``cc-…`` id is NOT in the
system prompt: it arrives in the first message (a Conductor's is bound into its registry row), so
every session of one kind and agent shares one system prompt (and its prompt cache). Nothing here
talks to the host.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

from .context import RUNTIME
from .quick_agent import SOURCE as QUICK_SOURCE

REPO = Path(__file__).resolve().parents[2]
AGENT_DIR = REPO / 'agents' / 'agent'
CAST_SOURCE = REPO / 'agents' / 'AGENTS.md'
OC_SCRIPTS = '/mnt/e/__daniel347x/__Dan_Root/__repos/opencode-companion/scripts'
OC_SEAT = f'{OC_SCRIPTS}/oc_seat.sh'
"""The command a seat runs for helpers and waits (the doctrine's own path: the merged main checkout)."""
OC_SELF_RESUME = f'{OC_SCRIPTS}/oc_self_resume.sh'
CONDUCTOR_AGENT = 'conductor'

_FRONTMATTER = re.compile(r'\A---\r?\n.*?\r?\n---\r?\n', re.DOTALL)
SEPARATOR = '\n\n---\n\n'

QUICK_PREAMBLE = """\
# You are a top-level Claude Code session started by `oc_seat.sh quick`

You are a **QUICK WORKER**, or a **QUICK HELPER** when your first message says so, running in the
official Claude Code CLI on Dan's Max subscription: a session of your own, not an OpenCode session
and not anyone's subagent. Your session id is the `cc-…` id in your first message. The quick-worker
instructions below were written for OpenCode; the runtime section at the end of this prompt adapts
them, and where the two differ, the runtime section wins.
"""

ROOT_SEARCH_ENFORCEMENT = """\
- **Root searches:** the host refuses a root search it is certain of, before anything runs: a
  whole-tree walk (`grep -r`, `find`, `ls -R`, `du`, `tree`, `rg -u` …) from a repository root or
  above, `cd repo && … grep -r … .` included, or a Grep, Glob or `rg` across the repositories under a
  folder above them (`__repos`, `$HOME`); its message names the subfolders to search instead. What it
  cannot be sure of passes, so the rule is still yours to keep."""
"""What the Claude host's root-search guard enforces (docs/root-search-guard.md): one runtime bullet in every
Claude seat's prompt (manager.py words it for the Manager)."""

_SEAT_TOOLS_BASH_WAIT = """\
- **Tools:** Bash, Read, Edit, Write, Grep, Glob, WebFetch, WebSearch and TaskStop, plus MCP
  tools. The Workflowy tools your instructions write as `node(…)`, `anchor(…)`, `glimpse(…)`,
  `beacon(…)`, `tag(…)`, `slice_group(…)`, `lightning_rod(…)` and `system(…)` are
  `mcp__workflowy__node`, `mcp__workflowy__anchor`, `mcp__workflowy__glimpse` and so on (the same
  prefix for each), with the same `{verb, params}` form; use each family's `describe` before
  guessing a nested payload. WebFetch and WebSearch reach the web; there is no OpenCode `subagent`,
  `shell`, `read`, `grep` or `glob` tool. If the Workflowy or context tools are missing, say so in
  one line and stop; never substitute another tool's result.
- **Bash:** paths are WSL paths (`/mnt/e/…`). There is no working-directory parameter: put
  `cd /absolute/path && …` in the same command. Give every command that may run long an explicit
  `timeout`.
""" + ROOT_SEARCH_ENFORCEMENT + """
- **Waiting on a script:** one `oc_seat.sh wait … --max 540` fits a foreground Bash call with
  `timeout: 600000`. To wait on several sessions at once, run each `wait` with Bash
  `run_in_background: true`; its completion notification wakes you, with the path of its output.
  There is no Monitor tool and no sleep loop: `oc_seat.sh wait` is the way to wait.
  **Never `cmd &`, `nohup`, `setsid` or any other detached process:** the host cannot see it, and
  your session may be closed while it runs.
"""
"""The runtime bullets every Claude seat shares (quick workers and helpers, dispatched workers and lead seats)."""

NATIVE_SEAT_RUNTIME = f"""\
# RUNTIME: TOP-LEVEL CLAUDE CODE SEAT (overrides the OpenCode assumptions above)

You run in the official Claude Code CLI on WSL/Linux as a top-level session that Dan's local
Claude host launched for an `oc_*` script, on Dan's Max subscription. Everything above about your
role, Workflowy and the work applies unchanged; this section adapts the harness.

- **Your session id is the `cc-…` id your first message names.** Wherever your instructions say
  "your session id" (a helper's `--parent-seat`, for example), pass exactly that id, copied, never
  retyped. `bash {OC_SEAT}` routes `cc-` ids to the Claude host (`quick --parent-seat`, `wait`,
  `info`, `socket`, `resume`); never give one to any other OpenCode tool, API or resolver.
- **Helpers, when your instructions let you start them:** quick agents run on OpenCode
  (`quick-ds41-flash`, `quick-sol61-…`) or on Claude Code (`quick-opus55-‹level›`,
  `quick-fable51-‹level›`); the start command prints the new session's id, `ses_…` or `cc-…`: copy it. You have no Agent tool: never try to
  start a native subagent.
""" + _SEAT_TOOLS_BASH_WAIT

CAST_PREAMBLE = """\
# You are a top-level Claude Code session started by `oc_seat.sh dispatch`

You are a **worker** in a program, or a **LEAD SEAT** when your first message says so, running in
the official Claude Code CLI on Dan's Max subscription: a session of your own, not an OpenCode
session and not anyone's subagent. Your session id is the `cc-…` id in your first message. Your
agent paragraph and the worker instructions below were written for OpenCode; the runtime section at
the end of this prompt adapts them, and where the two differ, the runtime section wins.
"""

NATIVE_CAST_RUNTIME = f"""\
# RUNTIME: TOP-LEVEL CLAUDE CODE WORKER (overrides the OpenCode assumptions above)

You run in the official Claude Code CLI on WSL/Linux as a top-level session that Dan's local
Claude host launched for `oc_seat.sh dispatch`, on Dan's Max subscription: a program's worker, or
a lead seat started fresh (no seat base: nothing came before your first message). Everything above
about your role, Workflowy and the work applies unchanged; this section adapts the harness.

- **Your session id is the `cc-…` id your first message names.** Wherever your instructions say
  "your session id" or "your own id" (a helper's `--parent-seat`, for example), pass exactly that
  id, copied, never retyped. `bash {OC_SEAT}` routes `cc-` ids to the Claude host (`fork` and
  `quick --parent-seat`, `wait`, `info`, `socket`, `resume`); never give one to any other OpenCode
  tool, API or resolver.
- **Helpers, only when your dispatch gives you some** (a lead seat's `helpers:` field; your seat
  clause says how): start each as your instructions say, with your own `cc-…` id as
  `--parent-seat`: forked from the helper seat base your dispatch names (`bash {OC_SEAT} fork --base
  sb-… --socket "[ap:H]" --parent-seat ‹your cc-… id› --no-wait`; seat bases and their helpers run
  on OpenCode), or as a quick helper when your dispatch says so (`bash {OC_SEAT} quick --agent
  ‹quick agent› --socket "[ap:H]" --parent-seat ‹your cc-… id› --no-wait`). The start command
  prints the helper's id, `ses_…` or `cc-…`: copy it. You have no Agent tool: never try to start a
  native subagent; no in-harness subagent of any kind is used inside a program.
""" + _SEAT_TOOLS_BASH_WAIT

QUICK_OVERRIDES = (
    (
        'native-workdir',
        "Give every shell command, file read and edit the absolute path of the repository your "
        "prompt names (the shell tool's `workdir`).",
        "Give every shell command, file read and edit the absolute path of the repository your "
        "prompt names (Claude Code's Bash has no working-directory parameter: start the command "
        "with `cd /absolute/path && …`).",
    ),
    (
        'native-workflowy-tools',
        "Read it first, in full: `node(verb='read', params={'node_handle': '[ap:SOCKET]'})`. If the "
        "Workflowy tools (`node`, `anchor`, `glimpse`, `beacon` …) are not in your tool list, reply "
        "in one line that they are unavailable and stop.",
        "Read it first, in full: `mcp__workflowy__node(verb='read', params={'node_handle': "
        "'[ap:SOCKET]'})`. If the native Workflowy tools (`mcp__workflowy__node`, "
        "`mcp__workflowy__anchor`, `mcp__workflowy__glimpse`, `mcp__workflowy__beacon` …) are not in "
        "your tool list, reply in one line that they are unavailable and stop.",
    ),
    (
        'native-grep-glob',
        "Search a named folder inside the repository, under a timeout, with the `grep` and `glob` "
        "tools (`rg --column` in the shell for a hit far into a very long line).",
        "Search a named folder inside the repository, under a timeout, with Claude Code's Grep tool, "
        "or Glob on a named subfolder (`rg --column` in Bash for a hit far into a very long line).",
    ),
    (
        'native-helper-wait',
        "3. **Collect it:** `bash …/oc_seat.sh wait ‹helper session id› --max 540` (exit 2 = still "
        "running: call it again), then read its socket's `## Result`.",
        "3. **Collect it:** `bash …/oc_seat.sh wait ‹helper session id› --max 540` in Bash with "
        "`timeout: 600000` (exit 2 = still running: call it again; for a `cc-…` helper, 4 = needs "
        "attention and 5 = UNKNOWN: read what it printed, and never treat either as done), then read "
        "its socket's `## Result`. Several helpers at once: start each `wait` with Bash "
        "`run_in_background: true` and collect each when its completion notification arrives.",
    ),
    (
        'native-context',
        "`context_status` (no arguments) gives your own window and target, measured.",
        "`mcp__context__context_status` (no arguments) gives your own window and target, measured "
        "(*Native context binding*, at the end of this prompt, says exactly what it measures).",
    ),
    (
        'native-oversized-output',
        "- **Oversized tool output** arrives as a stub naming the `outputPath` of the full output and "
        "the line to continue from: read that file from there; don't re-run the command. `read` "
        "returns at most 2,000 lines per call.",
        "- **Oversized tool output:** when a result says it was truncated or saved to a file, read the "
        "rest from that file instead of re-running the command. Claude Code's Read returns at most "
        "2,000 lines per call; continue a longer file with an offset window.",
    ),
)
"""The exact OpenCode-only passages of the quick-worker doctrine replaced for Claude Code:
(name, anchor, replacement). Each anchor must match the source exactly once."""

CAST_OVERRIDES = (
    (
        'native-workflowy-tools',
        "1. **Check that the Workflowy MCP tool `node` is in your tool list.**",
        "1. **Check that the native Workflowy MCP tool `mcp__workflowy__node` is in your tool list.**",
    ),
    (
        'native-grep-glob',
        "Prefer OpenCode's own `grep` and `glob` tools to `rg` / `grep` / `find` in the shell: built on "
        "ripgrep, they honour `.gitignore`, are case-sensitive by default, stop at 100 results with a "
        "note, and time out after 30 seconds. One weakness: `grep` cuts a very long line at 2,000 "
        "characters from its start, so a hit far into a long line (a one-line beacon comment or "
        "docstring) may not show; `rg --column` in the shell finds it.",
        "Prefer Claude Code's own Grep tool to `rg` / `grep` / `find` in Bash: built on ripgrep, it "
        "honours `.gitignore`, returns results grouped by file, with line numbers, and may search one "
        "repository's root, never `__repos` or above. Glob honours `.gitignore` and stops after 60 "
        "seconds, so it may search one repository's root too, never `__repos` or above. A "
        "hit far into a very long line (a one-line beacon comment or docstring) can be cropped in a "
        "preview; `rg --column` in Bash finds it.",
    ),
    (
        'native-context',
        "It is a native tool in your tool list (plugin-provided, beside `shell`/`read`). It reads your "
        "session's model and role and reports, measured from the server's own accounting: tokens "
        "used, your true window, percent, headroom, session cost, and a verdict against **your "
        "target** (a conductor or primary session aims to hand off at it, a worker or seat to land at "
        "it).",
        "In Claude Code it is `mcp__context__context_status`, bound to this launch. It reads your role "
        "and model and reports the input tokens of your previous completed model call, your window, "
        "the percent used and a verdict against **your target** (a conductor or primary session aims "
        "to hand off at it, a worker or seat to land at it); *Native context binding*, at the end of "
        "this prompt, says exactly what it measures.",
    ),
    (
        'native-oversized-output',
        "When a tool result exceeds the host limit, you receive a compact **stub**: total size, the "
        "`outputPath` of the full output on disk, a small sample, and the exact line to continue from. "
        "When you need the full data, **read that path** (with the given offset) — do not re-run the "
        "command that produced it.\n"
        "\n"
        "**Paging in `read`:** the `read` tool returns at most 2,000 lines per call and passes every "
        "line whole; a longer file ends with `[Output truncated. Continue reading with offset: N]`, so "
        "call `read` again from that offset. Other tools' output over the host limit (~50 KB) arrives "
        "as the stub above.",
        "When a tool result says it was truncated or saved to a file, **read that file** (Read with an "
        "offset) instead of re-running the command that produced it.\n"
        "\n"
        "**Paging in Read:** Claude Code's Read returns at most 2,000 lines per call; continue a longer "
        "file with an offset window.",
    ),
)
"""The exact OpenCode-only passages of ``agents/AGENTS.md`` replaced for Claude Code cast workers."""

RESIDUAL_OPENCODE_MARKERS = (
    ('OpenCode-owned tool guidance', re.compile(r"OpenCode['’]s own", re.IGNORECASE)),
    ('OpenCode shell workdir', re.compile(r"shell tool['’]s `workdir`")),
    ('OpenCode grep/glob tools', re.compile(r"the `grep` and `glob` tools")),
    ('OpenCode read tool', re.compile(r"`read` (?:tool|returns)")),
    ('OpenCode output stub', re.compile(r"`outputPath`")),
    ('plugin-provided context tool', re.compile(r"plugin-provided", re.IGNORECASE)),
)
"""Deliberately short watchlist of OpenCode-only mechanics; ``oc_seat.sh`` and OpenCode helpers are legitimate."""

CONDUCTOR_PREAMBLE = """\
# You are a Claude Code Conductor started by `oc_launch`

You run in the official Claude Code CLI on Dan's Max subscription: a top-level session of your own,
not an OpenCode session and not anyone's subagent, launched by Dan's local Claude host for
`oc_launch` and bound into your program's registry. The Conductor and worker instructions below
were written for OpenCode; the runtime section at the end of this prompt adapts them, and where the
two differ, the runtime section wins.
"""

NATIVE_CONDUCTOR_RUNTIME = f"""\
# RUNTIME: CLAUDE CODE CONDUCTOR (overrides the OpenCode assumptions above)

You run in the official Claude Code CLI on WSL/Linux as a top-level session that Dan's local Claude
host launched for `oc_launch`, on Dan's Max subscription. Everything above about your role, the
Toolkit, Workflowy and the program applies unchanged; this section adapts the harness.

- **Identity.** `oc_launch` named you `[ap:PROG]-NNN · …` (a label on the host: never rename
  yourself, and never call OpenCode's session API about yourself) and bound your id, `cc-…`, into
  your registry row as `execution: {{backend: claude, id: "cc-…"}}`, where an OpenCode Conductor's
  row carries `opencode_session_id`. You never pass your own id anywhere: the scripts find a
  program's current Conductor from its registry. Never give any `cc-…` id to an OpenCode tool, API
  or resolver; `bash {OC_SEAT}` routes `cc-…` ids to the Claude host itself.
- **Tools:** Bash, Read, Edit, Write, Grep, Glob, WebFetch, WebSearch and TaskStop, plus MCP
  tools. The Workflowy tools your instructions write as `node(…)`, `anchor(…)`, `glimpse(…)`,
  `beacon(…)`, `tag(…)`, `slice_group(…)`, `lightning_rod(…)` and `system(…)` are
  `mcp__workflowy__node`, `mcp__workflowy__anchor`, `mcp__workflowy__glimpse` and so on (the same
  prefix for each), with the same `{{verb, params}}` form; use each family's `describe` before
  guessing a nested payload. Every seat role now carries the same built-ins — Edit, Write, WebFetch
  and WebSearch included — so the tools are there if a job ever needs them; availability is not
  implementation authority, and the doctrine is unchanged: a Conductor never writes code itself, and
  every worker is still started by a script. You have no Agent tool and no OpenCode `subagent`,
  `shell`, `read`, `grep` or `glob` tool. If the Workflowy or context tools are missing, say so in
  one line and stop; never substitute another tool's result.
- **Bash:** paths are WSL paths (`/mnt/e/…`). There is no working-directory parameter: put
  `cd /absolute/path && …` in the same command. Give every command that may run long an explicit
  `timeout`; one call runs at most an hour (`timeout: 3600000`).
{ROOT_SEARCH_ENFORCEMENT}
- **Every worker is started by a script, never a subagent.** No in-harness subagent of any kind is
  ever used inside a program. Wherever anything you read says to dispatch a worker with OpenCode's
  `subagent` tool, or to resume one by passing its session id back to it, that route does not exist
  for you: use the scripts below. Each prints the new session's id, `ses_…` or `cc-…` (a cast row
  whose model is `anthropic/*` runs on Claude Code on Max): copy it into your state, never retype it.
  - **An ordinary worker**, on either backend, by its cast agent, after its socket's dispatch is
    written: `bash {OC_SEAT} dispatch --agent ‹cast agent› --socket "[ap:S]" --program "[ap:PROG]"
    --no-wait`. It sends the worker its one line (read the Primary Worker Clause, then the socket)
    itself.
  - **A lead seat:** the same command with `--lead` starts it fresh, with no seat base
    (`--variant ‹level›` and `--target 0.40` as your instructions give them for a fork); or fork
    it from an OpenCode seat base with `bash {OC_SEAT} create …` and `fork …`, exactly as your
    instructions describe. A Claude agent's lead seat is always fresh.
  - **Quick workers and quick helpers** on either backend:
    `bash {OC_SEAT} quick --agent ‹quick agent› --socket "[ap:S]" --program "[ap:PROG]" --no-wait`
    (OpenCode: `quick-sol61-…`, `quick-ds41-flash`; Claude Code on Max: `quick-opus55-‹level›`,
    `quick-fable51-‹level›`).
  - **A follow-up** to an idle script-started worker, in its own session: pass this run's instruction
    socket (a further job, a new flat leg, or the same interrupted piece); track and wait by session id:
    `bash {OC_SEAT} resume ‹id› --socket "[ap:S]"`.
- **Waiting: in the background, then end your turn.**
  1. Record the dispatch first, as your instructions require (ConductorState `in_flight`, or a
     quick program's `quick_state`): write-ahead, before you wait.
  2. For each in-flight worker, start `bash {OC_SEAT} wait ‹id› --max 3000` with Bash
     `run_in_background: true` and `timeout: 3600000`. Keep `--max` well under that one-hour
     ceiling (never above 3300); a longer job simply loops.
  3. **Before ending a turn, confirm that a background waiter is registered for every in-flight
     worker** (each start returns a task id; check them against your `in_flight` list) and start
     any that is missing. A worker with no waiter is a doorbell that never rings.
  4. End your turn. A waiter's completion notification wakes you with its output: exit 0 done,
     2 still running (start the next background wait and end the turn again), and for a `cc-…`
     worker 4 needs attention and 5 UNKNOWN (read what it printed; never treat either as done).
  Why: while you wait in the background, Dan's or the Manager's message reaches you at once, and
  context is spent only on wake-ups. A foreground wait is one long-running command: a message for you
  waits until it ends, so use one only for a short `--max 540` whose result you act on at once (Bash
  `timeout: 600000`). **Never `cmd &`, `nohup`, `setsid` or any
  other detached process**: the host cannot see it, nothing wakes you when it ends, and your session
  may be closed while it runs. There is no Monitor tool and no sleep loop: a pause your instructions
  ask for (a retry backoff) is a background `sleep N` whose completion wakes you. TaskStop ends a
  background task you no longer need; it never stops a worker.
- **Messages from Dan or the Manager** (`Dan rules:` / `Dan asks:`): a steer reaches a busy Claude
  session at its next tool step; one long-running command delays it until that command ends. Treat
  each exactly as your instructions say: a brief by other means.
- **Context and handoff.** `mcp__context__context_status` (no arguments) gives your own target; a
  Conductor hands off at it. Claude Code never compacts this conversation. Seat your successor as
  your instructions say, with `bash {OC_SELF_RESUME} "[ap:‹PROGRAM ROOT›]"`: it starts a new Claude
  Code Conductor with your model and thinking level (read from your registry row), under the next
  number and a new id, binds it and marks your row stopped. Then write your final message and stop.
- **Oversized output:** when a tool result says it was truncated or saved to a file, read the rest
  from that file instead of re-running the command. Read returns at most 2,000 lines per call;
  continue a longer file with an offset window.
"""

CONDUCTOR_OVERRIDES = (
    (
        'native-context',
        "Watch your fill with the context_status tool and hand off per that page before you run dry.",
        "Watch your fill with `mcp__context__context_status` (no arguments) and hand off per that page "
        "before you run dry.",
    ),
)
"""The exact OpenCode-only passage of the ``conductor`` agent body replaced for Claude Code."""

CONDUCTOR_RESIDUAL_MARKERS = RESIDUAL_OPENCODE_MARKERS + (
    ('OpenCode subagent tool', re.compile(r'`subagent`|\bsubagent tool\b', re.IGNORECASE)),
    ('bare context_status tool', re.compile(r'\bthe context_status tool\b')),
)
"""The seat watchlist plus the two mechanics a Conductor must never meet unadapted."""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def without_frontmatter(source: str, path: Path) -> str:
    match = _FRONTMATTER.match(source)
    if match is None:
        raise ValueError(f'{path} has no YAML frontmatter to strip')
    return source[match.end():]


def drift_warning(what: str, name: str, path: Path, found: int, composer: str = 'scripts/claude_headless/seat_prompt.py') -> str:
    """The loud line for one override whose anchor no longer matches its doctrine exactly once."""
    return (f'DOCTRINE DRIFT: the {what} override {name!r} was skipped: its anchor must match exactly once in {path} '
            f'(found {found}), so this prompt carries that passage in its OpenCode wording. Update the anchor in '
            f'{composer} to the doctrine\'s new wording (test_claude_doctrine_drift.py names every one).')


def apply_overrides(text: str, overrides: tuple, *, what: str, path: Path, prefix: str = '') -> tuple[str, list[str], list[str], list[str]]:
    """(text, override names applied, override names skipped, drift warnings). An anchor that is missing or repeated
    (doctrine drift) skips that one override with a loud warning; every other override still applies. A doctrine edit
    must never stop a launch: the drift test catches it at edit time instead."""
    applied, skipped, drift = [], [], []
    for name, anchor, replacement in overrides:
        found = text.count(anchor)
        if found != 1:
            skipped.append(prefix + name)
            drift.append(drift_warning(what, name, path, found))
            continue
        text = text.replace(anchor, replacement, 1)
        applied.append(prefix + name)
    return text, applied, skipped, drift


# @beacon[
#   id=seat-prompt@adapt,
#   slice_labels=opc-claude-seats,
#   kind=ast,
#   comment=One doctrine file with its exact-anchor overrides applied; an anchor that is missing or repeated (doctrine drift) skips that override with a loud DOCTRINE DRIFT warning, never a refused launch.,
# ]
def adapt(path: Path, overrides: tuple, *, what: str) -> tuple[str, list[str], str, list[str], list[str]]:
    """(adapted text, override names applied, source SHA-256, override names skipped, drift warnings) for one
    doctrine file. An empty doctrine still refuses: there is nothing to compose."""
    raw = Path(path).read_bytes()
    text = raw.decode('utf-8')
    if not text.strip():
        raise ValueError(f'{what} doctrine {path} is empty; refusing to compose a seat prompt')
    text, applied, skipped, drift = apply_overrides(text, overrides, what=what, path=path)
    return text, applied, _sha256(raw), skipped, drift


def agent_body(agent: str) -> tuple[str, str, Path]:
    """(the agent file's body without its frontmatter, the file's SHA-256, its path)."""
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*', agent or ''):
        raise ValueError(f'{agent!r} is not an agent name')
    path = AGENT_DIR / f'{agent}.md'
    if not path.is_file():
        raise ValueError(f'no agent file {path}')
    raw = path.read_bytes()
    return without_frontmatter(raw.decode('utf-8'), path).strip(), _sha256(raw), path


def residual_opencode_warnings(text: str, markers: tuple = RESIDUAL_OPENCODE_MARKERS) -> list[str]:
    warnings = []
    for label, pattern in markers:
        for match in pattern.finditer(text):
            line = text.count('\n', 0, match.start()) + 1
            warnings.append(f'Residual OpenCode-only text: {label} at adapted line {line}: '
                            f'{match.group(0)!r}; add a native override in seat_prompt.py')
    return warnings


def assemble(*, kind: str, agent: str | None, parts: list[str], runtime: str, sources: list[dict],
             overrides: list[str], strict: bool = False, markers: tuple = RESIDUAL_OPENCODE_MARKERS,
             drifted: list[str] | None = None, drift: list[str] | None = None) -> dict:
    """Join the adapted parts, scan them for residual OpenCode-only text, then append the runtime and
    the native context binding. → the composition record (see :func:`compose_quick_worker_prompt`)."""
    body = SEPARATOR.join(p.strip() for p in parts if p and p.strip())
    warnings = list(drift or []) + residual_opencode_warnings(body, markers)
    if strict and warnings:
        raise ValueError('\n'.join(warnings))
    text = body + SEPARATOR + runtime.strip() + '\n\n' + RUNTIME
    return {'text': text, 'kind': kind, 'agent': agent, 'sources': sources, 'overrides': overrides,
            'prompt_sha256': _sha256(text.encode('utf-8')), 'bytes': len(text.encode('utf-8')),
            'warnings': warnings, 'drifted': list(drifted or []), 'drift': list(drift or [])}


def drift_label(composed: dict) -> str | None:
    """The seat label naming the overrides a doctrine drift skipped (None when nothing drifted), so `oc_seat info`
    shows it beside the seat for as long as the seat lives."""
    drifted = composed.get('drifted') or []
    return ', '.join(drifted)[:2000] if drifted else None


# @beacon[
#   id=seat-prompt@compose_quick_worker_prompt,
#   slice_labels=opc-claude-seats,
#   kind=ast,
#   comment=The Claude quick worker/helper system prompt: top-level preamble · the quick agent's body · quick-worker/AGENTS.md with its OpenCode-only passages replaced · the native seat runtime · the context binding. Doctrine drift skips that override with a loud warning.,
# ]
def compose_quick_worker_prompt(agent: str, *, strict: bool = False, source: Path | None = None) -> dict:
    """The system prompt appended to Claude Code's own for one quick worker or helper.

    Returns ``{"text", "kind", "agent", "sources", "overrides", "prompt_sha256", "bytes", "warnings", "drifted",
    "drift"}``: the prompt, ``quick-worker``, the agent name, ``[{"path", "sha256"}]`` for the agent file and the
    doctrine, the override names applied, every warning (drift first, then residual OpenCode text), the override
    names a doctrine drift skipped and their DOCTRINE DRIFT lines. Raises ``ValueError`` only for a missing agent
    file or an empty doctrine (strict mode: also on any warning, drift included). Workers and helpers share it: the
    first message says which one."""
    body, body_sha, body_path = agent_body(agent)
    path = Path(source) if source else QUICK_SOURCE
    doctrine, applied, doctrine_sha, skipped, drift = adapt(path, QUICK_OVERRIDES, what='quick-worker')
    return assemble(kind='quick-worker', agent=agent, parts=[QUICK_PREAMBLE, body, doctrine],
                    runtime=NATIVE_SEAT_RUNTIME, overrides=applied, strict=strict, drifted=skipped, drift=drift,
                    sources=[{'path': str(body_path), 'sha256': body_sha},
                             {'path': str(path), 'sha256': doctrine_sha}])


# @beacon[
#   id=seat-prompt@compose_cast_worker_prompt,
#   slice_labels=opc-claude-seats,
#   kind=ast,
#   comment=The Claude system prompt of a fresh seat (oc_seat dispatch: an ordinary program worker or a fresh lead seat): top-level preamble · the cast agent's body · agents/AGENTS.md with its OpenCode-only passages replaced · the native cast runtime (cc- id, helpers forked or quick with --parent-seat cc-…) · the context binding. Doctrine drift skips that override with a loud warning.,
# ]
def compose_cast_worker_prompt(agent: str, *, strict: bool = False, source: Path | None = None) -> dict:
    """The system prompt appended to Claude Code's own for a dispatched cast worker or a fresh lead seat
    (``oc_seat dispatch``; the OpenCode equivalent: the agent body plus ``~/AGENTS.md``). Workers and lead seats share
    it: the first message says which. Same record and refusals as :func:`compose_quick_worker_prompt`."""
    body, body_sha, body_path = agent_body(agent)
    path = Path(source) if source else CAST_SOURCE
    doctrine, applied, doctrine_sha, skipped, drift = adapt(path, CAST_OVERRIDES, what='worker')
    return assemble(kind='cast-worker', agent=agent, parts=[CAST_PREAMBLE, body, doctrine],
                    runtime=NATIVE_CAST_RUNTIME, overrides=applied, strict=strict, drifted=skipped, drift=drift,
                    sources=[{'path': str(body_path), 'sha256': body_sha},
                             {'path': str(path), 'sha256': doctrine_sha}])


# @beacon[
#   id=seat-prompt@compose_conductor_prompt,
#   slice_labels=opc-claude-seats,
#   kind=ast,
#   comment=The Claude Conductor system prompt (role policy replace, so it stands alone): preamble · the conductor agent body · agents/AGENTS.md with its OpenCode-only passages replaced · the native CONDUCTOR runtime (script dispatch only, background waits then end the turn, a waiter for every in-flight worker) · the context binding. Doctrine drift skips that override with a loud warning.,
# ]
def compose_conductor_prompt(*, strict: bool = False, source: Path | None = None,
                             agent_source: Path | None = None) -> dict:
    """The whole system prompt of a Claude Code Conductor (``oc_launch``; role ``conductor``, policy ``replace``).
    The OpenCode equivalent is the ``conductor`` agent body, which replaces OpenCode's coding prompt, followed by
    ``~/AGENTS.md``. Same record and refusals as :func:`compose_quick_worker_prompt`; ``source`` / ``agent_source``
    substitute the two doctrine files (tests). The Conductor's own id is never in it: it is bound into the registry."""
    body_path = Path(agent_source) if agent_source else AGENT_DIR / f'{CONDUCTOR_AGENT}.md'
    raw = body_path.read_bytes()
    body = without_frontmatter(raw.decode('utf-8'), body_path)
    body, applied, skipped, drift = apply_overrides(body, CONDUCTOR_OVERRIDES, what='conductor', path=body_path,
                                                    prefix='conductor:')
    path = Path(source) if source else CAST_SOURCE
    doctrine, doctrine_applied, doctrine_sha, doctrine_skipped, doctrine_drift = adapt(path, CAST_OVERRIDES, what='worker')
    return assemble(kind='conductor', agent=CONDUCTOR_AGENT, parts=[CONDUCTOR_PREAMBLE, body.strip(), doctrine],
                    runtime=NATIVE_CONDUCTOR_RUNTIME, overrides=applied + doctrine_applied, strict=strict,
                    markers=CONDUCTOR_RESIDUAL_MARKERS, drifted=skipped + doctrine_skipped,
                    drift=drift + doctrine_drift,
                    sources=[{'path': str(body_path), 'sha256': _sha256(raw)},
                             {'path': str(path), 'sha256': doctrine_sha}])

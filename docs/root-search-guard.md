# Root-search guard — whole-tree searches from a root are refused

A recursive search started at a repository root or above walks everything under it: on `/mnt/e`
(DrvFs), the companion repo's gitignored `tmp/` alone holds ~3,000 entries, and such a search hangs
for 1–6 minutes. The Claude host refuses these calls at the moment they are made, for every role
it runs (Manager, Conductors, lead seats, quick workers, helpers). This page is the contract: the
host check (`scripts/claude_headless/root_search.py`), its tests and the agents' prompt wording say
exactly this, and the check passes the shared test vectors (`scripts/fixtures/root_search_vectors.json`).

**OpenCode sessions are not guarded.** The OpenCode side (a TypeScript port of the check in a
plugin, `plugins/root-search-guard.ts`) is held on branch `feat/root-search-guard-2`, not shipped
(to-do [ap:K9TT9Y]). When it ships, the port must pass these vectors as they stand here, including
every change made since that branch (the setting and the folder-of-repositories rule below among them).

## Roots

- **Fixed roots:** every folder in the **setting** and every parent of it; `$HOME` and its parents
  (`/home`, `/`); every drive root `/mnt/‹letter›` and `/mnt`.
- **Repository roots:** every git top-level, i.e. a folder holding a `.git` entry (a directory, or a
  worktree's `.git` file).
- **Folders of repositories:** a folder one of whose first three non-hidden subfolders (sorted by
  name) holds a `.git` entry directly. This needs no setting. It costs one listing of the folder and
  at most three `.git` lookups, made only when a search starts there (the same moment the folder's
  own `.git` lookup is made); a listing that fails (any `OSError`) means "not a root". It is the one
  rule that does not wait for certainty: a false positive is accepted (Dan's ruling).

**The setting** is a list of extra root folders, empty by default:
`config/claude-headless-root-search.json` (`{"extra_roots": [...]}`, beside the host's other
settings in `config/`), or the environment variable `ROOT_SEARCH_GUARD_ROOTS` (folders separated by
`:`), which replaces the file's list when set. A leading `~` is the home folder; a relative entry is
ignored; an unreadable or malformed file gives no extra roots. The host reads it once (a change takes
effect at its next restart); host health shows it (`root_search_hook.extra_roots`). Dan's file lists
`/mnt/e/__daniel347x/__Dan_Root/__repos`.

A subfolder of a repository is never a root, however large. Paths are compared after `~`
expansion, resolution against the tracked folder (below) and `.`/`..`/trailing-slash
normalization; symlinks are not resolved. `E:\…` and `E:/…` mean `/mnt/e/…`.

## What counts as a root search

| Kind | Commands and tools | Refused at |
|---|---|---|
| **Whole-tree walk** (ignores `.gitignore`) | shell `grep`/`egrep`/`fgrep` with `-r`, `-R`, `--recursive`, `--dereference-recursive` or `-d recurse`, and `rgrep`; `find`; `ls -R`; `tree`; `du`; `ack`; `rg`/`fd`/`ag` with ignore handling off (`rg -u`/`-uu`/`-uuu`, `--unrestricted`, `--no-ignore`, `--no-ignore-vcs`; `fd -I`/`-u`/`--no-ignore`/`--no-ignore-vcs`; `ag -u`/`-U`/`-a`); Claude Code's **Glob** in a session whose settings do not make it honour `.gitignore` (below) | any root |
| **Ignore-aware search** (honors `.gitignore`) | Claude Code's **Grep** tool, and its **Glob** tool where the session's settings make it honour `.gitignore`; OpenCode's `grep` and `glob` tools (ripgrep, 30 s timeout); shell `rg`, `fd`, `ag` | a folder above repositories only (a fixed root or a folder of repositories): **one repository root is allowed** |

**Claude Code's Glob.** By default it walks ignored folders. Every fresh host launch sets
`CLAUDE_CODE_GLOB_NO_IGNORE=false` in the session's settings (`launch.FORCED_PROFILE_ENV`), which
makes it honour `.gitignore`, and the host profile `config/claude-headless-settings.json` stops it
after 60 s (`CLAUDE_CODE_GLOB_TIMEOUT_SECONDS`). A resume keeps the settings its session was launched
with, which may predate the first. So the host records, per generation, whether its settings say
`CLAUDE_CODE_GLOB_NO_IGNORE=false` (`glob_honours_gitignore` in `state`), and judges Glob by it: where
they do, a Glob at a repository root lists that repository's files quickly and is allowed like Grep,
and one at a folder above repositories lists every repository and may run its full 60 s; where they
do not, Glob is a whole-tree walk, refused at any root.

A shallow depth limit is not a whole-tree walk and is allowed anywhere: `find -maxdepth` 0–2,
`tree -L` 1–2, `rg`/`fd --max-depth` 0–2. `git grep` and `git ls-files` are not walks; neither are
`rg --type-list`, `rg -V` and `rg -h`, which print and exit.

**Starting paths.** A tool's `path`; with none, the folder the session stands in (an absolute Glob
`pattern` starts at its literal prefix). A shell command's path operands; with none, the current
folder. An operand `*` or `dir/*` counts as `dir` itself: the shell expands it to every top-level
entry. Other globs (`scripts/*.py`) and files are not roots.

**The folder a shell command starts in** (so `.` and other relative operands can be judged):

- **Claude Code** keeps one shell folder per session: it starts in the launch folder and a `cd`
  persists while it stays inside the session's working directories (Claude Code resets it to the
  launch folder otherwise). The host never models this. Its `PreToolUse` hook receives the folder
  from Claude Code itself (the hook input's `cwd`) before the call runs. That folder is **known**
  unless another main-thread Bash call of the session is still running, since that call may still
  move the shell before this one starts. In that case it is unknown. A session launched in a
  repository root therefore has `grep -rn foo .` refused, and the same command after `cd scripts`
  allowed.
- **OpenCode** (the vectors' OpenCode shapes, for the held port) starts every `shell` call as a
  fresh process in its `workdir` (relative to the session folder; `~` is the home folder), else in
  the session folder, so the folder is always known.

**Pipes and `xargs`.** An `rg`, `ag` or `ack` with no path operand after a `|` reads its input, not
the tree. A `grep -r` with no file operand does **not**: GNU grep then searches the current folder
whatever its input, so `git ls-files | grep -rn foo` at a root is a root search. Under `xargs`, any
of them gets its operands from the input and is not judged as a walk of the current folder.

## Chained commands: the whole command is judged before anything runs

1. A quote-aware lexer (not one regex) splits the command into pieces at `;`, `&&`, `||`, `|`, `|&`,
   `&` and newlines. Quoted text, comments and heredoc bodies are data.
2. The pieces are walked in order, tracking the folder: `cd`/`pushd`/`popd`, and `( … )` subshells
   restore it. Every `cd` is assumed to succeed, but a `cd` that may not run leaves the folder
   unknown: one after `||`, one inside an `if`/`case` branch or a loop, one in a function body, and
   any `cd` in an and/or list run in the background with `&` (it moves only that background shell).
   A `cd` in a plain `{ … }` group is certain, unless the group follows `||`.
3. Each piece is judged, and so is every command it contains: `bash -c '…'`/`sh -c '…'`/`eval`,
   `$(…)`, backticks and `<(…)`, behind wrappers (`timeout`, `env`, `nice`, `nohup`, `time`,
   `command`, `exec`, `sudo`, `stdbuf`, `xargs`).
4. **If any piece is a root search, the whole command is refused**, so nothing in it runs.

So `cd /mnt/e/…/opencode-companion && git status; grep -rn foo .` **is** a root search: the root
is never typed, but `.` is the repository root. `cd X/scripts 2>/dev/null || cd X; grep -rn foo .`
is not judged (bash may be in `scripts/`), and `cd X || exit 1; grep -rn foo .` is refused.

## Refuse only when certain

Whatever cannot be known without running the command is let through (the folder-of-repositories
rule above is the one exception):

- **When the starting folder is unknown** (a Claude Code permission request, which carries no
  folder, or a hook call while another Bash runs), relative operands before an absolute `cd` (or
  `cd ~`) are unknown: a bare `grep -r foo .` is then allowed, and so is a Grep or Glob with a
  relative `path` or none.
- `cd "$DIR"`, `cd $(…)`, `cd -`, a relative `cd` from an unknown folder, `popd` past the stack, and
  the uncertain `cd`s above: the folder is unknown from there on.
- Operands holding `$VAR`, `$(…)` or backticks; scripts (`bash x.sh`, `python …`).
- Any doubt in the lexer (an unterminated quote, an unbalanced `$(`) or an error in the check: the
  call is allowed. A parser error never refuses a call.

## The refusal

The message names the refused piece, says why in one clause (a whole-tree walk at the root of X,
which ignores `.gitignore`; or a search across every repository under a folder above them), and
what to do instead: the root's top-level folders (excluding `tmp`, `node_modules`, `.git` and
hidden folders) to name instead, and, only at a repository root, the Grep tool (which may search
one repository and is refused above them). The model sees it as the tool's error, and nothing in
the call runs.

**Claude Code: every call reaches the host.** Claude Code approves some calls itself without
asking (read-only commands and Grep/Glob calls inside the session's working directory), so the host
does not rely on permission requests. Every launch's `initialize` registers the host's own
`PreToolUse` hook for `Bash|Grep|Glob`. Claude Code sends every such call to it as a
`hook_callback` before any permission check, with the folder the shell stands in. A root search is
answered `permissionDecision: "deny"` with the message; any other call gets no opinion, and the
normal permission path follows without judging that call again. A permission request for a call
the hook did not see is still judged, with the starting folder unknown, and denied with
`{"behavior":"deny","message":"…"}`. Every decision is answered at once and recorded with the
permissions: the `permissions` row (`kind: "hook_callback"` for the hook) has `"behavior": "deny"`
and a `root_search` record (tool, piece, root, root kind, walk kind), and the `hook_decision` /
`permission_decision` event carries the same record ([host](claude-headless-host.md)).

**Nothing hangs.** The host answers each callback within milliseconds. Claude Code waits at most the
hook's 30 s `timeout` for an answer: a host that stopped answering that long (a blocked event loop,
or a storage failure that withholds its answers) would make each Bash, Grep and Glob call fail with
Claude Code's error "PreToolUse hook did not respond before its timeout": the call does not run, the
model sees the error and the turn goes on.

## Is it still working? (after a Claude Code update)

The hook rests on Claude Code behaviour that is not documented: it must call the hook for every
Bash/Grep/Glob call, report the shell's folder (`cwd`), and honour a deny. Every generation checks
all three as it runs (its **witness**), so an update that breaks one is noticed at the first affected
call:

- **missing callback:** a main-thread Bash, Grep or Glob call ran (its result is not an error)
  without the hook having answered for it;
- **cwd missing:** a hook call arrived with no folder while no other main-thread Bash was running;
- **deny ignored:** a call the hook denied ran anyway.

Each is flagged once per generation as a `root_search_hook` incident with an escalation (to Dan for
a Manager, to the owning Conductor for a seat, so `oc_seat wait` reports it as needing attention),
naming the Claude Code version; nothing is refused, delayed or held because of it. The session's
`state` carries the counts (`root_search_hook`: `seen_answering`, `answered`, `denied`,
`results_checked`, the three conditions and `flags`).

**The check after an update:** start any Claude session (or wait for one), let it make one Bash,
Grep or Glob call, then read host health:

```bash
printf '{}' | bash /mnt/e/__daniel347x/__Dan_Root/__repos/opencode-companion/scripts/claude_host.sh call health
```

`root_search_hook.by_claude_code_version["‹version›"]` must show `seen_answering` equal to
`generations` and `flagged: 0`; `root_search_hook.flagged` names any generation that raised a flag,
and `extra_roots` shows the setting in force. A hook that stopped working leaves the
permission-answer check (calls outside the session's own folder) still refusing root searches.

The Glob setting (above) has no witness, and Claude Code could rename it. To check it after an
update, from a freshly launched session, Glob a pattern in a scratch git repository whose
`.gitignore` lists a folder holding a matching file: the file must not be listed.

## Test vectors

`scripts/fixtures/root_search_vectors.json` lists commands and tool calls with the expected
`refuse`/`allow`, the root a refusal names, and why. They include the real incidents of the
investigation [ap:11HR3P], Manager-Session-220's command, a session launched in a repository root,
the setting, folders of repositories, OpenCode's tool shapes (for the held port), and a few exact
messages. The environment they assume (a neutral layout: the home folder, the setting's extra roots,
which folders are repositories, what a listing returns, which listings fail) is part of the file, so
the vectors run anywhere. `scripts/test_root_search_guard.py` runs them against the Python check; the
held TypeScript port must pass every one of them before it ships.

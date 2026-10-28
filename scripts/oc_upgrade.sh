#!/usr/bin/env bash
# oc_upgrade.sh — upgrade OpenCode safely with respect to OUR tooling (plugins, seats, config, proxy).
#
#   bash scripts/oc_upgrade.sh --check [VERSION]   # contract check only; changes nothing
#   bash scripts/oc_upgrade.sh [VERSION]           # check → upgrade → restart → smoke test
#   bash scripts/oc_upgrade.sh --rollback          # back to the version before the last upgrade (same major only)
#   --force                                        # upgrade even though a contract check failed
#
# VERSION defaults to the newest vX.Y.Z tag of the running major version (anomalyco/opencode). From Windows:
#   wsl bash -lc "bash /mnt/e/__daniel347x/__Dan_Root/__repos/opencode-companion/scripts/oc_upgrade.sh --check"
#
# What it protects. `opencode upgrade` swaps only the binary (~/.opencode/bin/opencode); our config, plugins and
# agent files live in ~/.config/opencode and restart_oc_server.sh re-installs them from this repo. The risk is the
# CONTRACT our plugins and seat machinery rely on inside OpenCode, so before upgrading this script:
#   1. makes a blob-filtered, sparse clone of the target tag (~/diagnostics/opencode-src/opencode-vX), the
#      version-matched source the Manager reads ([ap:JCTC02]); the sparse set includes packages/ai/src, because the
#      model-effort chain lives there and a core-only clone is not a usable candidate source;
#   2. checks the loci we depend on (✅/❌; any ❌ refuses the upgrade unless --force):
#        · the guard (tool-output-stub): tool.ts triggers execute.after and returns the hook's result, and
#          tool-output.ts leaves a result alone when metadata.truncated is set
#        · the tool names the guard lists: "shell" (tail stub) and "read" (exempt)
#        · the plugin API our plugins call: tool.hook("execute.after") and tool.transform, session.get and
#          session.context, model.list, mcp.transform
#        · seats: a fork's prompt-cache key is its source's (session/affinity.ts), so top-level bases share it;
#          plugins/chatgpt-cache-affinity.ts copies the same rule
#        · mcp-fetch-timeout: mcp/oauth.ts (the MCP transport's fetch) is loaded only by dynamic import, so the
#          global fetch it captures is the plugin's wrapper (installed at plugin setup, before the first MCP connect);
#          a static import would capture Bun's own fetch first and bring back the ~6-minute limit on silent MCP calls.
#          The live proof is scripts/oc_mcp_silence_test.py 600 (survives with the plugin, dies without)
#        · the ChatGPT plan's prompt cache (plugins/chatgpt-cache-affinity.ts): the "Sign in with ChatGPT" route still
#          sends model calls to api.openai.com (provider/chatgpt.ts), and the session hook "model.request" still hands
#          plugins the request's headers and base URL (plugin/src/promise/session.ts). OpenAI routes this route's
#          prompt cache by the `session-id` header, which OpenCode sends itself only for the Codex logins; our plugin
#          sets it to the session's affinity. The live proof is scripts/oc_cache_check.py (turns after the first, and
#          every fork's first turn, read from the cache)
#        · openai-usage-tap: the session hook "http.response" (the tap's view of every plan call)
#   2b. runs the §9 dependency contract (scripts/oc_upgrade_checks.py) in the SAME contract section: reference-resolved
#      OpenAPI shape checks for session identity/prompt/model/history/active/inbox/fork and token counters, plus named
#      SOURCE assertions for the fork boundary, projector copying, instruction inheritance, affinity request path,
#      plugin hook fields, the ChatGPT route and the whole model-effort chain (core + ai + our proxy fold). SOURCE is
#      not candidate-runtime execution: the labels and the per-check limit lines say what --check cannot prove; live
#      proof stays the smoke test, scripts/oc_cache_check.py and scripts/oc_mcp_silence_test.py. Missing or changed
#      shapes/blocks are review alarms with the existing exit-4/--force policy, never an exact-version gate.
#   3. shows how much the key files changed since the version we run (when that clone exists);
# then (not --check) refuses if any session is running, upgrades (`opencode upgrade VERSION --method curl`), restarts
# through restart_oc_server.sh (whose config check runs on the NEW binary, so a plugin that no longer loads refuses
# the restart and the service keeps running), and smoke-tests the version the binary and the server report.
# Every upgrade is appended to ~/.local/state/opencode-upgrade/history.tsv (for --rollback).
#
# Clone ≠ binary: a passing check is strong evidence, not proof; the smoke test and the first real use are the proof.

set -u
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BIN="$HOME/.opencode/bin/opencode"
SRC_ROOT="$HOME/diagnostics/opencode-src"
STATE="$HOME/.local/state/opencode-upgrade"
HISTORY="$STATE/history.tsv"
UPSTREAM="https://github.com/anomalyco/opencode.git"
SPARSE="packages/ai/src packages/core/src packages/plugin/src packages/server/src packages/cli/src packages/protocol packages/tui/src"
# What the §9 checks need on disk before they can say anything honest. An existing core/src alone is NOT enough:
# packages/ai/src carries the effort pipeline, and protocol/openapi.json is the schema the contract is checked against.
REQUIRED_SOURCES="packages/ai/src packages/core/src packages/protocol/openapi.json"

CHECK_ONLY=0; ROLLBACK=0; FORCE=0; TARGET=""
for a in "$@"; do
  case "$a" in
    --check) CHECK_ONLY=1 ;;
    --rollback) ROLLBACK=1 ;;
    --force) FORCE=1 ;;
    -h|--help) sed -n '2,/^$/p' "$0"; exit 0 ;;
    v*|[0-9]*) TARGET="${a#v}" ;;
    *) echo "unknown argument: $a"; exit 2 ;;
  esac
done

say() { printf '%s\n' "$*"; }
ok() { printf '  ✅ %s\n' "$*"; }
bad() { printf '  ❌ %s\n' "$*"; FAILS=$((FAILS+1)); }
note() { printf '  ⚠️  %s\n' "$*"; }
FAILS=0

current="$("$BIN" --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+[^[:space:]]*' | head -1)"   # prints "opencode vX.Y.Z"
[ -n "$current" ] || { say "cannot run $BIN --version"; exit 1; }
major="${current%%.*}"

busy_check() {
  local st
  st="$("$BIN" api get /api/session/active 2>/dev/null | tr -d '[:space:]' || true)"
  if [ -z "$st" ]; then
    say "⛔ cannot read /api/session/active from the service; not upgrading blind (is it running? opencode service status)"
    exit 3
  fi
  if [ "$st" != '{"data":{}}' ]; then
    say "⛔ sessions are running: $st"
    say "   Upgrade only when nothing is running (the boom, or /api/session/active returning no sessions)."
    exit 3
  fi
}

restart_and_smoke() {
  local want="$1" v info
  say ""; say "── restart (restart_oc_server.sh: config check on the new binary, install, service restart, MCP) ──"
  bash "$REPO/scripts/restart_oc_server.sh" || { bad "restart script refused or failed (output above); the service was not replaced"; return 1; }
  say ""; say "── smoke test ──"
  v="$("$BIN" --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+[^[:space:]]*' | head -1)"
  [ "$v" = "$want" ] && ok "binary is $v" || bad "binary is '$v', wanted $want"
  info="$("$BIN" api get /api/info 2>/dev/null | tr -d '[:space:]' || true)"
  echo "$info" | grep -q "\"version\":\"$want\"" && ok "the service reports $want" || bad "/api/info: ${info:-no answer}"
  return 0
}

# ───────────────────────────── rollback ─────────────────────────────
if [ "$ROLLBACK" = 1 ]; then
  [ -s "$HISTORY" ] || { say "no upgrade history at $HISTORY — nothing to roll back to"; exit 1; }
  prev="$(awk -F'\t' -v m="$major" '$4=="upgrade" && $3 ~ "^"m"\\." && $2 ~ "^"m"\\." {p=$2} END{print p}' "$HISTORY")"
  [ -n "$prev" ] || { say "no earlier $major.x version in $HISTORY — a rollback never crosses a major version"; exit 1; }
  say "Rolling back: $current → $prev"
  busy_check
  mkdir -p "$STATE"
  "$BIN" upgrade "$prev" --method curl > "$STATE/upgrade.log" 2>&1 || { tail -20 "$STATE/upgrade.log"; say "❌ opencode upgrade $prev failed"; exit 1; }
  printf '%s\t%s\t%s\trollback\n' "$(date -Is)" "$current" "$prev" >> "$HISTORY"
  restart_and_smoke "$prev"
  [ "$FAILS" = 0 ] && say "✅ rolled back to $prev" || say "⚠️ rolled back, but $FAILS smoke check(s) failed"
  exit $(( FAILS > 0 ))
fi

# ───────────────────────────── target ─────────────────────────────
if [ -z "$TARGET" ]; then   # this major's versions are git tags (vX.Y.Z); the GitHub releases list may hold another line
  TARGET="$(git ls-remote --tags "$UPSTREAM" "v$major.*" 2>/dev/null | awk '{print $2}' | sed 's#refs/tags/v##; s#\^{}##' \
    | grep -E '^[0-9]+\.[0-9]+\.[0-9]+$' | sort -uV | tail -1)"
  [ -n "$TARGET" ] || { say "could not read the newest $major.x tag from $UPSTREAM; pass a VERSION"; exit 1; }
fi
say "OpenCode: running $current · target $TARGET"
if [ "$TARGET" = "$current" ] && [ "$CHECK_ONLY" = 0 ]; then
  say "Already on $current. (Use --check to re-run the contract check.)"; exit 0
fi

# ───────────────────────────── source clone ─────────────────────────────
missing_sources() {
  local dir="$1" f missing=""
  for f in $REQUIRED_SOURCES; do
    [ -e "$dir/$f" ] || missing="$missing $f"
  done
  printf '%s' "${missing# }"
}

source_ready() {
  [ -n "$1" ] && [ -z "$(missing_sources "$1")" ]
}

ensure_clone() {
  local ver="$1" dir="$SRC_ROOT/opencode-v$1"
  if source_ready "$dir"; then echo "$dir"; return 0; fi
  if [ -d "$dir/.git" ]; then
    say "  completing existing clone $dir (missing: $(missing_sources "$dir"))" >&2
    if git -C "$dir" sparse-checkout add $SPARSE >/dev/null 2>&1 \
       || git -C "$dir" sparse-checkout set $SPARSE >/dev/null 2>&1; then
      if source_ready "$dir"; then echo "$dir"; return 0; fi
    fi
    say "  ❌ clone of v$ver is still missing $(missing_sources "$dir") — the candidate source is incomplete; not checking a partial tree" >&2
    return 1
  fi
  mkdir -p "$SRC_ROOT"
  say "  cloning v$ver (sparse: ai/core/plugin/server/cli/protocol/tui, blob-filtered) → $dir" >&2
  git clone -q --filter=blob:none --no-checkout --depth 1 --branch "v$ver" "$UPSTREAM" "$dir" >&2 \
    && git -C "$dir" sparse-checkout set $SPARSE >&2 \
    && git -C "$dir" checkout -q >&2 \
    || { say "  ❌ clone of v$ver failed" >&2; return 1; }
  if ! source_ready "$dir"; then
    say "  ❌ clone of v$ver is missing $(missing_sources "$dir") — the candidate source is incomplete" >&2
    return 1
  fi
  echo "$dir"
}
say ""; say "── source clone ──"
NEW="$(ensure_clone "$TARGET")" || exit 1
say "  target source: $NEW"
OLD="$SRC_ROOT/opencode-v$current"; [ -d "$OLD/packages/core/src" ] || OLD=""

# ───────────────────────────── contract check ─────────────────────────────
P="$NEW/packages"
say ""; say "── contract check against v$TARGET (what our plugins and seats rely on) ──"

grep -q 'hooks.trigger("tool", "execute.after"' "$P/core/src/tool.ts" 2>/dev/null \
  && grep -q 'afterEvent.result.output' "$P/core/src/tool.ts" 2>/dev/null \
  && ok "guard: tool.ts triggers execute.after and returns the hook's result" \
  || bad "guard: tool.ts no longer returns what execute.after leaves in the result — re-read core/src/tool.ts"
grep -q 'metadata?.truncated !== undefined' "$P/core/src/tool-output.ts" 2>/dev/null \
  && ok "guard: tool-output.ts leaves a result alone when metadata.truncated is set (the stub survives)" \
  || bad "guard: tool-output.ts no longer skips metadata.truncated results — OpenCode may truncate the guard's output again"
grep -q 'export const name = "shell"' "$P/core/src/tool/plugin/shell.ts" 2>/dev/null \
  && ok 'tool "shell" (the guard tail-stubs it)' || bad 'the shell tool is no longer named "shell" — update TAIL_TOOLS in plugins/tool-output-stub.ts'
grep -q 'export const name = "read"' "$P/core/src/tool/plugin/read.ts" 2>/dev/null \
  && ok 'tool "read" (the guard exempts it)' || bad 'the read tool is no longer named "read" — update EXEMPT_TOOLS in plugins/tool-output-stub.ts'

grep -q '"execute.after"' "$P/plugin/src/promise/tool.ts" 2>/dev/null && grep -q 'readonly transform' "$P/plugin/src/promise/tool.ts" 2>/dev/null \
  && ok "plugin API: tool.hook(\"execute.after\") and tool.transform" || bad "plugin API: the tool domain changed (plugin/src/promise/tool.ts)"
grep -q '"context"' "$P/plugin/src/promise/session.ts" 2>/dev/null && grep -q '"get"' "$P/plugin/src/promise/session.ts" 2>/dev/null \
  && ok "plugin API: session.get and session.context (context_status)" || bad "plugin API: session.get/context changed (plugin/src/promise/session.ts) — context_status"
grep -q 'list(providerID' "$P/plugin/src/promise/model.ts" 2>/dev/null \
  && ok "plugin API: model.list (context_status's window)" || bad "plugin API: model.list changed (plugin/src/promise/model.ts) — context_status"
grep -q 'readonly transform' "$P/plugin/src/promise/mcp.ts" 2>/dev/null \
  && ok "plugin API: mcp.transform (mcp-fetch-timeout's server list)" || bad "plugin API: mcp.transform changed (plugin/src/promise/mcp.ts)"

grep -q 'session.parentID ?? session.fork?.sessionID ?? session.id' "$P/core/src/session/affinity.ts" 2>/dev/null \
  && ok "seats and chatgpt-cache-affinity: a fork's prompt-cache key is its source's, a subagent's its parent's" \
  || bad "seats: session/affinity.ts changed — re-check that forks still share their base's prompt-cache key, and update the copy of the rule in plugins/chatgpt-cache-affinity.ts"

if grep -q 'import("./oauth.js")' "$P/core/src/mcp/client.ts" 2>/dev/null \
   && ! grep -rqE '^import .*from "(\./|\.\./mcp/)oauth(\.js)?"' "$P/core/src" 2>/dev/null; then
  ok "mcp-fetch-timeout: mcp/oauth.ts is loaded only by dynamic import, after plugin setup (the wrapper reaches MCP)"
else
  bad "mcp-fetch-timeout: mcp/oauth.ts may now load before plugins (static import, or client.ts changed): MCP calls"
  note "  silent > ~6 min may lose their reply again. Run scripts/oc_mcp_silence_test.py 600 against the new binary"
fi

CHATGPT="$P/core/src/plugin/provider/chatgpt.ts"
SESSION_API="$P/plugin/src/promise/session.ts"
if grep -qF '"chatgpt-token-sharing"' "$CHATGPT" 2>/dev/null \
   && grep -qF 'const resource = "https://api.openai.com/v1"' "$CHATGPT" 2>/dev/null \
   && grep -qF 'baseURL: resource' "$CHATGPT" 2>/dev/null; then
  ok 'ChatGPT plan: "Sign in with ChatGPT" still sends model calls to api.openai.com (where chatgpt-cache-affinity acts)'
else
  bad 'ChatGPT plan: the "Sign in with ChatGPT" route changed (core/src/plugin/provider/chatgpt.ts): check its base URL'
  note "  against plugins/chatgpt-cache-affinity.ts, which sets the session-id header only on requests bound for api.openai.com."
fi
if grep -qF '"model.request": SessionModelRequest' "$SESSION_API" 2>/dev/null \
   && grep -qF 'headers: Record<string, string>' "$SESSION_API" 2>/dev/null \
   && grep -qF 'baseURL?: string' "$SESSION_API" 2>/dev/null; then
  ok 'plugin API: session.hook("model.request") with the request headers and base URL (chatgpt-cache-affinity)'
else
  bad 'plugin API: the "model.request" session hook changed (plugin/src/promise/session.ts): chatgpt-cache-affinity'
  note "  may no longer set the session-id header, and the plan's prompt cache would then hit only by luck."
fi
grep -qF 'session-id' "$CHATGPT" 2>/dev/null \
  && note 'provider/chatgpt.ts mentions session-id now: OpenCode may send it on this route itself. chatgpt-cache-affinity leaves an existing header alone; confirm with scripts/oc_cache_check.py before retiring it'
grep -qF '"http.response": SessionHttpResponse' "$SESSION_API" 2>/dev/null \
  && ok 'plugin API: session.hook("http.response") (openai-usage-tap)' \
  || bad 'plugin API: the "http.response" session hook changed (plugin/src/promise/session.ts): openai-usage-tap'

say ""; say "── dependency contract (§9): reference-resolved schema + SOURCE checks (not candidate execution) ──"
CHECKER="$REPO/scripts/oc_upgrade_checks.py"
if [ ! -f "$CHECKER" ]; then
  bad "dependency contract: $CHECKER is missing — the §9 checks cannot run"
elif ! command -v python3 >/dev/null 2>&1; then
  bad "dependency contract: python3 not found — the §9 checks cannot run"
else
  CHECK_OUT="$(python3 "$CHECKER" --packages "$P" --repo "$REPO" 2>&1)"; CHECK_RC=$?
  printf '%s\n' "$CHECK_OUT" | grep -v '^__oc_upgrade_fails__=' || true
  CHECK_FAILS="$(printf '%s\n' "$CHECK_OUT" | sed -n 's/^__oc_upgrade_fails__=//p' | tail -1)"
  if [ "$CHECK_RC" -ne 0 ] || [ -z "$CHECK_FAILS" ]; then
    bad "dependency contract: the check helper did not complete (exit $CHECK_RC) — run python3 scripts/oc_upgrade_checks.py --packages \"$P\""
  else
    FAILS=$((FAILS + CHECK_FAILS))
  fi
fi

if [ -n "$OLD" ] && [ "$OLD" != "$NEW" ]; then
  say ""; say "── changed since v$current (lines of diff in the files we depend on, §9 loci included) ──"
  for f in core/src/tool.ts core/src/tool-output.ts core/src/tool/plugin/shell.ts core/src/tool/plugin/read.ts \
           core/src/tool/plugin/subagent.ts core/src/session/affinity.ts core/src/model.ts core/src/mcp/client.ts \
           core/src/mcp/oauth.ts core/src/plugin/provider/openai.ts core/src/plugin/provider/chatgpt.ts \
           plugin/src/promise/tool.ts plugin/src/promise/session.ts plugin/src/promise/mcp.ts \
           protocol/openapi.json protocol/src/groups/session.ts core/src/session.ts core/src/session/projector.ts \
           core/src/session/instruction-state.ts core/src/session/model-request.ts \
           core/src/session/runner/to-llm-message.ts ai/src/route/client.ts ai/src/effort-updates.ts \
           ai/src/protocols/openai-responses.ts ai/src/protocols/open-responses.ts; do
    if [ -f "$OLD/packages/$f" ] && [ -f "$P/$f" ]; then
      n="$(diff -u "$OLD/packages/$f" "$P/$f" 2>/dev/null | grep -cE '^[+-][^+-]')"
      printf '  %-44s %s\n' "$f" "$([ "$n" = 0 ] && echo unchanged || echo "$n lines changed")"
    elif [ -f "$P/$f" ]; then
      printf '  %-44s %s\n' "$f" "not in v$current (new locus)"
    elif [ -f "$OLD/packages/$f" ]; then
      printf '  %-44s %s\n' "$f" "removed in v$TARGET (relocated-locus review)"
    fi
  done
fi

say ""
if [ "$FAILS" -gt 0 ]; then
  say "❌ $FAILS contract check(s) failed for v$TARGET."
  if [ "$CHECK_ONLY" = 1 ] || [ "$FORCE" = 0 ]; then
    say "   Not upgrading. Read the failing file(s) in $P, adjust our plugin/config, or re-run with --force."
    exit 4
  fi
  say "   --force given: continuing anyway."
else
  say "✅ contract holds for v$TARGET."
fi
[ "$CHECK_ONLY" = 1 ] && { say "(--check: nothing changed.)"; exit 0; }

# ───────────────────────────── upgrade ─────────────────────────────
busy_check
mkdir -p "$STATE"
say ""; say "── upgrade $current → $TARGET ──"
"$BIN" upgrade "$TARGET" --method curl > "$STATE/upgrade.log" 2>&1 || { tail -20 "$STATE/upgrade.log"; say "❌ opencode upgrade failed; nothing restarted (log: $STATE/upgrade.log)"; exit 1; }
say "  upgraded (installer log: $STATE/upgrade.log)"
printf '%s\t%s\t%s\tupgrade\n' "$(date -Is)" "$current" "$TARGET" >> "$HISTORY"
FAILS=0
restart_and_smoke "$TARGET"
say ""
if [ "$FAILS" = 0 ]; then
  say "✅ OpenCode $current → $TARGET, restarted, smoke test clean."
else
  say "⚠️ upgraded to $TARGET but $FAILS smoke check(s) failed."
fi
say "   Roll back: bash $REPO/scripts/oc_upgrade.sh --rollback   (→ $current)"
say "   Manager: update [ap:JCTC02] (the source clone, now $NEW) if this version is kept."
exit $(( FAILS > 0 ))

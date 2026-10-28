#!/usr/bin/env bash
# oc_self_resume.sh — a CONDUCTOR seats its own successor at the context threshold ([ap:QE4RXD] §7 · [ap:O3EZZY]).
#
#   bash /mnt/e/__daniel347x/__Dan_Root/__repos/opencode-companion/scripts/oc_self_resume.sh "[ap:PROG]" [--dry-run] [extra oc_launch flags]
#
# Runs from WSL (where the conductor lives). Wraps `oc_launch.py --self-resume`:
#   • picks a Python that has fastmcp (the nexus repo's .venv-wsl, else $OC_PY, else python3)
#   • points the Workflowy client at the Windows-side MCP daemon (dial host parsed from ~/.config/opencode/opencode.json;
#     bearer token from the Windows broker_token file, or WORKFLOWY_BROKER_TOKEN/_FILE if already set)
#   • hands everything else to oc_launch: the newest bound registry row is the caller; it may be busy; authority, briefs
#     and model/variant default to that row's; a new row NNN+1 is written, the session created with the fixed title,
#     the fixed ignition sent ("Resuming from [ap:PROG] …"), the sid bound, and the caller's row marked stopped · handed off.
# Prints oc_launch's LAUNCHED block. Exit 0 = the successor is running. Non-zero = nothing was launched; fall back to
# printing the paste line for the human ([ap:QE4RXD] §7 fallback).
set -euo pipefail
HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
[ $# -ge 1 ] || { echo "usage: oc_self_resume.sh \"[ap:PROG]\" [--dry-run] [oc_launch flags]" >&2; exit 2; }

# --- interpreter with fastmcp
PY=""
for cand in "${OC_PY:-}" \
            /mnt/e/__daniel347x/__Dan_Root/__repos/workflowy-nexus-mcp-tools/.venv-wsl/bin/python \
            python3; do
  [ -n "$cand" ] || continue
  if "$cand" -c 'import fastmcp' >/dev/null 2>&1; then PY="$cand"; break; fi
done
[ -n "$PY" ] || { echo "no Python with fastmcp found (set OC_PY)" >&2; exit 3; }

# --- Workflowy daemon dial host: from OpenCode's own MCP config (the same URL the conductor's workflowy tools use)
if [ -z "${WORKFLOWY_MCP_HTTP_DIAL_HOST:-}" ]; then
  HOST="$("$PY" - <<'EOF'
import json, os
from urllib.parse import urlparse
try:
    cfg = json.load(open(os.path.expanduser('~/.config/opencode/opencode.json')))
    wf = cfg.get('mcp', {}).get('servers', {}).get('workflowy', {})
    u = urlparse(wf.get('url', '')); print(u.hostname or '127.0.0.1')
except Exception:
    print('127.0.0.1')
EOF
)"
  export WORKFLOWY_MCP_HTTP_DIAL_HOST="$HOST"
fi

# --- bearer token: the Windows broker_token file (visible through /mnt/c) unless already supplied
if [ -z "${WORKFLOWY_BROKER_TOKEN:-}" ] && [ -z "${WORKFLOWY_BROKER_TOKEN_FILE:-}" ]; then
  TOK="$(ls /mnt/c/Users/*/AppData/Local/workflowy-mcp/broker_token 2>/dev/null | head -1 || true)"
  [ -n "$TOK" ] || { echo "no broker_token file found under /mnt/c/Users/*/AppData/Local/workflowy-mcp/ (set WORKFLOWY_BROKER_TOKEN)" >&2; exit 4; }
  export WORKFLOWY_BROKER_TOKEN_FILE="$TOK"
fi

cd "$HERE/.."
exec "$PY" -X utf8 scripts/oc_launch.py "$1" --self-resume "${@:2}"

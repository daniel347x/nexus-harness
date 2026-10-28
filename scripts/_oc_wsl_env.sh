#!/usr/bin/env bash
# _oc_wsl_env.sh — shared WSL environment for the companion CLI scripts that need the Workflowy daemon
# (sourced by oc_seat.sh · oc_measure.sh; the same plumbing oc_self_resume.sh uses inline).
#   • a Python with fastmcp (the nexus repo's .venv-wsl, else $OC_PY, else python3)
#   • WORKFLOWY_MCP_HTTP_DIAL_HOST from ~/.config/opencode/opencode.json (the conductor's own MCP URL)
#   • WORKFLOWY_BROKER_TOKEN_FILE = the Windows broker_token through /mnt/c (unless a token is already set)
# Exports PY and cds to the repo root. Exit codes: 3 no Python with fastmcp · 4 no token file.
set -euo pipefail
_HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

PY=""
for cand in "${OC_PY:-}" \
            /mnt/e/__daniel347x/__Dan_Root/__repos/workflowy-nexus-mcp-tools/.venv-wsl/bin/python \
            python3; do
  [ -n "$cand" ] || continue
  if "$cand" -c 'import fastmcp' >/dev/null 2>&1; then PY="$cand"; break; fi
done
[ -n "$PY" ] || { echo "no Python with fastmcp found (set OC_PY)" >&2; exit 3; }
export PY

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

if [ -z "${WORKFLOWY_BROKER_TOKEN:-}" ] && [ -z "${WORKFLOWY_BROKER_TOKEN_FILE:-}" ]; then
  TOK="$(ls /mnt/c/Users/*/AppData/Local/workflowy-mcp/broker_token 2>/dev/null | head -1 || true)"
  [ -n "$TOK" ] || { echo "no broker_token file found under /mnt/c/Users/*/AppData/Local/workflowy-mcp/ (set WORKFLOWY_BROKER_TOKEN)" >&2; exit 4; }
  export WORKFLOWY_BROKER_TOKEN_FILE="$TOK"
fi

cd "$_HERE/.."

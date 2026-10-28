#!/usr/bin/env bash
# oc_map_refresh.sh — ONE blocking Cartographer map refresh at a unit boundary, for the CONDUCTOR ([ap:GIAWVR] · [ap:DOCIEN]).
#
#   bash /mnt/e/__daniel347x/__Dan_Root/__repos/opencode-companion/scripts/oc_map_refresh.sh "<REPO_FOLDER_UUID>" \
#        --mode folder (--expect-symbol NAME | --no-sentinel) [--timeout 600] [--poll 15] [--dry-run]
#
# Runs from WSL (where the conductor lives). Same plumbing as oc_self_resume.sh:
#   • a Python that has fastmcp (the nexus repo's .venv-wsl, else $OC_PY, else python3)
#   • the Workflowy client pointed at the Windows-side MCP daemon (dial host parsed from ~/.config/opencode/opencode.json;
#     bearer token from the Windows broker_token file, or WORKFLOWY_BROKER_TOKEN/_FILE if already set)
# then hands everything to oc_map_refresh.py: launch beacon.map.refresh EXACTLY ONCE · poll system.job.status to terminal ·
# wait for the trailing cache refresh (sentinel symbol resolves, or a 45 s blind grace) · print ONE summary line.
# Exit 0 = the slice and the resolver are current (read the line; a WARNING line is acceptable and is recorded).
# Non-zero = read the message: 2 usage · 3 refused (cooldown / overlap / barrier — protection, not failure) · 4 job failed ·
# 5 timeout (the job is still running detached — poll system.job.status; do NOT launch again).
# Give the bash tool ~12 minutes so the script's own 10-minute cap fires first.
set -euo pipefail
HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
[ $# -ge 2 ] || { echo "usage: oc_map_refresh.sh \"<FOLDER-or-FILE uuid>\" --mode folder|file (--expect-symbol NAME | --no-sentinel) [--timeout S] [--poll S] [--dry-run]" >&2; exit 2; }

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
    mcp = cfg.get('mcp', {}); wf = mcp.get('servers', mcp).get('workflowy', {})
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
  [ -n "$TOK" ] || { echo "no broker_token file found under /mnt/c/Users/*/AppData/Local/workflowy-mcp/ (set WORKFLOWY_BROKER_TOKEN)" >&2; exit 3; }
  export WORKFLOWY_BROKER_TOKEN_FILE="$TOK"
fi

cd "$HERE/.."
exec "$PY" -X utf8 scripts/oc_map_refresh.py "$@"

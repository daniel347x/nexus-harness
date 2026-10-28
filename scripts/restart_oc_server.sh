#!/usr/bin/env bash
# Run directly from PowerShell:
# wsl bash -lc "bash /mnt/e/__daniel347x/__Dan_Root/__repos/opencode-companion/scripts/restart_oc_server.sh"
# Installs the repo's OpenCode config, prompt, plugins and agents, then restarts OpenCode's background service
# (`opencode service restart`) and reports HTTP + MCP readiness. Refuses while a session is running, or when OpenCode
# would reject the candidate config. An unreachable Workflowy MCP daemon only warns: the restart ends
# "READY WITHOUT WORKFLOWY MCP".
#   --check   check the candidate config only; install and restart nothing.
set -euo pipefail
HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$HERE/opencode_server.py" "$@"

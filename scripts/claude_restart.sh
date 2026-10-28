#!/usr/bin/env bash
# Guarded Claude host restart (WSL/POSIX): unsettled work refuses the restart, idle sessions are
# closed only on an explicit --close-idle, every owner must be proved gone before one witness-checked
# operator SIGTERM, then a fresh host is started and health-confirmed. Stdlib only; never a Windows shim.
set -euo pipefail
HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
exec env PYTHONPATH="$HERE${PYTHONPATH:+:$PYTHONPATH}" python3 -m claude_headless.restart "$@"

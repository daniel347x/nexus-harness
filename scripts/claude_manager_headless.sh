#!/usr/bin/env bash
# Thin operator launcher for the headless native Manager seat (U04); stdlib only, never a Windows shim.
set -euo pipefail
exec python3 "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/claude_manager_headless.py" "$@"

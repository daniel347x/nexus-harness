#!/usr/bin/env bash
# Stdlib-only native host/client, not the OpenCode launcher and never a Windows shim.
set -euo pipefail
exec python3 "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/claude_host.py" "$@"

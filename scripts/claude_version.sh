#!/usr/bin/env bash
# Claude Code version control (WSL native install): status · mark-good · unmark · rollback [VERSION] ·
# freeze · unfreeze. From PowerShell:
#   wsl bash -lc "bash /mnt/e/__daniel347x/__Dan_Root/__repos/opencode-companion/scripts/claude_version.sh rollback"
set -euo pipefail
exec python3 "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/claude_version.py" "$@"

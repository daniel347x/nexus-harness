#!/usr/bin/env bash
# oc.sh — run any oc_* script from WSL: the Manager's one entry point (an OpenCode Manager's shell is WSL).
#
#   bash /mnt/e/__daniel347x/__Dan_Root/__repos/opencode-companion/scripts/oc.sh view "[ap:PROG]"
#   bash …/oc.sh sessions --to-dock
#   bash …/oc.sh steer "[ap:PROG]" --file steer.md --rules
#   bash …/oc.sh                      # lists the scripts
#
# ‹name› is the script without its oc_ prefix and .py (oc_view.py → view); the full file name works too.
# It picks a Python that has fastmcp and points the Workflowy client at the Windows-side daemon (_oc_wsl_env.sh,
# the same setup oc_seat.sh uses), then runs the script from YOUR working directory, so relative paths
# (--file steer.md) mean what you meant. On Windows, run the .py files directly instead.
set -euo pipefail
_OC_HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

if [ $# -lt 1 ] || [ "$1" = "-h" ] || [ "$1" = "--help" ]; then
  echo "usage: bash $_OC_HERE/oc.sh ‹name› [args…]   (e.g. view, sessions, steer, launch, preflight, seal, peek, search, session, shelves, seat)"
  echo "scripts:"; for f in "$_OC_HERE"/oc_*.py; do n="$(basename "$f" .py)"; echo "  ${n#oc_}"; done
  exit 2
fi

_OC_NAME="${1%.py}"; _OC_NAME="${_OC_NAME#oc_}"; shift
_OC_SCRIPT="$_OC_HERE/oc_${_OC_NAME}.py"
[ -f "$_OC_SCRIPT" ] || { echo "oc.sh: no script oc_${_OC_NAME}.py in $_OC_HERE (run oc.sh with no arguments for the list)" >&2; exit 2; }

# DB submission and telemetry are stdlib-only and need neither the OpenCode server nor Workflowy credentials.
if [ "$_OC_NAME" = "dbap" ] || [ "$_OC_NAME" = "telemetry" ]; then
  exec "${OC_PY:-python3}" -X utf8 "$_OC_SCRIPT" "$@"
fi

_OC_CALLER_PWD="$PWD"
source "$_OC_HERE/_oc_wsl_env.sh"   # sets PY and the Workflowy variables; it also cds to the repo root
cd "$_OC_CALLER_PWD"
exec "$PY" -X utf8 "$_OC_SCRIPT" "$@"

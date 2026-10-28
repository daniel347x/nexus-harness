#!/usr/bin/env bash
set -euo pipefail
repo="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
common="$(git -C "$repo" rev-parse --path-format=absolute --git-common-dir)"
nexus="${NEXUS_MD_CHECKOUT:-$(dirname -- "$(dirname -- "$common")")/workflowy-nexus-mcp-tools}"
args=(--nexus "$nexus/workflowy-glimpse-cache")
if (( $# )); then args+=(--shots "$@"); fi
python3 "$repo/docs/readme/capture.py" "${args[@]}"

#!/usr/bin/env bash
# oc_measure.sh — MEASURE a seat-base manifest before it is built, from WSL (RECON's entry point). Read-only: no base is
# created, nothing is warmed, no model is called. Docs: the RECON clause [ap:73ZNJY] · the package-manager page [ap:MCC7MG].
#
#   bash /mnt/e/__daniel347x/__Dan_Root/__repos/opencode-companion/scripts/oc_measure.sh \
#        --manifest-ap "[ap:YOUR_SOCKET]" --agent opus55 --target 100000        # exit 0 under target · 3 over · 1 error
#   bash …/oc_measure.sh --symbols reconcile_tree map_codebase --map-root 7834ca3d-…   # three tiers per candidate
#
# A full ~60-item manifest takes about 3 minutes (the daemon serves reads one at a time); pass the bash tool a timeout
# of at least 600000 ms. Candidate symbols take ~5 s each.
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/_oc_wsl_env.sh"
exec "$PY" -X utf8 scripts/oc_measure.py "$@"

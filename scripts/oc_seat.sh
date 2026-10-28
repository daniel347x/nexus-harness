#!/usr/bin/env bash
# oc_seat.sh — SEAT BASES and SEATS from WSL: the conductor's (and a lead seat's) one script. Docs: the conductor's
# seats clause (StageConfiguration required_conductor_worker_clauses) · design [ap:8X6X8D] · handles only, nothing kept
# warm: Manager-Session-066; no registry, one handle → session map: Manager-Session-068; the manifest opens with the
# snapshot map, then the SkyView, and no worker clause is passed on the command line: Manager-Session-069 ([ap:QBMHEQ]).
#
#   bash /mnt/e/__daniel347x/__Dan_Root/__repos/opencode-companion/scripts/oc_seat.sh dispatch --agent sol61-xhigh \
#        --socket "[ap:S]" --program "[ap:PROG]" --no-wait   # a program WORKER, fresh, top-level (never the subagent tool);
#        --lead = a fresh LEAD SEAT, no seat base; an anthropic/* agent (opus55…, fable51…) runs on Claude Code on Max and prints cc-…
#   bash …/oc_seat.sh create --program "[ap:PROG]" \
#        --agent sol61-xhigh --role lead --manifest-ap "[ap:M]" --label NAME   # → sb-… : HOLD IT (OpenCode agents only)
#   bash …/oc_seat.sh fork --base sb-… --socket "[ap:S]" --no-wait            # a lead seat
#   bash …/oc_seat.sh fork --base sb-… --socket "[ap:T]" --parent-seat ses_… --no-wait   # a lead forking a helper
#   bash …/oc_seat.sh wait ses_… --max 540          # exit 0 done · 2 still running
#   bash …/oc_seat.sh resume ses_… --socket "[ap:S]"   # a FOLLOW-UP to an idle seat (context checked first; exit 3 = too full)
#   bash …/oc_seat.sh list --program "[ap:PROG]"
#   bash …/oc_seat.sh quick --agent quick-opus55-high --socket "[ap:S]" --no-wait   # a Claude Code quick worker on Max (or quick-fable51-‹level›):
#        prints cc-…, which wait / info / socket / resume / cancel / resolve and --parent-seat take (exit codes 4–8: oc_seat.py)
#
# An unknown handle is refused: run the same `create` again and use the new handle. Nothing is kept warm.
# Pass the bash tool an explicit timeout (≥ 600000 ms; create renders and warms, which may take a few minutes).
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/_oc_wsl_env.sh"
exec "$PY" -X utf8 scripts/oc_seat.py "$@"

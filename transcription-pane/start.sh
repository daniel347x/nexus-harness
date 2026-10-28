#!/usr/bin/env bash
# Start / stop the transcription pane server (WSL, 127.0.0.1:9911). Open http://127.0.0.1:9911 in Chrome.
#   bash transcription-pane/start.sh          start in the background (log: ~/.local/state/opencode-companion/transcription-pane.log)
#   bash transcription-pane/start.sh stop     stop it
#   bash transcription-pane/start.sh status   is it running?
#   bash transcription-pane/start.sh restart [--max SECONDS]
#       waits for a quiet moment (no Send, Refine or control in flight in the pane, and every live
#       Manager's reply finished), then stops, starts and checks that the page answers. Open pane tabs
#       resync by themselves. From a Manager's own turn, run it in the background: it restarts once
#       that turn has ended. --max gives up (exit 3, nothing restarted) instead of waiting on.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
STATE="$HOME/.local/state/opencode-companion"
PID="$STATE/transcription-pane.pid"
LOG="$STATE/transcription-pane.log"
mkdir -p "$STATE"

running() { [ -f "$PID" ] && kill -0 "$(cat "$PID")" 2>/dev/null; }
start_server() {
  setsid nohup python3 "$HERE/server.py" >>"$LOG" 2>&1 < /dev/null &
  echo $! > "$PID"
  sleep 1
  if running; then echo "started (pid $(cat "$PID")): http://127.0.0.1:9911"; else echo "failed to start; see $LOG"; tail -5 "$LOG"; exit 1; fi
}

case "${1:-start}" in
  stop)
    if running; then kill "$(cat "$PID")" && rm -f "$PID" && echo "stopped"; else echo "not running"; rm -f "$PID"; fi ;;
  status)
    if running; then echo "running (pid $(cat "$PID")) on http://127.0.0.1:9911"; else echo "not running"; fi ;;
  start)
    if running; then echo "already running (pid $(cat "$PID")) on http://127.0.0.1:9911"; exit 0; fi
    start_server ;;
  restart)
    shift
    python3 "$HERE/tools/pane_quiet.py" "$@" || exit $?
    if running; then
      old="$(cat "$PID")"; kill "$old"
      for _ in $(seq 1 50); do kill -0 "$old" 2>/dev/null || break; sleep 0.2; done
      if kill -0 "$old" 2>/dev/null; then echo "pid $old did not stop; nothing started"; exit 1; fi
      rm -f "$PID"; echo "stopped (pid $old)"
    fi
    start_server
    if python3 "$HERE/tools/pane_quiet.py" --ready; then
      echo "restarted at $(date '+%H:%M:%S'): open pane tabs resync by themselves; reload the page only for browser-side changes"
    else
      echo "started, but http://127.0.0.1:9911 does not answer; see $LOG"; exit 1
    fi ;;
  *)
    echo "usage: $0 [start|stop|status|restart [--max SECONDS]]"; exit 2 ;;
esac

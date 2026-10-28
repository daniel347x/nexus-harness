#!/usr/bin/env bash
# oc_payload_proxy.sh — start | stop | restart | status | log | tail  the Payload Extension for OpenCode (proxy/oc_payload_proxy.py).
#   bash /mnt/e/__daniel347x/__Dan_Root/__repos/opencode-companion/proxy/oc_payload_proxy.sh start
# Detached (setsid nohup), pid file + log under ~/.local/state/opencode-payload-proxy/. Idempotent start.
# OpenCode must point at it: every provider's settings.baseURL in config/opencode.json (http://127.0.0.1:4097/…).
# The proxy must be UP before OpenCode makes a model call; the restart script (scripts/opencode_server.py) RESTARTS it
# while OpenCode is down, so a code change to the proxy goes live with every OpenCode restart.
# NEVER restart it by hand while OpenCode has a session mid-call: in-flight requests (and 429 backoffs) would drop.
set -euo pipefail
HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
STATE="${XDG_STATE_HOME:-$HOME/.local/state}/opencode-payload-proxy"
PIDF="$STATE/proxy.pid"; LOGF="$STATE/proxy.log"; PORT="${OC_PROXY_PORT:-4097}"
mkdir -p "$STATE"
alive() { [ -f "$PIDF" ] && kill -0 "$(cat "$PIDF")" 2>/dev/null; }
case "${1:-status}" in
  restart)
    if alive; then kill "$(cat "$PIDF")"; for _ in $(seq 1 20); do alive || break; sleep 0.25; done; fi
    rm -f "$PIDF"
    exec bash "${BASH_SOURCE[0]}" start ;;
  start)
    if alive; then echo "already running (pid $(cat "$PIDF")) on :$PORT"; exit 0; fi
    setsid nohup python3 "$HERE/oc_payload_proxy.py" --port "$PORT" </dev/null >>"$LOGF" 2>&1 &
    echo $! > "$PIDF"; sleep 1
    if alive && (exec 3<>/dev/tcp/127.0.0.1/"$PORT") 2>/dev/null; then echo "started (pid $(cat "$PIDF")) on http://127.0.0.1:$PORT  log: $LOGF"; else echo "FAILED to start — see $LOGF"; tail -20 "$LOGF"; exit 1; fi ;;
  stop)
    if alive; then kill "$(cat "$PIDF")"; sleep 0.5; echo "stopped"; else echo "not running"; fi; rm -f "$PIDF" ;;
  status)
    if alive; then echo "running (pid $(cat "$PIDF")) on :$PORT"; else echo "NOT running"; fi
    [ -f "$STATE/requests.jsonl" ] && echo "requests logged: $(wc -l < "$STATE/requests.jsonl")  ($STATE/requests.jsonl)" ;;
  log)  tail -n "${2:-20}" "$STATE/requests.jsonl" 2>/dev/null || echo "no requests yet" ;;
  tail) tail -n "${2:-30}" "$LOGF" ;;
  *) echo "usage: $0 start|stop|restart|status|log [N]|tail [N]"; exit 2 ;;
esac

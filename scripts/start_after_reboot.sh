#!/usr/bin/env bash
# Bring everything back after a computer restart: the payload proxy (127.0.0.1:4097, in front of every
# OpenCode model provider), CodeNomad (127.0.0.1:9899), the transcription pane (127.0.0.1:9911) and the
# Claude host behind the pane's Claude Manager view. The OpenCode service (127.0.0.1:4096) starts on its own.
# The Workflowy MCP daemon runs on Windows and is started there; this script only checks it. Runs from any folder.
# From PowerShell:
#   wsl bash -lc "bash /mnt/e/__daniel347x/__Dan_Root/__repos/opencode-companion/scripts/start_after_reboot.sh"
# Judges each server by its port, not its pid file: after a reboot a stale pid can belong to another process.
set -u
HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

up() { curl -s -o /dev/null --max-time 3 "http://127.0.0.1:$1/"; }

# The Workflowy MCP daemon (Windows side, port 8766 on the Windows host as WSL sees it). When it is dark, open the
# Windows MCP connector in its own PowerShell window (it spawns the daemon) and wait for 8766. The connector command
# carries a private token, so it lives outside git, in ~/.config/opencode-companion/windows_mcp_connector_command
# (one line, e.g. `npx @typingmind/mcp <token>`). Run this script before Docker Desktop: Docker can take the connector's port.
WIN_HOST="$(ip route show default 2>/dev/null | awk '{print $3; exit}')"
daemon_up() { [ -n "$WIN_HOST" ] && curl -s -o /dev/null --max-time 3 "http://$WIN_HOST:8766/"; }
CONNECTOR_FILE="$HOME/.config/opencode-companion/windows_mcp_connector_command"
if daemon_up; then echo "Workflowy daemon: reachable ($WIN_HOST:8766)"
elif [ -s "$CONNECTOR_FILE" ] && command -v cmd.exe >/dev/null; then
  echo "Workflowy daemon: dark; opening the MCP connector in a Windows PowerShell window"
  (cd /mnt/c && cmd.exe /c start "MCP connector" powershell.exe -NoExit -Command "$(head -n1 "$CONNECTOR_FILE")") >/dev/null 2>&1
  for _ in $(seq 1 45); do daemon_up && break; sleep 2; done
  if daemon_up; then echo "Workflowy daemon: reachable ($WIN_HOST:8766)"
  else echo "Workflowy daemon: still NOT reachable after 90 s; check the connector window (port conflict? Docker?)"; fi
else echo "Workflowy daemon: NOT reachable at ${WIN_HOST:-?}:8766 and no connector command in $CONNECTOR_FILE; start it on Windows first"; fi

# The payload proxy: every OpenCode provider's baseURL points at it, so without it every OpenCode model call fails.
# Nothing starts it at boot (the OpenCode restart script restarts it; a reboot does not run that script).
if up 4097; then echo "Payload proxy: already up (4097)"
else rm -f "$HOME/.local/state/opencode-payload-proxy/proxy.pid"; bash "$HERE/../proxy/oc_payload_proxy.sh" start; fi

if up 4096; then echo "OpenCode service: up (4096)"; else echo "OpenCode service: DOWN (4096); try: opencode service start"; fi

if up 9899; then echo "CodeNomad: already up (9899)"
else rm -f "$HOME/.local/codenomad/codenomad.pid"; bash "$HOME/.local/codenomad/start.sh"; fi

if up 9911; then echo "Transcription pane: already up (9911)"
else rm -f "$HOME/.local/state/opencode-companion/transcription-pane.pid"; bash "$HERE/../transcription-pane/start.sh"; fi

# The Claude host behind the pane's Claude Manager view (docs/claude-headless-host.md). Never started at boot;
# a reboot leaves it absent. Its Manager launches default to the manager-sessions folder on their own.
host_ok() { printf '{}' | bash "$HERE/claude_host.sh" call health 2>/dev/null | grep -q '"host_pid"'; }
if host_ok; then echo "Claude host: already up"
else bash "$HERE/claude_host.sh" start >/dev/null && echo "Claude host: start requested"; fi

sleep 3
for p in 4097 9899 9911; do up "$p" && echo "  $p responding" || echo "  $p NOT responding"; done
host_ok && echo "  Claude host healthy" || echo "  Claude host NOT healthy; see ~/.local/state/opencode-companion/claude-headless/service.log"

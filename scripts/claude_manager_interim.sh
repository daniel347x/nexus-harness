#!/usr/bin/env bash
# claude_manager_interim.sh — the interim native Claude Code Manager on WSL, usable until the headless Claude Manager host is built.
#
# Plain, interactive Claude Code (Dan's Max subscription, the Linux install) in the manager-sessions folder, with
#   - the Manager prompt appended: agents/agent/manager.md (frontmatter removed) + manager-sessions/claude/claude-runtime-wsl.md
#   - the Workflowy MCP over HTTP (the same Windows daemon OpenCode uses), and only that MCP (--strict-mcp-config).
# Dan types or dictates (Wispr Flow) straight into the terminal. It uses none of the removed terminal-automation
# machinery (no input injection, no background terminal processes); the pane's Claude Send/control stays unavailable
# until that host lands. Nothing global is written: the prompt and the MCP
# config live in a private temp directory removed on exit, and the Workflowy bearer token stays in this process's
# environment (Claude Code expands ${WORKFLOWY_MCP_BEARER} in the config), never in a file or on a command line.
#
#   bash /mnt/e/__daniel347x/__Dan_Root/__repos/opencode-companion/scripts/claude_manager_interim.sh [claude args…]
#   e.g. … --model claude-opus-5-5     · … --resume ‹uuid›     · … --continue
set -euo pipefail

CLAUDE=/home/daniel347x/.local/bin/claude          # the Linux install, never a Windows shim on PATH
COMPANION=/mnt/e/__daniel347x/__Dan_Root/__repos/opencode-companion
SESSIONS=/mnt/e/__daniel347x/__Dan_Root/__repos/manager-sessions
TOKEN_FILE=/mnt/c/Users/danie/AppData/Local/workflowy-mcp/broker_token
GATEWAY=$(ip route show default | awk '{print $3; exit}')
URL="${WORKFLOWY_MCP_URL:-http://${GATEWAY}:8766/mcp}"

dir=$(mktemp -d "${TMPDIR:-/tmp}/claude-manager.XXXXXX")
chmod 700 "$dir"
trap 'rm -rf "$dir"' EXIT

# The Manager prompt: manager.md without its frontmatter, then the WSL/native runtime override.
awk 'NR == 1 && /^---$/ {fm = 1; next} fm && /^---$/ {fm = 0; next} !fm' "$COMPANION/agents/agent/manager.md" > "$dir/prompt.md"
cat "$SESSIONS/claude/claude-runtime-wsl.md" >> "$dir/prompt.md"

# The Workflowy MCP: the header names an environment variable, never the token itself.
cat > "$dir/mcp.json" <<EOF
{"mcpServers": {"workflowy": {"type": "http", "url": "$URL", "headers": {"Authorization": "Bearer \${WORKFLOWY_MCP_BEARER}"}}}}
EOF

WORKFLOWY_MCP_BEARER="$(tr -d '\r\n' < "$TOKEN_FILE")"
export WORKFLOWY_MCP_BEARER

cd "$SESSIONS"
"$CLAUDE" --append-system-prompt-file "$dir/prompt.md" --mcp-config "$dir/mcp.json" --strict-mcp-config "$@"

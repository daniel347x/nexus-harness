# OpenCode server lifecycle (WSL)

OpenCode runs as its own **background service** in WSL (`opencode serve --service`, started, registered and
restarted by OpenCode itself): 127.0.0.1:4096, Basic auth with user `opencode` and the password in
`~/.config/opencode/service.json`. Plain `opencode` (and the web app, OpenChamber, CodeNomad) attaches to it.
`opencode service status | start | stop | restart | get | set` manage it.

Install this repo's files and restart, from PowerShell:

```powershell
wsl bash -lc "bash /mnt/e/__daniel347x/__Dan_Root/__repos/opencode-companion/scripts/restart_oc_server.sh"
```

The script:
1. Checks that the Workflowy MCP daemon the config dials (the Windows-hosted daemon) is reachable. If it is not, it
   warns loudly and restarts anyway (the agents stay reachable); the run then ends `READY WITHOUT WORKFLOWY MCP`
   instead of `READY`: start the daemon, then restart again.
2. **Checks the candidate config** on a throwaway `opencode serve` (port 4098, its own config, data and state homes,
   its own password): any configuration diagnostic, any plugin that fails to load, or any repo agent missing from
   `/api/agent` refuses the restart. OpenCode itself does not reject a bad config: it drops the bad value (or the
   whole file, if the JSON is malformed) and logs a warning, so this check is where a mistake shows before it costs
   a session. An unknown top-level key is ignored silently and no check can see it.
3. Refuses if any session is running (`/api/session/active`).
4. Installs `config/opencode.json` (the previous copy kept as `opencode.json.bak-prev-sync`), `plugins/*.ts` and
   `agents/agent/*.md` (into `~/.config/opencode/agents/`), and the worker instructions `agents/AGENTS.md` into the
   **home folder**, `~/AGENTS.md`, removing any global `~/.config/opencode/AGENTS.md`. Copy-only otherwise: never
   deletes a file. Why home: OpenCode reads `~/AGENTS.md` as a project instruction file for every session located in
   home (every conductor, seat, helper and worker), and never for a session outside it, such as the Manager's in
   `manager-sessions` on `/mnt/e`, whose prompt is the `manager` agent's body (`agents/agent/manager.md`).
5. Checks the service settings (port 4096, hostname 127.0.0.1, `UV_PROJECT_ENVIRONMENT=.venv-wsl`).
6. Restarts the payload proxy when a provider routes through it.
7. Runs `opencode service restart`, then waits for a new server process and a connected Workflowy MCP.

`--check` runs the isolated candidate check without installing or restarting the live service.
Before probing, the script can regenerate the repository's `agents/agent/manager-quick.md` from
`quick-worker/AGENTS.md`; inspect/commit any generated change, never edit the installed copy.
Candidate acceptance is not no-click execution certification: see
[unattended execution readiness](unattended-execution.md) for fresh tool receipts and route checks.

MCP connected is not tab-registry proof: use Workflowy's `system.broker.status` for that.

A Workflowy cache reset is separate and lives in `workflowy-nexus-mcp-tools/scripts/`: restart the Windows MCP
daemon first, then run this script.

Tests (WSL): `python3 -m pytest scripts/test_opencode_server.py -q`.

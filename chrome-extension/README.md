# chrome-extension: our Chrome extensions

Small, unpacked Chrome extensions, one per folder. Install each from chrome://extensions → Developer mode →
Load unpacked → the folder.

## codenomad-session-watcher/

Read-only. Runs on CodeNomad's page (`http://127.0.0.1:9899`) and tells the transcription pane
(`../transcription-pane/`, `http://127.0.0.1:9911`) which OpenCode session CodeNomad has open, by watching the
labels CodeNomad itself puts on the visible conversation (`data-session-id` on the active
`.message-stream-container`). It never clicks, types or changes anything on the page.

## Planned: the OpenCode web view (`http://127.0.0.1:4096`)

OpenCode's own web view has no plugin system and shows MCP tool calls by name only. If we ever work there
again, an extension reading OpenCode's HTTP API (not the page) could add a tool-call panel and the payload
visuals. CodeNomad already shows full tool inputs and outputs, so this is not needed today.

Documentation lives in Workflowy (🐫 CodeNomad DEPLOYMENT AND USAGE INSTRUCTIONS Dock, and 🧩 BUILT ON OpenCode).

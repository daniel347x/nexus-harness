# `glimpse-rows` — the daemon's real row-list replies, for View's tests

Recorded data, not hand-written approximations. Do not edit them to make a test pass.

| file | what | provenance |
|---|---|---|
| `envelopes.json` | `glimpse.run` / `anchor.dock.render` replies with `include_rows: true` (Dock, simple Dock, slice group, created-asc and modified-desc raw roots with a repeated-ancestor occurrence, node, tags), two taps (the echoed `render_spec` with only `focus` replaced by a real row's triple), a stale focus and an unknown parameter. Each record keeps the MCP transport: `structuredContent`, the content text twin, `isError`. | `capture_envelopes.py`, run in process (`mcp.call_tool`) over S1's offline fixture `tests/fixtures/glimpse-include-rows/scenario.py`, from the S1 ext4 test clone whose `src/workflowy_mcp/server.py` SHA-256 `aa0d4915d49b10115665e98dfa3f3d75aa0f831837fd81bf44fd871b8ddc0f1d` equals NEXUS `paneviews-md-render` @ `8e6e320`. Fixture SHA-256 `b8a0c9426396d8720c3583316fa9d4dd5b2d240fb471640e8d5eb6a2302abac3`. |
| `pre_s1_forbidden.json` | The pre-S1 daemon's `forbidden_parameter` refusals of `include_rows` for both operations. | Copied verbatim from NEXUS `8e6e320` `tests/fixtures/glimpse-include-rows/edge_outputs.json` (SHA-256 `837640cd…fc18`, captured by S1 from the archived pre-S1 `0c56c96` server). |

Re-capture (only if the daemon's contract legitimately changes):

    cd ~/.cache/wnmt-tests/workflowy-nexus-mcp-tools-wt-s1 && .venv/bin/python <companion>/transcription-pane/tests/fixtures/glimpse-rows/capture_envelopes.py --out <companion>/transcription-pane/tests/fixtures/glimpse-rows/envelopes.json

The timestamps are the fixture's small epoch seconds (1970); View's browser tests add their own
chronological corpus from the NEXUS row oracle for day separators.

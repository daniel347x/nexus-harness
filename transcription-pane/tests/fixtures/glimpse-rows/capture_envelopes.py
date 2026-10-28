"""Record the NEXUS daemon's real include_rows MCP replies for the pane's View tests.

Run from a NEXUS checkout that contains S1 (glimpse.run / anchor.dock.render include_rows), with that
checkout's own Python environment, e.g.:

    cd <nexus checkout or its wnmt-tests ext4 clone> && .venv/bin/python <this file> --out <envelopes.json>

It drives the daemon's actual FastMCP tools in process (mcp.call_tool) over S1's committed offline
fixture (tests/fixtures/glimpse-include-rows/scenario.py): no live daemon, cache or Workflowy tab.
Each record keeps the MCP transport as the pane meets it: structuredContent, the content text twin
and isError. A tap is recorded by focusing the echoed render_spec on one real row, exactly as View does.
"""
import argparse
import asyncio
import copy
import hashlib
import json
import pathlib
import subprocess
import sys

sys.path[:0] = ["src", "tests", "tests/fixtures/glimpse-include-rows"]
from workflowy_mcp import server as srv  # noqa: E402
import scenario  # noqa: E402


def call(family, verb, params):
    result = asyncio.run(srv.mcp.call_tool(family, {"verb": verb, "params": params}))
    return {"family": family, "verb": verb, "params": params, "isError": bool(result.is_error),
            "structuredContent": result.structured_content,
            "text": [part.text for part in result.content if getattr(part, "type", None) == "text"]}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    out = pathlib.Path(parser.parse_args().out)
    records = {}
    with scenario.patched_environment(srv):
        for name in ("anchor_dock", "simple_dock", "slice_group", "raw_root_created_asc", "raw_root_modified_desc",
                     "node_parcel", "tags_parcel"):
            family, verb, params = scenario.SCENARIOS[name]
            family = {"GLIMPSE_REGISTRY": "glimpse", "ANCHOR_REGISTRY": "anchor"}[family]
            records[name] = call(family, verb, {**copy.deepcopy(params), "include_rows": True})
        for name in ("anchor_dock", "raw_root_created_asc"):
            envelope = records[name]["structuredContent"]
            row = envelope["rows"][-1]
            spec = copy.deepcopy(envelope["render_spec"])
            spec["focus"] = {"mode": "row", "uuid": row["uuid"], "ancestor_path": row["ancestor_path"], "occurrence": row["occurrence"]}
            records[name + "_tap"] = call("glimpse", "run", {"render_spec": spec, "as_markdown": True, "include_rows": True})
        stale = copy.deepcopy(records["anchor_dock"]["structuredContent"]["render_spec"])
        stale["focus"] = {"mode": "row", "uuid": "99999999-0000-4000-8000-000000000000", "ancestor_path": [scenario.DOCK], "occurrence": 0}
        records["stale_focus"] = call("glimpse", "run", {"render_spec": stale, "as_markdown": True, "include_rows": True})
        records["unknown_parameter"] = call("glimpse", "run", {"render_spec": {"node": scenario.A1}, "include_rowz": True})
    server_sha = hashlib.sha256(pathlib.Path("src/workflowy_mcp/server.py").read_bytes()).hexdigest()
    try:
        head = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        head = None
    payload = {"server_sha256": server_sha, "git_head": head, "records": records}
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(out, server_sha)


if __name__ == "__main__":
    main()

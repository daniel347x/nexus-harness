#!/usr/bin/env python3
"""oc_map_refresh.py — ONE blocking Cartographer map refresh at a unit boundary ([ap:GIAWVR] · contract [ap:DOCIEN]).

    oc.sh map_refresh "<FOLDER-or-FILE node uuid | [ap:X]>" --mode folder|file
        [--repo-path PATH] [--no-probe] [--expect-symbol NAME] [--timeout 600] [--poll 15] [--dry-run]

What it does, and refuses to do:
  0. Reads the target node. The repository's root FOLDER node says `Root: <repo path>` (or --repo-path); a subfolder
     says `Path: <its name>`, and its disk folder is the root plus each ancestor's `Path:` segment.
  1. FOLDER mode (default): writes a PROBE FILE inside the TARGET folder (the repo root only when the target is the root;
     a probe outside the refreshed folder would never be mapped) — `zz_oc_map_refresh_probe_<8hex>.py`, one function
     `oc_map_refresh_probe_<8hex>()` — BEFORE launching. Its name is the sentinel: a brand-new symbol cannot resolve by name
     until BOTH the refresh job has reconciled the file into Workflowy AND the trailing /nodes-export cache refresh has
     landed. That is exactly the readiness the conductor needs, and it works whether or not the unit added symbols.
     The file is deleted afterwards (always — also on failure); its FILE node is a harmless ghost the next refresh removes.
     --no-probe disables this and falls back to --expect-symbol / the blind grace. FILE mode never probes.
  2. beacon(verb='map.refresh', {node_handle, mode}) EXACTLY ONCE. Prints the job record, keeps job_id. A refusal
     (cooldown_active with retry_after · overlap_conflict · coherence_barrier_active · map_target_invalid ·
     governance_cleanup_required · anything else) exits non-zero with the daemon's own message. NEVER retries, NEVER `force`.
  3. Polls system(verb='job.status', {job_id, view: 'summary'}) every --poll seconds until terminal
     (one logged fallback to the old default if an older daemon rejects `view`):
     completed / completed_with_warnings → continue (warnings printed) · failed / cancelled → exit non-zero.
  4. Waits for the trailing cache refresh: polls beacon(verb='resolve', {source_handle: <sentinel>}) every 30 s until
     match_count >= 1 (bounded by --timeout; on the cap exit 0 with a WARNING — a stale cache is not a failed refresh).
     With no sentinel available (FILE mode without --expect-symbol, or --no-probe without one): 45 s blind grace + WARNING.
  5. Exit 0 with ONE summary line: job_id · status · elapsed · files_refreshed · sentinel resolved yes/no/blind —
     the line the Conductor pastes into ConductorState.
  6. On --timeout: exit non-zero, print the job_id and "still running detached — poll system.job.status, do not re-fire".

Runs on Windows (repo root as cwd) or from WSL via scripts/oc_map_refresh.sh, which sets the daemon plumbing.
Reuses oc_common.py (the boom's Workflowy client). Observation plus exactly one launch and one throwaway file.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import secrets
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from oc_common import Workflowy, note_json  # noqa: E402

TERMINAL_OK = {"completed", "completed_with_warnings"}
TERMINAL_BAD = {"failed", "cancelled"}
SENTINEL_POLL_S = 30
BLIND_GRACE_S = 45
EXIT_USAGE, EXIT_REFUSED, EXIT_FAILED, EXIT_TIMEOUT = 2, 3, 4, 5
ROOT_RE = re.compile(r"^\s*Root:\s*(.+?)\s*$", re.M)
PATH_RE = re.compile(r"^\s*Path:\s*(.+?)\s*$", re.M)
UUID_RE = re.compile(r"`([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})`")


def _j(txt: str) -> dict:
    """The daemon replies with text that carries JSON; parse it or wrap the text so callers can still read it."""
    j = note_json(txt) if txt else None
    if isinstance(j, dict):
        return j
    try:
        j = json.loads(txt)
        return j if isinstance(j, dict) else {"raw": txt}
    except Exception:  # noqa: BLE001
        return {"raw": (txt or "")[:400]}


def _err_text(j: dict) -> str:
    e = j.get("error")
    if isinstance(e, dict):
        ra = e.get("retry_after")
        if ra is None and isinstance(e.get("detail"), dict):
            ra = e["detail"].get("retry_after")
        return f"{e.get('code', '')}: {e.get('message', '')}" + (f" (retry_after={ra}s)" if ra else "")
    return json.dumps(e)[:300] if e else (j.get("raw") or "")[:300] or "unknown refusal"


def _elapsed(t0: float) -> str:
    s = int(time.monotonic() - t0)
    return f"{s // 60}m{s % 60:02d}s" if s >= 60 else f"{s}s"


def _find_job_id(obj) -> str | None:
    """Any `job_id` (or a carto-refresh-* id) anywhere in a reply, however the daemon nests it."""
    if isinstance(obj, dict):
        v = obj.get("job_id")
        if isinstance(v, str) and v:
            return v
        v = obj.get("id")
        if isinstance(v, str) and v.startswith("carto-refresh-"):
            return v
        for x in obj.values():
            r = _find_job_id(x)
            if r:
                return r
    elif isinstance(obj, list):
        for x in obj:
            r = _find_job_id(x)
            if r:
                return r
    elif isinstance(obj, str):
        m = re.search(r"carto-refresh-[a-z]+-[0-9a-f]{8}", obj)
        if m:
            return m.group(0)
    return None


async def _adopt_running_job(wf: "Workflowy", root: str, launched_after: float) -> str | None:
    """A parser can be wrong; the job list cannot. If a CARTO_REFRESH on this root is active and was created after we
    launched, that is our job — adopt it rather than call the launch a refusal (and never, ever launch again)."""
    try:
        lst = _j(await wf.call("system", "job.status", {"kind": "CARTO_REFRESH", "status": "active", "limit": 10}))
    except Exception:  # noqa: BLE001
        return None
    for row in (lst.get("detached_jobs") or []) + (lst.get("in_memory_jobs") or []):
        ru = str(row.get("root_uuid") or "")
        if root and not (ru.startswith(root) or root.startswith(ru[: len(root)])):
            continue
        try:
            from datetime import datetime, timezone
            created = datetime.fromisoformat(str(row.get("created_at")).replace("Z", "+00:00"))
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            if created.timestamp() < launched_after - 5:
                continue
        except Exception:  # noqa: BLE001
            pass
        jid = row.get("job_id")
        if jid:
            return str(jid)
    return None


async def _target_folder(wf: "Workflowy", node: str, note_txt: str, repo_path: str | None) -> tuple[Path | None, Path | None]:
    """(repo root, disk folder of the target). The root node carries `Root:`; each folder below it carries
    `Path: <name>`, so the folder is the root joined with the `Path:` segments from the root down to the target."""
    m = ROOT_RE.search(note_txt or "")
    if m:
        root = _host_path(repo_path) if repo_path else _host_path(m.group(1))
        return root, root
    own = PATH_RE.search(note_txt or "")
    if not own:
        return (_host_path(repo_path), _host_path(repo_path)) if repo_path else (None, None)
    parts = [own.group(1)]
    chain = list(dict.fromkeys(UUID_RE.findall(await wf.call("node", "ancestors", {"node_handle": node, "uuids": "every-level"}))))
    for uuid in reversed(chain[:-1]):  # nearest ancestor first; the last entry is the target itself
        txt = await wf.read_note_text(uuid)
        r, pth = ROOT_RE.search(txt or ""), PATH_RE.search(txt or "")
        if r:
            root = _host_path(repo_path) if repo_path else _host_path(r.group(1))
            return root, root.joinpath(*parts)
        if not pth:
            break
        parts.insert(0, pth.group(1))
    return (_host_path(repo_path), _host_path(repo_path).joinpath(*parts)) if repo_path else (None, None)


def _host_path(p: str) -> Path:
    """A path as the daemon wrote it (Windows `E:\\…`) or as the caller gave it, made usable on THIS host."""
    p = p.strip().strip('"').strip("'")
    if os.name != "nt":
        m = re.match(r"^([A-Za-z]):[\\/](.*)$", p)
        if m:
            return Path(f"/mnt/{m.group(1).lower()}/{m.group(2).replace(chr(92), '/')}")
    else:
        m = re.match(r"^/mnt/([a-z])/(.*)$", p)
        if m:
            return Path(f"{m.group(1).upper()}:\\{m.group(2).replace('/', chr(92))}")
    return Path(p)


def _probe_text(fn: str) -> str:
    return (
        f'"""oc_map_refresh probe — a throwaway sentinel file written by opencode-companion/scripts/oc_map_refresh.py.\n\n'
        f"It exists only so that the symbol below becomes resolvable by name once the map AND the nodes-export cache are\n"
        f"current after a refresh. It is deleted as soon as that is observed; its mapped node is removed by the next refresh.\n"
        f'Safe to delete on sight. Never import, never commit.\n"""\n\n\n'
        f"def {fn}() -> str:\n"
        f'    """Sentinel: resolvable by name only after the refresh job and the trailing cache refresh have both landed."""\n'
        f'    return "{fn}"\n'
    )


# @beacon[
#   id=auto-beacon@main-psgb,
#   role=main,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=One map refresh and the existing terminal/readiness wait; each job poll names the summary view, with one logged legacy-schema fallback and no detail fetch. Existing terminal detection, warnings and exits stay unchanged.,
# ]
async def main(a: argparse.Namespace) -> int:
    """Launch once and poll summaries; an older schema falls back for this run only."""
    t0 = time.monotonic()
    probe_path: Path | None = None
    sentinel_symbol: str | None = a.expect_symbol
    try:
        async with Workflowy() as wf:
            # -- 0 · read the target: proves the handle, and yields the repo root for a FOLDER
            try:
                note_txt = await wf.read_note_text(a.node)
            except Exception as e:  # noqa: BLE001
                print(f"cannot reach the daemon or read {a.node!r}: {str(e)[:200]}", file=sys.stderr)
                return EXIT_REFUSED
            j = note_json(note_txt) if note_txt else None
            if isinstance(j, dict) and j.get("success") is False:
                print(f"target {a.node!r} did not resolve: {_err_text(j)}", file=sys.stderr)
                return EXIT_REFUSED
            repo_root, probe_dir = (await _target_folder(wf, a.node, note_txt, a.repo_path)) if a.mode == "folder" else (
                (_host_path(a.repo_path), None) if a.repo_path else (None, None))
            print(f"target {a.node} · mode {a.mode} · root {repo_root if repo_root else '(none read)'}"
                  + (f" · folder {probe_dir}" if probe_dir and probe_dir != repo_root else ""))

            # -- 1 · the probe (FOLDER mode only)
            use_probe = a.mode == "folder" and not a.no_probe
            if use_probe:
                if probe_dir is None or not probe_dir.is_dir():
                    print(f"cannot place the probe: target folder {probe_dir!r} is not a directory on this host — pass --repo-path, "
                          f"or --no-probe with --expect-symbol", file=sys.stderr)
                    return EXIT_USAGE
                tag = secrets.token_hex(4)
                sentinel_symbol = f"oc_map_refresh_probe_{tag}"
                probe_path = probe_dir / f"zz_oc_map_refresh_probe_{tag}.py"
                if not a.dry_run:
                    probe_path.write_text(_probe_text(sentinel_symbol), encoding="utf-8")
                print(f"probe {'would be ' if a.dry_run else ''}written: {probe_path.name} · sentinel {sentinel_symbol}")
            elif a.mode == "file" and a.expect_symbol:
                print(f"sentinel (caller-supplied): {a.expect_symbol}")
            else:
                print("no sentinel available — the trailing cache refresh will be a blind 45 s grace")

            if a.dry_run:
                print(f"DRY RUN — would call beacon.map.refresh(node_handle={a.node!r}, mode={a.mode!r}) once, poll system.job.status "
                      f"every {a.poll}s to terminal, then "
                      + (f"poll beacon.resolve({sentinel_symbol!r}) every {SENTINEL_POLL_S}s" if sentinel_symbol
                         else f"wait {BLIND_GRACE_S}s blind") + f"; total cap {a.timeout}s. Nothing launched, nothing written.")
                return 0

            # -- 2 · launch ONCE
            launched_at = time.time()
            launch_txt = await wf.call("beacon", "map.refresh", {"node_handle": a.node, "mode": a.mode})
            launch = _j(launch_txt)
            job_id = _find_job_id(launch) if launch.get("success") is not False else None
            if not job_id:
                # the reply may be a shape we do not parse: the job list is the authority, not our parser
                job_id = await _adopt_running_job(wf, a.node.split("-")[0].strip("[]"), launched_at)
                if job_id:
                    print(f"launch reply not parsed as a job record; ADOPTED the active job {job_id} on this root (reply head: "
                          f"{(launch_txt or '')[:160]!r})")
            if not job_id:
                print(f"REFUSED — {_err_text(launch)}", file=sys.stderr)
                print(f"  raw reply: {(launch_txt or '')[:400]!r}", file=sys.stderr)
                print("Not retrying. cooldown_active → wait retry_after, then launch once by hand · overlap_conflict → another job "
                      "holds the region, wait for it (system.job.status kind=CARTO_REFRESH) · coherence_barrier_active → a human's call.",
                      file=sys.stderr)
                return EXIT_REFUSED
            print(f"launched {job_id} · status {launch.get('status', '?')}")

            # -- 3 · poll to terminal
            status, rec = "", {}
            summary_view = True
            while True:
                if time.monotonic() - t0 > a.timeout:
                    print(f"TIMEOUT after {_elapsed(t0)} — job {job_id} is still running detached (last status {status or '?'}). "
                          f"Poll system(verb='job.status', params={{'job_id': '{job_id}'}}); do NOT re-fire.", file=sys.stderr)
                    return EXIT_TIMEOUT
                await asyncio.sleep(a.poll)
                params = {"job_id": job_id, **({"view": "summary"} if summary_view else {})}
                rec = _j(await wf.call("system", "job.status", params))
                error = rec.get("error")
                if (summary_view and rec.get("success") is False and isinstance(error, dict)
                        and error.get("code") == "forbidden_parameter" and "view" in str(error.get("message", ""))):
                    print("Older daemon rejects view; polling its default job status for the rest of this run.")
                    summary_view = False
                    rec = _j(await wf.call("system", "job.status", {"job_id": job_id}))
                status = str(rec.get("status") or "").lower()
                prog = rec.get("progress") or {}
                done, total, phase = prog.get("completed_files"), prog.get("total_files"), prog.get("current_phase") or ""
                print(f"  {_elapsed(t0):>7} · {status or '?'}" + (f" · {done}/{total} files" if total else "") + (f" · {phase}" if phase else ""))
                if status in TERMINAL_OK or status in TERMINAL_BAD:
                    break
            summary = rec.get("result_summary") or {}
            files = summary.get("files_refreshed")
            warnings = rec.get("warnings") or []
            if status in TERMINAL_BAD:
                print(f"FAILED — {job_id} ended {status}: {_err_text(rec)}", file=sys.stderr)
                for w in warnings[:10]:
                    print(f"  warning: {w}", file=sys.stderr)
                return EXIT_FAILED
            for w in warnings[:10]:
                print(f"  warning: {w}")

            # -- 4 · trailing cache refresh
            if not sentinel_symbol:
                await asyncio.sleep(BLIND_GRACE_S)
                print(f"WARNING: blind wait (no sentinel) — {BLIND_GRACE_S}s grace after terminal; the slice view may lag a few seconds")
                sentinel = "blind"
            else:
                sentinel = "no"
                while True:
                    r = _j(await wf.call("beacon", "resolve", {"source_handle": sentinel_symbol}))
                    if int(r.get("match_count") or 0) >= 1:
                        sentinel = "yes"
                        break
                    if time.monotonic() - t0 > a.timeout:
                        print(f"WARNING: sentinel {sentinel_symbol!r} did not resolve within {a.timeout}s — a stale cache is not a "
                              f"failed refresh; the job completed. Continue, and check the cache later.")
                        break
                    print(f"  {_elapsed(t0):>7} · waiting for cache — {sentinel_symbol} not yet resolvable")
                    await asyncio.sleep(SENTINEL_POLL_S)

        # -- 5 · the one line
        print(f"{job_id} · {status} · {_elapsed(t0)} · files_refreshed={files if files is not None else '?'} · sentinel resolved {sentinel}"
              + (f" ({sentinel_symbol})" if sentinel == "yes" else ""))
        return 0
    finally:
        if probe_path is not None and probe_path.exists():
            try:
                probe_path.unlink()
                print(f"probe removed: {probe_path.name} (its FILE node is a harmless ghost until the next refresh)")
            except OSError as e:
                print(f"WARNING: could not remove probe {probe_path}: {e} — delete it by hand", file=sys.stderr)


def parse() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="one blocking Cartographer map refresh; never loops, never forces")
    ap.add_argument("node", help="the FOLDER (or FILE) node: UUID, unique prefix, or [ap:X]")
    ap.add_argument("--mode", required=True, choices=["folder", "file"])
    ap.add_argument("--repo-path", help="repository root on this host; default: parsed from the FOLDER node's `Root:` line")
    ap.add_argument("--no-probe", action="store_true", help="folder mode: do not write the probe file; use --expect-symbol or a blind wait")
    ap.add_argument("--expect-symbol", help="file mode (or --no-probe): a symbol the unit ADDED, used as the sentinel")
    ap.add_argument("--timeout", type=int, default=600, help="total wait in seconds (default 600)")
    ap.add_argument("--poll", type=int, default=15, help="job.status poll interval in seconds (default 15)")
    ap.add_argument("--dry-run", action="store_true", help="read the target and print the plan; launch nothing, write nothing")
    return ap.parse_args()


if __name__ == "__main__":
    sys.exit(asyncio.run(main(parse())))

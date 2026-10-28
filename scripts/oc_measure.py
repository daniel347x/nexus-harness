#!/usr/bin/env python3
r"""oc_measure.py — MEASURE a seat-base manifest BEFORE it is built, and size candidate symbols at the three read
tiers, so RECON can trim a package to the conductor's target (Dan, Manager-Sessions 063–064; design [ap:X8OVJJ],
plan [ap:Y6S16M]). Read-only: no seat base is created, nothing is warmed, no model is called, nothing is
written. It costs MCP reads only.

WHAT IT DOES. Renders the manifest exactly as `oc_seat create` would (the snapshot map note(s) first, then the SkyView,
then every line in reading order, with notes, through the same render path), then reports per resource: bytes,
≈ tokens, share, and a running total; the fixed overhead (OpenCode's system prompt + tools, ~26K); the total against
a target; what every seat turn and one warm-up would cost at the agent's rates; and the largest items as cut
candidates, each `symbol` body shown beside its TIER-2 size (what demoting it would save).

THE THREE READ TIERS for a mapped symbol (Dan, [ap:Y6S16M]):
  tier 1  the beacon slice, headings only: name + signature (+ the beacon's tags)       render_symbols headings_only
  tier 2  the same slice with headings_only false: + the docstring and the comments     render_symbols (default)
          slurped from beside the definition (the "why", without the code)
  tier 3  the whole body, comments included                                             beacon.snippet  (= `symbol` line)

TOKENS are estimated: bytes ÷ a fixed bytes-per-token figure per model family (Opus ≈ 2.8 on this code-heavy
content, DeepSeek ≈ 4.6, Qwen ≈ 3.3; fitted from earlier warm-ups). Tokenizers differ, so a target is always "on the
agent that will read it". The real number is the parked prefix `oc_seat create` prints after its warm-up.

USAGE (the Manager: oc.sh measure …  ·  RECON in WSL: bash scripts/oc_measure.sh …)
  Measure a manifest (the conductor's `🪑 Seat base · …` node: the ONE fenced block under its `## Manifest` heading;
  a file: the whole file), or a RECON's PROPOSED LINES (its socket: the ONE fenced block under `## Proposed lines`;
  RECON drafts lines, only the conductor writes the manifest). No fallback: a missing heading is an error.
      --manifest-ap "[ap:X]" | --proposed-ap "[ap:RECON socket]" | --manifest FILE | --resource "KIND ARG" [--resource …]
      [--agent opus55] [--target 100000] [--role lead|helper] [--cut 10] [--tiers] [--map-root UUID]
  (A manifest's `map` and `skyview` lines are measured first, as `create` renders them; a RECON's proposed lines have none.)
  Size candidate symbols BEFORE including them (three tiers each; needs a map root: --map-root, or the root_handles of
  the manifest's `render lightning_rod render_symbols` line):
      --symbols NAME [NAME …] --map-root UUID [--agent opus55]
  --tiers   also size every `symbol` line of the manifest at tiers 1 and 2 (one extra render each).
  --cut N   how many of the largest items to list as cut candidates (default 10; tier 2 sized for each symbol).
Exit code: 0 at or under the target (or no target), 3 over it, 1 on a usage/render error.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from oc_common import Workflowy  # noqa: E402
from oc_pack import PKG_DIR, PROXY_TTL, block_under, compose, host_path, parse_manifest, render_items  # noqa: E402
from oc_seat import agent_model, seat_base_opening  # noqa: E402

OVERHEAD = 26_000        # OpenCode's system prompt + tool definitions in every warm prefix (measured on earlier warm-ups)
# Bytes per token by model family (substring of the agent name → ratio), fitted from earlier warm-ups.
DEFAULT_BPT = (("opus", 2.8), ("fable", 2.8), ("ds41", 4.6), ("deepseek", 4.6), ("qwen", 3.3))
# $ per million tokens: (cache read, cache write AS BILLED here). Anthropic writes go through the payload proxy, whose
# TTL sets the write price: 5m = 1.25× base input (default since Manager-Session-064), 1h = 2× ([ap:QS2ZHC]); OpenRouter
# models list their own. Estimates for orientation.
_W = 2.0 if PROXY_TTL == "1h" else 1.25
RATES = {"opus55": (0.20, 4.0 * _W), "opus55-or": (0.20, 4.0 * _W), "fable51": (0.25, 10.0 * _W),
         "fable51-or": (0.25, 10.0 * _W), "qwen3-max": (0.25, 2.50), "ds41-flash": (0.006, 0.30)}
# Render cache: byte counts of rendered items, keyed by the exact manifest line, kept CACHE_TTL_S. The daemon resolves
# symbol names at ~0.9 s each (a 70-symbol render takes about a minute), so re-measuring an unchanged line (RECON's
# render line when the conductor measures the full manifest) should cost nothing. --no-cache bypasses it.
CACHE_PATH = PKG_DIR / "measure_cache.json"
CACHE_TTL_S = 1800


# ----------------------------------------------------------------------------------------------- pure helpers

def bytes_per_token(agent: str) -> tuple[float, str]:
    """(bytes per token, how it was obtained) for `agent`, from its model family; 3.0 for an unknown family."""
    for key, v in DEFAULT_BPT:
        if key in agent:
            return v, f"fixed figure for '{key}' models"
    return 3.0, f"generic figure (no known family in '{agent}')"


def map_roots(items: list[tuple]) -> list[str]:
    """root_handles of the first `render lightning_rod render_symbols {…}` line (for tier renders), else []."""
    for kind, arg, *_ in items:
        if kind == "render" and arg.startswith("lightning_rod render_symbols"):
            try:
                j = json.loads(arg.split(" ", 2)[2])
            except Exception:  # noqa: BLE001
                continue
            r = j.get("root_handles")
            return [r] if isinstance(r, str) else list(r or [])
    return []


def mapped_symbols(items: list[tuple]) -> set[str]:
    """Every symbol named in the manifest's render_symbols lines (the symbol map, tier 2)."""
    out: set[str] = set()
    for kind, arg, *_ in items:
        if kind == "render" and arg.startswith("lightning_rod render_symbols"):
            try:
                j = json.loads(arg.split(" ", 2)[2])
            except Exception:  # noqa: BLE001
                continue
            for s in j.get("symbols") or []:
                out.add(s if isinstance(s, str) else (s.get("symbol") or s.get("uuid") or ""))
    return out


def label(kind: str, arg: str) -> str:
    if kind == "render":
        fam_verb = " ".join(arg.split(" ", 2)[:2]).replace(" ", ".")
        try:
            j = json.loads(arg.split(" ", 2)[2])
            n = len(j.get("symbols") or [])
            ho = " · headings only" if j.get("headings_only") else ""
            return f"{fam_verb} ({n} symbols{ho})" if n else fam_verb
        except Exception:  # noqa: BLE001
            return fam_verb
    return arg if len(arg) <= 72 else arg[:69] + "…"


def k(tokens: float) -> str:
    return f"≈{tokens / 1000:,.1f}K"


# ----------------------------------------------------------------------------------------------- rendering

async def _read_note(handle: str) -> str:
    async with Workflowy() as wf:
        _, note = await wf.read_note(handle)
        return note


def load_manifest(a) -> tuple[dict, str]:
    """→ ({maps, skyview, skyview_note, items}, where it came from)."""
    if a.proposed_ap:
        text = block_under(asyncio.run(_read_note(a.proposed_ap)), "Proposed lines", f"RECON socket {a.proposed_ap}")
        return parse_manifest(text, full=True), a.proposed_ap
    if a.manifest_ap:
        text = block_under(asyncio.run(_read_note(a.manifest_ap)), "Manifest", f"manifest node {a.manifest_ap}")
        return parse_manifest(text, full=True), a.manifest_ap
    if a.manifest:
        return parse_manifest(host_path(a.manifest).read_text(encoding="utf-8"), full=True), a.manifest
    items = [(r.partition(" ")[0].lower(), r.partition(" ")[2].strip(), "") for r in (a.resource or [])]
    return {"maps": [], "skyview": None, "skyview_note": "", "items": items}, "--resource lines"


PARALLEL = 6                              # concurrent reads on the one daemon connection (pure reads; order is kept)
_SEM: asyncio.Semaphore | None = None
_CACHE: dict | None = None
USE_CACHE = True


def _cache() -> dict:
    global _CACHE
    if _CACHE is None:
        try:
            raw = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            raw = {}
        now = time.time()
        _CACHE = {k: v for k, v in raw.items() if isinstance(v, dict) and now - v.get("at", 0) < CACHE_TTL_S}
    return _CACHE


def _cache_save() -> None:
    if _CACHE is None:
        return
    try:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        CACHE_PATH.write_text(json.dumps(_CACHE), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


def body_bytes(text: str) -> int:
    """Bytes of a section body exactly as oc_pack.compose writes it (newlines normalized, outer blank lines stripped)."""
    return len(text.replace("\r\n", "\n").replace("\r", "\n").strip("\n").encode("utf-8"))


async def _gather(items: list[tuple], wf) -> list[tuple[int | None, str | None]]:
    """render_one for every item, up to PARALLEL at a time, results in the items' order."""
    return list(await asyncio.gather(*(render_one(it, wf) for it in items)))


async def render_one(item: tuple, wf) -> tuple[int | None, str | None]:
    """(body bytes, None) or (None, error) for one manifest item, through oc_pack.render_items (the path `ensure` uses),
    on the shared connection `wf`; one item at a time so a failure is reported instead of aborting the measurement.
    Served from the render cache when the same line was rendered in the last CACHE_TTL_S."""
    key = f"{item[0]} {item[1]}"
    if USE_CACHE and key in _cache():
        return _cache()[key]["bytes"], None
    try:
        global _SEM
        if _SEM is None:
            _SEM = asyncio.Semaphore(PARALLEL)
        async with _SEM:
            out = await render_items([item], wf=wf)
        n = body_bytes(out[0][2])
        _cache()[key] = {"bytes": n, "at": time.time()}
        return n, None
    except SystemExit as e:
        return None, str(e)
    except Exception as e:  # noqa: BLE001
        return None, f"{type(e).__name__}: {e}"


async def tier_sizes(name: str, roots: list[str], wf) -> dict:
    """{t1, t2} bytes for one symbol via render_symbols (headings only / full). Each single-symbol render also carries
    its folder and file headers (shared when symbols are rendered together), so these are slight over-estimates."""
    out: dict = {}
    for key, ho in (("t1", True), ("t2", False)):
        params = {"root_handles": roots, "symbols": [name], "headings_only": ho}
        txt, err = await render_one(("render", f"lightning_rod render_symbols {json.dumps(params)}", ""), wf)
        out[key] = txt
        if err:
            out[key + "_err"] = err[:160]
    return out


# ----------------------------------------------------------------------------------------------- reports

def cost_line(total_tokens: float, agent: str) -> str:
    r = RATES.get(agent)
    if not r:
        return f"*(no rate table for `{agent}`: per-turn and warm-up costs not estimated)*"
    per_turn = total_tokens * r[0] / 1e6
    warm = total_tokens * r[1] / 1e6
    return (f"**Every turn of every seat re-reads this: ≈ ${per_turn:.3f}** (cache read ${r[0]}/M) · "
            f"one warm-up writes it: ≈ ${warm:.2f} (cache write ${r[1]}/M as billed) · "
            f"each extra 10K tokens ≈ {10_000 * r[0] / 1e6 * 100:.2f}¢ per turn per seat")


async def measure_manifest(a, mf: dict, source: str, bpt: float, how: str, wf) -> tuple[list[str], float]:
    role = a.role
    rows: list[dict] = []
    head = [("ap", h, "", "map") for h in mf.get("maps") or []]
    if mf.get("skyview"):
        head.append(("ap", mf["skyview"], mf.get("skyview_note") or "", "SkyView"))
    todo = head + [(kd, arg, note, "") for kd, arg, note in mf["items"]]
    print(f"   rendering {len(todo)} section(s) as `create` would …", file=sys.stderr, flush=True)
    sections, errors = [], []
    results = await _gather([(kd, arg, note) for kd, arg, note, _ in todo], wf)
    for (kd, arg, note, fixed), (nbytes, err) in zip(todo, results):
        if err:
            errors.append(f"- `{kd} {label(kd, arg)}`: {err[:200]}")
            continue
        sections.append((kd, arg, "", note))
        rows.append({"kind": kd, "arg": arg, "bytes": nbytes, "fixed": fixed})
    # the framing (opening line, contents line, section headers, notes) = compose() with empty bodies
    framing = len(compose("measure", sections, opening=seat_base_opening(role)).encode("utf-8"))
    total_bytes = framing + sum(r["bytes"] for r in rows)
    content_tokens = total_bytes / bpt
    total_tokens = content_tokens + OVERHEAD
    in_map = mapped_symbols(mf["items"])

    L = [f"# 📏 Measure · {source} · agent `{a.agent}` · {role} base", "",
         f"Calibration: **{bpt:.2f} bytes per token**, {how}. Fixed overhead ~{OVERHEAD // 1000}K tokens (OpenCode's "
         "system prompt + tools: in every prefix, not a resource).", "",
         "| # | Kind | Resource | KB | ≈ tokens | % | running |", "|---|---|---|---|---|---|---|"]
    run = OVERHEAD
    L.append(f"| — | fixed | OpenCode system prompt + tools | — | {k(OVERHEAD)} | {100 * OVERHEAD / total_tokens:.1f}% | {k(run)} |")
    if framing > 0:
        t = framing / bpt
        run += t
        L.append(f"| — | framing | opening line · section headers and notes | {framing / 1024:,.1f} | "
                 f"{k(t)} | {100 * t / total_tokens:.1f}% | {k(run)} |")
    for i, r in enumerate(rows, 1):
        t = r["bytes"] / bpt
        run += t
        tag = f" *({r['fixed']})*" if r["fixed"] else ""
        dup = " *(also in the symbol map)*" if r["kind"] == "symbol" and r["arg"] in in_map else ""
        L.append(f"| {i} | {r['kind']} | `{label(r['kind'], r['arg'])}`{tag}{dup} | {r['bytes'] / 1024:,.1f} | {k(t)} | "
                 f"{100 * t / total_tokens:.1f}% | {k(run)} |")
    by_kind: dict[str, int] = {}
    for r in rows:
        by_kind[r["kind"]] = by_kind.get(r["kind"], 0) + r["bytes"]
    L += ["", "By kind: " + " · ".join(f"{kd} {k(b / bpt)} ({100 * b / total_bytes:.0f}%)"
                                        for kd, b in sorted(by_kind.items(), key=lambda kv: -kv[1])), ""]
    verdict = ""
    if a.target:
        pct = 100 * total_tokens / a.target
        diff = total_tokens - a.target
        verdict = (f" · target {a.target:,} → **{pct:.0f}% of target, "
                   + (f"OVER by {k(diff)}**" if diff > 0 else f"under by {k(-diff)}**"))
    L.append(f"## Total ≈ {total_tokens:,.0f} tokens (content {k(content_tokens)} + fixed {k(OVERHEAD)}){verdict}")
    kb = total_bytes / 1024
    kb_verdict = ""
    if a.target_kb:
        kb_verdict = (f" · target {a.target_kb:,} KB → **{100 * kb / a.target_kb:.0f}%, "
                      + (f"OVER by {kb - a.target_kb:,.1f} KB**" if kb > a.target_kb else f"under by {a.target_kb - kb:,.1f} KB**"))
    L.append(f"Rendered size: **{kb:,.1f} KB** (the snapshot text, without the fixed overhead){kb_verdict}")
    L += ["", cost_line(total_tokens, a.agent), ""]

    # cut candidates: the largest material items (never the map or the SkyView); symbol bodies get their tier-2 size beside them
    roots = [a.map_root] if a.map_root else map_roots(mf["items"])
    cands = sorted((r for r in rows if not r["fixed"]), key=lambda r: -r["bytes"])[: a.cut]
    tiers_for = {r["arg"] for r in rows if r["kind"] == "symbol"} if a.tiers else {r["arg"] for r in cands if r["kind"] == "symbol"}
    tiers: dict[str, dict] = {}
    if roots and tiers_for:
        print(f"   sizing {len(tiers_for)} symbol(s) at tiers 1 and 2 …", file=sys.stderr, flush=True)
        names = sorted(tiers_for)
        got = await asyncio.gather(*(tier_sizes(n, roots, wf) for n in names))
        tiers = dict(zip(names, got))
    L += [f"## Cut candidates — the {len(cands)} largest items", "",
          "| Resource | now | tier 2 (slice with comments) | saves | tier 1 (headings) |", "|---|---|---|---|---|"]
    for r in cands:
        now = r["bytes"] / bpt
        if r["kind"] == "symbol" and r["arg"] in tiers and tiers[r["arg"]].get("t2"):
            t2 = tiers[r["arg"]]["t2"] / bpt
            t1 = (tiers[r["arg"]].get("t1") or 0) / bpt
            dup = " (already in the symbol map: dropping the body loses nothing from tier 2)" if r["arg"] in in_map else ""
            L.append(f"| symbol `{r['arg']}`{dup} | {k(now)} | {k(t2)} | {k(now - t2)} | {k(t1)} |")
        else:
            L.append(f"| {r['kind']} `{label(r['kind'], r['arg'])}` | {k(now)} | — | — | — |")
    if tiers_for and not roots:
        L += ["", "*Tier sizes skipped: no map root (give `--map-root`, or put a `render lightning_rod render_symbols` "
              "line with root_handles in the manifest).*"]
    if a.tiers and tiers:
        L += ["", "## Every symbol line at the three tiers", "",
              "| Symbol | tier 1 | tier 2 | tier 3 (body, as now) |", "|---|---|---|---|"]
        body = {r["arg"]: r["bytes"] for r in rows if r["kind"] == "symbol"}
        for name in sorted(tiers, key=lambda n: -body.get(n, 0)):
            t = tiers[name]
            L.append(f"| `{name}` | {k((t.get('t1') or 0) / bpt)} | {k((t.get('t2') or 0) / bpt)} | {k(body.get(name, 0) / bpt)} |")
    if errors:
        L += ["", f"## ⚠️ {len(errors)} section(s) failed to render (not counted; `ensure` would refuse this manifest)", *errors]
    L += ["", "*Estimates: bytes ÷ the calibration above; tier-1/2 renders include their folder and file headers, which "
          "are shared when several symbols render together, so they read slightly high. The parked prefix `oc_seat create` "
          "prints after its warm-up is the truth.*"]
    return L, (float("nan") if errors else (3.0 if ((a.target and total_tokens > a.target)
                                                     or (a.target_kb and kb > a.target_kb)) else 0.0))


async def measure_symbols(a, bpt: float, roots: list[str], wf) -> list[str]:
    L = [f"# 📏 Candidate symbols at the three read tiers · agent `{a.agent}`", "",
         "| Symbol | tier 1 (headings) | tier 2 (slice with comments) | tier 3 (full body) |", "|---|---|---|---|"]
    res = []
    for name in a.symbols:
        body, err = await render_one(("symbol", name, ""), wf)
        t = {} if err else (await tier_sizes(name, roots, wf) if roots else {})
        res.append((name, t, body, err))
    for name, t, body, err in sorted(res, key=lambda x: -(x[2] or 0)):
        f = lambda b: k(b / bpt) if isinstance(b, int) else "—"  # noqa: E731
        note = f" ⚠️ {err[:80]}" if err else ""
        L.append(f"| `{name}`{note} | {f(t.get('t1'))} | {f(t.get('t2'))} | {f(body)} |")
    if not roots:
        L += ["", "*Tiers 1–2 skipped: no `--map-root`.*"]
    return L


# @beacon[
#   id=oc-measure@main,
#   role=measure,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=oc_measure entry point — size a seat-base manifest before building it; three read tiers per symbol; per-agent calibration,
# ]
def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    p = argparse.ArgumentParser(description="Measure a seat-base manifest before building it; size candidate symbols "
                                            "at the three read tiers. Read-only.")
    src = p.add_mutually_exclusive_group()
    src.add_argument("--manifest-ap", help="the conductor's seat-base manifest node (the block under `## Manifest`)")
    src.add_argument("--proposed-ap", help="a RECON socket: its PROPOSED LINES (the block under `## Proposed lines`)")
    src.add_argument("--manifest", help="manifest file")
    src.add_argument("--resource", action="append", help='one manifest line, e.g. "symbol reconcile_tree" (repeatable)')
    p.add_argument("--symbols", nargs="+", help="candidate symbols to size at tiers 1–3 (no manifest needed)")
    p.add_argument("--map-root", help="Cartographer map root UUID for tier renders (default: the manifest's render_symbols root)")
    p.add_argument("--agent", default="opus55", help="the agent that will read the base (calibration + rates); default opus55")
    p.add_argument("--target", type=int, help="target prefix in tokens on that agent, e.g. 100000")
    p.add_argument("--target-kb", type=int, help="target rendered size in KB (the snapshot text, fixed overhead excluded)")
    p.add_argument("--no-cache", action="store_true", help=f"re-render everything (default: reuse renders of identical "
                                                           f"lines from the last {CACHE_TTL_S // 60} min)")
    p.add_argument("--role", choices=("lead", "helper"), default="lead", help="lead or helper base (its opening wording)")
    p.add_argument("--cut", type=int, default=10, help="how many largest items to list as cut candidates (default 10)")
    p.add_argument("--tiers", action="store_true", help="size every symbol line at tiers 1 and 2 (one render each)")
    a = p.parse_args(argv)
    if not (a.manifest_ap or a.proposed_ap or a.manifest or a.resource or a.symbols):
        p.error("give a manifest (--manifest-ap / --proposed-ap / --manifest / --resource) and/or --symbols")
    try:
        agent_model(a.agent)
    except SystemExit as e:
        print(f"❌ {e}")
        return 1
    global USE_CACHE
    USE_CACHE = not a.no_cache
    bpt, how = bytes_per_token(a.agent)
    mf, source = load_manifest(a) if (a.manifest_ap or a.proposed_ap or a.manifest or a.resource) else (None, "")
    out, status = asyncio.run(_run(a, mf, source, bpt, how))
    print("\n".join(out))
    return status


async def _run(a, mf: dict | None, source: str, bpt: float, how: str) -> tuple[list[str], int]:
    out: list[str] = []
    status = 0
    async with Workflowy() as wf:                     # ONE daemon connection for every render
        if mf is not None:
            lines, verdict = await measure_manifest(a, mf, source, bpt, how, wf)
            out += lines
            if verdict != verdict:          # NaN: render errors
                status = 1
            elif verdict:
                status = 3
        if a.symbols:
            roots = [a.map_root] if a.map_root else (map_roots(mf["items"]) if mf else [])
            out += ([""] if out else []) + await measure_symbols(a, bpt, roots, wf)
    _cache_save()
    return out, status


if __name__ == "__main__":
    raise SystemExit(main())

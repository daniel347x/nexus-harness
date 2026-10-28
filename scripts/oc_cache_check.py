#!/usr/bin/env python3
"""oc_cache_check.py — live checks that the ChatGPT plan's prompt cache works through OpenCode.

    oc.sh cache_check                    (the launcher, scripts/oc.sh; from any folder)
    oc.sh cache_check --model openai/gpt-6-astra#medium --turns 5 --forks 3
    oc.sh cache_check --feature-smoke --manifest /abs/path/feature-smoke.json

SIMPLE MODE (default): one session of N short turns, then F forks of it with one turn each, on the given model (default
openai/gpt-6.1-sol#medium, on Dan's ChatGPT plan). Prints how much of each turn OpenCode recorded as read from the
prompt cache. Every turn after the first, and every fork's first turn, should hit: a session's turns share one cache
route, and a fork shares its source's. On the "Sign in with ChatGPT" route that route is the `session-id` header, which
plugins/chatgpt-cache-affinity.ts sets (OpenAI ignores the body's prompt_cache_key there). A brand-new session's first
turn may hit too, because OpenCode's system prompt and tools are the same in every session; it is reported, not judged.
This mode proves an ACK-sized prefix reads back; it is NOT proof that genuine tool reading was inherited.

FINISHED-FEATURE SMOKE (`--feature-smoke`): the opt-in journey a real pre-seeded session must survive — one genuine
multi-step tool-reading pre-pass, same-level fork F1, changed-level fork F2, a completed deliberate `--extend` on the
base, and later fork F3 — driven through the real `oc_seat.sh dispatch/fork/resume/wait/info` owners from a Conductor-
supplied manifest of owned scratch sockets and read-only material. It reports both accounts, the recomputed B/N facts,
copy/identity/recall/obedience evidence, post-proxy effective effort and exact-id cleanup. See `oc_feature_smoke.py`
for the manifest schema and exit codes (0 pass · 1 required observation failed · 2 incomplete). It never retries a cold
step into a pass and never creates Workflowy nodes.

Exit 0 when every judged turn hit, 1 when any missed, 2 on an error (simple mode). The test sessions are deleted unless
--keep.
Cost: N + F short turns (~25K input tokens each, nearly all of it cached when the check passes); the smoke is a real
journey and costs what its manifest's reading and jobs cost.
Per call (the session-id sent, the key OpenAI used): ~/.local/state/opencode-companion/openai-usage.jsonl (WSL).
"""
import argparse
import sys

import oc_client as c


def assistant_tokens(sid):
    return [c.msg_tokens(m) for m in c.oc_messages(sid) if c.msg_type(m) == "assistant"]


def cached(t):
    total = t["input"] + t["cache"]["read"]
    return t["cache"]["read"], total, (100 * t["cache"]["read"] / total if total else 0.0)


# @beacon[
#   id=auto-beacon@oc_cache_check-main,
#   role=main,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=Live cache checks: the simple N-turns-then-F-forks proof (an ACK-sized prefix, not reading coverage), and the opt-in finished-feature journey through the real oc_seat dispatch/fork/resume/wait/info owners from one owned-input manifest (pre-pass → same-level F1 → changed-level F2 → completed --extend → F3) with both accounts, copy/identity/recall/obedience evidence, post-proxy effort and exact-id cleanup.,
# ]
def main() -> int:
    ap = argparse.ArgumentParser(description="Live check: does the ChatGPT plan's prompt cache work through OpenCode?")
    ap.add_argument("--model", default="openai/gpt-6.1-sol#medium", help="provider/model#variant (default: %(default)s)")
    ap.add_argument("--turns", type=int, default=3, help="turns in the session (default: %(default)s)")
    ap.add_argument("--forks", type=int, default=2, help="forks of it, one turn each (default: %(default)s)")
    ap.add_argument("--keep", action="store_true", help="keep the test sessions (default: delete them)")
    ap.add_argument("--feature-smoke", action="store_true",
                    help="run the opt-in finished-feature journey instead of the simple turn check")
    ap.add_argument("--manifest", help="with --feature-smoke: the owned-input manifest (see oc_feature_smoke.py)")
    ap.add_argument("--receipt-dir", help="with --feature-smoke: receipt/log directory (default: the program receipts)")
    ap.add_argument("--state-dir", help="with --feature-smoke: XDG state dir holding the tap/proxy logs")
    ap.add_argument("--wait-max", type=float, default=1800,
                    help="with --feature-smoke: one wait's --max seconds (default: %(default)s)")
    args = ap.parse_args()
    if args.feature_smoke:
        if not args.manifest:
            ap.error("--feature-smoke needs --manifest PATH (the owned-input manifest)")
        import oc_feature_smoke
        return oc_feature_smoke.main(manifest=args.manifest, receipt_dir=args.receipt_dir,
                                     state_dir=args.state_dir, wait_max=args.wait_max)

    created: list[str] = []
    judged = hits = 0
    try:
        _, session = c.oc_create_session(title="oc_cache_check", model=c.parse_model_ref(args.model))
        sid = session["id"]
        created.append(sid)
        print(f"session {sid} · {args.model} · {args.turns} turns, then {args.forks} fork(s)", flush=True)
        for i in range(1, args.turns + 1):
            c.oc_prompt(sid, f"Reply with the single word: turn{i}.")
            c.oc_wait(sid, timeout=180)
        for i, t in enumerate(assistant_tokens(sid), 1):
            read, total, pct = cached(t)
            if i == 1:
                print(f"  turn {i}: cached {read} of {total} ({pct:.0f}%)  first turn, not judged", flush=True)
                continue
            judged += 1
            hits += read > 0
            print(f"  turn {i}: cached {read} of {total} ({pct:.0f}%)  {'HIT' if read else 'MISS'}", flush=True)
        for k in range(1, args.forks + 1):
            _, fork = c.oc_fork(sid)
            fid = fork["id"]
            created.append(fid)
            c.oc_prompt(fid, f"Reply with the single word: fork{k}.")
            c.oc_wait(fid, timeout=180)
            read, total, pct = cached(assistant_tokens(fid)[-1])
            judged += 1
            hits += read > 0
            print(f"  fork {k}, first turn: cached {read} of {total} ({pct:.0f}%)  {'HIT' if read else 'MISS'}",
                  flush=True)
    except Exception as e:  # report and clean up; the exit code says it was not a verdict
        print(f"error: {type(e).__name__}: {e}", flush=True)
        return 2
    finally:
        if args.keep:
            print("kept:", " ".join(created), flush=True)
        else:
            for x in reversed(created):
                try:
                    c.oc_delete(x)
                except Exception:
                    pass
    passed = judged > 0 and hits == judged
    print(f"{'✅' if passed else '❌'} {hits} of {judged} judged turns read from the prompt cache", flush=True)
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())

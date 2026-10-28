#!/usr/bin/env python3
"""oc_claude_restart.py — the guarded Claude host restart, queued FROM INSIDE a Claude Code Manager's own turn.

claude_restart.sh refuses (exit 3) while any session is unsettled, and the Manager's own turn IS one. So this script
detaches one operation-keyed job and returns QUEUED, not restarted. Only typed same-ID
pre-stop own-turn/settlement/transition-lock holds retry. Permanent or unknown refusals and
post-stop outcomes do not blindly rerun argv. A viable replacement is observed by the engine
beyond the pre-stop queue budget. Popups use its service/Manager/notice facts, not option text.

  oc.sh claude_restart --close-idle                                   # restart only; Dan opens a New Manager
  oc.sh claude_restart --close-idle --resume ‹uuid›                    # and resume this same conversation
  oc.sh claude_restart --close-idle --launch-manager --model claude-opus-5-5 --effort high

Every argument except --timeout and --interval is passed to claude_restart.sh unchanged. Run it as the LAST action of
the turn. Log: /tmp/opencode/oc_claude_restart.log
"""
from __future__ import annotations

import argparse, json, os, subprocess, sys, time, uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from oc_restart import popup  # noqa: E402
from claude_headless.restart import RestartControl, CANCELLED_EXIT  # noqa: E402

HERE = Path(__file__).resolve().parent
LOG = Path("/tmp/opencode/oc_claude_restart.log")
REFUSED = 3


def refusal_reason(out: str) -> str:
    """The first line of a REFUSED message, without the long list of dead historical records."""
    for line in out.splitlines():
        if "REFUSED" in line or "refusing" in line:
            return line.split(" dead historical records")[0][:600]
    return (out.strip().splitlines() or ["(no output)"])[-1][:600]


def bind_operation(args):
    """Parse both ID aliases/equals forms; every request retains its ID before detach."""
    parser = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
    parser.add_argument('--operation-id', '--_operation-id', dest='operation_id')
    parser.add_argument('--recover')
    parser.add_argument('--status')
    parsed, _ = parser.parse_known_args(args)
    operation_id = parsed.recover or parsed.status or parsed.operation_id or str(uuid.uuid4())
    if not (parsed.recover or parsed.status or parsed.operation_id):
        args = [*args, '--operation-id', operation_id]
    return args, operation_id


def restart_envelope(text, operation_id=None):
    """Machine envelope only; prose/exit code can never authorize retry or success claims."""
    found = None
    decoder = json.JSONDecoder()
    for offset, char in enumerate(text):
        if char != '{':
            continue
        try:
            candidate, _ = decoder.raw_decode(text[offset:])
        except ValueError:
            continue
        if (isinstance(candidate, dict) and candidate.get('schema') == 'claude-restart/1'
                and (operation_id is None or candidate.get('operation_id') == operation_id)):
            request = candidate.get('request')
            if (type(candidate.get('admitted')) is not bool or not isinstance(request, dict)
                    or request.get('kind') not in ('restart', 'account_switch')
                    or not all(key in request for key in ('account', 'close_idle', 'namespace', 'manager', 'notify'))
                    or type(request['close_idle']) is not bool or type(request['notify']) is not bool
                    or not isinstance(request['namespace'], dict) or not isinstance(request['manager'], dict)
                    or not all(isinstance(candidate.get(key), dict) for key in ('service', 'manager', 'notice', 'publication', 'retry'))
                    or not all(isinstance(candidate.get(key), list) for key in ('effects', 'errors'))
                    or not all(isinstance(effect, dict) for effect in candidate['effects'])
                    or not all(isinstance(error, dict) for error in candidate['errors'])
                    or not isinstance(candidate.get('phase'), str)
                    or candidate.get('requested_transition') not in ('pending', 'succeeded', 'failed', 'refused', 'cancelled')):
                continue  # missing facts are malformed evidence, never proof that no stop happened
            found = candidate
    return found


def retryable(envelope):
    if not envelope:
        return False
    retry = envelope.get('retry') or {}
    return (retry.get('safe_pre_stop_retry') is True and
        envelope.get('requested_transition') == 'refused' and
        retry.get('classification') in ('own_turn', 'settlement', 'transition_lock') and
        envelope.get('phase') in ('waiting', 'settling') and
        not any(effect.get('kind') in ('host_signal', 'selector_commit', 'start_host')
                for effect in envelope.get('effects') or []))


def outcome_text(envelope, operation_id):
    if not envelope:
        return f'Outcome unknown. Operation {operation_id}; query its status, do not repeat the switch.'
    service, manager, notice = (envelope.get(k) or {} for k in ('service', 'manager', 'notice'))
    label = (service.get('account') or {}).get('label') or 'unknown account'
    if service.get('state') == 'ready':
        prefix = (f'Switch failed; {label} service restored' if envelope.get('direction') == 'restore_old' else
                  f'{label} host ready')
    elif service.get('state') == 'starting':
        prefix = 'Replacement still starting; not ready yet'
    else:
        prefix = f"{envelope.get('requested_transition', 'unknown')}: phase {envelope.get('phase', 'unknown')}; service {service.get('state', 'unknown')}"
    details = f"Manager {manager.get('state', 'unknown')}; notice {notice.get('delivery_status') or notice.get('status') or 'not scheduled'}."
    errors = envelope.get('errors') or []
    error = ('\n' + str(errors[-1].get('message', ''))) if errors else ''
    return prefix + '. ' + details + error + '\nOperation ' + operation_id + '\n' + str((envelope.get('commands') or {}).get('status', ''))


# @beacon[
#   id=opc@queued_claude_restart_worker,
#   slice_labels=opc-basic,opc-pane,opc-claude-restart,
#   kind=ast,
#   comment=Consume one engine envelope under a preallocated ID: retry only typed pre-stop transients, keep viable startup observation beyond the queue budget, preserve cancellation/partial effects and derive service/Manager/notice popup claims from retained facts rather than argv.,
# ]
def worker(script: str, args: list[str], timeout: int, interval: int, control=None) -> int:
    """Queue the one engine; typed pre-stop retry only, never another post-stop transition."""
    args, operation_id = bind_operation(args)
    def cancelled():
        return control is not None and control.phase() == 'cancelled'

    def wait(seconds):
        deadline = time.monotonic() + seconds
        while True:
            if cancelled():
                return True
            left = deadline - time.monotonic()
            if left <= 0:
                return False
            time.sleep(min(left, 0.2) if control is not None else left)

    t0, attempt = time.time(), 0
    if wait(3):
        return CANCELLED_EXIT
    while True:
        if cancelled():
            return CANCELLED_EXIT
        attempt += 1
        gate = ['--_control', str(control.path)] if control is not None else []
        r = subprocess.run(["bash", script, *args, *gate], capture_output=True, text=True)
        out = (r.stdout + r.stderr).strip()
        print(f"=== attempt {attempt} {time.strftime('%H:%M:%S')} exit {r.returncode}\n{out}", flush=True)
        if cancelled():
            return CANCELLED_EXIT
        envelope = restart_envelope(r.stdout, operation_id)
        if r.returncode != REFUSED or not retryable(envelope):
            break
        if time.time() - t0 > timeout:
            popup("Claude host restart: NOT DONE (timed out)",
                  f"The guarded restart still refused after {timeout}s; inspect retained stop receipts if stopping began "
                  f"({attempt} attempts).\n\n{refusal_reason(out)}")
            return REFUSED
        if wait(interval):
            return CANCELLED_EXIT
    if r.returncode == 0:
        popup('Claude host restart: DONE' if envelope and envelope.get('service', {}).get('state') == 'ready' else
              'Claude host restart: OUTCOME UNKNOWN', outcome_text(envelope, operation_id))
    else:
        popup("Claude host restart: FAILED",
              outcome_text(envelope, operation_id))
    return r.returncode


# @beacon[
#   id=opc@queued_claude_restart_main,
#   slice_labels=opc-basic,opc-claude-restart,
#   kind=ast,
#   comment=Allocates/parses every restart request ID before detach and returns QUEUED with status/recover commands, never a restart result; child attempts retain the same ID and canonical engine envelope.,
# ]
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--timeout", type=int, default=3600,
                    help="seconds to keep retrying while the restart refuses (default 3600: waiting harms nothing)")
    ap.add_argument("--interval", type=int, default=15, help="seconds between attempts (default 15)")
    ap.add_argument("--_worker", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--_script", default=str(HERE / "claude_restart.sh"), help=argparse.SUPPRESS)
    ap.add_argument('--_control', type=Path, default=None, help=argparse.SUPPRESS)
    a, passthrough = ap.parse_known_args()
    passthrough, operation_id = bind_operation(passthrough)
    if a._worker:
        return worker(a._script, passthrough, a.timeout, a.interval,
                      **({'control': RestartControl(a._control)} if a._control else {}))
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "w") as log:
        subprocess.Popen([sys.executable, "-X", "utf8", __file__, "--_worker", "--timeout", str(a.timeout),
                          "--interval", str(a.interval), "--_script", a._script,
                          *(['--_control', str(a._control)] if a._control else []), *passthrough],
                         stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                         start_new_session=True, env=os.environ.copy())
    from claude_headless.restart_state import commands
    print(json.dumps({'schema': 'claude-restart-queue/1', 'status': 'QUEUED', 'operation_id': operation_id,
        'commands': commands(operation_id, {})}))
    print(f'QUEUED, not restarted. Operation {operation_id}; only typed pre-stop holds retry every {a.interval}s '
          f'within {a.timeout}s. END YOUR TURN NOW. Log: {LOG}')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

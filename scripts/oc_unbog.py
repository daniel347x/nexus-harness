#!/usr/bin/env python3
"""oc_unbog.py — find and clear what blocks the guarded Claude host restart, by hand (`oc.sh unbog`).

Written during the 2026-10-22 usage-limit lockout and kept as the operator's emergency tool
(the by-hand runbook is the 🔑 LOCKED OUT page [ap:D8NOYW]). Run in WSL:

    bash scripts/oc.sh unbog show                      # read-only: every session blocking the restart, and why
    bash scripts/oc.sh unbog interrupt cc-AAAA cc-BBBB  # host-native interrupt of exactly those sessions
    bash scripts/oc.sh unbog watch [SECONDS]           # re-read until nothing blocks (default 180 s; Ctrl+C any time)
    bash scripts/oc.sh unbog stop-idle cc-AAAA         # close one idle session held by a stale marker

(`python3 scripts/oc_unbog.py …` works too: it needs only the standard library and the host socket.)

`show` and `watch` only read. `interrupt` sends the host's own correlated native `interrupt` control
(the documented operator path in docs/claude-headless-manager.md, never a signal or kill) to the exact
execution ids named, at their current generation, with an explicit authority; queued inputs are
cancelled (their texts stay in their receipts). Partial tool effects are NOT rolled back; an authorized
interrupt is classified `interrupted / record_only`, so no automatic `continue` follows. `stop-idle`
first asks the host to close the session itself; only if the host refuses and every idle proof holds
(the restart's own process-tree proof) does it send one SIGTERM to that exact `claude` process.
"""
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from claude_headless.client import Client  # noqa: E402
from claude_headless.restart import partition_unsettled, session_name  # noqa: E402

AUTHORITY = ('Dan explicit operator interrupt (oc.sh unbog): ending a stuck turn so the guarded host restart '
             'can run')


def short(value, limit=160):
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= limit else text[:limit] + '…'


def ago(stamp):
    if not isinstance(stamp, (int, float)):
        return '?'
    seconds = int(time.time() - stamp)
    return f'{seconds // 3600}h{(seconds % 3600) // 60:02d}m ago'


def describe(record, reasons):
    labels = record.get('labels') if isinstance(record.get('labels'), dict) else {}
    retry = record.get('last_retry') or {}
    rate = record.get('last_rate_limit_info') or {}
    lines = [
        f"● {session_name(record)}",
        f"    blockers : {'; '.join(reasons)}",
        f"    state    : state={record.get('state')} owner={record.get('owner')} pid={record.get('pid')} "
        f"native={record.get('native_session_state')} turn_open={record.get('primary_turn_open')}",
        f"    work     : pending_inputs={len(record.get('pending_message_uuids') or [])} "
        f"background_tasks={len(record.get('background_tasks') or {})} "
        f"recovery={bool(record.get('pending_recovery'))} escalations={bool(record.get('pending_escalations'))}",
    ]
    if retry:
        lines.append(f"    retry    : attempt {retry.get('attempt')}/{retry.get('max_retries')} "
                     f"status={retry.get('error_status')} delay_ms={retry.get('retry_delay_ms')} "
                     f"error={short(retry.get('error'))}")
    if rate:
        lines.append(f"    ratelimit: {short(rate)} (seen {ago(record.get('last_rate_limit_at'))})")
    extra = {k: labels.get(k) for k in ('kind', 'program', 'program_core', 'registry_n') if labels.get(k)}
    for key in ('parent', 'program', 'owning_conductor', 'socket'):
        if record.get(key):
            extra[key] = record.get(key)
    if extra:
        lines.append(f"    belongs  : {short(extra, 240)}")
    lines.append(f"    last input {ago(record.get('last_input_at'))}; model {record.get('last_assistant_model')}")
    return '\n'.join(lines)


def snapshot(client):
    rows = list(client.call('list', all=True) or [])
    blocking, historical = partition_unsettled(rows)
    return rows, blocking, historical


def show(client):
    rows, blocking, historical = snapshot(client)
    health = client.call('health')
    print(f"host pid {health.get('host_pid') or health.get('pid')} · {len(rows)} sessions known · "
          f"{len(blocking)} blocking the restart · {len(historical)} proved-dead (reported only)\n")
    for record, reasons in blocking:
        print(describe(record, reasons) + '\n')
    if not blocking:
        print('Nothing blocks. Run the guarded restart now.')
    managers = [r for r in rows if r.get('role') == 'Manager' and not (r.get('owner') == 'gone'
                and r.get('state') in ('exited', 'launch_failed') and r.get('generation', 0) == 0)]
    managers.sort(key=lambda r: r.get('last_input_at') or r.get('first_launched_at') or 0, reverse=True)
    if managers:
        print('Manager conversations, newest input first (for `claude_restart.sh --resume UUID`):')
        for r in managers[:5]:
            print(f"    {r.get('native_uuid')}  {session_name(r)}  state={r.get('state')} owner={r.get('owner')} "
                  f"native={r.get('native_session_state')} last input {ago(r.get('last_input_at'))}")
    return blocking


def interrupt(client, ids):
    rows = {r.get('execution_id'): r for r in client.call('list', all=True) or []}
    for execution_id in ids:
        record = rows.get(execution_id)
        if record is None:
            print(f'✗ {execution_id}: not in the host list; skipped (copy the exact cc-… id from `show`)')
            continue
        print(f'→ interrupting {session_name(record)} …', flush=True)
        try:
            receipt = client.call('interrupt', execution_id=execution_id, generation=record.get('generation'),
                                  authority=AUTHORITY, cancel_queued=True,
                                  reason='operator unbog: stuck turn ended; guarded host restart pending')
        except Exception as error:  # the host refused: print its exact reason, never retry blindly
            print(f'✗ {execution_id}: host refused: {type(error).__name__}: {error}')
            continue
        print(f"  receipt request_id={receipt.get('request_id')} state={receipt.get('state')} "
              f"settled={(receipt.get('settlement') or {}).get('settled')}")


IDLE_PROOF = (
    ('state', lambda v: v == 'ready', 'state is ready'),
    ('owner', lambda v: v == 'host', 'owned by this host'),
    ('native_session_state', lambda v: v == 'idle', 'Claude Code reports idle'),
    ('primary_turn_open', lambda v: v is False, 'its own turn is closed'),
    ('pending_message_uuids', lambda v: not v, 'no input awaiting a result'),
    ('background_tasks', lambda v: not v, 'no registered background task'),
    ('background_unsettled', lambda v: not v, 'no unsettled background work'),
    ('pending_native_requests', lambda v: not v, 'no permission/dialog request'),
    ('native_queued_turns', lambda v: not v, 'no queued native turn'),
    ('pending_recovery', lambda v: not v, 'no scheduled recovery'),
)


def descendants(pid):
    children = {}
    for entry in os.listdir('/proc'):
        if not entry.isdigit():
            continue
        try:
            stat = Path(f'/proc/{entry}/stat').read_text()
            ppid = int(stat.rsplit(')', 1)[1].split()[1])
            cmd = Path(f'/proc/{entry}/cmdline').read_bytes().replace(b'\0', b' ').decode(errors='replace').strip()
        except (OSError, ValueError, IndexError):
            continue
        children.setdefault(ppid, []).append((int(entry), cmd))
    found, stack = [], [pid]
    while stack:
        for child in children.get(stack.pop(), []):
            found.append(child)
            stack.append(child[0])
    return found


def stop_idle(client, execution_id):
    """SIGTERM one Claude Code process that is provably idle but held only by a stale host marker.

    Mirrors the host's own restart process-tree proof (settle_for_restart): every idle condition
    holds, the PID is this conversation's own `claude` process, and its only descendant is the
    context MCP helper. Anything else refuses and changes nothing.
    """
    def record_now():
        for row in client.call('list', all=True) or []:
            if row.get('execution_id') == execution_id:
                return row
        return None

    record = record_now()
    if record is None:
        print(f'✗ {execution_id} is not in the host list; nothing done')
        return 1
    try:  # show the host's own words first; if it now closes cleanly, no signal is needed at all
        print('close_input says:', json.dumps(client.call('close_input', execution_id=execution_id,
                                                           generation=record.get('generation'))))
        print('The host accepted the close itself; no signal sent. Re-run `show` until it reads exited.')
        return 0
    except Exception as error:
        print(f'close_input refused: {error}')
    failed = [why for key, ok, why in IDLE_PROOF if not ok(record.get(key))]
    if failed:
        print('✗ NOT idle, refusing (nothing signalled): ' + '; '.join(f'NOT {w}' for w in failed))
        return 3
    pid = record.get('pid')
    try:
        cmdline = Path(f'/proc/{pid}/cmdline').read_bytes().replace(b'\0', b' ').decode(errors='replace')
    except OSError:
        print(f'✗ PID {pid} is not visible; refusing (nothing signalled)')
        return 3
    if record.get('native_uuid', '#') not in cmdline or 'claude' not in cmdline:
        print(f'✗ PID {pid} is not this conversation\'s claude process ({short(cmdline)}); refusing')
        return 3
    tree = descendants(pid)
    strays = [(p, c) for p, c in tree if 'claude_context_mcp.py' not in c]
    print(f'PID {pid}: {short(cmdline, 120)}')
    for p, c in tree:
        print(f'    child {p}: {short(c, 120)}')
    if strays:
        print('✗ a child other than the context helper is running; refusing (nothing signalled)')
        return 3
    again = record_now() or {}
    if again.get('pid') != pid or [w for k, ok, w in IDLE_PROOF if not ok(again.get(k))]:
        print('✗ the record changed while checking; refusing (nothing signalled). Run it again.')
        return 3
    print(f'All idle proofs hold (stale marker only: native_retry_active={record.get("native_retry_active")}). '
          f'Sending one SIGTERM to {pid} …', flush=True)
    os.kill(pid, 15)
    for _ in range(60):
        time.sleep(1)
        row = record_now() or {}
        if row.get('owner') == 'gone':
            print(f"✓ exited: state={row.get('state')} owner=gone — the stale markers are dropped; "
                  'Send from the pane now reconnects it on the current login.')
            return 0
    print('Signal sent; the host has not yet recorded the exit. Run `show` again in a moment.')
    return 2


def watch(client, seconds=180, interval=3):
    deadline = time.monotonic() + seconds
    while True:
        _, blocking, _ = snapshot(client)
        stamp = time.strftime('%H:%M:%S')
        if not blocking:
            print(f'{stamp} nothing blocks the guarded restart any more.')
            return 0
        print(f"{stamp} still blocking: " + ' | '.join(
            f"{r.get('execution_id')} [{r.get('role')}] {', '.join(why)}" for r, why in blocking), flush=True)
        if time.monotonic() > deadline:
            print('Stopped watching (nothing was changed). Run `show` for detail.')
            return 2
        time.sleep(interval)


# @beacon[
#   id=oc-unbog@main,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=`oc.sh unbog show|interrupt|watch|stop-idle`: the operator's by-hand way to see and clear what blocks the guarded Claude host restart (stuck retrying turns, stale markers); host-native interrupt, guarded single-process stop; runbook [ap:D8NOYW].,
# ]
def main(argv):
    if argv and argv[0] == 'stop-idle':
        client = Client(os.environ['OC_CLAUDE_HOST_SOCKET']) if os.environ.get('OC_CLAUDE_HOST_SOCKET') else Client()
        if len(argv) != 2:
            print('usage: oc.sh unbog stop-idle cc-<exact id>')
            return 2
        return stop_idle(client, argv[1])
    if not argv or argv[0] not in ('show', 'interrupt', 'watch'):
        print(__doc__)
        return 2
    client = Client(os.environ['OC_CLAUDE_HOST_SOCKET']) if os.environ.get('OC_CLAUDE_HOST_SOCKET') else Client()
    if argv[0] == 'show':
        show(client)
    elif argv[0] == 'interrupt':
        if len(argv) < 2:
            print('name the exact cc-… execution ids to interrupt (from `show`)')
            return 2
        interrupt(client, argv[1:])
    else:
        seconds = int(argv[1]) if len(argv) > 1 and argv[1].isdigit() else 180
        return watch(client, seconds=seconds)
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))

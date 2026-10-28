#!/usr/bin/env python3
"""Standalone Claude host and file-first operator client; independent of the pane."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys

from claude_headless.client import Client
from claude_headless.host import Host, SOCKET_PATH, STATE_ROOT, owner_lock, witness
from claude_headless.store import private_dir
from claude_headless.host_start import start_admission


# @beacon[
#   id=opc@claude_host_main,
#   slice_labels=opc-basic,opc-claude-restart,
#   kind=ast,
#   comment=Maintained host start/serve: holds existing host.lock across Popen by inherited FD, retains operation/start identity before reconstruction and returns start_requested only; exact file-first client never drives a terminal.,
# ]
def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--socket-path', type=Path, default=SOCKET_PATH)
    parser.add_argument('--state-root', type=Path, default=STATE_ROOT)
    parser.add_argument('--operation-id', default=None, help=argparse.SUPPRESS)
    parser.add_argument('--start-token', default=None, help=argparse.SUPPRESS)
    parser.add_argument('--_host-lock-fd', type=int, default=None, help=argparse.SUPPRESS)
    parser.add_argument('--silence-threshold', type=float, default=600,
                        help='observation seconds only; NEVER interrupts/kills/resends')
    parser.add_argument('--collection-threshold', type=float, default=10800,
                        help='continuous verified native settlement seconds before proof-gated EOF (default 3 hours)')
    parser.add_argument('--collection-cadence', type=float, default=180,
                        help='seconds between collector passes (each pass checks every eligible session)')
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('serve', help='foreground service; stdin/pane are not its lifetime')
    commands.add_parser('start', help='explicit detached start; does not reuse/restart any existing host')
    call = commands.add_parser('call', help='exact method; JSON params read from private file or stdin')
    call.add_argument('method')
    call.add_argument('--file', type=Path)
    args = parser.parse_args()
    os.umask(0o077)
    try:
        if args.command == 'serve':
            asyncio.run(Host(args.state_root, args.socket_path, silence_threshold=args.silence_threshold,
                collection_threshold=args.collection_threshold, collection_cadence=args.collection_cadence,
                host_lock_fd=args._host_lock_fd, operation_id=args.operation_id, start_token=args.start_token).serve())
        elif args.command == 'start':
            root = private_dir(args.state_root)
            with start_admission(root, args.operation_id):
                lock_fd = owner_lock(root / 'host.lock')
            try:
                with (root / 'service.log').open('ab') as log:
                    (root / 'service.log').chmod(0o600)
                    process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()),
                        '--socket-path', str(args.socket_path), '--state-root', str(root),
                        '--silence-threshold', str(args.silence_threshold),
                        '--collection-threshold', str(args.collection_threshold),
                        '--collection-cadence', str(args.collection_cadence),
                        '--_host-lock-fd', str(lock_fd),
                        *(['--operation-id', args.operation_id] if args.operation_id else []),
                        *(['--start-token', args.start_token] if args.start_token else []), 'serve'],
                        pass_fds=(lock_fd,), stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
            finally:
                # Close, NOT LOCK_UN: the child's inherited open-file description keeps
                # this same lock for its lifetime, even if the launcher dies before reply.
                os.close(lock_fd)
            print(json.dumps({'host_pid': process.pid, 'status': 'start_requested',
                              'process_witness': witness(process.pid),
                              'operation_id': args.operation_id, 'start_token': args.start_token,
                              'receipt': str(root / 'host-start-receipt.json'),
                              'socket_path': str(args.socket_path), 'service_log': str(root / 'service.log')}))
        else:
            params = json.loads(args.file.read_text(encoding='utf-8') if args.file else sys.stdin.read())
            print(json.dumps(Client(args.socket_path).call(args.method, **params), ensure_ascii=False))
    except KeyboardInterrupt:
        # Explicit host operator stop is not a turn timeout. CLI EOF/ownership may
        # remain unknown; restart reconciles witnesses and never blindly respawns.
        return 130
    except Exception as error:
        if args.command == 'serve':
            try:
                from claude_headless.host_start import write_start_receipt
                write_start_receipt(args.state_root, args.socket_path, operation_id=args.operation_id,
                    start_token=args.start_token, phase='initialization_failed', error=type(error).__name__)
            except Exception:
                pass  # optional receipt failure never masks the original initialization error
        # The host's own refusal reason (e.g. "cannot close input with unsettled work: native retry active") is
        # what the operator needs in an emergency; it carries no message bodies. Capped so a long one stays readable.
        reason = str(error).strip()
        reason = reason if len(reason) <= 2000 else reason[:2000] + ' …'
        print(f'{type(error).__name__}: host operation failed: {reason or "(no reason given)"}; '
              'inspect private state/receipt for detail', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

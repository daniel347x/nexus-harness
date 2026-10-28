#!/usr/bin/env python3
"""Inspect or defer/cancel ONE independent Claude recovery-notice episode.

oc.sh claude_notices list [--json]
oc.sh claude_notices defer EXACT_EPISODE [--authority TEXT] [--json]
oc.sh claude_notices cancel EXACT_EPISODE [--authority TEXT] [--json]

No session/task stop, account change, Send or alarm re-arm. A retained admitted input remains
admitted even when its alarm is deferred. Other episodes keep the standing five-hour behavior.
"""
import argparse
import json
import os

from claude_headless.client import Client


def describe_episode(row):
    controlled = row.get('status') in ('deferred_by_operator', 'cancelled_by_operator')
    receipt = row.get('receipt') or {}
    admitted = row.get('send_admitted') or bool(receipt)
    status = row.get('status', 'unknown')
    deadline = 'NOT ARMED' if controlled else 'due_at=' + str(row.get('due_at'))
    delivery = ('; exact Send already admitted (' + str(row.get('delivery_status') or receipt.get('status') or
                 row.get('receipt_status') or 'pending') + '), never replayed') if admitted else ''
    return f"{row.get('episode_id')}: {status}; {deadline}; message={row.get('message_uuid')}{delivery}"


# @beacon[
#   id=opc@claude_notice_control,
#   slice_labels=opc-basic,opc-claude-seats,
#   kind=ast,
#   comment=Operator CLI inspects or durably defers/cancels one exact independent recovery episode; displays not-armed versus admitted receipts honestly and never stops sessions or rearms another episode.,
# ]
def main(argv=None, *, client=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('action', choices=('list', 'defer', 'cancel'))
    parser.add_argument('episode', nargs='?')
    parser.add_argument('--authority', default=None)
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args(argv)
    if (args.action == 'list') != (args.episode is None):
        parser.error('defer/cancel require one exact episode; list takes none')
    client = client or (Client(os.environ['OC_CLAUDE_HOST_SOCKET']) if os.environ.get('OC_CLAUDE_HOST_SOCKET') else Client())
    try:
        if args.action == 'list':
            rows = client.call('quota_checks')
            answer = rows
        else:
            answer = client.call('quota_check_control', episode=args.episode, action=args.action,
                                 authority=args.authority or f'operator CLI: {args.action} episode {args.episode}')
            rows = [answer.get('display') or answer.get('record') or answer]
        if args.json:
            print(json.dumps(answer, ensure_ascii=False, indent=2))
        else:
            print('\n'.join(describe_episode(row) for row in rows) or 'No recovery-notice episodes.')
        return 0
    except (ValueError, RuntimeError, OSError) as error:
        print(f'Notice control failed: {error}')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())

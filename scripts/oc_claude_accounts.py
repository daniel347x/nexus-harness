#!/usr/bin/env python3
"""Enroll and inspect native Claude Max account folders (selection uses claude_restart)."""
import argparse
import json
from claude_headless import accounts


# @beacon[
#   id=opc@claude_accounts_main,
#   slice_labels=opc-basic,opc-claude-seats,
#   kind=ast,
#   comment=oc.sh claude_accounts enroll/list/status: native one-time terminal login and secret-free actual account mapping; never changes the selector or logs out the default account.,
# ]
def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='verb', required=True)
    enroll = sub.add_parser('enroll')
    enroll.add_argument('label', choices=accounts.LABELS)
    enroll.add_argument('--email', required=True)
    for name in ('list', 'status'):
        sub.add_parser(name).add_argument('--json', action='store_true')
    args = parser.parse_args(argv)
    try:
        result = accounts.enroll(args.label, args.email) if args.verb == 'enroll' else accounts.status()
        print(json.dumps(result, indent=2))
        return 0
    except (ValueError, OSError, RuntimeError) as error:
        print(str(error))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())

"""Owner console: status/enable/disable project-scoped OpenSpec/SDD auto-approval."""
import argparse
import json
import sqlite3
import sys
from spec_approval_queue import ApprovalQueue, QueueError
from spec_tools import SpecError


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='action', required=True)
    for action in ('status', 'enable', 'disable'):
        command = commands.add_parser(action)
        command.add_argument('project', help='Existing project relative to the configured workspace root')
        if action == 'enable':
            expiry = command.add_mutually_exclusive_group()
            expiry.add_argument('--hours', type=float, help='Default: 24 hours')
            expiry.add_argument('--permanent', action='store_true', help='Until explicitly disabled')
    args = parser.parse_args(argv)
    try:
        queue = ApprovalQueue()
        if args.action == 'status':
            result = queue.project_policy_status(args.project)
        else:
            result = queue.project_policy_console(args.project, args.action,
                hours=getattr(args, 'hours', None), permanent=getattr(args, 'permanent', False))
        print(json.dumps({'ok': True, 'data': result}, ensure_ascii=True))
        return 1 if result.get('reason') == 'owner_declined' else 0
    except (QueueError, SpecError) as error:
        print(json.dumps({'ok': False, 'error': {'code': error.code, 'message': str(error)}}, ensure_ascii=True))
        return 2
    except (OSError, sqlite3.Error):
        print(json.dumps({'ok': False, 'error': {'code': 'POLICY_UNAVAILABLE',
              'message': 'Policy storage or project identity unavailable'}}, ensure_ascii=True))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())

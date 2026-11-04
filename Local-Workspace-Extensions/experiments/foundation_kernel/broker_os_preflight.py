"""Read-only Windows broker isolation preflight; never changes permissions.

Example (ONLY after trusted host provisions distinct OS principals):
  python broker_os_preflight.py --broker-sid S-... --agent-sid S-... \
      --protected-path C:/ProtectedBroker/State \
      --protected-path C:/ProtectedBroker/Policy.json \
      --protected-path C:/ProtectedBroker/broker.exe

Always reports production_ready=false because ACL inspection alone cannot
prove secure parent authentication, host registry reduction or real receipts.
"""
from __future__ import annotations
import argparse
import json
import sys

try:
    from .windows_isolation import preflight_isolation
except ImportError:
    from windows_isolation import preflight_isolation


def main(argv=None) -> int:
    parser=argparse.ArgumentParser(description="Read-only Windows broker OS guard")
    parser.add_argument("--broker-sid",required=True)
    parser.add_argument("--agent-sid",required=True)
    parser.add_argument("--protected-path",action="append",required=True)
    args=parser.parse_args(argv)
    try:
        report=preflight_isolation(
            broker_sid=args.broker_sid,agent_sid=args.agent_sid,
            protected_paths=args.protected_path)
    except Exception:
        report={"filesystem_preflight_passed":False,
                "ready_for_production":False,
                "findings":["OS_PREFLIGHT_UNEXPECTED_FAILURE"],
                "read_only":True}
    print(json.dumps(report,sort_keys=True,ensure_ascii=True))
    # This CLI cannot authorize rollout. It is diagnosis, not owner approval.
    return 0 if report["filesystem_preflight_passed"] else 2


if __name__=="__main__":
    raise SystemExit(main())

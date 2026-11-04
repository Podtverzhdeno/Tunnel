"""P085 stand-alone *protected installation* reviewer pipe entrypoint.

Do NOT run from Local-Workspace-Extensions/ or expose as an MCP run_script tool.
The ENTIRE service package must first be copied by the owner to a protected
Windows directory under its own service SID, with the private config/key/ledger
in owner-controlled ACLs. The untrusted chat MCP must NOT read or edit this
installation, its files, main coordinator DB or canonical Python sources.
No account creation, secret issuance, ACL modification or key output is done.

--doctor: non-mutating validation of local service identity and paths.
--serve-one: service exactly one Windows named-pipe request; the only possible
business action is reserving a signed, SHA+lease-bound review grant. NO MERGE.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from workspace_mcp.host_pipe_peer import current_process_sid
from workspace_mcp.review_grants import GrantDenied, GrantLedger
from workspace_mcp.review_grant_broker import BrokerPolicy, ReviewGrantBroker
from workspace_mcp.review_pipe_server import serve_one
from workspace_mcp.review_finalizer import ProtectedReviewFinalizer
from workspace_mcp.trusted_journal_loader import source_sha, load_pinned_journal
from workspace_mcp.owner_cutover_gate import require_owner_cutover, service_private_root
from workspace_mcp.review_qa_attestation import QATrustPolicy

ROOT = Path(__file__).resolve().parent
PRIVATE = service_private_root(ROOT)
CONFIG = PRIVATE / "config.json"
KEY = PRIVATE / "review-key.bin"
LEDGER = PRIVATE / "ledger.sqlite3"
MAX_CONFIG = 16384


def _safe_read(path: Path, max_bytes: int) -> bytes:
    path=Path(path)
    cur=path.anchor and Path(path.anchor)
    if cur is None or not path.is_absolute():
        raise GrantDenied("PRIVATE_PATH_MUST_BE_ABSOLUTE")
    for part in path.parts[1:]:
        cur=cur/part
        if cur.is_symlink() or getattr(cur,"is_junction",lambda:False)():
            raise GrantDenied("PRIVATE_LINKED_PATH")
    if not path.is_file() or path.stat().st_size > max_bytes:
        raise GrantDenied("PRIVATE_RESOURCE_MISSING")
    with path.open("rb") as fd:
        contents=fd.read(max_bytes+1)
    if len(contents)>max_bytes:
        raise GrantDenied("PRIVATE_RESOURCE_OVERSIZED")
    return contents


def load_configuration(*, service_root: Path=ROOT) -> tuple[BrokerPolicy, Path, Path, bytes, Path]:
    root=Path(service_root).resolve(strict=True)
    private=service_private_root(root)
    config_path=private/"config.json"
    key_path=private/"review-key.bin"
    ledger_path=private/"ledger.sqlite3"
    qa_key_path=private/"qa-verify-key.bin"
    raw=_safe_read(config_path,MAX_CONFIG)
    try:
        config=json.loads(raw.decode("utf-8"))
    except (UnicodeError,ValueError) as exc:
        raise GrantDenied("PRIVATE_CONFIG_INVALID") from exc
    fields={"version","service_sid","reviewer_client_sid","untrusted_mcp_sid",
            "reviewer_actor","workspace","owner_principals","project_root",
            "qa_runner_actor","qa_runner_environment_sha256",
            "trusted_journal_sha256"}
    if (type(config) is not dict or set(config)!=fields
            or type(config["version"]) is not int or config["version"]!=1
            or type(config["project_root"]) is not str):
        raise GrantDenied("PRIVATE_CONFIG_INVALID")
    policy=BrokerPolicy(config["service_sid"],config["reviewer_client_sid"],
                        config["untrusted_mcp_sid"],config["reviewer_actor"],
                        config["workspace"],config["owner_principals"])
    policy.validate()
    qa_policy=QATrustPolicy(config["qa_runner_actor"],
                            config["qa_runner_environment_sha256"])
    qa_policy.validate()
    project=Path(config["project_root"])
    if not project.is_absolute() or not project.is_dir() or project.is_symlink():
        raise GrantDenied("PROJECT_ROOT_INVALID")
    project=project.resolve(strict=True)
    # Local Workspace MCP's registered root is the PARENT of this project.
    # Even a sibling directory below IdeaProjects is fully writable through
    # its broad alias. Refuse everything inside that entire root, not merely
    # inside Local-Workspace-Extensions itself.
    shared_workspace_root = project.parent
    if (root==project or root.is_relative_to(project)
            or project.is_relative_to(root)
            or root==shared_workspace_root
            or root.is_relative_to(shared_workspace_root)
            or private.is_relative_to(project)):
        raise GrantDenied("PROTECTED_INSTALL_MUST_BE_OUTSIDE_PROJECT")
    actual=current_process_sid()
    if actual != policy.service_sid:
        raise GrantDenied("BROKER_PROCESS_IDENTITY_MISMATCH")
    key=_safe_read(key_path,4096)
    qa_key=_safe_read(qa_key_path,4096)
    if (len(key)<32 or len(key)>512 or len(qa_key)<32 or len(qa_key)>512
            or key==qa_key):
        raise GrantDenied("PRIVATE_KEY_INVALID")
    if (ledger_path.is_symlink() or getattr(ledger_path,"is_junction",lambda:False)()
            or (ledger_path.exists() and not ledger_path.is_file())):
        raise GrantDenied("LEDGER_PATH_INVALID")
    expected_journal=config["trusted_journal_sha256"]
    if (type(expected_journal) is not str or len(expected_journal)!=64
            or any(c not in "0123456789abcdef" for c in expected_journal)
            or source_sha(root)!=expected_journal):
        raise GrantDenied("TRUSTED_JOURNAL_PIN_MISMATCH")
    # This does NOT assert that OS DACLs have been verified from the worker
    # account. That must be a separate actual cross-principal test.
    return (policy, project, project/".agent-team"/"team.sqlite3",
            key, ledger_path, qa_policy, qa_key, expected_journal)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    modes=parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--doctor",action="store_true")
    modes.add_argument("--serve-one",action="store_true")
    modes.add_argument("--accept-one",action="store_true",
                       help="Privileged finalization; requires protected owner cutover attestation")
    args=parser.parse_args(argv)
    try:
        policy,project,db,key,ledger_path,qa_policy,qa_key,journal_pin=load_configuration()
        if args.doctor:
            print(json.dumps({"ok":True,"schema":"p085-protected-broker-doctor-v1",
                "service_sid_matches_process":True,
                "service_outside_project":True,
                "os_acl_cross_principal_verified":False,
                "privileged_reviewer_accept_enabled":False,
                "next":"Run independent worker/reviewer SID ACL E2E; do not enable merge."}))
            return 0
        broker=ReviewGrantBroker(policy,project=project,coordinator_db=db,
                                 ledger=GrantLedger(ledger_path,key))
        finalizer=None
        if args.accept_one:
            # A protected-file operator acknowledgement is still NOT a
            # substitute for actual two-principal OS ACL negative E2E tests.
            require_owner_cutover(ROOT,policy=policy,project=project,
                                  journal_sha256=journal_pin,qa_policy=qa_policy)
            pinned=load_pinned_journal(ROOT,journal_pin)
            finalizer=ProtectedReviewFinalizer(
                project=project,database=db,workspace=policy.workspace,
                owner_principals=policy.owner_principals,
                trusted_reviewer_actor=policy.reviewer_actor,
                ledger=broker._ledger,qa_key=qa_key,qa_policy=qa_policy,
                journal_factory=pinned)
        result=serve_one(policy,broker,qa_policy=qa_policy,
                         qa_verification_key=qa_key,
                         trusted_finalizer=finalizer)
        performed=(result.get("ok") is True and
                   result.get("result",{}).get("status")=="done")
        print(json.dumps({"handled":True,
                          "grant_processed":result.get("ok") is True,
                          "formal_review_performed":performed}))
        return 0 if result.get("ok") is True else 3
    except (GrantDenied,OSError,ValueError,RuntimeError):
        # No secret or private filesystem paths may leak on failure.
        print(json.dumps({"ok":False,"error_code":"PROTECTED_BROKER_NOT_READY"}))
        return 2

if __name__=="__main__":
    raise SystemExit(main())

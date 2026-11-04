"""P085 reviewer broker core: deny spoofed identity and one-shot grants.

Disposable test-only identities/keys and local SQLite. The native Windows
named-pipe SID API is separately exercised by test_host_pipe_peer.py.
"""
from __future__ import annotations

import concurrent.futures
import hashlib
import hmac
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from workspace_mcp.review_grants import (
    GrantDenied, GrantLedger, SCHEMA, ISSUER, AUDIENCE, canonical,
)
from workspace_mcp.review_grant_broker import BrokerPolicy, ReviewGrantBroker
from workspace_mcp.review_target_snapshot import snapshot

SERVER = "S-1-5-21-1000-1000-1000-101"
REVIEWER = "S-1-5-21-1000-1000-1000-102"
UNTRUSTED = "S-1-5-21-1000-1000-1000-103"
INTRUDER = "S-1-5-21-1000-1000-1000-104"
KEY = b"test-only-p085-broker-key-not-production!"
ACTOR = "reviewer:verified"
AUTHOR = {f"developer-{i}": f"developer:principal-{i}" for i in range(1, 5)}


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


class BrokerTests(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory(prefix="p085-broker-fixture-")
        self.addCleanup(t.cleanup)
        self.root = Path(t.name)
        self.db = self.root / "coordinator.sqlite3"
        self.spec = "openspec/changes/demo/specs/review/spec.md"
        self.sandbox = ".agent-team/workspaces/developer-1/P152"
        self.source = "processor_example.py"
        self.test_file = "test_processor_example.py"
        for name, data in [
            (self.spec, b"Canonical spec\n"),
            (self.sandbox + "/" + self.source, b"def foo(): return 1\n"),
            (self.sandbox + "/" + self.test_file, b"def test_foo(): assert True\n"),
        ]:
            p = self.root / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
        self.baseline = {"__spec_sha__": sha((self.root / self.spec).read_bytes()),
                         self.source: None, self.test_file: None}
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute("CREATE TABLE tasks(task_id TEXT PRIMARY KEY,"
                       " owner TEXT,status TEXT,review_token TEXT,review_until TEXT,"
                       " revision INTEGER,sandbox TEXT,deliverables TEXT,"
                       " baseline TEXT,spec_ref TEXT)")
            db.execute("INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?,?,?)", (
                "P152", "developer-1", "reviewing", "token-one-test-only-012345",
                (datetime.now(timezone.utc)+timedelta(minutes=10)).isoformat(),
                7, self.sandbox, json.dumps([self.source,self.test_file]),
                json.dumps(self.baseline), self.spec+" / requirement"))
        self.policy = BrokerPolicy(SERVER, REVIEWER, UNTRUSTED, ACTOR,
                                   "projects", AUTHOR.copy())
        self.ledger = GrantLedger(self.root / "private" / "ledger.db", KEY)
        (self.root / "private").mkdir()
        self.peer = REVIEWER
        self.broker = self.make_broker()

    def make_broker(self, *, peer=None, service_sid=SERVER, policy=None):
        return ReviewGrantBroker(policy or self.policy, project=self.root,
                                 coordinator_db=self.db, ledger=self.ledger,
                                 peer_sid_resolver=lambda handle: self.peer if peer is None else peer,
                                 service_sid_resolver=lambda: service_sid)

    def env(self, *, nonce="a"*32, action="accept"):
        target=snapshot(self.root,self.db,"P152",workspace="projects",
                        owner_principals=AUTHOR)
        moment=int(datetime.now(timezone.utc).timestamp())
        p = {"schema":SCHEMA, "issuer":ISSUER, "audience":AUDIENCE,
             "nonce":nonce, "actor_id":ACTOR, "workspace":"projects",
             "task_id":"P152", "action":action, "revision":target.revision,
             "spec_sha256":target.spec_sha256,
             "source_bundle_sha256":target.source_bundle_sha256,
             "review_lease_sha256":target.review_lease_sha256,
             "issued_at":moment-5, "expires_at":moment+60}
        return {"payload":p, "mac":hmac.new(KEY,canonical(p),hashlib.sha256).hexdigest()}

    def request(self, env=None):
        return {"task_id":"P152", "signed_grant":self.env() if env is None else env}

    def test_scoped_reservation_is_not_review_accept(self):
        result=self.broker.reserve(self.request(), connected_pipe_handle=19)
        self.assertEqual(result["state"],"RESERVED_REQUIRES_PRIVILEGED_QA")
        self.assertFalse(result["formal_review_performed"])
        self.assertFalse(result["source_merged"])

    def test_replay_denied_after_restart(self):
        request=self.request()
        self.broker.reserve(request, connected_pipe_handle=19)
        restarted=self.make_broker()
        with self.assertRaisesRegex(GrantDenied,"GRANT_ALREADY_SPENT"):
            restarted.reserve(request, connected_pipe_handle=19)

    def test_untrusted_and_other_sids_denied_before_claim(self):
        request=self.request()
        for sid,expected in ((UNTRUSTED,"UNTRUSTED_AGENT_PRINCIPAL"),
                             (INTRUDER,"REVIEWER_PRINCIPAL_NOT_AUTHORIZED")):
            with self.subTest(sid=sid):
                self.peer=sid
                with self.assertRaisesRegex(GrantDenied,expected):
                    self.broker.reserve(request, connected_pipe_handle=19)
        self.assertEqual(self.ledger.state("a"*32),"UNKNOWN")

    def test_role_actor_sid_and_token_cannot_be_supplied_in_request(self):
        request=self.request()
        for key,value in [("caller_role","architect-reviewer"),
                          ("actor_id",ACTOR),("sid",REVIEWER),
                          ("review_token","token-one-test-only-012345"),
                          ("trusted", True)]:
            with self.subTest(key=key), self.assertRaisesRegex(
                    GrantDenied,"INVALID_BROKER_REQUEST"):
                self.broker.reserve({**request,key:value},connected_pipe_handle=19)
        self.assertEqual(self.ledger.state("a"*32),"UNKNOWN")

    def test_missing_signed_grant_fails_closed(self):
        with self.assertRaisesRegex(GrantDenied,"INVALID_BROKER_REQUEST"):
            self.broker.reserve({"task_id":"P152"},connected_pipe_handle=19)

    def test_broker_process_identity_mismatch_denied_at_startup(self):
        with self.assertRaisesRegex(GrantDenied,"BROKER_PROCESS_IDENTITY_MISMATCH"):
            self.make_broker(service_sid=UNTRUSTED)

    def test_config_same_principal_denied_at_startup(self):
        unsafe=BrokerPolicy(SERVER,REVIEWER,SERVER,ACTOR,"projects",AUTHOR.copy())
        with self.assertRaisesRegex(GrantDenied,"OS_PRINCIPALS_NOT_SEPARATED"):
            self.make_broker(policy=unsafe)

    def test_author_reviewer_identity_overlap_denied_at_startup(self):
        unsafe=BrokerPolicy(SERVER,REVIEWER,UNTRUSTED,ACTOR,"projects",
                            {**AUTHOR,"developer-2":ACTOR})
        with self.assertRaisesRegex(GrantDenied,"UNTRUSTED_AUTHOR_MAPPING"):
            self.make_broker(policy=unsafe)

    def test_test_source_drift_fails_before_ledger_reservation(self):
        request=self.request()
        (self.root/self.sandbox/self.test_file).write_bytes(b"def test_foo(): assert False\n")
        with self.assertRaisesRegex(GrantDenied,"STALE_OR_CROSS_TASK_SCOPE"):
            self.broker.reserve(request,connected_pipe_handle=19)
        self.assertEqual(self.ledger.state("a"*32),"UNKNOWN")

    def test_review_lease_rotation_fails_before_ledger(self):
        request=self.request()
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute("UPDATE tasks SET review_token='new-token-0123456789'"
                       " WHERE task_id='P152'")
        with self.assertRaisesRegex(GrantDenied,"STALE_OR_CROSS_TASK_SCOPE"):
            self.broker.reserve(request,connected_pipe_handle=19)

    def test_concurrent_duplicate_grants_only_one_success(self):
        req=self.request()
        def attempt(_):
            try:
                self.broker.reserve(req,connected_pipe_handle=19)
                return "RESERVED"
            except GrantDenied as exc:
                return str(exc)
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results=list(pool.map(attempt,range(8)))
        self.assertEqual(results.count("RESERVED"),1,results)
        self.assertEqual(results.count("GRANT_ALREADY_SPENT"),7,results)

    def test_pending_merge_denied(self):
        req=self.request()
        (self.root/".agent-team/merge-intents/P152").mkdir(parents=True)
        with self.assertRaisesRegex(GrantDenied,"REVIEW_STATE_NOT_SAFE"):
            self.broker.reserve(req,connected_pipe_handle=19)

    def test_signature_forgery_denied(self):
        req=self.request()
        req["signed_grant"]["payload"]["revision"]+=1
        with self.assertRaisesRegex(GrantDenied,"INVALID_SIGNATURE"):
            self.broker.reserve(req,connected_pipe_handle=19)

if __name__ == "__main__":
    unittest.main()

"""Offline P085 integration: trusted snapshot -> scoped signature -> one-use claim.

All state is disposable. HMAC issuer here is *test-only*, not an exposed MCP
tool or remotely accessible owner approval. No actual coordinator review runs.
"""
from __future__ import annotations
import hashlib
import hmac
import json
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from test_review_target_snapshot import HostSnapshotTests, NOW
from workspace_mcp.review_grants import (
    GrantDenied, GrantLedger, SCHEMA, ISSUER, AUDIENCE, canonical, verify,
)
from workspace_mcp.review_target_snapshot import snapshot

KEY=b"p085-offline-test-only-key-not-production!!"
ACTOR="reviewer:trusted-host-principal"


class OfflineGrantFlowTests(unittest.TestCase):
    def setUp(self):
        self.fixture=HostSnapshotTests("test_snapshot_exact_file_bundle_and_author")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        t=tempfile.TemporaryDirectory(prefix="p085-ledger-test-")
        self.addCleanup(t.cleanup)
        self.ledger=GrantLedger(Path(t.name)/"grants.sqlite3",KEY)

    def grant(self, target=None, *, actor=ACTOR, nonce="a"*32):
        t=target or self.fixture.run_snapshot()
        payload={
            "schema":SCHEMA,"issuer":ISSUER,"audience":AUDIENCE,
            "nonce":nonce,"actor_id":actor,"workspace":t.workspace,
            "task_id":t.task_id,"action":"accept","revision":t.revision,
            "spec_sha256":t.spec_sha256,
            "source_bundle_sha256":t.source_bundle_sha256,
            "review_lease_sha256":t.review_lease_sha256,
            "issued_at":int(NOW.timestamp())-5,
            "expires_at":int(NOW.timestamp())+30,
        }
        return {"payload":payload,
                "mac":hmac.new(KEY,canonical(payload),hashlib.sha256).hexdigest()}

    def reserve(self,envelope,target):
        return self.ledger.reserve(envelope,authenticated_actor=ACTOR,
                                   target=target,now=int(NOW.timestamp()))

    def test_full_readonly_snapshot_and_oneuse_ledger(self):
        t=self.fixture.run_snapshot()
        signed=self.grant(t)
        self.assertEqual(self.reserve(signed,t)["status"],
                         "RESERVED_REQUIRES_PRIVILEGED_QA")
        with self.assertRaisesRegex(GrantDenied,"GRANT_ALREADY_SPENT"):
            self.reserve(signed,t)

    def test_test_file_rewrite_invalidates_signature_scope(self):
        t=self.fixture.run_snapshot()
        envelope=self.grant(t)
        (self.fixture.root/self.fixture.sandbox/self.fixture.test).write_bytes(
            b"def test(): assert False\n")
        new=self.fixture.run_snapshot()
        with self.assertRaisesRegex(GrantDenied,"STALE_OR_CROSS_TASK_SCOPE"):
            self.reserve(envelope,new)
        self.assertEqual(self.ledger.state("a"*32),"UNKNOWN")

    def test_source_rewrite_invalidates_signature_scope(self):
        t=self.fixture.run_snapshot()
        envelope=self.grant(t)
        (self.fixture.root/self.fixture.sandbox/self.fixture.prod).write_bytes(
            b"def f(): return 9\n")
        new=self.fixture.run_snapshot()
        with self.assertRaisesRegex(GrantDenied,"STALE_OR_CROSS_TASK_SCOPE"):
            self.reserve(envelope,new)

    def test_review_lease_rotation_invalidates_signature_scope(self):
        t=self.fixture.run_snapshot()
        envelope=self.grant(t)
        self.fixture.update(review_token="z"*30)
        new=self.fixture.run_snapshot()
        with self.assertRaisesRegex(GrantDenied,"STALE_OR_CROSS_TASK_SCOPE"):
            self.reserve(envelope,new)

    def test_pending_merge_blocks_consumer_even_with_valid_signature(self):
        t=self.fixture.run_snapshot()
        envelope=self.grant(t)
        (self.fixture.root/".agent-team/merge-intents/P152").mkdir(parents=True)
        pending=self.fixture.run_snapshot()
        self.assertTrue(pending.merge_intent_pending)
        with self.assertRaisesRegex(GrantDenied,"REVIEW_STATE_NOT_SAFE"):
            self.reserve(envelope,pending)

    def test_expired_lease_fails_before_grant(self):
        t=self.fixture.run_snapshot()
        envelope=self.grant(t)
        self.fixture.update(review_until=(NOW-timedelta(seconds=1)).isoformat())
        with self.assertRaisesRegex(GrantDenied,"REVIEW_LEASE_NOT_ACTIVE"):
            self.fixture.run_snapshot()
        self.assertEqual(self.ledger.state("a"*32),"UNKNOWN")

    def test_self_review_denied(self):
        t=self.fixture.run_snapshot()
        signer=t.author_actor_id
        env=self.grant(t,actor=signer)
        with self.assertRaisesRegex(GrantDenied,"SELF_REVIEW_FORBIDDEN"):
            self.ledger.reserve(env,authenticated_actor=signer,target=t,
                                now=int(NOW.timestamp()))

    def test_no_formal_accept_or_mutation_of_candidate(self):
        original=(self.fixture.root/self.fixture.sandbox/self.fixture.prod).read_bytes()
        t=self.fixture.run_snapshot()
        result=self.reserve(self.grant(t),t)
        self.assertFalse(result["formal_review_performed"])
        self.assertEqual(original,(self.fixture.root/self.fixture.sandbox/self.fixture.prod).read_bytes())


if __name__=="__main__":
    unittest.main()

"""P085 grant validation, scoped identity and durable one-use fencing.

All fixtures use disposable OS temp directories. Never touches live MCP host,
live coordinator or a real signing key. An external trusted issuer is REQUIRED.
"""
from __future__ import annotations

import concurrent.futures
import hashlib
import hmac
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from workspace_mcp.review_grants import (
    AUDIENCE, ISSUER, SCHEMA, GrantDenied, GrantLedger,
    ReviewTarget, canonical, verify,
)

KEY = b"test-only-secret-unsafe-for-production!!!"
NOW = 1793600000
A = "owner:reviewer"
AUTHOR = "user:developer1"
H = "a" * 64
J = "b" * 64


def fixture(*, action="accept"):
    p = dict(
        schema=SCHEMA, issuer=ISSUER, audience=AUDIENCE, nonce="c" * 32,
        actor_id=A, workspace="projects", task_id="P152", action=action,
        revision=4, spec_sha256=H, source_bundle_sha256=J,
        review_lease_sha256="e"*64,
        issued_at=NOW - 5, expires_at=NOW + 30,
    )
    return p


def sign(p, key=KEY):
    return {"payload": p, "mac": hmac.new(key, canonical(p), hashlib.sha256).hexdigest()}


def target():
    return ReviewTarget("projects", "P152", 4, H, J, "e"*64, True, False, AUTHOR)


class GrantTests(unittest.TestCase):
    def valid(self, envelope=None, **kwargs):
        return verify(envelope or sign(fixture()), KEY, authenticated_actor=A,
                      target=kwargs.get("target", target()), now=NOW)

    def denied(self, field, value, message):
        p = fixture()
        p[field] = value
        with self.assertRaisesRegex(GrantDenied, message):
            self.valid(sign(p))

    def test_valid_scope_is_only_verification_not_accept(self):
        r = self.valid()
        self.assertEqual(r["task_id"], "P152")
        self.assertEqual(r["action"], "accept")
        self.assertNotIn("accepted", r)

    def test_key_is_not_optional(self):
        for key in (b"", b"short", None, "unsafe"):
            with self.subTest(key=key):
                with self.assertRaisesRegex(GrantDenied, "TRUSTED_KEY_UNAVAILABLE"):
                    verify(sign(fixture()), key, authenticated_actor=A, target=target(), now=NOW)

    def test_invalid_forged_signature(self):
        p = fixture()
        signed = sign(p)
        p["task_id"] = "P153"
        with self.assertRaisesRegex(GrantDenied, "INVALID_SIGNATURE"):
            self.valid(signed)

    def test_actor_mismatch_and_anonymous_denied(self):
        for actor in ("owner:someone_else", "developer-1", "", None):
            with self.subTest(actor=actor), self.assertRaises(GrantDenied):
                verify(sign(fixture()), KEY, authenticated_actor=actor,
                       target=target(), now=NOW)

    def test_cross_scope_revision_hashes_and_task_rejected(self):
        for change in (
            {"workspace": "other"}, {"task_id": "P153"},
            {"revision": 5}, {"spec_sha256": "f" * 64},
            {"source_bundle_sha256": "f" * 64},
            {"review_lease_sha256": "f" * 64},
        ):
            with self.subTest(change=change):
                with self.assertRaisesRegex(GrantDenied, "STALE_OR_CROSS_TASK_SCOPE"):
                    self.valid(sign({**fixture(), **change}))

    def test_lease_rotation_fences_old_signed_scope(self):
        with self.assertRaisesRegex(GrantDenied,"STALE_OR_CROSS_TASK_SCOPE"):
            self.valid(target=replace(target(),review_lease_sha256="f"*64))

    def test_scope_and_action_malformed(self):
        for field, value in (("nonce", "../x"), ("task_id", "P152;DROP"),
                             ("action", "seed"), ("action", ["accept"]),
                             ("revision", True), ("revision", -1),
                             ("spec_sha256", "a" * 63),
                             ("workspace", "../workspace")):
            with self.subTest(field=field, val=value):
                with self.assertRaises(GrantDenied):
                    self.valid(sign({**fixture(), field: value}))

    def test_expired_future_and_too_long_grants(self):
        for change in (
            {"expires_at": NOW}, {"issued_at": NOW - 301},
            {"issued_at": NOW + 16}, {"expires_at": NOW + 301},
            {"issued_at": NOW - 1, "expires_at": NOW - 1},
            {"expires_at": True}
        ):
            with self.subTest(change=change):
                with self.assertRaisesRegex(GrantDenied, "EXPIRED_OR_INVALID_GRANT_TIME"):
                    self.valid(sign({**fixture(), **change}))

    def test_issuer_and_audience_bound(self):
        self.denied("issuer", "some_other_issuer", "INVALID_AUDIENCE_OR_ISSUER")
        self.denied("audience", "generic-worker", "INVALID_AUDIENCE_OR_ISSUER")

    def test_pending_merge_expired_lease_and_author_review_denied(self):
        for t in (replace(target(), review_lease_active=False),
                  replace(target(), merge_intent_pending=True)):
            with self.assertRaisesRegex(GrantDenied, "REVIEW_STATE_NOT_SAFE"):
                self.valid(target=t)
        with self.assertRaisesRegex(GrantDenied, "SELF_REVIEW_FORBIDDEN"):
            self.valid(target=replace(target(), author_actor_id=A))

    def test_missing_extra_fields_denied(self):
        for payload in ({}, {**fixture(), "extra": "bypass"}):
            with self.assertRaisesRegex(GrantDenied, "INVALID_GRANT_FIELDS"):
                self.valid({"payload": payload, "mac": "a" * 64})
        with self.assertRaisesRegex(GrantDenied, "INVALID_ENVELOPE"):
            self.valid({"payload": fixture(), "mac": "a" * 64, "role": "architect-reviewer"})


class DurableNonceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "grant.sqlite3"
        self.ledger = GrantLedger(self.db, KEY)
        self.kwargs = {"authenticated_actor": A, "target": target(), "now": NOW}
        self.env = sign(fixture())

    def test_one_shot_and_restart_replay_rejected(self):
        first = self.ledger.reserve(self.env, **self.kwargs)
        self.assertEqual(first["status"], "RESERVED_REQUIRES_PRIVILEGED_QA")
        self.assertFalse(first["formal_review_performed"])
        self.assertEqual(self.ledger.state("c" * 32), "RESERVED")
        restarted = GrantLedger(self.db, KEY)
        with self.assertRaisesRegex(GrantDenied, "GRANT_ALREADY_SPENT"):
            restarted.reserve(self.env, **self.kwargs)

    def test_race_two_reviewers_only_one_wins(self):
        def attempt(_):
            try:
                self.ledger.reserve(self.env, **self.kwargs)
                return "reserved"
            except GrantDenied as exc:
                return str(exc)
        with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
            outcomes = list(pool.map(attempt, range(24)))
        self.assertEqual(outcomes.count("reserved"), 1, outcomes)
        self.assertEqual(outcomes.count("GRANT_ALREADY_SPENT"), 23, outcomes)

    def test_crash_reserved_is_not_auto_released(self):
        self.ledger.reserve(self.env, **self.kwargs)
        # No release(), no generated retry token and no unilateral override.
        with self.assertRaisesRegex(GrantDenied, "GRANT_ALREADY_SPENT"):
            self.ledger.reserve(self.env, **self.kwargs)
        self.assertEqual(self.ledger.state("c" * 32), "RESERVED")

    def test_different_nonce_same_revision_is_also_rejected(self):
        self.ledger.reserve(self.env, **self.kwargs)
        p = fixture()
        p["nonce"] = "d" * 32
        with self.assertRaisesRegex(GrantDenied, "GRANT_ALREADY_SPENT"):
            self.ledger.reserve(sign(p), **self.kwargs)
        self.assertEqual(self.ledger.state("d" * 32), "UNKNOWN")

    def test_new_revision_requires_new_signed_scope(self):
        self.ledger.reserve(self.env, **self.kwargs)
        p = fixture()
        p["nonce"] = "d" * 32
        p["revision"] = 5
        new_target = replace(target(), revision=5)
        r = self.ledger.reserve(sign(p), authenticated_actor=A,
                                target=new_target, now=NOW)
        self.assertEqual(r["status"], "RESERVED_REQUIRES_PRIVILEGED_QA")
        self.assertEqual(self.ledger.state("d" * 32), "RESERVED")


if __name__ == "__main__":
    unittest.main()

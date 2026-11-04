"""P085 regression: strict types for trusted target snapshots.

Imports existing test-only fixtures; no live host, lease, keys, or mutation.
"""
from dataclasses import replace
import unittest

from workspace_mcp.review_grants import GrantDenied, verify
from test_review_grants import A, NOW, KEY, fixture, sign, target


class TrustedTargetTypeRegression(unittest.TestCase):
    def test_malformed_target_fields_are_never_usable_as_authority(self):
        corrupt = (
            ("review_lease_active", 1),
            ("review_lease_active", "true"),
            ("review_lease_active", ["true"]),
            ("review_lease_active", {"value": True}),
            ("merge_intent_pending", 0),
            ("merge_intent_pending", ""),
            ("merge_intent_pending", None),
            ("merge_intent_pending", []),
            ("merge_intent_pending", {}),
            ("revision", 4.0),
            ("author_actor_id", None),
            ("author_actor_id", ""),
            ("author_actor_id", []),
            ("author_actor_id", {}),
        )
        grant = sign(fixture())
        for field, bad in corrupt:
            with self.subTest(field=field, value=repr(bad)):
                with self.assertRaisesRegex(GrantDenied, "TRUSTED_TARGET_INVALID"):
                    verify(grant, KEY, authenticated_actor=A,
                           target=replace(target(), **{field: bad}), now=NOW)


if __name__ == "__main__":
    unittest.main()

"""Offline negative/positive tests for Chain2 triage. No browser or queue writes."""
from __future__ import annotations
import importlib.util
from pathlib import Path
import tempfile
import unittest

path=Path(__file__).resolve().parent/"chain2-triage.py"
spec=importlib.util.spec_from_file_location("chain2_triage",path)
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def record(job,role="developer",ticket="t01",**kw):
    return {"job_id":job,"ticket":ticket,"role":role,"state":"active",
            "handoff_exists":False,"request_valid":False,**kw}


def parent(child,confirmed=False,incidents=None,parent_id="P"):
    return {"state":"done","job_id":parent_id,"successor_job_id":child,
            "successor_message_saved":confirmed,
            "successor_incidents":incidents if incidents is not None else []}


def fixture(*rows,active=None):
    return {"ok":True,"no_mutations_performed":True,"total_stages":50,
            "summary":{"active":len([x for x in rows if x.get("state")=="active"]) if active is None else active,
                       "done":19,"queue":0,"not_started":22,"done_ack_verified":19},
            "lineage":list(rows)}


class TriageTests(unittest.TestCase):
    def test_empty_campaign_stays_read_only(self):
        result=module.classify(fixture())
        self.assertTrue(result["read_only"])
        self.assertTrue(result["no_jobs_claimed_or_replayed"])
        self.assertEqual(result["jobs"],[])

    def test_valid_handoff_needs_role_action_not_auto_ack(self):
        job="f"*36
        doc=fixture(parent(job,confirmed=True),record(job,handoff_exists=True,handoff_valid=True))
        result=module.classify(doc)
        self.assertEqual(result["summary"]["active_categories"],
                         {"handoff_ready_for_role_review":1})
        self.assertFalse(result["jobs"][0]["handoff_request_valid"])

    def test_unknown_send_with_valid_handoff_requires_browser_check(self):
        job="a"*36
        incident={"stage":"unknown","reason":"send_button_missing","private":"must_not_echo"}
        doc=fixture(parent(job,confirmed=True,incidents=[incident]),
                    record(job,handoff_exists=True,handoff_valid=True))
        result=module.classify(doc)
        self.assertEqual(result["jobs"][0]["category"],"browser_delivery_uncertain")
        self.assertTrue(result["jobs"][0]["incident_recorded"])
        self.assertNotIn("must_not_echo",str(result))

    def test_no_message_saved_but_parent_exists_is_uncertain(self):
        job="b"*36
        doc=fixture(parent(job,confirmed=False),record(job,handoff_exists=True,handoff_valid=True))
        result=module.classify(doc)
        self.assertEqual(result["jobs"][0]["category"],"browser_delivery_uncertain")

    def test_invalid_handoff_and_incomplete_evidence_distinguished(self):
        doc=fixture(record("c"*36,handoff_exists=True,handoff_valid=False),
                    record("d"*36))
        result=module.classify(doc)
        self.assertEqual(result["summary"]["active_categories"],
                         {"handoff_invalid":1,"worker_evidence_incomplete":1})

    def test_bad_shape_fails_closed(self):
        self.assertFalse(module.classify({"ok":False,"reason":"missing"})["ok"])
        self.assertFalse(module.classify(fixture(record("e"*36),active=2))["ok"])
        doc=fixture(parent("f"*36,parent_id="p1"),
                    parent("f"*36,parent_id="p2"),
                    record("f"*36))
        self.assertEqual(module.classify(doc)["reason"],"duplicate_parent")

    def test_duplicate_active_identity_and_incomplete_document_fail_closed(self):
        bad=fixture(record("c"*36),record("c"*36))
        self.assertEqual(module.classify(bad)["reason"],"duplicate_or_invalid_active_id")
        self.assertFalse(module.classify({"ok":True})["ok"])

    def test_missing_lineage_does_not_create_any_state(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            result=module.classify(module._lineage(root))
            self.assertFalse(result["ok"])
            self.assertEqual(list(root.iterdir()),[])


    def test_missing_handoff_cannot_be_pending_ack(self):
        job="a"*36
        data=fixture(parent(job,confirmed=True),
                     record(job,handoff_exists=False,handoff_valid=False,
                            request_valid=True))
        result=module.classify(data)
        self.assertEqual(result["jobs"][0]["category"],
                         "worker_evidence_incomplete")
        self.assertFalse(result["jobs"][0]["handoff_request_valid"])

    def test_unknown_handoff_validity_cannot_be_pending_ack(self):
        job="b"*36
        data=fixture(parent(job,confirmed=True),
                     record(job,handoff_exists=True,handoff_valid=None,
                            request_valid=True))
        result=module.classify(data)
        self.assertEqual(result["jobs"][0]["category"],
                         "worker_evidence_incomplete")
        self.assertFalse(result["jobs"][0]["handoff_request_valid"])

    def test_non_boolean_request_flag_does_not_promote_review(self):
        job="c"*36
        data=fixture(parent(job,confirmed=True),
                     record(job,handoff_exists=True,handoff_valid=True,
                            request_valid="true"))
        result=module.classify(data)
        self.assertEqual(result["jobs"][0]["category"],
                         "worker_evidence_incomplete")
        self.assertFalse(result["jobs"][0]["handoff_request_valid"])

    def test_complete_handoff_and_exact_request_true_is_pending_ack(self):
        job="d"*36
        data=fixture(parent(job,confirmed=True),
                     record(job,handoff_exists=True,handoff_valid=True,
                            request_valid=True))
        result=module.classify(data)
        self.assertEqual(result["jobs"][0]["category"],
                         "handoff_request_pending_ack")
        self.assertTrue(result["jobs"][0]["handoff_request_valid"])


if __name__=="__main__":
    unittest.main(verbosity=2)

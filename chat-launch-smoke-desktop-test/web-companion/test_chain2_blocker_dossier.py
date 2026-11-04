"""Isolated Chain2 blocker diagnostics tests; no live artifacts changed."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import chain2_blocker_dossier as d

class BlockerDossierTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base=Path(tmp.name)
        self.state=self.base/"bridge-state"
        self.acks=self.state/"completion-acks"
        self.reports=self.base/"agent-reports"
        self.diag=self.state/"diagnostics"
        for p in (self.acks,self.reports,self.diag):p.mkdir(parents=True)
        self.job="01234567-89ab-4cde-8901-234567890abc"
        self.patcher=[
            patch.object(d.c,"STATE",self.state),
            patch.object(d.c,"ACKS",self.acks),
            patch.object(d.c,"REPORTS",self.reports)
        ]
        for p in self.patcher:p.start()
        self.addCleanup(lambda:[p.stop() for p in reversed(self.patcher)])
    def record(self,events,exists=True):
        p=self.diag/(self.job+".json")
        if exists:p.write_text(json.dumps(events),encoding="utf-8")
        return p
    def sample(self,stages=(),reasons=(),handoff=False,request=False,
               report=False,ack=False):
        events=[{"stage":st,"reason":reason,"at":"2026-11-04T00:00:00+00:00"}
                for st,reason in zip(stages,reasons)]
        return {"events":events,"handoff_exists":handoff,"handoff_valid":handoff,
                "request_valid":request,
                "receipt":{"report_exists":report,"ack_matches":ack}}
    def test_presend_never_called_completed_or_retryable(self):
        x=self.sample(["prompt_filled","unknown","tab_closed"],
                      [None,"send_button_missing","unexpected_close"])
        self.assertEqual(d.classify(x),"PRE_SEND_UNCONFIRMED")
        self.assertIn("no automatic replay",d.recommended_next(d.classify(x)))
    def test_click_with_unknown_delivery(self):
        x=self.sample(["send_clicked","unknown"],[None,"unknown"])
        self.assertEqual(d.classify(x),"CLICKED_BUT_UNCONFIRMED")
    def test_auth_mismatch_takes_priority(self):
        x=self.sample(["send_clicked","unknown"],[None,"account_context_changed"])
        self.assertEqual(d.classify(x),"ACCOUNT_CONTEXT_DISPUTED")
    def test_tab_closed_with_report_and_no_ack(self):
        x=self.sample(["message_saved","tab_closed"],[None,"unexpected_close"],
                      handoff=True,request=False,report=True)
        self.assertEqual(d.classify(x),"CLOSED_WITHOUT_VERIFIED_ACK")
    def test_valid_report_ack_hash_required(self):
        report=self.reports/("completion-"+self.job+".md")
        body="Job ID: "+self.job+"\n"+("test evidence\n"*15)
        report.write_text(body,encoding="utf-8")
        sha=hashlib.sha256(report.read_bytes()).hexdigest()
        (self.acks/(self.job+".json")).write_text(json.dumps({
            "job_id":self.job,"status":"REPORT_ACKED",
            "artifact_sha256":sha,"ack_id":"verified-ack"}))
        self.assertTrue(d.receipt_status(self.job,report)["ack_matches"])
        report.write_text(body+"\ntampered",encoding="utf-8")
        self.assertFalse(d.receipt_status(self.job,report)["ack_matches"])
    def test_report_above_cap_never_matches_ack(self):
        report=self.reports/("completion-"+self.job+".md")
        report.write_bytes(b"X"*150001)
        result=d.receipt_status(self.job,report)
        self.assertTrue(result["report_exists"])
        self.assertIsNone(result["report_sha256"])
        self.assertFalse(result["ack_matches"])

    def test_telemetry_only_known_stages_and_bounded_reason(self):
        self.record([
            {"at":"a","stage":"message_saved"},
            {"at":"a","stage":"token_exfiltration","reason":"secret"},
            {"at":"b","stage":"unknown","reason":"R"*100}
        ])
        events,status=d.events_for(self.job)
        self.assertEqual(status,"available")
        self.assertEqual([x["stage"] for x in events],["message_saved","unknown"])
        self.assertIsNone(events[-1]["reason"])
    def test_restored_tab_supersedes_prior_close_without_ack(self):
        record=self.sample(["message_saved","tab_closed","tab_restored"],
                           [None,"unexpected_close",None],
                           handoff=True,request=False,report=True)
        self.assertEqual(d.classify(record),"REPORT_PRESENT_REQUEST_MISSING")
        record["events"].append({"stage":"tab_closed","reason":"unexpected_close"})
        self.assertEqual(d.classify(record),"CLOSED_WITHOUT_VERIFIED_ACK")

    def test_unverified_reopening_does_not_clear_close(self):
        record=self.sample(["message_saved","tab_closed","tab_opening"],
                           [None,"unexpected_close",None],
                           handoff=True,request=False,report=True)
        self.assertEqual(d.classify(record),"CLOSED_WITHOUT_VERIFIED_ACK")

    def test_oversized_or_corrupt_telemetry_fails_closed(self):
        path=self.record([])
        path.write_bytes(b"{"+b"a"*65536+b"}")
        self.assertEqual(d.events_for(self.job),([],"missing_or_invalid"))
        path.write_bytes(b"{not-json}")
        self.assertEqual(d.events_for(self.job),([],"missing_or_invalid"))

    def test_historical_delivery_evidence_survives_watchdog_flood(self):
        events=[{"stage":"message_saved","at":"2026-11-03T10:00:00Z"}]
        events += [{"stage":"report_watchdog","at":"2026-11-03T10:01:00Z"}
                   for _ in range(82)]
        self.record(events)
        rows,status=d.events_for(self.job)
        self.assertEqual(status,"available")
        self.assertEqual(rows[0]["stage"],"message_saved")
        state=self.sample()
        state["events"]=rows
        self.assertEqual(d.classify(state),"SENT_BUT_REPORT_MISSING")

    def test_historical_account_dispute_cannot_be_erased_by_noise(self):
        events=[{"stage":"auth_context_changed","reason":"account_context_changed"}]
        events += [{"stage":"report_watchdog"} for _ in range(80)]
        self.record(events)
        rows,status=d.events_for(self.job)
        self.assertEqual(status,"available")
        state=self.sample()
        state["events"]=rows
        self.assertEqual(d.classify(state),"ACCOUNT_CONTEXT_DISPUTED")

    def test_missing_diagnostics_is_unknown_not_sent(self):
        events,state=d.events_for(self.job)
        self.assertEqual((events,state),([],"missing_or_invalid"))
        self.assertEqual(d.classify(self.sample()),"DELIVERY_UNVERIFIED")
    def test_readonly_dashboard_does_not_create_queue_or_mark_done(self):
        class FakeChain:
            seed_count=1
        cfg={"seed_count":1}
        tickets=[{"id":"test-ticket"}]
        jid=self.job
        touched=self.state/"marker-that-must-remain-absent"
        self.assertFalse(touched.exists())
        def locate(_):return "active",{"id":jid}
        with patch.object(d.c,"load",return_value=(cfg,tickets,"a"*64)),\
             patch.object(d.c,"ROLES",("developer",)),\
             patch.object(d.c,"jid",return_value=jid),\
             patch.object(d.c,"locate",side_effect=locate),\
             patch.object(d.c,"handoff_path",return_value=self.base/"missing.json"),\
             patch.object(d.c,"marker",return_value=None),\
             patch.object(d.c,"request_valid",return_value=False):
            output=d.analyze()
        self.assertEqual(output["active_count"],1)
        self.assertEqual(output["blockers"][0]["classification"],"DELIVERY_UNVERIFIED")
        self.assertFalse(touched.exists())
        self.assertEqual(list(self.state.glob("queue/*")),[])
        self.assertTrue(output["no_queue_mutations"])
    def test_repo_prefixed_artifact_is_diagnostic_only(self):
        import chain_campaign as cc
        ticket,role="l02-redaction","developer"
        role_root=self.base/"experiments"/"role-workstreams"/"chain2"/ticket/role
        role_root.mkdir(parents=True)
        source=role_root/"offline_qa.py"
        source.write_text("isolated test",encoding="utf-8")
        hand=role_root/"handoff.json"
        hand.write_text(json.dumps({"changed_files":[
            "chat-launch-smoke-desktop-test/experiments/role-workstreams/"
            "chain2/l02-redaction/developer/offline_qa.py"]}),encoding="utf-8")
        with patch.object(cc,"workspace",return_value=role_root), \
             patch.object(cc,"handoff_path",return_value=hand):
            info=d.diagnose_handoff_paths(ticket,role)
        self.assertEqual(info["status"],"workspace_prefix_repair_candidate")
        self.assertEqual(info["prefixed_but_physically_present"],1)
        self.assertEqual(info["suggested_role_relative_names"],["offline_qa.py"])
        self.assertFalse(info["automatic_rewrite_permitted"])
        self.assertEqual(source.read_text(encoding="utf-8"),"isolated test")

    def test_external_artifact_never_gets_rewrite_hint(self):
        import chain_campaign as cc
        ticket,role="l02-redaction","developer"
        role_root=self.base/"experiments"/"role-workstreams"/"chain2"/ticket/role
        role_root.mkdir(parents=True)
        hand=role_root/"handoff.json"
        hand.write_text(json.dumps({"changed_files":["../../outside.txt",
            "C:/not-allowed", "a_missing.py"]}),encoding="utf-8")
        with patch.object(cc,"workspace",return_value=role_root), \
             patch.object(cc,"handoff_path",return_value=hand):
            info=d.diagnose_handoff_paths(ticket,role)
        self.assertEqual(info["status"],"requires_manual_artifact_inspection")
        self.assertGreaterEqual(info["unsafe"],2)
        self.assertEqual(info["suggested_role_relative_names"],[])

    def test_symlinked_parent_cannot_produce_repair_hint(self):
        import chain_campaign as cc
        ticket,role="l02-redaction","developer"
        root=self.base/"experiments"/"role-workstreams"/"chain2"/ticket/role
        root.mkdir(parents=True)
        outside=self.base/"outside"
        outside.mkdir()
        (outside/"offline_qa.py").write_text("external",encoding="utf-8")
        alias=root/"alias"
        try:
            alias.symlink_to(outside,target_is_directory=True)
        except (OSError,NotImplementedError):
            self.skipTest("Directory symlink creation unavailable on this host")
        hand=root/"handoff.json"
        hand.write_text(json.dumps({"changed_files":[
            "chat-launch-smoke-desktop-test/experiments/role-workstreams/"
            "chain2/l02-redaction/developer/alias/offline_qa.py"]}),encoding="utf-8")
        with patch.object(cc,"workspace",return_value=root), \
             patch.object(cc,"handoff_path",return_value=hand):
            result=d.diagnose_handoff_paths(ticket,role)
        self.assertEqual(result["status"],"requires_manual_artifact_inspection")
        self.assertEqual(result["unsafe"],1)
        self.assertEqual(result["suggested_role_relative_names"],[])
        self.assertFalse(result["automatic_rewrite_permitted"])

    def test_unsafe_path_state_never_becomes_repair_suggestion(self):
        import chain_campaign as cc
        ticket,role="l02-redaction","developer"
        root=self.base/"experiments"/"role-workstreams"/"chain2"/ticket/role
        root.mkdir(parents=True)
        hand=root/"handoff.json"
        hand.write_text(json.dumps({"changed_files":[
            "chat-launch-smoke-desktop-test/experiments/role-workstreams/"
            "chain2/l02-redaction/developer/link/qa.py"]}),encoding="utf-8")
        with patch.object(cc,"workspace",return_value=root), \
             patch.object(cc,"handoff_path",return_value=hand), \
             patch.object(d,"role_path_status",return_value="unsafe"):
            result=d.diagnose_handoff_paths(ticket,role)
        self.assertEqual(result["unsafe"],1)
        self.assertEqual(result["suggested_role_relative_names"],[])

    def test_readonly_delivery_preflight_never_creates_ack(self):
        active=self.state/"active"
        active.mkdir(parents=True)
        job={"id":self.job,"completion_mode":"report",
             "report_name":"completion-"+self.job+".md"}
        (active/(self.job+".json")).write_text(json.dumps(job),encoding="utf-8")
        report=self.reports/("completion-"+self.job+".md")
        content="Job ID: "+self.job+"\n## Outcome\nSuccess\n## Evidence\n"+("verified details\n"*12)
        report.write_text(content,encoding="utf-8")
        result=d.report_preflight(self.job,active,self.reports)
        self.assertTrue(result["report_integrity_ready"])
        self.assertFalse(result["would_create_ack"])
        self.assertFalse(result["host_identity_verified"])
        self.assertFalse((self.acks/(self.job+".json")).exists())
        report.write_text("wrong binding\n"+"A"*130,encoding="utf-8")
        result=d.report_preflight(self.job,active,self.reports)
        self.assertFalse(result["report_integrity_ready"])
        self.assertEqual(result["pending_reason"],"report_binding_invalid")
        self.assertFalse((self.acks/(self.job+".json")).exists())

    def test_author_request_preflight_is_not_authorization(self):
        import chain_campaign as cc
        ticket="l06-p108"
        digest="b"*64
        metadata={"campaign_id":"chain2","ticket_id":ticket,
                  "stage":"developer","manifest_sha256":digest}
        job={"id":self.job,"role_metadata":metadata}
        with patch.object(cc,"locate",return_value=("active",job)),\
             patch.object(cc,"jid",return_value=self.job),\
             patch.object(cc,"armed",return_value=True),\
             patch.object(cc,"marker",return_value=None),\
             patch.object(cc,"read_handoff",return_value="c"*64):
            result=d.request_preflight(ticket,"developer",self.job,digest,[{}],1)
        self.assertTrue(result["structural_preconditions_met"])
        self.assertEqual(result["reason"],"original_actor_request_required")
        self.assertFalse(result["trusted_actor_verified"])
        self.assertFalse(result["request_created"])
        self.assertFalse((self.state/"handoff-requests"/(self.job+".json")).exists())
        with patch.object(cc,"locate",return_value=("active",job)),\
             patch.object(cc,"jid",return_value=self.job),\
             patch.object(cc,"armed",return_value=False):
            result=d.request_preflight(ticket,"developer",self.job,digest,[{}],1)
        self.assertFalse(result["structural_preconditions_met"])
        self.assertEqual(result["reason"],"campaign_not_armed")

    def test_request_preflight_rejects_unbound_metadata(self):
        import chain_campaign as cc
        with patch.object(cc,"locate",return_value=("active",{
            "id":self.job,"role_metadata":{"campaign_id":"chain2"}})),\
             patch.object(cc,"jid",return_value=self.job),\
             patch.object(cc,"armed",return_value=True):
            result=d.request_preflight("l06-p108","developer",self.job,
                                        "b"*64,[{}],1)
        self.assertFalse(result["structural_preconditions_met"])
        self.assertEqual(result["reason"],"role_metadata_not_bound")
        self.assertFalse(result["request_created"])

    def test_malformed_ack_not_accepted(self):
        report=self.reports/("completion-"+self.job+".md")
        report.write_text("A"*120)
        (self.acks/(self.job+".json")).write_text("broken")
        self.assertFalse(d.receipt_status(self.job,report)["ack_matches"])

if __name__=="__main__":
    unittest.main()

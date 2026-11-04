"""Role campaign tests run ONLY against temporary filesystem and fake heartbeat."""
from __future__ import annotations
import hashlib
import json
import shutil
import tempfile
import unittest
from contextlib import ExitStack
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch

import role_campaign as rc
import enqueue_completion as ec


class CampaignTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.t=Path(self.temp.name)
        self.project=self.t/"project"
        self.root=self.project/"web-companion"
        self.state=self.root/"bridge-state"
        self.briefs=self.project/"agent-briefs"
        self.reports=self.project/"agent-reports"
        self.acks=self.state/"completion-acks"
        self.campaigns=self.root/"role-campaigns"
        self.campaigns.mkdir(parents=True)
        self.briefs.mkdir(parents=True)
        self.reports.mkdir()
        for d in ("queue","active","done","diagnostics","completion-acks"):
            (self.state/d).mkdir(parents=True,exist_ok=True)
        original=rc.CAMPAIGNS/"eng1.json"
        shutil.copyfile(original,self.campaigns/"eng1.json")
        self.patches=ExitStack()
        self.addCleanup(self.patches.close)
        mapping={
            (rc,"ROOT"):self.root,(rc,"PROJECT"):self.project,
            (rc,"STATE"):self.state,(rc,"QUEUE"):self.state/"queue",
            (rc,"ACTIVE"):self.state/"active",(rc,"DONE"):self.state/"done",
            (rc,"ACKS"):self.acks,(rc,"REPORTS"):self.reports,
            (rc,"BRIEFS"):self.briefs,(rc,"HOLD"):self.state/"admission-hold.json",
            (rc,"ARM"):self.state/"role-campaign-armed.json",
            (rc,"CAMPAIGNS"):self.campaigns,
            (ec,"ROOT"):self.root,(ec,"PROJECT"):self.project,
            (ec,"QUEUE"):self.state/"queue",(ec,"ACTIVE"):self.state/"active",
            (ec,"DONE"):self.state/"done"
        }
        for (obj,attr),val in mapping.items():
            self.patches.enter_context(patch.object(obj,attr,val))
        self.set_heartbeat()
        self.data,self.digest=rc.load()
        self.one=self.data["tickets"][0]

    def set_heartbeat(self,age=0,version="0.6.6",state="polling"):
        h={"version":version,"state":state,"windows":1,
           "at":(datetime.now(timezone.utc)-timedelta(seconds=age)).isoformat()}
        (self.state/"diagnostics"/"extension.json").write_text(json.dumps(h),encoding="utf-8")

    def put_job(self,stage,role="feature",state="done",valid=True):
        jid=rc.stage_id("eng1",self.one["id"],role)
        metadata={"campaign_id":"eng1","ticket_id":self.one["id"],
                  "stage":role,"manifest_sha256":self.digest}
        report=self.reports/("completion-"+jid+".md")
        contents="Job ID: "+jid+"\n## Outcome\nWorked\n## Evidence\nTests ran\n"
        report.write_text(contents,encoding="utf-8")
        sha=hashlib.sha256(report.read_bytes()).hexdigest()
        ack_id="a"*32
        (self.acks/(jid+".json")).write_text(json.dumps({
          "status":"REPORT_ACKED","job_id":jid,
          "artifact_sha256":sha if valid else "0"*64,
          "ack_id":ack_id}),encoding="utf-8")
        obj={"id":jid,"status":"done","report_sha256":sha,
             "report_ack_id":ack_id,"role_metadata":metadata}
        (self.state/stage/(jid+".json")).write_text(json.dumps(obj),encoding="utf-8")
        return jid

    def arm_file(self):
        (self.state/"role-campaign-armed.json").write_text(json.dumps({
          "enabled":True,"campaign_id":"eng1","manifest_sha256":self.digest,
          "max_total_stages":75}),encoding="utf-8")

    def test_twenty_five_features_and_seventy_five_stages(self):
        p=rc.preview()
        self.assertEqual((p["features"],p["total_stage_jobs"],p["ready_count"]),
                         (25,75,25))
        self.assertFalse(p["armed"])

    def test_deterministic_unique_job_ids_and_roles(self):
        seen=set()
        for ticket in self.data["tickets"]:
            for stage in rc.PHASES:
                jid=rc.stage_id("eng1",ticket["id"],stage)
                self.assertNotIn(jid,seen);seen.add(jid)
                self.assertLessEqual(len(rc.role_name("eng1",ticket["id"],stage)),48)
        self.assertEqual(len(seen),75)

    def test_reviewer_and_developer_blocked_before_prerequisite(self):
        self.assertTrue(rc.stage_ready("eng1",self.one,"feature",self.digest)) if hasattr(rc,"stage_ready") else None
        self.assertFalse(rc.accepted("eng1",self.one["id"],"feature",self.digest))
        self.assertFalse(any(role!="feature" for _,role,_ in rc.ready(self.data,self.digest)))

    def test_manifest_rejects_weak_workload(self):
        bad=json.loads((self.campaigns/"eng1.json").read_text(encoding="utf-8"))
        bad["tickets"][0]["acceptance"]=["Just do it"]
        (self.campaigns/"eng1.json").write_text(json.dumps(bad),encoding="utf-8")
        with self.assertRaisesRegex(rc.CampaignError,"workload_too_small"):
            rc.load()

    def test_manifest_rejects_duplicate_ticket(self):
        bad=json.loads((self.campaigns/"eng1.json").read_text(encoding="utf-8"))
        bad["tickets"].append(dict(bad["tickets"][0]))
        (self.campaigns/"eng1.json").write_text(json.dumps(bad,ensure_ascii=False),encoding="utf-8")
        with self.assertRaisesRegex(rc.CampaignError,"duplicate"):
            rc.load()

    def test_manifest_rejects_wrong_project_and_boolean_quota(self):
        bad=json.loads((self.campaigns/"eng1.json").read_text(encoding="utf-8"))
        bad["allowed_project"]="other"
        (self.campaigns/"eng1.json").write_text(json.dumps(bad),encoding="utf-8")
        with self.assertRaisesRegex(rc.CampaignError,"unsafe_campaign"):
            rc.load()
        bad["allowed_project"]="chat-launch-smoke-desktop-test"
        bad["max_active"]=True
        (self.campaigns/"eng1.json").write_text(json.dumps(bad),encoding="utf-8")
        with self.assertRaisesRegex(rc.CampaignError,"unsafe_campaign"):
            rc.load()

    def test_not_armed_no_queue(self):
        self.assertEqual(rc.refill_unlocked()["reason"],"not_armed")
        self.assertEqual(len(list((self.state/"queue").glob("*.json"))),0)

    def test_hold_blocks_when_armed(self):
        self.arm_file()
        rc.HOLD.write_text("hold")
        self.assertEqual(rc.refill_unlocked()["reason"],"admission_held")
        self.assertEqual(len(list(rc.QUEUE.glob("*.json"))),0)

    def test_arm_requires_explicit_confirmation(self):
        with self.assertRaisesRegex(rc.CampaignError,"explicit_owner_arm"):
            rc.arm()

    def test_arm_rejects_live_old_jobs(self):
        (rc.ACTIVE/"old.json").write_text("{}")
        with self.assertRaisesRegex(rc.CampaignError,"existing_jobs"):
            rc.arm(confirm=True)

    def test_arm_rejects_hold_and_stale_client(self):
        rc.HOLD.write_text("hold")
        with self.assertRaisesRegex(rc.CampaignError,"admission_held"):
            rc.arm(confirm=True)
        rc.HOLD.unlink()
        self.set_heartbeat(age=1000)
        with self.assertRaisesRegex(rc.CampaignError,"browser_not_ready"):
            rc.arm(confirm=True)

    def test_arming_only_creates_marker_not_browser_jobs(self):
        val=rc.arm(confirm=True)
        self.assertTrue(val["enabled"])
        self.assertEqual(len(list(rc.QUEUE.glob("*.json"))),0)
        self.assertTrue(rc.is_armed(self.data,self.digest))
        self.assertTrue(rc.arm(confirm=True)["already_armed"])

    def test_refill_creates_four_large_roles_and_bounded_queue(self):
        self.arm_file()
        r=rc.refill_unlocked()
        self.assertEqual(r["queued"],4)
        self.assertEqual(len(list(rc.QUEUE.glob("*.json"))),4)
        self.assertTrue(all(x["role"]=="feature" for x in r["jobs"]))
        self.assertEqual(rc.refill_unlocked()["reason"],"capacity")
        for j in r["jobs"]:
            path=rc.BRIEFS/(rc.role_name("eng1",j["ticket"],"feature")+".md")
            self.assertGreater(len(path.read_text(encoding="utf-8")),2000)
            data=json.loads((rc.QUEUE/(j["job_id"]+".json")).read_text(encoding="utf-8"))
            self.assertEqual(data["descendant_budget"],0)
            self.assertEqual(data["role_metadata"]["stage"],"feature")
            self.assertIn("## Acceptance criteria",path.read_text(encoding="utf-8"))

    def test_refill_replenishes_only_free_capacity(self):
        self.arm_file()
        first=rc.refill_unlocked()
        jid=first["jobs"][0]["job_id"]
        (rc.QUEUE/(jid+".json")).rename(rc.ACTIVE/(jid+".json"))
        result=rc.refill_unlocked()
        self.assertEqual(result["queued"],1)
        self.assertEqual(len(list(rc.QUEUE.glob("*.json"))),4)

    def test_refill_never_mixes_with_legacy_job(self):
        self.arm_file()
        (rc.ACTIVE/"old.json").write_text(json.dumps({"role":"legacy"}))
        r=rc.refill_unlocked()
        self.assertEqual(r["reason"],"unowned_jobs_present")
        self.assertEqual(len(list(rc.QUEUE.glob("*.json"))),0)

    def test_feature_ack_and_done_unlock_developer(self):
        self.put_job("done")
        self.assertTrue(rc.accepted("eng1",self.one["id"],"feature",self.digest))
        options=list(rc.ready(self.data,self.digest))
        self.assertIn(("developer",self.one["id"]),
             [(stage,t["id"]) for t,stage,_ in options])
        self.assertNotIn(("reviewer",self.one["id"]),
             [(stage,t["id"]) for t,stage,_ in options])

    def test_acked_but_active_does_not_unlock_developer(self):
        self.put_job("active")
        self.assertFalse(rc.accepted("eng1",self.one["id"],"feature",self.digest))
        self.assertNotIn(("developer",self.one["id"]),
             [(stage,t["id"]) for t,stage,_ in rc.ready(self.data,self.digest)])

    def test_wrong_report_sha_blocks_developer(self):
        self.put_job("done",valid=False)
        self.assertFalse(rc.accepted("eng1",self.one["id"],"feature",self.digest))

    def test_review_only_after_developer_done_ack(self):
        self.put_job("done",role="feature")
        self.put_job("done",role="developer")
        stages=[(t["id"],s) for t,s,_ in rc.ready(self.data,self.digest)]
        self.assertIn((self.one["id"],"reviewer"),stages)


    def test_refill_prioritizes_new_developer_after_feature_done(self):
        self.arm_file()
        first=rc.refill_unlocked()
        self.assertEqual(first["queued"],4)
        jid=rc.stage_id("eng1",self.one["id"],"feature")
        self.assertTrue((rc.QUEUE/(jid+".json")).is_file())
        # This test simulates a completed, SHA-bound browser receipt.
        (rc.QUEUE/(jid+".json")).unlink()
        self.put_job("done",role="feature")
        refill=rc.refill_unlocked()
        self.assertEqual(refill["queued"],1)
        self.assertEqual(refill["jobs"][0]["role"],"developer")
        self.assertEqual(refill["jobs"][0]["ticket"],self.one["id"])
        self.assertEqual(len(list(rc.QUEUE.glob("*.json"))),4)

    def test_review_stage_preferred_when_both_predecessors_accepted(self):
        self.arm_file()
        self.put_job("done",role="feature")
        self.put_job("done",role="developer")
        result=rc.refill_unlocked()
        self.assertEqual(result["queued"],4)
        self.assertEqual(result["jobs"][0]["role"],"reviewer")
        self.assertEqual(result["jobs"][0]["ticket"],self.one["id"])

    def test_bad_sha_never_queues_reviewer(self):
        self.arm_file()
        self.put_job("done",role="feature")
        self.put_job("done",role="developer",valid=False)
        queued=rc.refill_unlocked()
        self.assertEqual(queued["queued"],4)
        self.assertFalse(any(x["role"]=="reviewer" for x in queued["jobs"]))

    def test_no_automatic_refill_when_arm_manifest_sha_changes(self):
        self.arm_file()
        src=self.campaigns/"eng1.json"
        contents=json.loads(src.read_text(encoding="utf-8"))
        contents["name"]="Engineering Foundation updated"
        src.write_text(json.dumps(contents,ensure_ascii=False),encoding="utf-8")
        self.assertEqual(rc.refill_unlocked()["reason"],"not_armed")
        self.assertEqual(len(list(rc.QUEUE.glob("*.json"))),0)

    def test_no_replay_after_done_and_valid_ack(self):
        self.arm_file()
        self.put_job("done",role="feature")
        result=rc.refill_unlocked()
        self.assertEqual(result["queued"],4)
        self.assertNotIn(
            rc.stage_id("eng1",self.one["id"],"feature"),
            [x["job_id"] for x in result["jobs"]])


    def test_claim_requires_manifest_arm_and_immutable_brief(self):
        self.arm_file()
        result=rc.refill_unlocked()
        jid=result["jobs"][0]["job_id"]
        doc=json.loads((rc.QUEUE/(jid+".json")).read_text(encoding="utf-8"))
        self.assertTrue(rc.claim_allowed(doc))
        bad={**doc,"role_metadata":{**doc["role_metadata"],"stage":"reviewer"}}
        self.assertFalse(rc.claim_allowed(bad))
        brief=rc.BRIEFS/(doc["role"]+".md")
        brief.write_text("changed after dispatch",encoding="utf-8")
        self.assertFalse(rc.claim_allowed(doc))
        self.arm_file()

    def test_claim_invalid_when_disarmed_or_held(self):
        self.arm_file()
        r=rc.refill_unlocked()
        jid=r["jobs"][0]["job_id"]
        doc=json.loads((rc.QUEUE/(jid+".json")).read_text(encoding="utf-8"))
        rc.HOLD.write_text("hold")
        self.assertFalse(rc.claim_allowed(doc))
        rc.HOLD.unlink()
        rc.ARM.write_text('{"enabled":false}')
        self.assertFalse(rc.claim_allowed(doc))

    def test_claim_legacy_task_preserves_original_semantics(self):
        self.assertTrue(rc.claim_allowed({"id":"legacy","prompt":"test"}))

    def test_developer_claim_rechecks_finished_feature(self):
        self.arm_file()
        self.put_job("done",role="feature")
        r=rc.refill_unlocked()
        doc=next(json.loads((rc.QUEUE/(x["job_id"]+".json")).read_text(encoding="utf-8"))
                 for x in r["jobs"] if x["role"]=="developer")
        self.assertTrue(rc.claim_allowed(doc))
        feature_id=rc.stage_id("eng1",self.one["id"],"feature")
        (rc.ACKS/(feature_id+".json")).write_text('{"status":"INVALID"}')
        self.assertFalse(rc.claim_allowed(doc))


    def test_role_metrics_covers_all_seventy_five_stages(self):
        import role_metrics
        result=role_metrics.collect()
        self.assertEqual(result["total"],75)
        for stage in rc.PHASES:
            self.assertEqual(result["per_role"][stage]["total"],25)
            self.assertEqual(result["per_role"][stage]["verified_reports"],0)
            self.assertIsNone(result["per_role"][stage]["median_send_to_report_seconds"])

    def test_role_metrics_does_not_infer_compute_time(self):
        import role_metrics
        self.put_job("done",role="feature")
        data=role_metrics.collect()
        matching=next(x for x in data["tasks"]
                      if x["ticket"]==self.one["id"] and x["role"]=="feature")
        self.assertTrue(matching["report_ack_verified"])
        self.assertTrue(matching["recorded_completed"])
        self.assertIsNone(matching["model_compute_seconds"])
        self.assertEqual(data["per_role"]["feature"]["verified_reports"],1)

    def test_role_metrics_records_queued_without_work(self):
        import role_metrics
        self.arm_file()
        campaign=rc.refill_unlocked()
        self.assertEqual(campaign["queued"],4)
        result=role_metrics.collect()
        queued=[x for x in result["tasks"] if x["state"]=="queue"]
        self.assertEqual(len(queued),4)
        self.assertTrue(all(x["send_to_report_seconds"] is None for x in queued))
        self.assertTrue(all(x["queued_wait_seconds"] is not None for x in queued))

    def test_unknown_stage_is_not_replayed(self):
        jid=rc.stage_id("eng1",self.one["id"],"feature")
        (rc.DONE/(jid+".json")).write_text(json.dumps({"id":jid,"status":"unknown"}))
        self.assertNotIn(self.one["id"],
             [t["id"] for t,s,_ in rc.ready(self.data,self.digest) if s=="feature"])

    def test_immutable_brief_conflict(self):
        jid=rc.stage_id("eng1",self.one["id"],"feature")
        path=rc.BRIEFS/(rc.role_name("eng1",self.one["id"],"feature")+".md")
        path.write_text("tampered",encoding="utf-8")
        with self.assertRaisesRegex(rc.CampaignError,"immutable_brief"):
            rc.ensure_brief(self.data,self.one,"feature",jid)

    def test_deterministic_enqueue_is_idempotent(self):
        jid=rc.stage_id("eng1",self.one["id"],"feature")
        brief=rc.ensure_brief(self.data,self.one,"feature",jid)
        meta={"campaign_id":"eng1","ticket_id":self.one["id"],
              "stage":"feature","manifest_sha256":self.digest}
        role=rc.role_name("eng1",self.one["id"],"feature")
        a=ec.enqueue(role,brief,job_id=jid,metadata=meta)
        self.assertTrue(a["queued"])
        b=ec.enqueue(role,brief,job_id=jid,metadata=meta)
        self.assertTrue(b["already_exists"])
        self.assertEqual(len(list(ec.QUEUE.glob("*.json"))),1)
        (ec.QUEUE/(jid+".json")).rename(ec.ACTIVE/(jid+".json"))
        self.assertTrue(ec.enqueue(role,brief,job_id=jid,metadata=meta)["already_exists"])

    def test_deterministic_enqueue_detects_changed_brief(self):
        jid=rc.stage_id("eng1",self.one["id"],"feature")
        brief=rc.ensure_brief(self.data,self.one,"feature",jid)
        meta={"campaign_id":"eng1","ticket_id":self.one["id"],
              "stage":"feature","manifest_sha256":self.digest}
        role=rc.role_name("eng1",self.one["id"],"feature")
        ec.enqueue(role,brief,job_id=jid,metadata=meta)
        (rc.BRIEFS/(role+".md")).write_text("tampered",encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError,"deterministic_job_conflict"):
            ec.enqueue(role,brief,job_id=jid,metadata=meta)


if __name__=="__main__":
    unittest.main(verbosity=2)

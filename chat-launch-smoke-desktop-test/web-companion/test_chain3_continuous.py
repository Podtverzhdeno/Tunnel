"""Hermetic successor-chain QA: no real chats, no user files mutated."""
import hashlib
import json
import shutil
import tempfile
import unittest
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import bridge
import chain3_continuous as c3
import chain_campaign as old
import enqueue_completion as enqueue
import role_campaign as legacy

class ContinuousTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name)/"project"
        self.app=self.root/"web-companion"
        self.state=self.app/"bridge-state"
        self.queues={name:self.state/name for name in
                     ("queue","active","done","completion-acks",
                      "handoff-requests","continuous-handoff-requests","diagnostics")}
        self.briefs=self.root/"agent-briefs"
        self.reports=self.root/"agent-reports"
        self.campaigns=self.app/"role-campaigns"
        for p in list(self.queues.values())+[self.briefs,self.reports,self.campaigns]:
            p.mkdir(parents=True)
        for name in ("chain3.json","chain2.json","sdd1.json","eng1.json"):
            shutil.copyfile(c3.ROOT/"role-campaigns"/name,self.campaigns/name)
        self.stack=ExitStack()
        self.addCleanup(self.stack.close)
        values=[
            (c3,"ROOT",self.app),(c3,"PROJECT",self.root),(c3,"STATE",self.state),
            (c3,"CONFIG",self.campaigns/"chain3.json"),
            (c3,"QUEUE",self.queues["queue"]),(c3,"ACTIVE",self.queues["active"]),
            (c3,"DONE",self.queues["done"]),(c3,"ACKS",self.queues["completion-acks"]),
            (c3,"REPORTS",self.reports),(c3,"BRIEFS",self.briefs),
            (c3,"HOLD",self.state/"admission-hold.json"),
            (c3,"ARM",self.state/"continuous-armed.json"),
            (c3,"REQUESTS",self.queues["continuous-handoff-requests"]),
            (c3,"RATE",self.state/"continuous-claim-rate.json"),
            (c3,"MODE",self.state/"chain3-mode.json"),
            (old,"ROOT",self.app),(old,"PROJECT",self.root),(old,"STATE",self.state),
            (old,"QUEUE",self.queues["queue"]),(old,"ACTIVE",self.queues["active"]),
            (old,"DONE",self.queues["done"]),
            (old,"ACKS",self.queues["completion-acks"]),
            (old,"REPORTS",self.reports),(old,"BRIEFS",self.briefs),
            (old,"HOLD",self.state/"admission-hold.json"),
            (old,"CONFIG",self.campaigns/"chain2.json"),
            (old,"ARM",self.state/"role-campaign-armed.json"),
            (old,"REQUESTS",self.queues["handoff-requests"]),
            (enqueue,"ROOT",self.app),(enqueue,"PROJECT",self.root),
            (enqueue,"QUEUE",self.queues["queue"]),
            (enqueue,"ACTIVE",self.queues["active"]),
            (enqueue,"DONE",self.queues["done"]),
            (legacy,"CAMPAIGNS",self.campaigns),
            (bridge,"STATE",self.state),(bridge,"QUEUE",self.queues["queue"]),
            (bridge,"ACTIVE",self.queues["active"]),
            (bridge,"DONE",self.queues["done"]),
            (bridge,"ACKS",self.queues["completion-acks"]),
            (bridge,"REPORTS",self.reports),
            (bridge,"DIAG",self.queues["diagnostics"]),
            (bridge,"ADMISSION_HOLD",self.state/"admission-hold.json"),
        ]
        for obj,attribute,value in values:
            self.stack.enter_context(patch.object(obj,attribute,value))
        self.stack.enter_context(patch.object(legacy,"client_ready",return_value=True))
        self.finished=self.stack.enter_context(patch.object(c3,"chain2_finished",return_value=True))
        self.stack.enter_context(patch.object(old,"accepted",return_value=True))
        heartbeat={"version":"0.6.6","state":"polling",
                   "at":datetime.now(timezone.utc).isoformat(),"windows":1}
        (self.queues["diagnostics"]/"extension.json").write_text(json.dumps(heartbeat))
        self.cfg,self.tickets,self.old_digest,self.digest=c3.load()

    def write_active(self,lane=0,gen=0,role="developer"):
        id=c3.jid(lane,gen,role)
        path=self.queues["queue"]/(id+".json")
        if path.is_file():
            target=self.queues["active"]/(id+".json")
            path.rename(target)
        else:
            target=self.queues["active"]/(id+".json")
            meta={"campaign_id":"chain3","ticket_id":c3.label(lane,gen),
                  "stage":role,"manifest_sha256":self.digest}
            target.write_text(json.dumps({"id":id,"role":c3.role_name(lane,gen,role),
                                          "role_metadata":meta,"completion_mode":"report",
                                          "report_name":"completion-"+id+".md"}))
        return id

    def handoff(self,lane=0,gen=0,role="developer",terminal=False):
        id=c3.jid(lane,gen,role)
        work=c3.workspace(lane,gen,role)
        work.mkdir(parents=True,exist_ok=True)
        artifact=work/"evidence.txt"
        artifact.write_text("verified test evidence",encoding="utf-8")
        d={"version":1,"parent_job_id":id,
           "upstream_handoff":c3.upstream_path(lane,gen,role,self.tickets,self.cfg["seed_count"]),
           "summary":"Verified substantive implementation and tests.",
           "changed_files":["evidence.txt"],"tests":["verified unit suite PASS"],
           "issues":["Pending additional independent QA."],
           "feature_proposals":["Request a reviewed protocol extension (not auto-approved)."],
           "next_focus":"Continue verifying reliability under the existing accepted scope.",
           "no_actionable_work":terminal}
        if role=="reviewer" and not terminal:
            qa=work/"qa_failure.txt"
            qa.write_text(
                "FAIL negative case: expected a verified rejection, observed acceptance. "
                "Reproduction: deterministic isolated test with sanitized inputs. "
                "The issue is advisory until reviewed by an authorized maintainer.",
                encoding="utf-8")
            d["changed_files"].append("qa_failure.txt")
            approved=next(t for t in self.tickets if
                t["id"]==c3.last_chain2_ticket(lane,self.tickets,self.cfg["seed_count"]))
            d["next_work"]={
                "issue_id":f"issue-l{lane:02d}-g{gen:08d}",
                "criterion":approved["acceptance"][0],
                "objective":"Fix the reproduced negative case in the approved scoped design.",
                "evidence_file":"qa_failure.txt",
                "evidence_sha256":hashlib.sha256(qa.read_bytes()).hexdigest(),
                "next_verification":"Repeat the failing isolated test and confirm rejection."
            }
        c3.handoff_path(lane,gen,role).write_text(json.dumps(d),encoding="utf-8")
        return d

    def finalize(self,lane=0,gen=0,role="developer"):
        id=c3.jid(lane,gen,role)
        active=self.queues["active"]/(id+".json")
        obj=json.loads(active.read_text())
        report=self.reports/("completion-"+id+".md")
        report.write_text("Job ID: "+id+"\n## Outcome\nPassed\n## Evidence\n"
                          "Test cases verified\n## Tests\nPassed\n## Handoff\n"
                          "Requested one successor\n")
        sha=hashlib.sha256(report.read_bytes()).hexdigest()
        obj.update({"status":"done","report_sha256":sha,
                    "report_ack_id":"a"*32})
        (self.queues["completion-acks"]/(id+".json")).write_text(
            json.dumps({"job_id":id,"status":"REPORT_ACKED",
                        "artifact_sha256":sha,"ack_id":"a"*32}))
        active.unlink()
        (self.queues["done"]/(id+".json")).write_text(json.dumps(obj))
        return id

    def test_mode_selection_is_explicit_and_never_grants_host_trust(self):
        self.assertEqual(c3.mode_status()["selected_mode"],"sandbox")
        with self.assertRaisesRegex(c3.ContinuationError,"explicit_mode_selection"):
            c3.select_mode("trusted")
        with self.assertRaisesRegex(c3.ContinuationError,"invalid_mode"):
            c3.select_mode("unsupported",True)
        result=c3.select_mode("trusted",True)
        self.assertTrue(result["changed"])
        self.assertEqual(c3.mode_status()["effective_mode"],"blocked")
        self.assertFalse(c3.mode_status()["trusted_host_verified"])
        self.assertFalse(c3.mode_status()["mode_selection_is_authorization"])
        self.assertFalse(c3.preview()["owner_armed"])
        self.assertEqual(c3.refill_unlocked()["reason"],"trusted_host_attestation_required")
        with self.assertRaisesRegex(c3.ContinuationError,"trusted_host_attestation_required"):
            c3.arm(True)
        self.assertEqual(list(self.queues["queue"].glob("*.json")),[])
        result=c3.select_mode("sandbox",True)
        self.assertEqual(result["effective_mode"],"sandbox")
        self.assertTrue(c3.arm(True)["armed"])

    def test_mode_selection_locked_after_chain3_arm_or_existing_job(self):
        c3.arm(True)
        with self.assertRaisesRegex(c3.ContinuationError,"mode_selection_locked"):
            c3.select_mode("trusted",True)
        self.assertEqual(c3.mode_status()["selected_mode"],"sandbox")
        c3.ARM.unlink()
        self.write_active()
        with self.assertRaisesRegex(c3.ContinuationError,"mode_selection_locked"):
            c3.select_mode("trusted",True)

    def test_invalid_or_linked_mode_file_fails_closed(self):
        c3.MODE.write_text('{"schema_version":1,"campaign_id":"chain3",'
                           '"mode":"trusted","owner_authorized":true}',encoding="utf-8")
        with self.assertRaisesRegex(c3.ContinuationError,"invalid_mode_selection"):
            c3.preview()
        self.assertFalse(c3.armed(self.digest))
        self.assertEqual(list(self.queues["queue"].glob("*.json")),[])
        c3.MODE.write_text("broken-json",encoding="utf-8")
        self.assertFalse(c3.armed(self.digest))
        with self.assertRaises(c3.ContinuationError):
            c3.select_mode("sandbox",True)

    def test_switch_to_trusted_blocks_previous_untrusted_arm_if_corrupted(self):
        c3.arm(True)
        job=self.write_active()
        obj=json.loads((self.queues["active"]/(job+".json")).read_text())
        c3.MODE.write_text(json.dumps({"schema_version":1,"campaign_id":"chain3",
                                       "mode":"trusted"}),encoding="utf-8")
        self.assertFalse(c3.armed(self.digest))
        self.assertFalse(c3.claim_allowed(obj))
        self.assertEqual(c3.refill_unlocked()["reason"],"trusted_host_attestation_required")
        self.assertFalse(c3.handoff_requested_for_job(obj))

    def test_mode_selection_blocked_for_unverifiable_existing_state(self):
        (self.queues["queue"]/"malformed.json").write_text("{broken",encoding="utf-8")
        with self.assertRaisesRegex(c3.ContinuationError,"mode_selection_state_unverifiable"):
            c3.select_mode("trusted",True)

    def test_unarmed_and_incomplete_cannot_create_jobs(self):
        self.assertEqual(c3.preview()["generation_limit"],None)
        self.assertFalse(c3.preview()["owner_armed"])
        self.assertEqual(c3.refill_unlocked()["reason"],"not_owner_armed")
        with self.assertRaisesRegex(c3.ContinuationError,"explicit_owner_arm"):
            c3.arm()
        self.assertTrue(c3.arm(True)["armed"])
        self.assertTrue(c3.arm(True)["already_armed"])
        self.finished.return_value=False
        self.assertEqual(c3.refill_unlocked()["reason"],"chain2_incomplete")
        self.assertEqual(list(self.queues["queue"].glob("*.json")),[])

    def test_owner_armed_refill_starts_bounded_generation_zero(self):
        c3.arm(True)
        value=c3.refill_unlocked()
        self.assertEqual(value["queued"],4)
        self.assertEqual({j["generation"] for j in value["jobs"]},{0})
        self.assertEqual({j["role"] for j in value["jobs"]},{"developer"})
        self.assertEqual(len(list(self.queues["queue"].glob("*.json"))),4)
        self.assertTrue(all(x["job_id"]!=c3.jid(0,1,"developer") for x in value["jobs"]))
        self.assertEqual(c3.refill_unlocked()["reason"],"capacity")

    def test_request_and_two_generations_unique_ids(self):
        c3.arm(True)
        current=self.write_active()
        with self.assertRaisesRegex(c3.ContinuationError,"missing_or_unsafe_file"):
            c3.request(current)
        self.handoff()
        req=c3.request(current)
        self.assertEqual(req["successor"],c3.jid(0,0,"reviewer"))
        self.assertFalse(req["queued"])
        self.assertTrue(c3.request(current)["already_requested"])
        current_obj=json.loads((self.queues["active"]/(current+".json")).read_text())
        self.assertTrue(c3.handoff_requested_for_job(current_obj))
        self.finalize()
        self.assertTrue(c3.accepted(0,0,"developer",self.cfg,self.tickets,self.digest))
        self.assertIn((0,0,"reviewer"),list(c3.ready(self.cfg,self.tickets,self.digest)))
        rev=self.write_active(0,0,"reviewer")
        self.handoff(0,0,"reviewer")
        rev_req=c3.request(rev)
        self.assertEqual(rev_req["successor"],c3.jid(0,1,"developer"))
        self.finalize(0,0,"reviewer")
        self.assertTrue(c3.accepted(0,0,"reviewer",self.cfg,self.tickets,self.digest))
        self.assertIn((0,1,"developer"),list(c3.ready(self.cfg,self.tickets,self.digest)))
        self.assertEqual(len(set([current,rev,c3.jid(0,1,"developer"),
                                  c3.jid(0,1000000,"developer")])),4)
        self.assertTrue(c3.jid(0,1000000,"developer"))

    def test_terminal_metadata_must_match_actual_handoff_decision(self):
        c3.arm(True)
        parent=self.write_active(0,0,"reviewer")
        self.handoff(0,0,"reviewer",terminal=True)
        req=c3.request(parent)
        self.assertTrue(req["terminal"])
        marker=c3.REQUESTS/(parent+".json")
        contents=json.loads(marker.read_text())
        self.assertTrue(c3.request_valid(0,0,"reviewer",self.tickets,
                                        self.cfg["seed_count"],self.digest))
        # A malicious marker cannot turn an explicitly terminal report into a child.
        contents["terminal"]=False
        contents["successor_job_id"]=c3.jid(0,1,"developer")
        marker.write_text(json.dumps(contents))
        self.assertFalse(c3.request_valid(0,0,"reviewer",self.tickets,
                                         self.cfg["seed_count"],self.digest))
        self.finalize(0,0,"reviewer")
        self.assertFalse(c3.accepted(0,0,"reviewer",self.cfg,self.tickets,self.digest))
        self.assertNotIn((0,1,"developer"),list(c3.ready(
            self.cfg,self.tickets,self.digest)))

    def test_nonterminal_review_cannot_forge_early_lane_completion(self):
        c3.arm(True)
        parent=self.write_active(0,0,"reviewer")
        self.handoff(0,0,"reviewer")
        c3.request(parent)
        marker=c3.REQUESTS/(parent+".json")
        contents=json.loads(marker.read_text())
        contents["terminal"]=True
        contents["successor_job_id"]=""
        marker.write_text(json.dumps(contents))
        self.assertFalse(c3.request_valid(0,0,"reviewer",self.tickets,
                                         self.cfg["seed_count"],self.digest))

    def test_terminal_review_stops_lane_without_fabricated_work(self):
        c3.arm(True)
        dev=self.write_active()
        self.handoff()
        c3.request(dev);self.finalize()
        reviewer=self.write_active(0,0,"reviewer")
        self.handoff(0,0,"reviewer",terminal=True)
        req=c3.request(reviewer)
        self.assertIsNone(req["successor"])
        self.finalize(0,0,"reviewer")
        self.assertNotIn((0,1,"developer"),list(c3.ready(self.cfg,self.tickets,self.digest)))

    def test_failed_or_mutated_handoff_never_admits_child(self):
        c3.arm(True)
        p=self.write_active()
        d=self.handoff()
        c3.request(p)
        d["summary"]="Modified after immutable marker was recorded."
        c3.handoff_path(0,0,"developer").write_text(json.dumps(d))
        self.assertFalse(c3.handoff_requested_for_job(
            json.loads((self.queues["active"]/(p+".json")).read_text())))
        self.finalize()
        self.assertFalse(c3.accepted(0,0,"developer",self.cfg,self.tickets,self.digest))
        self.assertNotIn((0,0,"reviewer"),list(c3.ready(self.cfg,self.tickets,self.digest)))

    def test_unmapped_artifact_mutation_invalidates_handoff_request(self):
        # No optional files_sha256 map: request integrity must still cover
        # every changed_files artifact after the handoff is recorded.
        c3.arm(True)
        parent=self.write_active()
        record=self.handoff()
        self.assertNotIn("files_sha256",record)
        self.assertTrue(c3.request(parent)["requested"])
        self.assertTrue(c3.request_valid(0,0,"developer",self.tickets,
                                         self.cfg["seed_count"],self.digest))
        artifact=c3.workspace(0,0,"developer")/"evidence.txt"
        artifact.write_text("tampered after handoff without changing its JSON")
        self.assertFalse(c3.request_valid(0,0,"developer",self.tickets,
                                          self.cfg["seed_count"],self.digest))
        with self.assertRaisesRegex(c3.ContinuationError,"immutable_request_conflict"):
            c3.request(parent)
        job=json.loads((self.queues["active"]/(parent+".json")).read_text())
        self.assertFalse(c3.handoff_requested_for_job(job))
        self.finalize()
        self.assertFalse(c3.accepted(0,0,"developer",self.cfg,self.tickets,self.digest))
        self.assertNotIn((0,0,"reviewer"),list(c3.ready(
            self.cfg,self.tickets,self.digest)))

    def test_handoff_bundle_rejects_excessive_file_without_request(self):
        c3.arm(True)
        parent=self.write_active()
        self.handoff()
        artifact=c3.workspace(0,0,"developer")/"evidence.txt"
        with artifact.open("wb") as f:
            f.truncate(16*1024*1024+1)
        with self.assertRaisesRegex(c3.ContinuationError,"artifact_too_large_for_handoff"):
            c3.request(parent)
        self.assertEqual(list(c3.REQUESTS.glob("*.json")),[])

    def test_handoff_rejects_nested_symlink_even_within_role_workspace(self):
        c3.arm(True)
        parent=self.write_active()
        data=self.handoff()
        folder=c3.workspace(0,0,"developer")/"nested"
        folder.mkdir()
        (folder/"evidence.txt").write_text("scoped nested evidence")
        data["changed_files"].append("nested/evidence.txt")
        c3.handoff_path(0,0,"developer").write_text(json.dumps(data))
        original=Path.is_symlink
        with patch.object(Path,"is_symlink",
                          lambda node: node==folder or original(node)):
            with self.assertRaisesRegex(c3.ContinuationError,"artifact_link_invalid"):
                c3.request(parent)
        self.assertEqual(list(c3.REQUESTS.glob("*.json")),[])

    def test_handoff_rejects_nested_junction_even_within_role_workspace(self):
        c3.arm(True)
        parent=self.write_active()
        data=self.handoff()
        folder=c3.workspace(0,0,"developer")/"nested"
        folder.mkdir()
        (folder/"evidence.txt").write_text("scoped nested evidence")
        data["changed_files"].append("nested/evidence.txt")
        c3.handoff_path(0,0,"developer").write_text(json.dumps(data))
        original=getattr(Path,"is_junction",lambda node:False)
        with patch.object(Path,"is_junction",
                          lambda node: node==folder or original(node),create=True):
            with self.assertRaisesRegex(c3.ContinuationError,"artifact_link_invalid"):
                c3.request(parent)
        self.assertEqual(list(c3.REQUESTS.glob("*.json")),[])

    def test_handoff_rejects_linked_role_root_before_report_read(self):
        c3.arm(True)
        parent=self.write_active()
        self.handoff()
        role_dir=c3.workspace(0,0,"developer")
        original=Path.is_symlink
        with patch.object(Path,"is_symlink",
                          lambda node: node==role_dir or original(node)):
            with self.assertRaisesRegex(c3.ContinuationError,"workspace_escape"):
                c3.request(parent)
        self.assertEqual(list(c3.REQUESTS.glob("*.json")),[])

    def test_optional_hash_cannot_bypass_large_file_limit(self):
        c3.arm(True)
        parent=self.write_active()
        data=self.handoff()
        data["files_sha256"]={"evidence.txt":"0"*64}
        c3.handoff_path(0,0,"developer").write_text(json.dumps(data))
        artifact=c3.workspace(0,0,"developer")/"evidence.txt"
        with artifact.open("wb") as file:
            file.truncate(16*1024*1024+1)
        with self.assertRaisesRegex(c3.ContinuationError,"artifact_too_large_for_handoff"):
            c3.request(parent)
        self.assertEqual(list(c3.REQUESTS.glob("*.json")),[])

    def test_duplicate_artifact_names_are_not_counted_twice(self):
        c3.arm(True)
        parent=self.write_active()
        data=self.handoff()
        data["changed_files"].append(data["changed_files"][0])
        c3.handoff_path(0,0,"developer").write_text(json.dumps(data))
        with self.assertRaisesRegex(c3.ContinuationError,"duplicate_artifacts"):
            c3.request(parent)
        self.assertEqual(list(c3.REQUESTS.glob("*.json")),[])

    def test_changed_during_streaming_hash_fails_closed(self):
        c3.arm(True)
        parent=self.write_active()
        self.handoff()
        artifact=c3.workspace(0,0,"developer")/"evidence.txt"
        real_open=Path.open
        def tamper_after_open(node,mode="r",*args,**kwargs):
            handle=real_open(node,mode,*args,**kwargs)
            if node==artifact and mode=="rb":
                with real_open(node,"ab") as change:
                    change.write(b"concurrent appended bytes")
            return handle
        with patch.object(Path,"open",tamper_after_open):
            with self.assertRaisesRegex(c3.ContinuationError,
                                        "artifact_changed_during_hash"):
                c3.request(parent)
        self.assertEqual(list(c3.REQUESTS.glob("*.json")),[])

    def test_optional_artifact_hash_must_match_before_request(self):
        c3.arm(True)
        parent=self.write_active()
        data=self.handoff()
        data["files_sha256"]={"evidence.txt":"0"*64}
        c3.handoff_path(0,0,"developer").write_text(json.dumps(data))
        with self.assertRaisesRegex(c3.ContinuationError,"artifact_sha_changed"):
            c3.request(parent)
        self.assertEqual(list(c3.REQUESTS.glob("*.json")),[])

    def test_optional_artifact_hash_valid_with_streaming_digest(self):
        c3.arm(True)
        parent=self.write_active()
        data=self.handoff()
        artifact=c3.workspace(0,0,"developer")/"evidence.txt"
        data["files_sha256"]={"evidence.txt":hashlib.sha256(artifact.read_bytes()).hexdigest()}
        c3.handoff_path(0,0,"developer").write_text(json.dumps(data))
        self.assertTrue(c3.request(parent)["requested"])
        self.assertTrue(c3.request_valid(0,0,"developer",self.tickets,
                                         self.cfg["seed_count"],self.digest))

    def test_rate_and_hold_blocks_claim(self):
        c3.arm(True)
        c3.refill_unlocked()
        first=self.queues["queue"]/(c3.jid(0,0,"developer")+".json")
        job=json.loads(first.read_text())
        self.assertTrue(c3.claim_allowed(job))
        self.assertTrue(c3.reserve_claim(job))
        self.assertFalse(c3.claim_allowed(job))
        self.assertFalse(c3.reserve_claim(job))
        self.assertEqual(c3.rate_state()["claims"],1)
        self.queues["queue"].joinpath("does-not-exist.json").unlink(missing_ok=True)
        c3.HOLD.write_text("pause")
        self.assertFalse(c3.claim_allowed(job))
        self.assertEqual(c3.refill_unlocked()["reason"],"admission_held")

    def test_not_authorized_source_and_proposals_never_create_more(self):
        c3.arm(True)
        current=self.write_active()
        self.handoff()
        c3.request(current)
        bogus={"campaign_id":"chain3","ticket_id":c3.label(0,1),
               "stage":"developer","manifest_sha256":self.digest}
        fake={"id":c3.jid(0,1,"developer"),"role_metadata":bogus,
              "completion_mode":"report"}
        self.assertFalse(c3.claim_allowed(fake))
        # New feature proposals are text-only and not inserted in the queue.
        self.assertEqual(len(list(self.queues["queue"].glob("*.json"))),0)

    def test_bridge_rejects_ack_without_request_and_claim_beyond_rate(self):
        import threading
        import urllib.request
        self.stack.enter_context(patch.object(old,"refill_unlocked",
                                              return_value={"ok":True,"queued":0}))
        self.stack.enter_context(patch.object(c3,"rate_ok",return_value=True))
        c3.arm(True)
        self.state.joinpath("role-campaign-armed.json").write_text(json.dumps({
            "enabled":True,"campaign_id":"chain2","manifest_sha256":"test",
            "max_total_stages":50}))
        # Use isolated HTTP; never communicate with live port 18763.
        with bridge.Server(("127.0.0.1",0),bridge.Handler) as server:
            server.bridge_token="test-secret-"+"z"*40
            thread=threading.Thread(target=server.serve_forever,daemon=True)
            thread.start()
            try:
                def get(uri):
                    req=urllib.request.Request(
                        f"http://127.0.0.1:{server.server_port}"+uri,
                        headers={"X-Chat-Smoke-Key":server.bridge_token,
                                 "Origin":"chrome-extension://test"})
                    with urllib.request.urlopen(req,timeout=3) as response:
                        return json.load(response)
                job=get("/v1/next")["job"]
                self.assertEqual(job["role_metadata"]["campaign_id"],"chain3")
                self.assertEqual(job["role_metadata"]["stage"],"developer")
                lane,gen=c3.parse_label(job["role_metadata"]["ticket_id"],self.cfg["seed_count"])
                self.assertEqual(gen,0)
                self.assertEqual(job["id"],c3.jid(lane,gen,"developer"))
                self.assertEqual(get("/v1/ack?id="+job["id"])["pending"],
                                 "handoff_not_requested")
            finally:
                server.shutdown()
                server.server_close()
                thread.join(3)

    def test_review_cannot_spawn_without_scoped_evidence(self):
        c3.arm(True)
        p=self.write_active(0,0,"reviewer")
        data=self.handoff(0,0,"reviewer")
        data.pop("next_work")
        c3.handoff_path(0,0,"reviewer").write_text(json.dumps(data))
        with self.assertRaisesRegex(c3.QualityError,"structured_next_work_required"):
            c3.request(p)
        data=self.handoff(0,0,"reviewer")
        data["next_work"]["criterion"]="Unapproved feature proposal outside the SDD"
        c3.handoff_path(0,0,"reviewer").write_text(json.dumps(data))
        with self.assertRaisesRegex(c3.QualityError,"criterion_outside_approved_scope"):
            c3.request(p)
        data=self.handoff(0,0,"reviewer")
        data["next_work"]["criterion"]=["not a string"]
        c3.handoff_path(0,0,"reviewer").write_text(json.dumps(data))
        with self.assertRaisesRegex(c3.QualityError,"criterion_outside_approved_scope"):
            c3.request(p)

    def test_tampered_evidence_blocks_successor(self):
        c3.arm(True)
        p=self.write_active(0,0,"reviewer")
        data=self.handoff(0,0,"reviewer")
        evidence=c3.workspace(0,0,"reviewer")/data["next_work"]["evidence_file"]
        evidence.write_text(evidence.read_text()+"\nTAMPER")
        with self.assertRaisesRegex(c3.QualityError,"evidence_sha_mismatch"):
            c3.request(p)
        self.assertFalse(c3.handoff_requested_for_job(
            json.loads((self.queues["active"]/(p+".json")).read_text())))
        self.assertEqual(list(c3.REQUESTS.glob("*.json")),[])

    def test_recycled_issue_cannot_spawn_another_generation(self):
        c3.arm(True)
        first=self.write_active(0,0,"reviewer")
        old=self.handoff(0,0,"reviewer")
        c3.request(first)
        self.finalize(0,0,"reviewer")
        second=self.write_active(0,1,"reviewer")
        new=self.handoff(0,1,"reviewer")
        new["next_work"]["issue_id"]=old["next_work"]["issue_id"]
        c3.handoff_path(0,1,"reviewer").write_text(json.dumps(new))
        with self.assertRaisesRegex(c3.QualityError,"recycled_issue"):
            c3.request(second)
        self.assertEqual(len(list(c3.REQUESTS.glob("*.json"))),1)

    def test_no_actionable_work_cannot_be_combined_with_next_work(self):
        c3.arm(True)
        p=self.write_active(0,0,"reviewer")
        doc=self.handoff(0,0,"reviewer")
        doc["no_actionable_work"]=True
        c3.handoff_path(0,0,"reviewer").write_text(json.dumps(doc))
        with self.assertRaisesRegex(c3.QualityError,"terminal_must_not_propose_work"):
            c3.request(p)

    def test_developer_cannot_self_authorize_next_work(self):
        c3.arm(True)
        p=self.write_active()
        doc=self.handoff()
        doc["next_work"]={"issue_id":"new-fake-issue"}
        c3.handoff_path(0,0,"developer").write_text(json.dumps(doc))
        with self.assertRaisesRegex(c3.ContinuationError,"developer_cannot_self_approve"):
            c3.request(p)

    def test_unique_id_does_not_bypass_repeated_problem_and_evidence_gate(self):
        c3.arm(True)
        p0=self.write_active(0,0,"reviewer")
        first=self.handoff(0,0,"reviewer")
        c3.request(p0)
        self.finalize(0,0,"reviewer")
        p1=self.write_active(0,1,"reviewer")
        second=self.handoff(0,1,"reviewer")
        self.assertNotEqual(first["next_work"]["issue_id"],
                            second["next_work"]["issue_id"])
        with self.assertRaisesRegex(c3.QualityError,"recycled_problem_signature"):
            c3.request(p1)
        second["next_work"]["objective"]=(
            "Validate a distinct failure mode in the approved SDD scope.")
        c3.handoff_path(0,1,"reviewer").write_text(json.dumps(second))
        with self.assertRaisesRegex(c3.QualityError,"recycled_evidence"):
            c3.request(p1)
        evidence=c3.workspace(0,1,"reviewer")/"qa_failure.txt"
        evidence.write_text(evidence.read_text()+
            "\\nNew, separately reproduced negative test for a distinct failure mode.")
        second["next_work"]["evidence_sha256"]=hashlib.sha256(
            evidence.read_bytes()).hexdigest()
        c3.handoff_path(0,1,"reviewer").write_text(json.dumps(second))
        self.assertTrue(c3.request(p1)["requested"])

    def test_followup_brief_inherits_concrete_verified_issue(self):
        c3.arm(True)
        p=self.write_active(0,0,"reviewer")
        doc=self.handoff(0,0,"reviewer")
        self.assertTrue(c3.request(p)["requested"])
        self.finalize(0,0,"reviewer")
        rel=c3.ensure_brief(0,1,"developer",self.cfg,self.tickets,self.digest)
        content=(self.root/rel).read_text(encoding="utf-8")
        self.assertIn(doc["next_work"]["issue_id"],content)
        self.assertIn(doc["next_work"]["objective"],content)
        self.assertIn("Do not invent defects",content)
        self.assertIn("Do not recycle unresolved issues",content)

if __name__=="__main__":
    unittest.main()

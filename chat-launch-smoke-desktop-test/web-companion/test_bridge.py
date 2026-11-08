import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

import bridge

class ServerTests(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.root=Path(self.t.name)
        self.queue=self.root/"queue"; self.active=self.root/"active"; self.done=self.root/"done"
        self.diag=self.root/"diagnostics"
        self.reports=self.root/"reports"
        self.acks=self.root/"acks"
        for d in (self.queue,self.active,self.done,self.diag,self.reports,self.acks): d.mkdir()
        self.patches=[
            patch.object(bridge,"QUEUE",self.queue),
            patch.object(bridge,"ACTIVE",self.active),
            patch.object(bridge,"DONE",self.done),
            patch.object(bridge,"DIAG",self.diag),
            patch.object(bridge,"REPORTS",self.reports),
            patch.object(bridge,"ACKS",self.acks),
            patch.object(bridge,"ADMISSION_HOLD",self.root/"admission-hold.json")
        ]
        for p in self.patches:p.start()
        self.server=bridge.Server(("127.0.0.1",0),bridge.Handler)
        self.server.bridge_token="test-secret-1234567890123456789012345678"
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.addr="http://127.0.0.1:%d"%self.server.server_port
    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join(3)
        for p in self.patches:p.stop()
        self.t.cleanup()
    def request(self,path,method="GET",data=None,key=None,origin="chrome-extension://testid"):
        content=None if data is None else json.dumps(data).encode()
        req=urllib.request.Request(self.addr+path,data=content,method=method,headers={
            "X-Chat-Smoke-Key":key or self.server.bridge_token,
            "Origin":origin,
            "Content-Type":"application/json"
        })
        try:
            with urllib.request.urlopen(req,timeout=2) as r:return r.status,json.load(r)
        except urllib.error.HTTPError as e:
            return e.code,json.load(e)
    def put(self,jid,prompt="ping"):
        (self.queue/(jid+".json")).write_text(json.dumps({"id":jid,"prompt":prompt}),encoding="utf-8")

    def test_corrupt_active_job_cannot_be_completed_or_acknowledged(self):
        jid = "01234567-89ab-4cde-8901-000000000351"
        active = self.active / (jid + ".json")
        done = self.done / (jid + ".json")
        invalid_cases = {
            "json_list": "[]",
            "truncated_json": "{",
            "mismatched_id": json.dumps({
                "id": "01234567-89ab-4cde-8901-000000000352"
            }),
            "invalid_role_metadata": json.dumps({
                "id": jid, "role_metadata": ["untrusted"]
            }),
        }
        for reason, content in invalid_cases.items():
            with self.subTest(reason=reason):
                done.unlink(missing_ok=True)
                active.write_text(content, encoding="utf-8")
                status, result = self.request(
                    "/v1/result", "POST", {"id": jid, "status": "unknown"}
                )
                self.assertEqual(status, 409)
                self.assertEqual(result, {"error": "active_state_invalid"})
                self.assertEqual(active.read_text(encoding="utf-8"), content)
                self.assertFalse(done.exists())

    def test_corrupt_completed_job_is_not_a_valid_duplicate_receipt(self):
        jid = "01234567-89ab-4cde-8901-000000000353"
        done = self.done / (jid + ".json")
        invalid_cases = {
            "json_list": "[]",
            "truncated_json": "{",
            "mismatched_id": json.dumps({
                "id": "01234567-89ab-4cde-8901-000000000354",
                "status": "done"
            }),
            "unknown_state": json.dumps({
                "id": jid, "status": "forged_success"
            }),
        }
        for reason, content in invalid_cases.items():
            with self.subTest(reason=reason):
                done.write_text(content, encoding="utf-8")
                status, result = self.request(
                    "/v1/result", "POST", {"id": jid, "status": "unknown"}
                )
                self.assertEqual(status, 409)
                self.assertEqual(result, {"error": "completed_state_invalid"})
                self.assertEqual(done.read_text(encoding="utf-8"), content)

    def test_result_url_strips_sensitive_query_parameters(self):
        jid="01234567-89ab-4cde-8901-000000000151"
        self.put(jid)
        self.assertEqual(self.request("/v1/next")[1]["job"]["id"],jid)
        status,result=self.request("/v1/result","POST",{
          "id":jid,"status":"done",
          "url":"https://chatgpt.com/c/safe-chat?session=PRIVATE_TOKEN&model=hidden"
        })
        self.assertEqual(status,200)
        final=json.loads((self.done/(jid+".json")).read_text(encoding="utf-8"))
        self.assertEqual(final["url"],"https://chatgpt.com/c/safe-chat")
        self.assertNotIn("PRIVATE_TOKEN",json.dumps(final))
        for bad in ("https://evil.example/c/safe-chat",
                    "https://chatgpt.com.attacker.test/c/safe-chat",
                    "https://chatgpt.com/c/safe-chat#secret",
                    "https://chatgpt.com:443/c/safe-chat",
                    "https://chatgpt.com/c/../private"):
            other="01234567-89ab-4cde-8901-000000000152"
            (self.active/(other+".json")).write_text(json.dumps({"id":other}))
            self.assertEqual(self.request("/v1/result","POST",{
              "id":other,"status":"done","url":bad})[0],400)

    def test_corrupt_json_shape_fails_closed_for_recovery_and_closure(self):
        import hashlib
        jid="01234567-89ab-4cde-8901-000000000174"
        (self.active/(jid+".json")).write_text("[]",encoding="utf-8")
        self.assertEqual(bridge.recovery_candidate(jid),
                         (False,"not_active_or_unreadable"))
        (self.active/(jid+".json")).unlink()
        content=b"Job ID: "+jid.encode()+b"\\n## Outcome\\nDone\\n## Evidence\\n"
        sha=hashlib.sha256(content).hexdigest()
        (self.reports/("completion-"+jid+".md")).write_bytes(content)
        saved={"id":jid,"status":"done","url":"https://chatgpt.com/c/safe",
               "report_path":"agent-reports/completion-"+jid+".md",
               "report_sha256":sha,"report_ack_id":"f"*32}
        done=self.done/(jid+".json")
        ack=self.acks/(jid+".json")
        done.write_text(json.dumps(saved),encoding="utf-8")
        ack.write_text("[]",encoding="utf-8")
        self.assertEqual(bridge.receipt_closure_candidate(jid),
                         (False,"receipt_not_verified",None))
        done.write_text("[]",encoding="utf-8")
        ack.write_text(json.dumps({"status":"REPORT_ACKED","job_id":jid,
              "artifact_sha256":sha,"ack_id":"f"*32}),encoding="utf-8")
        self.assertEqual(bridge.receipt_closure_candidate(jid),
                         (False,"receipt_not_verified",None))

    def test_recovery_requires_save_after_latest_send(self):
        jid="01234567-89ab-4cde-8901-000000000153"
        doc={"id":jid,"completion_mode":"report",
             "report_name":"completion-"+jid+".md"}
        (self.active/(jid+".json")).write_text(json.dumps(doc),encoding="utf-8")
        stages=[
          {"at":"2026-11-03T01:00:01+00:00","stage":"send_clicked"},
          {"at":"2026-11-03T01:00:02+00:00","stage":"message_saved"},
          {"at":"2026-11-03T01:00:03+00:00","stage":"send_clicked"}
        ]
        target=self.diag/(jid+".json")
        target.write_text(json.dumps(stages),encoding="utf-8")
        self.assertEqual(self.request("/v1/recover?id="+jid)[1]["reason"],
                         "missing_evidence")
        stages.append({"at":"2026-11-03T01:00:04+00:00","stage":"message_saved"})
        target.write_text(json.dumps(stages),encoding="utf-8")
        self.assertEqual(self.request("/v1/recover?id="+jid)[1]["eligible"],True)
        self.assertEqual(self.request("/v1/recover?id=invalid-job")[1]["reason"],
                         "invalid_job_id")

    def test_recovery_scan_does_not_double_count_identified_and_failed(self):
        payload={"tabs_total":4,"chat_tabs":4,"other_chatgpt_tabs":0,
                 "non_chatgpt_tabs":0,"unknown_url_tabs":0,"discarded":0,
                 "attempted":4,"identified":3,"unsaved_identified":0,
                 "eligible":0,"recovered":0,"ambiguous":0,
                 "injection_failed":2}
        self.assertEqual(self.request("/v1/recovery-scan","POST",payload)[0],400)
        payload["identified"]=2
        self.assertEqual(self.request("/v1/recovery-scan","POST",payload)[0],200)

    def test_accepted_receipt_close_only_recovery_rechecks_sha_and_url(self):
        import hashlib
        jid="01234567-89ab-4cde-8901-000000000161"
        content=b"Job ID: "+jid.encode()+b"\n## Outcome\nDone\n## Evidence\nVerified\n"
        sha=hashlib.sha256(content).hexdigest()
        (self.reports/("completion-"+jid+".md")).write_bytes(content)
        (self.acks/(jid+".json")).write_text(json.dumps({
            "status":"REPORT_ACKED","job_id":jid,
            "artifact_sha256":sha,"ack_id":"f"*32}),encoding="utf-8")
        saved={"id":jid,"status":"done",
               "url":"https://chatgpt.com/c/safe?do-not-persist=PRIVATE",
               "report_path":"agent-reports/completion-"+jid+".md",
               "report_sha256":sha,"report_ack_id":"f"*32}
        done=self.done/(jid+".json")
        done.write_text(json.dumps(saved),encoding="utf-8")
        status,decision=self.request("/v1/close-eligible?id="+jid)
        self.assertEqual(status,200)
        self.assertEqual(decision,{"eligible":True,"reason":"receipt_done",
                                   "url":"https://chatgpt.com/c/safe"})
        self.assertNotIn("PRIVATE",json.dumps(decision))
        (self.reports/("completion-"+jid+".md")).write_bytes(content+b"tampered")
        self.assertFalse(self.request("/v1/close-eligible?id="+jid)[1]["eligible"])
        (self.reports/("completion-"+jid+".md")).write_bytes(content)
        saved["report_ack_id"]="a"*32
        done.write_text(json.dumps(saved),encoding="utf-8")
        self.assertFalse(self.request("/v1/close-eligible?id="+jid)[1]["eligible"])
        saved["report_ack_id"]="f"*32
        saved["url"]="https://chatgpt.com.attacker.test/c/safe"
        done.write_text(json.dumps(saved),encoding="utf-8")
        self.assertFalse(self.request("/v1/close-eligible?id="+jid)[1]["eligible"])
        saved["url"]="https://chatgpt.com/c/safe"
        saved["status"]="submitted"
        done.write_text(json.dumps(saved),encoding="utf-8")
        self.assertFalse(self.request("/v1/close-eligible?id="+jid)[1]["eligible"])

    def test_close_only_does_not_authorize_from_active_or_unknown(self):
        jid="01234567-89ab-4cde-8901-000000000162"
        self.put(jid)
        self.request("/v1/next")
        status,decision=self.request("/v1/close-eligible?id="+jid)
        self.assertEqual(status,200)
        self.assertFalse(decision["eligible"])
        self.assertNotIn("url",decision)
        status,decision=self.request("/v1/close-eligible?id=invalid")
        self.assertEqual(status,200)
        self.assertEqual(decision["reason"],"invalid_job_id")
        self.assertEqual(self.request("/v1/close-eligible?x=1")[0],400)

    def test_health_and_auth(self):
        self.assertEqual(self.request("/v1/health")[0],200)
        self.assertEqual(self.request("/v1/health",key="wrong")[0],403)
        self.assertEqual(self.request("/v1/health",origin="https://attacker.example")[0],403)
    def test_empty(self):
        self.assertIsNone(self.request("/v1/next")[1]["job"])
    def test_claim_once_and_idempotent_receipt(self):
        jid="01234567-89ab-4cde-8901-23456789abcd"
        self.put(jid,"Тест")
        a=self.request("/v1/next")[1]["job"]
        self.assertEqual(a["prompt"],"Тест")
        self.assertIsNone(self.request("/v1/next")[1]["job"])
        self.assertEqual(len(list(self.active.glob("*.json"))),1)
        result={"id":jid,"status":"done","url":"https://chatgpt.com/c/abcde"}
        self.assertEqual(self.request("/v1/result","POST",result)[1]["status"],"done")
        self.assertTrue(self.request("/v1/result","POST",result)[1]["duplicate"])
        self.assertEqual(len(list(self.active.glob("*.json"))),0)
        self.assertEqual(len(list(self.done.glob("*.json"))),1)
    def test_reject_invalid_result(self):
        jid="01234567-89ab-4cde-8901-23456789abcd";self.put(jid)
        self.request("/v1/next")
        result={"id":jid,"status":"done","url":"https://attacker.example/"}
        self.assertEqual(self.request("/v1/result","POST",result)[0],400)
        self.assertEqual(len(list(self.active.glob("*.json"))),1)
    def test_submitted_requires_saved_chat_url(self):
        jid="01234567-89ab-4cde-8901-23456789abcd"
        self.put(jid)
        self.request("/v1/next")
        self.assertEqual(self.request("/v1/result","POST",{"id":jid,"status":"submitted","url":""})[0],400)
        result={"id":jid,"status":"submitted","url":"https://chatgpt.com/c/abcde"}
        status,value=self.request("/v1/result","POST",result)
        self.assertEqual(status,200)
        self.assertEqual(value["status"],"submitted")
        self.assertEqual(self.request("/v1/result","POST",result)[1]["duplicate"],True)

    def test_operational_hold_blocks_new_claims_but_not_existing_results(self):
        existing="01234567-89ab-4cde-8901-000000000001"
        pending="01234567-89ab-4cde-8901-000000000002"
        self.put(existing)
        self.assertEqual(self.request("/v1/next")[1]["job"]["id"],existing)
        self.put(pending)
        bridge.ADMISSION_HOLD.write_text('{"reason":"load25_pre_send_failures"}',encoding="utf-8")
        code,blocked=self.request("/v1/next")
        self.assertEqual(code,200)
        self.assertTrue(blocked["busy"])
        self.assertIsNone(blocked["job"])
        self.assertEqual(blocked["reason"],"admission_held")
        self.assertTrue((self.queue/(pending+".json")).exists())
        self.assertTrue((self.active/(existing+".json")).exists())
        # An in-flight result must remain acceptable while admission is held.
        status,result=self.request("/v1/result","POST",{
            "id":existing,"status":"done","url":"https://chatgpt.com/c/test"
        })
        self.assertEqual(status,200)
        self.assertEqual(result["status"],"done")
        self.assertTrue((self.queue/(pending+".json")).exists())
        bridge.ADMISSION_HOLD.unlink()
        self.assertEqual(self.request("/v1/next")[1]["job"]["id"],pending)


    def test_armed_role_campaign_triggers_refill_when_queue_empty(self):
        import role_campaign
        jid="01234567-89ab-4cde-8901-000000000091"
        with patch.object(bridge,"STATE",self.root):
            self.assertIsNone(self.request("/v1/next")[1]["job"])
            (self.root/"role-campaign-armed.json").write_text('{"enabled":true}',encoding="utf-8")
            with patch.object(role_campaign,"refill_unlocked",side_effect=lambda:self.put(jid)) as refill:
                status,value=self.request("/v1/next")
            self.assertEqual(status,200)
            self.assertEqual(value["job"]["id"],jid)
            refill.assert_called_once_with()
            self.assertTrue((self.active/(jid+".json")).is_file())
            self.assertFalse((self.queue/(jid+".json")).exists())

    def test_role_campaign_not_refilled_during_admission_hold(self):
        import role_campaign
        (self.root/"role-campaign-armed.json").write_text('{"enabled":true}',encoding="utf-8")
        bridge.ADMISSION_HOLD.write_text("hold",encoding="utf-8")
        with patch.object(bridge,"STATE",self.root):
            with patch.object(role_campaign,"refill_unlocked") as refill:
                status,value=self.request("/v1/next")
        self.assertEqual(status,200)
        self.assertEqual(value["reason"],"admission_held")
        refill.assert_not_called()

    def test_role_provenance_survives_result_without_prompt(self):
        jid="01234567-89ab-4cde-8901-000000000093"
        self.put(jid)
        item=self.queue/(jid+".json")
        value=json.loads(item.read_text(encoding="utf-8"))
        meta={"campaign_id":"eng1","ticket_id":"claim-fairness",
              "stage":"feature","manifest_sha256":"a"*64}
        value.update(role_metadata=meta,role="rw-eng1-claim-fairness-feature",
                     brief_sha256="c"*64)
        item.write_text(json.dumps(value),encoding="utf-8")
        import role_campaign
        with patch.object(role_campaign,"claim_allowed",return_value=True):
            self.assertEqual(self.request("/v1/next")[1]["job"]["id"],jid)
        status,result=self.request("/v1/result","POST",{
          "id":jid,"status":"done","url":"https://chatgpt.com/c/test"})
        self.assertEqual(status,200)
        saved=json.loads((self.done/(jid+".json")).read_text(encoding="utf-8"))
        self.assertEqual(saved["role_metadata"],meta)
        self.assertEqual(saved["role"],value["role"])
        self.assertEqual(saved["brief_sha256"],"c"*64)
        self.assertNotIn("prompt",saved)


    def test_role_claim_rejected_without_owner_arm(self):
        import role_campaign
        jid="01234567-89ab-4cde-8901-000000000094"
        self.put(jid)
        f=self.queue/(jid+".json")
        job=json.loads(f.read_text(encoding="utf-8"))
        job["role_metadata"]={"campaign_id":"eng1","ticket_id":"claim-fairness",
             "stage":"developer","manifest_sha256":"a"*64}
        f.write_text(json.dumps(job),encoding="utf-8")
        with patch.object(role_campaign,"claim_allowed",return_value=False) as eligible:
            status,result=self.request("/v1/next")
        self.assertEqual(status,200)
        self.assertEqual(result["reason"],"role_claim_not_ready")
        self.assertTrue(f.is_file())
        self.assertFalse((self.active/(jid+".json")).exists())
        eligible.assert_called_once()


    def test_chain2_dispatch_uses_new_role_controller(self):
        import chain_campaign
        jid="01234567-89ab-4cde-8901-000000000099"
        with patch.object(bridge,"STATE",self.root):
            (self.root/"role-campaign-armed.json").write_text(
                '{"enabled":true,"campaign_id":"chain2"}',encoding="utf-8")
            with patch.object(chain_campaign,"refill_unlocked",
                              side_effect=lambda:self.put(jid)) as refill:
                status,value=self.request("/v1/next")
        self.assertEqual(status,200)
        self.assertEqual(value["job"]["id"],jid)
        refill.assert_called_once_with()

    def test_chain2_requires_handoff_before_report_ack(self):
        import chain_campaign
        jid="01234567-89ab-4cde-8901-000000000098"
        self.put(jid)
        f=self.queue/(jid+".json")
        data=json.loads(f.read_text(encoding="utf-8"))
        data.update(completion_mode="report",report_name="completion-"+jid+".md",
                    role_metadata={"campaign_id":"chain2","ticket_id":"claim-fairness",
                      "stage":"developer","manifest_sha256":"a"*64})
        f.write_text(json.dumps(data),encoding="utf-8")
        self.request("/v1/heartbeat","POST",{
            "version":"0.6.6","state":"polling","windows":1})
        with patch.object(chain_campaign,"claim_allowed",return_value=True):
            self.assertEqual(self.request("/v1/next")[1]["job"]["id"],jid)
        status,ack=self.request("/v1/ack?id="+jid)
        self.assertEqual(status,200)
        self.assertEqual(ack["pending"],"handoff_not_requested")
        status,receipt=self.request("/v1/result","POST",{
          "id":jid,"status":"done","url":"https://chatgpt.com/c/test",
          "ack_id":"a"*32})
        self.assertEqual(status,409)
        self.assertEqual(receipt["error"],"handoff_request_required")
        self.assertTrue((self.active/(jid+".json")).is_file())
        self.assertFalse((self.done/(jid+".json")).exists())

    def test_chain2_report_ack_only_after_verified_handoff(self):
        import chain_campaign
        jid="01234567-89ab-4cde-8901-000000000097"
        self.put(jid)
        f=self.queue/(jid+".json")
        data=json.loads(f.read_text(encoding="utf-8"))
        data.update(completion_mode="report",report_name="completion-"+jid+".md",
                    role_metadata={"campaign_id":"chain2","ticket_id":"claim-fairness",
                      "stage":"developer","manifest_sha256":"a"*64})
        f.write_text(json.dumps(data),encoding="utf-8")
        self.request("/v1/heartbeat","POST",{
            "version":"0.6.6","state":"polling","windows":1})
        with patch.object(chain_campaign,"claim_allowed",return_value=True):
            self.assertEqual(self.request("/v1/next")[1]["job"]["id"],jid)
        with patch.object(chain_campaign,"handoff_requested_for_job",return_value=False):
            self.assertEqual(self.request("/v1/ack?id="+jid)[1]["pending"],
                             "handoff_not_requested")
        with patch.object(chain_campaign,"handoff_requested_for_job",return_value=True):
            self.assertEqual(self.request("/v1/ack?id="+jid)[1]["pending"],
                             "report_not_found")

    def test_recovery_scan_v064_reports_durable_ownership_only_as_counts(self):
        payload={"tabs_total":17,"chat_tabs":12,"other_chatgpt_tabs":1,
                 "non_chatgpt_tabs":4,"unknown_url_tabs":0,"discarded":5,
                 "attempted":8,"identified":4,"unsaved_identified":1,
                 "eligible":2,"recovered":2,"ambiguous":1,"injection_failed":2,
                 "stored_owned":3,"stored_invalid":1,"stored_conflict":1,
                 "restored_from_local":1,"recovered_dom_only":1}
        status,response=self.request("/v1/recovery-scan","POST",payload)
        self.assertEqual(status,200)
        self.assertTrue(response["ok"])
        saved=json.loads((self.diag/"recovery-scan.json").read_text(encoding="utf-8"))
        for k,v in payload.items():
            self.assertEqual(saved[k],v)
        self.assertFalse(any(k in saved for k in
            ("urls","tab_ids","prompt","cookies","account","chat_contents")))
        for case in (
            {**payload,"restored_from_local":3},
            {**payload,"recovered_dom_only":2},
            {**payload,"stored_conflict":2},
            {**payload,"stored_invalid":False},
            {**payload,"private_chat_url":"https://chatgpt.com/c/private"}):
            self.assertEqual(self.request("/v1/recovery-scan","POST",case)[0],400)
            self.assertEqual(
                json.loads((self.diag/"recovery-scan.json").read_text(encoding="utf-8")),
                saved)
        # Rolling upgrade: v0.6.3 scan shape is still accepted read-only.
        old={k:v for k,v in payload.items() if k not in (
            "stored_owned","stored_invalid","stored_conflict",
            "restored_from_local","recovered_dom_only")}
        self.assertEqual(self.request("/v1/recovery-scan","POST",old)[0],200)

    def test_recovery_scan_aggregate_counts_only(self):
        empty={"tabs_total":17,"chat_tabs":12,"other_chatgpt_tabs":1,
               "non_chatgpt_tabs":4,"unknown_url_tabs":0,"discarded":5,
               "attempted":8,"identified":4,"unsaved_identified":1,"eligible":2,
               "recovered":2,"ambiguous":1,"injection_failed":2}
        status,out=self.request("/v1/recovery-scan","POST",empty)
        self.assertEqual(status,200)
        self.assertTrue(out["ok"])
        p=self.diag/"recovery-scan.json"
        stored=json.loads(p.read_text(encoding="utf-8"))
        self.assertEqual({k:stored[k] for k in empty},empty)
        self.assertIn("at",stored)
        self.assertFalse(any(k in stored for k in ("urls","prompt","account","tab_ids")))
        invalid_cases=[
            {**empty,"extra":"https://chatgpt.com/c/private"},
            {**empty,"recovered":4},
            {**empty,"discarded":14},
            {**empty,"chat_tabs":True},
            {**empty,"attempted":1001},
            {**empty,"unsaved_identified":5},
            {**empty,"non_chatgpt_tabs":5}
        ]
        for case in invalid_cases:
            self.assertEqual(self.request("/v1/recovery-scan","POST",case)[0],400)
            self.assertEqual(json.loads(p.read_text(encoding="utf-8")),stored)

    def test_recovery_requires_active_saved_job_without_conflicts(self):
        jid="01234567-89ab-4cde-8901-000000000031"
        self.put(jid)
        path=self.queue/(jid+".json")
        obj=json.loads(path.read_text(encoding="utf-8"))
        obj.update(completion_mode="report",report_name="completion-"+jid+".md")
        path.write_text(json.dumps(obj),encoding="utf-8")
        self.request("/v1/heartbeat","POST",{
            "version":"0.6.6","state":"polling","windows":1})
        status,result=self.request("/v1/recover?id="+jid)
        self.assertEqual(status,200)
        self.assertFalse(result["eligible"])  # Queued, not claimed.
        self.assertEqual(self.request("/v1/next")[1]["job"]["id"],jid)
        self.assertFalse(self.request("/v1/recover?id="+jid)[1]["eligible"])
        self.request("/v1/event","POST",{"id":jid,"stage":"send_clicked"})
        self.assertFalse(self.request("/v1/recover?id="+jid)[1]["eligible"])
        self.request("/v1/event","POST",{"id":jid,"stage":"message_saved"})
        eligible=self.request("/v1/recover?id="+jid)[1]
        self.assertEqual(eligible,{"eligible":True,"reason":"eligible"})
        self.assertEqual(self.request("/v1/recover?id=garbage")[1]["eligible"],False)
        self.request("/v1/event","POST",{"id":jid,"stage":"tab_binding_disputed"})
        self.assertEqual(self.request("/v1/recover?id="+jid)[1]["reason"],
                         "needs_reconciliation")
        self.request("/v1/event","POST",{"id":jid,"stage":"unknown",
                                      "reason":"account_context_changed"})
        disputed=self.request("/v1/recover?id="+jid)[1]
        self.assertFalse(disputed["eligible"])
        self.assertEqual(disputed["reason"],"needs_reconciliation")

    def test_hard_cap_twenty_five_active_jobs(self):
        for i in range(30):
            jid="01234567-89ab-4cde-8901-"+str(i).zfill(12)
            self.put(jid)
        ids=[]
        for _ in range(bridge.MAX_ACTIVE_JOBS):
            found=self.request("/v1/next")[1]["job"]
            self.assertIsNotNone(found)
            ids.append(found["id"])
        self.assertEqual(bridge.MAX_ACTIVE_JOBS,25)
        self.assertEqual(len(set(ids)),25)
        blocked=self.request("/v1/next")[1]
        self.assertEqual(blocked.get("busy"),True)
        self.assertEqual(blocked.get("active"),25)
        self.assertEqual(len(list(self.queue.glob("*.json"))),5)
        result={"id":ids[0],"status":"done","url":"https://chatgpt.com/c/test"}
        self.assertEqual(self.request("/v1/result","POST",result)[0],200)
        refill=self.request("/v1/next")[1]["job"]
        self.assertIsNotNone(refill)
        self.assertEqual(len(list(self.active.glob("*.json"))),25)
        self.assertEqual(len(list(self.queue.glob("*.json"))),4)

    def test_telemetry_is_persisted_and_bounded(self):
        jid="01234567-89ab-4cde-8901-23456789abcd"
        self.put(jid)
        self.request("/v1/next")
        for i in range(65):
            status,body=self.request("/v1/event","POST",{
                "id":jid,"stage":"editor_found","reason":""
            })
            self.assertEqual(status,200)
        events=json.loads((self.diag/(jid+".json")).read_text(encoding="utf-8"))
        self.assertEqual(len(events),60)
        self.assertEqual(events[-1]["stage"],"editor_found")
        self.assertIn("at",events[-1])
        # Nothing contains a prompt, browser credentials or free-text values.
        self.assertTrue(all(set(e).issubset({"at","stage","reason"}) for e in events))

    def test_telemetry_denies_invalid_and_unknown(self):
        jid="01234567-89ab-4cde-8901-23456789abcd"
        self.assertEqual(self.request("/v1/event","POST",{"id":jid,"stage":"send_clicked"})[0],404)
        self.put(jid)
        self.request("/v1/next")
        self.assertEqual(self.request("/v1/event","POST",{
            "id":jid,"stage":"send_clicked","reason":"paste some secret"
        })[0],400)
        self.assertEqual(self.request("/v1/event","POST",{
            "id":jid,"stage":"../../secret"
        })[0],400)
        self.assertEqual(self.request("/v1/event","POST",{
            "id":jid,"stage":"send_clicked"
        },key="wrong")[0],403)

    def test_extension_heartbeat(self):
        status,data=self.request("/v1/heartbeat","POST",{
            "version":"0.2.3","state":"polling","windows":1
        })
        self.assertEqual(status,200)
        ext=json.loads((self.diag/"extension.json").read_text(encoding="utf-8"))
        self.assertEqual(ext["version"],"0.2.3")
        self.assertEqual(ext["windows"],1)
        self.assertEqual(self.request("/v1/heartbeat","POST",{
            "version":"0.2.3","state":"polling","windows":-1
        })[0],400)

    def test_browser_response_captured_without_leaking_to_receipt(self):
        jid="01234567-89ab-4cde-8901-23456789abcd"
        self.put(jid)
        self.request("/v1/next")
        response="Независимый вывод исследовательского агента."
        res={"id":jid,"status":"done",
             "url":"https://chatgpt.com/c/abcde","response_text":response}
        status,value=self.request("/v1/result","POST",res)
        self.assertEqual(status,200)
        path=self.reports/("chat-"+jid+".md")
        self.assertEqual(path.read_text(encoding="utf-8"),response)
        record=json.loads((self.done/(jid+".json")).read_text(encoding="utf-8"))
        self.assertEqual(record["response_chars"],len(response))
        self.assertNotIn("response_text",record)

    def test_completion_rejects_legacy_stale_and_paused_browser(self):
        jid="01234567-89ab-4cde-8901-23456789abcd"
        job={"id":jid,"prompt":"report","completion_mode":"report",
             "report_name":"completion-"+jid+".md"}
        queued=self.queue/(jid+".json")
        queued.write_text(json.dumps(job),encoding="utf-8")
        def blocked():
            status,res=self.request("/v1/next")
            self.assertEqual(status,200)
            self.assertIsNone(res["job"])
            self.assertEqual(res["reason"],"completion_client_not_ready")
            self.assertTrue(queued.is_file())
            self.assertFalse((self.active/(jid+".json")).exists())
        blocked() # no heartbeat: fail closed
        self.request("/v1/heartbeat","POST",{
            "version":"0.4.2","state":"polling","windows":1})
        blocked() # currently installed older browser extension
        self.request("/v1/heartbeat","POST",{
            "version":"0.5.0","state":"polling","windows":1})
        blocked() # v0.5.0 cannot claim v0.6.0 tasks
        self.request("/v1/heartbeat","POST",{
            "version":"0.6.0","state":"polling","windows":1})
        blocked() # pre-recovery worker is not eligible
        self.request("/v1/heartbeat","POST",{
            "version":"0.6.1","state":"polling","windows":1})
        blocked() # v0.6.1 still has uninstrumented recovery
        self.request("/v1/heartbeat","POST",{
            "version":"0.6.6","state":"paused","windows":1})
        blocked() # user deliberately paused due to account mismatch
        (self.diag/"extension.json").write_text(json.dumps({
            "at":"2020-01-01T00:00:00+00:00","version":"0.6.6",
            "state":"polling","windows":1}),encoding="utf-8")
        blocked() # stale heartbeat from a dead browser
        self.request("/v1/heartbeat","POST",{
            "version":"0.6.6","state":"polling","windows":1})
        claimed=self.request("/v1/next")[1]["job"]
        self.assertEqual(claimed["id"],jid)
        self.assertFalse(queued.exists())

    def test_completion_requires_durable_bound_report_ack(self):
        jid="01234567-89ab-4cde-8901-23456789abcd"
        job={"id":jid,"prompt":"report","completion_mode":"report",
             "report_name":"completion-"+jid+".md"}
        (self.queue/(jid+".json")).write_text(json.dumps(job),encoding="utf-8")
        self.request("/v1/heartbeat","POST",{
            "version":"0.6.6","state":"polling","windows":1})
        self.assertEqual(self.request("/v1/next")[1]["job"]["id"],jid)
        pending=self.request("/v1/ack?id="+jid)[1]
        self.assertIsNone(pending["ack"])
        self.assertEqual(pending["pending"],"report_not_found")
        done={"id":jid,"status":"done","url":"https://chatgpt.com/c/abcdef"}
        self.assertEqual(self.request("/v1/result","POST",done)[0],409)
        # Self-declared role is advisory; this test verifies file binding and
        # report content integrity but does NOT prove host actor identity.
        report=self.reports/("completion-"+jid+".md")
        report.write_text("# Report\\nJob ID: "+jid+
          "\\n## Outcome\\nDelivered scoped result with concrete evidence.\\n"+
          "## Evidence\\nMeasured and tested the negative cases.\\n",encoding="utf-8")
        code,res=self.request("/v1/ack?id="+jid)
        self.assertEqual(code,200)
        ack=res["ack"]
        self.assertEqual(ack["job_id"],jid)
        self.assertEqual(ack["trust_level"],"advisory_artifact_integrity_only")
        self.assertEqual(self.request("/v1/ack?id="+jid)[1]["ack"],ack)
        wrong={**done,"ack_id":"bad"}
        self.assertEqual(self.request("/v1/result","POST",wrong)[0],409)
        self.assertTrue((self.active/(jid+".json")).exists())
        accepted={**done,"ack_id":ack["ack_id"]}
        code,res=self.request("/v1/result","POST",accepted)
        self.assertEqual(code,200)
        saved=json.loads((self.done/(jid+".json")).read_text(encoding="utf-8"))
        self.assertEqual(saved["report_sha256"],ack["artifact_sha256"])
        self.assertEqual(saved["report_ack_id"],ack["ack_id"])
        self.assertEqual(self.request("/v1/result","POST",accepted)[1]["duplicate"],True)

    def test_completion_report_must_bind_job_and_be_immutable(self):
        jid="01234567-89ab-4cde-8901-23456789abcd"
        self.put(jid)
        path=self.queue/(jid+".json")
        job=json.loads(path.read_text())
        job.update(completion_mode="report",report_name="completion-"+jid+".md")
        path.write_text(json.dumps(job))
        self.request("/v1/heartbeat","POST",{
            "version":"0.6.6","state":"polling","windows":1})
        self.request("/v1/next")
        report=self.reports/("completion-"+jid+".md")
        report.write_text("# Report\\n## Outcome\\nThis is the wrong job\\n"+
            "## Evidence\\nStill not the authorized receipt. This sample is long enough to satisfy size threshold while still lacking a job binding.\\n",encoding="utf-8")
        self.assertEqual(self.request("/v1/ack?id="+jid)[1]["pending"],"report_binding_invalid")
        report.write_text("# Report\\nJob ID: "+jid+
          "\\n## Outcome\\nValid evidence for report integrity.\\n"+
          "## Evidence\\nAn actual reproducible test was run.\\n",encoding="utf-8")
        ack=self.request("/v1/ack?id="+jid)[1]["ack"]
        self.assertIsNotNone(ack)
        report.write_text(report.read_text()+"Unexpected post-ack mutation\\n",encoding="utf-8")
        self.assertEqual(self.request("/v1/ack?id="+jid)[1]["pending"],"accepted_report_changed")
        self.assertEqual(self.request("/v1/result","POST",{"id":jid,"status":"done",
            "url":"https://chatgpt.com/c/abcdef","ack_id":ack["ack_id"]})[0],409)
        self.assertTrue((self.active/(jid+".json")).exists())

    def test_unknown_does_not_replay(self):
        jid="01234567-89ab-4cde-8901-23456789abcd";self.put(jid)
        self.request("/v1/next")
        result={"id":jid,"status":"unknown","url":""}
        self.assertEqual(self.request("/v1/result","POST",result)[0],200)
        self.assertIsNone(self.request("/v1/next")[1]["job"])
    def test_queued_followup_only_after_result(self):
        first="01234567-89ab-4cde-8901-23456789abcd"
        second="11234567-89ab-4cde-8901-23456789abcd"
        self.put(first); self.put(second)
        self.assertEqual(self.request("/v1/next")[1]["job"]["id"],first)
        self.assertEqual(self.request("/v1/next")[1]["job"]["id"],second)
        self.assertIsNone(self.request("/v1/next")[1]["job"])
        self.request("/v1/result","POST",{"id":first,"status":"unknown","url":""})

if __name__=="__main__":unittest.main(verbosity=2)

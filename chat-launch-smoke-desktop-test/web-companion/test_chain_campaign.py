"""Isolated two-role handoff and successor generation tests; never touch live queue."""
import hashlib,json,shutil,tempfile,unittest
from contextlib import ExitStack
from datetime import datetime,timezone
from pathlib import Path
from unittest.mock import patch
import chain_campaign as cc
import role_campaign as legacy
import enqueue_completion as ec

class ChainTests(unittest.TestCase):
 def setUp(self):
  tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
  self.base=Path(tmp.name)/"project"
  self.root=self.base/"web-companion"
  self.state=self.root/"bridge-state"
  self.work=self.base/"experiments"/"role-workstreams"
  self.briefs=self.base/"agent-briefs"
  self.reports=self.base/"agent-reports"
  self.campaign=self.root/"role-campaigns"
  for d in (self.briefs,self.reports,self.campaign):
   d.mkdir(parents=True)
  for folder in ("queue","active","done","completion-acks","handoff-requests","diagnostics"):
   (self.state/folder).mkdir(parents=True)
  for name in ("eng1.json","sdd1.json","chain2.json"):
   shutil.copyfile(cc.ROOT/"role-campaigns"/name,self.campaign/name)
  stack=ExitStack();self.addCleanup(stack.close)
  attrs=[
   (cc,"ROOT",self.root),(cc,"PROJECT",self.base),(cc,"STATE",self.state),
   (cc,"QUEUE",self.state/"queue"),(cc,"ACTIVE",self.state/"active"),(cc,"DONE",self.state/"done"),
   (cc,"ACKS",self.state/"completion-acks"),(cc,"REPORTS",self.reports),
   (cc,"BRIEFS",self.briefs),(cc,"HOLD",self.state/"admission-hold.json"),
   (cc,"ARM",self.state/"role-campaign-armed.json"),(cc,"CONFIG",self.campaign/"chain2.json"),
   (cc,"REQUESTS",self.state/"handoff-requests"),
   (ec,"ROOT",self.root),(ec,"PROJECT",self.base),
   (ec,"QUEUE",self.state/"queue"),(ec,"ACTIVE",self.state/"active"),(ec,"DONE",self.state/"done"),
   (legacy,"CAMPAIGNS",self.campaign),
  ]
  for ob,k,v in attrs:stack.enter_context(patch.object(ob,k,v))
  stack.enter_context(patch.object(legacy,"client_ready",return_value=True))
  hb={"version":"0.6.6","state":"polling",
      "at":datetime.now(timezone.utc).isoformat(),"windows":1}
  (self.state/"diagnostics"/"extension.json").write_text(json.dumps(hb))
  self.cfg,self.tickets,self.digest=cc.load()
  self.first=self.tickets[0]
  self.next_ticket=self.tickets[self.cfg["seed_count"]]
 def arm(self):
  cc.ARM.write_text(json.dumps({"enabled":True,"campaign_id":"chain2",
    "manifest_sha256":self.digest,"max_total_stages":50}))
 def record(self,ticket,role,state="active",status=None):
  id=cc.jid(ticket,role)
  meta={"campaign_id":"chain2","ticket_id":ticket,"stage":role,
        "manifest_sha256":self.digest}
  obj={"id":id,"role":cc.role_name(ticket,role),
       "role_metadata":meta,"completion_mode":"report",
       "report_name":"completion-"+id+".md"}
  if status is not None:obj["status"]=status
  (getattr(cc,state.upper())/(id+".json")).write_text(json.dumps(obj))
  return id
 def handoff(self,ticket,role):
  id=cc.jid(ticket,role)
  folder=cc.workspace(ticket,role)
  folder.mkdir(parents=True,exist_ok=True)
  (folder/"implementation.py").write_text("def run(): return 1\n")
  pt,pr=cc.parent_for(ticket,role,self.tickets,self.cfg["seed_count"])
  upstream=(f"chat-launch-smoke-desktop-test/experiments/role-workstreams/chain2/"
            f"{pt}/{pr}/handoff.json" if pt else None)
  data={"version":1,"parent_job_id":id,"upstream_handoff":upstream,
        "summary":"Implemented substantive interface and tests.",
        "changed_files":["implementation.py"],"tests":["python -m unittest: PASS 14"],
        "issues":["Await independent review."],
        "feature_proposals":["Add resilience metrics after review."],
        "next_focus":"Reproduce tests, review edge cases and carry approved work forward."}
  cc.handoff_path(ticket,role).write_text(json.dumps(data))
  return data
 def finalize(self,ticket,role):
  id=cc.jid(ticket,role)
  source=cc.ACTIVE/(id+".json")
  doc=json.loads(source.read_text())
  report=cc.REPORTS/("completion-"+id+".md")
  report.write_text("Job ID: "+id+"\n## Outcome\nDone\n## Evidence\nTests run\n")
  sha=hashlib.sha256(report.read_bytes()).hexdigest()
  doc.update(status="done",report_sha256=sha,report_ack_id="a"*32)
  (cc.ACKS/(id+".json")).write_text(json.dumps({"job_id":id,"status":"REPORT_ACKED",
                 "artifact_sha256":sha,"ack_id":"a"*32}))
  source.unlink()
  (cc.DONE/(id+".json")).write_text(json.dumps(doc))
  return id

 def test_loopback_full_handoff_ack_and_one_successor(self):
  import bridge,threading,urllib.request,urllib.error
  self.arm()
  seeds=cc.refill_unlocked()
  self.assertEqual(seeds["queued"],self.cfg["seed_count"])
  # A real isolated HTTP server exercises admission, ACK and DONE endpoints.
  with ExitStack() as patcher:
   for name,val in (
     ("STATE",self.state),("QUEUE",cc.QUEUE),("ACTIVE",cc.ACTIVE),
     ("DONE",cc.DONE),("ACKS",cc.ACKS),("REPORTS",cc.REPORTS),
     ("DIAG",self.state/"diagnostics"),("ADMISSION_HOLD",cc.HOLD)):
    patcher.enter_context(patch.object(bridge,name,val))
   server=bridge.Server(("127.0.0.1",0),bridge.Handler)
   server.bridge_token="test-role-secret-1234567890123456789012"
   thread=threading.Thread(target=server.serve_forever,daemon=True)
   thread.start()
   try:
    def req(path,body=None):
     data=json.dumps(body).encode() if body is not None else None
     request=urllib.request.Request(
       f"http://127.0.0.1:{server.server_port}"+path,
       data=data,method="POST" if body is not None else "GET",
       headers={"X-Chat-Smoke-Key":server.bridge_token,
                "Content-Type":"application/json",
                "Origin":"chrome-extension://test"})
     try:
      with urllib.request.urlopen(request,timeout=3) as r:
       return r.status,json.load(r)
     except urllib.error.HTTPError as ex:
      return ex.code,json.load(ex)
    status,claimed=req("/v1/next")
    self.assertEqual(status,200)
    current=claimed["job"]
    self.assertEqual(current["role_metadata"]["stage"],"developer")
    ticket=current["role_metadata"]["ticket_id"]
    id=current["id"]
    self.assertEqual(req("/v1/ack?id="+id)[1]["pending"],"handoff_not_requested")
    self.handoff(ticket,"developer")
    requested=cc.request(id)
    self.assertEqual(requested["successor"],cc.jid(ticket,"reviewer"))
    self.assertTrue(cc.request(id)["already_requested"])
    self.assertEqual(req("/v1/ack?id="+id)[1]["pending"],"report_not_found")
    report=cc.REPORTS/("completion-"+id+".md")
    report.write_text("Job ID: "+id+"\n## Outcome\n"
       "Substantial implementation and independent test evidence documented.\n"
       "## Evidence\nFunctional tests and negative cases checked; hashes recorded.\n"
       "## Tests\n18 passed, no unknown results.\n",encoding="utf-8")
    status,result=req("/v1/ack?id="+id)
    self.assertEqual(status,200)
    ack=result["ack"]
    self.assertEqual(ack["status"],"REPORT_ACKED")
    self.assertEqual(req("/v1/result",{"id":id,"status":"done",
       "url":"https://chatgpt.com/c/test","ack_id":ack["ack_id"]})[0],200)
    self.assertTrue((cc.DONE/(id+".json")).is_file())
    self.assertTrue(cc.accepted(ticket,"developer",self.digest,self.tickets,self.cfg["seed_count"]))
    for _ in range(self.cfg["seed_count"]-1):
     status,job=req("/v1/next")
     self.assertEqual(status,200)
     self.assertIsNotNone(job["job"])
    status,next_job=req("/v1/next")
    self.assertEqual(status,200)
    self.assertEqual(next_job["job"]["role_metadata"]["stage"],"reviewer")
    self.assertEqual(next_job["job"]["role_metadata"]["ticket_id"],ticket)
    self.assertEqual(next_job["job"]["id"],cc.jid(ticket,"reviewer"))
    self.assertEqual(len(list(cc.REQUESTS.glob("*.json"))),1)
   finally:
    server.shutdown();server.server_close();thread.join(3)


 def test_reviewer_brief_reviews_proposals_not_autoruns(self):
  ticket=self.first
  body=cc.brief_text(ticket,"reviewer",cc.jid(ticket["id"],"reviewer"),
                     (ticket["id"],"developer"))
  self.assertIn("ACCEPT/REWORK/BLOCKED",body)
  self.assertIn("feature_proposals",body)
  self.assertIn("Не запускай предложенные фичи",body)
  self.assertIn("upstream_handoff=",body)

 def test_wrong_upstream_handoff_blocks_request(self):
  self.arm()
  id=self.record(self.first["id"],"developer")
  body=self.handoff(self.first["id"],"developer")
  body["upstream_handoff"]="other"
  cc.handoff_path(self.first["id"],"developer").write_text(json.dumps(body))
  with self.assertRaisesRegex(cc.ChainError,"handoff_upstream_mismatch"):
   cc.request(id)

 def test_manifest_has_two_roles_and_finite_50_stages(self):
  out=cc.preview()
  self.assertEqual(out["roles"],["developer","reviewer"])
  self.assertEqual((out["features"],out["stage_jobs"],out["root_lanes"]),(25,50,10))
  self.assertFalse(out["armed"])
 def test_unarmed_refill_never_creates_jobs(self):
  self.assertEqual(cc.refill_unlocked()["reason"],"not_armed")
  self.assertEqual(list(cc.QUEUE.glob("*.json")),[])
 def test_hold_blocks_refill_and_arm(self):
  cc.HOLD.write_text("held")
  self.arm()
  self.assertEqual(cc.refill_unlocked()["reason"],"admission_held")
  with self.assertRaisesRegex(cc.ChainError,"admission_held"):cc.arm(confirm=True)
 def test_arm_needs_explicit_confirmation(self):
  with self.assertRaisesRegex(cc.ChainError,"explicit_owner_arm"):cc.arm()
 def test_no_new_jobs_when_old_active_exists(self):
  cc.ACTIVE.joinpath("legacy.json").write_text("{}")
  with self.assertRaisesRegex(cc.ChainError,"existing_jobs"):cc.arm(confirm=True)
 def test_refill_seeds_exactly_ten_developers_not_reviewers(self):
  self.arm()
  value=cc.refill_unlocked()
  self.assertEqual(value["queued"],self.cfg["seed_count"])
  self.assertTrue(all(x["role"]=="developer" for x in value["jobs"]))
  self.assertEqual(len(list(cc.QUEUE.glob("*.json"))),self.cfg["seed_count"])
  self.assertEqual(cc.refill_unlocked()["reason"],"capacity")
 def test_start_brief_never_requests_successor(self):
  self.arm();v=cc.refill_unlocked()["jobs"][0]
  path=cc.BRIEFS/(cc.role_name(v["ticket"],"developer")+".md")
  t=path.read_text(encoding="utf-8")
  self.assertIn("Не делай никаких новых агентов В НАЧАЛЕ",t)
  self.assertIn("## Один преемник В КОНЦЕ",t)
  self.assertIn('args=["request","--parent",',t)
 def test_no_request_for_unknown_or_unarmed_parent(self):
  id=self.record(self.first["id"],"developer")
  self.handoff(self.first["id"],"developer")
  with self.assertRaisesRegex(cc.ChainError,"campaign_not_authorized"):
   cc.request(id)
  self.arm()
  with self.assertRaisesRegex(cc.ChainError,"parent_not_active"):
   cc.request("01234567-89ab-4cde-8901-234567890abc")
 def test_handoff_validates_actual_artifacts_and_no_traversal(self):
  self.arm();id=self.record(self.first["id"],"developer")
  body=self.handoff(self.first["id"],"developer")
  self.assertEqual(len(cc.read_handoff(id,self.first["id"],"developer")),64)
  body["changed_files"]=["../secret.txt"]
  cc.handoff_path(self.first["id"],"developer").write_text(json.dumps(body))
  with self.assertRaisesRegex(cc.ChainError,"unsafe_artifact_path"):
   cc.request(id)
 def test_request_only_after_work_and_once(self):
  self.arm()
  id=self.record(self.first["id"],"developer")
  with self.assertRaisesRegex(cc.ChainError,"handoff_missing"):cc.request(id)
  self.handoff(self.first["id"],"developer")
  result=cc.request(id)
  self.assertTrue(result["requested"]);self.assertFalse(result["queued"])
  self.assertEqual(result["successor"],cc.jid(self.first["id"],"reviewer"))
  self.assertTrue(cc.request(id)["already_requested"])
  self.assertEqual(len(list(cc.REQUESTS.glob("*.json"))),1)
  self.assertEqual(len(list(cc.QUEUE.glob("*.json"))),0)
 def test_mutating_handoff_after_request_blocks_ack_and_successor(self):
  self.arm();id=self.record(self.first["id"],"developer")
  body=self.handoff(self.first["id"],"developer")
  cc.request(id)
  body["summary"]="Changed after immutable request, no longer accepted."
  cc.handoff_path(self.first["id"],"developer").write_text(json.dumps(body))
  record=json.loads((cc.ACTIVE/(id+".json")).read_text())
  self.assertFalse(cc.handoff_requested_for_job(record))
  self.finalize(self.first["id"],"developer")
  self.assertFalse(cc.accepted(self.first["id"],"developer",self.digest,self.tickets,self.cfg["seed_count"]))
 def test_handoff_without_done_does_not_start_reviewer(self):
  self.arm();id=self.record(self.first["id"],"developer")
  self.handoff(self.first["id"],"developer")
  cc.request(id)
  options=list(cc.ready(self.cfg,self.tickets,self.digest))
  self.assertFalse(any(t["id"]==self.first["id"] and r=="reviewer"
                       for t,r,_ in options))
 def test_done_report_and_handoff_all_required_to_start_reviewer(self):
  self.arm();id=self.record(self.first["id"],"developer")
  self.handoff(self.first["id"],"developer")
  cc.request(id)
  self.finalize(self.first["id"],"developer")
  self.assertTrue(cc.accepted(self.first["id"],"developer",self.digest,self.tickets,self.cfg["seed_count"]))
  self.assertTrue(any(t["id"]==self.first["id"] and r=="reviewer"
                      for t,r,_ in cc.ready(self.cfg,self.tickets,self.digest)))
 def test_reviewer_transmits_to_next_developer(self):
  self.arm()
  for role in ("developer","reviewer"):
   id=self.record(self.first["id"],role)
   self.handoff(self.first["id"],role)
   cc.request(id)
   self.finalize(self.first["id"],role)
  options=list(cc.ready(self.cfg,self.tickets,self.digest))
  self.assertTrue(any(t["id"]==self.next_ticket["id"] and r=="developer"
                      for t,r,_ in options))
 def test_no_successor_when_campaign_budget_exhausted(self):
  self.arm();last=self.tickets[-1]
  id=self.record(last["id"],"reviewer")
  self.handoff(last["id"],"reviewer")
  result=cc.request(id)
  self.assertIsNone(result["successor"])
 def test_bounded_role_claim_revalidates_handoff(self):
  self.arm()
  id=self.record(self.first["id"],"developer",state="queue")
  path=cc.BRIEFS/(cc.role_name(self.first["id"],"developer")+".md")
  cc.ensure_brief(self.first,"developer",id,(None,None))
  obj=json.loads((cc.QUEUE/(id+".json")).read_text())
  obj["brief_sha256"]=hashlib.sha256(path.read_bytes()).hexdigest()
  self.assertTrue(cc.claim_allowed(obj))
  obj["role_metadata"]["stage"]="reviewer"
  self.assertFalse(cc.claim_allowed(obj))
 def test_proposed_features_do_not_enter_queue(self):
  self.arm();id=self.record(self.first["id"],"developer")
  self.handoff(self.first["id"],"developer")
  cc.request(id);self.finalize(self.first["id"],"developer")
  jobs=cc.refill_unlocked()
  self.assertEqual(jobs["queued"],self.cfg["seed_count"])
  self.assertTrue(all(x["ticket"] in {t["id"] for t in self.tickets} for x in jobs["jobs"]))

 def test_multi_hop_developer_reviewer_developer_reviewer(self):
  self.arm()
  first=self.first["id"]; second=self.next_ticket["id"]
  steps=[(first,"developer"),(first,"reviewer"),
         (second,"developer"),(second,"reviewer")]
  for index,(ticket,role) in enumerate(steps):
   current=self.record(ticket,role)
   self.handoff(ticket,role)
   request=cc.request(current)
   target=steps[index+1] if index+1<len(steps) else None
   if target:
    self.assertEqual(request["successor"],cc.jid(*target))
   self.assertFalse(request["queued"])
   self.assertTrue(cc.request(current)["already_requested"])
   self.finalize(ticket,role)
   self.assertTrue(cc.accepted(ticket,role,self.digest,self.tickets,self.cfg["seed_count"]))
   if target:
    self.assertTrue(any(t["id"]==target[0] and next_role==target[1]
                        for t,next_role,_ in cc.ready(self.cfg,self.tickets,self.digest)))
  # A finite chain cannot loop back to already issued stage IDs.
  all_ids=[cc.jid(*pair) for pair in steps]
  self.assertEqual(len(set(all_ids)),len(steps))
  self.assertEqual(len(list(cc.REQUESTS.glob("*.json"))),4)

 def test_existing_active_can_request_while_new_claims_held(self):
  self.arm()
  id=self.record(self.first["id"],"developer")
  self.handoff(self.first["id"],"developer")
  cc.HOLD.write_text('{"state":"OPERATOR_RECONCILIATION_REQUIRED"}')
  self.assertEqual(cc.refill_unlocked()["reason"],"admission_held")
  result=cc.request(id)
  self.assertTrue(result["requested"])
  self.assertFalse(result["queued"])
  self.assertTrue(cc.request(id)["already_requested"])
  self.assertEqual(len(list(cc.QUEUE.glob("*.json"))),0)

 def test_structured_handoff_preserves_evidence_without_authority(self):
  self.arm()
  id=self.record(self.first["id"],"developer")
  body=self.handoff(self.first["id"],"developer")
  body["tests"]=[{"job_id":"synthetic","status":"completed","returncode":0,
                   "issues":["source_sha_mismatch"],"passed":15},
                  "Independent test receipt, not production authorization."]
  body["feature_proposals"]=[{"title":"test-only","review_only":True,
                              "rationale":"safe isolated scope"}]
  body["source_sha256"]={"canonical_sdd":"a"*64}
  body["files_sha256"]={"implementation.py":hashlib.sha256(
                        (cc.workspace(self.first["id"],"developer")/"implementation.py").read_bytes()).hexdigest()}
  body["owner_approval_obtained"]=False
  path=cc.handoff_path(self.first["id"],"developer")
  path.write_text(json.dumps(body))
  self.assertEqual(len(cc.read_handoff(id,self.first["id"],"developer")),64)
  body["owner_approval_obtained"]=True
  path.write_text(json.dumps(body))
  with self.assertRaisesRegex(cc.ChainError,"untrusted_authority_claim"):
   cc.read_handoff(id,self.first["id"],"developer")
  body["owner_approval_obtained"]=False
  body["files_sha256"]["implementation.py"]="b"*64
  path.write_text(json.dumps(body))
  with self.assertRaisesRegex(cc.ChainError,"handoff_artifact_sha_mismatch"):
   cc.read_handoff(id,self.first["id"],"developer")

if __name__=="__main__":unittest.main()

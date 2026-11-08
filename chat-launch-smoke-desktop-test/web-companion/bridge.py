"""Loopback-only one-chat bridge. Queued jobs are never automatically retried.
Run from this directory with: python bridge.py
"""
import hashlib
import json
import os
import re
import secrets
import threading
import uuid
from urllib.parse import urlsplit, parse_qs
from advisory_completion import evaluate, Pending
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STATE = ROOT / "bridge-state"
QUEUE = STATE / "queue"
ACTIVE = STATE / "active"
DONE = STATE / "done"
DIAG = STATE / "diagnostics"
ACKS = STATE / "completion-acks"
REPORTS = ROOT.parent / "agent-reports"
TOKEN_PATH = STATE / "token"
ADMISSION_HOLD = STATE / "admission-hold.json"
PORT = 18763
MAX_ACTIVE_JOBS = 25
LOCK = threading.RLock()

def completion_client_ready():
    """Fail closed: old/stale browser workers must not claim completion-gated jobs.

    Heartbeat is only an operational compatibility signal, not authenticated
    browser/account identity. A production security boundary still needs P085.
    """
    try:
        ext = json.loads((DIAG / "extension.json").read_text(encoding="utf-8"))
        seen = datetime.fromisoformat(ext["at"])
        age = (datetime.now(timezone.utc) - seen).total_seconds()
        return (ext.get("version") == "0.6.6"
                and ext.get("state") == "polling"
                and type(ext.get("windows")) is int and ext["windows"] >= 1
                and 0 <= age <= 120)
    except (OSError, ValueError, TypeError, KeyError):
        return False

def recovery_candidate(job_id):
    """Conservative eligibility check for reattaching a lost browser task."""
    try:
        if type(job_id) is not str:
            return False, "invalid_job_id"
        try:
            if str(uuid.UUID(job_id)) != job_id:
                return False, "invalid_job_id"
        except (ValueError,TypeError,AttributeError):
            return False, "invalid_job_id"
        record = json.loads((ACTIVE / (job_id + ".json")).read_text(encoding="utf-8"))
        if (record.get("id") != job_id or record.get("completion_mode") != "report"
                or record.get("report_name") != "completion-" + job_id + ".md"):
            return False, "invalid_job"
        events = json.loads((DIAG / (job_id + ".json")).read_text(encoding="utf-8"))
        if not isinstance(events, list) or any(not isinstance(e, dict) for e in events):
            return False, "missing_evidence"
        if any(e.get("reason") in ("account_context_changed", "unexpected_close")
               or e.get("stage") in ("unknown", "report_disputed", "tab_preserved",
                                      "tab_binding_disputed")
               for e in events):
            return False, "needs_reconciliation"
        sent = next((i for i in range(len(events)-1,-1,-1)
                     if events[i].get("stage")=="send_clicked"),None)
        saved = next((i for i in range(len(events)-1,-1,-1)
                      if events[i].get("stage")=="message_saved"),None)
        if sent is None or saved is None or saved < sent:
            return False, "missing_evidence"
        return True, "eligible"
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return False, "not_active_or_unreadable"

def receipt_closure_candidate(job_id):
    """Allow closing a previously accepted tab after MV3/browser restart.

    A DONE filename alone is not proof; require the immutable report ACK,
    matching actual report bytes, a canonical URL and the original job ID.
    """
    try:
        if type(job_id) is not str or str(uuid.UUID(job_id)) != job_id:
            return False, "invalid_job_id", None
    except (ValueError,AttributeError,TypeError):
        return False, "invalid_job_id", None
    try:
        done=DONE/(job_id+".json")
        report=REPORTS/("completion-"+job_id+".md")
        ackfile=ACKS/(job_id+".json")
        if (not done.is_file() or done.is_symlink() or not report.is_file()
            or report.is_symlink() or not ackfile.is_file() or ackfile.is_symlink()):
            return False, "receipt_or_artifact_missing", None
        saved=json.loads(done.read_text(encoding="utf-8"))
        ack=json.loads(ackfile.read_text(encoding="utf-8"))
        digest=hashlib.sha256(report.read_bytes()).hexdigest()
        if (saved.get("id")!=job_id or saved.get("status")!="done"
            or saved.get("report_path")!="agent-reports/completion-"+job_id+".md"
            or saved.get("report_sha256")!=digest
            or ack.get("job_id")!=job_id or ack.get("status")!="REPORT_ACKED"
            or ack.get("artifact_sha256")!=digest
            or not saved.get("report_ack_id")
            or saved.get("report_ack_id")!=ack.get("ack_id")):
            return False, "receipt_not_verified", None
        uri=urlsplit(saved.get("url",""))
        if (uri.scheme!="https" or uri.netloc!="chatgpt.com"
            or not re.fullmatch(r"/c/[0-9a-z-]+",uri.path)
            or uri.fragment):
            return False, "receipt_url_invalid", None
        return True, "receipt_done", "https://chatgpt.com"+uri.path
    except (OSError,ValueError,TypeError,KeyError,AttributeError):
        return False, "receipt_not_verified", None


def initialize():
    for d in (STATE, QUEUE, ACTIVE, DONE, DIAG, ACKS, REPORTS):
        d.mkdir(parents=True, exist_ok=True)
    if not TOKEN_PATH.exists():
        # A random local bearer key. Never send this to stdout or a website.
        token = secrets.token_urlsafe(32)
        fd = os.open(str(TOKEN_PATH), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            out.write(token)
    token = TOKEN_PATH.read_text(encoding="utf-8").strip()
    if len(token) < 32:
        raise RuntimeError("Bridge token too short")
    config = ROOT / "bridge-config.json"
    expected = {"endpoint": f"http://127.0.0.1:{PORT}", "key": token}
    if config.exists():
        try:
            existing = json.loads(config.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            raise RuntimeError("Invalid existing extension bridge configuration")
        if existing != expected:
            raise RuntimeError("Bridge configuration differs; do not overwrite silently")
    else:
        config.write_text(json.dumps(expected), encoding="utf-8")
    return token

class Handler(BaseHTTPRequestHandler):
    server_version = "ChatSmokeBridge/1.0"
    def log_message(self, fmt, *args):
        # Never log bearer keys, URL query parameters, prompts or conversation contents.
        pass
    def _origin(self):
        origin = self.headers.get("Origin", "")
        return origin if origin.startswith("chrome-extension://") and origin.count("/") == 2 else None
    def _headers(self, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if self._origin():
            self.send_header("Access-Control-Allow-Origin", self._origin())
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "X-Chat-Smoke-Key, Content-Type")
        self.end_headers()
    def _reply(self, status, obj):
        self._headers(status)
        self.wfile.write(json.dumps(obj, ensure_ascii=False).encode("utf-8"))
    def _deny(self, reason):
        # On Windows, rejecting a POST while leaving its body unread can reset
        # the socket before a client receives our structured HTTP 403 response.
        # Drain only a small, already-declared body; do not retain or log it.
        if self.command == "POST":
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if 0 < size <= 4096:
                    self.rfile.read(size)
                elif size > 4096:
                    self.close_connection = True
            except (ValueError, OSError):
                self.close_connection = True
        self._reply(403, {"error": reason})
        return False
    def _authorized(self):
        if self.headers.get("Host") != f"127.0.0.1:{PORT}" and self.server.server_port == PORT:
            return self._deny("bad_host")
        got = self.headers.get("X-Chat-Smoke-Key", "")
        if not secrets.compare_digest(got, self.server.bridge_token):
            return self._deny("unauthorized")
        origin = self.headers.get("Origin")
        if origin and not self._origin():
            return self._deny("invalid_origin")
        return True
    def do_OPTIONS(self):
        if not self._origin():
            self._reply(403, {"error":"invalid_origin"}); return
        self._headers(204)
    def do_GET(self):
        if not self._authorized(): return
        if self.path == "/v1/health":
            self._reply(200, {"ok":True, "bridge":"local", "ready":True});return
        if self.path.startswith("/v1/recover?"):
            query=urlsplit(self.path)
            fields=parse_qs(query.query, strict_parsing=True)
            ids=fields.get("id",[])
            if query.path!="/v1/recover" or len(ids)!=1:
                self._reply(400, {"error":"invalid_recovery_query"});return
            with LOCK:
                eligible,reason=recovery_candidate(ids[0])
                self._reply(200, {"eligible":eligible,"reason":reason});return
        if self.path.startswith("/v1/close-eligible?"):
            query=urlsplit(self.path)
            fields=parse_qs(query.query,strict_parsing=True)
            ids=fields.get("id",[])
            if query.path!="/v1/close-eligible" or len(ids)!=1:
                self._reply(400,{"error":"invalid_closure_query"});return
            with LOCK:
                eligible,reason,url=receipt_closure_candidate(ids[0])
                response={"eligible":eligible,"reason":reason}
                if eligible:response["url"]=url
                self._reply(200,response);return
        if self.path.startswith("/v1/ack?"): 
            query=urlsplit(self.path)
            fields=parse_qs(query.query, strict_parsing=True)
            jid=fields.get("id",[])
            if query.path!="/v1/ack" or len(jid)!=1:
                self._reply(400, {"error":"invalid_ack_query"});return
            with LOCK:
                candidate=ACTIVE/(jid[0]+".json")
                if candidate.is_file():
                    current=json.loads(candidate.read_text(encoding="utf-8"))
                    campaign=(current.get("role_metadata") or {}).get("campaign_id")
                    if campaign in ("chain2","chain3"):
                        if campaign=="chain2":
                            from chain_campaign import handoff_requested_for_job
                        else:
                            from chain3_continuous import handoff_requested_for_job
                        if not handoff_requested_for_job(current):
                            self._reply(200,{"ok":True,"ack":None,
                                             "pending":"handoff_not_requested"});return
                try:
                    ack=evaluate(ACTIVE,REPORTS,ACKS,jid[0])
                    self._reply(200, {"ok":True,"ack":ack});return
                except Pending as e:
                    self._reply(200, {"ok":True,"ack":None,"pending":e.code});return
        if self.path == "/v1/next":
            with LOCK:
                # An operational admission hold blocks only NEW claims.
                # Existing jobs may still fetch ACKs and submit results.
                if ADMISSION_HOLD.exists():
                    # Explicit one-shot v0.6.4 repeat experiment. Its finite,
                    # SHA-bound separate queue is the ONLY exception to the
                    # legacy hold; never claim legacy QUEUE while held.
                    if (STATE/"repeat-v064-20261103"/"permit.json").is_file():
                        try:
                            from repeat_v064 import claim_held_trial
                            trial=claim_held_trial()
                        except (OSError,ValueError,RuntimeError):
                            self._reply(409,{"error":"repeat_trial_invalid"});return
                        if trial is not None:
                            self._reply(200,{"job":trial});return
                    self._reply(200, {"job":None,"busy":True,
                                      "reason":"admission_held"});return
                # Defense in depth: 25 active jobs maximum even with a buggy extension.
                active_count = sum(1 for _ in ACTIVE.glob("*.json"))
                if active_count >= MAX_ACTIVE_JOBS:
                    self._reply(200, {"job":None, "busy":True, "active":active_count});return
                jobs = sorted(QUEUE.glob("*.json"))
                if not jobs and (STATE/"role-campaign-armed.json").is_file():
                    try:
                        armed=json.loads((STATE/"role-campaign-armed.json").read_text(encoding="utf-8"))
                        if armed.get("campaign_id")=="chain2":
                            from chain_campaign import refill_unlocked
                        else:
                            from role_campaign import refill_unlocked
                        refill_unlocked()
                        # A separately owner-armed continuation starts ONLY
                        # after all 50 original Chain2 stages have valid ACKs.
                        # The original manifest and live jobs are never rewritten.
                        if (not any(QUEUE.glob("*.json")) and
                            (STATE/"continuous-armed.json").is_file()):
                            from chain3_continuous import refill_unlocked as continuous_refill
                            continuous_refill()
                    except (OSError,ValueError,RuntimeError):
                        self._reply(409,{"error":"role_refill_failed"});return
                    jobs=sorted(QUEUE.glob("*.json"))
                if not jobs:
                    self._reply(200, {"job":None, "busy":False});return
                # A v0.4.x worker does not understand report ACKs: never
                # let it claim a job it would otherwise close too early.
                try:
                    queued = json.loads(jobs[0].read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    self._reply(409, {"error":"queued_job_invalid"});return
                if queued.get("completion_mode") == "report" and not completion_client_ready():
                    self._reply(200, {"job":None,"busy":True,
                                      "reason":"completion_client_not_ready"});return
                if queued.get("role_metadata") is not None:
                    campaign=(queued.get("role_metadata") or {}).get("campaign_id")
                    if campaign=="chain2":
                        from chain_campaign import claim_allowed
                    elif campaign=="chain3":
                        from chain3_continuous import claim_allowed
                    else:
                        from role_campaign import claim_allowed
                    if not claim_allowed(queued):
                        self._reply(200, {"job":None,"busy":True,
                                          "reason":"role_claim_not_ready"});return
                    if campaign=="chain3":
                        from chain3_continuous import reserve_claim
                        if not reserve_claim(queued):
                            self._reply(200, {"job":None,"busy":True,
                                              "reason":"continuous_rate_or_owner_gate"});return
                target = ACTIVE / jobs[0].name
                os.replace(jobs[0], target)  # durable claim, no automatic replay
                self._reply(200, {"job":queued});return
        self._reply(404, {"error":"not_found"})
    def _heartbeat(self):
        # Extension service-worker liveness without browser contents or IDs.
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 256:
                raise ValueError()
            data = json.loads(self.rfile.read(size).decode("utf-8"))
            version, state = data.get("version"), data.get("state")
            count = data.get("windows")
            if not isinstance(version, str) or len(version) > 16 or any(
                c not in "0123456789." for c in version
            ):
                raise ValueError()
            if state not in ("polling", "no_window", "ready", "paused"):
                raise ValueError()
            if type(count) is not int or not 0 <= count <= 100:
                raise ValueError()
        except (ValueError, KeyError, TypeError, UnicodeDecodeError):
            self._reply(400, {"error": "invalid_heartbeat"})
            return
        with LOCK:
            DIAG.mkdir(parents=True, exist_ok=True)
            info = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "version": version, "state": state, "windows": count}
            tmp = DIAG / "extension.writing"
            tmp.write_text(json.dumps(info), encoding="utf-8")
            os.replace(tmp, DIAG / "extension.json")
            self._reply(200, {"ok": True})

    def _recovery_scan(self):
        """Store bounded, aggregate DOM scan evidence without tab URLs or text."""
        keys=("tabs_total","chat_tabs","other_chatgpt_tabs","non_chatgpt_tabs",
              "unknown_url_tabs","discarded","attempted","identified",
              "unsaved_identified","eligible","recovered","ambiguous","injection_failed")
        extra=("stored_owned","stored_invalid","stored_conflict",
               "restored_from_local","recovered_dom_only")
        try:
            size=int(self.headers.get("Content-Length","0"))
            if not 0 < size <= 1024:
                raise ValueError()
            data=json.loads(self.rfile.read(size).decode("utf-8"))
            if type(data) is not dict or set(data) not in (
                    set(keys), set(keys)|set(extra)):
                raise ValueError()
            legacy_scan=set(data)==set(keys)
            if any(type(data[k]) is not int or not 0 <= data[k] <= 1000
                   for k in data):
                raise ValueError()
            # Backward-compatible with v0.6.3 scanning while the operator
            # reloads the extension; no new admissions until v0.6.4.
            for k in extra:data.setdefault(k,0)
            total_chatgpt=data["chat_tabs"]+data["other_chatgpt_tabs"]
            if (total_chatgpt+data["non_chatgpt_tabs"]+data["unknown_url_tabs"] != data["tabs_total"]
                or data["discarded"]>total_chatgpt
                or data["attempted"]>total_chatgpt-data["discarded"]
                or data["identified"]>data["attempted"]
                or data["unsaved_identified"]>min(data["identified"],data["other_chatgpt_tabs"])
                or data["eligible"]>data["identified"]-data["unsaved_identified"]
                or data["recovered"]>data["eligible"]
                or data["ambiguous"]>data["identified"]
                or data["injection_failed"]>data["attempted"]
                or data["identified"]+data["injection_failed"]>data["attempted"]
                or data["stored_conflict"]>data["ambiguous"]
                or data["restored_from_local"]>data["recovered"]
                or data["recovered_dom_only"]>data["recovered"]
                or (not legacy_scan and
                    data["restored_from_local"]+data["recovered_dom_only"]!=data["recovered"])):
                raise ValueError()
        except (ValueError,TypeError,UnicodeDecodeError,KeyError):
            self._reply(400,{"error":"invalid_scan"});return
        with LOCK:
            DIAG.mkdir(parents=True,exist_ok=True)
            record={"at":datetime.now(timezone.utc).isoformat(timespec="seconds"),**data}
            tmp=DIAG/"recovery-scan.writing"
            tmp.write_text(json.dumps(record),encoding="utf-8")
            os.replace(tmp,DIAG/"recovery-scan.json")
            self._reply(200,{"ok":True})

    def _event(self):
        # Authenticated, bounded metadata. No prompt, transcript, cookies or token.
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 1024:
                raise ValueError()
            data = json.loads(self.rfile.read(size).decode("utf-8"))
            jid = data.get("id")
            stage = data.get("stage")
            reason = data.get("reason", "")
            if not isinstance(jid, str) or len(jid) != 36 or not all(
                c in "0123456789abcdef-" for c in jid
            ):
                raise ValueError()
            stages = {
                "tab_opening","tab_created","assigned","editor_found",
                "prompt_filled","send_clicked","message_saved","handoff_failed",
                "done","submitted","unknown","watchdog_timeout","receipt_accepted",
                "receipt_failed","tab_closed","tab_close_failed","tab_missing",
                "tab_close_skipped","tab_preserved","auth_context_changed","runtime_error",
                "report_pending","report_acked","report_watchdog","report_disputed",
                "tab_recovered","tab_restored","tab_identity_unverified",
                "tab_binding_disputed","send_ui_diagnostic"
            }
            if stage not in stages:
                raise ValueError()
            if not isinstance(reason, str) or len(reason) > 48 or any(
                c not in "abcdefghijklmnopqrstuvwxyz0123456789_" for c in reason
            ):
                raise ValueError()
            if stage == "send_ui_diagnostic" and reason not in {
                "editor_absent", "button_absent", "button_disabled",
                "button_not_scoped", "button_ambiguous",
            }:
                raise ValueError()
        except (ValueError, KeyError, TypeError, UnicodeDecodeError):
            self._reply(400, {"error": "invalid_event"})
            return
        with LOCK:
            matches = any((root / (jid + ".json")).exists()
                          for root in (QUEUE, ACTIVE, DONE))
            if not matches:
                self._reply(404, {"error": "unknown_job"})
                return
            DIAG.mkdir(parents=True, exist_ok=True)
            target = DIAG / (jid + ".json")
            try:
                previous = json.loads(target.read_text(encoding="utf-8")) if target.exists() else []
                if not isinstance(previous, list):
                    previous = []
            except (ValueError, OSError):
                previous = []
            item = {
                "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "stage": stage
            }
            if reason:
                item["reason"] = reason
            previous = (previous + [item])[-60:]
            tmp = DIAG / (jid + ".writing")
            tmp.write_text(json.dumps(previous, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, target)
            self._reply(200, {"ok": True, "recorded": True})

    def do_POST(self):
        if not self._authorized(): return
        if self.path == "/v1/recovery-scan":
            self._recovery_scan()
            return
        if self.path == "/v1/event":
            self._event()
            return
        if self.path == "/v1/heartbeat":
            self._heartbeat()
            return
        if self.path != "/v1/result":
            self._reply(404, {"error":"not_found"});return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 40000: raise ValueError()
            data = json.loads(self.rfile.read(size).decode("utf-8"))
            jid = data["id"]
            status = data["status"]
            if not isinstance(jid,str) or len(jid)!=36 or not all(x in "0123456789abcdef-" for x in jid):
                raise ValueError()
            if status not in ("done","submitted","unknown"): raise ValueError()
            url = data.get("url","")
            if not isinstance(url,str) or len(url)>250:
                raise ValueError()
            if url:
                parsed=urlsplit(url)
                if (parsed.scheme!="https" or parsed.netloc!="chatgpt.com"
                    or not re.fullmatch(r"/c/[0-9a-z-]+",parsed.path)
                    or parsed.fragment):
                    raise ValueError()
                # Never persist tracking parameters or other query contents.
                url="https://chatgpt.com"+parsed.path
            if status in ("done", "submitted") and not url:
                raise ValueError()
            ack_id = data.get("ack_id", "")
            if not isinstance(ack_id,str) or len(ack_id)>64:
                raise ValueError()
            response_text = data.get("response_text", "")
            if not isinstance(response_text, str) or len(response_text)>8000:
                raise ValueError()
            if status != "done" and response_text:
                raise ValueError()
        except (ValueError, KeyError, UnicodeDecodeError, TypeError):
            self._reply(400, {"error":"invalid_request"});return
        with LOCK:
            active=ACTIVE / (jid+".json")
            completed=DONE / (jid+".json")
            if completed.exists():
                try:
                    saved=json.loads(completed.read_text(encoding="utf-8"))
                except (OSError,ValueError,UnicodeError):
                    self._reply(409, {"error":"completed_state_invalid"});return
                if (type(saved) is not dict or saved.get("id")!=jid
                    or saved.get("status") not in ("done","submitted","unknown")):
                    self._reply(409, {"error":"completed_state_invalid"});return
                self._reply(200, {"ok":True,"status":saved["status"],"duplicate":True});return
            if not active.exists():
                self._reply(404, {"error":"not_active"});return
            try:
                current=json.loads(active.read_text(encoding="utf-8"))
            except (OSError,ValueError,UnicodeError):
                self._reply(409, {"error":"active_state_invalid"});return
            if (type(current) is not dict or current.get("id")!=jid
                or (current.get("role_metadata") is not None
                    and type(current.get("role_metadata")) is not dict)):
                self._reply(409, {"error":"active_state_invalid"});return
            campaign=(current.get("role_metadata") or {}).get("campaign_id")
            if campaign in ("chain2","chain3"):
                if campaign=="chain2":
                    from chain_campaign import handoff_requested_for_job
                else:
                    from chain3_continuous import handoff_requested_for_job
                if not handoff_requested_for_job(current):
                    self._reply(409,{"error":"handoff_request_required"});return
            if current.get("completion_mode")=="report":
                if status!="done" or not ack_id:
                    self._reply(409, {"error":"report_ack_required"});return
                try:
                    verified=evaluate(ACTIVE,REPORTS,ACKS,jid)
                except Pending as e:
                    self._reply(409, {"error":"report_not_acked","reason":e.code});return
                if verified["ack_id"]!=ack_id:
                    self._reply(409, {"error":"ack_id_mismatch"});return
            result={"id":jid,"status":status,"url":url}
            # Preserve stage identity for downstream feature/review gates.
            if type(current.get("role_metadata")) is dict:
                result["role_metadata"]=current["role_metadata"]
            if type(current.get("role")) is str:
                result["role"]=current["role"]
            if type(current.get("brief_sha256")) is str:
                result["brief_sha256"]=current["brief_sha256"]
            if current.get("completion_mode")=="report":
                result["report_ack_id"]=ack_id
                result["report_sha256"]=verified["artifact_sha256"]
                result["report_path"]=verified["artifact_relpath"]
            if status == "done" and response_text:
                REPORTS.mkdir(parents=True, exist_ok=True)
                report = REPORTS / ("chat-" + jid + ".md")
                if not report.exists():
                    with report.open("x",encoding="utf-8") as output:
                        output.write(response_text)
                elif report.read_text(encoding="utf-8") != response_text:
                    self._reply(409, {"error": "report_conflict"})
                    return
                result["response_file"] = "agent-reports/" + report.name
                result["response_chars"] = len(response_text)
            tmp=DONE/(jid+".writing")
            tmp.write_text(json.dumps(result,ensure_ascii=False),encoding="utf-8")
            os.replace(tmp,completed)
            active.unlink()
            self._reply(200, {"ok":True, "status":status})

class Server(ThreadingHTTPServer):
    allow_reuse_address=True
    daemon_threads=True

def serve():
    key=initialize()
    server=Server(("127.0.0.1",PORT),Handler)
    server.bridge_token=key
    print(json.dumps({"ok":True,"listen":"127.0.0.1","port":PORT,"web_extension_config_exists":True}),flush=True)
    try: server.serve_forever(poll_interval=.5)
    except KeyboardInterrupt: pass
    finally: server.server_close()

if __name__=="__main__":
    serve()

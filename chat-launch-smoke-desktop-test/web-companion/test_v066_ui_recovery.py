"""Hermetic v0.6.6 compatibility and privacy-limited UI telemetry QA."""
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import bridge

JOB_ID="01234567-89ab-4cde-8901-23456789abcd"

class UIRecoveryV066Tests(unittest.TestCase):
    def test_old_worker_cannot_claim_and_new_worker_must_be_live(self):
        with tempfile.TemporaryDirectory() as td:
            diag=Path(td)
            p=diag/"extension.json"
            with patch.object(bridge,"DIAG",diag):
                cases=[
                    ("0.6.5","polling",0,False),
                    ("0.6.6","paused",0,False),
                    ("0.6.6","no_window",0,False),
                    ("0.6.6","polling",200,False),
                    ("0.6.6","polling",0,True),
                ]
                for version,state,age,expected in cases:
                    with self.subTest(version=version,state=state,age=age):
                        at=(datetime.now(timezone.utc)-timedelta(seconds=age)).isoformat()
                        p.write_text(json.dumps({"at":at,"version":version,
                           "state":state,"windows":1}),encoding="utf-8")
                        self.assertEqual(bridge.completion_client_ready(),expected)

    def test_ui_diagnostic_event_is_enum_only_and_no_prompt_persisted(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);state=root/"bridge-state"
            queue=state/"queue";active=state/"active";done=state/"done"
            diag=state/"diagnostics"
            for p in (queue,active,done,diag):p.mkdir(parents=True)
            (queue/(JOB_ID+".json")).write_text(json.dumps({"id":JOB_ID}))
            patches=[
                patch.object(bridge,"STATE",state),
                patch.object(bridge,"QUEUE",queue),
                patch.object(bridge,"ACTIVE",active),
                patch.object(bridge,"DONE",done),
                patch.object(bridge,"DIAG",diag),
            ]
            for p in patches:p.start()
            try:
                with bridge.Server(("127.0.0.1",0),bridge.Handler) as server:
                    server.bridge_token="local-test-token-"+"x"*48
                    thread=threading.Thread(target=server.serve_forever,daemon=True)
                    thread.start()
                    try:
                        def event(reason):
                            data=json.dumps({"id":JOB_ID,"stage":"send_ui_diagnostic",
                                             "reason":reason}).encode("utf-8")
                            req=urllib.request.Request(
                                f"http://127.0.0.1:{server.server_port}/v1/event",
                                data=data,method="POST",headers={
                                    "X-Chat-Smoke-Key":server.bridge_token,
                                    "Origin":"chrome-extension://test",
                                    "Content-Type":"application/json"})
                            try:
                                with urllib.request.urlopen(req,timeout=3) as resp:
                                    return resp.status
                            except urllib.error.HTTPError as e:
                                return e.code
                        self.assertEqual(event("button_disabled"),200)
                        self.assertEqual(event("user_personal_details"),400)
                        entries=json.loads((diag/(JOB_ID+".json")).read_text())
                        self.assertEqual(len(entries),1)
                        self.assertEqual(entries[0]["reason"],"button_disabled")
                        self.assertNotIn("prompt",entries[0])
                        self.assertNotIn("account",entries[0])
                    finally:
                        server.shutdown()
                        thread.join(3)
            finally:
                for p in reversed(patches):p.stop()

if __name__=="__main__":unittest.main()

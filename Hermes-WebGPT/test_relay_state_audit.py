"""Atomic SQLite transition journal tests with no browser, provider, or secrets."""
import json
import http.client
import threading
from contextlib import closing
import os
import sqlite3
import tempfile
import unittest
import uuid

import relay


def req(message="SYNTHETIC_PRIVATE_PROMPT"):
    return {"model": relay.MODEL, "messages": [
        {"role": "user", "content": message}]}


class AuditTransitionTests(unittest.TestCase):
    def setUp(self):
        self.q = relay.Queue(":memory:")
        self.addCleanup(self.q.close)

    def test_full_state_history_is_atomic_and_ordered(self):
        ident = self.q.submit(req(), turn_id="turn-audit-fixture-0001")
        job = self.q.claim(str(uuid.uuid4()))
        self.assertEqual(ident, job["id"])
        self.q.event(ident, job["lease"], "intent", tab=11)
        self.q.event(ident, job["lease"], "saved", tab=11,
                     url="https://chatgpt.com/c/test")
        reply = json.dumps({"request_id": ident, "content": "fixture-ok",
                            "tool_calls": []})
        self.q.event(ident, job["lease"], "result", tab=11,
                     url="https://chatgpt.com/c/test", text=reply)
        rows = list(reversed(self.q.state_event_tail()["events"]))
        self.assertEqual(
            ["queued", "claimed", "intent", "saved", "done"],
            [r["new_state"] for r in rows])
        self.assertEqual(
            [None, "queued", "claimed", "intent", "saved"],
            [r["previous_state"] for r in rows])
        self.assertEqual([1, 2, 3, 4, 5], [r["seq"] for r in rows])
        self.assertTrue(all(r["recorded_utc"].endswith("Z") for r in rows))
        self.assertTrue(self.q.state_event_tail()["history_complete_for_current_requests"])
        self.assertFalse(self.q.state_event_tail()["browser_send_confirmed"])

    def test_rollback_removes_state_and_its_audit_entry_together(self):
        ident = self.q.submit(req(), turn_id="turn-audit-fixture-0002")
        initial = len(self.q.state_event_tail()["events"])
        self.q.db.execute("BEGIN IMMEDIATE")
        self.q.db.execute(
            "UPDATE requests SET state='blocked',reason='worker_failed' WHERE id=?",
            (ident,))
        self.assertEqual(initial+1,len(self.q.state_event_tail()["events"]))
        self.q.db.execute("ROLLBACK")
        self.assertEqual("queued", self.q.read(ident)["state"])
        self.assertEqual(initial,len(self.q.state_event_tail()["events"]))

    def test_unknown_external_effect_is_preserved(self):
        ident = self.q.submit(req(), turn_id="turn-audit-fixture-0003")
        job = self.q.claim(str(uuid.uuid4()))
        self.q.event(ident, job["lease"], "intent", tab=21)
        final = self.q.cancel(ident)
        self.assertEqual(final, "uncertain")
        history = list(reversed(self.q.state_event_tail()["events"]))
        self.assertEqual(["queued", "claimed", "intent", "uncertain"],
                         [r["new_state"] for r in history])
        self.assertEqual("client_disconnected", history[-1]["reason_code"])
        self.assertFalse(self.q.state_event_tail()["browser_send_confirmed"])

    def test_same_state_update_is_not_false_progress(self):
        ident = self.q.submit(req(), turn_id="turn-audit-fixture-0004")
        before=len(self.q.state_event_tail()["events"])
        self.q.db.execute("UPDATE requests SET state=state WHERE id=?", (ident,))
        self.assertEqual(before,len(self.q.state_event_tail()["events"]))

    def test_output_is_metadata_only_and_bounded(self):
        ident = self.q.submit(req(), turn_id="turn-audit-fixture-0005")
        report=self.q.state_event_tail()
        s=json.dumps(report)
        self.assertNotIn(ident,s)
        self.assertNotIn("SYNTHETIC_PRIVATE_PROMPT",s)
        self.assertNotIn("Bearer",s)
        self.assertNotIn("chatgpt.com",s)
        self.assertEqual(64,len(report["events"][0]["request_digest"]))
        for bad in (0,501,True,False,"10"):
            with self.subTest(bad=bad),self.assertRaisesRegex(
                relay.RelayError,"invalid_audit_limit"):
                self.q.state_event_tail(limit=bad)

    def test_missing_legacy_history_not_backfilled_as_verified(self):
        with tempfile.TemporaryDirectory() as folder:
            path=os.path.join(folder,"legacy.sqlite")
            c=sqlite3.connect(path,isolation_level=None)
            c.execute("""CREATE TABLE requests (
                id TEXT PRIMARY KEY, fingerprint TEXT UNIQUE, payload TEXT,
                state TEXT, worker TEXT, lease TEXT, tab INTEGER,
                saved_url TEXT, result TEXT, reason TEXT, created REAL)""")
            c.execute("INSERT INTO requests(id,state,created) VALUES(?,?,?)",
                      ("old-opaque-id","claimed",1.0))
            c.close()
            new=relay.Queue(path)
            try:
                pre=new.state_event_tail()
                self.assertEqual(1,pre["pre_audit_existing_request_count"])
                self.assertEqual(1,pre["history_missing_request_count"])
                self.assertFalse(pre["history_complete_for_current_requests"])
                self.assertEqual([],pre["events"])
                # Later transitions don't retroactively prove the old history.
                new.db.execute(
                    "UPDATE requests SET state='uncertain' WHERE id='old-opaque-id'")
                post=new.state_event_tail()
                self.assertEqual(1,post["pre_audit_existing_request_count"])
                self.assertFalse(post["history_complete_for_current_requests"])
                self.assertEqual(1,len(post["events"]))
                self.assertEqual("claimed",post["events"][0]["previous_state"])
            finally:new.close()

    def test_two_db_connections_share_committed_audit(self):
        with tempfile.TemporaryDirectory() as folder:
            path=os.path.join(folder, "shared.sqlite")
            a,b=relay.Queue(path),relay.Queue(path)
            try:
                ident=a.submit(req(),turn_id="turn-audit-fixture-0006")
                job=b.claim(str(uuid.uuid4()))
                self.assertEqual(ident,job["id"])
                self.assertEqual(["claimed","queued"],
                                 [x["new_state"] for x in a.state_event_tail()["events"]])
                self.assertEqual(a.state_event_tail()["events"],
                                 b.state_event_tail()["events"])
            finally:a.close();b.close()

    def test_authenticated_cursor_feed_and_safe_paging(self):
        ident=self.q.submit(req(),turn_id="turn-audit-fixture-0080")
        job=self.q.claim(str(uuid.uuid4()))
        server=relay.Server(("127.0.0.1",0),self.q,"synthetic-owner-key",timeout=2)
        thread=threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        def call(path,auth=True):
            with closing(http.client.HTTPConnection(*server.server_address,timeout=3)) as conn:
                headers=({"Authorization":"Bearer synthetic-owner-key"} if auth else {})
                conn.request("GET",path,headers=headers)
                response=conn.getresponse()
                return response.status,json.loads(response.read())
        try:
            self.assertEqual(401,call("/bridge/audit?after=0&limit=1",False)[0])
            first_status,first=call("/bridge/audit?after=0&limit=1")
            self.assertEqual(200,first_status)
            self.assertEqual(1,len(first["events"]))
            self.assertTrue(first["has_more"])
            self.assertEqual("queued",first["events"][0]["new_state"])
            self.assertEqual(1,first["next_seq"])
            second_status,second=call("/bridge/audit?after=1&limit=1")
            self.assertEqual(200,second_status)
            self.assertEqual("claimed",second["events"][0]["new_state"])
            self.assertFalse(second["has_more"])
            self.assertEqual(2,second["next_seq"])
            self.assertNotIn("SYNTHETIC_PRIVATE_PROMPT",json.dumps(first))
            self.assertNotIn(ident,json.dumps(first))
            self.assertNotIn(job["lease"],json.dumps(first))
            for bad in ("/bridge/audit?after=x&limit=1",
                        "/bridge/audit?after=0&limit=0",
                        "/bridge/audit?after=0&limit=501",
                        "/bridge/audit?after=0&limit=1&foo=secret"):
                with self.subTest(path=bad):
                    self.assertEqual(400,call(bad)[0])
        finally:
            server.shutdown()
            thread.join(timeout=3)
            server.server_close()

    def test_storage_failure_in_audit_get_reports_redacted_503(self):
        from unittest.mock import patch
        server=relay.Server(("127.0.0.1",0),self.q,"synthetic-owner-key",timeout=2)
        thread=threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        try:
            with patch.object(self.q,"state_events_since",side_effect=sqlite3.OperationalError(
                    "SENSITIVE_SQL_PATH_or_internal_database_error")):
                with closing(http.client.HTTPConnection(*server.server_address,timeout=3)) as conn:
                    conn.request("GET","/bridge/audit?after=0&limit=1",
                                 headers={"Authorization":"Bearer synthetic-owner-key"})
                    response=conn.getresponse()
                    self.assertEqual(503,response.status)
                    raw=response.read().decode("utf-8")
                    self.assertIn('"code":"audit_storage_unavailable"',raw)
                    self.assertNotIn("SENSITIVE_SQL_PATH",raw)
        finally:
            server.shutdown()
            thread.join(timeout=3)
            server.server_close()

    def test_empty_cursor_preserves_offset_and_rejects_bad_numbers(self):
        self.q.submit(req(),turn_id="turn-audit-fixture-0081")
        empty=self.q.state_events_since(after_seq=10,limit=1)
        self.assertEqual([],empty["events"])
        self.assertEqual(10,empty["next_seq"])
        self.assertFalse(empty["has_more"])
        for bad in (dict(after_seq=-1),dict(after_seq=True),
                    dict(after_seq=2**80),dict(limit=0),dict(limit=True),
                    dict(limit=501)):
            with self.subTest(bad=bad),self.assertRaisesRegex(
                    relay.RelayError,"invalid_audit_cursor"):
                self.q.state_events_since(**bad)

    def test_queue_full_rolls_back_without_phantom_event(self):
        for n in range(5):
            self.q.submit(req("payload-%d"%n),
                          turn_id="turn-audit-fixture-%04d"%(n+50))
        before=len(self.q.state_event_tail()["events"])
        with self.assertRaisesRegex(relay.RelayError,"queue_full"):
            self.q.submit(req("sixth"),turn_id="turn-audit-fixture-0055")
        self.assertEqual(before,len(self.q.state_event_tail()["events"]))
        self.assertEqual(before,self.q.db.execute(
            "SELECT COUNT(*) FROM request_state_events").fetchone()[0])


if __name__=="__main__":
    unittest.main()

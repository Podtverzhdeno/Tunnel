"""Read-only continuity metric QA: invalid handoff must never be counted as valid."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import chain2_lineage_monitor as m

class LineageMetricTests(unittest.TestCase):
    def run_case(self,invalid):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            hand=root/"handoff.json"
            hand.write_text("{}",encoding="utf-8")
            def locate(id):
                return ("active",{"id":id}) if id=="test-ticket:developer" else (None,None)
            def read_handoff(*args):
                if invalid:raise m.c.ChainError("unverified_handoff_artifact")
                return "a"*64
            with patch.object(m.c,"load",return_value=({"seed_count":1},[{"id":"test-ticket"}],"x"*64)),\
                 patch.object(m.c,"ROLES",("developer","reviewer")),\
                 patch.object(m.c,"jid",side_effect=lambda ticket,role:ticket+":"+role),\
                 patch.object(m.c,"locate",side_effect=locate),\
                 patch.object(m.c,"successor",return_value=("test-ticket","reviewer")),\
                 patch.object(m.c,"handoff_path",return_value=hand),\
                 patch.object(m.c,"request_valid",return_value=False),\
                 patch.object(m.c,"marker",return_value=None),\
                 patch.object(m.c,"read_handoff",side_effect=read_handoff),\
                 patch.object(m.c,"armed",return_value=True),\
                 patch.object(m.c,"HOLD",root/"never-held.json"),\
                 patch.object(m.c,"STATE",root/"state"):
                result=m.inspect()
            self.assertTrue(result["no_mutations_performed"])
            self.assertEqual(result["summary"]["active"],1)
            self.assertEqual(result["summary"]["done"],0)
            self.assertEqual(result["summary"]["not_started"],1)
            return result
    def test_older_message_saved_survives_telemetry_window(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            diag=root/"diagnostics"
            diag.mkdir()
            job="01234567-89ab-4cde-8901-234567890abc"
            events=[{"stage":"message_saved","at":"2026-11-03T10:00:00Z"}]
            events.extend({"stage":"unknown","reason":"watchdog_timeout"} for _ in range(28))
            (diag/(job+".json")).write_text(json.dumps(events),encoding="utf-8")
            with patch.object(m.c,"STATE",root):
                result=m.events_for(job)
            self.assertEqual(len(result),29)
            self.assertTrue(any(x["stage"]=="message_saved" for x in result))

    def test_done_parent_reports_old_child_account_dispute(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            diag=root/"diagnostics"
            diag.mkdir()
            child="test-ticket:reviewer"
            events=[{"stage":"auth_context_changed"},{"stage":"message_saved"}]
            events.extend({"stage":"unknown","reason":"watchdog_timeout"} for _ in range(30))
            (diag/(child+".json")).write_text(json.dumps(events),encoding="utf-8")
            def locate(ident):
                if ident=="test-ticket:developer":return "done",{"id":ident}
                if ident==child:return "active",{"id":ident}
                return None,None
            def successor(ticket,role,*args):
                return (ticket,"reviewer") if role=="developer" else (None,None)
            with patch.object(m.c,"load",return_value=({"seed_count":1},[{"id":"test-ticket"}],"x"*64)),\
                 patch.object(m.c,"ROLES",("developer","reviewer")),\
                 patch.object(m.c,"jid",side_effect=lambda ticket,role:ticket+":"+role),\
                 patch.object(m.c,"locate",side_effect=locate),\
                 patch.object(m.c,"successor",side_effect=successor),\
                 patch.object(m.c,"handoff_path",return_value=root/"missing.json"),\
                 patch.object(m.c,"request_valid",return_value=True),\
                 patch.object(m.c,"marker",return_value={"verified":"marker"}),\
                 patch.object(m.c,"accepted",return_value=True),\
                 patch.object(m.c,"armed",return_value=True),\
                 patch.object(m.c,"HOLD",root/"never-held.json"),\
                 patch.object(m.c,"STATE",root):
                report=m.inspect()
            self.assertEqual(report["summary"]["successors_message_saved"],1)
            self.assertIn("child_account_context_disputed",
                          [x["reason"] for x in report["problems"]])
            self.assertFalse(any(x["reason"]=="child_claimed_but_send_unconfirmed"
                                 for x in report["problems"]))
            self.assertTrue(report["no_mutations_performed"])

    def test_invalid_handoff_is_not_ready(self):
        result=self.run_case(invalid=True)
        s=result["summary"]
        self.assertEqual(s["active_valid_handoff_waiting_request"],0)
        self.assertEqual(s["active_invalid_handoff"],1)
        self.assertEqual(result["problems"][0]["reason"],"active_handoff_invalid")
    def test_valid_handoff_is_only_counted_when_verified(self):
        result=self.run_case(invalid=False)
        s=result["summary"]
        self.assertEqual(s["active_valid_handoff_waiting_request"],1)
        self.assertEqual(s["active_invalid_handoff"],0)
        self.assertEqual(result["problems"][0]["reason"],
                         "active_handoff_valid_but_successor_not_requested")

if __name__=="__main__":
    unittest.main()

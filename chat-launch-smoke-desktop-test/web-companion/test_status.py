import json
import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timezone
from unittest.mock import patch
import status

class ReadOnlyStatusTests(unittest.TestCase):
    def test_latest_contains_safe_events_and_health(self):
        with tempfile.TemporaryDirectory() as name, patch.object(status,"ROOT",Path(name)):
            root=Path(name)
            for folder in ("queue","active","done","diagnostics"):
                (root/folder).mkdir()
            jid="01234567-89ab-4cde-8901-23456789abcd"
            (root/"done"/(jid+".json")).write_text(
                json.dumps({"id":jid,"status":"submitted","url":"https://chatgpt.com/c/abcde"}),
                encoding="utf-8")
            (root/"diagnostics"/(jid+".json")).write_text(json.dumps([
                {"at":"2026-11-02T19:10:00+00:00","stage":"tab_opening"},
                {"at":"2026-11-02T19:10:01+00:00","stage":"send_clicked"},
                {"at":"2026-11-02T19:10:06+00:00","stage":"tab_closed"}
            ]),encoding="utf-8")
            (root/"diagnostics"/"extension.json").write_text(json.dumps({
                "at":datetime.now(timezone.utc).isoformat(),"version":"0.2.3",
                "state":"polling","windows":1}),encoding="utf-8")
            result=status.find_latest()
            latest=result["latest"]
            self.assertEqual(result["extension"]["version"],"0.2.3")
            self.assertEqual(latest["state"],"submitted")
            self.assertEqual(latest["last_stage"],"tab_closed")
            self.assertEqual(latest["event_count"],3)
            output=json.dumps(result)
            self.assertNotIn("token",output.lower())
            self.assertNotIn("prompt",output.lower())
    def test_no_diagnostics_yet_is_valid(self):
        with tempfile.TemporaryDirectory() as name, patch.object(status,"ROOT",Path(name)):
            output=status.find_latest()
            self.assertIsNone(output["latest"])
            self.assertIsNone(output["extension"])
            self.assertEqual(output["jobs"],[])
    def test_recovery_ownership_counts_privacy(self):
        with tempfile.TemporaryDirectory() as name, patch.object(status,"ROOT",Path(name)):
            root=Path(name)
            diag=root/"diagnostics"
            diag.mkdir()
            (diag/"recovery-scan.json").write_text(json.dumps({
                "at":"2026-11-03T05:20:00+00:00",
                "tabs_total":17,"chat_tabs":1,"non_chatgpt_tabs":16,
                "identified":1,"eligible":1,"recovered":1,
                "ambiguous":0,"stored_owned":1,"stored_invalid":0,
                "stored_conflict":0,"restored_from_local":1,
                "recovered_dom_only":0,
                "prompt":"SECRET PROMPT","cookies":"SECRET COOKIE",
                "tab_ids":[123],"urls":["https://chatgpt.com/c/private"]
            }),encoding="utf-8")
            result=status.find_latest()
            self.assertEqual(result["recovery_scan"]["restored_from_local"],1)
            self.assertEqual(result["recovery_scan"]["stored_owned"],1)
            self.assertEqual(result["recovery_scan"]["chat_tabs"],1)
            display=json.dumps(result,ensure_ascii=False)
            for secret in ("SECRET PROMPT","SECRET COOKIE","tab_ids",
                           "https://chatgpt.com/c/private"):
                self.assertNotIn(secret,display)

    def test_active_report_exists_at_project_level(self):
        """A completed MCP report lives beside web-companion, not inside it."""
        with tempfile.TemporaryDirectory() as name:
            project = Path(name)
            root = project/"web-companion"/"bridge-state"
            (root/"active").mkdir(parents=True)
            (root/"completion-acks").mkdir()
            (project/"agent-reports").mkdir()
            jid = "01234567-89ab-4cde-8901-23456789abcd"
            (root/"active"/(jid+".json")).write_text(
                json.dumps({"id":jid,"completion_mode":"report"}),encoding="utf-8")
            (root/"completion-acks"/(jid+".json")).write_text(
                json.dumps({"status":"REPORT_ACKED"}),encoding="utf-8")
            (project/"agent-reports"/("completion-"+jid+".md")).write_text(
                "Job ID: "+jid+"\n## Outcome\nOK\n## Evidence\nverified\n",encoding="utf-8")
            with patch.object(status,"ROOT",root):
                result=status.find_latest()
            self.assertEqual(result["latest"]["report_ack_recorded"],True)
            self.assertEqual(result["latest"]["report_exists"],True)

if __name__=="__main__":unittest.main(verbosity=2)

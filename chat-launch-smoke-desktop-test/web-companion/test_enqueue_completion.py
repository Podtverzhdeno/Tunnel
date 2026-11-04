"""Offline admission tests: never queue a real ChatGPT task."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from datetime import datetime, timezone
import enqueue_completion as ec

class AdmissionTests(unittest.TestCase):
    def test_fail_closed_until_browser_reloads(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            project=root.parent/"nonexistent-project"
            # A temporary project tree, not live queue or browser.
            project=root/"project"
            (project/"agent-briefs").mkdir(parents=True)
            (project/"agent-briefs"/"experiment.md").write_text("Test description")
            companion=project/"web-companion"
            diag=companion/"bridge-state"/"diagnostics"
            diag.mkdir(parents=True)
            hb=diag/"extension.json"
            with patch.object(ec,"ROOT",companion),patch.object(ec,"PROJECT",project),patch.object(ec,"QUEUE",companion/"bridge-state"/"queue"),patch.object(ec,"ACTIVE",companion/"bridge-state"/"active"),patch.object(ec,"DONE",companion/"bridge-state"/"done"):
                self.assertRaisesRegex(RuntimeError,"heartbeat_missing",
                    ec.enqueue,"experiment","agent-briefs/experiment.md")
                hb.write_text(json.dumps({"version":"0.4.2","state":"polling",
                    "at":datetime.now(timezone.utc).isoformat()}))
                self.assertRaisesRegex(RuntimeError,"reload_extension",
                    ec.enqueue,"experiment","agent-briefs/experiment.md")
                self.assertEqual(len(list(ec.QUEUE.glob("*.json"))),0)
                hb.write_text(json.dumps({"version":"0.6.6","state":"polling",
                    "at":datetime.now(timezone.utc).isoformat()}))
                output=ec.enqueue("experiment","agent-briefs/experiment.md")
                self.assertTrue(output["queued"])
                saved=json.loads((ec.QUEUE/(output["job_id"]+".json")).read_text())
                self.assertEqual(saved["completion_mode"],"report")
                self.assertIn(output["job_id"],saved["prompt"])
                self.assertIn("Local Workspace run_script",saved["prompt"])
                self.assertIn("--max-lines, 200",saved["prompt"])
                self.assertIn("job_status (completed, returncode=0)",saved["prompt"])
                self.assertIn("job_output",saved["prompt"])
                self.assertEqual(saved["descendant_budget"],0)
                self.assertEqual(saved["report_name"],"completion-"+saved["id"]+".md")
                self.assertRaisesRegex(ValueError,"invalid_role",
                    ec.enqueue,"../wrong","agent-briefs/experiment.md")
                self.assertRaisesRegex(ValueError,"brief_must_match",
                    ec.enqueue,"experiment","agent-briefs/other.md")

    def test_twenty_five_pending_job_limit_without_browser_actions(self):
        with tempfile.TemporaryDirectory() as tmp:
            project=Path(tmp)/"project"
            companion=project/"web-companion"
            briefs=project/"agent-briefs"
            briefs.mkdir(parents=True)
            diag=companion/"bridge-state"/"diagnostics"
            diag.mkdir(parents=True)
            (diag/"extension.json").write_text(json.dumps({
                "version":"0.6.6","state":"polling",
                "at":datetime.now(timezone.utc).isoformat()
            }),encoding="utf-8")
            with patch.object(ec,"ROOT",companion),patch.object(ec,"PROJECT",project),patch.object(ec,"QUEUE",companion/"bridge-state"/"queue"),patch.object(ec,"ACTIVE",companion/"bridge-state"/"active"),patch.object(ec,"DONE",companion/"bridge-state"/"done"):
                for n in range(1,26):
                    role=f"load-{n}"
                    (briefs/(role+".md")).write_text("Independent test",encoding="utf-8")
                    self.assertTrue(ec.enqueue(role,"agent-briefs/"+role+".md")["queued"])
                (briefs/"load-26.md").write_text("Overflow",encoding="utf-8")
                self.assertRaisesRegex(ValueError,"queue_full",
                    ec.enqueue,"load-26","agent-briefs/load-26.md")
                self.assertEqual(len(list(ec.QUEUE.glob("*.json"))),25)

if __name__=="__main__":
    unittest.main(verbosity=2)

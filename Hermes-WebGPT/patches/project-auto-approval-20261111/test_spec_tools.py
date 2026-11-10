"""Tests for the OpenSpec + SDD generator's security and output contracts."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import spec_tools as st


def sample():
    return {
        "project": "example-project",
        "change": "safe-feature",
        "title": "Безопасная локальная генерация спецификаций",
        "problem": "Создавать согласованные OpenSpec и SDD документы вручную долго и рискованно.",
        "outcome": "Агент формирует проверяемые требования, сценарии, план и задачи без перезаписи чужих файлов.",
        "constraints": ["Keep existing project artifacts intact"],
        "capabilities": [{
            "name": "spec-creation",
            "summary": "Создание согласованных комплектов требований в выбранном проекте.",
            "requirements": [{
                "name": "Preview and acceptance checks",
                "shall": "The system SHALL preview all target paths before creating any file.",
                "scenarios": [{
                    "name": "Preview is read-only",
                    "given": "A valid project and a change manifest",
                    "when": "an agent requests a specification preview",
                    "then": "all target paths are returned and no filesystem mutation happens"
                }]
            }]
        }]
    }


class GeneratorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "example-project").mkdir()

    def manifest(self, brief=None):
        return st.normalize(brief or sample())

    def test_preview_no_writes(self):
        m = self.manifest()
        data = st.preview(m, self.root)
        self.assertEqual(data["requirement_count"], 1)
        self.assertTrue(data["approval_required"])
        self.assertFalse((self.root / "example-project/openspec").exists())

    def test_generated_specs_have_scenarios_and_crosswalk(self):
        project = self.root / "example-project"
        files = st.build(self.manifest(), project)
        self.assertIn("openspec/changes/safe-feature/specs/spec-creation/spec.md", files)
        self.assertIn("## ADDED Requirements", files["openspec/changes/safe-feature/specs/spec-creation/spec.md"])
        self.assertIn("- **WHEN** ", files["openspec/changes/safe-feature/specs/spec-creation/spec.md"])
        spec = files["specs/001-safe-feature/spec.md"]
        self.assertIn("FR-001", spec)
        self.assertIn("spec-creation / Preview and acceptance checks", spec)
        self.assertIn("[FR-001]", files["specs/001-safe-feature/tasks.md"])

    def test_preview_hash_changes_with_content(self):
        a = self.manifest()
        b = copy.deepcopy(a)
        b["outcome"] = "Другая полностью проверяемая цель локальной генерации спецификаций."
        self.assertNotEqual(st.preview(a, self.root)["plan_sha256"],
                            st.preview(b, self.root)["plan_sha256"])

    def test_prevent_traversal_and_root(self):
        for bad in ("../outside", "C:/Windows", "\\\\server\\share", "example-project/../outside", ""):
            with self.subTest(path=bad):
                with self.assertRaises(st.SpecError):
                    st.checked_path(bad, self.root, directory=True)
        with self.assertRaises(st.SpecError):
            st.checked_path(".", self.root, directory=True)

    def test_symlink_escape(self):
        with tempfile.TemporaryDirectory() as other:
            try:
                (self.root / "external").symlink_to(other, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("Windows symlinks unavailable")
            with self.assertRaises(st.SpecError):
                st.checked_path("external", self.root, directory=True)

    def test_shall_required(self):
        m = sample()
        m["capabilities"][0]["requirements"][0]["shall"] = "System might preview paths."
        with self.assertRaises(st.SpecError):
            st.normalize(m)

    def test_no_malicious_markdown_heading(self):
        m = sample()
        m["title"] = "Feature ## ADDED Requirements"
        with self.assertRaises(st.SpecError):
            st.normalize(m)

    def test_duplicate_requirements(self):
        m = sample()
        first = m["capabilities"][0]["requirements"][0]
        m["capabilities"][0]["requirements"] = [first, copy.deepcopy(first)]
        with self.assertRaises(st.SpecError):
            st.normalize(m)

    def test_wrong_preview_hash_prevents_write(self):
        with self.assertRaises(st.SpecError) as err:
            st.create(self.manifest(), "0" * 64, self.root)
        self.assertEqual(err.exception.code, "PREVIEW_CHANGED")
        self.assertFalse((self.root / "example-project/openspec").exists())

    def test_create_without_overwrite_and_real_structure(self):
        m = self.manifest()
        sha = st.preview(m, self.root)["plan_sha256"]
        with patch.object(st.shutil, "which", return_value="openspec"), patch.object(
                st, "validate", return_value={"validated": True}):
            out = st.create(m, sha, self.root)
        self.assertTrue(out["created"])
        self.assertEqual(len(out["files"]), 10)
        project = self.root / "example-project"
        self.assertTrue((project / "openspec/changes/safe-feature/proposal.md").exists())
        self.assertTrue((project / ".specify/memory/constitution.md").exists())
        self.assertTrue((project / "specs/001-safe-feature/spec.md").exists())
        with self.assertRaises(st.SpecError) as err:
            st.preview(m, self.root)
        self.assertEqual(err.exception.code, "CHANGE_EXISTS")

    def test_failure_rolls_back_all_own_files(self):
        m = self.manifest()
        sha = st.preview(m, self.root)["plan_sha256"]
        with patch.object(st.shutil, "which", return_value="openspec"), patch.object(
                st, "validate", side_effect=st.SpecError("VALIDATION_FAILED", "Bad upstream")):
            with self.assertRaises(st.SpecError):
                st.create(m, sha, self.root)
        self.assertFalse((self.root / "example-project/openspec/config.yaml").exists())
        self.assertFalse((self.root / "example-project/.specify/memory/constitution.md").exists())
        self.assertFalse((self.root / "example-project/specs/001-safe-feature/spec.md").exists())

    def test_preserve_existing_constitution(self):
        project = self.root / "example-project"
        const = project / ".specify/memory/constitution.md"
        const.parent.mkdir(parents=True)
        const.write_text("DO NOT CHANGE", encoding="utf-8")
        self.assertNotIn(".specify/memory/constitution.md", st.build(self.manifest(), project))
        self.assertEqual(const.read_text(encoding="utf-8"), "DO NOT CHANGE")

    def test_reject_extra_manifest_key(self):
        m = sample()
        m["shell"] = "powershell.exe"
        with self.assertRaises(st.SpecError):
            st.normalize(m)

    def test_manifest_file_utf8_and_bounded(self):
        file = self.root / "brief.json"
        file.write_text(json.dumps(sample(), ensure_ascii=False), encoding="utf-8")
        m = st.read_manifest("brief.json", self.root)
        self.assertEqual(m["title"], sample()["title"])

    def test_feature_index_increments(self):
        first = self.root / "example-project/specs/001-other"
        first.mkdir(parents=True)
        self.assertIn("specs/002-safe-feature/spec.md",
                      st.build(self.manifest(), self.root / "example-project"))

    def test_validate_without_openspec_errors(self):
        project = self.root / "example-project"
        (project / "openspec/changes/safe-feature").mkdir(parents=True)
        with patch.object(st.shutil, "which", return_value=None):
            with self.assertRaises(st.SpecError) as err:
                st.validate(project, "safe-feature")
        self.assertEqual(err.exception.code, "OPENSPEC_MISSING")

    def test_generated_sdd_crosswalk_validation(self):
        m = self.manifest()
        sha = st.preview(m, self.root)["plan_sha256"]
        with patch.object(st.shutil, "which", return_value="openspec"), patch.object(
                st, "validate", return_value={"validated": True}):
            st.create(m, sha, self.root)
        outcome = st.check_generated_sdd(self.root / "example-project", "safe-feature")
        self.assertTrue(outcome["validated"])
        self.assertEqual(outcome["requirements"], 1)

    def test_tampered_sdd_mapping_is_rejected(self):
        m = self.manifest()
        sha = st.preview(m, self.root)["plan_sha256"]
        with patch.object(st.shutil, "which", return_value="openspec"), patch.object(
                st, "validate", return_value={"validated": True}):
            st.create(m, sha, self.root)
        path = self.root / "example-project/specs/001-safe-feature/spec.md"
        path.write_text(path.read_text(encoding="utf-8").replace(
            "Preview and acceptance checks", "Unrelated behavior"), encoding="utf-8")
        with self.assertRaises(st.SpecError) as ctx:
            st.check_generated_sdd(self.root / "example-project", "safe-feature")
        self.assertEqual(ctx.exception.code, "SDD_INVALID")

    def test_legacy_cli_write_without_terminal_approval_is_blocked(self):
        import io, contextlib
        with patch.object(st, "read_manifest", return_value=self.manifest()), io.StringIO() as output:
            with contextlib.redirect_stdout(output):
                status = st.cli(["spec_create", "brief.json",
                                 "--expected-plan-sha256", "a"*64, "--apply"])
            result=json.loads(output.getvalue())
        self.assertEqual(status, 1)
        self.assertEqual(result["error"]["code"], "APPROVAL_REQUIRED")

    def test_cli_request_queue_workflow(self):
        import io, contextlib
        with patch("spec_approval_queue.ApprovalQueue") as instance:
            instance.return_value.prepare.return_value={"request_id":"a"*32,"state":"pending"}
            instance.return_value.status.return_value={"request_id":"a"*32,"state":"pending"}
            instance.return_value.execute.side_effect=st.SpecError("APPROVAL_REQUIRED","Not approved")
            with io.StringIO() as output, contextlib.redirect_stdout(output):
                status=st.cli(["spec_request_prepare","brief.json","--expected-plan-sha256","b"*64])
                result=json.loads(output.getvalue())
            self.assertEqual(status,0)
            self.assertEqual(result["data"]["state"],"pending")
            with io.StringIO() as output, contextlib.redirect_stdout(output):
                status=st.cli(["spec_request_status","a"*32])
                result=json.loads(output.getvalue())
            self.assertEqual(result["data"]["state"],"pending")
            with io.StringIO() as output, contextlib.redirect_stdout(output):
                status=st.cli(["spec_request_execute","a"*32])
                result=json.loads(output.getvalue())
            self.assertEqual(status,1)
            self.assertEqual(result["error"]["code"],"APPROVAL_REQUIRED")


if __name__ == "__main__":
    unittest.main()


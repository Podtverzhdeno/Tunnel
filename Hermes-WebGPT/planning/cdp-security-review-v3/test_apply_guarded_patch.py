"""Pure local source-drift/anchor tests. No Windows project mutation."""
from pathlib import Path
import tempfile
import unittest

import apply_guarded_patch as ap

class PatchTests(unittest.TestCase):
    def test_fixture_worker_anchors_are_unique(self):
        synthetic = (
            "from playwright.async_api import async_playwright\n"
            "async def run():\n"
            "    http=ScopedWorkerHttp(relay,key)\n"
            "    return await execute_one(http,args.fixture,args.case,journal)\n"
        )
        result = ap.transform("fixture_worker.py", synthetic.encode())
        self.assertIn(b"require_fixture_relay_attestation", result)
        self.assertIn(b"HERMES_CDP_FIXTURE_PROOF_KEY", result)

    def test_worker_requires_distinct_attestation_secret(self):
        source = (
            "from playwright.async_api import async_playwright\n"
            "async def run():\n"
            "    http=ScopedWorkerHttp(relay,key)\n"
            "    return await execute_one(http,args.fixture,args.case,journal)\n"
        )
        result = ap.transform("fixture_worker.py",source.encode("utf-8"))
        # An accidentally copied worker bearer must never double as the
        # fixture proof key. Both gates must precede get_one_job/claim.
        self.assertIn(b"if proof_key == key:",result)
        self.assertIn(b"fixture_proof_key_must_be_independent",result)
        self.assertLess(result.find(b"require_fixture_relay_attestation"),
                        result.rfind(b"return await execute_one"))

    def test_no_duplicate_anchor_accepted(self):
        snippet = b"from playwright.async_api import async_playwright\n" * 2
        with self.assertRaises(RuntimeError):
            ap.transform("fixture_worker.py", snippet)

    def test_linked_target_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp)/"cdp-live-preflight-20261111"/"standalone-worker-v2"
            base.mkdir(parents=True)
            link=Path(tmp)/"linked"
            try:
                link.symlink_to(base,target_is_directory=True)
            except (OSError,NotImplementedError):
                self.skipTest("symlink unsupported")
            with self.assertRaises(RuntimeError):
                ap.preflight(link,Path(__file__).parent)

    def test_wrong_source_sha_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp)/"cdp-live-preflight-20261111"/"standalone-worker-v2"
            base.mkdir(parents=True)
            for name in ap.CURRENT_SHA:
                (base/name).write_text("fake source",encoding="utf8")
            with self.assertRaises(RuntimeError) as ctx:
                ap.preflight(base, Path(__file__).parent)
            self.assertIn("source_sha_drift",str(ctx.exception))

    def test_full_staged_apply_on_synthetic_pinned_sources(self):
        from contextlib import redirect_stdout
        from io import StringIO
        from unittest.mock import patch
        worker = (
            "from playwright.async_api import async_playwright\n"
            "async def run():\n"
            "    http=ScopedWorkerHttp(relay,key)\n"
            "    return await execute_one(http,args.fixture,args.case,journal)\n"
        )
        tester = (
            "import relay\nfrom cdp_fixture_server import create_server\n"
            "def run():\n"
            "    if True:\n"
            "        server.daemon_threads=True\n"
            "        server_thread=run_server(server)\n"
            "        if True:\n"
            "            if True:\n"
            "                env[\"HERMES_CDP_FIXTURE_WORKER_KEY\"]=worker_auth\n"
        )
        # The actual Windows files are SHA pinned in production; this test
        # substitutes only the SHA mapping for its throwaway fixture inputs.
        with tempfile.TemporaryDirectory() as tmp:
            target=Path(tmp)/"cdp-live-preflight-20261111"/"standalone-worker-v2"
            target.mkdir(parents=True)
            (target/"fixture_worker.py").write_text(worker,encoding="utf-8")
            (target/"test_worker_subprocess_e2e.py").write_text(tester,encoding="utf-8")
            expected={name:ap.sha((target/name).read_bytes()) for name in ap.CURRENT_SHA}
            with patch.object(ap,"CURRENT_SHA",expected):
                with redirect_stdout(StringIO()):
                    self.assertEqual(ap.main(["--target",str(target)]),0)
                self.assertFalse((target/"fixture_relay_attestation.py").exists())
                with redirect_stdout(StringIO()):
                    self.assertEqual(ap.main(["--target",str(target),"--apply"]),0)
            self.assertTrue((target/"fixture_relay_attestation.py").is_file())
            self.assertTrue((target/"test_fixture_relay_attestation.py").is_file())
            final=(target/"fixture_worker.py").read_text(encoding="utf-8")
            self.assertIn("require_fixture_relay_attestation(",final)
            self.assertEqual(final.count("require_fixture_relay_attestation("),1)
            self.assertFalse((target/".cdp_fixture_proof_patch.lock").exists())
            self.assertEqual(len(list(target.glob("*.tmp"))),0)
            backups=list(target.glob("*.pre-fixture-proof.*.bak"))
            self.assertEqual(len(backups),2)
            for backup in backups:
                self.assertIn(backup.read_text(encoding="utf-8"), (worker,tester))

    def test_modified_bundle_payload_fails_before_any_target_write(self):
        import shutil
        from unittest.mock import patch
        # Valid mock staging files with all required unique anchors. Copy the
        # real bundle payload to a new directory, then tamper one payload.
        worker = (
            "from playwright.async_api import async_playwright\n"
            "async def run():\n"
            "    http=ScopedWorkerHttp(relay,key)\n"
            "    return await execute_one(http,args.fixture,args.case,journal)\n"
        )
        tester = (
            "import relay\nfrom cdp_fixture_server import create_server\n"
            "def run():\n"
            "    if True:\n"
            "        server.daemon_threads=True\n"
            "        server_thread=run_server(server)\n"
            "        if True:\n"
            "            if True:\n"
            "                env[\"HERMES_CDP_FIXTURE_WORKER_KEY\"]=worker_auth\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp)
            target = parent / "cdp-live-preflight-20261111" / "standalone-worker-v2"
            target.mkdir(parents=True)
            (target / "fixture_worker.py").write_text(worker, encoding="utf-8")
            (target / "test_worker_subprocess_e2e.py").write_text(tester, encoding="utf-8")
            sha_map = {n:ap.sha((target / n).read_bytes()) for n in ap.CURRENT_SHA}
            bundle = parent / "bundle"
            bundle.mkdir()
            for name in ap.BUNDLE_SHA:
                shutil.copyfile(Path(__file__).parent / name, bundle / name)
            (bundle / "fixture_relay_attestation.py").write_bytes(
                (bundle / "fixture_relay_attestation.py").read_bytes() + b"# tampered\n"
            )
            with patch.object(ap, "CURRENT_SHA", sha_map):
                with self.assertRaises(RuntimeError) as caught:
                    ap.preflight(target, bundle)
            self.assertIn("bundle_source_sha_drift", str(caught.exception))
            self.assertEqual((target / "fixture_worker.py").read_text("utf-8"),worker)
            self.assertEqual(list(target.glob("*.bak")),[])

    def test_post_commit_verifies_all_final_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            target=Path(tmp)
            changed={"worker.py":(b"before",b"after")}
            added={"proof.py":b"fixture proof bytes"}
            (target/"worker.py").write_bytes(b"after")
            (target/"proof.py").write_bytes(b"fixture proof bytes")
            ap.verify_post_commit(target,changed,added)
            (target/"proof.py").write_bytes(b"concurrent attacker writes")
            with self.assertRaises(RuntimeError) as caught:
                ap.verify_post_commit(target,changed,added)
            self.assertIn("post_commit_digest_mismatch",str(caught.exception))

    def test_post_commit_rejects_missing_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            target=Path(tmp)
            changed={"worker.py":(b"before",b"after")}
            with self.assertRaises(RuntimeError) as caught:
                ap.verify_post_commit(target,changed,{})
            self.assertIn("post_commit_file_not_regular",str(caught.exception))

    def test_failed_drift_keeps_all_original_files_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            target=Path(tmp)/"cdp-live-preflight-20261111"/"standalone-worker-v2"
            target.mkdir(parents=True)
            originals={"fixture_worker.py":b"unsafe source",
                       "test_worker_subprocess_e2e.py":b"unsafe test"}
            for name, data in originals.items():
                (target/name).write_bytes(data)
            with self.assertRaises(RuntimeError):
                ap.preflight(target, Path(__file__).parent)
            for name, data in originals.items():
                self.assertEqual((target/name).read_bytes(),data)
            self.assertFalse((target/"fixture_relay_attestation.py").exists())

    def test_invalid_staging_target_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp)/"unsafe"
            base.mkdir()
            with self.assertRaises(RuntimeError):
                ap.preflight(base,Path(__file__).parent)

    def test_plan_has_no_mutation(self):
        import subprocess, sys
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp)/"cdp-live-preflight-20261111"/"standalone-worker-v2"
            base.mkdir(parents=True)
            for name in ap.CURRENT_SHA:
                (base/name).write_text("fake",encoding="utf8")
            result=subprocess.run([sys.executable,str(Path(ap.__file__)),
                                   "--target",str(base)],capture_output=True,text=True)
            self.assertEqual(result.returncode,2)
            self.assertFalse((base/"fixture_relay_attestation.py").exists())

if __name__=="__main__":
    unittest.main(verbosity=2)

"""Fault-injection tests for the v3 staging install's compare-before-restore.

Only synthetic source files in a temporary directory are changed. This does
not mount, read or mutate the user's Windows Hermes workspace.
"""
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import json
import os
import tempfile
import unittest
from unittest.mock import patch

import apply_guarded_patch as ap


WORKER = (
    "from playwright.async_api import async_playwright\n"
    "async def run():\n"
    "    http=ScopedWorkerHttp(relay,key)\n"
    "    return await execute_one(http,args.fixture,args.case,journal)\n"
)
TESTER = (
    "import relay\nfrom cdp_fixture_server import create_server\n"
    "def run():\n"
    "    if True:\n"
    "        server.daemon_threads=True\n"
    "        server_thread=run_server(server)\n"
    "        if True:\n"
    "            if True:\n"
    "                env[\"HERMES_CDP_FIXTURE_WORKER_KEY\"]=worker_auth\n"
)


class RollbackTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="hermes-rollback-v3-test-")
        self.addCleanup(self.temp.cleanup)
        self.target = Path(self.temp.name)/"cdp-live-preflight-20261111"/"standalone-worker-v2"
        self.target.mkdir(parents=True)
        self.source={"fixture_worker.py":WORKER.encode(),
                     "test_worker_subprocess_e2e.py":TESTER.encode()}
        for name,raw in self.source.items():
            (self.target/name).write_bytes(raw)
        self.expected = {name:ap.sha(raw) for name,raw in self.source.items()}
        self.bundle=Path(ap.__file__).parent

    def apply(self):
        buf=StringIO()
        with patch.object(ap, "CURRENT_SHA", self.expected):
            with redirect_stdout(buf):
                try:
                    code=ap.main(["--target",str(self.target),"--apply"])
                except RuntimeError as exc:
                    code=2
        parsed=[]
        for line in buf.getvalue().splitlines():
            try:parsed.append(json.loads(line))
            except ValueError:pass
        return code,parsed

    def check_originals_and_clean(self):
        for name, data in self.source.items():
            self.assertEqual((self.target/name).read_bytes(),data,name)
        self.assertFalse((self.target/"fixture_relay_attestation.py").exists())
        self.assertFalse((self.target/"test_fixture_relay_attestation.py").exists())
        self.assertFalse((self.target/".cdp_fixture_proof_patch.lock").exists())
        self.assertEqual([],list(self.target.glob("*.tmp")))

    def test_success_has_expected_files_and_backups(self):
        code,logs=self.apply()
        self.assertEqual(code,0)
        self.assertTrue(logs[-1]["ok"])
        self.assertEqual(len(list(self.target.glob("*.bak"))),2)
        for name in self.source:
            before=self.source[name]
            expected=ap.transform(name,before)
            self.assertEqual((self.target/name).read_bytes(),expected)
        self.assertFalse((self.target/".cdp_fixture_proof_patch.lock").exists())

    def test_injected_failure_on_second_replacement_restores_originals(self):
        real_replace=ap.os.replace
        faulted=[False]
        def inject(src,dst):
            if Path(dst).name=="fixture_worker.py" and not faulted[0]:
                faulted[0]=True
                raise OSError("simulated commit failure before worker source update")
            return real_replace(src,dst)
        with patch.object(ap.os,"replace",side_effect=inject):
            code,logs=self.apply()
        self.assertEqual(code,2)
        self.assertTrue(faulted[0])
        roll=next(x for x in logs if "rollback" in x)
        self.assertIn("test_worker_subprocess_e2e.py",roll["rollback"]["restored"])
        self.assertEqual(roll["rollback"]["conflicts"],[])
        self.check_originals_and_clean()
        self.assertEqual(len(list(self.target.glob("*.bak"))),2)

    def test_postcommit_check_failure_rolls_back_all_own_changes(self):
        with patch.object(ap,"verify_post_commit",side_effect=RuntimeError("test injected")):
            code,logs=self.apply()
        self.assertEqual(code,2)
        roll=next(x for x in logs if "rollback" in x)
        self.assertEqual(set(roll["rollback"]["restored"]),set(self.source))
        self.assertEqual(len(roll["rollback"]["removed"]),2)
        self.assertEqual(roll["rollback"]["conflicts"],[])
        self.check_originals_and_clean()

    def test_concurrent_foreign_write_is_preserved_and_reported(self):
        foreign=b"# FOREIGN CONCURRENT VERSION; must remain untouched\n"
        def injected_verify(_target,_changed,_added):
            (_target/"fixture_worker.py").write_bytes(foreign)
            raise RuntimeError("concurrent source mutation")
        with patch.object(ap,"verify_post_commit",side_effect=injected_verify):
            code,logs=self.apply()
        self.assertEqual(code,2)
        roll=next(x for x in logs if "rollback" in x)
        self.assertTrue(roll["manual_review_required"])
        self.assertIn("fixture_worker.py",roll["rollback"]["conflicts"])
        self.assertIn("test_worker_subprocess_e2e.py",roll["rollback"]["restored"])
        self.assertEqual((self.target/"fixture_worker.py").read_bytes(),foreign)
        self.assertEqual((self.target/"test_worker_subprocess_e2e.py").read_bytes(),self.source["test_worker_subprocess_e2e.py"])
        self.assertFalse((self.target/"fixture_relay_attestation.py").exists())
        self.assertFalse((self.target/".cdp_fixture_proof_patch.lock").exists())

    def test_stale_lock_blocks_without_touching_source(self):
        lock=self.target/".cdp_fixture_proof_patch.lock"
        lock.write_bytes(b"foreign lock: manual review")
        with patch.object(ap,"CURRENT_SHA",self.expected):
            with redirect_stdout(StringIO()):
                with self.assertRaises(RuntimeError):
                    ap.main(["--target",str(self.target),"--apply"])
        self.assertEqual(lock.read_bytes(),b"foreign lock: manual review")
        for name,data in self.source.items():
            self.assertEqual((self.target/name).read_bytes(),data)
        self.assertFalse((self.target/"fixture_relay_attestation.py").exists())

    def test_changed_lock_after_commit_is_not_removed_and_rolls_back(self):
        corrupted=b"replaced or tampered lock"
        def injected_verify(_target,_changed,_added):
            ap.verify_post_commit_original(_target,_changed,_added)
            (_target/".cdp_fixture_proof_patch.lock").write_bytes(corrupted)
        with patch.object(ap,"verify_post_commit_original",ap.verify_post_commit,create=True):
            with patch.object(ap,"verify_post_commit",side_effect=injected_verify):
                code,logs=self.apply()
        self.assertEqual(code,2)
        roll=next(x for x in logs if "rollback" in x)
        self.assertFalse(roll["manual_review_required"])
        self.assertIn("staging_lock_not_owned_after_operation", " ".join(str(x) for x in logs))
        self.assertEqual((self.target/".cdp_fixture_proof_patch.lock").read_bytes(),corrupted)
        for name,data in self.source.items():self.assertEqual((self.target/name).read_bytes(),data)
        self.assertFalse((self.target/"fixture_relay_attestation.py").exists())

    def test_rollback_refuses_overwrite_foreign_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            p=root/"test.py"
            p.write_bytes(b"someone else's content")
            self.assertFalse(ap._atomic_restore(root,"test.py",b"before",b"our new bytes"))
            self.assertEqual(p.read_bytes(),b"someone else's content")

    def test_replace_raises_after_successful_side_effect_still_restores(self):
        real_replace=ap.os.replace
        fired=[False]
        def after_effect(src,dst):
            result=real_replace(src,dst)
            if Path(dst).name=="test_worker_subprocess_e2e.py" and not fired[0]:
                fired[0]=True
                raise OSError("injected error after atomic replacement")
            return result
        with patch.object(ap.os,"replace",side_effect=after_effect):
            code,logs=self.apply()
        self.assertTrue(fired[0])
        self.assertEqual(code,2)
        self.check_originals_and_clean()
        summary=next(x for x in logs if "rollback" in x)
        self.assertIn("test_worker_subprocess_e2e.py",summary["rollback"]["restored"])
        self.assertEqual(summary["rollback"]["conflicts"],[])

    def test_unsupported_hardlinks_fail_closed_before_source_update(self):
        real_link=ap.os.link
        failed=[False]
        def no_support(src,dst,*args,**kw):
            if not failed[0]:
                failed[0]=True
                raise OSError("hardlinks unsupported on this volume")
            return real_link(src,dst,*args,**kw)
        with patch.object(ap.os,"link",side_effect=no_support):
            code,logs=self.apply()
        self.assertTrue(failed[0])
        self.assertEqual(code,2)
        self.check_originals_and_clean()
        self.assertEqual([],list(self.target.glob("*.bak")))
        self.assertEqual([],list(self.target.glob(".cdp-proof-publish-*.tmp")))

    def test_create_only_link_raises_after_side_effect_without_partial_file(self):
        real_link=ap.os.link
        fired=[False]
        def after_effect(src,dst,*args,**kwargs):
            result=real_link(src,dst,*args,**kwargs)
            if Path(dst).name=="fixture_relay_attestation.py" and not fired[0]:
                fired[0]=True
                raise OSError("injected hardlink error after file creation")
            return result
        with patch.object(ap.os,"link",side_effect=after_effect):
            code,logs=self.apply()
        self.assertTrue(fired[0])
        self.assertEqual(code,2)
        self.check_originals_and_clean()
        summary=next(x for x in logs if "rollback" in x)
        self.assertEqual(summary["rollback"]["conflicts"],[])
        self.assertEqual(len(list(self.target.glob("*.bak"))),2)

    def test_concurrent_source_modification_between_replacements_is_preserved(self):
        real_replace=ap.os.replace
        foreign=b"# concurrent contributor source; never overwrite this\n"
        inserted=[False]
        def change_next(src,dst):
            result=real_replace(src,dst)
            if Path(dst).name=="test_worker_subprocess_e2e.py" and not inserted[0]:
                inserted[0]=True
                (self.target/"fixture_worker.py").write_bytes(foreign)
            return result
        with patch.object(ap.os,"replace",side_effect=change_next):
            code,logs=self.apply()
        self.assertEqual(code,2)
        self.assertEqual((self.target/"fixture_worker.py").read_bytes(),foreign)
        self.assertEqual((self.target/"test_worker_subprocess_e2e.py").read_bytes(),
                         self.source["test_worker_subprocess_e2e.py"])
        self.assertFalse((self.target/"fixture_relay_attestation.py").exists())
        summary=next(x for x in logs if "rollback" in x)
        self.assertIn("test_worker_subprocess_e2e.py",summary["rollback"]["restored"])
        self.assertEqual(summary["rollback"]["conflicts"],[])

    def test_invalid_apply_preflight_does_not_create_backups(self):
        (self.target/"fixture_worker.py").write_bytes(b"drifted")
        with patch.object(ap,"CURRENT_SHA",self.expected):
            with self.assertRaises(RuntimeError):
                ap.preflight(self.target,self.bundle)
        self.assertEqual([],list(self.target.glob("*.bak")))
        self.assertEqual([],list(self.target.glob("*.tmp")))


if __name__ == "__main__":
    unittest.main(verbosity=2)

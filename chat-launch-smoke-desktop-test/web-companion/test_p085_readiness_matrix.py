"""No privilege, no principal provisioning: P085 readiness negative QA."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import p085_readiness_matrix as m

def deployment(**overrides):
    base={
        "ok":True,"read_only":True,"windows":True,
        "blockers":["ELEVATED_INSTALLER_NOT_AVAILABLE",
            "BROKER_PYTHON_RUNTIME_UNTRUSTED","BROKER_SERVICE_UNPROVISIONED",
            "REAL_TWO_SID_E2E_UNVERIFIED"],
        "safe_to_activate":False,
        "stage_health":{"status":"NOT_STAGED","blockers":["P085_STAGE_NOT_INSTALLED"],
                        "trusted_deployment_verified":False}
    }
    base.update(overrides)
    return base

def accounts(**overrides):
    base={"ok":True,"schema":"p085-local-principals-readonly-check-v1",
          "accounts_configured":0,"accounts_missing":5,
          "system_changes_performed":False,
          "p085_production_enabled":False,
          "account_owners_os_attested":False}
    base.update(overrides)
    return base

class MatrixTests(unittest.TestCase):
    def test_zero_principals_and_stage_is_denied(self):
        result=m.evaluate(deployment=deployment(),accounts=accounts())
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"],"UNTRUSTED")
        self.assertEqual(result["accounts_missing"],5)
        self.assertIn("P085_STAGE_NOT_INSTALLED",result["blockers"])
        self.assertIn("P085_ISOLATED_PRINCIPALS_MISSING",result["blockers"])
        self.assertFalse(result["owner_authorization_verified"])
        self.assertFalse(result["can_complete_chain2"])
        self.assertFalse(result["can_activate_chain3"])
    def test_forged_success_does_not_grant_privilege(self):
        fake=deployment(safe_to_activate=True,
            stage_health={"status":"STRUCTURE_AND_ACL_OBSERVED","blockers":[],
                          "trusted_deployment_verified":True},
            blockers=[])
        result=m.evaluate(deployment=fake,accounts=accounts(
            accounts_configured=5,accounts_missing=0))
        self.assertTrue(result["ok"])
        self.assertIn("UNTRUSTED_SELF_ASSERTED_DEPLOYMENT_READY",result["blockers"])
        self.assertFalse(result["trusted_actor_verified"])
        self.assertFalse(result["broker_service_trusted"])
        self.assertFalse(result["can_activate_chain3"])
    def test_malformed_schema_is_unknown(self):
        scenarios=[
            (deployment(),accounts(accounts_missing=True)),
            (deployment(),accounts(accounts_configured=4)),
            (deployment(),accounts(system_changes_performed=True)),
            (deployment(),accounts(account_owners_os_attested=True)),
            (deployment(stage_health={}),accounts()),
            (deployment(blockers="ALL_OK"),accounts()),
            (deployment(),accounts(p085_production_enabled=True)),
        ]
        for dep,acc in scenarios:
            with self.subTest(dep=dep,acc=acc):
                x=m.evaluate(deployment=dep,accounts=acc)
                self.assertFalse(x["ok"])
                self.assertEqual(x["status"],"UNKNOWN")
                self.assertFalse(x["owner_authorization_verified"])
                self.assertFalse(x["can_complete_chain2"])
    def test_invalid_objects_fail_closed(self):
        for d,a in ((None,None),("true",[]),(True,True)):
            result=m.evaluate(deployment=d,accounts=a)
            self.assertEqual(result["status"],"UNKNOWN")
            self.assertFalse(result["can_activate_chain3"])
    def test_cli_error_fails_closed(self):
        with patch.object(m,"run_readonly_script",side_effect=[
            {"ok":False,"error_code":"DIAGNOSTIC_TIMEOUT"},
            {"ok":True,"value":accounts()}]):
            r=m.inspect()
        self.assertEqual(r["status"],"UNKNOWN")
        self.assertFalse(r["can_activate_chain3"])
        self.assertFalse(r["owner_authorization_verified"])
    def test_healthy_but_untrusted_status_always_denies(self):
        with patch.object(m,"run_readonly_script",side_effect=[
            {"ok":True,"value":deployment(blockers=[],
                stage_health={"status":"STRUCTURE_AND_ACL_OBSERVED",
                "blockers":[],"trusted_deployment_verified":False})},
            {"ok":True,"value":accounts(accounts_configured=5,accounts_missing=0)}]):
            r=m.inspect()
        self.assertTrue(r["ok"])
        self.assertFalse(r["trusted_actor_verified"])
        self.assertFalse(r["can_complete_chain2"])
    def test_nonallowlisted_program_never_invoked(self):
        with tempfile.TemporaryDirectory() as td:
            file=Path(td)/"foo.py"
            file.write_text("raise RuntimeError('should not run')",encoding="utf-8")
            with patch.object(m.subprocess,"run",side_effect=AssertionError("EXECUTED")):
                r=m.run_readonly_script(file)
            self.assertEqual(r["error_code"],"SCRIPT_NOT_ALLOWLISTED")
    def test_source_drift_denies_execution(self):
        with tempfile.TemporaryDirectory() as td:
            file=Path(td)/m.SOURCES["deployment"][0]
            file.write_text("print('pretend ok')",encoding="utf-8")
            with patch.object(m,"OWNER",Path(td)),\
                 patch.object(m.subprocess,"run",side_effect=AssertionError("EXECUTED")):
                r=m.run_readonly_script(file)
            self.assertEqual(r["error_code"],"SCRIPT_SOURCE_SHA_DRIFT")
    def test_source_drift_during_run_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            file=root/"owner_readonly.py"
            initial=b'print("{\\"ok\\": true}")\n'
            file.write_bytes(initial)
            digest=hashlib.sha256(initial).hexdigest()
            def race(*args,**kwargs):
                self.assertEqual(args[0][1:5],["-E","-s","-S","-B"])
                file.write_bytes(b"print('SOURCE_CHANGED')")
                return type("Result",(),{"returncode":0,"stdout":'{"ok":true}'})()
            with patch.object(m,"OWNER",root),\
                 patch.object(m,"SOURCES",{"deployment":("owner_readonly.py",digest)}),\
                 patch.object(m.subprocess,"run",side_effect=race):
                result=m.run_readonly_script(file)
            self.assertEqual(result["error_code"],"SCRIPT_SOURCE_CHANGED_DURING_RUN")

    def test_dependency_inventory_detects_mutation_without_execution(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            target=root/"Setup-P085WindowsPrincipals.ps1"
            target.write_bytes(b"# owner reviewed powershell dry-run")
            mod=root/"experiments"/"foundation_kernel"/"windows_isolation.py"
            mod.parent.mkdir(parents=True)
            mod.write_bytes(b"# approved isolation validator")
            pins={
                "Setup-P085WindowsPrincipals.ps1":
                    hashlib.sha256(target.read_bytes()).hexdigest(),
                "experiments/foundation_kernel/windows_isolation.py":
                    hashlib.sha256(mod.read_bytes()).hexdigest()
            }
            first=m.check_source_inventory(root,pinned=pins)
            self.assertTrue(first["ok"])
            self.assertFalse(first["os_trust_verified"])
            target.write_bytes(b"# ATTACKER DRIFT")
            second=m.check_source_inventory(root,pinned=pins)
            self.assertFalse(second["ok"])
            self.assertEqual(second["error_code"],"SOURCE_DEPENDENCY_SHA_DRIFT")

    def test_namespace_module_injection_and_symlink_fail_closed(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            folder=root/"experiments"
            folder.mkdir()
            target=root/"safe.py"
            target.write_text("pass\n")
            pins={"safe.py":hashlib.sha256(target.read_bytes()).hexdigest()}
            self.assertTrue(m.check_source_inventory(root,pinned=pins)["ok"])
            (folder/"__init__.py").write_text("print('import code injection')\n")
            rejected=m.check_source_inventory(root,pinned=pins)
            self.assertFalse(rejected["ok"])
            self.assertEqual(rejected["error_code"],"PYTHON_PACKAGE_LAYOUT_CHANGED")

    def test_nonlisted_dependency_blocks_before_running_any_script(self):
        with patch.object(m,"check_source_inventory",return_value={
            "ok":False,"error_code":"SOURCE_DEPENDENCY_SHA_DRIFT"}),\
             patch.object(m,"run_readonly_script",
                 side_effect=AssertionError("SHOULD_NOT_EXECUTE")):
            result=m.inspect()
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"],"UNKNOWN")
        self.assertEqual(result["blockers"],["SOURCE_DEPENDENCY_SHA_DRIFT"])
        self.assertFalse(result["can_activate_chain3"])

    def test_post_execution_drift_never_returns_healthy_status(self):
        with patch.object(m,"check_source_inventory",side_effect=[
                {"ok":True,"sources_checked":6,"dependency_sources_checked":4},
                {"ok":False,"error_code":"SOURCE_DEPENDENCY_SHA_DRIFT"}]),\
             patch.object(m,"run_readonly_script",side_effect=[
                {"ok":True,"value":deployment()},
                {"ok":True,"value":accounts()}]):
            result=m.inspect()
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"],"UNKNOWN")
        self.assertFalse(result["owner_authorization_verified"])

    def test_genuine_nonroot_paths_are_not_exposed(self):
        r=m.evaluate(deployment=deployment(blockers=["BROKER_CODE_TREE_UNTRUSTED",
            "C:/Users/my-private/path","P085_STAGE_NOT_INSTALLED"]),accounts=accounts())
        combined=json.dumps(r)
        self.assertNotIn("Users",combined)
        self.assertNotIn("my-private",combined)
    def test_no_mutation_fields_ever_true(self):
        r=m.evaluate(deployment=deployment(),accounts=accounts())
        self.assertFalse(r["accounts_changed"])
        self.assertFalse(r["service_installed_by_this_check"])

if __name__=="__main__":
    unittest.main()

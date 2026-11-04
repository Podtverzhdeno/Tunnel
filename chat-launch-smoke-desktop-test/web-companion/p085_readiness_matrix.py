"""P085 owner readiness matrix: read-only and permanently non-authorizing.

Runs two existing, bounded diagnostic programs (the account program executes
only the previously reviewed non-interactive PowerShell *dry-run*). No UAC,
account creation, service installation, ACL mutation, broker pipe access,
keys, tokens, grants, lease requests or changing Chain2/Chain3 states.

Important: agent-editable status/plan/check outputs cannot establish OS-backed
actor identity or approval. This diagnostic NEVER returns auth=true.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

APP=Path(__file__).resolve().parent
OWNER=APP.parent.parent/"Local-Workspace-Extensions"
SOURCES={
    "deployment":("p085_windows_deploy_preflight.py",
        "acea00b3723cc4cdf98b28b1a9c609ac16fddc5637a358c20a76886a26c16baf"),
    "accounts":("p085_accounts_readonly_check.py",
        "1194b6fbcb866a8b8494d3266abd83f87519a0fa88e4be704156d1ee786f7560"),
}
# The two directly executed scripts import additional agent-editable modules,
# and the account inventory calls a mutable PowerShell -File. Every dependency
# must be SHA-checked before running ANY diagnostic. This still does not
# establish OS-protected code authenticity.
DEPENDENCIES={
    "p085_stage_audit.py":
        "87746aebbebb08cdff422bbb573c4a20d3decae02e73d291d4b1846b58b9cb09",
    "experiments/foundation_kernel/windows_isolation.py":
        "d9f7a5735d0aede9e1229952d7937c6d3e809119cf9175a4fbbc5f6616b8ca61",
    "experiments/foundation_kernel/__init__.py":
        "bfdcfcfc34f24854440ad13e99d8b95d8fb2036e1faec6aa34b9c5f83b37a941",
    "Setup-P085WindowsPrincipals.ps1":
        "a24c8ab163dec6782d4afc0335f633e8f426a1025d3f5e42c90993dd7589dbb9",
}
ISSUES={
    "P085_STAGE_NOT_INSTALLED":"OWNER_REVIEW_PROTECTED_STAGING",
    "ELEVATED_INSTALLER_NOT_AVAILABLE":"OWNER_INTERACTIVE_UAC_REQUIRED",
    "BROKER_PYTHON_RUNTIME_UNTRUSTED":"OWNER_REVIEW_INTERPRETER_ACL",
    "BROKER_CODE_TREE_UNTRUSTED":"OWNER_REVIEW_IMMUTABLE_CODE_ACL",
    "BROKER_SERVICE_UNPROVISIONED":"OWNER_SERVICE_INSTALL_NOT_VERIFIED",
    "REAL_TWO_SID_E2E_UNVERIFIED":"INDEPENDENT_TWO_SID_E2E_REQUIRED",
    "WINDOWS_PROCESS_SID_NOT_VERIFIED":"PROCESS_IDENTITY_UNVERIFIED",
    "WINDOWS_REQUIRED":"WINDOWS_HOST_REQUIRED",
}
_ALLOWED_STAGE={"NOT_STAGED","STRUCTURE_AND_ACL_OBSERVED","UNTRUSTED_LOCATION",
    "STRUCTURE_FAILED","INSPECTION_FAILED"}

def check_source_inventory(root:Path=OWNER, *, pinned:dict|None=None)->dict:
    """Inspect all locally importable diagnostics, without executing Python or PS.

    Digest checks detect ordinary source drift, not malicious modification of
    this checker, protected Windows launchers or privileged host identity.
    """
    expected=(pinned if pinned is not None else
              {**{name:sha for name,sha in SOURCES.values()},**DEPENDENCIES})
    try:
        if (not root.is_dir() or root.is_symlink()
            or getattr(root,"is_junction",lambda:False)()):
            return {"ok":False,"error_code":"SOURCE_ROOT_UNSAFE"}
        # An injected experiments/__init__.py could transform the intended
        # namespace package import routing into executable startup code.
        if (root/"experiments"/"__init__.py").exists():
            return {"ok":False,"error_code":"PYTHON_PACKAGE_LAYOUT_CHANGED"}
        if type(expected) is not dict or not expected:
            return {"ok":False,"error_code":"EMPTY_DEPENDENCY_MANIFEST"}
        for rel,sha in expected.items():
            if (type(rel) is not str or type(sha) is not str or
                len(sha)!=64 or any(c not in "0123456789abcdef" for c in sha)):
                return {"ok":False,"error_code":"SOURCE_MANIFEST_INVALID"}
            part=Path(rel)
            if part.is_absolute() or ".." in part.parts or "\\" in rel or ":" in rel:
                return {"ok":False,"error_code":"SOURCE_MANIFEST_INVALID"}
            cur=root
            for folder in part.parts[:-1]:
                cur=cur/folder
                if (not cur.is_dir() or cur.is_symlink()
                    or getattr(cur,"is_junction",lambda:False)()):
                    return {"ok":False,"error_code":"SOURCE_IMPORT_PATH_UNSAFE"}
            target=root/part
            if (not target.is_file() or target.is_symlink()
                or getattr(target,"is_junction",lambda:False)()):
                return {"ok":False,"error_code":"SOURCE_DEPENDENCY_MISSING_OR_UNSAFE"}
            if target.stat().st_size>500000:
                return {"ok":False,"error_code":"SOURCE_DEPENDENCY_OVERSIZED"}
            actual=hashlib.sha256(target.read_bytes()).hexdigest()
            if actual!=sha:
                return {"ok":False,"error_code":"SOURCE_DEPENDENCY_SHA_DRIFT"}
        return {"ok":True,"sources_checked":len(expected),
                "dependency_sources_checked":len(DEPENDENCIES) if pinned is None else None,
                "os_trust_verified":False}
    except (OSError,ValueError,TypeError):
        return {"ok":False,"error_code":"SOURCE_INVENTORY_UNREADABLE"}

def run_readonly_script(path:Path,*,timeout:int=30)->dict:
    """Allow only exact reviewed diagnostic sources, NEVER arbitrary commands."""
    if not isinstance(path,Path): return {"ok":False,"error_code":"SCRIPT_PATH_TYPE"}
    matched=[(name,digest) for name,digest in SOURCES.values()
             if path==OWNER/name]
    if not matched:return {"ok":False,"error_code":"SCRIPT_NOT_ALLOWLISTED"}
    file=path
    try:
        if (not file.is_file() or file.is_symlink() or
            getattr(file,"is_junction",lambda:False)() or
            not file.parent.resolve()==OWNER.resolve()):
            return {"ok":False,"error_code":"SCRIPT_NOT_REGULAR"}
        payload=file.read_bytes()
        if len(payload)>30000 or hashlib.sha256(payload).hexdigest()!=matched[0][1]:
            return {"ok":False,"error_code":"SCRIPT_SOURCE_SHA_DRIFT"}
        # No shell, no PowerShell elevated mode, no pip/install. -B prevents
        # local .pyc writes on this diagnostic path.
        result=subprocess.run([sys.executable,"-E","-s","-S","-B",str(file)],cwd=OWNER,
                              input="",capture_output=True,text=True,
                              timeout=timeout,check=False)
        # Post-execution check detects ordinary concurrent source drift. It
        # cannot prove code trust against a privileged/agent-editable host.
        if (not file.is_file() or file.is_symlink() or
            hashlib.sha256(file.read_bytes()).hexdigest()!=matched[0][1]):
            return {"ok":False,"error_code":"SCRIPT_SOURCE_CHANGED_DURING_RUN"}
        if result.returncode!=0 or len(result.stdout)>10000:
            return {"ok":False,"error_code":"DIAGNOSTIC_PROGRAM_FAILED"}
        value=json.loads(result.stdout)
        if type(value) is not dict or value.get("ok") is not True:
            return {"ok":False,"error_code":"DIAGNOSTIC_JSON_UNTRUSTED"}
        return {"ok":True,"value":value}
    except subprocess.TimeoutExpired:
        return {"ok":False,"error_code":"DIAGNOSTIC_TIMEOUT"}
    except (OSError,ValueError,UnicodeError,TypeError):
        return {"ok":False,"error_code":"DIAGNOSTIC_UNREADABLE"}

def evaluate(*,deployment:dict,accounts:dict)->dict:
    """Fail closed on unexpected formats; no raw paths/SIDs/credentials output."""
    if type(deployment) is not dict or type(accounts) is not dict:
        return {"ok":False,"status":"UNKNOWN","blockers":["INVALID_DIAGNOSTIC_DATA"],
                "owner_authorization_verified":False,"can_activate_chain3":False,
                "can_complete_chain2":False}
    try:
        if (deployment.get("ok") is not True or
            deployment.get("read_only") is not True or
            type(deployment.get("blockers")) is not list or
            type(deployment.get("windows")) is not bool or
            type(deployment.get("safe_to_activate")) is not bool or
            type(deployment.get("stage_health")) is not dict or
            accounts.get("ok") is not True or
            accounts.get("schema")!="p085-local-principals-readonly-check-v1" or
            type(accounts.get("accounts_configured")) is not int or
            type(accounts.get("accounts_missing")) is not int or
            accounts["accounts_missing"]+accounts["accounts_configured"]!=5 or
            not 0<=accounts["accounts_missing"]<=5 or
            accounts.get("system_changes_performed") is not False or
            accounts.get("p085_production_enabled") is not False or
            accounts.get("account_owners_os_attested") is not False):
            raise ValueError("invalid_evidence_schema")
        stage=deployment["stage_health"]
        if (type(stage.get("status")) is not str or
            type(stage.get("blockers")) is not list or
            type(stage.get("trusted_deployment_verified")) is not bool):
            raise ValueError("stage_status_untrusted")
        reasons=[reason for reason in deployment["blockers"]+
                 stage["blockers"] if type(reason) is str and reason in ISSUES]
        if deployment["safe_to_activate"] or stage["trusted_deployment_verified"]:
            # These are not expected from agent-editable diagnostic programs:
            # even success-looking data cannot establish signed host identity.
            reasons.append("UNTRUSTED_SELF_ASSERTED_DEPLOYMENT_READY")
        if accounts["accounts_missing"]>0:
            reasons.append("P085_ISOLATED_PRINCIPALS_MISSING")
        if accounts.get("account_owners_os_attested") is not False:
            reasons.append("P085_ACCOUNTS_OWNER_ATTESTATION_UNKNOWN")
        if stage["status"] not in _ALLOWED_STAGE:
            reasons.append("UNRECOGNIZED_STAGE_STATUS")
        blockers=sorted(set(reasons))
        # Evidence is advisory from a user-writable workspace. Only an
        # independent OS-protected broker+owner interaction can grant actor
        # capabilities. No such external trust provider is connected here.
        return {"ok":True,"mode":"READ_ONLY","status":"UNTRUSTED",
                "source":"LOCAL_DIAGNOSTICS_NOT_OS_ATTESTATION",
                "windows":deployment["windows"],
                "accounts_configured":accounts["accounts_configured"],
                "accounts_missing":accounts["accounts_missing"],
                "stage_status":stage["status"],
                "blockers":blockers,
                "owner_review_phases":[
                    "REVIEW_EXISTING_OWNER_ONLY_INSTALLER_AND_ACCOUNT_DRY_RUN",
                    "OWNER_INTERACTIVE_PROVISION_IDENTITIES_IF_APPROVED",
                    "OWNER_PROTECT_PYTHON_AND_CODE_WITH_WINDOWS_ACL",
                    "INDEPENDENT_TWO_PRINCIPAL_E2E_AND_IDENTITY_BROKER_REVIEW",
                    "OWNER_AUTHORIZED_PRODUCTION_CUTOVER_ONLY_AFTER_PROOF"],
                "owner_authorization_verified":False,
                "trusted_actor_verified":False,
                "broker_service_trusted":False,
                "can_activate_chain3":False,
                "can_complete_chain2":False,
                "accounts_changed":False,
                "service_installed_by_this_check":False}
    except (ValueError,KeyError,TypeError):
        return {"ok":False,"status":"UNKNOWN",
                "blockers":["DIAGNOSTIC_SCHEMA_INVALID"],
                "owner_authorization_verified":False,
                "trusted_actor_verified":False,
                "can_activate_chain3":False,"can_complete_chain2":False,
                "accounts_changed":False,
                "service_installed_by_this_check":False}

def inspect():
    pre=check_source_inventory(OWNER)
    if pre.get("ok") is not True:
        return {"ok":False,"mode":"READ_ONLY","status":"UNKNOWN",
                "blockers":[pre.get("error_code","SOURCE_INVENTORY_UNVERIFIED")],
                "owner_authorization_verified":False,
                "trusted_actor_verified":False,
                "can_activate_chain3":False,"can_complete_chain2":False,
                "accounts_changed":False,
                "service_installed_by_this_check":False}
    dep=run_readonly_script(OWNER/SOURCES["deployment"][0])
    acc=run_readonly_script(OWNER/SOURCES["accounts"][0])
    post=check_source_inventory(OWNER)
    if post.get("ok") is not True:
        return {"ok":False,"mode":"READ_ONLY","status":"UNKNOWN",
                "blockers":[post.get("error_code","SOURCE_CHANGED_DURING_DIAGNOSTIC")],
                "owner_authorization_verified":False,
                "trusted_actor_verified":False,
                "can_activate_chain3":False,"can_complete_chain2":False,
                "accounts_changed":False,
                "service_installed_by_this_check":False}
    if dep.get("ok") is not True or acc.get("ok") is not True:
        return {"ok":False,"mode":"READ_ONLY","status":"UNKNOWN",
                "blockers":[dep.get("error_code","UNKNOWN_DEPLOYMENT"),
                            acc.get("error_code","UNKNOWN_ACCOUNTS")],
                "owner_authorization_verified":False,
                "trusted_actor_verified":False,
                "can_activate_chain3":False,"can_complete_chain2":False,
                "accounts_changed":False,
                "service_installed_by_this_check":False}
    result=evaluate(deployment=dep["value"],accounts=acc["value"])
    result["source_inventory_checked"]=pre["sources_checked"]
    result["dependency_sources_checked"]=pre["dependency_sources_checked"]
    return result

if __name__=="__main__":
    print(json.dumps(inspect(),ensure_ascii=True,sort_keys=True))

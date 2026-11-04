"""P085 autonomous, NON-PRIVILEGED readiness and regression supervisor.

Read-only inspection + bounded tests; optional atomic report persistence under
the existing .agent-team/reports. Cannot install services, create OS accounts,
modify MCP tools, accept code, approve specs or operate the live SQLite queue.

Run hourly from an existing trusted scheduled task; repeat safely.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
import uuid
from contextlib import closing, contextmanager, nullcontext
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import urlopen

ROOT=Path(__file__).resolve().parent
MIRROR=ROOT/"mcp-host-source"
REPORTS=ROOT/".agent-team"/"reports"
SCHEMA="p085-autonomous-readiness-v1"
MAX_REPORT_BYTES=32768
AUDIT_LOCK_NAME="P085_auto_single_instance.lock"
# A green "Ran 0 tests" is never meaningful proof of tested capabilities.
MIN_TESTS={"observer-profile":10,"observer-http":5,
           "host-full-suite":30,"broker-os-ipc-supervisor":30,
           "windows-bootstrap-negative":10}
SUITES=(
    ("observer-profile", MIRROR,
     ("-m","unittest","discover","-s","tests","-p","test_capability_profiles.py","-q")),
    ("observer-http", MIRROR,
     ("-m","unittest","discover","-s","tests","-p","test_http.py","-q")),
    # Full mirror suite is mandatory: never mask other agents' unfinished
    # modules by passing the targeted observer checks alone.
    ("host-full-suite", MIRROR,
     ("-m","unittest","discover","-s","tests","-q")),
    ("broker-os-ipc-supervisor", ROOT,
     ("-m","unittest",
      "experiments.foundation_kernel.test_windows_isolation",
      "experiments.foundation_kernel.test_win_pipe_ipc",
      "experiments.foundation_kernel.test_broker_supervisor","-q")),
    ("windows-bootstrap-negative", ROOT,
     ("-m","unittest","test_p085_windows_deploy_preflight",
      "test_p085_broker_runtime_boundary",
      "test_p085_immutable_code_acl",
      "test_p085_broker_release_candidate",
      "test_p085_stage_audit",
      "test_p085_installer_source","-q")),
    ("normative-crosswalk", ROOT, ("spec_check.py",)),
    ("approved-openspec-strict", ROOT, (
        "ops.py", "spec_validate","Local-Workspace-Extensions",
        "add-agent-foundation-hardening")),
)
REQUIRED=(MIRROR/"SOURCE_MANIFEST.json",MIRROR/"manage.py",
          MIRROR/"workspace_mcp"/"tools.py",
          MIRROR/"workspace_mcp"/"http_server.py",
          ROOT/"host_patches"/"P085_observer_bearer_http_v4.patch",
          ROOT/"experiments"/"foundation_kernel"/"broker_named_pipe.py",
          ROOT/"experiments"/"foundation_kernel"/"broker_supervisor.py",
          ROOT/"openspec"/"changes"/"add-agent-foundation-hardening"/"tasks.md",
          ROOT/"p085_autonomous_guard.py",
          ROOT/"test_p085_autonomous_guard.py",
          ROOT/"p085_windows_deploy_preflight.py",
          ROOT/"test_p085_windows_deploy_preflight.py",
          ROOT/"p085_broker_release_candidate.py",
          ROOT/"test_p085_broker_release_candidate.py",
          ROOT/"test_p085_broker_runtime_boundary.py",
          ROOT/"test_p085_immutable_code_acl.py",
          ROOT/"Install-P085ProtectedService.ps1",
          ROOT/"test_p085_installer_source.py",
          ROOT/"p085_stage_audit.py",
          ROOT/"test_p085_stage_audit.py",
          ROOT/"spec_check.py",ROOT/"spec_analyzer.py",ROOT/"spec_tools.py")

class ReadinessError(ValueError):
    pass


def _audit_directory(dest:Path)->Path:
    dest=Path(dest)
    if dest.is_symlink() or getattr(dest,"is_junction",lambda:False)():
        raise ReadinessError("REPORT_DIRECTORY_LINKED")
    dest.mkdir(parents=True,exist_ok=True)
    if not dest.is_dir():
        raise ReadinessError("REPORT_DIRECTORY_INVALID")
    return dest


@contextmanager
def single_audit(dest=REPORTS):
    """One audit process per report directory; not an OS trust boundary.

    The file lock is held through tests, comparison and report publication.
    A crash releases the OS lock; no stale PID/age heuristics or forced
    deletion can steal another auditor's lease.
    """
    dest=_audit_directory(dest)
    path=dest/AUDIT_LOCK_NAME
    if path.is_symlink() or getattr(path,"is_junction",lambda:False)():
        raise ReadinessError("AUDIT_LOCK_LINKED")
    with path.open("a+b") as handle:
        if path.stat().st_size==0:
            handle.write(b"\\0")
            handle.flush()
        handle.seek(0)
        if os.name=="nt":
            import msvcrt
            lock_error=OSError
            lock=lambda:msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
            unlock=lambda:msvcrt.locking(handle.fileno(),msvcrt.LK_UNLCK,1)
        else:
            import fcntl
            lock_error=BlockingIOError
            lock=lambda:fcntl.flock(handle.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
            unlock=lambda:fcntl.flock(handle.fileno(),fcntl.LOCK_UN)
        try:
            lock()
        except lock_error as exc:
            raise ReadinessError("AUDIT_ALREADY_RUNNING") from exc
        try:
            yield
        finally:
            unlock()


def _safe_file(path:Path)->bool:
    return path.is_file() and not path.is_symlink() and not getattr(path,"is_junction",lambda:False)()


def observed_paths()->tuple[Path,...]:
    """Freeze a bounded complete Python source set before running a suite."""
    selected=set(REQUIRED)
    folders=(
        MIRROR/"workspace_mcp",MIRROR/"tests",
        ROOT/"experiments"/"foundation_kernel",
    )
    for folder in folders:
        if folder.is_symlink() or getattr(folder,"is_junction",lambda:False)():
            raise ReadinessError("SOURCE_FOLDER_LINKED")
        if folder.is_dir():
            for candidate in folder.rglob("*.py"):
                current=candidate
                while current!=folder:
                    if current.is_symlink() or getattr(
                            current,"is_junction",lambda:False)():
                        raise ReadinessError("SOURCE_PATH_LINKED")
                    current=current.parent
                if len(selected)>=240:
                    raise ReadinessError("SOURCE_DISCOVERY_LIMIT")
                selected.add(candidate)
    return tuple(sorted(selected))


def source_digest(paths=None)->dict:
    """Fingerprint the *observed* artifact bytes, not their approval status."""
    if paths is None:
        paths=observed_paths()
    sha=hashlib.sha256()
    missing=[]
    file_hashes={}
    for path in paths:
        if not _safe_file(path):
            missing.append(path.name)
            continue
        if path.stat().st_size>2_000_000:
            raise ReadinessError("SOURCE_TOO_LARGE")
        data=path.read_bytes()
        try:
            identity=path.relative_to(ROOT).as_posix()
        except ValueError:
            # Disposable test fixtures may live outside the project.
            identity=path.name
        digest=hashlib.sha256(data).hexdigest()
        file_hashes[identity]=digest
        sha.update(identity.encode("utf-8"))
        sha.update(b"\0")
        sha.update(bytes.fromhex(digest))
    return {"source_fingerprint_sha256":sha.hexdigest(),
            "source_files_checked":len(paths)-len(missing),
            "missing":sorted(missing),
            "_file_hashes":file_hashes}


def public_source(record:dict)->dict:
    """Keep internal hashes available for accurate drift diagnosis only."""
    return {key:value for key,value in record.items() if key!="_file_hashes"}


def changed_source_paths(before:dict,after:dict)->list[str]:
    left=before.get("_file_hashes",{})
    right=after.get("_file_hashes",{})
    return sorted(key for key in left.keys()|right.keys()
                  if left.get(key)!=right.get(key))[:30]


def test_case(name:str,cwd:Path,args:tuple,timeout:int=75)->dict:
    started=time.monotonic()
    if not cwd.is_dir() or cwd.is_symlink():
        return {"name":name,"status":"UNAVAILABLE","tests":None,
                "skipped":None,"elapsed_seconds":0}
    try:
        env=os.environ.copy()
        # The kernel's original tests have two absolute imports from their
        # own folder. Set a narrow fixed PYTHONPATH instead of weakening
        # test discovery or modifying those accepted test modules.
        if name=="broker-os-ipc-supervisor":
            env["PYTHONPATH"]=os.pathsep.join(
                (str(ROOT),str(ROOT/"experiments"/"foundation_kernel")))
        completed=subprocess.run([sys.executable,*args],cwd=cwd,
                  stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,
                  stderr=subprocess.PIPE,shell=False,timeout=timeout,
                  check=False,env=env)
        data=(completed.stdout[-8192:]+b"\n"+completed.stderr[-8192:]).decode(
            "utf-8","replace")
        match=re.search(r"Ran (\d+) tests? in",data)
        skipped=re.search(r"skipped=(\d+)",data)
        if completed.returncode==0 and name in (
                "normative-crosswalk","approved-openspec-strict"):
            try:
                obj=json.loads(completed.stdout.decode("utf-8"))
                if name=="normative-crosswalk":
                    ok=(obj.get("ok") is True and obj.get("errors")==[])
                else:
                    body=obj.get("data") or {}
                    ok=(obj.get("ok") is True
                        and body.get("validated") is True
                        and (body.get("sdd") or {}).get("validated") is True)
            except (ValueError,UnicodeError,TypeError):
                ok=False
        else:
            count=int(match.group(1)) if match else 0
            ok=(completed.returncode==0 and "OK" in data
                and match is not None
                and count>=MIN_TESTS.get(name,1))
        return {"name":name,"status":"PASS" if ok else "FAIL",
                "tests":int(match.group(1)) if match else None,
                "skipped":int(skipped.group(1)) if skipped else 0,
                "elapsed_seconds":round(time.monotonic()-started,2),
                "exit_code":completed.returncode,
                "output_sha256":hashlib.sha256(completed.stdout+completed.stderr).hexdigest()}
    except subprocess.TimeoutExpired:
        return {"name":name,"status":"TIMEOUT","tests":None,
                "skipped":None,"elapsed_seconds":round(time.monotonic()-started,2)}
    except OSError:
        return {"name":name,"status":"UNAVAILABLE","tests":None,
                "skipped":None,"elapsed_seconds":round(time.monotonic()-started,2)}


def live_health()->dict:
    """Observe only a bounded loopback health response; never call MCP tools."""
    try:
        with urlopen("http://127.0.0.1:8765/healthz",timeout=2) as response:
            raw=response.read(2049)
            if len(raw)>2048:
                raise ReadinessError("HEALTH_RESPONSE_OVERSIZED")
            obj=json.loads(raw)
            count=obj.get("tools")
            if (type(count) is not int or count<0 or count>256
                or obj.get("status")!="ok"):
                raise ReadinessError("HEALTH_RESPONSE_INVALID")
            return {"reachable":True,"tool_count":count,
                    "privileged_tool_surface_possible":count>6,
                    "verified_host_identity":False}
    except (OSError,ValueError,TypeError):
        return {"reachable":False,"tool_count":None,
                "privileged_tool_surface_possible":None,
                "verified_host_identity":False}


def queue_counts(root=ROOT)->dict:
    """Observe status totals only; no payloads, prompts or lease tokens."""
    database=root/".agent-team"/"team.sqlite3"
    if not _safe_file(database):
        return {"available":False,"counts":{}}
    try:
        with closing(sqlite3.connect(database.resolve().as_uri()+"?mode=ro",
                                     uri=True,timeout=2)) as db:
            db.execute("PRAGMA query_only=ON")
            rows=db.execute("SELECT status,COUNT(*) FROM tasks GROUP BY status").fetchall()
            if len(rows)>16:
                raise ReadinessError("QUEUE_STATUS_COUNT_INVALID")
            return {"available":True,"counts":{
                str(status):int(amount) for status,amount in rows
                if type(status) is str and type(amount) is int}}
    except (sqlite3.Error,ReadinessError):
        return {"available":False,"counts":{}}


def classify(source:dict,health:dict,queue:dict,checks:list)->dict:
    """Fail closed on missing/partial suites; never infer passing QA from no QA."""
    required={name for name,_,_ in SUITES}
    observed=[c.get("name") for c in checks if type(c) is dict]
    statuses={c.get("name"):c.get("status") for c in checks if type(c) is dict}
    complete=(len(observed)==len(required) and set(observed)==required)
    if not checks:
        offline_status="NOT_RUN"
    elif complete and all(statuses[name]=="PASS" for name in required):
        offline_status="PASS"
    else:
        offline_status="FAIL"
    bad=[c.get("name") for c in checks
         if type(c) is not dict or c.get("status")!="PASS"]
    blockers=[]
    if source["missing"]:blockers.append("SOURCE_MIRROR_INCOMPLETE")
    if offline_status=="NOT_RUN":
        blockers.append("OFFLINE_REGRESSION_NOT_RUN")
    elif offline_status=="FAIL":
        blockers.append("OFFLINE_REGRESSION_FAILED")
    if not health["reachable"]:blockers.append("LIVE_MCP_HEALTH_UNAVAILABLE")
    elif health["privileged_tool_surface_possible"]:
        blockers.append("LIVE_MCP_PRIVILEGED_INTERFACE_EXPOSED")
    if not queue["available"]:blockers.append("QUEUE_OBSERVATION_UNAVAILABLE")
    blockers.extend([
        "BROKER_SEPARATE_WINDOWS_PRINCIPAL_UNVERIFIED",
        "HOST_TRUSTED_GRANT_AUTHORIZATION_NOT_DEPLOYED",
        "NO_REAL_MULTI_SID_NEGATIVE_E2E",
    ])
    return {
        "offline_regression_status":offline_status,
        "offline_regression_passed":offline_status=="PASS" and not source["missing"],
        "read_only_advisory":True,
        "trusted_host_actor_authenticated":False,
        "os_separation_verified":False,
        "auto_merge_authorized":False,
        "auto_provision_or_deploy_authorized":False,
        "p085_release_ready":False,
        "blockers":blockers,
        "next_safe_lane": (
            "REPAIR_OFFLINE_NEGATIVE_TESTS" if offline_status=="FAIL" else
            "RUN_REQUIRED_OFFLINE_TESTS" if offline_status=="NOT_RUN" else
            "VERIFY_WINDOWS_BROKER_ISOLATION_AND_ISSUER"
        ),
    }


def report_once(*,run_tests=True,root=ROOT)->dict:
    start=datetime.now(timezone.utc)
    source=source_digest() if root==ROOT else source_digest(tuple(root/p.relative_to(ROOT) for p in REQUIRED))
    tests=[test_case(*item) for item in SUITES] if run_tests else []
    end_source=source_digest() if root==ROOT else source_digest(tuple(root/p.relative_to(ROOT) for p in REQUIRED))
    if end_source!=source:
        tests.append({"name":"source-stability","status":"FAIL",
                      "tests":None,"skipped":None,"elapsed_seconds":0})
    health=live_health()
    queue=queue_counts(root)
    gate=classify(end_source,health,queue,tests)
    try:
        from p085_windows_deploy_preflight import inspect as windows_inspect
        deployment=windows_inspect()
        if (type(deployment) is not dict or deployment.get("ok") is not True
            or deployment.get("safe_to_activate") is not False):
            raise ReadinessError("WINDOWS_DEPLOYMENT_REPORT_UNTRUSTED")
        safe_codes=set(str(x) for x in deployment.get("blockers",())
                       if type(x) is str and re.fullmatch(r"[A-Z0-9_]{4,80}",x))
        for code in sorted(safe_codes):
            if code not in gate["blockers"]:
                gate["blockers"].append(code)
    except (OSError,ValueError,ImportError,AttributeError):
        deployment={"ok":False,"read_only":True,"safe_to_activate":False,
                    "blockers":["WINDOWS_DEPLOYMENT_PREFLIGHT_UNAVAILABLE"]}
        gate["blockers"].append("WINDOWS_DEPLOYMENT_PREFLIGHT_UNAVAILABLE")
    return {
        "schema":SCHEMA,"started_at_utc":start.isoformat(),
        "finished_at_utc":datetime.now(timezone.utc).isoformat(),
        "source":public_source(end_source),
        "source_stable":end_source==source,
        "changed_source_paths":changed_source_paths(source,end_source),
        "live_host":health,"queue":queue,"checks":tests,"gate":gate,
        "windows_deployment":deployment,
        "mode":"TESTS_AND_READ_ONLY_DIAGNOSIS" if run_tests else "READ_ONLY_DIAGNOSIS",
        "live_mutation_performed":False,"owner_approval_inferred":False,
    }


def previous_report(dest=REPORTS)->dict|None:
    """Read only the last bounded, non-secret report; never trust its claims."""
    path=Path(dest)/"P085_auto_latest.json"
    if not _safe_file(path) or path.stat().st_size>MAX_REPORT_BYTES:
        return None
    try:
        obj=json.loads(path.read_text(encoding="ascii"))
        return obj if (type(obj) is dict and obj.get("schema")==SCHEMA) else None
    except (OSError,UnicodeError,ValueError):
        return None


def status_delta(old:dict|None,new:dict)->dict:
    """Changes in safety checks, not changes in routine queue quantities."""
    if old is None:
        return {"baseline_found":False,"risk_changed":True,
                "new_blockers":list(new["gate"]["blockers"]),
                "resolved_blockers":[],"changed_test_suites":[]}
    before=set(old.get("gate",{}).get("blockers",()))
    after=set(new["gate"]["blockers"])
    existing={r.get("name"):r.get("status") for r in old.get("checks",())
              if isinstance(r,dict)}
    changed=[r["name"] for r in new["checks"]
             if existing.get(r["name"])!=r.get("status")]
    changed_source=(old.get("source",{}).get("source_fingerprint_sha256")!=
                    new["source"].get("source_fingerprint_sha256"))
    return {"baseline_found":True,
            "risk_changed":bool(before!=after or changed or changed_source),
            "source_changed":changed_source,
            "new_blockers":sorted(after-before),
            "resolved_blockers":sorted(before-after),
            "changed_test_suites":changed}


def persist(report:dict,dest=REPORTS)->Path:
    """Optional reports only; safe atomic replace, no SQLite writes."""
    dest=_audit_directory(dest)
    raw=(json.dumps(report,sort_keys=True,ensure_ascii=True,
                    separators=(",",":"))+"\n").encode("ascii")
    if len(raw)>MAX_REPORT_BYTES:raise ReadinessError("REPORT_TOO_LARGE")
    unique=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")+"_"+uuid.uuid4().hex[:12]
    history=dest/("P085_auto_"+unique+".json")
    with history.open("xb") as f:
        f.write(raw)
        f.flush()
        os.fsync(f.fileno())
    temp=dest/(".P085_latest_"+uuid.uuid4().hex+".tmp")
    try:
        with temp.open("xb") as f:
            f.write(raw)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp,dest/"P085_auto_latest.json")
    finally:
        if temp.exists():temp.unlink()
    return history


def stage_candidate_if_safe(report:dict)->dict:
    """Package keyless review input only after complete and stable QA.

    The candidate is user-writable, unsigned and NOT a trusted release
    source. No protected Windows path, account, host or approval is modified.
    """
    if not report["source_stable"] or not report["gate"]["offline_regression_passed"]:
        return {"status":"NOT_STAGED_UNVERIFIED_QA",
                "release_approved":False,"os_acl_verified":False}
    from p085_broker_release_candidate import build_candidate, BundleRejected
    try:
        result=build_candidate()
        if public_source(source_digest())!=report["source"]:
            raise ReadinessError("CANDIDATE_SOURCE_DRIFT")
        return {"status":"STAGED_FOR_INDEPENDENT_REVIEW",
                "candidate_id":result["candidate_id"],
                "source_file_count":result["file_count"],
                "created":result["created"],
                "release_approved":False,"os_acl_verified":False}
    except (BundleRejected, OSError):
        return {"status":"STAGING_FAILED",
                "release_approved":False,"os_acl_verified":False}


def main(argv=None)->int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write-report",action="store_true",
                        help="Write nonsecret diagnostics under .agent-team/reports")
    parser.add_argument("--quick",action="store_true",
                        help="Do not run tests; health and queue observation only")
    args=parser.parse_args(argv)
    try:
        # A quick health snapshot is not evidence of passed tests. Never
        # let it replace the canonical latest full-QA baseline.
        if args.quick and args.write_report:
            raise ReadinessError("QUICK_REPORT_CANNOT_REPLACE_FULL_AUDIT")
        # Lock the entire observation→comparison→publish transaction.
        # A second scheduled run exits without overwriting a newer report.
        with (single_audit() if args.write_report else nullcontext()):
            old=previous_report()
            result=report_once(run_tests=not args.quick)
            if args.write_report:
                result["release_candidate"]=stage_candidate_if_safe(result)
                if result["release_candidate"]["status"]=="STAGING_FAILED":
                    result["gate"]["blockers"].append(
                        "RELEASE_CANDIDATE_STAGING_FAILED")
                # A candidate never authorizes release, regardless of tests.
                result["gate"]["p085_release_ready"]=False
            result["delta"]=status_delta(old,result)
            if args.write_report:
                result["report_file"]=str(persist(result).relative_to(ROOT))
        print(json.dumps({"ok":True,"data":result},ensure_ascii=True,
                         separators=(",",":")))
        # A quick diagnostics-only pass is successful observation, NEVER
        # a passing offline regression or a trusted release approval.
        candidate=result.get("release_candidate",{})
        return 0 if (args.quick or
                     (result["gate"]["offline_regression_passed"] and
                      candidate.get("status")!="STAGING_FAILED")) else 2
    except (ReadinessError,OSError,ValueError) as exc:
        # Report stable internal diagnostic codes, never OS paths, process
        # output, access tokens, policy bytes or user-supplied exception text.
        code=str(exc) if type(exc) is ReadinessError else ""
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{2,79}",code):
            code="READINESS_CHECK_FAILED"
        print(json.dumps({"ok":False,"error":code},ensure_ascii=True))
        return 2

if __name__=="__main__":
    raise SystemExit(main())

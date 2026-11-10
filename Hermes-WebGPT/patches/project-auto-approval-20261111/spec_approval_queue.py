"""Durable per-operation review queue for OpenSpec/SDD creation.

A human approves each plan or explicitly enables a project-scoped policy from
the local interactive terminal. No MCP argument can enable that policy.
NOT a complete OS privilege boundary while unrestricted run_script exists.
"""
from contextlib import closing
import hashlib
import json
import math
import os
import secrets
import sqlite3
import sys
import time
from pathlib import Path
import spec_tools as spec

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATABASE = Path(__file__).resolve().parent / ".local-state" / "spec_requests.sqlite3"
MAX_AGE_SEC = 900
MAX_ACTIVE_REQUESTS = 100
RECONCILE_AFTER_SEC = 300

class QueueError(Exception):
    def __init__(self, code, message, retryable=False):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class ApprovalQueue:
    def __init__(self, db_path=DEFAULT_DATABASE, root=ROOT, clock=None):
        self.db_path = Path(db_path)
        self.root = Path(root).resolve()
        self.clock = clock or time.time
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS spec_requests (
                    request_id TEXT PRIMARY KEY,
                    manifest_path TEXT NOT NULL,
                    manifest_sha256 TEXT NOT NULL,
                    project_path TEXT NOT NULL,
                    change_name TEXT NOT NULL,
                    plan_sha256 TEXT NOT NULL,
                    state TEXT NOT NULL CHECK (
                       state IN ('pending','approved','denied','running','completed','failed')
                    ),
                    created_at REAL NOT NULL,
                    expires_at REAL NOT NULL,
                    decision_at REAL,
                    running_at REAL,
                    result_json TEXT,
                    artifact_json TEXT
                )
            """)
            cols={row[1] for row in db.execute("PRAGMA table_info(spec_requests)")}
            if "running_at" not in cols:
                db.execute("ALTER TABLE spec_requests ADD COLUMN running_at REAL")
            if "artifact_json" not in cols:
                db.execute("ALTER TABLE spec_requests ADD COLUMN artifact_json TEXT")
            db.executescript('''
                CREATE TABLE IF NOT EXISTS spec_project_policies (
                    project_path TEXT PRIMARY KEY, grant_id TEXT NOT NULL,
                    enabled INTEGER NOT NULL CHECK(enabled IN (0,1)),
                    created_at REAL NOT NULL, expires_at REAL,
                    device TEXT NOT NULL, inode TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS spec_request_project_policy (
                    request_id TEXT PRIMARY KEY, project_path TEXT NOT NULL,
                    grant_id TEXT NOT NULL, device TEXT NOT NULL, inode TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS spec_project_policy_audit (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL, project_path TEXT NOT NULL,
                    action TEXT NOT NULL, grant_id TEXT, request_id TEXT
                );
            ''')

    def _project_target(self, relative):
        project = spec.checked_path(relative, self.root, directory=True)
        # Reject aliases even when they resolve back inside the workspace.
        path = self.root
        for part in relative.split('/'):
            path = path / part
            if path.is_symlink() or getattr(path, 'is_junction', lambda: False)():
                raise QueueError('PROJECT_ALIAS_DENIED', 'Project aliases are not eligible for auto-approval')
        stamp = project.stat()
        if not stamp.st_ino:
            raise QueueError('PROJECT_IDENTITY_UNAVAILABLE', 'Stable directory identity is required')
        return project, os.path.normcase(str(project)), str(stamp.st_dev), str(stamp.st_ino)

    def _policy(self, db, project, relative=None):
        try:
            relative = relative or Path(project).relative_to(self.root).as_posix()
            target, key, device, inode = self._project_target(relative)
            if target != Path(project):
                return None
        except (OSError, ValueError, QueueError, spec.SpecError):
            return None
        row = db.execute('SELECT grant_id,enabled,created_at,expires_at,device,inode FROM spec_project_policies WHERE project_path=?', (key,)).fetchone()
        if not row or row[1] != 1 or row[2] > self.clock() or (row[3] is not None and self.clock() >= row[3]):
            return None
        if (row[4], row[5]) != (device, inode):
            return None
        return {'project_path': key, 'grant_id': row[0], 'device': device, 'inode': inode}

    def _binding(self, db, request_id):
        row = db.execute('SELECT project_path,grant_id,device,inode FROM spec_request_project_policy WHERE request_id=?', (request_id,)).fetchone()
        return dict(zip(('project_path','grant_id','device','inode'), row)) if row else None

    def _require_project_policy(self, db, row):
        binding = self._binding(db, row['request_id'])
        if not binding:
            return False
        policy = self._policy(db, Path(row['project_path']))
        if not policy or policy != binding:
            raise QueueError('PROJECT_APPROVAL_REVOKED', 'Project policy expired, was revoked, or the directory changed')
        return True

    def _audit_policy(self, db, project, action, grant_id=None, request_id=None):
        db.execute('INSERT INTO spec_project_policy_audit(timestamp,project_path,action,grant_id,request_id) VALUES(?,?,?,?,?)',
                   (self.clock(), project, action, grant_id, request_id))

    def project_policy_status(self, project):
        target, key, _, _ = self._project_target(project)
        with closing(self._connect()) as db:
            row = db.execute('SELECT grant_id,enabled,created_at,expires_at FROM spec_project_policies WHERE project_path=?', (key,)).fetchone()
            active = self._policy(db, target)
        return {'project': target.relative_to(self.root).as_posix(),
                'operation': 'spec_create', 'enabled': active is not None,
                'configured': row is not None, 'grant_id': row[0] if row else None,
                'expires_at': row[3] if row else None}

    def project_policy_console(self, project, action, *, hours=None, permanent=False, stdin=None, stdout=None):
        ins = sys.stdin if stdin is None else stdin
        out = sys.stdout if stdout is None else stdout
        if not getattr(ins, 'isatty', lambda: False)() or not getattr(out, 'isatty', lambda: False)():
            raise QueueError('INTERACTIVE_REQUIRED', 'Changing project auto-approval requires the owner local terminal')
        if action not in ('enable', 'disable') or type(permanent) is not bool:
            raise QueueError('POLICY_ACTION_INVALID', 'Select enable or disable')
        if action == 'disable' and (hours is not None or permanent):
            raise QueueError('POLICY_EXPIRY_INVALID', 'Disable does not take expiry options')
        if permanent and hours is not None:
            raise QueueError('POLICY_EXPIRY_INVALID', 'Choose a duration or permanent mode')
        if not permanent and action == 'enable':
            hours = 24 if hours is None else hours
            if type(hours) not in (float, int) or not math.isfinite(hours) or not 0 < hours <= 8760:
                raise QueueError('POLICY_EXPIRY_INVALID', 'Duration must be positive and at most one year')
        target, key, device, inode = self._project_target(project)
        name = target.relative_to(self.root).as_posix()
        phrase = action.upper() + ' ' + name
        out.write('PROJECT AUTO-APPROVAL — OpenSpec/SDD creation only\n')
        out.write('Project: ' + str(target) + '\n')
        out.write('Existing files, plan hashes, request expiry and reconciliation guards remain enforced.\n')
        if action == 'enable':
            out.write('Duration: ' + ('permanent until revoked' if permanent else str(hours) + ' hours') + '\n')
        out.write('Type exactly ' + phrase + ': ')
        out.flush()
        if ins.readline().strip() != phrase:
            return {'project': name, 'changed': False, 'reason': 'owner_declined'}
        with closing(self._connect()) as db:
            db.execute('BEGIN IMMEDIATE')
            if self._project_target(project) != (target, key, device, inode):
                db.execute('ROLLBACK')
                raise QueueError('PROJECT_CHANGED', 'Project changed while the owner reviewed the policy')
            now = self.clock()
            if action == 'enable':
                grant_id = secrets.token_hex(16)
                expiry = None if permanent else now + hours * 3600
                db.execute('''INSERT INTO spec_project_policies VALUES(?,?,1,?,?,?,?)
                    ON CONFLICT(project_path) DO UPDATE SET grant_id=excluded.grant_id,
                    enabled=1,created_at=excluded.created_at,expires_at=excluded.expires_at,
                    device=excluded.device,inode=excluded.inode''',
                    (key, grant_id, now, expiry, device, inode))
            else:
                previous = db.execute('SELECT grant_id FROM spec_project_policies WHERE project_path=?', (key,)).fetchone()
                grant_id = previous[0] if previous else None
                db.execute('UPDATE spec_project_policies SET enabled=0 WHERE project_path=?', (key,))
            self._audit_policy(db, key, action, grant_id)
            db.execute('COMMIT')
        return {**self.project_policy_status(project), 'changed': True}

    def _connect(self):
        db=sqlite3.connect(str(self.db_path), timeout=8, isolation_level=None)
        db.execute("PRAGMA busy_timeout=8000")
        return db

    def _row(self, db, request_id):
        if not isinstance(request_id, str) or len(request_id) != 32 or any(c not in "0123456789abcdef" for c in request_id):
            raise QueueError("REQUEST_ID_INVALID", "Invalid request ID")
        row=db.execute("""
          SELECT request_id,manifest_path,manifest_sha256,project_path,change_name,
                 plan_sha256,state,created_at,expires_at,decision_at,running_at,result_json,artifact_json
          FROM spec_requests WHERE request_id=?
        """,(request_id,)).fetchone()
        if row is None:
            raise QueueError("REQUEST_NOT_FOUND", "Approval request was not found")
        cols=("request_id","manifest_path","manifest_sha256","project_path",
              "change_name","plan_sha256","state","created_at","expires_at",
              "decision_at","running_at","result_json","artifact_json")
        return dict(zip(cols,row))

    def _manifest(self, manifest_path):
        file=spec.checked_path(manifest_path, self.root)
        digest=hashlib.sha256(file.read_bytes()).hexdigest()
        manifest=spec.read_manifest(manifest_path, self.root)
        project,files,plan_hash=spec.plan(manifest,self.root)
        return digest, manifest, project,files,plan_hash

    def prepare(self, manifest_path, expected_plan_hash):
        if not isinstance(expected_plan_hash,str) or len(expected_plan_hash)!=64:
            raise QueueError("PLAN_HASH_INVALID", "Expected SHA-256 is required")
        manifest_hash,brief,project,files,digest=self._manifest(manifest_path)
        if not secrets.compare_digest(digest,expected_plan_hash):
            raise QueueError("PREVIEW_CHANGED", "Request a fresh preview before approval")
        artifact_hashes={path:hashlib.sha256(content.encode("utf-8")).hexdigest()
                         for path,content in files.items()}
        artifact_json=json.dumps(artifact_hashes,sort_keys=True,ensure_ascii=True)
        now=self.clock()
        request_id=secrets.token_hex(16)
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            active=db.execute("""SELECT COUNT(*) FROM spec_requests WHERE
                (state IN (\'pending\',\'approved\') AND expires_at>?) OR state=\'running\'""", (now,)).fetchone()[0]
            if active>=MAX_ACTIVE_REQUESTS:
                db.execute("ROLLBACK")
                raise QueueError("QUEUE_FULL","Too many active requests; let approvals expire or resolve running jobs")
            db.execute("""
              INSERT INTO spec_requests(request_id,manifest_path,manifest_sha256,
                project_path,change_name,plan_sha256,state,created_at,expires_at,artifact_json)
              VALUES(?,?,?,?,?,?,'pending',?,?,?)
            """,(request_id,manifest_path,manifest_hash,str(project),brief["change"],
                  digest,now,now+MAX_AGE_SEC,artifact_json))
            policy = self._policy(db, project, brief['project'])
            if policy:
                # Keep the stored state pending: reverting to old broker code
                # must fail closed instead of mistaking this for human approval.
                db.execute('INSERT INTO spec_request_project_policy VALUES(?,?,?,?,?)',
                           (request_id, policy['project_path'], policy['grant_id'], policy['device'], policy['inode']))
                self._audit_policy(db, policy['project_path'], 'auto_scope_bound', policy['grant_id'], request_id)
            db.execute("COMMIT")
        return {"request_id":request_id,"state":"approved" if policy else "pending","project":project.relative_to(self.root).as_posix(),
                "change":brief["change"],"plan_sha256":digest,"expires_at":now+MAX_AGE_SEC,
                "file_count":len(files),"requirement_count":sum(len(c["requirements"]) for c in brief["capabilities"]),
                "approval_source":"project_policy" if policy else "manual",
                "approval_method":"Owner-enabled project policy" if policy else "Interactive local terminal only; run owner_approve.py REQUEST_ID"}

    def status(self, request_id):
        with closing(self._connect()) as db:
            row=self._row(db,request_id)
            binding = self._binding(db, request_id)
            policy_active = binding is not None and self._policy(db, Path(row['project_path'])) == binding
        status=row["state"]
        if status == 'pending' and binding:
            status = 'approved' if policy_active else 'revoked'
        if status in ("pending","approved","revoked") and self.clock() >= row["expires_at"]:
            status="expired"
        if status=="running" and (row["running_at"] is None or self.clock()-row["running_at"]>=RECONCILE_AFTER_SEC):
            status="reconciliation_required"
        out={"request_id":row["request_id"],"project":Path(row["project_path"]).relative_to(self.root).as_posix(),
             "change":row["change_name"],"state":status,"expires_at":row["expires_at"]}
        if binding:
            out['approval_source'] = 'project_policy'
        if row["state"]=="completed":
            out["result"]=json.loads(row["result_json"])
        if row["state"]=="failed":
            out["error_code"]=(json.loads(row["result_json"]).get("code","CREATE_FAILED")
                               if row["result_json"] else "CREATE_FAILED")
        return out

    def diagnose(self, request_id):
        """Read-only recovery report; never finalize, delete, or retry an operation."""
        with closing(self._connect()) as db:
            row=self._row(db,request_id)
        state=self.status(request_id)["state"]
        if not row["artifact_json"]:
            return {"request_id":request_id,"state":state,"available":False,
                    "safe_to_retry":False,"reason":"Legacy request has no stored artifact fingerprints"}
        project=Path(row["project_path"])
        if (project.resolve()!=project or not project.is_relative_to(self.root)
                or project==self.root):
            raise QueueError("PROJECT_INVALID","Approved project path has been redirected")
        try:
            expected=json.loads(row["artifact_json"])
        except (TypeError,ValueError):
            raise QueueError("ARTIFACT_MANIFEST_INVALID","Stored artifact manifest is invalid")
        if not isinstance(expected,dict) or len(expected)>200:
            raise QueueError("ARTIFACT_MANIFEST_INVALID","Too many or missing artifact entries")
        counts={"matching":0,"missing":0,"changed":0,"unsafe":0}
        samples={k:[] for k in counts}
        for relative,wanted in expected.items():
            if (not isinstance(relative,str) or
                not isinstance(wanted,str) or len(wanted)!=64 or
                any(x in (".","..") for x in relative.split("/"))):
                raise QueueError("ARTIFACT_MANIFEST_INVALID","Invalid stored artifact entry")
            path=project / relative
            try:
                if path.is_symlink() or not path.resolve().is_relative_to(project):
                    kind="unsafe"
                elif not path.exists():
                    kind="missing"
                elif not path.is_file() or path.stat().st_size>2*1024*1024:
                    kind="changed"
                else:
                    actual=hashlib.sha256(path.read_bytes()).hexdigest()
                    kind="matching" if secrets.compare_digest(actual,wanted) else "changed"
            except OSError:
                kind="unsafe"
            counts[kind]+=1
            if len(samples[kind])<12:
                samples[kind].append(relative)
        return {"request_id":request_id,"state":state,"available":True,
                "expected_files":len(expected),"counts":counts,"sample_paths":samples,
                "all_files_match":counts["matching"]==len(expected),
                "safe_to_retry":False,
                "action":"Review evidence manually. Never automatically re-run or delete files."}

    def reconcile_console(self, request_id, *, stdin=None, stdout=None):
        """Finalize a stale crash only if every generated artifact is unchanged.

        Reconciliation updates the SQLite ledger, not project files. It never
        starts generation, restores missing files or deletes anything.
        """
        ins = sys.stdin if stdin is None else stdin
        out = sys.stdout if stdout is None else stdout
        if not getattr(ins, "isatty", lambda: False)() or not getattr(out, "isatty", lambda: False)():
            raise QueueError("INTERACTIVE_REQUIRED", "Reconciliation requires a local interactive terminal")
        with closing(self._connect()) as db:
            row = self._row(db, request_id)
        if row["state"] != "running":
            raise QueueError("RECONCILIATION_DENIED", "Only running requests can be reconciled")
        if row["running_at"] is None or self.clock() - row["running_at"] < RECONCILE_AFTER_SEC:
            raise QueueError("RECONCILIATION_DENIED", "Execution is not stale enough to reconcile")
        report = self.diagnose(request_id)
        if not report["available"] or not report["all_files_match"] or report["expected_files"] == 0:
            raise QueueError("ARTIFACTS_INCOMPLETE", "Some approved artifacts are absent, changed or unsafe")
        project = Path(row["project_path"])
        try:
            verified = spec.validate(project, row["change_name"])
        except (spec.SpecError, OSError):
            raise QueueError("VALIDATION_FAILED", "OpenSpec/SDD strict validation must succeed before reconciliation") from None
        if not verified.get("validated") or not verified.get("sdd", {}).get("validated"):
            raise QueueError("VALIDATION_FAILED", "Generated SDD crosswalk was not verified")
        expected = json.loads(row["artifact_json"])
        out.write("\nLOCAL RECOVERY REVIEW — NO PROJECT FILES WILL BE MODIFIED\n")
        out.write(f"Project: {project}\nChange: {row['change_name']}\n")
        out.write(f"Plan SHA-256: {row['plan_sha256']}\n")
        out.write(f"Verified files: {len(expected)}; OpenSpec and SDD checks: PASS\n")
        out.write("Type exactly RECONCILE " + request_id[:8] + " to finalize the ledger: ")
        out.flush()
        if ins.readline().strip() != "RECONCILE " + request_id[:8]:
            raise QueueError("RECONCILIATION_REJECTED", "User did not approve reconciliation")
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            latest = self._row(db, request_id)
            if (latest["state"] != "running" or
                latest["running_at"] is None or
                self.clock() - latest["running_at"] < RECONCILE_AFTER_SEC or
                latest["artifact_json"] != row["artifact_json"]):
                db.execute("ROLLBACK")
                raise QueueError("RECONCILIATION_CONFLICT", "Request state changed during review")
            # Check all file hashes again after the human confirmation, before commit.
            updated = self.diagnose(request_id)
            if not updated["all_files_match"]:
                db.execute("ROLLBACK")
                raise QueueError("ARTIFACTS_INCOMPLETE", "Files changed while approval was pending")
            result = {
                "created": True,
                "replayed": False,
                "reconciled": True,
                "project": project.relative_to(self.root).as_posix(),
                "change": row["change_name"],
                "files": sorted(expected),
                "upstream": verified,
            }
            cursor = db.execute(
                "UPDATE spec_requests SET state='completed',result_json=? "
                "WHERE request_id=? AND state='running'",
                (json.dumps(result, ensure_ascii=True), request_id),
            )
            if cursor.rowcount != 1:
                db.execute("ROLLBACK")
                raise QueueError("RECONCILIATION_CONFLICT", "Request changed before finalization")
            db.execute("COMMIT")
        return {"request_id": request_id, "state": "completed",
                "reconciled": True, "files": len(expected)}

    def review_console(self, request_id, *, stdin=None, stdout=None):
        ins=sys.stdin if stdin is None else stdin
        out=sys.stdout if stdout is None else stdout
        if not getattr(ins,"isatty",lambda:False)() or not getattr(out,"isatty",lambda:False)():
            raise QueueError("INTERACTIVE_REQUIRED", "Approval requires a local interactive terminal")
        with closing(self._connect()) as db:
            row=self._row(db,request_id)
        if row["state"]!="pending" or self.clock()>=row["expires_at"]:
            raise QueueError("APPROVAL_INVALID", "This request is no longer awaiting approval")
        try:
            manifest_hash,brief,project,files,current_hash=self._manifest(row["manifest_path"])
        except (spec.SpecError,OSError):
            raise QueueError("PREVIEW_CHANGED","Manifest or project changed since preview") from None
        if (manifest_hash!=row["manifest_sha256"] or
            str(project)!=row["project_path"] or current_hash!=row["plan_sha256"]):
            raise QueueError("PREVIEW_CHANGED","Manifest or target changed since preview")
        out.write("\nLocal Workspace OpenSpec + SDD APPROVAL\n")
        out.write(f"Project: {row['project_path']}\nChange: {row['change_name']}\n")
        out.write(f"SHA-256: {current_hash}\nTarget files:\n")
        for rel in files:
            out.write("  + "+rel+"\n")
        out.write("\nType exactly APPROVE "+request_id[:8]+" to accept, otherwise deny: ")
        out.flush()
        choice=ins.readline().strip()
        now=self.clock()
        state="approved" if choice=="APPROVE "+request_id[:8] else "denied"
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            latest=self._row(db,request_id)
            if latest["state"]!="pending" or now>=latest["expires_at"]:
                db.execute("ROLLBACK")
                raise QueueError("APPROVAL_INVALID","Approval changed or expired")
            db.execute("UPDATE spec_requests SET state=?,decision_at=? WHERE request_id=?",
                      (state,now,request_id))
            # An explicit human decision supersedes project auto-approval.
            db.execute('DELETE FROM spec_request_project_policy WHERE request_id=?', (request_id,))
            db.execute("COMMIT")
        return {"request_id":request_id,"state":state,"project":project.relative_to(self.root).as_posix()}

    def execute(self, request_id):
        # Validate everything BEFORE consuming. No automatic second attempt on failure.
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            row=self._row(db,request_id)
            if row["state"]=="completed":
                result=json.loads(row["result_json"])
                db.execute("COMMIT")
                return {**result, "replayed":True}
            if row["state"]=="running":
                db.execute("ROLLBACK")
                if row["running_at"] is None or self.clock()-row["running_at"]>=RECONCILE_AFTER_SEC:
                    raise QueueError("RECONCILIATION_REQUIRED","Stale run: inspect outputs; never retry writes automatically")
                raise QueueError("IN_PROGRESS","An execution is in progress; check status",retryable=True)
            auto = row['state'] == 'pending' and self._require_project_policy(db, row)
            if row["state"]!="approved" and not auto:
                db.execute("ROLLBACK")
                raise QueueError("APPROVAL_REQUIRED","Local terminal approval is missing or consumed")
            if self.clock()>=row["expires_at"]:
                db.execute("ROLLBACK")
                raise QueueError("APPROVAL_EXPIRED","Local terminal approval expired")
            try:
                current_manifest,brief,project,files,current_plan=self._manifest(row["manifest_path"])
            except (spec.SpecError,OSError):
                db.execute("ROLLBACK")
                raise QueueError("PREVIEW_CHANGED","Manifest or target is no longer valid") from None
            if (current_manifest!=row["manifest_sha256"] or
                str(project)!=row["project_path"] or
                current_plan!=row["plan_sha256"]):
                db.execute("ROLLBACK")
                raise QueueError("PREVIEW_CHANGED","Plan changed since local terminal approval")
            db.execute("UPDATE spec_requests SET state='running',running_at=? WHERE request_id=?",(self.clock(),request_id))
            if auto:
                binding = self._binding(db, request_id)
                self._audit_policy(db, binding['project_path'], 'auto_execution_started', binding['grant_id'], request_id)
            db.execute("COMMIT")
        try:
            with closing(self._connect()) as db:
                binding = self._binding(db, request_id)
            if binding:
                target, key, device, inode = self._project_target(Path(row['project_path']).relative_to(self.root).as_posix())
                if (key, device, inode) != (binding['project_path'], binding['device'], binding['inode']):
                    raise QueueError('PROJECT_CHANGED', 'Project directory identity changed before creation')
            # Calls actual OpenSpec --strict and crosswalk checks. Fails closed.
            result=spec.create(brief,row["plan_sha256"],self.root)
            result={"created":True,"replayed":False,"change":brief["change"],
                    "project":project.relative_to(self.root).as_posix(),
                    "upstream":result["upstream"],"files":result["files"]}
        except Exception as e:
            with closing(self._connect()) as db:
                db.execute("UPDATE spec_requests SET state='failed',result_json=? WHERE request_id=? AND state='running'",
                           (json.dumps({"code":getattr(e,"code","CREATE_FAILED")}),request_id))
            raise QueueError("CREATE_FAILED","Creation failed; inspect files and request new approval") from None
        with closing(self._connect()) as db:
            db.execute("UPDATE spec_requests SET state='completed',result_json=? WHERE request_id=? AND state='running'",
                       (json.dumps(result,ensure_ascii=True),request_id))
        return result


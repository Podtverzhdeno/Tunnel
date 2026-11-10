"""Generate OpenSpec change + Spec Kit-style SDD artifacts from a typed JSON brief.

Use through Local Workspace run_script -> ops.py spec_preview/spec_create/spec_validate.
The manifest is authored/reviewed by the agent; this code does not call an LLM.
State changes require explicit --apply AND a matching preview hash. This flag
is not a trusted human approval; do not expose native writes until host policy.
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAX_MANIFEST = 128 * 1024
SLUG = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
ALLOWED_FIELDS = {"project", "change", "title", "problem", "outcome", "capabilities", "constraints"}
ACTIONS = {"spec_preview", "spec_create", "spec_validate", "spec_status",
           "spec_request_prepare", "spec_request_status", "spec_request_execute", "spec_request_diagnose", "spec_report"}


class SpecError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def short(value, label, minimum=3, maximum=600):
    if not isinstance(value, str):
        raise SpecError("INVALID_MANIFEST", f"{label} must be a string")
    value = " ".join(value.split())
    if not minimum <= len(value) <= maximum or any(ord(c) < 32 for c in value):
        raise SpecError("INVALID_MANIFEST", f"{label} length must be {minimum}..{maximum}")
    if re.search(r"(?i)(?:^|\s)(?:#{1,6}|~~~|---)(?:\s|$)", value):
        raise SpecError("INVALID_MANIFEST", f"{label} contains Markdown structure")
    return value


def slug(value, label="slug"):
    if not isinstance(value, str) or len(value) > 60 or not SLUG.fullmatch(value):
        raise SpecError("INVALID_MANIFEST", f"{label} must be kebab-case (max 60)")
    return value


def checked_path(relative, root=ROOT, directory=False):
    if not isinstance(relative, str) or not relative or relative.startswith(("/", "\\")):
        raise SpecError("PATH_INVALID", "Only workspace-relative paths are accepted")
    if "\\" in relative or ":" in relative or any(x in ("..", ".") for x in relative.split("/")):
        raise SpecError("PATH_INVALID", "Traversal, device paths and backslashes are forbidden")
    base = Path(root).resolve()
    target = (base / relative).resolve()
    if not target.is_relative_to(base):
        raise SpecError("PATH_DENIED", "Path escapes workspace")
    if directory:
        if not target.is_dir() or target == base:
            raise SpecError("PROJECT_INVALID", "Select a pre-existing child project")
    elif not target.is_file():
        raise SpecError("FILE_MISSING", "Manifest file not found")
    return target


def read_manifest(path, root=ROOT):
    f = checked_path(path, root)
    if f.suffix.lower() != ".json" or f.stat().st_size > MAX_MANIFEST:
        raise SpecError("INVALID_MANIFEST", "Manifest must be a JSON file <= 128 KiB")
    try:
        raw = f.read_text(encoding="utf-8-sig")
        obj = json.loads(raw)
    except (UnicodeError, ValueError):
        raise SpecError("INVALID_MANIFEST", "Manifest must contain valid UTF-8 JSON")
    return normalize(obj)


def normalize(value):
    if not isinstance(value, dict):
        raise SpecError("INVALID_MANIFEST", "Root JSON must be an object")
    unknown = set(value) - ALLOWED_FIELDS
    if unknown:
        raise SpecError("INVALID_MANIFEST", "Unrecognized fields: " + ", ".join(sorted(unknown)))
    project = value.get("project")
    if not isinstance(project, str) or len(project) > 160:
        raise SpecError("INVALID_MANIFEST", "project must be a path relative to workspace root")
    change = slug(value.get("change"), "change")
    title = short(value.get("title"), "title", 5, 120)
    problem = short(value.get("problem"), "problem", 20, 900)
    outcome = short(value.get("outcome"), "outcome", 20, 900)
    raw_caps = value.get("capabilities")
    if not isinstance(raw_caps, list) or not 1 <= len(raw_caps) <= 8:
        raise SpecError("INVALID_MANIFEST", "Provide 1..8 capabilities")
    caps = []
    seen = set()
    total = 0
    for index, item in enumerate(raw_caps):
        if not isinstance(item, dict) or set(item) - {"name", "summary", "requirements"}:
            raise SpecError("INVALID_MANIFEST", f"capability {index} has invalid fields")
        name = slug(item.get("name"), "capability name")
        if name in seen:
            raise SpecError("DUPLICATE", "Duplicate capability: " + name)
        seen.add(name)
        summary = short(item.get("summary"), "capability summary", 15, 550)
        reqs = item.get("requirements")
        if not isinstance(reqs, list) or not 1 <= len(reqs) <= 15:
            raise SpecError("INVALID_MANIFEST", "Each capability needs 1..15 requirements")
        clean_reqs = []
        labels = set()
        for item_req in reqs:
            if not isinstance(item_req, dict) or set(item_req) - {"name", "shall", "scenarios"}:
                raise SpecError("INVALID_MANIFEST", "Unexpected requirement attributes")
            label = short(item_req.get("name"), "requirement name", 4, 100)
            if label.casefold() in labels:
                raise SpecError("DUPLICATE", "Duplicate requirement: " + label)
            labels.add(label.casefold())
            shall = short(item_req.get("shall"), "shall requirement", 20, 500)
            if not re.search(r"\bSHALL\b", shall):
                raise SpecError("INVALID_MANIFEST", "Requirement must contain normative SHALL")
            scenarios = item_req.get("scenarios")
            if not isinstance(scenarios, list) or not 1 <= len(scenarios) <= 5:
                raise SpecError("INVALID_MANIFEST", "Requirement needs 1..5 scenarios")
            clean_scenarios = []
            scene_names = set()
            for scene in scenarios:
                if not isinstance(scene, dict) or set(scene) - {"name", "given", "when", "then"}:
                    raise SpecError("INVALID_MANIFEST", "Invalid scenario fields")
                sn = short(scene.get("name"), "scenario name", 4, 100)
                if sn.casefold() in scene_names:
                    raise SpecError("DUPLICATE", "Duplicate scenario name")
                scene_names.add(sn.casefold())
                clean_scenarios.append({
                    "name": sn,
                    "given": short(scene["given"], "given", 4, 500) if "given" in scene else "",
                    "when": short(scene.get("when"), "when", 5, 500),
                    "then": short(scene.get("then"), "then", 5, 500),
                })
            clean_reqs.append({"name": label, "shall": shall, "scenarios": clean_scenarios})
            total += 1
        caps.append({"name": name, "summary": summary, "requirements": clean_reqs})
    if total > 60:
        raise SpecError("INVALID_MANIFEST", "Maximum 60 requirements per change")
    constraints = value.get("constraints", [])
    if not isinstance(constraints, list) or len(constraints) > 15:
        raise SpecError("INVALID_MANIFEST", "constraints must be a list <=15")
    constraints = [short(t, "constraint", 4, 500) for t in constraints]
    return {"project": project, "change": change, "title": title, "problem": problem,
            "outcome": outcome, "capabilities": caps, "constraints": constraints}


def existing_feature_names(project):
    folder = project / "specs"
    if not folder.is_dir():
        return set()
    return {p.name for p in folder.iterdir() if p.is_dir()}


def feature_folder(project, change):
    existing = existing_feature_names(project)
    used = [int(m.group(1)) for n in existing if (m := re.match(r"^(\d{3})-", n))]
    for i in range(1, 1000):
        folder = f"{i:03d}-{change}"
        if i not in used and folder not in existing:
            return folder
    raise SpecError("PROJECT_FULL", "No available feature ID")


def build(manifest, project):
    name = manifest["change"]
    folder = feature_folder(project, name)
    prefix = f"openspec/changes/{name}"
    feature = f"specs/{folder}"
    caps = manifest["capabilities"]
    proposal = [
        "# Proposal — " + manifest["title"], "", "## Why", manifest["problem"],
        "", "## What Changes", "- " + manifest["outcome"], "",
        "## Capabilities", "", "### New Capabilities",
    ] + [f"- **{c['name']}**: {c['summary']}" for c in caps]
    proposal += ["", "### Modified Capabilities", "None identified in the submitted brief.",
                 "", "## Impact", "Affected components and exact files: TBD during design review.",
                 "", "## Acceptance", "Each SHALL requirement must pass the linked WHEN/THEN scenario.", ""]
    design = [
        "# Design — " + manifest["title"], "", "## Context", manifest["problem"],
        "", "## Goals", manifest["outcome"], "", "## Architecture",
        "TBD: identify existing entrypoints, trust boundaries, data flows and component responsibilities before implementation.",
        "", "## Constraints",
    ] + (["- " + x for x in manifest["constraints"]] or ["- None supplied; validate during discovery."])
    design += ["", "## Decisions & alternatives",
               "- Proposed design: TBD; compare at least one alternative and existing behavior.",
               "- Security: define read/write scope, rollback and untrusted inputs.",
               "- Failure modes: timeouts, retries, stale data, privilege failures.",
               "", "## Rollback",
               "TBD: define a reversible strategy before approving state-changing implementation.", ""]
    source = {}
    tasks = ["# Implementation tasks — " + manifest["title"], "",
             "Status: generated plan; no implementation is claimed.",
             "", "- [ ] 0. Review and approve this proposal, requirements and acceptance scenarios.",
             "- [ ] 1. Audit current project implementation, data model and trust boundaries.",
             "- [ ] 2. Finalize design, rollback and test fixtures."]
    sdd_tasks = ["# SDD Tasks — " + manifest["title"], "",
                 "Checkboxes are intentionally unchecked until test evidence exists.", ""]
    stories = ["# Feature Specification: " + manifest["title"], "",
               "**Status:** Draft — user review required", "",
               "**Input problem:** " + manifest["problem"], "",
               "**Target outcome:** " + manifest["outcome"], "",
               "## User Scenarios & Testing", ""]
    mapping = []
    counter = 0
    for ci, cap in enumerate(caps, start=1):
        block = ["# Spec Delta", "", "## Purpose", cap["summary"],
                 "", "## ADDED Requirements"]
        stories += [f"### User Story {ci} — {cap['name']} (P1)", "",
                    cap["summary"], "", "**Independent Test:**",
                    "Use isolated fixtures and execute the named acceptance scenarios.", "",
                    "**Acceptance Scenarios:**"]
        for ri, req in enumerate(cap["requirements"], start=1):
            counter += 1
            fid = f"FR-{counter:03d}"
            block += ["", "### Requirement: " + req["name"], req["shall"], ""]
            for scene in req["scenarios"]:
                block += ["#### Scenario: " + scene["name"]]
                if scene["given"]:
                    block += ["- **GIVEN** " + scene["given"]]
                block += ["- **WHEN** " + scene["when"], "- **THEN** " + scene["then"], ""]
                stories += [f"{ri}. Given {scene['given'] or 'the feature context'}, when {scene['when']}, then {scene['then']}."]
            task_no = f"T{counter:03d}"
            ref = cap["name"] + " / " + req["name"]
            tasks += [f"- [ ] {task_no} Implement and test {ref}; include negative cases, outputs and error contracts."]
            sdd_tasks += [f"- [ ] {task_no} [{fid}] Implement {ref}; attach acceptance-test evidence."]
            mapping += [f"| {fid} | {ref} | {req['shall']} |"]
        stories += ["", ""]
        source[f"{prefix}/specs/{cap['name']}/spec.md"] = "\n".join(block).rstrip() + "\n"
    tasks += ["- [ ] FINAL Run local spec crosswalk and installed OpenSpec strict validation.",
              "- [ ] FINAL Run unit, negative, and E2E acceptance checks with evidence.",
              "- [ ] FINAL Reviewer approval and explicit release/rollback decision.", ""]
    stories += ["## Edge Cases",
                "- Authorization denied, unavailable dependencies, repeated retries and concurrent modification.",
                "- Invalid input, Windows Unicode handling and output/timeout limits.", "",
                "## Functional Requirements",
                "Normative requirements are owned by the OpenSpec delta files; this is an index.", "",
                "| ID | Canonical OpenSpec requirement | Contract |",
                "| --- | --- | --- |"] + mapping
    stories += ["", "## Success Criteria",
                f"- SC-001: All {counter} mapped functional requirements have verified acceptance evidence.",
                "- SC-002: No previously existing project files are overwritten by the specification generator.",
                "- SC-003: Upstream OpenSpec strict validation passes for this change.",
                "- SC-004: Implementation review explicitly distinguishes unit/mock from live integration results.", ""]
    plan = ["# Implementation Plan — " + manifest["title"], "",
            "## Technical Context", "Project implementation details: TBD from repository discovery.",
            "", "## Proposed Architecture", "See the OpenSpec design.md; no implementation decisions are assumed.",
            "", "## Quality Gates",
            "1. Review requirements and missing constraints.",
            "2. Define system boundaries, threat model and rollback.",
            "3. Implement in a scoped worktree and run acceptance tests.",
            "4. Validate OpenSpec, crosswalk and reviewer approval.", "",
            "## Dependencies",
            "Tools and versions: determine from existing project manifests; no packages installed.", ""]
    checklist = ["# Requirement Quality Checklist", "",
                 "- [x] Each OpenSpec capability has a SHALL requirement and testable WHEN/THEN scenario.",
                 "- [x] Each requirement has an FR mapping and a traceable implementation task.",
                 "- [ ] User confirmed requirements reflect intended behavior.",
                 "- [ ] Technical decisions and rollback are completed.",
                 "- [ ] Unit and end-to-end acceptance evidence attached.",
                 "- [ ] Reviewer approved release.", ""]
    source.update({
        f"{prefix}/proposal.md": "\n".join(proposal),
        f"{prefix}/design.md": "\n".join(design),
        f"{prefix}/tasks.md": "\n".join(tasks),
        f"{feature}/spec.md": "\n".join(stories),
        f"{feature}/plan.md": "\n".join(plan),
        f"{feature}/tasks.md": "\n".join(sdd_tasks + [""]),
        f"{feature}/checklists/requirements.md": "\n".join(checklist),
    })
    if not (project / "openspec/config.yaml").exists():
        source["openspec/config.yaml"] = "schema: spec-driven\n"
    if not (project / ".specify/memory/constitution.md").exists():
        source[".specify/memory/constitution.md"] = (
            "# Project Constitution (DRAFT)\n\n"
            "This file has not been approved by the project owner.\n"
            "## Principles\n"
            "1. Preserve existing project files and user data.\n"
            "2. Define observable acceptance criteria before implementation.\n"
            "3. Require explicit approval for risky or irreversible actions.\n"
            "4. Record test evidence and reviewer decisions.\n")
    return source


def plan(manifest, root=ROOT):
    project = checked_path(manifest["project"], root, directory=True)
    if not (project / "openspec/changes").exists() and (project / "openspec").exists():
        # Other custom layouts are supported; no need to create a new project.
        pass
    name = manifest["change"]
    if (project / "openspec/changes" / name).exists():
        raise SpecError("CHANGE_EXISTS", "This OpenSpec change already exists; no overwrite")
    files = build(manifest, project)
    for rel in files:
        target = (project / rel).resolve()
        if not target.is_relative_to(project):
            raise SpecError("PATH_DENIED", "Planned path escapes project")
        if target.exists() or target.is_symlink():
            raise SpecError("FILE_EXISTS", f"Refusing to overwrite {rel}")
    digest = hashlib.sha256(json.dumps(files, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
    return project, files, digest


def preview(manifest, root=ROOT):
    project, files, digest = plan(manifest, root)
    return {"project": project.relative_to(Path(root).resolve()).as_posix(),
            "change": manifest["change"], "plan_sha256": digest,
            "files": list(files),
            "requirement_count": sum(len(c["requirements"]) for c in manifest["capabilities"]),
            "scenario_count": sum(len(r["scenarios"]) for c in manifest["capabilities"] for r in c["requirements"]),
            "will_write": False, "approval_required": True,
            "warning": "Only reviewed manifests may be applied; --apply is NOT host-level approval"}


def check_generated_sdd(project, change):
    """Cross-check an auto-generated SDD feature against its OpenSpec deltas."""
    features = project / "specs"
    candidates = sorted(p for p in features.glob(f"???-{change}") if p.is_dir()) if features.is_dir() else []
    if len(candidates) != 1:
        raise SpecError("SDD_MISSING", "Expected exactly one generated SDD feature folder")
    folder = candidates[0]
    spec = (folder / "spec.md").read_text(encoding="utf-8")
    tasks = (folder / "tasks.md").read_text(encoding="utf-8")
    for name in ("plan.md", "checklists/requirements.md"):
        if not (folder / name).is_file():
            raise SpecError("SDD_MISSING", "SDD artifact missing: " + name)
    for heading in ("## User Scenarios & Testing", "## Functional Requirements", "## Success Criteria"):
        if heading not in spec:
            raise SpecError("SDD_INVALID", "SDD spec missing section: " + heading)
    mappings = re.findall(r"(?m)^[|] (FR-[0-9]{3}) [|] ([a-z][a-z0-9-]*) / ([^|]+?) [|]", spec)
    if not mappings or len(mappings) != len(set(m[0] for m in mappings)):
        raise SpecError("SDD_INVALID", "FR mappings missing or duplicated")
    delta_dir = project / "openspec/changes" / change / "specs"
    observed = set()
    scenario_count = 0
    for delta in delta_dir.glob("*/spec.md"):
        content = delta.read_text(encoding="utf-8")
        requirements = re.findall(r"(?m)^### Requirement: (.+)$", content)
        scenario_count += len(re.findall(r"(?m)^#### Scenario: ", content))
        observed.update((delta.parent.name, r.strip()) for r in requirements)
    expected = {(cap, title.strip()) for _, cap, title in mappings}
    if expected != observed:
        raise SpecError("SDD_INVALID", "FR crosswalk does not match exact OpenSpec requirements")
    for fid, _, _ in mappings:
        if f"[{fid}]" not in tasks:
            raise SpecError("SDD_INVALID", f"Unmapped implementation task: {fid}")
    return {"validated": True, "feature_folder": folder.relative_to(project).as_posix(),
            "requirements": len(observed), "scenarios": scenario_count,
            "fr_mappings": len(mappings)}


def validate(project, change, timeout=35):
    slug(change, "change")
    if not (project / "openspec/changes" / change).is_dir():
        raise SpecError("CHANGE_MISSING", "OpenSpec change directory does not exist")
    executable = shutil.which("openspec")
    if not executable:
        raise SpecError("OPENSPEC_MISSING", "Install/configure OpenSpec separately before creation")
    try:
        result = subprocess.run([executable, "validate", change, "--strict"], cwd=str(project),
                                stdin=subprocess.DEVNULL, text=True, encoding="utf-8",
                                errors="replace", capture_output=True, timeout=timeout,
                                check=False)
    except (subprocess.TimeoutExpired, OSError):
        raise SpecError("VALIDATION_FAILED", "OpenSpec process timed out or failed to launch")
    if result.returncode != 0:
        raise SpecError("VALIDATION_FAILED", (result.stdout + result.stderr)[:1200])
    candidates = list((project / "specs").glob(f"???-{change}/spec.md")) if (project / "specs").is_dir() else []
    if len(candidates) == 1 and "**Status:** Draft — user review required" in candidates[0].read_text(encoding="utf-8"):
        sdd = check_generated_sdd(project, change)
    else:
        sdd = {"validated": False, "reason": "No generator-owned SDD feature to cross-check"}
    return {"validated": True, "validator": "openspec validate --strict",
            "message": result.stdout.strip()[:400], "sdd": sdd}


def create(manifest, expected_hash, root=ROOT):
    project, files, digest = plan(manifest, root)
    if expected_hash != digest:
        raise SpecError("PREVIEW_CHANGED", "Manifest/target changed after preview; review a new plan")
    if not shutil.which("openspec"):
        raise SpecError("OPENSPEC_MISSING", "OpenSpec CLI required for validated creation")
    created = []
    dirs = []
    try:
        for rel, content in files.items():
            dest = project / rel
            # Recheck canonical parent immediately before writes; reject junction/symlink pivots.
            if not dest.resolve().is_relative_to(project) or not dest.parent.resolve().is_relative_to(project):
                raise SpecError("PATH_DENIED", "Planned path was redirected outside the project")
            parent = dest.parent
            missing = []
            while not parent.exists() and parent.is_relative_to(project):
                missing.append(parent)
                parent = parent.parent
            for folder in reversed(missing):
                folder.mkdir(exist_ok=False)
                dirs.append(folder)
            # Exclusive create is the final precondition against concurrent writers.
            with dest.open("x", encoding="utf-8", newline="\n") as handle:
                created.append(dest)
                handle.write(content)
        result = validate(project, manifest["change"])
        return {"created": True, "files": [str(p.relative_to(project)).replace("\\", "/") for p in created],
                "plan_sha256": digest, "change": manifest["change"], "upstream": result}
    except BaseException:
        for file in reversed(created):
            # Remove only our files; avoid deleting a concurrent user modification.
            try:
                if file.exists() and file.read_text(encoding="utf-8") == files[file.relative_to(project).as_posix()]:
                    file.unlink()
            except OSError:
                pass
        for folder in reversed(dirs):
            try:
                folder.rmdir()
            except OSError:
                pass
        raise


def status(project_path, root=ROOT):
    project = checked_path(project_path, root, directory=True)
    changes = project / "openspec/changes"
    features = project / "specs"
    return {
        "project": project_path,
        "changes": sorted(d.name for d in changes.iterdir() if d.is_dir() and SLUG.fullmatch(d.name)) if changes.is_dir() else [],
        "features": sorted(d.name for d in features.iterdir() if d.is_dir() and re.match(r"^\d{3}-", d.name)) if features.is_dir() else [],
        "openspec_available": shutil.which("openspec") is not None,
    }


def parse(argv):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    for name in ("spec_preview", "spec_create"):
        p = sub.add_parser(name)
        p.add_argument("manifest", help="Relative UTF-8 JSON manifest in the workspace")
        if name == "spec_create":
            p.add_argument("--apply", action="store_true")
            p.add_argument("--expected-plan-sha256", required=True)
    q = sub.add_parser("spec_request_prepare")
    q.add_argument("manifest")
    q.add_argument("--expected-plan-sha256", required=True)
    r = sub.add_parser("spec_request_status")
    r.add_argument("request_id")
    d = sub.add_parser("spec_request_diagnose")
    d.add_argument("request_id")
    e = sub.add_parser("spec_request_execute")
    e.add_argument("request_id")
    v = sub.add_parser("spec_validate")
    v.add_argument("project")
    v.add_argument("change")
    s = sub.add_parser("spec_status")
    s.add_argument("project")
    qa = sub.add_parser("spec_report")
    qa.add_argument("project")
    qa.add_argument("change")
    qa.add_argument("--strict", action="store_true")
    return parser.parse_args(argv)


def cli(argv):
    from spec_approval_queue import ApprovalQueue, QueueError
    try:
        args = parse(argv)
        if args.action in ("spec_preview", "spec_create"):
            m = read_manifest(args.manifest)
            if args.action == "spec_preview":
                data = preview(m)
            else:
                raise SpecError("APPROVAL_REQUIRED",
                    "Legacy direct spec_create is disabled. Use spec_request_prepare, "
                    "local owner_approve.py, then spec_request_execute")
        elif args.action == "spec_request_prepare":
            data = ApprovalQueue().prepare(args.manifest, args.expected_plan_sha256)
        elif args.action == "spec_request_status":
            data = ApprovalQueue().status(args.request_id)
        elif args.action == "spec_request_diagnose":
            data = ApprovalQueue().diagnose(args.request_id)
        elif args.action == "spec_request_execute":
            data = ApprovalQueue().execute(args.request_id)
        elif args.action == "spec_validate":
            data = validate(checked_path(args.project, directory=True), args.change)
        elif args.action == "spec_report":
            from spec_analyzer import analyze
            data = analyze(args.project, args.change, strict=args.strict)
        else:
            data = status(args.project)
        result, code = {"ok": True, "data": data}, 0
    except (SpecError, QueueError) as e:
        result, code = {"ok": False, "error": {"code": e.code, "message": str(e),
                       "retryable": getattr(e, "retryable", False)}}, 1
    except (OSError, UnicodeError):
        result, code = {"ok": False, "error": {"code": "IO_FAILED",
                                              "message": "Filesystem operation failed", "retryable": False}}, 2
    print(json.dumps(result, ensure_ascii=True, separators=(",", ":")))
    return code


if __name__ == "__main__":
    sys.exit(cli(sys.argv[1:]))


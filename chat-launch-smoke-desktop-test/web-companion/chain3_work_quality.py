"""Read-only quality gate for proposed continued work in Chain3.

A reviewer cannot create a successor using prose alone: it must link exactly
one *new*, source-scoped defect to a specific existing SDD criterion and a
SHA-bound test artifact. This is structural evidence, not a trusted human
approval or proof of a model's claimed test success.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

ISSUE_ID=re.compile(r"[a-z][a-z0-9-]{5,63}\Z")
SHA256=re.compile(r"[0-9a-f]{64}\Z")
FIELDS={"issue_id","criterion","objective","evidence_file",
        "evidence_sha256","next_verification"}
MAX_EVIDENCE_BYTES=131072

class QualityError(ValueError):pass

def _text(x,minimum=20,maximum=800):
    return type(x) is str and minimum<=len(x.strip())<=maximum and "\x00" not in x

def validate_decision(*, decision, no_actionable_work,
                      approved_ticket, workspace, changed_files):
    """Return a normalized, verified *work proposal* or None.

    Only the known acceptance/validation/failure criteria of the immutable SDD
    manifest are allowed; free-form new features are not an authority source.
    """
    if type(no_actionable_work) is not bool:
        raise QualityError("continuation_flag_not_boolean")
    if no_actionable_work:
        if decision is not None:
            raise QualityError("terminal_must_not_propose_work")
        return None
    if type(decision) is not dict or set(decision)!=FIELDS:
        raise QualityError("structured_next_work_required")
    issue_id=decision["issue_id"]
    if type(issue_id) is not str or not ISSUE_ID.fullmatch(issue_id):
        raise QualityError("invalid_issue_id")
    allowed={x for field in ("acceptance","failure_scenarios","validation")
             for x in approved_ticket[field]}
    if type(decision["criterion"]) is not str or decision["criterion"] not in allowed:
        raise QualityError("criterion_outside_approved_scope")
    if not _text(decision["objective"],30,800):
        raise QualityError("weak_work_objective")
    if not _text(decision["next_verification"],30,800):
        raise QualityError("missing_acceptance_verification")
    name=decision["evidence_file"]
    if type(name) is not str or not 4<=len(name)<=128:
        raise QualityError("invalid_evidence_name")
    rel=Path(name)
    if (rel.is_absolute() or ".." in rel.parts or "\\" in name or ":" in name
        or name not in changed_files or rel.suffix not in {".txt",".json",".md"}):
        raise QualityError("unsafe_or_unlisted_evidence_file")
    candidate=workspace/rel
    root=workspace.resolve()
    if (not candidate.is_file() or candidate.is_symlink() or
        not candidate.resolve().is_relative_to(root)):
        raise QualityError("missing_or_escaped_evidence_file")
    size=candidate.stat().st_size
    if not 80<=size<=MAX_EVIDENCE_BYTES:
        raise QualityError("evidence_size_invalid")
    expected=decision["evidence_sha256"]
    if type(expected) is not str or not SHA256.fullmatch(expected):
        raise QualityError("evidence_sha_invalid")
    if hashlib.sha256(candidate.read_bytes()).hexdigest()!=expected:
        raise QualityError("evidence_sha_mismatch")
    return dict(decision)

def validate_novel_issue(decision, previous_decisions):
    """Reject repeated issue IDs across prior complete reviewer generations."""
    if decision is None:return
    seen=set()
    signatures=set()
    evidence_hashes=set()
    def signature(x):
        return (x["criterion"]," ".join(x["objective"].casefold().split()))
    for previous in previous_decisions:
        if type(previous) is not dict or not FIELDS.issubset(previous):
            raise QualityError("historical_work_evidence_missing")
        issue=previous.get("issue_id")
        if type(issue) is not str:raise QualityError("historical_issue_invalid")
        if issue in seen:raise QualityError("historical_duplicate_issue")
        seen.add(issue)
        signatures.add(signature(previous))
        evidence_hashes.add(previous["evidence_sha256"])
    if decision["issue_id"] in seen:
        raise QualityError("recycled_issue_has_no_new_work")
    if signature(decision) in signatures:
        raise QualityError("recycled_problem_signature")
    if decision["evidence_sha256"] in evidence_hashes:
        raise QualityError("recycled_evidence_without_new_information")

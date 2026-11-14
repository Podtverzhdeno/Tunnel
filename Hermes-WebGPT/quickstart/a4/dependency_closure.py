"""Audit transitive local Python imports needed for clean-clone Hermes startup.

Only metadata, filenames and risk counts are printed. Does not copy sources,
open credentials, connect to the network or change runtime configuration.
"""
from __future__ import annotations
import ast
import hashlib
import json
from pathlib import Path
import re

ROOT=Path(__file__).resolve().parents[2]
ENTRY=("relay.py","gateway_runner.py","start_bridge.py")
# Pinned source PRESENCE requirements for the current standalone Hermes
# startup dependency closure. Content is NOT included; hashes are calculated
# by the separate audit. A release must provide all these modules or fail
# readiness instead of claiming a working clean clone.
REQUIRED_PY_FILES=(
    "gateway_bootstrap.py", "gateway_runner.py", "gateway_status_precision.py",
    "hermes_delivery_evidence.py", "hermes_notice_expiry.py",
    "hermes_private_storage.py", "hermes_relay_completion_observer.py",
    "hermes_tool_failure_hints.py", "hermes_tool_schema_guard.py",
    "hermes_windows_browser.py", "model_news_prefetch.py",
    "model_prompt_compaction.py", "relay.py", "start_bridge.py",
    "telegram_access.py", "telegram_delete_control.py",
    "telegram_hop_observer.py", "telegram_status_cleanup.py",
    "telegram_stream_receipt_observer.py", "telegram_topic_context_answer.py",
    "telegram_topic_extensions.py", "telegram_topic_features.py",
    "telegram_topic_menu.py", "telegram_topic_settings.py",
)
SECRET_PATTERNS=(r"sk-[A-Za-z0-9_-]{20,}",r"gh[pousr]_[A-Za-z0-9]{25,}",
                 r"\b[0-9]{8,12}:[A-Za-z0-9_-]{28,}")
def closure(root=ROOT):
    todo=list(ENTRY)
    seen=set()
    report={}
    while todo:
        name=todo.pop(0)
        if name in seen:
            continue
        seen.add(name)
        source=root/name
        if not source.is_file() or source.is_symlink():
            report[name]={"exists":False}
            continue
        raw=source.read_bytes()
        if source.suffix!=".py":
            report[name]={"exists":True}
            continue
        text=raw.decode("utf-8-sig",errors="replace")
        try:
            tree=ast.parse(text)
        except SyntaxError:
            report[name]={"exists":True,"syntax_error":True}
            continue
        names={n.module.split(".")[0] for n in ast.walk(tree)
               if isinstance(n,ast.ImportFrom) and n.module}
        names|={a.name.split(".")[0] for n in ast.walk(tree)
                if isinstance(n,ast.Import) for a in n.names}
        deps=sorted({mod+".py" for mod in names
                     if (root/(mod+".py")).is_file() and mod+".py"!=name})
        todo.extend([p for p in deps if p not in seen])
        report[name]={"exists":True, "bytes":len(raw),
                      "sha256":hashlib.sha256(raw).hexdigest(),
                      "local_imports":deps,
                      "possible_credentials":sum(len(re.findall(pat,text))
                        for pat in SECRET_PATTERNS)}
    return {"entrypoints":list(ENTRY),
            "files":dict(sorted(report.items())),
            "limitations":"Static root Python imports only; dynamic imports and non-Python assets need independent review."}

def missing_required_files(root=ROOT) -> list[str]:
    # Only check declared source filenames; never scan private state.
    return sorted(name for name in REQUIRED_PY_FILES if not (root/name).is_file()
                  or (root/name).is_symlink())

if __name__=="__main__":
    item=closure()
    item["missing_required"]=missing_required_files()
    print(json.dumps(item,ensure_ascii=False))

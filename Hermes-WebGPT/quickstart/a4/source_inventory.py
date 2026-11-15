"""Read-only source dependency inventory; never prints source contents or secrets."""
from __future__ import annotations
import ast
import hashlib
import json
from pathlib import Path
import re

ROOT=Path(__file__).resolve().parents[2]
REQUIRED=("relay.py","gateway_runner.py","start_bridge.py","quickstart/start.ps1")
SECRET_PATTERNS=(r"sk-[A-Za-z0-9_-]{20,}",r"gh[pousr]_[A-Za-z0-9]{25,}",
                 r"\b[0-9]{8,12}:[A-Za-z0-9_-]{28,}")

def audit(root=ROOT):
    result={}
    for name in REQUIRED:
        path=root/name
        if not path.is_file() or path.is_symlink():
            result[name]={"exists":False}
            continue
        data=path.read_bytes()
        summary={"exists":True,"bytes":len(data),
                 "sha256":hashlib.sha256(data).hexdigest()}
        if path.suffix==".py":
            src=data.decode("utf-8-sig",errors="replace")
            try:
                tree=ast.parse(src)
                mods=sorted({node.module.split(".")[0] for node in ast.walk(tree)
                             if isinstance(node,ast.ImportFrom) and node.module}|
                            {alias.name.split(".")[0] for node in ast.walk(tree)
                             if isinstance(node,ast.Import) for alias in node.names})
                summary["imports"]=mods
                summary["parse_ok"]=True
            except SyntaxError:
                summary["parse_ok"]=False
            summary["possible_credential_patterns"]=sum(
                len(re.findall(pattern,src)) for pattern in SECRET_PATTERNS)
        result[name]=summary
    return result

if __name__=="__main__":
    print(json.dumps(audit(),ensure_ascii=False))

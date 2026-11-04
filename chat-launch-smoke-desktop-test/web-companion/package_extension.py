"""Package reviewed source code, not the user's local browser credentials or queue."""
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED
import hashlib
import json

root=Path(__file__).resolve().parent
files=[
    "manifest.json","core.js","content.js","background.js",
    "background_content.js","popup.html","popup.js","README.md",
    "bridge.py","setup_bridge.py","start_bridge.py","restart_bridge.py",
    "enqueue.py","status.py","enqueue_batch.py","batch_status.py",
    "ten_agent_plan.json","prepare_retry.py","retry_nine_plan.json",
    "enqueue_retry.py","recovery_status.py",
    "enqueue_completion.py","advisory_completion.py",
    "role_campaign.py","role_metrics.py","chain_campaign.py","chain_metrics.py",
    "role-campaigns/eng1.json","role-campaigns/chain2.json",
    "CHAIN2_RUNBOOK.md","ROLE_CAMPAIGNS.md"
]
manifest=json.loads((root/"manifest.json").read_text(encoding="utf-8"))
assert manifest["manifest_version"]==3
assert manifest["version"]=="0.6.6"
assert manifest["content_scripts"][0]["matches"]==["https://chatgpt.com/*"]
assert manifest["content_scripts"][0]["js"]==[
    "core.js","content.js","background_content.js"]
assert manifest["background"]["service_worker"]=="background.js"
assert manifest["host_permissions"]==["http://127.0.0.1:18763/*","https://chatgpt.com/*"]
dest=root.parent/"chat-launch-smoke-web-companion-0.6.6.zip"
with ZipFile(dest,"w",ZIP_DEFLATED) as out:
    for name in files:
        source=root/name
        assert source.is_file() and not source.is_symlink(),name
        out.write(source,arcname="web-companion/"+name)
with ZipFile(dest) as archive:
    paths=set(archive.namelist())
    assert paths=={"web-companion/"+name for name in files}
    assert not any("token" in name.lower() or "bridge-state" in name or
                   "bridge-config" in name or "__pycache__" in name
                   for name in paths)
    assert json.loads(archive.read("web-companion/manifest.json"))["version"]=="0.6.6"
print(json.dumps({"ok":True,"archive":str(dest),"bytes":dest.stat().st_size,
   "sha256":hashlib.sha256(dest.read_bytes()).hexdigest(),"file_count":len(files)}))

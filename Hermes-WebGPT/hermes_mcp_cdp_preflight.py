"""Read-only readiness for Hermes -> Relay -> MCP -> ChatGPT Web CDP.

Never opens a browser, claims a Relay job, uses Telegram, reads cookies,
prints chat IDs, or confirms a user account is signed in.
"""
from __future__ import annotations
import json
from pathlib import Path
import re
from urllib.request import Request, ProxyHandler, build_opener
from urllib.error import URLError

ROOT=Path(__file__).resolve().parent
CHAT=re.compile(r"^https://chatgpt[.]com/c/[a-z0-9-]+$")
MAX_JSON=100000


def fetch(url, bearer=None):
    headers={} if bearer is None else {"Authorization":"Bearer "+bearer}
    opener=build_opener(ProxyHandler({}))
    with opener.open(Request(url,headers=headers),timeout=2.5) as resp:
        if resp.status!=200:
            raise ValueError("unexpected_http_status")
        data=resp.read(MAX_JSON+1)
    if len(data)>MAX_JSON:
        raise ValueError("body_too_large")
    return json.loads(data)


def inspect(*, relay_port=18765, cdp_port=9223):
    result={
        "ok":True, "read_only":True, "relay_reachable":False,
        "playwright_model_enabled":False, "cdp_reachable":False,
        "matching_chat_tabs":0, "worker_principal_present":False,
        "browser_authentication_verified":False,
        "telegram_delivery_verified":False, "actions":0,
        "ready_for_verified_e2e":False,
    }
    base=f"http://127.0.0.1:{relay_port}"
    cdp=f"http://127.0.0.1:{cdp_port}"
    try:
        info=fetch(base+"/health")
        result["relay_reachable"]=info.get("service")=="hermes-webgpt-relay" and info.get("protocol")==1
    except (OSError, ValueError, TypeError, KeyError):
        return result
    auth=ROOT/"data"/"relay-auth.json"
    worker=ROOT/"data"/"playwright-worker-auth.json"
    result["worker_principal_present"]=worker.is_file() and not worker.is_symlink()
    if auth.is_file() and not auth.is_symlink():
        try:
            key=json.loads(auth.read_text(encoding="utf8"))["key"]
            models=fetch(base+"/v1/models",str(key))
            result["playwright_model_enabled"]=sum(
                item.get("id")=="web-chatgpt-high-cdp"
                for item in models.get("data",[]) if isinstance(item,dict))==1
        except (OSError, ValueError, KeyError, TypeError):
            pass
    try:
        version=fetch(cdp+"/json/version")
        targets=fetch(cdp+"/json/list")
        result["cdp_reachable"]=isinstance(version,dict) and bool(version.get("Browser"))
        if isinstance(targets,list):
            result["matching_chat_tabs"]=sum(
                target.get("type")=="page" and bool(CHAT.fullmatch(target.get("url","")))
                for target in targets if isinstance(target,dict))
    except (OSError, ValueError, TypeError, KeyError):
        pass
    # A visible URL cannot prove account authorization, so never assert
    # a successful roundtrip until one exact request-bound E2E completes.
    return result


if __name__=="__main__":
    print(json.dumps(inspect(),sort_keys=True))

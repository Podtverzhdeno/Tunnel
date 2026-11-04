"""Start only the isolated loopback bridge; never create a browser window."""
import json,os,subprocess,sys,time,urllib.request
from bridge import ROOT, PORT, initialize
def health(token):
    req=urllib.request.Request("http://127.0.0.1:"+str(PORT)+"/v1/health",
                               headers={"X-Chat-Smoke-Key":token})
    try:
        with urllib.request.urlopen(req,timeout=1) as resp:
            return json.load(resp).get("ok") is True
    except Exception:
        return False
if __name__=="__main__":
    key=initialize()
    if health(key):
        print(json.dumps({"ok":True,"state":"already_running","port":PORT}));sys.exit(0)
    flags=0
    if os.name=="nt":
        flags=subprocess.CREATE_NO_WINDOW|subprocess.DETACHED_PROCESS|subprocess.CREATE_NEW_PROCESS_GROUP
    p=subprocess.Popen([sys.executable,str(ROOT/"bridge.py")],cwd=str(ROOT),
        stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
        close_fds=True,creationflags=flags)
    # Only readiness checks, no browser navigation or user chat requests.
    for i in range(12):
        if health(key):
            print(json.dumps({"ok":True,"state":"started","port":PORT,"pid":p.pid}));sys.exit(0)
        if p.poll() is not None:break
        time.sleep(.25)
    print(json.dumps({"ok":False,"state":"unavailable","port":PORT,"returncode":p.poll()}))
    sys.exit(1)

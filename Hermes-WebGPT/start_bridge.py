from pathlib import Path
import json
import os
import subprocess
import sys
import time
import urllib.request

ROOT=Path(__file__).resolve().parent

def configured_capacity():
    path=ROOT/'relay_capacity.json'
    if not path.exists():return 1
    obj=json.loads(path.read_text(encoding='utf-8'))
    if type(obj) is not dict or set(obj)!={'max_active_requests'} or type(obj['max_active_requests']) is not int or obj['max_active_requests'] not in (1,2,3):
        raise ValueError('invalid_relay_capacity_config')
    return obj['max_active_requests']

def health():
    try:
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open('http://127.0.0.1:18765/health',timeout=2) as r:result=json.load(r)
        return result.get('service')=='hermes-webgpt-relay' and result.get('protocol')==1
    except Exception:return False
def main():
    if health():
        print(json.dumps({'ok':True,'already_running':True,'port':18765}));return
    receipt=ROOT/'bridge_process.json'
    if receipt.exists():raise SystemExit('Existing launch receipt requires health/PID reconciliation; not launching a duplicate.')
    log=open(ROOT/'bridge_process.log','ab',buffering=0)
    flags=(subprocess.DETACHED_PROCESS|subprocess.CREATE_NEW_PROCESS_GROUP) if os.name=='nt' else 0
    p=subprocess.Popen([sys.executable,str(ROOT/'relay.py'),'--data',str(ROOT/'data'),
                        '--max-active-requests',str(configured_capacity())]+\
                        (['--enable-playwright'] if os.environ.get('HERMES_RELAY_CDP_ENABLED')=='1' else []),cwd=ROOT,
                       stdin=subprocess.DEVNULL,stdout=log,stderr=log,creationflags=flags,start_new_session=os.name!='nt',close_fds=True)
    result={'pid':p.pid,'port':18765,'ok':False,'created':True}
    receipt.write_text(json.dumps(result,indent=2),encoding='utf-8')
    for _ in range(50):
        if health():result['ok']=True;break
        if p.poll() is not None:break
        time.sleep(.1)
    receipt.write_text(json.dumps(result,indent=2),encoding='utf-8');print(json.dumps(result),flush=True)
    if not result['ok']:raise SystemExit(1)
if __name__=='__main__':main()

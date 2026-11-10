"""One-shot live bridge diagnosis. READ-ONLY. Redacts user/chat content and keys."""
from pathlib import Path
import datetime,json,sqlite3,urllib.request,time,subprocess
ROOT=Path(__file__).resolve().parent
result={"observed_utc":datetime.datetime.now(datetime.timezone.utc).isoformat()}
try:
 state=json.loads((ROOT/"profile/gateway_state.json").read_text(encoding="utf-8"))
 result["gateway"]={"state":state.get("gateway_state"),"telegram":state.get("platforms",{}).get("telegram",{}).get("state"),"pid":state.get("pid"),"active_agents":state.get("active_agents")}
except Exception as e:result["gateway_error"]=type(e).__name__
try:
 opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
 with opener.open("http://127.0.0.1:18765/health",timeout=3) as x:
  obj=json.load(x)
  result["relay_health"]={"http_status":x.status,"ok":obj.get("ok"),"state":obj.get("state")}
  build=obj.get("runtime_build")
  allowed=("relay.py","hermes_tool_schema_guard.py","model_prompt_compaction.py","model_news_prefetch.py")
  if isinstance(build,dict):
   result["relay_health"]["runtime_build"]={name:value for name,value in build.items() if name in allowed and isinstance(value,str) and len(value)==64 and all(c in "0123456789abcdef" for c in value)}
except Exception as e:result["relay_health_error"]=type(e).__name__
p=ROOT/"data/relay.sqlite3"
if p.exists():
 try:
  with sqlite3.connect(p.as_uri()+"?mode=ro",uri=True,timeout=2) as c:
   result["states"]=dict(c.execute("select state,count(*) from requests group by state").fetchall())
   result["recent"]=[{"request_id":str(r[0])[:12],"state":r[1],"worker_present":bool(r[2]),"tab_assigned":r[3] is not None,"saved_url_present":bool(r[4]),"result_present":bool(r[5]),"age_seconds":int(time.time()-r[6])} for r in c.execute("select id,state,worker,tab,saved_url,result,created from requests order by created desc limit 8")]
 except Exception as e:result["db_error"]=type(e).__name__
print(json.dumps(result,ensure_ascii=False))

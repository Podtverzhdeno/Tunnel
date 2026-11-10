"""Compact model-facing Hermes tool catalogue without changing canonical payload.

The queue keeps full function schemas, all system/developer/user messages,
and the original tool results. Only the *browser model's one-message view*
uses shorter descriptions, with full schema validation at the Relay boundary.
"""
from __future__ import annotations
import copy
import json
import re
from model_news_prefetch import maybe_prefetch_news

# More detail for operational tools; minimize decorative prose, not permissions.
DESCRIPTIONS={
    "terminal":340,
    "browser_navigate":500,
    "browser_snapshot":260,
    "browser_click":220,
    "browser_type":170,
    "browser_press":145,
    "skill_view":245,
    "send_message":390,
    "delegate_task":340,
    "cronjob":280,
    "memory":220,
    "skill_manage":300,
    "process":210,
    "patch":240,
    "write_file":220,
    "read_file":230,
}
KEYWORDS_TO_PRESERVE={"type","properties","required","enum","const","items",
    "additionalProperties","patternProperties","allOf","anyOf","oneOf",
    "not","if","then","else","$ref","$defs","definitions",
    "minLength","maxLength","minimum","maximum","exclusiveMinimum",
    "exclusiveMaximum","maxItems","minItems","uniqueItems",
    "maxProperties","minProperties","pattern","multipleOf","default",
    "additionalItems","prefixItems","contains","minContains","maxContains",
    "dependentRequired","dependentSchemas","propertyNames",
}
# Unknown keys are retained conservatively, rather than erasing a constraint.
INFO_KEYS={"title","examples","example","deprecated","$comment","description"}

def _trim(text,limit):
    if not isinstance(text,str):return ""
    text=re.sub(r"\s+"," ",text).strip()
    if len(text)<=limit:return text
    # End on a whole word where possible; never invent replacement instructions.
    prefix=text[:limit].rsplit(" ",1)[0]
    return (prefix if len(prefix)>limit//2 else text[:limit]).rstrip()+"…"

def _schema_view(node,detailed=False):
    if isinstance(node,dict):
        out={}
        for k,v in node.items():
            if k in INFO_KEYS:
                if k=="description" and detailed:
                    description=_trim(v,56)
                    if description:out["description"]=description
                continue
            # Map keys are arbitrary parameter/definition names, not metadata.
            if k in {"properties","patternProperties","$defs","definitions","dependentSchemas"} and isinstance(v,dict):
                out[k]={name:_schema_view(schema,detailed=detailed) for name,schema in v.items()}
            elif k=="dependencies" and isinstance(v,dict):
                out[k]={name:_schema_view(schema,detailed=detailed) if isinstance(schema,(dict,bool))
                        else copy.deepcopy(schema) for name,schema in v.items()}
            elif k in {"allOf","anyOf","oneOf","prefixItems"} and isinstance(v,list):
                out[k]=[_schema_view(schema,detailed=detailed) for schema in v]
            elif k=="items" and isinstance(v,list):
                out[k]=[_schema_view(schema,detailed=detailed) for schema in v]
            elif k in {"items","additionalProperties","additionalItems","contains","propertyNames",
                       "not","if","then","else","unevaluatedProperties","unevaluatedItems","contentSchema"}:
                out[k]=_schema_view(v,detailed=detailed)
            else:
                # Literal enum/const/default objects and extension values must
                # remain exact, even when their keys resemble schema metadata.
                out[k]=copy.deepcopy(v)
        return out
    return copy.deepcopy(node)

def _tool_view(tool,focused=False):
    fn=tool["function"]
    name=fn["name"]
    limit=DESCRIPTIONS.get(name,170) if focused else max(110,min(245,DESCRIPTIONS.get(name,150)))
    purpose=_trim(fn.get("description",""),limit)
    result={"name":name}
    if purpose:result["purpose"]=purpose
    result["parameters"]=_schema_view(fn.get("parameters",{"type":"object"}),detailed=focused)
    return result

def _focused_names(request):
    messages=request.get("messages",[])
    user=next((m.get("content","") for m in reversed(messages) if m.get("role")=="user"),"")
    if not isinstance(user,str):user=""
    user=user.lower()
    focus={"terminal","skill_view","browser_navigate"}
    if any(x in user for x in ("новост","rss","llm","исследован","статей","publication","news","research")):
        focus|={"browser_snapshot","skills_list","read_file"}
    if any(x in user for x in ("файл","код","проект","github","git","patch","test","скрипт","script")):
        focus|={"read_file","write_file","patch","search_files"}
    if any(x in user for x in ("напомни","cron","расписан","schedule")):
        focus.add("cronjob")
    if any(x in user for x in ("отправ","сообщен","send","message")):
        focus.add("send_message")
    for m in messages[-8:]:
        if m.get("role")=="assistant":
            for c in m.get("tool_calls") or []:
                n=(c.get("function") or {}).get("name")
                if isinstance(n,str):focus.add(n)
    choice=request.get("tool_choice")
    if isinstance(choice,dict):
        n=(choice.get("function") or {}).get("name")
        if isinstance(n,str):focus.add(n)
    return focus

def _compact_old_results(messages):
    """Preserve the newest THREE tool outputs, trim only stale massive ones."""
    last=[i for i,m in enumerate(messages) if m.get("role")=="tool"]
    protected=set(last[-3:])
    out=[]
    for i,m in enumerate(messages):
        if i in protected or m.get("role")!="tool":
            out.append(m)
            continue
        c=m.get("content")
        if not isinstance(c,str) or len(c)<3600:
            out.append(m)
            continue
        # Old tool output is never summarised as if the original remained visible.
        replacement=dict(m)
        replacement["content"]=json.dumps({
            "_hermes_model_view_omitted":True,
            "notice":"Older tool result omitted to reduce latency; call the tool again if specific details are needed.",
            "original_characters":len(c),
            "head":c[:450],
            "tail":c[-150:],
        },ensure_ascii=False,separators=(",",":"))
        out.append(replacement)
    return out

def model_request_for_web(request):
    """Return a model-only view. Never mutate request or its nested schemas."""
    result={k:v for k,v in request.items() if k not in ("tools","messages")}
    result["messages"]=_compact_old_results(request["messages"])
    focus=_focused_names(request)
    result["tool_catalog"]=[_tool_view(t,focused=t["function"]["name"] in focus) for t in request.get("tools",[])]
    # Read-only evidence is model-only: canonical Hermes payload, fingerprint and
    # full tool schemas stay unchanged. Fallback to offered tools if feeds fail.
    evidence=maybe_prefetch_news(request)
    if evidence is not None:
        result["prefetched_public_news"]=evidence
    return result

"""Validate tool-call argument JSON against the schemas actually offered by Hermes.

No tool executes here. Reject unsupported assertion keywords rather than silently
accept a potentially dangerous tool invocation.
"""
import json
import math
import re
from fractions import Fraction

class ToolSchemaError(ValueError):
    pass

META={"title","description","default","examples","deprecated","readOnly","writeOnly",
      "$schema","$id","$comment","format"}
ASSERT={"type","properties","required","additionalProperties","items","enum","const",
        "minLength","maxLength","pattern","minimum","maximum","exclusiveMinimum",
        "exclusiveMaximum","minItems","maxItems","minProperties","maxProperties",
        "allOf","anyOf","oneOf","not","$ref","$defs","definitions",
        "patternProperties","uniqueItems","multipleOf","if","then","else"}
SUPPORTED=META|ASSERT

def _fail(msg):
    raise ToolSchemaError(msg)

def _same(a,b):
    return json.dumps(a,sort_keys=True,ensure_ascii=False,allow_nan=False)==json.dumps(b,sort_keys=True,ensure_ascii=False,allow_nan=False)

def _type(x,t):
    return {
      "object": lambda:isinstance(x,dict),
      "array": lambda:isinstance(x,list),
      "string":lambda:isinstance(x,str),
      "integer":lambda:type(x) is int,
      "number":lambda:type(x) is int or (type(x) is float and math.isfinite(x)),
      "boolean":lambda:type(x) is bool,
      "null":lambda:x is None
    }.get(t,lambda:False)()

def _local_ref(root,ref):
    if not isinstance(ref,str) or not (ref=="#" or ref.startswith("#/")):
        _fail("external_schema_ref_forbidden")
    node=root
    if ref=="#":return node
    for bit in ref[2:].split("/"):
        bit=bit.replace("~1","/").replace("~0","~")
        if not isinstance(node,dict) or bit not in node:_fail("unknown_schema_ref")
        node=node[bit]
    return node

def _matches(x,s,root,depth):
    try:_validate(x,s,root,depth);return True
    except ToolSchemaError:return False

def _validate(x,s,root,depth=0):
    if depth>32:_fail("schema_depth_limit")
    if type(s) is bool:
        if not s:_fail("false_schema")
        return
    if not isinstance(s,dict):_fail("invalid_schema")
    if any(k not in SUPPORTED for k in s):_fail("unsupported_schema_keyword")
    if "$ref" in s:_validate(x,_local_ref(root,s["$ref"]),root,depth+1)
    if "type" in s:
        types=s["type"]
        if isinstance(types,str):types=[types]
        if not isinstance(types,list) or not types or any(type(t) is not str for t in types):
            _fail("invalid_schema_type")
        if not any(_type(x,t) for t in types):_fail("invalid_argument_type")
    if "const" in s and not _same(x,s["const"]):_fail("invalid_argument_const")
    if "enum" in s:
        if not isinstance(s["enum"],list) or not any(_same(x,v) for v in s["enum"]):
            _fail("invalid_argument_enum")
    for clause in s.get("allOf",[]):_validate(x,clause,root,depth+1)
    if "anyOf" in s and not any(_matches(x,a,root,depth+1) for a in s["anyOf"]):
        _fail("anyof_invalid")
    if "oneOf" in s and sum(_matches(x,a,root,depth+1) for a in s["oneOf"])!=1:
        _fail("oneof_invalid")
    if "not" in s and _matches(x,s["not"],root,depth+1):_fail("not_invalid")
    if "if" in s:
        key="then" if _matches(x,s["if"],root,depth+1) else "else"
        if key in s:_validate(x,s[key],root,depth+1)
    if isinstance(x,dict):
        props=s.get("properties",{})
        patterns=s.get("patternProperties",{})
        if not isinstance(props,dict) or not isinstance(patterns,dict):_fail("invalid_props_schema")
        if any(k not in x for k in s.get("required",[])):_fail("required_missing")
        if len(x)<s.get("minProperties",0) or len(x)>s.get("maxProperties",10**9):
            _fail("properties_count")
        for k,v in x.items():
            applied=False
            if k in props:_validate(v,props[k],root,depth+1);applied=True
            for pat,sub in patterns.items():
                try:matching=re.search(pat,k) is not None
                except re.error:_fail("schema_invalid_pattern")
                if matching:_validate(v,sub,root,depth+1);applied=True
            if not applied:_validate(v,s.get("additionalProperties",True),root,depth+1)
    if isinstance(x,list):
        if len(x)<s.get("minItems",0) or len(x)>s.get("maxItems",10**9):
            _fail("items_count")
        if s.get("uniqueItems",False):
            if len({json.dumps(i,sort_keys=True,ensure_ascii=False,allow_nan=False) for i in x})!=len(x):
                _fail("items_duplicate")
        items=s.get("items",True)
        if isinstance(items,list):
            for v,sub in zip(x,items):_validate(v,sub,root,depth+1)
            for v in x[len(items):]:_validate(v,s.get("additionalItems",True),root,depth+1)
        else:
            for v in x:_validate(v,items,root,depth+1)
    if isinstance(x,str):
        if len(x)<s.get("minLength",0) or len(x)>s.get("maxLength",10**9):
            _fail("string_length")
        if "pattern" in s:
            try:matched=re.search(s["pattern"],x) is not None
            except re.error:_fail("schema_invalid_pattern")
            if not matched:_fail("string_pattern")
    if type(x) in (int,float):
        if "minimum" in s and x<s["minimum"]:_fail("min")
        if "maximum" in s and x>s["maximum"]:_fail("max")
        if "exclusiveMinimum" in s and x<=s["exclusiveMinimum"]:_fail("ex_min")
        if "exclusiveMaximum" in s and x>=s["exclusiveMaximum"]:_fail("ex_max")
        if "multipleOf" in s:
            step=s["multipleOf"]
            if (type(step) not in (int,float) or step<=0 or
                    (type(step) is float and not math.isfinite(step))):
                _fail("invalid_multiple_of_schema")
            # JSON numbers are decimal values. Binary float tolerances can
            # accept a value just outside the offered constraint.
            if (Fraction(str(x))/Fraction(str(step))).denominator!=1:
                _fail("multiple_of")

def validate_offered_tool_arguments(tools,name,arguments):
    if not isinstance(arguments,dict):_fail("arguments_not_object")
    try:json.dumps(arguments,ensure_ascii=False,allow_nan=False)
    except (TypeError,ValueError,RecursionError):_fail("arguments_not_json")
    matched=[t["function"] for t in tools if isinstance(t,dict) and
             isinstance(t.get("function"),dict) and t["function"].get("name")==name]
    if len(matched)!=1:_fail("not_unique_offered_tool")
    schema=matched[0].get("parameters",{"type":"object"})
    try:
        _validate(arguments,schema,schema)
    except ToolSchemaError:
        raise
    except (TypeError,ValueError,KeyError,ZeroDivisionError,OverflowError,RecursionError) as exc:
        # A malformed offered schema is a rejected invocation, never a crashed
        # HTTP handler leaving Hermes waiting on a saved request indefinitely.
        raise ToolSchemaError("invalid_tool_schema") from exc

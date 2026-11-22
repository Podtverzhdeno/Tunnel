"""Read-only audit of installed tunnel-client 0.0.19 CLI help. Do not log IDs/keys."""
from __future__ import annotations
import re, subprocess, json
from .observer_tunnel import find_cli

def main():
    cli=find_cli()
    result={}
    for label,args in [('connect',['runtimes','connect','--help']),('list',['runtimes','list','--help']),('global',['--help'])]:
        p=subprocess.run([str(cli),*args],capture_output=True,timeout=9)
        data=(p.stdout+p.stderr).decode('utf-8','replace').splitlines()
        result[label]={'returncode':p.returncode,'match_lines':[line.strip()[:500] for line in data if any(x in line.lower() for x in ['--alias','--runtime-api-key','--mcp-server-url','--tunnel-id','--header','--profile','--json','connect'])][:36]}
    print(json.dumps(result,ensure_ascii=False))
if __name__=='__main__':main()

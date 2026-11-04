"""Setup, diagnostics and local MCP server entrypoint. Python >= 3.10, no pip."""
import argparse
import json
import os
import sys
from pathlib import Path
from workspace_mcp.config import DEFAULT_CONFIG,DEFAULT_DATA,NAME,Settings,WorkspaceError
from workspace_mcp.tools import ToolRegistry
from workspace_mcp.http_server import serve,dispatch


def save_config(path,obj):
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix(path.suffix+'.tmp')
    tmp.write_text(json.dumps(obj,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    os.replace(tmp,path)


def main():
    parser=argparse.ArgumentParser(description='Local Workspace MCP administration')
    parser.add_argument('--config',default=str(DEFAULT_CONFIG))
    sub=parser.add_subparsers(dest='action',required=True)
    sub.add_parser('init',help='Create empty configuration; will not overwrite')
    add=sub.add_parser('add-root',help='Grant one folder explicit permissions')
    add.add_argument('--name',required=True)
    add.add_argument('--path',required=True)
    add.add_argument('--write',action='store_true')
    add.add_argument('--execute',action='store_true')
    add.add_argument('--git',action='store_true')
    sub.add_parser('list-roots',help='Show configured folders')
    sub.add_parser('doctor',help='Local configuration/tool diagnostics')
    run=sub.add_parser('serve',help='Start MCP Streamable HTTP on loopback')
    run.add_argument('--port',type=int,default=8765)
    run.add_argument('--profile', choices=('observer','legacy'), default='observer',
                     help='observer: read-only, explicit allowlist; legacy: UNSAFE full tools for a separate trusted operator plane only')
    args=parser.parse_args()
    path=Path(args.config).expanduser().resolve()
    try:
        if args.action=='init':
            if path.exists():raise WorkspaceError(f'Config already exists: {path}')
            save_config(path,DEFAULT_DATA)
            print(f'Created {path}. Add a root using: python manage.py add-root --name projects --path "D:\\Projects" --write --execute --git')
        elif args.action=='add-root':
            if not NAME.fullmatch(args.name):raise WorkspaceError('Invalid workspace alias')
            folder=Path(args.path).expanduser().resolve(strict=True)
            if not folder.is_dir() or folder==Path(folder.anchor) or folder==Path.home().resolve():
                raise WorkspaceError('Choose an existing subdirectory, not an entire drive or home directory')
            if not path.exists():raise WorkspaceError('Run init first')
            data=json.loads(path.read_text(encoding='utf-8'))
            if any(r['name']==args.name or Path(r['path']).resolve()==folder for r in data['roots']):
                raise WorkspaceError('Workspace name/path already used')
            data['roots'].append({'name':args.name,'path':str(folder),'read':True,'write':args.write,'execute':args.execute,'git':args.git})
            save_config(path,data)
            print(f'Granted {args.name} -> {folder}; write={args.write}, execute={args.execute}, git={args.git}. Restart server if already running.')
        elif args.action=='list-roots':
            data=json.loads(path.read_text(encoding='utf-8'))
            print(json.dumps(data['roots'],indent=2,ensure_ascii=False))
        else:
            settings=Settings(config_file=path)
            profile=getattr(args,'profile','observer')
            registry=ToolRegistry(settings,profile=profile)
            # Additional host extensions must never be loaded into the
            # observer plane. Legacy is for isolated operator use only,
            # not for scheduled agents or network tunnel exposure.
            if profile=='legacy' and args.action=='serve':
                # Loopback + a cloud tunnel is not authenticated actor identity.
                # The privileged legacy registry must not be published by this
                # HTTP entrypoint until a separate OS-protected broker exists.
                raise WorkspaceError('LEGACY_HTTP_PROFILE_DISABLED_WITHOUT_TRUSTED_HOST_BROKER')
            if args.action=='doctor':
                print(json.dumps({'config':str(settings.config_file),'state':str(settings.state_dir),
                    'tools':len(registry.tools),'roots':registry.fs.workspaces(),
                    'mcp_initialize':dispatch(registry,{'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-11-25'}})['result']['protocolVersion']},
                    indent=2,ensure_ascii=False))
            elif args.action=='serve':serve(registry,args.port)
    except (WorkspaceError,ValueError,OSError) as exc:
        print(f'ERROR: {exc}',file=sys.stderr)
        return 1
    return 0

if __name__=='__main__':sys.exit(main())

"""P085 host-side capability reduction tests for the imported host mirror.

This tests a restricted process profile, NOT authenticated principal identity.
No live host or tunnel is started. All network tests bind an ephemeral port.
"""
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.request import Request, urlopen

from workspace_mcp.config import Settings, WorkspaceError
from workspace_mcp.tools import OBSERVATION_TOOLS, ToolRegistry
from workspace_mcp.http_server import make_handler, dispatch


class HostProfileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base=Path(self.tmp.name)
        self.base=base
        root=base/'work'
        root.mkdir()
        (root/'visible.txt').write_text('nonsecret fixture',encoding='utf-8')
        config=base/'config.json'
        self.config_path=config
        config.write_text(json.dumps({'roots':[{'name':'work','path':str(root),
                                                  'read':True,'write':True,
                                                  'execute':True,'git':True}]}),
                          encoding='utf-8')
        self.settings=Settings(config_file=config,state_dir=base/'state')
        self.registry=ToolRegistry(self.settings,profile='observer')
        self.root=root

    def test_observer_has_exact_read_only_allowlist(self):
        names={x['name'] for x in self.registry.list()}
        self.assertEqual(names,OBSERVATION_TOOLS)
        self.assertTrue(all(x['annotations']['readOnlyHint'] is True
                            for x in self.registry.list()))
        self.assertEqual(len(names),6)
        self.assertNotIn('job_output',names)
        self.assertNotIn('git_status',names)

    def test_observer_view_masks_ineffective_permissions(self):
        roots=self.registry.call('list_workspaces',{})
        self.assertEqual(len(roots),1)
        self.assertTrue(roots[0]['read'])
        self.assertFalse(roots[0]['write'])
        self.assertFalse(roots[0]['execute'])
        self.assertFalse(roots[0]['git'])

    def test_observer_can_read_bounded_workspace_text(self):
        result=self.registry.call('read_file',{'workspace':'work',
                                                'path':'visible.txt'})
        self.assertIn('nonsecret fixture', result['content'])

    def test_all_privileged_tools_rejected_before_callback(self):
        forbidden=[
            'write_file','replace_text','move_path','trash_path',
            'restore_trash','create_directory','run_script',
            'run_command','run_tests','cancel_job','git_add',
            'git_commit','git_push','git_fetch','job_output',
            'list_jobs','git_status','git_diff','git_log','read_binary',
        ]
        file=self.root/'visible.txt'
        before=file.read_bytes()
        for name in forbidden:
            with self.subTest(name=name):
                self.assertFalse(self.registry.available(name))
                with self.assertRaisesRegex(WorkspaceError,'unavailable'):
                    self.registry.call(name, {})
        self.assertEqual(file.read_bytes(),before)

    def test_bad_profile_is_not_accepted(self):
        for profile in ('architect-reviewer','developer-1','owner',None,True):
            with self.subTest(profile=profile):
                with self.assertRaises(WorkspaceError):
                    ToolRegistry(self.settings,profile=profile)

    def test_extension_registration_cannot_bypass_allowlist(self):
        invoked=[]
        with self.assertRaisesRegex(WorkspaceError,'sealed'):
            self.registry.add('evil_unadvertised',lambda:invoked.append(1),
                              'stub harmless test',read_only=True)
        self.assertFalse(self.registry.available('evil_unadvertised'))
        with self.assertRaises(WorkspaceError):
            self.registry.call('evil_unadvertised',{})
        # Previously an extension could replace read_file and claim a
        # read-only hint, then execute a privileged callable on an allowed name.
        with self.assertRaisesRegex(WorkspaceError,'Duplicate'):
            self.registry.add('read_file',lambda:invoked.append(2),
                              'malicious replacement',read_only=True)
        with self.assertRaises(TypeError):
            self.registry.tools['read_file']={'func':lambda:invoked.append(3)}
        with self.assertRaises(TypeError):
            self.registry.tools['read_file']['func']=lambda:invoked.append(4)
        self.assertEqual(invoked,[])
        self.assertTrue(self.registry.available('read_file'))
        self.assertIn('nonsecret fixture',
                      self.registry.call('read_file',{'workspace':'work',
                                                      'path':'visible.txt'})['content'])

    def test_registry_default_is_observer_not_privileged(self):
        default=ToolRegistry(self.settings)
        self.assertEqual(default.profile,'observer')
        self.assertNotIn('run_script',{x['name'] for x in default.list()})

    def test_observer_cli_without_bearer_fails_closed(self):
        cfg=self.base/'observer-cli.json'
        cfg.write_text(json.dumps({'roots':[{
            'name':'work','path':str(self.root),'read':True,
            'write':False,'execute':False,'git':False}]}),encoding='utf-8')
        root=Path(__file__).resolve().parents[1]
        env=os.environ.copy()
        env.pop('LOCAL_MCP_OBSERVER_BEARER',None)
        result=subprocess.run([
            sys.executable,str(root/'manage.py'),
            '--config',str(cfg),'serve','--profile','observer',
            '--port','19467'],
            cwd=root,stdin=subprocess.DEVNULL,env=env,
            capture_output=True,text=True,encoding='utf-8',
            errors='replace',timeout=8)
        self.assertNotEqual(result.returncode,0)
        self.assertIn('OBSERVER_HTTP_AUTH_TOKEN_REQUIRED',result.stderr)

    def test_legacy_disallows_duplicate_tool_hijacking(self):
        registry=ToolRegistry(self.settings,profile='legacy')
        with self.assertRaisesRegex(WorkspaceError,'Duplicate'):
            registry.add('read_file',lambda:None,'overwrite',read_only=True)

    def test_legacy_profile_cannot_be_served_over_http(self):
        root=Path(__file__).resolve().parents[1]
        result=subprocess.run([
            sys.executable,str(root/'manage.py'),
            '--config',str(self.settings.config_file),
            'serve','--profile','legacy','--port','19465'],
            cwd=root,stdin=subprocess.DEVNULL,
            capture_output=True,text=True,encoding='utf-8',
            errors='replace',timeout=8)
        self.assertNotEqual(result.returncode,0)
        self.assertIn('LEGACY_HTTP_PROFILE_DISABLED',result.stderr)


    def test_http_tools_list_and_direct_hidden_tool_call(self):
        # Live observer HTTP must use a *distinct* read-only grant config.
        # The default workspace fixture deliberately has write/execute flags
        # enabled, and should never become an exposed HTTP listener.
        with self.assertRaisesRegex(WorkspaceError,'OBSERVER_ROOT_MUST_BE_READ_ONLY'):
            make_handler(self.registry)
        cfg=self.base/'observer-config.json'
        cfg.write_text(json.dumps({'roots':[{'name':'work','path':str(self.root),
                                              'read':True,'write':False,
                                              'execute':False,'git':False}]}),
                       encoding='utf-8')
        safe=ToolRegistry(Settings(config_file=cfg,state_dir=self.base/'observer-state'))
        token='T'*43
        server=ThreadingHTTPServer(
            ('127.0.0.1',0),make_handler(safe,observer_token=token))
        thread=threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        url='http://127.0.0.1:'+str(server.server_port)+'/mcp'
        def rpc(message):
            req=Request(url,data=json.dumps(message).encode('utf-8'),
                        headers={'Content-Type':'application/json',
                                 'Authorization':'Bearer '+token})
            with urlopen(req,timeout=5) as response:
                return json.load(response)
        items=rpc({'jsonrpc':'2.0','id':1,'method':'tools/list'})
        self.assertEqual({i['name'] for i in items['result']['tools']},OBSERVATION_TOOLS)
        target=self.root/'would_write.txt'
        blocked=rpc({'jsonrpc':'2.0','id':2,'method':'tools/call','params':{
            'name':'write_file','arguments':{'workspace':'work',
                                            'path':'would_write.txt',
                                            'content':'danger'}}})
        self.assertEqual(blocked['error']['code'],-32602)
        self.assertFalse(target.exists())
        direct=rpc({'jsonrpc':'2.0','id':3,'method':'tools/call','params':{
            'name':'run_script','arguments':{'workspace':'work','path':'anything.py',
                                              'caller_role':'architect-reviewer'}}})
        self.assertEqual(direct['error']['code'],-32602)
        self.assertFalse(target.exists())

    def test_observer_token_required_at_handler_construction(self):
        cfg=self.base/'secure-ro-config.json'
        cfg.write_text(json.dumps({'roots':[{
            'name':'work','path':str(self.root),'read':True,
            'write':False,'execute':False,'git':False}]}),
            encoding='utf-8')
        readonly=ToolRegistry(Settings(config_file=cfg,
                                        state_dir=self.base/'secure-state'))
        for token in (None,'','tiny','insecure token','A'*42,
                      'A'*129,'\n'+'A'*42):
            with self.subTest(token=token):
                with self.assertRaisesRegex(WorkspaceError,
                                            'OBSERVER_HTTP_AUTH_TOKEN_REQUIRED'):
                    make_handler(readonly,observer_token=token)
        # A correctly formatted token is a transport credential, not a
        # verified reviewer role or authority for mutation.
        make_handler(readonly,observer_token='A'*43)

    def test_direct_legacy_http_listener_refused(self):
        legacy=ToolRegistry(self.settings,profile='legacy')
        with self.assertRaisesRegex(WorkspaceError,'PRIVILEGED_HTTP_PROFILE_DENIED'):
            make_handler(legacy)

    def test_observer_config_or_state_inside_visible_root_denied(self):
        localcfg=self.root/'visible-config.json'
        localcfg.write_text(json.dumps({'roots':[{
            'name':'work','path':str(self.root),'read':True,
            'write':False,'execute':False,'git':False}]}),encoding='utf-8')
        local=ToolRegistry(Settings(config_file=localcfg,
                                    state_dir=self.base/'safe-state'))
        with self.assertRaisesRegex(WorkspaceError,
                                    'OBSERVER_CONTROL_PATH_INSIDE_EXPOSED_ROOT'):
            make_handler(local)
        externalcfg=self.base/'no-write.json'
        externalcfg.write_text(localcfg.read_text(encoding='utf-8'),encoding='utf-8')
        localstate=ToolRegistry(Settings(config_file=externalcfg,
                                         state_dir=self.root/'state'))
        with self.assertRaisesRegex(WorkspaceError,
                                    'OBSERVER_CONTROL_PATH_INSIDE_EXPOSED_ROOT'):
            make_handler(localstate)

    def test_legacy_mode_is_compatible_but_not_trusted_identity(self):
        legacy=ToolRegistry(self.settings,profile='legacy')
        self.assertIn('run_script',{x['name'] for x in legacy.list()})
        self.assertIn('write_file',{x['name'] for x in legacy.list()})
        self.assertFalse(self.registry.available('run_script'))
        self.assertTrue(legacy.available('run_script'))


if __name__=='__main__':
    unittest.main()

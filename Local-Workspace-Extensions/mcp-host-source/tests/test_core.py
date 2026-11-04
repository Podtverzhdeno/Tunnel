import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from workspace_mcp.config import Settings,WorkspaceError
from workspace_mcp.files import sha
from workspace_mcp.tools import ToolRegistry
from workspace_mcp.http_server import dispatch


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name)
        self.folder=self.base/'project';self.folder.mkdir()
        self.state=self.base/'state'
        self.cfg=self.base/'config.json'
        self.cfg.write_text(json.dumps({'roots':[{'name':'work','path':str(self.folder),'read':True,'write':True,'execute':True,'git':True}],
            'limits':{'max_job_seconds':30},'security':{'allow_git_remote':False,'allowed_commands':['python','git']}}))
        self.settings=Settings(config_file=self.cfg,state_dir=self.state)
        self.registry=ToolRegistry(self.settings)
        self.f=self.registry.fs

    def test_workspaces(self):
        self.assertEqual(self.f.workspaces()[0]['name'],'work')
        self.assertGreaterEqual(len(self.registry.tools),25)

    def test_guard_parent_absolute_and_drive(self):
        for p in ('../x','a/../../x','/etc/passwd','C:\\Windows\\system.ini','\\\\server\\share\\a','a//b','a\\..\\x'):
            with self.subTest(p=p),self.assertRaises(WorkspaceError):self.f.read('work',p)

    def test_block_confidential(self):
        for p in ('.env','dir/.env.prod','id_rsa','top.pem','.git/config','.ssh/id_ed25519','secrets.json'):
            with self.subTest(p=p),self.assertRaises(WorkspaceError):self.f.write('work',p,'oops')
        self.f.write('work','.env.example','no secrets')

    def test_write_read_hash_version(self):
        a=self.f.write('work','hello.txt','hello\nworld\n')
        self.assertEqual(a['bytes'],12)
        text=self.f.read('work','hello.txt')
        self.assertIn('1: hello',text['content'])
        self.assertEqual(text['sha256'],a['sha256'])
        with self.assertRaises(WorkspaceError):self.f.write('work','hello.txt','overwrite')
        with self.assertRaises(WorkspaceError):self.f.write('work','hello.txt','overwrite',overwrite=True,expected_sha256='0'*64)
        b=self.f.write('work','hello.txt','new',overwrite=True,expected_sha256=a['sha256'])
        self.assertEqual((self.folder/'hello.txt').read_text(),'new')
        self.assertNotEqual(a['sha256'],b['sha256'])

    def test_patch_unique(self):
        a=self.f.write('work','a.txt','abc abc')
        with self.assertRaises(WorkspaceError):self.f.replace_text('work','a.txt','abc','def')
        self.f.replace_text('work','a.txt','abc abc','xyz',a['sha256'])
        self.assertEqual((self.folder/'a.txt').read_text(),'xyz')

    def test_binary(self):
        data=b'\x00\xff\x02';import base64
        self.f.write('work','a.bin',base64.b64encode(data).decode(),base64_input=True)
        self.assertEqual(base64.b64decode(self.f.read_binary('work','a.bin')['base64']),data)
        with self.assertRaises(WorkspaceError):self.f.read('work','a.bin')

    def test_tree_and_search(self):
        self.f.mkdir('work','src')
        self.f.write('work','src/a.py','print("needle")\n')
        self.assertEqual(len(self.f.search('work','needle')['matches']),1)
        self.assertEqual(self.f.tree('work')['entries'][0]['path'],'src')
        self.assertEqual(self.f.list_dir('work','src')['entries'][0]['name'],'a.py')

    def test_trash_restore(self):
        self.f.write('work','a.txt','a')
        i=self.f.trash_path('work','a.txt')['trash_id']
        self.assertFalse((self.folder/'a.txt').exists())
        self.f.restore(i)
        self.assertTrue((self.folder/'a.txt').exists())
        with self.assertRaises(WorkspaceError):self.f.restore(i)

    def test_mkdir_move(self):
        self.f.mkdir('work','src')
        self.f.write('work','a.txt','x')
        self.f.move('work','a.txt','src/b.txt')
        self.assertTrue((self.folder/'src/b.txt').is_file())
        with self.assertRaises(WorkspaceError):self.f.move('work','src','src/inside')

    @unittest.skipUnless(hasattr(os,'symlink'),'symlinks unavailable')
    def test_symlinks(self):
        outside=self.base/'outside.txt';outside.write_text('private')
        try:(self.folder/'out').symlink_to(outside)
        except OSError:self.skipTest('symlink permissions unavailable')
        with self.assertRaises(WorkspaceError):self.f.read('work','out')
        (self.folder/'.env').write_text('SENSITIVE=secret')
        (self.folder/'alias').symlink_to(self.folder/'.env')
        with self.assertRaises(WorkspaceError):self.f.read('work','alias')

    def test_config_state_block(self):
        self.assertFalse((self.folder/'.local-mcp-state').exists())
        with self.assertRaises(WorkspaceError):self.f.read('work','.local-mcp-state/a')

    def test_empty_workspace_rejected(self):
        data=json.loads(self.cfg.read_text());data['roots']=[]
        self.cfg.write_text(json.dumps(data))
        with self.assertRaises(WorkspaceError):Settings(config_file=self.cfg,state_dir=self.state)

    def test_readonly_workspace_rejects_write(self):
        data=json.loads(self.cfg.read_text());data['roots'][0]['write']=False
        self.cfg.write_text(json.dumps(data))
        locked=ToolRegistry(Settings(config_file=self.cfg,state_dir=self.state))
        with self.assertRaises(WorkspaceError):locked.fs.write('work','x.txt','x')
        self.assertEqual(locked.fs.list_dir('work')['entries'],[])

    def test_overwrite_rejects_mismatched_binary_hash(self):
        import base64
        a=self.f.write('work','blob.bin',base64.b64encode(b'one').decode(),base64_input=True)
        with self.assertRaises(WorkspaceError):self.f.write('work','blob.bin',base64.b64encode(b'two').decode(),overwrite=True,expected_sha256='bad',base64_input=True)
        self.assertEqual((self.folder/'blob.bin').read_bytes(),b'one')

    def test_reject_arg_schema(self):
        with self.assertRaises(WorkspaceError):self.registry.call('read_file',{'workspace':'work','path':'x','unknown':1})
        with self.assertRaises(WorkspaceError):self.registry.call('read_file',{'workspace':'work'})
        with self.assertRaises(WorkspaceError):self.registry.call('read_file',{'workspace':0,'path':'x'})

    def test_protect_git_repository_from_trash(self):
        (self.folder/'repo').mkdir()
        (self.folder/'repo'/'.git').mkdir()
        with self.assertRaises(WorkspaceError):self.f.trash_path('work','repo',recursive=True)

    def test_protocol(self):
        result=dispatch(self.registry,{'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-11-25'}})
        self.assertEqual(result['result']['protocolVersion'],'2025-11-25')
        tools=dispatch(self.registry,{'jsonrpc':'2.0','id':2,'method':'tools/list'})['result']['tools']
        self.assertIn('read_file',[x['name'] for x in tools])
        res=dispatch(self.registry,{'jsonrpc':'2.0','id':3,'method':'tools/call','params':{'name':'list_workspaces','arguments':{}}})
        self.assertFalse(res['result']['isError'])
        res=dispatch(self.registry,{'jsonrpc':'2.0','id':4,'method':'tools/call','params':{'name':'read_file','arguments':{'workspace':'work','path':'../no'}}})
        self.assertTrue(res['result']['isError'])
        self.assertIsNone(dispatch(self.registry,{'jsonrpc':'2.0','method':'notifications/initialized'}))

    def test_run_script_logs(self):
        self.f.write('work','go.py','print("JOB OK", flush=True)\n')
        job=self.registry.jobs.script('work','go.py')
        for i in range(150):
            status=self.registry.jobs.status(job['job_id'])
            if status['status']!='running':break
            time.sleep(.02)
        self.assertEqual(status['status'],'completed',status)
        self.assertIn('JOB OK',self.registry.jobs.output(job['job_id'])['text'])
        self.assertTrue(self.registry.jobs._job(job['job_id'])['done'].wait(2))

    def test_script_executable_permission(self):
        data=json.loads(self.cfg.read_text());data['roots'][0]['execute']=False
        self.cfg.write_text(json.dumps(data))
        locked=ToolRegistry(Settings(config_file=self.cfg,state_dir=self.state))
        self.f.write('work','a.py','print(1)')
        with self.assertRaises(WorkspaceError):locked.jobs.script('work','a.py')

    def test_disallowed_command(self):
        with self.assertRaises(WorkspaceError):self.registry.jobs.run('work','powershell',['-Command','ls'])
        with self.assertRaises(WorkspaceError):self.registry.jobs.run('work','/bin/sh',['-c','true'])

    @unittest.skipUnless(subprocess.run(['git','--version'],stdout=subprocess.DEVNULL).returncode==0,'git unavailable')
    def test_git(self):
        def git(*args):
            return subprocess.run(['git','-C',str(self.folder),*args],stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=True)
        git('init','-q');git('config','user.email','test@example.com');git('config','user.name','Test')
        self.f.write('work','a.txt','first')
        self.assertIn('a.txt',self.registry.git.status('work')['output'])
        self.registry.git.add('work',files=['a.txt'])
        commit=self.registry.git.commit('work','initial')
        self.assertEqual(commit['returncode'],0,commit)
        self.assertIn('initial',self.registry.git.log('work')['output'])
        with self.assertRaises(WorkspaceError):self.registry.git.push('work')


if __name__=='__main__':unittest.main()

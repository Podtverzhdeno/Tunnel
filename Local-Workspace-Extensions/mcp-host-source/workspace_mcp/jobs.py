"""Bounded subprocess supervisor. Jobs run locally with current OS-user permissions."""
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from .config import WorkspaceError
from .security import safe_path

# Windows batch/.cmd scripts are interpreted by cmd.exe even with shell=False.
# Exclude metacharacters from all args to avoid cmd injection via npm.cmd/mvn.cmd.
WINDOWS_META = re.compile(r'[&|<>^%"!()\r\n]')
SAFE_EXECUTABLE = re.compile(r'^[a-zA-Z][a-zA-Z0-9_.-]{0,50}$')


def process_tree_kill(proc):
    if proc.poll() is not None:
        return
    if os.name == 'nt':
        subprocess.run(['taskkill','/PID',str(proc.pid),'/T','/F'], stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=10,check=False)
        if proc.poll() is None:
            proc.kill()
    else:
        try: os.killpg(proc.pid,signal.SIGKILL)
        except ProcessLookupError: pass


class Jobs:
    def __init__(self,settings,audit):
        self.s=settings
        self.audit=audit
        self.lock=threading.RLock()
        self.jobs={}
        self.folder=settings.state_dir/'jobs'
        self.folder.mkdir(exist_ok=True)

    def _start(self,workspace,cwd,command,args,kind):
        root=self.s.root(workspace,'execute')
        if not isinstance(args,list) or len(args)>100 or any(not isinstance(a,str) or len(a)>2048 or '\x00' in a or '\r' in a or '\n' in a for a in args):
            raise WorkspaceError('args must be an array of simple strings (max 100)')
        if not isinstance(command,str) or not SAFE_EXECUTABLE.fullmatch(command):
            raise WorkspaceError('Executable name must be a simple name, not a path')
        if command.casefold() not in {x.casefold() for x in self.s.allowed_commands}:
            raise WorkspaceError(f'Executable not allowlisted: {command}')
        if os.name=='nt' and any(WINDOWS_META.search(a) for a in args):
            raise WorkspaceError('Shell metacharacters are prohibited on Windows')
        # Avoid shell command strings; the user controls which executable is permitted.
        resolved=shutil.which(command)
        if not resolved:
            raise WorkspaceError(f'Executable not found on PATH: {command}')
        _,directory,_=safe_path(self.s,workspace,cwd,'execute',allow_root=True,must_exist=True)
        if not directory.is_dir():raise WorkspaceError('Working directory must be a directory')
        with self.lock:
            active=sum(1 for j in self.jobs.values() if j['proc'].poll() is None)
            if active>=self.s.max_jobs:raise WorkspaceError(f'Maximum active jobs: {self.s.max_jobs}')
            ident=uuid.uuid4().hex
            log=self.folder/(ident+'.log')
            # Never inherit important tunnel/control-plane credentials into child processes.
            env={k:v for k,v in os.environ.items() if not (k.upper().endswith(('_API_KEY','_SECRET','_TOKEN','_PASSWORD')) or k.upper().startswith(('AWS_', 'AZURE_', 'GOOGLE_', 'GCP_', 'OPENAI_', 'CONTROL_PLANE_', 'GITHUB_', 'GH_', 'TUNNEL_')) or k.upper() in {'SSH_AUTH_SOCK', 'OPENAI_ADMIN_KEY'})}
            env['PYTHONUNBUFFERED']='1'
            flags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name=='nt' else 0
            with log.open('wb') as handle:
                proc=subprocess.Popen([resolved,*args],cwd=str(directory),stdin=subprocess.DEVNULL,stdout=handle,stderr=subprocess.STDOUT,
                                      env=env,shell=False,creationflags=flags,start_new_session=os.name!='nt',close_fds=True)
            self.jobs[ident]={'id':ident,'proc':proc,'log':log,'workspace':workspace,'cwd':str(directory.relative_to(root.path)),
                              'command':[command,*args],'started':time.monotonic(),'started_at':datetime.now(timezone.utc).isoformat(),
                              'kind':kind,'deadline':self.s.max_job_seconds,'timed_out':False,'cancelled':False,'log_limit_exceeded':False,'done':threading.Event()}
            threading.Thread(target=self._watch,args=(ident,),daemon=True).start()
            self.audit.write('job_start',True,workspace,detail={'id':ident,'executable':command,'kind':kind})
            return {'job_id':ident,'pid':proc.pid,'command':[command,*args],'timeout_seconds':self.s.max_job_seconds,'status':'running'}

    def _watch(self,ident):
        job=self.jobs[ident]
        try:
            while job['proc'].poll() is None:
                if job['log'].stat().st_size > self.s.max_job_log:
                    job['log_limit_exceeded']=True
                    process_tree_kill(job['proc'])
                    break
                if time.monotonic()-job['started'] > job['deadline']:
                    job['timed_out']=True
                    process_tree_kill(job['proc'])
                    break
                time.sleep(0.2)
        finally:
            try:job['proc'].wait(timeout=3)
            except subprocess.TimeoutExpired:pass
            try:
                self.audit.write('job_end',job['proc'].returncode==0 and not job['timed_out'] and not job['cancelled'] and not job['log_limit_exceeded'],job['workspace'],
                                 detail={'id':ident,'returncode':job['proc'].returncode,'timeout':job['timed_out'],'cancelled':job['cancelled'],'log_limit_exceeded':job['log_limit_exceeded']})
            except OSError:
                # Workspace may be deleted externally during shutdown/tests.
                pass
            finally:
                job['done'].set()

    def _job(self,ident):
        with self.lock:
            if not isinstance(ident,str) or ident not in self.jobs:
                raise WorkspaceError('Unknown job ID (job history is in-memory; jobs from past restarts are not available)')
            return self.jobs[ident]

    def run(self,workspace,command,args=None,cwd='.'):
        return self._start(workspace,cwd,command,args or [],'command')

    def tests(self,workspace,framework='pytest',cwd='.',extra_args=None):
        templates={
            'pytest':('python',['-m','pytest']),
            'unittest':('python',['-m','unittest','discover']),
            'ruff':('python',['-m','ruff','check','.']),
            'mypy':('python',['-m','mypy','.']),
            'npm':('npm',['test','--']),
            'pnpm':('pnpm',['test']),
            'maven':('mvn',['test']),
            'gradle':('gradle',['test']),
            'cargo':('cargo',['test']),
            'go':('go',['test','./...']),
            'dotnet':('dotnet',['test'])
        }
        if framework not in templates:raise WorkspaceError(f'Unknown framework: {framework}')
        cmd,base=templates[framework]
        return self._start(workspace,cwd,cmd,base+(extra_args or []),'tests:'+framework)

    def script(self,workspace,path,args=None,cwd='.'):
        _,file,rel=safe_path(self.s,workspace,path,'execute',must_exist=True)
        if not file.is_file() or file.suffix.lower() not in {'.py','.js','.mjs'}:
            raise WorkspaceError('Only existing .py/.js/.mjs scripts inside workspace can run')
        root=self.s.root(workspace,'execute')
        _,directory,_=safe_path(self.s,workspace,cwd,'execute',allow_root=True,must_exist=True)
        # Build a path relative to cwd; never accept an arbitrary external executable.
        script_path=os.path.relpath(file,directory)
        runner='python' if file.suffix.lower()=='.py' else 'node'
        return self._start(workspace,cwd,runner,[script_path,*(args or [])],'script')

    def status(self,ident):
        j=self._job(ident); rc=j['proc'].poll()
        return {'job_id':ident,'status':'timed_out' if j['timed_out'] else 'log_limit_exceeded' if j['log_limit_exceeded'] else 'cancelled' if j['cancelled'] else 'running' if rc is None else 'completed' if rc==0 else 'failed',
                'returncode':rc,'started_at':j['started_at'],'elapsed_seconds':round(time.monotonic()-j['started'],2),
                'workspace':j['workspace'],'cwd':j['cwd'],'command':j['command']}

    def output(self,ident,offset=0,max_bytes=32768):
        j=self._job(ident)
        if type(offset) is not int or offset<0 or type(max_bytes) is not int or not 1<=max_bytes<=self.s.max_output:
            raise WorkspaceError('Invalid log range')
        size=j['log'].stat().st_size
        if offset>size:raise WorkspaceError('Log offset beyond file')
        with j['log'].open('rb') as f:
            f.seek(offset);data=f.read(max_bytes)
        return {'job_id':ident,'offset':offset,'next_offset':offset+len(data),'total_bytes':size,
                'text':data.decode('utf-8',errors='replace'),'has_more':offset+len(data)<size,'status':self.status(ident)['status']}

    def cancel(self,ident):
        with self.lock:
            j=self._job(ident)
            if j['proc'].poll() is not None:return {'job_id':ident,'cancelled':False,'reason':'already finished'}
            j['cancelled']=True
            process_tree_kill(j['proc'])
            return {'job_id':ident,'cancelled':True}

    def shutdown(self):
        """Stop child trees before the server process exits."""
        with self.lock:
            for job in self.jobs.values():
                if job['proc'].poll() is None:
                    job['cancelled']=True
                    process_tree_kill(job['proc'])

    def list(self):
        with self.lock:
            return [self.status(i) for i in list(self.jobs)[-50:]]

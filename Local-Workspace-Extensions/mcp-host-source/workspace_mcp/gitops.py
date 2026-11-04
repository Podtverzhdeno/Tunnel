"""Restricted Git verbs; no arbitrary git subcommands."""
import os
import subprocess
from pathlib import Path
from .config import WorkspaceError
from .security import safe_path


class Git:
    def __init__(self,settings):
        self.s=settings

    def repo(self,workspace,path='.'):
        _,p,rel=safe_path(self.s,workspace,path,'git',allow_root=True,must_exist=True)
        if not p.is_dir():raise WorkspaceError('Git path must be a directory')
        return p

    def _call(self,workspace,path,args):
        p=self.repo(workspace,path)
        if any(not isinstance(x,str) or '\x00' in x for x in args): raise WorkspaceError('Invalid Git argument')
        # Disables interactive prompts, pager, git-configured external diff and hooks.
        env=os.environ.copy()
        env.update({'GIT_TERMINAL_PROMPT':'0','GIT_PAGER':'cat','GIT_OPTIONAL_LOCKS':'0','GIT_EXTERNAL_DIFF':'',
                    'GIT_CONFIG_NOSYSTEM':'1','GIT_CONFIG_GLOBAL':os.devnull,'GIT_LFS_SKIP_SMUDGE':'1'})
        cmd=['git','-C',str(p),'-c','core.pager=cat','-c','diff.external=','-c',f'core.hooksPath={os.devnull}',*args]
        try:
            result=subprocess.run(cmd,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
                                  timeout=25,env=env,check=False)
        except FileNotFoundError as exc:raise WorkspaceError('Git is not installed') from exc
        except subprocess.TimeoutExpired as exc:raise WorkspaceError('Git operation timed out') from exc
        output=result.stdout[:self.s.max_output].decode('utf-8',errors='replace')
        return {'returncode':result.returncode,'output':output,'truncated':len(result.stdout)>self.s.max_output}

    def status(self,workspace,path='.'):
        return self._call(workspace,path,['status','--short','--branch'])

    def diff(self,workspace,path='.',staged=False):
        return self._call(workspace,path,['--no-pager','diff','--no-ext-diff',*(['--cached'] if staged else []),'--'])

    def log(self,workspace,path='.',count=15):
        if type(count) is not int or not 1<=count<=50:raise WorkspaceError('count must be 1..50')
        return self._call(workspace,path,['--no-pager','log','--oneline','--decorate',f'-{count}'])

    def add(self,workspace,path='.',files=None,stage_all=False):
        self.s.root(workspace,'write')
        repo_dir=self.repo(workspace,path)
        if stage_all:
            if files:raise WorkspaceError('Cannot combine files and stage_all')
            targets=['.']
        else:
            if not isinstance(files,list) or not files or len(files)>100:
                raise WorkspaceError('Specify files list or stage_all=true')
            targets=[]
            for item in files:
                # git add allows staging deletions; path must be within configured root.
                _,target,rel=safe_path(self.s,workspace,item,'write')
                if not target.resolve().is_relative_to(repo_dir.resolve()):
                    raise WorkspaceError('Staged path must be inside selected git repo')
                targets.append(target.relative_to(repo_dir).as_posix())
        return self._call(workspace,path,['add','-A','--',*targets])

    def commit(self,workspace,message,path='.'):
        self.s.root(workspace,'write')
        if not isinstance(message,str) or not 1<=len(message)<=250 or '\x00' in message:
            raise WorkspaceError('Commit message must be 1..250 characters')
        return self._call(workspace,path,['commit','-m',message,'--no-gpg-sign'])

    def branches(self,workspace,path='.'):
        return self._call(workspace,path,['branch','--list'])

    def fetch(self,workspace,path='.'):
        if not self.s.allow_git_remote:raise WorkspaceError('Remote Git disabled in config')
        return self._call(workspace,path,['fetch','--all','--prune'])

    def push(self,workspace,path='.'):
        if not self.s.allow_git_remote:raise WorkspaceError('Remote Git disabled in config')
        self.s.root(workspace,'write')
        return self._call(workspace,path,['push'])

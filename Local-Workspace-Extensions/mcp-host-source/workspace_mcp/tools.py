"""Single registry for MCP discovery, dispatch and local tests."""
import inspect
import json
import threading
from types import MappingProxyType
from .config import WorkspaceError
from .files import Files
from .audit import Audit
from .jobs import Jobs
from .gitops import Git


def description(text):
    return text.strip()


# Explicit, fixed host-side capability profiles; NEVER chosen from MCP request
# parameters, client-supplied role labels, tool arguments or HTTP headers.
# Legacy mode is NOT a verified reviewer identity or secure owner plane.
OBSERVATION_TOOLS = frozenset({
    'list_workspaces', 'list_directory', 'directory_tree',
    'file_info', 'read_file', 'search_text',
})
# Git "read" commands are intentionally excluded: repository config,
# external diff drivers and fsmonitor may execute helper processes.
HOST_PROFILES = frozenset({'observer', 'legacy'})


class ToolRegistry:
    def __init__(self,settings,profile='observer'):
        if profile not in HOST_PROFILES or type(profile) is not str:
            raise WorkspaceError('Invalid host capability profile')
        self.profile=profile
        self.s=settings
        self.audit=Audit(settings)
        self.fs=Files(settings)
        self.jobs=Jobs(settings,self.audit)
        self.git=Git(settings)
        self.tools={}
        self._register()
        if self.profile=='observer':
            # Freeze both tool names and dispatch callbacks. In particular,
            # a late extension cannot rebind read_file as a privileged callable
            # while leaving the readOnlyHint=True metadata.
            self.tools=MappingProxyType({
                key:MappingProxyType(dict(value))
                for key,value in self.tools.items()
            })

    def add(self,name,func,doc,properties=None,required=(),read_only=False):
        if name in self.tools:
            raise WorkspaceError('Duplicate tool registration prohibited')
        if isinstance(self.tools,MappingProxyType):
            raise WorkspaceError('Observer registry is sealed')
        self.tools[name]={'func':func,'schema':{'type':'object','properties':properties or {},'required':list(required),'additionalProperties':False},
                          'description':doc,'readOnlyHint':read_only}

    def _register(self):
        S=lambda s:{'type':'string','description':s}
        B=lambda s:{'type':'boolean','description':s}
        I=lambda s:{'type':'integer','description':s}
        A=lambda s:{'type':'array','items':{'type':'string'},'description':s}
        W=S('Workspace alias from list_workspaces; never an absolute path')
        P=S('Relative path inside the workspace, forward slashes recommended')
        self.add('list_workspaces',self.fs.workspaces,'List configured accessible roots and privileges',read_only=True)
        self.add('list_directory',self.fs.list_dir,'List files/directories inside a workspace',{'workspace':W,'path':P,'limit':I('Maximum entries, 1..500')},['workspace'],True)
        self.add('directory_tree',self.fs.tree,'Browse a bounded directory tree',{'workspace':W,'path':P,'max_depth':I('0..8'),'limit':I('1..1000')},['workspace'],True)
        self.add('file_info',self.fs.info,'Get file size, timestamp and SHA256 hash',{'workspace':W,'path':P},['workspace','path'],True)
        self.add('read_file',self.fs.read,'Read UTF-8 file with numbered lines and hash',{'workspace':W,'path':P,'start_line':I('1-based start line'),'max_lines':I('Maximum lines')},['workspace','path'],True)
        self.add('read_binary',self.fs.read_binary,'Read small binary file as base64',{'workspace':W,'path':P},['workspace','path'],True)
        self.add('search_text',self.fs.search,'Search content of workspace text files',{'workspace':W,'query':S('Literal string to match'),'path':P,'filename_glob':S('Filename glob, e.g. *.py'),'case_sensitive':B('Case sensitivity'),'max_matches':I('Maximum matches')},['workspace','query'],True)
        self.add('create_directory',self.fs.mkdir,'Create a directory',{'workspace':W,'path':P,'parents':B('Create parents')},['workspace','path'])
        self.add('write_file',self.fs.write,'Create file or atomically overwrite. Existing files REQUIRE overwrite=true and expected_sha256 from read_file/file_info.',{'workspace':W,'path':P,'content':S('UTF-8 file content, or base64 if base64_input=true'),'overwrite':B('Overwrite an existing file'),'expected_sha256':S('Current SHA256 for optimistic locking'),'base64_input':B('Decode base64 instead of UTF-8')},['workspace','path','content'])
        self.add('replace_text',self.fs.replace_text,'Replace one exact unique substring in a text file',{'workspace':W,'path':P,'old':S('Exact unique old text'),'new':S('Replacement'),'expected_sha256':S('Optional expected current SHA256')},['workspace','path','old','new'])
        self.add('move_path',self.fs.move,'Rename or move within a workspace, never overwrite',{'workspace':W,'source':P,'destination':P},['workspace','source','destination'])
        self.add('trash_path',self.fs.trash_path,'Move file or directory to reversible private trash; no permanent deletes',{'workspace':W,'path':P,'recursive':B('Required for directory')},['workspace','path'])
        self.add('restore_trash',self.fs.restore,'Restore a prior trashed file or directory',{'ident':S('Trash ID from trash_path')},['ident'])
        self.add('run_command',self.jobs.run,'Start allowlisted executable without shell; returns job_id for polling. Execution uses local OS-user privileges!',{'workspace':W,'command':S('Allowlisted executable name, e.g. python, npm, git'),'args':A('Argument array (no shell strings)'),'cwd':P},['workspace','command'])
        self.add('run_tests',self.jobs.tests,'Run pytest/unittest/ruff/mypy/npm/pnpm/maven/gradle/cargo/go/dotnet test job',{'workspace':W,'framework':S('Framework: pytest, unittest, ruff, mypy, npm, pnpm, maven, gradle, cargo, go, dotnet'),'cwd':P,'extra_args':A('Additional arguments')},['workspace'])
        self.add('run_script',self.jobs.script,'Execute a .py/.js/.mjs script inside an execution-enabled workspace',{'workspace':W,'path':P,'args':A('Arguments'),'cwd':P},['workspace','path'])
        self.add('job_status',self.jobs.status,'Poll the state and return code of a job',{'ident':S('Job ID')},['ident'],True)
        self.add('job_output',self.jobs.output,'Read incremental process logs with byte offset',{'ident':S('Job ID'),'offset':I('Start byte offset'),'max_bytes':I('Maximum bytes')},['ident'],True)
        self.add('list_jobs',self.jobs.list,'List jobs from this server process',read_only=True)
        self.add('cancel_job',self.jobs.cancel,'Kill a running job and its child process tree',{'ident':S('Job ID')},['ident'])
        self.add('git_status',self.git.status,'Get git short branch/status',{'workspace':W,'path':P},['workspace'],True)
        self.add('git_diff',self.git.diff,'Read unstaged or staged git diff',{'workspace':W,'path':P,'staged':B('Show staged diff')},['workspace'],True)
        self.add('git_log',self.git.log,'Read recent commits',{'workspace':W,'path':P,'count':I('1..50')},['workspace'],True)
        self.add('git_add',self.git.add,'Stage selected paths or all paths with explicit stage_all=true',{'workspace':W,'path':P,'files':A('Files relative to workspace root'),'stage_all':B('Stage every file in repo')},['workspace'])
        self.add('git_commit',self.git.commit,'Create git commit without running commit hooks',{'workspace':W,'message':S('Commit message'),'path':P},['workspace','message'])
        self.add('git_branches',self.git.branches,'List local git branches',{'workspace':W,'path':P},['workspace'],True)
        self.add('git_fetch',self.git.fetch,'Fetch from remotes (disabled by default)',{'workspace':W,'path':P},['workspace'])
        self.add('git_push',self.git.push,'Push current branch (disabled by default)',{'workspace':W,'path':P},['workspace'])

    def assert_safe_http_profile(self):
        """Fail closed before exposing a registry through an HTTP listener.

        A read-only capability profile is necessary but not sufficient for OS
        isolation. A separate Windows principal and ACL tests remain P085
        release blockers.
        """
        if self.profile!='observer':
            raise WorkspaceError('PRIVILEGED_HTTP_PROFILE_DENIED')
        if (set(self.tools) != set(OBSERVATION_TOOLS) | {
                'read_binary','create_directory','write_file','replace_text',
                'move_path','trash_path','restore_trash','run_command',
                'run_tests','run_script','job_status','job_output','list_jobs',
                'cancel_job','git_status','git_diff','git_log','git_add',
                'git_commit','git_branches','git_fetch','git_push'}):
            raise WorkspaceError('OBSERVER_TOOL_REGISTRY_UNEXPECTED')
        if not isinstance(self.tools,MappingProxyType):
            raise WorkspaceError('OBSERVER_TOOL_REGISTRY_UNSEALED')
        for root in self.s.roots.values():
            if (not root.read or root.write or root.execute or root.git):
                raise WorkspaceError('OBSERVER_ROOT_MUST_BE_READ_ONLY')
            for guarded in (self.s.config_file,self.s.state_dir):
                if guarded == root.path or guarded.is_relative_to(root.path):
                    raise WorkspaceError('OBSERVER_CONTROL_PATH_INSIDE_EXPOSED_ROOT')
        return True

    def available(self,name):
        item=self.tools.get(name)
        return (item is not None and (self.profile=='legacy' or
                (name in OBSERVATION_TOOLS and item['readOnlyHint'] is True)))

    def list(self):
        return [{'name':name,'description':item['description'],'inputSchema':item['schema'],
                 'annotations':{'readOnlyHint':item['readOnlyHint'],'destructiveHint':not item['readOnlyHint']}}
                for name,item in self.tools.items() if self.available(name)]

    def call(self,name,arguments):
        # Deny BEFORE parsing untrusted inputs, resolving paths or executing
        # any registry callback. Also blocks extensions added after startup.
        if not self.available(name):
            raise WorkspaceError('Tool unavailable for host capability profile')
        tool=self.tools.get(name)
        if tool is None:raise KeyError(name)
        if not isinstance(arguments,dict):raise WorkspaceError('arguments must be an object')
        schema=tool['schema']
        unexpected=set(arguments)-set(schema['properties'])
        missing=set(schema['required'])-set(arguments)
        if unexpected or missing:
            raise WorkspaceError(f'Invalid arguments: unexpected={sorted(unexpected)}, missing={sorted(missing)}')
        for key,value in arguments.items():
            typ=schema['properties'][key]['type']
            if (typ=='string' and not isinstance(value,str)) or (typ=='boolean' and type(value) is not bool) or (typ=='integer' and type(value) is not int) or (typ=='array' and (not isinstance(value,list) or any(not isinstance(v,str) for v in value))):
                raise WorkspaceError(f'Invalid type for {key}: expected {typ}')
        try:
            result=tool['func'](**arguments)
            if name=='list_workspaces' and self.profile=='observer':
                # Effective permissions, not underlying OS root grants.
                result=[{**root,'write':False,'execute':False,'git':False}
                        for root in result]
            if not tool['readOnlyHint']:
                self.audit.write(name,True,arguments.get('workspace'),arguments.get('path') or arguments.get('source'))
            return result
        except (WorkspaceError,ValueError,OSError,UnicodeError) as exc:
            self.audit.write(name,False,arguments.get('workspace'),arguments.get('path'),type(exc).__name__)
            raise WorkspaceError(str(exc)) from exc

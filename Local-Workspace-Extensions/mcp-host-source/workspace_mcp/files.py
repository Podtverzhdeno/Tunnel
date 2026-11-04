"""Read/write/search tools. Writes are atomic; destructive delete is reversible."""
import base64
import hashlib
import json
import mimetypes
import os
import shutil
import tempfile
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from .config import WorkspaceError
from .security import safe_path, sensitive, validate_glob, require_regular


def sha(data):
    return hashlib.sha256(data).hexdigest()


class Files:
    def __init__(self, settings):
        self.s = settings
        self.lock = threading.RLock()
        self.trash = self.s.state_dir / 'trash'
        self.trash.mkdir(exist_ok=True)

    def workspaces(self):
        return [{'name': r.name, 'path': str(r.path), 'read': r.read,
                 'write': r.write, 'execute': r.execute, 'git': r.git} for r in self.s.roots.values()]

    def list_dir(self, workspace, path='.', limit=150):
        if type(limit) is not int or not 1 <= limit <= 500:
            raise WorkspaceError('limit must be 1..500')
        root, p, rel = safe_path(self.s, workspace, path, allow_root=True, must_exist=True)
        if not p.is_dir():
            raise WorkspaceError('Not a directory')
        result = []
        for f in sorted(p.iterdir(), key=lambda x: x.name.casefold()):
            relpath = f.relative_to(root.path).as_posix()
            try:
                safe_path(self.s, workspace, relpath, must_exist=True)
                typ = 'directory' if f.is_dir() else 'file' if f.is_file() else 'other'
                result.append({'name': f.name, 'path': relpath, 'type': typ, 'bytes': f.stat().st_size if typ == 'file' else None})
            except (WorkspaceError, OSError):
                continue
            if len(result) == limit:
                break
        return {'path': rel, 'entries': result, 'truncated': len(result) == limit}

    def tree(self, workspace, path='.', max_depth=3, limit=300):
        if type(max_depth) is not int or not 0 <= max_depth <= 8 or type(limit) is not int or not 1 <= limit <= 1000:
            raise WorkspaceError('Invalid depth/limit')
        root, start, _ = safe_path(self.s, workspace, path, allow_root=True, must_exist=True)
        if not start.is_dir():
            raise WorkspaceError('Not a directory')
        out=[]
        def visit(directory, depth):
            if depth > max_depth or len(out) >= limit:
                return
            for item in sorted(directory.iterdir(), key=lambda x:x.name.casefold()):
                if len(out) >= limit:
                    return
                rel = item.relative_to(root.path).as_posix()
                try:
                    safe_path(self.s, workspace, rel, must_exist=True)
                    kind='dir' if item.is_dir() else 'file' if item.is_file() else 'other'
                    out.append({'path':rel,'type':kind})
                    if kind=='dir' and depth < max_depth:
                        visit(item, depth+1)
                except (WorkspaceError, OSError):
                    continue
        visit(start, 0)
        return {'entries':out, 'truncated':len(out)>=limit}

    def info(self, workspace, path):
        root, p, rel = safe_path(self.s, workspace, path, must_exist=True)
        st=p.stat()
        result = {'path':rel, 'directory':p.is_dir(), 'size':st.st_size, 'modified_utc':datetime.fromtimestamp(st.st_mtime, timezone.utc).isoformat()}
        if p.is_file() and st.st_size <= self.s.max_read:
            result['sha256']=sha(p.read_bytes())
        return result

    def read(self, workspace, path, start_line=1, max_lines=500):
        if type(start_line) is not int or start_line < 1 or type(max_lines) is not int or not 1<=max_lines<=2000:
            raise WorkspaceError('Invalid line range')
        root, p, rel = safe_path(self.s, workspace, path, must_exist=True)
        require_regular(p)
        size=p.stat().st_size
        if size>self.s.max_read:
            raise WorkspaceError(f'File exceeds max_read_bytes: {size} > {self.s.max_read}')
        data=p.read_bytes()
        try:
            content=data.decode('utf-8')
        except UnicodeDecodeError:
            raise WorkspaceError('Not UTF-8 text; use read_binary')
        lines=content.splitlines(keepends=True)
        subset=lines[start_line-1:start_line-1+max_lines]
        return {'path':rel, 'sha256':sha(data), 'total_lines':len(lines), 'start_line':start_line,
                'content': ''.join(f'{start_line+i}: {line}' for i,line in enumerate(subset)),
                'has_more':start_line-1+max_lines<len(lines)}

    def read_binary(self, workspace, path):
        _, p, rel=safe_path(self.s, workspace, path, must_exist=True)
        require_regular(p)
        if p.stat().st_size>min(self.s.max_read, 1048576):
            raise WorkspaceError('Binary file too large')
        data=p.read_bytes()
        return {'path':rel,'size':len(data),'sha256':sha(data),'mime_type':mimetypes.guess_type(p.name)[0] or 'application/octet-stream',
                'base64':base64.b64encode(data).decode('ascii')}

    def write(self, workspace, path, content, overwrite=False, expected_sha256=None, base64_input=False):
        if not isinstance(content, str):
            raise WorkspaceError('content must be a string')
        try:
            data=base64.b64decode(content,validate=True) if base64_input else content.encode('utf-8')
        except (ValueError, UnicodeError) as exc:
            raise WorkspaceError(f'Invalid input data: {exc}') from exc
        if len(data)>self.s.max_write:
            raise WorkspaceError('Write exceeds max_write_bytes')
        with self.lock:
            root,p,rel=safe_path(self.s,workspace,path,'write')
            if not p.parent.is_dir():
                raise WorkspaceError('Parent directory does not exist; use make_directory')
            if p.exists() or p.is_symlink():
                if not p.is_file():
                    raise WorkspaceError('Target is not a regular file')
                if not overwrite:
                    raise WorkspaceError('File exists; set overwrite=true and expected_sha256')
                if not expected_sha256 or sha(p.read_bytes()) != expected_sha256:
                    raise WorkspaceError('SHA-256 mismatch; read current file before overwriting')
            elif expected_sha256 is not None:
                raise WorkspaceError('expected_sha256 supplied for nonexistent file')
            fd,tmp=tempfile.mkstemp(prefix='.mcp-write-',dir=str(p.parent))
            try:
                with os.fdopen(fd,'wb') as f:
                    f.write(data)
                    f.flush();os.fsync(f.fileno())
                if p.exists():
                    os.chmod(tmp,p.stat().st_mode)
                # re-check link traversal at commit time; not atomic vs malicious local races.
                safe_path(self.s,workspace,path,'write')
                os.replace(tmp,p)
            finally:
                if os.path.exists(tmp): os.unlink(tmp)
        return {'path':rel,'bytes':len(data),'sha256':sha(data),'written':True}

    def replace_text(self, workspace, path, old, new, expected_sha256=None):
        if not isinstance(old,str) or not old or not isinstance(new,str):
            raise WorkspaceError('old must be nonempty; new must be a string')
        with self.lock:
            _,p,_=safe_path(self.s,workspace,path,'write',must_exist=True)
            if p.stat().st_size>self.s.max_read: raise WorkspaceError('File too large to patch')
            data=p.read_bytes(); before=data.decode('utf-8')
            if expected_sha256 is not None and sha(data)!=expected_sha256:
                raise WorkspaceError('SHA-256 mismatch')
            count=before.count(old)
            if count!=1: raise WorkspaceError(f'Expected exactly 1 match; found {count}')
            return self.write(workspace,path,before.replace(old,new,1),overwrite=True,expected_sha256=sha(data))

    def mkdir(self, workspace, path, parents=False):
        with self.lock:
            _,p,rel=safe_path(self.s,workspace,path,'write')
            p.mkdir(parents=bool(parents),exist_ok=False)
            return {'created':rel}

    def move(self, workspace, source, destination):
        with self.lock:
            _,src,srel=safe_path(self.s,workspace,source,'write',must_exist=True)
            _,dst,drel=safe_path(self.s,workspace,destination,'write')
            if dst.exists() or dst.is_symlink(): raise WorkspaceError('Destination exists')
            if not dst.parent.is_dir(): raise WorkspaceError('Destination parent missing')
            if dst.resolve().is_relative_to(src.resolve()): raise WorkspaceError('Cannot move into own subdirectory')
            src.rename(dst)
            return {'from':srel,'to':drel}

    def trash_path(self, workspace, path, recursive=False):
        with self.lock:
            _,src,rel=safe_path(self.s,workspace,path,'write',must_exist=True)
            if src.is_dir() and not recursive: raise WorkspaceError('Set recursive=true for directories')
            if src.is_dir() and (src/'.git').exists():
                raise WorkspaceError('Refusing to trash a Git repository directory')
            ident=uuid.uuid4().hex
            dest=self.trash/ident
            shutil.move(str(src),str(dest))
            meta={'id':ident,'workspace':workspace,'path':rel,'date':datetime.now(timezone.utc).isoformat()}
            (self.trash/(ident+'.json')).write_text(json.dumps(meta),encoding='utf-8')
            return {'trashed':rel,'trash_id':ident,'restore_with':'restore_trash'}

    def restore(self, ident):
        if not isinstance(ident,str) or len(ident)!=32 or any(c not in '0123456789abcdef' for c in ident):
            raise WorkspaceError('Invalid trash ID')
        with self.lock:
            meta_file=self.trash/(ident+'.json');old=self.trash/ident
            if not old.exists() or not meta_file.is_file(): raise WorkspaceError('Trash entry does not exist')
            meta=json.loads(meta_file.read_text(encoding='utf-8'))
            _,dest,rel=safe_path(self.s,meta['workspace'],meta['path'],'write')
            if dest.exists() or not dest.parent.is_dir(): raise WorkspaceError('Restore destination unavailable')
            shutil.move(str(old),str(dest));meta_file.unlink()
            return {'restored':rel,'workspace':meta['workspace']}

    def search(self, workspace, query, path='.', filename_glob='*', case_sensitive=False, max_matches=80):
        if not isinstance(query,str) or not query or len(query)>2000 or type(max_matches) is not int or not 1<=max_matches<=200:
            raise WorkspaceError('Invalid search options')
        pattern=validate_glob(filename_glob)
        root,start,_=safe_path(self.s,workspace,path,allow_root=True,must_exist=True)
        if not start.is_dir(): raise WorkspaceError('Search path must be a directory')
        matches=[]; scanned=0
        for current,dirs,files in os.walk(start, followlinks=False):
            dirs[:]=sorted([d for d in dirs if not sensitive(Path(current,d).relative_to(root.path).parts)
                            and not Path(current,d).is_symlink()])
            for name in sorted(files):
                if scanned>=self.s.max_search_files or len(matches)>=max_matches: break
                if not __import__('fnmatch').fnmatch(name,pattern):continue
                f=Path(current)/name;rel=f.relative_to(root.path).as_posix()
                try:
                    safe_path(self.s,workspace,rel,must_exist=True)
                    if f.stat().st_size>self.s.max_read:continue
                    text=f.read_text(encoding='utf-8')
                except (UnicodeError,OSError,WorkspaceError):continue
                scanned+=1
                for number,line in enumerate(text.splitlines(),1):
                    if (query in line) if case_sensitive else (query.casefold() in line.casefold()):
                        matches.append({'path':rel,'line':number,'text':line[:300]})
                        if len(matches)>=max_matches: break
            if scanned>=self.s.max_search_files or len(matches)>=max_matches:break
        return {'matches':matches,'files_scanned':scanned,'truncated':scanned>=self.s.max_search_files or len(matches)>=max_matches}

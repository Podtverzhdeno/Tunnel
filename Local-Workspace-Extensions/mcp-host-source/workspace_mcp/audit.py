import json
import threading
from datetime import datetime, timezone

class Audit:
    def __init__(self, settings):
        self.log_file = settings.state_dir / 'audit.jsonl'
        self.lock = threading.Lock()

    def write(self, action, success, workspace=None, path=None, detail=None):
        entry = {'timestamp': datetime.now(timezone.utc).isoformat(), 'action': action,
                 'success': bool(success), 'workspace': workspace, 'path': path,
                 'detail': (str(detail)[:240] if detail is not None else None)}
        with self.lock, self.log_file.open('a', encoding='utf-8') as f:
            f.write(json.dumps(entry, ensure_ascii=False) + '\n')

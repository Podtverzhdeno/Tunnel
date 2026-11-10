"""Run the candidate against disposable ledgers; never enable a live policy."""
import hashlib
import json
from pathlib import Path
import platform
import sys
import unittest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import spec_approval_queue

if Path(spec_approval_queue.__file__).resolve() != HERE / 'spec_approval_queue.py':
    raise RuntimeError('Unexpected queue module')

suite = unittest.defaultTestLoader.discover(str(HERE), pattern='test*.py')
result = unittest.TextTestRunner(verbosity=1).run(suite)
evidence = {
    'ok': result.wasSuccessful(), 'tests': result.testsRun,
    'failures': len(result.failures), 'errors': len(result.errors),
    'skipped': len(result.skipped), 'skip_reasons': [r for _, r in result.skipped],
    'python': platform.python_version(), 'platform': platform.system(),
    'scope': 'Temporary projects and databases only; no real owner grants issued',
    'sources': {name: hashlib.sha256((HERE / name).read_bytes()).hexdigest()
                for name in ('spec_approval_queue.py', 'owner_project_approval.py',
                             'test_project_auto_approval.py')},
}
print(json.dumps(evidence, ensure_ascii=True))
raise SystemExit(0 if result.wasSuccessful() else 1)

"""User-project Gateway entry point with the Windows browser compatibility fix."""
from pathlib import Path
import datetime as dt
import hashlib
import json
import os
import tempfile

from hermes_windows_browser import configure_browser_launcher


def run():
    root = Path(__file__).resolve().parent
    browser = configure_browser_launcher()
    receipt = {
        'schema': 'hermes-gateway-runtime-v1',
        'pid': os.getpid(),
        'observed_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
        'browser_launcher': browser,
        'bootstrap_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'browser_shim_sha256': hashlib.sha256((root/'hermes_windows_browser.py').read_bytes()).hexdigest(),
    }
    fd, temporary = tempfile.mkstemp(prefix='.gateway-runtime-', dir=root)
    try:
        with os.fdopen(fd, 'w', encoding='utf8') as target:
            json.dump(receipt, target)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, root/'gateway_browser_runtime.json')
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    from hermes_cli.gateway import run_gateway
    run_gateway(verbose=0, quiet=False, replace=False)


if __name__ == '__main__':
    run()

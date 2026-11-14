"""Launch real Yandex synthetic no-toggle test using existing Playwright venv."""
from pathlib import Path
import subprocess,sys
HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]
py=ROOT/".venv-cdp"/"Scripts"/"python.exe"
p=subprocess.run([str(py),str(HERE/"test_yandex_no_toggle.py")],
 cwd=ROOT,text=True,capture_output=True,encoding="utf8",errors="replace",timeout=50)
print("exit_code",p.returncode)
print(p.stdout[-2400:])
if p.returncode:print("stderr_tail",p.stderr[-2400:])
sys.exit(p.returncode)

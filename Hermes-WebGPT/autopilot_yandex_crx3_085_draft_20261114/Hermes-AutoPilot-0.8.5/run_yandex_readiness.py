"""Run installed Yandex UI fixture using the existing project Playwright venv."""
import subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
py=ROOT/".venv-cdp"/"Scripts"/"python.exe"
target=Path(__file__).with_name("test_yandex_readiness.py")
if not py.is_file():raise SystemExit("PROJECT_PLAYWRIGHT_VENV_UNAVAILABLE")
p=subprocess.run([str(py),str(target)],cwd=ROOT,timeout=90,
 capture_output=True,text=True,encoding="utf8",errors="replace")
print("exit_code",p.returncode)
print(p.stdout[-5000:])
if p.returncode:print("stderr_tail",p.stderr[-2500:])
sys.exit(p.returncode)

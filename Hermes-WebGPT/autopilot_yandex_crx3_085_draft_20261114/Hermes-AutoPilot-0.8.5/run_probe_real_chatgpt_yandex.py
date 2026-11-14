"""Run no-send real ChatGPT page probe in installed Yandex with isolated profile."""
from pathlib import Path
import subprocess,sys
ROOT=Path(__file__).resolve().parents[2]
cmd=[str(ROOT/".venv-cdp"/"Scripts"/"python.exe"),
  str(Path(__file__).with_name("probe_real_chatgpt_yandex.py"))]
p=subprocess.run(cmd,cwd=ROOT,capture_output=True,text=True,
 encoding="utf8",errors="replace",timeout=55)
print("exit_code",p.returncode)
print(p.stdout[-3200:])
if p.returncode:print("stderr_type","nonempty")
sys.exit(p.returncode)

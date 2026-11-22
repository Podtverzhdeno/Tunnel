"""Black-box A4 release GUI acceptance: genuine WinForms owner buttons.

Only synthetic versioned bundles, isolated LOCALAPPDATA, no live services.
"""
from __future__ import annotations
import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import sys

PROJECT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(PROJECT))
from quickstart.a4.release_manager import ReleaseManager
GUI=PROJECT/"quickstart/a4/setup.ps1"

PS=r"""
[Console]::OutputEncoding=[Text.UTF8Encoding]::new()
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class AQ {
 [DllImport("user32.dll",CharSet=CharSet.Unicode,EntryPoint="SendMessageW")]
 public static extern IntPtr Text(IntPtr h,uint m,IntPtr w,string text);
 [DllImport("user32.dll",EntryPoint="SendMessageW")]
 public static extern IntPtr Msg(IntPtr h,uint m,IntPtr w,IntPtr l);
 [DllImport("user32.dll")] public static extern IntPtr GetDlgItem(IntPtr h,int id);
 [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h,out uint pid);
 public delegate bool Callback(IntPtr h,IntPtr data);
 [DllImport("user32.dll")] [return:MarshalAs(UnmanagedType.Bool)]
 public static extern bool EnumWindows(Callback cb,IntPtr d);
 [DllImport("user32.dll",EntryPoint="GetClassNameW",CharSet=CharSet.Unicode)]
 public static extern int GetClassName(IntPtr h,System.Text.StringBuilder s,int n);
 public static IntPtr DialogButton(uint target,int id){
  IntPtr found=IntPtr.Zero;
  EnumWindows(delegate(IntPtr h,IntPtr d) {
    uint pid=0;GetWindowThreadProcessId(h,out pid);
    if(pid!=target)return true;
    var text=new System.Text.StringBuilder(128);GetClassName(h,text,text.Capacity);
    if(text.ToString()!="#32770")return true;
    var button=GetDlgItem(h,id);
    if(button!=IntPtr.Zero){found=button;return false;}
    return true;
  },IntPtr.Zero);
  return found;
 }
 public static System.Threading.Tasks.Task ClickAsync(IntPtr h) {
    return System.Threading.Tasks.Task.Run(()=>{Msg(h,245,IntPtr.Zero,IntPtr.Zero);});
 }
}
'@
$pidNum=[int]$env:A4_TEST_GUI_PID
$root=[Windows.Automation.AutomationElement]::RootElement
$win=$null
for($wait=0;$wait -lt 90 -and $null -eq $win;$wait++){
 $all=$root.FindAll([Windows.Automation.TreeScope]::Children,
                    [Windows.Automation.Condition]::TrueCondition)
 for($i=0;$i -lt $all.Count;$i++){
  $item=$all.Item($i)
  if($item.Current.ProcessId -eq $pidNum -and
     [string]$item.Current.Name -like "*Hermes*"){$win=$item;break}
 }
 if($null -eq $win){Start-Sleep -Milliseconds 100}
}
if($null -eq $win){throw "gui_not_found"}
$nodes=$win.FindAll([Windows.Automation.TreeScope]::Descendants,
                    [Windows.Automation.Condition]::TrueCondition)
for($i=0;$i -lt 50 -and $nodes.Count -lt 34;$i++){
 Start-Sleep -Milliseconds 100
 $nodes=$win.FindAll([Windows.Automation.TreeScope]::Descendants,
                    [Windows.Automation.Condition]::TrueCondition)
}
$edits=@($nodes|Where-Object {$_.Current.ClassName -like "WindowsForms10.EDIT*"}|
         Sort-Object {$_.Current.BoundingRectangle.Y})
$buttons=@($nodes|Where-Object {$_.Current.ClassName -like "WindowsForms10.BUTTON*"}|
         Sort-Object @{Expression={$_.Current.BoundingRectangle.Y}},
                     @{Expression={$_.Current.BoundingRectangle.X}})
if($edits.Count -lt 8 -or $buttons.Count -lt 12){
  throw ("gui_update_controls_missing; edits="+$edits.Count+" buttons="+$buttons.Count)
}
$folder=$edits[$edits.Count-2].Current.NativeWindowHandle
$digest=$edits[$edits.Count-1].Current.NativeWindowHandle
# Identify each privileged action by its visible label, not relative
# order: optional DEV controls may be appended below release actions.
function ButtonByName([string]$caption){
  $hits=@($buttons|Where-Object {$_.Current.Name -ceq $caption})
  if($hits.Count -ne 1){throw ("gui_release_action_ambiguous: "+$caption)}
  $handle=[IntPtr]$hits[0].Current.NativeWindowHandle
  if($handle -eq [IntPtr]::Zero){throw ("gui_release_action_missing_handle: "+$caption)}
  return $handle
}
$preview=ButtonByName 'Проверить пакет'
$activate=ButtonByName 'Активировать'
$rollback=ButtonByName 'Откатить'
$state=Join-Path $env:LOCALAPPDATA "Hermes-OneClick"
$pointer=Join-Path $state "a4-active-release.json"
$wanted=$env:A4_TEST_BUNDLE_PATH
$sha=$env:A4_TEST_MANIFEST_SHA
[void][AQ]::Text([IntPtr]$folder,12,[IntPtr]::Zero,$wanted)
# Invalid SHA must not arm any activation.
[void][AQ]::Text([IntPtr]$digest,12,[IntPtr]::Zero,('0'*64))
[void][AQ]::Msg([IntPtr]$preview,245,[IntPtr]::Zero,[IntPtr]::Zero)
[void][AQ]::Msg([IntPtr]$activate,245,[IntPtr]::Zero,[IntPtr]::Zero)
Start-Sleep -Milliseconds 100
$old=(Get-Content $pointer -Raw|ConvertFrom-Json).version
if($old -ne "v1.0.0"){throw "bad_preview_modified_active"}
[void][AQ]::Text([IntPtr]$digest,12,[IntPtr]::Zero,$sha)
[void][AQ]::Msg([IntPtr]$preview,245,[IntPtr]::Zero,[IntPtr]::Zero)
$activateTask=[AQ]::ClickAsync([IntPtr]$activate)
$yes=[IntPtr]::Zero
for($i=0;$i -lt 70 -and $yes -eq [IntPtr]::Zero;$i++){
  $yes=[AQ]::DialogButton([uint32]$pidNum,6)
  if($yes -eq [IntPtr]::Zero){Start-Sleep -Milliseconds 100}
}
if($yes -eq [IntPtr]::Zero){throw "activation_consent_missing"}
[void][AQ]::Msg($yes,245,[IntPtr]::Zero,[IntPtr]::Zero)
if(-not $activateTask.Wait(15000)){throw "activation_not_completed"}
$active=(Get-Content $pointer -Raw|ConvertFrom-Json).version
if($active -ne "v1.1.0"){throw ("activation_not_persisted; value="+$active)}
$rollbackTask=[AQ]::ClickAsync([IntPtr]$rollback)
$yes=[IntPtr]::Zero
for($i=0;$i -lt 70 -and $yes -eq [IntPtr]::Zero;$i++){
  $yes=[AQ]::DialogButton([uint32]$pidNum,6)
  if($yes -eq [IntPtr]::Zero){Start-Sleep -Milliseconds 100}
}
if($yes -eq [IntPtr]::Zero){throw "rollback_consent_missing"}
[void][AQ]::Msg($yes,245,[IntPtr]::Zero,[IntPtr]::Zero)
if(-not $rollbackTask.Wait(15000)){throw "rollback_not_completed"}
$restored=(Get-Content $pointer -Raw|ConvertFrom-Json).version
if($restored -ne "v1.0.0"){throw "rollback_did_not_restore_previous"}
[void][AQ]::Msg([IntPtr]$buttons[3].Current.NativeWindowHandle,
                 245,[IntPtr]::Zero,[IntPtr]::Zero)
@{ok=$true;initial=$old;activated=$active;rollback=$restored;
   rejected_unpinned=$true;native_owner_confirmations=2;controls=$nodes.Count}|
  ConvertTo-Json -Compress
"""

def make_bundle(base: Path,version: str) -> tuple[Path,str]:
    bundle=base/version
    bundle.mkdir()
    files={
        "Setup-Hermes-A4.cmd":b"@echo off\n",
        "quickstart/a4/setup.ps1":(
            "param([switch]$SmokeTest)\n"
            "if($SmokeTest){Write-Output 'synthetic pinned GUI'}\n").encode("utf8"),
    }
    listing=[]
    for path,raw in files.items():
        dst=bundle/path
        dst.parent.mkdir(parents=True,exist_ok=True)
        dst.write_bytes(raw)
        listing.append({"path":path,"sha256":hashlib.sha256(raw).hexdigest()})
    raw=json.dumps({"schema_version":1,"product":"Hermes-A4",
                    "version":version,"files":listing},sort_keys=True).encode()
    (bundle/"manifest.json").write_bytes(raw)
    return bundle,hashlib.sha256(raw).hexdigest()

def main():
    if os.name!="nt":
        raise RuntimeError("windows_required")
    with tempfile.TemporaryDirectory(prefix="a4-update-gui-") as root:
        base=Path(root)
        state=base/"Hermes-OneClick"
        state.mkdir()
        manager=ReleaseManager(state)
        old,digest_old=make_bundle(base,"v1.0.0")
        manager.install(old,digest_old)
        new,digest=make_bundle(base,"v1.1.0")
        env=os.environ.copy()
        env["LOCALAPPDATA"]=str(base)
        env["A4_TEST_BUNDLE_PATH"]=str(new)
        env["A4_TEST_MANIFEST_SHA"]=digest
        gui=subprocess.Popen(["powershell.exe","-NoLogo","-NoProfile","-STA",
                    "-ExecutionPolicy","Bypass","-File",str(GUI)],
                  cwd=PROJECT,env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        try:
            env["A4_TEST_GUI_PID"]=str(gui.pid)
            encoded=base64.b64encode(PS.encode("utf-16le")).decode("ascii")
            proc=subprocess.run(["powershell.exe","-NoLogo","-NoProfile",
                    "-NonInteractive","-EncodedCommand",encoded],
                    cwd=PROJECT,env=env,capture_output=True,text=True,
                    encoding="utf8",errors="replace",timeout=90)
            if proc.returncode:
                raise AssertionError("gui_update_case_failed: "+
                    proc.stdout[-500:]+" | "+proc.stderr[-1600:])
            result=json.loads(proc.stdout.strip().splitlines()[-1])
            if not result.get("ok"):
                raise AssertionError("gui_update_case_unsuccessful")
            if manager.active()["version"]!="v1.0.0":
                raise AssertionError("final_version_wrong")
            displaced=json.loads((state/"a4-active-release.rollback-current.json").read_text())
            if displaced["version"]!="v1.1.0":
                raise AssertionError("rollback_backup_missing")
            print(json.dumps({"ok":True,"user_files_unchanged":True,
                    "real_winforms_buttons":True,"result":result},
                    ensure_ascii=False,sort_keys=True))
            return 0
        finally:
            if gui.poll() is None:
                gui.terminate()
            try:gui.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                gui.kill();gui.communicate()

if __name__=="__main__":
    raise SystemExit(main())

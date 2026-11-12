"""Exercise the real native process factory with an owned synthetic child only.

This demonstrates process inheritance, not a model consuming the environment.
"""
import base64
import ctypes as c
from ctypes import wintypes as w
import hashlib
import os
from pathlib import Path
import subprocess
import time

import pytest

import test_wd_q4_shared_agent_startup as agent
from test_wd_startup_recovery import load


pytestmark = pytest.mark.skipif(os.name != "nt" or not agent.SHELL, reason="Windows PowerShell required")


class _Basic(c.Structure):
    _fields_ = [("process_time", c.c_longlong), ("job_time", c.c_longlong),
                ("flags", w.DWORD), ("min_working", c.c_size_t),
                ("max_working", c.c_size_t), ("active_limit", w.DWORD),
                ("affinity", c.c_size_t), ("priority", w.DWORD), ("scheduling", w.DWORD)]


class _Counters(c.Structure):
    _fields_ = [(name, c.c_ulonglong) for name in
                ("read_ops", "write_ops", "other_ops", "read_bytes", "write_bytes", "other_bytes")]


class _Limits(c.Structure):
    _fields_ = [("basic", _Basic), ("io", _Counters), ("process_memory", c.c_size_t),
                ("job_memory", c.c_size_t), ("peak_process", c.c_size_t), ("peak_job", c.c_size_t)]


def _kernel():
    k = c.WinDLL("kernel32", use_last_error=True)
    for name, result, args in (
        ("CreateJobObjectW", w.HANDLE, [c.c_void_p, w.LPCWSTR]),
        ("SetInformationJobObject", w.BOOL, [w.HANDLE, c.c_int, c.c_void_p, w.DWORD]),
        ("AssignProcessToJobObject", w.BOOL, [w.HANDLE, w.HANDLE]),
        ("IsProcessInJob", w.BOOL, [w.HANDLE, w.HANDLE, c.POINTER(w.BOOL)]),
        ("OpenProcess", w.HANDLE, [w.DWORD, w.BOOL, w.DWORD]),
        ("CloseHandle", w.BOOL, [w.HANDLE]),
    ):
        fn = getattr(k, name)
        fn.restype, fn.argtypes = result, args
    return k


def _check(ok, action):
    if not ok:
        raise OSError(c.get_last_error(), action)


def _encoded(script):
    return base64.b64encode(script.encode("utf-16-le")).decode("ascii")


def _wait_file(path, process, seconds=12):
    deadline = time.monotonic() + seconds
    while not path.exists():
        if process.poll() is not None:
            raise AssertionError("synthetic launcher exited before handshake")
        if time.monotonic() >= deadline:
            raise TimeoutError("synthetic child handshake timed out")
        time.sleep(0.02)


@pytest.mark.parametrize("present", [False, True], ids=["absent", "present"])
def test_native_factory_inherits_only_test_recall(tmp_path, monkeypatch, present):
    marker = "synthetic native recall only"
    monkeypatch.setenv("WD_SYNTH_FORBIDDEN", "synthetic inherited environment canary")
    ready, go, result = (tmp_path / name for name in ("ready.txt", "continue.txt", "result.txt"))
    child_code = r'''
$ErrorActionPreference='Stop'
if($null -ne [Environment]::GetEnvironmentVariable('WD_SYNTH_FORBIDDEN','Process')){exit 5}
[IO.File]::WriteAllText($env:WD_SYNTH_READY+'.tmp',[string]$PID)
[IO.File]::Move($env:WD_SYNTH_READY+'.tmp',$env:WD_SYNTH_READY)
$timer=[Diagnostics.Stopwatch]::StartNew()
while(-not [IO.File]::Exists($env:WD_SYNTH_GO)){
  if($timer.ElapsedMilliseconds -gt 10000){exit 3}
  [Threading.Thread]::Sleep(20)
}
$v=[Environment]::GetEnvironmentVariable('WD_MIND_STARTUP_RECALL','Process')
if($null -eq $v){$value='absent'}else{
  $h=[Security.Cryptography.SHA256]::Create()
  try{$value=[BitConverter]::ToString($h.ComputeHash([Text.Encoding]::UTF8.GetBytes($v))).Replace('-','').ToLowerInvariant()}
  finally{$h.Dispose()}
}
[IO.File]::WriteAllText($env:WD_SYNTH_RESULT,$value)
'''
    factory = agent.ROOT / "ops/windows/reboot/start-wd-tools-consumer.ps1"
    # The only executable imported is this one function, never launcher top-level code.
    parent_code = r'''
$ErrorActionPreference='Stop'
if([Console]::In.ReadLine() -cne 'OWNED_JOB_READY'){exit 4}
''' + load(factory, "Start-WdToolsNativeProcess") + "\n" + r'''
$shell=Join-Path $PSHOME $(if($PSVersionTable.PSEdition -ceq 'Core'){'pwsh.exe'}else{'powershell.exe'})
$p=$null
try{
  $p=Start-WdToolsNativeProcess -CliPath $shell -ArgumentLine $env:WD_SYNTH_ARGS -Worktree $env:WD_SYNTH_DIR
  if(-not $p.WaitForExit(15000)){throw 'synthetic_child_timeout'}
  if($p.ExitCode -ne 0){throw 'synthetic_child_failed'}
}finally{if($null -ne $p){$p.Dispose()}}
'''
    system_root = os.environ["SystemRoot"]
    shell = str(Path(system_root) / "System32/WindowsPowerShell/v1.0/powershell.exe")
    env = {"SystemRoot": system_root, "WINDIR": system_root,
           "PATH": str(Path(system_root) / "System32"),
           "TEMP": str(tmp_path), "TMP": str(tmp_path), "USERPROFILE": str(tmp_path),
           "APPDATA": str(tmp_path), "LOCALAPPDATA": str(tmp_path),
           "WD_SYNTH_READY": str(ready), "WD_SYNTH_GO": str(go),
           "WD_SYNTH_RESULT": str(result), "WD_SYNTH_DIR": str(tmp_path),
           "WD_SYNTH_ARGS": "-NoLogo -NoProfile -NonInteractive -EncodedCommand " + _encoded(child_code)}
    if present:
        env["WD_MIND_STARTUP_RECALL"] = marker
    k = _kernel()
    job = k.CreateJobObjectW(None, None)
    _check(job, "create owned job")
    process = None
    try:
        limits = _Limits()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        _check(k.SetInformationJobObject(job, 9, c.byref(limits), c.sizeof(limits)), "set job limit")
        # Parent has no code path to create its child until the stdin signal.
        process = subprocess.Popen([shell, "-NoLogo", "-NoProfile", "-NonInteractive",
                                    "-EncodedCommand", _encoded(parent_code)],
                                   cwd=tmp_path, env=env, stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        _check(k.AssignProcessToJobObject(job, int(process._handle)), "assign owned parent")
        member = w.BOOL()
        _check(k.IsProcessInJob(int(process._handle), job, c.byref(member)), "check parent job")
        assert member.value
        process.stdin.write(b"OWNED_JOB_READY\n")
        process.stdin.flush()
        _wait_file(ready, process)
        child_pid = int(ready.read_text(encoding="ascii"))
        child = k.OpenProcess(0x1000, False, child_pid)  # query only; never terminate by PID
        _check(child, "open owned child for membership check")
        try:
            _check(k.IsProcessInJob(child, job, c.byref(member)), "check child job")
            assert member.value
        finally:
            k.CloseHandle(child)
        go.write_text("continue", encoding="ascii")
        stdout, stderr = process.communicate(timeout=20)
        assert process.returncode == 0, (stdout, stderr)
        expected = hashlib.sha256(marker.encode("utf-8")).hexdigest() if present else "absent"
        assert result.read_text(encoding="ascii") == expected
        assert marker.encode() not in stdout + stderr
    finally:
        # Closing this exact job kills only its assigned owned process tree.
        k.CloseHandle(job)
        if process is not None:
            try:
                process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()  # this Popen is the owned parent, never a live lane
                process.communicate(timeout=5)

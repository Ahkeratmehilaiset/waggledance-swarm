"""Synthetic-only stdin relay tests: no live bridge, no real Mind invocation."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import pytest

ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "ops/windows/reboot/Invoke-WdQ4LaneRecall.ps1"
SHELLS = list(dict.fromkeys(filter(None, (shutil.which("powershell.exe"), shutil.which("pwsh")))))
pytestmark = pytest.mark.skipif(os.name != "nt" or not SHELLS, reason="Windows PowerShell required")

@pytest.fixture
def bundle(tmp_path):
    root = tmp_path / 'bundle'
    root.mkdir()
    wrapper = root / 'Invoke-WdBridgePython.ps1'
    # Synthetic executable echoes only digest; query stays inside in-memory streams.
    py = sys.executable.replace("'", "''")
    source = ("param([Parameter(Position=0)][string]$Tool,[Parameter(ValueFromRemainingArguments)][string[]]$Rest)\n"
              "if ($Tool -cne 'tools/wd_q4_lane_caller_cli.py' -or @($Rest).Count -ne 1 -or $Rest[0] -cne '--enabled') { exit 9 }\n"
              "if ($env:RECALL_INVOKED) { [IO.File]::WriteAllText($env:RECALL_INVOKED,'started') }\n"
              "if ($env:RECALL_MODE_FILE) { [IO.File]::WriteAllText($env:RECALL_MODE_FILE,[string]$env:WD_Q4_LIVE_MODE) }\n"
              "$code=if ($env:RECALL_TEST_MODE -eq 'slow') {'import sys,time,pathlib,os; sys.stdin.buffer.read(); pathlib.Path(sys.argv[1]).write_text(str(os.getpid())); time.sleep(8); pathlib.Path(sys.argv[2]).touch()'} "
              "elseif ($env:RECALL_TEST_MODE -eq 'exit4') {'import sys; sys.stdin.buffer.read(); sys.exit(4)'} "
              "else {'import sys,os,hashlib; raw=sys.stdin.buffer.read(); assert raw not in str(sys.argv).encode(); assert raw not in str(dict(os.environ)).encode(); print(hashlib.sha256(raw).hexdigest())'}\n"
              "& '" + py + "' -c $code $env:RECALL_STARTED $env:RECALL_SENTINEL\n"
              "$global:LASTEXITCODE=$LASTEXITCODE\n")
    wrapper.write_text(source, encoding='utf-8')
    files = {'Invoke-WdBridgePython.ps1': hashlib.sha256(wrapper.read_bytes()).hexdigest()}
    manifest = root / 'deployment-manifest.json'
    manifest.write_text(json.dumps({'schema_version': 1, 'files': files}), encoding='utf-8')
    env = {k:v for k,v in os.environ.items() if not k.startswith(('WD_BRIDGE_', 'WD_REBOOT_', 'AGENT_BRIDGE_'))}
    env.update(WD_BRIDGE_BUNDLE_ROOT=str(root), WD_BRIDGE_PYTHON_WRAPPER=str(wrapper),
               WD_REBOOT_EXPECTED_MANIFEST_HASH=hashlib.sha256(manifest.read_bytes()).hexdigest())
    return env, root

def invoke(shell, env, raw, *args):
    return subprocess.run([shell, '-NoProfile', '-NonInteractive', '-File', str(HELPER), *args],
                          input=raw, capture_output=True, timeout=20, env=env)

@pytest.mark.parametrize('shell', SHELLS)
def test_redirected_stdin_reaches_nested_python_exactly(bundle, shell):
    env, _ = bundle
    raw = b'{"situation":"synthetic","time":"2026-11-06T00:00:00Z"}'
    result = invoke(shell, env, raw)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().decode() == hashlib.sha256(raw).hexdigest()
    assert b'synthetic' not in result.stderr


@pytest.mark.parametrize('shell', SHELLS)
@pytest.mark.parametrize('fraction', ['.1', '.123456'])
def test_supported_fractional_utc_reaches_nested_python_unchanged(bundle, shell, fraction):
    env, _ = bundle
    raw = ('{"situation":"synthetic","time":"2026-11-06T00:00:00' + fraction + 'Z"}').encode('ascii')
    result = invoke(shell, env, raw, '-Live')
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().decode() == hashlib.sha256(raw).hexdigest()


@pytest.mark.parametrize('live,inherited,expected', [(False, '', ''), (False, '1', ''), (True, '', '1')])
def test_live_mode_is_explicit_and_reaches_only_the_query_child(bundle, live, inherited, expected):
    env, root = bundle
    marker = root / 'child-mode'
    env['RECALL_MODE_FILE'] = str(marker)
    env['WD_Q4_LIVE_MODE'] = inherited
    args = ('-Live',) if live else ()
    result = invoke(SHELLS[0], env, b'{"situation":"x","time":"2026-11-06T00:00:00Z"}', *args)
    assert result.returncode == 0, result.stderr
    assert marker.read_text() == expected
    assert env['WD_Q4_LIVE_MODE'] == inherited

@pytest.mark.parametrize('raw', [b'', b'not-json', b'[]', b'{"situation":', b'{}'])
def test_invalid_input_refused_without_starting_wrapper(bundle, raw):
    env, root = bundle
    marker = root / 'wrapper-started'
    env['RECALL_INVOKED'] = str(marker)
    result = invoke(SHELLS[0], env, raw)
    assert result.returncode != 0 and result.stdout == b''
    assert not marker.exists()
    assert raw not in result.stderr if raw else True

def test_nonzero_child_is_propagated_without_output(bundle):
    env, _ = bundle
    env['RECALL_TEST_MODE']='exit4'
    result = invoke(SHELLS[0], env, b'{"situation":"x","time":"2026-11-06T00:00:00Z"}')
    assert result.returncode == 4 and result.stdout == b''

def test_timeout_is_unknown_and_does_not_return_partial_output(bundle):
    env, root = bundle
    env['RECALL_TEST_MODE']='slow'
    sentinel = root / 'synthetic-child-survived'
    started = root / 'synthetic-child-started'
    env['RECALL_STARTED']=str(started)
    env['RECALL_SENTINEL']=str(sentinel)
    result = invoke(SHELLS[0], env, b'{"situation":"x","time":"2026-11-06T00:00:00Z"}', '-TimeoutSeconds', '4')
    assert result.returncode == 3 and result.stdout == b''
    assert b'cleanup confirmed' not in result.stderr.lower()
    assert started.exists() and int(started.read_text()) > 0
    time.sleep(9)
    assert not sentinel.exists(), 'synthetic descendant continued after timeout'

def test_bad_manifest_pin_fails_before_child(bundle):
    env, _ = bundle
    env['WD_REBOOT_EXPECTED_MANIFEST_HASH']='0'*64
    result = invoke(SHELLS[0], env, b'{"situation":"x","time":"2026-11-06T00:00:00Z"}')
    assert result.returncode != 0 and result.stdout == b''

@pytest.mark.parametrize('shell', SHELLS)
def test_powershell_pipeline_forwards_once_without_query_in_argv(bundle, shell):
    env, _ = bundle
    raw = b'{"situation":"pipeline-synthetic","time":"2026-11-06T00:00:00Z"}'
    path = str(HELPER).replace("'", "''")
    command = "$q=[Console]::In.ReadToEnd(); $q | & '" + path + "'; exit $LASTEXITCODE"
    result = subprocess.run([shell, '-NoProfile', '-NonInteractive', '-Command', command],
                            input=raw, capture_output=True, timeout=20, env=env)
    assert raw not in command.encode() and raw not in result.stderr
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().decode() == hashlib.sha256(raw).hexdigest()

def test_tampered_wrapper_refused(bundle):
    env, root = bundle
    with (root / 'Invoke-WdBridgePython.ps1').open('ab') as stream:
        stream.write(b'\n# tampered')
    result = invoke(SHELLS[0], env, b'{"situation":"x","time":"2026-11-06T00:00:00Z"}')
    assert result.returncode != 0 and result.stdout == b''

def test_stalled_stdin_is_bounded_without_starting_wrapper(bundle):
    env, root = bundle
    marker = root / 'wrapper-started'
    env['RECALL_INVOKED'] = str(marker)
    with subprocess.Popen([SHELLS[0], '-NoProfile', '-NonInteractive', '-File', str(HELPER),
                           '-TimeoutSeconds', '3'], stdin=subprocess.PIPE,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env) as process:
        code = process.wait(timeout=15)
        assert code != 0 and process.stdout.read() == b''
    assert not marker.exists()


def test_outer_watchdog_bounds_worker_preflight(bundle, tmp_path):
    env, root = bundle
    marker = root / 'synthetic-preflight-started'
    env['RECALL_PREFLIGHT_STARTED'] = str(marker)
    source = HELPER.read_text(encoding='utf-8-sig')
    needle = '        Add-Type -TypeDefinition @"'
    assert source.count(needle) == 1
    injected = "        [IO.File]::WriteAllText($env:RECALL_PREFLIGHT_STARTED,'started'); Start-Sleep -Seconds 12\n" + needle
    slow_helper = tmp_path / 'synthetic-preflight-relay.ps1'
    slow_helper.write_text(source.replace(needle, injected), encoding='utf-8')
    before = time.monotonic()
    result = subprocess.run([SHELLS[0], '-NoProfile', '-NonInteractive', '-File', str(slow_helper),
                             '-TimeoutSeconds', '6'], input=b'{"situation":"x","time":"2026-11-06T00:00:00Z"}',
                            capture_output=True, timeout=15, env=env)
    assert marker.exists(), 'synthetic worker did not enter preflight'
    assert time.monotonic() - before < 10
    assert result.returncode == 3 and result.stdout == b''


def test_pipeline_stop_terminates_own_internal_worker(bundle):
    """Stop a real PowerShell runspace; observe the exact owned worker handle."""
    import ctypes
    from ctypes import wintypes
    env, root = bundle
    env['RECALL_TEST_MODE'] = 'slow'
    env['RECALL_STARTED'] = str(root / 'started-pid')
    env['RECALL_SENTINEL'] = str(root / 'finished')
    helper = str(HELPER).replace("'", "''")
    marker = env['RECALL_STARTED'].replace("'", "''")
    script = (
        "$q=[Console]::In.ReadLine(); $p=[powershell]::Create(); "
        "$null=$p.AddCommand('" + helper + "').AddParameter('QueryJson',$q).AddParameter('TimeoutSeconds',30); "
        "$async=$p.BeginInvoke(); $clock=[Diagnostics.Stopwatch]::StartNew(); "
        "while (-not [IO.File]::Exists('" + marker + "') -and $clock.ElapsedMilliseconds -lt 15000) { Start-Sleep -Milliseconds 25 }; "
        "if (-not [IO.File]::Exists('" + marker + "')) { exit 8 }; "
        "[Console]::Out.WriteLine('READY'); $null=[Console]::In.ReadLine(); "
        "$p.Stop(); [Console]::Out.WriteLine('STOPPED'); $p.Dispose()"
    )
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    nt = ctypes.WinDLL('ntdll', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    class PBI(ctypes.Structure):
        _fields_ = [('reserved', ctypes.c_void_p), ('peb', ctypes.c_void_p),
                    ('reserved2', ctypes.c_void_p * 2), ('pid', ctypes.c_void_p), ('parent', ctypes.c_void_p)]
    def parent_pid(pid):
        handle = kernel.OpenProcess(0x1000, False, pid)
        assert handle
        try:
            pbi = PBI()
            assert nt.NtQueryInformationProcess(handle, 0, ctypes.byref(pbi), ctypes.sizeof(pbi), None) == 0
            return int(pbi.parent)
        finally:
            kernel.CloseHandle(handle)
    query = '{"situation":"synthetic-stop","time":"2026-11-06T00:00:00Z"}'
    proc = subprocess.Popen([SHELLS[0], '-NoProfile', '-NonInteractive', '-Command', script],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, text=True)
    worker_handle = child_handle = None
    try:
        proc.stdin.write(query + '\n'); proc.stdin.flush()
        deadline = time.monotonic() + 18
        while not Path(env['RECALL_STARTED']).exists() and proc.poll() is None and time.monotonic() < deadline:
            time.sleep(.05)
        assert Path(env['RECALL_STARTED']).exists(), 'synthetic child did not start'
        python_pid = int(Path(env['RECALL_STARTED']).read_text())
        child_handle = kernel.OpenProcess(0x100000 | 0x1000, False, python_pid)
        assert child_handle
        wrapper_pid = parent_pid(python_pid)
        worker_pid = parent_pid(wrapper_pid)
        assert parent_pid(worker_pid) == proc.pid
        worker_handle = kernel.OpenProcess(0x100000 | 0x1000, False, worker_pid)
        assert worker_handle
        assert not (root / 'finished').exists()
        stop_started = time.monotonic()
        proc.stdin.write('stop\n'); proc.stdin.flush()
        proc.wait(timeout=12)
        assert time.monotonic() - stop_started < 4, 'pipeline stop waited for the synthetic child to finish'
        assert kernel.WaitForSingleObject(worker_handle, 1500) == 0, 'owned worker remained alive after pipeline stop'
        assert kernel.WaitForSingleObject(child_handle, 1500) == 0, 'owned synthetic child remained alive after pipeline stop'
        assert not (root / 'finished').exists()
    finally:
        if proc.poll() is None:
            proc.kill(); proc.wait(timeout=5)
        if worker_handle:
            kernel.CloseHandle(worker_handle)
        if child_handle:
            kernel.CloseHandle(child_handle)

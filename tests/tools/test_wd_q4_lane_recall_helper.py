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

"""Synthetic-only host boundary; no live sessions, model, or bridge data."""
import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
HOST = ROOT / "ops/windows/reboot/Invoke-WdQ4SessionHost.ps1"
JOB = ROOT / "ops/windows/reboot/WdQ4ProcessJob.ps1"
PWSH = shutil.which("pwsh")


def q(value):
    return "'" + str(value).replace("'", "''") + "'"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def invoke(tmp_path, code, *, enabled=True, corrupt=None, timeout=5, repeat=False, oversize_helper=False,
           change_second_helper=False, reload_before_second=False):
    if os.name != "nt" or not PWSH:
        pytest.skip("PowerShell 7 on Windows required")
    assert HOST.exists(), "RED: host adapter absent"
    scratch = tmp_path / "s"
    scratch.mkdir()
    cwd = tmp_path / "w"
    cwd.mkdir()
    child = tmp_path / "synthetic.py"
    child.write_text(code, encoding="utf-8")
    job = JOB
    if oversize_helper:
        job = tmp_path / "oversize-helper.ps1"
        job.write_bytes(b"x" * (1024 * 1024 + 1))
    hashes = {"bootstrap": sha(child), "interpreter": sha(Path(sys.executable)), "job": sha(job)}
    if corrupt:
        hashes[corrupt] = "0" * 64
    call = ("Invoke-WdQ4SessionHost " + ("-Enabled " if enabled else "")
              + "-JobHelper " + q(job) + " -JobHelperSha256 " + q(hashes["job"])
              + " -Interpreter " + q(sys.executable) + " -InterpreterSha256 " + q(hashes["interpreter"])
              + " -Bootstrap " + q(child) + " -BootstrapSha256 " + q(hashes["bootstrap"])
              + " -BootstrapArguments @() -WorkingDirectory " + q(cwd) + " -ScratchRoot " + q(scratch)
              + " -InputBytes $inputData -TimeoutSeconds " + str(timeout) + " -MaxOutputBytes 4096")
    second_call = call
    if change_second_helper:
        another = tmp_path / "different-helper.ps1"
        another.write_bytes(JOB.read_bytes() + b"\n# synthetic changed pin\n")
        second_call = call.replace(q(job), q(another)).replace(q(hashes["job"]), q(sha(another)))
    script = ("$ErrorActionPreference='Stop'\n. " + q(HOST) + "\n"
              "$inputData=[Text.Encoding]::UTF8.GetBytes('synthetic-private-marker')\n"
              "$r=" + call + "\n" + ((". " + q(HOST) + "\n" if reload_before_second else "")
                                         + "$again=" + second_call + "\n" if repeat else "")
              + "@{status=$r.Status;output=$(if($null -eq $r.Output){$null}else{[Text.Encoding]::UTF8.GetString($r.Output)})} | ConvertTo-Json -Compress\n")
    if repeat:
        script += "@{again=$again.Status}|ConvertTo-Json -Compress\n"
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    proc = subprocess.run([PWSH, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                          capture_output=True, text=True, timeout=35)
    assert proc.returncode == 0, proc.stderr
    assert "synthetic-private-marker" not in proc.stdout + proc.stderr
    lines = proc.stdout.splitlines()
    return (json.loads(lines[0]) if not repeat else [json.loads(line) for line in lines]), scratch


GOOD = "import sys; assert sys.stdin.buffer.read()==b'synthetic-private-marker'; sys.stdout.buffer.write(b'{\"ok\":true}\\n')"


def test_off_never_launches(tmp_path):
    result, scratch = invoke(tmp_path, GOOD, enabled=False)
    assert result == {"status": "OFF", "output": None}
    assert list(scratch.iterdir()) == []


def test_valid_pinned_child_receives_pipe_and_emits_one_json(tmp_path):
    result, scratch = invoke(tmp_path, GOOD)
    assert result == {"status": "OK", "output": '{"ok":true}\n'}
    assert len(list(scratch.iterdir())) == 1
    assert not any(x.is_file() for x in scratch.rglob("*"))


def test_two_owned_successes_use_same_pinned_helper(tmp_path):
    result, scratch = invoke(tmp_path, GOOD, repeat=True)
    assert result == [{"status": "OK", "output": '{"ok":true}\n'}, {"again": "OK"}]
    assert len(list(scratch.iterdir())) == 2


def test_oversize_helper_read_is_bounded_and_never_dispatches(tmp_path):
    assert "ReadAllBytes($JobHelper)" not in HOST.read_text(encoding="utf-8")
    result, scratch = invoke(tmp_path, GOOD, oversize_helper=True)
    assert result == {"status": "REFUSED", "output": None}
    assert list(scratch.iterdir()) == []


def test_different_helper_pin_after_first_call_never_dispatches(tmp_path):
    result, scratch = invoke(tmp_path, GOOD, repeat=True, change_second_helper=True)
    assert result == [{"status": "OK", "output": '{"ok":true}\n'}, {"again": "REFUSED"}]
    assert len(list(scratch.iterdir())) == 1


@pytest.mark.parametrize("corrupt", ["bootstrap", "interpreter", "job"])
def test_bad_pin_never_launches(tmp_path, corrupt):
    result, scratch = invoke(tmp_path, GOOD, corrupt=corrupt)
    assert result == {"status": "REFUSED", "output": None}
    assert list(scratch.iterdir()) == []


@pytest.mark.parametrize("code", [
    "import sys;sys.stdout.buffer.write(b'{\"ok\":true}\\n');sys.exit(2)",
    "import sys;sys.stdout.buffer.write(b'{\"ok\":true}')",
    "import sys;sys.stdout.buffer.write(b'{}\\n{}\\n')",
    "import sys;sys.stdout.buffer.write(b'{\"a\":1,}\\n')",
    "import sys;sys.stdout.buffer.write(b'{\"a\":NaN}\\n')",
    "import sys;sys.stdout.buffer.write(b'[]\\n')",
])
def test_no_output_on_bad_exit_or_invalid_framing(tmp_path, code):
    result, _ = invoke(tmp_path, code)
    assert result == {"status": "REFUSED", "output": None}


def test_timeout_never_returns_partial_stdout(tmp_path):
    result, _ = invoke(tmp_path, "import sys,time; print('{}',flush=True);time.sleep(10)", timeout=1)
    assert result == {"status": "TIMEOUT", "output": None}


def test_inner_timeout_is_not_misreported_as_refusal_or_output(tmp_path):
    code = "import sys;sys.stdout.buffer.write(b'{\"ignored\":true}\\n');sys.exit(4)"
    result, _ = invoke(tmp_path, code)
    assert result == {"status": "TIMEOUT", "output": None}


@pytest.mark.parametrize("exit_code", [3, 130])
def test_inner_cleanup_unknown_blocks_another_call_in_same_host(tmp_path, exit_code):
    result, scratch = invoke(tmp_path, "import sys;sys.exit(" + str(exit_code) + ")", repeat=True)
    assert result == [{"status": "UNKNOWN", "output": None}, {"again": "UNKNOWN"}]
    assert len(list(scratch.iterdir())) == 1


def test_reloading_host_does_not_clear_cleanup_unknown(tmp_path):
    result, scratch = invoke(tmp_path, "import sys;sys.exit(3)", repeat=True, reload_before_second=True)
    assert result == [{"status": "UNKNOWN", "output": None}, {"again": "UNKNOWN"}]
    assert len(list(scratch.iterdir())) == 1


def test_reloading_host_retains_exact_pinned_helper(tmp_path):
    result, scratch = invoke(tmp_path, GOOD, repeat=True, reload_before_second=True)
    assert result == [{"status": "OK", "output": '{"ok":true}\n'}, {"again": "OK"}]
    assert len(list(scratch.iterdir())) == 2

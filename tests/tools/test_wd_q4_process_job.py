"""Synthetic job contract: no live bridge, model, or private input."""
import base64
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "ops/windows/reboot/WdQ4ProcessJob.ps1"
HOSTS = [pytest.param(shutil.which("powershell.exe"), id="powershell"),
         pytest.param(shutil.which("pwsh"), id="pwsh")]


def quote(value):
    return "'" + str(value).replace("'", "''") + "'"


def run_ps(host, body, *, prelude=""):
    if os.name != "nt" or not host:
        pytest.skip("Windows PowerShell required")
    assert HELPER.is_file(), "tests-first RED: inactive job helper is missing"
    script = "$ErrorActionPreference='Stop'\n" + prelude + "\n. " + quote(HELPER) + "\n" + body
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    result = subprocess.run([host, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, (result.returncode, result.stderr)
    assert "synthetic-query-sentinel" not in result.stdout + result.stderr
    return json.loads(result.stdout)


@pytest.mark.parametrize("host", HOSTS)
def test_job_pipes_query_without_forwarding_parent_secret(host, tmp_path):
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    work = tmp_path / "work"
    work.mkdir()
    code = ("import os,sys,tempfile,pathlib; q=sys.stdin.buffer.read(); "
            "assert q==b'synthetic-query-sentinel'; "
            "assert all('synthetic-query-sentinel' not in a for a in sys.argv); "
            "assert 'Q4_PARENT_SECRET' not in os.environ; "
            "assert os.environ['TEMP']==os.environ['TMP']==tempfile.gettempdir(); "
            "assert pathlib.Path(os.environ['TEMP']).parent==pathlib.Path(" + repr(str(scratch)) + ");print('ok')")
    body = ("$env:Q4_PARENT_SECRET='parent-only'\n"
            "$q=[Text.Encoding]::UTF8.GetBytes('synthetic-query-sentinel')\n"
            "$r=Invoke-WdQ4ProcessJob -Executable " + quote(sys.executable)
            + " -Arguments @('-I','-S','-B','-c'," + quote(code) + ")"
            + " -WorkingDirectory " + quote(work) + " -ScratchRoot " + quote(scratch)
            + " -InputBytes $q -TimeoutSeconds 5 -MaxOutputBytes 4096\n"
            "@{exit=$r.ExitCode;text=[Text.Encoding]::UTF8.GetString($r.Stdout).Trim();parent=$env:Q4_PARENT_SECRET}"
            "|ConvertTo-Json -Compress")
    assert run_ps(host, body) == {"exit": 0, "text": "ok", "parent": "parent-only"}
    assert len(list(scratch.iterdir())) == 1
    assert not any(item.is_file() for item in scratch.rglob("*"))


@pytest.mark.parametrize("host", HOSTS)
@pytest.mark.parametrize("mode", ["timeout", "output_limit"])
def test_job_refuses_timeout_and_oversize_without_raw_output(host, tmp_path, mode):
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    work = tmp_path / "work"
    work.mkdir()
    code = "import time; time.sleep(10)" if mode == "timeout" else "import sys; sys.stdout.write('X'*100000);sys.stdout.flush()"
    body = ("try { $null=Invoke-WdQ4ProcessJob -Executable " + quote(sys.executable)
            + " -Arguments @('-I','-S','-B','-c'," + quote(code) + ")"
            + " -WorkingDirectory " + quote(work) + " -ScratchRoot " + quote(scratch)
            + " -InputBytes ([byte[]]@()) -TimeoutSeconds 1 -MaxOutputBytes 1024; $state='accepted' }"
            " catch { $state=$_.Exception.Message }\n@{state=$state}|ConvertTo-Json -Compress")
    assert run_ps(host, body)["state"] == "q4_job_refused"


@pytest.mark.parametrize("host", HOSTS)
def test_job_closes_its_descendants_even_when_root_exits(host, tmp_path):
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    work = tmp_path / "work"
    work.mkdir()
    marker = tmp_path / "synthetic-descendant-survived.txt"
    descendant = "import time,pathlib;time.sleep(2);pathlib.Path(" + repr(str(marker)) + ").write_text('escaped')"
    code = ("import subprocess,sys; subprocess.Popen([sys.executable,'-c'," + repr(descendant)
            + "],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);print('ok')")
    body = ("$r=Invoke-WdQ4ProcessJob -Executable " + quote(sys.executable)
            + " -Arguments @('-I','-S','-B','-c'," + quote(code) + ")"
            + " -WorkingDirectory " + quote(work) + " -ScratchRoot " + quote(scratch)
            + " -InputBytes ([byte[]]@()) -TimeoutSeconds 5 -MaxOutputBytes 4096\n"
            "Start-Sleep -Milliseconds 2600\n"
            "@{exit=$r.ExitCode;survived=[IO.File]::Exists(" + quote(marker) + ")}|ConvertTo-Json -Compress")
    assert run_ps(host, body) == {"exit": 0, "survived": False}
    assert not marker.exists()


@pytest.mark.parametrize("host", HOSTS)
def test_timeout_also_terminates_own_descendant(host, tmp_path):
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    work = tmp_path / "work"
    work.mkdir()
    marker = tmp_path / "synthetic-timeout-descendant-survived.txt"
    descendant = "import time,pathlib;time.sleep(2);pathlib.Path(" + repr(str(marker)) + ").write_text('escaped')"
    code = ("import subprocess,sys,time;subprocess.Popen([sys.executable,'-c'," + repr(descendant)
            + "],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);time.sleep(10)")
    body = ("try { $null=Invoke-WdQ4ProcessJob -Executable " + quote(sys.executable)
            + " -Arguments @('-I','-S','-B','-c'," + quote(code) + ")"
            + " -WorkingDirectory " + quote(work) + " -ScratchRoot " + quote(scratch)
            + " -InputBytes ([byte[]]@()) -TimeoutSeconds 1 -MaxOutputBytes 4096; $state='accepted' }"
            " catch { $state=$_.Exception.Message }\nStart-Sleep -Milliseconds 2400\n"
            "@{state=$state;survived=[IO.File]::Exists(" + quote(marker) + ")}|ConvertTo-Json -Compress")
    assert run_ps(host, body) == {"state": "q4_job_refused", "survived": False}
    assert not marker.exists()


@pytest.mark.parametrize("host", HOSTS)
@pytest.mark.parametrize("overlap", ["working_directory", "ancestor"])
def test_scratch_overlap_refuses_before_child_starts(host, tmp_path, overlap):
    work = tmp_path / "work"
    work.mkdir()
    marker = tmp_path / "synthetic-child-started.txt"
    scratch = work if overlap == "working_directory" else tmp_path
    code = "import pathlib;pathlib.Path(" + repr(str(marker)) + ").write_text('started')"
    body = ("try { $null=Invoke-WdQ4ProcessJob -Executable " + quote(sys.executable)
            + " -Arguments @('-I','-S','-B','-c'," + quote(code) + ")"
            + " -WorkingDirectory " + quote(work) + " -ScratchRoot " + quote(scratch)
            + " -InputBytes ([byte[]]@()) -TimeoutSeconds 5 -MaxOutputBytes 4096; $state='accepted' }"
            " catch { $state=$_.Exception.Message }\n@{state=$state}|ConvertTo-Json -Compress")
    assert run_ps(host, body)["state"] == "q4_job_refused"
    assert not marker.exists()


@pytest.mark.parametrize("host", HOSTS)
def test_concurrent_large_stdin_and_stdout_do_not_deadlock(host, tmp_path):
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    work = tmp_path / "work"
    work.mkdir()
    code = ("import sys;sys.stdout.buffer.write(b'Y'*65536);sys.stdout.buffer.flush();"
            "q=sys.stdin.buffer.read();assert len(q)==200000;sys.stdout.buffer.write(b'ok')")
    body = ("$q=New-Object byte[] 200000\n"
            "$r=Invoke-WdQ4ProcessJob -Executable " + quote(sys.executable)
            + " -Arguments @('-I','-S','-B','-c'," + quote(code) + ")"
            + " -WorkingDirectory " + quote(work) + " -ScratchRoot " + quote(scratch)
            + " -InputBytes $q -TimeoutSeconds 5 -MaxOutputBytes 131072\n"
            "@{exit=$r.ExitCode;bytes=$r.Stdout.Length}|ConvertTo-Json -Compress")
    assert run_ps(host, body) == {"exit": 0, "bytes": 65538}


@pytest.mark.parametrize("host", HOSTS)
@pytest.mark.parametrize("when", ["before_dot_source", "after_dot_source"])
def test_preloaded_same_name_type_refuses_without_receiving_input(host, tmp_path, when):
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    work = tmp_path / "work"
    work.mkdir()
    preload = ("Add-Type 'public static class WdQ4ProcessJobNativeV1 { public static bool Called=false; "
               "public static object Run(string a,string[] b,string c,string d,byte[] e,int f,int g) "
               "{ Called=true; return null; } }'\n")
    body = ((preload if when == "after_dot_source" else "")
            + "$q=[Text.Encoding]::UTF8.GetBytes('synthetic-query-sentinel')\n"
            "try { $null=Invoke-WdQ4ProcessJob -Executable " + quote(sys.executable)
            + " -Arguments @('-c','pass') -WorkingDirectory " + quote(work)
            + " -ScratchRoot " + quote(scratch)
            + " -InputBytes $q -TimeoutSeconds 5 -MaxOutputBytes 4096; $state='accepted' }"
            " catch { $state=$_.Exception.Message }\n"
            "@{state=$state;called=[WdQ4ProcessJobNativeV1]::Called}|ConvertTo-Json -Compress")
    assert run_ps(host, body, prelude=preload if when == "before_dot_source" else "") == {
        "state": "q4_job_refused", "called": False}
    assert not list(scratch.iterdir())


@pytest.mark.parametrize("host", HOSTS)
def test_helper_owned_type_can_be_reused_in_same_session(host, tmp_path):
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    work = tmp_path / "work"
    work.mkdir()
    invocation = ("Invoke-WdQ4ProcessJob -Executable " + quote(sys.executable)
            + " -Arguments @('-I','-S','-B','-c','print(42)') -WorkingDirectory " + quote(work)
            + " -ScratchRoot " + quote(scratch)
            + " -InputBytes ([byte[]]@()) -TimeoutSeconds 5 -MaxOutputBytes 4096")
    body = "$a=" + invocation + "\n$b=" + invocation + "\n@{first=$a.ExitCode;second=$b.ExitCode}|ConvertTo-Json -Compress"
    assert run_ps(host, body) == {"first": 0, "second": 0}
    assert len(list(scratch.iterdir())) == 2


@pytest.mark.parametrize("host", HOSTS)
def test_redot_source_discards_ownership_and_refuses_existing_type(host, tmp_path):
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    work = tmp_path / "work"
    work.mkdir()
    invocation = ("Invoke-WdQ4ProcessJob -Executable " + quote(sys.executable)
            + " -Arguments @('-I','-S','-B','-c','print(42)') -WorkingDirectory " + quote(work)
            + " -ScratchRoot " + quote(scratch)
            + " -InputBytes ([byte[]]@()) -TimeoutSeconds 5 -MaxOutputBytes 4096")
    body = ("$a=" + invocation + "\n. " + quote(HELPER)
            + "\ntry { $null=" + invocation + "; $state='accepted' } catch { $state=$_.Exception.Message }\n"
            "@{first=$a.ExitCode;second=$state}|ConvertTo-Json -Compress")
    assert run_ps(host, body) == {"first": 0, "second": "q4_job_refused"}
    assert len(list(scratch.iterdir())) == 1

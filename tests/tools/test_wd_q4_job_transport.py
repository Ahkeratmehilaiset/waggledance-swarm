"""Synthetic Q4 transport integration; no installed bundle or real query."""
import base64
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
WRAPPER = ROOT / "ops/windows/reboot/Invoke-WdQ4Query.ps1"
HELPER = ROOT / "ops/windows/reboot/WdQ4ProcessJob.ps1"
HOSTS = [pytest.param(shutil.which("powershell.exe"), id="powershell"),
         pytest.param(shutil.which("pwsh"), id="pwsh")]


def quote(value):
    return "'" + str(value).replace("'", "''") + "'"


def run_ps(host, script):
    if os.name != "nt" or not host:
        pytest.skip("Windows PowerShell required")
    prefix = "$ErrorActionPreference='Stop'\n. " + quote(WRAPPER) + "\n"
    encoded = base64.b64encode((prefix + script).encode("utf-16-le")).decode("ascii")
    run = subprocess.run([host, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                         capture_output=True, text=True, timeout=25)
    assert run.returncode == 0, (run.returncode, run.stderr)
    assert "synthetic-secret" not in run.stdout + run.stderr
    return json.loads(run.stdout)


@pytest.fixture
def fake(tmp_path):
    root = tmp_path / "synthetic-code"
    tools = root / "tools"
    tools.mkdir(parents=True)
    (tools / "__init__.py").write_text("", encoding="utf-8")
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    return root, tools, scratch


def script_for(root, scratch, timeout):
    return ("$h=[IO.File]::ReadAllBytes(" + quote(HELPER) + ")\n"
            "$sha=[Security.Cryptography.SHA256]::Create();"
            "$hash=[BitConverter]::ToString($sha.ComputeHash($h)).Replace('-','');$sha.Dispose()\n"
            "$plan=[pscustomobject]@{CodeRoot=" + quote(root) + ";ResolvedPins=$null;"
            "JobHelperBytes=$h;JobHelperSha256=$hash;Config=@{interpreter=" + quote(sys.executable)
            + ";timeout=" + str(timeout) + ";scratch_root=" + quote(scratch) + "}}\n"
            "$query=[Text.Encoding]::UTF8.GetBytes('synthetic-secret')\n")


@pytest.mark.parametrize("host", HOSTS)
def test_real_job_transport_keeps_query_off_args_environment_and_disk(host, fake):
    root, tools, scratch = fake
    (tools / "wd_q4_agent_query.py").write_text(
        "import sys,os\n"
        "class VerifiedQ4Config:\n def __init__(self,**x): pass\n"
        "def query_with_verified_config(config,stream):\n"
        " q=stream.read();assert q.hex()=='73796e7468657469632d736563726574'\n"
        " assert all(q.decode() not in a for a in sys.argv)\n"
        " assert all(q.decode() not in v for v in os.environ.values())\n"
        " return {'status':'ABSTAIN','scope':'wd.q4-shared-projected-events.v1',"
        "'descriptor_sha256':'a'*64,'memory_sha256':'b'*64,'results':[]}\n", encoding="utf-8")
    script = script_for(root, scratch, 5) + (
        "$r=Invoke-WdQ4QueryTransport $plan $query\n"
        "@{status=$r.status}|ConvertTo-Json -Compress")
    assert run_ps(host, script)["status"] == "ABSTAIN"
    for directory in (scratch, root):
        for path in directory.rglob("*"):
            if path.is_file():
                assert b"synthetic-secret" not in path.read_bytes()


@pytest.mark.parametrize("host", HOSTS)
def test_same_pinned_transport_can_be_used_twice_in_one_host(host, fake):
    root, tools, scratch = fake
    (tools / "wd_q4_agent_query.py").write_text(
        "class VerifiedQ4Config:\n def __init__(self,**x): pass\n"
        "def query_with_verified_config(config,stream):\n"
        " assert stream.read()==b'synthetic-secret'\n"
        " return {'status':'ABSTAIN','scope':'wd.q4-shared-projected-events.v1',"
        "'descriptor_sha256':'a'*64,'memory_sha256':'b'*64,'results':[]}\n", encoding="utf-8")
    script = script_for(root, scratch, 5) + (
        "$a=Invoke-WdQ4QueryTransport $plan $query\n"
        "$b=Invoke-WdQ4QueryTransport $plan $query\n"
        "@{first=$a.status;second=$b.status}|ConvertTo-Json -Compress")
    assert run_ps(host, script) == {"first": "ABSTAIN", "second": "ABSTAIN"}
    assert len(list(scratch.iterdir())) == 2


@pytest.mark.parametrize("host", HOSTS)
def test_tampered_job_helper_is_refused_before_child(host, fake, tmp_path):
    root, tools, scratch = fake
    marker = tmp_path / "unsafe-child-started.txt"
    (tools / "wd_q4_agent_query.py").write_text(
        "from pathlib import Path\nPath(" + repr(str(marker)) + ").write_text('started')\n", encoding="utf-8")
    script = script_for(root, scratch, 5) + (
        "$plan.JobHelperBytes[0]=($plan.JobHelperBytes[0] -bxor 1)\n"
        "try {$null=Invoke-WdQ4QueryTransport $plan $query;$state='accepted'} "
        "catch {$state=$_.Exception.Message}\n"
        "@{state=$state;started=[IO.File]::Exists(" + quote(marker) + ")}|ConvertTo-Json -Compress")
    assert run_ps(host, script) == {"state": "binding_refused", "started": False}
    assert not marker.exists() and not list(scratch.iterdir())


@pytest.mark.parametrize("host", HOSTS)
def test_changed_pinned_helper_is_not_silently_reused_in_same_host(host, fake):
    root, tools, scratch = fake
    (tools / "wd_q4_agent_query.py").write_text(
        "class VerifiedQ4Config:\n def __init__(self,**x): pass\n"
        "def query_with_verified_config(config,stream):\n"
        " assert stream.read()==b'synthetic-secret'\n"
        " return {'status':'ABSTAIN','scope':'wd.q4-shared-projected-events.v1',"
        "'descriptor_sha256':'a'*64,'memory_sha256':'b'*64,'results':[]}\n", encoding="utf-8")
    script = script_for(root, scratch, 5) + (
        "$first=Invoke-WdQ4QueryTransport $plan $query\n"
        "$plan.JobHelperBytes[0]=($plan.JobHelperBytes[0] -bxor 1)\n"
        "$sha=[Security.Cryptography.SHA256]::Create();"
        "$plan.JobHelperSha256=[BitConverter]::ToString($sha.ComputeHash($plan.JobHelperBytes)).Replace('-','');$sha.Dispose()\n"
        "try {$null=Invoke-WdQ4QueryTransport $plan $query;$state='accepted'} "
        "catch {$state=$_.Exception.Message}\n"
        "@{first=$first.status;second=$state}|ConvertTo-Json -Compress")
    assert run_ps(host, script) == {"first": "ABSTAIN", "second": "binding_refused"}
    assert len(list(scratch.iterdir())) == 1


@pytest.mark.parametrize("host", HOSTS)
def test_timed_out_transport_kills_synthetic_descendant(host, fake, tmp_path):
    root, tools, scratch = fake
    marker = tmp_path / "descendant-escaped.txt"
    child = "import time,pathlib;time.sleep(3);pathlib.Path(" + repr(str(marker)) + ").write_text('escaped')"
    (tools / "wd_q4_agent_query.py").write_text(
        "import sys,subprocess,time\n"
        "class VerifiedQ4Config:\n def __init__(self,**x): pass\n"
        "def query_with_verified_config(config,stream):\n"
        " q=stream.read();assert q==b'synthetic-secret'\n"
        " subprocess.Popen([sys.executable,'-c'," + repr(child) + "],"
        "stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)\n"
        " time.sleep(20)\n", encoding="utf-8")
    script = script_for(root, scratch, 1) + (
        "try {$null=Invoke-WdQ4QueryTransport $plan $query;$state='accepted'} "
        "catch {$state='refused'}\nStart-Sleep -Seconds 4\n"
        "@{state=$state;escaped=[IO.File]::Exists(" + quote(marker) + ")}|ConvertTo-Json -Compress")
    assert run_ps(host, script) == {"state": "refused", "escaped": False}
    assert not marker.exists()
    for path in scratch.rglob("*"):
        if path.is_file():
            assert b"synthetic-secret" not in path.read_bytes()

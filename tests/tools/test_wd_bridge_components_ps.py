"""F29b PowerShell front end tests (authored per operator directive; NOT executed yet).

A synthetic bundle holds a STUB Invoke-WdBridgePython.ps1 whose SHA-256 is pinned
in a synthetic deployment-manifest.json; the external anchor is that manifest's
SHA-256. The stub records the argv it received and replays a scripted report and
exit status, so the front end is tested as a pure relay with no Python, provider
or real bundle involved. Every refusal has a same-fixture success twin.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / ".agent-bridge" / "bin" / "Test-WdBridgeComponents.ps1"
SHELLS = list(dict.fromkeys(filter(None, (shutil.which("pwsh"), shutil.which("powershell.exe")))))
pytestmark = pytest.mark.skipif(not SHELLS, reason="PowerShell is required")
_SCRUB = ("AGENT_BRIDGE_", "WD_", "CLAUDE_CODE_", "GIT_", "STUB_")

STUB = r"""
[CmdletBinding(PositionalBinding = $false)]
param(
    [Parameter(Mandatory, Position = 0)] [string] $Tool,
    [switch] $VerifyPackage,
    [Parameter(ValueFromRemainingArguments)] [string[]] $ToolArguments = @()
)
$record = [ordered]@{ tool = $Tool; verify_package = [bool]$VerifyPackage; argv = @($ToolArguments) }
[IO.File]::WriteAllText($env:STUB_RECORD, ($record | ConvertTo-Json -Compress -Depth 4))
if ($env:STUB_MODE -ceq 'throw') { throw 'pinned bridge invocation refuses a tool outside the packaged entrypoints: tools/wd_bridge_doctor.py' }
foreach ($line in ($env:STUB_LINES -split "`n")) { if ($line) { Write-Output $line } }
$global:LASTEXITCODE = [int]$env:STUB_CODE
"""


def _report(verdict: str) -> str:
    return json.dumps({"schema": "wd.bridge-doctor-report.v1", "verdict": verdict, "lane": "claude-rco-2",
                       "installs_performed": False, "authority_effect": "none"}, sort_keys=True)


def _bundle(tmp_path: Path, *, tamper_wrapper: bool = False, pin_wrapper: bool = True) -> tuple[Path, str]:
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    wrapper = bundle / "Invoke-WdBridgePython.ps1"
    wrapper.write_text(STUB, encoding="utf-8")
    files = {"Invoke-WdBridgePython.ps1": hashlib.sha256(wrapper.read_bytes()).hexdigest().upper()} if pin_wrapper else {}
    manifest = bundle / "deployment-manifest.json"
    manifest.write_text(json.dumps({"schema_version": 1, "files": files}), encoding="utf-8")
    if tamper_wrapper:
        wrapper.write_text(STUB + "\n# tampered\n", encoding="utf-8")
    return wrapper, hashlib.sha256(manifest.read_bytes()).hexdigest().upper()


def _args(tmp_path: Path) -> list[str]:
    cfg = tmp_path / "cfg dir; $(calc)"
    cfg.mkdir(exist_ok=True)
    return ["-ManifestPath", str(cfg / "bridge_components.json"), "-PathsConfig", str(cfg / "paths.json"),
            "-Lane", "claude-rco-2"]


def _run(shell: str, tmp_path: Path, args: list[str], *, wrapper: Path | None, anchor: str | None,
         lines: str = "", code: int = 0, mode: str = "") -> tuple[subprocess.CompletedProcess, dict | None]:
    env = {k: v for k, v in os.environ.items() if not k.startswith(_SCRUB)}
    record = tmp_path / "stub-record.json"
    if record.exists():
        record.unlink()
    if wrapper is not None:
        env["WD_BRIDGE_PYTHON_WRAPPER"] = str(wrapper)
    if anchor is not None:
        env["WD_REBOOT_EXPECTED_MANIFEST_HASH"] = anchor
    env.update(STUB_RECORD=str(record), STUB_LINES=lines, STUB_CODE=str(code), STUB_MODE=mode)
    process = subprocess.run([shell, "-NoProfile", "-NonInteractive", "-File", str(SCRIPT), *args],
                             env=env, capture_output=True, text=True, encoding="utf-8", timeout=120)
    return process, (json.loads(record.read_text(encoding="utf-8")) if record.exists() else None)


def _json(process: subprocess.CompletedProcess) -> dict:
    lines = [line for line in process.stdout.splitlines() if line.strip()]
    assert len(lines) == 1, process.stdout
    return json.loads(lines[0])


@pytest.mark.parametrize("shell", SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize("verdict,code", [("ready", 0), ("degraded", 1), ("refuse", 2), ("invalid_input", 3)])
def test_relays_report_and_exit_status_unchanged(tmp_path, shell, verdict, code):
    wrapper, anchor = _bundle(tmp_path)
    process, record = _run(shell, tmp_path, _args(tmp_path), wrapper=wrapper, anchor=anchor,
                           lines=_report(verdict), code=code)
    assert process.returncode == code, process.stderr
    assert process.stdout.strip() == _report(verdict)
    assert record["tool"] == "tools/wd_bridge_doctor.py" and record["verify_package"] is True


@pytest.mark.parametrize("shell", SHELLS, ids=lambda p: Path(p).stem)
def test_argv_is_passed_as_array_without_shell_building(tmp_path, shell):
    wrapper, anchor = _bundle(tmp_path)
    args = _args(tmp_path) + ["-EvidencePath", str(tmp_path / "ev idence.json"), "-Now", "2026-09-29T21:00:00Z"]
    process, record = _run(shell, tmp_path, args, wrapper=wrapper, anchor=anchor, lines=_report("ready"))
    assert process.returncode == 0, process.stderr
    cfg = tmp_path / "cfg dir; $(calc)"
    assert record["argv"] == ["--manifest", str(cfg / "bridge_components.json"),
                              "--paths-config", str(cfg / "paths.json"), "--lane", "claude-rco-2",
                              "--evidence", str(tmp_path / "ev idence.json"),
                              "--now", "2026-09-29T21:00:00Z"]


@pytest.mark.parametrize("shell", SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize("case", ["no_wrapper", "relative_wrapper", "wrong_name", "no_anchor",
                                  "anchor_mismatch", "wrapper_tampered", "wrapper_unpinned"])
def test_unverified_or_missing_pin_refuses_without_calling_anything(tmp_path, shell, case):
    wrapper, anchor = _bundle(tmp_path, tamper_wrapper=case == "wrapper_tampered",
                              pin_wrapper=case != "wrapper_unpinned")
    if case == "no_wrapper":
        wrapper = None
    elif case == "relative_wrapper":
        wrapper = Path("bundle") / "Invoke-WdBridgePython.ps1"
    elif case == "wrong_name":
        renamed = wrapper.with_name("Invoke-Other.ps1")
        shutil.copyfile(wrapper, renamed)
        wrapper = renamed
    elif case == "no_anchor":
        anchor = None
    elif case == "anchor_mismatch":
        anchor = "0" * 64
    process, record = _run(shell, tmp_path, _args(tmp_path), wrapper=wrapper, anchor=anchor,
                           lines=_report("ready"), code=0)
    assert process.returncode == 3, process.stdout
    assert _json(process)["verdict"] == "doctor_unavailable"
    assert record is None  # the stub (stand-in for the doctor) never ran


@pytest.mark.parametrize("shell", SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize("args_patch", [
    # Values that start with "-" are omitted on purpose: PowerShell's own binder would
    # treat them as parameter names before this script runs, so they test nothing here.
    {"-ManifestPath": "relative.json"}, {"-PathsConfig": "C:paths.json"}, {"-Lane": "Bad Lane"},
    {"-Lane": "9lane"}, {"-EvidencePath": "ev.json"}, {"-Now": "2026-09-29 21:00"}, {"-Now": "2026-09-29T21:00:00"}])
def test_invalid_arguments_refuse_before_the_wrapper(tmp_path, shell, args_patch):
    wrapper, anchor = _bundle(tmp_path)
    args = _args(tmp_path)
    for key, value in args_patch.items():
        if key in args:
            args[args.index(key) + 1] = value
        else:
            args += [key, value]
    process, record = _run(shell, tmp_path, args, wrapper=wrapper, anchor=anchor, lines=_report("ready"))
    assert process.returncode == 3
    assert _json(process)["verdict"] == "invalid_input" and record is None


@pytest.mark.parametrize("shell", SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize("lines,code", [
    (_report("ready"), 2),            # verdict/exit mismatch
    (_report("degraded"), 0),
    ("", 2),                          # argparse-style failure: no JSON, exit 2
    ("Traceback (most recent call last)", 1),
    ('{"schema":"other","verdict":"ready"}', 0),
    ('{"schema":"wd.bridge-doctor-report.v1","verdict":"READY"}', 0),
])
def test_inconsistent_or_missing_report_refuses(tmp_path, shell, lines, code):
    wrapper, anchor = _bundle(tmp_path)
    process, _ = _run(shell, tmp_path, _args(tmp_path), wrapper=wrapper, anchor=anchor, lines=lines, code=code)
    assert process.returncode == 3
    assert _json(process)["verdict"] == "doctor_unavailable"


@pytest.mark.parametrize("shell", SHELLS, ids=lambda p: Path(p).stem)
def test_wrapper_refusal_is_doctor_unavailable(tmp_path, shell):
    wrapper, anchor = _bundle(tmp_path)
    process, record = _run(shell, tmp_path, _args(tmp_path), wrapper=wrapper, anchor=anchor, mode="throw")
    assert process.returncode == 3
    report = _json(process)
    assert report["verdict"] == "doctor_unavailable" and "packaged entrypoints" in report["error"]
    assert record is not None  # the wrapper was reached and refused


def test_script_source_has_no_fallback_execution_or_writes():
    source = SCRIPT.read_text(encoding="utf-8")
    for forbidden in ("Invoke-Expression", "iex ", "Start-Process", "cmd /c", "cmd.exe", "python.exe",
                      "Get-Command python", "$PSScriptRoot", "Set-Content", "Out-File", "Add-Content",
                      "WriteAllText", "New-Item", "Remove-Item", "Invoke-WebRequest", "Invoke-RestMethod"):
        assert forbidden not in source, forbidden
    assert re.search(r"\$env:WD_BRIDGE_PYTHON_WRAPPER", source)
    assert "not packaged in bundle 8a7576af" in " ".join(source.split()).lower()

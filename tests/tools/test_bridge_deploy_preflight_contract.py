"""Deploy-WdRebootBundle.ps1 safe preflight contract (Lead 5bfff409; RCO2 7643c571 F1-F3).

The deployer's exact preflight region (from "Running mutation-free deployment preflight" up to the -DryRun return)
is cut out of the source and run in an isolated pwsh child with stub Resolve/Register/Initialize scripts under
tmp_path and a fake Get-ScheduledTask: no deploy, install, provider, Grok, scheduler or real C: file is touched.

Contract:
- F1: -DryRun never runs the Grok model resolver (not even its -DryRun probe, which calls the provider CLI).
- F2: -DryRun and the actual install assert the read-only (no -Apply) Grok recovery readiness before the preflight
  is declared ready; -StageOnly skips it (artifact-store semantics unchanged).
- F3: -DryRun and the actual install refuse unless WD-Supervisor is Disabled and not Running; the installer never
  disables it. A missing task refuses unless -SkipTaskRegistration (then a warning, and no registration).
"""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]
DEPLOYER = ROOT / "ops" / "windows" / "reboot" / "Deploy-WdRebootBundle.ps1"
PWSH = shutil.which("pwsh")
START = "Write-Host 'Running mutation-free deployment preflight...'"
END = "if ($DryRun) {"

pytestmark = pytest.mark.skipif(PWSH is None, reason="pwsh is not installed")

HARNESS = r"""
param([string] $Region, [string] $Stubs, [string] $Log, [string] $Out, [string] $Supervisor,
    [string] $Mode, [string] $Skip)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$env:PF_LOG = $Log
function Get-ScheduledTask {
    param([string] $TaskName)
    Add-Content -LiteralPath $env:PF_LOG -Value 'Get-ScheduledTask'
    $other = [pscustomobject]@{ TaskName = 'WD-Other'; State = 'Running'; Settings = [pscustomobject]@{ Enabled = $true } }
    $tasks = @($other)
    $make = { param($enabled, $state) [pscustomobject]@{ TaskName = 'WD-Supervisor'; State = $state
        Settings = [pscustomobject]@{ Enabled = $enabled } } }
    switch ($Supervisor) {
        'disabled' { $tasks += & $make $false 'Disabled' }
        'enabled' { $tasks += & $make $true 'Ready' }
        'running' { $tasks += & $make $false 'Running' }
        'duplicate' { $tasks += & $make $false 'Disabled'; $tasks += & $make $false 'Disabled' }
        'missing' { }
        default { throw "unknown supervisor case $Supervisor" }
    }
    return $tasks
}
$StageOnly = $Mode -ceq 'stage'
$DryRun = $Mode -ceq 'dry'
$SkipGrokResolve = $Skip.Contains('grok')
$SkipTaskRegistration = $Skip.Contains('task')
$materializedRebootRoot = $Stubs
$machineFull = Join-Path $Stubs 'machine'
$warnings = New-Object System.Collections.Generic.List[string]
function Write-Warning { param([string] $Message) $warnings.Add($Message) }
function Write-Host { param($Object) }
try { . ([scriptblock]::Create([IO.File]::ReadAllText($Region))); $result = 'ready' }
catch { $result = $_.Exception.Message }
$calls = if (Test-Path -LiteralPath $Log) { @(Get-Content -LiteralPath $Log) } else { @() }
$record = [ordered]@{ result = $result; calls = @($calls); warnings = @($warnings) }
[IO.File]::WriteAllText($Out, ($record | ConvertTo-Json -Depth 4 -Compress), (New-Object Text.UTF8Encoding $false))
"""

STUB = "Add-Content -LiteralPath $env:PF_LOG -Value ('{name} ' + ($args -join ' ')).Trim()\n"
FAILING_INIT = "Add-Content -LiteralPath $env:PF_LOG -Value 'Initialize'\n" \
    "throw 'Grok recovery state is missing; run the controlled installation first'\n"


def _region(deployer: Path = DEPLOYER) -> str:
    text = deployer.read_text(encoding="utf-8")
    start = text.index(START)
    end = text.index(END, start)
    return text[start:end]


def _preflight(tmp_path: Path, mode: str, supervisor: str = "disabled", skip: str = "", init_fails: bool = False,
               deployer: Path = DEPLOYER) -> dict:
    stubs = tmp_path / "reboot"
    stubs.mkdir()
    for file, name in (("Resolve-WdGrokModel.ps1", "Resolve"), ("Register-WdScheduledTasks.ps1", "Register"),
                       ("Initialize-WdGrokRecovery.ps1", "Initialize")):
        body = FAILING_INIT if init_fails and name == "Initialize" else STUB.replace("{name}", name)
        (stubs / file).write_text(body, encoding="utf-8")
    region = tmp_path / "region.ps1"
    region.write_text(_region(deployer), encoding="utf-8")
    harness = tmp_path / "harness.ps1"
    harness.write_text(HARNESS, encoding="utf-8")
    out = tmp_path / "out.json"
    done = subprocess.run([PWSH, "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(harness),
                           "-Region", str(region), "-Stubs", str(stubs), "-Log", str(tmp_path / "calls.log"),
                           "-Out", str(out), "-Supervisor", supervisor, "-Mode", mode, "-Skip", skip or "none"],
                          capture_output=True, timeout=120)
    assert done.returncode == 0, done.stderr.decode("utf-8", "replace")
    return json.loads(out.read_text(encoding="utf-8"))


def _scripts(record: dict) -> list[str]:
    return [call for call in record["calls"] if call != "Get-ScheduledTask"]


# --- F1: a dry run never reaches the resolver (provider CLI); the actual install still probes it

def test_a_dry_run_never_runs_the_grok_resolver(tmp_path):
    record = _preflight(tmp_path, "dry")
    assert record["result"] == "ready"
    assert not [call for call in record["calls"] if call.startswith("Resolve")]
    assert _scripts(record) == ["Initialize", "Register -SupervisorScript " + str(tmp_path / "reboot" /
                                                                               "wd_supervisor.ps1")]


def test_the_actual_install_preflight_still_probes_the_resolver_dry(tmp_path):
    record = _preflight(tmp_path, "install")
    assert record["result"] == "ready"
    assert _scripts(record)[:2] == ["Initialize", "Resolve -DryRun -OutputDirectory " + str(tmp_path / "reboot" /
                                                                                            "machine")]


def test_skip_grok_resolve_skips_the_probe_on_install(tmp_path):
    record = _preflight(tmp_path, "install", skip="grok")
    assert record["result"] == "ready"
    assert not [call for call in record["calls"] if call.startswith("Resolve")]


# --- F2: the read-only Grok recovery readiness is asserted by every preflight except -StageOnly

@pytest.mark.parametrize("mode", ["dry", "install"])
def test_grok_recovery_readiness_is_asserted_read_only(tmp_path, mode):
    record = _preflight(tmp_path, mode)
    assert "Initialize" in _scripts(record)  # no argument at all: never -Apply


@pytest.mark.parametrize("mode", ["dry", "install"])
def test_a_failed_grok_recovery_readiness_refuses_before_resolve_and_register(tmp_path, mode):
    record = _preflight(tmp_path, mode, init_fails=True)
    assert record["result"] == "Grok recovery state is missing; run the controlled installation first"
    assert _scripts(record) == ["Initialize"]


def test_stage_only_touches_no_task_grok_or_recovery_check(tmp_path):
    record = _preflight(tmp_path, "stage", supervisor="enabled", init_fails=True)
    assert record == {"result": "ready", "calls": [], "warnings": []}


# --- F3: Supervisor OFF is asserted, never enforced by the installer

@pytest.mark.parametrize("mode", ["dry", "install"])
@pytest.mark.parametrize("supervisor, state", [("enabled", "enabled=True, state=Ready"),
                                               ("running", "enabled=False, state=Running")])
def test_an_enabled_or_running_supervisor_refuses_before_any_script(tmp_path, mode, supervisor, state):
    record = _preflight(tmp_path, mode, supervisor=supervisor)
    assert record["result"] == ("refusing to deploy: WD-Supervisor must be Disabled and not Running ("
                                + state + "); this installer never disables it itself")
    assert _scripts(record) == []


@pytest.mark.parametrize("skip", ["none", "task", "grok,task"])
def test_skip_task_registration_never_bypasses_the_supervisor_off_check(tmp_path, skip):
    record = _preflight(tmp_path, "install", supervisor="enabled", skip=skip)
    assert record["result"].startswith("refusing to deploy: WD-Supervisor must be Disabled and not Running")


def test_a_missing_supervisor_task_refuses_without_skip_task_registration(tmp_path):
    record = _preflight(tmp_path, "install", supervisor="missing")
    assert record["result"].startswith("refusing to deploy: the WD-Supervisor scheduled task is missing")
    assert _scripts(record) == []


def test_a_missing_supervisor_task_with_skip_task_registration_warns_and_registers_nothing(tmp_path):
    record = _preflight(tmp_path, "install", supervisor="missing", skip="task")
    assert record["result"] == "ready"
    assert record["warnings"] == ["WD-Supervisor scheduled task is missing; -SkipTaskRegistration given, "
                                  "so no task is registered or changed."]
    assert not [call for call in record["calls"] if call.startswith("Register")]


def test_a_duplicate_supervisor_task_refuses(tmp_path):
    record = _preflight(tmp_path, "dry", supervisor="duplicate")
    assert record["result"] == "refusing to deploy: more than one WD-Supervisor scheduled task exists"


@pytest.mark.parametrize("mode", ["dry", "install"])
def test_a_disabled_idle_supervisor_is_ready_and_never_changed(tmp_path, mode):
    record = _preflight(tmp_path, mode)
    assert record["result"] == "ready"
    assert not any("Apply" in call or "Disable" in call or "Enable" in call for call in record["calls"])


# --- static: both recovery calls stay read-only; the install-path one still follows the final-pair gate

def test_the_preflight_and_install_recovery_calls_are_read_only_and_ordered():
    text = DEPLOYER.read_text(encoding="utf-8")
    preflight = text.index("$grokRecoveryReadiness = Join-Path $materializedRebootRoot 'Initialize-WdGrokRecovery.ps1'")
    gate = text.index("$ExpectedFinalCommit -cnotmatch '^[0-9a-f]{40}$'")
    install = text.index("& (Join-Path $targetRoot 'Initialize-WdGrokRecovery.ps1') | Out-Host")
    assert text.index(START) < preflight < text.index("DRY RUN: no files") < gate < install
    assert text.count("Initialize-WdGrokRecovery.ps1") == 2
    assert "& $grokRecoveryReadiness | Out-Host" in text

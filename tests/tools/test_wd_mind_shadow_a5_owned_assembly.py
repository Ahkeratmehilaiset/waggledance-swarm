"""Strict-fake tests of the internal A5 owned assembly (Lead's v16): the actual factory and the actual parent Core run
exactly once on one admitted Config from verified Source closures; refusals come before any write or runner call; the
receipt keeps the executed Source hashes, the factory record and the original Core result. The synthetic runner is
reused from the factory/Core integration test, so no type is compiled and no process, job or member exists."""
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ASSEMBLY = ROOT / "tests/windows/wd_mind_shadow_a5_owned_assembly.ps1"
FACTORY = ROOT / "tests/windows/wd_mind_shadow_a5_owned_factory.ps1"
PARENT = ROOT / "tests/windows/wd_mind_shadow_a5_owned_parent.ps1"
CONTROLLER = ROOT / "tests/windows/wd_mind_shadow_a5_controller.ps1"
LAUNCH = "5a5a5a5a-0000-4000-8000-000000000016"  # synthetic, never a real launch id


def _integration_runner():
    path = ROOT / "tests/tools/test_wd_mind_shadow_a5_provider_core_integration.py"
    spec = importlib.util.spec_from_file_location("a5_integration_fixture", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.RUNNER


HOST = r"""
param([string]$Assembly, [string]$Fixture)
$ErrorActionPreference = 'Stop'; Set-StrictMode -Version Latest
foreach ($name in @('Add-Type', 'Start-Process', 'Stop-Process', 'Start-Job', 'Invoke-Expression', 'Get-Process',
        'Get-CimInstance', 'Get-WmiObject', 'Set-Acl', 'Get-Acl', 'New-Item')) {
    Set-Item -Path "function:global:$name" -Value ([scriptblock]::Create("throw 'unexpected native boundary: $name'"))
}
. $Assembly
$spec = ConvertFrom-Json -InputObject ([IO.File]::ReadAllText($Fixture))
# Test-only command oracle for the synthetic runner: two named functions of the frozen controller, never its body.
$tokens = $null; $errors = $null
$ast = [Management.Automation.Language.Parser]::ParseInput([IO.File]::ReadAllText($spec.controller), [ref]$tokens,
    [ref]$errors)
$parts = @(foreach ($name in @('Get-WdA5Literal', 'Get-WdA5MemberCommand')) {
        @($ast.EndBlock.Statements | Where-Object {
                $_ -is [Management.Automation.Language.FunctionDefinitionAst] -and $_.Name -ceq $name })[0].Extent.Text
    })
$global:A5CommandOracle = New-Module -ScriptBlock ([scriptblock]::Create($parts -join "`n"))
$rows = @()
foreach ($item in $spec.rows) {
    $run = $spec.audit + '\run-' + $item.name
    $evidence = $spec.audit + '\evidence-' + $item.name
    [void][IO.Directory]::CreateDirectory($run); [void][IO.Directory]::CreateDirectory($evidence)
    $inventory = {
        @(foreach ($root in @($run, $evidence)) {
                'D ' + $root
                [IO.Directory]::GetFileSystemEntries($root, '*', [IO.SearchOption]::AllDirectories) |
                    ForEach-Object { 'E ' + $_ }
            }) | Sort-Object
    }
    $before = @(& $inventory)
    $global:A5Calls = New-Object Collections.Generic.List[string]
    $global:A5Held = @{}; $global:A5Killed = $false; $global:A5Cleaning = $false; $global:A5Mode = [string]$item.mode
    $global:A5KillUtc = $null; $global:A5Constructor = $null; $global:A5Ready = $null
    $global:A5ControllerTime = '2026-10-22T00:00:01.0000000Z'
    $global:A5Config = @{
        Context = @{ schema = 'wd.mind-shadow-a5-controller-context.v1'; harness_run_id = 'SYNTHETIC-ASSEMBLY'
            launch_id = $spec.launch; source_commit = ('a' * 40); manifest_sha256 = ('B' * 64)
            parent_pid = [string]$PID; parent_creation_utc = '2026-10-22T00:00:00.0000000Z' }
        HostPath = $spec.host; LoopPath = $spec.loop; MemberPath = $spec.member; ControllerPath = $spec.controller
        RunRoot = $run; EvidenceRoot = $evidence
        Pins = @{ host_sha256 = $spec.pins.host; loop_sha256 = $spec.pins.loop; member_sha256 = $spec.pins.member
            controller_sha256 = $spec.pins.controller }
        ControllerReadySeconds = 10; HoldSeconds = 60; SafetyMarginSeconds = 5; ReadyWaitSeconds = 5
        ExitWaitSeconds = 2; DrainSeconds = 2
    }
    if ($item.partial) { $global:A5Config.Remove('DrainSeconds') }
    $paths = @{ parent = $spec.parent; factory = $spec.factory; runner = $spec.runner }
    $pins = @{ parent = $spec.pins.parent; factory = $spec.pins.factory; runner = $spec.pins.runner }
    if ($item.path_key) { $paths[$item.path_key] = $item.path_value }
    if ($item.pin_key) { $pins[$item.pin_key] = $item.pin_value }
    $row = [ordered]@{ name = $item.name }
    try {
        $row.receipt = Invoke-WdA5OwnedAssemblyCore -ApprovedNativeExecution:([bool]$item.approved) `
            -Config $global:A5Config -ParentPath $paths.parent -ExpectedParentSha256 $pins.parent `
            -FactoryPath $paths.factory -ExpectedFactorySha256 $pins.factory -RunnerPath $paths.runner `
            -ExpectedRunnerSha256 $pins.runner
        $row.result = 'RAN'
    } catch { $row.result = 'ERROR'; $row.error = [string]$_.Exception.Message }
    finally { foreach ($s in @($global:A5Held.Values)) { $s.Dispose() } }
    $row.calls = @($global:A5Calls)
    $row.inv_before = @($before)
    $row.inv_after = @(& $inventory)
    $row.parent_pid = [string]$PID
    $rows += $row
}
ConvertTo-Json -InputObject @($rows) -Compress -Depth 16
"""

ROWS = {
    "positive": {}, "core_unknown": {"mode": "unknown_accounting"}, "not_approved": {"approved": False},
    "partial_config": {"partial": True}, "missing_parent_pin": {"pin_key": "parent", "pin_value": ""},
    "lowercase_factory_pin": {"pin_key": "factory", "pin_value": "LOWER"},
    "factory_pin_mismatch": {"pin_key": "factory", "pin_value": "0" * 64},
    "substituted_factory": {"path_key": "factory", "path_value": "COPY"},
    "relative_runner": {"path_key": "runner", "path_value": "pins\\runner.ps1"},
}
REFUSED = {"not_approved": "explicit opt-in", "partial_config": "unknown parent receipt keys",
           "missing_parent_pin": "parent pin is not an uppercase SHA-256",
           "lowercase_factory_pin": "factory pin is not an uppercase SHA-256",
           "factory_pin_mismatch": "factory file is not its pinned bytes",
           "substituted_factory": "factory file is not its pinned bytes",
           "relative_runner": "runner path is not a full printable C:\\ path"}


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest().upper()


HOSTS = [(name, shutil.which(name)) for name in ("pwsh", "powershell") if shutil.which(name)]


@pytest.fixture(scope="module", params=HOSTS or [("no-host", None)], ids=[h[0] for h in HOSTS] or ["no-host"])
def rows(request, tmp_path_factory):
    label, executable = request.param
    if os.name != "nt" or executable is None:
        pytest.skip("Windows C: NTFS and a PS5.1/PS7 host only; untested, not native evidence")
    folder = tmp_path_factory.mktemp("a5assembly")
    pins = folder / "pins"
    pins.mkdir()
    for name in ("host.exe", "loop.ps1", "member.ps1"):
        (pins / name).write_bytes(("SYNTHETIC " + name.split(".")[0] + "\n").encode("ascii"))
    runner = pins / "runner.ps1"
    runner.write_bytes(_integration_runner().encode("utf-8"))
    copy = pins / "factory-copy.ps1"  # a substituted factory: one extra trailing comment line
    copy.write_bytes(FACTORY.read_bytes() + b"# substituted\r\n")
    (folder / ".codex-audit").mkdir()
    items = []
    for name, change in ROWS.items():
        item = {"name": name, "approved": True, "mode": "", "partial": False, "path_key": "", "path_value": "",
                "pin_key": "", "pin_value": ""}
        item.update(change)
        if item["path_value"] == "COPY":
            item["path_value"] = str(copy)
        if item["pin_value"] == "LOWER":
            item["pin_value"] = _sha(FACTORY).lower()
        items.append(item)
    spec = {"parent": str(PARENT), "factory": str(FACTORY), "controller": str(CONTROLLER), "runner": str(runner),
            "host": str(pins / "host.exe"), "loop": str(pins / "loop.ps1"), "member": str(pins / "member.ps1"),
            "audit": str(folder / ".codex-audit"), "launch": LAUNCH, "rows": items}
    spec["pins"] = {key: _sha(spec[key]) for key in ("parent", "factory", "controller", "runner", "host", "loop",
                                                    "member")}
    fixture, host = folder / "fixture.json", folder / "host.ps1"
    fixture.write_text(json.dumps(spec), encoding="utf-8")
    host.write_bytes(HOST.encode("utf-8"))
    done = subprocess.run([executable, "-NoProfile", "-NonInteractive", "-File", str(host), str(ASSEMBLY),
                           str(fixture)], capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr[-4000:]
    return {row["name"]: row for row in json.loads(done.stdout.strip().splitlines()[-1])}, spec


@pytest.mark.parametrize("name", sorted(REFUSED))
def test_assembly_refuses_before_any_write_or_runner_call(rows, name):
    row = rows[0][name]
    assert row["result"] == "ERROR" and REFUSED[name] in row["error"], row
    assert row["calls"] == [] and row["inv_after"] == row["inv_before"], row  # no write, no runner operation


def test_assembly_runs_the_actual_factory_and_core_once_and_keeps_the_receipt(rows):
    row, spec = rows[0]["positive"], rows[1]
    assert row["result"] == "RAN", row
    receipt = row["receipt"]
    assert receipt["schema"] == "wd.mind-shadow-a5-owned-assembly-receipt.v1" and receipt["whole_A5_PASS"] is False
    assert receipt["status"] == "OBSERVATIONS_COMPLETE_NOT_A5_PASS" and receipt["native_authority"].startswith("none")
    for key in ("parent", "factory", "runner"):
        assert receipt["sources"][key] == {"path": spec[key], "sha256": spec["pins"][key]}, key
    assert receipt["config"]["context"]["launch_id"] == LAUNCH and receipt["config"]["pins"]["loop_sha256"] == (
        spec["pins"]["loop"])
    core, factory, calls = receipt["core"], receipt["factory"], row["calls"]
    assert core["status"] == "OBSERVATIONS_COMPLETE_NOT_A5_PASS" and core["errors"] == []
    assert core["whole_A5_PASS"] is False
    assert receipt["core_error"] is None and factory["errors"] == [] and factory["launch"]["calls"] == 1
    assert calls.count("initialize-fake") == calls.count("constructor-fake") == 1, calls  # exactly once
    assert calls.count("root-only-stop") == calls.count("outer-stop") == calls.count("outer-dispose") == 1, calls
    assert calls[0] == "identity-" + row["parent_pid"] and calls[-2:] == ["outer-stop", "outer-dispose"], calls


def test_assembly_keeps_an_original_core_failure_as_unknown(rows):
    row = rows[0]["core_unknown"]
    assert row["result"] == "RAN", row
    receipt = row["receipt"]
    assert receipt["status"] == "UNKNOWN" and receipt["whole_A5_PASS"] is False
    assert receipt["core"]["observation"] is None
    assert receipt["core"]["status"] == "UNKNOWN" and receipt["core"]["errors"] and receipt["core_error"] is None
    assert row["calls"].count("constructor-fake") == 1 and row["calls"].count("outer-stop") == 1, row


def test_assembly_direct_execution_refuses_and_dot_source_is_inert():
    if os.name != "nt" or not HOSTS:
        pytest.skip("needs a Windows PowerShell host: untested, not evidence")
    for _, executable in HOSTS:
        done = subprocess.run([executable, "-NoProfile", "-NonInteractive", "-File", str(ASSEMBLY)],
                              capture_output=True, text=True, timeout=60)
        assert done.returncode == 3 and "refused: " in done.stderr, done
        inert = subprocess.run([executable, "-NoProfile", "-NonInteractive", "-Command",
                                ". '" + str(ASSEMBLY) + "'; (Get-Command Invoke-WdA5OwnedAssemblyCore).Name"],
                               capture_output=True, text=True, timeout=60)
        assert inert.returncode == 0 and inert.stdout.strip() == "Invoke-WdA5OwnedAssemblyCore", inert

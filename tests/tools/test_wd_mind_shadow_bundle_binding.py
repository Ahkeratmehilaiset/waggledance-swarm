"""Synthetic behavior of the Mind shadow job adapter's bundle pins and its root command (PR1916 bot P1-A and P1-B).

The adapter is never executed here. A test host parses it with the PowerShell parser, defines its top-level functions,
replaces the reparse walk and the file snapshot with fakes and every other disk reader and the process, job and
expression cmdlets with functions that throw, and runs the adapter's OWN pin-validation and root-command statements
(extracted from the parsed file by statement ranges, not reimplemented) against a FAKE bundle tree below the test's
temporary directory. P1-A: WD_BRIDGE_CODE_ROOT and WD_BRIDGE_BIN must be exactly the bundle's tools-bootstrap and its
.agent-bridge\\bin, with no reparse point on their paths; a descendant, another directory or a reparse ancestry is
refused. P1-B: the generated root command is then run once in a child of the same PowerShell host against a FAKE
wrapper with the deployed wrapper's parameter shape; it must bind -VerifyPackage as a real switch, and a modeled
verification failure must keep the fake producer from running. The live wrapper, producer, bundle and bridge are
never invoked. A verdict here is synthetic author proof, never native acceptance (A1-A7 stays NOT_RUN). Findings:
the PR1916 review bot (P1-A, P1-B), RCO1 and RCO2 concurring; repair assignment: Lead; fable-5 wrote this test and
the adapter.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

# Windows scope (Lead fixture assignment v2): the pin contract under test is Windows-only (the adapter's bundle prefix
# trims and appends a backslash, and the fake reparse ancestry below uses the same separator), so on another OS these
# synthetic pin and command runs are skipped as an untested platform, never counted as evidence.
pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows-only bundle pin contract (backslash bundle prefix): "
                                "non-Windows is an untested platform for these synthetic runs, not evidence")

ROOT = Path(__file__).resolve().parents[2]
ADAPTER = ROOT / "ops" / "windows" / "reboot" / "Start-WdMindShadowJob.ps1"
WRAPPER_HASH = "AB" * 32

HOST = r"""
param([string] $Adapter, [string] $Fixture)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
foreach ($name in @('Start-Process', 'Stop-Process', 'Start-Job', 'Invoke-Expression', 'Invoke-Command', 'Add-Type',
        'Wait-Process', 'Get-Process', 'Get-CimInstance', 'Get-WmiObject', 'New-Item', 'Remove-Item', 'Set-Content',
        'Out-File', 'Move-Item', 'Copy-Item')) {
    Set-Item -Path "function:global:$name" -Value ([scriptblock]::Create("throw 'unexpected native call: $name'"))
}
$tokens = $null; $errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile($Adapter, [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw 'the adapter does not parse' }
$top = @($ast.EndBlock.Statements)
foreach ($statement in $top) {
    if ($statement -is [Management.Automation.Language.FunctionDefinitionAst]) {
        . ([scriptblock]::Create($statement.Extent.Text))
    }
}
foreach ($name in @('Publish-WdShadowRecord', 'Test-WdShadowOffSwitch', 'Get-WdShadowDiskShortfall',
        'Test-WdShadowLiveDirLock', 'Get-WdShadowGuardStop')) {
    Set-Item -Path "function:$name" -Value ([scriptblock]::Create("throw 'unexpected disk read: $name'"))
}
function Read-WdShadowFileSnapshot {
    param([string] $Path)
    return [pscustomobject]@{ Hash = [string]$global:WdFake['wrapper_hash']; Text = '' }
}
function Get-WdShadowReparseAncestor {
    param([string] $Path)
    foreach ($flagged in @($global:WdFake['reparse'])) {
        if ($null -eq $flagged) { continue }
        if ([string]::Equals($Path, $flagged, [StringComparison]::OrdinalIgnoreCase) -or
            $Path.StartsWith($flagged + '\', [StringComparison]::OrdinalIgnoreCase)) {
            return "$flagged is a reparse point"
        }
    }
    return $null
}
function Get-StatementRange([object[]] $Statements, [string] $First, [string] $Last) {
    $from = -1; $to = -1
    for ($i = 0; $i -lt $Statements.Count; $i++) {
        $text = $Statements[$i].Extent.Text
        if ($from -lt 0 -and $text.StartsWith($First)) { $from = $i }
        if ($from -ge 0 -and $to -lt 0 -and $text.StartsWith($Last)) { $to = $i }
    }
    if ($from -lt 0 -or $to -lt $from) { throw "statement range not found: $First .. $Last" }
    return (@($Statements[$from..$to] | ForEach-Object { $_.Extent.Text }) -join "`n")
}
$constants = @($top | Where-Object {
    $_ -is [Management.Automation.Language.AssignmentStatementAst] -and
    @('$WrapperName', '$RootRunIdPrefix', '$RootWitnessPrologue', '$RootExitShim') -ccontains $_.Left.Extent.Text
} | ForEach-Object { $_.Extent.Text }) -join "`n"
$pinText = Get-StatementRange $top '$bundlePrefix = ' 'if ((Read-WdShadowFileSnapshot -Path $wrapperPath).Hash'
$commandText = Get-StatementRange $top '$rootTokenPath = ' '$arguments = '
function Invoke-WdFakeEntry($Entry) {
    $global:WdFake = $Entry
    . ([scriptblock]::Create($constants))
    $bundle = $Entry['bundle']
    $manifest = [pscustomobject]@{ files = [pscustomobject]@{} }
    if ($Entry['wrapper_pin']) {
        Add-Member -InputObject $manifest.files -MemberType NoteProperty -Name $WrapperName -Value $Entry['wrapper_pin']
    }
    foreach ($name in @('WD_BRIDGE_CODE_ROOT', 'WD_BRIDGE_BIN', 'WD_BRIDGE_PYTHON_WRAPPER')) {
        [Environment]::SetEnvironmentVariable($name, [string]$Entry[$name], 'Process')
    }
    $row = [ordered]@{ name = $Entry['name']; outcome = 'accepted'; message = ''; arguments = @() }
    try {
        . ([scriptblock]::Create($pinText))
    } catch {
        $row.outcome = 'refused'
        $row.message = $_.Exception.Message
        return $row
    }
    if ($Entry['kind'] -ceq 'command') {
        $runDir = $Entry['run_dir']
        $launchId = '11111111-2222-4333-8444-555555555555'
        $ExpectedSourceCommit = 'a' * 40
        $ExpectedManifestSha256 = 'B' * 64
        $liveFull = $Entry['live_dir']
        . ([scriptblock]::Create($commandText))
        $row.arguments = @($arguments)
    }
    return $row
}
$rows = @()
foreach ($item in (ConvertFrom-Json -InputObject ([IO.File]::ReadAllText($Fixture)))) {
    $entry = @{}
    foreach ($property in $item.PSObject.Properties) { $entry[$property.Name] = $property.Value }
    $rows += Invoke-WdFakeEntry $entry
}
ConvertTo-Json -InputObject @($rows) -Compress -Depth 4
"""

# The deployed wrapper's parameter shape (Invoke-WdBridgePython.ps1: Tool at position 0, a VerifyPackage switch and
# the remaining tool arguments); this FAKE records the binding, models the package verification and stands in for
# the producer with a marker file.
FAKE_WRAPPER = r"""[CmdletBinding(PositionalBinding = $false)]
param(
    [Parameter(Mandatory, Position = 0)] [string] $Tool,
    [switch] $VerifyPackage,
    [Parameter(ValueFromRemainingArguments)] [string[]] $ToolArguments = @()
)
$record = [ordered]@{ tool = $Tool; verify = [bool]$VerifyPackage; arguments = @($ToolArguments) }
[IO.File]::AppendAllText((Join-Path $PSScriptRoot 'calls.jsonl'), ($record | ConvertTo-Json -Compress) + "`n")
if ($VerifyPackage -and $env:WD_FAKE_VERIFY_FAILS -ceq '1') { $global:LASTEXITCODE = 41; return }
[IO.File]::WriteAllText((Join-Path $PSScriptRoot 'producer.ran'), $Tool)
$global:LASTEXITCODE = 0
"""


def _bundle(base, name):
    bundle = base / name
    for sub in ("tools-bootstrap/.agent-bridge/bin/sub", "tools-bootstrap/sub", "other", "run", "live"):
        (bundle / sub).mkdir(parents=True)
    (bundle / "Invoke-WdBridgePython.ps1").write_text(FAKE_WRAPPER, encoding="utf-8")
    return bundle


def _pins(bundle, code="tools-bootstrap", bin_="tools-bootstrap/.agent-bridge/bin", wrapper="Invoke-WdBridgePython.ps1"):
    def full(rel):
        return "" if rel is None else (rel if os.path.isabs(rel) else str(bundle / rel))
    return {"WD_BRIDGE_CODE_ROOT": full(code), "WD_BRIDGE_BIN": full(bin_), "WD_BRIDGE_PYTHON_WRAPPER": full(wrapper)}


def _fixtures(base):
    bundle = _bundle(base, "bundle")
    other = base / "elsewhere"
    other.mkdir()

    def pin(name, expected, reparse=(), wrapper_pin=WRAPPER_HASH, wrapper_hash=WRAPPER_HASH, **pins):
        item = {"name": name, "kind": "pins", "bundle": str(bundle), "wrapper_pin": wrapper_pin,
                "wrapper_hash": wrapper_hash, "reparse": [str(bundle / rel) for rel in reparse]}
        item.update(_pins(bundle, **pins))
        return item, expected

    items = [
        pin("code_root_descendant_refused", "refused", code="tools-bootstrap/sub"),
        pin("code_root_other_bundle_dir_refused", "refused", code="other"),
        pin("bin_descendant_refused", "refused", bin_="tools-bootstrap/.agent-bridge/bin/sub"),
        pin("bin_other_bundle_dir_refused", "refused", bin_="tools-bootstrap/sub"),
        pin("code_root_reparse_ancestry_refused", "refused", reparse=("tools-bootstrap",)),
        pin("bin_reparse_ancestry_refused", "refused", reparse=("tools-bootstrap/.agent-bridge",)),
        pin("exact_pins_accepted", "accepted"),
        pin("exact_pins_other_case_accepted", "accepted", code="TOOLS-BOOTSTRAP", bin_="Tools-Bootstrap/.AGENT-BRIDGE/BIN"),
        pin("missing_code_root_refused", "refused", code=None),
        pin("code_root_outside_the_bundle_refused", "refused", code=str(other)),
        pin("wrapper_not_the_bundle_root_file_refused", "refused", wrapper="tools-bootstrap/Invoke-WdBridgePython.ps1"),
        pin("wrapper_reparse_ancestry_refused", "refused", reparse=("Invoke-WdBridgePython.ps1",)),
        pin("missing_manifest_wrapper_pin_refused", "refused", wrapper_pin=None),
        pin("wrapper_bytes_not_the_manifest_pin_refused", "refused", wrapper_hash="CD" * 32),
    ]
    for name, fails in (("verification_fails_producer_not_run", "1"), ("verification_passes_producer_runs", "0")):
        own = _bundle(base, "bundle_" + name)
        item = {"name": name, "kind": "command", "bundle": str(own), "wrapper_pin": WRAPPER_HASH,
                "wrapper_hash": WRAPPER_HASH, "reparse": [], "run_dir": str(own / "run"),
                "live_dir": str(own / "live"), "verify_fails": fails}
        item.update(_pins(own))
        items.append((item, "accepted"))
    return items


def _hosts():
    hosts = []
    pwsh = shutil.which("pwsh")
    if pwsh:
        hosts.append(("ps7", pwsh))
    if os.name == "nt":
        system = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"
        ps5 = system / "WindowsPowerShell" / "v1.0" / "powershell.exe"
        if ps5.is_file():
            hosts.append(("ps5", str(ps5)))
    return hosts


def _run_root_command(executable, item, arguments):
    # The generated command runs once in a child of the same host, only against the FAKE wrapper of its own bundle.
    joined = "\n".join(arguments)
    assert item["bundle"] in joined
    for live in (os.environ.get("WD_BRIDGE_PYTHON_WRAPPER"), os.environ.get("WD_BRIDGE_CODE_ROOT"), "wd-reboot-bundles"):
        assert not live or live not in joined, "the generated command names a live path"
    env = {key: value for key, value in os.environ.items() if key != "WD_MIND_SHADOW_RUN_ID"}
    env["WD_FAKE_VERIFY_FAILS"] = item["verify_fails"]
    done = subprocess.run([executable, *arguments], capture_output=True, text=True, timeout=120, env=env,
                          cwd=item["run_dir"])
    bundle = Path(item["bundle"])
    calls = bundle / "calls.jsonl"
    rows = [json.loads(line) for line in calls.read_text(encoding="utf-8").splitlines()] if calls.exists() else []
    return {"exit": done.returncode, "calls": rows, "producer_ran": (bundle / "producer.ran").exists()}


@pytest.fixture(scope="module", params=_hosts() or [pytest.param(None, marks=pytest.mark.skip(
    reason="no PowerShell host on this runner: the synthetic pin and command run needs pwsh or Windows PowerShell"))],
    ids=lambda host: host[0] if host else "no-host")
def runs(request, tmp_path_factory):
    _, executable = request.param
    folder = tmp_path_factory.mktemp("bundle")
    items = _fixtures(folder)
    host = folder / "host.ps1"
    host.write_text(HOST, encoding="utf-8")
    fixture = folder / "fixture.json"
    fixture.write_text(json.dumps([item for item, _ in items]), encoding="utf-8")
    done = subprocess.run([executable, "-NoProfile", "-NonInteractive", "-File", str(host), str(ADAPTER),
                           str(fixture)], capture_output=True, text=True, timeout=180)
    assert done.returncode == 0, done.stderr[-2000:]
    rows = {row["name"]: row for row in json.loads(done.stdout.strip().splitlines()[-1])}
    for item, _ in items:
        if item["kind"] == "command" and rows[item["name"]]["outcome"] == "accepted":
            rows[item["name"]]["child"] = _run_root_command(executable, item, rows[item["name"]]["arguments"])
    return items, rows


def _fixtures_names():
    return [({"name": name}, None) for name in (
        "code_root_descendant_refused", "code_root_other_bundle_dir_refused", "bin_descendant_refused",
        "bin_other_bundle_dir_refused", "code_root_reparse_ancestry_refused", "bin_reparse_ancestry_refused",
        "exact_pins_accepted", "exact_pins_other_case_accepted", "missing_code_root_refused",
        "code_root_outside_the_bundle_refused", "wrapper_not_the_bundle_root_file_refused",
        "wrapper_reparse_ancestry_refused", "missing_manifest_wrapper_pin_refused",
        "wrapper_bytes_not_the_manifest_pin_refused")]


@pytest.mark.parametrize("name", [item["name"] for item, _ in _fixtures_names()])
def test_bundle_pins_accept_only_the_exact_code_root_bin_and_wrapper(runs, name):
    items, rows = runs
    expected = dict((item["name"], outcome) for item, outcome in items)[name]
    assert rows[name]["outcome"] == expected, rows[name]["message"]


def test_root_command_binds_verify_package_before_the_fake_producer_can_run(runs):
    items, rows = runs
    for item, _ in items:
        if item["kind"] != "command":
            continue
        row = rows[item["name"]]
        assert row["outcome"] == "accepted", row["message"]
        child = row["child"]
        assert len(child["calls"]) == 1, child
        call = child["calls"][0]
        assert call["tool"] == "sandbox/mind/mind_capture_producer.py"
        assert call["verify"] is True, call
        assert "-VerifyPackage" not in call["arguments"]
        assert call["arguments"] == ["--src", item["WD_BRIDGE_CODE_ROOT"], "--bin", item["WD_BRIDGE_BIN"],
                                     "--live-dir", item["live_dir"]]
        if item["verify_fails"] == "1":
            assert child["producer_ran"] is False and child["exit"] == 41, child
        else:
            assert child["producer_ran"] is True and child["exit"] == 0, child


def test_every_fixture_has_exactly_one_row(runs):
    items, rows = runs
    assert sorted(rows) == sorted(item["name"] for item, _ in items)
    assert sorted(item["name"] for item, _ in _fixtures_names()) == sorted(
        item["name"] for item, _ in items if item["kind"] == "pins")

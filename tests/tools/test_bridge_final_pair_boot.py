"""Bridge final-pair boot binding (Lead 684ceba6 / cfed740c; AUTHORED, NOT RUN).

The generated launcher refuses unless the installer's state pointer next to it records exactly its pinned final
pair and bundle, and it never tries an older bundle. Only New-ForwardingWrapper is extracted (PowerShell AST) and
run in an isolated pwsh child against a fake bundle under tmp_path: no deploy, install, StageOnly, scheduled task,
model, provider, credential or real C: file. Requires the proposed 684ceba6 deployer patch (92cb768a).
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]
DEPLOYER = ROOT / "ops" / "windows" / "reboot" / "Deploy-WdRebootBundle.ps1"
COMMIT = "a" * 40
PWSH = shutil.which("pwsh")
REFUSAL = "does not match this launcher's pinned final package"

GENERATE = r"""
param([string] $Deployer, [string] $Target, [string] $EntryHash, [string] $ManifestHash, [string] $Commit,
    [string] $Out)
$ErrorActionPreference = 'Stop'
$tokens = $null
$errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($Deployer, [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw ('deployer parse errors: ' + $errors.Count) }
$function = $ast.Find({ param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
    $node.Name -eq 'New-ForwardingWrapper' }, $true)
if ($null -eq $function) { throw 'New-ForwardingWrapper is missing' }
. ([scriptblock]::Create($function.Extent.Text))
$text = New-ForwardingWrapper -Target $Target -ExpectedHash $EntryHash -ExpectedManifestHash $ManifestHash `
    -WrapperKind supervisor -ExpectedFinalCommit $Commit -ExpectedFinalManifestHash $ManifestHash
[IO.File]::WriteAllText($Out, $text, (New-Object Text.UTF8Encoding $false))
"""

RUN = r"""
param([string] $Launcher, [string] $ErrorFile)
try { & $Launcher; exit 0 }
catch { [IO.File]::WriteAllText($ErrorFile, $_.Exception.Message); exit 1 }
"""

TARGET = "Set-Content -LiteralPath $env:WD_FINAL_PAIR_MARKER -Value ran -Encoding ascii\n"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()  # Get-FileHash form


def _pwsh(tmp_path: Path, script: str, *args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    path = tmp_path / f"harness-{abs(hash(script))}.ps1"
    path.write_text(script, encoding="utf-8")
    return subprocess.run([PWSH, "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(path), *args],
                          capture_output=True, timeout=120, env=env)  # bytes: never a decode-thread failure


@pytest.fixture
def launcher(tmp_path):
    if PWSH is None:
        pytest.skip("pwsh is not installed")
    bundle, machine = tmp_path / "bundles" / COMMIT, tmp_path / "machine"
    bundle.mkdir(parents=True)
    machine.mkdir()
    target, manifest = bundle / "wd_supervisor.ps1", bundle / "deployment-manifest.json"
    target.write_text(TARGET, encoding="ascii")
    manifest.write_text('{"schema_version": 1}\n', encoding="ascii")
    wrapper = machine / "wd_supervisor.ps1"
    done = _pwsh(tmp_path, GENERATE, "-Deployer", str(DEPLOYER), "-Target", str(target), "-EntryHash", _sha(target),
                 "-ManifestHash", _sha(manifest), "-Commit", COMMIT, "-Out", str(wrapper))
    assert done.returncode == 0, done.stderr.decode("utf-8", "replace")
    return {"tmp": tmp_path, "bundle": bundle, "machine": machine, "wrapper": wrapper,
            "manifest_hash": _sha(manifest), "marker": tmp_path / "marker.txt", "error": tmp_path / "error.txt"}


def _pointer(launcher, **over) -> str:
    value = {"source_commit": COMMIT, "final_commit": COMMIT, "manifest_sha256": launcher["manifest_hash"],
             "final_manifest_sha256": launcher["manifest_hash"], "active_bundle": str(launcher["bundle"])}
    value.update(over)
    return json.dumps(value)


def _run(launcher, pointer: str | None) -> subprocess.CompletedProcess:
    if pointer is not None:
        (launcher["machine"] / "WD_REBOOT_STATE_CURRENT.json").write_text(pointer, encoding="utf-8")
    env = dict(os.environ, WD_FINAL_PAIR_MARKER=str(launcher["marker"]))
    return _pwsh(launcher["tmp"], RUN, "-Launcher", str(launcher["wrapper"]), "-ErrorFile", str(launcher["error"]),
                 env=env)


def test_a_matching_pointer_invokes_the_pinned_target(launcher):
    done = _run(launcher, _pointer(launcher))
    assert done.returncode == 0, done.stderr.decode("utf-8", "replace")
    assert launcher["marker"].read_text(encoding="ascii").strip() == "ran"
    assert not launcher["error"].exists()


@pytest.mark.parametrize("case", ["source_commit", "final_commit", "manifest_sha256", "final_manifest_sha256",
                                  "active_bundle", "missing", "corrupt"])
def test_any_pointer_mismatch_refuses_loudly_and_runs_nothing(launcher, case):
    if case == "missing":
        pointer = None
    elif case == "corrupt":
        pointer = "{not json"
    elif case == "active_bundle":
        pointer = _pointer(launcher, active_bundle=str(launcher["bundle"].parent / ("c" * 40)))
    else:
        pointer = _pointer(launcher, **{case: "b" * 40 if case.endswith("commit") else "0" * 64})
    done = _run(launcher, pointer)
    assert done.returncode == 1
    assert REFUSAL in launcher["error"].read_text(encoding="utf-8")  # the exact message, never a console wrap
    assert not launcher["marker"].exists()  # the target never ran, and no other bundle was tried


def test_the_final_pair_gate_runs_before_the_migration_and_every_machine_write():
    text = DEPLOYER.read_text(encoding="utf-8")
    stage_only = text.index("STAGE ONLY: commit-addressed bundle verified")
    gate = text.index("$ExpectedFinalCommit -cnotmatch '^[0-9a-f]{40}$'")
    migration = text.index("Initialize-WdGrokRecovery.ps1')")
    transaction = text.index("$machineMutationStarted = $false")
    first_wrapper = text.index("Write-Utf8NoBomAtomic -Path $machinePath -Content $wrapper")
    pointer = text.index("final_commit = $ExpectedFinalCommit")
    assert stage_only < gate < migration < transaction < first_wrapper < pointer


# --- the actual-install gate itself (Lead ed23900a): the deployer's exact gate, extracted and run on preset values

GATE = r"""
param([string] $Deployer, [string] $Commit, [string] $Manifest, [string] $Head, [string] $Installed, [string] $Out)
$ErrorActionPreference = 'Stop'
$tokens = $null
$errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($Deployer, [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw ('deployer parse errors: ' + $errors.Count) }
$gate = @($ast.FindAll({ param($node) $node -is [System.Management.Automation.Language.IfStatementAst] -and
    $node.Clauses[0].Item1.Extent.Text.Contains('$ExpectedFinalCommit -cnotmatch') }, $true))
if ($gate.Count -ne 1) { throw ('final-pair gate count: ' + $gate.Count) }
if ($Commit -ceq 'ABSENT') { $Commit = '' }
if ($Manifest -ceq 'ABSENT') { $Manifest = '' }
$ExpectedFinalCommit, $ExpectedFinalManifestHash = $Commit, $Manifest
$head, $installedManifestHash = $Head, $Installed
try { . ([scriptblock]::Create($gate[0].Extent.Text)); $result = 'admitted' } catch { $result = $_.Exception.Message }
[IO.File]::WriteAllText($Out, $result, (New-Object Text.UTF8Encoding $false))
"""

ORDER = r"""
param([string] $Deployer, [string] $Out)
$ErrorActionPreference = 'Stop'
$tokens = $null
$errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($Deployer, [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw ('deployer parse errors: ' + $errors.Count) }
function Get-Block($node) {
    $parent = $node.Parent
    while ($null -ne $parent -and $parent -isnot [System.Management.Automation.Language.StatementBlockAst] -and
        $parent -isnot [System.Management.Automation.Language.NamedBlockAst]) { $parent = $parent.Parent }
    return $parent
}
$gate = @($ast.FindAll({ param($node) $node -is [System.Management.Automation.Language.IfStatementAst] -and
    $node.Clauses[0].Item1.Extent.Text.Contains('$ExpectedFinalCommit -cnotmatch') }, $true))
$commands = @($ast.FindAll({ param($node) $node -is [System.Management.Automation.Language.CommandAst] }, $true))
$invoked = @($commands | Where-Object { $_.InvocationOperator -eq 'Ampersand' })  # the outer & (Join-Path ...) calls
$migration = @($invoked | Where-Object { $_.Extent.Text.Contains('Initialize-WdGrokRecovery.ps1') })
$taskApply = @($invoked | Where-Object { $_.Extent.Text.Contains('Register-WdScheduledTasks.ps1') -and
    $_.Extent.Text.Contains('-Apply') })
$writers = @('Write-Utf8NoBomAtomic', 'Copy-Item', 'New-Item', 'Move-Item', ('Remove' + '-Item'))
$machineWrites = @($commands | Where-Object { $writers -contains $_.GetCommandName() -and
    $_.Extent.Text -match '[$](machinePath|machineFull|backupRoot)' })
$start = $gate[0].Extent.StartOffset
$facts = [ordered]@{
    gate = $gate.Count
    migration = $migration.Count
    same_block = [object]::ReferenceEquals((Get-Block $gate[0]), (Get-Block $migration[0]))
    migration_after_gate = $migration[0].Extent.StartOffset -gt $start
    machine_writes = $machineWrites.Count
    writes_after_gate = @($machineWrites | Where-Object { $_.Extent.StartOffset -gt $start }).Count
    task_apply = $taskApply.Count
    task_apply_after_gate = @($taskApply | Where-Object { $_.Extent.StartOffset -gt $start }).Count
}
[IO.File]::WriteAllText($Out, ($facts | ConvertTo-Json -Compress), (New-Object Text.UTF8Encoding $false))
"""


def _gate(tmp_path: Path, commit: str, manifest: str) -> str:
    out = tmp_path.joinpath("gate.txt")
    done = _pwsh(tmp_path, GATE, "-Deployer", str(DEPLOYER), "-Commit", commit, "-Manifest", manifest,
                 "-Head", COMMIT, "-Installed", "A" * 64, "-Out", str(out))
    assert done.returncode == 0, done.stderr.decode("utf-8", "replace")
    return out.read_text(encoding="utf-8")


@pytest.mark.parametrize("commit, manifest", [
    ("ABSENT", "ABSENT"), ("ABSENT", "A" * 64), (COMMIT, "ABSENT"), ("b" * 40, "A" * 64), (COMMIT, "B" * 64),
    (COMMIT.upper(), "A" * 64), (COMMIT, "a" * 64), (COMMIT[:39], "A" * 64),
], ids=["both_absent", "commit_absent", "manifest_absent", "wrong_commit", "wrong_manifest", "uppercase_commit",
        "lowercase_manifest", "short_commit"])
def test_the_actual_install_gate_refuses_an_absent_or_wrong_pair(tmp_path, commit, manifest):
    if PWSH is None:
        pytest.skip("pwsh is not installed")
    result = _gate(tmp_path, commit, manifest)
    assert result.startswith("refusing to activate: the operator-pinned final pair"), result


def test_the_actual_install_gate_admits_only_the_exact_pair(tmp_path):
    if PWSH is None:
        pytest.skip("pwsh is not installed")
    assert _gate(tmp_path, COMMIT, "A" * 64) == "admitted"  # the twin: exactly the install's head and manifest


def test_the_gate_precedes_the_migration_and_every_machine_write_in_one_block(tmp_path):
    if PWSH is None:
        pytest.skip("pwsh is not installed")
    out = tmp_path.joinpath("order.json")
    done = _pwsh(tmp_path, ORDER, "-Deployer", str(DEPLOYER), "-Out", str(out))
    assert done.returncode == 0, done.stderr.decode("utf-8", "replace")
    facts = json.loads(out.read_text(encoding="utf-8"))
    assert (facts["gate"], facts["migration"], facts["same_block"], facts["migration_after_gate"]) == (1, 1, True, True)
    assert facts["machine_writes"] >= 5 and facts["writes_after_gate"] == facts["machine_writes"]
    assert facts["task_apply"] == 1 and facts["task_apply_after_gate"] == 1

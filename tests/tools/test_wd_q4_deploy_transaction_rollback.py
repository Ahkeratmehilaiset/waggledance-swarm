"""Run the deployer's real exception rollback against synthetic C: fixtures only.

No installer, provider, scheduler, live bundle, lane or bridge is started. The
transaction is extracted verbatim from the deployer AST; only wrapper creation
and an injectable post-write fault are supplied by this test. This verifies
exception recovery, not power loss, process termination or atomic deployment.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "ops/windows/reboot/Deploy-WdRebootBundle.ps1"
SHELLS = [value for value in (shutil.which("powershell.exe"), shutil.which("pwsh.exe")) if value]
pytestmark = pytest.mark.skipif(os.name != "nt" or not SHELLS, reason="Windows PowerShell required")
POINTER = "WD_Q4_DESCRIPTOR_CURRENT.json"
STATE = "WD_REBOOT_STATE_CURRENT.json"
INTEGRITY = "WD_REBOOT_INTEGRITY_CURRENT.sha256"
WRAPPER = "start-wd-all.ps1"
DATA = "wd_supervisor_loop.json"
Q4_CONTENT = b'{"schema":"synthetic-q4"}\n'


def sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def psquote(value: object) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def top_files(machine: Path) -> dict[str, bytes]:
    return {path.name: path.read_bytes() for path in machine.iterdir() if path.is_file()}


def make_fixture(tmp_path: Path, *, pointer_exists: bool, q4_enabled: bool):
    # Pytest must be invoked with a persistent C: .codex-audit basetemp.
    raw_root = tmp_path.absolute()
    assert raw_root.drive.upper() == "C:" and ".codex-audit" in raw_root.parts
    for part in (raw_root, *raw_root.parents):
        info = os.lstat(part)
        assert not (getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT)
    root = raw_root.resolve(strict=True)
    assert os.path.normcase(str(root)) == os.path.normcase(str(raw_root))
    machine, target = root / "machine", root / "target"
    machine.mkdir()
    target.mkdir()
    initial = [WRAPPER, "start-wd-agent.ps1", DATA, STATE, "WD_REBOOT_STATE_CURRENT.md",
               INTEGRITY, "WD_REBOOT_INTEGRITY_CURRENT.json", "WD_GROK_MODEL_CURRENT.json",
               "WD_GROK_MODEL_CURRENT.md"]
    for index, name in enumerate(initial):
        # Distinct, non-text contents catch accidental normalization or replacement.
        (machine / name).write_bytes((f"original-{index}\r\n".encode() + bytes([index, 255])))
    if pointer_exists:
        (machine / POINTER).write_bytes(b"original-external-pointer\x00\xff")
    (target / DATA).write_bytes(b"synthetic-new-data\n")
    manifest = target / "deployment-manifest.json"
    manifest.write_bytes(b'{"schema_version":1,"synthetic":true}\n')
    sentinel = root / "outside-machine-sentinel.bin"
    sentinel.write_bytes(b"sentinel-outside-transaction\x00\xff")
    return {"root": root, "machine": machine, "target": target, "manifest": manifest,
            "sentinel": sentinel, "before": top_files(machine), "q4_enabled": q4_enabled}


def invoke_transaction(fixture, *, shell: str, fail_leaf: str, fail_restore: bool = False, mutant: bool = False):
    """Extract the source's backup and try/catch range, invoke under strict path guards."""
    work = fixture
    script = rf'''
$ErrorActionPreference = 'Stop'
if ($PSVersionTable.PSEdition -eq 'Desktop') {{ $env:PSModulePath=Join-Path $PSHOME 'Modules' }}
Import-Module Microsoft.PowerShell.Utility
Import-Module Microsoft.PowerShell.Management
$tokens=$null; $errors=$null
$deployer={psquote(DEPLOY)}
$ast=[Management.Automation.Language.Parser]::ParseFile($deployer,[ref]$tokens,[ref]$errors)
if ($errors.Count) {{ throw 'deployer parse failed' }}
foreach ($name in @('Resolve-FullPath','Assert-ChildPath','Write-Utf8NoBomAtomic','Get-WdQ4PointerIntegrityLine')) {{
    $found=@($ast.FindAll({{param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -ceq $name}},$true))
    if ($found.Count -ne 1) {{ throw 'expected function not unique' }}
    . ([scriptblock]::Create($found[0].Extent.Text))
}}
$actualAtomic=${{function:Write-Utf8NoBomAtomic}}
$source=[IO.File]::ReadAllText($deployer)
$start=$source.IndexOf('$backupRoot = Join-Path $machineFull (',[StringComparison]::Ordinal)
$transactions=@($ast.FindAll({{param($n) $n -is [Management.Automation.Language.TryStatementAst] -and
    $n.Extent.Text -match '^try\s*\{{\s*\$machineMutationStarted\s*=\s*\$true' -and
    $n.Extent.Text.Contains('rollback also failed')}},$true))
if ($start -lt 0 -or $transactions.Count -ne 1 -or $transactions[0].Extent.EndOffset -le $start) {{
    throw 'expected original transaction not uniquely found'
}}
$chunk=$source.Substring($start,$transactions[0].Extent.EndOffset-$start)
if ({'$true' if mutant else '$false'}) {{
    # Test-only mutant: omit the Q4 pointer from the source transaction list.
    $needle='$(if ($Q4DefaultPointerPath) {{ ''WD_Q4_DESCRIPTOR_CURRENT.json'' }})'
    if (-not $chunk.Contains($needle)) {{ throw 'mutant marker missing' }}
    $chunk=$chunk.Replace($needle,'')
}}
$sandbox=[IO.Path]::GetFullPath({psquote(work['root'])}).TrimEnd('\') + '\'
$machineFull=[IO.Path]::GetFullPath({psquote(work['machine'])})
$targetRoot=[IO.Path]::GetFullPath({psquote(work['target'])})
foreach ($p in @($machineFull,$targetRoot)) {{
    if (-not $p.StartsWith($sandbox,[StringComparison]::OrdinalIgnoreCase)) {{ throw 'unsafe synthetic fixture' }}
}}
function Assert-SyntheticPath([string]$Path) {{
    $full=[IO.Path]::GetFullPath($Path)
    if (-not $full.StartsWith($sandbox,[StringComparison]::OrdinalIgnoreCase)) {{ throw 'outside synthetic fixture' }}
}}
function Copy-Item {{
    [CmdletBinding()] param([string]$LiteralPath,[string]$Destination,[switch]$Force)
    Assert-SyntheticPath $LiteralPath; Assert-SyntheticPath $Destination
    if ({'$true' if fail_restore else '$false'} -and $LiteralPath.StartsWith($backupRoot,[StringComparison]::OrdinalIgnoreCase) -and
        [IO.Path]::GetFileName($Destination) -ceq '{WRAPPER}') {{ throw 'synthetic restore failure' }}
    Microsoft.PowerShell.Management\Copy-Item -LiteralPath $LiteralPath -Destination $Destination -Force:$Force
}}
function Remove-Item {{
    [CmdletBinding()] param([string]$LiteralPath,[switch]$Force)
    Assert-SyntheticPath $LiteralPath
    Microsoft.PowerShell.Management\Remove-Item -LiteralPath $LiteralPath -Force:$Force
}}
function Move-Item {{
    [CmdletBinding()] param([string]$LiteralPath,[string]$Destination,[switch]$Force)
    Assert-SyntheticPath $LiteralPath; Assert-SyntheticPath $Destination
    Microsoft.PowerShell.Management\Move-Item -LiteralPath $LiteralPath -Destination $Destination -Force:$Force
}}
function Write-Utf8NoBomAtomic {{
    param([string]$Path,[string]$Content)
    Assert-SyntheticPath $Path
    & $actualAtomic -Path $Path -Content $Content
    if ([IO.Path]::GetFileName($Path) -ceq {psquote(fail_leaf)}) {{ throw 'synthetic post-write failure' }}
}}
function New-ForwardingWrapper {{ param($Target,$ExpectedHash,$ExpectedManifestHash,$WrapperKind,$FixedAgent,
    $ExpectedFinalCommit,$ExpectedFinalManifestHash,$ExpectedQ4PointerSha256) return '# synthetic new wrapper' }}
$wrapperSpecs=@(
 [pscustomobject]@{{Name='{WRAPPER}';Target='{WRAPPER}';Kind='fleet';Agent=''}},
 [pscustomobject]@{{Name='start-wd-agent.ps1';Target='start-wd-agent.ps1';Kind='agent';Agent=''}})
$dataSpecs=@([pscustomobject]@{{Name='{DATA}';Target='{DATA}'}})
$installedManifest=[pscustomobject]@{{files=[pscustomobject]@{{'{WRAPPER}'=('a'*64);'start-wd-agent.ps1'=('b'*64)}}}}
$installedManifestPath={psquote(work['manifest'])}
$installedManifestHash=('C'*64);$ExpectedFinalManifestHash=('C'*64)
$ExpectedFinalCommit=('d'*40);$head=('d'*40);$branch='synthetic-only'
$Q4DefaultPointerPath={'synthetic' if work['q4_enabled'] else ''!r}
$Q4DefaultPointerSha256='{sha(Q4_CONTENT)}';$q4PointerText={{Q4_TEXT}}
$SkipGrokResolve=$true;$SkipTaskRegistration=$true
$caught=''; try {{ . ([scriptblock]::Create($chunk)) }} catch {{ $caught=$_.Exception.Message }}
if (-not $caught) {{ throw 'expected injected transaction failure did not occur' }}
@{{error=$caught;transaction_bytes=$transactions[0].Extent.Text.Length;
   pointer_in_transaction=($transactionNames -ccontains '{POINTER}')}}|ConvertTo-Json -Compress
'''
    # Literal synthetic JSON only; it is not user input or a command argument.
    q4_text = Q4_CONTENT.decode("ascii").replace("'", "''").replace("\n", "`n")
    script = script.replace("{Q4_TEXT}", '"' + q4_text.replace('"', '`"') + '"')
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    result = subprocess.run([shell, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                            cwd=ROOT, capture_output=True, text=True, timeout=30, check=False)
    assert result.returncode == 0, (result.stdout, result.stderr)
    output = [line for line in result.stdout.splitlines() if line.strip()]
    assert output, result.stderr
    return json.loads(output[-1])


@pytest.mark.parametrize("fault", [WRAPPER, DATA, POINTER, STATE, INTEGRITY])
@pytest.mark.parametrize("pointer_exists", [False, True])
@pytest.mark.parametrize("shell", SHELLS, ids=lambda path: Path(path).name)
def test_real_transaction_restores_original_bytes_after_each_fault(tmp_path, fault, pointer_exists, shell):
    fixture = make_fixture(tmp_path, pointer_exists=pointer_exists, q4_enabled=True)
    output = invoke_transaction(fixture, shell=shell, fail_leaf=fault)
    assert output["error"] == "synthetic post-write failure"
    assert output["transaction_bytes"] > 1000 and output["pointer_in_transaction"] is True
    assert top_files(fixture["machine"]) == fixture["before"]
    assert fixture["sentinel"].read_bytes() == b"sentinel-outside-transaction\x00\xff"


@pytest.mark.parametrize("shell", SHELLS, ids=lambda path: Path(path).name)
def test_pointer_is_untouched_in_no_q4_baseline(tmp_path, shell):
    fixture = make_fixture(tmp_path, pointer_exists=True, q4_enabled=False)
    output = invoke_transaction(fixture, shell=shell, fail_leaf=STATE)
    assert output["pointer_in_transaction"] is False
    assert top_files(fixture["machine"]) == fixture["before"]


@pytest.mark.parametrize("shell", SHELLS, ids=lambda path: Path(path).name)
def test_failed_restore_is_reported_and_other_files_are_still_restored(tmp_path, shell):
    fixture = make_fixture(tmp_path, pointer_exists=True, q4_enabled=True)
    output = invoke_transaction(fixture, shell=shell, fail_leaf=STATE, fail_restore=True)
    assert "rollback also failed" in output["error"] and "synthetic restore failure" in output["error"]
    after = top_files(fixture["machine"])
    assert after[WRAPPER] != fixture["before"][WRAPPER]
    assert {k: v for k, v in after.items() if k != WRAPPER} == {
        k: v for k, v in fixture["before"].items() if k != WRAPPER}


@pytest.mark.parametrize("shell", SHELLS, ids=lambda path: Path(path).name)
def test_test_only_mutant_without_pointer_backup_is_detected(tmp_path, shell):
    fixture = make_fixture(tmp_path, pointer_exists=True, q4_enabled=True)
    output = invoke_transaction(fixture, shell=shell, fail_leaf=STATE, mutant=True)
    assert output["pointer_in_transaction"] is False
    assert top_files(fixture["machine"]) != fixture["before"]
    assert (fixture["machine"] / POINTER).read_bytes() == Q4_CONTENT

"""Synthetic backup-inventory checks. Never invoke the installer or live paths."""
import builtins
import hashlib
import json
import os
from pathlib import Path
import subprocess
import types

import pytest


ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "ops/windows/reboot/Deploy-WdRebootBundle.ps1"
SHELL = (Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
         if os.name == "nt" else None)


def test_module_import_on_posix_without_systemroot():
    """Exercise the complete module import with a synthetic POSIX os module."""
    builtin = vars(builtins).copy()
    original = builtin["__import__"]

    def portable_import(name, *args, **kwargs):
        if name == "os":
            return types.SimpleNamespace(name="posix", environ={})
        return original(name, *args, **kwargs)

    builtin["__import__"] = portable_import
    source = Path(__file__).read_text(encoding="utf-8")
    namespace = {"__builtins__": builtin, "__name__": "synthetic_posix_import", "__file__": __file__}
    exec(compile(source, __file__, "exec"), namespace)
    assert namespace["SHELL"] is None


def sha(data):
    return hashlib.sha256(data).hexdigest().upper()


@pytest.fixture
def scenario(tmp_path):
    machine = tmp_path / ".codex-audit" / "machine"
    backup = machine / "wd-reboot-backups" / "synthetic"
    backup.mkdir(parents=True)
    names = ["start-wd-all.ps1", "never-present.ps1", "WD_REBOOT_STATE_CURRENT.json"]
    return machine, backup, names


def invoke(machine, backup, names, present, new_manifest="d" * 64, previous_store=None):
    env = dict(os.environ, TEST_MACHINE=str(machine), TEST_BACKUP=str(backup),
               TEST_NAMES=json.dumps(names), TEST_PRESENT=json.dumps(present), TEST_SCRIPT=str(DEPLOY),
               TEST_NEW_MANIFEST=new_manifest,
               TEST_PREVIOUS_STORE=str(previous_store or machine / "wd-reboot-bundles"))
    script = r"""
$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\Modules\Microsoft.PowerShell.Utility\Microsoft.PowerShell.Utility.psd1') -ErrorAction Stop
$tokens=$null; $errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile($env:TEST_SCRIPT,[ref]$tokens,[ref]$errors)
if ($errors.Count) { throw 'parse failure' }
foreach ($name in @('Resolve-FullPath','Assert-WdPathWithoutReparse','Assert-ChildPath','Write-WdRebootBackupInventory')) {
  $fn=$ast.FindAll({param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -ceq $name},$true)
  if (@($fn).Count -ne 1) { throw "missing exact function $name" }
  . ([scriptblock]::Create($fn[0].Extent.Text))
}
$presence=@{}
$raw=$env:TEST_PRESENT | ConvertFrom-Json
foreach($p in $raw.PSObject.Properties) { $presence[$p.Name]=[bool]$p.Value }
[string[]]$names=(ConvertFrom-Json -InputObject $env:TEST_NAMES)
$params=@{MachineRoot=$env:TEST_MACHINE; BackupRoot=$env:TEST_BACKUP; TransactionNames=$names;
          OriginallyPresent=$presence; NewCommit=('c'*40); NewManifestSha256=$env:TEST_NEW_MANIFEST;
          PreviousBundleStore=$env:TEST_PREVIOUS_STORE}
Write-WdRebootBackupInventory @params | ConvertTo-Json -Depth 12 -Compress
"""
    return subprocess.run([str(SHELL), "-NoProfile", "-NonInteractive", "-Command", script],
                          cwd=ROOT, env=env, text=True, capture_output=True, timeout=30)


def seeded(scenario, previous_store=None):
    machine, backup, names = scenario
    commit = "a" * 40
    old_bundle = (previous_store or machine / "wd-reboot-bundles") / commit
    old_bundle.mkdir(parents=True)
    manifest = b'{"source_commit":"' + commit.encode() + b'"}'
    (old_bundle / "deployment-manifest.json").write_bytes(manifest)
    state = json.dumps({"schema_version": 1, "source_commit": commit, "active_bundle": str(old_bundle),
                        "manifest_sha256": sha(manifest)}, separators=(",", ":")).encode()
    for name, data in ((names[0], b"previous launcher"), (names[2], state)):
        (machine / name).write_bytes(data)
        (backup / name).write_bytes(data)
    return {names[0]: True, names[1]: False, names[2]: True}, sha(state), sha(manifest), commit


@pytest.mark.skipif(os.name != "nt", reason="requires Windows PowerShell")
def test_inventory_binds_present_absent_state_and_bundle(scenario):
    machine, backup, names = scenario
    presence, state_sha, manifest_sha, commit = seeded(scenario)
    result = invoke(machine, backup, names, presence)
    assert result.returncode == 0, result.stderr
    doc = json.loads((backup / "rollback-inventory.json").read_bytes())
    digest = (backup / "rollback-inventory.sha256").read_text().strip()
    assert digest == sha((backup / "rollback-inventory.json").read_bytes())
    entries = {row["name"]: row for row in doc["files"]}
    assert entries[names[0]]["present"] is True and entries[names[0]]["sha256"] == sha(b"previous launcher")
    assert entries[names[1]] == {"name": names[1], "present": False, "sha256": None}
    assert entries[names[2]]["sha256"] == state_sha
    assert doc["prior_state"]["state_sha256"] == state_sha
    assert doc["prior_state"]["source_commit"] == commit
    assert doc["prior_state"]["manifest_sha256"] == manifest_sha
    assert Path(doc["prior_state"]["bundle_store"]) == machine / "wd-reboot-bundles"
    assert doc["new_commit"] == "c" * 40
    assert doc["new_manifest_sha256"] == "D" * 64


@pytest.mark.skipif(os.name != "nt", reason="requires Windows PowerShell")
def test_prior_bundle_may_use_explicit_separate_store(scenario):
    machine, backup, names = scenario
    store = machine.parent / "separate-previous-store"
    presence, state_sha, manifest_sha, commit = seeded(scenario, store)
    result = invoke(machine, backup, names, presence, previous_store=store)
    assert result.returncode == 0, result.stderr
    doc = json.loads((backup / "rollback-inventory.json").read_bytes())
    assert doc["prior_state"]["state_sha256"] == state_sha
    assert Path(doc["prior_state"]["bundle_store"]) == store
    assert Path(doc["prior_state"]["active_bundle"]) == store / commit
    assert doc["prior_state"]["manifest_sha256"] == manifest_sha


@pytest.mark.skipif(os.name != "nt", reason="requires Windows PowerShell")
@pytest.mark.parametrize("mismatch", ["wrong_store", "escaping_path", "non_c_store"])
def test_prior_store_requires_explicit_matching_c_path(scenario, mismatch):
    machine, backup, names = scenario
    store = machine.parent / "separate-previous-store"
    presence, _, _, commit = seeded(scenario, store)
    approved = store
    if mismatch == "wrong_store":
        approved = machine.parent / "not-the-previous-store"
    elif mismatch == "non_c_store":
        approved = r"D:\unapproved-synthetic-store"
    else:
        state_path = machine / names[2]
        state = json.loads(state_path.read_bytes())
        state["active_bundle"] = str(store / ".." / "outside" / commit)
        changed = json.dumps(state, separators=(",", ":")).encode()
        state_path.write_bytes(changed)
        (backup / names[2]).write_bytes(changed)
    result = invoke(machine, backup, names, presence, previous_store=approved)
    assert result.returncode != 0
    expected_error = ("absolute canonical C-drive" if mismatch == "non_c_store" else
                      "prior bundle path invalid" if mismatch == "escaping_path" else
                      "prior bundle path mismatch")
    assert expected_error in result.stderr, result.stderr
    assert not (backup / "rollback-inventory.json").exists()


@pytest.mark.skipif(os.name != "nt", reason="requires Windows PowerShell")
def test_separate_store_reparse_is_rejected(scenario):
    machine, backup, names = scenario
    real_store = machine.parent / "real-store"
    alias_store = machine.parent / "alias-store"
    presence, _, _, commit = seeded(scenario, real_store)
    try:
        alias_store.symlink_to(real_store, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"synthetic symlink creation unavailable: {error.winerror}")
    state_path = machine / names[2]
    state = json.loads(state_path.read_bytes())
    state["active_bundle"] = str(alias_store / commit)
    changed = json.dumps(state, separators=(",", ":")).encode()
    state_path.write_bytes(changed)
    (backup / names[2]).write_bytes(changed)
    result = invoke(machine, backup, names, presence, previous_store=alias_store)
    assert result.returncode != 0
    assert "reparse point" in result.stderr, result.stderr
    assert not (backup / "rollback-inventory.json").exists()


@pytest.mark.skipif(os.name != "nt", reason="requires Windows PowerShell")
def test_first_install_records_absence_without_inventing_prior_state(scenario):
    machine, backup, names = scenario
    result = invoke(machine, backup, names, {n: False for n in names})
    assert result.returncode == 0, result.stderr
    doc = json.loads((backup / "rollback-inventory.json").read_bytes())
    assert doc["prior_state"] is None
    assert all(row["present"] is False and row["sha256"] is None for row in doc["files"])


@pytest.mark.skipif(os.name != "nt", reason="requires Windows PowerShell")
@pytest.mark.parametrize("damage", ["copy", "source", "manifest", "missing-now-present"])
def test_inconsistent_backup_refuses_before_inventory(scenario, damage):
    machine, backup, names = scenario
    presence, _, _, commit = seeded(scenario)
    if damage == "copy":
        (backup / names[0]).write_bytes(b"different copy")
    elif damage == "source":
        (machine / names[0]).write_bytes(b"changed source")
    elif damage == "manifest":
        (machine / "wd-reboot-bundles" / commit / "deployment-manifest.json").write_bytes(b"tampered")
    else:
        (machine / names[1]).write_bytes(b"appeared")
    result = invoke(machine, backup, names, presence)
    assert result.returncode != 0
    assert not (backup / "rollback-inventory.json").exists()


@pytest.mark.skipif(os.name != "nt", reason="requires Windows PowerShell")
def test_inventory_hash_detects_postwrite_change(scenario):
    machine, backup, names = scenario
    presence, _, _, _ = seeded(scenario)
    result = invoke(machine, backup, names, presence)
    assert result.returncode == 0, result.stderr
    path = backup / "rollback-inventory.json"
    expected = (backup / "rollback-inventory.sha256").read_text().strip()
    path.write_bytes(path.read_bytes() + b" ")
    assert sha(path.read_bytes()) != expected


@pytest.mark.skipif(os.name != "nt", reason="requires Windows PowerShell")
@pytest.mark.parametrize("new_manifest", ["", "d" * 63, "g" * 64])
def test_new_manifest_hash_must_be_exact_sha256(scenario, new_manifest):
    machine, backup, names = scenario
    presence, _, _, _ = seeded(scenario)
    result = invoke(machine, backup, names, presence, new_manifest)
    assert result.returncode != 0
    assert not (backup / "rollback-inventory.json").exists()


@pytest.mark.skipif(os.name != "nt", reason="requires Windows PowerShell")
def test_copied_file_hash_detects_postwrite_change(scenario):
    machine, backup, names = scenario
    presence, _, _, _ = seeded(scenario)
    result = invoke(machine, backup, names, presence)
    assert result.returncode == 0, result.stderr
    doc = json.loads((backup / "rollback-inventory.json").read_bytes())
    expected = next(row["sha256"] for row in doc["files"] if row["name"] == names[0])
    (backup / names[0]).write_bytes(b"tampered")
    assert sha((backup / names[0]).read_bytes()) != expected


@pytest.mark.skipif(os.name != "nt", reason="requires Windows PowerShell")
def test_duplicate_or_escaping_transaction_names_refuse(scenario):
    machine, backup, names = scenario
    for bad_names in (names + [names[0].upper()], names + ["../escape"]):
        result = invoke(machine, backup, bad_names, {n: False for n in bad_names})
        assert result.returncode != 0
        assert not (backup / "rollback-inventory.json").exists()


@pytest.mark.skipif(os.name != "nt", reason="requires Windows PowerShell")
def test_prior_state_names_only_the_commit_addressed_bundle(scenario):
    machine, backup, names = scenario
    presence, _, _, _ = seeded(scenario)
    path = machine / names[2]
    data = json.loads(path.read_bytes())
    data["active_bundle"] = str(machine / "other")
    changed = json.dumps(data, separators=(",", ":")).encode()
    path.write_bytes(changed)
    (backup / names[2]).write_bytes(changed)
    result = invoke(machine, backup, names, presence)
    assert result.returncode != 0
    assert not (backup / "rollback-inventory.json").exists()


@pytest.mark.skipif(os.name != "nt", reason="requires Windows PowerShell")
def test_dangling_symlink_is_not_recorded_as_absent(scenario):
    machine, backup, names = scenario
    try:
        (machine / names[0]).symlink_to(machine / "missing-target")
    except OSError as error:
        pytest.skip(f"synthetic symlink creation unavailable: {error.winerror}")
    result = invoke(machine, backup, names, {name: False for name in names})
    assert result.returncode != 0, "dangling reparse point was recorded as absent"
    assert "backup inventory previously absent file appeared" in result.stderr, result.stderr
    assert not (backup / "rollback-inventory.json").exists()


@pytest.mark.skipif(os.name != "nt", reason="requires Windows PowerShell")
def test_real_backup_loop_refuses_dangling_symlink_before_copy(scenario):
    machine, backup, names = scenario
    try:
        (machine / names[0]).symlink_to(machine / "missing-target")
    except OSError as error:
        pytest.skip(f"synthetic symlink creation unavailable: {error.winerror}")
    env = dict(os.environ, TEST_SCRIPT=str(DEPLOY), TEST_MACHINE=str(machine), TEST_BACKUP=str(backup))
    script = r"""
$ErrorActionPreference='Stop'; $tokens=$null; $errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile($env:TEST_SCRIPT,[ref]$tokens,[ref]$errors)
if ($errors.Count) { throw 'parse failure' }
foreach($name in @('Resolve-FullPath','Assert-WdPathWithoutReparse','Assert-ChildPath')) {
  $fn=$ast.FindAll({param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -ceq $name},$true)
  if(@($fn).Count -ne 1) { throw 'missing function' }
  . ([scriptblock]::Create($fn[0].Extent.Text))
}
$text=[IO.File]::ReadAllText($env:TEST_SCRIPT)
$at=$text.IndexOf('$backedUp = $false', $text.IndexOf('$transactionNames = @('))
$end=$text.IndexOf('$backupInventory = Write-WdRebootBackupInventory', $at)
if($at -lt 0 -or $end -le $at) { throw 'backup loop boundaries unavailable' }
$machineFull=$env:TEST_MACHINE; $backupRoot=$env:TEST_BACKUP
$transactionNames=@('start-wd-all.ps1'); $refused=$false
try { . ([scriptblock]::Create($text.Substring($at,$end-$at))) } catch {
  if ($_.Exception.Message -like '*reparse point*') { $refused=$true }
}
if(-not $refused -or (Test-Path -LiteralPath (Join-Path $backupRoot 'start-wd-all.ps1'))) {
  throw 'real path checker did not refuse the synthetic dangling link before copy'
}
"""
    result = subprocess.run([str(SHELL), "-NoProfile", "-NonInteractive", "-Command", script],
                            cwd=ROOT, env=env, text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(os.name != "nt", reason="requires Windows PowerShell")
@pytest.mark.parametrize("reparse_kind", ["source", "backup_parent"])
def test_prebackup_reparse_refuses_before_copy_or_directory_creation(reparse_kind):
    # Execute only the backup loop, with every filesystem operation replaced by
    # a stub. The synthetic refusal has no target outside this process.
    env = dict(os.environ, TEST_SCRIPT=str(DEPLOY), TEST_REPARSE_KIND=reparse_kind)
    script = r"""
$ErrorActionPreference = 'Stop'
$text=[IO.File]::ReadAllText($env:TEST_SCRIPT)
$at=$text.IndexOf('$backedUp = $false', $text.IndexOf('$transactionNames = @('))
$end=$text.IndexOf('$backupInventory = Write-WdRebootBackupInventory', $at)
if($at -lt 0 -or $end -le $at) { throw 'backup loop boundaries unavailable' }
$fragment=$text.Substring($at, $end-$at)
$machineFull='C:\synthetic-machine'; $backupRoot='C:\synthetic-machine\wd-reboot-backups\synthetic'
$transactionNames=@('start-wd-all.ps1')
$global:copies=0; $global:dirs=0
function Assert-ChildPath { param($Parent,$Child) return $Child }
function Test-Path { param($LiteralPath,$PathType) return ($PathType -eq 'Leaf') }
function New-Item { param($ItemType,$Path,[switch]$Force) $global:dirs++ }
function Copy-Item { param($LiteralPath,$Destination) $global:copies++ }
function Assert-WdPathWithoutReparse {
  param($Path,$TrustedRoot,$ExpectedType,[switch]$AllowMissing)
  if ($env:TEST_REPARSE_KIND -eq 'source' -and $ExpectedType -eq 'Leaf') { throw 'synthetic source reparse' }
  if ($env:TEST_REPARSE_KIND -eq 'backup_parent' -and $ExpectedType -eq 'Directory') { throw 'synthetic backup reparse' }
  return $Path
}
$refused=$false
try { . ([scriptblock]::Create($fragment)) } catch {
  if ($_.Exception.Message -like 'synthetic * reparse') { $refused=$true }
}
if (-not $refused -or $global:copies -ne 0 -or
    ($env:TEST_REPARSE_KIND -eq 'backup_parent' -and $global:dirs -ne 0)) {
  throw 'not refused before mutation'
}
"""
    result = subprocess.run([str(SHELL), "-NoProfile", "-NonInteractive", "-Command", script],
                            cwd=ROOT, env=env, text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr


def test_inventory_is_written_after_backup_and_before_machine_mutation():
    text = DEPLOY.read_text(encoding="utf-8")
    start = text.index("$transactionNames = @(")
    call = text.index("Write-WdRebootBackupInventory -MachineRoot", start)
    assert text.index("Copy-Item -LiteralPath $machinePath", start) < call
    assert call < text.index("$machineMutationStarted = $false", call)


def test_dryrun_and_stageonly_do_not_resolve_or_read_previous_store():
    text = DEPLOY.read_text(encoding="utf-8")
    dry = text.index("Write-Host 'DRY RUN: no files, tasks, caches, or processes will be changed.'")
    dry_return = text.index("    return", dry)
    stage = text.index("Write-Host 'STAGE ONLY: commit-addressed bundle verified and left staged.")
    stage_return = text.index("    return", stage)
    prior = text.index("$previousStoreFull = $storeFull", stage_return)
    migration = text.index("# Migration is explicit", prior)
    assert dry < dry_return < stage < stage_return < prior < migration
    assert "Previous bundle store is NOT_CHECKED until a final install." in text[dry:dry_return]
    assert "Previous bundle store is NOT_CHECKED until a final install." in text[stage:stage_return]
    assert "-PreviousBundleStore $previousStoreFull" in text[migration:]

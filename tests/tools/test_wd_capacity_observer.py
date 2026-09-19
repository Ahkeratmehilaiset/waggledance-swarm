# SPDX-License-Identifier: BUSL-1.1
"""Exercise the actual pinned observer runner in both supported PowerShell hosts."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
HOSTS = [x for x in ('powershell', 'pwsh') if sys.platform == 'win32' and shutil.which(x)]
PYTHON = sys.executable
if sys.platform == 'win32' and 'WindowsApps' in PYTHON:
    candidate = Path(os.environ['LOCALAPPDATA']) / 'Programs/Python/Python313/python.exe'
    PYTHON = str(candidate) if candidate.is_file() else PYTHON


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


@pytest.mark.parametrize('host', HOSTS or [None])
@pytest.mark.parametrize('tamper', [None, 'manifest', 'source', 'executable'])
def test_runner_checks_pins_and_invokes_only_scheduled_metadata(tmp_path, host, tamper):
    if host is None:
        pytest.skip('PowerShell unavailable')
    runner = tmp_path / 'Invoke-WdCapacityObserver.ps1'
    shutil.copyfile(ROOT / 'ops/windows/reboot/Invoke-WdCapacityObserver.ps1', runner)
    tool = tmp_path / 'tools/bridge_capacity_collector.py'
    tool.parent.mkdir()
    tool.write_text("import sys\nassert '--scheduled' in sys.argv\n"
                    "assert '--provider' in sys.argv\nprint('metadata-only-fixture')\n")
    manifest = tmp_path / 'manifest.json'
    value = dict(schema='wd.capacity-observer-install.v1', execution_mode='metadata_only',
                 files={'tools/bridge_capacity_collector.py': sha(tool)},
                 python=PYTHON, python_sha256=sha(Path(PYTHON)),
                 codex=PYTHON, codex_sha256=sha(Path(PYTHON)),
                 store=str(tmp_path / 'observer.sqlite'))
    if tamper == 'executable':
        value['python_sha256'] = '0' * 64
    manifest.write_text(json.dumps(value), encoding='utf-8')
    anchor = sha(manifest)
    if tamper == 'source':
        tool.write_text("raise RuntimeError('must not run')\n")
    elif tamper == 'manifest':
        manifest.write_text('{}')
    proc = subprocess.run([host, '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
                           '-File', str(runner), '-ManifestPath', str(manifest),
                           '-ManifestSha256', anchor], capture_output=True, text=True, timeout=30)
    if tamper:
        assert proc.returncode != 0
        assert 'metadata-only-fixture' not in proc.stdout
    else:
        assert proc.returncode == 0, proc.stderr
        assert 'metadata-only-fixture' in proc.stdout


@pytest.mark.parametrize('host', HOSTS or [None])
def test_install_retry_after_registration_failure_reuses_exact_release(tmp_path, host):
    if host is None:
        pytest.skip('Windows PowerShell unavailable')
    repo = tmp_path / 'repo'
    source = repo / 'ops/windows/reboot'
    source.mkdir(parents=True)
    installer = source / 'Install-WdCapacityObserver.ps1'
    shutil.copyfile(ROOT / 'ops/windows/reboot/Install-WdCapacityObserver.ps1', installer)
    for relative in ('tools/bridge_capacity_advisor.py', 'tools/bridge_capacity_collector.py',
                     'tools/bridge_capacity_recovery.py', 'ops/windows/reboot/Invoke-WdCapacityObserver.ps1'):
        target = repo / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('fixture source')
    harness = tmp_path / 'retry.ps1'
    harness.write_text(r'''
param($Installer,$Python,$Root)
$ErrorActionPreference='Stop'
$global:wd_test_attempt=0
$global:wd_test_starts=0
$global:wd_test_task=$null
$global:wd_test_head='1111111111111111111111111111111111111111'
function git { $global:LASTEXITCODE=0; if($args -contains 'rev-parse') { $global:wd_test_head } }
function New-ScheduledTaskAction { param($Execute,$Argument,$WorkingDirectory) [pscustomobject]@{Execute=$Execute;Arguments=$Argument;WorkingDirectory=$WorkingDirectory} }
function New-ScheduledTaskTrigger { param([switch]$AtLogOn,$User,[switch]$Once,$At,$RepetitionInterval) [pscustomobject]@{} }
function New-ScheduledTaskSettingsSet { param($MultipleInstances,$ExecutionTimeLimit,[switch]$StartWhenAvailable) [pscustomobject]@{} }
function New-ScheduledTaskPrincipal { param($UserId,$LogonType,$RunLevel) [pscustomobject]@{UserId=$UserId;LogonType=$LogonType;RunLevel=$RunLevel} }
function Get-ScheduledTask { param($TaskName,$ErrorAction) $global:wd_test_task }
function Export-ScheduledTask { param($TaskName) '<Task>previous fixture</Task>' }
function Register-ScheduledTask { param($TaskName,$Action,$Trigger,$Settings,$Principal,[switch]$Force)
  $global:wd_test_attempt++; if($global:wd_test_attempt -eq 1){throw 'simulated registration denial'}
  $global:wd_test_task=[pscustomobject]@{Actions=@($Action);Principal=$Principal;State='Ready'}
}
function Start-ScheduledTask {param($TaskName) $global:wd_test_starts++}
try { & $Installer -PythonExecutable $Python -CodexExecutable $Python -InstallRoot $Root -Apply; throw 'first registration should fail' }
catch { if($_.Exception.Message -ne 'simulated registration denial'){throw} }
& $Installer -PythonExecutable $Python -CodexExecutable $Python -InstallRoot $Root -Apply
& $Installer -PythonExecutable $Python -CodexExecutable $Python -InstallRoot $Root -Apply
if($global:wd_test_attempt -ne 2 -or $global:wd_test_starts -ne 2){throw 'registration was duplicated'}
$user=[Security.Principal.WindowsIdentity]::GetCurrent()
foreach($alias in @(($user.Name -split '\\')[-1],$user.User.Value)) {
  $global:wd_test_task.Principal.UserId=$alias
  & $Installer -PythonExecutable $Python -CodexExecutable $Python -InstallRoot $Root -Apply
}
if($global:wd_test_attempt -ne 2 -or $global:wd_test_starts -ne 4){throw 'same SID alias not reused'}
foreach($foreign in @('S-1-5-18','S-1-5-invalid')) {
  $global:wd_test_task.Principal.UserId=$foreign
  try { & $Installer -PythonExecutable $Python -CodexExecutable $Python -InstallRoot $Root -Apply; throw 'foreign principal accepted' }
  catch { if($_.Exception.Message -ne 'Existing task is not this exact Limited observer; refusing replacement'){throw} }
}
if($global:wd_test_attempt -ne 2 -or $global:wd_test_starts -ne 4){throw 'foreign principal caused side effect'}
$global:wd_test_task.Principal.UserId=$user.User.Value
$global:wd_test_head='2222222222222222222222222222222222222222'
try { & $Installer -PythonExecutable $Python -CodexExecutable $Python -InstallRoot $Root -Apply; throw 'update must be explicit' }
catch { if($_.Exception.Message -ne 'A verified observer update requires -Apply -Update'){throw} }
& $Installer -PythonExecutable $Python -CodexExecutable $Python -InstallRoot $Root -Apply -Update
if($global:wd_test_attempt -ne 3 -or $global:wd_test_starts -ne 5){throw 'update not applied exactly once'}
'retry-preserved-exact-release'
''', encoding='utf-8')
    proc = subprocess.run([host, '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
                           '-File', str(harness), '-Installer', str(installer),
                           '-Python', PYTHON, '-Root', str(tmp_path / 'installed')],
                          capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    assert 'retry-preserved-exact-release' in proc.stdout


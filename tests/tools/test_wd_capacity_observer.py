# SPDX-License-Identifier: BUSL-1.1
"""Exercise the actual pinned observer runner in both supported PowerShell hosts."""
import hashlib
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from tools.bridge_capacity_collector import reserve_poll

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
                     'tools/bridge_capacity_recovery.py', 'ops/windows/reboot/Invoke-WdCapacityObserver.ps1',
                     'ops/windows/reboot/Get-WdCapacityStatus.ps1'):
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
function New-ScheduledTaskTrigger { param([switch]$AtLogOn,$User,[switch]$Once,$At,$RepetitionInterval)
  if($Once){$global:wd_test_poll_seconds=$RepetitionInterval.TotalSeconds}
  [pscustomobject]@{}
}
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
"poll-seconds=$global:wd_test_poll_seconds"
''', encoding='utf-8')
    proc = subprocess.run([host, '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
                           '-File', str(harness), '-Installer', str(installer),
                           '-Python', PYTHON, '-Root', str(tmp_path / 'installed')],
                          capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    assert 'retry-preserved-exact-release' in proc.stdout
    interval = float(next(line.split('=', 1)[1] for line in proc.stdout.splitlines()
                          if line.startswith('poll-seconds=')))
    # Reproduce the observed scheduler jitter using the actual configured trigger
    # and actual shared poll budget, without querying any provider.
    start = datetime.now(timezone.utc)
    admitted = []
    for tick in range(21):
        elapsed = tick * interval - (0.25 if tick else 0)
        if reserve_poll(tmp_path / 'cadence.sqlite', now=start + timedelta(seconds=elapsed)):
            admitted.append(elapsed)
    gaps = [b - a for a, b in zip(admitted, admitted[1:])]
    assert gaps and min(gaps) >= 300, 'provider budget must not increase'
    assert max(gaps) <= 360, 'small scheduler jitter must not cause a ten-minute gap'


@pytest.mark.parametrize('host', HOSTS or [None])
@pytest.mark.parametrize('case', ['valid','pointer','manifest','source','python','escaped_store','missing_store','foreign_store'])
def test_shared_status_locator_is_verified_readonly_and_does_not_collect(tmp_path, host, case):
    if host is None:
        pytest.skip('Windows PowerShell unavailable')
    root = tmp_path / 'installed'
    release = root / ('a' * 40)
    files = {}
    for relative in ('tools/bridge_capacity_advisor.py','tools/bridge_capacity_collector.py',
                     'ops/windows/reboot/Get-WdCapacityStatus.ps1'):
        target = release / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, target)
        files[relative.replace('/', '\\')] = sha(target)
    store = root / 'observations.sqlite'
    from tools.bridge_capacity_collector import save_observation
    import sqlite3
    if case == 'foreign_store':
        with sqlite3.connect(store) as db:
            db.execute('CREATE TABLE foreign_data(value TEXT)')
    elif case != 'missing_store':
        save_observation(store, dict(provider='codex',observed_at='2026-09-19T00:00:00Z'))
    manifest = release / 'manifest.json'
    value = dict(schema='wd.capacity-observer-install.v1',execution_mode='metadata_only',source_commit='a'*40,
                 files=files,python=PYTHON,python_sha256='0'*64 if case=='python' else sha(Path(PYTHON)),
                 store=str(tmp_path/'escaped.sqlite' if case=='escaped_store' else store))
    manifest.write_text(json.dumps(value),encoding='utf-8')
    pointer = dict(mode='metadata_only',source_commit='a'*40,manifest=str(manifest),manifest_sha256=sha(manifest))
    if case=='pointer': pointer['mode']='execute'
    (root/'current.json').write_text(json.dumps(pointer),encoding='utf-8')
    if case=='manifest': manifest.write_text('{}')
    if case=='source': (release/'tools/bridge_capacity_collector.py').write_text("raise RuntimeError('must not execute')")
    before = {str(p.relative_to(tmp_path)):p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    result = subprocess.run([host,'-NoProfile','-NonInteractive','-File',str(release/'ops/windows/reboot/Get-WdCapacityStatus.ps1'),
                             '-InstallRoot',str(root)],capture_output=True,text=True,timeout=30)
    assert result.returncode == (0 if case=='valid' else 2), result.stderr
    output = json.loads(result.stdout)
    assert output['execution_allowed'] is False
    if case=='valid': assert output['installation']['source_verified'] is True
    after = {str(p.relative_to(tmp_path)):p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    assert before == after

def native_hook_release(tmp_path):
    release = tmp_path / 'release'
    files = {}
    for relative in ('tools/bridge_capacity_advisor.py','tools/bridge_capacity_collector.py',
                     'ops/windows/reboot/Invoke-WdCapacityObserver.ps1'):
        target = release / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, target)
        files[relative.replace('/', '\\')] = sha(target)
    store = tmp_path / 'observations.sqlite'
    manifest = release / 'manifest.json'
    manifest.write_text(json.dumps(dict(schema='wd.capacity-observer-install.v1',execution_mode='metadata_only',
        files=files,python=PYTHON,python_sha256=sha(Path(PYTHON)),store=str(store))),encoding='utf-8')
    return release, manifest, store


@pytest.mark.parametrize('host', HOSTS or [None])
@pytest.mark.parametrize('case', ['auth', 'success', 'bad_manifest', 'failed_storage', 'statusline'])
def test_native_hook_runner_never_starts_provider_or_blocks_stop(tmp_path, host, case):
    if host is None: pytest.skip('Windows PowerShell unavailable')
    release, manifest, store = native_hook_release(tmp_path)
    anchor = '0'*64 if case=='bad_manifest' else sha(manifest)
    if case=='failed_storage': store.mkdir()
    payload = dict(session_id='native-fixture', hook_event_name='Stop' if case=='success' else 'StopFailure',
                   error='authentication_failed', error_details='SECRET', last_assistant_message='SECRET', prompt='SECRET')
    proc = subprocess.run([host,'-NoProfile','-NonInteractive','-File',str(release/'ops/windows/reboot/Invoke-WdCapacityObserver.ps1'),
                            '-ManifestPath',str(manifest),'-ManifestSha256',anchor,
                            '-Mode','ClaudeStatusline' if case=='statusline' else 'ClaudeHook'],
                           input=json.dumps(payload),text=True,capture_output=True,timeout=30)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == '' if case!='statusline' else 'quota age unknown' in proc.stdout
    assert 'SECRET' not in proc.stdout + proc.stderr
    if store.is_file():
        assert b'SECRET' not in store.read_bytes()
        from tools.bridge_capacity_collector import status
        result = status(store)
        if case=='auth': assert result['alerts'][0]['state']=='auth_required'
        if case=='success': assert result['native_activity'][0]['availability_state']=='successful_turn_observed'
    else:
        assert case in ('bad_manifest','failed_storage')


@pytest.mark.parametrize('host', HOSTS or [None])
def test_native_hook_install_preserves_foreign_hooks_and_is_idempotent(tmp_path, host):
    if host is None: pytest.skip('Windows PowerShell unavailable')
    release, manifest, _ = native_hook_release(tmp_path)
    worktree = tmp_path / 'worktree'
    (worktree/'.claude').mkdir(parents=True)
    (worktree/'.git').write_text('gitdir: fixture')
    settings = worktree/'.claude/settings.local.json'
    original = dict(permissions={'allow':['Bash(git status)']},hooks={'Stop':[{'hooks':[{'type':'command','command':'echo foreign'}]}]},
                    statusLine={'type':'command','command':'previous inspected observer'})
    settings.write_text(json.dumps(original))
    installer = ROOT/'ops/windows/reboot/Install-WdClaudeCapacityHooks.ps1'
    base = [host,'-NoProfile','-NonInteractive','-File',str(installer),'-Worktree',str(worktree),
            '-ManifestPath',str(manifest),'-ManifestSha256',sha(manifest),'-Apply']
    for _ in range(2):
        proc = subprocess.run(base+['-ExpectedSettingsSha256',sha(settings)],capture_output=True,text=True,timeout=30)
        assert proc.returncode == 0, proc.stderr
    value = json.loads(settings.read_text(encoding='utf-8-sig'))
    assert value['permissions']==original['permissions']
    assert len(value['hooks']['Stop'])==2
    assert value['hooks']['Stop'][0]['hooks'][0]['command']=='echo foreign'
    for event in ('UserPromptSubmit','StopFailure'): assert len(value['hooks'][event])==1
    assert len(list((worktree/'.codex-audit').glob('*.json')))==2
    value['statusLine']['command']='foreign change'
    settings.write_text(json.dumps(value))
    before=settings.read_bytes()
    proc = subprocess.run(base+['-ExpectedSettingsSha256',sha(settings)],capture_output=True,text=True,timeout=30)
    assert proc.returncode != 0 and settings.read_bytes()==before
@pytest.mark.parametrize('host', HOSTS or [None])
@pytest.mark.parametrize('site', ['runner','hooks'])
def test_junction_with_matching_hash_is_refused_before_writing(tmp_path, host, site):
    if host is None: pytest.skip('Windows NTFS junctions')
    release, manifest, store = native_hook_release(tmp_path)
    outside=tmp_path/'outside'
    (release/'tools').rename(outside)
    junction=release/'tools'
    proc=subprocess.run([host,'-NoProfile','-NonInteractive','-Command',
                         f"New-Item -ItemType Junction -Path '{junction}' -Target '{outside}' | Out-Null"],
                        capture_output=True,text=True,timeout=30)
    assert proc.returncode==0,proc.stderr
    try:
        if site=='runner':
            command=[str(release/'ops/windows/reboot/Invoke-WdCapacityObserver.ps1'),'-ManifestPath',str(manifest),
                     '-ManifestSha256',sha(manifest),'-Mode','ClaudeHook']
        else:
            worktree=tmp_path/'worktree'
            worktree.mkdir()
            (worktree/'.git').write_text('gitdir: fixture')
            command=[str(ROOT/'ops/windows/reboot/Install-WdClaudeCapacityHooks.ps1'),'-Worktree',str(worktree),
                     '-ManifestPath',str(manifest),'-ManifestSha256',sha(manifest),'-ExpectedSettingsSha256','missing','-Apply']
        proc=subprocess.run([host,'-NoProfile','-NonInteractive','-File']+command,
                             input=json.dumps(dict(session_id='fixture',hook_event_name='StopFailure',error='authentication_failed')),
                             capture_output=True,text=True,timeout=30)
        if site=='hooks':
            assert proc.returncode!=0 and not (worktree/'.claude/settings.local.json').exists()
        assert not store.exists(), 'matching hashes must not authorize traversing a junction'
    finally:
        # Remove just this verified fixture junction, never recurse through its target.
        assert junction.parent==release and junction.lstat().st_file_attributes & 0x400
        junction.rmdir()
@pytest.mark.parametrize('host', HOSTS or [None])
@pytest.mark.parametrize('dangling', [False,True])
def test_observer_installer_refuses_junction_root_before_any_write(tmp_path, host, dangling):
    if host is None: pytest.skip('Windows NTFS junctions')
    outside=tmp_path/'outside'
    outside.mkdir()
    sentinel=outside/'preserve.txt'
    sentinel.write_text('foreign bytes')
    junction=tmp_path/'installed'
    proc=subprocess.run([host,'-NoProfile','-NonInteractive','-Command',
                         f"New-Item -ItemType Junction -Path '{junction}' -Target '{outside}' | Out-Null"],
                        capture_output=True,text=True,timeout=30)
    assert proc.returncode==0,proc.stderr
    if dangling:
        moved=tmp_path/'moved'
        outside.rename(moved)
        outside=moved
        sentinel=outside/'preserve.txt'
    try:
        proc=subprocess.run([host,'-NoProfile','-NonInteractive','-File',str(ROOT/'ops/windows/reboot/Install-WdCapacityObserver.ps1'),
                             '-InstallRoot',str(junction),'-PythonExecutable',PYTHON,'-CodexExecutable',PYTHON,'-Apply'],
                            capture_output=True,text=True,timeout=30)
        assert proc.returncode!=0 and 'reparse point' in proc.stderr
        assert list(outside.iterdir())==[sentinel] and sentinel.read_text()=='foreign bytes'
    finally:
        assert junction.parent==tmp_path and junction.lstat().st_file_attributes & 0x400
        junction.rmdir()

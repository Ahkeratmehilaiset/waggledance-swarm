"""Exercise native launch argv: model choice must not be a reboot pin."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]
REBOOT = ROOT / 'ops/windows/reboot'
HOSTS = list(dict.fromkeys(filter(None, [shutil.which('pwsh'), shutil.which('powershell.exe')])))


@pytest.mark.parametrize('host', HOSTS or [None])
@pytest.mark.parametrize('lane', ['lead', 'tools', 'claude'])
def test_native_resume_leaves_model_and_effort_to_native_session(tmp_path, host, lane):
    if host is None or os.name != 'nt':
        pytest.skip('Windows native launcher')
    thread = '9f375967-f824-4e2e-8104-7f0011117cf5'
    settings = tmp_path / 'wd-claude-event-driven-settings.json'
    shutil.copyfile(REBOOT / settings.name, settings)
    pin = hashlib.sha256(settings.read_bytes()).hexdigest().upper()
    source = (REBOOT / 'start-wd-agent.ps1').read_text(encoding='utf-8')
    block = source.split('$launchArguments = @()\n', 1)[1].split('$previousPreference =', 1)[0]
    if lane == 'tools':
        block = f"""
$ast=[Management.Automation.Language.Parser]::ParseFile('{REBOOT / 'start-wd-tools-consumer.ps1'}',[ref]$null,[ref]$null)
$fn=$ast.Find({{param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -ceq 'Get-WdNativeToolsArguments'}},$true)
. ([scriptblock]::Create($fn.Extent.Text))
$launchArguments=Get-WdNativeToolsArguments -Saved $nativeResume -Worktree $worktree -Model native -Effort native -Prompt $continuationPrompt -ImagePath '' -WritableRoots @() -NetworkAccess $true
"""
    script = tmp_path / 'probe.ps1'
    script.write_text(f"""
$ErrorActionPreference='Stop'; Set-StrictMode -Version Latest
function Assert-LanePathWithoutReparse {{ param($Path,$TrustedRoot,$ExpectedType) return $Path }}
function Assert-WdLaneLaunchAvailable {{ param($Lane,$KnownLanes,$ExternalSessions,[switch]$AllowUnpinnedParser) }}
$deploymentAnchor=[pscustomobject]@{{files=[pscustomobject]@{{'wd-claude-event-driven-settings.json'='{pin}'}}}}
$laneTrustedDrive=[IO.Path]::GetPathRoot($PSScriptRoot)
$cliName='{ 'claude.cmd' if lane == 'claude' else 'codex.cmd' }'
$nativeLead=${str(lane == 'lead').lower()}; $model='native'; $effort='native'; $Agent='test'
$nativeResume=[pscustomobject]@{{thread_id='{thread}'; initial_context_delivered=$true}}
$claudeResume=$nativeResume; $worktree='C:\\work'; $lane=@{{}}; $manifest=@{{lanes=@()}}
$externalSessions=@(); $sourceTreeMode=$false; $DryRun=$false
$continuationPrompt='continue once'; $startupPrompt='continue once'; $targetImagePath=''
$launchArguments=@()
{block}
$launchArguments | ConvertTo-Json -Compress
""", encoding='utf-8')
    env = {k: v for k, v in os.environ.items() if k.upper() != 'PSMODULEPATH'}
    result = subprocess.run([host, '-NoProfile', '-NonInteractive', '-File', str(script)],
                            capture_output=True, text=True, timeout=30, env=env)
    assert result.returncode == 0, result.stderr
    args = json.loads(result.stdout)
    assert args[:2] == ['--resume' if lane == 'claude' else 'resume', thread]
    assert '--model' not in args
    assert '--effort' not in args
    assert not any('model_reasoning_effort' in a for a in args)
    assert 'native' not in args
    if lane == 'tools':
        assert args[args.index('--sandbox') + 1] == 'workspace-write'
    elif lane == 'lead':
        assert args[args.index('--sandbox') + 1] == 'danger-full-access'
    else:
        assert args[args.index('--settings') + 1] == str(settings)


def test_shipped_fleet_delegates_native_model_selection():
    fleet = json.loads((REBOOT / 'wd-fleet.json').read_text())
    supervisor = json.loads((REBOOT / 'wd_supervisor_loop.json').read_text())
    for lane in fleet['lanes']:
        assert (lane['model'], lane['effort']) == ('native', 'native')
    for tools in [fleet['tools_supervisor'], supervisor['tools_consumer']]:
        assert (tools['model'], tools['reasoning_effort']) == ('native', 'native')


@pytest.mark.parametrize('host', HOSTS or [None])
@pytest.mark.parametrize('tamper', [False, True])
def test_installed_result_library_is_pinned_and_preserves_callers_bridge_pin(tmp_path, host, tamper):
    if host is None:
        pytest.skip('PowerShell unavailable')
    helper = tmp_path / 'CheckedPowerShellResult.ps1'
    shutil.copyfile(ROOT / 'tools/CheckedPowerShellResult.ps1', helper)
    helper_hash = hashlib.sha256(helper.read_bytes()).hexdigest().upper()
    manifest = tmp_path / 'deployment-manifest.json'
    manifest.write_text('{}')
    manifest_hash = hashlib.sha256(manifest.read_bytes()).hexdigest().upper()
    if tamper:
        helper.write_text('throw "untrusted helper executed"')
    script = tmp_path / 'probe.ps1'
    script.write_text(f"""
$ErrorActionPreference='Stop'
$ast=[Management.Automation.Language.Parser]::ParseFile('{REBOOT / 'Deploy-WdRebootBundle.ps1'}',[ref]$null,[ref]$null)
$fn=$ast.Find({{param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -ceq 'New-ForwardingWrapper'}},$true)
. ([scriptblock]::Create($fn.Extent.Text))
$text=New-ForwardingWrapper -Target '{helper}' -ExpectedHash '{helper_hash}' -ExpectedManifestHash '{manifest_hash}' -WrapperKind library
$env:WD_REBOOT_EXPECTED_MANIFEST_HASH='existing-session-pin'
$global:LASTEXITCODE=7
. ([scriptblock]::Create($text))
$result=Invoke-CheckedResult {{ [pscustomobject]@{{passed=$true}} }}
@{{passed=$result.passed;pin=$env:WD_REBOOT_EXPECTED_MANIFEST_HASH;old_exit=$LASTEXITCODE}}|ConvertTo-Json -Compress
""", encoding='utf-8')
    env = {k: v for k, v in os.environ.items() if k.upper() != 'PSMODULEPATH'}
    result = subprocess.run([host, '-NoProfile', '-NonInteractive', '-File', str(script)],
                            capture_output=True, text=True, timeout=30, env=env)
    if tamper:
        assert result.returncode != 0
        assert 'integrity mismatch' in result.stderr
        assert 'untrusted helper executed' not in result.stderr
    else:
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout) == dict(passed=True, pin='existing-session-pin', old_exit=7)

"""Fleet launch passes only explicitly supplied Mind policy pins."""
import json
import os
from pathlib import Path

import pytest

from test_wd_reboot_bundle import REBOOT, LANE_TEST_SHELLS, _run_powershell
from test_wd_startup_recovery import load, q


FLEET = REBOOT / 'start-wd-all.ps1'
SHA = 'a' * 64
APPROVAL = 'b' * 64


@pytest.mark.skipif(os.name != 'nt', reason='Windows launch arguments')
@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda value: Path(value).stem)
@pytest.mark.parametrize('policy,sha,approval,expected', [
    ('', '', '', []),
    (r'C:\Python\wd-mind-identifier-policy.json', SHA, APPROVAL,
     ['-MindOperatorPolicyPath', r'"C:\Python\wd-mind-identifier-policy.json"',
      '-MindOperatorPolicySha256', SHA, '-MindOperatorApprovalReferenceSha256', APPROVAL]),
    (r'C:\Program Files\Mind\policy.json', SHA.upper(), APPROVAL.upper(),
     ['-MindOperatorPolicyPath', r'"C:\Program Files\Mind\policy.json"',
      '-MindOperatorPolicySha256', SHA, '-MindOperatorApprovalReferenceSha256', APPROVAL]),
    ('C:/Python/synthetic-policy.json', SHA, APPROVAL,
     ['-MindOperatorPolicyPath', r'"C:\Python\synthetic-policy.json"',
      '-MindOperatorPolicySha256', SHA, '-MindOperatorApprovalReferenceSha256', APPROVAL]),
])
def test_explicit_complete_pins_only(ps, policy, sha, approval, expected):
    script = load(FLEET, 'Get-WdMindFleetPolicyArguments') + f'''
$ErrorActionPreference='Stop'
$value=@(Get-WdMindFleetPolicyArguments -PolicyPath {q(policy)} -PolicySha256 {q(sha)} -ApprovalSha256 {q(approval)})
ConvertTo-Json -InputObject $value -Compress
'''
    assert json.loads(_run_powershell(script, executable=ps).stdout.lstrip('\ufeff')) == expected


@pytest.mark.skipif(os.name != 'nt', reason='Windows launch arguments')
@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda value: Path(value).stem)
@pytest.mark.parametrize('policy,sha,approval', [
    (r'C:\Python\policy.json', '', ''),
    ('', SHA, APPROVAL),
    (r'C:\Python\policy.json', SHA, ''),
    (r'C:\Python\policy.json', 'x' * 64, APPROVAL),
    (r'C:\Python\policy.json', SHA, 'b' * 63),
    (r'D:\Python\policy.json', SHA, APPROVAL),
    ('d:/Python/policy.json', SHA, APPROVAL),
    (r'C:relative\policy.json', SHA, APPROVAL),
    (r'C:\Python\..\policy.json', SHA, APPROVAL),
    ('C:/Python/../policy.json', SHA, APPROVAL),
    ('C:/Python/./policy.json', SHA, APPROVAL),
    (r'C:\Python\folder.\policy.json', SHA, APPROVAL),
    (r'C:\Python\folder \policy.json', SHA, APPROVAL),
    ('C:/Python/folder./policy.json', SHA, APPROVAL),
    (r'C:\Python\a&b.json', SHA, APPROVAL),
    (r'C:\Python\a;b.json', SHA, APPROVAL),
    ('C:\\Python\\policy" -Apply', SHA, APPROVAL),
    ('C:\\Python\\', SHA, APPROVAL),
])
def test_incomplete_or_unsafe_pins_refuse(ps, policy, sha, approval):
    script = load(FLEET, 'Get-WdMindFleetPolicyArguments') + f'''
$ErrorActionPreference='Stop'
try {{ [void](Get-WdMindFleetPolicyArguments -PolicyPath {q(policy)} -PolicySha256 {q(sha)} -ApprovalSha256 {q(approval)}); 'ACCEPT' }}
catch {{ 'REFUSE' }}
'''
    assert _run_powershell(script, executable=ps).stdout.strip() == 'REFUSE'


def test_fleet_supplies_explicit_pins_to_each_interactive_launch():
    source = FLEET.read_text(encoding='utf-8-sig')
    for name in ('MindOperatorPolicyPath', 'MindOperatorPolicySha256', 'MindOperatorApprovalReferenceSha256'):
        assert f'[string] ${name}' in source
    assert source.index('$mindPolicyArguments = @(') < source.index('$launchStartedUtc =')
    launch = source[source.index('  foreach ($state in $laneStates) {', source.index('$launchStartedUtc =')):]
    assert launch.index('$wtArguments += $mindPolicyArguments') < launch.index('Start-Process -FilePath $wtPath')


@pytest.mark.skipif(os.name != 'nt', reason='Windows launcher argument construction')
@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda value: Path(value).stem)
def test_four_fleet_launches_receive_explicit_pins_without_starting_terminal(ps):
    source = FLEET.read_text(encoding='utf-8-sig')
    start = source.index('  foreach ($state in $laneStates) {', source.index('$launchStartedUtc ='))
    end = source.index('  if ($launched.Count -gt 0)', start)
    launch = source[start:end]
    script = load(FLEET, 'Get-WdMindFleetPolicyArguments') + r'''
$ErrorActionPreference='Stop'
$script:captured=@()
function Write-Host { }
function Start-Process { param($FilePath,$ArgumentList,[switch]$PassThru)
  $script:captured += ,@($ArgumentList)
}
$names=@('codex-lead-1','claude-rco-1','claude-rco-2','fable-5')
$laneStates=@(foreach ($name in $names) { [pscustomobject]@{live=@();lane=[pscustomobject]@{agent=$name;worktree='C:\Python\project2'}} })
$launched=@();$wtPath='C:\synthetic\wt.exe';$expectedSupervisorExecutable='C:\synthetic\powershell.exe'
$agentLauncher='C:\synthetic\start-wd-agent.ps1';$ManifestPath='C:\synthetic\fleet.json'
$RunId='synthetic';$handshakeDirectory='C:\synthetic\handshake';$bundleManifestAnchor='a'*64
$ExternalSessionsPath='';$ExternalSessionsHash=''
$mindPolicyArguments=@(Get-WdMindFleetPolicyArguments -PolicyPath 'C:\Synthetic Space\policy.json' -PolicySha256 ('a'*64) -ApprovalSha256 ('b'*64))
''' + launch + '''
ConvertTo-Json -InputObject @($script:captured) -Depth 5 -Compress
'''
    calls = json.loads(_run_powershell(script, executable=ps).stdout.lstrip('\ufeff'))
    assert len(calls) == 4
    assert {args[args.index('-Agent') + 1] for args in calls} == {
        'codex-lead-1', 'claude-rco-1', 'claude-rco-2', 'fable-5'}
    for args in calls:
        assert args[-6:] == ['-MindOperatorPolicyPath', r'"C:\Synthetic Space\policy.json"',
                             '-MindOperatorPolicySha256', SHA,
                             '-MindOperatorApprovalReferenceSha256', APPROVAL]


@pytest.mark.skipif(os.name != 'nt', reason='Windows launch arguments')
@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda value: Path(value).stem)
@pytest.mark.parametrize('policy,sha,approval,warning', [
    ('', '', '', False),
    (r'C:\Python\policy.json', SHA, '', True),
    (r'C:\Python\policy.json', 'not-a-pin', APPROVAL, True),
])
def test_invalid_mind_configuration_keeps_fleet_launchable(ps, policy, sha, approval, warning):
    source = FLEET.read_text(encoding='utf-8-sig')
    start = source.index('$mindPolicyArguments = @()')
    end = source.index('$modeWasDefaulted =', start)
    preflight = source[start:end]
    script = load(FLEET, 'Get-WdMindFleetPolicyArguments') + f'''
$ErrorActionPreference='Stop'
$warnings=@()
function Write-Warning {{ param([string]$Message,[string]$WarningAction) $script:warnings += $Message }}
$MindOperatorPolicyPath={q(policy)}
$MindOperatorPolicySha256={q(sha)}
$MindOperatorApprovalReferenceSha256={q(approval)}
''' + preflight + '''
@{arguments=@($mindPolicyArguments);warnings=@($warnings);lane_launch_reached=$true} | ConvertTo-Json -Compress
'''
    result = json.loads(_run_powershell(script, executable=ps).stdout.lstrip('\ufeff'))
    assert result['arguments'] == [] and result['lane_launch_reached'] is True
    assert bool(result['warnings']) is warning
    if warning:
        assert 'refused' in result['warnings'][0] and 'without Mind' in result['warnings'][0]


@pytest.mark.skipif(os.name != 'nt', reason='Windows launch arguments')
@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda value: Path(value).stem)
def test_warning_preference_stop_does_not_block_lane_launch(ps):
    source = FLEET.read_text(encoding='utf-8-sig')
    start = source.index('$mindPolicyArguments = @()')
    end = source.index('$modeWasDefaulted =', start)
    preflight = source[start:end]
    script = load(FLEET, 'Get-WdMindFleetPolicyArguments') + '''
$ErrorActionPreference='Stop'
$WarningPreference='Stop'
$MindOperatorPolicyPath='C:\\Python\\synthetic.json'
$MindOperatorPolicySha256=''
$MindOperatorApprovalReferenceSha256=''
. {
''' + preflight + '''
} 3>$null
@{arguments=@($mindPolicyArguments);lane_launch_reached=$true} | ConvertTo-Json -Compress
'''
    result = json.loads(_run_powershell(script, executable=ps).stdout.lstrip('\ufeff'))
    assert result == {'arguments': [], 'lane_launch_reached': True}


@pytest.mark.skipif(os.name != 'nt', reason='Windows launch arguments')
@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda value: Path(value).stem)
def test_quoted_policy_path_round_trips_to_a_child_process(tmp_path, ps):
    child = tmp_path / 'synthetic child.ps1'
    child.write_text('''param([string]$MindOperatorPolicyPath,[string]$MindOperatorPolicySha256,
      [string]$MindOperatorApprovalReferenceSha256)
@{path=$MindOperatorPolicyPath;pin=$MindOperatorPolicySha256;approval=$MindOperatorApprovalReferenceSha256} |
  ConvertTo-Json -Compress
''', encoding='utf-8')
    policy = str(tmp_path / 'space in path' / 'synthetic-policy.json')
    output = tmp_path / 'child-output.json'
    script = load(FLEET, 'Get-WdMindFleetPolicyArguments') + f'''
$ErrorActionPreference='Stop'
$policyArgs=@(Get-WdMindFleetPolicyArguments -PolicyPath {q(policy)} -PolicySha256 {q(SHA)} -ApprovalSha256 {q(APPROVAL)})
$arguments=@('-NoProfile','-NonInteractive','-File',('"'+{q(child)}+'"'))+$policyArgs
$p=Start-Process -FilePath {q(ps)} -ArgumentList $arguments -RedirectStandardOutput {q(output)} -WindowStyle Hidden -PassThru -Wait
if ($p.ExitCode -ne 0) {{ throw 'synthetic_child_failed' }}
Get-Content -LiteralPath {q(output)} -Raw
'''
    result = json.loads(_run_powershell(script, executable=ps).stdout.lstrip('\ufeff'))
    assert result == {'path': policy, 'pin': SHA, 'approval': APPROVAL}

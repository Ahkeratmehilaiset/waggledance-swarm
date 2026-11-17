"""Opt-in Tools pin forwarding uses synthetic values and no live processes."""
import hashlib
import json
from pathlib import Path
import subprocess

import pytest

from test_wd_reboot_bundle import LANE_TEST_SHELLS, REBOOT, _run_powershell
from test_wd_startup_recovery import load
from test_mind_launch_wrapper_2179 import GENERATE


HELPER = REBOOT / 'WdBridgeLimitedBootstrap.ps1'
SUPERVISOR = REBOOT / 'wd_supervisor.ps1'
H1, H2 = 'b' * 64, 'c' * 64
POLICY = r"C:\synthetic path\owner's policy.json"


def quoted(value):
    return "'" + value.replace("'", "''") + "'"


@pytest.mark.parametrize('shell', LANE_TEST_SHELLS, ids=lambda value: Path(value).stem)
@pytest.mark.parametrize('mode', ['apply', 'dryrun'])
@pytest.mark.parametrize('case', ['complete', 'forward_slash', 'omitted', 'partial', 'bad_hash', 'bad_path', 'trailing', 'root', 'whitespace'])
def test_limited_supervisor_forwards_only_a_complete_valid_triplet(shell, mode, case):
    path = {'bad_path': r'C:\synthetic\..\policy.json', 'trailing': 'C:\\synthetic\\',
            'root': 'C:\\', 'whitespace': '   ', 'forward_slash': POLICY.replace('\\', '/')}.get(case, POLICY)
    policy = H1 if case != 'bad_hash' else 'not-a-sha'
    approval = '' if case == 'partial' else H2
    if case == 'whitespace': policy, approval = ' ', '  '
    if case == 'omitted':
        path = policy = approval = ''
    args = (f"-MindOperatorPolicyPath {quoted(path)} -MindOperatorPolicySha256 {quoted(policy)} "
            f"-MindOperatorApprovalReferenceSha256 {quoted(approval)}")
    script = ("$ErrorActionPreference='Stop'; $WarningPreference='SilentlyContinue'\n" +
              load(HELPER, 'Get-WdBridgeSupervisorArguments') + '\n' +
              "$a=@(Get-WdBridgeSupervisorArguments -SupervisorScript 'wd_supervisor.ps1' "
              f"-ConfigPath 'config.json' -Apply ${str(mode == 'apply').lower()} {args}); "
              '@{arguments=$a} | ConvertTo-Json -Compress')
    result = json.loads(_run_powershell(script, executable=shell).stdout.strip().splitlines()[-1])
    forwarded = result['arguments']
    assert '-BridgeWorkersOnly' in forwarded
    assert ('-Apply' in forwarded) == (mode == 'apply')
    if case in ('complete', 'forward_slash'):
        assert forwarded[-6:] == ['-MindOperatorPolicyPath', POLICY, '-MindOperatorPolicySha256', H1,
                                  '-MindOperatorApprovalReferenceSha256', H2]
    else:
        assert '-MindOperatorPolicyPath' not in forwarded
        assert '-MindOperatorPolicySha256' not in forwarded
        assert '-MindOperatorApprovalReferenceSha256' not in forwarded
        assert ('-MindRequestedButDisabled' in forwarded) == (case != 'omitted')


@pytest.mark.parametrize('shell', LANE_TEST_SHELLS, ids=lambda value: Path(value).stem)
def test_supervisor_rejects_partial_pins_without_startup_arguments(shell):
    script = ("$ErrorActionPreference='Stop'; $WarningPreference='SilentlyContinue'\n" +
              load(SUPERVISOR, 'Get-WdToolsMindArguments') + '\n' +
              "$r=Get-WdToolsMindArguments -PolicyPath 'C:\\synthetic\\policy.json' "
              f"-PolicySha256 '{H1}' -ApprovalReferenceSha256 ''; "
              '@{enabled=$r.enabled;reason=$r.reason;arguments=@($r.arguments)} | ConvertTo-Json -Compress')
    result = json.loads(_run_powershell(script, executable=shell).stdout)
    assert result['enabled'] is False and result['reason'] == 'invalid_pins'
    assert result['arguments'] == []


@pytest.mark.parametrize('shell', LANE_TEST_SHELLS, ids=lambda value: Path(value).stem)
def test_supervisor_normalizes_forward_slash_policy_path(shell):
    script = ("$ErrorActionPreference='Stop'\n" + load(SUPERVISOR, 'Get-WdToolsMindArguments') + '\n' +
              f"$r=Get-WdToolsMindArguments -PolicyPath {quoted(POLICY.replace(chr(92), '/'))} "
              f"-PolicySha256 '{H1.upper()}' -ApprovalReferenceSha256 '{H2.upper()}'; "
              '@{enabled=$r.enabled;arguments=@($r.arguments)} | ConvertTo-Json -Compress')
    result = json.loads(_run_powershell(script, executable=shell).stdout)
    assert result['enabled'] is True
    assert result['arguments'] == ['-MindOperatorPolicyPath', POLICY, '-MindOperatorPolicySha256', H1,
                                   '-MindOperatorApprovalReferenceSha256', H2]


@pytest.mark.parametrize('shell', LANE_TEST_SHELLS, ids=lambda value: Path(value).stem)
@pytest.mark.parametrize('case', ['disabled', 'invalid_pin', 'valid', 'omitted'])
def test_actual_stale_tools_branch_never_stops_a_lane_for_mind_request(shell, case):
    # Execute the supervisor's actual reconciliation branch with synthetic
    # process records and no-op process operations; no real lane is touched.
    reason = {'disabled':'off', 'invalid_pin':'shape_valid', 'valid':'shape_valid', 'omitted':'off'}[case]
    disabled = '$true' if case == 'disabled' else '$false'
    script = r'''
$ErrorActionPreference='Stop'
$source=Get-Content -LiteralPath __SOURCE__ -Raw
$tokens=$null; $errors=$null
$ast=[Management.Automation.Language.Parser]::ParseInput($source,[ref]$tokens,[ref]$errors)
if($errors.Count){throw 'parse'}
$toolsMind=[pscustomobject]@{reason=__REASON__}
$MindRequestedButDisabled=__DISABLED__
$wrapperProcesses=@([pscustomobject]@{Name='powershell.exe';CommandLine='';ProcessId=123})
$opaqueHostsBlock=$false; $readyWrapperProcesses=@(); $exactWrapperProcesses=@(); $legacyConsumers=@()
$graceWrapperProcesses=@(); $configuredWrapperProcesses=$wrapperProcesses; $expiredWrapperProcesses=@()
$toolsConversationSurface='headless'; $Apply=$true; $toolsProcesses=@(); $toolsConflictPath='unused'
$toolsConfig='unused'; $toolsGeneration='unused'; $toolsPowerShellHost='unused'; $toolsArguments=@()
$actions=New-Object 'System.Collections.Generic.List[string]'; $script:fakeStopped=$false; $script:fakeStarted=$false
function Stop-VerifiedProcessTree { param($RootProcess,$InitialProcesses,$ConflictPath) $script:fakeStopped=$true; return 0 }
function Assert-MachineToolsConfigExact {param($MachineConfigPath)}
function Assert-SupervisorBundleFileIntegrity {param($RelativePath)}
function Start-OutOfTaskJobPowerShell {param($a,$b,$c) $script:fakeStarted=$true}
$assignment=@($ast.FindAll({param($n) $n -is [Management.Automation.Language.AssignmentStatementAst]},$true) |
    Where-Object { $_.Extent.Text.StartsWith('$requestedMindMismatch =') })
if($assignment.Count -ne 1){throw 'assignment'}
. ([scriptblock]::Create($assignment[0].Extent.Text))
$branch=@($ast.FindAll({param($n) $n -is [Management.Automation.Language.IfStatementAst]},$true) |
    Where-Object { $_.Extent.Text.StartsWith('if ($requestedMindMismatch)') -and $_.Extent.Text.Contains('Stop-VerifiedProcessTree') })
if($branch.Count -ne 1){throw 'branch'}
. ([scriptblock]::Create($branch[0].Extent.Text))
@{stopped=$script:fakeStopped;started=$script:fakeStarted;actions=@($actions)} | ConvertTo-Json -Compress
'''.replace('__SOURCE__', quoted(str(SUPERVISOR))).replace('__REASON__', quoted(reason)).replace('__DISABLED__', disabled)
    result = json.loads(_run_powershell(script, executable=shell).stdout)
    assert result['stopped'] is (case == 'omitted')
    assert result['started'] is (case == 'omitted')
    if case != 'omitted':
        assert any('no process changes' in action for action in result['actions'])


@pytest.mark.parametrize('shell', LANE_TEST_SHELLS, ids=lambda value: Path(value).stem)
def test_invalid_optional_pins_do_not_throw_when_warnings_are_errors(shell):
    script = ("$ErrorActionPreference='Stop'; $WarningPreference='Stop'\n" +
              load(HELPER, 'Get-WdBridgeSupervisorArguments') + '\n' +
              "$result=@(Get-WdBridgeSupervisorArguments -SupervisorScript 'wd_supervisor.ps1' "
              "-ConfigPath 'config.json' -Apply $true -MindOperatorPolicyPath 'C:\\synthetic\\policy.json' 3>&1); "
              "$warnings=@($result | Where-Object { $_ -is [Management.Automation.WarningRecord] }); "
              "$arguments=@($result | Where-Object { $_ -is [string] }); "
              '@{warnings=$warnings.Count;arguments=$arguments} | ConvertTo-Json -Compress')
    result = json.loads(_run_powershell(script, executable=shell).stdout)
    assert result['warnings'] == 1
    assert '-BridgeWorkersOnly' in result['arguments']
    assert '-MindOperatorPolicyPath' not in result['arguments']


@pytest.mark.parametrize('shell', LANE_TEST_SHELLS, ids=lambda value: Path(value).stem)
@pytest.mark.parametrize('case', ['complete', 'partial', 'whitespace', 'trailing', 'root'])
def test_limited_bootstrap_passes_safe_argv_to_only_a_synthetic_supervisor(tmp_path, shell, case):
    fake = tmp_path / 'synthetic-supervisor.ps1'
    fake.write_text("[CmdletBinding()] param([switch]$Apply,[switch]$BridgeWorkersOnly,[string]$ConfigPath='', "
                    "[string]$MindOperatorPolicyPath='', [string]$MindOperatorPolicySha256='', "
                    "[string]$MindOperatorApprovalReferenceSha256='', [switch]$MindRequestedButDisabled)\n"
                    "@{apply=[bool]$Apply;only=[bool]$BridgeWorkersOnly;path=$MindOperatorPolicyPath;"
                    "policy=$MindOperatorPolicySha256;approval=$MindOperatorApprovalReferenceSha256;"
                    "disabled=[bool]$MindRequestedButDisabled} | ConvertTo-Json -Compress\n", encoding='utf-8')
    path = {'whitespace':' ', 'trailing':'C:\\synthetic\\', 'root':'C:\\'}.get(case, POLICY)
    digest = ' ' if case == 'whitespace' else H1
    approval = '' if case == 'partial' else (' ' if case == 'whitespace' else H2)
    functions = ('Get-WdBridgeSupervisorArguments', 'Invoke-WdBridgeSupervisorOnce')
    script = ("$ErrorActionPreference='Stop';$WarningPreference='Stop'\n" +
              '\n'.join(load(HELPER, name) for name in functions) + '\n' +
              f"$r=Invoke-WdBridgeSupervisorOnce -HostPath {quoted(shell)} -SupervisorScript {quoted(str(fake))} "
              f"-ConfigPath 'config.json' -Apply $true -TimeoutSeconds 20 -MindOperatorPolicyPath {quoted(path)} "
              f"-MindOperatorPolicySha256 {quoted(digest)} -MindOperatorApprovalReferenceSha256 {quoted(approval)}; "
              '@{exit_code=$r.exit_code;timed_out=$r.timed_out;lines=@($r.lines)} | ConvertTo-Json -Compress')
    result = json.loads(_run_powershell(script, executable=shell).stdout.strip().splitlines()[-1])
    assert result['exit_code'] == 0 and result['timed_out'] is False
    data = json.loads(result['lines'][0])
    assert data['apply'] is True and data['only'] is True
    if case == 'complete':
        assert (data['path'], data['policy'], data['approval'], data['disabled']) == (POLICY, H1, H2, False)
    else:
        assert (data['path'], data['policy'], data['approval'], data['disabled']) == ('', '', '', True)


@pytest.mark.parametrize('shell', LANE_TEST_SHELLS, ids=lambda value: Path(value).stem)
@pytest.mark.parametrize('case', ['valid', 'wrong_policy', 'wrong_approval', 'changed_policy', 'changed_helper', 'changed_manifest'])
def test_supervisor_checks_actual_policy_and_manifest_approved_descriptor(tmp_path, shell, case):
    bundle = tmp_path / 'bundle'
    bundle.mkdir()
    helper = bundle / 'Invoke-WdMindStartupRead.ps1'
    helper.write_bytes((REBOOT / helper.name).read_bytes())
    policy = tmp_path / 'synthetic-policy.json'
    policy.write_bytes(b'{"synthetic":true}')
    policy_hash = hashlib.sha256(policy.read_bytes()).hexdigest()
    descriptor = bundle / 'wd-mind-identifier-policy-approval.json'
    descriptor.write_text(json.dumps({'approval_reference_sha256': H2, 'identifier_policy_sha256': policy_hash,
        'schema': 'wd.mind-identifier-policy-approval.v1'}, sort_keys=True, separators=(',', ':')), encoding='ascii')
    sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = bundle / 'deployment-manifest.json'
    manifest.write_text(json.dumps({'schema_version': 1, 'files': {helper.name: sha(helper), descriptor.name: sha(descriptor)}}),
                        encoding='ascii')
    manifest_hash = sha(manifest)
    if case == 'changed_policy': policy.write_bytes(b'{"synthetic":false}')
    if case == 'changed_helper': helper.write_bytes(helper.read_bytes() + b'\n# changed')
    if case == 'changed_manifest': manifest.write_bytes(manifest.read_bytes() + b' ')
    effective_policy = 'd' * 64 if case == 'wrong_policy' else policy_hash
    approval = 'd' * 64 if case == 'wrong_approval' else H2
    functions = ('Read-Utf8SupervisorSnapshot', 'Assert-WdSupervisorPathWithoutReparse', 'Test-WdToolsMindPolicyApproval')
    script = ("$ErrorActionPreference='Stop'\n" + '\n'.join(load(SUPERVISOR, name) for name in functions) + '\n' +
              f"$env:WD_REBOOT_EXPECTED_MANIFEST_HASH='{manifest_hash}'; "
              f"$r=Test-WdToolsMindPolicyApproval -BundleRoot {quoted(str(bundle))} -PolicyPath {quoted(str(policy))} "
              f"-PolicySha256 '{effective_policy}' -ApprovalReferenceSha256 '{approval}'; "
              '@{approved=$r} | ConvertTo-Json -Compress')
    result = json.loads(_run_powershell(script, executable=shell).stdout)
    assert result['approved'] is (case == 'valid')


@pytest.mark.parametrize('shell', LANE_TEST_SHELLS, ids=lambda value: Path(value).stem)
@pytest.mark.parametrize('case', ['complete', 'forward_slash', 'partial', 'bad_hash', 'whitespace', 'trailing', 'root', 'omitted'])
def test_generated_fleet_wrapper_forwards_or_disables_optional_pins_in_dry_run(tmp_path, shell, case):
    commit = 'a' * 40
    bundle = tmp_path / 'bundles' / commit
    machine = tmp_path / 'machine'
    bundle.mkdir(parents=True)
    machine.mkdir()
    target = bundle / 'target.ps1'
    target.write_text("[CmdletBinding()] param([string]$MindOperatorPolicyPath='', [string]$MindOperatorPolicySha256='', "
                      "[string]$MindOperatorApprovalReferenceSha256='', [switch]$DryRun)\n"
                      "@{path=$MindOperatorPolicyPath;policy=$MindOperatorPolicySha256;"
                      "approval=$MindOperatorApprovalReferenceSha256;"
                      "dry=[bool]$DryRun}|ConvertTo-Json -Compress\n",
                      encoding='utf-8')
    manifest = bundle / 'deployment-manifest.json'
    manifest.write_text('{"schema_version":1}', encoding='ascii')
    sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest().upper()
    wrapper = machine / 'fleet.ps1'
    generator = tmp_path / 'generate.ps1'
    generator.write_text(GENERATE, encoding='utf-8')
    generated = subprocess.run([shell, '-NoProfile', '-NonInteractive', '-File', str(generator),
        '-Deployer', str(REBOOT / 'Deploy-WdRebootBundle.ps1'), '-Target', str(target), '-EntryHash', sha(target),
        '-ManifestHash', sha(manifest), '-Commit', commit, '-Out', str(wrapper), '-Kind', 'fleet', '-FixedAgent', ''],
        capture_output=True, text=True, timeout=30)
    assert generated.returncode == 0, generated.stderr
    (machine / 'WD_REBOOT_STATE_CURRENT.json').write_text(json.dumps({'source_commit':commit, 'final_commit':commit,
        'manifest_sha256':sha(manifest), 'final_manifest_sha256':sha(manifest), 'active_bundle':str(bundle)}), encoding='utf-8')
    args = ''
    if case != 'omitted':
        policy = 'invalid' if case == 'bad_hash' else H1
        path = {'trailing':'C:\\synthetic\\', 'root':'C:\\', 'whitespace':'   ',
                'forward_slash':POLICY.replace('\\', '/')}.get(case, POLICY)
        if case == 'whitespace': policy = ' '
        args = f" -MindOperatorPolicyPath {quoted(path)} -MindOperatorPolicySha256 '{policy}'"
        if case != 'partial': args += f" -MindOperatorApprovalReferenceSha256 '{' ' if case == 'whitespace' else H2}'"
    script = f"$WarningPreference='Stop'; & {quoted(str(wrapper))} -DryRun{args}"
    result = _run_powershell(script, executable=shell)
    data = json.loads(result.stdout.strip().splitlines()[-1])
    assert data['dry'] is True
    if case in ('complete', 'forward_slash'):
        assert (data['path'], data['policy'], data['approval']) == (POLICY, H1, H2)
    else:
        assert data['path'] == data['policy'] == data['approval'] == ''
        if case != 'omitted': assert 'Mind content is OFF' in result.stdout

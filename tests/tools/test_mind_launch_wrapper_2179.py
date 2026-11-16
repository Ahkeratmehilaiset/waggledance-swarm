"""Generated pinned launchers forward Mind activation arguments, without deploying."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import subprocess

import pytest


DEPLOYER = Path(__file__).resolve().parents[2] / 'ops/windows/reboot/Deploy-WdRebootBundle.ps1'
PWSH = shutil.which('pwsh')
WINDOWS_POWERSHELL = shutil.which('powershell.exe')
COMMIT = 'a' * 40
POLICY_HASH = 'b' * 64
APPROVAL_HASH = 'c' * 64

GENERATE = r'''
param([string]$Deployer,[string]$Target,[string]$EntryHash,[string]$ManifestHash,
      [string]$Commit,[string]$Out,[string]$Kind,[string]$FixedAgent)
$ErrorActionPreference='Stop'
$tokens=$null;$errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile($Deployer,[ref]$tokens,[ref]$errors)
if ($errors.Count) {throw 'parse'}
$fn=$ast.Find({param($node)$node -is [Management.Automation.Language.FunctionDefinitionAst] -and
  $node.Name -ceq 'New-ForwardingWrapper'},$true)
if ($null -eq $fn) {throw 'missing'}
. ([scriptblock]::Create($fn.Extent.Text))
$wrapper=New-ForwardingWrapper -Target $Target -ExpectedHash $EntryHash -ExpectedManifestHash $ManifestHash `
  -WrapperKind $Kind -FixedAgent $FixedAgent -ExpectedFinalCommit $Commit -ExpectedFinalManifestHash $ManifestHash
[IO.File]::WriteAllText($Out,$wrapper,(New-Object Text.UTF8Encoding $false))
'''

TARGET = r'''
param([string]$Agent='', [string]$Generation='', [string]$MindOperatorPolicyPath='',
      [string]$MindOperatorPolicySha256='', [string]$MindOperatorApprovalReferenceSha256='')
[ordered]@{agent=$Agent;generation=$Generation;path=$MindOperatorPolicyPath;
  policy=$MindOperatorPolicySha256;approval=$MindOperatorApprovalReferenceSha256;
  anchor=$env:WD_REBOOT_EXPECTED_MANIFEST_HASH}|ConvertTo-Json -Compress
'''


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


@pytest.fixture(params=[pytest.param(PWSH, id='pwsh7'), pytest.param(WINDOWS_POWERSHELL, id='powershell51')])
def shell(request):
    if request.param is None:
        pytest.skip('requested PowerShell version unavailable')
    return request.param


@pytest.fixture(params=['agent', 'fixed', 'tools'])
def generated_launcher(tmp_path, request, shell):
    bundle = tmp_path / 'bundles' / COMMIT
    machine = tmp_path / 'machine'
    bundle.mkdir(parents=True)
    machine.mkdir()
    target = bundle / 'target.ps1'
    target.write_text(TARGET, encoding='utf-8')
    manifest = bundle / 'deployment-manifest.json'
    manifest.write_text('{"schema_version":1}', encoding='ascii')
    wrapper = machine / 'launcher.ps1'
    generator = tmp_path / 'generate.ps1'
    generator.write_text(GENERATE, encoding='utf-8')
    kind = 'tools' if request.param == 'tools' else 'agent'
    fixed = 'fable-5' if request.param == 'fixed' else ''
    generated = subprocess.run([shell, '-NoProfile', '-NonInteractive', '-File', str(generator),
        '-Deployer', str(DEPLOYER), '-Target', str(target), '-EntryHash', _sha(target),
        '-ManifestHash', _sha(manifest), '-Commit', COMMIT, '-Out', str(wrapper),
        '-Kind', kind, '-FixedAgent', fixed], capture_output=True, text=True, timeout=30)
    assert generated.returncode == 0, generated.stderr
    pointer = {'source_commit':COMMIT, 'final_commit':COMMIT, 'manifest_sha256':_sha(manifest),
               'final_manifest_sha256':_sha(manifest), 'active_bundle':str(bundle)}
    state = machine / 'WD_REBOOT_STATE_CURRENT.json'
    state.write_text(json.dumps(pointer), encoding='utf-8')
    return request.param, wrapper, state, pointer, _sha(manifest), tmp_path, shell


def _invoke(case, *, with_policy, stale=False):
    kind, wrapper, state, pointer, anchor, root, shell = case
    if stale:
        state.write_text(json.dumps(dict(pointer, source_commit='d' * 40)), encoding='utf-8')
    policy_path = str(root / 'synthetic-policy.json')
    args = []
    if kind == 'agent':
        args += ['-Agent', 'claude-rco-1']
    if kind == 'tools':
        args += ['-Generation', COMMIT]
    if with_policy:
        args += ['-MindOperatorPolicyPath', policy_path, '-MindOperatorPolicySha256', POLICY_HASH,
                 '-MindOperatorApprovalReferenceSha256', APPROVAL_HASH]
    proc = subprocess.run([shell, '-NoProfile', '-NonInteractive', '-File', str(wrapper), *args],
                          capture_output=True, text=True, timeout=30)
    return proc, policy_path, anchor


def test_pinned_wrapper_forwards_explicit_mind_arguments(generated_launcher):
    proc, path, anchor = _invoke(generated_launcher, with_policy=True)
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout.lstrip('\ufeff'))
    assert data['path'] == path and data['policy'] == POLICY_HASH and data['approval'] == APPROVAL_HASH
    assert data['anchor'] == anchor
    assert data['agent'] == {'agent':'claude-rco-1', 'fixed':'fable-5', 'tools':''}[generated_launcher[0]]
    assert data['generation'] == (COMMIT if generated_launcher[0] == 'tools' else '')


def test_pinned_wrapper_omission_keeps_mind_off(generated_launcher):
    proc, _, _ = _invoke(generated_launcher, with_policy=False)
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout.lstrip('\ufeff'))
    assert data['path'] == data['policy'] == data['approval'] == ''


def test_pinned_wrapper_rejects_stale_pointer_before_target(generated_launcher):
    proc, _, _ = _invoke(generated_launcher, with_policy=True, stale=True)
    assert proc.returncode != 0
    assert 'does not match this launcher' in proc.stderr
    assert proc.stdout == ''

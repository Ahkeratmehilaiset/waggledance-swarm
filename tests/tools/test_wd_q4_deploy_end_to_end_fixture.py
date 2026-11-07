"""Synthetic pointer, resolver, extracted deploy transaction and guards.

The transaction is the existing AST-extracted fixture, with test-only wrapper
generation and bounded fault injection. It is not the full installer, a real
bundle, a crash/power-loss test or a production activation.
"""
import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess

import pytest

from test_wd_q4_default_descriptor_bootstrap import staged, canon, sha  # noqa: F401
from test_wd_startup_recovery import load, q
import test_wd_q4_deploy_transaction_rollback as rollback
from tools import wd_q4_descriptor_pointer as pointer
from tools import wd_q4_guard_state_provision_cli as guards
from tools.wd_q4_lane_binding import _roster

ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / 'ops/windows/reboot/Get-WdQ4DefaultDescriptor.ps1'
COMMIT = 'b' * 40
LANES = ('codex-lead-1', 'codex-tools-1', 'claude-rco-1', 'claude-rco-2', 'fable-5')
pytestmark = pytest.mark.skipif(os.name != 'nt' or not rollback.SHELLS, reason='Windows required')


def _synthetic_tuple(item, tmp_path):
    bundle = item['bundle']
    # All paths remain under the pytest audit root; no real fleet is read.
    runtime = tmp_path / 'bridge'
    (runtime / 'shared' / '.codex-audit').mkdir(parents=True)
    fleet = json.loads((bundle / 'wd-fleet.json').read_bytes())
    for i, lane in enumerate(fleet['lanes'] + [fleet['tools_supervisor']], 1):
        lane['agent_uuid'] = f'00000000-0000-0000-0000-{i:012d}'
    fleet['runtime_root'] = str(runtime)
    fleet_raw = canon(fleet)
    (bundle / 'wd-fleet.json').write_bytes(fleet_raw)
    (bundle / 'Invoke-WdQ4Query.ps1').write_bytes(b'synthetic-query-wrapper')
    (bundle / 'start-wd-agent.ps1').write_bytes(b'# synthetic agent target')
    (bundle / rollback.DATA).write_bytes(b'synthetic-new-data\n')
    manifest = json.loads(item['manifest'].read_bytes())
    manifest['source_commit'] = COMMIT
    for name in ('wd-fleet.json', 'Invoke-WdQ4Query.ps1', 'start-wd-agent.ps1', rollback.DATA):
        manifest['files'][name] = sha((bundle / name).read_bytes())
    manifest_raw = canon(manifest)
    item['manifest'].write_bytes(manifest_raw)
    manifest_sha = sha(manifest_raw)
    lanes, roster = _roster(fleet_raw, sha(fleet_raw))
    binding = item['binding_doc']
    binding['binding']['roster_sha256'] = roster
    binding.update(lanes=sorted(lanes), manifest_sha256=manifest_sha,
                   wrapper_sha256=sha(b'synthetic-query-wrapper'))
    binding_raw = canon(binding)
    item['binding'].write_bytes(binding_raw)
    desc = item['descriptor_doc']
    desc.update(fleet_sha256=sha(fleet_raw), binding_sha256=sha(binding_raw),
                fixed_plan_path=str(item['descriptor'].parent / 'fixed-plan.v2.json'),
                snapshot_path=str(item['descriptor'].parent / 'public-snapshot.v1.json'),
                expected_binding=binding['binding'])
    desc_raw = canon(desc)
    item['descriptor'].write_bytes(desc_raw)
    prepared = pointer.prepare_staged_descriptor_pointer(enabled=True, expected_bundle=str(bundle),
        expected_commit=COMMIT, expected_manifest_sha256=manifest_sha.upper(),
        generations_root=str(item['descriptor'].parent.parent), generation=COMMIT,
        descriptor_sha256=sha(desc_raw))
    assert prepared['status'] == 'PREPARED'
    pin_path = tmp_path / 'staged-pointer.json'
    pin_path.write_bytes(prepared['pointer_bytes'])
    return runtime, manifest_sha, pin_path, prepared['pointer_sha256']


def _resolve(item, manifest_sha, pin_path, pointer_sha, shell):
    deployer = ROOT / 'ops/windows/reboot/Deploy-WdRebootBundle.ps1'
    functions = ('Resolve-FullPath', 'Assert-WdPathWithoutReparse', 'Read-Utf8DeploymentSnapshot',
                 'Get-WdPinnedQ4InstallDefault')
    script = '\n'.join(load(deployer, name) for name in functions)
    script += load(HELPER, 'Get-WdQ4DefaultDescriptor') + f'''
$r=Get-WdQ4DefaultDescriptor -PointerPath {q(pin_path)} -ExpectedPointerSha256 {q(pointer_sha)} `
  -ActiveBundle {q(item['bundle'])} -ExpectedManifestHash {q(manifest_sha)} -ExpectedCommit {COMMIT}
$installed=$false
if ($r.State -ceq 'READY') {{
 $v=Get-WdPinnedQ4InstallDefault -DescriptorPath $r.DescriptorPath -DescriptorSha256 $r.DescriptorSha256 `
   -BundleRoot {q(item['bundle'])} -ExpectedManifestHash {q(manifest_sha)} -ExpectedCommit {COMMIT}
 $installed=($v.DescriptorPath -ceq $r.DescriptorPath -and $v.DescriptorSha256 -ceq $r.DescriptorSha256)
}}
@{{state=$r.State;path=$r.DescriptorPath;hash=$r.DescriptorSha256;install_precheck=$installed}}|ConvertTo-Json -Compress
'''
    result = subprocess.run([shell, '-NoProfile', '-NonInteractive', '-Command', script],
                            capture_output=True, text=True, timeout=30, check=True)
    return json.loads(result.stdout)


def _transaction(monkeypatch, fixture, pointer_bytes, manifest_sha, shell, fault):
    # Adapt only the existing synthetic harness's fixed pointer/commit inputs.
    # Its path guards, production AST extraction and test-only wrapper stay intact.
    original_run = subprocess.run
    monkeypatch.setattr(rollback, 'Q4_CONTENT', pointer_bytes)
    def run(args, **kwargs):
        assert '-EncodedCommand' in args
        index = args.index('-EncodedCommand') + 1
        text = base64.b64decode(args[index]).decode('utf-16-le')
        before = "$installedManifestHash=('C'*64);$ExpectedFinalManifestHash=('C'*64)"
        after = f"$installedManifestHash='{manifest_sha.upper()}';$ExpectedFinalManifestHash='{manifest_sha.upper()}'"
        assert text.count(before) == 1
        text = text.replace(before, after)
        before = "$ExpectedFinalCommit=('d'*40);$head=('d'*40)"
        assert text.count(before) == 1
        text = text.replace(before, f"$ExpectedFinalCommit='{COMMIT}';$head='{COMMIT}'")
        if fault == 'no-fault':
            before = "if (-not $caught) { throw 'expected injected transaction failure did not occur' }"
            assert text.count(before) == 1
            text = text.replace(before, "if (-not $caught) { $caught='completed' }")
        args = list(args)
        args[index] = base64.b64encode(text.encode('utf-16-le')).decode('ascii')
        return original_run(args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(rollback.subprocess, 'run', run)
        return rollback.invoke_transaction(fixture, shell=shell, fail_leaf=fault)


@pytest.mark.parametrize('fault', ['no-fault', rollback.POINTER, rollback.STATE])
@pytest.mark.parametrize('shell', rollback.SHELLS, ids=lambda path: Path(path).name)
def test_real_pointer_resolver_extracted_transaction_and_guard(staged, tmp_path, monkeypatch, shell, fault):
    runtime, manifest_sha, pin_path, pin = _synthetic_tuple(staged, tmp_path)
    pointer_bytes = pin_path.read_bytes()
    assert _resolve(staged, manifest_sha, pin_path, pin, shell) == {
        'state':'READY', 'path':str(staged['descriptor']), 'hash':sha(staged['descriptor'].read_bytes()),
        'install_precheck':True}
    assert _resolve(staged, manifest_sha, pin_path, '0'*64, shell)['state'] == 'UNAVAILABLE'
    tx_root = tmp_path / 'transaction'
    tx_root.mkdir()
    fixture = rollback.make_fixture(tx_root, pointer_exists=True, q4_enabled=True)
    fixture['target'] = staged['bundle']
    fixture['manifest'] = staged['manifest']
    # Existing fixture requires both machine and target to be under its root.
    fixture['root'] = tmp_path
    result = _transaction(monkeypatch, fixture, pointer_bytes, manifest_sha, shell, fault)
    assert result['pointer_in_transaction'] is True
    if fault != 'no-fault':
        assert result['error'] == 'synthetic post-write failure'
        assert rollback.top_files(fixture['machine']) == fixture['before']
        assert not any((runtime / 'shared' / '.codex-audit').iterdir())
    else:
        assert result['error'] == 'completed'
        assert (fixture['machine'] / rollback.POINTER).read_bytes() == pointer_bytes
        state = json.loads((fixture['machine'] / rollback.STATE).read_bytes())
        assert state['active_bundle'] == str(staged['bundle'])
        assert state['manifest_sha256'] == manifest_sha.upper()
        assert state['q4_default_pointer_sha256'] == pin
        assert pin.upper() in (fixture['machine'] / rollback.INTEGRITY).read_text()
        monkeypatch.setattr(guards, '_STATE_POINTER', fixture['machine'] / rollback.STATE)
        assert guards.provision_anchored(enabled=True, bundle_root=str(staged['bundle']),
                                          manifest_sha256=manifest_sha.upper()) == {'status':'READY'}
        assert all((runtime/'shared'/'.codex-audit'/lane/'q4-caller-state').is_dir() for lane in LANES)
    assert fixture['sentinel'].read_bytes() == b'sentinel-outside-transaction\x00\xff'

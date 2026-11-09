"""Synthetic, read-only readiness checks; no live bridge or model query."""
import hashlib
import importlib
import json
from pathlib import Path

import pytest

from tests.tools.test_wd_q4_staged_fixed_backend import staged, staged_runtime  # noqa: F401 - fixtures

LANES = ('codex-lead-1', 'codex-tools-1', 'claude-rco-1', 'claude-rco-2', 'fable-5')


def module():
    return importlib.import_module('tools.wd_q4_reboot_readiness')


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def test_default_off_does_not_access_any_inputs():
    class Untouchable:
        def __getattribute__(self, _): raise AssertionError('OFF accessed input')
    assert module().inspect(enabled=False, descriptor_path=Untouchable(), manifest_sha256=Untouchable()) == {'status':'OFF'}


@pytest.fixture
def guards(tmp_path):
    runtime = tmp_path/'bridge'; paths = {}
    for lane in LANES:
        path = runtime/'shared'/'.codex-audit'/lane/'q4-caller-state'; path.mkdir(parents=True)
        paths[lane] = path
    raw = json.dumps({'runtime_root':str(runtime)}).encode()
    return runtime, paths, raw, sha(raw)


def test_guard_observation_is_read_only_and_checks_all_five(guards):
    runtime, paths, fleet, pin = guards
    before = sorted(str(p.relative_to(runtime)) for p in runtime.rglob('*'))
    assert module()._inspect_guards(fleet, pin) == {lane:'CLEAR_AT_CHECK' for lane in LANES}
    assert sorted(str(p.relative_to(runtime)) for p in runtime.rglob('*')) == before


@pytest.mark.parametrize('lane', LANES)
def test_existing_pending_marker_is_reported_and_never_changed(guards, lane):
    _, paths, fleet, pin = guards
    marker = paths[lane]/(lane+'.pending.json'); marker.write_bytes(b'synthetic-unknown')
    result = module()._inspect_guards(fleet, pin)
    assert result[lane] == 'PENDING' and marker.read_bytes() == b'synthetic-unknown'
    assert all(result[other] == 'CLEAR_AT_CHECK' for other in LANES if other != lane)


@pytest.mark.parametrize('lane', LANES)
def test_missing_guard_directory_is_unavailable_not_clear(guards, lane):
    _, paths, fleet, pin = guards
    paths[lane].rmdir()
    result = module()._inspect_guards(fleet, pin)
    assert result[lane] == 'UNAVAILABLE' and not paths[lane].exists()


def test_wrong_fleet_pin_does_not_mark_any_lane_clear(guards):
    _, _, fleet, _ = guards
    assert set(module()._inspect_guards(fleet, '0'*64).values()) == {'UNAVAILABLE'}


@pytest.fixture
def real_tiny_runtime(staged_runtime):
    from sandbox.mind.step2 import runtime_builder
    root, bundle_peer, _, plan, runtime_peer, runtime_helper, manifest = staged_runtime
    runtime = Path(plan['runtime_root'])
    deployment_path = root/'deployment-manifest.json'
    deployment = json.loads(deployment_path.read_bytes())
    for name in module()._REQUIRED_CODE:
        path = runtime/'assets'/'code'/name
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(('synthetic '+name).encode())
        if name not in module()._RUNTIME_ONLY:
            bundled = root/'tools-bootstrap'/name
            bundled.parent.mkdir(parents=True, exist_ok=True)
            bundled.write_bytes(path.read_bytes())
            deployment['files']['tools-bootstrap/'+name] = sha(path.read_bytes())
    for name in ('site/synthetic.py','model/synthetic.bin'):
        path=runtime/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'synthetic')
    deployment_raw=json.dumps(deployment,sort_keys=True,separators=(',',':')).encode()
    deployment_path.write_bytes(deployment_raw);plan['deployment_manifest_sha256']=sha(deployment_raw)
    manifest.unlink()
    result = runtime_builder.build_runtime_manifest(runtime, manifest,
        generation=plan['runtime_generation'], revision=plan['runtime_revision'])
    plan['runtime_manifest_sha256'] = result['manifest_sha256']
    return plan, runtime_peer, runtime_helper, manifest


def test_runtime_check_uses_real_manifest_and_peer_binding(real_tiny_runtime):
    plan, _, _, manifest = real_tiny_runtime
    module()._check_runtime(plan, manifest.read_bytes())


@pytest.mark.parametrize('part', ['peer','helper','manifest','required_dynamic','bundled_caller'])
def test_runtime_check_rejects_changed_files(real_tiny_runtime, part):
    plan, peer, helper, manifest = real_tiny_runtime
    path = {'peer':peer, 'helper':helper, 'manifest':manifest,
            'required_dynamic':Path(plan['runtime_root'])/'assets/code/sandbox/mind/step2/q4_controlled_imports.py',
            'bundled_caller':Path(plan['bundle_root'])/'tools-bootstrap/tools/wd_q4_lane_caller_cli.py'}[part]
    path.write_bytes(path.read_bytes()+b'changed')
    with pytest.raises(ValueError):
        module()._check_runtime(plan, manifest.read_bytes())


@pytest.fixture
def composed_readiness(real_tiny_runtime, guards, monkeypatch):
    """Real runtime and guard checks; caller/binding parsing is a controlled seam."""
    from tools import wd_q4_guard_completion, wd_q4_lane_binding, wd_q4_lane_caller_cli, wd_q4_secure_transport
    plan, _, _, runtime_manifest = real_tiny_runtime
    _, _, fleet, fleet_pin = guards
    host = guards[0]/'synthetic-powershell.exe'
    host.write_bytes(b'synthetic-host')
    plan['host_sha256'] = sha(host.read_bytes())
    monkeypatch.setattr(wd_q4_secure_transport, '_staged_system_host', lambda: str(host))
    manifest_pin = plan['deployment_manifest_sha256']
    plan.update(backend='staged-card-wrapper-v1', bundle_commit='a'*40,
                bundle_bytes=len(b'synthetic-card-bundle'), bundle_sha256=sha(b'synthetic-card-bundle'),
                interpreter_sha256='d'*64)
    descriptor = {'lanes':list(LANES), 'bundle_root':plan['bundle_root'],
        'runtime_manifest_sha256':plan['runtime_manifest_sha256'],
        'runtime_manifest_path':plan['runtime_manifest_path'], 'interpreter_sha256':'d'*64,
        'outer':{'runtime-root':plan['runtime_root'], 'runtime-generation':plan['runtime_generation'],
                 'runtime-revision':plan['runtime_revision']}}
    pins = {'synthetic':'pin'}
    inputs = {'expected_fleet_sha256':fleet_pin, 'binding_bytes':b'synthetic-binding',
              'expected_binding_sha256':sha(b'synthetic-binding'), 'fixed_plan_bytes':b'synthetic-plan',
              'runtime_manifest_bytes':runtime_manifest.read_bytes(), 'fixed_bundle_bytes':b'synthetic-card-bundle'}
    config = (descriptor,b'synthetic-descriptor',sha(b'synthetic-descriptor'),fleet,pins,inputs)
    calls=[]
    monkeypatch.setenv('WD_REBOOT_EXPECTED_MANIFEST_HASH',manifest_pin)
    monkeypatch.setattr(wd_q4_lane_caller_cli, '_configuration', lambda *_: (calls.append('configuration') or config))
    monkeypatch.setattr(wd_q4_lane_caller_cli, '_require_current_snapshot', lambda *_: calls.append('snapshot'))
    monkeypatch.setattr(wd_q4_secure_transport, '_fixed_plan', lambda _:plan)
    monkeypatch.setattr(wd_q4_lane_binding, 'resolve_lane_binding', lambda *_a, **kw:
        (calls.append(kw['lane']) or {'expected_commit':'a'*40,'expected_manifest_sha256':manifest_pin,
                                     'expected_binding':pins}))
    monkeypatch.setattr(wd_q4_guard_completion, 'acquire_guard', lambda **_:pytest.fail('guard acquisition'))
    args = {'enabled':True,'descriptor_path':'synthetic','descriptor_sha256':'c'*64,
            'manifest_sha256':manifest_pin,'expected_commit':'a'*40}
    return args, calls, guards, plan


def test_composed_inspection_rechecks_all_five_without_acquiring(composed_readiness):
    args, calls, guards, _ = composed_readiness
    runtime = guards[0]
    before = sorted(str(p.relative_to(runtime)) for p in runtime.rglob('*'))
    result = module().inspect(**args)
    assert result['status'] == 'PRECHECK_PASSED' and result['next_turn'] == 'UNKNOWN'
    assert result['source_authentication'] == 'UNKNOWN'
    assert result['card_snapshot'] == 'NOT_CHECKED'
    assert result['guards'] == {lane:'CLEAR_AT_CHECK' for lane in LANES}
    assert calls.count('configuration') == 2 and calls.count('snapshot') == 2
    assert all(calls.count(lane) == 1 for lane in LANES)
    assert sorted(str(p.relative_to(runtime)) for p in runtime.rglob('*')) == before


def test_composed_changed_host_fails_closed(composed_readiness):
    args, _, guards, _ = composed_readiness
    (guards[0]/'synthetic-powershell.exe').write_bytes(b'changed-host')
    assert module().inspect(**args) == {'status':'UNAVAILABLE'}


def test_composed_host_changed_during_inspection_fails_closed(composed_readiness, monkeypatch):
    args, _, guards, _ = composed_readiness
    target = module(); original = target._inspect_guards
    def changed(*values):
        observed = original(*values)
        (guards[0]/'synthetic-powershell.exe').write_bytes(b'changed-during-inspection')
        return observed
    monkeypatch.setattr(target, '_inspect_guards', changed)
    assert target.inspect(**args) == {'status':'UNAVAILABLE'}


def test_composed_pending_is_unavailable_without_deleting_it(composed_readiness):
    args, _, guards, _ = composed_readiness
    lane = LANES[0]; marker = guards[1][lane]/(lane+'.pending.json');marker.write_bytes(b'synthetic-pending')
    result = module().inspect(**args)
    assert result['status'] == 'UNAVAILABLE' and result['guards'][lane] == 'PENDING'
    assert marker.read_bytes() == b'synthetic-pending'


def test_composed_guard_change_during_inspection_is_unknown(composed_readiness, monkeypatch):
    args, _, guards, _ = composed_readiness
    target = module(); original = target._inspect_guards; calls=[]
    lane=LANES[0]; marker=guards[1][lane]/(lane+'.pending.json')
    def changed(*values):
        observed=original(*values); calls.append(True)
        if len(calls)==1: marker.write_bytes(b'synthetic-race')
        return observed
    monkeypatch.setattr(target,'_inspect_guards',changed)
    assert target.inspect(**args) == {'status':'UNKNOWN'}
    assert marker.read_bytes() == b'synthetic-race'


@pytest.mark.parametrize('field', ['bundle_sha256', 'bundle_bytes', 'interpreter_sha256'])
def test_composed_mismatched_plan_pins_fail_closed(composed_readiness, field):
    args, _, _, plan = composed_readiness
    plan[field] = 1 if field == 'bundle_bytes' else '0'*64
    assert module().inspect(**args) == {'status':'UNAVAILABLE'}


@pytest.mark.parametrize('error,status', [(ValueError(),'UNAVAILABLE'),(TimeoutError(),'UNKNOWN'),
                                          (KeyboardInterrupt(),'UNKNOWN')])
def test_composed_failures_are_closed_without_details(composed_readiness, monkeypatch, error, status):
    from tools import wd_q4_lane_caller_cli as caller
    args, _, _, _ = composed_readiness
    def refuse(*_): raise error
    monkeypatch.setattr(caller,'_configuration',refuse)
    assert module().inspect(**args) == {'status':status}

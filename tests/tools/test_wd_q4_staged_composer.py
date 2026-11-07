"""Synthetic only: stage binding, session descriptor and pointer without activation."""
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path
import pytest
from tools import wd_q4_lane_binding_producer as bp
from tests.tools.test_wd_q4_lane_binding_producer import case as binding_case
from tests.tools.test_wd_q4_descriptor_cli import synthetic, write_request


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def setup_case(tmp_path):
    source = binding_case.__wrapped__()
    store = tmp_path / 'reboot-bundles'
    store.mkdir()
    bundle = store / ('a' * 40)
    bundle.mkdir()
    active = store / ('f' * 40)
    active.mkdir()
    state = tmp_path / 'WD_REBOOT_STATE_CURRENT.json'
    state_bytes = json.dumps({'schema_version': 1, 'active_bundle': str(active),
        'source_commit': 'f' * 40, 'final_commit': 'f' * 40,
        'manifest_sha256': 'D' * 64, 'final_manifest_sha256': 'D' * 64}).encode('ascii')
    state.write_bytes(state_bytes)
    from tools import wd_q4_staged_composer as staged
    staged._STATE_POINTER = state  # synthetic test-only hook, never a CLI argument
    (bundle / 'deployment-manifest.json').write_bytes(source['manifest_bytes'])
    (bundle / 'wd-fleet.json').write_bytes(source['fleet_bytes'])
    (bundle / 'Invoke-WdQ4Query.ps1').write_bytes(source['wrapper_bytes'])
    fixture_dir = tmp_path / 'fixture'
    fixture_dir.mkdir()
    request, _, _ = synthetic(fixture_dir)
    root = tmp_path / 'permanent-generations'
    root.mkdir()
    stage = root / 'stage'
    stage.mkdir()
    public_plan = tmp_path / 'public-plan.json'
    public_plan.write_bytes(source['public_plan_bytes'])
    snapshot = tmp_path / 'snapshot.json'
    snapshot.write_bytes(source['snapshot_bytes'])
    binding = bp.build_lane_binding(**source)
    expected = json.loads(binding['binding_bytes'])['binding']
    request.update(target_dir=str(stage), fleet_path=str(bundle / 'wd-fleet.json'),
        fleet_sha256=source['expected_fleet_sha256'], binding_path=str(stage / 'binding-source.json'),
        binding_sha256='0'*64, snapshot_path=str(snapshot), snapshot_sha256=source['expected_snapshot_sha256'])
    request['descriptor'].update(bundle_root=str(bundle), public_plan=str(public_plan), expected_binding=expected)
    request['fixed_plan'].update(bundle_sha256=source['expected_snapshot_sha256'], bundle_bytes=len(source['snapshot_bytes']),
        expected_pointer_sha256=expected['pointer_sha256'], policy_sha256=expected['policy_sha256'],
        roster_sha256=expected['roster_sha256'], expected_public_policy_sha256=expected['public_policy_sha256'],
        expected_source_sha256=expected['source_anchor_sha256'], expected_encoder_sha256=expected['encoder_sha256'],
        approved_tasks=['private-task', 'public-task'])
    request_path = write_request(fixture_dir, request)
    return dict(enabled=True, request_path=str(request_path), stage_dir=str(stage), expected_bundle=str(bundle),
        expected_commit='a'*40, expected_manifest_sha256=source['expected_manifest_sha256'].upper(),
        generation_root=str(root), bundle_store_root=str(store), active_bundle_root=str(active),
        expected_state_sha256=sha(state_bytes),
        expected_public_plan_sha256=source['expected_public_plan_sha256']), source, stage


def test_default_off_does_not_touch_input():
    from tools import wd_q4_staged_composer as s
    assert s.compose() == {'status': 'OFF'}


def test_staged_composition_is_hash_bound_and_never_activates(tmp_path):
    from tools import wd_q4_staged_composer as s
    kw, source, stage = setup_case(tmp_path)
    result = s.compose(**kw)
    assert result['status'] == 'PREPARED'
    pointer = Path(result['pointer_path']).read_bytes()
    descriptor = Path(result['descriptor_path']).read_bytes()
    assert sha(pointer) == result['pointer_sha256']
    assert sha(descriptor) == result['descriptor_sha256']
    assert sha((stage / 'binding-source.json').read_bytes()) == result['binding_sha256']
    assert json.loads(pointer)['descriptor_sha256'] == sha(descriptor)
    assert len(list(tmp_path.rglob('WD_REBOOT_STATE_CURRENT.json'))) == 1  # synthetic source only
    assert not list(tmp_path.rglob('WD_Q4_DESCRIPTOR_CURRENT.json'))


def test_refuses_changed_manifest_before_stage_write(tmp_path):
    from tools import wd_q4_staged_composer as s
    kw, _, stage = setup_case(tmp_path)
    kw['expected_manifest_sha256'] = 'F'*64
    with pytest.raises(s.ComposeRefused): s.compose(**kw)
    assert not list(stage.iterdir())


def test_refuses_nonempty_stage_without_overwrite(tmp_path):
    from tools import wd_q4_staged_composer as s
    kw, _, stage = setup_case(tmp_path)
    (stage / 'existing').write_bytes(b'keep')
    with pytest.raises(s.ComposeRefused): s.compose(**kw)
    assert (stage / 'existing').read_bytes() == b'keep'


def test_refuses_public_plan_pin_mismatch(tmp_path):
    from tools import wd_q4_staged_composer as s
    kw, _, stage = setup_case(tmp_path)
    kw['expected_public_plan_sha256'] = 'f'*64
    with pytest.raises(s.ComposeRefused): s.compose(**kw)
    assert not list(stage.iterdir())


def test_refuses_approved_tasks_that_do_not_match_snapshot_policy(tmp_path):
    from tools import wd_q4_staged_composer as s
    kw, _, stage = setup_case(tmp_path)
    path = Path(kw['request_path'])
    req = json.loads(path.read_text())
    req['fixed_plan']['approved_tasks'] = ['task-a']
    path.write_text(json.dumps(req), encoding='ascii')
    with pytest.raises(s.ComposeRefused): s.compose(**kw)
    assert not list(stage.iterdir())


def test_failure_after_write_is_incomplete_and_never_retried(tmp_path, monkeypatch):
    from tools import wd_q4_staged_composer as s
    kw, _, stage = setup_case(tmp_path)
    def interrupted(_):
        raise ValueError('do not expose input')
    monkeypatch.setattr(s.cli, '_compose', interrupted)
    with pytest.raises(s.ComposeIncomplete) as err: s.compose(**kw)
    assert str(err.value) == 'staging_incomplete'
    assert err.value.__context__ is None
    assert (stage / 'binding-source.json').is_file()
    with pytest.raises(s.ComposeRefused): s.compose(**kw)


def test_refuses_stage_inside_bundle_before_any_write(tmp_path):
    from tools import wd_q4_staged_composer as s
    kw, _, _ = setup_case(tmp_path)
    stage = Path(kw['expected_bundle']) / 'stage'
    stage.mkdir()
    kw['stage_dir'] = str(stage)
    with pytest.raises(s.ComposeRefused): s.compose(**kw)
    assert not list(stage.iterdir())


def test_interruption_after_binding_has_no_receipt_and_no_automatic_retry(tmp_path, monkeypatch):
    from tools import wd_q4_staged_composer as s
    kw, _, stage = setup_case(tmp_path)
    def interrupted(_):
        raise KeyboardInterrupt()
    monkeypatch.setattr(s.cli, '_compose', interrupted)
    with pytest.raises(KeyboardInterrupt): s.compose(**kw)
    assert (stage / 'binding-source.json').is_file()
    assert not (stage / 'candidate-pointer.json').exists()
    with pytest.raises(s.ComposeRefused): s.compose(**kw)


def test_rejects_target_directory_mismatch_before_write(tmp_path):
    from tools import wd_q4_staged_composer as s
    kw, _, stage = setup_case(tmp_path)
    path = Path(kw['request_path'])
    req = json.loads(path.read_text())
    req['target_dir'] = str(tmp_path / 'outside')
    path.write_text(json.dumps(req), encoding='ascii')
    with pytest.raises(s.ComposeRefused): s.compose(**kw)
    assert not list(stage.iterdir())


def test_copied_generation_cannot_be_activated_under_different_root(tmp_path):
    from tools import wd_q4_staged_composer as s
    from tools import wd_q4_descriptor_pointer as pointer
    kw, _, stage = setup_case(tmp_path)
    result = s.compose(**kw)
    copied = tmp_path / 'different-permanent-root'
    shutil.copytree(stage, copied)
    generation = Path(result['descriptor_path']).parent.name
    with pytest.raises(pointer.PointerRefused):
        pointer.prepare_staged_descriptor_pointer(enabled=True, expected_bundle=kw['expected_bundle'],
            expected_commit=kw['expected_commit'], expected_manifest_sha256=kw['expected_manifest_sha256'],
            generations_root=str(copied), generation=generation, descriptor_sha256=result['descriptor_sha256'])


@pytest.mark.parametrize('missing', ['generation_root', 'bundle_store_root', 'active_bundle_root'])
def test_missing_external_root_is_refused_before_any_write(tmp_path, missing):
    from tools import wd_q4_staged_composer as s
    kw, _, stage = setup_case(tmp_path)
    del kw[missing]
    with pytest.raises(s.ComposeRefused): s.compose(**kw)
    assert not list(stage.iterdir())


@pytest.mark.parametrize('inside', ['active', 'other_bundle'])
def test_refuses_stage_in_any_bundle_store_before_write(tmp_path, inside):
    from tools import wd_q4_staged_composer as s
    kw, _, _ = setup_case(tmp_path)
    outer = Path(kw['active_bundle_root']) if inside == 'active' else Path(kw['bundle_store_root']) / 'third-bundle'
    root = outer / 'generation-root'
    stage = root / 'stage'
    stage.mkdir(parents=True)
    kw.update(generation_root=str(root), stage_dir=str(stage))
    path = Path(kw['request_path'])
    request = json.loads(path.read_text())
    request.update(target_dir=str(stage), binding_path=str(stage / 'binding-source.json'))
    path.write_text(json.dumps(request), encoding='ascii')
    with pytest.raises(s.ComposeRefused): s.compose(**kw)
    assert not list(stage.iterdir())


def test_refuses_generation_root_that_contains_bundle_store(tmp_path):
    from tools import wd_q4_staged_composer as s
    kw, _, stage = setup_case(tmp_path)
    kw['generation_root'] = str(tmp_path)
    with pytest.raises(s.ComposeRefused): s.compose(**kw)
    assert not list(stage.iterdir())


def test_refuses_stage_outside_authorized_generation_root(tmp_path):
    from tools import wd_q4_staged_composer as s
    kw, _, _ = setup_case(tmp_path)
    stage = tmp_path / 'elsewhere'
    stage.mkdir()
    kw['stage_dir'] = str(stage)
    with pytest.raises(s.ComposeRefused): s.compose(**kw)
    assert not list(stage.iterdir())


def test_refuses_stage_inside_active_bundle_outside_bundle_store(tmp_path):
    from tools import wd_q4_staged_composer as s
    kw, _, _ = setup_case(tmp_path)
    active = tmp_path / 'other-volume-active-bundle'
    stage = active / 'root' / 'stage'
    stage.mkdir(parents=True)
    kw.update(active_bundle_root=str(active), generation_root=str(stage.parent), stage_dir=str(stage))
    path = Path(kw['request_path'])
    request = json.loads(path.read_text())
    request.update(target_dir=str(stage), binding_path=str(stage / 'binding-source.json'))
    path.write_text(json.dumps(request), encoding='ascii')
    with pytest.raises(s.ComposeRefused): s.compose(**kw)
    assert not list(stage.iterdir())


def test_refuses_active_root_different_from_pinned_current(tmp_path):
    from tools import wd_q4_staged_composer as s
    kw, _, stage = setup_case(tmp_path)
    wrong = Path(kw['bundle_store_root']) / ('e' * 40)
    wrong.mkdir()
    kw['active_bundle_root'] = str(wrong)
    with pytest.raises(s.ComposeRefused): s.compose(**kw)
    assert not list(stage.iterdir())


def test_refuses_forged_current_digest_before_write(tmp_path):
    from tools import wd_q4_staged_composer as s
    kw, _, stage = setup_case(tmp_path)
    kw['expected_state_sha256'] = 'f' * 64
    with pytest.raises(s.ComposeRefused): s.compose(**kw)
    assert not list(stage.iterdir())


def test_refuses_pointer_changed_during_preparation(tmp_path, monkeypatch):
    from tools import wd_q4_staged_composer as s
    kw, _, stage = setup_case(tmp_path)
    real = s.binding_producer.build_lane_binding
    def change_current(**kwargs):
        result = real(**kwargs)
        (tmp_path / 'WD_REBOOT_STATE_CURRENT.json').write_bytes(b'changed')
        return result
    monkeypatch.setattr(s.binding_producer, 'build_lane_binding', change_current)
    with pytest.raises(s.ComposeRefused): s.compose(**kw)
    assert not list(stage.iterdir())


def _short_path(path):
    if os.name != 'nt': pytest.skip('Windows only')
    import ctypes
    fn = ctypes.windll.kernel32.GetShortPathNameW
    fn.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint]
    fn.restype = ctypes.c_uint
    size = fn(str(path), None, 0)
    if not size: pytest.skip('short path unavailable')
    buf = ctypes.create_unicode_buffer(size)
    if not fn(str(path), buf, size): pytest.skip('short path unavailable')
    if os.path.normcase(buf.value) == os.path.normcase(str(path)):
        pytest.skip('8.3 short names disabled or not assigned')
    return Path(buf.value)


def test_real_83_alias_of_bundle_store_cannot_hide_ancestor(tmp_path):
    from tools import wd_q4_staged_composer as s
    kw, _, _ = setup_case(tmp_path)
    short_store = _short_path(Path(kw['bundle_store_root']))
    active = Path(kw['active_bundle_root'])
    stage = active / 'generation-long-root' / 'stage'
    stage.mkdir(parents=True)
    kw.update(bundle_store_root=str(short_store), active_bundle_root=str(short_store / active.name),
              generation_root=str(stage.parent), stage_dir=str(stage))
    path = Path(kw['request_path'])
    request = json.loads(path.read_text())
    request.update(target_dir=str(stage), binding_path=str(stage / 'binding-source.json'))
    path.write_text(json.dumps(request), encoding='ascii')
    with pytest.raises(s.ComposeRefused): s.compose(**kw)
    assert not list(stage.iterdir())


def test_real_83_alias_of_current_bundle_is_accepted_by_identity(tmp_path):
    from tools import wd_q4_staged_composer as s
    kw, _, _ = setup_case(tmp_path)
    short_store = _short_path(Path(kw['bundle_store_root']))
    kw['active_bundle_root'] = str(short_store / Path(kw['active_bundle_root']).name)
    kw['bundle_store_root'] = str(short_store)
    assert s.compose(**kw)['status'] == 'PREPARED'


def test_active_bundle_junction_is_rejected_before_write(tmp_path):
    if os.name != 'nt': pytest.skip('Windows only')
    from tools import wd_q4_staged_composer as s
    kw, _, stage = setup_case(tmp_path)
    junction = tmp_path / 'synthetic-active-junction'
    command = subprocess.run(['cmd.exe', '/c', 'mklink', '/J', str(junction), kw['active_bundle_root']],
                             capture_output=True, text=True, timeout=10)
    if command.returncode != 0: pytest.skip('junction creation unavailable')
    assert os.path.samefile(junction, kw['active_bundle_root'])
    kw['active_bundle_root'] = str(junction)
    with pytest.raises(s.ComposeRefused): s.compose(**kw)
    assert not list(stage.iterdir())


def test_bundle_store_must_be_current_bundle_parent(tmp_path):
    from tools import wd_q4_staged_composer as s
    kw, _, stage = setup_case(tmp_path)
    wrong = tmp_path / 'unrelated-store'
    wrong.mkdir()
    kw['bundle_store_root'] = str(wrong)
    with pytest.raises(s.ComposeRefused): s.compose(**kw)
    assert not list(stage.iterdir())


def test_missing_current_pointer_refuses_before_write(tmp_path):
    from tools import wd_q4_staged_composer as s
    kw, _, stage = setup_case(tmp_path)
    (tmp_path / 'WD_REBOOT_STATE_CURRENT.json').unlink()
    with pytest.raises(s.ComposeRefused): s.compose(**kw)
    assert not list(stage.iterdir())


def test_case_colliding_current_keys_refused_even_when_hash_matches(tmp_path):
    from tools import wd_q4_staged_composer as s
    kw, _, stage = setup_case(tmp_path)
    state = tmp_path / 'WD_REBOOT_STATE_CURRENT.json'
    document = json.loads(state.read_text())
    document['ACTIVE_BUNDLE'] = document['active_bundle']
    data = json.dumps(document).encode('utf-8')
    state.write_bytes(data)
    kw['expected_state_sha256'] = sha(data)
    with pytest.raises(s.ComposeRefused): s.compose(**kw)
    assert not list(stage.iterdir())


# --- D1 shared generation root for compose_staged_card (opt-in shared_stage=True) -----------------------------
# One live generations root receives every run: earlier generations and their per-generation candidate pointers
# stay byte-identical, overlaps and foreign entries are refused before any write, and a failure or interruption
# after the first write is INCOMPLETE (unknown), never a clean refusal.

from tests.tools.test_wd_q4_staged_card_composer import materialize  # noqa: E402,F401 - pytest fixture
from tests.tools.test_wd_q4_staged_lane_query import staged_producer  # noqa: E402,F401 - pytest fixture

_GEN_B, _GEN_C = 'b' * 40, 'c' * 40


@pytest.fixture
def shared_case(materialize, monkeypatch):
    """The materialize fixture predates the af098f1d peer fix (no runtime manifest entries for peer and backend),
    so its success path is refused at _read_staged_backend on this head. D1 is about the stage root, so only that
    attestation is replaced by a spy that answers for the exact bundle peer path and fixed plan; every other
    composer check runs for real. The peer attestation itself is covered by the secure transport tests."""
    from tools import wd_q4_staged_composer as staged
    peer = Path(materialize['expected_bundle']) / 'tools-bootstrap' / 'tools' / 'wd_q4_secure_transport.py'
    backend = (Path(materialize['expected_bundle']) / 'tools-bootstrap' / 'tools' / 'wd_q4_staged_fixed_backend.py')
    fixed = staged.transport._fixed_plan(materialize['fixed_plan_bytes'])

    def attest(path, plan):
        assert Path(path) == peer and plan == fixed
        return backend.read_bytes()
    monkeypatch.setattr(staged.transport, '_read_staged_backend', attest)
    return materialize


def _snapshot(stage):
    return {str(p.relative_to(stage)): (p.read_bytes() if p.is_file() else None)
            for p in sorted(Path(stage).rglob('*'))}


def _shared(case, generation):
    from tools import wd_q4_staged_composer as staged
    return staged.compose_staged_card(**dict(case, generation=generation, shared_stage=True))


def test_shared_stage_composes_two_generations_with_noncolliding_pointers(shared_case):
    first = _shared(shared_case, _GEN_B)
    before = _snapshot(shared_case['stage_dir'])
    second = _shared(shared_case, _GEN_C)
    stage = Path(shared_case['stage_dir'])
    assert first['status'] == second['status'] == 'PREPARED'
    assert Path(first['pointer_path']) == stage / ('candidate-pointer-' + _GEN_B + '.json')
    assert Path(second['pointer_path']) == stage / ('candidate-pointer-' + _GEN_C + '.json')
    after = _snapshot(stage)
    assert {key: after[key] for key in before} == before  # earlier generation bytes are rollback-safe
    assert sha(Path(first['pointer_path']).read_bytes()) == first['pointer_sha256']
    assert sha(Path(second['pointer_path']).read_bytes()) == second['pointer_sha256']


def test_default_stage_still_requires_empty_dir_and_fixed_pointer_name(shared_case):
    from tools import wd_q4_staged_composer as staged
    result = staged.compose_staged_card(**shared_case)
    assert Path(result['pointer_path']).name == 'candidate-pointer.json'
    with pytest.raises(staged.ComposeRefused):
        staged.compose_staged_card(**dict(shared_case, generation=_GEN_C))


@pytest.mark.parametrize('flag', [1, 'yes', None, 0])
def test_shared_stage_flag_must_be_a_bool(shared_case, flag):
    from tools import wd_q4_staged_composer as staged
    with pytest.raises(staged.ComposeRefused):
        staged.compose_staged_card(**dict(shared_case, shared_stage=flag))
    assert not any(Path(shared_case['stage_dir']).iterdir())


@pytest.mark.parametrize('overlap', ['generation-dir', 'pointer-file', 'foreign-file', 'foreign-dir', 'bad-pointer',
                                     'generation-named-file'])
def test_shared_stage_refuses_overlap_or_foreign_entry_before_any_write(shared_case, overlap):
    from tools import wd_q4_staged_composer as staged
    stage = Path(shared_case['stage_dir'])
    _shared(shared_case, _GEN_B)
    if overlap == 'generation-dir':
        (stage / _GEN_C).mkdir()
    elif overlap == 'pointer-file':
        (stage / ('candidate-pointer-' + _GEN_C + '.json')).write_bytes(b'{}')
    elif overlap == 'foreign-file':
        (stage / 'notes.txt').write_bytes(b'x')
    elif overlap == 'foreign-dir':
        (stage / 'scratch').mkdir()
    elif overlap == 'bad-pointer':
        (stage / 'candidate-pointer.json').write_bytes(b'{}')
    else:
        (stage / ('d' * 40)).write_bytes(b'not a generation directory')
    before = _snapshot(stage)
    with pytest.raises(staged.ComposeRefused):
        _shared(shared_case, _GEN_C)
    assert _snapshot(stage) == before


def test_shared_stage_refuses_recomposing_an_existing_generation(shared_case):
    from tools import wd_q4_staged_composer as staged
    _shared(shared_case, _GEN_B)
    before = _snapshot(shared_case['stage_dir'])
    with pytest.raises(staged.ComposeRefused):
        _shared(shared_case, _GEN_B)
    assert _snapshot(shared_case['stage_dir']) == before


@pytest.mark.parametrize('error', [OSError, KeyboardInterrupt])
def test_shared_stage_interrupted_pointer_write_is_incomplete_and_keeps_old_bytes(shared_case, monkeypatch, error):
    from tools import wd_q4_staged_composer as staged
    _shared(shared_case, _GEN_B)
    before = _snapshot(shared_case['stage_dir'])
    real = staged._exclusive

    def interrupted(path, raw):
        if Path(path).name.startswith('candidate-pointer-'):
            raise error()
        return real(path, raw)
    monkeypatch.setattr(staged, '_exclusive', interrupted)
    with pytest.raises(staged.ComposeIncomplete):
        _shared(shared_case, _GEN_C)
    after = _snapshot(shared_case['stage_dir'])
    assert {key: after[key] for key in before} == before
    assert not (Path(shared_case['stage_dir']) / ('candidate-pointer-' + _GEN_C + '.json')).exists()


# --- descriptor timeout_seconds must fit the lane caller cap (lane_caller_cli _configuration: 1..600) -------------

@pytest.mark.parametrize('timeout', [601, 0, -1, 1800, True, 600.0, '600', None])
def test_staged_card_refuses_timeout_outside_lane_caller_cap_before_write(shared_case, timeout):
    from tools import wd_q4_staged_composer as staged
    case = dict(shared_case, descriptor_options=dict(shared_case['descriptor_options'], timeout_seconds=timeout))
    with pytest.raises(staged.ComposeRefused):
        staged.compose_staged_card(**case)
    assert not any(Path(case['stage_dir']).iterdir())


@pytest.mark.parametrize('timeout', [1, 600])
def test_staged_card_accepts_timeout_at_lane_caller_bounds(shared_case, timeout):
    from tools import wd_q4_staged_composer as staged
    case = dict(shared_case, descriptor_options=dict(shared_case['descriptor_options'], timeout_seconds=timeout))
    result = staged.compose_staged_card(**case)
    assert result['status'] == 'PREPARED'
    assert json.loads(Path(result['descriptor_path']).read_bytes())['timeout_seconds'] == timeout

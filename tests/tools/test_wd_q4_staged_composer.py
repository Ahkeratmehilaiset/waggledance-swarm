"""Synthetic only: stage binding, session descriptor and pointer without activation."""
import hashlib
import json
from pathlib import Path
import pytest
from tools import wd_q4_lane_binding_producer as bp
from tests.tools.test_wd_q4_lane_binding_producer import case as binding_case
from tests.tools.test_wd_q4_descriptor_cli import synthetic, write_request


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def setup_case(tmp_path):
    source = binding_case.__wrapped__()
    bundle = tmp_path / ('a' * 40)
    bundle.mkdir()
    (bundle / 'deployment-manifest.json').write_bytes(source['manifest_bytes'])
    (bundle / 'wd-fleet.json').write_bytes(source['fleet_bytes'])
    (bundle / 'Invoke-WdQ4Query.ps1').write_bytes(source['wrapper_bytes'])
    fixture_dir = tmp_path / 'fixture'
    fixture_dir.mkdir()
    request, _, _ = synthetic(fixture_dir)
    stage = tmp_path / 'stage'
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
    assert not list(tmp_path.rglob('WD_REBOOT_STATE_CURRENT.json'))
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

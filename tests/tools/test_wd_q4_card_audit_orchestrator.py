"""Synthetic inputs only; no installed bridge or active state."""
import hashlib
import importlib
import json
from pathlib import Path

import pytest
from tests.tools.test_wd_q4_real_card_generation import real_fixture
from tests.tools.test_wd_q4_real_card_decision_cli import fixture
from tests.tools.test_wd_q4_real_card_snapshot_cli import setup_request
from tests.tools.test_wd_q4_staged_card_composer import materialize, staged_producer


@pytest.fixture
def tmp_path(tmp_path_factory):
    """Keep synthetic artifacts inside an audit-named root under any pytest basetemp."""
    root = tmp_path_factory.mktemp('q4-card-orchestrator') / '.codex-audit'
    root.mkdir()
    return root


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def write(path, value):
    raw = json.dumps(value, sort_keys=True, separators=(',', ':')).encode()
    path.write_bytes(raw)
    return sha(raw)


def test_off_does_not_touch_inputs():
    mod = importlib.import_module('tools.wd_q4_card_audit_orchestrator')
    class Untouchable:
        def __getattribute__(self, name):
            raise AssertionError('OFF read an input')
    assert mod.run(request_path=Untouchable()) == {'status': 'OFF'}


def make_request(real_fixture, tmp_path):
    snapshot, snapshot_path, _, _ = setup_request(real_fixture, tmp_path)
    snapshot_path = tmp_path / 'snapshot-template.json'
    decision, decision_path, decision_pin, _, _ = fixture(real_fixture, tmp_path)
    args = real_fixture[0]
    snapshot['source_path'] = decision['source_path']
    snap_pin = write(snapshot_path, snapshot)
    out = tmp_path / 'orchestrator-output'; out.mkdir()
    request = {'schema': 'wd.q4-card-audit-prefix-request.v1',
               'decision': {'path': str(decision_path), 'sha256': decision_pin},
               'snapshot_template': {'path': str(snapshot_path), 'sha256': snap_pin},
               'output_root': str(out)}
    path = tmp_path / 'orchestrator-request.json'
    return path, write(path, request), out, args


def test_real_decision_to_snapshot_is_explicitly_incomplete(real_fixture, tmp_path, monkeypatch):
    pytest.importorskip('faiss')
    mod = importlib.import_module('tools.wd_q4_card_audit_orchestrator')
    from tools import wd_q4_real_card_snapshot_producer as producer
    path, pin, out, args = make_request(real_fixture, tmp_path)
    monkeypatch.setattr(producer, '_load_pinned_e5', lambda **_: (args['encode'], args['identity'], args['dimension'], lambda: True))
    result = mod.run(enabled=True, request_path=str(path), request_sha256=pin)
    assert result['status'] == 'INCOMPLETE'
    assert result['next_step'] == 'STAGED_COMPOSER_REQUIRES_PINNED_CURRENT'
    assert sha((out / 'snapshot' / 'card-snapshot-candidate.json').read_bytes()) == result['card_pointer_sha256']
    assert not (out / 'candidate-pointer.json').exists()


def test_bad_outer_pin_refused_without_writes(real_fixture, tmp_path):
    mod = importlib.import_module('tools.wd_q4_card_audit_orchestrator')
    path, _, out, _ = make_request(real_fixture, tmp_path)
    with pytest.raises(mod.Refused):
        mod.run(enabled=True, request_path=str(path), request_sha256='0' * 64)
    assert list(out.iterdir()) == []


def test_interruption_after_output_is_unknown(real_fixture, tmp_path, monkeypatch):
    mod = importlib.import_module('tools.wd_q4_card_audit_orchestrator')
    from tools import wd_q4_real_card_decision_cli as decision
    path, pin, out, _ = make_request(real_fixture, tmp_path)
    def interrupted(**kwargs):
        raise KeyboardInterrupt()
    monkeypatch.setattr(decision, 'run', interrupted)
    with pytest.raises(mod.Unknown):
        mod.run(enabled=True, request_path=str(path), request_sha256=pin)
    assert list(out.iterdir())


@pytest.mark.parametrize('fault', ['template_pin', 'input_mismatch', 'unsafe_mode', 'generation_name'])
def test_mismatch_refused_before_output(real_fixture, tmp_path, fault):
    mod = importlib.import_module('tools.wd_q4_card_audit_orchestrator')
    path, _, out, _ = make_request(real_fixture, tmp_path)
    request = json.loads(path.read_bytes())
    template_path = Path(request['snapshot_template']['path'])
    template = json.loads(template_path.read_bytes())
    if fault == 'input_mismatch':
        template['inputs']['policy']['sha256'] = '0' * 64
    elif fault == 'unsafe_mode':
        template['source_mode'] = 'operator_authorized_source'
    elif fault == 'generation_name':
        template['generation_name'] = 'other'
    request['snapshot_template']['sha256'] = write(template_path, template)
    if fault == 'template_pin':
        request['snapshot_template']['sha256'] = '0' * 64
    with pytest.raises(mod.Refused):
        mod.run(enabled=True, request_path=str(path), request_sha256=write(path, request))
    assert list(out.iterdir()) == []


def test_non_audit_source_rejected_before_anchor_reader(real_fixture, tmp_path, monkeypatch):
    mod = importlib.import_module('tools.wd_q4_card_audit_orchestrator')
    from tools import wd_q4_external_generation_anchor as anchor
    path, _, out, _ = make_request(real_fixture, tmp_path)
    request = json.loads(path.read_bytes())
    decision_path = Path(request['decision']['path'])
    template_path = Path(request['snapshot_template']['path'])
    decision, template = json.loads(decision_path.read_bytes()), json.loads(template_path.read_bytes())
    decision['source_path'] = decision['git']['executable']
    template['source_path'] = decision['source_path']
    request['decision']['sha256'] = write(decision_path, decision)
    request['snapshot_template']['sha256'] = write(template_path, template)
    seen = []
    monkeypatch.setattr(anchor, 'verify', lambda **kwargs: seen.append(True))
    with pytest.raises(mod.Refused):
        mod.run(enabled=True, request_path=str(path), request_sha256=write(path, request))
    assert seen == [] and list(out.iterdir()) == []


def test_real_plan_and_composer_from_verified_synthetic_c2(materialize, tmp_path):
    from tools import wd_q4_lane_link
    mod = importlib.import_module('tools.wd_q4_card_audit_orchestrator')
    case = dict(materialize)
    staged = json.loads(Path(case['public_plan_path']).read_bytes())
    base = {key: staged[key] for key in wd_q4_lane_link._PUBLIC_PLAN_SCHEMAS['wd.q4-public-wrapper-plan.v1']}
    base['schema'] = 'wd.q4-public-wrapper-plan.v1'
    fixed = json.loads(case.pop('fixed_plan_bytes'))
    fixed = {k: v for k, v in fixed.items() if k not in ('schema', 'backend')}
    case.pop('enabled'); case.pop('expected_fixed_plan_sha256')
    case.pop('public_plan_path'); case.pop('expected_public_plan_sha256')
    case.pop('stage_dir'); case.pop('generation_root')
    real_pointer = Path(staged['root']) / 'card-snapshot-candidate.json'
    real_pointer.write_bytes(Path(staged['pointer_path']).read_bytes())
    out = tmp_path / 'finish'; out.mkdir()
    result = mod._finish(enabled=True, output_root=out, base_plan=base, fixed_template=fixed,
        composer_options=case, pointer_path=str(real_pointer), pointer_sha256=staged['pointer_sha256'],
        decision_path=staged['card_decision_path'], card_policy_path=staged['card_policy_path'],
        source_path=staged['source_path'], generation_path=staged['generation_path'])
    assert result['status'] == 'PREPARED'
    pointer = Path(result['pointer_path'])
    assert sha(pointer.read_bytes()) == result['pointer_sha256']
    assert not (tmp_path / 'WD_Q4_DESCRIPTOR_CURRENT.json').exists()


@pytest.mark.parametrize('fault', [None, 'finish_pin', 'interrupted', 'wrapper_tamper'])
def test_full_synthetic_chain_uses_real_writers_and_fixture_current(materialize, tmp_path, monkeypatch, fault):
    pytest.importorskip('faiss')
    from tools import wd_q4_lane_link
    from tools import wd_q4_real_card_snapshot_producer as producer
    mod = importlib.import_module('tools.wd_q4_card_audit_orchestrator')
    real_dir = tmp_path / 'real-card-fixture'; real_dir.mkdir()
    real = real_fixture.__wrapped__(real_dir)
    staged = json.loads(Path(materialize['public_plan_path']).read_bytes())
    real[0]['roster_bytes'] = Path(staged['roster_path']).read_bytes()
    real[0]['roster_sha256'] = sha(real[0]['roster_bytes'])
    path, _, out, args = make_request(real, real_dir)
    request = json.loads(path.read_bytes())
    decision = json.loads(Path(request['decision']['path']).read_bytes())
    case = dict(materialize)
    base = {k: staged[k] for k in wd_q4_lane_link._PUBLIC_PLAN_SCHEMAS['wd.q4-public-wrapper-plan.v1']}
    base['schema'] = 'wd.q4-public-wrapper-plan.v1'
    for key in ('policy', 'roster'):
        base[key + '_path'] = decision['inputs'][key]['path']
        base[key + '_sha256'] = decision['inputs'][key]['sha256']
    fixed = json.loads(case.pop('fixed_plan_bytes'))
    fixed = {k: v for k, v in fixed.items() if k not in ('schema', 'backend')}
    for key in ('enabled', 'expected_fixed_plan_sha256', 'public_plan_path', 'expected_public_plan_sha256',
                'stage_dir', 'generation_root'):
        case.pop(key)
    finish = {'schema': 'wd.q4-card-audit-finish-template.v1', 'base_plan': base,
              'fixed_template': fixed, 'composer_options': case}
    finish_path = tmp_path / 'finish-template.json'
    request['finish'] = {'path': str(finish_path), 'sha256': write(finish_path, finish)}
    if fault == 'finish_pin':
        request['finish']['sha256'] = '0' * 64
    pin = write(path, request)
    monkeypatch.setattr(producer, '_load_pinned_e5', lambda **_: (args['encode'], args['identity'], args['dimension'], lambda: True))
    if fault == 'finish_pin':
        with pytest.raises(mod.Refused):
            mod.run(enabled=True, request_path=str(path), request_sha256=pin)
        assert list(out.iterdir()) == []
        return
    if fault == 'interrupted':
        def interrupted(**kwargs):
            raise KeyboardInterrupt()
        monkeypatch.setattr(mod, '_finish', interrupted)
        with pytest.raises(mod.Unknown):
            mod.run(enabled=True, request_path=str(path), request_sha256=pin)
        assert list(out.iterdir()) and not (out / 'stage' / 'candidate-pointer.json').exists()
        return
    if fault == 'wrapper_tamper':
        (Path(case['expected_bundle']) / 'Invoke-WdQ4Query.ps1').write_bytes(b'tampered synthetic wrapper')
        with pytest.raises(mod.Unknown):
            mod.run(enabled=True, request_path=str(path), request_sha256=pin)
        assert list(out.iterdir()) and not (out / 'stage' / 'candidate-pointer.json').exists()
        return
    result = mod.run(enabled=True, request_path=str(path), request_sha256=pin)
    assert result['status'] == 'PREPARED'
    candidate = out / 'stage' / 'candidate-pointer.json'
    assert sha(candidate.read_bytes()) == result['pointer_sha256']
    assert not (tmp_path / 'WD_Q4_DESCRIPTOR_CURRENT.json').exists()


@pytest.mark.parametrize('fault', [None, 'outside_parent', 'nonempty', 'wrong_state', 'policy_mismatch', 'mode_mismatch', 'interrupted'])
def test_real_source_mode_stages_candidate_in_separate_generation_root(materialize, tmp_path, monkeypatch, fault):
    pytest.importorskip('faiss')
    from tools import wd_q4_lane_link
    from tools import wd_q4_real_card_snapshot_producer as producer
    mod = importlib.import_module('tools.wd_q4_card_audit_orchestrator')
    real_dir = tmp_path / 'synthetic-input'; real_dir.mkdir()
    real = real_fixture.__wrapped__(real_dir)
    staged = json.loads(Path(materialize['public_plan_path']).read_bytes())
    real[0]['roster_bytes'] = Path(staged['roster_path']).read_bytes()
    real[0]['roster_sha256'] = sha(real[0]['roster_bytes'])
    path, _, out, args = make_request(real, real_dir)
    request = json.loads(path.read_bytes())
    for key in ('decision', 'snapshot_template'):
        p = Path(request[key]['path']); doc = json.loads(p.read_bytes())
        doc['source_mode'] = 'operator_authorized_source'
        request[key]['sha256'] = write(p, doc)
    decision = json.loads(Path(request['decision']['path']).read_bytes())
    base = {k: staged[k] for k in wd_q4_lane_link._PUBLIC_PLAN_SCHEMAS['wd.q4-public-wrapper-plan.v1']}
    base['schema'] = 'wd.q4-public-wrapper-plan.v1'
    for key in ('policy', 'roster', 'public_policy'):
        base[key + '_path'] = decision['inputs'][key]['path']
        base[key + '_sha256'] = decision['inputs'][key]['sha256']
    case = dict(materialize)
    fixed = json.loads(case.pop('fixed_plan_bytes'))
    fixed = {k: v for k, v in fixed.items() if k not in ('schema', 'backend')}
    for key in ('enabled', 'expected_fixed_plan_sha256', 'public_plan_path', 'expected_public_plan_sha256',
                'stage_dir', 'generation_root'):
        case.pop(key)
    finish_path = tmp_path / 'real-finish-template.json'
    request['finish'] = {'path': str(finish_path), 'sha256': write(finish_path,
        {'schema':'wd.q4-card-audit-finish-template.v1', 'base_plan':base, 'fixed_template':fixed,
         'composer_options':case})}
    gen_parent = tmp_path / 'persistent-generations'; gen_parent.mkdir()
    gen = gen_parent / 'generation-one'; gen.mkdir()
    monkeypatch.setattr(mod, '_GENERATION_PARENT', gen_parent)
    if fault == 'outside_parent':
        monkeypatch.setattr(mod, '_GENERATION_PARENT', tmp_path)
    if fault == 'nonempty':
        (gen / 'partial').write_bytes(b'partial')
    if fault == 'wrong_state':
        document = json.loads(finish_path.read_bytes())
        document['composer_options']['expected_state_sha256'] = '0' * 64
        request['finish']['sha256'] = write(finish_path, document)
    if fault == 'policy_mismatch':
        document = json.loads(finish_path.read_bytes())
        document['base_plan']['policy_sha256'] = '0' * 64
        request['finish']['sha256'] = write(finish_path, document)
    if fault == 'mode_mismatch':
        snapshot_path = Path(request['snapshot_template']['path'])
        snapshot = json.loads(snapshot_path.read_bytes())
        snapshot['source_mode'] = 'synthetic_fixture'
        request['snapshot_template']['sha256'] = write(snapshot_path, snapshot)
    request.update(schema='wd.q4-card-real-candidate-request.v1', generation_root=str(gen))
    pin = write(path, request)
    monkeypatch.setattr(producer, '_load_pinned_e5', lambda **_: (args['encode'], args['identity'], args['dimension'], lambda: True))
    if fault == 'interrupted':
        def interrupted(**kwargs):
            raise KeyboardInterrupt()
        monkeypatch.setattr(mod, '_finish', interrupted)
        with pytest.raises(mod.Unknown):
            mod.run(enabled=True, request_path=str(path), request_sha256=pin)
        assert list(out.iterdir()) and list(gen.iterdir())
        with pytest.raises(mod.Refused):
            mod.run(enabled=True, request_path=str(path), request_sha256=pin)
        return
    if fault:
        with pytest.raises(mod.Refused):
            mod.run(enabled=True, request_path=str(path), request_sha256=pin)
        assert list(out.iterdir()) == [] and not (gen / 'stage').exists()
        return
    result = mod.run(enabled=True, request_path=str(path), request_sha256=pin)
    pointer = gen / 'stage' / 'candidate-pointer.json'
    assert result['status'] == 'PREPARED' and result['pointer_path'] == str(pointer)
    assert sha(pointer.read_bytes()) == result['pointer_sha256']
    assert not (tmp_path / 'WD_Q4_DESCRIPTOR_CURRENT.json').exists()


# --- D1: _finish may compose into one shared immutable generations root (composer shared_stage=True) ------------
# The materialize fixture predates the af098f1d peer fix, so the composer refuses its success path at
# _read_staged_backend on this head. These tests replace only that attestation (exact bundle peer path, staged
# backend plan); the plan writers, the fixed plan writer, the composer and the CLI result verifier run for real.

_GEN_B, _GEN_C = 'b' * 40, 'c' * 40


@pytest.fixture
def attested(materialize, monkeypatch):
    from tools import wd_q4_staged_composer as staged
    tools_dir = Path(materialize['expected_bundle']) / 'tools-bootstrap' / 'tools'

    def attest(path, plan):
        assert Path(path) == tools_dir / 'wd_q4_secure_transport.py' and plan['backend'] == 'staged-card-wrapper-v1'
        return (tools_dir / 'wd_q4_staged_fixed_backend.py').read_bytes()
    monkeypatch.setattr(staged.transport, '_read_staged_backend', attest)
    return materialize


def _finish_into(case, out, **option_changes):
    from tools import wd_q4_lane_link
    mod = importlib.import_module('tools.wd_q4_card_audit_orchestrator')
    case = dict(case)
    staged = json.loads(Path(case['public_plan_path']).read_bytes())
    base = {key: staged[key] for key in wd_q4_lane_link._PUBLIC_PLAN_SCHEMAS['wd.q4-public-wrapper-plan.v1']}
    base['schema'] = 'wd.q4-public-wrapper-plan.v1'
    fixed = json.loads(case.pop('fixed_plan_bytes'))
    fixed = {k: v for k, v in fixed.items() if k not in ('schema', 'backend')}
    for key in ('enabled', 'expected_fixed_plan_sha256', 'public_plan_path', 'expected_public_plan_sha256',
                'stage_dir', 'generation_root'):
        case.pop(key)
    case.update(option_changes)
    real_pointer = Path(staged['root']) / 'card-snapshot-candidate.json'
    real_pointer.write_bytes(Path(staged['pointer_path']).read_bytes())
    out.mkdir(exist_ok=True)
    return mod._finish(enabled=True, output_root=out, base_plan=base, fixed_template=fixed,
        composer_options=case, pointer_path=str(real_pointer), pointer_sha256=staged['pointer_sha256'],
        decision_path=staged['card_decision_path'], card_policy_path=staged['card_policy_path'],
        source_path=staged['source_path'], generation_path=staged['generation_path'])


def _tree(root):
    return {str(p.relative_to(root)): (p.read_bytes() if p.is_file() else None) for p in sorted(Path(root).rglob('*'))}


def test_finish_composes_two_generations_into_one_shared_root(attested, tmp_path):
    shared = tmp_path / 'live' / 'generations'
    shared.mkdir(parents=True)
    first = _finish_into(attested, tmp_path / 'finish-b', generation=_GEN_B, shared_stage_root=str(shared))
    assert first['status'] == 'PREPARED'
    assert Path(first['pointer_path']) == shared / ('candidate-pointer-' + _GEN_B + '.json')
    before = _tree(shared)
    second = _finish_into(attested, tmp_path / 'finish-c', generation=_GEN_C, shared_stage_root=str(shared))
    assert second['status'] == 'PREPARED'
    assert Path(second['pointer_path']) == shared / ('candidate-pointer-' + _GEN_C + '.json')
    assert Path(second['descriptor_path']).is_relative_to(shared / _GEN_C)
    descriptor = json.loads(Path(second['descriptor_path']).read_bytes())
    fixed = json.loads((shared / _GEN_C / 'fixed-plan.v2.json').read_bytes())
    copy = shared / _GEN_C / 'public-plan.json'  # the selector reads the candidate's own public plan here
    assert Path(descriptor['public_plan']) == copy and Path(fixed['public_plan_path']) == copy
    assert sha(copy.read_bytes()) == fixed['public_plan_sha256']
    after = _tree(shared)
    assert {key: after[key] for key in before} == before
    assert not (tmp_path / 'finish-b' / 'stage').exists() and not (tmp_path / 'finish-c' / 'stage').exists()


def test_finish_default_keeps_private_stage_and_fixed_pointer_name(attested, tmp_path):
    out = tmp_path / 'finish'
    result = _finish_into(attested, out)
    assert Path(result['pointer_path']) == out / 'stage' / 'candidate-pointer.json'
    descriptor = json.loads(Path(result['descriptor_path']).read_bytes())
    assert Path(descriptor['public_plan']).parent == out / 'public-plan'
    assert not (Path(result['descriptor_path']).parent / 'public-plan.json').exists()


def test_finish_accepts_shared_root_beside_the_output_root(attested, tmp_path):
    shared = tmp_path / 'live' / 'generations'  # the generation root (its parent) must not hold the bundle store
    shared.mkdir(parents=True)
    result = _finish_into(attested, tmp_path / 'live' / 'finish', generation=_GEN_B, shared_stage_root=str(shared))
    assert Path(result['pointer_path']) == shared / ('candidate-pointer-' + _GEN_B + '.json')


@pytest.mark.parametrize('layout', ['inside-output', 'contains-output', 'missing', 'file'])
def test_finish_refuses_bad_shared_root_before_any_write(attested, tmp_path, layout):
    out = tmp_path / 'case' / 'finish'
    (tmp_path / 'case').mkdir()
    shared = {'inside-output': out / 'generations', 'contains-output': tmp_path / 'case',
              'missing': tmp_path / 'absent', 'file': tmp_path / 'not-a-dir'}[layout]
    if layout == 'file':
        shared.write_bytes(b'x')
    if layout == 'inside-output':
        shared.mkdir(parents=True)
    with pytest.raises((ValueError, OSError)):
        _finish_into(attested, out, generation=_GEN_B, shared_stage_root=str(shared))
    expected = ['generations'] if layout == 'inside-output' else []
    assert sorted(p.name for p in out.iterdir()) == expected
    if shared.is_dir() and layout != 'contains-output':
        assert list(shared.iterdir()) == []


@pytest.mark.parametrize('options', [{'shared_stage': True}, {'shared_stage_root': None},
                                     {'shared_stage_root': 1}, {'generation': '..', 'shared_stage_root': 'live'},
                                     {'generation': 'B' * 40, 'shared_stage_root': 'live'}])
def test_finish_refuses_unknown_or_malformed_shared_option_before_any_write(attested, tmp_path, options):
    out = tmp_path / 'finish'
    changes = dict(generation=_GEN_B)
    changes.update(options)
    if changes.get('shared_stage_root') == 'live':
        shared = tmp_path / 'live' / 'generations'
        shared.mkdir(parents=True)
        changes['shared_stage_root'] = str(shared)
    with pytest.raises((ValueError, OSError)):
        _finish_into(attested, out, **changes)
    assert list(out.iterdir()) == []


def _chain(materialize, tmp_path, monkeypatch, shared):
    from tools import wd_q4_lane_link
    from tools import wd_q4_real_card_snapshot_producer as producer
    real_dir = tmp_path / 'real-card-fixture'; real_dir.mkdir()
    real = real_fixture.__wrapped__(real_dir)
    staged = json.loads(Path(materialize['public_plan_path']).read_bytes())
    real[0]['roster_bytes'] = Path(staged['roster_path']).read_bytes()
    real[0]['roster_sha256'] = sha(real[0]['roster_bytes'])
    path, _, out, args = make_request(real, real_dir)
    request = json.loads(path.read_bytes())
    decision = json.loads(Path(request['decision']['path']).read_bytes())
    case = dict(materialize)
    base = {k: staged[k] for k in wd_q4_lane_link._PUBLIC_PLAN_SCHEMAS['wd.q4-public-wrapper-plan.v1']}
    base['schema'] = 'wd.q4-public-wrapper-plan.v1'
    for key in ('policy', 'roster'):
        base[key + '_path'] = decision['inputs'][key]['path']
        base[key + '_sha256'] = decision['inputs'][key]['sha256']
    fixed = json.loads(case.pop('fixed_plan_bytes'))
    fixed = {k: v for k, v in fixed.items() if k not in ('schema', 'backend')}
    for key in ('enabled', 'expected_fixed_plan_sha256', 'public_plan_path', 'expected_public_plan_sha256',
                'stage_dir', 'generation_root'):
        case.pop(key)
    case.update(generation=_GEN_B, shared_stage_root=str(shared(out)))
    finish = {'schema': 'wd.q4-card-audit-finish-template.v1', 'base_plan': base,
              'fixed_template': fixed, 'composer_options': case}
    finish_path = tmp_path / 'finish-template.json'
    request['finish'] = {'path': str(finish_path), 'sha256': write(finish_path, finish)}
    monkeypatch.setattr(producer, '_load_pinned_e5', lambda **_: (args['encode'], args['identity'], args['dimension'], lambda: True))
    return path, write(path, request), out


def test_run_composes_into_shared_root_with_generation_pointer(attested, tmp_path, monkeypatch):
    pytest.importorskip('faiss')
    mod = importlib.import_module('tools.wd_q4_card_audit_orchestrator')
    shared = tmp_path / 'live' / 'generations'
    shared.mkdir(parents=True)
    path, pin, out = _chain(attested, tmp_path, monkeypatch, lambda out: shared)
    result = mod.run(enabled=True, request_path=str(path), request_sha256=pin)
    assert result['status'] == 'PREPARED'
    assert Path(result['pointer_path']) == shared / ('candidate-pointer-' + _GEN_B + '.json')
    assert not (out / 'stage').exists()


def test_run_refuses_shared_root_containing_output_before_any_write(attested, tmp_path, monkeypatch):
    pytest.importorskip('faiss')
    mod = importlib.import_module('tools.wd_q4_card_audit_orchestrator')
    path, pin, out = _chain(attested, tmp_path, monkeypatch, lambda out: out.parent)
    with pytest.raises(mod.Refused):  # run must refuse before its own decision and snapshot writes
        mod.run(enabled=True, request_path=str(path), request_sha256=pin)
    assert list(out.iterdir()) == []

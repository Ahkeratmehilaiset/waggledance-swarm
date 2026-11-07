"""Synthetic inputs only; no installed bridge or active state."""
import hashlib
import importlib
import json
from pathlib import Path

import pytest
from tests.tools.test_wd_q4_real_card_generation import real_fixture
from tests.tools.test_wd_q4_real_card_decision_cli import fixture
from tests.tools.test_wd_q4_real_card_snapshot_cli import setup_request


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

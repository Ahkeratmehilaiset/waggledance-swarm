"""Resolved-child card decision forwarding; fabricated local input only."""
import importlib.util
import io
import hashlib
import json
from pathlib import Path

import pytest

from sandbox.mind.step2 import shared_card_projection as cards
from sandbox.mind.step2 import shared_capture_adapter as adapter
from tools import wd_q4_resolved_child as child

_spec = importlib.util.spec_from_file_location('real_reader_fixture',
                                               Path(__file__).with_name('test_wd_q4_real_card_reader.py'))
fixture_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fixture_module)
real_fixture = fixture_module.real_fixture
real_snapshot = fixture_module.real_snapshot


def _data(snapshot):
    _, _, _, _, projection, args = snapshot
    raw = json.dumps(projection, sort_keys=True, indent=1).encode('utf-8')
    tasks, roster = adapter.strict_pins(args['policy_bytes'], args['policy_sha256'],
                                        args['roster_bytes'], args['roster_sha256'])
    decision = cards.verify_route_decision(args['decision_bytes'], args['decision_sha256'],
                                          policy_sha256=args['policy_sha256'], roster_sha256=args['roster_sha256'])
    return raw, tasks, roster, decision


def test_real_projection_requires_and_uses_verified_decision(real_snapshot):
    raw, tasks, roster, decision = _data(real_snapshot)
    with pytest.raises(ValueError):
        child._validate_projection(raw, tasks, roster)
    child._validate_projection(raw, tasks, roster, decision)


def test_real_projection_rejects_decision_that_lacks_observed_head(real_snapshot):
    raw, tasks, roster, decision = _data(real_snapshot)
    decision['observed_heads'] = []
    with pytest.raises(ValueError):
        child._validate_projection(raw, tasks, roster, decision)


def test_real_projection_rejects_missing_author_in_roster(real_snapshot):
    raw, tasks, roster, decision = _data(real_snapshot)
    roster = dict(roster)
    roster.pop('codex-lead-1')
    with pytest.raises(ValueError):
        child._validate_projection(raw, tasks, roster, decision)


def test_wrong_decision_pin_refuses_before_query(real_snapshot, monkeypatch):
    directory, _, _, _, _, args = real_snapshot
    memory = (directory / 'memory.json').read_bytes()
    projection = (directory / 'projection.json').read_bytes()
    monkeypatch.setattr(child.q4, 'query_and_resolve', lambda *a, **k: pytest.fail('unexpected query'))
    output = io.StringIO()
    code = child.run_resolved(None, io.BytesIO(b'{}'), output, policy_bytes=args['policy_bytes'],
        policy_sha256=args['policy_sha256'], roster_bytes=args['roster_bytes'], roster_sha256=args['roster_sha256'],
        memory_bytes=memory, memory_sha256=hashlib.sha256(memory).hexdigest(), projection_bytes=projection,
        projection_sha256=hashlib.sha256(projection).hexdigest(), card_decision_bytes=args['decision_bytes'],
        card_decision_sha256='0' * 64)
    assert code == 2 and json.loads(output.getvalue())['status'] == 'REFUSED'

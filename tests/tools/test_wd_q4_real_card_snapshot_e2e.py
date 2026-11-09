"""Fabricated public-format cards over real C2, FAISS and in-process Q4.

The embedding is synthetic and the resolved-child launch boundary is replaced
with the real private reader in-process. No E5 model, live bridge, production
session or operating-system child process is exercised.
"""
import datetime as dt
import hashlib
import importlib.util
import io
import json
from pathlib import Path

import pytest

from sandbox.mind.step2 import runtime_launcher, shared_card_checkpoint, shared_card_projection
from sandbox.mind.step2 import wd_q4_private_read as reader
from tools import wd_q4_agent_query as q4
from tools import wd_q4_card_generation as producer
from tools import wd_q4_resolved_child as child

_spec = importlib.util.spec_from_file_location('real_reader_fixture',
                                               Path(__file__).with_name('test_wd_q4_real_card_reader.py'))
fixture_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fixture_module)
real_fixture = fixture_module.real_fixture
rt = fixture_module.fixture_module.rt


@pytest.fixture
def real_snapshot(real_fixture, tmp_path):
    """Five fabricated cards satisfy Q4's existing minimum-candidate guard."""
    pytest.importorskip('faiss')
    args, out, event, observation = real_fixture
    head = event['payload']['public_knowledge']['head']
    events = []
    for i in range(5):
        row = rt.fx.card(rt.fx.LEAD, i, 'merged', {'pr': 2046 + i}, 'green', 'none', ['genesis'], head=head)
        row['payload']['public_knowledge']['tags'].remove('synthetic')
        events.append(row)
    decision, decision_sha, source = rt.anchor(tmp_path / 'five-card-prefix', events,
        decision='enable_real_card_route', synthetic=False, observed_heads=observation['heads'],
        vocabulary_sha256=args['vocabulary_sha256'], head_observation_sha256=observation['sha256'])
    args.update(source, decision_bytes=decision, decision_sha256=decision_sha)
    built = producer.prepare_card_generation(**args)
    directory = out / 'candidate'
    artifacts = {str(path.relative_to(directory)).replace('\\', '/'): hashlib.sha256(path.read_bytes()).hexdigest()
                 for path in directory.rglob('*') if path.is_file()}
    decision_obj = json.loads(decision)
    pointer = {'schema': 'wd.q4-card-snapshot.v1', 'generation': 'candidate', 'artifacts': artifacts,
               'decision_sha256': decision_sha, 'policy_sha256': args['policy_sha256'],
               'roster_sha256': args['roster_sha256'], 'source_prefix': decision_obj['source_prefix']}
    pointer_bytes = json.dumps(pointer, sort_keys=True, separators=(',', ':')).encode('ascii')
    snapshot = dict(pointer_bytes=pointer_bytes, expected_pointer_sha256=hashlib.sha256(pointer_bytes).hexdigest(),
                    decision_bytes=decision, decision_sha256=decision_sha,
                    policy_bytes=args['policy_bytes'], policy_sha256=args['policy_sha256'],
                    roster_bytes=args['roster_bytes'], roster_sha256=args['roster_sha256'],
                    card_policy_bytes=args['card_policy_bytes'], source_path=args['source_path'],
                    generation_path=args['generation_path'], end_cursor=args['end_cursor'])
    projection = json.loads((directory / 'projection.json').read_bytes())
    return directory, built, decision_obj, snapshot, projection, args


def test_c2_in_harness_does_not_authorize_legacy_resolved_child(real_snapshot, monkeypatch):
    directory, built, _, snapshot, projection, args = real_snapshot
    verdict = shared_card_checkpoint.validate_card_snapshot(enabled=True, generation_dir=directory, **snapshot)
    assert verdict == {'status': 'VALIDATED_STAGED', 'completeness': 'UNKNOWN'}
    calls = []
    monkeypatch.setattr(reader, 'ENCODER_FACTORY', lambda **_kwargs: rt.fx.Encoder())
    monkeypatch.setattr(reader, 'bind_runtime_encoder', lambda *_args, **_kwargs: None)
    monkeypatch.setattr(q4, '_utc_now', lambda: dt.datetime(2026, 11, 3, tzinfo=dt.timezone.utc))
    def local_launcher(*, query, **_kwargs):
        calls.append('in_process_private_reader')
        question = json.loads(query)
        result = reader._private_read_locked(directory, directory / 'descriptor.json', built['descriptor_sha256'],
            question['situation'], question['time'], r'C:\fake\synthetic-model', rt.fx.IDENTITY['revision'])
        return runtime_launcher._validate_output(json.dumps(result).encode('utf-8'), 0, built['descriptor_sha256'])
    monkeypatch.setattr(q4, '_invoke_launcher', local_launcher)
    memory = (directory / 'memory.json').read_bytes()
    projection_bytes = (directory / 'projection.json').read_bytes()
    query = json.dumps({'situation': 'merged green pr 2046 genesis', 'time': '2026-11-02T00:00:00Z'}).encode()
    output = io.StringIO()
    code = child.run_resolved(rt.config(directory, built['descriptor_sha256']), io.BytesIO(query), output,
        policy_bytes=args['policy_bytes'], policy_sha256=args['policy_sha256'],
        roster_bytes=args['roster_bytes'], roster_sha256=args['roster_sha256'],
        memory_bytes=memory, memory_sha256=built['memory_sha256'],
        projection_bytes=projection_bytes, projection_sha256=hashlib.sha256(projection_bytes).hexdigest(),
        card_decision_bytes=args['decision_bytes'], card_decision_sha256=args['decision_sha256'])
    answer = json.loads(output.getvalue())
    assert code == 2 and answer['status'] == 'REFUSED' and calls == []
    assert projection['records'][0]['provenance']['source_event_ref'] in projection['sources']


def test_real_reader_validates_actual_c2_before_and_after_query(real_snapshot):
    directory, built, _, snapshot, projection, _ = real_snapshot
    result = shared_card_projection.ask_shared_cards(directory, built['memory_sha256'], rt.fx.Encoder(),
        'merged green pr 2046 genesis', '2026-11-02T00:00:00Z', enabled=True, real_snapshot=snapshot)
    assert result['status'] == 'ANSWER'
    assert any(item['stored_text'] == projection['records'][0]['stored_text'] for item in result['results'])
    assert result['coverage']['completeness'] == 'UNKNOWN'


def test_changed_projection_rejected_by_real_c2_and_reader(real_snapshot):
    directory, built, _, snapshot, _, _ = real_snapshot
    path = directory / 'projection.json'
    path.write_bytes(path.read_bytes() + b' ')
    assert shared_card_checkpoint.validate_card_snapshot(enabled=True, generation_dir=directory, **snapshot) == {
        'status': 'INCOMPLETE'}
    with pytest.raises(shared_card_projection.SharedCardRefused, match='snapshot_invalid'):
        shared_card_projection.ask_shared_cards(directory, built['memory_sha256'], rt.fx.Encoder(),
            'merged green pr 2046 genesis', '2026-11-02T00:00:00Z', enabled=True, real_snapshot=snapshot)

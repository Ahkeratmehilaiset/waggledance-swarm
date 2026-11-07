"""Read-only real-format cards over fabricated local fixtures; no live sources."""
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from sandbox.mind.step2 import shared_card_projection as cards
from sandbox.mind.step2 import shared_card_checkpoint as checkpoint
from tools import wd_q4_card_generation as producer

_spec = importlib.util.spec_from_file_location('real_card_generation_fixture',
                                               Path(__file__).with_name('test_wd_q4_real_card_generation.py'))
fixture_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fixture_module)
real_fixture = fixture_module.real_fixture


@pytest.fixture
def real_snapshot(real_fixture):
    pytest.importorskip('faiss')
    args, out, event, observation = real_fixture
    built = producer.prepare_card_generation(**args)
    directory = out / 'candidate'
    artifacts = {str(path.relative_to(directory)).replace('\\', '/'): hashlib.sha256(path.read_bytes()).hexdigest()
                 for path in directory.rglob('*') if path.is_file()}
    decision = json.loads(args['decision_bytes'])
    pointer = {'schema': 'wd.q4-card-snapshot.v1', 'generation': 'candidate', 'artifacts': artifacts,
               'decision_sha256': args['decision_sha256'], 'policy_sha256': args['policy_sha256'],
               'roster_sha256': args['roster_sha256'], 'source_prefix': decision['source_prefix']}
    raw = json.dumps(pointer, sort_keys=True, separators=(',', ':')).encode('ascii')
    snapshot = dict(pointer_bytes=raw, expected_pointer_sha256=hashlib.sha256(raw).hexdigest(),
                    decision_bytes=args['decision_bytes'], decision_sha256=args['decision_sha256'],
                    policy_bytes=args['policy_bytes'], policy_sha256=args['policy_sha256'],
                    roster_bytes=args['roster_bytes'], roster_sha256=args['roster_sha256'],
                    card_policy_bytes=args['card_policy_bytes'], source_path=args['source_path'],
                    generation_path=args['generation_path'], end_cursor=args['end_cursor'])
    projection = json.loads((directory / 'projection.json').read_bytes())
    return directory, built, decision, snapshot, projection, args


def test_real_formatter_is_internal_and_default_reader_still_refuses(real_snapshot):
    _, _, decision, _, projection, _ = real_snapshot
    record = projection['records'][0]
    item = {'ref_sha256': hashlib.sha256(record['provenance']['event_ref'].encode()).hexdigest(),
            'doc_id_sha256': hashlib.sha256(record['record_id'].encode()).hexdigest(),
            'ref_kind': cards.REF_KIND, 'canonical_event': False}
    with pytest.raises(cards.SharedCardRefused):
        cards.resolve_shared_card_results(projection, [item], '2026-11-02T00:00:00Z', enabled=True)
    resolved = cards.resolve_route_results(projection, [item], '2026-11-02T00:00:00Z', decision)
    assert len(resolved) == 1 and resolved[0]['stored_text'] == record['stored_text']


def test_real_reader_refuses_without_an_actual_c2_verdict(real_snapshot):
    directory, built, _, snapshot, _, args = real_snapshot
    snapshot = dict(snapshot, expected_pointer_sha256='0' * 64)
    assert checkpoint.validate_card_snapshot(enabled=True, generation_dir=directory, **snapshot)['status'] == 'INCOMPLETE'
    with pytest.raises(cards.SharedCardRefused, match='snapshot_invalid'):
        cards.ask_shared_cards(directory, built['memory_sha256'], None, 'synthetic',
                               '2026-11-02T00:00:00Z', enabled=True, real_snapshot=snapshot)


def test_off_does_not_inspect_real_snapshot():
    class Untouchable:
        def __getattribute__(self, name): raise AssertionError('OFF inspected input')
    assert cards.ask_shared_cards(Untouchable(), None, None, None, None, real_snapshot=Untouchable()) == {'status': 'OFF'}


def test_claimed_status_is_not_a_c2_snapshot(real_snapshot, monkeypatch):
    directory, built, _, _, _, _ = real_snapshot
    monkeypatch.setattr(checkpoint, 'validate_card_snapshot', lambda **kwargs: pytest.fail('invented status accepted'))
    with pytest.raises(cards.SharedCardRefused, match='snapshot_invalid'):
        cards.ask_shared_cards(directory, built['memory_sha256'], None, 'synthetic',
                               '2026-11-02T00:00:00Z', enabled=True,
                               real_snapshot={'status': 'VALIDATED_STAGED', 'completeness': 'UNKNOWN'})


def test_real_memory_is_refused_without_opt_in(real_snapshot):
    directory, built, _, _, _, _ = real_snapshot
    with pytest.raises(cards.SharedCardRefused, match='memory_invalid'):
        cards.ask_shared_cards(directory, built['memory_sha256'], None, 'synthetic',
                               '2026-11-02T00:00:00Z', enabled=True)

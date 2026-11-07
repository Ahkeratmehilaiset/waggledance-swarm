"""Pinned public-card expiry, fabricated sources only; no native/model/live IO."""
import json

import pytest

from sandbox.mind.step2 import shared_card_projection as cards
from sandbox.mind.step2 import shared_result_resolver as resolver
from tests.tools import test_shared_card_route as rt
from tests.tools.test_wd_q4_real_card_generation import real_fixture


def build_sample(mode, root, *, successor=False):
    root.mkdir(parents=True, exist_ok=True)
    if mode == 'real':
        local = root / 'local'; local.mkdir()
        args, _, first, observation = real_fixture.__wrapped__(local)
        head = first['payload']['public_knowledge']['head']
    else:
        head = '1' * 40
        first = rt.fx.card(rt.fx.LEAD, 0, 'merged', {'pr': 2046}, 'green', 'none', ['genesis'], head=head)
    second = rt.fx.card(rt.fx.LEAD, 1, 'merged', {'pr': 2047}, 'green', 'none', ['genesis'], head=head, days=5)
    if mode == 'real':
        second['payload']['public_knowledge']['tags'].remove('synthetic')
    events = [first, second]
    if successor:
        newer = rt.fx.card(rt.fx.LEAD, 20, 'merged', {'pr': 2046}, 'green', 'none', ['genesis'],
                           head=head, days=5, supersedes=rt.fx.event_id(first))
        if mode == 'real':
            newer['payload']['public_knowledge']['tags'].remove('synthetic')
        events.append(newer)
    if mode == 'real':
        from tools import wd_q4_card_generation as producer
        decision, decision_pin, source = rt.anchor(root / 'prefix', events,
            decision='enable_real_card_route', synthetic=False, observed_heads=observation['heads'],
            vocabulary_sha256=args['vocabulary_sha256'], head_observation_sha256=observation['sha256'])
        out = root / 'out'; out.mkdir()
        args.update(output_root=out, generation_name='cards', decision_bytes=decision,
                    decision_sha256=decision_pin, **source)
        built = producer.prepare_card_generation(**args)
        directory = out / 'cards'
        projection = json.loads((directory / 'projection.json').read_bytes())
        strict = {key: args[key] for key in ('policy_bytes', 'policy_sha256', 'roster_bytes', 'roster_sha256')}
    else:
        projection, built, directory, decision, decision_pin = rt.build(root, events)
        strict = rt.pins()
    return dict(mode=mode, events=events, projection=projection, built=built, directory=directory,
                decision=decision, decision_pin=decision_pin, strict=strict)


@pytest.fixture(scope='module', params=['synthetic', 'real'])
def sample(request, tmp_path_factory):
    return build_sample(request.param, tmp_path_factory.mktemp('e') / '.codex-audit')


def hit(record):
    return {'ref_sha256': rt.fx.sha(record['provenance']['event_ref'].encode()),
            'doc_id_sha256': rt.fx.sha(record['record_id'].encode()),
            'ref_kind': cards.REF_KIND, 'canonical_event': False}


def resolve(sample, records, when):
    directory = sample['directory']
    return resolver.resolve_results(memory_manifest=(directory / 'memory.json').read_bytes(),
        expected_memory_sha256=sample['built']['memory_sha256'],
        projection_bytes=(directory / 'projection.json').read_bytes(), results=[hit(r) for r in records],
        query_utc=when, approved_tasks=frozenset({rt.TASK}), **sample['strict'],
        card_decision_bytes=sample['decision'], card_decision_sha256=sample['decision_pin'])


def original_record(sample):
    source_id = rt.fx.event_id(sample['events'][0])
    return next(r for r in sample['projection']['records'] if r['provenance']['source_event_ref'] == source_id)


@pytest.mark.parametrize('when,current', [
    ('2026-11-04T09:59:59.999999Z', True),
    ('2026-11-04T10:00:00Z', False),
    ('2026-11-04T10:00:00.000001Z', False),
])
def test_public_card_expiry_equality_and_microsecond_boundaries(sample, when, current):
    record = original_record(sample)
    if current:
        result = resolve(sample, [record], when)
        assert result == [dict(hit(record), stored_text=record['stored_text'])]
    else:
        with pytest.raises(resolver.ResolveRefused):
            resolve(sample, [record], when)


def test_mixed_current_and_expired_hits_fail_closed(sample):
    with pytest.raises(resolver.ResolveRefused):
        resolve(sample, sample['projection']['records'], '2026-11-05T00:00:00Z')


def test_current_hit_survives_unreturned_expired_card_in_same_projection(sample):
    first = original_record(sample)
    current = next(r for r in sample['projection']['records'] if r != first)
    assert cards.card_status(current['stored_text'], '2026-11-05T00:00:00Z') == 'CURRENT'
    assert resolve(sample, [current], '2026-11-05T00:00:00Z') == [dict(hit(current), stored_text=current['stored_text'])]


def test_all_expired_hits_fail_closed(sample):
    with pytest.raises(resolver.ResolveRefused):
        resolve(sample, sample['projection']['records'], '2026-11-07T00:00:00Z')


def test_unknown_expiry_classification_never_becomes_public_answer(sample, monkeypatch):
    monkeypatch.setattr(cards, 'card_status', lambda *args: 'UNKNOWN')
    with pytest.raises(resolver.ResolveRefused):
        resolve(sample, [original_record(sample)], '2026-11-02T00:00:00Z')


def test_historical_card_formatter_keeps_its_explicit_stale_semantics(sample):
    decision = json.loads(sample['decision'])
    record = original_record(sample)
    result = cards.resolve_route_results(sample['projection'], [hit(record)], '2026-11-05T00:00:00Z', decision)
    assert result and cards.card_status(result[0]['stored_text'], '2026-11-05T00:00:00Z') == 'STALE'


def test_current_successor_does_not_resurrect_superseded_expired_card(sample, tmp_path):
    newer = build_sample(sample['mode'], tmp_path / '.codex-audit', successor=True)
    old_id = rt.fx.event_id(newer['events'][0])
    assert old_id not in {r['provenance']['source_event_ref'] for r in newer['projection']['records']}
    result = resolve(newer, newer['projection']['records'], '2026-11-05T00:00:00Z')
    assert len(result) == 2
    assert all(cards.card_status(r['stored_text'], '2026-11-05T00:00:00Z') == 'CURRENT' for r in result)

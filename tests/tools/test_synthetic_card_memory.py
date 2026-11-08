"""Synthetic closed-vocabulary knowledge card memory (lead-mind-closed-knowledge-cards-fable-20261107-v1).

Synthetic cards only: every card carries the ``synthetic`` tag, nothing is read from the bridge and no live content
is used. The cards are validated by the existing ``public_knowledge_card`` validator.
"""
import hashlib
import json
import re

import pytest

from sandbox.mind.step2 import public_knowledge_card as pkc
from sandbox.mind.step2 import synthetic_card_memory as scm
from tools.bridge_compact_view import event_id

LEAD, TOOLS, FABLE = 'codex-lead-1', 'codex-tools-1', 'fable-5'
IDENTITY = {'model': 'synthetic-bow', 'revision': 'a' * 40, 'weights_sha256': 'b' * 64}
DIM = 64
NOW = '2026-11-03T00:00:00Z'


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('ascii')


VOCABULARY = canonical({'schema': 'wd.mind-public-vocabulary.v1',
                        'components': ['mind.genesis', 'mind.continuation', 'bridge.writer', 'launcher',
                                       'mind.cards'],
                        'tags': ['synthetic', 'hex', 'cache', 'disk', 'monitor', 'genesis', 'pytest', 'dryrun']})
PATHS = canonical({'schema': 'wd.mind-public-paths.v1',
                   'paths': ['tools/wd_q4_genesis_cli.py', 'ops/windows/reboot/start-wd-agent.ps1']})
PINS = {'vocabulary_bytes': VOCABULARY, 'vocabulary_sha256': hashlib.sha256(VOCABULARY).hexdigest(),
        'path_allowlist_bytes': PATHS, 'path_allowlist_sha256': hashlib.sha256(PATHS).hexdigest()}


def card(agent, minute, kind, subject, outcome, nxt, tags, *, head=None, deps=None, supersedes=None, days=3,
         day=1):
    data = {'v': '1', 'kind': kind, 'subject': subject, 'outcome': outcome, 'next': nxt,
            'valid_until_utc': '2026-11-%02dT10:%02d:00Z' % (day + days, minute),
            'tags': ['synthetic'] + list(tags)}
    if head is not None:
        data['head'] = head
    if deps is not None:
        data['depends_on'] = deps
    if supersedes is not None:
        data['supersedes'] = supersedes
    return {'ts_utc': '2026-11-%02dT10:%02d:00Z' % (day, minute), 'agent': agent, 'type': 'status',
            'task_id': 'mind-public-knowledge-v1', 'status': 'info', 'to': '', 'message': '', 'paths': [],
            'write_scope': [], 'payload': {'public_knowledge': data}}


def corpus():
    return [
        card(LEAD, 0, 'merged', {'pr': 2046}, 'green', 'none', ['genesis'], head='1' * 40),
        card(TOOLS, 1, 'blocked', {'component': 'mind.genesis'}, 'red', 'needs_operator', ['genesis', 'disk']),
        card(FABLE, 2, 'test_result', {'pr': 2049}, 'green', 'needs_review', ['pytest'], head='2' * 40),
        card(LEAD, 3, 'available', {'component': 'bridge.writer'}, 'green', 'none', ['monitor']),
        card(TOOLS, 4, 'milestone', {'component': 'mind.continuation'}, 'partial', 'waits_dependency', ['hex'],
             deps=[{'component': 'mind.genesis'}]),
        card(FABLE, 5, 'handoff', {'repo_path': 'ops/windows/reboot/start-wd-agent.ps1'}, 'unknown', 'needs_fix',
             ['dryrun']),
        card(LEAD, 6, 'bundle_deployed', {'component': 'launcher'}, 'green', 'none', ['cache'], head='3' * 40),
        card(TOOLS, 7, 'landed_on_branch', {'repo_path': 'tools/wd_q4_genesis_cli.py'}, 'green', 'needs_review',
             ['genesis'], head='4' * 40),
        card(FABLE, 8, 'unblocked', {'component': 'mind.cards'}, 'green', 'none', ['hex', 'cache']),
    ]


def bow(text):
    vector = [0.0] * DIM
    for token in re.findall(r'[a-z0-9]{2,}', text.lower()):
        vector[int(hashlib.sha256(token.encode()).hexdigest(), 16) % DIM] += 1.0
    norm = sum(x * x for x in vector) ** 0.5 or 1.0
    return [x / norm for x in vector]


def encode(texts):
    np = pytest.importorskip('numpy')
    return np.array([bow(text) for text in texts], dtype=np.float32)


class Encoder:
    def query(self, text):
        np = pytest.importorskip('numpy')
        return np.array(bow(text), dtype=np.float32)


def project(events, now=NOW):
    return scm.project_synthetic_cards(events, now_utc=now, enabled=True, **PINS)


def test_default_off_is_inert(tmp_path):
    class NoTouch:
        def __getattribute__(self, name):
            raise AssertionError('OFF touched input')
    assert scm.project_synthetic_cards(NoTouch()) == {'status': 'OFF'}
    assert scm.build_synthetic_card_memory(NoTouch(), None, None, None, tmp_path / 'absent', 'x') == {'status': 'OFF'}
    assert scm.ask_synthetic_cards(tmp_path / 'absent', '0' * 64, None, 'q', NOW) == {'status': 'OFF'}
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize('enabled', ['yes', 1, None])
def test_enable_must_be_true(enabled):
    with pytest.raises(scm.CardMemoryRefused, match='enabled_invalid'):
        scm.project_synthetic_cards(corpus(), now_utc=NOW, enabled=enabled, **PINS)


@pytest.mark.parametrize('change', ['no_synthetic_tag', 'not_object', 'no_payload', 'tags_not_list', 'too_many'])
def test_anything_not_marked_synthetic_refuses_the_whole_call(change):
    events = corpus()
    if change == 'no_synthetic_tag':
        events[3]['payload']['public_knowledge']['tags'] = ['monitor']
    elif change == 'not_object':
        events[3] = 'a string'
    elif change == 'no_payload':
        del events[3]['payload']
    elif change == 'tags_not_list':
        events[3]['payload']['public_knowledge']['tags'] = 'synthetic'
    else:
        events = events * 500
    with pytest.raises(scm.CardMemoryRefused, match='live_input_refused|events_invalid'):
        project(events)


def test_vocabulary_without_the_synthetic_tag_refuses():
    vocabulary = canonical({'schema': 'wd.mind-public-vocabulary.v1', 'components': ['launcher'], 'tags': ['hex']})
    pins = dict(PINS, vocabulary_bytes=vocabulary, vocabulary_sha256=hashlib.sha256(vocabulary).hexdigest())
    with pytest.raises(scm.CardMemoryRefused, match='vocabulary_not_synthetic'):
        scm.project_synthetic_cards(corpus(), now_utc=NOW, enabled=True, **pins)


def test_closed_cards_become_records_with_the_rebuilt_line_and_source_ref():
    events = corpus()
    projection = project(events)
    assert projection['accepted'] == len(events) and projection['refused'] == {}
    assert projection['scope'] == scm.SCOPE and projection['synthetic'] is True and projection['authority'] == 'none'
    for event, record in zip(events, projection['records']):
        expected = pkc.validate(enabled=True, event=event, source_ref=event_id(event),
                                vocabulary_bytes=VOCABULARY, expected_vocabulary_sha256=PINS['vocabulary_sha256'],
                                path_allowlist_bytes=PATHS, expected_path_allowlist_sha256=PINS['path_allowlist_sha256'],
                                now_utc=NOW)
        assert record['stored_text'] == record['summary'] == expected['stored_text']
        assert record['provenance']['source_event_ref'] == event_id(event)
        assert record['provenance']['visibility'] == 'broadcast'
        assert record['meta'] == {'type': 'status', 'status': 'info', 'agent': event['agent'],
                                  'task_id': 'mind-public-knowledge-v1'}
    assert set(projection['sources']) == {event_id(e) for e in events}


@pytest.mark.parametrize('change', ['message', 'recipient', 'review_lane', 'verdict_kind', 'unknown_tag',
                                    'absolute_path', 'extra_card_key', 'routing_in_tags', 'over_seven_days',
                                    'secret_event_key'])
def test_unclosed_cards_are_refused_and_counted(change):
    canary = 'zq-card-canary'
    events = corpus()
    bad = card(LEAD, 30, 'available', {'component': 'launcher'}, 'green', 'none', [])
    data = bad['payload']['public_knowledge']
    if change == 'message':
        bad['message'] = canary
    elif change == 'recipient':
        bad['to'] = 'claude-rco-2'
    elif change == 'review_lane':
        bad['agent'] = 'claude-rco-1'
    elif change == 'verdict_kind':
        data['kind'] = 'approve'
    elif change == 'unknown_tag':
        data['tags'].append(canary)
    elif change == 'absolute_path':
        data['subject'] = {'repo_path': 'C:/Users/' + canary + '/notes.txt'}
    elif change == 'extra_card_key':
        data['text'] = canary
    elif change == 'routing_in_tags':
        data['tags'].append({'to_list': [canary]})
    elif change == 'over_seven_days':
        data['valid_until_utc'] = '2026-11-09T10:31:00Z'
    else:
        bad['secret'] = canary
    projection = project(events + [bad])
    assert projection['accepted'] == len(events) and projection['refused'] == {'card_refused': 1}
    assert canary not in json.dumps(projection)


def test_supersession_by_the_same_lane_is_accepted_and_by_another_lane_refused():
    events = corpus()
    newer = card(TOOLS, 20, 'unblocked', {'component': 'mind.genesis'}, 'green', 'none', ['genesis'],
                 supersedes=event_id(events[1]))
    foreign = card(LEAD, 21, 'unblocked', {'component': 'mind.genesis'}, 'green', 'none', ['genesis'],
                   supersedes=event_id(events[1]))
    projection = project(events + [newer, foreign])
    assert projection['accepted'] == len(events) + 1 and projection['refused'] == {'card_refused': 1}
    assert projection['superseded'] == [event_id(events[1])]


@pytest.mark.parametrize('change', ['stored_text', 'source_ref', 'vocabulary_pin', 'extra_record', 'source_card',
                                    'dropped_source', 'superseded_list', 'now_utc', 'extra_key'])
def test_validate_rederives_every_record(change):
    projection = project(corpus())
    scm.validate_card_projection(projection)
    bad = json.loads(json.dumps(projection))
    if change == 'stored_text':
        bad['records'][0]['stored_text'] = bad['records'][0]['summary'] = 'merged green codex-lead-1 pr:1 next:none'
    elif change == 'source_ref':
        bad['records'][0]['provenance']['source_event_ref'] = 'e' * 64
    elif change == 'vocabulary_pin':
        bad['vocabulary_sha256'] = '0' * 64
    elif change == 'extra_record':
        bad['records'].append(bad['records'][0])
        bad['accepted'] += 1
    elif change == 'source_card':
        ref = bad['records'][0]['provenance']['source_event_ref']
        bad['sources'][ref]['payload']['public_knowledge']['outcome'] = 'red'
    elif change == 'dropped_source':
        del bad['sources'][bad['records'][0]['provenance']['source_event_ref']]
    elif change == 'superseded_list':
        bad['superseded'] = [bad['records'][0]['provenance']['source_event_ref']]
    elif change == 'now_utc':
        bad['now_utc'] = '2026-11-01T00:00:00Z'
    else:
        bad['note'] = 'x'
    with pytest.raises(scm.CardMemoryRefused, match='projection_invalid'):
        scm.validate_card_projection(bad)


@pytest.fixture
def built(tmp_path):
    pytest.importorskip('faiss')
    events = corpus()
    projection = project(events)
    root = tmp_path / 'out'
    root.mkdir()
    result = scm.build_synthetic_card_memory(projection, encode, IDENTITY, DIM, root, 'cards', enabled=True)
    return {'dir': root / 'cards', 'memory_sha256': result['memory_sha256'], 'events': events,
            'projection': projection, 'result': result, 'root': root}


def test_end_to_end_answer_cites_a_current_closed_card(built):
    answer = scm.ask_synthetic_cards(built['dir'], built['memory_sha256'], Encoder(),
                                     'blocked red mind genesis needs operator disk', '2026-11-02T00:00:00Z',
                                     enabled=True)
    assert answer['status'] == 'ANSWER' and answer['results']
    first = answer['results'][0]
    assert set(first) == {'ref', 'text', 'time_utc', 'cell', 'source_ref_sha256', 'card_status', 'valid_until_utc',
                          'visibility', 'provenance'}
    assert first['source_ref_sha256'] == event_id(built['events'][1])
    assert first['text'].startswith('blocked red codex-tools-1 component:mind.genesis')
    assert first['card_status'] == 'CURRENT' and first['visibility'] == 'broadcast'
    assert first['provenance'] == 'SOURCE_HASH_ONLY'
    assert answer['coverage'] == {'cutoff_utc': built['result']['cutoff_utc']}


def test_expired_cards_are_not_returned_after_valid_until(built):
    answer = scm.ask_synthetic_cards(built['dir'], built['memory_sha256'], Encoder(),
                                     'blocked red mind genesis needs operator disk', '2026-11-05T00:00:00Z',
                                     enabled=True)
    # The whole synthetic corpus has expired; Q4 excludes it before ranking,
    # and must abstain instead of returning stale source content.
    assert answer['status'] == 'ABSTAIN'
    assert answer['reason'] == 'empty_cell'
    assert answer['results'] == []
    assert answer['coverage'] == {'cutoff_utc': built['result']['cutoff_utc']}


def test_a_superseded_card_is_marked_superseded(tmp_path):
    pytest.importorskip('faiss')
    events = corpus()
    newer = card(TOOLS, 20, 'unblocked', {'component': 'mind.genesis'}, 'green', 'none', ['genesis', 'disk'],
                 supersedes=event_id(events[1]))
    projection = project(events + [newer])
    root = tmp_path / 'out'
    root.mkdir()
    result = scm.build_synthetic_card_memory(projection, encode, IDENTITY, DIM, root, 'cards', enabled=True)
    answer = scm.ask_synthetic_cards(root / 'cards', result['memory_sha256'], Encoder(),
                                     'blocked red mind genesis needs operator disk', '2026-11-02T00:00:00Z',
                                     enabled=True)
    statuses = {r['source_ref_sha256']: r['card_status'] for r in answer['results']}
    assert statuses[event_id(events[1])] == 'SUPERSEDED'


def test_abstain_before_any_card_reports_coverage(built):
    answer = scm.ask_synthetic_cards(built['dir'], built['memory_sha256'], Encoder(), 'mind genesis',
                                     '2026-10-01T00:00:00Z', enabled=True)
    assert answer['status'] == 'ABSTAIN' and answer['results'] == []
    assert answer['coverage'] == {'cutoff_utc': built['result']['cutoff_utc']}


def test_ask_refuses_a_wrong_pin_or_a_tampered_projection(built):
    with pytest.raises(scm.CardMemoryRefused, match='memory_mismatch'):
        scm.ask_synthetic_cards(built['dir'], '0' * 64, Encoder(), 'genesis', '2026-11-02T00:00:00Z', enabled=True)
    path = built['dir'] / 'projection.json'
    path.write_bytes(path.read_bytes().replace(b'"green"', b'"red"', 1))
    with pytest.raises(scm.CardMemoryRefused, match='projection_mismatch'):
        scm.ask_synthetic_cards(built['dir'], built['memory_sha256'], Encoder(), 'genesis', '2026-11-02T00:00:00Z',
                                enabled=True)


def test_memory_has_its_own_scope_and_descriptor_schema(built):
    from sandbox.mind.step2 import shared_index_builder as sib
    manifest = json.loads((built['dir'] / 'memory.json').read_bytes())
    descriptor = json.loads((built['dir'] / 'descriptor.json').read_bytes())
    assert manifest['scope'] == scm.SCOPE != sib.SCOPE and manifest['synthetic'] is True
    assert descriptor['schema'] == scm.DESCRIPTOR_SCHEMA != sib.DESCRIPTOR_SCHEMA


def test_build_refuses_an_invalid_projection_before_writing(tmp_path):
    projection = project(corpus())
    projection['records'][0]['stored_text'] = 'changed'
    root = tmp_path / 'out'
    root.mkdir()
    with pytest.raises(scm.CardMemoryRefused, match='projection_invalid'):
        scm.build_synthetic_card_memory(projection, encode, IDENTITY, DIM, root, 'cards', enabled=True)
    assert list(root.iterdir()) == []


def test_a_supersedes_target_outside_the_batch_is_refused_and_duplicates_are_counted():
    events = corpus()
    orphan = card(TOOLS, 22, 'unblocked', {'component': 'mind.genesis'}, 'green', 'none', ['genesis'],
                  supersedes='f' * 64)
    projection = project(events + [orphan, json.loads(json.dumps(events[0]))])
    assert projection['accepted'] == len(events)
    assert projection['refused'] == {'card_refused': 1, 'duplicate_skipped': 1}
    assert projection['superseded'] == []


def test_a_card_not_yet_valid_at_projection_time_is_refused():
    events = corpus()
    projection = project(events, now='2026-11-01T10:04:00Z')
    assert projection['accepted'] == 5 and projection['refused'] == {'card_refused': 4}


def test_supersession_only_shows_once_the_newer_card_is_available(tmp_path):
    pytest.importorskip('faiss')
    events = corpus()
    newer = card(TOOLS, 20, 'unblocked', {'component': 'mind.genesis'}, 'green', 'none', ['genesis', 'disk'],
                 supersedes=event_id(events[1]), day=2)
    projection = project(events + [newer])
    root = tmp_path / 'out'
    root.mkdir()
    result = scm.build_synthetic_card_memory(projection, encode, IDENTITY, DIM, root, 'cards', enabled=True)
    answer = scm.ask_synthetic_cards(root / 'cards', result['memory_sha256'], Encoder(),
                                     'blocked red mind genesis needs operator disk', '2026-11-02T00:00:00Z',
                                     enabled=True)
    statuses = {r['source_ref_sha256']: r['card_status'] for r in answer['results']}
    assert statuses[event_id(events[1])] == 'CURRENT'
    assert event_id(newer) not in statuses


def test_ask_refuses_an_invalid_situation_time(built):
    with pytest.raises(scm.CardMemoryRefused, match='situation_time_invalid'):
        scm.ask_synthetic_cards(built['dir'], built['memory_sha256'], Encoder(), 'genesis', 'yesterday',
                                enabled=True)


def test_a_vocabulary_pin_mismatch_refuses_the_whole_call():
    with pytest.raises(scm.CardMemoryRefused, match='pin_mismatch'):
        scm.project_synthetic_cards(corpus(), now_utc=NOW, enabled=True,
                                    **dict(PINS, vocabulary_sha256='0' * 64))


def test_a_guarded_word_in_an_accepted_card_refuses_the_whole_projection():
    from tools.bridge_diff_privacy import PRIVATE_MARKERS
    word = next(m.lower() for m in PRIVATE_MARKERS if re.fullmatch(r'[a-z][a-z0-9_.-]{0,47}', m.lower()))
    vocabulary = canonical({'schema': 'wd.mind-public-vocabulary.v1', 'components': ['launcher', word],
                            'tags': ['synthetic']})
    pins = dict(PINS, vocabulary_bytes=vocabulary, vocabulary_sha256=hashlib.sha256(vocabulary).hexdigest())
    events = [card(LEAD, 0, 'available', {'component': word}, 'green', 'none', [])]
    with pytest.raises(scm.CardMemoryRefused, match='guarded_word'):
        scm.project_synthetic_cards(events, now_utc=NOW, enabled=True, **pins)


def test_ask_refuses_a_memory_whose_manifest_is_not_the_synthetic_card_scope(built):
    path = built['dir'] / 'memory.json'
    raw = path.read_bytes().replace(scm.SCOPE.encode(), b'wd.q4-shared-projected-events.v1')
    path.write_bytes(raw)
    with pytest.raises(scm.CardMemoryRefused, match='memory_invalid'):
        scm.ask_synthetic_cards(built['dir'], hashlib.sha256(raw).hexdigest(), Encoder(), 'genesis',
                                '2026-11-02T00:00:00Z', enabled=True)

"""Shared-route closed cards, p3-p5 (lead-mind-shared-cards-p3p5-fable-20261107-v1).

Synthetic data only: every card carries the ``synthetic`` tag, the bridge log is a synthetic file under tmp_path and
no installed bridge, policy or runtime is read. The existing v1-v6 readers and pins are not touched.
"""
import hashlib
import json
import re
from pathlib import Path

import pytest

from sandbox.mind.step2 import shared_card_projection as scp
from tools.bridge_compact_view import event_id

LEAD, TOOLS, FABLE = 'codex-lead-1', 'codex-tools-1', 'fable-5'
LANES = (LEAD, TOOLS, 'claude-rco-1', 'claude-rco-2', FABLE)
UUIDS = {lane: f'{i:08d}-1111-4111-8111-111111111111' for i, lane in enumerate(LANES, 1)}
IDENTITY = {'model': 'synthetic-bow', 'revision': 'a' * 40, 'weights_sha256': 'b' * 64}
DIM = 64
NOW = '2026-11-03T00:00:00Z'
REPO = Path(__file__).resolve().parents[2]


def raw(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


def sha(value):
    return hashlib.sha256(value).hexdigest()


VOCABULARY = raw({'schema': 'wd.mind-public-vocabulary.v1',
                  'components': ['mind.genesis', 'mind.continuation', 'bridge.writer', 'launcher', 'mind.cards'],
                  'tags': ['synthetic', 'hex', 'cache', 'disk', 'monitor', 'genesis', 'pytest', 'dryrun']})
PATHS = raw({'schema': 'wd.mind-public-paths.v1', 'paths': ['tools/wd_q4_genesis_cli.py']})
PINS = {'vocabulary_bytes': VOCABULARY, 'vocabulary_sha256': sha(VOCABULARY),
        'path_allowlist_bytes': PATHS, 'path_allowlist_sha256': sha(PATHS)}
NONREVIEW = raw({'schema': 'wd.q4-non-review-policy.v1', 'approved_tasks': ['mind-public-knowledge-v1']})
POLICY = raw({'schema': 'wd.mind-public-card-policy.v1', 'task_id': 'mind-public-knowledge-v1',
              'writers': [LEAD, TOOLS, FABLE]})
ROSTER = raw({'lanes': [{'agent': n, 'agent_uuid': UUIDS[n]} for n in LANES if n != TOOLS],
              'tools_supervisor': {'agent': TOOLS, 'agent_uuid': UUIDS[TOOLS]}})


def card(agent, minute, kind, subject, outcome, nxt, tags, *, head=None, deps=None, supersedes=None, days=3, day=1,
         writer=True):
    data = {'v': '1', 'kind': kind, 'subject': subject, 'outcome': outcome, 'next': nxt,
            'valid_until_utc': '2026-11-%02dT10:%02d:00Z' % (day + days, minute), 'tags': ['synthetic'] + list(tags)}
    if head is not None:
        data['head'] = head
    if deps is not None:
        data['depends_on'] = deps
    if supersedes is not None:
        data['supersedes'] = supersedes
    event = {'ts_utc': '2026-11-%02dT10:%02d:30Z' % (day, minute), 'agent': agent, 'agent_uuid': UUIDS[agent],
             'type': 'status', 'task_id': 'mind-public-knowledge-v1', 'status': 'info', 'to': '', 'message': '',
             'paths': [], 'write_scope': [], 'payload': {'public_knowledge': data}}
    if writer:
        event.update(ts_utc='2026-11-%02dT10:%02d:30.1234567Z' % (day, minute), severity='', run_id='synthetic-run',
                     pid=1234, cwd='C:\\Users\\ZQ-PRIVATE-CANARY\\project', session_id='synthetic-run')
    return event


def corpus():
    return [
        card(LEAD, 0, 'merged', {'pr': 2046}, 'green', 'none', ['genesis'], head='1' * 40),
        card(TOOLS, 1, 'blocked', {'component': 'mind.genesis'}, 'red', 'needs_operator', ['genesis', 'disk']),
        card(FABLE, 2, 'test_result', {'pr': 2049}, 'green', 'needs_review', ['pytest'], head='2' * 40),
        card(LEAD, 3, 'available', {'component': 'bridge.writer'}, 'green', 'none', ['monitor'], writer=False),
        card(TOOLS, 4, 'milestone', {'component': 'mind.continuation'}, 'partial', 'waits_dependency', ['hex'],
             deps=[{'component': 'mind.genesis'}, {'pr': 2046}]),
        card(LEAD, 6, 'bundle_deployed', {'component': 'launcher'}, 'green', 'none', ['cache'], head='3' * 40),
        card(FABLE, 8, 'unblocked', {'component': 'mind.cards'}, 'green', 'none', ['hex', 'cache']),
    ]


def project(events, now=NOW):
    return scp.project_shared_cards(events, now_utc=now, enabled=True, **PINS)


def transport_regex():
    """The resolved-route stored_text rule, read from the launcher file at this head (Invoke-WdQ4Query.ps1)."""
    text = (REPO / 'ops/windows/reboot/Invoke-WdQ4Query.ps1').read_text(encoding='utf-8')
    found = re.findall(r"\$item\.stored_text -cnotmatch '(\^\[[^']*\]\{1,320\})\\z'", text)
    assert len(found) == 1
    return re.compile(found[0] + r'\Z')


# --- default OFF, synthetic only -------------------------------------------------------------------------------

def test_default_off_is_inert(tmp_path):
    class NoTouch:
        def __getattribute__(self, name):
            raise AssertionError('OFF touched input')
    assert scp.project_shared_cards(NoTouch()) == {'status': 'OFF'}
    assert scp.verify_shared_card_sources(NoTouch()) == {'status': 'OFF'}
    assert scp.build_shared_card_memory(NoTouch(), None, None, None, tmp_path / 'absent', 'x') == {'status': 'OFF'}
    assert scp.resolve_shared_card_results(NoTouch(), NoTouch(), NoTouch()) == {'status': 'OFF'}
    assert scp.ask_shared_cards(tmp_path / 'absent', '0' * 64, None, 'q', NOW) == {'status': 'OFF'}
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize('enabled', ['yes', 1, None])
def test_enable_must_be_true(enabled):
    with pytest.raises(scp.SharedCardRefused, match='enabled_invalid'):
        scp.project_shared_cards(corpus(), now_utc=NOW, enabled=enabled, **PINS)


@pytest.mark.parametrize('change', ['no_synthetic_tag', 'not_object', 'too_many'])
def test_anything_not_marked_synthetic_refuses_the_whole_call(change):
    events = corpus()
    if change == 'no_synthetic_tag':
        events[2]['payload']['public_knowledge']['tags'] = ['pytest']
    elif change == 'not_object':
        events[2] = ['a list']
    else:
        events = events * 600
    with pytest.raises(scp.SharedCardRefused, match='live_input_refused|events_invalid'):
        project(events)


def test_existing_v6_pins_are_unchanged_and_not_reused():
    pytest.importorskip('faiss')  # the v6 builder module imports faiss
    from sandbox.mind.step2 import q4_descriptor, shared_index_builder as sib, shared_projection as sp
    from sandbox.mind.step2 import shared_result_resolver as resolver
    assert sp.SCHEMA == resolver.PROJECTION_SCHEMA == 'wd.bridge-mind-shared-projection.v6'
    assert sib.SCOPE == resolver.SCOPE == q4_descriptor.SHARED_SCOPE == 'wd.q4-shared-projected-events.v1'
    assert scp.SCHEMA == 'wd.bridge-mind-shared-card-projection.v7'
    assert scp.SCOPE not in (sib.SCOPE, 'wd.mind-synthetic-card-events.v1')
    assert scp.DESCRIPTOR_SCHEMA != sib.DESCRIPTOR_SCHEMA


# --- p3 projection: writer envelopes normalized, metadata never stored -------------------------------------------

def test_writer_envelopes_are_normalized_and_writer_metadata_is_never_stored():
    events = corpus()
    projection = project(events)
    assert projection['accepted'] == len(events) and projection['refused'] == {}
    dumped = json.dumps(projection)
    for secret in ('ZQ-PRIVATE-CANARY', 'agent_uuid', 'synthetic-run', '1234567', '"pid"', 'cwd'):
        assert secret not in dumped
    assert [r['provenance']['source_event_ref'] for r in projection['records']] == [event_id(e) for e in events]
    for source in projection['sources'].values():
        assert set(source) == {'ts_utc', 'agent', 'type', 'task_id', 'status', 'to', 'message', 'paths',
                               'write_scope', 'payload'}
        assert re.fullmatch(r'2026-11-01T10:\d\d:00Z', source['ts_utc'])


def test_the_record_time_is_the_card_minute_and_available_the_next_minute():
    record = project(corpus())['records'][0]
    assert record['time_utc'] == '2026-11-01T10:00:00.000000Z'
    assert record['available_utc'] == '2026-11-01T10:01:00.000000Z'


@pytest.mark.parametrize('change', ['message', 'recipient', 'review_lane', 'verdict_kind', 'unknown_tag',
                                    'extra_card_key', 'writer_unknown_key', 'writer_relative_cwd',
                                    'writer_capability', 'no_agent_uuid', 'over_seven_days', 'repo_path_subject'])
def test_unclosed_or_unsupported_cards_are_refused_and_counted(change):
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
    elif change == 'extra_card_key':
        data['text'] = canary
    elif change == 'writer_unknown_key':
        bad['note'] = canary
    elif change == 'writer_relative_cwd':
        bad['cwd'] = canary + '/x'
    elif change == 'writer_capability':
        bad['capabilities'] = ['vote']
    elif change == 'no_agent_uuid':
        del bad['agent_uuid']
    elif change == 'over_seven_days':
        data['valid_until_utc'] = '2026-11-09T10:31:00Z'
    else:
        data['subject'] = {'repo_path': 'tools/wd_q4_genesis_cli.py'}
    projection = project(events + [bad])
    assert projection['accepted'] == len(events) and projection['refused'] == {'card_refused': 1}
    assert canary not in json.dumps(projection)


# --- p4 transport-safe line ----------------------------------------------------------------------------------------

def test_every_line_passes_the_unchanged_resolved_transport_rule():
    rule = transport_regex()
    projection = project(corpus())
    for record in projection['records']:
        assert rule.fullmatch(record['stored_text']), record['stored_text']
        assert record['stored_text'] == record['summary']


def test_the_line_is_rebuilt_from_closed_fields_only():
    lines = [r['stored_text'] for r in project(corpus())['records']]
    assert lines[0] == ('merged green codex-lead-1 pr:2046 head:' + '1' * 40
                        + ' next:none tag:genesis tag:synthetic until:2026-11-04T10:00Z')
    assert lines[4] == ('milestone partial codex-tools-1 component:mind.continuation next:waits_dependency'
                        ' tag:hex tag:synthetic dep:component:mind.genesis dep:pr:2046 until:2026-11-04T10:04Z')


def test_a_line_over_the_transport_cap_is_refused():
    vocabulary = raw({'schema': 'wd.mind-public-vocabulary.v1', 'components': ['c' * 48],
                      'tags': ['synthetic'] + ['t%02d' % i + 'x' * 44 for i in range(7)]})
    pins = dict(PINS, vocabulary_bytes=vocabulary, vocabulary_sha256=sha(vocabulary))
    long_tags = ['t%02d' % i + 'x' * 44 for i in range(5)]
    events = [card(LEAD, 0, 'available', {'component': 'c' * 48}, 'green', 'none', long_tags)]
    out = scp.project_shared_cards(events, now_utc=NOW, enabled=True, **pins)
    assert out['accepted'] == 0 and out['refused'] == {'card_refused': 1}


# --- p5 truthful until, supersession, coverage ---------------------------------------------------------------------

@pytest.mark.parametrize('line,now,expected', [
    ('merged green codex-lead-1 pr:1 next:none tag:synthetic until:2026-11-04T10:00Z', '2026-11-04T09:59:59Z',
     'CURRENT'),
    ('merged green codex-lead-1 pr:1 next:none tag:synthetic until:2026-11-04T10:00Z', '2026-11-04T10:00:00Z',
     'STALE'),
    ('merged green codex-lead-1 pr:1 next:none tag:synthetic', '2026-11-04T09:00:00Z', 'UNKNOWN'),
    ('merged green codex-lead-1 pr:1 until:2026-11-04T10:00Z next:none', '2026-11-04T09:00:00Z', 'UNKNOWN'),
    ('merged green codex-lead-1 pr:1 next:none until:2026-13-04T10:00Z', '2026-11-04T09:00:00Z', 'UNKNOWN'),
    ('merged green codex-lead-1 pr:1 next:none until:2026-11-04T10:00Z', 'not a time', 'UNKNOWN'),
])
def test_card_status_from_the_line(line, now, expected):
    assert scp.card_status(line, now) == expected


def test_a_superseded_card_is_left_out_and_listed():
    events = corpus()
    newer = card(TOOLS, 20, 'unblocked', {'component': 'mind.genesis'}, 'green', 'none', ['genesis'],
                 supersedes=event_id(events[1]))
    projection = project(events + [newer])
    refs = [r['provenance']['source_event_ref'] for r in projection['records']]
    assert event_id(events[1]) not in refs and event_id(newer) in refs
    assert projection['superseded'] == [event_id(events[1])]
    assert projection['accepted'] == len(events) and projection['refused'] == {'superseded_excluded': 1}


def test_supersession_by_another_lane_or_outside_the_batch_is_refused():
    events = corpus()
    foreign = card(LEAD, 21, 'unblocked', {'component': 'mind.genesis'}, 'green', 'none', [],
                   supersedes=event_id(events[1]))
    orphan = card(TOOLS, 22, 'unblocked', {'component': 'mind.genesis'}, 'green', 'none', [], supersedes='f' * 64)
    projection = project(events + [foreign, orphan])
    assert projection['accepted'] == len(events) and projection['refused'] == {'card_refused': 2}
    assert projection['superseded'] == []


def test_coverage_names_the_cutoff_and_unknown_completeness():
    projection = project(corpus())
    assert projection['coverage'] == {'cutoff_utc': '2026-11-01T10:09:00.000000Z', 'completeness': 'UNKNOWN'}


# --- validation re-derives everything ------------------------------------------------------------------------------

@pytest.mark.parametrize('change', ['stored_text', 'vocabulary_pin', 'extra_record', 'source_card',
                                    'dropped_source', 'superseded_list', 'now_utc', 'coverage', 'extra_key',
                                    'refused_token'])
def test_validate_rederives_every_record(change):
    events = corpus()
    newer = card(TOOLS, 20, 'unblocked', {'component': 'mind.genesis'}, 'green', 'none', ['genesis'],
                 supersedes=event_id(events[1]))
    projection = project(events + [newer])
    scp.validate_shared_card_projection(projection)
    bad = json.loads(json.dumps(projection))
    first = bad['records'][0]
    if change == 'stored_text':
        first['stored_text'] = first['summary'] = 'merged green codex-lead-1 pr:1 next:none'
    elif change == 'vocabulary_pin':
        bad['vocabulary_sha256'] = '0' * 64
    elif change == 'extra_record':
        bad['records'].append(first)
        bad['accepted'] += 1
    elif change == 'source_card':
        bad['sources'][first['provenance']['source_event_ref']]['payload']['public_knowledge']['outcome'] = 'red'
    elif change == 'dropped_source':
        del bad['sources'][event_id(events[1])]
    elif change == 'superseded_list':
        bad['superseded'] = []
    elif change == 'now_utc':
        bad['now_utc'] = '2026-11-01T00:00:00Z'
    elif change == 'coverage':
        bad['coverage']['completeness'] = 'COMPLETE'
    elif change == 'extra_key':
        bad['note'] = 'x'
    else:
        bad['refused']['free text'] = 1
    with pytest.raises(scp.SharedCardRefused, match='projection_invalid'):
        scp.validate_shared_card_projection(bad)


# --- source verification through the existing prefix verifier ------------------------------------------------------

@pytest.fixture
def source(tmp_path):
    from waggledance.core import bridge_log_reader as bridge
    path, generation = tmp_path / 'events.jsonl', tmp_path / 'generation.json'
    generation.write_bytes(raw({'generation': 'synthetic-g1'}))

    def make(rows):
        content = b''.join(raw(row) + b'\n' for row in rows)
        path.write_bytes(content)
        cursor = bridge.read_bridge_log(str(path), generation_path=str(generation)).candidate_cursor
        return dict(source_path=str(path), generation_path=str(generation), end_cursor=cursor,
                    expected_prefix_sha256=sha(content), nonreview_policy_bytes=NONREVIEW,
                    nonreview_policy_sha256=sha(NONREVIEW), card_policy_bytes=POLICY, card_policy_sha256=sha(POLICY),
                    roster_bytes=ROSTER, roster_sha256=sha(ROSTER))
    return make


def test_sources_verify_within_a_pinned_synthetic_prefix(source):
    events = corpus()
    newer = card(TOOLS, 20, 'unblocked', {'component': 'mind.genesis'}, 'green', 'none', ['genesis'],
                 supersedes=event_id(events[1]))
    projection = project(events + [newer], now='2026-11-06T00:00:00Z')
    out = scp.verify_shared_card_sources(projection, enabled=True, **source(events + [newer]))
    assert out == {'status': 'MATCH_WITHIN_PREFIX', 'cards': len(events) + 1, 'completeness': 'UNKNOWN'}


@pytest.mark.parametrize('count',[1,None])
def test_source_verification_parses_one_prefix_for_all_cards(source, monkeypatch, count):
    from tools import wd_q4_public_card_prefix_verifier as verifier
    events=corpus()[:count];projection=project(events);args=source(events)
    original=verifier._read;calls=[]
    def counted(*a,**kw):
        calls.append(1)
        return original(*a,**kw)
    monkeypatch.setattr(verifier,'_read',counted)
    assert scp.verify_shared_card_sources(projection,enabled=True,**args)=={
        'status':'MATCH_WITHIN_PREFIX','cards':len(events),'completeness':'UNKNOWN'}
    assert len(calls)==1


@pytest.mark.parametrize('mutation',['rewrite','replace','generation','append'])
def test_source_rechecked_after_all_card_checks(source,monkeypatch,mutation):
    from tools import wd_q4_public_card_prefix_verifier as verifier
    from pathlib import Path
    events=corpus();projection=project(events);args=source(events)
    original=verifier._check;calls=[]
    def mutate(*a,**kw):
        result=original(*a,**kw)
        calls.append(1)
        if len(calls)!=len(projection['sources']): return result
        file=Path(args['source_path'])
        if mutation=='rewrite':
            data=file.read_bytes();file.write_bytes(b'!' + data[1:])
        elif mutation=='replace':
            data=file.read_bytes();file.rename(file.with_name('old-synthetic.jsonl'));file.write_bytes(data)
        elif mutation=='generation':
            Path(args['generation_path']).write_bytes(b'{"generation":"other"}')
        else:
            with file.open('ab') as stream:stream.write(b'{"task_id":"unrelated"}\n')
        return result
    monkeypatch.setattr(verifier,'_check',mutate)
    if mutation=='append':
        assert scp.verify_shared_card_sources(projection,enabled=True,**args)['status']=='MATCH_WITHIN_PREFIX'
    else:
        with pytest.raises(scp.SharedCardRefused,match='source_unverified'):
            scp.verify_shared_card_sources(projection,enabled=True,**args)


@pytest.mark.parametrize('change', ['missing_row', 'changed_row', 'wrong_uuid', 'writer_not_in_policy',
                                    'tampered_ref'])
def test_source_verification_fails_closed(source, change):
    events = corpus()
    projection = project(events)
    rows = [json.loads(json.dumps(e)) for e in events]
    args = None
    if change == 'missing_row':
        rows = rows[1:]
    elif change == 'changed_row':
        rows[0]['cwd'] = 'C:\\Users\\OTHER\\project'
    elif change == 'wrong_uuid':
        rows[2]['agent_uuid'] = UUIDS[LEAD]
        projection = project([rows[i] if i == 2 else e for i, e in enumerate(events)])
    elif change == 'tampered_ref':
        # an original source id is a hash claim: re-derivation cannot see a swap, source verification must
        first = projection['records'][0]
        old = first['provenance']['source_event_ref']
        first['provenance']['source_event_ref'] = 'e' * 64
        ref = scp.record_ref(first['stored_text'], 'e' * 64)
        first['provenance']['event_ref'], first['record_id'] = ref, 'card:' + ref
        projection['sources']['e' * 64] = projection['sources'].pop(old)
        projection = json.loads(json.dumps(projection))
        scp.validate_shared_card_projection(projection)  # a consistent swap re-derives cleanly
    if change == 'writer_not_in_policy':
        policy = raw({'schema': 'wd.mind-public-card-policy.v1', 'task_id': 'mind-public-knowledge-v1',
                      'writers': [LEAD, TOOLS]})
        args = dict(source(rows), card_policy_bytes=policy, card_policy_sha256=sha(policy))
    with pytest.raises(scp.SharedCardRefused, match='source_unverified'):
        scp.verify_shared_card_sources(projection, enabled=True, **(args or source(rows)))


# --- build, resolve to the transport shape, ask ---------------------------------------------------------------------

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


@pytest.fixture
def built(tmp_path):
    pytest.importorskip('faiss')
    events = corpus()
    projection = project(events)
    root = tmp_path / 'out'
    root.mkdir()
    result = scp.build_shared_card_memory(projection, encode, IDENTITY, DIM, root, 'cards', enabled=True)
    return {'dir': root / 'cards', 'memory_sha256': result['memory_sha256'], 'events': events,
            'projection': projection, 'result': result}


def test_memory_has_its_own_scope_descriptor_and_projection_schema(built):
    manifest = json.loads((built['dir'] / 'memory.json').read_bytes())
    descriptor = json.loads((built['dir'] / 'descriptor.json').read_bytes())
    assert manifest['scope'] == descriptor['scope'] == scp.SCOPE and manifest['projection_schema'] == scp.SCHEMA
    assert descriptor['schema'] == scp.DESCRIPTOR_SCHEMA
    assert built['result']['coverage'] == built['projection']['coverage']


def test_the_existing_v6_resolver_refuses_the_card_memory(built):
    from sandbox.mind.step2 import shared_result_resolver as resolver
    manifest = (built['dir'] / 'memory.json').read_bytes()
    projection = (built['dir'] / 'projection.json').read_bytes()
    with pytest.raises(resolver.ResolveRefused):
        resolver.resolve_results(memory_manifest=manifest, expected_memory_sha256=built['memory_sha256'],
                                 projection_bytes=projection, results=[], query_utc='2026-11-02T00:00:00Z',
                                 approved_tasks=frozenset({'mind-public-knowledge-v1'}))


def test_ask_answers_in_the_resolved_transport_shape_with_status_and_coverage(built):
    rule = transport_regex()
    answer = scp.ask_shared_cards(built['dir'], built['memory_sha256'], Encoder(),
                                  'blocked red mind genesis needs operator disk', '2026-11-02T00:00:00Z',
                                  enabled=True)
    assert answer['status'] == 'ANSWER' and answer['results']
    assert answer['coverage'] == {'cutoff_utc': built['result']['coverage']['cutoff_utc'], 'completeness': 'UNKNOWN'}
    by_ref = {sha(r['provenance']['event_ref'].encode()): r for r in built['projection']['records']}
    for item in answer['results']:
        assert set(item) == {'canonical_event', 'doc_id_sha256', 'ref_kind', 'ref_sha256', 'stored_text',
                             'card_status'}
        assert item['ref_kind'] == 'sha256_of_projected_metadata' and item['canonical_event'] is False
        assert rule.fullmatch(item['stored_text'])
        record = by_ref[item['ref_sha256']]
        assert item['doc_id_sha256'] == sha(record['record_id'].encode())
        assert item['stored_text'] == record['stored_text']
        assert item['card_status'] == scp.card_status(item['stored_text'], '2026-11-02T00:00:00Z') == 'CURRENT'
    blocked = [i for i in answer['results'] if i['stored_text'].startswith('blocked red codex-tools-1')]
    assert blocked


def test_ask_abstains_when_all_cards_have_expired(built):
    answer = scp.ask_shared_cards(built['dir'], built['memory_sha256'], Encoder(),
                                  'blocked red mind genesis needs operator disk', '2026-11-05T00:00:00Z',
                                  enabled=True)
    assert answer['status'] == 'ABSTAIN' and answer['results'] == []


def test_built_card_memory_answers_only_current_cards_in_mixed_corpus(tmp_path):
    events = corpus()
    for event in events[2:]:
        event['payload']['public_knowledge']['valid_until_utc'] = '2026-11-07T10:00:00Z'
    projection = project(events)
    root = tmp_path / 'out'
    root.mkdir()
    built = scp.build_shared_card_memory(projection, encode, IDENTITY, DIM, root, 'mixed', enabled=True)
    answer = scp.ask_shared_cards(root / 'mixed', built['memory_sha256'], Encoder(),
                                  'mind synthetic red', '2026-11-05T00:00:00Z', enabled=True)
    assert answer['status'] == 'ANSWER' and answer['results']
    assert {item['card_status'] for item in answer['results']} == {'CURRENT'}
    stale_refs = {sha(record['provenance']['event_ref'].encode()) for record in projection['records']
                  if scp.card_status(record['stored_text'], '2026-11-05T00:00:00Z') == 'STALE'}
    assert stale_refs and all(item['ref_sha256'] not in stale_refs for item in answer['results'])


def test_abstain_before_any_card_still_reports_coverage(built):
    answer = scp.ask_shared_cards(built['dir'], built['memory_sha256'], Encoder(), 'mind genesis',
                                  '2026-10-01T00:00:00Z', enabled=True)
    assert answer['status'] == 'ABSTAIN' and answer['results'] == []
    assert answer['coverage']['completeness'] == 'UNKNOWN'


def test_resolve_needs_hash_only_items_visible_at_the_query_time(built):
    record = built['projection']['records'][0]
    item = {'ref_sha256': sha(record['provenance']['event_ref'].encode()),
            'doc_id_sha256': sha(record['record_id'].encode()), 'ref_kind': 'sha256_of_projected_metadata',
            'canonical_event': False}
    out = scp.resolve_shared_card_results(built['projection'], [item], '2026-11-02T00:00:00Z', enabled=True)
    assert out == [dict(item, stored_text=record['stored_text'])]
    for bad, when in ((dict(item, ref_sha256='0' * 64), '2026-11-02T00:00:00Z'),
                      (dict(item, extra=1), '2026-11-02T00:00:00Z'),
                      (item, '2026-11-01T10:00:30Z')):
        with pytest.raises(scp.SharedCardRefused, match='resolution_refused'):
            scp.resolve_shared_card_results(built['projection'], [bad], when, enabled=True)


def test_ask_refuses_a_wrong_pin_or_a_tampered_projection(built):
    with pytest.raises(scp.SharedCardRefused, match='memory_mismatch'):
        scp.ask_shared_cards(built['dir'], '0' * 64, Encoder(), 'genesis', '2026-11-02T00:00:00Z', enabled=True)
    path = built['dir'] / 'projection.json'
    path.write_bytes(path.read_bytes().replace(b'"green"', b'"red"', 1))
    with pytest.raises(scp.SharedCardRefused, match='projection_mismatch'):
        scp.ask_shared_cards(built['dir'], built['memory_sha256'], Encoder(), 'genesis', '2026-11-02T00:00:00Z',
                             enabled=True)


def test_build_refuses_an_invalid_projection_before_writing(tmp_path):
    projection = project(corpus())
    projection['records'][0]['stored_text'] = 'changed'
    root = tmp_path / 'out'
    root.mkdir()
    with pytest.raises(scp.SharedCardRefused, match='projection_invalid'):
        scp.build_shared_card_memory(projection, encode, IDENTITY, DIM, root, 'cards', enabled=True)
    assert list(root.iterdir()) == []


def test_a_successor_of_a_refused_predecessor_is_refused_too():
    vocabulary = raw({'schema': 'wd.mind-public-vocabulary.v1', 'components': ['mind.genesis'],
                      'tags': ['synthetic', 'genesis'] + ['t%02d' % i + 'x' * 44 for i in range(5)]})
    pins = dict(PINS, vocabulary_bytes=vocabulary, vocabulary_sha256=sha(vocabulary))
    long_tags = ['t%02d' % i + 'x' * 44 for i in range(5)]
    older = card(TOOLS, 1, 'blocked', {'component': 'mind.genesis'}, 'red', 'none', long_tags)
    newer = card(TOOLS, 20, 'unblocked', {'component': 'mind.genesis'}, 'green', 'none', ['genesis'],
                 supersedes=event_id(older))
    out = scp.project_shared_cards([older, newer], now_utc=NOW, enabled=True, **pins)
    assert out['accepted'] == 0 and out['refused'] == {'card_refused': 2} and out['superseded'] == []
    assert out['coverage'] == {'cutoff_utc': None, 'completeness': 'UNKNOWN'}


def test_a_vocabulary_without_the_synthetic_tag_or_a_wrong_pin_refuses():
    vocabulary = raw({'schema': 'wd.mind-public-vocabulary.v1', 'components': ['launcher'], 'tags': ['hex']})
    with pytest.raises(scp.SharedCardRefused, match='vocabulary_not_synthetic'):
        scp.project_shared_cards(corpus(), now_utc=NOW, enabled=True,
                                 **dict(PINS, vocabulary_bytes=vocabulary, vocabulary_sha256=sha(vocabulary)))
    with pytest.raises(scp.SharedCardRefused, match='pin_mismatch'):
        scp.project_shared_cards(corpus(), now_utc=NOW, enabled=True, **dict(PINS, vocabulary_sha256='0' * 64))


@pytest.mark.parametrize('value', ['bad uuid', 'C:/Users/x', '', 7])
def test_an_unsafe_agent_uuid_is_refused(value):
    events = corpus()
    events[0]['agent_uuid'] = value
    projection = project(events)
    assert projection['accepted'] == len(events) - 1 and projection['refused'] == {'card_refused': 1}


def test_duplicates_and_same_card_with_other_writer_metadata_are_one_record():
    events = corpus()
    twin = json.loads(json.dumps(events[0]))
    twin['cwd'] = 'C:\\Users\\OTHER\\project'
    twin['ts_utc'] = twin['ts_utc'].replace(':30.1234567Z', ':45.0000000Z')
    projection = project(events + [json.loads(json.dumps(events[1])), twin])
    assert projection['accepted'] == len(events) and projection['refused'] == {'duplicate_skipped': 2}
    assert [r['provenance']['source_event_ref'] for r in projection['records']] == [event_id(e) for e in events]


def test_a_guarded_word_in_an_accepted_card_refuses_the_whole_projection():
    from tools.bridge_diff_privacy import PRIVATE_MARKERS
    word = next(m.lower() for m in PRIVATE_MARKERS if re.fullmatch(r'[a-z][a-z0-9_.-]{0,47}', m.lower()))
    vocabulary = raw({'schema': 'wd.mind-public-vocabulary.v1', 'components': ['launcher', word],
                      'tags': ['synthetic']})
    pins = dict(PINS, vocabulary_bytes=vocabulary, vocabulary_sha256=sha(vocabulary))
    with pytest.raises(scp.SharedCardRefused, match='guarded_word'):
        scp.project_shared_cards([card(LEAD, 0, 'available', {'component': word}, 'green', 'none', [])],
                                 now_utc=NOW, enabled=True, **pins)


def test_validate_refuses_two_sources_for_the_same_normalized_card():
    projection = project(corpus())
    bad = json.loads(json.dumps(projection))
    first = bad['records'][0]
    twin = json.loads(json.dumps(first))
    ref = scp.record_ref(twin['stored_text'], 'e' * 64)
    twin['provenance']['source_event_ref'], twin['provenance']['event_ref'], twin['record_id'] = 'e' * 64, ref, 'card:' + ref
    bad['records'].insert(1, twin)
    bad['sources']['e' * 64] = json.loads(json.dumps(bad['sources'][first['provenance']['source_event_ref']]))
    bad['accepted'] += 1
    with pytest.raises(scp.SharedCardRefused, match='projection_invalid'):
        scp.validate_shared_card_projection(bad)

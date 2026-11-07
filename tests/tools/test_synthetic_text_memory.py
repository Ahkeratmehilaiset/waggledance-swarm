"""Synthetic-only text memory candidate (lead-mind-useful-text-synthetic-20261107-v1): synthetic events only."""
import hashlib
import json
import re

import pytest

from sandbox.mind.step2 import synthetic_text_memory as stm

LEAD, TOOLS, FABLE = 'codex-lead-1', 'codex-tools-1', 'fable-5'
IDENTITY = {'model': 'synthetic-bow', 'revision': 'a' * 40, 'weights_sha256': 'b' * 64}
DIM = 64
TOPICS = [
    (FABLE, 'done', 'done', 'synthetic-genesis',
     'Genesis skip progress returns exit code four with only the receipt digest; the same config resumes later.'),
    (TOOLS, 'message', 'info', 'synthetic-disk',
     'The disk space dips on C come from backup shadow storage, not from test runs.'),
    (LEAD, 'message', 'info', 'synthetic-monitor',
     'The bridge monitor must be re-armed with the native Monitor tool when it expires.'),
    (FABLE, 'milestone', 'milestone', 'synthetic-powershell',
     'PowerShell variable names are case insensitive so a local can shadow a parameter.'),
    (TOOLS, 'progress', 'in_progress', 'synthetic-pytest',
     'Pytest basetemp and TEMP must point under the lane worktree audit folder.'),
    (LEAD, 'handoff', 'handoff', 'synthetic-continuation',
     'Continuation routes must drop the genesis skip key before the runner accepts them.'),
    (TOOLS, 'test', 'done', 'synthetic-closure',
     'Package closure test lists every bundled bridge code file and its hash.'),
    (FABLE, 'done', 'done', 'synthetic-launcher',
     'Launcher DryRun must not write any diagnostics file.'),
]


def event(agent, kind, status, task, message, minute=0, **extra):
    value = {'ts_utc': '2026-11-01T10:%02d:30Z' % minute, 'agent': agent, 'type': kind, 'status': status,
             'task_id': task, 'to': '', 'message': message}
    value.update(extra)
    return value


def corpus():
    return [event(*row, minute=i) for i, row in enumerate(TOPICS)]


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


def test_default_off_does_nothing(tmp_path):
    assert stm.project_synthetic_text(corpus()) == {'status': 'OFF'}
    assert stm.build_synthetic_text_memory(None, None, None, None, tmp_path / 'absent', 'x') == {'status': 'OFF'}
    assert stm.ask_synthetic_text(tmp_path / 'absent', '0' * 64, None, 'q', '2026-11-02T00:00:00Z') == \
        {'status': 'OFF'}
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize('enabled', ['yes', 1, None])
def test_enable_must_be_true(enabled):
    with pytest.raises(stm.TextRefused, match='enabled_invalid'):
        stm.project_synthetic_text(corpus(), enabled=enabled)


@pytest.mark.parametrize('task', ['lead-mind-production-20261031', 'synthetic', 'Synthetic-x', 'synthetic-x y', 7])
def test_any_non_synthetic_task_refuses_the_whole_call(task):
    events = corpus() + [event(LEAD, 'message', 'info', task, 'a real looking message', minute=30)]
    with pytest.raises(stm.TextRefused, match='live_input_refused'):
        stm.project_synthetic_text(events, enabled=True)


def test_review_rows_bookkeeping_and_empty_messages_never_stored():
    canary = 'zq-review-canary'
    events = [event(FABLE, 'message', 'info', 'synthetic-ok', 'kept text about synthetic retrieval'),
              event('claude-rco-1', 'message', 'info', 'synthetic-r', canary + ' rco message'),
              event('claude-rco-2', 'done', 'done', 'synthetic-r', canary + ' rco done'),
              event(LEAD, 'decision', 'done', 'synthetic-r', canary + ' decision'),
              event(LEAD, 'finding', 'info', 'synthetic-r', canary + ' finding'),
              event(LEAD, 'rco_pass', 'info', 'synthetic-r', canary + ' pass'),
              event(LEAD, 'message', 'info', 'synthetic-r/pr-branch', canary + ' branch'),
              event(LEAD, 'claim', 'active', 'synthetic-r', canary + ' claim'),
              event(LEAD, 'release', 'released', 'synthetic-r', canary + ' release'),
              event(LEAD, 'wake_request', 'requested', 'synthetic-r', canary + ' wake'),
              event(LEAD, 'message', 'info', 'synthetic-r', ''),
              event(LEAD, 'message', 'info', 'synthetic-r', '   \n  ')]
    projection = stm.project_synthetic_text(events, enabled=True)
    assert projection['accepted'] == 1 and len(projection['records']) == 1
    assert projection['refused'] == {'bookkeeping_skipped': 3, 'message_empty': 2, 'pr_branch_task_excluded': 1,
                                     'review_lane_excluded': 2, 'type_not_allowed': 3}
    assert canary not in json.dumps(projection)
    assert projection['records'][0]['stored_text'] == \
        'message info fable-5 synthetic-ok\nkept text about synthetic retrieval'


@pytest.mark.parametrize('line', ['see C:\\Users\\someone\\notes.txt', 'see c:/work/file.txt', 'see /home/someone/x',
                                  'see /Users/someone/x', 'see \\\\server\\share', 'password=abc', 'api_key: abc',
                                  'token: abc', 'the secret is abc', 'the sealed answer is 4', 'from the gold set',
                                  'a heldout case', 'the judge said', 'PRIVATE' + '_MARKER here', '%USERPROFILE%\\x',
                                  'Bearer abcdef', 'ghp_' + 'a' * 20])
def test_guarded_line_cuts_text_or_refuses_record(line):
    cut = stm.project_synthetic_text([event(FABLE, 'message', 'info', 'synthetic-a', 'first useful line\n' + line +
                                            '\nlater useful line')], enabled=True)
    assert [r['stored_text'] for r in cut['records']] == ['message info fable-5 synthetic-a\nfirst useful line']
    assert cut['records'][0]['meta']['text_cut'] is True
    assert line not in json.dumps(cut)
    refused = stm.project_synthetic_text([event(FABLE, 'message', 'info', 'synthetic-a', line + '\nlater line')],
                                         enabled=True)
    assert refused['records'] == [] and refused['refused'] == {'text_guarded': 1}
    assert line not in json.dumps(refused)


def test_text_is_capped_and_unread_fields_never_stored():
    canary = 'zq-unread-canary'
    long_message = '\n'.join('line %04d about synthetic memory' % i for i in range(400))
    projection = stm.project_synthetic_text([event(FABLE, 'message', 'info', 'synthetic-a', long_message,
        payload={'note': canary}, paths=[canary], write_scope=[canary], cwd=canary, agent_uuid=canary,
        session_id=canary, run_id=canary, pr=12, head='c' * 40)], enabled=True)
    record = projection['records'][0]
    body = record['stored_text'].split('\n', 1)[1]
    assert len(body) <= stm.TEXT_CAP and long_message.startswith(body) and record['meta']['text_cut'] is True
    assert canary not in json.dumps(projection) and 'c' * 40 not in json.dumps(projection)
    assert record['stored_text'] == record['summary']
    assert record['provenance']['canonical_event'] is False


def test_record_ref_binds_text_and_fields():
    one = stm.project_synthetic_text([event(FABLE, 'message', 'info', 'synthetic-a', 'alpha text')], enabled=True)
    two = stm.project_synthetic_text([event(FABLE, 'message', 'info', 'synthetic-a', 'beta text')], enabled=True)
    a, b = one['records'][0], two['records'][0]
    assert a['provenance']['event_ref'] != b['provenance']['event_ref']
    assert a['record_id'] == 'text:' + a['provenance']['event_ref']
    assert stm.record_ref(a) == a['provenance']['event_ref']


@pytest.fixture
def built(tmp_path):
    pytest.importorskip('numpy')
    pytest.importorskip('faiss')
    projection = stm.project_synthetic_text(corpus(), enabled=True)
    root = tmp_path / 'root'
    root.mkdir()
    out = stm.build_synthetic_text_memory(projection, encode, IDENTITY, DIM, root, 'synthetic-text', enabled=True)
    return {'projection': projection, 'root': root, 'dir': root / 'synthetic-text', **out}


def test_end_to_end_cited_retrieval(built):
    disk = next(r for r in built['projection']['records'] if r['meta']['task_id'] == 'synthetic-disk')
    answer = stm.ask_synthetic_text(built['dir'], built['memory_sha256'], Encoder(),
                                    'where do the disk space dips on C come from', '2026-11-02T00:00:00Z',
                                    enabled=True)
    assert answer['status'] == 'ANSWER', answer
    first = answer['results'][0]
    assert first['ref'] == disk['provenance']['event_ref'] and first['text'] == disk['stored_text']
    assert 'backup shadow storage' in first['text']
    assert set(first) == {'ref', 'text', 'time_utc', 'cell', 'kind', 'source_event_ref'}
    assert first['source_event_ref'] == disk['provenance']['source_event_ref']
    assert answer['source_verified'] is False
    before = stm.ask_synthetic_text(built['dir'], built['memory_sha256'], Encoder(),
                                    'where do the disk space dips on C come from', '2026-11-01T09:00:00Z',
                                    enabled=True)
    assert before['status'] == 'ABSTAIN' and before['results'] == []


def test_memory_is_not_a_production_q4_memory(built):
    from sandbox.mind.step2 import shared_index_builder as builder
    memory = json.loads((built['dir'] / 'memory.json').read_bytes())
    descriptor = json.loads((built['dir'] / 'descriptor.json').read_bytes())
    assert memory['scope'] == stm.SCOPE != builder.SCOPE and memory['synthetic'] is True
    assert descriptor['schema'] == stm.DESCRIPTOR_SCHEMA != builder.DESCRIPTOR_SCHEMA
    assert descriptor['scope'] == stm.SCOPE and built['descriptor'] == descriptor
    assert hashlib.sha256((built['dir'] / 'memory.json').read_bytes()).hexdigest() == built['memory_sha256']


def test_ask_refuses_wrong_pin_or_tampered_projection(built):
    with pytest.raises(stm.TextRefused, match='memory_mismatch'):
        stm.ask_synthetic_text(built['dir'], '0' * 64, Encoder(), 'disk space', '2026-11-02T00:00:00Z', enabled=True)
    path = built['dir'] / 'projection.json'
    raw = path.read_bytes()
    path.write_bytes(raw.replace(b'backup shadow storage', b'backup shadow STORAGE'))
    with pytest.raises(stm.TextRefused, match='projection_mismatch'):
        stm.ask_synthetic_text(built['dir'], built['memory_sha256'], Encoder(), 'disk space', '2026-11-02T00:00:00Z',
                               enabled=True)


def test_build_refuses_foreign_or_altered_projection(tmp_path):
    pytest.importorskip('numpy')
    from sandbox.mind.step2 import shared_projection as sp
    root = tmp_path / 'root'
    root.mkdir()
    shared = sp.project_events([event(FABLE, 'message', 'info', 'synthetic-a', 'x')], frozenset({'synthetic-a'}))
    with pytest.raises(stm.TextRefused, match='projection_invalid'):
        stm.build_synthetic_text_memory(shared, encode, IDENTITY, DIM, root, 'a', enabled=True)
    projection = stm.project_synthetic_text(corpus(), enabled=True)
    altered = json.loads(json.dumps(projection))
    altered['records'][0]['stored_text'] = altered['records'][0]['summary'] = 'message info fable-5 synthetic-x\nnew'
    with pytest.raises(stm.TextRefused, match='projection_invalid'):
        stm.build_synthetic_text_memory(altered, encode, IDENTITY, DIM, root, 'b', enabled=True)
    live = json.loads(json.dumps(projection))
    live['records'][0]['meta']['task_id'] = 'lead-mind-production-20261031'
    with pytest.raises(stm.TextRefused, match='projection_invalid'):
        stm.build_synthetic_text_memory(live, encode, IDENTITY, DIM, root, 'c', enabled=True)
    assert list(root.iterdir()) == []


def consistent(agent=FABLE, kind='message', status='info', task='synthetic-a', body='consistent body text'):
    fields = {'ts_utc': '2026-11-01T10:00:00.000000Z', 'agent': agent, 'type': kind, 'status': status,
              'task_id': task, 'pr': None, 'head': None, 'ci': None}
    return stm._record(fields, body, False, 'e' * 64)


@pytest.mark.parametrize('change', ['live_task', 'review_lane', 'review_type', 'bookkeeping', 'body', 'provenance',
                                    'branch_task', 'review_task', 'review_body', 'secret_body', 'evasion_body'])
def test_validate_rederives_every_record(change):
    projection = stm.project_synthetic_text(corpus(), enabled=True)
    stm.validate_projection(projection)
    bad = json.loads(json.dumps(projection))
    if change == 'live_task':
        bad['records'][0] = consistent(task='lead-mind-production-20261031')
    elif change == 'review_lane':
        bad['records'][0] = consistent(agent='claude-rco-1')
    elif change == 'review_type':
        bad['records'][0] = consistent(kind='decision')
    elif change == 'bookkeeping':
        bad['records'][0] = consistent(kind='claim', status='active')
    elif change == 'branch_task':
        bad['records'][0] = consistent(task='synthetic-a/pr-branch')
    elif change == 'review_task':
        bad['records'][0] = consistent(task='synthetic-review-a')
    elif change == 'review_body':
        bad['records'][0] = consistent(body='the vote is in')
    elif change == 'secret_body':
        bad['records'][0] = consistent(body='key AKIA' + 'IOSFODNN7EXAMPLE')
    elif change == 'evasion_body':
        bad['records'][0] = consistent(body='pass\u200bword is here')
    elif change == 'body':
        record = bad['records'][0]
        record['stored_text'] = record['summary'] = record['stored_text'] + ' changed'
    else:
        bad['records'][0]['provenance']['canonical_event'] = True
    with pytest.raises(stm.TextRefused, match='projection_invalid'):
        stm.validate_projection(bad)


def test_ask_refuses_a_memory_outside_the_synthetic_scope(built):
    from sandbox.mind.step2 import shared_index_builder as builder
    path = built['dir'] / 'memory.json'
    memory = json.loads(path.read_bytes())
    memory['scope'] = builder.SCOPE
    raw = json.dumps(memory, sort_keys=True, separators=(',', ':')).encode()
    path.write_bytes(raw)
    with pytest.raises(stm.TextRefused, match='memory_invalid'):
        stm.ask_synthetic_text(built['dir'], hashlib.sha256(raw).hexdigest(), Encoder(), 'disk space',
                               '2026-11-02T00:00:00Z', enabled=True)


def test_records_bind_the_canonical_source_event_ref():
    from tools.bridge_compact_view import event_id
    events = corpus()
    projection = stm.project_synthetic_text(events, enabled=True)
    assert [r['provenance']['source_event_ref'] for r in projection['records']] == [event_id(e) for e in events]
    twin = [event(FABLE, 'message', 'info', 'synthetic-a', 'same text', payload={'n': 1}),
            event(FABLE, 'message', 'info', 'synthetic-a', 'same text', payload={'n': 2})]
    one, two = stm.project_synthetic_text(twin, enabled=True)['records']
    assert one['stored_text'] == two['stored_text']
    assert one['provenance']['source_event_ref'] != two['provenance']['source_event_ref']
    assert one['record_id'] != two['record_id']
    bad = json.loads(json.dumps(projection))
    bad['records'][0]['provenance']['source_event_ref'] = 'E' * 64
    with pytest.raises(stm.TextRefused, match='projection_invalid'):
        stm.validate_projection(bad)


@pytest.fixture
def source(tmp_path):
    from waggledance.core import bridge_log_reader as bridge
    events = corpus() + [event(LEAD, 'claim', 'active', 'synthetic-x', 'claim row', minute=40),
                         event('claude-rco-1', 'message', 'info', 'synthetic-x', 'review row', minute=41)]
    path, generation = tmp_path / 'events.jsonl', tmp_path / 'generation.json'
    generation.write_bytes(json.dumps({'generation': 'synthetic-g1'}).encode())

    def write(rows):
        content = b''.join(json.dumps(row, sort_keys=True, separators=(',', ':')).encode() + b'\n' for row in rows)
        path.write_bytes(content)
        cursor = bridge.read_bridge_log(str(path), generation_path=str(generation)).candidate_cursor
        return {'source_path': str(path), 'generation_path': str(generation), 'end_cursor': cursor,
                'expected_prefix_sha256': hashlib.sha256(content).hexdigest()}

    return {'events': events, 'write': write, 'path': path, 'generation': generation}


def test_verify_is_off_by_default():
    assert stm.verify_synthetic_sources(None) == {'status': 'OFF'}


def test_every_record_is_verified_within_the_frozen_synthetic_prefix(source):
    from tools.bridge_compact_view import event_id
    pins = source['write'](source['events'])
    projection = stm.project_synthetic_text(source['events'], enabled=True)
    result = stm.verify_synthetic_sources(projection, enabled=True, **pins)
    assert result == {'status': 'VERIFIED_WITHIN_PREFIX', 'records': 8, 'completeness': 'UNKNOWN',
                      'source_event_refs': sorted(event_id(e) for e in corpus())}
    with source['path'].open('ab') as handle:  # rows after the pinned end are not part of the frozen prefix
        handle.write(json.dumps(event(LEAD, 'message', 'info', 'lead-live-task', 'later'), sort_keys=True).encode()
                     + b'\n')
    assert stm.verify_synthetic_sources(projection, enabled=True, **pins)['status'] == 'VERIFIED_WITHIN_PREFIX'


@pytest.mark.parametrize('fault', ['missing_row', 'changed_row'])
def test_unbound_records_refuse(source, fault):
    events = source['events']
    projection = stm.project_synthetic_text(events, enabled=True)
    rows = [dict(e) for e in events]
    if fault == 'missing_row':
        del rows[1]
    elif fault == 'changed_row':
        rows[1]['message'] += ' changed'
    pins = source['write'](rows)
    with pytest.raises(stm.TextRefused, match='source_unbound'):
        stm.verify_synthetic_sources(projection, enabled=True, **pins)


def test_a_non_synthetic_row_in_the_prefix_refuses_the_whole_call(source):
    rows = source['events'] + [event(LEAD, 'message', 'info', 'lead-live-task', 'live looking text', minute=50)]
    pins = source['write'](rows)
    projection = stm.project_synthetic_text(source['events'], enabled=True)
    with pytest.raises(stm.TextRefused, match='live_input_refused'):
        stm.verify_synthetic_sources(projection, enabled=True, **pins)


@pytest.mark.parametrize('fault', ['prefix_pin', 'generation', 'cursor_past_end', 'relative_path', 'cursor_type'])
def test_unpinned_source_refuses(source, fault):
    from waggledance.core import bridge_log_reader as bridge
    pins = source['write'](source['events'])
    projection = stm.project_synthetic_text(source['events'], enabled=True)
    if fault == 'prefix_pin':
        pins['expected_prefix_sha256'] = '0' * 64
    elif fault == 'generation':
        source['generation'].write_bytes(json.dumps({'generation': 'synthetic-g2'}).encode())
    elif fault == 'cursor_past_end':
        cursor = pins['end_cursor']
        pins['end_cursor'] = bridge.BridgeCursor(cursor.offset + 10, cursor.file_identity, cursor.generation)
    elif fault == 'relative_path':
        pins['source_path'] = 'events.jsonl'
    else:
        cursor = pins['end_cursor']
        pins['end_cursor'] = (cursor.offset, cursor.file_identity, cursor.generation)
    with pytest.raises(stm.TextRefused, match='source_refused'):
        stm.verify_synthetic_sources(projection, enabled=True, **pins)


def test_ask_can_verify_its_source_before_answering(built, source):
    pins = source['write'](source['events'])
    answer = stm.ask_synthetic_text(built['dir'], built['memory_sha256'], Encoder(),
                                    'where do the disk space dips on C come from', '2026-11-02T00:00:00Z',
                                    enabled=True, source=pins)
    assert answer['status'] == 'ANSWER' and answer['source_verified'] is True
    verified = stm.verify_synthetic_sources(built['projection'], enabled=True, **pins)['source_event_refs']
    assert all(result['source_event_ref'] in verified for result in answer['results'])
    pins['expected_prefix_sha256'] = '0' * 64
    with pytest.raises(stm.TextRefused, match='source_refused'):
        stm.ask_synthetic_text(built['dir'], built['memory_sha256'], Encoder(), 'disk space', '2026-11-02T00:00:00Z',
                               enabled=True, source=pins)


def test_a_record_claiming_a_real_source_row_with_other_text_refuses(source):
    from tools.bridge_compact_view import event_id
    events = source['events']
    pins = source['write'](events)
    projection = stm.project_synthetic_text(events, enabled=True)
    real = projection['records'][1]
    fields = {'ts_utc': real['time_utc'], 'agent': real['meta']['agent'], 'type': real['meta']['type'],
              'status': real['meta']['status'], 'task_id': real['meta']['task_id'], 'pr': None, 'head': None,
              'ci': None}
    forged = stm._record(fields, 'a different consistent body', False, event_id(events[1]))
    stm.validate_projection(dict(projection, records=[forged], accepted=1))
    with pytest.raises(stm.TextRefused, match='source_unbound'):
        stm.verify_synthetic_sources(dict(projection, records=[forged], accepted=1), enabled=True, **pins)


@pytest.mark.parametrize('bad', ['E' * 64, 'e' * 63, None, 7])
def test_record_refuses_a_malformed_source_ref(bad):
    fields = {'ts_utc': '2026-11-01T10:00:00.000000Z', 'agent': FABLE, 'type': 'message', 'status': 'info',
              'task_id': 'synthetic-a', 'pr': None, 'head': None, 'ci': None}
    with pytest.raises(ValueError):
        stm._record(fields, 'body', False, bad)


@pytest.mark.parametrize('change', ['extra', 'missing', 'not_dict'])
def test_ask_refuses_a_malformed_source_binding(built, source, change):
    pins = source['write'](source['events'])
    if change == 'extra':
        pins['max_prefix_bytes'] = 1
    elif change == 'missing':
        del pins['generation_path']
    else:
        pins = list(pins.items())
    with pytest.raises(stm.TextRefused, match='source_refused'):
        stm.ask_synthetic_text(built['dir'], built['memory_sha256'], Encoder(), 'disk space', '2026-11-02T00:00:00Z',
                               enabled=True, source=pins)



def cut_or_refused(line):
    """The line never reaches the projection: it cuts the text after it, and alone it refuses the record."""
    cut = stm.project_synthetic_text([event(FABLE, 'message', 'info', 'synthetic-a', 'first useful line\n' + line +
                                            '\nlater useful line')], enabled=True)
    assert [r['stored_text'] for r in cut['records']] == ['message info fable-5 synthetic-a\nfirst useful line']
    assert cut['records'][0]['meta']['text_cut'] is True
    refused = stm.project_synthetic_text([event(FABLE, 'message', 'info', 'synthetic-a', line + '\nlater line')],
                                         enabled=True)
    assert refused['records'] == [] and refused['refused'] == {'text_guarded': 1}
    for projection in (cut, refused):
        dumped = json.dumps(projection, ensure_ascii=False)
        assert line not in dumped and 'later' not in dumped


def kept(line):
    projection = stm.project_synthetic_text([event(FABLE, 'message', 'info', 'synthetic-a', line)], enabled=True)
    return [r['stored_text'] for r in projection['records']] == ['message info fable-5 synthetic-a\n' + line]


SECRET_SHAPES = [
    'aws key AKIA' + 'IOSFODNN7EXAMPLE',
    'jwt eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.c2ln',
    '-----BEGIN OPENSSH PRIVATE KEY-----',
    'clone https://someone:hunter2' + chr(64) + 'example.org/repo',
    'Authorization: Basic dXNlcjpwYXNz',
    'Cookie: sid=abc',
    'Set-Cookie: sid=abc',
    'the passphrase is horse',
    'pwd=abc',
    'auth=abc',
    'glpat-' + 'x' * 20,
    'AIza' + 'Sy' + 'a1' * 16,
    'hf_' + 'a1' * 15,
    'npm_' + 'a1' * 18,
    'blob QWxhZGRpbjpvcGVuIHNlc2FtZQ9xK2',
    'id 9fK2xLq8ZtR4vW7mN3pB6cJ1',
    'salasana on kissa',
    'tunnus abc',
    'access_token=abc',
    'x-api-token: abc',
    'client_secret=abc',
    'session_key: abc',
    'xoxb-123',
    'blob AbcdEfghIjklMnopQrstUv',  # long mixed-case run without digits
    'the pwd is abc',
    'Authorization Basic abc',
    'key AKIA' + 'ABCDEFGHIJKL',  # short AWS shape, no long run
    'AIza' + 'abc_def-ghi_jkl-mnop',
    'jwt eyJ' + 'abc_defg.x',
]


@pytest.mark.parametrize('line', SECRET_SHAPES)
def test_secret_like_shapes_cut_the_text(line):
    cut_or_refused(line)


@pytest.mark.parametrize('line', [
    'pass​word=abc',  # zero-width space
    'pass­word=abc',  # soft hyphen
    'ｔｏｋｅｎ: abc',  # fullwidth letters
    'tоken: abc',  # Cyrillic o
    '‮abc cba',  # right-to-left override
    'tab\there',
    'bell\x07here',
    'carriage\rreturn',
    'non breaking space',
])
def test_unicode_evasions_and_controls_cut_the_text(line):
    cut_or_refused(line)


@pytest.mark.parametrize('line', [
    'merged commit 64616d07c1bfbeabe59ae4aacd0108e1780e1c7d on the synthetic branch',
    'head: 64616d07c1bfbeabe59ae4aacd0108e1780e1c7d',
    'sha256=' + '0123456789abcdef' * 4,
    'see sandbox/mind/step2/synthetic_text_memory.py for the rule',
    'the lead-mind-synthetic-text-privacy-fixes-20261108-v2 task is done',
    'prefix sha ' + '0123456789abcdef' * 4,
    'task synthetic-disk-space-dips-20261107 at 2026-11-07T00:04:55.5375230Z',
    'Levytilan notkahdus johtuu varmuuskopiosta: äöå ÄÖÅ',
    'short commit 64616d07 and PR 2049',
    'internationalizations are lowercase words',
])
def test_ordinary_lines_are_kept(line):
    assert kept(line)


def test_line_length_is_bounded():
    assert stm.LINE_CAP == 280
    assert kept('a' + ' word' * 55 + 'x' * 4)
    assert not kept('a' + ' word' * 55 + 'x' * 5)


@pytest.mark.parametrize('line', [
    'RCO2 changes_requested on PR 1562',
    'rco_pass at head abc',
    'this is a veto',
    'finding GP1 is open',
    'my vote is yes',
    'recusal noted',
    'build_consensus done',
    'I approve this',
    'claude-rco-1 said so',
    'rco-2 looked at it',
    'RCO2 looked at it',
    'claude_rco2 said so',
])
def test_review_words_in_text_cut_it(line):
    cut_or_refused(line)


def test_only_broadcast_non_review_events_are_stored():
    canary = 'zq-directed-canary'
    events = [event(FABLE, 'message', 'info', 'synthetic-ok', 'kept broadcast text'),
              event(LEAD, 'message', 'info', 'synthetic-d', canary + ' directed', to=TOOLS),
              event(LEAD, 'message', 'info', 'synthetic-d', canary + ' to rco', to='claude-rco-2'),
              event(LEAD, 'message', 'info', 'synthetic-d', canary + ' list', to=[TOOLS]),
              event(LEAD, 'message', 'info', 'synthetic-d', canary + ' none', to=None),
              event(LEAD, 'message', 'info', 'synthetic-d', canary + ' payload rco', payload={'to': 'claude-rco-1'}),
              event(LEAD, 'message', 'info', 'synthetic-d', canary + ' payload rcpt',
                    payload={'recipient': 'rco-2'}),
              event(LEAD, 'message', 'info', 'synthetic-review-d', canary + ' review task'),
              event(LEAD, 'message', 'info', 'synthetic-rco2-d', canary + ' rco task')]
    no_to = event(LEAD, 'message', 'info', 'synthetic-d', canary + ' no to')
    del no_to['to']
    projection = stm.project_synthetic_text(events + [no_to], enabled=True)
    assert [r['stored_text'] for r in projection['records']] == ['message info fable-5 synthetic-ok\nkept broadcast text']
    assert projection['refused'] == {'directed_excluded': 4, 'rco_addressed_excluded': 3, 'review_task_excluded': 2}
    assert canary not in json.dumps(projection)


def test_a_directed_source_row_does_not_verify(source):
    rows = [dict(e) for e in source['events']]
    projection = stm.project_synthetic_text(rows, enabled=True)
    rows[2]['to'] = TOOLS
    pins = source['write'](rows)
    with pytest.raises(stm.TextRefused, match='source_unbound'):
        stm.verify_synthetic_sources(projection, enabled=True, **pins)



@pytest.mark.parametrize('line', [
    'merged at 64616d07c1bfbeabe59ae4aacd0108e1780e1c7d on the synthetic branch',  # no digest context word
    'value ' + '0123456789abcdef' * 4,
    '0123456789abcdef0123456789abcdef01234567',
    'tok=' + '0123456789abcdef' * 4,
])
def test_a_hex_digest_without_a_digest_context_word_cuts_the_text(line):
    cut_or_refused(line)


@pytest.mark.parametrize('line', [
    'tok a3f9k2x8-q1m7z4c6_v0b5n8w2',
    'id Ab3dE-fG7hI_jK9lM-nO1pQ',
    'k 9a8b7c6d-5e4f3g2h-1i0j',
    'x abcdefghijklmnop12345',
    'opaque abc-def_ghi-jkl_mnop=Q9',
])
def test_url_safe_token_runs_cut_the_text(line):
    cut_or_refused(line)


@pytest.mark.parametrize('routing', [
    {'expected_responders': {'claude-rco-2': {}}},
    {'visibility': 'private'},
    {'private': True},
    {'reply_to': TOOLS},
    {'recipients': []},
    {'target_agent': LEAD},
    {'audience': 'lanes'},
    {'payload': {'audience': 'rco'}},
    {'payload': {'nested': [{'to_list': [TOOLS]}]}},
    {'payload': {'To': ''}},
    {'To': ''},
])
def test_any_routing_key_besides_the_top_level_broadcast_to_excludes_the_event(routing):
    canary = 'zq-routing-canary'
    projection = stm.project_synthetic_text([event(FABLE, 'message', 'info', 'synthetic-ok', 'kept broadcast text'),
                                             event(LEAD, 'message', 'info', 'synthetic-d', canary, **routing)],
                                            enabled=True)
    assert projection['accepted'] == 1 and projection['refused'] == {'directed_excluded': 1}
    assert canary not in json.dumps(projection)


@pytest.mark.parametrize('line', [
    'reviewed and fine',
    'under review now',
    'the reviewer said yes',
    'the verdict is clear',
    'consensus reached',
    'LGTM',
    'signed off by the lead',
    'sign-off given',
    'pass at head 1234567',
])
def test_wider_review_words_cut_the_text(line):
    cut_or_refused(line)


@pytest.mark.parametrize('line', [
    'contact matti' + chr(64) + 'example.org',
    'ping ' + chr(64) + 'mattiv please',
    'Matti Virtanen said hello',
    'kysy Maija Meikäläiseltä',
    'call +358401234567',
    'call 040 123 4567',
    'hetu 010190-123A',
])
def test_personal_data_shapes_cut_the_text(line):
    cut_or_refused(line)


def test_corpus_lines_are_still_kept_after_the_privacy_rules():
    projection = stm.project_synthetic_text(corpus(), enabled=True)
    assert projection['accepted'] == len(TOPICS) and projection['refused'] == {}
    assert all(record['meta']['text_cut'] is False for record in projection['records'])

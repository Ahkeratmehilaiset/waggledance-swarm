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
             'task_id': task, 'message': message}
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
    assert set(first) == {'ref', 'text', 'time_utc', 'cell', 'kind'}
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
    return stm._record(fields, body, False)


@pytest.mark.parametrize('change', ['live_task', 'review_lane', 'review_type', 'bookkeeping', 'body', 'provenance',
                                    'branch_task'])
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

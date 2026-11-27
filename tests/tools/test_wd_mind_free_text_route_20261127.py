"""Train 4 item C10: one free-text question beside the fixed startup question. Synthetic only."""

import hashlib
import io
import json
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import wd_bridge_mind_session as session
from tools import wd_mind_full_runtime as runtime
from tools import wd_mind_runtime_process as process

MARKER = 'zq-synthetic-marker-7f3a'
NONCE = 'd' * 64
EMOJI = '\U0001F41D'                                        # 12 bytes once ASCII-escaped


def _startup(**extra):
    return dict({'schema': 'wd.mind-pipe-request.v1', 'query_id': 'startup_work_overview',
                 'nonce': NONCE, 'lane': 'codex-lead-1', 'scope': 'own'}, **extra)


def _free(query=MARKER + ' what is the next step', **extra):
    return dict({'schema': 'wd.mind-pipe-request.v1', 'query_id': 'free_text_v1', 'query': query,
                 'nonce': NONCE, 'lane': 'codex-lead-1', 'scope': 'own'}, **extra)


# ---------------------------------------------------------------- the question bounds

QUESTIONS = [
    ('one_char', 'x', True),
    ('max_chars', 'a' * 512, True),
    ('too_long', 'a' * 513, False),
    ('max_umlauts', 'ä' * 341, True),                # 6 escaped bytes each: 2046
    ('too_many_umlauts', 'ä' * 342, False),          # 2052
    ('max_supplementary', EMOJI * 170, True),             # 2040
    ('too_many_supplementary', EMOJI * 171, False),       # 2052
    ('empty', '', False),
    ('blank', '   ', False),
    ('newline', 'line one\nline two', False),
    ('tab', 'a\tb', False),
    ('nul', 'a\x00b', False),
    ('lone_surrogate', 'a\ud800b', False),
    ('not_nfc', 'é', False),
    ('nfc', 'é', True),
    ('finnish', 'mikä on seuraava askel?', True),
    ('bytes', b'abc', False),
    ('startup_sentence', 'current task active work next step recent status', False),
    ('startup_sentence_spaced', 'current task active work next step recent status ', True),
    ('none', None, False),
]


def test_the_reserved_sentence_is_the_startup_sentence():
    assert process.FREE_TEXT_RESERVED == runtime._STARTUP_PIPE_QUERY


@pytest.mark.parametrize('name, value, ok', QUESTIONS, ids=[q[0] for q in QUESTIONS])
def test_client_and_owner_bound_the_question_identically(name, value, ok):
    assert process.free_text_question_ok(value) is ok
    assert runtime._free_text_question_ok(value) is ok


def test_the_longest_allowed_questions_fit_the_pipe_frame():
    for question in ('a' * 512, 'ä' * 341, EMOJI * 170, '"' * 512, '\\' * 512):
        if not process.free_text_question_ok(question):
            continue
        frame = process._local_candidate_encode(process._local_candidate_request(
            dict(_free(question), schema='wd.mind-pipe-request.v2')))
        assert len(frame) <= process._LOCAL_CANDIDATE_MAX_BYTES


# ---------------------------------------------------------------- request shapes at the client and the owner

@pytest.mark.parametrize('request_, ok', [
    (_startup(), True),
    (_startup(query='current task'), False),             # the startup request keeps its exact key set
    (_free(), True),
    ({k: v for k, v in _free().items() if k != 'query'}, False),
    (_free(query='a' * 513), False),
    (_free(query='line\nbreak'), False),
    (_free(extra='x'), False),
    (dict(_free(), query_id='free_text_v2'), False),
    (dict(_free(), query=7), False),
])
def test_client_request_shapes(request_, ok):
    for schema in ('wd.mind-pipe-request.v1', 'wd.mind-pipe-request.v2'):
        wire = dict(request_, schema=schema)
        if ok:
            assert process._local_candidate_request(wire) == wire
        else:
            with pytest.raises(ValueError):
                process._local_candidate_request(wire)


@pytest.mark.parametrize('request_, passes', [
    (_startup(), True), (_free(), True),
    (_startup(query='x'), False), ({k: v for k, v in _free().items() if k != 'query'}, False),
    (_free(query='a' * 513), False), (_free(query='a\nb'), False), (_free(extra='x'), False),
    (_free(query=runtime._STARTUP_PIPE_QUERY), False),     # refused before the lock, so READY stays READY
])
def test_the_owner_candidate_request_validates_before_any_state(monkeypatch, request_, passes):
    owner = object.__new__(runtime._SingleManifestOwner)
    owner._lanes = frozenset({'codex-lead-1'})
    for located, schema in ((False, 'wd.mind-pipe-request.v1'), (True, 'wd.mind-pipe-request.v2')):
        wire = dict(request_, schema=schema)
        # A valid shape passes validation and stops only at the switched-off budget gate.
        monkeypatch.setattr(runtime, '_STARTUP_OWNER_SOURCE_BUDGET_ENABLED', False)
        with pytest.raises(runtime.FullRuntimeRefused) as caught:
            owner._candidate_request(wire, located=located)
        assert MARKER not in str(caught.value)
        monkeypatch.setattr(runtime, '_STARTUP_OWNER_SOURCE_BUDGET_ENABLED', True)
        if passes:
            with pytest.raises(AttributeError):                    # reached the owner lock: validation passed
                owner._candidate_request(wire, located=located)
        else:
            with pytest.raises(runtime.FullRuntimeRefused):
                owner._candidate_request(wire, located=located)


def _handler(monkeypatch, calls):
    handler = object.__new__(runtime._FlatRequestHandler)
    handler._lock, handler._closed, handler._failed = threading.Lock(), False, False
    handler._memory, handler._lanes = object(), frozenset({'codex-lead-1'})
    handler._retained = None
    handler._adapter = SimpleNamespace(query=lambda _: None)
    monkeypatch.setattr(runtime._FlatRequestHandler, '_running_owner', lambda self: None)
    monkeypatch.setattr(runtime._FlatRequestHandler, '_source_check', lambda self: None)

    def query(**kwargs):
        calls.append(kwargs['query'])
        return {'schema': 'wd.mind-flat-f32-query.v1', 'status': 'SEMANTIC_CANDIDATE',
                'authority': 'none', 'relevance': 'UNVERIFIED', 'index_match': 'NOT_APPLICABLE',
                'own_latest_task': 'UNKNOWN', 'results': [{'ref_sha256': 'a' * 64, 'doc_id_sha256': 'b' * 64,
                    'source_reference': {'sha256': 'c' * 64, 'kind': 'sha256_of_canonical_event_json',
                                         'verified': 'MATCH_WITHIN_PREFIX', 'sender_authentication': 'UNKNOWN'},
                    'source_time_utc': '2026-01-01T00:00:00Z', 'source_valid_until_utc': None,
                    'event_age_seconds_upper_bound': 1000000}]}
    monkeypatch.setattr(runtime, 'query_flat_memory', query)
    return handler


def test_the_owner_asks_the_free_question_and_returns_hashes_only(monkeypatch):
    calls = []
    handler = _handler(monkeypatch, calls)
    pipe = runtime.map_pipe_candidates(enabled=True, handler=handler, request=_free())
    assert calls == [MARKER + ' what is the next step']
    assert pipe['status'] == 'CANDIDATES' and pipe['candidates'][0]['source_event_sha256'] == 'c' * 64
    assert MARKER not in json.dumps(pipe)                  # the answer frame never carries the question
    startup = runtime.map_pipe_candidates(enabled=True, handler=handler, request=_startup())
    assert calls[-1] is runtime._STARTUP_PIPE_QUERY and startup['status'] == 'CANDIDATES'


@pytest.mark.parametrize('request_', [
    _free(query=runtime._STARTUP_PIPE_QUERY),              # would silently get the startup band and spread
    _free(query='a' * 513), _free(query='a\nb'), _startup(query='x'),
    {k: v for k, v in _free().items() if k != 'query'}, dict(_free(), schema='wd.mind-pipe-request.v2'),
], ids=['startup_text', 'too_long', 'newline', 'startup_with_query', 'free_without_query', 'v2_schema'])
def test_the_owner_refuses_bad_free_requests_before_any_query(monkeypatch, request_):
    calls = []
    handler = _handler(monkeypatch, calls)
    with pytest.raises(Exception) as caught:
        runtime.map_pipe_candidates(enabled=True, handler=handler, request=request_)
    assert calls == []
    assert MARKER not in str(caught.value) and runtime._STARTUP_PIPE_QUERY not in str(caught.value)


def test_free_text_ranks_by_cosine_without_the_startup_band():
    from datetime import datetime, timezone
    old, new = datetime(2026, 1, 1, tzinfo=timezone.utc), datetime(2026, 11, 1, tzinfo=timezone.utc)
    rows = [('best_old', 0.90, 0.0, old), ('close_new', 0.88, 0.0, new)]
    assert [r[0] for r in runtime._order_selected(rows, MARKER)] == ['best_old', 'close_new']
    assert [r[0] for r in runtime._order_selected(rows, runtime._STARTUP_PIPE_QUERY)] == ['close_new', 'best_old']
    assert runtime._first_three(rows, {}, MARKER) == rows[:3]


# ---------------------------------------------------------------- session CLI: the question comes from a pinned file

def _ask_args(tmp_path, question_bytes):
    policy = b'{"private":"synthetic-only"}'
    policy_path = tmp_path / 'policy.json'
    policy_path.write_bytes(policy)
    question_path = tmp_path / 'question.txt'
    question_path.write_bytes(question_bytes)
    return ['--mind-local-candidate-ask', '--policy-path', str(policy_path),
            '--policy-sha256', hashlib.sha256(policy).hexdigest(),
            '--approval-reference-sha256', 'c' * 64, '--lane', 'codex-lead-1', '--timeout-seconds', '10',
            '--question-path', str(question_path)]


def _run_ask(monkeypatch, args):
    sent = []
    query = SimpleNamespace(start=lambda: None,
                            wait=lambda **_: {'status': 'REFUSED', 'authority': 'none',
                                              'sender_authentication': 'UNKNOWN', 'source_verification': 'UNVERIFIED',
                                              'candidate_frame': None, 'refusal': 'PIPE_ACCESS_DENIED'},
                            status=lambda: {'state': 'STOPPED'})

    def create(**kwargs):
        sent.append(kwargs['request'])
        return query
    monkeypatch.setattr(session, 'create_selected_row_resolver', lambda **_: object())
    monkeypatch.setattr(process, 'create_local_candidate_query', create)
    out, err = io.BytesIO(), io.BytesIO()
    before = {id(pending) for pending in session._LOCAL_CANDIDATE_PENDING}
    try:
        code = session._local_candidate_entry(args, out, err, str(Path.cwd()), 'a' * 64)
    finally:
        for pending in [p for p in session._LOCAL_CANDIDATE_PENDING if id(p) not in before]:
            session._LOCAL_CANDIDATE_PENDING.remove(pending)
    return code, out.getvalue(), err.getvalue(), sent


@pytest.mark.parametrize('raw', [(MARKER + ' next step').encode(), (MARKER + ' next step\n').encode(),
                                 (MARKER + ' next step\r\n').encode()])
def test_ask_mode_sends_the_pinned_question_and_never_echoes_it(monkeypatch, tmp_path, raw):
    code, out, err, sent = _run_ask(monkeypatch, _ask_args(tmp_path, raw))
    assert code == 2 and out == b''
    assert err == b'{"error":"mind_session_content_refused","reason":"PIPE_ACCESS_DENIED","authority":"none"}\n'
    assert len(sent) == 1 and sent[0]['query_id'] == 'free_text_v1' and sent[0]['query'] == MARKER + ' next step'
    assert sent[0]['scope'] == session._startup_scope_for_lane('codex-lead-1')   # no scope widening
    assert MARKER.encode() not in out + err


@pytest.mark.parametrize('raw', [b'', b'   ', b'two\nlines', b'\xff\xfe', ('a' * 513).encode(), b'a' * 2049,
                                 'é'.encode()],
                         ids=['empty', 'blank', 'two_lines', 'not_utf8', 'too_long', 'file_too_big', 'not_nfc'])
def test_ask_mode_refuses_a_bad_question_with_one_fixed_token(monkeypatch, tmp_path, raw):
    code, out, err, sent = _run_ask(monkeypatch, _ask_args(tmp_path, raw))
    assert (code, out, sent) == (2, b'', [])
    assert err == b'{"error":"mind_session_content_refused","reason":"QUESTION_INVALID","authority":"none"}\n'


def test_ask_mode_refuses_a_question_path_that_is_no_plain_file(monkeypatch, tmp_path):
    args = _ask_args(tmp_path, MARKER.encode())
    for path in (tmp_path / 'missing.txt', tmp_path / 'folder', Path('relative.txt')):
        if path.name == 'folder':
            path.mkdir()
        code, out, err, sent = _run_ask(monkeypatch, args[:-1] + [str(path)])
        assert (code, out, sent) == (2, b'', [])
        assert err == b'{"error":"mind_session_content_refused","reason":"QUESTION_INVALID","authority":"none"}\n'


def test_the_question_never_reaches_argv(tmp_path):
    # No digest or text of the question in the command line (RCO2 on PR 2201).
    args = _ask_args(tmp_path, MARKER.encode())
    assert not any(MARKER in a or hashlib.sha256(MARKER.encode()).hexdigest() in a for a in args)


def test_the_policy_reader_still_checks_its_pin(tmp_path):
    path = tmp_path / 'policy.json'
    path.write_bytes(b'{}')
    assert session._selected_pin_read(path, hashlib.sha256(b'{}').hexdigest(), 64) == b'{}'
    with pytest.raises(ValueError):
        session._selected_pin_read(path, 'e' * 64, 64)
    with pytest.raises(ValueError):
        session._selected_pin_read(path, 'not-a-pin', 64)


@pytest.mark.parametrize('mutate', [
    lambda a: a[:-2],                                             # no question file
    lambda a: a + ['--question-sha256', 'e' * 64],                # no pin argument any more
    lambda a: ['--mind-local-candidate-read'] + a[1:],            # the read mode takes no question
    lambda a: a[:-2] + ['--question-file', a[-1]],
])
def test_ask_mode_argument_shapes(monkeypatch, tmp_path, mutate):
    code, out, err, sent = _run_ask(monkeypatch, mutate(_ask_args(tmp_path, MARKER.encode())))
    assert (code, out, sent) == (2, b'', [])
    assert err == b'{"error":"mind_session_content_refused","authority":"none"}\n'


def test_ask_mode_is_never_controlled(tmp_path):
    args = _ask_args(tmp_path, MARKER.encode())
    out, err = io.BytesIO(), io.BytesIO()
    code = session._local_candidate_entry(args, out, err, str(Path.cwd()), 'a' * 64, control=io.BytesIO(b''))
    assert code == 2 and out.getvalue() == b''


def test_no_writer_in_the_three_modules_formats_the_question():
    # Static guard next to the runtime checks above: no print, log or error writer
    # names the request's query field.
    root = Path(__file__).resolve().parents[2] / 'tools'
    for name in ('wd_mind_runtime_process.py', 'wd_mind_full_runtime.py', 'wd_bridge_mind_session.py'):
        for number, line in enumerate((root / name).read_text(encoding='utf-8').splitlines(), 1):
            if any(sink in line for sink in ('print(', '.write(', 'logging', 'logger')):
                assert "['query']" not in line and '"query"' not in line and 'question' not in line, (name, number)


# ---------------------------------------------------------------- a real Windows pipe round trip

@pytest.mark.skipif(__import__('os').name != 'nt', reason='Windows named pipes only')
@pytest.mark.parametrize('question', [MARKER + ' next step', EMOJI * 170, '\u00e4' * 341])
def test_a_free_question_crosses_a_real_pipe_unchanged(question):
    import os
    seen = []

    class Owner:
        def candidates(self, request):
            seen.append(dict(request))
            return {'schema': 'wd.mind-pipe-candidates.v1', 'nonce': request['nonce'], 'lane': request['lane'],
                    'scope': request['scope'], 'status': 'CANDIDATES',
                    'candidates': [{'ref_sha256': 'a' * 64, 'doc_id_sha256': 'b' * 64,
                                    'source_event_sha256': 'c' * 64}]}
    name = process.local_candidate_pipe_name(manifest_sha256=hashlib.sha256(os.urandom(32)).hexdigest())
    service = process.create_local_candidate_service(enabled=True, owner=Owner(), pipe_name=name)
    service.start()
    try:
        query = process.create_local_candidate_query(enabled=True, pipe_name=name, request=_free(question))
        query.start()
        result = query.wait(timeout=10)
        assert result['status'] == 'CANDIDATES' and result['refusal'] is None
        assert seen == [_free(question)]
        assert question not in result['candidate_frame'].decode('ascii')
    finally:
        assert service.close(timeout=5)['state'] == 'STOPPED'

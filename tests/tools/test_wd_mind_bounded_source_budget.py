"""Synthetic source-budget tests; never read a live bridge log."""
import io

import pytest

from tools import wd_bridge_mind_session as session


def test_budget_never_reads_after_exhaustion():
    class Probe(io.BytesIO):
        def __init__(self):
            super().__init__(b'abcdef')
            self.calls = []

        def read(self, size=-1):
            self.calls.append(size)
            return super().read(size)

    source = Probe()
    budget = session._SourceReadBudget(4)
    assert session._bounded_source_read(source, budget, 3) == b'abc'
    with pytest.raises(ValueError, match='source_budget'):
        session._bounded_source_read(source, budget, 2)
    assert source.calls == [3]
    assert session._bounded_source_read(source, budget, 1) == b'd'
    with pytest.raises(ValueError, match='source_budget'):
        session._bounded_source_read(source, budget, 1)
    assert source.calls == [3, 1]


@pytest.mark.parametrize('invalid', [True, False, 0, -1, 1.5, '3', None])
def test_budget_rejects_nonpositive_or_noninteger_limit(invalid):
    with pytest.raises(ValueError, match='source_budget'):
        session._SourceReadBudget(invalid)


def test_budget_reserves_worst_case_before_delegating():
    budget = session._SourceReadBudget(10)
    budget.reserve(7)
    with pytest.raises(ValueError, match='source_budget'):
        budget.reserve(4)
    budget.reserve(3)
    with pytest.raises(ValueError, match='source_budget'):
        budget.reserve(1)


def test_selected_row_is_refused_before_source_read_when_over_budget(monkeypatch):
    from pathlib import Path
    from types import SimpleNamespace
    from tools import wd_mind_content_query_readonly as reader

    class Probe(io.BytesIO):
        calls = 0
        def read(self, size=-1):
            self.calls += 1
            return super().read(size)

    source = Probe(b'a\n')
    bridge = SimpleNamespace(_open_log=lambda _path: source)
    monkeypatch.setattr(reader, '_narrow_dependencies', lambda: (bridge, object()))
    monkeypatch.setattr(reader, '_narrow_fence', lambda *_: None)
    resolver = object.__new__(session._SelectedRowResolver)
    resolver._cursor = SimpleNamespace(offset=2)
    resolver._source_budget = session._SourceReadBudget(1)
    context = {'messages': {'source_path': str(Path('synthetic-source')),
                            'generation_path': str(Path('synthetic-sidecar'))}, 'policy': {}}
    candidate = {'source_offset': 0, 'source_length': 2, 'chunk_index': 0}
    with pytest.raises(ValueError, match='source_budget'):
        resolver._selected(context, [candidate], 'codex-lead-1', 'own', None)
    assert source.calls == 0


def test_prefix_is_reserved_before_retained_verifier(monkeypatch):
    import json
    import os
    import threading
    import time
    from types import SimpleNamespace
    from tools import wd_mind_retained_query as retained

    calls = []
    monkeypatch.setattr(session._SelectedRowResolver, '_check_controls', lambda _self: None)
    monkeypatch.setattr(session._SelectedRowResolver, '_context', lambda _self: {})
    monkeypatch.setattr(retained, '_source_check', lambda _context: calls.append('read'))
    resolver = object.__new__(session._SelectedRowResolver)
    resolver._cursor = SimpleNamespace(offset=2)
    resolver._source_budget = session._SourceReadBudget(1)
    resolver._generation = None
    resolver._lock = threading.Lock()
    resolver._pid = os.getpid()
    resolver._closed = False
    resolver._private = None
    candidate = {'ref_sha256': 'a'*64, 'doc_id_sha256': 'b'*64, 'source_event_sha256': 'c'*64,
                 'source_offset': 0, 'source_length': 1, 'chunk_index': 0}
    frame = {'schema': 'wd.mind-pipe-candidates.v2', 'nonce': 'd'*64, 'lane': 'codex-lead-1',
             'scope': 'own', 'status': 'CANDIDATES', 'candidates': [candidate]}
    with pytest.raises(session.MindContentSessionRefused):
        resolver.resolve(ipc_bytes=json.dumps(frame).encode(), expected_nonce='d'*64,
                         lane='codex-lead-1', scope='own', deadline_monotonic=time.monotonic()+10)
    assert calls == []


def test_startup_candidate_supplies_fixed_source_limit(monkeypatch):
    calls = []
    monkeypatch.setattr(session, '_selected_pin_read', lambda *_args: b'{}')
    def reject_after_recording(**kwargs):
        calls.append(kwargs['source_read_limit'])
        assert kwargs['source_segment_bytes'] == session._STARTUP_SOURCE_SEGMENT_BYTES == 1024 * 1024
        raise ValueError('synthetic')
    monkeypatch.setattr(session, 'create_selected_row_resolver', reject_after_recording)
    args = ['--mind-local-candidate-read', '--policy-path', 'synthetic-policy',
            '--policy-sha256', 'a'*64, '--approval-reference-sha256', 'b'*64,
            '--lane', 'codex-lead-1', '--timeout-seconds', '5']
    output, error = io.BytesIO(), io.BytesIO()
    assert session._local_candidate_entry(args, output, error, 'synthetic-bundle', 'c'*64) == 2
    assert calls == [1024 * 1024 * 1024]
    assert output.getvalue() == b''


def test_actual_budgeted_resolver_reads_synthetic_pinned_rows(flat_consumer_case, monkeypatch):
    import json
    import time
    from tools import wd_mind_content_query_readonly as reader

    original = session.create_selected_row_resolver
    monkeypatch.setattr(session, 'create_selected_row_resolver',
                        lambda **kwargs: original(source_read_limit=1024*1024*1024, **kwargs))
    resolver, frame, _, _ = _fixtures._located_candidates(flat_consumer_case, monkeypatch)
    before = resolver._source_budget._remaining
    result = resolver.resolve(ipc_bytes=reader._canonical(frame), expected_nonce='1'*64,
                              lane='codex-lead-1', scope='shared', deadline_monotonic=time.monotonic()+60)
    assert result['authority'] == 'none' and result['results']
    assert before - resolver._source_budget._remaining >= 2 * resolver._cursor.offset


# Reuse the existing independently generated synthetic source and controls.
import importlib.util as _importlib_util
from pathlib import Path as _Path
import sys as _sys
_spec = _importlib_util.spec_from_file_location('budget_source_fixtures',
    _Path(__file__).with_name('test_wd_mind_session_content.py'))
_fixtures = _importlib_util.module_from_spec(_spec)
_sys.modules[_spec.name] = _fixtures
_spec.loader.exec_module(_fixtures)
source_fixtures = _fixtures.source_fixtures
flat_consumer_case = _fixtures.flat_consumer_case


def test_real_synthetic_prefix_is_not_scanned_when_budget_cannot_cover_checks(flat_consumer_case, monkeypatch):
    from tools import wd_mind_retained_query as retained

    original = session.create_selected_row_resolver
    # The tail reader may consume up to 64 MiB plus a boundary byte. No
    # remaining budget means the prefix preflight must refuse before scanning.
    monkeypatch.setattr(session, 'create_selected_row_resolver',
                        lambda **kwargs: original(source_read_limit=64*1024*1024+1, **kwargs))
    calls = []
    monkeypatch.setattr(retained, '_source_check', lambda _context: calls.append('scan'))
    with pytest.raises(session.MindContentSessionRefused):
        _fixtures._located_candidates(flat_consumer_case, monkeypatch)
    assert calls == []

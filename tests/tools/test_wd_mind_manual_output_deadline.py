"""Synthetic manual-output deadline checks; no real lane, source, or model."""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import wd_bridge_mind_session as session
from tools import wd_mind_runtime_process as process


@pytest.mark.parametrize(('flush_at', 'expected_exit'), [
    (399.0, 0), (400.0, 2), (401.0, 2),
])
def test_manual_result_flush_respects_outer_deadline(monkeypatch, flush_at, expected_exit):
    """Bytes written before a late flush must be discarded when exit is nonzero."""
    clock = SimpleNamespace(value=100.0)
    observed = {}
    monkeypatch.setattr(session, 'time', SimpleNamespace(monotonic=lambda: clock.value))
    monkeypatch.setattr(session, '_selected_pin_read', lambda *_args: b'{}')

    class Resolver:
        def resolve(self, **kwargs):
            observed['resolve_deadline'] = kwargs['deadline_monotonic']
            return {'schema': 'wd.mind-flat-verified-session.v1', 'authority': 'none',
                    'sender_authentication': 'UNKNOWN', 'lane': 'codex-lead-1',
                    'results': [{'text': 'synthetic only'}]}

    def make_resolver(**kwargs):
        observed['outer_deadline'] = kwargs['deadline_monotonic']
        return Resolver()

    class Query:
        def start(self):
            pass

        def wait(self, *, timeout):
            return {'status': 'CANDIDATES', 'authority': 'none',
                    'sender_authentication': 'UNKNOWN', 'source_verification': 'UNVERIFIED',
                    'candidate_frame': b'synthetic'}

    class Output(io.BytesIO):
        def flush(self):
            clock.value = flush_at
            return super().flush()

    monkeypatch.setattr(session, 'create_selected_row_resolver', make_resolver)
    monkeypatch.setattr(process, 'create_local_candidate_query', lambda **_kwargs: Query())
    args = ['--mind-manual-candidate-read', '--policy-path', r'C:\synthetic-policy',
            '--policy-sha256', hashlib.sha256(b'{}').hexdigest(),
            '--approval-reference-sha256', 'c' * 64, '--lane', 'codex-lead-1',
            '--timeout-seconds', '300']
    out, err = Output(), io.BytesIO()
    code = session._local_candidate_entry(args, out, err, str(Path.cwd()), 'a' * 64)

    assert observed == {'outer_deadline': 400.0, 'resolve_deadline': 190.0}
    # The producer cannot retract bytes already written, even on failure.
    written = out.getvalue()
    assert written and json.loads(written)['authority'] == 'none'
    assert code == expected_exit
    accepted = written if code == 0 else b''
    if expected_exit == 0:
        assert accepted == written and err.getvalue() == b''
    else:
        assert accepted == b'' and err.getvalue()

"""Regression tests for the closed unbudgeted route; all inputs synthetic."""
import io
import json
import threading

import pytest

from tools import wd_mind_full_runtime as runtime
from tools import wd_mind_runtime_child as child
from tools import wd_mind_runtime_process as process


class UntouchableInput:
    def read(self, *_args):
        raise AssertionError("input must not be read")


def unexpected(*_args, **_kwargs):
    raise AssertionError("owner or source must not be touched")


def test_local_query_cli_refuses_before_admission_or_source(monkeypatch, capsys):
    monkeypatch.setattr(child, '_guarded_local_entry', unexpected)
    monkeypatch.setattr(child, '_load_pinned_canonical_child', unexpected)
    args = ['--mind-local-query', '--policy-sha256', 'b' * 64,
            '--approval-reference-sha256', 'c' * 64, '--lane', 'codex-lead-1']
    assert child._fixed_child_main(args) == 2
    assert child._fixed_child_main(['--mind-local-query']) == 2
    assert capsys.readouterr() == ('', '')


def test_direct_legacy_entry_does_not_read_or_create_owner(monkeypatch):
    monkeypatch.setattr(child, 'create_local_manifest_query', unexpected)
    output = io.BytesIO()
    result = child._local_query_entry(control_stream=UntouchableInput(), output_stream=output,
        bundle_root='unused', expected_manifest_sha256='a' * 64, lane='codex-lead-1',
        policy_sha256='b' * 64, approval_reference_sha256='c' * 64)
    assert result == 2 and output.getvalue() == b''


@pytest.mark.parametrize('marker_state', [b'C', b'R'])
def test_guarded_legacy_entry_refuses_before_reservation_and_preserves_marker(
        tmp_path, monkeypatch, capsys, marker_state):
    marker = tmp_path / 'mind-owner-reservation.v1'
    before = b'WD_MIND_OWNER_V1\n' + marker_state
    marker.write_bytes(before)
    monkeypatch.setattr(child, '_AUDIT_ROOT', tmp_path)
    monkeypatch.setattr(child, '_reserve_local_owner', unexpected)
    monkeypatch.setattr(child, '_LocalAdmission', unexpected)
    monkeypatch.setattr(child, 'create_local_manifest_query', unexpected)
    output = io.BytesIO()
    assert child._guarded_local_entry(child._local_query_entry,
        control_stream=UntouchableInput(), output_stream=output, bundle_root='unused',
        expected_manifest_sha256='a' * 64, lane='codex-lead-1',
        policy_sha256='b' * 64, approval_reference_sha256='c' * 64) == 2
    assert marker.read_bytes() == before
    assert output.getvalue() == b''
    assert capsys.readouterr() == ('', '')


def test_located_pipe_dispatch_still_calls_candidate_owner(monkeypatch):
    request = {'schema':'wd.mind-pipe-request.v2', 'query_id':'startup_work_overview',
               'nonce':'a' * 64, 'lane':'codex-lead-1', 'scope':'shared'}
    response = {'schema':'wd.mind-pipe-candidates.v2', 'nonce':request['nonce'],
                'lane':request['lane'], 'scope':request['scope'], 'status':'ABSTAIN', 'candidates':[]}
    calls, sent = [], []
    class Owner:
        def located_candidates(self, value):
            calls.append(value)
            return response
    class Connection:
        def poll(self, _timeout): return True
        def recv_bytes(self, _limit): return json.dumps(request).encode('ascii')
        def send_bytes(self, value):
            sent.append(value)
            service._stop.set()
        def close(self): pass
    class Listener:
        def accept(self): return Connection()
        def close(self): pass
    service = process._LocalCandidateService(Owner(), 'synthetic-unused-name')
    service._listener = Listener()
    service._serve()
    assert calls == [request]
    assert [json.loads(value) for value in sent] == [response]


def test_located_adapter_stays_independent_of_local_query_gate(monkeypatch):
    seen = []
    class Owner:
        def located_candidates(self, request):
            seen.append(request)
            return {'status':'ABSTAIN'}
    adapter = object.__new__(child._LocalManifestQuery)
    adapter._lock, adapter._cancel = threading.Lock(), threading.Event()
    adapter._state, adapter._policy_invalid, adapter._owner = 'READY', False, Owner()
    monkeypatch.setattr(child._LocalManifestQuery, '_check_external_policy', lambda _self: None)
    request = {'synthetic':'request'}
    assert adapter.located_candidates(request) == {'status':'ABSTAIN'}
    assert seen == [request]

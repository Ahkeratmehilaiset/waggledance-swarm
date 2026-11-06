"""Synthetic-only canonical bridge ref matching; no live bridge data."""
import hashlib
import json
from pathlib import Path
import pytest
from sandbox.mind.step2 import shared_capture_adapter as capture
from sandbox.mind.step2 import shared_projection as projection
from tools.bridge_compact_view import event_id

TASK = 'mind-public-status'
LANES = ('codex-lead-1', 'codex-tools-1', 'claude-rco-1', 'claude-rco-2', 'fable-5')


def raw(value): return json.dumps(value, sort_keys=True, separators=(',', ':')).encode('utf-8')
def sha(value): return hashlib.sha256(value).hexdigest()


@pytest.fixture
def case(tmp_path):
    uuids = {name: f'{i:08d}-1111-4111-8111-111111111111' for i, name in enumerate(LANES, 1)}
    policy = raw({'schema': 'wd.q4-non-review-policy.v1', 'approved_tasks': [TASK]})
    public = raw({'schema': 'wd.mind-public-status-policy.v1', 'public_tasks': [TASK], 'public_types': ['status']})
    roster = raw({'lanes': [{'agent': n, 'agent_uuid': uuids[n]} for n in LANES if n != 'codex-tools-1'],
                  'tools_supervisor': {'agent': 'codex-tools-1', 'agent_uuid': uuids['codex-tools-1']}})
    event = {'ts_utc': '2026-11-06T18:05:30Z', 'agent': 'codex-lead-1', 'agent_uuid': uuids['codex-lead-1'],
             'type': 'status', 'status': 'info', 'task_id': TASK, 'to': '', 'message': '', 'paths': [],
             'write_scope': [], 'payload': {'public_status': True}, 'cwd': 'C:\\private-canary\\path'}
    tasks, identities = capture.strict_pins(policy, sha(policy), roster, sha(roster))
    record, _ = projection.project_event(event, tasks, include_refs=False, roster=identities, exclude_review_signal=True)
    source = tmp_path / 'synthetic-events.jsonl'
    def setup(events):
        content = b''.join(raw(e) + b'\n' for e in events)
        source.write_bytes(content)
        return dict(enabled=True, record=record, policy_bytes=policy, policy_sha256=sha(policy),
                    public_policy_bytes=public, public_policy_sha256=sha(public), roster_bytes=roster,
                    roster_sha256=sha(roster), source_path=str(source), expected_snapshot_sha256=sha(content),
                    coverage_start_utc='2026-11-06T18:05:00Z', coverage_end_utc='2026-11-06T18:06:00Z')
    return event, setup


def test_default_off_never_reads():
    from tools import wd_q4_canonical_ref_verifier as v
    assert v.verify() == {'status': 'OFF'}


def test_distinct_status_same_minute_resolves_only_target(case):
    from tools import wd_q4_canonical_ref_verifier as v
    event, setup = case
    other = dict(event, status='done')
    out = v.verify(**setup([event, other]))
    assert out == {'status':'MATCH', 'canonical_event_refs':[event_id(event)], 'count':1}


def test_identical_projection_from_two_events_is_ambiguous(case):
    from tools import wd_q4_canonical_ref_verifier as v
    event, setup = case
    other = dict(event, ts_utc='2026-11-06T18:05:43Z')
    out = v.verify(**setup([event, other]))
    assert out['status'] == 'AMBIGUOUS' and out['count'] == 2
    assert set(out['canonical_event_refs']) == {event_id(event), event_id(other)}


def test_next_minute_does_not_match(case):
    from tools import wd_q4_canonical_ref_verifier as v
    event, setup = case
    other = dict(event, ts_utc='2026-11-06T18:06:00Z')
    assert v.verify(**setup([other])) == {'status':'NONE', 'canonical_event_refs':[], 'count':0}


@pytest.mark.parametrize('pin', ['policy_sha256', 'public_policy_sha256', 'roster_sha256'])
def test_wrong_policy_or_roster_pin_refuses_before_reader(case, monkeypatch, pin):
    from tools import wd_q4_canonical_ref_verifier as v
    event, setup = case
    args = setup([event]); args[pin] = 'f'*64
    monkeypatch.setattr(v.bridge, 'read_bridge_log', lambda *a, **k: pytest.fail('reader called'))
    with pytest.raises(v.VerifierRefused): v.verify(**args)


def test_no_content_leak_and_private_rows_are_excluded(case):
    from tools import wd_q4_canonical_ref_verifier as v
    event, setup = case
    private = dict(event, message='private-message-canary', paths=['C:\\secret-canary'])
    out = v.verify(**setup([private, event]))
    assert out['status'] == 'MATCH' and out['canonical_event_refs'] == [event_id(event)]
    assert all(x not in json.dumps(out) for x in ('private-message-canary', 'secret-canary', 'private-canary', 'public_status'))


def test_over_2000_window_rows_are_refused(case):
    from tools import wd_q4_canonical_ref_verifier as v
    event, setup = case
    with pytest.raises(v.VerifierRefused): v.verify(**setup([event] * 2001))


def test_missing_source_is_unknown(case):
    from tools import wd_q4_canonical_ref_verifier as v
    event, setup = case
    args = setup([event]); Path(args['source_path']).unlink()
    assert v.verify(**args) == {'status':'UNKNOWN', 'canonical_event_refs':[], 'count':0}


@pytest.mark.parametrize('missing', ['source_path', 'expected_snapshot_sha256'])
def test_missing_source_descriptor_is_unknown_without_read(case, monkeypatch, missing):
    from tools import wd_q4_canonical_ref_verifier as v
    event, setup = case
    args = setup([event]); args[missing] = None
    monkeypatch.setattr(v.bridge, 'read_bridge_log', lambda *a, **k: pytest.fail('reader called'))
    assert v.verify(**args) == {'status':'UNKNOWN', 'canonical_event_refs':[], 'count':0}


def test_incomplete_coverage_is_unknown_before_reader(case, monkeypatch):
    from tools import wd_q4_canonical_ref_verifier as v
    event, setup = case
    args = setup([event]); args['coverage_end_utc'] = '2026-11-06T18:05:50Z'
    monkeypatch.setattr(v.bridge, 'read_bridge_log', lambda *a, **k: pytest.fail('reader called'))
    assert v.verify(**args) == {'status':'UNKNOWN', 'canonical_event_refs':[], 'count':0}


def test_source_digest_mismatch_and_partial_line_unknown(case):
    from tools import wd_q4_canonical_ref_verifier as v
    event, setup = case
    args = setup([event]); args['expected_snapshot_sha256'] = 'f'*64
    assert v.verify(**args)['status'] == 'UNKNOWN'
    args = setup([event]); content = raw(event); Path(args['source_path']).write_bytes(content)
    args['expected_snapshot_sha256'] = sha(content)
    assert v.verify(**args)['status'] == 'UNKNOWN'


def test_over_100000_rows_are_scanned_in_pinned_pages(case, monkeypatch):
    from tools import wd_q4_canonical_ref_verifier as v
    event, setup = case
    later = dict(event, ts_utc='2026-11-06T18:05:40Z')
    filler = {'ts_utc': '2026-11-06T18:04:00Z'}
    args = setup([event] + [filler] * 100001 + [later])
    actual = v.bridge.read_bridge_log
    calls = []
    def observe(*a, **k):
        calls.append(k)
        return actual(*a, **k)
    monkeypatch.setattr(v.bridge, 'read_bridge_log', observe)
    out = v.verify(**args)
    assert out['status'] == 'AMBIGUOUS' and out['count'] == 2
    assert set(out['canonical_event_refs']) == {event_id(event), event_id(later)}
    assert len(calls) >= 3
    assert all(c['max_rows'] <= 100000 for c in calls)


def test_paged_source_change_never_produces_match(case, monkeypatch):
    from tools import wd_q4_canonical_ref_verifier as v
    event, setup = case
    args = setup([event, dict(event, ts_utc='2026-11-06T18:05:40Z')])
    actual = v.bridge.read_bridge_log
    calls = []
    def mutate(*a, **k):
        calls.append(1)
        if len(calls) == 3:
            with open(args['source_path'], 'ab') as f: f.write(b'{}\n')
        return actual(*a, **k)
    monkeypatch.setattr(v, '_MAX_PAGE_ROWS', 1)
    monkeypatch.setattr(v.bridge, 'read_bridge_log', mutate)
    assert v.verify(**args)['status'] == 'UNKNOWN'


def test_caller_pin_refusal_has_no_exception_chain_or_source_locals(case):
    from tools import wd_q4_canonical_ref_verifier as v
    event, setup = case
    args = setup([event]); args['policy_sha256'] = 'f'*64
    with pytest.raises(v.VerifierRefused) as err:
        v.verify(**args)
    assert err.value.__context__ is None and err.value.__cause__ is None
    tb = err.value.__traceback__
    while tb:
        if tb.tb_frame.f_code.co_name == 'verify':
            assert tb.tb_frame.f_locals['source_path'] is None
            assert tb.tb_frame.f_locals['policy_bytes'] is None
        tb = tb.tb_next


def test_byte_bounded_paging_finds_matches_across_pages(case, monkeypatch):
    from tools import wd_q4_canonical_ref_verifier as v
    event, setup = case
    later = dict(event, ts_utc='2026-11-06T18:05:48Z')
    filler = {'ts_utc': '2026-11-06T18:04:00Z', 'message': 'x' * 100}
    args = setup([event] + [filler] * 30 + [later])
    monkeypatch.setattr(v, '_MAX_PAGE_BYTES', 1024)
    out = v.verify(**args)
    assert out['status'] == 'AMBIGUOUS'
    assert set(out['canonical_event_refs']) == {event_id(event), event_id(later)}


def test_same_size_rewrite_after_first_page_fails_prefix_pin(case, monkeypatch):
    from tools import wd_q4_canonical_ref_verifier as v
    event, setup = case
    args = setup([event, dict(event, ts_utc='2026-11-06T18:05:40Z')])
    actual = v.bridge.read_bridge_log
    calls = []
    def mutate(*a, **k):
        calls.append(1)
        if len(calls) == 3:
            with open(args['source_path'], 'r+b') as stream:
                stream.seek(0)
                stream.write(b'X')
        return actual(*a, **k)
    monkeypatch.setattr(v, '_MAX_PAGE_ROWS', 1)
    monkeypatch.setattr(v.bridge, 'read_bridge_log', mutate)
    assert v.verify(**args)['status'] == 'UNKNOWN'


def test_page_or_hash_budget_exhaustion_is_unknown(case, monkeypatch):
    from tools import wd_q4_canonical_ref_verifier as v
    event, setup = case
    args = setup([event])
    monkeypatch.setattr(v, '_MAX_HASH_BUDGET', 0)
    assert v.verify(**args)['status'] == 'UNKNOWN'


def test_more_than_2000_same_minute_rows_across_pages_refuses(case, monkeypatch):
    from tools import wd_q4_canonical_ref_verifier as v
    event, setup = case
    args = setup([event] * 2001)
    monkeypatch.setattr(v, '_MAX_PAGE_ROWS', 500)
    with pytest.raises(v.VerifierRefused, match='window_too_large'):
        v.verify(**args)


def test_public_candidate_with_wrong_roster_identity_is_unknown(case):
    from tools import wd_q4_canonical_ref_verifier as v
    event, setup = case
    bad = dict(event, agent_uuid='ffffffff-ffff-4fff-8fff-ffffffffffff')
    assert v.verify(**setup([event, bad]))['status'] == 'UNKNOWN'

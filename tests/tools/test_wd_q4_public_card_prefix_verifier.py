"""Only synthetic source files; never reads the installed bridge."""
from copy import deepcopy
import hashlib
import json

import pytest
from tools.bridge_compact_view import event_id
from waggledance.core import bridge_log_reader as bridge


def raw(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


def sha(value):
    return hashlib.sha256(value).hexdigest()


@pytest.fixture
def case(tmp_path):
    lanes=('codex-lead-1','codex-tools-1','claude-rco-1','claude-rco-2','fable-5')
    uuids={lane:f'{i:08d}-1111-4111-8111-111111111111' for i,lane in enumerate(lanes,1)}
    nonreview=raw({'schema':'wd.q4-non-review-policy.v1','approved_tasks':['mind-public-knowledge-v1']})
    policy=raw({'schema':'wd.mind-public-card-policy.v1','task_id':'mind-public-knowledge-v1',
                'writers':['codex-lead-1','codex-tools-1','fable-5']})
    roster=raw({'lanes':[{'agent':n,'agent_uuid':uuids[n]} for n in lanes if n!='codex-tools-1'],
                'tools_supervisor':{'agent':'codex-tools-1','agent_uuid':uuids['codex-tools-1']}})
    vocab=raw({'schema':'wd.mind-public-vocabulary.v1','components':['mind.genesis'],'tags':['hex']})
    paths=raw({'schema':'wd.mind-public-paths.v1','paths':['tools/safe.py']})
    event={'ts_utc':'2026-11-06T12:00:30Z','agent':'codex-lead-1','agent_uuid':uuids['codex-lead-1'],
           'type':'status','task_id':'mind-public-knowledge-v1','status':'info','to':'','message':'',
           'paths':[],'write_scope':[], 'payload':{'public_knowledge':{'v':'1','kind':'merged',
           'subject':{'pr':1562},'head':'a'*40,'outcome':'green','next':'none',
           'valid_until_utc':'2026-11-07T12:00:00Z','tags':['hex']}}}
    source=tmp_path/'events.jsonl'; generation=tmp_path/'generation.json'
    generation.write_bytes(raw({'generation':'synthetic-g1'}))
    def make(rows, target=event):
        content=b''.join(raw(row)+b'\n' for row in rows)
        source.write_bytes(content)
        cursor=bridge.read_bridge_log(str(source),generation_path=str(generation)).candidate_cursor
        return dict(enabled=True,source_path=str(source),generation_path=str(generation),end_cursor=cursor,
            expected_prefix_sha256=sha(content),target_event_ref=event_id(target),
            nonreview_policy_bytes=nonreview,nonreview_policy_sha256=sha(nonreview),
            card_policy_bytes=policy,card_policy_sha256=sha(policy),roster_bytes=roster,roster_sha256=sha(roster),
            vocabulary_bytes=vocab,vocabulary_sha256=sha(vocab),path_allowlist_bytes=paths,
            path_allowlist_sha256=sha(paths),now_utc='2026-11-06T13:00:00Z')
    return event,make


def test_off_does_not_read():
    from tools import wd_q4_public_card_prefix_verifier as v
    assert v.verify() == {'status':'OFF'}


def test_scoped_scan_reuses_verified_prefix_without_sharing_mutable_rows(case, monkeypatch):
    from tools import wd_q4_public_card_prefix_verifier as v
    event, make = case
    args = make([event])
    calls = []
    original = bridge.read_bridge_log
    def counted(*a, **kw):
        calls.append(1)
        return original(*a, **kw)
    monkeypatch.setattr(bridge, 'read_bridge_log', counted)
    key = (args['source_path'], args['generation_path'], args['end_cursor'], args['expected_prefix_sha256'])
    with v._snapshot_scan_scope():
        rows, claims = v._read_with_claims(*key)
        assert len(claims) == 1
        rows[event_id(event)]['status'] = 'modified-in-caller'
        second = v._read(*key)
        assert second[event_id(event)]['status'] == event['status']
        assert len(calls) == 1
    v._read(*key)
    assert len(calls) == 2


def test_scoped_scan_rejects_prefix_mutation_but_allows_append(case, monkeypatch):
    from tools import wd_q4_public_card_prefix_verifier as v
    event, make = case
    args = make([event])
    key = (args['source_path'], args['generation_path'], args['end_cursor'], args['expected_prefix_sha256'])
    from pathlib import Path
    source = Path(args['source_path'])
    original = source.read_bytes()
    scans = []
    read = bridge.read_bridge_log
    def counted(*a, **kw):
        scans.append(1)
        return read(*a, **kw)
    monkeypatch.setattr(bridge, 'read_bridge_log', counted)
    with v._snapshot_scan_scope():
        v._read_with_claims(*key)
        with source.open('ab') as stream:
            stream.write(b'{"synthetic":"append"}\n')
        assert event_id(event) in v._read(*key)
        source.write_bytes(original.replace(b'"status":"info"', b'"status":"nope"'))
        with pytest.raises(ValueError):
            v._read(*key)
        source.write_bytes(original)
        assert event_id(event) in v._read(*key)
        assert len(scans) == 2


def test_scan_scope_does_not_leak_into_another_thread(case, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from tools import wd_q4_public_card_prefix_verifier as v
    event, make = case
    args = make([event])
    key = (args['source_path'], args['generation_path'], args['end_cursor'], args['expected_prefix_sha256'])
    scans = []
    read = bridge.read_bridge_log
    def counted(*a, **kw):
        scans.append(1)
        return read(*a, **kw)
    monkeypatch.setattr(bridge, 'read_bridge_log', counted)
    with v._snapshot_scan_scope():
        v._read_with_claims(*key)
        with ThreadPoolExecutor(max_workers=1) as pool:
            assert event_id(event) in pool.submit(v._read, *key).result()
        assert event_id(event) in v._read(*key)
    assert len(scans) == 2


def test_scan_scope_is_not_shared_with_child_async_task(case, monkeypatch):
    import asyncio
    from tools import wd_q4_public_card_prefix_verifier as v
    event, make = case
    args = make([event])
    key = (args['source_path'], args['generation_path'], args['end_cursor'], args['expected_prefix_sha256'])
    scans = []
    read = bridge.read_bridge_log
    def counted(*a, **kw):
        scans.append(1)
        return read(*a, **kw)
    monkeypatch.setattr(bridge, 'read_bridge_log', counted)
    async def check():
        with v._snapshot_scan_scope():
            v._read_with_claims(*key)
            async def child():
                return v._read(*key)
            assert event_id(event) in await asyncio.create_task(child())
            assert event_id(event) in v._read(*key)
    asyncio.run(check())
    assert len(scans) == 2


def test_copied_context_cannot_reuse_rows_after_scope_exits(case, monkeypatch):
    from contextvars import copy_context
    from tools import wd_q4_public_card_prefix_verifier as v
    event, make = case
    args = make([event])
    key = (args['source_path'], args['generation_path'], args['end_cursor'], args['expected_prefix_sha256'])
    scans = []
    read = bridge.read_bridge_log
    def counted(*a, **kw):
        scans.append(1)
        return read(*a, **kw)
    monkeypatch.setattr(bridge, 'read_bridge_log', counted)
    with v._snapshot_scan_scope():
        v._read_with_claims(*key)
        inherited = copy_context()
    assert event_id(event) in inherited.run(v._read, *key)
    assert len(scans) == 2


def test_scope_revokes_rows_if_cleanup_is_interrupted_after_assignment(case, monkeypatch):
    from contextvars import copy_context
    from tools import wd_q4_public_card_prefix_verifier as v
    event, make = case
    args = make([event])
    key = (args['source_path'], args['generation_path'], args['end_cursor'], args['expected_prefix_sha256'])
    original = v._ScanMemo
    class InterruptScope(original):
        def __setattr__(self, name, value):
            super().__setattr__(name, value)
            if name == 'state' and value == (False, None):
                raise KeyboardInterrupt('synthetic cleanup interrupt')
    monkeypatch.setattr(v, '_ScanMemo', InterruptScope)
    with pytest.raises(KeyboardInterrupt):
        with v._snapshot_scan_scope():
            v._read_with_claims(*key)
            inherited = copy_context()
            scope = v._SCAN_MEMO.get()
    assert scope.active is False
    assert scope.entry is None
    assert inherited.run(v._SCAN_MEMO.get).entry is None


def test_scope_revokes_rows_if_cleanup_is_interrupted_before_assignment(case):
    import inspect
    import sys
    from contextvars import copy_context
    from tools import wd_q4_public_card_prefix_verifier as v
    event, make = case
    args = make([event])
    key = (args['source_path'], args['generation_path'], args['end_cursor'], args['expected_prefix_sha256'])
    func = v._snapshot_scan_scope.__wrapped__
    lines, start = inspect.getsourcelines(func)
    target = start + next(i for i, line in enumerate(lines) if 'scope.state = (False, None)' in line)
    fired = []
    def trace(frame, kind, _arg):
        if frame.f_code is func.__code__ and kind == 'line' and frame.f_lineno == target and not fired:
            fired.append(True)
            raise KeyboardInterrupt('synthetic interruption before revocation')
        return trace
    previous = sys.gettrace()
    try:
        with pytest.raises(KeyboardInterrupt):
            with v._snapshot_scan_scope():
                v._read_with_claims(*key)
                inherited = copy_context()
                scope = v._SCAN_MEMO.get()
                sys.settrace(trace)
    finally:
        sys.settrace(previous)
    assert fired
    assert scope.state == (False, None)
    assert inherited.run(v._SCAN_MEMO.get).entry is None


def test_nested_scope_restores_outer_if_set_is_interrupted_before_token(monkeypatch):
    from tools import wd_q4_public_card_prefix_verifier as v
    actual = v._SCAN_MEMO
    class InterruptAfterSet:
        fired = False
        def get(self): return actual.get()
        def reset(self, token): return actual.reset(token)
        def set(self, value):
            token = actual.set(value)
            if not self.fired:
                self.fired = True
                raise KeyboardInterrupt('synthetic interrupt after context set')
            return token
    with v._snapshot_scan_scope():
        outer = actual.get()
        monkeypatch.setattr(v, '_SCAN_MEMO', InterruptAfterSet())
        with pytest.raises(KeyboardInterrupt):
            with v._snapshot_scan_scope():
                raise AssertionError('inner scope must not begin')
        assert actual.get() is outer
        assert outer.active is True


def test_original_envelope_is_verified_within_pinned_prefix(case):
    from tools import wd_q4_public_card_prefix_verifier as v
    event,make=case
    result=v.verify(**make([event]))
    assert result=={'status':'MATCH_WITHIN_PREFIX','canonical_event_ref':event_id(event),
                    'predecessor_refs':[],'completeness':'UNKNOWN'}


def writer_envelope(event):
    """Synthetic shape from pinned Write-AgentEvent.ps1 lines 560-579."""
    event.update(ts_utc='2026-11-06T12:00:30.1234567Z',severity='',run_id='synthetic-run',
                 pid=1234,cwd='C:\\Users\\PRIVATE-CANARY\\project',role='lead-impl',
                 session_id='synthetic-run',capabilities=['implementation','tests','bridge_event'])
    return event


def test_writer_envelope_7_digit_timestamp_returns_original_ref_only(case):
    from tools import wd_q4_public_card_prefix_verifier as v
    event,make=case
    writer_envelope(event)
    args=make([event]); args['now_utc']='2026-11-06T13:00:00.0000000Z'
    out=v.verify(**args)
    assert out=={'status':'MATCH_WITHIN_PREFIX','canonical_event_ref':event_id(event),
                 'predecessor_refs':[],'completeness':'UNKNOWN'}
    assert 'PRIVATE-CANARY' not in repr(out)


def test_seventh_timestamp_digit_cannot_make_future_event_current(case):
    from tools import wd_q4_public_card_prefix_verifier as v
    event,make=case
    writer_envelope(event)
    args=make([event]); args['now_utc']='2026-11-06T12:00:30.1234566Z'
    assert v.verify(**args)=={'status':'UNKNOWN','completeness':'UNKNOWN'}
    args['now_utc']='2026-11-06T12:00:30.1234567Z'
    assert v.verify(**args)['status']=='MATCH_WITHIN_PREFIX'


def test_source_digest_covers_discarded_writer_metadata(case):
    from tools import wd_q4_public_card_prefix_verifier as v
    event,make=case
    writer_envelope(event)
    args=make([event]); original_ref=event_id(event)
    event['cwd']='C:\\Users\\DIFFERENT-PRIVATE\\project'
    args=make([event]); args['target_event_ref']=original_ref
    assert v.verify(**args)=={'status':'UNKNOWN','completeness':'UNKNOWN'}


@pytest.mark.parametrize('fault',['unknown','wrong_pid','wrong_cwd','role','capability','severity','message','run_id'])
def test_writer_envelope_refuses_unknown_or_disallowed_content(case,fault):
    from tools import wd_q4_public_card_prefix_verifier as v
    event,make=case
    writer_envelope(event)
    if fault=='unknown': event['mystery']='secret'
    if fault=='wrong_pid': event['pid']=True
    if fault=='wrong_cwd': event['cwd']='relative/private'
    if fault=='role': event['role']='rco-security'
    if fault=='capability': event['capabilities']=['vote']
    if fault=='severity': event['severity']='private arbitrary text'
    if fault=='message': event['message']='PRIVATE-CANARY'
    if fault=='run_id': event['run_id']='secret\ntext'
    assert v.verify(**make([event]))=={'status':'UNKNOWN','completeness':'UNKNOWN'}


def test_chain_uses_original_not_derived_refs(case):
    from tools import wd_q4_public_card_prefix_verifier as v
    event,make=case
    prior=deepcopy(event); prior['ts_utc']='2026-11-06T11:59:15Z'
    event['payload']['public_knowledge']['supersedes']=event_id(prior)
    out=v.verify(**make([prior,event]))
    assert out['status']=='MATCH_WITHIN_PREFIX' and out['canonical_event_ref']==event_id(event)
    assert out['predecessor_refs']==[event_id(prior)]


def test_three_deep_chain_preserves_original_order(case):
    from tools import wd_q4_public_card_prefix_verifier as v
    event,make=case
    first=deepcopy(event); first['ts_utc']='2026-11-06T11:57:20Z'
    second=deepcopy(event); second['ts_utc']='2026-11-06T11:58:50Z'
    second['payload']['public_knowledge']['supersedes']=event_id(first)
    event['payload']['public_knowledge']['supersedes']=event_id(second)
    result=v.verify(**make([first,second,event]))
    assert result['status']=='MATCH_WITHIN_PREFIX'
    assert result['predecessor_refs']==[event_id(second),event_id(first)]


def test_append_after_pinned_prefix_does_not_claim_later_coverage(case):
    from tools import wd_q4_public_card_prefix_verifier as v
    event,make=case
    args=make([event])
    with open(args['source_path'],'ab') as stream:
        stream.write(raw({'task_id':'unrelated','message':'PRIVATE-CANARY'})+b'\n')
    result=v.verify(**args)
    assert result['status']=='MATCH_WITHIN_PREFIX' and result['completeness']=='UNKNOWN'
    assert 'PRIVATE-CANARY' not in repr(result)


def test_mutated_generation_or_missing_source_is_unknown(case):
    from pathlib import Path
    from tools import wd_q4_public_card_prefix_verifier as v
    event,make=case
    args=make([event]); Path(args['generation_path']).write_bytes(raw({'generation':'different'}))
    assert v.verify(**args)=={'status':'UNKNOWN','completeness':'UNKNOWN'}
    Path(args['source_path']).unlink()
    assert v.verify(**args)=={'status':'UNKNOWN','completeness':'UNKNOWN'}


@pytest.mark.parametrize('pin',['nonreview_policy_sha256','vocabulary_sha256','path_allowlist_sha256'])
def test_each_independent_pin_is_required_before_read(case,pin,monkeypatch):
    from tools import wd_q4_public_card_prefix_verifier as v
    event,make=case
    args=make([event]); args[pin]='f'*64
    monkeypatch.setattr(v.bridge,'read_bridge_log',lambda *a,**kw: pytest.fail('source read'))
    assert v.verify(**args)=={'status':'UNKNOWN','completeness':'UNKNOWN'}


@pytest.mark.parametrize('fault',['missing_prior','wrong_pin','wrong_identity','wrong_generation','wrong_roster',
    'private_message','extra_key','review_lane','bad_subject','same_minute','expired','future','changed_prefix'])
def test_uncertainty_does_not_become_match(case,fault):
    from tools import wd_q4_public_card_prefix_verifier as v
    event,make=case
    prior=deepcopy(event); prior['ts_utc']='2026-11-06T11:59:15Z'
    if fault=='same_minute': prior['ts_utc']='2026-11-06T12:00:01Z'
    if fault=='bad_subject': prior['payload']['public_knowledge']['subject']={'pr':42}
    event['payload']['public_knowledge']['supersedes']=event_id(prior)
    if fault=='private_message': event['message']='PRIVATE-CANARY'
    if fault=='extra_key': event['unknown']='PRIVATE-CANARY'
    if fault=='review_lane': event['agent']='claude-rco-1'
    args=make([event] if fault=='missing_prior' else [prior,event])
    if fault=='wrong_pin': args['card_policy_sha256']='f'*64
    if fault=='wrong_roster': args['roster_sha256']='f'*64
    if fault=='wrong_identity': args['end_cursor']=bridge.BridgeCursor(args['end_cursor'].offset,'posix-v1:0:0','synthetic-g1')
    if fault=='wrong_generation': args['end_cursor']=bridge.BridgeCursor(args['end_cursor'].offset,args['end_cursor'].file_identity,'other')
    if fault=='expired': args['now_utc']='2026-11-07T12:00:00Z'
    if fault=='future': args['now_utc']='2026-11-06T12:00:10Z'
    if fault=='changed_prefix': open(args['source_path'],'ab').write(b'not part of pinned prefix\n'); args['expected_prefix_sha256']='f'*64
    result=v.verify(**args)
    assert result=={'status':'UNKNOWN','completeness':'UNKNOWN'}
    assert 'PRIVATE-CANARY' not in repr(result)

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

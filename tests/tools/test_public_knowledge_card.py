"""Synthetic closed knowledge cards only; no bridge or publication."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json

import pytest
from tools.bridge_compact_view import event_id


def encode(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True).encode('ascii')


def sha(value):
    return hashlib.sha256(value).hexdigest()


@pytest.fixture
def case():
    vocabulary=encode({'schema':'wd.mind-public-vocabulary.v1',
        'components':['mind.genesis','bridge.writer'], 'tags':['hex','cache']})
    paths=encode({'schema':'wd.mind-public-paths.v1','paths':['docs/readme.md','tools/safe.py']})
    event={'ts_utc':'2026-11-06T12:00:00Z','agent':'codex-lead-1','type':'status',
        'task_id':'mind-public-knowledge-v1','status':'info','to':'','message':'','paths':[],
        'write_scope':[], 'payload':{'public_knowledge':{'v':'1','kind':'merged',
        'subject':{'pr':1562},'head':'a'*40,'outcome':'green','next':'none',
        'valid_until_utc':'2026-11-07T12:00:00Z','tags':['hex','cache']}}}
    return {'enabled':True,'event':event,'source_ref':event_id(event),
            'vocabulary_bytes':vocabulary,'expected_vocabulary_sha256':sha(vocabulary),
            'path_allowlist_bytes':paths,'expected_path_allowlist_sha256':sha(paths),
            'now_utc':'2026-11-06T13:00:00Z'}


def validate(case):
    from sandbox.mind.step2 import public_knowledge_card as card
    return card.validate(**case)


def test_default_off_is_inert():
    from sandbox.mind.step2 import public_knowledge_card as card
    class NoTouch:
        def __getattribute__(self,name): raise AssertionError('OFF touched input')
    assert card.validate(event=NoTouch())=={'status':'OFF'}


def test_valid_card_bound_to_event_and_rebuilt(case):
    result=validate(case)
    assert result['status']=='CURRENT'
    assert result['source_ref_sha256']==event_id(case['event'])
    assert result['visibility']=='broadcast' and result['provenance']=='SOURCE_HASH_ONLY'
    assert result['stored_text']=='merged green codex-lead-1 pr:1562 head:'+('a'*40)+' next:none tags:cache,hex'
    assert len(result['stored_text'].encode())<=320


def test_stale_and_expiry_boundary(case):
    case['now_utc']='2026-11-07T12:00:00Z'
    assert validate(case)['status']=='STALE'


@pytest.mark.parametrize('change',[
    'extra_event','secret_field','message','recipient','paths','write_scope','wrong_task','wrong_type','review_lane',
    'extra_payload','extra_card','nested_tag','kind','outcome','next','bad_head','head_wrong_kind','zero_pr',
    'bool_pr','pr_overflow','two_subjects','unknown_component','path_absolute','path_parent','path_private',
    'unlisted_path','unknown_tag','too_many_tags','too_many_dependencies','bad_time','expired_over_seven_days',
    'before_event','missing_source','wrong_source','wrong_vocab_pin','wrong_path_pin','duplicate_json'])
def test_refuses_unclosed_or_unpinned_input(case,change):
    from sandbox.mind.step2 import public_knowledge_card as card
    e=case['event']; data=e['payload']['public_knowledge']
    if change=='extra_event': e['unexpected']='x'
    elif change=='secret_field': e['secret']='synthetic-canary'
    elif change=='message': e['message']='private free text'
    elif change=='recipient': e['to']='fable-5'
    elif change=='paths': e['paths']=['C:/Users/example/private']
    elif change=='write_scope': e['write_scope']=['x']
    elif change=='wrong_task': e['task_id']='other'
    elif change=='wrong_type': e['type']='rco_pass'
    elif change=='review_lane': e['agent']='claude-rco-1'
    elif change=='extra_payload': e['payload']['public_status']=True
    elif change=='extra_card': data['text']='free text'
    elif change=='nested_tag': data['tags']=[{'to_list':['fable-5']}]
    elif change=='kind': data['kind']='approve'
    elif change=='outcome': data['outcome']='approved'
    elif change=='next': data['next']='anything'
    elif change=='bad_head': data['head']='A'*40
    elif change=='head_wrong_kind': data['kind']='blocked'
    elif change=='zero_pr': data['subject']={'pr':0}
    elif change=='bool_pr': data['subject']={'pr':True}
    elif change=='pr_overflow': data['subject']={'pr':10_000_000}
    elif change=='two_subjects': data['subject']={'pr':1,'component':'mind.genesis'}
    elif change=='unknown_component': data['subject']={'component':'unknown.component'}
    elif change=='path_absolute': data['subject']={'repo_path':'C:/Users/x/notes.txt'}
    elif change=='path_parent': data['subject']={'repo_path':'../secret'}
    elif change=='path_private': data['subject']={'repo_path':'sealed/notes.txt'}
    elif change=='unlisted_path': data['subject']={'repo_path':'docs/other.md'}
    elif change=='unknown_tag': data['tags']=['not-known']
    elif change=='too_many_tags': data['tags']=['hex']*9
    elif change=='too_many_dependencies': data['depends_on']=[{'pr':1}]*5
    elif change=='bad_time': data['valid_until_utc']='2026-11-07T12:00:01Z'
    elif change=='expired_over_seven_days': data['valid_until_utc']='2026-11-13T12:01:00Z'
    elif change=='before_event': case['now_utc']='2026-11-06T11:59:00Z'
    elif change=='missing_source': case['source_ref']=None
    elif change=='wrong_source': case['source_ref']='0'*64
    elif change=='wrong_vocab_pin': case['expected_vocabulary_sha256']='0'*64
    elif change=='wrong_path_pin': case['expected_path_allowlist_sha256']='0'*64
    elif change=='duplicate_json':
        case['vocabulary_bytes']=b'{"schema":"wd.mind-public-vocabulary.v1","components":[],"tags":[],"tags":[]}'
        case['expected_vocabulary_sha256']=sha(case['vocabulary_bytes'])
    if change not in ('missing_source','wrong_source'):
        case['source_ref']=event_id(e)
    with pytest.raises(card.CardRefused): validate(case)


def test_allowed_component_and_allowlisted_path(case):
    data=case['event']['payload']['public_knowledge']
    data.update(kind='milestone',subject={'component':'mind.genesis'})
    data.pop('head')
    data['depends_on']=[{'repo_path':'tools/safe.py'},{'pr':12}]
    case['source_ref']=event_id(case['event'])
    assert validate(case)['status']=='CURRENT'


def test_supersession_requires_same_lane_and_exact_prior_ref(case):
    from sandbox.mind.step2 import public_knowledge_card as card
    prior=deepcopy(case['event']); prior['ts_utc']='2026-11-06T11:00:00Z'
    case['event']['payload']['public_knowledge']['supersedes']=event_id(prior)
    case['source_ref']=event_id(case['event'])
    with pytest.raises(card.CardRefused): validate(case)
    case['previous_event']=prior
    assert validate(case)['status']=='CURRENT'
    prior['agent']='fable-5'
    case['event']['payload']['public_knowledge']['supersedes']=event_id(prior)
    case['source_ref']=event_id(case['event'])
    with pytest.raises(card.CardRefused): validate(case)


@pytest.mark.parametrize('path',['.git/config','.ssh/id_rsa','configs/sealed/items.json',
                                 'C:/Users/a/secrets.txt','tools/../private.txt'])
def test_forbidden_path_is_rejected_even_if_allowlisted(case,path):
    from sandbox.mind.step2 import public_knowledge_card as card
    raw=encode({'schema':'wd.mind-public-paths.v1','paths':[path]})
    case['path_allowlist_bytes']=raw
    case['expected_path_allowlist_sha256']=sha(raw)
    case['event']['payload']['public_knowledge']['subject']={'repo_path':path}
    case['source_ref']=event_id(case['event'])
    with pytest.raises(card.CardRefused): validate(case)


def test_line_budget_refuses_excessive_allowed_data(case):
    from sandbox.mind.step2 import public_knowledge_card as card
    path='docs/'+'x'*210+'.md'
    raw=encode({'schema':'wd.mind-public-paths.v1','paths':[path]})
    case['path_allowlist_bytes']=raw
    case['expected_path_allowlist_sha256']=sha(raw)
    data=case['event']['payload']['public_knowledge']
    data['subject']={'repo_path':path}
    data['depends_on']=[{'component':'bridge.writer'},{'component':'mind.genesis'},{'pr':1}]
    case['source_ref']=event_id(case['event'])
    with pytest.raises(card.CardRefused): validate(case)


def test_refusal_does_not_retain_input_in_its_exception_chain(case):
    from sandbox.mind.step2 import public_knowledge_card as card
    case['event']['message']='synthetic-private-canary'
    case['source_ref']=event_id(case['event'])
    try:
        validate(case)
    except card.CardRefused as exc:
        assert exc.__context__ is None and exc.__cause__ is None
        frame=exc.__traceback__
        while frame:
            if frame.tb_frame.f_globals.get('__name__')==card.__name__:
                assert 'synthetic-private-canary' not in repr(frame.tb_frame.f_locals)
            frame=frame.tb_next
    else:
        pytest.fail('expected refusal')


def make_prior(event, timestamp, subject=None, supersedes=None):
    prior=deepcopy(event)
    prior['ts_utc']=timestamp
    data=prior['payload']['public_knowledge']
    data.pop('supersedes',None)
    if subject is not None: data['subject']=subject
    if supersedes is not None: data['supersedes']=supersedes
    return prior


def test_chained_supersession_validates_all_prior_events(case):
    first=make_prior(case['event'],'2026-11-06T10:00:00Z')
    second=make_prior(case['event'],'2026-11-06T11:00:00Z',supersedes=event_id(first))
    case['event']['payload']['public_knowledge']['supersedes']=event_id(second)
    case.update(source_ref=event_id(case['event']),previous_event=second,earlier_events=[first])
    assert validate(case)['status']=='CURRENT'


def test_supersession_accepts_sixteen_verified_predecessors(case):
    chain=[make_prior(case['event'],'2026-11-06T00:00:00Z')]
    for minute in range(1,16):
        chain.append(make_prior(case['event'],f'2026-11-06T00:{minute:02}:00Z',
                                supersedes=event_id(chain[-1])))
    case['event']['payload']['public_knowledge']['supersedes']=event_id(chain[-1])
    case.update(source_ref=event_id(case['event']),previous_event=chain[-1],
                earlier_events=list(reversed(chain[:-1])))
    assert validate(case)['status']=='CURRENT'


def test_supersession_refuses_different_subject_even_same_lane(case):
    from sandbox.mind.step2 import public_knowledge_card as card
    prior=make_prior(case['event'],'2026-11-06T11:00:00Z',subject={'pr':1563})
    case['event']['payload']['public_knowledge']['supersedes']=event_id(prior)
    case.update(source_ref=event_id(case['event']),previous_event=prior)
    with pytest.raises(card.CardRefused): validate(case)


@pytest.mark.parametrize('fault',['missing_ancestor','altered_ancestor','cross_subject','cross_lane',
                                   'extra_ancestor','cycle','non_list','too_long'])
def test_supersession_chain_fails_closed(case,fault):
    from sandbox.mind.step2 import public_knowledge_card as card
    first=make_prior(case['event'],'2026-11-06T10:00:00Z')
    if fault=='cross_subject': first['payload']['public_knowledge']['subject']={'pr':42}
    if fault=='cross_lane': first['agent']='fable-5'
    second=make_prior(case['event'],'2026-11-06T11:00:00Z',supersedes=event_id(first))
    case['event']['payload']['public_knowledge']['supersedes']=event_id(second)
    chain=[first]
    if fault=='missing_ancestor': chain=[]
    elif fault=='altered_ancestor': first['payload']['public_knowledge']['outcome']='red'
    elif fault=='extra_ancestor': chain=[first,deepcopy(first)]
    elif fault=='cycle': second['ts_utc']='2026-11-06T09:00:00Z'; case['event']['payload']['public_knowledge']['supersedes']=event_id(second)
    elif fault=='non_list': chain=(first,)
    elif fault=='too_long': chain=[deepcopy(first) for _ in range(16)]
    case.update(source_ref=event_id(case['event']),previous_event=second,earlier_events=chain)
    with pytest.raises(card.CardRefused): validate(case)

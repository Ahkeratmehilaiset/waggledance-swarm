"""Only locally fabricated Git and source rows; no installed bridge."""
import hashlib
import importlib
import json
from pathlib import Path

import pytest
from tests.tools.test_wd_q4_real_card_generation import real_fixture
from tests.tools import test_shared_card_projection as fx


def sha(value): return hashlib.sha256(value).hexdigest()


def arguments(real_fixture):
    args,_,_,_=real_fixture
    from sandbox.mind.step2 import shared_card_projection as cards
    anchors=json.dumps({'schema':'wd.q4-approved-git-anchors.v1','anchors':args['real_approved_anchors']},
                       sort_keys=True,separators=(',',':')).encode()
    public=fx.raw({'schema':'wd.mind-public-status-policy.v1',
                   'public_tasks':['mind-public-knowledge-v1'],'public_types':['status']})
    decision=json.loads(args['decision_bytes'])
    return dict(enabled=True,mode='real',policy_bytes=args['policy_bytes'],policy_sha256=args['policy_sha256'],
        roster_bytes=args['roster_bytes'],roster_sha256=args['roster_sha256'],
        card_policy_bytes=args['card_policy_bytes'],card_policy_sha256=sha(args['card_policy_bytes']),
        public_policy_bytes=public,public_policy_sha256=sha(public),
        vocabulary_bytes=args['vocabulary_bytes'],vocabulary_sha256=args['vocabulary_sha256'],
        path_allowlist_bytes=args['path_allowlist_bytes'],path_allowlist_sha256=args['path_allowlist_sha256'],
        expected_card_module_sha256=cards.module_sha256(),source_path=args['source_path'],
        generation_path=args['generation_path'],end_cursor=args['end_cursor'],
        expected_prefix_sha256=decision['source_prefix']['sha256'],git_executable=args['real_git_executable'],
        git_executable_sha256=sha(Path(args['real_git_executable']).read_bytes()),
        git_repository=args['real_git_repository'],anchors_bytes=anchors,anchors_sha256=sha(anchors))


def test_default_off_does_not_touch_arguments():
    mod=importlib.import_module('tools.wd_q4_real_card_decision_prepare')
    class Untouchable:
        def __getattribute__(self,_):raise AssertionError('OFF touched input')
    assert mod.prepare(enabled=False,anchors_bytes=Untouchable())=={'status':'OFF'}


@pytest.mark.parametrize('mode',['real','synthetic'])
def test_canonical_pinned_decision_roundtrip(real_fixture,mode):
    mod=importlib.import_module('tools.wd_q4_real_card_decision_prepare')
    from sandbox.mind.step2 import shared_card_projection as cards
    args=arguments(real_fixture);args['mode']=mode
    if mode=='synthetic':
        args['vocabulary_bytes']=fx.VOCABULARY;args['vocabulary_sha256']=sha(fx.VOCABULARY)
    first=mod.prepare(**args);second=mod.prepare(**args)
    assert first==second and first['status']=='PREPARED' and first['source_authentication']=='UNKNOWN'
    assert first['freshness']=='UNKNOWN' and first['card_validation']=='NOT_RUN'
    obj=cards.verify_route_decision(first['decision_bytes'],first['decision_sha256'],
        policy_sha256=args['policy_sha256'],roster_sha256=args['roster_sha256'])
    assert obj['synthetic'] is (mode=='synthetic')
    assert obj['source_prefix']['sha256']==args['expected_prefix_sha256']
    assert ('head_observation_sha256' in obj) is (mode=='real')


def test_verified_append_after_frozen_prefix_is_allowed(real_fixture):
    mod=importlib.import_module('tools.wd_q4_real_card_decision_prepare');args=arguments(real_fixture)
    first=mod.prepare(**args)
    with Path(args['source_path']).open('ab') as stream:
        stream.write(b'{"task_id":"synthetic-unrelated"}\n')
    assert mod.prepare(**args)==first


def test_prefix_changed_between_checks_is_refused(real_fixture,monkeypatch):
    mod=importlib.import_module('tools.wd_q4_real_card_decision_prepare');args=arguments(real_fixture)
    from tools import wd_q4_public_card_prefix_verifier as prefix
    original=prefix._read;calls=[]
    def mutate(*values,**kwargs):
        result=original(*values,**kwargs);calls.append(True)
        if len(calls)==1:Path(args['source_path']).write_bytes(b'changed synthetic content\n')
        return result
    monkeypatch.setattr(prefix,'_read',mutate)
    with pytest.raises(mod.Refused):mod.prepare(**args)
    assert len(calls)==1


def test_decision_parses_pinned_prefix_only_once(real_fixture,monkeypatch):
    mod=importlib.import_module('tools.wd_q4_real_card_decision_prepare');args=arguments(real_fixture)
    from tools import wd_q4_public_card_prefix_verifier as prefix
    original=prefix._read;calls=[]
    def counted(*values,**kwargs):
        calls.append(1)
        return original(*values,**kwargs)
    monkeypatch.setattr(prefix,'_read',counted)
    assert mod.prepare(**args)['status']=='PREPARED'
    assert len(calls)==1


def test_prepared_real_decision_drives_actual_generation(real_fixture):
    pytest.importorskip('faiss')
    mod=importlib.import_module('tools.wd_q4_real_card_decision_prepare')
    from tools import wd_q4_card_generation as producer
    result=mod.prepare(**arguments(real_fixture))
    args,_,_,_=real_fixture
    args=dict(args,decision_bytes=result['decision_bytes'],decision_sha256=result['decision_sha256'])
    assert producer.prepare_card_generation(**args)['status']=='STAGED'


@pytest.mark.parametrize('fault',['policy','roster','card_policy','public_policy','vocab','paths','anchors',
                                  'git','module','prefix','cursor','generation','mode','unknown_anchor'])
def test_mismatch_refuses_without_output(real_fixture,fault):
    mod=importlib.import_module('tools.wd_q4_real_card_decision_prepare');args=arguments(real_fixture)
    mapping={'policy':'policy_sha256','roster':'roster_sha256','card_policy':'card_policy_sha256',
             'public_policy':'public_policy_sha256','vocab':'vocabulary_sha256','paths':'path_allowlist_sha256',
             'anchors':'anchors_sha256','git':'git_executable_sha256','module':'expected_card_module_sha256',
             'prefix':'expected_prefix_sha256'}
    if fault in mapping:args[mapping[fault]]='0'*64
    if fault=='mode':args['mode']='automatic'
    if fault=='cursor':
        from waggledance.core.bridge_log_reader import BridgeCursor
        c=args['end_cursor'];args['end_cursor']=BridgeCursor(c.offset,'wrong',c.generation)
    if fault=='generation':Path(args['generation_path']).write_bytes(b'{"generation":"different"}')
    if fault=='unknown_anchor':
        raw=json.dumps({'schema':'wd.q4-approved-git-anchors.v1','anchors':{'refs/heads/x':'0'*40}},
                       sort_keys=True,separators=(',',':')).encode()
        args['anchors_bytes']=raw;args['anchors_sha256']=sha(raw)
    with pytest.raises(mod.Refused):mod.prepare(**args)

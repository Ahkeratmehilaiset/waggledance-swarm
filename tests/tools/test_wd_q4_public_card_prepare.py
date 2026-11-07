"""Validate typed public-card preparation without writing to a bridge."""
import hashlib
import importlib
import json

import pytest
from tests.tools import test_shared_card_projection as fx


def sha(raw): return hashlib.sha256(raw).hexdigest()


def arguments():
    public=fx.raw({'schema':'wd.mind-public-status-policy.v1',
                   'public_tasks':['mind-public-knowledge-v1'],'public_types':['status']})
    claim={'v':'1','kind':'merged','subject':{'pr':2046},'outcome':'green','next':'none',
           'valid_until_utc':'2026-11-04T10:00:00Z','tags':['genesis'],'head':'1'*40}
    return dict(enabled=True,author=fx.LEAD,agent_uuid=fx.UUIDS[fx.LEAD],created_utc='2026-11-01T10:00:00Z',
                claim=claim,policy_bytes=fx.NONREVIEW,policy_sha256=sha(fx.NONREVIEW),
                public_policy_bytes=public,public_policy_sha256=sha(public),
                card_policy_bytes=fx.POLICY,card_policy_sha256=sha(fx.POLICY),
                roster_bytes=fx.ROSTER,roster_sha256=sha(fx.ROSTER),**fx.PINS)


def test_off_does_not_inspect_input():
    mod=importlib.import_module('tools.wd_q4_public_card_prepare')
    class Untouchable:
        def __getattribute__(self,_):raise AssertionError('OFF touched input')
    assert mod.prepare(enabled=False,claim=Untouchable())=={'status':'OFF'}


def test_prepared_writer_args_pass_closed_synthetic_envelope():
    mod=importlib.import_module('tools.wd_q4_public_card_prepare')
    from tools import wd_q4_public_card_prefix_verifier as verifier
    args=arguments();result=mod.prepare(**args);writer=result['writer_args']
    assert result['status']=='PREPARED' and result['truth']=='UNVERIFIED'
    assert result['timestamp_binding']=='UNKNOWN' and result['source_authentication']=='UNKNOWN'
    assert writer['Agent']==fx.LEAD and writer['AgentUuid']==fx.UUIDS[fx.LEAD]
    assert writer['Type']=='status' and writer['Status']=='info' and writer['TaskId']=='mind-public-knowledge-v1'
    assert writer['Message']==writer['To']=='' and writer['Paths']==writer['WriteScope']==[]
    event={'ts_utc':'2026-11-01T10:00:00.1234567Z','agent':writer['Agent'],'agent_uuid':writer['AgentUuid'],
           'type':writer['Type'],'task_id':writer['TaskId'],'status':writer['Status'],
           'message':writer['Message'],'to':writer['To'],'paths':writer['Paths'],
           'write_scope':writer['WriteScope'],'payload':json.loads(writer['PayloadJson']),
           'severity':'','run_id':'synthetic-run','pid':123,'cwd':'C:\\synthetic\\private'}
    verifier._envelope(event)
    normalized=verifier._minute(verifier._utc(event['ts_utc']))
    assert normalized==result['prepared_minute']
    from tools.bridge_compact_view import event_id
    ref=event_id(event)
    checked=verifier._check(ref,{ref:event},{fx.LEAD},{fx.LEAD:fx.UUIDS[fx.LEAD]},
        verifier._utc('2026-11-01T10:00:59Z'),args['vocabulary_bytes'],args['vocabulary_sha256'],
        args['path_allowlist_bytes'],args['path_allowlist_sha256'])
    assert checked['status']=='MATCH_WITHIN_PREFIX' and checked['completeness']=='UNKNOWN'


@pytest.mark.parametrize('fault',['reviewer','wrong_uuid','bad_policy','bad_roster','bad_vocab',
    'bad_card_policy','non_utc','seconds','free_text','private_path','wrong_truth','supersedes','not_public'])
def test_unsafe_input_is_refused(fault):
    mod=importlib.import_module('tools.wd_q4_public_card_prepare');args=arguments()
    if fault=='reviewer':args['author']='claude-rco-1';args['agent_uuid']=fx.UUIDS['claude-rco-1']
    if fault=='wrong_uuid':args['agent_uuid']=fx.UUIDS[fx.FABLE]
    if fault=='bad_policy':args['policy_sha256']='0'*64
    if fault=='bad_roster':args['roster_sha256']='0'*64
    if fault=='bad_vocab':args['vocabulary_sha256']='0'*64
    if fault=='bad_card_policy':args['card_policy_sha256']='0'*64
    if fault=='non_utc':args['created_utc']='2026-11-01T12:00:00+02:00'
    if fault=='seconds':args['created_utc']='2026-11-01T10:00:01Z'
    if fault=='free_text':args['claim']['notes']='synthetic secret'
    if fault=='private_path':args['claim']['subject']={'repo_path':'C:/Users/private'}
    if fault=='wrong_truth':args['claim']['outcome']='sounds-good'
    if fault=='supersedes':args['claim']['supersedes']='a'*64
    if fault=='not_public':
        raw=fx.raw({'schema':'wd.mind-public-status-policy.v1','public_tasks':[], 'public_types':['status']})
        args['public_policy_bytes']=raw;args['public_policy_sha256']=sha(raw)
    with pytest.raises(mod.Refused):mod.prepare(**args)

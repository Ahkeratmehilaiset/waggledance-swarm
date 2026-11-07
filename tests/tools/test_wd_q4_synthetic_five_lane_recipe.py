"""Synthetic-only first-read gate and five-lane receipt classification."""
import hashlib
import json

import pytest


def raw(value): return json.dumps(value,sort_keys=True,separators=(',',':')).encode()
def sha(value): return hashlib.sha256(value).hexdigest()


@pytest.fixture
def inputs():
    fields={'manifest_sha256':'a'*64,'baseline_sha256':'b'*64,'runtime_manifest_sha256':'c'*64,
            'genesis_pointer_sha256':'d'*64,'runtime_revision':'e'*40,'runtime_generation':'f'*64}
    chain={'schema':'synthetic-real-genesis-chain-prep.v1','status':'PREPARED',
           'authority':'synthetic bundle and manifest; direct materialization bypasses fixed CURRENT composer',
           **{key:value for key,value in fields.items() if key!='runtime_manifest_sha256'}}
    first={'schema':'synthetic-real-genesis-lane.v1','lane':'codex-lead-1','exit_code':0,'status':'VALIDATED',
           'cleanup':'chain_confirmed','answer_status':'ANSWER','result_count':1,'reference_count':1,
           'coverage':'UNKNOWN','freshness':'UNKNOWN','identity':'declared-not-authenticated',
           'stdout_sha256':'1'*64,'stderr_sha256':'2'*64,
           'guard_provision':'lower-level synthetic, anchored CLI not used',**fields}
    return first,chain,fields


def plan(first,chain,fields,**overrides):
    from tools import wd_q4_synthetic_five_lane_recipe as recipe
    receipt=raw(first); report=raw(chain)
    kwargs=dict(enabled=True,first_receipt_bytes=receipt,expected_first_sha256=sha(receipt),
        chain_report_bytes=report,expected_chain_sha256=sha(report),
        expected_runtime_manifest_sha256=fields['runtime_manifest_sha256'],fresh_run_id='synthetic-run-01')
    kwargs.update(overrides)
    return recipe.make_plan(**kwargs)


def test_default_off_and_genuine_synthetic_plan(inputs):
    from tools import wd_q4_synthetic_five_lane_recipe as recipe
    assert recipe.make_plan()=={'status':'OFF'}
    first,chain,fields=inputs
    result=plan(first,chain,fields)
    assert result['status']=='READY_TO_PREPARE_SYNTHETIC_GUARDS'
    assert result['native_execution']=='NOT_RUN' and result['real_agent_use']=='UNKNOWN'
    assert result['guard_existence']=='NOT_CHECKED' and result['fixture_setup']=='NOT_RUN'
    assert len(result['lanes'])==5
    assert len({v['guard_relative'] for v in result['lanes']})==5
    assert all(v['guard_relative'].startswith('synthetic-run-01/') for v in result['lanes'])


@pytest.mark.parametrize('field,value',[
    ('exit_code',2),('exit_code',3),('status','REFUSED'),('cleanup','UNKNOWN'),
    ('lane','fable-5'),('identity','authenticated'),('answer_status','ANSWER'),
    ('coverage','PASS'),('freshness','PASS'),('runtime_revision','0'*40),
])
def test_bad_first_receipt_refuses_without_actions(inputs,field,value):
    first,chain,fields=inputs
    if field=='answer_status': first['reference_count']=0
    else: first[field]=value
    assert plan(first,chain,fields)=={'status':'UNKNOWN'}


def test_pins_and_fresh_identifier_are_mandatory(inputs):
    first,chain,fields=inputs
    for changes in ({'expected_first_sha256':'0'*64},{'expected_chain_sha256':'0'*64},
                    {'expected_runtime_manifest_sha256':'0'*64},{'fresh_run_id':'../old-guard'},
                    {'fresh_run_id':'synthetic-run-01/..'}):
        assert plan(first,chain,fields,**changes)=={'status':'UNKNOWN'}


def test_synthetic_abstention_and_missing_or_duplicate_lane_receipts(inputs):
    from tools import wd_q4_synthetic_five_lane_recipe as recipe
    first,chain,fields=inputs
    first.update(answer_status='ABSTAIN',result_count=0,reference_count=0)
    p=plan(first,chain,fields)
    receipts=[{'schema':'synthetic-real-genesis-lane.v1','lane':item['lane'],'run_id':p['fresh_run_id'],'guard_relative':item['guard_relative'],
               'exit_code':0,'status':'VALIDATED','identity':'declared-not-authenticated',
               'stdout_sha256':'3'*64,'stderr_sha256':'4'*64,
               'coverage':'UNKNOWN','freshness':'UNKNOWN','cleanup':'chain_confirmed',
               'answer_status':'ABSTAIN','result_count':0,'reference_count':0,
               'runtime_manifest_sha256':p['runtime_manifest_sha256'],'runtime_revision':p['runtime_revision'],
               'runtime_generation':p['runtime_generation'],'genesis_pointer_sha256':p['genesis_pointer_sha256'],
               'manifest_sha256':'1'*64,'baseline_sha256':'2'*64}
              for item in p['lanes']]
    kwargs={'expected_fresh_manifest_sha256':'1'*64,'expected_fresh_descriptor_sha256':'2'*64}
    def check(rows,**more):
        encoded=[raw(row) for row in rows]
        pins={row['lane']:sha(data) for row,data in zip(rows,encoded)}
        return recipe.classify_results(p,encoded,expected_receipt_sha256=pins,**{**kwargs,**more})['status']
    assert check(receipts)=='SYNTHETIC_FIVE_RECEIPTS_MATCH'
    assert recipe.classify_results(p,[raw(row) for row in receipts],**kwargs)['status']=='UNKNOWN'
    assert check(receipts,expected_fresh_descriptor_sha256='0'*64)=='UNKNOWN'
    assert check(receipts[:-1])=='UNKNOWN'
    assert check(receipts[:-1]+[receipts[0]])=='UNKNOWN'
    receipts[1]['guard_relative']=receipts[0]['guard_relative']
    assert check(receipts)=='UNKNOWN'
    receipts[1]['guard_relative']=p['lanes'][1]['guard_relative']
    receipts[2]['exit_code']=3
    assert check(receipts)=='UNKNOWN'


def test_cli_refuses_unpinned_or_invalid_real_receipt(inputs,tmp_path,capsys):
    from tools import wd_q4_synthetic_five_lane_recipe as recipe
    first,chain,fields=inputs
    first['exit_code']=2
    a,b=raw(first),raw(chain)
    (tmp_path/'first.json').write_bytes(a); (tmp_path/'chain.json').write_bytes(b)
    assert recipe.main(['--enable',str(tmp_path/'first.json'),sha(a),str(tmp_path/'chain.json'),sha(b),
                        fields['runtime_manifest_sha256'],'synthetic-run-01'])==2
    assert json.loads(capsys.readouterr().out)=={'status':'UNKNOWN'}


def test_cli_uses_explicit_pinned_synthetic_inputs(inputs,tmp_path,capsys):
    from tools import wd_q4_synthetic_five_lane_recipe as recipe
    first,chain,fields=inputs
    a,b=raw(first),raw(chain)
    (tmp_path/'first.json').write_bytes(a)
    (tmp_path/'chain.json').write_bytes(b)
    assert recipe.main(['--enable',str(tmp_path/'first.json'),sha(a),str(tmp_path/'chain.json'),sha(b),
                        fields['runtime_manifest_sha256'],'synthetic-run-01'])==0
    assert json.loads(capsys.readouterr().out)['status']=='READY_TO_PREPARE_SYNTHETIC_GUARDS'

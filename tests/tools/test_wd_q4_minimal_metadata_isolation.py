"""Synthetic metadata-only proofs; never open the real bridge or production data."""
import json
import io
import os
import sys
from pathlib import Path

import pytest

from tests.tools.test_wd_q4_genesis_bootstrap import synthetic  # noqa: F401
from tests.tools import test_wd_q4_genesis_bootstrap as base
from sandbox.mind.step2 import shared_capture_adapter as adapter
from tools import wd_q4_genesis_bootstrap as genesis


CANARY = 'synthetic-excluded-never-index-79ff46'
AUTHORS = ('codex-lead-1', 'codex-tools-1', 'fable-5')


@pytest.mark.skipif(os.name != 'nt', reason='actual private checkpoint needs Windows')
def test_real_genesis_excludes_sensitive_rows_from_all_index_bytes_and_query(synthetic, monkeypatch):
    np=pytest.importorskip('numpy'); pytest.importorskip('faiss')
    from sandbox.mind.step2 import memory, q4, shared_index_checkpoint as checkpoint
    args,_=synthetic
    fake=sys.modules['sandbox.mind.step2.wd_q4_prefix_build_child']; child=fake._actual
    monkeypatch.setitem(sys.modules,fake.__name__,child)
    monkeypatch.setattr(checkpoint,'read_verified_prefix_pointer',fake._real_accessor)
    monkeypatch.setattr(genesis,'_prescan_public_rows',base.REAL_PRESCAN)
    monkeypatch.setattr(child,'_encoder',lambda encoder,identity:
                        lambda texts: np.ones((len(texts),384),dtype=np.float32)/np.sqrt(384))
    model=Path(args['trusted_model_root']); weights=b'synthetic-weights-not-a-model'
    (model/'model.safetensors').write_bytes(weights)
    args['plan_template']['encoder'].update(weights_sha256=base.sha(weights),assets={'model.safetensors':base.sha(weights)})
    args['plan_template'].update(timeout=10,max_prefix_bytes=65536,max_windows=1)
    fleet=json.loads(args['fleet_bytes']); roster={r['agent']:r['agent_uuid'] for r in fleet['lanes']+[fleet['tools_supervisor']]}
    template=json.loads(base.f2_rows(args,['public'])[0])
    rows=[]; expected=[]
    for i in range(12):
        event=dict(template,agent=AUTHORS[i%3],agent_uuid=roster[AUTHORS[i%3]],
                   ts_utc=f'2026-10-31T12:{i:02d}:00Z')
        rows.append(base.raw(event)+b'\n'); expected.append((event['agent'],event['ts_utc'][:16]))
        if i==0: rows.append(base.raw(dict(event,message=CANARY))+b'\n')
        if i==1: rows.append(base.raw(dict(event,to='fable-5',message=CANARY))+b'\n')
        if i==2: rows.append(base.raw(dict(event,paths=[CANARY]))+b'\n')
        if i==3: rows.append(base.raw(dict(event,write_scope=[CANARY]))+b'\n')
        if i==4: rows.append(base.raw(dict(event,payload={'public_status':True,'secret':CANARY}))+b'\n')
        if i==5: rows.append(base.raw(dict(event,cwd={'recipient':CANARY}))+b'\n')
        if i==6: rows.append(base.raw(dict(event,type='review'))+b'\n')
        if i==7: rows.append(base.raw(dict(event,status='veto'))+b'\n')
        if i==8: rows.append(base.raw(dict(event,task_id='synthetic-rco-review'))+b'\n')
        if i==9: rows.append(base.raw(dict(event,agent='claude-rco-1',agent_uuid=roster['claude-rco-1']))+b'\n')
        if i==10: rows.append(base.raw(dict(event,to='claude-rco-2'))+b'\n')
    data=b''.join(rows); Path(args['source_path']).write_bytes(data)
    args.update(max_prefix_bytes=65536,max_bytes=65536,max_rows=128)
    run=dict(args,cutoff_utc='2026-10-31T13:00:00Z',batch_rows=True,renewable=True,
             expected_source_identity=base.identity_of(args['source_path']),
             expected_source_prefix_bytes=len(data),expected_source_prefix_sha256=base.sha(data))
    report=genesis.bootstrap(**run)
    assert report['status']=='PUBLISHED_GENESIS' and report['genesis_initial_prefix_fully_covered'] is True
    root=Path(args['checkpoint_root'])/args['generation']
    projection=json.loads((root/'projection.json').read_bytes())
    # Rows were constructed directly, not issued by the attested publisher.
    # A matching declared roster shape is indexed, not authenticated.
    assert projection['authorship']=='declared_roster_consistent'
    observed=[(r['meta']['agent'],r['time_utc'][:16]) for r in projection['records']]
    assert sorted(observed)==sorted(expected)
    for path in root.rglob('*'):
        if path.is_file(): assert CANARY.encode() not in path.read_bytes(), path.name
    class Encoder:
        def query(self, text): return np.ones(384,dtype=np.float32)/np.sqrt(384)
    result=q4.Q4(memory.Memory(root),Encoder()).ask('which status updates appeared', '2026-10-31T14:00:00Z')
    assert CANARY not in json.dumps(result)
    allowed={r['provenance']['event_ref'] for r in projection['records']}
    assert result['status']=='ANSWER' and result['results']
    assert all(r['ref'] in allowed for r in result['results'])
    # Q4 returns a bounded ranked subset; it does not prove complete recall.
    assert len(result['results']) < len(expected)


@pytest.mark.parametrize('key', sorted(adapter._PUBLIC_ROUTING_KEYS - {'to'}))
def test_nested_routing_alias_excludes_the_entire_status(key):
    from tests.tools import test_mind_pilot_policy as p
    assert not adapter._public_status_event(p.event(cwd={key:CANARY}),frozenset({p.TASK}),frozenset({'status'}))


def test_declared_public_shape_without_publisher_attestation_is_not_authentication():
    from tests.tools import test_mind_pilot_policy as p
    # This shape is synthetic and intentionally has no publisher attestation.
    assert adapter._public_status_event(p.event(),frozenset({p.TASK}),frozenset({'status'}))


@pytest.mark.parametrize('signal', sorted(adapter.projection.EXCLUDED_TYPES))
@pytest.mark.parametrize('field', ['type','status'])
def test_each_review_type_and_status_is_excluded(signal, field):
    from tests.tools import test_mind_pilot_policy as p
    assert adapter._exclude_review_signal(p.event(**{field:signal}))


def test_wrong_uuid_refuses_entire_mixed_public_batch(monkeypatch):
    from sandbox.mind.step2 import test_shared_capture_adapter as fixture
    monkeypatch.setattr(adapter,'_utc_now',lambda:fixture.NOW)
    policy,fleet,roster=fixture.strict_inputs()
    policy_bytes,policy_sha,roster_bytes,roster_sha=fixture.encoded_pins(policy,fleet)
    common=dict(type='status',to='',message='',payload={'public_status':True})
    good=fixture.row(agent_uuid=roster['codex-lead-1'],**common)
    bad=fixture.row('fable-5',agent_uuid=roster['codex-lead-1'],**common)
    with pytest.raises(adapter.AdapterRefused):
        adapter.adapt_rows([good,bad],fixture.snapshot(),ttl_seconds=120,
            policy_bytes=policy_bytes,policy_sha256=policy_sha,roster_bytes=roster_bytes,roster_sha256=roster_sha,
            _public_tasks=fixture.TASKS,_public_types=frozenset({'status'}))


@pytest.mark.skipif(os.name != 'nt', reason='real guarded caller needs Windows')
def test_five_declared_lanes_query_same_descriptor_and_get_same_records(tmp_path, monkeypatch, capsys):
    """Real guarded callers, synthetic peer and query; no actual lane identity is asserted."""
    from tests.tools import test_wd_q4_live_snapshot_e2e as e2e
    from tools import wd_q4_lane_caller_cli as caller
    original=caller.main; comparisons=[]
    def five_calls(argv):
        query=sys.stdin.buffer.read()
        descriptor=caller._configuration()
        fleet=json.loads(descriptor[3])
        lanes={r['agent']:r['agent_uuid'] for r in fleet['lanes']+[fleet['tools_supervisor']]}
        results={}
        for name,uuid in lanes.items():
            with monkeypatch.context() as scope:
                scope.setenv('AGENT_BRIDGE_AGENT',name)
                scope.setenv('AGENT_BRIDGE_AGENT_UUID',uuid)
                scope.setattr(sys,'stdin',io.TextIOWrapper(io.BytesIO(query),encoding='utf-8'))
                assert original(argv)==0
                output=capsys.readouterr()
                assert not output.err
                value=json.loads(output.out)
                assert value['lane']==name and value['status']=='CONTENT_WITHHELD'
                assert value['original_inner_status']=='ANSWER' and value['original_result_count']>0
                assert 'stored_text' not in output.out
                assert value['lane_authentication']=='UNKNOWN'
                results[name]=(value,output.out)
        assert set(results)==caller._LANES
        expected=results['codex-lead-1'][0]
        for value,_ in results.values():
            assert value['descriptor_sha256']==expected['descriptor_sha256']
            assert value['binding']==expected['binding']
            assert value['original_result_refs']==expected['original_result_refs']
            assert value['original_answer_sha256'] != ''
        comparisons.append(expected['descriptor_sha256'])
        sys.stdout.write(results['codex-lead-1'][1])
        return 0
    monkeypatch.setattr(caller,'main',five_calls)
    e2e.test_two_real_fenced_publications_are_selected_by_running_caller(
        tmp_path,monkeypatch,capsys,'none',False,'codex-lead-1')
    assert len(comparisons)==2 and comparisons[0]!=comparisons[1]


@pytest.mark.skipif(os.name != 'nt', reason='actual private checkpoint needs Windows')
def test_late_public_after_512_excluded_rows_uses_real_bounded_skip(synthetic, monkeypatch):
    np=pytest.importorskip('numpy'); pytest.importorskip('faiss')
    from sandbox.mind.step2 import shared_index_checkpoint as checkpoint
    args,_=synthetic
    fake=sys.modules['sandbox.mind.step2.wd_q4_prefix_build_child']; child=fake._actual
    monkeypatch.setitem(sys.modules,fake.__name__,child)
    monkeypatch.setattr(checkpoint,'read_verified_prefix_pointer',fake._real_accessor)
    monkeypatch.setattr(genesis,'_prescan_public_rows',base.REAL_PRESCAN)
    monkeypatch.setattr(child,'_encoder',lambda encoder,identity:
                        lambda texts: np.ones((len(texts),384),dtype=np.float32)/np.sqrt(384))
    model=Path(args['trusted_model_root']); weights=b'synthetic-weights-not-a-model'
    (model/'model.safetensors').write_bytes(weights)
    args['plan_template']['encoder'].update(weights_sha256=base.sha(weights),assets={'model.safetensors':base.sha(weights)})
    args['plan_template'].update(timeout=10,max_prefix_bytes=1024*1024,max_windows=2)
    private=base.f2_rows(args,['private'])[0]; public=base.f2_rows(args,['public'])[0]
    data=private*512+public; Path(args['source_path']).write_bytes(data)
    args.update(max_prefix_bytes=1024*1024,max_bytes=65536,max_rows=32)
    run=dict(args,cutoff_utc='2026-10-31T13:00:00Z',batch_rows=True,allow_partial_genesis=True,
             resume_genesis_skip=True,renewable=True,expected_source_identity=base.identity_of(args['source_path']),
             expected_source_prefix_bytes=len(data),expected_source_prefix_sha256=base.sha(data))
    skips=0
    while True:
        try:
            result=genesis.bootstrap(**run)
            break
        except genesis.GenesisRefused as exc:
            assert str(exc)=='genesis_skip_progress'
            skips+=1
            assert skips<=10
    assert skips==8
    assert json.loads(base.skip_receipt(args))['offset']==len(private)*512
    assert result['status']=='PUBLISHED_GENESIS' and result['genesis_initial_prefix_fully_covered'] is True
    projection=json.loads((Path(args['checkpoint_root'])/args['generation']/'projection.json').read_bytes())
    assert len(projection['records'])==1 and projection['records'][0]['meta']['agent']=='codex-lead-1'

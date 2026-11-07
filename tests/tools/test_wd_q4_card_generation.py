"""Synthetic only: prepare one pinned card generation, never switch a pointer."""
import hashlib
import json
from pathlib import Path
import pytest

from tools import wd_q4_card_generation as producer
from tests.tools import test_shared_card_route as rt


def prepared(tmp_path, events=None):
    events = rt.fx.corpus() if events is None else events
    decision, decision_sha, source = rt.anchor(tmp_path/'source', events)
    out = tmp_path/'out'; out.mkdir()
    args = dict(enabled=True, decision_bytes=decision, decision_sha256=decision_sha,
                vocabulary_bytes=rt.fx.VOCABULARY, vocabulary_sha256=rt.fx.PINS['vocabulary_sha256'],
                path_allowlist_bytes=rt.fx.PATHS, path_allowlist_sha256=rt.fx.PINS['path_allowlist_sha256'],
                now_utc=rt.fx.NOW, output_root=out, generation_name='candidate', encode=rt.fx.encode,
                identity=rt.fx.IDENTITY, dimension=rt.fx.DIM, **source, **rt.pins())
    return args,out


def mixed_prepared(tmp_path,rows,*,heads='all'):
    events=rt.fx.corpus()
    decision,decision_sha,source=rt.anchor(tmp_path/'source',events,prefix_rows=rows,heads=heads)
    out=tmp_path/'out'; out.mkdir()
    args=dict(enabled=True,decision_bytes=decision,decision_sha256=decision_sha,
        vocabulary_bytes=rt.fx.VOCABULARY,vocabulary_sha256=rt.fx.PINS['vocabulary_sha256'],
        path_allowlist_bytes=rt.fx.PATHS,path_allowlist_sha256=rt.fx.PINS['path_allowlist_sha256'],
        now_utc=rt.fx.NOW,output_root=out,generation_name='candidate',encode=rt.fx.encode,
        identity=rt.fx.IDENTITY,dimension=rt.fx.DIM,**source,**rt.pins())
    return args,out


def test_mixed_prefix_ignores_noncard_private_and_review_events(tmp_path):
    pytest.importorskip('faiss')
    cards=rt.fx.corpus()
    noise=[{'task_id':'mind-public-knowledge-v1','type':'status','agent':'claude-rco-1',
            'message':'synthetic-private-secret-canary','payload':{'kind':'checkpoint'}},
           {'task_id':'unrelated','type':'status','message':'synthetic-private-secret-canary'},
           {'task_id':'mind-public-knowledge-v1','payload':None}]
    args,out=mixed_prepared(tmp_path,noise[:1]+cards+noise[1:])
    result=producer.prepare_card_generation(**args)
    assert result['status']=='STAGED'
    for path in (out/'candidate').rglob('*'):
        if path.is_file(): assert b'synthetic-private-secret-canary' not in path.read_bytes()


@pytest.mark.parametrize('fault',['private_message','private_path','directed','review_author','malformed_card','extra_payload','wrong_task','foreign_uuid'])
def test_invalid_card_claim_refuses_all_before_write(tmp_path,fault):
    cards=rt.fx.corpus(); bad=json.loads(json.dumps(cards[0]))
    if fault=='private_message': bad['message']='synthetic-private-secret-canary'
    if fault=='private_path': bad['paths']=['C:/synthetic/private']
    if fault=='directed': bad['to']='claude-rco-1'
    if fault=='review_author': bad['agent']='claude-rco-1'
    if fault=='malformed_card': bad['payload']['public_knowledge']=None
    if fault=='extra_payload': bad['payload']['other']='synthetic-private-secret-canary'
    if fault=='wrong_task': bad['task_id']='unrelated'
    if fault=='foreign_uuid': bad['agent_uuid']=rt.fx.UUIDS[rt.fx.FABLE]
    args,out=mixed_prepared(tmp_path,cards+[bad])
    with pytest.raises(producer.CardGenerationRefused): producer.prepare_card_generation(**args)
    assert list(out.iterdir())==[]


def test_prefix_without_a_card_claim_refuses_before_write(tmp_path):
    args,out=mixed_prepared(tmp_path,[{'task_id':'mind-public-knowledge-v1','payload':{'kind':'checkpoint'}}])
    with pytest.raises(producer.CardGenerationRefused): producer.prepare_card_generation(**args)
    assert list(out.iterdir())==[]


def test_unknown_head_is_refused_before_writing(tmp_path):
    args,out=mixed_prepared(tmp_path,rt.fx.corpus(),heads=None)
    with pytest.raises(producer.CardGenerationRefused): producer.prepare_card_generation(**args)
    assert list(out.iterdir())==[]


def test_unobserved_head_is_refused_before_writing(tmp_path):
    args,out=mixed_prepared(tmp_path,rt.fx.corpus(),heads=['1'*40])
    with pytest.raises(producer.CardGenerationRefused): producer.prepare_card_generation(**args)
    assert list(out.iterdir())==[]


def test_default_off_does_not_touch_any_inputs(tmp_path):
    class Untouchable:
        def __getattribute__(self, _name): raise AssertionError('input accessed')
    assert producer.prepare_card_generation(decision_bytes=Untouchable()) == {'status':'OFF'}
    assert list(tmp_path.iterdir()) == []


def test_anchored_prefix_builds_generation_without_pointer_change(tmp_path):
    pytest.importorskip('faiss')
    args,out=prepared(tmp_path); pointer=out/'q4-live-current.json'; pointer.write_bytes(b'unchanged synthetic')
    result=producer.prepare_card_generation(**args)
    directory=out/'candidate'; memory=(directory/'memory.json').read_bytes(); descriptor=(directory/'descriptor.json').read_bytes()
    assert result['status']=='STAGED' and result['completeness']=='UNKNOWN'
    assert hashlib.sha256(memory).hexdigest()==result['memory_sha256']
    assert hashlib.sha256(descriptor).hexdigest()==result['descriptor_sha256']
    assert pointer.read_bytes()==b'unchanged synthetic'
    manifest=json.loads(memory)
    assert manifest['card_route']['source_status']=='MATCH_WITHIN_PREFIX'
    assert manifest['card_route']['completeness']=='UNKNOWN'


@pytest.mark.parametrize('fault',['decision_pin','source_changed','policy_pin','roster_pin'])
def test_untrusted_inputs_refuse_before_output(tmp_path,fault):
    args,out=prepared(tmp_path)
    if fault=='decision_pin': args['decision_sha256']='0'*64
    if fault=='source_changed': Path(args['source_path']).write_bytes(b'changed synthetic')
    if fault=='policy_pin': args['policy_sha256']='0'*64
    if fault=='roster_pin': args['roster_sha256']='0'*64
    with pytest.raises(producer.CardGenerationRefused): producer.prepare_card_generation(**args)
    assert list(out.iterdir()) == []


def test_failed_build_after_write_is_unknown_and_never_retried(tmp_path,monkeypatch):
    args,out=prepared(tmp_path); calls=[]
    def partial(*_a,**_kw):
        calls.append(1); (out/'candidate').mkdir(); (out/'candidate'/'partial').write_text('synthetic')
        raise RuntimeError('synthetic')
    monkeypatch.setattr(producer.cards,'build_shared_route_card_memory',partial)
    with pytest.raises(producer.CardGenerationUnknown): producer.prepare_card_generation(**args)
    assert calls==[1] and (out/'candidate'/'partial').read_text()=='synthetic'


def test_interrupted_build_preserves_unknown_and_is_not_swallowed(tmp_path,monkeypatch):
    args,out=prepared(tmp_path)
    def interrupted(*_a,**_kw):
        (out/'candidate').mkdir(); (out/'candidate'/'partial').write_text('synthetic')
        raise KeyboardInterrupt()
    monkeypatch.setattr(producer.cards,'build_shared_route_card_memory',interrupted)
    with pytest.raises(producer.CardGenerationInterrupted):
        try:
            producer.prepare_card_generation(**args)
        except Exception:
            pytest.fail('ordinary exception handling swallowed interruption')
    assert (out/'candidate'/'partial').read_text()=='synthetic'


def test_built_generation_is_queryable_by_existing_synthetic_reader(tmp_path,monkeypatch):
    import datetime as dt
    import io
    pytest.importorskip('faiss')
    from sandbox.mind.step2 import runtime_launcher, wd_q4_private_read as reader
    from tools import wd_q4_agent_query as q4, wd_q4_resolved_child as child
    args,out=prepared(tmp_path); built=producer.prepare_card_generation(**args); directory=out/'candidate'
    monkeypatch.setattr(reader,'ENCODER_FACTORY',lambda **_kw: rt.fx.Encoder())
    monkeypatch.setattr(reader,'bind_runtime_encoder',lambda *_a,**_kw: None)
    monkeypatch.setattr(q4,'_utc_now',lambda:dt.datetime(2026,11,3,tzinfo=dt.timezone.utc))
    def launcher(*,query,**_kw):
        situation=json.loads(query)
        answer=reader._private_read_locked(directory,directory/'descriptor.json',built['descriptor_sha256'],
            situation['situation'],situation['time'],r'C:\fake\model',rt.fx.IDENTITY['revision'])
        return runtime_launcher._validate_output(json.dumps(answer).encode(),0,built['descriptor_sha256'])
    monkeypatch.setattr(q4,'_invoke_launcher',launcher)
    memory=(directory/'memory.json').read_bytes(); projection=(directory/'projection.json').read_bytes()
    data=dict(rt.pins(),memory_bytes=memory,memory_sha256=built['memory_sha256'],projection_bytes=projection,
              projection_sha256=hashlib.sha256(projection).hexdigest(),card_decision_bytes=args['decision_bytes'],
              card_decision_sha256=args['decision_sha256'])
    out_stream=io.StringIO(); request=json.dumps({'situation':'blocked red mind genesis needs operator disk',
                                                   'time':'2026-11-02T00:00:00Z'}).encode()
    code=child.run_resolved(rt.config(directory,built['descriptor_sha256']),io.BytesIO(request),out_stream,**data)
    answer=json.loads(out_stream.getvalue())
    assert code==0 and answer['status']=='ANSWER'
    assert any(item['stored_text'].startswith('blocked red codex-tools-1 component:mind.genesis') for item in answer['results'])
    assert 'ZQ-PRIVATE-CANARY' not in out_stream.getvalue()

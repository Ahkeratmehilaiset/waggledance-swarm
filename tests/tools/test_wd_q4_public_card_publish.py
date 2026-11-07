"""Only synthetic writer receipts; never invoke the operator bridge."""
import importlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from tests.tools.test_wd_q4_public_card_prepare import arguments


def setup(monkeypatch):
    mod=importlib.import_module('tools.wd_q4_public_card_publish')
    args=arguments(); claim=args.pop('claim')
    args.pop('enabled'); args.pop('author'); args.pop('agent_uuid'); args.pop('created_utc')
    monkeypatch.setenv('AGENT_BRIDGE_AGENT','codex-lead-1')
    from tests.tools import test_shared_card_projection as fx
    monkeypatch.setenv('AGENT_BRIDGE_AGENT_UUID',fx.UUIDS[fx.LEAD])
    monkeypatch.setenv('AGENT_BRIDGE_SESSION_ID','wd-reboot-synthetic')
    monkeypatch.setenv('AGENT_BRIDGE_RUN_ID','wd-reboot-synthetic')
    monkeypatch.setattr(mod,'_minute_now',lambda:'2026-11-01T10:00:00Z')
    monkeypatch.setattr(mod.status_writer,'_attest',lambda:{'pin_status':'manifest_and_launcher_verified',
        'observed_agent':'codex-lead-1','native_conversation_id':'synthetic-conversation'})
    monkeypatch.setattr(mod.status_writer,'_pinned_helpers',lambda:(Path('C:/synthetic/evidence.ps1'),Path('C:/synthetic/Write-AgentEvent.ps1')))
    monkeypatch.setattr(mod.status_writer,'_active_environment',lambda:{})
    return mod,claim,args,fx


def receipt(command,fx,*,change=None):
    payload=json.loads(command[command.index('-PayloadJson')+1])
    event={'ts_utc':'2026-11-01T10:00:30.1234567Z','agent':fx.LEAD,'agent_uuid':fx.UUIDS[fx.LEAD],
           'type':'status','task_id':'mind-public-knowledge-v1','status':'info','to':'','message':'',
           'paths':[],'write_scope':[],'payload':payload,'severity':'','run_id':'wd-reboot-synthetic',
           'session_id':'wd-reboot-synthetic','pid':123,'cwd':'C:\\synthetic'}
    delivery={'schema':'waggledance.bridge.delivery-receipt.v1','accepted':True,
              'delivery_status':'canonical','canonical_durable':True}
    if change: change(event,delivery)
    event['_bridge_delivery']=delivery
    return SimpleNamespace(stdout=json.dumps(event),returncode=0)


def test_default_off_never_touches_bridge():
    mod=importlib.import_module('tools.wd_q4_public_card_publish')
    class Untouchable:
        def __getattribute__(self,_): raise AssertionError('input touched')
    assert mod.publish(claim=Untouchable())=={'status':'OFF'}


def test_pinned_writer_receipt_checked_after_single_append(monkeypatch):
    mod,claim,args,fx=setup(monkeypatch); calls=[]
    def run(command,**kwargs):
        calls.append(command)
        assert command[command.index('-TaskId')+1]=='mind-public-knowledge-v1'
        return receipt(command,fx)
    monkeypatch.setattr(mod.subprocess,'run',run)
    result=mod.publish(enabled=True,claim=claim,**args)
    assert result['status']=='WRITTEN' and result['delivery']=='canonical'
    assert result['source_authentication']==result['publisher_authentication']=='UNKNOWN'
    assert len(result['event_ref_sha256'])==64 and len(calls)==1


def test_writer_crossing_a_minute_boundary_is_validated_as_written(monkeypatch):
    mod,claim,args,fx=setup(monkeypatch); calls=[]
    def run(command,**kwargs):
        calls.append(command)
        return receipt(command,fx,change=lambda event,delivery: event.update(ts_utc='2026-11-01T10:01:00Z'))
    monkeypatch.setattr(mod.subprocess,'run',run)
    result=mod.publish(enabled=True,claim=claim,**args)
    assert result['status']=='WRITTEN' and len(calls)==1


def test_card_expiring_at_next_minute_refuses_before_append(monkeypatch):
    mod,claim,args,fx=setup(monkeypatch)
    claim['valid_until_utc']='2026-11-01T10:01:00Z'
    monkeypatch.setattr(mod.subprocess,'run',lambda *_a,**_kw:pytest.fail('must not append expiring card'))
    with pytest.raises(mod.Refused):
        mod.publish(enabled=True,claim=claim,**args)


def test_two_minute_validity_survives_one_minute_rollover(monkeypatch):
    mod,claim,args,fx=setup(monkeypatch)
    claim['valid_until_utc']='2026-11-01T10:02:00Z'
    monkeypatch.setattr(mod.subprocess,'run',lambda command,**_kw: receipt(
        command,fx,change=lambda event,delivery: event.update(ts_utc='2026-11-01T10:01:59Z')))
    assert mod.publish(enabled=True,claim=claim,**args)['status']=='WRITTEN'


@pytest.mark.parametrize('fault',['uuid','session','payload','timestamp','timestamp_before','private','delivery','exception','interrupt'])
def test_post_entry_failure_is_unknown_and_never_retried(monkeypatch,fault):
    mod,claim,args,fx=setup(monkeypatch);calls=[]
    def run(command,**kwargs):
        calls.append(command)
        if fault=='exception':raise TimeoutError('synthetic private diagnostic')
        if fault=='interrupt':raise KeyboardInterrupt()
        def change(event,delivery):
            if fault=='uuid':event['agent_uuid']=fx.UUIDS[fx.FABLE]
            if fault=='session':event['session_id']='different-session'
            if fault=='payload':event['payload']['public_knowledge']['outcome']='red'
            if fault=='timestamp':event['ts_utc']='2026-11-01T10:02:00Z'
            if fault=='timestamp_before':event['ts_utc']='2026-11-01T09:59:59.9999999Z'
            if fault=='private':event['message']='synthetic secret'
            if fault=='delivery':delivery['accepted']=False
        return receipt(command,fx,change=change)
    monkeypatch.setattr(mod.subprocess,'run',run)
    with pytest.raises(BaseException if fault=='interrupt' else mod.Unknown):
        mod.publish(enabled=True,claim=claim,**args)
    assert len(calls)==1


def test_queued_receipt_is_not_reported_as_canonical(monkeypatch):
    mod,claim,args,fx=setup(monkeypatch)
    def run(command,**kwargs):
        def queued(event,delivery):
            delivery.update(delivery_status='queued',canonical_durable=False,
                            retained_wal_path='C:\\synthetic\\wal',retained_wal_sha256='a'*64)
        return receipt(command,fx,change=queued)
    monkeypatch.setattr(mod.subprocess,'run',run)
    result=mod.publish(enabled=True,claim=claim,**args)
    assert result['status']=='QUEUED' and result['delivery']=='queued'
    assert result['retained_wal_sha256']=='a'*64 and 'synthetic' not in json.dumps(result)


def test_pinned_helper_refusal_happens_before_append(monkeypatch):
    mod,claim,args,fx=setup(monkeypatch)
    monkeypatch.setattr(mod.status_writer,'_pinned_helpers',lambda: (_ for _ in ()).throw(ValueError()))
    monkeypatch.setattr(mod.subprocess,'run',lambda *_a,**_kw:pytest.fail('must not append'))
    with pytest.raises(mod.Refused): mod.publish(enabled=True,claim=claim,**args)


def test_invalid_claim_and_session_fail_before_writer(monkeypatch):
    mod,claim,args,fx=setup(monkeypatch)
    monkeypatch.setattr(mod.subprocess,'run',lambda *_a,**_kw:pytest.fail('must not append'))
    claim['valid_until_utc']='2026-11-04T10.00.00Z'
    with pytest.raises(mod.Refused): mod.publish(enabled=True,claim=claim,**args)
    claim['valid_until_utc']='2026-11-04T10:00:00Z'
    monkeypatch.setenv('AGENT_BRIDGE_AGENT','claude-rco-1')
    with pytest.raises(mod.Refused): mod.publish(enabled=True,claim=claim,**args)

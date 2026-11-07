"""Opt-in typed public-card append using the inherited, manifest-pinned bridge.

The supplied policies and the card are caller assertions, not authentication
or proof of fact. The event is verified only against the writer's own receipt;
neither the host clock nor publisher is authenticated here. A queued event is
not a canonical append. An uncertain attempt is never retried automatically.
"""
import datetime as dt
import json
import os
import re
import subprocess

from tools import wd_mind_public_event_writer as status_writer
from tools import wd_q4_public_card_prepare as prepare
from tools import wd_q4_public_card_prefix_verifier as verifier


class Refused(ValueError):
    """Nothing was submitted to the bridge writer."""


class Unknown(RuntimeError):
    """The writer was entered; the append outcome requires reconciliation."""


class Interrupted(BaseException):
    """An interrupted writer attempt may have appended; never blindly retry."""


def _minute_now():
    return dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:00Z')


def _receipt(raw, prepared, policies, run_id, session_id):
    if type(raw) is not str or len(raw)>65536: raise ValueError()
    obj=json.loads(raw,object_pairs_hook=verifier.capture._strict_pairs,
                   parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    if type(obj) is not dict: raise ValueError()
    delivery=obj.pop('_bridge_delivery')
    args=prepared['writer_args']
    expected={'agent':args['Agent'],'agent_uuid':args['AgentUuid'],'type':'status',
        'task_id':'mind-public-knowledge-v1','status':'info','to':'','message':'',
        'paths':[],'write_scope':[],'payload':json.loads(args['PayloadJson']),
        'run_id':run_id,'session_id':session_id}
    if any(obj.get(key)!=value for key,value in expected.items()): raise ValueError()
    verifier._envelope(obj)
    when=verifier._utc(obj['ts_utc'])
    if verifier._minute(when)!=prepared['prepared_minute']: raise ValueError()
    tasks,roster=verifier.capture.strict_pins(policies['policy_bytes'],policies['policy_sha256'],
        policies['roster_bytes'],policies['roster_sha256'])
    writers=verifier._policy(policies['card_policy_bytes'],policies['card_policy_sha256'],tasks)
    ref=verifier.event_id(obj)
    checked=verifier._check(ref,{ref:obj},writers,roster,when,policies['vocabulary_bytes'],
        policies['vocabulary_sha256'],policies['path_allowlist_bytes'],policies['path_allowlist_sha256'])
    if checked.get('status')!='MATCH_WITHIN_PREFIX': raise ValueError()
    if (type(delivery) is not dict or delivery.get('schema')!='waggledance.bridge.delivery-receipt.v1'
            or delivery.get('accepted') is not True or type(delivery.get('canonical_durable')) is not bool
            or delivery.get('delivery_status') not in ('canonical','queued')
            or (delivery['delivery_status']=='canonical') != delivery['canonical_durable']): raise ValueError()
    result={'status':'WRITTEN' if delivery['canonical_durable'] else 'QUEUED',
            'delivery':delivery['delivery_status'],'event_ref_sha256':ref,
            'truth':'UNVERIFIED','source_authentication':'UNKNOWN','publisher_authentication':'UNKNOWN',
            'clock_authentication':'UNKNOWN'}
    if not delivery['canonical_durable']:
        wal=delivery.get('retained_wal_sha256')
        if (type(wal) is not str or re.fullmatch(r'[a-f0-9]{64}',wal) is None
                or type(delivery.get('retained_wal_path')) is not str or not delivery['retained_wal_path']):
            raise ValueError()
        result['retained_wal_sha256']=wal
    return result


def publish(*, enabled=False, claim=None, **policies):
    if enabled is False: return {'status':'OFF'}
    prepared=None; command=None
    try:
        if enabled is not True: raise ValueError()
        agent=os.environ.get('AGENT_BRIDGE_AGENT',''); uuid=os.environ.get('AGENT_BRIDGE_AGENT_UUID','')
        run_id=os.environ.get('AGENT_BRIDGE_RUN_ID',''); session_id=os.environ.get('AGENT_BRIDGE_SESSION_ID','')
        if any(not status_writer._FRAME.fullmatch(os.environ.get(key,'')) for key in
               ('AGENT_BRIDGE_SESSION_ID','AGENT_BRIDGE_RUN_ID')): raise ValueError()
        evidence=status_writer._attest()
        if (evidence.get('pin_status')!='manifest_and_launcher_verified'
                or evidence.get('observed_agent')!=agent or not evidence.get('native_conversation_id')):
            raise ValueError()
        prepared=prepare.prepare(enabled=True,author=agent,agent_uuid=uuid,created_utc=_minute_now(),
                                 claim=claim,**policies)
        _,writer=status_writer._pinned_helpers()
        env=status_writer._active_environment()
        env.pop('AGENT_BRIDGE_ROLE',None); env.pop('AGENT_BRIDGE_CAPABILITIES',None)
        args=prepared['writer_args']
        command=[status_writer._powershell(),'-NoProfile','-NonInteractive','-File',str(writer),
                 '-Agent',agent,'-AgentUuid',uuid,'-Type','status','-TaskId','mind-public-knowledge-v1',
                 '-Status','info','-PayloadJson',args['PayloadJson'],'-ReceiptJson']
    except Exception:
        pass
    if prepared is None or command is None: raise Refused('public_card_publish_refused')
    result=None; interrupted=False
    try:
        process=subprocess.run(command,cwd=str(status_writer.ROOT),env=env,capture_output=True,
                               text=True,timeout=60,check=True)
        if process.returncode!=0: raise ValueError()
        result=_receipt(process.stdout,prepared,policies,run_id,session_id)
    except Exception:
        pass
    except BaseException:
        interrupted=True
    finally:
        claim=policies=prepared=command=env=None
    if interrupted: raise Interrupted('public_card_publish_unknown')
    if result is None: raise Unknown('public_card_publish_unknown')
    return result

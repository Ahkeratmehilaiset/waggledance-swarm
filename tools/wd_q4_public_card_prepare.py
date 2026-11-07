"""Prepare a typed public-card broadcast, without writing to the bridge.

The closed claim, its target and outcome are caller assertions, not verified
facts. Policy and roster pins are supplied by the caller and do not authenticate
publisher or source. The caller must separately bind and validate the actual
writer event: Write-AgentEvent creates its own timestamp and metadata. No free
text, review votes, implicit publication or authority is introduced here.
"""
import hashlib
import json


class Refused(ValueError):
    """Input could not be prepared; contains no caller data."""


def _json(value):
    return json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode('utf-8')


def _allowed_subject(subject):
    return type(subject) is dict and len(subject)==1 and next(iter(subject)) in ('pr','component')


def prepare(*, enabled=False, author=None, agent_uuid=None, created_utc=None, claim=None,
            policy_bytes=None, policy_sha256=None, public_policy_bytes=None, public_policy_sha256=None,
            card_policy_bytes=None, card_policy_sha256=None, roster_bytes=None, roster_sha256=None,
            vocabulary_bytes=None, vocabulary_sha256=None, path_allowlist_bytes=None, path_allowlist_sha256=None):
    if enabled is False: return {'status':'OFF'}
    try:
        if enabled is not True: raise ValueError()
        from sandbox.mind.step2 import public_knowledge_card as cards
        from sandbox.mind.step2 import shared_capture_adapter as capture
        from tools import wd_q4_public_card_prefix_verifier as verifier
        tasks,roster=capture.strict_pins(policy_bytes,policy_sha256,roster_bytes,roster_sha256)
        public_tasks,public_types=capture.public_status_policy(public_policy_bytes,public_policy_sha256,tasks)
        writers=verifier._policy(card_policy_bytes,card_policy_sha256,tasks)
        if ('mind-public-knowledge-v1' not in public_tasks or 'status' not in public_types
                or type(author) is not str or author not in cards._LANES or author not in writers
                or type(agent_uuid) is not str or roster.get(author)!=agent_uuid): raise ValueError()
        # JSON serialization makes a detached, closed copy and disallows NaN.
        claim_raw=_json(claim)
        if len(claim_raw)>8192: raise ValueError()
        value=json.loads(claim_raw.decode('utf-8'))
        if (type(value) is not dict or not cards._REQUIRED<=set(value)
                or not set(value)<=cards._REQUIRED|{'head','depends_on'}
                or not _allowed_subject(value['subject'])
                or type(value.get('depends_on',[])) is not list
                or any(not _allowed_subject(s) for s in value.get('depends_on',[]))): raise ValueError()
        # This validates the exact fixed-width UTC minute; it is not a trusted clock.
        cards._time(created_utc)
        event={'ts_utc':created_utc,'agent':author,'type':'status','task_id':'mind-public-knowledge-v1',
               'status':'info','to':'','message':'','paths':[],'write_scope':[],
               'payload':{'public_knowledge':value}}
        source_ref=hashlib.sha256(_json(event)).hexdigest()
        checked=cards.validate(enabled=True,event=event,source_ref=source_ref,vocabulary_bytes=vocabulary_bytes,
            expected_vocabulary_sha256=vocabulary_sha256,path_allowlist_bytes=path_allowlist_bytes,
            expected_path_allowlist_sha256=path_allowlist_sha256,now_utc=created_utc)
        if checked.get('status')!='CURRENT': raise ValueError()
        payload=_json(event['payload']).decode('utf-8')
        return {'status':'PREPARED','prepared_minute':created_utc,'truth':'UNVERIFIED',
                'timestamp_binding':'UNKNOWN','source_authentication':'UNKNOWN','publisher_authentication':'UNKNOWN',
                'writer_args':{'Agent':author,'AgentUuid':agent_uuid,'Type':'status',
                               'TaskId':'mind-public-knowledge-v1','Status':'info','To':'','Message':'',
                               'Paths':[],'WriteScope':[],'PayloadJson':payload}}
    except Exception:
        raise Refused('public_card_prepare_refused') from None

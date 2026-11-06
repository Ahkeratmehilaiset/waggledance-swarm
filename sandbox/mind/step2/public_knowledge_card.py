"""Synthetic opt-in validator for closed broadcast knowledge cards.

No bridge reader, publisher, adapter, policy or production integration. The
caller supplies reviewable vocabulary/allowlist bytes and independently pinned
hashes. A source hash proves only exact supplied JSON, not that an event was
broadcast, who wrote it, or that a clock is trustworthy. Real bridge envelopes,
policy authorization, identity and source resolution need separate work.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re

_HEX64 = re.compile(r'[0-9a-f]{64}\Z')
_HEAD = re.compile(r'[0-9a-f]{40}\Z')
_TOKEN = re.compile(r'[a-z][a-z0-9_.-]{0,47}\Z')
_PATH = re.compile(r'[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*\Z')
_MINUTE = re.compile(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:00Z\Z')
_LANES = frozenset({'codex-lead-1','codex-tools-1','fable-5'})
_KINDS = frozenset({'merged','landed_on_branch','blocked','unblocked','milestone','handoff',
                    'available','test_result','bundle_deployed','superseded'})
_OUTCOMES = frozenset({'green','red','partial','refused','unknown'})
_NEXT = frozenset({'none','needs_review','needs_operator','needs_fix','waits_dependency'})
_HEAD_KINDS = frozenset({'merged','landed_on_branch','test_result','bundle_deployed'})
_EVENT = frozenset({'ts_utc','agent','type','task_id','status','to','message','paths','write_scope','payload'})
_REQUIRED = frozenset({'v','kind','subject','outcome','next','valid_until_utc','tags'})
_OPTIONAL = frozenset({'head','depends_on','supersedes'})
_FORBIDDEN_PATH_PARTS = frozenset({'users','private','sealed','gold','heldout','judge','secrets',
                                  '.codex-audit','.agent-bridge','.git','.ssh','.aws'})


class CardRefused(ValueError):
    """Invalid or unpinned input; never include input values in the reason."""


def _digest(raw):
    return hashlib.sha256(raw).hexdigest()


def _pairs(pairs):
    result={}
    for key,value in pairs:
        if key in result: raise ValueError()
        result[key]=value
    return result


def _document(raw,pin,schema,keys):
    if (type(raw) is not bytes or not 0<len(raw)<=65536 or type(pin) is not str
            or not _HEX64.fullmatch(pin) or _digest(raw)!=pin): raise ValueError()
    data=json.loads(raw.decode('utf-8'),object_pairs_hook=_pairs,
                    parse_constant=lambda value: (_ for _ in ()).throw(ValueError()))
    if type(data) is not dict or set(data)!=keys or data['schema']!=schema: raise ValueError()
    return data


def _time(value):
    if type(value) is not str or not _MINUTE.fullmatch(value): raise ValueError()
    return datetime.strptime(value,'%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)


def _event_id(event):
    raw=json.dumps(event,sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode('utf-8')
    if len(raw)>8192: raise ValueError()
    return _digest(raw)


def _path(value):
    if type(value) is not str or len(value)>240 or not _PATH.fullmatch(value): raise ValueError()
    parts=value.split('/')
    if any(part in ('.','..') or part.endswith('.') or part.lower() in _FORBIDDEN_PATH_PARTS for part in parts):
        raise ValueError()
    return value


def _subject(value,components,paths):
    if type(value) is not dict or len(value)!=1: raise ValueError()
    key,item=next(iter(value.items()))
    if key=='pr' and type(item) is int and 1<=item<=9_999_999:
        return 'pr:'+str(item)
    if key=='component' and type(item) is str and item in components:
        return 'component:'+item
    if key=='repo_path' and _path(item) in paths:
        return 'path:'+item
    raise ValueError()


def _validate(event,source_ref,vocabulary_bytes,expected_vocabulary_sha256,
              path_allowlist_bytes,expected_path_allowlist_sha256,now_utc,previous_event):
    vocab=_document(vocabulary_bytes,expected_vocabulary_sha256,'wd.mind-public-vocabulary.v1',
                    {'schema','components','tags'})
    allowed=_document(path_allowlist_bytes,expected_path_allowlist_sha256,'wd.mind-public-paths.v1',
                      {'schema','paths'})
    for key in ('components','tags'):
        values=vocab[key]
        if (type(values) is not list or len(values)>4096 or
                any(type(x) is not str or not _TOKEN.fullmatch(x) for x in values)
                or len(values)!=len(set(values))): raise ValueError()
    paths=allowed['paths']
    if type(paths) is not list or len(paths)>4096 or any(_path(x)!=x for x in paths) or len(paths)!=len(set(paths)):
        raise ValueError()
    if (type(event) is not dict or set(event)!=_EVENT
            or any(type(event[k]) is not str for k in ('agent','type','task_id','status','to','message'))
            or type(event['paths']) is not list or type(event['write_scope']) is not list
            or event['agent'] not in _LANES
            or event['type']!='status' or event['task_id']!='mind-public-knowledge-v1' or event['status']!='info'
            or event['to']!='' or event['message']!='' or event['paths']!=[] or event['write_scope']!=[]
            or type(event['payload']) is not dict or set(event['payload'])!={'public_knowledge'}): raise ValueError()
    if type(source_ref) is not str or not _HEX64.fullmatch(source_ref):
        raise ValueError()
    data=event['payload']['public_knowledge']
    if (type(data) is not dict or not _REQUIRED<=set(data) or not set(data)<=_REQUIRED|_OPTIONAL
            or data['v']!='1' or data['kind'] not in _KINDS or data['outcome'] not in _OUTCOMES
            or data['next'] not in _NEXT): raise ValueError()
    subject=_subject(data['subject'],set(vocab['components']),set(paths))
    if 'head' in data and (data['kind'] not in _HEAD_KINDS or type(data['head']) is not str
                           or not _HEAD.fullmatch(data['head'])): raise ValueError()
    tags=data['tags']
    if (type(tags) is not list or len(tags)>8 or
            any(type(tag) is not str or tag not in vocab['tags'] for tag in tags)
            or len(tags)!=len(set(tags))): raise ValueError()
    deps=data.get('depends_on',[])
    if type(deps) is not list or len(deps)>4: raise ValueError()
    dependencies=[_subject(item,set(vocab['components']),set(paths)) for item in deps]
    if len(dependencies)!=len(set(dependencies)): raise ValueError()
    created,expires,now=_time(event['ts_utc']),_time(data['valid_until_utc']),_time(now_utc)
    if not created<expires<=created+timedelta(days=7) or now<created: raise ValueError()
    if _event_id(event)!=source_ref: raise ValueError()
    if 'supersedes' in data:
        prior=data['supersedes']
        if (type(prior) is not str or not _HEX64.fullmatch(prior) or type(previous_event) is not dict
                or previous_event.get('agent')!=event['agent']
                or _time(previous_event.get('ts_utc'))>=created): raise ValueError()
        _validate(previous_event,prior,vocabulary_bytes,expected_vocabulary_sha256,
                  path_allowlist_bytes,expected_path_allowlist_sha256,event['ts_utc'],None)
    elif previous_event is not None:
        raise ValueError()
    line=data['kind']+' '+data['outcome']+' '+event['agent']+' '+subject
    if 'head' in data: line+=' head:'+data['head']
    line+=' next:'+data['next']
    if tags: line+=' tags:'+','.join(sorted(tags))
    if dependencies: line+=' deps:'+','.join(sorted(dependencies))
    if len(line.encode('utf-8'))>320: raise ValueError()
    return {'status':'CURRENT' if now<expires else 'STALE','stored_text':line,
            'source_ref_sha256':source_ref,'valid_until_utc':data['valid_until_utc'],
            'visibility':'broadcast','provenance':'SOURCE_HASH_ONLY'}


def validate(*, enabled=False,event=None,source_ref=None,vocabulary_bytes=None,expected_vocabulary_sha256=None,
             path_allowlist_bytes=None,expected_path_allowlist_sha256=None,now_utc=None,previous_event=None):
    if enabled is False:
        return {'status':'OFF'}
    result=None
    try:
        if enabled is not True: raise ValueError()
        result=_validate(event,source_ref,vocabulary_bytes,expected_vocabulary_sha256,
                         path_allowlist_bytes,expected_path_allowlist_sha256,now_utc,previous_event)
    except Exception:
        pass
    finally:
        del event,source_ref,vocabulary_bytes,expected_vocabulary_sha256
        del path_allowlist_bytes,expected_path_allowlist_sha256,now_utc,previous_event
    if result is None:
        raise CardRefused('card_refused')
    return result

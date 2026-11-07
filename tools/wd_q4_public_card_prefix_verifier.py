"""Default-off, synthetic-prefix knowledge-card verification.

Pins and clock are caller-supplied, not authenticated here. A match proves only
that exact envelopes and their backwards links occur in that pinned prefix. It
does not prove publisher identity, later supersession, or complete history.
This module neither publishes data nor modifies a bridge or its policies.
"""
import copy
import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
import datetime as dt
import hashlib
import hmac
import json
import ntpath
import os
import re
import threading
from pathlib import Path

from sandbox.mind.step2 import public_knowledge_card as card
from sandbox.mind.step2 import shared_capture_adapter as capture
from tools.bridge_compact_view import event_id
from waggledance.core import bridge_log_reader as bridge

_HEX=re.compile(r'[0-9a-f]{64}\Z')
_TIMESTAMP=re.compile(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,7})?Z\Z')
_SESSION=re.compile(r'[A-Za-z0-9._:-]{0,128}\Z')
_ROLES={'codex-lead-1':'lead-impl','codex-tools-1':'tools-tests','fable-5':'fable-producer'}
_TASK='mind-public-knowledge-v1'
_MAX_SOURCE=512*1024*1024
_PAGE=16*1024*1024
_MAX_CANDIDATES=4096
_MAX_HASH_WORK=6*1024*1024*1024
_SCAN_MEMO = ContextVar('wd_q4_snapshot_scan_memo', default=None)


class _ScanMemo:
    def __init__(self):
        self.owner = _scan_owner()
        self.state = (True, None)

    @property
    def active(self): return self.state[0]

    @property
    def entry(self): return self.state[1]


def _scan_owner():
    try:
        task = asyncio.current_task()
    except RuntimeError:
        task = None
    return threading.get_ident(), task


@contextmanager
def _snapshot_scan_scope():
    """Revoke and clear the one-entry cache even in copied asynchronous contexts."""
    scope = _ScanMemo()
    previous = _SCAN_MEMO.get()
    token = None
    try:
        token = _SCAN_MEMO.set(scope)
        yield
    finally:
        try:
            scope.state = (False, None)
        finally:
            try:
                # A single interruption immediately before the first assignment
                # must not leave an inherited context holding parsed rows.
                scope.state = (False, None)
            finally:
                if token is not None:
                    _SCAN_MEMO.reset(token)
                elif _SCAN_MEMO.get() is scope:
                    # set() may complete before its token is assigned when a
                    # single interrupt arrives at that bytecode boundary.
                    _SCAN_MEMO.set(previous)


def _unknown():
    return {'status':'UNKNOWN','completeness':'UNKNOWN'}


def _utc(value):
    if type(value) is not str or _TIMESTAMP.fullmatch(value) is None:
        raise ValueError()
    fraction=value[:-1].partition('.')[2]
    # datetime truncates the seventh digit: retain it for 100 ns comparisons.
    return (dt.datetime.fromisoformat(value[:-1]+'+00:00'),int(fraction[6]) if len(fraction)==7 else 0)


def _minute(value):
    return value[0].replace(second=0,microsecond=0).strftime('%Y-%m-%dT%H:%M:00Z')


def _envelope(event):
    """Accept the synthetic prototype or a closed writer envelope, then strip metadata."""
    base=card._EVENT|{'agent_uuid'}
    keys=set(event)
    if keys==base:
        return
    required=base|{'severity','run_id','pid','cwd'}
    optional={'role','session_id','capabilities'}
    if not required<=keys or not keys<=required|optional: raise ValueError()
    cwd=event['cwd']; run=event['run_id']; pid=event['pid']
    if (event['severity'] not in ('','info') or type(cwd) is not str or not 1<=len(cwd)<=1024
            or not ntpath.isabs(cwd) or any(x in cwd for x in ('\x00','\r','\n'))
            or type(run) is not str or _SESSION.fullmatch(run) is None
            or type(pid) is not int or not 1<=pid<=2147483647): raise ValueError()
    if 'role' in event and event['role']!=_ROLES.get(event['agent']): raise ValueError()
    if 'session_id' in event and (type(event['session_id']) is not str
            or not event['session_id'] or _SESSION.fullmatch(event['session_id']) is None): raise ValueError()
    if 'capabilities' in event:
        caps=event['capabilities']
        if (type(caps) is not list or not caps or len(caps)>64
                or any(type(x) is not str or x not in capture._PUBLIC_WRITER_CAPABILITIES for x in caps)
                or len(set(caps))!=len(caps)): raise ValueError()


def _policy(raw, pin, tasks):
    if (type(raw) is not bytes or not 0<len(raw)<=65536 or type(pin) is not str
            or _HEX.fullmatch(pin) is None or not hmac.compare_digest(hashlib.sha256(raw).hexdigest(),pin)):
        raise ValueError()
    obj=json.loads(raw.decode('utf-8'),object_pairs_hook=capture._strict_pairs,
                   parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    if (type(obj) is not dict or set(obj)!={'schema','task_id','writers'}
            or obj['schema']!='wd.mind-public-card-policy.v1' or obj['task_id']!=_TASK or _TASK not in tasks
            or type(obj['writers']) is not list or not obj['writers']
            or any(type(name) is not str or name not in card._LANES for name in obj['writers'])
            or len(set(obj['writers']))!=len(obj['writers'])):
        raise ValueError()
    return frozenset(obj['writers'])


def _scan(source_path,generation_path,end_cursor,expected_prefix_sha256,*,collect_claims=False):
    scope=_SCAN_MEMO.get()
    if scope is not None and scope.owner != _scan_owner(): scope=None
    if (type(source_path) is not str or not os.path.isabs(source_path)
            or type(generation_path) is not str or not os.path.isabs(generation_path)
            or type(end_cursor) is not bridge.BridgeCursor or bridge._validate_cursor(end_cursor)
            or type(end_cursor.generation) is not str or not end_cursor.generation
            or type(end_cursor.offset) is not int or not 0<end_cursor.offset<=_MAX_SOURCE
            or type(expected_prefix_sha256) is not str or _HEX.fullmatch(expected_prefix_sha256) is None):
        if scope is not None and scope.active: scope.state = (True,None)
        raise ValueError()
    key=(source_path,generation_path,end_cursor,expected_prefix_sha256)
    if scope is not None and scope.active:
        memo=scope.entry
        if memo is not None:
            previous_key,previous_full,previous_rows,previous_claims=memo
            if previous_key==key and (previous_full or not collect_claims):
                try:
                    _check_prefix_unchanged(source_path,generation_path,end_cursor,expected_prefix_sha256)
                except BaseException:
                    scope.state=(True,None)
                    raise
                return copy.deepcopy((previous_rows, previous_claims if collect_claims else []))
        scope.state=(True,None)
    cursor=bridge.BridgeCursor(0,end_cursor.file_identity,end_cursor.generation)
    prefix_sha=hashlib.sha256(b'').hexdigest()
    candidates={}; claims=[]; pages=work=0
    while True:
        pages+=1
        work+=2*min(end_cursor.offset,cursor.offset+_PAGE)
        if pages>128 or work>_MAX_HASH_WORK: raise ValueError()
        read=bridge.read_bridge_log(source_path,cursor=cursor,
            max_bytes=min(_PAGE,end_cursor.offset-cursor.offset),max_rows=100000,
            generation_path=generation_path,expected_prefix_sha256=prefix_sha,max_prefix_bytes=_MAX_SOURCE)
        if (not isinstance(read,bridge.BridgePrefixReadResult)
                or read.status not in (bridge.BridgeReadStatus.OK,bridge.BridgeReadStatus.IDLE)
                or read.snapshot_length is None or read.snapshot_length<end_cursor.offset
                or read.candidate_cursor is None or read.candidate_prefix is None):
            raise ValueError()
        for event in read.rows:
            if type(event) is not dict: continue
            payload=event.get('payload')
            claim=collect_claims and type(payload) is dict and 'public_knowledge' in payload
            if event.get('task_id')!=_TASK and not claim: continue
            encoded=json.dumps(event,sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode()
            if len(encoded)>8192: raise ValueError()
            ref=event_id(event)
            if event.get('task_id')==_TASK:
                if len(candidates)>=_MAX_CANDIDATES: raise ValueError()
                candidates[ref]=event
            if claim:
                if len(claims)>=_MAX_CANDIDATES: raise ValueError()
                claims.append((ref,event))
        nxt=read.candidate_cursor
        if nxt.offset==end_cursor.offset:
            if nxt!=end_cursor or not hmac.compare_digest(read.candidate_prefix.sha256,expected_prefix_sha256):
                raise ValueError()
            with bridge._open_log(Path(source_path)) as stream:
                if bridge._file_identity(stream)!=end_cursor.file_identity or os.fstat(stream.fileno()).st_size<end_cursor.offset:
                    raise ValueError()
            if bridge._read_generation(Path(generation_path))!=end_cursor.generation: raise ValueError()
            if scope is not None and scope.active:
                stored_rows,stored_claims=copy.deepcopy((candidates,claims))
                scope.state=(True,(key,collect_claims,stored_rows,stored_claims))
            return candidates,claims
        if nxt.offset<=cursor.offset or nxt.offset>end_cursor.offset: raise ValueError()
        cursor,prefix_sha=nxt,read.candidate_prefix.sha256


def _read(source_path,generation_path,end_cursor,expected_prefix_sha256):
    return _scan(source_path,generation_path,end_cursor,expected_prefix_sha256)[0]


def _read_with_claims(source_path,generation_path,end_cursor,expected_prefix_sha256):
    return _scan(source_path,generation_path,end_cursor,expected_prefix_sha256,collect_claims=True)


def _check_prefix_unchanged(source_path,generation_path,end_cursor,expected_prefix_sha256):
    """Rebind a previously parsed prefix without parsing it a second time."""
    if (type(source_path) is not str or not os.path.isabs(source_path)
            or type(generation_path) is not str or not os.path.isabs(generation_path)
            or type(end_cursor) is not bridge.BridgeCursor or bridge._validate_cursor(end_cursor)
            or type(end_cursor.generation) is not str or not end_cursor.generation
            or type(end_cursor.offset) is not int or not 0<end_cursor.offset<=_MAX_SOURCE
            or type(expected_prefix_sha256) is not str or _HEX.fullmatch(expected_prefix_sha256) is None):
        raise ValueError()
    if bridge._read_generation(Path(generation_path))!=end_cursor.generation: raise ValueError()
    with bridge._open_log(Path(source_path)) as stream:
        if (bridge._file_identity(stream)!=end_cursor.file_identity
                or os.fstat(stream.fileno()).st_size<end_cursor.offset
                or not hmac.compare_digest(bridge._hash_prefix(stream,end_cursor.offset).hexdigest(),expected_prefix_sha256)):
            raise ValueError()
        stream.seek(end_cursor.offset-1)
        if (stream.read(1)!=b'\n' or bridge._file_identity(stream)!=end_cursor.file_identity
                or os.fstat(stream.fileno()).st_size<end_cursor.offset): raise ValueError()
    with bridge._open_log(Path(source_path)) as current:
        if (bridge._file_identity(current)!=end_cursor.file_identity
                or os.fstat(current.fileno()).st_size<end_cursor.offset): raise ValueError()
    if bridge._read_generation(Path(generation_path))!=end_cursor.generation: raise ValueError()


def _check(target,rows,writers,roster,now,vocab,vocab_sha,paths,paths_sha):
    if type(target) is not str or _HEX.fullmatch(target) is None: raise ValueError()
    chain=[]; seen=set(); ref=target
    while True:
        if ref in seen or len(chain)>=16 or ref not in rows: raise ValueError()
        seen.add(ref); event=rows[ref]
        if (type(event) is not dict or event_id(event)!=ref or event.get('agent') not in writers
                or event.get('agent_uuid')!=roster.get(event.get('agent'))): raise ValueError()
        _envelope(event)
        created=_utc(event['ts_utc'])
        if created>now: raise ValueError()
        if chain and (created>=chain[-1][2] or _minute(created)==_minute(chain[-1][2])): raise ValueError()
        payload=event.get('payload')
        if type(payload) is not dict or set(payload)!={'public_knowledge'} or type(payload['public_knowledge']) is not dict:
            raise ValueError()
        chain.append((ref,event,created))
        prev=payload['public_knowledge'].get('supersedes')
        if prev is None: break
        if type(prev) is not str or _HEX.fullmatch(prev) is None: raise ValueError()
        ref=prev
    # Original hashes above are the public refs. Synthetic, normalized hashes
    # exist only inside this validation step and are never returned.
    normalized=[]
    for _,event,created in reversed(chain):
        obj={key:copy.deepcopy(event[key]) for key in card._EVENT}; obj['ts_utc']=_minute(created)
        data=obj['payload']['public_knowledge']
        if normalized: data['supersedes']=event_id(normalized[-1])
        normalized.append(obj)
    target_normal=normalized[-1]
    result=card.validate(enabled=True,event=target_normal,source_ref=event_id(target_normal),
        vocabulary_bytes=vocab,expected_vocabulary_sha256=vocab_sha,path_allowlist_bytes=paths,
        expected_path_allowlist_sha256=paths_sha,now_utc=_minute(now),
        previous_event=normalized[-2] if len(normalized)>1 else None,
        earlier_events=list(reversed(normalized[:-2])))
    if result['status']!='CURRENT': raise ValueError()
    return {'status':'MATCH_WITHIN_PREFIX','canonical_event_ref':target,
            'predecessor_refs':[item[0] for item in chain[1:]],'completeness':'UNKNOWN'}


def verify(*,enabled=False,source_path=None,generation_path=None,end_cursor=None,expected_prefix_sha256=None,
           target_event_ref=None,nonreview_policy_bytes=None,nonreview_policy_sha256=None,
           card_policy_bytes=None,card_policy_sha256=None,roster_bytes=None,roster_sha256=None,
           vocabulary_bytes=None,vocabulary_sha256=None,path_allowlist_bytes=None,path_allowlist_sha256=None,
           now_utc=None):
    if enabled is False: return {'status':'OFF'}
    result=None
    try:
        if enabled is not True: raise ValueError()
        tasks,roster=capture.strict_pins(nonreview_policy_bytes,nonreview_policy_sha256,roster_bytes,roster_sha256)
        writers=_policy(card_policy_bytes,card_policy_sha256,tasks)
        now=_utc(now_utc)
        if type(target_event_ref) is not str or _HEX.fullmatch(target_event_ref) is None: raise ValueError()
        card._document(vocabulary_bytes,vocabulary_sha256,'wd.mind-public-vocabulary.v1',
                       {'schema','components','tags'})
        card._document(path_allowlist_bytes,path_allowlist_sha256,'wd.mind-public-paths.v1',{'schema','paths'})
        rows=_read(source_path,generation_path,end_cursor,expected_prefix_sha256)
        result=_check(target_event_ref,rows,writers,roster,now,vocabulary_bytes,vocabulary_sha256,
                      path_allowlist_bytes,path_allowlist_sha256)
    except Exception:
        pass
    finally:
        source_path=generation_path=target_event_ref=rows=roster=None
        nonreview_policy_bytes=card_policy_bytes=roster_bytes=vocabulary_bytes=path_allowlist_bytes=None
    return result if result is not None else _unknown()

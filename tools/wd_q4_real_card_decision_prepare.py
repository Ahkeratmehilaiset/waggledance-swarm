"""Prepare a caller-pinned real or synthetic card route decision in memory.

This checks a bounded source prefix and local Git ancestry, not source identity,
publisher authority, public origin, later records, or freshness.  The input file
identity and public-policy/anchor pins are not fields in the existing decision
schema; callers must retain and independently bind them at the next boundary.
Card contents are not validated here. No file, pointer, schedule or bridge event
is written, and no decision grants authority to ingest or publish.
"""
import hashlib
import json
import os
from pathlib import Path
import re


_HEX=re.compile(r'[0-9a-f]{64}\Z')


class Refused(ValueError):
    """Input cannot produce a bound decision; no caller data in the message."""


def _sha(raw): return hashlib.sha256(raw).hexdigest()


def _git_bytes(executable, pin):
    if (type(executable) is not str or not os.path.isabs(executable)
            or type(pin) is not str or not _HEX.fullmatch(pin)): raise ValueError()
    path=Path(executable)
    for part in (path,*path.parents):
        info=part.lstat()
        if part.is_symlink() or getattr(info,'st_file_attributes',0)&0x400: raise ValueError()
    before=path.stat()
    if not path.is_file() or not 0<before.st_size<=64*1024*1024: raise ValueError()
    with path.open('rb') as stream:
        raw=stream.read(64*1024*1024+1)
        if not os.path.samestat(before,os.fstat(stream.fileno())): raise ValueError()
    if len(raw)!=before.st_size or _sha(raw)!=pin: raise ValueError()
    return raw


def _vocabulary(cards, raw, pin, paths, paths_pin):
    vocabulary=cards._document(raw,pin,'wd.mind-public-vocabulary.v1',{'schema','components','tags'})
    allowed=cards._document(paths,paths_pin,'wd.mind-public-paths.v1',{'schema','paths'})
    for key in ('components','tags'):
        values=vocabulary[key]
        if (type(values) is not list or len(values)>4096 or len(values)!=len(set(values))
                or any(type(value) is not str or not cards._TOKEN.fullmatch(value) for value in values)):
            raise ValueError()
    values=allowed['paths']
    if (type(values) is not list or len(values)>4096 or len(values)!=len(set(values))
            or any(cards._path(value)!=value for value in values)): raise ValueError()
    return vocabulary


def prepare(*, enabled=False, mode=None, policy_bytes=None, policy_sha256=None,
            roster_bytes=None, roster_sha256=None, card_policy_bytes=None, card_policy_sha256=None,
            public_policy_bytes=None, public_policy_sha256=None, vocabulary_bytes=None, vocabulary_sha256=None,
            path_allowlist_bytes=None, path_allowlist_sha256=None, expected_card_module_sha256=None,
            source_path=None, generation_path=None, end_cursor=None, expected_prefix_sha256=None,
            git_executable=None, git_executable_sha256=None, git_repository=None, anchors_bytes=None,
            anchors_sha256=None):
    if enabled is False: return {'status':'OFF'}
    try:
        if enabled is not True or mode not in ('real','synthetic'): raise ValueError()
        from sandbox.mind.step2 import shared_capture_adapter as capture
        from sandbox.mind.step2 import public_knowledge_card as card
        from sandbox.mind.step2 import shared_card_projection as projection
        from tools import wd_q4_public_card_prefix_verifier as prefix
        from tools import wd_q4_public_git_heads as git
        tasks,roster=capture.strict_pins(policy_bytes,policy_sha256,roster_bytes,roster_sha256)
        public_tasks,public_types=capture.public_status_policy(public_policy_bytes,public_policy_sha256,tasks)
        if 'mind-public-knowledge-v1' not in public_tasks or 'status' not in public_types: raise ValueError()
        prefix._policy(card_policy_bytes,card_policy_sha256,tasks)
        vocab=_vocabulary(card,vocabulary_bytes,vocabulary_sha256,path_allowlist_bytes,path_allowlist_sha256)
        if mode=='synthetic' and 'synthetic' not in vocab['tags']: raise ValueError()
        if (type(expected_card_module_sha256) is not str or not _HEX.fullmatch(expected_card_module_sha256)
                or projection.module_sha256()!=expected_card_module_sha256): raise ValueError()
        anchors=card._document(anchors_bytes,anchors_sha256,'wd.q4-approved-git-anchors.v1',{'schema','anchors'})
        executable_raw=_git_bytes(git_executable,git_executable_sha256)
        prefix._read(source_path,generation_path,end_cursor,expected_prefix_sha256)
        observed=git.observe(enabled=True,git_executable=git_executable,repository=git_repository,anchors=anchors['anchors'])
        if observed.get('status')!='OBSERVED_LOCAL_PINNED_ANCESTRY': raise ValueError()
        value={'schema':projection.DECISION_SCHEMA,
               'decision':projection.REAL_DECISION_NAME if mode=='real' else projection.DECISION_NAME,
               'synthetic':mode=='synthetic','projection_schema':projection.SCHEMA,'scope':projection.SHARED_SCOPE,
               'card_module_sha256':expected_card_module_sha256,'nonreview_policy_sha256':policy_sha256,
               'roster_sha256':roster_sha256,'card_policy_sha256':card_policy_sha256,
               'vocabulary_sha256':vocabulary_sha256,'path_allowlist_sha256':path_allowlist_sha256,
               'source_prefix':{'sha256':expected_prefix_sha256,'offset':end_cursor.offset,'generation':end_cursor.generation},
               'observed_heads':observed['heads']}
        if mode=='real': value['head_observation_sha256']=observed['sha256']
        decision=json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True).encode('ascii')
        pin=_sha(decision)
        if projection.verify_route_decision(decision,pin,policy_sha256=policy_sha256,roster_sha256=roster_sha256)!=value:
            raise ValueError()
        prefix._read(source_path,generation_path,end_cursor,expected_prefix_sha256)
        if (_git_bytes(git_executable,git_executable_sha256)!=executable_raw
                or projection.module_sha256()!=expected_card_module_sha256
                or git.observe(enabled=True,git_executable=git_executable,repository=git_repository,
                               anchors=anchors['anchors'])!=observed
                or _git_bytes(git_executable,git_executable_sha256)!=executable_raw): raise ValueError()
        return {'status':'PREPARED','decision_bytes':decision,'decision_sha256':pin,'mode':mode,
                'public_policy_sha256':public_policy_sha256,'anchors_sha256':anchors_sha256,
                'source_authentication':'UNKNOWN','publisher_authentication':'UNKNOWN','freshness':'UNKNOWN',
                'completeness':'UNKNOWN','card_validation':'NOT_RUN','file_identity_binding':'CALLER_MUST_RETAIN'}
    except Exception:
        raise Refused('card_decision_prepare_refused') from None

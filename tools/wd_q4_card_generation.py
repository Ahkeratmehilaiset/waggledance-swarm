"""Prepare one anchored card generation; never update a pointer.

All pins are caller supplied. A matching prefix is not authenticated as the
live bridge, and coverage/freshness beyond that prefix remain unknown. The
separate real-decision path requires caller-approved Git anchors; neither that
assertion nor roster consistency authenticates the source or publisher. Output
is staged only; this does not implement publication, scheduling or installation.
"""
import hashlib
import json
from pathlib import Path

from sandbox.mind.step2 import shared_card_projection as cards
from tools import wd_q4_public_card_prefix_verifier as prefix
from tools import wd_q4_live_snapshot_publish as live
from tools import wd_q4_public_git_heads as public_heads


class CardGenerationRefused(ValueError):
    """No generation build was attempted."""


class CardGenerationUnknown(RuntimeError):
    """A build may have written a partial generation; do not retry blindly."""


class CardGenerationInterrupted(BaseException):
    """An interrupted build has an unknown outcome and must remain stopped."""


def _claimed_cards(source_path,generation_path,end_cursor,expected_sha256):
    """Select card claims from the exact bounded prefix; never return other rows."""
    bridge=prefix.bridge
    # Reuse the prefix verifier's full cursor, generation, identity and budget
    # preflight. Its task filter alone cannot detect a card claim on a wrong task.
    prefix._read(source_path,generation_path,end_cursor,expected_sha256)
    cursor=bridge.BridgeCursor(0,end_cursor.file_identity,end_cursor.generation)
    digest=hashlib.sha256(b'').hexdigest(); pages=work=0; selected=[]
    while True:
        pages+=1; work+=2*min(end_cursor.offset,cursor.offset+prefix._PAGE)
        if pages>128 or work>prefix._MAX_HASH_WORK: raise ValueError()
        result=bridge.read_bridge_log(source_path,cursor=cursor,
            max_bytes=min(prefix._PAGE,end_cursor.offset-cursor.offset),max_rows=100000,
            generation_path=generation_path,expected_prefix_sha256=digest,max_prefix_bytes=prefix._MAX_SOURCE)
        if (not isinstance(result,bridge.BridgePrefixReadResult) or
                result.status not in (bridge.BridgeReadStatus.OK,bridge.BridgeReadStatus.IDLE) or
                result.snapshot_length is None or result.snapshot_length<end_cursor.offset or
                result.candidate_cursor is None or result.candidate_prefix is None):
            raise ValueError()
        for row in result.rows:
            if type(row) is not dict: continue
            payload=row.get('payload')
            if type(payload) is not dict or 'public_knowledge' not in payload: continue
            if len(selected)>=prefix._MAX_CANDIDATES: raise ValueError()
            raw=json.dumps(row,sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode('utf-8')
            if len(raw)>8192: raise ValueError()
            selected.append((prefix.event_id(row),row))
        next_cursor=result.candidate_cursor
        if next_cursor.offset==end_cursor.offset:
            if next_cursor!=end_cursor or result.candidate_prefix.sha256!=expected_sha256: raise ValueError()
            return selected
        if next_cursor.offset<=cursor.offset or next_cursor.offset>end_cursor.offset: raise ValueError()
        cursor,digest=next_cursor,result.candidate_prefix.sha256


def prepare_card_generation(*, enabled=False, decision_bytes=None, decision_sha256=None,
        vocabulary_bytes=None, vocabulary_sha256=None, path_allowlist_bytes=None, path_allowlist_sha256=None,
        now_utc=None, policy_bytes=None, policy_sha256=None, roster_bytes=None, roster_sha256=None,
        card_policy_bytes=None, source_path=None, generation_path=None, end_cursor=None,
        output_root=None, generation_name=None, encode=None, identity=None, dimension=None,
        real_git_executable=None, real_git_repository=None, real_approved_anchors=None):
    if enabled is False:
        return {'status': 'OFF'}
    try:
        if enabled is not True: raise ValueError()
        decision=cards.verify_route_decision(decision_bytes,decision_sha256,
                                             policy_sha256=policy_sha256,roster_sha256=roster_sha256)
        real=decision['synthetic'] is False
        if real:
            observation=public_heads.observe(enabled=True,git_executable=real_git_executable,
                    repository=real_git_repository,anchors=real_approved_anchors)
            if (observation['sha256']!=decision['head_observation_sha256'] or
                    observation['heads']!=decision['observed_heads']): raise ValueError()
        elif any(value is not None for value in (real_git_executable,real_git_repository,real_approved_anchors)):
            raise ValueError()
        anchor=decision['source_prefix']
        if (type(getattr(end_cursor,'offset',None)) is not int or end_cursor.offset!=anchor['offset'] or
                getattr(end_cursor,'generation',None)!=anchor['generation']):
            raise ValueError()
        claims=_claimed_cards(source_path,generation_path,end_cursor,anchor['sha256'])
        if not claims: raise ValueError()
        for ref,event in claims:
            # Every claim, including duplicates or wrong-task claims, must pass
            # the same exact prefix, closed envelope, writer and roster checks.
            moment=event['ts_utc'][:17]+'59.9999999Z'
            checked=prefix.verify(enabled=True,source_path=source_path,generation_path=generation_path,
                end_cursor=end_cursor,expected_prefix_sha256=anchor['sha256'],target_event_ref=ref,
                nonreview_policy_bytes=policy_bytes,nonreview_policy_sha256=policy_sha256,
                card_policy_bytes=card_policy_bytes,card_policy_sha256=decision['card_policy_sha256'],
                roster_bytes=roster_bytes,roster_sha256=roster_sha256,vocabulary_bytes=vocabulary_bytes,
                vocabulary_sha256=vocabulary_sha256,path_allowlist_bytes=path_allowlist_bytes,
                path_allowlist_sha256=path_allowlist_sha256,now_utc=moment)
            if checked.get('status')!='MATCH_WITHIN_PREFIX': raise ValueError()
            head=event['payload']['public_knowledge'].get('head')
            if head is not None and (decision['observed_heads'] is None or head not in decision['observed_heads']):
                # Until the projection stores a masked head without breaking
                # source identity, never write an unverified raw head.
                raise ValueError()
        projection=cards.project_shared_cards([row for _,row in claims],vocabulary_bytes=vocabulary_bytes,
            vocabulary_sha256=vocabulary_sha256,path_allowlist_bytes=path_allowlist_bytes,
            path_allowlist_sha256=path_allowlist_sha256,now_utc=now_utc,enabled=True,
            _real_decision=decision if real else None)
        if projection['refused'].get('card_refused',0): raise ValueError()
        # Reuse the existing verifier before entering the output phase; the
        # builder repeats these checks for the precise projection it writes.
        cards.verify_shared_card_sources(projection,enabled=True,source_path=source_path,
            generation_path=generation_path,end_cursor=end_cursor,expected_prefix_sha256=anchor['sha256'],
            nonreview_policy_bytes=policy_bytes,nonreview_policy_sha256=policy_sha256,
            card_policy_bytes=card_policy_bytes,card_policy_sha256=decision['card_policy_sha256'],
            roster_bytes=roster_bytes,roster_sha256=roster_sha256,_real_decision=decision if real else None)
    except Exception:
        raise CardGenerationRefused('card_generation_refused') from None
    try:
        built=cards.build_shared_route_card_memory(projection,encode,identity,dimension,output_root,
            generation_name,enabled=True,policy_bytes=policy_bytes,policy_sha256=policy_sha256,
            roster_bytes=roster_bytes,roster_sha256=roster_sha256,decision_bytes=decision_bytes,
            decision_sha256=decision_sha256,card_policy_bytes=card_policy_bytes,source_path=source_path,
            generation_path=generation_path,end_cursor=end_cursor,real_git_executable=real_git_executable,
            real_git_repository=real_git_repository,real_approved_anchors=real_approved_anchors)
        directory=Path(output_root)/generation_name
        live._read(directory/'memory.json',16*1024*1024,built['memory_sha256'])
        live._read(directory/'descriptor.json',65536,built['descriptor_sha256'])
        return {'status':'STAGED','memory_sha256':built['memory_sha256'],
                'descriptor_sha256':built['descriptor_sha256'],'records':built['records'],
                'completeness':'UNKNOWN'}
    except (KeyboardInterrupt, SystemExit, GeneratorExit):
        raise CardGenerationInterrupted('card_generation_unknown') from None
    except Exception:
        raise CardGenerationUnknown('card_generation_unknown') from None

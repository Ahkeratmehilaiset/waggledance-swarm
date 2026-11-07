"""Prepare one anchored card generation; never update a pointer.

All pins are caller supplied. A matching prefix is not authenticated as the
live bridge, and coverage/freshness beyond that prefix remain unknown. The
separate real-decision path requires caller-approved Git anchors; neither that
assertion nor roster consistency authenticates the source or publisher. Output
is staged only; this does not implement publication, scheduling or installation.
"""
import copy
import json
import re
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
    return prefix._read_with_claims(source_path,generation_path,end_cursor,expected_sha256)[1]


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
        rows,claims=prefix._read_with_claims(source_path,generation_path,end_cursor,anchor['sha256'])
        if not claims: raise ValueError()
        tasks,roster=prefix.capture.strict_pins(policy_bytes,policy_sha256,roster_bytes,roster_sha256)
        writers=prefix._policy(card_policy_bytes,decision['card_policy_sha256'],tasks)
        prefix.card._document(vocabulary_bytes,vocabulary_sha256,'wd.mind-public-vocabulary.v1',{'schema','components','tags'})
        prefix.card._document(path_allowlist_bytes,path_allowlist_sha256,'wd.mind-public-paths.v1',{'schema','paths'})
        accepted=[]; rejected_invalid_expiry=0
        for ref,event in claims:
            # A locale-formatted expiry can be quarantined only after checking
            # every other field against the same policy, roster and prefix.
            # The corrected copy is validation-only and is never indexed.
            payload=event.get('payload')
            card=payload.get('public_knowledge') if type(payload) is dict else None
            expiry=card.get('valid_until_utc') if type(card) is dict else None
            if type(expiry) is str and re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}\.\d{2}\.00Z',expiry):
                if rows.get(ref)!=event: raise ValueError()
                corrected_rows=dict(rows)
                corrected=copy.deepcopy(event)
                corrected['payload']['public_knowledge']['valid_until_utc']=expiry[:13]+expiry[13:].replace('.',':')
                corrected_ref=prefix.event_id(corrected)
                corrected_rows[corrected_ref]=corrected
                moment=event['ts_utc'][:17]+'59.9999999Z'
                prefix._check(corrected_ref,corrected_rows,writers,roster,prefix._utc(moment),vocabulary_bytes,
                    vocabulary_sha256,path_allowlist_bytes,path_allowlist_sha256)
                head=card.get('head')
                if head is not None and (decision['observed_heads'] is None or head not in decision['observed_heads']):
                    raise ValueError()
                rejected_invalid_expiry+=1
                continue
            # Every claim, including duplicates or wrong-task claims, must pass
            # the same exact prefix, closed envelope, writer and roster checks.
            moment=event['ts_utc'][:17]+'59.9999999Z'
            checked=prefix._check(ref,rows,writers,roster,prefix._utc(moment),vocabulary_bytes,
                vocabulary_sha256,path_allowlist_bytes,path_allowlist_sha256)
            if checked.get('status')!='MATCH_WITHIN_PREFIX': raise ValueError()
            head=event['payload']['public_knowledge'].get('head')
            if head is not None and (decision['observed_heads'] is None or head not in decision['observed_heads']):
                # Until the projection stores a masked head without breaking
                # source identity, never write an unverified raw head.
                raise ValueError()
            accepted.append(event)
        prefix._check_prefix_unchanged(source_path,generation_path,end_cursor,anchor['sha256'])
        if not accepted: raise ValueError()
        projection=cards.project_shared_cards(accepted,vocabulary_bytes=vocabulary_bytes,
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
                'completeness':'UNKNOWN','rejected_invalid_expiry':rejected_invalid_expiry}
    except (KeyboardInterrupt, SystemExit, GeneratorExit):
        raise CardGenerationInterrupted('card_generation_unknown') from None
    except Exception:
        raise CardGenerationUnknown('card_generation_unknown') from None

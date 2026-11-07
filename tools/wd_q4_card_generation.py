"""Prepare one synthetic, anchored card generation; never update a pointer.

All pins are caller supplied. A matching prefix is not authenticated as the
live bridge, and coverage/freshness beyond that prefix remain unknown. This
does not implement publication, scheduling, installation or recovery.
"""
from pathlib import Path

from sandbox.mind.step2 import shared_card_projection as cards
from tools import wd_q4_public_card_prefix_verifier as prefix
from tools import wd_q4_live_snapshot_publish as live


class CardGenerationRefused(ValueError):
    """No generation build was attempted."""


class CardGenerationUnknown(RuntimeError):
    """A build may have written a partial generation; do not retry blindly."""


class CardGenerationInterrupted(BaseException):
    """An interrupted build has an unknown outcome and must remain stopped."""


def prepare_card_generation(*, enabled=False, decision_bytes=None, decision_sha256=None,
        vocabulary_bytes=None, vocabulary_sha256=None, path_allowlist_bytes=None, path_allowlist_sha256=None,
        now_utc=None, policy_bytes=None, policy_sha256=None, roster_bytes=None, roster_sha256=None,
        card_policy_bytes=None, source_path=None, generation_path=None, end_cursor=None,
        output_root=None, generation_name=None, encode=None, identity=None, dimension=None):
    if enabled is False:
        return {'status': 'OFF'}
    try:
        if enabled is not True: raise ValueError()
        decision=cards.verify_route_decision(decision_bytes,decision_sha256,
                                             policy_sha256=policy_sha256,roster_sha256=roster_sha256)
        anchor=decision['source_prefix']
        if (type(getattr(end_cursor,'offset',None)) is not int or end_cursor.offset!=anchor['offset'] or
                getattr(end_cursor,'generation',None)!=anchor['generation']):
            raise ValueError()
        rows=prefix._read(source_path,generation_path,end_cursor,anchor['sha256'])
        projection=cards.project_shared_cards(list(rows.values()),vocabulary_bytes=vocabulary_bytes,
            vocabulary_sha256=vocabulary_sha256,path_allowlist_bytes=path_allowlist_bytes,
            path_allowlist_sha256=path_allowlist_sha256,now_utc=now_utc,enabled=True)
        # Reuse the existing verifier before entering the output phase; the
        # builder repeats these checks for the precise projection it writes.
        cards.verify_shared_card_sources(projection,enabled=True,source_path=source_path,
            generation_path=generation_path,end_cursor=end_cursor,expected_prefix_sha256=anchor['sha256'],
            nonreview_policy_bytes=policy_bytes,nonreview_policy_sha256=policy_sha256,
            card_policy_bytes=card_policy_bytes,card_policy_sha256=decision['card_policy_sha256'],
            roster_bytes=roster_bytes,roster_sha256=roster_sha256)
    except Exception:
        raise CardGenerationRefused('card_generation_refused') from None
    try:
        built=cards.build_shared_route_card_memory(projection,encode,identity,dimension,output_root,
            generation_name,enabled=True,policy_bytes=policy_bytes,policy_sha256=policy_sha256,
            roster_bytes=roster_bytes,roster_sha256=roster_sha256,decision_bytes=decision_bytes,
            decision_sha256=decision_sha256,card_policy_bytes=card_policy_bytes,source_path=source_path,
            generation_path=generation_path,end_cursor=end_cursor)
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

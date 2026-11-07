"""Default-off, private, bounded provenance diagnostic for a pinned projection.

The caller must independently anchor the projection hash, policy and roster
pins, and source path/generation/cursor/prefix tuple to the intended snapshot.
This module cannot authenticate those inputs, establish their mutual origin,
prove completeness or freshness, or authenticate a same-user caller. It is not
a production activation, a review or a gate. No raw records, source paths,
canonical IDs, queries or answers are returned.
"""
import hashlib
import hmac
import json
import re

from sandbox.mind.step2 import shared_capture_adapter as capture
from sandbox.mind.step2 import shared_projection as projection
from tools import wd_q4_canonical_ref_verifier as verifier

_HEX = re.compile(r'[0-9a-f]{64}\Z')
_MAX_PROJECTION = 16 * 1024 * 1024


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def diagnose(*, enabled=False, projection_bytes=None, expected_projection_sha256=None,
             ref_sha256=None, doc_id_sha256=None, policy_bytes=None, policy_sha256=None,
             public_policy_bytes=None, public_policy_sha256=None, roster_bytes=None, roster_sha256=None,
             source_path=None, generation_path=None, end_cursor=None, expected_prefix_sha256=None):
    """Check a unique projected ref against one caller-anchored source prefix.

    The sole positive result means only one match was found within that prefix.
    An absent, ambiguous, mismatched or unavailable input is UNKNOWN.
    This diagnostic does not measure freshness, establish absence or
    authenticate any trust anchor.
    """
    if enabled is False:
        return {'status': 'OFF'}
    try:
        if (enabled is not True or type(projection_bytes) is not bytes
                or not 0 < len(projection_bytes) <= _MAX_PROJECTION
                or any(type(pin) is not str or _HEX.fullmatch(pin) is None for pin in
                       (expected_projection_sha256, ref_sha256, doc_id_sha256, expected_prefix_sha256))
                or not hmac.compare_digest(_sha(projection_bytes), expected_projection_sha256)):
            raise ValueError()
        tasks, roster = capture.strict_pins(policy_bytes, policy_sha256, roster_bytes, roster_sha256)
        public_tasks, public_types = capture.public_status_policy(public_policy_bytes, public_policy_sha256, tasks)
        doc = json.loads(projection_bytes.decode('utf-8', 'strict'), object_pairs_hook=capture._strict_pairs,
                         parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        if json.dumps(doc, sort_keys=True, indent=1).encode('utf-8') != projection_bytes:
            raise ValueError()
        projection.validate_projection(doc, approved_tasks=tasks, include_refs=False, roster=roster,
                                       exclude_review_signal=True)
        selected = []
        for record in doc['records']:
            ref = record['provenance']['event_ref']
            if (hmac.compare_digest(_sha(ref.encode('utf-8')), ref_sha256)
                    and hmac.compare_digest(_sha(record['record_id'].encode('utf-8')), doc_id_sha256)):
                selected.append(record)
        if len(selected) != 1:
            raise ValueError()
        record = selected[0]
        if record['meta']['task_id'] not in public_tasks or record['meta']['type'] not in public_types:
            raise ValueError()
        result = verifier.verify_within_prefix(enabled=True, record=record, policy_bytes=policy_bytes,
            policy_sha256=policy_sha256, public_policy_bytes=public_policy_bytes,
            public_policy_sha256=public_policy_sha256, roster_bytes=roster_bytes, roster_sha256=roster_sha256,
            source_path=source_path, generation_path=generation_path, end_cursor=end_cursor,
            expected_prefix_sha256=expected_prefix_sha256)
        if (type(result) is dict and set(result) == {'status', 'canonical_event_refs', 'count', 'completeness'}
                and result['status'] == 'MATCH_WITHIN_PREFIX' and result['completeness'] == 'UNKNOWN'
                and type(result['count']) is int and result['count'] == 1
                and type(result['canonical_event_refs']) is list and len(result['canonical_event_refs']) == 1
                and type(result['canonical_event_refs'][0]) is str
                and _HEX.fullmatch(result['canonical_event_refs'][0]) is not None):
            return {'status': 'MATCH_WITHIN_PREFIX'}
    except Exception:
        pass
    return {'status': 'UNKNOWN'}

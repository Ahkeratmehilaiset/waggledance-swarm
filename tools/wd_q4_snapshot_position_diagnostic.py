"""Default-off observation of a pinned query snapshot's source-log position.

The bundle digest and expected pointer digest must be independently anchored to
what the reader actually used. Source/generation paths and all policy pins are
caller trust inputs. The observation proves neither source authentication nor
wall-clock freshness, coverage, a lease, or resistance to same-user ABA races.
It binds the bundle's active artifact bytes to its pointer but does not prove
that a reader accepted them. It is not connected to the production reader or
launcher. It returns no source
paths, raw records, cursor, digest, query, answer or precise byte count.
"""
import hashlib
import hmac
import re

from sandbox.mind.step2 import shared_capture_adapter as capture
from sandbox.mind.step2 import shared_index_checkpoint as checkpoint
from tools import wd_q4_secure_transport as transport
from tools import wd_q4_snapshot_lag as lag
from waggledance.core.bridge_log_reader import BridgeCursor, BridgePrefixPin

_HEX = re.compile(r'[0-9a-f]{64}\Z')
_LAG_BUCKETS = frozenset((0, 4096, 65536))


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def observe(*, enabled=False, checkpoint_root=None, expected_pointer_sha256=None,
            bundle_bytes=None, expected_bundle_sha256=None, policy_sha256=None,
            roster_sha256=None, public_policy_sha256=None, log_path=None,
            generation_path=None, max_prefix_bytes=None, max_lag_bytes=0):
    """Bound the observed source position of exactly one independently pinned bundle.

    The caller must protect and authenticate the tuple and bind it to the
    consumer independently. checkpoint_root is retained for compatibility and
    is not read. Only fixed lag buckets and one fixed prefix budget are allowed;
    they disclose at most coarse position thresholds, not an exact byte count.
    This point-in-time observation is not freshness, completeness, producer
    authenticity, or a guarantee against later writes.
    """
    if enabled is False:
        return {'status': 'OFF'}
    try:
        pins=(expected_pointer_sha256,expected_bundle_sha256,policy_sha256,roster_sha256,public_policy_sha256)
        if (enabled is not True or any(type(pin) is not str or _HEX.fullmatch(pin) is None for pin in pins)
                or type(max_lag_bytes) is not int or max_lag_bytes not in _LAG_BUCKETS
                or (max_prefix_bytes is not None and
                    (type(max_prefix_bytes) is not int or max_prefix_bytes != lag.MAX_PREFIX_BYTES))
                or type(bundle_bytes) is not bytes or not 0 < len(bundle_bytes) <= transport._MAX_BUNDLE
                or not hmac.compare_digest(_sha(bundle_bytes),expected_bundle_sha256)):
            raise ValueError()
        parts=transport._fixed_bundle_decode(bundle_bytes)
        if (not hmac.compare_digest(_sha(parts['pointer_bytes']),expected_pointer_sha256)
                or not hmac.compare_digest(_sha(parts['policy_bytes']),policy_sha256)
                or not hmac.compare_digest(_sha(parts['roster_bytes']),roster_sha256)
                or not hmac.compare_digest(_sha(parts['public_policy_bytes']),public_policy_sha256)):
            raise ValueError()
        tasks, roster = capture.strict_pins(parts['policy_bytes'],policy_sha256,parts['roster_bytes'],roster_sha256)
        capture.public_status_policy(parts['public_policy_bytes'],public_policy_sha256,tasks)
        pointer=checkpoint._parse_pointer_snapshot(parts['pointer_bytes'],
            expected_pointer_sha256=expected_pointer_sha256,policy_sha=policy_sha256,
            roster_sha=roster_sha256,prefix_required=True)
        checkpoint._bind_active_artifact_bytes(parts['active_artifact_bytes'],
            expected_hashes=pointer['artifacts'],enabled=True)
        checkpoint._validate_pointer_history(None,parts['pointer_bytes'],pointer,tasks,roster,
            policy_sha256,roster_sha256,policy_bytes=parts['policy_bytes'],roster_bytes=parts['roster_bytes'],
            expected_source_mode=checkpoint._requested_source_mode('public-v1',public_policy_sha256),
            prefix_required=True,snapshot_bytes=parts['history_bytes'])
        watermark=BridgePrefixPin(BridgeCursor(**pointer['watermark']),pointer['prefix_watermark_sha256'])
        result=lag.check_snapshot_position(enabled=True,log_path=log_path,generation_path=generation_path,
            watermark=watermark,max_prefix_bytes=lag.MAX_PREFIX_BYTES,max_lag_bytes=max_lag_bytes)
        if (type(result) is not dict or set(result) != {'status','lag_bytes','source_authentication','freshness'}
                or result['status'] != 'BOUNDED_BY_LOG_POSITION' or result['source_authentication'] != 'UNKNOWN'
                or result['freshness'] != 'UNKNOWN' or type(result['lag_bytes']) is not int
                or type(max_lag_bytes) is not int or not 0 <= result['lag_bytes'] <= max_lag_bytes):
            raise ValueError()
        return {'status':'BOUNDED_BY_LOG_POSITION','freshness':'UNKNOWN',
                'coverage':'UNKNOWN','source_authentication':'UNKNOWN'}
    except Exception:
        return {'status':'UNKNOWN'}

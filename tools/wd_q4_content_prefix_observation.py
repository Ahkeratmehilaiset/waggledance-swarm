"""Default-off, non-authenticating observation of a content-route prefix.

This composes the existing content re-derivation and bounded position checks.
All pins, source paths, policies and generation identity remain caller trust
inputs. A successful observation establishes neither publisher authentication
nor time freshness, completeness, a lease, or resistance to same-user ABA.
It returns no records, input paths, cursors, digests or precise byte counts.
No caller or launcher uses this building block by default.
"""
import hashlib
import hmac

from sandbox.mind.step2 import content_memory as content
from tools import wd_q4_snapshot_lag as lag
from waggledance.core.bridge_log_reader import BridgeCursor, BridgePrefixPin

_LAG_BUCKETS = frozenset((0, 4096, 65536))
_POSITION_KEYS = frozenset(("status", "lag_bytes", "source_authentication", "freshness"))
_DERIVATION_KEYS = frozenset(
    ("status", "projection_sha256", "decision_sha256", "source_prefix", "doc_commit")
)


def _bounded_position(value, limit):
    return (
        type(value) is dict
        and set(value) == _POSITION_KEYS
        and value["status"] == "BOUNDED_BY_LOG_POSITION"
        and value["source_authentication"] == "UNKNOWN"
        and value["freshness"] == "UNKNOWN"
        and type(value["lag_bytes"]) is int
        and 0 <= value["lag_bytes"] <= limit
    )


def observe(
    *, enabled=False, projection_bytes=None, projection_sha256=None,
    messages=None, documents=None, decision_bytes=None, decision_sha256=None,
    policy_bytes=None, policy_sha256=None, roster_bytes=None, roster_sha256=None,
    trusted_git=None, max_prefix_bytes=None, max_lag_bytes=0,
):
    """Return OFF, UNKNOWN, or a closed independent derivation/position result.

    Detected changes across the observation window refuse. Equal observations
    do not exclude coordinated same-user rewrites between checks. Authentication,
    freshness and same-user ABA therefore remain UNKNOWN on success as well.
    """
    if enabled is False:
        return {"status": "OFF"}
    try:
        if (
            enabled is not True
            or type(projection_bytes) is not bytes
            or not 0 < len(projection_bytes) <= content.MAX_PROJECTION_BYTES
            or type(projection_sha256) is not str
            or content.HEX64.fullmatch(projection_sha256) is None
            or not hmac.compare_digest(
                hashlib.sha256(projection_bytes).hexdigest(), projection_sha256
            )
            or type(messages) is not dict
            or set(messages) != content.ROUTE_MESSAGE_KEYS
            or type(max_prefix_bytes) is not int
            or not 0 < max_prefix_bytes <= min(content.MAX_PREFIX_BYTES, lag.MAX_PREFIX_BYTES)
            or type(max_lag_bytes) is not int
            or max_lag_bytes not in _LAG_BUCKETS
        ):
            raise ValueError()
        messages = dict(messages)
        decision = content.verify_content_route_decision(
            decision_bytes, decision_sha256,
            policy_sha256=policy_sha256, roster_sha256=roster_sha256,
        )
        prefix, cursor = decision["source_prefix"], messages["end_cursor"]
        if (
            type(prefix) is not dict
            or type(cursor) is not BridgeCursor
            or type(cursor.offset) is not int
            or not 0 < cursor.offset <= max_prefix_bytes
            or cursor.offset != prefix["offset"]
            or cursor.generation != prefix["generation"]
        ):
            raise ValueError()
        position_args = dict(
            enabled=True, log_path=messages["source_path"],
            generation_path=messages["generation_path"],
            watermark=BridgePrefixPin(cursor, prefix["sha256"]),
            max_prefix_bytes=max_prefix_bytes, max_lag_bytes=max_lag_bytes,
        )
        # The existing position guard checks every path ancestor for reparse
        # before the content validator can open any message source.
        before = lag.check_snapshot_position(**position_args)
        if not _bounded_position(before, max_lag_bytes):
            raise ValueError()
        checked = content.validate_content_snapshot(
            enabled=True, projection_bytes=projection_bytes,
            projection_sha256=projection_sha256, messages=messages, documents=documents,
            decision_bytes=decision_bytes, decision_sha256=decision_sha256,
            policy_bytes=policy_bytes, policy_sha256=policy_sha256,
            roster_bytes=roster_bytes, roster_sha256=roster_sha256,
            trusted_git=trusted_git,
        )
        if (
            type(checked) is not dict
            or set(checked) != _DERIVATION_KEYS
            or checked["status"] != "MATCH"
            or checked["projection_sha256"] != projection_sha256
            or checked["decision_sha256"] != decision_sha256
            or checked["source_prefix"] != prefix
        ):
            raise ValueError()
        after = lag.check_snapshot_position(**position_args)
        if not _bounded_position(after, max_lag_bytes) or after != before:
            raise ValueError()
        return {
            "status": "OBSERVED",
            "derivation": "MATCH_WITHIN_PREFIX",
            "position": "BOUNDED_BY_LOG_POSITION",
            "publisher_authentication": "UNKNOWN",
            "source_authentication": "UNKNOWN",
            "freshness": "UNKNOWN",
            "same_user_aba": "UNKNOWN",
        }
    except Exception:
        return {"status": "UNKNOWN"}

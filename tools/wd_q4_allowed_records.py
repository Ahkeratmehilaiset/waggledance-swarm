"""Default-off pinned projection -> visible record map for answer validation.

Caller authenticates all eight binding pins, the projection/policy/roster
origins, the clock and the acceptable query age. This code only compares bytes
against three pins and checks time relative to the supplied clock; it cannot
establish wall-clock truth, snapshot freshness, source publicity, publisher or
lane authentication. Neither the non-review policy nor map membership alone
proves that a record is public. No live source, file, query or network is read.
"""

from datetime import timedelta

from sandbox.mind.step2 import shared_capture_adapter as adapter
from sandbox.mind.step2 import shared_projection as projection
from tools import wd_q4_lane_query as lane_query

_MAX_RECORDS = 4096  # matches the current shared index builder limit
_MAX_AGE = 2 ** 31 - 1  # representability bound, not a freshness policy


def build_allowed_records(*, projection_bytes, policy_bytes, roster_bytes, expected_binding,
                          query_utc, current_utc, max_age_seconds, enabled=False):
    """Build the existing (ref hash, document hash) -> visible projection record map."""
    try:
        if enabled is not True:
            raise ValueError()
        if (type(expected_binding) is not dict or set(expected_binding) != lane_query.BINDING_KEYS
                or any(not lane_query._hex(expected_binding[key], lane_query._HEX64)
                       for key in lane_query.BINDING_KEYS)
                or not lane_query._bounded(projection_bytes, lane_query.MAX_PROJECTION_BYTES)
                or not lane_query._bounded(policy_bytes, lane_query.MAX_POLICY_BYTES)
                or not lane_query._bounded(roster_bytes, lane_query.MAX_ROSTER_BYTES)
                or type(max_age_seconds) is not int or not 0 <= max_age_seconds <= _MAX_AGE):
            raise ValueError()
        lane_query._canonical_utc(query_utc)
        lane_query._canonical_utc(current_utc)
        query_time = projection.corpus_mod.parse_utc(query_utc)
        current_time = projection.corpus_mod.parse_utc(current_utc)
        age = current_time - query_time
        if age < timedelta(0) or age > timedelta(seconds=max_age_seconds):
            raise ValueError()
        tasks, roster = adapter.strict_pins(policy_bytes, expected_binding["policy_sha256"],
                                            roster_bytes, expected_binding["roster_sha256"])
        records = lane_query._records(projection_bytes, expected_binding["projection_sha256"], tasks, roster)
        if len(records) > _MAX_RECORDS:
            raise ValueError()
        return {key: record for key, record in records.items()
                if projection.visible_at(record, query_utc, guard_band_s=lane_query.VISIBILITY_GUARD_SECONDS)}
    except Exception:
        pass
    # This function's traceback retains no caller inputs; caller frames and
    # memory erasure remain outside this boundary.
    projection_bytes = policy_bytes = roster_bytes = expected_binding = None
    query_utc = current_utc = max_age_seconds = enabled = None
    query_time = current_time = age = tasks = roster = records = None
    raise ValueError("allowed_records_refused") from None

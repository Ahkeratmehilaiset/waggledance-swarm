"""Pure bounded merge of strictly projected metadata; not a writer or trust anchor.

The caller must authenticate both inputs, roster, policy and their source before
use. Projection validation cannot re-establish the original event's UUID,
recipient, public classification, completeness or freshness. This module
does not advance or atomically bind any source cursor or publish an index.
"""
import copy

from sandbox.mind.step2 import shared_capture_adapter as adapter
from sandbox.mind.step2 import shared_projection as projection

MAX_RECORDS = 4096
_RCO = frozenset({"claude-rco-1", "claude-rco-2"})


class HistoryRefused(ValueError):
    """Fixed refusal without metadata or source paths."""


def merge_projections(previous, delta, *, policy_bytes, policy_sha256, roster_bytes, roster_sha256):
    """Revalidate, merge by id and sort; refuse overflow instead of evicting.

    The output is a fresh metadata projection. Its dropped/refused counters are
    intentionally zero: original ingestion counts are not retained or summed.
    Neither supplied hashes nor recomputed record IDs authenticate origin.
    """
    try:
        tasks, roster = adapter.strict_pins(policy_bytes, policy_sha256, roster_bytes, roster_sha256)
        by_id = {}
        for value in (previous, delta):
            if type(value) is not dict or type(value.get("records")) is not list or len(value["records"]) > MAX_RECORDS:
                raise ValueError()
            projection.validate_projection(value, approved_tasks=tasks, include_refs=False,
                                           roster=roster, exclude_review_signal=True)
            for record in value["records"]:
                meta = record["meta"]
                if (meta["task_id"] == projection.UNKNOWN or meta["task_id"] not in tasks or meta["agent"] in _RCO
                        or record["provenance"]["canonical_event"] is not False):
                    raise ValueError()
                record_id = record["record_id"]
                if record_id in by_id and by_id[record_id] != record:
                    raise ValueError()
                by_id.setdefault(record_id, record)
                if len(by_id) > MAX_RECORDS:
                    raise ValueError()
        records = [copy.deepcopy(record) for record in sorted(by_id.values(),
                   key=lambda record: (record["time_utc"], record["record_id"]))]
        result = {"schema": projection.SCHEMA, "authority": "none", "scope": projection.SCOPE,
                  "accepted": len(records), "refused": {}, "dropped_fields": 0, "records": records,
                  "authorship": projection.AUTHORSHIP_ROSTER, "review_signal_excluded": True}
        projection.validate_projection(result, approved_tasks=tasks, include_refs=False,
                                       roster=roster, exclude_review_signal=True)
        return result
    except Exception:
        raise HistoryRefused("history_refused") from None

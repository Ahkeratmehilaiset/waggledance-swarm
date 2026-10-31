"""Inactive in-memory metadata reducer; not wired to any live source or writer.

Input must be a strict in-process anchored snapshot already obtained by the
caller. Pins are caller assertions, not authentication; this function cannot
verify source identity, operating-system access, freshness or same-user races.
It returns bounded metadata bytes and performs no I/O, logging or publication.
Caller-approved task labels must themselves be safe to disclose. The emitted
records and timing may still reveal activity; no general noninterference or
anonymity is claimed. The snapshot binding is private, never returned.
"""
import json

from sandbox.mind.step2 import shared_bridge_snapshot as snapshot
from sandbox.mind.step2 import shared_capture_adapter as adapter
from sandbox.mind.step2 import shared_projection as projection

MAX_OUTPUT_BYTES = 1024 * 1024


class StatusRefused(ValueError):
    """Fixed error, with no source path, token, data or cursor."""


def build_status(snapshot_result, *, enabled=False, anchor_sha256, policy_bytes, policy_sha256,
                 roster_bytes, roster_sha256, public_policy_bytes=None, public_policy_sha256=None, anchor_bytes=None):
    """Return metadata only after matching explicit anchor, policy and roster.

    This is neither a durable producer nor a checkpoint; the caller must own
    the trusted pins and separately establish capture freshness and continuity.
    """
    try:
        if enabled is not True or type(snapshot_result) is not dict:
            raise ValueError()
        tasks, roster = adapter.strict_pins(policy_bytes, policy_sha256, roster_bytes, roster_sha256)
        public_tasks, public_types = adapter.public_status_policy(public_policy_bytes, public_policy_sha256, tasks)
        data = snapshot_result.get("projection")
        snapshot.verify_public_anchor(snapshot_result, anchor_bytes, expected_anchor_sha256=anchor_sha256,
            expected_policy_sha256=policy_sha256, expected_roster_sha256=roster_sha256,
            expected_public_policy_sha256=public_policy_sha256)
        if snapshot.verify_anchored_projection(snapshot_result, data, expected_anchor_sha256=anchor_sha256,
                expected_policy_sha256=policy_sha256, expected_roster_sha256=roster_sha256,
                expected_mode="public-v1", expected_public_policy_sha256=public_policy_sha256,
                expected_anchor_bytes=anchor_bytes) is not True:
            raise ValueError()
        projection.validate_projection(data, approved_tasks=tasks, include_refs=False, roster=roster,
                                       exclude_review_signal=True)
        if data["refused"] or len(data["records"]) > snapshot.MAX_ROWS:
            raise ValueError()
        records = []
        for record in data["records"]:
            meta = record["meta"]
            if (meta["task_id"] not in public_tasks or meta["type"] not in public_types
                    or meta["agent"] in ("claude-rco-1", "claude-rco-2")):
                raise ValueError()
            records.append({"time_utc": record["time_utc"], "agent": meta["agent"],
                            "type": meta["type"], "status": meta["status"], "task_id": meta["task_id"]})
        result = {"schema": "wd.mind-status-metadata.v1", "authority": "none",
                  "scope": "in_memory_metadata_not_gate", "freshness": "UNKNOWN", "records": records}
        output = json.dumps(result, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                            allow_nan=False).encode("ascii")
        if len(output) > MAX_OUTPUT_BYTES:
            raise ValueError()
        return output
    except Exception:
        raise StatusRefused("status_refused") from None

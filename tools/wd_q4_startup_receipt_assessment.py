"""Assess already-read Mind startup receipts without launching or changing anything.

The caller must independently authenticate the expected inputs, collect each
receipt safely, observe process liveness, and carry out any restoration.  A
PASS here does not prove that an agent consumed recalled text.  This pure
assessment does not install, restart, access files, or execute a rollback.
"""

from datetime import datetime, timedelta, timezone
import re


LANES = (
    "codex-lead-1", "codex-tools-1", "fable-5", "claude-rco-1", "claude-rco-2",
)
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_RUN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


def _digest(value):
    return type(value) is str and _DIGEST.fullmatch(value) is not None


def _utc(value):
    if type(value) is not str or not value.endswith("Z"):
        raise ValueError("UTC timestamp required")
    try:
        result = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ValueError("invalid UTC timestamp") from exc
    if result.tzinfo != timezone.utc:
        raise ValueError("UTC timestamp required")
    return result


def assess_startup_receipts(*, expected_runs, expected_manifest_sha256,
                            expected_descriptor_sha256, records,
                            observed_at_utc, started_at_utc, deadline_utc, now_utc):
    """Return RECEIPTS_OK, WAIT, or ROLLBACK_REQUIRED, never release approval.

    ``expected_runs`` is supplied by the caller's independently pinned launch
    plan. The caller independently supplies when each receipt was observed;
    the receipt timestamp is the read's start, not proof of completion. A stale
    or absent receipt waits until the deadline; a current-run refusal is
    immediately reported. No filesystem or process I/O is performed.
    """
    if (type(expected_runs) is not dict or set(expected_runs) != set(LANES)
            or any(type(run) is not str or _RUN.fullmatch(run) is None
                   for run in expected_runs.values())
            or not _digest(expected_manifest_sha256)
            or not _digest(expected_descriptor_sha256)
            or type(records) is not dict or type(observed_at_utc) is not dict):
        raise ValueError("invalid expected startup inputs")
    start, deadline, now = map(_utc, (started_at_utc, deadline_utc, now_utc))
    if deadline <= start or now < start:
        raise ValueError("invalid startup interval")

    lanes = {}
    for lane in LANES:
        record = records.get(lane)
        if type(record) is not dict or record.get("run_id") != expected_runs[lane]:
            lanes[lane] = "PENDING"
            continue
        try:
            recorded_at = _utc(record.get("ts_utc"))
        except ValueError:
            lanes[lane] = "INVALID"
            continue
        # Launcher timestamps are truncated to seconds, so the same second
        # may legitimately begin just before a subsecond launch anchor.
        if recorded_at + timedelta(seconds=1) <= start:
            lanes[lane] = "PENDING"
            continue
        observed = observed_at_utc.get(lane)
        if observed is None:
            lanes[lane] = "PENDING"
            continue
        try:
            observed_at = _utc(observed)
        except ValueError:
            lanes[lane] = "INVALID"
            continue
        refs = record.get("refs")
        valid = (
            recorded_at <= observed_at <= now and observed_at <= deadline
            and record.get("schema") == "wd.mind-startup-read.v1"
            and record.get("lane") == lane
            and record.get("expected_manifest_sha256") == expected_manifest_sha256
            and record.get("descriptor_sha256") == expected_descriptor_sha256
            and record.get("outcome") == "success"
            and type(record.get("exit_code")) is int and record["exit_code"] == 0
            and record.get("inner_status") == "ANSWER"
            and type(record.get("delivered_items")) is int and record["delivered_items"] > 0
            and type(record.get("delivered_chars")) is int and record["delivered_chars"] > 0
            and _digest(record.get("delivered_sha256"))
            and type(refs) is list and bool(refs) and all(_digest(ref) for ref in refs)
        )
        lanes[lane] = "PASS" if valid else "INVALID"

    if "INVALID" in lanes.values() or ("PENDING" in lanes.values() and now >= deadline):
        decision = "ROLLBACK_REQUIRED"
    elif "PENDING" in lanes.values():
        decision = "WAIT"
    else:
        decision = "RECEIPTS_OK"
    return {
        "decision": decision,
        "lanes": lanes,
        "process_liveness_verified": False,
        "agent_context_verified": False,
        "automatic_rollback_performed": False,
    }

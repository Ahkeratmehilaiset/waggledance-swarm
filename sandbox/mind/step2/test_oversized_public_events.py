"""Synthetic large-row exclusion: no real bridge rows, paths or secrets."""
import pytest

from sandbox.mind.step2 import shared_bridge_snapshot as snapshot
from sandbox.mind.step2.test_shared_bridge_catchup import catchup, digest, event, fixture, records


def _large_excluded():
    return event(2, task_id="nonreview-private", payload={}, message="x" * (270 * 1024))


def test_explicitly_excluded_large_valid_row_allows_public_catchup(tmp_path):
    path, gen, start = fixture(tmp_path, [event(0), _large_excluded(), event(1)])
    result, _ = catchup(path, gen, start, digest(b""), max_bytes=snapshot.MAX_BYTES)
    assert result["caught_up"] is True
    assert records(result) == 2
    assert result["candidate_cursor"].offset == path.stat().st_size


@pytest.mark.parametrize("change", ["eligible", "ambiguous", "invalid", "invalid_routing", "invalid_payload",
                                    "invalid_identity", "unknown_field"])
def test_large_eligible_ambiguous_or_invalid_rows_refuse(tmp_path, change):
    row = _large_excluded()
    if change == "eligible":
        row.update(task_id="public-task", payload={"public_status": True})
    elif change == "ambiguous":
        row["TASK_ID"] = "public-task"
    elif change == "invalid":
        row["ts_utc"] = "invalid-time"
    elif change == "invalid_routing":
        row["to"] = 1
    elif change == "invalid_payload":
        row["payload"] = []
    elif change == "invalid_identity":
        row["agent_uuid"] = "untrusted-synthetic-identity"
    else:
        row["unknown"] = "ambiguous"
    path, gen, start = fixture(tmp_path, [event(0), row])
    with pytest.raises(snapshot.SnapshotRefused):
        catchup(path, gen, start, digest(b""), max_bytes=snapshot.MAX_BYTES)


def test_large_excluded_rows_still_enforce_aggregate_budget(tmp_path):
    row = _large_excluded()
    row["message"] = "x" * (750 * 1024)
    path, gen, start = fixture(tmp_path, [row, dict(row, ts_utc="2026-10-31T12:00:51Z")])
    with pytest.raises(snapshot.SnapshotRefused):
        catchup(path, gen, start, digest(b""), max_bytes=snapshot.MAX_BYTES)


def test_large_excluded_row_with_bad_integrity_refuses(tmp_path, monkeypatch):
    path, gen, start = fixture(tmp_path, [_large_excluded()])
    original = snapshot.adapter.adapt_rows

    def alter_ref(rows, *args, **kwargs):
        rows[0]["ref"] = "0" * 64
        return original(rows, *args, **kwargs)

    monkeypatch.setattr(snapshot.adapter, "adapt_rows", alter_ref)
    with pytest.raises(snapshot.SnapshotRefused):
        catchup(path, gen, start, digest(b""), max_bytes=snapshot.MAX_BYTES)

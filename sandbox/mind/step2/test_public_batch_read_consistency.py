"""Synthetic batch-reader/verifier consistency; never use live bridge data."""
from datetime import datetime
import hashlib
import json

import pytest

from sandbox.mind.step2 import shared_bridge_snapshot as snapshot
from sandbox.mind.step2.test_status_public_filter import event, read_fixture
from waggledance.core.bridge_log_reader import BridgeCursor, BridgePrefixPin


def _read(path, pins, cutoff, *, max_rows=100, **kwargs):
    anchor = json.loads(pins["anchor_bytes"])
    cursor = BridgeCursor(**anchor["cursor_start"])
    start = BridgePrefixPin(cursor, hashlib.sha256(path.read_bytes()[:cursor.offset]).hexdigest())
    result = snapshot.read_public_catchup(path, cursor, frozenset({"public-task", "nonreview-private"}),
        enabled=True, generation_path=anchor["generation_path"], max_bytes=65536, max_rows=max_rows, max_windows=8,
        expected_prefix_sha256=start.sha256, max_prefix_bytes=65536, cutoff_utc=cutoff, **kwargs, **pins)
    return result, start


def _verify(result, start, pins, cutoff, *, batch):
    return snapshot.verify_public_catchup(result, expected_anchor_bytes=pins["anchor_bytes"],
        expected_anchor_sha256=pins["anchor_sha256"], expected_policy_sha256=pins["policy_sha256"],
        expected_roster_sha256=pins["roster_sha256"], expected_public_policy_sha256=pins["public_policy_sha256"],
        expected_start_prefix=start, expected_cutoff_utc=cutoff, expected_batch_rows=batch)


def test_batch_reader_rechecks_entire_window_before_exposing_young_record(tmp_path, monkeypatch):
    _, pins, path = read_fixture(tmp_path, [event(ts_utc="2026-10-31T12:00:59Z")])
    cutoff = datetime.fromisoformat("2026-10-31T12:01:00+00:00")
    real_ready = snapshot._ready_projection

    def faulty_per_row_guard(value, instant):
        if set(value) == {"records"}:  # simulate a bug in the per-row prefilter only
            return True
        return real_ready(value, instant)

    monkeypatch.setattr(snapshot, "_ready_projection", faulty_per_row_guard)
    result, start = _read(path, pins, cutoff, batch_rows=True)
    assert result["end_reason"] == "deferred" and result["windows"] == ()
    assert result["candidate_prefix"] == start and result["candidate_cursor"].offset == 0
    assert _verify(result, start, pins, cutoff, batch=True)


def test_young_private_row_never_projects_and_does_not_skip_young_public(tmp_path):
    _, pins, path = read_fixture(tmp_path, [
        event(ts_utc="2026-10-31T12:00:59Z", to="fable-5", message="synthetic-private"),
        event(ts_utc="2026-10-31T12:00:59Z"), event(ts_utc="2026-10-31T11:55:00Z")])
    cutoff = datetime.fromisoformat("2026-10-31T12:01:00+00:00")
    result, start = _read(path, pins, cutoff, batch_rows=True)
    first_line = path.read_bytes().index(b"\n") + 1
    assert result["candidate_cursor"].offset == first_line and result["end_reason"] == "deferred"
    assert all(not window["projection"]["records"] for window in result["windows"])
    assert "synthetic-private" not in repr(result)
    assert _verify(result, start, pins, cutoff, batch=True)


def test_batch_mismatched_public_roster_refuses(tmp_path):
    _, pins, path = read_fixture(tmp_path, [])
    row = event(ts_utc="2026-10-31T12:00:59Z", agent_uuid="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
    path.write_bytes(json.dumps(row, sort_keys=True).encode() + b"\n")
    with pytest.raises(snapshot.SnapshotRefused):
        _read(path, pins, datetime.fromisoformat("2026-10-31T12:01:00+00:00"), batch_rows=True)


def test_default_remains_unbatched_and_batch_requires_cutoff(tmp_path):
    _, pins, path = read_fixture(tmp_path, [event(ts_utc="2026-10-31T11:58:00Z")])
    cutoff = datetime.fromisoformat("2026-10-31T12:01:00+00:00")
    result, start = _read(path, pins, cutoff)
    assert "batch_rows" not in result and _verify(result, start, pins, cutoff, batch=False)
    with pytest.raises(snapshot.SnapshotRefused):
        _read(path, pins, None, batch_rows=True)


@pytest.mark.parametrize("broken_row_guard", [False, True], ids=["normal-prefilter", "window-recheck"])
def test_later_fully_young_window_refuses_instead_of_returning_unverifiable_chain(tmp_path, monkeypatch,
                                                                                  broken_row_guard):
    _, pins, path = read_fixture(tmp_path, [event(ts_utc="2026-10-31T11:58:00Z"),
                                           event(ts_utc="2026-10-31T12:00:59Z")])
    cutoff = datetime.fromisoformat("2026-10-31T12:01:00+00:00")
    if broken_row_guard:
        real_ready = snapshot._ready_projection
        def faulty_per_row_guard(value, instant):
            if set(value) == {"records"}:
                return True
            return real_ready(value, instant)
        monkeypatch.setattr(snapshot, "_ready_projection", faulty_per_row_guard)
    with pytest.raises(snapshot.SnapshotRefused):
        _read(path, pins, cutoff, batch_rows=True, max_rows=1)
    if broken_row_guard:
        monkeypatch.setattr(snapshot, "_ready_projection", real_ready)
    later = datetime.fromisoformat("2026-10-31T12:01:01+00:00")
    result, start = _read(path, pins, later, batch_rows=True, max_rows=1)
    raw = path.read_bytes()
    assert result["caught_up"] is True and result["end_reason"] == "snapshot_end"
    assert result["candidate_cursor"].offset == len(raw)
    assert result["candidate_prefix"].sha256 == hashlib.sha256(raw).hexdigest()
    assert sum(len(window["projection"]["records"]) for window in result["windows"]) == 2
    assert _verify(result, start, pins, later, batch=True)

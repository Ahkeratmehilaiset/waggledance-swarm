"""Synthetic opt-in catch-up readiness; no real bridge or runtime input."""
from dataclasses import asdict
from datetime import datetime
import hashlib
import json

import pytest

from sandbox.mind.step2 import shared_bridge_snapshot as snapshot
from sandbox.mind.step2.test_status_public_filter import event, read_fixture
from waggledance.core.bridge_log_reader import BridgeCursor, BridgePrefixPin


def _bytes(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _call(path, pins, cutoff):
    anchor = json.loads(pins["anchor_bytes"])
    cursor = BridgeCursor(**anchor["cursor_start"])
    start = BridgePrefixPin(cursor, _sha(path.read_bytes()[:cursor.offset]))
    result = snapshot.read_public_catchup(
        path, cursor, frozenset({"public-task", "nonreview-private"}), enabled=True,
        generation_path=anchor["generation_path"], max_bytes=65536, max_rows=100, max_windows=64,
        expected_prefix_sha256=start.sha256, max_prefix_bytes=65536, cutoff_utc=cutoff, **pins)
    assert snapshot.verify_public_catchup(
        result, expected_anchor_bytes=pins["anchor_bytes"], expected_anchor_sha256=pins["anchor_sha256"],
        expected_policy_sha256=pins["policy_sha256"], expected_roster_sha256=pins["roster_sha256"],
        expected_public_policy_sha256=pins["public_policy_sha256"], expected_start_prefix=start,
        expected_cutoff_utc=cutoff)
    return result


def _repin(pins, cursor):
    anchor = json.loads(pins["anchor_bytes"])
    anchor["cursor_start"] = asdict(cursor)
    raw = _bytes(anchor)
    return dict(pins, anchor_bytes=raw, anchor_sha256=_sha(raw))


def _records(result):
    return [record for window in result["windows"] for record in window["projection"]["records"]]


def test_ready_prefix_preserves_byte_cursor_before_young_row(tmp_path):
    _, pins, path = read_fixture(tmp_path, [event(ts_utc="2026-10-31T11:58:00Z"),
        event(ts_utc="2026-10-31T12:01:10Z"), event(ts_utc="2026-10-31T11:59:00Z")])
    cutoff = datetime.fromisoformat("2026-10-31T12:01:30+00:00")
    result = _call(path, pins, cutoff)
    original = path.read_bytes()
    end = original.index(b"\n") + 1
    assert result["end_reason"] == "deferred" and result["caught_up"] is False
    assert result["candidate_cursor"].offset == end
    assert result["candidate_prefix"].sha256 == _sha(original[:end])
    assert len(_records(result)) == 1
    assert "2026-10-31T12:01:00" not in repr(asdict(result["_catchup_binding"]))
    resumed = _call(path, _repin(pins, result["candidate_cursor"]),
                    datetime.fromisoformat("2026-10-31T12:02:01+00:00"))
    assert len(_records(resumed)) == 2 and resumed["candidate_cursor"].offset == len(original)


@pytest.mark.parametrize("cutoff,ready", [
    ("2026-10-31T12:01:00+00:00", False),
    ("2026-10-31T12:01:01+00:00", True),
])
def test_first_young_row_keeps_exact_start_until_strict_boundary(tmp_path, cutoff, ready):
    _, pins, path = read_fixture(tmp_path, [event(ts_utc="2026-10-31T12:00:59Z")])
    result = _call(path, pins, datetime.fromisoformat(cutoff))
    assert bool(_records(result)) is ready
    assert result["candidate_cursor"].offset == (path.stat().st_size if ready else 0)
    if not ready:
        assert result["end_reason"] == "deferred" and result["candidate_prefix"].sha256 == _sha(b"")


def test_partial_tail_does_not_advance_past_last_complete_row(tmp_path):
    _, pins, path = read_fixture(tmp_path, [event(ts_utc="2026-10-31T11:58:00Z")])
    with path.open("ab") as stream:
        stream.write(b'{"ts_utc":')
    result = _call(path, pins, datetime.fromisoformat("2026-10-31T12:01:01+00:00"))
    raw = path.read_bytes()
    end = raw.index(b"\n") + 1
    assert result["end_reason"] == "partial_record" and result["candidate_cursor"].offset == end
    assert result["candidate_prefix"].sha256 == _sha(raw[:end])


def test_cutoff_is_independently_required_and_cannot_be_relabelled(tmp_path):
    _, pins, path = read_fixture(tmp_path, [event(ts_utc="2026-10-31T12:00:59Z")])
    cutoff = datetime.fromisoformat("2026-10-31T12:01:00+00:00")
    result = _call(path, pins, cutoff)
    start = result["start_prefix"]
    common = dict(expected_anchor_bytes=pins["anchor_bytes"], expected_anchor_sha256=pins["anchor_sha256"],
                  expected_policy_sha256=pins["policy_sha256"], expected_roster_sha256=pins["roster_sha256"],
                  expected_public_policy_sha256=pins["public_policy_sha256"], expected_start_prefix=start)
    with pytest.raises(snapshot.SnapshotRefused):
        snapshot.verify_public_catchup(result, **common)  # no implied acceptance of this cutoff
    with pytest.raises(snapshot.SnapshotRefused):
        snapshot.verify_public_catchup(result, expected_cutoff_utc=datetime.fromisoformat(
            "2026-10-31T12:01:01+00:00"), **common)
    result["cutoff_utc"] = "2026-10-31T12:01:01+00:00"
    with pytest.raises(snapshot.SnapshotRefused):
        snapshot.verify_public_catchup(result, expected_cutoff_utc=cutoff, **common)


def test_existing_prefix_rewrite_is_not_accepted_on_resume(tmp_path):
    _, pins, path = read_fixture(tmp_path, [event(ts_utc="2026-10-31T11:58:00Z"),
        event(ts_utc="2026-10-31T12:01:10Z")])
    result = _call(path, pins, datetime.fromisoformat("2026-10-31T12:01:30+00:00"))
    current = result["candidate_prefix"]
    new_pins = _repin(pins, current.cursor)
    content = path.read_bytes()
    path.write_bytes(b" " + content[1:])  # rewrite prior bytes without advancing or changing generation
    anchor = json.loads(new_pins["anchor_bytes"])
    with pytest.raises(snapshot.SnapshotRefused):
        snapshot.read_public_catchup(
            path, current.cursor, frozenset({"public-task", "nonreview-private"}), enabled=True,
            generation_path=anchor["generation_path"], max_bytes=65536, max_rows=100, max_windows=64,
            expected_prefix_sha256=current.sha256, max_prefix_bytes=65536,
            cutoff_utc=datetime.fromisoformat("2026-10-31T12:02:01+00:00"), **new_pins)

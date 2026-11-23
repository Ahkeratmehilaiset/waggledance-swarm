"""Synthetic tests for the read-only live Mind snapshot boundary."""

from datetime import datetime, timedelta, timezone
import hashlib

import pytest

from tools import wd_bridge_mind as mind
from tools import wd_mind_live_reader as reader
from tools.bridge_compact_view import event_id


NOW = datetime(2026, 10, 31, 8, 0, tzinfo=timezone.utc)
LANES = ("codex-lead-1", "codex-tools-1", "claude-rco-1", "claude-rco-2", "fable-5")


@pytest.fixture
def publication(tmp_path):
    audit = tmp_path / ".codex-audit"
    audit.mkdir()
    event = {"agent": "codex-tools-1", "type": "message", "status": "ready",
             "task_id": "synthetic-task", "ts_utc": "2026-10-31T07:59:00Z"}
    row = {"event": event, "ref": event_id(event)}
    source = {"log_identity": "synthetic-log", "generation": "synthetic-gen", "cursor_start": 0,
              "cursor_end": 1, "event_count": 1, "coverage": "complete"}
    state = mind.full_rebuild([row], source=source, as_of_utc="2026-10-31T07:59:00Z")
    snapshot = mind.snapshot(state, as_of_utc="2026-10-31T07:59:00Z")
    path = audit / "publication.json"
    digest = mind.write_snapshot(snapshot, path, audit_root=audit)
    return path, audit, digest


def read(publication, **overrides):
    path, audit, digest = publication
    args = dict(path=path, audit_root=audit, expected_digest=digest, expected_log="synthetic-log",
                expected_generation="synthetic-gen", lane="codex-lead-1", now=NOW)
    args.update(overrides)
    return reader.read_current(**args)


@pytest.mark.parametrize("lane", LANES)
def test_all_lanes_receive_only_bounded_metadata(publication, lane):
    result = read(publication, lane=lane)
    assert result["lane"] == lane
    assert result["event_count"] == 1
    assert set(result) == {"lane", "as_of_utc", "generation", "snapshot_sha256", "last_event_ref", "event_count"}


@pytest.mark.parametrize("overrides", [
    {"expected_digest": "0" * 64},
    {"expected_log": "wrong-log"},
    {"expected_generation": "wrong-generation"},
    {"lane": "unknown-agent"},
    {"lane": []},
    {"now": NOW + timedelta(minutes=10)},
    {"now": NOW - timedelta(minutes=2)},
    {"now": datetime(2026, 10, 31, 8, 0)},
])
def test_mismatched_stale_future_or_invalid_context_is_refused(publication, overrides):
    with pytest.raises(reader.LiveReadRefused):
        read(publication, **overrides)


def test_corrupted_publication_is_refused(publication):
    path, _, _ = publication
    path.write_bytes(b"{}")
    with pytest.raises(reader.LiveReadRefused):
        read(publication)


def test_digest_is_for_snapshot_not_publication_bytes(publication):
    path, _, digest = publication
    assert hashlib.sha256(path.read_bytes()).hexdigest() != digest
    assert read(publication)["snapshot_sha256"] == digest


def test_ten_minute_boundary_is_inclusive(publication):
    assert read(publication, now=NOW + timedelta(minutes=9))["event_count"] == 1

"""Synthetic in-memory capture projection; never reads a real bridge or memory."""
import base64
import datetime as dt
import json

import pytest

from sandbox.mind import mind_capture_producer as producer
from sandbox.mind.step2 import shared_capture_adapter as adapter
from tools.bridge_compact_view import event_id

NOW = dt.datetime(2026, 10, 31, 12, 2, tzinfo=dt.timezone.utc)
IDENTITY = "windows-v1:0000abcd:0000000000000001"
TASKS = frozenset({"synthetic-task"})


def cursor(offset, generation="generation1", identity=IDENTITY):
    raw = json.dumps({"offset": offset, "file_identity": identity, "generation": generation},
                     sort_keys=True, separators=(",", ":")).encode()
    return "bc1." + base64.urlsafe_b64encode(raw).decode().rstrip("=")


def snapshot(**changes):
    value = {"file_identity": IDENTITY, "cursor_start": cursor(0), "cursor_end": cursor(100),
             "generation": "generation1", "snapshot_length": 100, "position": 100,
             "unconsumed_bytes": 0, "observed_utc": "2026-10-31T12:01:00Z"}
    value.update(changes)
    return value


def row(agent="codex-lead-1", task="synthetic-task", **extra):
    event = {"agent": agent, "type": "message", "task_id": task, "status": "done",
             "ts_utc": "2026-10-31T12:00:10Z", "message": "private-sentinel",
             "payload": {"private": "private-sentinel"}}
    event.update(extra)
    return {"event": event, "ref": event_id(event), "row_sha256": producer.row_digest(event), "source": "detail"}


def test_only_approved_non_rco_metadata_is_projected_and_duplicates_deduped(monkeypatch):
    monkeypatch.setattr(adapter, "_utc_now", lambda: NOW)
    result = adapter.adapt_rows([row(), row(), row("claude-rco-1"), row(task="unknown-task")],
                                snapshot(), TASKS, ttl_seconds=120)
    projection = result["projection"]
    assert projection["accepted"] == 1
    assert projection["records"][0]["meta"]["agent"] == "codex-lead-1"
    assert result["source"]["verification"] == "caller_asserted_not_authenticated"
    assert result["source"]["generation_status"] == "caller_asserted"
    assert result["source"]["age_seconds"] == 60 and result["source"]["ttl_seconds"] == 120
    assert result["source"]["cursor_start_sha256"] != result["source"]["cursor_end_sha256"]
    assert "private-sentinel" not in json.dumps(result)
    assert "unknown-task" not in json.dumps(result) and "claude-rco-1" not in json.dumps(result)


@pytest.mark.parametrize("field", ["ref", "row_sha256"])
def test_bad_canonical_ref_or_row_hash_refuses_before_projection(monkeypatch, field):
    monkeypatch.setattr(adapter, "_utc_now", lambda: NOW)
    bad = row()
    bad[field] = "0" * 64
    with pytest.raises(adapter.AdapterRefused, match="^capture_refused$"):
        adapter.adapt_rows([bad], snapshot(), TASKS, ttl_seconds=120)


@pytest.mark.parametrize("change", [
    {"unconsumed_bytes": 1}, {"snapshot_length": 101}, {"position": 99},
    {"observed_utc": "2026-10-31T11:00:00Z"}, {"observed_utc": "2026-10-31T12:02:00.000001Z"},
    {"observed_utc": "2026-10-31T12:01:00"}, {"file_identity": "different"},
])
def test_incomplete_stale_or_inconsistent_snapshot_refused(monkeypatch, change):
    monkeypatch.setattr(adapter, "_utc_now", lambda: NOW)
    with pytest.raises(adapter.AdapterRefused, match="^capture_refused$"):
        adapter.adapt_rows([row()], snapshot(**change), TASKS, ttl_seconds=120)


def test_missing_generation_is_explicitly_unknown(monkeypatch):
    monkeypatch.setattr(adapter, "_utc_now", lambda: NOW)
    snap = snapshot(generation=None, cursor_start=cursor(0, None), cursor_end=cursor(100, None))
    result = adapter.adapt_rows([row()], snap, TASKS, ttl_seconds=120)
    assert result["source"]["generation_status"] == "UNKNOWN"


def test_policy_is_explicit_and_event_types_cannot_override_review_exclusion(monkeypatch):
    monkeypatch.setattr(adapter, "_utc_now", lambda: NOW)
    with pytest.raises(adapter.AdapterRefused):
        adapter.adapt_rows([row()], snapshot(), None, ttl_seconds=120)
    result = adapter.adapt_rows([row("claude-rco-2", type="handoff")], snapshot(), TASKS, ttl_seconds=120)
    assert result["projection"]["accepted"] == 0


def test_excluded_row_still_requires_valid_canonical_ref(monkeypatch):
    monkeypatch.setattr(adapter, "_utc_now", lambda: NOW)
    excluded = row("claude-rco-1")
    excluded["ref"] = "f" * 64
    with pytest.raises(adapter.AdapterRefused, match="^capture_refused$"):
        adapter.adapt_rows([excluded], snapshot(), TASKS, ttl_seconds=120)


def test_cursor_generation_change_and_reversed_offsets_refuse(monkeypatch):
    monkeypatch.setattr(adapter, "_utc_now", lambda: NOW)
    for snap in [snapshot(cursor_end=cursor(100, "other")),
                 snapshot(cursor_start=cursor(101)), snapshot(cursor_end=cursor(100, identity="posix-v1:1:2"))]:
        with pytest.raises(adapter.AdapterRefused, match="^capture_refused$"):
            adapter.adapt_rows([row()], snap, TASKS, ttl_seconds=120)


def test_negative_or_unbounded_ttl_and_excess_precision_are_refused(monkeypatch):
    monkeypatch.setattr(adapter, "_utc_now", lambda: NOW)
    for ttl in (0, -1, float("inf"), float("nan"), True):
        with pytest.raises(adapter.AdapterRefused, match="^capture_refused$"):
            adapter.adapt_rows([row()], snapshot(), TASKS, ttl_seconds=ttl)
    with pytest.raises(adapter.AdapterRefused, match="^capture_refused$"):
        adapter.adapt_rows([row()], snapshot(observed_utc="2026-10-31T12:02:00.0000001Z"),
                           TASKS, ttl_seconds=120)


@pytest.mark.parametrize("recipient", ["claude-rco-1", "claude-rco-2", "RCO1", "rco-2",
                                        ["codex-tools-1", "claude-rco-1"], "fable-5,claude-rco-2"])
@pytest.mark.parametrize("field", ["to", "recipient"])
def test_approved_lead_message_addressed_to_rco_is_excluded(monkeypatch, field, recipient):
    monkeypatch.setattr(adapter, "_utc_now", lambda: NOW)
    result = adapter.adapt_rows([row(**{field: recipient})], snapshot(), TASKS, ttl_seconds=120)
    assert result["projection"]["accepted"] == 0


def test_very_large_ttl_is_refused_even_for_otherwise_valid_snapshot(monkeypatch):
    monkeypatch.setattr(adapter, "_utc_now", lambda: NOW)
    with pytest.raises(adapter.AdapterRefused, match="^capture_refused$"):
        adapter.adapt_rows([row()], snapshot(), TASKS, ttl_seconds=10**10)


def test_ttl_900_boundary_and_non_rco_recipient(monkeypatch):
    monkeypatch.setattr(adapter, "_utc_now", lambda: NOW)
    assert adapter.adapt_rows([row(to="fable-5")], snapshot(), TASKS, ttl_seconds=900)["projection"]["accepted"] == 1
    with pytest.raises(adapter.AdapterRefused, match="^capture_refused$"):
        adapter.adapt_rows([row()], snapshot(), TASKS, ttl_seconds=901)


@pytest.mark.parametrize("recipient", ["claude-rco-1", ["claude-rco-2"], {"opaque": "unknown"}])
def test_nested_or_unclassifiable_recipient_is_excluded(monkeypatch, recipient):
    monkeypatch.setattr(adapter, "_utc_now", lambda: NOW)
    result = adapter.adapt_rows([row(payload={"recipient": recipient})], snapshot(), TASKS, ttl_seconds=120)
    assert result["projection"]["accepted"] == 0


def test_oversized_event_refused_before_canonical_hash(monkeypatch):
    monkeypatch.setattr(adapter, "_utc_now", lambda: NOW)
    oversized = row(payload={"private": "secret-sentinel" * 30000})
    calls = []
    def must_not_hash(*_):
        calls.append(True)
        raise AssertionError("hashed before size check")
    monkeypatch.setattr(adapter, "event_id", must_not_hash)
    with pytest.raises(adapter.AdapterRefused, match="^capture_refused$") as error:
        adapter.adapt_rows([oversized], snapshot(), TASKS, ttl_seconds=120)
    assert "secret-sentinel" not in str(error.value)
    assert calls == []


def test_excessive_nesting_refused_before_canonical_hash(monkeypatch):
    monkeypatch.setattr(adapter, "_utc_now", lambda: NOW)
    nested = "private"
    for _ in range(40):
        nested = [nested]
    deep = row(payload={"x": nested})
    calls = []
    monkeypatch.setattr(adapter, "event_id", lambda value: calls.append(value))
    with pytest.raises(adapter.AdapterRefused, match="^capture_refused$"):
        adapter.adapt_rows([deep], snapshot(), TASKS, ttl_seconds=120)
    assert calls == []


def test_batch_budget_applies_before_hashing_each_row(monkeypatch):
    monkeypatch.setattr(adapter, "_utc_now", lambda: NOW)
    first = row()
    monkeypatch.setattr(adapter, "_BATCH_LIMIT", adapter._event_budget(first["event"]) * 2 - 1)
    calls = []
    def count_hash(value):
        calls.append(True)
        return event_id(value)
    monkeypatch.setattr(adapter, "event_id", count_hash)
    with pytest.raises(adapter.AdapterRefused, match="^capture_refused$"):
        adapter.adapt_rows([first, first], snapshot(), TASKS, ttl_seconds=120)
    assert calls == [True]


@pytest.mark.parametrize("field", ["To", "TO", "Recipient", "RECIPIENT"])
def test_mixed_case_top_level_rco_recipient_is_excluded(monkeypatch, field):
    monkeypatch.setattr(adapter, "_utc_now", lambda: NOW)
    assert adapter.adapt_rows([row(**{field: "claude-rco-1"})], snapshot(), TASKS,
                              ttl_seconds=120)["projection"]["accepted"] == 0


@pytest.mark.parametrize("payload_key", ["payload", "Payload", "PAYLOAD"])
@pytest.mark.parametrize("recipient_key", ["To", "Recipient"])
def test_mixed_case_payload_and_recipient_rco_is_excluded(monkeypatch, payload_key, recipient_key):
    monkeypatch.setattr(adapter, "_utc_now", lambda: NOW)
    item = row()
    item["event"].pop("payload")
    item["event"][payload_key] = {recipient_key: "claude-rco-2"}
    item["ref"] = event_id(item["event"])
    item["row_sha256"] = producer.row_digest(item["event"])
    assert adapter.adapt_rows([item], snapshot(), TASKS, ttl_seconds=120)["projection"]["accepted"] == 0


@pytest.mark.parametrize("changes", [
    {"to": "fable-5", "To": "claude-rco-1"},
    {"payload": {"to": "fable-5", "TO": "claude-rco-1"}},
    {"payload": {"x": 1}, "Payload": {"recipient": "claude-rco-1"}},
    {"payload": {"unknown": {"x": 1, "X": 2}}},
])
def test_ascii_case_collisions_are_refused(monkeypatch, changes):
    monkeypatch.setattr(adapter, "_utc_now", lambda: NOW)
    with pytest.raises(adapter.AdapterRefused, match="^capture_refused$"):
        adapter.adapt_rows([row(**changes)], snapshot(), TASKS, ttl_seconds=120)

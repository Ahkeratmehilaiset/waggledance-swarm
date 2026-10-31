"""Synthetic in-memory capture projection; never reads a real bridge or memory."""
import base64
import datetime as dt
import hashlib
import json

import pytest

from sandbox.mind import mind_capture_producer as producer
from sandbox.mind.step2 import shared_capture_adapter as adapter
from tools.bridge_compact_view import event_id

NOW = dt.datetime(2026, 10, 31, 12, 2, tzinfo=dt.timezone.utc)
IDENTITY = "windows-v1:0000abcd:0000000000000001"
TASKS = frozenset({"synthetic-task"})


def strict_inputs():
    roster = {"codex-lead-1": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
              "codex-tools-1": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
              "claude-rco-1": "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
              "claude-rco-2": "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
              "fable-5": "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"}
    policy = {"schema": "wd.q4-non-review-policy.v1", "approved_tasks": sorted(TASKS)}
    fleet = {"lanes": [{"agent": agent, "agent_uuid": uuid} for agent, uuid in roster.items()
                       if agent != "codex-tools-1"],
             "tools_supervisor": {"agent": "codex-tools-1", "agent_uuid": roster["codex-tools-1"]}}
    return policy, fleet, roster


def encoded_pins(policy, fleet):
    encode = lambda item: json.dumps(item, sort_keys=True, separators=(",", ":")).encode()
    p, r = encode(policy), encode(fleet)
    return (p, hashlib.sha256(p).hexdigest(), r, hashlib.sha256(r).hexdigest())


def test_strict_pins_include_tools_supervisor():
    policy, fleet, roster = strict_inputs()
    assert adapter.strict_pins(*encoded_pins(policy, fleet)) == (TASKS, roster)


def test_strict_pins_match_reader_policy_task_limit():
    policy, fleet, _ = strict_inputs()
    policy["approved_tasks"] = [f"synthetic-{i}" for i in range(512)]
    assert len(adapter.strict_pins(*encoded_pins(policy, fleet))[0]) == 512
    policy["approved_tasks"].append("synthetic-512")
    with pytest.raises(ValueError):
        adapter.strict_pins(*encoded_pins(policy, fleet))


@pytest.mark.parametrize("case", ["missing-tools", "duplicate-lane", "duplicate-uuid", "duplicate-task",
                                  "review-task", "extra-policy-field", "policy-case-collision",
                                  "fleet-case-collision", "policy-pin", "roster-pin"])
def test_strict_pins_fail_closed_on_ambiguous_or_incomplete_inputs(case):
    policy, fleet, roster = strict_inputs()
    if case == "missing-tools":
        fleet.pop("tools_supervisor")
    elif case == "duplicate-lane":
        fleet["lanes"].append(dict(fleet["tools_supervisor"]))
    elif case == "duplicate-uuid":
        fleet["tools_supervisor"]["agent_uuid"] = roster["codex-lead-1"]
    elif case == "duplicate-task":
        policy["approved_tasks"].append("synthetic-task")
    elif case == "review-task":
        policy["approved_tasks"].append("pr-review")
    elif case == "extra-policy-field":
        policy["extra"] = True
    elif case == "policy-case-collision":
        policy["Schema"] = policy["schema"]
    elif case == "fleet-case-collision":
        fleet["Lanes"] = []
    pins = list(encoded_pins(policy, fleet))
    if case == "policy-pin":
        pins[1] = "0" * 64
    elif case == "roster-pin":
        pins[3] = "0" * 64
    with pytest.raises(ValueError):
        adapter.strict_pins(*pins)


def test_strict_v6_removes_rco_targets_and_review_rows_before_counters(monkeypatch):
    monkeypatch.setattr(adapter, "_utc_now", lambda: NOW)
    policy, fleet, roster = strict_inputs()
    rows = [row(agent_uuid=roster["codex-lead-1"]),
            row("codex-tools-1", agent_uuid=roster["codex-tools-1"]),
            row("claude-rco-1", agent_uuid=roster["claude-rco-1"]),
            row(agent_uuid=roster["codex-lead-1"], To="claude-rco-2"),
            row(agent_uuid=roster["codex-lead-1"], payload={"Recipient": "claude-rco-1"}),
            row(agent_uuid=roster["codex-lead-1"], type="review"),
            row(agent_uuid=roster["codex-lead-1"], status="veto"),
            row("fable-5", task="pr-review", agent_uuid=roster["fable-5"])]
    p, ph, r, rh = encoded_pins(policy, fleet)
    result = adapter.adapt_rows(rows, snapshot(), ttl_seconds=120, policy_bytes=p, policy_sha256=ph,
                                roster_bytes=r, roster_sha256=rh)["projection"]
    assert result["accepted"] == 2 and result["refused"] == {}
    assert result["review_signal_excluded"] is True
    assert {item["meta"]["agent"] for item in result["records"]} == {"codex-lead-1", "codex-tools-1"}


@pytest.mark.parametrize("kind", ["unknown-agent", "missing-agent", "wrong-uuid", "missing-uuid", "rco-wrong-uuid"])
def test_strict_approved_task_rejects_unverifiable_identity(monkeypatch, kind):
    monkeypatch.setattr(adapter, "_utc_now", lambda: NOW)
    policy, fleet, roster = strict_inputs()
    if kind == "unknown-agent":
        bad = row("not-a-lane", agent_uuid=roster["codex-lead-1"])
    elif kind == "missing-agent":
        bad = row(None, agent_uuid=roster["codex-lead-1"])
    elif kind == "wrong-uuid":
        bad = row("fable-5", agent_uuid=roster["codex-lead-1"])
    elif kind == "rco-wrong-uuid":
        bad = row("claude-rco-1", agent_uuid=roster["codex-lead-1"])
    else:
        bad = row()
    p, ph, r, rh = encoded_pins(policy, fleet)
    with pytest.raises(adapter.AdapterRefused, match="^capture_refused$"):
        adapter.adapt_rows([bad], snapshot(), ttl_seconds=120, policy_bytes=p, policy_sha256=ph,
                           roster_bytes=r, roster_sha256=rh)


@pytest.mark.parametrize("change", [{"type": "not-a-valid-type"}, {"ts_utc": "not-a-time"},
                                     {"pr": "bad-synthetic-pr"}])
def test_strict_allowed_identity_projector_integrity_refusal_is_not_success(monkeypatch, change):
    monkeypatch.setattr(adapter, "_utc_now", lambda: NOW)
    policy, fleet, roster = strict_inputs()
    p, ph, r, rh = encoded_pins(policy, fleet)
    with pytest.raises(adapter.AdapterRefused, match="^capture_refused$"):
        adapter.adapt_rows([row(agent_uuid=roster["codex-lead-1"], **change)], snapshot(), ttl_seconds=120,
                           policy_bytes=p, policy_sha256=ph, roster_bytes=r, roster_sha256=rh)


def test_strict_explicit_exclusions_are_silent_even_for_unapproved_task(monkeypatch):
    monkeypatch.setattr(adapter, "_utc_now", lambda: NOW)
    policy, fleet, roster = strict_inputs()
    p, ph, r, rh = encoded_pins(policy, fleet)
    rows = [row("claude-rco-1", agent_uuid=roster["claude-rco-1"]),
            row(agent_uuid=roster["codex-lead-1"], status="veto"),
            row("unknown-agent", task="outside-policy", agent_uuid="synthetic-bad")]
    result = adapter.adapt_rows(rows, snapshot(), ttl_seconds=120, policy_bytes=p, policy_sha256=ph,
                                roster_bytes=r, roster_sha256=rh)
    assert result["projection"]["accepted"] == 0 and result["projection"]["refused"] == {}
    assert result["projection"]["dropped_fields"] == 0
    assert "claude-rco" not in json.dumps(result) and "outside-policy" not in json.dumps(result)


def test_legacy_unpinned_policy_does_not_gain_a_false_identity_claim(monkeypatch):
    monkeypatch.setattr(adapter, "_utc_now", lambda: NOW)
    _, _, roster = strict_inputs()
    result = adapter.adapt_rows([row("fable-5", agent_uuid=roster["codex-lead-1"])], snapshot(), TASKS,
                                ttl_seconds=120)
    assert result["projection"]["accepted"] == 1
    assert result["projection"]["authorship"] == adapter.projection.AUTHORSHIP_UNVERIFIED


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

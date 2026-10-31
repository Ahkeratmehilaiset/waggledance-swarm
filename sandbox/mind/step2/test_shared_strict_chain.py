"""Tests-first expected v6 strict-chain API, using synthetic JSONL only.

Proposed boundary (intentionally not yet implemented): snapshot/adapter and
resolver/child require caller-supplied policy_bytes, policy_sha256, roster_bytes
and roster_sha256. Roster bytes are anchored fleet JSON with four ``lanes``
and ``tools_supervisor``. No digest, UUID declaration or v6 label authenticates
itself: the caller must independently establish those expected pins.
"""
import datetime as dt
import hashlib
import io
import json

import pytest

from sandbox.mind.step2 import shared_bridge_snapshot as snapshot
from sandbox.mind.step2 import shared_capture_adapter as adapter
from sandbox.mind.step2 import shared_result_resolver as resolver
from sandbox.mind import mind_capture_producer as producer
from tools import wd_q4_resolved_child as child
from tools.bridge_compact_view import event_id
from waggledance.core.bridge_log_reader import read_bridge_log


ROSTER = {
    "codex-lead-1": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
    "codex-tools-1": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
    "claude-rco-1": "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
    "claude-rco-2": "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
    "fable-5": "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee",
}
TASK = "synthetic-task"


def sha(data):
    return hashlib.sha256(data).hexdigest()


def bindings():
    policy = json.dumps({"schema": "wd.q4-non-review-policy.v1", "approved_tasks": [TASK]},
                        sort_keys=True, separators=(",", ":")).encode()
    fleet = json.dumps({"lanes": [{"agent": name, "agent_uuid": uuid} for name, uuid in ROSTER.items()
                                   if name != "codex-tools-1"],
                        "tools_supervisor": {"agent": "codex-tools-1", "agent_uuid": ROSTER["codex-tools-1"]}},
                       sort_keys=True, separators=(",", ":")).encode()
    return {"policy_bytes": policy, "policy_sha256": sha(policy), "roster_bytes": fleet, "roster_sha256": sha(fleet)}


def event(agent, **changes):
    when = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=5)).isoformat()
    out = {"ts_utc": when, "agent": agent, "agent_uuid": ROSTER[agent], "type": "message", "status": "done",
           "task_id": TASK, "message": "private-body-sentinel", "payload": {"value": "private-payload-sentinel"}}
    out.update(changes)
    return out


def jsonl(events):
    return b"".join(json.dumps(item, separators=(",", ":")).encode() + b"\n" for item in events)


@pytest.fixture
def source(tmp_path):
    path = tmp_path / "synthetic-only.jsonl"
    path.write_bytes(b"")
    cursor = read_bridge_log(path).candidate_cursor
    assert cursor is not None  # synthetic caller's anchor; never mint this inside the adapter
    return path, cursor


def strict_snapshot(source, events, pins=None):
    path, cursor = source
    path.write_bytes(jsonl(events))
    return snapshot.read_shared_delta(path, cursor, max_bytes=65536, max_rows=100, **(bindings() if pins is None else pins))


def test_strict_snapshot_roster_policy_and_no_review_signal_in_records_or_counters(source):
    events = [
        event("codex-lead-1"), event("codex-tools-1"),
        event("fable-5", agent_uuid=ROSTER["codex-lead-1"]),  # wrong declared UUID
        event("claude-rco-1"), event("claude-rco-2"),
        event("codex-lead-1", To="claude-rco-1"),
        event("codex-lead-1", payload={"Recipient": "claude-rco-2"}),
        event("fable-5", task_id="review-task"),
        event("fable-5", task_id="fable-5/review"),
    ]
    result = strict_snapshot(source, events)
    projection = result["projection"]
    assert projection["schema"] == "wd.bridge-mind-shared-projection.v6"
    assert projection["authorship"] == "declared_roster_consistent"
    assert projection["review_signal_excluded"] is True
    assert {row["meta"]["agent"] for row in projection["records"]} == {"codex-lead-1", "codex-tools-1"}
    assert projection["accepted"] == 2 and projection["refused"] == {}
    assert all(row["meta"]["task_id"] == TASK for row in projection["records"])
    assert "private-body-sentinel" not in repr(result) and "private-payload-sentinel" not in repr(result)
    assert "review-task" not in repr(result) and "fable-5/review" not in repr(result)


@pytest.mark.parametrize("missing", ["policy_bytes", "policy_sha256", "roster_bytes", "roster_sha256"])
def test_snapshot_requires_each_independent_binding(source, missing):
    pins = bindings()
    pins[missing] = None
    with pytest.raises(snapshot.SnapshotRefused):
        strict_snapshot(source, [event("codex-lead-1")], pins)


@pytest.mark.parametrize("field", ["policy_sha256", "roster_sha256"])
def test_snapshot_refuses_external_pin_mismatch(source, field):
    pins = bindings()
    pins[field] = "0" * 64
    with pytest.raises(snapshot.SnapshotRefused, match="^snapshot_refused$"):
        strict_snapshot(source, [event("codex-lead-1")], pins)


def _direct_adapter_inputs(source):
    path, cursor = source
    item = event("codex-tools-1")
    path.write_bytes(jsonl([item]))
    result = read_bridge_log(path, cursor=cursor, max_bytes=65536, max_rows=100)
    assert result.candidate_cursor is not None
    descriptor = {"file_identity": cursor.file_identity, "cursor_start": snapshot._token(cursor),
                  "cursor_end": snapshot._token(result.candidate_cursor), "generation": cursor.generation,
                  "snapshot_length": result.snapshot_length, "position": result.candidate_cursor.offset,
                  "unconsumed_bytes": 0, "observed_utc": dt.datetime.now(dt.timezone.utc).isoformat()}
    row = {"event": item, "ref": event_id(item), "row_sha256": producer.row_digest(item), "source": "detail"}
    return [row], descriptor


def test_direct_adapter_requires_same_strict_roster_and_keeps_tools(source):
    rows, descriptor = _direct_adapter_inputs(source)
    result = adapter.adapt_rows(rows, descriptor, ttl_seconds=900, **bindings())
    assert result["projection"]["accepted"] == 1
    assert result["projection"]["records"][0]["meta"]["agent"] == "codex-tools-1"
    assert result["projection"]["review_signal_excluded"] is True
    assert result["projection"]["authorship"] == "declared_roster_consistent"


def test_direct_adapter_does_not_fall_back_when_roster_or_policy_pin_missing(source):
    rows, descriptor = _direct_adapter_inputs(source)
    for field in ("policy_sha256", "roster_sha256"):
        pins = bindings()
        pins[field] = None
        with pytest.raises(adapter.AdapterRefused, match="^capture_refused$"):
            adapter.adapt_rows(rows, descriptor, ttl_seconds=900, **pins)


def synthetic_v6_projection():
    """One well-formed projection; labels are NOT evidence it came from strict source processing."""
    from sandbox.mind.step2 import shared_projection as sp
    value = sp.project_events([event("codex-lead-1")], approved_tasks=frozenset({TASK}), include_refs=False)
    value.update(schema="wd.bridge-mind-shared-projection.v6", authorship="declared_roster_consistent",
                 review_signal_excluded=True)
    return value


def resolve_arguments(projection):
    data = json.dumps(projection, sort_keys=True, indent=1).encode()
    pinned = bindings()
    memory = json.dumps({"scope": "wd.q4-shared-projected-events.v1",
                         "projection_schema": "wd.bridge-mind-shared-projection.v6",
                         "projection_sha256": sha(data),
                         "policy": {"authorship": "declared_roster_consistent",
                                    "review_signal_excluded": True,
                                    "policy_sha256": pinned["policy_sha256"],
                                    "roster_sha256": pinned["roster_sha256"]}}, sort_keys=True).encode()
    row = projection["records"][0]
    hit = {"ref_sha256": sha(row["provenance"]["event_ref"].encode()),
           "doc_id_sha256": sha(row["record_id"].encode()),
           "ref_kind": "sha256_of_projected_metadata", "canonical_event": False}
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    return {"memory_manifest": memory, "expected_memory_sha256": sha(memory), "projection_bytes": data,
            "results": [hit], "query_utc": now, **pinned}


def test_resolver_accepts_only_explicit_pinned_strict_v6_and_returns_projected_text():
    projection = synthetic_v6_projection()
    output = resolver.resolve_results(**resolve_arguments(projection))
    assert output[0]["stored_text"] == projection["records"][0]["stored_text"]
    assert output[0]["canonical_event"] is False
    assert "private-body-sentinel" not in repr(output)


@pytest.mark.parametrize("change", ["loose", "missing_roster", "missing_policy", "bad_roster", "bad_memory"])
def test_resolver_refuses_weak_or_unanchored_tuple(change):
    projection = synthetic_v6_projection()
    if change == "loose":
        projection["review_signal_excluded"] = False
        projection["authorship"] = "declared_unverified"
    args = resolve_arguments(projection)
    if change == "missing_roster":
        args["roster_sha256"] = None
    elif change == "missing_policy":
        args["policy_sha256"] = None
    elif change == "bad_roster":
        args["roster_sha256"] = "0" * 64
    elif change == "bad_memory":
        args["expected_memory_sha256"] = "0" * 64
    with pytest.raises(resolver.ResolveRefused):
        resolver.resolve_results(**args)


def test_relabelled_projection_does_not_override_caller_memory_anchor():
    original = synthetic_v6_projection()
    args = resolve_arguments(original)
    # The labels alone can be forged: alter bytes and rehash a forged memory,
    # but the original independently supplied memory pin must still win.
    altered = dict(original, authorship="declared_unverified")
    changed = resolve_arguments(altered)
    changed["expected_memory_sha256"] = args["expected_memory_sha256"]
    with pytest.raises(resolver.ResolveRefused):
        resolver.resolve_results(**changed)


def test_resolved_child_requires_same_caller_anchored_roster(monkeypatch):
    from tools import wd_q4_agent_query as q4
    args = resolve_arguments(synthetic_v6_projection())
    monkeypatch.setattr(q4, "query_and_resolve", lambda *_, **__: {"status": "ABSTAIN", "results": []})
    data = {"policy_bytes": args["policy_bytes"], "policy_sha256": args["policy_sha256"],
            "roster_bytes": args["roster_bytes"], "roster_sha256": args["roster_sha256"],
            "memory_bytes": args["memory_manifest"], "memory_sha256": args["expected_memory_sha256"],
            "projection_bytes": args["projection_bytes"], "projection_sha256": sha(args["projection_bytes"])}
    out = io.StringIO()
    assert child.run_resolved(object(), io.BytesIO(b"synthetic-query"), out, **data) == 0
    assert json.loads(out.getvalue())["status"] == "ABSTAIN"
    data["roster_sha256"] = "0" * 64
    out = io.StringIO()
    assert child.run_resolved(object(), io.BytesIO(b"synthetic-query"), out, **data) == 2

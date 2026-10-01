# SPDX-License-Identifier: BUSL-1.1
"""F26 S-A: request-cancellation derivation from canonical events (pure, dormant). SYNTHETIC fixtures only."""
from __future__ import annotations

import ast
import copy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools import wd_routing_cancellations as module  # noqa: E402
from tools.wd_routing_cancellations import derive_cancellations  # noqa: E402

NOW = datetime(2026, 10, 1, 19, 30, tzinfo=timezone.utc)
TASK = "codex-lead-1/s-a-fixture"
OTHER = "codex-lead-1/s-a-other"
RID = "c82f2988-fixture"
DIGEST = "d" * 64
PREFIX = "a" * 64
MAX_AGE = 120
UNBOUND = "0" * 64


def events_sha256(events):
    """Independent re-statement of the snapshot digest contract (canonical JSON of the whole list)."""
    try:
        text = json.dumps(list(events), sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
    except Exception:      # a hostile or unencodable fixture: the deriver must refuse before comparing
        return UNBOUND
    return hashlib.sha256(text.encode("ascii")).hexdigest()


def snapshot(events=(), **over):
    row = {"schema": module.SNAPSHOT_SCHEMA, "log_generation": "gen-1", "file_identity": "vol-1:file-77",
           "snapshot_bytes": 4096, "prefix_sha256": PREFIX, "observed_utc": "2026-10-01T19:29:30Z",
           "truncated": False, "event_count": len(events), "events_sha256": events_sha256(events)}
    row.update(over)
    return row


def current(**over):
    row = {"log_generation": "gen-1", "file_identity": "vol-1:file-77", "log_bytes": 4096, "prefix_sha256": PREFIX}
    row.update(over)
    return row


def fact(task=TASK, rid=RID, digest=DIGEST, **payload_over):
    payload = {"schema": module.FACT_SCHEMA, "cancelled_request_id": rid, "cancelled_request_digest": digest,
               "scope": "whole_request"}
    payload.update(payload_over)
    return {"ts_utc": "2026-10-01T19:00:00Z", "agent": "codex-lead-1", "type": "decision", "status": "cancelled",
            "task_id": task, "message": "cancel", "payload": payload}


def lead(status="assigned", payload=None, task=TASK, kind="wake_request"):
    """A Lead event with a benign request-shaped payload unless one is given."""
    if payload is None:
        payload = {"task_revision": "r-1", "result_fields": ["summary", "head"],
                   "result_contract": {"schema": "wd.task-result-contract.v1", "required": ["summary"],
                                       "additional_properties": False, "types": {"summary": "string"}}}
    return {"ts_utc": "2026-10-01T19:00:00Z", "agent": "codex-lead-1", "type": kind, "status": status,
            "task_id": task, "message": "m", "payload": payload}


def derive(events, snap=None, log=None, now=NOW, max_age=MAX_AGE):
    events = list(events)
    snap = snapshot(events) if snap is None else snap
    RECORD.clear()        # building the fixture digest may touch a hostile fixture; only the deriver counts
    out = derive_cancellations(events, snap, current() if log is None else log, now, max_age_seconds=max_age)
    assert list(out) == list(module.OUTPUT_FIELDS) and out["schema"] == module.SCHEMA
    if not out["complete"]:
        assert out["cancelled"] == [] and out["unknown_tasks"] == [] and out["reason"]
    return out


CLEAR = {"complete": True, "cancelled": [], "unknown_tasks": [], "reason": None}


def view(out):
    return {key: out[key] for key in ("complete", "cancelled", "unknown_tasks", "reason")}


# --- the fact ------------------------------------------------------------------------------------------

def test_a_closed_whole_request_fact_is_cancelled_with_the_digest_verbatim():
    out = derive([fact(), {"agent": "fable-5", "status": "done", "task_id": TASK}])
    assert out == {"schema": module.SCHEMA, "complete": True,
                   "cancelled": [{"task_id": TASK, "request_id": RID, "request_digest": DIGEST}],
                   "unknown_tasks": [], "reason": None}


def test_an_exact_repeat_of_a_fact_counts_once():
    assert derive([fact(), fact()])["cancelled"] == [{"task_id": TASK, "request_id": RID, "request_digest": DIGEST}]


def test_no_cancellation_at_all_is_complete_and_empty():
    out = derive([{"agent": "codex-lead-1", "type": "wake_request", "status": "assigned", "task_id": TASK}])
    assert (out["complete"], out["cancelled"], out["unknown_tasks"]) == (True, [], [])


@pytest.mark.parametrize("status", ["assigned", "answered", "done", "progress", "scope_update", None])
def test_a_benign_lead_event_is_not_a_control_and_leaves_the_task_clear(status):
    assert view(derive([lead(status)])) == CLEAR


# --- the six measured legacy shapes and other non-facts: the TASK is unknown, never a fact, never ignored ----

LEGACY_PAYLOADS = [
    {},                                                                          # 09-29T09:37Z message
    {"cancelled_request_id": RID, "supersedes_request_id": "x", "production_hold": True},
    {"cancelled_request_id": RID, "cancellation_reason": "r", "production_hold": True},
    {"cancelled_request_id": RID, "scope": "one_prior_request_only"},
    {"cancelled_request_id": RID, "scope": "source_implementation_only", "schema_proposal_retained": True},
    {"cancelled_request_ids": [RID], "reason": "r"},                              # list form
]


@pytest.mark.parametrize("payload", LEGACY_PAYLOADS)
def test_a_legacy_cancellation_shape_makes_its_task_unknown_and_is_never_a_fact(payload):
    event = fact()
    event["payload"] = payload
    out = derive([event, fact(task=OTHER, rid="r-other")])
    assert out["complete"] is True and out["unknown_tasks"] == [TASK]
    assert out["cancelled"] == [{"task_id": OTHER, "request_id": "r-other", "request_digest": DIGEST}]


@pytest.mark.parametrize("over", [
    {"scope": "source_implementation_only"}, {"scope": "Whole_Request"}, {"cancelled_request_digest": None},
    {"cancelled_request_digest": "D" * 64}, {"cancelled_request_digest": "d" * 63}, {"cancelled_request_id": ""},
    {"cancelled_request_id": ["a"]}, {"extra": 1}, {"schema": "wd.request-cancellation.v0"},
])
def test_a_partial_or_malformed_v1_payload_makes_its_task_unknown(over):
    event = fact()
    event["payload"].update(over)
    out = derive([event])
    assert out["complete"] is True and out["cancelled"] == [] and out["unknown_tasks"] == [TASK]


def test_a_v1_payload_missing_a_key_makes_its_task_unknown():
    event = fact()
    del event["payload"]["scope"]
    assert derive([event])["unknown_tasks"] == [TASK]


def test_a_legacy_unknown_task_also_hides_a_v1_fact_for_the_same_task():
    legacy = fact()
    legacy["payload"] = {"cancelled_request_id": "older"}
    out = derive([legacy, fact()])
    assert out["cancelled"] == [] and out["unknown_tasks"] == [TASK]


@pytest.mark.parametrize("task", [None, "", 7, ["t"]])
def test_an_authority_cancellation_with_no_readable_task_makes_everything_incomplete(task):
    event = fact()
    event["task_id"] = task
    out = derive([event, fact(task=OTHER, rid="r-other")])
    assert out["reason"] == "cancellation_unattributable"


def test_an_event_that_is_not_an_object_makes_everything_incomplete():
    assert derive([fact(), ["not", "an", "event"]])["reason"] == "input_malformed"


# --- H-1: every other authority control shape makes its task unknown (80813c0d) ---------------------------

CONTROL_STATUSES = ["Cancelled", "CANCELLED", "canceled", "cancel", "hold", "HOLD", "on_hold", "Hold", "paused",
                    "pause", "withdrawn", "superseded", "revoked", "aborted", "suspended", "frozen", "halted",
                    "stopped", "rescinded", "retracted", "ON_HOLD", "held", "work_held"]


@pytest.mark.parametrize("status", CONTROL_STATUSES)
@pytest.mark.parametrize("payload", [{}, None, "free"], ids=["empty", "none", "str"])
def test_a_control_status_makes_its_task_unknown_and_leaves_other_tasks(status, payload):
    out = derive([lead(status, payload), fact(task=OTHER, rid="r-other")])
    assert out["complete"] is True and out["unknown_tasks"] == [TASK]
    assert out["cancelled"] == [{"task_id": OTHER, "request_id": "r-other", "request_digest": DIGEST}]


@pytest.mark.parametrize("status", ["Cancelled", "CANCELLED", "canceled", "cancel", "done", "answered", None,
                                    "cancelled ", " cancelled", "hold"])
def test_a_v1_fact_under_any_status_but_exactly_cancelled_is_never_a_fact_and_its_task_is_unknown(status):
    event = fact()
    event["status"] = status
    out = derive([event])
    assert out["complete"] is True and out["cancelled"] == [] and out["unknown_tasks"] == [TASK]


def test_a_casefolded_cancelled_never_promotes_and_hides_the_exact_fact_for_the_same_task():
    variant = fact()
    variant["status"] = "Cancelled"
    out = derive([variant, fact()])
    assert out["cancelled"] == [] and out["unknown_tasks"] == [TASK]


@pytest.mark.parametrize("status, payload", [
    ("answered", {"cancelled_request_id": RID}),                     # the real 10-01T16:08Z Lead shape
    ("scope_update", {"production_hold": True}),
    ("answered", {"supersedes_request_id": "r-0"}),
    ("assigned", {"task_revision": "r", "control": {"hold": True}}),  # a nested key
    ("assigned", {"result": [{"note": "n"}, {"paused_until": "2026-10-02T00:00:00Z"}]}),
    ("assigned", {"action": "pause"}),                                # a directive value
    ("assigned", {"directive": ["resume", "Withdraw"]}),
    ("assigned", {"state": "HOLD"}),
    ("assigned", {"cancelledRequestId": RID}),                        # camel case
    ("assigned", {"Production_Hold": False}),                         # a control key, whatever its value
    ("assigned", {"task_revision": "r", "release_held": True}),       # a held spelling
    ("assigned", "cancel all"),                                       # a str payload
])
def test_a_control_payload_under_any_status_makes_its_task_unknown(status, payload):
    out = derive([lead(status, payload), fact(task=OTHER, rid="r-other")])
    assert out["complete"] is True and out["unknown_tasks"] == [TASK]
    assert [row["task_id"] for row in out["cancelled"]] == [OTHER]


@pytest.mark.parametrize("kind", ["cancel", "hold", "pause_request", "Supersede"])
def test_a_control_type_makes_its_task_unknown(kind):
    assert derive([lead("assigned", kind=kind)])["unknown_tasks"] == [TASK]


@pytest.mark.parametrize("payload", [
    {"action": "resume"}, {"state": "live"}, {"note": "hold this thought"}, {"message": "cancel"},
    {"findings": ["HOLD stays", "paused lanes"]},
])
def test_benign_keys_and_free_text_values_are_not_controls(payload):
    assert view(derive([lead("answered", payload)])) == CLEAR


@pytest.mark.parametrize("status, payload", [("hold", {}), ("answered", {"production_hold": True}),
                                             ("Cancelled", {}), ("assigned", {"action": "pause"})])
@pytest.mark.parametrize("task", [None, "", 7, ["t"]])
def test_an_unattributable_control_makes_everything_incomplete(status, payload, task):
    event = lead(status, payload)
    event["task_id"] = task
    assert derive([event, fact(task=OTHER, rid="r-other")])["reason"] == "cancellation_unattributable"


@pytest.mark.parametrize("payload, named", [
    ({"cancelled_request_id": RID, "cancelled_task_id": OTHER}, [OTHER]),
    ({"held_task_ids": [OTHER, "codex-lead-1/third"]}, [OTHER, "codex-lead-1/third"]),
    ({"production_hold": True, "tasks": [OTHER]}, [OTHER]),
    ({"hold": {"target_task": OTHER}}, [OTHER]),
])
def test_tasks_named_by_a_control_are_unknown_too(payload, named):
    out = derive([lead("answered", payload), fact(task=OTHER, rid="r-other")])
    assert out["complete"] is True and out["cancelled"] == []
    assert out["unknown_tasks"] == sorted({TASK, *named})


@pytest.mark.parametrize("value", [None, "", 7, {"id": OTHER}, [OTHER, None], [OTHER, ""]])
def test_an_unreadable_task_named_by_a_control_makes_everything_incomplete(value):
    out = derive([lead("answered", {"production_hold": True, "held_task_ids": value})])
    assert out["reason"] == "cancellation_unattributable"


def test_a_task_key_in_a_benign_payload_is_not_read():
    assert view(derive([lead("answered", {"task_ids": 7, "task_revision": "r"})])) == CLEAR


def test_a_control_by_any_other_label_is_not_read():
    for agent in ["fable-5", "Codex-Lead-1", "operator"]:
        event = lead("hold", {"production_hold": True})
        event["agent"] = agent
        assert view(derive([event])) == CLEAR


# --- contradictions -------------------------------------------------------------------------------------

def test_one_request_with_two_digests_makes_the_task_unknown():
    out = derive([fact(), fact(digest="e" * 64)])
    assert out["cancelled"] == [] and out["unknown_tasks"] == [TASK]


def test_one_request_id_under_two_tasks_makes_both_tasks_unknown():
    out = derive([fact(task=TASK), fact(task=OTHER)])
    assert out["cancelled"] == [] and out["unknown_tasks"] == sorted([TASK, OTHER])


# --- only the authority label is read (a label is not provenance) ----------------------------------------

@pytest.mark.parametrize("agent", ["fable-5", "Codex-Lead-1", "codex-lead-1 ", "operator", None])
def test_a_cancellation_by_any_other_label_is_not_read(agent):
    event = fact()
    event["agent"] = agent
    assert view(derive([event])) == view(derive([]))


# --- E-1: the events are exactly the list the snapshot describes ---------------------------------------------

def test_omitting_the_cancellation_from_its_snapshot_is_incomplete_not_clear():
    full = [lead(), fact()]
    assert derive(full)["cancelled"] != []
    for subset in ([], [lead()], [fact()]):
        out = derive(subset, snapshot(full))
        assert out["complete"] is False and out["reason"] == "event_coverage_mismatch"


def test_reordering_or_changing_an_event_is_a_mismatch():
    full = [lead(), fact()]
    assert derive(list(reversed(full)), snapshot(full))["reason"] == "event_coverage_mismatch"
    changed = copy.deepcopy(full)
    changed[1]["message"] = "cancel!"
    assert derive(changed, snapshot(full))["reason"] == "event_coverage_mismatch"
    extra = full + [lead("answered")]
    assert derive(extra, snapshot(full))["reason"] == "event_coverage_mismatch"


def test_a_count_or_digest_alone_does_not_bind():
    full = [lead(), fact()]
    # the digest of the given list but the count of the full read, and the other way round
    assert derive([lead()], snapshot([lead()], event_count=2))["reason"] == "event_coverage_mismatch"
    assert derive([lead()], snapshot(full, event_count=1))["reason"] == "event_coverage_mismatch"


def test_the_digest_is_canonical_json_so_key_order_does_not_matter():
    event = fact()
    shuffled = dict(reversed(list(event.items())))
    shuffled["payload"] = dict(reversed(list(event["payload"].items())))
    assert list(shuffled) != list(event)
    out = derive([shuffled], snapshot([event]))
    assert out["cancelled"] == [{"task_id": TASK, "request_id": RID, "request_digest": DIGEST}]


def test_the_digest_is_ascii_escaped_json_of_the_whole_list():
    events = [lead("answered", {"note": "äö ☃ \ud800"}), fact()]
    text = json.dumps(events, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
    snap = snapshot(events, events_sha256=hashlib.sha256(text.encode("ascii")).hexdigest())
    assert derive(events, snap)["complete"] is True
    pretty = json.dumps(events, sort_keys=True, ensure_ascii=False)
    snap = snapshot(events, events_sha256=hashlib.sha256(pretty.encode("utf-8", "surrogatepass")).hexdigest())
    assert derive(events, snap)["reason"] == "event_coverage_mismatch"


def test_an_unencodable_int_is_input_malformed_not_a_crash():
    event = lead("answered", {"n": 10 ** 5000})
    assert derive([event], snapshot([lead()]))["reason"] == "input_malformed"


@pytest.mark.parametrize("over", [
    {"schema": "wd.routing-cancellation-snapshot.v0"}, {"schema": None}, {"event_count": True},
    {"event_count": -1}, {"event_count": "2"}, {"event_count": None}, {"events_sha256": "A" * 64},
    {"events_sha256": "a" * 63}, {"events_sha256": None},
])
def test_malformed_snapshot_event_binding_is_incomplete(over):
    assert derive([fact()], snapshot([fact()], **over))["reason"] == "snapshot_incomplete"


@pytest.mark.parametrize("field", ["schema", "event_count", "events_sha256"])
def test_a_snapshot_without_the_event_binding_is_incomplete(field):
    snap = snapshot([fact()])
    del snap[field]
    assert derive([fact()], snap)["reason"] == "snapshot_incomplete"


def test_the_legacy_unbound_snapshot_shape_is_incomplete():
    legacy = {key: value for key, value in snapshot([fact()]).items()
              if key not in ("schema", "event_count", "events_sha256")}
    assert derive([fact()], legacy)["reason"] == "snapshot_incomplete"


# --- read coverage: untruncated, the CURRENT log identity, fresh ---------------------------------------------

@pytest.mark.parametrize("snap, log, reason", [
    (snapshot([fact()], truncated=True), None, "snapshot_incomplete"),
    (snapshot([fact()], truncated=None), None, "snapshot_incomplete"),
    (snapshot([fact()], truncated=0), None, "snapshot_incomplete"),
    ({k: v for k, v in snapshot([fact()]).items() if k != "file_identity"}, None, "snapshot_incomplete"),
    (dict(snapshot([fact()]), extra=1), None, "snapshot_incomplete"),
    (snapshot([fact()], snapshot_bytes=True), current(log_bytes=True), "snapshot_incomplete"),
    (snapshot([fact()], observed_utc="2026-10-01T19:29:30"), None, "snapshot_incomplete"),
    (snapshot([fact()], prefix_sha256="A" * 64), current(prefix_sha256="A" * 64), "snapshot_incomplete"),
    (snapshot([fact()]), dict(current(), extra=1), "snapshot_incomplete"),
    (snapshot([fact()]), current(log_generation="gen-2"), "snapshot_not_current_log"),      # log rotated
    (snapshot([fact()]), current(file_identity="vol-1:file-78"), "snapshot_not_current_log"),
    (snapshot([fact()]), current(log_bytes=4097), "snapshot_not_current_log"),             # appended after the read
    (snapshot([fact()]), current(prefix_sha256="b" * 64), "snapshot_not_current_log"),     # another prefix
    (snapshot([fact()], observed_utc="2026-10-01T19:30:01Z"), None, "snapshot_future"),
    (snapshot([fact()], observed_utc="2026-10-01T19:27:59Z"), None, "snapshot_stale"),   # 121 s old, policy 120 s
])
def test_coverage_is_complete_only_for_a_current_untruncated_fresh_read(snap, log, reason):
    out = derive([fact()], snap, log)
    assert out["complete"] is False and out["reason"] == reason


def test_exactly_max_age_old_and_exactly_now_are_still_fresh():
    assert derive([fact()], snapshot([fact()], observed_utc="2026-10-01T19:28:00Z"))["complete"] is True
    assert derive([fact()], snapshot([fact()], observed_utc="2026-10-01T19:30:00Z"))["complete"] is True


def test_the_freshness_bound_is_the_callers_policy():
    old = snapshot([fact()], observed_utc="2026-10-01T19:00:00Z")
    assert derive([fact()], old, max_age=120)["reason"] == "snapshot_stale"
    assert derive([fact()], old, max_age=3600)["complete"] is True


def test_a_frozen_historical_inventory_never_counts_as_current_coverage():
    frozen = snapshot([fact()], snapshot_bytes=2048, prefix_sha256="c" * 64, observed_utc="2026-10-01T16:00:00Z")
    assert derive([fact()], frozen)["complete"] is False


# --- A-1: real-log scale with per-event bounds ------------------------------------------------------------

def realistic(index):
    """A reply-shaped event of 36 nodes (the 6000-event case the whole-list budget refused)."""
    return {"agent": "fable-5", "type": "message", "status": "answered", "task_id": f"t-{index}",
            "ts_utc": "2026-10-01T19:00:00Z", "message": "m",
            "payload": {"result": {"summary": "s", "head": "h", "findings": ["a", "b", "c"], "commands": ["x"],
                                   "test_status": "t", "next_action": "n"},
                        "execution_evidence": {f"field_{j}": "v" for j in range(16)}}}


def nodes(item):
    if type(item) is dict:
        return 1 + sum(nodes(child) for child in item.values())
    if type(item) is list:
        return 1 + sum(nodes(child) for child in item)
    return 1


@pytest.mark.parametrize("count", [6000, 60_000])
def test_a_real_scale_whole_log_read_is_complete(count):
    events = [realistic(index) for index in range(count)]
    assert nodes(events[0]) == 36 and count * 36 > 200_000
    events.insert(count // 2, fact())
    events.append(lead("hold", {}, task=OTHER))
    out = derive(events)
    assert out["complete"] is True and out["unknown_tasks"] == [OTHER]
    assert out["cancelled"] == [{"task_id": TASK, "request_id": RID, "request_digest": DIGEST}]


def test_the_node_budget_is_per_event():
    base = lead("answered", {"note": []})
    fill = module.MAX_EVENT_NODES - nodes(base)
    at_limit = lead("answered", {"note": [None] * fill})
    over = lead("answered", {"note": [None] * (fill + 1)})
    assert nodes(at_limit) == module.MAX_EVENT_NODES
    assert derive([at_limit, at_limit, fact()])["complete"] is True      # twice the budget across events
    assert derive([at_limit, over, fact()])["reason"] == "input_malformed"


def test_the_depth_bound_is_per_event():
    def nested(levels):
        item: object = "x"
        for _ in range(levels):
            item = [item]
        return item
    # the event dict is depth 0, payload 1, note 2: MAX_DEPTH - 2 list levels put "x" at MAX_DEPTH
    ok = lead("answered", {"note": nested(module.MAX_DEPTH - 2)})
    deep = lead("answered", {"note": nested(module.MAX_DEPTH - 1)})
    assert derive([ok])["complete"] is True
    assert derive([deep])["reason"] == "input_malformed"


def test_the_event_count_policy_is_explicit_and_bounded(monkeypatch):
    assert (module.MAX_EVENTS, module.MAX_EVENT_NODES, module.MAX_DEPTH) == (1_000_000, 50_000, 32)
    monkeypatch.setattr(module, "MAX_EVENTS", 3)
    assert derive([lead(), lead(), fact()])["complete"] is True
    out = derive([lead(), lead(), lead(), fact()])
    assert out["reason"] == "event_count_over_policy"


# --- caller contract ------------------------------------------------------------------------------------

@pytest.mark.parametrize("now", [datetime(2026, 10, 1, 19, 30), "2026-10-01T19:30:00Z", None])
def test_now_must_be_an_offset_aware_datetime(now):
    with pytest.raises(ValueError):
        derive_cancellations([], snapshot(), current(), now, max_age_seconds=MAX_AGE)


@pytest.mark.parametrize("max_age", [0, -1, 120.0, True, None, "120"])
def test_max_age_is_an_explicit_positive_int(max_age):
    with pytest.raises(ValueError):
        derive_cancellations([], snapshot(), current(), NOW, max_age_seconds=max_age)


def test_max_age_has_no_default():
    with pytest.raises(TypeError):
        derive_cancellations([], snapshot(), current(), NOW)


# --- hook-free strict gate ------------------------------------------------------------------------------

RECORD: list = []


class _RecStr(str):
    def __eq__(self, other):
        RECORD.append("eq")
        return str.__eq__(self, other)

    def __ne__(self, other):
        RECORD.append("ne")
        return str.__ne__(self, other)

    def casefold(self):
        RECORD.append("casefold")
        return str.casefold(self)

    def __contains__(self, item):
        RECORD.append("contains")
        return str.__contains__(self, item)

    __hash__ = str.__hash__


class _RecDict(dict):
    def get(self, *args):
        RECORD.append("get")
        return dict.get(self, *args)

    def __getitem__(self, key):
        RECORD.append("getitem")
        return dict.__getitem__(self, key)

    def __contains__(self, key):
        RECORD.append("contains")
        return dict.__contains__(self, key)

    def items(self):
        RECORD.append("items")
        return dict.items(self)

    def __len__(self):
        RECORD.append("len")
        return dict.__len__(self)


class _RecList(list):
    def __iter__(self):
        RECORD.append("iter")
        return list.__iter__(self)

    def __len__(self):
        RECORD.append("len")
        return list.__len__(self)


class _RecInt(int):
    def __eq__(self, other):
        RECORD.append("eq")
        return int.__eq__(self, other)

    __hash__ = int.__hash__


def _hostile_cases():
    def event_agent(e): e["agent"] = _RecStr("codex-lead-1")
    def event_status(e): e["status"] = _RecStr("cancelled")
    def event_task(e): e["task_id"] = _RecStr(TASK)
    def payload_dict(e): e["payload"] = _RecDict(e["payload"])
    def payload_digest(e): e["payload"]["cancelled_request_digest"] = _RecStr(DIGEST)
    def payload_key(e): e["payload"][_RecStr("production_hold")] = True
    def control_value(e): e["payload"]["action"] = _RecStr("pause")
    def task_list(e): e["payload"]["held_task_ids"] = _RecList([OTHER])
    def nested_dict(e): e["payload"]["control"] = _RecDict({"hold": True})
    def int_value(e): e["n"] = _RecInt(3)
    def nonfinite(e): e["note"] = float("nan")
    def tuple_value(e): e["note"] = (1, 2)
    return [event_agent, event_status, event_task, payload_dict, payload_digest, payload_key, control_value,
            task_list, nested_dict, int_value, nonfinite, tuple_value]


@pytest.mark.parametrize("spoil", _hostile_cases())
def test_a_foreign_value_in_any_event_makes_everything_incomplete_and_runs_no_hook(spoil):
    clean = [fact(), fact(task=OTHER, rid="r-other")]
    event = fact()
    spoil(event)
    out = derive([event, fact(task=OTHER, rid="r-other")], snapshot(clean))
    assert out["reason"] == "input_malformed" and RECORD == []


def test_a_subclass_event_object_runs_no_hook():
    out = derive([_RecDict(fact())], snapshot([fact()]))
    assert out["reason"] == "input_malformed" and RECORD == []


def test_a_subclass_event_list_runs_no_hook():
    events = _RecList([fact()])
    snap = snapshot([fact()])
    RECORD.clear()
    out = derive_cancellations(events, snap, current(), NOW, max_age_seconds=MAX_AGE)
    assert out["reason"] == "input_malformed" and RECORD == []


@pytest.mark.parametrize("which", ["snapshot", "current", "count", "digest"])
def test_a_foreign_identity_runs_no_hook(which):
    snap = snapshot([fact()])
    log = current()
    if which == "snapshot":
        snap = _RecDict(snap)
    elif which == "current":
        log = _RecDict(log)
    elif which == "count":
        snap["event_count"] = _RecInt(1)
    else:
        snap["events_sha256"] = _RecStr(snap["events_sha256"])
    assert derive([fact()], snap, log)["reason"] == "input_malformed" and RECORD == []


def test_deep_or_cyclic_input_is_incomplete_not_a_crash():
    deep: object = "x"
    for _ in range(80):
        deep = [deep]
    event = fact()
    event["note"] = deep
    assert derive([event], snapshot([fact()]))["reason"] == "input_malformed"
    loop: list = []
    loop.append(loop)
    event = fact()
    event["note"] = loop
    assert derive([event], snapshot([fact()]))["reason"] == "input_malformed"
    events: list = [fact()]
    events.append(events)
    assert derive(events, snapshot([fact(), fact()]))["reason"] == "input_malformed"


def test_exact_builtin_twins_of_the_hostile_cases_still_derive():
    assert derive([fact()])["cancelled"] == [{"task_id": TASK, "request_id": RID, "request_digest": DIGEST}]
    twin = lead("answered", {"production_hold": True, "action": "pause", "held_task_ids": [OTHER],
                             "control": {"hold": True}, "n": 3})
    assert derive([twin, fact()])["unknown_tasks"] == sorted([TASK, OTHER])


@pytest.mark.parametrize("events", [None, (fact(),), {"a": fact()}])
def test_events_must_be_a_list(events):
    out = derive_cancellations(events, snapshot([fact()]), current(), NOW, max_age_seconds=MAX_AGE)
    assert out["complete"] is False and out["reason"] == "input_malformed"


# --- dormant and distinct --------------------------------------------------------------------------------

def test_the_output_is_not_the_p2_input_schema_so_no_existing_consumer_reads_it():
    # P2 (claude-rco-2 307c2cce, module 8c29e16c) accepts a statement only when it is exactly
    # {schema: wd.routing-cancellation-coverage.v1, complete: True, cancelled: [...]} (closed keys).
    out = derive([fact()])
    assert out["schema"] != "wd.routing-cancellation-coverage.v1"
    assert set(out) != {"schema", "complete", "cancelled"}


def test_the_module_reads_no_clock_file_environment_reader_or_process():
    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    attrs = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    imports = {alias.name for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))
               for alias in node.names} | {node.module for node in ast.walk(tree)
                                             if isinstance(node, ast.ImportFrom) and node.module}
    assert not ({"open", "input", "exec", "eval"} & names)
    assert not ({"now", "utcnow", "today", "time", "environ", "getenv", "read_text", "run", "Popen", "load",
                 "loads"} & attrs)
    assert not ({"os", "subprocess", "time", "pathlib", "socket", "tools"} & imports)


def test_the_input_is_not_mutated():
    events = [fact(), fact(task=OTHER, rid="r-other"), lead("hold", {"held_task_ids": [OTHER]})]
    snap = snapshot(events)
    before = copy.deepcopy((events, snap))
    derive(events, snap)
    assert (events, snap) == before

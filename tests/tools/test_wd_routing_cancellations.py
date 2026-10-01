# SPDX-License-Identifier: BUSL-1.1
"""F26 S-A: request-cancellation derivation from canonical events (pure, dormant). SYNTHETIC fixtures only."""
from __future__ import annotations

import ast
import copy
from datetime import datetime, timedelta, timezone
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


def snapshot(**over):
    row = {"log_generation": "gen-1", "file_identity": "vol-1:file-77", "snapshot_bytes": 4096,
           "prefix_sha256": PREFIX, "observed_utc": "2026-10-01T19:29:30Z", "truncated": False}
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


def derive(events, snap=None, log=None, now=NOW, max_age=MAX_AGE):
    out = derive_cancellations(list(events), snapshot() if snap is None else snap, current() if log is None else log,
                               now, max_age_seconds=max_age)
    assert list(out) == list(module.OUTPUT_FIELDS) and out["schema"] == module.SCHEMA
    if not out["complete"]:
        assert out["cancelled"] == [] and out["unknown_tasks"] == [] and out["reason"]
    return out


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
    assert derive([event]) == derive([])


@pytest.mark.parametrize("status", ["Cancelled", "cancel", "done", None])
def test_only_status_exactly_cancelled_is_a_cancellation(status):
    event = fact()
    event["status"] = status
    assert derive([event]) == derive([])


# --- read coverage: untruncated, the CURRENT log identity, fresh ---------------------------------------------

@pytest.mark.parametrize("snap, log, reason", [
    (snapshot(truncated=True), None, "snapshot_incomplete"),
    (snapshot(truncated=None), None, "snapshot_incomplete"),
    (snapshot(truncated=0), None, "snapshot_incomplete"),
    ({k: v for k, v in snapshot().items() if k != "file_identity"}, None, "snapshot_incomplete"),
    (dict(snapshot(), extra=1), None, "snapshot_incomplete"),
    (snapshot(snapshot_bytes=True), current(log_bytes=True), "snapshot_incomplete"),
    (snapshot(observed_utc="2026-10-01T19:29:30"), None, "snapshot_incomplete"),
    (snapshot(prefix_sha256="A" * 64), current(prefix_sha256="A" * 64), "snapshot_incomplete"),
    (None, dict(current(), extra=1), "snapshot_incomplete"),
    (None, current(log_generation="gen-2"), "snapshot_not_current_log"),      # log rotated
    (None, current(file_identity="vol-1:file-78"), "snapshot_not_current_log"),
    (None, current(log_bytes=4097), "snapshot_not_current_log"),             # appended after the read
    (None, current(prefix_sha256="b" * 64), "snapshot_not_current_log"),     # another prefix
    (snapshot(observed_utc="2026-10-01T19:30:01Z"), None, "snapshot_future"),
    (snapshot(observed_utc="2026-10-01T19:27:59Z"), None, "snapshot_stale"),   # 121 s old, policy 120 s
])
def test_coverage_is_complete_only_for_a_current_untruncated_fresh_read(snap, log, reason):
    out = derive([fact()], snap, log)
    assert out["complete"] is False and out["reason"] == reason


def test_exactly_max_age_old_and_exactly_now_are_still_fresh():
    assert derive([fact()], snapshot(observed_utc="2026-10-01T19:28:00Z"))["complete"] is True
    assert derive([fact()], snapshot(observed_utc="2026-10-01T19:30:00Z"))["complete"] is True


def test_the_freshness_bound_is_the_callers_policy():
    old = snapshot(observed_utc="2026-10-01T19:00:00Z")
    assert derive([fact()], old, max_age=120)["reason"] == "snapshot_stale"
    assert derive([fact()], old, max_age=3600)["complete"] is True


def test_a_frozen_historical_inventory_never_counts_as_current_coverage():
    frozen = snapshot(snapshot_bytes=2048, prefix_sha256="c" * 64, observed_utc="2026-10-01T16:00:00Z")
    assert derive([fact()], frozen)["complete"] is False


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


def _hostile_cases():
    def event_agent(e): e["agent"] = _RecStr("codex-lead-1")
    def event_status(e): e["status"] = _RecStr("cancelled")
    def event_task(e): e["task_id"] = _RecStr(TASK)
    def payload_dict(e): e["payload"] = _RecDict(e["payload"])
    def payload_digest(e): e["payload"]["cancelled_request_digest"] = _RecStr(DIGEST)
    def nonfinite(e): e["note"] = float("nan")
    def tuple_value(e): e["note"] = (1, 2)
    return [event_agent, event_status, event_task, payload_dict, payload_digest, nonfinite, tuple_value]


@pytest.mark.parametrize("spoil", _hostile_cases())
def test_a_foreign_value_in_any_event_makes_everything_incomplete_and_runs_no_hook(spoil):
    RECORD.clear()
    event = fact()
    spoil(event)
    out = derive([event, fact(task=OTHER, rid="r-other")])
    assert out["reason"] == "input_malformed" and RECORD == []


def test_a_subclass_event_object_runs_no_hook():
    RECORD.clear()
    assert derive([_RecDict(fact())])["reason"] == "input_malformed" and RECORD == []


@pytest.mark.parametrize("which", ["snapshot", "current"])
def test_a_foreign_identity_runs_no_hook(which):
    RECORD.clear()
    snap = _RecDict(snapshot()) if which == "snapshot" else snapshot()
    log = _RecDict(current()) if which == "current" else current()
    assert derive([fact()], snap, log)["reason"] == "input_malformed" and RECORD == []


def test_deep_or_cyclic_input_is_incomplete_not_a_crash():
    deep: object = "x"
    for _ in range(80):
        deep = [deep]
    event = fact()
    event["note"] = deep
    assert derive([event])["reason"] == "input_malformed"
    loop: list = []
    loop.append(loop)
    event = fact()
    event["note"] = loop
    assert derive([event])["reason"] == "input_malformed"


def test_exact_builtin_twins_of_the_hostile_cases_still_derive():
    assert derive([fact()])["cancelled"] == [{"task_id": TASK, "request_id": RID, "request_digest": DIGEST}]


@pytest.mark.parametrize("events", [None, (fact(),), {"a": fact()}])
def test_events_must_be_a_list(events):
    out = derive_cancellations(events, snapshot(), current(), NOW, max_age_seconds=MAX_AGE)
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
    assert not ({"now", "utcnow", "today", "time", "environ", "getenv", "read_text", "run", "Popen"} & attrs)
    assert not ({"os", "subprocess", "time", "pathlib", "socket", "tools"} & imports)


def test_the_input_is_not_mutated():
    events = [fact(), fact(task=OTHER, rid="r-other")]
    before = copy.deepcopy(events)
    derive(events)
    assert events == before

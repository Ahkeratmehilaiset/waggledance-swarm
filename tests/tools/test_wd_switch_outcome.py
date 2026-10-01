# SPDX-License-Identifier: BUSL-1.1
"""Switch outcome reader (contract section 5): causal twins on the REAL RecoveryStore, plus hostile histories.

Each journal is written through tools/bridge_capacity_recovery.RecoveryStore with the exact move sequence and
reasons that tools/wd_lane_relaunch_executor.py writes, read back from SQLite here in the test, and then
injected into the pure reader (which itself touches no file).
"""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools.bridge_capacity_recovery import RecoveryStore  # noqa: E402
from tools.wd_switch_outcome import SCHEMA, derive_outcome  # noqa: E402
from test_bridge_capacity_recovery import plan as valid_plan  # noqa: E402  the store's own valid fixture

EPOCH = {"pid": 4243, "process_started_at": "2026-10-01T10:00:00+00:00", "native_thread_id": "thread-1",
         "session_id": "session-1", "launched_at": "2026-10-01T10:00:01+00:00", "profile": "fallback"}
BOUND = json.dumps(EPOCH, sort_keys=True)   # exactly what the executor journals at apply_pending -> verified


def _store(tmp_path):
    store = RecoveryStore(tmp_path / "recovery.sqlite3")
    return store, store.plan("request-1", valid_plan())


def _rows(tmp_path, tid):
    with sqlite3.connect(tmp_path / "recovery.sqlite3") as db:
        db.row_factory = sqlite3.Row
        return [dict(row) for row in db.execute(
            "SELECT sequence, transition_id, phase, observed_at, reason FROM journal WHERE transition_id=? "
            "ORDER BY sequence", (tid,))]


def _to_stop(store, tid):
    store.move(tid, "planned", "quiesced")
    store.move(tid, "quiesced", "checkpointed", checkpoint="provider_resume")
    store.move(tid, "checkpointed", "checkpointed", reason=json.dumps({"stop_intent_at": "2026-10-01T10:00:00+00:00"}))


def _to_fenced(store, tid):
    _to_stop(store, tid)
    store.move(tid, "checkpointed", "apply_pending", reason=json.dumps({"source_stopped_at": "2026-10-01T10:00:00+00:00"}))


def _outcome(tmp_path, tid, runner_returned=True):
    return derive_outcome(tid, _rows(tmp_path, tid), runner_returned=runner_returned)


# --- the executor's paths, causally through the real store -----------------------------------------------

def test_a_bound_target_with_delivered_continuity_is_the_only_success(tmp_path):
    store, tid = _store(tmp_path)
    _to_fenced(store, tid)
    store.move(tid, "apply_pending", "verified", reason=BOUND)
    store.move(tid, "verified", "resume_pending")
    store.move(tid, "resume_pending", "resumed", reason="continuity_delivered")
    result = _outcome(tmp_path, tid)
    assert (result["schema"], result["outcome"], result["success"], result["rows"]) == (SCHEMA, "CONTINUED", True, 8)
    assert result["target_epoch"] == EPOCH


def test_a_rollback_is_rolled_back_through_resume_and_never_a_success(tmp_path):
    store, tid = _store(tmp_path)
    _to_fenced(store, tid)
    store.move(tid, "apply_pending", "verified", reason="rolled_back_to_previous")
    assert _outcome(tmp_path, tid)["outcome"] == "ROLLED_BACK"
    store.move(tid, "verified", "resume_pending")
    assert _outcome(tmp_path, tid, runner_returned=False)["outcome"] == "ROLLED_BACK"
    store.move(tid, "resume_pending", "resumed", reason="continuity_delivered")
    result = _outcome(tmp_path, tid)
    assert (result["outcome"], result["success"], result["target_epoch"]) == ("ROLLED_BACK", False, None)


@pytest.mark.parametrize("runner_returned, expected", [(False, "AWAITING_CONTINUITY"), (True, "HELD")])
def test_an_unconfirmed_resume_is_never_continued(tmp_path, runner_returned, expected):
    store, tid = _store(tmp_path)
    _to_fenced(store, tid)
    store.move(tid, "apply_pending", "verified", reason=BOUND)
    store.move(tid, "verified", "resume_pending")       # resume_lane returned False: the executor stops here
    result = _outcome(tmp_path, tid, runner_returned)
    assert (result["outcome"], result["success"]) == (expected, False)


def test_a_failed_rollback_is_held(tmp_path):
    store, tid = _store(tmp_path)
    _to_fenced(store, tid)
    store.move(tid, "apply_pending", "apply_pending", reason="rollback_failed:not_verified")
    assert (_outcome(tmp_path, tid)["outcome"], _outcome(tmp_path, tid)["reason"]) == ("HELD", "rollback_failed:not_verified")


def test_a_failed_source_stop_is_cancelled(tmp_path):
    store, tid = _store(tmp_path)
    _to_stop(store, tid)
    store.move(tid, "checkpointed", "cancelled_before_apply", reason="source_stop_failed")
    assert _outcome(tmp_path, tid)["outcome"] == "CANCELLED"


@pytest.mark.parametrize("steps, expected", [(0, "REQUESTED"), (1, "QUIESCED"), (2, "CHECKPOINTED"),
                                             (3, "CHECKPOINTED"), (4, "FENCED")])
def test_a_crash_at_each_step_reports_where_it_stopped_never_success(tmp_path, steps, expected):
    store, tid = _store(tmp_path)
    moves = [("planned", "quiesced", {}), ("quiesced", "checkpointed", {"checkpoint": "provider_resume"}),
             ("checkpointed", "checkpointed", {"reason": json.dumps({"stop_intent_at": "t"})}),
             ("checkpointed", "apply_pending", {"reason": json.dumps({"source_stopped_at": "t"})})]
    for expected_phase, phase, extra in moves[:steps]:
        store.move(tid, expected_phase, phase, **extra)
    result = _outcome(tmp_path, tid)
    assert (result["outcome"], result["success"]) == (expected, False)


def test_verified_target_awaiting_resume_is_applied_not_success(tmp_path):
    store, tid = _store(tmp_path)
    _to_fenced(store, tid)
    store.move(tid, "apply_pending", "verified", reason=BOUND)
    result = _outcome(tmp_path, tid)
    assert (result["outcome"], result["success"], result["target_epoch"]) == ("APPLIED", False, EPOCH)


# --- markers, epoch, ordering and identity: hostile histories are UNKNOWN, never success ---------------------

def _history(*phases_reasons, tid=1):
    return [{"sequence": i + 1, "transition_id": tid, "phase": p, "observed_at": "2026-10-01T10:00:00+00:00",
             "reason": r} for i, (p, r) in enumerate(phases_reasons)]


GOOD = [("planned", None), ("quiesced", None), ("checkpointed", None),
        ("checkpointed", json.dumps({"stop_intent_at": "t"})), ("apply_pending", json.dumps({"source_stopped_at": "t"})),
        ("verified", BOUND), ("resume_pending", None), ("resumed", "continuity_delivered")]


def test_the_injected_good_history_is_continued():
    assert derive_outcome(1, _history(*GOOD), runner_returned=True)["outcome"] == "CONTINUED"


def _replace(index, row):
    rows = list(GOOD)
    rows[index] = row
    return rows


@pytest.mark.parametrize("rows, reason", [
    ([], "no_history"),
    ([("resumed", "continuity_delivered")], "move_impossible:0"),
    (_replace(3, ("checkpointed", "anything")), "self_move_marker_unknown:3"),
    (_replace(3, ("checkpointed", json.dumps({"stop_intent_at": "t", "extra": 1}))), "self_move_marker_unknown:3"),
    (GOOD[:3] + GOOD[4:], "apply_without_stop_intent:3"),
    (_replace(4, ("apply_pending", "x")), "self_move_marker_unknown:4"),
    (_replace(5, ("verified", "{}")), "verified_without_bound_epoch:5"),
    (_replace(5, ("verified", json.dumps(dict(EPOCH, pid=True)))), "verified_without_bound_epoch:5"),
    (_replace(5, ("verified", json.dumps(dict(EPOCH, pid=0)))), "verified_without_bound_epoch:5"),
    (_replace(5, ("verified", json.dumps({k: v for k, v in EPOCH.items() if k != "session_id"}))),
     "verified_without_bound_epoch:5"),
    (_replace(5, ("verified", "NaN")), "verified_without_bound_epoch:5"),
    (_replace(7, ("resumed", "other")), "resumed_without_continuity:7"),
    (GOOD[:6] + [("resumed", "continuity_delivered")], "move_impossible:6"),
    (GOOD + [("planned", None)], "move_impossible:8"),
    (GOOD[:5] + [("apply_pending", "rollback_failed:x"), ("verified", BOUND)], "row_after_held:6"),
    (_replace(5, ("verified", "rolled_back_to_previous ")), "verified_without_bound_epoch:5"),
    (_replace(0, ("PLANNED", None)), "phase_unknown:0"),
])
def test_hostile_or_impossible_histories_are_unknown(rows, reason):
    result = derive_outcome(1, _history(*rows), runner_returned=True)
    assert (result["outcome"], result["reason"], result["success"]) == ("UNKNOWN", reason, False)


def test_reordered_foreign_and_malformed_rows_are_unknown():
    rows = _history(*GOOD)
    swapped = list(rows); swapped[1], swapped[2] = swapped[2], swapped[1]
    assert derive_outcome(1, swapped, runner_returned=True)["reason"] == "move_impossible:1"
    resequenced = [dict(r) for r in rows]; resequenced[4]["sequence"] = 2
    assert derive_outcome(1, resequenced, runner_returned=True)["reason"] == "sequence_not_increasing:4"
    foreign = [dict(r) for r in rows]; foreign[6]["transition_id"] = 2
    assert derive_outcome(1, foreign, runner_returned=True)["reason"] == "row_foreign_transition:6"
    extra = [dict(r) for r in rows]; extra[0]["lane"] = "x"
    assert derive_outcome(1, extra, runner_returned=True)["reason"] == "row_malformed:0"
    no_time = [dict(r) for r in rows]; no_time[2]["observed_at"] = ""
    assert derive_outcome(1, no_time, runner_returned=True)["reason"] == "row_malformed:2"


def test_str_subclasses_and_bad_inputs_never_pass():
    class Lying(str):
        def __eq__(self, other):
            return True
        __hash__ = str.__hash__
    rows = _history(*GOOD)
    rows[7]["reason"] = Lying("continuity_delivered")
    assert derive_outcome(1, rows, runner_returned=True)["outcome"] == "UNKNOWN"
    assert derive_outcome(True, _history(*GOOD), runner_returned=True)["reason"] == "input_malformed"
    assert derive_outcome(1, _history(*GOOD), runner_returned=1)["reason"] == "input_malformed"
    assert derive_outcome(1, "rows", runner_returned=True)["reason"] == "no_history"

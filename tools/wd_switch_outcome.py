#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""Switch outcome reader: what one relaunch transition achieved (switch interface contract section 5).

Pure and passive. It reads only the records the caller injects: the RecoveryStore journal rows of ONE
transition (``journal(sequence, transition_id, phase, observed_at, reason)``, written by
``RecoveryStore.plan`` and ``RecoveryStore.move`` from ``tools/wd_lane_relaunch_executor.py``). It opens no
file, reads no clock, environment or process, and changes nothing. Nothing in the runtime calls it yet
and no flag enables anything.

Outcomes (``wd.switch-outcome.v1``)::

    REQUESTED            planned
    QUIESCED             quiesced
    CHECKPOINTED         checkpointed, with or without the executor's stop-intent self-move
    FENCED               apply_pending: the source was stopped, the target is not yet verified
    APPLIED              verified with a bound target epoch (still awaiting continuity)
    AWAITING_CONTINUITY  resume_pending while the runner may still deliver continuity
    CONTINUED            resumed after a bound target: the ONLY success
    ROLLED_BACK          verified with rolled_back_to_previous, and every later row: never a success
    CANCELLED            cancelled_before_apply (terminal)
    HELD                 an operator must reconcile: rollback_failed:*, or resume_pending after the runner returned
    UNKNOWN              anything this reader cannot prove; never a success

Rules that keep it honest:

* Success is derived ONLY from an ``apply_pending`` -> ``verified`` row whose reason is the executor's bound
  target epoch (a JSON object with exactly pid, process_started_at, native_thread_id, session_id, launched_at
  and profile, in their exact types), followed by ``resume_pending`` and ``resumed`` with
  ``continuity_delivered``. A final ``resumed`` never erases an earlier ``rolled_back_to_previous``.
* ``resume_pending`` is never CONTINUED.
* Self-moves carry markers: ``checkpointed`` -> ``checkpointed`` must be exactly ``{"stop_intent_at": str}``;
  ``apply_pending`` -> ``apply_pending`` must start with ``rollback_failed:``. An unknown marker is UNKNOWN; it
  can never manufacture a phase or a success.
* Ordering and identity are explicit: every row names the requested transition, ``sequence`` strictly
  increases, the first row is ``planned``, every move is one the executor makes, ``apply_pending`` requires an
  earlier stop intent, and nothing follows a terminal or HELD row.
* Exact types only (``type(x) is str`` / ``int``): a subclass can lie about equality.
"""
from __future__ import annotations

import json
from typing import Any

SCHEMA = "wd.switch-outcome.v1"
SUCCESS = "CONTINUED"
ROW_KEYS = frozenset({"sequence", "transition_id", "phase", "observed_at", "reason"})
EPOCH_TYPES = {"pid": int, "process_started_at": str, "native_thread_id": str, "session_id": str,
               "launched_at": str, "profile": str}
ROLLBACK = "rolled_back_to_previous"
CONTINUITY = "continuity_delivered"
# Every phase move wd_lane_relaunch_executor makes (a self-move carries only a marker).
MOVES = {
    None: {"planned"},
    "planned": {"quiesced"},
    "quiesced": {"checkpointed"},
    "checkpointed": {"checkpointed", "apply_pending", "cancelled_before_apply"},
    "apply_pending": {"apply_pending", "verified"},
    "verified": {"resume_pending"},
    "resume_pending": {"resumed"},
    "resumed": set(),
    "cancelled_before_apply": set(),
}
# Stable refusal reasons (UNKNOWN).
R_INPUT = "input_malformed"
R_EMPTY = "no_history"
R_ROW = "row_malformed"
R_FOREIGN = "row_foreign_transition"
R_ORDER = "sequence_not_increasing"
R_PHASE = "phase_unknown"
R_MOVE = "move_impossible"
R_MARKER = "self_move_marker_unknown"
R_NO_STOP_INTENT = "apply_without_stop_intent"
R_EPOCH = "verified_without_bound_epoch"
R_RESUMED = "resumed_without_continuity"
R_AFTER_HELD = "row_after_held"


def _result(transition_id: Any, outcome: str, reason: str | None, rows: int, phase: str | None = None,
            target_epoch: dict | None = None) -> dict:
    return {"schema": SCHEMA, "transition_id": transition_id, "outcome": outcome, "success": outcome == SUCCESS,
            "reason": reason, "phase": phase, "rows": rows, "target_epoch": target_epoch}


def _unknown(transition_id: Any, reason: str, rows: int, index: int | None = None) -> dict:
    return _result(transition_id, "UNKNOWN", reason if index is None else "%s:%d" % (reason, index), rows)


def _unique_pairs(pairs: list) -> dict:
    """A JSON object whose keys are all distinct; json.loads calls this for every object at every depth."""
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON key")
        value[key] = item
    return value


def _json_object(text: Any) -> dict | None:
    if type(text) is not str:
        return None
    try:
        # Strict JSON: a duplicate key at any depth is refused (never last-wins), as is NaN or Infinity.
        value = json.loads(text, object_pairs_hook=_unique_pairs,
                           parse_constant=lambda name: (_ for _ in ()).throw(ValueError(name)))
    except (ValueError, RecursionError):
        return None
    return value if type(value) is dict else None


def _bound_epoch(reason: Any) -> dict | None:
    """The executor's bound target epoch (json.dumps(bound, sort_keys=True)), exact keys and exact types."""
    epoch = _json_object(reason)
    if epoch is None or set(epoch) != set(EPOCH_TYPES):
        return None
    if any(type(epoch[key]) is not kind for key, kind in EPOCH_TYPES.items()):
        return None
    if epoch["pid"] <= 0 or not all(epoch[key] for key in EPOCH_TYPES if key != "pid"):
        return None
    return epoch


def _marker(reason: Any, key: str) -> bool:
    value = _json_object(reason)
    return value is not None and set(value) == {key} and type(value[key]) is str and bool(value[key])


def derive_outcome(transition_id: Any, rows: Any, *, runner_returned: Any) -> dict:
    """The outcome of ONE transition from its injected journal rows (any order is checked, never sorted)."""
    if type(transition_id) is not int or transition_id < 1 or type(runner_returned) is not bool:
        return _unknown(transition_id, R_INPUT, 0)
    if type(rows) is not list or not rows:
        return _unknown(transition_id, R_EMPTY, 0)
    count = len(rows)
    previous_phase, previous_sequence = None, None
    stop_intent = rolled_back = held = False
    target_epoch = None
    for index, row in enumerate(rows):
        if held:
            return _unknown(transition_id, R_AFTER_HELD, count, index)
        if type(row) is not dict or set(row) != ROW_KEYS:
            return _unknown(transition_id, R_ROW, count, index)
        sequence, owner, phase, observed, reason = (row["sequence"], row["transition_id"], row["phase"],
                                                    row["observed_at"], row["reason"])
        if (type(sequence) is not int or type(owner) is not int or type(observed) is not str or not observed
                or (reason is not None and type(reason) is not str)):
            return _unknown(transition_id, R_ROW, count, index)
        if owner != transition_id:
            return _unknown(transition_id, R_FOREIGN, count, index)
        if previous_sequence is not None and sequence <= previous_sequence:
            return _unknown(transition_id, R_ORDER, count, index)
        if type(phase) is not str or phase not in MOVES:
            return _unknown(transition_id, R_PHASE, count, index)
        if phase not in MOVES[previous_phase]:
            return _unknown(transition_id, R_MOVE, count, index)
        if phase == previous_phase == "checkpointed":
            if not _marker(reason, "stop_intent_at"):
                return _unknown(transition_id, R_MARKER, count, index)
            stop_intent = True
        elif phase == previous_phase == "apply_pending":
            if not (type(reason) is str and reason.startswith("rollback_failed:") and len(reason) > 16):
                return _unknown(transition_id, R_MARKER, count, index)
            held = True
        elif phase == "apply_pending":
            if not stop_intent:
                return _unknown(transition_id, R_NO_STOP_INTENT, count, index)
            if not _marker(reason, "source_stopped_at"):
                return _unknown(transition_id, R_MARKER, count, index)
        elif phase == "verified":
            if reason == ROLLBACK:
                rolled_back = True
            else:
                target_epoch = _bound_epoch(reason)
                if target_epoch is None:
                    return _unknown(transition_id, R_EPOCH, count, index)
        elif phase == "resumed" and reason != CONTINUITY:
            return _unknown(transition_id, R_RESUMED, count, index)
        previous_phase, previous_sequence = phase, sequence
    reason = rows[-1]["reason"]
    if held:
        return _result(transition_id, "HELD", reason, count, previous_phase)
    if rolled_back and previous_phase in ("verified", "resume_pending", "resumed"):
        if previous_phase == "resume_pending" and runner_returned:
            return _result(transition_id, "HELD", reason, count, previous_phase)
        return _result(transition_id, "ROLLED_BACK", reason, count, previous_phase)
    outcome = {
        "planned": "REQUESTED",
        "quiesced": "QUIESCED",
        "checkpointed": "CHECKPOINTED",
        "apply_pending": "FENCED",
        "verified": "APPLIED",
        "resume_pending": "HELD" if runner_returned else "AWAITING_CONTINUITY",
        "resumed": SUCCESS,
        "cancelled_before_apply": "CANCELLED",
    }[previous_phase]
    return _result(transition_id, outcome, reason, count, previous_phase,
                   target_epoch if outcome in ("APPLIED", "AWAITING_CONTINUITY", "HELD", SUCCESS) else None)

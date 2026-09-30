#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""F27: the durable task and operation journal for stand-in incarnation and hand-back.

Fixture-only and default OFF: there is no default root, nothing in the runtime imports this
module, and it runs, stops, launches and claims nothing. It is the TASK-progress journal,
kept separate from the process and profile transition journal
(``tools/bridge_capacity_recovery.RecoveryStore``): a relaunch or a stand-in changes who
runs a task, never what the task already did (plan section 2.12, Lead request c2300622).

One journal file per task: ``<root>/<task key>.jsonl``, append-only, one JSON object per
line, hash-chained (``seq``, ``prev_sha256`` of the previous line's exact bytes). Records:

* ``task_opened``: the immutable task identity (task_id, revision, base commit). A new
  revision is a new journal, never an edit.
* ``step_planned``, ``step_started``, ``step_committed``, ``step_failed``: one step open at
  a time; a committed step is final.
* ``wip_checkpoint``: base, head, dirty inventory, WIP artifact digest and the head the
  tests last ran on, for the open step. A WIP checkpoint is never "green".
* ``op_intent`` (op_id, op_kind, idempotency_key, idempotent), then ``op_attempted``,
  ``op_applied``, ``op_verified``, ``op_failed`` or ``op_unknown``: every action with an
  effect outside the worktree. ``op_attempted`` is written BEFORE the effect is called.
* ``fence``: the executor's complete fence of the previous writer (pid, start time, token,
  generation and descendants all verified). It is the only way ownership moves; an expired
  lease or old conversation metadata never is.
* ``hold`` and ``hold_released``; ``handback`` (at a step boundary, with no open operation).

Only the current owner (from ``task_opened`` or the last ``fence``/``handback``) may append,
under an exclusive OS lock and a compare-and-swap on the last sequence number.

``reconcile`` never replays anything. Its verdict is ``continue`` (with the resume point),
``reverify`` (idempotent operations whose outcome must be re-checked against the receiving
system's own receipt), or ``hold``: an unknown outcome of a non-idempotent operation, an
unreleased hold, or a torn, corrupt or foreign journal. A missing or corrupt journal is
never read as "nothing happened".
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, Iterator

SCHEMA = "wd.task-journal.v1"
ZERO = "0" * 64
HEX40 = re.compile(r"[0-9a-f]{40}")
HEX64 = re.compile(r"[0-9a-f]{64}")
TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,159}")
MAX_LINE_BYTES = 64 * 1024
MAX_RECORDS = 100_000
OWNER_KEYS = frozenset({"agent", "generation", "token"})
FENCE_EVIDENCE_KEYS = ("pid_verified", "start_time_verified", "token_revoked", "generation_verified",
                       "descendants_verified", "no_active_unclassified_write")
DIRTY_STATES = frozenset({"modified", "added", "deleted", "renamed", "untracked"})
OP_NEXT = {  # allowed operation transitions; verified and failed are terminal
    "intent": {"op_attempted", "op_failed"},
    "attempted": {"op_applied", "op_verified", "op_failed", "op_unknown"},  # verified implies applied
    "applied": {"op_verified", "op_unknown"},
    "unknown": {"op_verified", "op_failed"},
}
FIELDS = {
    "task_opened": {"task_id", "revision", "base_commit"},
    "step_planned": {"step", "title"},
    "step_started": {"step"},
    "step_committed": {"step", "head"},
    "step_failed": {"step", "reason"},
    "wip_checkpoint": {"step", "base", "head", "dirty", "artifact_sha256", "tested_head"},
    "op_intent": {"op_id", "op_kind", "idempotency_key", "idempotent"},
    "op_attempted": {"op_id"},
    "op_applied": {"op_id", "receipt"},
    "op_verified": {"op_id", "receipt"},
    "op_failed": {"op_id", "evidence"},
    "op_unknown": {"op_id", "evidence"},
    "fence": {"previous_owner", "new_owner", "evidence"},
    "hold": {"reason"},
    "hold_released": {"hold_seq", "evidence"},
    "handback": {"to_owner", "at_step", "evidence"},
}
ENVELOPE = frozenset({"schema", "seq", "prev_sha256", "kind", "owner", "at_utc"})


class JournalError(Exception):
    """The journal refuses; ``code`` is a stable reason."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _refuse(condition: bool, code: str) -> None:
    if not condition:
        raise JournalError(code)


def _aware_utc(moment: Any) -> datetime:
    """Exactly a datetime; ONE offset read that is exactly a timedelta; no astimezone."""
    _refuse(type(moment) is datetime, "time_unknown")
    try:
        offset = moment.utcoffset()
        current = None if type(offset) is not timedelta else \
            (moment.replace(tzinfo=None) - offset).replace(tzinfo=timezone.utc)
    except Exception:  # noqa: BLE001 - a broken tzinfo is not a time
        current = None
    _refuse(current is not None, "time_unknown")
    return current


def _text(value: Any, pattern: re.Pattern = TOKEN) -> bool:
    return isinstance(value, str) and pattern.fullmatch(value) is not None


def _owner(value: Any) -> dict:
    _refuse(isinstance(value, dict) and set(value) == OWNER_KEYS and all(_text(value[key]) for key in OWNER_KEYS),
            "owner_invalid")
    return value


def _unique(pairs):
    result = {}
    for key, item in pairs:
        _refuse(key not in result, "journal_duplicate_key")
        result[key] = item
    return result


def _reject_constant(value):
    raise JournalError("journal_non_finite")


def task_key(task_id: str) -> str:
    """A filesystem-safe, collision-free key: readable prefix plus a digest of the exact id."""
    _refuse(_text(task_id), "task_id_invalid")
    readable = re.sub(r"[^A-Za-z0-9._-]", "_", task_id)[:80]
    return readable + "-" + hashlib.sha256(task_id.encode("utf-8")).hexdigest()[:16]


@contextmanager
def _exclusive_lock(path: Path) -> Iterator[None]:
    """A non-blocking exclusive OS lock, released by the OS if the process dies."""
    try:
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0), 0o600)
    except OSError:
        raise JournalError("journal_unwritable") from None
    locked = False
    try:
        try:
            if os.name == "nt":
                import msvcrt
                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            locked = True
        except OSError:
            raise JournalError("journal_locked") from None
        yield
    finally:
        if locked:
            try:
                if os.name == "nt":
                    import msvcrt
                    os.lseek(descriptor, 0, os.SEEK_SET)
                    msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
            except OSError:
                pass
        os.close(descriptor)


def _validate_fields(kind: str, fields: dict) -> None:
    """Per-kind field shapes; state rules are checked by the fold."""
    _refuse(kind in FIELDS and isinstance(fields, dict) and set(fields) == FIELDS[kind], "record_fields_invalid")
    if "step" in fields:
        _refuse(type(fields["step"]) is int and fields["step"] >= 1, "record_fields_invalid")
    if kind == "task_opened":
        _refuse(_text(fields["task_id"]) and type(fields["revision"]) is int and fields["revision"] >= 1
                and _text(fields["base_commit"], HEX40), "record_fields_invalid")
    elif kind == "step_planned":
        _refuse(isinstance(fields["title"], str) and 0 < len(fields["title"]) <= 200, "record_fields_invalid")
    elif kind == "step_committed":
        _refuse(_text(fields["head"], HEX40), "record_fields_invalid")
    elif kind in ("step_failed", "hold"):
        _refuse(isinstance(fields["reason"], str) and 0 < len(fields["reason"]) <= 500, "record_fields_invalid")
    elif kind == "wip_checkpoint":
        dirty = fields["dirty"]
        _refuse(_text(fields["base"], HEX40) and _text(fields["head"], HEX40)
                and _text(fields["artifact_sha256"], HEX64)
                and (fields["tested_head"] is None or _text(fields["tested_head"], HEX40))
                and isinstance(dirty, list) and len(dirty) <= 5000
                and all(isinstance(item, dict) and set(item) == {"path", "state"}
                        and isinstance(item["path"], str) and 0 < len(item["path"]) <= 400
                        and item["state"] in DIRTY_STATES for item in dirty), "record_fields_invalid")
    elif kind == "op_intent":
        _refuse(_text(fields["op_id"]) and _text(fields["op_kind"]) and _text(fields["idempotency_key"])
                and type(fields["idempotent"]) is bool, "record_fields_invalid")
    elif kind.startswith("op_"):
        _refuse(_text(fields["op_id"]), "record_fields_invalid")
        for key in ("receipt", "evidence"):
            if key in fields:
                _refuse(isinstance(fields[key], str) and 0 < len(fields[key]) <= 2000, "record_fields_invalid")
    elif kind == "fence":
        _owner(fields["previous_owner"])
        _owner(fields["new_owner"])
        evidence = fields["evidence"]
        # An incomplete fence is never a fence: the executor records a hold instead.
        _refuse(isinstance(evidence, dict) and set(evidence) == set(FENCE_EVIDENCE_KEYS)
                and all(evidence[key] is True for key in FENCE_EVIDENCE_KEYS), "fence_incomplete")
        _refuse(fields["new_owner"]["token"] != fields["previous_owner"]["token"], "fence_same_token")
    elif kind == "hold_released":
        _refuse(type(fields["hold_seq"]) is int and fields["hold_seq"] >= 1
                and isinstance(fields["evidence"], str) and 0 < len(fields["evidence"]) <= 2000,
                "record_fields_invalid")
    elif kind == "handback":
        _owner(fields["to_owner"])
        _refuse(type(fields["at_step"]) is int and fields["at_step"] >= 0
                and _text(fields["evidence"], HEX64), "record_fields_invalid")


class State:
    """The validated fold of a journal."""

    def __init__(self) -> None:
        self.task: dict | None = None
        self.owner: dict | None = None
        self.seq = 0
        self.last_sha256 = ZERO
        self.steps: dict[int, str] = {}  # step -> planned | started | committed | failed
        self.open_step: int | None = None
        self.wip: dict[int, dict] = {}  # step -> last wip checkpoint fields
        self.ops: dict[str, dict] = {}  # op_id -> {"state", "idempotent", "kind", "idempotency_key"}
        self.holds: dict[int, str] = {}  # hold seq -> reason (unreleased)

    def apply(self, record: dict) -> None:
        kind, fields = record["kind"], {k: v for k, v in record.items() if k not in ENVELOPE}
        _validate_fields(kind, fields)
        if self.task is None:
            _refuse(kind == "task_opened", "journal_must_open_with_the_task")
            self.task, self.owner = dict(fields), dict(record["owner"])
            return
        _refuse(kind != "task_opened", "task_reopened")
        if kind == "fence":
            # The executor writes the fence for the NEW owner; the fenced writer never can.
            _refuse(record["owner"] == fields["new_owner"], "fence_writer_invalid")
        else:
            _refuse(record["owner"] == self.owner, "writer_not_the_owner")
        if kind == "step_planned":
            _refuse(fields["step"] not in self.steps and fields["step"] == len(self.steps) + 1, "step_order_invalid")
            self.steps[fields["step"]] = "planned"
        elif kind == "step_started":
            _refuse(self.steps.get(fields["step"]) == "planned" and self.open_step is None, "step_order_invalid")
            self.steps[fields["step"]], self.open_step = "started", fields["step"]
        elif kind in ("step_committed", "step_failed"):
            _refuse(self.open_step == fields["step"], "step_order_invalid")
            _refuse(not self._open_ops(), "step_closed_with_open_operation")
            self.steps[fields["step"]] = "committed" if kind == "step_committed" else "failed"
            self.open_step = None
        elif kind == "wip_checkpoint":
            _refuse(self.open_step == fields["step"], "wip_without_open_step")
            self.wip[fields["step"]] = dict(fields)
        elif kind == "op_intent":
            _refuse(fields["op_id"] not in self.ops and self.open_step is not None, "op_intent_invalid")
            self.ops[fields["op_id"]] = {"state": "intent", "idempotent": fields["idempotent"],
                                         "op_kind": fields["op_kind"], "idempotency_key": fields["idempotency_key"]}
        elif kind.startswith("op_"):
            op = self.ops.get(fields["op_id"])
            _refuse(op is not None and kind in OP_NEXT.get(op["state"], set()), "op_transition_invalid")
            op["state"] = kind[3:]
        elif kind == "fence":
            _refuse(fields["previous_owner"] == self.owner, "fence_of_another_owner")
            self.owner = dict(fields["new_owner"])
        elif kind == "hold":
            self.holds[record["seq"]] = fields["reason"]
        elif kind == "hold_released":
            _refuse(fields["hold_seq"] in self.holds, "hold_release_invalid")
            self.holds.pop(fields["hold_seq"])
        elif kind == "handback":
            _refuse(self.open_step is None and not self._open_ops(), "handback_not_at_a_boundary")
            _refuse(fields["at_step"] == self.last_committed(), "handback_step_mismatch")
            self.owner = dict(fields["to_owner"])

    def _open_ops(self) -> list[str]:
        return sorted(op_id for op_id, op in self.ops.items() if op["state"] not in ("verified", "failed"))

    def last_committed(self) -> int:
        committed = [step for step, status in self.steps.items() if status == "committed"]
        return max(committed) if committed else 0


class TaskJournal:
    """One task's journal under an explicit root (there is no default root)."""

    def __init__(self, root: Path, task_id: str) -> None:
        self.root = Path(root)
        self.task_id = task_id
        self.path = self.root / (task_key(task_id) + ".jsonl")
        self.lock_path = self.path.with_name(self.path.name + ".lock")

    def _lines(self) -> list[bytes]:
        try:
            with open(self.path, "rb") as stream:
                raw = stream.read(MAX_LINE_BYTES * MAX_RECORDS + 1)
        except FileNotFoundError:
            return []
        except OSError:
            raise JournalError("journal_unreadable") from None
        _refuse(len(raw) <= MAX_LINE_BYTES * MAX_RECORDS, "journal_oversized")
        if not raw:
            return []
        # A crash mid-append leaves a line without its newline: never parsed, never dropped.
        _refuse(raw.endswith(b"\n"), "journal_torn_tail")
        return raw[:-1].split(b"\n")

    def replay(self) -> State:
        """The validated fold. Any structural defect raises JournalError."""
        state = State()
        for line in self._lines():
            _refuse(0 < len(line) <= MAX_LINE_BYTES, "journal_line_invalid")
            try:
                record = json.loads(line.decode("utf-8"), object_pairs_hook=_unique, parse_constant=_reject_constant)
            except (UnicodeError, ValueError, RecursionError):
                raise JournalError("journal_line_invalid") from None
            _refuse(isinstance(record, dict) and ENVELOPE <= set(record) and record["schema"] == SCHEMA,
                    "journal_line_invalid")
            _refuse(type(record["seq"]) is int and record["seq"] == state.seq + 1
                    and record["prev_sha256"] == state.last_sha256, "journal_chain_broken")
            _owner(record["owner"])
            _refuse(isinstance(record["at_utc"], str) and len(record["at_utc"]) <= 40, "journal_line_invalid")
            state.apply(record)
            if state.seq == 0:
                _refuse(record["task_id"] == self.task_id, "journal_of_another_task")
            state.seq, state.last_sha256 = record["seq"], hashlib.sha256(line).hexdigest()
        return state

    def append(self, kind: str, fields: dict, *, owner: dict, expected_seq: int, now: Any) -> dict:
        """Append one record as ``owner`` if the journal's last seq is still ``expected_seq``."""
        current = _aware_utc(now)
        _owner(owner)
        _validate_fields(kind, fields)
        self.root.mkdir(parents=True, exist_ok=True)
        with _exclusive_lock(self.lock_path):
            state = self.replay()
            _refuse(state.seq == expected_seq, "journal_cas_conflict")
            record = {"schema": SCHEMA, "seq": state.seq + 1, "prev_sha256": state.last_sha256, "kind": kind,
                      "owner": dict(owner), "at_utc": current.strftime("%Y-%m-%dT%H:%M:%S.%fZ"), **fields}
            state.apply(record)  # the same rules as replay, before anything is written
            line = json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
            _refuse(len(line) <= MAX_LINE_BYTES, "record_oversized")
            try:
                descriptor = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_BINARY", 0),
                                     0o600)
                try:
                    written = os.write(descriptor, line + b"\n")
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
            except OSError:
                raise JournalError("journal_unwritable") from None
            _refuse(written == len(line) + 1, "journal_torn_tail")
            return record

    def reconcile(self) -> dict:
        """The resume verdict for a stand-in or a hand-back. Never replays an effect."""
        try:
            state = self.replay()
        except JournalError as error:
            return {"verdict": "hold", "reasons": [error.code], "resume": None, "reverify": [], "owner": None}
        if state.task is None:
            return {"verdict": "hold", "reasons": ["journal_missing"], "resume": None, "reverify": [], "owner": None}
        reasons = ["unreleased_hold:" + str(seq) for seq in sorted(state.holds)]
        reverify = []
        for op_id in state._open_ops():
            op = state.ops[op_id]
            if op["state"] == "intent":
                continue  # never attempted: nothing left the worktree
            if op["idempotent"]:
                reverify.append({"op_id": op_id, "op_kind": op["op_kind"], "idempotency_key": op["idempotency_key"],
                                 "state": op["state"]})
            else:
                reasons.append("unknown_external_outcome:" + op_id)
        if state.open_step is not None:
            wip = state.wip.get(state.open_step)
            resume = {"step": state.open_step, "from": "wip_checkpoint", "wip": wip} if wip is not None else \
                {"step": state.open_step, "from": "last_committed_step", "last_committed": state.last_committed(),
                 "note": "no WIP checkpoint for the open step: quarantine the dirty tree, never reset it"}
        else:
            resume = {"step": state.last_committed() + 1, "from": "step_boundary",
                      "last_committed": state.last_committed()}
        verdict = "hold" if reasons else ("reverify" if reverify else "continue")
        return {"verdict": verdict, "reasons": reasons, "resume": resume, "reverify": reverify,
                "owner": dict(state.owner), "task": dict(state.task), "seq": state.seq}

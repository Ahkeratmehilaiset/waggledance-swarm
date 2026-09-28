#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""Deterministic W0/F17 switch journal: records, transitions and fold.

A journal is an append-only JSONL file of strictly versioned records. Each
operation (one switch intent) owns a hash chain of records with consecutive
``seq`` values. The first record fixes the operation binding (lane, task
revision, source/target generation, policy/intent digests, reservation,
external idempotency key, revocation version, expiry); every later record
binds to it by digest. A record is committed only when its terminating
newline is on disk: a torn tail is reported and never folded.

Semantics this module enforces, independent of any caller:

* attempted, applied, verified and continued are distinct phases;
* ``rollback_verified`` is a rolled-back outcome, never a successful switch;
* ``resume_pending`` is not continued;
* an unknown external effect must HOLD, and a HOLD leaves only through a
  ``reconciled`` record with evidence; no retry while the effect is unknown;
* identical replay of a committed record is idempotent; a different record at
  a committed position is refused (``conflicting_replay``);
* hard boundaries are module constants: no record field, argument or CLI flag
  can raise retry limits, add operation kinds, or grant authority.

This is a pure evidence journal. It never stops, starts, launches or
provisions anything, and its output grants no authority (``authority_granted``
and ``actuation_performed`` are always False).
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from types import MappingProxyType
from typing import Any


RECORD_SCHEMA = "wd.bridge-v2-switch-journal-record.v1"
BINDING_SCHEMA = "wd.bridge-v2-switch-binding.v1"
FOLD_SCHEMA = "wd.bridge-v2-switch-journal-fold.v1"

HARD_BOUNDARIES = MappingProxyType({
    "max_attempts": 3,
    "max_rollback_attempts": 3,
    "max_records_per_operation": 64,
    "max_operations": 1024,
    "max_record_bytes": 16384,
    "max_evidence_digests": 32,
    "operation_kinds": frozenset({"model_switch", "effort_switch", "stand_in", "hand_back"}),
    "grants_authority": False,
    "actuates": False,
})

_ID = re.compile(r"[a-z0-9][a-z0-9._:-]{0,127}")
_TASK_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,159}")
_DIGEST = re.compile(r"[0-9a-f]{64}")
_UTC = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?Z")

_RECORD_FIELDS = frozenset({
    "schema", "operation_id", "seq", "prev_digest", "phase", "reason",
    "recorded_at_utc", "binding", "binding_digest", "observed_revocation_version",
    "evidence_digests",
})
_BINDING_FIELDS = frozenset({
    "schema", "operation_kind", "lane", "task_id", "task_revision",
    "source_generation", "target_generation", "intent_digest", "policy_digest",
    "reservation_id", "idempotency_key", "revocation_version", "expires_at_utc",
})

REASONS = MappingProxyType({
    "intent_recorded": frozenset({"requested"}),
    "attempted": frozenset({"started", "retry"}),
    "attempt_failed": frozenset({"no_side_effect", "precondition_failed", "refused_by_target"}),
    "effect_unknown": frozenset({"timeout", "crash", "ambiguous_receipt"}),
    "applied": frozenset({"receipt_observed"}),
    "verification_failed": frozenset({"identity_mismatch", "profile_mismatch",
                                      "generation_mismatch", "timeout"}),
    "verified": frozenset({"identity_and_profile_observed"}),
    "resume_pending": frozenset({"awaiting_requester", "awaiting_checkpoint"}),
    "continued": frozenset({"task_resumed"}),
    "rollback_attempted": frozenset({"verification_failed", "revocation_observed",
                                     "operator_hold", "continuation_failed", "retry"}),
    "rollback_failed": frozenset({"no_side_effect", "refused_by_target"}),
    "rollback_effect_unknown": frozenset({"timeout", "crash", "ambiguous_receipt"}),
    "rolled_back": frozenset({"receipt_observed"}),
    "rollback_verified": frozenset({"identity_and_profile_observed"}),
    "hold": frozenset({"unknown_effect", "rollback_exhausted", "rollback_unverified"}),
    "reconciled": frozenset({"effect_applied", "effect_absent"}),
    "revoked": frozenset({"revocation_observed"}),
    "expired": frozenset({"deadline_passed"}),
    "abandoned": frozenset({"requester_withdrawn", "retry_exhausted"}),
})
PHASES = frozenset(REASONS)
TRANSITIONS = MappingProxyType({
    None: frozenset({"intent_recorded"}),
    "intent_recorded": frozenset({"attempted", "revoked", "expired", "abandoned"}),
    "attempted": frozenset({"applied", "attempt_failed", "effect_unknown"}),
    "attempt_failed": frozenset({"attempted", "revoked", "expired", "abandoned"}),
    "effect_unknown": frozenset({"hold"}),
    "applied": frozenset({"verified", "verification_failed"}),
    "verification_failed": frozenset({"rollback_attempted"}),
    "verified": frozenset({"resume_pending", "continued", "rollback_attempted"}),
    "resume_pending": frozenset({"continued", "rollback_attempted"}),
    "rollback_attempted": frozenset({"rolled_back", "rollback_failed", "rollback_effect_unknown"}),
    "rollback_failed": frozenset({"rollback_attempted", "hold"}),
    "rollback_effect_unknown": frozenset({"hold"}),
    "rolled_back": frozenset({"rollback_verified", "hold"}),
    "hold": frozenset({"reconciled"}),
    "rollback_verified": frozenset(),
    "continued": frozenset(),
    "revoked": frozenset(),
    "expired": frozenset(),
    "abandoned": frozenset(),
})
TERMINAL = frozenset(state for state, nxt in TRANSITIONS.items() if state is not None and not nxt)
# Claims about the outside world need evidence; so does any claim of absence.
EVIDENCE_REQUIRED = frozenset({
    "attempt_failed", "applied", "verification_failed", "verified", "continued",
    "rollback_failed", "rolled_back", "rollback_verified", "reconciled",
})
# Phases that start or extend a switch are refused once a newer revocation is seen.
REVOCATION_BLOCKED = frozenset({"attempted", "resume_pending", "continued"})
_HOLD_REASON_BY_STATE = MappingProxyType({
    "effect_unknown": "unknown_effect",
    "rollback_effect_unknown": "unknown_effect",
    "rollback_failed": "rollback_exhausted",
    "rolled_back": "rollback_unverified",
})
_ROLLBACK_REASON_BY_STATE = MappingProxyType({
    "verification_failed": frozenset({"verification_failed"}),
    "verified": frozenset({"revocation_observed", "operator_hold", "continuation_failed"}),
    "resume_pending": frozenset({"revocation_observed", "operator_hold", "continuation_failed"}),
    "rollback_failed": frozenset({"retry"}),
})


class ContractError(ValueError):
    """Stable machine-readable refusal."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def canonical_bytes(value: Any) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeError):
        raise ContractError("invalid_record") from None


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def _closed(value: Any, fields: frozenset[str]) -> dict[str, Any]:
    if type(value) is not dict or any(type(k) is not str for k in value):
        raise ContractError("invalid_record")
    if fields - value.keys():
        raise ContractError("missing_field")
    if value.keys() - fields:
        raise ContractError("unknown_field")
    return dict(value)


def _id(value: Any) -> str:
    if type(value) is not str or not _ID.fullmatch(value):
        raise ContractError("invalid_identifier")
    return value


def _digest(value: Any, *, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    if type(value) is not str or not _DIGEST.fullmatch(value):
        raise ContractError("invalid_digest")
    return value


def _int(value: Any, low: int, high: int) -> int:
    if type(value) is not int or not low <= value <= high:
        raise ContractError("invalid_number")
    return value


def _utc(value: Any) -> datetime:
    if type(value) is not str or not _UTC.fullmatch(value):
        raise ContractError("invalid_utc")
    try:
        return datetime.fromisoformat(value[:-1] + "+00:00").astimezone(timezone.utc)
    except ValueError:
        raise ContractError("invalid_utc") from None


def parse_binding(raw: Any) -> dict[str, Any]:
    row = _closed(raw, _BINDING_FIELDS)
    if type(row["schema"]) is not str or row["schema"] != BINDING_SCHEMA:
        raise ContractError("invalid_schema")
    if type(row["operation_kind"]) is not str or row["operation_kind"] not in HARD_BOUNDARIES["operation_kinds"]:
        raise ContractError("invalid_operation_kind")
    for key in ("lane", "source_generation", "target_generation", "reservation_id", "idempotency_key"):
        _id(row[key])
    if type(row["task_id"]) is not str or not _TASK_ID.fullmatch(row["task_id"]) or any(
            part in ("", ".", "..") for part in row["task_id"].split("/")):
        raise ContractError("invalid_task_id")
    if row["source_generation"] == row["target_generation"]:
        raise ContractError("same_generation")
    _int(row["task_revision"], 0, 2 ** 53)
    _int(row["revocation_version"], 0, 2 ** 53)
    for key in ("intent_digest", "policy_digest"):
        _digest(row[key])
    _utc(row["expires_at_utc"])
    return row


def parse_record(raw: Any) -> dict[str, Any]:
    """Validate one record's shape; transitions are checked by Journal.append."""
    row = _closed(raw, _RECORD_FIELDS)
    if type(row["schema"]) is not str or row["schema"] != RECORD_SCHEMA:
        raise ContractError("invalid_schema")
    _id(row["operation_id"])
    _int(row["seq"], 1, HARD_BOUNDARIES["max_records_per_operation"])
    if type(row["phase"]) is not str or row["phase"] not in PHASES:
        raise ContractError("invalid_phase")
    if type(row["reason"]) is not str or row["reason"] not in REASONS[row["phase"]]:
        raise ContractError("invalid_reason")
    _utc(row["recorded_at_utc"])
    _int(row["observed_revocation_version"], 0, 2 ** 53)
    evidence = row["evidence_digests"]
    if type(evidence) is not list or len(evidence) > HARD_BOUNDARIES["max_evidence_digests"]:
        raise ContractError("invalid_evidence")
    for item in evidence:
        _digest(item)
    if len(set(evidence)) != len(evidence):
        raise ContractError("invalid_evidence")
    if row["phase"] in EVIDENCE_REQUIRED and not evidence:
        raise ContractError("evidence_required")
    if row["seq"] == 1:
        if row["phase"] != "intent_recorded" or row["prev_digest"] is not None:
            raise ContractError("invalid_transition")
        binding = parse_binding(row["binding"])
        if _digest(row["binding_digest"]) != digest(binding):
            raise ContractError("binding_mismatch")
    else:
        if row["phase"] == "intent_recorded":
            raise ContractError("invalid_transition")
        if row["binding"] is not None:
            raise ContractError("binding_only_on_intent")
        _digest(row["prev_digest"])
        _digest(row["binding_digest"])
    if len(canonical_bytes(row)) > HARD_BOUNDARIES["max_record_bytes"]:
        raise ContractError("record_too_large")
    return row


class _Operation:
    def __init__(self, record: dict[str, Any]):
        self.binding = record["binding"]
        self.binding_digest = record["binding_digest"]
        self.expires_at = _utc(self.binding["expires_at_utc"])
        self.records: list[dict[str, Any]] = []
        self.digests: list[str] = []
        self.state: str | None = None
        self.attempts = 0
        self.rollback_attempts = 0
        self.held_track: str | None = None
        self.revocation_seen = self.binding["revocation_version"]
        self.ever_applied = False
        self.ever_verified = False
        self.ever_unknown = False

    def allowed(self) -> list[str]:
        if self.state in TERMINAL:
            return []
        out = []
        for phase in sorted(TRANSITIONS[self.state]):
            try:
                self._check(phase, self._default_reason(phase), None, at=_utc(self.records[-1]["recorded_at_utc"]))
            except ContractError:
                continue
            out.append(phase)
        return out

    def _default_reason(self, phase: str) -> str:
        if phase == "attempted":
            return "started" if self.attempts == 0 else "retry"
        if phase == "hold":
            return _HOLD_REASON_BY_STATE.get(self.state, "unknown_effect")
        if phase == "rollback_attempted":
            return sorted(_ROLLBACK_REASON_BY_STATE.get(self.state, {"retry"}))[0]
        if phase == "abandoned":
            return "requester_withdrawn"
        return sorted(REASONS[phase])[0]

    def _check(self, phase: str, reason: str, record: dict[str, Any] | None, *, at: datetime) -> None:
        if self.state in TERMINAL:
            raise ContractError("operation_terminal")
        if phase not in TRANSITIONS[self.state]:
            if self.state in ("effect_unknown", "rollback_effect_unknown", "hold") and phase in (
                    "attempted", "rollback_attempted", "applied", "rolled_back", "abandoned", "revoked"):
                raise ContractError("unknown_effect_requires_reconcile")
            raise ContractError("invalid_transition")
        revoked_newer = self.revocation_seen > self.binding["revocation_version"]
        if record is not None and record["observed_revocation_version"] > self.binding["revocation_version"]:
            revoked_newer = True
        if phase in REVOCATION_BLOCKED and revoked_newer:
            raise ContractError("revocation_blocks_phase")
        if phase == "revoked" and not revoked_newer:
            raise ContractError("revocation_not_observed")
        if phase == "attempted":
            if self.attempts >= HARD_BOUNDARIES["max_attempts"]:
                raise ContractError("retry_exhausted")
            if reason != ("started" if self.attempts == 0 else "retry"):
                raise ContractError("invalid_reason")
            if at > self.expires_at:
                raise ContractError("intent_expired")
        if phase == "expired" and record is not None and _utc(record["recorded_at_utc"]) <= self.expires_at:
            raise ContractError("not_expired")
        if phase == "abandoned" and reason == "retry_exhausted" and (
                self.state != "attempt_failed" or self.attempts < HARD_BOUNDARIES["max_attempts"]):
            raise ContractError("invalid_reason")
        if phase == "rollback_attempted":
            if self.rollback_attempts >= HARD_BOUNDARIES["max_rollback_attempts"]:
                raise ContractError("retry_exhausted")
            if reason not in _ROLLBACK_REASON_BY_STATE[self.state]:
                raise ContractError("invalid_reason")
            if reason == "revocation_observed" and not revoked_newer:
                raise ContractError("revocation_not_observed")
        if phase == "hold":
            if reason != _HOLD_REASON_BY_STATE[self.state]:
                raise ContractError("invalid_reason")
            if self.state == "rollback_failed" and self.rollback_attempts < HARD_BOUNDARIES["max_rollback_attempts"]:
                raise ContractError("rollback_not_exhausted")

    def apply(self, record: dict[str, Any], record_digest: str) -> None:
        phase, reason = record["phase"], record["reason"]
        if record["binding_digest"] != self.binding_digest:
            raise ContractError("binding_mismatch")
        if record["seq"] > 1:
            if record["prev_digest"] != self.digests[-1]:
                raise ContractError("prev_digest_mismatch")
            if _utc(record["recorded_at_utc"]) < _utc(self.records[-1]["recorded_at_utc"]):
                raise ContractError("time_regression")
            if record["observed_revocation_version"] < self.revocation_seen:
                raise ContractError("revocation_regression")
        if record["observed_revocation_version"] < self.binding["revocation_version"]:
            raise ContractError("revocation_regression")
        if phase != "intent_recorded":
            self._check(phase, reason, record, at=_utc(record["recorded_at_utc"]))
        # Commit.
        self.records.append(record)
        self.digests.append(record_digest)
        self.revocation_seen = max(self.revocation_seen, record["observed_revocation_version"])
        previous = self.state
        if phase == "attempted":
            self.attempts += 1
        elif phase == "rollback_attempted":
            self.rollback_attempts += 1
        elif phase == "applied":
            self.ever_applied = True
        elif phase == "verified":
            self.ever_verified = True
        elif phase in ("effect_unknown", "rollback_effect_unknown"):
            self.ever_unknown = True
        if phase == "hold":
            self.held_track = "forward" if previous == "effect_unknown" else "rollback"
            self.state = "hold"
        elif phase == "reconciled":
            if self.held_track == "forward":
                self.state = "applied" if reason == "effect_applied" else "attempt_failed"
                if reason == "effect_applied":
                    self.ever_applied = True
            else:
                self.state = "rolled_back" if reason == "effect_applied" else "rollback_failed"
            self.held_track = None
        else:
            self.state = phase

    def summary(self, operation_id: str) -> dict[str, Any]:
        state = self.state
        rolled = state in ("rolled_back", "rollback_verified") or (
            state == "hold" and self.held_track == "rollback") or state in (
            "rollback_attempted", "rollback_failed", "rollback_effect_unknown")
        return {
            "operation_id": operation_id,
            "binding_digest": self.binding_digest,
            "lane": self.binding["lane"],
            "task_id": self.binding["task_id"],
            "task_revision": self.binding["task_revision"],
            "state": state,
            "last_seq": len(self.records),
            "last_digest": self.digests[-1],
            "attempts": self.attempts,
            "rollback_attempts": self.rollback_attempts,
            "attempted": self.attempts > 0,
            "applied": self.ever_applied and not rolled,
            "verified": self.ever_verified and not rolled,
            "continued": state == "continued",
            "resume_pending": state == "resume_pending",
            "rolled_back": rolled,
            "rollback_verified": state == "rollback_verified",
            "success": state == "continued",
            "hold_required": state in ("effect_unknown", "rollback_effect_unknown", "hold"),
            "unknown_effect_seen": self.ever_unknown,
            "revocation_observed": self.revocation_seen > self.binding["revocation_version"],
            "terminal": state in TERMINAL,
            "next_phases": self.allowed(),
        }


class Journal:
    """In-memory fold of committed records; append validates before committing."""

    def __init__(self) -> None:
        self._ops: dict[str, _Operation] = {}
        self.record_count = 0

    def append(self, raw: Any) -> str:
        """Return 'appended' or 'duplicate'; raise ContractError on refusal."""
        record = parse_record(raw)
        record_digest = digest(record)
        op = self._ops.get(record["operation_id"])
        if op is not None and record["seq"] <= len(op.records):
            if op.digests[record["seq"] - 1] == record_digest:
                return "duplicate"
            raise ContractError("conflicting_replay")
        if op is None:
            if record["seq"] != 1:
                raise ContractError("seq_gap")
            if len(self._ops) >= HARD_BOUNDARIES["max_operations"]:
                raise ContractError("too_many_operations")
            op = _Operation(record)
            op.apply(record, record_digest)
            self._ops[record["operation_id"]] = op
        else:
            if record["seq"] != len(op.records) + 1:
                raise ContractError("seq_gap")
            op.apply(record, record_digest)
        self.record_count += 1
        return "appended"

    def fold(self) -> dict[str, Any]:
        operations = [self._ops[k].summary(k) for k in sorted(self._ops)]
        return {
            "schema": FOLD_SCHEMA,
            "record_count": self.record_count,
            "operation_count": len(operations),
            "operations": operations,
            "authority_granted": False,
            "actuation_performed": False,
        }

    def operation(self, operation_id: str) -> dict[str, Any]:
        op = self._ops.get(operation_id)
        if op is None:
            raise ContractError("unknown_operation")
        return op.summary(operation_id)


def next_record(journal: Journal, operation_id: str, phase: str, reason: str, recorded_at_utc: str,
                evidence_digests: list[str], observed_revocation_version: int | None = None) -> dict[str, Any]:
    """Build the chained record that would follow ``operation_id`` (not appended)."""
    op = journal._ops.get(operation_id)
    if op is None:
        raise ContractError("unknown_operation")
    return {
        "schema": RECORD_SCHEMA, "operation_id": operation_id, "seq": len(op.records) + 1,
        "prev_digest": op.digests[-1], "phase": phase, "reason": reason,
        "recorded_at_utc": recorded_at_utc, "binding": None, "binding_digest": op.binding_digest,
        "observed_revocation_version": (op.revocation_seen if observed_revocation_version is None
                                        else observed_revocation_version),
        "evidence_digests": list(evidence_digests),
    }


def intent_record(operation_id: str, binding: dict[str, Any], recorded_at_utc: str) -> dict[str, Any]:
    return {
        "schema": RECORD_SCHEMA, "operation_id": operation_id, "seq": 1, "prev_digest": None,
        "phase": "intent_recorded", "reason": "requested", "recorded_at_utc": recorded_at_utc,
        "binding": binding, "binding_digest": digest(parse_binding(binding)),
        "observed_revocation_version": binding["revocation_version"] if type(binding) is dict else 0,
        "evidence_digests": [],
    }


# --- file journal -----------------------------------------------------------------------

def _refuse_live_path(path: Path) -> None:
    parts = {part.casefold() for part in path.resolve().parts}
    if ".agent-bridge" in parts:
        raise ContractError("live_runtime_path_refused")


def read_journal(path: Path) -> tuple[Journal, int, int]:
    """Fold a JSONL journal. Returns (journal, committed_bytes, torn_tail_bytes).

    A record is committed only by its terminating newline; a torn tail is
    never folded. Every committed line must be a canonical record.
    """
    data = path.read_bytes() if path.exists() else b""
    committed = data.rfind(b"\n") + 1
    journal = Journal()
    for line in data[:committed].split(b"\n")[:-1]:
        try:
            raw = json.loads(line.decode("utf-8"), parse_constant=_reject_constant)
        except (UnicodeDecodeError, ValueError):
            raise ContractError("invalid_journal_line") from None
        if canonical_bytes(raw) != line:
            raise ContractError("noncanonical_journal_line")
        journal.append(raw)
    return journal, committed, len(data) - committed


def _reject_constant(_: str) -> Any:
    raise ValueError("non-finite number")


def append_file(path: Path, raw: Any) -> tuple[str, Journal]:
    """Validate against the committed journal, then append one canonical line.

    Refuses while a torn tail exists (repair first) and if the file changed
    between validation and write. A duplicate is not written again.
    """
    _refuse_live_path(path)
    journal, committed, torn = read_journal(path)
    if torn:
        raise ContractError("torn_tail_requires_repair")
    result = journal.append(raw)
    if result == "duplicate":
        return result, journal
    line = canonical_bytes(parse_record(raw)) + b"\n"
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_BINARY", 0), 0o600)
    try:
        if os.fstat(fd).st_size != committed:
            raise ContractError("concurrent_modification")
        written = os.write(fd, line)
        if written != len(line):
            raise ContractError("short_write")
        os.fsync(fd)
    finally:
        os.close(fd)
    return result, journal


def repair_file(path: Path) -> int:
    """Drop an uncommitted torn tail after proving the committed prefix is valid."""
    _refuse_live_path(path)
    _, committed, torn = read_journal(path)
    if not torn:
        raise ContractError("no_torn_tail")
    with open(path, "r+b") as handle:
        handle.truncate(committed)
        handle.flush()
        os.fsync(handle.fileno())
    return torn


def api_contract() -> dict[str, Any]:
    return {
        "record_schema": RECORD_SCHEMA, "binding_schema": BINDING_SCHEMA, "fold_schema": FOLD_SCHEMA,
        "record_fields": sorted(_RECORD_FIELDS), "binding_fields": sorted(_BINDING_FIELDS),
        "reasons": {phase: sorted(reasons) for phase, reasons in sorted(REASONS.items())},
        "transitions": {str(state): sorted(nxt) for state, nxt in TRANSITIONS.items()},
        "terminal": sorted(TERMINAL), "evidence_required": sorted(EVIDENCE_REQUIRED),
        "revocation_blocked": sorted(REVOCATION_BLOCKED),
        "hard_boundaries": {k: (sorted(v) if isinstance(v, frozenset) else v)
                            for k, v in HARD_BOUNDARIES.items()},
        "commit_rule": "a record is committed only by its terminating newline; torn tails are never folded",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("fold", "repair"):
        p = sub.add_parser(name)
        p.add_argument("--journal", required=True, type=Path)
    p = sub.add_parser("append")
    p.add_argument("--journal", required=True, type=Path)
    p.add_argument("--record-file", required=True, type=Path)
    sub.add_parser("contract")
    args = parser.parse_args(argv)
    try:
        if args.command == "contract":
            out: dict[str, Any] = api_contract()
        elif args.command == "fold":
            journal, _, torn = read_journal(args.journal)
            out = dict(journal.fold(), torn_tail_bytes=torn)
        elif args.command == "repair":
            out = {"removed_torn_bytes": repair_file(args.journal)}
        else:
            raw = json.loads(args.record_file.read_text(encoding="utf-8"), parse_constant=_reject_constant)
            result, journal = append_file(args.journal, raw)
            out = dict(journal.fold(), result=result)
    except ContractError as exc:
        print(json.dumps({"error": exc.code}, sort_keys=True))
        return 2
    except (OSError, ValueError):
        print(json.dumps({"error": "io_or_parse_error"}, sort_keys=True))
        return 2
    print(json.dumps(out, sort_keys=True))
    return 3 if out.get("torn_tail_bytes") else 0


if __name__ == "__main__":
    sys.exit(main())

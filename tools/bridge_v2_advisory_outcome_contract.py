#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""Pure W0 join of advisory attempts and independently reported outcomes.

An attempt_id identifies one consultation attempt, including failures and
skips. Digests are equality bindings, never proof of source authenticity.
Reported evaluator independence is not authenticated here. Set digests bind
only the supplied validated unique rows, not completeness or provenance.
Numeric values retain their JSON spelling: 5 and 5.0 conflict on replay.
The local task-ID evidence domain is max 160 characters and differs from
other bridge validators; it is not an authoritative shared task contract.
``correctness_counts`` covers completed attempts only; non-completed claims
are reported separately and never qualify success. No matched or paired
held-out comparison protocol, preregistered policy, or independent
scoring verifier is available in this W0 module; incremental benefit remains
unknown and no output grants learning, qualification, spend, or dispatch.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
import hashlib
import json
import math
import re
from typing import Any


ATTEMPT_SCHEMA = "wd.bridge-v2-advisory-attempt.v1"
OUTCOME_SCHEMA = "wd.bridge-v2-advisory-outcome.v1"
JOIN_SCHEMA = "wd.bridge-v2-advisory-join.v1"
MAX_ATTEMPTS = 4096
MAX_OUTCOMES = 8192
MAX_SUGGESTIONS = 128
MAX_PROVENANCE_REFS = 32
MAX_COST_UNITS = 10**15
MAX_LATENCY_MS = 10**9
_ID = re.compile(r"[A-Za-z0-9._:-]{1,128}")
_TASK_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,159}")
_DIGEST = re.compile(r"[0-9a-f]{64}")
_UTC = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?Z")
_ATTEMPT_FIELDS = frozenset({
    "schema", "attempt_id", "request_id", "prompt_digest", "task_id",
    "requester_agent", "consumer_agent",
    "task_class", "artifact_digest", "artifact_version", "advisor_profile",
    "attempt_status", "suggestion_ids", "observed_profile", "observed_model",
    "observed_cost_units", "latency_ms", "observed_at_utc", "provenance_refs",
})
_OUTCOME_FIELDS = frozenset({
    "schema", "outcome_id", "attempt_id", "request_id", "prompt_digest",
    "task_id", "requester_agent", "consumer_agent", "task_class",
    "artifact_digest", "artifact_version",
    "observed_profile", "suggestion_id", "disposition", "correctness", "evaluator_id",
    "scoring_evidence_digest", "changed_artifact_digest", "judged_at_utc",
    "provenance_refs",
})
_BINDING = ("attempt_id", "request_id", "prompt_digest", "task_id",
            "requester_agent", "consumer_agent",
            "task_class", "artifact_digest", "artifact_version", "observed_profile")
_STATUSES = ("completed", "failed", "skipped", "timeout", "unknown")
_DISPOSITIONS = ("used", "rejected", "unused", "unknown")
_CORRECTNESS = ("correct", "incorrect", "unknown")


class ContractError(ValueError):
    """Stable machine-readable refusal of malformed or conflicting evidence."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _object(value: Any, fields: frozenset[str]) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or any(not isinstance(k, str) for k in value):
        raise ContractError("invalid_object")
    if fields - value.keys():
        raise ContractError("missing_field")
    if value.keys() - fields:
        raise ContractError("unknown_field")
    return value


def _id(value: Any) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise ContractError("invalid_identifier")
    return value


def _task_id(value: Any) -> str:
    if (not isinstance(value, str) or not _TASK_ID.fullmatch(value)
            or any(segment in ("", ".", "..") for segment in value.split("/"))):
        raise ContractError("invalid_task_id")
    return value


def _digest(value: Any, *, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str) or not _DIGEST.fullmatch(value):
        raise ContractError("invalid_digest")
    return value


def _utc(value: Any) -> datetime:
    if not isinstance(value, str) or not _UTC.fullmatch(value):
        raise ContractError("invalid_utc")
    try:
        return datetime.fromisoformat(value[:-1] + "+00:00").astimezone(timezone.utc)
    except ValueError:
        raise ContractError("invalid_utc") from None


def _number(value: Any, maximum: int) -> int | float | None:
    if value is None:
        return None
    if type(value) not in (int, float):  # bool is not a measured number
        raise ContractError("invalid_number")
    # Bound integers before float conversion: math.isfinite(10**5000) raises.
    if type(value) is int:
        if not 0 <= value <= maximum:
            raise ContractError("invalid_number")
        return value
    if not math.isfinite(value) or value < 0 or value > maximum:
        raise ContractError("invalid_number")
    return value


def _refs(value: Any) -> list[str]:
    if not isinstance(value, list) or len(value) > MAX_PROVENANCE_REFS:
        raise ContractError("invalid_provenance")
    if any(not isinstance(item, str) or not _DIGEST.fullmatch(item) for item in value):
        raise ContractError("invalid_provenance")
    if len(value) != len(set(value)):
        raise ContractError("invalid_provenance")
    return list(value)


def _canonical_digest(value: Any) -> str:
    try:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeError):
        raise ContractError("invalid_object") from None
    return hashlib.sha256(encoded).hexdigest()


def _set_digest(kind: str, row_digests: list[str]) -> str:
    # Domain-separated, order/replay-invariant binding of unique validated rows.
    return _canonical_digest({"schema": "wd.bridge-v2-advisory-input-set.v1",
                              "kind": kind, "count": len(row_digests),
                              "row_digests": sorted(row_digests)})


def parse_attempt(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Validate one consultation attempt without inventing missing metrics."""
    row = _object(raw, _ATTEMPT_FIELDS)
    if row["schema"] != ATTEMPT_SCHEMA:
        raise ContractError("invalid_schema")
    result = dict(row)
    for key in ("attempt_id", "request_id", "task_class",
                "requester_agent", "consumer_agent",
                "artifact_version", "advisor_profile"):
        result[key] = _id(row[key])
    result["task_id"] = _task_id(row["task_id"])
    for key in ("prompt_digest", "artifact_digest"):
        result[key] = _digest(row[key])
    if row["attempt_status"] not in _STATUSES:
        raise ContractError("invalid_status")
    suggestions = row["suggestion_ids"]
    if not isinstance(suggestions, list) or len(suggestions) > MAX_SUGGESTIONS:
        raise ContractError("invalid_suggestions")
    result["suggestion_ids"] = [_id(item) for item in suggestions]
    if len(result["suggestion_ids"]) != len(set(result["suggestion_ids"])):
        raise ContractError("duplicate_suggestion")
    if len(result["suggestion_ids"]) != len({item.casefold() for item in result["suggestion_ids"]}):
        raise ContractError("suggestion_id_casefold_collision")
    result["observed_profile"] = (None if row["observed_profile"] is None
                                  else _id(row["observed_profile"]))
    result["observed_model"] = (None if row["observed_model"] is None
                                else _id(row["observed_model"]))
    result["observed_cost_units"] = _number(row["observed_cost_units"], MAX_COST_UNITS)
    result["latency_ms"] = _number(row["latency_ms"], MAX_LATENCY_MS)
    _utc(row["observed_at_utc"])
    result["provenance_refs"] = _refs(row["provenance_refs"])
    return result


def parse_outcome(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a reported suggestion outcome, not its source authenticity."""
    row = _object(raw, _OUTCOME_FIELDS)
    if row["schema"] != OUTCOME_SCHEMA:
        raise ContractError("invalid_schema")
    result = dict(row)
    for key in ("outcome_id", "attempt_id", "request_id",
                "requester_agent", "consumer_agent",
                "task_class", "artifact_version", "suggestion_id"):
        result[key] = _id(row[key])
    result["task_id"] = _task_id(row["task_id"])
    for key in ("prompt_digest", "artifact_digest"):
        result[key] = _digest(row[key])
    result["observed_profile"] = (None if row["observed_profile"] is None
                                  else _id(row["observed_profile"]))
    if row["disposition"] not in _DISPOSITIONS:
        raise ContractError("invalid_disposition")
    if row["correctness"] not in _CORRECTNESS:
        raise ContractError("invalid_correctness")
    result["evaluator_id"] = (None if row["evaluator_id"] is None
                              else _id(row["evaluator_id"]))
    result["scoring_evidence_digest"] = _digest(row["scoring_evidence_digest"], nullable=True)
    result["changed_artifact_digest"] = _digest(row["changed_artifact_digest"], nullable=True)
    if row["correctness"] != "unknown" and (
            result["evaluator_id"] is None or result["scoring_evidence_digest"] is None):
        raise ContractError("missing_independent_evidence")
    _utc(row["judged_at_utc"])
    result["provenance_refs"] = _refs(row["provenance_refs"])
    return result


def join_outcomes(attempts: list[Mapping[str, Any]],
                  outcomes: list[Mapping[str, Any]]) -> dict[str, Any]:
    """Idempotently join records while refusing conflicting or foreign claims.

    The denominator includes every distinct attempt, even failed/skipped and
    those with no outcome. No baseline/held-out comparison protocol is defined
    here, so all incremental benefit and learning authority are refused.
    """
    if not isinstance(attempts, list) or len(attempts) > MAX_ATTEMPTS:
        raise ContractError("invalid_attempts")
    if not isinstance(outcomes, list) or len(outcomes) > MAX_OUTCOMES:
        raise ContractError("invalid_outcomes")
    by_attempt: dict[str, tuple[dict[str, Any], str]] = {}
    attempt_casefold_ids: dict[str, str] = {}
    for raw in attempts:
        row = parse_attempt(raw)
        digest = _canonical_digest(row)
        folded = row["attempt_id"].casefold()
        if folded in attempt_casefold_ids and attempt_casefold_ids[folded] != row["attempt_id"]:
            raise ContractError("attempt_id_casefold_collision")
        attempt_casefold_ids[folded] = row["attempt_id"]
        prior = by_attempt.get(row["attempt_id"])
        if prior is not None and prior[1] != digest:
            raise ContractError("conflicting_attempt")
        by_attempt[row["attempt_id"]] = (row, digest)
    by_outcome: dict[str, tuple[dict[str, Any], str]] = {}
    outcome_casefold_ids: dict[str, str] = {}
    for raw in outcomes:
        row = parse_outcome(raw)
        digest = _canonical_digest(row)
        folded = row["outcome_id"].casefold()
        if folded in outcome_casefold_ids and outcome_casefold_ids[folded] != row["outcome_id"]:
            raise ContractError("outcome_id_casefold_collision")
        outcome_casefold_ids[folded] = row["outcome_id"]
        prior = by_outcome.get(row["outcome_id"])
        if prior is not None and prior[1] != digest:
            raise ContractError("conflicting_outcome")
        by_outcome[row["outcome_id"]] = (row, digest)
    by_suggestion: dict[tuple[str, str], tuple[dict[str, Any], str]] = {}
    for row, digest in by_outcome.values():
        matching = by_attempt.get(row["attempt_id"])
        if matching is None:
            raise ContractError("unknown_attempt")
        attempt = matching[0]
        if any(row[key] != attempt[key] for key in _BINDING):
            raise ContractError("foreign_binding")
        if row["suggestion_id"] not in attempt["suggestion_ids"]:
            raise ContractError("unknown_suggestion")
        if _utc(row["judged_at_utc"]) < _utc(attempt["observed_at_utc"]):
            raise ContractError("invalid_time_order")
        if (row["evaluator_id"] is not None
                and any(row["evaluator_id"].casefold() == profile.casefold()
                        for profile in (attempt["requester_agent"], attempt["consumer_agent"],
                                        attempt["advisor_profile"], attempt["observed_profile"],
                                        attempt["observed_model"])
                        if profile is not None)):
            raise ContractError("self_evaluation")
        if row["disposition"] == "used" and attempt["attempt_status"] != "completed":
            raise ContractError("invalid_disposition_for_attempt")
        key = (row["attempt_id"], row["suggestion_id"])
        if key in by_suggestion:
            raise ContractError("duplicate_suggestion_outcome")
        by_suggestion[key] = (row, digest)
    status_counts = {status: 0 for status in _STATUSES}
    disposition_counts = {disposition: 0 for disposition in _DISPOSITIONS}
    correctness_counts = {correctness: 0 for correctness in _CORRECTNESS}
    noncompleted_correctness_counts = {correctness: 0 for correctness in _CORRECTNESS}
    joined = []
    for attempt_id in sorted(by_attempt):
        row, digest = by_attempt[attempt_id]
        status_counts[row["attempt_status"]] += 1
        suggestions = []
        for suggestion_id in row["suggestion_ids"]:
            matched = by_suggestion.get((attempt_id, suggestion_id))
            if matched is None:
                suggestion = {
                    "suggestion_id": suggestion_id, "disposition": "unknown",
                    "correctness": "unknown", "reason": "missing_outcome",
                    "evidence_state": "none", "outcome_digest": None,
                    "independence_verified": False,
                    "evaluator_id": None, "scoring_evidence_digest": None,
                    "changed_artifact_digest": None,
                }
            else:
                outcome, outcome_digest = matched
                suggestion = {
                    "suggestion_id": suggestion_id,
                    "disposition": outcome["disposition"],
                    "correctness": outcome["correctness"], "reason": "joined",
                    "evidence_state": ("reported_evaluator_independence_unverified"
                                       if outcome["correctness"] != "unknown"
                                       else "no_correctness_claim"),
                    "independence_verified": False,
                    "outcome_digest": outcome_digest,
                    "evaluator_id": outcome["evaluator_id"],
                    "scoring_evidence_digest": outcome["scoring_evidence_digest"],
                    "changed_artifact_digest": outcome["changed_artifact_digest"],
                }
            disposition_counts[suggestion["disposition"]] += 1
            target_counts = (correctness_counts if row["attempt_status"] == "completed"
                             else noncompleted_correctness_counts)
            target_counts[suggestion["correctness"]] += 1
            suggestions.append(suggestion)
        joined.append({**row, "attempt_digest": digest, "suggestions": suggestions})
    return {
        "schema": JOIN_SCHEMA, "attempt_count": len(by_attempt),
        "outcome_count": len(by_outcome), "denominator_attempts": len(by_attempt),
        "attempt_set_digest": _set_digest("attempt", [digest for _, digest in by_attempt.values()]),
        "outcome_set_digest": _set_digest("outcome", [digest for _, digest in by_outcome.values()]),
        "input_set_completeness_authenticated": False,
        "independence_verified": False,
        "attempt_status_counts": status_counts,
        "disposition_counts": disposition_counts,
        "correctness_counts": correctness_counts,
        "noncompleted_correctness_counts": noncompleted_correctness_counts,
        "attempts": joined,
        "benefit": {"state": "unknown", "reason": "comparison_protocol_unimplemented",
                    "attribution": "not_established"},
        "qualification_allowed": False, "learning_update_allowed": False,
        "spend_allowed": False, "dispatch_allowed": False,
    }

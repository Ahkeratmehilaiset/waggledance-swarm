#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""Pure, dormant accounting for observed usage and accepted work.

This module deliberately does not choose a model, rank a provider, dispatch
work, write a store, or infer money that was not observed.  It turns an
explicit, bounded JSON document into a deterministic report that keeps two
identities separate:

* a usage attempt is identified by ``provider/session/turn/attempt``;
* an acceptance receipt is identified by
  ``contract/revision/artifact/evaluation``.

Those identities must not be joined just because they happen to occur in the
same input file.  In particular, reopening a contract revision removes it from
the *active* acceptance view without rewriting its historical receipt.

Usage counters are cumulative observations.  A delta is reportable only when
an explicit baseline is present and no reset is observed.  Missing baselines
and counter resets make aggregate coverage partial, rather than becoming zero
usage or a guessed delta.

The module is intentionally offline and standard-library-only.  The CLI reads
one document and writes a report to stdout; no ledger is persisted here.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence


INPUT_SCHEMA = "wd.work-ledger-input.v1"
REPORT_SCHEMA = "wd.work-ledger-report.v1"
DEFAULT_MAX_INPUT_BYTES = 1_048_576
DEFAULT_MAX_RECORDS = 10_000
_FLOAT_LIMIT = 1.7976931348623157e308


class InputError(ValueError):
    """Raised when an input cannot support an honest ledger report."""


def _reject_duplicate_keys(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name, value in pairs:
        if name in result:
            raise InputError("duplicate JSON key")
        result[name] = value
    return result


def _reject_constant(name: str) -> Any:
    raise InputError(f"non-finite JSON constant is not accepted: {name}")


def _reject_float(text: str) -> float:
    value = float(text)
    if not math.isfinite(value):
        raise InputError("non-finite numeric literal is not accepted")
    return value


def _reject_int(text: str) -> int:
    value = int(text)
    if abs(value) > _FLOAT_LIMIT:
        raise InputError("integer literal is too large to be used as a number")
    return value


def _assert_finite(value: Any, depth: int = 0) -> None:
    if depth > 50:
        raise InputError("input nesting is too deep")
    if isinstance(value, bool):
        return
    if isinstance(value, float) and not math.isfinite(value):
        raise InputError("non-finite number is not accepted")
    if isinstance(value, int) and abs(value) > _FLOAT_LIMIT:
        raise InputError("integer value is too large to be used as a number")
    if isinstance(value, Mapping):
        for item in value.values():
            _assert_finite(item, depth + 1)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _assert_finite(item, depth + 1)


def strict_loads(text: str) -> Any:
    """Load JSON while rejecting duplicate, non-finite and overflow values."""
    try:
        parsed = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_constant,
            parse_float=_reject_float,
            parse_int=_reject_int,
        )
    except json.JSONDecodeError as exc:
        raise InputError("invalid JSON input") from exc
    _assert_finite(parsed)
    return parsed


def load_document(path: Path, *, max_bytes: int = DEFAULT_MAX_INPUT_BYTES) -> Mapping[str, Any]:
    """Read one bounded JSON document without creating files or directories."""
    if max_bytes <= 0:
        raise InputError("max_bytes must be positive")
    try:
        with path.open("rb") as source:
            payload = source.read(max_bytes + 1)
    except OSError as exc:
        raise InputError("input cannot be read") from exc
    if len(payload) > max_bytes:
        raise InputError("input exceeds max_bytes")
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise InputError("input is not UTF-8") from exc
    parsed = strict_loads(text)
    if not isinstance(parsed, Mapping):
        raise InputError("ledger input must be a JSON object")
    return parsed


def _string(record: Mapping[str, Any], field: str, *, context: str) -> str:
    value = record.get(field)
    if not isinstance(value, str) or not value.strip():
        raise InputError(f"{context}.{field} must be a non-empty string")
    return value.strip()


def _non_negative_int(value: Any, *, context: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise InputError(f"{context} must be a non-negative integer")
    if value > _FLOAT_LIMIT:
        raise InputError(f"{context} is too large")
    return value


def _timestamp(value: Any, *, context: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise InputError(f"{context} must be an ISO timestamp with timezone")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise InputError(f"{context} is not an ISO timestamp") from exc
    if parsed.tzinfo is None:
        raise InputError(f"{context} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _check_keys(record: Mapping[str, Any], *, allowed: set[str], context: str) -> None:
    unknown = sorted(set(record) - allowed)
    if unknown:
        raise InputError(f"{context} has unsupported fields")


def _money(value: Any, *, context: str) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise InputError(f"{context} must be an object")
    _check_keys(value, allowed={"amount", "currency"}, context=context)
    amount = value.get("amount")
    if isinstance(amount, bool) or not isinstance(amount, (int, float)):
        raise InputError(f"{context}.amount must be a finite non-negative number")
    try:
        amount_value = float(amount)
    except (OverflowError, ValueError) as exc:
        raise InputError(f"{context}.amount must be finite") from exc
    if not math.isfinite(amount_value) or amount_value < 0:
        raise InputError(f"{context}.amount must be a finite non-negative number")
    return {"amount": amount_value, "currency": _string(value, "currency", context=context)}


def _usage(record: Any, index: int) -> dict[str, Any]:
    context = f"usage_attempts[{index}]"
    if not isinstance(record, Mapping):
        raise InputError(f"{context} must be an object")
    _check_keys(
        record,
        allowed={
            "provider", "session_id", "turn_id", "attempt_id", "observed_at",
            "cumulative_tokens", "baseline_tokens", "monetary",
        },
        context=context,
    )
    required = ("provider", "session_id", "turn_id", "attempt_id", "observed_at", "cumulative_tokens")
    missing = [name for name in required if name not in record]
    if missing:
        raise InputError(f"{context} is missing: {', '.join(missing)}")
    baseline = None
    if "baseline_tokens" in record and record["baseline_tokens"] is not None:
        baseline = _non_negative_int(record["baseline_tokens"], context=f"{context}.baseline_tokens")
    return {
        "provider": _string(record, "provider", context=context),
        "session_id": _string(record, "session_id", context=context),
        "turn_id": _string(record, "turn_id", context=context),
        "attempt_id": _string(record, "attempt_id", context=context),
        "observed_at": _timestamp(record["observed_at"], context=f"{context}.observed_at"),
        "cumulative_tokens": _non_negative_int(record["cumulative_tokens"], context=f"{context}.cumulative_tokens"),
        "baseline_tokens": baseline,
        "monetary": _money(record.get("monetary"), context=f"{context}.monetary"),
    }


def _usage_identity(record: Mapping[str, Any]) -> tuple[str, str, str, str]:
    return (record["provider"], record["session_id"], record["turn_id"], record["attempt_id"])


def _usage_fingerprint(record: Mapping[str, Any]) -> tuple[Any, ...]:
    money = record["monetary"]
    money_key = None if money is None else (money["amount"], money["currency"])
    return (
        record["cumulative_tokens"], record["baseline_tokens"], money_key,
    )


def _acceptance(record: Any, index: int) -> dict[str, Any]:
    context = f"accepted_work[{index}]"
    if not isinstance(record, Mapping):
        raise InputError(f"{context} must be an object")
    _check_keys(
        record,
        allowed={"contract_id", "revision", "artifact_id", "evaluation_id", "state", "observed_at"},
        context=context,
    )
    required = ("contract_id", "revision", "artifact_id", "evaluation_id", "state", "observed_at")
    missing = [name for name in required if name not in record]
    if missing:
        raise InputError(f"{context} is missing: {', '.join(missing)}")
    state = _string(record, "state", context=context)
    if state not in {"accepted", "reopened"}:
        raise InputError(f"{context}.state must be accepted or reopened")
    return {
        "contract_id": _string(record, "contract_id", context=context),
        "revision": _string(record, "revision", context=context),
        "artifact_id": _string(record, "artifact_id", context=context),
        "evaluation_id": _string(record, "evaluation_id", context=context),
        "state": state,
        "observed_at": _timestamp(record["observed_at"], context=f"{context}.observed_at"),
    }


def _acceptance_identity(record: Mapping[str, Any]) -> tuple[str, str, str, str]:
    return (record["contract_id"], record["revision"], record["artifact_id"], record["evaluation_id"])


def _serial_usage(record: Mapping[str, Any], *, state: str, delta: int | None) -> dict[str, Any]:
    return {
        "provider": record["provider"],
        "session_id": record["session_id"],
        "turn_id": record["turn_id"],
        "attempt_id": record["attempt_id"],
        "observed_at": _iso(record["observed_at"]),
        "cumulative_tokens": record["cumulative_tokens"],
        "baseline_tokens": record["baseline_tokens"],
        "delta_state": state,
        "observed_token_delta": delta,
        "monetary": record["monetary"],
    }


def build_report(document: Mapping[str, Any]) -> dict[str, Any]:
    """Build a deterministic report from explicit observations only.

    The function has no time, filesystem, network, model, scheduler, or bridge
    dependency.  It is therefore safe to call repeatedly in a shadow path.
    """
    if not isinstance(document, Mapping):
        raise InputError("ledger input must be an object")
    _assert_finite(document)
    _check_keys(document, allowed={"schema", "usage_attempts", "accepted_work"}, context="ledger input")
    if document.get("schema") != INPUT_SCHEMA:
        raise InputError("unsupported ledger schema")
    usage_raw = document.get("usage_attempts")
    acceptance_raw = document.get("accepted_work")
    if not isinstance(usage_raw, list):
        raise InputError("usage_attempts must be a list")
    if not isinstance(acceptance_raw, list):
        raise InputError("accepted_work must be a list")
    if len(usage_raw) > DEFAULT_MAX_RECORDS or len(acceptance_raw) > DEFAULT_MAX_RECORDS:
        raise InputError("ledger record count exceeds limit")

    usages: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    usage_duplicates = 0
    for index, raw in enumerate(usage_raw):
        item = _usage(raw, index)
        key = _usage_identity(item)
        prior = usages.get(key)
        if prior is None:
            usages[key] = item
        elif _usage_fingerprint(prior) == _usage_fingerprint(item):
            usage_duplicates += 1
        else:
            raise InputError("conflicting duplicate usage attempt")

    acceptance_events: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}
    acceptance_duplicates = 0
    for index, raw in enumerate(acceptance_raw):
        item = _acceptance(raw, index)
        key = (*_acceptance_identity(item), item["state"])
        prior = acceptance_events.get(key)
        if prior is None:
            acceptance_events[key] = item
        else:
            acceptance_duplicates += 1
            # Replays may arrive out of order. Retain the earliest observation
            # of this immutable identity, not whichever row arrived first.
            # A new acceptance after reopening needs a new evaluation identity.
            if item["observed_at"] < prior["observed_at"]:
                acceptance_events[key] = item

    usage_rows: list[dict[str, Any]] = []
    observed_deltas: list[int] = []
    missing_baseline = 0
    reset = 0
    money_by_currency: dict[str, float] = {}
    for key in sorted(usages):
        item = usages[key]
        baseline = item["baseline_tokens"]
        if baseline is None:
            state, delta = "missing_baseline", None
            missing_baseline += 1
        elif item["cumulative_tokens"] < baseline:
            state, delta = "counter_reset", None
            reset += 1
        else:
            state, delta = "observed_delta", item["cumulative_tokens"] - baseline
            observed_deltas.append(delta)
        money = item["monetary"]
        if money is not None:
            currency = money["currency"]
            aggregate = money_by_currency.get(currency, 0.0) + money["amount"]
            if not math.isfinite(aggregate):
                raise InputError("monetary aggregate must remain finite")
            money_by_currency[currency] = aggregate
        usage_rows.append(_serial_usage(item, state=state, delta=delta))

    acceptance_by_revision: dict[tuple[str, str], list[dict[str, Any]]] = {}
    historical_acceptance_keys: set[tuple[str, str, str, str]] = set()
    for event in acceptance_events.values():
        revision_key = (event["contract_id"], event["revision"])
        acceptance_by_revision.setdefault(revision_key, []).append(event)
        if event["state"] == "accepted":
            historical_acceptance_keys.add(_acceptance_identity(event))

    active_rows: list[dict[str, Any]] = []
    active_accepted = 0
    active_reopened = 0
    for revision_key in sorted(acceptance_by_revision):
        events = sorted(
            acceptance_by_revision[revision_key],
            key=lambda event: (event["observed_at"], event["state"], event["artifact_id"], event["evaluation_id"]),
        )
        latest = events[-1]
        same_time_states = {event["state"] for event in events if event["observed_at"] == latest["observed_at"]}
        if len(same_time_states) > 1:
            raise InputError("acceptance state is ambiguous at one timestamp")
        row = {
            "contract_id": latest["contract_id"],
            "revision": latest["revision"],
            "state": latest["state"],
            "artifact_id": latest["artifact_id"],
            "evaluation_id": latest["evaluation_id"],
            "observed_at": _iso(latest["observed_at"]),
        }
        active_rows.append(row)
        if latest["state"] == "accepted":
            active_accepted += 1
        else:
            active_reopened += 1

    total_attempts = len(usages)
    complete_attempts = len(observed_deltas)
    partial = bool(missing_baseline or reset)
    coverage_state = (
        "no_usage_attempts" if total_attempts == 0
        else "partial_observation" if partial
        else "complete_observation"
    )
    report = {
        "schema": REPORT_SCHEMA,
        "usage": {
            "unique_attempts": total_attempts,
            "duplicate_attempts_ignored": usage_duplicates,
            "attempts_with_observed_delta": complete_attempts,
            "attempts_missing_baseline": missing_baseline,
            "attempts_with_counter_reset": reset,
            "coverage_state": coverage_state,
            "observation_scope": "supplied_rows_only",
            "coverage_note": (
                "Coverage describes only supplied usage_attempts rows and cannot "
                "establish that all work is accounted for."
            ),
            # Do not present a subset as the full total.
            "token_delta_total": None if partial else sum(observed_deltas),
            "observed_partial_token_delta_total": sum(observed_deltas) if partial else None,
            "rows": usage_rows,
        },
        "accepted_work": {
            "unique_acceptance_receipts": len(historical_acceptance_keys),
            "duplicate_acceptance_receipts_ignored": acceptance_duplicates,
            "historical_accepted_contract_revisions": len({key[:2] for key in historical_acceptance_keys}),
            "active_accepted_contract_revisions": active_accepted,
            "active_reopened_contract_revisions": active_reopened,
            "active_rows": active_rows,
            "denominator_note": (
                "A reopened revision remains a historical receipt but is excluded "
                "from the active accepted-work view."
            ),
        },
        "money": {
            "observed_by_currency": dict(sorted(money_by_currency.items())) or None,
            "combined_total": None,
            "state": "observed_by_currency_only" if money_by_currency else "unobserved",
            "note": "Money is null unless explicitly observed; currencies are never combined.",
        },
        "ranking": None,
        "execution": {"performed": False, "reason": "pure dormant ledger"},
    }
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a pure observed-usage/accepted-work ledger report.")
    parser.add_argument("--input", required=True, type=Path, help="strict UTF-8 ledger JSON input")
    parser.add_argument("--max-input-bytes", type=int, default=DEFAULT_MAX_INPUT_BYTES)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = build_report(load_document(args.input, max_bytes=args.max_input_bytes))
    except InputError as exc:
        print(f"ledger input refused: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

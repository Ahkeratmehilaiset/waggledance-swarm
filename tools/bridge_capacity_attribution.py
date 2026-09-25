#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""Conservative attribution, recency and readiness for capacity observations.

Revision 2. It classifies ``wd.capacity-observation.v1`` records and is wired
into the collector's read-only ``--status`` path behind an opt-in flag.

What ``auth_context_id`` actually is, having read its derivation
----------------------------------------------------------------
``collect_codex`` is called with the resolved filesystem path of ``CODEX_HOME``
and computes ``digest([auth_context, account])``, where ``account`` contributes
only a visible shape such as type and planType. So the id is a **machine-local
credential-store fingerprint**, not a provider assertion about billing.

Equality therefore proves that two observations came through the same local
credential directory on this machine. It does NOT prove shared provider quota
membership, and inequality proves even less, because one provider account reached
through two ``CODEX_HOME`` paths yields two different ids. Revision 1 called
equality a ``shared_pool_candidate``; that overstated it and is corrected here to
``same_local_credential_store`` with pool membership left ``unknown``.

Why an attestation can never authorise anything
-----------------------------------------------
Nothing in this repository authenticates an attestation. A JSON object naming an
attester is a *declaration*, and a forged declaration is byte-indistinguishable
from a genuine one. Since the two cannot be told apart, no attestation may enable
dispatch or establish a cost denominator. Attestations are therefore recorded as
provenance with ``authentication: none`` and never change readiness.

Why an open window is not headroom
----------------------------------
``resets_at`` is provider-supplied and bounds when a window closes. It says
nothing about present consumption. A window that is still open, paired with a
usage figure observed long ago or never stamped by the provider, does not
establish availability, so readiness stays ``not_ready`` in that case.

Cost stays decomposed
---------------------
Cost amount, currency, accepted-work denominator and pool/window attribution are
four separate elements. Any one missing leaves cost ``unmeasured``; they are
never combined into a single number.

Standard library only. No provider call, network, credential, login, settings
change, scheduler or deployment. The collector wiring adds no write of any kind.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence


OBSERVATION_SCHEMA = "wd.capacity-observation.v1"
REPORT_SCHEMA = "wd.capacity-attribution-report.v1"
ATTESTATION_SCHEMA = "wd.capacity-pool-attestation.v1"

DEFAULT_MAX_INPUT_BYTES = 1_048_576
DEFAULT_MAX_OBSERVATIONS = 1000

#: Local-recency bound. Ours, not the provider's, hence never called "fresh".
DEFAULT_RECENT_SECONDS = 300

#: Attestations older than this are expired. They are unauthenticated either
#: way, so this only stops an ancient declaration from looking current.
DEFAULT_ATTESTATION_MAX_AGE_SECONDS = 30 * 24 * 3600

#: Never consulted for attribution. A label is not a billing boundary.
FORBIDDEN_ATTRIBUTION_FIELDS = frozenset(
    {"agent", "lane", "role", "agent_uuid", "session_id", "native_thread_id", "task_id"}
)

POOL_SAME_LOCAL_STORE = "same_local_credential_store"
POOL_DIFFERENT_LOCAL_STORE = "different_local_credential_store"
#: One observation has no comparator, so no relation can be claimed at all.
POOL_SINGLE_UNKNOWN = "single_observation_relation_unknown"
POOL_INSUFFICIENT = "insufficient_evidence"
#: Membership is always unknown: no provider endpoint states it.
POOL_MEMBERSHIP_UNKNOWN = "unknown"

VALIDITY_OPEN = "provider_window_open"
VALIDITY_CLOSED = "provider_window_boundary_passed"
VALIDITY_UNPROVABLE = "unprovable_no_provider_boundary"

READY_NOT = "not_ready"
READY_UNKNOWN = "unknown"

COST_UNMEASURED = "unmeasured"


class InputError(Exception):
    """Raised for malformed, oversized, or non-strict input."""


def _reject_duplicate_keys(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    seen: set[str] = set()
    for name, _ in pairs:
        if name in seen:
            raise InputError("duplicate JSON key")
        seen.add(name)
    return dict(pairs)


def _reject_constant(name: str) -> Any:
    raise InputError(f"non-finite JSON constant is not accepted: {name}")


#: Largest magnitude that survives a float() conversion. Anything beyond this
#: would overflow the moment any caller treated it as a float.
_FLOAT_LIMIT = 1.7976931348623157e308


def _reject_float(text: str) -> float:
    """Reject a numeric literal that parses to a non-finite float, e.g. 1e999."""
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
    """Walk the parsed document and reject any non-finite or overflow number.

    Defence in depth behind the parse hooks: a hook covers literals, this covers
    whatever survives, at any nesting level.
    """
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
    """Parse JSON rejecting duplicate keys and every non-finite or overflow number."""
    parsed = json.loads(
        text, object_pairs_hook=_reject_duplicate_keys, parse_constant=_reject_constant,
        parse_float=_reject_float, parse_int=_reject_int,
    )
    _assert_finite(parsed)
    return parsed


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        as_float = float(value)
    except (OverflowError, ValueError):
        return None
    return as_float if math.isfinite(as_float) else None


def _parse_utc(value: Any) -> datetime | None:
    """Parse an explicit UTC-offset timestamp, or return None.

    A naive timestamp is REJECTED rather than assumed to be UTC. Assuming a
    timezone silently invents an offset, which is the same class of mistake this
    module refuses everywhere else.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _timestamp_state(value: Any) -> str:
    """Distinguish absent, unparsable and timezone-less timestamps."""
    if not isinstance(value, str) or not value.strip():
        return "absent"
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return "unparsable"
    return "ok" if parsed.tzinfo is not None else "timezone_unknown"


def _epoch_to_utc(value: Any) -> datetime | None:
    number = _finite_number(value)
    if number is None:
        return None
    if not (0 < number < 4_102_444_800):  # reject absurd epochs rather than convert
        return None
    try:
        return datetime.fromtimestamp(number, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def _iso(value: datetime | None) -> str | None:
    return value.isoformat().replace("+00:00", "Z") if value else None


def auth_context_of(observation: Mapping[str, Any]) -> str | None:
    """Return the local credential-store fingerprint, never a label field."""
    value = observation.get("auth_context_id")
    return value.strip() if isinstance(value, str) and value.strip() else None


def provider_windows(observation: Mapping[str, Any]) -> list[dict[str, Any]]:
    payload = observation.get("payload")
    if not isinstance(payload, Mapping):
        return []
    limits = payload.get("rate_limits")
    if not isinstance(limits, Mapping):
        limits = payload.get("rateLimits")
    if not isinstance(limits, Mapping):
        return []
    windows: list[dict[str, Any]] = []
    for name, window in sorted(limits.items()):
        if not isinstance(window, Mapping):
            continue
        raw = window.get("resets_at", window.get("resetsAt"))
        resets_at = _epoch_to_utc(raw) or _parse_utc(raw)
        windows.append(
            {
                "window": name,
                "used_percent": _finite_number(
                    window.get("used_percentage", window.get("usedPercent"))
                ),
                "provider_resets_at": _iso(resets_at),
                "used_percent_observed_at": None,
                "used_percent_note": (
                    "no provider timestamp exists for this figure; it is not aged "
                    "and does not establish present consumption"
                ),
            }
        )
    return windows


def classify_validity(observation: Mapping[str, Any], now: datetime) -> dict[str, Any]:
    """Provider window boundary only. Deliberately says nothing about headroom."""
    windows = provider_windows(observation)
    parsed = sorted(d for d in (_parse_utc(w["provider_resets_at"]) for w in windows) if d)
    earliest = parsed[0] if parsed else None
    if earliest is None:
        state = VALIDITY_UNPROVABLE
    else:
        state = VALIDITY_OPEN if now < earliest else VALIDITY_CLOSED
    return {
        "provider_validity_state": state,
        "earliest_provider_boundary": _iso(earliest),
        "windows": windows,
        "provider_observation_timestamp": None,
        "headroom_note": (
            "an open window bounds when the window closes; it is NOT evidence of "
            "remaining headroom or of current usage"
        ),
    }


def classify_local_recency(
    observation: Mapping[str, Any], now: datetime, *, recent_seconds: int = DEFAULT_RECENT_SECONDS
) -> dict[str, Any]:
    raw = observation.get("observed_at")
    observed = _parse_utc(raw)
    if observed is None:
        state = _timestamp_state(raw)
        return {
            "observation_age_seconds": None,
            "local_recency_state": {
                "absent": "unknown_no_observed_at",
                "unparsable": "unknown_unparsable_observed_at",
                "timezone_unknown": "unknown_timezone_missing",
            }.get(state, "unknown_no_observed_at"),
            "clock_note": (
                "observed_at carries no timezone offset, so it is refused rather "
                "than assumed to be UTC" if state == "timezone_unknown"
                else "record carries no usable observed_at"
            ),
        }
    age = (now - observed).total_seconds()
    if age < 0:
        return {"observation_age_seconds": round(age, 3),
                "local_recency_state": "future_observed_at_clock_skew",
                "clock_note": "observed_at is ahead of our clock; reported, not clamped"}
    return {
        "observation_age_seconds": round(age, 3),
        "local_recency_state": "recent_local_observation" if age <= recent_seconds
        else "stale_local_observation",
        "clock_note": "aged against our clock only; not evidence the provider figure is true now",
    }


def classify_readiness(validity: Mapping[str, Any], local: Mapping[str, Any]) -> dict[str, Any]:
    """Readiness needs BOTH an open window and a usage figure we can still trust.

    A future reset boundary with stale usage is explicitly not ready, because the
    boundary describes the window and the usage describes consumption.
    """
    reasons: list[str] = []
    if validity["provider_validity_state"] != VALIDITY_OPEN:
        reasons.append(f"window_{validity['provider_validity_state']}")
    if local["local_recency_state"] != "recent_local_observation":
        reasons.append(f"usage_{local['local_recency_state']}")
    if reasons:
        return {"readiness": READY_NOT, "reasons": reasons}
    # Even here we do not claim availability: no provider stamps present usage.
    return {
        "readiness": READY_UNKNOWN,
        "reasons": ["no_provider_timestamp_for_present_usage"],
    }


def load_attestation(document: Any, *, now: datetime | None = None,
                     max_age_seconds: int = DEFAULT_ATTESTATION_MAX_AGE_SECONDS
                     ) -> dict[str, dict[str, Any]]:
    """Load unauthenticated pool declarations. These never authorise anything."""
    if document is None:
        return {}
    now = now or datetime.now(timezone.utc)
    if not isinstance(document, Mapping):
        raise InputError("attestation must be a JSON object")
    if document.get("schema") != ATTESTATION_SCHEMA:
        raise InputError("unsupported attestation schema")
    entries = document.get("entries")
    if not isinstance(entries, list):
        raise InputError("attestation must contain an 'entries' list")

    mapping: dict[str, dict[str, Any]] = {}
    conflicts: set[str] = set()
    for index, entry in enumerate(entries):
        if not isinstance(entry, Mapping):
            raise InputError(f"attestation entry {index} is not an object")
        context = entry.get("auth_context_id")
        pool = entry.get("pool_id")
        attester = entry.get("attester")
        source = entry.get("source")
        missing = [n for n, v in (("auth_context_id", context), ("pool_id", pool),
                                  ("attester", attester), ("source", source))
                   if not isinstance(v, str) or not v.strip()]
        if missing:
            raise InputError(f"attestation entry {index} missing: {', '.join(missing)}")
        attested_at = _parse_utc(entry.get("attested_at"))
        if attested_at is None:
            raise InputError(f"attestation entry {index} has no parsable attested_at")
        key = context.strip()
        age = (now - attested_at).total_seconds()
        record = {
            "pool_id": pool.strip(),
            "attester": attester.strip(),
            "source": source.strip(),
            "attested_at": _iso(attested_at),
            "authentication": "none",
            "expired": age > max_age_seconds or age < 0,
            "conflicting": False,
        }
        if key in mapping and mapping[key]["pool_id"] != record["pool_id"]:
            conflicts.add(key)
        mapping[key] = record
    for key in conflicts:
        mapping[key]["conflicting"] = True
    return mapping


def classify_pool(observation: Mapping[str, Any], *, context_counts: Mapping[str, int],
                  attestation: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    context = auth_context_of(observation)
    if context is None:
        return {
            "auth_context_id": None,
            "local_store_relation": POOL_INSUFFICIENT,
            "provider_pool_membership": POOL_MEMBERSHIP_UNKNOWN,
            "pool_id": None,
            "declaration": None,
            "basis": "no auth_context_id on the observation",
            "proves": "nothing; identity is missing",
        }
    seen = context_counts.get(context, 0)
    if seen > 1:
        relation = POOL_SAME_LOCAL_STORE
    elif seen == 1:
        # One observation has nothing to compare against. Calling that a
        # different store would assert a relation no evidence supports.
        relation = POOL_SINGLE_UNKNOWN
    else:
        relation = POOL_DIFFERENT_LOCAL_STORE
    declared = attestation.get(context)
    declaration = None
    if declared:
        usable = not declared["expired"] and not declared["conflicting"]
        declaration = {
            **declared,
            "accepted_as_provenance": usable,
            "authorises_dispatch": False,
            "establishes_cost_denominator": False,
            "why": (
                "unauthenticated declaration; a forged entry is indistinguishable "
                "from a genuine one, so it can record provenance but never authorise"
            ),
        }
    return {
        "auth_context_id": context,
        "local_store_relation": relation,
        # Always unknown: no provider endpoint states pool membership.
        "provider_pool_membership": POOL_MEMBERSHIP_UNKNOWN,
        "pool_id": declared["pool_id"] if declared and declaration
        and declaration["accepted_as_provenance"] else None,
        "declaration": declaration,
        "basis": (
            "another observation shares this local credential-store fingerprint"
            if relation == POOL_SAME_LOCAL_STORE
            else "this fingerprint appears once in the input, so there is no comparator"
        ),
        "proves": (
            "same local CODEX_HOME credential directory and account shape; NOT "
            "shared provider quota membership"
            if relation == POOL_SAME_LOCAL_STORE else
            "nothing; with a single observation no relation to any other store can "
            "be established, and a distinct fingerprint would not imply an "
            "independent pool either"
        ),
    }


def classify_cost(observation: Mapping[str, Any], pool: Mapping[str, Any]) -> dict[str, Any]:
    """Four separate elements; any one missing leaves cost unmeasured."""
    cost = observation.get("cost")
    cost = cost if isinstance(cost, Mapping) else {}
    monetary = cost.get("monetary") if isinstance(cost.get("monetary"), Mapping) else {}
    amount = _finite_number(monetary.get("amount"))
    amount = amount if amount is not None and amount >= 0 else None
    currency = monetary.get("currency")
    currency = currency.strip() if isinstance(currency, str) and currency.strip() else None
    denominator = observation.get("accepted_work_units")
    denominator = denominator if isinstance(denominator, int) and not isinstance(
        denominator, bool) and denominator > 0 else None
    attributed_pool = pool.get("pool_id")

    missing = [name for name, value in (
        ("cost_amount", amount), ("currency", currency),
        ("accepted_work_denominator", denominator),
        ("pool_window_attribution", attributed_pool)) if value is None]
    return {
        "cost_amount": amount,
        "currency": currency,
        "accepted_work_denominator": denominator,
        "pool_window_attribution": attributed_pool,
        "missing_elements": missing,
        "cost_state": COST_UNMEASURED,
        "cost_per_accepted_unit": None,
        "why_unmeasured": (
            "cost-per-accepted-unit requires an authenticated pool attribution, "
            "which no provider endpoint supplies here; missing elements: "
            + (", ".join(missing) if missing else "none, but attribution is still unauthenticated")
        ),
    }


def attribute(observations: Sequence[Any], *, now: datetime | None = None,
              attestation: Mapping[str, Mapping[str, Any]] | None = None,
              recent_seconds: int = DEFAULT_RECENT_SECONDS) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    attestation = attestation or {}
    rejected: list[dict[str, Any]] = []
    valid: list[Mapping[str, Any]] = []
    for index, obs in enumerate(observations):
        if not isinstance(obs, Mapping):
            rejected.append({"index": index, "reasons": ["observation_not_an_object"]})
        elif obs.get("schema") != OBSERVATION_SCHEMA:
            rejected.append({"index": index, "reasons": ["unsupported_observation_schema"]})
        else:
            valid.append(obs)

    counts: dict[str, int] = {}
    for obs in valid:
        context = auth_context_of(obs)
        if context:
            counts[context] = counts.get(context, 0) + 1

    rows: list[dict[str, Any]] = []
    for obs in valid:
        pool = classify_pool(obs, context_counts=counts, attestation=attestation)
        validity = classify_validity(obs, now)
        local = classify_local_recency(obs, now, recent_seconds=recent_seconds)
        rows.append({
            "provider": obs.get("provider"),
            "source_ref": obs.get("source_ref"),
            "pool": pool,
            "validity": validity,
            "local": local,
            "readiness": classify_readiness(validity, local),
            "cost": classify_cost(obs, pool),
        })

    return {
        "rows": rows,
        "rejected": rejected,
        # No authenticated authorization path exists, so both stay false always.
        "dispatch_enabled": False,
        "dispatch_note": (
            "no authenticated attestation mechanism exists; declarations record "
            "provenance and never enable dispatch"
        ),
        "cost_denominator_available": False,
        "cost_denominator_note": (
            "an unauthenticated declaration cannot establish a denominator, and "
            "local credential-store equality is not provider pool membership"
        ),
    }


def build_report(document: Mapping[str, Any], *, base_commit: str | None = None,
                 attestation_doc: Any = None, now: datetime | None = None,
                 recent_seconds: int = DEFAULT_RECENT_SECONDS) -> dict[str, Any]:
    observations = document.get("observations")
    if not isinstance(observations, list):
        raise InputError("input must contain an 'observations' list")
    now = now or datetime.now(timezone.utc)
    attestation = load_attestation(attestation_doc, now=now)
    result = attribute(observations, now=now, attestation=attestation,
                       recent_seconds=recent_seconds)
    return {
        "schema": REPORT_SCHEMA,
        "generated_at_utc": _iso(now),
        "base_commit": base_commit,
        "input_observations": len(observations),
        "attestation_entries": len(attestation),
        **result,
        "never_inferred_from": sorted(FORBIDDEN_ATTRIBUTION_FIELDS),
        "auth_context_id_meaning": (
            "sha256 over the resolved CODEX_HOME path plus a visible account shape; "
            "a machine-local credential-store fingerprint, not a provider account id"
        ),
        "absent_supported_evidence": [
            "no provider endpoint states quota-pool membership for an auth context",
            "no provider timestamp exists for the instant a usage figure was true",
            "the Claude statusline path emits no auth context at all, only a session id",
            "no authentication exists for an operator attestation, so a forged one "
            "cannot be distinguished from a genuine one",
        ],
    }


def attribution_block(status_document: Mapping[str, Any], *, now: datetime | None = None,
                      attestation_doc: Any = None) -> dict[str, Any]:
    """Additive block for the collector --status output. Never raises."""
    try:
        observations = status_document.get("observations")
        if not isinstance(observations, list):
            return {"state": "unavailable", "reason": "status carries no observations list"}
        now = now or datetime.now(timezone.utc)
        attestation = load_attestation(attestation_doc, now=now)
        result = attribute(observations, now=now, attestation=attestation)
        return {"state": "available", "schema": REPORT_SCHEMA, **result}
    except InputError:
        # Concise and non-secret: the input may contain values we must not echo.
        return {"state": "unavailable", "reason": "input_rejected_by_strict_validation"}
    except Exception as exc:  # never let classification break a read-only status
        return {"state": "unavailable", "reason": f"classifier_error:{type(exc).__name__}"}


def load_json_file(path: Path, *, max_bytes: int = DEFAULT_MAX_INPUT_BYTES) -> Any:
    """Bounded, strictly parsed read of any JSON file, with concise errors."""
    if not path.is_file():
        raise InputError("file not found")
    with path.open("r", encoding="utf-8") as handle:
        text = handle.read(max_bytes + 1)
    if len(text.encode("utf-8")) > max_bytes:
        raise InputError(f"file exceeds the {max_bytes}-byte bound")
    try:
        return strict_loads(text)
    except json.JSONDecodeError as exc:
        raise InputError(
            f"file is not valid JSON at line {exc.lineno} column {exc.colno}"
        ) from None


def load_document(path: Path, *, max_bytes: int = DEFAULT_MAX_INPUT_BYTES,
                  max_observations: int = DEFAULT_MAX_OBSERVATIONS) -> dict[str, Any]:
    if not path.is_file():
        raise InputError(f"input file not found: {path}")
    with path.open("r", encoding="utf-8") as handle:
        text = handle.read(max_bytes + 1)
    if len(text.encode("utf-8")) > max_bytes:
        raise InputError(f"input exceeds the {max_bytes}-byte bound")
    try:
        document = strict_loads(text)
    except json.JSONDecodeError as exc:
        # Report position only. The document may hold values we must not echo.
        raise InputError(
            f"input is not valid JSON at line {exc.lineno} column {exc.colno}"
        ) from None
    if not isinstance(document, Mapping):
        raise InputError("input must be a JSON object")
    observations = document.get("observations")
    if not isinstance(observations, list):
        raise InputError("input must contain an 'observations' list")
    if len(observations) > max_observations:
        raise InputError(f"input has {len(observations)} observations, "
                         f"over the {max_observations} bound")
    return dict(document)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=("Classify capacity observations for attribution, recency and "
                     "readiness. Read-only; authorises nothing."))
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--attestation", type=Path, default=None)
    parser.add_argument("--base-commit", default=None)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--recent-seconds", type=int, default=DEFAULT_RECENT_SECONDS)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        document = load_document(args.input)
        attestation_doc = None
        if args.attestation is not None:
            # Bounded and strictly parsed exactly like the observation input.
            attestation_doc = load_json_file(args.attestation)
        report = build_report(document, base_commit=args.base_commit,
                              attestation_doc=attestation_doc,
                              recent_seconds=args.recent_seconds)
    except InputError as exc:
        print(f"input error: {exc}", file=sys.stderr)
        return 2
    except (OSError, ValueError, RecursionError):
        # Parser/encoding/IO errors can contain input values or local paths.
        print("input error: unreadable or malformed input", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0
    print(f"observations: {report['input_observations']}  rejected: {len(report['rejected'])}")
    for row in report["rows"]:
        print(f"  {str(row['provider']):8} store={row['pool']['local_store_relation']:32} "
              f"membership={row['pool']['provider_pool_membership']:8} "
              f"ready={row['readiness']['readiness']:10} cost={row['cost']['cost_state']}")
    print(f"dispatch_enabled: {report['dispatch_enabled']}   "
          f"cost_denominator_available: {report['cost_denominator_available']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

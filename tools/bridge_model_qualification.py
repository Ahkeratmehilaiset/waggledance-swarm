#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""Offline qualification report for model/effort profiles, aggregated by risk stratum.

The harness turns *explicitly supplied* scored observations into a per-stratum
report, so that a capability-tier policy can be argued from measured evidence
instead of from price or allowance ratios.

Deliberate non-features, each enforced and regression-tested:

* **No pooled critical-miss average.** Strata are reported separately and
  ``pooled_critical_miss_rate`` is always ``null``. Pooling a representative mix
  is dominated by easy items and hides the stratum that matters.
* **No automatic approval.** The strongest verdict is
  ``eligible_for_operator_review``. Nothing here qualifies a profile, switches a
  model, grants credits, or approves a release.
* **Chat/advisory evidence cannot qualify a profile.** Such observations are
  rejected, not down-weighted.
* **Unknown model or effort is preserved but never eligible.** The numbers are
  still reported; the profile just cannot be reviewed for qualification.
* **Evidence identity is required.** Observations carry a unique
  ``observation_id``; duplicates are rejected, and eligibility counts *distinct
  ``evidence_id`` values*, so copying one observation three times proves nothing.
* **Incompatible cost units are never summed.** Monetary totals are keyed by
  currency and quota totals by pool and unit. Unknown cost stays ``null``, never
  ``0``; a claim missing its denominator, currency, pool or unit is dropped while
  quality and latency survive, because those need no cost denominator.
* **No false-alarm rate without its own denominator.** False alarms are not
  drawn from ``defects_total``, so the rate is reported only when the caller
  supplies ``false_alarm_opportunities``.
* **Quality and latency are reported separately** and never combined.

Input is a bounded, strictly parsed JSON document (duplicate keys and the
non-finite JSON constants are rejected). Standard library only; no provider,
network, scheduler, credential, or bridge access.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence


INPUT_SCHEMA = "wd.model-qualification-input.v1"
REPORT_SCHEMA = "wd.model-qualification-report.v1"

DEFAULT_MAX_INPUT_BYTES = 1_048_576
DEFAULT_MAX_OBSERVATIONS = 1000

#: Evidence that can never qualify a profile, however much of it is supplied.
NON_QUALIFYING_EVIDENCE = frozenset({"chat", "advisory", "brainstorm", "discussion"})

#: Provenance values accepted as an actual observation of the running model.
KNOWN_PROVENANCE = frozenset({"transcript", "server_response", "runtime_api"})

#: Values that mean "we do not actually know", so the profile cannot be reviewed.
UNKNOWN_TOKENS = frozenset({"", "unknown", "unspecified", "none", "null", "n/a"})

VERDICT_INSUFFICIENT = "insufficient_evidence"
VERDICT_UNKNOWN_PROFILE = "unknown_profile_not_eligible"
VERDICT_ELIGIBLE = "eligible_for_operator_review"

#: Distinct evidence items a stratum needs before it may even be *reviewed*.
DEFAULT_MIN_OBSERVATIONS = 3


class InputError(Exception):
    """Raised for malformed, oversized, or non-strict input documents."""


@dataclass(frozen=True)
class StratumKey:
    observed_model: str
    observed_effort: str
    risk_stratum: str

    def as_dict(self) -> dict[str, str]:
        return {
            "observed_model": self.observed_model,
            "observed_effort": self.observed_effort,
            "risk_stratum": self.risk_stratum,
        }


@dataclass
class Rejection:
    index: int
    reasons: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"index": self.index, "reasons": sorted(self.reasons)}


def is_unknown(value: Any) -> bool:
    return not isinstance(value, str) or value.strip().lower() in UNKNOWN_TOKENS


def finite_non_negative(value: Any) -> float | None:
    """Return ``value`` as a finite, non-negative float, else ``None``.

    Guards three separate failure modes the first revision missed: negatives,
    non-finite floats, and integers too large to convert to float at all
    (``float(10**400)`` raises ``OverflowError`` rather than returning ``inf``).
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        as_float = float(value)
    except (OverflowError, ValueError):
        return None
    if not math.isfinite(as_float) or as_float < 0:
        return None
    return as_float


def non_negative_int(value: Any) -> int | None:
    """Return ``value`` as a non-negative int, else ``None``.

    Floats are rejected rather than truncated: a fractional defect count means
    the caller measured something other than what it claims. Booleans are not
    counts either, despite being ints in Python.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if value < 0:
        return None
    return value


def validate_observation(obs: Any, index: int) -> Rejection | None:
    """Return a :class:`Rejection` when ``obs`` may not be aggregated."""
    reasons: list[str] = []
    if not isinstance(obs, Mapping):
        return Rejection(index, ["observation_not_an_object"])

    for key in ("observation_id", "evidence_id"):
        value = obs.get(key)
        if not isinstance(value, str) or not value.strip():
            reasons.append(f"missing_{key}")

    for key in ("observed_model", "observed_effort", "risk_stratum"):
        value = obs.get(key)
        if not isinstance(value, str) or not value.strip():
            reasons.append(f"missing_{key}")

    provenance = obs.get("model_provenance")
    if not isinstance(provenance, str) or provenance not in KNOWN_PROVENANCE:
        reasons.append("unknown_model_provenance")

    evidence = obs.get("evidence_source")
    if not isinstance(evidence, str) or not evidence.strip():
        reasons.append("missing_evidence_source")
    elif evidence.strip().lower() in NON_QUALIFYING_EVIDENCE:
        reasons.append("chat_or_advisory_evidence_cannot_qualify")

    oracle = obs.get("independent_oracle")
    if not isinstance(oracle, Mapping) or oracle.get("present") is not True:
        reasons.append("independent_oracle_required")
    elif not str(oracle.get("oracle_id") or "").strip():
        reasons.append("independent_oracle_id_required")

    pair = obs.get("author_reviewer")
    if not isinstance(pair, Mapping):
        reasons.append("author_reviewer_evidence_required")
    else:
        author = str(pair.get("author") or "").strip()
        reviewer = str(pair.get("reviewer") or "").strip()
        if not author or not reviewer:
            reasons.append("author_reviewer_evidence_required")
        elif author == reviewer:
            reasons.append("author_must_not_be_reviewer")

    defects = non_negative_int(obs.get("defects_total"))
    misses = non_negative_int(obs.get("critical_misses"))
    alarms = non_negative_int(obs.get("false_alarms"))
    if defects is None:
        reasons.append("invalid_defects_total")
    if misses is None:
        reasons.append("invalid_critical_misses")
    if alarms is None:
        reasons.append("invalid_false_alarms")
    if defects is not None and defects == 0:
        reasons.append("defects_total_must_be_positive")
    if defects is not None and misses is not None and misses > defects:
        reasons.append("critical_misses_exceed_defects_total")

    opportunities = obs.get("false_alarm_opportunities")
    if opportunities is not None:
        as_int = non_negative_int(opportunities)
        if as_int is None:
            reasons.append("invalid_false_alarm_opportunities")
        elif alarms is not None and alarms > as_int:
            reasons.append("false_alarms_exceed_opportunities")

    latency = obs.get("latency_ms")
    if latency is not None:
        if not isinstance(latency, Mapping):
            reasons.append("invalid_latency_ms")
        else:
            for part in ("queue", "model", "tool"):
                if part in latency and finite_non_negative(latency[part]) is None:
                    reasons.append(f"invalid_latency_{part}")

    if reasons:
        return Rejection(index, reasons)
    return None


def _cost_claim(obs: Mapping[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Extract a cost claim, dropping any part that is not safely attributable.

    Monetary needs an explicit currency, quota needs an explicit pool and unit,
    and both need a denominator. Unknown cost stays ``None``; it never becomes
    ``0``, and incompatible units are never merged into one number.
    """
    notes: list[str] = []
    empty: dict[str, Any] = {"monetary": None, "quota": None, "denominator": None}
    cost = obs.get("cost")
    if cost is None:
        return empty, ["cost_unknown"]
    if not isinstance(cost, Mapping):
        return empty, ["cost_claim_dropped_not_an_object"]

    denominator = cost.get("denominator")
    if not isinstance(denominator, str) or not denominator.strip():
        return empty, ["cost_claim_dropped_missing_denominator"]

    result: dict[str, Any] = {"monetary": None, "quota": None, "denominator": denominator.strip()}

    monetary = cost.get("monetary")
    if monetary is not None:
        if not isinstance(monetary, Mapping):
            notes.append("monetary_dropped_not_an_object")
        else:
            amount = finite_non_negative(monetary.get("amount"))
            currency = monetary.get("currency")
            if amount is None:
                notes.append("monetary_dropped_invalid_amount")
            elif not isinstance(currency, str) or not currency.strip():
                notes.append("monetary_dropped_missing_currency")
            else:
                result["monetary"] = {"amount": amount, "currency": currency.strip()}

    quota = cost.get("quota")
    if quota is not None:
        if not isinstance(quota, Mapping):
            notes.append("quota_dropped_not_an_object")
        else:
            amount = finite_non_negative(quota.get("amount"))
            pool = quota.get("pool")
            unit = quota.get("unit")
            if amount is None:
                notes.append("quota_dropped_invalid_amount")
            elif not isinstance(pool, str) or not pool.strip():
                notes.append("quota_dropped_missing_pool")
            elif not isinstance(unit, str) or not unit.strip():
                notes.append("quota_dropped_missing_unit")
            else:
                result["quota"] = {
                    "amount": amount,
                    "pool": pool.strip(),
                    "unit": unit.strip(),
                }

    if result["monetary"] is None and result["quota"] is None:
        notes.append("cost_unknown")
    else:
        notes.append("cost_attributed")
    return result, notes


def _mean(values: Sequence[float]) -> float | None:
    if not values:
        return None
    return round(_finite_sum(values) / len(values), 3)


def _finite_sum(values: Sequence[float]) -> float:
    try:
        result = math.fsum(values)
    except (OverflowError, ValueError) as exc:
        raise InputError('numeric aggregate exceeds finite range') from exc
    if not math.isfinite(result):
        raise InputError('numeric aggregate exceeds finite range')
    return result


def aggregate(
    observations: Sequence[Any],
    *,
    min_observations: int = DEFAULT_MIN_OBSERVATIONS,
) -> dict[str, Any]:
    """Aggregate observations into per-stratum rows plus explicit rejections."""
    if type(min_observations) is not int or min_observations < 1:
        raise InputError('min_observations must be a positive integer')
    rejections: list[Rejection] = []
    buckets: dict[StratumKey, list[Mapping[str, Any]]] = {}
    seen_observation_ids: set[str] = set()

    for index, obs in enumerate(observations):
        rejection = validate_observation(obs, index)
        if rejection is not None:
            rejections.append(rejection)
            continue
        observation_id = str(obs["observation_id"]).strip()
        if observation_id in seen_observation_ids:
            rejections.append(Rejection(index, ["duplicate_observation_id"]))
            continue
        seen_observation_ids.add(observation_id)
        key = StratumKey(
            observed_model=str(obs["observed_model"]).strip(),
            observed_effort=str(obs["observed_effort"]).strip(),
            risk_stratum=str(obs["risk_stratum"]).strip(),
        )
        buckets.setdefault(key, []).append(obs)

    strata: list[dict[str, Any]] = []
    for key in sorted(buckets, key=lambda k: (k.risk_stratum, k.observed_model, k.observed_effort)):
        rows = buckets[key]
        defects = sum(int(r["defects_total"]) for r in rows)
        misses = sum(int(r["critical_misses"]) for r in rows)
        alarms = sum(int(r["false_alarms"]) for r in rows)

        opportunities_values = [
            non_negative_int(r.get("false_alarm_opportunities"))
            for r in rows
            if r.get("false_alarm_opportunities") is not None
        ]
        all_have_opportunities = len(opportunities_values) == len(rows) and all(
            v is not None for v in opportunities_values
        )
        opportunities_total = sum(v for v in opportunities_values if v is not None)
        if all_have_opportunities and opportunities_total:
            false_alarm_rate: float | None = round(alarms / opportunities_total, 4)
            false_alarm_note = "rate_over_supplied_opportunities"
        else:
            false_alarm_rate = None
            false_alarm_note = "false_alarm_rate_requires_explicit_opportunities"

        # Cost, never merged across currencies or across pool/unit pairs.
        monetary_by_currency: dict[str, float] = {}
        quota_by_pool_unit: dict[str, float] = {}
        denominators: set[str] = set()
        cost_notes: list[str] = []
        attributed = 0
        for row in rows:
            claim, notes = _cost_claim(row)
            cost_notes.extend(notes)
            if claim["monetary"] is None and claim["quota"] is None:
                continue
            attributed += 1
            if claim["denominator"]:
                denominators.add(claim["denominator"])
            if claim["monetary"] is not None:
                currency = claim["monetary"]["currency"]
                monetary_by_currency[currency] = _finite_sum([
                    monetary_by_currency.get(currency, 0.0), claim["monetary"]["amount"]
                ])
            if claim["quota"] is not None:
                bucket = json.dumps([claim['quota']['pool'], claim['quota']['unit']],
                                    ensure_ascii=True, separators=(',', ':'))
                quota_by_pool_unit[bucket] = _finite_sum([
                    quota_by_pool_unit.get(bucket, 0.0), claim["quota"]["amount"]
                ])

        latencies: dict[str, list[float]] = {"queue": [], "model": [], "tool": []}
        for row in rows:
            latency = row.get("latency_ms")
            if isinstance(latency, Mapping):
                for part in latencies:
                    value = finite_non_negative(latency.get(part))
                    if value is not None:
                        latencies[part].append(value)

        distinct_evidence = {str(r["evidence_id"]).strip() for r in rows}
        reasons: list[str] = []
        unknown_profile = is_unknown(key.observed_model) or is_unknown(key.observed_effort)
        if unknown_profile:
            reasons.append("unknown_model_or_effort_cannot_be_qualified")
        if len(distinct_evidence) < min_observations:
            reasons.append(
                f"distinct_evidence_{len(distinct_evidence)}_below_{min_observations}"
            )

        if unknown_profile:
            verdict = VERDICT_UNKNOWN_PROFILE
        elif len(distinct_evidence) < min_observations:
            verdict = VERDICT_INSUFFICIENT
        else:
            verdict = VERDICT_ELIGIBLE
            reasons.append("operator_and_gate_review_still_required")

        strata.append(
            {
                **key.as_dict(),
                "accepted_observations": len(rows),
                "distinct_evidence": len(distinct_evidence),
                "quality": {
                    "defects_total": defects,
                    "critical_misses": misses,
                    "critical_miss_rate": round(misses / defects, 4) if defects else None,
                    "false_alarms": alarms,
                    "false_alarm_opportunities": opportunities_total
                    if all_have_opportunities
                    else None,
                    "false_alarm_rate": false_alarm_rate,
                    "false_alarm_rate_note": false_alarm_note,
                },
                "latency_ms": {
                    "queue_mean": _mean(latencies["queue"]),
                    "model_mean": _mean(latencies["model"]),
                    "tool_mean": _mean(latencies["tool"]),
                },
                "cost": {
                    "monetary_by_currency": dict(sorted(monetary_by_currency.items())) or None,
                    "quota_by_pool_unit": dict(sorted(quota_by_pool_unit.items())) or None,
                    "denominators": sorted(denominators),
                    "attributed_observations": attributed,
                    "unattributed_observations": len(rows) - attributed,
                    "notes": sorted(set(cost_notes)),
                },
                "qualification": {"verdict": verdict, "reasons": reasons},
            }
        )

    return {
        "strata": strata,
        "rejected": [r.as_dict() for r in rejections],
        # Deliberately null: pooling across risk strata hides the critical tail.
        "pooled_critical_miss_rate": None,
        "pooled_rate_suppressed_reason": "pooling_across_risk_strata_is_not_reported",
    }


def build_report(document: Mapping[str, Any], *, base: str | None = None,
                 min_observations: int = DEFAULT_MIN_OBSERVATIONS) -> dict[str, Any]:
    observations = document.get("observations")
    if not isinstance(observations, list):
        raise InputError("input document must contain an 'observations' list")
    aggregated = aggregate(observations, min_observations=min_observations)
    return {
        "schema": REPORT_SCHEMA,
        "generated_at_utc": datetime.now(timezone.utc)
        .isoformat()
        .replace("+00:00", "Z"),
        "base": base,
        "input_observations": len(observations),
        **aggregated,
        "approval": {
            "automatic": False,
            "verdict": "requires_operator_and_gate_review",
            "note": (
                "This report never qualifies a profile, switches a model, grants "
                "credits, or approves a release."
            ),
        },
    }


def _reject_duplicate_keys(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    seen: set[str] = set()
    for name, _ in pairs:
        if name in seen:
            raise InputError(f"duplicate JSON key: {name!r}")
        seen.add(name)
    return dict(pairs)


def _reject_constant(name: str) -> Any:
    raise InputError(f"non-finite JSON constant is not accepted: {name}")


def strict_loads(text: str) -> Any:
    """Parse JSON rejecting duplicate keys and NaN/Infinity constants."""
    return json.loads(
        text, object_pairs_hook=_reject_duplicate_keys, parse_constant=_reject_constant,
        parse_float=_finite_json_float,
    )


def _finite_json_float(value: str) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise InputError('JSON number exceeds finite range')
    return result


def load_document(path: Path, *, max_bytes: int = DEFAULT_MAX_INPUT_BYTES,
                  max_observations: int = DEFAULT_MAX_OBSERVATIONS) -> dict[str, Any]:
    if any(type(v) is not int or v < 1 for v in (max_bytes, max_observations)):
        raise InputError('input bounds must be positive integers')
    if not path.is_file():
        raise InputError(f"input file not found: {path}")
    # Bounded read: never pull more than the limit into memory, and do not trust
    # a stat() that could disagree with what the read actually returns.
    with path.open("rb") as handle:
        raw = handle.read(max_bytes + 1)
    if len(raw) > max_bytes:
        raise InputError(f"input exceeds the {max_bytes}-byte bound")
    try:
        document = strict_loads(raw.decode('utf-8'))
    except (json.JSONDecodeError, UnicodeDecodeError, RecursionError) as exc:
        raise InputError(f"input is not valid JSON: {exc}") from exc
    if not isinstance(document, Mapping):
        raise InputError("input document must be a JSON object")
    schema = document.get("schema")
    if schema != INPUT_SCHEMA:
        raise InputError(f"unsupported input schema: {schema!r} (expected {INPUT_SCHEMA!r})")
    observations = document.get("observations")
    if not isinstance(observations, list):
        raise InputError("input document must contain an 'observations' list")
    if len(observations) > max_observations:
        raise InputError(
            f"input has {len(observations)} observations, over the {max_observations} bound"
        )
    return dict(document)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Aggregate supplied scored observations into a per-risk-stratum "
            "qualification report. Advisory only; approves nothing."
        ),
    )
    parser.add_argument("--input", type=Path, required=True, help="Bounded JSON input document.")
    parser.add_argument("--base", default=None, help="Optional base commit recorded in the report.")
    parser.add_argument("--json", action="store_true", help="Emit the report as JSON on stdout.")
    parser.add_argument(
        "--min-observations",
        type=int,
        default=DEFAULT_MIN_OBSERVATIONS,
        help="Distinct evidence items a stratum needs before it may be reviewed.",
    )
    parser.add_argument(
        "--max-input-bytes", type=int, default=DEFAULT_MAX_INPUT_BYTES,
        help="Reject input larger than this.",
    )
    parser.add_argument(
        "--max-observations", type=int, default=DEFAULT_MAX_OBSERVATIONS,
        help="Reject documents with more observations than this.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        document = load_document(
            args.input,
            max_bytes=args.max_input_bytes,
            max_observations=args.max_observations,
        )
        report = build_report(document, base=args.base, min_observations=args.min_observations)
    except InputError as exc:
        print(f"input error: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True, allow_nan=False))
        return 0

    print(f"observations: {report['input_observations']}  rejected: {len(report['rejected'])}")
    for row in report["strata"]:
        quality = row["quality"]
        print(
            f"  [{row['risk_stratum']}] {row['observed_model']} effort={row['observed_effort']}: "
            f"n={row['accepted_observations']} distinct_evidence={row['distinct_evidence']} "
            f"critical_miss_rate={quality['critical_miss_rate']} "
            f"false_alarm_rate={quality['false_alarm_rate']} "
            f"verdict={row['qualification']['verdict']}"
        )
        print(
            f"      cost monetary={row['cost']['monetary_by_currency']} "
            f"quota={row['cost']['quota_by_pool_unit']}"
        )
    print("pooled_critical_miss_rate: null (pooling across risk strata is not reported)")
    print("approval: automatic=False (requires operator and gate review)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

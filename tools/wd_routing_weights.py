#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""Bridge v2 F26: shadow routing weights from verified outcomes (plan section 2.11).

``derive_shadow_weights(verified_outcomes, signed_bounds, now)`` turns routing outcome
records into one ``wd.routing-shadow-weights.v1`` record: a weight per (task class,
profile). It reads no file, clock, environment, network or bridge. It writes nothing,
and it changes no route, role, flag or veto. Its record always says ``mode``
"shadow", ``authority`` "none", ``activation`` "none" and ``execution_allowed``
False. Only a separate signed F26 activation may ever let a caller use the weights,
and nothing here can produce or imply that activation.

Bounds: ``signed_bounds`` is ``{"bounds": {...}, "sha256": pin}``. The caller takes
the pin from the signed F26 policy. The bounds' canonical digest must equal it, or
the record is ``refused`` with no weights. Hard bounds are never learned.

What counts (everything else is listed in ``rejected`` with a stable reason):
* A closed ``wd.routing-outcome.v1`` record with ``verified`` true, dated no later
  than ``now`` and no older than ``max_outcome_age_seconds``.
* A success or a requalification needs at least ``min_independent_evaluators``
  distinct evaluators other than the worker, so self-grading never counts. Only an
  exact bridge member id counts as an independent evaluator, and identities are
  compared folded (case, padding and the ``_``/``-`` separator), so a spelling of the
  worker is never someone else and two spellings of one member count once. A failure
  needs one evaluator, who may be the worker: a failure can only restrict.
* Dedupe: the same ``outcome_id`` with the same content counts once, and with other
  content it is a conflict. One task (``dispatch_key``) counts once per profile and
  kind. A second outcome id with the same result is a duplicate, and one with
  another result is a conflict. Conflicts are rejected and leave the pair
  ``conflicted``.

Weights: each outcome decays by ``0.5 ** (age / half_life_seconds)``. The raw weight
is ``(successes + prior_strength) / (failures + prior_strength)``, clamped to
``[min_weight, max_weight]`` and rounded to 6 decimals. Neutral is 1.0. A pair with
fewer than ``min_samples`` counted outcomes stays ``unknown`` with no weight.

Stop signal: every accepted failure (``failure``, ``limit_hit`` or
``quality_regression``) quarantines its pair. Only a later requalification with the
independent quorum lifts the quarantine, and ordinary successes never do.
Precedence: quarantined, then conflicted, then unknown or known.

Replay: the record depends only on the inputs (outcome order does not matter).
``evidence_digest`` freezes the bounds pin, ``now`` and every accepted and rejected
outcome. ``replay`` recomputes the record and compares it.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from tools.lane_profile_record import _utc
from tools.wd_composer_select import digest
from tools.wd_task_router import MEMBERS, TASK_CLASSES

SCHEMA = "wd.routing-shadow-weights.v1"
OUTCOME_SCHEMA = "wd.routing-outcome.v1"
BOUNDS_SCHEMA = "wd.routing-weight-bounds.v1"
FEATURE = "F26"

KINDS = ("outcome", "requalification")
RESULTS = ("success", "failure")
STOP_SIGNALS = ("failure", "limit_hit", "quality_regression")
OUTCOME_KEYS = ("schema", "outcome_id", "kind", "dispatch_key", "task_class", "profile_id", "worker", "result",
                "stop_signal", "evaluators", "verified", "evidence_sha256", "observed_utc")
BOUNDS_KEYS = ("schema", "min_weight", "max_weight", "prior_strength", "half_life_seconds",
               "max_outcome_age_seconds", "min_independent_evaluators", "min_samples")
_HEX = frozenset("0123456789abcdef")
_PLACES = 6


def _text(value: Any) -> bool:
    # Exact str: a subclass can redefine equality and pass the member test below.
    return type(value) is str and bool(value)


def _hex64(value: Any) -> bool:
    return type(value) is str and len(value) == 64 and set(value) <= _HEX


def _finite(value: Any) -> bool:
    return type(value) in (int, float) and value == value and value not in (float("inf"), float("-inf"))


def _identity(name: str) -> str:
    """One identity per spelling family: case, padding and the '_'/'-' separator do not change who it is."""
    return name.strip().casefold().replace("_", "-")


def _positive_int(value: Any, minimum: int = 1) -> bool:
    return type(value) is int and value >= minimum


def _bounds(signed_bounds: Any) -> tuple[dict | None, str | None, str]:
    """(bounds, pin, reason). bounds is None when refused."""
    if not (isinstance(signed_bounds, dict) and set(signed_bounds) == {"bounds", "sha256"}):
        return None, None, "bounds_malformed"
    bounds, pin = signed_bounds["bounds"], signed_bounds["sha256"]
    if not _hex64(pin):
        return None, None, "bounds_malformed"
    if not (isinstance(bounds, dict) and set(bounds) == set(BOUNDS_KEYS) and bounds["schema"] == BOUNDS_SCHEMA):
        return None, pin, "bounds_invalid"
    if digest(bounds) != pin:
        return None, pin, "bounds_unbound"
    low, high, prior = bounds["min_weight"], bounds["max_weight"], bounds["prior_strength"]
    if not (_finite(low) and _finite(high) and _finite(prior) and 0 < low <= 1 <= high and prior > 0
            and all(_positive_int(bounds[k]) for k in ("half_life_seconds", "max_outcome_age_seconds",
                                                       "min_independent_evaluators", "min_samples"))):
        return None, pin, "bounds_invalid"
    return bounds, pin, ""


def _check(outcome: Any, bounds: dict, now: datetime) -> tuple[datetime | None, str]:
    """(observed time, "") for a countable outcome, else (None, reason)."""
    if not (isinstance(outcome, dict) and set(outcome) == set(OUTCOME_KEYS) and outcome["schema"] == OUTCOME_SCHEMA):
        return None, "malformed"
    evaluators = outcome["evaluators"]
    if not (_text(outcome["outcome_id"]) and outcome["kind"] in KINDS and _hex64(outcome["dispatch_key"])
            and outcome["task_class"] in TASK_CLASSES and _text(outcome["profile_id"]) and _text(outcome["worker"])
            and outcome["result"] in RESULTS and _hex64(outcome["evidence_sha256"])
            and isinstance(evaluators, list) and evaluators and all(_text(e) for e in evaluators)
            and len(set(evaluators)) == len(evaluators)):
        return None, "malformed"
    failure = outcome["result"] == "failure"
    if (outcome["stop_signal"] in STOP_SIGNALS) != failure or (not failure and outcome["stop_signal"] is not None):
        return None, "malformed"
    if outcome["kind"] == "requalification" and failure:
        return None, "malformed"
    if outcome["verified"] is not True:
        return None, "unverified"
    observed = _utc(outcome["observed_utc"])
    if observed is None:
        return None, "malformed"
    if observed > now:
        return None, "future_dated"
    if (now - observed).total_seconds() > bounds["max_outcome_age_seconds"]:
        return None, "stale"
    # Exact member ids only, folded against the worker and deduplicated by identity (RCO1 SF3).
    independent = {_identity(e) for e in evaluators if e in MEMBERS} - {_identity(outcome["worker"])}
    if not failure and len(independent) < bounds["min_independent_evaluators"]:
        return None, "quorum_not_met"
    return observed, ""


def _refused(reason: str, pin: str | None, now: Any) -> dict:
    record = {"schema": SCHEMA, "feature": FEATURE, "mode": "shadow", "state": "refused", "reasons": [reason],
              "bounds_sha256": pin, "now_utc": now if isinstance(now, str) else None, "weights": [], "rejected": [],
              "duplicates_ignored": 0, "execution_allowed": False, "authority": "none", "activation": "none"}
    record["evidence_digest"] = digest({"state": "refused", "reason": reason, "bounds_sha256": pin,
                                        "now_utc": record["now_utc"]})
    return record


def _derive(verified_outcomes: Any, signed_bounds: Any, now: Any) -> dict:
    moment = _utc(now) if isinstance(now, str) else None
    bounds, pin, reason = _bounds(signed_bounds)
    if bounds is None:
        return _refused(reason, pin, now)
    if moment is None:
        return _refused("now_invalid", pin, now)
    if not isinstance(verified_outcomes, list):
        return _refused("outcomes_malformed", pin, now)

    rejected: list[dict] = []
    conflicted: set = set()
    duplicates = 0
    by_id: dict = {}
    for outcome in verified_outcomes:
        key = outcome.get("outcome_id") if isinstance(outcome, dict) else None
        if not _text(key) or digest(outcome) is None:
            rejected.append({"outcome_id": key if _text(key) else None, "outcome_digest": None,
                             "reason": "malformed"})
            continue
        by_id.setdefault(key, []).append(outcome)

    candidates: list[tuple[dict, datetime]] = []
    for key in sorted(by_id):
        copies = {digest(item): item for item in by_id[key]}
        duplicates += len(by_id[key]) - len(copies)
        if len(copies) > 1:
            for item_digest in sorted(copies):
                item = copies[item_digest]
                rejected.append({"outcome_id": key, "outcome_digest": item_digest, "reason": "conflicting_duplicate"})
                if item.get("task_class") in TASK_CLASSES and _text(item.get("profile_id")):
                    conflicted.add((item["task_class"], item["profile_id"]))
            continue
        item_digest, item = next(iter(copies.items()))
        observed, why = _check(item, bounds, moment)
        if observed is None:
            rejected.append({"outcome_id": key, "outcome_digest": item_digest, "reason": why})
            continue
        candidates.append((item, observed))

    # One task counts once per profile and kind: a second id is a duplicate or a conflict.
    by_task: dict = {}
    for item, observed in candidates:
        by_task.setdefault((item["dispatch_key"], item["profile_id"], item["kind"]), []).append((item, observed))
    accepted: list[tuple[dict, datetime]] = []
    for task_key in sorted(by_task):
        group = sorted(by_task[task_key], key=lambda pair: (pair[1], pair[0]["outcome_id"]))
        results = {(pair[0]["task_class"], pair[0]["result"]) for pair in group}
        if len(results) > 1:
            for item, _ in group:
                rejected.append({"outcome_id": item["outcome_id"], "outcome_digest": digest(item),
                                 "reason": "conflicting_task_outcome"})
                conflicted.add((item["task_class"], item["profile_id"]))
            continue
        accepted.append(group[0])
        for item, _ in group[1:]:
            rejected.append({"outcome_id": item["outcome_id"], "outcome_digest": digest(item),
                             "reason": "duplicate_task_outcome"})

    pairs: dict = {}
    for item, observed in accepted:
        pairs.setdefault((item["task_class"], item["profile_id"]), []).append((item, observed))
    weights = []
    for pair in sorted(set(pairs) | conflicted):
        rows = pairs.get(pair, [])
        samples = [(item, observed) for item, observed in rows if item["kind"] == "outcome"]
        successes = failures = 0.0
        for item, observed in samples:
            decay = 0.5 ** ((moment - observed).total_seconds() / bounds["half_life_seconds"])
            if item["result"] == "success":
                successes += decay
            else:
                failures += decay
        stops = [observed for item, observed in rows if item["result"] == "failure"]
        lifts = [observed for item, observed in rows if item["kind"] == "requalification"]
        raw = (successes + bounds["prior_strength"]) / (failures + bounds["prior_strength"])
        weight = round(min(max(raw, bounds["min_weight"]), bounds["max_weight"]), _PLACES)
        if stops and not (lifts and max(lifts) > max(stops)):
            state, why, weight = "quarantined", ["stop_signal_without_later_requalification"], None
        elif pair in conflicted:
            state, why, weight = "conflicted", ["conflicting_evidence"], None
        elif len(samples) < bounds["min_samples"]:
            state, why, weight = "unknown", ["below_min_samples"], None
        else:
            state, why = "known", []
        weights.append({"task_class": pair[0], "profile_id": pair[1], "state": state, "weight": weight,
                        "reasons": why, "samples": len(samples),
                        "decayed_successes": round(successes, _PLACES), "decayed_failures": round(failures, _PLACES)})

    rejected.sort(key=lambda r: (str(r["outcome_id"]), str(r["outcome_digest"]), r["reason"]))
    now_text = moment.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    record = {"schema": SCHEMA, "feature": FEATURE, "mode": "shadow", "state": "derived", "reasons": [],
              "bounds_sha256": pin, "now_utc": now_text, "weights": weights, "rejected": rejected,
              "duplicates_ignored": duplicates, "execution_allowed": False, "authority": "none",
              "activation": "none"}
    record["evidence_digest"] = digest({
        "bounds_sha256": pin, "now_utc": now_text,
        "accepted": sorted(digest(item) for item, _ in accepted),
        "rejected": [[r["outcome_digest"], r["reason"]] for r in rejected]})
    return record


def derive_shadow_weights(verified_outcomes: Any, signed_bounds: Any, now: Any) -> dict:
    """Shadow weights. Never raises: anything unexpected is a refused record with no weights."""
    try:
        return _derive(verified_outcomes, signed_bounds, now)
    except Exception:  # noqa: BLE001 - learning never raises; anything unexpected refuses
        return _refused("input_malformed", None, now)


def replay(record: Any, verified_outcomes: Any, signed_bounds: Any, now: Any) -> bool:
    """True only if recomputing from the same inputs gives exactly this record."""
    expected = derive_shadow_weights(verified_outcomes, signed_bounds, now)
    return digest(record) is not None and digest(record) == digest(expected)

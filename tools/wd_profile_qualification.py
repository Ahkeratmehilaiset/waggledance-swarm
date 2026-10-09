#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""Bridge v2 F21: minimal profile qualification receipts (plan section 2.1 and the F21 row).

``build_receipt(suite, runs, signed_thresholds, profile, code_sha, now)`` turns one
qualification suite and the harness runs of ONE profile into one
``wd.profile-receipt.v1``. ``validate_receipt`` checks a receipt against the binding a
caller expects. ``router_qualification`` adapts a valid receipt to the F19 router's
``qualification`` entries. All three are pure: they touch no file, clock,
environment, network, provider or model. The harness that runs the cases in
isolated worktrees with no production writes is outside this module; this module
only judges the evidence it produces.

A receipt is bound to the exact code SHA, profile (id, provider, model, effort),
provider version, suite digest, run-set digest and signed-thresholds pin, and
``validate_receipt`` checks every one of them except the run set against the
caller's binding. It expires ``receipt_ttl_seconds`` after its newest counted run. It claims per-class
results on this suite only: ``claims`` always says universal false and
availability not_measured. No receipt, an invalid one or an expired one means the
profile is out of the envelope; a caller never fills the gap with a default.

What counts (everything else is listed in ``rejected`` with a stable reason):
* A closed ``wd.qualification-run.v1`` naming a suite case, this profile, this code
  SHA and provider version, and the case's pinned grader digest (so a result
  graded by anything else, including the profile itself, never counts). It must
  be dated no later than ``now`` and no older than ``max_run_age_seconds``.
* One run that is not ``isolated`` or reports production writes refuses the whole
  receipt: the evidence came from an unsafe harness. That holds even for a run that
  would not count for another reason (malformed, stale, unknown case, other profile),
  and a run object without exactly ``isolated: true`` and an integer
  ``production_writes: 0`` is unsafe (Q1810-D1).
* ``(case_id, repeat)`` counts once. Identical copies are deduplicated, and a
  conflicting copy rejects every copy.

Per class: samples, successes, the Wilson 95% interval and its half-width
(``uncertainty``), repeats per case and the adversarial holdout pass rate. A class
is ``qualified`` only if every signed threshold holds:
* samples at least ``min_samples``;
* every case of the class has at least ``min_repeats`` counted repeats;
* at least ``min_holdout_cases`` holdout cases, passing at ``min_holdout_pass_rate``;
* the Wilson lower bound at least ``min_success_rate``;
* the uncertainty at most ``max_uncertainty``.
A class with no counted run is ``unmeasured``, and it is not qualified.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Any

from tools.lane_profile_record import _utc
from tools.wd_composer_select import digest
from tools.wd_task_router import TASK_CLASSES

RECEIPT_SCHEMA = "wd.profile-receipt.v1"
SUITE_SCHEMA = "wd.qualification-suite.v1"
RUN_SCHEMA = "wd.qualification-run.v1"
THRESHOLDS_SCHEMA = "wd.qualification-thresholds.v1"
FEATURE = "F21"

CASE_KINDS = ("synthetic", "replayed", "adversarial_holdout")
HOLDOUT = "adversarial_holdout"
PROFILE_KEYS = ("profile_id", "provider", "model", "effort", "provider_version")
SUITE_KEYS = ("schema", "suite_id", "version", "cases")
CASE_KEYS = ("case_id", "task_class", "kind", "input_sha256", "grader_sha256")
RUN_KEYS = ("schema", "case_id", "repeat", "profile_id", "provider", "model", "effort", "provider_version",
            "code_sha", "grader_sha256", "passed", "isolated", "production_writes", "transcript_sha256",
            "observed_utc")
THRESHOLD_KEYS = ("schema", "min_samples", "min_repeats", "min_holdout_cases", "min_holdout_pass_rate",
                  "min_success_rate", "max_uncertainty", "receipt_ttl_seconds", "max_run_age_seconds")
BINDING_KEYS = ("code_sha", "suite_sha256", "thresholds_sha256") + PROFILE_KEYS
CLASS_ROW_KEYS = ("task_class", "state", "reasons", "cases", "samples", "successes", "success_rate", "wilson_low",
                  "wilson_high", "uncertainty", "min_repeats_seen", "holdout_cases", "holdout_pass_rate")
EVIDENCE_KEYS = ("suite", "runs", "signed_thresholds", "built_at_utc")
CLAIMS = {"universal": False, "availability": "not_measured", "scope": "this suite, code and profile only"}
Z95 = 1.959963984540054
_PLACES = 6
_HEX = frozenset("0123456789abcdef")


class _Refuse(Exception):
    def __init__(self, *reasons: str) -> None:
        super().__init__(reasons[0] if reasons else "refused")
        self.reasons = list(reasons)


def _require(condition: bool, *reasons: str) -> None:
    if not condition:
        raise _Refuse(*reasons)


def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value)


def _hex(value: Any, length: int) -> bool:
    return isinstance(value, str) and len(value) == length and set(value) <= _HEX


def _rate(value: Any) -> bool:
    return type(value) in (int, float) and 0 <= value <= 1


def _count(value: Any, minimum: int) -> bool:
    return type(value) is int and value >= minimum


def _stamp(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def wilson(successes: int, samples: int) -> tuple[float, float, float]:
    """(low, high, half-width) of the Wilson 95% score interval, rounded to 6 places."""
    p = successes / samples
    z2 = Z95 * Z95
    denominator = 1 + z2 / samples
    center = (p + z2 / (2 * samples)) / denominator
    half = Z95 * math.sqrt(p * (1 - p) / samples + z2 / (4 * samples * samples)) / denominator
    return round(max(center - half, 0.0), _PLACES), round(min(center + half, 1.0), _PLACES), round(half, _PLACES)


def _thresholds(signed: Any) -> tuple[dict, str]:
    _require(isinstance(signed, dict) and set(signed) == {"thresholds", "sha256"} and _hex(signed["sha256"], 64),
             "thresholds_malformed")
    thresholds, pin = signed["thresholds"], signed["sha256"]
    _require(isinstance(thresholds, dict) and set(thresholds) == set(THRESHOLD_KEYS)
             and thresholds["schema"] == THRESHOLDS_SCHEMA, "thresholds_invalid")
    _require(digest(thresholds) == pin, "thresholds_unbound")
    _require(all(_count(thresholds[k], 1) for k in ("min_samples", "min_repeats", "min_holdout_cases",
                                                     "receipt_ttl_seconds", "max_run_age_seconds"))
             and all(_rate(thresholds[k]) for k in ("min_holdout_pass_rate", "min_success_rate", "max_uncertainty")),
             "thresholds_invalid")
    return thresholds, pin


def _suite(suite: Any) -> dict:
    _require(isinstance(suite, dict) and set(suite) == set(SUITE_KEYS) and suite["schema"] == SUITE_SCHEMA
             and _text(suite["suite_id"]) and _text(suite["version"]) and isinstance(suite["cases"], list)
             and bool(suite["cases"]), "suite_malformed")
    cases = {}
    for case in suite["cases"]:
        _require(isinstance(case, dict) and set(case) == set(CASE_KEYS) and _text(case["case_id"])
                 and case["task_class"] in TASK_CLASSES and case["kind"] in CASE_KINDS
                 and _hex(case["input_sha256"], 64) and _hex(case["grader_sha256"], 64), "suite_malformed")
        _require(case["case_id"] not in cases, "suite_duplicate_case")
        cases[case["case_id"]] = case
    return cases


def _profile(profile: Any) -> dict:
    _require(isinstance(profile, dict) and set(profile) == set(PROFILE_KEYS)
             and all(_text(profile[k]) for k in PROFILE_KEYS), "profile_malformed")
    return profile


def _proves_isolation(run: dict) -> bool:
    """Only exactly ``isolated: true`` and an int ``production_writes`` of 0 prove a safe harness; a missing or
    mistyped field proves nothing, so it is unsafe (Q1810-D1)."""
    return run.get("isolated") is True and type(run.get("production_writes")) is int and run["production_writes"] == 0


def _run_reason(run: Any, cases: dict, profile: dict, code_sha: str, thresholds: dict,
                now: datetime) -> tuple[datetime | None, str]:
    # An unsafe harness taints everything it produced, not only this run: checked before any other reason, so a
    # malformed, stale, other-profile or unknown-case unsafe run refuses the receipt too (RCO1 F21-N4; RCO1/RCO2
    # Q1810-D1: the check used to follow the shape and timestamp returns). A run that is not an object carries no
    # harness fields and is only malformed.
    _require(not isinstance(run, dict) or _proves_isolation(run), "isolation_violation", digest(run))
    if not (isinstance(run, dict) and set(run) == set(RUN_KEYS) and run["schema"] == RUN_SCHEMA
            and _text(run["case_id"]) and _count(run["repeat"], 1) and type(run["passed"]) is bool
            and type(run["isolated"]) is bool and _count(run["production_writes"], 0)
            and _hex(run["transcript_sha256"], 64) and _hex(run["grader_sha256"], 64)
            and _hex(run["code_sha"], 40)):
        return None, "malformed"
    observed = _utc(run["observed_utc"])
    if observed is None:
        return None, "malformed"
    case = cases.get(run["case_id"])
    if case is None:
        return None, "unknown_case"
    if any(run[k] != profile[k] for k in PROFILE_KEYS):
        return None, "other_profile"
    if run["code_sha"] != code_sha:
        return None, "other_code"
    if run["grader_sha256"] != case["grader_sha256"]:
        return None, "grader_not_pinned"
    if observed > now:
        return None, "future_dated"
    if now - observed > timedelta(seconds=thresholds["max_run_age_seconds"]):
        return None, "stale"
    return observed, ""


def _class_report(task_class: str, cases: dict, counted: dict, thresholds: dict) -> dict:
    own = sorted(case_id for case_id, case in cases.items() if case["task_class"] == task_class)
    runs = [run for (case_id, _), run in sorted(counted.items()) if cases[case_id]["task_class"] == task_class]
    report = {"task_class": task_class, "state": "unmeasured", "reasons": ["no_counted_runs"], "cases": len(own),
              "samples": len(runs), "successes": sum(1 for run in runs if run["passed"]), "success_rate": None,
              "wilson_low": None, "wilson_high": None, "uncertainty": None, "min_repeats_seen": 0,
              "holdout_cases": sum(1 for case_id in own if cases[case_id]["kind"] == HOLDOUT),
              "holdout_pass_rate": None}
    if not runs:
        return report
    repeats = {case_id: sum(1 for (cid, _) in counted if cid == case_id) for case_id in own}
    report["min_repeats_seen"] = min(repeats.values())
    samples, successes = report["samples"], report["successes"]
    report["success_rate"] = round(successes / samples, _PLACES)
    report["wilson_low"], report["wilson_high"], report["uncertainty"] = wilson(successes, samples)
    holdout_runs = [run for run in runs if cases[run["case_id"]]["kind"] == HOLDOUT]
    if holdout_runs:
        report["holdout_pass_rate"] = round(sum(1 for run in holdout_runs if run["passed"]) / len(holdout_runs),
                                            _PLACES)
    reasons = []
    if samples < thresholds["min_samples"]:
        reasons.append("below_min_samples")
    if report["min_repeats_seen"] < thresholds["min_repeats"]:
        reasons.append("below_min_repeats")
    if report["holdout_cases"] < thresholds["min_holdout_cases"]:
        reasons.append("too_few_holdout_cases")
    elif report["holdout_pass_rate"] is None or report["holdout_pass_rate"] < thresholds["min_holdout_pass_rate"]:
        reasons.append("holdout_below_rate")
    if report["wilson_low"] < thresholds["min_success_rate"]:
        reasons.append("success_lower_bound_below_rate")
    if report["uncertainty"] > thresholds["max_uncertainty"]:
        reasons.append("uncertainty_above_max")
    report["state"], report["reasons"] = ("not_qualified", reasons) if reasons else ("qualified", [])
    return report


def _seal(receipt: dict) -> dict:
    receipt["receipt_sha256"] = digest({k: v for k, v in receipt.items() if k != "receipt_sha256"})
    return receipt


def _refused(reasons: list[str], context: dict) -> dict:
    return _seal({"schema": RECEIPT_SCHEMA, "feature": FEATURE, "state": "refused", "reasons": reasons,
                  "profile": context.get("profile"), "code_sha": context.get("code_sha"),
                  "suite_sha256": context.get("suite_sha256"), "runs_sha256": context.get("runs_sha256"),
                  "thresholds_sha256": context.get("thresholds_sha256"), "measured_at_utc": None,
                  "valid_until_utc": None, "classes": [], "rejected": context.get("rejected", []),
                  "claims": dict(CLAIMS), "authority": "none", "execution_allowed": False})


def _build(suite: Any, runs: Any, signed_thresholds: Any, profile: Any, code_sha: Any, now: Any,
           context: dict) -> dict:
    moment = _utc(now) if isinstance(now, str) else None
    _require(moment is not None, "now_invalid")
    thresholds, context["thresholds_sha256"] = _thresholds(signed_thresholds)
    context["profile"] = dict(_profile(profile))
    _require(_hex(code_sha, 40), "code_sha_malformed")
    context["code_sha"] = code_sha
    cases = _suite(suite)
    context["suite_sha256"] = digest(suite)
    _require(isinstance(runs, list), "runs_malformed")
    run_digests = [digest(run) for run in runs]
    _require(all(d is not None for d in run_digests), "runs_not_canonical_json")
    context["runs_sha256"] = digest(sorted(run_digests))

    rejected: list[dict] = []
    by_slot: dict = {}
    for run, run_digest in sorted(zip(runs, run_digests), key=lambda pair: pair[1]):
        observed, why = _run_reason(run, cases, context["profile"], code_sha, thresholds, moment)
        if observed is None:
            rejected.append({"run_sha256": run_digest, "reason": why})
            continue
        by_slot.setdefault((run["case_id"], run["repeat"]), {})[run_digest] = (run, observed)
    counted: dict = {}
    newest = None
    for slot in sorted(by_slot):
        copies = by_slot[slot]
        if len(copies) > 1:
            rejected.extend({"run_sha256": d, "reason": "conflicting_repeat"} for d in sorted(copies))
            continue
        run, observed = next(iter(copies.values()))
        counted[slot] = run
        newest = observed if newest is None or observed > newest else newest
    context["rejected"] = rejected
    classes = sorted({case["task_class"] for case in cases.values()}, key=TASK_CLASSES.index)
    receipt = {"schema": RECEIPT_SCHEMA, "feature": FEATURE, "state": "built", "reasons": [],
               "profile": context["profile"], "code_sha": code_sha, "suite_sha256": context["suite_sha256"],
               "runs_sha256": context["runs_sha256"], "thresholds_sha256": context["thresholds_sha256"],
               "measured_at_utc": _stamp(newest) if newest else None,
               "valid_until_utc": _stamp(newest + timedelta(seconds=thresholds["receipt_ttl_seconds"]))
               if newest else None,
               "classes": [_class_report(c, cases, counted, thresholds) for c in classes],
               "rejected": rejected, "claims": dict(CLAIMS), "authority": "none", "execution_allowed": False}
    return _seal(receipt)


def build_receipt(suite: Any, runs: Any, signed_thresholds: Any, profile: Any, code_sha: Any, now: Any) -> dict:
    """One receipt for one profile. Never raises: anything unexpected is a refused receipt."""
    context: dict = {}
    try:
        return _build(suite, runs, signed_thresholds, profile, code_sha, now, context)
    except _Refuse as refusal:
        return _refused(refusal.reasons, context)
    except Exception:  # noqa: BLE001 - a receipt never raises; anything unexpected refuses
        return _refused(["input_malformed"], context)


def _class_rows_valid(rows: Any) -> bool:
    """Every class row has the built shape, a unique class, a state that agrees with its reasons and samples, and
    the rates and Wilson interval its own counts give (RCO1 F21-1, F21-N3)."""
    if type(rows) is not list:
        return False
    seen: set = set()
    for row in rows:
        if type(row) is not dict or set(row) != set(CLASS_ROW_KEYS) or type(row["task_class"]) is not str \
                or row["task_class"] not in TASK_CLASSES or row["task_class"] in seen:
            return False
        seen.add(row["task_class"])
        if not all(_count(row[k], 0) for k in ("cases", "samples", "successes", "min_repeats_seen", "holdout_cases")):
            return False
        samples, successes, reasons = row["samples"], row["successes"], row["reasons"]
        if successes > samples or type(reasons) is not list or not all(_text(reason) for reason in reasons):
            return False
        if row["state"] == "unmeasured":
            if samples != 0 or reasons != ["no_counted_runs"]:
                return False
            continue
        if samples == 0 or row["state"] not in ("qualified", "not_qualified") \
                or (row["state"] == "qualified") == bool(reasons):
            return False
        if row["success_rate"] != round(successes / samples, _PLACES) \
                or (row["wilson_low"], row["wilson_high"], row["uncertainty"]) != wilson(successes, samples):
            return False
    return True


def validate_receipt(receipt: Any, binding: Any, now: Any) -> dict:
    """{"valid": bool, "reasons": [...]} for one receipt against the caller's expected binding.

    ``binding`` names what the caller will use: the exact ``code_sha``, the profile
    fields (``profile_id``, ``provider``, ``model``, ``effort``, ``provider_version``),
    and the signed ``suite_sha256`` and ``thresholds_sha256``. So a receipt from
    another suite or from laxer thresholds never validates.

    ``receipt_sha256`` is the receipt's digest of itself: integrity, NOT origin. A resealed or fabricated receipt
    with consistent rows validates here; only ``replay`` from trusted inputs proves where it came from, and
    ``router_qualification`` requires that replay (RCO1 F21-1)."""
    reasons = []
    try:
        moment = _utc(now) if isinstance(now, str) else None
        if moment is None:
            return {"valid": False, "reasons": ["now_invalid"]}
        if not (isinstance(receipt, dict) and receipt.get("schema") == RECEIPT_SCHEMA
                and receipt.get("state") == "built"):
            return {"valid": False, "reasons": ["receipt_not_built"]}
        # A None digest (non-canonical JSON such as NaN) or a missing one never compares equal (RCO1 F21-2).
        recomputed = digest({k: v for k, v in receipt.items() if k != "receipt_sha256"})
        if not _hex(receipt.get("receipt_sha256"), 64) or recomputed is None \
                or receipt["receipt_sha256"] != recomputed:
            return {"valid": False, "reasons": ["receipt_digest_mismatch"]}
        if not _class_rows_valid(receipt.get("classes")):
            reasons.append("class_rows_malformed")
        if not (isinstance(binding, dict) and set(binding) == set(BINDING_KEYS)):
            return {"valid": False, "reasons": ["binding_malformed"]}
        for key in ("code_sha", "suite_sha256", "thresholds_sha256"):
            if receipt.get(key) != binding[key]:
                reasons.append(key + "_mismatch")
        profile = receipt.get("profile") if isinstance(receipt.get("profile"), dict) else {}
        reasons.extend("profile_mismatch:" + k for k in PROFILE_KEYS if profile.get(k) != binding[k])
        if receipt.get("claims") != CLAIMS or receipt.get("authority") != "none" \
                or receipt.get("execution_allowed") is not False:
            reasons.append("claims_overreach")
        measured, until = _utc(receipt.get("measured_at_utc")), _utc(receipt.get("valid_until_utc"))
        if measured is None or until is None or not measured <= moment < until:
            reasons.append("receipt_expired_or_undated")
    except Exception:  # noqa: BLE001 - validation never raises
        return {"valid": False, "reasons": ["input_malformed"]}
    return {"valid": not reasons, "reasons": reasons}


def router_qualification(receipt: Any, binding: Any, now: Any, evidence: Any = None) -> list[dict]:
    """The F19 router's ``qualification`` entries for one receipt, or [] unless it is valid AND proven.

    Proof is deterministic replay from trusted evidence the caller supplies itself, never the receipt's own
    digest: ``evidence`` is {"suite", "runs", "signed_thresholds", "built_at_utc"} (the ``now`` the receipt was
    built at), and the receipt must rebuild byte-identically from it with the binding's profile and code SHA.
    No evidence, any mismatch or anything unexpected gives [], so the router keeps every class unknown
    (RCO1 F21-1). An ``unmeasured`` class yields no entry, so the router keeps it unknown."""
    try:
        if not validate_receipt(receipt, binding, now)["valid"]:
            return []
        if type(evidence) is not dict or set(evidence) != set(EVIDENCE_KEYS):
            return []
        profile = {key: binding[key] for key in PROFILE_KEYS}
        if not replay(receipt, evidence["suite"], evidence["runs"], evidence["signed_thresholds"], profile,
                      binding["code_sha"], evidence["built_at_utc"]):
            return []
        return [{"task_class": row["task_class"], "profile_id": receipt["profile"]["profile_id"],
                 "qualified": row["state"] == "qualified", "receipt_sha256": receipt["receipt_sha256"],
                 "observed_utc": receipt["measured_at_utc"], "valid_until_utc": receipt["valid_until_utc"]}
                for row in receipt["classes"] if row["state"] in ("qualified", "not_qualified")]
    except Exception:  # noqa: BLE001 - a malformed receipt or evidence yields no entry, never a crash
        return []


def replay(receipt: Any, suite: Any, runs: Any, signed_thresholds: Any, profile: Any, code_sha: Any,
           now: Any) -> bool:
    """True only if rebuilding from the same inputs gives exactly this receipt."""
    rebuilt = build_receipt(suite, runs, signed_thresholds, profile, code_sha, now)
    return digest(receipt) is not None and digest(receipt) == digest(rebuilt)

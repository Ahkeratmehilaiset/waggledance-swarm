#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""P4b post-merge canary RUNNER (classification core) — DORMANT / UNWIRED.

Implements the classification + debounce + fail-safe logic of the P4b canary
defined by ``docs/architecture/P4B_POST_MERGE_CANARY_V1.md`` (PR #1390). **Consulted
by nothing yet** — pure, testable logic with NO IO (no test execution, no git, no
network). The actual canary-set EXECUTION + error-rate sampling + the emit-to-P4a
wiring are a SEPARATE off-allowlist piece (tools/lead), exactly as P4a's #1389
keeps the revert+push wiring separate from its dormant eligibility core.

Given the RESULTS of one-or-more canary runs of a single merged SHA, ``classify``
returns one of:
  PASS               canary set green within budget                -> no signal
  SUSPECT            a regress seen but NOT yet debounced          -> no signal (logged)
  CONFIRMED_REGRESS  debounced same-cause regress, FP within bound -> emit P4a signal
  INCONCLUSIVE       timeout / unrunnable / FP over threshold      -> NO signal, escalate

FAIL-SAFE on the destructive side: only a positively CONFIRMED, within-FP-budget,
same-cause regress emits a P4a rollback trigger. Uncertainty NEVER triggers a
rollback (a false trigger reverts a GOOD merge; a miss is backstopped by human MTTR).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

PASS = "PASS"
SUSPECT = "SUSPECT"
CONFIRMED_REGRESS = "CONFIRMED_REGRESS"
INCONCLUSIVE = "INCONCLUSIVE"

# Spec §4: P4a must never fire on a single (flaky) run. Floor aligns with the P4a
# debounce floor (#1389). No caller may lower it below 2.
MIN_CONFIRMATIONS_FLOOR = 2
# Spec §5: acceptance FP-rate threshold.
DEFAULT_FP_THRESHOLD = 0.01


@dataclass
class CanaryVerdict:
    outcome: str
    failing_check_ids: list = field(default_factory=list)
    confirmations: int = 0
    fp_rate: float = 0.0
    p4a_signal: dict | None = None      # populated ONLY on CONFIRMED_REGRESS
    reason: str = ""

    def as_dict(self) -> dict:
        return {
            "outcome": self.outcome,
            "failing_check_ids": self.failing_check_ids,
            "confirmations": self.confirmations,
            "fp_rate": self.fp_rate,
            "p4a_signal": self.p4a_signal,
            "reason": self.reason,
        }


def _run_failures(run: dict) -> set:
    """The set of failing check_ids in a single run (a check maps to a bool PASS)."""
    checks = run.get("checks") or {}
    return {cid for cid, ok in checks.items() if not ok}


def _run_usable(run: dict) -> bool:
    """A run counts toward a verdict only if it COMPLETED within budget. A run that
    timed out / was cut by the wall-clock cap / failed to run is unusable (it can
    neither pass nor confirm a regress) -> drives INCONCLUSIVE (fail-safe)."""
    return bool(run.get("completed")) and bool(run.get("within_budget"))


def classify(
    runs: Sequence[dict],
    *,
    merged_sha: str,
    prior_good_sha: str = "",
    required_confirmations: int = MIN_CONFIRMATIONS_FLOOR,
    fp_rate: float = 0.0,
    fp_threshold: float = DEFAULT_FP_THRESHOLD,
    canary_manifest_version: str = "",
    receipt_ref: str = "",
) -> CanaryVerdict:
    """Classify a merged SHA's canary runs. Pure + fail-safe. ``runs`` is a list of
    ``{"checks": {check_id: bool}, "completed": bool, "within_budget": bool}``.

    Order (fail-safe first): any unusable run OR an over-threshold FP-rate ->
    INCONCLUSIVE (never a rollback trigger). Else: no failures -> PASS; a same-cause
    failure confirmed on >= required_confirmations usable runs (floor 2) ->
    CONFIRMED_REGRESS (+ P4a signal); a failure not yet debounced -> SUSPECT.
    """
    required = max(int(required_confirmations), MIN_CONFIRMATIONS_FLOOR)

    if not runs:
        return CanaryVerdict(INCONCLUSIVE, fp_rate=fp_rate,
                             reason="no canary runs -> cannot confirm; escalate")

    # Fail-safe gate 1: every run must be usable. An unrunnable/timed-out run means
    # we cannot positively confirm health OR a regress -> INCONCLUSIVE, no trigger.
    if any(not _run_usable(r) for r in runs):
        return CanaryVerdict(
            INCONCLUSIVE, fp_rate=fp_rate,
            reason="a canary run exceeded budget / did not complete -> escalate, no rollback")

    # Fail-safe gate 2: an over-threshold FP-rate disqualifies this canary from
    # triggering auto-rollback (spec §5) -> INCONCLUSIVE (may alert, never acts).
    if fp_rate > fp_threshold:
        return CanaryVerdict(
            INCONCLUSIVE, fp_rate=fp_rate,
            reason=f"fp_rate {fp_rate} > threshold {fp_threshold} -> not P4a-eligible")

    # Per-check confirmation count across usable runs (same-cause binding).
    counts: dict[str, int] = {}
    for r in runs:
        for cid in _run_failures(r):
            counts[cid] = counts.get(cid, 0) + 1

    if not counts:
        return CanaryVerdict(PASS, fp_rate=fp_rate,
                             reason=f"canary green on {len(runs)} run(s)")

    confirmed = {cid: n for cid, n in counts.items() if n >= required}
    if confirmed:
        failing = sorted(confirmed)
        confirmations = min(confirmed.values())
        signal = build_p4a_signal(
            merged_sha=merged_sha, prior_good_sha=prior_good_sha,
            failing_check_ids=failing, confirmations=confirmations,
            fp_rate=fp_rate, canary_manifest_version=canary_manifest_version,
            receipt_ref=receipt_ref,
        )
        return CanaryVerdict(
            CONFIRMED_REGRESS, failing_check_ids=failing, confirmations=confirmations,
            fp_rate=fp_rate, p4a_signal=signal,
            reason=f"same-cause regress confirmed on >={required} runs: {failing}")

    # A failure exists but is not debounced to the floor (could be flaky).
    return CanaryVerdict(
        SUSPECT, failing_check_ids=sorted(counts), confirmations=max(counts.values()),
        fp_rate=fp_rate,
        reason=f"regress seen but < {required} same-cause confirmations -> suspect, no trigger")


def build_p4a_signal(
    *, merged_sha: str, prior_good_sha: str, failing_check_ids: Sequence[str],
    confirmations: int, fp_rate: float, canary_manifest_version: str,
    receipt_ref: str,
) -> dict:
    """The structured signal P4a consumes (spec §7). Structured fields only — no
    free-text authority (per the P2/D5 taxonomy). P4a independently re-verifies its
    pure-revert-to-known-green tree-equality (#1389); it does not trust a bare flag."""
    return {
        "kind": "p4b_confirmed_regress",
        "merged_sha": merged_sha,
        "prior_good_sha": prior_good_sha,
        "failing_check_ids": list(failing_check_ids),
        "confirmations": confirmations,
        "fp_rate_at_emit": fp_rate,
        "canary_manifest_version": canary_manifest_version,
        "receipt_ref": receipt_ref,
    }


def canary_receipt(verdict: CanaryVerdict, *, merged_sha: str,
                   canary_manifest_version: str, budget_seconds_used: float = 0.0,
                   run_count: int = 0) -> dict:
    """MAGMA canary receipt (spec §8): re-derivable record of one canary evaluation."""
    return {
        "type": "p4b_canary_receipt",
        "merged_sha": merged_sha,
        "outcome": verdict.outcome,
        "canary_manifest_version": canary_manifest_version,
        "failing_check_ids": verdict.failing_check_ids,
        "confirmations": verdict.confirmations,
        "fp_rate": verdict.fp_rate,
        "run_count": run_count,
        "budget_seconds_used": budget_seconds_used,
        "p4a_trigger_emitted": verdict.outcome == CONFIRMED_REGRESS,
        "reason": verdict.reason,
    }

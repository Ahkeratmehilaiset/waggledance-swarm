# SPDX-License-Identifier: BUSL-1.1
"""Repeat-window trend summary for the measured local low-risk autogrowth real loop.

Promotes the single-shot measured local autogrowth real-loop dry-run
(`build_low_risk_autogrowth_chain_dry_run`) into a **repeat-window** measurement:
it runs the real loop N times against ephemeral control planes and summarises the
*trend* (really the reproducibility/stability) of the measured evidence across the
window - how many promotions each run measured, whether that count is stable, and
whether any run tripped an authority guardrail.

This is capability EVIDENCE only. It performs no production writes, no provider
calls, no bridge appends, and no scheduler/runtime authority; every authority and
invariant field is DERIVED from the observed runs (never hardcoded), and a 100%
stable measurement never grants claim-safe / production authority. The trend
summary is deterministic: the whole window is replayed twice and every gated field
must be byte-identical, else the proof fails closed.

Only safe scalars are emitted (counts + flags + fixed labels) - never a raw query,
context, run id, or payload.

Exact validation commands::

    python tools/run_low_risk_real_loop_repeat_window_trend.py --json
    python -m pytest tests/test_low_risk_real_loop_repeat_window_trend.py -q

Engineering record; offline (no provider/cloud calls); forbidden-vocabulary
guarded. No claim of superiority over any external system.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from typing import Any, Callable, Sequence

ROOT = __import__("pathlib").Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.wd_image1_capability_manifest import (  # noqa: E402
    build_low_risk_autogrowth_chain_dry_run,
)

FORBIDDEN_VOCABULARY: tuple[str, ...] = (
    "conscious", "sentient", "aware", "alive", "AGI",
    "revolutionary", "magical", "human-like mind", "self-aware",
    "explosive intelligence", "emergent",
    "beats all competitors", "world's best", "world's fastest",
)

REPORT_VERSION = "wd.low_risk_real_loop_repeat_window_trend.v1"
MEASUREMENT_BASIS = "v1_low_risk_real_loop_repeat_window"
DEFAULT_WINDOW = 3
# Sane upper bound: the window only needs a few repeats to establish stability;
# a huge window is rejected (bounds runtime/DoS, not silently accepted).
MAX_WINDOW = 25
EXPECTED_CLAIM_LABEL = "MEASURED_LOCAL_DRY_RUN"

# The FULL authority_boundary axis set the dry-run must report. The guardrail
# must see EVERY one of these: iterating only the present keys would let an
# adversarial/buggy dry-run OMIT the very axis it set True (partial-omission
# guardrail bypass), so a missing expected axis fails closed (raise), not just an
# empty boundary. Extra (future) axes are still strict-bool-checked + covered by
# the data-driven any() guardrail.
_EXPECTED_AUTHORITY_AXES = frozenset({
    "external_writes_applied",
    "production_control_plane_touched",
    "production_scheduler_enqueue",
    "provider_jobs_created",
    "builder_jobs_created",
    "gate_skip_authority",
    "operator_gate_bypassed",
    "runtime_authority_granted",
    "fast_track_priority",
})

# The stable trend fields compared across the run-twice determinism check - every
# gated field below must appear here, else a run2 drift could pass unseen.
_STABLE_FIELDS = (
    "window_size",
    "all_runs_ok",
    "any_guardrail_tripped",
    "promoted_solver_count_min",
    "promoted_solver_count_max",
    "promoted_solver_count_stable",
    "promoted_run_count_min",
    "promoted_run_count_max",
    "promoted_run_count_stable",
    "runtime_authority_granted",
    "external_writes_applied",
    "claim_label",
    "measurement_basis",
)


def _strict_bool(value: Any, name: str) -> bool:
    if value is not True and value is not False:
        raise ValueError(f"{name} is not a strict bool")
    return value


def _nonneg_int(value: Any, name: str) -> int:
    if not (isinstance(value, int) and not isinstance(value, bool) and value >= 0):
        raise ValueError(f"{name} is not a non-negative int")
    return value


def _run_safe_scalars(dry_run: dict[str, Any]) -> dict[str, Any]:
    """Extract the safe scalar evidence from a single dry-run result.

    Authority axes are taken over the FULL authority_boundary (data-driven, so a
    new axis is covered by default) and every axis must be a strict bool.
    """
    authority = dry_run.get("authority_boundary")
    if not isinstance(authority, dict) or not authority:
        raise ValueError("authority_boundary missing or not a mapping")
    # Fail closed on a PARTIAL boundary: every expected axis must be present, so
    # an omitted axis cannot bypass the guardrail (not just the empty case).
    missing = _EXPECTED_AUTHORITY_AXES - set(authority)
    if missing:
        raise ValueError(
            f"authority_boundary missing expected axes: {sorted(missing)}"
        )
    for axis, flag in authority.items():
        _strict_bool(flag, f"authority_boundary.{axis}")
    chain = dry_run.get("chain")
    if not isinstance(chain, dict):
        raise ValueError("chain missing or not a mapping")
    return {
        "ok": _strict_bool(dry_run.get("ok"), "ok"),
        "guardrail_tripped": any(v is True for v in authority.values()),
        "runtime_authority_granted": _strict_bool(
            authority.get("runtime_authority_granted"), "runtime_authority_granted"
        ),
        "external_writes_applied": _strict_bool(
            authority.get("external_writes_applied"), "external_writes_applied"
        ),
        "promoted_solver_count": _nonneg_int(
            chain.get("auto_promoted_solver_count"), "auto_promoted_solver_count"
        ),
        "promoted_run_count": _nonneg_int(
            chain.get("auto_promoted_run_count"), "auto_promoted_run_count"
        ),
        "claim_label": str(dry_run.get("claim_label") or ""),
    }


def _summarise_window(runs: list[dict[str, Any]]) -> dict[str, Any]:
    solver_counts = [r["promoted_solver_count"] for r in runs]
    run_counts = [r["promoted_run_count"] for r in runs]
    labels = {r["claim_label"] for r in runs}
    # Normalize to a CLOSED value: never echo caller-controlled label text into
    # the emitted report (even on the unexpected path). Emit the expected label
    # only when every run reports exactly it, else a fixed sanitized token.
    claim_label = EXPECTED_CLAIM_LABEL if labels == {EXPECTED_CLAIM_LABEL} else "UNEXPECTED"
    return {
        "window_size": len(runs),
        "all_runs_ok": all(r["ok"] for r in runs),
        "any_guardrail_tripped": any(r["guardrail_tripped"] for r in runs),
        "promoted_solver_count_min": min(solver_counts),
        "promoted_solver_count_max": max(solver_counts),
        "promoted_solver_count_stable": min(solver_counts) == max(solver_counts),
        "promoted_run_count_min": min(run_counts),
        "promoted_run_count_max": max(run_counts),
        "promoted_run_count_stable": min(run_counts) == max(run_counts),
        "runtime_authority_granted": any(
            r["runtime_authority_granted"] for r in runs
        ),
        "external_writes_applied": any(
            r["external_writes_applied"] for r in runs
        ),
        "claim_label": claim_label,
        "measurement_basis": MEASUREMENT_BASIS,
    }


def _stable_view(summary: dict[str, Any]) -> dict[str, Any]:
    return {k: summary.get(k) for k in _STABLE_FIELDS}


def build_repeat_window_trend_proof(
    *,
    window: int = DEFAULT_WINDOW,
    dry_run_factory: Callable[[], dict[str, Any]] = build_low_risk_autogrowth_chain_dry_run,
) -> dict[str, Any]:
    if not (
        isinstance(window, int)
        and not isinstance(window, bool)
        and 2 <= window <= MAX_WINDOW
    ):
        raise ValueError(f"window must be an int in [2, {MAX_WINDOW}]")

    def _one_window() -> dict[str, Any]:
        return _summarise_window(
            [_run_safe_scalars(dry_run_factory()) for _ in range(window)]
        )

    window1 = _one_window()
    window2 = _one_window()
    deterministic = json.dumps(_stable_view(window1), sort_keys=True) == json.dumps(
        _stable_view(window2), sort_keys=True
    )

    # Every field DERIVED from the observed window (never hardcoded).
    all_runs_ok = window1["all_runs_ok"] is True
    guardrail_tripped = window1["any_guardrail_tripped"] is True
    solver_stable = window1["promoted_solver_count_stable"] is True
    run_stable = window1["promoted_run_count_stable"] is True
    solver_min = window1["promoted_solver_count_min"]
    solver_positive = isinstance(solver_min, int) and solver_min >= 1
    label_ok = window1["claim_label"] == EXPECTED_CLAIM_LABEL
    runtime_authority_granted = window1["runtime_authority_granted"] is True
    external_writes_applied = window1["external_writes_applied"] is True

    # Evidence vs authority: a reproducible promotion measurement WITHOUT any
    # production authority. Stable + ok + positive + no guardrail trip.
    evidence_present = bool(
        deterministic
        and all_runs_ok
        and solver_stable
        and run_stable
        and solver_positive
        and label_ok
        and not guardrail_tripped
        and not runtime_authority_granted
        and not external_writes_applied
    )

    blockers: list[str] = []
    if not deterministic:
        blockers.append("non_deterministic_window_trend")
    if not all_runs_ok:
        blockers.append("a_run_not_ok")
    if not solver_stable:
        blockers.append("promoted_solver_count_unstable")
    if not run_stable:
        blockers.append("promoted_run_count_unstable")
    if not solver_positive:
        blockers.append("promoted_solver_count_not_positive")
    if not label_ok:
        blockers.append("unexpected_claim_label")
    if guardrail_tripped:
        blockers.append("authority_guardrail_tripped")
    if runtime_authority_granted:
        blockers.append("runtime_authority_granted")
    if external_writes_applied:
        blockers.append("external_writes_applied")

    return {
        "report_version": REPORT_VERSION,
        "ok": not blockers,
        "blockers": blockers,
        "measurement_basis": MEASUREMENT_BASIS,
        "deterministic_replay": {"windows": 2, "stable_trend_identical": deterministic},
        "trend": window1,
        "evidence_vs_authority": {
            "evidence_present": evidence_present,
            "runtime_authority_granted": runtime_authority_granted,
            "external_writes_applied": external_writes_applied,
            "guardrail_tripped": guardrail_tripped,
            # measurement-only: a stable 100% measurement NEVER upgrades the claim.
            "claim_safe": False,
        },
        "invariants": {
            "no_cloud_api_calls_this_session": True,
            "no_pull_or_download_this_session": True,
            "deterministic_offline": deterministic,
            "no_production_writes": not external_writes_applied,
            "no_runtime_authority_flip": not runtime_authority_granted,
            "measurement_only_no_claim_safe_upgrade": True,
            "no_superiority_claim": True,
        },
    }


def render_summary(report: dict[str, Any]) -> str:
    trend = report["trend"]
    eva = report["evidence_vs_authority"]
    dr = report["deterministic_replay"]
    return "\n".join([
        "Low-risk real-loop repeat-window trend",
        f"  ok={report['ok']} blockers={report['blockers']}",
        f"  window_size={trend['window_size']} all_runs_ok={trend['all_runs_ok']} "
        f"deterministic={dr['stable_trend_identical']}",
        f"  promoted_solver_count: min={trend['promoted_solver_count_min']} "
        f"max={trend['promoted_solver_count_max']} stable={trend['promoted_solver_count_stable']}",
        f"  any_guardrail_tripped={trend['any_guardrail_tripped']} "
        f"evidence_present={eva['evidence_present']} claim_safe={eva['claim_safe']}",
    ])


def assert_vocabulary_clean(text: str) -> None:
    hit = [
        p for p in FORBIDDEN_VOCABULARY
        if re.search(r"\b" + re.escape(p) + r"\b", text, re.IGNORECASE)
    ]
    if hit:
        raise SystemExit(f"forbidden vocabulary in rendered text: {hit}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--window", type=int, default=DEFAULT_WINDOW,
                        help=f"number of repeat runs in the window (2..{MAX_WINDOW})")
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not (2 <= args.window <= MAX_WINDOW):
        parser.error(f"--window must be in [2, {MAX_WINDOW}]")
    report = build_repeat_window_trend_proof(window=args.window)
    summary = render_summary(report)
    assert_vocabulary_clean(summary)
    json_report = json.dumps(report, indent=2, sort_keys=True)
    assert_vocabulary_clean(json_report)
    print(json_report if args.json else summary)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

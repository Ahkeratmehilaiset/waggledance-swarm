# SPDX-License-Identifier: BUSL-1.1
"""Deterministic offline proof of the real low-risk autogrowth loop.

Ties ``RuntimeGapDetector -> AutogrowthScheduler -> LowRiskGrower/AutoPromotion``
into reproducible WD Image #1 panel-3 (low-risk autonomy loop) evidence WITHOUT
granting runtime authority, writing externally, calling providers, enqueuing the
production scheduler, or flipping any production claim.

How it works (no new chain wiring, no renderer/meta-tool layer):

* It reuses the merged ``run_low_risk_autogrowth_chain_dry_run`` (which exercises
  the real detector -> digest -> scheduler -> dispatch path against a fresh,
  ephemeral, auto-deleted control plane).
* It runs that chain twice under a fixed clock and proves the chain evidence is
  byte-identical: **deterministic replay**.
* It checks the loop produced *correct* evidence (the auto-promoted solver
  computes the expected output), then emits a ``manifest_contribution`` that
  explicitly separates EVIDENCE (the loop ran correctly) from PRODUCTION
  AUTHORITY (which stays false). ``claim_safe`` stays ``false`` — a local proof
  is capability evidence, never production runtime authority.

Exact validation commands::

    python tools/run_low_risk_autogrowth_real_loop_proof.py --json
    python -m pytest tests/test_low_risk_autogrowth_real_loop_proof.py -q

This is an engineering record. It runs fully offline (no cloud/provider calls)
and emits a forbidden-vocabulary-guarded JSON envelope. It does not assert
WaggleDance is faster or superior to any external system.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.run_low_risk_autogrowth_chain_dry_run import (  # noqa: E402
    build_low_risk_autogrowth_chain_dry_run,
)

FORBIDDEN_VOCABULARY: tuple[str, ...] = (
    "conscious", "sentient", "aware", "alive", "AGI",
    "revolutionary", "magical", "human-like mind", "self-aware",
    "explosive intelligence", "emergent",
    "beats all competitors", "world's best", "world's fastest",
)

REPORT_VERSION = "wd.low_risk_autogrowth_real_loop_proof.v1"
CAPABILITY_ID = "low_risk_autonomy_loop"
# Fixed clock so the determinism check isolates *chain* determinism from wall
# clock. The chain logic, not the timestamp, is what must replay identically.
FIXED_CLOCK_UTC = "2026-01-01T00:00:00Z"
RUNTIME_PATH = (
    "RuntimeGapDetector.record -> digest_signals_into_intents -> "
    "AutogrowthScheduler.tick -> LowRiskGrower/AutoPromotion"
)


def _utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_utc(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def _evidence_view(report: dict[str, Any]) -> dict[str, Any]:
    """The deterministic, environment-independent slice of a dry-run report."""
    return {
        "ok": report.get("ok"),
        "chain": report.get("chain"),
        "dispatch": report.get("dispatch"),
        "authority_boundary": report.get("authority_boundary"),
        "no_overclaim_guardrails": report.get("no_overclaim_guardrails"),
    }


def build_real_loop_proof(*, now_utc: datetime | None = None) -> dict[str, Any]:
    clock = now_utc or _parse_utc(FIXED_CLOCK_UTC)
    # out_dir omitted -> each run uses an ephemeral auto-deleted temp control
    # plane; nothing is written outside that temp dir.
    run1 = build_low_risk_autogrowth_chain_dry_run(now_utc=clock)
    run2 = build_low_risk_autogrowth_chain_dry_run(now_utc=clock)

    v1 = json.dumps(_evidence_view(run1), sort_keys=True)
    v2 = json.dumps(_evidence_view(run2), sort_keys=True)
    deterministic = v1 == v2

    chain = run1.get("chain", {})
    dispatch = run1.get("dispatch", {})
    authority = run1.get("authority_boundary", {})

    chain_complete = bool(
        run1.get("ok")
        and chain.get("detector_signals_recorded", 0) >= 1
        and chain.get("intents_created", 0) >= 1
        and chain.get("scheduler_outcome") == "auto_promoted"
        and chain.get("auto_promoted_solver_count", 0) >= 1
    )
    dispatch_correct = bool(
        dispatch.get("matched") is True
        and dispatch.get("output") == dispatch.get("expected_output")
    )
    authority_held_closed = bool(authority) and all(
        v is False for v in authority.values()
    )
    evidence_present = bool(
        deterministic and chain_complete and dispatch_correct and authority_held_closed
    )

    # Single source of truth: derive every authority/invariant flag from the
    # observed authority_boundary (never hardcode "safe"). If the chain ever
    # leaked authority, these flags reflect it AND ok becomes False below, so a
    # downstream counter that reads manifest_contribution standalone still sees
    # the leak. claim_safe is policy-false: a local proof is never production
    # authority, so this proof can never upgrade the literal claim.
    runtime_authority_granted = bool(authority.get("runtime_authority_granted"))
    external_writes_applied = bool(authority.get("external_writes_applied"))
    scheduler_enqueue = bool(authority.get("production_scheduler_enqueue"))
    production_flip = bool(
        authority.get("production_control_plane_touched")
        or authority.get("operator_gate_bypassed")
        or authority.get("gate_skip_authority")
    )
    provider_calls = 1 if authority.get("provider_jobs_created") else 0
    claim_safe = False

    blockers: list[str] = []
    if not deterministic:
        blockers.append("non_deterministic_replay")
    if not chain_complete:
        blockers.append("chain_incomplete")
    if not dispatch_correct:
        blockers.append("dispatch_output_mismatch")
    if not authority_held_closed:
        blockers.append("authority_flag_open")

    return {
        "report_version": REPORT_VERSION,
        "generated_at_utc": _utc_iso(),
        "ok": not blockers,
        "blockers": blockers,
        "capability_id": CAPABILITY_ID,
        "runtime_path": RUNTIME_PATH,
        "deterministic_replay": {
            "runs": 2,
            "fixed_clock_utc": FIXED_CLOCK_UTC,
            "evidence_identical": deterministic,
        },
        "chain_evidence": chain,
        "dispatch_evidence": {
            "matched": dispatch.get("matched"),
            "output": dispatch.get("output"),
            "expected_output": dispatch.get("expected_output"),
            "output_correct": dispatch_correct,
        },
        # The point of the slice: evidence is NOT production authority.
        "evidence_vs_authority": {
            "evidence_present": evidence_present,
            "authority_boundary": authority,
            "production_authority_granted": any(authority.values()) if authority else False,
        },
        # Counter-shaped contribution for the WD Image #1 low-risk-autonomy
        # capability: it counts EVIDENCE and never infers authority. A consumer
        # must keep claim_safe false on the basis of this proof alone.
        "manifest_contribution": {
            "capability_id": CAPABILITY_ID,
            "evidence_present": evidence_present,
            "runtime_authority_granted": runtime_authority_granted,
            "external_writes_applied": external_writes_applied,
            "scheduler_enqueue": scheduler_enqueue,
            "production_flip": production_flip,
            "provider_calls": provider_calls,
            "claim_safe": claim_safe,
            "claim_safe_rationale": (
                "local deterministic offline proof is capability evidence, not "
                "production runtime authority; the literal claim stays unsafe "
                "until production runtime evidence supports it"
            ),
        },
        "invariants": {
            "no_cloud_api_calls_this_session": provider_calls == 0,
            # The proof performs no network I/O; no observed-authority signal
            # exists for pull/download, so this is a structural property.
            "no_pull_or_download_this_session": True,
            "deterministic_offline": deterministic,
            "no_external_writes": not external_writes_applied,
            "no_runtime_authority_flip": not runtime_authority_granted,
            "no_scheduler_enqueue": not scheduler_enqueue,
            "no_production_flip": not production_flip,
            "no_claim_safe_flip": not claim_safe,
            "forbidden_vocabulary_excluded": list(FORBIDDEN_VOCABULARY),
        },
    }


def render_summary(report: dict[str, Any]) -> str:
    dr = report["deterministic_replay"]
    ce = report["chain_evidence"]
    de = report["dispatch_evidence"]
    mc = report["manifest_contribution"]
    return "\n".join([
        "Low-risk autogrowth real-loop proof",
        f"  ok={report['ok']} blockers={report['blockers']}",
        f"  runtime_path={report['runtime_path']}",
        f"  deterministic_replay: runs={dr['runs']} identical={dr['evidence_identical']}",
        f"  chain: detector={ce.get('detector_signals_recorded')} intents={ce.get('intents_created')} "
        f"outcome={ce.get('scheduler_outcome')} auto_promoted={ce.get('auto_promoted_solver_count')}",
        f"  dispatch: matched={de['matched']} output={de['output']} expected={de['expected_output']} correct={de['output_correct']}",
        f"  evidence_present={mc['evidence_present']} | runtime_authority_granted={mc['runtime_authority_granted']} "
        f"claim_safe={mc['claim_safe']}",
    ])


def assert_vocabulary_clean(text: str) -> None:
    low = text.lower()
    hit = [p for p in FORBIDDEN_VOCABULARY if p.lower() in low]
    if hit:
        raise SystemExit(f"forbidden vocabulary in rendered summary: {hit}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--now", default=None,
                        help="Optional UTC override (e.g. 2026-06-16T07:00:00Z) for the fixed replay clock.")
    parser.add_argument("--out-dir", type=Path, default=None,
                        help="Optional new directory for the JSON proof artifact; must not already exist.")
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    now = _parse_utc(args.now) if args.now else None
    report = build_real_loop_proof(now_utc=now)

    summary = render_summary(report)
    assert_vocabulary_clean(summary)

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(summary)

    if args.out_dir is not None:
        out_dir = args.out_dir.resolve()
        if out_dir.exists():
            print(f"out_dir must not exist: {out_dir}", file=sys.stderr)
            return 1
        out_dir.mkdir(parents=True)
        (out_dir / "low_risk_autogrowth_real_loop_proof.json").write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8",
        )

    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

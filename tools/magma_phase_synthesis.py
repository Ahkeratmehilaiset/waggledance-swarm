#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Operator-facing MAGMA 100h sprint phase synthesis.

Phase C tool-build (PR2 of 2) of the 100h plan
(``valiant-beaming-rocket.md``). Consumes the locked
``docs/runs/magma_100h_sprint_2026_05_23/baseline.json`` and
(optionally) the JSON report from
``tools/magma_slice_counter_read.py`` and emits a per-phase operator
summary in either markdown (default, human review) or JSON
(machine-readable).

Read-only. No baseline mutation. Strictly an aggregator: every claim
in the synthesis is copied verbatim from the inputs.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any, Iterable

DEFAULT_BASELINE = Path("docs/runs/magma_100h_sprint_2026_05_23/baseline.json")


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _path(obj: Any, *keys: str) -> Any:
    cur = obj
    for k in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    return cur


def _kv_lines(items: Iterable[tuple[str, Any]]) -> list[str]:
    return [f"- **{name}:** {value}" for name, value in items]


def build_synthesis(
    baseline: dict[str, Any],
    *,
    counter_read: dict[str, Any] | None = None,
    generated_at_utc: dt.datetime | None = None,
) -> dict[str, Any]:
    """Build a structured synthesis dict that both renderers consume."""

    generated = generated_at_utc or dt.datetime.now(dt.UTC)

    cs = baseline.get("current_state") or {}
    a3 = cs.get("a3_counterfactual_axis") or {}
    a4 = cs.get("a4_solver_growth_axis") or {}
    adv = cs.get("adversarial_corpus") or {}
    cp = cs.get("competitor_pilot") or {}
    gov = cs.get("governance_throughput") or {}
    rad = cs.get("receipt_adoption") or {}

    return {
        "generated_at_utc": generated.replace(microsecond=0).isoformat().replace(
            "+00:00", "Z"
        ),
        "sprint_id": baseline.get("sprint_id"),
        "schema_version": baseline.get("schema_version"),
        "baseline_generated_at_utc": baseline.get("generated_at_utc"),
        "ok": baseline.get("ok"),
        "blockers": list(baseline.get("blockers") or []),
        "release_boundary": dict(baseline.get("release_boundary") or {}),
        "forbidden_claims_count": len(baseline.get("forbidden_claims") or []),
        "must_win_axes": {
            "a3_counterfactual": {
                "claim_label": a3.get("claim_label"),
                "delta_proven": a3.get("counterfactual_delta_proven"),
                "receipt_chain_verified": a3.get("receipt_chain_verified"),
                "variant_count": a3.get("variant_count"),
                "variants_with_gate_delta": a3.get("variants_with_gate_delta"),
                "variants_with_kind_delta": a3.get("variants_with_kind_delta"),
            },
            "a4_solver_growth": {
                "claim_label": a4.get("claim_label"),
                "growth_proven": a4.get("solver_growth_proven"),
                "receipt_chain_verified": a4.get("receipt_chain_verified"),
            },
        },
        "ceded_axes": list(cp.get("ceded_axes") or []),
        "competitor_pilot": {
            "pilot_status": cp.get("pilot_status"),
            "consensus_grade": cp.get("consensus_grade"),
            "rival_local_checks_status": cp.get("rival_local_checks_status"),
            "rival_local_check_pass_count": cp.get(
                "rival_local_check_pass_count"
            ),
            "rival_local_check_required_count": cp.get(
                "rival_local_check_required_count"
            ),
            "rival_local_check_blocked_count": cp.get(
                "rival_local_check_blocked_count"
            ),
            "rival_local_check_consensus_grade": cp.get(
                "rival_local_check_consensus_grade"
            ),
            "rivals": list(cp.get("rivals") or []),
        },
        "adversarial_corpus": {
            "available": adv.get("available"),
            "case_count": adv.get("case_count"),
            "pass_count": adv.get("pass_count"),
            "fail_count": adv.get("fail_count"),
            "gate_accuracy": adv.get("gate_accuracy"),
            "verdict_accuracy": adv.get("verdict_accuracy"),
            "reason_code_accuracy": adv.get("reason_code_accuracy"),
        },
        "governance_throughput": {
            "metric_count": gov.get("metric_count"),
            "event_count_in_window": gov.get("event_count_in_window"),
            "task_count_in_window": gov.get("task_count_in_window"),
            "window_label": gov.get("window_label"),
            "status_counts": dict(gov.get("status_counts") or {}),
        },
        "receipt_adoption": {
            "high_criticality_gap_count": rad.get("high_criticality_gap_count"),
            "action_required_gap_count": rad.get("action_required_gap_count"),
            "accepted_exception_count": rad.get("accepted_exception_count"),
            "medium_gap_target_count": len(rad.get("medium_gap_targets") or []),
            "status_counts": dict(rad.get("status_counts") or {}),
        },
        "next_work_packages": [
            {
                "id": pkg.get("id"),
                "owner": pkg.get("owner"),
                "peer": pkg.get("peer"),
                "target": pkg.get("target"),
                "acceptance": pkg.get("acceptance"),
            }
            for pkg in (baseline.get("next_work_packages") or [])
            if isinstance(pkg, dict)
        ],
        "counter_read": _summarize_counter_read(counter_read),
    }


def _summarize_counter_read(report: dict[str, Any] | None) -> dict[str, Any]:
    if not report:
        return {"present": False}
    findings = (
        list(report.get("static_findings") or [])
        + list(report.get("delta_findings") or [])
    )
    return {
        "present": True,
        "decision": report.get("decision"),
        "findings_count": int(report.get("findings_count") or len(findings)),
        "static_finding_codes": [
            f.get("code") for f in (report.get("static_findings") or [])
        ],
        "delta_finding_codes": [
            f.get("code") for f in (report.get("delta_findings") or [])
        ],
        "anchor_path": report.get("anchor_path"),
        "baseline_path": report.get("baseline_path"),
    }


def render_markdown(synthesis: dict[str, Any]) -> str:
    """Render the structured synthesis as an operator-readable markdown."""

    lines: list[str] = []
    lines.append("# MAGMA 100h Sprint Phase Synthesis")
    lines.append("")
    lines.extend(
        _kv_lines(
            [
                ("Generated", synthesis["generated_at_utc"]),
                ("Sprint", synthesis.get("sprint_id")),
                ("Schema", synthesis.get("schema_version")),
                ("Baseline generated", synthesis.get("baseline_generated_at_utc")),
                ("Baseline.ok", synthesis.get("ok")),
            ]
        )
    )
    lines.append("")

    blockers = synthesis.get("blockers") or []
    lines.append("## Blockers")
    if blockers:
        for b in blockers:
            lines.append(f"- {b}")
    else:
        lines.append("- (none)")
    lines.append("")

    lines.append("## Release-boundary status")
    lines.append("")
    lines.append("| Flag | Value |")
    lines.append("|------|-------|")
    for flag, value in sorted(synthesis.get("release_boundary", {}).items()):
        lines.append(f"| {flag} | {value} |")
    lines.append("")

    a3 = synthesis["must_win_axes"]["a3_counterfactual"]
    lines.append("## A3 counterfactual axis (must-win)")
    lines.extend(
        _kv_lines(
            [
                ("Claim label", a3.get("claim_label")),
                ("Counterfactual delta proven", a3.get("delta_proven")),
                ("Receipt chain verified", a3.get("receipt_chain_verified")),
                ("Variant count", a3.get("variant_count")),
                (
                    "Variants with kind delta",
                    a3.get("variants_with_kind_delta"),
                ),
                (
                    "Variants with gate delta",
                    a3.get("variants_with_gate_delta"),
                ),
            ]
        )
    )
    lines.append("")

    a4 = synthesis["must_win_axes"]["a4_solver_growth"]
    lines.append("## A4 solver-growth axis (must-win)")
    lines.extend(
        _kv_lines(
            [
                ("Claim label", a4.get("claim_label")),
                ("Solver growth proven", a4.get("growth_proven")),
                ("Receipt chain verified", a4.get("receipt_chain_verified")),
            ]
        )
    )
    lines.append("")

    lines.append("## Ceded axes")
    ceded = synthesis.get("ceded_axes") or []
    if ceded:
        for c in ceded:
            lines.append(f"- {c}")
    else:
        lines.append("- (none)")
    lines.append("")

    cp = synthesis["competitor_pilot"]
    lines.append("## Competitor pilot")
    lines.extend(
        _kv_lines(
            [
                ("Pilot status", cp.get("pilot_status")),
                ("Consensus grade", cp.get("consensus_grade")),
                (
                    "Rival local checks status",
                    cp.get("rival_local_checks_status"),
                ),
                (
                    "Rival local check passed",
                    f"{cp.get('rival_local_check_pass_count')} / "
                    f"{cp.get('rival_local_check_required_count')}",
                ),
                (
                    "Rival local check blocked",
                    cp.get("rival_local_check_blocked_count"),
                ),
                (
                    "Rival local check consensus grade",
                    cp.get("rival_local_check_consensus_grade"),
                ),
                ("Rivals in scope", ", ".join(cp.get("rivals") or [])),
            ]
        )
    )
    lines.append("")

    adv = synthesis["adversarial_corpus"]
    lines.append("## Adversarial corpus")
    lines.extend(
        _kv_lines(
            [
                ("Available", adv.get("available")),
                ("Cases", adv.get("case_count")),
                ("Pass", adv.get("pass_count")),
                ("Fail", adv.get("fail_count")),
                ("Gate accuracy", adv.get("gate_accuracy")),
                ("Verdict accuracy", adv.get("verdict_accuracy")),
                ("Reason-code accuracy", adv.get("reason_code_accuracy")),
            ]
        )
    )
    lines.append("")

    rad = synthesis["receipt_adoption"]
    lines.append("## Receipt adoption")
    lines.extend(
        _kv_lines(
            [
                ("High-criticality gap count", rad.get("high_criticality_gap_count")),
                ("Action-required gap count", rad.get("action_required_gap_count")),
                ("Accepted exception count", rad.get("accepted_exception_count")),
                ("Medium gap target count", rad.get("medium_gap_target_count")),
                ("Status counts", rad.get("status_counts")),
            ]
        )
    )
    lines.append("")

    gov = synthesis["governance_throughput"]
    lines.append("## Governance throughput")
    lines.extend(
        _kv_lines(
            [
                ("Metric count", gov.get("metric_count")),
                ("Window", gov.get("window_label")),
                ("Events in window", gov.get("event_count_in_window")),
                ("Tasks in window", gov.get("task_count_in_window")),
                ("Status counts", gov.get("status_counts")),
            ]
        )
    )
    lines.append("")

    lines.append("## Next work packages")
    pkgs = synthesis.get("next_work_packages") or []
    if not pkgs:
        lines.append("- (none queued)")
    else:
        for pkg in pkgs:
            owner = pkg.get("owner") or "?"
            peer = pkg.get("peer") or "?"
            lines.append(
                f"- **{pkg.get('id')}** (owner: {owner}, peer: {peer})"
            )
            tgt = pkg.get("target")
            if tgt:
                lines.append(f"  - target: {tgt}")
            acc = pkg.get("acceptance")
            if acc:
                lines.append(f"  - acceptance: {acc}")
    lines.append("")

    cr = synthesis["counter_read"]
    lines.append("## Counter-read audit")
    if not cr.get("present"):
        lines.append(
            "- counter-read report not provided -- run `python "
            "tools/magma_slice_counter_read.py --baseline <path>` and "
            "pass `--counter-read <report.json>` here for the audit row."
        )
    else:
        lines.extend(
            _kv_lines(
                [
                    ("Decision", cr.get("decision")),
                    ("Findings count", cr.get("findings_count")),
                    (
                        "Static finding codes",
                        cr.get("static_finding_codes") or "(none)",
                    ),
                    (
                        "Delta finding codes",
                        cr.get("delta_finding_codes") or "(none)",
                    ),
                    ("Baseline path", cr.get("baseline_path")),
                    ("Anchor path", cr.get("anchor_path") or "(static-only)"),
                ]
            )
        )
    lines.append("")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--baseline",
        type=Path,
        default=DEFAULT_BASELINE,
        help=f"MAGMA-sprint baseline.json (default: {DEFAULT_BASELINE}).",
    )
    parser.add_argument(
        "--counter-read",
        type=Path,
        default=None,
        help=(
            "Optional path to a JSON report from "
            "tools/magma_slice_counter_read.py to include in the synthesis."
        ),
    )
    parser.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        help="Output format (default: markdown).",
    )
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args(argv)

    if not args.baseline.exists():
        print(
            f"magma_phase_synthesis: baseline not found: {args.baseline}",
            file=sys.stderr,
        )
        return 2

    baseline = _read_json(args.baseline)
    counter_read = (
        _read_json(args.counter_read) if args.counter_read else None
    )
    if args.counter_read and counter_read is None:
        print(
            f"magma_phase_synthesis: counter-read report unreadable: "
            f"{args.counter_read}",
            file=sys.stderr,
        )
        return 2

    synthesis = build_synthesis(baseline, counter_read=counter_read)
    if args.format == "json":
        rendered = json.dumps(synthesis, indent=2, sort_keys=True) + "\n"
    else:
        rendered = render_markdown(synthesis)

    sys.stdout.write(rendered)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

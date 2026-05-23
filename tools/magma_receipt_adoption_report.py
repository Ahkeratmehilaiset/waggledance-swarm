# SPDX-License-Identifier: BUSL-1.1
"""Report where MAGMA receipt v1 is actually adopted.

This is a read-only scanner. It does not prove runtime correctness and it does
not execute project code; it only records whether selected critical paths
contain direct receipt/evaluation/bundle hooks or only older MAGMA event hooks.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import sys
from typing import Any, Iterable, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


RECEIPT_PATTERNS = (
    "build_magma_receipt",
    "write_receipt_bundle",
    "ReceiptBundleEntry",
    "runtime_receipt_sink",
)
EVALUATION_PATTERNS = (
    "build_evaluation_result",
    "evaluation_result_digest",
    "EvaluationResult",
    "build_handle_query_runtime_summary",
)
VERIFIER_PATTERNS = (
    "verify_manifest",
    "verify_magma_receipt",
)
MAGMA_EVENT_PATTERNS = (
    "_magma_safe",
    "AuditProjector",
    "EventLogAdapter",
    "TrustAdapter",
    "ReplayAdapter",
    "ProvenanceAdapter",
    "emit_magma_event",
    "record_action_event",
    "record_policy_decision",
)
RECEIPT_OK_STATUSES = frozenset({"receipt_bound", "receipt_capable_opt_in"})


@dataclass(frozen=True)
class AcceptedException:
    applies_to_status: str
    status: str
    reason: str
    follow_up: str


@dataclass(frozen=True)
class AdoptionTarget:
    path: str
    label: str
    criticality: str
    reason: str
    accepted_exception: AcceptedException | None = None


DEFAULT_TARGETS: tuple[AdoptionTarget, ...] = (
    AdoptionTarget(
        "waggledance/core/v3_13_0/write_rco_gate.py",
        "WriteRCOGate action authority",
        "high",
        "External-effect and write-action governance should be receipt-bound.",
    ),
    AdoptionTarget(
        "waggledance/core/autonomy_growth/auto_promotion_engine.py",
        "Autogrowth auto-promotion",
        "high",
        "Promotion decisions define solver-growth authority.",
    ),
    AdoptionTarget(
        "waggledance/core/v3_13_0/solver_provenance.py",
        "Solver provenance",
        "high",
        "Solver provenance should bind to receipts before strong evidence claims.",
    ),
    AdoptionTarget(
        "waggledance/core/autonomy/runtime.py",
        "Autonomy runtime MAGMA append path",
        "medium",
        "Runtime MAGMA events are useful but not equivalent to receipt bundles.",
        AcceptedException(
            applies_to_status="magma_event_only",
            status="accepted_observability_path",
            reason=(
                "AutonomyRuntime projects post-decision MAGMA observability "
                "events through fail-open adapters; authority gates are "
                "receipt-bound elsewhere."
            ),
            follow_up=(
                "Add a separate opt-in runtime_summary_receipt module only "
                "if per-query or per-mission summary receipts are needed."
            ),
        ),
    ),
    AdoptionTarget(
        "tools/run_pdam_counterfactual_demo.py",
        "PDAM counterfactual demo",
        "medium",
        "The demo should remain a receipt-bound proof surface.",
    ),
    AdoptionTarget(
        "tools/run_magma_composition_demo.py",
        "MAGMA composition demo",
        "medium",
        "Composition demo should bind payload, evaluation result, and receipt.",
    ),
    AdoptionTarget(
        "tools/run_magma_adversarial_eval.py",
        "MAGMA adversarial eval",
        "medium",
        "Adversarial corpus evidence should flow through EvaluationResult.",
    ),
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Report MAGMA receipt adoption across critical WD paths.",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=ROOT,
        help="Repository root to scan. Default: inferred project root.",
    )
    parser.add_argument("--json", action="store_true", help="Print JSON report.")
    parser.add_argument(
        "--markdown",
        action="store_true",
        help="Print a markdown summary instead of JSON.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = build_adoption_report(root=args.root)
    if args.markdown:
        print(render_markdown(report))
    else:
        print(json.dumps(report, indent=None if args.json else 2, sort_keys=True))
    return 0


def build_adoption_report(
    *,
    root: Path,
    targets: Sequence[AdoptionTarget] = DEFAULT_TARGETS,
) -> dict[str, Any]:
    root = root.resolve()
    entries = [scan_target(root=root, target=target) for target in targets]
    counts: dict[str, int] = {}
    for entry in entries:
        counts[entry["status"]] = counts.get(entry["status"], 0) + 1
    high_gaps = [
        entry
        for entry in entries
        if entry["criticality"] == "high"
        and entry["status"] not in RECEIPT_OK_STATUSES
    ]
    action_required_gaps = [
        entry
        for entry in entries
        if entry["status"] not in RECEIPT_OK_STATUSES
        and entry["accepted_exception"] is None
    ]
    accepted_exceptions = [
        entry
        for entry in entries
        if entry["accepted_exception"] is not None
    ]
    return {
        "report_version": "magma.receipt_adoption_report.v0",
        "root": str(root),
        "target_count": len(entries),
        "status_counts": dict(sorted(counts.items())),
        "high_criticality_gap_count": len(high_gaps),
        "action_required_gap_count": len(action_required_gaps),
        "accepted_exception_count": len(accepted_exceptions),
        "entries": entries,
        "interpretation": (
            "Static adoption signal only. A receipt_bound status means the "
            "file contains direct receipt/bundle hooks; receipt_capable_opt_in "
            "means the file exposes a tested opt-in hook that can emit receipts "
            "through a caller-provided sink. Neither status proves every runtime "
            "branch emits a valid verified receipt. An accepted_exception marks "
            "a reviewed non-authority path where the current non-receipt status "
            "is intentional rather than an action-required gap."
        ),
    }


def scan_target(*, root: Path, target: AdoptionTarget) -> dict[str, Any]:
    path = root / target.path
    if not path.exists():
        return _entry(
            target=target,
            status="missing",
            path_exists=False,
            pattern_hits={},
            line_hits=[],
        )
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        text = path.read_text(encoding="utf-8", errors="replace")
    pattern_hits = {
        "receipt": _count_patterns(text, RECEIPT_PATTERNS),
        "evaluation": _count_patterns(text, EVALUATION_PATTERNS),
        "verifier": _count_patterns(text, VERIFIER_PATTERNS),
        "magma_event": _count_patterns(text, MAGMA_EVENT_PATTERNS),
    }
    return _entry(
        target=target,
        status=_classify(pattern_hits),
        path_exists=True,
        pattern_hits=pattern_hits,
        line_hits=_line_hits(text),
    )


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# MAGMA receipt adoption report",
        "",
        f"- version: `{report['report_version']}`",
        f"- targets: `{report['target_count']}`",
        f"- high criticality gaps: `{report['high_criticality_gap_count']}`",
        f"- action-required gaps: `{report['action_required_gap_count']}`",
        f"- accepted exceptions: `{report['accepted_exception_count']}`",
        "",
        "| status | criticality | exception | path | label |",
        "| --- | --- | --- | --- | --- |",
    ]
    for entry in report["entries"]:
        exception = entry["accepted_exception"]
        lines.append(
            "| {status} | {criticality} | {exception} | `{path}` | {label} |".format(
                status=entry["status"],
                criticality=entry["criticality"],
                exception=(
                    exception["status"]
                    if exception is not None
                    else ""
                ),
                path=entry["path"],
                label=entry["label"],
            )
        )
    lines.append("")
    lines.append(report["interpretation"])
    return "\n".join(lines)


def _entry(
    *,
    target: AdoptionTarget,
    status: str,
    path_exists: bool,
    pattern_hits: dict[str, dict[str, int]],
    line_hits: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "path": target.path,
        "label": target.label,
        "criticality": target.criticality,
        "reason": target.reason,
        "path_exists": path_exists,
        "status": status,
        "accepted_exception": _accepted_exception_for(
            target=target,
            status=status,
            path_exists=path_exists,
        ),
        "pattern_hits": pattern_hits,
        "line_hits": line_hits,
    }


def _accepted_exception_for(
    *,
    target: AdoptionTarget,
    status: str,
    path_exists: bool,
) -> dict[str, str] | None:
    exception = target.accepted_exception
    if exception is None or not path_exists or status != exception.applies_to_status:
        return None
    return {
        "applies_to_status": exception.applies_to_status,
        "status": exception.status,
        "reason": exception.reason,
        "follow_up": exception.follow_up,
    }


def _classify(pattern_hits: dict[str, dict[str, int]]) -> str:
    receipt_count = sum(pattern_hits.get("receipt", {}).values())
    evaluation_count = sum(pattern_hits.get("evaluation", {}).values())
    verifier_count = sum(pattern_hits.get("verifier", {}).values())
    magma_event_count = sum(pattern_hits.get("magma_event", {}).values())
    if receipt_count and evaluation_count:
        if (
            pattern_hits.get("receipt", {}).get("runtime_receipt_sink")
            and pattern_hits.get("evaluation", {}).get(
                "build_handle_query_runtime_summary"
            )
            and not pattern_hits.get("receipt", {}).get("build_magma_receipt")
        ):
            return "receipt_capable_opt_in"
        return "receipt_bound"
    if receipt_count or verifier_count:
        return "receipt_surface_only"
    if evaluation_count:
        return "evaluation_only"
    if magma_event_count:
        return "magma_event_only"
    return "not_receipt_bound"


def _count_patterns(text: str, patterns: Iterable[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for pattern in patterns:
        count = text.count(pattern)
        if count:
            counts[pattern] = count
    return counts


def _line_hits(text: str) -> list[dict[str, Any]]:
    all_patterns = (
        RECEIPT_PATTERNS
        + EVALUATION_PATTERNS
        + VERIFIER_PATTERNS
        + MAGMA_EVENT_PATTERNS
    )
    hits: list[dict[str, Any]] = []
    for line_number, line in enumerate(text.splitlines(), 1):
        matched = [pattern for pattern in all_patterns if pattern in line]
        if matched:
            hits.append({"line": line_number, "patterns": matched})
    return hits


if __name__ == "__main__":
    raise SystemExit(main())

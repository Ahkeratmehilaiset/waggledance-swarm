# SPDX-License-Identifier: BUSL-1.1
"""Build a read-only V12 adversarial corpus maturity summary."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.validate_synthetic_adversarial_corpus import (  # noqa: E402
    CRITICAL_DEFECT_TYPES,
    DEFAULT_CORPUS,
    DEFAULT_EXPECTATIONS,
    MIN_CRITICAL_DEFECT_CASES,
    REQUIRED_DEFECT_TYPES,
    REQUIRED_GATES,
    REQUIRED_RISK_CLASSES,
    REQUIRED_VERDICTS,
    validate_corpus,
)


REPORT_VERSION = "wd.v12.adversarial_corpus_maturity_summary.v0"
CLAIM_LABEL = "MEASURED_LOCAL_PARTIAL"
EXPANSION_CORPUS = (
    ROOT
    / "tests"
    / "fixtures"
    / "magma_adversarial_corpus"
    / "v0_expansion_2026_05_23.json"
)
EXPANSION_EXPECTATIONS = (
    ROOT
    / "tests"
    / "fixtures"
    / "magma_adversarial_corpus"
    / "v0_expansion_2026_05_23_expectations.json"
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Emit a path-free, read-only maturity summary for the local "
            "MAGMA synthetic adversarial corpus."
        ),
    )
    parser.add_argument(
        "--now",
        default=None,
        help="Optional UTC timestamp override for deterministic output.",
    )
    parser.add_argument(
        "--min-cases",
        type=int,
        default=50,
        help="Minimum strict v0 corpus case count expected.",
    )
    parser.add_argument(
        "--min-privacy-canaries",
        type=int,
        default=20,
        help="Minimum cases carrying privacy canaries expected.",
    )
    parser.add_argument(
        "--min-peer-review-traps",
        type=int,
        default=20,
        help="Minimum peer-review trap markers expected.",
    )
    parser.add_argument(
        "--min-defect-family-floor",
        type=int,
        default=7,
        help="Minimum cases expected for every required defect family.",
    )
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = build_adversarial_corpus_maturity_summary(
            now_utc=_parse_utc(args.now) if args.now else None,
            min_cases=args.min_cases,
            min_privacy_canaries=args.min_privacy_canaries,
            min_peer_review_traps=args.min_peer_review_traps,
            min_defect_family_floor=args.min_defect_family_floor,
        )
    except ValueError as exc:
        print(f"adversarial corpus maturity summary FAILED: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(render_markdown(report), end="")
    return 0 if report["ok"] else 1


def build_adversarial_corpus_maturity_summary(
    *,
    now_utc: datetime | None = None,
    min_cases: int = 50,
    min_privacy_canaries: int = 20,
    min_peer_review_traps: int = 20,
    min_defect_family_floor: int = 7,
    corpus_report: Mapping[str, Any] | None = None,
    expansion_report: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if min_cases < 1:
        raise ValueError("--min-cases must be >= 1")
    if min_privacy_canaries < 0:
        raise ValueError("--min-privacy-canaries must be >= 0")
    if min_peer_review_traps < 0:
        raise ValueError("--min-peer-review-traps must be >= 0")
    if min_defect_family_floor < 1:
        raise ValueError("--min-defect-family-floor must be >= 1")

    generated_at = (now_utc or datetime.now(timezone.utc)).astimezone(timezone.utc)
    generated_at_utc = generated_at.isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )
    source = dict(
        corpus_report
        if corpus_report is not None
        else validate_corpus(DEFAULT_CORPUS, DEFAULT_EXPECTATIONS)
    )
    expansion = dict(
        expansion_report
        if expansion_report is not None
        else validate_corpus(
            EXPANSION_CORPUS,
            EXPANSION_EXPECTATIONS,
            folded_into_corpus_path=DEFAULT_CORPUS,
            folded_into_expectations_path=DEFAULT_EXPECTATIONS,
        )
    )
    coverage = _mapping(source.get("coverage"))
    expansion_summary = _mapping(expansion.get("expansion_summary"))
    blockers = _source_blockers(source)
    blockers.extend(
        _maturity_blockers(
            source,
            coverage,
            min_cases=min_cases,
            min_privacy_canaries=min_privacy_canaries,
            min_peer_review_traps=min_peer_review_traps,
            min_defect_family_floor=min_defect_family_floor,
        )
    )
    blockers.extend(_expansion_blockers(expansion, expansion_summary))

    defect_counts = _int_mapping(coverage.get("defect_type_counts"))
    risk_counts = _int_mapping(coverage.get("risk_class_counts"))
    gate_counts = _int_mapping(coverage.get("expected_gate_counts"))
    verdict_counts = _int_mapping(coverage.get("expected_verdict_counts"))
    uniform_family_floor = _uniform_family_floor_summary(
        defect_counts,
        min_defect_family_floor=min_defect_family_floor,
    )

    return {
        "report_version": REPORT_VERSION,
        "generated_at_utc": generated_at_utc,
        "ok": not blockers,
        "blockers": blockers,
        "claim_label": CLAIM_LABEL,
        "source": _source_summary(source),
        "maturity": {
            "min_cases": min_cases,
            "case_count": _as_int(source.get("case_count")),
            "full_coverage_required": source.get("full_coverage_required") is True,
            "split_required": source.get("split_required") is True,
            "held_out_case_count": _as_int(coverage.get("held_out_case_count")),
            "min_critical_defect_cases": _as_int(
                coverage.get("min_critical_defect_cases")
            ),
            "privacy_canary_count": _as_int(coverage.get("privacy_canary_count")),
            "min_privacy_canaries": min_privacy_canaries,
            "peer_review_trap_count": _as_int(coverage.get("peer_review_trap_count")),
            "min_peer_review_traps": min_peer_review_traps,
            "defect_type_count": len(defect_counts),
            "risk_class_count": len(risk_counts),
            "gate_count": len(gate_counts),
            "verdict_count": len(verdict_counts),
            "critical_defect_type_counts": _int_mapping(
                coverage.get("critical_defect_type_counts")
            ),
        },
        "uniform_family_floor": uniform_family_floor,
        "coverage": {
            "defect_type_counts": defect_counts,
            "risk_class_counts": risk_counts,
            "expected_gate_counts": gate_counts,
            "expected_verdict_counts": verdict_counts,
        },
        "historical_expansion": _historical_expansion_summary(expansion_summary),
        "maturation_targets": _maturation_targets(
            defect_counts=defect_counts,
            risk_counts=risk_counts,
            gate_counts=gate_counts,
            verdict_counts=verdict_counts,
        ),
        "authority_boundary": _authority_boundary(),
    }


def render_markdown(report: Mapping[str, Any]) -> str:
    maturity = _mapping(report.get("maturity"))
    uniform_family_floor = _mapping(report.get("uniform_family_floor"))
    authority = _mapping(report.get("authority_boundary"))
    expansion = _mapping(report.get("historical_expansion"))
    lines = [
        "# V12 Adversarial Corpus Maturity Summary",
        "",
        f"ok: `{_bool_text(report.get('ok'))}`",
        f"blockers: `{len(list(report.get('blockers') or []))}`",
        f"cases: `{maturity.get('case_count', 0)}/{maturity.get('min_cases', 0)}`",
        (
            "privacy canaries: "
            f"`{maturity.get('privacy_canary_count', 0)}/"
            f"{maturity.get('min_privacy_canaries', 0)}`"
        ),
        (
            "peer-review traps: "
            f"`{maturity.get('peer_review_trap_count', 0)}/"
            f"{maturity.get('min_peer_review_traps', 0)}`"
        ),
        f"held-out cases: `{maturity.get('held_out_case_count', 0)}`",
        (
            "defect family floor: "
            f"`{uniform_family_floor.get('families_at_or_above_floor', 0)}/"
            f"{uniform_family_floor.get('family_count', 0)} >= "
            f"{uniform_family_floor.get('min_defect_family_floor', 0)} "
            f"(weakest {uniform_family_floor.get('weakest_count', 0)})`"
        ),
        (
            "historical expansion folded into v0: "
            f"`{_bool_text(expansion.get('fold_in_verified'))}`"
        ),
        "",
        "## Maturation Targets",
    ]
    for target in list(report.get("maturation_targets") or []):
        lines.append(
            "- "
            f"{target['kind']} `{target['name']}` count="
            f"`{target['count']}` reason=`{target['reason']}`"
        )
    if not report.get("maturation_targets"):
        lines.append("- none")

    lines.extend(
        [
            "",
            "## Authority Boundary",
            f"corpus mutation: `{_bool_text(authority.get('corpus_mutation'))}`",
            f"runtime authority: `{_bool_text(authority.get('runtime_authority'))}`",
            f"promotion authority: `{_bool_text(authority.get('promotion_authority'))}`",
            f"bridge write authority: `{_bool_text(authority.get('bridge_write_authority'))}`",
            f"network authority: `{_bool_text(authority.get('network_authority'))}`",
            "",
            "This summary is read-only. It does not add corpus cases, alter "
            "expectations, run live solvers, promote, enqueue schedulers, append "
            "bridge events, call network, or grant runtime authority.",
        ]
    )
    return "\n".join(lines) + "\n"


def _source_blockers(source: Mapping[str, Any]) -> list[str]:
    blockers: list[str] = []
    if source.get("ok") is not True:
        blockers.append("source_validation_not_ok")
    if source.get("errors"):
        blockers.append("source_errors_present")
    if source.get("corpus") != "<redacted>":
        blockers.append("source_corpus_not_redacted")
    if source.get("expectations") != "<redacted>":
        blockers.append("source_expectations_not_redacted")
    if source.get("full_coverage_required") is not True:
        blockers.append("full_coverage_required_not_true")
    if source.get("split_required") is not True:
        blockers.append("split_required_not_true")
    return blockers


def _maturity_blockers(
    source: Mapping[str, Any],
    coverage: Mapping[str, Any],
    *,
    min_cases: int,
    min_privacy_canaries: int,
    min_peer_review_traps: int,
    min_defect_family_floor: int,
) -> list[str]:
    blockers: list[str] = []
    if _as_int(source.get("case_count")) < min_cases:
        blockers.append("case_count_below_minimum")
    if _as_int(coverage.get("held_out_case_count")) < 6:
        blockers.append("held_out_case_count_below_minimum")
    if _as_int(coverage.get("privacy_canary_count")) < min_privacy_canaries:
        blockers.append("privacy_canary_count_below_minimum")
    if _as_int(coverage.get("peer_review_trap_count")) < min_peer_review_traps:
        blockers.append("peer_review_trap_count_below_minimum")

    defect_counts = _int_mapping(coverage.get("defect_type_counts"))
    missing_defects = sorted(REQUIRED_DEFECT_TYPES - set(defect_counts))
    if missing_defects:
        blockers.append("required_defect_type_missing")
    for defect_type in sorted(REQUIRED_DEFECT_TYPES):
        if defect_counts.get(defect_type, 0) < min_defect_family_floor:
            blockers.append(f"defect_family_floor_below_minimum:{defect_type}")
    critical_counts = _int_mapping(coverage.get("critical_defect_type_counts"))
    for defect_type in sorted(CRITICAL_DEFECT_TYPES):
        if critical_counts.get(defect_type, 0) < MIN_CRITICAL_DEFECT_CASES:
            blockers.append(f"critical_defect_floor_missing:{defect_type}")

    for name, required, counts in (
        (
            "risk_class",
            REQUIRED_RISK_CLASSES,
            _int_mapping(coverage.get("risk_class_counts")),
        ),
        (
            "expected_gate",
            REQUIRED_GATES,
            _int_mapping(coverage.get("expected_gate_counts")),
        ),
        (
            "expected_verdict",
            REQUIRED_VERDICTS,
            _int_mapping(coverage.get("expected_verdict_counts")),
        ),
    ):
        if sorted(required - set(counts)):
            blockers.append(f"{name}_coverage_missing")
    return blockers


def _uniform_family_floor_summary(
    defect_counts: Mapping[str, int],
    *,
    min_defect_family_floor: int,
) -> dict[str, Any]:
    required_counts = {
        defect_type: int(defect_counts.get(defect_type, 0))
        for defect_type in sorted(REQUIRED_DEFECT_TYPES)
    }
    below_floor = {
        defect_type: count
        for defect_type, count in required_counts.items()
        if count < min_defect_family_floor
    }
    weakest_count = min(required_counts.values()) if required_counts else 0
    return {
        "min_defect_family_floor": min_defect_family_floor,
        "met": not below_floor,
        "family_count": len(required_counts),
        "families_at_or_above_floor": len(required_counts) - len(below_floor),
        "weakest_count": weakest_count,
        "below_floor": below_floor,
    }


def _expansion_blockers(
    expansion: Mapping[str, Any],
    expansion_summary: Mapping[str, Any],
) -> list[str]:
    blockers: list[str] = []
    if expansion.get("ok") is not True:
        blockers.append("historical_expansion_validation_not_ok")
    if expansion.get("errors"):
        blockers.append("historical_expansion_errors_present")
    if expansion_summary.get("is_expansion") is not True:
        blockers.append("historical_expansion_not_detected")
    if expansion_summary.get("folded_into_v0_claim") is not True:
        blockers.append("historical_expansion_fold_claim_missing")
    fold_in = _mapping(expansion_summary.get("fold_in"))
    if fold_in.get("status") != "pass":
        blockers.append("historical_expansion_fold_in_not_pass")
    if _as_int(fold_in.get("missing_case_count")):
        blockers.append("historical_expansion_missing_cases")
    if _as_int(fold_in.get("missing_expectation_count")):
        blockers.append("historical_expansion_missing_expectations")
    return blockers


def _source_summary(source: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "validator": "tools/validate_synthetic_adversarial_corpus.py",
        "corpus_redacted": source.get("corpus") == "<redacted>",
        "expectations_redacted": source.get("expectations") == "<redacted>",
        "source_ok": source.get("ok") is True,
        "source_error_count": len(list(source.get("errors") or [])),
    }


def _historical_expansion_summary(expansion_summary: Mapping[str, Any]) -> dict[str, Any]:
    fold_in = _mapping(expansion_summary.get("fold_in"))
    return {
        "label": str(expansion_summary.get("label", "")),
        "status": str(expansion_summary.get("status", "")),
        "case_count": _as_int(expansion_summary.get("case_count")),
        "expectation_count": _as_int(expansion_summary.get("expectation_count")),
        "fold_in_status": str(fold_in.get("status", "")),
        "fold_in_verified": fold_in.get("status") == "pass",
        "missing_case_count": _as_int(fold_in.get("missing_case_count")),
        "missing_expectation_count": _as_int(
            fold_in.get("missing_expectation_count")
        ),
    }


def _maturation_targets(
    *,
    defect_counts: Mapping[str, int],
    risk_counts: Mapping[str, int],
    gate_counts: Mapping[str, int],
    verdict_counts: Mapping[str, int],
) -> list[dict[str, Any]]:
    targets: list[dict[str, Any]] = []
    targets.extend(
        _weakest_rows(defect_counts, kind="defect_type", reason="lowest case count")
    )
    targets.extend(
        _weakest_rows(risk_counts, kind="risk_class", reason="lowest risk coverage")
    )
    targets.extend(
        _weakest_rows(gate_counts, kind="expected_gate", reason="lowest gate coverage")
    )
    targets.extend(
        _weakest_rows(
            verdict_counts,
            kind="expected_verdict",
            reason="lowest verdict coverage",
        )
    )
    return targets[:8]


def _weakest_rows(
    counts: Mapping[str, int],
    *,
    kind: str,
    reason: str,
    limit: int = 2,
) -> list[dict[str, Any]]:
    return [
        {
            "kind": kind,
            "name": name,
            "count": count,
            "reason": reason,
        }
        for name, count in sorted(counts.items(), key=lambda item: (item[1], item[0]))[
            :limit
        ]
    ]


def _authority_boundary() -> dict[str, Any]:
    return {
        "read_only_summary": True,
        "corpus_mutation": False,
        "expectation_mutation": False,
        "runtime_authority": False,
        "promotion_authority": False,
        "scheduler_authority": False,
        "bridge_write_authority": False,
        "network_authority": False,
        "storage_write_authority": False,
        "solver_execution_authority": False,
        "external_writes_applied": False,
    }


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _int_mapping(value: Any) -> dict[str, int]:
    if not isinstance(value, Mapping):
        return {}
    return {str(key): _as_int(raw) for key, raw in value.items()}


def _as_int(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return max(0, value)


def _bool_text(value: Any) -> str:
    return "true" if value is True else "false"


def _parse_utc(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("--now must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

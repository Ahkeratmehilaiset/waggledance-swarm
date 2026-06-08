# SPDX-License-Identifier: BUSL-1.1
"""Build a read-only V12 ingredient coverage rollup."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any, Callable, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.build_v12_adversarial_corpus_maturity_summary import (  # noqa: E402
    REPORT_VERSION as ADVERSARIAL_CORPUS_REPORT_VERSION,
    build_adversarial_corpus_maturity_summary,
)
from tools.build_v12_counterfactual_eval_coverage_summary import (  # noqa: E402
    REPORT_VERSION as COUNTERFACTUAL_EVAL_REPORT_VERSION,
    build_counterfactual_eval_coverage_summary,
)
from tools.build_v12_solver_growth_family_coverage_summary import (  # noqa: E402
    REPORT_VERSION as SOLVER_GROWTH_REPORT_VERSION,
    build_solver_growth_family_coverage_summary,
)
from tools.run_v12_memory_palace_shortcut_runtime_promotion_design import (  # noqa: E402
    REPORT_VERSION as MEMORY_PALACE_RUNTIME_DESIGN_REPORT_VERSION,
    build_memory_palace_shortcut_runtime_promotion_design,
)
from tools.verify_v12_memory_palace_shortcut_runtime_promotion_design import (  # noqa: E402
    VERIFICATION_VERSION as MEMORY_PALACE_VERIFICATION_VERSION,
    verify_memory_palace_shortcut_runtime_promotion_design,
)


REPORT_VERSION = "wd.v12.ingredient_coverage_rollup.v0"
CLAIM_LABEL = "MEASURED_LOCAL_PARTIAL"

COMMON_FALSE_FIELDS = (
    "runtime_authority",
    "promotion_authority",
    "scheduler_authority",
    "bridge_write_authority",
    "network_authority",
    "storage_write_authority",
    "solver_execution_authority",
)
COMMON_TRUE_FIELDS = ("read_only_summary",)
EXTERNAL_FALSE_FIELDS = ("external_writes_applied",)
MEMORY_FALSE_FIELDS = (
    "runtime_route_changed",
    "storage_write_performed",
    "bridge_append_performed",
    "solver_call_performed",
    "scheduler_enqueue_performed",
    "promotion_performed",
    "promotion_action_allowed",
    "gate_skip_performed",
    "network_access_performed",
    "approval_granted",
    "release_decision_made",
    "automatic_release_decision",
)
MEMORY_TRUE_FIELDS = (
    "design_only",
    "manual_review_required",
    "operator_gate_required_for_runtime_promotion",
)

INGREDIENT_SPECS = (
    {
        "id": "solver_growth_family",
        "label": "Solver-Growth Family Coverage",
        "expected_report_version": SOLVER_GROWTH_REPORT_VERSION,
        "false_fields": COMMON_FALSE_FIELDS,
        "true_fields": COMMON_TRUE_FIELDS,
    },
    {
        "id": "counterfactual_eval",
        "label": "Counterfactual-Eval Coverage",
        "expected_report_version": COUNTERFACTUAL_EVAL_REPORT_VERSION,
        "false_fields": COMMON_FALSE_FIELDS + EXTERNAL_FALSE_FIELDS,
        "true_fields": COMMON_TRUE_FIELDS,
    },
    {
        "id": "adversarial_corpus",
        "label": "Adversarial Corpus Maturity",
        "expected_report_version": ADVERSARIAL_CORPUS_REPORT_VERSION,
        "false_fields": COMMON_FALSE_FIELDS + EXTERNAL_FALSE_FIELDS,
        "true_fields": COMMON_TRUE_FIELDS,
    },
    {
        "id": "memory_palace_shortcut_runtime_design",
        "label": "Memory Palace Shortcut Runtime-Promotion Design",
        "expected_report_version": MEMORY_PALACE_RUNTIME_DESIGN_REPORT_VERSION,
        "false_fields": MEMORY_FALSE_FIELDS,
        "true_fields": MEMORY_TRUE_FIELDS,
        "verification_id": "memory_palace_shortcut_runtime_design_verification",
        "expected_verification_version": MEMORY_PALACE_VERIFICATION_VERSION,
    },
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Emit a path-free, read-only rollup over current local V12 "
            "ingredient coverage summaries."
        ),
    )
    parser.add_argument(
        "--now",
        default=None,
        help="Optional UTC timestamp override for deterministic output.",
    )
    parser.add_argument(
        "--min-ingredients",
        type=int,
        default=len(INGREDIENT_SPECS),
        help="Minimum ingredient rows expected in the rollup.",
    )
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = build_v12_ingredient_coverage_rollup(
            now_utc=_parse_utc(args.now) if args.now else None,
            min_ingredients=args.min_ingredients,
        )
    except ValueError as exc:
        print(f"V12 ingredient coverage rollup FAILED: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(render_markdown(report), end="")
    return 0 if report["ok"] else 1


def build_v12_ingredient_coverage_rollup(
    *,
    now_utc: datetime | None = None,
    min_ingredients: int = len(INGREDIENT_SPECS),
    source_reports: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    if min_ingredients < 1:
        raise ValueError("--min-ingredients must be >= 1")

    generated_at = (now_utc or datetime.now(timezone.utc)).astimezone(timezone.utc)
    generated_at_utc = generated_at.isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )
    reports = _source_reports(generated_at, source_reports)
    rows = [_ingredient_row(spec, reports) for spec in INGREDIENT_SPECS]
    blockers = _rollup_blockers(rows, min_ingredients=min_ingredients)
    ok_rows = sum(1 for row in rows if row["ok"] is True)
    authority_clean_rows = sum(
        1 for row in rows if row["authority_boundary_ok"] is True
    )

    return {
        "report_version": REPORT_VERSION,
        "generated_at_utc": generated_at_utc,
        "ok": not blockers,
        "blockers": blockers,
        "claim_label": CLAIM_LABEL,
        "coverage": {
            "min_ingredients": min_ingredients,
            "ingredient_count": len(rows),
            "ok_ingredient_count": ok_rows,
            "authority_clean_ingredient_count": authority_clean_rows,
            "all_ingredients_ok": ok_rows == len(rows),
            "all_authority_boundaries_clean": authority_clean_rows == len(rows),
        },
        "ingredients": rows,
        "recommended_next_slice": _recommended_next_slice(rows, blockers),
        "authority_boundary": _authority_boundary(),
    }


def render_markdown(report: Mapping[str, Any]) -> str:
    coverage = _mapping(report.get("coverage"))
    authority = _mapping(report.get("authority_boundary"))
    lines = [
        "# V12 Ingredient Coverage Rollup",
        "",
        f"ok: `{_bool_text(report.get('ok'))}`",
        f"blockers: `{len(list(report.get('blockers') or []))}`",
        (
            "ingredients ok: "
            f"`{coverage.get('ok_ingredient_count', 0)}/"
            f"{coverage.get('ingredient_count', 0)}`"
        ),
        (
            "authority clean: "
            f"`{coverage.get('authority_clean_ingredient_count', 0)}/"
            f"{coverage.get('ingredient_count', 0)}`"
        ),
        "",
        "## Ingredients",
    ]
    for row in list(report.get("ingredients") or []):
        lines.append(
            "- "
            f"{row['id']}: ok=`{_bool_text(row['ok'])}`, "
            f"authority=`{_bool_text(row['authority_boundary_ok'])}`, "
            f"blockers=`{row['blocker_count']}`"
        )

    lines.extend(
        [
            "",
            "## Authority Boundary",
            f"runtime authority: `{_bool_text(authority.get('runtime_authority'))}`",
            f"promotion authority: `{_bool_text(authority.get('promotion_authority'))}`",
            f"scheduler authority: `{_bool_text(authority.get('scheduler_authority'))}`",
            f"bridge write authority: `{_bool_text(authority.get('bridge_write_authority'))}`",
            f"network authority: `{_bool_text(authority.get('network_authority'))}`",
            f"storage write authority: `{_bool_text(authority.get('storage_write_authority'))}`",
            "",
            "This rollup is read-only. It does not execute per-query receipt "
            "artifact generation, dispatch solvers, enqueue schedulers, append "
            "bridge events, call network, mutate storage, promote, or grant "
            "runtime authority.",
        ]
    )
    return "\n".join(lines) + "\n"


def _source_reports(
    now_utc: datetime,
    source_reports: Mapping[str, Mapping[str, Any]] | None,
) -> dict[str, Mapping[str, Any]]:
    injected = source_reports or {}

    def provided_or_build(
        key: str,
        builder: Callable[[], Mapping[str, Any]],
    ) -> Mapping[str, Any]:
        if key in injected:
            return injected[key]
        return builder()

    result: dict[str, Mapping[str, Any]] = {}
    result["solver_growth_family"] = provided_or_build(
        "solver_growth_family",
        lambda: build_solver_growth_family_coverage_summary(now_utc=now_utc),
    )
    result["counterfactual_eval"] = provided_or_build(
        "counterfactual_eval",
        lambda: build_counterfactual_eval_coverage_summary(now_utc=now_utc),
    )
    result["adversarial_corpus"] = provided_or_build(
        "adversarial_corpus",
        lambda: build_adversarial_corpus_maturity_summary(now_utc=now_utc),
    )
    memory_report = provided_or_build(
        "memory_palace_shortcut_runtime_design",
        lambda: build_memory_palace_shortcut_runtime_promotion_design(
            now_utc=now_utc
        ),
    )
    result["memory_palace_shortcut_runtime_design"] = memory_report
    result["memory_palace_shortcut_runtime_design_verification"] = provided_or_build(
        "memory_palace_shortcut_runtime_design_verification",
        lambda: verify_memory_palace_shortcut_runtime_promotion_design(
            memory_report
        ),
    )
    return result


def _ingredient_row(
    spec: Mapping[str, Any],
    reports: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    ingredient_id = str(spec["id"])
    report = _mapping(reports.get(ingredient_id))
    blockers: list[str] = []
    expected_version = str(spec["expected_report_version"])
    source_version = str(report.get("report_version", ""))
    if source_version != expected_version:
        blockers.append("source_report_version_mismatch")
    if report.get("ok") is not True:
        blockers.append("source_report_not_ok")
    source_blockers = sorted(str(item) for item in list(report.get("blockers") or []))
    if source_blockers:
        blockers.append("source_blockers_present")

    authority_source = _mapping(report.get("authority_boundary"))
    verification: Mapping[str, Any] | None = None
    if "verification_id" in spec:
        verification = _mapping(reports.get(str(spec["verification_id"])))
        authority_source = verification
        expected_verification = str(spec["expected_verification_version"])
        if verification.get("verification_version") != expected_verification:
            blockers.append("verification_version_mismatch")
        if verification.get("ok") is not True:
            blockers.append("verification_not_ok")
        verification_blockers = list(verification.get("blockers") or [])
        if verification_blockers:
            blockers.append("verification_blockers_present")

    authority_check = _authority_check(
        authority_source,
        true_fields=tuple(str(item) for item in spec["true_fields"]),
        false_fields=tuple(str(item) for item in spec["false_fields"]),
    )
    if authority_check["ok"] is not True:
        blockers.append("authority_boundary_not_ok")

    return {
        "id": ingredient_id,
        "label": str(spec["label"]),
        "source_report_version": source_version,
        "expected_report_version": expected_version,
        "ok": not blockers,
        "blockers": sorted(set(blockers)),
        "source_blockers": source_blockers,
        "blocker_count": len(set(blockers)),
        "authority_boundary_ok": authority_check["ok"],
        "authority_true_fields_ok": authority_check["true_fields_ok"],
        "authority_false_fields_ok": authority_check["false_fields_ok"],
        "required_true_fields": list(spec["true_fields"]),
        "required_false_fields": list(spec["false_fields"]),
        "verification_version": (
            str(verification.get("verification_version", ""))
            if verification is not None
            else ""
        ),
        "recommended_next_slice": _ingredient_next_slice(
            ingredient_id,
            report,
            verification,
        ),
    }


def _authority_check(
    source: Mapping[str, Any],
    *,
    true_fields: Sequence[str],
    false_fields: Sequence[str],
) -> dict[str, Any]:
    true_ok = all(source.get(field) is True for field in true_fields)
    false_ok = all(source.get(field) is False for field in false_fields)
    return {
        "ok": true_ok and false_ok,
        "true_fields_ok": true_ok,
        "false_fields_ok": false_ok,
    }


def _rollup_blockers(
    rows: Sequence[Mapping[str, Any]],
    *,
    min_ingredients: int,
) -> list[str]:
    blockers: list[str] = []
    if len(rows) < min_ingredients:
        blockers.append("ingredient_count_below_minimum")
    for row in rows:
        if row.get("ok") is not True:
            for blocker in list(row.get("blockers") or []):
                blockers.append(f"ingredient_blocked:{row['id']}:{blocker}")
    return sorted(set(blockers))


def _ingredient_next_slice(
    ingredient_id: str,
    report: Mapping[str, Any],
    verification: Mapping[str, Any] | None,
) -> str:
    if verification is not None and verification.get("ok") is not True:
        return f"fix_{ingredient_id}_verification_before_rollup"
    if report.get("ok") is not True:
        return f"fix_{ingredient_id}_source_summary_before_rollup"
    if ingredient_id == "solver_growth_family":
        return str(report.get("recommended_next_slice", "expand_solver_growth_cases"))
    if ingredient_id == "counterfactual_eval":
        targets = list(report.get("next_eval_targets") or [])
        return str(targets[0]) if targets else "add_second_counterfactual_sample_family"
    if ingredient_id == "adversarial_corpus":
        targets = list(report.get("maturation_targets") or [])
        if targets:
            target = targets[0]
            return f"expand_adversarial_corpus:{target['kind']}:{target['name']}"
        return "maintain_adversarial_corpus_maturity_floor"
    if ingredient_id == "memory_palace_shortcut_runtime_design":
        return "operator_authorized_shadow_replay_design_fixture_only"
    return "no_next_slice"


def _recommended_next_slice(
    rows: Sequence[Mapping[str, Any]],
    blockers: Sequence[str],
) -> str:
    if blockers:
        return "fix_blocked_v12_ingredient_before_claiming_rollup_complete"
    for row in rows:
        next_slice = str(row.get("recommended_next_slice", ""))
        if next_slice:
            return next_slice
    return "no_rollup_followup_available"


def _authority_boundary() -> dict[str, Any]:
    return {
        "read_only_summary": True,
        "runtime_authority": False,
        "promotion_authority": False,
        "scheduler_authority": False,
        "bridge_write_authority": False,
        "network_authority": False,
        "storage_write_authority": False,
        "solver_execution_authority": False,
        "receipt_artifact_generation_executed": False,
        "external_writes_applied": False,
    }


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


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

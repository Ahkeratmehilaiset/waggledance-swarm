# SPDX-License-Identifier: BUSL-1.1
"""Build a read-only V12 substrate evidence freshness rollup."""
from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.build_v12_ingredient_coverage_rollup import (  # noqa: E402
    REPORT_VERSION as INGREDIENT_ROLLUP_VERSION,
    build_v12_ingredient_coverage_rollup,
)
from tools.validate_competitive_evidence_matrix_freshness import (  # noqa: E402
    DEFAULT_MATRIX,
    DEFAULT_MAX_AGE_DAYS,
    validate_matrix_freshness,
)


REPORT_VERSION = "wd.v12.substrate_evidence_freshness_rollup.v0"
SUBSTRATE_INGREDIENT_IDS = (
    "counterfactual_eval",
    "solver_growth_family",
    "adversarial_corpus",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Emit a path-free, read-only freshness rollup over V12 substrate "
            "evidence and the competitive evidence matrix."
        ),
    )
    parser.add_argument(
        "--now",
        default=None,
        help="Optional UTC timestamp override for deterministic output.",
    )
    parser.add_argument(
        "--matrix",
        type=Path,
        default=DEFAULT_MATRIX,
        help="Competitive evidence matrix Markdown file.",
    )
    parser.add_argument(
        "--max-age-days",
        type=int,
        default=DEFAULT_MAX_AGE_DAYS,
        help="Freshness target for planning evidence.",
    )
    parser.add_argument(
        "--require-fresh-matrix",
        action="store_true",
        help="Fail if the competitive matrix is stale.",
    )
    parser.add_argument(
        "--max-next-slices",
        type=int,
        default=5,
        help="Maximum next substrate slices to include.",
    )
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        matrix_text = args.matrix.read_text(encoding="utf-8")
        report = build_v12_substrate_evidence_freshness_rollup(
            now_utc=_parse_utc(args.now) if args.now else None,
            matrix_text=matrix_text,
            max_age_days=args.max_age_days,
            require_fresh_matrix=bool(args.require_fresh_matrix),
            max_next_slices=args.max_next_slices,
        )
    except (OSError, ValueError) as exc:
        print(
            f"V12 substrate evidence freshness rollup FAILED: {exc}",
            file=sys.stderr,
        )
        return 1

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(render_markdown(report), end="")
    return 0 if report["ok"] else 1


def build_v12_substrate_evidence_freshness_rollup(
    *,
    now_utc: datetime | None = None,
    matrix_text: str | None = None,
    max_age_days: int = DEFAULT_MAX_AGE_DAYS,
    require_fresh_matrix: bool = False,
    max_next_slices: int = 5,
    ingredient_rollup: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if max_age_days <= 0:
        raise ValueError("--max-age-days must be > 0")
    if max_next_slices < 1:
        raise ValueError("--max-next-slices must be >= 1")

    generated_at = (now_utc or datetime.now(timezone.utc)).astimezone(timezone.utc)
    generated_at_utc = generated_at.isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )
    source_rollup = dict(
        ingredient_rollup
        if ingredient_rollup is not None
        else build_v12_ingredient_coverage_rollup(now_utc=generated_at)
    )
    matrix_report = validate_matrix_freshness(
        matrix_text if matrix_text is not None else DEFAULT_MATRIX.read_text(
            encoding="utf-8"
        ),
        now=generated_at.date(),
        max_age_days=max_age_days,
        require_fresh=require_fresh_matrix,
    )

    blockers = _source_blockers(
        source_rollup,
        matrix_report,
        now_date=generated_at.date(),
        max_age_days=max_age_days,
    )
    rollup_age_days = _age_days(
        _parse_report_date(source_rollup.get("generated_at_utc")),
        generated_at.date(),
    )
    substrate_rows = _substrate_rows(
        source_rollup,
        rollup_age_days=rollup_age_days,
        max_age_days=max_age_days,
    )
    stale_windows = _stale_windows(substrate_rows, matrix_report)

    return {
        "report_version": REPORT_VERSION,
        "generated_at_utc": generated_at_utc,
        "ok": not blockers,
        "blockers": blockers,
        "claim_label": "MEASURED_LOCAL_PARTIAL",
        "freshness": {
            "now_date": generated_at.date().isoformat(),
            "max_age_days": max_age_days,
            "ingredient_rollup_age_days": rollup_age_days,
            "substrate_fresh_for_planning": all(
                row["fresh_for_planning"] for row in substrate_rows
            ),
            "competitive_matrix_fresh_for_planning": (
                matrix_report.get("fresh_for_planning") is True
            ),
            "stale_window_count": len(stale_windows),
            "stale_windows": stale_windows,
        },
        "substrate_ingredients": substrate_rows,
        "competitive_matrix": _competitive_matrix_summary(matrix_report),
        "next_substrate_slices": _next_substrate_slices(
            substrate_rows,
            matrix_report,
            blockers,
            max_next_slices=max_next_slices,
        ),
        "authority_boundary": _authority_boundary(),
    }


def render_markdown(report: Mapping[str, Any]) -> str:
    freshness = _mapping(report.get("freshness"))
    matrix = _mapping(report.get("competitive_matrix"))
    authority = _mapping(report.get("authority_boundary"))
    lines = [
        "# V12 Substrate Evidence Freshness Rollup",
        "",
        f"ok: `{_bool_text(report.get('ok'))}`",
        f"blockers: `{len(list(report.get('blockers') or []))}`",
        f"max age days: `{freshness.get('max_age_days', 0)}`",
        (
            "substrate fresh for planning: "
            f"`{_bool_text(freshness.get('substrate_fresh_for_planning'))}`"
        ),
        (
            "competitive matrix fresh for planning: "
            f"`{_bool_text(matrix.get('fresh_for_planning'))}`"
        ),
        (
            "competitive matrix snapshot age: "
            f"`{matrix.get('snapshot_age_days', 'unknown')}` days"
        ),
        "",
        "## Substrate Ingredients",
    ]
    for row in list(report.get("substrate_ingredients") or []):
        lines.append(
            "- "
            f"{row['id']}: ok=`{_bool_text(row['ok'])}`, "
            f"authority=`{_bool_text(row['authority_boundary_ok'])}`, "
            f"fresh=`{_bool_text(row['fresh_for_planning'])}`, "
            f"next=`{row['recommended_next_slice']}`"
        )
    if not report.get("substrate_ingredients"):
        lines.append("- none")

    lines.extend(["", "## Next Substrate Slices"])
    for item in list(report.get("next_substrate_slices") or []):
        lines.append(f"- {item}")
    if not report.get("next_substrate_slices"):
        lines.append("- none")

    lines.extend(
        [
            "",
            "## Authority Boundary",
            f"runtime authority: `{_bool_text(authority.get('runtime_authority'))}`",
            f"promotion authority: `{_bool_text(authority.get('promotion_authority'))}`",
            f"scheduler authority: `{_bool_text(authority.get('scheduler_authority'))}`",
            f"bridge write authority: `{_bool_text(authority.get('bridge_write_authority'))}`",
            f"network authority: `{_bool_text(authority.get('network_authority'))}`",
            "",
            "This rollup is read-only. It does not run live scheduler ticks, "
            "promote solvers, enqueue work, append bridge events, call network, "
            "or grant runtime authority.",
        ]
    )
    return "\n".join(lines) + "\n"


def _source_blockers(
    source_rollup: Mapping[str, Any],
    matrix_report: Mapping[str, Any],
    *,
    now_date: date,
    max_age_days: int,
) -> list[str]:
    blockers: list[str] = []
    if source_rollup.get("report_version") != INGREDIENT_ROLLUP_VERSION:
        blockers.append("ingredient_rollup_version_mismatch")
    if source_rollup.get("ok") is not True:
        blockers.append("ingredient_rollup_not_ok")
    if matrix_report.get("ok") is not True:
        for blocker in list(matrix_report.get("blockers") or []):
            blockers.append(f"competitive_matrix:{blocker}")

    rollup_date = _parse_report_date(source_rollup.get("generated_at_utc"))
    if rollup_date is None:
        blockers.append("ingredient_rollup_generated_at_invalid")
    else:
        age_days = _age_days(rollup_date, now_date)
        if age_days is None or age_days < 0:
            blockers.append("ingredient_rollup_generated_at_in_future")
        elif age_days > max_age_days:
            blockers.append("ingredient_rollup_age_exceeds_max_age")
    return sorted(set(blockers))


def _substrate_rows(
    source_rollup: Mapping[str, Any],
    *,
    rollup_age_days: int | None,
    max_age_days: int,
) -> list[dict[str, Any]]:
    by_id = {
        str(row.get("id", "")): _mapping(row)
        for row in list(source_rollup.get("ingredients") or [])
    }
    rows: list[dict[str, Any]] = []
    for ingredient_id in SUBSTRATE_INGREDIENT_IDS:
        row = by_id.get(ingredient_id, {})
        fresh = (
            row.get("ok") is True
            and row.get("authority_boundary_ok") is True
            and rollup_age_days is not None
            and 0 <= rollup_age_days <= max_age_days
        )
        rows.append({
            "id": ingredient_id,
            "ok": row.get("ok") is True,
            "authority_boundary_ok": row.get("authority_boundary_ok") is True,
            "blocker_count": _as_int(row.get("blocker_count")),
            "rollup_age_days": rollup_age_days,
            "fresh_for_planning": fresh,
            "recommended_next_slice": str(
                row.get("recommended_next_slice", "missing_source_row")
            ),
        })
    return rows


def _competitive_matrix_summary(matrix_report: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "ok": matrix_report.get("ok") is True,
        "snapshot_date": matrix_report.get("snapshot_date"),
        "snapshot_age_days": matrix_report.get("snapshot_age_days"),
        "freshness_audit_date": matrix_report.get("freshness_audit_date"),
        "fresh_for_planning": matrix_report.get("fresh_for_planning") is True,
        "historical_stale_allowed": (
            matrix_report.get("historical_stale_allowed") is True
        ),
        "priority_rows": [str(item) for item in list(
            matrix_report.get("priority_rows") or []
        )],
        "evidence_bearing_axis_count": len(
            list(matrix_report.get("evidence_bearing_axes") or [])
        ),
        "blockers": [str(item) for item in list(matrix_report.get("blockers") or [])],
    }


def _stale_windows(
    substrate_rows: Sequence[Mapping[str, Any]],
    matrix_report: Mapping[str, Any],
) -> list[dict[str, Any]]:
    stale: list[dict[str, Any]] = []
    for row in substrate_rows:
        if row.get("fresh_for_planning") is not True:
            stale.append({
                "id": str(row.get("id", "")),
                "kind": "substrate_ingredient",
                "age_days": row.get("rollup_age_days"),
                "reason": "ingredient_not_fresh_or_authority_not_clean",
            })
    if matrix_report.get("fresh_for_planning") is not True:
        stale.append({
            "id": "competitive_evidence_matrix",
            "kind": "planning_matrix",
            "age_days": matrix_report.get("snapshot_age_days"),
            "reason": (
                "historical_stale_allowed"
                if matrix_report.get("historical_stale_allowed") is True
                else "matrix_not_fresh_for_planning"
            ),
        })
    return stale


def _next_substrate_slices(
    substrate_rows: Sequence[Mapping[str, Any]],
    matrix_report: Mapping[str, Any],
    blockers: Sequence[str],
    *,
    max_next_slices: int,
) -> list[str]:
    if blockers:
        return [
            "fix_v12_substrate_freshness_rollup_blockers_before_claiming_planning_fresh"
        ]

    next_slices: list[str] = []
    if matrix_report.get("fresh_for_planning") is not True:
        priority = ",".join(
            str(item) for item in list(matrix_report.get("priority_rows") or [])
        )
        next_slices.append(
            "refresh_competitive_matrix_priority_rows_from_current_v12_proofs:"
            f"{priority or 'unspecified'}"
        )

    for row in substrate_rows:
        candidate = str(row.get("recommended_next_slice", ""))
        if candidate and candidate not in next_slices:
            next_slices.append(candidate)
    return next_slices[:max_next_slices]


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
        "external_writes_applied": False,
    }


def _parse_report_date(value: Any) -> date | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return _parse_utc(value).date()
    except ValueError:
        return None


def _age_days(start: date | None, now_date: date) -> int | None:
    if start is None:
        return None
    return (now_date - start).days


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


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

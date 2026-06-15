# SPDX-License-Identifier: BUSL-1.1
"""Validate the competitive evidence matrix freshness contract.

The competitive matrix is allowed to retain historical PROVEN/MEASURED
labels when the underlying artifacts are stale, but that stale state must
be explicit and machine-checkable. This validator separates two questions:

* Is the matrix freshness metadata internally valid?
* Is the matrix fresh enough to use for planning without a rerun?

Default validation exits 0 for a stale-but-explicitly-historical matrix.
Use ``--require-fresh`` when a workflow needs current <=N-day evidence.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date, datetime, timezone
import json
import re
import sys
from pathlib import Path
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MATRIX = ROOT / "docs" / "benchmarks" / "COMPETITIVE_EVIDENCE_MATRIX_2026.md"
DEFAULT_MAX_AGE_DAYS = 14


@dataclass(frozen=True)
class AxisLabel:
    axis: str
    title: str
    label: str

    @property
    def evidence_bearing(self) -> bool:
        label = self.label.upper().replace("_", "-")
        return bool(re.search(r"\b(PROVEN|MEASURED(?:-[A-Z0-9-]+)?)\b", label))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate COMPETITIVE_EVIDENCE_MATRIX_2026 freshness metadata.",
    )
    parser.add_argument(
        "--matrix",
        type=Path,
        default=DEFAULT_MATRIX,
        help="Competitive evidence matrix Markdown file.",
    )
    parser.add_argument(
        "--now",
        default=datetime.now(timezone.utc).date().isoformat(),
        help="UTC date for age calculation, YYYY-MM-DD. Defaults to today's UTC date.",
    )
    parser.add_argument(
        "--max-age-days",
        type=int,
        default=DEFAULT_MAX_AGE_DAYS,
        help="Freshness target for PROVEN/MEASURED rows.",
    )
    parser.add_argument(
        "--require-fresh",
        action="store_true",
        help="Fail when the snapshot is older than --max-age-days.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print machine-readable JSON.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        now = _parse_date(str(args.now))
    except ValueError as exc:
        print(f"competitive evidence freshness FAILED: {exc}", file=sys.stderr)
        return 2
    if args.max_age_days <= 0:
        print(
            "competitive evidence freshness FAILED: --max-age-days must be > 0",
            file=sys.stderr,
        )
        return 2
    try:
        text = args.matrix.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"competitive evidence freshness FAILED: {exc}", file=sys.stderr)
        return 2

    report = validate_matrix_freshness(
        text,
        now=now,
        max_age_days=args.max_age_days,
        require_fresh=bool(args.require_fresh),
    )
    if args.json:
        print(json.dumps(report, sort_keys=True))
    else:
        _print_human(report)
    return 0 if report["ok"] else 1


def validate_matrix_freshness(
    text: str,
    *,
    now: date,
    max_age_days: int = DEFAULT_MAX_AGE_DAYS,
    require_fresh: bool = False,
) -> dict[str, Any]:
    metadata = _extract_metadata(text)
    axes = _extract_axis_labels(text)
    blockers: list[str] = []

    snapshot_date = _metadata_date(metadata, "snapshot_date", blockers)
    audit_date = _metadata_date(metadata, "freshness_audit_date", blockers)
    metadata_max_age = _metadata_int(metadata, "max_age_days", blockers)
    status = str(metadata.get("status", "")).strip().lower()
    fresh_for_planning = _metadata_bool(metadata, "fresh_for_planning", blockers)
    historical_until_refreshed = _metadata_bool(
        metadata,
        "historical_labels_until_refreshed",
        blockers,
    )
    priority_rows = _metadata_rows(metadata, "priority_rows", blockers)
    priority_snapshot_date = _metadata_date_optional(
        metadata,
        "priority_rows_snapshot_date",
        blockers,
    )
    priority_audit_date = _metadata_date_optional(
        metadata,
        "priority_rows_freshness_audit_date",
        blockers,
    )
    priority_fresh_for_planning = _metadata_bool_optional(
        metadata,
        "priority_rows_fresh_for_planning",
        blockers,
    )

    if metadata_max_age is not None and metadata_max_age != max_age_days:
        blockers.append("metadata_max_age_days_mismatch")

    age_days: int | None = None
    stale = False
    if snapshot_date is not None:
        age_days = (now - snapshot_date).days
        if age_days < 0:
            blockers.append("snapshot_date_in_future")
        stale = age_days > max_age_days
        if fresh_for_planning is not None and fresh_for_planning is stale:
            blockers.append("fresh_for_planning_mismatch")

    stale_marked_historical = (
        status in {"historical_stale", "historical_snapshot", "historical"}
        and historical_until_refreshed is True
    )
    if stale and not stale_marked_historical:
        blockers.append("stale_evidence_not_marked_historical")
    if require_fresh and stale:
        blockers.append("snapshot_age_exceeds_max_age")

    if (
        audit_date is not None
        and snapshot_date is not None
        and audit_date < snapshot_date
    ):
        blockers.append("freshness_audit_before_snapshot")

    evidence_axes = [axis for axis in axes if axis.evidence_bearing]
    if not evidence_axes:
        blockers.append("no_proven_or_measured_axes_found")
    missing_priority = [
        row for row in priority_rows if row not in {axis.axis for axis in evidence_axes}
    ]
    if priority_rows and missing_priority:
        blockers.append(
            "priority_rows_not_evidence_bearing:" + ",".join(missing_priority)
        )
    if stale and not priority_rows:
        blockers.append("stale_priority_rows_missing")

    priority_age_days: int | None = None
    priority_stale: bool | None = None
    if priority_snapshot_date is not None:
        priority_age_days = (now - priority_snapshot_date).days
        if priority_age_days < 0:
            blockers.append("priority_rows_snapshot_date_in_future")
        priority_stale = priority_age_days > max_age_days
        if (
            priority_fresh_for_planning is not None
            and priority_fresh_for_planning is priority_stale
        ):
            blockers.append("priority_rows_fresh_for_planning_mismatch")
    if (
        priority_audit_date is not None
        and priority_snapshot_date is not None
        and priority_audit_date < priority_snapshot_date
    ):
        blockers.append("priority_rows_freshness_audit_before_snapshot")
    resolved_priority_fresh_for_planning = (
        False if priority_stale else True
        if priority_age_days is not None
        else priority_fresh_for_planning
    )
    if priority_fresh_for_planning is True and priority_snapshot_date is None:
        blockers.append("priority_rows_snapshot_date_missing_for_priority_refresh")
    if resolved_priority_fresh_for_planning is True and not priority_rows:
        blockers.append("priority_rows_missing_for_priority_refresh")

    return {
        "ok": not blockers,
        "schema_version": "waggledance.competitive_evidence_freshness.v1",
        "now": now.isoformat(),
        "max_age_days": max_age_days,
        "snapshot_date": snapshot_date.isoformat() if snapshot_date else None,
        "snapshot_age_days": age_days,
        "freshness_audit_date": audit_date.isoformat() if audit_date else None,
        "fresh_for_planning": (
            False if stale else True if age_days is not None else None
        ),
        "historical_stale_allowed": bool(stale and stale_marked_historical),
        "require_fresh": bool(require_fresh),
        "status": status or None,
        "priority_rows": priority_rows,
        "priority_rows_snapshot_date": (
            priority_snapshot_date.isoformat() if priority_snapshot_date else None
        ),
        "priority_rows_snapshot_age_days": priority_age_days,
        "priority_rows_freshness_audit_date": (
            priority_audit_date.isoformat() if priority_audit_date else None
        ),
        "priority_rows_fresh_for_planning": resolved_priority_fresh_for_planning,
        "evidence_bearing_axes": [
            {"axis": axis.axis, "title": axis.title, "label": axis.label}
            for axis in evidence_axes
        ],
        "blockers": blockers,
    }


def _extract_metadata(text: str) -> dict[str, str]:
    metadata: dict[str, str] = {}
    snapshot = re.search(
        r"^\*\*Evidence snapshot date:\*\*\s*(\d{4}-\d{2}-\d{2})\s*$",
        text,
        re.MULTILINE,
    )
    if snapshot:
        metadata["snapshot_date"] = snapshot.group(1)
    audit = re.search(
        r"^\*\*Freshness audit:\*\*\s*(\d{4}-\d{2}-\d{2})\b", text, re.MULTILINE
    )
    if audit:
        metadata["freshness_audit_date"] = audit.group(1)
    meta_line = re.search(r"^\*\*Freshness metadata:\*\*\s*(.+)$", text, re.MULTILINE)
    if meta_line:
        for match in re.finditer(r"`([a-z_]+)=([^`]+)`", meta_line.group(1)):
            metadata[match.group(1)] = match.group(2).strip()
    return metadata


def _extract_axis_labels(text: str) -> list[AxisLabel]:
    axes: list[AxisLabel] = []
    current_axis: tuple[str, str] | None = None
    for line in text.splitlines():
        heading = re.match(r"^###\s+([A-Z])\.\s+(.+?)\s*$", line)
        if heading:
            current_axis = (heading.group(1), heading.group(2).strip())
            continue
        if current_axis is None:
            continue
        label = re.match(r"^\*\s+\*\*Label:\*\*\s+(.+?)\s*$", line)
        if label:
            axes.append(
                AxisLabel(
                    axis=current_axis[0],
                    title=current_axis[1],
                    label=label.group(1).strip(),
                )
            )
    return axes


def _metadata_date(
    metadata: dict[str, str],
    key: str,
    blockers: list[str],
) -> date | None:
    value = metadata.get(key)
    if not value:
        blockers.append(f"{key}_missing")
        return None
    try:
        return _parse_date(value)
    except ValueError:
        blockers.append(f"{key}_invalid")
        return None


def _metadata_date_optional(
    metadata: dict[str, str],
    key: str,
    blockers: list[str],
) -> date | None:
    value = metadata.get(key)
    if not value:
        return None
    try:
        return _parse_date(value)
    except ValueError:
        blockers.append(f"{key}_invalid")
        return None


def _metadata_int(
    metadata: dict[str, str],
    key: str,
    blockers: list[str],
) -> int | None:
    value = metadata.get(key)
    if not value:
        blockers.append(f"{key}_missing")
        return None
    try:
        parsed = int(value)
    except ValueError:
        blockers.append(f"{key}_invalid")
        return None
    if parsed <= 0:
        blockers.append(f"{key}_invalid")
        return None
    return parsed


def _metadata_bool(
    metadata: dict[str, str],
    key: str,
    blockers: list[str],
) -> bool | None:
    value = metadata.get(key)
    if value is None:
        blockers.append(f"{key}_missing")
        return None
    normalized = value.strip().lower()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    blockers.append(f"{key}_invalid")
    return None


def _metadata_bool_optional(
    metadata: dict[str, str],
    key: str,
    blockers: list[str],
) -> bool | None:
    value = metadata.get(key)
    if value is None:
        return None
    normalized = value.strip().lower()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    blockers.append(f"{key}_invalid")
    return None


def _metadata_rows(
    metadata: dict[str, str],
    key: str,
    blockers: list[str],
) -> list[str]:
    value = metadata.get(key)
    if value is None:
        blockers.append(f"{key}_missing")
        return []
    rows = [item.strip().upper() for item in value.split(",") if item.strip()]
    invalid = [row for row in rows if not re.fullmatch(r"[A-Z]", row)]
    if invalid:
        blockers.append(f"{key}_invalid")
        return []
    return rows


def _parse_date(value: str) -> date:
    try:
        return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc).date()
    except ValueError as exc:
        raise ValueError(f"date must be YYYY-MM-DD, got {value!r}") from exc


def _print_human(report: dict[str, Any]) -> None:
    if report["ok"]:
        if report["fresh_for_planning"]:
            print(
                "competitive evidence freshness OK: "
                f"snapshot age {report['snapshot_age_days']}d <= {report['max_age_days']}d"
            )
        else:
            print(
                "competitive evidence freshness OK: "
                f"snapshot age {report['snapshot_age_days']}d is historical/stale; "
                "rerun required before planning use"
            )
        return
    print(
        "competitive evidence freshness FAILED: "
        + ", ".join(str(item) for item in report["blockers"]),
        file=sys.stderr,
    )


if __name__ == "__main__":
    raise SystemExit(main())

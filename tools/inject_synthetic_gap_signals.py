# SPDX-License-Identifier: BUSL-1.1
"""Inject deterministic synthetic gap signals into a ControlPlaneDB.

This is a **test substrate** tool, not a production data generator. It exists
so dev boxes that lack real user-facing chat traffic can still exercise the
runtime-gap-signal pipeline end-to-end:

    inject_synthetic_gap_signals.py
        -> ControlPlaneDB.record_runtime_gap_signal_many
        -> runtime_gap_signals rows
        -> tools/runtime_gap_signal_concurrency_histogram.py (R25 decision tool)

The 2026-05-10 R25 deferral decision was made against a healthy
``control_plane.db``; the 2026-05-11..2026-05-18 silence (no new rows while
``audit_log.db`` and ``case_store.db`` kept growing) is because this dev
environment runs benchmark/proof harness traffic, not user chat. This tool
unblocks dev-box validation of the R25 decision-tool, the counterfactual
evaluation scaffolding, and any future ingredient depending on
``runtime_gap_signals`` shape — without inventing a fleet that does not exist.

Codex RCO constraints 2026-05-20T11:16:59Z (task_id
wd-substrate-traffic-lock-resolution-2026-05-20):

* Refuses to target ``data/control_plane.db`` (the canonical production DB)
  unless BOTH ``--allow-live`` AND ``--synthetic-confirm`` are passed. Either
  alone is rejected.
* Every injected row carries ``kind="synthetic_gap_injection"`` and a
  ``signal_payload`` with ``synthetic=true``, plus ``source``, ``run_id``,
  ``cell_coord`` for downstream attribution.
* Dry-run by default; ``--apply`` is required to write.
* Synthetic data **cannot** justify R25 production rollout. The R25 decision
  tool may see the synthetic concurrency, but any verdict derived from a DB
  containing synthetic rows must be treated as test-substrate evidence only.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
from typing import Any, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from waggledance.core.storage.control_plane import ControlPlaneDB  # noqa: E402


SYNTHETIC_KIND = "synthetic_gap_injection"
DEFAULT_CELLS = (
    "hub",
    "bee_ops",
    "environment",
    "home_comfort",
    "logistics",
    "production",
    "safety_security",
)
LIVE_DB_GUARDED_PATHS = (
    # Canonical production path (relative). The check uses path basename and
    # parent dir name so worktree-prefixed copies also trigger the guard.
    Path("data") / "control_plane.db",
)
LIVE_DB_BASENAME = "control_plane.db"
LIVE_DB_PARENT = "data"


class SyntheticInjectionError(ValueError):
    """Raised when a synthetic injection cannot proceed safely."""

    def __init__(self, report: dict[str, Any]) -> None:
        super().__init__("; ".join(str(error) for error in report.get("errors", [])))
        self.report = report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Inject deterministic synthetic gap signals into a ControlPlaneDB. "
            "Test substrate only. Synthetic rows cannot justify R25 production "
            "rollout."
        ),
    )
    parser.add_argument(
        "--db",
        type=Path,
        required=True,
        help=(
            "Target ControlPlaneDB SQLite path. Refused for the canonical "
            "production path data/control_plane.db unless both --allow-live "
            "AND --synthetic-confirm are also passed."
        ),
    )
    parser.add_argument(
        "--cells",
        default=",".join(DEFAULT_CELLS),
        help=(
            "Comma-separated cell ids to populate. Default: 7-cell baseline "
            "matching configs/hex_cells.yaml."
        ),
    )
    parser.add_argument(
        "--rows-per-cell",
        type=int,
        default=10,
        help="Number of synthetic rows per cell (must be > 0).",
    )
    parser.add_argument(
        "--window-seconds",
        type=int,
        default=1,
        help=(
            "Concurrency window granularity. Synthetic rows are staggered so "
            "the R25 histogram observes N>=2 concurrent cells within this "
            "window when --concurrent-cells > 1."
        ),
    )
    parser.add_argument(
        "--concurrent-cells",
        type=int,
        default=4,
        help=(
            "How many cells emit in the same window (for R25 N>=K threshold "
            "exercises). Must be <= number of cells."
        ),
    )
    parser.add_argument(
        "--run-id",
        default=None,
        help=(
            "Identifier for this synthetic batch (used in payload + log). "
            "Default: synthetic-<utc-timestamp>."
        ),
    )
    parser.add_argument(
        "--source",
        default="dev-box-synthetic",
        help="Source label for downstream attribution (default: dev-box-synthetic).",
    )
    parser.add_argument(
        "--allow-live",
        action="store_true",
        help=(
            "Acknowledge that --db points at the canonical production "
            "data/control_plane.db. REQUIRED in addition to --synthetic-confirm "
            "before this tool will write to the live DB."
        ),
    )
    parser.add_argument(
        "--synthetic-confirm",
        action="store_true",
        help=(
            "Acknowledge that the rows being written are synthetic and cannot "
            "justify any R25 production rollout decision. REQUIRED in addition "
            "to --allow-live before this tool will write to the live DB."
        ),
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually write rows. Default is dry-run report.",
    )
    parser.add_argument(
        "--now",
        default=None,
        help="UTC timestamp anchor for synthetic observed_at (default: now).",
    )
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = inject_synthetic_signals(
            db_path=args.db,
            cells=tuple(c.strip() for c in args.cells.split(",") if c.strip()),
            rows_per_cell=args.rows_per_cell,
            concurrent_cells=args.concurrent_cells,
            window_seconds=args.window_seconds,
            run_id=args.run_id,
            source=args.source,
            allow_live=args.allow_live,
            synthetic_confirm=args.synthetic_confirm,
            apply=args.apply,
            now_utc=(
                _parse_utc(args.now) if args.now else datetime.now(timezone.utc)
            ),
        )
    except SyntheticInjectionError as exc:
        report = exc.report
        exit_code = int(report.get("exit_code", 2))
    else:
        # Honor the report's declared exit_code (e.g. refused_live_db -> 3).
        exit_code = int(report.get("exit_code", 0))

    if args.json:
        print(json.dumps(report, sort_keys=True))
    else:
        print(report["decision"])
        for reason in report.get("reasons", []):
            print(f"- {reason}")
        if report.get("apply") is False and report.get("would_write_rows"):
            print(
                f"dry-run: would write {report['would_write_rows']} synthetic rows. "
                "Pass --apply to write."
            )
        if report.get("apply") and report.get("wrote_rows"):
            print(
                f"wrote {report['wrote_rows']} synthetic rows tagged "
                f"kind={SYNTHETIC_KIND!r} run_id={report.get('run_id')!r}."
            )
        for error in report.get("errors", []):
            print(f"- {error}", file=sys.stderr)
    return exit_code


def inject_synthetic_signals(
    *,
    db_path: Path,
    cells: Sequence[str],
    rows_per_cell: int,
    concurrent_cells: int,
    window_seconds: int,
    run_id: str | None,
    source: str,
    allow_live: bool,
    synthetic_confirm: bool,
    apply: bool,
    now_utc: datetime,
) -> dict[str, Any]:
    """Evaluate + optionally write synthetic gap signals to ``db_path``."""
    db_path = Path(db_path)
    if not cells:
        raise SyntheticInjectionError(
            {
                "decision": "invalid_arguments",
                "ok": False,
                "errors": ["at least one cell id is required"],
                "exit_code": 2,
            }
        )
    if rows_per_cell <= 0:
        raise SyntheticInjectionError(
            {
                "decision": "invalid_arguments",
                "ok": False,
                "errors": ["--rows-per-cell must be a positive integer"],
                "exit_code": 2,
            }
        )
    if window_seconds <= 0:
        raise SyntheticInjectionError(
            {
                "decision": "invalid_arguments",
                "ok": False,
                "errors": ["--window-seconds must be a positive integer"],
                "exit_code": 2,
            }
        )
    if concurrent_cells <= 0 or concurrent_cells > len(cells):
        raise SyntheticInjectionError(
            {
                "decision": "invalid_arguments",
                "ok": False,
                "errors": [
                    "--concurrent-cells must be in 1..len(cells); "
                    f"got {concurrent_cells} with {len(cells)} cells"
                ],
                "exit_code": 2,
            }
        )

    if _looks_like_live_db(db_path):
        if not (allow_live and synthetic_confirm):
            return {
                "decision": "refused_live_db",
                "ok": False,
                "apply": False,
                "errors": [
                    (
                        f"refusing to target the canonical production DB at "
                        f"{db_path}: --allow-live AND --synthetic-confirm are "
                        f"both required to write into it. Pass both flags only "
                        f"after acknowledging that synthetic rows cannot "
                        f"justify R25 production rollout."
                    )
                ],
                "db_path": str(db_path),
                "exit_code": 3,
            }

    run_id_resolved = run_id or _default_run_id(now_utc)
    total_rows = rows_per_cell * len(cells)
    plan = _build_signal_plan(
        cells=cells,
        rows_per_cell=rows_per_cell,
        concurrent_cells=concurrent_cells,
        window_seconds=window_seconds,
        now_utc=now_utc,
        run_id=run_id_resolved,
        source=source,
    )

    if not apply:
        return {
            "decision": "dry_run_ready",
            "ok": True,
            "apply": False,
            "would_write_rows": total_rows,
            "db_path": str(db_path),
            "run_id": run_id_resolved,
            "source": source,
            "cells": list(cells),
            "rows_per_cell": rows_per_cell,
            "concurrent_cells": concurrent_cells,
            "window_seconds": window_seconds,
            "kind": SYNTHETIC_KIND,
            "reasons": [
                (
                    "dry-run: synthetic rows are deterministic and "
                    "tagged synthetic=true; cannot justify R25 production "
                    "decisions"
                ),
            ],
            "exit_code": 0,
        }

    if not db_path.parent.exists():
        db_path.parent.mkdir(parents=True, exist_ok=True)

    with ControlPlaneDB(db_path) as cp:
        records = cp.record_runtime_gap_signal_many(plan)

    return {
        "decision": "applied",
        "ok": True,
        "apply": True,
        "wrote_rows": len(records),
        "db_path": str(db_path),
        "run_id": run_id_resolved,
        "source": source,
        "cells": list(cells),
        "rows_per_cell": rows_per_cell,
        "concurrent_cells": concurrent_cells,
        "window_seconds": window_seconds,
        "kind": SYNTHETIC_KIND,
        "reasons": [
            (
                "applied synthetic rows tagged synthetic=true; downstream "
                "tools must treat any verdict derived from this DB as "
                "test-substrate evidence only"
            ),
        ],
        "exit_code": 0,
    }


def _build_signal_plan(
    *,
    cells: Sequence[str],
    rows_per_cell: int,
    concurrent_cells: int,
    window_seconds: int,
    now_utc: datetime,
    run_id: str,
    source: str,
) -> list[dict[str, Any]]:
    """Build the deterministic list of synthetic signal rows to insert."""
    plan: list[dict[str, Any]] = []
    base_ts = now_utc.astimezone(timezone.utc).replace(microsecond=0)
    for row_index in range(rows_per_cell):
        # All `concurrent_cells` cells share the same observed_at window so
        # the R25 histogram observes N>=concurrent_cells within one window;
        # the remaining cells stagger across later windows.
        for cell_pos, cell in enumerate(cells):
            if cell_pos < concurrent_cells:
                offset_seconds = row_index * window_seconds
            else:
                offset_seconds = (
                    row_index * window_seconds
                    + (cell_pos - concurrent_cells + 1) * window_seconds
                )
            observed_at = (base_ts + timedelta(seconds=offset_seconds)).isoformat()
            payload = json.dumps(
                {
                    "synthetic": True,
                    "source": source,
                    "run_id": run_id,
                    "cell_coord": cell,
                    "row_index": row_index,
                    "cell_position": cell_pos,
                    "window_seconds": window_seconds,
                },
                sort_keys=True,
            )
            plan.append(
                {
                    "kind": SYNTHETIC_KIND,
                    "family_kind": "synthetic_family",
                    "cell_coord": cell,
                    "signal_payload": payload,
                    "weight": 1.0,
                    "observed_at": observed_at,
                }
            )
    return plan


def _looks_like_live_db(db_path: Path) -> bool:
    """Return True iff ``db_path`` is the canonical production ControlPlaneDB.

    The canonical production location is ``data/control_plane.db`` (relative
    to the project root). We match on basename plus parent-directory name so
    that worktree-prefixed paths and absolute paths both trigger the guard.
    """
    resolved = Path(db_path).expanduser()
    if resolved.name != LIVE_DB_BASENAME:
        return False
    parent_name = resolved.parent.name
    if parent_name == LIVE_DB_PARENT:
        return True
    # Also catch absolute paths that resolve to project-root/data/...
    try:
        resolved_abs = resolved.resolve()
    except (OSError, RuntimeError):
        return False
    parts = resolved_abs.parts
    if len(parts) >= 2 and parts[-2] == LIVE_DB_PARENT and parts[-1] == LIVE_DB_BASENAME:
        return True
    return False


def _default_run_id(now_utc: datetime) -> str:
    stamp = now_utc.astimezone(timezone.utc).strftime("%Y%m%dt%H%M%S%fz")
    return f"synthetic-{stamp}"


def _parse_utc(value: str) -> datetime:
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


if __name__ == "__main__":
    raise SystemExit(main())

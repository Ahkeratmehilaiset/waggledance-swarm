# SPDX-License-Identifier: BUSL-1.1
"""Production runtime_gap_signal concurrency histogram.

The R25 (3D hex per-cell DB sharding) go/no-go decision hinges on the
question: "how often does production have N concurrently-active hex cells
writing to ``runtime_gap_signals`` over a 1 s window?"

Run A-D + Run E + Run F + Option B (PR #223) measurement track shows:
- N=1 writer: tolerable for any realistic SLA after Option B.
- N=2..3 writers: still tolerable.
- N=4+ writers: knee point — write p99 jumps 65 percent.

If production never crosses the N=4 knee, R25 sharding is over-engineering.
If production routinely hits N=4+, R25 is the structural answer.

This tool reads a ``ControlPlaneDB`` SQLite file (production or staging
snapshot) and produces the concurrency histogram needed for the decision.
It is read-only — it does not modify the DB or write to ``runtime_gap_signals``.

Usage:
    python -m tools.runtime_gap_signal_concurrency_histogram \
        --db /path/to/control_plane.db \
        --window-seconds 1 \
        --out-json runtime_gap_concurrency.json
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable


def _iter_signal_timestamps(
    db_path: Path,
) -> Iterable[tuple[datetime, str]]:
    """Yield (timestamp, cell_coord) for every runtime_gap_signal row.

    Uses ``read_uncommitted`` so a live production DB can be analyzed
    without coordinating with the writer. The schema is owned by
    ``ControlPlaneDB`` — we touch only ``created_at`` and ``cell_coord``,
    both of which are stable contract fields.
    """
    conn = sqlite3.connect(str(db_path), isolation_level=None)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        cur = conn.execute(
            "SELECT created_at, cell_coord FROM runtime_gap_signals "
            "WHERE cell_coord IS NOT NULL ORDER BY created_at ASC"
        )
        for row in cur:
            ts_raw = row["created_at"]
            if ts_raw is None:
                continue
            try:
                ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
            except ValueError:
                continue
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            yield ts, str(row["cell_coord"])
    finally:
        conn.close()


def _window_key(ts: datetime, window_seconds: int, epoch: datetime) -> int:
    delta = (ts - epoch).total_seconds()
    return int(delta // window_seconds)


def build_histogram(
    db_path: Path,
    window_seconds: int = 1,
) -> dict:
    """Compute the per-cell + windowed-concurrency histogram."""
    per_cell_count: Counter = Counter()
    windows: dict[int, set[str]] = defaultdict(set)
    window_writes: Counter = Counter()
    first_ts: datetime | None = None
    last_ts: datetime | None = None

    for ts, cell in _iter_signal_timestamps(db_path):
        if first_ts is None:
            first_ts = ts
        last_ts = ts
        per_cell_count[cell] += 1

    if first_ts is None:
        return {
            "db_path": str(db_path),
            "window_seconds": window_seconds,
            "row_count": 0,
            "error": "no runtime_gap_signal rows with cell_coord",
        }

    # Second pass: group by window using first_ts as epoch.
    epoch = first_ts.replace(microsecond=0)
    for ts, cell in _iter_signal_timestamps(db_path):
        wk = _window_key(ts, window_seconds, epoch)
        windows[wk].add(cell)
        window_writes[wk] += 1

    if not windows:
        return {
            "db_path": str(db_path),
            "window_seconds": window_seconds,
            "row_count": 0,
            "error": "no windows could be built",
        }

    concurrency_per_window = [len(cells) for cells in windows.values()]
    writes_per_window = list(window_writes.values())

    # Histogram of windows by concurrency level
    concurrency_hist: Counter = Counter(concurrency_per_window)
    total_windows = len(windows)

    # SLA threshold percentages (how many windows have >= N concurrent cells)
    sla_buckets: dict[str, dict[str, float | int]] = {}
    for n_threshold in (2, 3, 4, 5, 6, 7):
        windows_at_or_above = sum(
            1 for c in concurrency_per_window if c >= n_threshold
        )
        sla_buckets[f"N_ge_{n_threshold}"] = {
            "windows": windows_at_or_above,
            "pct_of_total_windows": round(
                100.0 * windows_at_or_above / total_windows, 3
            ),
        }

    return {
        "db_path": str(db_path),
        "window_seconds": window_seconds,
        "first_ts_utc": first_ts.isoformat(),
        "last_ts_utc": last_ts.isoformat() if last_ts else None,
        "span_seconds": (
            (last_ts - first_ts).total_seconds() if last_ts else 0.0
        ),
        "row_count": int(sum(per_cell_count.values())),
        "distinct_cells": len(per_cell_count),
        "per_cell_count": dict(per_cell_count.most_common()),
        "total_windows": total_windows,
        "concurrency_per_window_summary": {
            "min": min(concurrency_per_window),
            "p50": int(statistics.median(concurrency_per_window)),
            "p99": _percentile(concurrency_per_window, 0.99),
            "max": max(concurrency_per_window),
            "mean": round(statistics.fmean(concurrency_per_window), 3),
        },
        "writes_per_window_summary": {
            "min": min(writes_per_window),
            "p50": int(statistics.median(writes_per_window)),
            "p99": _percentile(writes_per_window, 0.99),
            "max": max(writes_per_window),
            "mean": round(statistics.fmean(writes_per_window), 3),
        },
        "concurrency_histogram": dict(sorted(concurrency_hist.items())),
        "sla_thresholds": sla_buckets,
        "r25_decision_signal": _r25_signal(sla_buckets, total_windows),
    }


def _percentile(values: list[int], percentile: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    rank = (len(ordered) - 1) * percentile
    idx = int(rank)
    if idx >= len(ordered):
        return ordered[-1]
    return ordered[idx]


def _r25_signal(
    sla_buckets: dict[str, dict[str, float | int]], total_windows: int
) -> dict[str, str]:
    """Translate the histogram into an R25 go/no-go signal.

    Thresholds match the Run A-F + Option B measurement track:
    - Above N=4 knee: 2D write p99 grows 65 percent per added writer.
    - Below N=4 knee: roughly linear growth, tolerable for sub-150 ms SLA.
    """
    pct_n4 = sla_buckets.get("N_ge_4", {}).get("pct_of_total_windows", 0)
    pct_n2 = sla_buckets.get("N_ge_2", {}).get("pct_of_total_windows", 0)

    if total_windows < 60:
        verdict = "insufficient-data"
        rationale = (
            f"only {total_windows} windows observed; collect at least 60 s "
            "(or larger window for production scale) before deciding"
        )
    elif pct_n4 > 50:
        verdict = "r25-strongly-recommended"
        rationale = (
            f">{pct_n4:.1f} percent of windows have >=4 concurrent writer "
            "cells. Write p99 will exceed 150 ms in the majority of windows. "
            "Sharding (per-cell DB or sharded runtime_gap_signals) is justified."
        )
    elif pct_n4 > 10:
        verdict = "r25-consider"
        rationale = (
            f"{pct_n4:.1f} percent of windows have >=4 concurrent writer "
            "cells (knee region). R25 deferral is acceptable until any "
            "specific write-p99 SLA miss is observed in production; "
            "re-run with a larger sample if uncertain."
        )
    elif pct_n2 > 5:
        verdict = "r25-defer"
        rationale = (
            f"only {pct_n4:.1f} percent of windows have >=4 concurrent cells "
            f"and {pct_n2:.1f} percent have >=2. Production is below the "
            "Run F knee; the Option B read/cross-table fix (PR #223) "
            "addresses the operational regimes that matter. R25 is "
            "over-engineering for this traffic pattern."
        )
    else:
        verdict = "r25-not-needed"
        rationale = (
            f"only {pct_n2:.1f} percent of windows have >=2 concurrent "
            "cells. Production traffic is single-branch-dominant. R25 "
            "sharding has no observable benefit for this pattern."
        )
    return {"verdict": verdict, "rationale": rationale}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db",
        type=Path,
        required=True,
        help="Path to ControlPlaneDB SQLite file (production or snapshot)",
    )
    parser.add_argument(
        "--window-seconds",
        type=int,
        default=1,
        help="Concurrency window granularity (default 1 s)",
    )
    parser.add_argument(
        "--out-json",
        type=Path,
        default=None,
        help="Optional output JSON path. If omitted, prints summary to stdout only.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.db.exists():
        print(f"error: --db path does not exist: {args.db}", file=sys.stderr)
        return 1

    result = build_histogram(args.db, window_seconds=args.window_seconds)

    if "error" in result:
        print(f"error: {result['error']}", file=sys.stderr)
        return 2

    # Compact human-readable summary first
    print(f"# runtime_gap_signal concurrency histogram")
    print(f"# db_path: {result['db_path']}")
    print(f"# window: {result['window_seconds']} s")
    print(f"# span: {result['span_seconds']:.0f} s ({result['row_count']} rows)")
    print(f"# distinct cells: {result['distinct_cells']}")
    print()
    print("Per-cell write count:")
    for cell, count in result["per_cell_count"].items():
        print(f"  {cell:20s} {count:8d}")
    print()
    print(f"Concurrency-per-window stats:")
    cps = result["concurrency_per_window_summary"]
    print(f"  min={cps['min']}  p50={cps['p50']}  p99={cps['p99']}  "
          f"max={cps['max']}  mean={cps['mean']}")
    print()
    print("SLA-threshold buckets (pct of windows with >= N concurrent cells):")
    for k, v in result["sla_thresholds"].items():
        print(f"  {k}: {v['pct_of_total_windows']:6.2f} %  "
              f"({v['windows']} of {result['total_windows']} windows)")
    print()
    verdict = result["r25_decision_signal"]
    print(f"R25 decision signal: {verdict['verdict']}")
    print(f"  {verdict['rationale']}")

    if args.out_json is not None:
        args.out_json.parent.mkdir(parents=True, exist_ok=True)
        args.out_json.write_text(
            json.dumps(result, indent=2, sort_keys=True), encoding="utf-8"
        )
        print(f"\nwrote {args.out_json}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

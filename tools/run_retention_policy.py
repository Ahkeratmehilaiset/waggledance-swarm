# SPDX-License-Identifier: BUSL-1.1
"""Run retention pruning on the WaggleDance SQLite databases.

Audit H33+H34: audit_log.db and world_store.db grow unbounded with no
DELETE-pruning anywhere in the production code path. This is a one-shot
operator/cron-callable script that prunes per
``waggledance.core.storage.retention_policy.default_rules()``.

Usage:
    python -m tools.run_retention_policy                    # dry-run (preview)
    python -m tools.run_retention_policy --apply            # actually delete
    python -m tools.run_retention_policy --json out.json    # dump report

Defaults (matching the audit findings):
  audit_log.audit            -> 30 days  (compliance evidence)
  world_store.world_snapshots -> 7 days   (high-volume telemetry)
  case_store.case_trajectories -> 90 days (learning funnel)

Operator should run this on a schedule (e.g. once a day via cron /
systemd timer) once the dry-run preview looks correct.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Make `waggledance` importable when invoked as `python tools/run_retention_policy.py`.
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from waggledance.core.storage.retention_policy import (  # noqa: E402
    RetentionPolicy,
    default_rules,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Prune old rows from audit_log/world_store/case_store per H33+H34.",
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="Actually DELETE + VACUUM. Without this flag the script only "
             "counts rows that WOULD be pruned (safe preview).",
    )
    parser.add_argument(
        "--data-dir", default="data",
        help="Path to the data/ directory (default: ./data)",
    )
    parser.add_argument(
        "--json", dest="json_out", default=None,
        help="Optional path to dump the PruneReport as JSON.",
    )
    args = parser.parse_args(argv)

    rules = default_rules(args.data_dir)
    if not rules:
        print(f"No retention-eligible DBs found under {args.data_dir!r}.")
        return 0

    policy = RetentionPolicy(rules)
    report = policy.run_once(dry_run=not args.apply)

    print("# Retention report")
    print(f"  mode:            {'APPLY (DELETE + VACUUM)' if args.apply else 'DRY-RUN (preview only)'}")
    print(f"  rules evaluated: {report.rules_evaluated}")
    total = sum(report.rows_deleted.values())
    label = "rows deleted" if args.apply else "rows that WOULD be deleted"
    print(f"  {label:30s} {total:,}")
    for db_table, count in sorted(report.rows_deleted.items()):
        print(f"    {db_table:35s} {count:,}")
    if report.errors:
        print(f"  errors: {len(report.errors)}")
        for err in report.errors:
            print(f"    {err}")

    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps(report.to_dict(), indent=2), encoding="utf-8",
        )
        print(f"\nReport JSON written to {args.json_out}")

    return 1 if report.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())

# SPDX-License-Identifier: BUSL-1.1
"""Derive the solver-first served ratio from the durable chat-served ledger.

``core/route_telemetry.py`` computes ``solver_first_served_ratio`` from
in-memory per-process counters; that value dies with the process and cannot
back a claim-window assertion. This tool derives the same statistic from the
DURABLE evidence instead: the hash-chained chat-served ledger written on the
serve path (``waggledance/core/magma/chat_served_ledger.py``). The
``receipt_index`` claim-window side-stream indexes receipts over the same
durable store; the ledger is the canonical record this tool reads, which is
why the tool is named after the ledger and not the index.

Semantics mirror ``RouteTelemetry.solver_first_served_stats`` exactly:

* the denominator is served responses (``served_pending`` entries), optionally
  narrowed by ``--served-route-type``;
* the numerator counts ``route_type`` membership in the solver set ONLY within
  the denominator set, so the ratio can never exceed 1.0 even when the caller
  narrows the denominator to exclude a solver route;
* an empty denominator yields ratio 0.0.

Honesty boundaries: the output is measurement only. It never contains a
``claim_safe`` field, never names filesystem paths, and never asserts a named
production window unless the caller supplied explicit boundaries. Receipt
coverage (receipted / gap / unresolved-pending) is reported alongside the
ratio so a number derived from an incomplete or holed window cannot read as
complete. Chain verification failure is a structured rejection, not a partial
answer. So are a nonexistent ledger path (an absent file is no evidence, not
clean zero evidence) and any per-served_id lifecycle violation -- a duplicate
pending, a terminal without a pending, or a second terminal. Chain
verification proves hash linkage only, so this tool walks the ledger in order
and enforces the same lifecycle rule as ``chat_served_accounting`` itself
instead of attributing it to the chain: one logical serve is never counted
twice and an orphan terminal is never silently ignored.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from waggledance.core.magma.chat_served_ledger import (  # noqa: E402
    GAP_TERMINAL,
    RECEIPT_TERMINAL,
    SERVED_PENDING,
    LedgerCorruptionError,
    read_entries,
    verify_chain,
)

REPORT_SCHEMA = "wd.served_ratio_from_ledger.v1"
DEFAULT_SOLVER_ROUTE_TYPES = ("solver",)
UNKNOWN_ROUTE_TYPE = "unknown"

# Per-served_id lifecycle states for the ordered ledger walk (mirrors the
# state tokens of ``chat_served_accounting.derive_coverage``).
LIFECYCLE_PENDING = "pending"
LIFECYCLE_RECEIPTED = "receipted"
LIFECYCLE_GAP = "gap"

EXIT_OK = 0
EXIT_REJECTED = 3


class DerivationRejected(Exception):
    """Fail-closed rejection: the ledger cannot back a derived ratio."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _parse_entry_ts(value: object) -> datetime | None:
    """Parse a ledger ``ts_utc`` token; None when unparseable.

    Comparison happens on parsed datetimes, never raw strings: raw string
    ordering mis-ranks differing sub-second precisions ('.' sorts before 'Z'),
    the exact defect class fixed on the bridge resolvers in PR #1613/#1614.

    A timezone-NAIVE parse result (a hash-valid token like
    ``2026-08-21T09:00:00`` with no offset) is treated as unparseable rather
    than compared: mixing naive and aware datetimes raises TypeError, and
    guessing an offset would silently place the entry in or out of a window.
    """
    if not isinstance(value, str) or not value:
        return None
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed


def _entry_route_type(entry: Mapping[str, Any]) -> str:
    metadata = entry.get("metadata")
    if isinstance(metadata, Mapping):
        route_type = metadata.get("route_type")
        if isinstance(route_type, str) and route_type:
            return route_type
    return UNKNOWN_ROUTE_TYPE


def lifecycle_violation(state: str | None, entry_type: object) -> str | None:
    """Per-served_id lifecycle rule as a pure predicate; ``None`` means ok.

    Mirrors ``chat_served_accounting._lifecycle_violation`` (the bc3 rule)
    verbatim: exactly one ``served_pending`` per served_id, then at most one
    terminal, never a terminal before its pending, and no unknown entry
    types. ``state`` is the served_id's state BEFORE ``entry_type`` is applied
    (``None`` when unseen).
    """
    if entry_type == SERVED_PENDING:
        if state is not None:
            return "duplicate_pending"
    elif entry_type in (RECEIPT_TERMINAL, GAP_TERMINAL):
        if state is None:
            return "terminal_without_pending"
        if state in (LIFECYCLE_RECEIPTED, LIFECYCLE_GAP):
            return "second_terminal"
    else:
        return "unknown_entry_type"
    return None


def derive_report(
    *,
    ledger_path: str,
    solver_route_types: Sequence[str] = DEFAULT_SOLVER_ROUTE_TYPES,
    served_route_types: Sequence[str] | None = None,
    window_start: str | None = None,
    window_end: str | None = None,
) -> dict[str, Any]:
    """Derive the ratio report from ``ledger_path``; raise DerivationRejected on
    anything that would make the number untrustworthy."""
    solver_set = frozenset(solver_route_types)
    served_set = frozenset(served_route_types) if served_route_types is not None else None
    if not solver_set:
        raise DerivationRejected("solver_route_types_empty")
    if served_set is not None and not served_set:
        raise DerivationRejected("served_route_types_empty")

    start_dt = _parse_entry_ts(window_start) if window_start is not None else None
    if window_start is not None and start_dt is None:
        raise DerivationRejected("window_start_unparseable")
    end_dt = _parse_entry_ts(window_end) if window_end is not None else None
    if window_end is not None and end_dt is None:
        raise DerivationRejected("window_end_unparseable")
    if start_dt is not None and end_dt is not None and start_dt > end_dt:
        raise DerivationRejected("window_start_after_end")
    windowed = start_dt is not None or end_dt is not None

    if not Path(ledger_path).exists():
        # A missing ledger must not read as a genuinely empty one: an
        # empty-but-present file is evidence of zero servings, an absent file
        # is no evidence at all. Without this guard a mistyped --ledger path
        # reports served_total=0 with evidence_complete=True.
        raise DerivationRejected("ledger_not_found")
    try:
        entries, torn_tail = read_entries(ledger_path)
    except LedgerCorruptionError as exc:
        raise DerivationRejected(f"ledger_corrupt:{exc}") from exc
    chain = verify_chain(entries)
    if not chain.ok:
        raise DerivationRejected(
            f"chain_invalid:index={chain.broken_at}:reason={chain.reason}"
        )

    # ONE ordered lifecycle walk over the whole ledger, mirroring
    # chat_served_accounting.derive_coverage. Chain verification proves hash
    # linkage only -- it does NOT enforce the per-served_id lifecycle rule, so
    # a chain-valid ledger CAN carry a duplicate pending for one served_id
    # (one logical serve counted twice), a terminal with no pending (an
    # orphan that would be silently ignored), or a second (even conflicting)
    # terminal. Terminals used to be collected independently of pendings,
    # which is exactly how the first two shapes slipped past the
    # second-terminal guard (codex-lead-1 2026-08-22T04:30:52Z finding).
    # Every violation is a fail-closed rejection, never a partial answer.
    lifecycle: dict[str, str] = {}
    for entry in entries:
        served_id = str(entry.get("served_id"))
        etype = entry.get("entry_type")
        violation = lifecycle_violation(lifecycle.get(served_id), etype)
        if violation == "unknown_entry_type":
            # Defensive: verify_chain already rejects non-well-formed entry
            # types, but the walk must never count what it cannot classify.
            raise DerivationRejected(f"unknown_entry_type:{etype}")
        if violation is not None:
            raise DerivationRejected(f"{violation}_for_served_id:{served_id}")
        if etype == SERVED_PENDING:
            lifecycle[served_id] = LIFECYCLE_PENDING
        elif etype == RECEIPT_TERMINAL:
            lifecycle[served_id] = LIFECYCLE_RECEIPTED
        else:
            lifecycle[served_id] = LIFECYCLE_GAP

    served_total = 0
    solver_total = 0
    excluded_unknown = 0
    out_of_window = 0
    per_route: dict[str, int] = {}
    receipted = 0
    gap = 0
    pending = 0
    for entry in entries:
        if entry.get("entry_type") != SERVED_PENDING:
            continue
        if windowed:
            entry_dt = _parse_entry_ts(entry.get("ts_utc"))
            if entry_dt is None:
                # A window was requested but this entry cannot be placed in
                # or out of it; a silent guess either way corrupts the count.
                raise DerivationRejected("entry_ts_unparseable_under_window")
            if (start_dt is not None and entry_dt < start_dt) or (
                end_dt is not None and entry_dt > end_dt
            ):
                out_of_window += 1
                continue
        route_type = _entry_route_type(entry)
        if served_set is not None:
            if route_type == UNKNOWN_ROUTE_TYPE:
                # Membership in a narrowed denominator cannot be proven for an
                # entry without a route_type; excluding it silently would hide
                # evidence, so it is excluded AND counted.
                excluded_unknown += 1
                continue
            if route_type not in served_set:
                continue
        served_total += 1
        per_route[route_type] = per_route.get(route_type, 0) + 1
        # Subset invariant: the numerator is counted ONLY inside the served
        # denominator, mirroring RouteTelemetry.solver_first_served_stats.
        # The UNKNOWN_ROUTE_TYPE exclusion is UNCONDITIONAL, mirroring the
        # served-side guard above: an entry whose route_type metadata is
        # missing can never be PROVEN solver-first, so it never counts in the
        # numerator even when the caller explicitly configures
        # --solver-route-type unknown (fail-closed numerator).
        if route_type in solver_set and route_type != UNKNOWN_ROUTE_TYPE:
            solver_total += 1
        # The walk above guarantees exactly one pending per served_id, so
        # served_total counts DISTINCT served_ids (the canonical denominator)
        # and coverage is that served_id's final lifecycle state.
        coverage = lifecycle.get(str(entry.get("served_id")))
        if coverage == LIFECYCLE_RECEIPTED:
            receipted += 1
        elif coverage == LIFECYCLE_GAP:
            gap += 1
        else:
            pending += 1

    ratio = (solver_total / served_total) if served_total else 0.0
    return {
        "schema": REPORT_SCHEMA,
        "solver_first_served_total": solver_total,
        "served_total": served_total,
        "solver_first_served_ratio": ratio,
        "per_route_served": dict(sorted(per_route.items())),
        "receipt_coverage": {
            "receipted": receipted,
            "gap": gap,
            "pending": pending,
        },
        "gapless": gap == 0,
        "evidence_complete": (not torn_tail) and pending == 0,
        "torn_tail": torn_tail,
        "chain_ok": True,
        "window": {
            "start_ts_utc": window_start,
            "end_ts_utc": window_end,
            "windowed": windowed,
            "named_production_window": False,
            "out_of_window_served_entries": out_of_window,
        },
        "excluded_unknown_route_count": excluded_unknown,
        "solver_route_types": sorted(solver_set),
        "served_route_types": sorted(served_set) if served_set is not None else None,
        "flags": {
            "measurement_only": True,
            "derived_from": "durable_ledger",
            "runtime_authority_granted": False,
        },
    }


def compare_with_telemetry(
    report: Mapping[str, Any],
    telemetry_stats: Mapping[str, Any],
) -> dict[str, Any]:
    """Report divergence between the durable and telemetry ratios.

    Neither source is 'corrected'; divergence is surfaced for a human or a
    later reconciliation gate to judge.
    """
    telemetry_ratio = telemetry_stats.get("solver_first_served_ratio")
    if not isinstance(telemetry_ratio, (int, float)) or isinstance(telemetry_ratio, bool):
        raise DerivationRejected("telemetry_snapshot_missing_ratio")
    ledger_ratio = float(report["solver_first_served_ratio"])
    delta = abs(float(telemetry_ratio) - ledger_ratio)
    return {
        "telemetry_ratio": float(telemetry_ratio),
        "ledger_ratio": ledger_ratio,
        "abs_delta": delta,
        "diverges": delta > 1e-9,
    }


def _parse_args(argv: Iterable[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Derive solver_first_served_ratio from the durable chat-served "
            "ledger (measurement only; never asserts claim safety)."
        )
    )
    parser.add_argument("--ledger", required=True, help="Path to the chat-served ledger JSONL.")
    parser.add_argument(
        "--solver-route-type",
        action="append",
        dest="solver_route_types",
        default=None,
        help="Route type counted as solver-first (repeatable; default: solver).",
    )
    parser.add_argument(
        "--served-route-type",
        action="append",
        dest="served_route_types",
        default=None,
        help="Narrow the served denominator to these route types (repeatable).",
    )
    parser.add_argument("--window-start-ts", default=None, help="Inclusive ISO-8601 UTC lower bound.")
    parser.add_argument("--window-end-ts", default=None, help="Inclusive ISO-8601 UTC upper bound.")
    parser.add_argument(
        "--telemetry-snapshot",
        default=None,
        help="Optional JSON file holding a RouteTelemetry solver_first_served_stats result to compare against.",
    )
    parser.add_argument("--json", action="store_true", help="Emit the JSON report to stdout.")
    return parser.parse_args(list(argv) if argv is not None else None)


def main(argv: Iterable[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        report = derive_report(
            ledger_path=args.ledger,
            solver_route_types=tuple(args.solver_route_types or DEFAULT_SOLVER_ROUTE_TYPES),
            served_route_types=(
                tuple(args.served_route_types) if args.served_route_types is not None else None
            ),
            window_start=args.window_start_ts,
            window_end=args.window_end_ts,
        )
        if args.telemetry_snapshot is not None:
            try:
                with open(args.telemetry_snapshot, encoding="utf-8") as handle:
                    telemetry_stats = json.load(handle)
            except (OSError, json.JSONDecodeError) as exc:
                raise DerivationRejected(f"telemetry_snapshot_unreadable:{exc}") from exc
            if not isinstance(telemetry_stats, Mapping):
                raise DerivationRejected("telemetry_snapshot_not_an_object")
            report["telemetry_divergence"] = compare_with_telemetry(report, telemetry_stats)
    except DerivationRejected as exc:
        rejection = {
            "schema": REPORT_SCHEMA,
            "derived": False,
            "reason": exc.reason,
            "flags": {
                "measurement_only": True,
                "derived_from": "durable_ledger",
                "runtime_authority_granted": False,
            },
        }
        print(json.dumps(rejection, sort_keys=True))
        return EXIT_REJECTED
    report["derived"] = True
    if args.json:
        print(json.dumps(report, sort_keys=True))
    else:
        print(
            "solver_first_served_ratio="
            f"{report['solver_first_served_ratio']:.6f} "
            f"({report['solver_first_served_total']}/{report['served_total']}); "
            f"coverage receipted={report['receipt_coverage']['receipted']} "
            f"gap={report['receipt_coverage']['gap']} "
            f"pending={report['receipt_coverage']['pending']}"
        )
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())

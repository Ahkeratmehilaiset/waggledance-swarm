#!/usr/bin/env python3
"""Stage-1 vector-indexer stub — reads the MAGMA vector event log and
reports what the FAISS state *would be* if the events were applied.

**Does not write FAISS.** This tool exists so that the event-sourcing
projection pattern (MAGMA events → FAISS indices) has a concrete,
testable consumer before the real indexer lands in Stage 2. The real
indexer will reuse this module's `replay()` function and add the
FAISS writes + `vector.commit_applied` emission.

Invocation:

    python tools/vector_indexer.py                      # human-readable
    python tools/vector_indexer.py --json               # machine summary
    python tools/vector_indexer.py --since evt_abc123   # replay from
                                                         # event onward
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from waggledance.core.magma import vector_events  # noqa: E402


@dataclass
class CellState:
    cell_id: str
    upsert_requests: int = 0
    delete_requests: int = 0
    committed_count: int = 0
    last_commit_id: str | None = None
    # signature per solver_id — captures the authoritative shape each
    # upsert references. When a delete lands, solver_id is removed.
    signatures: dict[str, str] = field(default_factory=dict)


@dataclass
class ReplayReport:
    events_seen: int = 0
    events_skipped: int = 0
    cells: dict[str, CellState] = field(default_factory=dict)
    unknown_event_types: dict[str, int] = field(default_factory=dict)
    first_event_id: str | None = None
    last_event_id: str | None = None


def _state_for(cells: dict[str, CellState], cell_id: str) -> CellState:
    if cell_id not in cells:
        cells[cell_id] = CellState(cell_id=cell_id)
    return cells[cell_id]


def replay(path: Path | str | None = None,
           since_event_id: str | None = None) -> ReplayReport:
    """Walk the event log and build the per-cell projection. If
    `since_event_id` is given, start from the event AFTER that id.

    Intentionally tolerant: unknown event types are counted but do not
    abort the replay. Stage 2 will add a strict mode."""
    report = ReplayReport()
    active = since_event_id is None

    for event in vector_events.read_events(path):
        eid = event.event_id()
        if not active:
            if eid == since_event_id:
                active = True
            continue
        if report.first_event_id is None:
            report.first_event_id = eid
        report.last_event_id = eid
        report.events_seen += 1

        cell = _state_for(report.cells, event.cell_id)
        if event.event == vector_events.EVT_SOLVER_UPSERTED:
            # Informational only — the ledger write has happened. The
            # corresponding vector.upsert_requested is what drives the
            # cell state.
            pass
        elif event.event == vector_events.EVT_VECTOR_UPSERT_REQUESTED:
            cell.upsert_requests += 1
            sig = event.payload.get("signature", "")
            mid = event.payload.get("model_id")
            if mid:
                cell.signatures[mid] = sig
        elif event.event == vector_events.EVT_VECTOR_DELETE_REQUESTED:
            cell.delete_requests += 1
            mid = event.payload.get("model_id")
            if mid and mid in cell.signatures:
                del cell.signatures[mid]
        elif event.event == vector_events.EVT_VECTOR_COMMIT_APPLIED:
            cell.committed_count = int(event.payload.get("vector_count", 0))
            cell.last_commit_id = event.payload.get("faiss_commit_id")
        else:
            report.unknown_event_types[event.event] = (
                report.unknown_event_types.get(event.event, 0) + 1
            )
            report.events_skipped += 1

    return report


def _format_report(report: ReplayReport) -> str:
    lines = [
        "vector-indexer (stub) replay report",
        "",
        f"events seen:    {report.events_seen}",
        f"events skipped: {report.events_skipped}",
        f"first event:    {report.first_event_id or '—'}",
        f"last event:     {report.last_event_id or '—'}",
        "",
        f"{'cell':12} {'upserts':>8} {'deletes':>8} "
        f"{'committed':>10} {'signatures':>10}  last_commit",
        "-" * 80,
    ]
    for name in sorted(report.cells):
        c = report.cells[name]
        lines.append(
            f"{c.cell_id:12} {c.upsert_requests:>8} {c.delete_requests:>8} "
            f"{c.committed_count:>10} {len(c.signatures):>10}  "
            f"{c.last_commit_id or '—'}"
        )
    if report.unknown_event_types:
        lines.append("")
        lines.append("unknown event types:")
        for name, n in sorted(report.unknown_event_types.items()):
            lines.append(f"  {name}: {n}")
    lines.append("")
    return "\n".join(lines)


def _to_json(report: ReplayReport) -> dict:
    return {
        "events_seen": report.events_seen,
        "events_skipped": report.events_skipped,
        "first_event_id": report.first_event_id,
        "last_event_id": report.last_event_id,
        "unknown_event_types": dict(report.unknown_event_types),
        "cells": {
            name: {
                "cell_id": c.cell_id,
                "upsert_requests": c.upsert_requests,
                "delete_requests": c.delete_requests,
                "committed_count": c.committed_count,
                "last_commit_id": c.last_commit_id,
                "signatures": dict(c.signatures),
            }
            for name, c in report.cells.items()
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", type=Path, default=None,
                    help="Event log path (default from env var or "
                         "data/vector/events.jsonl)")
    ap.add_argument("--since", type=str, default=None,
                    help="Replay from the event AFTER this event_id")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    report = replay(args.log, since_event_id=args.since)
    if args.json:
        print(json.dumps(_to_json(report), indent=2, default=str))
    else:
        print(_format_report(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())

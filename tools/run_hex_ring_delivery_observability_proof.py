#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""Offline proof for the hex ring-delivery observability summary.

Lead-dispatched fable lane (48h hex-mesh autonomy roadmap). The
``summarize_ring_delivery_batch`` aggregate is the swarm's multi-instance
coordination-readiness signal: it must make a ring fragmenting on TOPOLOGY
(well-formed messages routed across an adjacency / hierarchy boundary they do
not have) distinguishable AT A GLANCE from one fragmenting on malformed input,
by the stable ``blocked_by_class`` -- never by parsing free-text reasons.

This proof builds three deterministic batches over a sample topology and asserts:

* a HEALTHY batch (all valid edges) delivers fully -- success ratio 1.0, no blocks;
* a TOPOLOGY-FRAGMENTING batch (well-formed messages crossing the wrong edge)
  blocks entirely under the ``topology_boundary`` class -- success ratio 0.0;
* a SCHEMA-MALFORMED batch (unknown cells) blocks entirely under the
  ``schema_invalid`` class;
* the two failure modes are DISTINGUISHABLE by ``blocked_by_class`` (a fragmenting
  ring is not confused with malformed input);
* per-summary accounting is internally consistent (category and message-kind
  totals reconcile to the blocked / delivered counts); and
* NO summary ever reports transport (``transport_applied`` is always False).

Observability-only: it reads already-computed deliveries; it performs no
topology mutation, no routing influence, and no transport.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from waggledance.core.hex_topology.cell_message_contract import (  # noqa: E402
    CellMessage,
    make_message,
)
from waggledance.core.hex_topology.ring_messaging import (  # noqa: E402
    RING_BLOCK_CLASS_SCHEMA,
    RING_BLOCK_CLASS_TOPOLOGY,
    deliver_batch,
    summarize_ring_delivery_batch,
)


REPORT_VERSION = "wd.hex_ring_delivery_observability_proof.v0"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out-dir",
        type=Path,
        required=True,
        help="New output directory for the proof. It must not already exist.",
    )
    parser.add_argument(
        "--now",
        default=None,
        help="Optional UTC timestamp override such as 2026-06-27T00:00:00Z.",
    )
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = build_hex_ring_delivery_observability_proof(
            out_dir=args.out_dir,
            now_utc=_parse_utc(args.now) if args.now else None,
        )
    except (OSError, ValueError) as exc:
        print(
            f"hex ring delivery observability proof FAILED: {exc}",
            file=sys.stderr,
        )
        return 1

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    elif report["ok"]:
        print(
            "hex ring delivery observability proof OK: "
            f"{report['proof_path']}"
        )
    else:
        print(
            "hex ring delivery observability proof FAILED: "
            f"{', '.join(report['blockers'])}",
            file=sys.stderr,
        )
    return 0 if report["ok"] else 1


def build_hex_ring_delivery_observability_proof(
    *,
    out_dir: Path,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    generated_at = (now_utc or datetime.now(timezone.utc)).astimezone(
        timezone.utc
    )
    out_dir = out_dir.resolve()
    if out_dir.exists():
        raise ValueError(f"out_dir must not exist: {out_dir}")
    if not out_dir.parent.exists():
        raise ValueError(f"out_dir parent does not exist: {out_dir.parent}")
    out_dir.mkdir()

    topology = _topology()

    healthy = summarize_ring_delivery_batch(
        deliver_batch(topology, _healthy_batch())
    )
    fragmenting = summarize_ring_delivery_batch(
        deliver_batch(topology, _topology_fragmenting_batch())
    )
    malformed = summarize_ring_delivery_batch(
        deliver_batch(topology, _schema_malformed_batch())
    )

    proof_checks = {
        "healthy_ring_fully_delivers": (
            healthy["blocked_count"] == 0
            and healthy["delivered_count"] == healthy["total"]
            and healthy["total"] > 0
            and healthy["delivery_success_ratio"] == 1.0
        ),
        "topology_fragmenting_blocks_topology_class": (
            fragmenting["delivered_count"] == 0
            and fragmenting["blocked_count"] == fragmenting["total"]
            and fragmenting["total"] > 0
            and fragmenting["delivery_success_ratio"] == 0.0
            and fragmenting["blocked_by_class"]
            == {RING_BLOCK_CLASS_TOPOLOGY: fragmenting["total"]}
        ),
        "schema_malformed_blocks_schema_class": (
            malformed["delivered_count"] == 0
            and malformed["blocked_count"] == malformed["total"]
            and malformed["total"] > 0
            and malformed["blocked_by_class"]
            == {RING_BLOCK_CLASS_SCHEMA: malformed["total"]}
        ),
        "topology_vs_schema_distinguishable": (
            set(fragmenting["blocked_by_class"])
            != set(malformed["blocked_by_class"])
            and RING_BLOCK_CLASS_TOPOLOGY not in malformed["blocked_by_class"]
            and RING_BLOCK_CLASS_SCHEMA not in fragmenting["blocked_by_class"]
        ),
        "blocked_category_totals_reconcile": all(
            sum(s["blocked_by_category"].values()) == s["blocked_count"]
            and sum(s["blocked_by_class"].values()) == s["blocked_count"]
            for s in (healthy, fragmenting, malformed)
        ),
        "by_message_kind_accounting_consistent": all(
            sum(
                slot["delivered"] + slot["blocked"]
                for slot in s["by_message_kind"].values()
            )
            == s["total"]
            and sum(slot["delivered"] for slot in s["by_message_kind"].values())
            == s["delivered_count"]
            for s in (healthy, fragmenting, malformed)
        ),
        "no_transport_in_any_summary": all(
            s["transport_applied"] is False
            for s in (healthy, fragmenting, malformed)
        ),
    }
    blockers = [
        name for name, passed in proof_checks.items() if passed is not True
    ]
    report = {
        "report_version": REPORT_VERSION,
        "generated_at_utc": _format_utc(generated_at),
        "ok": not blockers,
        "blockers": blockers,
        "proof_checks": proof_checks,
        "healthy_summary": healthy,
        "topology_fragmenting_summary": fragmenting,
        "schema_malformed_summary": malformed,
    }
    proof_path = out_dir / "hex_ring_delivery_observability_proof.json"
    report["proof_path"] = str(proof_path)
    proof_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report


def _topology() -> dict[str, Any]:
    # root -> {a, b}; a -> {a1, a2}; a<->b neighbors; a1<->a2 neighbors.
    return {
        "cells": {
            "root": {
                "cell_id": "root", "parent_cell_id": None,
                "child_cell_ids": ["a", "b"], "neighbor_cell_ids": [],
            },
            "a": {
                "cell_id": "a", "parent_cell_id": "root",
                "child_cell_ids": ["a1", "a2"], "neighbor_cell_ids": ["b"],
            },
            "b": {
                "cell_id": "b", "parent_cell_id": "root",
                "child_cell_ids": [], "neighbor_cell_ids": ["a"],
            },
            "a1": {
                "cell_id": "a1", "parent_cell_id": "a",
                "child_cell_ids": [], "neighbor_cell_ids": ["a2"],
            },
            "a2": {
                "cell_id": "a2", "parent_cell_id": "a",
                "child_cell_ids": [], "neighbor_cell_ids": ["a1"],
            },
        },
    }


def _healthy_batch() -> list[CellMessage]:
    return [
        _msg("a", "b", "ring_request"),          # b is a neighbor of a
        _msg("a1", "a2", "neighbor_observation"),  # a2 is a neighbor of a1
        _msg("a1", "a", "child_to_parent"),       # a is parent of a1
        _msg("a", "a1", "parent_to_child"),       # a1 is a child of a
    ]


def _topology_fragmenting_batch() -> list[CellMessage]:
    # well-formed messages routed across a boundary they do NOT have.
    return [
        _msg("a", "a1", "ring_request"),       # a1 is not a neighbor of a
        _msg("a1", "root", "child_to_parent"),  # root is not parent of a1
        _msg("a", "b", "parent_to_child"),     # b is not a child of a
    ]


def _schema_malformed_batch() -> list[CellMessage]:
    return [
        _msg("a", "ghost", "ring_request"),   # to_cell_id unknown
        _msg("ghost", "a", "ring_request"),   # from_cell_id unknown
    ]


def _msg(from_cell_id: str, to_cell_id: str, kind: str) -> CellMessage:
    return make_message(
        from_cell_id=from_cell_id,
        to_cell_id=to_cell_id,
        kind=kind,
    )


def _parse_utc(value: str) -> datetime:
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    parsed = datetime.fromisoformat(normalized)
    if (
        parsed.tzinfo is None
        or parsed.utcoffset() != timezone.utc.utcoffset(parsed)
    ):
        raise ValueError(f"--now requires a UTC timestamp: {value}")
    return parsed.astimezone(timezone.utc)


def _format_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


if __name__ == "__main__":
    raise SystemExit(main())

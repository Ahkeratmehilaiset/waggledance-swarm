# SPDX-License-Identifier: BUSL-1.1
"""Divergence breakdown for hex canary mirror runs (read-only).

The proof runner (#1033) reports HOW MANY mirrored routing decisions
diverge; storyboard panels 4->5 readiness also needs WHICH routes would
change. This tool re-runs the same read-only mirror over a decisions
JSONL (closed input contract shared with the proof runner) and breaks
the divergences down:

* per intent: decision/divergence counts, divergence rate, and the mesh
  cells the divergent decisions would have landed in;
* top divergent intents (bounded --top);
* bounded privacy-safe detail records for the divergent decisions —
  query digest + length, intent, both cell ids, mesh method,
  classification; NEVER the raw query text.

Same contract as the rest of the canary chain: read-only shadow
comparison, no routing influence, no authority; digest-bound artifact;
all claim gates emitted false; deterministic via --now.

Exit codes: 0 ok (divergences are data, not a failure), 2 invalid
arguments/input records, 3 input file missing.
"""
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

from waggledance.core.hex_topology.canary_mirror import (  # noqa: E402
    CanaryMirrorError,
    build_canary_route_comparison,
)
from waggledance.core.magma.canonical import sha256_digest  # noqa: E402
from tools.run_hex_canary_mirror_proof import (  # noqa: E402
    CLAIM_GATES,
    DEMO_DECISIONS,
    MAX_SOURCE_LABEL_CHARS,
    _parse_utc,
    _read_decisions,
    _validate_decision_keys,
)

BREAKDOWN_VERSION = "wd.v12.hex_canary_divergence_breakdown.v0"
CLAIM_LABEL = "MEASURED_LOCAL_SHADOW_MIRROR"
DEFAULT_TOP = 10
DEFAULT_MAX_DETAIL = 50

# The only fields a divergence detail record may carry (privacy-closed).
DETAIL_FIELDS = (
    "query_digest",
    "query_length",
    "intent",
    "intent_cell_id",
    "mesh_cell_id",
    "mesh_method",
    "classification",
    "production_cell_id",
    "quality_path",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Break hex canary mirror divergences down per intent and mesh "
            "cell (read-only shadow comparison; privacy-safe details)."
        ),
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--input", type=Path, default=None)
    source.add_argument(
        "--demo",
        action="store_true",
        help="Use the proof runner's deterministic demo corpus.",
    )
    parser.add_argument("--now", default=None)
    parser.add_argument(
        "--top",
        type=int,
        default=DEFAULT_TOP,
        help=f"Max intents in top_divergent_intents (default {DEFAULT_TOP}).",
    )
    parser.add_argument(
        "--max-detail",
        type=int,
        default=DEFAULT_MAX_DETAIL,
        help=(
            "Max divergence detail records emitted "
            f"(default {DEFAULT_MAX_DETAIL}; truncation is reported)."
        ),
    )
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.top < 1 or args.max_detail < 0:
        print("--top must be >= 1 and --max-detail >= 0", file=sys.stderr)
        return 2

    if args.now is not None:
        now = _parse_utc(args.now)
        if now is None:
            print(f"--now is not a valid ISO-8601 instant: {args.now!r}", file=sys.stderr)
            return 2
    else:
        now = datetime.now(timezone.utc)

    if args.demo:
        decisions = [dict(d) for d in DEMO_DECISIONS]
        source_label = "demo"
    else:
        if not args.input.exists():
            print(f"input file not found: {args.input}", file=sys.stderr)
            return 3
        try:
            decisions = _read_decisions(args.input)
        except ValueError as exc:
            print(f"invalid input: {exc}", file=sys.stderr)
            return 2
        source_label = str(args.input)[:MAX_SOURCE_LABEL_CHARS]

    try:
        artifact = build_divergence_breakdown(
            decisions=decisions,
            source_label=source_label,
            now=now,
            top=args.top,
            max_detail=args.max_detail,
        )
    except (CanaryMirrorError, ValueError) as exc:
        print(f"divergence breakdown refused: {exc}", file=sys.stderr)
        return 2

    payload = json.dumps(artifact, indent=2, sort_keys=True)
    if args.out is not None:
        args.out.write_text(payload + "\n", encoding="utf-8")
    if args.json:
        print(payload)
    else:
        _print_summary(artifact)
    return 0


def build_divergence_breakdown(
    *,
    decisions: Sequence[Mapping[str, Any]],
    source_label: str,
    now: datetime,
    top: int = DEFAULT_TOP,
    max_detail: int = DEFAULT_MAX_DETAIL,
) -> dict[str, Any]:
    """Mirror each decision and aggregate divergences per intent/cell.

    Fail-closed on malformed decisions (same closed contract as the
    proof runner); a breakdown must never silently skip records.
    """
    by_intent: dict[str, dict[str, Any]] = {}
    details: list[dict[str, Any]] = []
    divergence_count = 0

    for index, decision in enumerate(decisions):
        _validate_decision_keys(index, decision)
        record = build_canary_route_comparison(
            query=decision["query"],
            intent=decision["intent"],
            production_capability_id=decision["production_capability_id"],
            quality_path=decision["quality_path"],
            production_cell_id=decision.get("production_cell_id"),
        )
        intent = record["intent"]
        bucket = by_intent.setdefault(
            intent,
            {"decisions": 0, "divergences": 0, "divergent_mesh_cells": {}},
        )
        bucket["decisions"] += 1
        if record["agreement"] is True:
            continue
        divergence_count += 1
        bucket["divergences"] += 1
        cells = bucket["divergent_mesh_cells"]
        cells[record["mesh_cell_id"]] = cells.get(record["mesh_cell_id"], 0) + 1
        details.append({field: record[field] for field in DETAIL_FIELDS})

    for bucket in by_intent.values():
        bucket["divergence_rate"] = (
            bucket["divergences"] / bucket["decisions"]
            if bucket["decisions"]
            else 0.0
        )
        bucket["divergent_mesh_cells"] = dict(
            sorted(bucket["divergent_mesh_cells"].items())
        )

    top_divergent = sorted(
        (
            {"intent": intent, **bucket}
            for intent, bucket in by_intent.items()
            if bucket["divergences"] > 0
        ),
        key=lambda item: (-item["divergences"], item["intent"]),
    )[:top]

    detail_truncated = len(details) > max_detail
    core: dict[str, Any] = {
        "report_version": BREAKDOWN_VERSION,
        "claim_label": CLAIM_LABEL,
        "generated_at_utc": now.astimezone(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z"),
        "input_source": source_label[:MAX_SOURCE_LABEL_CHARS],
        "decision_count": len(list(decisions)),
        "divergence_count": divergence_count,
        "divergence_rate": (
            divergence_count / len(list(decisions)) if decisions else 0.0
        ),
        "by_intent": dict(sorted(by_intent.items())),
        "top_divergent_intents": top_divergent,
        "divergence_details": details[:max_detail],
        "divergence_details_truncated": detail_truncated,
        "divergence_details_omitted_count": max(0, len(details) - max_detail),
        # Read-only contract literals (mirrors the canary chain).
        "no_runtime_mutation": True,
        "runtime_authority_granted": False,
        "routing_influence_applied": False,
        "production_decision_unchanged": True,
    }
    for gate in CLAIM_GATES:
        core.setdefault(gate, False)
    return {**core, "canonical_digest": sha256_digest(core)}


def _print_summary(artifact: Mapping[str, Any]) -> None:
    print(
        f"hex canary divergence breakdown @ {artifact['generated_at_utc']} "
        f"({artifact['claim_label']}, read-only)"
    )
    print(
        f"  source: {artifact['input_source']} "
        f"({artifact['decision_count']} decisions, "
        f"{artifact['divergence_count']} divergent, "
        f"rate {artifact['divergence_rate']:.4f})"
    )
    for item in artifact["top_divergent_intents"]:
        cells = ", ".join(
            f"{cell}:{count}"
            for cell, count in item["divergent_mesh_cells"].items()
        )
        print(
            f"    intent {item['intent']}: {item['divergences']}/"
            f"{item['decisions']} divergent -> {cells}"
        )
    if artifact["divergence_details_truncated"]:
        print(
            "  details truncated: "
            f"{artifact['divergence_details_omitted_count']} omitted"
        )


if __name__ == "__main__":
    raise SystemExit(main())

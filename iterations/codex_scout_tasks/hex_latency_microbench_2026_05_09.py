# SPDX-License-Identifier: Apache-2.0
"""Repeatable hex latency scout microbenchmark.

Phase D Priority 2 measures pure hex topology/ring messaging paths without
network, providers, Ollama, ChromaDB, or product data writes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from waggledance.application.services.hex_topology_registry import (
    HexTopologyRegistry,
)
from waggledance.core.hex_topology import parent_child_relations as pcr
from waggledance.core.hex_topology import ring_messaging as rm
from waggledance.core.hex_topology.cell_message_contract import make_message


SNAPSHOT_PATH = ROOT / "iterations" / "codex_scout_tasks" / (
    "hex_latency_snapshot_2026_05_09.json"
)


def _read_snapshot() -> dict:
    return json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))


def _snapshot_sha12() -> str:
    data = SNAPSHOT_PATH.read_bytes()
    return hashlib.sha256(data).hexdigest()[:12]


def _time_operation(name: str, count: int, fn, samples: int = 1) -> dict:
    timings: list[float] = []
    for _ in range(samples):
        t0 = time.perf_counter()
        fn()
        timings.append((time.perf_counter() - t0) * 1000)
    ordered = sorted(timings)
    return {
        "operation": name,
        "count": count,
        "samples": samples,
        "total_ms": round(sum(timings), 4),
        "p50_ms": round(statistics.median(ordered), 4),
        "p95_ms": round(ordered[int((len(ordered) - 1) * 0.95)], 4),
        "p99_ms": round(ordered[int((len(ordered) - 1) * 0.99)], 4),
        "max_ms": round(max(ordered), 4),
    }


def _build_topology(cell_count: int, degree: int, children_per_parent: int) -> dict:
    cells: dict[str, dict] = {}
    for i in range(cell_count):
        cid = f"c{i:05d}"
        neighbors = [
            f"c{((i + offset) % cell_count):05d}"
            for offset in range(1, degree + 1)
        ]
        parent = None if i == 0 else f"c{((i - 1) // children_per_parent):05d}"
        first_child = i * children_per_parent + 1
        children = [
            f"c{j:05d}"
            for j in range(first_child, min(first_child + children_per_parent, cell_count))
        ]
        cells[cid] = {
            "cell_id": cid,
            "parent_cell_id": parent,
            "child_cell_ids": children,
            "neighbor_cell_ids": neighbors,
            "live_state": "shadow_only",
            "subdivision_state": "leaf",
        }
    return {"schema_version": 1, "cells": cells}


def _ring_messages(cell_count: int, count: int) -> list:
    return [
        make_message(
            from_cell_id=f"c{(i % cell_count):05d}",
            to_cell_id=f"c{((i % cell_count) + 1) % cell_count:05d}",
            kind="ring_request",
            payload={"i": i},
        )
        for i in range(count)
    ]


def _child_messages(cell_count: int, children_per_parent: int, count: int) -> list:
    out = []
    for i in range(count):
        child_idx = 1 + (i % max(1, cell_count - 1))
        parent_idx = (child_idx - 1) // children_per_parent
        out.append(
            make_message(
                from_cell_id=f"c{child_idx:05d}",
                to_cell_id=f"c{parent_idx:05d}",
                kind="child_to_parent",
                payload={"i": i},
            )
        )
    return out


def run() -> dict:
    snapshot = _read_snapshot()
    topo_cfg = snapshot["synthetic_topology"]
    workload = snapshot["workload"]

    cell_count = int(topo_cfg["cell_count"])
    degree = int(topo_cfg["ring_neighbor_degree"])
    children_per_parent = int(topo_cfg["child_count_per_parent"])
    topology = _build_topology(cell_count, degree, children_per_parent)

    ring_count = int(workload["ring_messages"])
    relation_count = int(workload["relation_queries"])
    registry_neighbor_count = int(workload["registry_neighbor_queries"])
    registry_origin_count = int(workload["registry_origin_queries"])

    ring_messages = _ring_messages(cell_count, ring_count)
    child_messages = _child_messages(cell_count, children_per_parent, ring_count)
    relation_ids = [f"c{(i % cell_count):05d}" for i in range(relation_count)]

    reg = HexTopologyRegistry(
        config_path=str(ROOT / snapshot["registry_config_path"]),
        agents=[],
    )
    registry_cell_ids = list(reg.cells.keys())
    queries = list(snapshot["query_samples"])

    operations = [
        _time_operation(
            "ring_messaging.deliver_batch ring_request",
            ring_count,
            lambda: rm.deliver_batch(topology, ring_messages),
        ),
        _time_operation(
            "ring_messaging.deliver_batch child_to_parent",
            ring_count,
            lambda: rm.deliver_batch(topology, child_messages),
        ),
        _time_operation(
            "parent_child_relations.neighbors_of repeated",
            relation_count,
            lambda: [pcr.neighbors_of(topology, cid) for cid in relation_ids],
        ),
        _time_operation(
            "HexTopologyRegistry.get_neighbor_cells repeated",
            registry_neighbor_count,
            lambda: [
                reg.get_neighbor_cells(registry_cell_ids[i % len(registry_cell_ids)])
                for i in range(registry_neighbor_count)
            ],
        ),
        _time_operation(
            "HexTopologyRegistry.select_origin_cell repeated",
            registry_origin_count,
            lambda: [
                reg.select_origin_cell(queries[i % len(queries)])
                for i in range(registry_origin_count)
            ],
        ),
    ]

    return {
        "benchmark_id": "r18-hex-latency-microbench-2026-05-09",
        "schema_version": 1,
        "snapshot_path": str(SNAPSHOT_PATH),
        "snapshot_sha256_12": _snapshot_sha12(),
        "counts": {
            "synthetic_cells": cell_count,
            "ring_neighbor_degree": degree,
            "ring_messages": ring_count,
            "relation_queries": relation_count,
            "registry_neighbor_queries": registry_neighbor_count,
            "registry_origin_queries": registry_origin_count,
            "default_config_cells": len(registry_cell_ids),
        },
        "operations": operations,
        "threshold_10ms": [op for op in operations if op["max_ms"] > 10.0],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-json", default="")
    args = parser.parse_args()
    result = run()
    text = json.dumps(result, indent=2, sort_keys=True)
    print(text)
    if args.out_json:
        out = Path(args.out_json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

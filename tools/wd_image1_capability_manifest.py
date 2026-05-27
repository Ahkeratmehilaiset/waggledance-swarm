# SPDX-License-Identifier: BUSL-1.1
"""Repo/runtime-read-only capability manifest for the WD Image #1 storyboard.

The tool deliberately separates the visual claim from repo-safe wording.
It does not mutate runtime state, bridge state, GitHub state, or tracked files.
Local executable proofs may create ephemeral temp files and delete them before
returning.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import sys
import tempfile
from typing import Iterable, Sequence

import yaml


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from waggledance.core.hex_topology.cell_message_contract import make_message
from waggledance.core.hex_topology.parent_child_relations import (
    ancestors_of,
    children_of,
    siblings_of,
)
from waggledance.core.hex_topology.ring_messaging import deliver_batch
from waggledance.core.hex_topology.subdivision_operator import (
    apply_plan_to_topology,
    plan_subdivision,
)
from waggledance.core.autonomy_growth import (
    AutogrowthScheduler,
    GapSignal,
    LowRiskGrower,
    OUTCOME_AUTO_PROMOTED,
    RuntimeQuery,
    RuntimeQueryRouter,
    digest_signals_into_intents,
    is_low_risk_family,
)
from waggledance.core.storage.control_plane import ControlPlaneDB
from waggledance.application.services.hex_topology_registry import (
    HexTopologyRegistry,
)
from waggledance.core.hex_cell_topology import ALL_CELLS, HexCellTopology

STATUS_IMPLEMENTED = "implemented"
STATUS_PARTIAL = "partial"
STATUS_PLANNED = "planned"
STATUS_BLOCKED = "blocked"
VALID_STATUSES = {
    STATUS_IMPLEMENTED,
    STATUS_PARTIAL,
    STATUS_PLANNED,
    STATUS_BLOCKED,
}


@dataclass(frozen=True)
class Evidence:
    path: str
    present: bool
    note: str


@dataclass(frozen=True)
class Capability:
    capability_id: str
    title: str
    image_claim: str
    safe_statement: str
    status: str
    claim_safe: bool
    evidence: tuple[Evidence, ...]
    gaps: tuple[str, ...]
    next_smallest_pr: str
    proof: dict | None = None

    def to_dict(self) -> dict:
        data = asdict(self)
        data["evidence"] = [asdict(item) for item in self.evidence]
        if self.proof is None:
            data.pop("proof", None)
        return data


def _exists(root: Path, rel_path: str) -> bool:
    return (root / rel_path).exists()


def _evidence(root: Path, items: Iterable[tuple[str, str]]) -> tuple[Evidence, ...]:
    return tuple(
        Evidence(path=path, present=_exists(root, path), note=note)
        for path, note in items
    )


def _all_present(items: Sequence[Evidence]) -> bool:
    return all(item.present for item in items)


def _some_present(items: Sequence[Evidence]) -> bool:
    return any(item.present for item in items)


def _status_for(
    evidence: Sequence[Evidence],
    *,
    complete: bool = False,
    planned: bool = False,
) -> str:
    if complete and _all_present(evidence):
        return STATUS_IMPLEMENTED
    if _some_present(evidence):
        return STATUS_PARTIAL
    if planned:
        return STATUS_PLANNED
    return STATUS_BLOCKED


def _blocked_hex_mesh_entry_proof(
    *,
    missing_inputs: Sequence[str],
    current_config: dict | None = None,
) -> dict:
    route_order = [
        "language_detection",
        "hot_cache",
        "memory_context",
        "route_selection",
        "deterministic_solver",
        "hybrid_retrieval_8_cell",
        "hex_neighbor_assist_7_cell",
        "orchestrator_llm_fallback",
    ]
    return {
        "proof_id": "hex_mesh_entry_route_order_v1",
        "ok": False,
        "blocked_reason": "missing_required_inputs",
        "missing_inputs": list(missing_inputs),
        "proves_every_query_first_enters_mesh": False,
        "literal_claim_safe": False,
        "current_config": current_config or {
            "hybrid_retrieval_enabled": False,
            "hybrid_retrieval_mode": "unknown",
            "hybrid_retrieval_authoritative": False,
            "hex_mesh_enabled": False,
            "hex_mesh_cell_config_path": "configs/hex_cells.yaml",
        },
        "topologies": {
            "solver_retrieval": {
                "cell_count": 0,
                "cell_ids": [],
                "entry_point": "HybridRetrievalService.retrieve",
                "gated_by": "hybrid_retrieval.enabled",
            },
            "agent_routing": {
                "cell_count": 0,
                "cell_ids": [],
                "entry_point": "HexNeighborAssist.resolve",
                "gated_by": "hex_mesh.enabled",
            },
        },
        "chat_route_order": route_order,
        "pre_hex_steps": route_order[:5],
        "solver_retrieval_samples": [],
        "agent_routing_samples": [],
        "safe_conclusion": (
            "Required config files are missing, so no hex entry proof or "
            "literal first-entry mesh claim is safe for this root."
        ),
    }


def build_hex_mesh_entry_proof(root: Path | str = ROOT) -> dict:
    """Report current query-entry boundaries for the two hex topologies."""

    repo_root = Path(root)
    settings_path = repo_root / "configs" / "settings.yaml"
    if not settings_path.exists():
        return _blocked_hex_mesh_entry_proof(
            missing_inputs=("configs/settings.yaml",),
        )

    settings = _load_yaml_mapping(settings_path)
    hybrid_cfg = _nested_mapping(settings, "hybrid_retrieval")
    hex_mesh_cfg = _nested_mapping(settings, "hex_mesh")

    solver_topology = HexCellTopology()
    solver_samples = [
        {
            "query": "What is the temperature in Celsius?",
            "intent": "chat",
            "expected_cell": "thermal",
        },
        {
            "query": "calculate 2 plus 2",
            "intent": "math",
            "expected_cell": "math",
        },
        {
            "query": "How much electricity power does heating use?",
            "intent": "chat",
            "expected_cell": "energy",
        },
    ]
    solver_assignments = []
    for sample in solver_samples:
        assignment = solver_topology.assign_cell(
            str(sample["intent"]),
            str(sample["query"]),
        )
        solver_assignments.append({
            "query": sample["query"],
            "intent": sample["intent"],
            "expected_cell": sample["expected_cell"],
            "cell_id": assignment.cell_id,
            "method": assignment.method,
            "ring1": assignment.neighbors_ring1,
            "ring2": assignment.neighbors_ring2,
            "matched_expected": assignment.cell_id == sample["expected_cell"],
        })

    cell_config_path = str(
        hex_mesh_cfg.get("cell_config_path") or "configs/hex_cells.yaml"
    )
    cell_config_file = repo_root / cell_config_path
    hybrid_enabled = bool(hybrid_cfg.get("enabled", False))
    hybrid_mode = str(hybrid_cfg.get("mode", "shadow"))
    hex_mesh_enabled = bool(hex_mesh_cfg.get("enabled", False))
    current_config = {
        "hybrid_retrieval_enabled": hybrid_enabled,
        "hybrid_retrieval_mode": hybrid_mode,
        "hybrid_retrieval_authoritative": (
            hybrid_enabled and hybrid_mode == "authoritative"
        ),
        "hex_mesh_enabled": hex_mesh_enabled,
        "hex_mesh_cell_config_path": cell_config_path,
    }
    if not cell_config_file.exists():
        return _blocked_hex_mesh_entry_proof(
            missing_inputs=(cell_config_path,),
            current_config=current_config,
        )

    agent_registry = HexTopologyRegistry(
        config_path=str(cell_config_file),
    )
    agent_samples = [
        {
            "query": "bee hive swarm monitoring",
            "intent": "bee",
            "expected_cell": "bee_ops",
        },
        {
            "query": "safety fire alarm",
            "intent": "safety",
            "expected_cell": "safety_security",
        },
        {
            "query": "energy hvac lighting",
            "intent": "energy",
            "expected_cell": "home_comfort",
        },
    ]
    agent_assignments = []
    for sample in agent_samples:
        cell_id = agent_registry.select_origin_cell(
            str(sample["query"]),
            str(sample["intent"]),
        )
        neighbors = [
            cell.id for cell in agent_registry.get_neighbor_cells(str(cell_id))
        ] if cell_id else []
        agent_assignments.append({
            "query": sample["query"],
            "intent": sample["intent"],
            "expected_cell": sample["expected_cell"],
            "cell_id": cell_id,
            "ring1": neighbors,
            "matched_expected": cell_id == sample["expected_cell"],
        })

    route_order = [
        "language_detection",
        "hot_cache",
        "memory_context",
        "route_selection",
        "deterministic_solver",
        "hybrid_retrieval_8_cell",
        "hex_neighbor_assist_7_cell",
        "orchestrator_llm_fallback",
    ]
    pre_hex_steps = route_order[:5]
    ok = (
        len(ALL_CELLS) == 8
        and agent_registry.cell_count == 7
        and all(item["matched_expected"] for item in solver_assignments)
        and all(item["matched_expected"] for item in agent_assignments)
        and hybrid_enabled is True
        and hybrid_mode in {"shadow", "candidate", "authoritative"}
    )
    return {
        "proof_id": "hex_mesh_entry_route_order_v1",
        "ok": ok,
        "proves_every_query_first_enters_mesh": False,
        "literal_claim_safe": False,
        "current_config": current_config,
        "topologies": {
            "solver_retrieval": {
                "cell_count": len(ALL_CELLS),
                "cell_ids": list(ALL_CELLS),
                "entry_point": "HybridRetrievalService.retrieve",
                "gated_by": "hybrid_retrieval.enabled",
            },
            "agent_routing": {
                "cell_count": agent_registry.cell_count,
                "cell_ids": sorted(agent_registry.cells.keys()),
                "entry_point": "HexNeighborAssist.resolve",
                "gated_by": "hex_mesh.enabled",
            },
        },
        "chat_route_order": route_order,
        "pre_hex_steps": pre_hex_steps,
        "solver_retrieval_samples": solver_assignments,
        "agent_routing_samples": agent_assignments,
        "safe_conclusion": (
            "Current code has two independent hex topologies. Chat requests "
            "do not literally enter a hex mesh first: cache, memory, route "
            "selection, and deterministic solver stages precede hex-backed "
            "retrieval/neighbor assist. The 8-cell retrieval path is enabled "
            "in candidate mode, while the 7-cell hex mesh is disabled by "
            "current settings."
        ),
    }


def _load_yaml_mapping(path: Path) -> dict:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return data if isinstance(data, dict) else {}


def _nested_mapping(data: dict, key: str) -> dict:
    value = data.get(key)
    return value if isinstance(value, dict) else {}


def build_hexagonal_upgrade_proof() -> dict:
    """Run a pure in-memory proof for subdivision + hierarchy + messages."""

    topology = {
        "cells": {
            "thermal": {
                "schema_version": 1,
                "cell_id": "thermal",
                "parent_cell_id": None,
                "child_cell_ids": [],
                "neighbor_cell_ids": ["energy"],
                "live_state": "live",
                "subdivision_state": "leaf",
            },
            "energy": {
                "schema_version": 1,
                "cell_id": "energy",
                "parent_cell_id": None,
                "child_cell_ids": [],
                "neighbor_cell_ids": ["thermal"],
                "live_state": "live",
                "subdivision_state": "leaf",
            },
        }
    }
    original = json.loads(json.dumps(topology, sort_keys=True))
    plan = plan_subdivision(
        parent_cell_id="thermal",
        new_child_cell_ids=("thermal.heating", "thermal.cooling"),
        rationale="Image #1 proof: split one live parent into shadow children.",
        target_state="subdivision_in_shadow",
    )
    shadow_topology = apply_plan_to_topology(topology, plan)

    messages = [
        make_message(
            from_cell_id="thermal",
            to_cell_id="energy",
            kind="ring_request",
            payload={"purpose": "neighbor proof"},
        ),
        make_message(
            from_cell_id="thermal",
            to_cell_id="thermal.heating",
            kind="parent_to_child",
            payload={"purpose": "hierarchy proof"},
        ),
        make_message(
            from_cell_id="thermal.heating",
            to_cell_id="thermal",
            kind="child_to_parent",
            payload={"purpose": "hierarchy proof"},
        ),
    ]
    deliveries = deliver_batch(shadow_topology, messages)
    relations = {
        "thermal_children": children_of(shadow_topology, "thermal"),
        "heating_siblings": siblings_of(shadow_topology, "thermal.heating"),
        "heating_ancestors": ancestors_of(shadow_topology, "thermal.heating"),
    }
    ok = (
        topology == original
        and plan.no_runtime_mutation
        and relations["thermal_children"] == [
            "thermal.cooling",
            "thermal.heating",
        ]
        and relations["heating_siblings"] == ["thermal.cooling"]
        and relations["heating_ancestors"] == ["thermal"]
        and all(item.delivered for item in deliveries)
    )
    return {
        "proof_id": "hexagonal_upgrades_in_memory_v1",
        "ok": ok,
        "no_runtime_mutation": topology == original and plan.no_runtime_mutation,
        "plan": plan.to_dict(),
        "relations": relations,
        "deliveries": [item.to_dict() for item in deliveries],
    }


def build_low_risk_autonomy_proof() -> dict:
    """Run a temp-DB proof for gap -> intent -> scheduler -> solver serve."""

    family_kind = "scalar_unit_conversion"
    cell_coord = "thermal"
    intent_seed = "wd_image1_celsius_to_kelvin"
    seed = _scalar_unit_seed(intent_seed)
    temp_path: Path | None = None
    proof: dict

    with tempfile.TemporaryDirectory(prefix="wd-image1-low-risk-") as temp_dir:
        temp_path = Path(temp_dir)
        cp = ControlPlaneDB(temp_path / "control_plane.sqlite")
        cp.migrate()
        try:
            LowRiskGrower(cp).ensure_low_risk_policies()
            router = RuntimeQueryRouter(cp, min_signal_interval_seconds=0.0)
            query = RuntimeQuery(
                family_kind=family_kind,
                inputs={"x": 25.0},
                cell_coord=cell_coord,
                intent_seed=intent_seed,
                spec_seed=seed,
            )

            before = router.route(query)
            recorded_signals = cp.list_runtime_gap_signals(
                kind="runtime_miss",
                family_kind=family_kind,
                cell_coord=cell_coord,
            )
            candidate = GapSignal(
                kind="runtime_miss",
                family_kind=family_kind,
                cell_coord=cell_coord,
                intent_seed=intent_seed,
                spec_seed=seed,
                weight=1.0,
            )
            digest = digest_signals_into_intents(
                cp,
                candidate_signals=[candidate],
                min_signals_per_intent=1,
                autoenqueue=True,
            )
            queued = cp.list_autogrowth_queue(status="queued", limit=5)

            scheduler = AutogrowthScheduler(
                cp,
                scheduler_id="wd_image1_low_risk_autonomy_proof",
            )
            tick = scheduler.tick()
            after = router.route(query)
            intent = (
                cp.get_growth_intent(tick.intent_id)
                if tick.intent_id is not None else None
            )
            queue_rows = cp.list_autogrowth_queue(limit=5)
            runs = cp.list_autogrowth_runs(family_kind=family_kind, limit=5)
            served_value = (
                after.output
                if isinstance(after.output, (int, float))
                else None
            )
            ok = (
                is_low_risk_family(family_kind)
                and before.served is False
                and before.source == "gap_emitted"
                and len(recorded_signals) == 1
                and digest.intents_created == 1
                and digest.intents_enqueued == 1
                and len(queued) == 1
                and tick.claimed is True
                and tick.outcome == OUTCOME_AUTO_PROMOTED
                and intent is not None
                and intent.status == "fulfilled"
                and after.served is True
                and after.source == "auto_promoted_solver"
                and served_value is not None
                and abs(float(served_value) - 298.15) < 0.000001
                and cp.count_solvers(status="auto_promoted") == 1
            )
            proof = {
                "proof_id": "low_risk_autonomy_temp_db_v1",
                "ok": ok,
                "family_kind": family_kind,
                "family_low_risk": is_low_risk_family(family_kind),
                "cell_coord": cell_coord,
                "external_writes_applied": False,
                "operator_gate_required": False,
                "runtime_authority_changed": False,
                "temporary_control_plane_db": True,
                "route_before": {
                    "served": before.served,
                    "source": before.source,
                    "signal_id_present": before.signal_id is not None,
                    "miss_reason": before.miss_reason,
                },
                "recorded_signal": {
                    "count": len(recorded_signals),
                    "ids": [item.id for item in recorded_signals],
                    "kinds": [item.kind for item in recorded_signals],
                },
                "digest": {
                    "intents_created": digest.intents_created,
                    "intents_enqueued": digest.intents_enqueued,
                    "by_family": dict(digest.by_family),
                },
                "queued_before_tick": [
                    {
                        "id": row.id,
                        "intent_id": row.intent_id,
                        "status": row.status,
                        "priority": row.priority,
                        "attempt_count": row.attempt_count,
                    }
                    for row in queued
                ],
                "scheduler_tick": {
                    "claimed": tick.claimed,
                    "queue_row_id": tick.queue_row_id,
                    "intent_id": tick.intent_id,
                    "family_kind": tick.family_kind,
                    "cell_coord": tick.cell_coord,
                    "outcome": tick.outcome,
                    "solver_id_present": tick.solver_id is not None,
                    "autogrowth_run_id": tick.autogrowth_run_id,
                },
                "intent_after_tick": (
                    {
                        "id": intent.id,
                        "intent_key": intent.intent_key,
                        "status": intent.status,
                        "priority": intent.priority,
                        "signal_count": intent.signal_count,
                    }
                    if intent is not None else None
                ),
                "queue_after_tick": [
                    {
                        "id": row.id,
                        "intent_id": row.intent_id,
                        "status": row.status,
                        "attempt_count": row.attempt_count,
                        "last_error": row.last_error,
                    }
                    for row in queue_rows
                ],
                "route_after": {
                    "served": after.served,
                    "source": after.source,
                    "output": after.output,
                    "solver_id_present": after.solver_id is not None,
                },
                "run_outcomes": [row.outcome for row in runs],
                "growth_event_counts": {
                    "signal_recorded": cp.count_growth_events(
                        event_kind="signal_recorded",
                        family_kind=family_kind,
                    ),
                    "intent_created": cp.count_growth_events(
                        event_kind="intent_created",
                        family_kind=family_kind,
                    ),
                    "intent_enqueued": cp.count_growth_events(
                        event_kind="intent_enqueued",
                        family_kind=family_kind,
                    ),
                    "solver_auto_promoted": cp.count_growth_events(
                        event_kind="solver_auto_promoted",
                        family_kind=family_kind,
                    ),
                },
            }
        finally:
            cp.close()

    temp_db_removed = temp_path is not None and not temp_path.exists()
    proof["temp_db_removed"] = temp_db_removed
    proof["ok"] = bool(proof["ok"] and temp_db_removed)
    return proof


def _scalar_unit_seed(name: str) -> dict:
    return {
        "spec": {
            "from_unit": "C",
            "to_unit": "K",
            "factor": 1.0,
            "offset": 273.15,
        },
        "validation_cases": [
            {"inputs": {"x": 0.0}, "expected": 273.15},
            {"inputs": {"x": 25.0}, "expected": 298.15},
            {"inputs": {"x": 100.0}, "expected": 373.15},
        ],
        "shadow_samples": [{"x": float(i)} for i in range(12)],
        "solver_name_seed": name,
        "cell_id": "thermal",
        "source": "wd_image1_low_risk_autonomy_proof",
        "source_kind": "local_temp_db_proof",
    }


def _capabilities(root: Path) -> tuple[Capability, ...]:
    hex_evidence = _evidence(
        root,
        (
            (
                "waggledance/core/hex_cell_topology.py",
                "8-cell solver-retrieval topology.",
            ),
            (
                "configs/hex_cells.yaml",
                "7-cell agent-routing topology source of truth.",
            ),
            (
                "docs/architecture/HEX_TOPOLOGIES.md",
                "Disambiguates the two independent hex topologies.",
            ),
        ),
    )
    solver_evidence = _evidence(
        root,
        (
            (
                "waggledance/core/reasoning/solver_router.py",
                "Solver-first reasoning route surface.",
            ),
            (
                "waggledance/core/capabilities/selector.py",
                "Capability selection backing the solver route.",
            ),
            (
                "docs/architecture/HONEYCOMB_SOLVER_SCALING.md",
                "Architecture doc records solver-first and current gaps.",
            ),
        ),
    )
    magma_evidence = _evidence(
        root,
        (
            (
                "waggledance/core/magma/event_log_adapter.py",
                "Structured event log adapter.",
            ),
            (
                "waggledance/core/magma/receipt_bundle.py",
                "MAGMA receipt bundle writer/verifier surface.",
            ),
            (
                "docs/architecture/CONTROL_PLANE_AND_DATA_PLANE.md",
                "Storage truth doc records append-only boundary.",
            ),
        ),
    )
    autogrowth_evidence = _evidence(
        root,
        (
            (
                "waggledance/core/autonomy_growth/low_risk_policy.py",
                "Low-risk family allowlist.",
            ),
            (
                "waggledance/core/autonomy_growth/runtime_query_router.py",
                "Runtime gap detector and low-risk dispatch seam.",
            ),
            (
                "waggledance/core/autonomy_growth/autogrowth_scheduler.py",
                "Queue consumer for bounded autogrowth ticks.",
            ),
        ),
    )
    hex_upgrade_evidence = _evidence(
        root,
        (
            (
                "waggledance/core/hex_topology/subdivision_operator.py",
                "Pure shadow-first subdivision planner.",
            ),
            (
                "waggledance/core/hex_topology/ring_messaging.py",
                "Pure deterministic ring delivery primitive.",
            ),
            (
                "waggledance/core/hex_topology/parent_child_relations.py",
                "Pure parent/child/sibling/neighbor relation helpers.",
            ),
        ),
    )
    future_evidence = _evidence(
        root,
        (
            (
                "docs/architecture/explosive_intelligence_growth_2.md",
                "Future scale architecture and risk register.",
            ),
            (
                "docs/architecture/HONEYCOMB_SOLVER_SCALING.md",
                "Scoreboard and non-goals for capability growth.",
            ),
        ),
    )
    hex_upgrade_proof = build_hexagonal_upgrade_proof()
    low_risk_autonomy_proof = build_low_risk_autonomy_proof()
    hex_entry_proof = build_hex_mesh_entry_proof(root)

    return (
        Capability(
            capability_id="hex_mesh_entry",
            title="Hex-mesh query entry",
            image_claim=(
                "Every query first enters an intelligent 8-cell honeycomb "
                "topology."
            ),
            safe_statement=(
                "WD has two independent topologies: an 8-cell "
                "solver-retrieval topology and a 7-cell agent-routing "
                "topology; exact runtime entry order depends on flags and "
                "call path."
            ),
            status=_status_for(hex_evidence),
            claim_safe=False,
            evidence=hex_evidence,
            gaps=(
                "The repo explicitly documents two different hex topologies.",
                "The 7-cell hex_mesh path can be disabled by runtime config.",
                "The route-order proof shows cache, memory, route selection, "
                "and deterministic solver stages before hex-backed stages.",
            ),
            next_smallest_pr=(
                "Add a runtime-facing trace smoke that compares this static "
                "route-order proof against one live ChatService request."
            ),
            proof=hex_entry_proof,
        ),
        Capability(
            capability_id="deterministic_solver_first",
            title="Deterministic solver-first routing",
            image_claim=(
                "Queries are routed first through authoritative solvers, then "
                "specialist models, with LLM only advisory."
            ),
            safe_statement=(
                "Solver-first routing surfaces exist; full per-solver-call "
                "MAGMA trace coverage is still the next proof boundary."
            ),
            status=_status_for(solver_evidence),
            claim_safe=False,
            evidence=solver_evidence,
            gaps=(
                "Architecture docs record MAGMA coverage as goal/action/"
                "capability-level rather than per-solver-call.",
                "The image's 'full MAGMA provenance' wording should wait for "
                "trace-completeness evidence.",
            ),
            next_smallest_pr=(
                "Thread a per-solver-call trace event through SolverRouter "
                "and add a focused route test."
            ),
        ),
        Capability(
            capability_id="magma_audit_log",
            title="MAGMA audit log",
            image_claim="MAGMA is an append-only provenance trail.",
            safe_statement=(
                "MAGMA audit/provenance wrappers and receipt bundles exist; "
                "hard append-only enforcement is not yet safe to claim."
            ),
            status=_status_for(magma_evidence),
            claim_safe=False,
            evidence=magma_evidence,
            gaps=(
                "The storage truth doc says append-only is convention for "
                "some MAGMA-backed paths.",
                "A hardening pass is needed before literal append-only "
                "language is safe.",
            ),
            next_smallest_pr=(
                "Add append-only enforcement or a failing proof that clearly "
                "keeps wording at audit-wrapper level."
            ),
        ),
        Capability(
            capability_id="low_risk_autonomy_loop",
            title="Low-risk autonomy loop",
            image_claim=(
                "The system autonomously grows new low-risk solvers through "
                "gap mining without human intervention."
            ),
            safe_statement=(
                "A bounded low-risk autogrowth substrate exists with an "
                "allowlist, runtime gap seam, scheduler ticks, and proof "
                "fixtures; unrestricted runtime authority is not claimed."
            ),
            status=_status_for(autogrowth_evidence),
            claim_safe=False,
            evidence=autogrowth_evidence,
            gaps=(
                "AutogrowthScheduler is caller-driven and explicitly "
                "bounded.",
                "The low-risk allowlist is fixed; adding families requires "
                "reviewed deterministic compiler and executor support.",
                "The executable proof uses an ephemeral temp DB; it does not "
                "grant production runtime authority.",
            ),
            next_smallest_pr=(
                "Wire the temp-DB proof into a runtime-facing smoke that "
                "reports the active scheduler cadence and authority boundary."
            ),
            proof=low_risk_autonomy_proof,
        ),
        Capability(
            capability_id="hexagonal_upgrades",
            title="Hexagonal upgrades",
            image_claim=(
                "Full hexagonal upgrades implement dynamic subdivision, ring "
                "messaging, and parent-child hierarchy."
            ),
            safe_statement=(
                "Pure primitives for subdivision planning, ring messaging, "
                "and parent-child relation queries exist; runtime mutation is "
                "intentionally gated."
            ),
            status=_status_for(hex_upgrade_evidence),
            claim_safe=False,
            evidence=hex_upgrade_evidence,
            gaps=(
                "Subdivision is shadow-first and does not mutate runtime "
                "topology.",
                "Ring delivery is pure validation, not a networked runtime "
                "delivery layer.",
            ),
            next_smallest_pr=(
                "Wire the pure proof into a read-only runtime-facing smoke "
                "that reports current config and active topology boundaries."
            ),
            proof=hex_upgrade_proof,
        ),
        Capability(
            capability_id="future_waggledance_swarm",
            title="Future WaggleDance swarm",
            image_claim=(
                "The future swarm has emergent intelligence, infinite "
                "scalability, and industrial-grade efficiency."
            ),
            safe_statement=(
                "The repo has future scale architecture and measurable axes; "
                "unlimited scalability remains a target, not a fact."
            ),
            status=(
                STATUS_PLANNED
                if _some_present(future_evidence)
                else STATUS_BLOCKED
            ),
            claim_safe=False,
            evidence=future_evidence,
            gaps=(
                "No finite software system can honestly prove infinite "
                "scalability.",
                "Future claims must be tied to measured axes such as "
                "coverage, fallback rate, latency, and audit completeness.",
            ),
            next_smallest_pr=(
                "Create a scale-axis scorecard artifact and link each future "
                "claim to a measurable proof."
            ),
        ),
    )


def build_manifest(root: Path | str = ROOT) -> dict:
    """Build the read-only Image #1 capability manifest."""

    repo_root = Path(root)
    capabilities = _capabilities(repo_root)
    counts = {status: 0 for status in sorted(VALID_STATUSES)}
    unsafe_claim_ids: list[str] = []
    for capability in capabilities:
        counts[capability.status] += 1
        if not capability.claim_safe:
            unsafe_claim_ids.append(capability.capability_id)

    return {
        "schema_version": "wd_image1_capability_manifest.v1",
        "claim_policy": (
            "Literal image claims are unsafe unless claim_safe=true; use "
            "safe_statement for docs and user-facing copy."
        ),
        "capabilities": [capability.to_dict() for capability in capabilities],
        "summary": {
            "capability_count": len(capabilities),
            "status_counts": counts,
            "unsafe_literal_claim_ids": unsafe_claim_ids,
            "all_literal_claims_safe": len(unsafe_claim_ids) == 0,
            "proofs_ok": all(
                capability.proof is None or capability.proof.get("ok") is True
                for capability in capabilities
            ),
        },
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Emit the read-only WD Image #1 capability manifest."
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=ROOT,
        help="Repository root to inspect. Defaults to this checkout.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON. Present for explicitness; JSON is the only output.",
    )
    parser.add_argument(
        "--strict-claims",
        action="store_true",
        help="Exit 2 when any literal image claim is not safe to repeat.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    manifest = build_manifest(args.root)
    print(json.dumps(manifest, indent=2, sort_keys=True))
    if args.strict_claims and not manifest["summary"]["all_literal_claims_safe"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

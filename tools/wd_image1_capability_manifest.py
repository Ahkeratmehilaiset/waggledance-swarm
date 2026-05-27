# SPDX-License-Identifier: BUSL-1.1
"""Repo/runtime-read-only capability manifest for the WD Image #1 storyboard.

The tool deliberately separates the visual claim from repo-safe wording.
It does not mutate runtime state, bridge state, GitHub state, or tracked files.
Local executable proofs may create ephemeral temp files and delete them before
returning.
"""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import gc
import json
import os
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
    proof = {
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
    runtime_trace_smoke = _build_hex_mesh_runtime_trace_smoke_from_static(proof)
    proof["runtime_trace_smoke"] = runtime_trace_smoke
    proof["ok"] = bool(proof["ok"] and runtime_trace_smoke["ok"])
    return proof


def _load_yaml_mapping(path: Path) -> dict:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return data if isinstance(data, dict) else {}


def _nested_mapping(data: dict, key: str) -> dict:
    value = data.get(key)
    return value if isinstance(value, dict) else {}


def build_hex_mesh_runtime_trace_smoke(root: Path | str = ROOT) -> dict:
    """Run one read-only ChatService request and compare route-stage order."""

    static_proof = build_hex_mesh_entry_proof(root)
    existing = static_proof.get("runtime_trace_smoke")
    if isinstance(existing, dict):
        return existing
    return _build_hex_mesh_runtime_trace_smoke_from_static(static_proof)


def _build_hex_mesh_runtime_trace_smoke_from_static(static_proof: dict) -> dict:
    proof_id = "hex_mesh_runtime_trace_smoke_v1"
    if static_proof.get("ok") is not True:
        return {
            "proof_id": proof_id,
            "ok": False,
            "blocked_reason": "static_hex_mesh_entry_proof_not_ok",
            "static_proof_ok": static_proof.get("ok") is True,
            "trace_source": "ChatResult.route_stage_trace",
            "test_only_instrumentation": False,
            "no_runtime_mutation": True,
            "external_writes_applied": False,
        }

    current_config = static_proof.get("current_config") or {}
    static_route_order = list(static_proof.get("chat_route_order") or [])
    disabled_static_stages = []
    if not current_config.get("hybrid_retrieval_enabled", False):
        disabled_static_stages.append("hybrid_retrieval_8_cell")
    if not current_config.get("hex_mesh_enabled", False):
        disabled_static_stages.append("hex_neighbor_assist_7_cell")
    expected_live_order = [
        stage
        for stage in static_route_order
        if stage not in disabled_static_stages
    ]

    class _TraceHotCache:
        def get(self, key: str) -> None:
            return None

        def set(self, key: str, value: str, ttl: int) -> None:
            return None

    class _TraceMemoryService:
        async def retrieve_context(
            self,
            *,
            query: str,
            language: str,
            limit: int,
        ) -> list:
            return []

    class _TraceConfig:
        def get(self, key: str, default: object = None) -> object:
            return {
                "advanced_learning.micro_model_enabled": False,
                "swarm.enabled": False,
            }.get(key, default)

    class _TraceHybridRetrieval:
        enabled = bool(current_config.get("hybrid_retrieval_enabled", False))
        is_authoritative = bool(
            current_config.get("hybrid_retrieval_authoritative", False)
        )

        async def retrieve(self, query: str, intent: str, k: int) -> object:
            from waggledance.application.services.hybrid_retrieval_service import (
                HybridTraceResult,
            )

            mode = str(current_config.get("hybrid_retrieval_mode") or "shadow")
            return HybridTraceResult(
                retrieval_mode=f"hybrid:{mode}",
                route_source="cell:math+global",
                answered_by_layer="llm",
                cell_id="math",
                llm_fallback=True,
            )

    class _TraceHexNeighborAssist:
        enabled = bool(current_config.get("hex_mesh_enabled", False))

        async def resolve(
            self,
            *,
            query: str,
            intent: str,
            context: dict,
        ) -> dict:
            return {
                "confidence": 0.0,
                "trace": {"cell_count": 7, "answered": False},
            }

    class _TraceOrchestrator:
        async def handle_task(self, task: object, route: object) -> object:
            from waggledance.core.domain.agent import AgentResult

            return AgentResult(
                agent_id="wd_image1_runtime_smoke",
                response="runtime smoke answer",
                confidence=0.8,
                latency_ms=1.0,
                source="llm",
            )

        async def run_round_table(self, task: object) -> object:
            raise AssertionError("runtime smoke should not need round-table")

    class _TraceTelemetry:
        def record(
            self,
            route_type: str,
            latency_ms: float,
            success: bool,
        ) -> None:
            return None

    class _TraceHybridObserver:
        async def record_candidate(self, **kwargs: object) -> None:
            return None

    async def _run_request() -> object:
        import waggledance.application.services.chat_service as chat_mod
        from waggledance.application.dto.chat_dto import ChatRequest
        from waggledance.application.services.chat_service import ChatService

        service = ChatService(
            orchestrator=_TraceOrchestrator(),
            memory_service=_TraceMemoryService(),
            hot_cache=_TraceHotCache(),
            routing_policy_fn=chat_mod.select_route,
            config=_TraceConfig(),
            hybrid_retrieval=(
                _TraceHybridRetrieval()
                if current_config.get("hybrid_retrieval_enabled", False)
                else None
            ),
            hex_neighbor_assist=(
                _TraceHexNeighborAssist()
                if current_config.get("hex_mesh_enabled", False)
                else None
            ),
        )
        service._telemetry = _TraceTelemetry()
        service._hybrid_observer = _TraceHybridObserver()
        result = await service.handle(
            ChatRequest(
                query="statistics summary for hive sensor readings",
                language="auto",
                profile="HOME",
            )
        )
        await asyncio.sleep(0)
        return result

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        result = asyncio.run(_run_request())
    else:
        return {
            "proof_id": proof_id,
            "ok": False,
            "blocked_reason": "active_asyncio_loop",
            "static_route_order": static_route_order,
            "expected_live_route_order": expected_live_order,
            "disabled_static_stages": disabled_static_stages,
            "trace_source": "ChatResult.route_stage_trace",
            "test_only_instrumentation": False,
            "no_runtime_mutation": True,
            "external_writes_applied": False,
        }
    observed_events = list(getattr(result, "route_stage_trace", None) or [])
    observed_route_order = [
        event["stage"]
        for event in observed_events
        if event["stage"] in static_route_order
    ]
    extra_observed_stages = [
        event["stage"]
        for event in observed_events
        if event["stage"] not in static_route_order
    ]
    pre_hex_stages = list(static_proof.get("pre_hex_steps") or [])
    pre_hex_observed = [
        stage for stage in observed_route_order if stage in pre_hex_stages
    ]
    ok = (
        observed_route_order == expected_live_order
        and pre_hex_observed == pre_hex_stages
        and getattr(result, "source", None) == "llm"
        and getattr(result, "cached", None) is False
    )
    return {
        "proof_id": proof_id,
        "ok": ok,
        "query": "statistics summary for hive sensor readings",
        "static_route_order": static_route_order,
        "expected_live_route_order": expected_live_order,
        "observed_route_order": observed_route_order,
        "trace_source": "ChatResult.route_stage_trace",
        "test_only_instrumentation": False,
        "disabled_static_stages": disabled_static_stages,
        "extra_observed_stages": extra_observed_stages,
        "pre_hex_stages_observed_before_optional_hex": (
            pre_hex_observed == pre_hex_stages
        ),
        "live_result": {
            "source": getattr(result, "source", None),
            "confidence": getattr(result, "confidence", None),
            "cached": getattr(result, "cached", None),
            "hybrid_trace_present": getattr(result, "hybrid_trace", None)
            is not None,
            "round_table": getattr(result, "round_table", None),
        },
        "current_config": current_config,
        "no_runtime_mutation": True,
        "external_writes_applied": False,
        "events": observed_events,
    }


def _blocked_deterministic_solver_trace_proof(
    *,
    missing_inputs: Sequence[str],
    blocked_reason: str = "missing_required_inputs",
    inspected_root: str | None = None,
    import_root: str | None = None,
) -> dict:
    safe_conclusion = (
        "Required solver-routing files are missing, so no deterministic "
        "solver trace proof is available for this root."
    )
    if blocked_reason == "non_current_import_root":
        safe_conclusion = (
            "The inspected root is not the manifest tool's current import "
            "root, so the proof blocks instead of certifying one checkout "
            "with SolverRouter imported from another checkout."
        )
    proof = {
        "proof_id": "deterministic_solver_trace_v1",
        "ok": False,
        "blocked_reason": blocked_reason,
        "missing_inputs": list(missing_inputs),
        "router_entrypoint": (
            "waggledance.core.reasoning.solver_router.SolverRouter.route"
        ),
        "selected_solver_ids": [],
        "trace": [],
        "query_text_recorded": False,
        "magma_execution_receipt_claimed": False,
        "safe_conclusion": safe_conclusion,
    }
    if inspected_root is not None:
        proof["inspected_root"] = inspected_root
    if import_root is not None:
        proof["import_root"] = import_root
    return proof


def _blocked_solver_trace_magma_receipt_proof(
    *,
    missing_inputs: Sequence[str],
    blocked_reason: str = "missing_required_inputs",
    inspected_root: str | None = None,
    import_root: str | None = None,
) -> dict:
    safe_conclusion = (
        "Required runtime receipt files are missing, so no solver-trace "
        "MAGMA receipt proof is available for this root."
    )
    if blocked_reason == "non_current_import_root":
        safe_conclusion = (
            "The inspected root is not the manifest tool's current import "
            "root, so the receipt proof blocks instead of certifying one "
            "checkout with runtime code imported from another checkout."
        )
    proof = {
        "proof_id": "solver_trace_runtime_receipt_v1",
        "ok": False,
        "blocked_reason": blocked_reason,
        "missing_inputs": list(missing_inputs),
        "receipt_scope": "opt_in_handle_query_runtime_summary",
        "solver_call_trace_receipt_bound": False,
        "solver_call_trace_privacy_safe": False,
        "default_sink_required": False,
        "external_writes_applied": False,
        "operator_gate_required": False,
        "safe_conclusion": safe_conclusion,
    }
    if inspected_root is not None:
        proof["inspected_root"] = inspected_root
    if import_root is not None:
        proof["import_root"] = import_root
    return proof


def build_solver_trace_magma_receipt_proof(root: Path | str = ROOT) -> dict:
    """Prove an opt-in MAGMA runtime receipt binds the solver call trace."""

    repo_root = Path(root)
    required = (
        "waggledance/core/autonomy/runtime.py",
        "waggledance/core/magma/runtime_summary_receipt.py",
        "tools/run_runtime_receipt_emission_proof.py",
        "tools/verify_magma_receipt.py",
    )
    missing = [
        rel_path
        for rel_path in required
        if not (repo_root / rel_path).exists()
    ]
    if missing:
        return _blocked_solver_trace_magma_receipt_proof(
            missing_inputs=missing,
        )

    resolved_repo_root = repo_root.resolve()
    resolved_import_root = ROOT.resolve()
    if resolved_repo_root != resolved_import_root:
        return _blocked_solver_trace_magma_receipt_proof(
            missing_inputs=[],
            blocked_reason="non_current_import_root",
            inspected_root=str(resolved_repo_root),
            import_root=str(resolved_import_root),
        )

    from tools.run_runtime_receipt_emission_proof import (
        build_runtime_receipt_emission_proof,
    )

    temp_root = None
    report: dict
    with tempfile.TemporaryDirectory(prefix="wd-image1-solver-receipt-") as tmp:
        temp_root = Path(tmp)
        report = build_runtime_receipt_emission_proof(
            out_dir=temp_root / "proof",
            now_utc=datetime(2026, 5, 27, 18, 0, tzinfo=timezone.utc),
        )
    temp_artifacts_removed = temp_root is not None and not temp_root.exists()
    blockers = list(report.get("blockers") or [])
    if not temp_artifacts_removed:
        blockers.append("temp_artifacts_not_removed")
    ok = (
        report.get("ok") is True
        and report.get("verifier_ok") is True
        and report.get("solver_call_trace_count") == 1
        and report.get("solver_call_trace_digest_bound") is True
        and report.get("solver_call_trace_receipt_bound") is True
        and report.get("solver_call_trace_privacy_safe") is True
        and report.get("raw_payload_leak_check") is True
        and temp_artifacts_removed
    )
    return {
        "proof_id": "solver_trace_runtime_receipt_v1",
        "ok": ok,
        "blockers": blockers,
        "receipt_scope": "opt_in_handle_query_runtime_summary",
        "chain_id": report.get("chain_id"),
        "receipt_count": report.get("receipt_count"),
        "verifier_ok": report.get("verifier_ok"),
        "evaluation_version": report.get("evaluation_version"),
        "reason_codes": report.get("reason_codes", []),
        "solver_selection": report.get("solver_selection", []),
        "solver_call_trace_count": report.get("solver_call_trace_count"),
        "solver_call_trace_digest_bound": report.get(
            "solver_call_trace_digest_bound"
        ),
        "solver_call_trace_receipt_bound": report.get(
            "solver_call_trace_receipt_bound"
        ),
        "solver_call_trace_privacy_safe": report.get(
            "solver_call_trace_privacy_safe"
        ),
        "raw_payload_leak_check": report.get("raw_payload_leak_check"),
        "external_writes_applied": False,
        "local_artifacts_written": True,
        "temp_artifacts_removed": temp_artifacts_removed,
        "default_sink_required": report.get("default_sink_required"),
        "operator_gate_required": report.get("operator_gate_required"),
        "safe_conclusion": (
            "An opt-in AutonomyRuntime.handle_query MAGMA runtime summary "
            "receipt can bind the sanitized solver_call_trace and verifies "
            "offline without recording raw query or context text. This does "
            "not yet make receipt emission default for every runtime path."
        ),
    }


def build_deterministic_solver_trace_proof(root: Path | str = ROOT) -> dict:
    """Prove SolverRouter emits a privacy-safe selected-solver trace."""

    repo_root = Path(root)
    required = (
        "waggledance/core/reasoning/solver_router.py",
        "waggledance/core/capabilities/selector.py",
        "waggledance/core/capabilities/registry.py",
    )
    missing = [
        rel_path
        for rel_path in required
        if not (repo_root / rel_path).exists()
    ]
    if missing:
        return _blocked_deterministic_solver_trace_proof(
            missing_inputs=missing,
        )

    resolved_repo_root = repo_root.resolve()
    resolved_import_root = ROOT.resolve()
    if resolved_repo_root != resolved_import_root:
        return _blocked_deterministic_solver_trace_proof(
            missing_inputs=[],
            blocked_reason="non_current_import_root",
            inspected_root=str(resolved_repo_root),
            import_root=str(resolved_import_root),
        )

    from waggledance.core.reasoning.solver_router import SolverRouter

    sample_query = "calculate 2 + 2"
    result = SolverRouter().route("math", sample_query)
    trace = list(result.solver_call_trace)
    trace_json = json.dumps(trace, sort_keys=True)
    selected_solver_ids = [
        str(item.get("capability_id"))
        for item in trace
        if item.get("stage") == "solver_call"
    ]
    query_text_recorded = (
        sample_query in trace_json
        or '"query"' in trace_json
    )
    receipt_proof = build_solver_trace_magma_receipt_proof(root)
    ok = (
        result.quality_path == "gold"
        and result.selection.fallback_used is False
        and selected_solver_ids == ["solve.math"]
        and bool(trace)
        and not query_text_recorded
        and all(
            item.get("execution_boundary") == "safe_action_bus"
            for item in trace
        )
        and receipt_proof.get("ok") is True
    )
    return {
        "proof_id": "deterministic_solver_trace_v1",
        "ok": ok,
        "router_entrypoint": (
            "waggledance.core.reasoning.solver_router.SolverRouter.route"
        ),
        "quality_path": result.quality_path,
        "fallback_used": result.selection.fallback_used,
        "selected_solver_ids": selected_solver_ids,
        "trace": trace,
        "query_text_recorded": query_text_recorded,
        "magma_execution_receipt_claimed": receipt_proof.get("ok") is True,
        "magma_execution_receipt_scope": receipt_proof.get("receipt_scope"),
        "magma_execution_receipt_proof": receipt_proof,
        "receipt_metrics": {
            "receipt_count": receipt_proof.get("receipt_count"),
            "solver_call_trace_count": receipt_proof.get(
                "solver_call_trace_count"
            ),
            "solver_call_trace_receipt_bound": receipt_proof.get(
                "solver_call_trace_receipt_bound"
            ),
        },
        "external_writes_applied": False,
        "safe_conclusion": (
            "SolverRouter now emits a privacy-safe selected-solver trace "
            "before SafeActionBus execution, and an opt-in MAGMA runtime "
            "summary receipt can bind that trace. Default receipt emission "
            "for every runtime path remains a separate proof boundary."
        ),
    }


def _blocked_hexagonal_upgrade_proof(
    *,
    missing_inputs: Sequence[str],
    blocked_reason: str = "missing_required_inputs",
    inspected_root: str | None = None,
    import_root: str | None = None,
) -> dict:
    proof = {
        "proof_id": "hexagonal_upgrades_in_memory_v1",
        "ok": False,
        "blocked_reason": blocked_reason,
        "missing_inputs": list(missing_inputs),
        "no_runtime_mutation": False,
        "plan": None,
        "relations": {},
        "deliveries": [],
        "safe_conclusion": (
            "Required hexagonal upgrade proof files are missing, so the "
            "in-memory subdivision/ring proof is unavailable for this root."
        ),
    }
    if blocked_reason == "non_current_import_root":
        proof["safe_conclusion"] = (
            "The inspected root is not the manifest tool's current import "
            "root, so the proof blocks instead of certifying one checkout "
            "with runtime code imported from another checkout."
        )
    if inspected_root is not None:
        proof["inspected_root"] = inspected_root
    if import_root is not None:
        proof["import_root"] = import_root
    return proof


def build_hexagonal_upgrade_proof(root: Path | str = ROOT) -> dict:
    """Run a pure in-memory proof for subdivision + hierarchy + messages."""

    repo_root = Path(root)
    required = (
        "waggledance/core/hex_topology/subdivision_operator.py",
        "waggledance/core/hex_topology/ring_messaging.py",
        "waggledance/core/hex_topology/parent_child_relations.py",
    )
    missing = [
        rel_path
        for rel_path in required
        if not (repo_root / rel_path).exists()
    ]
    if missing:
        return _blocked_hexagonal_upgrade_proof(
            missing_inputs=missing,
        )

    resolved_repo_root = repo_root.resolve()
    resolved_import_root = ROOT.resolve()
    if resolved_repo_root != resolved_import_root:
        return _blocked_hexagonal_upgrade_proof(
            missing_inputs=[],
            blocked_reason="non_current_import_root",
            inspected_root=str(resolved_repo_root),
            import_root=str(resolved_import_root),
        )

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


def _blocked_hexagonal_runtime_smoke(
    *,
    missing_inputs: Sequence[str],
    blocked_reason: str = "missing_required_inputs",
    inspected_root: str | None = None,
    import_root: str | None = None,
) -> dict:
    proof = {
        "proof_id": "hexagonal_upgrades_runtime_boundary_smoke_v1",
        "ok": False,
        "blocked_reason": blocked_reason,
        "missing_inputs": list(missing_inputs),
        "runtime_wiring_present": False,
        "container_registry_present": False,
        "container_hex_neighbor_assist_wiring_present": False,
        "active_runtime_dispatch_enabled": False,
        "no_runtime_topology_mutation": True,
        "runtime_authority_changed": False,
        "operator_gate_required": False,
        "external_writes_applied": False,
        "safe_conclusion": (
            "Required runtime topology files are missing, so no "
            "hexagonal-upgrades runtime boundary smoke is available for "
            "this root."
        ),
    }
    if blocked_reason == "non_current_import_root":
        proof["safe_conclusion"] = (
            "The inspected root is not the manifest tool's current import "
            "root, so the proof blocks instead of certifying one checkout "
            "with runtime code imported from another checkout."
        )
    if inspected_root is not None:
        proof["inspected_root"] = inspected_root
    if import_root is not None:
        proof["import_root"] = import_root
    return proof


class _HexRuntimeSmokeSettings:
    db_path = "shared_memory.db"

    def __init__(self, *, hex_mesh_enabled: bool, cell_config_path: str):
        self._hex_mesh_enabled = hex_mesh_enabled
        self._cell_config_path = cell_config_path

    def get_profile(self) -> str:
        return "HOME"

    def get(self, key: str, default=None):
        if key == "hex_mesh.enabled":
            return self._hex_mesh_enabled
        if key == "hex_mesh.cell_config_path":
            return self._cell_config_path
        return default


def build_hexagonal_upgrade_runtime_smoke(
    root: Path | str = ROOT,
) -> dict:
    """Report active runtime topology boundaries without mutating topology."""

    repo_root = Path(root)
    settings_path = repo_root / "configs" / "settings.yaml"
    if not settings_path.exists():
        return _blocked_hexagonal_runtime_smoke(
            missing_inputs=("configs/settings.yaml",),
        )

    settings = _load_yaml_mapping(settings_path)
    hex_mesh_cfg = _nested_mapping(settings, "hex_mesh")
    cell_config_path = str(
        hex_mesh_cfg.get("cell_config_path") or "configs/hex_cells.yaml"
    )
    cell_config_file = Path(cell_config_path)
    if not cell_config_file.is_absolute():
        cell_config_file = repo_root / cell_config_file

    required = (
        "waggledance/bootstrap/container.py",
        "waggledance/application/services/hex_topology_registry.py",
        "waggledance/application/services/hex_neighbor_assist.py",
        "waggledance/core/hex_topology/subdivision_operator.py",
        "waggledance/core/hex_topology/ring_messaging.py",
        "waggledance/core/hex_topology/parent_child_relations.py",
        cell_config_path,
    )
    missing = [
        rel_path
        for rel_path in required
        if not (
            (Path(rel_path).is_absolute() and Path(rel_path).exists())
            or (repo_root / rel_path).exists()
        )
    ]
    if missing:
        return _blocked_hexagonal_runtime_smoke(
            missing_inputs=missing,
        )

    resolved_repo_root = repo_root.resolve()
    resolved_import_root = ROOT.resolve()
    if resolved_repo_root != resolved_import_root:
        return _blocked_hexagonal_runtime_smoke(
            missing_inputs=[],
            blocked_reason="non_current_import_root",
            inspected_root=str(resolved_repo_root),
            import_root=str(resolved_import_root),
        )

    from waggledance.bootstrap.container import Container

    container_text = (repo_root / "waggledance/bootstrap/container.py").read_text(
        encoding="utf-8"
    )
    assist_text = (
        repo_root / "waggledance/application/services/hex_neighbor_assist.py"
    ).read_text(encoding="utf-8")
    assist_wiring_present = (
        "def hex_neighbor_assist" in container_text
        and "HexNeighborAssist(" in container_text
        and "hex_mesh.enabled" in container_text
        and "enabled: bool = False" in assist_text
        and "self.enabled = enabled" in assist_text
    )

    hex_mesh_enabled = bool(hex_mesh_cfg.get("enabled", False))
    container_registry_present = False
    registry_stats: dict = {}
    cell_ids: list[str] = []
    enabled_cell_ids: list[str] = []
    neighbor_map: dict[str, list[str]] = {}
    sample_origins: list[dict] = []
    old_cwd = Path.cwd()
    try:
        os.chdir(repo_root)
        container = Container(
            settings=_HexRuntimeSmokeSettings(
                hex_mesh_enabled=hex_mesh_enabled,
                cell_config_path=cell_config_path,
            ),
            stub=True,
        )
        registry = container.hex_topology_registry
        registry_stats = registry.stats()
        cells = registry.cells
        cell_ids = list(cells.keys())
        enabled_cell_ids = [
            cell_id for cell_id, cell in cells.items() if cell.enabled
        ]
        neighbor_map = {
            cell_id: [
                neighbor.id for neighbor in registry.get_neighbor_cells(cell_id)
            ]
            for cell_id in cell_ids
        }
        for sample in (
            {
                "query": "bee hive swarm monitoring",
                "intent": "bee",
                "expected_cell": "bee_ops",
            },
            {
                "query": "energy hvac lighting",
                "intent": "energy",
                "expected_cell": "home_comfort",
            },
            {
                "query": "safety fire alarm",
                "intent": "safety",
                "expected_cell": "safety_security",
            },
        ):
            selected = registry.select_origin_cell(
                str(sample["query"]),
                str(sample["intent"]),
            )
            sample_origins.append({
                "query": sample["query"],
                "intent": sample["intent"],
                "expected_cell": sample["expected_cell"],
                "cell_id": selected,
                "matched_expected": selected == sample["expected_cell"],
            })
        container_registry_present = registry.cell_count > 0
    finally:
        os.chdir(old_cwd)

    shadow_child_ids = ("thermal.heating", "thermal.cooling")
    shadow_children_absent = all(
        child_id not in cell_ids for child_id in shadow_child_ids
    )
    ok = (
        container_registry_present
        and assist_wiring_present
        and registry_stats.get("cells_loaded") == 7
        and len(enabled_cell_ids) == 7
        and all(item["matched_expected"] for item in sample_origins)
        and shadow_children_absent
    )
    return {
        "proof_id": "hexagonal_upgrades_runtime_boundary_smoke_v1",
        "ok": ok,
        "runtime_wiring_present": (
            container_registry_present and assist_wiring_present
        ),
        "container_registry_present": container_registry_present,
        "container_hex_neighbor_assist_wiring_present": assist_wiring_present,
        "current_config": {
            "hex_mesh_enabled": hex_mesh_enabled,
            "hex_mesh_cell_config_path": cell_config_path,
        },
        "active_runtime_dispatch_enabled": hex_mesh_enabled,
        "runtime_topology": {
            "cell_count": registry_stats.get("cells_loaded"),
            "enabled_cell_count": len(enabled_cell_ids),
            "cell_ids": cell_ids,
            "enabled_cell_ids": enabled_cell_ids,
            "neighbor_map": neighbor_map,
            "sample_origins": sample_origins,
            "stats": registry_stats,
        },
        "shadow_child_cell_ids_absent_from_runtime_config": (
            shadow_children_absent
        ),
        "shadow_child_cell_ids": list(shadow_child_ids),
        "no_runtime_topology_mutation": shadow_children_absent,
        "runtime_authority_changed": False,
        "operator_gate_required": False,
        "external_writes_applied": False,
        "safe_conclusion": (
            "Runtime-facing hex topology wiring can load the current "
            "7-cell agent-routing registry through Container and report "
            "the active hex_mesh dispatch gate. The subdivision/ring proof "
            "remains shadow-only: its child cells are not inserted into the "
            "runtime config, and no runtime topology authority is changed."
        ),
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


def _blocked_low_risk_runtime_boundary_smoke(
    *,
    missing_inputs: Sequence[str],
    blocked_reason: str = "missing_required_inputs",
    inspected_root: str | None = None,
    import_root: str | None = None,
) -> dict:
    proof = {
        "proof_id": "low_risk_autogrowth_runtime_boundary_smoke_v1",
        "ok": False,
        "blocked_reason": blocked_reason,
        "missing_inputs": list(missing_inputs),
        "runtime_wiring_present": False,
        "container_ticker_present": False,
        "lifespan_start_stop_present": False,
        "runtime_authority_changed": False,
        "production_control_plane_mutated": False,
        "operator_gate_required": False,
        "external_writes_applied": False,
        "safe_conclusion": (
            "Required runtime wiring files are missing, so no low-risk "
            "autogrowth runtime boundary smoke is available for this root."
        ),
    }
    if blocked_reason == "non_current_import_root":
        proof["safe_conclusion"] = (
            "The inspected root is not the manifest tool's current import "
            "root, so the proof blocks instead of certifying one checkout "
            "with runtime code imported from another checkout."
        )
    if inspected_root is not None:
        proof["inspected_root"] = inspected_root
    if import_root is not None:
        proof["import_root"] = import_root
    return proof


class _RuntimeBoundarySmokeSettings:
    db_path = "shared_memory.db"

    def get_profile(self) -> str:
        return "HOME"

    def get(self, key: str, default=None):
        return default


def build_low_risk_autogrowth_runtime_boundary_smoke(
    root: Path | str = ROOT,
) -> dict:
    """Report runtime wiring and cadence without changing runtime authority."""

    repo_root = Path(root)
    required = (
        "waggledance/core/autonomy_growth/autogrowth_scheduler.py",
        "waggledance/bootstrap/container.py",
        "waggledance/adapters/http/api.py",
        "tests/integration/test_runtime_autogrowth_lifespan.py",
    )
    missing = [
        rel_path
        for rel_path in required
        if not (repo_root / rel_path).exists()
    ]
    if missing:
        return _blocked_low_risk_runtime_boundary_smoke(
            missing_inputs=missing,
        )

    resolved_repo_root = repo_root.resolve()
    resolved_import_root = ROOT.resolve()
    if resolved_repo_root != resolved_import_root:
        return _blocked_low_risk_runtime_boundary_smoke(
            missing_inputs=[],
            blocked_reason="non_current_import_root",
            inspected_root=str(resolved_repo_root),
            import_root=str(resolved_import_root),
        )

    from waggledance.bootstrap.container import Container

    api_text = (repo_root / "waggledance/adapters/http/api.py").read_text(
        encoding="utf-8"
    )
    lifespan_start_stop_present = (
        "autogrowth_background_ticker" in api_text
        and "await autogrowth_ticker.start()" in api_text
        and "await autogrowth_ticker.stop()" in api_text
    )

    temp_root: Path | None = None
    old_cwd = Path.cwd()
    ticker_present = False
    ticker_is_running: bool | None = None
    control_plane_path: str | None = None
    schema_version: int | None = None
    interval_seconds: float | None = None
    max_ticks_per_wake: int | None = None
    container = None
    cp_db = None
    try:
        with tempfile.TemporaryDirectory(
            prefix="wd-image1-autogrowth-runtime-"
        ) as tmp:
            temp_root = Path(tmp)
            os.chdir(temp_root)
            container = Container(
                settings=_RuntimeBoundarySmokeSettings(),
                stub=False,
            )
            try:
                ticker = container.autogrowth_background_ticker
                ticker_present = ticker is not None
                if ticker is not None:
                    ticker_is_running = getattr(ticker, "is_running", None)
                    interval_seconds = getattr(ticker, "interval_seconds", None)
                    max_ticks_per_wake = getattr(
                        ticker,
                        "max_ticks_per_wake",
                        None,
                    )
                cp_db = container.control_plane_db
                if cp_db is not None:
                    control_plane_path = str(cp_db.db_path)
                    schema_version = cp_db.schema_version()
            finally:
                cp_db = (
                    getattr(container, "control_plane_db", None)
                    if container is not None else None
                )
                if cp_db is not None:
                    cp_db.close()
                cp_db = None
                ticker = None
                container = None
                os.chdir(old_cwd)
                gc.collect()
    finally:
        os.chdir(old_cwd)

    temp_artifacts_removed = temp_root is not None and not temp_root.exists()
    ok = (
        ticker_present
        and ticker_is_running is False
        and interval_seconds == 30.0
        and max_ticks_per_wake == 20
        and lifespan_start_stop_present
        and schema_version is not None
        and temp_artifacts_removed
    )
    return {
        "proof_id": "low_risk_autogrowth_runtime_boundary_smoke_v1",
        "ok": ok,
        "runtime_wiring_present": ticker_present and lifespan_start_stop_present,
        "container_ticker_present": ticker_present,
        "lifespan_start_stop_present": lifespan_start_stop_present,
        "default_interval_seconds": interval_seconds,
        "default_max_ticks_per_wake": max_ticks_per_wake,
        "is_running_before_start": ticker_is_running,
        "temporary_control_plane_db": True,
        "temporary_control_plane_db_path": control_plane_path,
        "control_plane_schema_version_present": schema_version is not None,
        "temp_artifacts_removed": temp_artifacts_removed,
        "production_control_plane_mutated": False,
        "runtime_authority_changed": False,
        "operator_gate_required": False,
        "external_writes_applied": False,
        "safe_conclusion": (
            "Runtime wiring can construct an AutogrowthBackgroundTicker with "
            "the default cadence and bounded max ticks per wake, and FastAPI "
            "lifespan contains start/stop hooks. The smoke uses only a "
            "temporary control-plane DB and does not change production runtime "
            "authority."
        ),
    }


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
                "waggledance/core/magma/runtime_summary_receipt.py",
                "Opt-in runtime summary receipt binds sanitized query-path payloads.",
            ),
            (
                "tools/run_runtime_receipt_emission_proof.py",
                "Executable proof for verified runtime receipt emission.",
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
            (
                "waggledance/bootstrap/container.py",
                "Runtime container wires the background autogrowth ticker.",
            ),
            (
                "waggledance/adapters/http/api.py",
                "FastAPI lifespan starts and stops the autogrowth ticker.",
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
            (
                "waggledance/bootstrap/container.py",
                "Runtime container wires the active hex topology registry.",
            ),
            (
                "waggledance/application/services/hex_topology_registry.py",
                "Runtime-facing agent hex topology registry.",
            ),
            (
                "waggledance/application/services/hex_neighbor_assist.py",
                "Feature-flagged runtime hex neighbor assist boundary.",
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
    hex_upgrade_proof = build_hexagonal_upgrade_proof(root)
    hex_upgrade_runtime_smoke = build_hexagonal_upgrade_runtime_smoke(root)
    hex_upgrade_proof["runtime_boundary_smoke"] = (
        hex_upgrade_runtime_smoke
    )
    hex_upgrade_proof["ok"] = bool(
        hex_upgrade_proof.get("ok") is True
        and hex_upgrade_runtime_smoke.get("ok") is True
    )
    low_risk_autonomy_proof = build_low_risk_autonomy_proof()
    low_risk_runtime_boundary_smoke = (
        build_low_risk_autogrowth_runtime_boundary_smoke(root)
    )
    low_risk_autonomy_proof["runtime_boundary_smoke"] = (
        low_risk_runtime_boundary_smoke
    )
    low_risk_autonomy_proof["ok"] = bool(
        low_risk_autonomy_proof.get("ok") is True
        and low_risk_runtime_boundary_smoke.get("ok") is True
    )
    hex_entry_proof = build_hex_mesh_entry_proof(root)
    solver_trace_proof = build_deterministic_solver_trace_proof(root)

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
                "Render the WS route-stage labels in the dashboard UI and "
                "add a visual contract smoke."
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
                "Solver-first routing surfaces exist, SolverRouter emits "
                "a privacy-safe selected-solver trace, and an opt-in MAGMA "
                "runtime summary receipt can bind that trace; default full "
                "coverage remains a next boundary."
            ),
            status=_status_for(solver_evidence),
            claim_safe=False,
            evidence=solver_evidence,
            gaps=(
                "Solver trace receipt binding is currently opt-in through a "
                "runtime receipt sink, not default for every runtime path.",
                "The image's 'full MAGMA provenance' wording should wait for "
                "trace-completeness evidence.",
            ),
            next_smallest_pr=(
                "Promote the solver trace receipt sink from opt-in proof to "
                "configured runtime coverage and exposed metrics."
            ),
            proof=solver_trace_proof,
        ),
        Capability(
            capability_id="magma_audit_log",
            title="MAGMA audit log",
            image_claim="MAGMA is an append-only provenance trail.",
            safe_statement=(
                "MAGMA audit/provenance wrappers, receipt bundles, and an "
                "opt-in solver-trace runtime receipt proof exist; hard "
                "append-only/default enforcement is not yet safe to claim."
            ),
            status=_status_for(magma_evidence),
            claim_safe=False,
            evidence=magma_evidence,
            gaps=(
                "The solver trace receipt proof uses an opt-in disk bundle "
                "sink; the default runtime does not require it yet.",
                "The storage truth doc says append-only is convention for "
                "some MAGMA-backed paths.",
                "A hardening pass is needed before literal append-only "
                "language is safe.",
            ),
            next_smallest_pr=(
                "Promote the runtime receipt sink to configured append-only "
                "storage coverage or keep default wording at opt-in proof level."
            ),
            proof=solver_trace_proof.get("magma_execution_receipt_proof"),
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
                "allowlist, runtime gap seam, scheduler ticks, a runtime "
                "ticker boundary smoke, and proof fixtures; unrestricted "
                "runtime authority is not claimed."
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
                "The runtime boundary smoke proves ticker construction and "
                "lifespan hooks, not autonomous production authority.",
            ),
            next_smallest_pr=(
                "Promote runtime boundary reporting into operator-visible "
                "metrics without changing the low-risk authority boundary."
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
                "and parent-child relation queries exist, and a runtime "
                "boundary smoke reports the active config topology without "
                "mutating it."
            ),
            status=_status_for(hex_upgrade_evidence),
            claim_safe=False,
            evidence=hex_upgrade_evidence,
            gaps=(
                "Subdivision is shadow-first and does not mutate runtime "
                "topology.",
                "Ring delivery is pure validation, not a networked runtime "
                "delivery layer.",
                "The runtime boundary smoke reports current Container/config "
                "wiring; it does not activate subdivision authority.",
            ),
            next_smallest_pr=(
                "Promote hexagonal topology boundary reporting into "
                "operator-visible metrics without enabling runtime mutation."
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

# SPDX-License-Identifier: BUSL-1.1
"""Repo/runtime-read-only capability manifest for the WD Image #1 storyboard.

The tool deliberately separates the visual claim from repo-safe wording.
It does not mutate runtime state, bridge state, GitHub state, or tracked files.
Local executable proofs may create ephemeral temp files and delete them before
returning.
"""

from __future__ import annotations

import argparse
import ast
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

from tools.hex_shadow_subdivision_replay import (  # noqa: E402
    build_shadow_subdivision_replay_verifier_summary_bridge_event_template_index_entry,
    build_shadow_subdivision_replay_verifier_summary_bridge_event_template,
    build_shadow_subdivision_replay_verifier_summary,
    build_source_snapshot,
    build_shadow_subdivision_replay_artifact,
    verify_shadow_subdivision_replay_artifact,
)
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
HEX_MESH_CHAT_ROUTE_ORDER = (
    "language_detection",
    "hot_cache",
    "memory_context",
    "route_selection",
    "deterministic_solver",
    "hybrid_retrieval_8_cell",
    "hex_neighbor_assist_7_cell",
    "orchestrator_llm_fallback",
)


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
    route_order = list(HEX_MESH_CHAT_ROUTE_ORDER)
    return {
        "proof_id": "hex_mesh_entry_route_order_v1",
        "ok": False,
        "blocked_reason": "missing_required_inputs",
        "missing_inputs": list(missing_inputs),
        "proves_every_query_first_enters_mesh": False,
        "literal_claim_safe": False,
        "current_config": current_config
        or {
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
        solver_assignments.append(
            {
                "query": sample["query"],
                "intent": sample["intent"],
                "expected_cell": sample["expected_cell"],
                "cell_id": assignment.cell_id,
                "method": assignment.method,
                "ring1": assignment.neighbors_ring1,
                "ring2": assignment.neighbors_ring2,
                "matched_expected": assignment.cell_id == sample["expected_cell"],
            }
        )

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
        neighbors = (
            [cell.id for cell in agent_registry.get_neighbor_cells(str(cell_id))]
            if cell_id
            else []
        )
        agent_assignments.append(
            {
                "query": sample["query"],
                "intent": sample["intent"],
                "expected_cell": sample["expected_cell"],
                "cell_id": cell_id,
                "ring1": neighbors,
                "matched_expected": cell_id == sample["expected_cell"],
            }
        )

    route_order = list(HEX_MESH_CHAT_ROUTE_ORDER)
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
    route_stage_ui_smoke = build_hex_mesh_route_stage_ui_smoke(repo_root)
    route_stage_operator_metrics_smoke = (
        build_hex_mesh_route_stage_operator_metrics_smoke(repo_root)
    )
    route_stage_runtime_metrics_smoke = (
        build_hex_mesh_route_stage_runtime_metrics_smoke(repo_root)
    )
    proof["runtime_trace_smoke"] = runtime_trace_smoke
    proof["route_stage_ui_smoke"] = route_stage_ui_smoke
    proof["route_stage_operator_metrics_smoke"] = route_stage_operator_metrics_smoke
    proof["route_stage_runtime_metrics_smoke"] = route_stage_runtime_metrics_smoke
    proof["ok"] = bool(
        proof["ok"]
        and runtime_trace_smoke["ok"]
        and route_stage_ui_smoke["ok"]
        and route_stage_operator_metrics_smoke["ok"]
        and route_stage_runtime_metrics_smoke["ok"]
    )
    return proof


def build_hex_mesh_route_stage_ui_smoke(root: Path | str = ROOT) -> dict:
    """Verify that sanitized route-stage labels are visible in the dashboard."""

    proof_id = "hex_mesh_route_stage_ui_smoke_v1"
    repo_root = Path(root)
    required_paths = (
        "web/hologram-brain-v6.html",
        "waggledance/adapters/http/routes/chat.py",
        "tests/test_hologram_ui_stabilization.py",
        "tests/integration/test_chat_api_contract.py",
    )
    missing_inputs = [
        rel_path for rel_path in required_paths if not (repo_root / rel_path).exists()
    ]
    base = {
        "proof_id": proof_id,
        "expected_route_stages": list(HEX_MESH_CHAT_ROUTE_ORDER),
        "dashboard_path": "web/hologram-brain-v6.html",
        "event_builder_path": "waggledance/adapters/http/routes/chat.py",
        "test_only_instrumentation": False,
        "no_runtime_mutation": True,
        "external_writes_applied": False,
    }
    if missing_inputs:
        return {
            **base,
            "ok": False,
            "blocked_reason": "missing_required_inputs",
            "missing_inputs": missing_inputs,
        }

    resolved_repo_root = repo_root.resolve()
    resolved_import_root = ROOT.resolve()
    if resolved_repo_root != resolved_import_root:
        return {
            **base,
            "ok": False,
            "blocked_reason": "non_current_import_root",
            "missing_inputs": [],
            "inspected_root": str(resolved_repo_root),
            "import_root": str(resolved_import_root),
        }

    html = (repo_root / "web" / "hologram-brain-v6.html").read_text(encoding="utf-8")
    chat_route_source = (
        repo_root / "waggledance" / "adapters" / "http" / "routes" / "chat.py"
    ).read_text(encoding="utf-8")
    ui_test_source = (
        repo_root / "tests" / "test_hologram_ui_stabilization.py"
    ).read_text(encoding="utf-8")
    api_contract_source = (
        repo_root / "tests" / "integration" / "test_chat_api_contract.py"
    ).read_text(encoding="utf-8")

    try:
        from types import SimpleNamespace

        from waggledance.adapters.http.routes.chat import (
            ChatHttpResponse,
            _build_chat_route_ws_event,
        )

        raw_markers = {
            "query": "WD_IMAGE1_PRIVATE_QUERY_MARKER",
            "language": "WD_IMAGE1_PRIVATE_LANGUAGE_MARKER",
            "profile": "WD_IMAGE1_PRIVATE_PROFILE_MARKER",
            "session": "WD_IMAGE1_PRIVATE_SESSION_MARKER",
        }
        resp = ChatHttpResponse(
            response="route-stage ui smoke",
            source="llm",
            confidence=0.8,
            latency_ms=1.0,
            cached=False,
            language="en",
            agent_id="wd_image1_route_stage_ui_smoke",
            round_table=False,
            route_stage_trace=[
                {
                    "stage": "language_detection",
                    "explicit_hint": True,
                    "detected_language": raw_markers["language"],
                    "query": raw_markers["query"],
                },
                {
                    "stage": "hot_cache",
                    "hit": False,
                    "cache_key": raw_markers["session"],
                },
                {
                    "stage": "hybrid_retrieval_8_cell",
                    "enabled": True,
                    "mode": "candidate",
                    "cell_id": "math",
                    "profile": raw_markers["profile"],
                },
            ],
        )
        service = SimpleNamespace(
            _hybrid_retrieval=SimpleNamespace(enabled=True),
            _hex_neighbor_assist=SimpleNamespace(enabled=False),
        )
        event = _build_chat_route_ws_event(resp, service)
        data = event.get("data") if isinstance(event, dict) else {}
        data = data if isinstance(data, dict) else {}
        labels = data.get("route_stage_labels")
        labels = labels if isinstance(labels, list) else []
        serialized = json.dumps(data, sort_keys=True)
        forbidden_raw_markers_absent = all(
            marker not in serialized for marker in raw_markers.values()
        )
        disabled_route_stages = data.get("disabled_route_stages")
        disabled_route_stages = (
            disabled_route_stages if isinstance(disabled_route_stages, list) else []
        )
        ws_event_contract = {
            "ok": (
                event.get("type") == "chat_route"
                and "query" not in data
                and "language" not in data
                and "profile" not in data
                and isinstance(data.get("route_stage_trace"), list)
                and isinstance(data.get("route_stage_labels"), list)
                and "hex_neighbor_assist_7_cell" in disabled_route_stages
                and forbidden_raw_markers_absent
            ),
            "event_type": event.get("type"),
            "data_keys": sorted(data.keys()),
            "label_stages": [
                item.get("stage") for item in labels if isinstance(item, dict)
            ],
            "disabled_route_stages": disabled_route_stages,
            "forbidden_raw_markers_absent": forbidden_raw_markers_absent,
        }
    except Exception as exc:  # pragma: no cover - surfaced in proof payload
        ws_event_contract = {
            "ok": False,
            "error": repr(exc),
            "data_keys": [],
            "label_stages": [],
            "disabled_route_stages": [],
            "forbidden_raw_markers_absent": False,
        }

    checks = {
        "dashboard_stage_allowlist_present": (
            "const CHAT_ROUTE_STAGE_NAMES" in html
            and all(stage in html for stage in HEX_MESH_CHAT_ROUTE_ORDER)
        ),
        "dashboard_stage_container_present": (
            "data-route-stage-list" in html
            and "route-stage-chip" in html
            and "route-stage-observed" in html
            and "route-stage-disabled" in html
            and "t('chat.route_stages_label')" in html
        ),
        "dashboard_uses_client_allowlist": (
            "CHAT_ROUTE_STAGE_NAMES[stage]" in html
            and "if (!name || seen.has(stage)) return;" in html
            and "item.label" not in html
            and "route_stage_trace" not in html
        ),
        "dashboard_escapes_stage_rendering": all(
            token in html
            for token in (
                "escapeHtml(row.stage)",
                "escapeHtml(row.name)",
                "escapeHtml(statusLabel)",
            )
        ),
        "dashboard_ws_event_handler_present": (
            "msg.type === 'chat_route'" in html
            and "_mergeChatRouteInfo(msg.data, false)" in html
            and "_mergeChatRouteInfo(data, true)" in html
        ),
        "ws_event_builder_exposes_safe_labels": all(
            token in chat_route_source
            for token in (
                '"route_stage_trace": trace',
                '"route_stage_labels": labels',
                '"disabled_route_stages": disabled_route_stages',
                "_sanitize_route_stage_trace(resp.route_stage_trace)",
            )
        ),
        "ui_regression_test_present": all(
            token in ui_test_source
            for token in (
                "test_hologram_chat_renders_ws_route_stage_labels",
                "test_hologram_route_stage_renderer_uses_client_allowlist",
                "test_chat_route_http_response_does_not_clobber_ws_stage_details",
            )
        ),
        "api_contract_privacy_test_present": all(
            token in api_contract_source
            for token in (
                "test_success_response_includes_privacy_safe_route_stage_trace",
                "test_route_stage_trace_boundary_drops_unsafe_trace_keys",
                "raw_query not in event_json",
                "raw_language not in event_json",
                "raw_profile not in event_json",
            )
        ),
        "ws_event_contract_runtime_ok": ws_event_contract["ok"] is True,
    }
    ok = all(checks.values())
    return {
        **base,
        "ok": ok,
        "checks": checks,
        "ws_event_contract": ws_event_contract,
        "observed_ui_stage_names": [
            stage for stage in HEX_MESH_CHAT_ROUTE_ORDER if stage in html
        ],
        "safe_conclusion": (
            "The dashboard chat panel renders privacy-safe route-stage labels "
            "from the HTTP/WS contract via a client-side allowlist. It does "
            "not render backend-supplied free-form labels or raw trace payloads."
        ),
    }


def build_hex_mesh_route_stage_operator_metrics_smoke(
    root: Path | str = ROOT,
) -> dict:
    """Verify route-stage counts are exposed as read-only operator metrics."""

    proof_id = "hex_mesh_route_stage_operator_metrics_smoke_v1"
    repo_root = Path(root)
    required_paths = (
        "waggledance/adapters/http/routes/metrics.py",
        "waggledance/adapters/http/routes/chat.py",
        "tests/test_metrics_endpoint.py",
        "docs/API.md",
    )
    metric_groups = (
        "expected",
        "enabled",
        "pre_hex",
        "hex_backed",
        "optional",
        "disabled_optional",
    )
    missing_inputs = [
        rel_path for rel_path in required_paths if not (repo_root / rel_path).exists()
    ]
    base = {
        "proof_id": proof_id,
        "metrics_endpoint": "/metrics",
        "metric_name": "waggledance_route_stage_count",
        "metric_groups": list(metric_groups),
        "expected_route_stages": list(HEX_MESH_CHAT_ROUTE_ORDER),
        "test_only_instrumentation": False,
        "runtime_routing_changed": False,
        "disabled_hex_paths_enabled": False,
        "no_runtime_mutation": True,
        "external_writes_applied": False,
    }
    if missing_inputs:
        return {
            **base,
            "ok": False,
            "blocked_reason": "missing_required_inputs",
            "missing_inputs": missing_inputs,
        }

    resolved_repo_root = repo_root.resolve()
    resolved_import_root = ROOT.resolve()
    if resolved_repo_root != resolved_import_root:
        return {
            **base,
            "ok": False,
            "blocked_reason": "non_current_import_root",
            "missing_inputs": [],
            "inspected_root": str(resolved_repo_root),
            "import_root": str(resolved_import_root),
        }

    metrics_text = (
        repo_root / "waggledance/adapters/http/routes/metrics.py"
    ).read_text(encoding="utf-8")
    tests_text = (repo_root / "tests/test_metrics_endpoint.py").read_text(
        encoding="utf-8"
    )
    docs_text = (repo_root / "docs/API.md").read_text(encoding="utf-8")

    try:
        from types import SimpleNamespace

        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from waggledance.adapters.http.routes.metrics import router as metrics_router

        class _MetricsHexAssist:
            enabled = False

            def get_metrics(self) -> dict:
                return {"enabled": False}

        app = FastAPI()
        app.state.container = SimpleNamespace(
            hex_neighbor_assist=_MetricsHexAssist(),
            hybrid_retrieval=SimpleNamespace(enabled=True),
            autogrowth_background_ticker=None,
        )
        app.include_router(metrics_router)
        resp = TestClient(app).get("/metrics")
        body = resp.text
        forbidden_markers = (
            "WD_IMAGE1_PRIVATE_QUERY_MARKER",
            "query=",
            "profile=",
            "language=",
            "route_stage_trace",
        )
        expected_lines = {
            'waggledance_route_stage_count{group="expected"} 8.0',
            'waggledance_route_stage_count{group="enabled"} 7.0',
            'waggledance_route_stage_count{group="pre_hex"} 5.0',
            'waggledance_route_stage_count{group="hex_backed"} 2.0',
            'waggledance_route_stage_count{group="optional"} 2.0',
            'waggledance_route_stage_count{group="disabled_optional"} 1.0',
        }
        runtime_contract = {
            "ok": (
                resp.status_code == 200
                and expected_lines.issubset(set(body.splitlines()))
                and all(marker not in body for marker in forbidden_markers)
            ),
            "status_code": resp.status_code,
            "expected_lines": sorted(expected_lines),
            "missing_lines": sorted(
                line for line in expected_lines if line not in body
            ),
            "forbidden_payload_markers_absent": all(
                marker not in body for marker in forbidden_markers
            ),
        }
    except Exception as exc:  # pragma: no cover - surfaced in proof payload
        runtime_contract = {
            "ok": False,
            "error": repr(exc),
            "status_code": None,
            "expected_lines": [],
            "missing_lines": [],
            "forbidden_payload_markers_absent": False,
        }

    forbidden_mutation_tokens = (
        "component.enabled =",
        "hex_neighbor_assist.enabled =",
        "hybrid_retrieval.enabled =",
        ".resolve(",
        ".retrieve(",
    )
    checks = {
        "metrics_reuse_route_stage_allowlist": (
            "CHAT_ROUTE_STAGE_ORDER" in metrics_text
            and "len(CHAT_ROUTE_STAGE_ORDER)" in metrics_text
        ),
        "route_stage_count_metric_present": (
            "waggledance_route_stage_count" in metrics_text
            and "disabled_optional" in metrics_text
            and "hex_backed" in metrics_text
            and "pre_hex" in metrics_text
        ),
        "optional_component_flags_read_only": (
            "_route_stage_component_enabled" in metrics_text
            and '_safe_getattr(component, "enabled", False)' in metrics_text
            and not any(token in metrics_text for token in forbidden_mutation_tokens)
        ),
        "endpoint_regression_tests_present": all(
            token in tests_text
            for token in (
                "test_metrics_body_contains_route_stage_count_gauges",
                "test_metrics_route_stage_counts_disable_missing_optional_components",
                'waggledance_route_stage_count{group="expected"} 8.0',
                'waggledance_route_stage_count{group="disabled_optional"} 1.0',
            )
        ),
        "api_docs_present": (
            "privacy-safe route-stage count gauges" in docs_text
            and "waggledance_route_stage_count" in docs_text
            and "enable disabled hex paths" in docs_text
        ),
        "runtime_contract_ok": runtime_contract["ok"] is True,
    }
    ok = all(checks.values())
    return {
        **base,
        "ok": ok,
        "checks": checks,
        "runtime_contract": runtime_contract,
        "operator_visible_metrics": ok,
        "safe_conclusion": (
            "The public Prometheus /metrics endpoint exposes route-stage "
            "counts derived from the static stage allowlist and optional "
            "component flags. The scrape path records no raw query/context "
            "data and does not enable disabled hex routing paths."
        ),
    }


def build_hex_mesh_route_stage_runtime_metrics_smoke(
    root: Path | str = ROOT,
) -> dict:
    """Verify sanitized route traces feed per-stage runtime metrics."""

    proof_id = "hex_mesh_route_stage_runtime_metrics_smoke_v1"
    repo_root = Path(root)
    required_paths = (
        "waggledance/adapters/http/routes/chat.py",
        "waggledance/adapters/http/routes/metrics.py",
        "waggledance/adapters/http/routes/compat_dashboard.py",
        "waggledance/adapters/http/route_stage_latency_feed.py",
        "waggledance/bootstrap/container.py",
        "web/hologram-brain-v6.html",
        "configs/settings.yaml",
        "tests/test_metrics_endpoint.py",
        "tests/integration/test_chat_api_contract.py",
        "tests/test_legacy_consolidation.py",
        "docs/API.md",
        "docs/operations/ROUTE_STAGE_LATENCY_RUNBOOK.md",
    )
    metric_names = (
        "waggledance_route_stage_observations_total",
        "waggledance_route_stage_request_latency_ms_total",
        "waggledance_route_stage_request_latency_histogram_ms",
    )
    missing_inputs = [
        rel_path for rel_path in required_paths if not (repo_root / rel_path).exists()
    ]
    base = {
        "proof_id": proof_id,
        "metrics_endpoint": "/metrics",
        "metric_names": list(metric_names),
        "expected_route_stages": list(HEX_MESH_CHAT_ROUTE_ORDER),
        "source_trace": "ChatHttpResponse.route_stage_trace",
        "runtime_routing_changed": False,
        "disabled_hex_paths_enabled": False,
        "raw_payload_recorded": False,
        "no_runtime_mutation": True,
        "external_writes_applied": False,
    }
    if missing_inputs:
        return {
            **base,
            "ok": False,
            "blocked_reason": "missing_required_inputs",
            "missing_inputs": missing_inputs,
        }

    resolved_repo_root = repo_root.resolve()
    resolved_import_root = ROOT.resolve()
    if resolved_repo_root != resolved_import_root:
        return {
            **base,
            "ok": False,
            "blocked_reason": "non_current_import_root",
            "missing_inputs": [],
            "inspected_root": str(resolved_repo_root),
            "import_root": str(resolved_import_root),
        }

    chat_text = (repo_root / "waggledance/adapters/http/routes/chat.py").read_text(
        encoding="utf-8"
    )
    metrics_text = (
        repo_root / "waggledance/adapters/http/routes/metrics.py"
    ).read_text(encoding="utf-8")
    ops_text = (
        repo_root / "waggledance/adapters/http/routes/compat_dashboard.py"
    ).read_text(encoding="utf-8")
    provider_text = (
        repo_root / "waggledance/adapters/http/route_stage_latency_feed.py"
    ).read_text(encoding="utf-8")
    container_text = (repo_root / "waggledance/bootstrap/container.py").read_text(
        encoding="utf-8"
    )
    html_text = (repo_root / "web/hologram-brain-v6.html").read_text(encoding="utf-8")
    settings_text = (repo_root / "configs/settings.yaml").read_text(encoding="utf-8")
    metrics_tests_text = (repo_root / "tests/test_metrics_endpoint.py").read_text(
        encoding="utf-8"
    )
    chat_tests_text = (
        repo_root / "tests/integration/test_chat_api_contract.py"
    ).read_text(encoding="utf-8")
    ops_tests_text = (repo_root / "tests/test_legacy_consolidation.py").read_text(
        encoding="utf-8"
    )
    docs_text = (repo_root / "docs/API.md").read_text(encoding="utf-8")
    runbook_text = (
        repo_root / "docs/operations/ROUTE_STAGE_LATENCY_RUNBOOK.md"
    ).read_text(encoding="utf-8")

    try:
        from types import SimpleNamespace

        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from waggledance.adapters.http.routes.chat import (
            RouteStageRuntimeMetrics,
            _sanitize_route_stage_trace,
        )
        from waggledance.adapters.http.routes.metrics import router as metrics_router

        class _MetricsHexAssist:
            enabled = False

            def get_metrics(self) -> dict:
                return {"enabled": False}

        raw_trace = [
            {
                "stage": "language_detection",
                "query": "WD_IMAGE1_PRIVATE_QUERY_MARKER",
                "profile": "WD_IMAGE1_PRIVATE_PROFILE_MARKER",
                "detected_language": "WD_IMAGE1_PRIVATE_LANGUAGE_MARKER",
            },
            {
                "stage": "hot_cache",
                "hit": False,
                "route_stage_trace": "WD_IMAGE1_PRIVATE_TRACE_MARKER",
            },
            {
                "stage": "orchestrator_llm_fallback",
                "source": "llm",
            },
        ]
        sanitized_trace = _sanitize_route_stage_trace(raw_trace)
        runtime_metrics = RouteStageRuntimeMetrics()
        runtime_metrics.record(sanitized_trace, 12.5)
        runtime_metrics.record(
            [{"stage": "language_detection"}, {"stage": "hot_cache"}],
            7.5,
        )
        app = FastAPI()
        app.state.container = SimpleNamespace(
            hex_neighbor_assist=_MetricsHexAssist(),
            hybrid_retrieval=SimpleNamespace(enabled=True),
            route_stage_runtime_metrics=runtime_metrics,
            autogrowth_background_ticker=None,
        )
        app.include_router(metrics_router)
        resp = TestClient(app).get("/metrics")
        body = resp.text
        forbidden_markers = (
            "WD_IMAGE1_PRIVATE_QUERY_MARKER",
            "WD_IMAGE1_PRIVATE_PROFILE_MARKER",
            "WD_IMAGE1_PRIVATE_LANGUAGE_MARKER",
            "WD_IMAGE1_PRIVATE_TRACE_MARKER",
            "query=",
            "profile=",
            "language=",
            "route_stage_trace",
        )
        expected_lines = {
            "waggledance_route_stage_observations_total{"
            'stage="language_detection"} 2.0',
            "waggledance_route_stage_observations_total{" 'stage="hot_cache"} 2.0',
            "waggledance_route_stage_observations_total{"
            'stage="orchestrator_llm_fallback"} 1.0',
            "waggledance_route_stage_request_latency_ms_total{"
            'stage="language_detection"} 20.0',
            "waggledance_route_stage_request_latency_ms_total{"
            'stage="hot_cache"} 20.0',
            "waggledance_route_stage_request_latency_ms_total{"
            'stage="orchestrator_llm_fallback"} 12.5',
            "waggledance_route_stage_request_latency_histogram_ms_bucket{"
            'le="50",stage="language_detection"} 2.0',
            "waggledance_route_stage_request_latency_histogram_ms_count{"
            'stage="language_detection"} 2.0',
            "waggledance_route_stage_request_latency_histogram_ms_sum{"
            'stage="language_detection"} 20.0',
        }
        runtime_contract = {
            "ok": (
                resp.status_code == 200
                and expected_lines.issubset(set(body.splitlines()))
                and all(marker not in body for marker in forbidden_markers)
            ),
            "status_code": resp.status_code,
            "expected_lines": sorted(expected_lines),
            "missing_lines": sorted(
                line for line in expected_lines if line not in body
            ),
            "forbidden_payload_markers_absent": all(
                marker not in body for marker in forbidden_markers
            ),
            "sanitized_trace": sanitized_trace,
        }
    except Exception as exc:  # pragma: no cover - surfaced in proof payload
        runtime_contract = {
            "ok": False,
            "error": repr(exc),
            "status_code": None,
            "expected_lines": [],
            "missing_lines": [],
            "forbidden_payload_markers_absent": False,
            "sanitized_trace": [],
        }

    forbidden_storage_tokens = (
        "req.query",
        "request.query",
        "profile=",
        "language=",
        'route_stage_trace": trace',
    )
    checks = {
        "chat_records_after_sanitized_response": (
            "RouteStageRuntimeMetrics" in chat_text
            and "_record_route_stage_runtime_metrics" in chat_text
            and "ChatHttpResponse.from_result(result)" in chat_text
            and "resp.route_stage_trace" in chat_text
            and "resp.latency_ms" in chat_text
        ),
        "runtime_aggregator_allowlists_stage_names": (
            "stage in self._allowed_stages" in chat_text
            and "observations_total" in chat_text
            and "request_latency_ms_total" in chat_text
            and "request_latency_ms_buckets" in chat_text
        ),
        "metrics_export_counters_present": all(
            token in metrics_text
            for token in (
                "_collect_route_stage_runtime_metrics",
                "waggledance_route_stage_observations_total",
                "waggledance_route_stage_request_latency_ms_total",
                "waggledance_route_stage_request_latency_histogram_ms",
                "HistogramMetricFamily",
                "CHAT_ROUTE_STAGE_ORDER",
            )
        ),
        "raw_payload_storage_absent": not any(
            token in metrics_text for token in forbidden_storage_tokens
        ),
        "endpoint_regression_tests_present": all(
            token in metrics_tests_text
            for token in (
                "test_metrics_body_contains_route_stage_runtime_counters",
                "test_metrics_route_stage_runtime_counters_default_to_zero",
                "WD_IMAGE1_PRIVATE_QUERY_MARKER",
                "not_an_allowed_stage",
                "request_latency_histogram_ms_bucket",
                "request_latency_histogram_ms_count",
            )
        ),
        "chat_contract_records_metrics_after_request": all(
            token in chat_tests_text
            for token in (
                "test_chat_request_updates_privacy_safe_route_stage_runtime_metrics",
                "waggledance_route_stage_observations_total",
                "waggledance_route_stage_request_latency_histogram_ms_bucket",
                "PRIVATE_QUERY_MARKER_METRICS",
            )
        ),
        "ops_latency_panel_templates_present": all(
            token in "\n".join((ops_text, html_text, ops_tests_text))
            for token in (
                "route_stage_latency",
                "routeStagePanels",
                "RouteStageLatencyP95Warning",
                "RouteStageLatencyP99Critical",
                "histogram_quantile(0.95",
                "histogram_quantile(0.99",
                "prometheus_query_templates",
                "controls_present",
            )
        ),
        "ops_latency_feed_state_present": all(
            token
            in "\n".join(
                (
                    ops_text,
                    provider_text,
                    html_text,
                    ops_tests_text,
                    docs_text,
                    runbook_text,
                )
            )
            for token in (
                "route_stage_latency_feed",
                "feed_state",
                "prometheus_alertmanager_snapshot",
                "RouteStageLatencyPrometheusAlertmanagerFeed",
                "RouteStageLatencyFeedUnavailable",
                "panel_values",
                "activeRouteStageLatencyAlerts",
                "test_ops_route_stage_latency_feed_state_sanitizes_snapshot",
                "test_ops_route_stage_latency_feed_state_rejects_non_finite_numbers",
                "test_route_stage_latency_feed_provider_rejects_non_finite_numbers",
                "It intentionally does not forward Alertmanager annotations",
                "math.isfinite",
            )
        ),
        "ops_latency_feed_provider_wired": all(
            token in "\n".join((provider_text, container_text, settings_text))
            for token in (
                "def route_stage_latency_feed",
                "RouteStageLatencyPrometheusAlertmanagerFeed.from_config",
                "prometheus_base_url",
                "alertmanager_base_url",
                "allowed_private_hosts",
                "enabled: false",
            )
        ),
        "ops_latency_feed_provider_guardrails_present": all(
            token in provider_text
            for token in (
                "URL_USERINFO_REFUSED",
                "URL_QUERY_REFUSED",
                "URL_SECRET_REFUSED",
                "URL_PRIVATE_HOST_REFUSED",
                "CREDENTIAL_HEADER_REFUSED",
                "follow_redirects=False",
                "MAX_TIMEOUT_SECONDS",
                "MAX_RESPONSE_BYTES",
            )
        ),
        "latency_runbook_present": all(
            token in runbook_text
            for token in (
                "Route-stage latency operator runbook",
                "waggledance_route_stage_request_latency_histogram_ms_bucket",
                "RouteStageLatencyP95Warning",
                "RouteStageLatencyP99Critical",
                "histogram_quantile(0.95",
                "histogram_quantile(0.99",
                "Optional feed provider",
                "allowed_private_hosts",
                "No panel or alert rule should call a mutating endpoint",
            )
        ),
        "api_docs_present": (
            "route-stage runtime observation/latency counters" in docs_text
            and "waggledance_route_stage_observations_total" in docs_text
            and "waggledance_route_stage_request_latency_ms_total" in docs_text
            and "waggledance_route_stage_request_latency_histogram_ms_bucket"
            in docs_text
            and "ROUTE_STAGE_LATENCY_RUNBOOK.md" in docs_text
            and "It is not an internal span timer" in docs_text
            and "route_stage_latency_feed" in docs_text
            and "allowed_private_hosts" in docs_text
        ),
        "runtime_contract_ok": runtime_contract["ok"] is True,
    }
    ok = all(checks.values())
    return {
        **base,
        "ok": ok,
        "checks": checks,
        "runtime_contract": runtime_contract,
        "operator_visible_metrics": ok,
        "rate_query_supported": ok,
        "histogram_quantile_supported": ok,
        "latency_panel_templates_visible": ok,
        "prometheus_alertmanager_feed_supported": ok,
        "prometheus_alertmanager_feed_provider_configured": ok,
        "latency_feed_state_visible": ok,
        "alert_thresholds_documented": ok,
        "latency_metric_semantics": "stage_correlated_request_latency",
        "runbook_path": "docs/operations/ROUTE_STAGE_LATENCY_RUNBOOK.md",
        "safe_conclusion": (
            "The public Prometheus /metrics endpoint exposes per-stage "
            "observation counters and stage-correlated request latency "
            "counters and histograms from sanitized route traces. It supports "
            "Prometheus rate, p95/p99 histogram_quantile panels, and optional "
            "sanitized Prometheus/Alertmanager feed state without storing raw "
            "query, profile, language, context, or full route trace payloads."
        ),
    }


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
        stage for stage in static_route_order if stage not in disabled_static_stages
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
            "hybrid_trace_present": getattr(result, "hybrid_trace", None) is not None,
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
    missing = [rel_path for rel_path in required if not (repo_root / rel_path).exists()]
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
        "solver_call_trace_digest_bound": report.get("solver_call_trace_digest_bound"),
        "solver_call_trace_receipt_bound": report.get(
            "solver_call_trace_receipt_bound"
        ),
        "solver_call_trace_privacy_safe": report.get("solver_call_trace_privacy_safe"),
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
    missing = [rel_path for rel_path in required if not (repo_root / rel_path).exists()]
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
    query_text_recorded = sample_query in trace_json or '"query"' in trace_json
    receipt_proof = build_solver_trace_magma_receipt_proof(root)
    ok = (
        result.quality_path == "gold"
        and result.selection.fallback_used is False
        and selected_solver_ids == ["solve.math"]
        and bool(trace)
        and not query_text_recorded
        and all(item.get("execution_boundary") == "safe_action_bus" for item in trace)
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
            "solver_call_trace_count": receipt_proof.get("solver_call_trace_count"),
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
    missing = [rel_path for rel_path in required if not (repo_root / rel_path).exists()]
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
        and relations["thermal_children"]
        == [
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
        "operator_metrics_smoke": {
            "proof_id": "hexagonal_topology_boundary_operator_metrics_smoke_v1",
            "ok": False,
            "operator_visible_metrics": False,
            "metrics_endpoint": "/metrics",
            "metric_names": [
                "waggledance_hex_topology_boundary_up",
                "waggledance_hex_topology_cells",
                "waggledance_hex_topology_agents_mapped",
                "waggledance_hex_topology_neighbor_links",
                "waggledance_hex_topology_runtime_dispatch_enabled",
                "waggledance_hex_topology_runtime_mutation_authority",
            ],
        },
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
        "waggledance/adapters/http/routes/metrics.py",
        "waggledance/core/hex_topology/subdivision_operator.py",
        "waggledance/core/hex_topology/ring_messaging.py",
        "waggledance/core/hex_topology/parent_child_relations.py",
        "tests/test_metrics_endpoint.py",
        "docs/API.md",
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
    metrics_text = (
        repo_root / "waggledance/adapters/http/routes/metrics.py"
    ).read_text(encoding="utf-8")
    metrics_tests_text = (repo_root / "tests/test_metrics_endpoint.py").read_text(
        encoding="utf-8"
    )
    docs_text = (repo_root / "docs/API.md").read_text(encoding="utf-8")
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
    operator_metric_names = (
        "waggledance_hex_topology_boundary_up",
        "waggledance_hex_topology_cells",
        "waggledance_hex_topology_agents_mapped",
        "waggledance_hex_topology_neighbor_links",
        "waggledance_hex_topology_runtime_dispatch_enabled",
        "waggledance_hex_topology_runtime_mutation_authority",
    )
    metrics_contract = {
        "ok": False,
        "status_code": None,
        "expected_lines": [],
        "missing_lines": [],
        "forbidden_payload_markers_absent": False,
    }
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
        enabled_cell_ids = [cell_id for cell_id, cell in cells.items() if cell.enabled]
        neighbor_map = {
            cell_id: [neighbor.id for neighbor in registry.get_neighbor_cells(cell_id)]
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
            sample_origins.append(
                {
                    "query": sample["query"],
                    "intent": sample["intent"],
                    "expected_cell": sample["expected_cell"],
                    "cell_id": selected,
                    "matched_expected": selected == sample["expected_cell"],
                }
            )
        container_registry_present = registry.cell_count > 0

        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from waggledance.adapters.http.routes.metrics import router as metrics_router

        app = FastAPI()
        app.state.container = container
        app.include_router(metrics_router)
        resp = TestClient(app).get("/metrics")
        body = resp.text
        expected_lines = {
            "waggledance_hex_topology_boundary_up 1.0",
            'waggledance_hex_topology_cells{state="configured"} 7.0',
            'waggledance_hex_topology_cells{state="enabled"} 7.0',
            "waggledance_hex_topology_agents_mapped 42.0",
            "waggledance_hex_topology_neighbor_links 24.0",
            "waggledance_hex_topology_runtime_dispatch_enabled 0.0",
            "waggledance_hex_topology_runtime_mutation_authority 0.0",
        }
        forbidden_markers = (
            "WD_IMAGE1_PRIVATE_QUERY_MARKER",
            "query=",
            "profile=",
            "route_stage_trace",
        )
        missing_lines = sorted(line for line in expected_lines if line not in body)
        metrics_contract = {
            "ok": (
                resp.status_code == 200
                and not missing_lines
                and all(marker not in body for marker in forbidden_markers)
            ),
            "status_code": resp.status_code,
            "expected_lines": sorted(expected_lines),
            "missing_lines": missing_lines,
            "forbidden_payload_markers_absent": all(
                marker not in body for marker in forbidden_markers
            ),
        }
    finally:
        os.chdir(old_cwd)

    shadow_child_ids = ("thermal.heating", "thermal.cooling")
    shadow_children_absent = all(
        child_id not in cell_ids for child_id in shadow_child_ids
    )
    metrics_checks = {
        "metric_collectors_present": all(
            name in metrics_text for name in operator_metric_names
        ),
        "registry_boundary_read_present": (
            "hex_topology_registry" in metrics_text
            and "stats_fn()" in metrics_text
            and 'getattr(registry, "cells"' in metrics_text
        ),
        "endpoint_regression_tests_present": all(
            token in metrics_tests_text
            for token in (
                "test_metrics_body_contains_hex_topology_boundary_gauges",
                "test_metrics_hex_topology_boundary_missing_registry_fails_closed",
                'waggledance_hex_topology_cells{state="configured"}',
                "waggledance_hex_topology_runtime_mutation_authority 0.0",
            )
        ),
        "api_docs_present": (
            "hex topology boundary gauges" in docs_text
            and "waggledance_hex_topology_cells" in docs_text
            and "do not enable dispatch" in docs_text
        ),
        "runtime_contract_ok": metrics_contract["ok"] is True,
    }
    operator_metrics_smoke = {
        "proof_id": "hexagonal_topology_boundary_operator_metrics_smoke_v1",
        "ok": all(metrics_checks.values()),
        "metrics_endpoint": "/metrics",
        "metric_names": list(operator_metric_names),
        "runtime_contract": metrics_contract,
        "checks": metrics_checks,
        "operator_visible_metrics": all(metrics_checks.values()),
        "no_runtime_topology_mutation": True,
        "runtime_authority_changed": False,
        "external_writes_applied": False,
        "safe_conclusion": (
            "The public /metrics endpoint exposes only aggregated active "
            "hex topology boundary gauges: cell counts, mapped-agent count, "
            "directed neighbor-link count, dispatch gate state, and an "
            "explicit zero runtime-mutation-authority guardrail. It does "
            "not expose raw query/context data or add topology controls."
        ),
    }
    ok = (
        container_registry_present
        and assist_wiring_present
        and registry_stats.get("cells_loaded") == 7
        and len(enabled_cell_ids) == 7
        and all(item["matched_expected"] for item in sample_origins)
        and shadow_children_absent
        and operator_metrics_smoke["ok"]
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
        "operator_metrics_smoke": operator_metrics_smoke,
        "shadow_child_cell_ids_absent_from_runtime_config": (shadow_children_absent),
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
                if tick.intent_id is not None
                else None
            )
            queue_rows = cp.list_autogrowth_queue(limit=5)
            runs = cp.list_autogrowth_runs(family_kind=family_kind, limit=5)
            served_value = (
                after.output if isinstance(after.output, (int, float)) else None
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
                    if intent is not None
                    else None
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
    missing = [rel_path for rel_path in required if not (repo_root / rel_path).exists()]
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
        with tempfile.TemporaryDirectory(prefix="wd-image1-autogrowth-runtime-") as tmp:
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
                    if container is not None
                    else None
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


def _blocked_low_risk_operator_metrics_smoke(
    *,
    missing_inputs: Sequence[str],
    blocked_reason: str = "missing_required_inputs",
    inspected_root: str | None = None,
    import_root: str | None = None,
) -> dict:
    proof = {
        "proof_id": "low_risk_autogrowth_operator_metrics_smoke_v1",
        "ok": False,
        "blocked_reason": blocked_reason,
        "missing_inputs": list(missing_inputs),
        "metrics_endpoint": "/metrics",
        "prometheus_namespace": "waggledance_autogrowth",
        "operator_visible_metrics": False,
        "metric_names": [],
        "missing_metrics": [],
        "runtime_authority_changed": False,
        "operator_gate_required": False,
        "external_writes_applied": False,
        "safe_conclusion": (
            "Required operator metrics inputs are missing, so the low-risk "
            "autogrowth boundary cannot be shown as operator-visible for "
            "this root."
        ),
    }
    if blocked_reason == "non_current_import_root":
        proof["safe_conclusion"] = (
            "The inspected root is not the manifest tool's current import "
            "root, so the operator-metrics proof blocks instead of "
            "certifying one checkout with runtime code imported from "
            "another checkout."
        )
    if inspected_root is not None:
        proof["inspected_root"] = inspected_root
    if import_root is not None:
        proof["import_root"] = import_root
    return proof


def build_low_risk_autogrowth_operator_metrics_smoke(
    root: Path | str = ROOT,
) -> dict:
    """Prove low-risk autogrowth exposes operator-visible metrics."""

    repo_root = Path(root)
    required = (
        "waggledance/adapters/http/routes/metrics.py",
        "waggledance/core/autonomy_growth/autogrowth_scheduler.py",
        "tests/test_metrics_endpoint.py",
        "docs/API.md",
    )
    missing = [rel_path for rel_path in required if not (repo_root / rel_path).exists()]
    if missing:
        return _blocked_low_risk_operator_metrics_smoke(
            missing_inputs=missing,
        )

    resolved_repo_root = repo_root.resolve()
    resolved_import_root = ROOT.resolve()
    if resolved_repo_root != resolved_import_root:
        return _blocked_low_risk_operator_metrics_smoke(
            missing_inputs=[],
            blocked_reason="non_current_import_root",
            inspected_root=str(resolved_repo_root),
            import_root=str(resolved_import_root),
        )

    metrics_text = (
        repo_root / "waggledance/adapters/http/routes/metrics.py"
    ).read_text(encoding="utf-8")
    metrics_tree = ast.parse(metrics_text)
    counter_source_names: list[str] = []
    gauge_source_names: set[str] = set()
    literal_metric_names: set[str] = set()
    for node in ast.walk(metrics_tree):
        if isinstance(node, ast.Assign):
            target_names = [
                target.id for target in node.targets if isinstance(target, ast.Name)
            ]
            if "_AUTOGROWTH_COUNTER_NAMES" in target_names and isinstance(
                node.value, ast.Tuple
            ):
                counter_source_names = [
                    item.value
                    for item in node.value.elts
                    if isinstance(item, ast.Constant) and isinstance(item.value, str)
                ]
            if "gauge_values" in target_names and isinstance(node.value, ast.Dict):
                gauge_source_names.update(
                    key.value
                    for key in node.value.keys
                    if isinstance(key, ast.Constant) and isinstance(key.value, str)
                )
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == "_AUTOGROWTH_COUNTER_NAMES"
            and isinstance(node.value, ast.Tuple)
        ):
            counter_source_names = [
                item.value
                for item in node.value.elts
                if isinstance(item, ast.Constant) and isinstance(item.value, str)
            ]
        if isinstance(node, ast.Call) and node.args:
            func_name = node.func.id if isinstance(node.func, ast.Name) else None
            if (
                func_name in {"GaugeMetricFamily", "CounterMetricFamily"}
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
                and node.args[0].value.startswith("waggledance_autogrowth")
            ):
                literal_metric_names.add(node.args[0].value)

    emitted_metric_names = set(literal_metric_names)
    emitted_metric_names.update(
        f"waggledance_autogrowth_{name}" for name in gauge_source_names
    )
    for source_name in counter_source_names:
        base = source_name[:-6] if source_name.endswith("_total") else source_name
        emitted_metric_names.add(f"waggledance_autogrowth_{base}_total")

    test_text = (repo_root / "tests/test_metrics_endpoint.py").read_text(
        encoding="utf-8"
    )
    expected_metrics = {
        "waggledance_autogrowth_up",
        "waggledance_autogrowth_background_enabled",
        "waggledance_autogrowth_background_running",
        "waggledance_autogrowth_background_interval_seconds",
        "waggledance_autogrowth_background_max_ticks_per_wake",
        "waggledance_autogrowth_wakeups_total",
        "waggledance_autogrowth_non_idle_ticks_total",
        "waggledance_autogrowth_errors_total",
    }
    missing_metrics = [
        name
        for name in sorted(expected_metrics)
        if name not in emitted_metric_names or name not in test_text
    ]
    double_suffix_absent = all(
        "total_total" not in name for name in emitted_metric_names
    )
    ok = not missing_metrics and double_suffix_absent
    return {
        "proof_id": "low_risk_autogrowth_operator_metrics_smoke_v1",
        "ok": ok,
        "proof_mode": "ast_source_contract",
        "metrics_endpoint": "/metrics",
        "prometheus_namespace": "waggledance_autogrowth",
        "operator_visible_metrics": ok,
        "metric_names": sorted(expected_metrics),
        "missing_metrics": missing_metrics,
        "double_suffix_absent": double_suffix_absent,
        "runtime_authority_changed": False,
        "operator_gate_required": False,
        "external_writes_applied": False,
        "safe_conclusion": (
            "The public Prometheus /metrics endpoint exposes the low-risk "
            "autogrowth background ticker boundary as scrapeable operator "
            "metrics. The proof parses the metrics source and endpoint tests "
            "without importing Prometheus, and does not start the ticker or "
            "grant runtime growth authority."
        ),
    }


def _blocked_low_risk_autogrowth_alert_runbook_smoke(
    *,
    missing_inputs: Sequence[str],
    blocked_reason: str = "missing_required_inputs",
) -> dict:
    return {
        "proof_id": "low_risk_autogrowth_alert_runbook_smoke_v1",
        "ok": False,
        "blocked_reason": blocked_reason,
        "missing_inputs": list(missing_inputs),
        "runbook_path": "docs/operations/LOW_RISK_AUTOGROWTH_RUNBOOK.md",
        "api_docs_path": "docs/API.md",
        "alert_thresholds_documented": False,
        "metric_names": [],
        "missing_metric_mentions": [],
        "missing_threshold_rules": [],
        "api_docs_link_runbook": False,
        "forbidden_controls_absent": False,
        "forbidden_control_tokens_found": [],
        "runtime_authority_changed": False,
        "operator_gate_required": False,
        "external_writes_applied": False,
        "safe_conclusion": (
            "Required runbook or API documentation inputs are missing, so "
            "operator alert thresholds cannot be certified for this root."
        ),
    }


def build_low_risk_autogrowth_alert_runbook_smoke(
    root: Path | str = ROOT,
) -> dict:
    """Prove low-risk autogrowth has read-only operator alert thresholds."""

    repo_root = Path(root)
    runbook_rel = "docs/operations/LOW_RISK_AUTOGROWTH_RUNBOOK.md"
    api_rel = "docs/API.md"
    required = (runbook_rel, api_rel)
    missing = [rel_path for rel_path in required if not (repo_root / rel_path).exists()]
    if missing:
        return _blocked_low_risk_autogrowth_alert_runbook_smoke(
            missing_inputs=missing,
        )

    runbook_text = (repo_root / runbook_rel).read_text(encoding="utf-8")
    api_text = (repo_root / api_rel).read_text(encoding="utf-8")
    runbook_lower = runbook_text.lower()
    api_lower = api_text.lower()

    metric_names = {
        "waggledance_autogrowth_up",
        "waggledance_autogrowth_background_enabled",
        "waggledance_autogrowth_background_running",
        "waggledance_autogrowth_background_interval_seconds",
        "waggledance_autogrowth_background_max_ticks_per_wake",
        "waggledance_autogrowth_wakeups_total",
        "waggledance_autogrowth_non_idle_ticks_total",
        "waggledance_autogrowth_errors_total",
    }
    missing_metric_mentions = [
        name for name in sorted(metric_names) if name not in runbook_text
    ]
    required_threshold_rules = {
        "waggledance_autogrowth_up == 0",
        "increase(waggledance_autogrowth_errors_total[10m]) > 0",
        "increase(waggledance_autogrowth_errors_total[10m]) >= 3",
        "increase(waggledance_autogrowth_wakeups_total[30m]) == 0",
        "increase(waggledance_autogrowth_wakeups_total[10m]) > 40",
        "increase(waggledance_autogrowth_non_idle_ticks_total[10m]) > 20",
    }
    missing_threshold_rules = [
        rule for rule in sorted(required_threshold_rules) if rule not in runbook_text
    ]
    forbidden_control_tokens = {
        "POST /api/autogrowth",
        "autogrowth_start",
        "autogrowth_stop",
        "start_button",
        "stop_button",
        "config_write",
        "write_config",
    }
    forbidden_control_tokens_found = [
        token
        for token in sorted(forbidden_control_tokens)
        if token.lower() in runbook_lower
    ]
    api_docs_link_runbook = (
        "low_risk_autogrowth_runbook.md" in api_lower
        and "waggledance_autogrowth_errors_total" in api_text
        and "waggledance_autogrowth_wakeups_total" in api_text
    )
    guardrail_language_present = all(
        phrase in runbook_lower
        for phrase in (
            "does not add runtime controls",
            "does not grant new solver-growth authority",
            "no alert rule in this runbook should call a mutating endpoint",
            "no alert rule should auto-merge",
        )
    )
    ok = (
        not missing_metric_mentions
        and not missing_threshold_rules
        and api_docs_link_runbook
        and not forbidden_control_tokens_found
        and guardrail_language_present
    )
    return {
        "proof_id": "low_risk_autogrowth_alert_runbook_smoke_v1",
        "ok": ok,
        "proof_mode": "source_doc_contract",
        "runbook_path": runbook_rel,
        "api_docs_path": api_rel,
        "alert_thresholds_documented": not missing_threshold_rules,
        "metric_names": sorted(metric_names),
        "missing_metric_mentions": missing_metric_mentions,
        "missing_threshold_rules": missing_threshold_rules,
        "api_docs_link_runbook": api_docs_link_runbook,
        "forbidden_controls_absent": not forbidden_control_tokens_found,
        "forbidden_control_tokens_found": forbidden_control_tokens_found,
        "guardrail_language_present": guardrail_language_present,
        "runtime_authority_changed": False,
        "operator_gate_required": False,
        "external_writes_applied": False,
        "safe_conclusion": (
            "The low-risk autogrowth runbook documents conservative "
            "Prometheus alert thresholds for source health, error count, "
            "wakeup stalls, wakeup bursts, and non-idle burst rates. The "
            "contract is read-only and does not introduce runtime controls "
            "or growth authority."
        ),
    }


def _blocked_magma_handoff_provider_metrics_runbook_smoke(
    *,
    missing_inputs: Sequence[str],
    blocked_reason: str = "missing_required_inputs",
) -> dict:
    return {
        "proof_id": "magma_handoff_provider_metrics_runbook_smoke_v1",
        "ok": False,
        "blocked_reason": blocked_reason,
        "missing_inputs": list(missing_inputs),
        "runbook_path": ("docs/operations/MAGMA_HANDOFF_PROVIDER_METRICS_RUNBOOK.md"),
        "api_docs_path": "docs/API.md",
        "metrics_endpoint": "/metrics",
        "alert_thresholds_documented": False,
        "metric_names": [],
        "missing_metric_mentions": [],
        "missing_threshold_rules": [],
        "api_docs_link_runbook": False,
        "source_metrics_contract_present": False,
        "forbidden_controls_absent": False,
        "forbidden_control_tokens_found": [],
        "guardrail_language_present": False,
        "runtime_authority_changed": False,
        "operator_gate_required": False,
        "external_writes_applied": False,
        "safe_conclusion": (
            "Required MAGMA handoff metrics runbook or API documentation "
            "inputs are missing, so provider alert thresholds cannot be "
            "certified for this root."
        ),
    }


def build_magma_handoff_provider_metrics_runbook_smoke(
    root: Path | str = ROOT,
) -> dict:
    """Prove MAGMA handoff provider metrics have read-only thresholds."""

    repo_root = Path(root)
    runbook_rel = "docs/operations/MAGMA_HANDOFF_PROVIDER_METRICS_RUNBOOK.md"
    api_rel = "docs/API.md"
    metrics_rel = "waggledance/adapters/http/routes/metrics.py"
    tests_rel = "tests/test_metrics_endpoint.py"
    required = (runbook_rel, api_rel, metrics_rel, tests_rel)
    missing = [rel_path for rel_path in required if not (repo_root / rel_path).exists()]
    if missing:
        return _blocked_magma_handoff_provider_metrics_runbook_smoke(
            missing_inputs=missing,
        )

    runbook_text = (repo_root / runbook_rel).read_text(encoding="utf-8")
    api_text = (repo_root / api_rel).read_text(encoding="utf-8")
    metrics_text = (repo_root / metrics_rel).read_text(encoding="utf-8")
    tests_text = (repo_root / tests_rel).read_text(encoding="utf-8")
    runbook_lower = runbook_text.lower()
    api_lower = api_text.lower()

    metric_names = {
        "waggledance_magma_handoff_provider_up",
        "waggledance_magma_handoff_provider_configured",
        "waggledance_magma_handoff_snapshot_valid",
        "waggledance_magma_handoff_history_feed_present",
        "waggledance_magma_handoff_history_retained_count",
        "waggledance_magma_handoff_history_dropped_count",
        "waggledance_magma_handoff_freshness_source_configured",
        "waggledance_magma_handoff_freshness_source_valid",
        "waggledance_magma_handoff_freshness_source_stale",
        "waggledance_magma_handoff_active_alerts",
        "waggledance_magma_handoff_local_paths_recorded",
        "waggledance_magma_handoff_runtime_authority_granted",
        "waggledance_magma_handoff_payload_files_imported",
        "waggledance_magma_handoff_provider_status",
        "waggledance_magma_handoff_freshness_state",
        "waggledance_magma_handoff_provider_alert_active",
    }
    missing_metric_mentions = [
        name for name in sorted(metric_names) if name not in runbook_text
    ]
    required_threshold_rules = {
        "waggledance_magma_handoff_provider_up == 0",
        "waggledance_magma_handoff_snapshot_valid == 0",
        "waggledance_magma_handoff_freshness_source_stale == 1",
        "waggledance_magma_handoff_history_dropped_count > 0",
        "waggledance_magma_handoff_local_paths_recorded > 0",
        "waggledance_magma_handoff_runtime_authority_granted > 0",
        "waggledance_magma_handoff_payload_files_imported > 0",
        (
            "waggledance_magma_handoff_provider_alert_active{"
            'alert_id="MagmaShareImportHandoffProviderUnavailable"} == 1'
        ),
        (
            "waggledance_magma_handoff_provider_alert_active{"
            'alert_id="MagmaShareImportHandoffFreshnessSourceUnavailable"} '
            "== 1"
        ),
    }
    missing_threshold_rules = [
        rule for rule in sorted(required_threshold_rules) if rule not in runbook_text
    ]
    forbidden_control_tokens = {
        "import_payload",
        "payload_import",
        "grant_runtime_authority",
        "authority_mutation",
        "write_config",
        "config_write",
        "auto_import",
        "auto_merge",
    }
    forbidden_control_tokens_found = [
        token
        for token in sorted(forbidden_control_tokens)
        if token.lower() in runbook_lower
    ]
    api_docs_link_runbook = (
        "magma_handoff_provider_metrics_runbook.md" in api_lower
        and "waggledance_magma_handoff_provider_up" in api_text
        and "waggledance_magma_handoff_freshness_source_stale" in api_text
        and "do not import payloads" in api_lower
        and "runtime authority" in api_lower
    )
    source_metrics_contract_present = all(
        token in "\n".join((metrics_text, tests_text))
        for token in (
            "_collect_magma_handoff_metrics",
            "_MAGMA_HANDOFF_PROVIDER_ALERT_IDS",
            "waggledance_magma_handoff_provider_up",
            "waggledance_magma_handoff_provider_alert_active",
            "MagmaShareImportHandoffProviderUnavailable",
            "MagmaShareImportHandoffFreshnessSourceUnavailable",
            "test_metrics_body_contains_magma_handoff_provider_health_gauges",
            "test_metrics_magma_handoff_freshness_failure_is_sanitized",
        )
    )
    guardrail_language_present = all(
        phrase in runbook_lower
        for phrase in (
            "does not add import controls",
            "does not grant runtime authority",
            "no alert rule in this runbook should call a mutating endpoint",
            "no alert rule should import payloads",
            "no alert rule should auto-merge",
        )
    )
    ok = (
        not missing_metric_mentions
        and not missing_threshold_rules
        and api_docs_link_runbook
        and source_metrics_contract_present
        and not forbidden_control_tokens_found
        and guardrail_language_present
    )
    return {
        "proof_id": "magma_handoff_provider_metrics_runbook_smoke_v1",
        "ok": ok,
        "proof_mode": "source_doc_contract",
        "runbook_path": runbook_rel,
        "api_docs_path": api_rel,
        "metrics_endpoint": "/metrics",
        "alert_thresholds_documented": not missing_threshold_rules,
        "metric_names": sorted(metric_names),
        "missing_metric_mentions": missing_metric_mentions,
        "missing_threshold_rules": missing_threshold_rules,
        "api_docs_link_runbook": api_docs_link_runbook,
        "source_metrics_contract_present": source_metrics_contract_present,
        "forbidden_controls_absent": not forbidden_control_tokens_found,
        "forbidden_control_tokens_found": forbidden_control_tokens_found,
        "guardrail_language_present": guardrail_language_present,
        "runtime_authority_changed": False,
        "operator_gate_required": False,
        "external_writes_applied": False,
        "safe_conclusion": (
            "The MAGMA handoff provider metrics runbook documents "
            "conservative Prometheus alert thresholds for source health, "
            "snapshot validity, freshness, retention drops, private-material "
            "flags, runtime-authority flags, and payload-import flags. The "
            "contract is read-only and does not introduce import controls or "
            "runtime authority."
        ),
    }


def _blocked_magma_handoff_metrics_alert_state_smoke(
    *,
    missing_inputs: Sequence[str],
    blocked_reason: str = "missing_required_inputs",
) -> dict:
    return {
        "proof_id": "magma_handoff_metrics_alert_state_smoke_v1",
        "ok": False,
        "blocked_reason": blocked_reason,
        "missing_inputs": list(missing_inputs),
        "ops_endpoint": "/api/ops",
        "dashboard_path": "web/hologram-brain-v6.html",
        "api_contract_present": False,
        "ui_contract_present": False,
        "test_contract_present": False,
        "docs_contract_present": False,
        "runbook_contract_present": False,
        "fixed_alert_ids_enforced": False,
        "alert_state_visible": False,
        "forbidden_controls_absent": False,
        "forbidden_control_tokens_found": [],
        "runtime_authority_changed": False,
        "operator_gate_required": False,
        "external_writes_applied": False,
        "safe_conclusion": (
            "Required Ops API, dashboard, test, or documentation inputs are "
            "missing, so the MAGMA handoff metrics alert-state feed cannot be "
            "certified."
        ),
    }


def build_magma_handoff_metrics_alert_state_smoke(
    root: Path | str = ROOT,
) -> dict:
    """Prove the MAGMA handoff metrics alert-state feed is read-only."""

    repo_root = Path(root)
    api_rel = "waggledance/adapters/http/routes/compat_dashboard.py"
    html_rel = "web/hologram-brain-v6.html"
    tests_rel = "tests/test_legacy_consolidation.py"
    docs_rel = "docs/API.md"
    runbook_rel = "docs/operations/MAGMA_HANDOFF_PROVIDER_METRICS_RUNBOOK.md"
    required = (api_rel, html_rel, tests_rel, docs_rel, runbook_rel)
    missing = [rel_path for rel_path in required if not (repo_root / rel_path).exists()]
    if missing:
        return _blocked_magma_handoff_metrics_alert_state_smoke(
            missing_inputs=missing,
        )

    api_text = (repo_root / api_rel).read_text(encoding="utf-8")
    html_text = (repo_root / html_rel).read_text(encoding="utf-8")
    tests_text = (repo_root / tests_rel).read_text(encoding="utf-8")
    docs_text = (repo_root / docs_rel).read_text(encoding="utf-8")
    runbook_text = (repo_root / runbook_rel).read_text(encoding="utf-8")
    combined_runtime_lower = "\n".join(
        (api_text, html_text, docs_text, runbook_text)
    ).lower()

    alert_ids = {
        "MagmaHandoffMetricsSourceDown",
        "MagmaHandoffSnapshotInvalid",
        "MagmaHandoffFreshnessStale",
        "MagmaHandoffRetentionDropped",
        "MagmaHandoffPrivateMaterialRecorded",
        "MagmaHandoffRuntimeAuthorityReported",
        "MagmaHandoffPayloadImported",
        "MagmaHandoffProviderUnavailable",
        "MagmaHandoffFreshnessSourceUnavailable",
    }
    api_contract_present = all(
        token in api_text
        for token in (
            "MAGMA_HANDOFF_METRICS_ALERT_IDS",
            "MAGMA_HANDOFF_METRICS_ALERT_METRICS",
            "magma_share_import_handoff_metrics_alert_feed",
            '"metrics_alert_state"',
            '"prometheus_alertmanager_snapshot"',
            '"controls_present"',
            "_sanitize_magma_handoff_metrics_active_alerts",
            "MagmaHandoffMetricsAlertFeedUnavailable",
        )
    )
    ui_contract_present = all(
        token in html_text
        for token in (
            "magmaProviderHealth.metrics_alert_state",
            "activeMagmaMetricsAlerts",
            "MAGMA Handoff Metrics Alerts",
            "Metrics Feed",
        )
    )
    test_contract_present = all(
        token in tests_text
        for token in (
            "test_ops_magma_handoff_metrics_alert_state_sanitizes_snapshot",
            "test_ops_magma_handoff_metrics_alert_feed_failure_is_sanitized",
            "MagmaHandoffRuntimeAuthorityReported",
            "C:/private/prometheus.yml",
            "private operator stack trace",
        )
    )
    docs_contract_present = all(
        token in docs_text
        for token in (
            "provider_health.metrics_alert_state",
            "magma_share_import_handoff_metrics_alert_feed",
            "fixed MAGMA handoff metric alert IDs",
            "provider exception details",
            "do not import payloads",
        )
    )
    runbook_contract_present = all(
        token in runbook_text
        for token in (
            "MagmaHandoffRuntimeAuthorityReported",
            "MagmaHandoffPrivateMaterialRecorded",
            "Read-only alert-state",
            "must not trigger import",
        )
    )
    fixed_alert_ids_enforced = all(alert_id in api_text for alert_id in alert_ids)
    forbidden_control_tokens = {
        "import_payload",
        "payload_import",
        "grant_runtime_authority",
        "authority_mutation",
        "write_config",
        "config_write",
        "auto_import",
        "magma_share_import_handoff_start",
        "magma_share_import_handoff_stop",
        "magma_handoff_metrics_alert_feed_import",
        "start_button",
        "stop_button",
    }
    forbidden_control_tokens_found = [
        token
        for token in sorted(forbidden_control_tokens)
        if token.lower() in combined_runtime_lower
    ]
    ok = (
        api_contract_present
        and ui_contract_present
        and test_contract_present
        and docs_contract_present
        and runbook_contract_present
        and fixed_alert_ids_enforced
        and not forbidden_control_tokens_found
    )
    return {
        "proof_id": "magma_handoff_metrics_alert_state_smoke_v1",
        "ok": ok,
        "proof_mode": "source_contract",
        "ops_endpoint": "/api/ops",
        "dashboard_path": html_rel,
        "api_contract_present": api_contract_present,
        "ui_contract_present": ui_contract_present,
        "test_contract_present": test_contract_present,
        "docs_contract_present": docs_contract_present,
        "runbook_contract_present": runbook_contract_present,
        "fixed_alert_ids_enforced": fixed_alert_ids_enforced,
        "alert_ids": sorted(alert_ids),
        "alert_state_visible": ui_contract_present,
        "forbidden_controls_absent": not forbidden_control_tokens_found,
        "forbidden_control_tokens_found": forbidden_control_tokens_found,
        "runtime_authority_changed": False,
        "operator_gate_required": False,
        "external_writes_applied": False,
        "safe_conclusion": (
            "The MAGMA handoff metrics alert-state feed is optional, "
            "read-only, and exposed through /api/ops provider health. It "
            "accepts only fixed runbook alert IDs, sanitized timestamps, "
            "finite numeric samples, and WD-generated summaries; it does not "
            "add import controls or runtime authority."
        ),
    }


def _blocked_magma_handoff_metrics_alertmanager_adapter_smoke(
    *,
    missing_inputs: Sequence[str],
    blocked_reason: str = "missing_required_inputs",
) -> dict:
    return {
        "proof_id": "magma_handoff_metrics_alertmanager_adapter_smoke_v1",
        "ok": False,
        "blocked_reason": blocked_reason,
        "missing_inputs": list(missing_inputs),
        "adapter_path": (
            "waggledance/adapters/http/" "magma_handoff_metrics_alert_feed.py"
        ),
        "settings_path": "configs/settings.yaml",
        "ops_endpoint": "/api/ops",
        "adapter_contract_present": False,
        "container_contract_present": False,
        "settings_contract_present": False,
        "test_contract_present": False,
        "docs_contract_present": False,
        "cache_backoff_contract_present": False,
        "slo_drill_contract_present": False,
        "release_gate_examples_present": False,
        "release_evidence_package_contract_present": False,
        "release_evidence_validator_contract_present": False,
        "reviewer_handoff_summary_contract_present": False,
        "reviewer_bridge_event_template_contract_present": False,
        "reviewer_bridge_event_template_decision_reference_slot_present": False,
        "reviewer_handoff_bundle_index_contract_present": False,
        "reviewer_handoff_bundle_verifier_contract_present": False,
        "reviewer_handoff_bundle_verification_summary_contract_present": False,
        "reviewer_handoff_bundle_operator_decision_reference_validator_contract_present": False,
        "reviewer_handoff_bundle_operator_decision_reference_review_summary_contract_present": False,
        "reviewer_handoff_bundle_operator_decision_reference_review_bundle_index_contract_present": False,
        "reviewer_handoff_bundle_operator_decision_reference_review_bundle_verifier_contract_present": False,
        "reviewer_handoff_bundle_operator_decision_reference_review_bundle_verification_summary_contract_present": False,
        "reviewer_handoff_bundle_operator_decision_reference_review_bundle_verification_bridge_event_template_contract_present": False,
        "reviewer_handoff_bundle_operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry_contract_present": False,
        "reviewer_handoff_bundle_operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry_verifier_contract_present": False,
        "reviewer_handoff_bundle_operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry_verification_summary_contract_present": False,
        "reviewer_handoff_bundle_operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry_verification_summary_bridge_event_template_contract_present": False,
        "reviewer_handoff_bundle_operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry_contract_present": False,
        "reviewer_handoff_bundle_operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry_verifier_contract_present": False,
        "guardrails_present": False,
        "runtime_authority_changed": False,
        "operator_gate_required": False,
        "external_writes_applied": False,
        "safe_conclusion": (
            "Required adapter, settings, container, test, or documentation "
            "inputs are missing, so the MAGMA handoff metrics Alertmanager "
            "adapter cannot be certified."
        ),
    }


def build_magma_handoff_metrics_alertmanager_adapter_smoke(
    root: Path | str = ROOT,
) -> dict:
    """Prove the MAGMA metrics Alertmanager adapter is configured read-only."""

    repo_root = Path(root)
    adapter_rel = "waggledance/adapters/http/magma_handoff_metrics_alert_feed.py"
    container_rel = "waggledance/bootstrap/container.py"
    ops_rel = "waggledance/adapters/http/routes/compat_dashboard.py"
    metrics_rel = "waggledance/adapters/http/routes/metrics.py"
    package_rel = "tools/package_magma_alert_feed_release_evidence.py"
    validator_rel = "tools/validate_magma_alert_feed_release_evidence.py"
    summary_rel = "tools/build_magma_alert_feed_reviewer_handoff_summary.py"
    bridge_template_rel = (
        "tools/build_magma_alert_feed_reviewer_bridge_event_template.py"
    )
    bundle_index_rel = "tools/build_magma_alert_feed_reviewer_handoff_bundle_index.py"
    bundle_verifier_rel = (
        "tools/verify_magma_alert_feed_reviewer_handoff_bundle_index.py"
    )
    bundle_verification_summary_rel = (
        "tools/build_magma_alert_feed_reviewer_handoff_bundle_verification_summary.py"
    )
    decision_reference_validator_rel = "tools/validate_magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference.py"
    decision_reference_review_summary_rel = "tools/build_magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference_review_summary.py"
    decision_reference_review_bundle_index_rel = "tools/build_magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference_review_bundle_index.py"
    decision_reference_review_bundle_verifier_rel = "tools/verify_magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference_review_bundle_index.py"
    decision_reference_review_bundle_verification_summary_rel = "tools/build_magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference_review_bundle_verification_summary.py"
    decision_reference_review_bundle_verification_bridge_template_rel = "tools/build_magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference_review_bundle_verification_bridge_event_template.py"
    decision_reference_review_bundle_verification_bridge_template_index_entry_rel = (
        "tools/build_magma_decision_review_verification_template_index_entry.py"
    )
    decision_reference_review_bundle_verification_bridge_template_index_entry_verifier_rel = (
        "tools/verify_magma_decision_review_verification_template_index_entry.py"
    )
    decision_reference_review_bundle_verification_bridge_template_index_entry_summary_rel = (
        "tools/build_magma_decision_review_verification_template_index_entry_summary.py"
    )
    decision_reference_review_bundle_verification_bridge_template_index_entry_summary_bridge_template_rel = "tools/build_magma_decision_review_verification_template_index_entry_summary_bridge_event_template.py"
    decision_reference_review_bundle_verification_bridge_template_index_entry_summary_bridge_template_index_entry_rel = "tools/build_magma_decision_review_verification_template_index_entry_summary_bridge_event_template_index_entry.py"
    decision_reference_review_bundle_verification_bridge_template_index_entry_summary_bridge_template_index_entry_verifier_rel = "tools/verify_magma_decision_review_verification_template_index_entry_summary_bridge_event_template_index_entry.py"
    settings_rel = "configs/settings.yaml"
    tests_rel = "tests/test_legacy_consolidation.py"
    metrics_tests_rel = "tests/test_metrics_endpoint.py"
    package_tests_rel = "tests/tools/test_magma_alert_feed_release_evidence_package.py"
    validator_tests_rel = (
        "tests/tools/test_magma_alert_feed_release_evidence_validator.py"
    )
    summary_tests_rel = "tests/tools/test_magma_alert_feed_reviewer_handoff_summary.py"
    bridge_template_tests_rel = (
        "tests/tools/test_magma_alert_feed_reviewer_bridge_event_template.py"
    )
    bundle_index_tests_rel = (
        "tests/tools/test_magma_alert_feed_reviewer_handoff_bundle_index.py"
    )
    bundle_verifier_tests_rel = (
        "tests/tools/test_magma_alert_feed_reviewer_handoff_bundle_verifier.py"
    )
    bundle_verification_summary_tests_rel = "tests/tools/test_magma_alert_feed_reviewer_handoff_bundle_verification_summary.py"
    decision_reference_validator_tests_rel = "tests/tools/test_magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference_validator.py"
    decision_reference_review_summary_tests_rel = "tests/tools/test_magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference_review_summary.py"
    decision_reference_review_bundle_index_tests_rel = "tests/tools/test_magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference_review_bundle_index.py"
    decision_reference_review_bundle_verifier_tests_rel = "tests/tools/test_magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference_review_bundle_verifier.py"
    decision_reference_review_bundle_verification_summary_tests_rel = "tests/tools/test_magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference_review_bundle_verification_summary.py"
    decision_reference_review_bundle_verification_bridge_template_tests_rel = "tests/tools/test_magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference_review_bundle_verification_bridge_event_template.py"
    decision_reference_review_bundle_verification_bridge_template_index_entry_tests_rel = (
        "tests/tools/test_magma_decision_review_verification_template_index_entry.py"
    )
    decision_reference_review_bundle_verification_bridge_template_index_entry_verifier_tests_rel = "tests/tools/test_magma_decision_review_verification_template_index_entry_verifier.py"
    decision_reference_review_bundle_verification_bridge_template_index_entry_summary_tests_rel = "tests/tools/test_magma_decision_review_verification_template_index_entry_summary.py"
    decision_reference_review_bundle_verification_bridge_template_index_entry_summary_bridge_template_tests_rel = "tests/tools/test_magma_decision_review_verification_template_index_entry_summary_bridge_event_template.py"
    decision_reference_review_bundle_verification_bridge_template_index_entry_summary_bridge_template_index_entry_tests_rel = "tests/tools/test_magma_decision_review_verification_template_index_entry_summary_bridge_event_template_index_entry.py"
    decision_reference_review_bundle_verification_bridge_template_index_entry_summary_bridge_template_index_entry_verifier_tests_rel = "tests/tools/test_magma_decision_review_verification_template_index_entry_summary_bridge_event_template_index_entry_verifier.py"
    docs_rel = "docs/API.md"
    manifest_rel = "docs/architecture/WD_IMAGE1_FUNCTIONALITY_MANIFEST.md"
    runbook_rel = "docs/operations/MAGMA_HANDOFF_PROVIDER_METRICS_RUNBOOK.md"
    hologram_rel = "web/hologram-brain-v6.html"
    required = (
        adapter_rel,
        container_rel,
        ops_rel,
        metrics_rel,
        package_rel,
        validator_rel,
        summary_rel,
        bridge_template_rel,
        bundle_index_rel,
        bundle_verifier_rel,
        bundle_verification_summary_rel,
        decision_reference_validator_rel,
        decision_reference_review_summary_rel,
        decision_reference_review_bundle_index_rel,
        decision_reference_review_bundle_verifier_rel,
        decision_reference_review_bundle_verification_summary_rel,
        decision_reference_review_bundle_verification_bridge_template_rel,
        decision_reference_review_bundle_verification_bridge_template_index_entry_rel,
        decision_reference_review_bundle_verification_bridge_template_index_entry_verifier_rel,
        decision_reference_review_bundle_verification_bridge_template_index_entry_summary_rel,
        decision_reference_review_bundle_verification_bridge_template_index_entry_summary_bridge_template_rel,
        decision_reference_review_bundle_verification_bridge_template_index_entry_summary_bridge_template_index_entry_rel,
        decision_reference_review_bundle_verification_bridge_template_index_entry_summary_bridge_template_index_entry_verifier_rel,
        settings_rel,
        tests_rel,
        metrics_tests_rel,
        package_tests_rel,
        validator_tests_rel,
        summary_tests_rel,
        bridge_template_tests_rel,
        bundle_index_tests_rel,
        bundle_verifier_tests_rel,
        bundle_verification_summary_tests_rel,
        decision_reference_validator_tests_rel,
        decision_reference_review_summary_tests_rel,
        decision_reference_review_bundle_index_tests_rel,
        decision_reference_review_bundle_verifier_tests_rel,
        decision_reference_review_bundle_verification_summary_tests_rel,
        decision_reference_review_bundle_verification_bridge_template_tests_rel,
        decision_reference_review_bundle_verification_bridge_template_index_entry_tests_rel,
        decision_reference_review_bundle_verification_bridge_template_index_entry_verifier_tests_rel,
        decision_reference_review_bundle_verification_bridge_template_index_entry_summary_tests_rel,
        decision_reference_review_bundle_verification_bridge_template_index_entry_summary_bridge_template_tests_rel,
        decision_reference_review_bundle_verification_bridge_template_index_entry_summary_bridge_template_index_entry_tests_rel,
        decision_reference_review_bundle_verification_bridge_template_index_entry_summary_bridge_template_index_entry_verifier_tests_rel,
        docs_rel,
        manifest_rel,
        runbook_rel,
        hologram_rel,
    )
    missing = [rel_path for rel_path in required if not (repo_root / rel_path).exists()]
    if missing:
        return _blocked_magma_handoff_metrics_alertmanager_adapter_smoke(
            missing_inputs=missing,
        )

    adapter_text = (repo_root / adapter_rel).read_text(encoding="utf-8")
    container_text = (repo_root / container_rel).read_text(encoding="utf-8")
    ops_text = (repo_root / ops_rel).read_text(encoding="utf-8")
    metrics_text = (repo_root / metrics_rel).read_text(encoding="utf-8")
    package_text = (repo_root / package_rel).read_text(encoding="utf-8")
    validator_text = (repo_root / validator_rel).read_text(encoding="utf-8")
    summary_text = (repo_root / summary_rel).read_text(encoding="utf-8")
    bridge_template_text = (repo_root / bridge_template_rel).read_text(encoding="utf-8")
    bundle_index_text = (repo_root / bundle_index_rel).read_text(encoding="utf-8")
    bundle_verifier_text = (repo_root / bundle_verifier_rel).read_text(encoding="utf-8")
    bundle_verification_summary_text = (
        repo_root / bundle_verification_summary_rel
    ).read_text(encoding="utf-8")
    decision_reference_validator_text = (
        repo_root / decision_reference_validator_rel
    ).read_text(encoding="utf-8")
    decision_reference_review_summary_text = (
        repo_root / decision_reference_review_summary_rel
    ).read_text(encoding="utf-8")
    decision_reference_review_bundle_index_text = (
        repo_root / decision_reference_review_bundle_index_rel
    ).read_text(encoding="utf-8")
    decision_reference_review_bundle_verifier_text = (
        repo_root / decision_reference_review_bundle_verifier_rel
    ).read_text(encoding="utf-8")
    decision_reference_review_bundle_verification_summary_text = (
        repo_root / decision_reference_review_bundle_verification_summary_rel
    ).read_text(encoding="utf-8")
    decision_reference_review_bundle_verification_bridge_template_text = (
        repo_root / decision_reference_review_bundle_verification_bridge_template_rel
    ).read_text(encoding="utf-8")
    decision_reference_review_bundle_verification_bridge_template_index_entry_text = (
        repo_root
        / decision_reference_review_bundle_verification_bridge_template_index_entry_rel
    ).read_text(encoding="utf-8")
    decision_reference_review_bundle_verification_bridge_template_index_entry_verifier_text = (
        repo_root
        / decision_reference_review_bundle_verification_bridge_template_index_entry_verifier_rel
    ).read_text(
        encoding="utf-8"
    )
    decision_reference_review_bundle_verification_bridge_template_index_entry_summary_text = (
        repo_root
        / decision_reference_review_bundle_verification_bridge_template_index_entry_summary_rel
    ).read_text(
        encoding="utf-8"
    )
    decision_reference_review_bundle_verification_bridge_template_index_entry_summary_bridge_template_text = (
        repo_root
        / decision_reference_review_bundle_verification_bridge_template_index_entry_summary_bridge_template_rel
    ).read_text(
        encoding="utf-8"
    )
    decision_reference_review_bundle_verification_bridge_template_index_entry_summary_bridge_template_index_entry_text = (
        repo_root
        / decision_reference_review_bundle_verification_bridge_template_index_entry_summary_bridge_template_index_entry_rel
    ).read_text(
        encoding="utf-8"
    )
    decision_reference_review_bundle_verification_bridge_template_index_entry_summary_bridge_template_index_entry_verifier_text = (
        repo_root
        / decision_reference_review_bundle_verification_bridge_template_index_entry_summary_bridge_template_index_entry_verifier_rel
    ).read_text(
        encoding="utf-8"
    )
    settings_text = (repo_root / settings_rel).read_text(encoding="utf-8")
    tests_text = (repo_root / tests_rel).read_text(encoding="utf-8")
    metrics_tests_text = (repo_root / metrics_tests_rel).read_text(encoding="utf-8")
    package_tests_text = (repo_root / package_tests_rel).read_text(encoding="utf-8")
    validator_tests_text = (repo_root / validator_tests_rel).read_text(encoding="utf-8")
    summary_tests_text = (repo_root / summary_tests_rel).read_text(encoding="utf-8")
    bridge_template_tests_text = (repo_root / bridge_template_tests_rel).read_text(
        encoding="utf-8"
    )
    bundle_index_tests_text = (repo_root / bundle_index_tests_rel).read_text(
        encoding="utf-8"
    )
    bundle_verifier_tests_text = (repo_root / bundle_verifier_tests_rel).read_text(
        encoding="utf-8"
    )
    bundle_verification_summary_tests_text = (
        repo_root / bundle_verification_summary_tests_rel
    ).read_text(encoding="utf-8")
    decision_reference_validator_tests_text = (
        repo_root / decision_reference_validator_tests_rel
    ).read_text(encoding="utf-8")
    decision_reference_review_summary_tests_text = (
        repo_root / decision_reference_review_summary_tests_rel
    ).read_text(encoding="utf-8")
    decision_reference_review_bundle_index_tests_text = (
        repo_root / decision_reference_review_bundle_index_tests_rel
    ).read_text(encoding="utf-8")
    decision_reference_review_bundle_verifier_tests_text = (
        repo_root / decision_reference_review_bundle_verifier_tests_rel
    ).read_text(encoding="utf-8")
    decision_reference_review_bundle_verification_summary_tests_text = (
        repo_root / decision_reference_review_bundle_verification_summary_tests_rel
    ).read_text(encoding="utf-8")
    decision_reference_review_bundle_verification_bridge_template_tests_text = (
        repo_root
        / decision_reference_review_bundle_verification_bridge_template_tests_rel
    ).read_text(encoding="utf-8")
    decision_reference_review_bundle_verification_bridge_template_index_entry_tests_text = (
        repo_root
        / decision_reference_review_bundle_verification_bridge_template_index_entry_tests_rel
    ).read_text(
        encoding="utf-8"
    )
    decision_reference_review_bundle_verification_bridge_template_index_entry_verifier_tests_text = (
        repo_root
        / decision_reference_review_bundle_verification_bridge_template_index_entry_verifier_tests_rel
    ).read_text(
        encoding="utf-8"
    )
    decision_reference_review_bundle_verification_bridge_template_index_entry_summary_tests_text = (
        repo_root
        / decision_reference_review_bundle_verification_bridge_template_index_entry_summary_tests_rel
    ).read_text(
        encoding="utf-8"
    )
    decision_reference_review_bundle_verification_bridge_template_index_entry_summary_bridge_template_tests_text = (
        repo_root
        / decision_reference_review_bundle_verification_bridge_template_index_entry_summary_bridge_template_tests_rel
    ).read_text(
        encoding="utf-8"
    )
    decision_reference_review_bundle_verification_bridge_template_index_entry_summary_bridge_template_index_entry_tests_text = (
        repo_root
        / decision_reference_review_bundle_verification_bridge_template_index_entry_summary_bridge_template_index_entry_tests_rel
    ).read_text(
        encoding="utf-8"
    )
    decision_reference_review_bundle_verification_bridge_template_index_entry_summary_bridge_template_index_entry_verifier_tests_text = (
        repo_root
        / decision_reference_review_bundle_verification_bridge_template_index_entry_summary_bridge_template_index_entry_verifier_tests_rel
    ).read_text(
        encoding="utf-8"
    )
    docs_text = (repo_root / docs_rel).read_text(encoding="utf-8")
    manifest_text = (repo_root / manifest_rel).read_text(encoding="utf-8")
    runbook_text = (repo_root / runbook_rel).read_text(encoding="utf-8")
    hologram_text = (repo_root / hologram_rel).read_text(encoding="utf-8")
    combined_runtime_lower = "\n".join(
        (
            adapter_text,
            container_text,
            ops_text,
            metrics_text,
            package_text,
            validator_text,
            summary_text,
            bridge_template_text,
            bundle_index_text,
            bundle_verifier_text,
            bundle_verification_summary_text,
            decision_reference_validator_text,
            decision_reference_review_summary_text,
            decision_reference_review_bundle_index_text,
            decision_reference_review_bundle_verifier_text,
            decision_reference_review_bundle_verification_summary_text,
            decision_reference_review_bundle_verification_bridge_template_text,
            decision_reference_review_bundle_verification_bridge_template_index_entry_text,
            decision_reference_review_bundle_verification_bridge_template_index_entry_verifier_text,
            decision_reference_review_bundle_verification_bridge_template_index_entry_summary_text,
            decision_reference_review_bundle_verification_bridge_template_index_entry_summary_bridge_template_text,
            decision_reference_review_bundle_verification_bridge_template_index_entry_summary_bridge_template_index_entry_text,
            decision_reference_review_bundle_verification_bridge_template_index_entry_summary_bridge_template_index_entry_verifier_text,
            settings_text,
            metrics_tests_text,
            package_tests_text,
            validator_tests_text,
            summary_tests_text,
            bridge_template_tests_text,
            bundle_index_tests_text,
            bundle_verifier_tests_text,
            bundle_verification_summary_tests_text,
            decision_reference_validator_tests_text,
            decision_reference_review_summary_tests_text,
            decision_reference_review_bundle_index_tests_text,
            decision_reference_review_bundle_verifier_tests_text,
            decision_reference_review_bundle_verification_summary_tests_text,
            decision_reference_review_bundle_verification_bridge_template_tests_text,
            decision_reference_review_bundle_verification_bridge_template_index_entry_tests_text,
            decision_reference_review_bundle_verification_bridge_template_index_entry_verifier_tests_text,
            decision_reference_review_bundle_verification_bridge_template_index_entry_summary_tests_text,
            decision_reference_review_bundle_verification_bridge_template_index_entry_summary_bridge_template_tests_text,
            decision_reference_review_bundle_verification_bridge_template_index_entry_summary_bridge_template_index_entry_tests_text,
            decision_reference_review_bundle_verification_bridge_template_index_entry_summary_bridge_template_index_entry_verifier_tests_text,
            docs_text,
            manifest_text,
            runbook_text,
            hologram_text,
        )
    ).lower()

    adapter_contract_present = all(
        token in adapter_text
        for token in (
            "MagmaHandoffMetricsAlertmanagerFeed",
            "MagmaHandoffMetricsAlertFeedError",
            "UnavailableMagmaHandoffMetricsAlertFeed",
            "/api/v2/alerts",
            "follow_redirects=False",
            "allowed_private_hosts",
            "CREDENTIAL_HEADER_REFUSED",
            "URL_USERINFO_REFUSED",
            "URL_QUERY_REFUSED",
            "URL_LOCAL_HOST_REFUSED",
            "RESPONSE_TOO_LARGE",
            "RESPONSE_SOURCE_URL_REFUSED",
            "contains_secret_marker_substring",
            "_alertmanager_active_alerts",
            "DEFAULT_CACHE_TTL_SECONDS",
            "DEFAULT_FAILURE_BACKOFF_SECONDS",
            "provider_health",
            "BACKOFF_ACTIVE",
        )
    )
    container_contract_present = all(
        token in container_text
        for token in (
            "magma_share_import_handoff_metrics_alert_feed",
            "magma_handoff_metrics_alert_feed",
            "MagmaHandoffMetricsAlertmanagerFeed.from_config",
            "UnavailableMagmaHandoffMetricsAlertFeed",
            "metrics_alert_state will report unavailable",
        )
    )
    settings_contract_present = all(
        token in settings_text
        for token in (
            "magma_handoff_metrics_alert_feed:",
            "enabled: false",
            "alertmanager_base_url: ''",
            "allowed_private_hosts: []",
            "cache_ttl_s: 30",
            "failure_backoff_s: 30",
            "headers: {}",
        )
    )
    test_contract_present = all(
        token in "\n".join((tests_text, metrics_tests_text))
        for token in (
            "test_magma_handoff_metrics_alertmanager_feed_reads_operator_alerts",
            "test_magma_handoff_metrics_alertmanager_feed_uses_bounded_backoff",
            "test_metrics_body_contains_magma_alert_feed_cache_gauges",
            "test_metrics_magma_alert_feed_backoff_failure_is_sanitized",
            "magma_alert_feed_availability_5m",
            "drill_evidence",
            "test_magma_handoff_metrics_alertmanager_feed_guardrails_refuse_secrets",
            "test_container_wires_configured_magma_handoff_metrics_alert_feed",
            "MagmaHandoffRuntimeAuthorityReported",
            "PRIVATE_ANNOTATION",
            "CREDENTIAL_HEADER_REFUSED",
        )
    )
    docs_contract_present = all(
        token in "\n".join((docs_text, manifest_text, runbook_text))
        for token in (
            "magma_handoff_metrics_alert_feed",
            "/api/v2/alerts",
            "disabled by default",
            "credential",
            "allowed_private_hosts",
            "private or localhost hosts",
            "bounded failure backoff",
            "cache/backoff",
            "slo_panels",
            "drill_evidence",
        )
    )
    cache_backoff_contract_present = all(
        token
        in "\n".join(
            (
                adapter_text,
                ops_text,
                metrics_text,
                settings_text,
                tests_text,
                metrics_tests_text,
                docs_text,
                manifest_text,
                runbook_text,
                hologram_text,
            )
        )
        for token in (
            "cache_ttl_s",
            "failure_backoff_s",
            "cache_hit_count",
            "backoff_skip_count",
            "waggledance_magma_handoff_alert_feed_cache_hits_total",
            "waggledance_magma_handoff_alert_feed_backoff_active",
            "metrics_alert_state",
            "feed_health",
        )
    )
    slo_drill_contract_present = all(
        token
        in "\n".join(
            (
                ops_text,
                metrics_text,
                tests_text,
                docs_text,
                manifest_text,
                runbook_text,
                hologram_text,
            )
        )
        for token in (
            "MAGMA_HANDOFF_METRICS_ALERT_FEED_SLO_PANELS",
            "magma_alert_feed_availability_5m",
            "magma_alert_feed_fetch_failures_15m",
            "drill_evidence",
            "required_artifacts",
            "MAGMA Alert Feed SLOs",
            "MAGMA Alert Drill Evidence",
            "SLO panel templates",
        )
    )
    release_gate_examples_present = all(
        token in "\n".join((docs_text, manifest_text, runbook_text))
        for token in (
            "Manual release-gate examples",
            "Pre-merge MAGMA alert-feed observability gate",
            "avg_over_time(waggledance_magma_handoff_alert_feed_available[5m]) == 1",
            "increase(waggledance_magma_handoff_alert_feed_fetch_failures_total[15m]) == 0",
            "max_over_time(waggledance_magma_handoff_alert_feed_backoff_active[15m]) == 0",
            "waggledance_magma_handoff_runtime_authority_granted == 0",
            "must not auto-merge",
            "documentation-only",
            "operator-owned reviewer handoff summary",
        )
    )
    release_evidence_package_contract_present = all(
        token
        in "\n".join(
            (
                package_text,
                package_tests_text,
                docs_text,
                manifest_text,
                runbook_text,
            )
        )
        for token in (
            "magma_alert_feed_release_evidence.v1",
            "write_magma_alert_feed_release_evidence_package",
            "manual_gate.automatic_release_decision",
            "automatic_release_decision",
            "raw_scrape_included",
            "raw_alertmanager_labels_recorded",
            "no endpoint fetches",
            "test_magma_alert_feed_release_evidence_package_writes_sanitized_artifacts",
            "test_release_evidence_package_cli_json_is_path_free",
            "operator-owned reviewer handoff summary",
        )
    )
    release_evidence_validator_contract_present = all(
        token
        in "\n".join(
            (
                validator_text,
                validator_tests_text,
                docs_text,
                manifest_text,
                runbook_text,
            )
        )
        for token in (
            "validate_magma_alert_feed_release_evidence_package",
            "digest_checks",
            "release_decision_made",
            "external_fetch_performed",
            "transport_added",
            "automatic_release_decision",
            "test_release_evidence_validator_accepts_package_and_digest_inputs",
            "test_release_evidence_validator_cli_json_is_path_free",
            "operator-owned reviewer handoff summary",
        )
    )
    reviewer_handoff_summary_contract_present = all(
        token
        in "\n".join(
            (
                summary_text,
                summary_tests_text,
                docs_text,
                manifest_text,
                runbook_text,
            )
        )
        for token in (
            "magma_alert_feed_reviewer_handoff_summary.v1",
            "build_magma_alert_feed_reviewer_handoff_summary",
            "approval_granted",
            "release_decision_made",
            "automatic_release_decision",
            "external_fetch_performed",
            "transport_added",
            "test_reviewer_handoff_summary_carries_validated_evidence_without_decision",
            "test_reviewer_handoff_summary_cli_json_is_path_free",
            "operator-owned reviewer handoff summary",
        )
    )
    reviewer_bridge_event_template_contract_present = all(
        token
        in "\n".join(
            (
                bridge_template_text,
                bridge_template_tests_text,
                docs_text,
                manifest_text,
                runbook_text,
            )
        )
        for token in (
            "magma_alert_feed_reviewer_bridge_event_template.v1",
            "build_magma_alert_feed_reviewer_bridge_event_template",
            "template_only",
            "direct_bridge_write_performed",
            "approval_granted",
            "release_decision_made",
            "automatic_release_decision",
            "test_reviewer_bridge_event_template_validates_bridge_schema",
            "test_reviewer_bridge_event_template_cli_json_is_path_free",
            "optional bridge-event template",
        )
    )
    reviewer_bridge_event_template_decision_reference_slot_present = all(
        token
        in "\n".join(
            (
                bridge_template_text,
                bridge_template_tests_text,
                docs_text,
                manifest_text,
                runbook_text,
            )
        )
        for token in (
            "operator_decision_ref",
            "decision_reference",
            "decision_reference_is_approval",
            "decision_reference_is_release_decision",
            "test_reviewer_bridge_event_template_rejects_unsafe_decision_reference",
            "operator decision-reference slot",
        )
    )
    reviewer_handoff_bundle_index_contract_present = all(
        token
        in "\n".join(
            (
                bundle_index_text,
                bundle_index_tests_text,
                docs_text,
                manifest_text,
                runbook_text,
            )
        )
        for token in (
            "magma_alert_feed_reviewer_handoff_bundle_index.v1",
            "build_magma_alert_feed_reviewer_handoff_bundle_index",
            "all_artifact_digests_recorded",
            "artifact_payloads_included",
            "local_paths_recorded",
            "test_reviewer_handoff_bundle_index_ties_digests_without_authority",
            "test_reviewer_handoff_bundle_index_cli_json_is_path_free",
            "local reviewer handoff bundle index",
        )
    )
    reviewer_handoff_bundle_verifier_contract_present = all(
        token
        in "\n".join(
            (
                bundle_verifier_text,
                bundle_verifier_tests_text,
                docs_text,
                manifest_text,
                runbook_text,
            )
        )
        for token in (
            "magma_alert_feed_reviewer_handoff_bundle_verification.v1",
            "verify_magma_alert_feed_reviewer_handoff_bundle_index",
            "digest_checks",
            "schema_version_checks",
            "test_reviewer_handoff_bundle_verifier_recomputes_digests_without_authority",
            "test_reviewer_handoff_bundle_verifier_cli_json_is_path_free",
            "local reviewer handoff bundle verifier",
        )
    )
    reviewer_handoff_bundle_verification_summary_contract_present = all(
        token
        in "\n".join(
            (
                bundle_verification_summary_text,
                bundle_verification_summary_tests_text,
                docs_text,
                manifest_text,
                runbook_text,
            )
        )
        for token in (
            "magma_alert_feed_reviewer_handoff_bundle_verification_summary.v1",
            "build_magma_alert_feed_reviewer_handoff_bundle_verification_summary",
            "verification_ok",
            "verification_report_boundary_ok",
            "test_reviewer_handoff_bundle_verification_summary_renders_verifier_result_without_authority",
            "test_reviewer_handoff_bundle_verification_summary_cli_json_is_path_free",
            "local reviewer handoff bundle verification summary",
        )
    )
    reviewer_handoff_bundle_operator_decision_reference_validator_contract_present = all(
        token
        in "\n".join(
            (
                decision_reference_validator_text,
                decision_reference_validator_tests_text,
                docs_text,
                manifest_text,
                runbook_text,
            )
        )
        for token in (
            "magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference_validation.v1",
            "validate_magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference",
            "decision_reference_validated",
            "decision_reference_is_approval",
            "decision_reference_is_release_decision",
            "test_operator_decision_reference_validator_accepts_context_reference_without_approval",
            "test_operator_decision_reference_validator_cli_json_is_path_free",
            "local operator decision-reference validator",
        )
    )
    reviewer_handoff_bundle_operator_decision_reference_review_summary_contract_present = all(
        token
        in "\n".join(
            (
                decision_reference_review_summary_text,
                decision_reference_review_summary_tests_text,
                docs_text,
                manifest_text,
                runbook_text,
            )
        )
        for token in (
            "magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference_review_summary.v1",
            "build_magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference_review_summary",
            "decision_reference_validated",
            "review_operator_decision_reference_context",
            "record_operator_decision_separately",
            "test_operator_decision_reference_review_summary_renders_validation_without_approval",
            "test_operator_decision_reference_review_summary_cli_json_is_path_free",
            "local operator decision-reference review summary",
        )
    )
    reviewer_handoff_bundle_operator_decision_reference_review_bundle_index_contract_present = all(
        token
        in "\n".join(
            (
                decision_reference_review_bundle_index_text,
                decision_reference_review_bundle_index_tests_text,
                docs_text,
                manifest_text,
                runbook_text,
            )
        )
        for token in (
            "magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference_review_bundle_index.v1",
            "build_magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference_review_bundle_index",
            "operator_decision_reference_validation",
            "operator_decision_reference_review_summary",
            "all_artifact_digests_recorded",
            "artifact_payloads_included",
            "local_paths_recorded",
            "transport_added",
            "direct_bridge_write_performed",
            "approval_granted",
            "release_decision_made",
            "runtime_controls_added",
            "review_operator_decision_reference_review_bundle_index",
            "test_operator_decision_reference_review_bundle_index_ties_digests_without_authority",
            "test_operator_decision_reference_review_bundle_index_cli_json_is_path_free",
            "local operator decision-reference review bundle index",
        )
    )
    reviewer_handoff_bundle_operator_decision_reference_review_bundle_verifier_contract_present = all(
        token
        in "\n".join(
            (
                decision_reference_review_bundle_verifier_text,
                decision_reference_review_bundle_verifier_tests_text,
                docs_text,
                manifest_text,
                runbook_text,
            )
        )
        for token in (
            "magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference_review_bundle_verification.v1",
            "verify_magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference_review_bundle_index",
            "operator_decision_reference_review_bundle_verification",
            "digest_checks",
            "schema_version_checks",
            "artifact_payloads_included",
            "local_paths_recorded",
            "transport_added",
            "direct_bridge_write_performed",
            "approval_granted",
            "release_decision_made",
            "runtime_controls_added",
            "test_operator_decision_reference_review_bundle_verifier_recomputes_digests_without_authority",
            "test_operator_decision_reference_review_bundle_verifier_cli_json_is_path_free",
            "local operator decision-reference review bundle verifier",
        )
    )
    reviewer_handoff_bundle_operator_decision_reference_review_bundle_verification_summary_contract_present = all(
        token
        in "\n".join(
            (
                decision_reference_review_bundle_verification_summary_text,
                decision_reference_review_bundle_verification_summary_tests_text,
                docs_text,
                manifest_text,
                runbook_text,
            )
        )
        for token in (
            "magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference_review_bundle_verification_summary.v1",
            "build_magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference_review_bundle_verification_summary",
            "operator_decision_reference_review_bundle_verification",
            "source_contract_check",
            "rebuilt_index_check",
            "decision_reference_verified",
            "artifact_payloads_included",
            "local_paths_recorded",
            "transport_added",
            "direct_bridge_write_performed",
            "approval_granted",
            "release_decision_made",
            "runtime_controls_added",
            "test_operator_decision_reference_review_bundle_verification_summary_renders_verifier_result_without_authority",
            "test_operator_decision_reference_review_bundle_verification_summary_cli_json_is_path_free",
            "local operator decision-reference review bundle verification summary",
        )
    )
    reviewer_handoff_bundle_operator_decision_reference_review_bundle_verification_bridge_event_template_contract_present = all(
        token
        in "\n".join(
            (
                decision_reference_review_bundle_verification_bridge_template_text,
                decision_reference_review_bundle_verification_bridge_template_tests_text,
                docs_text,
                manifest_text,
                runbook_text,
            )
        )
        for token in (
            "magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference_review_bundle_verification_bridge_event_template.v1",
            "build_magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference_review_bundle_verification_bridge_event_template",
            "operator_decision_reference_review_bundle_verification",
            "source_contract_check",
            "rebuilt_index_check",
            "decision_reference_verified",
            "template_only",
            "artifact_payloads_included",
            "local_paths_recorded",
            "transport_added",
            "direct_bridge_write_performed",
            "approval_granted",
            "release_decision_made",
            "runtime_controls_added",
            "test_operator_decision_reference_review_bundle_verification_bridge_event_template_validates_bridge_schema",
            "test_operator_decision_reference_review_bundle_verification_bridge_event_template_cli_json_is_path_free",
            "local operator decision-reference review bundle verification bridge-event template",
        )
    )
    reviewer_handoff_bundle_operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry_contract_present = all(
        token
        in "\n".join(
            (
                decision_reference_review_bundle_verification_bridge_template_index_entry_text,
                decision_reference_review_bundle_verification_bridge_template_index_entry_tests_text,
                docs_text,
                manifest_text,
                runbook_text,
            )
        )
        for token in (
            "magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry.v1",
            "build_magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry",
            "operator_decision_reference_review_bundle_verification_bridge_event_template",
            "template_index_entry",
            "bridge_event_schema_validated",
            "source_contract_check",
            "rebuilt_template_check",
            "template_only",
            "artifact_payloads_included",
            "local_paths_recorded",
            "transport_added",
            "direct_bridge_write_performed",
            "approval_granted",
            "release_decision_made",
            "runtime_controls_added",
            "test_operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry_ties_digests_without_authority",
            "test_operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry_cli_json_is_path_free",
            "local operator decision-reference review bundle verification bridge-event template index entry",
        )
    )
    reviewer_handoff_bundle_operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry_verifier_contract_present = all(
        token
        in "\n".join(
            (
                decision_reference_review_bundle_verification_bridge_template_index_entry_verifier_text,
                decision_reference_review_bundle_verification_bridge_template_index_entry_verifier_tests_text,
                docs_text,
                manifest_text,
                runbook_text,
            )
        )
        for token in (
            "magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry_verification.v1",
            "verify_magma_decision_review_verification_template_index_entry",
            "operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry",
            "digest_checks",
            "schema_version_checks",
            "source_contract_check",
            "rebuilt_index_entry_check",
            "bridge_event_schema_check",
            "template_only",
            "artifact_payloads_included",
            "local_paths_recorded",
            "transport_added",
            "direct_bridge_write_performed",
            "approval_granted",
            "release_decision_made",
            "runtime_controls_added",
            "test_operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry_verifier_recomputes_digests_without_authority",
            "test_operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry_verifier_cli_json_is_path_free",
            "local operator decision-reference review bundle verification bridge-event template index-entry verifier",
        )
    )
    reviewer_handoff_bundle_operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry_verification_summary_contract_present = all(
        token
        in "\n".join(
            (
                decision_reference_review_bundle_verification_bridge_template_index_entry_summary_text,
                decision_reference_review_bundle_verification_bridge_template_index_entry_summary_tests_text,
                docs_text,
                manifest_text,
                runbook_text,
            )
        )
        for token in (
            "magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry_verification_summary.v1",
            "build_magma_decision_review_verification_template_index_entry_summary",
            "operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry_verification",
            "source_contract_check",
            "rebuilt_index_entry_check",
            "bridge_event_schema_check",
            "decision_reference_verified",
            "template_only",
            "artifact_payloads_included",
            "local_paths_recorded",
            "transport_added",
            "direct_bridge_write_performed",
            "approval_granted",
            "release_decision_made",
            "runtime_controls_added",
            "test_operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry_summary_renders_verifier_result_without_authority",
            "test_operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry_summary_cli_json_is_path_free",
            "local operator decision-reference review bundle verification bridge-event template index-entry verification summary",
        )
    )
    reviewer_handoff_bundle_operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry_verification_summary_bridge_event_template_contract_present = all(
        token
        in "\n".join(
            (
                decision_reference_review_bundle_verification_bridge_template_index_entry_summary_bridge_template_text,
                decision_reference_review_bundle_verification_bridge_template_index_entry_summary_bridge_template_tests_text,
                docs_text,
                manifest_text,
                runbook_text,
            )
        )
        for token in (
            "magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry_verification_summary_bridge_event_template.v1",
            "build_magma_decision_review_verification_template_index_entry_summary_bridge_event_template",
            "operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry_verification",
            "source_contract_check",
            "rebuilt_index_entry_check",
            "bridge_event_schema_check",
            "decision_reference_verified",
            "template_only",
            "artifact_payloads_included",
            "local_paths_recorded",
            "transport_added",
            "direct_bridge_write_performed",
            "approval_granted",
            "release_decision_made",
            "runtime_controls_added",
            "test_operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry_summary_bridge_event_template_validates_bridge_schema",
            "test_operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry_summary_bridge_event_template_cli_json_is_path_free",
            "local operator decision-reference review bundle verification bridge-event template index-entry verification summary bridge-event template",
        )
    )
    reviewer_handoff_bundle_operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry_contract_present = all(
        token
        in "\n".join(
            (
                decision_reference_review_bundle_verification_bridge_template_index_entry_summary_bridge_template_index_entry_text,
                decision_reference_review_bundle_verification_bridge_template_index_entry_summary_bridge_template_index_entry_tests_text,
                docs_text,
                manifest_text,
                runbook_text,
            )
        )
        for token in (
            "magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry.v1",
            "build_magma_decision_review_verification_template_index_entry_summary_bridge_event_template_index_entry",
            "operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry_verification_summary_bridge_event_template",
            "template_index_entry",
            "bridge_event_schema_validated",
            "source_contract_check",
            "rebuilt_template_check",
            "template_only",
            "artifact_payloads_included",
            "local_paths_recorded",
            "transport_added",
            "direct_bridge_write_performed",
            "approval_granted",
            "release_decision_made",
            "runtime_controls_added",
            "test_operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry_summary_bridge_event_template_index_entry_ties_digests_without_authority",
            "test_operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry_summary_bridge_event_template_index_entry_cli_json_is_path_free",
            "local operator decision-reference review bundle verification bridge-event template index-entry verification summary bridge-event template index entry",
        )
    )
    reviewer_handoff_bundle_operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry_verifier_contract_present = all(
        token
        in "\n".join(
            (
                decision_reference_review_bundle_verification_bridge_template_index_entry_summary_bridge_template_index_entry_verifier_text,
                decision_reference_review_bundle_verification_bridge_template_index_entry_summary_bridge_template_index_entry_verifier_tests_text,
                docs_text,
                manifest_text,
                runbook_text,
            )
        )
        for token in (
            "magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry_verification.v1",
            "verify_magma_decision_review_verification_template_index_entry_summary_bridge_event_template_index_entry",
            "operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry_verification_summary_bridge_event_template",
            "source_contract_check",
            "rebuilt_index_entry_check",
            "bridge_event_schema_check",
            "digest_checks",
            "size_checks",
            "schema_version_checks",
            "artifact_payloads_included",
            "local_paths_recorded",
            "transport_added",
            "direct_bridge_write_performed",
            "approval_granted",
            "release_decision_made",
            "runtime_controls_added",
            "test_operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry_summary_bridge_event_template_index_entry_verifier_recomputes_digests_without_authority",
            "test_operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry_summary_bridge_event_template_index_entry_verifier_cli_json_is_path_free",
            "local verifier for the operator decision-reference review bundle verification bridge-event template index-entry verification summary bridge-event template index entry",
        )
    )
    guardrails_present = all(
        token in adapter_text
        for token in (
            "URL_USERINFO_REFUSED",
            "URL_QUERY_REFUSED",
            "URL_SECRET_REFUSED",
            "URL_PRIVATE_HOST_REFUSED",
            "URL_LOCAL_HOST_REFUSED",
            "CREDENTIAL_HEADER_REFUSED",
            "HEADER_CONTROL_REFUSED",
            "TIMEOUT_OUT_OF_RANGE",
            "SIZE_CAP_OUT_OF_RANGE",
        )
    )
    forbidden_control_tokens = {
        "import_payload",
        "payload_import",
        "grant_runtime_authority",
        "authority_mutation",
        "write_config",
        "config_write",
        "auto_import",
        "magma_handoff_metrics_alert_feed_import",
        "magma_handoff_metrics_alert_feed_start",
        "magma_handoff_metrics_alert_feed_stop",
    }
    forbidden_control_tokens_found = [
        token
        for token in sorted(forbidden_control_tokens)
        if token.lower() in combined_runtime_lower
    ]
    ok = (
        adapter_contract_present
        and container_contract_present
        and settings_contract_present
        and test_contract_present
        and docs_contract_present
        and cache_backoff_contract_present
        and slo_drill_contract_present
        and release_gate_examples_present
        and release_evidence_package_contract_present
        and release_evidence_validator_contract_present
        and reviewer_handoff_summary_contract_present
        and reviewer_bridge_event_template_contract_present
        and reviewer_bridge_event_template_decision_reference_slot_present
        and reviewer_handoff_bundle_index_contract_present
        and reviewer_handoff_bundle_verifier_contract_present
        and reviewer_handoff_bundle_verification_summary_contract_present
        and reviewer_handoff_bundle_operator_decision_reference_validator_contract_present
        and reviewer_handoff_bundle_operator_decision_reference_review_summary_contract_present
        and reviewer_handoff_bundle_operator_decision_reference_review_bundle_index_contract_present
        and reviewer_handoff_bundle_operator_decision_reference_review_bundle_verifier_contract_present
        and reviewer_handoff_bundle_operator_decision_reference_review_bundle_verification_summary_contract_present
        and reviewer_handoff_bundle_operator_decision_reference_review_bundle_verification_bridge_event_template_contract_present
        and reviewer_handoff_bundle_operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry_contract_present
        and reviewer_handoff_bundle_operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry_verifier_contract_present
        and reviewer_handoff_bundle_operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry_verification_summary_contract_present
        and reviewer_handoff_bundle_operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry_verification_summary_bridge_event_template_contract_present
        and reviewer_handoff_bundle_operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry_contract_present
        and reviewer_handoff_bundle_operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry_verifier_contract_present
        and guardrails_present
        and not forbidden_control_tokens_found
    )
    return {
        "proof_id": "magma_handoff_metrics_alertmanager_adapter_smoke_v1",
        "ok": ok,
        "proof_mode": "source_contract",
        "adapter_path": adapter_rel,
        "settings_path": settings_rel,
        "ops_endpoint": "/api/ops",
        "adapter_contract_present": adapter_contract_present,
        "container_contract_present": container_contract_present,
        "settings_contract_present": settings_contract_present,
        "test_contract_present": test_contract_present,
        "docs_contract_present": docs_contract_present,
        "cache_backoff_contract_present": cache_backoff_contract_present,
        "slo_drill_contract_present": slo_drill_contract_present,
        "release_gate_examples_present": release_gate_examples_present,
        "release_evidence_package_contract_present": (
            release_evidence_package_contract_present
        ),
        "release_evidence_validator_contract_present": (
            release_evidence_validator_contract_present
        ),
        "reviewer_handoff_summary_contract_present": (
            reviewer_handoff_summary_contract_present
        ),
        "reviewer_bridge_event_template_contract_present": (
            reviewer_bridge_event_template_contract_present
        ),
        "reviewer_bridge_event_template_decision_reference_slot_present": (
            reviewer_bridge_event_template_decision_reference_slot_present
        ),
        "reviewer_handoff_bundle_index_contract_present": (
            reviewer_handoff_bundle_index_contract_present
        ),
        "reviewer_handoff_bundle_verifier_contract_present": (
            reviewer_handoff_bundle_verifier_contract_present
        ),
        "reviewer_handoff_bundle_verification_summary_contract_present": (
            reviewer_handoff_bundle_verification_summary_contract_present
        ),
        "reviewer_handoff_bundle_operator_decision_reference_validator_contract_present": (
            reviewer_handoff_bundle_operator_decision_reference_validator_contract_present
        ),
        "reviewer_handoff_bundle_operator_decision_reference_review_summary_contract_present": (
            reviewer_handoff_bundle_operator_decision_reference_review_summary_contract_present
        ),
        "reviewer_handoff_bundle_operator_decision_reference_review_bundle_index_contract_present": (
            reviewer_handoff_bundle_operator_decision_reference_review_bundle_index_contract_present
        ),
        "reviewer_handoff_bundle_operator_decision_reference_review_bundle_verifier_contract_present": (
            reviewer_handoff_bundle_operator_decision_reference_review_bundle_verifier_contract_present
        ),
        "reviewer_handoff_bundle_operator_decision_reference_review_bundle_verification_summary_contract_present": (
            reviewer_handoff_bundle_operator_decision_reference_review_bundle_verification_summary_contract_present
        ),
        "reviewer_handoff_bundle_operator_decision_reference_review_bundle_verification_bridge_event_template_contract_present": (
            reviewer_handoff_bundle_operator_decision_reference_review_bundle_verification_bridge_event_template_contract_present
        ),
        "reviewer_handoff_bundle_operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry_contract_present": (
            reviewer_handoff_bundle_operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry_contract_present
        ),
        "reviewer_handoff_bundle_operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry_verifier_contract_present": (
            reviewer_handoff_bundle_operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry_verifier_contract_present
        ),
        "reviewer_handoff_bundle_operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry_verification_summary_contract_present": (
            reviewer_handoff_bundle_operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry_verification_summary_contract_present
        ),
        "reviewer_handoff_bundle_operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry_verification_summary_bridge_event_template_contract_present": (
            reviewer_handoff_bundle_operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry_verification_summary_bridge_event_template_contract_present
        ),
        "reviewer_handoff_bundle_operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry_contract_present": (
            reviewer_handoff_bundle_operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry_contract_present
        ),
        "reviewer_handoff_bundle_operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry_verifier_contract_present": (
            reviewer_handoff_bundle_operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry_verifier_contract_present
        ),
        "guardrails_present": guardrails_present,
        "forbidden_controls_absent": not forbidden_control_tokens_found,
        "forbidden_control_tokens_found": forbidden_control_tokens_found,
        "runtime_authority_changed": False,
        "operator_gate_required": False,
        "external_writes_applied": False,
        "safe_conclusion": (
            "The MAGMA handoff metrics Alertmanager adapter is disabled by "
            "default, read-only, and wired into /api/ops provider health. It "
            "uses bounded GETs to /api/v2/alerts and refuses credential, "
            "query, redirect, oversized response, and non-allowlisted "
            "private-host shapes. Its TTL cache and bounded failure backoff "
            "surface only sanitized provider-health metrics and read-only "
            "SLO/drill-evidence templates. The runbook adds manual "
            "release-gate examples that consume that evidence without adding "
            "merge, promotion, configuration, importer/exporter, or runtime "
            "controls. The local evidence package tool records only explicit "
            "operator-provided /api/ops and /metrics evidence for manual "
            "review; it fetches no endpoints and makes no automatic release "
            "decision. The validator checks package structure and optional "
            "local artifact digests for reviewers without writes, transport, "
            "endpoint fetches, or release decisions. The reviewer handoff "
            "summary renders validated evidence context without approval, "
            "release-decision automation, transport, endpoint fetches, or "
            "runtime controls. The optional bridge-event template validates "
            "as handoff-shaped JSON but does not append bridge events or "
            "grant approval, release authority, transport, endpoint fetches, "
            "or runtime controls; its optional operator decision-reference "
            "slot is context only and is explicitly not approval or a "
            "release decision. The local reviewer handoff bundle index ties "
            "the package, validation report, summary, and bridge-event "
            "template digests without including payloads, recording paths, "
            "transporting artifacts, or making approval/release decisions."
            " The local reviewer handoff bundle verifier recomputes those "
            "digests and schema versions from explicit local files while "
            "keeping payload inclusion, path recording, transport, approval, "
            "release decisions, and runtime controls disabled. The local "
            "reviewer handoff bundle verification summary renders that "
            "verifier result into path-free reviewer context while preserving "
            "the same no-approval, no-transport, no-bridge-write, no-payload, "
            "no-local-path, and no-runtime-control boundary. The local "
            "operator decision-reference validator checks the bundle's "
            "bridge-event template reference against the verified bundle "
            "summary while keeping that reference context-only, not approval "
            "or a release decision. The local operator decision-reference "
            "review summary renders that validator result into path-free "
            "reviewer context while keeping the operator decision separate "
            "and preserving the same no-approval, no-release-decision, "
            "no-bridge-write, no-transport, no-payload, no-local-path, and "
            "no-runtime-control boundary. The local operator "
            "decision-reference review bundle index ties the validation "
            "result and review summary digests without including payloads, "
            "recording paths, transport, bridge writes, approval, release "
            "decisions, or runtime controls. The local operator "
            "decision-reference review bundle verifier recomputes those "
            "artifact checks without including payloads, recording paths, "
            "transport, bridge writes, approval, release decisions, or "
            "runtime controls. The local operator decision-reference review "
            "bundle verification summary renders the verifier result as "
            "path-free reviewer context with source-contract, rebuilt-index, "
            "and decision-reference verification checks while preserving the "
            "same no-authority boundary. The local operator "
            "decision-reference review bundle verification bridge-event "
            "template renders that verified summary as schema-valid handoff "
            "JSON without appending bridge events, including payloads, "
            "recording paths, transporting artifacts, approving, promoting, "
            "or changing runtime controls. The local operator "
            "decision-reference review bundle verification bridge-event "
            "template index-entry verification summary bridge-event template "
            "index entry ties that summary and bridge-event template report "
            "with digest and rebuilt-template checks while preserving the "
            "same no-payload, no-local-path, no-transport, no-bridge-write, "
            "no-approval, no-release-decision, and no-runtime-control "
            "boundary. The local verifier for that index entry recomputes "
            "digest, size, schema, source-contract, rebuilt-entry, and "
            "bridge-event-schema checks from explicit local files while "
            "preserving the same no-authority boundary."
        ),
    }


def _blocked_low_risk_autogrowth_ops_alert_state_smoke(
    *,
    missing_inputs: Sequence[str],
    blocked_reason: str = "missing_required_inputs",
) -> dict:
    return {
        "proof_id": "low_risk_autogrowth_ops_alert_state_smoke_v1",
        "ok": False,
        "blocked_reason": blocked_reason,
        "missing_inputs": list(missing_inputs),
        "ops_endpoint": "/api/ops",
        "dashboard_path": "web/hologram-brain-v6.html",
        "alert_state_visible": False,
        "local_snapshot_source": False,
        "rate_rules_deferred": False,
        "forbidden_controls_absent": False,
        "forbidden_control_tokens_found": [],
        "runtime_authority_changed": False,
        "operator_gate_required": False,
        "external_writes_applied": False,
        "safe_conclusion": (
            "Required Ops API or dashboard inputs are missing, so the "
            "read-only autogrowth alert state cannot be certified."
        ),
    }


def build_low_risk_autogrowth_ops_alert_state_smoke(
    root: Path | str = ROOT,
) -> dict:
    """Prove the Ops dashboard exposes read-only autogrowth alert state."""

    repo_root = Path(root)
    api_rel = "waggledance/adapters/http/routes/compat_dashboard.py"
    html_rel = "web/hologram-brain-v6.html"
    tests_rel = "tests/test_legacy_consolidation.py"
    docs_rel = "docs/API.md"
    required = (api_rel, html_rel, tests_rel, docs_rel)
    missing = [rel_path for rel_path in required if not (repo_root / rel_path).exists()]
    if missing:
        return _blocked_low_risk_autogrowth_ops_alert_state_smoke(
            missing_inputs=missing,
        )

    api_text = (repo_root / api_rel).read_text(encoding="utf-8")
    html_text = (repo_root / html_rel).read_text(encoding="utf-8")
    tests_text = (repo_root / tests_rel).read_text(encoding="utf-8")
    docs_text = (repo_root / docs_rel).read_text(encoding="utf-8")
    combined_runtime_lower = "\n".join((api_text, html_text, docs_text)).lower()

    api_contract_present = all(
        token in api_text
        for token in (
            '"alert_state"',
            '"local_ops_snapshot"',
            '"prometheus_alertmanager_feed"',
            '"deferred_rules"',
            '"controls_present"',
            "AutogrowthSourceDown",
            "AutogrowthErrorsObserved",
        )
    )
    ui_contract_present = all(
        token in html_text
        for token in (
            "ag.alert_state",
            "activeAutogrowthAlerts",
            "Autogrowth Alerts",
        )
    )
    test_contract_present = all(
        token in tests_text
        for token in (
            "test_ops_autogrowth_alert_state_reports_errors_without_details",
            "AutogrowthErrorsObserved",
            "private stack trace",
            "activeAutogrowthAlerts",
        )
    )
    docs_contract_present = (
        "autogrowth.alert_state" in docs_text
        and 'source="local_ops_snapshot"' in docs_text
        and "Prometheus/Alertmanager" in docs_text
        and "does not add mutating endpoints" in docs_text
    )
    rate_rules_deferred = all(
        token in api_text
        for token in (
            "AutogrowthErrorBurst",
            "AutogrowthWakeupStalled",
            "AutogrowthWakeupBurst",
            "AutogrowthNonIdleBurst",
        )
    )
    forbidden_control_tokens = {
        "POST /api/autogrowth",
        "/api/autogrowth/start",
        "/api/autogrowth/stop",
        "autogrowth_start",
        "autogrowth_stop",
        "start_button",
        "stop_button",
        "config_write",
        "write_config",
    }
    forbidden_control_tokens_found = [
        token
        for token in sorted(forbidden_control_tokens)
        if token.lower() in combined_runtime_lower
    ]
    ok = (
        api_contract_present
        and ui_contract_present
        and test_contract_present
        and docs_contract_present
        and rate_rules_deferred
        and not forbidden_control_tokens_found
    )
    return {
        "proof_id": "low_risk_autogrowth_ops_alert_state_smoke_v1",
        "ok": ok,
        "proof_mode": "source_contract",
        "ops_endpoint": "/api/ops",
        "dashboard_path": html_rel,
        "api_contract_present": api_contract_present,
        "ui_contract_present": ui_contract_present,
        "test_contract_present": test_contract_present,
        "docs_contract_present": docs_contract_present,
        "alert_state_visible": ui_contract_present,
        "local_snapshot_source": '"local_ops_snapshot"' in api_text,
        "rate_rules_deferred": rate_rules_deferred,
        "forbidden_controls_absent": not forbidden_control_tokens_found,
        "forbidden_control_tokens_found": forbidden_control_tokens_found,
        "runtime_authority_changed": False,
        "operator_gate_required": False,
        "external_writes_applied": False,
        "safe_conclusion": (
            "The Ops API and hologram dashboard expose a read-only local "
            "autogrowth alert snapshot. Time-window alert rules remain "
            "deferred to Prometheus/Alertmanager data and no controls or "
            "runtime growth authority are added."
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


_FUTURE_SCALE_AXES: tuple[dict[str, str], ...] = (
    {
        "axis_id": "coverage",
        "image_phrase": "emergent intelligence",
        "proxy": "coverage",
        "current_status": "proxy_defined",
        "claim_gate": "Needs a versioned coverage corpus and pass/fail trend.",
    },
    {
        "axis_id": "llm_fallback_rate",
        "image_phrase": "industrial-grade efficiency",
        "proxy": "LLM fallback rate",
        "current_status": "proxy_defined",
        "claim_gate": "Needs runtime fallback-rate export by route and profile.",
    },
    {
        "axis_id": "route_depth",
        "image_phrase": "emergent intelligence",
        "proxy": "route depth",
        "current_status": "proxy_defined",
        "claim_gate": "Needs route-depth histograms from sanitized traces.",
    },
    {
        "axis_id": "useful_composite_paths",
        "image_phrase": "emergent intelligence",
        "proxy": "useful composite paths",
        "current_status": "proxy_defined",
        "claim_gate": "Needs replayable composite-path usefulness receipts.",
    },
    {
        "axis_id": "contradiction_rate",
        "image_phrase": "emergent intelligence",
        "proxy": "contradiction rate",
        "current_status": "proxy_defined",
        "claim_gate": "Needs verifier-backed contradiction-rate reporting.",
    },
    {
        "axis_id": "insight_score",
        "image_phrase": "emergent intelligence",
        "proxy": "insight score",
        "current_status": "proxy_defined",
        "claim_gate": "Needs a scored insight rubric and reproducible corpus.",
    },
    {
        "axis_id": "latency",
        "image_phrase": "industrial-grade efficiency",
        "proxy": "latency",
        "current_status": "proxy_defined",
        "claim_gate": "Needs p50/p95/p99 latency baselines under load.",
    },
    {
        "axis_id": "audit_completeness",
        "image_phrase": "industrial-grade efficiency",
        "proxy": "audit completeness",
        "current_status": "proxy_defined",
        "claim_gate": "Needs route and solver trace receipt coverage metrics.",
    },
)


def _blocked_future_scale_axis_scorecard(
    *,
    missing_inputs: Sequence[str],
    blocked_reason: str = "missing_required_inputs",
    inspected_root: str | None = None,
    import_root: str | None = None,
) -> dict:
    proof = {
        "proof_id": "future_scale_axis_scorecard_v1",
        "ok": False,
        "blocked_reason": blocked_reason,
        "missing_inputs": list(missing_inputs),
        "literal_future_claim_safe": False,
        "unbounded_claims_rejected": True,
        "axes": [],
        "claim_decomposition": [],
        "runtime_authority_changed": False,
        "operator_gate_required": False,
        "external_writes_applied": False,
        "safe_conclusion": (
            "Required future-scale scorecard inputs are missing, so the "
            "future swarm claim remains a target with no local proof."
        ),
    }
    if blocked_reason == "non_current_import_root":
        proof["safe_conclusion"] = (
            "The inspected root is not the manifest tool's current import "
            "root, so the future scorecard blocks instead of certifying one "
            "checkout with constants imported from another checkout."
        )
    if inspected_root is not None:
        proof["inspected_root"] = inspected_root
    if import_root is not None:
        proof["import_root"] = import_root
    return proof


def build_future_scale_axis_scorecard(root: Path | str = ROOT) -> dict:
    """Gate future swarm wording through measurable axes."""

    repo_root = Path(root)
    required = (
        "docs/architecture/explosive_intelligence_growth_2.md",
        "docs/architecture/HONEYCOMB_SOLVER_SCALING.md",
        "docs/architecture/WD_IMAGE1_FUNCTIONALITY_MANIFEST.md",
    )
    missing = [rel_path for rel_path in required if not (repo_root / rel_path).exists()]
    if missing:
        return _blocked_future_scale_axis_scorecard(
            missing_inputs=missing,
        )

    resolved_repo_root = repo_root.resolve()
    resolved_import_root = ROOT.resolve()
    if resolved_repo_root != resolved_import_root:
        return _blocked_future_scale_axis_scorecard(
            missing_inputs=[],
            blocked_reason="non_current_import_root",
            inspected_root=str(resolved_repo_root),
            import_root=str(resolved_import_root),
        )

    eig_text = (
        repo_root / "docs/architecture/explosive_intelligence_growth_2.md"
    ).read_text(encoding="utf-8")
    honeycomb_text = (
        repo_root / "docs/architecture/HONEYCOMB_SOLVER_SCALING.md"
    ).read_text(encoding="utf-8")
    manifest_text = (
        repo_root / "docs/architecture/WD_IMAGE1_FUNCTIONALITY_MANIFEST.md"
    ).read_text(encoding="utf-8")
    honeycomb_lower = honeycomb_text.lower()
    eig_lower = eig_text.lower()

    axes = []
    for axis in _FUTURE_SCALE_AXES:
        proxy = axis["proxy"]
        axes.append(
            {
                "axis_id": axis["axis_id"],
                "image_phrase": axis["image_phrase"],
                "proxy": proxy,
                "current_status": axis["current_status"],
                "proxy_named_in_scoreboard_doc": proxy.lower() in honeycomb_lower,
                "source_path": "docs/architecture/HONEYCOMB_SOLVER_SCALING.md",
                "claim_gate": axis["claim_gate"],
                "literal_claim_safe": False,
            }
        )

    claim_decomposition = [
        {
            "image_phrase": "emergent intelligence",
            "literal_claim_safe": False,
            "axis_ids": [
                "coverage",
                "route_depth",
                "useful_composite_paths",
                "contradiction_rate",
                "insight_score",
                "audit_completeness",
            ],
            "safe_replacement": (
                "measured solver coverage, route depth, composite paths, "
                "contradiction rate, insight score, and audit completeness"
            ),
        },
        {
            "image_phrase": "infinite scalability",
            "literal_claim_safe": False,
            "axis_ids": [
                "coverage",
                "llm_fallback_rate",
                "latency",
                "audit_completeness",
            ],
            "safe_replacement": (
                "bounded scale targets with benchmark-only simulation before "
                "runtime activation"
            ),
        },
        {
            "image_phrase": "industrial-grade efficiency",
            "literal_claim_safe": False,
            "axis_ids": [
                "llm_fallback_rate",
                "latency",
                "audit_completeness",
            ],
            "safe_replacement": (
                "reported fallback, latency, and audit-completeness metrics"
            ),
        },
    ]

    eig_disabled_by_default = "enabled: false" in eig_lower
    eig_benchmark_only = (
        "| m6 | benchmarks and scale simulation | benchmark-only |" in eig_lower
    )
    scorecard_doc_present = "future scale-axis scorecard" in (manifest_text.lower())
    all_axis_proxies_named = all(axis["proxy_named_in_scoreboard_doc"] for axis in axes)
    ok = (
        all_axis_proxies_named
        and eig_disabled_by_default
        and eig_benchmark_only
        and scorecard_doc_present
    )
    return {
        "proof_id": "future_scale_axis_scorecard_v1",
        "ok": ok,
        "literal_future_claim_safe": False,
        "unbounded_claims_rejected": True,
        "axis_count": len(axes),
        "defined_axis_count": sum(
            1 for axis in axes if axis["proxy_named_in_scoreboard_doc"]
        ),
        "all_axis_proxies_named": all_axis_proxies_named,
        "eig_disabled_by_default": eig_disabled_by_default,
        "eig_benchmark_only": eig_benchmark_only,
        "scorecard_doc_present": scorecard_doc_present,
        "axes": axes,
        "claim_decomposition": claim_decomposition,
        "runtime_authority_changed": False,
        "operator_gate_required": False,
        "external_writes_applied": False,
        "safe_conclusion": (
            "The future swarm wording is decomposed into measurable scale "
            "axes. The literal claims for emergent intelligence, infinite "
            "scalability, and industrial-grade efficiency remain unsafe until "
            "those axes have versioned metrics and proof artifacts."
        ),
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
            (
                "waggledance/adapters/http/routes/chat.py",
                "Privacy-safe HTTP/WS route-stage trace boundary.",
            ),
            (
                "waggledance/adapters/http/routes/metrics.py",
                "Prometheus route-stage counters and latency histogram.",
            ),
            (
                "waggledance/adapters/http/routes/compat_dashboard.py",
                "Ops API exposes read-only route-stage latency panel templates.",
            ),
            (
                "waggledance/adapters/http/route_stage_latency_feed.py",
                "Optional operator-owned Prometheus/Alertmanager feed provider.",
            ),
            (
                "web/hologram-brain-v6.html",
                "Dashboard chat route-stage labels and Ops latency panels.",
            ),
            (
                "docs/operations/ROUTE_STAGE_LATENCY_RUNBOOK.md",
                "Operator p95/p99 latency panel and alert thresholds.",
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
                "waggledance/core/magma/share_manifest.py",
                "Operator-gated exporter, no-authority importer, and bounded peer-review handoff summary helpers validate the no-payload contract.",
            ),
            (
                "tools/export_magma_share_manifest.py",
                "Explicit CLI writes payload-free share manifests from verified receipt bundles.",
            ),
            (
                "tools/import_magma_share_manifest.py",
                "Explicit CLI verifies fresh share manifests and can write no-authority peer-review handoffs.",
            ),
            (
                "tools/package_magma_alert_feed_release_evidence.py",
                "Explicit CLI packages sanitized MAGMA alert-feed release evidence from operator-provided local files.",
            ),
            (
                "tools/validate_magma_alert_feed_release_evidence.py",
                "Explicit CLI validates packaged MAGMA alert-feed release evidence and optional local artifact digests.",
            ),
            (
                "tools/build_magma_alert_feed_reviewer_handoff_summary.py",
                "Explicit CLI renders sanitized reviewer handoff context from a local evidence package and local validation report.",
            ),
            (
                "tools/build_magma_alert_feed_reviewer_bridge_event_template.py",
                "Explicit CLI renders a sanitized bridge-event template from a local reviewer handoff summary without appending it.",
            ),
            (
                "tools/build_magma_alert_feed_reviewer_handoff_bundle_index.py",
                "Explicit CLI builds a local reviewer handoff bundle index from package, validation, summary, and bridge-template digests without payload transport.",
            ),
            (
                "tools/verify_magma_alert_feed_reviewer_handoff_bundle_index.py",
                "Explicit CLI verifies a local reviewer handoff bundle index by recomputing artifact digests without payload transport.",
            ),
            (
                "tools/build_magma_alert_feed_reviewer_handoff_bundle_verification_summary.py",
                "Explicit CLI renders a local reviewer handoff bundle verification summary from the verifier report without approval automation.",
            ),
            (
                "tools/validate_magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference.py",
                "Explicit CLI validates a local reviewer handoff bundle operator decision reference without granting approval.",
            ),
            (
                "tools/build_magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference_review_summary.py",
                "Explicit CLI renders a local operator decision-reference review summary without granting approval.",
            ),
            (
                "tools/build_magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference_review_bundle_index.py",
                "Explicit CLI builds a local operator decision-reference review bundle index from validation and review-summary digests without granting approval.",
            ),
            (
                "tools/verify_magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference_review_bundle_index.py",
                "Explicit CLI verifies a local operator decision-reference review bundle index by recomputing artifact checks without granting approval.",
            ),
            (
                "tools/build_magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference_review_bundle_verification_summary.py",
                "Explicit CLI renders a local operator decision-reference review bundle verification summary from the verifier result without granting approval.",
            ),
            (
                "tools/build_magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference_review_bundle_verification_bridge_event_template.py",
                "Explicit CLI renders a local operator decision-reference review bundle verification bridge-event template without appending it or granting approval.",
            ),
            (
                "tools/build_magma_decision_review_verification_template_index_entry.py",
                "Explicit CLI builds a local operator decision-reference review bundle verification bridge-event template index entry without appending it or granting approval.",
            ),
            (
                "tools/verify_magma_decision_review_verification_template_index_entry.py",
                "Explicit CLI verifies a local operator decision-reference review bundle verification bridge-event template index entry by recomputing artifact checks without granting approval.",
            ),
            (
                "tools/build_magma_decision_review_verification_template_index_entry_summary.py",
                "Explicit CLI renders a local operator decision-reference review bundle verification bridge-event template index-entry verification summary without appending it or granting approval.",
            ),
            (
                "tools/build_magma_decision_review_verification_template_index_entry_summary_bridge_event_template.py",
                "Explicit CLI renders a local operator decision-reference review bundle verification bridge-event template index-entry verification summary bridge-event template without appending it or granting approval.",
            ),
            (
                "tools/build_magma_decision_review_verification_template_index_entry_summary_bridge_event_template_index_entry.py",
                "Explicit CLI builds a local operator decision-reference review bundle verification bridge-event template index-entry verification summary bridge-event template index entry without appending it or granting approval.",
            ),
            (
                "tools/verify_magma_decision_review_verification_template_index_entry_summary_bridge_event_template_index_entry.py",
                "Explicit CLI verifies a local operator decision-reference review bundle verification bridge-event template index-entry verification summary bridge-event template index entry by recomputing artifact checks without granting approval.",
            ),
            (
                "waggledance/adapters/http/routes/compat_dashboard.py",
                "Ops API exposes sanitized read-only MAGMA import handoff status, bounded history, provider health, thresholds, operator-owned feed freshness source state, and metrics alert-state feed state.",
            ),
            (
                "waggledance/adapters/http/routes/metrics.py",
                "Prometheus metrics expose privacy-safe MAGMA handoff provider health and freshness source state without import controls.",
            ),
            (
                "waggledance/adapters/http/magma_handoff_metrics_alert_feed.py",
                "Optional configured Alertmanager adapter for MAGMA handoff metric alerts with timeout, credential, and private-host guardrails.",
            ),
            (
                "docs/operations/MAGMA_HANDOFF_PROVIDER_METRICS_RUNBOOK.md",
                "Operator runbook documents read-only MAGMA handoff provider metrics alert thresholds.",
            ),
            (
                "web/hologram-brain-v6.html",
                "Hologram Ops panel renders MAGMA import handoff status/history, provider health, thresholds, and feed freshness state without controls.",
            ),
            (
                "schemas/v3_13_0/magma_share_manifest.v0.json",
                "Contract-first cross-instance MAGMA share manifest forbids raw material exports.",
            ),
            (
                "tests/contracts/test_magma_share_manifest_schema.py",
                "Schema regression tests reject raw payloads, replacement maps, raw context, solver output, and raw-query digests.",
            ),
            (
                "tests/tools/test_magma_share_manifest_exporter.py",
                "Exporter tests require operator approval, source verification, count consistency, and payload-marker absence.",
            ),
            (
                "tests/tools/test_magma_share_manifest_importer.py",
                "Importer tests reject context drift and prove peer-review handoff history does not grant authority.",
            ),
            (
                "tests/tools/test_magma_alert_feed_release_evidence_package.py",
                "Evidence package tests prove sanitized artifacts, path-free CLI JSON, and no automatic release decision.",
            ),
            (
                "tests/tools/test_magma_alert_feed_release_evidence_validator.py",
                "Evidence validator tests prove digest checks, privacy/control blockers, path-free CLI JSON, and no release decision.",
            ),
            (
                "tests/tools/test_magma_alert_feed_reviewer_handoff_summary.py",
                "Reviewer handoff summary tests prove sanitized context, path-free CLI JSON, and no approval automation.",
            ),
            (
                "tests/tools/test_magma_alert_feed_reviewer_bridge_event_template.py",
                "Reviewer bridge-event template tests prove schema-valid handoff JSON, path-free CLI output, and no direct bridge write.",
            ),
            (
                "tests/tools/test_magma_alert_feed_reviewer_handoff_bundle_index.py",
                "Reviewer handoff bundle index tests prove digest binding, path-free CLI output, no payload inclusion, and no approval automation.",
            ),
            (
                "tests/tools/test_magma_alert_feed_reviewer_handoff_bundle_verifier.py",
                "Reviewer handoff bundle verifier tests prove recomputed digest checks, path-free CLI output, no payload inclusion, and no approval automation.",
            ),
            (
                "tests/tools/test_magma_alert_feed_reviewer_handoff_bundle_verification_summary.py",
                "Reviewer handoff bundle verification summary tests prove path-free verifier-result rendering, no payload inclusion, and no approval automation.",
            ),
            (
                "tests/tools/test_magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference_validator.py",
                "Reviewer handoff bundle operator decision-reference validator tests prove path-free context validation and no approval automation.",
            ),
            (
                "tests/tools/test_magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference_review_summary.py",
                "Reviewer handoff bundle operator decision-reference review summary tests prove path-free context rendering and no approval automation.",
            ),
            (
                "tests/tools/test_magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference_review_bundle_index.py",
                "Reviewer handoff bundle operator decision-reference review bundle index tests prove digest binding, path-free CLI output, no payload inclusion, and no approval automation.",
            ),
            (
                "tests/tools/test_magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference_review_bundle_verifier.py",
                "Reviewer handoff bundle operator decision-reference review bundle verifier tests prove recomputed digest checks, path-free CLI output, no payload inclusion, and no approval automation.",
            ),
            (
                "tests/tools/test_magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference_review_bundle_verification_summary.py",
                "Reviewer handoff bundle operator decision-reference review bundle verification summary tests prove path-free verifier-result rendering, no payload inclusion, and no approval automation.",
            ),
            (
                "tests/tools/test_magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference_review_bundle_verification_bridge_event_template.py",
                "Reviewer handoff bundle operator decision-reference review bundle verification bridge-event template tests prove schema-valid handoff JSON, path-free CLI output, no payload inclusion, and no approval automation.",
            ),
            (
                "tests/tools/test_magma_decision_review_verification_template_index_entry.py",
                "Reviewer handoff bundle operator decision-reference review bundle verification bridge-event template index-entry tests prove digest binding, path-free CLI output, no payload inclusion, and no approval automation.",
            ),
            (
                "tests/tools/test_magma_decision_review_verification_template_index_entry_verifier.py",
                "Reviewer handoff bundle operator decision-reference review bundle verification bridge-event template index-entry verifier tests prove recomputed digest checks, path-free CLI output, no payload inclusion, and no approval automation.",
            ),
            (
                "tests/tools/test_magma_decision_review_verification_template_index_entry_summary_bridge_event_template_index_entry.py",
                "Reviewer handoff bundle operator decision-reference review bundle verification bridge-event template index-entry verification summary bridge-event template index-entry tests prove digest binding, path-free CLI output, no payload inclusion, and no approval automation.",
            ),
            (
                "tests/tools/test_magma_decision_review_verification_template_index_entry_summary_bridge_event_template_index_entry_verifier.py",
                "Reviewer handoff bundle operator decision-reference review bundle verification bridge-event template index-entry verification summary bridge-event template index-entry verifier tests prove recomputed digest checks, path-free CLI output, no payload inclusion, and no approval automation.",
            ),
            (
                "tests/test_metrics_endpoint.py",
                "Metrics endpoint tests prove MAGMA provider-health gauges use fixed labels and do not leak private feed refs.",
            ),
            (
                "docs/architecture/MAGMA_SHARE_MANIFEST_CONTRACT.md",
                "Architecture doc records the no-default-runtime-export share boundary.",
            ),
            (
                "docs/API.md",
                "API contract documents read-only MAGMA import handoff status/history, provider health, thresholds, and feed freshness state.",
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
            (
                "waggledance/adapters/http/routes/metrics.py",
                "Prometheus metrics expose the autogrowth ticker boundary.",
            ),
            (
                "waggledance/adapters/http/routes/compat_dashboard.py",
                "Ops API exposes read-only autogrowth ticker and alert status.",
            ),
            (
                "web/hologram-brain-v6.html",
                "Hologram Ops panel renders autogrowth status and alerts.",
            ),
            (
                "docs/API.md",
                "Operator-facing metrics contract documents autogrowth counters.",
            ),
            (
                "docs/operations/LOW_RISK_AUTOGROWTH_RUNBOOK.md",
                "Operator runbook documents read-only autogrowth alert thresholds.",
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
            (
                "tools/hex_shadow_subdivision_replay.py",
                "Read-only shadow subdivision replay artifact builder, verifier, reviewer summary renderer, bridge-event template builder, and template index-entry builder.",
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
            (
                "tools/wd_image1_capability_manifest.py",
                "Executable scale-axis scorecard proof.",
            ),
        ),
    )
    hex_upgrade_proof = build_hexagonal_upgrade_proof(root)
    hex_upgrade_runtime_smoke = build_hexagonal_upgrade_runtime_smoke(root)
    hex_upgrade_source_snapshot = build_source_snapshot(root)
    hex_upgrade_shadow_replay = build_shadow_subdivision_replay_artifact(
        upgrade_proof=hex_upgrade_proof,
        runtime_boundary_smoke=hex_upgrade_runtime_smoke,
        source_snapshot=hex_upgrade_source_snapshot,
    )
    hex_upgrade_shadow_replay_verification = verify_shadow_subdivision_replay_artifact(
        hex_upgrade_shadow_replay,
        expected_git_commit=hex_upgrade_source_snapshot.get("git_commit"),
    )
    hex_upgrade_shadow_replay_verifier_summary = (
        build_shadow_subdivision_replay_verifier_summary(
            hex_upgrade_shadow_replay_verification,
            reviewer_agent_id="codex-tools-1",
            handoff_ref="wd-image1-hex-shadow-replay-verifier-summary",
        )
    )
    hex_upgrade_shadow_replay_verifier_summary_bridge_event_template = (
        build_shadow_subdivision_replay_verifier_summary_bridge_event_template(
            hex_upgrade_shadow_replay_verifier_summary,
            agent_id="codex-lead-1",
            task_id="wd-image1-hex-shadow-replay-verifier-summary-template",
            to="operator,claude-rco-1,codex-tools-1",
            role="lead-impl",
        )
    )
    hex_upgrade_shadow_replay_verifier_summary_bridge_event_template_index_entry = build_shadow_subdivision_replay_verifier_summary_bridge_event_template_index_entry(
        hex_upgrade_shadow_replay_verifier_summary_bridge_event_template,
        template_report_bytes=json.dumps(
            hex_upgrade_shadow_replay_verifier_summary_bridge_event_template,
            sort_keys=True,
        ).encode("utf-8"),
    )
    hex_upgrade_proof["runtime_boundary_smoke"] = hex_upgrade_runtime_smoke
    hex_upgrade_proof["shadow_subdivision_replay"] = hex_upgrade_shadow_replay
    hex_upgrade_proof["shadow_subdivision_replay_verification"] = (
        hex_upgrade_shadow_replay_verification
    )
    hex_upgrade_proof["shadow_subdivision_replay_verifier_summary"] = (
        hex_upgrade_shadow_replay_verifier_summary
    )
    hex_upgrade_proof[
        "shadow_subdivision_replay_verifier_summary_bridge_event_template"
    ] = hex_upgrade_shadow_replay_verifier_summary_bridge_event_template
    hex_upgrade_proof[
        "shadow_subdivision_replay_verifier_summary_bridge_event_template_index_entry"
    ] = hex_upgrade_shadow_replay_verifier_summary_bridge_event_template_index_entry
    hex_upgrade_proof["ok"] = bool(
        hex_upgrade_proof.get("ok") is True
        and hex_upgrade_runtime_smoke.get("ok") is True
        and hex_upgrade_shadow_replay.get("ok") is True
        and hex_upgrade_shadow_replay_verification.get("ok") is True
        and hex_upgrade_shadow_replay_verifier_summary.get("ok") is True
        and hex_upgrade_shadow_replay_verifier_summary_bridge_event_template.get("ok")
        is True
        and hex_upgrade_shadow_replay_verifier_summary_bridge_event_template_index_entry.get(
            "ok"
        )
        is True
    )
    low_risk_autonomy_proof = build_low_risk_autonomy_proof()
    low_risk_runtime_boundary_smoke = build_low_risk_autogrowth_runtime_boundary_smoke(
        root
    )
    low_risk_operator_metrics_smoke = build_low_risk_autogrowth_operator_metrics_smoke(
        root
    )
    low_risk_alert_runbook_smoke = build_low_risk_autogrowth_alert_runbook_smoke(root)
    low_risk_ops_alert_state_smoke = build_low_risk_autogrowth_ops_alert_state_smoke(
        root
    )
    low_risk_autonomy_proof["runtime_boundary_smoke"] = low_risk_runtime_boundary_smoke
    low_risk_autonomy_proof["operator_metrics_smoke"] = low_risk_operator_metrics_smoke
    low_risk_autonomy_proof["alert_runbook_smoke"] = low_risk_alert_runbook_smoke
    low_risk_autonomy_proof["ops_alert_state_smoke"] = low_risk_ops_alert_state_smoke
    low_risk_autonomy_proof["ok"] = bool(
        low_risk_autonomy_proof.get("ok") is True
        and low_risk_runtime_boundary_smoke.get("ok") is True
        and low_risk_operator_metrics_smoke.get("ok") is True
        and low_risk_alert_runbook_smoke.get("ok") is True
        and low_risk_ops_alert_state_smoke.get("ok") is True
    )
    hex_entry_proof = build_hex_mesh_entry_proof(root)
    solver_trace_proof = build_deterministic_solver_trace_proof(root)
    magma_metrics_runbook_smoke = build_magma_handoff_provider_metrics_runbook_smoke(
        root
    )
    magma_metrics_alert_state_smoke = build_magma_handoff_metrics_alert_state_smoke(
        root
    )
    magma_metrics_alertmanager_adapter_smoke = (
        build_magma_handoff_metrics_alertmanager_adapter_smoke(root)
    )
    magma_audit_proof = dict(
        solver_trace_proof.get("magma_execution_receipt_proof") or {}
    )
    magma_audit_proof["provider_metrics_runbook_smoke"] = magma_metrics_runbook_smoke
    magma_audit_proof["metrics_alert_state_smoke"] = magma_metrics_alert_state_smoke
    magma_audit_proof["metrics_alertmanager_adapter_smoke"] = (
        magma_metrics_alertmanager_adapter_smoke
    )
    magma_audit_proof["ok"] = bool(
        magma_audit_proof.get("ok") is True
        and magma_metrics_runbook_smoke.get("ok") is True
        and magma_metrics_alert_state_smoke.get("ok") is True
        and magma_metrics_alertmanager_adapter_smoke.get("ok") is True
    )
    future_scale_scorecard = build_future_scale_axis_scorecard(root)

    return (
        Capability(
            capability_id="hex_mesh_entry",
            title="Hex-mesh query entry",
            image_claim=(
                "Every query first enters an intelligent 8-cell honeycomb " "topology."
            ),
            safe_statement=(
                "WD has two independent topologies: an 8-cell "
                "solver-retrieval topology and a 7-cell agent-routing "
                "topology; HTTP/WS route-stage labels are privacy-safe and "
                "dashboard-visible, route-stage operator metrics expose "
                "counts and runtime rate/latency counters from sanitized "
                "traces plus p95/p99 PromQL latency panel templates and "
                "an optional sanitized read-only Prometheus/Alertmanager "
                "feed provider with timeout, credential, and private-host "
                "guardrails; exact runtime entry order depends on flags and "
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
                "Add provider health/cache metrics and bounded backoff "
                "without adding route controls."
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
                "opt-in solver-trace runtime receipt proof exist. A "
                "contract-first cross-instance share manifest also defines a "
                "no-payload/no-raw-material export boundary, and an explicit "
                "operator-gated local exporter validates it before writing "
                "share metadata. A no-authority importer validates fresh "
                "share metadata against a local receipt bundle and rejects "
                "context drift before building a replay plan. An "
                "operator-owned peer-review handoff artifact can record "
                "import decisions without payloads, local paths, or runtime "
                "authority. A read-only /api/ops and hologram summary can "
                "surface bounded handoff status history plus provider health "
                "with read-only freshness/retention thresholds plus a "
                "sanitized operator-owned feed freshness source for the "
                "explicit handoff feed. The same sanitized state is exposed "
                "as privacy-safe /metrics gauges with fixed "
                "status/freshness/alert labels and without disk scanning, "
                "payload import, local path exposure, provider exception "
                "details, or runtime controls. A read-only metrics runbook "
                "documents conservative Prometheus alert thresholds for that "
                "handoff provider state without adding import controls or "
                "runtime authority. An optional read-only /api/ops "
                "metrics_alert_state can surface sanitized Alertmanager "
                "state for fixed MAGMA handoff metric alert IDs without raw "
                "labels, annotations, URLs, paths, exception details, import "
                "controls, or runtime authority. A configured adapter can "
                "fetch that state from an operator-owned Alertmanager with "
                "timeout, credential, private-host, TTL cache, and bounded "
                "failure-backoff guardrails. The Ops/hologram surface now "
                "adds read-only freshness/error SLO panels, drill-evidence "
                "artifact classes, and manual release-gate examples for "
                "operator review. A local evidence package tool can write "
                "sanitized JSON/Markdown release-review artifacts from "
                "explicit operator-provided /api/ops and /metrics files "
                "without endpoint fetches, transport, automatic release "
                "decisions, or runtime controls. A companion validator can "
                "check package structure and optional local artifact digests "
                "for reviewers without writes, transport, endpoint fetches, "
                "or release decisions. A local reviewer handoff bundle index "
                "can bind the package, validation report, handoff summary, "
                "and bridge-event template digests without including "
                "payloads, recording paths, transport, or approval "
                "automation. A local verifier can recompute those index "
                "digests from explicit local files without transport, "
                "payload inclusion, path recording, or approval automation. "
                "A local verification summary renderer can turn that "
                "verifier result into path-free reviewer context without "
                "transport, bridge writes, payload inclusion, path recording, "
                "or approval automation. A local operator decision-reference "
                "validator can check that the bundle bridge-event template "
                "reference matches the expected operator-owned reference and "
                "stays context-only, not approval or a release decision. A "
                "local operator decision-reference review summary renderer "
                "can turn that validation result into path-free reviewer "
                "context while keeping the operator decision separate. A "
                "local operator decision-reference review bundle index can "
                "tie that validation result and review summary digests "
                "without payload inclusion, path recording, transport, "
                "bridge writes, or approval automation. A local operator "
                "decision-reference review bundle verifier can recompute "
                "those digest, size, and schema checks from explicit local "
                "files while keeping payload inclusion, path recording, "
                "transport, bridge writes, and approval automation false. A "
                "local operator decision-reference review bundle "
                "verification summary renderer can turn that verifier "
                "result into path-free reviewer context with rebuilt-index "
                "and source-contract checks while keeping payload inclusion, "
                "path recording, transport, bridge writes, and approval "
                "automation false. A local operator decision-reference "
                "review bundle verification bridge-event template can render "
                "that verified summary as schema-valid handoff JSON while "
                "keeping payload inclusion, path recording, transport, "
                "bridge writes, and approval automation false. A local "
                "operator decision-reference review bundle verification "
                "bridge-event template index entry can bind that verified "
                "summary and template digest with schema-validation and "
                "rebuilt-template checks while keeping payload inclusion, "
                "path recording, transport, bridge writes, and approval "
                "automation false. A local verifier for that index entry "
                "can recompute digest, size, schema, source-contract, "
                "rebuilt-entry, and bridge-event-schema checks while "
                "keeping payload inclusion, path recording, transport, "
                "bridge writes, and approval "
                "automation false. A local operator decision-reference "
                "review bundle verification bridge-event template "
                "index-entry verification summary renderer can turn that "
                "verifier result into path-free reviewer context while "
                "keeping payload inclusion, path recording, transport, "
                "bridge writes, and approval automation false. A local "
                "bridge-event template for that summary can render "
                "schema-valid handoff JSON while keeping payload inclusion, "
                "path recording, transport, bridge writes, approval "
                "automation, and release decisions false. A local index "
                "entry for that summary bridge-event template can bind the "
                "summary/template digests without payload inclusion, path "
                "recording, transport, bridge writes, approval automation, "
                "or release decisions. A local verifier for that index "
                "entry can recompute digest, size, schema, source-contract, "
                "rebuilt-entry, and bridge-event-schema checks while "
                "keeping payload inclusion, path recording, transport, "
                "bridge writes, approval automation, and release decisions "
                "false; "
                "hard append-only/default enforcement is still not yet safe "
                "to claim."
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
                "The share importer, peer-review handoff, and bounded "
                "operator summary history/provider health remain local "
                "proof/status tooling; "
                "they do not add cross-instance transport or runtime "
                "authority.",
            ),
            next_smallest_pr=(
                "Add a local verification summary renderer for the operator "
                "decision-reference "
                "review bundle verification bridge-event template "
                "index-entry verification summary bridge-event template "
                "index-entry verifier without appending it."
            ),
            proof=magma_audit_proof,
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
                "ticker boundary smoke, Prometheus operator metrics, a "
                "read-only dashboard ops overlay with local alert state, "
                "operator alert thresholds, and proof fixtures; unrestricted "
                "runtime authority is not claimed."
            ),
            status=_status_for(autogrowth_evidence),
            claim_safe=False,
            evidence=autogrowth_evidence,
            gaps=(
                "AutogrowthScheduler is caller-driven and explicitly " "bounded.",
                "The low-risk allowlist is fixed; adding families requires "
                "reviewed deterministic compiler and executor support.",
                "The executable proof uses an ephemeral temp DB; it does not "
                "grant production runtime authority.",
                "The runtime boundary smoke proves ticker construction and "
                "lifespan hooks, not autonomous production authority.",
                "Operator metrics expose ticker cadence and counters, not "
                "new mutation authority.",
                "The dashboard overlay is read-only status; it adds no "
                "start/stop or configuration controls.",
                "The dashboard alert state is a local snapshot; "
                "time-window rules remain delegated to the operator "
                "Prometheus/Alertmanager feed.",
                "The alert thresholds are read-only Prometheus/operator "
                "runbook guidance; they add no mutating endpoints or runtime "
                "authority.",
            ),
            next_smallest_pr=(
                "Wire a real Prometheus/Alertmanager feed into the read-only "
                "Ops alert state without adding controls."
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
                "boundary smoke plus operator-visible /metrics gauges "
                "report the active config topology without mutating it. A "
                "shadow replay artifact binds the pure plan/relation/delivery "
                "proof to that read-only metrics contract without runtime "
                "activation, and a local verifier recomputes the replay "
                "digests and no-authority guardrails offline. A path-free "
                "reviewer summary renders that verifier result as context "
                "without bridge writes, transport, or runtime authority. A "
                "template-only bridge-event template renderer can turn that "
                "summary into schema-valid handoff JSON without appending it "
                "or granting subdivision authority, and a local index entry "
                "binds that template's digest and schema check without "
                "including payloads or granting runtime authority."
            ),
            status=_status_for(hex_upgrade_evidence),
            claim_safe=False,
            evidence=hex_upgrade_evidence,
            gaps=(
                "Subdivision is shadow-first and does not mutate runtime " "topology.",
                "Ring delivery is pure validation, not a networked runtime "
                "delivery layer.",
                "The runtime boundary smoke and metrics report current "
                "Container/config wiring; they do not activate subdivision "
                "authority.",
            ),
            next_smallest_pr=(
                "Add a local verifier for the shadow subdivision replay "
                "verifier summary bridge-event template index entry without "
                "appending it or activating runtime subdivision authority."
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
                "The repo has future scale architecture and a scale-axis "
                "scorecard; unlimited scalability remains a target, not a "
                "fact."
            ),
            status=_status_for(future_evidence),
            claim_safe=False,
            evidence=future_evidence,
            gaps=(
                "No finite software system can honestly prove infinite " "scalability.",
                "Future claims must be tied to measured axes such as "
                "coverage, fallback rate, latency, and audit completeness.",
                "The current scorecard defines gates; it does not yet export "
                "all runtime metrics.",
            ),
            next_smallest_pr=(
                "Populate the scale-axis scorecard from runtime metrics and "
                "benchmark artifacts."
            ),
            proof=future_scale_scorecard,
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

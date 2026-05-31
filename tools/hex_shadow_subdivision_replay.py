# SPDX-License-Identifier: BUSL-1.1
"""Build a read-only hex shadow-subdivision replay artifact."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
PROOF_ID = "hex_shadow_subdivision_replay_v1"
PROOF_TYPE = "shadow_replay_hypothetical"

TOPOLOGY_BOUNDARY_METRIC_NAMES: tuple[str, ...] = (
    "waggledance_hex_topology_boundary_up",
    "waggledance_hex_topology_cells",
    "waggledance_hex_topology_agents_mapped",
    "waggledance_hex_topology_neighbor_links",
    "waggledance_hex_topology_runtime_dispatch_enabled",
    "waggledance_hex_topology_runtime_mutation_authority",
)


def _canonical_digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _utc_iso(value: datetime) -> str:
    return (
        value.astimezone(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _git_head_commit(root: Path) -> str | None:
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            check=False,
            capture_output=True,
            encoding="utf-8",
            timeout=5,
        )
    except Exception:
        return None
    if completed.returncode != 0:
        return None
    value = completed.stdout.strip()
    return value if value else None


def build_source_snapshot(
    root: Path | str = ROOT,
    *,
    now_utc: datetime | None = None,
) -> dict:
    """Return path-free local checkout metadata for persisted replay context."""

    repo_root = Path(root)
    git_commit = _git_head_commit(repo_root)
    return {
        "source": "local_checkout",
        "collected_at_utc": _utc_iso(now_utc or datetime.now(timezone.utc)),
        "git_commit": git_commit,
        "git_commit_available": git_commit is not None,
    }


def _delivery_summary(deliveries: Sequence[Mapping[str, Any]]) -> dict:
    kinds: list[str] = []
    delivered_count = 0
    blocked_count = 0
    for delivery in deliveries:
        msg = delivery.get("msg")
        if isinstance(msg, Mapping) and isinstance(msg.get("kind"), str):
            kinds.append(str(msg["kind"]))
        if delivery.get("delivered") is True:
            delivered_count += 1
        else:
            blocked_count += 1
    return {
        "message_count": len(deliveries),
        "delivered_count": delivered_count,
        "blocked_count": blocked_count,
        "message_kinds": sorted(set(kinds)),
    }


def _metric_contract(runtime_boundary_smoke: Mapping[str, Any]) -> dict:
    metrics = runtime_boundary_smoke.get("operator_metrics_smoke")
    if not isinstance(metrics, Mapping):
        metrics = {}
    runtime_contract = metrics.get("runtime_contract")
    if not isinstance(runtime_contract, Mapping):
        runtime_contract = {}

    metric_names = [
        str(name)
        for name in metrics.get("metric_names", [])
        if isinstance(name, str)
    ]
    expected_lines = [
        str(line)
        for line in runtime_contract.get("expected_lines", [])
        if isinstance(line, str)
    ]
    return {
        "metrics_endpoint": metrics.get("metrics_endpoint", "/metrics"),
        "metric_names": sorted(metric_names),
        "expected_lines": sorted(expected_lines),
        "status_code": runtime_contract.get("status_code"),
        "forbidden_payload_markers_absent": (
            runtime_contract.get("forbidden_payload_markers_absent") is True
        ),
    }


def _runtime_topology_summary(runtime_boundary_smoke: Mapping[str, Any]) -> dict:
    topology = runtime_boundary_smoke.get("runtime_topology")
    if not isinstance(topology, Mapping):
        topology = {}
    return {
        "cell_count": topology.get("cell_count"),
        "enabled_cell_count": topology.get("enabled_cell_count"),
        "dispatch_enabled": (
            runtime_boundary_smoke.get("active_runtime_dispatch_enabled") is True
        ),
        "shadow_child_cell_ids_absent_from_runtime_config": (
            runtime_boundary_smoke.get(
                "shadow_child_cell_ids_absent_from_runtime_config"
            )
            is True
        ),
    }


def build_shadow_subdivision_replay_artifact(
    *,
    upgrade_proof: Mapping[str, Any],
    runtime_boundary_smoke: Mapping[str, Any],
    source_snapshot: Mapping[str, Any] | None = None,
) -> dict:
    """Bind the pure shadow plan proof to the read-only metrics contract.

    The returned artifact is intentionally summary/digest-only: it does not
    include runtime topology config contents, query strings, local paths, or
    message payloads.
    """

    plan = upgrade_proof.get("plan")
    if not isinstance(plan, Mapping):
        plan = {}
    relations = upgrade_proof.get("relations")
    if not isinstance(relations, Mapping):
        relations = {}
    deliveries = upgrade_proof.get("deliveries")
    if not isinstance(deliveries, Sequence) or isinstance(deliveries, (str, bytes)):
        deliveries = []

    metric_contract = _metric_contract(runtime_boundary_smoke)
    runtime_summary = _runtime_topology_summary(runtime_boundary_smoke)
    delivery_summary = _delivery_summary(
        [
            delivery
            for delivery in deliveries
            if isinstance(delivery, Mapping)
        ]
    )
    shadow_plan_summary = {
        "plan_id": plan.get("plan_id"),
        "parent_cell_id": plan.get("parent_cell_id"),
        "new_child_cell_ids": sorted(
            str(child)
            for child in plan.get("new_child_cell_ids", [])
            if isinstance(child, str)
        ),
        "target_state": plan.get("target_state"),
        "no_runtime_mutation": plan.get("no_runtime_mutation") is True,
    }
    relation_summary = {
        "thermal_children": list(relations.get("thermal_children", [])),
        "heating_siblings": list(relations.get("heating_siblings", [])),
        "heating_ancestors": list(relations.get("heating_ancestors", [])),
    }
    digest_inputs = {
        "shadow_plan_summary": shadow_plan_summary,
        "relation_summary": relation_summary,
        "delivery_summary": delivery_summary,
        "metric_contract": metric_contract,
        "runtime_topology_summary": runtime_summary,
        "source_snapshot": dict(source_snapshot or {}),
    }
    guardrails = {
        "no_runtime_topology_mutation": (
            upgrade_proof.get("no_runtime_mutation") is True
            and runtime_boundary_smoke.get("no_runtime_topology_mutation") is True
        ),
        "runtime_authority_changed": (
            runtime_boundary_smoke.get("runtime_authority_changed") is True
        ),
        "operator_gate_required": (
            runtime_boundary_smoke.get("operator_gate_required") is True
        ),
        "external_writes_applied": (
            runtime_boundary_smoke.get("external_writes_applied") is True
        ),
        "dispatch_controls_added": False,
        "network_transport_added": False,
        "raw_query_or_payload_included": False,
        "runtime_config_contents_included": False,
        "local_paths_recorded": False,
        "numeric_equality_to_shadow_children_claimed": False,
    }
    metric_names_present = set(metric_contract["metric_names"])
    all_metric_names_present = all(
        name in metric_names_present for name in TOPOLOGY_BOUNDARY_METRIC_NAMES
    )
    ok = (
        upgrade_proof.get("ok") is True
        and runtime_boundary_smoke.get("ok") is True
        and shadow_plan_summary["no_runtime_mutation"]
        and delivery_summary["message_count"] == delivery_summary["delivered_count"]
        and runtime_summary["shadow_child_cell_ids_absent_from_runtime_config"]
        and metric_contract["forbidden_payload_markers_absent"]
        and all_metric_names_present
        and guardrails["no_runtime_topology_mutation"]
        and not guardrails["runtime_authority_changed"]
        and not guardrails["operator_gate_required"]
        and not guardrails["external_writes_applied"]
    )
    artifact = {
        "proof_id": PROOF_ID,
        "proof_type": PROOF_TYPE,
        "ok": ok,
        "binding_scope": "structural_metrics_contract_only",
        "shadow_plan_summary": shadow_plan_summary,
        "relation_summary": relation_summary,
        "delivery_summary": delivery_summary,
        "runtime_topology_summary": runtime_summary,
        "metric_contract_summary": metric_contract,
        "source_snapshot": dict(source_snapshot or {}),
        "digests": {
            "plan": _canonical_digest(shadow_plan_summary),
            "relations": _canonical_digest(relation_summary),
            "deliveries": _canonical_digest(delivery_summary),
            "metric_contract": _canonical_digest(metric_contract),
            "runtime_topology_summary": _canonical_digest(runtime_summary),
            "source_snapshot": _canonical_digest(dict(source_snapshot or {})),
            "full_binding": _canonical_digest(digest_inputs),
        },
        "guardrails": guardrails,
        "safe_conclusion": (
            "The replay binds a pure shadow subdivision plan, parent/child "
            "relation checks, and delivered message counts to the current "
            "read-only hex topology boundary metrics contract. It is not "
            "evidence that subdivision is active in runtime topology, and it "
            "does not grant runtime mutation authority."
        ),
    }
    artifact["artifact_digest"] = _canonical_digest(
        {key: value for key, value in artifact.items() if key != "artifact_digest"}
    )
    if not ok:
        artifact["blocked_reason"] = "upstream_proof_or_metric_contract_failed"
    return artifact


def build_replay_artifact_for_root(root: Path | str = ROOT) -> dict:
    """Build the full replay artifact from the current manifest proof inputs."""

    repo_root = Path(root)
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    from tools.wd_image1_capability_manifest import (  # noqa: PLC0415
        build_hexagonal_upgrade_proof,
        build_hexagonal_upgrade_runtime_smoke,
    )

    upgrade_proof = build_hexagonal_upgrade_proof(repo_root)
    runtime_smoke = build_hexagonal_upgrade_runtime_smoke(repo_root)
    return build_shadow_subdivision_replay_artifact(
        upgrade_proof=upgrade_proof,
        runtime_boundary_smoke=runtime_smoke,
        source_snapshot=build_source_snapshot(repo_root),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Emit the read-only hex shadow subdivision replay artifact."
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
        "--strict",
        action="store_true",
        help="Exit 2 when the replay artifact does not pass.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    artifact = build_replay_artifact_for_root(args.root)
    print(json.dumps(artifact, indent=2, sort_keys=True))
    if args.strict and artifact.get("ok") is not True:
        return 2
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised by CLI smoke
    raise SystemExit(main())

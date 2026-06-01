# SPDX-License-Identifier: BUSL-1.1
"""Build a read-only hex shadow-subdivision replay artifact."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
PROOF_ID = "hex_shadow_subdivision_replay_v1"
PROOF_TYPE = "shadow_replay_hypothetical"
VERIFIER_PROOF_ID = "hex_shadow_subdivision_replay_verifier_v1"
VERIFIER_SUMMARY_PROOF_ID = "hex_shadow_subdivision_replay_verifier_summary_v1"
VERIFIER_SUMMARY_BRIDGE_EVENT_TEMPLATE_PROOF_ID = (
    "hex_shadow_subdivision_replay_verifier_summary_bridge_event_template_v1"
)
VERIFIER_SUMMARY_BRIDGE_EVENT_TEMPLATE_VERSION = (
    "hex_shadow_subdivision_replay_verifier_summary_bridge_event_template.v1"
)
VERIFIER_SUMMARY_BRIDGE_EVENT_STATUS = (
    "hex_shadow_subdivision_replay_verifier_summary_bridge_event_template_ready"
)

TOPOLOGY_BOUNDARY_METRIC_NAMES: tuple[str, ...] = (
    "waggledance_hex_topology_boundary_up",
    "waggledance_hex_topology_cells",
    "waggledance_hex_topology_agents_mapped",
    "waggledance_hex_topology_neighbor_links",
    "waggledance_hex_topology_runtime_dispatch_enabled",
    "waggledance_hex_topology_runtime_mutation_authority",
)

ARTIFACT_TOP_LEVEL_KEYS: frozenset[str] = frozenset(
    {
        "artifact_digest",
        "binding_scope",
        "blocked_reason",
        "delivery_summary",
        "digests",
        "guardrails",
        "metric_contract_summary",
        "ok",
        "proof_id",
        "proof_type",
        "relation_summary",
        "runtime_topology_summary",
        "safe_conclusion",
        "shadow_plan_summary",
        "source_snapshot",
    }
)
GUARDRAIL_TRUE_FIELDS: tuple[str, ...] = ("no_runtime_topology_mutation",)
GUARDRAIL_FALSE_FIELDS: tuple[str, ...] = (
    "runtime_authority_changed",
    "operator_gate_required",
    "external_writes_applied",
    "dispatch_controls_added",
    "network_transport_added",
    "raw_query_or_payload_included",
    "runtime_config_contents_included",
    "local_paths_recorded",
    "numeric_equality_to_shadow_children_claimed",
)
DIGEST_NAMES: tuple[str, ...] = (
    "plan",
    "relations",
    "deliveries",
    "metric_contract",
    "runtime_topology_summary",
    "source_snapshot",
    "full_binding",
)
PATH_MARKERS: tuple[str, ...] = (
    "file://",
    "/home/",
    "/python/",
    "/users/",
    "/workspace/",
    "/workspaces/",
    "/tmp/",
    "waggledance-agent-worktrees",
)
WINDOWS_DRIVE_PATH_PATTERN = re.compile(r"(?:^|[^A-Za-z0-9])(?:[A-Za-z]:[\\/])")
AGENT_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{1,32}$")
SAFE_REF_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:._-]{0,191}$")
SAFE_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:._-]{0,191}$")
SESSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
SUMMARY_AUTHORITY_FALSE_FIELDS: tuple[str, ...] = (
    "approval_granted",
    "release_decision_made",
    "automatic_release_decision",
    "direct_bridge_write_performed",
    "transport_added",
    "external_fetch_performed",
    "runtime_controls_added",
    "runtime_subdivision_authority_granted",
    "artifact_payloads_included",
    "local_paths_recorded",
)


def _canonical_digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _json_error_report(blocker: str) -> dict:
    return {
        "proof_id": VERIFIER_PROOF_ID,
        "verified_proof_id": None,
        "ok": False,
        "artifact_declared_ok": False,
        "recomputed_contract_ok": False,
        "blockers": [blocker],
        "checks": {
            "artifact_readable": blocker != "artifact_unreadable",
            "artifact_json_valid": blocker != "artifact_json_invalid",
            "artifact_is_object": blocker != "artifact_not_object",
            "input_path_recorded": False,
        },
        "recomputed_digests": {},
        "declared_digests": {},
        "guardrails": {},
        "safe_conclusion": (
            "The local replay verifier failed closed before artifact "
            "validation. It did not record the input path or grant runtime "
            "subdivision authority."
        ),
    }


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non_finite_json_number:{value}")


def _contains_path_marker(value: Any) -> bool:
    if isinstance(value, str):
        normalized = value.replace("\\", "/").lower()
        return (
            WINDOWS_DRIVE_PATH_PATTERN.search(value) is not None
            or normalized.startswith("//")
            or any(marker in normalized for marker in PATH_MARKERS)
        )
    if isinstance(value, Mapping):
        return any(
            _contains_path_marker(key) or _contains_path_marker(item)
            for key, item in value.items()
        )
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return any(_contains_path_marker(item) for item in value)
    return False


def _mapping_or_empty(value: Any) -> dict:
    return dict(value) if isinstance(value, Mapping) else {}


def _sequence_of_strings(value: Any) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    return [item for item in value if isinstance(item, str)]


def _safe_ref_or_invalid(value: Any) -> str:
    if isinstance(value, str) and SAFE_REF_PATTERN.fullmatch(value):
        return value
    return "invalid_ref"


def _safe_token(value: Any, fallback: str = "invalid_token") -> str:
    if isinstance(value, str) and SAFE_TOKEN_PATTERN.fullmatch(value):
        return value
    return fallback


def _safe_token_list(value: Any) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    tokens: list[str] = []
    for item in value:
        tokens.append(_safe_token(item))
    return sorted(set(tokens))


def _check_status(value: Any) -> str:
    return "match" if value is True else "mismatch"


def _artifact_digest_input(artifact: Mapping[str, Any]) -> dict:
    return {
        key: value
        for key, value in artifact.items()
        if key != "artifact_digest"
    }


def _expected_artifact_digests(
    artifact: Mapping[str, Any],
) -> tuple[dict[str, str], str]:
    shadow_plan_summary = _mapping_or_empty(
        artifact.get("shadow_plan_summary")
    )
    relation_summary = _mapping_or_empty(artifact.get("relation_summary"))
    delivery_summary = _mapping_or_empty(artifact.get("delivery_summary"))
    metric_contract = _mapping_or_empty(
        artifact.get("metric_contract_summary")
    )
    runtime_summary = _mapping_or_empty(
        artifact.get("runtime_topology_summary")
    )
    source_snapshot = _mapping_or_empty(artifact.get("source_snapshot"))
    digest_inputs = {
        "shadow_plan_summary": shadow_plan_summary,
        "relation_summary": relation_summary,
        "delivery_summary": delivery_summary,
        "metric_contract": metric_contract,
        "runtime_topology_summary": runtime_summary,
        "source_snapshot": source_snapshot,
    }
    return (
        {
            "plan": _canonical_digest(shadow_plan_summary),
            "relations": _canonical_digest(relation_summary),
            "deliveries": _canonical_digest(delivery_summary),
            "metric_contract": _canonical_digest(metric_contract),
            "runtime_topology_summary": _canonical_digest(runtime_summary),
            "source_snapshot": _canonical_digest(source_snapshot),
            "full_binding": _canonical_digest(digest_inputs),
        },
        _canonical_digest(_artifact_digest_input(artifact)),
    )


def _source_snapshot_check(source_snapshot: Mapping[str, Any]) -> bool:
    if set(source_snapshot) != {
        "source",
        "collected_at_utc",
        "git_commit",
        "git_commit_available",
    }:
        return False
    if source_snapshot.get("source") != "local_checkout":
        return False
    collected = source_snapshot.get("collected_at_utc")
    if not isinstance(collected, str) or not collected.endswith("Z"):
        return False
    commit_available = source_snapshot.get("git_commit_available")
    git_commit = source_snapshot.get("git_commit")
    if commit_available is True:
        return (
            isinstance(git_commit, str)
            and len(git_commit) == 40
            and all(char in "0123456789abcdef" for char in git_commit.lower())
        )
    return commit_available is False and git_commit is None


def _is_git_commit(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 40
        and all(char in "0123456789abcdef" for char in value.lower())
    )


def verify_shadow_subdivision_replay_artifact(
    artifact: Mapping[str, Any],
    *,
    expected_git_commit: str | None = None,
) -> dict:
    """Verify a replay artifact without trusting its declared ok flag.

    The verifier is local/offline and path-free. It recomputes every digest
    from the artifact summaries, checks the replay guardrails, optionally
    binds the source snapshot to an expected Git commit, and rejects unknown
    top-level fields so raw payloads or runtime config blobs cannot be
    smuggled into a verified replay.
    """

    blockers: list[str] = []
    if not isinstance(artifact, Mapping):
        return _json_error_report("artifact_not_object")

    artifact_dict = dict(artifact)
    unknown_keys = sorted(set(artifact_dict) - ARTIFACT_TOP_LEVEL_KEYS)
    missing_keys = sorted(
        key
        for key in ARTIFACT_TOP_LEVEL_KEYS
        if key != "blocked_reason" and key not in artifact_dict
    )
    for key in missing_keys:
        blockers.append(f"missing_field:{key}")
    for key in unknown_keys:
        blockers.append(f"unknown_field:{key}")

    shadow_plan_summary = _mapping_or_empty(
        artifact_dict.get("shadow_plan_summary")
    )
    relation_summary = _mapping_or_empty(artifact_dict.get("relation_summary"))
    delivery_summary = _mapping_or_empty(artifact_dict.get("delivery_summary"))
    runtime_summary = _mapping_or_empty(
        artifact_dict.get("runtime_topology_summary")
    )
    metric_contract = _mapping_or_empty(
        artifact_dict.get("metric_contract_summary")
    )
    source_snapshot = _mapping_or_empty(artifact_dict.get("source_snapshot"))
    guardrails = _mapping_or_empty(artifact_dict.get("guardrails"))
    declared_digests = _mapping_or_empty(artifact_dict.get("digests"))
    recomputed_digests, recomputed_artifact_digest = (
        _expected_artifact_digests(artifact_dict)
    )

    checks: dict[str, bool] = {
        "proof_identity": artifact_dict.get("proof_id") == PROOF_ID,
        "proof_type": artifact_dict.get("proof_type") == PROOF_TYPE,
        "binding_scope": (
            artifact_dict.get("binding_scope")
            == "structural_metrics_contract_only"
        ),
        "top_level_fields_known": not unknown_keys,
        "top_level_fields_present": not missing_keys,
        "shadow_plan_no_runtime_mutation": (
            shadow_plan_summary.get("no_runtime_mutation") is True
        ),
        "shadow_plan_target_state": (
            shadow_plan_summary.get("target_state")
            == "subdivision_in_shadow"
        ),
        "delivery_counts_consistent": (
            isinstance(delivery_summary.get("message_count"), int)
            and isinstance(delivery_summary.get("delivered_count"), int)
            and isinstance(delivery_summary.get("blocked_count"), int)
            and delivery_summary.get("message_count")
            == delivery_summary.get("delivered_count")
            and delivery_summary.get("blocked_count") == 0
        ),
        "runtime_dispatch_disabled": (
            runtime_summary.get("dispatch_enabled") is False
        ),
        "runtime_shadow_children_absent": (
            runtime_summary.get(
                "shadow_child_cell_ids_absent_from_runtime_config"
            )
            is True
        ),
        "metric_endpoint": metric_contract.get("metrics_endpoint")
        == "/metrics",
        "metric_status_code": metric_contract.get("status_code") == 200,
        "metric_payload_markers_absent": (
            metric_contract.get("forbidden_payload_markers_absent") is True
        ),
        "source_snapshot_path_free": (
            _source_snapshot_check(source_snapshot)
            and not _contains_path_marker(source_snapshot)
        ),
        "artifact_path_free": not _contains_path_marker(artifact_dict),
        "artifact_digest_match": (
            artifact_dict.get("artifact_digest") == recomputed_artifact_digest
        ),
    }
    checks["expected_git_commit_valid"] = (
        expected_git_commit is None or _is_git_commit(expected_git_commit)
    )
    checks["source_snapshot_git_commit_matches_expected"] = (
        expected_git_commit is None
        or source_snapshot.get("git_commit") == expected_git_commit
    )

    metric_names = set(_sequence_of_strings(metric_contract.get("metric_names")))
    expected_lines = _sequence_of_strings(metric_contract.get("expected_lines"))
    checks["required_metric_names_present"] = all(
        name in metric_names for name in TOPOLOGY_BOUNDARY_METRIC_NAMES
    )
    checks["required_metric_lines_present"] = all(
        any(name in line for line in expected_lines)
        for name in TOPOLOGY_BOUNDARY_METRIC_NAMES
    )

    for name in DIGEST_NAMES:
        checks[f"digest_{name}_match"] = (
            declared_digests.get(name) == recomputed_digests[name]
        )

    for name in GUARDRAIL_TRUE_FIELDS:
        checks[f"guardrail_{name}"] = guardrails.get(name) is True
    for name in GUARDRAIL_FALSE_FIELDS:
        checks[f"guardrail_{name}"] = guardrails.get(name) is False

    recomputed_contract_ok = all(
        checks[name]
        for name in (
            "proof_identity",
            "proof_type",
            "binding_scope",
            "shadow_plan_no_runtime_mutation",
            "shadow_plan_target_state",
            "delivery_counts_consistent",
            "runtime_dispatch_disabled",
            "runtime_shadow_children_absent",
            "metric_endpoint",
            "metric_status_code",
            "metric_payload_markers_absent",
            "required_metric_names_present",
            "required_metric_lines_present",
        )
    ) and all(
        checks[f"guardrail_{name}"]
        for name in (*GUARDRAIL_TRUE_FIELDS, *GUARDRAIL_FALSE_FIELDS)
    )
    checks["declared_ok_matches_recomputed_contract"] = (
        artifact_dict.get("ok") is recomputed_contract_ok
    )
    expected_git_commit_report = (
        expected_git_commit if _is_git_commit(expected_git_commit) else None
    )

    for name, passed in checks.items():
        if not passed:
            blockers.append(name)
    for name in DIGEST_NAMES:
        if not checks[f"digest_{name}_match"]:
            blockers.append(f"digest_mismatch:{name}")

    return {
        "proof_id": VERIFIER_PROOF_ID,
        "verified_proof_id": artifact_dict.get("proof_id"),
        "ok": not blockers,
        "artifact_declared_ok": artifact_dict.get("ok") is True,
        "expected_git_commit": expected_git_commit_report,
        "recomputed_contract_ok": recomputed_contract_ok,
        "blockers": sorted(set(blockers)),
        "checks": checks,
        "recomputed_digests": {
            **recomputed_digests,
            "artifact": recomputed_artifact_digest,
        },
        "declared_digests": {
            name: declared_digests.get(name) for name in DIGEST_NAMES
        }
        | {"artifact": artifact_dict.get("artifact_digest")},
        "guardrails": {
            name: guardrails.get(name)
            for name in (*GUARDRAIL_TRUE_FIELDS, *GUARDRAIL_FALSE_FIELDS)
        },
        "safe_conclusion": (
            "The verifier recomputes replay summary digests, the full binding "
            "digest, the artifact digest, and no-authority guardrails from "
            "the supplied artifact. It is a local offline check only and does "
            "not activate runtime subdivision authority."
        ),
    }


def _summary_error_report(blocker: str) -> dict:
    return {
        "proof_id": VERIFIER_SUMMARY_PROOF_ID,
        "ok": False,
        "created_at_utc": _utc_iso(datetime.now(timezone.utc)),
        "reviewer_ownership": {
            "reviewer_agent_id": "invalid_ref",
            "handoff_ref": "invalid_ref",
            "manual_review_required": True,
            "approval_granted": False,
            "runtime_subdivision_authority_granted": False,
        },
        "shadow_subdivision_replay_verification": {
            "verification_ok": False,
            "verified_proof_id": None,
            "blocker_count": 1,
            "blockers": [blocker],
        },
        "operator_boundary": {
            "manual_review_required": True,
            "approval_granted": False,
            "release_decision_made": False,
            "automatic_release_decision": False,
            "direct_bridge_write_performed": False,
            "transport_added": False,
            "external_fetch_performed": False,
            "runtime_controls_added": False,
            "runtime_subdivision_authority_granted": False,
            "artifact_payloads_included": False,
            "local_paths_recorded": False,
        },
        "reviewer_next_actions": [
            "review_shadow_subdivision_replay_verifier_report",
            "compare_summary_to_local_verifier_report",
            "record_operator_decision_separately",
        ],
        "manual_review_required": True,
        "approval_granted": False,
        "release_decision_made": False,
        "automatic_release_decision": False,
        "direct_bridge_write_performed": False,
        "transport_added": False,
        "external_fetch_performed": False,
        "runtime_controls_added": False,
        "runtime_subdivision_authority_granted": False,
        "artifact_payloads_included": False,
        "local_paths_recorded": False,
        "blockers": [blocker],
        "warnings": [],
        "safe_conclusion": (
            "The verifier summary failed closed before rendering reviewer "
            "context. It records no input path, writes no bridge event, and "
            "grants no runtime subdivision authority."
        ),
    }


def _verification_report_contract_blockers(
    verification_report: Mapping[str, Any],
) -> list[str]:
    blockers: list[str] = []
    checks = _mapping_or_empty(verification_report.get("checks"))
    guardrails = _mapping_or_empty(verification_report.get("guardrails"))

    if verification_report.get("proof_id") != VERIFIER_PROOF_ID:
        blockers.append("verification_report_proof_id_mismatch")
    if verification_report.get("verified_proof_id") != PROOF_ID:
        blockers.append("verification_report_verified_proof_id_mismatch")
    if verification_report.get("artifact_declared_ok") is not True:
        blockers.append("verification_report_artifact_declared_ok_not_true")
    if verification_report.get("recomputed_contract_ok") is not True:
        blockers.append("verification_report_recomputed_contract_ok_not_true")

    for name in (
        "artifact_path_free",
        "source_snapshot_path_free",
        "artifact_digest_match",
        "required_metric_names_present",
        "required_metric_lines_present",
        "declared_ok_matches_recomputed_contract",
        "expected_git_commit_valid",
        "source_snapshot_git_commit_matches_expected",
    ):
        if checks.get(name) is not True:
            blockers.append(f"verification_report_check_not_true:{name}")
    for name in DIGEST_NAMES:
        if checks.get(f"digest_{name}_match") is not True:
            blockers.append(f"verification_report_digest_not_match:{name}")

    for name in GUARDRAIL_TRUE_FIELDS:
        if guardrails.get(name) is not True:
            blockers.append(f"verification_report_guardrail_not_true:{name}")
    for name in GUARDRAIL_FALSE_FIELDS:
        if guardrails.get(name) is not False:
            blockers.append(f"verification_report_guardrail_not_false:{name}")

    expected_git_commit = verification_report.get("expected_git_commit")
    if expected_git_commit is not None and not _is_git_commit(expected_git_commit):
        blockers.append("verification_report_expected_git_commit_invalid")
    return blockers


def build_shadow_subdivision_replay_verifier_summary(
    verification_report: Mapping[str, Any],
    *,
    reviewer_agent_id: str,
    handoff_ref: str,
    now_utc: datetime | None = None,
) -> dict:
    """Render path-free reviewer context from a local replay verifier report."""

    if not isinstance(verification_report, Mapping):
        return _summary_error_report("verification_report_not_object")

    input_path_free = not _contains_path_marker(verification_report)
    report_blockers = _safe_token_list(verification_report.get("blockers"))
    report_warnings = _safe_token_list(verification_report.get("warnings"))
    contract_blockers = _verification_report_contract_blockers(
        verification_report
    )
    if not input_path_free:
        contract_blockers.append("verification_report_path_free")

    checks = _mapping_or_empty(verification_report.get("checks"))
    guardrails = _mapping_or_empty(verification_report.get("guardrails"))
    blockers = sorted(set(report_blockers + contract_blockers))
    verification_ok = (
        verification_report.get("ok") is True
        and verification_report.get("proof_id") == VERIFIER_PROOF_ID
    )
    reviewer_agent = _safe_ref_or_invalid(reviewer_agent_id)
    handoff = _safe_ref_or_invalid(handoff_ref)
    if reviewer_agent == "invalid_ref":
        blockers.append("reviewer_agent_id_invalid")
    if handoff == "invalid_ref":
        blockers.append("handoff_ref_invalid")
    blockers = sorted(set(blockers))

    summary = {
        "proof_id": VERIFIER_SUMMARY_PROOF_ID,
        "ok": verification_ok and not blockers,
        "created_at_utc": _utc_iso(now_utc or datetime.now(timezone.utc)),
        "reviewer_ownership": {
            "reviewer_agent_id": reviewer_agent,
            "handoff_ref": handoff,
            "manual_review_required": True,
            "approval_granted": False,
            "runtime_subdivision_authority_granted": False,
        },
        "shadow_subdivision_replay_verification": {
            "verification_ok": verification_ok,
            "verifier_proof_id": _safe_ref_or_invalid(
                verification_report.get("proof_id")
            ),
            "verified_proof_id": _safe_ref_or_invalid(
                verification_report.get("verified_proof_id")
            ),
            "expected_git_commit": (
                verification_report.get("expected_git_commit")
                if _is_git_commit(verification_report.get("expected_git_commit"))
                else None
            ),
            "artifact_declared_ok": (
                verification_report.get("artifact_declared_ok") is True
            ),
            "recomputed_contract_ok": (
                verification_report.get("recomputed_contract_ok") is True
            ),
            "digest_checks": {
                **{
                    name: _check_status(checks.get(f"digest_{name}_match"))
                    for name in DIGEST_NAMES
                },
                "artifact": _check_status(checks.get("artifact_digest_match")),
            },
            "contract_checks": {
                "artifact_path_free": _check_status(
                    checks.get("artifact_path_free")
                ),
                "source_snapshot_path_free": _check_status(
                    checks.get("source_snapshot_path_free")
                ),
                "required_metric_names_present": _check_status(
                    checks.get("required_metric_names_present")
                ),
                "required_metric_lines_present": _check_status(
                    checks.get("required_metric_lines_present")
                ),
                "declared_ok_matches_recomputed_contract": _check_status(
                    checks.get("declared_ok_matches_recomputed_contract")
                ),
                "source_snapshot_git_commit_matches_expected": _check_status(
                    checks.get("source_snapshot_git_commit_matches_expected")
                ),
            },
            "guardrails": {
                name: guardrails.get(name)
                for name in (*GUARDRAIL_TRUE_FIELDS, *GUARDRAIL_FALSE_FIELDS)
            },
            "blocker_count": len(report_blockers),
            "blockers": report_blockers,
            "warning_count": len(report_warnings),
            "warnings": report_warnings,
        },
        "operator_boundary": {
            "verification_report_boundary_ok": not contract_blockers,
            "boundary_blockers": sorted(set(contract_blockers)),
            "manual_review_required": True,
            "approval_granted": False,
            "release_decision_made": False,
            "automatic_release_decision": False,
            "direct_bridge_write_performed": False,
            "transport_added": False,
            "external_fetch_performed": False,
            "runtime_controls_added": False,
            "runtime_subdivision_authority_granted": False,
            "artifact_payloads_included": False,
            "local_paths_recorded": False,
        },
        "reviewer_next_actions": [
            "review_shadow_subdivision_replay_verifier_report",
            "compare_summary_to_local_verifier_report",
            "record_operator_decision_separately",
        ],
        "manual_review_required": True,
        "approval_granted": False,
        "release_decision_made": False,
        "automatic_release_decision": False,
        "direct_bridge_write_performed": False,
        "transport_added": False,
        "external_fetch_performed": False,
        "runtime_controls_added": False,
        "runtime_subdivision_authority_granted": False,
        "artifact_payloads_included": False,
        "local_paths_recorded": False,
        "blockers": blockers,
        "warnings": report_warnings,
        "safe_conclusion": (
            "This summary renders the local shadow subdivision replay "
            "verifier result as reviewer context only. It does not append a "
            "bridge event, transport artifacts, expose local paths, approve "
            "runtime subdivision authority, or change runtime topology."
        ),
    }
    if _contains_path_marker(summary):
        return _summary_error_report("summary_output_path_marker")
    json.dumps(summary, allow_nan=False, sort_keys=True)
    return summary


def _bridge_template_error_report(reason: str) -> dict:
    return {
        "proof_id": VERIFIER_SUMMARY_BRIDGE_EVENT_TEMPLATE_PROOF_ID,
        "ok": False,
        "template_version": VERIFIER_SUMMARY_BRIDGE_EVENT_TEMPLATE_VERSION,
        "template_only": True,
        "direct_bridge_write_performed": False,
        "automatic_release_decision": False,
        "approval_granted": False,
        "release_decision_made": False,
        "runtime_controls_added": False,
        "runtime_subdivision_authority_granted": False,
        "transport_added": False,
        "external_fetch_performed": False,
        "artifact_payloads_included": False,
        "local_paths_recorded": False,
        "blockers": [
            "shadow_subdivision_replay_verifier_summary_bridge_event_template_"
            f"failed:{_safe_token(reason)}"
        ],
        "warnings": [],
        "safe_conclusion": (
            "The bridge-event template failed closed before rendering. It "
            "does not append to the bridge, transport artifacts, record "
            "local paths, or grant runtime subdivision authority."
        ),
    }


def _validate_bridge_agent_id(label: str, value: Any) -> str | None:
    if isinstance(value, str) and AGENT_ID_PATTERN.fullmatch(value):
        return None
    return f"{label}_unsafe"


def _validate_bridge_targets(raw_targets: Any) -> tuple[str, str | None]:
    if not isinstance(raw_targets, str):
        return "", "to_unsafe"
    targets = [item.strip() for item in raw_targets.split(",") if item.strip()]
    if not targets:
        return "", "to_unsafe"
    for target in targets:
        error = _validate_bridge_agent_id("to", target)
        if error is not None:
            return "", error
    return ",".join(targets), None


def _bridge_template_input_error(
    *,
    agent_id: str,
    task_id: str,
    to: str,
    severity: str,
    role: str,
    run_id: str,
    session_id: str,
) -> str | None:
    error = _validate_bridge_agent_id("agent", agent_id)
    if error is not None:
        return error
    if not isinstance(task_id, str) or not SAFE_REF_PATTERN.fullmatch(task_id):
        return "task_id_unsafe"
    _, target_error = _validate_bridge_targets(to)
    if target_error is not None:
        return target_error
    if severity not in {"", "low", "medium", "high"}:
        return "severity_unsafe"
    if role:
        error = _validate_bridge_agent_id("role", role)
        if error is not None:
            return error
    if run_id and not SAFE_REF_PATTERN.fullmatch(run_id):
        return "run_id_unsafe"
    if session_id and not SESSION_ID_PATTERN.fullmatch(session_id):
        return "session_id_unsafe"
    return None


def _verifier_summary_contract_blockers(summary: Mapping[str, Any]) -> list[str]:
    blockers: list[str] = []
    if summary.get("ok") is not True:
        blockers.append("verifier_summary_not_ok")
    if summary.get("proof_id") != VERIFIER_SUMMARY_PROOF_ID:
        blockers.append("verifier_summary_proof_id_mismatch")
    if summary.get("manual_review_required") is not True:
        blockers.append("verifier_summary_manual_review_required_not_true")
    if _safe_token_list(summary.get("blockers")):
        blockers.append("verifier_summary_blockers_present")
    for field in SUMMARY_AUTHORITY_FALSE_FIELDS:
        if summary.get(field) is not False:
            blockers.append(f"verifier_summary_{field}_not_false")

    reviewer = _mapping_or_empty(summary.get("reviewer_ownership"))
    if _safe_ref_or_invalid(reviewer.get("reviewer_agent_id")) == "invalid_ref":
        blockers.append("reviewer_ownership_reviewer_agent_id_invalid")
    if _safe_ref_or_invalid(reviewer.get("handoff_ref")) == "invalid_ref":
        blockers.append("reviewer_ownership_handoff_ref_invalid")
    if reviewer.get("manual_review_required") is not True:
        blockers.append("reviewer_ownership_manual_review_required_not_true")
    if reviewer.get("approval_granted") is not False:
        blockers.append("reviewer_ownership_approval_granted_not_false")
    if reviewer.get("runtime_subdivision_authority_granted") is not False:
        blockers.append(
            "reviewer_ownership_runtime_subdivision_authority_granted_not_false"
        )

    verification = _mapping_or_empty(
        summary.get("shadow_subdivision_replay_verification")
    )
    if verification.get("verification_ok") is not True:
        blockers.append("verifier_summary_verification_not_ok")
    if verification.get("verifier_proof_id") != VERIFIER_PROOF_ID:
        blockers.append("verifier_summary_verifier_proof_id_mismatch")
    if verification.get("verified_proof_id") != PROOF_ID:
        blockers.append("verifier_summary_verified_proof_id_mismatch")
    if verification.get("artifact_declared_ok") is not True:
        blockers.append("verifier_summary_artifact_declared_ok_not_true")
    if verification.get("recomputed_contract_ok") is not True:
        blockers.append("verifier_summary_recomputed_contract_ok_not_true")
    if verification.get("blocker_count") != 0:
        blockers.append("verifier_summary_blocker_count_nonzero")
    if _safe_token_list(verification.get("blockers")):
        blockers.append("verifier_summary_verification_blockers_present")

    digest_checks = _mapping_or_empty(verification.get("digest_checks"))
    for name in (*DIGEST_NAMES, "artifact"):
        if digest_checks.get(name) != "match":
            blockers.append(f"verifier_summary_digest_check_not_match:{name}")
    contract_checks = _mapping_or_empty(verification.get("contract_checks"))
    for name in (
        "artifact_path_free",
        "source_snapshot_path_free",
        "required_metric_names_present",
        "required_metric_lines_present",
        "declared_ok_matches_recomputed_contract",
        "source_snapshot_git_commit_matches_expected",
    ):
        if contract_checks.get(name) != "match":
            blockers.append(f"verifier_summary_contract_check_not_match:{name}")
    guardrails = _mapping_or_empty(verification.get("guardrails"))
    for name in GUARDRAIL_TRUE_FIELDS:
        if guardrails.get(name) is not True:
            blockers.append(f"verifier_summary_guardrail_not_true:{name}")
    for name in GUARDRAIL_FALSE_FIELDS:
        if guardrails.get(name) is not False:
            blockers.append(f"verifier_summary_guardrail_not_false:{name}")
    expected_git_commit = verification.get("expected_git_commit")
    if expected_git_commit is not None and not _is_git_commit(expected_git_commit):
        blockers.append("verifier_summary_expected_git_commit_invalid")

    boundary = _mapping_or_empty(summary.get("operator_boundary"))
    if boundary.get("verification_report_boundary_ok") is not True:
        blockers.append("operator_boundary_verification_report_not_ok")
    if _safe_token_list(boundary.get("boundary_blockers")):
        blockers.append("operator_boundary_blockers_present")
    if boundary.get("manual_review_required") is not True:
        blockers.append("operator_boundary_manual_review_required_not_true")
    for field in SUMMARY_AUTHORITY_FALSE_FIELDS:
        if boundary.get(field) is not False:
            blockers.append(f"operator_boundary_{field}_not_false")
    return sorted(set(blockers))


def _match_status_map(value: Any, keys: Sequence[str]) -> dict[str, str]:
    raw = _mapping_or_empty(value)
    return {
        key: "match" if raw.get(key) == "match" else "unknown"
        for key in keys
    }


def build_shadow_subdivision_replay_verifier_summary_bridge_event_template(
    summary: Mapping[str, Any],
    *,
    agent_id: str,
    task_id: str,
    to: str = "operator,claude-rco-1,codex-tools-1",
    severity: str = "medium",
    role: str = "lead-impl",
    run_id: str = "",
    session_id: str = "",
    now_utc: datetime | None = None,
) -> dict:
    """Return a bridge-event template without appending it."""

    if not isinstance(summary, Mapping):
        return _bridge_template_error_report("verifier_summary_not_object")
    if _contains_path_marker(summary):
        return _bridge_template_error_report("verifier_summary_path_free")
    input_error = _bridge_template_input_error(
        agent_id=agent_id,
        task_id=task_id,
        to=to,
        severity=severity,
        role=role,
        run_id=run_id,
        session_id=session_id,
    )
    if input_error is not None:
        return _bridge_template_error_report(input_error)
    targets, _ = _validate_bridge_targets(to)

    contract_blockers = _verifier_summary_contract_blockers(summary)
    if contract_blockers:
        return _bridge_template_error_report(contract_blockers[0])

    verification = _mapping_or_empty(
        summary.get("shadow_subdivision_replay_verification")
    )
    reviewer = _mapping_or_empty(summary.get("reviewer_ownership"))
    boundary = _mapping_or_empty(summary.get("operator_boundary"))
    warnings = _safe_token_list(summary.get("warnings"))
    verification_warnings = _safe_token_list(verification.get("warnings"))
    guardrails = _mapping_or_empty(verification.get("guardrails"))
    payload = {
        "schema_version": VERIFIER_SUMMARY_BRIDGE_EVENT_TEMPLATE_VERSION,
        "summary_proof_id": VERIFIER_SUMMARY_PROOF_ID,
        "reviewer_ownership": {
            "reviewer_agent_id": _safe_ref_or_invalid(
                reviewer.get("reviewer_agent_id")
            ),
            "handoff_ref": _safe_ref_or_invalid(reviewer.get("handoff_ref")),
            "manual_review_required": True,
            "approval_granted": False,
            "runtime_subdivision_authority_granted": False,
        },
        "shadow_subdivision_replay_verification": {
            "verification_ok": True,
            "verifier_proof_id": VERIFIER_PROOF_ID,
            "verified_proof_id": PROOF_ID,
            "expected_git_commit": (
                verification.get("expected_git_commit")
                if _is_git_commit(verification.get("expected_git_commit"))
                else None
            ),
            "artifact_declared_ok": True,
            "recomputed_contract_ok": True,
            "digest_checks": _match_status_map(
                verification.get("digest_checks"),
                (*DIGEST_NAMES, "artifact"),
            ),
            "contract_checks": _match_status_map(
                verification.get("contract_checks"),
                (
                    "artifact_path_free",
                    "source_snapshot_path_free",
                    "required_metric_names_present",
                    "required_metric_lines_present",
                    "declared_ok_matches_recomputed_contract",
                    "source_snapshot_git_commit_matches_expected",
                ),
            ),
            "guardrails": {
                name: guardrails.get(name)
                for name in (*GUARDRAIL_TRUE_FIELDS, *GUARDRAIL_FALSE_FIELDS)
            },
            "blocker_count": 0,
            "warning_count": len(verification_warnings),
            "warnings": verification_warnings,
        },
        "operator_boundary": {
            "verification_report_boundary_ok": True,
            "manual_review_required": True,
            "approval_granted": False,
            "release_decision_made": False,
            "automatic_release_decision": False,
            "direct_bridge_write_performed": False,
            "transport_added": False,
            "external_fetch_performed": False,
            "runtime_controls_added": False,
            "runtime_subdivision_authority_granted": False,
            "artifact_payloads_included": False,
            "local_paths_recorded": False,
        },
        "template_only": True,
        "manual_review_required": True,
        "approval_granted": False,
        "release_decision_made": False,
        "automatic_release_decision": False,
        "direct_bridge_write_performed": False,
        "transport_added": False,
        "external_fetch_performed": False,
        "runtime_controls_added": False,
        "runtime_subdivision_authority_granted": False,
        "artifact_payloads_included": False,
        "local_paths_recorded": False,
    }
    event = {
        "ts_utc": _utc_iso(now_utc or datetime.now(timezone.utc)),
        "agent": agent_id,
        "type": "handoff",
        "task_id": task_id,
        "status": VERIFIER_SUMMARY_BRIDGE_EVENT_STATUS,
        "severity": severity,
        "to": targets,
        "message": (
            "Hex shadow subdivision replay verifier summary bridge-event "
            "template ready; manual_review_required=true; "
            "approval_granted=false; release_decision_made=false; "
            "runtime_subdivision_authority_granted=false; template_only=true; "
            "no bridge write, transport, external fetch, payload inclusion, "
            "local path recording, runtime controls, or topology mutation."
        ),
        "paths": [],
        "write_scope": [],
        "run_id": run_id,
        "role": role,
        "session_id": session_id,
        "capabilities": [
            "wd_image1",
            "hexagonal_upgrades",
            "bridge_event",
        ],
        "pid": 0,
        "cwd": "template_not_emitted",
        "payload": payload,
    }
    if _contains_path_marker(event):
        return _bridge_template_error_report("bridge_event_template_path_marker")
    json.dumps(event, allow_nan=False, sort_keys=True)
    return {
        "proof_id": VERIFIER_SUMMARY_BRIDGE_EVENT_TEMPLATE_PROOF_ID,
        "ok": True,
        "template_version": VERIFIER_SUMMARY_BRIDGE_EVENT_TEMPLATE_VERSION,
        "bridge_event_template": event,
        "template_only": True,
        "direct_bridge_write_performed": False,
        "automatic_release_decision": False,
        "approval_granted": False,
        "release_decision_made": False,
        "runtime_controls_added": False,
        "runtime_subdivision_authority_granted": False,
        "transport_added": False,
        "external_fetch_performed": False,
        "artifact_payloads_included": False,
        "local_paths_recorded": False,
        "blockers": [],
        "warnings": warnings,
    }


def _load_summary_verification_report(path: Path | str) -> tuple[dict | None, dict | None]:
    try:
        raw = Path(path).read_text(encoding="utf-8")
    except OSError:
        return None, _summary_error_report("verification_report_unreadable")
    try:
        report = json.loads(raw, parse_constant=_reject_json_constant)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None, _summary_error_report("verification_report_json_invalid")
    if not isinstance(report, Mapping):
        return None, _summary_error_report("verification_report_not_object")
    return dict(report), None


def _load_verifier_summary_report(path: Path | str) -> tuple[dict | None, dict | None]:
    try:
        raw = Path(path).read_text(encoding="utf-8")
    except OSError:
        return None, _bridge_template_error_report("verifier_summary_unreadable")
    try:
        summary = json.loads(raw, parse_constant=_reject_json_constant)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None, _bridge_template_error_report("verifier_summary_json_invalid")
    if not isinstance(summary, Mapping):
        return None, _bridge_template_error_report("verifier_summary_not_object")
    return dict(summary), None


def verify_shadow_subdivision_replay_json_file(
    path: Path | str,
    *,
    expected_git_commit: str | None = None,
) -> dict:
    """Load and verify a replay artifact without recording the input path."""

    try:
        raw = Path(path).read_text(encoding="utf-8")
    except OSError:
        return _json_error_report("artifact_unreadable")
    try:
        artifact = json.loads(raw, parse_constant=_reject_json_constant)
    except (TypeError, ValueError, json.JSONDecodeError):
        return _json_error_report("artifact_json_invalid")
    if not isinstance(artifact, Mapping):
        return _json_error_report("artifact_not_object")
    return verify_shadow_subdivision_replay_artifact(
        artifact,
        expected_git_commit=expected_git_commit,
    )


def _utc_iso(value: datetime) -> str:
    return (
        value.astimezone(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


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
    if not ok:
        artifact["blocked_reason"] = "upstream_proof_or_metric_contract_failed"
    artifact["artifact_digest"] = _canonical_digest(
        {key: value for key, value in artifact.items() if key != "artifact_digest"}
    )
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
        description=(
            "Emit or verify the read-only hex shadow subdivision replay "
            "artifact."
        )
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
        help="Exit 2 when the emitted artifact or verifier report does not pass.",
    )
    parser.add_argument(
        "--verify-json",
        type=Path,
        help=(
            "Verify an existing replay artifact JSON file. The report is "
            "path-free and does not record the input path."
        ),
    )
    parser.add_argument(
        "--summary-verification-json",
        type=Path,
        help=(
            "Render a path-free reviewer summary from an existing verifier "
            "report JSON file. The summary does not record the input path or "
            "append bridge events."
        ),
    )
    parser.add_argument(
        "--summary-bridge-event-template-json",
        type=Path,
        help=(
            "Render a template-only bridge event from an existing verifier "
            "summary JSON file. The template is printed but not appended."
        ),
    )
    parser.add_argument(
        "--expected-git-commit",
        help=(
            "Expected source_snapshot.git_commit for --verify-json. Defaults "
            "to the current --root HEAD when available."
        ),
    )
    parser.add_argument("--reviewer-agent", default="codex-tools-1")
    parser.add_argument(
        "--handoff-ref",
        default="hex-shadow-subdivision-replay-verifier-summary",
    )
    parser.add_argument("--agent", default="codex-lead-1")
    parser.add_argument(
        "--task-id",
        default="hex-shadow-subdivision-replay-verifier-summary-template",
    )
    parser.add_argument(
        "--to",
        default="operator,claude-rco-1,codex-tools-1",
    )
    parser.add_argument(
        "--severity",
        default="medium",
        choices=("", "low", "medium", "high"),
    )
    parser.add_argument("--role", default="lead-impl")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--session-id", default="")
    parser.add_argument(
        "--now",
        default=None,
        help="Optional UTC timestamp override such as 2026-05-31T12:00:00Z.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    mode_count = sum(
        mode is not None
        for mode in (
            args.verify_json,
            args.summary_verification_json,
            args.summary_bridge_event_template_json,
        )
    )
    if mode_count > 1:
        report = _bridge_template_error_report("multiple_modes_requested")
        print(json.dumps(report, indent=2, sort_keys=True))
        return 2

    if args.summary_bridge_event_template_json is not None:
        summary, failure = _load_verifier_summary_report(
            args.summary_bridge_event_template_json
        )
        if failure is not None:
            report = failure
        else:
            try:
                now_utc = _parse_utc(args.now) if args.now else None
            except ValueError:
                report = _bridge_template_error_report("now_utc_invalid")
            else:
                report = build_shadow_subdivision_replay_verifier_summary_bridge_event_template(
                summary or {},
                agent_id=args.agent,
                task_id=args.task_id,
                to=args.to,
                severity=args.severity,
                role=args.role,
                run_id=args.run_id,
                session_id=args.session_id,
                now_utc=now_utc,
            )
        print(json.dumps(report, indent=2, sort_keys=True))
        if args.strict and report.get("ok") is not True:
            return 2
        return 0

    if args.summary_verification_json is not None:
        report, failure = _load_summary_verification_report(
            args.summary_verification_json
        )
        summary = (
            failure
            if failure is not None
            else build_shadow_subdivision_replay_verifier_summary(
                report or {},
                reviewer_agent_id=args.reviewer_agent,
                handoff_ref=args.handoff_ref,
            )
        )
        print(json.dumps(summary, indent=2, sort_keys=True))
        if args.strict and summary.get("ok") is not True:
            return 2
        return 0

    if args.verify_json is not None:
        expected_git_commit = (
            args.expected_git_commit
            if args.expected_git_commit is not None
            else _git_head_commit(args.root)
        )
        report = verify_shadow_subdivision_replay_json_file(
            args.verify_json,
            expected_git_commit=expected_git_commit,
        )
        print(json.dumps(report, indent=2, sort_keys=True))
        if args.strict and report.get("ok") is not True:
            return 2
        return 0

    artifact = build_replay_artifact_for_root(args.root)
    print(json.dumps(artifact, indent=2, sort_keys=True))
    if args.strict and artifact.get("ok") is not True:
        return 2
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised by CLI smoke
    raise SystemExit(main())

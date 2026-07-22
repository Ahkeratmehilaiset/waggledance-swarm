#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""Build a non-authoritative bridge writer cutover evidence report.

This module is intentionally pure.  It reads one caller-supplied evidence
document and the fixed source configuration, validates them, and emits a HOLD
report.  It never discovers or mutates runtime, process, Scheduled Task, Git,
or bridge state.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import sys
from typing import Callable, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = "configs/bridge_writer_cutover_cutbook.v1.json"
CONFIG_SCHEMA = "wd.bridge_writer_cutover_cutbook_config.v1"
EVIDENCE_SCHEMA = "wd.bridge_writer_cutover_evidence.v1"
STATE_SCHEMA = "wd.bridge_writer_state_observation.v1"
INVENTORY_SCHEMA = "wd.bridge_writer_inventory_observation.v1"
VALIDATED_INVENTORY_SCHEMA = "wd.bridge_runtime.validated_inventory.v1"
CHECKPOINT_SCHEMA = "wd.bridge_writer_checkpoint_observation.v1"
LOCK_LIFECYCLE_SCHEMA = "wd.bridge_writer_lock_lifecycle_receipt.v1"
REPORT_SCHEMA = "wd.bridge_writer_cutover_cutbook.v1"
DECISION_HOLD = "HOLD_SOURCE_FOUNDATION_ONLY"

EXIT_ERROR = 2
EXIT_HOLD = 3
FULL_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
UTC_TIMESTAMP_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$"
)
RUNTIME_BLOB_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
ID_RE = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,127}$")
SID_RE = re.compile(r"^S-\d(?:-\d+)+$", re.IGNORECASE)

AUTHORITY_KEYS = frozenset(
    {
        "apply_allowed",
        "capability_grant_allowed",
        "deployment_allowed",
        "git_mutation_allowed",
        "process_mutation_allowed",
        "scheduled_task_mutation_allowed",
        "runtime_write_allowed",
        "bridge_append_allowed",
        "source_write_allowed",
        "merge_allowed",
        "claim_safe_flip_allowed",
        "operator_approval_collected",
    }
)
AUTHORITY = {key: False for key in sorted(AUTHORITY_KEYS)}

WRITER_LOCKS = (
    r"Global\WaggleDanceBridgeAppendV1",
    r"Global\WaggleDanceBridgeAppendV2",
)
REPLAYER_LOCKS = (
    r"Global\WaggleDanceBridgeSpoolReplayV1",
    *WRITER_LOCKS,
)
WRITER_COMPONENTS = (
    ".agent-bridge/bin/Restore-BridgeSpool.ps1",
    ".agent-bridge/bin/Write-AgentEvent.ps1",
    "tools/bridge_event_writer.py",
)
DIRECT_PYTHON_CALLERS = (
    "tools/bridge_loop_tick.py",
    "tools/close_bridge_rco_request.py",
    "tools/idle_protocol_activate.py",
)
DIRECT_WRAPPER_CALLERS = (
    ".agent-bridge/bin/Claim-AgentTask.ps1",
    ".agent-bridge/bin/Invoke-BridgeGit.ps1",
    ".agent-bridge/bin/Invoke-RoleReview.ps1",
    ".agent-bridge/bin/Invoke-StaleClaimSweep.ps1",
    ".agent-bridge/bin/Read-AgentBridge.ps1",
    ".agent-bridge/bin/Release-AgentTask.ps1",
    ".agent-bridge/bin/Send-Liveness.ps1",
    ".agent-bridge/bin/Start-AgentBridgeConsumerLoop.ps1",
    "tools/pr_bridge_wake.py",
)
REQUIRED_ENTRY_KEYS = {
    "process": (
        "action_id",
        "pid",
        "identity_sha256",
        "command_sha256",
        "closure_sha256",
        "entrypoint_blob_id",
        "dependency_blob_ids",
        "toolchain_ids",
    ),
    "scheduled_task": (
        "action_id",
        "identity_sha256",
        "action_sha256",
        "definition_sha256",
        "closure_sha256",
        "entrypoint_blob_id",
        "dependency_blob_ids",
        "toolchain_ids",
    ),
    "runtime_blob": ("id", "source_path", "sha256", "size"),
    "toolchain": ("id", "sha256", "size"),
    "cutbook_process_provenance": (
        "action_kind",
        "action_id",
        "identity_sha256",
        "command_or_action_sha256",
        "closure_sha256",
        "entrypoint_blob_id",
        "dependency_blob_ids",
        "toolchain_ids",
        "runtime_blobs",
        "toolchain",
        "exact_source_head",
        "origin",
        "provenance_sha256",
    ),
    "cutbook_scheduled_task_provenance": (
        "action_kind",
        "action_id",
        "identity_sha256",
        "command_or_action_sha256",
        "definition_sha256",
        "closure_sha256",
        "entrypoint_blob_id",
        "dependency_blob_ids",
        "toolchain_ids",
        "runtime_blobs",
        "toolchain",
        "exact_source_head",
        "origin",
        "provenance_sha256",
    ),
    "cutbook_runtime_blob": (
        "blob_id",
        "source_path",
        "source_blob_sha256",
        "runtime_blob_sha256",
        "size",
    ),
}
INCOMPLETE_SCOPE_REASON = "non_heuristic_process_task_scope_not_implemented"
ALLOWED_ORIGIN = "exact_head_runtime_install_provenance"
BLOCKED_ORIGINS = frozenset(
    {"linked_worktree", "legacy_direct_append", "unknown"}
)
CRITICAL_CLASSES = (
    "event-conservation-break",
    "lock-protocol-violation",
    "rollback-rehearsal-failure",
    "runtime-provenance-drift",
    "wal-corruption",
    "writer-identity-spoof",
)


class ContractError(ValueError):
    """A document is malformed or attempts to widen authority."""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build a source-only bridge writer cutover HOLD report."
    )
    parser.add_argument("--evidence-json", required=True, type=Path)
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = load_json_strict(ROOT / CONFIG_PATH, "cutbook config")
        evidence = load_json_strict(args.evidence_json, "cutbook evidence")
        report = build_bridge_writer_cutover_cutbook(evidence, config)
    except (ContractError, OSError, UnicodeError) as exc:
        print(f"cutbook FAILED: {exc}", file=sys.stderr)
        return EXIT_ERROR

    if args.json:
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    else:
        print(report["decision"])
        for blocker in report["blockers"]:
            print(f"{blocker['code']}: {blocker['detail']}")
    return EXIT_HOLD


def load_json_strict(path: Path, label: str) -> dict[str, object]:
    try:
        raw = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ContractError(f"{label} must be strict UTF-8") from exc
    try:
        value = json.loads(
            raw,
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=lambda token: _reject_json_constant(token),
        )
    except json.JSONDecodeError as exc:
        raise ContractError(f"{label} is invalid JSON: {exc.msg}") from exc
    if not isinstance(value, dict):
        raise ContractError(f"{label} must be an object")
    return value


def _reject_duplicate_pairs(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ContractError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_json_constant(token: str) -> object:
    raise ContractError(f"non-finite JSON number is forbidden: {token}")


def canonical_digest(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_bridge_writer_cutover_cutbook(
    evidence: Mapping[str, object],
    config: Mapping[str, object],
) -> dict[str, object]:
    """Validate evidence and return a deterministic, non-authoritative HOLD."""

    _validate_config(config)
    normalized = _validate_evidence(evidence)
    blockers: list[dict[str, str]] = []

    def checked(fn: Callable[[], None]) -> bool:
        before = len(blockers)
        fn()
        return len(blockers) == before

    exact_head = str(normalized["exact_source_head"])
    provenance_ok = checked(
        lambda: _audit_provenance(normalized, exact_head, blockers)
    )
    inventory_ok = checked(
        lambda: _audit_inventories(normalized, exact_head, blockers)
    )
    conservation_consistency_ok = checked(
        lambda: _audit_conservation(normalized, exact_head, blockers)
    )
    lock_lifecycle_consistency_ok = checked(
        lambda: _audit_lock_lifecycle(normalized, config, exact_head, blockers)
    )
    rule_10_ok = checked(lambda: _audit_rule_10(normalized, exact_head, blockers))
    downstream_blockers: list[dict[str, str]] = []
    _audit_downstream_receipts(
        normalized, exact_head, downstream_blockers
    )
    receipt_candidate_ok = not downstream_blockers
    _block(
        downstream_blockers,
        "receipt_authentication_not_implemented",
        "a sealed post-cutover receipt-index verifier is not implemented",
    )
    receipts_ok = False

    _block(
        blockers,
        "wal_replay_order_attestation_not_implemented",
        "finals-then-pendings filename order lacks an authenticated replay plan",
    )
    conservation_ok = False

    _block(
        blockers,
        "lock_lifecycle_authentication_not_implemented",
        "the lock-lifecycle receipt has no sealed collector verifier",
    )
    _block(
        blockers,
        "quiet_window_actor_attestation_not_implemented",
        "ordinary-writer stop and attested-replayer-only proof is not implemented",
    )

    _block(
        blockers,
        "incomplete_scope_proof",
        INCOMPLETE_SCOPE_REASON,
    )
    _block(
        blockers,
        "source_foundation_only",
        "this pure artifact cannot authorize or execute writer cutover",
    )

    downstream = normalized["downstream_receipts"]
    assert isinstance(downstream, Mapping)
    report = {
        "schema": REPORT_SCHEMA,
        "decision": DECISION_HOLD,
        "exit_code": EXIT_HOLD,
        "ok": False,
        "exact_source_head": exact_head,
        "config_sha256": canonical_digest(config),
        "evidence_sha256": canonical_digest(evidence),
        "checks": {
            "provenance": provenance_ok,
            "inventory": inventory_ok,
            "event_wal_conservation": conservation_ok,
            "event_wal_conservation_consistency": conservation_consistency_ok,
            "rule_10": rule_10_ok,
            "lock_lifecycle": False,
            "lock_lifecycle_consistency": lock_lifecycle_consistency_ok,
            "quiet_window_actor_attestation": False,
            "downstream_receipts": receipts_ok,
        },
        "scope_proof": {
            "complete": False,
            "reason": INCOMPLETE_SCOPE_REASON,
        },
        "downstream_claim_gates": {
            "qualified": receipts_ok,
            "candidate_qualified": receipt_candidate_ok,
            "served_total": downstream["served_total"],
            "served_with_receipt_total": downstream[
                "served_with_receipt_total"
            ],
            "solver_first_served_total": downstream[
                "solver_first_served_total"
            ],
            "claim_safe_effect": "none",
            "blockers": sorted(
                downstream_blockers,
                key=lambda item: (item["code"], item["detail"]),
            ),
        },
        "blockers": sorted(
            blockers,
            key=lambda item: (item["code"], item["detail"]),
        ),
        "authority": dict(AUTHORITY),
    }
    return report


def _validate_config(config: Mapping[str, object]) -> None:
    _exact_keys(
        config,
        {
            "schema",
            "activation_state",
            "lock_protocol",
            "stream_policy",
            "inventory_policy",
            "quiet_window",
            "rule_10",
            "downstream_receipt_gate",
            "authority",
        },
        "config",
    )
    _literal(config["schema"], CONFIG_SCHEMA, "config.schema")
    _literal(
        config["activation_state"],
        "hold_source_foundation_only",
        "config.activation_state",
    )

    locks = _object(config["lock_protocol"], "config.lock_protocol")
    _exact_keys(
        locks,
        {
            "replay_guard",
            "append_deadline_ms",
            "writer_acquire_order",
            "replayer_append_acquire_order",
            "release_order_reverse",
        },
        "config.lock_protocol",
    )
    replay_guard = _object(locks["replay_guard"], "config replay guard")
    _exact_keys(replay_guard, {"name", "timeout_ms"}, "config replay guard")
    _literal(
        replay_guard["name"], REPLAYER_LOCKS[0], "config replay guard name"
    )
    _exact_int(replay_guard["timeout_ms"], 0, "config replay guard timeout")
    _exact_int(
        locks["append_deadline_ms"], 10000, "config append lock deadline"
    )
    _literal_list(
        locks["writer_acquire_order"], WRITER_LOCKS, "writer lock order"
    )
    _literal_list(
        locks["replayer_append_acquire_order"],
        WRITER_LOCKS,
        "replayer append lock order",
    )
    _exact_bool(
        locks["release_order_reverse"], True, "reverse lock release"
    )

    stream = _object(config["stream_policy"], "config.stream_policy")
    _exact_keys(
        stream,
        {
            "generation_change_allowed",
            "rotation_allowed",
            "rewrite_allowed",
        },
        "config.stream_policy",
    )
    for key, value in stream.items():
        _exact_bool(value, False, f"config.stream_policy.{key}")

    inventory = _object(config["inventory_policy"], "config.inventory_policy")
    _exact_keys(
        inventory,
        {
            "writer_components",
            "direct_python_callers",
            "direct_wrapper_callers",
            "required_entry_keys",
            "allowed_runtime_origin",
            "blocked_runtime_origins",
            "scope_proof",
        },
        "config.inventory_policy",
    )
    _literal_list(
        inventory["writer_components"], WRITER_COMPONENTS, "writer components"
    )
    _literal_list(
        inventory["direct_python_callers"],
        DIRECT_PYTHON_CALLERS,
        "direct Python callers",
    )
    _literal_list(
        inventory["direct_wrapper_callers"],
        DIRECT_WRAPPER_CALLERS,
        "direct wrapper callers",
    )
    entry_keys = _object(
        inventory["required_entry_keys"], "config required entry keys"
    )
    _exact_keys(
        entry_keys, set(REQUIRED_ENTRY_KEYS), "config required entry keys"
    )
    for entry_kind, expected_keys in REQUIRED_ENTRY_KEYS.items():
        _literal_list(
            entry_keys[entry_kind], expected_keys, f"{entry_kind} entry keys"
        )
    _literal(
        inventory["allowed_runtime_origin"],
        ALLOWED_ORIGIN,
        "allowed runtime origin",
    )
    blocked = _string_list(
        inventory["blocked_runtime_origins"], "blocked runtime origins"
    )
    if set(blocked) != BLOCKED_ORIGINS or len(blocked) != len(BLOCKED_ORIGINS):
        raise ContractError("blocked runtime origins do not match the contract")
    _validate_incomplete_scope(inventory["scope_proof"], "config scope proof")

    quiet = _object(config["quiet_window"], "config.quiet_window")
    _exact_keys(
        quiet,
        {
            "attested_replayer_only",
            "orphan_writer_pid_target",
            "pending_wal_file_target",
            "pending_wal_record_target",
            "final_replayable_wal_file_target",
            "final_replayable_wal_record_target",
            "quarantine_growth_target",
        },
        "config.quiet_window",
    )
    _exact_bool(quiet["attested_replayer_only"], True, "attested replayer")
    for key in (
        "orphan_writer_pid_target",
        "pending_wal_file_target",
        "pending_wal_record_target",
        "final_replayable_wal_file_target",
        "final_replayable_wal_record_target",
        "quarantine_growth_target",
    ):
        _exact_int(quiet[key], 0, f"config.quiet_window.{key}")

    rule = _object(config["rule_10"], "config.rule_10")
    _exact_keys(
        rule,
        {
            "detection_target_bps",
            "min_cases_per_critical_class",
            "critical_classes",
            "rollback_max_ms",
            "rollback_max_scheduler_ticks",
            "require_train_holdout_disjoint",
            "require_executed_drill",
            "require_post_cutover_rehearsal",
        },
        "config.rule_10",
    )
    for key, expected in {
        "detection_target_bps": 10000,
        "min_cases_per_critical_class": 2,
        "rollback_max_ms": 60000,
        "rollback_max_scheduler_ticks": 1,
    }.items():
        _exact_int(rule[key], expected, f"config.rule_10.{key}")
    _literal_list(
        rule["critical_classes"], CRITICAL_CLASSES, "Rule-10 critical classes"
    )
    for key in (
        "require_train_holdout_disjoint",
        "require_executed_drill",
        "require_post_cutover_rehearsal",
    ):
        _exact_bool(rule[key], True, f"config.rule_10.{key}")

    receipts = _object(
        config["downstream_receipt_gate"], "config.downstream_receipt_gate"
    )
    _exact_keys(
        receipts,
        {
            "served_min",
            "receipt_coverage_min_bps",
            "solver_first_min_bps",
            "evidence_max_age_days",
            "claim_safe_effect",
        },
        "config.downstream_receipt_gate",
    )
    for key, expected in {
        "served_min": 10000,
        "receipt_coverage_min_bps": 9500,
        "solver_first_min_bps": 9500,
        "evidence_max_age_days": 14,
    }.items():
        _exact_int(receipts[key], expected, f"config receipt gate {key}")
    _literal(receipts["claim_safe_effect"], "none", "claim-safe effect")
    _validate_authority(config["authority"], "config.authority")


def _validate_evidence(evidence: Mapping[str, object]) -> Mapping[str, object]:
    _exact_keys(
        evidence,
        {
            "schema",
            "exact_source_head",
            "captured_at_utc",
            "pre_freeze_inventory",
            "quiet_start_state",
            "post_drain_state",
            "post_start_inventory",
            "provenance",
            "rule_10",
            "lock_lifecycle_receipt",
            "downstream_receipts",
            "authority",
        },
        "evidence",
    )
    _literal(evidence["schema"], EVIDENCE_SCHEMA, "evidence.schema")
    head = _commit(evidence["exact_source_head"], "evidence exact source head")
    _timestamp(evidence["captured_at_utc"], "evidence capture time")
    _validate_inventory(evidence["pre_freeze_inventory"], "pre-freeze", head)
    _validate_state(evidence["quiet_start_state"], "quiet-start")
    _validate_state(evidence["post_drain_state"], "post-drain")
    _validate_inventory(evidence["post_start_inventory"], "post-start", head)
    _validate_provenance(evidence["provenance"])
    _validate_rule_10(evidence["rule_10"])
    _validate_lock_lifecycle_receipt(evidence["lock_lifecycle_receipt"])
    _validate_receipts(evidence["downstream_receipts"])
    _validate_authority(evidence["authority"], "evidence.authority")
    return evidence


def _validate_inventory(raw: object, label: str, head: str) -> None:
    inventory = _object(raw, f"{label} inventory")
    _exact_keys(
        inventory,
        {
            "schema",
            "exact_source_head",
            "captured_at_utc",
            "host_identity_sha256",
            "boot_id_sha256",
            "inventory_sha256",
            "validated_inventory_sha256",
            "validated_inventory",
            "writer_instances",
            "scope_proof",
        },
        f"{label} inventory",
    )
    _literal(inventory["schema"], INVENTORY_SCHEMA, f"{label} schema")
    _commit(inventory["exact_source_head"], f"{label} source head")
    _timestamp(inventory["captured_at_utc"], f"{label} capture time")
    for key in (
        "host_identity_sha256",
        "boot_id_sha256",
        "inventory_sha256",
        "validated_inventory_sha256",
    ):
        _sha256(inventory[key], f"{label}.{key}")
    _validate_validated_inventory(
        inventory["validated_inventory"], f"{label} validated inventory"
    )
    _validate_incomplete_scope(inventory["scope_proof"], f"{label} scope")
    writers = _list(inventory["writer_instances"], f"{label} writer instances")
    for index, raw_writer in enumerate(writers):
        writer = _object(raw_writer, f"{label} writer {index}")
        _exact_keys(
            writer,
            {
                "action_id",
                "pid",
                "identity_sha256",
                "origin",
                "exact_source_head",
                "provenance_sha256",
            },
            f"{label} writer {index}",
        )
        _entity_id(writer["action_id"], f"{label} writer action")
        _positive_int(writer["pid"], f"{label} writer pid")
        _sha256(writer["identity_sha256"], f"{label} writer identity")
        _identifier(writer["origin"], f"{label} writer origin")
        _commit(writer["exact_source_head"], f"{label} writer head")
        _sha256(writer["provenance_sha256"], f"{label} writer provenance")
    if inventory["exact_source_head"] != head:
        # A mismatch is a semantic blocker, but the value must still be exact.
        return


def _validate_validated_inventory(raw: object, label: str) -> None:
    projection = _object(raw, label)
    _exact_keys(
        projection,
        {
            "schema",
            "exact_source_head",
            "host_identity_sha256",
            "boot_id_sha256",
            "captures",
            "processes",
            "scheduled_tasks",
            "runtime_blobs",
            "toolchain",
            "scope_proof",
            "inventory_sha256",
        },
        label,
    )
    _literal(projection["schema"], VALIDATED_INVENTORY_SCHEMA, f"{label}.schema")
    _commit(projection["exact_source_head"], f"{label}.exact_source_head")
    _sha256(projection["host_identity_sha256"], f"{label}.host")
    _sha256(projection["boot_id_sha256"], f"{label}.boot")
    _sha256(projection["inventory_sha256"], f"{label}.inventory_sha256")
    _validate_incomplete_scope(projection["scope_proof"], f"{label}.scope")

    captures = _list(projection["captures"], f"{label}.captures")
    capture_labels: list[str] = []
    capture_times: list[datetime] = []
    capture_digests: list[str] = []
    for index, raw_capture in enumerate(captures):
        capture = _object(raw_capture, f"{label}.capture[{index}]")
        _exact_keys(
            capture,
            {"label", "captured_at_utc", "sample_sha256"},
            f"{label}.capture[{index}]",
        )
        capture_labels.append(_string(capture["label"], "capture label"))
        capture_times.append(
            _timestamp(capture["captured_at_utc"], "capture timestamp")
        )
        capture_digests.append(_sha256(capture["sample_sha256"], "capture digest"))
    if (
        capture_labels != ["A", "B"]
        or len(capture_times) != 2
        or capture_times[0] >= capture_times[1]
        or len(capture_digests) != len(set(capture_digests))
    ):
        raise ContractError(f"{label} must contain ordered A/B captures")

    processes = _list(projection["processes"], f"{label}.processes")
    if not processes:
        raise ContractError(f"{label}.processes must not be empty")
    process_keys: list[tuple[str, int, str]] = []
    seen_pids: set[int] = set()
    seen_process_identities: set[str] = set()
    for index, raw_process in enumerate(processes):
        process = _object(raw_process, f"{label}.process[{index}]")
        _exact_keys(
            process,
            set(REQUIRED_ENTRY_KEYS["process"]),
            f"{label}.process[{index}]",
        )
        action_id = _entity_id(process["action_id"], "process action")
        pid = _positive_int(process["pid"], "process pid")
        identity = _sha256(process["identity_sha256"], "process identity")
        _sha256(process["command_sha256"], "process command")
        _sha256(process["closure_sha256"], "process closure")
        _blob_id(process["entrypoint_blob_id"], "process entrypoint")
        _validate_blob_id_set(
            process["dependency_blob_ids"], "process dependencies", nonempty=True
        )
        _validate_blob_id_set(
            process["toolchain_ids"], "process toolchain ids", nonempty=True
        )
        if pid in seen_pids or identity in seen_process_identities:
            raise ContractError(f"{label} contains a duplicate process identity")
        seen_pids.add(pid)
        seen_process_identities.add(identity)
        process_keys.append((action_id, pid, identity))
    if process_keys != sorted(process_keys):
        raise ContractError(f"{label}.processes must be canonical")

    tasks = _list(projection["scheduled_tasks"], f"{label}.scheduled_tasks")
    if not tasks:
        raise ContractError(f"{label}.scheduled_tasks must not be empty")
    task_keys: list[tuple[str, str]] = []
    task_action_ids: set[str] = set()
    task_identities: set[str] = set()
    for index, raw_task in enumerate(tasks):
        task = _object(raw_task, f"{label}.scheduled_task[{index}]")
        _exact_keys(
            task,
            set(REQUIRED_ENTRY_KEYS["scheduled_task"]),
            f"{label}.scheduled_task[{index}]",
        )
        action_id = _entity_id(task["action_id"], "scheduled task action")
        identity = _sha256(task["identity_sha256"], "scheduled task identity")
        _sha256(task["action_sha256"], "scheduled task action digest")
        _sha256(task["definition_sha256"], "scheduled task definition")
        _sha256(task["closure_sha256"], "scheduled task closure")
        _blob_id(task["entrypoint_blob_id"], "scheduled task entrypoint")
        _validate_blob_id_set(
            task["dependency_blob_ids"],
            "scheduled task dependencies",
            nonempty=True,
        )
        _validate_blob_id_set(
            task["toolchain_ids"],
            "scheduled task toolchain ids",
            nonempty=True,
        )
        task_keys.append((action_id, identity))
        if action_id in task_action_ids or identity in task_identities:
            raise ContractError(f"{label}.scheduled_tasks contains a duplicate")
        task_action_ids.add(action_id)
        task_identities.add(identity)
    if task_keys != sorted(task_keys):
        raise ContractError(f"{label}.scheduled_tasks must be unique and canonical")

    process_identity_hashes = sorted(key[2] for key in process_keys)
    task_identity_hashes = sorted(key[1] for key in task_keys)
    for raw_capture in captures:
        capture = _object(raw_capture, "validated capture")
        expected_sample_digest = canonical_digest(
            {
                "boot_id_sha256": projection["boot_id_sha256"],
                "captured_at_utc": capture["captured_at_utc"],
                "host_identity_sha256": projection["host_identity_sha256"],
                "label": capture["label"],
                "process_identity_sha256s": process_identity_hashes,
                "task_identity_sha256s": task_identity_hashes,
            }
        )
        if capture["sample_sha256"] != expected_sample_digest:
            raise ContractError(f"{label} capture digest is not rederivable")

    runtime_blobs = _list(projection["runtime_blobs"], f"{label}.runtime_blobs")
    if not runtime_blobs:
        raise ContractError(f"{label}.runtime_blobs must not be empty")
    runtime_ids: list[str] = []
    for index, raw_blob in enumerate(runtime_blobs):
        blob = _object(raw_blob, f"{label}.runtime_blob[{index}]")
        _exact_keys(
            blob,
            set(REQUIRED_ENTRY_KEYS["runtime_blob"]),
            f"{label}.runtime_blob[{index}]",
        )
        runtime_ids.append(_entity_id(blob["id"], "runtime blob id"))
        _source_path(blob["source_path"], "runtime source path")
        _sha256(blob["sha256"], "runtime blob digest")
        _positive_int(blob["size"], "runtime blob size")
    if len(runtime_ids) != len(set(runtime_ids)) or runtime_ids != sorted(runtime_ids):
        raise ContractError(f"{label}.runtime_blobs must be unique and canonical")
    runtime_paths = {
        str(_object(raw, "runtime blob")["source_path"])
        for raw in runtime_blobs
    }
    if not set(WRITER_COMPONENTS).issubset(runtime_paths):
        raise ContractError(f"{label} omits a canonical writer component")

    toolchain = _list(projection["toolchain"], f"{label}.toolchain")
    if not toolchain:
        raise ContractError(f"{label}.toolchain must not be empty")
    tool_ids: list[str] = []
    for index, raw_tool in enumerate(toolchain):
        tool = _object(raw_tool, f"{label}.toolchain[{index}]")
        _exact_keys(
            tool,
            set(REQUIRED_ENTRY_KEYS["toolchain"]),
            f"{label}.toolchain[{index}]",
        )
        tool_ids.append(_entity_id(tool["id"], "toolchain id"))
        _sha256(tool["sha256"], "toolchain digest")
        _positive_int(tool["size"], "toolchain size")
    if len(tool_ids) != len(set(tool_ids)) or tool_ids != sorted(tool_ids):
        raise ContractError(f"{label}.toolchain must be unique and canonical")


def _validate_blob_id_set(
    raw: object, label: str, *, nonempty: bool = False
) -> list[str]:
    values = [_blob_id(value, label) for value in _list(raw, label)]
    if (
        (nonempty and not values)
        or len(values) != len(set(values))
        or values != sorted(values)
    ):
        raise ContractError(f"{label} must be unique and canonical")
    return values


def _validate_state(raw: object, label: str) -> None:
    state = _object(raw, f"{label} state")
    _exact_keys(
        state,
        {
            "schema",
            "captured_at_utc",
            "host_identity_sha256",
            "boot_id_sha256",
            "inventory_sha256",
            "canonical",
            "checkpoint",
            "wal",
            "quarantine_count",
            "unknown_append_count",
        },
        f"{label} state",
    )
    _literal(state["schema"], STATE_SCHEMA, f"{label} state schema")
    _timestamp(state["captured_at_utc"], f"{label} capture time")
    for key in ("host_identity_sha256", "boot_id_sha256", "inventory_sha256"):
        _sha256(state[key], f"{label}.{key}")
    _nonnegative_int(state["quarantine_count"], f"{label} quarantine count")
    _nonnegative_int(state["unknown_append_count"], f"{label} unknown appends")

    canonical = _object(state["canonical"], f"{label} canonical")
    _exact_keys(
        canonical,
        {
            "file_identity_sha256",
            "length",
            "record_count",
            "tail_anchor_sha256",
            "utf8_valid",
            "jsonl_valid",
            "lf_terminated",
            "row_sha256s",
            "suffix_row_sha256s",
        },
        f"{label} canonical",
    )
    for key in (
        "file_identity_sha256",
        "tail_anchor_sha256",
    ):
        _sha256(canonical[key], f"{label}.canonical.{key}")
    _nonnegative_int(canonical["length"], f"{label} canonical length")
    _nonnegative_int(
        canonical["record_count"], f"{label} canonical record count"
    )
    for key in ("utf8_valid", "jsonl_valid", "lf_terminated"):
        _boolean(canonical[key], f"{label}.canonical.{key}")
    for key in ("row_sha256s", "suffix_row_sha256s"):
        for index, digest in enumerate(
            _list(canonical[key], f"{label}.canonical.{key}")
        ):
            _sha256(digest, f"{label}.canonical.{key}[{index}]")

    checkpoint = _object(state["checkpoint"], f"{label} checkpoint")
    _exact_keys(
        checkpoint,
        {
            "schema",
            "file_identity_sha256",
            "length",
            "tail_anchor_sha256",
            "matches",
        },
        f"{label} checkpoint",
    )
    _literal(
        checkpoint["schema"], CHECKPOINT_SCHEMA, f"{label} checkpoint schema"
    )
    _sha256(
        checkpoint["file_identity_sha256"], f"{label} checkpoint identity"
    )
    _nonnegative_int(checkpoint["length"], f"{label} checkpoint length")
    _sha256(checkpoint["tail_anchor_sha256"], f"{label} checkpoint tail")
    _boolean(checkpoint["matches"], f"{label} checkpoint match")

    wal = _object(state["wal"], f"{label} WAL")
    _exact_keys(
        wal,
        {
            "pending_file_count",
            "pending_record_count",
            "final_replayable_file_count",
            "final_replayable_record_count",
            "replay_plan_sha256",
            "rows",
        },
        f"{label} WAL",
    )
    _nonnegative_int(wal["pending_file_count"], f"{label} pending WAL files")
    _nonnegative_int(wal["pending_record_count"], f"{label} pending WAL")
    _nonnegative_int(
        wal["final_replayable_file_count"], f"{label} final WAL files"
    )
    _nonnegative_int(
        wal["final_replayable_record_count"], f"{label} final WAL"
    )
    _sha256(wal["replay_plan_sha256"], f"{label} replay plan")
    rows = _list(wal["rows"], f"{label} WAL rows")
    for index, raw_row in enumerate(rows):
        row = _object(raw_row, f"{label} WAL row {index}")
        _exact_keys(
            row,
            {
                "wal_file_identity_sha256",
                "file_ordinal",
                "source_kind",
                "source_order_sha256",
                "row_index",
                "row_sha256",
                "classification",
            },
            f"{label} WAL row",
        )
        _sha256(
            row["wal_file_identity_sha256"], f"{label} WAL file identity"
        )
        _nonnegative_int(row["file_ordinal"], f"{label} WAL file ordinal")
        source_kind = _string(row["source_kind"], f"{label} WAL source kind")
        if source_kind not in {"final", "pending"}:
            raise ContractError(f"{label} WAL source kind is invalid")
        _sha256(row["source_order_sha256"], f"{label} WAL source order")
        _nonnegative_int(row["row_index"], f"{label} WAL row index")
        _sha256(row["row_sha256"], f"{label} WAL row digest")
        classification = _string(row["classification"], "WAL classification")
        if classification not in {
            "replayed",
            "deduped_existing",
            "deduped_within_wal",
        }:
            raise ContractError(f"{label} WAL classification is invalid")


def _validate_provenance(raw: object) -> None:
    entries = _list(raw, "provenance")
    for index, raw_entry in enumerate(entries):
        entry = _object(raw_entry, f"provenance {index}")
        action_kind = _string(entry.get("action_kind"), "provenance action kind")
        if action_kind == "process":
            entry_contract = "cutbook_process_provenance"
        elif action_kind == "scheduled_task":
            entry_contract = "cutbook_scheduled_task_provenance"
        else:
            raise ContractError("provenance action kind is invalid")
        _exact_keys(
            entry,
            set(REQUIRED_ENTRY_KEYS[entry_contract]),
            f"provenance {index}",
        )
        _entity_id(entry["action_id"], "provenance action")
        for key in (
            "identity_sha256",
            "command_or_action_sha256",
            "closure_sha256",
            "provenance_sha256",
        ):
            _sha256(entry[key], f"provenance.{key}")
        if action_kind == "scheduled_task":
            _sha256(entry["definition_sha256"], "provenance task definition")
        _blob_id(entry["entrypoint_blob_id"], "provenance entrypoint blob")
        _validate_blob_id_set(
            entry["dependency_blob_ids"], "dependency blobs", nonempty=True
        )
        toolchain_ids = _validate_blob_id_set(
            entry["toolchain_ids"],
            "provenance toolchain ids",
            nonempty=True,
        )
        runtime_blobs = _list(entry["runtime_blobs"], "provenance runtime blobs")
        for blob_index, raw_blob in enumerate(runtime_blobs):
            blob = _object(raw_blob, f"provenance runtime blob {blob_index}")
            _exact_keys(
                blob,
                set(REQUIRED_ENTRY_KEYS["cutbook_runtime_blob"]),
                f"provenance runtime blob {blob_index}",
            )
            _blob_id(blob["blob_id"], "provenance runtime blob id")
            _source_path(blob["source_path"], "provenance runtime source path")
            _sha256(blob["source_blob_sha256"], "provenance source blob")
            _sha256(blob["runtime_blob_sha256"], "provenance runtime blob")
            _positive_int(blob["size"], "provenance runtime blob size")
        toolchain = _list(entry["toolchain"], "provenance toolchain")
        observed_tool_ids: list[str] = []
        for tool_index, raw_tool in enumerate(toolchain):
            tool = _object(raw_tool, f"provenance toolchain {tool_index}")
            _exact_keys(
                tool,
                set(REQUIRED_ENTRY_KEYS["toolchain"]),
                f"provenance toolchain {tool_index}",
            )
            observed_tool_ids.append(_blob_id(tool["id"], "provenance tool id"))
            _sha256(tool["sha256"], "provenance tool digest")
            _positive_int(tool["size"], "provenance tool size")
        if observed_tool_ids != toolchain_ids:
            raise ContractError("provenance toolchain does not match toolchain ids")
        _commit(entry["exact_source_head"], "provenance source head")
        _identifier(entry["origin"], "provenance origin")


def _validate_rule_10(raw: object) -> None:
    rule = _object(raw, "rule_10")
    _exact_keys(
        rule,
        {
            "corpus_sha256",
            "training_case_ids",
            "critical_classes",
            "heldout_cases",
            "rollback_drill",
            "post_cutover_rehearsal",
            "exact_head_consensus_passed",
        },
        "rule_10",
    )
    _sha256(rule["corpus_sha256"], "Rule-10 corpus digest")
    for value in _list(rule["training_case_ids"], "training cases"):
        _identifier(value, "training case")
    for value in _list(rule["critical_classes"], "critical classes"):
        _identifier(value, "critical class")
    for index, raw_case in enumerate(
        _list(rule["heldout_cases"], "held-out cases")
    ):
        case = _object(raw_case, f"held-out case {index}")
        _exact_keys(case, {"case_id", "class", "detected"}, "held-out case")
        _identifier(case["case_id"], "held-out case id")
        _identifier(case["class"], "held-out class")
        _boolean(case["detected"], "held-out detection")
    drill = _object(rule["rollback_drill"], "rollback drill")
    _exact_keys(
        drill,
        {"artifact_kind", "executed", "passed", "elapsed_ms", "scheduler_ticks"},
        "rollback drill",
    )
    _identifier(drill["artifact_kind"], "rollback artifact kind")
    _boolean(drill["executed"], "rollback executed")
    _boolean(drill["passed"], "rollback passed")
    _nonnegative_int(drill["elapsed_ms"], "rollback elapsed")
    _nonnegative_int(drill["scheduler_ticks"], "rollback scheduler ticks")
    rehearsal = _object(rule["post_cutover_rehearsal"], "rehearsal")
    _exact_keys(
        rehearsal,
        {"executed", "passed", "exact_source_head"},
        "rehearsal",
    )
    _boolean(rehearsal["executed"], "rehearsal executed")
    _boolean(rehearsal["passed"], "rehearsal passed")
    _commit(rehearsal["exact_source_head"], "rehearsal head")
    _boolean(rule["exact_head_consensus_passed"], "exact-head consensus")


def _validate_receipts(raw: object) -> None:
    receipts = _object(raw, "downstream receipts")
    _exact_keys(
        receipts,
        {
            "telemetry_source",
            "exact_source_head",
            "window_id",
            "lifecycle_id",
            "clean_marker_sha256",
            "index_sha256",
            "captured_at_utc",
            "served_total",
            "served_with_receipt_total",
            "solver_first_served_total",
            "gap_total",
            "unresolved_total",
            "pending_failure_total",
            "denominator_total",
            "served_event_identity_sha256s",
            "receipt_event_identity_sha256s",
            "solver_first_event_identity_sha256s",
            "gap_event_identity_sha256s",
            "unresolved_event_identity_sha256s",
            "pending_failure_event_identity_sha256s",
        },
        "downstream receipts",
    )
    _identifier(receipts["telemetry_source"], "telemetry source")
    _commit(receipts["exact_source_head"], "receipt source head")
    _identifier(receipts["window_id"], "receipt window")
    _identifier(receipts["lifecycle_id"], "receipt lifecycle")
    _sha256(receipts["clean_marker_sha256"], "receipt clean marker")
    _sha256(receipts["index_sha256"], "receipt index")
    _timestamp(receipts["captured_at_utc"], "receipt capture time")
    for key in (
        "served_total",
        "served_with_receipt_total",
        "solver_first_served_total",
        "gap_total",
        "unresolved_total",
        "pending_failure_total",
        "denominator_total",
    ):
        _nonnegative_int(receipts[key], f"downstream receipts.{key}")
    for key in (
        "served_event_identity_sha256s",
        "receipt_event_identity_sha256s",
        "solver_first_event_identity_sha256s",
        "gap_event_identity_sha256s",
        "unresolved_event_identity_sha256s",
        "pending_failure_event_identity_sha256s",
    ):
        for index, identity in enumerate(
            _list(receipts[key], f"downstream receipts.{key}")
        ):
            _sha256(identity, f"downstream receipts.{key}[{index}]")


def _validate_lock_lifecycle_receipt(raw: object) -> None:
    receipt = _object(raw, "lock lifecycle receipt")
    _exact_keys(
        receipt,
        {
            "schema",
            "exact_source_head",
            "replayer_action_id",
            "replayer_provenance_sha256",
            "quiet_start_state_canonical_sha256",
            "post_drain_state_canonical_sha256",
            "quiet_window_started_at_utc",
            "quiet_window_ended_at_utc",
            "append_deadline_ms",
            "append_deadline_started_at_utc",
            "append_deadline_expires_at_utc",
            "outcome",
            "events",
            "captured_at_utc",
            "receipt_canonical_sha256",
        },
        "lock lifecycle receipt",
    )
    _literal(
        receipt["schema"],
        LOCK_LIFECYCLE_SCHEMA,
        "lock lifecycle receipt schema",
    )
    _commit(receipt["exact_source_head"], "lock lifecycle source head")
    _entity_id(receipt["replayer_action_id"], "lock lifecycle replayer action")
    for key in (
        "replayer_provenance_sha256",
        "quiet_start_state_canonical_sha256",
        "post_drain_state_canonical_sha256",
        "receipt_canonical_sha256",
    ):
        _sha256(receipt[key], f"lock lifecycle receipt.{key}")
    for key in (
        "quiet_window_started_at_utc",
        "quiet_window_ended_at_utc",
        "append_deadline_started_at_utc",
        "append_deadline_expires_at_utc",
        "captured_at_utc",
    ):
        _timestamp(receipt[key], f"lock lifecycle receipt.{key}")
    _nonnegative_int(
        receipt["append_deadline_ms"],
        "lock lifecycle receipt.append_deadline_ms",
    )
    _identifier(receipt["outcome"], "lock lifecycle receipt outcome")
    events = _list(receipt["events"], "lock lifecycle events")
    if not events:
        raise ContractError("lock lifecycle events must not be empty")
    for index, raw_event in enumerate(events):
        event = _object(raw_event, f"lock lifecycle event {index}")
        _exact_keys(
            event,
            {"sequence", "at_utc", "operation", "subject", "timeout_ms", "result"},
            f"lock lifecycle event {index}",
        )
        _nonnegative_int(event["sequence"], f"lock lifecycle event {index} sequence")
        _timestamp(event["at_utc"], f"lock lifecycle event {index} time")
        _identifier(event["operation"], f"lock lifecycle event {index} operation")
        _string(event["subject"], f"lock lifecycle event {index} subject")
        _nonnegative_int(event["timeout_ms"], f"lock lifecycle event {index} timeout")
        _identifier(event["result"], f"lock lifecycle event {index} result")


def _audit_provenance(
    evidence: Mapping[str, object],
    exact_head: str,
    blockers: list[dict[str, str]],
) -> None:
    entries = _list(evidence["provenance"], "provenance")
    seen_bindings: set[tuple[str, str]] = set()
    for raw_entry in entries:
        entry = _object(raw_entry, "provenance entry")
        action_id = str(entry["action_id"])
        binding = (action_id, str(entry["identity_sha256"]))
        if binding in seen_bindings:
            _block(blockers, "duplicate_provenance_binding", action_id)
        seen_bindings.add(binding)
        if entry["exact_source_head"] != exact_head:
            _block(blockers, "provenance_head_mismatch", action_id)
        if entry["origin"] != ALLOWED_ORIGIN:
            _block(blockers, "inadmissible_runtime_origin", action_id)
        dependencies = list(entry["dependency_blob_ids"])
        if len(dependencies) != len(set(dependencies)) or dependencies != sorted(
            dependencies
        ):
            _block(blockers, "dependency_blob_set_invalid", action_id)
        if entry["entrypoint_blob_id"] not in dependencies:
            _block(
                blockers,
                "entrypoint_missing_from_dependency_closure",
                action_id,
            )
        expected_blob_ids = dependencies
        runtime_blobs = _list(entry["runtime_blobs"], "runtime blobs")
        observed_blob_ids = [
            _object(raw_blob, "runtime blob")["blob_id"]
            for raw_blob in runtime_blobs
        ]
        if (
            len(observed_blob_ids) != len(set(observed_blob_ids))
            or observed_blob_ids != sorted(observed_blob_ids)
            or set(observed_blob_ids) != set(expected_blob_ids)
        ):
            _block(blockers, "runtime_blob_binding_invalid", action_id)
        for raw_blob in runtime_blobs:
            blob = _object(raw_blob, "runtime blob")
            if blob["source_blob_sha256"] != blob["runtime_blob_sha256"]:
                _block(
                    blockers,
                    "runtime_source_blob_mismatch",
                    f"{action_id}:{blob['blob_id']}",
                )
        digest_input = {
            key: value
            for key, value in entry.items()
            if key != "provenance_sha256"
        }
        if entry["provenance_sha256"] != canonical_digest(digest_input):
            _block(blockers, "provenance_digest_mismatch", action_id)


def _audit_inventories(
    evidence: Mapping[str, object],
    exact_head: str,
    blockers: list[dict[str, str]],
) -> None:
    provenance_entries = _list(evidence["provenance"], "provenance")
    provenance = {
        (str(entry["action_id"]), str(entry["identity_sha256"])): entry
        for entry in provenance_entries
        if isinstance(entry, Mapping)
    }
    inventories = (
        ("pre_freeze", evidence["pre_freeze_inventory"]),
        ("post_start", evidence["post_start_inventory"]),
    )
    host_boot: tuple[object, object] | None = None
    definition_digest: str | None = None
    observed_bindings: set[tuple[str, str]] = set()
    for label, raw_inventory in inventories:
        inventory = _object(raw_inventory, f"{label} inventory")
        projection = _object(
            inventory["validated_inventory"], f"{label} validated inventory"
        )
        if inventory["exact_source_head"] != exact_head:
            _block(blockers, "inventory_head_mismatch", label)
        current_host_boot = (
            inventory["host_identity_sha256"],
            inventory["boot_id_sha256"],
        )
        if host_boot is None:
            host_boot = current_host_boot
        elif current_host_boot != host_boot:
            _block(blockers, "inventory_host_boot_drift", label)
        digest_input = {
            key: value
            for key, value in inventory.items()
            if key != "inventory_sha256"
        }
        if inventory["inventory_sha256"] != canonical_digest(digest_input):
            _block(blockers, "inventory_digest_mismatch", label)
        projection_digest_input = {
            key: value
            for key, value in projection.items()
            if key != "inventory_sha256"
        }
        if projection["inventory_sha256"] != canonical_digest(
            projection_digest_input
        ):
            _block(blockers, "validated_inventory_digest_mismatch", label)
        if (
            inventory["validated_inventory_sha256"]
            != projection["inventory_sha256"]
        ):
            _block(blockers, "validated_inventory_binding_mismatch", label)
        if projection["exact_source_head"] != inventory["exact_source_head"]:
            _block(blockers, "validated_inventory_head_mismatch", label)
        if (
            projection["host_identity_sha256"],
            projection["boot_id_sha256"],
        ) != current_host_boot:
            _block(blockers, "validated_inventory_host_boot_mismatch", label)
        captures = _list(projection["captures"], "validated captures")
        final_capture = _timestamp(
            _object(captures[-1], "validated final capture")["captured_at_utc"],
            "validated final capture time",
        )
        inventory_capture = _timestamp(
            inventory["captured_at_utc"], "inventory capture time"
        )
        if final_capture > inventory_capture:
            _block(blockers, "validated_inventory_capture_order_invalid", label)
        current_definition_digest = _projection_definition_digest(projection)
        if definition_digest is None:
            definition_digest = current_definition_digest
        elif current_definition_digest != definition_digest:
            _block(blockers, "validated_inventory_definition_drift", label)

        runtime_blobs = {
            str(blob["id"]): blob
            for blob in _list(projection["runtime_blobs"], "runtime blobs")
            if isinstance(blob, Mapping)
        }
        toolchain = {
            str(tool["id"]): tool
            for tool in _list(projection["toolchain"], "toolchain")
            if isinstance(tool, Mapping)
        }
        projected_processes: set[tuple[object, object, object]] = set()
        for raw_process in _list(projection["processes"], "processes"):
            process = _object(raw_process, "process")
            process_key = (
                process["action_id"],
                process["pid"],
                process["identity_sha256"],
            )
            projected_processes.add(process_key)
            binding = (
                str(process["action_id"]),
                str(process["identity_sha256"]),
            )
            observed_bindings.add(binding)
            _audit_projection_provenance_binding(
                action=process,
                digest_field="command_sha256",
                provenance=provenance.get(binding),
                runtime_blobs=runtime_blobs,
                toolchain=toolchain,
                label=f"{label}:process:{binding[0]}",
                blockers=blockers,
            )
        for raw_task in _list(projection["scheduled_tasks"], "scheduled tasks"):
            task = _object(raw_task, "scheduled task")
            binding = (
                str(task["action_id"]),
                str(task["identity_sha256"]),
            )
            observed_bindings.add(binding)
            _audit_projection_provenance_binding(
                action=task,
                digest_field="action_sha256",
                provenance=provenance.get(binding),
                runtime_blobs=runtime_blobs,
                toolchain=toolchain,
                label=f"{label}:scheduled_task:{binding[0]}",
                blockers=blockers,
            )

        seen_pids: set[int] = set()
        seen_identities: set[str] = set()
        observed_writer_processes: set[tuple[object, object, object]] = set()
        for raw_writer in _list(inventory["writer_instances"], "writers"):
            writer = _object(raw_writer, "writer")
            pid = int(writer["pid"])
            identity = str(writer["identity_sha256"])
            if pid in seen_pids or identity in seen_identities:
                _block(blockers, "duplicate_writer_identity", label)
            seen_pids.add(pid)
            seen_identities.add(identity)
            action_id = str(writer["action_id"])
            origin = str(writer["origin"])
            if origin in BLOCKED_ORIGINS or origin != ALLOWED_ORIGIN:
                _block(blockers, "legacy_or_unknown_writer", f"{label}:{action_id}")
            if writer["exact_source_head"] != exact_head:
                _block(blockers, "writer_head_mismatch", f"{label}:{action_id}")
            binding = (action_id, identity)
            bound = provenance.get(binding)
            writer_process = (
                writer["action_id"],
                writer["pid"],
                writer["identity_sha256"],
            )
            observed_writer_processes.add(writer_process)
            if (
                bound is None
                or writer["provenance_sha256"] != bound["provenance_sha256"]
                or writer_process not in projected_processes
            ):
                _block(blockers, "orphan_writer", f"{label}:{action_id}")
        if observed_writer_processes != projected_processes:
            _block(blockers, "writer_projection_coverage_mismatch", label)
    if set(provenance) != observed_bindings:
        _block(blockers, "provenance_inventory_coverage_mismatch", "inventory")


def _projection_definition_digest(projection: Mapping[str, object]) -> str:
    process_definitions = [
        {
            key: value
            for key, value in _object(raw, "projected process").items()
            if key not in {"pid", "identity_sha256"}
        }
        for raw in _list(projection["processes"], "projected processes")
    ]
    task_definitions = [
        {
            key: value
            for key, value in _object(raw, "projected task").items()
            if key != "identity_sha256"
        }
        for raw in _list(projection["scheduled_tasks"], "projected tasks")
    ]
    process_definitions.sort(key=canonical_digest)
    task_definitions.sort(key=canonical_digest)
    return canonical_digest(
        {
            "processes": process_definitions,
            "runtime_blobs": projection["runtime_blobs"],
            "scheduled_tasks": task_definitions,
            "toolchain": projection["toolchain"],
        }
    )


def _audit_projection_provenance_binding(
    *,
    action: Mapping[str, object],
    digest_field: str,
    provenance: Mapping[str, object] | None,
    runtime_blobs: Mapping[str, Mapping[str, object]],
    toolchain: Mapping[str, Mapping[str, object]],
    label: str,
    blockers: list[dict[str, str]],
) -> None:
    if provenance is None:
        _block(blockers, "missing_projection_provenance", label)
        return
    expected_kind = (
        "process" if digest_field == "command_sha256" else "scheduled_task"
    )
    if (
        provenance["action_kind"] != expected_kind
        or provenance["command_or_action_sha256"] != action[digest_field]
        or provenance["closure_sha256"] != action["closure_sha256"]
        or provenance["entrypoint_blob_id"] != action["entrypoint_blob_id"]
        or provenance["dependency_blob_ids"] != action["dependency_blob_ids"]
        or provenance["toolchain_ids"] != action["toolchain_ids"]
    ):
        _block(blockers, "projection_action_provenance_mismatch", label)
    if digest_field == "action_sha256" and (
        provenance.get("definition_sha256") != action["definition_sha256"]
    ):
        _block(blockers, "projection_task_definition_mismatch", label)
    for raw_binding in _list(provenance["runtime_blobs"], "provenance blobs"):
        binding = _object(raw_binding, "provenance blob")
        runtime = runtime_blobs.get(str(binding["blob_id"]))
        if (
            runtime is None
            or runtime["source_path"] != binding["source_path"]
            or runtime["sha256"] != binding["source_blob_sha256"]
            or runtime["sha256"] != binding["runtime_blob_sha256"]
            or runtime["size"] != binding["size"]
        ):
            _block(
                blockers,
                "projection_runtime_blob_provenance_mismatch",
                f"{label}:{binding['blob_id']}",
            )
    for raw_binding in _list(provenance["toolchain"], "provenance tools"):
        binding = _object(raw_binding, "provenance tool")
        projected = toolchain.get(str(binding["id"]))
        if projected != binding:
            _block(
                blockers,
                "projection_toolchain_provenance_mismatch",
                f"{label}:{binding['id']}",
            )


def _audit_conservation(
    evidence: Mapping[str, object],
    exact_head: str,
    blockers: list[dict[str, str]],
) -> None:
    pre_inventory = _object(evidence["pre_freeze_inventory"], "pre inventory")
    post_inventory = _object(evidence["post_start_inventory"], "post inventory")
    quiet = _object(evidence["quiet_start_state"], "quiet state")
    post = _object(evidence["post_drain_state"], "post-drain state")

    capture_times = [
        _timestamp(pre_inventory["captured_at_utc"], "pre-freeze capture"),
        _timestamp(quiet["captured_at_utc"], "quiet-start capture"),
        _timestamp(post["captured_at_utc"], "post-drain capture"),
        _timestamp(post_inventory["captured_at_utc"], "post-start capture"),
        _timestamp(evidence["captured_at_utc"], "evidence capture"),
    ]
    if any(
        earlier >= later
        for earlier, later in zip(capture_times, capture_times[1:])
    ):
        _block(blockers, "cutover_capture_order_invalid", "quiet_window")

    expected_host_boot = (
        pre_inventory["host_identity_sha256"],
        pre_inventory["boot_id_sha256"],
    )
    for label, observation in (
        ("quiet", quiet),
        ("post_drain", post),
        ("post_start", post_inventory),
    ):
        observed = (
            observation["host_identity_sha256"],
            observation["boot_id_sha256"],
        )
        if observed != expected_host_boot:
            _block(blockers, "quiet_window_host_boot_drift", label)

    if quiet["inventory_sha256"] != pre_inventory["inventory_sha256"]:
        _block(blockers, "quiet_inventory_binding_mismatch", "quiet_start")
    if post["inventory_sha256"] != pre_inventory["inventory_sha256"]:
        _block(blockers, "quiet_inventory_binding_mismatch", "post_drain")
    if post_inventory["exact_source_head"] != exact_head:
        _block(blockers, "post_start_head_mismatch", exact_head)
    post_projection = _object(
        post_inventory["validated_inventory"], "post-start validated inventory"
    )
    post_projection_captures = _list(
        post_projection["captures"], "post-start validated captures"
    )
    first_post_start_capture = _timestamp(
        _object(post_projection_captures[0], "post-start capture A")[
            "captured_at_utc"
        ],
        "post-start capture A time",
    )
    if first_post_start_capture <= capture_times[2]:
        _block(
            blockers,
            "post_start_projection_precedes_drain",
            "post_start_inventory",
        )

    quiet_canonical = _object(quiet["canonical"], "quiet canonical")
    post_canonical = _object(post["canonical"], "post canonical")
    for label, state, canonical in (
        ("quiet_start", quiet, quiet_canonical),
        ("post_drain", post, post_canonical),
    ):
        for flag in ("utf8_valid", "jsonl_valid", "lf_terminated"):
            if canonical[flag] is not True:
                _block(blockers, "canonical_format_invalid", f"{label}:{flag}")
        rows = list(canonical["row_sha256s"])
        if canonical["record_count"] != len(rows):
            _block(blockers, "canonical_record_count_mismatch", label)
        if state["unknown_append_count"] != 0:
            _block(blockers, "unknown_canonical_append", label)
        checkpoint = _object(state["checkpoint"], f"{label} checkpoint")
        if (
            checkpoint["matches"] is not True
            or checkpoint["file_identity_sha256"]
            != canonical["file_identity_sha256"]
            or checkpoint["length"] != canonical["length"]
            or checkpoint["tail_anchor_sha256"]
            != canonical["tail_anchor_sha256"]
        ):
            _block(blockers, "checkpoint_mismatch", label)

    if list(quiet_canonical["suffix_row_sha256s"]):
        _block(blockers, "quiet_start_suffix_not_empty", "quiet_start")

    if (
        post_canonical["file_identity_sha256"]
        != quiet_canonical["file_identity_sha256"]
    ):
        _block(blockers, "canonical_file_identity_drift", "post_drain")
    if post_canonical["length"] < quiet_canonical["length"]:
        _block(blockers, "canonical_stream_shrank", "post_drain")

    quiet_wal = _object(quiet["wal"], "quiet WAL")
    post_wal = _object(post["wal"], "post WAL")
    quiet_wal_rows = _list(quiet_wal["rows"], "quiet WAL rows")
    post_wal_rows = _list(post_wal["rows"], "post WAL rows")
    if quiet_wal_rows != post_wal_rows:
        _block(blockers, "wal_row_inventory_drift", "post_drain")
    if quiet_wal["pending_record_count"] != len(quiet_wal_rows):
        _block(blockers, "pending_wal_count_mismatch", "quiet_start")
    if post_wal["pending_file_count"] != 0:
        _block(blockers, "pending_wal_files_after_drain", "post_drain")
    if post_wal["pending_record_count"] != 0:
        _block(blockers, "pending_wal_after_drain", "post_drain")
    if post_wal["final_replayable_file_count"] != 0:
        _block(blockers, "final_replayable_wal_files_after_drain", "post_drain")
    if post_wal["final_replayable_record_count"] != 0:
        _block(blockers, "final_replayable_wal_after_drain", "post_drain")

    pre_rows = list(quiet_canonical["row_sha256s"])
    existing = set(pre_rows)
    new_rows: list[str] = []
    occurrences: set[tuple[object, object]] = set()
    files: dict[int, list[Mapping[str, object]]] = {}
    file_identities: dict[int, str] = {}
    identity_ordinals: dict[str, int] = {}
    file_kinds: dict[int, str] = {}
    file_order_hashes: dict[int, str] = {}
    order_hash_ordinals: dict[str, int] = {}
    for raw_row in quiet_wal_rows:
        row = _object(raw_row, "WAL row")
        file_ordinal = int(row["file_ordinal"])
        row_index = int(row["row_index"])
        file_identity = str(row["wal_file_identity_sha256"])
        source_kind = str(row["source_kind"])
        source_order_sha256 = str(row["source_order_sha256"])
        occurrence = (row["wal_file_identity_sha256"], row["row_index"])
        if occurrence in occurrences:
            _block(blockers, "duplicate_wal_occurrence", str(occurrence))
        occurrences.add(occurrence)
        if file_ordinal in file_identities and (
            file_identities[file_ordinal] != file_identity
        ):
            _block(blockers, "wal_file_identity_drift", str(file_ordinal))
        if file_identity in identity_ordinals and (
            identity_ordinals[file_identity] != file_ordinal
        ):
            _block(blockers, "wal_file_identity_reused", file_identity)
        file_identities[file_ordinal] = file_identity
        identity_ordinals[file_identity] = file_ordinal
        if file_ordinal in file_kinds and file_kinds[file_ordinal] != source_kind:
            _block(blockers, "wal_source_kind_drift", str(file_ordinal))
        if (
            file_ordinal in file_order_hashes
            and file_order_hashes[file_ordinal] != source_order_sha256
        ):
            _block(blockers, "wal_source_order_binding_drift", str(file_ordinal))
        if (
            source_order_sha256 in order_hash_ordinals
            and order_hash_ordinals[source_order_sha256] != file_ordinal
        ):
            _block(blockers, "wal_source_order_binding_reused", source_order_sha256)
        file_kinds[file_ordinal] = source_kind
        file_order_hashes[file_ordinal] = source_order_sha256
        order_hash_ordinals[source_order_sha256] = file_ordinal
        files.setdefault(file_ordinal, []).append(row)

    if sorted(files) != list(range(len(files))):
        _block(blockers, "wal_file_ordinal_invalid", "quiet_start")
    ordered_kinds = [file_kinds[ordinal] for ordinal in sorted(files)]
    if ordered_kinds != sorted(
        ordered_kinds, key=lambda value: 0 if value == "final" else 1
    ):
        _block(blockers, "wal_source_kind_order_invalid", "quiet_start")
    replay_plan = [
        {
            "file_ordinal": ordinal,
            "source_kind": file_kinds[ordinal],
            "source_order_sha256": file_order_hashes[ordinal],
            "wal_file_identity_sha256": file_identities[ordinal],
        }
        for ordinal in sorted(files)
    ]
    expected_replay_plan_sha256 = canonical_digest(replay_plan)
    if quiet_wal["replay_plan_sha256"] != expected_replay_plan_sha256:
        _block(blockers, "wal_replay_plan_digest_mismatch", "quiet_start")
    if post_wal["replay_plan_sha256"] != expected_replay_plan_sha256:
        _block(blockers, "wal_replay_plan_digest_mismatch", "post_drain")
    expected_occurrence_order = [
        (file_ordinal, int(row["row_index"]))
        for file_ordinal in sorted(files)
        for row in sorted(files[file_ordinal], key=lambda item: int(item["row_index"]))
    ]
    observed_occurrence_order = [
        (int(_object(row, "WAL row")["file_ordinal"]), int(row["row_index"]))
        for row in quiet_wal_rows
    ]
    if observed_occurrence_order != expected_occurrence_order:
        _block(blockers, "wal_occurrence_order_invalid", "quiet_start")

    replayable_file_count = 0
    for file_ordinal in sorted(files):
        file_rows = sorted(files[file_ordinal], key=lambda item: int(item["row_index"]))
        if [int(row["row_index"]) for row in file_rows] != list(
            range(len(file_rows))
        ):
            _block(blockers, "wal_row_index_invalid", str(file_ordinal))
        file_new: set[str] = set()
        for row in file_rows:
            digest = str(row["row_sha256"])
            expected_classification: str
            if digest in existing:
                expected_classification = "deduped_existing"
            elif digest in file_new:
                expected_classification = "deduped_within_wal"
            else:
                expected_classification = "replayed"
                file_new.add(digest)
                new_rows.append(digest)
            if row["classification"] != expected_classification:
                _block(blockers, "wal_classification_mismatch", digest)
        if file_new:
            replayable_file_count += 1
            existing.update(file_new)

    expected_post_rows = pre_rows + new_rows
    if quiet_wal["pending_file_count"] != len(files):
        _block(blockers, "pending_wal_file_count_mismatch", "quiet_start")
    if quiet_wal["final_replayable_record_count"] != len(new_rows):
        _block(blockers, "replayable_wal_count_mismatch", "quiet_start")
    if quiet_wal["final_replayable_file_count"] != replayable_file_count:
        _block(blockers, "replayable_wal_file_count_mismatch", "quiet_start")
    if list(post_canonical["row_sha256s"]) != expected_post_rows:
        _block(blockers, "canonical_row_conservation_failed", "post_drain")
    if post_canonical["record_count"] != len(expected_post_rows):
        _block(blockers, "post_record_conservation_failed", "post_drain")
    if new_rows and post_canonical["length"] <= quiet_canonical["length"]:
        _block(blockers, "canonical_length_did_not_advance", "post_drain")
    post_rows = list(post_canonical["row_sha256s"])
    if post_rows[: len(pre_rows)] != pre_rows:
        _block(blockers, "canonical_prefix_mismatch", "post_drain")
    if list(post_canonical["suffix_row_sha256s"]) != new_rows:
        _block(blockers, "canonical_suffix_mismatch", "post_drain")
    if post["quarantine_count"] > quiet["quarantine_count"]:
        _block(blockers, "quarantine_growth", "post_drain")


def _audit_rule_10(
    evidence: Mapping[str, object],
    exact_head: str,
    blockers: list[dict[str, str]],
) -> None:
    rule = _object(evidence["rule_10"], "rule_10")
    training_values = _string_list(rule["training_case_ids"], "training cases")
    training = set(training_values)
    if (
        not training_values
        or len(training_values) != len(training)
        or training_values != sorted(training_values)
    ):
        _block(blockers, "training_case_set_invalid", "rule_10")
    critical = _string_list(rule["critical_classes"], "critical classes")
    if critical != list(CRITICAL_CLASSES):
        _block(blockers, "critical_class_set_invalid", "rule_10")
    cases = _list(rule["heldout_cases"], "held-out cases")
    corpus_digest = canonical_digest(
        {
            "training_case_ids": rule["training_case_ids"],
            "critical_classes": rule["critical_classes"],
            "heldout_cases": cases,
        }
    )
    if rule["corpus_sha256"] != corpus_digest:
        _block(blockers, "rule_10_corpus_digest_mismatch", "rule_10")
    heldout_ids: list[str] = []
    heldout_order: list[tuple[str, str]] = []
    by_class: dict[str, list[Mapping[str, object]]] = {}
    for raw_case in cases:
        case = _object(raw_case, "held-out case")
        case_id = str(case["case_id"])
        heldout_ids.append(case_id)
        class_name = str(case["class"])
        heldout_order.append((class_name, case_id))
        by_class.setdefault(class_name, []).append(case)
    if len(heldout_ids) != len(set(heldout_ids)):
        _block(blockers, "duplicate_heldout_case", "rule_10")
    if heldout_order != sorted(heldout_order):
        _block(blockers, "heldout_case_order_invalid", "rule_10")
    if training & set(heldout_ids):
        _block(blockers, "training_holdout_overlap", "rule_10")
    for class_name in critical:
        class_cases = by_class.get(class_name, [])
        if len(class_cases) < 2:
            _block(blockers, "critical_class_case_shortfall", class_name)
        if not class_cases or any(case["detected"] is not True for case in class_cases):
            _block(blockers, "critical_class_detection_below_target", class_name)
    if set(by_class) != set(critical):
        _block(blockers, "heldout_class_set_mismatch", "rule_10")

    drill = _object(rule["rollback_drill"], "rollback drill")
    if (
        drill["artifact_kind"] != "executed_drill"
        or drill["executed"] is not True
        or drill["passed"] is not True
    ):
        _block(blockers, "rollback_drill_not_executed", "rule_10")
    if drill["elapsed_ms"] > 60000:
        _block(blockers, "rollback_time_exceeded", "rule_10")
    if drill["scheduler_ticks"] > 1:
        _block(blockers, "rollback_tick_exceeded", "rule_10")
    rehearsal = _object(rule["post_cutover_rehearsal"], "rehearsal")
    if (
        rehearsal["executed"] is not True
        or rehearsal["passed"] is not True
        or rehearsal["exact_source_head"] != exact_head
    ):
        _block(blockers, "post_cutover_rehearsal_failed", "rule_10")
    if rule["exact_head_consensus_passed"] is not True:
        _block(blockers, "exact_head_consensus_missing", "rule_10")
    _block(
        blockers,
        "rule_10_execution_authentication_not_implemented",
        "sealed corpus, drill, rehearsal, and consensus verifier is not implemented",
    )


def _audit_downstream_receipts(
    evidence: Mapping[str, object],
    exact_head: str,
    blockers: list[dict[str, str]],
) -> None:
    receipts = _object(evidence["downstream_receipts"], "downstream receipts")
    if receipts["telemetry_source"] != "verified_per_served_event_receipt_index":
        _block(blockers, "wrong_receipt_telemetry_source", "downstream")
    if receipts["exact_source_head"] != exact_head:
        _block(blockers, "receipt_head_mismatch", "downstream")
    index_digest = canonical_digest(
        {key: value for key, value in receipts.items() if key != "index_sha256"}
    )
    if receipts["index_sha256"] != index_digest:
        _block(blockers, "receipt_index_digest_mismatch", "downstream")
    served = int(receipts["served_total"])
    with_receipt = int(receipts["served_with_receipt_total"])
    solver_first = int(receipts["solver_first_served_total"])
    denominator = int(receipts["denominator_total"])
    identity_fields = {
        "served_total": "served_event_identity_sha256s",
        "served_with_receipt_total": "receipt_event_identity_sha256s",
        "solver_first_served_total": "solver_first_event_identity_sha256s",
        "gap_total": "gap_event_identity_sha256s",
        "unresolved_total": "unresolved_event_identity_sha256s",
        "pending_failure_total": "pending_failure_event_identity_sha256s",
    }
    identities: dict[str, list[object]] = {}
    for count_field, identity_field in identity_fields.items():
        values = _list(receipts[identity_field], identity_field)
        identities[identity_field] = values
        if len(values) != len(set(values)):
            _block(blockers, "duplicate_served_event_identity", identity_field)
        if receipts[count_field] != len(values):
            _block(blockers, "receipt_identity_count_mismatch", count_field)
    served_identities = set(identities["served_event_identity_sha256s"])
    for identity_field in (
        "receipt_event_identity_sha256s",
        "solver_first_event_identity_sha256s",
        "gap_event_identity_sha256s",
        "unresolved_event_identity_sha256s",
        "pending_failure_event_identity_sha256s",
    ):
        if not set(identities[identity_field]).issubset(served_identities):
            _block(blockers, "receipt_identity_not_in_denominator", identity_field)
    if denominator != served:
        _block(blockers, "receipt_denominator_mismatch", "downstream")
    if with_receipt > served or solver_first > served:
        _block(blockers, "receipt_numerator_exceeds_denominator", "downstream")
    failures = sum(
        int(receipts[key])
        for key in ("gap_total", "unresolved_total", "pending_failure_total")
    )
    if failures > served:
        _block(blockers, "receipt_failure_accounting_invalid", "downstream")
    failure_sets = [
        set(identities[key])
        for key in (
            "gap_event_identity_sha256s",
            "unresolved_event_identity_sha256s",
            "pending_failure_event_identity_sha256s",
        )
    ]
    if sum(len(values) for values in failure_sets) != len(
        set().union(*failure_sets)
    ):
        _block(blockers, "receipt_failure_identity_overlap", "downstream")
    receipt_identities = set(identities["receipt_event_identity_sha256s"])
    failure_identities = set().union(*failure_sets)
    if receipt_identities & failure_identities:
        _block(blockers, "receipt_success_failure_overlap", "downstream")
    if receipt_identities | failure_identities != served_identities:
        _block(blockers, "receipt_partition_incomplete", "downstream")
    solver_identities = set(
        identities["solver_first_event_identity_sha256s"]
    )
    if not solver_identities.issubset(receipt_identities):
        _block(blockers, "solver_first_without_receipt", "downstream")
    if served < 10000:
        _block(blockers, "served_total_below_minimum", "downstream")
    if with_receipt * 10000 < served * 9500:
        _block(blockers, "receipt_coverage_below_minimum", "downstream")
    if solver_first * 10000 < served * 9500:
        _block(blockers, "solver_first_below_minimum", "downstream")
    evidence_time = _timestamp(evidence["captured_at_utc"], "evidence time")
    receipt_time = _timestamp(receipts["captured_at_utc"], "receipt time")
    post_inventory = _object(evidence["post_start_inventory"], "post inventory")
    post_start_time = _timestamp(
        post_inventory["captured_at_utc"], "post-start capture time"
    )
    if receipt_time <= post_start_time:
        _block(blockers, "receipt_precedes_cutover", "downstream")
    age_seconds = (evidence_time - receipt_time).total_seconds()
    if age_seconds < 0 or age_seconds > 14 * 24 * 60 * 60:
        _block(blockers, "receipt_evidence_stale", "downstream")


def _audit_lock_lifecycle(
    evidence: Mapping[str, object],
    config: Mapping[str, object],
    exact_head: str,
    blockers: list[dict[str, str]],
) -> None:
    receipt = _object(evidence["lock_lifecycle_receipt"], "lock lifecycle receipt")
    if receipt["exact_source_head"] != exact_head:
        _block(blockers, "lock_lifecycle_head_mismatch", "receipt")

    provenance_matches = [
        entry
        for raw_entry in _list(evidence["provenance"], "provenance")
        for entry in [_object(raw_entry, "provenance entry")]
        if entry["action_kind"] == "scheduled_task"
        and entry["action_id"] == receipt["replayer_action_id"]
        and entry["provenance_sha256"] == receipt["replayer_provenance_sha256"]
    ]
    if len(provenance_matches) != 1:
        _block(
            blockers,
            "lock_lifecycle_replayer_provenance_mismatch",
            str(receipt["replayer_action_id"]),
        )
    else:
        replayer_provenance = provenance_matches[0]
        digest_input = {
            key: value
            for key, value in replayer_provenance.items()
            if key != "provenance_sha256"
        }
        if replayer_provenance["provenance_sha256"] != canonical_digest(
            digest_input
        ):
            _block(
                blockers,
                "lock_lifecycle_replayer_provenance_digest_mismatch",
                str(receipt["replayer_action_id"]),
            )
        if replayer_provenance["exact_source_head"] != exact_head:
            _block(
                blockers,
                "lock_lifecycle_replayer_head_mismatch",
                str(receipt["replayer_action_id"]),
            )
        entrypoint_blob_id = str(replayer_provenance["entrypoint_blob_id"])
        entrypoint_blobs = [
            blob
            for raw_blob in _list(
                replayer_provenance["runtime_blobs"],
                "replayer provenance runtime blobs",
            )
            for blob in [_object(raw_blob, "replayer provenance runtime blob")]
            if blob["blob_id"] == entrypoint_blob_id
        ]
        if (
            entrypoint_blob_id != "restore-bridge-spool"
            or len(entrypoint_blobs) != 1
            or entrypoint_blobs[0]["source_path"]
            != ".agent-bridge/bin/Restore-BridgeSpool.ps1"
        ):
            _block(
                blockers,
                "lock_lifecycle_replayer_entrypoint_mismatch",
                str(receipt["replayer_action_id"]),
            )

    quiet_state = _object(evidence["quiet_start_state"], "quiet-start state")
    post_state = _object(evidence["post_drain_state"], "post-drain state")
    quiet_canonical = _object(quiet_state["canonical"], "quiet-start canonical")
    post_canonical = _object(post_state["canonical"], "post-drain canonical")
    if receipt["quiet_start_state_canonical_sha256"] != canonical_digest(
        quiet_canonical
    ):
        _block(blockers, "lock_lifecycle_quiet_state_digest_mismatch", "quiet-start")
    if receipt["post_drain_state_canonical_sha256"] != canonical_digest(
        post_canonical
    ):
        _block(blockers, "lock_lifecycle_quiet_state_digest_mismatch", "post-drain")

    quiet_start = _timestamp(quiet_state["captured_at_utc"], "quiet-start time")
    quiet_end = _timestamp(post_state["captured_at_utc"], "post-drain time")
    receipt_start = _timestamp(
        receipt["quiet_window_started_at_utc"], "receipt quiet start"
    )
    receipt_end = _timestamp(
        receipt["quiet_window_ended_at_utc"], "receipt quiet end"
    )
    if (
        receipt_start != quiet_start
        or receipt_end != quiet_end
        or receipt_start >= receipt_end
    ):
        _block(blockers, "lock_lifecycle_quiet_interval_mismatch", "receipt")

    expected_receipt_digest = canonical_digest(
        {
            key: value
            for key, value in receipt.items()
            if key != "receipt_canonical_sha256"
        }
    )
    if receipt["receipt_canonical_sha256"] != expected_receipt_digest:
        _block(blockers, "lock_lifecycle_receipt_digest_mismatch", "receipt")

    lock_config = _object(config["lock_protocol"], "config lock protocol")
    deadline_ms = int(lock_config["append_deadline_ms"])
    deadline_start = _timestamp(
        receipt["append_deadline_started_at_utc"], "append deadline start"
    )
    deadline_end = _timestamp(
        receipt["append_deadline_expires_at_utc"], "append deadline end"
    )
    if (
        receipt["append_deadline_ms"] != deadline_ms
        or deadline_end - deadline_start != timedelta(milliseconds=deadline_ms)
        or deadline_start < receipt_start
        or deadline_end > receipt_end
    ):
        _block(blockers, "lock_lifecycle_deadline_invalid", "shared append deadline")

    events = [
        _object(raw_event, "lock lifecycle event")
        for raw_event in _list(receipt["events"], "lock lifecycle events")
    ]
    event_times = [
        _timestamp(event["at_utc"], "lock lifecycle event time")
        for event in events
    ]
    captured_at = _timestamp(receipt["captured_at_utc"], "lock lifecycle capture")
    evidence_time = _timestamp(evidence["captured_at_utc"], "evidence capture")
    if (
        [event["sequence"] for event in events] != list(range(len(events)))
        or any(left >= right for left, right in zip(event_times, event_times[1:]))
    ):
        _block(blockers, "lock_lifecycle_event_sequence_invalid", "receipt")
    if (
        any(
            event_time <= receipt_start or event_time >= receipt_end
            for event_time in event_times
        )
        or captured_at <= receipt_end
        or captured_at > evidence_time
    ):
        _block(blockers, "lock_lifecycle_chronology_invalid", "quiet interval")

    expected_locks = list(REPLAYER_LOCKS)
    constructed: list[str] = []
    acquired: list[str] = []
    waited: set[str] = set()
    next_lock = 0
    failure_kind = ""
    mutation_seen = False
    cleanup_started = False
    cleanup_events: list[tuple[str, str]] = []
    cleanup_failed = False
    timeout_completion_not_before: datetime | None = None

    for event, event_time in zip(events, event_times):
        operation = str(event["operation"])
        subject = str(event["subject"])
        result = str(event["result"])
        timeout_ms = int(event["timeout_ms"])
        if (
            timeout_completion_not_before is not None
            and event_time < timeout_completion_not_before
        ):
            _block(
                blockers,
                "lock_lifecycle_timeout_completion_invalid",
                subject,
            )

        if operation == "construct":
            if (
                cleanup_started
                or failure_kind
                or next_lock >= len(expected_locks)
                or len(constructed) != next_lock
                or subject != expected_locks[next_lock]
                or timeout_ms != 0
                or result not in {"succeeded", "construction_failure"}
            ):
                _block(blockers, "lock_lifecycle_acquire_order_invalid", subject)
                continue
            if subject == WRITER_LOCKS[0] and event_time != deadline_start:
                _block(blockers, "lock_lifecycle_deadline_invalid", subject)
            if subject in WRITER_LOCKS and not (deadline_start <= event_time <= deadline_end):
                _block(blockers, "lock_lifecycle_deadline_invalid", subject)
            if result == "construction_failure":
                failure_kind = "construction"
            else:
                constructed.append(subject)
            continue

        if operation == "acquire":
            if (
                cleanup_started
                or failure_kind
                or next_lock >= len(expected_locks)
                or subject != expected_locks[next_lock]
                or subject not in constructed
                or subject in waited
                or result not in {"acquired", "timeout", "abandoned", "unexpected_wait"}
            ):
                _block(blockers, "lock_lifecycle_acquire_order_invalid", subject)
                continue
            waited.add(subject)
            if subject == REPLAYER_LOCKS[0]:
                if timeout_ms != 0:
                    _block(blockers, "lock_lifecycle_deadline_invalid", subject)
            else:
                remaining = deadline_end - event_time
                remaining_us = (
                    (remaining.days * 24 * 60 * 60 + remaining.seconds)
                    * 1_000_000
                    + remaining.microseconds
                )
                remaining_ms = max(
                    0,
                    min(
                        deadline_ms,
                        (remaining_us + 999) // 1000,
                    ),
                )
                if (
                    not (deadline_start <= event_time <= deadline_end)
                    or timeout_ms != remaining_ms
                ):
                    _block(blockers, "lock_lifecycle_deadline_invalid", subject)
            next_lock += 1
            if result in {"acquired", "abandoned"}:
                acquired.append(subject)
            if result != "acquired":
                failure_kind = result
            if result == "timeout":
                timeout_completion_not_before = event_time + timedelta(
                    milliseconds=timeout_ms
                )
            continue

        if operation == "mutation":
            if (
                cleanup_started
                or mutation_seen
                or failure_kind
                or acquired != expected_locks
                or subject != "canonical_stream"
                or timeout_ms != 0
                or result != "succeeded"
            ):
                _block(blockers, "lock_lifecycle_mutation_without_all_locks", subject)
            else:
                mutation_seen = True
            continue

        if operation in {"release", "dispose"}:
            cleanup_started = True
            cleanup_events.append((operation, subject))
            if timeout_ms != 0 or result not in {"succeeded", "failed"}:
                _block(blockers, "lock_lifecycle_cleanup_order_invalid", subject)
            if result != "succeeded":
                cleanup_failed = True
            continue

        _block(blockers, "lock_lifecycle_event_sequence_invalid", operation)

    expected_cleanup: list[tuple[str, str]] = []
    for lock_name in reversed(constructed):
        if lock_name in acquired:
            expected_cleanup.append(("release", lock_name))
        expected_cleanup.append(("dispose", lock_name))
    if cleanup_events != expected_cleanup:
        _block(blockers, "lock_lifecycle_cleanup_order_invalid", "receipt")
    if cleanup_failed:
        _block(blockers, "lock_lifecycle_cleanup_failed", "receipt")
    if (
        timeout_completion_not_before is not None
        and captured_at < timeout_completion_not_before
    ):
        _block(
            blockers,
            "lock_lifecycle_timeout_completion_invalid",
            "receipt capture",
        )

    expected_outcome = "succeeded" if not failure_kind else failure_kind
    if receipt["outcome"] != expected_outcome:
        _block(blockers, "lock_lifecycle_outcome_mismatch", "receipt")
    if failure_kind or not mutation_seen or acquired != expected_locks:
        _block(
            blockers,
            "lock_lifecycle_not_successful",
            failure_kind or "incomplete lifecycle",
        )


def _validate_authority(raw: object, label: str) -> None:
    authority = _object(raw, label)
    _exact_keys(authority, AUTHORITY_KEYS, label)
    for key, value in authority.items():
        _exact_bool(value, False, f"{label}.{key}")


def _validate_incomplete_scope(raw: object, label: str) -> None:
    scope = _object(raw, label)
    _exact_keys(scope, {"complete", "reason"}, label)
    _exact_bool(scope["complete"], False, f"{label}.complete")
    _literal(scope["reason"], INCOMPLETE_SCOPE_REASON, f"{label}.reason")


def _block(
    blockers: list[dict[str, str]], code: str, detail: str
) -> None:
    item = {"code": code, "detail": detail}
    if item not in blockers:
        blockers.append(item)


def _object(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ContractError(f"{label} must be an object")
    return value


def _list(value: object, label: str) -> list[object]:
    if not isinstance(value, list):
        raise ContractError(f"{label} must be an array")
    return value


def _exact_keys(
    value: Mapping[str, object], expected: set[str] | frozenset[str], label: str
) -> None:
    actual = set(value)
    if actual != set(expected):
        missing = sorted(set(expected) - actual)
        extra = sorted(actual - set(expected))
        raise ContractError(f"{label} keys mismatch; missing={missing}, extra={extra}")


def _string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ContractError(f"{label} must be a non-empty string")
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in value):
        raise ContractError(f"{label} contains control characters")
    if SID_RE.fullmatch(value):
        raise ContractError(f"{label} must not expose a SID")
    lowered = value.casefold()
    if (
        value.startswith(("/", "\\"))
        or re.match(r"^[a-zA-Z]:[\\/]", value)
        or "<task" in lowered
        or "</" in lowered
        or "bearer " in lowered
        or "token=" in lowered
        or "password=" in lowered
    ):
        raise ContractError(f"{label} contains prohibited sensitive data")
    return value


def _identifier(value: object, label: str) -> str:
    text = _string(value, label)
    if not ID_RE.fullmatch(text):
        raise ContractError(f"{label} is not a safe identifier")
    return text


def _entity_id(value: object, label: str) -> str:
    text = _string(value, label)
    if not RUNTIME_BLOB_ID_RE.fullmatch(text):
        raise ContractError(f"{label} is not a deployment-gate entity id")
    return text


def _source_path(value: object, label: str) -> str:
    text = _string(value, label)
    raw_parts = text.split("/")
    path = PurePosixPath(text)
    windows = PureWindowsPath(text)
    if (
        path.is_absolute()
        or path.as_posix() != text
        or windows.drive
        or windows.root
        or windows.anchor
        or "\\" in text
        or ":" in text
        or any(part in {"", ".", ".."} for part in raw_parts)
    ):
        raise ContractError(f"{label} must be a normalized repository-relative path")
    reserved = {
        "con",
        "prn",
        "aux",
        "nul",
        "clock$",
        "conin$",
        "conout$",
        *(f"com{index}" for index in range(1, 10)),
        *(f"lpt{index}" for index in range(1, 10)),
    }
    for part in raw_parts:
        if part.endswith((".", " ")):
            raise ContractError(f"{label} contains a Windows-unsafe component")
        if part.split(".", 1)[0].casefold() in reserved:
            raise ContractError(f"{label} contains a reserved Windows device name")
    return text


def _commit(value: object, label: str) -> str:
    text = _string(value, label)
    if not FULL_COMMIT_RE.fullmatch(text):
        raise ContractError(f"{label} must be lowercase 40-hex")
    return text


def _sha256(value: object, label: str) -> str:
    text = _string(value, label)
    if not SHA256_RE.fullmatch(text):
        raise ContractError(f"{label} must be lowercase SHA-256")
    return text


def _blob_id(value: object, label: str) -> str:
    text = _string(value, label)
    if not RUNTIME_BLOB_ID_RE.fullmatch(text):
        raise ContractError(f"{label} must be a canonical runtime blob id")
    return text


def _timestamp(value: object, label: str) -> datetime:
    text = _string(value, label)
    if not UTC_TIMESTAMP_RE.fullmatch(text):
        raise ContractError(
            f"{label} must be a canonical UTC timestamp with at most "
            "six fractional digits"
        )
    try:
        parsed = datetime.fromisoformat(text[:-1] + "+00:00")
    except ValueError as exc:
        raise ContractError(f"{label} is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ContractError(f"{label} must be UTC")
    return parsed.astimezone(timezone.utc)


def _boolean(value: object, label: str) -> bool:
    if type(value) is not bool:
        raise ContractError(f"{label} must be a JSON boolean")
    return value


def _exact_bool(value: object, expected: bool, label: str) -> None:
    if value is not expected:
        raise ContractError(f"{label} must be exactly {str(expected).lower()}")


def _nonnegative_int(value: object, label: str) -> int:
    if type(value) is not int or value < 0:
        raise ContractError(f"{label} must be a non-negative integer")
    return value


def _positive_int(value: object, label: str) -> int:
    if type(value) is not int or value <= 0:
        raise ContractError(f"{label} must be a positive integer")
    return value


def _exact_int(value: object, expected: int, label: str) -> None:
    if type(value) is not int or value != expected:
        raise ContractError(f"{label} must be exactly {expected}")


def _literal(value: object, expected: str, label: str) -> None:
    if value != expected or type(value) is not str:
        raise ContractError(f"{label} must be {expected!r}")


def _string_list(value: object, label: str) -> list[str]:
    return [_string(item, f"{label} item") for item in _list(value, label)]


def _literal_list(value: object, expected: Sequence[str], label: str) -> None:
    actual = _string_list(value, label)
    if actual != list(expected):
        raise ContractError(f"{label} does not match the source contract")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

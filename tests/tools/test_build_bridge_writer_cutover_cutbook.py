# SPDX-License-Identifier: BUSL-1.1
"""Tests for the source-only bridge writer cutover cutbook builder."""
from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import re

import pytest

from tools import build_bridge_writer_cutover_cutbook as cutbook


ROOT = Path(__file__).resolve().parents[2]
HEAD = "a" * 40


def _digest(value: int) -> str:
    return f"{value:064x}"


def _config() -> dict[str, object]:
    return cutbook.load_json_strict(
        ROOT / cutbook.CONFIG_PATH,
        "test config",
    )


def _seal_provenance(entry: dict[str, object]) -> None:
    entry["provenance_sha256"] = cutbook.canonical_digest(
        {key: value for key, value in entry.items() if key != "provenance_sha256"}
    )


def _seal_inventory(inventory: dict[str, object]) -> None:
    inventory["inventory_sha256"] = cutbook.canonical_digest(
        {key: value for key, value in inventory.items() if key != "inventory_sha256"}
    )


def _seal_validated_inventory(inventory: dict[str, object]) -> None:
    process_identities = sorted(
        str(item["identity_sha256"]) for item in inventory["processes"]
    )
    task_identities = sorted(
        str(item["identity_sha256"]) for item in inventory["scheduled_tasks"]
    )
    for capture in inventory["captures"]:
        capture["sample_sha256"] = cutbook.canonical_digest(
            {
                "boot_id_sha256": inventory["boot_id_sha256"],
                "captured_at_utc": capture["captured_at_utc"],
                "host_identity_sha256": inventory["host_identity_sha256"],
                "label": capture["label"],
                "process_identity_sha256s": process_identities,
                "task_identity_sha256s": task_identities,
            }
        )
    inventory["inventory_sha256"] = cutbook.canonical_digest(
        {key: value for key, value in inventory.items() if key != "inventory_sha256"}
    )


def _provenance(
    *,
    action_id: str = "bridge-writer",
    identity_sha256: str | None = None,
    scheduled_task: bool = False,
) -> dict[str, object]:
    if scheduled_task:
        entrypoint = "restore-bridge-spool"
        dependencies: list[str] = [entrypoint]
        runtime_blobs = [
            {
                "blob_id": entrypoint,
                "source_path": ".agent-bridge/bin/Restore-BridgeSpool.ps1",
                "source_blob_sha256": _digest(16),
                "runtime_blob_sha256": _digest(16),
                "size": 160,
            }
        ]
        action_digest = _digest(311)
        closure_digest = _digest(312)
        toolchain = [
            {"id": "powershell", "sha256": _digest(330), "size": 330}
        ]
    else:
        entrypoint = "write-agent-event"
        dependencies = [
            "bridge-event-writer",
            "bridge-loop-tick",
            entrypoint,
        ]
        runtime_blobs = [
            {
                "blob_id": "bridge-event-writer",
                "source_path": "tools/bridge_event_writer.py",
                "source_blob_sha256": _digest(14),
                "runtime_blob_sha256": _digest(14),
                "size": 140,
            },
            {
                "blob_id": "bridge-loop-tick",
                "source_path": "tools/bridge_loop_tick.py",
                "source_blob_sha256": _digest(15),
                "runtime_blob_sha256": _digest(15),
                "size": 150,
            },
            {
                "blob_id": entrypoint,
                "source_path": ".agent-bridge/bin/Write-AgentEvent.ps1",
                "source_blob_sha256": _digest(13),
                "runtime_blob_sha256": _digest(13),
                "size": 130,
            },
        ]
        action_digest = _digest(11)
        closure_digest = _digest(12)
        toolchain = [
            {"id": "powershell", "sha256": _digest(330), "size": 330},
            {"id": "python", "sha256": _digest(331), "size": 331},
        ]
    entry: dict[str, object] = {
        "action_kind": "scheduled_task" if scheduled_task else "process",
        "action_id": action_id,
        "identity_sha256": identity_sha256 or _digest(10),
        "command_or_action_sha256": action_digest,
        "closure_sha256": closure_digest,
        "entrypoint_blob_id": entrypoint,
        "dependency_blob_ids": dependencies,
        "toolchain_ids": [str(item["id"]) for item in toolchain],
        "runtime_blobs": runtime_blobs,
        "toolchain": toolchain,
        "exact_source_head": HEAD,
        "origin": cutbook.ALLOWED_ORIGIN,
        "provenance_sha256": "",
    }
    if scheduled_task:
        entry["definition_sha256"] = _digest(313)
    _seal_provenance(entry)
    return entry


def _inventory(*, captured_at_utc: str, pid: int) -> dict[str, object]:
    process_provenance = _provenance(identity_sha256=_digest(1000 + pid))
    task_provenance = _provenance(
        action_id="bridge-replayer-task",
        identity_sha256=_digest(310),
        scheduled_task=True,
    )
    capture_prefix = "2026-07-21T09:59:" if pid == 101 else "2026-07-21T10:02:"
    validated_inventory: dict[str, object] = {
        "schema": cutbook.VALIDATED_INVENTORY_SCHEMA,
        "exact_source_head": HEAD,
        "host_identity_sha256": _digest(20),
        "boot_id_sha256": _digest(21),
        "captures": [
            {
                "label": "A",
                "captured_at_utc": f"{capture_prefix}58Z",
                "sample_sha256": _digest(320),
            },
            {
                "label": "B",
                "captured_at_utc": f"{capture_prefix}59Z",
                "sample_sha256": _digest(321),
            },
        ],
        "processes": [
            {
                "action_id": process_provenance["action_id"],
                "pid": pid,
                "identity_sha256": process_provenance["identity_sha256"],
                "command_sha256": process_provenance[
                    "command_or_action_sha256"
                ],
                "closure_sha256": process_provenance["closure_sha256"],
                "entrypoint_blob_id": process_provenance["entrypoint_blob_id"],
                "dependency_blob_ids": process_provenance[
                    "dependency_blob_ids"
                ],
                "toolchain_ids": process_provenance["toolchain_ids"],
            }
        ],
        "scheduled_tasks": [
            {
                "action_id": task_provenance["action_id"],
                "identity_sha256": task_provenance["identity_sha256"],
                "action_sha256": task_provenance[
                    "command_or_action_sha256"
                ],
                "definition_sha256": task_provenance["definition_sha256"],
                "closure_sha256": task_provenance["closure_sha256"],
                "entrypoint_blob_id": task_provenance["entrypoint_blob_id"],
                "dependency_blob_ids": task_provenance["dependency_blob_ids"],
                "toolchain_ids": task_provenance["toolchain_ids"],
            }
        ],
        "runtime_blobs": [
            {
                "id": "bridge-event-writer",
                "source_path": "tools/bridge_event_writer.py",
                "sha256": _digest(14),
                "size": 140,
            },
            {
                "id": "bridge-loop-tick",
                "source_path": "tools/bridge_loop_tick.py",
                "sha256": _digest(15),
                "size": 150,
            },
            {
                "id": "restore-bridge-spool",
                "source_path": ".agent-bridge/bin/Restore-BridgeSpool.ps1",
                "sha256": _digest(16),
                "size": 160,
            },
            {
                "id": "write-agent-event",
                "source_path": ".agent-bridge/bin/Write-AgentEvent.ps1",
                "sha256": _digest(13),
                "size": 130,
            },
        ],
        "toolchain": [
            {"id": "powershell", "sha256": _digest(330), "size": 330},
            {"id": "python", "sha256": _digest(331), "size": 331},
        ],
        "scope_proof": {
            "complete": False,
            "reason": cutbook.INCOMPLETE_SCOPE_REASON,
        },
        "inventory_sha256": "",
    }
    _seal_validated_inventory(validated_inventory)
    inventory: dict[str, object] = {
        "schema": cutbook.INVENTORY_SCHEMA,
        "exact_source_head": HEAD,
        "captured_at_utc": captured_at_utc,
        "host_identity_sha256": _digest(20),
        "boot_id_sha256": _digest(21),
        "inventory_sha256": "",
        "validated_inventory_sha256": validated_inventory["inventory_sha256"],
        "validated_inventory": validated_inventory,
        "writer_instances": [
            {
                "action_id": "bridge-writer",
                "pid": pid,
                "identity_sha256": process_provenance["identity_sha256"],
                "origin": cutbook.ALLOWED_ORIGIN,
                "exact_source_head": HEAD,
                "provenance_sha256": process_provenance["provenance_sha256"],
            }
        ],
        "scope_proof": {
            "complete": False,
            "reason": cutbook.INCOMPLETE_SCOPE_REASON,
        },
    }
    _seal_inventory(inventory)
    return inventory


def _wal_rows() -> list[dict[str, object]]:
    wal_file = _digest(30)
    source_order = _digest(32)
    return [
        {
            "wal_file_identity_sha256": wal_file,
            "file_ordinal": 0,
            "source_kind": "final",
            "source_order_sha256": source_order,
            "row_index": 0,
            "row_sha256": _digest(2),
            "classification": "deduped_existing",
        },
        {
            "wal_file_identity_sha256": wal_file,
            "file_ordinal": 0,
            "source_kind": "final",
            "source_order_sha256": source_order,
            "row_index": 1,
            "row_sha256": _digest(3),
            "classification": "replayed",
        },
        {
            "wal_file_identity_sha256": wal_file,
            "file_ordinal": 0,
            "source_kind": "final",
            "source_order_sha256": source_order,
            "row_index": 2,
            "row_sha256": _digest(3),
            "classification": "deduped_within_wal",
        },
        {
            "wal_file_identity_sha256": wal_file,
            "file_ordinal": 0,
            "source_kind": "final",
            "source_order_sha256": source_order,
            "row_index": 3,
            "row_sha256": _digest(4),
            "classification": "replayed",
        },
    ]


def _seal_replay_plan(wal: dict[str, object]) -> None:
    files: dict[int, dict[str, object]] = {}
    for row in wal["rows"]:
        ordinal = int(row["file_ordinal"])
        files.setdefault(
            ordinal,
            {
                "file_ordinal": ordinal,
                "source_kind": row["source_kind"],
                "source_order_sha256": row["source_order_sha256"],
                "wal_file_identity_sha256": row["wal_file_identity_sha256"],
            },
        )
    wal["replay_plan_sha256"] = cutbook.canonical_digest(
        [files[ordinal] for ordinal in sorted(files)]
    )


def _state(*, post_drain: bool, inventory_sha256: str) -> dict[str, object]:
    rows = [_digest(1), _digest(2)]
    length = 100
    tail = _digest(2)
    suffix: list[str] = []
    if post_drain:
        rows += [_digest(3), _digest(4)]
        length = 140
        tail = _digest(4)
        suffix = [_digest(3), _digest(4)]
    captured = "2026-07-21T10:02:00Z" if post_drain else "2026-07-21T10:01:00Z"
    wal_rows = _wal_rows()
    replay_plan_sha256 = cutbook.canonical_digest(
        [
            {
                "file_ordinal": 0,
                "source_kind": wal_rows[0]["source_kind"],
                "source_order_sha256": wal_rows[0]["source_order_sha256"],
                "wal_file_identity_sha256": wal_rows[0][
                    "wal_file_identity_sha256"
                ],
            }
        ]
    )
    state: dict[str, object] = {
        "schema": cutbook.STATE_SCHEMA,
        "captured_at_utc": captured,
        "host_identity_sha256": _digest(20),
        "boot_id_sha256": _digest(21),
        "inventory_sha256": inventory_sha256,
        "canonical": {
            "file_identity_sha256": _digest(42),
            "length": length,
            "record_count": len(rows),
            "tail_anchor_sha256": tail,
            "utf8_valid": True,
            "jsonl_valid": True,
            "lf_terminated": True,
            "row_sha256s": rows,
            "suffix_row_sha256s": suffix,
        },
        "checkpoint": {
            "schema": cutbook.CHECKPOINT_SCHEMA,
            "file_identity_sha256": _digest(42),
            "length": length,
            "tail_anchor_sha256": tail,
            "matches": True,
        },
        "wal": {
            "pending_file_count": 0 if post_drain else 1,
            "pending_record_count": 0 if post_drain else 4,
            "final_replayable_file_count": 0 if post_drain else 1,
            "final_replayable_record_count": 0 if post_drain else 2,
            "replay_plan_sha256": replay_plan_sha256,
            "rows": wal_rows,
        },
        "quarantine_count": 0,
        "unknown_append_count": 0,
    }
    return state


def _rule_10() -> dict[str, object]:
    heldout_cases = [
        {
            "case_id": f"{class_name}-{suffix}",
            "class": class_name,
            "detected": True,
        }
        for class_name in cutbook.CRITICAL_CLASSES
        for suffix in (1, 2)
    ]
    rule: dict[str, object] = {
        "corpus_sha256": "",
        "training_case_ids": ["train-identity", "train-wal"],
        "critical_classes": list(cutbook.CRITICAL_CLASSES),
        "heldout_cases": heldout_cases,
        "rollback_drill": {
            "artifact_kind": "executed_drill",
            "executed": True,
            "passed": True,
            "elapsed_ms": 60000,
            "scheduler_ticks": 1,
        },
        "post_cutover_rehearsal": {
            "executed": True,
            "passed": True,
            "exact_source_head": HEAD,
        },
        "exact_head_consensus_passed": True,
    }
    rule["corpus_sha256"] = cutbook.canonical_digest(
        {
            "training_case_ids": rule["training_case_ids"],
            "critical_classes": rule["critical_classes"],
            "heldout_cases": rule["heldout_cases"],
        }
    )
    return rule


def _receipts() -> dict[str, object]:
    served = [_digest(1000 + index) for index in range(10000)]
    receipts = served[:9500]
    solver_first = served[:9500]
    gaps = served[9500:9700]
    unresolved = served[9700:9850]
    pending = served[9850:10000]
    result: dict[str, object] = {
        "telemetry_source": "verified_per_served_event_receipt_index",
        "exact_source_head": HEAD,
        "window_id": "writer-cutover-window-1",
        "lifecycle_id": "writer-cutover-lifecycle-1",
        "clean_marker_sha256": _digest(50),
        "index_sha256": "",
        "captured_at_utc": "2026-07-21T10:03:30Z",
        "served_total": len(served),
        "served_with_receipt_total": len(receipts),
        "solver_first_served_total": len(solver_first),
        "gap_total": len(gaps),
        "unresolved_total": len(unresolved),
        "pending_failure_total": len(pending),
        "denominator_total": len(served),
        "served_event_identity_sha256s": served,
        "receipt_event_identity_sha256s": receipts,
        "solver_first_event_identity_sha256s": solver_first,
        "gap_event_identity_sha256s": gaps,
        "unresolved_event_identity_sha256s": unresolved,
        "pending_failure_event_identity_sha256s": pending,
    }
    result["index_sha256"] = cutbook.canonical_digest(
        {key: value for key, value in result.items() if key != "index_sha256"}
    )
    return result


def _lock_lifecycle_receipt(evidence: dict[str, object]) -> dict[str, object]:
    replayer = next(
        entry
        for entry in evidence["provenance"]
        if entry["action_kind"] == "scheduled_task"
    )
    events = [
        ("2026-07-21T10:01:00.100Z", "construct", cutbook.REPLAYER_LOCKS[0], 0, "succeeded"),
        ("2026-07-21T10:01:00.200Z", "acquire", cutbook.REPLAYER_LOCKS[0], 0, "acquired"),
        ("2026-07-21T10:01:00.300Z", "construct", cutbook.WRITER_LOCKS[0], 0, "succeeded"),
        ("2026-07-21T10:01:00.301Z", "acquire", cutbook.WRITER_LOCKS[0], 9999, "acquired"),
        ("2026-07-21T10:01:00.400Z", "construct", cutbook.WRITER_LOCKS[1], 0, "succeeded"),
        ("2026-07-21T10:01:00.500Z", "acquire", cutbook.WRITER_LOCKS[1], 9800, "acquired"),
        ("2026-07-21T10:01:00.600Z", "mutation", "canonical_stream", 0, "succeeded"),
        ("2026-07-21T10:01:00.700Z", "release", cutbook.WRITER_LOCKS[1], 0, "succeeded"),
        ("2026-07-21T10:01:00.800Z", "dispose", cutbook.WRITER_LOCKS[1], 0, "succeeded"),
        ("2026-07-21T10:01:00.900Z", "release", cutbook.WRITER_LOCKS[0], 0, "succeeded"),
        ("2026-07-21T10:01:01.000Z", "dispose", cutbook.WRITER_LOCKS[0], 0, "succeeded"),
        ("2026-07-21T10:01:01.100Z", "release", cutbook.REPLAYER_LOCKS[0], 0, "succeeded"),
        ("2026-07-21T10:01:01.200Z", "dispose", cutbook.REPLAYER_LOCKS[0], 0, "succeeded"),
    ]
    receipt: dict[str, object] = {
        "schema": cutbook.LOCK_LIFECYCLE_SCHEMA,
        "exact_source_head": HEAD,
        "replayer_action_id": replayer["action_id"],
        "replayer_provenance_sha256": replayer["provenance_sha256"],
        "quiet_start_state_canonical_sha256": cutbook.canonical_digest(
            evidence["quiet_start_state"]["canonical"]
        ),
        "post_drain_state_canonical_sha256": cutbook.canonical_digest(
            evidence["post_drain_state"]["canonical"]
        ),
        "quiet_window_started_at_utc": evidence["quiet_start_state"]["captured_at_utc"],
        "quiet_window_ended_at_utc": evidence["post_drain_state"]["captured_at_utc"],
        "append_deadline_ms": 10000,
        "append_deadline_started_at_utc": "2026-07-21T10:01:00.300Z",
        "append_deadline_expires_at_utc": "2026-07-21T10:01:10.300Z",
        "outcome": "succeeded",
        "events": [
            {
                "sequence": sequence,
                "at_utc": at_utc,
                "operation": operation,
                "subject": subject,
                "timeout_ms": timeout_ms,
                "result": result,
            }
            for sequence, (at_utc, operation, subject, timeout_ms, result) in enumerate(events)
        ],
        "captured_at_utc": "2026-07-21T10:02:30.000Z",
        "receipt_canonical_sha256": "",
    }
    receipt["receipt_canonical_sha256"] = cutbook.canonical_digest(
        {key: value for key, value in receipt.items() if key != "receipt_canonical_sha256"}
    )
    return receipt


def _seal_lock_lifecycle(receipt: dict[str, object]) -> None:
    receipt["receipt_canonical_sha256"] = cutbook.canonical_digest(
        {key: value for key, value in receipt.items() if key != "receipt_canonical_sha256"}
    )


def _evidence() -> dict[str, object]:
    pre = _inventory(captured_at_utc="2026-07-21T10:00:00Z", pid=101)
    post = _inventory(captured_at_utc="2026-07-21T10:03:00Z", pid=201)
    evidence: dict[str, object] = {
        "schema": cutbook.EVIDENCE_SCHEMA,
        "exact_source_head": HEAD,
        "captured_at_utc": "2026-07-21T10:04:00Z",
        "pre_freeze_inventory": pre,
        "quiet_start_state": _state(
            post_drain=False,
            inventory_sha256=str(pre["inventory_sha256"]),
        ),
        "post_drain_state": _state(
            post_drain=True,
            inventory_sha256=str(pre["inventory_sha256"]),
        ),
        "post_start_inventory": post,
        "provenance": [
            _provenance(identity_sha256=_digest(1101)),
            _provenance(identity_sha256=_digest(1201)),
            _provenance(
                action_id="bridge-replayer-task",
                identity_sha256=_digest(310),
                scheduled_task=True,
            ),
        ],
        "rule_10": _rule_10(),
        "downstream_receipts": _receipts(),
        "authority": dict(cutbook.AUTHORITY),
    }
    evidence["lock_lifecycle_receipt"] = _lock_lifecycle_receipt(evidence)
    return evidence


def _report(evidence: dict[str, object] | None = None) -> dict[str, object]:
    return cutbook.build_bridge_writer_cutover_cutbook(
        evidence or _evidence(),
        _config(),
    )


def _codes(report: dict[str, object]) -> set[str]:
    return {str(item["code"]) for item in report["blockers"]}


def _downstream_codes(report: dict[str, object]) -> set[str]:
    downstream = report["downstream_claim_gates"]
    return {str(item["code"]) for item in downstream["blockers"]}


def test_valid_fixture_is_still_deterministic_hold_without_authority() -> None:
    evidence = _evidence()
    first = _report(evidence)
    second = _report(deepcopy(evidence))

    assert first == second
    assert first["schema"] == cutbook.REPORT_SCHEMA
    assert first["decision"] == cutbook.DECISION_HOLD
    assert first["exit_code"] == 3
    assert first["ok"] is False
    assert first["checks"] == {
        "provenance": True,
        "inventory": True,
        "event_wal_conservation": False,
        "event_wal_conservation_consistency": True,
        "rule_10": False,
        "lock_lifecycle": False,
        "lock_lifecycle_consistency": True,
        "quiet_window_actor_attestation": False,
        "downstream_receipts": False,
    }
    assert first["authority"] == cutbook.AUTHORITY
    assert all(value is False for value in first["authority"].values())
    assert first["scope_proof"] == {
        "complete": False,
        "reason": cutbook.INCOMPLETE_SCOPE_REASON,
    }
    assert _codes(first) == {
        "incomplete_scope_proof",
        "lock_lifecycle_authentication_not_implemented",
        "quiet_window_actor_attestation_not_implemented",
        "rule_10_execution_authentication_not_implemented",
        "source_foundation_only",
        "wal_replay_order_attestation_not_implemented",
    }
    assert first["downstream_claim_gates"]["qualified"] is False
    assert first["downstream_claim_gates"]["candidate_qualified"] is True
    assert first["downstream_claim_gates"]["claim_safe_effect"] == "none"
    assert _downstream_codes(first) == {"receipt_authentication_not_implemented"}


def test_lock_lifecycle_receipt_structural_spoofing_is_rejected() -> None:
    cases = []
    evidence = _evidence()
    evidence["lock_lifecycle_receipt"]["schema"] = "wd.spoof.v1"
    cases.append(evidence)
    evidence = _evidence()
    evidence["lock_lifecycle_receipt"]["authority"] = dict(cutbook.AUTHORITY)
    cases.append(evidence)
    evidence = _evidence()
    evidence["lock_lifecycle_receipt"]["events"][0]["timeout_ms"] = True
    cases.append(evidence)

    for evidence in cases:
        with pytest.raises(cutbook.ContractError):
            _report(evidence)


def test_lock_lifecycle_semantic_contradictions_hold_without_authority() -> None:
    cases: list[tuple[dict[str, object], str]] = []

    def add(expected: str, mutate) -> None:
        evidence = _evidence()
        receipt = evidence["lock_lifecycle_receipt"]
        mutate(receipt)
        _seal_lock_lifecycle(receipt)
        cases.append((evidence, expected))

    add(
        "lock_lifecycle_replayer_provenance_mismatch",
        lambda receipt: receipt.__setitem__("replayer_provenance_sha256", _digest(999)),
    )
    add(
        "lock_lifecycle_quiet_state_digest_mismatch",
        lambda receipt: receipt.__setitem__("quiet_start_state_canonical_sha256", _digest(998)),
    )
    add(
        "lock_lifecycle_quiet_interval_mismatch",
        lambda receipt: receipt.__setitem__("quiet_window_ended_at_utc", "2026-07-21T10:01:59Z"),
    )
    add(
        "lock_lifecycle_deadline_invalid",
        lambda receipt: receipt["events"][5].__setitem__("timeout_ms", 9801),
    )
    add(
        "lock_lifecycle_acquire_order_invalid",
        lambda receipt: receipt["events"][4].__setitem__("subject", cutbook.WRITER_LOCKS[0]),
    )
    add(
        "lock_lifecycle_mutation_without_all_locks",
        lambda receipt: receipt["events"][5].__setitem__("result", "timeout"),
    )
    add(
        "lock_lifecycle_cleanup_order_invalid",
        lambda receipt: receipt["events"][7].__setitem__("subject", cutbook.WRITER_LOCKS[0]),
    )
    add(
        "lock_lifecycle_cleanup_failed",
        lambda receipt: receipt["events"][7].__setitem__("result", "failed"),
    )

    for evidence, expected in cases:
        report = _report(evidence)
        assert expected in _codes(report)
        assert report["checks"]["lock_lifecycle_consistency"] is False
        assert report["checks"]["lock_lifecycle"] is False
        assert "lock_lifecycle_authentication_not_implemented" in _codes(report)
        assert all(value is False for value in report["authority"].values())

    evidence = _evidence()
    evidence["lock_lifecycle_receipt"]["receipt_canonical_sha256"] = _digest(997)
    report = _report(evidence)
    assert "lock_lifecycle_receipt_digest_mismatch" in _codes(report)
    assert report["checks"]["lock_lifecycle_consistency"] is False


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (
            lambda evidence, entry: entry.__setitem__(
                "provenance_sha256", _digest(996)
            ),
            "lock_lifecycle_replayer_provenance_digest_mismatch",
        ),
        (
            lambda evidence, entry: (
                entry.__setitem__("exact_source_head", "b" * 40),
                _seal_provenance(entry),
                evidence["lock_lifecycle_receipt"].__setitem__(
                    "replayer_provenance_sha256", entry["provenance_sha256"]
                ),
            ),
            "lock_lifecycle_replayer_head_mismatch",
        ),
        (
            lambda evidence, entry: (
                entry["runtime_blobs"][0].__setitem__(
                    "source_path", ".agent-bridge/bin/Write-AgentEvent.ps1"
                ),
                _seal_provenance(entry),
                evidence["lock_lifecycle_receipt"].__setitem__(
                    "replayer_provenance_sha256", entry["provenance_sha256"]
                ),
            ),
            "lock_lifecycle_replayer_entrypoint_mismatch",
        ),
    ],
)
def test_lock_lifecycle_replayer_provenance_is_self_contained(
    mutate,
    expected: str,
) -> None:
    evidence = _evidence()
    entry = next(
        item
        for item in evidence["provenance"]
        if item["action_kind"] == "scheduled_task"
    )
    mutate(evidence, entry)
    receipt = evidence["lock_lifecycle_receipt"]
    receipt["replayer_provenance_sha256"] = entry["provenance_sha256"]
    _seal_lock_lifecycle(receipt)

    report = _report(evidence)
    assert expected in _codes(report)
    assert report["checks"]["lock_lifecycle_consistency"] is False
    assert report["checks"]["lock_lifecycle"] is False
    assert all(value is False for value in report["authority"].values())


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (
            lambda entry: entry.__setitem__("origin", "linked_worktree"),
            "inadmissible_runtime_origin",
        ),
        (
            lambda entry: entry["runtime_blobs"][0].__setitem__(
                "runtime_blob_sha256", _digest(995)
            ),
            "runtime_source_blob_mismatch",
        ),
    ],
)
def test_lock_lifecycle_candidate_requires_full_provenance_and_inventory_checks(
    mutate,
    expected: str,
) -> None:
    evidence = _evidence()
    entry = next(
        item
        for item in evidence["provenance"]
        if item["action_kind"] == "scheduled_task"
    )
    mutate(entry)
    _seal_provenance(entry)
    receipt = evidence["lock_lifecycle_receipt"]
    receipt["replayer_provenance_sha256"] = entry["provenance_sha256"]
    _seal_lock_lifecycle(receipt)

    report = _report(evidence)
    assert expected in _codes(report)
    assert report["checks"]["lock_lifecycle_consistency"] is False
    assert report["checks"]["lock_lifecycle"] is False
    assert all(value is False for value in report["authority"].values())


def test_timestamps_with_excess_fractional_precision_are_rejected() -> None:
    evidence = _evidence()
    receipt = evidence["lock_lifecycle_receipt"]
    receipt["append_deadline_started_at_utc"] = "2026-07-21T10:01:00.3000000Z"
    receipt["append_deadline_expires_at_utc"] = "2026-07-21T10:01:10.3000009Z"
    _seal_lock_lifecycle(receipt)

    with pytest.raises(cutbook.ContractError, match="canonical UTC timestamp"):
        _report(evidence)


def test_timeout_cleanup_cannot_precede_wait_completion() -> None:
    evidence = _evidence()
    receipt = evidence["lock_lifecycle_receipt"]
    receipt["events"] = deepcopy(receipt["events"][:4])
    receipt["events"][-1]["result"] = "timeout"
    receipt["events"].extend(
        [
            {
                "sequence": 4,
                "at_utc": "2026-07-21T10:01:00.400Z",
                "operation": "dispose",
                "subject": cutbook.WRITER_LOCKS[0],
                "timeout_ms": 0,
                "result": "succeeded",
            },
            {
                "sequence": 5,
                "at_utc": "2026-07-21T10:01:00.500Z",
                "operation": "release",
                "subject": cutbook.REPLAYER_LOCKS[0],
                "timeout_ms": 0,
                "result": "succeeded",
            },
            {
                "sequence": 6,
                "at_utc": "2026-07-21T10:01:00.600Z",
                "operation": "dispose",
                "subject": cutbook.REPLAYER_LOCKS[0],
                "timeout_ms": 0,
                "result": "succeeded",
            },
        ]
    )
    receipt["outcome"] = "timeout"
    _seal_lock_lifecycle(receipt)

    report = _report(evidence)
    assert "lock_lifecycle_timeout_completion_invalid" in _codes(report)
    assert report["checks"]["lock_lifecycle_consistency"] is False
    assert report["checks"]["lock_lifecycle"] is False


def test_lock_lifecycle_deadline_boundaries_and_duplicate_construction() -> None:
    evidence = _evidence()
    receipt = evidence["lock_lifecycle_receipt"]
    receipt["append_deadline_expires_at_utc"] = "2026-07-21T10:01:10.300001Z"
    _seal_lock_lifecycle(receipt)
    report = _report(evidence)
    assert "lock_lifecycle_deadline_invalid" in _codes(report)

    for boundary_time in (
        "2026-07-21T10:01:00.299999Z",
        "2026-07-21T10:01:10.300001Z",
    ):
        evidence = _evidence()
        receipt = evidence["lock_lifecycle_receipt"]
        receipt["events"][3]["at_utc"] = boundary_time
        receipt["events"][3]["timeout_ms"] = 0
        _seal_lock_lifecycle(receipt)
        report = _report(evidence)
        assert "lock_lifecycle_deadline_invalid" in _codes(report)
        assert report["checks"]["lock_lifecycle_consistency"] is False

    evidence = _evidence()
    receipt = evidence["lock_lifecycle_receipt"]
    duplicate = deepcopy(receipt["events"][2])
    duplicate["at_utc"] = "2026-07-21T10:01:00.300500Z"
    receipt["events"].insert(3, duplicate)
    for sequence, event in enumerate(receipt["events"]):
        event["sequence"] = sequence
    _seal_lock_lifecycle(receipt)
    report = _report(evidence)
    assert "lock_lifecycle_acquire_order_invalid" in _codes(report)
    assert report["checks"]["lock_lifecycle_consistency"] is False


def test_lock_lifecycle_remaining_deadline_uses_microsecond_ceiling() -> None:
    evidence = _evidence()
    receipt = evidence["lock_lifecycle_receipt"]
    receipt["events"][5]["at_utc"] = "2026-07-21T10:01:00.500001Z"
    _seal_lock_lifecycle(receipt)

    report = _report(evidence)
    assert report["checks"]["lock_lifecycle_consistency"] is True
    assert report["checks"]["lock_lifecycle"] is False
    assert _codes(report) >= {"lock_lifecycle_authentication_not_implemented"}


@pytest.mark.parametrize(
    ("failure_result", "constructed_failed_lock", "acquired_failed_lock"),
    [
        ("construction_failure", False, False),
        ("timeout", True, False),
        ("unexpected_wait", True, False),
        ("abandoned", True, True),
    ],
)
def test_lock_lifecycle_failure_paths_forbid_mutation_and_clean_partial_set(
    failure_result: str,
    constructed_failed_lock: bool,
    acquired_failed_lock: bool,
) -> None:
    evidence = _evidence()
    receipt = evidence["lock_lifecycle_receipt"]
    base = receipt["events"][:2]
    failed_lock = cutbook.WRITER_LOCKS[0]
    if failure_result == "construction_failure":
        base.append(
            {
                "sequence": 2,
                "at_utc": "2026-07-21T10:01:00.300Z",
                "operation": "construct",
                "subject": failed_lock,
                "timeout_ms": 0,
                "result": failure_result,
            }
        )
    else:
        base.extend(deepcopy(receipt["events"][2:4]))
        base[-1]["result"] = failure_result
    cleanup = []
    if constructed_failed_lock:
        if acquired_failed_lock:
            cleanup.append(("release", failed_lock))
        cleanup.append(("dispose", failed_lock))
    cleanup.extend(
        [
            ("release", cutbook.REPLAYER_LOCKS[0]),
            ("dispose", cutbook.REPLAYER_LOCKS[0]),
        ]
    )
    time_prefix = (
        "2026-07-21T10:01:10."
        if failure_result == "timeout"
        else "2026-07-21T10:01:00."
    )
    next_time = 400
    for operation, subject in cleanup:
        base.append(
            {
                "sequence": len(base),
                "at_utc": f"{time_prefix}{next_time:03d}Z",
                "operation": operation,
                "subject": subject,
                "timeout_ms": 0,
                "result": "succeeded",
            }
        )
        next_time += 100
    receipt["events"] = base
    receipt["outcome"] = (
        "construction"
        if failure_result == "construction_failure"
        else failure_result
    )
    _seal_lock_lifecycle(receipt)

    report = _report(evidence)
    codes = _codes(report)
    assert "lock_lifecycle_not_successful" in codes
    assert "lock_lifecycle_cleanup_order_invalid" not in codes
    assert "lock_lifecycle_cleanup_failed" not in codes
    assert "lock_lifecycle_mutation_without_all_locks" not in codes
    assert report["checks"]["lock_lifecycle_consistency"] is False
    assert report["checks"]["lock_lifecycle"] is False


def test_config_is_exact_hold_contract_and_matches_direct_callers() -> None:
    config = _config()
    cutbook._validate_config(config)
    assert config["activation_state"] == "hold_source_foundation_only"
    assert config["authority"] == cutbook.AUTHORITY

    python_callers = {
        path.relative_to(ROOT).as_posix()
        for path in (ROOT / "tools").glob("*.py")
        if re.search(
            r"(?m)^(?:from tools\.bridge_event_writer import|"
            r"import tools\.bridge_event_writer)",
            path.read_text(encoding="utf-8"),
        )
    }
    assert python_callers == set(cutbook.DIRECT_PYTHON_CALLERS)

    wrapper_callers = {
        path.relative_to(ROOT).as_posix()
        for path in (ROOT / ".agent-bridge" / "bin").glob("*.ps1")
        if not path.name.startswith("Test-")
        and path.name
        not in {"Watch-Bridge.ps1", "Write-AgentEvent.ps1", "Restore-BridgeSpool.ps1"}
        and "Write-AgentEvent.ps1" in path.read_text(encoding="utf-8")
    }
    wrapper_callers.add("tools/pr_bridge_wake.py")
    assert wrapper_callers == set(cutbook.DIRECT_WRAPPER_CALLERS)


def test_docs_and_config_repeat_hold_and_complete_authority_matrix() -> None:
    docs = (
        ROOT / "docs" / "architecture" / "BRIDGE_WRITER_CUTOVER_CUTBOOK_V1.md"
    ).read_text(encoding="utf-8")
    for token in (
        cutbook.DECISION_HOLD,
        cutbook.INCOMPLETE_SCOPE_REASON,
        "10,000 ms",
        "10,000 basis-point",
        "60,000 ms",
        "claim_safe_effect: none",
        "file_ordinal",
        "pending_file_count",
        "candidate_qualified",
        "replay_plan_sha256",
        "timeout completion",
        "receipt capture",
        "wal_replay_order_attestation_not_implemented",
    ):
        assert token in docs
    for authority in cutbook.AUTHORITY_KEYS:
        assert authority in _config()["authority"]


def test_config_mutation_and_type_confusion_are_rejected() -> None:
    mutations = []
    config = _config()
    config["unexpected"] = False
    mutations.append(config)
    config = _config()
    config["authority"]["deployment_allowed"] = True
    mutations.append(config)
    config = _config()
    config["lock_protocol"]["append_deadline_ms"] = True
    mutations.append(config)
    config = _config()
    config["rule_10"]["rollback_max_ms"] = 60001
    mutations.append(config)
    config = _config()
    config["inventory_policy"]["scope_proof"]["complete"] = True
    mutations.append(config)

    for malformed in mutations:
        with pytest.raises(cutbook.ContractError):
            cutbook.build_bridge_writer_cutover_cutbook(_evidence(), malformed)


def test_evidence_schema_extra_keys_types_and_authority_are_rejected() -> None:
    mutations = []
    evidence = _evidence()
    evidence["unexpected"] = False
    mutations.append(evidence)
    evidence = _evidence()
    evidence["schema"] = "third.party"
    mutations.append(evidence)
    evidence = _evidence()
    evidence["post_drain_state"]["canonical"]["length"] = True
    mutations.append(evidence)
    evidence = _evidence()
    evidence["authority"]["merge_allowed"] = 0
    mutations.append(evidence)
    evidence = _evidence()
    evidence["authority"]["operator_approval_collected"] = True
    mutations.append(evidence)
    evidence = _evidence()
    evidence["pre_freeze_inventory"]["scope_proof"]["complete"] = True
    mutations.append(evidence)

    for malformed in mutations:
        with pytest.raises(cutbook.ContractError):
            _report(malformed)


def test_sensitive_paths_sids_tokens_xml_and_command_fields_are_rejected() -> None:
    mutations = []
    for value in (
        r"C:\Python\project2-master\.agent-bridge\bin\writer.ps1",
        "tools/token=secret.py",
        "<Task>writer</Task>",
    ):
        evidence = _evidence()
        evidence["provenance"][0]["runtime_blobs"][0]["source_path"] = value
        mutations.append(evidence)
    for value in (
        "tools//writer.py",
        "tools/./writer.py",
        "tools/writer.py/",
        "tools/CON/writer.py",
        "tools/writer./event.py",
        "tools/writer /event.py",
    ):
        evidence = _evidence()
        evidence["provenance"][0]["runtime_blobs"][0]["source_path"] = value
        mutations.append(evidence)
    evidence = _evidence()
    evidence["provenance"][0]["action_id"] = "S-1-5-18"
    mutations.append(evidence)
    evidence = _evidence()
    evidence["provenance"][0]["command_line"] = "writer --token=secret"
    mutations.append(evidence)

    for malformed in mutations:
        with pytest.raises(cutbook.ContractError):
            _report(malformed)


def test_provenance_head_hash_origin_dependency_and_duplicate_fail_closed() -> None:
    cases: list[tuple[dict[str, object], str]] = []
    evidence = _evidence()
    entry = evidence["provenance"][0]
    entry["exact_source_head"] = "b" * 40
    _seal_provenance(entry)
    cases.append((evidence, "provenance_head_mismatch"))
    evidence = _evidence()
    evidence["provenance"][0]["runtime_blobs"][0][
        "runtime_blob_sha256"
    ] = _digest(99)
    _seal_provenance(evidence["provenance"][0])
    cases.append((evidence, "runtime_source_blob_mismatch"))
    evidence = _evidence()
    evidence["provenance"][0]["origin"] = "linked_worktree"
    _seal_provenance(evidence["provenance"][0])
    cases.append((evidence, "inadmissible_runtime_origin"))
    evidence = _evidence()
    evidence["provenance"][0]["runtime_blobs"].pop()
    _seal_provenance(evidence["provenance"][0])
    cases.append((evidence, "runtime_blob_binding_invalid"))
    evidence = _evidence()
    evidence["provenance"][0]["runtime_blobs"].append(
        deepcopy(evidence["provenance"][0]["runtime_blobs"][0])
    )
    _seal_provenance(evidence["provenance"][0])
    cases.append((evidence, "runtime_blob_binding_invalid"))
    evidence = _evidence()
    evidence["provenance"][0]["provenance_sha256"] = _digest(999)
    cases.append((evidence, "provenance_digest_mismatch"))
    evidence = _evidence()
    evidence["provenance"].append(deepcopy(evidence["provenance"][0]))
    cases.append((evidence, "duplicate_provenance_binding"))

    for evidence, expected in cases:
        report = _report(evidence)
        assert report["decision"] == cutbook.DECISION_HOLD
        assert expected in _codes(report)
        assert report["checks"]["provenance"] is False
        assert report["checks"]["lock_lifecycle_consistency"] is False

    evidence = _evidence()
    evidence["provenance"][0]["dependency_blob_ids"].append(
        "bridge-event-writer"
    )
    _seal_provenance(evidence["provenance"][0])
    with pytest.raises(cutbook.ContractError):
        _report(evidence)


def test_inventory_origin_orphan_duplicate_digest_and_drift_fail_closed() -> None:
    cases: list[tuple[dict[str, object], str]] = []
    evidence = _evidence()
    inventory = evidence["post_start_inventory"]
    inventory["writer_instances"][0]["origin"] = "legacy_direct_append"
    _seal_inventory(inventory)
    cases.append((evidence, "legacy_or_unknown_writer"))
    evidence = _evidence()
    inventory = evidence["post_start_inventory"]
    inventory["writer_instances"][0]["provenance_sha256"] = _digest(77)
    _seal_inventory(inventory)
    cases.append((evidence, "orphan_writer"))
    evidence = _evidence()
    inventory = evidence["post_start_inventory"]
    inventory["writer_instances"].append(deepcopy(inventory["writer_instances"][0]))
    _seal_inventory(inventory)
    cases.append((evidence, "duplicate_writer_identity"))
    evidence = _evidence()
    evidence["post_start_inventory"]["inventory_sha256"] = _digest(78)
    cases.append((evidence, "inventory_digest_mismatch"))
    evidence = _evidence()
    inventory = evidence["post_start_inventory"]
    inventory["host_identity_sha256"] = _digest(79)
    _seal_inventory(inventory)
    cases.append((evidence, "inventory_host_boot_drift"))

    for evidence, expected in cases:
        report = _report(evidence)
        assert expected in _codes(report)
        assert report["checks"]["inventory"] is False
        assert report["checks"]["lock_lifecycle_consistency"] is False


def test_validated_inventory_projection_is_consumed_and_exactly_bound() -> None:
    def reseal(inventory: dict[str, object]) -> None:
        projection = inventory["validated_inventory"]
        _seal_validated_inventory(projection)
        inventory["validated_inventory_sha256"] = projection["inventory_sha256"]
        _seal_inventory(inventory)

    cases: list[tuple[dict[str, object], str]] = []
    evidence = _evidence()
    evidence["provenance"] = []
    cases.append((evidence, "provenance_inventory_coverage_mismatch"))

    evidence = _evidence()
    inventory = evidence["post_start_inventory"]
    inventory["validated_inventory"]["inventory_sha256"] = _digest(900)
    _seal_inventory(inventory)
    cases.append((evidence, "validated_inventory_digest_mismatch"))

    evidence = _evidence()
    inventory = evidence["post_start_inventory"]
    inventory["validated_inventory_sha256"] = _digest(901)
    _seal_inventory(inventory)
    cases.append((evidence, "validated_inventory_binding_mismatch"))

    evidence = _evidence()
    inventory = evidence["post_start_inventory"]
    inventory["validated_inventory"]["runtime_blobs"][0]["sha256"] = _digest(902)
    reseal(inventory)
    cases.append((evidence, "projection_runtime_blob_provenance_mismatch"))

    evidence = _evidence()
    inventory = evidence["post_start_inventory"]
    inventory["validated_inventory"]["processes"][0]["command_sha256"] = _digest(
        903
    )
    reseal(inventory)
    cases.append((evidence, "projection_action_provenance_mismatch"))

    evidence = _evidence()
    inventory = evidence["post_start_inventory"]
    inventory["validated_inventory"]["scheduled_tasks"][0][
        "definition_sha256"
    ] = _digest(906)
    reseal(inventory)
    cases.append((evidence, "projection_task_definition_mismatch"))

    evidence = _evidence()
    inventory = evidence["post_start_inventory"]
    inventory["validated_inventory"]["toolchain"][0]["sha256"] = _digest(907)
    reseal(inventory)
    cases.append((evidence, "projection_toolchain_provenance_mismatch"))

    evidence = _evidence()
    inventory = evidence["post_start_inventory"]
    inventory["validated_inventory"]["runtime_blobs"][0]["size"] = 141
    post_process_provenance = evidence["provenance"][1]
    post_process_provenance["runtime_blobs"][0]["size"] = 141
    _seal_provenance(post_process_provenance)
    inventory["writer_instances"][0]["provenance_sha256"] = (
        post_process_provenance["provenance_sha256"]
    )
    reseal(inventory)
    cases.append((evidence, "validated_inventory_definition_drift"))

    evidence = _evidence()
    inventory = evidence["post_start_inventory"]
    inventory["writer_instances"] = []
    _seal_inventory(inventory)
    cases.append((evidence, "writer_projection_coverage_mismatch"))

    for evidence, expected in cases:
        report = _report(evidence)
        assert expected in _codes(report)
        assert report["checks"]["inventory"] is False
        assert report["checks"]["lock_lifecycle_consistency"] is False

    for field in ("processes", "scheduled_tasks", "runtime_blobs", "toolchain"):
        evidence = _evidence()
        evidence["post_start_inventory"]["validated_inventory"][field] = []
        with pytest.raises(cutbook.ContractError):
            _report(evidence)


def test_event_wal_conservation_adversarial_matrix() -> None:
    cases: list[tuple[dict[str, object], str]] = []

    def add(expected: str, mutate) -> None:
        evidence = _evidence()
        mutate(evidence)
        cases.append((evidence, expected))

    add(
        "canonical_stream_shrank",
        lambda e: e["post_drain_state"]["canonical"].__setitem__("length", 99),
    )
    add(
        "canonical_file_identity_drift",
        lambda e: e["post_drain_state"]["canonical"].__setitem__(
            "file_identity_sha256", _digest(90)
        ),
    )
    add(
        "canonical_format_invalid",
        lambda e: e["post_drain_state"]["canonical"].__setitem__(
            "utf8_valid", False
        ),
    )
    add(
        "pending_wal_after_drain",
        lambda e: e["post_drain_state"]["wal"].__setitem__(
            "pending_record_count", 1
        ),
    )
    add(
        "pending_wal_files_after_drain",
        lambda e: e["post_drain_state"]["wal"].__setitem__(
            "pending_file_count", 1
        ),
    )
    add(
        "final_replayable_wal_after_drain",
        lambda e: e["post_drain_state"]["wal"].__setitem__(
            "final_replayable_record_count", 1
        ),
    )
    add(
        "final_replayable_wal_files_after_drain",
        lambda e: e["post_drain_state"]["wal"].__setitem__(
            "final_replayable_file_count", 1
        ),
    )
    add(
        "quarantine_growth",
        lambda e: e["post_drain_state"].__setitem__("quarantine_count", 1),
    )
    add(
        "checkpoint_mismatch",
        lambda e: e["post_drain_state"]["checkpoint"].__setitem__(
            "matches", False
        ),
    )
    add(
        "unknown_canonical_append",
        lambda e: e["post_drain_state"].__setitem__("unknown_append_count", 1),
    )
    add(
        "canonical_prefix_mismatch",
        lambda e: e["post_drain_state"]["canonical"]["row_sha256s"].__setitem__(
            0, _digest(91)
        ),
    )
    add(
        "canonical_suffix_mismatch",
        lambda e: e["post_drain_state"]["canonical"].__setitem__(
            "suffix_row_sha256s", [_digest(3)]
        ),
    )
    add(
        "quiet_start_suffix_not_empty",
        lambda e: e["quiet_start_state"]["canonical"].__setitem__(
            "suffix_row_sha256s", [_digest(2)]
        ),
    )
    add(
        "wal_classification_mismatch",
        lambda e: e["quiet_start_state"]["wal"]["rows"][1].__setitem__(
            "classification", "deduped_existing"
        ),
    )

    def duplicate_occurrence(evidence: dict[str, object]) -> None:
        first = evidence["quiet_start_state"]["wal"]["rows"][0]
        second = evidence["quiet_start_state"]["wal"]["rows"][1]
        second["wal_file_identity_sha256"] = first["wal_file_identity_sha256"]
        second["row_index"] = first["row_index"]
        evidence["post_drain_state"]["wal"]["rows"] = deepcopy(
            evidence["quiet_start_state"]["wal"]["rows"]
        )

    add("duplicate_wal_occurrence", duplicate_occurrence)
    add(
        "canonical_row_conservation_failed",
        lambda e: e["post_drain_state"]["canonical"]["row_sha256s"].__setitem__(
            -1, _digest(92)
        ),
    )
    add(
        "quiet_window_host_boot_drift",
        lambda e: e["post_drain_state"].__setitem__(
            "boot_id_sha256", _digest(93)
        ),
    )
    add(
        "cutover_capture_order_invalid",
        lambda e: e["post_drain_state"].__setitem__(
            "captured_at_utc", "2026-07-21T09:00:00Z"
        ),
    )
    add(
        "cutover_capture_order_invalid",
        lambda e: e["post_drain_state"].__setitem__(
            "captured_at_utc", e["quiet_start_state"]["captured_at_utc"]
        ),
    )

    def post_projection_before_drain(evidence: dict[str, object]) -> None:
        inventory = evidence["post_start_inventory"]
        projection = inventory["validated_inventory"]
        projection["captures"][0]["captured_at_utc"] = "2026-07-21T09:58:58Z"
        projection["captures"][1]["captured_at_utc"] = "2026-07-21T09:58:59Z"
        _seal_validated_inventory(projection)
        inventory["validated_inventory_sha256"] = projection["inventory_sha256"]
        _seal_inventory(inventory)

    add(
        "post_start_projection_precedes_drain",
        post_projection_before_drain,
    )

    for evidence, expected in cases:
        report = _report(evidence)
        assert expected in _codes(report)
        assert report["checks"]["event_wal_conservation"] is False
        assert report["checks"]["event_wal_conservation_consistency"] is False


def test_wal_file_order_and_cross_file_dedup_are_rederived() -> None:
    evidence = _evidence()
    later_occurrence = {
        "wal_file_identity_sha256": _digest(31),
        "file_ordinal": 1,
        "source_kind": "pending",
        "source_order_sha256": _digest(33),
        "row_index": 0,
        "row_sha256": _digest(3),
        "classification": "deduped_existing",
    }
    for state_name in ("quiet_start_state", "post_drain_state"):
        evidence[state_name]["wal"]["rows"].append(deepcopy(later_occurrence))
        _seal_replay_plan(evidence[state_name]["wal"])
    quiet_wal = evidence["quiet_start_state"]["wal"]
    quiet_wal["pending_file_count"] = 2
    quiet_wal["pending_record_count"] = 5

    report = _report(evidence)
    assert report["checks"]["event_wal_conservation"] is False
    assert report["checks"]["event_wal_conservation_consistency"] is True
    assert "wal_replay_order_attestation_not_implemented" in _codes(report)

    evidence["quiet_start_state"]["wal"]["rows"][-1][
        "classification"
    ] = "deduped_within_wal"
    evidence["post_drain_state"]["wal"]["rows"] = deepcopy(
        evidence["quiet_start_state"]["wal"]["rows"]
    )
    report = _report(evidence)
    assert "wal_classification_mismatch" in _codes(report)
    assert report["checks"]["event_wal_conservation_consistency"] is False

    evidence = _evidence()
    for state_name in ("quiet_start_state", "post_drain_state"):
        evidence[state_name]["wal"]["rows"].insert(
            0, deepcopy(later_occurrence)
        )
        _seal_replay_plan(evidence[state_name]["wal"])
    quiet_wal = evidence["quiet_start_state"]["wal"]
    quiet_wal["pending_file_count"] = 2
    quiet_wal["pending_record_count"] = 5
    report = _report(evidence)
    assert "wal_occurrence_order_invalid" in _codes(report)
    assert report["checks"]["event_wal_conservation_consistency"] is False

    evidence = _evidence()
    forged_first = deepcopy(later_occurrence)
    forged_first["file_ordinal"] = 0
    forged_first["source_kind"] = "final"
    forged_first["classification"] = "replayed"
    forged_later_rows = evidence["quiet_start_state"]["wal"]["rows"]
    for row in forged_later_rows:
        row["file_ordinal"] = 1
        row["source_kind"] = "pending"
    forged_later_rows[1]["classification"] = "deduped_existing"
    forged_later_rows[2]["classification"] = "deduped_existing"
    forged_rows = [forged_first, *forged_later_rows]
    for state_name in ("quiet_start_state", "post_drain_state"):
        wal = evidence[state_name]["wal"]
        wal["rows"] = deepcopy(forged_rows)
        _seal_replay_plan(wal)
    quiet_wal = evidence["quiet_start_state"]["wal"]
    quiet_wal["pending_file_count"] = 2
    quiet_wal["pending_record_count"] = 5
    quiet_wal["final_replayable_file_count"] = 2
    report = _report(evidence)
    assert report["checks"]["event_wal_conservation_consistency"] is True
    assert report["checks"]["event_wal_conservation"] is False
    assert "wal_replay_order_attestation_not_implemented" in _codes(report)


def test_rule_10_adversarial_matrix() -> None:
    cases: list[tuple[dict[str, object], str]] = []

    def add(expected: str, mutate) -> None:
        evidence = _evidence()
        mutate(evidence["rule_10"])
        cases.append((evidence, expected))

    add("critical_class_case_shortfall", lambda rule: rule["heldout_cases"].pop())
    add(
        "training_case_set_invalid",
        lambda rule: rule.__setitem__("training_case_ids", []),
    )

    def caller_defined_class(rule: dict[str, object]) -> None:
        rule["critical_classes"] = ["caller-defined"]
        rule["heldout_cases"] = [
            {"case_id": "caller-1", "class": "caller-defined", "detected": True},
            {"case_id": "caller-2", "class": "caller-defined", "detected": True},
        ]
        rule["corpus_sha256"] = cutbook.canonical_digest(
            {
                "training_case_ids": rule["training_case_ids"],
                "critical_classes": rule["critical_classes"],
                "heldout_cases": rule["heldout_cases"],
            }
        )

    add("critical_class_set_invalid", caller_defined_class)
    add(
        "rule_10_corpus_digest_mismatch",
        lambda rule: rule.__setitem__("corpus_sha256", _digest(904)),
    )
    add(
        "critical_class_detection_below_target",
        lambda rule: rule["heldout_cases"][0].__setitem__("detected", False),
    )
    add(
        "training_holdout_overlap",
        lambda rule: rule["training_case_ids"].append(
            rule["heldout_cases"][0]["case_id"]
        ),
    )
    add(
        "rollback_time_exceeded",
        lambda rule: rule["rollback_drill"].__setitem__("elapsed_ms", 60001),
    )
    add(
        "rollback_tick_exceeded",
        lambda rule: rule["rollback_drill"].__setitem__("scheduler_ticks", 2),
    )
    add(
        "rollback_drill_not_executed",
        lambda rule: rule["rollback_drill"].__setitem__(
            "artifact_kind", "eligibility_receipt"
        ),
    )
    add(
        "rollback_drill_not_executed",
        lambda rule: rule["rollback_drill"].__setitem__("executed", False),
    )
    add(
        "post_cutover_rehearsal_failed",
        lambda rule: rule["post_cutover_rehearsal"].__setitem__("passed", False),
    )
    add(
        "post_cutover_rehearsal_failed",
        lambda rule: rule["post_cutover_rehearsal"].__setitem__(
            "exact_source_head", "b" * 40
        ),
    )
    add(
        "exact_head_consensus_missing",
        lambda rule: rule.__setitem__("exact_head_consensus_passed", False),
    )

    for evidence, expected in cases:
        report = _report(evidence)
        assert expected in _codes(report)
        assert report["checks"]["rule_10"] is False


def test_downstream_receipt_failures_do_not_become_source_authority() -> None:
    cases: list[tuple[dict[str, object], str]] = []

    def add(expected: str, mutate) -> None:
        evidence = _evidence()
        mutate(evidence["downstream_receipts"])
        cases.append((evidence, expected))

    add("served_total_below_minimum", lambda r: r.__setitem__("served_total", 9999))

    def below_receipt_ratio(receipts: dict[str, object]) -> None:
        receipts["served_with_receipt_total"] = 9499
        receipts["receipt_event_identity_sha256s"] = receipts[
            "receipt_event_identity_sha256s"
        ][:9499]

    add("receipt_coverage_below_minimum", below_receipt_ratio)

    def below_solver_ratio(receipts: dict[str, object]) -> None:
        receipts["solver_first_served_total"] = 9499
        receipts["solver_first_event_identity_sha256s"] = receipts[
            "solver_first_event_identity_sha256s"
        ][:9499]

    add("solver_first_below_minimum", below_solver_ratio)
    add(
        "receipt_numerator_exceeds_denominator",
        lambda r: r.__setitem__("served_total", 9499),
    )
    add(
        "receipt_denominator_mismatch",
        lambda r: r.__setitem__("denominator_total", 9999),
    )
    add(
        "wrong_receipt_telemetry_source",
        lambda r: r.__setitem__("telemetry_source", "producer_ok_counter"),
    )
    add(
        "receipt_evidence_stale",
        lambda r: r.__setitem__("captured_at_utc", "2026-06-01T00:00:00Z"),
    )
    add(
        "receipt_precedes_cutover",
        lambda r: r.__setitem__("captured_at_utc", "2026-07-21T10:02:00Z"),
    )
    add(
        "receipt_index_digest_mismatch",
        lambda r: r.__setitem__("index_sha256", _digest(905)),
    )

    def duplicate_identity(receipts: dict[str, object]) -> None:
        receipts["receipt_event_identity_sha256s"][1] = receipts[
            "receipt_event_identity_sha256s"
        ][0]

    add("duplicate_served_event_identity", duplicate_identity)

    def outside_denominator(receipts: dict[str, object]) -> None:
        receipts["receipt_event_identity_sha256s"][0] = _digest(999999)

    add("receipt_identity_not_in_denominator", outside_denominator)

    def overlap_failures(receipts: dict[str, object]) -> None:
        receipts["unresolved_event_identity_sha256s"][0] = receipts[
            "gap_event_identity_sha256s"
        ][0]

    add("receipt_failure_identity_overlap", overlap_failures)

    def incomplete_partition(receipts: dict[str, object]) -> None:
        receipts["gap_event_identity_sha256s"].pop()
        receipts["gap_total"] -= 1

    add("receipt_partition_incomplete", incomplete_partition)

    def success_failure_overlap(receipts: dict[str, object]) -> None:
        receipts["gap_event_identity_sha256s"][0] = receipts[
            "receipt_event_identity_sha256s"
        ][0]

    add("receipt_success_failure_overlap", success_failure_overlap)

    def solver_without_receipt(receipts: dict[str, object]) -> None:
        receipts["solver_first_event_identity_sha256s"][-1] = receipts[
            "gap_event_identity_sha256s"
        ][0]

    add("solver_first_without_receipt", solver_without_receipt)

    for evidence, expected in cases:
        report = _report(evidence)
        assert expected in _downstream_codes(report)
        assert report["checks"]["downstream_receipts"] is False
        assert report["downstream_claim_gates"]["qualified"] is False
        assert report["downstream_claim_gates"]["claim_safe_effect"] == "none"
        assert expected not in _codes(report)
        assert all(value is False for value in report["authority"].values())


def test_cli_valid_input_exits_three_and_has_no_mutation_switches(
    tmp_path: Path,
) -> None:
    evidence_path = tmp_path / "evidence.json"
    evidence_path.write_text(json.dumps(_evidence()), encoding="utf-8")
    stdout = io.StringIO()
    stderr = io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        result = cutbook.main(
            ["--evidence-json", str(evidence_path), "--json"]
        )
    assert result == 3
    assert stderr.getvalue() == ""
    payload = json.loads(stdout.getvalue())
    assert payload["decision"] == cutbook.DECISION_HOLD
    assert payload["authority"] == cutbook.AUTHORITY

    option_dests = {action.dest for action in cutbook.build_parser()._actions}
    assert option_dests == {"help", "evidence_json", "json"}
    for forbidden in ("apply", "execute", "runtime_root", "process", "task", "output"):
        assert forbidden not in option_dests


def test_cli_duplicate_json_key_and_unknown_apply_switch_exit_two(
    tmp_path: Path,
) -> None:
    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text(
        '{"schema":"wd.bridge_writer_cutover_evidence.v1",'
        '"schema":"wd.bridge_writer_cutover_evidence.v1"}',
        encoding="utf-8",
    )
    stderr = io.StringIO()
    with redirect_stderr(stderr):
        assert cutbook.main(["--evidence-json", str(duplicate)]) == 2
    assert "duplicate JSON key" in stderr.getvalue()

    with pytest.raises(SystemExit) as excinfo:
        cutbook.build_parser().parse_args(["--apply"])
    assert excinfo.value.code == 2

# SPDX-License-Identifier: BUSL-1.1
"""Tests for the accepted bridge queue visibility preflight."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from types import SimpleNamespace
from typing import Any

import pytest


import tools.bridge_accepted_queue_preflight as accepted_queue_preflight
from tools.bridge_accepted_queue_preflight import check_accepted_queue_complete


LEAF = "bridge-wal-v1-0123456789abcdef0123456789abcdef.jsonl"
FAILURE_STATUSES = {
    "invalid_pending_leaf",
    "pending_failed",
    "pending_append_release_failed",
    "orphan_block_failed",
    "invalid_ready_leaf",
    "failed",
}
WINDOWS_NATIVE_DRAIN_AVAILABLE = os.name == "nt" and bool(
    shutil.which("powershell.exe") or shutil.which("pwsh")
)


def _paths(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / ".agent-bridge"
    (root / "spool" / "accepted-v1").mkdir(parents=True)
    events = root / "shared" / "events.jsonl"
    events.parent.mkdir(parents=True)
    events.write_bytes(b"")
    return root, events


def _result(
    status: str,
    *,
    namespace: str = "ready",
    digest: str | None = None,
    leaf: str | None = LEAF,
    wal_bytes: bytes | None = None,
) -> dict[str, Any]:
    error = "simulated queue failure" if status in FAILURE_STATUSES else None
    result = {
        "namespace": namespace,
        "leaf": leaf,
        "sha256": digest,
        "status": status,
        "detail": "",
        "error": error,
    }
    if wal_bytes is not None:
        result["_test_wal_bytes"] = wal_bytes
    return result


def _receipt(root: Path, results: list[dict[str, Any]]) -> dict[str, Any]:
    for item in results:
        wal_bytes = item.pop("_test_wal_bytes", b"{}\n")
        leaf = item["leaf"]
        digest = item["sha256"] or hashlib.sha256(wal_bytes).hexdigest()
        if type(leaf) is str:
            queue_root = root / "spool" / "accepted-v1"
            marker_statuses = {
                "digest_marker_waiting_for_pending",
                "orphan_block_would_clear",
                "orphan_block_failed",
            }
            if item["namespace"] == "pending":
                path = queue_root / "pending" / leaf
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(wal_bytes)
            elif item["status"] in marker_statuses:
                marker = queue_root / "ready" / (
                    f".{leaf}.pending-recovery-blocked"
                )
                marker.parent.mkdir(parents=True, exist_ok=True)
                marker.write_text(
                    json.dumps(
                        {
                            "schema": "waggledance.bridge.accepted-pending-block.v1",
                            "wal_leaf": leaf,
                            "expected_sha256": digest,
                        },
                        separators=(",", ":"),
                    )
                    + "\n",
                    encoding="utf-8",
                )
            else:
                path = queue_root / "ready" / leaf
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(wal_bytes)
                if item["status"] not in {"invalid_ready_leaf"}:
                    marker = queue_root / "ready" / (
                        f".{leaf}.pending-recovery-blocked"
                    )
                    marker.write_text(
                        json.dumps(
                            {
                                "schema": "waggledance.bridge.accepted-pending-block.v1",
                                "wal_leaf": leaf,
                                "expected_sha256": digest,
                            },
                            separators=(",", ":"),
                        )
                        + "\n",
                        encoding="utf-8",
                    )
        if item["status"] == "pending_would_promote":
            item["detail"] = item["leaf"]
        elif item["status"] == "digest_marker_waiting_for_pending":
            item["detail"] = str(
                (
                    root
                    / "spool"
                    / "accepted-v1"
                    / "pending"
                    / item["leaf"]
                ).absolute()
            )
        elif item["status"] == "orphan_block_would_clear":
            item["detail"] = str(
                (
                    root
                    / "spool"
                    / "accepted-v1"
                    / "replayed"
                    / item["leaf"]
                ).absolute()
            )
        elif item["status"] == "already_delivered":
            item["detail"] = f"accepted WAL already delivered: {item['leaf']}"
        elif item["status"] == "dry_run":
            item["detail"] = (
                f"would replay: {item['leaf']}\n"
                "spool replay complete: replayed=1 deduped=0 "
                "failed=0 dryRun=True"
            )
        elif item["status"] == "canonical_proof_deferred":
            item["detail"] = (
                f"canonical proof deferred to caller: {item['leaf']}"
            )
    ready_seen = sum(
        item["namespace"] == "ready"
        and item["status"]
        in {
            "already_delivered",
            "canonical_proof_deferred",
            "dry_run",
            "failed",
        }
        for item in results
    )
    pending_failed = sum(
        item["namespace"] == "pending"
        and item["status"] in {"invalid_pending_leaf", "pending_failed"}
        for item in results
    )
    pending_skipped = sum(
        item["namespace"] == "pending"
        and item["status"]
        in {
            "pending_append_busy",
            "pending_append_dirty",
            "pending_young",
            "pending_active",
            "pending_would_promote",
        }
        for item in results
    )
    return {
        "schema": "waggledance.bridge.accepted-queue-drain.v1",
        "bridge_root": str(root.absolute()),
        "ready_seen": ready_seen,
        "drained": 0,
        "already_delivered": sum(
            item["namespace"] == "ready"
            and item["status"] == "already_delivered"
            for item in results
        ),
        "failed": sum(item["status"] in FAILURE_STATUSES for item in results),
        "dry_run": True,
        "pending_recovery": "age-gated-v1",
        "pending_min_age_seconds": 60,
        "pending_seen": pending_skipped + pending_failed,
        "pending_promoted": 0,
        "pending_skipped": pending_skipped,
        "pending_failed": pending_failed,
        "would_drain": sum(
            item["namespace"] == "ready"
            and item["status"] in {"dry_run", "canonical_proof_deferred"}
            for item in results
        ),
        "pending_path": str(
            (root / "spool" / "accepted-v1" / "pending").absolute()
        ),
        "results": results,
    }


def _runner_for(
    payload: object,
    *,
    returncode: int = 0,
    stderr: str = "",
):
    stdout = payload if isinstance(payload, str) else json.dumps(payload)

    def runner(command, **kwargs):
        assert "-DryRun" in command
        assert "-ReceiptJson" in command
        assert "-DeferCanonicalProof" in command
        assert kwargs["check"] is False
        return SimpleNamespace(
            returncode=returncode,
            stdout=stdout,
            stderr=stderr,
        )

    return runner


def _check(
    root: Path,
    events: Path,
    receipt: object,
    **runner_kwargs: object,
) -> dict[str, Any]:
    return check_accepted_queue_complete(
        bridge_root=root,
        events_path=events,
        runner=_runner_for(receipt, **runner_kwargs),
    )


def test_absent_namespace_is_complete_without_starting_powershell(
    tmp_path: Path,
) -> None:
    root = tmp_path / ".agent-bridge"
    events = root / "shared" / "events.jsonl"

    def forbidden_runner(*args, **kwargs):
        raise AssertionError("runner must not be called")

    report = check_accepted_queue_complete(
        bridge_root=root,
        events_path=events,
        runner=forbidden_runner,
    )

    assert report["ok"] is True
    assert report["complete"] is True
    assert report["decision"] == "accepted_queue_absent"


def test_first_queue_publication_between_absence_probes_is_not_missed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / ".agent-bridge"
    events = root / "shared" / "events.jsonl"
    events.parent.mkdir(parents=True)
    events.write_bytes(b"")
    accepted = root / "spool" / "accepted-v1"
    receipt = _receipt(root, [])
    original = accepted_queue_preflight.os.lstat
    accepted_lookups = 0

    def racing_lstat(path):
        nonlocal accepted_lookups
        if Path(path) == accepted:
            accepted_lookups += 1
            if accepted_lookups == 1:
                accepted.mkdir(parents=True)
                raise FileNotFoundError(str(path))
        return original(path)

    monkeypatch.setattr(accepted_queue_preflight.os, "lstat", racing_lstat)

    report = check_accepted_queue_complete(
        bridge_root=root,
        events_path=events,
        runner=_runner_for(receipt),
    )

    assert accepted.exists()
    assert report["decision"] != "accepted_queue_absent"
    assert report["complete"] is True


def test_empty_valid_receipt_is_complete(tmp_path: Path) -> None:
    root, events = _paths(tmp_path)

    report = _check(root, events, _receipt(root, []))

    assert report["ok"] is True
    assert report["complete"] is True
    assert report["decision"] == "accepted_queue_complete"


def test_empty_final_inventory_is_publication_fenced(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, events = _paths(tmp_path)
    held = False
    entries = 0
    scans = 0
    original = accepted_queue_preflight._accepted_namespace_inventory

    class Fence:
        def __enter__(self):
            nonlocal held, entries
            assert held is False
            held = True
            entries += 1

        def __exit__(self, exc_type, exc, traceback):
            nonlocal held
            held = False

    def instrumented_inventory(accepted_dir: Path):
        nonlocal scans
        scans += 1
        if scans == 3:
            assert held is True
        return original(accepted_dir)

    monkeypatch.setattr(
        accepted_queue_preflight,
        "_bridge_queue_publication_lease",
        Fence,
    )
    monkeypatch.setattr(
        accepted_queue_preflight,
        "_accepted_namespace_inventory",
        instrumented_inventory,
    )

    report = _check(root, events, _receipt(root, []))

    assert report["complete"] is True
    assert entries == 1
    assert held is False


@pytest.mark.parametrize("status", ["dry_run", "already_delivered"])
def test_ready_row_requires_exact_canonical_bytes(
    tmp_path: Path,
    status: str,
) -> None:
    root, events = _paths(tmp_path)
    row = b'{"type":"finding","status":"changes_requested"}\n'
    digest = hashlib.sha256(row).hexdigest()
    receipt = _receipt(
        root,
        [_result(status, digest=digest, wal_bytes=row)],
    )

    absent = _check(root, events, receipt)
    events.write_bytes(row)
    present = _check(root, events, receipt)

    assert absent["complete"] is False
    assert present["complete"] is True
    assert present["resolved_duplicates"][0]["resolution"] == "exact_canonical_row"


def test_semantically_equal_but_byte_different_row_is_unresolved(
    tmp_path: Path,
) -> None:
    root, events = _paths(tmp_path)
    wal = b'{"a":1,"b":2}\n'
    events.write_bytes(b'{"b": 2, "a": 1}\n')
    digest = hashlib.sha256(wal).hexdigest()

    report = _check(
        root,
        events,
        _receipt(
            root,
            [_result("already_delivered", digest=digest, wal_bytes=wal)],
        ),
    )

    assert report["ok"] is True
    assert report["complete"] is False


def test_pending_and_bound_marker_resolve_only_as_exact_canonical_pair(
    tmp_path: Path,
) -> None:
    root, events = _paths(tmp_path)
    row = b'{"type":"decision","status":"rco_pass"}\n'
    events.write_bytes(row)
    digest = hashlib.sha256(row).hexdigest()
    receipt = _receipt(
        root,
        [
            _result(
                "pending_would_promote",
                namespace="pending",
                digest=digest,
                wal_bytes=row,
            ),
            _result("digest_marker_waiting_for_pending", digest=digest),
        ],
    )

    report = _check(root, events, receipt)

    assert report["complete"] is True
    assert len(report["resolved_duplicates"]) == 2


@pytest.mark.parametrize(
    "status",
    [
        "pending_young",
        "pending_active",
        "pending_append_busy",
        "pending_append_dirty",
    ],
)
def test_ambiguous_pending_state_holds(tmp_path: Path, status: str) -> None:
    root, events = _paths(tmp_path)
    receipt = _receipt(root, [_result(status, namespace="pending")])

    report = _check(root, events, receipt)

    assert report["ok"] is True
    assert report["complete"] is False
    assert report["unresolved"][0]["status"] == status


def test_orphan_marker_still_requires_exact_canonical_row(tmp_path: Path) -> None:
    root, events = _paths(tmp_path)
    row = b'{"type":"finding","status":"blocked"}\n'
    digest = hashlib.sha256(row).hexdigest()
    receipt = _receipt(root, [_result("orphan_block_would_clear", digest=digest)])

    before = _check(root, events, receipt)
    events.write_bytes(row)
    after = _check(root, events, receipt)

    assert before["complete"] is False
    assert after["complete"] is True


def test_valid_failure_receipt_holds_even_with_exit_zero(tmp_path: Path) -> None:
    root, events = _paths(tmp_path)
    receipt = _receipt(root, [_result("failed", digest=None)])

    report = _check(root, events, receipt)

    assert report["ok"] is True
    assert report["complete"] is False


@pytest.mark.parametrize(
    ("mutate", "error_fragment"),
    [
        (lambda value: value.update(schema="wrong"), "schema"),
        (lambda value: value.update(bridge_root="C:\\wrong"), "root"),
        (lambda value: value.update(ready_seen=True), "counter"),
        (lambda value: value.update(pending_min_age_seconds=0), "age"),
        (lambda value: value.update(drained=1), "mutations"),
        (lambda value: value.update(extra=True), "keys"),
    ],
)
def test_invalid_receipt_contract_fails_closed(
    tmp_path: Path,
    mutate,
    error_fragment: str,
) -> None:
    root, events = _paths(tmp_path)
    receipt = _receipt(root, [])
    mutate(receipt)

    report = _check(root, events, receipt)

    assert report["ok"] is False
    assert report["complete"] is False
    assert error_fragment in report["errors"][0]


def test_unknown_status_fails_closed(tmp_path: Path) -> None:
    root, events = _paths(tmp_path)
    receipt = _receipt(root, [_result("pending_young", namespace="pending")])
    receipt["results"][0]["status"] = "future_status"

    report = _check(root, events, receipt)

    assert report["ok"] is False


def test_non_string_namespace_fails_closed_without_crashing(tmp_path: Path) -> None:
    root, events = _paths(tmp_path)
    receipt = _receipt(root, [_result("pending_young", namespace="pending")])
    receipt["results"][0]["namespace"] = []

    report = _check(root, events, receipt)

    assert report["ok"] is False
    assert report["decision"] == "accepted_queue_receipt_invalid"


def test_required_digest_cannot_be_null(tmp_path: Path) -> None:
    root, events = _paths(tmp_path)

    report = _check(root, events, _receipt(root, [_result("dry_run")]))

    assert report["ok"] is False
    assert "requires a digest" in report["errors"][0]


@pytest.mark.parametrize("status", ["dry_run", "already_delivered"])
def test_completion_status_requires_its_deterministic_detail(
    tmp_path: Path,
    status: str,
) -> None:
    root, events = _paths(tmp_path)
    row = b'{"type":"finding"}\n'
    events.write_bytes(row)
    receipt = _receipt(
        root,
        [_result(status, digest=hashlib.sha256(row).hexdigest())],
    )
    receipt["results"][0]["detail"] = "forged"

    report = _check(root, events, receipt)

    assert report["ok"] is False
    assert "detail" in report["errors"][0]


def test_counter_mismatch_fails_closed(tmp_path: Path) -> None:
    root, events = _paths(tmp_path)
    digest = "0" * 64
    receipt = _receipt(root, [_result("dry_run", digest=digest)])
    receipt["would_drain"] = 0

    report = _check(root, events, receipt)

    assert report["ok"] is False
    assert "would-drain" in report["errors"][0]


def test_ghost_pending_counters_without_results_fail_closed(tmp_path: Path) -> None:
    root, events = _paths(tmp_path)
    receipt = _receipt(root, [])
    receipt["pending_seen"] = 1
    receipt["pending_skipped"] = 1

    report = _check(root, events, receipt)

    assert report["ok"] is False
    assert "pending result count" in report["errors"][0]


@pytest.mark.parametrize(
    "relative_directory",
    [
        Path("unexpected-state"),
        Path("pending") / LEAF,
        Path("ready") / LEAF,
    ],
)
def test_unexpected_queue_directory_fails_closed(
    tmp_path: Path,
    relative_directory: Path,
) -> None:
    root, events = _paths(tmp_path)
    (root / "spool" / "accepted-v1" / relative_directory).mkdir(parents=True)

    report = _check(root, events, _receipt(root, []))

    assert report["ok"] is False
    assert report["decision"] == "accepted_queue_namespace_invalid"


def test_oversized_digest_marker_fails_closed(tmp_path: Path) -> None:
    root, events = _paths(tmp_path)
    marker = (
        root
        / "spool"
        / "accepted-v1"
        / "ready"
        / f".{LEAF}.pending-recovery-blocked"
    )
    marker.parent.mkdir(parents=True)
    marker.write_bytes(b"x" * (64 * 1024 + 1))

    report = _check(root, events, _receipt(root, []))

    assert report["ok"] is False
    assert report["decision"] == "accepted_queue_namespace_invalid"


@pytest.mark.parametrize(
    "payload",
    [
        '{"schema":"x","schema":"y"}',
        "{}\n{}",
        "\ufeff{}",
        "null",
    ],
)
def test_non_strict_json_receipt_fails_closed(tmp_path: Path, payload: str) -> None:
    root, events = _paths(tmp_path)

    report = _check(root, events, payload)

    assert report["ok"] is False
    assert report["complete"] is False


def test_deeply_nested_json_receipt_fails_closed(tmp_path: Path) -> None:
    root, events = _paths(tmp_path)
    payload = "[" * 2000 + "0" + "]" * 2000

    report = _check(root, events, payload)

    assert report["ok"] is False
    assert report["decision"] == "accepted_queue_receipt_invalid"


def test_oversized_json_integer_receipt_fails_closed(tmp_path: Path) -> None:
    root, events = _paths(tmp_path)
    payload = '{"counter":' + "1" * 5000 + "}"

    report = _check(root, events, payload)

    assert report["ok"] is False
    assert report["decision"] == "accepted_queue_receipt_invalid"


def test_nonzero_or_stderr_holds(tmp_path: Path) -> None:
    root, events = _paths(tmp_path)
    receipt = _receipt(root, [])

    nonzero = _check(root, events, receipt, returncode=7)
    stderr = _check(root, events, receipt, stderr="warning\n")

    assert nonzero["ok"] is False
    assert stderr["ok"] is False


def test_timeout_and_runner_exception_hold(tmp_path: Path) -> None:
    root, events = _paths(tmp_path)

    def timeout_runner(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="powershell", timeout=30)

    def broken_runner(*args, **kwargs):
        raise RuntimeError("boom")

    timeout = check_accepted_queue_complete(
        bridge_root=root,
        events_path=events,
        runner=timeout_runner,
    )
    broken = check_accepted_queue_complete(
        bridge_root=root,
        events_path=events,
        runner=broken_runner,
    )

    assert timeout["decision"] == "accepted_queue_drain_timeout"
    assert broken["decision"] == "accepted_queue_drain_failed"


def test_runner_result_with_raising_property_fails_closed(tmp_path: Path) -> None:
    root, events = _paths(tmp_path)

    class RaisingResult:
        @property
        def returncode(self):
            raise RuntimeError("boom")

    report = check_accepted_queue_complete(
        bridge_root=root,
        events_path=events,
        runner=lambda *args, **kwargs: RaisingResult(),
    )

    assert report["ok"] is False
    assert report["decision"] == "accepted_queue_drain_invalid_result"


def test_nul_path_fails_closed() -> None:
    report = check_accepted_queue_complete(
        bridge_root=Path("bad\x00root"),
    )

    assert report["ok"] is False
    assert report["decision"] == "accepted_queue_preflight_invalid_input"


def test_queue_ancestor_symlink_fails_closed(tmp_path: Path) -> None:
    actual_parent = tmp_path / "actual-parent"
    actual_parent.mkdir()
    actual_root, _ = _paths(actual_parent)
    linked_parent = tmp_path / "linked-parent"
    try:
        linked_parent.symlink_to(actual_parent, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlink creation is unavailable")
    root = linked_parent / actual_root.name
    events = root / "shared" / "events.jsonl"

    report = _check(root, events, _receipt(root, []))

    assert report["ok"] is False
    assert report["decision"] == "accepted_queue_namespace_invalid"


def test_queue_inventory_added_during_runner_fails_closed(tmp_path: Path) -> None:
    root, events = _paths(tmp_path)
    receipt = _receipt(root, [])

    def racing_runner(*args, **kwargs):
        pending = root / "spool" / "accepted-v1" / "pending" / LEAF
        pending.parent.mkdir(parents=True, exist_ok=True)
        pending.write_bytes(b'{"type":"finding"}\n')
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(receipt),
            stderr="",
        )

    report = check_accepted_queue_complete(
        bridge_root=root,
        events_path=events,
        runner=racing_runner,
    )

    assert report["ok"] is False
    assert report["decision"] == "accepted_queue_inventory_changed"


def test_same_leaf_with_different_wal_bytes_fails_closed(tmp_path: Path) -> None:
    root, events = _paths(tmp_path)
    canonical_row = b'{"type":"decision"}\n'
    retained_row = b'{"type":"finding"}\n'
    events.write_bytes(canonical_row)
    digest = hashlib.sha256(canonical_row).hexdigest()
    receipt = _receipt(
        root,
        [
            _result(
                "already_delivered",
                digest=digest,
                wal_bytes=retained_row,
            )
        ],
    )

    report = _check(root, events, receipt)

    assert report["ok"] is False
    assert report["decision"] == "accepted_queue_inventory_mismatch"


def test_queue_inventory_added_during_canonical_proof_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, events = _paths(tmp_path)
    row = b'{"type":"decision"}\n'
    events.write_bytes(row)
    receipt = _receipt(
        root,
        [
            _result(
                "already_delivered",
                digest=hashlib.sha256(row).hexdigest(),
                wal_bytes=row,
            )
        ],
    )
    original = accepted_queue_preflight._complete_canonical_row_hashes

    def racing_proof(path: Path) -> tuple[set[str], str]:
        hashes = original(path)
        second = (
            root
            / "spool"
            / "accepted-v1"
            / "pending"
            / "bridge-wal-v1-fedcba9876543210fedcba9876543210.jsonl"
        )
        second.parent.mkdir(parents=True, exist_ok=True)
        second.write_bytes(b'{"type":"finding"}\n')
        return hashes

    monkeypatch.setattr(
        accepted_queue_preflight,
        "_complete_canonical_row_hashes",
        racing_proof,
    )

    report = _check(root, events, receipt)

    assert report["ok"] is False
    assert report["decision"] == "accepted_queue_inventory_changed"


def test_canonical_change_during_final_queue_scan_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, events = _paths(tmp_path)
    row = b'{"type":"decision"}\n'
    events.write_bytes(row)
    receipt = _receipt(
        root,
        [
            _result(
                "already_delivered",
                digest=hashlib.sha256(row).hexdigest(),
                wal_bytes=row,
            )
        ],
    )
    original = accepted_queue_preflight._accepted_namespace_inventory
    scans = 0

    def racing_inventory(accepted_dir: Path):
        nonlocal scans
        scans += 1
        result = original(accepted_dir)
        if scans == 3:
            events.write_bytes(b"")
        return result

    monkeypatch.setattr(
        accepted_queue_preflight,
        "_accepted_namespace_inventory",
        racing_inventory,
    )

    report = _check(root, events, receipt)

    assert events.read_bytes() == b""
    assert report["ok"] is False
    assert report["decision"] == "accepted_queue_canonical_proof_changed"


def test_canonical_reordering_during_final_queue_scan_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, events = _paths(tmp_path)
    first = b'{"type":"decision","value":1}\n'
    second = b'{"type":"finding","value":2}\n'
    events.write_bytes(first + second)
    receipt = _receipt(
        root,
        [
            _result(
                "already_delivered",
                digest=hashlib.sha256(first).hexdigest(),
                wal_bytes=first,
            )
        ],
    )
    original = accepted_queue_preflight._accepted_namespace_inventory
    scans = 0

    def racing_inventory(accepted_dir: Path):
        nonlocal scans
        scans += 1
        result = original(accepted_dir)
        if scans == 3:
            events.write_bytes(second + first)
        return result

    monkeypatch.setattr(
        accepted_queue_preflight,
        "_accepted_namespace_inventory",
        racing_inventory,
    )

    report = _check(root, events, receipt)

    assert report["ok"] is False
    assert report["decision"] == "accepted_queue_canonical_proof_changed"


def test_canonical_write_during_held_final_queue_scan_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, events = _paths(tmp_path)
    row = b'{"type":"decision"}\n'
    events.write_bytes(row)
    receipt = _receipt(
        root,
        [
            _result(
                "already_delivered",
                digest=hashlib.sha256(row).hexdigest(),
                wal_bytes=row,
            )
        ],
    )
    original = accepted_queue_preflight._accepted_namespace_inventory
    scans = 0

    def racing_inventory(accepted_dir: Path):
        nonlocal scans
        scans += 1
        result = original(accepted_dir)
        if scans == 4:
            events.write_bytes(b"")
        return result

    monkeypatch.setattr(
        accepted_queue_preflight,
        "_accepted_namespace_inventory",
        racing_inventory,
    )

    report = _check(root, events, receipt)

    assert report["ok"] is False
    assert report["decision"] == "accepted_queue_canonical_proof_failed"


def test_queue_publication_during_final_canonical_scan_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, events = _paths(tmp_path)
    row = b'{"type":"decision"}\n'
    events.write_bytes(row)
    receipt = _receipt(
        root,
        [
            _result(
                "already_delivered",
                digest=hashlib.sha256(row).hexdigest(),
                wal_bytes=row,
            )
        ],
    )
    original = accepted_queue_preflight._accepted_namespace_inventory
    scans = 0

    def racing_inventory(accepted_dir: Path):
        nonlocal scans
        scans += 1
        if scans == 4:
            pending = (
                root
                / "spool"
                / "accepted-v1"
                / "pending"
                / "bridge-wal-v1-fedcba9876543210fedcba9876543210.jsonl"
            )
            pending.parent.mkdir(parents=True, exist_ok=True)
            pending.write_bytes(b'{"type":"finding"}\n')
        return original(accepted_dir)

    monkeypatch.setattr(
        accepted_queue_preflight,
        "_accepted_namespace_inventory",
        racing_inventory,
    )

    report = _check(root, events, receipt)

    assert report["ok"] is False
    assert report["decision"] == "accepted_queue_inventory_changed"


@pytest.mark.skipif(os.name != "nt", reason="Windows named-mutex probe")
def test_production_writer_cannot_publish_behind_final_inventory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, events = _paths(tmp_path)
    initial_event = {
        "ts_utc": "2026-08-22T05:00:00Z",
        "agent": "smoke-1",
        "type": "message",
        "task_id": "accepted-queue-publication-fence-initial",
        "status": "info",
    }
    row = (
        json.dumps(initial_event, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    events.write_bytes(row)
    receipt = _receipt(
        root,
        [
            _result(
                "already_delivered",
                digest=hashlib.sha256(row).hexdigest(),
                wal_bytes=row,
            )
        ],
    )
    suffix = hashlib.sha256(os.fspath(tmp_path).encode()).hexdigest()[:16]
    publication_name = (
        rf"Local\WaggleDanceBridgeAcceptedQueuePublicationV1-{suffix}"
    )
    append_name = rf"Local\WaggleDanceBridgeAppendV1-{suffix}"
    monkeypatch.setattr(
        accepted_queue_preflight,
        "QUEUE_PUBLICATION_MUTEX_NAME",
        publication_name,
    )
    monkeypatch.setattr(
        accepted_queue_preflight,
        "APPEND_MUTEX_NAME",
        append_name,
    )
    attempt = tmp_path / "writer-attempted.txt"
    allow = tmp_path / "writer-allowed.txt"
    acquired = tmp_path / "writer-acquired.txt"
    child: subprocess.Popen[str] | None = None
    original = accepted_queue_preflight._accepted_namespace_inventory
    scans = 0
    child_code = r'''
import json
from pathlib import Path
import sys
import time
import tools.bridge_event_writer as writer

root = Path(sys.argv[1])
attempt = Path(sys.argv[2])
allow = Path(sys.argv[3])
acquired = Path(sys.argv[4])
writer.QUEUE_PUBLICATION_MUTEX_NAME = sys.argv[5]
writer.APPEND_MUTEX_NAME = sys.argv[6]

class SignalingBackend(writer.WindowsAppendV1Backend):
    def acquire_mutex(self, name, timeout_ms):
        if name == writer.QUEUE_PUBLICATION_MUTEX_NAME:
            attempt.write_text("attempted", encoding="utf-8")
            deadline = time.monotonic() + 5
            while not allow.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            if not allow.exists():
                raise RuntimeError("parent did not release writer probe")
        mutex = super().acquire_mutex(name, timeout_ms)
        if name == writer.QUEUE_PUBLICATION_MUTEX_NAME:
            acquired.write_text("acquired", encoding="utf-8")
        return mutex

event = {
    "ts_utc": "2026-08-22T05:00:01Z",
    "agent": "smoke-1",
    "type": "message",
    "task_id": "accepted-queue-publication-fence-racer",
    "status": "info",
}
result = writer.write_bridge_event(
    bridge_root=root,
    event=event,
    write_sidecars=False,
    backend=SignalingBackend(),
)
print(json.dumps({"delivery_status": result.delivery_status}))
'''

    def racing_inventory(accepted_dir: Path):
        nonlocal scans, child
        scans += 1
        result = original(accepted_dir)
        if scans == 4:
            child = subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    child_code,
                    str(root),
                    str(attempt),
                    str(allow),
                    str(acquired),
                    publication_name,
                    append_name,
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="strict",
            )
            deadline = time.monotonic() + 5
            while not attempt.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            assert attempt.exists()
            allow.write_text("allowed", encoding="utf-8")
            time.sleep(0.25)
            assert not acquired.exists()
        return result

    monkeypatch.setattr(
        accepted_queue_preflight,
        "_accepted_namespace_inventory",
        racing_inventory,
    )

    report = _check(root, events, receipt)
    assert child is not None
    stdout, stderr = child.communicate(timeout=30)

    assert child.returncode == 0, stderr
    assert json.loads(stdout)["delivery_status"] == "canonical"
    assert acquired.exists()
    assert report["ok"] is True
    assert report["complete"] is True
    assert len(list((root / "spool" / "accepted-v1" / "ready").glob(
        "bridge-wal-v1-*.jsonl"
    ))) == 1
    assert list((root / "spool" / "accepted-v1" / "pending").glob(
        "bridge-wal-v1-*.jsonl"
    )) == []


def test_unterminated_canonical_tail_cannot_prove_duplicate(tmp_path: Path) -> None:
    root, events = _paths(tmp_path)
    row = b'{"type":"finding"}'
    events.write_bytes(row)
    digest = hashlib.sha256(row).hexdigest()

    report = _check(
        root,
        events,
        _receipt(
            root,
            [_result("already_delivered", digest=digest, wal_bytes=row)],
        ),
    )

    assert report["ok"] is False
    assert report["decision"] == "accepted_queue_canonical_proof_failed"


def test_canonical_symlink_cannot_prove_duplicate(tmp_path: Path) -> None:
    root, events = _paths(tmp_path)
    row = b'{"type":"finding"}\n'
    outside = tmp_path / "outside.jsonl"
    outside.write_bytes(row)
    events.unlink()
    try:
        events.symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation is unavailable")
    receipt = _receipt(
        root,
        [
            _result(
                "already_delivered",
                digest=hashlib.sha256(row).hexdigest(),
                wal_bytes=row,
            )
        ],
    )

    report = _check(root, events, receipt)

    assert report["ok"] is False
    assert report["decision"] == "accepted_queue_canonical_proof_failed"


def test_canonical_parent_symlink_cannot_prove_duplicate(tmp_path: Path) -> None:
    root, events = _paths(tmp_path)
    row = b'{"type":"finding"}\n'
    outside = tmp_path / "outside-shared"
    outside.mkdir()
    (outside / "events.jsonl").write_bytes(row)
    events.unlink()
    events.parent.rmdir()
    try:
        events.parent.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlink creation is unavailable")
    receipt = _receipt(
        root,
        [
            _result(
                "already_delivered",
                digest=hashlib.sha256(row).hexdigest(),
                wal_bytes=row,
            )
        ],
    )

    report = _check(root, events, receipt)

    assert report["ok"] is False
    assert report["decision"] == "accepted_queue_canonical_proof_failed"


@pytest.mark.skipif(
    not WINDOWS_NATIVE_DRAIN_AVAILABLE,
    reason="the Windows native bridge drainer is unavailable",
)
def test_real_drain_receipt_exact_duplicate_is_compatible(tmp_path: Path) -> None:
    root, events = _paths(tmp_path)
    event = {
        "ts_utc": "2026-08-22T05:00:00Z",
        "agent": "smoke-1",
        "type": "message",
        "task_id": "accepted-queue-parser-smoke",
        "status": "info",
        "message": "exact duplicate compatibility",
    }
    row = (
        json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    digest = hashlib.sha256(row).hexdigest()
    events.write_bytes(row)
    ready = root / "spool" / "accepted-v1" / "ready" / LEAF
    ready.parent.mkdir(parents=True)
    ready.write_bytes(row)
    marker = ready.parent / f".{LEAF}.pending-recovery-blocked"
    marker.write_text(
        json.dumps(
            {
                "schema": "waggledance.bridge.accepted-pending-block.v1",
                "wal_leaf": LEAF,
                "expected_sha256": digest,
            },
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )

    report = check_accepted_queue_complete(
        bridge_root=root,
        events_path=events,
        timeout_seconds=30,
    )

    if report["complete"] is False and any(
        "mutex is busy" in str(item.get("error", ""))
        or "mutex is unavailable" in str(item.get("error", ""))
        for item in report.get("unresolved", [])
    ):
        pytest.skip("the process-global bridge replay mutex is busy")
    assert report["ok"] is True, report
    assert report["complete"] is True, report
    assert report["decision"] == "accepted_queue_complete"


@pytest.mark.skipif(
    not WINDOWS_NATIVE_DRAIN_AVAILABLE,
    reason="the Windows native bridge drainer is unavailable",
)
def test_real_drain_orphan_marker_exact_duplicate_is_compatible(
    tmp_path: Path,
) -> None:
    root, events = _paths(tmp_path)
    event = {
        "ts_utc": "2026-08-22T05:00:00Z",
        "agent": "smoke-1",
        "type": "message",
        "task_id": "accepted-queue-orphan-smoke",
        "status": "info",
        "message": "orphan marker exact duplicate compatibility",
    }
    row = (
        json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    digest = hashlib.sha256(row).hexdigest()
    events.write_bytes(row)
    replayed = root / "spool" / "accepted-v1" / "replayed" / LEAF
    replayed.parent.mkdir(parents=True)
    replayed.write_bytes(row)
    marker = (
        root
        / "spool"
        / "accepted-v1"
        / "ready"
        / f".{LEAF}.pending-recovery-blocked"
    )
    marker.parent.mkdir(parents=True)
    marker.write_text(
        json.dumps(
            {
                "schema": "waggledance.bridge.accepted-pending-block.v1",
                "wal_leaf": LEAF,
                "expected_sha256": digest,
            },
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )

    report = check_accepted_queue_complete(
        bridge_root=root,
        events_path=events,
        timeout_seconds=30,
    )

    assert report["ok"] is True, report
    assert report["complete"] is True, report
    assert report["decision"] == "accepted_queue_complete"


@pytest.mark.skipif(
    not WINDOWS_NATIVE_DRAIN_AVAILABLE,
    reason="the Windows native bridge drainer is unavailable",
)
def test_real_drain_rejects_hardlinked_orphan_archive(tmp_path: Path) -> None:
    root, events = _paths(tmp_path)
    event = {
        "ts_utc": "2026-08-22T05:00:00Z",
        "agent": "smoke-1",
        "type": "message",
        "task_id": "accepted-queue-hardlink-smoke",
        "status": "info",
        "message": "hardlinked orphan archive must hold",
    }
    row = (
        json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    digest = hashlib.sha256(row).hexdigest()
    events.write_bytes(row)
    outside = tmp_path / "outside-wal.jsonl"
    outside.write_bytes(row)
    replayed = root / "spool" / "accepted-v1" / "replayed" / LEAF
    replayed.parent.mkdir(parents=True)
    try:
        os.link(outside, replayed)
    except OSError:
        pytest.skip("hard-link creation is unavailable")
    marker = (
        root
        / "spool"
        / "accepted-v1"
        / "ready"
        / f".{LEAF}.pending-recovery-blocked"
    )
    marker.parent.mkdir(parents=True)
    marker.write_text(
        json.dumps(
            {
                "schema": "waggledance.bridge.accepted-pending-block.v1",
                "wal_leaf": LEAF,
                "expected_sha256": digest,
            },
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )

    report = check_accepted_queue_complete(
        bridge_root=root,
        events_path=events,
        timeout_seconds=30,
    )

    assert report["ok"] is True, report
    assert report["complete"] is False, report
    assert report["unresolved"][0]["status"] == "orphan_block_failed"
    assert marker.exists()


@pytest.mark.skipif(
    not WINDOWS_NATIVE_DRAIN_AVAILABLE,
    reason="the Windows native bridge drainer is unavailable",
)
def test_real_drain_rejects_hardlinked_producer_marker(tmp_path: Path) -> None:
    root, events = _paths(tmp_path)
    event = {
        "ts_utc": "2026-08-22T05:00:00Z",
        "agent": "smoke-1",
        "type": "message",
        "task_id": "accepted-queue-hardlink-marker-smoke",
        "status": "info",
        "message": "hardlinked producer marker must hold",
    }
    row = (
        json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    digest = hashlib.sha256(row).hexdigest()
    events.write_bytes(row)
    ready = root / "spool" / "accepted-v1" / "ready" / LEAF
    ready.parent.mkdir(parents=True)
    ready.write_bytes(row)
    outside_marker = tmp_path / "outside-marker.json"
    outside_marker.write_text(
        json.dumps(
            {
                "schema": "waggledance.bridge.accepted-pending-block.v1",
                "wal_leaf": LEAF,
                "expected_sha256": digest,
            },
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )
    marker = ready.parent / f".{LEAF}.pending-recovery-blocked"
    try:
        os.link(outside_marker, marker)
    except OSError:
        pytest.skip("hard-link creation is unavailable")

    completed = subprocess.run(
        [
            accepted_queue_preflight._powershell_executable(),
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(accepted_queue_preflight.DEFAULT_DRAIN_SCRIPT),
            "-BridgeRoot",
            str(root),
            "-DryRun",
            "-ReceiptJson",
            "-DeferCanonicalProof",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
        timeout=30,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    receipt = json.loads(completed.stdout)
    assert receipt["results"][0]["status"] == "failed"
    assert "plain single-link file" in receipt["results"][0]["error"]
    assert marker.exists()


@pytest.mark.parametrize(
    "row",
    [
        b"{}\n",
        b'{"type":"message"}\n{"type":"finding"}\n',
        (
            b'{"type":"future_type","agent":"smoke-1",'
            b'"task_id":"accepted-queue-unknown-type",'
            b'"status":"info"}\n'
        ),
    ],
)
@pytest.mark.skipif(
    not WINDOWS_NATIVE_DRAIN_AVAILABLE,
    reason="the Windows native bridge drainer is unavailable",
)
def test_deferred_ready_validation_rejects_malformed_wal(
    tmp_path: Path,
    row: bytes,
) -> None:
    root, events = _paths(tmp_path)
    digest = hashlib.sha256(row).hexdigest()
    events.write_bytes(row)
    ready = root / "spool" / "accepted-v1" / "ready" / LEAF
    ready.parent.mkdir(parents=True)
    ready.write_bytes(row)
    marker = ready.parent / f".{LEAF}.pending-recovery-blocked"
    marker.write_text(
        json.dumps(
            {
                "schema": "waggledance.bridge.accepted-pending-block.v1",
                "wal_leaf": LEAF,
                "expected_sha256": digest,
            },
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )

    report = check_accepted_queue_complete(
        bridge_root=root,
        events_path=events,
        timeout_seconds=30,
    )

    assert report["ok"] is True, report
    assert report["complete"] is False, report
    assert report["unresolved"][0]["status"] == "failed"

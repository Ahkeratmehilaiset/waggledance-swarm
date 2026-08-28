# SPDX-License-Identifier: BUSL-1.1
"""Fail-closed visibility preflight for the accepted bridge event queue.

Canonical ``shared/events.jsonl`` remains the only authority input to bridge
merge and promotion classifiers.  This helper does not overlay accepted queue
rows.  It asks the existing queue drainer for a read-only receipt and only
declares the queue complete when every retained one-row WAL is already present
as the exact same complete byte row in canonical history.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
from contextlib import contextmanager
from typing import Any, Callable, Iterator, Mapping


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DRAIN_SCRIPT = ROOT / ".agent-bridge" / "bin" / "Drain-AcceptedBridgeQueue.ps1"
RECEIPT_SCHEMA = "waggledance.bridge.accepted-queue-drain.v1"
PENDING_RECOVERY = "age-gated-v1"
PENDING_MIN_AGE_SECONDS = 60
WAL_LEAF_RE = re.compile(r"^bridge-wal-v1-[0-9a-f]{32}\.jsonl$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
MAX_MARKER_BYTES = 64 * 1024
QUEUE_PUBLICATION_MUTEX_NAME = (
    r"Global\WaggleDanceBridgeAcceptedQueuePublicationV1"
)
APPEND_MUTEX_NAME = r"Global\WaggleDanceBridgeAppendV1"

TOP_LEVEL_KEYS = frozenset(
    {
        "schema",
        "bridge_root",
        "ready_seen",
        "drained",
        "already_delivered",
        "failed",
        "dry_run",
        "pending_recovery",
        "pending_min_age_seconds",
        "pending_seen",
        "pending_promoted",
        "pending_skipped",
        "pending_failed",
        "would_drain",
        "pending_path",
        "results",
    }
)
COUNTER_KEYS = (
    "ready_seen",
    "drained",
    "already_delivered",
    "failed",
    "pending_min_age_seconds",
    "pending_seen",
    "pending_promoted",
    "pending_skipped",
    "pending_failed",
    "would_drain",
)
RESULT_KEYS = frozenset({"namespace", "leaf", "sha256", "status", "detail", "error"})

PENDING_STATUSES = frozenset(
    {
        "pending_append_busy",
        "pending_append_dirty",
        "invalid_pending_leaf",
        "pending_young",
        "pending_active",
        "pending_would_promote",
        "pending_failed",
        "pending_append_release_failed",
    }
)
READY_STATUSES = frozenset(
    {
        "digest_marker_waiting_for_pending",
        "orphan_block_would_clear",
        "digest_marker_resolved_concurrently",
        "orphan_block_failed",
        "invalid_ready_leaf",
        "canonical_proof_deferred",
        "dry_run",
        "already_delivered",
        "failed",
    }
)
FAILURE_STATUSES = frozenset(
    {
        "invalid_pending_leaf",
        "pending_failed",
        "pending_append_release_failed",
        "orphan_block_failed",
        "invalid_ready_leaf",
        "failed",
    }
)
DIRECT_DUPLICATE_STATUSES = frozenset(
    {
        "pending_would_promote",
        "orphan_block_would_clear",
        "canonical_proof_deferred",
        "dry_run",
        "already_delivered",
    }
)
SHA_REQUIRED_STATUSES = DIRECT_DUPLICATE_STATUSES | frozenset(
    {"digest_marker_waiting_for_pending"}
)
NULL_SHA_STATUSES = frozenset(
    {
        "pending_append_busy",
        "pending_append_dirty",
        "invalid_pending_leaf",
        "pending_young",
        "pending_active",
        "pending_append_release_failed",
        "digest_marker_resolved_concurrently",
        "orphan_block_failed",
        "invalid_ready_leaf",
    }
)

AcceptedQueueRunner = Callable[..., Any]


class _ReceiptValidationError(ValueError):
    """Raised when the trusted drainer did not emit its exact v1 contract."""


def check_accepted_queue_complete(
    *,
    bridge_root: Path,
    events_path: Path | None = None,
    runner: AcceptedQueueRunner | None = None,
    drain_script: Path = DEFAULT_DRAIN_SCRIPT,
    timeout_seconds: int = 30,
) -> dict[str, Any]:
    """Return whether accepted queue state is fully visible canonically.

    An absent ``spool/accepted-v1`` directory is the only no-PowerShell fast
    path.  Once the namespace exists, every helper, receipt, path, counter, and
    retained-WAL ambiguity fails closed.
    """
    try:
        requested_root = _absolute_path(bridge_root)
        canonical_events = (
            _absolute_path(events_path)
            if events_path is not None
            else requested_root / "shared" / "events.jsonl"
        )
        script = _absolute_path(drain_script)
    except (OSError, TypeError, ValueError) as exc:
        return _error_report(
            bridge_root=bridge_root,
            decision="accepted_queue_preflight_invalid_input",
            error=f"{type(exc).__name__}: invalid accepted queue preflight path",
        )

    accepted_dir = requested_root / "spool" / "accepted-v1"
    chain_error = _plain_queue_chain_error(requested_root)
    if chain_error is not None:
        return _error_report(
            bridge_root=requested_root,
            events_path=canonical_events,
            decision="accepted_queue_namespace_invalid",
            error=chain_error,
        )
    try:
        os.lstat(accepted_dir)
    except FileNotFoundError:
        try:
            with _bridge_queue_publication_lease():
                os.lstat(accepted_dir)
        except FileNotFoundError:
            return {
                "ok": True,
                "complete": True,
                "decision": "accepted_queue_absent",
                "bridge_root": str(requested_root),
                "events_path": str(canonical_events),
                "unresolved": [],
                "resolved_duplicates": [],
                "errors": [],
                "receipt_summary": None,
            }
        except (OSError, ValueError) as exc:
            return _error_report(
                bridge_root=requested_root,
                events_path=canonical_events,
                decision="accepted_queue_preflight_unreadable",
                error=(
                    f"{type(exc).__name__}: accepted queue namespace "
                    "is unreadable"
                ),
            )
    except OSError as exc:
        return _error_report(
            bridge_root=requested_root,
            events_path=canonical_events,
            decision="accepted_queue_preflight_unreadable",
            error=f"{type(exc).__name__}: accepted queue namespace is unreadable",
        )

    before_inventory, namespace_error = _accepted_namespace_inventory(accepted_dir)
    if namespace_error is not None:
        return _error_report(
            bridge_root=requested_root,
            events_path=canonical_events,
            decision="accepted_queue_namespace_invalid",
            error=namespace_error,
        )

    if not script.is_file():
        return _error_report(
            bridge_root=requested_root,
            events_path=canonical_events,
            decision="accepted_queue_drain_helper_missing",
            error=f"accepted queue drain helper not found: {script}",
        )
    if type(timeout_seconds) is not int or timeout_seconds < 1:
        return _error_report(
            bridge_root=requested_root,
            events_path=canonical_events,
            decision="accepted_queue_preflight_invalid_input",
            error="timeout_seconds must be a positive integer",
        )

    command = [
        _powershell_executable(),
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script),
        "-BridgeRoot",
        str(requested_root),
        "-DryRun",
        "-ReceiptJson",
        "-DeferCanonicalProof",
    ]
    run = subprocess.run if runner is None else runner
    try:
        completed = run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="strict",
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return _error_report(
            bridge_root=requested_root,
            events_path=canonical_events,
            decision="accepted_queue_drain_timeout",
            error="accepted queue dry-run receipt timed out",
        )
    except (OSError, UnicodeError) as exc:
        return _error_report(
            bridge_root=requested_root,
            events_path=canonical_events,
            decision="accepted_queue_drain_failed",
            error=f"{type(exc).__name__}: accepted queue dry-run receipt failed",
        )
    except Exception as exc:  # noqa: BLE001 - injected runners fail closed too
        return _error_report(
            bridge_root=requested_root,
            events_path=canonical_events,
            decision="accepted_queue_drain_failed",
            error=f"{type(exc).__name__}: accepted queue runner failed",
        )

    try:
        return_code = getattr(completed, "returncode", None)
        stdout = getattr(completed, "stdout", None)
        stderr = getattr(completed, "stderr", None)
    except Exception as exc:  # noqa: BLE001 - malformed results fail closed
        return _error_report(
            bridge_root=requested_root,
            events_path=canonical_events,
            decision="accepted_queue_drain_invalid_result",
            error=f"{type(exc).__name__}: accepted queue runner result is unreadable",
        )
    if type(return_code) is not int:
        return _error_report(
            bridge_root=requested_root,
            events_path=canonical_events,
            decision="accepted_queue_drain_invalid_result",
            error="accepted queue runner returned an invalid exit code",
        )
    if return_code != 0:
        return _error_report(
            bridge_root=requested_root,
            events_path=canonical_events,
            decision="accepted_queue_drain_failed",
            error=f"accepted queue dry-run receipt exited {return_code}",
        )
    if type(stdout) is not str or type(stderr) is not str:
        return _error_report(
            bridge_root=requested_root,
            events_path=canonical_events,
            decision="accepted_queue_drain_invalid_result",
            error="accepted queue runner output must be UTF-8 text",
        )
    if stderr != "":
        return _error_report(
            bridge_root=requested_root,
            events_path=canonical_events,
            decision="accepted_queue_drain_stderr",
            error="accepted queue dry-run receipt emitted stderr",
        )

    try:
        receipt = _strict_json_object(stdout)
        normalized_results = _validate_receipt(receipt, requested_root)
    except (RecursionError, UnicodeError, ValueError) as exc:
        return _error_report(
            bridge_root=requested_root,
            events_path=canonical_events,
            decision="accepted_queue_receipt_invalid",
            error=str(exc) or "accepted queue receipt is invalid",
        )

    after_inventory, namespace_error = _accepted_namespace_inventory(accepted_dir)
    if namespace_error is not None:
        return _error_report(
            bridge_root=requested_root,
            events_path=canonical_events,
            decision="accepted_queue_namespace_invalid",
            error=namespace_error,
            receipt_summary=receipt,
        )
    if before_inventory != after_inventory:
        return _error_report(
            bridge_root=requested_root,
            events_path=canonical_events,
            decision="accepted_queue_inventory_changed",
            error="accepted queue inventory changed during dry-run preflight",
            receipt_summary=receipt,
        )
    inventory_binding_error = _receipt_inventory_binding_error(
        normalized_results,
        after_inventory,
    )
    if inventory_binding_error is not None:
        return _error_report(
            bridge_root=requested_root,
            events_path=canonical_events,
            decision="accepted_queue_inventory_mismatch",
            error=inventory_binding_error,
            receipt_summary=receipt,
        )

    proof_required = any(
        item["status"] in DIRECT_DUPLICATE_STATUSES
        or item["status"] == "digest_marker_waiting_for_pending"
        for item in normalized_results
    )
    canonical_hashes: set[str] = set()
    canonical_fingerprint = ""
    if proof_required:
        try:
            (
                canonical_hashes,
                canonical_fingerprint,
            ) = _complete_canonical_row_hashes(canonical_events)
        except (OSError, ValueError) as exc:
            return _error_report(
                bridge_root=requested_root,
                events_path=canonical_events,
                decision="accepted_queue_canonical_proof_failed",
                error=f"{type(exc).__name__}: {exc}",
                receipt_summary=receipt,
            )

    pending_proofs = {
        (item["leaf"], item["sha256"])
        for item in normalized_results
        if item["namespace"] == "pending"
        and item["status"] == "pending_would_promote"
    }
    resolved: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    for item in normalized_results:
        status = item["status"]
        digest = item["sha256"]
        exact_canonical = type(digest) is str and digest in canonical_hashes
        if status in DIRECT_DUPLICATE_STATUSES and exact_canonical:
            resolved.append({**item, "resolution": "exact_canonical_row"})
            continue
        if (
            status == "digest_marker_waiting_for_pending"
            and exact_canonical
            and (item["leaf"], digest) in pending_proofs
        ):
            resolved.append(
                {**item, "resolution": "paired_pending_exact_canonical_row"}
            )
            continue
        reason = (
            "exact canonical row is absent"
            if status in DIRECT_DUPLICATE_STATUSES
            else (
                "pending digest marker lacks a canonical pending proof"
                if status == "digest_marker_waiting_for_pending"
                else "accepted queue state is unresolved"
            )
        )
        unresolved.append({**item, "reason": reason})

    try:
        # Publication ownership covers the final queue observation even when
        # the namespace is empty and no canonical duplicate proof is needed.
        with _bridge_queue_publication_lease():
            final_inventory, namespace_error = _accepted_namespace_inventory(
                accepted_dir
            )
            if namespace_error is not None:
                return _error_report(
                    bridge_root=requested_root,
                    events_path=canonical_events,
                    decision="accepted_queue_namespace_invalid",
                    error=namespace_error,
                    receipt_summary=receipt,
                )
            if final_inventory != after_inventory:
                return _error_report(
                    bridge_root=requested_root,
                    events_path=canonical_events,
                    decision="accepted_queue_inventory_changed",
                    error="accepted queue inventory changed during canonical proof",
                    receipt_summary=receipt,
                )
            if proof_required:
                # Keep the canonical file read-leased until the final accepted-
                # queue scan is complete. On Windows the handle shares reads
                # only, so a writer cannot invalidate the proof during it.
                with _bridge_append_mutex_lease():
                    with _canonical_proof_lease(canonical_events) as (
                        final_canonical_hashes,
                        final_canonical_fingerprint,
                    ):
                        if (
                            final_canonical_hashes != canonical_hashes
                            or final_canonical_fingerprint
                            != canonical_fingerprint
                        ):
                            return _error_report(
                                bridge_root=requested_root,
                                events_path=canonical_events,
                                decision=(
                                    "accepted_queue_canonical_proof_changed"
                                ),
                                error=(
                                    "canonical bridge history changed after "
                                    "duplicate proof"
                                ),
                                receipt_summary=receipt,
                            )
                        post_proof_inventory, namespace_error = (
                            _accepted_namespace_inventory(accepted_dir)
                        )
                        if namespace_error is not None:
                            return _error_report(
                                bridge_root=requested_root,
                                events_path=canonical_events,
                                decision="accepted_queue_namespace_invalid",
                                error=namespace_error,
                                receipt_summary=receipt,
                            )
                        if post_proof_inventory != final_inventory:
                            return _error_report(
                                bridge_root=requested_root,
                                events_path=canonical_events,
                                decision="accepted_queue_inventory_changed",
                                error=(
                                    "accepted queue inventory changed during "
                                    "final proof"
                                ),
                                receipt_summary=receipt,
                            )
    except (OSError, ValueError) as exc:
        return _error_report(
            bridge_root=requested_root,
            events_path=canonical_events,
            decision=(
                "accepted_queue_canonical_proof_failed"
                if proof_required
                else "accepted_queue_publication_fence_failed"
            ),
            error=f"{type(exc).__name__}: {exc}",
            receipt_summary=receipt,
        )

    complete = not unresolved
    return {
        "ok": True,
        "complete": complete,
        "decision": (
            "accepted_queue_complete"
            if complete
            else "accepted_queue_incomplete"
        ),
        "bridge_root": str(requested_root),
        "events_path": str(canonical_events),
        "unresolved": unresolved,
        "resolved_duplicates": resolved,
        "errors": [],
        "receipt_summary": receipt,
    }


def bridge_events_path_matches_root(*, bridge_root: Path, events_path: Path) -> bool:
    """Return whether events are the canonical lexical path for this root."""
    try:
        requested_root = _absolute_path(bridge_root)
        requested_events = _absolute_path(events_path)
    except (OSError, TypeError, ValueError):
        return False
    return _path_key(requested_events) == _path_key(
        requested_root / "shared" / "events.jsonl"
    )


def _validate_receipt(
    receipt: Mapping[str, Any],
    requested_root: Path,
) -> list[dict[str, Any]]:
    if frozenset(receipt) != TOP_LEVEL_KEYS:
        raise _ReceiptValidationError("accepted queue receipt keys do not match v1")
    if receipt.get("schema") != RECEIPT_SCHEMA:
        raise _ReceiptValidationError("accepted queue receipt schema is invalid")
    if receipt.get("dry_run") is not True:
        raise _ReceiptValidationError("accepted queue receipt is not a dry run")
    if receipt.get("pending_recovery") != PENDING_RECOVERY:
        raise _ReceiptValidationError("accepted queue recovery mode is invalid")
    for key in COUNTER_KEYS:
        value = receipt.get(key)
        if type(value) is not int or value < 0:
            raise _ReceiptValidationError(
                f"accepted queue receipt counter {key} is invalid"
            )
    if receipt["pending_min_age_seconds"] != PENDING_MIN_AGE_SECONDS:
        raise _ReceiptValidationError("accepted queue pending age contract is invalid")
    if receipt["drained"] != 0 or receipt["pending_promoted"] != 0:
        raise _ReceiptValidationError("accepted queue dry run reports applied mutations")

    receipt_root = receipt.get("bridge_root")
    pending_path = receipt.get("pending_path")
    if type(receipt_root) is not str or not os.path.isabs(receipt_root):
        raise _ReceiptValidationError("accepted queue receipt root is not absolute")
    if type(pending_path) is not str or not os.path.isabs(pending_path):
        raise _ReceiptValidationError("accepted queue pending path is not absolute")
    if _path_key(receipt_root) != _path_key(requested_root):
        raise _ReceiptValidationError("accepted queue receipt root mismatch")
    expected_pending = requested_root / "spool" / "accepted-v1" / "pending"
    if _path_key(pending_path) != _path_key(expected_pending):
        raise _ReceiptValidationError("accepted queue receipt pending path mismatch")

    raw_results = receipt.get("results")
    if type(raw_results) is not list:
        raise _ReceiptValidationError("accepted queue receipt results must be a list")
    results: list[dict[str, Any]] = []
    identities: set[tuple[str, str | None]] = set()
    digest_by_leaf: dict[str, str] = {}
    for index, raw in enumerate(raw_results):
        if type(raw) is not dict or frozenset(raw) != RESULT_KEYS:
            raise _ReceiptValidationError(
                f"accepted queue result {index} does not match v1"
            )
        namespace = raw.get("namespace")
        status = raw.get("status")
        leaf = raw.get("leaf")
        digest = raw.get("sha256")
        detail = raw.get("detail")
        error = raw.get("error")
        if type(namespace) is not str or namespace not in {"pending", "ready"}:
            raise _ReceiptValidationError(
                f"accepted queue result {index} namespace is invalid"
            )
        allowed = PENDING_STATUSES if namespace == "pending" else READY_STATUSES
        if type(status) is not str or status not in allowed:
            raise _ReceiptValidationError(
                f"accepted queue result {index} status is invalid"
            )
        if status == "pending_append_release_failed":
            if leaf is not None:
                raise _ReceiptValidationError(
                    "pending append release failure leaf must be null"
                )
        elif type(leaf) is not str or not leaf or leaf in {".", ".."}:
            raise _ReceiptValidationError(
                f"accepted queue result {index} leaf is invalid"
            )
        elif "/" in leaf or "\\" in leaf:
            raise _ReceiptValidationError(
                f"accepted queue result {index} leaf is not a basename"
            )
        elif status not in {"invalid_pending_leaf", "invalid_ready_leaf"} and (
            WAL_LEAF_RE.fullmatch(leaf) is None
        ):
            raise _ReceiptValidationError(
                f"accepted queue result {index} WAL leaf is invalid"
            )
        if digest is not None and (
            type(digest) is not str or SHA256_RE.fullmatch(digest) is None
        ):
            raise _ReceiptValidationError(
                f"accepted queue result {index} digest is invalid"
            )
        if status in SHA_REQUIRED_STATUSES and type(digest) is not str:
            raise _ReceiptValidationError(
                f"accepted queue result {index} requires a digest"
            )
        if status in NULL_SHA_STATUSES and digest is not None:
            raise _ReceiptValidationError(
                f"accepted queue result {index} must not carry a digest"
            )
        if type(detail) is not str:
            raise _ReceiptValidationError(
                f"accepted queue result {index} detail must be a string"
            )
        if error is not None and type(error) is not str:
            raise _ReceiptValidationError(
                f"accepted queue result {index} error is invalid"
            )
        if status in FAILURE_STATUSES:
            if type(error) is not str or not error:
                raise _ReceiptValidationError(
                    f"accepted queue result {index} failure lacks an error"
                )
        elif error is not None:
            raise _ReceiptValidationError(
                f"accepted queue result {index} success carries an error"
            )
        if status == "pending_would_promote" and detail != leaf:
            raise _ReceiptValidationError(
                "pending promotion result detail does not bind its leaf"
            )
        if status == "digest_marker_waiting_for_pending" and (
            not os.path.isabs(detail)
            or _path_key(detail)
            != _path_key(
                requested_root
                / "spool"
                / "accepted-v1"
                / "pending"
                / str(leaf)
            )
        ):
            raise _ReceiptValidationError(
                "pending marker result detail path is invalid"
            )
        if status == "orphan_block_would_clear" and (
            not os.path.isabs(detail)
            or _path_key(detail)
            != _path_key(
                requested_root
                / "spool"
                / "accepted-v1"
                / "replayed"
                / str(leaf)
            )
        ):
            raise _ReceiptValidationError(
                "orphan marker result detail path is invalid"
            )
        if status == "already_delivered" and detail != (
            f"accepted WAL already delivered: {leaf}"
        ):
            raise _ReceiptValidationError(
                "already-delivered result detail is invalid"
            )
        if status == "canonical_proof_deferred" and detail != (
            f"canonical proof deferred to caller: {leaf}"
        ):
            raise _ReceiptValidationError(
                "deferred canonical proof result detail is invalid"
            )
        if status == "dry_run":
            normalized_detail = "\n".join(detail.splitlines())
            valid_details = {
                "\n".join(
                    (
                        f"would replay: {leaf}",
                        "spool replay complete: replayed=1 deduped=0 "
                        "failed=0 dryRun=True",
                    )
                ),
                "\n".join(
                    (
                        f"would archive as exact duplicate: {leaf}",
                        "spool replay complete: replayed=0 deduped=1 "
                        "failed=0 dryRun=True",
                    )
                ),
            }
            if normalized_detail not in valid_details:
                raise _ReceiptValidationError("dry-run result detail is invalid")
        identity = (namespace, leaf)
        if identity in identities:
            raise _ReceiptValidationError("accepted queue receipt repeats a result leaf")
        identities.add(identity)
        if type(leaf) is str and type(digest) is str:
            prior = digest_by_leaf.setdefault(leaf, digest)
            if prior != digest:
                raise _ReceiptValidationError(
                    "accepted queue receipt has conflicting leaf digests"
                )
        results.append(dict(raw))

    _validate_counter_invariants(receipt, results)
    return results


def _validate_counter_invariants(
    receipt: Mapping[str, Any],
    results: list[dict[str, Any]],
) -> None:
    count = lambda namespace, status: sum(  # noqa: E731
        1
        for item in results
        if item["namespace"] == namespace and item["status"] == status
    )
    ready_failed = count("ready", "failed")
    if receipt["already_delivered"] != count("ready", "already_delivered"):
        raise _ReceiptValidationError("accepted queue already-delivered count mismatch")
    if receipt["would_drain"] != (
        count("ready", "dry_run")
        + count("ready", "canonical_proof_deferred")
    ):
        raise _ReceiptValidationError("accepted queue would-drain count mismatch")
    if receipt["ready_seen"] != (
        receipt["already_delivered"] + receipt["would_drain"] + ready_failed
    ):
        raise _ReceiptValidationError("accepted queue ready count mismatch")
    failure_count = sum(item["status"] in FAILURE_STATUSES for item in results)
    if receipt["failed"] != failure_count:
        raise _ReceiptValidationError("accepted queue failure count mismatch")
    pending_failure_count = sum(
        item["namespace"] == "pending"
        and item["status"] in {"invalid_pending_leaf", "pending_failed"}
        for item in results
    )
    if receipt["pending_failed"] != pending_failure_count:
        raise _ReceiptValidationError("accepted queue pending failure count mismatch")
    direct_pending = [
        item
        for item in results
        if item["namespace"] == "pending"
        and item["status"] != "pending_append_release_failed"
    ]
    direct_pending_leaves = {item["leaf"] for item in direct_pending}
    unpaired_waiting_markers = sum(
        item["namespace"] == "ready"
        and item["status"] == "digest_marker_waiting_for_pending"
        and item["leaf"] not in direct_pending_leaves
        for item in results
    )
    expected_pending_seen = len(direct_pending) + unpaired_waiting_markers
    if receipt["pending_seen"] != expected_pending_seen:
        raise _ReceiptValidationError("accepted queue pending result count mismatch")
    directly_skipped = sum(
        item["status"]
        in {
            "pending_append_busy",
            "pending_append_dirty",
            "pending_young",
            "pending_active",
            "pending_would_promote",
        }
        for item in direct_pending
    )
    if receipt["pending_skipped"] != directly_skipped + unpaired_waiting_markers:
        raise _ReceiptValidationError("accepted queue pending skipped count mismatch")
    if receipt["pending_seen"] != (
        receipt["pending_promoted"]
        + receipt["pending_skipped"]
        + receipt["pending_failed"]
    ):
        raise _ReceiptValidationError("accepted queue pending count mismatch")


QueueInventoryEntry = tuple[str, str, str, str, str | None]


def _accepted_namespace_inventory(
    accepted_dir: Path,
) -> tuple[frozenset[QueueInventoryEntry], str | None]:
    expected_directories = {"pending", "ready", "replayed", "quarantine"}
    inventory: set[QueueInventoryEntry] = set()
    try:
        with os.scandir(accepted_dir) as entries:
            root_entries = list(entries)
        for entry in root_entries:
            if entry.name not in expected_directories:
                return frozenset(), "accepted queue contains an unexpected root entry"
            if _directory_entry_is_reparse(entry) or not entry.is_dir(
                follow_symlinks=False
            ):
                return (
                    frozenset(),
                    "accepted queue state path is not a plain directory",
                )
            inventory.add(("state", "directory", entry.name, "", None))
        for state_name in ("pending", "ready"):
            state_dir = accepted_dir / state_name
            try:
                with os.scandir(state_dir) as entries:
                    state_entries = list(entries)
            except FileNotFoundError:
                continue
            for entry in state_entries:
                if _directory_entry_is_reparse(entry) or not entry.is_file(
                    follow_symlinks=False
                ):
                    return frozenset(), (
                        "accepted queue contains a non-file entry in "
                        f"{state_name}"
                    )
                physical_leaf = entry.name
                leaf = physical_leaf
                kind = "wal"
                marker_match = re.fullmatch(
                    r"\.((?:bridge-wal-v1-[0-9a-f]{32}\.jsonl))"
                    r"\.pending-recovery-blocked",
                    leaf,
                )
                if state_name == "ready" and marker_match is not None:
                    leaf = marker_match.group(1)
                    kind = "marker"
                content_sha256, content = _plain_file_snapshot(
                    Path(entry.path),
                    include_bytes=kind == "marker",
                )
                authority_sha256 = (
                    _marker_expected_sha256(content, leaf)
                    if kind == "marker"
                    else content_sha256
                )
                inventory.add(
                    (
                        state_name,
                        kind,
                        leaf,
                        content_sha256,
                        authority_sha256,
                    )
                )
    except (OSError, ValueError) as exc:
        return (
            frozenset(),
            f"{type(exc).__name__}: accepted queue namespace scan failed",
        )
    return frozenset(inventory), None


def _receipt_inventory_binding_error(
    results: list[dict[str, Any]],
    inventory: frozenset[QueueInventoryEntry],
) -> str | None:
    authority = {
        (namespace, kind, leaf): (content_sha256, authority_sha256)
        for namespace, kind, leaf, content_sha256, authority_sha256 in inventory
        if namespace in {"pending", "ready"}
    }
    expected: set[tuple[str, str, str]] = set()
    result_by_identity: dict[tuple[str, str], dict[str, Any]] = {}
    marker_statuses = {
        "digest_marker_waiting_for_pending",
        "orphan_block_would_clear",
        "orphan_block_failed",
    }
    for item in results:
        status = item["status"]
        leaf = item["leaf"]
        if status in {
            "digest_marker_resolved_concurrently",
            "pending_append_release_failed",
        }:
            continue
        if type(leaf) is not str:
            return "accepted queue receipt has an unbound authority leaf"
        namespace = item["namespace"]
        kind = "marker" if status in marker_statuses else "wal"
        key = (namespace, kind, leaf)
        expected.add(key)
        result_by_identity[(namespace, leaf)] = item
        actual = authority.get(key)
        if actual is None:
            return "accepted queue receipt references missing queue authority"
        digest = item["sha256"]
        if type(digest) is str and actual[1] != digest:
            return "accepted queue receipt digest does not bind retained authority"

    for key, (_, marker_digest) in authority.items():
        namespace, kind, leaf = key
        if namespace != "ready" or kind != "marker" or key in expected:
            continue
        ready_result = result_by_identity.get(("ready", leaf))
        ready_wal = authority.get(("ready", "wal", leaf))
        if ready_result is None or ready_wal is None:
            continue
        expected.add(key)
        digest = ready_result["sha256"]
        if type(digest) is str and marker_digest != digest:
            return "accepted queue marker does not bind retained ready WAL"

    for item in results:
        if item["status"] != "digest_marker_waiting_for_pending":
            continue
        leaf = item["leaf"]
        pending = authority.get(("pending", "wal", leaf))
        if pending is None or pending[0] != item["sha256"]:
            return "accepted queue marker does not bind retained pending WAL"

    if set(authority) != expected:
        return "accepted queue receipt does not bind current queue inventory"
    return None


def _plain_queue_chain_error(requested_root: Path) -> str | None:
    root_chain: list[Path] = []
    cursor = requested_root
    while True:
        root_chain.append(cursor)
        parent = cursor.parent
        if parent == cursor:
            break
        cursor = parent
    queue_chain = [
        *reversed(root_chain),
        requested_root / "spool",
        requested_root / "spool" / "accepted-v1",
    ]
    seen: set[str] = set()
    for path in queue_chain:
        key = _path_key(path)
        if key in seen:
            continue
        seen.add(key)
        try:
            info = os.lstat(path)
        except FileNotFoundError:
            break
        except (OSError, ValueError) as exc:
            return f"{type(exc).__name__}: accepted queue path chain is unreadable"
        if not stat.S_ISDIR(info.st_mode) or _stat_is_reparse(info):
            return "accepted queue path chain contains a non-plain directory"
    return None


def _directory_entry_is_reparse(entry: os.DirEntry[str]) -> bool:
    if entry.is_symlink():
        return True
    is_junction = getattr(os.path, "isjunction", None)
    if callable(is_junction) and is_junction(entry.path):
        return True
    try:
        attributes = getattr(entry.stat(follow_symlinks=False), "st_file_attributes", 0)
    except OSError:
        return True
    return bool(
        attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )


def _open_canonical_read_descriptor(events_path: Path) -> int:
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    if os.name != "nt":
        return os.open(events_path, flags)

    # os.open does not expose Windows share-mode control.  A read-only
    # CreateFile handle with FILE_SHARE_READ intentionally denies writes and
    # deletion for the lifetime of the final duplicate-proof lease.
    import ctypes
    from ctypes import wintypes
    import msvcrt

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    handle = kernel32.CreateFileW(
        os.fspath(events_path),
        0x80000000,  # GENERIC_READ
        0x00000001,  # FILE_SHARE_READ
        None,
        3,  # OPEN_EXISTING
        0x00200000,  # FILE_FLAG_OPEN_REPARSE_POINT
        None,
    )
    if handle == wintypes.HANDLE(-1).value:
        code = ctypes.get_last_error()
        detail = ctypes.FormatError(code).strip()
        raise OSError(code, f"canonical bridge history read lease failed: {detail}")
    try:
        return msvcrt.open_osfhandle(handle, flags)
    except Exception:
        kernel32.CloseHandle(handle)
        raise


@contextmanager
def _bridge_named_mutex_lease(
    *,
    name: str,
    label: str,
) -> Iterator[None]:
    if os.name != "nt":
        yield
        return

    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = [
        wintypes.LPVOID,
        wintypes.BOOL,
        wintypes.LPCWSTR,
    ]
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.ReleaseMutex.argtypes = [wintypes.HANDLE]
    kernel32.ReleaseMutex.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    handle = kernel32.CreateMutexW(
        None,
        False,
        name,
    )
    if not handle:
        code = ctypes.get_last_error()
        raise OSError(code, f"{label} mutex creation failed")
    acquired = False
    try:
        wait = int(kernel32.WaitForSingleObject(handle, 5_000))
        if wait == 0x00000080:  # WAIT_ABANDONED
            acquired = True
            raise ValueError(f"{label} mutex ownership was abandoned")
        if wait == 0x00000102:  # WAIT_TIMEOUT
            raise ValueError(f"{label} mutex is busy")
        if wait == 0xFFFFFFFF:  # WAIT_FAILED
            code = ctypes.get_last_error()
            raise OSError(code, f"{label} mutex wait failed")
        if wait != 0x00000000:  # WAIT_OBJECT_0
            raise OSError(f"{label} mutex returned 0x{wait:08x}")
        acquired = True
        yield
    finally:
        release_error: OSError | None = None
        if acquired and not kernel32.ReleaseMutex(handle):
            code = ctypes.get_last_error()
            release_error = OSError(code, f"{label} mutex release failed")
        if not kernel32.CloseHandle(handle) and release_error is None:
            code = ctypes.get_last_error()
            release_error = OSError(code, f"{label} mutex close failed")
        if release_error is not None:
            raise release_error


@contextmanager
def _bridge_queue_publication_lease() -> Iterator[None]:
    with _bridge_named_mutex_lease(
        name=QUEUE_PUBLICATION_MUTEX_NAME,
        label="accepted queue publication fence",
    ):
        yield


@contextmanager
def _bridge_append_mutex_lease() -> Iterator[None]:
    with _bridge_named_mutex_lease(
        name=APPEND_MUTEX_NAME,
        label="canonical append",
    ):
        yield


@contextmanager
def _canonical_proof_lease(
    events_path: Path,
) -> Iterator[tuple[set[str], str]]:
    parent_snapshot = _plain_directory_chain_snapshot(events_path.parent)
    try:
        before = os.lstat(events_path)
    except (OSError, ValueError) as exc:
        raise ValueError("canonical bridge history leaf is unreadable") from exc
    if (
        not stat.S_ISREG(before.st_mode)
        or _stat_is_reparse(before)
        or before.st_nlink != 1
    ):
        raise ValueError("canonical bridge history leaf is not a plain file")
    descriptor = _open_canonical_read_descriptor(events_path)
    try:
        opened = os.fstat(descriptor)
        after_open = os.lstat(events_path)
        if (
            not os.path.samestat(before, after_open)
            or not os.path.samestat(opened, after_open)
            or _stat_is_reparse(after_open)
            or opened.st_nlink != 1
        ):
            raise ValueError("canonical bridge history leaf changed before read")
        hashes: set[str] = set()
        stream_digest = hashlib.sha256()
        row_digest = hashlib.sha256()
        row_bytes = 0
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            stream_digest.update(chunk)
            start = 0
            while start < len(chunk):
                end = chunk.find(b"\n", start)
                if end < 0:
                    segment = chunk[start:]
                    row_digest.update(segment)
                    row_bytes += len(segment)
                    break
                segment = chunk[start : end + 1]
                row_digest.update(segment)
                row_bytes += len(segment)
                hashes.add(row_digest.hexdigest())
                row_digest = hashlib.sha256()
                row_bytes = 0
                start = end + 1
        after_read = os.fstat(descriptor)
        after_read_path = os.lstat(events_path)
        if (
            not os.path.samestat(opened, after_read)
            or not os.path.samestat(after_read, after_read_path)
            or _stat_is_reparse(after_read_path)
            or after_read.st_nlink != 1
            or after_read.st_size != opened.st_size
            or after_read.st_mtime_ns != opened.st_mtime_ns
        ):
            raise ValueError("canonical bridge history changed during read")
        if row_bytes:
            raise ValueError("canonical bridge history has an unterminated row")
        try:
            yield hashes, stream_digest.hexdigest()
        finally:
            after_hold = os.fstat(descriptor)
            final_path = os.lstat(events_path)
            if (
                not os.path.samestat(opened, after_hold)
                or not os.path.samestat(after_hold, final_path)
                or _stat_is_reparse(final_path)
                or after_hold.st_nlink != 1
                or after_hold.st_size != opened.st_size
                or after_hold.st_mtime_ns != opened.st_mtime_ns
            ):
                raise ValueError(
                    "canonical bridge history changed while proof was held"
                )
            final_parent_snapshot = _plain_directory_chain_snapshot(
                events_path.parent
            )
            if len(parent_snapshot) != len(final_parent_snapshot) or any(
                before_path != after_path
                or not os.path.samestat(before_info, after_info)
                for (before_path, before_info), (after_path, after_info) in zip(
                    parent_snapshot,
                    final_parent_snapshot,
                )
            ):
                raise ValueError(
                    "canonical bridge history parent chain changed while proof "
                    "was held"
                )
    finally:
        os.close(descriptor)


def _complete_canonical_row_hashes(events_path: Path) -> tuple[set[str], str]:
    with _canonical_proof_lease(events_path) as proof:
        return proof


def _plain_directory_chain_snapshot(
    directory: Path,
) -> tuple[tuple[Path, os.stat_result], ...]:
    chain: list[Path] = []
    cursor = directory
    while True:
        chain.append(cursor)
        parent = cursor.parent
        if parent == cursor:
            break
        cursor = parent
    snapshots: list[tuple[Path, os.stat_result]] = []
    for candidate in reversed(chain):
        try:
            info = os.lstat(candidate)
        except (OSError, ValueError) as exc:
            raise ValueError(
                "canonical bridge history parent chain is unreadable"
            ) from exc
        if not stat.S_ISDIR(info.st_mode) or _stat_is_reparse(info):
            raise ValueError(
                "canonical bridge history parent chain is not plain"
            )
        snapshots.append((candidate, info))
    return tuple(snapshots)


def _plain_file_snapshot(
    path: Path,
    *,
    include_bytes: bool,
) -> tuple[str, bytes]:
    before = os.lstat(path)
    if (
        not stat.S_ISREG(before.st_mode)
        or _stat_is_reparse(before)
        or before.st_nlink != 1
    ):
        raise ValueError("accepted queue authority leaf is not a plain file")
    if include_bytes and before.st_size > MAX_MARKER_BYTES:
        raise ValueError("accepted queue digest marker is oversized")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        after_open = os.lstat(path)
        if (
            not os.path.samestat(opened, after_open)
            or _stat_is_reparse(after_open)
            or opened.st_nlink != 1
        ):
            raise ValueError("accepted queue authority leaf changed before read")
        digest = hashlib.sha256()
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            if include_bytes:
                chunks.append(chunk)
        after_read = os.fstat(descriptor)
        final_path = os.lstat(path)
        if (
            not os.path.samestat(opened, after_read)
            or not os.path.samestat(after_read, final_path)
            or _stat_is_reparse(final_path)
            or after_read.st_nlink != 1
            or after_read.st_size != opened.st_size
            or after_read.st_mtime_ns != opened.st_mtime_ns
        ):
            raise ValueError("accepted queue authority leaf changed during read")
        return digest.hexdigest(), b"".join(chunks)
    finally:
        os.close(descriptor)


def _marker_expected_sha256(content: bytes, leaf: str) -> str | None:
    try:
        marker = _strict_json_object(content.decode("utf-8", errors="strict"))
    except (RecursionError, UnicodeError, ValueError):
        return None
    expected = marker.get("expected_sha256")
    if (
        marker.get("schema") != "waggledance.bridge.accepted-pending-block.v1"
        or marker.get("wal_leaf") != leaf
        or type(expected) is not str
        or SHA256_RE.fullmatch(expected) is None
    ):
        return None
    return expected


def _strict_json_object(text: str) -> dict[str, Any]:
    if not text or text.startswith("\ufeff"):
        raise _ReceiptValidationError("accepted queue receipt is not strict JSON")

    def object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise _ReceiptValidationError(
                    f"accepted queue receipt repeats key {key!r}"
                )
            result[key] = value
        return result

    def reject_constant(value: str) -> None:
        raise _ReceiptValidationError(
            f"accepted queue receipt contains non-finite number {value}"
        )

    value = json.loads(
        text,
        object_pairs_hook=object_pairs,
        parse_constant=reject_constant,
    )
    if type(value) is not dict:
        raise _ReceiptValidationError("accepted queue receipt must be an object")
    return value


def _absolute_path(value: Path | os.PathLike[str]) -> Path:
    if not isinstance(value, (Path, os.PathLike)):
        raise TypeError("path must be path-like")
    raw = os.fspath(value)
    if type(raw) is not str or "\x00" in raw:
        raise ValueError("path must be a NUL-free string path")
    return Path(os.path.abspath(raw))


def _path_key(value: Path | str) -> str:
    return os.path.normcase(os.path.normpath(os.path.abspath(os.fspath(value))))


def _stat_is_reparse(info: os.stat_result) -> bool:
    attributes = getattr(info, "st_file_attributes", 0)
    return bool(
        attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )


def _powershell_executable() -> str:
    for name in (("powershell.exe", "pwsh") if os.name == "nt" else ("pwsh", "powershell")):
        found = shutil.which(name)
        if found:
            return found
    return "powershell.exe" if os.name == "nt" else "pwsh"


def _error_report(
    *,
    bridge_root: object,
    decision: str,
    error: str,
    events_path: object = "",
    receipt_summary: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "ok": False,
        "complete": False,
        "decision": decision,
        "bridge_root": str(bridge_root),
        "events_path": str(events_path),
        "unresolved": [],
        "resolved_duplicates": [],
        "errors": [error],
        "receipt_summary": (
            dict(receipt_summary) if receipt_summary is not None else None
        ),
    }


__all__ = [
    "AcceptedQueueRunner",
    "DEFAULT_DRAIN_SCRIPT",
    "bridge_events_path_matches_root",
    "check_accepted_queue_complete",
]

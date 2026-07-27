# SPDX-License-Identifier: BUSL-1.1
"""Tests for the receiver-owned chat-served replay registry."""
from __future__ import annotations

import json
import multiprocessing as mp
import os
import queue
import stat
import subprocess
import sys
import threading
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from waggledance.core.magma import chat_served_window_registry as R
from waggledance.core.magma.canonical import canonical_json_bytes, sha256_digest
from waggledance.core.magma.chat_served_claim_window_evidence import (
    ProductionWindowVerification,
)


def _digest(label: str) -> str:
    return sha256_digest({"test_label": label})


def _marker_path(index: int = 1) -> Path:
    return Path.cwd() / ".registry-test-markers" / f"window-{index}.json"


def _binding(index: int = 1) -> R.WindowRegistryBinding:
    return R.WindowRegistryBinding(
        window_id=f"window:{index:032x}",
        source_head=f"{index + 100:040x}",
        binding_digest=_digest(f"{index}:binding"),
        start_boundary_digest=_digest(f"{index}:start"),
        final_boundary_digest=_digest(f"{index}:final"),
        marker_path_digest=R.derive_window_marker_path_digest(
            _marker_path(index)
        ),
        marker_digest=_digest(f"{index}:marker"),
        final_ledger_head=_digest(f"{index}:ledger-head"),
    )


def _verdict(
    phase: str,
    marker_verified: bool,
    **overrides: object,
) -> ProductionWindowVerification:
    verdict = ProductionWindowVerification(
        ok=True,
        phase=phase,
        reason=None,
        marker_verified=marker_verified,
        ledger_entries=2,
        enabled_samples=2,
        pending_failures=0,
        receipt_index_entries=1,
        served_point_observations=5,
        receipt_terminals=1,
    )
    return verdict._replace(**overrides)


def _approval(
    binding: R.WindowRegistryBinding,
    *,
    phase: str = "pre_marker_verified",
    marker_verified: bool = False,
    **verdict_overrides: object,
) -> R.RegistryVerificationApproval:
    return R.RegistryVerificationApproval(
        binding=binding,
        verdict=_verdict(
            phase,
            marker_verified,
            **verdict_overrides,
        ),
    )


def _reserve(
    registry: R.ChatServedWindowRegistry,
    binding: R.WindowRegistryBinding,
) -> None:
    registry.reserve_after_verification(
        binding,
        lambda _prior: _approval(binding),
    )


_SPAWN_WAIT_SECONDS = 20.0
_VERIFIER_CRASH_EXIT_CODE = 71
_POST_APPEND_CRASH_EXIT_CODE = 72


def _spawn_reservation_race_worker(
    registry_path: str,
    binding: R.WindowRegistryBinding,
    start_gate: Any,
    ready_queue: Any,
    result_queue: Any,
) -> None:
    pid = os.getpid()
    ready_queue.put((pid,))
    if not start_gate.wait(_SPAWN_WAIT_SECONDS):
        result_queue.put((pid, "unexpected", "start_gate_timeout"))
        return
    try:
        record = R.ChatServedWindowRegistry(
            registry_path,
            lock_timeout_seconds=10.0,
        ).reserve_after_verification(
            binding,
            lambda _prior: _approval(binding),
        )
    except R.WindowRegistryReplayError as exc:
        result_queue.put((pid, "replay", str(exc)))
    except Exception as exc:
        result_queue.put(
            (pid, "unexpected", f"{type(exc).__name__}:{exc}")
        )
    else:
        result_queue.put((pid, "reserved", record["event_type"]))


def _spawn_holding_finalizer_worker(
    registry_path: str,
    binding: R.WindowRegistryBinding,
    callback_entered: Any,
    release_callback: Any,
    result_queue: Any,
) -> None:
    pid = os.getpid()

    def verifier(_prior: tuple[str, ...]) -> R.RegistryVerificationApproval:
        callback_entered.set()
        if not release_callback.wait(_SPAWN_WAIT_SECONDS):
            raise RuntimeError("release_callback_timeout")
        return _approval(
            binding,
            phase=R.FINAL_VERIFIED,
            marker_verified=True,
        )

    try:
        record = R.ChatServedWindowRegistry(
            registry_path,
            lock_timeout_seconds=10.0,
        ).finalize_after_verification(binding, verifier)
    except Exception as exc:
        result_queue.put(
            (pid, "unexpected", f"{type(exc).__name__}:{exc}")
        )
    else:
        result_queue.put((pid, "finalized", record["event_type"]))


def _spawn_reserve_busy_then_retry_worker(
    registry_path: str,
    binding: R.WindowRegistryBinding,
    first_attempt_done: Any,
    allow_retry: Any,
    result_queue: Any,
) -> None:
    pid = os.getpid()
    try:
        R.ChatServedWindowRegistry(
            registry_path,
            lock_timeout_seconds=0,
        ).reserve_after_verification(
            binding,
            lambda _prior: _approval(binding),
        )
    except R.WindowRegistryLockError as exc:
        if str(exc) != "registry_lock_busy":
            result_queue.put(
                (pid, "unexpected", f"{type(exc).__name__}:{exc}")
            )
            return
        first_attempt_done.set()
    except Exception as exc:
        result_queue.put(
            (pid, "unexpected", f"{type(exc).__name__}:{exc}")
        )
        return
    else:
        result_queue.put(
            (pid, "unexpected", "first_attempt_bypassed_finalizer_lock")
        )
        return

    if not allow_retry.wait(_SPAWN_WAIT_SECONDS):
        result_queue.put((pid, "unexpected", "allow_retry_timeout"))
        return
    try:
        record = R.ChatServedWindowRegistry(
            registry_path,
            lock_timeout_seconds=10.0,
        ).reserve_after_verification(
            binding,
            lambda _prior: _approval(binding),
        )
    except Exception as exc:
        result_queue.put(
            (pid, "unexpected", f"{type(exc).__name__}:{exc}")
        )
    else:
        result_queue.put(
            (pid, "lock_busy_then_reserved", record["event_type"])
        )


def _spawn_exit_in_verifier_worker(
    registry_path: str,
    binding: R.WindowRegistryBinding,
    callback_entered: Any,
) -> None:
    def verifier(_prior: tuple[str, ...]) -> R.RegistryVerificationApproval:
        callback_entered.set()
        os._exit(_VERIFIER_CRASH_EXIT_CODE)

    R.ChatServedWindowRegistry(
        registry_path,
        lock_timeout_seconds=10.0,
    ).reserve_after_verification(binding, verifier)


def _spawn_exit_after_append_worker(
    registry_path: str,
    binding: R.WindowRegistryBinding,
    append_completed: Any,
) -> None:
    original_append = R._append_record_unlocked

    def append_then_exit(*args: Any, **kwargs: Any) -> None:
        original_append(*args, **kwargs)
        append_completed.set()
        os._exit(_POST_APPEND_CRASH_EXIT_CODE)

    R._append_record_unlocked = append_then_exit
    R.ChatServedWindowRegistry(
        registry_path,
        lock_timeout_seconds=10.0,
    ).reserve_after_verification(
        binding,
        lambda _prior: _approval(binding),
    )


def _fork_snapshot_after_release_worker(
    registry_path: str,
    inherited_lock_fds: tuple[tuple[int, int, int], ...],
    allow_snapshot: Any,
    result_queue: Any,
) -> None:
    pid = os.getpid()
    leaked_lock_fds: list[int] = []
    for fd, expected_device, expected_inode in inherited_lock_fds:
        try:
            details = os.fstat(fd)
        except OSError:
            continue
        if (
            int(details.st_dev),
            int(details.st_ino),
        ) == (expected_device, expected_inode):
            leaked_lock_fds.append(fd)
    result_queue.put(
        (
            pid,
            "fork_state",
            tuple(sorted(R._ACTIVE_LOCK_FDS)),
            tuple(leaked_lock_fds),
        )
    )
    if not allow_snapshot.wait(_SPAWN_WAIT_SECONDS):
        result_queue.put((pid, "unexpected", "allow_snapshot_timeout"))
        return
    try:
        snapshot = R.ChatServedWindowRegistry(
            registry_path,
            lock_timeout_seconds=1.0,
        ).snapshot()
    except Exception as exc:
        result_queue.put(
            (pid, "unexpected", f"{type(exc).__name__}:{exc}")
        )
    else:
        result_queue.put((pid, "snapshot", len(snapshot.records)))


def _fork_inside_verifier_worker(
    registry_path: str,
    result_queue: Any,
) -> None:
    original_pid = os.getpid()
    binding = _binding(36)
    read_fd, write_fd = os.pipe()
    forked_pids: list[int] = []

    def verifier(_prior: tuple[str, ...]) -> R.RegistryVerificationApproval:
        forked_pid = os.fork()
        if forked_pid == 0:
            return _approval(binding)
        forked_pids.append(forked_pid)
        return _approval(binding)

    try:
        record = R.ChatServedWindowRegistry(
            registry_path,
            lock_timeout_seconds=2.0,
        ).reserve_after_verification(binding, verifier)
    except Exception as exc:
        if os.getpid() != original_pid:
            payload = f"{type(exc).__name__}:{exc}".encode("utf-8")
            try:
                os.write(write_fd, payload)
            finally:
                os._exit(
                    0
                    if payload
                    == (
                        b"WindowRegistryLockError:"
                        b"process_forked_during_verification"
                    )
                    else 81
                )
        result_queue.put(
            (
                original_pid,
                "unexpected",
                f"{type(exc).__name__}:{exc}",
            )
        )
        return

    if os.getpid() != original_pid:
        try:
            os.write(write_fd, b"unexpected_child_transition")
        finally:
            os._exit(82)

    os.close(write_fd)
    forked_pid = forked_pids[0]
    waited_pid, wait_status = os.waitpid(forked_pid, 0)
    child_payload = os.read(read_fd, 4096).decode("utf-8")
    os.close(read_fd)
    snapshot = R.ChatServedWindowRegistry(registry_path).snapshot()
    result_queue.put(
        (
            original_pid,
            "parent_reserved",
            record["event_type"],
            waited_pid,
            os.waitstatus_to_exitcode(wait_status),
            child_payload,
            len(snapshot.records),
        )
    )


def _spawn_queue_get(result_queue: Any) -> tuple[Any, ...]:
    try:
        return result_queue.get(timeout=_SPAWN_WAIT_SECONDS)
    except queue.Empty:
        pytest.fail("spawned registry worker did not report before timeout")


def _reap_spawned_processes(processes: list[Any]) -> None:
    for process in processes:
        process.join(timeout=_SPAWN_WAIT_SECONDS)
    for process in processes:
        if process.is_alive():
            process.terminate()
            process.join(timeout=5.0)
        if process.is_alive():
            process.kill()
            process.join(timeout=5.0)
        if process.is_alive():
            pytest.fail("spawned registry worker survived terminate and kill")


def _assert_registry_is_canonical_and_valid(
    path: Path,
    snapshot: R.WindowRegistrySnapshot,
) -> None:
    expected = b"".join(
        canonical_json_bytes(dict(record)) + b"\n"
        for record in snapshot.records
    )
    assert path.read_bytes() == expected
    assert b"\r" not in expected
    assert R.verify_registry_chain(snapshot.records) == R.RegistryChainResult(
        True,
        None,
        None,
    )


def test_marker_path_digest_binds_exact_absolute_target(tmp_path) -> None:
    target = tmp_path / "clean-shutdown.json"
    copied = tmp_path / "copy" / "clean-shutdown.json"
    pending = target.with_name(f".{target.name}.pending")

    target_digest = R.derive_window_marker_path_digest(target)

    assert R.is_registry_digest(target_digest)
    assert target_digest == R.derive_window_marker_path_digest(target)
    assert target_digest != R.derive_window_marker_path_digest(copied)
    assert target_digest != R.derive_window_marker_path_digest(pending)
    assert (
        R.WINDOW_MARKER_PATH_DIGEST_SCHEMA
        == "magma.chat_served_window_marker_path_digest.v1"
    )


def test_marker_path_digest_rejects_relative_empty_and_nul_paths() -> None:
    with pytest.raises(
        R.WindowRegistryPathError,
        match="marker_path_must_be_absolute",
    ):
        R.derive_window_marker_path_digest("markers/clean.json")
    with pytest.raises(R.WindowRegistryPathError, match="marker_path_empty"):
        R.derive_window_marker_path_digest("")
    with pytest.raises(
        R.WindowRegistryPathError,
        match="marker_path_nul_not_allowed",
    ):
        R.derive_window_marker_path_digest(
            os.fspath(Path.cwd() / "clean.json") + "\x00suffix"
        )


def test_two_phase_round_trip_separates_consumed_from_verified(tmp_path) -> None:
    path = tmp_path / "verified-windows.jsonl"
    registry = R.ChatServedWindowRegistry(path)
    binding = _binding()
    reserve_prior: list[tuple[str, ...]] = []
    finalize_prior: list[tuple[str, ...]] = []

    reservation = registry.reserve_after_verification(
        binding,
        lambda prior: (
            reserve_prior.append(prior) or _approval(binding)
        ),
    )
    reserved = registry.snapshot()

    assert reservation["event_type"] == R.RESERVED_PRE_MARKER
    assert reservation["marker_path_digest"] == binding.marker_path_digest
    assert reserve_prior == [()]
    assert reserved.consumed_window_ids == (binding.window_id,)
    assert reserved.verified_window_ids == ()
    assert reserved.reservation_for(binding.window_id) == reservation
    assert reserved.finalization_for(binding.window_id) is None
    assert reserved.measurement_only is True
    assert reserved.claim_safe_count == 0
    assert reserved.runtime_authority_granted is False

    finalization = registry.finalize_after_verification(
        binding,
        lambda prior: (
            finalize_prior.append(prior)
            or _approval(
                binding,
                phase=R.FINAL_VERIFIED,
                marker_verified=True,
            )
        ),
    )
    final = registry.snapshot()

    assert finalization["event_type"] == R.FINAL_VERIFIED
    assert finalization["marker_path_digest"] == binding.marker_path_digest
    assert finalization["reservation_hash"] == reservation["record_hash"]
    assert finalize_prior == [()]
    assert final.consumed_window_ids == (binding.window_id,)
    assert final.verified_window_ids == (binding.window_id,)
    assert final.is_consumed(binding.window_id) is True
    assert final.is_verified(binding.window_id) is True
    assert R.verify_registry_chain(final.records).ok is True
    assert final.head_hash == finalization["record_hash"]
    raw = path.read_bytes()
    assert raw.endswith(b"}\n")
    assert b"\r" not in raw
    assert len(raw.splitlines()) == 2
    for row in final.records:
        assert "marker_path" not in row
        assert row["measurement_only"] is True
        assert row["claim_safe_count"] == 0
        assert row["runtime_authority_granted"] is False


def test_callbacks_receive_consumed_ids_with_target_excluded_on_finalize(
    tmp_path,
) -> None:
    registry = R.ChatServedWindowRegistry(tmp_path / "registry.jsonl")
    first = _binding(1)
    second = _binding(2)
    observed: list[tuple[str, ...]] = []

    registry.reserve_after_verification(
        first,
        lambda prior: observed.append(prior) or _approval(first),
    )
    registry.reserve_after_verification(
        second,
        lambda prior: observed.append(prior) or _approval(second),
    )
    registry.finalize_after_verification(
        first,
        lambda prior: observed.append(prior)
        or _approval(
            first,
            phase=R.FINAL_VERIFIED,
            marker_verified=True,
        ),
    )
    registry.finalize_after_verification(
        second,
        lambda prior: observed.append(prior)
        or _approval(
            second,
            phase=R.FINAL_VERIFIED,
            marker_verified=True,
        ),
    )

    assert observed == [
        (),
        (first.window_id,),
        (second.window_id,),
        (first.window_id,),
    ]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("ok", False),
        ("phase", R.FINAL_VERIFIED),
        ("reason", "not_verified"),
        ("marker_verified", True),
        ("measurement_only", False),
        ("claim_safe_count", 1),
        ("claim_safe_count", 0.0),
        ("claim_safe_count", False),
        ("schema_version", "wrong.schema.v1"),
    ],
)
def test_reservation_rejects_any_non_pre_marker_verdict_field(
    tmp_path,
    field,
    value,
) -> None:
    registry = R.ChatServedWindowRegistry(tmp_path / "registry.jsonl")
    binding = _binding()

    with pytest.raises(
        R.WindowRegistryVerificationError,
        match=f"verification_verdict_invalid:{field}",
    ):
        registry.reserve_after_verification(
            binding,
            lambda _prior: _approval(
                binding,
                **{field: value},
            ),
        )

    assert registry.snapshot().records == ()


def test_approval_requires_exact_verdict_type_and_exact_binding(tmp_path) -> None:
    registry = R.ChatServedWindowRegistry(tmp_path / "registry.jsonl")
    binding = _binding()
    forged_mapping = _verdict(
        "pre_marker_verified",
        False,
    )._asdict()

    with pytest.raises(
        R.WindowRegistryVerificationError,
        match="verification_verdict_type_invalid",
    ):
        registry.reserve_after_verification(
            binding,
            lambda _prior: R.RegistryVerificationApproval(
                binding,
                forged_mapping,
            ),
        )

    with pytest.raises(
        R.WindowRegistryVerificationError,
        match="verification_approval_binding_mismatch",
    ):
        mismatched = replace(
            binding,
            marker_path_digest=_binding(2).marker_path_digest,
        )
        registry.reserve_after_verification(
            binding,
            lambda _prior: _approval(mismatched),
        )

    assert registry.snapshot().records == ()


def test_callback_failure_is_closed_and_persists_no_reservation(tmp_path) -> None:
    registry = R.ChatServedWindowRegistry(tmp_path / "registry.jsonl")
    binding = _binding()

    def fail(_prior):
        raise RuntimeError("private callback detail")

    with pytest.raises(
        R.WindowRegistryVerificationError,
        match="verification_callback_failed",
    ) as caught:
        registry.reserve_after_verification(binding, fail)

    assert isinstance(caught.value.__cause__, RuntimeError)
    assert registry.snapshot().records == ()


def test_finalization_callback_failure_preserves_only_reservation(
    tmp_path,
) -> None:
    registry = R.ChatServedWindowRegistry(tmp_path / "registry.jsonl")
    binding = _binding()
    _reserve(registry, binding)

    def fail(_prior):
        raise RuntimeError("private finalization callback detail")

    with pytest.raises(
        R.WindowRegistryVerificationError,
        match="verification_callback_failed",
    ) as caught:
        registry.finalize_after_verification(binding, fail)

    assert isinstance(caught.value.__cause__, RuntimeError)
    snapshot = registry.snapshot()
    assert snapshot.consumed_window_ids == (binding.window_id,)
    assert snapshot.verified_window_ids == ()
    assert len(snapshot.records) == 1


@pytest.mark.parametrize(
    "field",
    [
        "source_head",
        "binding_digest",
        "start_boundary_digest",
        "final_boundary_digest",
        "marker_path_digest",
        "marker_digest",
        "final_ledger_head",
    ],
)
def test_finalization_requires_every_exact_reservation_pin(
    tmp_path,
    field,
) -> None:
    registry = R.ChatServedWindowRegistry(tmp_path / "registry.jsonl")
    binding = _binding()
    _reserve(registry, binding)
    replacement = (
        f"{999:040x}"
        if field == "source_head"
        else _digest(f"wrong:{field}")
    )
    mismatched = replace(binding, **{field: replacement})
    callback_called = False

    def callback(_prior):
        nonlocal callback_called
        callback_called = True
        return _approval(
            mismatched,
            phase=R.FINAL_VERIFIED,
            marker_verified=True,
        )

    with pytest.raises(
        R.WindowRegistryStateError,
        match=f"{field}_pin_mismatch",
    ):
        registry.finalize_after_verification(mismatched, callback)

    assert callback_called is False
    assert registry.snapshot().verified_window_ids == ()


def test_finalize_requires_reservation_and_rejects_second_finalization(
    tmp_path,
) -> None:
    registry = R.ChatServedWindowRegistry(tmp_path / "registry.jsonl")
    binding = _binding()
    callback_called = False

    def callback(_prior):
        nonlocal callback_called
        callback_called = True
        return _approval(
            binding,
            phase=R.FINAL_VERIFIED,
            marker_verified=True,
        )

    with pytest.raises(R.WindowRegistryStateError, match="reservation_missing"):
        registry.finalize_after_verification(binding, callback)
    assert callback_called is False

    _reserve(registry, binding)
    registry.finalize_after_verification(binding, callback)
    callback_called = False
    with pytest.raises(
        R.WindowRegistryReplayError,
        match="window_already_finalized",
    ):
        registry.finalize_after_verification(binding, callback)
    assert callback_called is False


@pytest.mark.parametrize(
    "field",
    [
        "window_id",
        "binding_digest",
        "start_boundary_digest",
        "final_boundary_digest",
        "marker_path_digest",
        "marker_digest",
        "final_ledger_head",
    ],
)
def test_reservation_rejects_duplicate_or_semantic_replay_before_callback(
    tmp_path,
    field,
) -> None:
    registry = R.ChatServedWindowRegistry(tmp_path / "registry.jsonl")
    first = _binding(1)
    _reserve(registry, first)
    replay = replace(_binding(2), **{field: getattr(first, field)})
    callback_called = False

    def callback(_prior):
        nonlocal callback_called
        callback_called = True
        return _approval(replay)

    with pytest.raises(R.WindowRegistryReplayError, match=f"{field}_reused"):
        registry.reserve_after_verification(replay, callback)

    assert callback_called is False
    assert len(registry.snapshot().records) == 1


@pytest.mark.parametrize(
    "mutation",
    ["tamper", "extra_key", "authority", "broken_link"],
)
def test_snapshot_rejects_tampered_or_smuggled_chain(
    tmp_path,
    mutation,
) -> None:
    path = tmp_path / "registry.jsonl"
    registry = R.ChatServedWindowRegistry(path)
    _reserve(registry, _binding())
    record = json.loads(path.read_text(encoding="utf-8"))

    if mutation == "tamper":
        record["source_head"] = f"{777:040x}"
    elif mutation == "extra_key":
        record["raw_payload"] = "SECRET"
        record["record_hash"] = R.compute_registry_record_hash(record)
    elif mutation == "authority":
        record["runtime_authority_granted"] = True
        record["record_hash"] = R.compute_registry_record_hash(record)
    else:
        record["prev_record_hash"] = _digest("wrong-prev")
        record["record_hash"] = R.compute_registry_record_hash(record)
    path.write_bytes(canonical_json_bytes(record) + b"\n")

    with pytest.raises(R.WindowRegistryCorruptionError):
        registry.snapshot()


@pytest.mark.parametrize("value", [0.0, False])
def test_registry_row_requires_exact_integer_zero_claim_safe_count(
    tmp_path,
    value,
) -> None:
    path = tmp_path / "registry.jsonl"
    registry = R.ChatServedWindowRegistry(path)
    _reserve(registry, _binding())
    record = json.loads(path.read_text(encoding="utf-8"))
    record["claim_safe_count"] = value
    record["record_hash"] = R.compute_registry_record_hash(record)
    path.write_bytes(canonical_json_bytes(record) + b"\n")

    with pytest.raises(
        R.WindowRegistryCorruptionError,
        match="claim_safe_count",
    ):
        registry.snapshot()


@pytest.mark.parametrize(
    "raw",
    [
        b'{"partial":true',
        b"\n",
        b"{}\r\n",
        b'{"x":1,"x":2}\n',
        b"[]\n",
        b'{"x":NaN}\n',
        b'{"x":1e309}\n',
    ],
)
def test_strict_jsonl_rejects_torn_blank_cr_duplicate_nonobject_and_nan(
    tmp_path,
    raw,
) -> None:
    path = tmp_path / "registry.jsonl"
    path.write_bytes(raw)
    registry = R.ChatServedWindowRegistry(path)

    with pytest.raises(R.WindowRegistryCorruptionError):
        registry.snapshot()


def test_strict_jsonl_rejects_noncanonical_valid_record(tmp_path) -> None:
    path = tmp_path / "registry.jsonl"
    binding = _binding()
    record = R.build_window_reservation(
        sequence=0,
        **{
            field: getattr(binding, field)
            for field in (
                "window_id",
                "source_head",
                "binding_digest",
                "start_boundary_digest",
                "final_boundary_digest",
                "marker_path_digest",
                "marker_digest",
                "final_ledger_head",
            )
        },
        recorded_at_utc="2026-07-27T05:00:00Z",
        prev_record_hash=R.GENESIS_PREV_RECORD_HASH,
    )
    path.write_bytes(
        (json.dumps(record, sort_keys=True) + "\n").encode("utf-8")
    )

    with pytest.raises(
        R.WindowRegistryCorruptionError,
        match="jsonl_noncanonical_record",
    ):
        R.ChatServedWindowRegistry(path).snapshot()


def test_file_line_and_record_bounds_fail_closed_before_append(tmp_path) -> None:
    oversized_path = tmp_path / "oversized.jsonl"
    oversized_path.write_bytes(b"x" * 257)
    bounded_reader = R.ChatServedWindowRegistry(
        oversized_path,
        max_file_bytes=256,
        max_line_bytes=256,
    )
    with pytest.raises(
        R.WindowRegistryCorruptionError,
        match="registry_file_exceeds_bound",
    ):
        bounded_reader.snapshot()

    line_registry = R.ChatServedWindowRegistry(
        tmp_path / "line.jsonl",
        max_file_bytes=4096,
        max_line_bytes=128,
    )
    with pytest.raises(R.WindowRegistryError, match="jsonl_line_exceeds_bound"):
        line_registry.reserve_after_verification(
            _binding(1),
            lambda _prior: _approval(_binding(1)),
        )

    record_registry = R.ChatServedWindowRegistry(
        tmp_path / "record.jsonl",
        max_records=1,
    )
    binding = _binding(3)
    _reserve(record_registry, binding)
    callback_called = False

    def callback(_prior):
        nonlocal callback_called
        callback_called = True
        return _approval(
            binding,
            phase=R.FINAL_VERIFIED,
            marker_verified=True,
        )

    with pytest.raises(
        R.WindowRegistryError,
        match="registry_record_bound_exceeded",
    ):
        record_registry.finalize_after_verification(binding, callback)
    assert callback_called is False


def test_constructor_requires_explicit_absolute_receiver_path() -> None:
    with pytest.raises(
        R.WindowRegistryPathError,
        match="registry_path_must_be_absolute",
    ):
        R.ChatServedWindowRegistry("relative/registry.jsonl")


@pytest.mark.parametrize("value", ["1", b"1", True])
def test_constructor_rejects_coercible_non_numeric_lock_timeout(
    tmp_path,
    value,
) -> None:
    with pytest.raises(
        R.WindowRegistryLockError,
        match="lock_timeout_invalid",
    ):
        R.ChatServedWindowRegistry(
            tmp_path / "registry.jsonl",
            lock_timeout_seconds=value,
        )


@pytest.mark.parametrize("value", [10**400, -(10**400)])
def test_constructor_normalizes_lock_timeout_overflow_to_contract_error(
    tmp_path,
    value,
) -> None:
    with pytest.raises(
        R.WindowRegistryLockError,
        match="lock_timeout_invalid",
    ):
        R.ChatServedWindowRegistry(
            tmp_path / "registry.jsonl",
            lock_timeout_seconds=value,
        )


def test_registry_rejects_symlinked_parent(tmp_path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    linked = tmp_path / "linked"
    try:
        linked.symlink_to(real, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlink creation is unavailable")

    with pytest.raises(
        R.WindowRegistryPathError,
        match="path_reparse_not_allowed",
    ):
        R.ChatServedWindowRegistry(linked / "registry.jsonl")


def test_windows_reparse_attribute_is_recognized(monkeypatch) -> None:
    details = SimpleNamespace(
        st_mode=stat.S_IFREG,
        st_file_attributes=0x400,
    )
    monkeypatch.setattr(R.os, "lstat", lambda _path: details)

    assert R._path_is_link_or_reparse(Path("registry.jsonl")) is True


@pytest.mark.skipif(os.name != "nt", reason="Windows 8.3 alias behavior")
def test_windows_short_and_long_paths_share_lock_and_marker_digest(
    tmp_path,
) -> None:
    import ctypes
    from ctypes import wintypes

    long_dir = tmp_path / "registry-directory-with-a-long-name"
    long_dir.mkdir()
    get_short_path_name = ctypes.WinDLL(
        "kernel32",
        use_last_error=True,
    ).GetShortPathNameW
    get_short_path_name.argtypes = (
        wintypes.LPCWSTR,
        wintypes.LPWSTR,
        wintypes.DWORD,
    )
    get_short_path_name.restype = wintypes.DWORD

    def short_path(path: Path) -> Path:
        required = get_short_path_name(os.fspath(path), None, 0)
        if required == 0:
            pytest.skip("8.3 short names are unavailable")
        buffer = ctypes.create_unicode_buffer(required)
        written = get_short_path_name(
            os.fspath(path),
            buffer,
            required,
        )
        if written == 0 or written >= required:
            pytest.skip("8.3 short names are unavailable")
        short = Path(buffer.value)
        if os.path.normcase(os.fspath(short)) == os.path.normcase(
            os.fspath(path)
        ):
            pytest.skip("volume did not provide a distinct 8.3 alias")
        return short

    long_registry_path = long_dir / "verified-windows.jsonl"
    long_registry = R.ChatServedWindowRegistry(long_registry_path)
    _reserve(long_registry, _binding())
    short_registry_path = short_path(long_registry_path)
    short_registry = R.ChatServedWindowRegistry(
        short_registry_path,
        lock_timeout_seconds=0,
    )

    assert long_registry.path == short_registry.path
    assert long_registry.lock_path == short_registry.lock_path
    with long_registry._locked():
        with pytest.raises(
            R.WindowRegistryLockError,
            match="registry_lock_busy",
        ):
            short_registry.snapshot()

    long_marker = long_dir / "clean-shutdown-marker.json"
    long_marker.write_bytes(b"{}")
    short_marker = short_path(long_marker)
    assert R.derive_window_marker_path_digest(
        long_marker
    ) == R.derive_window_marker_path_digest(short_marker)


@pytest.mark.skipif(os.name != "nt", reason="Windows 8.3 alias behavior")
def test_windows_missing_short_alias_cannot_rebind_after_construction(
    tmp_path,
) -> None:
    import ctypes
    from ctypes import wintypes

    long_path = tmp_path / "verified-windows-registry-long-name.jsonl"
    long_path.write_bytes(b"")
    get_short_path_name = ctypes.WinDLL(
        "kernel32",
        use_last_error=True,
    ).GetShortPathNameW
    get_short_path_name.argtypes = (
        wintypes.LPCWSTR,
        wintypes.LPWSTR,
        wintypes.DWORD,
    )
    get_short_path_name.restype = wintypes.DWORD
    required = get_short_path_name(os.fspath(long_path), None, 0)
    if required == 0:
        pytest.skip("8.3 short names are unavailable")
    buffer = ctypes.create_unicode_buffer(required)
    written = get_short_path_name(
        os.fspath(long_path),
        buffer,
        required,
    )
    if written == 0 or written >= required:
        pytest.skip("8.3 short names are unavailable")
    short_path = Path(buffer.value)
    if os.path.normcase(os.fspath(short_path)) == os.path.normcase(
        os.fspath(long_path)
    ):
        pytest.skip("volume did not provide a distinct 8.3 alias")

    long_path.unlink()
    registry = R.ChatServedWindowRegistry(short_path)
    frozen_path = registry.path
    frozen_lock_path = registry.lock_path
    long_path.write_bytes(b"")
    try:
        if not long_path.samefile(short_path):
            pytest.skip("8.3 alias allocation changed after recreation")
    except OSError:
        pytest.skip("8.3 alias allocation changed after recreation")

    effective_path = R._canonicalize_absolute_path(frozen_path)
    effective_lock_path = effective_path.with_name(
        f".{effective_path.name}.lock"
    )
    assert effective_path != frozen_path
    assert effective_lock_path != frozen_lock_path
    with pytest.raises(
        R.WindowRegistryPathError,
        match="registry_path_rebound_since_construction",
    ):
        registry.snapshot()


def test_registry_rejects_hardlinked_registry_file(tmp_path) -> None:
    path = tmp_path / "registry.jsonl"
    registry = R.ChatServedWindowRegistry(path)
    _reserve(registry, _binding())
    alias = tmp_path / "registry-alias.jsonl"
    try:
        os.link(path, alias)
    except OSError:
        pytest.skip("hardlink creation is unavailable")

    with pytest.raises(
        R.WindowRegistryPathError,
        match="registry_hardlink_not_allowed",
    ):
        registry.snapshot()


@pytest.mark.skipif(os.name != "posix", reason="POSIX FIFO behavior")
def test_registry_fifo_path_fails_without_blocking(tmp_path) -> None:
    path = tmp_path / "registry.fifo"
    os.mkfifo(path)
    script = (
        "import sys\n"
        "from waggledance.core.magma import "
        "chat_served_window_registry as R\n"
        "try:\n"
        "    R.ChatServedWindowRegistry(sys.argv[1]).snapshot()\n"
        "except R.WindowRegistryPathError as exc:\n"
        "    print(str(exc))\n"
        "    raise SystemExit(0 if str(exc) == "
        "'registry_not_regular' else 3)\n"
        "raise SystemExit(4)\n"
    )
    try:
        completed = subprocess.run(
            [sys.executable, "-c", script, os.fspath(path)],
            check=False,
            capture_output=True,
            text=True,
            timeout=5.0,
        )
    except subprocess.TimeoutExpired:
        pytest.fail("registry FIFO open blocked instead of failing closed")

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "registry_not_regular"


def test_verification_callback_runs_while_registry_lock_is_held(
    tmp_path,
) -> None:
    path = tmp_path / "registry.jsonl"
    registry = R.ChatServedWindowRegistry(path)
    binding = _binding()

    def callback(_prior):
        contender = R.ChatServedWindowRegistry(
            path,
            lock_timeout_seconds=0,
        )
        with pytest.raises(
            R.WindowRegistryLockError,
            match="registry_lock_busy",
        ):
            contender.snapshot()
        return _approval(binding)

    registry.reserve_after_verification(binding, callback)
    assert registry.snapshot().consumed_window_ids == (binding.window_id,)


def test_lock_close_failure_does_not_wedge_process_mutex(
    tmp_path,
    monkeypatch,
) -> None:
    registry = R.ChatServedWindowRegistry(
        tmp_path / "registry.jsonl",
        lock_timeout_seconds=0,
    )
    original_close = R.os.close

    def close_then_raise(fd: int) -> None:
        original_close(fd)
        raise OSError("injected close failure")

    with monkeypatch.context() as patch:
        patch.setattr(R.os, "close", close_then_raise)
        with pytest.raises(OSError, match="injected close failure"):
            registry.snapshot()

    assert registry.snapshot().records == ()


@pytest.mark.skipif(
    os.name != "posix" or "fork" not in mp.get_all_start_methods(),
    reason="POSIX fork behavior",
)
@pytest.mark.filterwarnings(
    "ignore:This process.*fork.*:DeprecationWarning"
)
def test_fork_child_drops_inherited_thread_and_native_locks(
    tmp_path,
) -> None:
    path = tmp_path / "fork-registry.jsonl"
    registry = R.ChatServedWindowRegistry(path)
    holder_entered = threading.Event()
    release_holder = threading.Event()
    holder_errors: list[str] = []

    def hold_registry_lock() -> None:
        try:
            with registry._locked():
                holder_entered.set()
                if not release_holder.wait(_SPAWN_WAIT_SECONDS):
                    raise RuntimeError("release_holder_timeout")
        except Exception as exc:
            holder_errors.append(f"{type(exc).__name__}:{exc}")

    holder = threading.Thread(target=hold_registry_lock)
    holder.start()
    assert holder_entered.wait(_SPAWN_WAIT_SECONDS)

    context = mp.get_context("fork")
    allow_snapshot = context.Event()
    result_queue = context.Queue()
    with R._PATH_LOCKS_GUARD:
        inherited_lock_fds = tuple(
            (
                fd,
                int(os.fstat(fd).st_dev),
                int(os.fstat(fd).st_ino),
            )
            for fd in sorted(R._ACTIVE_LOCK_FDS)
        )
    assert inherited_lock_fds
    child = context.Process(
        target=_fork_snapshot_after_release_worker,
        args=(
            os.fspath(path),
            inherited_lock_fds,
            allow_snapshot,
            result_queue,
        ),
    )
    child_started = False
    try:
        child.start()
        child_started = True
        fork_state = _spawn_queue_get(result_queue)
        release_holder.set()
        holder.join(timeout=_SPAWN_WAIT_SECONDS)
        assert not holder.is_alive()
        allow_snapshot.set()
        snapshot_result = _spawn_queue_get(result_queue)
    finally:
        release_holder.set()
        allow_snapshot.set()
        holder.join(timeout=5.0)
        if child_started:
            _reap_spawned_processes([child])

    assert holder_errors == []
    assert fork_state[1:] == ("fork_state", (), ())
    assert snapshot_result[1:] == ("snapshot", 0)
    assert child.exitcode == 0


@pytest.mark.skipif(
    os.name != "posix" or not hasattr(os, "fork"),
    reason="POSIX fork behavior",
)
def test_fork_inside_verifier_child_fails_before_transition(
    tmp_path,
) -> None:
    context = mp.get_context("spawn")
    path = tmp_path / "fork-inside-verifier-registry.jsonl"
    result_queue = context.Queue()
    worker = context.Process(
        target=_fork_inside_verifier_worker,
        args=(os.fspath(path), result_queue),
    )
    started = False
    try:
        worker.start()
        started = True
        result = _spawn_queue_get(result_queue)
    finally:
        if started:
            _reap_spawned_processes([worker])

    assert result[1] == "parent_reserved"
    assert result[2] == R.RESERVED_PRE_MARKER
    assert result[3] > 0
    assert result[4] == 0
    assert result[5] == (
        "WindowRegistryLockError:"
        "process_forked_during_verification"
    )
    assert result[6] == 1
    assert worker.exitcode == 0


def test_spawned_processes_racing_same_reservation_burn_exactly_once(
    tmp_path,
) -> None:
    context = mp.get_context("spawn")
    path = tmp_path / "spawn-race-registry.jsonl"
    binding = _binding(31)
    start_gate = context.Event()
    ready_queue = context.Queue()
    result_queue = context.Queue()
    processes = [
        context.Process(
            target=_spawn_reservation_race_worker,
            args=(
                os.fspath(path),
                binding,
                start_gate,
                ready_queue,
                result_queue,
            ),
        )
        for _index in range(2)
    ]

    try:
        for process in processes:
            process.start()
        ready_pids = {
            _spawn_queue_get(ready_queue)[0]
            for _index in range(2)
        }
        start_gate.set()
        results = [
            _spawn_queue_get(result_queue)
            for _index in range(2)
        ]
    finally:
        start_gate.set()
        _reap_spawned_processes(processes)

    assert len(ready_pids) == 2
    assert {result[0] for result in results} == ready_pids
    assert sorted(result[1] for result in results) == [
        "replay",
        "reserved",
    ]
    assert {
        (result[1], result[2])
        for result in results
    } == {
        ("reserved", R.RESERVED_PRE_MARKER),
        ("replay", "window_id_reused"),
    }
    assert all(process.exitcode == 0 for process in processes)

    snapshot = R.ChatServedWindowRegistry(path).snapshot()
    assert len(snapshot.records) == 1
    assert snapshot.consumed_window_ids == (binding.window_id,)
    assert snapshot.verified_window_ids == ()
    assert snapshot.records[0]["sequence"] == 0
    _assert_registry_is_canonical_and_valid(path, snapshot)


def test_spawned_reserve_cannot_bypass_finalizer_and_retries_after_release(
    tmp_path,
) -> None:
    path = tmp_path / "spawn-reserve-finalize-registry.jsonl"
    first = _binding(32)
    second = _binding(33)
    _reserve(R.ChatServedWindowRegistry(path), first)

    context = mp.get_context("spawn")
    finalizer_entered = context.Event()
    release_finalizer = context.Event()
    reserve_first_attempt_done = context.Event()
    allow_reserve_retry = context.Event()
    finalizer_results = context.Queue()
    reserve_results = context.Queue()
    finalizer = context.Process(
        target=_spawn_holding_finalizer_worker,
        args=(
            os.fspath(path),
            first,
            finalizer_entered,
            release_finalizer,
            finalizer_results,
        ),
    )
    reserver = context.Process(
        target=_spawn_reserve_busy_then_retry_worker,
        args=(
            os.fspath(path),
            second,
            reserve_first_attempt_done,
            allow_reserve_retry,
            reserve_results,
        ),
    )
    processes: list[Any] = []

    try:
        finalizer.start()
        processes.append(finalizer)
        assert finalizer_entered.wait(_SPAWN_WAIT_SECONDS)

        reserver.start()
        processes.append(reserver)
        assert reserve_first_attempt_done.wait(_SPAWN_WAIT_SECONDS)

        release_finalizer.set()
        finalizer_result = _spawn_queue_get(finalizer_results)
        allow_reserve_retry.set()
        reserve_result = _spawn_queue_get(reserve_results)
    finally:
        release_finalizer.set()
        allow_reserve_retry.set()
        _reap_spawned_processes(processes)

    assert finalizer_result[1:] == ("finalized", R.FINAL_VERIFIED)
    assert reserve_result[1:] == (
        "lock_busy_then_reserved",
        R.RESERVED_PRE_MARKER,
    )
    assert finalizer_result[0] != reserve_result[0]
    assert all(process.exitcode == 0 for process in processes)

    snapshot = R.ChatServedWindowRegistry(path).snapshot()
    assert [record["event_type"] for record in snapshot.records] == [
        R.RESERVED_PRE_MARKER,
        R.FINAL_VERIFIED,
        R.RESERVED_PRE_MARKER,
    ]
    assert [record["sequence"] for record in snapshot.records] == [0, 1, 2]
    assert snapshot.consumed_window_ids == (
        first.window_id,
        second.window_id,
    )
    assert snapshot.verified_window_ids == (first.window_id,)
    _assert_registry_is_canonical_and_valid(path, snapshot)


def test_spawned_exit_inside_verifier_releases_lock_without_consuming_id(
    tmp_path,
) -> None:
    context = mp.get_context("spawn")
    path = tmp_path / "spawn-verifier-exit-registry.jsonl"
    binding = _binding(34)
    callback_entered = context.Event()
    process = context.Process(
        target=_spawn_exit_in_verifier_worker,
        args=(os.fspath(path), binding, callback_entered),
    )

    try:
        process.start()
        entered = callback_entered.wait(_SPAWN_WAIT_SECONDS)
    finally:
        _reap_spawned_processes([process])

    assert entered is True
    assert process.exitcode == _VERIFIER_CRASH_EXIT_CODE

    registry = R.ChatServedWindowRegistry(
        path,
        lock_timeout_seconds=1.0,
    )
    assert registry.snapshot().records == ()
    _reserve(registry, binding)
    snapshot = registry.snapshot()
    assert snapshot.consumed_window_ids == (binding.window_id,)
    assert len(snapshot.records) == 1
    _assert_registry_is_canonical_and_valid(path, snapshot)


def test_spawned_exit_after_durable_append_leaves_valid_burned_reservation(
    tmp_path,
) -> None:
    context = mp.get_context("spawn")
    path = tmp_path / "spawn-post-append-exit-registry.jsonl"
    binding = _binding(35)
    append_completed = context.Event()
    process = context.Process(
        target=_spawn_exit_after_append_worker,
        args=(os.fspath(path), binding, append_completed),
    )

    try:
        process.start()
        appended = append_completed.wait(_SPAWN_WAIT_SECONDS)
    finally:
        _reap_spawned_processes([process])

    assert appended is True
    assert process.exitcode == _POST_APPEND_CRASH_EXIT_CODE

    registry = R.ChatServedWindowRegistry(
        path,
        lock_timeout_seconds=1.0,
    )
    snapshot = registry.snapshot()
    assert len(snapshot.records) == 1
    assert snapshot.consumed_window_ids == (binding.window_id,)
    assert snapshot.verified_window_ids == ()
    _assert_registry_is_canonical_and_valid(path, snapshot)

    callback_calls: list[tuple[str, ...]] = []
    with pytest.raises(
        R.WindowRegistryReplayError,
        match="window_id_reused",
    ):
        registry.reserve_after_verification(
            binding,
            lambda prior: (
                callback_calls.append(prior) or _approval(binding)
            ),
        )
    assert callback_calls == []


def test_append_fsyncs_lock_and_registry_before_return(
    tmp_path,
    monkeypatch,
) -> None:
    fsync_fds: list[int] = []
    monkeypatch.setattr(R.os, "fsync", lambda fd: fsync_fds.append(fd))
    registry = R.ChatServedWindowRegistry(tmp_path / "registry.jsonl")

    _reserve(registry, _binding())

    assert len(fsync_fds) >= 2


def test_every_registry_append_fsyncs_parent_directory(
    tmp_path,
    monkeypatch,
) -> None:
    calls: list[Path] = []
    monkeypatch.setattr(R, "_prepare_parent", lambda _path: None)
    monkeypatch.setattr(
        R,
        "_fsync_parent_directory",
        lambda path: calls.append(path.parent),
    )
    path = tmp_path / "registry.jsonl"
    registry = R.ChatServedWindowRegistry(path)

    _reserve(registry, _binding(1))
    _reserve(registry, _binding(2))

    assert calls == [path.parent, path.parent]


def test_crash_left_empty_registry_still_fsyncs_parent_on_append(
    tmp_path,
    monkeypatch,
) -> None:
    path = tmp_path / "registry.jsonl"
    path.write_bytes(b"")
    calls: list[Path] = []
    monkeypatch.setattr(R, "_prepare_parent", lambda _path: None)
    monkeypatch.setattr(
        R,
        "_fsync_parent_directory",
        lambda target: calls.append(target.parent),
    )

    _reserve(R.ChatServedWindowRegistry(path), _binding())

    assert calls == [path.parent]


def test_prepare_parent_requests_full_ancestor_sync_in_order(
    tmp_path,
    monkeypatch,
) -> None:
    receiver_root = tmp_path / "receiver-root"
    registry_parent = receiver_root / "registry-state"
    path = registry_parent / "registry.jsonl"
    calls: list[Path] = []
    monkeypatch.setattr(
        R,
        "_fsync_parent_directory",
        lambda target: calls.append(target.parent),
    )

    R._prepare_parent(path)

    assert calls[-3:] == [
        tmp_path.parent,
        tmp_path,
        receiver_root,
    ]


def test_prepare_parent_retries_full_ancestor_sync_after_failure(
    tmp_path,
    monkeypatch,
) -> None:
    receiver_root = tmp_path / "receiver-root"
    registry_parent = receiver_root / "registry-state"
    path = registry_parent / "registry.jsonl"
    first_calls: list[Path] = []

    def fail_after_creation(target: Path) -> None:
        first_calls.append(target)
        if target == registry_parent:
            raise R.WindowRegistryError("injected ancestor fsync failure")

    with monkeypatch.context() as patch:
        patch.setattr(R, "_fsync_parent_directory", fail_after_creation)
        with pytest.raises(
            R.WindowRegistryError,
            match="injected ancestor fsync failure",
        ):
            R._prepare_parent(path)

    assert receiver_root.is_dir()
    assert registry_parent.is_dir()
    assert registry_parent in first_calls

    retry_calls: list[Path] = []
    with monkeypatch.context() as patch:
        patch.setattr(
            R,
            "_fsync_parent_directory",
            lambda target: retry_calls.append(target),
        )
        R._prepare_parent(path)

    assert retry_calls[-3:] == [
        tmp_path,
        receiver_root,
        registry_parent,
    ]


def test_parent_fsync_failure_returns_unknown_but_keeps_valid_burn(
    tmp_path,
    monkeypatch,
) -> None:
    path = tmp_path / "registry.jsonl"
    registry = R.ChatServedWindowRegistry(path)
    binding = _binding()
    assert registry.snapshot().records == ()

    def fail_parent_fsync(_path: Path) -> None:
        raise R.WindowRegistryError("injected parent fsync failure")

    with monkeypatch.context() as patch:
        patch.setattr(R, "_prepare_parent", lambda _path: None)
        patch.setattr(R, "_fsync_parent_directory", fail_parent_fsync)
        with pytest.raises(
            R.WindowRegistryError,
            match="injected parent fsync failure",
        ):
            _reserve(registry, binding)

    snapshot = registry.snapshot()
    assert snapshot.consumed_window_ids == (binding.window_id,)
    assert len(snapshot.records) == 1
    with pytest.raises(
        R.WindowRegistryReplayError,
        match="window_id_reused",
    ):
        _reserve(registry, binding)


def test_receiver_clock_rollback_advances_sequence_time_without_blocking(
    tmp_path,
    monkeypatch,
) -> None:
    times = iter(
        [
            "2026-07-27T05:00:00.000000Z",
            "2026-07-27T04:00:00.000000Z",
        ]
    )
    monkeypatch.setattr(R, "_receiver_timestamp", lambda: next(times))
    registry = R.ChatServedWindowRegistry(tmp_path / "registry.jsonl")

    _reserve(registry, _binding(1))
    _reserve(registry, _binding(2))
    records = registry.snapshot().records

    assert records[0]["recorded_at_utc"] == "2026-07-27T05:00:00.000000Z"
    assert records[1]["recorded_at_utc"] == "2026-07-27T05:00:00.000001Z"


def test_snapshot_rows_are_immutable_and_hash_has_explicit_domain(tmp_path) -> None:
    registry = R.ChatServedWindowRegistry(tmp_path / "registry.jsonl")
    _reserve(registry, _binding())
    row = registry.snapshot().records[0]

    with pytest.raises(TypeError):
        row["source_head"] = f"{888:040x}"
    undomained = sha256_digest(
        {
            key: row[key]
            for key in sorted(row)
            if key != "record_hash"
        }
    )
    assert row["record_hash"] != undomained


def test_no_naked_persistent_transition_api_is_exposed(tmp_path) -> None:
    registry = R.ChatServedWindowRegistry(tmp_path / "registry.jsonl")

    assert not hasattr(registry, "reserve_window")
    assert not hasattr(registry, "finalize_window")
    assert not hasattr(R, "reserve_window")
    assert not hasattr(R, "finalize_window")

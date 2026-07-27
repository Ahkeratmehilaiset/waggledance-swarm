# SPDX-License-Identifier: BUSL-1.1
"""Tests for the receiver-owned chat-served replay registry."""
from __future__ import annotations

import json
import os
import stat
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

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


def test_registry_rejects_symlinked_parent(tmp_path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    linked = tmp_path / "linked"
    try:
        linked.symlink_to(real, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlink creation is unavailable")
    registry = R.ChatServedWindowRegistry(linked / "registry.jsonl")

    with pytest.raises(
        R.WindowRegistryPathError,
        match="path_reparse_not_allowed",
    ):
        registry.reserve_after_verification(
            _binding(),
            lambda _prior: _approval(_binding()),
        )


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


def test_append_fsyncs_lock_and_registry_before_return(
    tmp_path,
    monkeypatch,
) -> None:
    fsync_fds: list[int] = []
    monkeypatch.setattr(R.os, "fsync", lambda fd: fsync_fds.append(fd))
    registry = R.ChatServedWindowRegistry(tmp_path / "registry.jsonl")

    _reserve(registry, _binding())

    assert len(fsync_fds) >= 2


def test_first_registry_creation_fsyncs_parent_directory_only_once(
    tmp_path,
    monkeypatch,
) -> None:
    calls: list[Path] = []
    monkeypatch.setattr(
        R,
        "_fsync_parent_directory",
        lambda path: calls.append(path.parent),
    )
    path = tmp_path / "registry.jsonl"
    registry = R.ChatServedWindowRegistry(path)

    _reserve(registry, _binding(1))
    _reserve(registry, _binding(2))

    assert calls == [path.parent]


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

# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

import asyncio
import json
import os
import stat
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from waggledance.adapters.http.api import lifespan
from waggledance.core.magma import chat_served_claim_window_evidence as evidence
from waggledance.core.magma.chat_served_accounting import (
    REQUIRED_CHAT_SERVED_POINTS,
)
from waggledance.core.magma.chat_served_claim_window_evidence import (
    ProductionWindowVerification,
)
from waggledance.core.magma.chat_served_ledger import GENESIS_PREV_HASH
from waggledance.core.magma.chat_served_runtime_window import (
    ChatServedRuntimeWindow,
    ChatServedRuntimeWindowResult,
    _append_jsonl,
    _scan_receipt_manifests,
    _strict_jsonl,
    _write_clean_marker_last,
)
from waggledance.core.magma.chat_served_window_registry import (
    ChatServedWindowRegistry,
    RegistryVerificationApproval,
    WindowRegistryBinding,
    derive_window_marker_path_digest,
)

_SOURCE_HEAD = "a" * 40


class _Emitter:
    enabled = True

    def __init__(self, root: Path, events: list[str], *, drain_clean: bool = True):
        self.pending_failure_ledger_path = str(
            root / "evidence" / "pending-failures.jsonl"
        )
        self.pending_append_failures = 0
        self.head = GENESIS_PREV_HASH
        self.events = events
        self.drain_clean = drain_clean
        self.intake_closed = False
        self.post_close_attempts = 0

    def close_intake(self) -> bool:
        self.events.append("close_intake")
        self.intake_closed = True
        return True

    async def drain(self, _timeout_seconds: float) -> dict[str, object]:
        self.events.append("drain")
        if not self.drain_clean:
            return {
                "status": "not_clean",
                "reason": "timeout",
                "intake_closed": True,
                "scheduled": 1,
                "completed": 0,
                "failed": 0,
                "cancelled": 1,
                "pending": 0,
                "post_close_attempts": 0,
                "schedule_failures": 0,
                "timed_out": True,
                "caller_cancelled": False,
            }
        return {
            "status": "drained",
            "reason": None,
            "intake_closed": True,
            "scheduled": 0,
            "completed": 0,
            "failed": 0,
            "cancelled": 0,
            "pending": 0,
            "post_close_attempts": 0,
            "schedule_failures": 0,
            "timed_out": False,
            "caller_cancelled": False,
        }

    def flush_sink(self) -> bool:
        self.events.append("flush")
        return True


def _window(
    tmp_path: Path,
    emitter: _Emitter,
    *,
    enabled_probe=lambda: True,
    window_id: str = "window:lifecycle-test",
    registry_path: Path | None = None,
) -> tuple[ChatServedRuntimeWindow, dict[str, Path]]:
    paths = {
        "ledger": tmp_path / "receipts" / "ledger.jsonl",
        "receipts": tmp_path / "receipts",
        "anchors": tmp_path / "evidence" / "anchors.jsonl",
        "enabled": tmp_path / "evidence" / "enabled.jsonl",
        "points": tmp_path / "evidence" / "points.jsonl",
        "index": tmp_path / "evidence" / "receipt-index.jsonl",
        "start": tmp_path / "evidence" / "start.json",
        "final": tmp_path / "evidence" / "final.json",
        "marker": tmp_path / "evidence" / "marker.json",
        "registry": (
            registry_path
            if registry_path is not None
            else tmp_path / "receiver" / "window-registry.jsonl"
        ),
    }
    window = ChatServedRuntimeWindow(
        emitter=emitter,
        window_id=window_id,
        source_head=_SOURCE_HEAD,
        ledger_path=str(paths["ledger"]),
        receipt_root=str(paths["receipts"]),
        anchor_store_path=str(paths["anchors"]),
        enabled_samples_path=str(paths["enabled"]),
        served_point_observations_path=str(paths["points"]),
        receipt_index_path=str(paths["index"]),
        start_boundary_path=str(paths["start"]),
        final_boundary_path=str(paths["final"]),
        clean_shutdown_marker_path=str(paths["marker"]),
        verified_window_registry_path=str(paths["registry"]),
        enabled_probe=enabled_probe,
        sample_interval_seconds=3600.0,
        max_sample_gap_seconds=3600,
        drain_timeout_seconds=1.0,
        now_fn=lambda: datetime(2026, 7, 27, tzinfo=timezone.utc),
    )
    return window, paths


def _pre_marker_verdict() -> ProductionWindowVerification:
    return ProductionWindowVerification(
        ok=True,
        phase="pre_marker_verified",
        reason=None,
        marker_verified=False,
        ledger_entries=2,
        enabled_samples=2,
        pending_failures=0,
        receipt_index_entries=1,
        served_point_observations=len(REQUIRED_CHAT_SERVED_POINTS),
        receipt_terminals=1,
        served_total=1,
        served_with_receipt_total=1,
        served_with_receipt_ratio=1.0,
        solver_first_served_total=1,
        solver_first_served_ratio=1.0,
    )


def test_runtime_window_writes_marker_last_after_pre_marker_verification(
    tmp_path: Path,
    monkeypatch,
) -> None:
    events: list[str] = []
    emitter = _Emitter(tmp_path, events)
    window, paths = _window(tmp_path, emitter)

    def verify(**kwargs):
        events.append("verify")
        assert kwargs["clean_shutdown_marker"] is None
        assert paths["marker"].exists() is False
        assert kwargs["previously_verified_window_ids"] == ()
        assert len(kwargs["enabled_samples"]) == 2
        assert kwargs["ledger_entries"] == ()
        return _pre_marker_verdict()

    monkeypatch.setattr(evidence, "verify_production_window", verify, raising=False)
    real_write_marker = _write_clean_marker_last

    def write_marker(path, marker):
        events.append("marker")
        assert paths["marker"].exists() is False
        snapshot = ChatServedWindowRegistry(paths["registry"]).snapshot()
        assert snapshot.is_consumed("window:lifecycle-test") is True
        assert snapshot.is_verified("window:lifecycle-test") is False
        real_write_marker(path, marker)

    monkeypatch.setattr(
        "waggledance.core.magma.chat_served_runtime_window."
        "_write_clean_marker_last",
        write_marker,
    )

    async def run() -> tuple[object, object]:
        started = await window.start()
        assert paths["start"].exists()
        assert paths["marker"].exists() is False
        finished = await window.shutdown()
        return started, finished

    started, finished = asyncio.run(run())

    assert started.status == "running"
    assert finished.status == "complete"
    assert finished.lifecycle_verified is True
    assert finished.clean_marker_written is True
    assert events == ["close_intake", "drain", "flush", "verify", "marker"]
    marker = json.loads(paths["marker"].read_text(encoding="utf-8"))
    assert marker["window_id"] == "window:lifecycle-test"
    assert marker["schema_version"].endswith(".v1")
    assert "claim_safe" not in marker
    reservation = ChatServedWindowRegistry(paths["registry"]).snapshot()
    assert reservation.consumed_window_ids == ("window:lifecycle-test",)
    assert reservation.verified_window_ids == ()
    assert finished.registry_state == "reserved_pre_marker"
    assert finished.public_summary()["registry_state"] == "reserved_pre_marker"
    assert str(tmp_path) not in json.dumps(finished.public_summary())


def test_runtime_registry_passes_all_prior_consumed_ids_to_verifier(
    tmp_path: Path,
    monkeypatch,
) -> None:
    registry_path = tmp_path / "receiver" / "window-registry.jsonl"
    prior_binding = WindowRegistryBinding(
        window_id="window:registry-first",
        source_head="b" * 40,
        binding_digest="sha256:" + ("1" * 64),
        start_boundary_digest="sha256:" + ("2" * 64),
        final_boundary_digest="sha256:" + ("3" * 64),
        marker_digest="sha256:" + ("4" * 64),
        marker_path_digest=derive_window_marker_path_digest(
            tmp_path / "prior" / "clean-shutdown.json"
        ),
        final_ledger_head="sha256:" + ("5" * 64),
    )
    ChatServedWindowRegistry(registry_path).reserve_after_verification(
        prior_binding,
        lambda prior_ids: RegistryVerificationApproval(
            prior_binding,
            _pre_marker_verdict(),
        ),
    )
    runtime_root = tmp_path / "runtime"
    window, paths = _window(
        runtime_root,
        _Emitter(runtime_root, []),
        window_id="window:registry-second",
        registry_path=registry_path,
    )
    seen_prior_ids: list[tuple[str, ...]] = []

    def verify(**kwargs):
        seen_prior_ids.append(kwargs["previously_verified_window_ids"])
        return _pre_marker_verdict()

    monkeypatch.setattr(evidence, "verify_production_window", verify, raising=False)

    async def run():
        assert (await window.start()).status == "running"
        return await window.shutdown()

    result = asyncio.run(run())

    assert result.status == "complete"
    assert seen_prior_ids == [("window:registry-first",)]
    assert paths["marker"].exists() is True
    snapshot = ChatServedWindowRegistry(registry_path).snapshot()
    assert snapshot.consumed_window_ids == (
        "window:registry-first",
        "window:registry-second",
    )
    assert snapshot.verified_window_ids == ()


def test_runtime_registry_replay_fails_before_any_new_evidence(
    tmp_path: Path,
    monkeypatch,
) -> None:
    registry_path = tmp_path / "receiver" / "window-registry.jsonl"
    first_root = tmp_path / "first"
    replay_root = tmp_path / "replay"
    first, _first_paths = _window(
        first_root,
        _Emitter(first_root, []),
        registry_path=registry_path,
    )
    replay, replay_paths = _window(
        replay_root,
        _Emitter(replay_root, []),
        registry_path=registry_path,
    )
    verifier_calls = 0

    def verify(**_kwargs):
        nonlocal verifier_calls
        verifier_calls += 1
        return _pre_marker_verdict()

    monkeypatch.setattr(evidence, "verify_production_window", verify, raising=False)

    async def run():
        assert (await first.start()).status == "running"
        assert (await first.shutdown()).status == "complete"
        return await replay.start()

    result = asyncio.run(run())

    assert result.status == "ineligible"
    assert result.reason == "window_registry_window_id_reused"
    assert result.registry_state == "not_reserved"
    assert verifier_calls == 1
    assert replay_paths["start"].exists() is False
    assert replay_paths["enabled"].exists() is False
    assert replay_paths["marker"].exists() is False
    snapshot = ChatServedWindowRegistry(registry_path).snapshot()
    assert snapshot.consumed_window_ids == ("window:lifecycle-test",)


def test_runtime_mutation_after_reservation_burns_id_without_marker(
    tmp_path: Path,
    monkeypatch,
) -> None:
    emitter = _Emitter(tmp_path, [])
    window, paths = _window(tmp_path, emitter)

    def verify(**_kwargs):
        return _pre_marker_verdict()

    monkeypatch.setattr(evidence, "verify_production_window", verify, raising=False)

    async def run():
        assert (await window.start()).status == "running"
        registry = window._window_registry
        assert registry is not None
        reserve = registry.reserve_after_verification

        def reserve_then_mutate(binding, callback):
            row = reserve(binding, callback)
            paths["final"].write_text('{"tampered":true}\n', encoding="utf-8")
            return row

        monkeypatch.setattr(
            registry,
            "reserve_after_verification",
            reserve_then_mutate,
        )
        return await window.shutdown()

    result = asyncio.run(run())

    assert result.status == "ineligible"
    assert result.registry_state == "reserved_pre_marker"
    assert (
        result.reason
        == "runtime_window_shutdown_failed:"
        "boundary_mutated_after_verification"
    )
    assert paths["marker"].exists() is False
    snapshot = ChatServedWindowRegistry(paths["registry"]).snapshot()
    assert snapshot.is_consumed("window:lifecycle-test") is True
    assert snapshot.is_verified("window:lifecycle-test") is False


def test_runtime_post_reservation_failure_reports_unknown_and_writes_no_marker(
    tmp_path: Path,
    monkeypatch,
) -> None:
    emitter = _Emitter(tmp_path, [])
    window, paths = _window(tmp_path, emitter)

    def verify(**_kwargs):
        return _pre_marker_verdict()

    monkeypatch.setattr(evidence, "verify_production_window", verify, raising=False)

    async def run():
        assert (await window.start()).status == "running"
        registry = window._window_registry
        assert registry is not None
        reserve = registry.reserve_after_verification

        def reserve_then_lose_confirmation(binding, callback):
            reserve(binding, callback)
            raise OSError("simulated post-fsync readback failure")

        monkeypatch.setattr(
            registry,
            "reserve_after_verification",
            reserve_then_lose_confirmation,
        )
        return await window.shutdown()

    result = asyncio.run(run())

    assert result.status == "ineligible"
    assert result.registry_state == "reservation_pending_or_unknown"
    assert (
        result.reason
        == "runtime_window_shutdown_failed:OSError"
    )
    assert paths["marker"].exists() is False
    snapshot = ChatServedWindowRegistry(paths["registry"]).snapshot()
    assert snapshot.is_consumed("window:lifecycle-test") is True
    assert snapshot.is_verified("window:lifecycle-test") is False


def test_runtime_missing_reservation_readback_writes_no_marker(
    tmp_path: Path,
    monkeypatch,
) -> None:
    emitter = _Emitter(tmp_path, [])
    window, paths = _window(tmp_path, emitter)

    def verify(**_kwargs):
        return _pre_marker_verdict()

    monkeypatch.setattr(evidence, "verify_production_window", verify, raising=False)

    async def run():
        assert (await window.start()).status == "running"
        registry = window._window_registry
        assert registry is not None
        reserve = registry.reserve_after_verification

        def reserve_then_hide_readback(binding, callback):
            row = reserve(binding, callback)
            monkeypatch.setattr(
                registry,
                "snapshot",
                lambda: SimpleNamespace(
                    reservation_for=lambda _window_id: None,
                    finalization_for=lambda _window_id: None,
                ),
            )
            return row

        monkeypatch.setattr(
            registry,
            "reserve_after_verification",
            reserve_then_hide_readback,
        )
        return await window.shutdown()

    result = asyncio.run(run())

    assert result.status == "ineligible"
    assert result.registry_state == "reserved_pre_marker"
    assert (
        result.reason
        == "runtime_window_shutdown_failed:"
        "window_registry_reservation_changed"
    )
    assert paths["marker"].exists() is False
    snapshot = ChatServedWindowRegistry(paths["registry"]).snapshot()
    assert snapshot.is_consumed("window:lifecycle-test") is True


def test_runtime_window_rejects_on_disk_mutation_during_pre_verification(
    tmp_path: Path,
    monkeypatch,
) -> None:
    emitter = _Emitter(tmp_path, [])
    window, paths = _window(tmp_path, emitter)

    def verify(**_kwargs):
        _append_jsonl(paths["points"], ({"tampered": True},))
        return _pre_marker_verdict()

    monkeypatch.setattr(evidence, "verify_production_window", verify, raising=False)

    async def run():
        assert (await window.start()).status == "running"
        return await window.shutdown()

    result = asyncio.run(run())

    assert result.status == "ineligible"
    assert (
        result.reason
        == "runtime_window_shutdown_failed:"
        "side_stream_mutated_after_verification"
    )
    assert paths["final"].exists() is True
    assert paths["marker"].exists() is False


def test_runtime_window_rejects_boundary_mutation_during_pre_verification(
    tmp_path: Path,
    monkeypatch,
) -> None:
    emitter = _Emitter(tmp_path, [])
    window, paths = _window(tmp_path, emitter)

    def verify(**_kwargs):
        paths["final"].write_text('{"tampered":true}\n', encoding="utf-8")
        return _pre_marker_verdict()

    monkeypatch.setattr(evidence, "verify_production_window", verify, raising=False)

    async def run():
        assert (await window.start()).status == "running"
        return await window.shutdown()

    result = asyncio.run(run())

    assert result.status == "ineligible"
    assert (
        result.reason
        == "runtime_window_shutdown_failed:"
        "boundary_mutated_after_verification"
    )
    assert paths["marker"].exists() is False


def test_runtime_window_rejects_anchor_mutation_during_pre_verification(
    tmp_path: Path,
    monkeypatch,
) -> None:
    emitter = _Emitter(tmp_path, [])
    window, paths = _window(tmp_path, emitter)

    def verify(**_kwargs):
        paths["anchors"].write_text('{"tampered":true}\n', encoding="utf-8")
        return _pre_marker_verdict()

    monkeypatch.setattr(evidence, "verify_production_window", verify, raising=False)

    async def run():
        assert (await window.start()).status == "running"
        return await window.shutdown()

    result = asyncio.run(run())

    assert result.status == "ineligible"
    assert (
        result.reason
        == "runtime_window_shutdown_failed:"
        "head_anchor_mutated_after_verification"
    )
    assert paths["marker"].exists() is False


def test_runtime_window_drain_failure_writes_no_final_or_marker(
    tmp_path: Path,
) -> None:
    emitter = _Emitter(tmp_path, [], drain_clean=False)
    window, paths = _window(tmp_path, emitter)

    async def run():
        assert (await window.start()).status == "running"
        return await window.shutdown()

    result = asyncio.run(run())

    assert result.status == "ineligible"
    assert paths["final"].exists() is False
    assert paths["marker"].exists() is False


def test_runtime_window_counter_catches_undurable_pending_failure(
    tmp_path: Path,
) -> None:
    emitter = _Emitter(tmp_path, [])
    window, paths = _window(tmp_path, emitter)
    original_drain = emitter.drain

    async def drain_and_lose_failure(timeout_seconds: float):
        result = await original_drain(timeout_seconds)
        emitter.pending_append_failures += 1
        return result

    emitter.drain = drain_and_lose_failure  # type: ignore[method-assign]

    async def run():
        assert (await window.start()).status == "running"
        return await window.shutdown()

    result = asyncio.run(run())

    assert result.status == "ineligible"
    assert not Path(emitter.pending_failure_ledger_path).exists()
    assert paths["marker"].exists() is False


def test_runtime_window_rejects_preexisting_window_artifact(tmp_path: Path) -> None:
    emitter = _Emitter(tmp_path, [])
    window, paths = _window(tmp_path, emitter)
    paths["marker"].parent.mkdir(parents=True)
    paths["marker"].write_text('{"stale":true}\n', encoding="utf-8")

    result = asyncio.run(window.start())

    assert result.status == "ineligible"
    assert result.reason == "preexisting_window_artifact"
    assert paths["start"].exists() is False
    assert paths["marker"].read_text(encoding="utf-8") == '{"stale":true}\n'


def test_start_failure_closes_measurement_intake_before_api_serving(
    tmp_path: Path,
) -> None:
    emitter = _Emitter(tmp_path, [])
    window, paths = _window(tmp_path, emitter)
    window._source_head = ""

    result = asyncio.run(window.start())

    assert result.status == "ineligible"
    assert emitter.intake_closed is True
    assert paths["ledger"].exists() is False
    assert paths["enabled"].exists() is False


@pytest.mark.parametrize("configured_path", [None, "relative/registry.jsonl"])
def test_runtime_window_requires_absolute_registry_pin_before_first_append(
    tmp_path: Path,
    configured_path: str | None,
) -> None:
    emitter = _Emitter(tmp_path, [])
    window, paths = _window(tmp_path, emitter)
    window._verified_window_registry_path = configured_path

    result = asyncio.run(window.start())

    assert result.status == "ineligible"
    assert result.reason == "window_registry_path_invalid"
    assert result.registry_state == "not_reserved"
    assert paths["start"].exists() is False
    assert paths["enabled"].exists() is False
    assert paths["marker"].exists() is False


def test_runtime_window_rejects_corrupt_registry_before_first_append(
    tmp_path: Path,
) -> None:
    emitter = _Emitter(tmp_path, [])
    window, paths = _window(tmp_path, emitter)
    paths["registry"].parent.mkdir(parents=True)
    paths["registry"].write_bytes(b'{"torn":true}')

    result = asyncio.run(window.start())

    assert result.status == "ineligible"
    assert (
        result.reason
        == "runtime_window_start_failed:WindowRegistryCorruptionError"
    )
    assert result.registry_state == "not_reserved"
    assert paths["start"].exists() is False
    assert paths["enabled"].exists() is False
    assert paths["marker"].exists() is False


def test_runtime_window_rejects_aliased_write_targets_before_first_append(
    tmp_path: Path,
) -> None:
    emitter = _Emitter(tmp_path, [])
    window, paths = _window(tmp_path, emitter)
    window._start_boundary_path = paths["ledger"]

    result = asyncio.run(window.start())

    assert result.status == "ineligible"
    assert paths["ledger"].exists() is False
    assert paths["enabled"].exists() is False


@pytest.mark.parametrize("alias_kind", ["registry", "lock"])
def test_runtime_window_rejects_registry_artifact_alias_before_first_append(
    tmp_path: Path,
    alias_kind: str,
) -> None:
    emitter = _Emitter(tmp_path, [])
    window, paths = _window(tmp_path, emitter)
    registry = ChatServedWindowRegistry(paths["registry"])
    alias = registry.path if alias_kind == "registry" else registry.lock_path
    window._enabled_samples_path = alias

    result = asyncio.run(window.start())

    assert result.status == "ineligible"
    assert result.reason == "runtime_window_start_failed:ValueError"
    assert paths["start"].exists() is False
    assert paths["marker"].exists() is False


def test_runtime_window_rejects_registry_inside_receipt_namespace(
    tmp_path: Path,
) -> None:
    emitter = _Emitter(tmp_path, [])
    poisoned = tmp_path / "receipts" / "receiver-registry.jsonl"
    window, paths = _window(tmp_path, emitter, registry_path=poisoned)

    result = asyncio.run(window.start())

    assert result.status == "ineligible"
    assert result.reason == "runtime_window_start_failed:ValueError"
    assert paths["start"].exists() is False
    assert paths["enabled"].exists() is False
    assert paths["registry"].exists() is False
    assert paths["marker"].exists() is False


@pytest.mark.parametrize(
    "overlap_direction",
    ["registry_under_evidence", "evidence_under_registry"],
)
def test_runtime_window_rejects_registry_evidence_namespace_overlap(
    tmp_path: Path,
    overlap_direction: str,
) -> None:
    emitter = _Emitter(tmp_path, [])
    if overlap_direction == "registry_under_evidence":
        registry_path = tmp_path / "evidence" / "registry.jsonl"
        window, paths = _window(
            tmp_path,
            emitter,
            registry_path=registry_path,
        )
    else:
        window, paths = _window(tmp_path, emitter)
        window._served_point_observations_path = (
            paths["registry"].parent / "points.jsonl"
        )

    result = asyncio.run(window.start())

    assert result.status == "ineligible"
    assert result.reason == "runtime_window_start_failed:ValueError"
    assert result.registry_state == "not_reserved"
    assert paths["start"].exists() is False
    assert paths["enabled"].exists() is False
    assert paths["marker"].exists() is False


@pytest.mark.parametrize("hardlinked_artifact", ["registry", "lock"])
def test_runtime_window_rejects_hardlinked_registry_artifacts(
    tmp_path: Path,
    hardlinked_artifact: str,
) -> None:
    emitter = _Emitter(tmp_path, [])
    window, paths = _window(tmp_path, emitter)
    registry = ChatServedWindowRegistry(paths["registry"])
    target = (
        registry.path
        if hardlinked_artifact == "registry"
        else registry.lock_path
    )
    outside = tmp_path / f"outside-{hardlinked_artifact}.bin"
    target.parent.mkdir(parents=True)
    outside.write_bytes(b"")
    try:
        os.link(outside, target)
    except OSError as exc:
        pytest.skip(f"hardlink creation unavailable: {type(exc).__name__}")

    result = asyncio.run(window.start())

    assert result.status == "ineligible"
    assert result.reason == "runtime_window_start_failed:WindowRegistryPathError"
    assert paths["start"].exists() is False
    assert paths["enabled"].exists() is False
    assert paths["marker"].exists() is False


def test_runtime_window_rejects_evidence_inside_receipt_bundle_namespace(
    tmp_path: Path,
) -> None:
    emitter = _Emitter(tmp_path, [])
    window, paths = _window(tmp_path, emitter)
    poisoned = (
        paths["receipts"] / ("served-" + "a" * 32) / "enabled.jsonl"
    )
    window._enabled_samples_path = poisoned

    result = asyncio.run(window.start())

    assert result.status == "ineligible"
    assert result.reason == "runtime_window_start_failed:ValueError"
    assert poisoned.parent.exists() is False
    assert paths["ledger"].exists() is False
    assert paths["marker"].exists() is False


def test_runtime_window_rejects_evidence_hardlinked_to_receipt_bundle(
    tmp_path: Path,
) -> None:
    emitter = _Emitter(tmp_path, [])
    window, paths = _window(tmp_path, emitter)
    bundle = paths["receipts"] / ("served-" + "a" * 32)
    bundle.mkdir(parents=True)
    for name in ("receipt.json", "payload.json", "evaluation.json"):
        (bundle / name).write_text("{}\n", encoding="utf-8")
    manifest = bundle / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "chain_id": "magma:chat_service:served:v0",
                "entries": [
                    {
                        "receipt": "receipt.json",
                        "payload": "payload.json",
                        "evaluation_result": "evaluation.json",
                    }
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    paths["enabled"].parent.mkdir(parents=True)
    try:
        os.link(manifest, paths["enabled"])
    except OSError as exc:
        pytest.skip(f"hardlink creation unavailable: {type(exc).__name__}")
    manifest_before = manifest.read_bytes()

    result = asyncio.run(window.start())

    assert result.status == "ineligible"
    assert result.reason == "runtime_window_start_failed:ValueError"
    assert manifest.read_bytes() == manifest_before
    assert paths["start"].exists() is False
    assert paths["marker"].exists() is False


def test_runtime_window_rejects_receipt_hardlink_created_during_drain(
    tmp_path: Path,
    monkeypatch,
) -> None:
    probe_source = tmp_path / "hardlink-probe-source"
    probe_alias = tmp_path / "hardlink-probe-alias"
    probe_source.write_bytes(b"probe")
    try:
        os.link(probe_source, probe_alias)
    except OSError as exc:
        pytest.skip(f"hardlink creation unavailable: {type(exc).__name__}")
    probe_alias.unlink()
    probe_source.unlink()

    emitter = _Emitter(tmp_path, [])
    window, paths = _window(tmp_path, emitter)
    bundle = paths["receipts"] / ("served-" + "a" * 32)
    bundle.mkdir(parents=True)
    for name in ("receipt.json", "payload.json", "evaluation.json"):
        (bundle / name).write_text("{}\n", encoding="utf-8")
    (bundle / "manifest.json").write_text(
        json.dumps(
            {
                "chain_id": "magma:chat_service:served:v0",
                "entries": [
                    {
                        "receipt": "receipt.json",
                        "payload": "payload.json",
                        "evaluation_result": "evaluation.json",
                    }
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    bundle_extra = bundle / "extra.bin"
    bundle_extra.write_bytes(b"")
    paths["ledger"].write_bytes(b"")
    original_drain = emitter.drain
    verifier_called = False

    def verify(**_kwargs):
        nonlocal verifier_called
        verifier_called = True
        return _pre_marker_verdict()

    monkeypatch.setattr(evidence, "verify_production_window", verify, raising=False)

    async def drain_and_alias(timeout_seconds: float):
        result = await original_drain(timeout_seconds)
        bundle_extra.unlink()
        os.link(paths["ledger"], bundle_extra)
        return result

    emitter.drain = drain_and_alias  # type: ignore[method-assign]

    async def run():
        assert (await window.start()).status == "running"
        return await window.shutdown()

    result = asyncio.run(run())

    assert result.status == "ineligible"
    assert verifier_called is False
    assert (
        result.reason
        == "runtime_window_shutdown_failed:ValueError"
    )
    assert os.path.samefile(paths["ledger"], bundle_extra)
    assert paths["marker"].exists() is False


def test_runtime_window_rejects_evidence_relinked_by_verifier_callback(
    tmp_path: Path,
    monkeypatch,
) -> None:
    probe_source = tmp_path / "hardlink-probe-source"
    probe_alias = tmp_path / "hardlink-probe-alias"
    probe_source.write_bytes(b"probe")
    try:
        os.link(probe_source, probe_alias)
    except OSError as exc:
        pytest.skip(f"hardlink creation unavailable: {type(exc).__name__}")
    probe_alias.unlink()
    probe_source.unlink()

    emitter = _Emitter(tmp_path, [])
    window, paths = _window(tmp_path, emitter)
    bundle = paths["receipts"] / ("served-" + "a" * 32)
    bundle.mkdir(parents=True)
    for name in ("receipt.json", "payload.json", "evaluation.json"):
        (bundle / name).write_text("{}\n", encoding="utf-8")
    (bundle / "manifest.json").write_text(
        json.dumps(
            {
                "chain_id": "magma:chat_service:served:v0",
                "entries": [
                    {
                        "receipt": "receipt.json",
                        "payload": "payload.json",
                        "evaluation_result": "evaluation.json",
                    }
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    bundle_extra = bundle / "extra.bin"
    bundle_extra.write_bytes(b"")
    paths["ledger"].write_bytes(b"")
    callback_ran = False

    def verify(**_kwargs):
        nonlocal callback_ran
        callback_ran = True
        paths["ledger"].unlink()
        os.link(bundle_extra, paths["ledger"])
        return _pre_marker_verdict()

    monkeypatch.setattr(evidence, "verify_production_window", verify, raising=False)

    async def run():
        assert (await window.start()).status == "running"
        return await window.shutdown()

    result = asyncio.run(run())

    assert callback_ran is True
    assert result.status == "ineligible"
    assert (
        result.reason
        == "runtime_window_shutdown_failed:ValueError"
    )
    assert result.lifecycle_verified is False
    assert result.clean_marker_written is False
    assert os.path.samefile(paths["ledger"], bundle_extra)
    assert paths["marker"].exists() is False


def test_receipt_scan_rejects_windows_reparse_bundle(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = tmp_path / "receipts"
    bundle = root / ("served-" + "a" * 32)
    bundle.mkdir(parents=True)
    real_lstat = os.lstat

    def hostile_lstat(path):
        details = real_lstat(path)
        if Path(path) == bundle:
            return SimpleNamespace(
                st_mode=details.st_mode,
                st_file_attributes=getattr(
                    stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400
                ),
            )
        return details

    monkeypatch.setattr(
        "waggledance.core.magma.chat_served_runtime_window.os.lstat",
        hostile_lstat,
    )

    with pytest.raises(ValueError, match="receipt_bundle_directory_invalid"):
        _scan_receipt_manifests(root)


@pytest.mark.skipif(os.name != "nt", reason="Windows junction boundary")
def test_evidence_append_rejects_real_windows_junction(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    junction = tmp_path / "junction"
    outside.mkdir()
    created = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(junction), str(outside)],
        check=False,
        capture_output=True,
        text=True,
    )
    if created.returncode != 0:
        pytest.skip("junction creation unavailable")
    try:
        with pytest.raises(ValueError, match="path_reparse_not_allowed"):
            _append_jsonl(junction / "escaped.jsonl", ({"safe": True},))
        assert not (outside / "escaped.jsonl").exists()
    finally:
        junction.rmdir()


def test_broken_final_symlink_cannot_escape_append_or_replace_marker(
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    append_target = outside / "escaped.jsonl"
    marker_target = outside / "escaped-marker.json"
    append_link = tmp_path / "append.jsonl"
    marker_link = tmp_path / "marker.json"
    try:
        os.symlink(append_target, append_link)
        os.symlink(marker_target, marker_link)
    except OSError as exc:
        pytest.skip(f"file symlink unavailable: {type(exc).__name__}")
    try:
        with pytest.raises(ValueError, match="path_reparse_not_allowed"):
            _append_jsonl(append_link, ({"safe": True},))
        with pytest.raises(ValueError, match="path_reparse_not_allowed"):
            _write_clean_marker_last(marker_link, {"status": "clean"})
        assert not append_target.exists()
        assert not marker_target.exists()
        assert append_link.is_symlink()
        assert marker_link.is_symlink()
    finally:
        append_link.unlink(missing_ok=True)
        marker_link.unlink(missing_ok=True)


def test_simulated_broken_final_reparse_is_guarded_before_lexists(
    tmp_path: Path,
    monkeypatch,
) -> None:
    append_path = tmp_path / "append.jsonl"
    marker_path = tmp_path / "marker.json"
    hostile = {append_path, marker_path}
    real_lstat = os.lstat

    def broken_reparse_lstat(path):
        if Path(path) in hostile:
            return SimpleNamespace(
                st_mode=stat.S_IFLNK,
                st_file_attributes=getattr(
                    stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400
                ),
            )
        return real_lstat(path)

    monkeypatch.setattr(
        "waggledance.core.magma.chat_served_runtime_window.os.lstat",
        broken_reparse_lstat,
    )

    with pytest.raises(ValueError, match="path_reparse_not_allowed"):
        _append_jsonl(append_path, ({"safe": True},))
    with pytest.raises(ValueError, match="path_reparse_not_allowed"):
        _write_clean_marker_last(marker_path, {"status": "clean"})
    assert not append_path.exists()
    assert not marker_path.exists()


def test_strict_jsonl_rejects_an_oversized_hostile_line(
    tmp_path: Path,
    monkeypatch,
) -> None:
    stream = tmp_path / "stream.jsonl"
    stream.write_text('{"value":"0123456789"}\n', encoding="utf-8")
    monkeypatch.setattr(
        "waggledance.core.magma.chat_served_runtime_window."
        "_MAX_JSONL_LINE_BYTES",
        8,
    )

    with pytest.raises(ValueError, match="jsonl_line_or_file_exceeds_bound"):
        _strict_jsonl(stream)


def test_api_lifespan_starts_before_yield_and_shuts_window_down_first() -> None:
    events: list[str] = []

    class _RuntimeWindow:
        async def start(self):
            events.append("window_start")
            return ChatServedRuntimeWindowResult(
                "running", "window:api-test", None, False, False
            )

        async def shutdown(self):
            events.append("window_shutdown")
            return ChatServedRuntimeWindowResult(
                "complete", "window:api-test", None, True, True
            )

    class _EventBus:
        async def publish(self, _event):
            events.append("shutdown_event")

    container = SimpleNamespace(
        llm=object(),
        vector_store=object(),
        memory_repository=object(),
        trust_store=object(),
        event_bus=_EventBus(),
        chat_served_runtime_window=_RuntimeWindow(),
        autogrowth_background_ticker=None,
        advisory_refresh_ticker=None,
        data_feed_scheduler=None,
    )
    app = SimpleNamespace(state=SimpleNamespace(container=container))

    async def run() -> None:
        async with lifespan(app):
            events.append("intake")

    asyncio.run(run())

    assert events[:3] == ["window_start", "intake", "window_shutdown"]
    assert events[-1] == "shutdown_event"
    assert app.state.chat_served_runtime_window_result["status"] == "complete"

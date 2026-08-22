# SPDX-License-Identifier: Apache-2.0
"""Focused AppendV1 tests for the direct Python bridge writer."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

import pytest

import tools.bridge_event_writer as bridge_writer
from tools.bridge_event_writer import (
    APPEND_MUTEX_NAME,
    APPEND_MUTEX_TIMEOUT_MS,
    QUEUE_PUBLICATION_MUTEX_NAME,
    QUEUE_PUBLICATION_MUTEX_TIMEOUT_MS,
    CHECKPOINT_SUFFIX,
    BridgeEventWriteError,
    WindowsAppendV1Backend,
    _PortableTestBackend,
    _checkpoint_bytes,
    write_bridge_event,
)


def _event(index: int = 1, *, agent: str = "codex") -> dict[str, object]:
    return {
        "ts_utc": f"2026-07-20T12:00:{index:02d}Z",
        "agent": agent,
        "type": "message",
        "task_id": f"python-append-v1-{index}",
        "status": "note",
        "severity": "",
        "to": "claude",
        "message": f"strict UTF-8 handoff {index}: ääkkönen",
        "paths": [],
        "write_scope": [],
        "run_id": "",
        "pid": 1234,
        "cwd": "C:\\Python\\project2",
        "payload": {"index": index},
    }


def _canonical(root: Path) -> Path:
    return root / "shared" / "events.jsonl"


def _rows(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _event_bytes(index: int = 1, *, agent: str = "codex") -> bytes:
    return (
        json.dumps(
            _event(index, agent=agent),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=False,
        )
        + "\n"
    ).encode("utf-8")


def _accepted_files(root: Path, state: str) -> list[Path]:
    return list((root / "spool" / "accepted-v1" / state).glob("bridge-wal-v1-*.jsonl"))


def test_lexical_guard_refuses_custom_target_before_creation(tmp_path: Path) -> None:
    root = tmp_path / "bridge-never-created"
    custom = tmp_path / "custom-events.jsonl"

    with pytest.raises(BridgeEventWriteError, match="non-canonical"):
        write_bridge_event(
            bridge_root=root,
            events_path=custom,
            event=_event(),
            backend=_PortableTestBackend(),
        )

    assert not root.exists()
    assert not custom.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows junction semantics required")
def test_windows_writer_rejects_accepted_queue_spool_junction(
    tmp_path: Path,
) -> None:
    powershell = shutil.which("powershell.exe")
    if powershell is None:
        pytest.skip("Windows PowerShell is required to create a test junction")
    root = tmp_path / "bridge"
    outside = tmp_path / "outside-spool"
    root.mkdir()
    outside.mkdir()
    environment = os.environ.copy()
    environment["WD_TEST_QUEUE_LINK"] = os.fspath(root / "spool")
    environment["WD_TEST_QUEUE_TARGET"] = os.fspath(outside)
    subprocess.run(
        [
            powershell,
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "New-Item -ItemType Junction -Path $env:WD_TEST_QUEUE_LINK "
            "-Target $env:WD_TEST_QUEUE_TARGET | Out-Null",
        ],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )

    with pytest.raises(BridgeEventWriteError, match="reparse point"):
        write_bridge_event(bridge_root=root, event=_event())

    assert list(outside.iterdir()) == []
    assert not _canonical(root).exists()

    ancestor_container = tmp_path / "ancestor-container"
    ancestor_outside = tmp_path / "ancestor-outside"
    ancestor_container.mkdir()
    ancestor_outside.mkdir()
    ancestor_link = ancestor_container / "parent-link"
    environment["WD_TEST_QUEUE_LINK"] = os.fspath(ancestor_link)
    environment["WD_TEST_QUEUE_TARGET"] = os.fspath(ancestor_outside)
    subprocess.run(
        [
            powershell,
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "New-Item -ItemType Junction -Path $env:WD_TEST_QUEUE_LINK "
            "-Target $env:WD_TEST_QUEUE_TARGET | Out-Null",
        ],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )
    missing_root = ancestor_link / "missing-bridge"

    with pytest.raises(BridgeEventWriteError, match="reparse point"):
        write_bridge_event(bridge_root=missing_root, event=_event(2))

    assert not (ancestor_outside / "missing-bridge").exists()


@pytest.mark.skipif(os.name == "nt", reason="portable symlink coverage runs off Windows")
def test_injected_writer_queues_without_following_canonical_parent_symlink(
    tmp_path: Path,
) -> None:
    root = tmp_path / "bridge"
    outside = tmp_path / "outside-shared"
    root.mkdir()
    outside.mkdir()
    (root / "shared").symlink_to(outside, target_is_directory=True)

    with pytest.warns(RuntimeWarning, match="symbolic link"):
        result = write_bridge_event(
            bridge_root=root,
            event=_event(),
            write_sidecars=False,
            backend=_PortableTestBackend(),
        )

    assert result.delivery_status == "queued"
    assert result.canonical_durable is False
    assert result.retained_wal_sha256 == hashlib.sha256(_event_bytes()).hexdigest()
    assert result.retained_wal_path is not None
    assert result.retained_wal_path.read_bytes() == _event_bytes()
    assert result.retained_wal_path.parent == root / "spool" / "accepted-v1" / "ready"
    assert list(outside.iterdir()) == []


@pytest.mark.skipif(os.name != "nt", reason="Windows junction semantics required")
def test_windows_writer_queues_without_following_shared_junction(
    tmp_path: Path,
) -> None:
    powershell = shutil.which("powershell.exe")
    if powershell is None:
        pytest.skip("Windows PowerShell is required to create a test junction")
    root = tmp_path / "bridge"
    outside = tmp_path / "outside-shared"
    root.mkdir()
    outside.mkdir()
    environment = os.environ.copy()
    environment["WD_TEST_QUEUE_LINK"] = os.fspath(root / "shared")
    environment["WD_TEST_QUEUE_TARGET"] = os.fspath(outside)
    subprocess.run(
        [
            powershell,
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "New-Item -ItemType Junction -Path $env:WD_TEST_QUEUE_LINK "
            "-Target $env:WD_TEST_QUEUE_TARGET | Out-Null",
        ],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )

    with pytest.warns(RuntimeWarning, match="reparse point"):
        result = write_bridge_event(
            bridge_root=root,
            event=_event(),
            write_sidecars=False,
        )

    assert result.delivery_status == "queued"
    assert result.canonical_durable is False
    assert result.retained_wal_sha256 == hashlib.sha256(_event_bytes()).hexdigest()
    assert result.retained_wal_path is not None
    assert result.retained_wal_path.read_bytes() == _event_bytes()
    assert result.retained_wal_path.parent == root / "spool" / "accepted-v1" / "ready"
    assert list(outside.iterdir()) == []


@pytest.mark.parametrize(
    ("case", "updates", "missing"),
    [
        ("invalid-agent", {"agent": "Codex"}, None),
        ("unknown-type", {"type": "custom"}, None),
        ("non-string-type", {"type": 7}, None),
        ("non-string-agent", {"agent": 7}, None),
        ("non-string-task-id", {"task_id": None}, None),
        ("non-string-status", {"status": []}, None),
        ("missing-agent", {}, "agent"),
        ("missing-type", {}, "type"),
        ("missing-task-id", {}, "task_id"),
        ("missing-status", {}, "status"),
    ],
)
def test_invalid_replayer_shape_refuses_before_root_or_wal_creation(
    tmp_path: Path,
    case: str,
    updates: dict[str, object],
    missing: str | None,
) -> None:
    del case
    root = tmp_path / "bridge-never-created"
    event = _event()
    event.update(updates)
    if missing is not None:
        event.pop(missing)

    with pytest.raises(BridgeEventWriteError):
        write_bridge_event(
            bridge_root=root,
            event=event,
            backend=_PortableTestBackend(),
        )

    assert not root.exists()
    assert not _canonical(root).exists()


@pytest.mark.skipif(os.name == "nt", reason="production backend is supported on Windows")
def test_production_backend_fails_closed_off_windows_before_creation(
    tmp_path: Path,
) -> None:
    root = tmp_path / "bridge-never-created"

    with pytest.raises(BridgeEventWriteError, match="require Windows"):
        write_bridge_event(bridge_root=root, event=_event())

    assert not root.exists()


class _WalObservationBackend(_PortableTestBackend):
    saw_publication_fence_before_pending = False
    saw_durable_pending_before_wait = False

    def acquire_mutex(self, name: str, timeout_ms: int):
        if name == QUEUE_PUBLICATION_MUTEX_NAME:
            assert _accepted_files(self._root, "pending") == []
            self.saw_publication_fence_before_pending = True
        elif name == APPEND_MUTEX_NAME and not self.saw_durable_pending_before_wait:
            pending = _accepted_files(self._root, "pending")
            assert len(pending) == 1
            assert pending[0].read_bytes() == _event_bytes()
            self.saw_durable_pending_before_wait = True
        return super().acquire_mutex(name, timeout_ms)


class _AcceptedPublishObservationBackend(_PortableTestBackend):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.accepted_publications: list[tuple[bool, bool]] = []

    def move(
        self,
        source: Path,
        destination: Path,
        *,
        replace: bool,
        write_through: bool,
    ) -> None:
        if destination.parent.name == "ready":
            self.accepted_publications.append((replace, write_through))
        super().move(
            source,
            destination,
            replace=replace,
            write_through=write_through,
        )


def test_pending_wal_is_durable_before_wait_and_clean_success_removes_it(
    tmp_path: Path,
) -> None:
    root = tmp_path / "bridge"
    backend = _WalObservationBackend()
    backend._root = root

    result = write_bridge_event(
        bridge_root=root,
        event=_event(),
        backend=backend,
    )

    assert backend.saw_publication_fence_before_pending is True
    assert backend.saw_durable_pending_before_wait is True
    assert backend.mutex_requests == [
        (
            QUEUE_PUBLICATION_MUTEX_NAME,
            QUEUE_PUBLICATION_MUTEX_TIMEOUT_MS,
        ),
        (APPEND_MUTEX_NAME, APPEND_MUTEX_TIMEOUT_MS),
        (APPEND_MUTEX_NAME, APPEND_MUTEX_TIMEOUT_MS),
    ]
    assert QUEUE_PUBLICATION_MUTEX_NAME == (
        r"Global\WaggleDanceBridgeAcceptedQueuePublicationV1"
    )
    assert QUEUE_PUBLICATION_MUTEX_TIMEOUT_MS == 10_000
    assert APPEND_MUTEX_NAME == r"Global\WaggleDanceBridgeAppendV1"
    assert APPEND_MUTEX_TIMEOUT_MS == 10_000
    assert result.delivery_status == "canonical"
    assert result.canonical_durable is True
    assert result.checkpoint_advanced is True
    assert result.retained_wal_path is None
    assert result.retained_wal_sha256 is None
    assert _accepted_files(root, "pending") == []
    assert _accepted_files(root, "ready") == []
    assert _rows(_canonical(root)) == [_event()]
    raw = _canonical(root).read_bytes()
    assert raw.endswith(b"\n")
    assert b"\r" not in raw
    assert "ääkkönen".encode() in raw
    outbox = root / "outbox" / "codex" / "2026-07-20.jsonl"
    assert not Path(os.fspath(outbox) + CHECKPOINT_SUFFIX).exists()


@pytest.mark.parametrize("outcome", ["error", "timeout", "abandoned"])
def test_publication_fence_failure_precedes_wal_acceptance(
    tmp_path: Path,
    outcome: str,
) -> None:
    root = tmp_path / "bridge"
    backend = _PortableTestBackend(mutex_outcomes=[outcome])

    with pytest.raises(
        BridgeEventWriteError,
        match="accepted queue publication fence",
    ):
        write_bridge_event(bridge_root=root, event=_event(), backend=backend)

    assert not root.exists()
    assert backend.mutex_requests == [
        (
            QUEUE_PUBLICATION_MUTEX_NAME,
            QUEUE_PUBLICATION_MUTEX_TIMEOUT_MS,
        )
    ]


@pytest.mark.parametrize("outcome", ["error", "timeout", "abandoned"])
def test_unclean_mutex_returns_verified_queued_delivery_without_sidecars(
    tmp_path: Path,
    outcome: str,
) -> None:
    root = tmp_path / "bridge"
    backend = _AcceptedPublishObservationBackend(
        mutex_outcomes=["clean", outcome]
    )

    with pytest.warns(RuntimeWarning, match="accepted into the durable replay queue"):
        result = write_bridge_event(bridge_root=root, event=_event(), backend=backend)

    assert not _canonical(root).exists()
    assert not (root / "shared").exists()
    assert result.delivery_status == "queued"
    assert result.canonical_durable is False
    assert result.checkpoint_advanced is False
    assert result.retained_wal_path is not None
    assert result.retained_wal_path.parent == root / "spool" / "accepted-v1" / "ready"
    assert re.fullmatch(r"bridge-wal-v1-[0-9a-f]{32}\.jsonl", result.retained_wal_path.name)
    assert result.retained_wal_path.read_bytes() == _event_bytes()
    assert result.retained_wal_sha256 == hashlib.sha256(_event_bytes()).hexdigest()
    assert re.fullmatch(r"[0-9a-f]{64}", result.retained_wal_sha256)
    assert result.outbox_written is False
    assert result.last_file_written is False
    assert not (root / "outbox").exists()
    assert not (root / "shared" / "last_codex.json").exists()
    assert _accepted_files(root, "pending") == []
    assert _accepted_files(root, "ready") == [result.retained_wal_path]
    marker = result.retained_wal_path.parent / (
        f".{result.retained_wal_path.name}.pending-recovery-blocked"
    )
    marker_record = json.loads(marker.read_text("utf-8"))
    assert marker_record["wal_leaf"] == result.retained_wal_path.name
    assert marker_record["expected_sha256"] == result.retained_wal_sha256
    assert backend.accepted_publications == [(False, True), (False, True)]
    if outcome == "abandoned":
        assert any("dirty ownership" in warning for warning in result.warning_messages)


def test_checkpoint_has_exact_order_schema_decimal_lengths_identity_and_tail_sha(
    tmp_path: Path,
) -> None:
    root = tmp_path / "bridge"
    backend = _PortableTestBackend()

    write_bridge_event(
        bridge_root=root,
        event=_event(),
        write_sidecars=False,
        backend=backend,
    )

    events = _canonical(root)
    checkpoint = Path(os.fspath(events) + CHECKPOINT_SUFFIX)
    payload = checkpoint.read_bytes()
    parsed = json.loads(payload)
    assert list(parsed) == [
        "schema",
        "version",
        "file_identity",
        "validated_length",
        "tail_anchor_length",
        "tail_anchor_sha256",
    ]
    assert parsed["schema"] == "waggledance.bridge.append-v1-validation"
    assert parsed["version"] == 1
    assert parsed["file_identity"].startswith("test-file-id-v1:")
    assert parsed["validated_length"] == str(events.stat().st_size)
    assert parsed["tail_anchor_length"] == str(min(4096, events.stat().st_size))
    assert len(parsed["tail_anchor_sha256"]) == 64
    assert payload == _checkpoint_bytes(
        file_identity=parsed["file_identity"],
        length=int(parsed["validated_length"]),
        anchor_length=int(parsed["tail_anchor_length"]),
        anchor_sha256=parsed["tail_anchor_sha256"],
    )


def test_corrupt_checkpoint_forces_full_scan_then_is_replaced(tmp_path: Path) -> None:
    root = tmp_path / "bridge"
    backend = _PortableTestBackend()
    write_bridge_event(
        bridge_root=root,
        event=_event(1),
        write_sidecars=False,
        backend=backend,
    )
    checkpoint = Path(os.fspath(_canonical(root)) + CHECKPOINT_SUFFIX)
    checkpoint.write_bytes(b'{"schema":"forged"}\n')

    write_bridge_event(
        bridge_root=root,
        event=_event(2),
        write_sidecars=False,
        backend=backend,
    )

    assert _rows(_canonical(root)) == [_event(1), _event(2)]
    parsed = json.loads(checkpoint.read_bytes())
    assert parsed["validated_length"] == str(_canonical(root).stat().st_size)


def _encode_checkpoint_object(value: dict[str, object]) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=False)
        + "\n"
    ).encode("utf-8")


def _mutated_checkpoint(kind: str, exact: bytes) -> bytes:
    checkpoint = json.loads(exact)
    if kind == "duplicate-key":
        return exact.replace(
            b'{"schema":',
            b'{"schema":"duplicate","schema":',
            1,
        )
    if kind == "order":
        return _encode_checkpoint_object(
            {
                "version": checkpoint["version"],
                "schema": checkpoint["schema"],
                "file_identity": checkpoint["file_identity"],
                "validated_length": checkpoint["validated_length"],
                "tail_anchor_length": checkpoint["tail_anchor_length"],
                "tail_anchor_sha256": checkpoint["tail_anchor_sha256"],
            }
        )
    if kind == "key-case":
        checkpoint["Schema"] = checkpoint.pop("schema")
        return _encode_checkpoint_object(checkpoint)
    if kind == "wrong-type":
        checkpoint["validated_length"] = int(checkpoint["validated_length"])
        return _encode_checkpoint_object(checkpoint)
    if kind == "trailing-byte":
        return exact + b"x"
    if kind == "stale-identity":
        checkpoint["file_identity"] = "test-file-id-v1:stale:identity"
        return _encode_checkpoint_object(checkpoint)
    if kind == "stale-length":
        checkpoint["validated_length"] = str(int(checkpoint["validated_length"]) + 1)
        return _encode_checkpoint_object(checkpoint)
    if kind == "stale-tail":
        checkpoint["tail_anchor_sha256"] = "0" * 64
        return _encode_checkpoint_object(checkpoint)
    raise AssertionError(f"unknown checkpoint mutation: {kind}")


@pytest.mark.parametrize(
    "kind",
    [
        "duplicate-key",
        "order",
        "key-case",
        "wrong-type",
        "trailing-byte",
        "stale-identity",
        "stale-length",
        "stale-tail",
    ],
)
def test_invalid_checkpoint_corpus_forces_exactly_one_full_scan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
) -> None:
    root = tmp_path / kind
    backend = _PortableTestBackend()
    write_bridge_event(
        bridge_root=root,
        event=_event(1),
        write_sidecars=False,
        backend=backend,
    )
    checkpoint_path = Path(os.fspath(_canonical(root)) + CHECKPOINT_SUFFIX)
    checkpoint_path.write_bytes(_mutated_checkpoint(kind, checkpoint_path.read_bytes()))

    full_scans: list[Path] = []
    original = bridge_writer._assert_strict_utf8_target

    def instrumented_full_scan(stream, path: Path, length: int) -> None:
        full_scans.append(path)
        original(stream, path, length)

    monkeypatch.setattr(
        bridge_writer,
        "_assert_strict_utf8_target",
        instrumented_full_scan,
    )

    write_bridge_event(
        bridge_root=root,
        event=_event(2),
        write_sidecars=False,
        backend=backend,
    )

    assert full_scans == [_canonical(root)]
    assert _rows(_canonical(root)) == [_event(1), _event(2)]


def test_exact_checkpoint_performs_zero_full_scans(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "exact"
    backend = _PortableTestBackend()
    write_bridge_event(
        bridge_root=root,
        event=_event(1),
        write_sidecars=False,
        backend=backend,
    )

    def refuse_full_scan(*_args, **_kwargs) -> None:
        raise AssertionError("exact checkpoint unexpectedly fell back to full scan")

    monkeypatch.setattr(
        bridge_writer,
        "_assert_strict_utf8_target",
        refuse_full_scan,
    )
    write_bridge_event(
        bridge_root=root,
        event=_event(2),
        write_sidecars=False,
        backend=backend,
    )

    assert _rows(_canonical(root)) == [_event(1), _event(2)]


def test_full_scan_refuses_invalid_utf8_and_preserves_canonical(tmp_path: Path) -> None:
    root = tmp_path / "bridge"
    events = _canonical(root)
    events.parent.mkdir(parents=True)
    original = b'{"old":"\xff"}\n'
    events.write_bytes(original)

    with pytest.warns(RuntimeWarning, match="accepted into the durable replay queue"):
        result = write_bridge_event(
            bridge_root=root,
            event=_event(),
            backend=_PortableTestBackend(),
        )

    assert events.read_bytes() == original
    assert result.delivery_status == "queued"
    assert result.canonical_durable is False
    assert result.retained_wal_path is not None
    assert result.retained_wal_path.read_bytes() == _event_bytes()
    assert result.retained_wal_sha256 == hashlib.sha256(_event_bytes()).hexdigest()
    assert result.outbox_written is False
    assert result.last_file_written is False
    assert not (root / "outbox").exists()
    assert not (root / "shared" / "last_codex.json").exists()


class _PartialFailureFile:
    def __init__(self, inner) -> None:
        self._inner = inner
        self.path = inner.path
        self._failed = False

    def __getattr__(self, name: str):
        return getattr(self._inner, name)

    def write_at(self, offset: int, payload: bytes) -> None:
        if not self._failed:
            self._failed = True
            self._inner.write_at(offset, payload[: max(1, len(payload) // 3)])
            raise OSError("simulated partial canonical write")
        self._inner.write_at(offset, payload)


class _PartialFailureBackend(_PortableTestBackend):
    def open_or_create_shared_read(self, path: Path):
        return _PartialFailureFile(super().open_or_create_shared_read(path))


def test_preflush_partial_append_rolls_back_and_returns_queued_delivery(
    tmp_path: Path,
) -> None:
    root = tmp_path / "bridge"

    with pytest.warns(RuntimeWarning, match="accepted into the durable replay queue"):
        result = write_bridge_event(
            bridge_root=root,
            event=_event(),
            backend=_PartialFailureBackend(),
        )

    assert _canonical(root).read_bytes() == b""
    assert result.delivery_status == "queued"
    assert result.canonical_durable is False
    assert result.checkpoint_advanced is False
    assert result.retained_wal_path is not None
    assert result.retained_wal_path.read_bytes() == _event_bytes()
    assert result.retained_wal_sha256 == hashlib.sha256(_event_bytes()).hexdigest()
    assert result.outbox_written is False
    assert result.last_file_written is False
    assert not (root / "outbox").exists()
    assert not (root / "shared" / "last_codex.json").exists()


class _RollbackFailureFile(_PartialFailureFile):
    def truncate(self, length: int) -> None:
        raise OSError("simulated rollback failure")


class _RollbackFailureBackend(_PortableTestBackend):
    def open_or_create_shared_read(self, path: Path):
        return _RollbackFailureFile(super().open_or_create_shared_read(path))


def test_append_rollback_uncertainty_returns_recovery_queued_delivery(
    tmp_path: Path,
) -> None:
    root = tmp_path / "bridge"

    with pytest.warns(RuntimeWarning, match="accepted into the durable replay queue"):
        result = write_bridge_event(
            bridge_root=root,
            event=_event(),
            backend=_RollbackFailureBackend(),
        )

    assert result.delivery_status == "queued"
    assert result.canonical_durable is False
    assert result.retained_wal_path is not None
    assert result.retained_wal_path.parent == root / "spool" / "accepted-v1" / "ready"
    assert result.retained_wal_path.read_bytes() == _event_bytes()
    assert result.retained_wal_sha256 == hashlib.sha256(_event_bytes()).hexdigest()
    assert any("ROLLBACK FAILED" in warning for warning in result.warning_messages)
    assert _accepted_files(root, "pending") == []
    assert not (root / "outbox").exists()
    assert not (root / "shared" / "last_codex.json").exists()


class _AcceptedPublicationUncertaintyBackend(_PortableTestBackend):
    def __init__(self, mode: str) -> None:
        super().__init__(mutex_outcomes=["clean", "timeout"])
        self.mode = mode

    def move(
        self,
        source: Path,
        destination: Path,
        *,
        replace: bool,
        write_through: bool,
    ) -> None:
        if (
            destination.parent.name == "ready"
            and destination.name.startswith("bridge-wal-v1-")
            and self.mode == "promotion"
        ):
            raise OSError("simulated accepted WAL promotion failure")
        super().move(
            source,
            destination,
            replace=replace,
            write_through=write_through,
        )

def test_ready_publication_failure_returns_accepted_pending_queue(
    tmp_path: Path,
) -> None:
    root = tmp_path / "bridge"

    with pytest.warns(RuntimeWarning, match="accepted into the durable replay queue"):
        result = write_bridge_event(
            bridge_root=root,
            event=_event(),
            backend=_AcceptedPublicationUncertaintyBackend("promotion"),
        )

    assert result.delivery_status == "queued"
    assert result.canonical_durable is False
    assert result.retained_wal_path is not None
    assert result.retained_wal_path.parent.name == "pending"
    assert result.retained_wal_path.read_bytes() == _event_bytes()
    assert result.retained_wal_sha256 == hashlib.sha256(_event_bytes()).hexdigest()
    assert not _canonical(root).exists()
    assert not (root / "shared").exists()
    assert not (root / "outbox").exists()
    assert not (root / "shared" / "last_codex.json").exists()
    assert _accepted_files(root, "ready") == []
    marker = root / "spool" / "accepted-v1" / "ready" / (
        f".{result.retained_wal_path.name}.pending-recovery-blocked"
    )
    assert json.loads(marker.read_text("utf-8"))["expected_sha256"] == (
        result.retained_wal_sha256
    )


class _PendingReadbackMismatchFile:
    def __init__(self, inner) -> None:
        self._inner = inner
        self.path = inner.path

    def __getattr__(self, name: str):
        return getattr(self._inner, name)

    def read_at(self, offset: int, count: int) -> bytes:
        payload = self._inner.read_at(offset, count)
        return payload[:-1] + b"x" if payload else payload


class _PendingReadbackMismatchBackend(_PortableTestBackend):
    def create_new(self, path: Path, *, hidden: bool):
        return _PendingReadbackMismatchFile(
            super().create_new(path, hidden=hidden)
        )


class _UnexcludablePendingMismatchBackend(_PendingReadbackMismatchBackend):
    def move(
        self,
        source: Path,
        destination: Path,
        *,
        replace: bool,
        write_through: bool,
    ) -> None:
        if source.parent.name == "pending" and destination.parent.name == "quarantine":
            raise OSError("simulated unaccepted quarantine failure")
        super().move(
            source,
            destination,
            replace=replace,
            write_through=write_through,
        )

    def delete(self, path: Path) -> None:
        if path.parent.name == "pending":
            raise OSError("simulated unaccepted delete failure")
        super().delete(path)


def test_unverified_pending_wal_is_excluded_before_hard_error(tmp_path: Path) -> None:
    root = tmp_path / "bridge"

    with pytest.raises(BridgeEventWriteError, match="excluded from automatic replay"):
        write_bridge_event(
            bridge_root=root,
            event=_event(),
            backend=_PendingReadbackMismatchBackend(),
        )

    assert not _canonical(root).exists()
    assert _accepted_files(root, "pending") == []
    assert _accepted_files(root, "ready") == []
    quarantine = root / "spool" / "accepted-v1" / "quarantine"
    assert len(list(quarantine.glob("unaccepted-bridge-wal-v1-*.jsonl"))) == 1


def test_acceptance_unknown_markerless_pending_never_auto_replays(tmp_path: Path) -> None:
    powershell = shutil.which("powershell.exe")
    if powershell is None:
        pytest.skip("Windows PowerShell is required for the accepted queue smoke")
    root = tmp_path / "bridge"

    with pytest.raises(BridgeEventWriteError, match="acceptance is unknown"):
        write_bridge_event(
            bridge_root=root,
            event=_event(),
            backend=_UnexcludablePendingMismatchBackend(),
        )

    pending = _accepted_files(root, "pending")
    assert len(pending) == 1
    marker = root / "spool" / "accepted-v1" / "ready" / (
        f".{pending[0].name}.pending-recovery-blocked"
    )
    assert not marker.exists()
    source_bin = Path(__file__).parents[2] / ".agent-bridge" / "bin"
    isolated_bin = tmp_path / "isolated-bin"
    isolated_bin.mkdir()
    mutex_suffix = hashlib.sha256(os.fspath(tmp_path).encode()).hexdigest()[:16]
    replacements = {
        r"Global\WaggleDanceBridgeAcceptedQueuePublicationV1": (
            rf"Local\WaggleDanceBridgeAcceptedQueuePublicationV1-{mutex_suffix}"
        ),
        r"Global\WaggleDanceBridgeAppendV1": (
            rf"Local\WaggleDanceBridgeAppendV1-{mutex_suffix}"
        ),
        r"Global\WaggleDanceBridgeSpoolReplayV1": (
            rf"Local\WaggleDanceBridgeSpoolReplayV1-{mutex_suffix}"
        ),
    }
    for script_name in (
        "Drain-AcceptedBridgeQueue.ps1",
        "Restore-BridgeSpool.ps1",
    ):
        script_text = (source_bin / script_name).read_text(encoding="utf-8")
        for original, replacement in replacements.items():
            if original in script_text:
                script_text = script_text.replace(original, replacement)
        assert rf"Local\WaggleDanceBridgeAppendV1-{mutex_suffix}" in script_text
        assert (
            rf"Local\WaggleDanceBridgeAcceptedQueuePublicationV1-{mutex_suffix}"
            in script_text
        )
        if script_name == "Restore-BridgeSpool.ps1":
            assert (
                rf"Local\WaggleDanceBridgeSpoolReplayV1-{mutex_suffix}"
                in script_text
            )
        (isolated_bin / script_name).write_text(script_text, encoding="utf-8")
    drain = isolated_bin / "Drain-AcceptedBridgeQueue.ps1"
    completed = subprocess.run(
        [
            powershell,
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            os.fspath(drain),
            "-BridgeRoot",
            os.fspath(root),
            "-PendingMinAgeSeconds",
            "0",
            "-ReceiptJson",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    receipt = json.loads(completed.stdout)
    assert receipt["pending_promoted"] == 0
    assert receipt["pending_failed"] == 1
    assert receipt["drained"] == 0
    assert receipt["failed"] == 1
    assert pending[0].is_file()
    assert not _canonical(root).exists()


class _AfterCanonicalCheckpointFailureBackend(_PortableTestBackend):
    def __init__(self) -> None:
        super().__init__()
        self.checkpoint_replacements = 0

    def move(self, source: Path, destination: Path, *, replace: bool, write_through: bool) -> None:
        if destination.name.endswith(CHECKPOINT_SUFFIX):
            self.checkpoint_replacements += 1
            if self.checkpoint_replacements == 2:
                raise OSError("simulated postflush checkpoint failure")
        super().move(
            source,
            destination,
            replace=replace,
            write_through=write_through,
        )


def test_postflush_checkpoint_failure_returns_success_and_retains_wal(
    tmp_path: Path,
) -> None:
    root = tmp_path / "bridge"

    with pytest.warns(RuntimeWarning, match="checkpoint advance failed"):
        result = write_bridge_event(
            bridge_root=root,
            event=_event(),
            write_sidecars=False,
            backend=_AfterCanonicalCheckpointFailureBackend(),
        )

    assert result.canonical_durable is True
    assert result.delivery_status == "canonical"
    assert result.checkpoint_advanced is False
    assert result.retained_wal_path is not None
    assert result.retained_wal_path.is_file()
    assert result.retained_wal_path.parent == root / "spool" / "accepted-v1" / "ready"
    assert result.retained_wal_path.read_bytes() == _event_bytes()
    assert result.retained_wal_sha256 == hashlib.sha256(_event_bytes()).hexdigest()
    assert _rows(_canonical(root)) == [_event()]


def test_warning_filter_cannot_turn_postflush_success_into_retryable_failure(
    tmp_path: Path,
) -> None:
    root = tmp_path / "bridge"

    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("error", RuntimeWarning)
        result = write_bridge_event(
            bridge_root=root,
            event=_event(),
            write_sidecars=False,
            backend=_AfterCanonicalCheckpointFailureBackend(),
        )

    assert result.canonical_durable is True
    assert result.checkpoint_advanced is False
    assert _rows(_canonical(root)) == [_event()]


class _WalCleanupFailureBackend(_PortableTestBackend):
    def delete(self, path: Path) -> None:
        if path.parent.name == "pending" and path.parent.parent.name == "accepted-v1":
            raise OSError("simulated WAL cleanup failure")
        super().delete(path)


def test_postflush_wal_cleanup_failure_returns_success_and_retains_wal(
    tmp_path: Path,
) -> None:
    root = tmp_path / "bridge"

    with pytest.warns(RuntimeWarning, match="WAL cleanup failed"):
        result = write_bridge_event(
            bridge_root=root,
            event=_event(),
            write_sidecars=False,
            backend=_WalCleanupFailureBackend(),
        )

    assert result.canonical_durable is True
    assert result.delivery_status == "canonical"
    assert result.retained_wal_path is not None
    assert result.retained_wal_path.is_file()
    assert result.retained_wal_path.parent == root / "spool" / "accepted-v1" / "ready"
    assert result.retained_wal_path.read_bytes() == _event_bytes()
    assert result.retained_wal_sha256 == hashlib.sha256(_event_bytes()).hexdigest()
    assert _rows(_canonical(root)) == [_event()]


def test_sidecar_uses_fresh_clean_mutex_and_failure_is_best_effort(
    tmp_path: Path,
) -> None:
    root = tmp_path / "bridge"
    backend = _PortableTestBackend(
        mutex_outcomes=["clean", "clean", "abandoned"]
    )

    with pytest.warns(RuntimeWarning, match="auxiliary outbox append was skipped"):
        result = write_bridge_event(
            bridge_root=root,
            event=_event(),
            backend=backend,
        )

    assert result.canonical_durable is True
    assert result.outbox_written is False
    assert result.last_file_written is True
    assert backend.mutex_acquisitions == 3
    assert not (root / "outbox").exists()
    assert json.loads((root / "shared" / "last_codex.json").read_text("utf-8")) == _event()


def test_sidecars_can_be_disabled_for_canonical_only_callers(tmp_path: Path) -> None:
    root = tmp_path / "bridge"
    backend = _PortableTestBackend()

    result = write_bridge_event(
        bridge_root=root,
        event=_event(),
        write_sidecars=False,
        backend=backend,
    )

    assert result.outbox_written is False
    assert result.last_file_written is False
    assert backend.mutex_acquisitions == 2
    assert not (root / "outbox").exists()
    assert not (root / "shared" / "last_codex.json").exists()


def test_mutex_release_failure_warns_without_negating_durable_success(
    tmp_path: Path,
) -> None:
    root = tmp_path / "bridge"

    with pytest.warns(RuntimeWarning, match="release failed"):
        result = write_bridge_event(
            bridge_root=root,
            event=_event(),
            write_sidecars=False,
            backend=_PortableTestBackend(fail_release=True),
        )

    assert result.canonical_durable is True
    assert _rows(_canonical(root)) == [_event()]


def test_portable_injected_backend_serializes_concurrent_rows(tmp_path: Path) -> None:
    root = tmp_path / "bridge"
    backend = _PortableTestBackend()

    def emit(index: int) -> None:
        write_bridge_event(
            bridge_root=root,
            event=_event(index),
            write_sidecars=False,
            backend=backend,
        )

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(emit, range(1, 9)))

    rows = _rows(_canonical(root))
    assert len(rows) == 8
    assert {row["task_id"] for row in rows} == {f"python-append-v1-{i}" for i in range(1, 9)}


@pytest.mark.skipif(os.name != "nt", reason="Windows kernel32 integration probe")
def test_windows_python_and_powershell_writers_handoff_on_same_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shell = shutil.which("pwsh") or shutil.which("powershell")
    if shell is None:
        pytest.skip("PowerShell is unavailable")
    root = tmp_path / "bridge"
    write_bridge_event(
        bridge_root=root,
        event=_event(1),
        write_sidecars=False,
        backend=WindowsAppendV1Backend(),
    )
    script = Path(__file__).resolve().parents[2] / ".agent-bridge" / "bin" / "Write-AgentEvent.ps1"
    env = os.environ.copy()
    env["AGENT_BRIDGE_RUNTIME_ROOT"] = str(root)
    env["AGENT_BRIDGE_TEST_FAIL_ON_FULL_VALIDATION"] = "1"
    completed = subprocess.run(
        [
            shell,
            "-NoProfile",
            "-File",
            str(script),
            "-Agent",
            "codex",
            "-Type",
            "message",
            "-TaskId",
            "powershell-append-v1-handoff",
            "-Status",
            "note",
            "-To",
            "claude",
            "-Message",
            "PowerShell continued the Python-created canonical stream.",
        ],
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr

    def refuse_python_full_scan(*_args, **_kwargs) -> None:
        raise AssertionError("PowerShell checkpoint was not byte-compatible with Python")

    monkeypatch.setattr(
        bridge_writer,
        "_assert_strict_utf8_target",
        refuse_python_full_scan,
    )
    write_bridge_event(
        bridge_root=root,
        event=_event(2),
        write_sidecars=False,
        backend=WindowsAppendV1Backend(),
    )
    rows = _rows(_canonical(root))
    assert [row["task_id"] for row in rows] == [
        "python-append-v1-1",
        "powershell-append-v1-handoff",
        "python-append-v1-2",
    ]


@pytest.mark.skipif(os.name != "nt", reason="Windows hard-link semantics required")
def test_windows_writer_queues_without_mutating_hardlinked_canonical(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "bridge"
    shared = root / "shared"
    shared.mkdir(parents=True)
    sentinel = tmp_path / "outside-sentinel.jsonl"
    original = _event_bytes(9)
    sentinel.write_bytes(original)
    os.link(sentinel, _canonical(root))
    mutex_suffix = hashlib.sha256(os.fspath(tmp_path).encode()).hexdigest()[:16]
    monkeypatch.setattr(
        bridge_writer,
        "APPEND_MUTEX_NAME",
        rf"Local\WaggleDanceBridgeAppendV1-{mutex_suffix}",
    )
    monkeypatch.setattr(
        bridge_writer,
        "QUEUE_PUBLICATION_MUTEX_NAME",
        rf"Local\WaggleDanceBridgeAcceptedQueuePublicationV1-{mutex_suffix}",
    )

    with pytest.warns(RuntimeWarning, match="plain single-link"):
        result = write_bridge_event(
            bridge_root=root,
            event=_event(10),
            write_sidecars=False,
            backend=WindowsAppendV1Backend(),
        )

    assert result.delivery_status == "queued"
    assert result.canonical_durable is False
    assert any("plain single-link" in item for item in result.warning_messages)
    assert sentinel.read_bytes() == original
    assert _canonical(root).read_bytes() == original
    assert result.retained_wal_path is not None
    assert result.retained_wal_path.is_file()


@pytest.mark.skipif(os.name != "nt", reason="Windows kernel32 concurrency probe")
def test_windows_backend_serializes_two_python_writers(tmp_path: Path) -> None:
    root = tmp_path / "bridge"

    def emit(index: int) -> None:
        write_bridge_event(
            bridge_root=root,
            event=_event(index),
            write_sidecars=False,
            backend=WindowsAppendV1Backend(),
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(emit, [1, 2]))

    rows = _rows(_canonical(root))
    assert {row["task_id"] for row in rows} == {
        "python-append-v1-1",
        "python-append-v1-2",
    }

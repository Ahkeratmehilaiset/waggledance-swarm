# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone

import pytest

from tools import verify_chat_served_production_window as verifier_cli
from tools.verify_chat_served_production_window import INPUT_SCHEMA, main
from tools.verify_magma_receipt import verify_manifest
from waggledance.core.magma.canonical import canonical_json_bytes, sha256_digest
from waggledance.core.magma.chat_query_route_evidence import (
    NORMALIZATION_VERSION,
    canonical_query_digest,
)
from waggledance.core.magma.chat_served_accounting import (
    REQUIRED_CHAT_SERVED_POINTS,
)
from waggledance.core.magma.chat_served_claim_window_evidence import (
    CLAIM_WINDOW_SIDE_STREAMS,
    MAX_PRODUCTION_WINDOW_RECORDS,
    PRODUCTION_WINDOW_VERIFICATION_SCHEMA,
    ProductionWindowVerification,
    derive_enabled_sample_sequence_digest,
    new_claim_window_final_boundary,
    new_claim_window_start_boundary,
    new_clean_shutdown_marker_v1,
    new_enabled_state_sample,
    new_receipt_index_entry,
    new_served_point_observation,
)
from waggledance.core.magma.chat_served_window_registry import (
    ChatServedWindowRegistry,
    RegistryVerificationApproval,
)
from waggledance.core.magma.chat_served_ledger import (
    GENESIS_PREV_HASH,
    new_receipt_terminal,
    new_served_pending,
)
from waggledance.core.magma.chat_served_metadata import WORLD_SNAPSHOT_NA_MARKER
from waggledance.core.magma.chat_served_receipt import (
    build_chat_served_summary,
    write_chat_served_receipt_bundle,
)
from waggledance.core.magma.chat_served_runtime_window import (
    _write_clean_marker_last,
)

_WINDOW = "window:cli"
_SOURCE_HEAD = "a" * 40
_TS_0 = "2026-07-27T00:00:00Z"
_TS_1 = "2026-07-27T00:00:01Z"
_TS_2 = "2026-07-27T00:00:02Z"
_TS_3 = "2026-07-27T00:00:03Z"


class _SpoofedString(str):
    def __eq__(self, _other):
        return True

    def __ne__(self, _other):
        return False


def _side_offsets(value: int) -> dict[str, int]:
    return {name: value for name in CLAIM_WINDOW_SIDE_STREAMS}


def _write_fixture(tmp_path):
    producer_root = tmp_path / "producer"
    receipt_root = producer_root / "receipts"
    receipt_root.mkdir(parents=True)
    query = "cli query"
    summary = build_chat_served_summary(
        query=query,
        response="cli response",
        route_type="solver",
        source="solver",
        confidence=1.0,
        latency_ms=1.0,
        cached=False,
        round_table=False,
        agent_id=None,
        language="en",
        profile="HOME",
        world_snapshot_ref=WORLD_SNAPSHOT_NA_MARKER,
        route_stage_trace=[
            {
                "stage": "route_selection",
                "route_type": "solver",
                "solver_intent": "chat",
            },
            {
                "stage": "deterministic_solver",
                "intent": "chat",
                "answered": True,
            },
        ],
    )
    bundle_report = write_chat_served_receipt_bundle(
        out_dir=receipt_root / "served-q1",
        summary_payload=summary,
        now_utc=datetime(2026, 7, 27, tzinfo=timezone.utc),
        verify_manifest=verify_manifest,
        ordinal=1,
    )
    receipt = bundle_report["receipt"]
    receipt_ref = sha256_digest(receipt)
    metadata = {
        "source": "solver",
        "route_type": "solver",
        "language": "en",
        "profile": "HOME",
        "world_snapshot_ref": WORLD_SNAPSHOT_NA_MARKER,
        "query_digest": canonical_query_digest(query),
        "normalization_version": NORMALIZATION_VERSION,
    }
    pending = new_served_pending("q1", GENESIS_PREV_HASH, _TS_0, metadata)
    terminal = new_receipt_terminal(
        "q1",
        pending["entry_hash"],
        _TS_1,
        receipt_ref,
    )
    samples = [
        new_enabled_state_sample(
            window_id=_WINDOW,
            enabled=True,
            sample_kind="start",
            ts_utc=_TS_0,
        ),
        new_enabled_state_sample(
            window_id=_WINDOW,
            enabled=True,
            sample_kind="end",
            ts_utc=_TS_1,
        ),
    ]
    observations = [
        new_served_point_observation(
            point=point,
            wired=True,
            ts_utc=_TS_1,
            window_id=_WINDOW,
        )
        for point in sorted(REQUIRED_CHAT_SERVED_POINTS)
    ]
    final_offsets = _side_offsets(0)
    final_offsets.update(
        {
            "enabled_state_samples": len(samples),
            "receipt_index": 1,
            "served_point_observations": len(observations),
        }
    )
    sequence_digest = derive_enabled_sample_sequence_digest(
        samples,
        window_id=_WINDOW,
    )
    assert sequence_digest is not None
    start = new_claim_window_start_boundary(
        window_id=_WINDOW,
        start_ledger_head=GENESIS_PREV_HASH,
        start_ledger_offset=0,
        side_stream_offsets=_side_offsets(0),
        source_head=_SOURCE_HEAD,
        start_enabled_sample_digest=samples[0]["sample_hash"],
        max_enabled_sample_gap_seconds=2,
        ts_utc=_TS_0,
    )
    final = new_claim_window_final_boundary(
        window_id=_WINDOW,
        start_boundary_digest=start["boundary_hash"],
        final_ledger_head=terminal["entry_hash"],
        final_ledger_offset=2,
        side_stream_offsets=final_offsets,
        end_enabled_sample_digest=samples[-1]["sample_hash"],
        enabled_samples_count=2,
        enabled_sample_sequence_digest=sequence_digest,
        ts_utc=_TS_2,
    )
    marker = new_clean_shutdown_marker_v1(
        window_id=_WINDOW,
        start_boundary_digest=start["boundary_hash"],
        final_boundary_digest=final["boundary_hash"],
        final_ledger_head=final["final_ledger_head"],
        end_enabled_sample_digest=final["end_enabled_sample_digest"],
        ts_utc=_TS_3,
    )
    envelope = {
        "schema_version": INPUT_SCHEMA,
        "start_boundary": start,
        "final_boundary": final,
        "clean_shutdown_marker": marker,
        "ledger_entries": [pending, terminal],
        "enabled_samples": samples,
        "pending_failures": [],
        "receipt_index": [
            new_receipt_index_entry(
                window_id=_WINDOW,
                served_id="q1",
                receipt_ref=receipt_ref,
                manifest_ref="served-q1/manifest.json",
            )
        ],
        "served_point_observations": observations,
    }
    evidence_path = producer_root / "evidence" / "evidence.json"
    evidence_path.parent.mkdir()
    evidence_path.write_text(json.dumps(envelope), encoding="utf-8")
    marker_path = evidence_path.parent / "clean-shutdown.json"
    marker_path.write_bytes(canonical_json_bytes(marker) + b"\n")
    return receipt_root, evidence_path, envelope


def _tree_hashes(root):
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _registry_path(tmp_path):
    return tmp_path / "receiver" / "verified-windows.jsonl"


def test_receiver_accepts_runtime_generated_clean_marker_exact_bytes(
    tmp_path,
) -> None:
    _receipt_root, _evidence_path, envelope = _write_fixture(tmp_path)
    marker = envelope["clean_shutdown_marker"]
    marker_path = tmp_path / "runtime" / "clean-shutdown.json"

    _write_clean_marker_last(marker_path, marker)

    assert marker_path.read_bytes() == canonical_json_bytes(marker) + b"\n"
    assert (
        verifier_cli._read_clean_shutdown_marker(marker_path.resolve())
        == marker
    )


def _reserve_registry(
    *,
    registry_path,
    receipt_root,
    envelope,
    marker_path=None,
    expected_window=_WINDOW,
    expected_source=_SOURCE_HEAD,
):
    if marker_path is None:
        marker_path = receipt_root.parent / "evidence" / "clean-shutdown.json"
    binding = verifier_cli._registry_binding(
        envelope,
        expected_window_id=expected_window,
        expected_source_head=expected_source,
        clean_shutdown_marker_path=marker_path,
    )
    pre_marker_envelope = {
        **envelope,
        "clean_shutdown_marker": None,
    }
    registry = ChatServedWindowRegistry(registry_path)

    def verify_pre_marker(previously_verified_window_ids):
        verdict = verifier_cli._verify(
            pre_marker_envelope,
            receipt_root,
            expected_window_id=expected_window,
            expected_source_head=expected_source,
            previously_verified_window_ids=previously_verified_window_ids,
        )
        return RegistryVerificationApproval(binding=binding, verdict=verdict)

    registry.reserve_after_verification(binding, verify_pre_marker)
    return registry, binding


def _cli_args(
    evidence_path,
    receipt_root,
    *extra,
    registry_path=None,
    marker_path=None,
    expected_window=_WINDOW,
    expected_source=_SOURCE_HEAD,
):
    if registry_path is None:
        registry_path = _registry_path(evidence_path.parents[2])
    if marker_path is None:
        marker_path = evidence_path.parent / "clean-shutdown.json"
    return [
        "--evidence",
        str(evidence_path),
        "--receipt-root",
        str(receipt_root),
        "--clean-shutdown-marker",
        str(marker_path),
        "--verified-window-registry",
        str(registry_path),
        "--expected-window-id",
        expected_window,
        "--expected-source-head",
        expected_source,
        *extra,
    ]


def test_parser_requires_explicit_registry_and_rejects_legacy_flag(
    tmp_path,
    capsys,
) -> None:
    receipt_root, evidence_path, _envelope = _write_fixture(tmp_path)
    args = _cli_args(evidence_path, receipt_root)
    registry_index = args.index("--verified-window-registry")
    without_registry = [
        *args[:registry_index],
        *args[registry_index + 2 :],
    ]

    with pytest.raises(SystemExit) as missing:
        verifier_cli.build_parser().parse_args(without_registry)
    assert missing.value.code == 2
    capsys.readouterr()

    marker_index = args.index("--clean-shutdown-marker")
    without_marker = [
        *args[:marker_index],
        *args[marker_index + 2 :],
    ]
    with pytest.raises(SystemExit) as missing_marker:
        verifier_cli.build_parser().parse_args(without_marker)
    assert missing_marker.value.code == 2
    capsys.readouterr()

    with pytest.raises(SystemExit) as legacy:
        verifier_cli.build_parser().parse_args(
            [
                *args,
                "--previously-verified-window-id",
                _WINDOW,
            ]
        )
    assert legacy.value.code == 2


def test_cli_rejects_relative_registry_path_with_path_free_json(
    tmp_path,
    capsys,
) -> None:
    receipt_root, evidence_path, _envelope = _write_fixture(tmp_path)

    exit_code = main(
        _cli_args(
            evidence_path,
            receipt_root,
            registry_path="relative-registry.jsonl",
        )
    )
    report = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert report["reason"] == "window_registry_path_invalid"
    assert report["measurement_only"] is True
    assert report["claim_safe_count"] == 0
    assert str(tmp_path) not in json.dumps(report)


def test_cli_rejects_relative_clean_marker_path_with_path_free_json(
    tmp_path,
    capsys,
) -> None:
    receipt_root, evidence_path, _envelope = _write_fixture(tmp_path)

    exit_code = main(
        _cli_args(
            evidence_path,
            receipt_root,
            marker_path="relative-clean-marker.json",
        )
    )
    report = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert report["reason"] == "window_registry_path_invalid"
    assert report["measurement_only"] is True
    assert report["claim_safe_count"] == 0
    assert str(tmp_path) not in json.dumps(report)


def test_cli_fresh_registry_cannot_create_its_own_reservation(
    tmp_path,
    capsys,
) -> None:
    receipt_root, evidence_path, _envelope = _write_fixture(tmp_path)
    registry_path = _registry_path(tmp_path)
    receipts_before = _tree_hashes(receipt_root)

    exit_code = main(
        _cli_args(
            evidence_path,
            receipt_root,
            registry_path=registry_path,
        )
    )
    report = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert report["reason"] == "window_registry_reservation_missing"
    assert not registry_path.exists()
    assert _tree_hashes(receipt_root) == receipts_before


def test_cli_rejects_registry_inside_producer_receipt_root(
    tmp_path,
    capsys,
) -> None:
    receipt_root, evidence_path, _envelope = _write_fixture(tmp_path)
    registry_path = receipt_root / "receiver-registry.jsonl"

    exit_code = main(
        _cli_args(
            evidence_path,
            receipt_root,
            registry_path=registry_path,
        )
    )
    report = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert report["reason"] == "window_registry_path_invalid"
    assert not registry_path.exists()
    assert str(tmp_path) not in json.dumps(report)


def test_cli_rejects_registry_inside_producer_evidence_namespace(
    tmp_path,
    capsys,
) -> None:
    receipt_root, evidence_path, _envelope = _write_fixture(tmp_path)
    registry_path = evidence_path.parent / "receiver-registry.jsonl"

    exit_code = main(
        _cli_args(
            evidence_path,
            receipt_root,
            registry_path=registry_path,
        )
    )
    report = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert report["reason"] == "window_registry_path_invalid"
    assert not registry_path.exists()
    assert str(tmp_path) not in json.dumps(report)


def test_cli_rejects_producer_paths_inside_registry_namespace(
    tmp_path,
    capsys,
) -> None:
    receipt_root, evidence_path, _envelope = _write_fixture(tmp_path)
    registry_path = evidence_path.parents[1] / "receiver-registry.jsonl"

    exit_code = main(
        _cli_args(
            evidence_path,
            receipt_root,
            registry_path=registry_path,
        )
    )
    report = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert report["reason"] == "window_registry_path_invalid"
    assert not registry_path.exists()
    assert str(tmp_path) not in json.dumps(report)


def test_cli_rejects_clean_marker_inside_registry_namespace(
    tmp_path,
    capsys,
) -> None:
    receipt_root, evidence_path, envelope = _write_fixture(tmp_path)
    registry_path = _registry_path(tmp_path)
    marker_path = registry_path.parent / "producer-clean-marker.json"
    marker_path.parent.mkdir(parents=True)
    marker_path.write_bytes(
        canonical_json_bytes(envelope["clean_shutdown_marker"]) + b"\n"
    )

    exit_code = main(
        _cli_args(
            evidence_path,
            receipt_root,
            registry_path=registry_path,
            marker_path=marker_path,
        )
    )
    report = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert report["reason"] == "window_registry_path_invalid"
    assert not registry_path.exists()
    assert str(tmp_path) not in json.dumps(report)


def test_cli_rejects_resolved_registry_alias_into_evidence_namespace(
    tmp_path,
    capsys,
) -> None:
    receipt_root, evidence_path, _envelope = _write_fixture(tmp_path)
    alias = tmp_path / "receiver-alias"
    try:
        os.symlink(evidence_path.parent, alias, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlink unavailable: {type(exc).__name__}")
    registry_path = alias / "receiver-registry.jsonl"

    exit_code = main(
        _cli_args(
            evidence_path,
            receipt_root,
            registry_path=registry_path,
        )
    )
    report = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert report["reason"] == "window_registry_path_invalid"
    assert not registry_path.exists()
    assert str(tmp_path) not in json.dumps(report)


def test_cli_rejects_resolved_evidence_alias_into_registry_namespace(
    tmp_path,
    capsys,
) -> None:
    receipt_root, evidence_path, _envelope = _write_fixture(tmp_path)
    registry_path = _registry_path(tmp_path)
    nested_evidence = registry_path.parent / "producer-evidence.json"
    nested_evidence.parent.mkdir(parents=True)
    nested_evidence.write_bytes(evidence_path.read_bytes())
    alias = evidence_path.parent / "evidence-alias.json"
    try:
        os.symlink(nested_evidence, alias)
    except OSError as exc:
        pytest.skip(f"file symlink unavailable: {type(exc).__name__}")

    exit_code = main(
        _cli_args(
            alias,
            receipt_root,
            registry_path=registry_path,
        )
    )
    report = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert report["reason"] == "window_registry_path_invalid"
    assert not registry_path.exists()
    assert str(tmp_path) not in json.dumps(report)


def test_cli_verifies_final_window_with_stable_non_authorizing_json(
    tmp_path,
    capsys,
) -> None:
    receipt_root, evidence_path, envelope = _write_fixture(tmp_path)
    registry_path = _registry_path(tmp_path)
    registry, _binding = _reserve_registry(
        registry_path=registry_path,
        receipt_root=receipt_root,
        envelope=envelope,
    )
    evidence_before = evidence_path.read_bytes()
    receipts_before = _tree_hashes(receipt_root)

    exit_code = main(
        _cli_args(
            evidence_path,
            receipt_root,
            registry_path=registry_path,
        )
    )
    report = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert report["ok"] is True
    assert report["phase"] == "final_verified"
    assert report["marker_verified"] is True
    assert report["measurement_only"] is True
    assert report["claim_safe_count"] == 0
    assert report["served_total"] == 1
    assert report["served_with_receipt_total"] == 1
    assert report["served_with_receipt_ratio"] == 1.0
    assert report["solver_first_served_total"] == 1
    assert report["solver_first_served_ratio"] == 1.0
    assert report["authority"] == "measurement_only"
    assert (
        report["schema_version"]
        == "magma.chat_served_production_window_verification.v2"
    )
    assert "eligible" not in report
    assert "clean_shutdown" not in report
    assert str(tmp_path) not in json.dumps(report)
    assert evidence_path.read_bytes() == evidence_before
    assert _tree_hashes(receipt_root) == receipts_before
    snapshot = registry.snapshot()
    assert snapshot.consumed_window_ids == (_WINDOW,)
    assert snapshot.verified_window_ids == (_WINDOW,)
    assert len(snapshot.records) == 2


@pytest.mark.parametrize("marker_state", ["missing", "pending_only"])
def test_cli_requires_final_durable_marker_not_pending_artifact(
    tmp_path,
    capsys,
    monkeypatch,
    marker_state,
) -> None:
    receipt_root, evidence_path, envelope = _write_fixture(tmp_path)
    marker_path = evidence_path.parent / "clean-shutdown.json"
    registry_path = _registry_path(tmp_path)
    registry, _binding = _reserve_registry(
        registry_path=registry_path,
        receipt_root=receipt_root,
        envelope=envelope,
        marker_path=marker_path,
    )
    reservation_bytes = registry_path.read_bytes()
    if marker_state == "pending_only":
        pending = marker_path.with_name(
            f".{marker_path.name}.test.pending"
        )
        marker_path.replace(pending)
        assert pending.exists()
    else:
        marker_path.unlink()
    monkeypatch.setattr(
        verifier_cli,
        "_verify",
        lambda *_args, **_kwargs: pytest.fail(
            "core verifier called before durable marker validation"
        ),
    )

    exit_code = main(
        _cli_args(
            evidence_path,
            receipt_root,
            registry_path=registry_path,
            marker_path=marker_path,
        )
    )
    report = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert report["reason"] == "clean_shutdown_marker_invalid"
    assert registry_path.read_bytes() == reservation_bytes
    assert registry.snapshot().verified_window_ids == ()


@pytest.mark.parametrize(
    "encoding",
    ["missing_lf", "pretty", "duplicate_key", "nonfinite"],
)
def test_cli_rejects_noncanonical_clean_marker(
    tmp_path,
    capsys,
    encoding,
) -> None:
    receipt_root, evidence_path, envelope = _write_fixture(tmp_path)
    marker_path = evidence_path.parent / "clean-shutdown.json"
    registry_path = _registry_path(tmp_path)
    registry, _binding = _reserve_registry(
        registry_path=registry_path,
        receipt_root=receipt_root,
        envelope=envelope,
        marker_path=marker_path,
    )
    reservation_bytes = registry_path.read_bytes()
    if encoding == "missing_lf":
        raw = canonical_json_bytes(envelope["clean_shutdown_marker"])
    elif encoding == "pretty":
        raw = (
            json.dumps(
                envelope["clean_shutdown_marker"],
                indent=2,
                sort_keys=True,
            ).encode("utf-8")
            + b"\n"
        )
    elif encoding == "duplicate_key":
        raw = b'{"schema_version":"x","schema_version":"y"}\n'
    else:
        raw = b'{"marker_hash":NaN}\n'
    marker_path.write_bytes(raw)

    exit_code = main(
        _cli_args(
            evidence_path,
            receipt_root,
            registry_path=registry_path,
            marker_path=marker_path,
        )
    )
    report = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert report["reason"] == "clean_shutdown_marker_invalid"
    assert registry_path.read_bytes() == reservation_bytes
    assert registry.snapshot().verified_window_ids == ()


def test_cli_bounds_clean_marker_before_json_parse(
    tmp_path,
    capsys,
    monkeypatch,
) -> None:
    receipt_root, evidence_path, envelope = _write_fixture(tmp_path)
    marker_path = evidence_path.parent / "clean-shutdown.json"
    registry_path = _registry_path(tmp_path)
    registry, _binding = _reserve_registry(
        registry_path=registry_path,
        receipt_root=receipt_root,
        envelope=envelope,
        marker_path=marker_path,
    )
    reservation_bytes = registry_path.read_bytes()
    monkeypatch.setattr(verifier_cli, "MAX_CLEAN_MARKER_BYTES", 1)

    exit_code = main(
        _cli_args(
            evidence_path,
            receipt_root,
            registry_path=registry_path,
            marker_path=marker_path,
        )
    )
    report = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert report["reason"] == "clean_shutdown_marker_invalid"
    assert registry_path.read_bytes() == reservation_bytes
    assert registry.snapshot().verified_window_ids == ()


def test_cli_rejects_hardlinked_clean_marker(
    tmp_path,
    capsys,
) -> None:
    receipt_root, evidence_path, envelope = _write_fixture(tmp_path)
    marker_path = evidence_path.parent / "clean-shutdown.json"
    registry_path = _registry_path(tmp_path)
    registry, _binding = _reserve_registry(
        registry_path=registry_path,
        receipt_root=receipt_root,
        envelope=envelope,
        marker_path=marker_path,
    )
    reservation_bytes = registry_path.read_bytes()
    outside = tmp_path / "outside-clean-marker.json"
    try:
        os.link(marker_path, outside)
    except OSError as exc:
        pytest.skip(f"hardlink creation unavailable: {type(exc).__name__}")

    exit_code = main(
        _cli_args(
            evidence_path,
            receipt_root,
            registry_path=registry_path,
            marker_path=marker_path,
        )
    )
    report = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert report["reason"] == "clean_shutdown_marker_invalid"
    assert registry_path.read_bytes() == reservation_bytes
    assert registry.snapshot().verified_window_ids == ()


def test_cli_rejects_symlinked_clean_marker(
    tmp_path,
    capsys,
) -> None:
    receipt_root, evidence_path, envelope = _write_fixture(tmp_path)
    marker_path = evidence_path.parent / "clean-shutdown.json"
    registry_path = _registry_path(tmp_path)
    registry, _binding = _reserve_registry(
        registry_path=registry_path,
        receipt_root=receipt_root,
        envelope=envelope,
        marker_path=marker_path,
    )
    reservation_bytes = registry_path.read_bytes()
    real_marker = marker_path.with_name("clean-shutdown-real.json")
    marker_path.replace(real_marker)
    try:
        os.symlink(real_marker, marker_path)
    except OSError as exc:
        pytest.skip(f"file symlink unavailable: {type(exc).__name__}")

    exit_code = main(
        _cli_args(
            evidence_path,
            receipt_root,
            registry_path=registry_path,
            marker_path=marker_path,
        )
    )
    report = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert report["reason"] == "window_registry_binding_mismatch"
    assert registry_path.read_bytes() == reservation_bytes
    assert registry.snapshot().verified_window_ids == ()


def test_cli_rejects_matching_marker_copied_to_different_path(
    tmp_path,
    capsys,
) -> None:
    receipt_root, evidence_path, envelope = _write_fixture(tmp_path)
    marker_path = evidence_path.parent / "clean-shutdown.json"
    registry_path = _registry_path(tmp_path)
    registry, _binding = _reserve_registry(
        registry_path=registry_path,
        receipt_root=receipt_root,
        envelope=envelope,
        marker_path=marker_path,
    )
    reservation_bytes = registry_path.read_bytes()
    copied_marker = marker_path.with_name("copied-clean-shutdown.json")
    copied_marker.write_bytes(marker_path.read_bytes())

    exit_code = main(
        _cli_args(
            evidence_path,
            receipt_root,
            registry_path=registry_path,
            marker_path=copied_marker,
        )
    )
    report = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert report["reason"] == "window_registry_binding_mismatch"
    assert registry_path.read_bytes() == reservation_bytes
    assert registry.snapshot().verified_window_ids == ()


def test_cli_second_run_is_replay_and_does_not_append(
    tmp_path,
    capsys,
) -> None:
    receipt_root, evidence_path, envelope = _write_fixture(tmp_path)
    registry_path = _registry_path(tmp_path)
    _reserve_registry(
        registry_path=registry_path,
        receipt_root=receipt_root,
        envelope=envelope,
    )
    args = _cli_args(
        evidence_path,
        receipt_root,
        registry_path=registry_path,
    )
    assert main(args) == 0
    capsys.readouterr()
    finalized_bytes = registry_path.read_bytes()

    exit_code = main(args)
    report = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert report["reason"] == "window_id_reused"
    assert report["measurement_only"] is True
    assert report["claim_safe_count"] == 0
    assert registry_path.read_bytes() == finalized_bytes


def test_cli_rechecks_exact_evidence_inside_registry_lock(
    tmp_path,
    capsys,
    monkeypatch,
) -> None:
    receipt_root, evidence_path, envelope = _write_fixture(tmp_path)
    registry_path = _registry_path(tmp_path)
    registry, _binding = _reserve_registry(
        registry_path=registry_path,
        receipt_root=receipt_root,
        envelope=envelope,
    )
    reservation_bytes = registry_path.read_bytes()
    real_read_json = verifier_cli._read_json
    reads = 0

    def changing_read(path):
        nonlocal reads
        reads += 1
        value = real_read_json(path)
        if reads == 2:
            value["clean_shutdown_marker"]["marker_hash"] = (
                "sha256:" + "f" * 64
            )
        return value

    monkeypatch.setattr(verifier_cli, "_read_json", changing_read)

    exit_code = main(
        _cli_args(
            evidence_path,
            receipt_root,
            registry_path=registry_path,
        )
    )
    report = json.loads(capsys.readouterr().out)

    assert reads == 2
    assert exit_code == 1
    assert report["reason"] == "window_registry_verification_failed"
    assert registry_path.read_bytes() == reservation_bytes
    assert registry.snapshot().verified_window_ids == ()


def test_cli_revalidates_namespace_inside_registry_lock(
    tmp_path,
    capsys,
    monkeypatch,
) -> None:
    receipt_root, evidence_path, envelope = _write_fixture(tmp_path)
    marker_path = evidence_path.parent / "clean-shutdown.json"
    registry_path = _registry_path(tmp_path)
    registry, _binding = _reserve_registry(
        registry_path=registry_path,
        receipt_root=receipt_root,
        envelope=envelope,
        marker_path=marker_path,
    )
    reservation_bytes = registry_path.read_bytes()
    real_validate = verifier_cli._validate_registry_separation
    validations = 0

    def changed_namespace(*args, **kwargs):
        nonlocal validations
        validations += 1
        if validations == 2:
            raise verifier_cli.WindowRegistryPathError(
                "simulated_namespace_swap"
            )
        return real_validate(*args, **kwargs)

    monkeypatch.setattr(
        verifier_cli,
        "_validate_registry_separation",
        changed_namespace,
    )

    exit_code = main(
        _cli_args(
            evidence_path,
            receipt_root,
            registry_path=registry_path,
            marker_path=marker_path,
        )
    )
    report = json.loads(capsys.readouterr().out)

    assert validations == 2
    assert exit_code == 1
    assert report["reason"] == "window_registry_verification_failed"
    assert registry_path.read_bytes() == reservation_bytes
    assert registry.snapshot().verified_window_ids == ()


def test_cli_rechecks_durable_marker_after_core_verification(
    tmp_path,
    capsys,
    monkeypatch,
) -> None:
    receipt_root, evidence_path, envelope = _write_fixture(tmp_path)
    marker_path = evidence_path.parent / "clean-shutdown.json"
    registry_path = _registry_path(tmp_path)
    registry, _binding = _reserve_registry(
        registry_path=registry_path,
        receipt_root=receipt_root,
        envelope=envelope,
        marker_path=marker_path,
    )
    reservation_bytes = registry_path.read_bytes()
    real_verify = verifier_cli._verify

    def verify_then_replace_marker(*args, **kwargs):
        verdict = real_verify(*args, **kwargs)
        changed = dict(envelope["clean_shutdown_marker"])
        changed["marker_hash"] = "sha256:" + "f" * 64
        marker_path.write_bytes(canonical_json_bytes(changed) + b"\n")
        return verdict

    monkeypatch.setattr(
        verifier_cli,
        "_verify",
        verify_then_replace_marker,
    )

    exit_code = main(
        _cli_args(
            evidence_path,
            receipt_root,
            registry_path=registry_path,
            marker_path=marker_path,
        )
    )
    report = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert (
        report["reason"]
        == "clean_shutdown_marker_changed_during_verification"
    )
    assert registry_path.read_bytes() == reservation_bytes
    assert registry.snapshot().verified_window_ids == ()


@pytest.mark.parametrize(
    "forgery",
    [
        "wrong_phase",
        "wrong_schema",
        "wrong_authority",
        "forged_ratio",
        "bool_total",
        "pending_failures",
        "short_enabled_timeline",
        "wrong_point_count",
        "over_bound_count",
        "extreme_count",
        "spoofed_authority",
        "huge_failure_count",
        "unsafe_failure_contract",
    ],
)
def test_cli_never_reports_success_when_registry_rejects_verdict(
    tmp_path,
    capsys,
    monkeypatch,
    forgery,
) -> None:
    receipt_root, evidence_path, envelope = _write_fixture(tmp_path)
    marker_path = evidence_path.parent / "clean-shutdown.json"
    registry_path = _registry_path(tmp_path)
    registry, _binding = _reserve_registry(
        registry_path=registry_path,
        receipt_root=receipt_root,
        envelope=envelope,
        marker_path=marker_path,
    )
    reservation_bytes = registry_path.read_bytes()
    forged = ProductionWindowVerification(
        True,
        "final_verified",
        None,
        True,
        2,
        2,
        0,
        1,
        len(REQUIRED_CHAT_SERVED_POINTS),
        1,
        served_total=1,
        served_with_receipt_total=1,
        served_with_receipt_ratio=1.0,
        solver_first_served_total=1,
        solver_first_served_ratio=1.0,
    )
    if forgery == "wrong_phase":
        forged = forged._replace(
            phase="pre_marker_verified",
            marker_verified=False,
        )
    elif forgery == "wrong_schema":
        forged = forged._replace(
            schema_version=(
                PRODUCTION_WINDOW_VERIFICATION_SCHEMA + ".forged"
            )
        )
    elif forgery == "wrong_authority":
        forged = forged._replace(authority="runtime")
    elif forgery == "forged_ratio":
        forged = forged._replace(solver_first_served_ratio=0.5)
    elif forgery == "bool_total":
        forged = forged._replace(served_total=True)
    elif forgery == "pending_failures":
        forged = forged._replace(pending_failures=1)
    elif forgery == "short_enabled_timeline":
        forged = forged._replace(enabled_samples=1)
    elif forgery == "wrong_point_count":
        forged = forged._replace(served_point_observations=0)
    elif forgery == "over_bound_count":
        forged = forged._replace(
            enabled_samples=MAX_PRODUCTION_WINDOW_RECORDS + 1
        )
    elif forgery == "extreme_count":
        forged = forged._replace(enabled_samples=10**5000)
    elif forgery == "spoofed_authority":
        forged = forged._replace(authority=_SpoofedString("runtime"))
    elif forgery == "huge_failure_count":
        forged = forged._replace(
            ok=False,
            phase="final_rejected",
            reason="hostile_count",
            marker_verified=False,
            enabled_samples=10**5000,
        )
    elif forgery == "unsafe_failure_contract":
        forged = forged._replace(
            ok=False,
            phase="final_rejected",
            reason=str(tmp_path),
            marker_verified=False,
            measurement_only=False,
        )
    else:  # pragma: no cover - parametrization is a closed set
        raise AssertionError(f"unknown forgery: {forgery}")
    monkeypatch.setattr(
        verifier_cli,
        "_verify",
        lambda *_args, **_kwargs: forged,
    )

    exit_code = main(
        _cli_args(
            evidence_path,
            receipt_root,
            registry_path=registry_path,
            marker_path=marker_path,
        )
    )
    report = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert report["ok"] is False
    assert report["reason"] == "window_registry_verification_failed"
    assert str(tmp_path) not in json.dumps(report)
    assert registry_path.read_bytes() == reservation_bytes
    assert registry.snapshot().verified_window_ids == ()


def test_cli_exits_nonzero_on_durable_marker_mismatch(
    tmp_path,
    capsys,
) -> None:
    receipt_root, evidence_path, envelope = _write_fixture(tmp_path)
    envelope["clean_shutdown_marker"]["marker_hash"] = "sha256:" + "f" * 64
    evidence_path.write_text(json.dumps(envelope), encoding="utf-8")
    registry_path = _registry_path(tmp_path)
    registry, _binding = _reserve_registry(
        registry_path=registry_path,
        receipt_root=receipt_root,
        envelope=envelope,
    )
    reservation_bytes = registry_path.read_bytes()

    exit_code = main(
        _cli_args(
            evidence_path,
            receipt_root,
            registry_path=registry_path,
        )
    )
    report = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert report["ok"] is False
    assert report["reason"] == "clean_shutdown_marker_mismatch"
    assert report["measurement_only"] is True
    assert report["claim_safe_count"] == 0
    assert registry_path.read_bytes() == reservation_bytes
    snapshot = registry.snapshot()
    assert snapshot.consumed_window_ids == (_WINDOW,)
    assert snapshot.verified_window_ids == ()


def test_cli_uses_receiver_pinned_context_not_producer_envelope(
    tmp_path,
    capsys,
) -> None:
    receipt_root, evidence_path, envelope = _write_fixture(tmp_path)
    registry_path = _registry_path(tmp_path)
    registry, _binding = _reserve_registry(
        registry_path=registry_path,
        receipt_root=receipt_root,
        envelope=envelope,
    )
    reservation_bytes = registry_path.read_bytes()

    stale_exit = main(
        _cli_args(
            evidence_path,
            receipt_root,
            registry_path=registry_path,
            expected_source="b" * 40,
        )
    )
    stale = json.loads(capsys.readouterr().out)

    assert stale_exit == 1
    assert stale["reason"] == "window_registry_binding_mismatch"
    assert registry_path.read_bytes() == reservation_bytes
    assert registry.snapshot().verified_window_ids == ()


def test_cli_rejects_unknown_input_fields_with_fixed_shape(tmp_path, capsys) -> None:
    receipt_root, evidence_path, envelope = _write_fixture(tmp_path)
    envelope["producer_ok"] = True
    evidence_path.write_text(json.dumps(envelope), encoding="utf-8")

    exit_code = main(_cli_args(evidence_path, receipt_root))
    report = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert report["reason"] == "cli_input_invalid"
    assert set(report) == {
        "ok",
        "phase",
        "reason",
        "marker_verified",
        "ledger_entries",
        "enabled_samples",
        "pending_failures",
        "receipt_index_entries",
        "served_point_observations",
        "receipt_terminals",
        "measurement_only",
        "claim_safe_count",
        "schema_version",
        "served_total",
        "served_with_receipt_total",
        "served_with_receipt_ratio",
        "solver_first_served_total",
        "solver_first_served_ratio",
        "authority",
    }
    assert report["served_total"] == 0
    assert report["served_with_receipt_total"] == 0
    assert report["served_with_receipt_ratio"] is None
    assert report["solver_first_served_total"] == 0
    assert report["solver_first_served_ratio"] is None
    assert report["authority"] == "measurement_only"
    assert (
        report["schema_version"]
        == "magma.chat_served_production_window_verification.v2"
    )


def test_cli_rejects_traversal_manifest_reference(tmp_path, capsys) -> None:
    receipt_root, evidence_path, envelope = _write_fixture(tmp_path)
    registry_path = _registry_path(tmp_path)
    registry, _binding = _reserve_registry(
        registry_path=registry_path,
        receipt_root=receipt_root,
        envelope=envelope,
    )
    reservation_bytes = registry_path.read_bytes()
    row = dict(envelope["receipt_index"][0])
    row["manifest_ref"] = "../served-q1/manifest.json"
    envelope["receipt_index"] = [row]
    evidence_path.write_text(json.dumps(envelope), encoding="utf-8")

    exit_code = main(
        _cli_args(
            evidence_path,
            receipt_root,
            registry_path=registry_path,
        )
    )
    report = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert report["reason"] == "window_registry_binding_mismatch"
    assert str(tmp_path) not in json.dumps(report)
    assert registry_path.read_bytes() == reservation_bytes
    assert registry.snapshot().verified_window_ids == ()


def test_cli_rejects_symlinked_receipt_bundle(tmp_path, capsys) -> None:
    receipt_root, evidence_path, envelope = _write_fixture(tmp_path)
    registry_path = _registry_path(tmp_path)
    registry, _binding = _reserve_registry(
        registry_path=registry_path,
        receipt_root=receipt_root,
        envelope=envelope,
    )
    reservation_bytes = registry_path.read_bytes()
    original = receipt_root / "served-q1"
    moved = receipt_root / "served-q1-real"
    original.rename(moved)
    try:
        os.symlink(moved, original, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlink unavailable: {type(exc).__name__}")

    exit_code = main(
        _cli_args(
            evidence_path,
            receipt_root,
            registry_path=registry_path,
        )
    )
    report = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert report["reason"] == "receipt_bundle_verification_failed"
    assert registry_path.read_bytes() == reservation_bytes
    assert registry.snapshot().verified_window_ids == ()


def test_cli_rejects_detected_reparse_component(
    tmp_path,
    capsys,
    monkeypatch,
) -> None:
    receipt_root, evidence_path, envelope = _write_fixture(tmp_path)
    registry_path = _registry_path(tmp_path)
    registry, _binding = _reserve_registry(
        registry_path=registry_path,
        receipt_root=receipt_root,
        envelope=envelope,
    )
    reservation_bytes = registry_path.read_bytes()
    real_detector = verifier_cli._path_is_link

    def detected(path):
        return path.name == "served-q1" or real_detector(path)

    monkeypatch.setattr(verifier_cli, "_path_is_link", detected)
    exit_code = main(
        _cli_args(
            evidence_path,
            receipt_root,
            registry_path=registry_path,
        )
    )
    report = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert report["reason"] == "receipt_bundle_verification_failed"
    assert registry_path.read_bytes() == reservation_bytes
    assert registry.snapshot().verified_window_ids == ()


def test_cli_bounds_evidence_file_before_json_parse(
    tmp_path,
    capsys,
    monkeypatch,
) -> None:
    receipt_root, evidence_path, _envelope = _write_fixture(tmp_path)
    monkeypatch.setattr(verifier_cli, "MAX_JSON_INPUT_BYTES", 1)

    exit_code = main(_cli_args(evidence_path, receipt_root))
    report = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert report["reason"] == "cli_input_invalid"

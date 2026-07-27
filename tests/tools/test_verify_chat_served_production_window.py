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
from waggledance.core.magma.canonical import sha256_digest
from waggledance.core.magma.chat_query_route_evidence import (
    NORMALIZATION_VERSION,
    canonical_query_digest,
)
from waggledance.core.magma.chat_served_accounting import (
    REQUIRED_CHAT_SERVED_POINTS,
)
from waggledance.core.magma.chat_served_claim_window_evidence import (
    CLAIM_WINDOW_SIDE_STREAMS,
    derive_enabled_sample_sequence_digest,
    new_claim_window_final_boundary,
    new_claim_window_start_boundary,
    new_clean_shutdown_marker_v1,
    new_enabled_state_sample,
    new_receipt_index_entry,
    new_served_point_observation,
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

_WINDOW = "window:cli"
_SOURCE_HEAD = "a" * 40
_TS_0 = "2026-07-27T00:00:00Z"
_TS_1 = "2026-07-27T00:00:01Z"
_TS_2 = "2026-07-27T00:00:02Z"
_TS_3 = "2026-07-27T00:00:03Z"


def _side_offsets(value: int) -> dict[str, int]:
    return {name: value for name in CLAIM_WINDOW_SIDE_STREAMS}


def _write_fixture(tmp_path):
    receipt_root = tmp_path / "receipts"
    receipt_root.mkdir()
    query = "cli query"
    summary = build_chat_served_summary(
        query=query,
        response="cli response",
        route_type="solver",
        source="chat",
        confidence=1.0,
        latency_ms=1.0,
        cached=False,
        round_table=False,
        agent_id=None,
        language="en",
        profile="HOME",
        world_snapshot_ref=WORLD_SNAPSHOT_NA_MARKER,
        route_stage_trace=[],
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
        "source": "chat",
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
    evidence_path = tmp_path / "evidence.json"
    evidence_path.write_text(json.dumps(envelope), encoding="utf-8")
    return receipt_root, evidence_path, envelope


def _tree_hashes(root):
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _cli_args(
    evidence_path,
    receipt_root,
    *extra,
    expected_window=_WINDOW,
    expected_source=_SOURCE_HEAD,
):
    return [
        "--evidence",
        str(evidence_path),
        "--receipt-root",
        str(receipt_root),
        "--expected-window-id",
        expected_window,
        "--expected-source-head",
        expected_source,
        *extra,
    ]


def test_cli_verifies_final_window_with_stable_non_authorizing_json(
    tmp_path,
    capsys,
) -> None:
    receipt_root, evidence_path, _envelope = _write_fixture(tmp_path)
    before = _tree_hashes(tmp_path)

    exit_code = main(_cli_args(evidence_path, receipt_root))
    report = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert report["ok"] is True
    assert report["phase"] == "final_verified"
    assert report["marker_verified"] is True
    assert report["measurement_only"] is True
    assert report["claim_safe_count"] == 0
    assert "eligible" not in report
    assert "clean_shutdown" not in report
    assert str(tmp_path) not in json.dumps(report)
    assert _tree_hashes(tmp_path) == before


def test_cli_exits_nonzero_on_marker_mismatch(tmp_path, capsys) -> None:
    receipt_root, evidence_path, envelope = _write_fixture(tmp_path)
    envelope["clean_shutdown_marker"]["marker_hash"] = "sha256:" + "f" * 64
    evidence_path.write_text(json.dumps(envelope), encoding="utf-8")

    exit_code = main(_cli_args(evidence_path, receipt_root))
    report = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert report["ok"] is False
    assert report["reason"] == "clean_marker_invalid"
    assert report["measurement_only"] is True
    assert report["claim_safe_count"] == 0


def test_cli_uses_receiver_pinned_context_not_producer_envelope(
    tmp_path,
    capsys,
) -> None:
    receipt_root, evidence_path, _envelope = _write_fixture(tmp_path)

    stale_exit = main(
        _cli_args(
            evidence_path,
            receipt_root,
            expected_source="b" * 40,
        )
    )
    stale = json.loads(capsys.readouterr().out)
    replay_exit = main(
        _cli_args(
            evidence_path,
            receipt_root,
            "--previously-verified-window-id",
            _WINDOW,
        )
    )
    replay = json.loads(capsys.readouterr().out)

    assert stale_exit == 1
    assert stale["reason"] == "source_head_mismatch"
    assert replay_exit == 1
    assert replay["reason"] == "window_id_reused"


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
    }


def test_cli_rejects_traversal_manifest_reference(tmp_path, capsys) -> None:
    receipt_root, evidence_path, envelope = _write_fixture(tmp_path)
    row = dict(envelope["receipt_index"][0])
    row["manifest_ref"] = "../served-q1/manifest.json"
    envelope["receipt_index"] = [row]
    evidence_path.write_text(json.dumps(envelope), encoding="utf-8")

    exit_code = main(_cli_args(evidence_path, receipt_root))
    report = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert report["reason"] == "receipt_index_entry_invalid"
    assert str(tmp_path) not in json.dumps(report)


def test_cli_rejects_symlinked_receipt_bundle(tmp_path, capsys) -> None:
    receipt_root, evidence_path, envelope = _write_fixture(tmp_path)
    link = receipt_root / "linked"
    try:
        os.symlink(receipt_root / "served-q1", link, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlink unavailable: {type(exc).__name__}")
    original = envelope["receipt_index"][0]
    envelope["receipt_index"] = [
        new_receipt_index_entry(
            window_id=_WINDOW,
            served_id=original["served_id"],
            receipt_ref=original["receipt_ref"],
            manifest_ref="linked/manifest.json",
        )
    ]
    evidence_path.write_text(json.dumps(envelope), encoding="utf-8")

    exit_code = main(_cli_args(evidence_path, receipt_root))
    report = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert report["reason"] == "receipt_bundle_verification_failed"


def test_cli_rejects_detected_reparse_component(
    tmp_path,
    capsys,
    monkeypatch,
) -> None:
    receipt_root, evidence_path, _envelope = _write_fixture(tmp_path)
    real_detector = verifier_cli._path_is_link

    def detected(path):
        return path.name == "served-q1" or real_detector(path)

    monkeypatch.setattr(verifier_cli, "_path_is_link", detected)
    exit_code = main(_cli_args(evidence_path, receipt_root))
    report = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert report["reason"] == "receipt_bundle_verification_failed"


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

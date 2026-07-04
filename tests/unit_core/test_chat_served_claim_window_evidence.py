# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

import json

from waggledance.core.magma import chat_served_ledger as L
from waggledance.core.magma.chat_served_accounting import (
    REQUIRED_CHAT_SERVED_POINTS,
)
from waggledance.core.magma.chat_served_claim_window_evidence import (
    build_claim_window_evidence,
    claim_window_from_evidence,
    derive_enabled_across_window,
    derive_instrumented_served_points,
    new_enabled_state_sample,
    new_served_point_observation,
    read_clean_shutdown_marker,
    read_latest_head_anchor,
    write_clean_shutdown_marker,
    write_head_anchor_checkpoint,
)

_TS = "2026-07-04T16:00:00Z"
_META = {"source": "chat", "route_type": "solver"}
_DIGEST = "sha256:" + "cd" * 32
_WINDOW = "window:phase2e"


def _chain(specs):
    entries = []
    prev = L.GENESIS_PREV_HASH
    for kind, served_id in specs:
        if kind == "pending":
            entry = L.new_served_pending(served_id, prev, _TS, _META)
        elif kind == "receipt":
            entry = L.new_receipt_terminal(served_id, prev, _TS, _DIGEST)
        else:
            entry = L.new_gap_terminal(served_id, prev, _TS, "receipt_build_failed")
        entries.append(entry)
        prev = entry["entry_hash"]
    return entries


def _write_ledger(path, entries) -> None:
    for entry in entries:
        L.append_entry(str(path), entry, fsync=False)


def _good_observations():
    return [
        new_served_point_observation(point=point, wired=True, ts_utc=_TS)
        for point in sorted(REQUIRED_CHAT_SERVED_POINTS)
    ]


def test_claim_window_evidence_happy_path_uses_independent_anchor_store(tmp_path) -> None:
    ledger_path = tmp_path / "chat-served-ledger.jsonl"
    anchor_path = tmp_path / "anchors" / "head-anchors.jsonl"
    marker_path = tmp_path / "markers" / "clean.json"
    entries = _chain([("pending", "q1"), ("receipt", "q1")])
    _write_ledger(ledger_path, entries)

    anchor_hash = write_head_anchor_checkpoint(
        str(anchor_path),
        str(ledger_path),
        window_id=_WINDOW,
        ts_utc=_TS,
        fsync=False,
    )
    write_clean_shutdown_marker(
        str(marker_path),
        window_id=_WINDOW,
        ts_utc=_TS,
        fsync=False,
    )

    evidence = build_claim_window_evidence(
        anchor_store_path=str(anchor_path),
        ledger_path=str(ledger_path),
        window_id=_WINDOW,
        enabled_samples=[
            new_enabled_state_sample(window_id=_WINDOW, enabled=True, ts_utc=_TS),
            new_enabled_state_sample(window_id=_WINDOW, enabled=True, ts_utc=_TS),
        ],
        clean_shutdown_marker_path=str(marker_path),
        served_point_observations=_good_observations(),
    )
    report = claim_window_from_evidence(str(ledger_path), evidence)

    assert anchor_hash.startswith("sha256:")
    assert str(ledger_path) not in anchor_path.read_text(encoding="utf-8")
    assert evidence.input_ready is True
    assert evidence.reason is None
    assert evidence.expected_head == L.head_hash(entries)
    assert evidence.enabled_across_window is True
    assert evidence.clean_shutdown is True
    assert evidence.missing_served_points == ()
    assert report.eligible is True
    assert report.reason is None


def test_claim_window_evidence_rejects_self_derived_anchor_path(tmp_path) -> None:
    ledger_path = tmp_path / "same-file.jsonl"
    entries = _chain([("pending", "q1"), ("receipt", "q1")])
    _write_ledger(ledger_path, entries)

    evidence = build_claim_window_evidence(
        anchor_store_path=str(ledger_path),
        ledger_path=str(ledger_path),
        window_id=_WINDOW,
        enabled_samples=[
            new_enabled_state_sample(window_id=_WINDOW, enabled=True, ts_utc=_TS),
        ],
        clean_shutdown_marker_path=str(tmp_path / "missing-clean.json"),
        served_point_observations=_good_observations(),
    )
    report = claim_window_from_evidence(str(ledger_path), evidence)

    assert evidence.input_ready is False
    assert evidence.reason == "head_anchor_not_independent"
    assert evidence.expected_head is None
    assert report.eligible is False
    assert report.reason == "missing_expected_head_anchor"


def test_head_anchor_store_corruption_and_torn_tail_fail_closed(tmp_path) -> None:
    ledger_path = tmp_path / "ledger.jsonl"
    anchor_path = tmp_path / "anchors.jsonl"
    entries = _chain([("pending", "q1"), ("receipt", "q1")])
    _write_ledger(ledger_path, entries)
    write_head_anchor_checkpoint(
        str(anchor_path),
        str(ledger_path),
        window_id=_WINDOW,
        ts_utc=_TS,
        fsync=False,
    )

    with open(anchor_path, "a", encoding="utf-8") as handle:
        handle.write('{"partial":')
    torn = read_latest_head_anchor(
        str(anchor_path),
        str(ledger_path),
        window_id=_WINDOW,
    )

    assert torn.ok is False
    assert torn.torn_tail is True
    assert torn.reason == "head_anchor_store_torn_tail"
    assert torn.expected_head is None

    anchor_path.write_text('{"bad":"entry"}\n{"still":"bad"}\n', encoding="utf-8")
    corrupt = read_latest_head_anchor(
        str(anchor_path),
        str(ledger_path),
        window_id=_WINDOW,
    )

    assert corrupt.ok is False
    assert corrupt.reason is not None
    assert corrupt.reason.startswith("head_anchor_store_invalid:")


def test_enabled_samples_are_nonempty_all_true_and_hash_validated() -> None:
    good = new_enabled_state_sample(window_id=_WINDOW, enabled=True, ts_utc=_TS)
    false_sample = new_enabled_state_sample(
        window_id=_WINDOW,
        enabled=False,
        ts_utc=_TS,
    )
    tampered = dict(good)
    tampered["enabled"] = False

    assert derive_enabled_across_window([good], window_id=_WINDOW) is True
    assert derive_enabled_across_window([], window_id=_WINDOW) is False
    assert derive_enabled_across_window([good, false_sample], window_id=_WINDOW) is False
    assert derive_enabled_across_window([tampered], window_id=_WINDOW) is False


def test_clean_shutdown_marker_is_hash_validated_and_window_bound(tmp_path) -> None:
    marker_path = tmp_path / "clean.json"
    write_clean_shutdown_marker(
        str(marker_path),
        window_id=_WINDOW,
        ts_utc=_TS,
        fsync=False,
    )

    assert read_clean_shutdown_marker(str(marker_path), window_id=_WINDOW) is True
    assert read_clean_shutdown_marker(str(marker_path), window_id="other") is False

    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    marker["status"] = "clean-but-tampered"
    marker_path.write_text(json.dumps(marker), encoding="utf-8")

    assert read_clean_shutdown_marker(str(marker_path), window_id=_WINDOW) is False


def test_served_point_observations_are_rederived_from_valid_hashed_records() -> None:
    observations = _good_observations()
    assert derive_instrumented_served_points(observations) == tuple(
        sorted(REQUIRED_CHAT_SERVED_POINTS)
    )

    tampered = dict(observations[0])
    tampered["wired"] = False
    points = derive_instrumented_served_points([tampered, *observations[1:]])

    assert observations[0]["point"] not in points
    assert len(points) == len(REQUIRED_CHAT_SERVED_POINTS) - 1


def test_evidence_reason_names_first_missing_outer_signal(tmp_path) -> None:
    ledger_path = tmp_path / "ledger.jsonl"
    anchor_path = tmp_path / "anchors.jsonl"
    marker_path = tmp_path / "clean.json"
    entries = _chain([("pending", "q1"), ("receipt", "q1")])
    _write_ledger(ledger_path, entries)
    write_head_anchor_checkpoint(
        str(anchor_path),
        str(ledger_path),
        window_id=_WINDOW,
        ts_utc=_TS,
        fsync=False,
    )

    no_enabled = build_claim_window_evidence(
        anchor_store_path=str(anchor_path),
        ledger_path=str(ledger_path),
        window_id=_WINDOW,
        enabled_samples=[],
        clean_shutdown_marker_path=str(marker_path),
        served_point_observations=_good_observations(),
    )
    assert no_enabled.reason == "enabled_window_not_proven"

    write_clean_shutdown_marker(
        str(marker_path),
        window_id=_WINDOW,
        ts_utc=_TS,
        fsync=False,
    )
    missing_point = build_claim_window_evidence(
        anchor_store_path=str(anchor_path),
        ledger_path=str(ledger_path),
        window_id=_WINDOW,
        enabled_samples=[
            new_enabled_state_sample(window_id=_WINDOW, enabled=True, ts_utc=_TS),
        ],
        clean_shutdown_marker_path=str(marker_path),
        served_point_observations=_good_observations()[:-1],
    )
    assert missing_point.reason == "served_point_instrumentation_not_proven"
    assert missing_point.input_ready is False

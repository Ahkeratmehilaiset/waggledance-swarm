# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

import copy
import itertools
import json
from datetime import datetime, timezone

import pytest

from waggledance.core.magma import chat_served_claim_window_evidence as E
from waggledance.core.magma import chat_served_ledger as L
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
    build_claim_window_evidence,
    claim_window_from_evidence,
    derive_enabled_across_window,
    derive_instrumented_served_points,
    new_claim_window_final_boundary,
    new_claim_window_id,
    new_claim_window_start_boundary,
    new_clean_shutdown_marker_v1,
    new_enabled_state_sample,
    new_receipt_index_entry,
    new_served_point_observation,
    read_clean_shutdown_marker,
    read_latest_head_anchor,
    valid_claim_window_final_boundary,
    valid_claim_window_start_boundary,
    valid_clean_shutdown_marker_v1,
    valid_receipt_index_entry,
    valid_served_point_observation,
    verify_claim_window_lifecycle_binding,
    verify_production_window,
    write_clean_shutdown_marker,
    write_enabled_state_sample,
    write_head_anchor_checkpoint,
    write_served_point_observation,
)
from waggledance.core.magma.chat_served_metadata import WORLD_SNAPSHOT_NA_MARKER
from waggledance.core.magma.chat_served_receipt import (
    build_chat_served_receipt,
    build_chat_served_summary,
)

_TS = "2026-07-04T16:00:00Z"
_TS_1 = "2026-07-04T16:00:01Z"
_TS_2 = "2026-07-04T16:00:02Z"
_TS_3 = "2026-07-04T16:00:03Z"
_TS_10 = "2026-07-04T16:00:10Z"
_META = {"source": "chat", "route_type": "solver"}
_DIGEST = "sha256:" + "cd" * 32
_OTHER_DIGEST = "sha256:" + "ef" * 32
_SOURCE_HEAD = "a" * 40
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


def _good_observations(*, window_id=None):
    return [
        new_served_point_observation(
            point=point,
            wired=True,
            ts_utc=_TS,
            window_id=window_id,
        )
        for point in sorted(REQUIRED_CHAT_SERVED_POINTS)
    ]


def _side_offsets(value):
    return {name: value for name in CLAIM_WINDOW_SIDE_STREAMS}


def _lifecycle_bundle(
    *,
    window_id=_WINDOW,
    sample_timestamps=(_TS, _TS_1),
    sample_states=None,
    start_ledger_offset=0,
    final_ledger_offset=1,
    start_side_offset=0,
    final_side_offset=1,
    final_enabled_offset=None,
    max_gap_seconds=1,
    start_sample_digest=None,
    end_sample_digest=None,
    final_ts=_TS_2,
    marker_ts=_TS_3,
):
    states = (
        [True] * len(sample_timestamps)
        if sample_states is None
        else list(sample_states)
    )
    assert len(states) == len(sample_timestamps)
    samples = [
        new_enabled_state_sample(
            window_id=window_id,
            enabled=enabled,
            ts_utc=timestamp,
        )
        for timestamp, enabled in zip(sample_timestamps, states, strict=True)
    ]
    start_side_offsets = _side_offsets(start_side_offset)
    final_side_offsets = _side_offsets(final_side_offset)
    final_side_offsets["enabled_state_samples"] = (
        start_side_offset + len(samples)
        if final_enabled_offset is None
        else final_enabled_offset
    )
    sequence_digest = E.derive_enabled_sample_sequence_digest(
        samples,
        window_id=window_id,
    )
    assert sequence_digest is not None
    start = new_claim_window_start_boundary(
        window_id=window_id,
        start_ledger_head=L.GENESIS_PREV_HASH,
        start_ledger_offset=start_ledger_offset,
        side_stream_offsets=start_side_offsets,
        source_head=_SOURCE_HEAD,
        start_enabled_sample_digest=(
            samples[0]["sample_hash"]
            if start_sample_digest is None
            else start_sample_digest
        ),
        max_enabled_sample_gap_seconds=max_gap_seconds,
        ts_utc=_TS,
    )
    final = new_claim_window_final_boundary(
        window_id=window_id,
        start_boundary_digest=start["boundary_hash"],
        final_ledger_head=_DIGEST,
        final_ledger_offset=final_ledger_offset,
        side_stream_offsets=final_side_offsets,
        end_enabled_sample_digest=(
            samples[-1]["sample_hash"]
            if end_sample_digest is None
            else end_sample_digest
        ),
        enabled_samples_count=len(samples),
        enabled_sample_sequence_digest=sequence_digest,
        ts_utc=final_ts,
    )
    marker = new_clean_shutdown_marker_v1(
        window_id=window_id,
        start_boundary_digest=start["boundary_hash"],
        final_boundary_digest=final["boundary_hash"],
        final_ledger_head=final["final_ledger_head"],
        end_enabled_sample_digest=final["end_enabled_sample_digest"],
        ts_utc=marker_ts,
    )
    return start, final, marker, samples


def _read_jsonl(path):
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
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
            new_enabled_state_sample(window_id=_WINDOW, enabled=True, ts_utc=_TS_1),
        ],
        clean_shutdown_marker_path=str(marker_path),
        served_point_observations=_good_observations(),
    )
    report = claim_window_from_evidence(str(ledger_path), evidence)

    assert anchor_hash.startswith("sha256:")
    assert str(ledger_path) not in anchor_path.read_text(encoding="utf-8")
    assert evidence.input_ready is False
    assert evidence.reason == "lifecycle_binding_missing"
    assert evidence.expected_head == L.head_hash(entries)
    assert evidence.enabled_across_window is True
    assert evidence.clean_shutdown is True
    assert evidence.missing_served_points == ()
    assert report.eligible is False
    assert report.reason == "lifecycle_binding_missing"


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
    assert report.reason == "head_anchor_not_independent"


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


def test_enabled_samples_reject_duplicate_nonmonotonic_and_excessive_gaps() -> None:
    first = new_enabled_state_sample(window_id=_WINDOW, enabled=True, ts_utc=_TS)
    duplicate = new_enabled_state_sample(window_id=_WINDOW, enabled=True, ts_utc=_TS)
    earlier = new_enabled_state_sample(
        window_id=_WINDOW,
        enabled=True,
        ts_utc="2026-07-04T15:59:59Z",
    )
    later = new_enabled_state_sample(window_id=_WINDOW, enabled=True, ts_utc=_TS_2)

    assert derive_enabled_across_window([first, duplicate], window_id=_WINDOW) is False
    assert derive_enabled_across_window([first, earlier], window_id=_WINDOW) is False
    assert (
        derive_enabled_across_window(
            [first, later],
            window_id=_WINDOW,
            max_gap_seconds=1,
        )
        is False
    )


def test_evidence_builders_reject_non_utc_timestamps() -> None:
    with pytest.raises(ValueError, match="UTC timestamp"):
        new_enabled_state_sample(
            window_id=_WINDOW,
            enabled=True,
            ts_utc="2026-07-04T16:00:00+01:00",
        )
    with pytest.raises(ValueError, match="UTC timestamp"):
        new_served_point_observation(
            point="solver",
            wired=True,
            ts_utc="not-a-timestamp",
        )


@pytest.mark.parametrize("invalid", ["false", 0, 1, None])
def test_evidence_builders_reject_non_boolean_states(invalid) -> None:
    with pytest.raises(ValueError, match="enabled must be a boolean"):
        new_enabled_state_sample(
            window_id=_WINDOW,
            enabled=invalid,
            ts_utc=_TS,
        )
    with pytest.raises(ValueError, match="wired must be a boolean"):
        new_served_point_observation(
            point="solver",
            wired=invalid,
            ts_utc=_TS,
        )


def test_enabled_sample_writer_appends_hash_valid_records(tmp_path) -> None:
    sample_path = tmp_path / "enabled-samples.jsonl"

    write_enabled_state_sample(
        str(sample_path),
        window_id=_WINDOW,
        enabled=True,
        ts_utc=_TS,
        fsync=False,
    )
    write_enabled_state_sample(
        str(sample_path),
        window_id=_WINDOW,
        enabled=False,
        ts_utc=_TS,
        fsync=False,
    )
    samples = _read_jsonl(sample_path)

    assert len(samples) == 2
    assert derive_enabled_across_window(samples[:1], window_id=_WINDOW) is True
    assert derive_enabled_across_window(samples, window_id=_WINDOW) is False


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


def test_lifecycle_binding_accepts_complete_v1_contract_without_authority() -> None:
    start, final, marker, samples = _lifecycle_bundle()

    result = verify_claim_window_lifecycle_binding(
        start_boundary=start,
        final_boundary=final,
        clean_shutdown_marker=marker,
        enabled_samples=samples,
        window_id=_WINDOW,
    )

    assert result.ok is True
    assert result.reason is None
    assert result._fields == ("ok", "reason")
    assert "claim_safe" not in start
    assert "claim_safe" not in final
    assert "claim_safe" not in marker
    assert valid_claim_window_start_boundary(start, window_id=_WINDOW) is True
    assert valid_claim_window_final_boundary(final, window_id=_WINDOW) is True
    assert valid_clean_shutdown_marker_v1(marker, window_id=_WINDOW) is True


def test_lifecycle_binding_rejects_marker_from_a_different_boundary() -> None:
    _, _, stale_marker, _ = _lifecycle_bundle()
    start, final, _, samples = _lifecycle_bundle(
        start_ledger_offset=1,
        final_ledger_offset=2,
        start_side_offset=1,
        final_side_offset=2,
    )

    result = verify_claim_window_lifecycle_binding(
        start_boundary=start,
        final_boundary=final,
        clean_shutdown_marker=stale_marker,
        enabled_samples=samples,
        window_id=_WINDOW,
    )

    assert result.ok is False
    assert result.reason == "marker_start_digest_mismatch"


def test_lifecycle_binding_rejects_cross_window_evidence() -> None:
    start, final, marker, samples = _lifecycle_bundle()

    result = verify_claim_window_lifecycle_binding(
        start_boundary=start,
        final_boundary=final,
        clean_shutdown_marker=marker,
        enabled_samples=samples,
        window_id="window:other",
    )

    assert result.ok is False
    assert result.reason == "start_boundary_invalid"


def test_lifecycle_binding_rejects_valid_but_unlinked_boundary() -> None:
    start, final, _, samples = _lifecycle_bundle()
    unlinked_final = new_claim_window_final_boundary(
        window_id=_WINDOW,
        start_boundary_digest=_OTHER_DIGEST,
        final_ledger_head=final["final_ledger_head"],
        final_ledger_offset=final["final_ledger_offset"],
        side_stream_offsets=final["side_stream_offsets"],
        end_enabled_sample_digest=final["end_enabled_sample_digest"],
        enabled_samples_count=final["enabled_samples_count"],
        enabled_sample_sequence_digest=final["enabled_sample_sequence_digest"],
        ts_utc=final["ts_utc"],
    )
    marker = new_clean_shutdown_marker_v1(
        window_id=_WINDOW,
        start_boundary_digest=_OTHER_DIGEST,
        final_boundary_digest=unlinked_final["boundary_hash"],
        final_ledger_head=unlinked_final["final_ledger_head"],
        end_enabled_sample_digest=unlinked_final["end_enabled_sample_digest"],
        ts_utc=_TS_3,
    )

    result = verify_claim_window_lifecycle_binding(
        start_boundary=start,
        final_boundary=unlinked_final,
        clean_shutdown_marker=marker,
        enabled_samples=samples,
        window_id=_WINDOW,
    )

    assert result.ok is False
    assert result.reason == "start_boundary_digest_mismatch"


@pytest.mark.parametrize(
    ("field", "reason"),
    [
        ("final_boundary_digest", "marker_final_digest_mismatch"),
        ("final_ledger_head", "marker_final_head_mismatch"),
        ("end_enabled_sample_digest", "marker_end_sample_mismatch"),
    ],
)
def test_lifecycle_binding_rejects_valid_but_unlinked_marker(field, reason) -> None:
    start, final, _, samples = _lifecycle_bundle()
    marker_values = {
        "start_boundary_digest": start["boundary_hash"],
        "final_boundary_digest": final["boundary_hash"],
        "final_ledger_head": final["final_ledger_head"],
        "end_enabled_sample_digest": final["end_enabled_sample_digest"],
    }
    marker_values[field] = _OTHER_DIGEST
    marker = new_clean_shutdown_marker_v1(
        window_id=_WINDOW,
        ts_utc=_TS_3,
        **marker_values,
    )

    result = verify_claim_window_lifecycle_binding(
        start_boundary=start,
        final_boundary=final,
        clean_shutdown_marker=marker,
        enabled_samples=samples,
        window_id=_WINDOW,
    )

    assert result.ok is False
    assert result.reason == reason


@pytest.mark.parametrize(
    ("digest_field", "reason"),
    [
        ("start_sample_digest", "start_enabled_sample_mismatch"),
        ("end_sample_digest", "end_enabled_sample_mismatch"),
    ],
)
def test_lifecycle_binding_rejects_sample_boundary_digest_mismatch(
    digest_field,
    reason,
) -> None:
    start, final, marker, samples = _lifecycle_bundle(
        **{digest_field: _OTHER_DIGEST}
    )

    result = verify_claim_window_lifecycle_binding(
        start_boundary=start,
        final_boundary=final,
        clean_shutdown_marker=marker,
        enabled_samples=samples,
        window_id=_WINDOW,
    )

    assert result.ok is False
    assert result.reason == reason


@pytest.mark.parametrize(
    "offsets",
    [
        {name: 0 for name in CLAIM_WINDOW_SIDE_STREAMS if name != "receipt_index"},
        {**_side_offsets(0), "unexpected": 0},
        {**_side_offsets(0), "receipt_index": -1},
        {**_side_offsets(0), "receipt_index": True},
    ],
)
def test_lifecycle_boundary_builder_rejects_invalid_side_offsets(offsets) -> None:
    sample = new_enabled_state_sample(window_id=_WINDOW, enabled=True, ts_utc=_TS)

    with pytest.raises(ValueError, match="side_stream_offsets"):
        new_claim_window_start_boundary(
            window_id=_WINDOW,
            start_ledger_head=L.GENESIS_PREV_HASH,
            start_ledger_offset=0,
            side_stream_offsets=offsets,
            source_head=_SOURCE_HEAD,
            start_enabled_sample_digest=sample["sample_hash"],
            max_enabled_sample_gap_seconds=1,
            ts_utc=_TS,
        )
    with pytest.raises(ValueError, match="side_stream_offsets"):
        new_claim_window_final_boundary(
            window_id=_WINDOW,
            start_boundary_digest=_DIGEST,
            final_ledger_head=_DIGEST,
            final_ledger_offset=1,
            side_stream_offsets=offsets,
            end_enabled_sample_digest=sample["sample_hash"],
            enabled_samples_count=1,
            enabled_sample_sequence_digest=_DIGEST,
            ts_utc=_TS_1,
        )


@pytest.mark.parametrize("invalid_count", [0, -1, True])
def test_final_boundary_builder_rejects_invalid_enabled_sample_count(
    invalid_count,
) -> None:
    sample = new_enabled_state_sample(window_id=_WINDOW, enabled=True, ts_utc=_TS)
    sequence_digest = E.derive_enabled_sample_sequence_digest(
        [sample],
        window_id=_WINDOW,
    )
    assert sequence_digest is not None

    with pytest.raises(ValueError, match="enabled_samples_count is invalid"):
        new_claim_window_final_boundary(
            window_id=_WINDOW,
            start_boundary_digest=_DIGEST,
            final_ledger_head=_DIGEST,
            final_ledger_offset=1,
            side_stream_offsets=_side_offsets(1),
            end_enabled_sample_digest=sample["sample_hash"],
            enabled_samples_count=invalid_count,
            enabled_sample_sequence_digest=sequence_digest,
            ts_utc=_TS_1,
        )


def test_lifecycle_binding_rejects_regressing_offsets() -> None:
    ledger_start, ledger_final, ledger_marker, ledger_samples = _lifecycle_bundle(
        start_ledger_offset=2,
        final_ledger_offset=1,
    )
    ledger_result = verify_claim_window_lifecycle_binding(
        start_boundary=ledger_start,
        final_boundary=ledger_final,
        clean_shutdown_marker=ledger_marker,
        enabled_samples=ledger_samples,
        window_id=_WINDOW,
    )

    side_start, side_final, side_marker, side_samples = _lifecycle_bundle(
        start_side_offset=1,
        final_side_offset=2,
        final_enabled_offset=0,
    )
    side_result = verify_claim_window_lifecycle_binding(
        start_boundary=side_start,
        final_boundary=side_final,
        clean_shutdown_marker=side_marker,
        enabled_samples=side_samples,
        window_id=_WINDOW,
    )

    assert ledger_result.reason == "ledger_offset_regressed"
    assert side_result.reason == "side_stream_offset_regressed:enabled_state_samples"


@pytest.mark.parametrize(
    ("sample_timestamps", "reason"),
    [
        ((_TS,), "enabled_boundary_samples_missing"),
        ((_TS, _TS), "enabled_timeline_invalid"),
        ((_TS_1, _TS), "enabled_timeline_invalid"),
        ((_TS, _TS_2), "enabled_timeline_invalid"),
        (("2026-07-04T15:59:59Z", _TS), "enabled_timeline_outside_boundary"),
    ],
)
def test_lifecycle_binding_rejects_invalid_enabled_timeline(
    sample_timestamps,
    reason,
) -> None:
    start, final, marker, samples = _lifecycle_bundle(
        sample_timestamps=sample_timestamps,
        max_gap_seconds=1,
    )

    result = verify_claim_window_lifecycle_binding(
        start_boundary=start,
        final_boundary=final,
        clean_shutdown_marker=marker,
        enabled_samples=samples,
        window_id=_WINDOW,
    )

    assert result.ok is False
    assert result.reason == reason


@pytest.mark.parametrize(
    ("sample_timestamps", "final_ts"),
    [
        ((_TS_2, _TS_3), _TS_3),
        ((_TS, _TS_1), _TS_3),
    ],
)
def test_lifecycle_binding_checks_each_window_edge_cadence(
    sample_timestamps,
    final_ts,
) -> None:
    start, final, marker, samples = _lifecycle_bundle(
        sample_timestamps=sample_timestamps,
        max_gap_seconds=1,
        final_ts=final_ts,
        marker_ts=_TS_10,
    )

    result = verify_claim_window_lifecycle_binding(
        start_boundary=start,
        final_boundary=final,
        clean_shutdown_marker=marker,
        enabled_samples=samples,
        window_id=_WINDOW,
    )

    assert result.ok is False
    assert result.reason == "enabled_boundary_cadence_gap"


def test_lifecycle_binding_rejects_nonmonotonic_boundary_timestamps() -> None:
    start, final, marker, samples = _lifecycle_bundle(
        final_ts=_TS_2,
        marker_ts=_TS_2,
    )

    result = verify_claim_window_lifecycle_binding(
        start_boundary=start,
        final_boundary=final,
        clean_shutdown_marker=marker,
        enabled_samples=samples,
        window_id=_WINDOW,
    )

    assert result.ok is False
    assert result.reason == "lifecycle_timestamp_not_monotonic"


def test_lifecycle_binding_fails_closed_when_sample_reader_raises() -> None:
    start, final, marker, samples = _lifecycle_bundle()

    def broken_samples():
        yield samples[0]
        raise RuntimeError("read failed")

    result = verify_claim_window_lifecycle_binding(
        start_boundary=start,
        final_boundary=final,
        clean_shutdown_marker=marker,
        enabled_samples=broken_samples(),
        window_id=_WINDOW,
    )

    assert result.ok is False
    assert result.reason == "enabled_samples_read_failed"


def test_lifecycle_binding_rejects_cursor_count_mismatch() -> None:
    start, final, _, samples = _lifecycle_bundle()
    mismatched_final = new_claim_window_final_boundary(
        window_id=_WINDOW,
        start_boundary_digest=start["boundary_hash"],
        final_ledger_head=final["final_ledger_head"],
        final_ledger_offset=final["final_ledger_offset"],
        side_stream_offsets=final["side_stream_offsets"],
        end_enabled_sample_digest=final["end_enabled_sample_digest"],
        enabled_samples_count=3,
        enabled_sample_sequence_digest=final["enabled_sample_sequence_digest"],
        ts_utc=final["ts_utc"],
    )
    marker = new_clean_shutdown_marker_v1(
        window_id=_WINDOW,
        start_boundary_digest=start["boundary_hash"],
        final_boundary_digest=mismatched_final["boundary_hash"],
        final_ledger_head=mismatched_final["final_ledger_head"],
        end_enabled_sample_digest=mismatched_final["end_enabled_sample_digest"],
        ts_utc=_TS_3,
    )

    result = verify_claim_window_lifecycle_binding(
        start_boundary=start,
        final_boundary=mismatched_final,
        clean_shutdown_marker=marker,
        enabled_samples=samples,
        window_id=_WINDOW,
    )

    assert result.ok is False
    assert result.reason == "enabled_sample_count_cursor_mismatch"


def test_lifecycle_binding_rejects_truncated_cursor_slice() -> None:
    start, final, marker, samples = _lifecycle_bundle(
        sample_timestamps=(_TS, _TS_1, _TS_2),
        final_ts=_TS_3,
        marker_ts=_TS_10,
    )

    result = verify_claim_window_lifecycle_binding(
        start_boundary=start,
        final_boundary=final,
        clean_shutdown_marker=marker,
        enabled_samples=[samples[0], samples[-1]],
        window_id=_WINDOW,
    )

    assert result.ok is False
    assert result.reason == "enabled_sample_offset_span_mismatch"


def test_lifecycle_binding_rejects_omitted_intermediate_false_sample() -> None:
    start, final, marker, full_samples = _lifecycle_bundle(
        sample_timestamps=(_TS, _TS_1, _TS_2),
        sample_states=(True, False, True),
        final_ts=_TS_3,
        marker_ts=_TS_10,
    )
    substituted_samples = [
        full_samples[0],
        new_enabled_state_sample(window_id=_WINDOW, enabled=True, ts_utc=_TS_1),
        full_samples[2],
    ]

    result = verify_claim_window_lifecycle_binding(
        start_boundary=start,
        final_boundary=final,
        clean_shutdown_marker=marker,
        enabled_samples=substituted_samples,
        window_id=_WINDOW,
    )

    assert result.ok is False
    assert result.reason == "enabled_sample_sequence_mismatch"


def test_lifecycle_binding_bounds_enabled_sample_materialization(monkeypatch) -> None:
    start, final, marker, _ = _lifecycle_bundle()
    samples = [
        new_enabled_state_sample(window_id=_WINDOW, enabled=True, ts_utc=timestamp)
        for timestamp in (_TS, _TS_1, _TS_2)
    ]
    monkeypatch.setattr(E, "MAX_ENABLED_SAMPLES_PER_WINDOW", 2)

    result = verify_claim_window_lifecycle_binding(
        start_boundary=start,
        final_boundary=final,
        clean_shutdown_marker=marker,
        enabled_samples=iter(samples),
        window_id=_WINDOW,
    )

    assert result.ok is False
    assert result.reason == "enabled_samples_exceed_bound"


@pytest.mark.parametrize("unexpected_key", ["raw_query", "ledger_path"])
def test_lifecycle_records_reject_unexpected_raw_content(unexpected_key) -> None:
    start, final, marker, _ = _lifecycle_bundle()
    for record in (start, final, marker):
        tampered = dict(record)
        tampered[unexpected_key] = "secret"
        if record is start:
            assert valid_claim_window_start_boundary(tampered, window_id=_WINDOW) is False
        elif record is final:
            assert valid_claim_window_final_boundary(tampered, window_id=_WINDOW) is False
        else:
            assert valid_clean_shutdown_marker_v1(tampered, window_id=_WINDOW) is False


def test_claim_window_ids_are_unique_tokens() -> None:
    first = new_claim_window_id()
    second = new_claim_window_id()

    assert first.startswith("window:")
    assert second.startswith("window:")
    assert first != second


def test_legacy_evidence_adapter_cannot_be_made_eligible_by_construction(
    tmp_path,
) -> None:
    ledger_path = tmp_path / "ledger.jsonl"
    entries = _chain([("pending", "q1"), ("receipt", "q1")])
    _write_ledger(ledger_path, entries)
    required = tuple(sorted(REQUIRED_CHAT_SERVED_POINTS))
    evidence = E.ClaimWindowEvidence(
        expected_head=L.head_hash(entries),
        enabled_across_window=True,
        clean_shutdown=True,
        required_served_points=required,
        instrumented_served_points=required,
        missing_served_points=(),
        input_ready=True,
        reason=None,
    )

    report = claim_window_from_evidence(str(ledger_path), evidence)

    assert report.eligible is False
    assert report.reason == "unclean_shutdown_window_invalid"


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


def test_served_point_v1_observations_require_the_expected_window() -> None:
    legacy = new_served_point_observation(point="solver", wired=True, ts_utc=_TS)
    bound = new_served_point_observation(
        point="solver",
        wired=True,
        ts_utc=_TS,
        window_id=_WINDOW,
    )

    assert valid_served_point_observation(legacy) is True
    assert valid_served_point_observation(legacy, window_id=_WINDOW) is False
    assert valid_served_point_observation(bound) is False
    assert valid_served_point_observation(bound, window_id=_WINDOW) is True
    assert valid_served_point_observation(bound, window_id="window:other") is False
    assert derive_instrumented_served_points(
        [legacy, bound],
        window_id=_WINDOW,
    ) == ("solver",)

    tampered = dict(bound)
    tampered["response"] = "raw"
    assert valid_served_point_observation(tampered, window_id=_WINDOW) is False


def test_served_point_writer_appends_hash_valid_records(tmp_path) -> None:
    observation_path = tmp_path / "served-points.jsonl"

    write_served_point_observation(
        str(observation_path),
        point="solver",
        wired=True,
        ts_utc=_TS,
        fsync=False,
    )
    write_served_point_observation(
        str(observation_path),
        point="hotcache",
        wired=False,
        ts_utc=_TS,
        fsync=False,
    )
    observations = _read_jsonl(observation_path)

    assert derive_instrumented_served_points(observations) == ("solver",)


def test_legacy_evidence_reason_is_lifecycle_binding_missing(tmp_path) -> None:
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
    assert no_enabled.reason == "lifecycle_binding_missing"

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
    assert missing_point.reason == "lifecycle_binding_missing"
    assert missing_point.input_ready is False


def _production_window_inputs():
    query = "hello"
    query_digest = canonical_query_digest(query)
    summary = build_chat_served_summary(
        query=query,
        response="world",
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
    payload, _evaluation, receipt = build_chat_served_receipt(
        summary_payload=summary,
        now_utc=datetime(2026, 7, 4, 16, 0, tzinfo=timezone.utc),
        ordinal=1,
    )
    receipt_ref = sha256_digest(receipt)
    metadata = {
        "source": "chat",
        "route_type": "solver",
        "language": "en",
        "profile": "HOME",
        "world_snapshot_ref": WORLD_SNAPSHOT_NA_MARKER,
        "query_digest": query_digest,
        "normalization_version": NORMALIZATION_VERSION,
    }
    pending = L.new_served_pending("q1", L.GENESIS_PREV_HASH, _TS, metadata)
    terminal = L.new_receipt_terminal(
        "q1",
        pending["entry_hash"],
        _TS_1,
        receipt_ref,
    )
    ledger = [pending, terminal]
    samples = [
        new_enabled_state_sample(
            window_id=_WINDOW,
            enabled=True,
            ts_utc=_TS,
            sample_kind="start",
        ),
        new_enabled_state_sample(
            window_id=_WINDOW,
            enabled=True,
            ts_utc=_TS_1,
            sample_kind="end",
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
    start_offsets = _side_offsets(0)
    final_offsets = {
        "enabled_state_samples": len(samples),
        "pending_append_failures": 0,
        "receipt_index": 1,
        "served_point_observations": len(observations),
    }
    sequence_digest = E.derive_enabled_sample_sequence_digest(
        samples,
        window_id=_WINDOW,
    )
    assert sequence_digest is not None
    start = new_claim_window_start_boundary(
        window_id=_WINDOW,
        start_ledger_head=L.GENESIS_PREV_HASH,
        start_ledger_offset=0,
        side_stream_offsets=start_offsets,
        source_head=_SOURCE_HEAD,
        start_enabled_sample_digest=samples[0]["sample_hash"],
        max_enabled_sample_gap_seconds=2,
        ts_utc=_TS,
    )
    final = new_claim_window_final_boundary(
        window_id=_WINDOW,
        start_boundary_digest=start["boundary_hash"],
        final_ledger_head=terminal["entry_hash"],
        final_ledger_offset=len(ledger),
        side_stream_offsets=final_offsets,
        end_enabled_sample_digest=samples[-1]["sample_hash"],
        enabled_samples_count=len(samples),
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
    manifest_ref = "served-q1/manifest.json"
    index = [
        new_receipt_index_entry(
            window_id=_WINDOW,
            served_id="q1",
            receipt_ref=receipt_ref,
            manifest_ref=manifest_ref,
        )
    ]
    bundles = {manifest_ref: {"receipt": receipt, "payload": payload}}
    return {
        "expected_window_id": _WINDOW,
        "expected_source_head": _SOURCE_HEAD,
        "start_boundary": start,
        "final_boundary": final,
        "clean_shutdown_marker": marker,
        "ledger_entries": ledger,
        "enabled_samples": samples,
        "pending_failures": [],
        "receipt_index": index,
        "served_point_observations": observations,
        "resolve_receipt_bundle": bundles.get,
        "verify_receipt_bundle": lambda _bundle: True,
        "content_address_receipt": sha256_digest,
        "previously_verified_window_ids": (),
    }


def test_production_window_pre_marker_and_final_verdicts_are_non_authorizing() -> None:
    inputs = _production_window_inputs()
    marker = inputs.pop("clean_shutdown_marker")

    pre_marker = verify_production_window(
        clean_shutdown_marker=None,
        **inputs,
    )
    final = verify_production_window(
        clean_shutdown_marker=marker,
        **inputs,
    )

    assert pre_marker.ok is True
    assert pre_marker.phase == "pre_marker_verified"
    assert pre_marker.marker_verified is False
    assert final.ok is True
    assert final.phase == "final_verified"
    assert final.marker_verified is True
    for verdict in (pre_marker, final):
        assert verdict.measurement_only is True
        assert verdict.claim_safe_count == 0
        assert "eligible" not in verdict._fields
        assert "clean_shutdown" not in verdict._fields


def test_production_window_registry_binding_digest_pins_context_and_all_evidence() -> None:
    inputs = _production_window_inputs()
    digest_inputs = {
        key: value
        for key, value in inputs.items()
        if key
        not in {
            "resolve_receipt_bundle",
            "verify_receipt_bundle",
            "content_address_receipt",
            "previously_verified_window_ids",
        }
    }

    first = E.derive_production_window_registry_binding_digest(**digest_inputs)
    repeated = E.derive_production_window_registry_binding_digest(
        **{
            **digest_inputs,
            "ledger_entries": tuple(digest_inputs["ledger_entries"]),
        }
    )
    assert first.startswith("sha256:")
    assert repeated == first

    mutations = {
        "expected_window_id": "window:other",
        "expected_source_head": "b" * 40,
        "start_boundary": {
            **digest_inputs["start_boundary"],
            "audit_nonce": "changed",
        },
        "final_boundary": {
            **digest_inputs["final_boundary"],
            "audit_nonce": "changed",
        },
        "clean_shutdown_marker": {
            **digest_inputs["clean_shutdown_marker"],
            "audit_nonce": "changed",
        },
    }
    for field in (
        "ledger_entries",
        "enabled_samples",
        "pending_failures",
        "receipt_index",
        "served_point_observations",
    ):
        mutations[field] = [
            *copy.deepcopy(digest_inputs[field]),
            {"audit_nonce": f"changed:{field}"},
        ]
    for field, changed_value in mutations.items():
        changed_inputs = copy.deepcopy(digest_inputs)
        changed_inputs[field] = changed_value
        assert (
            E.derive_production_window_registry_binding_digest(
                **changed_inputs
            )
            != first
        ), field


def test_production_window_rejects_reused_window_and_wrong_source() -> None:
    reused_inputs = _production_window_inputs()
    reused_inputs["previously_verified_window_ids"] = (_WINDOW,)
    reused = verify_production_window(**reused_inputs)

    stale_inputs = _production_window_inputs()
    stale_inputs["expected_source_head"] = "b" * 40
    stale = verify_production_window(**stale_inputs)

    assert reused.reason == "window_id_reused"
    assert stale.reason == "source_head_mismatch"


def test_production_window_bounds_hostile_window_registry(monkeypatch) -> None:
    inputs = _production_window_inputs()
    monkeypatch.setattr(E, "MAX_PRODUCTION_WINDOW_RECORDS", 6)
    inputs["previously_verified_window_ids"] = itertools.repeat("window:prior")

    result = verify_production_window(**inputs)

    assert result.reason == "window_registry_read_failed"


@pytest.mark.parametrize("producer_flag", ["eligible", "ok"])
def test_production_window_rejects_torn_marker_and_producer_flags(
    producer_flag,
) -> None:
    marker_inputs = _production_window_inputs()
    marker_inputs["clean_shutdown_marker"] = {
        **marker_inputs["clean_shutdown_marker"],
        "marker_hash": _OTHER_DIGEST,
    }
    marker_result = verify_production_window(**marker_inputs)

    flag_inputs = _production_window_inputs()
    flag_inputs["start_boundary"] = {
        **flag_inputs["start_boundary"],
        producer_flag: True,
    }
    flag_result = verify_production_window(**flag_inputs)

    assert marker_result.reason == "clean_marker_invalid"
    assert flag_result.reason == "privacy_or_producer_flag_violation"


def test_production_window_rejects_cyclic_and_overdeep_evidence() -> None:
    cyclic_inputs = _production_window_inputs()
    cycle = {}
    cycle["nested"] = cycle
    cyclic_inputs["start_boundary"]["diagnostic"] = cycle
    cyclic = verify_production_window(**cyclic_inputs)

    deep_inputs = _production_window_inputs()
    nested = {}
    cursor = nested
    for _ in range(E.MAX_PRIVACY_SCAN_DEPTH + 1):
        child = {}
        cursor["nested"] = child
        cursor = child
    deep_inputs["start_boundary"]["diagnostic"] = nested
    deep = verify_production_window(**deep_inputs)

    assert cyclic.reason == "privacy_or_producer_flag_violation"
    assert deep.reason == "privacy_or_producer_flag_violation"


def test_production_window_rejects_cross_window_and_whole_file_side_streams() -> None:
    cross_inputs = _production_window_inputs()
    row = cross_inputs["receipt_index"][0]
    cross_inputs["receipt_index"] = [
        new_receipt_index_entry(
            window_id="window:other",
            served_id=row["served_id"],
            receipt_ref=row["receipt_ref"],
            manifest_ref=row["manifest_ref"],
        )
    ]
    cross = verify_production_window(**cross_inputs)

    whole_inputs = _production_window_inputs()
    whole_inputs["receipt_index"] = [
        *whole_inputs["receipt_index"],
        whole_inputs["receipt_index"][0],
    ]
    whole = verify_production_window(**whole_inputs)

    assert cross.reason == "receipt_index_entry_invalid"
    assert whole.reason == "side_stream_cursor_slice_mismatch:receipt_index"


@pytest.mark.parametrize(
    "bundle_mutation",
    [
        {"raw_query": {"nested": "secret"}},
        {"diagnostic": {"host_path": "C:/Users/operator/private.txt"}},
    ],
)
def test_production_window_rejects_nested_raw_and_absolute_paths(
    bundle_mutation,
) -> None:
    inputs = _production_window_inputs()
    original_resolver = inputs["resolve_receipt_bundle"]

    def poisoned(manifest_ref):
        bundle = original_resolver(manifest_ref)
        assert bundle is not None
        return {**bundle, "payload": {**bundle["payload"], **bundle_mutation}}

    inputs["resolve_receipt_bundle"] = poisoned
    result = verify_production_window(**inputs)

    assert result.reason == "receipt_bundle_verification_failed"


@pytest.mark.parametrize(
    ("callback", "replacement"),
    [
        ("verify_receipt_bundle", lambda _bundle: False),
        ("content_address_receipt", lambda _receipt: _OTHER_DIGEST),
        ("resolve_receipt_bundle", lambda _ref: None),
    ],
)
def test_production_window_rejects_receipt_callback_mismatch(
    callback,
    replacement,
) -> None:
    inputs = _production_window_inputs()
    inputs[callback] = replacement

    result = verify_production_window(**inputs)

    assert result.reason == "receipt_bundle_verification_failed"


def test_production_window_rejects_post_marker_evidence_mutation() -> None:
    inputs = _production_window_inputs()
    start = inputs["start_boundary"]

    def mutating_verifier(_bundle):
        start["source_head"] = "b" * 40
        return True

    inputs["verify_receipt_bundle"] = mutating_verifier
    result = verify_production_window(**inputs)

    assert result.reason == "evidence_mutated_during_verification"


def test_receipt_index_schema_is_closed_domain_bound_and_path_safe() -> None:
    entry = new_receipt_index_entry(
        window_id=_WINDOW,
        served_id="q1",
        receipt_ref=_DIGEST,
        manifest_ref="served-q1/manifest.json",
    )

    assert valid_receipt_index_entry(entry, window_id=_WINDOW) is True
    assert valid_receipt_index_entry(
        {**entry, "bound": True},
        window_id=_WINDOW,
    ) is False
    with pytest.raises(ValueError, match="manifest_ref"):
        new_receipt_index_entry(
            window_id=_WINDOW,
            served_id="q1",
            receipt_ref=_DIGEST,
            manifest_ref="C:/receipt/manifest.json",
        )


def test_enabled_sample_v1_requires_explicit_transition_shape() -> None:
    transition = new_enabled_state_sample(
        window_id=_WINDOW,
        enabled=False,
        previous_enabled=True,
        sample_kind="transition",
        ts_utc=_TS_1,
    )

    assert E.valid_enabled_state_sample(transition, window_id=_WINDOW) is True
    with pytest.raises(ValueError, match="transition previous_enabled"):
        new_enabled_state_sample(
            window_id=_WINDOW,
            enabled=True,
            previous_enabled=True,
            sample_kind="transition",
            ts_utc=_TS_1,
        )

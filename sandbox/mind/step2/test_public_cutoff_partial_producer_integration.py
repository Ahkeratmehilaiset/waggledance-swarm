"""RED: cutoff-bound partial catch-up is not yet a checkpointable source.

All source bytes, paths, policy and encoder-independent projections are synthetic.
"""
from datetime import datetime
from dataclasses import asdict, replace
import hashlib
import json
import os
from pathlib import Path

import pytest

from sandbox.mind.step2 import shared_index_builder as builder, shared_index_checkpoint as checkpoint
from sandbox.mind.step2.test_public_catchup_ready_prefix import _call
from sandbox.mind.step2.test_status_public_filter import event, read_fixture


def _partial(tmp_path):
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    _, pins, path = read_fixture(source_dir, [event(ts_utc="2026-10-31T11:58:00Z"),
        event(ts_utc="2026-10-31T12:01:10Z"), event(ts_utc="2026-10-31T11:59:00Z")])
    cutoff = datetime.fromisoformat("2026-10-31T12:01:30+00:00")
    chain = _call(path, pins, cutoff)
    assert chain["caught_up"] is False and chain["end_reason"] == "deferred"
    source = {"catchup_result": chain, "expected_anchor_sha256": pins["anchor_sha256"],
        "mode": "public-catchup-partial-v1", "public_policy_bytes": pins["public_policy_bytes"],
        "public_policy_sha256": pins["public_policy_sha256"], "anchor_bytes": pins["anchor_bytes"],
        "expected_start_prefix": chain["start_prefix"], "expected_cutoff_utc": cutoff}
    return source, pins


def test_partial_cutoff_source_passes_builder_without_pretending_complete(tmp_path):
    source, pins = _partial(tmp_path)
    projection, frozen = builder.verified_partial_catchup_projection(source, enabled=True,
        policy_bytes=pins["policy_bytes"], policy_sha256=pins["policy_sha256"],
        roster_bytes=pins["roster_bytes"], roster_sha256=pins["roster_sha256"])
    assert len(projection["records"]) == 1
    assert frozen["end_reason"] == "deferred" and frozen["caught_up"] is False


def test_checkpoint_rechecks_same_partial_cutoff_and_cursor(tmp_path):
    source, pins = _partial(tmp_path)
    chain = source["catchup_result"]
    projection, frozen = checkpoint._checked_catchup_delta(source, chain,
        chain["start_prefix"].cursor, chain["candidate_cursor"],
        policy_bytes=pins["policy_bytes"], expected_policy_sha256=pins["policy_sha256"],
        roster_bytes=pins["roster_bytes"], expected_roster_sha256=pins["roster_sha256"],
        expected_anchor_sha256=pins["anchor_sha256"], expected_public_policy_sha256=pins["public_policy_sha256"],
        public_policy_bytes=pins["public_policy_bytes"], expected_anchor_bytes=pins["anchor_bytes"],
        expected_start_prefix=chain["start_prefix"])
    assert len(projection["records"]) == 1
    assert frozen["candidate_cursor"] == chain["candidate_cursor"] and frozen["caught_up"] is False


def test_cutoff_or_start_prefix_tampering_refuses_before_build(tmp_path):
    source, pins = _partial(tmp_path)
    kwargs = dict(enabled=True, policy_bytes=pins["policy_bytes"], policy_sha256=pins["policy_sha256"],
                  roster_bytes=pins["roster_bytes"], roster_sha256=pins["roster_sha256"])
    changed = dict(source, expected_cutoff_utc=datetime.fromisoformat("2026-10-31T12:02:01+00:00"))
    with pytest.raises(builder.BuildRefused, match="source_binding_refused"):
        builder.verified_partial_catchup_projection(changed, **kwargs)
    start = source["expected_start_prefix"]
    changed = dict(source, expected_start_prefix=replace(start, sha256="0" * 64))
    with pytest.raises(builder.BuildRefused, match="source_binding_refused"):
        builder.verified_partial_catchup_projection(changed, **kwargs)
    changed = dict(source, mode="public-catchup-ready-v1")
    with pytest.raises(builder.BuildRefused, match="source_binding_refused"):
        builder.verified_catchup_projection(changed, policy_bytes=pins["policy_bytes"],
            policy_sha256=pins["policy_sha256"], roster_bytes=pins["roster_bytes"], roster_sha256=pins["roster_sha256"])


@pytest.mark.skipif(os.name != "nt", reason="private checkpoint requires Windows fixed volume")
@pytest.mark.parametrize("first_windows,end_reason", [(8, "deferred"), (1, "max_windows")])
def test_fenced_producer_publishes_ready_prefix_then_resumes_without_skipping(tmp_path, first_windows, end_reason):
    pytest.importorskip("faiss")
    from sandbox.mind.step2 import shared_prefix_public_producer as producer
    from sandbox.mind.step2.test_public_catchup_producer_integration_new import _read_pointer, _request
    from sandbox.mind.step2.test_shared_index_builder import public_event

    rows = [public_event(ts_utc="2026-10-31T11:58:00Z"),
            public_event(ts_utc="2026-10-31T12:01:10Z", status="done"),
            public_event(ts_utc="2026-10-31T11:59:00Z", agent="fable-5")]
    request, fixtures = _request(tmp_path, rows)
    cutoff = datetime.fromisoformat("2026-10-31T12:01:30+00:00")
    with pytest.raises(producer.PrefixProducerRefused, match="request_invalid"):
        producer.publish_public_catchup(enabled=True, request=request, max_windows=8, cutoff_utc=cutoff)
    request = replace(request, fenced=True)
    first = producer.publish_public_catchup(enabled=True, request=request, max_windows=first_windows, cutoff_utc=cutoff)
    assert first["status"] == "PUBLISHED_GENESIS" and first["coverage"] == "partial_ready_prefix"
    _, end = _read_pointer(request, fixtures, first["pointer_sha256"])
    path = request.config.source.source
    with open(path, "rb") as stream:
        original = stream.read()
    assert end.cursor.offset == original.index(b"\n") + 1
    assert end.sha256 == hashlib.sha256(original[:end.cursor.offset]).hexdigest()
    first_receipt = json.loads((request.root / "gen-a" / "build_receipt.json").read_bytes())
    assert first_receipt["source_coverage"] == {"kind": "partial_ready_prefix", "end_reason": end_reason,
                                                "cutoff_utc": cutoff.isoformat()}
    projection = json.loads((request.root / "gen-a" / "projection.json").read_bytes())
    assert len(projection["records"]) == 1

    anchor_doc = json.loads(request.config.source.anchor_bytes)
    anchor_doc["cursor_start"] = asdict(end.cursor)
    anchor = json.dumps(anchor_doc, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    anchor_sha = hashlib.sha256(anchor).hexdigest()
    source = replace(request.config.source, cursor=end.cursor, anchor_bytes=anchor, anchor_sha256=anchor_sha)
    resumed = replace(request, config=replace(request.config, source=source),
        pins=replace(request.pins, anchor_sha256=anchor_sha), start_prefix=end,
        expected_pointer_sha256=first["pointer_sha256"], generation="gen-b")
    cutoff2 = datetime.fromisoformat("2026-10-31T12:02:01+00:00")
    with pytest.raises(producer.PrefixProducerRefused):
        producer.publish_public_catchup(enabled=True, request=replace(resumed, expected_pointer_sha256="0" * 64),
                                        max_windows=8, cutoff_utc=cutoff2)
    with pytest.raises(producer.PrefixProducerRefused):
        producer.publish_public_catchup(enabled=True, request=replace(resumed, start_prefix=replace(end, sha256="0" * 64)),
                                        max_windows=8, cutoff_utc=cutoff2)
    second = producer.publish_public_catchup(enabled=True, request=resumed, max_windows=8, cutoff_utc=cutoff2)
    assert second["status"] == "PUBLISHED_HISTORY" and second["coverage"] == "caught_up_to_final_snapshot_not_history"
    _, final = _read_pointer(resumed, fixtures, second["pointer_sha256"])
    assert final.cursor.offset == len(original) and final.sha256 == hashlib.sha256(original).hexdigest()
    projection2 = json.loads((request.root / "gen-b" / "projection.json").read_bytes())
    assert len(projection2["records"]) == 3
    second_receipt = json.loads((request.root / "gen-b" / "build_receipt.json").read_bytes())
    assert second_receipt["source_coverage"] == {"kind": "ready_caught_up", "end_reason": "snapshot_end",
                                                 "cutoff_utc": cutoff2.isoformat()}


@pytest.mark.skipif(os.name != "nt", reason="private checkpoint requires Windows fixed volume")
def test_checkpoint_does_not_publish_when_partial_receipt_cutoff_differs(tmp_path, monkeypatch):
    pytest.importorskip("faiss")
    from sandbox.mind.step2 import shared_prefix_public_producer as producer
    from sandbox.mind.step2.test_public_catchup_producer_integration_new import _request
    from sandbox.mind.step2.test_shared_index_builder import public_event

    request, _ = _request(tmp_path, [public_event(ts_utc="2026-10-31T11:58:00Z"),
                                     public_event(ts_utc="2026-10-31T12:01:10Z")])
    request = replace(request, fenced=True)
    real_verify = checkpoint._verify_generation

    def changed_receipt(*args, **kwargs):
        result = real_verify(*args, **kwargs)
        result["source_coverage"] = dict(result["source_coverage"], cutoff_utc="2026-10-31T12:02:00+00:00")
        return result

    monkeypatch.setattr(checkpoint, "_verify_generation", changed_receipt)
    with pytest.raises(producer.PrefixProducerRefused):
        producer.publish_public_catchup(enabled=True, request=request, max_windows=8,
                                        cutoff_utc=datetime.fromisoformat("2026-10-31T12:01:30+00:00"))
    assert not (request.root / checkpoint.POINTER).exists()


@pytest.mark.skipif(os.name != "nt", reason="private checkpoint requires Windows fixed volume")
def test_first_young_row_refuses_without_advancing_or_publishing(tmp_path):
    pytest.importorskip("faiss")
    from sandbox.mind.step2 import prefix_private_state, shared_prefix_public_producer as producer
    from sandbox.mind.step2.test_public_catchup_producer_integration_new import _request
    from sandbox.mind.step2.test_shared_index_builder import public_event

    request, _ = _request(tmp_path, [public_event(ts_utc="2026-10-31T12:01:10Z"),
                                     public_event(ts_utc="2026-10-31T11:59:00Z")])
    request = replace(request, fenced=True)
    with pytest.raises(producer.PrefixProducerRefused):
        producer.publish_public_catchup(enabled=True, request=request, max_windows=8,
                                        cutoff_utc=datetime.fromisoformat("2026-10-31T12:01:30+00:00"))
    assert not (request.root / checkpoint.POINTER).exists()
    with prefix_private_state.locked_state(request.private_state_root, request.root, enabled=True) as state:
        state.require(None, request.start_prefix)


@pytest.mark.skipif(os.name != "nt", reason="private checkpoint requires Windows fixed volume")
def test_earlier_cutoff_cannot_expose_unready_predecessor_records(tmp_path):
    pytest.importorskip("faiss")
    from sandbox.mind.step2 import shared_prefix_public_producer as producer
    from sandbox.mind.step2.test_public_catchup_producer_integration_new import _read_pointer, _request
    from sandbox.mind.step2.test_shared_index_builder import public_event

    request, fixtures = _request(tmp_path, [public_event(ts_utc="2026-10-31T12:00:10Z")])
    request = replace(request, fenced=True)
    later = datetime.fromisoformat("2026-10-31T12:01:30+00:00")
    first = producer.publish_public_catchup(enabled=True, request=request, max_windows=8, cutoff_utc=later)
    _, end = _read_pointer(request, fixtures, first["pointer_sha256"])
    old_row = json.dumps(public_event(ts_utc="2026-10-31T11:55:00Z"), separators=(",", ":")).encode() + b"\n"
    with open(request.config.source.source, "ab") as stream:
        stream.write(old_row)
    anchor_doc = json.loads(request.config.source.anchor_bytes)
    anchor_doc["cursor_start"] = asdict(end.cursor)
    anchor = json.dumps(anchor_doc, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    anchor_sha = hashlib.sha256(anchor).hexdigest()
    source = replace(request.config.source, cursor=end.cursor, anchor_bytes=anchor, anchor_sha256=anchor_sha)
    resumed = replace(request, config=replace(request.config, source=source),
        pins=replace(request.pins, anchor_sha256=anchor_sha), start_prefix=end,
        expected_pointer_sha256=first["pointer_sha256"], generation="gen-b")
    earlier = datetime.fromisoformat("2026-10-31T12:00:30+00:00")
    with pytest.raises(producer.PrefixProducerRefused):
        producer.publish_public_catchup(enabled=True, request=resumed, max_windows=8, cutoff_utc=earlier)
    assert _read_pointer(request, fixtures, first["pointer_sha256"])[1] == end


@pytest.mark.skipif(os.name != "nt", reason="private checkpoint requires Windows fixed volume")
def test_partial_record_is_not_skipped_and_can_be_completed(tmp_path):
    pytest.importorskip("faiss")
    from sandbox.mind.step2 import shared_prefix_public_producer as producer
    from sandbox.mind.step2.test_public_catchup_producer_integration_new import _read_pointer, _request
    from sandbox.mind.step2.test_shared_index_builder import public_event

    request, fixtures = _request(tmp_path, [public_event(ts_utc="2026-10-31T11:58:00Z")])
    request = replace(request, fenced=True)
    pending = json.dumps(public_event(ts_utc="2026-10-31T11:59:00Z"), separators=(",", ":")).encode() + b"\n"
    with open(request.config.source.source, "ab") as stream:
        stream.write(pending[:15])
    cutoff = datetime.fromisoformat("2026-10-31T12:01:30+00:00")
    first = producer.publish_public_catchup(enabled=True, request=request, max_windows=8, cutoff_utc=cutoff)
    assert first["coverage"] == "partial_ready_prefix"
    _, end = _read_pointer(request, fixtures, first["pointer_sha256"])
    content = Path(request.config.source.source).read_bytes()
    assert end.cursor.offset == content.index(b"\n") + 1
    assert end.sha256 == hashlib.sha256(content[:end.cursor.offset]).hexdigest()
    receipt = json.loads((request.root / "gen-a" / "build_receipt.json").read_bytes())
    assert receipt["source_coverage"]["end_reason"] == "partial_record"
    with open(request.config.source.source, "ab") as stream:
        stream.write(pending[15:])
    anchor_doc = json.loads(request.config.source.anchor_bytes)
    anchor_doc["cursor_start"] = asdict(end.cursor)
    anchor = json.dumps(anchor_doc, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    anchor_sha = hashlib.sha256(anchor).hexdigest()
    source = replace(request.config.source, cursor=end.cursor, anchor_bytes=anchor, anchor_sha256=anchor_sha)
    resumed = replace(request, config=replace(request.config, source=source),
        pins=replace(request.pins, anchor_sha256=anchor_sha), start_prefix=end,
        expected_pointer_sha256=first["pointer_sha256"], generation="gen-b")
    second = producer.publish_public_catchup(enabled=True, request=resumed, max_windows=8, cutoff_utc=cutoff)
    assert second["status"] == "PUBLISHED_HISTORY"
    _, final = _read_pointer(resumed, fixtures, second["pointer_sha256"])
    completed = Path(request.config.source.source).read_bytes()
    assert final.cursor.offset == len(completed) and final.sha256 == hashlib.sha256(completed).hexdigest()
    assert len(json.loads((request.root / "gen-b" / "projection.json").read_bytes())["records"]) == 2


@pytest.mark.skipif(os.name != "nt", reason="private checkpoint requires Windows fixed volume")
def test_checkpoint_verifier_rejects_unready_record_for_receipt_cutoff(tmp_path):
    pytest.importorskip("faiss")
    from sandbox.mind.step2 import shared_prefix_public_producer as producer
    from sandbox.mind.step2.test_public_catchup_producer_integration_new import _request
    from sandbox.mind.step2.test_shared_index_builder import public_event

    request, f = _request(tmp_path, [public_event(ts_utc="2026-10-31T12:00:10Z")])
    request = replace(request, fenced=True)
    producer.publish_public_catchup(enabled=True, request=request, max_windows=8,
        cutoff_utc=datetime.fromisoformat("2026-10-31T12:01:30+00:00"))
    receipt_path = request.root / "gen-a" / "build_receipt.json"
    receipt = json.loads(receipt_path.read_bytes())
    receipt["source_coverage"]["cutoff_utc"] = "2026-10-31T12:00:30+00:00"
    receipt_path.write_text(json.dumps(receipt, sort_keys=True, indent=1), encoding="utf-8")
    with pytest.raises(checkpoint.CheckpointRefused):
        checkpoint._verify_generation(request.root, "gen-a", f.APPROVED, f.ROSTER, f.POLICY_SHA, f.ROSTER_SHA)


@pytest.mark.skipif(os.name != "nt", reason="private checkpoint requires Windows fixed volume")
def test_fenced_empty_windows_advance_one_prefix_without_skipping_public_rows(tmp_path):
    pytest.importorskip("faiss")
    from sandbox.mind.step2 import shared_prefix_public_producer as producer
    from sandbox.mind.step2.test_public_catchup_producer_integration_new import _read_pointer, _request
    from sandbox.mind.step2.test_shared_index_builder import public_event, event

    request, fixtures = _request(tmp_path, [public_event(ts_utc="2026-10-31T11:55:00Z")])
    request = replace(request, fenced=True)
    cutoff = datetime.fromisoformat("2026-10-31T12:05:00+00:00")
    first = producer.publish_public_catchup(enabled=True, request=request, max_windows=1, cutoff_utc=cutoff)
    _, pin = _read_pointer(request, fixtures, first["pointer_sha256"])
    pending = [event(ts_utc="2026-10-31T11:56:00Z"), event(ts_utc="2026-10-31T11:57:00Z"),
               public_event(ts_utc="2026-10-31T12:04:10Z", status="done")]
    with open(request.config.source.source, "ab") as stream:
        for item in pending:
            stream.write(json.dumps(item, sort_keys=True).encode("utf-8") + b"\n")
    data = Path(request.config.source.source).read_bytes()
    boundaries = [pos + 1 for pos, byte in enumerate(data) if byte == 10]
    current = first["pointer_sha256"]
    for index in range(3):
        doc = json.loads(request.config.source.anchor_bytes)
        doc["cursor_start"] = asdict(pin.cursor)
        anchor = json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
        digest = hashlib.sha256(anchor).hexdigest()
        source = replace(request.config.source, cursor=pin.cursor, anchor_bytes=anchor, anchor_sha256=digest)
        resumed = replace(request, config=replace(request.config, source=source),
            pins=replace(request.pins, anchor_sha256=digest), start_prefix=pin,
            expected_pointer_sha256=current, generation="gen-" + str(index + 2))
        with pytest.raises(producer.PrefixProducerRefused):
            producer.publish_public_catchup(enabled=True,
                request=replace(resumed, start_prefix=replace(pin, sha256="0" * 64)),
                max_windows=1, cutoff_utc=cutoff)
        with pytest.raises(producer.PrefixProducerRefused):
            producer.publish_public_catchup(enabled=True,
                request=replace(resumed, expected_pointer_sha256="0" * 64),
                max_windows=1, cutoff_utc=cutoff)
        if index == 0:
            from sandbox.mind.step2 import shared_bridge_snapshot as snapshot
            observed = snapshot.read_public_catchup(source.source, source.cursor, fixtures.APPROVED,
                anchor_bytes=source.anchor_bytes, anchor_sha256=digest, generation_path=source.generation_path,
                max_bytes=source.max_bytes, max_rows=source.max_rows, max_windows=1,
                policy_bytes=source.policy_bytes, policy_sha256=source.policy_sha256,
                roster_bytes=source.roster_bytes, roster_sha256=source.roster_sha256,
                public_policy_bytes=request.config.public_policy_bytes,
                public_policy_sha256=request.config.public_policy_sha256,
                expected_prefix_sha256=pin.sha256, max_prefix_bytes=request.max_prefix_bytes,
                enabled=True, cutoff_utc=cutoff)
            window = observed["windows"][0]
            assert window["source"]["coverage"] == "partial_window_not_complete"
            common = dict(expected_start_prefix=pin, expected_pointer_sha256=current,
                policy_bytes=source.policy_bytes, expected_policy_sha256=source.policy_sha256,
                roster_bytes=source.roster_bytes, expected_roster_sha256=source.roster_sha256,
                expected_anchor_sha256=digest, expected_mode="public-v1",
                expected_public_policy_sha256=request.config.public_policy_sha256,
                public_policy_bytes=request.config.public_policy_bytes, expected_anchor_bytes=anchor)
            with pytest.raises(checkpoint.CheckpointRefused):
                checkpoint.prepare_prefix_empty(request.root, window, fixtures.APPROVED, enabled=True, **common)
            with pytest.raises(checkpoint.CheckpointRefused):
                checkpoint.advance_prefix_empty(request.root, window, fixtures.APPROVED,
                    allow_partial_window=True, fenced=False, **common)
            with pytest.raises(checkpoint.CheckpointRefused):
                checkpoint.prepare_prefix_empty(request.root, observed, fixtures.APPROVED,
                    enabled=True, allow_partial_window=True, **common)
            legacy = dict(common, expected_mode="legacy", expected_public_policy_sha256=None,
                          public_policy_bytes=None)
            with pytest.raises(checkpoint.CheckpointRefused):
                checkpoint.prepare_prefix_empty(request.root, window, fixtures.APPROVED,
                    enabled=True, allow_partial_window=True, **legacy)
            with pytest.raises(producer.PrefixProducerRefused):
                producer.publish_public_catchup(enabled=True, request=resumed, max_windows=1)
            assert _read_pointer(resumed, fixtures, current)[1] == pin
        if index == 2:
            with pytest.raises(producer.PrefixProducerRefused):
                producer.publish_public_catchup(enabled=True, request=resumed, max_windows=1, cutoff_utc=cutoff)
            assert _read_pointer(resumed, fixtures, current)[1] == pin
            run_cutoff = datetime.fromisoformat("2026-10-31T12:05:01+00:00")
        else:
            run_cutoff = cutoff
        result = producer.publish_public_catchup(enabled=True, request=resumed, max_windows=1, cutoff_utc=run_cutoff)
        assert result["status"] == ("EMPTY_ADVANCED" if index < 2 else "PUBLISHED_HISTORY")
        current = result["pointer_sha256"]
        _, pin = _read_pointer(resumed, fixtures, current)
        assert pin.cursor.offset == boundaries[index + 1]
        assert pin.sha256 == hashlib.sha256(data[:pin.cursor.offset]).hexdigest()
    projection = json.loads((request.root / "gen-4" / "projection.json").read_bytes())
    assert len(projection["records"]) == 2


@pytest.mark.skipif(os.name != "nt", reason="private checkpoint requires Windows fixed volume")
@pytest.mark.parametrize("fault", ["before_commit", "after_commit", "after_report", "after_report_typed"])
def test_fenced_empty_fault_is_unknown_and_never_retried(tmp_path, monkeypatch, fault):
    pytest.importorskip("faiss")
    from sandbox.mind.step2 import shared_prefix_public_producer as producer
    from sandbox.mind.step2.test_public_catchup_producer_integration_new import _read_pointer, _request
    from sandbox.mind.step2.test_shared_index_builder import public_event, event

    request, fixtures = _request(tmp_path, [public_event(ts_utc="2026-10-31T11:55:00Z")])
    request = replace(request, fenced=True)
    cutoff = datetime.fromisoformat("2026-10-31T12:05:00+00:00")
    first = producer.publish_public_catchup(enabled=True, request=request, max_windows=1, cutoff_utc=cutoff)
    _, pin = _read_pointer(request, fixtures, first["pointer_sha256"])
    with open(request.config.source.source, "ab") as stream:
        stream.write(json.dumps(event(ts_utc="2026-10-31T11:56:00Z"), sort_keys=True).encode("utf-8") + b"\n")
        stream.write(json.dumps(public_event(ts_utc="2026-10-31T11:58:00Z"), sort_keys=True).encode("utf-8") + b"\n")
    doc = json.loads(request.config.source.anchor_bytes)
    doc["cursor_start"] = asdict(pin.cursor)
    anchor = json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    digest = hashlib.sha256(anchor).hexdigest()
    source = replace(request.config.source, cursor=pin.cursor, anchor_bytes=anchor, anchor_sha256=digest)
    resumed = replace(request, config=replace(request.config, source=source),
        pins=replace(request.pins, anchor_sha256=digest), start_prefix=pin,
        expected_pointer_sha256=first["pointer_sha256"], generation="gen-b")
    calls = []
    real_commit = checkpoint.advance_prefix_empty
    real_report = producer._report
    def fail_commit(*args, **kwargs):
        calls.append("commit")
        if fault == "before_commit":
            raise OSError("synthetic before commit")
        result = real_commit(*args, **kwargs)
        if fault == "after_commit":
            raise OSError("synthetic after commit")
        return result
    def fail_report(*args, **kwargs):
        result = real_report(*args, **kwargs)
        if fault == "after_report":
            raise OSError("synthetic report failure after commit")
        if fault == "after_report_typed":
            raise producer.PrefixProducerRefused("prepare_refused")
        return result
    monkeypatch.setattr(checkpoint, "advance_prefix_empty", fail_commit)
    monkeypatch.setattr(producer, "_report", fail_report)
    with pytest.raises(producer.PrefixProducerRefused, match="publication_state_unknown"):
        producer.publish_public_catchup(enabled=True, request=resumed, max_windows=1, cutoff_utc=cutoff)
    assert calls == ["commit"]
    observed = hashlib.sha256((request.root / checkpoint.POINTER).read_bytes()).hexdigest()
    assert (observed == first["pointer_sha256"]) is (fault == "before_commit")

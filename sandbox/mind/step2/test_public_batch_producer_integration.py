"""Synthetic, opt-in batched public producer through real FAISS and fenced checkpoint."""
from dataclasses import asdict, replace
from datetime import datetime
import hashlib
import json
import os

import pytest

pytestmark = pytest.mark.skipif(os.name != "nt", reason="private checkpoint requires a Windows fixed volume")


@pytest.mark.parametrize("batch", [False, True], ids=["legacy_default", "batch_true"])
def test_fenced_producer_batch_and_default_bind_cutoff_to_checkpoint(tmp_path, monkeypatch, batch):
    pytest.importorskip("faiss")
    from sandbox.mind.step2 import shared_bridge_snapshot as snapshot, shared_prefix_public_producer as producer
    from sandbox.mind.step2 import prefix_private_state
    from sandbox.mind.step2.test_public_catchup_producer_integration_new import _read_pointer, _request
    from sandbox.mind.step2.test_shared_index_builder import public_event

    rows = [public_event(ts_utc="2026-10-31T11:58:00Z"),
            public_event(ts_utc="2026-10-31T11:59:00Z", agent="fable-5"),
            public_event(ts_utc="2026-10-31T12:00:15Z", to="fable-5", message="synthetic-private"),
            public_event(ts_utc="2026-10-31T12:01:10Z"),
            public_event(ts_utc="2026-10-31T11:57:00Z")]
    request, fixtures = _request(tmp_path, rows)
    source = replace(request.config.source, max_rows=100)
    request = replace(request, config=replace(request.config, source=source), fenced=True)
    cutoff = datetime.fromisoformat("2026-10-31T12:01:30+00:00")
    actual = []
    real_catchup = snapshot.read_public_catchup

    def capture(*args, **kwargs):
        result = real_catchup(*args, **kwargs)
        actual.append((kwargs.get("batch_rows", False), result))
        return result

    monkeypatch.setattr(snapshot, "read_public_catchup", capture)
    options = {"batch_rows": True} if batch else {}
    response = producer.publish_public_catchup(enabled=True, request=request, max_windows=1,
                                                cutoff_utc=cutoff, **options)
    assert response["status"] == "PUBLISHED_GENESIS" and response["coverage"] == "partial_ready_prefix"
    assert len(actual) == 1 and actual[0][0] is batch
    chain = actual[0][1]
    assert chain["end_reason"] == ("deferred" if batch else "max_windows")
    assert chain.get("batch_rows", False) is batch
    _, committed = _read_pointer(request, fixtures, response["pointer_sha256"])
    with open(source.source, "rb") as stream:
        raw = stream.read()
    lines = raw.splitlines(keepends=True)
    end = sum(map(len, lines[:3 if batch else 1]))
    assert committed == chain["candidate_prefix"] and committed.cursor.offset == end
    assert committed.sha256 == hashlib.sha256(raw[:end]).hexdigest()
    with prefix_private_state.locked_state(request.private_state_root, request.root, enabled=True) as state:
        assert state.load(response["pointer_sha256"]) == committed
    projection = json.loads((request.root / "gen-a" / "projection.json").read_bytes())
    assert len(projection["records"]) == (2 if batch else 1)
    assert "synthetic-private" not in repr(projection) and "12:01:00" not in repr(projection)
    receipt = json.loads((request.root / "gen-a" / "build_receipt.json").read_bytes())
    assert receipt["source_coverage"]["cutoff_utc"] == cutoff.isoformat()
    if batch:
        from sandbox.mind.step2 import shared_index_builder as builder, shared_index_checkpoint as checkpoint

        bound = {"catchup_result": chain, "expected_anchor_sha256": request.pins.anchor_sha256,
                 "mode": "public-catchup-partial-v1", "public_policy_bytes": request.config.public_policy_bytes,
                 "public_policy_sha256": request.pins.public_policy_sha256, "anchor_bytes": source.anchor_bytes,
                 "expected_start_prefix": request.start_prefix, "expected_cutoff_utc": cutoff,
                 "expected_batch_rows": True}
        build_args = dict(enabled=True, policy_bytes=source.policy_bytes, policy_sha256=request.pins.policy_sha256,
                          roster_bytes=source.roster_bytes, roster_sha256=request.pins.roster_sha256)
        projected, verified = builder.verified_partial_catchup_projection(bound, **build_args)
        assert len(projected["records"]) == 2 and verified["candidate_prefix"] == committed
        checkpoint_args = dict(policy_bytes=source.policy_bytes, expected_policy_sha256=request.pins.policy_sha256,
            roster_bytes=source.roster_bytes, expected_roster_sha256=request.pins.roster_sha256,
            expected_anchor_sha256=request.pins.anchor_sha256,
            expected_public_policy_sha256=request.pins.public_policy_sha256,
            public_policy_bytes=request.config.public_policy_bytes, expected_anchor_bytes=source.anchor_bytes,
            expected_start_prefix=request.start_prefix)
        _, verified2 = checkpoint._checked_catchup_delta(bound, chain, request.start_prefix.cursor,
                                                            committed.cursor, **checkpoint_args)
        assert verified2["candidate_prefix"] == committed
        for changed in (dict(bound, expected_batch_rows=False),
                        dict(bound, expected_cutoff_utc=datetime.fromisoformat("2026-10-31T12:02:01+00:00")),
                        {key: value for key, value in bound.items() if key != "expected_batch_rows"}):
            with pytest.raises(builder.BuildRefused):
                builder.verified_partial_catchup_projection(changed, **build_args)
            with pytest.raises(builder.BuildRefused):
                checkpoint._checked_catchup_delta(changed, chain, request.start_prefix.cursor,
                                                    committed.cursor, **checkpoint_args)

        anchor_doc = json.loads(source.anchor_bytes)
        anchor_doc["cursor_start"] = asdict(committed.cursor)
        anchor_raw = json.dumps(anchor_doc, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
        anchor_sha = hashlib.sha256(anchor_raw).hexdigest()
        next_source = replace(source, cursor=committed.cursor, anchor_bytes=anchor_raw, anchor_sha256=anchor_sha)
        resumed = replace(request, config=replace(request.config, source=next_source),
                          pins=replace(request.pins, anchor_sha256=anchor_sha), start_prefix=committed,
                          expected_pointer_sha256=response["pointer_sha256"], generation="gen-b")
        later = datetime.fromisoformat("2026-10-31T12:02:01+00:00")
        second = producer.publish_public_catchup(enabled=True, request=resumed, max_windows=1,
                                                 cutoff_utc=later, batch_rows=True)
        assert second["status"] == "PUBLISHED_HISTORY"
        assert second["coverage"] == "caught_up_to_final_snapshot_not_history"
        _, final = _read_pointer(resumed, fixtures, second["pointer_sha256"])
        assert final.cursor.offset == len(raw) and final.sha256 == hashlib.sha256(raw).hexdigest()
        final_projection = json.loads((request.root / "gen-b" / "projection.json").read_bytes())
        assert len(final_projection["records"]) == 4 and "synthetic-private" not in repr(final_projection)


@pytest.mark.parametrize("fault", ["missing_cutoff", "unfenced", "non_boolean", "wrong_pointer"])
def test_batch_requires_explicit_fenced_cutoff_and_exact_cas(tmp_path, fault):
    pytest.importorskip("faiss")
    from sandbox.mind.step2 import shared_index_checkpoint as checkpoint, shared_prefix_public_producer as producer
    from sandbox.mind.step2.test_public_catchup_producer_integration_new import _request
    from sandbox.mind.step2.test_shared_index_builder import public_event

    request, _ = _request(tmp_path, [public_event(ts_utc="2026-10-31T11:58:00Z")])
    request = replace(request, fenced=fault != "unfenced",
                      expected_pointer_sha256="0" * 64 if fault == "wrong_pointer" else None)
    cutoff = None if fault == "missing_cutoff" else datetime.fromisoformat("2026-10-31T12:01:30+00:00")
    batch = 1 if fault == "non_boolean" else True
    with pytest.raises(producer.PrefixProducerRefused):
        producer.publish_public_catchup(enabled=True, request=request, max_windows=1, cutoff_utc=cutoff,
                                        batch_rows=batch)
    assert not (request.root / checkpoint.POINTER).exists()


@pytest.mark.parametrize("tamper_case", ["valid", "changed_after_producer_check", "strip_context",
                                         "change_batch", "changed_under_lock", "changed_after_prepare"])
def test_batch_private_only_prefix_advances_fenced_checkpoint_before_young_row(tmp_path, monkeypatch, tamper_case):
    pytest.importorskip("faiss")
    from sandbox.mind.step2 import (prefix_private_state, shared_bridge_snapshot as snapshot,
                                    shared_index_checkpoint as checkpoint, shared_prefix_public_producer as producer)
    from sandbox.mind.step2.test_public_catchup_producer_integration_new import _read_pointer, _request
    from sandbox.mind.step2.test_shared_index_builder import public_event

    request, fixtures = _request(tmp_path, [public_event(ts_utc="2026-10-31T11:58:00Z")])
    source = replace(request.config.source, max_rows=100)
    request = replace(request, config=replace(request.config, source=source), fenced=True)
    cutoff = datetime.fromisoformat("2026-10-31T12:01:30+00:00")
    first = producer.publish_public_catchup(enabled=True, request=request, max_windows=1,
                                            cutoff_utc=cutoff, batch_rows=True)
    _, start = _read_pointer(request, fixtures, first["pointer_sha256"])
    new_rows = [public_event(ts_utc="2026-10-31T11:59:00Z", to="fable-5", message="private-a"),
                public_event(ts_utc="2026-10-31T12:00:00Z", to="fable-5", message="private-b"),
                public_event(ts_utc="2026-10-31T12:01:10Z"),
                public_event(ts_utc="2026-10-31T11:57:00Z")]
    lines = [json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii") + b"\n"
             for row in new_rows]
    with open(source.source, "ab") as stream:
        stream.write(b"".join(lines))
    anchor_doc = json.loads(source.anchor_bytes)
    anchor_doc["cursor_start"] = asdict(start.cursor)
    anchor_raw = json.dumps(anchor_doc, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    anchor_sha = hashlib.sha256(anchor_raw).hexdigest()
    next_source = replace(source, cursor=start.cursor, anchor_bytes=anchor_raw, anchor_sha256=anchor_sha)
    resumed = replace(request, config=replace(request.config, source=next_source),
                      pins=replace(request.pins, anchor_sha256=anchor_sha), start_prefix=start,
                      expected_pointer_sha256=first["pointer_sha256"], generation="gen-b")
    if tamper_case != "valid":
        from contextlib import contextmanager

        captured = []
        read = snapshot.read_public_catchup
        prepare = checkpoint.prepare_prefix_empty
        commit = checkpoint.advance_prefix_empty
        slot = checkpoint.reader_slot

        def capture(*args, **kwargs):
            value = read(*args, **kwargs)
            captured.append(value)
            return value

        def change_chain_before_checkpoint(*args, **kwargs):
            if tamper_case == "strip_context":
                kwargs.pop("batch_empty_context", None)
            elif tamper_case == "change_batch":
                kwargs["batch_empty_context"]["batch_rows"] = False
            else:
                captured[0]["cutoff_utc"] = "2026-10-31T12:02:01+00:00"
            return prepare(*args, **kwargs)

        monkeypatch.setattr(snapshot, "read_public_catchup", capture)
        if tamper_case in ("changed_after_producer_check", "strip_context", "change_batch"):
            monkeypatch.setattr(checkpoint, "prepare_prefix_empty", change_chain_before_checkpoint)
        elif tamper_case == "changed_after_prepare":
            def changed_before_commit(*args, **kwargs):
                captured[0]["cutoff_utc"] = "2026-10-31T12:02:01+00:00"
                return commit(*args, **kwargs)
            monkeypatch.setattr(checkpoint, "advance_prefix_empty", changed_before_commit)
        else:
            @contextmanager
            def changed_while_holding_slot(*args, **kwargs):
                with slot(*args, **kwargs):
                    captured[0]["cutoff_utc"] = "2026-10-31T12:02:01+00:00"
                    yield
            monkeypatch.setattr(checkpoint, "reader_slot", changed_while_holding_slot)
        with pytest.raises(producer.PrefixProducerRefused):
            producer.publish_public_catchup(enabled=True, request=resumed, max_windows=1,
                                            cutoff_utc=cutoff, batch_rows=True)
        _, unchanged = _read_pointer(request, fixtures, first["pointer_sha256"])
        assert unchanged == start
        if tamper_case == "changed_after_prepare":
            # Commit failed after the intent/fence; no unsafe implicit recovery.
            assert (request.root / checkpoint.FENCE).is_file()
        return
    second = producer.publish_public_catchup(enabled=True, request=resumed, max_windows=1,
                                             cutoff_utc=cutoff, batch_rows=True)
    assert second["status"] == "EMPTY_ADVANCED" and second["coverage"] == "partial_ready_prefix"
    _, end = _read_pointer(resumed, fixtures, second["pointer_sha256"])
    with open(source.source, "rb") as stream:
        raw = stream.read()
    expected_end = start.cursor.offset + len(lines[0]) + len(lines[1])
    assert end.cursor.offset == expected_end and end.sha256 == hashlib.sha256(raw[:expected_end]).hexdigest()
    with prefix_private_state.locked_state(request.private_state_root, request.root, enabled=True) as state:
        assert state.load(second["pointer_sha256"]) == end

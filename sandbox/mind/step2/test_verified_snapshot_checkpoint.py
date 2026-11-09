"""Synthetic checkpoint-to-Q4 snapshots, using a fake FAISS backend only."""
import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from sandbox.mind.step2 import cell, memory, q4, shared_index_checkpoint as checkpoint, shared_projection as projection
from sandbox.mind.step2.test_status_public_filter import ROSTER
from waggledance.core.bridge_log_reader import BridgeCursor


def enc(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def sha(value):
    return hashlib.sha256(value).hexdigest()


def make_case(monkeypatch, variant=None):
    import core.faiss_store as store
    tasks = frozenset({"public-task", "private-task"})
    policy_bytes = enc({"schema": "wd.q4-non-review-policy.v1", "approved_tasks": sorted(tasks)})
    roster_bytes = enc({"lanes": [{"agent": k, "agent_uuid": v} for k, v in ROSTER.items() if k != "codex-tools-1"],
                        "tools_supervisor": {"agent": "codex-tools-1", "agent_uuid": ROSTER["codex-tools-1"]}})
    policy_sha, roster_sha = sha(policy_bytes), sha(roster_bytes)
    events = [{"ts_utc": f"2026-10-20T12:0{i}:00Z", "agent": "codex-lead-1",
               "agent_uuid": ROSTER["codex-lead-1"], "type": "message", "status": "done",
               "task_id": "public-task"} for i in range(4)]
    if variant == "private_task":
        events[0]["task_id"] = "private-task"
    if variant == "private_type":
        events[0]["type"] = "claim"
    if variant == "fable_agent":
        events[0]["agent"] = "fable-5"
        events[0]["agent_uuid"] = ROSTER["fable-5"]
    projected = projection.project_events(events, approved_tasks=tasks, roster=ROSTER, exclude_review_signal=True)
    if variant == "review_projection":
        projected["records"][0]["meta"]["agent"] = "claude-rco-1"
    projection_raw = json.dumps(projected, sort_keys=True, indent=1).encode()
    records = projected["records"]
    ids = [f"{record['record_id']}#{view}" for record in records for view in ("w", "d")]
    vectors = np.ones((len(ids), 1), dtype=np.float32)
    encoder = {"model": "synthetic", "revision": "123", "weights_sha256": "b" * 64}
    rows = [{"record_id": f"{record['record_id']}#{view}", "kind": "event", "time_utc": record["time_utc"],
             "cell_id": "learning", "provenance": dict(record["provenance"], record=record["record_id"], view=view)}
            for record in records for view in ("w", "d")]
    if variant == "row_kind":
        rows[0]["kind"] = "lesson"
    if variant == "row_extra_private":
        rows[0]["private_synthetic_sentinel"] = "must-not-leak"
    if variant == "wrong_event_ref":
        rows[0]["provenance"]["event_ref"] = "0" * 64
    if variant == "duplicate_view":
        rows[1]["provenance"]["view"] = "w"
    texts = [record["stored_text"] for record in records for _ in ("w", "d")]
    if variant == "text":
        texts[0] = "synthetic mismatched content"
    if variant == "missing_view":
        ids, vectors, rows, texts = ids[:-1], vectors[:-1], rows[:-1], texts[:-1]
    metadata = [{} for _ in ids]
    if variant == "metadata_extra_private":
        metadata[0]["private_synthetic_sentinel"] = "must-not-leak"
    data = {"index": b"fake-index", "vectors": vectors.astype("<f4").tobytes(),
            "meta": enc({"dim": 1, "doc_ids": ids, "texts": texts, "metadata": metadata}),
            "provenance": b"\n".join(map(enc, rows)) + b"\n"}
    cell_encoder = dict(encoder)
    if variant == "encoder":
        cell_encoder["revision"] = "different"
    content = {"cell_id": "learning", "count": len(ids), "dim": 1, "encoder": cell_encoder,
               "cutoff_utc": "2027-01-01T00:00:00Z", "files": {key: sha(raw) for key, raw in data.items()},
               "ids_sha256": sha("\n".join(ids).encode()), "pairs_sha256": cell.pairs_sha256(ids, vectors)}
    cell_receipt = {"schema": cell.SCHEMA, "content": content,
                    "content_sha256": sha(cell.canonical(content).encode())}
    if variant == "receipt_extra_private":
        cell_receipt["private_synthetic_sentinel"] = "must-not-leak"
    data["receipt"] = enc(cell_receipt)
    cell_sha = sha(data["receipt"])
    cell_pointer = {"generation": 1, "seq": 1, "receipt_sha256": cell_sha}
    cell_log = {"schema": cell.LOG_SCHEMA, "prev_line_sha256": cell.ZERO, **cell_pointer}
    cell_snapshots = {"learning": {"pointer": enc(cell_pointer), "log": enc(cell_log) + b"\n", "artifacts": data}}
    public_policy_bytes = enc({"schema": "wd.mind-public-status-policy.v1", "public_tasks": ["public-task"],
                               "public_types": ["message"]})
    public_policy_sha = sha(public_policy_bytes)
    mode = {"status_mode": "public-v1", "public_policy_sha256": public_policy_sha}
    policy = {"authorship": projection.AUTHORSHIP_ROSTER, "review_signal_excluded": True,
              "policy_sha256": policy_sha, "roster_sha256": roster_sha}
    if variant == "policy":
        policy["authorship"] = "not-verified"
    memory_doc = {"schema": memory.SCHEMA, "scope": "wd.q4-shared-projected-events.v1", "dim": 1,
                  "cells": {"learning": {"generation": 1, "receipt_sha256": cell_sha}},
                  "projection_schema": projection.SCHEMA, "projection_sha256": sha(projection_raw),
                  "policy": policy, "source_mode": mode}
    memory_raw = enc(memory_doc)
    descriptor = {"schema": "wd.q4-shared-memory-descriptor.v2", "scope": memory_doc["scope"],
                  "memory_sha256": sha(memory_raw), "dim": 1, "encoder": encoder}
    from sandbox.mind.step2 import wd_q4_private_read as reader
    descriptor["schema"] = reader.DESCRIPTOR_SCHEMA
    descriptor_raw = enc(descriptor)
    cursor = BridgeCursor(offset=0, file_identity="posix-v1:1:2", generation=None)
    anchor_sha = "e" * 64
    binding = checkpoint._source_binding(cursor, cursor, anchor_sha, sha(projection_raw), policy_sha, roster_sha)
    if variant == "source_binding":
        binding["anchor_sha256"] = "0" * 64
    build = {"schema": "wd.bridge-mind-shared-index-build-receipt.v1", "scope": memory_doc["scope"],
             "descriptor_schema": reader.DESCRIPTOR_SCHEMA, "projection_schema": projection.SCHEMA,
             "projection_file": "projection.json", "encoder": encoder, "dim": 1, "memory_name": "gen-a",
             "memory_sha256": sha(memory_raw), "descriptor_sha256": sha(descriptor_raw),
             "projection_sha256": sha(projection_raw), "policy": policy, "source_mode": mode,
             "source_binding": binding}
    if variant == "source_mode":
        build["source_mode"] = {"status_mode": "legacy", "public_policy_sha256": None}
    if variant == "receipt_dim":
        build["dim"] = 2
    if variant == "projection_schema":
        build["projection_schema"] = "wrong"
    active = {"descriptor.json": descriptor_raw, "memory.json": memory_raw,
              "projection.json": projection_raw, "build_receipt.json": enc(build)}
    pointer = {"schema": checkpoint.PREFIX_SCHEMA, "generation": "gen-a",
               "artifacts": {key: sha(active[name]) for key, name in checkpoint.ARTIFACTS.items()},
               "policy_sha256": policy_sha, "roster_sha256": roster_sha, "anchor_sha256": anchor_sha,
               "cursor_start": asdict(cursor), "cursor_end": asdict(cursor), "watermark": asdict(cursor),
               "prefix_start_sha256": "d" * 64, "prefix_watermark_sha256": "d" * 64,
               "empty_advance": None, "previous": None, "previous_pointer_sha256": None}
    pointer_raw = enc(pointer)

    class FakeIndex:
        ntotal, d, metric_type = len(ids), 1, 0
        def __init__(self, dim=1):
            pass
        def reconstruct(self, index):
            return vectors[index]
        def reconstruct_n(self, start, count):
            return vectors[start:start + count]

    monkeypatch.setattr(store.faiss, "IndexFlatIP", FakeIndex, raising=False)
    monkeypatch.setattr(store.faiss, "METRIC_INNER_PRODUCT", 0, raising=False)
    monkeypatch.setattr(store.faiss, "deserialize_index", lambda data: FakeIndex(), raising=False)
    return dict(pointer_bytes=pointer_raw, expected_pointer_sha256=sha(pointer_raw), approved_tasks=tasks,
                policy_bytes=policy_bytes, policy_sha256=policy_sha, roster_bytes=roster_bytes,
                roster_sha256=roster_sha, expected_public_policy_sha256=public_policy_sha,
                public_policy_bytes=public_policy_bytes, expected_source_sha256=anchor_sha,
                expected_encoder_sha256=sha(checkpoint._canonical(encoder)),
                active_artifact_bytes=active, cell_snapshots=cell_snapshots, history_bytes={}, enabled=True)


def _preflight(args):
    from sandbox.mind.step2.shared_capture_adapter import strict_pins
    tasks, roster = strict_pins(args["policy_bytes"], args["policy_sha256"], args["roster_bytes"], args["roster_sha256"])
    pointer = checkpoint._parse_pointer_snapshot(args["pointer_bytes"],
        expected_pointer_sha256=args["expected_pointer_sha256"], policy_sha=args["policy_sha256"],
        roster_sha=args["roster_sha256"], prefix_required=True)
    active = checkpoint._bind_active_artifact_bytes(args["active_artifact_bytes"],
                                                     expected_hashes=pointer["artifacts"], enabled=True)
    return checkpoint._preflight_public_snapshot(pointer, active, tasks, roster,
        public_policy_bytes=args["public_policy_bytes"], public_policy_sha256=args["expected_public_policy_sha256"],
        expected_source_sha256=args["expected_source_sha256"], expected_encoder_sha256=args["expected_encoder_sha256"])


def test_public_preflight_no_io_but_checkpoint_stays_fail_closed(monkeypatch):
    from sandbox.mind.step2 import wd_q4_private_read as reader
    from tools import wd_q4_public_pointer_resolver, wd_q4_public_query  # Load code before the I/O guard.
    args = make_case(monkeypatch)
    def no_io(*a, **kw):
        pytest.fail("snapshot attempted disk I/O")
    with monkeypatch.context() as guard:
        for attr in ("open", "read_bytes", "exists", "is_dir"):
            guard.setattr(Path, attr, no_io)
        guard.setattr(checkpoint, "_read", no_io)
        guard.setattr(checkpoint, "_secure", no_io)
        guard.setattr(reader, "read_descriptor", no_io)
        guard.setattr(reader, "bind_memory", no_io)
        guard.setattr(cell, "file_sha256", no_io)
        guard.setattr(cell, "verify_generation", no_io)
        assert _preflight(args) is None
        with pytest.raises(checkpoint.CheckpointRefused):
            checkpoint.read_verified_snapshot_checkpoint(**args)


@pytest.mark.parametrize("variant", ["private_task", "private_type"])
def test_snapshot_rejects_general_nonreview_data_not_approved_for_public(monkeypatch, variant):
    args = make_case(monkeypatch, variant)
    with pytest.raises(checkpoint.CheckpointRefused):
        _preflight(args)


@pytest.mark.parametrize("fault", ["missing_source", "wrong_source", "mutable_source", "missing_encoder",
                                   "wrong_encoder", "mutable_encoder", "wrong_policy_hash", "malformed_policy",
                                   "missing_policy", "mutable_policy"])
def test_public_preflight_rejects_untrusted_pins_and_policy(monkeypatch, fault):
    args = make_case(monkeypatch)
    if fault.endswith("source"):
        args["expected_source_sha256"] = {"missing_source": None, "wrong_source": "0" * 64,
                                           "mutable_source": [args["expected_source_sha256"]]}[fault]
    elif fault.endswith("encoder"):
        args["expected_encoder_sha256"] = {"missing_encoder": None, "wrong_encoder": "0" * 64,
                                            "mutable_encoder": [args["expected_encoder_sha256"]]}[fault]
    elif fault == "wrong_policy_hash":
        args["expected_public_policy_sha256"] = "0" * 64
    elif fault == "malformed_policy":
        args["public_policy_bytes"] = enc({"schema": "bad", "private_sentinel": "secret"})
        args["expected_public_policy_sha256"] = sha(args["public_policy_bytes"])
    elif fault == "missing_policy":
        args["public_policy_bytes"] = None
    else:
        args["public_policy_bytes"] = bytearray(args["public_policy_bytes"])
    with pytest.raises(checkpoint.CheckpointRefused) as caught:
        _preflight(args)
    assert caught.value.__context__ is None and "secret" not in str(caught.value)


def test_public_preflight_honors_narrowed_agent_allowlist(monkeypatch):
    from tools import wd_q4_public_pointer_resolver as resolver
    args = make_case(monkeypatch, "fable_agent")
    monkeypatch.setattr(resolver, "_PUBLIC_AGENTS", frozenset({"codex-lead-1", "codex-tools-1"}))
    with pytest.raises(checkpoint.CheckpointRefused):
        _preflight(args)


def test_even_valid_public_input_refuses_before_any_native_call(monkeypatch):
    import core.faiss_store as store
    args = make_case(monkeypatch)
    monkeypatch.setattr(store.faiss, "IndexFlatIP", lambda *a: pytest.fail("native constructor reached"))
    monkeypatch.setattr(store.faiss, "deserialize_index", lambda *a: pytest.fail("native deserializer reached"))
    assert _preflight(args) is None
    with pytest.raises(checkpoint.CheckpointRefused):
        checkpoint.read_verified_snapshot_checkpoint(**args)


@pytest.mark.parametrize("target,field", [
    ("read_verified_snapshot_checkpoint", "expected_source_sha256"),
    ("read_verified_snapshot_checkpoint", "expected_encoder_sha256"),
    ("read_verified_snapshot_checkpoint", "enabled"),
    ("_preflight_public_snapshot", "expected_source_sha256"),
    ("_preflight_public_snapshot", "expected_encoder_sha256"),
    ("_preflight_public_snapshot", "expected_public_policy_sha256"),
])
def test_refusal_frame_locals_do_not_retain_invalid_private_inputs(monkeypatch, target, field):
    import traceback
    args = make_case(monkeypatch)
    sentinel = "synthetic-private-input-sentinel-" + field
    args[field] = sentinel
    with pytest.raises(checkpoint.CheckpointRefused) as caught:
        _preflight(args) if target == "_preflight_public_snapshot" else checkpoint.read_verified_snapshot_checkpoint(**args)
    frames = [frame for frame, _ in traceback.walk_tb(caught.value.__traceback__)
              if frame.f_code.co_name == target]
    assert len(frames) == 1
    assert all(value is not sentinel for value in frames[0].f_locals.values())


@pytest.mark.parametrize("variant", ["row_extra_private", "metadata_extra_private", "receipt_extra_private"])
def test_snapshot_never_returns_memory_with_private_unchecked_fields(monkeypatch, variant):
    args = make_case(monkeypatch, variant)
    with pytest.raises(checkpoint.CheckpointRefused):
        checkpoint.read_verified_snapshot_checkpoint(**args)


@pytest.mark.parametrize("variant", ["policy", "encoder", "row_kind", "text", "wrong_event_ref", "duplicate_view",
                                      "missing_view", "source_binding", "source_mode", "receipt_dim",
                                      "review_projection", "projection_schema"])
def test_snapshot_checkpoint_refuses_rehashed_semantic_mismatch(monkeypatch, variant):
    args = make_case(monkeypatch, variant)
    with pytest.raises(checkpoint.CheckpointRefused):
        checkpoint.read_verified_snapshot_checkpoint(**args)


@pytest.mark.parametrize("fault", ["pointer", "artifact", "cell_log", "policy", "roster", "history",
                                   "archive_missing", "disabled"])
def test_snapshot_checkpoint_refuses_tamper_or_missing_pin(monkeypatch, fault):
    args = make_case(monkeypatch)
    if fault == "pointer":
        args["expected_pointer_sha256"] = "0" * 64
    elif fault == "artifact":
        args["active_artifact_bytes"]["memory.json"] += b" "
    elif fault == "cell_log":
        args["cell_snapshots"]["learning"]["log"] = b"bad"
    elif fault == "policy":
        args["policy_sha256"] = "0" * 64
    elif fault == "roster":
        args["roster_sha256"] = "0" * 64
    elif fault == "history":
        args["history_bytes"] = {"../bad": b"bad"}
    elif fault == "archive_missing":
        pointer = json.loads(args["pointer_bytes"])
        pointer.update(previous="old", previous_pointer_sha256="0" * 64)
        args["pointer_bytes"] = enc(pointer)
        args["expected_pointer_sha256"] = sha(args["pointer_bytes"])
    else:
        args["enabled"] = False
    with pytest.raises(checkpoint.CheckpointRefused):
        checkpoint.read_verified_snapshot_checkpoint(**args)


@pytest.mark.parametrize("variant", ["policy", "source_binding", "source_mode", "receipt_dim",
                                      "review_projection", "projection_schema"])
def test_invalid_active_controls_refuse_before_faiss(monkeypatch, variant):
    import core.faiss_store as store
    args = make_case(monkeypatch, variant)
    monkeypatch.setattr(store.faiss, "IndexFlatIP", lambda *a: pytest.fail("native constructor reached"))
    monkeypatch.setattr(store.faiss, "deserialize_index", lambda *a: pytest.fail("native deserializer reached"))
    with pytest.raises(checkpoint.CheckpointRefused):
        checkpoint.read_verified_snapshot_checkpoint(**args)


def test_snapshot_checkpoint_respects_aggregate_budget_before_faiss(monkeypatch):
    import core.faiss_store as store
    args = make_case(monkeypatch)
    monkeypatch.setattr(checkpoint, "_MAX_VERIFIED_SNAPSHOT_BYTES", 1)
    monkeypatch.setattr(store.faiss, "IndexFlatIP", lambda *a: pytest.fail("native constructor reached"))
    monkeypatch.setattr(store.faiss, "deserialize_index", lambda *a: pytest.fail("native deserializer reached"))
    with pytest.raises(checkpoint.CheckpointRefused):
        checkpoint.read_verified_snapshot_checkpoint(**args)


def test_malformed_snapshot_pointer_refusal_has_no_raw_exception_context(monkeypatch):
    args = make_case(monkeypatch)
    args["pointer_bytes"] = b'{"synthetic-private-sentinel":"unfinished'
    args["expected_pointer_sha256"] = sha(args["pointer_bytes"])
    with pytest.raises(checkpoint.CheckpointRefused) as caught:
        checkpoint.read_verified_snapshot_checkpoint(**args)
    assert caught.value.__context__ is None and caught.value.__cause__ is None


@pytest.mark.parametrize("broken", [False, True])
def test_snapshot_checkpoint_verifies_bound_prefix_history_without_disk(monkeypatch, broken):
    from sandbox.mind.step2.shared_bridge_snapshot import _token
    from sandbox.mind.step2.shared_capture_adapter import strict_pins
    args = make_case(monkeypatch)
    previous_raw = args["pointer_bytes"]
    pointer = json.loads(previous_raw)
    tasks, roster = strict_pins(args["policy_bytes"], args["policy_sha256"], args["roster_bytes"], args["roster_sha256"])
    start = BridgeCursor(**pointer["watermark"])
    end = BridgeCursor(offset=start.offset + 1, file_identity=start.file_identity, generation=start.generation)
    mode = {"status_mode": "public-v1", "public_policy_sha256": args["expected_public_policy_sha256"]}
    pointer.update(previous=pointer["generation"], previous_pointer_sha256=sha(previous_raw), watermark=asdict(end),
                   prefix_start_sha256="0" * 64 if broken else pointer["prefix_watermark_sha256"],
                   prefix_watermark_sha256="c" * 64,
                   empty_advance={"schema": "wd.q4-empty-watermark.v2", "anchor_sha256": "a" * 64,
                       "projection_sha256": sha(checkpoint._canonical(projection._projection([], {}, 0, roster, True))),
                       "cursor_start_sha256": sha(_token(start).encode()), "cursor_end_sha256": sha(_token(end).encode()),
                       "policy_sha256": args["policy_sha256"], "roster_sha256": args["roster_sha256"], "source_mode": mode})
    args["pointer_bytes"] = enc(pointer)
    args["expected_pointer_sha256"] = sha(args["pointer_bytes"])
    args["history_bytes"] = {"q4-pointer-" + sha(previous_raw) + ".json": previous_raw}
    monkeypatch.setattr(checkpoint, "_read", lambda *a, **kw: pytest.fail("disk fallback"))
    pointer = checkpoint._parse_pointer_snapshot(args["pointer_bytes"],
        expected_pointer_sha256=args["expected_pointer_sha256"], policy_sha=args["policy_sha256"],
        roster_sha=args["roster_sha256"], prefix_required=True)
    def validate_history():
        return checkpoint._validate_pointer_history(None, args["pointer_bytes"], pointer, tasks, roster,
            args["policy_sha256"], args["roster_sha256"], policy_bytes=args["policy_bytes"],
            roster_bytes=args["roster_bytes"], expected_source_mode=checkpoint._requested_source_mode(
                "public-v1", args["expected_public_policy_sha256"]), prefix_required=True,
            snapshot_bytes=args["history_bytes"])
    if broken:
        with pytest.raises(checkpoint.CheckpointRefused):
            validate_history()
    else:
        validate_history()
        assert _preflight(args) is None
    with pytest.raises(checkpoint.CheckpointRefused):
        checkpoint.read_verified_snapshot_checkpoint(**args)

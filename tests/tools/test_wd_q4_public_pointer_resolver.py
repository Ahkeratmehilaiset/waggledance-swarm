"""Synthetic contract checks; no live publisher, model, or fleet required."""

import hashlib
import json
import os
from pathlib import Path

import pytest

from tools import wd_q4_public_pointer_resolver as resolver


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


@pytest.fixture
def simulated(monkeypatch, tmp_path):
    pointer = b"caller-pinned-pointer"
    descriptor = b"already-verified-descriptor"
    projection = json.dumps({"records": [{"meta": {"task_id": "public-task", "type": "message"}}]}).encode()
    values = {"q4-latest.json": pointer, "descriptor.json": descriptor, "projection.json": projection}
    artifacts = {"descriptor_sha256": sha(descriptor), "memory_sha256": "a" * 64,
                 "projection_sha256": sha(projection), "receipt_sha256": "b" * 64}
    view = {"pointer_sha256": sha(pointer), "generation": "generation-1", "artifacts": artifacts,
            "anchor_sha256": "c" * 64, "cursor_end_sha256": "d" * 64, "watermark_sha256": "e" * 64}
    seen = []
    monkeypatch.setattr(resolver.adapter, "strict_pins", lambda *a: (frozenset({"public-task"}), {}))
    monkeypatch.setattr(resolver.adapter, "public_status_policy",
                        lambda *a: (frozenset({"public-task"}), frozenset({"message"})))
    monkeypatch.setattr(resolver.checkpoint, "_secure", lambda path: path)
    monkeypatch.setattr(resolver.checkpoint, "_read", lambda path, *, max_bytes: values[path.name])
    def verify(root, tasks, **kwargs):
        seen.append(kwargs)
        return view
    monkeypatch.setattr(resolver.checkpoint, "read_verified_pointer", verify)
    return {"root": tmp_path, "expected_pointer_sha256": sha(pointer), "policy_bytes": b"policy",
            "policy_sha256": "1" * 64, "roster_bytes": b"roster", "roster_sha256": "2" * 64,
            "public_policy_bytes": b"public", "public_policy_sha256": "3" * 64}, values, view, seen


def test_disabled_without_opt_in(simulated):
    args, _, _, seen = simulated
    with pytest.raises(resolver.PublicPointerRefused, match="^disabled$"):
        resolver.resolve_public_pointer(**args)
    assert seen == []


def test_exact_caller_pin_and_public_mode_produce_reader_binding(simulated):
    args, _, view, seen = simulated
    result = resolver.resolve_public_pointer(**args, enabled=True)
    assert seen[0]["expected_pointer_sha256"] == args["expected_pointer_sha256"]
    assert seen[0]["expected_mode"] == "public-v1"
    assert seen[0]["expected_public_policy_sha256"] == args["public_policy_sha256"]
    assert result["descriptor_sha256"] == view["artifacts"]["descriptor_sha256"]
    assert result["memory_root"].endswith("generation-1")
    assert result["source_mode"] == "public-v1"
    assert result["freshness"] == "UNKNOWN"
    assert result["identity"] == "NOT_AUTHENTICATED"
    assert result["publisher_authentication"] == "NOT_AUTHENTICATED"
    assert result["source_filter_attestation"] == "NOT_VERIFIED"


@pytest.mark.parametrize("field", ["expected_pointer_sha256", "policy_sha256", "roster_sha256", "public_policy_sha256"])
def test_missing_or_malformed_pin_refuses_before_read(simulated, field):
    args, _, _, seen = simulated
    args[field] = None
    with pytest.raises(resolver.PublicPointerRefused, match="^resolution_refused$"):
        resolver.resolve_public_pointer(**args, enabled=True)
    assert seen == []


def test_pointer_replaced_after_verified_view_refuses(simulated):
    args, values, _, _ = simulated
    values["q4-latest.json"] = b"replacement"
    with pytest.raises(resolver.PublicPointerRefused, match="^resolution_refused$"):
        resolver.resolve_public_pointer(**args, enabled=True)


def test_out_of_policy_projection_refuses_even_when_hash_matches(simulated):
    args, values, view, _ = simulated
    values["projection.json"] = json.dumps({"records": [{"meta": {"task_id": "private", "type": "message"}}]}).encode()
    view["artifacts"]["projection_sha256"] = sha(values["projection.json"])
    with pytest.raises(resolver.PublicPointerRefused, match="^resolution_refused$"):
        resolver.resolve_public_pointer(**args, enabled=True)


def test_changed_descriptor_refuses(simulated):
    args, values, _, _ = simulated
    values["descriptor.json"] = b"different"
    with pytest.raises(resolver.PublicPointerRefused, match="^resolution_refused$"):
        resolver.resolve_public_pointer(**args, enabled=True)


def test_wrong_checkpoint_pin_refuses(simulated):
    args, _, view, _ = simulated
    view["pointer_sha256"] = "f" * 64
    with pytest.raises(resolver.PublicPointerRefused, match="^resolution_refused$"):
        resolver.resolve_public_pointer(**args, enabled=True)


@pytest.mark.skipif(os.name != "nt", reason="checkpoint publication requires a fixed Windows volume")
def test_real_public_generation_resolves_with_exact_pointer_and_keeps_private_rows_out(tmp_path):
    """Exercise synthetic snapshot -> builder -> checkpoint -> resolver without mocks."""
    from sandbox.mind.step2 import test_shared_index_builder as fixtures

    snapshot, _, checkpoint = fixtures._public_integration_modules()
    root = tmp_path / "public-series"
    root.mkdir()
    result, anchor_hash, anchor_bytes, start = fixtures._real_public_snapshot(
        snapshot, tmp_path / "synthetic-log", fixtures.MIXED_EVENTS
    )
    built = fixtures.build(
        root, name="gen-a", proj=result["projection"], approved_tasks=None, roster=None,
        source=fixtures._public_source(result, anchor_hash, anchor_bytes), allow_unbound_source=False,
    )
    artifacts = fixtures._artifacts_of(built)
    checkpoint.commit_generation(
        root, "gen-a", start, result["candidate_cursor"], fixtures.APPROVED,
        expected_pointer_sha256=None, expected_artifacts=artifacts, initial_cursor=start,
        snapshot_result=result, policy_bytes=fixtures.POLICY_BYTES,
        expected_policy_sha256=fixtures.POLICY_SHA, roster_bytes=fixtures.ROSTER_BYTES,
        expected_roster_sha256=fixtures.ROSTER_SHA, expected_anchor_sha256=anchor_hash,
        expected_anchor_bytes=anchor_bytes, **fixtures.PUBLIC_CHECKPOINT_KWARGS,
    )
    pointer_hash = sha((root / checkpoint.POINTER).read_bytes())
    pins = dict(
        expected_pointer_sha256=pointer_hash, policy_bytes=fixtures.POLICY_BYTES,
        policy_sha256=fixtures.POLICY_SHA, roster_bytes=fixtures.ROSTER_BYTES,
        roster_sha256=fixtures.ROSTER_SHA, public_policy_bytes=fixtures.PUBLIC_POLICY_BYTES,
        public_policy_sha256=fixtures.PUBLIC_POLICY_SHA, enabled=True,
    )
    resolved = resolver.resolve_public_pointer(root, **pins)
    assert resolved["generation"] == "gen-a"
    assert Path(resolved["memory_root"]) == root / "gen-a"
    assert Path(resolved["descriptor_path"]) == root / "gen-a" / "descriptor.json"
    assert resolved["descriptor_sha256"] == artifacts["descriptor_sha256"]
    assert sha(Path(resolved["descriptor_path"]).read_bytes()) == artifacts["descriptor_sha256"]
    assert resolved["memory_sha256"] == artifacts["memory_sha256"]
    assert resolved["projection_sha256"] == artifacts["projection_sha256"]
    assert resolved["receipt_sha256"] == artifacts["receipt_sha256"]
    assert resolved["pointer_sha256"] == pointer_hash
    assert fixtures.SENTINEL.encode() not in fixtures._files_text(root / "gen-a")
    with pytest.raises(resolver.PublicPointerRefused, match="^resolution_refused$"):
        resolver.resolve_public_pointer(root, **{**pins, "expected_pointer_sha256": "0" * 64})

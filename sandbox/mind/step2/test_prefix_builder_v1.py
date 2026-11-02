"""Synthetic opt-in prefix source binding; no live bridge or model."""
from dataclasses import replace
import hashlib
import json

import pytest

pytest.importorskip("faiss", reason="synthetic builder integration requires FAISS")

from sandbox.mind.step2 import shared_bridge_snapshot as snapshot, shared_index_builder as builder
from sandbox.mind.step2.test_shared_index_builder import (
    APPROVED, PUBLIC_EVENTS, POLICY_BYTES, POLICY_SHA, ROSTER_BYTES, ROSTER_SHA,
    PUBLIC_POLICY_BYTES, PUBLIC_POLICY_SHA, _real_public_snapshot, _public_source,
)
from waggledance.core.bridge_log_reader import BridgePrefixPin


@pytest.fixture
def prefix_source(tmp_path):
    _, anchor_sha, anchor_bytes, start = _real_public_snapshot(snapshot, tmp_path / "log", PUBLIC_EVENTS)
    data = json.loads(anchor_bytes)
    pin = BridgePrefixPin(start, hashlib.sha256(b"").hexdigest())
    result = snapshot.read_anchored_delta(
        data["source_path"], start, APPROVED, anchor_bytes=anchor_bytes, anchor_sha256=anchor_sha,
        generation_path=data["generation_path"], max_bytes=1 << 20, max_rows=100,
        policy_bytes=POLICY_BYTES, policy_sha256=POLICY_SHA, roster_bytes=ROSTER_BYTES,
        roster_sha256=ROSTER_SHA, public_policy_bytes=PUBLIC_POLICY_BYTES,
        public_policy_sha256=PUBLIC_POLICY_SHA, expected_prefix_sha256=pin.sha256,
        max_prefix_bytes=1 << 20)
    source = _public_source(result, anchor_sha, anchor_bytes)
    source["expected_start_prefix"] = pin
    return source, pin


def test_prefix_source_requires_external_prior_and_never_discloses_digest(prefix_source):
    source, pin = prefix_source
    proj = source["snapshot_result"]["projection"]
    digest = hashlib.sha256(json.dumps(proj, sort_keys=True, indent=1).encode()).hexdigest()
    binding, mode = builder.bind_source(source, proj, digest, POLICY_SHA, ROSTER_SHA, APPROVED)
    assert mode["status_mode"] == "public-v1"
    assert pin.sha256 not in json.dumps(binding) and pin.sha256 not in json.dumps(mode)
    for bad in (None, replace(pin, sha256="0" * 64)):
        changed = dict(source, expected_start_prefix=bad)
        with pytest.raises(builder.BuildRefused):
            builder.bind_source(changed, proj, digest, POLICY_SHA, ROSTER_SHA, APPROVED)
    changed = dict(source)
    del changed["expected_start_prefix"]
    with pytest.raises(builder.BuildRefused):
        builder.bind_source(changed, proj, digest, POLICY_SHA, ROSTER_SHA, APPROVED)


def test_prefix_predecessor_requires_verified_watermark_before_artifact_read(prefix_source, monkeypatch, tmp_path):
    _, pin = prefix_source
    predecessor = {"memory_dir": str(tmp_path), "memory_sha256": "1" * 64,
                   "projection_sha256": "2" * 64, "build_receipt_sha256": "3" * 64}
    def unexpected_read(*args, **kwargs):
        pytest.fail("prefix predecessor must refuse before any artifact read")
    monkeypatch.setattr(builder, "_read_plain_file", unexpected_read)
    with pytest.raises(builder.BuildRefused, match="prefix_watermark_required"):
        builder.load_predecessor(predecessor, {}, {"cursor_start_sha256": "4" * 64},
                                 expected_start_prefix=pin)

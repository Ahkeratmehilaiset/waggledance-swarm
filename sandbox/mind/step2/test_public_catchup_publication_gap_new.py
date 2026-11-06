"""Synthetic evidence of the catch-up / single-delta publication boundary."""
import hashlib
import json
import copy

import pytest

from sandbox.mind.step2 import shared_bridge_snapshot as snapshot
from sandbox.mind.step2 import shared_history as history
from sandbox.mind.step2 import shared_index_builder as builder
from sandbox.mind.step2.test_shared_bridge_catchup import TASKS, catchup, digest, event, fixture, pins, verify
from waggledance.core.bridge_log_reader import BridgePrefixPin


def test_verified_multiple_windows_cannot_yet_be_published_with_one_start_anchor(tmp_path):
    path, generation, cursor = fixture(tmp_path, [event(i) for i in range(6)])
    start = BridgePrefixPin(cursor, digest(b""))
    result, anchor = catchup(path, generation, cursor, start.sha256, max_rows=2)
    assert result["window_count"] == 3 and result["caught_up"] is True
    assert verify(result, anchor, start)
    policies = pins()
    expected = dict(expected_anchor_sha256=digest(anchor), expected_policy_sha256=policies["policy_sha256"],
                    expected_roster_sha256=policies["roster_sha256"], expected_mode="public-v1",
                    expected_public_policy_sha256=policies["public_policy_sha256"], expected_anchor_bytes=anchor)
    first, second = result["windows"][:2]
    assert snapshot.verify_anchored_projection(first, first["projection"],
        expected_start_prefix=first["start_prefix"], **expected)
    # The chain verifier knows how to bind this window to the original anchor,
    # but the builder's single-window verifier requires anchor.cursor_start to
    # equal this window's own start. It therefore cannot consume window two.
    with pytest.raises(snapshot.SnapshotRefused):
        snapshot.verify_anchored_projection(second, second["projection"],
            expected_start_prefix=second["start_prefix"], **expected)
    union = first["projection"]
    for window in result["windows"][1:]:
        union = history.merge_projections(union, window["projection"], **{key: policies[key] for key in
            ("policy_bytes", "policy_sha256", "roster_bytes", "roster_sha256")})
    source = {"snapshot_result": result, "expected_anchor_sha256": digest(anchor), "mode": "public-v1",
              "public_policy_bytes": policies["public_policy_bytes"],
              "public_policy_sha256": policies["public_policy_sha256"], "anchor_bytes": anchor,
              "expected_start_prefix": start}
    union_hash = hashlib.sha256(json.dumps(union, sort_keys=True, indent=1).encode()).hexdigest()
    with pytest.raises(builder.BuildRefused, match="source_binding_refused"):
        builder.bind_source(source, union, union_hash, policies["policy_sha256"], policies["roster_sha256"], TASKS)


def test_builder_binds_complete_catchup_union_without_a_forged_window(tmp_path):
    path, generation, cursor = fixture(tmp_path, [event(i) for i in range(6)])
    start = BridgePrefixPin(cursor, digest(b""))
    result, anchor = catchup(path, generation, cursor, start.sha256, max_rows=2)
    policies = pins()
    union = result["windows"][0]["projection"]
    for window in result["windows"][1:]:
        union = history.merge_projections(union, window["projection"], **{key: policies[key] for key in
            ("policy_bytes", "policy_sha256", "roster_bytes", "roster_sha256")})
    source = {"catchup_result": result, "expected_anchor_sha256": digest(anchor), "mode": "public-catchup-v1",
              "public_policy_bytes": policies["public_policy_bytes"],
              "public_policy_sha256": policies["public_policy_sha256"], "anchor_bytes": anchor,
              "expected_start_prefix": start}
    union_hash = hashlib.sha256(json.dumps(union, sort_keys=True, indent=1).encode()).hexdigest()
    binding, mode = builder.bind_source(source, union, union_hash, policies["policy_sha256"],
        policies["roster_sha256"], TASKS, policy_bytes=policies["policy_bytes"], roster_bytes=policies["roster_bytes"])
    assert binding["anchor_sha256"] == digest(anchor)
    assert binding["projection_sha256"] == union_hash
    assert binding["cursor_end_sha256"] == hashlib.sha256(snapshot._token(result["candidate_cursor"]).encode()).hexdigest()
    assert mode["status_mode"] == "public-v1"


@pytest.mark.parametrize("fault", ["partial", "reverse", "projection", "candidate", "anchor", "public_policy"])
def test_catchup_source_refuses_partial_or_tampered_chain(tmp_path, fault):
    path, generation, cursor = fixture(tmp_path, [event(i) for i in range(6)])
    start = BridgePrefixPin(cursor, digest(b""))
    result, anchor = catchup(path, generation, cursor, start.sha256,
                             max_rows=2, max_windows=1 if fault == "partial" else 16)
    policies = pins()
    source = {"catchup_result": copy.deepcopy(result), "expected_anchor_sha256": digest(anchor),
              "mode": "public-catchup-v1", "public_policy_bytes": policies["public_policy_bytes"],
              "public_policy_sha256": policies["public_policy_sha256"], "anchor_bytes": anchor,
              "expected_start_prefix": start}
    if fault == "reverse":
        source["catchup_result"]["windows"] = tuple(reversed(source["catchup_result"]["windows"]))
    elif fault == "projection":
        source["catchup_result"]["windows"][0]["projection"]["records"][0]["stored_text"] = "tampered"
    elif fault == "candidate":
        source["catchup_result"]["candidate_prefix"] = start
    elif fault == "anchor":
        source["anchor_bytes"] = anchor + b" "
    elif fault == "public_policy":
        source["public_policy_sha256"] = "0" * 64
    with pytest.raises(builder.BuildRefused, match="source_binding_refused"):
        builder.verified_catchup_projection(source, policy_bytes=policies["policy_bytes"],
            policy_sha256=policies["policy_sha256"], roster_bytes=policies["roster_bytes"],
            roster_sha256=policies["roster_sha256"])


def test_catchup_refuses_a_rejected_public_candidate_in_any_window(tmp_path, monkeypatch):
    # Force a synthetic upstream projection refusal at the adapter return seam.
    # The real snapshot reader then creates all bindings and commitments; this
    # test never constructs or alters its private binding objects.
    adapt = snapshot.adapter.adapt_rows
    calls = []
    def injected(*args, **kwargs):
        value = adapt(*args, **kwargs)
        calls.append(None)
        if len(calls) == 2:
            value["projection"]["refused"] = {"status_invalid": 1}
        return value
    monkeypatch.setattr(snapshot.adapter, "adapt_rows", injected)
    events = [event(0), event(1), event(2)]
    path, generation, cursor = fixture(tmp_path, events)
    start = BridgePrefixPin(cursor, digest(b""))
    result, anchor = catchup(path, generation, cursor, start.sha256, max_rows=1)
    assert result["caught_up"] is True and result["window_count"] >= 2
    assert any(window["projection"]["refused"] for window in result["windows"])
    policies = pins()
    source = {"catchup_result": result, "expected_anchor_sha256": digest(anchor), "mode": "public-catchup-v1",
              "public_policy_bytes": policies["public_policy_bytes"],
              "public_policy_sha256": policies["public_policy_sha256"], "anchor_bytes": anchor,
              "expected_start_prefix": start}
    with pytest.raises(builder.BuildRefused, match="source_binding_refused"):
        builder.verified_catchup_projection(source, policy_bytes=policies["policy_bytes"],
            policy_sha256=policies["policy_sha256"], roster_bytes=policies["roster_bytes"],
            roster_sha256=policies["roster_sha256"])

"""Synthetic, non-publishing checks of an explicitly opted-in partial chain.

The tests assume a trusted same-process snapshot producer. Bindings and their
commitments are splice guards, not authentication: these tests do not prove
snapshot-end against an active same-process forger who can construct bindings.
"""
import copy

import pytest

from sandbox.mind.step2 import shared_index_builder as builder
from sandbox.mind.step2.test_shared_bridge_catchup import catchup, digest, event, fixture, pins
from waggledance.core.bridge_log_reader import BridgePrefixPin


def _source(tmp_path, *, count=65, max_windows=64, tail=b""):
    events = [event(i % 60, ts_utc="2026-10-31T12:%02d:%02dZ" % (i // 60, i % 60))
              for i in range(count)]
    path, generation, cursor = fixture(tmp_path, events, tail=tail)
    start = BridgePrefixPin(cursor, digest(b""))
    result, anchor = catchup(path, generation, cursor, start.sha256, max_rows=1,
                             max_windows=max_windows)
    p = pins()
    source = {"catchup_result": result, "expected_anchor_sha256": digest(anchor),
              "mode": "public-catchup-v1", "public_policy_bytes": p["public_policy_bytes"],
              "public_policy_sha256": p["public_policy_sha256"], "anchor_bytes": anchor,
              "expected_start_prefix": start}
    args = {k: p[k] for k in ("policy_bytes", "policy_sha256", "roster_bytes", "roster_sha256")}
    return source, args


def test_disabled_before_touching_source():
    for flag in (False, None, 1, "yes"):
        with pytest.raises(builder.BuildRefused, match="partial_projection_disabled"):
            builder.verified_partial_catchup_projection(None, enabled=flag)


def test_64_windows_are_verified_as_partial_but_legacy_stays_closed(tmp_path):
    source, args = _source(tmp_path)
    assert source["catchup_result"]["caught_up"] is False
    with pytest.raises(builder.BuildRefused, match="source_binding_refused"):
        builder.verified_catchup_projection(source, **args)
    projection, frozen = builder.verified_partial_catchup_projection(source, enabled=True, **args)
    assert len(frozen["windows"]) == 64
    assert sum(len(w["projection"]["records"]) for w in frozen["windows"]) == 64
    # The existing public projection rounds time, and its merger deduplicates
    # identical projected records; do not claim 64 unique records survived.
    assert len(projection["records"]) == 2
    assert frozen["caught_up"] is False
    assert frozen["candidate_cursor"].offset > source["expected_start_prefix"].cursor.offset


@pytest.mark.parametrize("fault", ["projection", "candidate", "anchor", "policy"])
def test_tampered_partial_chain_is_refused(tmp_path, fault):
    source, args = _source(tmp_path, count=3, max_windows=2)
    source = copy.deepcopy(source)
    if fault == "projection":
        source["catchup_result"]["windows"][0]["projection"]["records"][0]["stored_text"] = "tampered"
    elif fault == "candidate":
        source["catchup_result"]["candidate_prefix"] = source["expected_start_prefix"]
    elif fault == "anchor":
        source["anchor_bytes"] += b" "
    else:
        source["public_policy_sha256"] = "0" * 64
    with pytest.raises(builder.BuildRefused, match="source_binding_refused"):
        builder.verified_partial_catchup_projection(source, enabled=True, **args)


def test_complete_chain_cannot_be_mislabelled_partial(tmp_path):
    source, args = _source(tmp_path, count=2)
    assert source["catchup_result"]["caught_up"] is True
    with pytest.raises(builder.BuildRefused, match="source_binding_refused"):
        builder.verified_partial_catchup_projection(source, enabled=True, **args)

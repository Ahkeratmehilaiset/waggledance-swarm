"""Synthetic source delta with a caller-provided byte-prefix pin."""
from dataclasses import asdict, replace
import hashlib
import json

import pytest

from sandbox.mind.step2 import shared_bridge_snapshot as snapshot
from sandbox.mind.step2.test_shared_source_anchor import TASKS, _bytes, _line, _pins, _verify, synthetic
from waggledance.core.bridge_log_reader import BridgePrefixPin, read_bridge_log


def test_anchored_delta_returns_cursor_bound_prefix_pin(tmp_path):
    source = tmp_path / "synthetic.jsonl"
    first, next_row = _line("done"), _line("started")
    source.write_bytes(first)
    generation = tmp_path / "generation.json"
    generation.write_bytes(_bytes({"generation": "synthetic"}))
    cursor = read_bridge_log(source, generation_path=generation).candidate_cursor
    anchor = _bytes({"schema": "wd.q4-source-anchor.v1", "source_path": str(source),
                     "generation_path": str(generation), "cursor_start": asdict(cursor),
                     "policy_sha256": _pins()["policy_sha256"], "roster_sha256": _pins()["roster_sha256"]})
    with source.open("ab") as stream:
        stream.write(next_row)
    kwargs = dict(anchor_bytes=anchor, anchor_sha256=hashlib.sha256(anchor).hexdigest(),
                  generation_path=generation, max_bytes=4096, max_rows=20,
                  expected_prefix_sha256=hashlib.sha256(first).hexdigest(), max_prefix_bytes=4096, **_pins())
    result = snapshot.read_anchored_delta(source, cursor, TASKS, **kwargs)
    pin = result["candidate_prefix"]
    assert pin.cursor == result["candidate_cursor"]
    assert pin.sha256 == hashlib.sha256(first + next_row).hexdigest()
    anchor_obj = json.loads(anchor)
    start_pin = BridgePrefixPin(cursor, hashlib.sha256(first).hexdigest())
    assert result["start_prefix"] == start_pin
    assert _verify(result, result["projection"], anchor_obj, expected_start_prefix=start_pin)
    for changed in ({k: v for k, v in result.items() if k != "candidate_prefix"},
                    {**result, "candidate_prefix": replace(pin, sha256="0" * 64)},
                    {**result, "candidate_prefix": replace(pin, cursor=cursor)},
                    {k: v for k, v in result.items() if k != "start_prefix"},
                    {**result, "start_prefix": replace(start_pin, sha256="0" * 64)},
                    {**result, "start_prefix": replace(start_pin, cursor=pin.cursor)}):
        with pytest.raises(snapshot.SnapshotRefused):
            _verify(changed, changed["projection"], anchor_obj, expected_start_prefix=start_pin)
    with pytest.raises(snapshot.SnapshotRefused):
        _verify(result, result["projection"], anchor_obj,
                expected_start_prefix=replace(start_pin, sha256="0" * 64))
    with pytest.raises(snapshot.SnapshotRefused):
        _verify(result, result["projection"], anchor_obj)
    with source.open("r+b") as stream:
        stream.seek(0)
        stream.write(b"x")
    with pytest.raises(snapshot.SnapshotRefused):
        snapshot.read_anchored_delta(source, cursor, TASKS, **kwargs)


def test_legacy_result_cannot_be_falsely_labeled_prefix_checked(synthetic):
    from sandbox.mind.step2.test_shared_source_anchor import _read
    path, generation, cursor, anchor = synthetic
    result = _read(path, generation, cursor, anchor)
    assert _verify(result, result["projection"], anchor)
    result["candidate_prefix"] = BridgePrefixPin(result["candidate_cursor"], "0" * 64)
    with pytest.raises(snapshot.SnapshotRefused):
        _verify(result, result["projection"], anchor)
    del result["candidate_prefix"]
    result["start_prefix"] = BridgePrefixPin(cursor, "0" * 64)
    with pytest.raises(snapshot.SnapshotRefused):
        _verify(result, result["projection"], anchor)

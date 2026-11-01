"""Synthetic tests for the checkpoint's independently pinned pointer parser.

Parsing a pointer does not validate its generation artifacts or history.
"""
import hashlib
import json

import pytest

from sandbox.mind.step2 import shared_index_checkpoint as checkpoint


def _pointer():
    cursor = {"file_identity": "posix-v1:1:2", "offset": 0, "generation": None}
    value = {
        "schema": checkpoint.SCHEMA,
        "generation": "generation-1",
        "artifacts": {name: "a" * 64 for name in checkpoint.ARTIFACTS},
        "policy_sha256": "b" * 64,
        "roster_sha256": "c" * 64,
        "anchor_sha256": "d" * 64,
        "cursor_start": cursor,
        "cursor_end": cursor,
        "watermark": cursor,
        "empty_advance": None,
        "previous": None,
        "previous_pointer_sha256": None,
    }
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def test_pointer_snapshot_uses_independent_pin_without_filesystem_reads(monkeypatch):
    raw = _pointer()
    digest = hashlib.sha256(raw).hexdigest()
    monkeypatch.setattr(checkpoint, "_read", lambda *a, **k: pytest.fail("unexpected path read"))
    parsed = checkpoint._parse_pointer_snapshot(
        raw, expected_pointer_sha256=digest, policy_sha="b" * 64,
        roster_sha="c" * 64, prefix_required=False,
    )
    assert parsed["generation"] == "generation-1"


@pytest.mark.parametrize("expected", [None, "e" * 64, "BAD"])
def test_pointer_snapshot_refuses_missing_or_incorrect_external_pin(expected):
    with pytest.raises(checkpoint.CheckpointRefused):
        checkpoint._parse_pointer_snapshot(
            _pointer(), expected_pointer_sha256=expected, policy_sha="b" * 64,
            roster_sha="c" * 64, prefix_required=False,
        )


def test_pointer_snapshot_reuses_full_pointer_schema_check():
    raw = _pointer().replace(b'"generation-1"', b'"../outside"')
    with pytest.raises(checkpoint.CheckpointRefused):
        checkpoint._parse_pointer_snapshot(
            raw, expected_pointer_sha256=hashlib.sha256(raw).hexdigest(),
            policy_sha="b" * 64, roster_sha="c" * 64, prefix_required=False,
        )


def test_current_validator_uses_the_same_pointer_parser(monkeypatch):
    from sandbox.mind.step2 import shared_projection

    raw = _pointer()
    seen = []
    monkeypatch.setattr(shared_projection, "_projection", lambda *args: {})

    def sentinel(data, **kwargs):
        seen.append((data, kwargs))
        raise RuntimeError("synthetic sentinel")

    monkeypatch.setattr(checkpoint, "_parse_bound_pointer", sentinel)
    with pytest.raises(RuntimeError, match="synthetic sentinel"):
        checkpoint._validated_previous(None, raw, {}, {}, "b" * 64, "c" * 64)
    assert seen == [(raw, {"policy_sha": "b" * 64, "roster_sha": "c" * 64,
                           "prefix_required": False})]


def test_snapshot_requires_expected_prefix_schema():
    raw = _pointer()
    with pytest.raises(checkpoint.CheckpointRefused):
        checkpoint._parse_pointer_snapshot(
            raw, expected_pointer_sha256=hashlib.sha256(raw).hexdigest(),
            policy_sha="b" * 64, roster_sha="c" * 64, prefix_required=True,
        )

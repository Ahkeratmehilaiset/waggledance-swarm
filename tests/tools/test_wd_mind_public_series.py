"""Synthetic-only continuation contract; no live source or filesystem reads."""

import copy
import hashlib
import json

import pytest

from tools import wd_mind_public_series as series


def _bytes(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("ascii")


def _sha(value):
    return hashlib.sha256(value).hexdigest()


def _anchor(offset):
    return {
        "schema": "wd.q4-source-anchor.v1",
        "source_path": "C:\\synthetic\\events.jsonl",
        "generation_path": "C:\\synthetic\\generation.json",
        "cursor_start": {"offset": offset, "file_identity": "synthetic-file", "generation": "synthetic-gen"},
        "policy_sha256": "a" * 64,
        "roster_sha256": "b" * 64,
        "status_mode": "public-v1",
        "public_policy_sha256": "c" * 64,
    }


def _args(root=None, previous=None, following=None):
    root = _bytes(_anchor(0) if root is None else root)
    previous = _bytes(_anchor(10) if previous is None else previous)
    following_obj = _anchor(20) if following is None else following
    following = _bytes(following_obj)
    return {"root_anchor_bytes": root, "root_anchor_sha256": _sha(root),
            "previous_anchor_bytes": previous, "previous_anchor_sha256": _sha(previous),
            "next_anchor_bytes": following, "next_anchor_sha256": _sha(following),
            "expected_candidate_cursor": copy.deepcopy(following_obj["cursor_start"])}


def _refused(**kwargs):
    with pytest.raises(series.SeriesRefused, match="^public_series_refused$"):
        series.check_continuation(enabled=True, **kwargs)


def test_off_does_not_consume_inputs():
    assert series.check_continuation() == {"status": "OFF"}
    assert series.check_continuation(enabled=False, root_anchor_bytes=object()) == {"status": "OFF"}
    with pytest.raises(series.SeriesRefused, match="^public_series_refused$"):
        series.check_continuation(enabled=1)


@pytest.mark.parametrize("offset,expected", [(20, "ADVANCED"), (10, "NO_ADVANCE")])
def test_exact_bound_continuation_reveals_no_paths_or_records(offset, expected):
    result = series.check_continuation(enabled=True, **_args(following=_anchor(offset)))
    assert result == {"status": expected, "authority": "none", "source_authenticity": "UNKNOWN"}
    assert "synthetic" not in repr(result)


def test_validation_performs_no_filesystem_io(monkeypatch):
    args = _args()

    def forbidden(*args, **kwargs):
        raise AssertionError("unexpected filesystem access")

    monkeypatch.setattr("builtins.open", forbidden)
    monkeypatch.setattr("os.open", forbidden)
    assert series.check_continuation(enabled=True, **args)["status"] == "ADVANCED"


@pytest.mark.parametrize("field", ["root_anchor_sha256", "previous_anchor_sha256", "next_anchor_sha256"])
def test_wrong_hash_always_refuses(field):
    args = _args()
    args[field] = "0" * 64
    _refused(**args)


@pytest.mark.parametrize("field,value", [
    ("schema", "other"), ("source_path", "C:\\other\\events.jsonl"),
    ("generation_path", "C:\\other\\generation.json"), ("policy_sha256", "d" * 64),
    ("roster_sha256", "e" * 64), ("public_policy_sha256", "f" * 64), ("status_mode", "legacy"),
])
@pytest.mark.parametrize("position", ["previous", "following"])
def test_all_non_cursor_fields_remain_bound_to_root(field, value, position):
    changed = _anchor(10 if position == "previous" else 20)
    changed[field] = value
    _refused(**_args(**{position: changed}))


@pytest.mark.parametrize("position", ["previous", "following"])
@pytest.mark.parametrize("field,value", [("file_identity", "other-file"), ("generation", "other-gen")])
def test_cursor_identity_or_generation_must_match_root(position, field, value):
    changed = _anchor(10 if position == "previous" else 20)
    changed["cursor_start"][field] = value
    _refused(**_args(**{position: changed}))


def test_candidate_must_be_explicitly_bound_and_no_skipping_or_rollback():
    args = _args()
    args["expected_candidate_cursor"]["offset"] = 19
    _refused(**args)
    args = _args(following=_anchor(9))
    _refused(**args)
    args = _args(root=_anchor(11))
    _refused(**args)
    args = _args()
    args["expected_candidate_cursor"] = None
    _refused(**args)


@pytest.mark.parametrize("offset", [-1, True, 1.5, "20", None])
def test_invalid_cursor_offset_refused(offset):
    changed = _anchor(20)
    changed["cursor_start"]["offset"] = offset
    _refused(**_args(following=changed))


@pytest.mark.parametrize("field,value", [
    ("source_path", "relative"), ("source_path", ""), ("source_path", "C:\\bad\x00path"),
    ("policy_sha256", "not-a-digest"), ("roster_sha256", "A" * 64),
])
def test_invalid_root_shape_refused(field, value):
    changed = _anchor(0)
    changed[field] = value
    _refused(**_args(root=changed))


def test_duplicate_keys_unknown_fields_and_oversized_json_refused():
    args = _args()
    for raw in (b'{"schema":"a","schema":"b"}', b"{" + b" " * 16384 + b"}"):
        args["root_anchor_bytes"] = raw
        args["root_anchor_sha256"] = _sha(raw)
        _refused(**args)
    changed = _anchor(20)
    changed["extra"] = "value"
    _refused(**_args(following=changed))
    changed = _anchor(20)
    changed["cursor_start"]["extra"] = "value"
    _refused(**_args(following=changed))

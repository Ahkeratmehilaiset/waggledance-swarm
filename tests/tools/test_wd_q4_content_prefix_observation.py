"""UNEXECUTED tests-only RED candidate for the exact 1e4d8b2d content route.

Place only at tests/tools/test_wd_q4_content_prefix_observation.py in the
Lead-bound dedicated worktree. No production implementation accompanies this
candidate. Source stays absent until the D15 RED is retained.
"""
import hashlib
import importlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from sandbox.mind.step2 import content_memory as content
from sandbox.mind.step2 import test_content_route as route_helpers
from sandbox.mind.step2 import test_content_rederive as rederive_helpers
from tools import wd_q4_snapshot_lag as lag


EXPECTED = {
    "status": "OBSERVED",
    "derivation": "MATCH_WITHIN_PREFIX",
    "position": "BOUNDED_BY_LOG_POSITION",
    "publisher_authentication": "UNKNOWN",
    "source_authentication": "UNKNOWN",
    "freshness": "UNKNOWN",
    "same_user_aba": "UNKNOWN",
}


def digest(value):
    return hashlib.sha256(value).hexdigest()


@pytest.fixture
def observer():
    # Failure here on the exact base is an honest missing-feature RED.
    return importlib.import_module("tools.wd_q4_content_prefix_observation")


@pytest.fixture
def snapshot(tmp_path):
    # Existing real content-route functions, no Git repo, model, FAISS build,
    # live ledger or capture. Data belongs to this test.
    data_root = tmp_path / ".codex-audit"
    data_root.mkdir()
    path, generation, cursor, prefix_sha = route_helpers.write_prefix(
        data_root / "prefix", route_helpers.prefix_rows()
    )
    policy, policy_sha = route_helpers.message_policy()
    messages = {
        "source_path": str(path), "generation_path": str(generation),
        "end_cursor": cursor, "policy_bytes": policy, "policy_sha256": policy_sha,
    }
    prefix = {
        "sha256": prefix_sha, "offset": cursor.offset, "generation": cursor.generation,
    }
    strict = route_helpers.strict_pins()
    decision, decision_sha = route_helpers.decision_of(
        prefix=prefix, message_pin=policy_sha, strict=strict
    )
    projection, _ = content._route_projection(
        messages, None, decision, decision_sha, **strict
    )
    projection_bytes = content._serialized(projection)
    return {
        "path": path, "generation": generation, "projection": projection,
        "args": dict(
            enabled=True, projection_bytes=projection_bytes,
            projection_sha256=digest(projection_bytes), messages=messages,
            documents=None, decision_bytes=decision, decision_sha256=decision_sha,
            max_prefix_bytes=1024 * 1024, max_lag_bytes=0, **strict
        ),
    }


def observe(observer, snapshot, **changes):
    return observer.observe(**dict(snapshot["args"], **changes))


def test_default_off_has_no_source_or_argument_side_effect(observer, monkeypatch):
    calls = []
    monkeypatch.setattr(
        content, "validate_content_snapshot",
        lambda **kwargs: calls.append("derive"),
    )
    monkeypatch.setattr(
        lag, "check_snapshot_position",
        lambda **kwargs: calls.append("position"),
    )
    assert observer.observe() == {"status": "OFF"}
    assert observer.observe(enabled=False, projection_bytes=object()) == {"status": "OFF"}
    assert calls == []


def test_real_message_prefix_derivation_and_zero_lag_remain_unauthenticated(
    observer, snapshot
):
    result = observe(observer, snapshot)
    assert result == EXPECTED
    encoded = json.dumps(result)
    for hidden in (
        str(snapshot["path"]), str(snapshot["generation"]),
        snapshot["args"]["projection_sha256"], "synteettinen",
    ):
        assert hidden.casefold() not in encoded.casefold()


def test_complete_bounded_tail_is_a_position_observation_only(observer, snapshot):
    assert observe(observer, snapshot) == EXPECTED
    with snapshot["path"].open("ab") as stream:
        stream.write(b'{"event":"synthetic-tail"}\n')
    assert observe(observer, snapshot, max_lag_bytes=4096) == EXPECTED
    assert observe(observer, snapshot) == {"status": "UNKNOWN"}


@pytest.mark.parametrize("suffix", [b"x", b'{}\npartial'])
def test_incomplete_tail_is_unknown_even_with_room_in_budget(
    observer, snapshot, suffix
):
    assert observe(observer, snapshot) == EXPECTED
    with snapshot["path"].open("ab") as stream:
        stream.write(suffix)
    assert observe(observer, snapshot, max_lag_bytes=4096) == {"status": "UNKNOWN"}


@pytest.mark.parametrize("change", ["prefix", "generation", "truncate", "rotate"])
def test_changed_source_never_inherits_a_previous_success(
    observer, snapshot, change
):
    assert observe(observer, snapshot) == EXPECTED
    path = snapshot["path"]
    original = path.read_bytes()
    if change == "prefix":
        path.write_bytes(b"x" * len(original))
    elif change == "generation":
        snapshot["generation"].write_text(
            json.dumps({"generation": "synthetic-other"}), encoding="utf-8"
        )
    elif change == "truncate":
        path.write_bytes(original[:-1])
    else:
        path.rename(path.with_name("retained-old.jsonl"))
        path.write_bytes(original)
    assert observe(observer, snapshot) == {"status": "UNKNOWN"}


def test_self_consistent_forged_projection_cannot_replace_real_derivation(
    observer, snapshot
):
    assert observe(observer, snapshot) == EXPECTED
    forged = rederive_helpers.forge(snapshot["projection"])
    # Existing structural checks accept the self-consistent row. The new
    # observation must still require its derivation from the actual prefix.
    content.validate_route_projection(forged)
    raw = content._serialized(forged)
    assert observe(
        observer, snapshot, projection_bytes=raw, projection_sha256=digest(raw)
    ) == {"status": "UNKNOWN"}


@pytest.mark.parametrize("change", ["append", "generation", "rewrite"])
def test_source_change_during_rederivation_invalidates_the_observation(
    observer, snapshot, monkeypatch, change
):
    original_derive = content.validate_content_snapshot
    assert observe(observer, snapshot, max_lag_bytes=4096) == EXPECTED

    def derive_then_change(**kwargs):
        result = original_derive(**kwargs)
        if change == "append":
            with snapshot["path"].open("ab") as stream:
                stream.write(b'{"event":"racing-tail"}\n')
        elif change == "generation":
            snapshot["generation"].write_text(
                json.dumps({"generation": "racing-generation"}), encoding="utf-8"
            )
        else:
            raw = snapshot["path"].read_bytes()
            snapshot["path"].write_bytes(b"x" * len(raw))
        return result

    monkeypatch.setattr(content, "validate_content_snapshot", derive_then_change)
    assert observe(observer, snapshot, max_lag_bytes=4096) == {"status": "UNKNOWN"}


def test_reparse_ancestor_is_refused_without_replacing_the_real_reader(
    observer, snapshot, monkeypatch
):
    assert observe(observer, snapshot) == EXPECTED
    original_lstat = Path.lstat
    parent = snapshot["path"].parent

    def reparse_parent(path, *args, **kwargs):
        original = original_lstat(path, *args, **kwargs)
        if path == parent:
            return SimpleNamespace(
                st_mode=original.st_mode, st_file_attributes=0x400
            )
        return original

    monkeypatch.setattr(Path, "lstat", reparse_parent)
    assert observe(observer, snapshot) == {"status": "UNKNOWN"}


@pytest.mark.parametrize(
    "changes",
    [
        {"enabled": 1},
        {"projection_sha256": "0" * 64},
        {"decision_sha256": "0" * 64},
        {"max_prefix_bytes": True},
        {"max_prefix_bytes": 1},
        {"max_lag_bytes": True},
        {"messages": None},
    ],
)
def test_invalid_or_unbound_inputs_fail_closed(observer, snapshot, changes):
    assert observe(observer, snapshot, **changes) == {"status": "UNKNOWN"}


def test_position_exception_has_no_raw_error_or_source_output(
    observer, snapshot, monkeypatch
):
    def fail(**kwargs):
        raise OSError("synthetic-private-path")

    monkeypatch.setattr(lag, "check_snapshot_position", fail)
    assert observe(observer, snapshot) == {"status": "UNKNOWN"}

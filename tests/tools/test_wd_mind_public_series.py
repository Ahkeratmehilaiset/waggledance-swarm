"""Synthetic-only continuation contract; no live source or filesystem reads."""

import copy
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
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


def test_v3_two_real_synthetic_publishes_keep_five_fixed_root_readers(tmp_path):
    """Only synthetic files: the root pin remains fixed as the window anchor advances."""
    from sandbox.mind.step2 import shared_bridge_snapshot as snapshot
    from sandbox.mind.step2.test_status_public_filter import ROSTER, event, read_fixture
    from tools import wd_mind_public_session as session

    source = tmp_path / "source"
    source.mkdir()
    first, root_pins, log = read_fixture(source, [event(), event(to="fable-5", message="synthetic-private")])
    audit = tmp_path / ".codex-audit"
    audit.mkdir()
    now = datetime.now(timezone.utc)
    root = root_pins["anchor_bytes"]
    root_doc = json.loads(root)
    root_sha = root_pins["anchor_sha256"]
    first_candidate = asdict(first["candidate_cursor"])
    first_args = dict(enabled=True, as_of=now, expected_previous_sha256=None,
        root_anchor_bytes=root, root_anchor_sha256=root_sha,
        previous_anchor_bytes=root, previous_anchor_sha256=root_sha,
        expected_previous_candidate=root_doc["cursor_start"], **root_pins)
    attempt1 = session.prepare_status_attempt_v3(audit, first, **first_args)
    assert session.recover_status_attempt_v3(audit, prior_sha256=None, attempt_sha256=attempt1,
                                              enabled=True) == "unchanged"
    with pytest.raises(session.PublicStatusRefused):
        session.publish_status_v3(audit, first, attempt_sha256="0" * 64, **first_args)
    state1 = session.publish_status_v3(audit, first, attempt_sha256=attempt1, **first_args)
    assert session.recover_status_attempt_v3(audit, prior_sha256=None, attempt_sha256=attempt1,
                                              enabled=True) == "landed"
    assert not (audit / session.FILENAME).exists()  # v2 is never used or migrated
    v2_hash = session.publish_status(audit, first, as_of=now, enabled=True,
                                     expected_previous_sha256=None, **root_pins)
    v2_bytes = (audit / session.FILENAME).read_bytes()
    assert hashlib.sha256(v2_bytes).hexdigest() == v2_hash

    for lane, uuid in ROSTER.items():
        view = json.loads(session.read_status_v3(audit, lane=lane, agent_uuid=uuid, as_of=now,
                                                 max_age_seconds=900, enabled=True, **root_pins))
        assert len(view["records"]) == 1 and "synthetic-private" not in repr(view)
    with pytest.raises(session.PublicStatusRefused):
        session.read_status_v3(audit, lane="fable-5", agent_uuid=ROSTER["fable-5"],
                               as_of=now + timedelta(seconds=901), max_age_seconds=900, enabled=True, **root_pins)

    with log.open("ab") as stream:
        stream.write(_bytes(event(agent="fable-5", agent_uuid=ROSTER["fable-5"])) + b"\n")
    anchor2 = _bytes(dict(root_doc, cursor_start=first_candidate))
    pins2 = dict(root_pins, anchor_bytes=anchor2, anchor_sha256=_sha(anchor2))
    tasks = frozenset({"public-task", "nonreview-private"})
    second = snapshot.read_anchored_delta(log, first["candidate_cursor"], tasks,
        generation_path=root_doc["generation_path"], max_bytes=65536, max_rows=100, **pins2)
    second_args = dict(enabled=True, as_of=now, expected_previous_sha256=state1,
        root_anchor_bytes=root, root_anchor_sha256=root_sha,
        previous_anchor_bytes=root, previous_anchor_sha256=root_sha,
        expected_previous_candidate=first_candidate, **pins2)
    attempt2 = session.prepare_status_attempt_v3(audit, second, **second_args)
    assert session.recover_status_attempt_v3(audit, prior_sha256=state1, attempt_sha256=attempt2,
                                              enabled=True) == "unchanged"
    state2 = session.publish_status_v3(audit, second, attempt_sha256=attempt2, **second_args)
    assert session.recover_status_attempt_v3(audit, prior_sha256=state1, attempt_sha256=attempt2,
                                              enabled=True) == "landed"
    assert state2 != state1
    assert (audit / session.FILENAME).read_bytes() == v2_bytes
    with pytest.raises(session.PublicStatusRefused):
        session.publish_status_v3(audit, first, attempt_sha256=attempt1, **first_args)
    for lane, uuid in ROSTER.items():
        view = json.loads(session.read_status_v3(audit, lane=lane, agent_uuid=uuid, as_of=now,
                                                 max_age_seconds=900, enabled=True, **root_pins))
        assert [item["agent"] for item in view["records"]] == ["fable-5"]
        assert "synthetic-private" not in repr(view) and view["source_freshness"] == "UNKNOWN"

    wrong_root = _bytes(dict(root_doc, cursor_start=dict(root_doc["cursor_start"], offset=1)))
    wrong_pins = dict(root_pins, anchor_bytes=wrong_root, anchor_sha256=_sha(wrong_root))
    with pytest.raises(session.PublicStatusRefused):
        session.read_status_v3(audit, lane="fable-5", agent_uuid=ROSTER["fable-5"], as_of=now,
                               max_age_seconds=900, enabled=True, **wrong_pins)
    # Old, valid anchored snapshot is a replay, not the next cursor.
    with pytest.raises(session.PublicStatusRefused):
        session.prepare_status_attempt_v3(
            audit, first, enabled=True, as_of=now, expected_previous_sha256=state2,
            root_anchor_bytes=root, root_anchor_sha256=root_sha,
            previous_anchor_bytes=anchor2, previous_anchor_sha256=_sha(anchor2),
            expected_previous_candidate=asdict(second["candidate_cursor"]), **root_pins)


def test_v3_default_off_has_no_filesystem_access(monkeypatch):
    from tools import wd_mind_public_session as session
    accessed = []

    def forbidden(*args, **kwargs):
        accessed.append(True)
        raise AssertionError("disabled mode must not access the filesystem")

    monkeypatch.setattr(session, "_directory", forbidden)
    with pytest.raises(session.PublicStatusRefused):
        session.prepare_status_attempt_v3("unused", None, as_of=None)
    with pytest.raises(session.PublicStatusRefused):
        session.publish_status_v3("unused", None, as_of=None, attempt_sha256="0" * 64)
    with pytest.raises(session.PublicStatusRefused):
        session.recover_status_attempt_v3("unused", prior_sha256=None, attempt_sha256="0" * 64)
    with pytest.raises(session.PublicStatusRefused):
        session.read_status_v3("unused", lane="fable-5", agent_uuid="unused", as_of=None, max_age_seconds=1)
    assert accessed == []

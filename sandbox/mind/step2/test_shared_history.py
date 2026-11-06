"""Pure synthetic metadata merge tests: no live events or persistent state."""
import copy
import hashlib
import importlib
import json
from datetime import datetime, timedelta, timezone

import pytest

from sandbox.mind.step2 import shared_projection as projection

TASKS = frozenset({"synthetic-task"})
ROSTER = {"codex-lead-1": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
          "codex-tools-1": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
          "claude-rco-1": "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
          "claude-rco-2": "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
          "fable-5": "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"}


def raw(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def pins():
    policy = raw({"schema": "wd.q4-non-review-policy.v1", "approved_tasks": sorted(TASKS)})
    roster = raw({"schema": "wd.q4-roster-snapshot.v1", "lanes": [
        {"agent": k, "agent_uuid": v, "role": "synthetic"} for k, v in ROSTER.items() if k != "codex-tools-1"],
        "tools_supervisor": {"agent": "codex-tools-1", "agent_uuid": ROSTER["codex-tools-1"], "role": "synthetic"}})
    return {"policy_bytes": policy, "policy_sha256": hashlib.sha256(policy).hexdigest(),
            "roster_bytes": roster, "roster_sha256": hashlib.sha256(roster).hexdigest()}


def projected(numbers):
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    events = [{"ts_utc": (start + timedelta(minutes=n)).isoformat(), "type": "message", "status": "done",
               "task_id": "synthetic-task", "agent": "codex-lead-1", "agent_uuid": ROSTER["codex-lead-1"],
               "message": "synthetic-secret-not-shared", "payload": {"private": "not-shared"}}
              for n in numbers]
    return projection.project_events(events, approved_tasks=TASKS, roster=ROSTER, exclude_review_signal=True)


def merger():
    return importlib.import_module("sandbox.mind.step2.shared_history")


def test_history_merge_keeps_old_new_deduplicates_and_sorts():
    history = merger()
    old, new = projected([3, 1]), projected([2, 3])
    before_old, before_new = copy.deepcopy(old), copy.deepcopy(new)
    result = history.merge_projections(old, new, **pins())
    assert result["accepted"] == 3
    assert result["records"] == sorted(result["records"], key=lambda row: (row["time_utc"], row["record_id"]))
    assert {row["record_id"] for row in result["records"]} == {
        row["record_id"] for row in old["records"] + new["records"]}
    assert "synthetic-secret-not-shared" not in repr(result) and "not-shared" not in repr(result)
    assert old == before_old and new == before_new


def test_empty_delta_preserves_projected_records_and_no_implicit_cursor():
    old, empty = projected([0, 1]), projected([])
    result = merger().merge_projections(old, empty, **pins())
    assert result["records"] == old["records"] and result["accepted"] == 2
    assert not any("cursor" in key for key in result)


def test_output_is_detached_and_previous_is_strictly_revalidated():
    history, old, delta = merger(), projected([0]), projected([1])
    result = history.merge_projections(old, delta, **pins())
    result["records"][0]["meta"]["agent"] = "altered"
    assert old["records"][0]["meta"]["agent"] == "codex-lead-1"
    old["records"][0]["provenance"]["canonical_event"] = 0
    with pytest.raises(history.HistoryRefused, match="^history_refused$"):
        history.merge_projections(old, delta, **pins())


def test_roster_with_duplicate_uuid_refuses_even_if_rehashed():
    history, config = merger(), pins()
    roster = json.loads(config["roster_bytes"])
    roster["lanes"][1]["agent_uuid"] = roster["lanes"][0]["agent_uuid"]
    config["roster_bytes"] = raw(roster)
    config["roster_sha256"] = hashlib.sha256(config["roster_bytes"]).hexdigest()
    with pytest.raises(history.HistoryRefused, match="^history_refused$"):
        history.merge_projections(projected([0]), projected([1]), **config)


def test_cap_refuses_without_truncating_or_mutating_inputs():
    history = merger()
    assert history.MAX_RECORDS == 4096
    old, delta = projected(range(4095)), projected([4095])
    full = history.merge_projections(old, delta, **pins())
    assert full["accepted"] == 4096
    with pytest.raises(history.HistoryRefused, match="^history_refused$"):
        history.merge_projections(full, projected([4096]), **pins())
    assert full["accepted"] == 4096 and len(full["records"]) == 4096


@pytest.mark.parametrize("mutation", ["policy_pin", "roster_pin", "forged_text", "unknown_task", "review_lane",
                                      "unverified_roster", "wrong_schema", "contains_secret", "wrong_identity",
                                      "integer_false"])
def test_bad_pins_or_nonpublic_projections_refuse(mutation):
    history, source, target, config = merger(), projected([1]), projected([2]), pins()
    if mutation == "policy_pin":
        config["policy_sha256"] = "0" * 64
    elif mutation == "roster_pin":
        config["roster_sha256"] = "0" * 64
    elif mutation == "forged_text":
        target["records"][0]["stored_text"] = "synthetic-secret-not-shared"
    elif mutation == "unknown_task":
        target["records"][0]["meta"]["task_id"] = "UNKNOWN"
    elif mutation == "review_lane":
        target["records"][0]["meta"]["agent"] = "claude-rco-1"
    elif mutation == "unverified_roster":
        target["authorship"] = projection.AUTHORSHIP_UNVERIFIED
    elif mutation == "wrong_schema":
        target["schema"] = "wrong"
    elif mutation == "contains_secret":
        target["records"][0]["extra"] = "synthetic-secret-not-shared"
    elif mutation == "wrong_identity":
        target["records"][0]["meta"]["agent"] = "not-a-lane"
    elif mutation == "integer_false":
        target["records"][0]["provenance"]["canonical_event"] = 0
    with pytest.raises(history.HistoryRefused, match="^history_refused$"):
        history.merge_projections(source, target, **config)


def test_conflicting_record_id_fails_even_if_a_validator_is_broken(monkeypatch):
    history, old, new = merger(), projected([1]), projected([1])
    new["records"][0]["stored_text"] = "conflicting synthetic text"
    # Fault-inject a broken validator solely to exercise the independent collision guard;
    # the ordinary forged_text test above exercises the real validator.
    monkeypatch.setattr(history.projection, "validate_projection", lambda value, **kw: value)
    with pytest.raises(history.HistoryRefused, match="^history_refused$"):
        history.merge_projections(old, new, **pins())

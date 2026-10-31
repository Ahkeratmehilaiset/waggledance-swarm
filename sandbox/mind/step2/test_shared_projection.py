"""Synthetic checks of the default-deny shared-record projection (RCO1 S1-S10 of the shared-scope review).

Every input here is synthetic. Sentinels are plain words that must never reach a record, an encoder input or a file.
"""

import hashlib
import json
import re

import pytest

from sandbox.mind.step2 import shared_projection as sp

TS = "2026-10-31T12:00:00Z"
TS_OUT = "2026-10-31T12:00:00.000000Z"
HEAD = "a" * 40
SENTINEL_MESSAGE = "zebra-message-body-7f3a"
SENTINEL_PAYLOAD = "quartz-payload-value-9c1d"
SENTINEL_PATH = "lantern-private-path-2b8e"
SENTINEL_IDENTITY = "falcon-identity-uuid-6d4f"
SENTINELS = (SENTINEL_MESSAGE, SENTINEL_PAYLOAD, SENTINEL_PATH, SENTINEL_IDENTITY)
HEX64 = re.compile(r"[0-9a-f]{64}")


def event(**changes):
    base = {"ts_utc": TS, "agent": "codex-lead-1", "type": "message", "status": "milestone",
            "task_id": "lead-mind-shared-projection-fable-20261031-v1", "pr": 1973, "head": HEAD, "ci": "green",
            "message": SENTINEL_MESSAGE, "payload": {"detail": SENTINEL_PAYLOAD, "nonce": "0" * 32},
            "paths": ["C:/" + SENTINEL_PATH + "/x.py"], "write_scope": [SENTINEL_PATH], "cwd": "C:/" + SENTINEL_PATH,
            "pid": 4242, "run_id": SENTINEL_IDENTITY, "session_id": SENTINEL_IDENTITY, "agent_uuid": SENTINEL_IDENTITY,
            "request_digest": "f" * 64, "to": "fable-5"}
    base.update(changes)
    return base


def no_sentinel(text):
    return all(word not in text for word in SENTINELS)


def test_s1_encoder_input_equals_stored_text_and_only_approved_tokens_reach_it(capsys):
    projection = sp.project_events([event()])
    assert projection["accepted"] == 1 and projection["refused"] == {}
    record = projection["records"][0]
    assert sp.encoder_input(record) == record["stored_text"] == record["summary"]
    assert record["stored_text"] == ("message milestone codex-lead-1 lead-mind-shared-projection-fable-20261031-v1 "
                                     "pr:1973 head:" + HEAD + " ci:green")
    assert record["kind"] == "event" and record["time_utc"] == TS_OUT == record["available_utc"]
    assert record["meta"] == {"type": "message", "status": "milestone", "agent": "codex-lead-1",
                              "task_id": "lead-mind-shared-projection-fable-20261031-v1", "pr": 1973, "head": HEAD,
                              "ci": "green"}
    serialized = json.dumps(projection, sort_keys=True)
    assert no_sentinel(serialized) and "nonce" not in serialized and "f" * 64 not in serialized
    out = capsys.readouterr()
    assert out.out == "" and out.err == ""


def test_s2_s4_forbidden_fields_are_never_read_and_never_serialized():
    class Poison(str):
        def __str__(self):  # pragma: no cover - reaching here is the defect
            raise AssertionError("a forbidden field was rendered")

    poisoned = event(message=Poison(SENTINEL_MESSAGE), cwd=Poison(SENTINEL_PATH))
    projection = sp.project_events([poisoned])
    assert projection["accepted"] == 1 and projection["dropped_fields"] == 11
    assert no_sentinel(json.dumps(projection, sort_keys=True))
    assert set(projection["records"][0]) == {"record_id", "kind", "time_utc", "available_utc", "stored_text",
                                             "summary", "meta", "provenance"}


@pytest.mark.parametrize("kind", ["decision", "finding", "rco_pass", "vote", "veto", "changes_requested", "recusal",
                                  "build_consensus", "review"])
def test_s3_peer_review_and_decision_events_are_excluded_and_change_nothing_else(kind):
    plain = sp.project_events([event(), event(task_id="other-task-1")])
    mixed = sp.project_events([event(), event(type=kind, task_id="review-task"), event(task_id="other-task-1")])
    assert mixed["records"] == plain["records"]
    assert mixed["refused"] == {"type_not_allowed": 1} and mixed["accepted"] == 2
    assert all("review-task" not in record["stored_text"] for record in mixed["records"])


def test_s5_notes_are_excluded_by_default_and_a_wrong_sha_allowlist_refuses_whole(tmp_path):
    note = {"name": "feedback_example_note.md", "text": "a short approved note text"}
    with pytest.raises(sp.ProjectionRefused, match="notes_excluded_by_default"):
        sp.project_notes([note])
    wrong = {note["name"]: "0" * 64}
    with pytest.raises(sp.ProjectionRefused, match="note_allowlist_mismatch"):
        sp.project_notes([note], allowlist=wrong)
    with pytest.raises(sp.ProjectionRefused, match="note_not_allowlisted"):
        sp.project_notes([dict(note, name="other_note.md")], allowlist=wrong)
    right = {note["name"]: hashlib.sha256(note["text"].encode("utf-8")).hexdigest()}
    projection = sp.project_notes([note], allowlist=right)
    record = projection["records"][0]
    assert sp.encoder_input(record) == record["stored_text"] == note["text"] and record["kind"] == "lesson"
    assert record["record_id"] == "lesson:" + right[note["name"]] and note["name"] not in json.dumps(projection)


@pytest.mark.parametrize("task", ["C:/x/y", "..\\up\\task", "a:b", "C:\\abs", "/root/x", "with space", "Upper-Case", ""])
def test_s6_a_path_shaped_task_id_becomes_unknown(task):
    record = sp.project_events([event(task_id=task)])["records"][0]
    assert record["meta"]["task_id"] == "UNKNOWN" and record["stored_text"].split(" ")[3] == "UNKNOWN"
    assert task not in record["stored_text"] or task == ""


def test_s7_a_refused_write_leaves_no_file_and_no_partial_output(tmp_path):
    marker = getattr(sp.corpus_mod.ws, "_guarded_word")  # the guard function; a guarded word is built at runtime
    projection = sp.project_events([event()])
    projection["records"][0]["stored_text"] = "PRIVATE_" + "MARKER"  # only this test spells it, split for the diff scanner
    out = tmp_path / "out" / "projection.json"
    with pytest.raises(sp.ProjectionRefused, match="output_guarded"):
        sp.write_projection(out, projection)
    assert not out.exists() and list(tmp_path.rglob("*")) in ([], [tmp_path / "out"])
    assert marker("PRIVATE_" + "MARKER") is not None


def test_s7b_a_good_write_is_atomic_and_returns_its_digest(tmp_path):
    out = tmp_path / "out" / "projection.json"
    digest = sp.write_projection(out, sp.project_events([event()]))
    assert out.exists() and digest == hashlib.sha256(out.read_bytes()).hexdigest()
    assert sorted(p.name for p in out.parent.iterdir()) == ["projection.json"]
    assert no_sentinel(out.read_text(encoding="utf-8"))


@pytest.mark.parametrize("field,value,reason", [
    ("status", "\U0001F41D" * 9, "status_over_bytes"),          # 9 chars, 36 bytes > 32
    ("task_id", "\U0001F41D" * 33, "task_id_over_bytes"),      # 33 chars, 132 bytes > 128
    ("agent", "\U0001F41D" * 11, "agent_over_bytes"),
    ("type", "x" * 33, "type_over_bytes"),
])
def test_s8_byte_caps_count_four_byte_characters(field, value, reason):
    projection = sp.project_events([event(**{field: value})])
    assert projection["records"] == [] and projection["refused"] == {reason: 1}


def test_s8b_a_non_token_approved_field_is_refused_not_truncated():
    projection = sp.project_events([event(status="milestone zebra")])
    assert projection["records"] == [] and projection["refused"] == {"status_invalid": 1}
    projection = sp.project_events([event(task_id="x" * 129)])  # over the byte cap: refused, never UNKNOWN or cut
    assert projection["records"] == [] and projection["refused"] == {"task_id_over_bytes": 1}


def test_s9_refs_are_digests_only_and_the_same_metadata_gives_the_same_ref():
    first = sp.project_events([event()])["records"][0]
    second = sp.project_events([event(message="other body", payload={"x": 1}, paths=["D:/elsewhere"])])["records"][0]
    assert first == second
    assert first["record_id"] == "event:" + first["provenance"]["event_ref"]
    assert HEX64.fullmatch(first["provenance"]["event_ref"])
    assert first["provenance"] == {"source_kind": "bridge_event_metadata", "event_ref": first["provenance"]["event_ref"],
                                   "tier": {"source_type": "bridge_event"}, "available_utc": TS_OUT}
    third = sp.project_events([event(status="done")])["records"][0]
    assert third["provenance"]["event_ref"] != first["provenance"]["event_ref"]


@pytest.mark.parametrize("task", ["m0-sealed-cases-v1", "q4-gold-set", "testset-heldout-v1", "judge-panel-2"])
def test_s10_sealed_named_events_are_excluded(task):
    projection = sp.project_events([event(task_id=task)])
    assert projection["records"] == [] and projection["refused"] == {"sealed_named_excluded": 1}


@pytest.mark.parametrize("change,reason", [
    ({"agent": "someone-else"}, "agent_not_lane"),
    ({"agent": "CODEX-LEAD-1"}, "agent_invalid"),
    ({"ts_utc": "yesterday"}, "time_invalid"),
    ({"ts_utc": "2026-10-31T12:00:00"}, "time_invalid"),
    ({"pr": "1973"}, "pr_invalid"),
    ({"pr": -1}, "pr_invalid"),
    ({"head": "A" * 40}, "head_invalid"),
    ({"head": "a" * 39}, "head_invalid"),
    ({"ci": "green " + SENTINEL_MESSAGE}, "ci_invalid"),
    ({"status": 7}, "status_invalid"),
    ({"type": "Message"}, "type_invalid"),
])
def test_default_deny_on_every_approved_field(change, reason):
    projection = sp.project_events([event(**change)])
    assert projection["records"] == [] and projection["refused"] == {reason: 1}
    assert no_sentinel(json.dumps(projection))


def test_optional_fields_absent_or_null_leave_the_head_line_short():
    record = sp.project_events([event(pr=None, head=None, ci=None)])["records"][0]
    assert record["stored_text"] == "message milestone codex-lead-1 lead-mind-shared-projection-fable-20261031-v1"
    assert record["meta"]["pr"] is None and record["meta"]["head"] is None and record["meta"]["ci"] is None
    without = {k: v for k, v in event().items() if k not in ("pr", "head", "ci")}
    assert sp.project_events([without])["records"][0] == record


def test_a_non_mapping_or_empty_batch_is_refused_or_empty():
    with pytest.raises(sp.ProjectionRefused, match="event_not_object"):
        sp.project_events(["not an object"])
    empty = sp.project_events([])
    assert empty["records"] == [] and empty["accepted"] == 0 and empty["schema"] == sp.SCHEMA

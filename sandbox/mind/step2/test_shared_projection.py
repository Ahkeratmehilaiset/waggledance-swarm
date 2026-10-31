"""Synthetic checks of the default-deny shared-record projection (RCO1 S1-S10 of the shared-scope review, plus the
v2 review: closed enums for status and CI, task ids rendered only from a pre-approved set, notes disabled, no
overwrite, projected (non-canonical) provenance).

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
TASK = "lead-mind-shared-projection-fable-20261031-v1"
APPROVED = frozenset({TASK, "other-task-1"})
SENTINEL_MESSAGE = "zebra-message-body-7f3a"
SENTINEL_PAYLOAD = "quartz-payload-value-9c1d"
SENTINEL_PATH = "lantern-private-path-2b8e"
SENTINEL_IDENTITY = "falcon-identity-uuid-6d4f"
SENTINEL_TOKEN = "orchid-regex-shaped-secret-4e2a"  # fits the old status/ci/task regexes
SENTINELS = (SENTINEL_MESSAGE, SENTINEL_PAYLOAD, SENTINEL_PATH, SENTINEL_IDENTITY, SENTINEL_TOKEN)
HEX64 = re.compile(r"[0-9a-f]{64}")


def event(**changes):
    base = {"ts_utc": TS, "agent": "codex-lead-1", "type": "message", "status": "milestone", "task_id": TASK,
            "pr": 1973, "head": HEAD, "ci": "green",
            "message": SENTINEL_MESSAGE, "payload": {"detail": SENTINEL_PAYLOAD, "nonce": "0" * 32},
            "paths": ["C:/" + SENTINEL_PATH + "/x.py"], "write_scope": [SENTINEL_PATH], "cwd": "C:/" + SENTINEL_PATH,
            "pid": 4242, "run_id": SENTINEL_IDENTITY, "session_id": SENTINEL_IDENTITY, "agent_uuid": SENTINEL_IDENTITY,
            "request_digest": "f" * 64, "to": "fable-5"}
    base.update(changes)
    return base


def project(*events, approved=APPROVED):
    return sp.project_events(list(events), approved_tasks=approved)


def no_sentinel(text):
    return all(word not in text for word in SENTINELS)


def test_s1_encoder_input_equals_stored_text_and_only_approved_tokens_reach_it(capsys):
    projection = project(event())
    assert projection["accepted"] == 1 and projection["refused"] == {}
    record = projection["records"][0]
    assert sp.encoder_input(record) == record["stored_text"] == record["summary"]
    assert record["stored_text"] == "message milestone codex-lead-1 " + TASK + " pr:1973 head:" + HEAD + " ci:green"
    assert record["kind"] == "event" and record["time_utc"] == TS_OUT == record["available_utc"]
    assert record["meta"] == {"type": "message", "status": "milestone", "agent": "codex-lead-1", "task_id": TASK,
                              "pr": 1973, "head": HEAD, "ci": "green"}
    serialized = json.dumps(projection, sort_keys=True)
    assert no_sentinel(serialized) and "nonce" not in serialized and "f" * 64 not in serialized
    out = capsys.readouterr()
    assert out.out == "" and out.err == ""


def test_s2_s4_forbidden_fields_are_never_read_and_never_serialized():
    class Poison(str):
        def __str__(self):  # pragma: no cover - reaching here is the defect
            raise AssertionError("a forbidden field was rendered")

    poisoned = event(message=Poison(SENTINEL_MESSAGE), cwd=Poison(SENTINEL_PATH))
    projection = project(poisoned)
    assert projection["accepted"] == 1 and projection["dropped_fields"] == 11
    assert no_sentinel(json.dumps(projection, sort_keys=True))
    assert set(projection["records"][0]) == {"record_id", "kind", "time_utc", "available_utc", "stored_text",
                                             "summary", "meta", "provenance"}


@pytest.mark.parametrize("kind", ["decision", "finding", "rco_pass", "vote", "veto", "changes_requested", "recusal",
                                  "build_consensus", "review"])
def test_s3_peer_review_and_decision_events_are_excluded_and_change_nothing_else(kind):
    plain = project(event(), event(task_id="other-task-1"))
    mixed = project(event(), event(type=kind, task_id="review-task"), event(task_id="other-task-1"))
    assert mixed["records"] == plain["records"]
    assert mixed["refused"] == {"type_not_allowed": 1} and mixed["accepted"] == 2
    assert all("review-task" not in record["stored_text"] for record in mixed["records"])


def test_s5_v2_notes_are_never_projected_on_the_shared_path_even_with_a_matching_hash_list():
    note = {"name": "feedback_example_note.md", "text": "a short note text"}
    with pytest.raises(sp.ProjectionRefused, match="notes_excluded"):
        sp.project_notes([note])
    right = {note["name"]: hashlib.sha256(note["text"].encode("utf-8")).hexdigest()}
    with pytest.raises(sp.ProjectionRefused, match="notes_excluded"):
        sp.project_notes([note], allowlist=right)
    assert not hasattr(sp, "NOTE_BYTES")


@pytest.mark.parametrize("task", ["C:/x/y", "..\\up\\task", "a:b", "C:\\abs", "/root/x", "with space", "Upper-Case", "",
                                  SENTINEL_TOKEN, "lead-mind-not-approved-20261031-v9"])
def test_s6_v2_a_task_id_outside_the_approved_set_or_path_shaped_becomes_unknown(task):
    record = project(event(task_id=task))["records"][0]
    assert record["meta"]["task_id"] == "UNKNOWN" and record["stored_text"].split(" ")[3] == "UNKNOWN"
    assert task == "" or task not in json.dumps(record)


def test_v2_no_approved_set_means_every_task_id_is_unknown_and_nothing_names_it():
    projection = sp.project_events([event()])
    assert projection["records"][0]["meta"]["task_id"] == "UNKNOWN" and TASK not in json.dumps(projection)
    projection = sp.project_events([event()], approved_tasks=frozenset())
    assert projection["records"][0]["meta"]["task_id"] == "UNKNOWN"


def test_v2_an_approved_set_must_itself_be_safe_shaped():
    with pytest.raises(sp.ProjectionRefused, match="approved_tasks_invalid"):
        sp.project_events([event()], approved_tasks=frozenset({"C:/not/a/task"}))
    with pytest.raises(sp.ProjectionRefused, match="approved_tasks_invalid"):
        sp.project_events([event()], approved_tasks=[TASK])  # a list is not a frozen set


@pytest.mark.parametrize("field,value", [("status", SENTINEL_TOKEN), ("status", "rco_pass"), ("status", "veto"),
                                         ("status", "changes_requested"), ("ci", SENTINEL_TOKEN), ("ci", "failing"),
                                         ("status", "milestone zebra")])
def test_v2_status_and_ci_are_closed_enums_and_anything_else_is_unknown(field, value):
    projection = project(event(**{field: value}))
    assert projection["accepted"] == 1
    record = projection["records"][0]
    assert record["meta"][field] == "UNKNOWN" and value not in json.dumps(record)
    assert ("UNKNOWN" if field == "status" else "ci:UNKNOWN") in record["stored_text"]


@pytest.mark.parametrize("field,values", [("status", sp.ALLOWED_STATUS), ("ci", sp.ALLOWED_CI)])
def test_v2_every_enum_member_renders_itself(field, values):
    assert values and all(re.fullmatch(r"[a-z][a-z0-9_]{0,31}", v) for v in values)
    for value in sorted(values):
        record = project(event(**{field: value}))["records"][0]
        assert record["meta"][field] == value


def test_s7_a_refused_write_leaves_no_file_and_no_staging(tmp_path):
    projection = project(event())
    projection["records"][0]["stored_text"] = "PRIVATE_" + "MARKER"  # only this test spells it, split for the scanner
    out = tmp_path / "out" / "projection.json"
    with pytest.raises(sp.ProjectionRefused, match="output_guarded"):
        sp.write_projection(out, projection)
    assert not out.exists() and list(tmp_path.rglob("*")) in ([], [tmp_path / "out"])


def test_s7b_a_good_write_is_exclusive_atomic_and_returns_its_digest(tmp_path):
    out = tmp_path / "out" / "projection.json"
    digest = sp.write_projection(out, project(event()))
    assert out.exists() and digest == hashlib.sha256(out.read_bytes()).hexdigest()
    assert sorted(p.name for p in out.parent.iterdir()) == ["projection.json"]
    assert no_sentinel(out.read_text(encoding="utf-8"))
    before = out.read_bytes()
    with pytest.raises(sp.ProjectionRefused, match="output_exists"):  # v2: never overwrite a published file
        sp.write_projection(out, project(event(status="done")))
    assert out.read_bytes() == before and sorted(p.name for p in out.parent.iterdir()) == ["projection.json"]


def test_s7c_a_filesystem_error_is_one_fixed_path_free_refusal(tmp_path):
    blocker = tmp_path / "file-not-dir"
    blocker.write_text("x", encoding="utf-8")
    with pytest.raises(sp.ProjectionRefused) as raised:
        sp.write_projection(blocker / "sub" / "projection.json", project(event()))
    assert str(raised.value) == "write_failed"


@pytest.mark.parametrize("field,value,reason", [
    ("status", "\U0001F41D" * 9, "status_over_bytes"),          # 9 chars, 36 bytes > 32
    ("task_id", "\U0001F41D" * 33, "task_id_over_bytes"),      # 33 chars, 132 bytes > 128
    ("agent", "\U0001F41D" * 11, "agent_over_bytes"),
    ("type", "x" * 33, "type_over_bytes"),
    ("ci", "\U0001F41D" * 9, "ci_over_bytes"),
])
def test_s8_byte_caps_count_four_byte_characters(field, value, reason):
    projection = project(event(**{field: value}))
    assert projection["records"] == [] and projection["refused"] == {reason: 1}


def test_s8b_an_oversized_task_id_is_refused_not_cut_or_unknown():
    projection = project(event(task_id="x" * 129))
    assert projection["records"] == [] and projection["refused"] == {"task_id_over_bytes": 1}


def test_s9_refs_are_projected_digests_only_and_never_claim_the_canonical_event():
    first = project(event())["records"][0]
    second = project(event(message="other body", payload={"x": 1}, paths=["D:/elsewhere"]))["records"][0]
    assert first == second
    assert first["record_id"] == "event:" + first["provenance"]["event_ref"]
    assert HEX64.fullmatch(first["provenance"]["event_ref"])
    assert first["provenance"] == {"source_kind": "projected_bridge_metadata", "event_ref": first["provenance"]["event_ref"],
                                   "ref_kind": "sha256_of_projected_metadata", "canonical_event": False,
                                   "tier": {"source_type": "bridge_event"}, "available_utc": TS_OUT}
    third = project(event(status="done"))["records"][0]
    assert third["provenance"]["event_ref"] != first["provenance"]["event_ref"]


@pytest.mark.parametrize("task", ["m0-sealed-cases-v1", "q4-gold-set", "testset-heldout-v1", "judge-panel-2"])
def test_s10_sealed_named_events_are_excluded_even_when_pre_approved(task):
    projection = project(event(task_id=task), approved=frozenset({task}))
    assert projection["records"] == [] and projection["refused"] == {"sealed_named_excluded": 1}
    unapproved = project(event(task_id=task))["records"][0]  # not approved: UNKNOWN, the name never appears
    assert unapproved["meta"]["task_id"] == "UNKNOWN" and task not in json.dumps(unapproved)


@pytest.mark.parametrize("change,reason", [
    ({"agent": "someone-else"}, "agent_not_lane"),
    ({"agent": "CODEX-LEAD-1"}, "agent_invalid"),
    ({"ts_utc": "yesterday"}, "time_invalid"),
    ({"ts_utc": "2026-10-31T12:00:00"}, "time_invalid"),
    ({"pr": "1973"}, "pr_invalid"),
    ({"pr": -1}, "pr_invalid"),
    ({"pr": True}, "pr_invalid"),
    ({"head": "A" * 40}, "head_invalid"),
    ({"head": "a" * 39}, "head_invalid"),
    ({"status": 7}, "status_invalid"),
    ({"ci": 7}, "ci_invalid"),
    ({"type": "Message"}, "type_invalid"),
    ({"task_id": 7}, "task_id_invalid"),
])
def test_default_deny_on_every_approved_field(change, reason):
    projection = project(event(**change))
    assert projection["records"] == [] and projection["refused"] == {reason: 1}
    assert no_sentinel(json.dumps(projection))


def test_optional_fields_absent_or_null_leave_the_head_line_short():
    record = project(event(pr=None, head=None, ci=None))["records"][0]
    assert record["stored_text"] == "message milestone codex-lead-1 " + TASK
    assert record["meta"]["pr"] is None and record["meta"]["head"] is None and record["meta"]["ci"] is None
    without = {k: v for k, v in event().items() if k not in ("pr", "head", "ci")}
    assert project(without)["records"][0] == record


def test_a_non_mapping_or_empty_batch_is_refused_or_empty():
    with pytest.raises(sp.ProjectionRefused, match="event_not_object"):
        project("not an object")
    empty = project()
    assert empty["records"] == [] and empty["accepted"] == 0 and empty["schema"] == sp.SCHEMA

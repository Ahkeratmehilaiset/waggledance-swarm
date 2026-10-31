"""Synthetic checks of the default-deny shared-record projection (RCO1 S1-S10 of the shared-scope review, plus the
v2 review: closed enums for status and CI, task ids rendered only from a pre-approved set, notes disabled, no
overwrite, projected (non-canonical) provenance).

Every input here is synthetic. Sentinels are plain words that must never reach a record, an encoder input or a file.
"""

import hashlib
import json
import os
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


def project(*events, approved=APPROVED, refs=False):
    return sp.project_events(list(events), approved_tasks=approved, include_refs=refs)


def write(out, projection, **kw):
    out.parent.mkdir(parents=True, exist_ok=True)
    return sp.write_projection(out.parent, out.name, projection, **kw)


def no_sentinel(text):
    return all(word not in text for word in SENTINELS)


def test_s1_encoder_input_equals_stored_text_and_only_approved_tokens_reach_it(capsys):
    projection = project(event(), refs=True)  # v4: refs render only on opt-in; the default is covered by the v4 tests
    assert projection["accepted"] == 1 and projection["refused"] == {}
    record = projection["records"][0]
    assert sp.encoder_input(record) == record["stored_text"] == record["summary"]
    assert record["stored_text"] == "message milestone codex-lead-1 " + TASK + " pr:1973 head:" + HEAD + " ci:green"
    assert record["kind"] == "event" and record["time_utc"] == TS_OUT and record["available_utc"] == "2026-10-31T12:01:00.000000Z"
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
    with pytest.raises(sp.ProjectionRefused, match="projection_invalid"):
        write(out, projection, approved_tasks=APPROVED)  # v3: strict revalidation refuses before the guard
    assert not out.exists() and list(tmp_path.rglob("*")) in ([], [tmp_path / "out"])


def test_s7b_a_good_write_is_exclusive_atomic_and_returns_its_digest(tmp_path):
    out = tmp_path / "out" / "projection.json"
    digest = write(out, project(event()), approved_tasks=APPROVED)
    assert out.exists() and digest == hashlib.sha256(out.read_bytes()).hexdigest()
    assert sorted(p.name for p in out.parent.iterdir()) == ["projection.json"]
    assert no_sentinel(out.read_text(encoding="utf-8"))
    before = out.read_bytes()
    with pytest.raises(sp.ProjectionRefused, match="output_exists"):  # v2: never overwrite a published file
        write(out, project(event(status="done")), approved_tasks=APPROVED)
    assert out.read_bytes() == before and sorted(p.name for p in out.parent.iterdir()) == ["projection.json"]


def test_s7c_a_filesystem_error_is_one_fixed_path_free_refusal(tmp_path):
    blocker = tmp_path / "file-not-dir"
    blocker.write_text("x", encoding="utf-8")
    with pytest.raises(sp.ProjectionRefused) as raised:
        sp.write_projection(blocker, "projection.json", project(event()), approved_tasks=APPROVED)
    assert str(raised.value) == "output_root_invalid"


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
                                   "tier": {"source_type": "bridge_event"}, "available_utc": "2026-10-31T12:01:00.000000Z"}  # v5: next boundary
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


# v3: publication races, strict revalidation at the writer, UTC boundary overflow. All synthetic, all deterministic.

def _foreign(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"foreign publication, never ours")
    return path.read_bytes()


def test_v3_a_staging_collision_never_removes_a_file_this_call_did_not_create(tmp_path, monkeypatch):
    out = tmp_path / "out" / "projection.json"
    fixed = out.with_name(".projection.json.deadbeefdeadbeef.staging")
    monkeypatch.setattr(sp, "_staging_name", lambda path: fixed)
    before = _foreign(fixed)
    with pytest.raises(sp.ProjectionRefused, match="staging_collision"):
        write(out, project(event()), approved_tasks=APPROVED)
    assert fixed.read_bytes() == before and not out.exists()
    assert sorted(p.name for p in out.parent.iterdir()) == [fixed.name]


def _racing(publish, out):
    def race(src, dst):
        _foreign(out)  # another publisher wins between our existence check and our publication step
        return publish(src, dst)
    return race


@pytest.mark.parametrize("platform,binding", [("nt", "_rename"), ("posix", "_link")])
def test_v3_a_concurrent_destination_is_never_overwritten_or_deleted(tmp_path, monkeypatch, platform, binding):
    out = tmp_path / "out" / "projection.json"
    monkeypatch.setattr(sp, "_PLATFORM", platform)
    monkeypatch.setattr(sp, binding, _racing(getattr(sp, binding), out))
    with pytest.raises(sp.ProjectionRefused, match="output_exists"):
        write(out, project(event()), approved_tasks=APPROVED)
    assert out.read_bytes() == b"foreign publication, never ours"
    assert sorted(p.name for p in out.parent.iterdir()) == ["projection.json"]  # own staging cleaned, theirs kept


def test_v3_the_posix_strategy_publishes_by_link_then_unlinks_only_its_staging(tmp_path, monkeypatch):
    out = tmp_path / "out" / "projection.json"
    monkeypatch.setattr(sp, "_PLATFORM", "posix")
    digest = write(out, project(event()), approved_tasks=APPROVED)
    assert digest == hashlib.sha256(out.read_bytes()).hexdigest()
    assert sorted(p.name for p in out.parent.iterdir()) == ["projection.json"] and out.stat().st_nlink == 1


@pytest.mark.parametrize("link", [None, lambda src, dst: (_ for _ in ()).throw(PermissionError(1, "no hard links"))])
def test_v3_an_unsupported_publication_strategy_fails_closed_with_nothing_left(tmp_path, monkeypatch, link):
    out = tmp_path / "out" / "projection.json"
    monkeypatch.setattr(sp, "_PLATFORM", "posix")
    monkeypatch.setattr(sp, "_link", link)
    with pytest.raises(sp.ProjectionRefused, match="publication_unsupported"):
        write(out, project(event()), approved_tasks=APPROVED)
    assert not out.exists() and list(out.parent.iterdir()) == []


def test_v3_an_unknown_platform_fails_closed_before_any_staging(tmp_path, monkeypatch):
    out = tmp_path / "out" / "projection.json"
    monkeypatch.setattr(sp, "_PLATFORM", "java")
    with pytest.raises(sp.ProjectionRefused, match="publication_unsupported"):
        write(out, project(event()), approved_tasks=APPROVED)
    assert list(out.parent.iterdir()) == []


def _forge(change):
    projection = project(event())
    change(projection)
    return projection


@pytest.mark.parametrize("change", [
    lambda p: p.__setitem__("schema", "wd.bridge-mind-shared-projection.v2"),
    lambda p: p.__setitem__("authority", "gate"),
    lambda p: p.__setitem__("extra", 1),
    lambda p: p.__setitem__("accepted", 2),
    lambda p: p.__setitem__("dropped_fields", True),
    lambda p: p.__setitem__("refused", {SENTINEL_TOKEN: 1}),
    lambda p: p.__setitem__("refused", {"time_invalid": 0}),
    lambda p: p["records"][0].__setitem__("stored_text", SENTINEL_MESSAGE),
    lambda p: p["records"][0].__setitem__("summary", SENTINEL_MESSAGE),
    lambda p: p["records"][0].__setitem__("record_id", "event:" + "0" * 64),
    lambda p: p["records"][0].__setitem__("note", SENTINEL_PAYLOAD),
    lambda p: p["records"][0]["meta"].__setitem__("status", SENTINEL_TOKEN),
    lambda p: p["records"][0]["meta"].__setitem__("task_id", SENTINEL_TOKEN),  # task-shaped but never approved
    lambda p: p["records"][0]["meta"].__setitem__("agent", "someone-else"),
    lambda p: p["records"][0]["meta"].__setitem__("type", "decision"),
    lambda p: p["records"][0]["meta"].__setitem__("head", SENTINEL_TOKEN),
    lambda p: p["records"][0]["meta"].__setitem__("ci", "rco_pass"),
    lambda p: p["records"][0]["provenance"].__setitem__("canonical_event", True),
    lambda p: p["records"][0].__setitem__("time_utc", TS),  # not the normalized form
    lambda p: p["records"].append("not a record"),
])
def test_v3_the_writer_revalidates_the_exact_schema_and_refuses_a_forged_projection(tmp_path, change):
    out = tmp_path / "out" / "projection.json"
    with pytest.raises(sp.ProjectionRefused, match="projection_invalid"):
        write(out, _forge(change), approved_tasks=APPROVED)
    assert not out.exists() and (not out.parent.exists() or list(out.parent.iterdir()) == [])


def test_v3_the_writer_needs_the_same_approved_set_to_accept_a_rendered_task(tmp_path):
    projection = project(event())
    with pytest.raises(sp.ProjectionRefused, match="projection_invalid"):
        write(tmp_path / "a.json", projection)  # no set: every rendered task must be UNKNOWN
    with pytest.raises(sp.ProjectionRefused, match="projection_invalid"):
        write(tmp_path / "b.json", projection, approved_tasks=frozenset({"other-task-1"}))
    assert write(tmp_path / "c.json", projection, approved_tasks=APPROVED)
    assert write(tmp_path / "d.json", project(event(), approved=None))
    assert sorted(p.name for p in tmp_path.iterdir()) == ["c.json", "d.json"]


def test_v3_the_writer_refuses_a_non_projection_value_without_touching_the_filesystem(tmp_path):
    for bad in ("text", None, [], {"records": []}):
        with pytest.raises(sp.ProjectionRefused, match="projection_invalid"):
            write(tmp_path / "out" / "projection.json", bad)
    assert list((tmp_path / "out").iterdir()) == []


def test_v3_every_refusal_reason_is_a_member_of_the_closed_reason_set():
    assert sp.REASONS >= {f"{name}_{kind}" for name in sp.BYTE_CAPS for kind in ("invalid", "over_bytes")}
    assert {"time_invalid", "agent_not_lane", "type_not_allowed", "pr_invalid", "head_invalid",
            "sealed_named_excluded", "head_line_over_bytes", "event_not_object"} <= sp.REASONS
    assert all(re.fullmatch(r"[a-z][a-z0-9_]{1,63}", reason) for reason in sp.REASONS)


@pytest.mark.parametrize("ts", ["0001-01-01T00:00:00+01:00", "9999-12-31T23:59:59-01:00"])
def test_v3_a_boundary_timestamp_is_refused_as_time_invalid_instead_of_aborting_the_batch(ts):
    out = project(event(ts_utc=ts), event())
    assert out["refused"] == {"time_invalid": 1} and out["accepted"] == 1


# v4: untrusted pr/head omitted by default, bounded time precision, publication confined to a trusted output root.

def test_v4_the_default_projection_omits_pr_and_head_and_the_head_line_has_no_refs():
    record = project(event())["records"][0]
    assert record["meta"]["pr"] is None and record["meta"]["head"] is None
    assert record["stored_text"] == f"message milestone codex-lead-1 {TASK} ci:green"
    assert "pr:" not in record["stored_text"] and HEAD not in json.dumps(project(event()))


def test_v4_refs_render_only_when_the_caller_opts_in_and_are_still_shape_validated():
    out = sp.project_events([event()], approved_tasks=APPROVED, include_refs=True)
    record = out["records"][0]
    assert record["meta"]["pr"] == 1973 and record["meta"]["head"] == HEAD and f"head:{HEAD}" in record["stored_text"]
    assert project(event(pr=True))["refused"] == {"pr_invalid": 1}  # malformed refs refuse even when omitted
    assert project(event(head=SENTINEL_TOKEN))["refused"] == {"head_invalid": 1}


def test_v4_time_is_floored_to_the_minute_so_seconds_and_fractions_carry_nothing():
    assert sp.TIME_PRECISION_SECONDS == 60
    record = project(event(ts_utc="2026-10-31T12:34:56.789012Z"))["records"][0]
    assert record["time_utc"] == "2026-10-31T12:34:00.000000Z" and record["available_utc"] == "2026-10-31T12:35:00.000000Z"
    assert record["provenance"]["available_utc"] == "2026-10-31T12:35:00.000000Z"  # v5: next boundary
    same = project(event(ts_utc="2026-10-31T12:34:00Z"))["records"][0]
    assert same["record_id"] == record["record_id"]  # one minute, one digest


def test_v4_the_writer_refuses_a_projection_carrying_refs_unless_told_they_were_opted_in(tmp_path):
    with_refs = sp.project_events([event()], approved_tasks=APPROVED, include_refs=True)
    with pytest.raises(sp.ProjectionRefused, match="projection_invalid"):
        write(tmp_path / "a.json", with_refs, approved_tasks=APPROVED)
    assert write(tmp_path / "b.json", with_refs, approved_tasks=APPROVED, include_refs=True)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["b.json"]


@pytest.mark.parametrize("name", ["", "..", "../x.json", "sub/x.json", "sub" + chr(92) + "x.json", "C:/x.json", "/x.json",
                                  ".hidden.json", "Upper.json", "x y.json", "x.json\x00", "a" * 129 + ".json",
                                  "x.txt", "x"])
def test_v4_an_output_name_must_be_one_safe_json_file_name_inside_the_root(tmp_path, name):
    with pytest.raises(sp.ProjectionRefused, match="output_name_invalid"):
        sp.write_projection(tmp_path, name, project(event()), approved_tasks=APPROVED)
    assert list(tmp_path.iterdir()) == []


def test_v4_the_output_root_must_be_an_existing_real_directory(tmp_path):
    with pytest.raises(sp.ProjectionRefused, match="output_root_invalid"):
        sp.write_projection(tmp_path / "missing", "x.json", project(event()), approved_tasks=APPROVED)
    with pytest.raises(sp.ProjectionRefused, match="output_root_invalid"):
        sp.write_projection(tmp_path / "missing" / "deeper", "x.json", project(event()), approved_tasks=APPROVED)
    assert list(tmp_path.iterdir()) == []


def test_v4_a_reparse_point_as_root_or_target_is_never_followed(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    try:
        os.symlink(real, link, target_is_directory=True)
    except (OSError, NotImplementedError):
        import subprocess
        done = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(real)], capture_output=True)
        if done.returncode != 0:
            pytest.skip("no symlink or junction privilege here")
    with pytest.raises(sp.ProjectionRefused, match="output_root_invalid"):
        sp.write_projection(link, "x.json", project(event()), approved_tasks=APPROVED)
    assert list(real.iterdir()) == []
    target_link = real / "y.json"
    try:
        os.symlink(tmp_path / "elsewhere.json", target_link)
    except (OSError, NotImplementedError):
        pytest.skip("no file symlink privilege here")
    with pytest.raises(sp.ProjectionRefused, match="output_exists"):
        sp.write_projection(real, "y.json", project(event()), approved_tasks=APPROVED)
    assert not (tmp_path / "elsewhere.json").exists() and sorted(p.name for p in real.iterdir()) == ["y.json"]


def test_v4_a_filesystem_error_after_staging_is_one_fixed_refusal_and_cleans_only_ours(tmp_path, monkeypatch):
    def broken(fd):
        raise OSError(5, "io error")
    monkeypatch.setattr(sp, "_fsync", broken)
    with pytest.raises(sp.ProjectionRefused, match="write_failed"):
        sp.write_projection(tmp_path, "x.json", project(event()), approved_tasks=APPROVED)
    assert list(tmp_path.iterdir()) == []


def test_v4_the_published_file_and_nothing_else_lands_inside_the_root(tmp_path):
    digest = sp.write_projection(tmp_path, "projection.json", project(event()), approved_tasks=APPROVED)
    assert [p.name for p in tmp_path.iterdir()] == ["projection.json"]
    assert hashlib.sha256((tmp_path / "projection.json").read_bytes()).hexdigest() == digest


# v5: availability is the NEXT minute boundary after the actual event (a within-minute query cannot see a later
# event), year-9999 overflow fails closed, include_refs is a strict boolean everywhere.

def test_v5_availability_is_the_next_minute_boundary_after_the_actual_event_time():
    late = project(event(ts_utc="2026-10-31T12:34:59.500000Z"))["records"][0]
    assert late["time_utc"] == "2026-10-31T12:34:00.000000Z"  # display stays floored for privacy
    assert late["available_utc"] == late["provenance"]["available_utc"] == "2026-10-31T12:35:00.000000Z"
    exact = project(event(ts_utc="2026-10-31T12:34:00Z"))["records"][0]
    assert exact["available_utc"] == "2026-10-31T12:35:00.000000Z"  # never available in the minute it happened
    assert exact["record_id"] == late["record_id"]  # one minute, one digest; availability is derived, not hashed


@pytest.mark.parametrize("query,guard,visible", [
    ("2026-10-31T12:34:10Z", 0, False),          # within the minute, before the event
    ("2026-10-31T12:34:59.999999Z", 0, False),   # within the minute, after the event
    ("2026-10-31T12:35:00Z", 0, False),          # the boundary itself: strict compare, same as Q4
    ("2026-10-31T12:35:00.000001Z", 0, True),
    ("2026-10-31T12:35:20Z", 30, False),         # guard band pushes the visible instant later
    ("2026-10-31T12:35:40Z", 30, True),
])
def test_v5_a_within_minute_query_cannot_see_a_later_event_and_the_rule_matches_q4(query, guard, visible):
    record = project(event(ts_utc="2026-10-31T12:34:59Z"))["records"][0]
    assert sp.visible_at(record, query, guard_band_s=guard) is visible
    from sandbox.mind.step2 import corpus as corpus_mod
    import datetime as dt
    limit = corpus_mod.utc_text(corpus_mod.parse_utc(query) - dt.timedelta(seconds=guard))
    assert (record["provenance"]["available_utc"] < limit) is visible  # exactly q4.ask: available < limit


def test_v5_a_query_time_that_does_not_parse_is_not_visible():
    record = project(event())["records"][0]
    assert sp.visible_at(record, "not a time") is False and sp.visible_at(record, None) is False


def test_v5_the_last_minute_of_year_9999_fails_closed_instead_of_overflowing():
    assert project(event(ts_utc="9999-12-31T23:59:30Z"))["refused"] == {"time_invalid": 1}
    ok = project(event(ts_utc="9999-12-31T23:58:30Z"))["records"][0]
    assert ok["available_utc"] == "9999-12-31T23:59:00.000000Z"


@pytest.mark.parametrize("flag", ["false", "False", "", 0, 1, None, [], 1.0])
def test_v5_include_refs_must_be_a_strict_boolean_on_every_entry_point(tmp_path, flag):
    with pytest.raises(sp.ProjectionRefused, match="include_refs_invalid"):
        sp.project_events([event()], approved_tasks=APPROVED, include_refs=flag)
    with pytest.raises(sp.ProjectionRefused, match="include_refs_invalid"):
        sp.project_event(event(), APPROVED, flag)
    good = project(event())
    with pytest.raises(sp.ProjectionRefused, match="include_refs_invalid"):
        sp.validate_projection(good, APPROVED, flag)
    with pytest.raises(sp.ProjectionRefused, match="include_refs_invalid"):
        sp.write_projection(tmp_path, "x.json", good, approved_tasks=APPROVED, include_refs=flag)
    assert list(tmp_path.iterdir()) == []
    assert HEAD not in json.dumps(good)


# ---- v6: RCO2 production fix spec S5 (peer-review signal excluded) and S2 (roster-consistent authorship) ----

ROSTER = {"codex-lead-1": "aaaa1111-1111-4111-8111-111111111aaa", "codex-tools-1": "bbbb2222-2222-4222-8222-222222222bbb",
          "claude-rco-1": "cccc3333-3333-4333-8333-333333333ccc", "claude-rco-2": "dddd4444-4444-4444-8444-444444444ddd",
          "fable-5": "eeee5555-5555-4555-8555-555555555eee"}
FOREIGN_UUID = "ffff9999-9999-4999-8999-999999999fff"


def fleet_document(roster=ROSTER):
    return {"schema_version": 1, "fleet_name": "synthetic", "lanes": [{"agent": agent, "agent_uuid": uuid, "role": "x"}
                                                                      for agent, uuid in roster.items()]}


def test_v6_review_lane_rows_are_refused_only_counted_when_the_exclusion_is_on():
    rco = event(agent="claude-rco-2", type="message", status="answered", agent_uuid=ROSTER["claude-rco-2"])
    strict = sp.project_events([rco], approved_tasks=APPROVED, exclude_review_signal=True)
    assert strict["accepted"] == 0 and strict["refused"] == {"review_lane_excluded": 1}
    assert strict["review_signal_excluded"] is True and strict["schema"] == sp.SCHEMA == "wd.bridge-mind-shared-projection.v6"
    assert "claude-rco-2" not in json.dumps(strict)  # not even the lane name of the excluded row survives
    loose = project(rco)
    assert loose["accepted"] == 1 and loose["review_signal_excluded"] is False
    assert sp.REVIEW_LANES == frozenset({"claude-rco-1", "claude-rco-2"}) and sp.REVIEW_LANES < sp.LANES
    for lane in sp.REVIEW_LANES:
        for kind in ("message", "claim", "done", "milestone"):
            out = sp.project_events([event(agent=lane, type=kind, status="done")], approved_tasks=APPROVED,
                                    exclude_review_signal=True)
            assert out["refused"] == {"review_lane_excluded": 1}


def test_v6_a_task_id_with_a_slash_is_refused_instead_of_unknown_when_the_exclusion_is_on():
    pr_branch = event(agent="codex-lead-1", task_id="fable-5/mind-x")
    strict = sp.project_events([pr_branch], approved_tasks=APPROVED, exclude_review_signal=True)
    assert strict["accepted"] == 0 and strict["refused"] == {"pr_branch_task_excluded": 1}
    assert "mind-x" not in json.dumps(strict)
    loose = project(pr_branch)
    assert loose["accepted"] == 1 and loose["records"][0]["meta"]["task_id"] == "UNKNOWN"
    kept = sp.project_events([event(agent="codex-lead-1")], approved_tasks=APPROVED, exclude_review_signal=True)
    assert kept["accepted"] == 1 and kept["records"][0]["meta"]["task_id"] == TASK  # Lead row on an approved task
    assert {"review_lane_excluded", "pr_branch_task_excluded", "agent_identity_mismatch"} <= sp.REASONS


def test_v6_roster_consistent_authorship_compares_the_uuid_and_never_persists_it():
    good = sp.project_events([event(agent="fable-5", agent_uuid=ROSTER["fable-5"])], approved_tasks=APPROVED,
                             roster=ROSTER)
    assert good["accepted"] == 1 and good["authorship"] == sp.AUTHORSHIP_ROSTER == "declared_roster_consistent"
    serialized = json.dumps(good, sort_keys=True)
    assert ROSTER["fable-5"] not in serialized and "agent_uuid" not in serialized and "authenticated" not in serialized
    for bad in (dict(agent="fable-5", agent_uuid=ROSTER["claude-rco-1"]), dict(agent="fable-5", agent_uuid=FOREIGN_UUID),
                dict(agent="fable-5", agent_uuid=None), dict(agent="fable-5", agent_uuid=ROSTER["fable-5"].upper()),
                dict(agent="fable-5", agent_uuid=[ROSTER["fable-5"]])):
        row = event(**bad)
        if bad["agent_uuid"] is None:
            del row["agent_uuid"]
        out = sp.project_events([row], approved_tasks=APPROVED, roster=ROSTER)
        assert out["accepted"] == 0 and out["refused"] == {"agent_identity_mismatch": 1}
        assert ROSTER["claude-rco-1"] not in json.dumps(out) and FOREIGN_UUID not in json.dumps(out)
    loose = project(event(agent="fable-5", agent_uuid=FOREIGN_UUID))
    assert loose["accepted"] == 1 and loose["authorship"] == sp.AUTHORSHIP_UNVERIFIED == "declared_unverified"
    with pytest.raises(sp.ProjectionRefused, match="agent_identity_mismatch"):
        sp.project_event(event(agent="codex-lead-1", agent_uuid=FOREIGN_UUID), APPROVED, False, roster=ROSTER)


@pytest.mark.parametrize("roster", [
    [], "roster", dict(ROSTER, extra="66666666-6666-4666-8666-666666666666"), {"not-a-lane": ROSTER["fable-5"]},
    dict(ROSTER, **{"fable-5": "not-a-uuid"}), dict(ROSTER, **{"fable-5": ROSTER["codex-lead-1"]}),
    dict(ROSTER, **{"fable-5": 5}), dict(ROSTER, **{"fable-5": ROSTER["fable-5"].upper()}), {},
])
def test_v6_the_roster_is_a_closed_lane_to_uuid_mapping(roster):
    with pytest.raises(sp.ProjectionRefused, match="roster_invalid"):
        sp.project_events([event()], approved_tasks=APPROVED, roster=roster)
    with pytest.raises(sp.ProjectionRefused, match="roster_invalid"):
        sp.validate_projection(project(event()), APPROVED, False, roster=roster)


def test_v6_a_lane_absent_from_a_partial_roster_cannot_project_while_the_roster_is_pinned():
    partial = {k: v for k, v in ROSTER.items() if k != "fable-5"}  # a fleet file that does not list every lane
    out = sp.project_events([event(agent="fable-5", agent_uuid=ROSTER["fable-5"]),
                             event(agent="codex-lead-1", agent_uuid=ROSTER["codex-lead-1"])],
                            approved_tasks=APPROVED, roster=partial)
    assert out["accepted"] == 1 and out["refused"] == {"agent_identity_mismatch": 1}
    assert out["records"][0]["meta"]["agent"] == "codex-lead-1"
    with pytest.raises(sp.ProjectionRefused, match="projection_invalid"):
        sp.validate_projection(sp.project_events([event(agent="fable-5", agent_uuid=ROSTER["fable-5"])],
                                                 approved_tasks=APPROVED, roster=ROSTER), APPROVED, False, roster=partial)


def test_v6_roster_from_fleet_and_the_pinned_loader(tmp_path):
    assert sp.roster_from_fleet(fleet_document()) == ROSTER
    assert sp.roster_from_fleet(fleet_document({k: v for k, v in ROSTER.items() if k != "claude-rco-1"})) == {
        k: v for k, v in ROSTER.items() if k != "claude-rco-1"}  # a lane the fleet file does not list is absent
    for bad in ({"lanes": "x"}, {}, {"lanes": []}, {"lanes": [{"agent": "grok-helper", "agent_uuid": FOREIGN_UUID}]},
                {"lanes": fleet_document()["lanes"] + fleet_document()["lanes"][:1]},
                {"lanes": [dict(lane, agent_uuid=FOREIGN_UUID.upper()) for lane in fleet_document()["lanes"]]},
                {"lanes": [dict(lane, agent_uuid=None) for lane in fleet_document()["lanes"]]}):
        with pytest.raises(sp.ProjectionRefused, match="roster_invalid"):
            sp.roster_from_fleet(bad)
    extra = fleet_document()
    extra["lanes"].append({"agent": "grok-helper", "agent_uuid": FOREIGN_UUID})  # a non-projection lane is ignored
    assert sp.roster_from_fleet(extra) == ROSTER
    path = tmp_path / "wd-fleet.json"
    raw = json.dumps(fleet_document(), indent=1).encode("utf-8")
    path.write_bytes(raw)
    assert sp.load_roster(path, hashlib.sha256(raw).hexdigest()) == ROSTER
    with pytest.raises(sp.ProjectionRefused, match="roster_pin_mismatch"):
        sp.load_roster(path, "0" * 64)
    with pytest.raises(sp.ProjectionRefused, match="roster_pin_invalid"):
        sp.load_roster(path, "zz")
    with pytest.raises(sp.ProjectionRefused, match="roster_unreadable"):
        sp.load_roster(tmp_path / "missing.json", hashlib.sha256(raw).hexdigest())
    path.write_bytes(b"{")
    with pytest.raises(sp.ProjectionRefused, match="roster_unreadable"):
        sp.load_roster(path, hashlib.sha256(b"{").hexdigest())


def test_v6_the_real_fleet_file_on_this_branch_yields_a_roster_for_every_projection_lane():
    import pathlib
    fleet = pathlib.Path(sp.__file__).resolve().parents[3] / "ops" / "windows" / "reboot" / "wd-fleet.json"
    roster = sp.roster_from_fleet(json.loads(fleet.read_bytes()))
    assert {"codex-lead-1", "fable-5", "claude-rco-1", "claude-rco-2"} <= set(roster) <= sp.LANES
    assert all(sp.UUID.fullmatch(uuid) for uuid in roster.values()) and len(set(roster.values())) == len(roster)
    # the repo copy at this base lists four lanes (no codex-tools-1): under this roster a Tools row cannot project
    out = sp.project_events([event(agent="codex-tools-1", agent_uuid=FOREIGN_UUID)], approved_tasks=APPROVED, roster=roster)
    assert out["accepted"] == (1 if "codex-tools-1" in roster else 0)


def test_v6_validate_projection_binds_authorship_and_the_exclusion_flag():
    events = [event(agent="codex-lead-1", agent_uuid=ROSTER["codex-lead-1"]),
              event(agent="claude-rco-1", agent_uuid=ROSTER["claude-rco-1"], type="message", status="answered")]
    strict = sp.project_events(events, approved_tasks=APPROVED, roster=ROSTER, exclude_review_signal=True)
    assert strict["accepted"] == 1 and strict["refused"] == {"review_lane_excluded": 1}
    assert sp.validate_projection(strict, APPROVED, False, roster=ROSTER, exclude_review_signal=True) is strict
    for kw in (dict(roster=None, exclude_review_signal=True), dict(roster=ROSTER, exclude_review_signal=False), {}):
        with pytest.raises(sp.ProjectionRefused, match="projection_invalid"):
            sp.validate_projection(strict, APPROVED, False, **kw)  # the same policy must be pinned by the validator
    loose = project(*events)
    assert loose["accepted"] == 2
    with pytest.raises(sp.ProjectionRefused, match="projection_invalid"):
        sp.validate_projection(loose, APPROVED, False, roster=ROSTER, exclude_review_signal=True)  # an RCO record
    # Documented limit, not a defect: the uuid is never persisted, so a relabelled authorship on a loose projection is
    # indistinguishable from a roster-checked one. Only the writer/builder pin (which re-projects or validates with its
    # own roster) decides; a consumer must never trust the label of a projection it did not build or validate itself.
    relabelled = dict(loose, authorship=sp.AUTHORSHIP_ROSTER)
    assert sp.validate_projection(relabelled, APPROVED, False, roster=ROSTER) is relabelled
    relabelled_off_roster = dict(relabelled, records=[dict(r, meta=dict(r["meta"], agent="fable-5")) for r in relabelled["records"]])
    with pytest.raises(sp.ProjectionRefused, match="projection_invalid"):
        sp.validate_projection(relabelled_off_roster, APPROVED, False, roster={k: v for k, v in ROSTER.items()} | {"fable-5": FOREIGN_UUID})
    forged = dict(strict, review_signal_excluded=False)
    with pytest.raises(sp.ProjectionRefused, match="projection_invalid"):
        sp.validate_projection(forged, APPROVED, False, roster=ROSTER, exclude_review_signal=True)
    forged = dict(strict, records=[dict(strict["records"][0], meta=dict(strict["records"][0]["meta"], agent="claude-rco-2"))])
    with pytest.raises(sp.ProjectionRefused, match="projection_invalid"):
        sp.validate_projection(forged, APPROVED, False, roster=ROSTER, exclude_review_signal=True)
    assert set(strict) == sp.PROJECTION_KEYS >= {"authorship", "review_signal_excluded"}


@pytest.mark.parametrize("flag", ["false", "True", "", 0, 1, None, [], 1.0])
def test_v6_the_exclusion_flag_is_a_strict_boolean_on_every_entry_point(tmp_path, flag):
    with pytest.raises(sp.ProjectionRefused, match="exclude_review_signal_invalid"):
        sp.project_events([event()], approved_tasks=APPROVED, exclude_review_signal=flag)
    with pytest.raises(sp.ProjectionRefused, match="exclude_review_signal_invalid"):
        sp.project_event(event(), APPROVED, False, exclude_review_signal=flag)
    good = project(event())
    with pytest.raises(sp.ProjectionRefused, match="exclude_review_signal_invalid"):
        sp.validate_projection(good, APPROVED, False, exclude_review_signal=flag)
    with pytest.raises(sp.ProjectionRefused, match="exclude_review_signal_invalid"):
        sp.write_projection(tmp_path, "x.json", good, approved_tasks=APPROVED, exclude_review_signal=flag)
    assert list(tmp_path.iterdir()) == []


def test_v6_write_projection_pins_the_same_policy_and_the_file_carries_it(tmp_path):
    strict = sp.project_events([event(agent="codex-lead-1", agent_uuid=ROSTER["codex-lead-1"])], approved_tasks=APPROVED,
                               roster=ROSTER, exclude_review_signal=True)
    with pytest.raises(sp.ProjectionRefused, match="projection_invalid"):
        write(tmp_path / "out" / "p.json", strict, approved_tasks=APPROVED)  # policy not pinned by the writer
    assert not (tmp_path / "out" / "p.json").exists()
    write(tmp_path / "out" / "p.json", strict, approved_tasks=APPROVED, roster=ROSTER, exclude_review_signal=True)
    stored = json.loads((tmp_path / "out" / "p.json").read_bytes())
    assert stored["authorship"] == "declared_roster_consistent" and stored["review_signal_excluded"] is True
    assert ROSTER["codex-lead-1"] not in (tmp_path / "out" / "p.json").read_text(encoding="utf-8")

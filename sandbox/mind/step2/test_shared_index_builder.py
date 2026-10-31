"""Synthetic tests for the shared projection builder: a validated projection in, a verifiable frozen memory out.

No model, no real events, no real memory: the encoder is a deterministic bag-of-words fake, every event is built
here with sentinel words in its forbidden fields, and every write goes to tmp_path.
"""

import hashlib
import json
import os

import numpy as np
import pytest

from sandbox.mind.step2 import cell as cell_mod
from sandbox.mind.step2 import memory as memory_mod
from sandbox.mind.step2 import q4 as q4_mod
from sandbox.mind.step2 import shared_projection as sp
from sandbox.mind.step2 import shared_index_builder as build_mod
from waggledance.core import hex_cell_topology as topology_mod

DIM = 16
TASK = "lead-mind-shared-projection-fable-20261031-v1"
APPROVED = frozenset({TASK, "other-task-1"})
SENTINEL = "zebra-message-body-7f3a"
IDENTITY = {"model": "fake-e5-small", "revision": "a" * 40, "weights_sha256": "b" * 64, "dim": DIM}


class FakeEncoder:
    """Deterministic hashed bag of words; the same words give the same vector."""

    def vector(self, text):
        out = np.zeros(DIM, dtype=np.float32)
        for word in q4_mod.tokens(text):
            out[int(hashlib.sha256(word.encode()).hexdigest(), 16) % DIM] += 1.0
        norm = np.linalg.norm(out)
        return (out / norm if norm else np.ones(DIM, dtype=np.float32) / np.sqrt(DIM)).astype("<f4")

    def __call__(self, texts):
        return np.vstack([self.vector(text) for text in texts])

    def query(self, text):
        return self.vector(text)


def event(**changes):
    base = {"ts_utc": "2026-10-31T12:00:30Z", "agent": "codex-lead-1", "type": "message", "status": "milestone",
            "task_id": TASK, "ci": "green", "message": SENTINEL, "payload": {"detail": SENTINEL},
            "paths": ["C:/" + SENTINEL], "run_id": SENTINEL, "to": "fable-5"}
    base.update(changes)
    return base


EVENTS = [event(), event(type="wake_request", status="requested", agent="claude-rco-1", ts_utc="2026-10-31T12:05:00Z"),
          event(type="test", status="info", agent="fable-5", ts_utc="2026-10-31T12:10:59Z"),
          event(type="done", status="blocked", agent="codex-tools-1", ts_utc="2026-10-31T12:20:00Z")]


def projection(events=None):
    return sp.project_events(list(EVENTS if events is None else events), approved_tasks=APPROVED)


def build(root, name="shared-v1", proj=None, encode=None, identity=IDENTITY, **kw):
    kw.setdefault("approved_tasks", APPROVED)
    return build_mod.build_memory(projection() if proj is None else proj, encode or FakeEncoder(), identity, root,
                                  name, **kw)


def files_under(root):
    return sorted(str(path.relative_to(root)).replace(os.sep, "/") for path in root.rglob("*") if path.is_file())


def test_round_trip_builds_a_verifiable_memory_that_q4_answers_from_without_text(tmp_path):
    out = build(tmp_path)
    memory_dir = tmp_path / "shared-v1"
    loaded = memory_mod.Memory(memory_dir)
    assert {cell_id: view.status for cell_id, view in loaded.views.items()} == {
        "general": "HEALTHY", "safety": "HEALTHY", "system": "HEALTHY", "learning": "HEALTHY"}
    assert out["memory_sha256"] == memory_mod.memory_sha256(memory_dir)
    engine = q4_mod.Q4(loaded, FakeEncoder())
    answer = engine.ask("message milestone from lead", "2026-10-31T13:00:00Z")
    assert answer["status"] == "ANSWER"
    refs = {result["ref"] for result in answer["results"]}
    assert refs and all(len(ref) == 64 for ref in refs)
    assert all(result["tier"] == "bridge_event" for result in answer["results"])
    assert SENTINEL not in json.dumps(answer)


def test_availability_binds_into_q4_no_record_is_a_candidate_inside_its_own_minute(tmp_path):
    build(tmp_path)
    engine = q4_mod.Q4(memory_mod.Memory(tmp_path / "shared-v1"), FakeEncoder())
    inside = engine.ask("message milestone from lead", "2026-10-31T12:00:45Z")  # same minute as the first event
    assert inside["telemetry"]["candidates"] == 0 and inside["status"] != "ANSWER"
    later = engine.ask("message milestone from lead", "2026-10-31T12:01:30Z")
    assert later["telemetry"]["candidates"] >= 1


def test_placement_is_a_closed_table_over_projected_type_with_blocked_to_safety():
    assert set(build_mod.PLACEMENT) == set(sp.ALLOWED_TYPES)
    assert set(build_mod.PLACEMENT.values()) | {build_mod.BLOCKED_CELL} <= set(topology_mod.ALL_CELLS)
    records = projection()["records"]
    assert [build_mod.place(record) for record in records] == ["general", "system", "learning", "safety"]
    with pytest.raises(build_mod.BuildRefused, match="placement_unknown"):
        build_mod.place({"meta": {"type": "decision", "status": "none"}})


def test_cutoff_is_the_latest_availability_and_every_row_lies_before_it(tmp_path):
    out = build(tmp_path)
    memory = json.loads((tmp_path / "shared-v1" / "memory.json").read_bytes())
    assert memory["cutoff_utc"] == out["descriptor"]["cutoff_utc"] == "2026-10-31T12:21:00.000000Z"
    for cell_id in memory["cells"]:
        rows = (tmp_path / "shared-v1" / "cells" / cell_id / "gen-000001" / "provenance.jsonl").read_text().splitlines()
        for raw in rows:
            row = json.loads(raw)
            assert row["time_utc"] < memory["cutoff_utc"] and row["provenance"]["available_utc"] <= memory["cutoff_utc"]
            assert row["provenance"]["view"] in ("w", "d") and row["record_id"].endswith("#" + row["provenance"]["view"])
            assert row["provenance"]["canonical_event"] is False


def test_descriptor_carries_identity_and_digests_but_no_text(tmp_path):
    out = build(tmp_path)
    path = tmp_path / "shared-v1" / "descriptor.json"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == out["descriptor_sha256"]
    descriptor = json.loads(path.read_bytes())
    assert descriptor == out["descriptor"]
    assert descriptor["schema"] == "wd.bridge-mind-q4-memory-descriptor.v2" and descriptor["scope"] == "wd.q4-shared-projected-events.v1"
    assert descriptor["encoder"] == IDENTITY and descriptor["projection_schema"] == sp.SCHEMA
    assert descriptor["cells"] == {"general": 2, "safety": 2, "system": 2, "learning": 2} and descriptor["records"] == 4
    assert descriptor["authority"] == "none" and descriptor["canonical_event"] is False and descriptor["authenticated"] is False
    assert descriptor["duplicates"] == 0 and descriptor["views"] == ["w", "d"]
    text = json.dumps(descriptor)
    assert "codex-lead-1" not in text and TASK not in text and "message" not in text.replace("memory", "")


def test_nothing_but_projected_metadata_reaches_any_file(tmp_path):
    build(tmp_path)
    for path in (tmp_path / "shared-v1").rglob("*"):
        if path.is_file():
            raw = path.read_bytes()
            assert SENTINEL.encode() not in raw
    texts = json.loads((tmp_path / "shared-v1" / "cells" / "general" / "gen-000001" / "faiss" / "cell_general"
                        / "meta.json").read_bytes())["texts"]
    assert texts == [f"message milestone codex-lead-1 {TASK} ci:green"] * 2  # both views, one head line


def test_a_second_build_under_the_same_name_never_overwrites(tmp_path):
    build(tmp_path)
    before = {name: (tmp_path / name).read_bytes() for name in files_under(tmp_path)}
    with pytest.raises(build_mod.BuildRefused, match="memory_exists"):
        build(tmp_path, proj=projection(EVENTS[:1]))
    assert {name: (tmp_path / name).read_bytes() for name in files_under(tmp_path)} == before


@pytest.mark.parametrize("name", ["", "..", "a/b", "A", ".hidden", "x y", "a" * 65, "a\\b"])
def test_memory_name_must_be_one_safe_directory_name(tmp_path, name):
    with pytest.raises(build_mod.BuildRefused, match="memory_name_invalid"):
        build(tmp_path, name=name)
    assert list(tmp_path.iterdir()) == []


def test_root_must_be_a_real_directory_and_target_must_not_exist_in_any_form(tmp_path):
    with pytest.raises(sp.ProjectionRefused, match="output_root_invalid"):
        build(tmp_path / "missing")
    (tmp_path / "taken").write_text("x", encoding="utf-8")
    with pytest.raises(build_mod.BuildRefused, match="memory_exists"):
        build(tmp_path, name="taken")
    assert (tmp_path / "taken").read_text(encoding="utf-8") == "x"
    try:
        os.symlink(tmp_path / "elsewhere", tmp_path / "linked", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("no symlink privilege here")
    with pytest.raises(build_mod.BuildRefused, match="memory_exists"):
        build(tmp_path, name="linked")
    assert not (tmp_path / "elsewhere").exists()


def test_forged_or_untold_projection_is_refused_before_any_filesystem_action(tmp_path):
    forged = projection()
    forged["records"][0]["stored_text"] = SENTINEL
    with pytest.raises(sp.ProjectionRefused, match="projection_invalid"):
        build(tmp_path, proj=forged)
    with pytest.raises(sp.ProjectionRefused, match="projection_invalid"):
        build(tmp_path, approved_tasks=None)  # rendered tasks need the same approved set
    with_refs = sp.project_events([event(pr=7, head="c" * 40)], approved_tasks=APPROVED, include_refs=True)
    with pytest.raises(sp.ProjectionRefused, match="projection_invalid"):
        build(tmp_path, proj=with_refs)
    with pytest.raises(sp.ProjectionRefused, match="include_refs_invalid"):
        build(tmp_path, include_refs="false")
    with pytest.raises(build_mod.BuildRefused, match="projection_empty"):
        build(tmp_path, proj=projection([]))
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("identity", [
    None, {}, dict(IDENTITY, extra=1), {k: v for k, v in IDENTITY.items() if k != "revision"},
    dict(IDENTITY, revision="A" * 40), dict(IDENTITY, weights_sha256="b" * 63), dict(IDENTITY, dim="16"),
    dict(IDENTITY, dim=0), dict(IDENTITY, model="../x"), dict(IDENTITY, model=""),
])
def test_encoder_identity_is_a_closed_shape_checked_record(tmp_path, identity):
    with pytest.raises(build_mod.BuildRefused, match="encoder_identity_invalid"):
        build(tmp_path, identity=identity)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("encode", [
    lambda texts: np.zeros((len(texts), DIM), dtype=np.float32),            # zero norm
    lambda texts: np.full((len(texts), DIM), np.nan, dtype=np.float32),     # not finite
    lambda texts: np.ones((len(texts), DIM + 1), dtype=np.float32),         # wrong width
    lambda texts: np.ones((len(texts) + 1, DIM), dtype=np.float32),         # wrong count
    lambda texts: "not an array",
    lambda texts: (_ for _ in ()).throw(TypeError("boom")),
])
def test_an_encoder_that_misbehaves_is_refused_with_nothing_written(tmp_path, encode):
    with pytest.raises(build_mod.BuildRefused, match="encoder_output_invalid"):
        build(tmp_path, encode=encode)
    assert list(tmp_path.iterdir()) == []


def test_a_non_callable_encoder_is_refused():
    with pytest.raises(build_mod.BuildRefused, match="encoder_invalid"):
        build_mod.build_memory(projection(), "not callable", IDENTITY, ".", "x", approved_tasks=APPROVED)


def test_a_failure_after_creation_removes_only_the_own_memory_directory(tmp_path, monkeypatch):
    (tmp_path / "foreign.txt").write_text("keep me", encoding="utf-8")
    original = build_mod._publish_cell
    calls = []

    def broken(cell_dir, cell_id, *args, **kw):
        calls.append(cell_id)
        if len(calls) == 2:
            raise OSError(5, "io error")
        return original(cell_dir, cell_id, *args, **kw)
    monkeypatch.setattr(build_mod, "_publish_cell", broken)
    with pytest.raises(build_mod.BuildRefused, match="write_failed"):
        build(tmp_path)
    assert files_under(tmp_path) == ["foreign.txt"] and (tmp_path / "foreign.txt").read_text(encoding="utf-8") == "keep me"


def test_an_unverifiable_generation_is_refused_and_removed(tmp_path, monkeypatch):
    def refuse(*args, **kw):
        raise cell_mod.CellError("pairs_mismatch")
    monkeypatch.setattr(build_mod.cell_mod, "verify_generation", refuse)
    with pytest.raises(build_mod.BuildRefused, match="generation_unverifiable"):
        build(tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_memory_json_binds_every_cell_and_a_rolled_back_receipt_is_quarantined(tmp_path):
    build(tmp_path)
    memory_dir = tmp_path / "shared-v1"
    memory = json.loads((memory_dir / "memory.json").read_bytes())
    assert memory["schema"] == memory_mod.SCHEMA and memory["dim"] == DIM and memory["records"] == 4
    assert memory["encoder"] == IDENTITY and memory["placement"] == build_mod.PLACEMENT_RULE
    receipt = json.loads((memory_dir / "cells" / "general" / "gen-000001" / "receipt.json").read_bytes())
    assert receipt["content"]["encoder"] == IDENTITY and receipt["content"]["projection_sha256"] == memory["projection_sha256"]
    assert receipt["scope"] == build_mod.SCOPE == "wd.q4-shared-projected-events.v1" and receipt["source"] == "shared_index_builder"
    assert memory["scope"] == build_mod.SCOPE and memory["duplicates"] == 0
    pointer = memory_dir / "cells" / "general" / "current.json"
    tampered = dict(json.loads(pointer.read_bytes()), receipt_sha256="0" * 64)
    pointer.write_bytes(cell_mod.canonical(tampered).encode("utf-8"))
    assert memory_mod.Memory(memory_dir).views["general"].status == "QUARANTINED"


def test_duplicate_events_in_one_minute_are_one_record_deterministically(tmp_path):
    twice = projection([event(), event(message="other body", ts_utc="2026-10-31T12:00:59Z")] + EVENTS[1:])
    assert twice["accepted"] == 5
    out = build(tmp_path, proj=twice)
    assert out["descriptor"]["records"] == 4 and out["descriptor"]["duplicates"] == 1
    assert out["descriptor"]["cells"] == {"general": 2, "safety": 2, "system": 2, "learning": 2}
    again = build(tmp_path, name="shared-v2", proj=twice)
    assert again["descriptor"]["cells"] == out["descriptor"]["cells"]
    first = json.loads((tmp_path / "shared-v1" / "cells" / "general" / "gen-000001" / "receipt.json").read_bytes())
    second = json.loads((tmp_path / "shared-v2" / "cells" / "general" / "gen-000001" / "receipt.json").read_bytes())
    assert first["content_sha256"] == second["content_sha256"]  # same content, independent of the duplicate


def test_an_absent_event_is_never_found_and_a_present_one_is_found_by_its_projected_ref(tmp_path):
    build(tmp_path)
    engine = q4_mod.Q4(memory_mod.Memory(tmp_path / "shared-v1"), FakeEncoder())
    absent = engine.ask("thermal seasonal energy forecast", "2026-10-31T13:00:00Z")
    assert absent["status"] != "ANSWER" or all(
        result["ref"] in {record["provenance"]["event_ref"] for record in projection()["records"]}
        for result in absent["results"])
    present = engine.ask("wake request from rco", "2026-10-31T13:00:00Z")
    wanted = projection()["records"][1]["provenance"]["event_ref"]
    assert present["status"] == "ANSWER" and present["results"][0]["ref"] == wanted
    assert SENTINEL not in json.dumps(present)


def test_a_tampered_provenance_row_is_quarantined_by_the_receipt_checksum(tmp_path):
    build(tmp_path)
    path = tmp_path / "shared-v1" / "cells" / "system" / "gen-000001" / "provenance.jsonl"
    rows = path.read_text(encoding="utf-8").splitlines()
    row = json.loads(rows[0])
    row["provenance"]["available_utc"] = "2026-10-31T12:00:00.000000Z"  # try to make it visible earlier
    rows[0] = cell_mod.canonical(row)
    path.write_text(chr(10).join(rows) + chr(10), encoding="utf-8")
    assert memory_mod.Memory(tmp_path / "shared-v1").views["system"].status == "QUARANTINED"
    assert memory_mod.Memory(tmp_path / "shared-v1").views["system"].reason == "provenance_checksum"

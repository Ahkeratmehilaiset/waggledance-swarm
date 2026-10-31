"""Synthetic tests for the shared index builder: a validated projection in, a verifiable frozen memory out.

No model, no real events, no real memory: the encoder is a deterministic bag-of-words fake, every event is built
here with sentinel words in its forbidden fields, and every write goes to tmp_path. The v2 tests exercise the REAL
Lead reader (``wd_q4_private_read.read_descriptor`` + ``bind_memory``) and the real ``q4.Q4`` on builder output.
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
from sandbox.mind.step2 import wd_q4_private_read as reader
from waggledance.core import hex_cell_topology as topology_mod

DIM = 16
TASK = "lead-mind-shared-projection-fable-20261031-v1"
APPROVED = frozenset({TASK, "other-task-1"})
SENTINEL = "zebra-message-body-7f3a"
IDENTITY = {"model": "fake-e5-small", "revision": "a" * 40, "weights_sha256": "b" * 64}


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


def build(root, name="shared-v1", proj=None, encode=None, identity=IDENTITY, dim=DIM, **kw):
    kw.setdefault("approved_tasks", APPROVED)
    return build_mod.build_memory(projection() if proj is None else proj, encode or FakeEncoder(), identity, dim,
                                  root, name, **kw)


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


def test_the_real_lead_reader_accepts_the_descriptor_binds_the_memory_and_q4_answers(tmp_path):
    out = build(tmp_path)
    memory_dir = tmp_path / "shared-v1"
    descriptor = reader.read_descriptor(memory_dir / "descriptor.json", out["descriptor_sha256"])
    assert descriptor == out["descriptor"]
    memory = reader.bind_memory(memory_dir, descriptor)
    assert len(memory.views) == 4 and all(view.healthy for view in memory.views.values())
    answer = q4_mod.Q4(memory, FakeEncoder()).ask("message milestone from lead", "2026-10-31T13:00:00Z")
    assert answer["status"] == "ANSWER" and answer["rule"] == "q4-rule-v1.2" and SENTINEL not in json.dumps(answer)
    assert all(item["ref_kind"] == q4_mod.PROJECTED_REF_KIND and item["canonical_event"] is False
               for item in answer["results"])
    with pytest.raises(reader.PrivateReadRefused, match="descriptor_pin_mismatch"):
        reader.read_descriptor(memory_dir / "descriptor.json", "0" * 64)
    other = build(tmp_path, name="shared-other", identity=dict(IDENTITY, revision="c" * 40))
    assert other["descriptor"]["encoder"]["revision"] == "c" * 40
    with pytest.raises(reader.PrivateReadRefused, match="memory_descriptor_mismatch"):
        reader.bind_memory(tmp_path / "shared-other", descriptor)  # a descriptor never binds another memory


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
    assert memory["cutoff_utc"] == out["build_receipt"]["cutoff_utc"] == "2026-10-31T12:21:00.000000Z"
    for cell_id in memory["cells"]:
        rows = (tmp_path / "shared-v1" / "cells" / cell_id / "gen-000001" / "provenance.jsonl").read_text().splitlines()
        for raw in rows:
            row = json.loads(raw)
            assert row["time_utc"] < memory["cutoff_utc"] and row["provenance"]["available_utc"] <= memory["cutoff_utc"]
            assert row["provenance"]["view"] in ("w", "d") and row["record_id"].endswith("#" + row["provenance"]["view"])
            assert row["provenance"]["canonical_event"] is False


def test_descriptor_is_the_exact_reader_shape_and_the_build_receipt_carries_the_details(tmp_path):
    out = build(tmp_path)
    memory_dir = tmp_path / "shared-v1"
    path = memory_dir / "descriptor.json"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == out["descriptor_sha256"]
    descriptor = json.loads(path.read_bytes())
    assert descriptor == out["descriptor"] == {
        "schema": "wd.bridge-mind-q4-memory-descriptor.v2", "scope": "wd.q4-shared-projected-events.v1",
        "memory_sha256": out["memory_sha256"], "dim": DIM, "encoder": IDENTITY}
    receipt_path = memory_dir / "build_receipt.json"
    assert hashlib.sha256(receipt_path.read_bytes()).hexdigest() == out["build_receipt_sha256"]
    receipt = json.loads(receipt_path.read_bytes())
    assert receipt == out["build_receipt"]
    assert receipt["schema"] == build_mod.BUILD_RECEIPT_SCHEMA and receipt["scope"] == build_mod.SCOPE
    assert receipt["memory_sha256"] == out["memory_sha256"] and receipt["descriptor_sha256"] == out["descriptor_sha256"]
    assert receipt["projection_sha256"] == out["projection_sha256"] and receipt["projection_schema"] == sp.SCHEMA
    assert receipt["cells"] == {"general": 2, "safety": 2, "system": 2, "learning": 2} and receipt["records"] == 4
    assert receipt["duplicates"] == 0 and receipt["views"] == ["w", "d"] and receipt["dim"] == DIM
    assert receipt["encoder"] == IDENTITY and receipt["memory_name"] == "shared-v1" and receipt["authority"] == "none"
    assert receipt["canonical_event"] is False and receipt["authenticated"] is False
    assert receipt["independent_views"] is False  # both views are the same embedding of one head line
    text = json.dumps(descriptor) + json.dumps(receipt)
    assert "codex-lead-1" not in text and TASK not in text and SENTINEL not in text


def test_the_validated_canonical_projection_is_persisted_once_and_reads_back_under_its_pinned_sha(tmp_path):
    proj = projection()
    out = build(tmp_path, proj=proj)
    memory_dir = tmp_path / "shared-v1"
    raw = (memory_dir / "projection.json").read_bytes()
    assert raw == json.dumps(proj, sort_keys=True, indent=1).encode("utf-8")
    sha = hashlib.sha256(raw).hexdigest()
    memory = json.loads((memory_dir / "memory.json").read_bytes())
    assert sha == out["projection_sha256"] == memory["projection_sha256"] == out["build_receipt"]["projection_sha256"]
    assert memory["projection_schema"] == sp.SCHEMA == json.loads(raw)["schema"]
    receipt = json.loads((memory_dir / "cells" / "general" / "gen-000001" / "receipt.json").read_bytes())
    assert receipt["content"]["projection_sha256"] == sha
    sp.validate_projection(json.loads(raw.decode("utf-8")), APPROVED, False)  # the persisted copy still validates
    assert SENTINEL.encode() not in raw
    refs = {record["provenance"]["event_ref"] for record in json.loads(raw)["records"]}
    answer = q4_mod.Q4(memory_mod.Memory(memory_dir), FakeEncoder()).ask("wake request from rco", "2026-10-31T13:00:00Z")
    assert answer["status"] == "ANSWER" and {item["ref"] for item in answer["results"]} <= refs  # refs resolve


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
    None, {}, dict(IDENTITY, extra=1), dict(IDENTITY, dim=DIM), {k: v for k, v in IDENTITY.items() if k != "revision"},
    dict(IDENTITY, revision="A" * 40), dict(IDENTITY, weights_sha256="b" * 63), dict(IDENTITY, model="../x"),
    dict(IDENTITY, model=""), dict(IDENTITY, model=7),
])
def test_encoder_identity_is_the_closed_three_key_reader_record(tmp_path, identity):
    with pytest.raises(build_mod.BuildRefused, match="encoder_identity_invalid"):
        build(tmp_path, identity=identity)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("dim", ["16", 16.0, 0, -1, True, build_mod.MAX_DIM + 1, None])
def test_dim_is_a_separate_bounded_integer(tmp_path, dim):
    with pytest.raises(build_mod.BuildRefused, match="dim_invalid"):
        build(tmp_path, dim=dim)
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


@pytest.mark.parametrize("error", [RuntimeError("C:/secret/path leaked " + SENTINEL), MemoryError(), SystemExit(3)])
def test_every_encoder_exception_is_sanitized_to_one_fixed_refusal_without_its_text(tmp_path, error):
    def raising(texts):
        raise error
    with pytest.raises(build_mod.BuildRefused) as raised:
        build(tmp_path, encode=raising)
    assert str(raised.value) == "encoder_output_invalid"
    assert raised.value.__cause__ is None and raised.value.__context__ is None
    assert list(tmp_path.iterdir()) == []


def test_an_interrupt_during_encoding_is_not_swallowed_and_nothing_is_written(tmp_path):
    def interrupted(texts):
        raise KeyboardInterrupt
    with pytest.raises(KeyboardInterrupt):
        build(tmp_path, encode=interrupted)
    assert list(tmp_path.iterdir()) == []


def test_bounds_refuse_too_many_records_or_too_many_input_bytes_before_any_encoder_call(tmp_path, monkeypatch):
    calls = []

    def counting(texts):
        calls.append(len(texts))
        return FakeEncoder()(texts)
    monkeypatch.setattr(build_mod, "MAX_RECORDS", 3)
    with pytest.raises(build_mod.BuildRefused, match="records_over_bound"):
        build(tmp_path, encode=counting)
    assert calls == [] and list(tmp_path.iterdir()) == []
    monkeypatch.setattr(build_mod, "MAX_RECORDS", 4)
    monkeypatch.setattr(build_mod, "MAX_INPUT_BYTES", 10)
    with pytest.raises(build_mod.BuildRefused, match="input_over_bound"):
        build(tmp_path, encode=counting)
    assert calls == [] and list(tmp_path.iterdir()) == []
    monkeypatch.setattr(build_mod, "MAX_INPUT_BYTES", 1 << 20)
    monkeypatch.setattr(build_mod, "ENCODE_BATCH", 3)
    assert build(tmp_path, encode=counting)["memory_sha256"]
    assert calls == [3, 1]  # four distinct head lines encoded in bounded batches
    assert (build_mod.MAX_RECORDS, build_mod.MAX_INPUT_BYTES, build_mod.ENCODE_BATCH) == (4, 1 << 20, 3)


def test_default_bounds_are_conservative_and_a_batch_that_returns_the_wrong_count_is_refused(tmp_path, monkeypatch):
    assert 0 < build_mod.ENCODE_BATCH <= 1024 and 0 < build_mod.MAX_RECORDS <= 65536
    assert 0 < build_mod.MAX_INPUT_BYTES <= 64 * 1024 * 1024
    monkeypatch.setattr(build_mod, "ENCODE_BATCH", 2)
    with pytest.raises(build_mod.BuildRefused, match="encoder_output_invalid"):
        build(tmp_path, encode=lambda texts: np.ones((4, DIM), dtype=np.float32))  # ignores the batch it was given
    assert list(tmp_path.iterdir()) == []


def test_the_e5_passage_adapter_matches_the_original_policy_with_a_stub_model():
    seen = {}

    class StubModel:
        def encode(self, inputs, **kwargs):
            seen["inputs"], seen["kwargs"] = list(inputs), dict(kwargs)
            return np.ones((len(inputs), build_mod.E5_DIM), dtype=np.float64)
    encode = build_mod.e5_passage_encoder(StubModel())
    long_text = "x" * (build_mod.E5_TRUNCATE + 50)
    vectors = encode(["head line one", long_text])
    assert seen["inputs"] == ["passage: head line one", "passage: " + "x" * build_mod.E5_TRUNCATE]
    assert seen["kwargs"] == {"batch_size": build_mod.E5_PASSAGE_BATCH, "normalize_embeddings": True,
                              "convert_to_numpy": True, "show_progress_bar": False}
    assert vectors.dtype == np.dtype("<f4") and vectors.shape == (2, build_mod.E5_DIM) and vectors.flags["C_CONTIGUOUS"]
    assert (build_mod.E5_PASSAGE_BATCH, build_mod.E5_TRUNCATE, build_mod.E5_DIM) == (32, 2000, 384)
    with pytest.raises(build_mod.BuildRefused, match="encoder_invalid"):
        build_mod.e5_passage_encoder(object())  # no encode method: not a loaded model
    with pytest.raises(build_mod.BuildRefused, match="encoder_invalid"):
        build_mod.e5_passage_encoder(None)


def test_the_e5_passage_adapter_builds_through_the_normal_route_with_a_stub_model(tmp_path):
    class StubModel:
        def encode(self, inputs, **kwargs):
            return FakeEncoder()([text[len("passage: "):] for text in inputs]).astype(np.float64)
    out = build(tmp_path, encode=build_mod.e5_passage_encoder(StubModel()))
    assert memory_mod.Memory(tmp_path / "shared-v1").views["general"].healthy and out["descriptor"]["dim"] == DIM


def test_a_non_callable_encoder_is_refused():
    with pytest.raises(build_mod.BuildRefused, match="encoder_invalid"):
        build_mod.build_memory(projection(), "not callable", IDENTITY, DIM, ".", "x", approved_tasks=APPROVED)


def test_a_failure_after_creation_retains_the_incomplete_directory_with_a_report_and_deletes_nothing(tmp_path,
                                                                                                        monkeypatch):
    (tmp_path / "foreign.txt").write_text("keep me", encoding="utf-8")
    original = build_mod._publish_cell
    calls = []

    def broken(cell_dir, cell_id, *args, **kw):
        calls.append(cell_id)
        if len(calls) == 2:
            raise OSError(5, "io error " + SENTINEL)
        return original(cell_dir, cell_id, *args, **kw)
    monkeypatch.setattr(build_mod, "_publish_cell", broken)
    before = files_under(tmp_path)
    with pytest.raises(build_mod.BuildRefused, match="write_failed"):
        build(tmp_path)
    memory_dir = tmp_path / "shared-v1"
    assert (tmp_path / "foreign.txt").read_text(encoding="utf-8") == "keep me"
    assert set(before) <= set(files_under(tmp_path))  # nothing that existed before is gone
    assert memory_dir.is_dir() and not (memory_dir / "memory.json").exists()
    assert not (memory_dir / "descriptor.json").exists() and not (memory_dir / "build_receipt.json").exists()
    report = json.loads((memory_dir / "INCOMPLETE.json").read_bytes())
    assert report["schema"] == build_mod.INCOMPLETE_SCHEMA and report["scope"] == build_mod.SCOPE
    assert report["reason"] == "write_failed" and report["stage"] == "cells" and report["complete"] is False
    assert SENTINEL not in json.dumps(report) and "io error" not in json.dumps(report)
    assert (memory_dir / "cells" / "general" / "gen-000001" / "receipt.json").exists()  # the first cell stays
    with pytest.raises(memory_mod.MemoryError_):
        memory_mod.Memory(memory_dir)  # an incomplete directory never loads as a memory
    with pytest.raises(build_mod.BuildRefused, match="memory_exists"):
        build(tmp_path)  # the retained directory is never reused or removed
    assert (memory_dir / "INCOMPLETE.json").exists()
    assert build(tmp_path, name="shared-v1-retry")["memory_sha256"]


def test_an_unverifiable_generation_is_refused_and_retained_as_incomplete(tmp_path, monkeypatch):
    def refuse(*args, **kw):
        raise cell_mod.CellError("pairs_mismatch")
    monkeypatch.setattr(build_mod.cell_mod, "verify_generation", refuse)
    with pytest.raises(build_mod.BuildRefused, match="generation_unverifiable"):
        build(tmp_path)
    memory_dir = tmp_path / "shared-v1"
    report = json.loads((memory_dir / "INCOMPLETE.json").read_bytes())
    assert report["reason"] == "generation_unverifiable" and report["stage"] == "cells"
    assert not (memory_dir / "memory.json").exists()
    staged = [path for path in (memory_dir / "cells" / "general").iterdir() if path.name.startswith(".staging-")]
    assert len(staged) == 1 and not (memory_dir / "cells" / "general" / "gen-000001").exists()


def test_a_guarded_object_after_creation_is_reported_as_output_guarded(tmp_path, monkeypatch):
    original = sp.corpus_mod.ws.guard_object

    def guard(obj):
        if isinstance(obj, dict) and obj.get("schema") == memory_mod.SCHEMA:  # only the memory manifest trips it
            raise sp.corpus_mod.ws.WorkspaceError("guarded")
        return original(obj)
    monkeypatch.setattr(sp.corpus_mod.ws, "guard_object", guard)
    with pytest.raises(build_mod.BuildRefused, match="output_guarded"):
        build(tmp_path)
    report = json.loads((tmp_path / "shared-v1" / "INCOMPLETE.json").read_bytes())
    assert report["reason"] == "output_guarded" and report["stage"] == "finalize"
    assert not (tmp_path / "shared-v1" / "memory.json").exists()
    assert not (tmp_path / "shared-v1" / "descriptor.json").exists()
    assert not (tmp_path / "shared-v1" / "build_receipt.json").exists()


def _assert_unloadable_and_refused_by_the_real_reader(memory_dir):
    """An incomplete generation: no memory.json, Memory() refuses, and the real reader refuses on every route."""
    assert not (memory_dir / "memory.json").exists()
    assert not any(path.name.startswith(".memory.json") for path in memory_dir.iterdir())  # no torn staging left loadable
    with pytest.raises(memory_mod.MemoryError_):
        memory_mod.Memory(memory_dir)
    descriptor_path = memory_dir / "descriptor.json"
    if descriptor_path.exists():
        pin = hashlib.sha256(descriptor_path.read_bytes()).hexdigest()
        descriptor = reader.read_descriptor(descriptor_path, pin)  # a complete descriptor may exist ...
        with pytest.raises(reader.PrivateReadRefused, match="memory_descriptor_unreadable"):
            reader.bind_memory(memory_dir, descriptor)  # ... but it can never bind: there is no memory.json
    else:
        with pytest.raises(reader.PrivateReadRefused, match="descriptor_unreadable"):
            reader.read_descriptor(descriptor_path, "0" * 64)


@pytest.mark.parametrize("target, stage", [("build_receipt.json", "build_receipt"), ("descriptor.json", "descriptor"),
                                           ("memory.json", "memory")])
def test_an_injected_write_failure_of_any_final_file_leaves_no_loadable_memory(tmp_path, monkeypatch, target, stage):
    original = build_mod._write_new

    def failing(path, data):
        if target in os.path.basename(str(path)):
            raise OSError(28, "no space " + SENTINEL)
        return original(path, data)
    monkeypatch.setattr(build_mod, "_write_new", failing)
    with pytest.raises(build_mod.BuildRefused, match="write_failed"):
        build(tmp_path)
    memory_dir = tmp_path / "shared-v1"
    report = json.loads((memory_dir / "INCOMPLETE.json").read_bytes())
    assert report["reason"] == "write_failed" and report["stage"] == stage and SENTINEL not in json.dumps(report)
    _assert_unloadable_and_refused_by_the_real_reader(memory_dir)
    assert all((memory_dir / "cells" / cell_id / "gen-000001" / "receipt.json").exists()
               for cell_id in ("general", "system", "learning", "safety"))  # the cells themselves are retained
    with pytest.raises(build_mod.BuildRefused, match="memory_exists"):
        build(tmp_path)


def test_a_rename_failure_at_the_commit_point_leaves_no_memory_json(tmp_path, monkeypatch):
    def refuse(src, dst):
        raise OSError(5, "io " + SENTINEL)
    monkeypatch.setattr(build_mod, "_rename", refuse)
    with pytest.raises(build_mod.BuildRefused, match="write_failed"):
        build(tmp_path)
    memory_dir = tmp_path / "shared-v1"
    report = json.loads((memory_dir / "INCOMPLETE.json").read_bytes())
    assert report["reason"] == "write_failed" and report["stage"] == "memory"
    assert not (memory_dir / "memory.json").exists() and (memory_dir / "descriptor.json").exists()
    with pytest.raises(memory_mod.MemoryError_):
        memory_mod.Memory(memory_dir)
    staged = [path for path in memory_dir.iterdir() if path.name.startswith(".memory.json.staging-")]
    assert len(staged) == 1  # the fully written staging copy is retained, never promoted


def test_memory_json_is_the_last_file_written_and_the_commit_point_of_a_generation(tmp_path, monkeypatch):
    order = []
    original_write, original_rename = build_mod._write_new, build_mod._rename

    def recording_write(path, data):
        order.append(("write", os.path.basename(str(path))))
        return original_write(path, data)

    def recording_rename(src, dst):
        order.append(("rename", os.path.basename(str(dst))))
        return original_rename(src, dst)
    monkeypatch.setattr(build_mod, "_write_new", recording_write)
    monkeypatch.setattr(build_mod, "_rename", recording_rename)
    build(tmp_path)
    names = [name for _, name in order]
    assert names[0] == "projection.json" and order[-1] == ("rename", "memory.json")
    assert names.index("build_receipt.json") < names.index("descriptor.json") < len(names) - 1
    assert all(name.startswith(".memory.json.staging-") for kind, name in order[-2:-1])
    assert not any(path.name.startswith(".memory.json") for path in (tmp_path / "shared-v1").iterdir())
    assert not (tmp_path / "shared-v1" / "INCOMPLETE.json").exists()


def test_a_truncated_memory_json_is_refused_by_the_loader_and_the_real_reader(tmp_path):
    out = build(tmp_path)
    memory_dir = tmp_path / "shared-v1"
    raw = (memory_dir / "memory.json").read_bytes()
    (memory_dir / "memory.json").write_bytes(raw[: len(raw) // 2])  # a torn manifest, simulated after the fact
    with pytest.raises(memory_mod.MemoryError_):
        memory_mod.Memory(memory_dir)
    descriptor = reader.read_descriptor(memory_dir / "descriptor.json", out["descriptor_sha256"])
    with pytest.raises(reader.PrivateReadRefused, match="memory_descriptor_mismatch"):
        reader.bind_memory(memory_dir, descriptor)


def test_memory_json_binds_every_cell_and_a_rolled_back_receipt_is_quarantined(tmp_path):
    build(tmp_path)
    memory_dir = tmp_path / "shared-v1"
    memory = json.loads((memory_dir / "memory.json").read_bytes())
    assert memory["schema"] == memory_mod.SCHEMA and memory["dim"] == DIM and memory["records"] == 4
    assert memory["encoder"] == IDENTITY and memory["placement"] == build_mod.PLACEMENT_RULE
    receipt = json.loads((memory_dir / "cells" / "general" / "gen-000001" / "receipt.json").read_bytes())
    assert receipt["content"]["encoder"] == IDENTITY and receipt["content"]["projection_sha256"] == memory["projection_sha256"]
    assert receipt["content"]["dim"] == DIM
    assert receipt["scope"] == build_mod.SCOPE == "wd.q4-shared-projected-events.v1" and receipt["source"] == "shared_index_builder"
    assert memory["scope"] == build_mod.SCOPE and memory["duplicates"] == 0 and memory["independent_views"] is False
    pointer = memory_dir / "cells" / "general" / "current.json"
    tampered = dict(json.loads(pointer.read_bytes()), receipt_sha256="0" * 64)
    pointer.write_bytes(cell_mod.canonical(tampered).encode("utf-8"))
    assert memory_mod.Memory(memory_dir).views["general"].status == "QUARANTINED"


def test_duplicate_events_in_one_minute_are_one_record_deterministically(tmp_path):
    twice = projection([event(), event(message="other body", ts_utc="2026-10-31T12:00:59Z")] + EVENTS[1:])
    assert twice["accepted"] == 5
    out = build(tmp_path, proj=twice)
    assert out["build_receipt"]["records"] == 4 and out["build_receipt"]["duplicates"] == 1
    assert out["build_receipt"]["cells"] == {"general": 2, "safety": 2, "system": 2, "learning": 2}
    again = build(tmp_path, name="shared-v2", proj=twice)
    assert again["build_receipt"]["cells"] == out["build_receipt"]["cells"]
    first = json.loads((tmp_path / "shared-v1" / "cells" / "general" / "gen-000001" / "receipt.json").read_bytes())
    second = json.loads((tmp_path / "shared-v2" / "cells" / "general" / "gen-000001" / "receipt.json").read_bytes())
    assert first["content_sha256"] == second["content_sha256"]  # same content, independent of the duplicate


def test_every_returned_ref_is_a_projected_ref_and_a_present_event_is_found_by_its_own_ref(tmp_path):
    """NOT a proof of absence: Q4 may still ANSWER an unrelated situation with projected records (ranking only
    consensus, no absent-query rejection). The claim here is only that no ref outside the projection can appear,
    and that the status is one of the closed Q4 statuses."""
    build(tmp_path)
    engine = q4_mod.Q4(memory_mod.Memory(tmp_path / "shared-v1"), FakeEncoder())
    projected = {record["provenance"]["event_ref"] for record in projection()["records"]}
    unrelated = engine.ask("thermal seasonal energy forecast", "2026-10-31T13:00:00Z")
    assert unrelated["status"] in {"ANSWER", "ABSTAIN"}
    assert all(result["ref"] in projected for result in unrelated["results"])
    assert SENTINEL not in json.dumps(unrelated)
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

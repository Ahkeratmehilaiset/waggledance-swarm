"""Synthetic tests for the shared index builder: a validated projection in, a verifiable frozen memory out.

No model, no real events, no real memory: the encoder is a deterministic bag-of-words fake, every event is built
here with sentinel words in its forbidden fields, and every write goes to tmp_path. The v2 tests exercise the REAL
Lead reader (``wd_q4_private_read.read_descriptor`` + ``bind_memory``) and the real ``q4.Q4`` on builder output.
"""

import hashlib
import json
import os
import sys

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
# v6 strict policy: every builder input is projected under a pinned roster with the review signal excluded.
ROSTER = {"codex-lead-1": "aaaa1111-1111-4111-8111-111111111aaa", "codex-tools-1": "bbbb2222-2222-4222-8222-222222222bbb",
          "claude-rco-1": "cccc3333-3333-4333-8333-333333333ccc", "claude-rco-2": "dddd4444-4444-4444-8444-444444444ddd",
          "fable-5": "eeee5555-5555-4555-8555-555555555eee"}
RUNTIME = {"generation": "gen-20261031-synthetic", "manifest_sha256": "c" * 64}
FOREIGN_UUID = "ffff9999-9999-4999-8999-999999999fff"


def sha(raw):
    return hashlib.sha256(raw if isinstance(raw, bytes) else raw.encode("utf-8")).hexdigest()


def pinned(document):
    raw = json.dumps(document, sort_keys=True, indent=1).encode("utf-8")
    return raw, sha(raw)


# v4 (Lead bound request 17:13:50Z + 17:15:50Z): the builder takes the caller's PINNED policy and roster snapshot bytes
# with their digests; the roster snapshot is a separate immutable document (lanes + tools_supervisor only, no memory or
# deployment fields, Lead 17:19:17Z) so the memory.policy digest chain is acyclic.
POLICY_DOC = {"schema": "wd.q4-non-review-policy.v1", "approved_tasks": sorted(APPROVED)}
ROSTER_DOC = {"schema": "wd.q4-roster-snapshot.v1",
              "lanes": [{"agent": agent, "agent_uuid": uuid} for agent, uuid in ROSTER.items() if agent != "codex-tools-1"],
              "tools_supervisor": {"agent": "codex-tools-1", "agent_uuid": ROSTER["codex-tools-1"]}}
POLICY_BYTES, POLICY_SHA = pinned(POLICY_DOC)
ROSTER_BYTES, ROSTER_SHA = pinned(ROSTER_DOC)
PINS = {"policy_bytes": POLICY_BYTES, "policy_sha256": POLICY_SHA, "roster_bytes": ROSTER_BYTES, "roster_sha256": ROSTER_SHA}
STRICT_POLICY = {"authorship": "declared_roster_consistent", "review_signal_excluded": True,
                 "policy_sha256": POLICY_SHA, "roster_sha256": ROSTER_SHA}


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
    base.setdefault("agent_uuid", ROSTER.get(base["agent"]))
    return base


EVENTS = [event(), event(type="wake_request", status="requested", agent="codex-tools-1", ts_utc="2026-10-31T12:05:00Z"),
          event(type="test", status="info", agent="fable-5", ts_utc="2026-10-31T12:10:59Z"),
          event(type="done", status="blocked", agent="codex-tools-1", ts_utc="2026-10-31T12:20:00Z")]


def projection(events=None, **kw):
    kw.setdefault("roster", ROSTER)
    kw.setdefault("exclude_review_signal", True)
    return sp.project_events(list(EVENTS if events is None else events), approved_tasks=APPROVED, **kw)


def build(root, name="shared-v1", proj=None, encode=None, identity=IDENTITY, dim=DIM, **kw):
    kw.setdefault("approved_tasks", APPROVED)
    kw.setdefault("roster", ROSTER)
    kw.setdefault("exclude_review_signal", True)
    kw.setdefault("pins", PINS)
    kw.setdefault("allow_unbound_source", True)  # synthetic route: no snapshot, recorded as unbound in the receipt
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
    sp.validate_projection(json.loads(raw.decode("utf-8")), APPROVED, False, roster=ROSTER,
                           exclude_review_signal=True)  # the persisted copy still validates under the same policy
    assert SENTINEL.encode() not in raw
    refs = {record["provenance"]["event_ref"] for record in json.loads(raw)["records"]}
    answer = q4_mod.Q4(memory_mod.Memory(memory_dir), FakeEncoder()).ask("wake request from tools", "2026-10-31T13:00:00Z")
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
    with pytest.raises(build_mod.BuildRefused, match="output_root_invalid"):
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
    with pytest.raises(build_mod.BuildRefused, match="projection_invalid"):  # B4: one refusal class at the entry
        build(tmp_path, proj=forged)
    narrower_policy = json.dumps({"schema": "wd.q4-non-review-policy.v1", "approved_tasks": ["other-task-1"]}).encode()
    with pytest.raises(build_mod.BuildRefused, match="projection_invalid"):  # rendered tasks need the PINNED approved set
        build(tmp_path, approved_tasks=None, pins=dict(PINS, policy_bytes=narrower_policy, policy_sha256=sha(narrower_policy)))
    with_refs = projection([event(pr=7, head="c" * 40)], include_refs=True)
    with pytest.raises(build_mod.BuildRefused, match="projection_invalid"):
        build(tmp_path, proj=with_refs)
    with pytest.raises(build_mod.BuildRefused, match="include_refs_invalid"):
        build(tmp_path, include_refs="false")
    with pytest.raises(build_mod.BuildRefused, match="projection_empty"):
        build(tmp_path, proj=projection([]))
    assert list(tmp_path.iterdir()) == []
    assert issubclass(build_mod.BuildRefused, ValueError) and not issubclass(build_mod.BuildRefused, sp.ProjectionRefused)


def test_the_builder_requires_the_strict_v6_policy_roster_and_review_exclusion(tmp_path):
    loose = sp.project_events(list(EVENTS), approved_tasks=APPROVED)  # v5 behaviour: no roster, no exclusion
    with pytest.raises(build_mod.BuildRefused, match="projection_policy_weak"):
        build(tmp_path, proj=loose)
    no_roster = sp.project_events(list(EVENTS), approved_tasks=APPROVED, exclude_review_signal=True)
    with pytest.raises(build_mod.BuildRefused, match="projection_policy_weak"):
        build(tmp_path, proj=no_roster)
    not_excluded = sp.project_events(list(EVENTS), approved_tasks=APPROVED, roster=ROSTER)
    with pytest.raises(build_mod.BuildRefused, match="projection_policy_weak"):
        build(tmp_path, proj=not_excluded)
    with pytest.raises(build_mod.BuildRefused, match="pin_caller_mismatch"):
        build(tmp_path, roster={k: v for k, v in ROSTER.items() if k != "fable-5"})  # not the pinned roster snapshot
    with pytest.raises(build_mod.BuildRefused, match="pins_required"):
        build(tmp_path, pins=None)
    with pytest.raises(build_mod.BuildRefused, match="roster_invalid"):
        build(tmp_path, roster="not a roster")
    for lax in (False, None, 1, "True"):
        with pytest.raises(build_mod.BuildRefused, match="review_exclusion_required"):
            build(tmp_path, exclude_review_signal=lax)  # the builder has no lax mode, not even a truthy non-bool
    assert list(tmp_path.iterdir()) == []
    with_rco = projection(list(EVENTS) + [event(agent="claude-rco-1", type="message", status="answered")])
    assert with_rco["refused"] == {} and with_rco["accepted"] == 4  # the review row leaves no record and no count
    out = build(tmp_path, proj=with_rco)
    assert out["build_receipt"]["records"] == 4 and out["build_receipt"]["policy"] == STRICT_POLICY
    assert "claude-rco-1" not in json.dumps(out) and "cccc3333" not in json.dumps(out)
    for path in (tmp_path / "shared-v1").rglob("*"):
        if path.is_file():
            assert b"cccc3333" not in path.read_bytes() and b"claude-rco-1" not in path.read_bytes()


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
        build_mod.build_memory(projection(), "not callable", IDENTITY, DIM, ".", "x", approved_tasks=APPROVED,
                               roster=ROSTER, exclude_review_signal=True, pins=PINS, allow_unbound_source=True)


def _fake_model_dir(tmp_path, weights=b"synthetic-weights-" + b"\x00" * 64):
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    (model_dir / "model.safetensors").write_bytes(weights)
    return model_dir, hashlib.sha256(weights).hexdigest()


def test_runtime_identity_is_derived_from_the_verified_model_dir_and_recorded_with_the_generation(tmp_path):
    from sandbox.mind.step2 import encode as encode_mod
    model_dir, weights_sha = _fake_model_dir(tmp_path)
    runtime = dict(RUNTIME, model_dir=str(model_dir), revision="a" * 40)
    derived = build_mod.runtime_identity(runtime)
    assert derived["identity"] == {"model": encode_mod.MODEL, "revision": "a" * 40, "weights_sha256": weights_sha}
    assert derived["weights_bytes"] == 18 + 64 and derived["generation"] == RUNTIME["generation"]
    assert derived["manifest_sha256"] == RUNTIME["manifest_sha256"]
    out = build(tmp_path, identity=None, runtime=runtime)  # identity DERIVED, not asserted by the caller
    assert out["descriptor"]["encoder"] == derived["identity"]
    receipt = out["build_receipt"]
    assert receipt["runtime"] == {"generation": RUNTIME["generation"], "manifest_sha256": RUNTIME["manifest_sha256"],
                                  "weights_bytes": 82, "identity_source": "encode.model_identity"}
    assert str(model_dir) not in json.dumps(receipt) and "model_dir" not in json.dumps(receipt)
    same = build(tmp_path, name="shared-asserted", identity=derived["identity"], runtime=runtime)  # both given: equal
    assert same["descriptor"]["encoder"] == derived["identity"]
    with pytest.raises(build_mod.BuildRefused, match="encoder_identity_mismatch"):
        build(tmp_path, name="shared-wrong", identity=IDENTITY, runtime=runtime)  # asserted != derived
    caller_only = build(tmp_path, name="shared-caller", identity=IDENTITY)  # synthetic route, no runtime
    assert caller_only["build_receipt"]["runtime"] == {"identity_source": "caller_asserted"}


@pytest.mark.parametrize("change, reason", [
    (dict(model_dir="missing"), "encoder_identity_unavailable"),
    (dict(revision=""), "encoder_identity_unavailable"),
    (dict(generation="Bad Generation!"), "runtime_invalid"),
    (dict(manifest_sha256="c" * 63), "runtime_invalid"),
    (dict(extra=1), "runtime_invalid"),
    ("not a dict", "runtime_invalid"),
])
def test_runtime_record_is_closed_and_a_missing_model_is_one_fixed_refusal(tmp_path, change, reason):
    model_dir, _ = _fake_model_dir(tmp_path)
    runtime = dict(RUNTIME, model_dir=str(model_dir), revision="a" * 40)
    if isinstance(change, dict):
        runtime.update(change)
        if change.get("model_dir") == "missing":
            runtime["model_dir"] = str(tmp_path / "nowhere")
    else:
        runtime = change
    with pytest.raises(build_mod.BuildRefused, match=reason) as raised:
        build(tmp_path, identity=None, runtime=runtime)
    assert str(tmp_path) not in str(raised.value) and raised.value.__cause__ is None
    assert not (tmp_path / "shared-v1").exists()


def test_without_a_runtime_the_caller_identity_is_required():
    with pytest.raises(build_mod.BuildRefused, match="encoder_identity_invalid"):
        build_mod.build_memory(projection(), FakeEncoder(), None, DIM, ".", "x", approved_tasks=APPROVED, roster=ROSTER,
                               exclude_review_signal=True, pins=PINS, allow_unbound_source=True)


@pytest.mark.parametrize("error", [RuntimeError("faiss " + SENTINEL), AttributeError("x " + SENTINEL), ZeroDivisionError()])
def test_b1_any_backend_exception_after_creation_is_one_fixed_refusal_with_an_incomplete_report(tmp_path, monkeypatch, error):
    def broken(*args, **kw):
        raise error
    monkeypatch.setattr(build_mod, "_publish_cell", broken)
    with pytest.raises(build_mod.BuildRefused, match="write_failed") as raised:
        build(tmp_path)
    assert raised.value.__cause__ is None and SENTINEL not in repr(raised.value)
    report = json.loads((tmp_path / "shared-v1" / "INCOMPLETE.json").read_bytes())
    assert report["reason"] == "write_failed" and report["stage"] == "cells" and SENTINEL not in json.dumps(report)
    assert not (tmp_path / "shared-v1" / "memory.json").exists()


def test_p3_posix_commit_uses_link_and_a_competitor_written_after_the_check_is_preserved(tmp_path, monkeypatch):
    competitor = b'{"schema": "competitor", "mine": true}'
    monkeypatch.setattr(build_mod, "_PLATFORM", "posix")

    def racing_link(src, dst):
        with open(dst, "xb") as handle:  # a competitor wins between the lstat check and the link
            handle.write(competitor)
        return os.link(src, dst)
    monkeypatch.setattr(build_mod, "_link", racing_link)
    with pytest.raises(build_mod.BuildRefused, match="memory_exists"):
        build(tmp_path)
    memory_dir = tmp_path / "shared-v1"
    assert (memory_dir / "memory.json").read_bytes() == competitor  # theirs, untouched
    report = json.loads((memory_dir / "INCOMPLETE.json").read_bytes())
    assert report["reason"] == "memory_exists" and report["stage"] == "memory"
    staged = [path for path in memory_dir.iterdir() if path.name.startswith(".memory.json.staging-")]
    assert len(staged) == 1  # ours is retained, never promoted, never removed


def test_p3_nt_commit_refuses_when_a_competitor_appears_after_the_check(tmp_path, monkeypatch):
    if os.name != "nt":
        pytest.skip("nt rename semantics are only exercised on Windows")
    competitor = b'{"schema": "competitor"}'
    monkeypatch.setattr(build_mod, "_PLATFORM", "nt")

    def racing_rename(src, dst):
        with open(dst, "xb") as handle:
            handle.write(competitor)
        return os.rename(src, dst)  # MoveFileEx without REPLACE_EXISTING: fails on an existing destination
    monkeypatch.setattr(build_mod, "_rename", racing_rename)
    with pytest.raises(build_mod.BuildRefused, match="memory_exists"):
        build(tmp_path)
    assert (tmp_path / "shared-v1" / "memory.json").read_bytes() == competitor


def test_p3_posix_link_success_followed_by_staging_cleanup_failure_is_still_published(tmp_path, monkeypatch):
    monkeypatch.setattr(build_mod, "_PLATFORM", "posix")
    monkeypatch.setattr(build_mod, "_link", os.link)  # NTFS supports hard links: the posix route runs for real here

    def failing_unlink(path):
        raise OSError(13, "permission " + SENTINEL)
    monkeypatch.setattr(build_mod, "_unlink", failing_unlink)
    out = build(tmp_path)
    memory_dir = tmp_path / "shared-v1"
    assert (memory_dir / "memory.json").exists() and not (memory_dir / "INCOMPLETE.json").exists()
    assert out["memory_sha256"] == memory_mod.memory_sha256(memory_dir)
    staged = [path for path in memory_dir.iterdir() if path.name.startswith(".memory.json.staging-")]
    assert len(staged) == 1 and staged[0].read_bytes() == (memory_dir / "memory.json").read_bytes()  # same inode
    descriptor = reader.read_descriptor(memory_dir / "descriptor.json", out["descriptor_sha256"])
    assert reader.bind_memory(memory_dir, descriptor).views["general"].healthy  # published and readable


def test_p3_posix_without_hard_links_and_an_unknown_platform_fail_closed_before_any_write(tmp_path, monkeypatch):
    monkeypatch.setattr(build_mod, "_PLATFORM", "posix")
    monkeypatch.setattr(build_mod, "_link", None)
    with pytest.raises(build_mod.BuildRefused, match="publication_unsupported"):
        build(tmp_path)
    monkeypatch.setattr(build_mod, "_PLATFORM", "java")
    monkeypatch.setattr(build_mod, "_link", os.link)
    with pytest.raises(build_mod.BuildRefused, match="publication_unsupported"):
        build(tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_p3_posix_link_refused_by_the_filesystem_at_commit_time_is_retained_as_incomplete(tmp_path, monkeypatch):
    monkeypatch.setattr(build_mod, "_PLATFORM", "posix")

    def no_links(src, dst):
        raise OSError(1, "EPERM " + SENTINEL)
    monkeypatch.setattr(build_mod, "_link", no_links)
    with pytest.raises(build_mod.BuildRefused, match="publication_unsupported"):
        build(tmp_path)
    report = json.loads((tmp_path / "shared-v1" / "INCOMPLETE.json").read_bytes())
    assert report["reason"] == "publication_unsupported" and not (tmp_path / "shared-v1" / "memory.json").exists()
    assert SENTINEL not in json.dumps(report)


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
    present = engine.ask("wake request from tools", "2026-10-31T13:00:00Z")
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


# ---- v4 (Lead bound request lead-mind-shared-index-fable-20261031-v4, 17:13:50Z; supplements 17:13:59Z, 17:15:50Z) ----


def test_v4_memory_policy_binds_the_exact_policy_and_roster_digests_and_the_descriptor_pins_them(tmp_path):
    out = build(tmp_path)
    assert out["build_receipt"]["policy"] == STRICT_POLICY
    memory = json.loads((tmp_path / "shared-v1" / "memory.json").read_bytes())
    assert memory["policy"] == STRICT_POLICY  # in memory.json, which the descriptor pins, not only in the receipt
    descriptor = reader.read_descriptor(tmp_path / "shared-v1" / "descriptor.json", out["descriptor_sha256"])
    assert descriptor["memory_sha256"] == out["memory_sha256"] == sha((tmp_path / "shared-v1" / "memory.json").read_bytes())
    # the Lead finding 17:15:50Z: a roster with one replaced uuid and a recomputed digest must not be the same memory
    other_roster = dict(ROSTER, **{"fable-5": FOREIGN_UUID})
    other_doc = dict(ROSTER_DOC, lanes=[dict(lane, agent_uuid=other_roster[lane["agent"]]) for lane in ROSTER_DOC["lanes"]])
    other_bytes, other_sha = pinned(other_doc)
    events = [dict(e, agent_uuid=other_roster[e["agent"]]) for e in EVENTS]
    other = build(tmp_path, name="other", proj=projection(events, roster=other_roster), roster=other_roster,
                  pins=dict(PINS, roster_bytes=other_bytes, roster_sha256=other_sha))
    assert other["build_receipt"]["policy"]["roster_sha256"] == other_sha != ROSTER_SHA
    assert other["memory_sha256"] != out["memory_sha256"] and other["descriptor_sha256"] != out["descriptor_sha256"]
    assert FOREIGN_UUID not in json.dumps(other) and "eeee5555" not in json.dumps(out)  # digests only, never the uuids
    for path in (tmp_path / "other").rglob("*"):
        if path.is_file():
            assert FOREIGN_UUID.encode() not in path.read_bytes()


def test_v4_pins_are_required_closed_and_digest_checked_with_nothing_written(tmp_path):
    with pytest.raises(build_mod.BuildRefused, match="pins_required"):
        build(tmp_path, pins=None)
    for bad in ("x", {}, [PINS], dict(PINS, extra=1), {k: v for k, v in PINS.items() if k != "roster_sha256"},
                dict(PINS, policy_bytes=POLICY_BYTES.decode()), dict(PINS, policy_sha256="zz"),
                dict(PINS, roster_sha256=ROSTER_SHA.upper()), dict(PINS, roster_bytes=bytearray(ROSTER_BYTES)),
                dict(PINS, policy_bytes=b"{" * (build_mod.POLICY_LIMIT + 1)),
                dict(PINS, roster_bytes=b"{" * (sp.ROSTER_BUDGET + 1))):
        with pytest.raises(build_mod.BuildRefused, match="pins_invalid"):
            build(tmp_path, pins=bad)
    for mismatch in (dict(PINS, policy_sha256="0" * 64), dict(PINS, roster_sha256=POLICY_SHA),
                     dict(PINS, roster_bytes=ROSTER_BYTES + b"\n"), dict(PINS, policy_bytes=POLICY_BYTES.replace(b"v1", b"v2"))):
        with pytest.raises(build_mod.BuildRefused, match="pin_mismatch"):
            build(tmp_path, pins=mismatch)
    assert list(tmp_path.iterdir()) == []


def test_v4_the_policy_document_is_closed_and_duplicate_keys_or_review_tasks_refuse(tmp_path):
    def with_policy(text):
        raw = text.encode("utf-8")
        return dict(PINS, policy_bytes=raw, policy_sha256=sha(raw))

    schema = "wd.q4-non-review-policy.v1"
    good = json.dumps(POLICY_DOC)
    for text in (json.dumps({"schema": "wd.q4-non-review-policy.v0", "approved_tasks": sorted(APPROVED)}),
                 json.dumps({"schema": schema, "approved_tasks": sorted(APPROVED), "memory_sha256": "0" * 64}),
                 json.dumps({"schema": schema}), json.dumps({"schema": schema, "approved_tasks": "not-a-list"}),
                 json.dumps({"schema": schema, "approved_tasks": [TASK, TASK]}),
                 json.dumps({"schema": schema, "approved_tasks": [TASK, 7]}),
                 json.dumps({"schema": schema, "approved_tasks": [TASK, "lead-rco-1-finding"]}),  # review-task pattern
                 json.dumps({"schema": schema, "approved_tasks": [TASK, "peer.review.1"]}),
                 json.dumps({"schema": schema, "approved_tasks": [TASK, "fable-5/mind-x"]}),  # not a safe task id
                 json.dumps({"schema": schema, "approved_tasks": [TASK] + ["t%d" % i for i in range(512)]}),  # 513 > reader 512
                 good.replace('"approved_tasks"', '"approved_tasks": [], "approved_tasks"', 1),  # duplicate key
                 good.replace("[", "[NaN, ", 1), "[]", "{", "null", good.encode("utf-16").decode("latin-1")):
        with pytest.raises(build_mod.BuildRefused, match="policy_invalid"):
            build(tmp_path, pins=with_policy(text))
    assert json.loads(good.replace('"approved_tasks"', '"approved_tasks": [], "approved_tasks"', 1)) == POLICY_DOC  # stdlib: last wins
    assert build_mod.MAX_APPROVED_TASKS == 512  # the reader (wd_q4_resolved_child.MAX_TASKS) refuses more; the builder must not accept more
    exactly_limit = json.dumps({"schema": schema, "approved_tasks": [TASK] + ["t%d" % i for i in range(511)]})
    assert build_mod.resolve_pins(with_policy(exactly_limit))["approved_tasks"] >= {TASK}  # 512 is still accepted
    with pytest.raises(build_mod.BuildRefused, match="pin_caller_mismatch"):
        build(tmp_path, approved_tasks=frozenset({TASK}))  # the caller's approved set must equal the pinned one
    with pytest.raises(build_mod.BuildRefused, match="approved_tasks_invalid"):
        build(tmp_path, approved_tasks={TASK})  # not a frozenset
    assert list(tmp_path.iterdir()) == []
    out = build(tmp_path, approved_tasks=None)  # the approved set is derived from the pinned policy
    assert out["build_receipt"]["policy"]["policy_sha256"] == POLICY_SHA and out["build_receipt"]["records"] == 4


def test_v4_the_roster_snapshot_is_acyclic_closed_and_must_name_all_five_lanes(tmp_path):
    def with_roster(document):
        raw = json.dumps(document).encode("utf-8")
        return dict(PINS, roster_bytes=raw, roster_sha256=sha(raw))

    fleet_like = dict(ROSTER_DOC, resolved_inputs={"memory_sha256": "0" * 64})  # a data-bearing fleet document = cycle
    deployment_lane = dict(ROSTER_DOC, lanes=[dict(ROSTER_DOC["lanes"][0], worktree="C:/x")] + ROSTER_DOC["lanes"][1:])
    deployment_tools = dict(ROSTER_DOC, tools_supervisor=dict(ROSTER_DOC["tools_supervisor"], task_name="x"))
    four = {k: v for k, v in ROSTER_DOC.items() if k != "tools_supervisor"}
    missing_lane = dict(ROSTER_DOC, lanes=ROSTER_DOC["lanes"][1:])
    twice = dict(ROSTER_DOC, lanes=ROSTER_DOC["lanes"] + [ROSTER_DOC["tools_supervisor"]])
    bad_uuid = dict(ROSTER_DOC, lanes=[dict(ROSTER_DOC["lanes"][0], agent_uuid="not-a-uuid")] + ROSTER_DOC["lanes"][1:])
    wrong_schema = dict(ROSTER_DOC, schema="wd-fleet.v1")
    no_schema = {k: v for k, v in ROSTER_DOC.items() if k != "schema"}
    for document in (fleet_like, deployment_lane, deployment_tools, four, missing_lane, twice, bad_uuid, wrong_schema,
                     no_schema, [], {"lanes": "x"}, dict(ROSTER_DOC, lanes="x"), dict(ROSTER_DOC, tools_supervisor="x")):
        with pytest.raises(build_mod.BuildRefused, match="roster_snapshot_invalid"):
            build(tmp_path, pins=with_roster(document))
    raw = json.dumps(ROSTER_DOC).replace('"lanes"', '"lanes": [], "lanes"', 1).encode("utf-8")  # duplicate key
    with pytest.raises(build_mod.BuildRefused, match="roster_snapshot_invalid"):
        build(tmp_path, pins=dict(PINS, roster_bytes=raw, roster_sha256=sha(raw)))
    with pytest.raises(build_mod.BuildRefused, match="pin_caller_mismatch"):
        build(tmp_path, roster=dict(ROSTER, **{"fable-5": FOREIGN_UUID}))  # caller roster differs from the snapshot
    with pytest.raises(build_mod.BuildRefused, match="roster_invalid"):
        build(tmp_path, roster="not a roster")
    assert list(tmp_path.iterdir()) == []
    assert build(tmp_path, roster=None)["build_receipt"]["policy"]["roster_sha256"] == ROSTER_SHA  # derived from pins


def test_v4_the_strict_publication_path_requires_a_verified_source_binding(tmp_path):
    for lax in (False, None, 1, "True"):
        with pytest.raises(build_mod.BuildRefused, match="source_binding_required"):
            build(tmp_path, allow_unbound_source=lax)  # the production default: no source, no publication
    for bad in ("x", {}, [], {"snapshot_result": {}, "expected_anchor_sha256": "zz"},
                {"snapshot_result": {}, "expected_anchor_sha256": "0" * 64, "extra": 1},
                {"snapshot_result": 5, "expected_anchor_sha256": "0" * 64}, {"snapshot_result": {}}):
        with pytest.raises(build_mod.BuildRefused, match="source_invalid"):
            build(tmp_path, source=bad, allow_unbound_source=False)
    assert list(tmp_path.iterdir()) == []
    out = build(tmp_path)  # synthetic route: explicitly allowed, recorded as unbound
    receipt = out["build_receipt"]
    assert receipt["source_binding"] is None and receipt["source_bound"] is False
    assert receipt["source_authenticated"] is False  # a caller-provided hash is never source authenticity
    assert build_mod.SOURCE_BINDING_SCHEMA == "wd.q4-shared-index-source-binding.v1"
    assert build_mod.SOURCE_BINDING_KEYS == {"schema", "anchor_sha256", "projection_sha256", "cursor_start_sha256",
                                             "cursor_end_sha256", "policy_sha256", "roster_sha256"}


def test_v4_an_unavailable_source_verifier_fails_closed_before_any_write(tmp_path, monkeypatch):
    source = {"snapshot_result": {"candidate_cursor": None, "source": {}}, "expected_anchor_sha256": "0" * 64}
    monkeypatch.setitem(sys.modules, build_mod.SNAPSHOT_MODULE, None)  # the import raises ImportError
    with pytest.raises(build_mod.BuildRefused, match="source_verifier_unavailable"):
        build(tmp_path, source=source, allow_unbound_source=False)
    with pytest.raises(build_mod.BuildRefused, match="source_verifier_unavailable"):
        build(tmp_path, source=source)  # a given source is verified even on the synthetic route

    class Half:  # a module without the verifier API
        _token = staticmethod(lambda cursor: "bc1.x")

    monkeypatch.setitem(sys.modules, build_mod.SNAPSHOT_MODULE, Half())
    with pytest.raises(build_mod.BuildRefused, match="source_verifier_unavailable"):
        build(tmp_path, source=source, allow_unbound_source=False)
    assert list(tmp_path.iterdir()) == []
    assert "verify" not in build_mod.__dict__ and not hasattr(build_mod, "verify_anchored_projection")  # not re-implemented


def _snapshot_module():
    """The Lead source-anchor verifier; absent on this branch -> the dependent test is SKIPPED (integration
    prerequisite, Lead 17:13:59Z: do not manufacture verification, do not modify Lead files)."""
    snapshot = pytest.importorskip(build_mod.SNAPSHOT_MODULE,
                                   reason="integration prerequisite: Lead shared_bridge_snapshot not on this branch")
    if not all(hasattr(snapshot, name) for name in ("verify_anchored_projection", "_ProjectionBinding", "_token",
                                                       "_canonical", "read_anchored_delta", "SnapshotRefused")):
        pytest.skip("integration prerequisite: the anchored snapshot chain is not committed in this tree")
    return snapshot


def _real_snapshot(snapshot, root, events, *, pins=PINS, approved=APPROVED, start=None):
    """A snapshot result from the REAL Lead chain over synthetic bridge-log BYTES written here (``read_bridge_log`` ->
    ``read_anchored_delta`` -> capture adapter -> projection): no mock and no binding built by hand (the Lead binding
    carries an in-process semantic commitment that only the real reader can produce). With ``start`` the events are
    APPENDED to the existing log and read from that cursor (the next delta of the same source). Returns
    ``(result, anchor_sha256, start_cursor)``. No real events, no real log."""
    from dataclasses import asdict
    from waggledance.core.bridge_log_reader import BridgeCursor, read_bridge_log
    root.mkdir(exist_ok=True)
    log, generation_path = root / "events.jsonl", root / "events.generation.json"
    payload = b"".join(json.dumps(row, sort_keys=True).encode("utf-8") + b"\n" for row in events)
    if start is None:
        log.write_bytes(payload)
        generation_path.write_text(json.dumps({"generation": "gen-synthetic"}), encoding="utf-8")
        probe = read_bridge_log(log, generation_path=generation_path, max_bytes=1 << 20, max_rows=100)
        assert probe.candidate_cursor is not None, probe.reason  # the reader's own identity of this synthetic file
        start = BridgeCursor(offset=0, file_identity=probe.candidate_cursor.file_identity, generation="gen-synthetic")
    else:
        with open(log, "ab") as handle:
            handle.write(payload)
    anchor_bytes = json.dumps({"schema": "wd.q4-source-anchor.v1", "source_path": str(log),
                               "generation_path": str(generation_path), "cursor_start": asdict(start),
                               "policy_sha256": pins["policy_sha256"], "roster_sha256": pins["roster_sha256"]},
                              sort_keys=True).encode("utf-8")
    anchor_sha256 = sha(anchor_bytes)
    result = snapshot.read_anchored_delta(log, start, approved, anchor_bytes=anchor_bytes, anchor_sha256=anchor_sha256,
                                          generation_path=generation_path, max_bytes=1 << 20, max_rows=100,
                                          policy_bytes=pins["policy_bytes"], policy_sha256=pins["policy_sha256"],
                                          roster_bytes=pins["roster_bytes"], roster_sha256=pins["roster_sha256"])
    return result, anchor_sha256, start


def test_v4_source_binding_is_written_only_after_the_lead_verifier_accepts(tmp_path):
    """Lead 17:33:37Z: a REAL snapshot feeds the builder, not a mock; the binding digests are the reader's own."""
    snapshot = _snapshot_module()
    result, anchor, start = _real_snapshot(snapshot, tmp_path / "log", EVENTS)
    out = build(tmp_path, proj=result["projection"], approved_tasks=None, roster=None,
                source={"snapshot_result": result, "expected_anchor_sha256": anchor}, allow_unbound_source=False)
    receipt = out["build_receipt"]
    assert receipt["source_binding"] == {
        "schema": "wd.q4-shared-index-source-binding.v1", "anchor_sha256": anchor,
        "projection_sha256": out["projection_sha256"], "cursor_start_sha256": sha(snapshot._token(start)),
        "cursor_end_sha256": sha(snapshot._token(result["candidate_cursor"])), "policy_sha256": POLICY_SHA,
        "roster_sha256": ROSTER_SHA}
    assert receipt["source_binding"]["cursor_start_sha256"] == result["source"]["cursor_start_sha256"]
    assert receipt["source_binding"]["cursor_end_sha256"] == result["source"]["cursor_end_sha256"]
    assert receipt["source_bound"] is True and receipt["source_authenticated"] is False
    assert receipt["records"] == 4 and receipt["delta_projection_sha256"] == out["projection_sha256"]
    serialized = json.dumps(out) + (tmp_path / "shared-v1" / "build_receipt.json").read_text("utf-8")
    assert start.file_identity not in serialized and "bc1." not in serialized  # identity and tokens are hashed only
    assert SENTINEL not in serialized and "_projection_binding" not in serialized


def test_v5_two_consecutive_real_lead_deltas_build_a_continuous_history_chain_without_any_mock(tmp_path):
    """The whole route with the real Lead chain and the real Lead merge helper: delta 1 -> generation 1; the log
    grows; delta 2 from generation 1's cursor end -> generation 2 with generation 1 as its pinned predecessor."""
    snapshot, history = _snapshot_module(), _history_module()
    first_result, first_anchor, start = _real_snapshot(snapshot, tmp_path / "log", EVENTS)
    first = build(tmp_path, proj=first_result["projection"], approved_tasks=None, roster=None,
                  source={"snapshot_result": first_result, "expected_anchor_sha256": first_anchor}, allow_unbound_source=False)
    second_result, second_anchor, resume = _real_snapshot(snapshot, tmp_path / "log", LATER_EVENTS,
                                                          start=first_result["candidate_cursor"])
    assert resume == first_result["candidate_cursor"] and second_result["projection"]["accepted"] == 3
    out = build(tmp_path, name="shared-v2", proj=second_result["projection"], approved_tasks=None, roster=None,
                source={"snapshot_result": second_result, "expected_anchor_sha256": second_anchor}, allow_unbound_source=False,
                predecessor=predecessor_of(tmp_path, "shared-v1", first))
    receipt = out["build_receipt"]
    delta_sha = sha(json.dumps(second_result["projection"], sort_keys=True, indent=1).encode("utf-8"))
    assert receipt["source_binding"]["projection_sha256"] == delta_sha == receipt["delta_projection_sha256"]
    assert receipt["source_binding"]["anchor_sha256"] == second_anchor
    assert receipt["source_binding"]["cursor_start_sha256"] == sha(snapshot._token(resume)) == receipt["history"]["predecessor"]["cursor_end_sha256"]
    assert receipt["source_binding"]["cursor_end_sha256"] == sha(snapshot._token(second_result["candidate_cursor"]))
    assert receipt["history"]["continuity_checked"] is True and receipt["history"]["predecessor"]["source_bound"] is True
    assert receipt["records"] == 6 == receipt["history"]["merged_records"] and receipt["history"]["overlap_records"] == 1
    merged = json.loads((tmp_path / "shared-v2" / "projection.json").read_bytes())
    assert record_ids(merged) == record_ids(first_result["projection"]) | record_ids(second_result["projection"])
    assert receipt["projection_sha256"] == sha((tmp_path / "shared-v2" / "projection.json").read_bytes()) != delta_sha
    # a third delta that does NOT start at generation 2's cursor end is refused as discontinuous (real cursors)
    third_result, third_anchor, _ = _real_snapshot(snapshot, tmp_path / "log", [event(ts_utc="2026-10-31T12:50:00Z")],
                                                   start=first_result["candidate_cursor"])
    with pytest.raises(build_mod.BuildRefused, match="predecessor_discontinuous"):
        build(tmp_path, name="shared-v3", proj=third_result["projection"], approved_tasks=None, roster=None,
              source={"snapshot_result": third_result, "expected_anchor_sha256": third_anchor}, allow_unbound_source=False,
              predecessor=predecessor_of(tmp_path, "shared-v2", out))
    serialized = json.dumps(out) + json.dumps(merged)
    assert "bc1." not in serialized and start.file_identity not in serialized and SENTINEL not in serialized
    assert sorted(p.name for p in tmp_path.iterdir()) == ["log", "shared-v1", "shared-v2"]


def test_v4_binding_takes_the_cursor_digests_from_the_verified_source_import_seam_double(tmp_path, monkeypatch):
    """Import-seam double, NOT verification: a stand-in module whose verifier accepts everything, so that THIS
    builder's own binding steps are observable here (digests come from ``source``, the end digest must be the digest
    of the candidate cursor, nothing raw is persisted). The real verifier tests above stay the acceptance proof."""
    import types
    from waggledance.core.bridge_log_reader import BridgeCursor

    def token(cursor):
        return "bc1." + json.dumps([cursor.offset, cursor.file_identity, cursor.generation])

    double = types.SimpleNamespace(verify_anchored_projection=lambda *a, **k: True, _token=token)
    monkeypatch.setitem(sys.modules, build_mod.SNAPSHOT_MODULE, double)
    start = BridgeCursor(offset=0, file_identity="synthetic-identity", generation="gen-synthetic")
    end = BridgeCursor(offset=77, file_identity="synthetic-identity", generation="gen-synthetic")
    source_meta = {"cursor_start_sha256": sha(token(start)), "cursor_end_sha256": sha(token(end)), "coverage": "x"}
    result = {"projection": projection(), "source": source_meta, "candidate_cursor": end}
    anchor = "5" * 64
    out = build(tmp_path, source={"snapshot_result": result, "expected_anchor_sha256": anchor}, allow_unbound_source=False)
    binding = out["build_receipt"]["source_binding"]
    assert binding["cursor_start_sha256"] == sha(token(start)) and binding["cursor_end_sha256"] == sha(token(end))
    assert binding["anchor_sha256"] == anchor and binding["projection_sha256"] == out["projection_sha256"]
    assert binding["policy_sha256"] == POLICY_SHA and binding["roster_sha256"] == ROSTER_SHA
    assert "bc1." not in json.dumps(out) and "synthetic-identity" not in json.dumps(out)
    # the end digest in source must be the digest of the candidate cursor, even when the verifier accepted
    for bad_meta in (dict(source_meta, cursor_end_sha256=sha(token(start))), dict(source_meta, cursor_end_sha256="z" * 64),
                     {k: v for k, v in source_meta.items() if k != "cursor_start_sha256"},
                     dict(source_meta, cursor_start="bc1.raw-token")):
        bad = {"projection": projection(), "source": bad_meta, "candidate_cursor": end}
        if "cursor_start" in bad_meta:
            bad["source"] = {k: v for k, v in bad_meta.items() if k != "cursor_start_sha256"}
        with pytest.raises(build_mod.BuildRefused, match="source_binding_refused"):
            build(tmp_path, name="bad", source={"snapshot_result": bad, "expected_anchor_sha256": anchor},
                  allow_unbound_source=False)
    with pytest.raises(build_mod.BuildRefused, match="source_binding_refused"):  # a verifier that does not say True
        monkeypatch.setitem(sys.modules, build_mod.SNAPSHOT_MODULE,
                            types.SimpleNamespace(verify_anchored_projection=lambda *a, **k: 1, _token=token))
        build(tmp_path, name="bad2", source={"snapshot_result": result, "expected_anchor_sha256": anchor},
              allow_unbound_source=False)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["shared-v1"]


@pytest.mark.parametrize("wrong", ["projection", "end", "anchor", "policy", "roster", "spliced_source"])
def test_v4_a_spliced_or_mismatched_source_binding_refuses_with_nothing_written(tmp_path, wrong):
    """Each case starts from a REAL Lead snapshot result and changes exactly one thing under the binding (the
    ``end_digest`` case, a source whose end digest is not the candidate's, cannot be produced by the real reader and
    is the builder's own check in the import-seam test above)."""
    snapshot = _snapshot_module()
    from waggledance.core.bridge_log_reader import BridgeCursor
    result, anchor, start = _real_snapshot(snapshot, tmp_path / "log", EVENTS)
    proj, expected_anchor, pins = result["projection"], anchor, PINS
    if wrong == "projection":
        proj = projection(list(EVENTS[:2]))  # a valid projection that is not the bound one
    elif wrong == "end":
        result["candidate_cursor"] = BridgeCursor(offset=999, file_identity=start.file_identity, generation=start.generation)
    elif wrong == "anchor":
        expected_anchor = "2" * 64
    elif wrong == "policy":  # a different (superset) pinned policy: the projection still validates, the digest differs
        other_bytes, other_sha = pinned(dict(POLICY_DOC, approved_tasks=sorted(APPROVED | {"other-task-2"})))
        pins = dict(PINS, policy_bytes=other_bytes, policy_sha256=other_sha)
    elif wrong == "roster":  # the same lanes and uuids with role fields: a different roster digest
        other_doc = dict(ROSTER_DOC, lanes=[dict(lane, role="synthetic") for lane in ROSTER_DOC["lanes"]])
        other_bytes, other_sha = pinned(other_doc)
        pins = dict(PINS, roster_bytes=other_bytes, roster_sha256=other_sha)
    elif wrong == "spliced_source":
        result["source"] = dict(result["source"], cursor_end_sha256=result["source"]["cursor_start_sha256"])  # changed under the binding
    with pytest.raises(build_mod.BuildRefused, match="source_binding_refused"):
        build(tmp_path, proj=proj, approved_tasks=None, roster=None, pins=pins,
              source={"snapshot_result": result, "expected_anchor_sha256": expected_anchor}, allow_unbound_source=False)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["log"]


# ---- v5 (Lead bound request lead-mind-shared-index-fable-20261031-v5, 17:55:13Z; supplement 17:56:10Z): bounded ----
# ---- prior metadata history: the NEXT complete frozen generation is built from a pinned predecessor + one delta ----

# The delta of the second generation: two new records and one record that is ALSO in EVENTS (same projected metadata
# in the same minute -> the same record id), so the overlap path is exercised by every history test.
LATER_EVENTS = [event(ts_utc="2026-10-31T12:30:00Z", status="done"),
                event(type="progress", status="info", agent="fable-5", ts_utc="2026-10-31T12:35:00Z"),
                event(type="done", status="blocked", agent="codex-tools-1", ts_utc="2026-10-31T12:20:00Z")]


def predecessor_of(root, name, out):
    """The caller's pinned predecessor record: the directory plus the three digests the previous build returned."""
    return {"memory_dir": str(root / name), "memory_sha256": out["memory_sha256"],
            "projection_sha256": out["projection_sha256"], "build_receipt_sha256": out["build_receipt_sha256"]}


def _history_module():
    """The pinned Lead merge helper (``shared_history.merge_projections`` at 9caa041b); absent on this branch -> the
    dependent test is SKIPPED (integration prerequisite, Lead 17:55:13Z: report skips, never manufacture the helper)."""
    history = pytest.importorskip(build_mod.HISTORY_MODULE,
                                  reason="integration prerequisite: Lead shared_history not on this branch")
    if not callable(getattr(history, "merge_projections", None)):
        pytest.skip("integration prerequisite: merge_projections not committed in this tree")
    return history


def record_ids(proj):
    return {record["record_id"] for record in proj["records"]}


def test_v5_a_first_generation_records_no_history_and_equal_merged_and_delta_digests(tmp_path):
    out = build(tmp_path)
    receipt = out["build_receipt"]
    assert receipt["history"] is None
    assert receipt["delta_projection_sha256"] == receipt["projection_sha256"] == out["projection_sha256"]
    memory = json.loads((tmp_path / "shared-v1" / "memory.json").read_bytes())
    assert memory["history"] is None and memory["projection_sha256"] == out["projection_sha256"]
    assert build_mod.HISTORY_SCHEMA == "wd.q4-shared-index-history.v1"
    assert build_mod.PREDECESSOR_KEYS == {"memory_dir", "memory_sha256", "projection_sha256", "build_receipt_sha256"}


def test_v5_a_predecessor_and_a_delta_build_one_generation_that_retains_prior_records(tmp_path):
    _history_module()
    first = build(tmp_path)
    before = {path: path.read_bytes() for path in (tmp_path / "shared-v1").rglob("*") if path.is_file()}
    delta = projection(LATER_EVENTS)
    delta_sha = sha(json.dumps(delta, sort_keys=True, indent=1).encode("utf-8"))
    out = build(tmp_path, name="shared-v2", proj=delta, predecessor=predecessor_of(tmp_path, "shared-v1", first))
    receipt = out["build_receipt"]
    merged_bytes = (tmp_path / "shared-v2" / "projection.json").read_bytes()
    merged = json.loads(merged_bytes)
    # the index contains the MERGED set; the receipt says explicitly which digest is merged and which is the delta
    assert record_ids(merged) == record_ids(projection()) | record_ids(delta) and merged["accepted"] == 6
    assert receipt["records"] == 6 and receipt["projection_sha256"] == out["projection_sha256"] == sha(merged_bytes)
    assert receipt["delta_projection_sha256"] == delta_sha != receipt["projection_sha256"]
    assert receipt["history"] == {
        "schema": "wd.q4-shared-index-history.v1", "helper": build_mod.HISTORY_MODULE,
        "predecessor": {"memory_sha256": first["memory_sha256"], "projection_sha256": first["projection_sha256"],
                        "build_receipt_sha256": first["build_receipt_sha256"], "records": 4, "source_bound": False,
                        "cursor_end_sha256": None},
        "delta_projection_sha256": delta_sha, "delta_records": 3, "delta_refused": {}, "overlap_records": 1,
        "merged_records": 6, "merged_projection_sha256": out["projection_sha256"], "continuity_checked": False,
        "continuity_route": "none", "watermark": None}
    memory = json.loads((tmp_path / "shared-v2" / "memory.json").read_bytes())
    assert memory["history"] == {"predecessor_memory_sha256": first["memory_sha256"],
                                 "predecessor_projection_sha256": first["projection_sha256"],
                                 "delta_projection_sha256": delta_sha}
    assert memory["records"] == 6 and memory["projection_sha256"] == out["projection_sha256"]
    # every cell receipt binds the MERGED projection digest, so a ref resolves against the persisted projection.json
    for path in (tmp_path / "shared-v2" / "cells").rglob("receipt.json"):
        assert json.loads(path.read_bytes())["content"]["projection_sha256"] == out["projection_sha256"]
    # the predecessor generation is untouched (history by retention, never by appending into an index)
    assert {path: path.read_bytes() for path in (tmp_path / "shared-v1").rglob("*") if path.is_file()} == before
    # the REAL Lead reader binds the new generation and a record that exists ONLY in the predecessor is found by its ref
    descriptor = reader.read_descriptor(tmp_path / "shared-v2" / "descriptor.json", out["descriptor_sha256"])
    opened = reader.bind_memory(tmp_path / "shared-v2", descriptor)
    engine = q4_mod.Q4(opened, FakeEncoder())
    projected = {record["provenance"]["event_ref"] for record in merged["records"]}
    present = engine.ask("wake request from tools", "2026-10-31T13:00:00Z")
    wanted = projection()["records"][1]["provenance"]["event_ref"]
    assert present["status"] == "ANSWER" and present["results"][0]["ref"] == wanted and wanted in projected
    later = engine.ask("progress info from fable", "2026-10-31T13:00:00Z")
    assert later["status"] == "ANSWER" and later["results"][0]["ref"] == delta["records"][1]["provenance"]["event_ref"]
    for answer in (present, later, engine.ask("thermal seasonal energy forecast", "2026-10-31T13:00:00Z")):
        assert all(result["ref"] in projected for result in answer["results"])
    everything = json.dumps(out) + merged_bytes.decode("utf-8") + json.dumps(memory)
    assert SENTINEL not in everything and "review_lane_excluded" not in everything and "pr_branch" not in everything


def test_v5_duplicates_across_generations_are_one_record_and_a_conflicting_id_refuses(tmp_path):
    _history_module()
    first = build(tmp_path)
    twice = projection([event(), event(message="other body", ts_utc="2026-10-31T12:00:59Z")] + LATER_EVENTS)
    assert twice["accepted"] == 5  # two in-minute duplicates of the first EVENTS record plus the three later ones
    out = build(tmp_path, name="shared-v2", proj=twice, predecessor=predecessor_of(tmp_path, "shared-v1", first))
    assert out["build_receipt"]["records"] == 6 and out["build_receipt"]["history"]["merged_records"] == 6
    assert out["build_receipt"]["history"]["overlap_records"] == 2 and out["build_receipt"]["history"]["delta_records"] == 5
    # a record id is the digest of its approved fields, so a delta that reuses a known id with other text is not a
    # valid projection at all: it refuses at validation, before the predecessor is read or the helper runs (the
    # helper's own same-id/different-content guard is defence in depth behind that)
    conflicting = projection(LATER_EVENTS)
    conflicting["records"][2]["stored_text"] = conflicting["records"][2]["summary"] = "forged text for a known id"
    with pytest.raises(build_mod.BuildRefused, match="projection_invalid"):
        build(tmp_path, name="shared-v3", proj=conflicting, predecessor=predecessor_of(tmp_path, "shared-v1", first))
    # and a merge result that is not exactly previous | delta refuses even when the helper returned it
    import types
    dropped = build_mod.build_memory.__globals__  # the module namespace, for the seam below
    real_merge = _history_module().merge_projections

    def lossy_merge(previous, delta, **pins):
        merged = real_merge(previous, delta, **pins)
        merged["records"] = merged["records"][1:]
        merged["accepted"] -= 1
        return merged
    with pytest.MonkeyPatch.context() as patch:
        patch.setitem(sys.modules, build_mod.HISTORY_MODULE, types.SimpleNamespace(merge_projections=lossy_merge))
        with pytest.raises(build_mod.BuildRefused, match="history_refused"):
            build(tmp_path, name="shared-v3", proj=projection(LATER_EVENTS), predecessor=predecessor_of(tmp_path, "shared-v1", first))
    assert dropped is build_mod.__dict__ and sorted(p.name for p in tmp_path.iterdir()) == ["shared-v1", "shared-v2"]


def test_v5_the_4096_cap_holds_for_the_merged_set_and_is_checked_before_the_helper_and_any_write(tmp_path):
    history = _history_module()
    assert build_mod.MAX_RECORDS == 4096 == history.MAX_RECORDS
    many = [event(ts_utc="2026-11-%02dT%02d:%02d:00Z" % (1 + n // 1440, (n // 60) % 24, n % 60)) for n in range(4095)]
    first = build(tmp_path, proj=projection(many))
    assert first["build_receipt"]["records"] == 4095
    one_more = projection([event(ts_utc="2026-11-05T00:00:00Z", status="done")])
    full = build(tmp_path, name="full", proj=one_more, predecessor=predecessor_of(tmp_path, "shared-v1", first))
    assert full["build_receipt"]["records"] == 4096 == full["build_receipt"]["history"]["merged_records"]
    over = projection([event(ts_utc="2026-11-05T00:01:00Z", status="done")])
    with pytest.raises(build_mod.BuildRefused, match="records_over_bound"):
        build(tmp_path, name="over", proj=over, predecessor=predecessor_of(tmp_path, "full", full))
    assert sorted(p.name for p in tmp_path.iterdir()) == ["full", "shared-v1"]


def test_v5_the_merged_bound_is_checked_before_the_helper_import_and_before_any_write(tmp_path, monkeypatch):
    first = build(tmp_path)  # four records
    monkeypatch.setattr(build_mod, "MAX_RECORDS", 5)
    monkeypatch.setitem(sys.modules, build_mod.HISTORY_MODULE, None)  # the helper import would raise ImportError
    with pytest.raises(build_mod.BuildRefused, match="records_over_bound"):  # 4 + 2 new (1 overlap) = 6 > 5
        build(tmp_path, name="shared-v2", proj=projection(LATER_EVENTS), predecessor=predecessor_of(tmp_path, "shared-v1", first))
    with pytest.raises(build_mod.BuildRefused, match="history_helper_unavailable"):  # 4 + 1 new = 5: bound passed first
        build(tmp_path, name="shared-v2", proj=projection(LATER_EVENTS[:1]), predecessor=predecessor_of(tmp_path, "shared-v1", first))
    assert sorted(p.name for p in tmp_path.iterdir()) == ["shared-v1"]


def test_v5_an_unavailable_history_helper_fails_closed_before_any_write(tmp_path, monkeypatch):
    first = build(tmp_path)
    predecessor = predecessor_of(tmp_path, "shared-v1", first)
    monkeypatch.setitem(sys.modules, build_mod.HISTORY_MODULE, None)
    with pytest.raises(build_mod.BuildRefused, match="history_helper_unavailable"):
        build(tmp_path, name="shared-v2", proj=projection(LATER_EVENTS), predecessor=predecessor)

    class Half:  # a module without the merge API
        MAX_RECORDS = 4096

    monkeypatch.setitem(sys.modules, build_mod.HISTORY_MODULE, Half())
    with pytest.raises(build_mod.BuildRefused, match="history_helper_unavailable"):
        build(tmp_path, name="shared-v2", proj=projection(LATER_EVENTS), predecessor=predecessor)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["shared-v1"]
    assert "merge_projections" not in build_mod.__dict__  # the merge is the pinned Lead helper, never re-implemented
    assert build_mod.HISTORY_MODULE == "sandbox.mind.step2.shared_history"


def test_v5_predecessor_shape_digest_and_policy_mismatches_refuse_before_any_write(tmp_path):
    first = build(tmp_path)
    good = predecessor_of(tmp_path, "shared-v1", first)
    delta = projection(LATER_EVENTS)
    for bad in ("x", {}, [good], dict(good, extra=1), {k: v for k, v in good.items() if k != "build_receipt_sha256"},
                dict(good, memory_dir=5), dict(good, memory_sha256="zz"), dict(good, projection_sha256=first["projection_sha256"].upper())):
        with pytest.raises(build_mod.BuildRefused, match="predecessor_invalid"):
            build(tmp_path, name="shared-v2", proj=delta, predecessor=bad)
    with pytest.raises(build_mod.BuildRefused, match="predecessor_unreadable"):
        build(tmp_path, name="shared-v2", proj=delta, predecessor=dict(good, memory_dir=str(tmp_path / "absent")))
    for key in ("memory_sha256", "projection_sha256", "build_receipt_sha256"):
        with pytest.raises(build_mod.BuildRefused, match="predecessor_mismatch"):
            build(tmp_path, name="shared-v2", proj=delta, predecessor=dict(good, **{key: "0" * 64}))
    # a byte-altered predecessor projection (copy of the generation, one record relabelled) fails its pinned digest
    import shutil
    shutil.copytree(tmp_path / "shared-v1", tmp_path / "copy")
    path = tmp_path / "copy" / "projection.json"
    altered = json.loads(path.read_bytes())
    altered["records"][0]["meta"]["status"] = "done"
    path.write_bytes(json.dumps(altered, sort_keys=True, indent=1).encode("utf-8"))
    with pytest.raises(build_mod.BuildRefused, match="predecessor_mismatch"):
        build(tmp_path, name="shared-v2", proj=delta, predecessor=dict(good, memory_dir=str(tmp_path / "copy")))
    # a predecessor built under OTHER pins is a different series: never carried silently into this one
    other_roster = dict(ROSTER, **{"fable-5": FOREIGN_UUID})
    other_doc = dict(ROSTER_DOC, lanes=[dict(lane, agent_uuid=other_roster[lane["agent"]]) for lane in ROSTER_DOC["lanes"]])
    other_bytes, other_sha = pinned(other_doc)
    events = [dict(e, agent_uuid=other_roster[e["agent"]]) for e in EVENTS]
    other = build(tmp_path, name="other", proj=projection(events, roster=other_roster), roster=other_roster,
                  pins=dict(PINS, roster_bytes=other_bytes, roster_sha256=other_sha))
    with pytest.raises(build_mod.BuildRefused, match="predecessor_policy_mismatch"):
        build(tmp_path, name="shared-v2", proj=delta, predecessor=predecessor_of(tmp_path, "other", other))
    # the predecessor must be a complete generation: an incomplete directory (no memory.json) is refused
    shutil.copytree(tmp_path / "shared-v1", tmp_path / "incomplete")
    (tmp_path / "incomplete" / "memory.json").unlink()
    with pytest.raises(build_mod.BuildRefused, match="predecessor_unreadable"):
        build(tmp_path, name="shared-v2", proj=delta, predecessor=dict(good, memory_dir=str(tmp_path / "incomplete")))
    assert sorted(p.name for p in tmp_path.iterdir()) == ["copy", "incomplete", "other", "shared-v1"]
    assert FOREIGN_UUID not in json.dumps(other)


def test_v5_an_empty_or_all_filtered_delta_never_builds_a_generation_even_with_a_predecessor(tmp_path):
    first = build(tmp_path)
    predecessor = predecessor_of(tmp_path, "shared-v1", first)
    for empty in (projection([]), projection([event(agent="claude-rco-1", ts_utc="2026-10-31T12:40:00Z")]),
                  projection([event(task_id="feature/pr-branch-task", ts_utc="2026-10-31T12:41:00Z")])):
        assert empty["accepted"] == 0
        with pytest.raises(build_mod.BuildRefused, match="projection_empty"):  # an empty delta is a cursor event, not a generation
            build(tmp_path, name="shared-v2", proj=empty, predecessor=predecessor)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["shared-v1"]


def _seam_double():
    """The import-seam stand-in for the Lead verifier (NOT verification, see the v4 seam test)."""
    import types

    def token(cursor):
        return "bc1." + json.dumps([cursor.offset, cursor.file_identity, cursor.generation])
    return types.SimpleNamespace(verify_anchored_projection=lambda *a, **k: True, _token=token), token


def _bound_source(token, proj, start, end, anchor="5" * 64):
    meta = {"cursor_start_sha256": sha(token(start)), "cursor_end_sha256": sha(token(end)), "coverage": "x"}
    return {"snapshot_result": {"projection": proj, "source": meta, "candidate_cursor": end}, "expected_anchor_sha256": anchor}


def test_v5_a_bound_delta_requires_a_bound_and_continuous_predecessor_import_seam_double(tmp_path, monkeypatch):
    from waggledance.core.bridge_log_reader import BridgeCursor
    double, token = _seam_double()
    monkeypatch.setitem(sys.modules, build_mod.SNAPSHOT_MODULE, double)
    c0, c1, c2, other = (BridgeCursor(offset=n, file_identity="synthetic-identity", generation="gen-synthetic") for n in (0, 77, 150, 9))
    bound_first = build(tmp_path, source=_bound_source(token, projection(), c0, c1), allow_unbound_source=False)
    unbound_first = build(tmp_path, name="unbound", proj=projection())
    delta = projection(LATER_EVENTS)
    # the delta's cursor start digest must equal the predecessor's recorded cursor end digest (an in-process part of
    # RCO2 H1; the checkpoint CAS against the current pointer remains the Lead's authority)
    with pytest.raises(build_mod.BuildRefused, match="predecessor_discontinuous"):
        build(tmp_path, name="shared-v2", proj=delta, source=_bound_source(token, delta, other, c2), allow_unbound_source=False,
              predecessor=predecessor_of(tmp_path, "shared-v1", bound_first))
    with pytest.raises(build_mod.BuildRefused, match="predecessor_unbound"):  # a bound delta never extends an unbound series
        build(tmp_path, name="shared-v2", proj=delta, source=_bound_source(token, delta, c1, c2), allow_unbound_source=False,
              predecessor=predecessor_of(tmp_path, "unbound", unbound_first))
    assert sorted(p.name for p in tmp_path.iterdir()) == ["shared-v1", "unbound"]
    assert "bc1." not in json.dumps(bound_first) and "synthetic-identity" not in json.dumps(bound_first)


def test_v5_a_continuous_bound_chain_records_the_delta_binding_and_the_predecessor_cursor_end(tmp_path, monkeypatch):
    _history_module()
    from waggledance.core.bridge_log_reader import BridgeCursor
    double, token = _seam_double()
    monkeypatch.setitem(sys.modules, build_mod.SNAPSHOT_MODULE, double)
    c0, c1, c2 = (BridgeCursor(offset=n, file_identity="synthetic-identity", generation="gen-synthetic") for n in (0, 77, 150))
    first = build(tmp_path, source=_bound_source(token, projection(), c0, c1), allow_unbound_source=False)
    delta = projection(LATER_EVENTS)
    delta_sha = sha(json.dumps(delta, sort_keys=True, indent=1).encode("utf-8"))
    out = build(tmp_path, name="shared-v2", proj=delta, source=_bound_source(token, delta, c1, c2), allow_unbound_source=False,
                predecessor=predecessor_of(tmp_path, "shared-v1", first))
    receipt = out["build_receipt"]
    # the source binding binds the DELTA (what the verifier saw), never the merged projection
    assert receipt["source_binding"]["projection_sha256"] == delta_sha == receipt["delta_projection_sha256"]
    assert receipt["source_binding"]["cursor_start_sha256"] == sha(token(c1)) == receipt["history"]["predecessor"]["cursor_end_sha256"]
    assert receipt["source_binding"]["cursor_end_sha256"] == sha(token(c2))
    assert receipt["history"]["predecessor"]["source_bound"] is True and receipt["history"]["continuity_checked"] is True
    assert receipt["history"]["continuity_route"] == "receipt_end" and receipt["history"]["watermark"] is None
    assert receipt["projection_sha256"] != delta_sha and receipt["records"] == 6
    assert "bc1." not in json.dumps(out) and "synthetic-identity" not in json.dumps(out)


# ---- v5 watermark route (Lead 18:19:12Z A -> empty -> B; agreed design 18:23:02Z; Lead API read_verified_pointer ----
# ---- at f5868baa): after a verified EMPTY advance the checkpoint watermark moves while the predecessor receipt ----
# ---- cursor_end stays, so the next delta starts at the watermark. The builder accepts that only through the ----
# ---- Lead's read-only verified-pointer function (same pins), never through a caller digest. ----


def _pointer_double(result=None, error=None, calls=None):
    """Import-seam stand-in for shared_index_checkpoint.read_verified_pointer (NOT verification): returns ``result``
    or raises ``error``; records every call so the builder's own arguments are observable."""
    import types

    def read_verified_pointer(root, approved_tasks, **kw):
        if calls is not None:
            calls.append((root, approved_tasks, kw))
        if error is not None:
            raise error
        return result
    return types.SimpleNamespace(read_verified_pointer=read_verified_pointer)


def _verified_pointer_of(first, *, pointer_sha256, anchor, cursor_end_sha256, watermark_sha256, generation="shared-v1"):
    return {"pointer_sha256": pointer_sha256, "generation": generation,
            "artifacts": {"descriptor_sha256": first["descriptor_sha256"], "memory_sha256": first["memory_sha256"],
                          "projection_sha256": first["projection_sha256"], "receipt_sha256": first["build_receipt_sha256"]},
            "anchor_sha256": anchor, "cursor_end_sha256": cursor_end_sha256, "watermark_sha256": watermark_sha256}


def test_v5w_watermark_shape_is_closed_and_needs_a_bound_delta(tmp_path, monkeypatch):
    assert build_mod.WATERMARK_KEYS == {"root", "pointer_sha256"}
    assert build_mod.CHECKPOINT_MODULE == "sandbox.mind.step2.shared_index_checkpoint"
    assert build_mod.PREDECESSOR_KEYS == {"memory_dir", "memory_sha256", "projection_sha256", "build_receipt_sha256"}
    first = build(tmp_path)
    good = predecessor_of(tmp_path, "shared-v1", first)
    delta = projection(LATER_EVENTS)
    monkeypatch.setitem(sys.modules, build_mod.CHECKPOINT_MODULE, _pointer_double(result={}))
    for bad in ("x", {}, [], {"root": str(tmp_path)}, {"root": str(tmp_path), "pointer_sha256": "zz"},
                {"root": 5, "pointer_sha256": "0" * 64}, {"root": str(tmp_path), "pointer_sha256": "0" * 64, "extra": 1}):
        with pytest.raises(build_mod.BuildRefused, match="watermark_invalid"):
            build(tmp_path, name="shared-v2", proj=delta, predecessor=dict(good, watermark=bad))
    with pytest.raises(build_mod.BuildRefused, match="watermark_invalid"):  # an unbound (synthetic) delta has no cursor to prove
        build(tmp_path, name="shared-v2", proj=delta, predecessor=dict(good, watermark={"root": str(tmp_path), "pointer_sha256": "0" * 64}))
    assert sorted(p.name for p in tmp_path.iterdir()) == ["shared-v1"]


def test_v5w_an_unavailable_pointer_verifier_fails_closed_before_any_write(tmp_path, monkeypatch):
    from waggledance.core.bridge_log_reader import BridgeCursor
    double, token = _seam_double()
    monkeypatch.setitem(sys.modules, build_mod.SNAPSHOT_MODULE, double)
    c0, c1, c2, c3 = (BridgeCursor(offset=n, file_identity="synthetic-identity", generation="gen-synthetic") for n in (0, 77, 150, 220))
    first = build(tmp_path, source=_bound_source(token, projection(), c0, c1), allow_unbound_source=False)
    delta = projection(LATER_EVENTS)
    watermark = {"root": str(tmp_path), "pointer_sha256": "7" * 64}
    monkeypatch.setitem(sys.modules, build_mod.CHECKPOINT_MODULE, None)
    with pytest.raises(build_mod.BuildRefused, match="watermark_verifier_unavailable"):
        build(tmp_path, name="shared-v2", proj=delta, source=_bound_source(token, delta, c2, c3), allow_unbound_source=False,
              predecessor=dict(predecessor_of(tmp_path, "shared-v1", first), watermark=watermark))

    class Half:  # a module without the function
        POINTER = "q4-latest.json"

    monkeypatch.setitem(sys.modules, build_mod.CHECKPOINT_MODULE, Half())
    with pytest.raises(build_mod.BuildRefused, match="watermark_verifier_unavailable"):
        build(tmp_path, name="shared-v2", proj=delta, source=_bound_source(token, delta, c2, c3), allow_unbound_source=False,
              predecessor=dict(predecessor_of(tmp_path, "shared-v1", first), watermark=watermark))
    assert sorted(p.name for p in tmp_path.iterdir()) == ["shared-v1"]
    assert "read_verified_pointer" not in build_mod.__dict__ and "_validated_previous" not in build_mod.__dict__


def test_v5w_the_verified_watermark_route_import_seam_double(tmp_path, monkeypatch):
    """Import-seam double for BOTH Lead functions (NOT verification): makes the builder's own steps observable: the
    pointer function is called with the resolved pins and the caller's pointer digest; the returned generation must be
    the predecessor (all four artifact digests, anchor, cursor end, name); the watermark must equal the delta start."""
    _history_module()
    from waggledance.core.bridge_log_reader import BridgeCursor
    double, token = _seam_double()
    monkeypatch.setitem(sys.modules, build_mod.SNAPSHOT_MODULE, double)
    c0, c1, c2, c3 = (BridgeCursor(offset=n, file_identity="synthetic-identity", generation="gen-synthetic") for n in (0, 77, 150, 220))
    anchor = "5" * 64
    first = build(tmp_path, source=_bound_source(token, projection(), c0, c1, anchor), allow_unbound_source=False)
    delta = projection(LATER_EVENTS)
    pointer_sha = "7" * 64
    verified = _verified_pointer_of(first, pointer_sha256=pointer_sha, anchor=anchor,
                                    cursor_end_sha256=sha(token(c1)), watermark_sha256=sha(token(c2)))
    calls = []
    monkeypatch.setitem(sys.modules, build_mod.CHECKPOINT_MODULE, _pointer_double(result=verified, calls=calls))
    watermark = {"root": str(tmp_path), "pointer_sha256": pointer_sha}
    out = build(tmp_path, name="shared-v2", proj=delta, source=_bound_source(token, delta, c2, c3, anchor), allow_unbound_source=False,
                predecessor=dict(predecessor_of(tmp_path, "shared-v1", first), watermark=watermark))
    receipt = out["build_receipt"]
    assert receipt["history"]["continuity_route"] == "verified_watermark" and receipt["history"]["continuity_checked"] is True
    assert receipt["history"]["watermark"] == {"pointer_sha256": pointer_sha, "watermark_sha256": sha(token(c2)),
                                               "generation": "shared-v1"}
    assert receipt["history"]["predecessor"]["cursor_end_sha256"] == sha(token(c1))  # the receipt end stays A's end
    assert receipt["source_binding"]["cursor_start_sha256"] == sha(token(c2))  # B starts at the verified watermark
    assert len(calls) == 1
    root, tasks, kw = calls[0]
    assert str(root) == str(tmp_path) and tasks == APPROVED
    assert kw == {"expected_pointer_sha256": pointer_sha, "policy_bytes": POLICY_BYTES, "policy_sha256": POLICY_SHA,
                  "roster_bytes": ROSTER_BYTES, "roster_sha256": ROSTER_SHA}
    assert "bc1." not in json.dumps(out) and "synthetic-identity" not in json.dumps(out)
    # every deviation of the verified pointer from the predecessor refuses before any write
    wrong_cases = {
        "memory": dict(verified, artifacts=dict(verified["artifacts"], memory_sha256="0" * 64)),
        "projection": dict(verified, artifacts=dict(verified["artifacts"], projection_sha256="0" * 64)),
        "receipt": dict(verified, artifacts=dict(verified["artifacts"], receipt_sha256="0" * 64)),
        "descriptor": dict(verified, artifacts=dict(verified["artifacts"], descriptor_sha256="0" * 64)),
        "anchor": dict(verified, anchor_sha256="6" * 64),
        "cursor_end": dict(verified, cursor_end_sha256=sha(token(c2))),
        "generation": dict(verified, generation="other"),
        "pointer": dict(verified, pointer_sha256="8" * 64),
    }
    for name, wrong in wrong_cases.items():
        monkeypatch.setitem(sys.modules, build_mod.CHECKPOINT_MODULE, _pointer_double(result=wrong))
        with pytest.raises(build_mod.BuildRefused, match="watermark_generation_mismatch"):
            build(tmp_path, name="shared-v3", proj=delta, source=_bound_source(token, delta, c2, c3, anchor), allow_unbound_source=False,
                  predecessor=dict(predecessor_of(tmp_path, "shared-v1", first), watermark=watermark))
    # a verified watermark that is not where the delta starts is a discontinuity, not an override
    monkeypatch.setitem(sys.modules, build_mod.CHECKPOINT_MODULE, _pointer_double(result=dict(verified, watermark_sha256=sha(token(c3)))))
    with pytest.raises(build_mod.BuildRefused, match="predecessor_discontinuous"):
        build(tmp_path, name="shared-v3", proj=delta, source=_bound_source(token, delta, c2, c3, anchor), allow_unbound_source=False,
              predecessor=dict(predecessor_of(tmp_path, "shared-v1", first), watermark=watermark))
    # the Lead function refusing, or returning anything but the closed record, is one fixed token
    for broken in (_pointer_double(error=ValueError("checkpoint_refused")), _pointer_double(result=None),
                   _pointer_double(result={k: v for k, v in verified.items() if k != "watermark_sha256"}),
                   _pointer_double(result=dict(verified, extra=1))):
        monkeypatch.setitem(sys.modules, build_mod.CHECKPOINT_MODULE, broken)
        with pytest.raises(build_mod.BuildRefused, match="watermark_refused"):
            build(tmp_path, name="shared-v3", proj=delta, source=_bound_source(token, delta, c2, c3, anchor), allow_unbound_source=False,
                  predecessor=dict(predecessor_of(tmp_path, "shared-v1", first), watermark=watermark))
    assert sorted(p.name for p in tmp_path.iterdir()) == ["shared-v1", "shared-v2"]


def _checkpoint_module():
    checkpoint = pytest.importorskip(build_mod.CHECKPOINT_MODULE,
                                     reason="integration prerequisite: Lead shared_index_checkpoint not on this branch")
    if not all(callable(getattr(checkpoint, name, None)) for name in ("read_verified_pointer", "commit_generation", "advance_empty_delta")):
        pytest.skip("integration prerequisite: the verified-pointer checkpoint API is not committed in this tree")
    return checkpoint


def test_v5w_real_chain_a_then_verified_empty_advance_then_b_through_the_lead_checkpoint(tmp_path):
    """The exact Lead case with the REAL Lead chain and REAL checkpoint: build A from a real delta and commit it as the
    pointer; the log grows by review-lane rows only (a strict EMPTY delta) and advance_empty_delta moves the watermark;
    B is read from the watermark and built with A as predecessor + the verified watermark. No mock anywhere."""
    snapshot, history, checkpoint = _snapshot_module(), _history_module(), _checkpoint_module()
    root = tmp_path / "mem"
    root.mkdir()
    a_result, a_anchor, start = _real_snapshot(snapshot, tmp_path / "log", EVENTS)
    first = build(root, name="gen-a", proj=a_result["projection"], approved_tasks=None, roster=None,
                  source={"snapshot_result": a_result, "expected_anchor_sha256": a_anchor}, allow_unbound_source=False)
    artifacts = {"descriptor_sha256": first["descriptor_sha256"], "memory_sha256": first["memory_sha256"],
                 "projection_sha256": first["projection_sha256"], "receipt_sha256": first["build_receipt_sha256"]}
    checkpoint.commit_generation(root, "gen-a", start, a_result["candidate_cursor"], APPROVED, expected_pointer_sha256=None,
                                 expected_artifacts=artifacts, initial_cursor=start, policy_bytes=POLICY_BYTES,
                                 expected_policy_sha256=POLICY_SHA, roster_bytes=ROSTER_BYTES, expected_roster_sha256=ROSTER_SHA,
                                 expected_anchor_sha256=a_anchor)
    p0 = sha((root / "q4-latest.json").read_bytes())
    # a strict EMPTY delta: only review-lane rows arrive (policy exclusion, no record, no count), the cursor still moves
    empty_result, empty_anchor, _ = _real_snapshot(snapshot, tmp_path / "log",
                                                   [event(agent="claude-rco-1", ts_utc="2026-10-31T12:25:00Z")],
                                                   start=a_result["candidate_cursor"])
    assert empty_result["projection"]["accepted"] == 0 and empty_result["projection"]["records"] == []
    advanced = checkpoint.advance_empty_delta(root, empty_result, APPROVED, expected_pointer_sha256=p0, policy_bytes=POLICY_BYTES,
                                              expected_policy_sha256=POLICY_SHA, roster_bytes=ROSTER_BYTES,
                                              expected_roster_sha256=ROSTER_SHA, expected_anchor_sha256=empty_anchor)
    assert advanced["status"] == "advanced"
    p1 = advanced["pointer_sha256"]
    assert p1 == sha((root / "q4-latest.json").read_bytes()) != p0
    b_result, b_anchor, resume = _real_snapshot(snapshot, tmp_path / "log", LATER_EVENTS, start=empty_result["candidate_cursor"])
    predecessor = predecessor_of(root, "gen-a", first)
    with pytest.raises(build_mod.BuildRefused, match="predecessor_discontinuous"):  # without the proof: fail closed
        build(root, name="gen-b", proj=b_result["projection"], approved_tasks=None, roster=None,
              source={"snapshot_result": b_result, "expected_anchor_sha256": b_anchor}, allow_unbound_source=False,
              predecessor=predecessor)
    with pytest.raises(build_mod.BuildRefused, match="watermark_refused"):  # the stale pre-advance pointer digest
        build(root, name="gen-b", proj=b_result["projection"], approved_tasks=None, roster=None,
              source={"snapshot_result": b_result, "expected_anchor_sha256": b_anchor}, allow_unbound_source=False,
              predecessor=dict(predecessor, watermark={"root": str(root), "pointer_sha256": p0}))
    out = build(root, name="gen-b", proj=b_result["projection"], approved_tasks=None, roster=None,
                source={"snapshot_result": b_result, "expected_anchor_sha256": b_anchor}, allow_unbound_source=False,
                predecessor=dict(predecessor, watermark={"root": str(root), "pointer_sha256": p1}))
    receipt = out["build_receipt"]
    assert receipt["history"]["continuity_route"] == "verified_watermark"
    assert receipt["history"]["watermark"] == {"pointer_sha256": p1, "generation": "gen-a",
                                               "watermark_sha256": sha(snapshot._token(empty_result["candidate_cursor"]))}
    assert receipt["source_binding"]["cursor_start_sha256"] == sha(snapshot._token(resume)) == receipt["history"]["watermark"]["watermark_sha256"]
    assert receipt["history"]["predecessor"]["cursor_end_sha256"] == sha(snapshot._token(a_result["candidate_cursor"]))
    assert receipt["records"] == 6 and receipt["history"]["overlap_records"] == 1
    assert sorted(p.name for p in root.iterdir() if p.is_dir()) == ["gen-a", "gen-b"]
    serialized = json.dumps(out)
    assert "bc1." not in serialized and start.file_identity not in serialized and SENTINEL not in serialized

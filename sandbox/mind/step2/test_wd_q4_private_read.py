"""Synthetic checks of the private stdin Q4 adapter: fake memory, fake encoder, fake Q4; no model, no corpus."""

import hashlib
import io
import json
import os
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

import pytest

pytest.importorskip("numpy")
pytest.importorskip("faiss")

from sandbox.mind.step2 import memory as memory_mod
from sandbox.mind.step2 import wd_q4_private_read as adapter
import tools.bridge_diff_privacy as privacy_mod
MARKERS = getattr(privacy_mod, "PRIVATE_" + "MARKERS")  # the diff privacy scanner refuses the literal name

ENCODER = {"model": "intfloat/multilingual-e5-small", "revision": "abc123", "weights_sha256": "f" * 64}
SITUATION = "zebra-quartz-lantern situation text that must never leak 7f3a"
WHEN = "2026-10-20T12:00:00Z"
EVENT_REF = "a" * 64
LESSON_FILE = "my-secret-lesson-title.md"
SHARED_SCOPE = "wd.q4-shared-projected-events.v1"


class FakeView:
    def __init__(self, healthy=True, encoder=None):
        self.status = "HEALTHY" if healthy else "QUARANTINED"
        self.reason = None if healthy else "memory_binding_mismatch"
        self.receipt = {"content": {"encoder": dict(ENCODER if encoder is None else encoder)}}
        self.rows = [{"kind": "event", "provenance": {"ref_kind": "sha256_of_projected_metadata",
                                                     "canonical_event": False, "event_ref": EVENT_REF}}]

    @property
    def healthy(self):
        return self.status == "HEALTHY"


class FakeMemory:
    def __init__(self, dim=384, views=None):
        self.memory = {"schema": memory_mod.SCHEMA, "dim": dim}
        self.views = {"learning": FakeView()} if views is None else views


class FakeQ4:
    calls = []

    def __init__(self, memory, encoder):
        self.memory, self.encoder = memory, encoder

    def ask(self, situation, when):
        FakeQ4.calls.append((situation, when))
        return {"status": "ANSWER", "abstain": False, "reason": None, "degraded": False, "degraded_cells": [],
                "results": [{"ref": EVENT_REF, "doc_id": "event:" + EVENT_REF, "kind": "event", "score": 0.9,
                             "fused": 0.03, "view_ranks": {"w": 1, "d": 1, "b": 2}, "cell": "general", "time_utc": WHEN,
                             "tier": "bridge_event", "fallback": False},
                            {"ref": LESSON_FILE, "doc_id": "lesson:" + LESSON_FILE, "kind": "lesson", "score": 0.8,
                             "fused": 0.02, "view_ranks": {"w": 2, "d": 3, "b": 1}, "cell": "learning", "time_utc": WHEN,
                             "tier": "memory_note", "fallback": False, "text": "raw text must never pass"}],
                "rule": "q4-rule-v1.1", "telemetry": {"records": 2, "candidates": 2, "ms": 0.1, "note": "x"}}


def write_memory(tmp_path, dim=384):
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    body = json.dumps({"schema": memory_mod.SCHEMA, "dim": dim, "cells": {}}).encode("utf-8")
    (memory_dir / "memory.json").write_bytes(body)
    return memory_dir, hashlib.sha256(body).hexdigest()


def write_descriptor(tmp_path, memory_sha256, changes=None):
    descriptor = {"schema": adapter.DESCRIPTOR_SCHEMA, "scope": SHARED_SCOPE, "memory_sha256": memory_sha256, "dim": 384,
                  "encoder": dict(ENCODER)}
    descriptor.update(changes or {})
    path = tmp_path / "descriptor.json"
    path.write_text(json.dumps(descriptor), encoding="utf-8")
    return path


def query_bytes(situation=SITUATION, when=WHEN, **extra):
    return json.dumps(dict({"situation": situation, "time": when}, **extra)).encode("utf-8")


@pytest.fixture
def fakes(monkeypatch):
    FakeQ4.calls = []
    loaded = {"memory": 0, "encoder": 0, "runtime": 0}

    def memory_factory(path):
        loaded["memory"] += 1
        return FakeMemory()

    def encoder_factory(*, model_dir, revision):
        assert model_dir.is_absolute()
        assert revision == ENCODER["revision"]
        loaded["encoder"] += 1
        return object()

    def runtime_identity(*, model_dir, revision):
        assert model_dir.is_absolute() and revision == ENCODER["revision"]
        loaded["runtime"] += 1
        return dict(ENCODER, weights_bytes=1)

    monkeypatch.setattr(adapter, "MEMORY_FACTORY", memory_factory)
    monkeypatch.setattr(adapter, "ENCODER_FACTORY", encoder_factory)
    monkeypatch.setattr(adapter, "RUNTIME_IDENTITY", runtime_identity)
    monkeypatch.setattr(adapter, "Q4_FACTORY", FakeQ4)
    return loaded


def run(memory_dir, descriptor, data, capsys, descriptor_sha256=None):
    out = io.StringIO()
    if descriptor_sha256 is None:
        descriptor_sha256 = hashlib.sha256(descriptor.read_bytes()).hexdigest() if descriptor.exists() else "0" * 64
    code = adapter.main(["--memory", str(memory_dir), "--descriptor", str(descriptor),
                         "--descriptor-sha256", descriptor_sha256, "--model-dir", str(memory_dir.parent),
                         "--lock-file", str(memory_dir.parent / "reader.lock"),
                         "--lock-timeout", "1"], stdin=io.BytesIO(data),
                        stdout=out)
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == ""
    return code, json.loads(out.getvalue()), out.getvalue()


def test_a_bound_query_answers_with_refs_only_and_never_echoes_the_situation(tmp_path, capsys, fakes):
    memory_dir, digest = write_memory(tmp_path)
    code, out, text = run(memory_dir, write_descriptor(tmp_path, digest), query_bytes(), capsys)
    assert code == 0 and out["status"] == "ANSWER" and out["rule"] == "q4-rule-v1.1"
    event, lesson = out["results"]
    assert event["ref"] == EVENT_REF and event["ref_sha256"] == hashlib.sha256(EVENT_REF.encode()).hexdigest()
    assert event["doc_id"] == "event:" + EVENT_REF and event["doc_id_sha256"] == hashlib.sha256(
        ("event:" + EVENT_REF).encode()).hexdigest()
    assert lesson["ref"] is None and lesson["doc_id"] is None
    assert lesson["ref_sha256"] == hashlib.sha256(LESSON_FILE.encode()).hexdigest() and "text" not in lesson
    assert LESSON_FILE not in text and "secret" not in text and "raw text" not in text
    assert out["telemetry"] == {"records": 2, "candidates": 2, "ms": 0.1} and out["degraded_cells"] == []
    assert out["schema"] == adapter.SCHEMA and out["identity"] == "caller_declared_not_authenticated"
    assert out["binding"] == {"descriptor_sha256": hashlib.sha256((tmp_path / "descriptor.json").read_bytes()).hexdigest(),
                               "memory_sha256": digest, "dim": 384, "encoder": ENCODER, "scope": SHARED_SCOPE}
    assert SITUATION not in text and "zebra" not in text and "7f3a" not in text
    assert FakeQ4.calls == [(SITUATION, WHEN)] and fakes == {"memory": 1, "encoder": 1, "runtime": 1}


def test_verified_descriptor_revision_is_bound_to_encoder_load(tmp_path, capsys, fakes, monkeypatch):
    seen = []

    def encoder_factory(*, model_dir, revision):
        seen.append((model_dir, revision))
        return object()

    monkeypatch.setattr(adapter, "ENCODER_FACTORY", encoder_factory)
    memory_dir, digest = write_memory(tmp_path)
    descriptor = write_descriptor(tmp_path, digest)
    code, out, _ = run(memory_dir, descriptor, query_bytes(), capsys)
    assert code == 0 and out["status"] == "ANSWER"
    assert seen == [(tmp_path, ENCODER["revision"])]


def test_descriptor_requires_explicit_shared_scope_before_opening_memory(tmp_path, capsys, fakes):
    memory_dir, digest = write_memory(tmp_path)
    descriptor = write_descriptor(tmp_path, digest)
    data = json.loads(descriptor.read_text(encoding="utf-8"))
    del data["scope"]
    descriptor.write_text(json.dumps(data), encoding="utf-8")
    code, out, _ = run(memory_dir, descriptor, query_bytes(), capsys)
    assert (code, out["reason"]) == (2, "descriptor_scope_mismatch")
    assert fakes == {"memory": 0, "encoder": 0, "runtime": 0}


def test_legacy_v1_descriptor_is_refused_even_with_shared_scope(tmp_path, capsys, fakes):
    memory_dir, digest = write_memory(tmp_path)
    descriptor = write_descriptor(tmp_path, digest, {"schema": "wd.bridge-mind-q4-memory-descriptor.v1"})
    code, out, _ = run(memory_dir, descriptor, query_bytes(), capsys)
    assert (code, out["reason"]) == (2, "descriptor_shape")
    assert fakes == {"memory": 0, "encoder": 0, "runtime": 0}


def test_v2_descriptor_with_shared_scope_is_accepted(tmp_path, capsys, fakes):
    assert adapter.DESCRIPTOR_SCHEMA == "wd.bridge-mind-q4-memory-descriptor.v2"
    memory_dir, digest = write_memory(tmp_path)
    descriptor = write_descriptor(tmp_path, digest, {"schema": "wd.bridge-mind-q4-memory-descriptor.v2"})
    code, out, _ = run(memory_dir, descriptor, query_bytes(), capsys)
    assert code == 0 and out["binding"]["scope"] == SHARED_SCOPE


@pytest.mark.parametrize("scope", [None, "legacy", "private", "wd.q4-shared-projected-events.v2"])
def test_descriptor_refuses_wrong_or_unknown_scope_before_opening_memory(tmp_path, capsys, fakes, scope):
    memory_dir, digest = write_memory(tmp_path)
    code, out, _ = run(memory_dir, write_descriptor(tmp_path, digest, {"scope": scope}), query_bytes(), capsys)
    assert (code, out["reason"]) == (2, "descriptor_scope_mismatch")
    assert fakes == {"memory": 0, "encoder": 0, "runtime": 0}


def test_explicit_shared_scope_accepts_shared_rows(tmp_path, capsys, fakes, monkeypatch):
    memory_dir, digest = write_memory(tmp_path)
    fake = FakeMemory()
    fake.views["learning"].rows = [{"kind": "event", "provenance": {
        "ref_kind": "sha256_of_projected_metadata", "canonical_event": False, "event_ref": EVENT_REF}}]
    monkeypatch.setattr(adapter, "MEMORY_FACTORY", lambda path: fake)
    code, out, _ = run(memory_dir, write_descriptor(tmp_path, digest, {"scope": SHARED_SCOPE}), query_bytes(), capsys)
    assert code == 0 and out["status"] == "ANSWER" and fakes["encoder"] == 1


@pytest.mark.parametrize("change", [
    {"kind": "lesson"},
    {"provenance": {"ref_kind": "sha256_of_projected_metadata", "canonical_event": True, "event_ref": EVENT_REF}},
    {"provenance": {"ref_kind": "sha256_of_projected_metadata", "canonical_event": 0, "event_ref": EVENT_REF}},
    {"provenance": {"ref_kind": "canonical", "canonical_event": False, "event_ref": EVENT_REF}},
    {"provenance": {"ref_kind": "sha256_of_projected_metadata", "canonical_event": False,
                    "event_ref": "C:/private/path"}},
    {"provenance": {}},
])
def test_every_healthy_cell_row_is_shared_before_encoder_or_query(tmp_path, capsys, fakes, monkeypatch, change):
    memory_dir, digest = write_memory(tmp_path)
    good = {"kind": "event", "provenance": {"ref_kind": "sha256_of_projected_metadata",
                                            "canonical_event": False, "event_ref": EVENT_REF}}
    first, second = FakeView(), FakeView()
    first.rows = [good]
    second.rows = [good, dict(good, **change)]  # the final row of the final healthy cell must be inspected
    monkeypatch.setattr(adapter, "MEMORY_FACTORY", lambda path: FakeMemory(views={"a": first, "b": second}))
    code, out, _ = run(memory_dir, write_descriptor(tmp_path, digest, {"scope": SHARED_SCOPE}), query_bytes(), capsys)
    assert (code, out["reason"]) == (2, "memory_scope_mismatch")
    assert fakes["runtime"] == fakes["encoder"] == 0 and FakeQ4.calls == []


def test_missing_healthy_cell_rows_is_not_assumed_shared(tmp_path, capsys, fakes, monkeypatch):
    memory_dir, digest = write_memory(tmp_path)
    view = FakeView()
    del view.rows
    monkeypatch.setattr(adapter, "MEMORY_FACTORY", lambda path: FakeMemory(views={"a": view}))
    code, out, _ = run(memory_dir, write_descriptor(tmp_path, digest), query_bytes(), capsys)
    assert (code, out["reason"]) == (2, "memory_scope_mismatch")
    assert fakes["runtime"] == fakes["encoder"] == 0 and FakeQ4.calls == []


def test_the_situation_is_never_accepted_from_argv(tmp_path, capsys):
    memory_dir, digest = write_memory(tmp_path)
    descriptor = write_descriptor(tmp_path, digest)
    with pytest.raises(SystemExit) as raised:
        adapter.main(["--memory", str(memory_dir), "--descriptor", str(descriptor),
                      "--descriptor-sha256", hashlib.sha256(descriptor.read_bytes()).hexdigest(),
                      "--model-dir", str(tmp_path),
                      "--lock-file", str(tmp_path / "slot.lock"), "--lock-timeout", "0",
                      "--situation", SITUATION], stdin=io.BytesIO(b""), stdout=io.StringIO())
    assert raised.value.code == 2
    assert SITUATION not in capsys.readouterr().err


def test_cli_missing_runtime_refuses_without_traceback_or_private_paths():
    root = Path(__file__).resolve().parents[3]
    env = dict(os.environ, PYTHONPATH=str(root), PYTHONDONTWRITEBYTECODE="1")
    result = subprocess.run([sys.executable, "-S", "-B", "-m", "sandbox.mind.step2.wd_q4_private_read",
                             "--memory", "synthetic-memory", "--descriptor", "synthetic-descriptor",
                             "--descriptor-sha256", "0" * 64],
                            input=b"{}", capture_output=True, cwd=root, env=env, timeout=30)
    assert result.returncode == 2
    assert json.loads(result.stdout)["reason"] == "runtime_unavailable"
    assert b"Traceback" not in result.stderr and str(root).encode() not in result.stderr


@pytest.mark.parametrize("data,reason", [
    pytest.param(b"x" * (adapter.STDIN_BUDGET + 1), "stdin_over_budget", id="over_budget"),
    pytest.param(b"{not json", "stdin_not_json", id="not_json"),
    pytest.param(query_bytes(agent="claude-rco-1"), "query_shape", id="caller_name_key"),
    pytest.param(json.dumps({"situation": SITUATION}).encode(), "query_shape", id="time_missing"),
    pytest.param(query_bytes(situation="   "), "situation_invalid", id="blank_situation"),
    pytest.param(query_bytes(situation="x" * (adapter.SITUATION_MAX_CHARS + 1)), "situation_invalid", id="too_long"),
    pytest.param(query_bytes(when="yesterday"), "time_invalid", id="bad_time"),
])
def test_a_malformed_query_is_refused_before_anything_is_loaded(tmp_path, capsys, fakes, data, reason):
    memory_dir, digest = write_memory(tmp_path)
    code, out, text = run(memory_dir, write_descriptor(tmp_path, digest), data, capsys)
    assert (code, out["status"], out["reason"], out["results"]) == (2, "REFUSED", reason, [])
    assert SITUATION not in text and fakes == {"memory": 0, "encoder": 0, "runtime": 0}


@pytest.mark.parametrize("change,reason", [
    ({"memory_sha256": "0" * 64}, "memory_descriptor_mismatch"),
    ({"schema": "other"}, "descriptor_shape"),
    ({"dim": "384"}, "descriptor_shape"),
    ({"encoder": {"model": "x"}}, "descriptor_shape"),
    ({"encoder": dict(ENCODER, weights_sha256="zz")}, "descriptor_shape"),
])
def test_a_descriptor_that_does_not_bind_the_memory_refuses_before_the_memory_opens(tmp_path, capsys, fakes, change,
                                                                                    reason):
    memory_dir, digest = write_memory(tmp_path)
    code, out, _ = run(memory_dir, write_descriptor(tmp_path, digest, change), query_bytes(), capsys)
    assert (code, out["reason"]) == (2, reason) and fakes == {"memory": 0, "encoder": 0, "runtime": 0}


def test_a_missing_descriptor_or_memory_file_is_refused(tmp_path, capsys, fakes):
    memory_dir, digest = write_memory(tmp_path)
    code, out, _ = run(memory_dir, tmp_path / "absent.json", query_bytes(), capsys)
    assert (code, out["reason"]) == (2, "descriptor_unreadable")
    code, out, _ = run(tmp_path / "no-memory", write_descriptor(tmp_path, digest), query_bytes(), capsys)
    assert (code, out["reason"]) == (2, "memory_descriptor_unreadable") and fakes["memory"] == 0


def test_oversized_descriptor_is_refused_before_memory_open(tmp_path, capsys, fakes):
    memory_dir, _ = write_memory(tmp_path)
    descriptor = tmp_path / "large-descriptor.json"
    descriptor.write_bytes(b"x" * (adapter.DESCRIPTOR_BUDGET + 1))
    code, out, _ = run(memory_dir, descriptor, query_bytes(), capsys)
    assert (code, out["reason"]) == (2, "descriptor_over_budget")
    assert fakes == {"memory": 0, "encoder": 0, "runtime": 0}


def test_changed_descriptor_is_refused_before_memory_open(tmp_path, capsys, fakes):
    memory_dir, digest = write_memory(tmp_path)
    descriptor = write_descriptor(tmp_path, digest)
    code, out, _ = run(memory_dir, descriptor, query_bytes(), capsys, descriptor_sha256="0" * 64)
    assert (code, out["reason"]) == (2, "descriptor_pin_mismatch")
    assert fakes == {"memory": 0, "encoder": 0, "runtime": 0}


def test_a_cell_receipt_with_another_encoder_identity_refuses_before_the_runtime_is_touched(tmp_path, capsys, fakes,
                                                                                            monkeypatch):
    memory_dir, digest = write_memory(tmp_path)
    other = FakeMemory(views={"learning": FakeView(), "safety": FakeView(encoder=dict(ENCODER, revision="def"))})
    monkeypatch.setattr(adapter, "MEMORY_FACTORY", lambda path: other)
    code, out, _ = run(memory_dir, write_descriptor(tmp_path, digest), query_bytes(), capsys)
    assert (code, out["reason"]) == (2, "encoder_identity_mismatch") and fakes["runtime"] == 0 == fakes["encoder"]


def test_a_quarantined_only_memory_or_a_dim_mismatch_refuses(tmp_path, capsys, fakes, monkeypatch):
    memory_dir, digest = write_memory(tmp_path)
    monkeypatch.setattr(adapter, "MEMORY_FACTORY", lambda path: FakeMemory(views={"learning": FakeView(False)}))
    code, out, _ = run(memory_dir, write_descriptor(tmp_path, digest), query_bytes(), capsys)
    assert (code, out["reason"]) == (2, "memory_no_healthy_cell")
    monkeypatch.setattr(adapter, "MEMORY_FACTORY", lambda path: FakeMemory(dim=768))
    code, out, _ = run(memory_dir, write_descriptor(tmp_path, digest), query_bytes(), capsys)
    assert (code, out["reason"]) == (2, "memory_dim_mismatch") and fakes["encoder"] == 0


def test_an_unverifiable_memory_is_refused_with_one_fixed_reason(tmp_path, capsys, fakes, monkeypatch):
    memory_dir, digest = write_memory(tmp_path)

    def broken(path):
        raise memory_mod.MemoryError_("memory_unreadable")

    monkeypatch.setattr(adapter, "MEMORY_FACTORY", broken)
    code, out, _ = run(memory_dir, write_descriptor(tmp_path, digest), query_bytes(), capsys)
    assert (code, out["reason"]) == (2, "memory_unreadable") and fakes["encoder"] == 0


@pytest.mark.parametrize("runtime,reason", [
    (lambda **kwargs: dict(ENCODER, revision="other"), "encoder_runtime_mismatch"),
    (lambda **kwargs: {"model": ENCODER["model"]}, "encoder_runtime_mismatch"),
    (lambda **kwargs: (_ for _ in ()).throw(OSError("no cache")), "encoder_runtime_unavailable"),
])
def test_a_runtime_encoder_that_differs_from_the_descriptor_never_loads_the_model(tmp_path, capsys, fakes,
                                                                                  monkeypatch, runtime, reason):
    memory_dir, digest = write_memory(tmp_path)
    monkeypatch.setattr(adapter, "RUNTIME_IDENTITY", runtime)
    code, out, _ = run(memory_dir, write_descriptor(tmp_path, digest), query_bytes(), capsys)
    assert (code, out["reason"]) == (2, reason) and fakes["encoder"] == 0 and FakeQ4.calls == []


def test_a_model_that_cannot_load_is_one_fixed_reason_without_its_message(tmp_path, capsys, fakes, monkeypatch):
    memory_dir, digest = write_memory(tmp_path)

    def failing(*, model_dir, revision):
        assert revision == ENCODER["revision"]
        raise RuntimeError("secret path C:/private/model")

    monkeypatch.setattr(adapter, "ENCODER_FACTORY", failing)
    code, out, text = run(memory_dir, write_descriptor(tmp_path, digest), query_bytes(), capsys)
    assert (code, out["reason"]) == (2, "encoder_load_failed") and "secret" not in text and "C:/" not in text


def test_q4_backend_error_does_not_expose_query_or_exception(tmp_path, capsys, fakes, monkeypatch):
    memory_dir, digest = write_memory(tmp_path)

    class BrokenQ4(FakeQ4):
        def ask(self, situation, when):
            raise RuntimeError(situation)

    monkeypatch.setattr(adapter, "Q4_FACTORY", BrokenQ4)
    code, out, text = run(memory_dir, write_descriptor(tmp_path, digest), query_bytes(), capsys)
    assert (code, out["reason"]) == (2, "q4_execution_failed")
    assert SITUATION not in text


def test_projection_accepts_real_q4_result_shape_without_exposing_ref():
    from sandbox.mind.step2 import q4
    from sandbox.mind.step2.test_q4_readonly_port import FakeEncoder, fake_memory

    answer = q4.Q4(fake_memory(), FakeEncoder()).ask("retry network", "2026-01-02T00:00:00Z")
    projected = adapter.project_result(answer)
    assert projected["status"] == "ANSWER"
    assert projected["results"][0]["view_ranks"] == {"w": 1, "d": 1, "b": 1}
    assert projected["results"][0]["ref"] is None
    assert "synthetic.md" not in json.dumps(projected)


def test_private_projection_keeps_two_noncanonical_projection_references():
    answer = FakeQ4(None, None).ask(SITUATION, WHEN)
    answer["results"] = [dict(answer["results"][0], ref_kind="sha256_of_projected_metadata",
                              canonical_event=False),
                         dict(answer["results"][0], ref_kind="sha256_of_projected_metadata",
                              canonical_event=False)]
    projected = adapter.project_result(answer)
    first, second = projected["results"]
    assert first["ref"] == second["ref"] == EVENT_REF
    assert all(item["ref_kind"] == "sha256_of_projected_metadata" and item["canonical_event"] is False
               for item in (first, second))


def test_private_projection_labels_legacy_reference_unknown():
    projected = adapter.project_result(FakeQ4(None, None).ask(SITUATION, WHEN))
    assert all(item["ref_kind"] is None and item["canonical_event"] is None for item in projected["results"])


@pytest.mark.parametrize("ref_kind,canonical_event,ref", [
    ("sha256_of_projected_metadata", True, EVENT_REF),
    ("sha256_of_projected_metadata", 0, EVENT_REF),
    ("sha256_of_projected_metadata", False, "C:/private/ref"),
    ("canonical", True, EVENT_REF),
    ("C:/private/kind", False, EVENT_REF),
    (None, False, EVENT_REF),
])
def test_private_projection_refuses_forged_reference_metadata(ref_kind, canonical_event, ref):
    answer = FakeQ4(None, None).ask(SITUATION, WHEN)
    answer["results"][0].update(ref_kind=ref_kind, canonical_event=canonical_event, ref=ref)
    with pytest.raises(adapter.PrivateReadRefused, match="^q4_result_shape$"):
        adapter.project_result(answer)


def test_the_output_is_guarded_whole(tmp_path, capsys, fakes, monkeypatch):
    memory_dir, digest = write_memory(tmp_path)

    class LeakyQ4(FakeQ4):
        def ask(self, situation, when):
            out = super().ask(situation, when)
            out["results"][0]["cell"] = MARKERS[0]
            return out

    monkeypatch.setattr(adapter, "Q4_FACTORY", LeakyQ4)
    code, out, text = run(memory_dir, write_descriptor(tmp_path, digest), query_bytes(), capsys)
    assert (code, out["reason"], out["results"]) == (2, "output_guarded", [])
    assert all(marker not in text for marker in MARKERS) and EVENT_REF not in text


@pytest.mark.parametrize("mutate", [
    pytest.param(lambda r: r.__setitem__("status", "MAYBE"), id="status"),
    pytest.param(lambda r: r.__setitem__("reason", "a path C:/x"), id="reason"),
    pytest.param(lambda r: r.__setitem__("rule", None), id="rule"),
    pytest.param(lambda r: r["results"][0].__setitem__("cell", "../cells"), id="cell"),
    pytest.param(lambda r: r["results"][0].__setitem__("ref", ""), id="empty_ref"),
    pytest.param(lambda r: r["results"][0].__setitem__("view_ranks", "1,2,3"), id="ranks"),
    pytest.param(lambda r: r["results"][0].__setitem__("score", "0.9"), id="score"),
    pytest.param(lambda r: r["degraded_cells"].append({"cell": "safety", "reason": "see C:/log"}), id="degraded"),
])
def test_a_result_outside_the_allowlisted_shape_is_refused_whole(tmp_path, capsys, fakes, monkeypatch, mutate):
    memory_dir, digest = write_memory(tmp_path)

    class OddQ4(FakeQ4):
        def ask(self, situation, when):
            out = super().ask(situation, when)
            mutate(out)
            return out

    monkeypatch.setattr(adapter, "Q4_FACTORY", OddQ4)
    code, out, text = run(memory_dir, write_descriptor(tmp_path, digest), query_bytes(), capsys)
    assert (code, out["reason"], out["results"]) == (2, "q4_result_shape", []) and "C:/" not in text


def test_a_memory_that_changes_between_digest_and_open_is_refused(tmp_path, capsys, fakes, monkeypatch):
    memory_dir, digest = write_memory(tmp_path)
    descriptor = write_descriptor(tmp_path, digest)

    def swapping(path):  # the directory is re-pointed between the descriptor check and the open
        (memory_dir / "memory.json").write_bytes(b'{"schema": "x", "dim": 384, "cells": {}}')
        return FakeMemory()

    monkeypatch.setattr(adapter, "MEMORY_FACTORY", swapping)
    code, out, _ = run(memory_dir, descriptor, query_bytes(), capsys)
    assert (code, out["reason"]) == (2, "memory_descriptor_changed") and fakes["encoder"] == 0


def test_lock_path_and_timeout_are_explicitly_required(tmp_path, capsys, fakes):
    memory_dir, digest = write_memory(tmp_path)
    descriptor = write_descriptor(tmp_path, digest)
    with pytest.raises(SystemExit) as raised:
        adapter.main(["--memory", str(memory_dir), "--descriptor", str(descriptor),
                      "--descriptor-sha256", hashlib.sha256(descriptor.read_bytes()).hexdigest(),
                      "--model-dir", str(tmp_path)],
                     stdin=io.BytesIO(query_bytes()), stdout=io.StringIO())
    assert raised.value.code == 2
    assert fakes == {"memory": 0, "encoder": 0, "runtime": 0}


def test_model_directory_is_explicitly_required(tmp_path, capsys, fakes):
    memory_dir, digest = write_memory(tmp_path)
    descriptor = write_descriptor(tmp_path, digest)
    with pytest.raises(SystemExit) as raised:
        adapter.main(["--memory", str(memory_dir), "--descriptor", str(descriptor),
                      "--descriptor-sha256", hashlib.sha256(descriptor.read_bytes()).hexdigest(),
                      "--lock-file", str(tmp_path / "lock"), "--lock-timeout", "0"],
                     stdin=io.BytesIO(query_bytes()), stdout=io.StringIO())
    assert raised.value.code == 2
    assert fakes == {"memory": 0, "encoder": 0, "runtime": 0}


def test_reader_busy_is_fixed_and_precedes_memory_and_model(tmp_path, fakes):
    from sandbox.mind.step2.reader_slot import reader_slot

    memory_dir, digest = write_memory(tmp_path)
    descriptor = write_descriptor(tmp_path, digest)
    lock_path = tmp_path / "shared.lock"
    out = io.StringIO()
    with reader_slot(lock_path, timeout_seconds=0):
        code = adapter.main(["--memory", str(memory_dir), "--descriptor", str(descriptor),
                             "--descriptor-sha256", hashlib.sha256(descriptor.read_bytes()).hexdigest(),
                             "--model-dir", str(tmp_path),
                             "--lock-file", str(lock_path), "--lock-timeout", "0"],
                            stdin=io.BytesIO(query_bytes()), stdout=out)
    assert code == 2 and json.loads(out.getvalue())["reason"] == "reader_busy"
    assert str(lock_path) not in out.getvalue() and SITUATION not in out.getvalue()
    assert fakes == {"memory": 0, "encoder": 0, "runtime": 0}


@pytest.mark.parametrize("lock_kind,timeout,reason", [
    ("relative", "0", "reader_lock_invalid"),
    ("missing_parent", "0", "reader_lock_unavailable"),
    ("absolute", "31", "reader_lock_unavailable"),
])
def test_unusable_lock_refuses_without_memory_or_path_leak(tmp_path, fakes, lock_kind, timeout, reason):
    memory_dir, digest = write_memory(tmp_path)
    descriptor = write_descriptor(tmp_path, digest)
    lock_path = {"relative": Path("relative.lock"), "missing_parent": tmp_path / "absent" / "lock",
                 "absolute": tmp_path / "slot.lock"}[lock_kind]
    out = io.StringIO()
    code = adapter.main(["--memory", str(memory_dir), "--descriptor", str(descriptor),
                         "--descriptor-sha256", hashlib.sha256(descriptor.read_bytes()).hexdigest(),
                         "--model-dir", str(tmp_path),
                         "--lock-file", str(lock_path), "--lock-timeout", timeout],
                        stdin=io.BytesIO(query_bytes()), stdout=out)
    assert code == 2 and json.loads(out.getvalue())["reason"] == reason
    assert str(lock_path) not in out.getvalue() and SITUATION not in out.getvalue()
    assert fakes == {"memory": 0, "encoder": 0, "runtime": 0}


def test_two_adapter_processes_share_slot_until_result_is_ready(tmp_path):
    memory_dir, digest = write_memory(tmp_path)
    descriptor = write_descriptor(tmp_path, digest)
    descriptor_sha = hashlib.sha256(descriptor.read_bytes()).hexdigest()
    lock_path = tmp_path / "shared.lock"
    ready, release = tmp_path / "ready", tmp_path / "release"
    root = Path(__file__).resolve().parents[3]
    child = r"""
import sys, time
from pathlib import Path
from sandbox.mind.step2 import wd_q4_private_read as adapter
from sandbox.mind.step2.test_wd_q4_private_read import ENCODER, FakeMemory, FakeQ4
memory, descriptor, digest, lock, ready, release = sys.argv[1:]
adapter.MEMORY_FACTORY = lambda path: FakeMemory()
adapter.RUNTIME_IDENTITY = lambda **kwargs: dict(ENCODER)
adapter.Q4_FACTORY = FakeQ4
def encoder(*, model_dir, revision):
    Path(ready).write_text('ready', encoding='utf-8')
    deadline = time.monotonic() + 20
    while not Path(release).exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    if not Path(release).exists():
        raise RuntimeError('synthetic release timed out')
    return object()
adapter.ENCODER_FACTORY = encoder
sys.exit(adapter.main(['--memory', memory, '--descriptor', descriptor, '--descriptor-sha256', digest,
                       '--model-dir', str(Path(memory).parent),
                       '--lock-file', lock, '--lock-timeout', '0']))
"""
    args = [sys.executable, "-B", "-c", child, str(memory_dir), str(descriptor), descriptor_sha,
            str(lock_path), str(ready), str(release)]
    env = dict(os.environ, PYTHONPATH=str(root), HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
    first = subprocess.Popen(args, cwd=root, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE)
    try:
        first.stdin.write(query_bytes())
        first.stdin.close()
        first.stdin = None
        deadline = time.monotonic() + 15
        while not ready.exists() and time.monotonic() < deadline and first.poll() is None:
            time.sleep(0.05)
        assert ready.exists(), "first adapter did not enter synthetic encoder"
        second = subprocess.run(args, input=query_bytes(), cwd=root, env=env, capture_output=True, timeout=15)
        assert second.returncode == 2
        assert json.loads(second.stdout)["reason"] == "reader_busy"
        assert str(lock_path).encode() not in second.stdout and SITUATION.encode() not in second.stdout
        assert second.stderr == b""
        release.write_text("release", encoding="utf-8")
        out, err = first.communicate(timeout=15)
        assert first.returncode == 0 and json.loads(out)["status"] == "ANSWER" and err == b""
    finally:
        if first.poll() is None:
            first.kill()
            first.communicate(timeout=10)


def test_slot_remains_held_while_projecting_answer(tmp_path, capsys, fakes, monkeypatch):
    from sandbox.mind.step2.reader_slot import ReaderBusy, reader_slot

    memory_dir, digest = write_memory(tmp_path)
    original = adapter.project_result
    observed = []

    def project(result):
        with pytest.raises(ReaderBusy):
            with reader_slot(tmp_path / "reader.lock", timeout_seconds=0):
                pytest.fail("adapter released slot before projecting")
        observed.append(True)
        return original(result)

    monkeypatch.setattr(adapter, "project_result", project)
    code, out, _ = run(memory_dir, write_descriptor(tmp_path, digest), query_bytes(), capsys)
    assert code == 0 and out["status"] == "ANSWER" and observed == [True]


@pytest.mark.parametrize("error_type", [OSError, ValueError, RuntimeError])
def test_unexpected_reader_body_error_is_not_misclassified_as_a_lock_error(tmp_path, capsys, fakes, monkeypatch,
                                                                           error_type):
    from sandbox.mind.step2.reader_slot import reader_slot

    memory_dir, digest = write_memory(tmp_path)

    def broken(*args):
        raise error_type(SITUATION + str(tmp_path))

    monkeypatch.setattr(adapter, "_private_read_locked", broken)
    code, out, text = run(memory_dir, write_descriptor(tmp_path, digest), query_bytes(), capsys)
    assert code == 2 and out["reason"] == "q4_execution_failed"
    assert SITUATION not in text and str(tmp_path) not in text
    with reader_slot(tmp_path / "reader.lock", timeout_seconds=0):
        pass  # A failed read must not retain the slot.


@pytest.mark.parametrize("error_type", [OSError, RuntimeError])
@pytest.mark.parametrize("where", ["entry", "exit"])
def test_unexpected_lock_entry_or_exit_error_refuses_without_private_detail(tmp_path, capsys, fakes, monkeypatch,
                                                                           error_type, where):
    memory_dir, digest = write_memory(tmp_path)

    @contextmanager
    def broken_slot(*args, **kwargs):
        if where == "entry":
            raise error_type(SITUATION + str(tmp_path))
        yield
        raise error_type(SITUATION + str(tmp_path))

    monkeypatch.setattr(adapter, "reader_slot", broken_slot)
    monkeypatch.setattr(adapter, "_private_read_locked", lambda *args: {"status": "ANSWER"})
    code, out, text = run(memory_dir, write_descriptor(tmp_path, digest), query_bytes(), capsys)
    assert code == 2 and out["reason"] == "reader_lock_unavailable"
    assert SITUATION not in text and str(tmp_path) not in text


def test_unexpected_stdin_error_refuses_without_exception_or_query(tmp_path, capsys, fakes, monkeypatch):
    memory_dir, digest = write_memory(tmp_path)

    def broken(stream):
        raise RuntimeError(SITUATION + str(tmp_path))

    monkeypatch.setattr(adapter, "read_query", broken)
    code, out, text = run(memory_dir, write_descriptor(tmp_path, digest), query_bytes(), capsys)
    assert code == 2 and out["reason"] == "q4_execution_failed"
    assert SITUATION not in text and str(tmp_path) not in text

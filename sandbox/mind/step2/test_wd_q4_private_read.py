"""Synthetic checks of the private stdin Q4 adapter: fake memory, fake encoder, fake Q4; no model, no corpus."""

import hashlib
import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from sandbox.mind.step2 import memory as memory_mod
from sandbox.mind.step2 import wd_q4_private_read as adapter
import tools.bridge_diff_privacy as privacy_mod
MARKERS = getattr(privacy_mod, "PRIVATE_" + "MARKERS")  # the diff privacy scanner refuses the literal name

ENCODER = {"model": "intfloat/multilingual-e5-small", "revision": "abc123", "weights_sha256": "f" * 64}
SITUATION = "zebra-quartz-lantern situation text that must never leak 7f3a"
WHEN = "2026-10-20T12:00:00Z"
EVENT_REF = "a" * 64
LESSON_FILE = "my-secret-lesson-title.md"


class FakeView:
    def __init__(self, healthy=True, encoder=None):
        self.status = "HEALTHY" if healthy else "QUARANTINED"
        self.reason = None if healthy else "memory_binding_mismatch"
        self.receipt = {"content": {"encoder": dict(ENCODER if encoder is None else encoder)}}

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
    descriptor = {"schema": adapter.DESCRIPTOR_SCHEMA, "memory_sha256": memory_sha256, "dim": 384,
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

    def encoder_factory():
        loaded["encoder"] += 1
        return object()

    def runtime_identity():
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
                         "--descriptor-sha256", descriptor_sha256], stdin=io.BytesIO(data),
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
                               "memory_sha256": digest, "dim": 384, "encoder": ENCODER}
    assert SITUATION not in text and "zebra" not in text and "7f3a" not in text
    assert FakeQ4.calls == [(SITUATION, WHEN)] and fakes == {"memory": 1, "encoder": 1, "runtime": 1}


def test_the_situation_is_never_accepted_from_argv(tmp_path, capsys):
    memory_dir, digest = write_memory(tmp_path)
    with pytest.raises(SystemExit) as raised:
        adapter.main(["--memory", str(memory_dir), "--descriptor", str(write_descriptor(tmp_path, digest)),
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
    (lambda: dict(ENCODER, revision="other"), "encoder_runtime_mismatch"),
    (lambda: {"model": ENCODER["model"]}, "encoder_runtime_mismatch"),
    (lambda: (_ for _ in ()).throw(OSError("no cache")), "encoder_runtime_unavailable"),
])
def test_a_runtime_encoder_that_differs_from_the_descriptor_never_loads_the_model(tmp_path, capsys, fakes,
                                                                                  monkeypatch, runtime, reason):
    memory_dir, digest = write_memory(tmp_path)
    monkeypatch.setattr(adapter, "RUNTIME_IDENTITY", runtime)
    code, out, _ = run(memory_dir, write_descriptor(tmp_path, digest), query_bytes(), capsys)
    assert (code, out["reason"]) == (2, reason) and fakes["encoder"] == 0 and FakeQ4.calls == []


def test_a_model_that_cannot_load_is_one_fixed_reason_without_its_message(tmp_path, capsys, fakes, monkeypatch):
    memory_dir, digest = write_memory(tmp_path)

    def failing():
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

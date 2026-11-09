"""Useful content route B through the agent query boundary (lead-q4-useful-work-content-fable-20261107-v1, increment 7a).

Synthetic data only. The chain is the real one where it can run in process: ``wd_q4_agent_query.query_and_resolve``,
the shared private reader (``wd_q4_private_read._private_read_locked`` with a synthetic encoder), the launcher output
check (``runtime_launcher._validate_output``) and the resolver content branch; only the launcher process call is
replaced by the private reader's own output. The content route is OFF unless the caller passes a pinned content route
decision that verifies; any refusal before the reader runs must not start it.
"""
import datetime as dt
import io
import json

import pytest

from sandbox.mind.step2 import shared_content_projection as cp
from sandbox.mind.step2 import test_content_memory as tcm
from sandbox.mind.step2 import test_content_route as tcr
from sandbox.mind.step2 import test_shared_content_projection as tcp
from sandbox.mind.step2 import test_shared_doc_projection as tdp
from tools import wd_q4_agent_query as q4

SITUATION = "miten hex-muisti tallentaa parven p" + tcm.A + tcm.A + "t" + tcm.O + "kset"


def sources(tmp_path):
    path, generation, cursor, prefix_sha = tcr.write_prefix(tmp_path / "prefix", tcr.prefix_rows())
    policy_bytes, policy_sha = tcr.message_policy()
    messages = {"source_path": str(path), "generation_path": str(generation), "end_cursor": cursor,
                "policy_bytes": policy_bytes, "policy_sha256": policy_sha}
    documents, _, commit = tcm.doc_source(tmp_path, {"docs/guide.md": tdp.doc_bytes(tcm.DOC_TEXT)}, ["docs/guide.md"])
    prefix = {"sha256": prefix_sha, "offset": cursor.offset, "generation": cursor.generation}
    decision, decision_sha = tcr.decision_of(prefix=prefix, message_pin=policy_sha, doc_pin=documents["policy_sha256"])
    return {"messages": messages, "documents": documents, "decision": decision, "decision_sha": decision_sha}


def config(memory_dir, descriptor_sha256):
    return q4.VerifiedQ4Config(interpreter=r"C:\fake\python.exe", interpreter_sha256="1" * 64,
        bootstrap=r"C:\fake\runtime_bootstrap.py", bootstrap_sha256="2" * 64, verifier_sha256="3" * 64,
        runtime_root=r"C:\fake\runtime", runtime_manifest=r"C:\fake\runtime.json", runtime_manifest_sha256="4" * 64,
        runtime_generation="5" * 40, runtime_revision=tcm.IDENTITY["revision"], memory=str(memory_dir),
        descriptor=str(memory_dir / "descriptor.json"), descriptor_sha256=descriptor_sha256,
        lock_file=r"C:\fake\lock", lock_timeout="2", timeout=30, scratch_root=r"C:\fake-scratch")


@pytest.fixture
def content(tmp_path, monkeypatch):
    pytest.importorskip("faiss")
    from sandbox.mind.step2 import runtime_launcher
    from sandbox.mind.step2 import wd_q4_private_read as reader
    route = sources(tmp_path)
    built, memory_dir = tcr.build_route(tmp_path, route)
    descriptor_sha256 = built["descriptor_sha256"]
    monkeypatch.setattr(reader, "ENCODER_FACTORY", lambda **kw: tcm.Encoder())
    monkeypatch.setattr(reader, "bind_runtime_encoder", lambda descriptor, model_dir: None)
    calls = []

    def launcher(*, query, **kwargs):
        """The private reader's real output through the real launcher output check (no process)."""
        situation = json.loads(query)
        calls.append(True)
        out = reader._private_read_locked(memory_dir, memory_dir / "descriptor.json", descriptor_sha256,
                                          situation["situation"], situation["time"], r"C:\fake\model",
                                          tcm.IDENTITY["revision"])
        return runtime_launcher._validate_output(json.dumps(out).encode("utf-8"), 0, descriptor_sha256)

    monkeypatch.setattr(q4, "_invoke_launcher", launcher)
    monkeypatch.setattr(q4, "_utc_now", lambda: dt.datetime(2026, 11, 8, 1, tzinfo=dt.timezone.utc))
    strict = tcr.strict_pins()
    args = dict(memory_manifest_bytes=(memory_dir / "memory.json").read_bytes(),
                projection_bytes=(memory_dir / "projection.json").read_bytes(),
                approved_tasks=frozenset({tcp.TASK, tcr.OTHER_TASK}), expected_memory_sha256=built["memory_sha256"],
                content_decision_bytes=route["decision"], content_decision_sha256=route["decision_sha"], **strict)
    return {"config": config(memory_dir, descriptor_sha256), "args": args, "calls": calls, "dir": memory_dir,
            "route": route}


def ask(content, situation=SITUATION, when=tcm.ASK_TIME, **changes):
    args = dict(content["args"])
    args.update(changes)
    args = {key: value for key, value in args.items() if value is not ...}
    query = json.dumps({"situation": situation, "time": when}).encode("utf-8")
    return q4.query_and_resolve(content["config"], io.BytesIO(query), args.pop("memory_manifest_bytes"),
                                args.pop("projection_bytes"), args.pop("approved_tasks"), **args)


def test_the_agent_query_answers_content_with_the_decision_end_to_end(content):
    out = ask(content)
    assert out["status"] == "ANSWER" and content["calls"] == [True]
    texts = [cp.decode_text(item["stored_text"]) for item in out["results"]]
    assert tcm.DOC_TEXT in texts
    for item in out["results"]:
        assert set(item) == {"ref_sha256", "doc_id_sha256", "stored_text", "ref_kind", "canonical_event"}
        assert item["canonical_event"] is False and item["ref_kind"] == "sha256_of_projected_metadata"
        assert tcp.TEXT_CLASS.fullmatch(item["stored_text"])


@pytest.mark.parametrize("change", [
    {"content_decision_bytes": ..., "content_decision_sha256": ...},
    {"content_decision_sha256": "0" * 64},
    {"content_decision_bytes": ...},
    {"content_decision_sha256": ...},
    {"card_decision_bytes": b"{}", "card_decision_sha256": "0" * 64},
    {"policy_bytes": tcr.strict_pins(tasks=(tcp.TASK,))["policy_bytes"],
     "policy_sha256": tcr.strict_pins(tasks=(tcp.TASK,))["policy_sha256"]},
    {"expected_memory_sha256": "0" * 64},
])
def test_any_other_content_input_refuses_before_the_reader_runs(content, change):
    with pytest.raises(q4.QueryRefused):
        ask(content, **change)
    assert content["calls"] == []


def test_a_content_decision_for_other_module_bytes_refuses_before_the_reader_runs(content, monkeypatch):
    from sandbox.mind.step2 import content_memory as cm
    monkeypatch.setattr(cm, "module_sha256s", lambda: dict.fromkeys(cm.module_sha256s(), "0" * 64))
    with pytest.raises(q4.QueryRefused):
        ask(content)
    assert content["calls"] == []


def test_the_content_answer_never_carries_unencoded_text(content):
    out = ask(content)
    blob = json.dumps(out, ensure_ascii=False)
    assert tcm.DOC_TEXT not in blob and all(ord(ch) < 128 for ch in blob)


def test_a_card_snapshot_never_rides_the_content_route(content):
    with pytest.raises(q4.QueryRefused):
        ask(content, card_snapshot={})
    assert content["calls"] == []


@pytest.mark.parametrize("stray", [{"content_decision_sha256": "0" * 64}, {"content_decision_bytes": b"{}"}])
def test_any_content_decision_part_keeps_a_v6_memory_closed(monkeypatch, stray):
    import importlib.util
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location("resolved_child_fixture_c", root / "tests/tools/test_wd_q4_resolved_child.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    data, result, record = module.fixture()
    monkeypatch.setattr(q4, "_utc_now", lambda: dt.datetime(2026, 10, 31, 13, tzinfo=dt.timezone.utc))
    monkeypatch.setattr(q4, "_invoke_launcher", lambda **_: result)
    query = b'{"situation":"synthetic question","time":"2026-10-31T12:02:00Z"}'
    args = dict(policy_bytes=data["policy_bytes"], policy_sha256=data["policy_sha256"],
                roster_bytes=data["roster_bytes"], roster_sha256=data["roster_sha256"],
                expected_memory_sha256=data["memory_sha256"])
    tasks = frozenset({"synthetic-task"})
    out = q4.query_and_resolve(module.config(), io.BytesIO(query), data["memory_bytes"], data["projection_bytes"],
                               tasks, **args)
    assert out["results"][0]["stored_text"] == record["stored_text"]
    with pytest.raises(q4.QueryRefused):
        q4.query_and_resolve(module.config(), io.BytesIO(query), data["memory_bytes"], data["projection_bytes"],
                             tasks, **args, **stray)

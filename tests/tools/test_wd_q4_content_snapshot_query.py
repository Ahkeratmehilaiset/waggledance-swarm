"""Caller-pinned content snapshot query (lead-q4-useful-work-content-fable-20261107-v1, increment 7b1, Lead 20:06Z B).

Synthetic data only. ``wd_q4_public_child.run_content`` hands stdin unread to
``wd_q4_public_query.query_content_snapshot``, which checks the pinned content decision, descriptor, memory and
projection before the query, runs the agent query boundary (the real private reader and launcher output check in
process; only the launcher process call is replaced), re-reads every pinned artifact after the query and binds every
pin into the output. OFF unless enabled; any refusal is the fixed refusal line with exit code 2.
"""
import dataclasses
import hashlib
import importlib.util
import io
import json
from pathlib import Path

import pytest

from sandbox.mind.step2 import shared_content_projection as cp
from sandbox.mind.step2 import test_content_memory as tcm
from sandbox.mind.step2 import test_content_route as tcr
from sandbox.mind.step2.shared_index_preflight import _encoder_digest
from tools import wd_q4_agent_query as q4
from tools import wd_q4_public_child as child
from tools import wd_q4_public_query as public

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("content_query_fixture", ROOT / "tests/tools/test_wd_q4_content_query.py")
cq = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cq)
content = cq.content

SCHEMA = "wd.q4-content-snapshot-query.v1"
FAIL = {"schema": SCHEMA, "status": "REFUSED", "reason": "content_query_refused"}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def pins(content):
    args = content["args"]
    descriptor = json.loads((content["dir"] / "descriptor.json").read_bytes())
    return dict(expected_encoder_sha256=_encoder_digest(descriptor["encoder"]), policy_bytes=args["policy_bytes"],
                policy_sha256=args["policy_sha256"], roster_bytes=args["roster_bytes"],
                roster_sha256=args["roster_sha256"], content_decision_bytes=args["content_decision_bytes"],
                content_decision_sha256=args["content_decision_sha256"],
                expected_memory_sha256=args["expected_memory_sha256"],
                expected_projection_sha256=sha(args["projection_bytes"]),
                content_sources=content_sources(content["route"]))


def content_sources(route):
    """The decision's sources as the query path takes them (synthetic files of the content fixture)."""
    messages, documents = route["messages"], route["documents"]
    return {"message_source_path": messages["source_path"], "message_generation_path": messages["generation_path"],
            "message_policy_bytes": messages["policy_bytes"], "doc_repo_root": documents["repo_root"],
            "doc_policy_bytes": documents["policy_bytes"], "doc_git_executable": documents["git_executable"],
            "doc_git_sha256": documents["git_sha256"]}


def run(content, config=None, **changes):
    kwargs = dict(pins(content), enabled=True)
    kwargs.update(changes)
    out = io.StringIO()
    query = json.dumps({"situation": cq.SITUATION, "time": tcm.ASK_TIME}).encode("utf-8")
    code = child.run_content(config or content["config"], io.BytesIO(query), out, **kwargs)
    return code, json.loads(out.getvalue())


def expected_binding(content):
    p = {key: value for key, value in pins(content).items() if key != "content_sources"}
    return {"source_mode": "content-snapshot-v1", "encoder_sha256": p["expected_encoder_sha256"],
            "policy_sha256": p["policy_sha256"], "roster_sha256": p["roster_sha256"],
            "content_decision_sha256": p["content_decision_sha256"],
            "descriptor_sha256": content["config"].descriptor_sha256, "memory_sha256": p["expected_memory_sha256"],
            "projection_sha256": p["expected_projection_sha256"]}


def test_the_content_snapshot_answers_with_every_pin_bound(content):
    code, out = run(content)
    assert code == 0 and content["calls"] == [True]
    assert set(out) == {"schema", "status", "results", "binding", "publisher_authentication",
                        "source_filter_attestation", "freshness", "same_user_aba", "completeness"}
    assert out["schema"] == SCHEMA and out["status"] == "ANSWER"
    assert out["binding"] == expected_binding(content)
    assert all(out[key] == "UNKNOWN" for key in ("publisher_authentication", "source_filter_attestation",
                                                 "freshness", "same_user_aba", "completeness"))
    assert tcm.DOC_TEXT in [cp.decode_text(item["stored_text"]) for item in out["results"]]
    for item in out["results"]:
        assert set(item) == {"canonical_event", "doc_id_sha256", "ref_kind", "ref_sha256", "stored_text"}
        assert len(item["stored_text"].encode("utf-8")) <= 320 and item["canonical_event"] is False


def test_the_query_function_returns_the_same_bound_answer(content):
    query = json.dumps({"situation": cq.SITUATION, "time": tcm.ASK_TIME}).encode("utf-8")
    out = public.query_content_snapshot(content["config"], io.BytesIO(query), enabled=True, **pins(content))
    assert out["schema"] == SCHEMA and out["binding"] == expected_binding(content)


def test_the_content_snapshot_is_off_unless_enabled(content):
    for enabled in (False, None, 1, "true"):
        assert run(content, enabled=enabled) == (2, FAIL)
    with pytest.raises(public.PublicQueryRefused):
        public.query_content_snapshot(content["config"], io.BytesIO(b"{}"), **pins(content))
    assert content["calls"] == []


@pytest.mark.parametrize("change", [
    {"expected_memory_sha256": "0" * 64},
    {"expected_projection_sha256": "0" * 64},
    {"expected_encoder_sha256": "0" * 64},
    {"content_decision_sha256": "0" * 64},
    {"content_decision_bytes": b"{}"},
    {"content_decision_bytes": None, "content_decision_sha256": None},
    {"policy_bytes": tcr.strict_pins(tasks=(cq.tcp.TASK,))["policy_bytes"],
     "policy_sha256": tcr.strict_pins(tasks=(cq.tcp.TASK,))["policy_sha256"]},
    {"roster_sha256": "0" * 64},
    {"policy_bytes": None},
])
def test_any_other_pin_refuses_before_the_reader_runs(content, change):
    assert run(content, **change) == (2, FAIL)
    assert content["calls"] == []


@pytest.mark.parametrize("field, value", [
    ("descriptor_sha256", "0" * 64),
    ("descriptor", "C:\\fake\\descriptor.json"),
    ("memory", "C:\\fake\\memory"),
    ("runtime_revision", "0" * 40),
])
def test_a_config_not_naming_the_pinned_snapshot_refuses_before_the_reader_runs(content, field, value):
    config = dataclasses.replace(content["config"], **{field: value})
    assert run(content, config=config) == (2, FAIL)
    assert content["calls"] == []


def test_an_artifact_changed_during_the_query_refuses(content, monkeypatch):
    original = q4._invoke_launcher

    def launcher(**kwargs):
        result = original(**kwargs)
        path = content["dir"] / "projection.json"
        path.write_bytes(path.read_bytes() + b"\n")
        return result

    monkeypatch.setattr(q4, "_invoke_launcher", launcher)
    assert run(content) == (2, FAIL)
    assert content["calls"] == [True]



def test_the_copied_content_constants_are_the_content_module_constants():
    from sandbox.mind.step2 import content_memory as cm
    assert public._CONTENT_PROJECTION_SCHEMA == cm.SCHEMA
    assert child.MAX_CONTENT_DECISION == cm.MAX_DECISION


@pytest.mark.parametrize("edit", [
    lambda m: m.update(projection_schema="wd.bridge-mind-shared-card-projection.v7"),
    lambda m: m.update(projection_sha256="0" * 64),
])
def test_a_memory_manifest_for_another_projection_refuses_before_the_reader_runs(content, edit):
    from sandbox.mind.step2 import cell as cell_mod
    path = content["dir"] / "memory.json"
    manifest = json.loads(path.read_bytes())
    edit(manifest)
    data = cell_mod.canonical(manifest).encode("utf-8")
    path.write_bytes(data)
    assert run(content, expected_memory_sha256=sha(data)) == (2, FAIL)
    assert content["calls"] == []


def test_a_descriptor_for_another_memory_refuses_before_the_reader_runs(content):
    path = content["dir"] / "descriptor.json"
    descriptor = json.loads(path.read_bytes())
    descriptor["memory_sha256"] = "0" * 64
    data = json.dumps(descriptor, sort_keys=True).encode("utf-8")
    path.write_bytes(data)
    config = dataclasses.replace(content["config"], descriptor_sha256=sha(data))
    assert run(content, config=config) == (2, FAIL)
    assert content["calls"] == []


def test_a_bound_output_with_a_changed_pin_is_refused_by_the_child(content, monkeypatch):
    original = public.query_content_snapshot

    def query(*args, **kwargs):
        out = original(*args, **kwargs)
        out["binding"]["memory_sha256"] = "0" * 64
        return out

    monkeypatch.setattr(public, "query_content_snapshot", query)
    assert run(content) == (2, FAIL)


def test_a_bound_output_with_another_source_mode_is_refused_by_the_child(content, monkeypatch):
    original = public.query_content_snapshot

    def query(*args, **kwargs):
        out = original(*args, **kwargs)
        out["binding"]["source_mode"] = "card-snapshot-v1"
        return out

    monkeypatch.setattr(public, "query_content_snapshot", query)
    assert run(content) == (2, FAIL)


# --- each layer's own checks, with the agent query boundary replaced by a recording stub --------------------------

def stub_boundary(monkeypatch, text="synthetic answer"):
    calls = []

    def query_and_resolve(*args, **kwargs):
        calls.append(True)
        return {"status": "ANSWER", "results": [{"ref_sha256": "a" * 64, "doc_id_sha256": "b" * 64,
                                                 "stored_text": text, "ref_kind": "sha256_of_projected_metadata",
                                                 "canonical_event": False}]}

    monkeypatch.setattr(q4, "query_and_resolve", query_and_resolve)
    return calls


def test_the_stubbed_boundary_alone_answers(content, monkeypatch):
    calls = stub_boundary(monkeypatch)
    code, out = run(content)
    assert code == 0 and calls == [True] and out["results"][0]["stored_text"] == "synthetic answer"


def test_the_query_function_checks_the_decision_itself(content, monkeypatch):
    calls = stub_boundary(monkeypatch)
    other, other_sha = tcr.decision_of(prefix=None, message_pin=None, doc_pin="c" * 64, nonreview_policy_sha256="d" * 64)
    assert run(content, content_decision_bytes=other, content_decision_sha256=other_sha) == (2, FAIL)
    assert calls == []


@pytest.mark.parametrize("edit", [
    lambda m: m.update(projection_schema="wd.bridge-mind-shared-card-projection.v7"),
    lambda m: m.update(projection_sha256="0" * 64),
])
def test_the_query_function_checks_the_manifest_itself(content, monkeypatch, edit):
    from sandbox.mind.step2 import cell as cell_mod
    calls = stub_boundary(monkeypatch)
    path = content["dir"] / "memory.json"
    manifest = json.loads(path.read_bytes())
    edit(manifest)
    data = cell_mod.canonical(manifest).encode("utf-8")
    path.write_bytes(data)
    descriptor_path = content["dir"] / "descriptor.json"
    descriptor = json.loads(descriptor_path.read_bytes())
    descriptor["memory_sha256"] = sha(data)
    descriptor_bytes = json.dumps(descriptor, sort_keys=True).encode("utf-8")
    descriptor_path.write_bytes(descriptor_bytes)
    config = dataclasses.replace(content["config"], descriptor_sha256=sha(descriptor_bytes))
    assert run(content, config=config, expected_memory_sha256=sha(data)) == (2, FAIL)
    assert calls == []


def test_the_query_function_refuses_an_oversize_text(content, monkeypatch):
    stub_boundary(monkeypatch, text="x" * 321)
    assert run(content) == (2, FAIL)
    query = json.dumps({"situation": cq.SITUATION, "time": tcm.ASK_TIME}).encode("utf-8")
    with pytest.raises(public.PublicQueryRefused):
        public.query_content_snapshot(content["config"], io.BytesIO(query), enabled=True, **pins(content))


def test_the_query_function_itself_is_off_unless_enabled(content, monkeypatch):
    calls = stub_boundary(monkeypatch)
    query = json.dumps({"situation": cq.SITUATION, "time": tcm.ASK_TIME}).encode("utf-8")
    for enabled in (False, None, 1):
        with pytest.raises(public.PublicQueryRefused):
            public.query_content_snapshot(content["config"], io.BytesIO(query), enabled=enabled, **pins(content))
    assert calls == []


def test_a_bound_output_with_another_schema_is_refused_by_the_child(content, monkeypatch):
    stub_boundary(monkeypatch)
    original = public.query_content_snapshot

    def query(*args, **kwargs):
        out = original(*args, **kwargs)
        out["schema"] = "wd.q4-card-snapshot-query.v1"
        return out

    monkeypatch.setattr(public, "query_content_snapshot", query)
    assert run(content) == (2, FAIL)

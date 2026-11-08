"""The default-off content-memory build child (lead-mind-4h-fable-memory-20261108-v1).

Synthetic throughout: the route fixture of ``test_content_route`` (anchored prefix, pinned document repository,
verified decision), the synthetic model directory and the fake ``sentence_transformers`` backend of
``test_wd_q4_prefix_build_child`` (so the fixed E5 path of the prefix child runs end to end without a real model),
and the fixture's own Git record as the trusted pin. A built memory must re-derive under
``content_memory.validate_content_snapshot`` exactly as the Spy-built memories of the content tests do.
"""
import hashlib
import json
from pathlib import Path

import pytest

from sandbox.mind.step2 import content_memory as cm
from sandbox.mind.step2 import encode as encode_mod
from sandbox.mind.step2 import test_content_rederive as tcrd
from sandbox.mind.step2 import test_content_route as tcr
from sandbox.mind.step2 import wd_q4_content_build_child as child
from sandbox.mind.step2 import wd_q4_prefix_build_child as prefix
from sandbox.mind.step2.test_wd_q4_prefix_build_child import (REVISION, WEIGHTS_SHA, Poison, assets_of, backend,  # noqa: F401
                                                             model_dir)

route = tcr.route


def sha(data):
    return hashlib.sha256(data).hexdigest()


def enc(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def write(path, data):
    path.write_bytes(data)
    return str(path), sha(data)


@pytest.fixture
def setup(tmp_path, route, model_dir, backend):
    """A pinned plan over the route fixture: decision, strict pins, message and document policies as files; the
    output root beside the model directory; the fixture's Git record as the trusted pin."""
    pytest.importorskip("faiss")
    files = tmp_path / "plan-files"
    files.mkdir()
    out = tmp_path / "out"
    out.mkdir()
    strict = tcr.strict_pins()
    decision_path, _ = write(files / "decision.json", route["decision"])
    policy_path, _ = write(files / "nonreview-policy.json", strict["policy_bytes"])
    roster_path, _ = write(files / "roster.json", strict["roster_bytes"])
    message_policy, _ = write(files / "message-policy.json", route["messages"]["policy_bytes"])
    doc_policy, _ = write(files / "doc-policy.json", route["documents"]["policy_bytes"])
    plan = {"schema": child.PLAN_SCHEMA, "name": "content-a",
            "encoder": {"model": encode_mod.MODEL, "revision": REVISION, "model_dir": str(model_dir),
                        "weights_sha256": WEIGHTS_SHA, "dim": encode_mod.DIM, "assets": assets_of(model_dir)},
            "decision_path": decision_path, "decision_sha256": route["decision_sha"],
            "nonreview_policy_path": policy_path, "nonreview_policy_sha256": strict["policy_sha256"],
            "roster_path": roster_path, "roster_sha256": strict["roster_sha256"],
            "messages": {"source_path": route["messages"]["source_path"],
                         "generation_path": route["messages"]["generation_path"], "policy_path": message_policy},
            "documents": {"repo_root": route["documents"]["repo_root"], "policy_path": doc_policy}}
    return {"plan": plan, "out": out, "files": files, "route": route, "model_dir": model_dir, "backend": backend,
            "trusted_git": tcr.trusted_git(route), "strict": strict}


def run(setup, plan=None, *, plan_bytes=None, plan_sha=None, trusted_git=..., trusted_model_root=None, root=None):
    raw = enc(setup["plan"] if plan is None else plan) if plan_bytes is None else plan_bytes
    return child.build_content_generation(
        enabled=True, plan_bytes=raw, expected_plan_sha256=sha(raw) if plan_sha is None else plan_sha,
        trusted_model_root=str(setup["model_dir"]) if trusted_model_root is None else trusted_model_root,
        trusted_git=setup["trusted_git"] if trusted_git is ... else trusted_git,
        root=str(setup["out"]) if root is None else root)


def refused(token, setup, plan=None, **changes):
    with pytest.raises(child.ContentBuildRefused, match="^" + token + "$"):
        run(setup, plan, **changes)
    assert list(setup["out"].iterdir()) == []


def plan_with(setup, **changes):
    plan = dict(setup["plan"])
    for key, value in changes.items():
        if value is ...:
            del plan[key]
        else:
            plan[key] = value
    return plan


def test_the_child_is_off_by_default_and_inspects_nothing():
    assert child.build_content_generation() == {"status": "OFF"}
    assert child.build_content_generation(plan_bytes=Poison(), expected_plan_sha256=Poison(),
                                          trusted_model_root=Poison(), trusted_git=Poison(),
                                          root=Poison()) == {"status": "OFF"}
    with pytest.raises(child.ContentBuildRefused, match="^request_refused$"):
        child.build_content_generation(enabled=1, plan_bytes=b"{}", expected_plan_sha256=sha(b"{}"),
                                       trusted_model_root="C:/x", root="C:/y")


def test_a_pinned_plan_builds_a_content_memory_on_the_fixed_encoder_path(setup):
    report = run(setup)
    assert set(report) == child.REPORT_KEYS and report["schema"] == child.REPORT_SCHEMA
    assert report["status"] == "BUILT" and report["name"] == "content-a"
    memory_dir = Path(report["memory_dir"])
    assert memory_dir == setup["out"] / "content-a"
    assert {p.name for p in memory_dir.iterdir()} >= {"descriptor.json", "memory.json", "projection.json"}
    projection = (memory_dir / "projection.json").read_bytes()
    assert report["projection_sha256"] == sha(projection) and report["projection_bytes"] == len(projection)
    descriptor = (memory_dir / "descriptor.json").read_bytes()
    assert report["descriptor_sha256"] == sha(descriptor)
    assert json.loads(descriptor)["memory_sha256"] == report["memory_sha256"]
    assert json.loads(descriptor)["encoder"] == report["encoder"] == {
        "model": encode_mod.MODEL, "revision": REVISION, "weights_sha256": WEIGHTS_SHA}
    assert json.loads(descriptor)["dim"] == encode_mod.DIM == 384
    assert report["document_count"] == len(json.loads(projection)["documents"]["records"]) >= 1
    assert report["records"] >= report["document_count"] + 1
    assert report["message_prefix_bytes"] == setup["route"]["prefix"]["offset"] > 0
    assert report["trusted_git_sha256"] == setup["trusted_git"]["git_sha256"]
    assert report["decision_sha256"] == setup["route"]["decision_sha"]
    assert report["plan_sha256"] == sha(enc(setup["plan"]))
    assert type(report["build_seconds"]) is float and report["build_seconds"] >= 0
    assert report["authority"] == "none" and report["publisher_authentication"] == "none"
    # the fixed E5 path ran: the fake backend was loaded offline and encoded the stored texts
    loads = [call for call in setup["backend"].calls if "path" in call]
    encodes = [call for call in setup["backend"].calls if "inputs" in call]
    assert loads and encodes and loads[0]["local_files_only"] is True
    assert loads[0]["env"].get("HF_HUB_OFFLINE") == "1"
    # the built memory re-derives from the same sources under the trusted pin, like a Spy-built one
    assert tcrd.validate(setup["route"], projection)["status"] == "MATCH"


def test_a_second_build_under_the_same_name_is_refused(setup):
    run(setup)
    with pytest.raises(child.ContentBuildRefused, match="^memory_exists$"):
        run(setup)
    assert run(setup, plan_with(setup, name="content-b"))["status"] == "BUILT"


@pytest.mark.parametrize("change", [
    {"schema": "wd.q4-prefix-build-plan.v1"}, {"schema": ...}, {"extra": 1}, {"name": "Content-A"}, {"name": ""},
    {"name": "a" * 65}, {"messages": None, "documents": None}, {"decision_path": "relative/decision.json"},
    {"decision_sha256": "0" * 63}, {"roster_sha256": "A" * 64}, {"messages": {"source_path": "C:/synthetic/log"}},
    {"documents": {"repo_root": "C:/synthetic/repo", "policy_path": "C:/synthetic/p.json", "git_executable": "C:/git.exe"}},
    {"documents": {"repo_root": "relative", "policy_path": "C:/synthetic/p.json"}},
])
def test_any_other_plan_shape_is_refused_before_anything_is_read(setup, monkeypatch, change):
    import builtins
    plan = plan_with(setup, **change)
    if "encoder" not in change:
        monkeypatch.setattr(encode_mod, "model_identity", lambda **k: pytest.fail("identity derived for a bad plan"))
    raw = enc(plan)
    monkeypatch.setattr(builtins, "open", lambda *a, **k: pytest.fail("a file was opened for a bad plan"))
    refused("plan_refused", setup, plan_bytes=raw)


@pytest.mark.parametrize("change", [
    lambda e: dict(e, dim=16), lambda e: dict(e, model_dir="relative/model"), lambda e: dict(e, revision="main"),
    lambda e: dict(e, weights_sha256=WEIGHTS_SHA.upper()), lambda e: dict(e, model="other-model"),
    lambda e: {k: v for k, v in e.items() if k != "assets"}, lambda e: dict(e, assets={}),
])
def test_an_encoder_record_other_than_the_fixed_one_is_refused(setup, monkeypatch, change):
    monkeypatch.setattr(encode_mod, "model_identity", lambda **k: pytest.fail("identity derived for a bad encoder"))
    refused("plan_refused", setup, plan_with(setup, encoder=change(setup["plan"]["encoder"])))


def test_the_plan_pin_and_encoding_are_checked_first(setup, monkeypatch):
    import builtins
    raw = enc(setup["plan"])
    monkeypatch.setattr(builtins, "open", lambda *a, **k: pytest.fail("a file was opened before the pin check"))
    refused("plan_refused", setup, plan_bytes=raw, plan_sha="0" * 64)
    refused("plan_refused", setup, plan_bytes=raw, plan_sha=sha(raw).upper())
    refused("plan_refused", setup, plan_bytes=raw + b"x")
    refused("plan_refused", setup, plan_bytes=raw.decode().replace("content-a", "content-" + chr(0xE4)).encode("utf-8"))
    refused("plan_refused", setup, plan_bytes=b"{" + raw[1:-1] + b',"name":"x"}')
    refused("plan_refused", setup, plan_bytes=b"[" + raw + b"]")
    refused("plan_refused", setup, plan_bytes=b"x" * (child.PLAN_LIMIT + 1))


@pytest.mark.parametrize("root, trusted", [
    ("relative/out", None), ("", None), (None, "relative/model"), (None, ""), (None, 7),
])
def test_relative_or_missing_roots_are_refused(setup, root, trusted):
    refused("request_refused", setup, root=root, trusted_model_root=trusted)


@pytest.mark.parametrize("trusted", [{"git_executable": "git", "git_sha256": "0" * 64}, {"git_sha256": "0" * 64},
                                     ("C:/git.exe", "0" * 64), "pin", 1])
def test_a_malformed_trusted_git_pin_is_refused_before_any_read(setup, monkeypatch, trusted):
    import builtins
    raw = enc(setup["plan"])
    monkeypatch.setattr(builtins, "open", lambda *a, **k: pytest.fail("a file was opened for a bad pin"))
    refused("request_refused", setup, plan_bytes=raw, trusted_git=trusted)


def test_a_document_plan_without_a_trusted_git_pin_refuses_before_any_subprocess_or_model_load(setup, monkeypatch):
    from sandbox.mind.step2 import shared_doc_projection as sdp
    monkeypatch.setattr(sdp.subprocess, "run", lambda *a, **k: pytest.fail("subprocess started"))
    monkeypatch.setattr(encode_mod, "model_identity", lambda **k: pytest.fail("identity derived without a pin"))
    monkeypatch.setattr(encode_mod, "E5", lambda **k: pytest.fail("backend loaded without a pin"))
    refused("git_pin_unavailable", setup, trusted_git=None)


def test_the_documents_run_the_trusted_git_and_nothing_else(setup, monkeypatch):
    """The plan has no Git field; a trusted pin naming another program (with its true digest) is what runs, and a
    program that is not Git refuses the build; a pin whose digest is not the file refuses before it starts."""
    from sandbox.mind.step2 import shared_doc_projection as sdp
    other = setup["files"] / "not-git.exe"
    other.write_bytes(b"not git")
    with pytest.raises(child.ContentBuildRefused, match="^repo_refused$"):
        run(setup, trusted_git={"git_executable": str(other), "git_sha256": sha(other.read_bytes())})
    started = []
    monkeypatch.setattr(sdp.subprocess, "run", lambda *a, **k: started.append(a) or pytest.fail("subprocess started"))
    with pytest.raises(child.ContentBuildRefused, match="^repo_refused$"):
        run(setup, trusted_git=dict(setup["trusted_git"], git_sha256="0" * 64))
    assert started == []


def test_a_message_only_decision_needs_no_trusted_git(setup, tmp_path):
    decision, decision_sha = tcr.decision_of(prefix=setup["route"]["prefix"],
                                             message_pin=setup["route"]["messages"]["policy_sha256"])
    decision_path, _ = write(setup["files"] / "decision-messages.json", decision)
    plan = plan_with(setup, decision_path=decision_path, decision_sha256=decision_sha, documents=None)
    report = run(setup, plan, trusted_git=None)
    assert report["status"] == "BUILT" and report["document_count"] == 0 and report["trusted_git_sha256"] is None
    assert report["message_prefix_bytes"] == setup["route"]["prefix"]["offset"]


def test_sources_other_than_the_decision_pins_are_refused(setup):
    refused("sources_refused", setup, plan_with(setup, decision_sha256="0" * 64))
    refused("sources_refused", setup, plan_with(setup, nonreview_policy_path=setup["plan"]["roster_path"]))
    refused("sources_refused", setup, plan_with(setup, documents=None))
    refused("sources_refused", setup, plan_with(setup, messages=None))
    other = dict(setup["plan"]["messages"], policy_path=setup["plan"]["documents"]["policy_path"])
    refused("sources_refused", setup, plan_with(setup, messages=other))
    missing = dict(setup["plan"]["documents"], policy_path=str(setup["files"] / "absent.json"))
    refused("sources_refused", setup, plan_with(setup, documents=missing))


def test_a_decision_for_other_strict_pins_is_refused(setup):
    other = tcr.strict_pins(tasks=(tcr.OTHER_TASK,))
    policy_path, _ = write(setup["files"] / "nonreview-other.json", other["policy_bytes"])
    with pytest.raises(child.ContentBuildRefused):
        run(setup, plan_with(setup, nonreview_policy_path=policy_path, nonreview_policy_sha256=other["policy_sha256"]))
    assert list(setup["out"].iterdir()) == []


def test_the_model_root_assets_identity_and_encoder_are_bound_like_the_prefix_child(setup, tmp_path, monkeypatch):
    refused("model_root_refused", setup, trusted_model_root=str(tmp_path / "elsewhere"))
    refused("model_root_refused", setup, root=str(setup["model_dir"]))
    stray = setup["model_dir"] / "extra.bin"
    stray.write_bytes(b"x")
    refused("assets_refused", setup)
    stray.unlink()
    refused("assets_refused", setup, plan_with(setup, encoder=dict(setup["plan"]["encoder"],
                                                                   assets={"model.safetensors": "0" * 64})))
    weights = setup["model_dir"] / "model.safetensors"
    original = weights.read_bytes()
    weights.write_bytes(original + b"!")
    plan = plan_with(setup, encoder=dict(setup["plan"]["encoder"], assets=assets_of(setup["model_dir"])))
    refused("identity_refused", setup, plan)
    weights.write_bytes(original)
    monkeypatch.setattr(encode_mod, "E5", lambda **k: (_ for _ in ()).throw(RuntimeError("synthetic")))
    refused("encoder_refused", setup)


def test_the_refusal_tokens_are_the_fixed_set():
    assert child.REASONS >= {"plan_refused", "request_refused", "sources_refused", "git_pin_unavailable",
                             "model_root_refused", "assets_refused", "identity_refused", "encoder_refused"}
    assert prefix.ENCODER_MODEL == encode_mod.MODEL and prefix.ENCODER_DIM == encode_mod.DIM
    assert child.PLAN_KEYS == {"schema", "name", "encoder", "decision_path", "decision_sha256", "nonreview_policy_path",
                               "nonreview_policy_sha256", "roster_path", "roster_sha256", "messages", "documents"}
    assert "git_executable" not in child.DOCUMENT_KEYS and "git_sha256" not in child.DOCUMENT_KEYS
    assert cm.DOCUMENT_KEYS >= {"git_executable", "git_sha256"}  # the builder's documents still carry the trusted values

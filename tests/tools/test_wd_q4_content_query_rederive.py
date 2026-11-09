"""Source re-derivation on the content query path (RCO2 MP1 R1, Lead source GO 2026-11-08 14:27Z).

Before this, the re-derivation ran only in the plan writer: ``query_content_snapshot`` served any memory whose pins
matched, so a plan written by hand could pair a genuine decision with a forged, consistently re-pinned projection.
Now the public child's query re-derives the projection from the decision's sources (exactly the anchored message
prefix, the pinned document commit through the pinned Git, the policies by the decision pins) before stdin is read,
and refuses without sources. Synthetic sources only; the reader runs in process as in the content fixture.
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
from sandbox.mind.step2 import test_content_rederive as tcrd
from sandbox.mind.step2 import test_content_route as tcr
from sandbox.mind.step2.shared_index_preflight import _encoder_digest
from tools import wd_q4_agent_query as q4
from tools import wd_q4_public_child as child
from tools import wd_q4_public_query as public

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("content_query_fixture_r", ROOT / "tests/tools/test_wd_q4_content_query.py")
cq = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cq)
content = cq.content

FAIL = {"schema": "wd.q4-content-snapshot-query.v1", "status": "REFUSED", "reason": "content_query_refused"}
FORGED = {"messages": "Vaarennetty viesti jota lahteessa ei koskaan ollut.",
          "documents": tcm.DOC_TEXT + " Vaarennetty lisays jota lahteessa ei ollut."}
SOURCE_KEYS = {"message_source_path", "message_generation_path", "message_policy_bytes", "doc_repo_root",
               "doc_policy_bytes", "doc_git_executable", "doc_git_sha256"}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def sources_of(route):
    messages, documents = route["messages"], route["documents"]
    return {"message_source_path": messages["source_path"], "message_generation_path": messages["generation_path"],
            "message_policy_bytes": messages["policy_bytes"], "doc_repo_root": documents["repo_root"],
            "doc_policy_bytes": documents["policy_bytes"], "doc_git_executable": documents["git_executable"],
            "doc_git_sha256": documents["git_sha256"]}


def memory_pins(memory_dir):
    descriptor = json.loads((memory_dir / "descriptor.json").read_bytes())
    manifest = json.loads((memory_dir / "memory.json").read_bytes())
    return {"expected_encoder_sha256": _encoder_digest(descriptor["encoder"]),
            "expected_memory_sha256": descriptor["memory_sha256"],
            "expected_projection_sha256": manifest["projection_sha256"]}


def pins(content, memory_dir=None):
    args = content["args"]
    return dict(memory_pins(memory_dir or content["dir"]), policy_bytes=args["policy_bytes"],
                policy_sha256=args["policy_sha256"], roster_bytes=args["roster_bytes"],
                roster_sha256=args["roster_sha256"], content_decision_bytes=args["content_decision_bytes"],
                content_decision_sha256=args["content_decision_sha256"])


class Stdin(io.BytesIO):
    """Records whether the query was read: every source check must run before it."""

    def __init__(self):
        super().__init__(json.dumps({"situation": cq.SITUATION, "time": tcm.ASK_TIME}).encode("utf-8"))
        self.reads = 0

    def read(self, *args):
        self.reads += 1
        return super().read(*args)


def run(content, config=None, memory_dir=None, **changes):
    kwargs = dict(pins(content, memory_dir), content_sources=sources_of(content["route"]),
                  trusted_git=tcrd.trusted_of(content["route"]), enabled=True)
    kwargs.update(changes)
    kwargs = {key: value for key, value in kwargs.items() if value is not ...}
    out, stdin = io.StringIO(), Stdin()
    code = child.run_content(config or content["config"], stdin, out, **kwargs)
    return code, json.loads(out.getvalue()), stdin.reads


@pytest.fixture
def forged(content, tmp_path, monkeypatch):
    """A hand-written plan's memory: one record's text replaced, refs recomputed, rebuilt with the same evidence and
    re-pinned end to end (descriptor, config). Every structural and pin check passes; the reader stub reads it."""
    from sandbox.mind.step2 import runtime_launcher
    from sandbox.mind.step2 import wd_q4_private_read as reader

    def make(part):
        projection = json.loads((content["dir"] / "projection.json").read_bytes())
        records = projection[part]["records"]
        index = next((i for i, r in enumerate(records) if cp.decode_text(r["stored_text"]) == tcm.DOC_TEXT), 0)
        records.insert(0, records.pop(index))  # forge the record the fixture query hits
        memory_dir, _ = tcrd.forged_memory(tmp_path / part, content["route"], tcrd.forge(projection, part, FORGED[part]))
        descriptor_sha256 = sha((memory_dir / "descriptor.json").read_bytes())
        config = cq.config(memory_dir, descriptor_sha256)

        def launcher(*, query, **kwargs):
            situation = json.loads(query)
            content["calls"].append(True)
            out = reader._private_read_locked(memory_dir, memory_dir / "descriptor.json", descriptor_sha256,
                                              situation["situation"], situation["time"], r"C:\fake\model",
                                              tcm.IDENTITY["revision"])
            return runtime_launcher._validate_output(json.dumps(out).encode("utf-8"), 0, descriptor_sha256)

        monkeypatch.setattr(q4, "_invoke_launcher", launcher)
        return config, memory_dir

    (tmp_path / "messages").mkdir()
    (tmp_path / "documents").mkdir()
    return make


# --- the bypass and its closure --------------------------------------------------------------------------------------

@pytest.mark.parametrize("part", ["messages", "documents"])
def test_a_hand_written_plan_with_a_forged_projection_no_longer_answers(content, forged, part):
    """RCO2 R1: without the query-path re-derivation this forged memory answered; now it refuses unread."""
    config, memory_dir = forged(part)
    assert run(content, config=config, memory_dir=memory_dir) == (2, FAIL, 0)
    assert run(content, config=config, memory_dir=memory_dir, content_sources=...) == (2, FAIL, 0)
    assert content["calls"] == []


def test_the_genuine_snapshot_answers_after_its_sources_re_derive(content):
    code, out, reads = run(content)
    assert code == 0 and out["status"] == "ANSWER" and reads > 0 and content["calls"] == [True]
    assert tcm.DOC_TEXT in [cp.decode_text(item["stored_text"]) for item in out["results"]]
    assert out["binding"]["projection_sha256"] == pins(content)["expected_projection_sha256"]


def test_without_sources_the_query_refuses_before_reading(content):
    assert run(content, content_sources=...) == (2, FAIL, 0)
    assert run(content, content_sources=None) == (2, FAIL, 0)
    assert content["calls"] == []


def test_the_query_function_itself_requires_the_sources(content):
    query = io.BytesIO(json.dumps({"situation": cq.SITUATION, "time": tcm.ASK_TIME}).encode("utf-8"))
    with pytest.raises(public.PublicQueryRefused, match="^content_query_refused$"):
        public.query_content_snapshot(content["config"], query, enabled=True, **pins(content))
    assert content["calls"] == []


@pytest.mark.parametrize("change", [
    lambda s, r: dict(s, extra=1),
    lambda s, r: {key: value for key, value in s.items() if key != "doc_git_sha256"},
    lambda s, r: dict(s, message_source_path=None),
    lambda s, r: dict(s, message_policy_bytes=tcr.message_policy(tasks=(tcr.OTHER_TASK,))[0]),
    lambda s, r: dict(s, doc_policy_bytes=s["message_policy_bytes"]),
    lambda s, r: dict(s, doc_repo_root=None, doc_policy_bytes=None, doc_git_executable=None, doc_git_sha256=None),
    lambda s, r: dict(s, doc_git_sha256="0" * 64),
    lambda s, r: dict(s, doc_git_executable="git"),
    lambda s, r: dict(s, message_source_path="events.jsonl"),
    lambda s, r: dict(s, message_generation_path=s["message_source_path"]),
    lambda s, r: dict(s, doc_repo_root=str(Path(s["doc_repo_root"]).parent)),
    lambda s, r: list(s.values()),
])
def test_sources_other_than_the_decision_pins_refuse_before_reading(content, change):
    sources = change(sources_of(content["route"]), content["route"])
    assert run(content, content_sources=sources) == (2, FAIL, 0)
    assert content["calls"] == []


def test_a_prefix_changed_after_the_build_refuses(content):
    path = Path(content["route"]["messages"]["source_path"])
    path.write_bytes(path.read_bytes().replace(b"valmiiksi", b"valmiixsi", 1))
    assert run(content) == (2, FAIL, 0)


def test_rows_appended_after_the_anchored_prefix_still_answer(content):
    with open(content["route"]["messages"]["source_path"], "ab") as stream:
        stream.write(tcr.raw(tcr.prefix_rows()[0] | {"ts_utc": "2026-11-07T10:09:00Z"}) + bytes([10]))
    assert run(content)[0] == 0


def test_the_re_derivation_gets_the_pinned_projection_bytes_and_the_decision_sources(content, monkeypatch):
    """Layer test: the query path hands the module check the exact pinned bytes and the decision's sources."""
    from sandbox.mind.step2 import content_memory as cm
    seen = []
    real = cm.validate_content_snapshot

    def spy(**kwargs):
        seen.append(kwargs)
        return real(**kwargs)

    monkeypatch.setattr(cm, "validate_content_snapshot", spy)
    assert run(content)[0] == 0
    (kwargs,) = seen
    projection = (content["dir"] / "projection.json").read_bytes()
    assert kwargs["enabled"] is True and kwargs["projection_bytes"] == projection
    assert kwargs["projection_sha256"] == sha(projection)
    assert kwargs["decision_bytes"] == content["args"]["content_decision_bytes"]
    route = content["route"]
    assert kwargs["documents"] == route["documents"]
    assert {key: kwargs["messages"][key] for key in ("source_path", "generation_path", "policy_bytes",
                                                    "policy_sha256", "end_cursor")} == {
        key: route["messages"][key] for key in ("source_path", "generation_path", "policy_bytes", "policy_sha256",
                                                "end_cursor")}


def test_the_child_forwards_the_sources_unchanged(content, monkeypatch):
    seen = []
    real = public.query_content_snapshot

    def spy(*args, **kwargs):
        seen.append(kwargs["content_sources"])
        return real(*args, **kwargs)

    monkeypatch.setattr(public, "query_content_snapshot", spy)
    assert run(content)[0] == 0
    assert seen == [sources_of(content["route"])]


# --- RCO1 Q1: the Git that runs is the trusted fleet pin, never the plan's -------------------------------------------

@pytest.fixture
def no_subprocess(monkeypatch):
    from sandbox.mind.step2 import shared_doc_projection as sdp
    monkeypatch.setattr(sdp.subprocess, "run", lambda *a, **k: pytest.fail("subprocess started"))


def test_without_a_trusted_git_pin_a_document_decision_refuses_before_any_subprocess(content, no_subprocess):
    """Fail closed: the fleet record has no Git digest yet, so the deployed wrapper passes None and documents refuse."""
    assert run(content, trusted_git=...) == (2, FAIL, 0)
    assert run(content, trusted_git=None) == (2, FAIL, 0)
    assert content["calls"] == []


def test_an_attacker_chosen_program_with_its_true_digest_in_the_plan_refuses_before_any_subprocess(
        content, tmp_path, no_subprocess):
    """A hand-written plan may name any program and its true digest; only the trusted pin may run, so it refuses."""
    evil = tmp_path / "evil-git.exe"
    evil.write_bytes(b"not git")
    sources = dict(sources_of(content["route"]), doc_git_executable=str(evil), doc_git_sha256=sha(evil.read_bytes()))
    assert run(content, content_sources=sources) == (2, FAIL, 0)
    assert content["calls"] == []


@pytest.mark.parametrize("trusted", [{"git_sha256": "0" * 64}, {"git_executable": "git"}, {"git_sha256": "A" * 64},
                                     {"extra": 1}, "pin", 1])
def test_a_trusted_pin_that_does_not_match_the_plan_or_is_malformed_refuses(content, no_subprocess, trusted):
    value = trusted if type(trusted) is not dict else dict(tcrd.trusted_of(content["route"]), **trusted)
    assert run(content, trusted_git=value) == (2, FAIL, 0)
    assert content["calls"] == []


def test_the_trusted_pin_reaches_the_module_check_and_the_child_unchanged(content, monkeypatch):
    from sandbox.mind.step2 import content_memory as cm
    seen = []
    real = cm.snapshot_sources

    def spy(decision, sources, trusted_git=None):
        seen.append(trusted_git)
        return real(decision, sources, trusted_git)

    monkeypatch.setattr(cm, "snapshot_sources", spy)
    code, out, _ = run(content)
    assert code == 0 and out["status"] == "ANSWER" and seen == [tcrd.trusted_of(content["route"])]


def test_a_config_for_another_memory_still_refuses(content):
    config = dataclasses.replace(content["config"], descriptor_sha256="0" * 64)
    assert run(content, config=config) == (2, FAIL, 0)


@pytest.fixture
def exact_doc_v2_query(tmp_path, monkeypatch):
    """Synthetic Git, prefix and index; the launcher is an in-process reader, never a real runtime."""
    import datetime as dt
    from sandbox.mind.step2 import runtime_launcher, wd_q4_private_read as reader
    pytest.importorskip("faiss")
    baseline = tcr.route.__wrapped__(tmp_path)
    route, (allowed, excluded, blob) = tcr.doc_v2_route(tmp_path, baseline)
    built, memory_dir = tcr.build_route(tmp_path, route)
    descriptor_sha256 = built["descriptor_sha256"]
    monkeypatch.setattr(reader, "ENCODER_FACTORY", lambda **kw: tcm.Encoder())
    monkeypatch.setattr(reader, "bind_runtime_encoder", lambda descriptor, model_dir: None)
    calls = []
    def launcher(*, query, **kwargs):
        situation = json.loads(query)
        calls.append(True)
        out = reader._private_read_locked(memory_dir, memory_dir / "descriptor.json", descriptor_sha256,
                                          situation["situation"], situation["time"], r"C:\fake\model",
                                          tcm.IDENTITY["revision"])
        return runtime_launcher._validate_output(json.dumps(out).encode("utf-8"), 0, descriptor_sha256)
    monkeypatch.setattr(q4, "_invoke_launcher", launcher)
    monkeypatch.setattr(q4, "_utc_now", lambda: dt.datetime(2026, 11, 8, 1, tzinfo=dt.timezone.utc))
    kwargs = dict(memory_pins(memory_dir), **tcr.strict_pins(),
                  content_decision_bytes=route["decision"], content_decision_sha256=route["decision_sha"],
                  content_sources=sources_of(route), trusted_git=tcrd.trusted_of(route), enabled=True)
    return {"route": route, "memory": memory_dir, "config": cq.config(memory_dir, descriptor_sha256),
            "kwargs": kwargs, "allowed": allowed, "excluded": excluded, "blob": blob, "calls": calls}


def v2_stdin():
    stdin = Stdin()
    stdin.seek(0)
    stdin.truncate()
    stdin.write(json.dumps({"situation": "what does reviewed documentation discuss", "time": tcm.ASK_TIME}).encode("utf-8"))
    stdin.seek(0)
    return stdin


def test_doc_v2_query_rederives_exact_document_and_preserves_message_policy(exact_doc_v2_query):
    fixture = exact_doc_v2_query
    original_policy = fixture["route"]["messages"]["policy_bytes"]
    stdin = v2_stdin()
    answer = public.query_content_snapshot(fixture["config"], stdin, **fixture["kwargs"])
    assert answer["status"] == "ANSWER" and stdin.reads > 0 and fixture["calls"] == [True]
    texts = [cp.decode_text(item["stored_text"]) for item in answer["results"]]
    assert "Synthetic reviewed documentation." in texts
    assert "Synthetic unapproved documentation." not in texts
    assert answer["freshness"] == "UNKNOWN" and answer["publisher_authentication"] == "UNKNOWN"
    assert fixture["route"]["messages"]["policy_bytes"] == original_policy
    assert fixture["kwargs"]["content_sources"]["message_policy_bytes"] == original_policy
    projection = json.loads((fixture["memory"] / "projection.json").read_bytes())
    assert {item["provenance"]["path"] for item in projection["documents"]["records"]} == {fixture["allowed"]}
    assert all(item["provenance"]["blob_id"] == fixture["blob"] and
               item["provenance"]["commit"] == fixture["route"]["commit"]
               for item in projection["documents"]["records"])


@pytest.mark.parametrize("fault", ["blob_bytes", "policy_bytes", "git_pin", "projection_pin"])
def test_doc_v2_query_refuses_changed_blob_or_independent_pin_before_query(exact_doc_v2_query, monkeypatch, fault):
    from sandbox.mind.step2 import shared_doc_projection as doc
    fixture = exact_doc_v2_query
    kwargs = dict(fixture["kwargs"])
    calls = []
    if fault == "blob_bytes":
        original = doc._git
        def altered(root, env, *args):
            value = original(root, env, *args)
            if len(args) >= 2 and args[:2] == ("cat-file", "blob"):
                calls.append(True)
                return bytes([value[0] ^ 1]) + value[1:]
            return value
        monkeypatch.setattr(doc, "_git", altered)
    elif fault == "policy_bytes":
        source = dict(kwargs["content_sources"])
        source["doc_policy_bytes"] += b" "
        kwargs["content_sources"] = source
    elif fault == "git_pin":
        source = dict(kwargs["content_sources"])
        source["doc_git_sha256"] = "0" * 64
        kwargs["content_sources"] = source
    else:
        kwargs["expected_projection_sha256"] = "0" * 64
    stdin = v2_stdin()
    with pytest.raises(public.PublicQueryRefused, match="^content_query_refused$"):
        public.query_content_snapshot(fixture["config"], stdin, **kwargs)
    assert stdin.reads == 0 and fixture["calls"] == []
    if fault == "blob_bytes":
        assert calls == [True]  # the synthetic object mismatch, not an earlier unrelated failure

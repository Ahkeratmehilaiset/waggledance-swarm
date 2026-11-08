"""The default-off v4 content plan writer (lead-q4-useful-work-content-fable-20261107-v1, increment 7b4).

Route B, Lead 20:06Z and 21:30Z. ``write_content_plan`` turns a caller-pinned v1 public plan, a pinned content route
decision and an installed content memory directory into ``public-plan.v4.json``; every check the content query makes
before stdin runs here first, so a written plan is one the lane caller answers through. Synthetic data; the lane
transport stands in for the wrapper and calls the real public child ``run_content`` in process.
"""
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
from tools import wd_q4_lane_link as link
from tools import wd_q4_lane_query as lane_query

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("content_lane_fixture_w",
                                               ROOT / "tests/tools/test_wd_q4_content_lane_query.py")
cl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cl)
content = cl.content
lane = cl.lane

V1 = "wd.q4-public-wrapper-plan.v1"
SOURCE_KEYS = ("message_source_path", "message_generation_path", "message_policy_path", "doc_repo_root",
               "doc_policy_path")
V4_KEYS = {"schema", "config", "policy_path", "roster_path", "content_decision_path", "policy_sha256",
           "roster_sha256", "content_decision_sha256", "encoder_sha256", "memory_sha256", "projection_sha256"}


def writer():
    from tools import wd_q4_public_content_plan_writer as module
    return module


def sha(data):
    return hashlib.sha256(data).hexdigest()


def write(path, data):
    path.write_bytes(data)
    return str(path), sha(data)


@pytest.fixture
def setup(lane, tmp_path):
    """A v1 plan over the lane fixture's policy and roster files, the decision file and an empty output directory."""
    document = lane["document"]
    files = tmp_path / "content-plan-writer"
    files.mkdir()
    v1 = {"schema": V1, "root": str(files), "config": dict(document["config"]),
          "policy_path": document["policy_path"], "roster_path": document["roster_path"],
          "public_policy_path": str(files / "public-policy.json"), "pointer_sha256": "1" * 64,
          "source_anchor_sha256": "2" * 64, "encoder_sha256": document["encoder_sha256"],
          "policy_sha256": document["policy_sha256"], "roster_sha256": document["roster_sha256"],
          "public_policy_sha256": "3" * 64}
    plan_path, plan_sha = write(files / "plan.v1.json", json.dumps(v1, sort_keys=True).encode())
    out = tmp_path / "content-plan-out"
    out.mkdir()
    route = lane["content"]["route"]
    message_policy, _ = write(files / "message-policy.json", route["messages"]["policy_bytes"])
    doc_policy, _ = write(files / "doc-policy.json", route["documents"]["policy_bytes"])
    arguments = {"public_plan_path": plan_path, "expected_public_plan_sha256": plan_sha, "output_dir": str(out),
                 "memory_dir": str(lane["content"]["dir"]), "content_decision_path": document["content_decision_path"],
                 "content_decision_sha256": document["content_decision_sha256"],
                 "message_source_path": route["messages"]["source_path"],
                 "message_generation_path": route["messages"]["generation_path"],
                 "message_policy_path": message_policy, "doc_repo_root": route["documents"]["repo_root"],
                 "doc_policy_path": doc_policy}
    return {"arguments": arguments, "out": out, "files": files, "v1": v1, "lane": lane, "document": document,
            "route": route}


def refused(setup, **changes):
    module = writer()
    with pytest.raises(module.ContentPlanRefused, match="^content_plan_refused$"):
        module.write_content_plan(enabled=True, **dict(setup["arguments"], **changes))
    assert list(setup["out"].iterdir()) == []


def test_the_writer_is_off_by_default_and_reads_nothing(setup):
    module = writer()
    assert module.write_content_plan() == {"status": "OFF"}
    assert module.write_content_plan(**setup["arguments"]) == {"status": "OFF"}
    assert list(setup["out"].iterdir()) == []
    with pytest.raises(module.ContentPlanRefused):
        module.write_content_plan(enabled="yes", **setup["arguments"])


def test_the_written_v4_plan_carries_the_seven_pins(setup):
    module = writer()
    result = module.write_content_plan(enabled=True, **setup["arguments"])
    plan_path = setup["out"] / "public-plan.v4.json"
    assert [item.name for item in setup["out"].iterdir()] == ["public-plan.v4.json"]
    raw = plan_path.read_bytes()
    plan = json.loads(raw)
    assert set(plan) == V4_KEYS and plan["schema"] == "wd.q4-public-wrapper-plan.v4"
    assert plan["config"] == setup["v1"]["config"]
    assert {key: plan[key] for key in V4_KEYS - {"schema", "config"}} == {
        key: setup["document"][key] for key in V4_KEYS - {"schema", "config"}}
    assert link._public_plan(str(plan_path), sha(raw))[2] == setup["lane"]["call"]["expected_binding"]
    assert result == {"schema": "wd.q4-public-content-plan-written.v1", "status": "WRITTEN",
                      "base_public_plan_sha256": setup["arguments"]["expected_public_plan_sha256"],
                      "public_plan_path": str(plan_path), "public_plan_sha256": sha(raw),
                      "content_decision_sha256": plan["content_decision_sha256"],
                      "memory_sha256": plan["memory_sha256"], "projection_sha256": plan["projection_sha256"],
                      "source_rederivation": "MATCH", "authority": "none", "issuer_authentication": "UNKNOWN"}


def test_the_lane_caller_answers_through_the_written_plan(setup):
    result = writer().write_content_plan(enabled=True, **setup["arguments"])
    call = dict(setup["lane"]["call"], public_plan_path=result["public_plan_path"],
                expected_public_plan_sha256=result["public_plan_sha256"])
    answer = lane_query.query_public(**call)
    assert answer["status"] == "ANSWER" and len(setup["lane"]["calls"]) == 1
    assert tcm.DOC_TEXT in [cp.decode_text(item["stored_text"]) for item in answer["results"]]


def test_a_plan_other_than_a_pinned_v1_plan_is_refused(setup):
    refused(setup, expected_public_plan_sha256="0" * 64)
    v2 = dict(setup["v1"], schema="wd.q4-public-wrapper-plan.v2", card_decision_path="x",
              card_decision_sha256="4" * 64)
    path, pin = write(setup["files"] / "plan.v2.json", json.dumps(v2, sort_keys=True).encode())
    refused(setup, public_plan_path=path, expected_public_plan_sha256=pin)


@pytest.mark.parametrize("change", [{"policy_sha256": "5" * 64}, {"roster_sha256": "6" * 64},
                                    {"encoder_sha256": "7" * 64}, {"config": {"descriptor_sha256": "8" * 64}}])
def test_a_v1_plan_whose_pins_are_not_the_installed_ones_is_refused(setup, change):
    path, pin = write(setup["files"] / "plan.other.json", json.dumps(dict(setup["v1"], **change), sort_keys=True).encode())
    refused(setup, public_plan_path=path, expected_public_plan_sha256=pin)


def test_a_decision_other_than_the_pinned_verified_one_is_refused(setup):
    refused(setup, content_decision_sha256="0" * 64)
    other, other_sha = tcr.decision_of(prefix=None, message_pin=None, doc_pin="c" * 64,
                                       nonreview_policy_sha256="d" * 64)
    path, pin = write(setup["files"] / "decision.other.bin", other)
    refused(setup, content_decision_path=path, content_decision_sha256=pin)


def test_a_verified_decision_the_memory_was_not_built_with_is_refused(setup):
    other, other_sha = tcr.decision_of(prefix=None, message_pin=None, doc_pin="c" * 64)
    path, pin = write(setup["files"] / "decision.docs-only.bin", other)
    refused(setup, content_decision_path=path, content_decision_sha256=pin)


@pytest.mark.parametrize("name", ["descriptor.json", "memory.json", "projection.json"])
def test_a_changed_memory_artifact_is_refused(setup, name):
    target = Path(setup["arguments"]["memory_dir"]) / name
    target.write_bytes(target.read_bytes() + b" ")
    refused(setup)


def test_a_memory_directory_other_than_the_installed_one_is_refused(setup):
    refused(setup, memory_dir=str(setup["files"]))
    refused(setup, memory_dir="relative/memory")


def test_the_output_directory_must_exist_be_absolute_and_be_empty(setup):
    (setup["out"] / "other.txt").write_bytes(b"x")
    module = writer()
    with pytest.raises(module.ContentPlanRefused):
        module.write_content_plan(enabled=True, **setup["arguments"])
    assert [item.name for item in setup["out"].iterdir()] == ["other.txt"]
    with pytest.raises(module.ContentPlanRefused):
        module.write_content_plan(enabled=True, **dict(setup["arguments"], output_dir=str(setup["files"] / "none")))
    assert not (setup["files"] / "none").exists()
    with pytest.raises(module.ContentPlanRefused):
        module.write_content_plan(enabled=True, **dict(setup["arguments"], output_dir="out"))


def test_a_failure_after_the_write_began_is_unknown(setup, monkeypatch):
    module = writer()

    def failing(path, raw):
        Path(path).write_bytes(raw[:1])
        raise OSError()

    monkeypatch.setattr(module, "_exclusive", failing)
    with pytest.raises(module.ContentPlanUnknown, match="^content_plan_unknown$"):
        module.write_content_plan(enabled=True, **setup["arguments"])


def request(setup, **changes):
    document = {"schema": "wd.q4-public-content-plan-request.v2",
                "public_plan_path": setup["arguments"]["public_plan_path"],
                "public_plan_sha256": setup["arguments"]["expected_public_plan_sha256"],
                "output_dir": setup["arguments"]["output_dir"], "memory_dir": setup["arguments"]["memory_dir"],
                "content_decision_path": setup["arguments"]["content_decision_path"],
                "content_decision_sha256": setup["arguments"]["content_decision_sha256"],
                **{key: setup["arguments"][key] for key in SOURCE_KEYS}}
    document.update(changes)
    document = {key: value for key, value in document.items() if value is not ...}
    return write(setup["files"] / "request.json", json.dumps(document, sort_keys=True).encode())


def test_the_command_line_is_off_without_arguments_and_writes_through_a_pinned_request(setup, capsys):
    module = writer()
    assert module.main([]) == 0 and json.loads(capsys.readouterr().out) == {"status": "OFF"}
    path, pin = request(setup)
    assert module.main(["--enabled", "--request", path, "--request-sha256", pin, "--force"]) == 2
    assert capsys.readouterr().err == '{"error":"content_plan_refused"}\n' and list(setup["out"].iterdir()) == []
    assert module.main(["--enabled", "--request", path, "--request-sha256", pin]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "WRITTEN" and Path(out["public_plan_path"]).is_file()


@pytest.mark.parametrize("changes", [
    {"memory_dir": ...}, {"extra": "x"}, {"schema": "wd.q4-public-card-plan-request.v1"},
    {"schema": "wd.q4-public-content-plan-request.v1"}, {"doc_policy_path": ...},
    dict({key: ... for key in SOURCE_KEYS}, schema="wd.q4-public-content-plan-request.v1"),
])
def test_the_command_line_refuses_any_other_request_with_one_fixed_line(setup, capsys, changes):
    module = writer()
    path, pin = request(setup, **changes)
    assert module.main(["--enabled", "--request", path, "--request-sha256", pin]) == 2
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == '{"error":"content_plan_refused"}\n'
    assert module.main(["--enabled", "--request", path, "--request-sha256", "0" * 64]) == 2
    assert list(setup["out"].iterdir()) == []


def rechain(setup, *, descriptor=None, manifest=None, projection=None):
    """Re-pin a changed memory chain (projection -> manifest -> descriptor -> v1 plan); returns plan path and pin."""
    directory = Path(setup["arguments"]["memory_dir"])
    loaded = {name: json.loads((directory / name).read_bytes())
              for name in ("descriptor.json", "memory.json", "projection.json")}
    for name, change in (("projection.json", projection), ("memory.json", manifest), ("descriptor.json", descriptor)):
        if change is not None:
            change(loaded[name])
    data = json.dumps(loaded["projection.json"], sort_keys=True, indent=1).encode("utf-8")
    (directory / "projection.json").write_bytes(data)
    loaded["memory.json"]["projection_sha256"] = sha(data)
    data = json.dumps(loaded["memory.json"], sort_keys=True).encode("utf-8")
    (directory / "memory.json").write_bytes(data)
    loaded["descriptor.json"]["memory_sha256"] = sha(data)
    data = json.dumps(loaded["descriptor.json"], sort_keys=True).encode("utf-8")
    (directory / "descriptor.json").write_bytes(data)
    v1 = dict(setup["v1"], config=dict(setup["v1"]["config"], descriptor_sha256=sha(data)))
    return write(setup["files"] / "plan.rechained.json", json.dumps(v1, sort_keys=True).encode())


def test_a_consistently_re_pinned_unchanged_chain_is_written(setup):
    path, pin = rechain(setup)
    assert writer().write_content_plan(enabled=True, **dict(setup["arguments"], public_plan_path=path,
                                                            expected_public_plan_sha256=pin))["status"] == "WRITTEN"


@pytest.mark.parametrize("change", [
    lambda d: d.update(schema="wd.bridge-mind-q4-memory-descriptor.v1"),
    lambda d: d.update(scope="wd.q4-other-scope.v1"),
    lambda d: d.update(dim=0),
    lambda d: d.update(extra=1),
])
def test_a_descriptor_other_than_the_v2_shared_one_is_refused(setup, change):
    path, pin = rechain(setup, descriptor=change)
    refused(setup, public_plan_path=path, expected_public_plan_sha256=pin)


def test_a_manifest_naming_another_projection_schema_is_refused(setup):
    path, pin = rechain(setup, manifest=lambda m: m.update(projection_schema="wd.mind-card-projection.v1"))
    refused(setup, public_plan_path=path, expected_public_plan_sha256=pin)


def test_a_re_pinned_projection_that_does_not_re_derive_is_refused(setup):
    path, pin = rechain(setup, projection=lambda p: p["documents"]["records"][0].update(
        stored_text=cp.encode_text("vaihdettu")))
    refused(setup, public_plan_path=path, expected_public_plan_sha256=pin)


@pytest.mark.parametrize("part", ["messages", "documents"])
def test_a_forged_row_that_passes_every_structural_check_is_refused(setup, part):
    """RCO2 MP1: a record whose text never occurred in the sources, every self-derived field recomputed and the
    whole chain re-pinned, passes the structural checks; only the source re-derivation refuses it."""
    path, pin = rechain(setup, projection=lambda p: p.update(tcrd.forge(p, part)))
    refused(setup, public_plan_path=path, expected_public_plan_sha256=pin)


def test_the_sources_are_the_ones_the_decision_pins(setup):
    """Present exactly when the decision pins them; the policies are read by the decision pins; the message source
    is exactly the anchored prefix; the documents come from a repository holding the pinned commit."""
    refused(setup, **{key: None for key in SOURCE_KEYS})
    refused(setup, message_source_path=None)
    refused(setup, doc_repo_root=None, doc_policy_path=None)
    refused(setup, message_policy_path=setup["arguments"]["doc_policy_path"])
    refused(setup, doc_policy_path=setup["arguments"]["message_policy_path"])
    refused(setup, message_generation_path=setup["arguments"]["message_policy_path"])
    refused(setup, doc_repo_root=str(setup["files"]))
    refused(setup, message_source_path="relative/events.jsonl")
    with pytest.raises(TypeError):
        writer().write_content_plan(enabled=True, **{key: value for key, value in setup["arguments"].items()
                                                      if key != "doc_repo_root"}, repo_root="x")


@pytest.mark.parametrize("key", SOURCE_KEYS)
def test_a_source_the_decision_does_not_pin_is_refused(key):
    """Unit layer: a decision with neither pin takes no source at all."""
    sources = dict.fromkeys(SOURCE_KEYS)
    decision = {"message_policy_sha256": None, "doc_policy_sha256": None, "source_prefix": None}
    assert writer()._route_sources(decision, sources) == (None, None)
    with pytest.raises(ValueError):
        writer()._route_sources(decision, dict(sources, **{key: "C:\\synthetic"}))


def test_a_message_prefix_changed_after_the_build_is_refused(setup):
    path = Path(setup["arguments"]["message_source_path"])
    path.write_bytes(path.read_bytes().replace(b"valmiiksi", b"valmiixsi", 1))
    refused(setup)


def test_rows_appended_after_the_anchored_prefix_still_write(setup):
    with open(setup["arguments"]["message_source_path"], "ab") as stream:
        stream.write(tcr.raw(tcr.prefix_rows()[0] | {"ts_utc": "2026-11-07T10:09:00Z"}) + bytes([10]))
    assert writer().write_content_plan(enabled=True, **setup["arguments"])["status"] == "WRITTEN"


def test_the_source_re_derivation_runs_on_the_pinned_projection_bytes(setup, monkeypatch):
    """Layer test: the writer hands the module check the raw pinned bytes, the plan pins and the decision sources."""
    from sandbox.mind.step2 import content_memory as cm
    seen = []
    real = cm.validate_content_snapshot

    def spy(**kwargs):
        seen.append(kwargs)
        return real(**kwargs)

    monkeypatch.setattr(cm, "validate_content_snapshot", spy)
    writer().write_content_plan(enabled=True, **setup["arguments"])
    (kwargs,) = seen
    projection = (Path(setup["arguments"]["memory_dir"]) / "projection.json").read_bytes()
    assert kwargs["enabled"] is True and kwargs["projection_bytes"] == projection
    assert kwargs["projection_sha256"] == sha(projection)
    assert kwargs["decision_sha256"] == setup["arguments"]["content_decision_sha256"]
    assert kwargs["policy_sha256"] == setup["v1"]["policy_sha256"]
    assert kwargs["roster_sha256"] == setup["v1"]["roster_sha256"]
    messages, documents = kwargs["messages"], kwargs["documents"]
    route = setup["route"]["messages"]
    assert {key: messages[key] for key in ("source_path", "generation_path", "policy_bytes", "policy_sha256")} == {
        key: route[key] for key in ("source_path", "generation_path", "policy_bytes", "policy_sha256")}
    assert messages["end_cursor"] == route["end_cursor"]
    assert documents == setup["route"]["documents"]


def test_a_valid_policy_other_than_the_decision_policy_is_refused(setup):
    other = tcr.strict_pins(tasks=(tcr.OTHER_TASK,))
    policy_path, policy_sha = write(setup["files"] / "policy.other.json", other["policy_bytes"])
    v1 = dict(setup["v1"], policy_path=policy_path, policy_sha256=policy_sha)
    path, pin = write(setup["files"] / "plan.policy.json", json.dumps(v1, sort_keys=True).encode())
    refused(setup, public_plan_path=path, expected_public_plan_sha256=pin)


def test_the_policy_and_roster_must_pass_the_strict_adapter(setup, monkeypatch):
    """Layer test: the strict adapter refusal alone refuses the plan."""
    from sandbox.mind.step2 import shared_capture_adapter as adapter
    seen = []

    def strict(*args):
        seen.append(len(args))
        raise ValueError()

    monkeypatch.setattr(adapter, "strict_pins", strict)
    refused(setup)
    assert seen == [4]

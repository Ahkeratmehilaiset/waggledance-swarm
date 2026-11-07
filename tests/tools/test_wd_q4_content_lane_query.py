"""Content snapshot plan v4 through the lane seam and the lane caller (lead-q4-useful-work-content-fable-20261107-v1, 7b3).

Route B, Lead 20:06Z. ``wd_q4_lane_link._public_plan`` reads a v4 plan into its seven pins, ``_public_result`` accepts
only the closed content reply carrying them, and ``wd_q4_lane_query.query_public`` in content mode checks the caller's
own pins, the pinned content decision and the caller-pinned content projection (re-derived, non-review roster lanes,
approved tasks) before the transport, then resolves every result to one visible record with identical text. Synthetic
data; the transport stands in for the wrapper and calls the real public child ``run_content`` in process.
"""
import dataclasses
import hashlib
import importlib.util
import io
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from sandbox.mind.step2 import shared_content_projection as cp
from sandbox.mind.step2 import test_content_memory as tcm
from sandbox.mind.step2 import test_content_route as tcr
from sandbox.mind.step2.shared_index_preflight import _encoder_digest
from tools import wd_q4_lane_link as link
from tools import wd_q4_lane_query as lane_query
from tools import wd_q4_public_child as child

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("content_query_fixture_l", ROOT / "tests/tools/test_wd_q4_content_query.py")
cq = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cq)
content = cq.content

PLAN_SCHEMA = "wd.q4-public-wrapper-plan.v4"
WHEN = datetime(2026, 11, 8, tzinfo=timezone.utc)
PINS = ("encoder_sha256", "policy_sha256", "roster_sha256", "content_decision_sha256", "memory_sha256",
        "projection_sha256")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def write(path, data):
    path.write_bytes(data)
    return str(path), sha(data)


def plan_document(content, files, **changes):
    args = content["args"]
    descriptor = json.loads((content["dir"] / "descriptor.json").read_bytes())
    document = {"schema": PLAN_SCHEMA, "config": {"descriptor_sha256": content["config"].descriptor_sha256},
                "encoder_sha256": _encoder_digest(descriptor["encoder"]),
                "memory_sha256": args["expected_memory_sha256"], "projection_sha256": sha(args["projection_bytes"])}
    for name, data in (("policy", args["policy_bytes"]), ("roster", args["roster_bytes"]),
                       ("content_decision", args["content_decision_bytes"])):
        document[name + "_path"], document[name + "_sha256"] = write(files / (name + ".bin"), data)
    document.update(changes)
    return {key: value for key, value in document.items() if value is not ...}


@pytest.fixture
def lane(content, tmp_path):
    files = tmp_path / "lane"
    files.mkdir()
    document = plan_document(content, files)
    plan_path, plan_sha = write(files / "plan.json", json.dumps(document, sort_keys=True).encode())
    bundle = tmp_path / ("e" * 40)
    bundle.mkdir()
    wrapper = bundle / "Invoke-WdQ4Query.ps1"
    wrapper.write_bytes(b"synthetic wrapper stand-in; the transport below runs in process")
    manifest = json.dumps({"schema_version": 1, "source_commit": "e" * 40,
                           "files": {"Invoke-WdQ4Query.ps1": sha(wrapper.read_bytes())}}, sort_keys=True).encode()
    (bundle / "deployment-manifest.json").write_bytes(manifest)
    calls = []

    def transport(*, wrapper, arguments, stdin_bytes):
        """What the wrapper does for a v4 plan: read the pinned plan and inputs, call run_content once."""
        calls.append(arguments)
        plan = json.loads(Path(arguments[arguments.index("-PublicPlanPath") + 1]).read_bytes())
        out = io.StringIO()
        code = child.run_content(content["config"], io.BytesIO(stdin_bytes), out,
                                 expected_encoder_sha256=plan["encoder_sha256"],
                                 policy_bytes=Path(plan["policy_path"]).read_bytes(), policy_sha256=plan["policy_sha256"],
                                 roster_bytes=Path(plan["roster_path"]).read_bytes(), roster_sha256=plan["roster_sha256"],
                                 content_decision_bytes=Path(plan["content_decision_path"]).read_bytes(),
                                 content_decision_sha256=plan["content_decision_sha256"],
                                 expected_memory_sha256=plan["memory_sha256"],
                                 expected_projection_sha256=plan["projection_sha256"], enabled=True)
        return out.getvalue().encode("utf-8"), code

    binding = {key: document[key] for key in PINS}
    binding["descriptor_sha256"] = content["config"].descriptor_sha256
    args = content["args"]
    call = {"lane": "fable-5", "situation": cq.SITUATION, "time": WHEN, "bundle_root": bundle,
            "expected_commit": "e" * 40, "expected_manifest_sha256": sha(manifest),
            "expected_wrapper_sha256": sha(wrapper.read_bytes()), "public_plan_path": plan_path,
            "expected_public_plan_sha256": plan_sha, "expected_binding": binding,
            "projection_bytes": args["projection_bytes"], "policy_bytes": args["policy_bytes"],
            "roster_bytes": args["roster_bytes"], "transport": transport, "enabled": True}
    return {"call": call, "calls": calls, "document": document, "files": files, "content": content}


def refused(reason, call):
    with pytest.raises(lane_query.LaneQueryRefused, match="^" + reason + "$"):
        lane_query.query_public(**call)


# --- the seam ---------------------------------------------------------------------------------------------------------

def test_a_v4_plan_reads_into_its_seven_pins(lane):
    path, raw, pins = link._public_plan(lane["call"]["public_plan_path"], lane["call"]["expected_public_plan_sha256"])
    assert pins == lane["call"]["expected_binding"]


@pytest.mark.parametrize("change", [
    {"root": "C:\\public"}, {"memory_sha256": ...}, {"content_decision_path": ""}, {"config": "x"},
    {"projection_sha256": "X" * 64}, {"card_decision_sha256": "9" * 64}, {"public_policy_sha256": "0" * 64},
])
def test_a_partial_or_extended_v4_plan_is_refused(lane, change):
    document = plan_document(lane["content"], lane["files"], **change)
    path, pin = write(lane["files"] / "other-plan.json", json.dumps(document, sort_keys=True).encode())
    with pytest.raises(ValueError):
        link._public_plan(path, pin)


def content_reply(pins, **changes):
    value = {"schema": "wd.q4-content-snapshot-query.v1", "status": "ANSWER",
             "binding": dict(pins, source_mode="content-snapshot-v1"),
             "results": [{"stored_text": "synthetic", "ref_sha256": "5" * 64, "doc_id_sha256": "6" * 64,
                          "ref_kind": "sha256_of_projected_metadata", "canonical_event": False}],
             "publisher_authentication": "UNKNOWN", "source_filter_attestation": "UNKNOWN", "freshness": "UNKNOWN",
             "same_user_aba": "UNKNOWN", "completeness": "UNKNOWN"}
    value.update(changes)
    return value


def test_the_seam_accepts_only_the_closed_content_reply(lane):
    pins = lane["call"]["expected_binding"]
    good = content_reply(pins)
    assert link._public_result(json.dumps(good).encode(), 0, pins)["status"] == "ANSWER"
    bad = [content_reply(pins, schema="wd.q4-card-snapshot-query.v1"),
           content_reply(pins, binding=dict(pins, source_mode="card-snapshot-v1")),
           content_reply(pins, binding=dict(pins, source_mode="content-snapshot-v1", card_policy_sha256="f" * 64)),
           content_reply(pins, binding=dict(pins, source_mode="content-snapshot-v1", memory_sha256="0" * 64)),
           content_reply(pins, completeness="COMPLETE"),
           {key: value for key, value in content_reply(pins).items() if key != "completeness"}]
    for value in bad:
        with pytest.raises(ValueError):
            link._public_result(json.dumps(value).encode(), 0, pins)


# --- the lane caller --------------------------------------------------------------------------------------------------

def test_the_lane_caller_answers_content_through_the_v4_plan(lane):
    answer = lane_query.query_public(**lane["call"])
    assert answer["status"] == "ANSWER" and answer["lane"] == "fable-5" and len(lane["calls"]) == 1
    assert answer["binding"] == lane["call"]["expected_binding"]
    assert tcm.DOC_TEXT in [cp.decode_text(item["stored_text"]) for item in answer["results"]]


def test_a_different_expected_content_pin_is_refused_before_the_transport(lane):
    call = dict(lane["call"], expected_binding=dict(lane["call"]["expected_binding"], memory_sha256="0" * 64))
    refused("binding_refused", call)
    assert lane["calls"] == []


def test_an_incomplete_content_binding_is_invalid_input(lane):
    binding = dict(lane["call"]["expected_binding"])
    del binding["memory_sha256"]
    refused("invalid_input", dict(lane["call"], expected_binding=binding))
    assert lane["calls"] == []


def test_a_plan_decision_for_other_pins_is_refused_before_the_transport(lane):
    other, other_sha = tcr.decision_of(prefix=None, message_pin=None, doc_pin="c" * 64, nonreview_policy_sha256="d" * 64)
    document = plan_document(lane["content"], lane["files"])
    document["content_decision_path"], document["content_decision_sha256"] = write(lane["files"] / "other.bin", other)
    path, pin = write(lane["files"] / "other-plan.json", json.dumps(document, sort_keys=True).encode())
    binding = dict(lane["call"]["expected_binding"], content_decision_sha256=other_sha)
    refused("binding_refused", dict(lane["call"], public_plan_path=path, expected_public_plan_sha256=pin,
                                    expected_binding=binding))
    assert lane["calls"] == []


@pytest.mark.parametrize("edit", [
    lambda p: p["messages"]["records"][0]["meta"].update(agent="claude-rco-1"),
    lambda p: p["messages"]["records"][0]["meta"].update(task_id=tcr.UNAPPROVED_TASK),
    lambda p: p["documents"]["records"][0].update(stored_text=cp.encode_text("vaihdettu")),
])
def test_a_projection_outside_the_route_is_refused_before_the_transport(lane, edit):
    projection = json.loads(lane["call"]["projection_bytes"])
    edit(projection)
    data = json.dumps(projection, sort_keys=True, indent=1).encode("utf-8")
    binding = dict(lane["call"]["expected_binding"], projection_sha256=sha(data))
    refused("projection_refused", dict(lane["call"], projection_bytes=data, expected_binding=binding))
    assert lane["calls"] == []


def test_a_reply_text_other_than_the_record_text_is_refused(lane, monkeypatch):
    original = child.run_content

    def run_content(config, stdin, stdout, **kwargs):
        buffer = io.StringIO()
        code = original(config, stdin, buffer, **kwargs)
        value = json.loads(buffer.getvalue())
        value["results"][0]["stored_text"] = "synthetic other text"
        stdout.write(json.dumps(value))
        return code

    monkeypatch.setattr(child, "run_content", run_content)
    refused("result_refused", lane["call"])


def test_a_card_or_v6_binding_never_meets_a_v4_plan(lane):
    binding = dict(lane["call"]["expected_binding"])
    del binding["content_decision_sha256"]
    binding["card_decision_sha256"] = "9" * 64
    refused("invalid_input", dict(lane["call"], expected_binding=binding))
    assert lane["calls"] == []


def test_the_seam_refuses_a_content_reply_with_a_bad_result_shape(lane):
    pins = lane["call"]["expected_binding"]
    item = content_reply(pins)["results"][0]
    bad = [content_reply(pins, results=[dict(item, ref_kind="event_ref")]),
           content_reply(pins, results=[dict(item, stored_text="x" * 321)]),
           content_reply(pins, results=[]),
           content_reply(pins, status="ABSTAIN"),
           content_reply(pins, extra="UNKNOWN"), content_reply(pins, status="PARTIAL")]
    for value in bad:
        with pytest.raises(ValueError):
            link._public_result(json.dumps(value).encode(), 0, pins)
    with pytest.raises(ValueError):
        link._public_result(json.dumps(content_reply(pins)).encode(), 1, pins)
    assert link._public_result(json.dumps(content_reply(pins, status="ABSTAIN", results=[])).encode(), 0,
                               pins)["status"] == "ABSTAIN"


@pytest.mark.parametrize("fault", ["other_bytes", "not_canonical"])
def test_projection_bytes_not_the_pinned_canonical_ones_are_refused(lane, fault):
    projection = json.loads(lane["call"]["projection_bytes"])
    if fault == "other_bytes":
        projection["documents"]["records"].pop()  # canonical and valid, but not the pinned bytes
        data = json.dumps(projection, sort_keys=True, indent=1).encode("utf-8")
        binding = lane["call"]["expected_binding"]
    else:
        data = json.dumps(projection, sort_keys=True).encode("utf-8")
        binding = dict(lane["call"]["expected_binding"], projection_sha256=sha(data))
    refused("projection_refused", dict(lane["call"], projection_bytes=data, expected_binding=binding))
    assert lane["calls"] == []


def test_a_message_record_of_a_lane_outside_the_roster_is_refused(lane):
    roster = json.loads(lane["call"]["roster_bytes"])
    projection = json.loads(lane["call"]["projection_bytes"])
    outsider = "codex-outside-9"
    assert outsider not in json.dumps(roster)
    projection["messages"]["records"][0]["meta"]["agent"] = outsider
    data = json.dumps(projection, sort_keys=True, indent=1).encode("utf-8")
    binding = dict(lane["call"]["expected_binding"], projection_sha256=sha(data))
    refused("projection_refused", dict(lane["call"], projection_bytes=data, expected_binding=binding))
    assert lane["calls"] == []


@pytest.mark.parametrize("change", [
    {"schema": "wd.q4-card-snapshot-query.v1"}, {"completeness": "COMPLETE"},
    {"binding": "source_mode"}, {"binding": "content_decision_sha256"},
])
def test_the_lane_answer_checks_the_content_reply_itself(lane, change):
    """Layer test: ``_answer`` refuses on its own, whatever the seam above it accepted."""
    expected = lane["call"]["expected_binding"]
    value = content_reply(expected, results=[])
    if change.get("binding") == "source_mode":
        value["binding"]["source_mode"] = "card-snapshot-v1"
    elif change.get("binding") == "content_decision_sha256":
        expected = dict(expected)  # a reply carrying the content pin never meets a binding without it
        del expected["content_decision_sha256"]
    else:
        value.update(change)
    assert lane_query._answer("fable-5", value, expected, {}, "2026-11-08T00:00:00Z") == (None, "binding_refused")
    if change.get("binding") != "content_decision_sha256":
        assert lane_query._answer("fable-5", content_reply(lane["call"]["expected_binding"], results=[]),
                                  lane["call"]["expected_binding"], {}, "2026-11-08T00:00:00Z")[1] is None

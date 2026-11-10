"""Synthetic real parent adapter and pinned content route; no native peer."""

import json
from pathlib import Path
import sys

import pytest

from tools import wd_q4_lane_query
from tests.tools.test_wd_q4_content_lane_query import cq, lane, WHEN
from sandbox.mind.step2 import shared_content_projection as projection
from sandbox.mind.step2 import test_content_route as tcr
from sandbox.mind.step2 import test_shared_content_projection as tcp
from tests.tools.test_wd_q4_lane_caller_cli import (
    LANE, TOKEN_CANARY, canon, case, invoke, sha,
)

EXCLUDED = ("synthetic-rco-author-canary", "synthetic-rco-recipient-canary",
            "synthetic-unapproved-task-canary", tcp.CANARIES["openai_key"](), "sealed synthetic evaluation canary")


@pytest.fixture
def content(case, tmp_path, monkeypatch):
    # The caller's fleet and the content projection must bind the SAME roster.
    fleet_path = case["files"]["wd-fleet.json"]
    fleet = json.loads(fleet_path.read_bytes())
    fleet["tools_supervisor"]["agent_uuid"] = tcp.ROSTER[LANE]
    fleet_raw = canon(fleet)
    fleet_path.write_bytes(fleet_raw)
    case["descriptor"]["fleet_sha256"] = sha(fleet_raw)
    case["manifest"]["files"]["wd-fleet.json"] = sha(fleet_raw)
    monkeypatch.setenv("AGENT_BRIDGE_AGENT_UUID", tcp.ROSTER[LANE])
    original = tcr.strict_pins

    def strict_pins(*args, **kwargs):
        pins = original(*args, **kwargs)
        return dict(pins, roster_bytes=fleet_raw, roster_sha256=sha(fleet_raw))

    monkeypatch.setattr(tcr, "strict_pins", strict_pins)
    original_rows = tcr.prefix_rows

    def rows_with_exclusions():
        return original_rows() + [
            tcp.row(EXCLUDED[0], agent=tcp.RCO1, minute=4),
            tcp.row(EXCLUDED[1], to=tcp.RCO1, minute=5),
            tcp.row(EXCLUDED[2], task_id=tcr.UNAPPROVED_TASK, minute=6),
            tcp.row(EXCLUDED[3], minute=7), tcp.row(EXCLUDED[4], minute=8),
        ]

    monkeypatch.setattr(tcr, "prefix_rows", rows_with_exclusions)
    built = cq.content.__wrapped__(tmp_path, monkeypatch)
    source = Path(built["route"]["messages"]["source_path"]).read_bytes().decode("utf-8")
    assert all(value in source for value in EXCLUDED)
    projected = json.loads((built["dir"] / "projection.json").read_bytes())
    records = projected["messages"]["records"] + projected["documents"]["records"]
    projected_text = [projection.decode_text(record["stored_text"]) for record in records]
    assert all(value not in text for value in EXCLUDED for text in projected_text)
    return built


@pytest.fixture
def parent_content(case, lane, monkeypatch):
    """Bind the real CLI, parent adapter and re-derived content to one synthetic bundle."""
    import tools

    document = dict(lane["document"])
    document["config"] = dict(document["config"], memory=str(lane["content"]["dir"]),
                              descriptor=str(lane["content"]["dir"] / "descriptor.json"),
                              runtime_revision=lane["content"]["config"].runtime_revision)
    plan_raw = canon(document)
    plan_path = case["files"]["public-plan.json"]
    plan_path.write_bytes(plan_raw)
    binding = dict(lane["call"]["expected_binding"])
    binding_raw = canon({"schema": "wd.q4-lane-binding.v1", "binding": binding,
                         "public_plan_sha256": sha(plan_raw)})
    case["files"]["binding.json"].write_bytes(binding_raw)
    case["descriptor"].update(expected_binding=binding, binding_sha256=sha(binding_raw))
    descriptor_raw = canon(case["descriptor"])
    case["files"]["descriptor.json"].write_bytes(descriptor_raw)
    monkeypatch.setenv("WD_Q4_SHARED_DESCRIPTOR_SHA256", sha(descriptor_raw))

    bundle = case["files"]["deployment-manifest.json"].parent
    wrapper = bundle / "Invoke-WdQ4Query.ps1"
    wrapper.write_bytes(b"synthetic wrapper; in-process synthetic transport")
    manifest = dict(case["manifest"])
    manifest["files"] = dict(manifest["files"], **{"Invoke-WdQ4Query.ps1": sha(wrapper.read_bytes())})
    manifest_raw = canon(manifest)
    case["files"]["deployment-manifest.json"].write_bytes(manifest_raw)
    monkeypatch.setenv("WD_REBOOT_EXPECTED_MANIFEST_HASH", sha(manifest_raw))
    lane["call"].update(bundle_root=bundle, expected_commit=manifest["source_commit"],
                        expected_manifest_sha256=sha(manifest_raw), expected_wrapper_sha256=sha(wrapper.read_bytes()),
                        public_plan_path=str(plan_path), expected_public_plan_sha256=sha(plan_raw))
    case["clock"]["default"] = WHEN
    case["query"] = canon({"situation": lane["call"]["situation"], "time": "2026-11-08T00:00:00Z"})

    real = case["real_adapter"]
    monkeypatch.setitem(sys.modules, "tools.wd_q4_lane_caller_adapter", real)
    monkeypatch.setattr(tools, "wd_q4_lane_caller_adapter", real, raising=False)
    monkeypatch.setattr(real, "_QUARANTINED", False)
    monkeypatch.setattr(real, "_HOLD", [])
    mutation = {"after_query": False, "queried": 0, "mutated": False}

    def synthetic_session(**kwargs):
        answer = wd_q4_lane_query.query_public(**dict(lane["call"], lane=kwargs["lane"],
                                                     situation=kwargs["situation"], time=kwargs["time"]))
        assert answer["status"] == "ANSWER" and answer["results"]
        mutation["queried"] += 1
        context = kwargs["_owner_context"]
        assert kwargs["_owner_guard"]._accept_owner_context(context) is context
        for name in ("transport", "attestation"):
            ticket = context._ticket(name)
            assert context._accept_ticket(name, ticket) is ticket
            ticket._begin()
            ticket._complete()
        if mutation["after_query"]:
            path = lane["content"]["dir"] / "projection.json"
            path.write_bytes(path.read_bytes() + b" ")
            mutation["mutated"] = True
        return answer

    monkeypatch.setattr(real.session, "query_session", synthetic_session)
    return {"case": case, "lane": lane, "mutation": mutation}


def test_real_parent_releases_screened_synthetic_content(parent_content, monkeypatch, capsys):
    assert invoke(parent_content["case"], monkeypatch) == 0
    captured = capsys.readouterr()
    assert captured.err == "" and TOKEN_CANARY not in captured.out
    assert all(value not in captured.out and projection.encode_text(value) not in captured.out for value in EXCLUDED)
    out = json.loads(captured.out)
    assert out["schema"] == "wd.q4-lane-content-delivery.v1"
    assert out["freshness"] == "UNKNOWN" and out["index_age"]["status"] == "UNKNOWN"
    assert out["answer"]["lane"] == LANE and out["answer"]["results"]


def test_projection_changed_after_answer_is_never_emitted(parent_content, monkeypatch, capsys):
    parent_content["mutation"]["after_query"] = True
    assert invoke(parent_content["case"], monkeypatch) != 0
    captured = capsys.readouterr()
    assert parent_content["mutation"]["queried"] == 1 and parent_content["mutation"]["mutated"]
    assert captured.out == "" and TOKEN_CANARY not in captured.err

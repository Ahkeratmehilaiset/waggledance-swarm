"""Synthetic cross-layer boundary: projection, startup origin and withheld output.

No live bridge, materializer, deployed descriptor or native lane process is used.
The lane identity is declared-consistent only; it is not authenticated.
"""

import hashlib
import json
from types import SimpleNamespace

import pytest

pytest.importorskip("numpy")
pytest.importorskip("faiss")

from sandbox.mind.step2 import content_memory, q4, shared_doc_projection
from sandbox.mind.step2 import shared_content_projection as content_projection
from sandbox.mind.step2 import shared_index_builder
from sandbox.mind.step2.shared_index_preflight import _encoder_digest
from sandbox.mind.step2.test_q4_readonly_port import FakeEncoder, FakeIndex
from sandbox.mind.step2.test_shared_content_projection import (
    FABLE, LEAD, RCO1, RCO2, TOOLS, build, row, rows_of,
)
from tests.tools.test_wd_q4_lane_caller_cli import LANES, TOKEN_CANARY, UUIDS, case
from tools.bridge_compact_view import event_id
from tools import wd_q4_lane_link


STARTUP = "%s startup read: own current task, last bound reply, operator directive of the day"
WHEN = "2026-11-08T12:00:00Z"


def synthetic_projection():
    events = []
    for index, lane in enumerate((LEAD, TOOLS, FABLE)):
        for number in range(4):
            events.append(row("current task has bound context and directive progress number %d" % number,
                              agent=lane, to=lane, minute=index * 4 + number))
    excluded = [
        row("current task restricted example A", agent=RCO1, to=FABLE, minute=20),
        row("current task restricted example B", agent=RCO2, to=FABLE, minute=21),
        row("current task restricted example C", agent=LEAD, to=RCO1, minute=22),
        row("current task restricted example D", agent=LEAD, to=RCO2, minute=23),
    ]
    projection = build(rows_of(*(events + excluded)))
    source_refs = {record["provenance"]["source_event_ref"] for record in projection["records"]}
    assert projection["accepted"] == len(events)
    assert source_refs == {event_id(event) for event in events}
    assert source_refs.isdisjoint({event_id(event) for event in excluded})
    documents = shared_doc_projection._records(
        "a" * 40, "docs/synthetic.md", "b" * 40, "2026-11-07T11:00:00Z", 0,
        [content_projection.encode_text("current task documentation provides shared context")],
    )
    return projection, documents


def memory_from_records(records):
    """Exercise the real projected metadata-to-Q4 path without publishing files."""
    rows, texts, metadata = [], [], []
    for record in records:
        for cell_row in shared_index_builder.cell_rows(content_memory._decoded(record), "learning"):
            rows.append(cell_row)
            texts.append(cell_row["stored_text"])
            metadata.append(cell_row["meta"])
    collection = SimpleNamespace(_index=FakeIndex(len(rows)), count=len(rows),
                                 _texts=texts, _metadata=metadata)
    view = SimpleNamespace(healthy=True, collection=collection, rows=rows)
    topology = SimpleNamespace(get_neighbors=lambda cell_id, max_ring: ["learning"])
    return SimpleNamespace(views={"learning": view}, memory={"dim": 2}, topology=topology)


def bind_synthetic_content_descriptor(case, projection, documents, monkeypatch):
    """Bind one content descriptor to synthetic bytes; no memory publication occurs."""
    cli = case["cli"]
    root = case["files"]["descriptor.json"].parent
    memory = root / "synthetic-content-memory"
    memory.mkdir()
    raw_projection = cli._canon({"messages": projection, "documents": {"records": documents}})
    encoder = {"model": "synthetic", "revision": "b" * 40, "weights_sha256": "a" * 64}
    raw_memory = cli._canon({"projection_schema": "wd.mind-content-memory-projection.v1",
                             "projection_sha256": cli._sha(raw_projection)})
    raw_memory_descriptor = cli._canon({"schema": "wd.bridge-mind-q4-memory-descriptor.v2",
        "scope": "wd.q4-shared-projected-events.v1", "memory_sha256": cli._sha(raw_memory),
        "dim": 2, "encoder": encoder})
    files = {"descriptor": (memory / "descriptor.json", raw_memory_descriptor),
             "memory": (memory / "memory.json", raw_memory),
             "projection": (memory / "projection.json", raw_projection),
             "policy": (root / "content-policy.json", b"{}"),
             "roster": (root / "content-roster.json", b"{}"),
             "content_decision": (root / "content-decision.json", b"{}")}
    for path, raw in files.values():
        path.write_bytes(raw)
    pins = {"encoder_sha256": _encoder_digest(encoder), "policy_sha256": cli._sha(files["policy"][1]),
            "roster_sha256": cli._sha(files["roster"][1]),
            "content_decision_sha256": cli._sha(files["content_decision"][1]),
            "descriptor_sha256": cli._sha(raw_memory_descriptor), "memory_sha256": cli._sha(raw_memory),
            "projection_sha256": cli._sha(raw_projection)}
    plan = {"schema": wd_q4_lane_link._CONTENT_PLAN_SCHEMA,
            "config": {"memory": str(memory), "descriptor": str(files["descriptor"][0]),
                       "descriptor_sha256": pins["descriptor_sha256"], "runtime_revision": "b" * 40},
            "encoder_sha256": pins["encoder_sha256"], "memory_sha256": pins["memory_sha256"],
            "projection_sha256": pins["projection_sha256"]}
    for key in ("policy", "roster", "content_decision"):
        plan[key + "_path"] = str(files[key][0])
        plan[key + "_sha256"] = pins[key + "_sha256"]
    if wd_q4_lane_link._CONTENT_PLAN_SCHEMA == "wd.q4-public-wrapper-plan.v5":
        for key in ("message_source_path", "message_generation_path", "message_policy_path", "doc_repo_root",
                    "doc_policy_path", "doc_git_executable", "doc_git_sha256"):
            plan[key] = None
    raw_plan = cli._canon(plan)
    case["files"]["public-plan.json"].write_bytes(raw_plan)
    raw_binding = cli._canon({"schema": "wd.q4-lane-binding.v1", "binding": pins,
                              "public_plan_sha256": cli._sha(raw_plan)})
    case["files"]["binding.json"].write_bytes(raw_binding)
    case["descriptor"]["binding_sha256"] = cli._sha(raw_binding)
    case["descriptor"]["expected_binding"] = pins
    descriptor_raw = cli._canon(case["descriptor"])
    case["files"]["descriptor.json"].write_bytes(descriptor_raw)
    monkeypatch.setenv("WD_Q4_SHARED_DESCRIPTOR_SHA256", cli._sha(descriptor_raw))
    return pins


def test_five_lanes_share_one_pinned_descriptor_but_reviewer_startup_abstains(case, monkeypatch):
    projection, documents = synthetic_projection()
    records = projection["records"] + documents
    by_id = {record["record_id"]: record for record in records}
    engine = q4.Q4(memory_from_records(records), FakeEncoder())

    cli = case["cli"]
    expected_binding = bind_synthetic_content_descriptor(case, projection, documents, monkeypatch)
    descriptor, descriptor_raw, descriptor_pin, fleet_raw, binding, inputs = cli._configuration()
    assert set(descriptor["lanes"]) == set(LANES)
    assert descriptor_pin == hashlib.sha256(descriptor_raw).hexdigest()
    assert binding == expected_binding
    cli._require_current_snapshot(descriptor, binding, inputs)
    shared_question = "what is the current task for this project"
    shared = engine.ask(shared_question, WHEN)
    assert shared["status"] == "ANSWER" and shared["results"]

    for lane in LANES:
        monkeypatch.chdir(case["worktrees"][lane])
        monkeypatch.setenv("AGENT_BRIDGE_AGENT", lane)
        monkeypatch.setenv("AGENT_BRIDGE_AGENT_UUID", UUIDS[lane])
        found_lane, identity_bytes, token = cli._identity(fleet_raw, descriptor, descriptor_pin)
        identity = json.loads(identity_bytes)
        assert found_lane == lane and identity["descriptor_sha256"] == descriptor_pin
        assert token == TOKEN_CANARY

        startup = engine.ask(STARTUP % lane, WHEN)
        if lane in (RCO1, RCO2):
            assert startup["status"] == "ABSTAIN" and not startup["results"]
        for hit in startup["results"]:
            record = by_id[hit["doc_id"]]
            assert lane in (record["meta"].get("agent"), record["meta"].get("to"))
            assert record not in documents

        # The public answer is synthetic and injected directly into the real outer
        # output boundary. It is not a native lane call or a freshness proof.
        items = [{"ref_sha256": hit["ref"],
                  "doc_id_sha256": hashlib.sha256(hit["doc_id"].encode()).hexdigest(),
                  "stored_text": content_projection.decode_text(by_id[hit["doc_id"]]["stored_text"])}
                 for hit in shared["results"]]
        answer = {"schema": "wd.q4-lane-answer.v1", "lane": lane, "status": "ANSWER", "reason": "",
                  "result_count": len(items), "results": items, "binding": binding, "trust": dict(cli._TRUST)}
        result = {"schema": "wd.q4-lane-caller-result.v1", "status": "VALIDATED", "lane": lane,
                  "lane_binding": "DECLARED_CONSISTENT", "lane_authentication": "UNKNOWN",
                  "cleanup": "chain_confirmed", "inner_status": "ANSWER", "result_count": len(items),
                  "answer": answer, "answer_sha256": cli._sha(cli._canon(answer)),
                  "identity_sha256": cli._sha(identity_bytes), "descriptor_sha256": descriptor_pin,
                  "call_id": "f" * 32}
        raw = cli._output(result, lane, cli._sha(identity_bytes), descriptor_pin, binding, 65536, token)
        withheld = json.loads(raw)
        assert withheld["status"] == "CONTENT_WITHHELD"
        assert withheld["reason"] == "SOURCE_FRESHNESS_UNVERIFIED"
        assert withheld["freshness"] == "UNKNOWN" and withheld["lane"] == lane
        assert withheld["descriptor_sha256"] == descriptor_pin and withheld["binding"] == binding
        assert "answer" not in withheld and "stored_text" not in raw
        assert all(item["stored_text"] not in raw for item in items)
    cli._require_current_snapshot(descriptor, binding, inputs)


@pytest.mark.parametrize("lane", LANES)
def test_startup_origin_filter_excludes_unlabelled_documents_for_every_lane(lane):
    chunks = [content_projection.encode_text(
        "current task documentation provides shared project context number %d" % index) for index in range(4)]
    documents = shared_doc_projection._records("a" * 40, "docs/synthetic.md", "b" * 40,
                                               "2026-11-07T11:00:00Z", 0, chunks)
    engine = q4.Q4(memory_from_records(documents), FakeEncoder())
    generic = engine.ask("what is the current task for this project", WHEN)
    assert generic["status"] == "ANSWER" and generic["results"]
    startup = engine.ask(STARTUP % lane, WHEN)
    assert startup["status"] == "ABSTAIN"
    assert startup["reason"] == "no_lane_origin_results" and not startup["results"]

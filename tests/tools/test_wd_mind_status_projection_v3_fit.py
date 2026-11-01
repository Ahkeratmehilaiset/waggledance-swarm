"""Bounded five-lane fit of the inactive public metadata projection with the real public status route.

Synthetic only: a temporary bridge log, synthetic roster uuids, no live data, no network, no installed bundle.
The route under test is the committed one: ``shared_bridge_snapshot.read_anchored_delta`` ->
``status_metadata_producer.build_status`` -> ``wd_mind_public_session.publish_status`` (v2 publication on disk)
-> the publication BODY handed to ``wd_mind_status_projection_v3.project_status`` -> compared with
``wd_mind_public_session.read_status`` for each of the five synthetic roster lanes.

Limitations kept explicit, never claimed away:

* ``project_status`` derives its approved task set from the SAME descriptor it validates (descriptor-only
  approval). ``test_descriptor_only_approval_limitation_is_pinned`` shows a descriptor approving a task outside the
  committed non-review pilot policy is still projected, while the production policy check refuses it.
* The body pin is the CALLER's digest over the CALLER's bytes. ``test_body_pin_is_the_caller_digest_not_the
  _publication_digest`` shows non-canonical bytes pass with their own digest and fail against the publication's
  ``body_sha256``; the only bytes the publication digest covers are ``session._json(doc["body"])``.
* Nothing here attests source freshness, publisher identity or lane identity; ``read_status`` itself labels the
  lane ``caller_declared_not_authenticated``.
* This file relies on the private names ``session._structure`` and ``session._json`` to reach the publication
  body exactly as the publisher digested it (pinned by ``test_private_helpers_relied_on_are_present``).
"""
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from sandbox.mind.step2 import shared_bridge_snapshot, shared_capture_adapter as adapter, status_metadata_producer
from tools import wd_mind_public_session as session
from tools import wd_mind_status_projection_v3 as v3
from waggledance.core.bridge_log_reader import read_bridge_log

ROOT = Path(__file__).resolve().parents[2]
PILOT_PUBLIC_POLICY = ROOT / "configs" / "mind" / "pilot_public_status_policy.json"
PILOT_NON_REVIEW_POLICY = ROOT / "configs" / "mind" / "pilot_non_review_policy.json"
ROSTER = {"codex-lead-1": "00000001-abcd-4ef0-8abc-000000000001",
          "codex-tools-1": "00000002-abcd-4ef0-8abc-000000000002",
          "claude-rco-1": "00000003-abcd-4ef0-8abc-000000000003",
          "claude-rco-2": "00000004-abcd-4ef0-8abc-000000000004",
          "fable-5": "00000005-abcd-4ef0-8abc-000000000005"}  # synthetic family, never real uuids
REVIEW_LANES = ("claude-rco-1", "claude-rco-2")
TASK = "public-synthetic-task"
SENTINEL = "synthetic-private-sentinel"
SYNTHETIC_CWD = "C:\\synthetic-only"
RECORD_KEYS = {"time_utc", "agent", "type", "status", "task_id"}


def enc(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def record(**changes):
    item = {"time_utc": "2026-10-31T12:00:00.000000Z", "agent": "fable-5", "type": "status", "status": "done",
            "task_id": TASK}
    item.update(changes)
    return item


def body(records):
    return enc({"schema": "wd.mind-status-metadata.v1", "authority": "none", "scope": "in_memory_metadata_not_gate",
                "freshness": "UNKNOWN", "records": records})


def descriptor(tasks=(TASK,), types=("status",), **extra):
    value = {"schema": "wd.mind-public-status-policy.v1", "public_tasks": list(tasks), "public_types": list(types)}
    value.update(extra)
    return enc(value)


def project(body_bytes, descriptor_bytes, body_sha256=None, descriptor_sha256=None, enabled=True):
    return v3.project_status(enabled=enabled, body_bytes=body_bytes,
                             body_sha256=sha(body_bytes) if body_sha256 is None else body_sha256,
                             descriptor_bytes=descriptor_bytes,
                             descriptor_sha256=sha(descriptor_bytes) if descriptor_sha256 is None else descriptor_sha256)


def refused(body_bytes, descriptor_bytes, **overrides):
    with pytest.raises(v3.ProjectionRefused) as exc:
        project(body_bytes, descriptor_bytes, **overrides)
    error = exc.value
    assert error.args == ("projection_refused",)
    assert error.__cause__ is None and error.__context__ is None
    assert SENTINEL not in repr(error) and SYNTHETIC_CWD not in repr(error)
    return error


# ---------------------------------------------------------------------------------------------------------------
# 1. The committed pilot descriptor, byte for byte
# ---------------------------------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def pilot():
    raw = PILOT_PUBLIC_POLICY.read_bytes()
    document = json.loads(raw)
    assert document["schema"] == "wd.mind-public-status-policy.v1"
    assert len(document["public_tasks"]) >= 1 and len(document["public_types"]) >= 1
    return raw, document


def test_committed_pilot_descriptor_is_accepted_as_committed(pilot):
    raw, document = pilot
    item = record(type=document["public_types"][0], task_id=document["public_tasks"][0])
    payload = body([item])
    result = project(payload, raw)
    assert result["schema"] == "wd.mind-status-projection.v3" and result["authority"] == "none"
    assert result["coverage"] == "UNKNOWN"
    assert result["trust"] == {"source_authentication": "UNKNOWN", "source_freshness": "UNKNOWN",
                               "publisher_authentication": "UNKNOWN"}
    assert result["records"] == [item] and set(result["records"][0]) == RECORD_KEYS
    output = json.dumps(result)
    assert sha(raw) not in output and sha(payload) not in output


def test_pilot_descriptor_refuses_a_type_outside_its_public_types(pilot):
    raw, document = pilot
    foreign = next(kind for kind in ("message", "handoff", "claim") if kind not in document["public_types"])
    refused(body([record(type=foreign, task_id=document["public_tasks"][0])]), raw)


def test_pilot_descriptor_refuses_a_task_outside_its_public_tasks(pilot):
    raw, document = pilot
    assert "other-synthetic-task" not in document["public_tasks"]
    refused(body([record(type=document["public_types"][0], task_id="other-synthetic-task")]), raw)


def test_descriptor_only_approval_limitation_is_pinned():
    """Explicit limitation, not a claim: the projection approves whatever the descriptor lists. A task the
    committed non-review pilot policy does NOT approve is still projected, while the production policy check
    (adapter.public_status_policy under the non-review task set, as wd_mind_public_session._pins runs it) refuses
    the same descriptor. A fix that pins an independent policy must change this test deliberately."""
    non_review = json.loads(PILOT_NON_REVIEW_POLICY.read_bytes())
    approved = frozenset(non_review["approved_tasks"])
    foreign = "synthetic-task-outside-non-review-policy"
    assert foreign not in approved
    raw = descriptor(tasks=(foreign,), types=("status",))
    result = project(body([record(task_id=foreign)]), raw)
    assert result["records"][0]["task_id"] == foreign
    with pytest.raises(ValueError):
        adapter.public_status_policy(raw, sha(raw), approved)
    # The committed pilot pair IS consistent with the production check:
    pilot_public = PILOT_PUBLIC_POLICY.read_bytes()
    public_tasks, public_types = adapter.public_status_policy(pilot_public, sha(pilot_public), approved)
    assert public_tasks <= approved and public_types


# ---------------------------------------------------------------------------------------------------------------
# 2. The real route: anchored snapshot -> producer -> publisher -> publication body -> projection == read_status
# ---------------------------------------------------------------------------------------------------------------


@pytest.fixture
def route(tmp_path):
    audit = tmp_path / ".codex-audit"
    bridge = audit / "synthetic-bridge" / "shared"
    bridge.mkdir(parents=True)
    log, generation = bridge / "events.jsonl", bridge / "events.generation.json"
    log.write_bytes(b"")
    generation.write_bytes(enc({"generation": "synthetic-generation"}))
    start = read_bridge_log(log, generation_path=generation).candidate_cursor
    tasks = frozenset({TASK})
    policy = enc({"schema": "wd.q4-non-review-policy.v1", "approved_tasks": sorted(tasks)})
    roster = enc({"lanes": [{"agent": k, "agent_uuid": v} for k, v in ROSTER.items() if k != "codex-tools-1"],
                  "tools_supervisor": {"agent": "codex-tools-1", "agent_uuid": ROSTER["codex-tools-1"]}})
    public = descriptor(tasks=sorted(tasks), types=("message",))
    anchor = enc({"schema": "wd.q4-source-anchor.v1", "source_path": str(log), "generation_path": str(generation),
                  "cursor_start": asdict(start), "policy_sha256": sha(policy), "roster_sha256": sha(roster),
                  "status_mode": "public-v1", "public_policy_sha256": sha(public)})
    row = {"ts_utc": datetime.now(timezone.utc).isoformat(), "agent": "codex-lead-1",
           "agent_uuid": ROSTER["codex-lead-1"], "type": "message", "task_id": TASK, "status": "done",
           "severity": "info", "to": "", "message": "", "paths": [], "write_scope": [], "run_id": "synthetic-run",
           "pid": 123, "cwd": SYNTHETIC_CWD, "role": "lead", "session_id": "synthetic-session",
           "capabilities": ["implementation", "tests"], "payload": {"public_status": True}}
    private = dict(row, to="fable-5", message=SENTINEL)
    log.write_bytes(enc(row) + b"\n" + enc(private) + b"\n")
    pins = dict(anchor_bytes=anchor, anchor_sha256=sha(anchor), policy_bytes=policy, policy_sha256=sha(policy),
                roster_bytes=roster, roster_sha256=sha(roster), public_policy_bytes=public,
                public_policy_sha256=sha(public))
    result = shared_bridge_snapshot.read_anchored_delta(log, start, tasks, generation_path=generation,
                                                        max_bytes=65536, max_rows=20, **pins)
    produced = status_metadata_producer.build_status(result, enabled=True, **pins)
    instant = datetime.now(timezone.utc)
    session.publish_status(audit, result, as_of=instant, enabled=True, expected_previous_sha256=None, **pins)
    raw = (audit / session.FILENAME).read_bytes()
    return SimpleNamespace(audit=audit, raw=raw, pins=pins, public=public, instant=instant,
                           producer_records=json.loads(produced)["records"],
                           private=(SENTINEL, SYNTHETIC_CWD, str(audit), str(log), "synthetic-run",
                                    "synthetic-session", "fable-5", *ROSTER.values(),
                                    *(value for name, value in pins.items() if name.endswith("_sha256"))))


def test_private_helpers_relied_on_are_present():
    assert callable(getattr(session, "_structure", None)) and callable(getattr(session, "_json", None))
    assert session.FILENAME == "public-status.json"


def test_publication_body_projects_and_matches_read_status_for_every_synthetic_lane(route):
    document = session._structure(route.raw)
    body_bytes = session._json(document["body"])
    assert sha(body_bytes) == document["body_sha256"]
    result = project(body_bytes, route.public, body_sha256=document["body_sha256"])
    assert len(result["records"]) == 1 and result["records"] == route.producer_records
    assert set(result["records"][0]) == RECORD_KEYS and result["records"][0]["agent"] == "codex-lead-1"
    for lane in sorted(ROSTER):
        parsed = json.loads(session.read_status(route.audit, lane=lane, agent_uuid=ROSTER[lane],
                                                as_of=route.instant, max_age_seconds=900, enabled=True,
                                                **route.pins))
        assert parsed["lane"] == lane and parsed["records"] == result["records"]
    output = json.dumps(result)
    for needle in route.private:
        assert needle not in output
    assert "to" not in result["records"][0] and "message" not in result["records"][0]


def test_body_pin_is_the_caller_digest_not_the_publication_digest(route):
    """Carrier gap pinned: the projection binds bytes to the caller's digest only. Non-canonical bytes of the same
    body pass with their own digest, and the publication's body_sha256 covers only session._json bytes."""
    document = session._structure(route.raw)
    canonical = session._json(document["body"])
    loose = json.dumps(document["body"], indent=1).encode("ascii")
    assert loose != canonical and sha(loose) != document["body_sha256"]
    assert project(loose, route.public)["records"] == project(canonical, route.public)["records"]
    refused(loose, route.public, body_sha256=document["body_sha256"])


def test_publication_with_a_review_lane_record_is_refused_by_the_projection(route):
    document = session._structure(route.raw)
    tampered = dict(document["body"], records=[dict(document["body"]["records"][0], agent="claude-rco-2")])
    refused(session._json(tampered), route.public)


# ---------------------------------------------------------------------------------------------------------------
# 3. Negatives: review lanes, the enabled literal, oversized inputs, descriptor and body shapes
# ---------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("lane", REVIEW_LANES)
def test_review_lane_record_is_refused(lane):
    refused(body([record(agent=lane)]), descriptor())


def test_unknown_lane_record_is_refused():
    refused(body([record(agent="synthetic-lane-" + SENTINEL)]), descriptor())


@pytest.mark.parametrize("literal", [1, 0, "true", "True", "1", None, [True], 1.0])
def test_enabled_must_be_the_bool_true_or_false(literal):
    payload = body([record()])
    with pytest.raises(v3.ProjectionRefused, match="^projection_refused$") as exc:
        v3.project_status(enabled=literal, body_bytes=payload, body_sha256=sha(payload),
                          descriptor_bytes=descriptor(), descriptor_sha256=sha(descriptor()))
    assert exc.value.__cause__ is None and exc.value.__context__ is None


def test_disabled_reads_nothing_and_reports_off_even_for_invalid_inputs():
    assert v3.project_status(enabled=False, body_bytes=SENTINEL, body_sha256=None, descriptor_bytes=object(),
                             descriptor_sha256=42) == {"status": "OFF"}


def test_oversized_body_is_refused_before_parsing():
    oversized = b"{" + b" " * session.MAX_BYTES  # MAX_BYTES + 1 bytes, correctly digested
    assert len(oversized) == session.MAX_BYTES + 1
    refused(oversized, descriptor())


def test_oversized_descriptor_is_refused_before_parsing():
    oversized = b"{" + b" " * v3._DESCRIPTOR_LIMIT
    assert len(oversized) == v3._DESCRIPTOR_LIMIT + 1
    refused(body([record()]), oversized)


def test_body_exactly_at_the_limit_is_accepted_and_one_byte_more_is_refused():
    item = record()
    payload = body([item])
    at_limit = payload + b" " * (session.MAX_BYTES - len(payload))  # JSON whitespace padding, same document
    assert len(at_limit) == session.MAX_BYTES
    assert project(at_limit, descriptor())["records"] == [item]
    refused(at_limit + b" ", descriptor())


@pytest.mark.parametrize("bad", [
    {"schema": "wd.mind-public-status-policy.v1", "public_tasks": [TASK]},                       # no public_types
    {"schema": "wd.mind-public-status-policy.v1", "public_tasks": [TASK], "public_types": "status"},  # not a list
    {"schema": "wd.mind-public-status-policy.v1", "public_tasks": [], "public_types": ["status"]},    # empty tasks
    {"schema": "wd.mind-public-status-policy.v1", "public_tasks": [TASK], "public_types": []},        # empty types
    {"schema": "wd.mind-public-status-policy.v1", "public_tasks": [TASK], "public_types": ["decision"]},  # review type
    {"schema": "wd.mind-public-status-policy.v2", "public_tasks": [TASK], "public_types": ["status"]},    # schema
    {"schema": "wd.mind-public-status-policy.v1", "public_tasks": [TASK], "public_types": ["status"],
     "extra": SENTINEL},                                                                              # extra key
    {"schema": "wd.mind-public-status-policy.v1", "public_tasks": [TASK, TASK], "public_types": ["status"]},  # dup
    {"schema": "wd.mind-public-status-policy.v1", "public_tasks": [SENTINEL + "/path"], "public_types": ["status"]},
    [TASK],                                                                                           # not an object
])
def test_descriptor_shape_negatives_are_refused_without_echo(bad):
    task = bad["public_tasks"][0] if isinstance(bad, dict) and bad.get("public_tasks") else TASK
    refused(body([record(task_id=task if isinstance(task, str) else TASK)]), enc(bad))


@pytest.mark.parametrize("change", [
    {"time_utc": "2026-10-31T12:00:30.000000Z"},   # not floored to the minute
    {"time_utc": "2026-10-31T12:00:00Z"},          # not the fixed microsecond form
    {"time_utc": "2026-10-31 12:00:00.000000Z"},   # space separator
    {"status": SENTINEL},                          # status outside the closed enum
    {"type": "decision"},                          # review type even if the descriptor asked for it
    {"task_id": SENTINEL + "/path"},               # path-shaped task
    {"to": "fable-5"},                             # routing field
    {"agent_uuid": ROSTER["fable-5"]},             # identity field
    {"cwd": SYNTHETIC_CWD},                        # private path
])
def test_body_record_negatives_are_refused_without_echo(change):
    types = ("status", "decision") if change.get("type") == "decision" else ("status",)
    raw = enc({"schema": "wd.mind-public-status-policy.v1", "public_tasks": [TASK], "public_types": list(types)})
    refused(body([record(**change)]), raw)


@pytest.mark.parametrize("change", [
    {"freshness": "fresh"}, {"authority": "lead"}, {"scope": "gate"}, {"schema": "wd.mind-status-metadata.v2"},
    {"note": SENTINEL},
])
def test_body_envelope_negatives_are_refused(change):
    value = json.loads(body([record()]))
    value.update(change)
    refused(enc(value), descriptor())


def test_body_envelope_missing_freshness_is_refused():
    value = json.loads(body([record()]))
    del value["freshness"]
    refused(enc(value), descriptor())

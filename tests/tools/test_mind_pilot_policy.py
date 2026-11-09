"""The deliberately NARROW, NOT-ENABLED public metadata pilot policy pair (Lead Source GO lead-mind-pilot-policy-fable-20261031-v1).

Two committed JSON documents bind the EXISTING strict schemas of ``shared_capture_adapter`` exactly:

* ``configs/mind/pilot_non_review_policy.json``  -> ``wd.q4-non-review-policy.v1`` with ONE approved task,
  ``lead-mind-production-20261031`` (the Lead production umbrella task);
* ``configs/mind/pilot_public_status_policy.json`` -> ``wd.mind-public-status-policy.v1`` with that one public task and
  ONE public type, ``status``.

The ``public_status: true`` marker is not a policy field (both schemas are closed): the adapter's public filter requires
it on every projected event, together with an empty ``message``, an empty ``to`` and empty scopes. Nothing here enables
anything: no fleet field points at these files, no runtime reads them, and this test only reads committed bytes and
calls pure validators with SYNTHETIC roster values. This is a pilot for one Lead task, not all-agent production access.
"""
import hashlib
import json
import pathlib
import re

import pytest

from sandbox.mind.step2 import shared_capture_adapter as adapter
from sandbox.mind.step2 import shared_projection as projection

ROOT = pathlib.Path(__file__).resolve().parents[2]
POLICY_PATH = ROOT / "configs" / "mind" / "pilot_non_review_policy.json"
PUBLIC_POLICY_PATH = ROOT / "configs" / "mind" / "pilot_public_status_policy.json"
FLEET_PATH = ROOT / "ops" / "windows" / "reboot" / "wd-fleet.json"
TASK = "lead-mind-production-20261031"
PUBLIC_TYPE = "status"
# SYNTHETIC roster: the real uuids live only in the manifest-pinned fleet file and never in a test or a bridge text.
ROSTER = {"codex-lead-1": "aaaa1111-1111-4111-8111-111111111aaa", "codex-tools-1": "bbbb2222-2222-4222-8222-222222222bbb",
          "claude-rco-1": "cccc3333-3333-4333-8333-333333333ccc", "claude-rco-2": "dddd4444-4444-4444-8444-444444444ddd",
          "fable-5": "eeee5555-5555-4555-8555-555555555eee"}
ROSTER_DOC = {"schema": "wd.q4-roster-snapshot.v1",
              "lanes": [{"agent": agent, "agent_uuid": uuid} for agent, uuid in ROSTER.items() if agent != "codex-tools-1"],
              "tools_supervisor": {"agent": "codex-tools-1", "agent_uuid": ROSTER["codex-tools-1"]}}
UUID_SHAPE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
SENTINEL = "zebra-message-body-7f3a"


def pinned(document):
    data = json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    return data, hashlib.sha256(data).hexdigest()


def committed(path):
    """The committed bytes exactly as the worktree holds them; the digest is CONSISTENCY for the validator, not trust."""
    data = path.read_bytes()
    return data, hashlib.sha256(data).hexdigest()


def event(**changes):
    """The narrow publisher shape the adapter accepts: a marked, untargeted, bodiless Lead status broadcast."""
    base = {"ts_utc": "2026-10-31T21:00:00.0000000Z", "agent": "codex-lead-1", "agent_uuid": ROSTER["codex-lead-1"],
            "type": PUBLIC_TYPE, "task_id": TASK, "status": "in_progress", "to": "", "message": "",
            "payload": {"public_status": True}}
    base.update(changes)
    return base


def test_pilot_policy_pair_binds_the_existing_strict_schemas_with_one_task_and_one_type():
    policy_bytes, policy_sha = committed(POLICY_PATH)
    public_bytes, public_sha = committed(PUBLIC_POLICY_PATH)
    roster_bytes, roster_sha = pinned(ROSTER_DOC)
    for data in (policy_bytes, public_bytes):
        data.decode("ascii")  # ASCII only, no BOM
        assert len(data) <= adapter._POLICY_LIMIT and b"\\" not in data and b":/" not in data and b":\\" not in data
    assert json.loads(policy_bytes) == {"schema": "wd.q4-non-review-policy.v1", "approved_tasks": [TASK]}
    assert json.loads(public_bytes) == {"schema": "wd.mind-public-status-policy.v1", "public_tasks": [TASK],
                                        "public_types": [PUBLIC_TYPE]}
    tasks, roster = adapter.strict_pins(policy_bytes, policy_sha, roster_bytes, roster_sha)
    assert tasks == frozenset({TASK}) and roster == ROSTER
    public_tasks, public_types = adapter.public_status_policy(public_bytes, public_sha, tasks)
    assert public_tasks == frozenset({TASK}) and public_types == frozenset({PUBLIC_TYPE})
    # the one task is shaped for the projection, is not a review task and carries no sealed marker
    assert projection.TASK.fullmatch(TASK) and adapter._REVIEW_TASK.search(TASK) is None
    assert not any(marker in TASK for marker in projection.SEALED_MARKERS)
    assert projection._task(TASK, tasks) == TASK and projection._task("lead-mind-live-20261031", tasks) == projection.UNKNOWN
    assert PUBLIC_TYPE in projection.ALLOWED_TYPES and PUBLIC_TYPE not in projection.EXCLUDED_TYPES
    # a wrong digest, a review task or a type outside the allowlist is refused by the SAME validators
    with pytest.raises(ValueError):
        adapter.strict_pins(policy_bytes, "0" * 64, roster_bytes, roster_sha)
    with pytest.raises(ValueError):
        adapter.public_status_policy(public_bytes, public_sha, frozenset({"another-task"}))
    review_bytes, review_sha = pinned({"schema": "wd.q4-non-review-policy.v1", "approved_tasks": [TASK, "x-rco1-review-y"]})
    with pytest.raises(ValueError):
        adapter.strict_pins(review_bytes, review_sha, roster_bytes, roster_sha)
    decision_bytes, decision_sha = pinned({"schema": "wd.mind-public-status-policy.v1", "public_tasks": [TASK],
                                           "public_types": [PUBLIC_TYPE, "decision"]})
    with pytest.raises(ValueError):
        adapter.public_status_policy(decision_bytes, decision_sha, tasks)
    text = (policy_bytes + public_bytes).decode("ascii")
    assert UUID_SHAPE.search(text) is None and SENTINEL not in text and "enabled" not in text


def test_pilot_policy_is_not_enabled_by_any_committed_carrier():
    fleet = json.loads(FLEET_PATH.read_bytes().decode("utf-8"))
    assert "mind_public_read" not in fleet  # the public read carrier is absent: nothing points at the pilot files
    assert fleet.get("q4_read") == {"enabled": False} and "resolved_inputs" not in fleet.get("q4_read", {})
    assert fleet.get("mind_read", {}).get("enabled") is False
    fleet_text = FLEET_PATH.read_bytes().decode("utf-8")
    assert "pilot_non_review_policy" not in fleet_text and "pilot_public_status_policy" not in fleet_text
    assert "configs/mind" not in fleet_text and "configs\\\\mind" not in fleet_text
    for path in (POLICY_PATH, PUBLIC_POLICY_PATH):
        document = json.loads(path.read_bytes())
        assert "enabled" not in document and "path" not in json.dumps(document)  # closed schemas carry no switch


def test_pilot_filter_admits_only_a_marked_bodiless_status_broadcast_of_the_one_task():
    policy_bytes, policy_sha = committed(POLICY_PATH)
    public_bytes, public_sha = committed(PUBLIC_POLICY_PATH)
    roster_bytes, roster_sha = pinned(ROSTER_DOC)
    tasks, _ = adapter.strict_pins(policy_bytes, policy_sha, roster_bytes, roster_sha)
    public_tasks, public_types = adapter.public_status_policy(public_bytes, public_sha, tasks)
    assert adapter._public_status_event(event(), public_tasks, public_types) is True
    assert adapter._public_status_event(event(status="done"), public_tasks, public_types) is True  # status enum is projection's
    rejected = [
        event(message=SENTINEL),                                   # a body never goes public
        event(type="message"), event(type="done"), event(type="milestone"),  # only the one pilot type
        event(type="decision"), event(type="finding"), event(type="rco_pass"),  # review signals, never
        event(task_id="lead-mind-live-20261031"),                  # only the one pilot task
        event(task_id="lead-mind-production-rco1-review-20261031"),
        event(to="fable-5"), event(to="codex-lead-1,fable-5"),     # targeted rows are private
        event(payload={}), event(payload={"public_status": False}), event(payload={"public_status": "true"}),
        event(payload={"public_status": True, "detail": SENTINEL}),  # the marker alone, nothing beside it
        event(paths=["C:\\x"]), event(write_scope=["a"]),
        event(capabilities=["rco_review"]),                        # a review-lane capability set is not a publisher
        event(extra=SENTINEL),                                     # unknown envelope key
        {k: v for k, v in event().items() if k != "to"},           # a missing to is not an empty to
    ]
    for row in rejected:
        assert adapter._public_status_event(row, public_tasks, public_types) is False, row
    # the public filter is a declaration check, not authentication: a forged lane label still passes HERE and is
    # the roster/identity layer's job, which is why the roster pin travels with the policy pins
    assert adapter._public_status_event(event(agent_uuid="ffff9999-9999-4999-8999-999999999fff"), public_tasks, public_types) is True

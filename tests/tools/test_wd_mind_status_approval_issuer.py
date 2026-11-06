"""Synthetic approval-candidate issuer checks; no live fleet or writes."""
import hashlib
import importlib
import json

import pytest

from sandbox.mind.step2.test_status_public_filter import ROSTER


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("ascii")


def sha(value):
    return hashlib.sha256(value).hexdigest()


def module():
    return importlib.import_module("tools.wd_mind_status_approval_issuer")


@pytest.fixture
def pins():
    policy = encode({"schema": "wd.q4-non-review-policy.v1", "approved_tasks": ["public-task", "private-task"]})
    roster = encode({"lanes": [{"agent": k, "agent_uuid": v} for k, v in ROSTER.items() if k != "codex-tools-1"],
                     "tools_supervisor": {"agent": "codex-tools-1", "agent_uuid": ROSTER["codex-tools-1"]}})
    descriptor = encode({"schema": "wd.mind-public-status-policy.v1", "public_tasks": ["public-task"],
                         "public_types": ["message"]})
    return {"policy_bytes": policy, "expected_policy_sha256": sha(policy), "roster_bytes": roster,
            "expected_roster_sha256": sha(roster), "descriptor_bytes": descriptor,
            "expected_descriptor_sha256": sha(descriptor)}


def test_off_never_reads_inputs():
    class Poison:
        def __getattribute__(self, name):
            raise AssertionError("off must not inspect")
    assert module().issue(issuance=Poison()) == {"status": "OFF"}


def test_positive_candidate_excludes_private_pins_and_works_with_projection(pins):
    from tools import wd_mind_status_projection_v3 as projection
    result = module().issue(enabled=True, issuance=pins)
    approval = result["approval_bytes"]
    assert result["approval_sha256"] == sha(approval)
    assert result["authority"] == "candidate_only"
    assert result["config_authentication"] == "UNKNOWN"
    document = json.loads(approval)
    assert set(document) == {"schema", "descriptor_sha256", "approved_tasks", "approved_types"}
    assert document["descriptor_sha256"] == pins["expected_descriptor_sha256"]
    assert document["approved_tasks"] == ["public-task"] and document["approved_types"] == ["message"]
    for forbidden in (pins["expected_policy_sha256"], pins["expected_roster_sha256"], "private-task", *ROSTER.values()):
        assert forbidden.encode() not in approval
    body = encode({"schema": "wd.mind-status-metadata.v1", "authority": "none",
                   "scope": "in_memory_metadata_not_gate", "freshness": "UNKNOWN", "records": []})
    projected = projection.project_status(enabled=True, body_bytes=body, body_sha256=sha(body),
        descriptor_bytes=pins["descriptor_bytes"], descriptor_sha256=pins["expected_descriptor_sha256"],
        approval_bytes=approval, expected_approval_sha256=result["approval_sha256"])
    assert projected["coverage"] == "UNKNOWN"


@pytest.mark.parametrize("kind", ["outside_policy", "wrong_policy_pin", "wrong_roster_pin", "wrong_descriptor_pin",
                                  "wrong_descriptor_schema", "duplicate_descriptor", "private_type"])
def test_invalid_or_unapproved_inputs_are_fixed_refusals(pins, kind):
    if kind == "outside_policy":
        raw = encode({"schema": "wd.mind-public-status-policy.v1", "public_tasks": ["outside-task"],
                      "public_types": ["message"]})
        pins["descriptor_bytes"], pins["expected_descriptor_sha256"] = raw, sha(raw)
    elif kind.endswith("_pin"):
        pins["expected_" + kind.removeprefix("wrong_").removesuffix("_pin") + "_sha256"] = "0" * 64
    else:
        desc = json.loads(pins["descriptor_bytes"])
        if kind == "wrong_descriptor_schema": desc["schema"] = "synthetic-private"
        elif kind == "private_type": desc["public_types"] = ["review"]
        raw = b'{"schema":"synthetic-private","schema":"x"}' if kind == "duplicate_descriptor" else encode(desc)
        pins["descriptor_bytes"], pins["expected_descriptor_sha256"] = raw, sha(raw)
    with pytest.raises(module().IssuerRefused) as exc:
        module().issue(enabled=True, issuance=pins)
    assert exc.value.args == ("issuer_refused",)
    assert "synthetic-private" not in repr(exc.value)
    assert exc.value.__cause__ is None and exc.value.__context__ is None


@pytest.mark.parametrize("task", ["rco1-task", "review-task", "code-reviewer", "prefixrco2", "sealed-task",
                                  "gold-task", "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"])
def test_sensitive_task_labels_refuse_even_if_both_policies_attempt_to_include_them(pins, task):
    policy = encode({"schema": "wd.q4-non-review-policy.v1", "approved_tasks": [task]})
    descriptor = encode({"schema": "wd.mind-public-status-policy.v1", "public_tasks": [task],
                         "public_types": ["message"]})
    pins.update(policy_bytes=policy, expected_policy_sha256=sha(policy), descriptor_bytes=descriptor,
                expected_descriptor_sha256=sha(descriptor))
    with pytest.raises(module().IssuerRefused):
        module().issue(enabled=True, issuance=pins)


@pytest.mark.parametrize("kind", ["policy", "roster"])
@pytest.mark.parametrize("embedded", [False, True])
def test_known_private_digest_in_public_task_is_refused(pins, monkeypatch, kind, embedded):
    private_digest = pins["expected_" + kind + "_sha256"]
    task = "prefix-" + private_digest + "-suffix" if embedded else private_digest
    descriptor = encode({"schema": "wd.mind-public-status-policy.v1", "public_tasks": [task],
                         "public_types": ["message"]})
    pins.update(descriptor_bytes=descriptor, expected_descriptor_sha256=sha(descriptor))
    if kind == "roster":
        policy = encode({"schema": "wd.q4-non-review-policy.v1", "approved_tasks": [task]})
        pins.update(policy_bytes=policy, expected_policy_sha256=sha(policy))
    else:
        # An actual policy containing its own digest would require a SHA-256 fixed
        # point. Stub only that prior pin validation to exercise this boundary.
        monkeypatch.setattr(module().adapter, "strict_pins", lambda *args: (frozenset({task}), {}))
    with pytest.raises(module().IssuerRefused):
        module().issue(enabled=True, issuance=pins)


@pytest.mark.parametrize("field", ["tasks", "types"])
def test_private_digest_comparison_is_case_insensitive(field):
    m = module()
    labels = ["prefix-" + "Ab" * 32]
    with pytest.raises(ValueError):
        m._refuse_private_digests(labels if field == "tasks" else [], labels if field == "types" else [],
                                  "ab" * 32, "cd" * 32)

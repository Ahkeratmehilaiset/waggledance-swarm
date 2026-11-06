"""Synthetic-only fail-closed contract for the inactive public metadata projection."""
import hashlib
import importlib
import json

import pytest


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("ascii")


def digest(value):
    return hashlib.sha256(value).hexdigest()


def approval_for(descriptor, tasks=("public-task",), types=("message",)):
    return encode({"schema": "wd.mind-public-approval.v1", "descriptor_sha256": digest(descriptor),
                   "approved_tasks": list(tasks), "approved_types": list(types)})


@pytest.fixture
def inputs():
    body = encode({"schema": "wd.mind-status-metadata.v1", "authority": "none",
                   "scope": "in_memory_metadata_not_gate", "freshness": "UNKNOWN",
                   "records": [{"time_utc": "2026-10-31T12:00:00.000000Z", "agent": "fable-5",
                                "type": "message", "status": "done", "task_id": "public-task"}]})
    descriptor = encode({"schema": "wd.mind-public-status-policy.v1", "public_tasks": ["public-task"],
                         "public_types": ["message"]})
    approval = approval_for(descriptor)
    return {"body_bytes": body, "body_sha256": digest(body), "descriptor_bytes": descriptor,
            "descriptor_sha256": digest(descriptor), "approval_bytes": approval,
            "expected_approval_sha256": digest(approval)}


def module():
    return importlib.import_module("tools.wd_mind_status_projection_v3")


def test_descriptor_cannot_self_authorize_a_new_task(inputs):
    changed = encode({"schema": "wd.mind-public-status-policy.v1", "public_tasks": ["public-task", "new-task"],
                      "public_types": ["message"]})
    inputs["descriptor_bytes"], inputs["descriptor_sha256"] = changed, digest(changed)
    with pytest.raises(module().ProjectionRefused, match="^projection_refused$"):
        module().project_status(enabled=True, **inputs)


def test_disabled_by_default_and_no_pins_in_output(inputs):
    m = module()
    assert m.project_status(**inputs) == {"status": "OFF"}
    result = m.project_status(enabled=True, **inputs)
    assert result["schema"] == "wd.mind-status-projection.v3"
    assert result["trust"] == {"source_authentication": "UNKNOWN", "source_freshness": "UNKNOWN",
                               "publisher_authentication": "UNKNOWN"}
    assert result["records"][0]["task_id"] == "public-task"
    output = json.dumps(result)
    assert all(inputs[key] not in output for key in ("body_sha256", "descriptor_sha256", "expected_approval_sha256"))


def test_replay_and_missing_delta_do_not_claim_coverage(inputs):
    m = module()
    first = m.project_status(enabled=True, **inputs)
    replay = m.project_status(enabled=True, **inputs)
    assert first == replay
    assert first["coverage"] == "UNKNOWN"
    empty = json.loads(inputs["body_bytes"])
    empty["records"] = []
    inputs["body_bytes"] = encode(empty)
    inputs["body_sha256"] = digest(inputs["body_bytes"])
    missing_delta = m.project_status(enabled=True, **inputs)
    assert missing_delta["records"] == []
    assert missing_delta["coverage"] == "UNKNOWN"


@pytest.mark.parametrize("field", ["anchor", "path", "cursor", "agent_uuid", "message", "payload"])
def test_private_or_unknown_fields_are_refused_without_echo(inputs, field):
    m = module()
    body = json.loads(inputs["body_bytes"])
    body["records"][0][field] = "synthetic-private-sentinel"
    inputs["body_bytes"] = encode(body)
    inputs["body_sha256"] = digest(inputs["body_bytes"])
    with pytest.raises(m.ProjectionRefused) as exc:
        m.project_status(enabled=True, **inputs)
    assert exc.value.args == ("projection_refused",)
    assert "synthetic-private-sentinel" not in repr(exc.value)
    assert exc.value.__cause__ is None and exc.value.__context__ is None


@pytest.mark.parametrize("field", ["body_sha256", "descriptor_sha256"])
def test_mismatched_independent_pins_refuse(inputs, field):
    inputs[field] = "0" * 64
    with pytest.raises(module().ProjectionRefused, match="^projection_refused$"):
        module().project_status(enabled=True, **inputs)


def test_pinned_body_must_follow_pinned_descriptor(inputs):
    body = json.loads(inputs["body_bytes"])
    body["records"][0]["task_id"] = "other-task"
    inputs["body_bytes"] = encode(body)
    inputs["body_sha256"] = digest(inputs["body_bytes"])
    with pytest.raises(module().ProjectionRefused, match="^projection_refused$"):
        module().project_status(enabled=True, **inputs)


def test_uuid_shaped_task_is_refused_even_if_approved(inputs):
    task = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    body = json.loads(inputs["body_bytes"])
    body["records"][0]["task_id"] = task
    descriptor = json.loads(inputs["descriptor_bytes"])
    descriptor["public_tasks"] = [task]
    inputs["body_bytes"] = encode(body)
    inputs["body_sha256"] = digest(inputs["body_bytes"])
    inputs["descriptor_bytes"] = encode(descriptor)
    inputs["descriptor_sha256"] = digest(inputs["descriptor_bytes"])
    inputs["approval_bytes"] = approval_for(inputs["descriptor_bytes"], tasks=(task,))
    inputs["expected_approval_sha256"] = digest(inputs["approval_bytes"])
    with pytest.raises(module().ProjectionRefused, match="^projection_refused$"):
        module().project_status(enabled=True, **inputs)


@pytest.mark.parametrize("field", ["body_bytes", "descriptor_bytes"])
def test_duplicate_json_keys_and_parser_details_refused(inputs, field):
    raw = b'{"synthetic-private-sentinel":1,"synthetic-private-sentinel":2}'
    inputs[field] = raw
    inputs[field.replace("bytes", "sha256")] = digest(raw)
    with pytest.raises(module().ProjectionRefused) as exc:
        module().project_status(enabled=True, **inputs)
    assert exc.value.args == ("projection_refused",)
    assert exc.value.__cause__ is None and exc.value.__context__ is None


@pytest.mark.parametrize("kind", ["missing", "wrong_pin", "duplicate_key", "extra_field", "duplicate_task",
                                  "duplicate_type", "wrong_schema", "wrong_binding"])
def test_approval_must_be_separate_pinned_closed_and_unambiguous(inputs, kind):
    if kind == "missing":
        inputs["approval_bytes"] = None
    elif kind == "wrong_pin":
        inputs["expected_approval_sha256"] = "0" * 64
    else:
        approval = json.loads(inputs["approval_bytes"])
        if kind == "extra_field": approval["private"] = "synthetic-private"
        elif kind == "duplicate_task": approval["approved_tasks"].append("public-task")
        elif kind == "duplicate_type": approval["approved_types"].append("message")
        elif kind == "wrong_schema": approval["schema"] = "other"
        elif kind == "wrong_binding": approval["descriptor_sha256"] = "0" * 64
        raw = (b'{"schema":"wd.mind-public-approval.v1","schema":"synthetic-private"}'
               if kind == "duplicate_key" else encode(approval))
        inputs["approval_bytes"], inputs["expected_approval_sha256"] = raw, digest(raw)
    with pytest.raises(module().ProjectionRefused) as exc:
        module().project_status(enabled=True, **inputs)
    assert exc.value.args == ("projection_refused",) and "synthetic-private" not in repr(exc.value)


def test_wrong_approval_pin_refuses_before_approval_json_parse(inputs, monkeypatch):
    m = module()
    inputs["expected_approval_sha256"] = "0" * 64
    monkeypatch.setattr(m.session, "_object", lambda *_: pytest.fail("untrusted approval was parsed"))
    with pytest.raises(m.ProjectionRefused):
        m.project_status(enabled=True, **inputs)


@pytest.mark.parametrize("kind", ["task", "type"])
def test_descriptor_scope_must_be_subset_even_when_approval_binds_its_hash(inputs, kind):
    descriptor = json.loads(inputs["descriptor_bytes"])
    descriptor["public_tasks" if kind == "task" else "public_types"].append(
        "other-task" if kind == "task" else "milestone")
    raw = encode(descriptor)
    inputs["descriptor_bytes"], inputs["descriptor_sha256"] = raw, digest(raw)
    approval = approval_for(raw)
    inputs["approval_bytes"], inputs["expected_approval_sha256"] = approval, digest(approval)
    with pytest.raises(module().ProjectionRefused):
        module().project_status(enabled=True, **inputs)


@pytest.mark.parametrize("task", ["review-task", "rco1-task", "code-reviewer", "prefixrco2", "sealed-task", "gold-task",
                                  "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"])
def test_approval_itself_cannot_allow_sensitive_task_shapes(inputs, task):
    descriptor = encode({"schema": "wd.mind-public-status-policy.v1", "public_tasks": [task],
                         "public_types": ["message"]})
    approval = approval_for(descriptor, tasks=(task,))
    body = json.loads(inputs["body_bytes"])
    body["records"][0]["task_id"] = task
    body_bytes = encode(body)
    inputs.update(descriptor_bytes=descriptor, descriptor_sha256=digest(descriptor), approval_bytes=approval,
                  expected_approval_sha256=digest(approval), body_bytes=body_bytes, body_sha256=digest(body_bytes))
    with pytest.raises(module().ProjectionRefused):
        module().project_status(enabled=True, **inputs)


def test_descriptor_may_use_a_strict_subset_of_independently_approved_labels(inputs):
    approval = approval_for(inputs["descriptor_bytes"], tasks=("public-task", "other-task"),
                            types=("message", "milestone"))
    inputs.update(approval_bytes=approval, expected_approval_sha256=digest(approval))
    assert module().project_status(enabled=True, **inputs)["records"][0]["task_id"] == "public-task"

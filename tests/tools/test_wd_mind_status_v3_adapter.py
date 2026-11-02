"""Synthetic test-root only; no live bridge or child processes."""
import hashlib
import importlib
import json

import pytest


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("ascii")


def sha(data):
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def case(tmp_path):
    from tools import wd_mind_status_projection_v3 as projection
    root = tmp_path / ".codex-audit" / "synthetic-mind-v3"
    root.mkdir(parents=True)
    descriptor = encoded({"schema": "wd.mind-public-status-policy.v1", "public_tasks": ["public-task"],
                          "public_types": ["message"]})
    approval = encoded({"schema": "wd.mind-public-approval.v1", "descriptor_sha256": sha(descriptor),
                        "approved_tasks": ["public-task"], "approved_types": ["message"]})
    body = encoded({"schema": "wd.mind-status-metadata.v1", "authority": "none",
        "scope": "in_memory_metadata_not_gate", "freshness": "UNKNOWN", "records": [
            {"time_utc": "2026-10-31T12:00:00.000000Z", "agent": "fable-5", "type": "message",
             "status": "done", "task_id": "public-task"}]})
    projected = projection.project_status(enabled=True, body_bytes=body, body_sha256=sha(body),
        descriptor_bytes=descriptor, descriptor_sha256=sha(descriptor), approval_bytes=approval,
        expected_approval_sha256=sha(approval))
    raw = encoded(projected)
    descriptor_path = root / "descriptor.json"
    descriptor_path.write_bytes(descriptor)
    return {"synthetic_root": root, "output_name": "synthetic-status.json", "projection_bytes": raw,
            "expected_projection_sha256": sha(raw), "descriptor_path": descriptor_path,
            "expected_descriptor_sha256": sha(descriptor), "approval_bytes": approval,
            "expected_approval_sha256": sha(approval)}


def adapter():
    return importlib.import_module("tools.wd_mind_status_v3_adapter")


def test_default_off_writes_nothing(case):
    assert adapter().publish_and_read(**case) == {"status": "OFF"}
    assert not (case["synthetic_root"] / case["output_name"]).exists()


def test_public_projection_is_exclusively_written_and_read(case):
    result = adapter().publish_and_read(enabled=True, **case)
    assert result["sha256"] == case["expected_projection_sha256"]
    assert result["projection"]["records"][0]["task_id"] == "public-task"
    assert (case["synthetic_root"] / case["output_name"]).read_bytes() == case["projection_bytes"]
    with pytest.raises(adapter().AdapterRefused):
        adapter().publish_and_read(enabled=True, **case)
    assert (case["synthetic_root"] / case["output_name"]).read_bytes() == case["projection_bytes"]


def test_reader_receives_all_caller_supplied_independent_pins(case, monkeypatch):
    module = adapter()
    original = module.reader.read_projection
    seen = []

    def observe(**kwargs):
        seen.append(kwargs)
        return original(**kwargs)

    monkeypatch.setattr(module.reader, "read_projection", observe)
    assert module.publish_and_read(enabled=True, **case)["sha256"] == case["expected_projection_sha256"]
    assert len(seen) == 1
    assert seen[0]["expected_projection_sha256"] == case["expected_projection_sha256"]
    assert seen[0]["expected_descriptor_sha256"] == case["expected_descriptor_sha256"]
    assert seen[0]["expected_approval_sha256"] == case["expected_approval_sha256"]
    assert seen[0]["approval_bytes"] == case["approval_bytes"]


def test_descriptor_changed_after_write_refuses_without_success(case, monkeypatch):
    module = adapter()
    original = module.reader.read_projection

    def change_then_read(**kwargs):
        case["descriptor_path"].write_bytes(b"{}")
        return original(**kwargs)

    monkeypatch.setattr(module.reader, "read_projection", change_then_read)
    with pytest.raises(module.AdapterRefused, match="^adapter_refused$"):
        module.publish_and_read(enabled=True, **case)
    # Caller-owned synthetic files may remain on refusal; this adapter never removes them.
    assert (case["synthetic_root"] / case["output_name"]).exists()


@pytest.mark.parametrize("missing", ["root", "descriptor"])
def test_missing_synthetic_path_does_not_leak_exception_context(case, missing):
    if missing == "root":
        case["synthetic_root"] = case["synthetic_root"].parent / "missing" / "synthetic-mind-v3"
    else:
        case["descriptor_path"] = case["synthetic_root"] / "synthetic-private-missing.json"
    with pytest.raises(adapter().AdapterRefused, match="^adapter_refused$") as caught:
        adapter().publish_and_read(enabled=True, **case)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


@pytest.mark.parametrize("kind", ["projection_pin", "descriptor_pin", "approval_pin", "approval_data",
                                  "bad_json", "noncanonical", "outside_root", "escape", "bad_root", "large"])
def test_bad_tuple_refuses_before_creating_output(case, tmp_path, kind):
    if kind == "projection_pin": case["expected_projection_sha256"] = "0" * 64
    elif kind == "descriptor_pin": case["expected_descriptor_sha256"] = "0" * 64
    elif kind == "approval_pin": case["expected_approval_sha256"] = "0" * 64
    elif kind == "approval_data": case["approval_bytes"] = b"{}"
    elif kind == "bad_json":
        case["projection_bytes"] = b"{"; case["expected_projection_sha256"] = sha(b"{")
    elif kind == "noncanonical":
        case["projection_bytes"] += b" "; case["expected_projection_sha256"] = sha(case["projection_bytes"])
    elif kind == "outside_root": case["descriptor_path"] = tmp_path / "not-in-root.json"
    elif kind == "escape": case["output_name"] = "../escape.json"
    elif kind == "bad_root": case["synthetic_root"] = tmp_path
    elif kind == "large":
        case["projection_bytes"] = b"x" * (1024 * 1024 + 1)
        case["expected_projection_sha256"] = sha(case["projection_bytes"])
    with pytest.raises(adapter().AdapterRefused, match="^adapter_refused$"):
        adapter().publish_and_read(enabled=True, **case)
    assert not (tmp_path / "escape.json").exists()
    assert not (tmp_path / ".codex-audit" / "synthetic-mind-v3" / "synthetic-status.json").exists()

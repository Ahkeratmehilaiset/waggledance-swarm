"""Synthetic tests of the inactive, caller-pinned public metadata reader."""
import hashlib
import importlib
import json
import os
from datetime import datetime, timezone

import pytest


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("ascii")


def digest(value):
    return hashlib.sha256(value).hexdigest()


def approval_for(descriptor):
    return encode({"schema": "wd.mind-public-approval.v1", "descriptor_sha256": digest(descriptor),
                   "approved_tasks": ["public-task"], "approved_types": ["message"]})


@pytest.fixture
def reader_args(tmp_path):
    body = encode({"schema": "wd.mind-status-metadata.v1", "authority": "none",
                   "scope": "in_memory_metadata_not_gate", "freshness": "UNKNOWN",
                   "records": [{"time_utc": "2026-10-31T12:00:00.000000Z", "agent": "fable-5",
                                "type": "message", "status": "done", "task_id": "public-task"}]})
    policy = encode({"schema": "wd.mind-public-status-policy.v1", "public_tasks": ["public-task"],
                     "public_types": ["message"]})
    body_path, descriptor_path = tmp_path / "body.json", tmp_path / "descriptor.json"
    body_path.write_bytes(body)
    descriptor_path.write_bytes(policy)
    approval = approval_for(policy)
    return {"body_path": body_path, "expected_body_sha256": digest(body), "descriptor_path": descriptor_path,
            "expected_descriptor_sha256": digest(policy), "approval_bytes": approval,
            "expected_approval_sha256": digest(approval)}


def module():
    return importlib.import_module("tools.wd_mind_status_reader_v3")


@pytest.mark.skipif(os.name != "nt", reason="Windows remote and device path boundary")
@pytest.mark.parametrize("path", [
    r"\\server\share\data.json",
    r"\\?\C:\data\file.json",
    r"\\.\pipe\synthetic",
    r"C:\data\file.json:alternate",
    r"C:\data\CON",
    r"C:\data\NUL\file.json",
    r"C:\data\COM¹.txt",
    r"C:\data\LPT²\file.json",
    "C:\\data\\trailing.\\file.json",
    "C:\\data\\bad?name.json",
    "C:\\data\\bad\x01name.json",
])
def test_remote_and_device_paths_refused_before_filesystem_access(monkeypatch, path):
    calls = []
    def unexpected_io(*args, **kwargs):
        calls.append(args)
        raise OSError("unexpected I/O")
    monkeypatch.setattr(module().os, "lstat", unexpected_io)
    with pytest.raises(ValueError):
        module()._read(path, "0" * 64, 1024)
    assert calls == []


def test_default_off_does_not_touch_poison():
    class Poison:
        def __fspath__(self):
            raise AssertionError("disabled reader touched a path")
    assert module().read_public(body_path=Poison(), descriptor_path=Poison(), expected_body_sha256=None,
                                expected_descriptor_sha256=None) == {"status": "OFF"}


@pytest.mark.parametrize("lane", ["codex-lead-1", "codex-tools-1", "claude-rco-1", "claude-rco-2", "fable-5"])
def test_five_declared_callers_obtain_the_same_public_view(reader_args, lane):
    result = module().read_public(enabled=True, **reader_args)
    assert result["schema"] == "wd.mind-status-projection.v3"
    assert result["coverage"] == "UNKNOWN"
    assert result["trust"] == {"source_authentication": "UNKNOWN", "source_freshness": "UNKNOWN",
                               "publisher_authentication": "UNKNOWN"}
    assert result["records"][0]["task_id"] == "public-task"
    assert lane not in json.dumps(result) or lane == "fable-5"  # source record, not caller identity


@pytest.mark.parametrize("which", ["body", "descriptor"])
@pytest.mark.parametrize("mutation", ["wrong_pin", "changed", "missing", "oversized", "symlink"])
def test_bad_inputs_fail_closed_without_echo(reader_args, tmp_path, which, mutation):
    m = module()
    path_key = "body_path" if which == "body" else "descriptor_path"
    hash_key = "expected_body_sha256" if which == "body" else "expected_descriptor_sha256"
    path = reader_args[path_key]
    if mutation == "wrong_pin":
        reader_args[hash_key] = "0" * 64
    elif mutation == "changed":
        path.write_bytes(b"synthetic-private-do-not-echo")
    elif mutation == "missing":
        reader_args[path_key] = tmp_path / "synthetic-private-missing"
    elif mutation == "oversized":
        limit = m.MAX_BODY_BYTES if which == "body" else m.MAX_DESCRIPTOR_BYTES
        path.write_bytes(b"x" * (limit + 1))
        reader_args[hash_key] = digest(path.read_bytes())
    else:
        link = tmp_path / (which + "-link")
        try:
            link.symlink_to(path)
        except (OSError, NotImplementedError):
            pytest.skip("synthetic symlink creation unavailable")
        reader_args[path_key] = link
    with pytest.raises(m.ReaderRefused) as exc:
        m.read_public(enabled=True, **reader_args)
    assert exc.value.args == ("reader_refused",)
    assert exc.value.__cause__ is None and exc.value.__context__ is None
    assert "synthetic-private" not in repr(exc.value)


def test_malformed_pinned_body_fails_without_parser_context(reader_args):
    raw = b'{"synthetic-private-marker": 1, "synthetic-private-marker": 2}'
    reader_args["body_path"].write_bytes(raw)
    reader_args["expected_body_sha256"] = digest(raw)
    with pytest.raises(module().ReaderRefused) as exc:
        module().read_public(enabled=True, **reader_args)
    assert exc.value.args == ("reader_refused",)
    assert exc.value.__cause__ is None and exc.value.__context__ is None


@pytest.fixture
def projection_args(reader_args, tmp_path):
    from tools import wd_mind_status_projection_v3 as v3
    body = reader_args["body_path"].read_bytes()
    descriptor = reader_args["descriptor_path"].read_bytes()
    projected = v3.project_status(enabled=True, body_bytes=body, body_sha256=reader_args["expected_body_sha256"],
                                  descriptor_bytes=descriptor,
                                  descriptor_sha256=reader_args["expected_descriptor_sha256"],
                                  approval_bytes=reader_args["approval_bytes"],
                                  expected_approval_sha256=reader_args["expected_approval_sha256"])
    raw = encode(projected)
    path = tmp_path / "projection.json"
    path.write_bytes(raw)
    return {"projection_path": path, "expected_projection_sha256": digest(raw),
            "descriptor_path": reader_args["descriptor_path"],
            "expected_descriptor_sha256": reader_args["expected_descriptor_sha256"],
            "approval_bytes": reader_args["approval_bytes"],
            "expected_approval_sha256": reader_args["expected_approval_sha256"]}


def test_projection_api_default_off_does_not_read(projection_args, monkeypatch):
    m = module()
    monkeypatch.setattr(m, "_read", lambda *a, **k: pytest.fail("default-off must not read"))
    assert m.read_projection(**projection_args) == {"status": "OFF"}


def test_caller_pinned_canonical_projection_is_revalidated(projection_args):
    result = module().read_projection(enabled=True, **projection_args)
    assert result["schema"] == "wd.mind-status-projection.v3"
    assert result["coverage"] == "UNKNOWN" and result["trust"]["source_freshness"] == "UNKNOWN"
    assert result["records"][0]["task_id"] == "public-task"


def test_synthetic_publisher_result_can_be_explicitly_serialized_for_reader(tmp_path):
    from sandbox.mind.step2.test_status_public_filter import ROSTER, event
    from tools import wd_mind_status_publish_once as publisher

    audit = tmp_path / "mind-status-private" / ".codex-audit"
    snapshots = audit / "snapshots"
    snapshots.mkdir(parents=True)
    data = encode(event()) + b"\n"
    policy = encode({"schema": "wd.q4-non-review-policy.v1", "approved_tasks": ["public-task"]})
    roster = encode({"lanes": [{"agent": k, "agent_uuid": v} for k, v in ROSTER.items() if k != "codex-tools-1"],
                     "tools_supervisor": {"agent": "codex-tools-1", "agent_uuid": ROSTER["codex-tools-1"]}})
    descriptor = encode({"schema": "wd.mind-public-status-policy.v1", "public_tasks": ["public-task"],
                         "public_types": ["message"]})
    approval = approval_for(descriptor)
    result = publisher.publish_once(enabled=True, request={"private_audit_root": audit,
        "private_storage_assumed": True, "snapshot_root": snapshots, "snapshot_bytes": data,
        "expected_snapshot_sha256": digest(data), "policy_bytes": policy, "policy_sha256": digest(policy),
        "roster_bytes": roster, "roster_sha256": digest(roster), "public_policy_bytes": descriptor,
        "public_policy_sha256": digest(descriptor), "approval_bytes": approval,
        "expected_approval_sha256": digest(approval), "as_of": datetime(2026, 11, 1, tzinfo=timezone.utc),
        "expected_previous_sha256": None})
    assert result["private_artifacts"] == "RETAINED"
    raw = encode(result["projection"])
    public_path, descriptor_path = tmp_path / "public.json", tmp_path / "public-policy.json"
    public_path.write_bytes(raw)
    descriptor_path.write_bytes(descriptor)
    assert module().read_projection(enabled=True, projection_path=public_path,
        expected_projection_sha256=digest(raw), descriptor_path=descriptor_path,
        expected_descriptor_sha256=digest(descriptor), approval_bytes=approval,
        expected_approval_sha256=digest(approval)) == result["projection"]
    assert str(audit).encode() not in raw and b"agent_uuid" not in raw and b"cursor" not in raw


@pytest.mark.parametrize("mutation", ["wrong_pin", "changed", "noncanonical", "duplicate", "extra", "coverage",
                                      "trust", "private", "descriptor_mismatch", "descriptor_policy"])
def test_projection_reader_refuses_invalid_or_unpinned_data(projection_args, mutation):
    m = module()
    path = projection_args["projection_path"]
    raw = path.read_bytes()
    if mutation == "wrong_pin":
        projection_args["expected_projection_sha256"] = "0" * 64
    elif mutation == "changed":
        path.write_bytes(b"synthetic-private-bad")
    elif mutation == "noncanonical":
        path.write_bytes(raw + b"\n")
        projection_args["expected_projection_sha256"] = digest(path.read_bytes())
    elif mutation == "duplicate":
        path.write_bytes(b'{"synthetic-private":1,"synthetic-private":2}')
        projection_args["expected_projection_sha256"] = digest(path.read_bytes())
    elif mutation in ("extra", "coverage", "trust", "private"):
        value = json.loads(raw)
        if mutation == "extra": value["unexpected"] = "synthetic-private"
        elif mutation == "coverage": value["coverage"] = "last_delta_only"
        elif mutation == "trust": value["trust"]["source_freshness"] = "VERIFIED"
        else: value["records"][0]["message"] = "synthetic-private"
        path.write_bytes(encode(value))
        projection_args["expected_projection_sha256"] = digest(path.read_bytes())
    elif mutation == "descriptor_mismatch":
        projection_args["expected_descriptor_sha256"] = "0" * 64
    else:
        descriptor = encode({"schema": "wd.mind-public-status-policy.v1", "public_tasks": ["other-task"],
                             "public_types": ["message"]})
        projection_args["descriptor_path"].write_bytes(descriptor)
        projection_args["expected_descriptor_sha256"] = digest(descriptor)
    with pytest.raises(m.ReaderRefused) as exc:
        m.read_projection(enabled=True, **projection_args)
    assert exc.value.args == ("reader_refused",)
    assert exc.value.__cause__ is None and exc.value.__context__ is None
    assert "synthetic-private" not in repr(exc.value)


@pytest.mark.parametrize("kind", ["body", "projection"])
@pytest.mark.parametrize("bad", ["missing", "wrong_pin", "descriptor_changed"])
def test_each_reader_requires_independent_approval_before_data_read(reader_args, projection_args, monkeypatch, kind, bad):
    m = module()
    args = dict(reader_args if kind == "body" else projection_args)
    data_path = args["body_path" if kind == "body" else "projection_path"]
    if bad == "missing":
        args["approval_bytes"] = None
    elif bad == "wrong_pin":
        args["expected_approval_sha256"] = "0" * 64
    else:
        altered = encode({"schema": "wd.mind-public-status-policy.v1", "public_tasks": ["other-task"],
                          "public_types": ["message"]})
        args["descriptor_path"].write_bytes(altered)
        args["expected_descriptor_sha256"] = digest(altered)
    original = m._read
    def guarded(path, *params):
        if path == data_path:
            pytest.fail("unapproved body/projection was read")
        return original(path, *params)
    monkeypatch.setattr(m, "_read", guarded)
    with pytest.raises(m.ReaderRefused):
        (m.read_public if kind == "body" else m.read_projection)(enabled=True, **args)

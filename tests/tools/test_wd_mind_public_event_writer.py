"""Synthetic-only public event emission: never contact the operator bridge."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from sandbox.mind.step2 import shared_capture_adapter as adapter
from tools import wd_mind_public_event_writer as subject


ROOT = Path(__file__).resolve().parents[2]


def _committed_blob(name):
    git = shutil.which("git")
    if git is None:
        pytest.fail("git is required to verify committed blob bytes")
    return subprocess.check_output([git, "show", "HEAD:" + name], cwd=ROOT)


def _frame(monkeypatch):
    fleet = json.loads((ROOT / "ops/windows/reboot/wd-fleet.json").read_bytes())
    lead = next(row for row in fleet["lanes"] if row["agent"] == "codex-lead-1")
    monkeypatch.setenv("AGENT_BRIDGE_AGENT", lead["agent"])
    monkeypatch.setenv("AGENT_BRIDGE_AGENT_UUID", lead["agent_uuid"])
    monkeypatch.setenv("AGENT_BRIDGE_SESSION_ID", "wd-reboot-synthetic")
    monkeypatch.setenv("AGENT_BRIDGE_RUN_ID", "wd-reboot-synthetic")
    monkeypatch.setenv("WD_REBOOT_EXPECTED_MANIFEST_HASH", "a" * 64)
    monkeypatch.delenv("WD_MIND_PUBLIC_ACTIVE_MANIFEST_SHA256", raising=False)
    return lead


def test_default_off_does_not_read_or_emit(monkeypatch):
    monkeypatch.setattr(subject, "_attest", lambda *_: pytest.fail("must not attest"))
    monkeypatch.setattr(subject, "_write", lambda *_: pytest.fail("must not write"))
    assert subject.publish() == {"status": "disabled"}


@pytest.mark.parametrize("bad", ["", "claude-rco-1", "unknown"])
def test_bad_or_review_agent_never_publishes(monkeypatch, bad):
    _frame(monkeypatch)
    monkeypatch.setenv("AGENT_BRIDGE_AGENT", bad)
    monkeypatch.setattr(subject, "_write", lambda *_: pytest.fail("must not write"))
    with pytest.raises(subject.PublicStatusRefused, match="^public_status_refused$"):
        subject.publish(enabled=True)


def test_unverified_or_mismatched_launcher_never_publishes(monkeypatch):
    _frame(monkeypatch)
    monkeypatch.setattr(subject, "_attest", lambda: {"pin_status": "manifest_verified_launcher_unknown", "observed_agent": "codex-lead-1"})
    monkeypatch.setattr(subject, "_write", lambda *_: pytest.fail("must not write"))
    with pytest.raises(subject.PublicStatusRefused, match="^public_status_refused$"):
        subject.publish(enabled=True)


def test_refusal_does_not_retain_attestation_exception_or_frames(monkeypatch):
    _frame(monkeypatch)
    def fail_with_private_diagnostic():
        raise ValueError("private diagnostic must not escape")
    monkeypatch.setattr(subject, "_attest", fail_with_private_diagnostic)
    with pytest.raises(subject.PublicStatusRefused, match="^public_status_refused$") as error:
        subject.publish(enabled=True)
    assert error.value.__cause__ is None and error.value.__context__ is None
    frames = []
    tb = error.value.__traceback__
    while tb:
        frames.append(tb.tb_frame.f_code.co_name)
        tb = tb.tb_next
    assert "_validated_event" not in frames and "fail_with_private_diagnostic" not in frames


def test_writer_failure_is_uncertain_and_never_retried(monkeypatch):
    lead = _frame(monkeypatch)
    monkeypatch.setattr(subject, "_attest", lambda: {"pin_status": "manifest_and_launcher_verified",
        "observed_agent": lead["agent"], "native_conversation_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"})
    calls = []
    def uncertain(event):
        calls.append(event)
        raise TimeoutError("private detail must not escape")
    monkeypatch.setattr(subject, "_write", uncertain)
    with pytest.raises(subject.PublicStatusWriteUncertain, match="^public_status_write_uncertain$") as error:
        subject.publish(enabled=True)
    assert len(calls) == 1 and error.value.__cause__ is None and error.value.__context__ is None
    assert "private detail" not in str(error.value)


@pytest.mark.parametrize("failure", [KeyboardInterrupt, SystemExit])
def test_interrupted_write_is_never_caught_by_ordinary_exception(monkeypatch, failure):
    lead = _frame(monkeypatch)
    monkeypatch.setattr(subject, "_attest", lambda: {"pin_status": "manifest_and_launcher_verified",
        "observed_agent": lead["agent"], "native_conversation_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"})
    calls = []
    def interrupted(event):
        calls.append(event)
        raise failure("private detail must not escape")
    monkeypatch.setattr(subject, "_write", interrupted)
    caught_by_ordinary = []
    with pytest.raises(BaseException) as error:
        try:
            subject.publish(enabled=True)
        except Exception:
            caught_by_ordinary.append(True)
            raise
    assert not caught_by_ordinary
    assert type(error.value).__name__ == "PublicStatusInterrupted"
    assert str(error.value) == "public_status_write_uncertain"
    assert error.value.__cause__ is None and error.value.__context__ is None
    assert len(calls) == 1


def test_git_lookup_is_portable_and_missing_git_is_explicit(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: "synthetic-git" if name == "git" else None)
    calls = []
    monkeypatch.setattr(subprocess, "check_output", lambda command, **kw: calls.append(command) or b"blob")
    assert _committed_blob("synthetic.json") == b"blob"
    assert calls == [["synthetic-git", "show", "HEAD:synthetic.json"]]
    monkeypatch.setattr(shutil, "which", lambda _: None)
    with pytest.raises(pytest.fail.Exception, match="git is required to verify committed blob bytes"):
        _committed_blob("synthetic.json")


@pytest.mark.parametrize("style", ["committed-lf", "checkout-crlf"])
@pytest.mark.parametrize("tamper", [None, "nonreview", "public", "roster"])
def test_exact_git_blobs_and_crlf_checkouts_only(tmp_path, monkeypatch, style, tamper):
    lead = _frame(monkeypatch)
    names = {"nonreview": "configs/mind/pilot_non_review_policy.json",
             "public": "configs/mind/pilot_public_status_policy.json",
             "roster": "ops/windows/reboot/wd-fleet.json"}
    for key, name in names.items():
        raw = _committed_blob(name)
        if style == "checkout-crlf":
            raw = raw.replace(b"\n", b"\r\n")
        if tamper == key:
            raw += b" "  # JSON-equivalent bytes are still untrusted.
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    monkeypatch.setattr(subject, "ROOT", tmp_path)
    monkeypatch.setattr(subject, "_attest", lambda: {"pin_status": "manifest_and_launcher_verified",
        "observed_agent": lead["agent"], "native_conversation_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"})
    emitted = []
    monkeypatch.setattr(subject, "_write", lambda event: emitted.append(event) or {"delivery_status": "canonical"})
    if tamper:
        with pytest.raises(subject.PublicStatusRefused, match="^public_status_refused$"):
            subject.publish(enabled=True)
        assert emitted == []
    else:
        assert subject.publish(enabled=True)["delivery_status"] == "canonical"
        assert len(emitted) == 1


@pytest.mark.parametrize("changed", ["agent", "agent_uuid", "task_id", "to", "message", "payload", "paths", "write_scope"])
def test_writer_receipt_must_match_fixed_public_envelope(monkeypatch, changed):
    lead = _frame(monkeypatch)
    event = {"agent": lead["agent"], "agent_uuid": lead["agent_uuid"], "type": "status",
             "task_id": subject.TASK, "status": "info", "to": "", "message": "", "payload": {"public_status": True}}
    written = dict(event, paths=[], write_scope=[])
    written[changed] = {"payload": {"public_status": True, "secret": "x"}, "paths": ["x"],
                        "write_scope": ["x"]}.get(changed, "wrong")
    written["_bridge_delivery"] = {"schema": "waggledance.bridge.delivery-receipt.v1", "accepted": True,
                                   "delivery_status": "canonical", "canonical_durable": True}
    monkeypatch.setattr(subject, "_pinned_helpers", lambda: (Path("synthetic-evidence"), Path("synthetic-writer")))
    monkeypatch.setattr(subject, "_powershell", lambda: "synthetic-powershell")
    monkeypatch.setattr(subject.subprocess, "run", lambda *a, **kw: SimpleNamespace(stdout=json.dumps(written)))
    with pytest.raises(ValueError):
        subject._write(event)


@pytest.mark.parametrize("extra", [{"recipient": "fable-5"}, {"unknown": "private"}, {"paths": ["private"]}])
def test_writer_receipt_with_extra_routing_or_private_fields_is_rejected(monkeypatch, extra):
    lead = _frame(monkeypatch)
    event = {"agent": lead["agent"], "agent_uuid": lead["agent_uuid"], "type": "status",
             "task_id": subject.TASK, "status": "info", "to": "", "message": "", "payload": {"public_status": True}}
    written = dict(event, paths=[], write_scope=[], **extra) if "paths" not in extra else dict(event, paths=extra["paths"], write_scope=[])
    written["_bridge_delivery"] = {"schema": "waggledance.bridge.delivery-receipt.v1", "accepted": True,
                                   "delivery_status": "canonical", "canonical_durable": True}
    monkeypatch.setattr(subject, "_pinned_helpers", lambda: (Path("synthetic-evidence"), Path("synthetic-writer")))
    monkeypatch.setattr(subject, "_powershell", lambda: "synthetic-powershell")
    monkeypatch.setattr(subject.subprocess, "run", lambda *a, **kw: SimpleNamespace(stdout=json.dumps(written)))
    with pytest.raises(ValueError):
        subject._write(event)


def test_writer_command_contains_only_fixed_public_fields(monkeypatch):
    lead = _frame(monkeypatch)
    monkeypatch.setenv("WD_MIND_PUBLIC_ACTIVE_MANIFEST_SHA256", "b" * 64)
    monkeypatch.setenv("AGENT_BRIDGE_ROLE", "must-not-leave-process")
    monkeypatch.setenv("AGENT_BRIDGE_CAPABILITIES", "must-not-leave-process")
    event = {"agent": lead["agent"], "agent_uuid": lead["agent_uuid"], "type": "status",
             "task_id": subject.TASK, "status": "info", "to": "", "message": "", "payload": {"public_status": True}}
    written = dict(event, paths=[], write_scope=[], _bridge_delivery={"schema": "waggledance.bridge.delivery-receipt.v1",
                   "accepted": True, "delivery_status": "canonical", "canonical_durable": True})
    seen = []
    def fake_run(command, **kwargs):
        seen.append((command, kwargs))
        return SimpleNamespace(stdout=json.dumps(written))
    monkeypatch.setattr(subject, "_pinned_helpers", lambda: (Path("synthetic-evidence"), Path("synthetic-writer")))
    monkeypatch.setattr(subject, "_powershell", lambda: "synthetic-powershell")
    monkeypatch.setattr(subject.subprocess, "run", fake_run)
    assert subject._write(event) == {"delivery_status": "canonical", "canonical_durable": True}
    command, kwargs = seen[0]
    assert command[command.index("-PayloadJson") + 1] == '{"public_status":true}'
    assert all(flag not in command for flag in ("-To", "-Message", "-Paths", "-WriteScope", "-RequestId"))
    assert "AGENT_BRIDGE_ROLE" not in kwargs["env"] and "AGENT_BRIDGE_CAPABILITIES" not in kwargs["env"]
    assert kwargs["env"]["WD_REBOOT_EXPECTED_MANIFEST_HASH"] == "b" * 64


@pytest.mark.parametrize("tamper", ["none", "anchor", "evidence", "writer", "dependency", "registry", "bin"])
def test_helper_bytes_are_checked_against_inherited_manifest(tmp_path, monkeypatch, tamper):
    bundle = tmp_path / "bundle"
    binary = bundle / "tools-bootstrap" / ".agent-bridge" / "bin"
    binary.mkdir(parents=True)
    names = ("Get-BridgeExecutionEvidence.ps1", "Write-AgentEvent.ps1")
    files = {}
    for name in names:
        data = ("synthetic-" + name).encode()
        (binary / name).write_bytes(data)
        files["tools-bootstrap/.agent-bridge/bin/" + name] = hashlib.sha256(data).hexdigest().upper()
    dependency = binary / "BridgeEventClassifier.ps1"
    dependency.write_bytes(b"synthetic-dependency")
    files["tools-bootstrap/.agent-bridge/bin/BridgeEventClassifier.ps1"] = hashlib.sha256(b"synthetic-dependency").hexdigest().upper()
    registry = bundle / "tools-bootstrap/configs/bridge_identity_registry.json"
    registry.parent.mkdir(parents=True)
    registry.write_bytes(b"synthetic-registry")
    files["tools-bootstrap/configs/bridge_identity_registry.json"] = hashlib.sha256(b"synthetic-registry").hexdigest().upper()
    raw = json.dumps({"files": files}, sort_keys=True).encode()
    (bundle / "deployment-manifest.json").write_bytes(raw)
    anchor = hashlib.sha256(raw).hexdigest()
    if tamper == "evidence":
        (binary / names[0]).write_bytes(b"tampered")
    elif tamper == "writer":
        (binary / names[1]).write_bytes(b"tampered")
    elif tamper == "dependency":
        dependency.write_bytes(b"tampered")
    elif tamper == "registry":
        registry.write_bytes(b"tampered")
    elif tamper == "anchor":
        anchor = "0" * 64
    elif tamper == "bin":
        binary = tmp_path
    monkeypatch.setenv("WD_BRIDGE_BIN", str(binary))
    monkeypatch.setenv("WD_REBOOT_EXPECTED_MANIFEST_HASH", anchor)
    if tamper == "none":
        assert subject._pinned_helpers() == (binary / names[0], binary / names[1])
    else:
        with pytest.raises((ValueError, OSError, KeyError)):
            subject._pinned_helpers()


def test_queued_receipt_requires_a_retained_wal(monkeypatch):
    lead = _frame(monkeypatch)
    event = {"agent": lead["agent"], "agent_uuid": lead["agent_uuid"], "type": "status",
             "task_id": subject.TASK, "status": "info", "to": "", "message": "", "payload": {"public_status": True}}
    delivery = {"schema": "waggledance.bridge.delivery-receipt.v1", "accepted": True,
                "delivery_status": "queued", "canonical_durable": False}
    written = dict(event, paths=[], write_scope=[], _bridge_delivery=delivery)
    monkeypatch.setattr(subject, "_pinned_helpers", lambda: (Path("synthetic-evidence"), Path("synthetic-writer")))
    monkeypatch.setattr(subject, "_powershell", lambda: "synthetic-powershell")
    monkeypatch.setattr(subject.subprocess, "run", lambda *a, **kw: SimpleNamespace(stdout=json.dumps(written)))
    with pytest.raises(ValueError):
        subject._write(event)
    delivery.update(retained_wal_path="synthetic-wal", retained_wal_sha256="a" * 64)
    assert subject._write(event) == {"delivery_status": "queued", "canonical_durable": False,
                                    "retained_wal_sha256": "a" * 64}


def test_synthetic_public_broadcast_projects_and_private_variants_exclude(monkeypatch):
    from sandbox.mind import mind_capture_producer as producer
    from sandbox.mind.step2.test_shared_capture_adapter import NOW, snapshot
    from tools.bridge_compact_view import event_id

    lead = _frame(monkeypatch)
    monkeypatch.setattr(subject, "_attest", lambda: {"pin_status": "manifest_and_launcher_verified", "observed_agent": lead["agent"], "native_conversation_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"})
    written = []
    monkeypatch.setattr(subject, "_write", lambda event: written.append(event) or {"delivery_status": "canonical", "canonical_durable": True})
    assert subject.publish(enabled=True)["delivery_status"] == "canonical"
    event = dict(written[0], ts_utc="2026-10-31T12:00:10Z", agent_uuid=lead["agent_uuid"], paths=[], write_scope=[])
    policy = (ROOT / "configs/mind/pilot_non_review_policy.json").read_bytes()
    roster = (ROOT / "ops/windows/reboot/wd-fleet.json").read_bytes()
    public = (ROOT / "configs/mind/pilot_public_status_policy.json").read_bytes()
    tasks, _ = adapter.strict_pins(policy, hashlib.sha256(policy).hexdigest(), roster, hashlib.sha256(roster).hexdigest())
    public_tasks, public_types = adapter.public_status_policy(public, hashlib.sha256(public).hexdigest(), tasks)
    assert event["payload"] == {"public_status": True}
    assert event["message"] == event["to"] == ""
    assert event["paths"] == event["write_scope"] == []
    assert adapter._public_status_event(event, public_tasks, public_types)
    monkeypatch.setattr(adapter, "_utc_now", lambda: NOW)
    row = {"event": event, "ref": event_id(event), "row_sha256": producer.row_digest(event), "source": "detail"}
    projection = adapter.adapt_rows(
        [row], snapshot(), ttl_seconds=120, policy_bytes=policy, policy_sha256=hashlib.sha256(policy).hexdigest(),
        roster_bytes=roster, roster_sha256=hashlib.sha256(roster).hexdigest(),
        _public_tasks=public_tasks, _public_types=public_types,
    )["projection"]
    assert projection["accepted"] == 1
    assert projection["records"][0]["meta"]["agent"] == lead["agent"]
    assert projection["records"][0]["meta"]["task_id"] == subject.TASK
    for change in ({"message": "private"}, {"to": "fable-5"}, {"paths": ["private"]},
                   {"write_scope": ["private"]}, {"recipient": "fable-5"},
                   {"payload": {"public_status": True, "path": "private"}}):
        assert not adapter._public_status_event(dict(event, **change), public_tasks, public_types)


def test_active_helper_manifest_can_differ_from_candidate_manifest(tmp_path, monkeypatch):
    bundle = tmp_path / "active"
    binary = bundle / "tools-bootstrap/.agent-bridge/bin"
    binary.mkdir(parents=True)
    files = {}
    for name in ("Get-BridgeExecutionEvidence.ps1", "Write-AgentEvent.ps1"):
        blob = ("synthetic-" + name).encode()
        (binary / name).write_bytes(blob)
        files["tools-bootstrap/.agent-bridge/bin/" + name] = hashlib.sha256(blob).hexdigest()
    registry = bundle / "tools-bootstrap/configs/bridge_identity_registry.json"
    registry.parent.mkdir(parents=True)
    registry.write_bytes(b"synthetic-registry")
    files["tools-bootstrap/configs/bridge_identity_registry.json"] = hashlib.sha256(b"synthetic-registry").hexdigest()
    raw = json.dumps({"files": files}).encode()
    (bundle / "deployment-manifest.json").write_bytes(raw)
    monkeypatch.setenv("WD_BRIDGE_BIN", str(binary))
    monkeypatch.setenv("WD_REBOOT_EXPECTED_MANIFEST_HASH", "a" * 64)
    monkeypatch.setenv("WD_MIND_PUBLIC_ACTIVE_MANIFEST_SHA256", hashlib.sha256(raw).hexdigest())
    assert subject._pinned_helpers() == (binary / "Get-BridgeExecutionEvidence.ps1", binary / "Write-AgentEvent.ps1")


def test_active_anchor_is_passed_to_attestation_subprocess(monkeypatch):
    seen = []
    monkeypatch.setenv("WD_REBOOT_EXPECTED_MANIFEST_HASH", "a" * 64)
    monkeypatch.setenv("WD_MIND_PUBLIC_ACTIVE_MANIFEST_SHA256", "b" * 64)
    monkeypatch.setattr(subject, "_pinned_helpers", lambda: (Path("synthetic-evidence"), Path("synthetic-writer")))
    monkeypatch.setattr(subject, "_powershell", lambda: "synthetic-powershell")
    def fake_run(command, **kwargs):
        seen.append(kwargs["env"]["WD_REBOOT_EXPECTED_MANIFEST_HASH"])
        return SimpleNamespace(stdout="{}")
    monkeypatch.setattr(subject.subprocess, "run", fake_run)
    subject._attest()
    assert seen == ["b" * 64]


@pytest.mark.parametrize("fault", ["none", "candidate_pin", "manifest", "writer", "policy", "public", "fleet"])
def test_candidate_inputs_are_checked_against_candidate_manifest(tmp_path, monkeypatch, fault):
    bundle = tmp_path / "candidate"
    code = bundle / "tools-bootstrap"
    values = {
        "tools-bootstrap/tools/wd_mind_public_event_writer.py": b"synthetic-writer",
        "tools-bootstrap/configs/mind/pilot_non_review_policy.json": b"synthetic-policy",
        "tools-bootstrap/configs/mind/pilot_public_status_policy.json": b"synthetic-public",
        "wd-fleet.json": b"synthetic-fleet",
    }
    for name, raw in values.items():
        target = bundle / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
    manifest = json.dumps({"files": {name: hashlib.sha256(raw).hexdigest() for name, raw in values.items()}}).encode()
    (bundle / "deployment-manifest.json").write_bytes(manifest)
    monkeypatch.setattr(subject, "ROOT", code)
    monkeypatch.setenv("WD_REBOOT_EXPECTED_MANIFEST_HASH", "0" * 64 if fault == "candidate_pin" else hashlib.sha256(manifest).hexdigest())
    targets = {"manifest": bundle / "deployment-manifest.json",
               "writer": bundle / "tools-bootstrap/tools/wd_mind_public_event_writer.py",
               "policy": bundle / "tools-bootstrap/configs/mind/pilot_non_review_policy.json",
               "public": bundle / "tools-bootstrap/configs/mind/pilot_public_status_policy.json",
               "fleet": bundle / "wd-fleet.json"}
    if fault in targets:
        targets[fault].write_bytes(targets[fault].read_bytes() + b"!")
    if fault == "none":
        assert subject._policy_fleet_bytes() == (b"synthetic-policy", b"synthetic-public", b"synthetic-fleet")
    else:
        with pytest.raises((ValueError, OSError)):
            subject._policy_fleet_bytes()

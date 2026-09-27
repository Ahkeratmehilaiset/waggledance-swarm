"""Real writer regression tests; every invocation uses an isolated bridge runtime."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]
SHELLS = list(dict.fromkeys(filter(None, [shutil.which("pwsh"), shutil.which("powershell.exe")])))
pytestmark = pytest.mark.skipif(os.name != "nt", reason="canonical writer append is Windows-only")


def write(shell, runtime, *args):
    env = {k: v for k, v in os.environ.items() if not k.startswith("AGENT_BRIDGE_")}
    env["AGENT_BRIDGE_RUNTIME_ROOT"] = str(runtime)
    return subprocess.run(
        [shell, "-NoProfile", "-NonInteractive", "-File",
         str(ROOT / ".agent-bridge/bin/Write-AgentEvent.ps1"),
         "-Agent", "operator", "-TaskId", "fixture/contract-guards",
         "-SessionId", "current-session", "-RunId", "current-run", *args],
        env=env, capture_output=True, text=True, timeout=40,
    )


def rows(runtime):
    path = runtime / "shared/events.jsonl"
    return path.read_bytes() if path.exists() else b""


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("key", ["agent", "agent_uuid", "session_id", "run_id", "task_id"])
def test_payload_cannot_poison_writer_identity(tmp_path, shell, key):
    result = write(shell, tmp_path, "-Type", "status", "-Status", "evidence",
                   "-PayloadJson", json.dumps({key: "different"}))
    assert result.returncode != 0, result.stdout
    assert "payload contract field" in result.stderr
    assert not rows(tmp_path)
    assert not (tmp_path / "shared/last_operator.json").exists()


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("kind,status", [("status", "ready_for_fresh_restart"),
    ("intent", "ready"), ("wake_request", "request"),
    ("message", "received"), ("message", "seen"), ("message", "acknowledged")])
def test_bound_nonanswer_is_refused_before_append(tmp_path, shell, kind, status):
    created = write(shell, tmp_path, "-Type", "message", "-Status", "request",
                    "-To", "operator", "-RequestId", "fixture-guard-request")
    assert created.returncode == 0, created.stderr
    before = rows(tmp_path)
    request = json.loads(before.splitlines()[-1])
    result = write(shell, tmp_path, "-Type", kind, "-Status", status,
                   "-To", "operator", "-ReplyToEventJson", json.dumps(request))
    assert result.returncode != 0, result.stdout
    assert "substantive answer" in result.stderr
    assert rows(tmp_path) == before


@pytest.mark.parametrize("shell", SHELLS)
def test_legacy_poisoned_last_identity_is_not_frozen_into_new_request(tmp_path, shell):
    shared = tmp_path / "shared"
    shared.mkdir()
    (shared / "last_peer.json").write_text(json.dumps({
        "agent": "peer", "agent_uuid": "11111111-2222-3333-4444-555555555555",
        "session_id": "current", "run_id": "run", "payload": {"session_id": "old"}}))
    result = write(shell, tmp_path, "-Type", "message", "-Status", "request", "-To", "peer")
    assert result.returncode != 0, result.stdout
    assert "conflicting responder identity" in result.stderr
    assert not rows(tmp_path)


@pytest.mark.parametrize("shell", SHELLS)
def test_matching_identity_and_legacy_nonce_remain_supported(tmp_path, shell):
    result = write(shell, tmp_path, "-Type", "message", "-Status", "request", "-To", "operator",
                   "-PayloadJson", json.dumps({"session_id": "current-session", "nonce": "n1"}))
    assert result.returncode == 0, result.stderr
    request = json.loads(rows(tmp_path).splitlines()[-1])
    reply = write(shell, tmp_path, "-Type", "message", "-Status", "answered", "-To", "operator",
                  "-ReplyToEventJson", json.dumps(request), "-PayloadJson", '{"nonce":"n1"}')
    assert reply.returncode == 0, reply.stderr
    assert len(rows(tmp_path).splitlines()) == 2


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("field", ["request_id", "request_digest", "expected_responders"])
def test_generated_binding_cannot_conflict_with_payload(tmp_path, shell, field):
    # A recorded target makes expected_responders an actual generated field.
    shared = tmp_path / "shared"
    shared.mkdir()
    (shared / "last_peer.json").write_text(json.dumps({
        "agent": "peer", "agent_uuid": "11111111-2222-3333-4444-555555555555",
        "session_id": "current", "run_id": "run"}))
    result = write(shell, tmp_path, "-Type", "message", "-Status", "request", "-To", "peer",
                   "-RequestId", "generated-request", "-PayloadJson", json.dumps({field: "different"}))
    assert result.returncode != 0, result.stdout
    assert "payload contract field" in result.stderr
    assert not rows(tmp_path)


@pytest.mark.parametrize("shell", SHELLS)
def test_observational_identity_is_allowed_under_result(tmp_path, shell):
    result = write(shell, tmp_path, "-Type", "status", "-Status", "evidence",
                   "-PayloadJson", '{"result":{"session_id":"a-measured-peer-session"}}')
    assert result.returncode == 0, result.stderr
    assert len(rows(tmp_path).splitlines()) == 1


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("status", ["blocked", "ready", "changes_requested", "review_requested", "proposal_accepted"])
def test_bound_substantive_request_like_status_is_an_answer(tmp_path, shell, status):
    created = write(shell, tmp_path, "-Type", "message", "-Status", "request", "-To", "operator")
    assert created.returncode == 0, created.stderr
    request = json.loads(rows(tmp_path).splitlines()[-1])
    result = write(shell, tmp_path, "-Type", "message", "-Status", status, "-To", "operator",
                   "-ReplyToEventJson", json.dumps(request))
    assert result.returncode == 0, result.stderr
    answer = json.loads(rows(tmp_path).splitlines()[-1])
    assert answer["in_reply_to_request_id"] == request["request_id"]
    from waggledance.core.bridge_request_contract import reply_matches_request
    assert reply_matches_request(request, answer, "operator")


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("field", ["request_id", "request_digest", "expected_responders",
    "in_reply_to_request_id", "in_reply_to_request_digest", "in_reply_to_requester"])
def test_payload_only_modern_binding_cannot_bypass_writer(tmp_path, shell, field):
    result = write(shell, tmp_path, "-Type", "status", "-Status", "evidence",
                   "-To", "peer", "-PayloadJson", json.dumps({field: "forged-binding"}))
    assert result.returncode != 0, result.stdout
    assert "payload contract field" in result.stderr
    assert not rows(tmp_path)

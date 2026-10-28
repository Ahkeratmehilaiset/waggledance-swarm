# SPDX-License-Identifier: BUSL-1.1
"""P1 lifecycle regressions on an isolated fixture bridge, never the live bridge."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from test_bridge_write_agent_event import (
    WINDOWS_APPEND_V1,
    _CloseoutWorld,
    _native_shell,
)


@WINDOWS_APPEND_V1
def test_p1_completed_bound_request_is_not_superseded(tmp_path: Path) -> None:
    """A correlated substantive answer ends the request; a new revision is no reason to close it."""
    world = _CloseoutWorld(
        tmp_path,
        _native_shell("powershell"),
        requirements={
            "nonce": "p1-synthetic-nonce",
            "task_revision": 1,
            "request_lifecycle": "wd.request-lifecycle.p1",
        },
    )
    completed = world.write(
        {
            **world.tools,
            "-Type": "message",
            "-Status": "answered",
            "-TaskId": world.request["task_id"],
            "-To": "codex-lead-1",
            "-ReplyToEventJson": json.dumps(world.request),
            "-Message": "synthetic completed delivery",
        },
        world.tools_frame,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    before = world.events()
    assert len(before) == 2
    assert before[-1]["in_reply_to_request_id"] == world.request["request_id"]

    closeout = world.close(world.closeout_arguments(
        PayloadJson=json.dumps({"superseded_by_request_id": "fixture-p1-successor-v2",
                                "superseded_by_task_revision": 2})
    ))
    assert closeout.returncode != 0, (
        "P1 must refuse to supersede an already answered request before accepting another event",
        closeout.stdout,
        closeout.stderr,
    )
    assert world.events() == before
    assert "answered or closed revision" in closeout.stderr


@WINDOWS_APPEND_V1
def test_p1_a_second_superseded_closeout_adds_no_event(tmp_path: Path) -> None:
    """One obsolete open revision receives one correlated closure, not duplicate closure traffic."""
    world = _CloseoutWorld(
        tmp_path,
        _native_shell("powershell"),
        requirements={
            "nonce": "p1-synthetic-nonce",
            "task_revision": 1,
            "request_lifecycle": "wd.request-lifecycle.p1",
        },
    )
    first = world.close(world.closeout_arguments(
        PayloadJson=json.dumps({"superseded_by_request_id": "fixture-p1-successor-v2",
                                "superseded_by_task_revision": 2})
    ))
    assert first.returncode == 0, first.stdout + first.stderr
    before = world.events()
    assert len(before) == 2
    assert before[-1]["status"] == "superseded"

    duplicate = world.close(world.closeout_arguments(
        PayloadJson=json.dumps({"superseded_by_request_id": "fixture-p1-successor-v2",
                                "superseded_by_task_revision": 2})
    ))
    assert duplicate.returncode != 0, (
        "P1 must refuse a second closure of a request that is no longer open",
        duplicate.stdout,
        duplicate.stderr,
    )
    assert world.events() == before
    assert "answered or closed revision" in duplicate.stderr


def _p1_world(tmp_path: Path, **metadata) -> _CloseoutWorld:
    return _CloseoutWorld(
        tmp_path,
        _native_shell("powershell"),
        requirements={
            "nonce": "p1-synthetic-nonce",
            "task_revision": 1,
            "request_lifecycle": "wd.request-lifecycle.p1",
            **metadata,
        },
    )


def _successor_arguments(world, **metadata) -> dict:
    return {
        **world.lead,
        "-Type": "message",
        "-Status": "requested",
        "-TaskId": world.request["task_id"],
        "-To": world.request["to"],
        "-Message": "synthetic P1 successor",
        "-RequestId": "fixture-p1-successor-v2",
        "-PayloadJson": json.dumps({
            "nonce": "p1-synthetic-nonce-v2",
            "task_revision": 2,
            "request_lifecycle": "wd.request-lifecycle.p1",
            "previous_request_id": world.request["request_id"],
            "previous_request_digest": world.request["request_digest"],
            **metadata,
        }),
    }


def _p1_close(world):
    return world.close(world.closeout_arguments(
        PayloadJson=json.dumps({
            "superseded_by_request_id": "fixture-p1-successor-v2",
            "superseded_by_task_revision": 2,
        })
    ))


@WINDOWS_APPEND_V1
def test_p1_open_request_blocks_an_unclosed_successor(tmp_path: Path) -> None:
    world = _p1_world(tmp_path)
    before = world.events()
    result = world.write(_successor_arguments(world), world.lead_frame)
    assert result.returncode != 0, result.stdout + result.stderr
    assert world.events() == before
    assert "already active for this logical task" in result.stderr


@WINDOWS_APPEND_V1
def test_p1_explicit_close_then_linked_successor_preserves_both_receipts(tmp_path: Path) -> None:
    world = _p1_world(tmp_path)
    closed = _p1_close(world)
    assert closed.returncode == 0, closed.stdout + closed.stderr
    result = world.write(_successor_arguments(world), world.lead_frame)
    assert result.returncode == 0, result.stdout + result.stderr
    events = world.events()
    assert len(events) == 3
    assert events[1]["type"] == "message"
    assert events[1]["status"] == "superseded"
    assert events[1]["in_reply_to_request_id"] == world.request["request_id"]
    successor = events[2]
    for field in ("request_lifecycle", "previous_request_id", "previous_request_digest",
                  "action_required", "fyi"):
        assert successor[field] == successor["payload"][field]
    assert successor["request_id"] == "fixture-p1-successor-v2"
    assert successor["action_required"] is True and successor["fyi"] is False
    assert successor["payload"]["task_revision"] == 2


@WINDOWS_APPEND_V1
def test_p1_answered_predecessor_can_link_without_a_superseded_event(tmp_path: Path) -> None:
    world = _p1_world(tmp_path)
    answer = world.write({
        **world.tools, "-Type": "message", "-Status": "answered",
        "-TaskId": world.request["task_id"], "-To": "codex-lead-1",
        "-ReplyToEventJson": json.dumps(world.request),
    }, world.tools_frame)
    assert answer.returncode == 0, answer.stdout + answer.stderr
    result = world.write(_successor_arguments(world), world.lead_frame)
    assert result.returncode == 0, result.stdout + result.stderr
    assert len(world.events()) == 3
    assert all(row["status"] != "superseded" for row in world.events())


@WINDOWS_APPEND_V1
@pytest.mark.parametrize("metadata", [
    {"previous_request_digest": "f" * 64},
    {"previous_request_id": "not-the-predecessor"},
    {"task_revision": 3},
], ids=["digest", "id", "skipped-revision"])
def test_p1_link_mismatch_fails_before_acceptance(tmp_path: Path, metadata) -> None:
    world = _p1_world(tmp_path)
    closed = _p1_close(world)
    assert closed.returncode == 0, closed.stdout + closed.stderr
    before = world.events()
    result = world.write(_successor_arguments(world, **metadata), world.lead_frame)
    assert result.returncode != 0, result.stdout + result.stderr
    assert world.events() == before
    assert "predecessor ID, digest or revision" in result.stderr


@WINDOWS_APPEND_V1
def test_p1_unknown_accepted_queue_state_holds_without_a_new_event(tmp_path: Path) -> None:
    world = _p1_world(tmp_path)
    pending = world.runtime_root / "spool" / "accepted-v1" / "pending"
    pending.mkdir(parents=True, exist_ok=True)
    blocker = pending / "unknown-p1-queue-entry"
    blocker.write_text("synthetic opaque accepted-state blocker", encoding="utf-8")
    before = world.events()
    result = _p1_close(world)
    assert result.returncode != 0, result.stdout + result.stderr
    assert world.events() == before
    assert blocker.read_text(encoding="utf-8") == "synthetic opaque accepted-state blocker"


@WINDOWS_APPEND_V1
def test_p1_partial_canonical_history_is_unknown_not_open(tmp_path: Path) -> None:
    world = _p1_world(tmp_path)
    log = world.shared / "events.jsonl"
    before = log.read_bytes() + b'{"unfinished":'
    log.write_bytes(before)
    result = _p1_close(world)
    assert result.returncode != 0, result.stdout + result.stderr
    assert log.read_bytes() == before


@WINDOWS_APPEND_V1
def test_p1_descriptive_fyi_cannot_hide_a_bound_request(tmp_path: Path) -> None:
    from test_bridge_write_agent_event import _closes

    world = _p1_world(tmp_path, action_required=False, fyi=True)
    assert world.request["action_required"] is False
    assert world.request["fyi"] is True
    assert not _closes(world.request, world.events())


@WINDOWS_APPEND_V1
@pytest.mark.parametrize("metadata", [
    {"action_required": "false", "fyi": True},
    {"action_required": False, "fyi": False},
    {"artifact": {"path": "relative.json", "bytes": 1, "sha256": "a" * 64, "owner": "codex-tools-1"}},
    {"artifact": {"path": "C:\\synthetic\\report.json", "bytes": True,
                  "sha256": "a" * 64, "owner": "codex-tools-1"}},
    {"artifact": {"path": "C:\\synthetic\\report.json", "bytes": 1,
                  "sha256": "short", "owner": "codex-tools-1"}},
], ids=["string-flag", "inconsistent-flags", "relative-artifact", "bool-size", "short-hash"])
def test_p1_invalid_metadata_is_refused(tmp_path: Path, metadata) -> None:
    world = _p1_world(tmp_path)
    closed = _p1_close(world)
    assert closed.returncode == 0, closed.stdout + closed.stderr
    before = world.events()
    result = world.write(_successor_arguments(world, **metadata), world.lead_frame)
    assert result.returncode != 0, result.stdout + result.stderr
    assert world.events() == before
    assert "P1 HOLD" in result.stderr


@WINDOWS_APPEND_V1
def test_p1_artifact_descriptor_is_bound_and_discoverable_without_dereference(tmp_path: Path) -> None:
    from test_bridge_write_agent_event import _run_bridge_script

    artifact = {
        "path": str(tmp_path / "not-opened-by-the-writer.json"),
        "bytes": 123,
        "sha256": "a" * 64,
        "owner": "codex-tools-1",
    }
    world = _p1_world(tmp_path, artifact=artifact)
    assert world.request["artifact"] == world.request["payload"]["artifact"] == artifact
    assert not Path(artifact["path"]).exists()  # Descriptor is a claim, not a verified file receipt.
    inventory = _run_bridge_script(
        world.root, world.runtime_root, "Get-BridgeRequestInventory.ps1",
        "-Agent", "codex-lead-1", "-RequestId", world.request["request_id"],
        "-NoCache", shell=world.shell,
    )
    assert inventory.returncode == 0, inventory.stdout + inventory.stderr
    result = json.loads(inventory.stdout)
    assert result["matched_count"] == 1
    assert result["requests"][0]["artifact"] == artifact
    assert result["requests"][0]["answer_state"] == "not_evaluated"


@WINDOWS_APPEND_V1
def test_p1_closeout_does_not_clear_a_recognized_rco_veto(tmp_path: Path, monkeypatch) -> None:
    from test_bridge_write_agent_event import _peer_gate

    world = _p1_world(tmp_path)
    veto = {
        "ts_utc": world.request["ts_utc"], "agent": "claude-rco-2",
        "agent_uuid": "76739997-0058-41a2-8514-78ff295537aa",
        "type": "finding", "status": "changes_requested",
        "task_id": world.request["task_id"], "to": "codex-lead-1",
        "message": "synthetic retained veto", "payload": {"head": "a" * 40},
    }
    before = _peer_gate([veto], monkeypatch)
    assert not before["clear_to_merge"]
    closed = _p1_close(world)
    assert closed.returncode == 0, closed.stdout + closed.stderr
    after = _peer_gate([veto, *world.events()], monkeypatch)
    assert not after["clear_to_merge"]
    assert after["latest_blocking_event"] == before["latest_blocking_event"]


@WINDOWS_APPEND_V1
def test_p1_legacy_closeout_contract_is_not_silently_redefined(tmp_path: Path) -> None:
    world = _CloseoutWorld(tmp_path, _native_shell("powershell"))
    first = world.close(world.closeout_arguments())
    assert first.returncode == 0, first.stdout + first.stderr
    second = world.close(world.closeout_arguments())
    assert second.returncode == 0, second.stdout + second.stderr
    assert "request_lifecycle" not in world.events()[-1]


@WINDOWS_APPEND_V1
@pytest.mark.parametrize("failure", ["admission", "wal_open"])
def test_p1_post_acquisition_failure_releases_append_mutex(tmp_path: Path, failure: str) -> None:
    """A caught failure must not strand ownership in the still-running caller."""
    import hashlib
    import os
    import re
    import subprocess
    import time

    world = _p1_world(tmp_path)
    suffix = hashlib.sha256(str(world.runtime_root).encode()).hexdigest()[:16]
    writer = (world.runtime_root.parent / ("writer-fixture-" + suffix)
              / ".agent-bridge/bin/Write-AgentEvent.ps1")
    source = writer.read_text(encoding="utf-8-sig")
    append_names = set(re.findall(r"Local\\WdWriterFixture-[0-9a-f]+-AppendV1", source))
    assert len(append_names) == 1
    append_name = append_names.pop()
    publication_name = append_name.removesuffix("AppendV1") + "AcceptedQueuePublicationV1"

    # Observe the real handle; do not replace the mutex implementation or its ACL.
    factory = "    return New-BridgeNamedMutex -Name $Name"
    assert source.count(factory) == 1
    source = source.replace(factory, """    $fixtureMutex = New-BridgeNamedMutex -Name $Name
    if ($Purpose -ceq 'Append') { $script:fixtureAppendHandle = $fixtureMutex.SafeWaitHandle }
    return $fixtureMutex""")
    if failure == "wal_open":
        opening = ("function Open-PendingCanonicalWalLease {\n"
                   "    param([Parameter(Mandatory)] [byte[]] $Bytes)\n")
        assert source.count(opening) == 1
        source = source.replace(opening, opening + (
            "    throw [IO.IOException]::new('fixture pending WAL open failure')\n"))
        arguments = world.closeout_arguments(PayloadJson=json.dumps({
            "superseded_by_request_id": "fixture-p1-successor-v2",
            "superseded_by_task_revision": 2,
        }))
        expected_error = "fixture pending WAL open failure"
    else:
        arguments = _successor_arguments(world)
        expected_error = "already active for this logical task"
    writer.write_text(source, encoding="utf-8-sig")
    before = (world.shared / "events.jsonl").read_bytes()
    arguments_file = tmp_path / "arguments.json"
    arguments_file.write_text(json.dumps(arguments), encoding="utf-8")
    ready = tmp_path / "owner-ready.json"
    host_script = tmp_path / "owner.ps1"

    def quote(value: Path | str) -> str:
        return "'" + str(value).replace("'", "''") + "'"

    host_script.write_text(
        "$ErrorActionPreference='Stop'\n"
        "$fixtureArguments=@{}\n"
        f"(Get-Content -LiteralPath {quote(arguments_file)} -Raw | ConvertFrom-Json)"
        ".PSObject.Properties | ForEach-Object {"
        "$fixtureArguments[$_.Name.TrimStart('-')]=$_.Value }\n"
        "$fixtureCaught=''\n"
        f"try {{ . {quote(writer)} @fixtureArguments }} "
        "catch { $fixtureCaught=$_.Exception.Message }\n"
        "$fixtureReport=@{caught=$fixtureCaught; "
        "append_handle_seen=($null -ne $script:fixtureAppendHandle); "
        "append_handle_closed=($null -ne $script:fixtureAppendHandle -and "
        "$script:fixtureAppendHandle.IsClosed)}\n"
        f"[IO.File]::WriteAllText({quote(ready)},"
        "($fixtureReport|ConvertTo-Json -Compress))\n"
        "[void][Console]::In.ReadLine()\n",
        encoding="utf-8-sig",
    )
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("AGENT_BRIDGE_", "WD_BRIDGE_"))
           or key.startswith("AGENT_BRIDGE_TEST_")}
    env.update(world.lead_frame)
    env["AGENT_BRIDGE_RUNTIME_ROOT"] = str(world.runtime_root)
    owner = subprocess.Popen(
        [world.shell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-File", str(host_script)], cwd=world.root, env=env,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        deadline = time.monotonic() + 30
        while not ready.exists() and owner.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        assert ready.exists(), "owner did not reach the caught-failure observation"
        observation = json.loads(ready.read_text(encoding="utf-8-sig"))
        assert expected_error in observation["caught"], observation
        assert observation["append_handle_seen"] is True, observation
        assert owner.poll() is None, "owner exited; abandonment is not release evidence"
        assert (world.shared / "events.jsonl").read_bytes() == before
        # A different process, not the recursively-owning caller thread, probes both locks.
        command = """
$ErrorActionPreference='Stop'
$fixtureResult=@{}
foreach($fixtureName in @(__APPEND__,__PUBLICATION__)) {
    $fixtureProbe=$null
    $fixtureAcquired=$false
    try {
        try {
            $fixtureProbe=[Threading.Mutex]::OpenExisting($fixtureName,
                ([Security.AccessControl.MutexRights]::Synchronize -bor
                 [Security.AccessControl.MutexRights]::Modify))
        } catch [Threading.WaitHandleCannotBeOpenedException] {
            $fixtureProbe=[Threading.Mutex]::new($false,$fixtureName)
        }
        $fixtureAcquired=$fixtureProbe.WaitOne(500)
        $fixtureResult[$fixtureName]=$fixtureAcquired
    } finally {
        if($fixtureAcquired){$fixtureProbe.ReleaseMutex()}
        if($null -ne $fixtureProbe){$fixtureProbe.Dispose()}
    }
}
$fixtureResult|ConvertTo-Json -Compress
""".replace("__APPEND__", quote(append_name)).replace("__PUBLICATION__", quote(publication_name))
        contender = subprocess.run(
            [world.shell, "-NoProfile", "-NonInteractive", "-Command", command],
            cwd=world.root, env=env, capture_output=True, text=True, timeout=20,
        )
        assert contender.returncode == 0, contender.stdout + contender.stderr
        locks = json.loads(contender.stdout)
        assert owner.poll() is None, "owner must remain alive throughout the probe"
        print("P1_MUTEX_OBSERVED " + json.dumps({
            "failure": failure, "owner_alive": True, **observation, "locks": locks,
        }, sort_keys=True))
        assert locks[publication_name] is True, locks
        assert locks[append_name] is True, "AppendV1 ownership leaked after caught failure"
        assert observation["append_handle_closed"] is True, observation
        assert (world.shared / "events.jsonl").read_bytes() == before
    finally:
        try:
            owner.communicate(input="\n", timeout=10)
        except subprocess.TimeoutExpired:
            owner.kill()  # Only this test's own isolated child, never a process-name sweep.
            owner.communicate(timeout=10)


# CLASS SWEEP: enumerate lifecycle exits, not just the reported partial answer.
def _class_request(*, targets="codex-tools-1,claude-rco-1", requester_uuid="fixture-lead-uuid"):
    identity = {"agent": "codex-lead-1", "agent_uuid": requester_uuid,
                "session_id": "fixture-lead-session", "run_id": "fixture-lead-run"}
    return {
        **identity, "ts_utc": "2026-01-01T00:00:00Z", "type": "wake_request",
        "status": "requested", "task_id": "fixture-p1-class-sweep", "to": targets,
        "request_id": "fixture-class-v1", "request_digest": "a" * 64,
        "request_lifecycle": "wd.request-lifecycle.p1", "task_revision": 1,
        "nonce": "fixture-class-nonce", "action_required": True, "fyi": False,
        "message": "synthetic lifecycle", "payload": {},
        "expected_responders": {
            target: {"agent_uuid": target + "-uuid", "session_id": target + "-session",
                     "run_id": target + "-run"}
            for target in targets.split(",") if target.strip()
        },
    }


def _class_terminal(request, target=None, *, time="2026-01-01T00:00:01Z"):
    closure = target is None
    identity = (request if closure else request["expected_responders"][target])
    return {
        "ts_utc": time, "agent": request["agent"] if closure else target,
        **{name: identity[name] for name in ("agent_uuid", "session_id", "run_id")},
        "type": "message", "status": "superseded" if closure else "answered",
        "task_id": request["task_id"], "to": request["to"] if closure else request["agent"],
        "in_reply_to_request_id": request["request_id"],
        "in_reply_to_request_digest": request["request_digest"],
        "in_reply_to_requester": {
            name: request[name] for name in ("agent", "agent_uuid", "session_id", "run_id")
        },
        "nonce": request["nonce"], "task_revision": request["task_revision"],
        "superseded_by_request_id": "fixture-class-v2" if closure else None,
        "superseded_by_task_revision": 2 if closure else None,
        "payload": {},
    }


def _class_next(request, *, requester_uuid=None, targets=None, first=False, wrong_link=False):
    event = _class_request(
        targets=request["to"] if targets is None else targets,
        requester_uuid=request["agent_uuid"] if requester_uuid is None else requester_uuid,
    )
    event.update(ts_utc="2026-01-01T00:00:04Z",
                 request_id="fixture-wrong-v2" if wrong_link else "fixture-class-v2",
                 request_digest="b" * 64, nonce="fixture-next-nonce")
    if not first:
        event.update(task_revision=2, previous_request_id=request["request_id"],
                     previous_request_digest=request["request_digest"])
    return event


def _class_transition(tmp_path, rows, event, *, closed_request=None, initialize=False):
    """Execute Source functions on synthetic rows only; never the installed writer."""
    import os
    import subprocess

    root = Path(__file__).resolve().parents[2]
    fixture = tmp_path / "class-transition.json"
    fixture.write_text(json.dumps({
        "rows": rows, "event": event, "closed_request": closed_request,
        "initialize": initialize,
    }), encoding="utf-8")
    quote = lambda value: "'" + str(value).replace("'", "''") + "'"
    command = (
        "$ErrorActionPreference='Stop'\n"
        f". {quote(root / '.agent-bridge/bin/BridgeEventClassifier.ps1')}\n"
        f". {quote(root / '.agent-bridge/bin/BridgeRequestContract.ps1')}\n"
        f"$fixture=ConvertFrom-Json ([IO.File]::ReadAllText({quote(fixture)}))\n"
        "try {\n"
        " if($fixture.initialize){\n"
        "  $event=[ordered]@{}\n"
        "  foreach($property in $fixture.event.PSObject.Properties){"
        "$event[$property.Name]=$property.Value}\n"
        "  Initialize-BridgeP1Envelope -Event $event -IsRequest $true\n"
        " } else {\n"
        "  Assert-BridgeP1Transition -Rows @($fixture.rows) -Event $fixture.event "
        "-ClosedRequest $fixture.closed_request\n"
        " }\n"
        " [pscustomobject]@{accepted=$true;error=$null}|ConvertTo-Json -Compress\n"
        "} catch {\n"
        " [pscustomobject]@{accepted=$false;error=$_.Exception.Message}|ConvertTo-Json -Compress\n"
        "}\n"
    )
    env = dict(os.environ)
    env["AGENT_BRIDGE_RUNTIME_ROOT"] = str(tmp_path / "synthetic-unused-runtime")
    run = subprocess.run(
        [_native_shell("powershell"), "-NoProfile", "-NonInteractive", "-Command", command],
        cwd=root, env=env, capture_output=True, text=True, timeout=30,
    )
    assert run.returncode == 0, run.stdout + run.stderr
    return json.loads(run.stdout)


@WINDOWS_APPEND_V1
@pytest.mark.parametrize("targets", ["", " ", ", ,"], ids=["empty", "blank", "separators"])
def test_class_sweep_no_target_request_is_hold_before_publication(tmp_path, targets):
    request = _class_request(targets=targets)
    result = _class_transition(tmp_path, [], request, initialize=True)
    assert result["accepted"] is False, result
    assert "at least one target" in result["error"]


@WINDOWS_APPEND_V1
@pytest.mark.parametrize("scenario", [
    "unanswered-close", "single-unanswered-close", "partial-first-close", "partial-second-close",
    "all-answered-close", "single-answered-close", "closed-close", "partial-closed-close",
    "all-answered-next", "single-answered-next", "closed-next", "partial-closed-next",
    "partial-closed-wrong-link", "uuid-unlinked-open", "uuid-closed", "targets-open", "targets-closed",
    "unbound-answer-close", "historical-no-target",
])
def test_class_sweep_each_reachable_lifecycle_state_has_safe_exit(tmp_path, scenario):
    request = _class_request(
        targets="codex-tools-1" if scenario.startswith("single-") else "codex-tools-1,claude-rco-1"
    )
    rows = [request]
    targets = request["to"].split(",")
    if scenario.startswith("partial-"):
        rows.append(_class_terminal(request, targets[1] if scenario == "partial-second-close" else targets[0]))
    elif scenario in {"all-answered-close", "single-answered-close",
                      "all-answered-next", "single-answered-next"}:
        rows.extend(_class_terminal(request, target) for target in targets)
    if scenario == "unbound-answer-close":
        answer = _class_terminal(request, targets[0])
        answer["in_reply_to_request_digest"] = "f" * 64
        rows.append(answer)
    if "closed" in scenario:
        rows.append(_class_terminal(request, time="2026-01-01T00:00:02Z"))
    is_next = scenario.endswith("-next") or scenario in {
        "partial-closed-wrong-link", "uuid-unlinked-open", "uuid-closed", "targets-open",
        "targets-closed", "historical-no-target",
    }
    if is_next:
        event = _class_next(
            request,
            requester_uuid="fixture-rotated-uuid" if scenario.startswith("uuid-") else None,
            targets=targets[0] if scenario.startswith("targets-") else None,
            first=scenario in {"uuid-unlinked-open", "targets-open"},
            wrong_link=scenario == "partial-closed-wrong-link",
        )
        if scenario == "historical-no-target":
            rows = [_class_request(targets="")]
        result = _class_transition(tmp_path, rows, event)
        accepted = scenario not in {
            "partial-closed-wrong-link", "uuid-unlinked-open", "targets-open", "historical-no-target",
        }
    else:
        event = _class_terminal(request, time="2026-01-01T00:00:03Z")
        result = _class_transition(tmp_path, rows, event, closed_request=request)
        accepted = scenario not in {
            "all-answered-close", "single-answered-close", "closed-close", "partial-closed-close",
        }
    assert result["accepted"] is accepted, (scenario, result)
    if not accepted:
        assert "P1 HOLD" in result["error"], result
    if scenario == "historical-no-target":
        assert "at least one target" in result["error"], result


@WINDOWS_APPEND_V1
def test_class_sweep_partial_answer_full_writer_closure_and_successor(tmp_path):
    """Owner criterion: durable closure preserves first answer and links the next revision."""
    from test_bridge_write_agent_event import _run_bridge_script

    world = _p1_world(tmp_path)
    identities = json.loads((world.root / "configs/bridge_identity_registry.json").read_text(
        encoding="utf-8"))["identities"]
    rco = {"agent": "claude-rco-1", "agent_uuid": identities["claude-rco-1"],
           "session_id": "fixture-rco-session", "run_id": "fixture-rco-run"}
    (world.shared / "last_claude-rco-1.json").write_text(json.dumps(rco), encoding="utf-8")
    task = world.request["task_id"] + "-multi"
    requested = world.write({
        **world.lead, "-Type": "wake_request", "-Status": "requested",
        "-TaskId": task, "-To": "codex-tools-1,claude-rco-1",
        "-RequestId": "fixture-multi-v1", "-Message": "synthetic multi-target",
        "-PayloadJson": json.dumps({"request_lifecycle": "wd.request-lifecycle.p1",
                                    "task_revision": 1, "nonce": "fixture-multi-nonce"}),
    }, world.lead_frame)
    assert requested.returncode == 0, requested.stdout + requested.stderr
    world.request = world.events()[-1]
    answer = world.write({
        **world.tools, "-Type": "message", "-Status": "answered", "-TaskId": task,
        "-To": "codex-lead-1", "-ReplyToEventJson": json.dumps(world.request),
    }, world.tools_frame)
    assert answer.returncode == 0, answer.stdout + answer.stderr
    first_answer = world.events()[-1]
    before = world.events()
    closure = world.close(world.closeout_arguments(
        TaskId=task, To=world.request["to"],
        PayloadJson=json.dumps({"superseded_by_request_id": "fixture-p1-successor-v2",
                                "superseded_by_task_revision": 2}),
    ))
    assert closure.returncode == 0, closure.stdout + closure.stderr
    assert world.events()[:-1] == before
    assert world.events()[-2] == first_answer
    successor = world.write(_successor_arguments(world), world.lead_frame)
    assert successor.returncode == 0, successor.stdout + successor.stderr
    events = world.events()
    assert len(events) == len(before) + 2
    assert events[-2]["in_reply_to_request_id"] == world.request["request_id"]
    assert events[-1]["previous_request_id"] == world.request["request_id"]
    assert events[-1]["previous_request_digest"] == world.request["request_digest"]
    assert sum(row.get("in_reply_to_request_id") == world.request["request_id"]
               and row["status"] == "answered" for row in events) == 1
    for row in (world.request, events[-1]):
        inventory = _run_bridge_script(
            world.root, world.runtime_root, "Get-BridgeRequestInventory.ps1",
            "-Agent", "codex-lead-1", "-RequestId", row["request_id"], "-NoCache",
            shell=world.shell,
        )
        assert inventory.returncode == 0, inventory.stdout + inventory.stderr
        parsed = json.loads(inventory.stdout)
        assert parsed["matched_count"] == 1
        assert parsed["requests"][0]["answer_state"] == "not_evaluated"

# Candidate appended to tests/tools/test_bridge_request_lifecycle_p1.py by Lead custody.
# NOT EXECUTED. Pure-contract tests assume an admitted actor; writer tests cover admission.
# Rename the existing unlinked negative scenario uuid-open -> uuid-unlinked-open;
# the linked positive uuid-open case below is its sanctioned exit, not a new first revision.


def _restart_metadata(request, reason="requester_session_replaced"):
    return {
        "previous_requester": {
            key: request[key] for key in ("agent", "agent_uuid", "session_id", "run_id")
        },
        "previous_targets": request["to"],
        "restart_reason": reason,
    }


@WINDOWS_APPEND_V1
@pytest.mark.parametrize("changed", ["agent_uuid", "session_id", "run_id", "session_and_run"])
def test_p1_restart_link_exits_uuid_open(tmp_path, changed):
    """Inner transition only: current actor admission is an explicit precondition."""
    request = _class_request()
    successor = _class_next(request)
    fields = ("session_id", "run_id") if changed == "session_and_run" else (changed,)
    for field in fields:
        successor[field] += "-restarted"
    successor.update(_restart_metadata(request))
    result = _class_transition(tmp_path, [request], successor)
    assert result["accepted"] is True, result


@WINDOWS_APPEND_V1
def test_p1_restarted_responder_cannot_answer_but_requester_has_exit(tmp_path):
    from waggledance.core.bridge_request_contract import reply_matches_request

    request = _class_request()
    target = "codex-tools-1"
    restarted_answer = _class_terminal(request, target)
    restarted_answer["session_id"] += "-restarted"
    restarted_answer["run_id"] += "-restarted"
    assert not reply_matches_request(request, restarted_answer, target)
    successor = _class_next(request)
    successor["expected_responders"][target] = {
        key: restarted_answer[key] for key in ("agent_uuid", "session_id", "run_id")
    }
    successor.update(_restart_metadata(request, "responder_session_replaced"))
    result = _class_transition(tmp_path, [request, restarted_answer], successor)
    assert result["accepted"] is True, result


@WINDOWS_APPEND_V1
def test_p1_restart_after_partial_answer_preserves_the_real_answer(tmp_path):
    request = _class_request()
    answer = _class_terminal(request, "claude-rco-1")
    rows = [request, answer]
    original = json.loads(json.dumps(rows))
    successor = _class_next(request)
    successor["session_id"] += "-restarted"
    successor["run_id"] += "-restarted"
    successor.update(_restart_metadata(request))
    result = _class_transition(tmp_path, rows, successor)
    assert result["accepted"] is True, result
    assert rows == original


@WINDOWS_APPEND_V1
def test_p1_restart_successor_is_terminal_only_for_old_continuity(tmp_path):
    request = _class_request()
    successor = _class_next(request)
    successor["session_id"] += "-restarted"
    successor["run_id"] += "-restarted"
    successor.update(_restart_metadata(request))
    third = _class_next(successor)
    third.update(ts_utc="2026-01-01T00:00:08Z", request_id="fixture-class-v3",
                 request_digest="c" * 64, nonce="fixture-third-nonce", task_revision=3)
    held = _class_transition(tmp_path, [request, successor], third)
    assert held["accepted"] is False, held
    rows = [request, successor]
    rows.extend(_class_terminal(successor, target, time="2026-01-01T00:00:06Z")
                for target in successor["to"].split(","))
    result = _class_transition(tmp_path, rows, third)
    assert result["accepted"] is True, result


@WINDOWS_APPEND_V1
@pytest.mark.parametrize("bad", [
    "missing-tuple", "wrong-agent", "wrong-uuid", "wrong-session", "wrong-run",
    "empty-tuple-value", "nonstring-tuple-value", "missing-targets", "wrong-targets",
    "missing-reason", "blank-reason", "unknown-reason", "nonstring-reason",
    "wrong-id", "wrong-digest", "skipped-revision", "unlinked-revision-one",
    "other-agent", "metadata-conflict", "no-actual-restart",
])
def test_p1_restart_metadata_cannot_bypass_the_active_request(tmp_path, bad):
    request = _class_request()
    successor = _class_next(request)
    successor["session_id"] += "-restarted"
    successor["run_id"] += "-restarted"
    successor.update(_restart_metadata(request))
    if bad == "missing-tuple":
        successor.pop("previous_requester")
    elif bad.startswith("wrong-") and bad[6:] in {"agent", "uuid", "session", "run"}:
        field = {"agent": "agent", "uuid": "agent_uuid", "session": "session_id", "run": "run_id"}[bad[6:]]
        successor["previous_requester"][field] += "-wrong"
    elif bad == "empty-tuple-value":
        successor["previous_requester"]["session_id"] = ""
    elif bad == "nonstring-tuple-value":
        successor["previous_requester"]["session_id"] = True
    elif bad == "missing-targets":
        successor.pop("previous_targets")
    elif bad == "wrong-targets":
        successor["previous_targets"] = "claude-rco-1"
    elif bad == "missing-reason":
        successor.pop("restart_reason")
    elif bad == "blank-reason":
        successor["restart_reason"] = " "
    elif bad == "unknown-reason":
        successor["restart_reason"] = "typed-recovery-authority"
    elif bad == "nonstring-reason":
        successor["restart_reason"] = True
    elif bad == "wrong-id":
        successor["previous_request_id"] = "wrong-predecessor"
    elif bad == "wrong-digest":
        successor["previous_request_digest"] = "f" * 64
    elif bad == "skipped-revision":
        successor["task_revision"] = 3
    elif bad == "unlinked-revision-one":
        successor["task_revision"] = 1
        successor.pop("previous_request_id")
        successor.pop("previous_request_digest")
    elif bad == "other-agent":
        successor["agent"] = "claude-rco-1"
    elif bad == "metadata-conflict":
        successor["payload"]["restart_reason"] = "responder_session_replaced"
    elif bad == "no-actual-restart":
        successor["session_id"] = request["session_id"]
        successor["run_id"] = request["run_id"]
    result = _class_transition(tmp_path, [request], successor)
    assert result["accepted"] is False, (bad, result)


@WINDOWS_APPEND_V1
def test_p1_new_requester_session_links_without_impersonating_old_tuple(tmp_path, monkeypatch):
    from test_bridge_write_agent_event import _peer_gate
    from waggledance.core.bridge_request_contract import reply_matches_request

    world = _p1_world(tmp_path)
    request = world.request
    veto = {"ts_utc": request["ts_utc"], "agent": "claude-rco-2",
            "agent_uuid": "76739997-0058-41a2-8514-78ff295537aa", "type": "finding",
            "status": "changes_requested", "task_id": request["task_id"],
            "to": "codex-lead-1", "message": "synthetic retained veto",
            "payload": {"head": "a" * 40}}
    before_gate = _peer_gate([veto], monkeypatch)
    before = world.events()
    old_close = world.close(world.closeout_arguments(
        SessionId="lead-session-2", RunId="lead-run-2", PayloadJson=json.dumps({
            "superseded_by_request_id": "fixture-p1-successor-v2",
            "superseded_by_task_revision": 2})), world.later_lead_frame)
    assert old_close.returncode != 0
    assert world.events() == before
    arguments = _successor_arguments(world, **_restart_metadata(request))
    arguments.update({"-SessionId": "lead-session-2", "-RunId": "lead-run-2"})
    completed = world.write(arguments, world.later_lead_frame)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    events = world.events()
    assert events[:-1] == before
    successor = events[-1]
    assert successor["session_id"] == "lead-session-2"
    assert successor["run_id"] == "lead-run-2"
    for name, value in _restart_metadata(request).items():
        assert successor[name] == successor["payload"][name] == value
    assert "in_reply_to_request_id" not in successor
    assert not reply_matches_request(request, successor, "codex-tools-1")
    assert not reply_matches_request(request, successor, "codex-tools-1", requester_closure=True)
    assert not any(row["status"] in {"answered", "rco_pass", "build_consensus_pass"}
                   for row in events)
    after_gate = _peer_gate([veto, *events], monkeypatch)
    assert not before_gate["clear_to_merge"] and not after_gate["clear_to_merge"]
    assert after_gate["latest_blocking_event"] == before_gate["latest_blocking_event"]

# Append after the v5 tests-only candidate. NOT RUN by the Tools helper.
# All event-log writes below are exclusively to this test's synthetic fixture runtime.


@WINDOWS_APPEND_V1
@pytest.mark.parametrize("duplicate", [False, True], ids=["single", "identical-successor-repeat"])
def test_p1_restart_inventory_keeps_continuity_separate_from_answers(tmp_path, duplicate):
    from test_bridge_write_agent_event import _run_bridge_script

    world = _p1_world(tmp_path)
    request = world.request

    def inventory(event):
        run = _run_bridge_script(
            world.root, world.runtime_root, "Get-BridgeRequestInventory.ps1",
            "-Agent", "codex-lead-1", "-RequestId", event["request_id"],
            "-NoCache", shell=world.shell,
        )
        assert run.returncode == 0, run.stdout + run.stderr
        output = json.loads(run.stdout)
        assert output["matched_count"] == 1
        return output["requests"][0]

    before = inventory(request)
    assert before["answer_state"] == "not_evaluated"
    # Baseline396 fails HERE, independently of restart publication or Source correction.
    assert before["p1_continuity_state"] == "not_evaluated"

    arguments = _successor_arguments(world, **_restart_metadata(request))
    arguments.update({"-SessionId": "lead-session-2", "-RunId": "lead-run-2"})
    run = world.write(arguments, world.later_lead_frame)
    assert run.returncode == 0, run.stdout + run.stderr
    successor = world.events()[-1]
    if duplicate:
        log = world.shared / "events.jsonl"
        identical_line = log.read_bytes().splitlines(keepends=True)[-1]
        assert identical_line.endswith(b"\n")
        with log.open("ab") as fixture_log:
            fixture_log.write(identical_line)

    old_entry = inventory(request)
    assert old_entry["answer_state"] == "not_evaluated"
    assert old_entry["p1_continuity_state"] == "superseded_by_restart"
    assert old_entry["p1_continuity_superseder"] == {
        "request_id": successor["request_id"],
        "request_digest": successor["request_digest"],
    }
    current_entry = inventory(successor)
    assert current_entry["answer_state"] == "not_evaluated"
    assert current_entry["p1_continuity_state"] == "not_evaluated"
    assert current_entry["occurrences"] == (2 if duplicate else 1)
    for name, value in _restart_metadata(request).items():
        assert current_entry[name] == value


@WINDOWS_APPEND_V1
@pytest.mark.parametrize("frame_kind", ["missing", "old-frame", "other-agent"])
def test_p1_restart_successor_requires_own_current_inherited_frame(tmp_path, frame_kind):
    world = _p1_world(tmp_path)
    arguments = _successor_arguments(world, **_restart_metadata(world.request))
    arguments.update({"-SessionId": "lead-session-2", "-RunId": "lead-run-2"})
    frame = {} if frame_kind == "missing" else (
        world.lead_frame if frame_kind == "old-frame" else world.tools_frame)
    before = world.events()
    completed = world.write(arguments, frame)
    assert completed.returncode != 0
    assert world.events() == before


@WINDOWS_APPEND_V1
def test_p1_public_writer_restarted_responder_has_recorded_exit(tmp_path):
    world = _p1_world(tmp_path)
    request = world.request
    target_identity = {
        "agent": "codex-tools-1", "agent_uuid": world.tools["-AgentUuid"],
        "session_id": "tools-session-2", "run_id": "tools-run-2",
    }
    (world.shared / "last_codex-tools-1.json").write_text(
        json.dumps(target_identity), encoding="utf-8")
    new_tools = {**world.tools, "-SessionId": "tools-session-2", "-RunId": "tools-run-2"}
    new_frame = {**world.tools_frame, "AGENT_BRIDGE_SESSION_ID": "tools-session-2",
                 "AGENT_BRIDGE_RUN_ID": "tools-run-2"}
    before = world.events()
    answer = world.write({**new_tools, "-Type": "message", "-Status": "answered",
        "-TaskId": request["task_id"], "-To": request["agent"],
        "-ReplyToEventJson": json.dumps(request)}, new_frame)
    assert answer.returncode != 0
    assert world.events() == before
    result = world.write(_successor_arguments(world, **_restart_metadata(
        request, "responder_session_replaced")), world.lead_frame)
    assert result.returncode == 0, result.stdout + result.stderr
    events = world.events()
    assert events[:-1] == before
    successor = events[-1]
    assert successor["expected_responders"]["codex-tools-1"] == {
        key: target_identity[key] for key in ("agent_uuid", "session_id", "run_id")
    }
    assert not any(row["status"] in {"answered", "rco_pass", "build_consensus_pass"}
                   for row in events)


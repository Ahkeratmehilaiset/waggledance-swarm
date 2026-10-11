"""Adversarial, isolated checks of native continuity control precedence.

The fixture log and package are private to pytest's .codex-audit basetemp.
No test opens or writes the live bridge runtime.
"""

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from test_wd_reboot_bundle import REBOOT, LANE_TEST_SHELLS, _run_powershell
from test_wd_startup_recovery import load, q


TOOLS = REBOOT / "start-wd-tools-consumer.ps1"
AGENT = "codex-lead-1"
TASK = "control-probe"


def _event(*, agent="operator", task=TASK, to="", type="message", status="hold"):
    return dict(agent=agent, task_id=task, to=to, type=type, status=status,
                ts_utc="2026-09-28T22:59:00Z", payload={})


def _nest(levels, leaf):
    """A payload whose innermost object, ``leaf``, sits at nesting level ``levels`` (the payload is level 1)."""
    node = leaf
    for _ in range(levels - 1):
        node = {"n": node}
    return node


def _without_payload(row):
    return {key: value for key, value in row.items() if key != "payload"}


def _fixture(tmp_path, rows, agent=AGENT, reader_suffix=""):
    bundle = tmp_path / "bundle"
    helper_dir = bundle / "tools-bootstrap/.agent-bridge/bin"
    helper_dir.mkdir(parents=True)
    files = {}
    for leaf in ("BridgeLogReader.ps1", "BridgeIncrementalReader.ps1"):
        target = helper_dir / leaf
        shutil.copyfile(REBOOT.parents[2] / ".agent-bridge/bin" / leaf, target)
        if leaf == "BridgeIncrementalReader.ps1" and reader_suffix:
            target.write_bytes(target.read_bytes() + reader_suffix.encode("utf-8"))
        files[target.relative_to(bundle).as_posix()] = hashlib.sha256(target.read_bytes()).hexdigest().upper()
    manifest = bundle / "deployment-manifest.json"
    manifest.write_text(json.dumps({"files": files}), encoding="utf-8")
    anchor = hashlib.sha256(manifest.read_bytes()).hexdigest().upper()
    runtime = tmp_path / "runtime"
    (runtime / "shared").mkdir(parents=True)
    log = runtime / "shared/events.jsonl"
    log.write_text("".join(json.dumps(row, separators=(",", ":")) + "\n" for row in rows), encoding="utf-8")
    prelude = "$ErrorActionPreference='Stop'\nSet-StrictMode -Version Latest\n"
    prelude += load(REBOOT / "Invoke-WdLaneTurnLoop.ps1", "Assert-WdTurnPath")
    prelude += load(TOOLS, "Test-WdContinuityControlEvents")
    prelude += f"""
$env:WD_BRIDGE_PYTHON_WRAPPER={q(bundle / 'Invoke-WdBridgePython.ps1')}
$env:WD_REBOOT_EXPECTED_MANIFEST_HASH='{anchor}'
function Check {{
 try {{@{{ok=$true;held=(Test-WdContinuityControlEvents -RuntimeRoot {q(runtime)} -TaskId {q(TASK)} -Agent {q(agent)} -CheckpointAt '2026-09-28T22:00:00Z')}}}}
 catch {{@{{ok=$false;error=$_.Exception.Message}}}}
}}
"""
    return log, prelude


@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize(
    "event,held",
    [
        (_event(to=""), True),
        (_event(task="fleet", to="all"), True),
        (_event(task="fleet", to=AGENT), True),
        (_event(task="fleet", to="claude-rco-1"), False),
        (_event(agent="fable-5", task="other", to="all", type="finding", status="changes_requested"), False),
        (_event(agent="fable-5", status="on_hold"), True),
        (_event(agent="fable-5", status="changesRequested"), True),
        (_event(agent="fable-5", status="vetoed"), True),
        (_event(agent="fable-5", status="unblocked"), False),
        (_event(agent="fable-5", status="holdover"), False),
        (_event(agent="fable-5", status="failed"), False),
        (_event(agent="operator", task="other", to=AGENT,
                status="operator_signed_head_exact_retracted"), False),
        (_event(agent="fable-5", type="finding", status="approved"), True),
    ],
    ids=["operator-empty", "operator-all", "operator-lane", "operator-other-lane",
         "foreign-broadcast", "on-hold", "camel-changes-requested", "vetoed",
         "unblocked-is-not-blocked", "holdover-is-not-hold", "failure-is-not-hold",
         "retracted-is-not-release", "same-task-finding-fail-closed"],
)
def test_scoped_control_tokens_and_broadcasts(tmp_path, ps, event, held):
    _, script = _fixture(tmp_path, [event])
    report = json.loads(_run_powershell(script + "Check | ConvertTo-Json -Compress", executable=ps).stdout)
    assert report == {"ok": True, "held": held}, (event, report)


# Shapes of the 17 same-task rows that latched the Lead's 12h wave task (RCO2 triage F209BCC1): worker progress
# reports and the Lead's own bug findings. Payload keys as observed; values are placeholders.
WORKER = "codex-tools-1"
DIAGNOSTICS = {
    "worker-blocked-waiting-dependency": _event(agent=WORKER, type="blocked", status="waiting_dependency",
                                                to=AGENT) | {"payload": {"notification": "informational",
                                                                         "runtime_verification": "NOT_RUN"}},
    "worker-status-routing-blocked": _event(agent=WORKER, type="status", status="routing_blocked")
    | {"payload": {"notification": "x", "inventory_coverage": "x", "completed_effect_replayed": False}},
    "worker-message-blocked": _event(agent=WORKER, type="message", status="blocked")
    | {"payload": {"result": {}, "result_validation": {}, "execution_evidence": {}}},
    "worker-blocked-blocked": _event(agent=WORKER, type="blocked", status="blocked"),
    "worker-blocked-inventory-conflict": _event(agent=WORKER, type="blocked", status="inventory_binding_conflict")
    | {"payload": {"diagnostic_only": True, "classification": "x", "next_action": "x"}},
    "lead-own-finding-confirmed-bug": _event(agent=AGENT, type="finding", status="confirmed_bug")
    | {"payload": {"classification": "x", "authority_effect": "x", "native_exit": 1}},
    "lead-own-finding-suspected-bug": _event(agent=AGENT, type="finding", status="suspected_bug")
    | {"payload": {"reviewer": "x", "finding_id": "x"}},
    # The other known worker (P1806-F1: only known workers are exempt).
    "known-worker-fable-5-blocked": _event(agent="fable-5", type="blocked", status="waiting_dependency"),
    "known-worker-fable-5-message-blocked": _event(agent="fable-5", type="message", status="blocked"),
    # P1806-F2 negatives: payload names that only share a word with changes_requested stay diagnostics.
    "worker-payload-near-miss-names": _event(agent=WORKER, type="message", status="blocked")
    | {"payload": {"changes": "x", "requested_by": "x", "change_requests": 0, "requestedChanges": "x"}},
    "own-finding-payload-near-miss-names": _event(agent=AGENT, type="finding", status="confirmed_bug")
    | {"payload": {"requested": "x", "changes_seen": 2}},
    # P1806-R1 negatives: separate words or the reversed order are not changes_requested.
    "worker-payload-separate-keys": _event(agent=WORKER, type="blocked", status="blocked")
    | {"payload": {"changes": "x", "requested": "x"}},
    "own-finding-payload-reversed-dot": _event(agent=AGENT, type="finding", status="confirmed_bug")
    | {"payload": {"requested.changes": "x"}},
    # RCO1/RCO2 on PR1843: nested structure without a control-named field stays a diagnostic, up to the
    # 16-level and 8192-value bounds.
    "worker-nested-clean-evidence": _event(agent=WORKER, type="blocked", status="blocked")
    | {"payload": {"evidence": {"tests": [{"name": "x", "outcome": "passed"}], "head": "x"},
                   "findings": [{"id": "x", "classification": "x"}, ["x", {"note": "x"}, []]]}},
    "fable-nested-clean-evidence": _event(agent="fable-5", type="message", status="blocked")
    | {"payload": {"evidence": [{"path": "x"}, []], "result": {"summary": "x", "native_exit": 0}}},
    "own-finding-nested-clean": _event(agent=AGENT, type="finding", status="confirmed_bug")
    | {"payload": {"finding": {"id": "x", "evidence": ["x", {"path": "x"}]}}},
    "worker-nested-clean-at-level-16": _event(agent=WORKER, type="status", status="routing_blocked")
    | {"payload": _nest(16, {"x": 1})},
    "worker-payload-8002-values": _event(agent=WORKER, type="message", status="blocked")
    | {"payload": {"items": list(range(8000))}},
}


def _check_log(ps, tmp_path, rows):
    _, script = _fixture(tmp_path, rows)
    return json.loads(_run_powershell(script + "Check | ConvertTo-Json -Compress", executable=ps).stdout)


@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize("name", list(DIAGNOSTICS))
def test_worker_progress_and_own_bug_findings_are_not_holds(tmp_path, ps, name):
    assert _check_log(ps, tmp_path, [DIAGNOSTICS[name]]) == {"ok": True, "held": False}


@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
def test_the_whole_observed_diagnostic_history_is_not_a_hold(tmp_path, ps):
    assert _check_log(ps, tmp_path, list(DIAGNOSTICS.values()) * 3) == {"ok": True, "held": False}


# Positive twins: each is a diagnostic row above with exactly one control element added. Explicit control is
# classified before the diagnostic exemption, so every one of them must still latch.
CONTROL_TWINS = {
    "mixed-row-hard-status-token": _event(agent=WORKER, type="blocked", status="waiting_dependency_on_hold"),
    "mixed-row-veto-on-message": _event(agent=WORKER, type="message", status="blocked_vetoed"),
    "own-finding-changes-requested": _event(agent=AGENT, type="finding", status="changes_requested"),
    "historical-release-held-payload": DIAGNOSTICS["worker-message-blocked"] | {"payload": {"release_held": True}},
    "release-held-false-still-a-control-field": _event(agent=WORKER, type="blocked", status="blocked")
    | {"payload": {"release_held": False}},
    "work-held-payload": _event(agent=WORKER, type="blocked", status="blocked") | {"payload": {"work_held": True}},
    "payload-control-field": _event(agent=WORKER, type="status", status="routing_blocked")
    | {"payload": {"control": "pause"}},
    "camel-payload-hold-field": _event(agent=WORKER, type="blocked", status="blocked") | {"payload": {"onHold": 1}},
    "payload-not-an-object": _event(agent=WORKER, type="blocked", status="blocked") | {"payload": "hold"},
    # Bot 4241335136 on PR1849 (Lead-confirmed): a missing or null payload cannot be inspected, so a
    # known-worker or own-finding row that would otherwise be a diagnostic stays a control.
    "worker-blocked-payload-absent": _without_payload(_event(agent=WORKER, type="blocked", status="blocked")),
    "worker-blocked-payload-null": _event(agent=WORKER, type="blocked", status="blocked") | {"payload": None},
    "fable-message-blocked-payload-absent": _without_payload(
        _event(agent="fable-5", type="message", status="blocked")),
    "fable-status-blocked-payload-null": _event(agent="fable-5", type="status", status="routing_blocked")
    | {"payload": None},
    "own-finding-payload-absent": _without_payload(_event(agent=AGENT, type="finding", status="confirmed_bug")),
    "own-finding-payload-null": _event(agent=AGENT, type="finding", status="suspected_bug") | {"payload": None},
    # RCO1 15:25Z / RCO2 CHANGES_REQUESTED 15:26Z on PR1843 (Tools, bot 4242064499): a control-named field at
    # any depth, in a nested object or an array element, keeps a known-worker or own-finding row a control.
    "worker-nested-object-release-held": _event(agent=WORKER, type="blocked", status="blocked")
    | {"payload": {"details": {"release_held": True}}},
    "worker-nested-result-work-held": DIAGNOSTICS["worker-message-blocked"]
    | {"payload": {"result": {"work_held": True}, "result_validation": {}, "execution_evidence": {}}},
    "worker-array-element-control": _event(agent=WORKER, type="status", status="routing_blocked")
    | {"payload": {"details": [{"control": "pause"}]}},
    "worker-array-in-array-hold-field": _event(agent=WORKER, type="blocked", status="waiting_dependency")
    | {"payload": {"steps": [[1, {"onHold": 1}]]}},
    "worker-control-field-at-level-16": _event(agent=WORKER, type="message", status="blocked")
    | {"payload": _nest(16, {"changes_requested": False})},
    "fable-nested-object-release-held": _event(agent="fable-5", type="message", status="blocked")
    | {"payload": {"details": {"release_held": True}}},
    "fable-array-element-control": _event(agent="fable-5", type="status", status="routing_blocked")
    | {"payload": {"evidence": [{"path": "x"}, {"control": "pause"}]}},
    "own-finding-nested-hold-field": _event(agent=AGENT, type="finding", status="confirmed_bug")
    | {"payload": {"finding": {"release_held": False}}},
    # A payload that cannot be fully scanned stays a control, even when every name in it is clean.
    "worker-payload-level-17": _event(agent=WORKER, type="blocked", status="blocked")
    | {"payload": _nest(17, {"x": 1})},
    "fable-payload-level-17-array": _event(agent="fable-5", type="message", status="blocked")
    | {"payload": _nest(16, {"x": [1]})},
    "worker-payload-8202-values": _event(agent=WORKER, type="blocked", status="blocked")
    | {"payload": {"items": list(range(8200))}},
    "rco1-blocked": _event(agent="claude-rco-1", type="blocked", status="blocked"),
    "rco2-message-blocked": _event(agent="claude-rco-2", type="message", status="blocked"),
    "rco-finding-confirmed-bug": _event(agent="claude-rco-2", type="finding", status="confirmed_bug"),
    "operator-blocked": _event(agent="operator", type="blocked", status="waiting_dependency"),
    "operator-broadcast-blocked": _event(agent="operator", task="fleet", to="all", type="message",
                                         status="blocked"),
    "lead-blocked-on-own-task": _event(agent=AGENT, type="blocked", status="waiting_dependency"),
    "lead-identity-blocked": _event(agent="codex-lead-1", type="message", status="blocked"),
    "peer-finding-confirmed-bug": _event(agent="fable-5", type="finding", status="confirmed_bug"),
    "own-finding-unknown-status": _event(agent=AGENT, type="finding", status="open"),
    "own-finding-status-prefix": _event(agent=AGENT, type="finding", status="confirmed_bugs"),
    "own-finding-status-case": _event(agent=AGENT, type="finding", status="Confirmed_Bug"),
    "worker-decision-blocked": _event(agent=WORKER, type="decision", status="blocked"),
    "worker-unknown-type-blocked": _event(agent=WORKER, type="note", status="blocked"),
    "unknown-empty-agent": _event(agent="", type="blocked", status="blocked"),
    "unknown-agent-shape": _event(agent="Codex-Tools-1", type="blocked", status="blocked"),
    # P1806-F1 (RCO1 ABF90637): a well-formed name that is not a known worker stays a control.
    "valid-unknown-agent": _event(agent="mallory-7", type="blocked", status="blocked"),
    "rotated-lead-identity": _event(agent="codex-lead-2", type="blocked", status="waiting_dependency"),
    "rotated-rco-identity": _event(agent="claude-rco-3", type="message", status="blocked"),
    "grok-scout-identity": _event(agent="grok-scout-1", type="status", status="routing_blocked"),
    # P1806-F2 (Lead FB138C8F): a payload field named changes_requested is a control field, whatever its value,
    # exactly like the status normalization. Tested spellings only: snake, camel, hyphen, compact (F2) and space,
    # dot, double underscore (R1, below); other separators are not claimed.
    "payload-changes-requested-true": _event(agent=WORKER, type="blocked", status="blocked")
    | {"payload": {"changes_requested": True}},
    "payload-changes-requested-false": _event(agent=WORKER, type="message", status="blocked")
    | {"payload": {"changes_requested": False}},
    "payload-changes-requested-camel": _event(agent=WORKER, type="status", status="routing_blocked")
    | {"payload": {"changesRequested": True}},
    "payload-changes-requested-hyphen": _event(agent=WORKER, type="blocked", status="waiting_dependency")
    | {"payload": {"changes-requested": False}},
    "payload-changes-requested-compact": _event(agent=WORKER, type="blocked", status="blocked")
    | {"payload": {"changesrequested": True}},
    "own-finding-payload-changes-requested": _event(agent=AGENT, type="finding", status="confirmed_bug")
    | {"payload": {"changes_requested": True}},
    "own-finding-payload-changes-requested-camel": _event(agent=AGENT, type="finding", status="suspected_bug")
    | {"payload": {"changesRequested": False}},
    "status-changes-requested-camel": _event(agent=WORKER, type="message", status="changesRequested"),
    "status-changes-requested-hyphen": _event(agent=WORKER, type="blocked", status="changes-requested"),
    # P1806-R1 (fable-5 E763A68C, 18 failed on PS5/PS7 at 9096825f): space, dot and double-underscore separators
    # in a known-worker payload name, a known-worker blocked status and an own-finding payload name.
    "r1-worker-payload-space": _event(agent=WORKER, type="blocked", status="blocked")
    | {"payload": {"changes requested": True}},
    "r1-worker-payload-dot": _event(agent=WORKER, type="message", status="blocked")
    | {"payload": {"changes.requested": False}},
    "r1-worker-payload-double-underscore": _event(agent=WORKER, type="status", status="routing_blocked")
    | {"payload": {"changes__requested": True}},
    "r1-worker-status-space": _event(agent=WORKER, type="blocked", status="changes requested"),
    "r1-worker-status-dot": _event(agent=WORKER, type="blocked", status="changes.requested"),
    "r1-worker-status-double-underscore": _event(agent=WORKER, type="blocked", status="changes__requested"),
    "r1-own-finding-payload-space": _event(agent=AGENT, type="finding", status="confirmed_bug")
    | {"payload": {"changes requested": False}},
    "r1-own-finding-payload-dot": _event(agent=AGENT, type="finding", status="suspected_bug")
    | {"payload": {"changes.requested": True}},
    "r1-own-finding-payload-double-underscore": _event(agent=AGENT, type="finding", status="confirmed_bug")
    | {"payload": {"changes__requested": False}},
}


@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize("author,held", [("fable-5", True), (WORKER, False)], ids=["own-lane-blocked", "other-worker"])
def test_a_known_worker_lane_never_exempts_its_own_blocked_rows(tmp_path, ps, author, held):
    _, script = _fixture(tmp_path, [_event(agent=author, type="blocked", status="waiting_dependency")], agent="fable-5")
    report = json.loads(_run_powershell(script + "Check | ConvertTo-Json -Compress", executable=ps).stdout)
    assert report == {"ok": True, "held": held}, report


@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize("name", list(CONTROL_TWINS))
def test_explicit_control_twin_still_latches(tmp_path, ps, name):
    assert _check_log(ps, tmp_path, [CONTROL_TWINS[name]]) == {"ok": True, "held": True}


@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize("order", ["control-first", "control-last"])
def test_diagnostics_never_hide_a_control_and_have_no_time_floor(tmp_path, ps, order):
    # A control far older than the checkpoint still latches: no producer-clock floor for either class.
    control = _event(agent=WORKER, status="paused") | {"ts_utc": "2020-01-01T00:00:00Z"}
    diagnostics = [row | {"ts_utc": "2030-01-01T00:00:00Z"} for row in DIAGNOSTICS.values()]
    rows = [control] + diagnostics if order == "control-first" else diagnostics + [control]
    assert _check_log(ps, tmp_path, rows) == {"ok": True, "held": True}


@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
def test_diagnostic_history_then_later_control_latches_and_no_ordinary_row_releases(tmp_path, ps):
    log, script = _fixture(tmp_path, list(DIAGNOSTICS.values()))
    script += f"""
$clear=Check
[IO.File]::AppendAllText({q(log)}, {q(json.dumps(_event(agent=WORKER, status='on_hold')) + chr(10))})
$held=Check
[IO.File]::AppendAllText({q(log)}, {q(json.dumps(DIAGNOSTICS['worker-message-blocked']) + chr(10)
                                    + json.dumps(_event(type='decision', status='rco_pass')) + chr(10))})
$after=Check
@{{clear=$clear;held=$held;after=$after}} | ConvertTo-Json -Depth 6 -Compress
"""
    report = json.loads(_run_powershell(script, executable=ps).stdout)
    assert report == {"clear": {"ok": True, "held": False}, "held": {"ok": True, "held": True},
                      "after": {"ok": True, "held": True}}, report


@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
def test_in_place_rewrite_of_a_diagnostic_row_cannot_stay_clear(tmp_path, ps):
    log, script = _fixture(tmp_path, [DIAGNOSTICS["worker-blocked-blocked"]])
    marker = b'"status":"blocked"'
    offset = log.read_bytes().index(marker) + len(b'"status":"')
    script += f"""
$before=Check
$stream=[IO.File]::Open({q(log)},[IO.FileMode]::Open,[IO.FileAccess]::Write,[IO.FileShare]::ReadWrite)
try {{
 [void]$stream.Seek({offset},[IO.SeekOrigin]::Begin)
 $bytes=[Text.Encoding]::ASCII.GetBytes('on_hold')
 $stream.Write($bytes,0,$bytes.Length)
 $stream.Flush($true)
}} finally {{$stream.Dispose()}}
$after=Check
@{{before=$before;after=$after}} | ConvertTo-Json -Depth 5 -Compress
"""
    report = json.loads(_run_powershell(script, executable=ps).stdout)
    assert report["before"] == {"ok": True, "held": False}, report
    assert report["after"] != {"ok": True, "held": False}, report


@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
def test_truncation_after_a_clear_diagnostic_history_is_never_clear(tmp_path, ps):
    log, script = _fixture(tmp_path, list(DIAGNOSTICS.values()))
    script += f"""
$before=Check
[IO.File]::WriteAllText({q(log)}, '')
$after=Check
@{{before=$before;after=$after}} | ConvertTo-Json -Depth 5 -Compress
"""
    report = json.loads(_run_powershell(script, executable=ps).stdout)
    assert report["before"] == {"ok": True, "held": False}, report
    assert report["after"]["ok"] is False, report


@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize("later", [_event(status="approved"), _event(status="in_progress"),
                                    _event(type="decision", status="rco_pass")],
                         ids=["approval", "message", "rco-pass"])
def test_later_noncontrol_never_releases_latched_hold(tmp_path, ps, later):
    log, script = _fixture(tmp_path, [_event()])
    script += f"""
$before=Check
[IO.File]::AppendAllText({q(log)}, {q(json.dumps(later) + chr(10))})
$after=Check
[IO.File]::WriteAllText({q(log)}, {q(json.dumps(_event(agent='peer', status='notice')) + chr(10))})
$truncated=Check
@{{before=$before;after=$after;truncated=$truncated}} | ConvertTo-Json -Depth 6 -Compress
"""
    report = json.loads(_run_powershell(script, executable=ps).stdout)
    assert all(report[name] == {"ok": True, "held": True}
               for name in ("before", "after", "truncated")), report


@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
def test_partial_prefix_is_unknown_until_all_pages_are_scanned(tmp_path, ps):
    row = _event(agent="peer", task="other", to=AGENT, status="notice")
    row["message"] = "x" * 4000
    _, script = _fixture(tmp_path, [row] * 800)
    script += "$checks=@(1..6 | ForEach-Object { Check }); $checks | ConvertTo-Json -Depth 5 -Compress"
    checks = json.loads(_run_powershell(script, executable=ps).stdout)
    assert checks[0]["ok"] is False and "catching up" in checks[0]["error"], checks
    assert checks[-1] == {"ok": True, "held": False}, checks
    first_complete = next(i for i, item in enumerate(checks) if item["ok"])
    assert all(not item["ok"] for item in checks[:first_complete]), checks
    assert all(item == {"ok": True, "held": False} for item in checks[first_complete:]), checks


@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize("mutation", ["truncate", "rotate", "partial"],
                         ids=["truncation", "rotation", "partial-row"])
def test_unreconciled_log_change_never_returns_clear(tmp_path, ps, mutation):
    row = _event(agent="peer", task="other", to=AGENT, status="notice")
    log, script = _fixture(tmp_path, [row] * 8)
    script += "$before=Check\n"
    if mutation == "truncate":
        script += f"[IO.File]::WriteAllText({q(log)}, '')\n"
    elif mutation == "rotate":
        script += f"[IO.File]::Delete({q(log)}); [IO.File]::WriteAllText({q(log)}, '')\n"
    else:
        script += f"[IO.File]::AppendAllText({q(log)}, '{{\"agent\":\"operator\"')\n"
    script += "$after=Check; @{before=$before;after=$after} | ConvertTo-Json -Depth 5 -Compress"
    report = json.loads(_run_powershell(script, executable=ps).stdout)
    assert report["before"] == {"ok": True, "held": False}, report
    assert report["after"]["ok"] is False, report


@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
def test_same_length_in_place_prefix_rewrite_cannot_hide_hold(tmp_path, ps):
    """A cursor must not treat an unchanged EOF as proof its prefix is unchanged."""
    log, script = _fixture(tmp_path, [_event(status="idle")])
    marker = b'"status":"idle"'
    offset = log.read_bytes().index(marker) + len(b'"status":"')
    script += f"""
$before=Check
$stream=[IO.File]::Open({q(log)},[IO.FileMode]::Open,[IO.FileAccess]::Write,[IO.FileShare]::ReadWrite)
try {{
 [void]$stream.Seek({offset},[IO.SeekOrigin]::Begin)
 $bytes=[Text.Encoding]::ASCII.GetBytes('hold')
 $stream.Write($bytes,0,$bytes.Length)
 $stream.Flush($true)
}} finally {{$stream.Dispose()}}
$after=Check
@{{before=$before;after=$after}} | ConvertTo-Json -Depth 5 -Compress
"""
    report = json.loads(_run_powershell(script, executable=ps).stdout)
    assert report["before"] == {"ok": True, "held": False}, report
    assert report["after"] != {"ok": True, "held": False}, report


FILLER = _event(agent="peer", task="other", to=AGENT, status="notice") | {"message": "x" * 4000}
CLEAR = {"ok": True, "held": False}
SETTLE = "function Settle { $r=@(); for ($i=0; $i -lt 10; $i++) { $c=Check; $r+=$c; if ($c.ok) { break } }; ,$r }\n"


def _rewrite(log, offset, text):
    return f"""
$stream=[IO.File]::Open({q(log)},[IO.FileMode]::Open,[IO.FileAccess]::Write,[IO.FileShare]::ReadWrite)
try {{
 [void]$stream.Seek({offset},[IO.SeekOrigin]::Begin)
 $bytes=[Text.Encoding]::ASCII.GetBytes('{text}')
 $stream.Write($bytes,0,$bytes.Length)
 $stream.Flush($true)
}} finally {{$stream.Dispose()}}
"""


def _status_offset(log):
    data = log.read_bytes()
    marker = b'"status":"blocked"'
    assert data.count(marker) == 1
    return data.index(marker) + len(b'"status":"')


@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize("before,after", [(25, 0), (300, 300)], ids=["past-64-kib", "inside-a-full-block"])
def test_same_length_rewrite_far_behind_the_cursor_cannot_stay_clear(tmp_path, ps, before, after):
    """Codex P1 4240977444 on PR1843: the anchor covers every byte below the cursor, not a 64 KiB prefix."""
    log, script = _fixture(tmp_path, [FILLER] * before + [DIAGNOSTICS["worker-blocked-blocked"]] + [FILLER] * after)
    offset = _status_offset(log)
    assert offset > 65536 and (after == 0 or offset + (1 << 20) < log.stat().st_size)
    script += SETTLE + "$before=Settle\n" + _rewrite(log, offset, "on_hold")
    script += "$after=Settle\n@{before=$before;after=$after} | ConvertTo-Json -Depth 6 -Compress"
    report = json.loads(_run_powershell(script, executable=ps).stdout)
    assert report["before"][-1] == CLEAR, report
    assert CLEAR not in report["after"], report
    assert report["after"][-1] == {"ok": True, "held": True}, report


@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
def test_appends_across_block_boundaries_stay_incremental_without_a_rebuild(tmp_path, ps):
    log, script = _fixture(tmp_path, [FILLER] * 200)
    extra = tmp_path / "extra.jsonl"
    extra.write_text("".join(json.dumps(FILLER, separators=(",", ":")) + "\n" for _ in range(400)), encoding="utf-8")
    script += SETTLE + "$before=Settle\n"
    script += f"[IO.File]::AppendAllText({q(log)}, [IO.File]::ReadAllText({q(extra)}))\n"
    script += "$after=Settle\n$again=Check\n@{before=$before;after=$after;again=$again} | ConvertTo-Json -Depth 6 -Compress"
    report = json.loads(_run_powershell(script, executable=ps).stdout)
    assert report["before"][-1] == CLEAR, report
    assert all(item == CLEAR or (item["ok"] is False and "catching up" in item["error"])
               for item in report["after"]), report
    assert report["after"][-1] == CLEAR and report["again"] == CLEAR, report


@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize("where", ["behind-the-cursor", "in-the-bytes-just-parsed"])
def test_a_rewrite_during_the_delta_read_cannot_stay_clear(tmp_path, ps, where):
    # The pinned reader copy gains a hook that rewrites one row right after the delta read returns.
    hook = (
        "\n$script:WdTestDelta = ${function:Read-BridgeEventDelta}\n"
        "function Read-BridgeEventDelta {\n"
        " param([string] $Path, $Cursor, [int64] $MaxBytes, [int] $MaxRows)\n"
        " $result = & $script:WdTestDelta -Path $Path -Cursor $Cursor -MaxBytes $MaxBytes -MaxRows $MaxRows\n"
        " if ($env:WD_TEST_REWRITE_AT) {\n"
        "  $at = [int64]$env:WD_TEST_REWRITE_AT; Remove-Item Env:WD_TEST_REWRITE_AT\n"
        "  $stream=[IO.File]::Open($Path,[IO.FileMode]::Open,[IO.FileAccess]::Write,[IO.FileShare]::ReadWrite)\n"
        "  try { [void]$stream.Seek($at,[IO.SeekOrigin]::Begin); $bytes=[Text.Encoding]::ASCII.GetBytes('on_hold');"
        " $stream.Write($bytes,0,$bytes.Length); $stream.Flush($true) } finally { $stream.Dispose() }\n"
        " }\n"
        " $result\n"
        "}\n"
    )
    diagnostic = DIAGNOSTICS["worker-blocked-blocked"]
    first = [diagnostic, FILLER] if where == "behind-the-cursor" else [FILLER]
    log, script = _fixture(tmp_path, first, reader_suffix=hook)
    later = diagnostic if where == "in-the-bytes-just-parsed" else FILLER
    appended = json.dumps(later, separators=(",", ":")) + "\n"
    script += SETTLE + "$before=Settle\n"
    script += f"[IO.File]::AppendAllText({q(log)}, {q(appended)})\n"
    offset_script = (
        "$data=[IO.File]::ReadAllBytes(" + q(log) + "); $text=[Text.Encoding]::UTF8.GetString($data);"
        " $env:WD_TEST_REWRITE_AT=[string]($text.IndexOf('\"status\":\"blocked\"') + 10)\n"
    )
    script += offset_script + "$after=Settle\n@{before=$before;after=$after} | ConvertTo-Json -Depth 6 -Compress"
    report = json.loads(_run_powershell(script, executable=ps).stdout)
    assert report["before"][-1] == CLEAR, report
    assert CLEAR not in report["after"], report
    assert report["after"][-1] == {"ok": True, "held": True}, report


APPEND_HOOK = (
    "\n$script:WdTestDelta = ${function:Read-BridgeEventDelta}\n"
    "function Read-BridgeEventDelta {\n"
    " param([string] $Path, $Cursor, [int64] $MaxBytes, [int] $MaxRows)\n"
    " $row = $env:WD_TEST_APPEND_ROW\n"
    " if ($row) { Remove-Item Env:WD_TEST_APPEND_ROW; [IO.File]::AppendAllText($Path, $row) }\n"
    " $result = & $script:WdTestDelta -Path $Path -Cursor $Cursor -MaxBytes $MaxBytes -MaxRows $MaxRows\n"
    " if ($row -and $env:WD_TEST_REWRITE_APPENDED) {\n"
    "  $text = [Text.Encoding]::ASCII.GetString([IO.File]::ReadAllBytes($Path))\n"
    "  $at = $text.LastIndexOf('\"status\":\"blocked\"') + 10\n"
    "  $stream=[IO.File]::Open($Path,[IO.FileMode]::Open,[IO.FileAccess]::Write,[IO.FileShare]::ReadWrite)\n"
    "  try { [void]$stream.Seek($at,[IO.SeekOrigin]::Begin); $bytes=[Text.Encoding]::ASCII.GetBytes('on_hold');"
    " $stream.Write($bytes,0,$bytes.Length); $stream.Flush($true) } finally { $stream.Dispose() }\n"
    " }\n"
    " $result\n"
    "}\n"
)
CATCHING_UP = {"ok": False, "error": "Continuity canonical scan catching up; recovery withheld"}


@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize("rewrite", [True, False], ids=["rewritten-to-a-control", "left-unchanged"])
def test_a_row_appended_during_the_read_is_compared_before_the_cursor_moves(tmp_path, ps, rewrite):
    """Disclosed residual R2 on PR1854, reproduced by Tools: at EOF the pre-read copy is empty, so a row appended
    after it and rewritten before the post pass was anchored without a compare. Such a check is withheld as
    catching up (1 s retry, no alert) with the cursor unmoved, and the next check copies and compares the row.
    The log is over 1 MiB, so a guard that reset the cursor would need more than one further check to rescan."""
    log, script = _fixture(tmp_path, [FILLER] * 300, reader_suffix=APPEND_HOOK)
    assert log.stat().st_size > (1 << 20)
    row = json.dumps(DIAGNOSTICS["worker-blocked-blocked"], separators=(",", ":")) + "\n"
    script += SETTLE + "$before=Settle\n"
    script += f"$env:WD_TEST_APPEND_ROW={q(row)}\n"
    if rewrite:
        script += "$env:WD_TEST_REWRITE_APPENDED='1'\n"
    script += "$after=Settle\n$again=Check\n@{before=$before;after=$after;again=$again} | ConvertTo-Json -Depth 6 -Compress"
    report = json.loads(_run_powershell(script, executable=ps).stdout)
    assert report["before"][-1] == CLEAR, report
    assert report["after"][0] == CATCHING_UP, report
    # Withheld once, then the next check resumes from the unmoved cursor: no rescan from byte 0.
    assert len(report["after"]) == 2, report
    expected = {"ok": True, "held": rewrite}
    assert report["after"][-1] == expected and report["again"] == expected, report

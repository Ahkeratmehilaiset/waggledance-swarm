"""Read-only fleet observations using ordinary, isolated runtime records."""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "ops/windows/reboot/Get-WdSwarmParallelStatus.ps1"
SHELLS = sorted({p for p in (shutil.which("powershell.exe"), shutil.which("pwsh")) if p})
pytestmark = pytest.mark.skipif(not SHELLS or not shutil.which("git"), reason="PowerShell/Git required")
GENERATION = "a" * 40


def quote(value: object) -> str:
    return "'" + str(value).replace("'", "''") + "'"


@pytest.fixture(params=SHELLS or [None])
def fleet(request):
    audit = ROOT / ".codex-audit"
    audit.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="parallel-status-", dir=audit) as directory:
        root = Path(directory)
        now = datetime.now(timezone.utc)
        head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
        lanes = []
        for agent in ["codex-lead-1", "claude-rco-1", "claude-rco-2", "fable-5", "codex-tools-1"]:
            worktree = root / agent
            (worktree / ".codex-audit").mkdir(parents=True)
            checkpoint = {
                "schema": "wd.lane-current.v1", "agent": agent,
                "worktree": str(worktree), "updated_at_utc": now.isoformat(),
                "task_id": "fixture-task", "status": "working", "head": head,
                "write_scope": [], "next_action": "Run affected tests",
                "blockers": [],
            }
            (worktree / ".codex-audit/wd-current-state.json").write_text(json.dumps(checkpoint), encoding="utf-8")
            lanes.append({"agent": agent, "worktree": str(worktree),
                          "turn_mode": "managed" if agent == "codex-lead-1" else "interactive"})
        ready = root / "ready.json"
        started = (now - timedelta(minutes=2)).isoformat()
        ready.write_text(json.dumps({
            "schema": "wd.tools-consumer-ready.v1", "status": "ready",
            "generation": GENERATION, "pid": 12345, "process_start_utc": started,
            "ready_at_utc": (now - timedelta(minutes=1)).isoformat(),
            "worktree": lanes[-1]["worktree"],
        }), encoding="utf-8")
        manifest = root / "fleet.json"
        manifest.write_text(json.dumps({
            "schema_version": 2, "git_executable": shutil.which("git"),
            "runtime_root": str(root), "handshake_root": str(root / "handshakes"), "lanes": lanes[:-1],
            "tools_supervisor": dict(lanes[-1], task_name="WD-Supervisor", readiness_path=str(ready),
                                     model="gpt-5.6-terra", reasoning_effort="high"),
        }), encoding="utf-8")
        pointer = root / "pointer.json"
        pointer.write_text(json.dumps({
            "schema_version": 1, "source_commit": GENERATION,
            "active_bundle": str(root), "fleet_manifest": str(manifest),
            "installed_at_utc": (now - timedelta(hours=1)).isoformat(),
        }), encoding="utf-8")
        yield {"root": root, "shell": request.param, "manifest": manifest,
               "pointer": pointer, "ready": ready, "lanes": lanes,
               "started": started, "head": head}


def update(path: Path, **changes):
    record = json.loads(path.read_text(encoding="utf-8"))
    record.update(changes)
    path.write_text(json.dumps(record), encoding="utf-8")


def checkpoint(fleet, index=-1):
    return Path(fleet["lanes"][index]["worktree"]) / ".codex-audit/wd-current-state.json"


@pytest.mark.parametrize("relative_root", ["", "Python"])
def test_installed_status_resolves_selected_bundle_from_shallow_root(fleet, monkeypatch, relative_root):
    """Exercise actual shallow-path resolution without writing machine files."""
    machine_root = Path(ROOT.anchor) / relative_root
    helpers = fleet["root"] / "tools-bootstrap/.agent-bridge/bin"
    shutil.copytree(ROOT / ".agent-bridge/bin", helpers)
    copied = fleet["root"] / "installed-status.ps1"
    copied.write_text(SCRIPT.read_text(encoding="utf-8").replace(
        "$PSScriptRoot", quote(machine_root)), encoding="utf-8")
    monkeypatch.setattr(__import__(__name__, fromlist=["SCRIPT"]), "SCRIPT", copied)
    report = run_status(fleet)
    assert len(report["lanes"]) == 5
    assert report["claim_observation"]["status"] == "observed"


@pytest.mark.parametrize('case', ['local', 'source_overlap', 'expired', 'checkpoint_only', 'invalid', 'read_claim'])
def test_conflicts_use_live_claim_resources_not_checkpoint_strings(fleet, case):
    scopes = ['.codex-audit/wd-current-state.json'] if case != 'source_overlap' else ['src/module.py']
    claims = fleet['root'] / 'work_queue/claims'
    claims.mkdir(parents=True)
    for index in (0, 4):
        update(checkpoint(fleet, index), write_scope=scopes, status='completed')
        if case == 'checkpoint_only':
            continue
        now = datetime.now(timezone.utc)
        record = dict(agent=fleet['lanes'][index]['agent'], task_id='active-test',
                      mode='read' if case == 'read_claim' else 'write',
                      cwd=fleet['lanes'][index]['worktree'], write_scope=scopes,
                      last_heartbeat_utc=(now - timedelta(seconds=400 if case == 'expired' else 0)).isoformat(),
                      lease_seconds=300)
        (claims / f'{index}.json').write_text('{broken' if case == 'invalid' else json.dumps(record))
    report = run_status(fleet)
    assert report['summary']['scope_collisions'] == (1 if case == 'source_overlap' else 0)
    assert report['claim_observation']['status'] == ('unknown' if case == 'invalid' else 'observed')


def test_fresh_canonical_answer_is_separate_from_old_checkpoint(fleet):
    old = (datetime.now(timezone.utc) - timedelta(hours=4)).isoformat()
    update(checkpoint(fleet, 2), status='idle', updated_at_utc=old)
    shared = fleet['root'] / 'shared'
    shared.mkdir()
    now = datetime.now(timezone.utc).isoformat()
    answer = dict(agent='claude-rco-2', type='message', status='answered', ts_utc=now,
                  in_reply_to_request_id='req1', task_id='task')
    (shared / 'events.jsonl').write_text(json.dumps(answer) + '\n')
    report = run_status(fleet)
    lane = report['lanes'][2]
    assert lane['checkpoint']['freshness'] == 'stale'
    assert datetime.fromisoformat(lane['progress']['last_substantive_progress_at_utc']) == datetime.fromisoformat(now)
    assert lane['progress']['status'] == 'canonical_answer_observed'
    assert lane['progress']['task_completion_verified'] is False
    assert lane['health_observation']['last_answer_at_utc'] == lane['progress']['last_substantive_progress_at_utc']
    assert lane['health_observation']['task_completion_verified'] is False


@pytest.mark.parametrize('case', ['missing', 'legacy', 'filtered', 'wrong_agent', 'invalid',
                                 'drained', 'pending', 'noise', 'partial', 'replaced', 'truncated'])
def test_wake_sentinel_is_not_pending_work_and_unknown_stays_unknown(fleet, case):
    root = fleet['root']
    shared = root / 'shared'
    shared.mkdir()
    events = shared / 'events.jsonl'
    seed = dict(agent='operator', to='claude-rco-2', type='message', status='notice',
                task_id='seed', payload={'notification': 'informational'})
    events.write_text(json.dumps(seed) + '\n')
    sentinel = root / 'wake_claude-rco-2'
    sentinel.write_text('2026-09-18T18:48:34Z')
    state = shared / 'monitor_claude-rco-2.cursor.json'
    if case != 'missing':
        result = subprocess.run([fleet['shell'], '-NoProfile', '-NonInteractive', '-Command',
            f"& {quote(ROOT / '.agent-bridge/bin/Monitor-AgentBridge.ps1')} -Agent claude-rco-2 "
            f"-RuntimeRoot {quote(root)} -TargetedOnly -IncludeWakeRequests -Json -MaxIterations 1"],
            text=True, capture_output=True, timeout=45)
        assert result.returncode == 0, result.stdout + result.stderr
        record = json.loads(state.read_text())
        if case == 'legacy': record['metadata'].pop('delivery_scope')
        if case == 'filtered': record['metadata']['from_agent'] = 'operator'
        if case == 'wrong_agent': record['metadata']['agent'] = 'claude-rco-1'
        state.write_text('{bad' if case == 'invalid' else json.dumps(record))
    if case in ('pending', 'noise'):
        row = dict(seed, task_id='new', status='request' if case == 'pending' else 'notice')
        with events.open('a') as stream: stream.write(json.dumps(row) + '\n')
    if case == 'partial':
        with events.open('a') as stream: stream.write('{"agent":')
    if case == 'replaced':
        replacement = shared / 'replacement'
        replacement.write_text(events.read_text())
        replacement.replace(events)
    if case == 'truncated': events.write_text('')
    report = run_status(fleet)
    lane = report['lanes'][2]
    expected = True if case == 'pending' else False if case in ('drained', 'noise') else None
    assert lane['sentinel_present'] is True
    assert lane['wake_pending'] is expected, lane['wake_observation']
    assert report['summary']['pending_wakes'] == (1 if case == 'pending' else 0)
    assert report['summary']['unknown_wake_observations'] == (5 if expected is None else 4)
    assert sentinel.read_text() == '2026-09-18T18:48:34Z'
    assert lane['wake_observation']['task_completion_verified'] is False


@pytest.mark.parametrize('case', ['live', 'wrong_parent', 'reused_pid', 'missing_native'])
def test_native_tools_status_requires_both_process_identities(fleet, case):
    manifest = json.loads(fleet['manifest'].read_text())
    manifest['tools_supervisor']['conversation_surface'] = 'native_terminal'
    fleet['manifest'].write_text(json.dumps(manifest))
    native_at = (datetime.fromisoformat(fleet['started']) + timedelta(seconds=10)).isoformat()
    update(fleet['ready'], schema='wd.tools-consumer-ready.v3', status='terminal_ready',
           readiness_scope='native_cli_only', conversation_surface='native_terminal',
           thread_id='01a0a07b-ca98-71e1-90cb-d588435a2d8d', task_completion_verified=False,
           native_pid=22345, native_parent_pid=12345, native_process_start_utc=native_at,
           codex_command=r'C:\Codex\codex.exe')
    wrapper = dict(ProcessId=12345, ParentProcessId=1, Name='powershell.exe',
                   CreationDate=fleet['started'], CommandLine='powershell -File start-wd-tools-consumer.ps1 -Generation ' + GENERATION)
    native = dict(ProcessId=22345, ParentProcessId=12345, Name='codex.exe',
                  CreationDate=native_at, ExecutablePath=r'C:\Codex\codex.exe',
                  CommandLine='codex resume 01a0a07b-ca98-71e1-90cb-d588435a2d8d')
    if case == 'wrong_parent': native['ParentProcessId'] = 99
    if case == 'reused_pid': native['CreationDate'] = datetime.now(timezone.utc).isoformat()
    processes = [wrapper] if case == 'missing_native' else [wrapper, native]
    report = run_status(fleet, lane_processes=processes)
    tools = next(lane for lane in report['lanes'] if lane['agent'] == 'codex-tools-1')
    assert tools['configured_conversation_surface'] == 'native_terminal'
    assert (tools['runtime']['identity'] == 'matched') is (case == 'live'), tools['runtime']
    if case == 'live':
        assert tools['runtime']['readiness_scope'] == 'native_cli_only'
        assert not tools['runtime']['native_checkpoint']['latest_final_recorded_verified']


def run_status(fleet, *, process="present", task="Ready", generation=GENERATION,
               started=None, lane_processes=None, runtime_processes=None):
    before = {str(p): (p.read_bytes(), p.stat().st_mtime_ns) for p in fleet["root"].rglob("*") if p.is_file()}
    process_body = {
        "absent": "return",
        "unknown": "throw 'process query unavailable'",
        "present": "[pscustomobject]@{ ProcessId = 12345; CreationDate = [datetime]" + quote(started or fleet["started"]) +
        "; CommandLine = " + quote("powershell.exe -File start-wd-tools-consumer.ps1 -Generation " + generation) + " }",
    }[process]
    if runtime_processes is not None:
        process_body = "$wanted=[int]($Filter -replace '^ProcessId=',''); foreach ($row in (ConvertFrom-Json -InputObject " + quote(json.dumps(runtime_processes)) + ")) { if ([int]$row.ProcessId -eq $wanted) { $row } }"
    task_body = "throw 'task query unavailable'" if task == "unknown" else "[pscustomobject]@{ State = " + quote(task) + " }"
    command = """
function Get-CimInstance { param($ClassName, $Filter, $ErrorAction)
    if (-not $Filter) { LANE_PROCESS_BODY; return }
    PROCESS_BODY
}
function Get-ScheduledTask { param($TaskName, $ErrorAction) TASK_BODY }
function Start-Process { throw 'status attempted process start' }
function Set-Content { throw 'status attempted write' }
function Enable-ScheduledTask { throw 'status attempted task enable' }
& SCRIPT -ManifestPath MANIFEST POINTER -Json
""".replace("LANE_PROCESS_BODY", "throw 'process query unavailable'" if process == "unknown" else
             "foreach ($row in (ConvertFrom-Json -InputObject " + quote(json.dumps(lane_processes or [])) + ")) { $row }").replace("PROCESS_BODY", process_body).replace("TASK_BODY", task_body).replace("SCRIPT", quote(SCRIPT)).replace("MANIFEST", quote(fleet["manifest"])).replace("POINTER", "-CurrentStatePath " + quote(fleet["pointer"]))
    result = subprocess.run([fleet["shell"], "-NoProfile", "-NonInteractive", "-Command", command], cwd=ROOT,
                            capture_output=True, text=True, timeout=45)
    assert result.returncode == 0, result.stderr
    after = {str(p): (p.read_bytes(), p.stat().st_mtime_ns) for p in fleet["root"].rglob("*") if p.is_file()}
    assert before == after, "status mutated its evidence or runtime directory"
    return json.loads(result.stdout)


def lane_handshake(fleet, mode="interactive"):
    run_id = "retained-session"
    directory = fleet["root"] / "handshakes" / run_id
    directory.mkdir(parents=True)
    path = directory / "codex-lead-1.json"
    record = {
        "schema_version": 1, "status": "bridge_bootstrapped", "agent": "codex-lead-1",
        "pid": 54321, "run_id": run_id, "session_id": run_id,
        "created_at_utc": (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
        "worktree": fleet["lanes"][0]["worktree"], "runtime_root": str(fleet["root"]),
        "bundle_generation": GENERATION,
    }
    if mode is not None:
        record["turn_mode"] = mode
    path.write_text(json.dumps(record), encoding="utf-8")
    process = {"Name": "powershell.exe", "ProcessId": 54321, "CreationDate": fleet["started"],
               "CommandLine": f'powershell.exe -File C:\\Python\\start-wd-agent.ps1 -Agent codex-lead-1 -RunId {run_id} -HandshakeDirectory "{directory}"'}
    return path, process


@pytest.mark.parametrize('case', ['live', 'wrong_parent', 'wrong_thread', 'reused_pid', 'blocked'])
def test_native_lead_queue_readiness_requires_live_exact_conversation(fleet, case):
    _, wrapper = lane_handshake(fleet)
    thread = '01a0a654-12af-7d81-85fc-d75d515c5b65'
    worktree = Path(fleet['lanes'][0]['worktree'])
    journal = worktree / '.codex-audit/wd-turn-loop'
    journal.mkdir()
    ready = dict(schema='wd.native-lead-ready.v1', agent='codex-lead-1', status='terminal_ready',
                 bridge_wake_transport='codex_queue', generation=GENERATION, session_id='retained-session',
                 relay_pid=54321, native_pid=54322, thread_id=thread, worktree=str(worktree),
                 native_process_start_utc=fleet['started'], relay_process_start_utc=fleet['started'])
    native = dict(Name='codex.exe', ProcessId=54322, ParentProcessId=54321,
                  CreationDate=fleet['started'], CommandLine='codex resume ' + thread)
    if case == 'wrong_parent': native['ParentProcessId'] = 1
    if case == 'wrong_thread': native['CommandLine'] = 'codex resume other'
    if case == 'reused_pid': native['CreationDate'] = datetime.now(timezone.utc).isoformat()
    if case == 'blocked': ready.update(status='bridge_wake_blocked', error='DateTime parse failed')
    (journal / 'native-terminal.json').write_text(json.dumps(ready))
    lead = run_status(fleet, lane_processes=[wrapper, native])['lanes'][0]['turn_execution']
    assert lead['external_wake_support'] == ('native_queue_bridge' if case == 'live' else 'native_queue_unverified')
    assert not lead['turn_execution_verified']
    if case == 'blocked':
        assert lead['relay_status'] == 'bridge_wake_blocked'
        assert lead['relay_error'] == 'DateTime parse failed'


def test_conversation_configuration_is_not_live_window_or_context_proof(fleet):
    manifest = json.loads(fleet["manifest"].read_text(encoding="utf-8"))
    manifest["lanes"][0]["conversation_surface"] = "local_window"
    fleet["manifest"].write_text(json.dumps(manifest), encoding="utf-8")
    _, process = lane_handshake(fleet, mode=None)
    report = run_status(fleet, lane_processes=[process])
    lead = next(lane for lane in report["lanes"] if lane["agent"] == "codex-lead-1")
    assert lead["configured_conversation_surface"] == "local_window"
    assert lead["conversation_control_verified"] is False
    assert lead["turn_execution"]["observed_turn_mode"] == "legacy_interactive"
    assert lead["turn_execution"]["turn_execution_verified"] is False


def test_tools_and_lead_configured_windows_are_separate_from_live_control_evidence(fleet):
    manifest = json.loads(fleet["manifest"].read_text())
    manifest["lanes"][0].update(turn_mode="managed", conversation_surface="local_window")
    manifest["tools_supervisor"]["conversation_surface"] = "local_window"
    fleet["manifest"].write_text(json.dumps(manifest))
    report = run_status(fleet)
    assert [lane["configured_conversation_surface"] for lane in report["lanes"]] == ["local_window", "none", "none", "none", "local_window"]
    assert all(lane["conversation_control_verified"] is False for lane in report["lanes"])
    tools = report["lanes"][-1]
    assert tools["runtime"]["identity"] == "unknown"
    assert tools["runtime"]["reason"] == "conversation_readiness_v2_required"
    assert tools["runnable_evidence"] != "observed"


@pytest.mark.parametrize("posture,expected", [
    (None, "workspace_write"), ("workspace_write", "workspace_write"),
    ("existing_interactive", "existing_interactive"), ("guess", "unknown"),
    ("malformed_policy", "unknown"),
])
def test_configured_permission_posture_is_not_live_permission_evidence(fleet, posture, expected):
    manifest = json.loads(fleet["manifest"].read_text())
    manifest["lanes"][0]["conversation_surface"] = "local_window"
    if posture is not None:
        manifest["lanes"][0]["conversation_permissions"] = (
            "invalid policy object" if posture == "malformed_policy" else
            {"posture": posture, "network_access": True, "additional_writable_roots": []}
        )
    manifest["tools_supervisor"]["conversation_surface"] = "local_window"
    fleet["manifest"].write_text(json.dumps(manifest))
    _, process = lane_handshake(fleet, mode=None)
    report = run_status(fleet, lane_processes=[process])
    assert [lane["configured_permission_posture"] for lane in report["lanes"]] == [
        expected, "not_applicable", "not_applicable", "not_applicable", "workspace_write"
    ]
    assert report["lanes"][0]["turn_execution"]["observed_turn_mode"] == "legacy_interactive"
    assert all(lane["conversation_control_verified"] is False for lane in report["lanes"])
    assert "not live" in report["semantics"]["configured_permission_posture"]
    assert "Name='cfg_posture'" in SCRIPT.read_text(encoding="utf-8")


def test_unknown_tools_surface_cannot_reuse_legacy_readiness(fleet):
    manifest = json.loads(fleet["manifest"].read_text())
    manifest["tools_supervisor"]["conversation_surface"] = "guess"
    fleet["manifest"].write_text(json.dumps(manifest))
    tools = run_status(fleet)["lanes"][-1]
    assert tools["configured_conversation_surface"] == "unknown"
    assert tools["runtime"]["identity"] == "unknown"
    assert tools["runnable_evidence"] != "observed"


def tools_conversation_ready(fleet):
    manifest = json.loads(fleet["manifest"].read_text())
    manifest["tools_supervisor"]["conversation_surface"] = "local_window"
    fleet["manifest"].write_text(json.dumps(manifest))
    now = datetime.now(timezone.utc)
    native_started = (now - timedelta(seconds=90)).isoformat()
    native_path = str(fleet["root"] / "codex.exe")
    update(fleet["ready"], schema="wd.tools-consumer-ready.v2", status="transport_ready",
           readiness_scope="ui_transport_only", conversation_surface="local_window",
           transport_ready=True, task_completion_verified=False,
           transport_ready_at_utc=(now - timedelta(seconds=60)).isoformat(),
           agent="codex-tools-1", model="gpt-5.6-terra", reasoning_effort="high",
           run_id="tools-session", session_id="tools-session", thread_id="tools-thread",
           native_pid=23456, native_parent_pid=12345, native_process_start_utc=native_started,
           codex_command=native_path, native_checkpoint_verified=False)
    return [
        {"Name": "powershell.exe", "ProcessId": 12345, "CreationDate": fleet["started"],
         "CommandLine": f'powershell.exe -STA -File "{fleet["root"] / "start-wd-tools-consumer.ps1"}" -Generation {GENERATION}'},
        {"Name": "codex.exe", "ProcessId": 23456, "ParentProcessId": 12345,
         "CreationDate": native_started, "ExecutablePath": native_path,
         "CommandLine": f'"{native_path}" app-server --listen stdio://'},
    ]


@pytest.mark.parametrize("arguments", [
    "app-server --listen stdio://",
    '"app-server" "--listen" "stdio://"',
    '"app-server" --listen stdio://',
    'app-server "--listen" "stdio://"',
])
def test_tools_v2_transport_identity_does_not_imply_checkpoint_or_progress(fleet, arguments):
    processes = tools_conversation_ready(fleet)
    processes[1]["CommandLine"] = f'"{processes[1]["ExecutablePath"]}" {arguments}'
    tools = run_status(fleet, runtime_processes=processes)["lanes"][-1]
    assert tools["runtime"]["identity"] == "matched"
    assert tools["runtime"]["readiness_scope"] == "ui_transport_only"
    assert tools["runtime"]["transport_ready_verified"] is True
    assert tools["runtime"]["observed_native_pid"] == 23456
    assert tools["runtime"]["thread_id"] == "tools-thread"
    assert tools["runtime"]["native_checkpoint"]["latest_final_recorded_verified"] is False
    assert tools["runnable_evidence"] == "unknown"
    assert tools["conversation_control_verified"] is False
    assert tools["progress"]["status"] == "unknown"


@pytest.mark.parametrize("arguments", [
    '"app-server --listen stdio://"',
    '"app-server --listen stdio://',
    'app-server" --listen stdio://',
    '"app-server\' --listen stdio://',
    'app-server "--listen stdio://',
    'app-server --listen" stdio://',
    'app-server --listen "stdio://',
    'app-server --listen stdio://"',
    'app-server-other --listen stdio://',
    'app-server --listen-other stdio://',
    'app-server --listen tcp://127.0.0.1:1234',
])
def test_tools_v2_native_arguments_must_be_complete_exact_tokens(fleet, arguments):
    processes = tools_conversation_ready(fleet)
    processes[1]["CommandLine"] = f'"{processes[1]["ExecutablePath"]}" {arguments}'
    tools = run_status(fleet, runtime_processes=processes)["lanes"][-1]
    assert tools["runtime"]["identity"] != "matched"
    assert tools["runtime"]["transport_ready_verified"] is False
    assert tools["runtime"]["observed_native_pid"] is None


@pytest.mark.parametrize("case", ["native_absent", "native_pid_reused", "native_parent", "native_path", "wrapper_file", "wrapper_generation_quote", "wrapper_generation_duplicate", "multiple_native", "thread_missing", "session_mismatch", "truthy_transport", "future_transport", "wrong_pin"])
def test_tools_v2_unbound_transport_is_never_positive(fleet, case):
    processes = tools_conversation_ready(fleet)
    if case == "native_absent": processes.pop()
    if case == "native_pid_reused": processes[1]["CreationDate"] = datetime.now(timezone.utc).isoformat()
    if case == "native_parent": processes[1]["ParentProcessId"] = 999
    if case == "native_path": processes[1]["ExecutablePath"] = str(fleet["root"] / "other-codex.exe")
    if case == "wrapper_file": processes[0]["CommandLine"] = processes[0]["CommandLine"].replace("start-wd-tools-consumer.ps1", "unrelated.ps1")
    if case == "wrapper_generation_quote": processes[0]["CommandLine"] = processes[0]["CommandLine"].replace(GENERATION, '"' + GENERATION + "'")
    if case == "wrapper_generation_duplicate": processes[0]["CommandLine"] += " -Generation " + GENERATION
    if case == "multiple_native": processes.append(dict(processes[1]))
    if case == "thread_missing": update(fleet["ready"], thread_id="")
    if case == "session_mismatch": update(fleet["ready"], session_id="other-session")
    if case == "truthy_transport": update(fleet["ready"], transport_ready="true")
    if case == "future_transport": update(fleet["ready"], transport_ready_at_utc="2099-01-01T00:00:00Z")
    if case == "wrong_pin": update(fleet["ready"], model="other-model")
    tools = run_status(fleet, runtime_processes=processes)["lanes"][-1]
    assert tools["runtime"]["identity"] != "matched"
    assert tools["runtime"]["transport_ready_verified"] is False
    assert tools["runtime"]["observed_native_pid"] is None
    assert tools["conversation_control_verified"] is False
    assert tools["runnable_evidence"] != "observed"


def test_tools_v2_latest_final_is_separate_from_previous_checkpoint(fleet):
    processes = tools_conversation_ready(fleet)
    stamp = (datetime.now(timezone.utc) - timedelta(seconds=20)).isoformat()
    update(fleet["ready"], last_turn_id="turn-"+"b"*32, last_native_turn_id="native-second",
           last_native_status="interrupted", last_turn_disposition="interrupted",
           last_turn_finalized_at_utc=stamp, native_checkpoint_verified=False,
           last_checkpoint_turn_id="turn-"+"c"*32, last_checkpoint_native_turn_id="native-first",
           last_checkpoint_disposition="blocked", last_checkpoint_verified_at_utc=stamp)
    tools = run_status(fleet, runtime_processes=processes)["lanes"][-1]
    checkpoint_record = tools["runtime"]["native_checkpoint"]
    assert checkpoint_record["status"] == "recorded"
    assert checkpoint_record["latest_final_recorded_verified"] is False
    assert checkpoint_record["last_turn_id"] == "turn-"+"b"*32
    assert checkpoint_record["last_checkpoint_turn_id"] == "turn-"+"c"*32
    assert tools["progress"]["status"] == "unknown" and tools["runnable_evidence"] == "unknown"


@pytest.mark.parametrize("case", ["valid", "truthy_flag", "wrong_turn", "future_time"])
def test_tools_v2_checkpoint_record_does_not_replace_transport_or_progress_evidence(fleet, case):
    processes = tools_conversation_ready(fleet)
    stamp = (datetime.now(timezone.utc) - timedelta(seconds=20)).isoformat()
    update(fleet["ready"], last_turn_id="turn-"+"b"*32, last_native_turn_id="native-turn",
           last_native_status="completed", last_turn_disposition="blocked",
           last_turn_finalized_at_utc=stamp, native_checkpoint_verified=True,
           last_checkpoint_turn_id="turn-"+"b"*32, last_checkpoint_native_turn_id="native-turn",
           last_checkpoint_disposition="blocked", last_checkpoint_verified_at_utc=stamp)
    if case == "truthy_flag": update(fleet["ready"], native_checkpoint_verified="true")
    if case == "wrong_turn": update(fleet["ready"], last_checkpoint_turn_id="turn-"+"c"*32)
    if case == "future_time": update(fleet["ready"], last_checkpoint_verified_at_utc="2099-01-01T00:00:00Z")
    tools = run_status(fleet, runtime_processes=processes)["lanes"][-1]
    assert tools["runtime"]["identity"] == "matched" and tools["runtime"]["transport_ready_verified"] is True
    assert tools["runtime"]["native_checkpoint"]["latest_final_recorded_verified"] is (case == "valid")
    assert tools["runtime"]["native_checkpoint"]["status"] == ("recorded" if case == "valid" else "invalid_record")
    assert tools["conversation_control_verified"] is False
    assert tools["progress"]["status"] == "unknown" and tools["runnable_evidence"] == "unknown"


@pytest.mark.parametrize("mode,observed,support", [
    (None, "legacy_interactive", "unsupported_existing_interactive"),
    ("interactive", "interactive", "unsupported_existing_interactive"),
    ("managed", "managed", "not_verified"),
])
def test_configured_mode_is_not_retained_live_mode_or_turn_proof(fleet, mode, observed, support):
    _, process = lane_handshake(fleet, mode)
    lane = run_status(fleet, lane_processes=[process])["lanes"][0]
    assert lane["configured_turn_mode"] == "managed"
    assert lane["turn_execution"]["observed_turn_mode"] == observed, lane["turn_execution"]["reason"]
    assert lane["turn_execution"]["external_wake_support"] == support
    assert lane["turn_execution"]["observed_pid"] == 54321
    assert lane["turn_execution"]["turn_execution_verified"] is False
    assert lane["runnable_evidence"] == "unknown"


@pytest.mark.parametrize("case", ["absent", "query_unknown", "multiple", "pid_mismatch", "reused_pid", "session_mismatch", "missing_handshake", "unknown_mode", "outside_handshake_root", "missing_worktree"])
def test_unproved_live_mode_stays_unknown_despite_managed_configuration(fleet, case):
    path, process = lane_handshake(fleet)
    processes = [process]
    kwargs = {}
    if case == "absent": processes = []
    if case == "query_unknown": kwargs["process"] = "unknown"
    if case == "multiple": processes.append(dict(process, ProcessId=54322))
    if case == "pid_mismatch": update(path, pid=54322)
    if case == "reused_pid": process["CreationDate"] = datetime.now(timezone.utc).isoformat()
    if case == "session_mismatch": update(path, session_id="another-session")
    if case == "missing_handshake": path.unlink()
    if case == "unknown_mode": update(path, turn_mode="guess")
    if case == "missing_worktree":
        record = json.loads(path.read_text())
        del record["worktree"]
        path.write_text(json.dumps(record))
    if case == "outside_handshake_root": process["CommandLine"] = process["CommandLine"].replace(str(path.parent), str(fleet["root"]))
    lane = run_status(fleet, lane_processes=processes, **kwargs)["lanes"][0]
    assert lane["configured_turn_mode"] == "managed"
    assert lane["turn_execution"]["observed_turn_mode"] == "unknown"
    assert lane["turn_execution"]["external_wake_support"] == "unknown"
    assert lane["turn_execution"]["turn_execution_verified"] is False


def test_stale_checkpoint_keeps_legacy_plan_but_has_no_fresh_runnable_evidence(fleet):
    update(checkpoint(fleet), updated_at_utc="2020-01-01T00:00:00Z")
    report = run_status(fleet)
    lane = report["lanes"][-1]
    assert lane["state_health"] == "stale"
    assert lane["runnable"] is True  # Legacy means a recorded next action exists.
    assert lane["runnable_evidence"] == "unknown"
    assert report["summary"]["fresh_runnable_evidence_lanes"] == 0


def test_current_runtime_is_bound_to_pid_start_and_generation(fleet):
    report = run_status(fleet)
    lane = report["lanes"][-1]
    assert lane["runnable_evidence"] == "observed"
    assert lane["runtime"]["identity"] == "matched"
    assert lane["runtime"]["observed_pid"] == 12345
    assert lane["runtime"]["observed_generation"] == GENERATION
    assert lane["checkpoint"]["source_domain"] == "lane_checkpoint"
    assert report["installed_bundle"]["source_commit"] == GENERATION
    assert lane["head"] == fleet["head"] != GENERATION
    assert report["summary"]["fresh_runnable_evidence_lanes"] == 1
    assert all(item["runtime"]["identity"] == "unknown" for item in report["lanes"][:-1])
    assert all(item["progress"]["status"] == "unknown" for item in report["lanes"])


@pytest.mark.parametrize("case", ["absent", "query_unknown", "pid_reused", "generation_mismatch", "record_generation_mismatch", "missing_ready", "invalid_ready", "old_ready", "degraded", "missing_pointer", "invalid_pointer", "unknown_task", "future_checkpoint", "head_mismatch"])
def test_incomplete_or_mismatched_runtime_never_becomes_fresh_runnable(fleet, case):
    kwargs = {}
    if case == "absent": kwargs["process"] = "absent"
    if case == "query_unknown": kwargs["process"] = "unknown"
    if case == "pid_reused": kwargs["started"] = datetime.now(timezone.utc).isoformat()
    if case == "generation_mismatch": kwargs["generation"] = "b" * 40
    if case == "record_generation_mismatch": update(fleet["ready"], generation="b" * 40)
    if case == "missing_ready": fleet["ready"].unlink()
    if case == "invalid_ready": fleet["ready"].write_text("{", encoding="utf-8")
    if case == "old_ready": update(fleet["ready"], ready_at_utc="2020-01-01T00:00:00Z")
    if case == "degraded": update(fleet["ready"], status="degraded")
    if case == "missing_pointer": fleet["pointer"].unlink()
    if case == "invalid_pointer": update(fleet["pointer"], source_commit="not-a-commit")
    if case == "unknown_task": kwargs["task"] = "unknown"
    if case == "future_checkpoint": update(checkpoint(fleet), updated_at_utc="2099-01-01T00:00:00Z")
    if case == "head_mismatch": update(checkpoint(fleet), head="b" * 40)
    report = run_status(fleet, **kwargs)
    assert report["lanes"][-1]["runnable_evidence"] != "observed"
    assert report["summary"]["fresh_runnable_evidence_lanes"] == 0


def test_disabled_supervisor_is_an_independent_intentional_state(fleet):
    report = run_status(fleet, task="Disabled")
    assert report["supervisor"]["status"] == "disabled"
    assert report["lanes"][-1]["runtime"]["identity"] == "matched"
    assert report["lanes"][-1]["runnable_evidence"] == "not_observed"
    assert report["summary"]["fresh_runnable_evidence_lanes"] == 0


@pytest.mark.parametrize("status,blockers", [("blocked", []), ("working", ["Await owner decision"])])
def test_checkpoint_blockers_prevent_fresh_runnable_claim(fleet, status, blockers):
    update(checkpoint(fleet), status=status, blockers=blockers)
    lane = run_status(fleet)["lanes"][-1]
    assert lane["runnable_evidence"] == "not_observed"


def test_heartbeat_and_checkpoint_time_are_not_substantive_progress(fleet):
    (fleet["root"] / "heartbeat_codex-tools-1.json").write_text(json.dumps({"ts_utc": datetime.now(timezone.utc).isoformat()}), encoding="utf-8")
    report = run_status(fleet)
    for lane in report["lanes"]:
        assert lane["progress"]["status"] == "unknown"
        assert lane["progress"]["last_substantive_progress_at_utc"] is None
        assert lane["progress"]["wait_age_seconds"] is None


@pytest.mark.parametrize("case", ["missing", "invalid_json", "missing_head", "missing_scope", "oversized"])
def test_missing_or_invalid_checkpoint_remains_unknown(fleet, case):
    path = checkpoint(fleet)
    if case == "missing":
        path.unlink()
    elif case == "invalid_json":
        path.write_text("{", encoding="utf-8")
    elif case == "oversized":
        update(path, next_action="a" * 33000)
    else:
        record = json.loads(path.read_text(encoding="utf-8"))
        del record["head" if case == "missing_head" else "write_scope"]
        path.write_text(json.dumps(record), encoding="utf-8")
    lane = run_status(fleet)["lanes"][-1]
    assert lane["runnable_evidence"] == "unknown"
    assert lane["state_health"] == ("missing" if case == "missing" else "invalid")


def test_other_selected_manifest_does_not_claim_installed_runtime(fleet):
    update(fleet["pointer"], fleet_manifest=str(fleet["root"] / "other-fleet.json"))
    report = run_status(fleet)
    assert report["installed_bundle"]["matches_selected_manifest"] is False
    assert report["lanes"][-1]["runtime"]["identity"] == "unknown"


def test_old_readiness_can_match_a_durable_process_but_is_not_progress(fleet):
    start = "2020-01-01T00:00:00+00:00"
    update(fleet["ready"], process_start_utc=start, ready_at_utc="2020-01-01T00:01:00+00:00")
    lane = run_status(fleet, started=start)["lanes"][-1]
    assert lane["runtime"]["identity"] == "matched"
    assert lane["runtime"]["readiness_age_seconds"] > 1800
    assert lane["progress"]["status"] == "unknown"


@pytest.mark.parametrize("status,expected", [("unknown", "unknown"), ("awaiting_ci", "unknown"), ("waiting", "not_observed"), ("completed", "not_observed")])
def test_free_form_checkpoint_status_does_not_imply_runnable(fleet, status, expected):
    update(checkpoint(fleet), status=status)
    lane = run_status(fleet)["lanes"][-1]
    assert lane["runnable"] is True
    assert lane["runnable_evidence"] == expected


def test_record_read_allows_concurrent_atomic_replacement(fleet):
    """A writer may replace the canonical record while its old snapshot is read."""
    path = fleet["ready"]
    replacement = path.with_suffix(".next.json")
    path.write_text('{"value":"before"}', encoding="utf-8")
    replacement.write_text('{"value":"after"}', encoding="utf-8")
    command = rf"""
$ErrorActionPreference = 'Stop'
$tokens = $null; $errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile({quote(SCRIPT)}, [ref]$tokens, [ref]$errors)
$reader = $ast.Find({{ param($node)
    $node -is [Management.Automation.Language.FunctionDefinitionAst] -and
    $node.Name -eq 'Read-WdStatusRecord'
}}, $true)
if (-not $reader -or $errors.Count) {{ throw 'Status reader could not be parsed' }}
Invoke-Expression $reader.Extent.Text
# Parsing occurs inside the reader's try/finally while its read handle is
# still held. This deterministic interleaving requires no timing or retries.
function ConvertFrom-Json {{
    [CmdletBinding()]
    param([Parameter(ValueFromPipeline)] [string] $InputObject)
    process {{
        [IO.File]::Replace({quote(replacement)}, {quote(path)}, {quote(path.with_suffix('.previous.json'))})
        Microsoft.PowerShell.Utility\ConvertFrom-Json -InputObject $InputObject
    }}
}}
$snapshot = Read-WdStatusRecord -Path {quote(path)}
$current = Microsoft.PowerShell.Utility\ConvertFrom-Json -InputObject ([IO.File]::ReadAllText({quote(path)}))
# The old snapshot is now the backup. Exclusive access proves its read handle
# was disposed after parsing, rather than leaked across subsequent observations.
$exclusive = [IO.File]::Open({quote(path.with_suffix('.previous.json'))}, [IO.FileMode]::Open,
    [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
$exclusive.Dispose()
[pscustomobject]@{{ snapshot = $snapshot.value; current = $current.value }} | ConvertTo-Json -Compress
"""
    result = subprocess.run([fleet["shell"], "-NoProfile", "-NonInteractive", "-Command", command],
                            cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"snapshot": "before", "current": "after"}
    assert not replacement.exists()

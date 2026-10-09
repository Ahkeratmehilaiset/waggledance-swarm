"""Stale-lease routing on both platforms, plus the real writer's platform fence."""

import contextlib
import hashlib
import json
import os
import shutil
import subprocess
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SWEEP = ROOT / ".agent-bridge/bin/Invoke-StaleClaimSweep.ps1"

FIXTURE_WRITER = r"""
[CmdletBinding()]
param(
    [string]$Agent, [string]$Type, [string]$Status, [string]$Severity,
    [string]$TaskId, [string]$To, [string]$Message, [string]$PayloadJson
)
$ErrorActionPreference = 'Stop'
if ($Agent -cne 'system' -or $Type -cne 'release' -or $Status -cne 'stale_lease') {
    throw 'unexpected stale-routing fixture event'
}
$record = [ordered]@{
    agent=$Agent; type=$Type; status=$Status; severity=$Severity;
    task_id=$TaskId; to=$To; message=$Message;
    payload=($PayloadJson | ConvertFrom-Json -ErrorAction Stop)
}
$path = Join-Path (Join-Path $env:AGENT_BRIDGE_RUNTIME_ROOT 'shared') 'events.jsonl'
$encoding = New-Object System.Text.UTF8Encoding($false)
[IO.File]::AppendAllText($path, (($record | ConvertTo-Json -Depth 16 -Compress) + "`n"), $encoding)
"""


def _fixture_sweep(tmp_path: Path, *, real_writer: bool) -> Path:
    """Run unmodified sweep/reader code with an isolated event transport."""
    fixture = tmp_path / "fixture"
    code = fixture / ".agent-bridge/bin"
    shutil.copytree(ROOT / ".agent-bridge/bin", code)
    configs = fixture / "configs"
    configs.mkdir()
    shutil.copy2(ROOT / "configs/bridge_identity_registry.json", configs)
    if real_writer:
        # The fixture must not share production's named kernel mutexes.
        prefix = "Local\\WdStaleFixture-" + uuid.uuid4().hex + "-"
        for script in code.glob("*.ps1"):
            source = script.read_text(encoding="utf-8-sig")
            if "Global\\WaggleDanceBridge" in source:
                script.write_text(
                    source.replace("Global\\WaggleDanceBridge", prefix),
                    encoding="utf-8-sig",
                )
    else:
        (code / "Write-AgentEvent.ps1").write_text(FIXTURE_WRITER, encoding="utf-8")
    return code / SWEEP.name


@pytest.mark.parametrize("shell", ["powershell", "pwsh"])
@pytest.mark.parametrize(
    ("request_sender", "request_to", "responder_session", "expected_to",
     "identity_complete", "duplicate_request"),
    [
        ("codex-lead-1", "codex-tools-1", "tools-session-regression",
         "codex-tools-1,codex-lead-1", True, None),
        ("codex-lead-1", "fable-5", "tools-session-regression",
         "codex-tools-1", True, None),
        ("codex-lead-1", "codex-tools-1", "different-session",
         "codex-tools-1", True, None),
        ("codex-lead-1", "codex-tools-1", "tools-session-regression",
         "codex-tools-1", False, None),
        ("codex-lead-1", "codex-tools-1", "tools-session-regression",
         "codex-tools-1", True, "different"),
        ("codex-lead-1", "codex-tools-1", "tools-session-regression",
         "codex-tools-1,codex-lead-1", True, "identical"),
        (None, None, None, "codex-tools-1", False, None),
    ],
)
def test_stale_release_addresses_only_verified_owner_and_dispatcher(
    tmp_path: Path, shell: str, request_sender: str | None,
    request_to: str | None, responder_session: str | None, expected_to: str,
    identity_complete: bool, duplicate_request: str | None,
) -> None:
    executable = shutil.which(shell)
    if executable is None:
        pytest.skip(f"{shell} is not installed")

    runtime = tmp_path / "runtime"
    claims = runtime / "work_queue/claims"
    shared = runtime / "shared"
    claims.mkdir(parents=True)
    shared.mkdir(parents=True)
    past = datetime.now(timezone.utc) - timedelta(minutes=20)
    task = "codex-lead-1/stale-routing-regression"
    claim = {
        "task_id": task,
        "agent": "codex-tools-1",
        "agent_uuid": "7a8af68d-20bc-4598-9953-23c5dd98b102",
        "owner_session_id": "tools-session-regression",
        "run_id": "tools-session-regression",
        "claimed_at_utc": past.isoformat(),
        "last_heartbeat_utc": past.isoformat(),
        "lease_seconds": 60,
        "claim_lease_expires_utc": (past + timedelta(minutes=1)).isoformat(),
        "owner_identity": "none",
        "write_scope": ["tests/tools/test_bridge_stale_routing.py"],
    }
    (claims / "stale-routing-regression.json").write_text(
        json.dumps(claim), encoding="utf-8"
    )
    if request_sender is not None:
        event = {
            "ts_utc": (past - timedelta(minutes=1)).isoformat(),
            "agent": request_sender,
            "type": "wake_request",
            "task_id": task,
            "status": "assigned",
            "to": request_to,
            "expected_responders": {
                "codex-tools-1": {
                    "agent_uuid": claim["agent_uuid"],
                    "session_id": responder_session,
                }
            },
        }
        if identity_complete:
            event.update(
                agent_uuid="d3c9d1d1-96a9-4eb8-a8e2-6f05f9d1a101",
                session_id="lead-session-regression",
                request_id="stale-routing-regression",
            )
        rows = [event]
        if duplicate_request:
            rows.append(dict(
                event,
                request_id=("different-request" if duplicate_request == "different"
                            else event["request_id"]),
            ))
        (shared / "events.jsonl").write_text(
            "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
        )

    env = os.environ.copy()
    env["AGENT_BRIDGE_RUNTIME_ROOT"] = str(runtime)
    for key in ("AGENT_BRIDGE_AGENT", "AGENT_BRIDGE_AGENT_UUID",
                "AGENT_BRIDGE_SESSION_ID", "AGENT_BRIDGE_ROLE"):
        env.pop(key, None)
    sweep = _fixture_sweep(tmp_path, real_writer=False)
    completed = subprocess.run(
        [executable, "-NoProfile", "-NonInteractive", "-File", str(sweep),
         "-StaleSeconds", "1", "-Quiet"],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=30,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert list((runtime / "work_queue/done").glob("*.stale_lease.json"))
    events = [json.loads(line) for line in (shared / "events.jsonl").read_text(
        encoding="utf-8-sig"
    ).splitlines()]
    release = next(e for e in events if e.get("type") == "release")
    assert release["status"] == "stale_lease"
    assert release["to"] == expected_to
    assert release["payload"]["claim_agent"] == claim["agent"]
    assert release["payload"]["claim_agent_uuid"] == claim["agent_uuid"]
    assert release["payload"]["claim_owner_session_id"] == claim["owner_session_id"]


@pytest.mark.parametrize("shell", ["powershell", "pwsh"])
def test_real_writer_respects_platform_fence_after_stale_archive(
    tmp_path: Path, shell: str,
) -> None:
    executable = shutil.which(shell)
    if executable is None:
        pytest.skip(f"{shell} is not installed")

    runtime = tmp_path / "runtime"
    claims = runtime / "work_queue/claims"
    shared = runtime / "shared"
    claims.mkdir(parents=True)
    shared.mkdir(parents=True)
    past = datetime.now(timezone.utc) - timedelta(minutes=20)
    task = "codex-lead-1/stale-routing-platform"
    (claims / "stale-routing-platform.json").write_text(json.dumps({
        "task_id": task,
        "agent": "codex-tools-1",
        "agent_uuid": "7a8af68d-20bc-4598-9953-23c5dd98b102",
        "owner_session_id": "tools-session-regression",
        "run_id": "tools-session-regression",
        "claimed_at_utc": past.isoformat(),
        "last_heartbeat_utc": past.isoformat(),
        "lease_seconds": 60,
        "claim_lease_expires_utc": (past + timedelta(minutes=1)).isoformat(),
        "owner_identity": "none",
        "write_scope": ["tests/tools/test_bridge_stale_routing.py"],
    }), encoding="utf-8")
    env = os.environ.copy()
    env["AGENT_BRIDGE_RUNTIME_ROOT"] = str(runtime)
    for key in ("AGENT_BRIDGE_AGENT", "AGENT_BRIDGE_AGENT_UUID",
                "AGENT_BRIDGE_SESSION_ID", "AGENT_BRIDGE_ROLE"):
        env.pop(key, None)
    sweep = _fixture_sweep(tmp_path, real_writer=True)
    completed = subprocess.run(
        [executable, "-NoProfile", "-NonInteractive", "-File", str(sweep),
         "-StaleSeconds", "1", "-Quiet"],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=30,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert list((runtime / "work_queue/done").glob("*.stale_lease.json"))
    path = shared / "events.jsonl"
    rows = [json.loads(line) for line in path.read_text(
        encoding="utf-8-sig"
    ).splitlines()] if path.exists() else []
    if os.name == "nt":
        release = next(row for row in rows if row.get("type") == "release")
        assert release["status"] == "stale_lease"
        assert release["to"] == "codex-tools-1"
    else:
        assert not rows
        # Write-Warning is stdout on PowerShell 5.1 and can differ on pwsh.
        assert "Windows file identity" in completed.stdout + completed.stderr


# F8 session-heartbeat fence (RCO1 2026-09-30, Lead request d79f933d): Write-BridgeSessionHeartbeat takes
# only the beat's own sibling lock (<beat>.json.lock), never the claim lock. The sweep now holds that lock
# from the liveness read through the archive; at 7779e9a2 it archived while a beat was being written.
@pytest.mark.skipif(os.name != "nt", reason="the sweep's FileShare.None lock is proven on Windows")
@pytest.mark.parametrize("shell", ["powershell", "pwsh"])
def test_stale_sweep_leaves_an_owned_claim_while_its_owner_beat_lock_is_held(
    tmp_path: Path, shell: str,
) -> None:
    from tools.bridge_v2_queue_transactions import FileClaimLock   # the Python twin of Enter-BridgeClaimLock

    executable = shutil.which(shell)
    if executable is None:
        pytest.skip(f"{shell} is not installed")
    runtime = tmp_path / "runtime"
    claims = runtime / "work_queue/claims"
    claims.mkdir(parents=True)
    (runtime / "shared").mkdir(parents=True)
    past = datetime.now(timezone.utc) - timedelta(minutes=20)
    session, token_sha = "tools-session-fence", hashlib.sha256(b"fence-token").hexdigest()
    claim = claims / "stale-fence.json"
    claim.write_text(json.dumps({
        "task_id": "codex-lead-1/stale-fence", "agent": "codex-tools-1",
        "agent_uuid": "7a8af68d-20bc-4598-9953-23c5dd98b102", "owner_session_id": session,
        "owner_token_sha256": token_sha, "run_id": session, "claimed_at_utc": past.isoformat(),
        "last_heartbeat_utc": past.isoformat(), "lease_seconds": 60,
        "claim_lease_expires_utc": (past + timedelta(minutes=1)).isoformat(),
        "write_scope": ["tests/tools/test_bridge_stale_routing.py"],
    }), encoding="utf-8")
    digest = hashlib.sha256(f"{session}\n{token_sha}".encode("utf-8")).hexdigest()
    beat = runtime / "work_queue/heartbeats" / f"{digest}.json"
    beat.parent.mkdir(parents=True)
    env = {key: value for key, value in os.environ.items() if not key.startswith(("AGENT_BRIDGE_", "WD_"))}
    env["AGENT_BRIDGE_RUNTIME_ROOT"] = str(runtime)
    sweep = _fixture_sweep(tmp_path, real_writer=False)

    def run() -> subprocess.CompletedProcess:
        return subprocess.run(
            [executable, "-NoProfile", "-NonInteractive", "-File", str(sweep), "-StaleSeconds", "1", "-Quiet"],
            cwd=ROOT, env=env, capture_output=True, text=True, timeout=120, check=False,
        )

    with FileClaimLock().hold(Path(str(beat) + ".lock"), 1):        # the owner's writer is mid-beat
        busy = run()
    assert busy.returncode == 0, busy.stdout + busy.stderr
    done = runtime / "work_queue/done"
    assert not list(done.glob("*.stale_lease.json")) and claim.exists()   # nothing decided this round
    swept = run()                                                   # success twin: released, beat still dead
    assert swept.returncode == 0, swept.stdout + swept.stderr
    assert len(list(done.glob("*.stale_lease.json"))) == 1 and not claim.exists()
    assert not Path(str(beat) + ".lock").exists()                   # the sweep removed its own lock file
    events = [json.loads(line) for line in (runtime / "shared/events.jsonl").read_text(
        encoding="utf-8-sig"
    ).splitlines()]
    assert [(event["type"], event["status"]) for event in events] == [("release", "stale_lease")]


@contextlib.contextmanager
def _held_without_sharing(path: Path):
    """Keep ``path`` open with share mode 0, as an antivirus or backup reader can: every other open fails."""
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                                     wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel32.CreateFileW(str(path), 0x80000000, 0, None, 3, 0x80, None)   # GENERIC_READ, OPEN_EXISTING
    assert handle not in (None, wintypes.HANDLE(-1).value), ctypes.get_last_error()
    try:
        yield
    finally:
        kernel32.CloseHandle(handle)


# A-F1 (Fable review 99897de5, Lead d06fbf85): an owner beat that EXISTS but cannot be read or evaluated is
# unknown, never "not live", so the sweep keeps the expired claim this round, as the core sweeper
# (waggledance/core/work_queue.py _session_heartbeat_state) does. Proof of abandonment: no beat, an expired or a
# future-dated beat. At 7bf841ff the unknown rows archived. A beat at the claim's own path that names another
# session or token is unknown too (ClaimLeaseHeartbeat.ps1 Get-BridgeSessionHeartbeatLiveness, 83da16a7), so the
# "foreign" row keeps the claim; it used to expect an archive (Lead 05:39:30Z test alignment, behaviour unchanged).
@pytest.mark.skipif(os.name != "nt", reason="the share-locked beat and the sweep's file locks are Windows-only")
@pytest.mark.parametrize("shell", ["powershell", "pwsh"])
@pytest.mark.parametrize(("beat_kind", "archived"), [
    ("absent", True), ("expired", True), ("future", True), ("foreign", False), ("fresh", False),
    ("fresh_share_locked", False), ("torn", False), ("empty", False), ("not_object", False),
    ("bad_time", False), ("directory", False),
    # S1 (Fable review 0286732f): pwsh 7 read an explicit-offset timestamp as local wall time taken for UTC, so
    # on a host east of UTC a FRESH beat stamped +03:00 or +00:00 looked future-dated (not live) and was reaped.
    ("fresh_plus3", False), ("fresh_utc_offset", False), ("expired_plus3", True),
    ("fresh_minus5", False), ("fresh_no_zone", False), ("future_plus3", True), ("numeric_time", False),
])
def test_stale_sweep_keeps_a_claim_whose_existing_owner_beat_cannot_be_read_or_evaluated(
    tmp_path: Path, shell: str, beat_kind: str, archived: bool,
) -> None:
    executable = shutil.which(shell)
    if executable is None:
        pytest.skip(f"{shell} is not installed")
    runtime = tmp_path / "runtime"
    claims = runtime / "work_queue/claims"
    claims.mkdir(parents=True)
    (runtime / "shared").mkdir(parents=True)
    now = datetime.now(timezone.utc)
    past = now - timedelta(minutes=20)
    session, token_sha = "tools-session-unknown", hashlib.sha256(b"unknown-token").hexdigest()
    claim = claims / "stale-unknown.json"
    claim.write_text(json.dumps({
        "task_id": "codex-lead-1/stale-unknown", "agent": "codex-tools-1",
        "agent_uuid": "7a8af68d-20bc-4598-9953-23c5dd98b102", "owner_session_id": session,
        "owner_token_sha256": token_sha, "run_id": session, "claimed_at_utc": past.isoformat(),
        "last_heartbeat_utc": past.isoformat(), "lease_seconds": 60,
        "claim_lease_expires_utc": (past + timedelta(minutes=1)).isoformat(),
        "write_scope": ["tests/tools/test_bridge_stale_routing.py"],
    }), encoding="utf-8")
    digest = hashlib.sha256(f"{session}\n{token_sha}".encode("utf-8")).hexdigest()
    beat = runtime / "work_queue/heartbeats" / f"{digest}.json"
    beat.parent.mkdir(parents=True)

    def body(owner_token: str, last_beat: object) -> str:
        return json.dumps({"owner_session_id": session, "owner_token_sha256": owner_token,
                           "last_beat_utc": last_beat, "ttl_seconds": 180})

    def offset(moment: datetime, hours: int) -> str:                 # the same instant, another zone
        return moment.astimezone(timezone(timedelta(hours=hours))).isoformat()

    def stamp(moment: datetime) -> str:                              # the writer's own UTC 'o' form
        return moment.strftime("%Y-%m-%dT%H:%M:%S.%fZ")

    contents = {
        "expired": body(token_sha, stamp(now - timedelta(minutes=30))),
        "future": body(token_sha, stamp(now + timedelta(minutes=10))),
        "foreign": body(hashlib.sha256(b"someone-else").hexdigest(), stamp(now)),
        "fresh": body(token_sha, stamp(now)), "fresh_share_locked": body(token_sha, stamp(now)),
        "torn": '{"owner_session_id": "', "empty": "", "not_object": "[]",
        "bad_time": body(token_sha, "not a time"),
        "fresh_plus3": body(token_sha, offset(now, 3)), "fresh_utc_offset": body(token_sha, now.isoformat()),
        "expired_plus3": body(token_sha, offset(now - timedelta(minutes=30), 3)),
        "fresh_minus5": body(token_sha, offset(now, -5)),
        "fresh_no_zone": body(token_sha, now.replace(tzinfo=None).isoformat()),   # read as UTC, as before
        "future_plus3": body(token_sha, offset(now + timedelta(minutes=10), 3)),
        "numeric_time": body(token_sha, 1790000000),
    }
    if beat_kind == "directory":
        beat.mkdir()
    elif beat_kind in contents:
        beat.write_bytes(contents[beat_kind].encode("utf-8"))
    env = {key: value for key, value in os.environ.items() if not key.startswith(("AGENT_BRIDGE_", "WD_"))}
    env["AGENT_BRIDGE_RUNTIME_ROOT"] = str(runtime)
    sweep = _fixture_sweep(tmp_path, real_writer=False)
    command = [executable, "-NoProfile", "-NonInteractive", "-File", str(sweep), "-StaleSeconds", "1", "-Quiet"]
    claim_before = claim.read_bytes()
    beat_before = beat.read_bytes() if beat.is_file() else None
    held = _held_without_sharing(beat) if beat_kind == "fresh_share_locked" else contextlib.nullcontext()
    with held:
        done = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True, timeout=120, check=False)
    assert done.returncode == 0, done.stdout + done.stderr
    swept = list((runtime / "work_queue/done").glob("*.stale_lease.json"))
    if archived:
        assert len(swept) == 1 and not claim.exists(), beat_kind
    else:
        assert not swept and claim.exists(), (beat_kind, done.stdout + done.stderr)
        assert claim.read_bytes() == claim_before, beat_kind          # kept as it was, not rewritten
        if beat_before is not None:
            assert beat.read_bytes() == beat_before, beat_kind        # the owner's beat (foreign or not) is untouched

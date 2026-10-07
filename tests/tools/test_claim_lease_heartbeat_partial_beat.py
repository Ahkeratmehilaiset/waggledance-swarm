# SPDX-License-Identifier: BUSL-1.1
"""A damaged or foreign beat at a claim's own heartbeat path is 'unknown', never 'not_live'.

Grok finding (production-landing reconcile 2026-10-06, reproduced at 7cf48159): Get-BridgeSessionHeartbeatLiveness
returned 'not_live' for a readable beat that lacked owner_session_id / owner_token_sha256 or named another session or
token, and Invoke-StaleClaimSweep archives a lease-expired claim on 'not_live'. The Python sweepers
(waggledance/core/work_queue.py, tools/bridge_v2_work_queue.py) answer 'unknown' for the same beats and keep the claim.
The success twins pin that a missing file, a stale beat and a future-dated beat are still proof of absence.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / ".agent-bridge" / "bin" / "ClaimLeaseHeartbeat.ps1"
SHELLS = [shell for shell in ("powershell.exe", "pwsh.exe") if os.name == "nt" and shutil.which(shell)]
pytestmark = pytest.mark.skipif(not SHELLS, reason="ClaimLeaseHeartbeat.ps1 is Windows PowerShell")
SESSION = "partial-beat-session"
TOKEN = "e" * 64
NOW = datetime.now(timezone.utc)


def _beat(**overrides: object) -> dict:
    beat = {"owner_session_id": SESSION, "owner_token_sha256": TOKEN,
            "last_beat_utc": NOW.isoformat().replace("+00:00", "Z"), "ttl_seconds": 600}
    for key, value in overrides.items():
        if value is None:
            beat.pop(key)
        else:
            beat[key] = value
    return beat


def _liveness(shell: str, root: Path, beat: dict | None) -> str:
    command = (
        f". '{SCRIPT}'; "
        f"$claim = [pscustomobject]@{{owner_session_id='{SESSION}'; owner_token_sha256='{TOKEN}'}}; "
        f"$path = Get-BridgeSessionHeartbeatPath -Root '{root}' -SessionId '{SESSION}' -TokenSha256 '{TOKEN}'; "
        "if ($env:WD_FIXTURE_BEAT) { "
        "$null = New-Item -ItemType Directory -Force -Path (Split-Path $path); "
        "[IO.File]::WriteAllText($path, $env:WD_FIXTURE_BEAT, (New-Object Text.UTF8Encoding($false))) }; "
        # The clock is the NOW the beats were built from, so a slow run can never age the fresh beat.
        f"'LIVENESS:' + (Get-BridgeSessionHeartbeatLiveness -Root '{root}' -Claim $claim "
        f"-NowUtc ([DateTimeOffset]::Parse('{NOW.isoformat()}', [Globalization.CultureInfo]::InvariantCulture)).UtcDateTime)"
    )
    env = {key: value for key, value in os.environ.items() if not key.startswith(("AGENT_BRIDGE_", "WD_"))}
    env["AGENT_BRIDGE_RUNTIME_ROOT"] = str(root)
    if beat is not None:
        env["WD_FIXTURE_BEAT"] = json.dumps(beat)
    process = subprocess.run([shutil.which(shell), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                              "-Command", command], env=env, capture_output=True, text=True, timeout=120)
    lines = [line for line in process.stdout.splitlines() if line.startswith("LIVENESS:")]
    assert len(lines) == 1, process.stdout + process.stderr
    return lines[0].removeprefix("LIVENESS:")


UNKNOWN = {
    "missing_owner_session_id": _beat(owner_session_id=None),
    "missing_owner_token_sha256": _beat(owner_token_sha256=None),
    "missing_both_owner_fields": _beat(owner_session_id=None, owner_token_sha256=None),
    "other_session": _beat(owner_session_id="someone-else"),
    "other_token": _beat(owner_token_sha256="d" * 64),
    "session_case_variant": _beat(owner_session_id=SESSION.upper()),
    "missing_last_beat_utc": _beat(last_beat_utc=None),
}
SUCCESS_TWINS = {
    "fresh_own_beat": (_beat(), "live"),
    "stale_own_beat": (_beat(last_beat_utc=(NOW - timedelta(hours=2)).isoformat().replace("+00:00", "Z")),
                       "not_live"),
    "future_own_beat": (_beat(last_beat_utc=(NOW + timedelta(hours=2)).isoformat().replace("+00:00", "Z")),
                        "not_live"),
    "no_artifact": (None, "not_live"),
}


@pytest.mark.parametrize("shell", SHELLS, ids=lambda s: s.split(".")[0])
@pytest.mark.parametrize("case", sorted(UNKNOWN))
def test_partial_or_foreign_beat_at_own_path_is_unknown(tmp_path: Path, shell: str, case: str) -> None:
    assert _liveness(shell, tmp_path, UNKNOWN[case]) == "unknown"


@pytest.mark.parametrize("shell", SHELLS, ids=lambda s: s.split(".")[0])
@pytest.mark.parametrize("case", sorted(SUCCESS_TWINS))
def test_success_twins_keep_live_and_not_live_proof(tmp_path: Path, shell: str, case: str) -> None:
    beat, expected = SUCCESS_TWINS[case]
    assert _liveness(shell, tmp_path, beat) == expected

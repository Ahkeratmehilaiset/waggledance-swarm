"""Tools recovers the EXACT incoming request beyond the recent 40-row view.

The routing summary comes from the actual pinned next-action function. The
harness below performs the steps WAKE_PROCEDURE_TOOLS.md prescribes with the
actual pinned helpers, and the test pins every command it uses to the procedure
text, so the documented path is the path exercised here.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from tools.bridge_next_action import recommend_next_action


ROOT = Path(__file__).resolve().parents[2]
BIN = ROOT / ".agent-bridge/bin"
PROCEDURE = ROOT / "ops/windows/reboot/WAKE_PROCEDURE_TOOLS.md"
SHELLS = list(dict.fromkeys(filter(None, (shutil.which("pwsh"), shutil.which("powershell.exe")))))
pytestmark = pytest.mark.skipif(not SHELLS, reason="PowerShell is required")
# The legacy recent-view path parses Read-AgentBridge -Raw stdout as JSON. Off Windows that read is never clean:
# its accepted-v1 drain refuses non-Windows hosts (Windows write-through publication) and Write-Warning lands on
# the child's stdout, as does the -ForegroundColor header's ANSI colour. Windows hosts run these cases.
WINDOWS_RECENT_VIEW = pytest.mark.skipif(
    os.name != "nt", reason="the recent view's accepted-v1 drain needs Windows; off Windows it warns on stdout")
NOW = datetime(2026, 9, 29, 10, 0, tzinfo=timezone.utc)
TS = "2026-09-29T09:50:00.1234567Z"
_SCRUB = ("AGENT_BRIDGE_", "CLAUDE_CODE_", "WD_", "GIT_")

# The procedure steps, executed with the pinned helpers. Output: one JSON object.
HARNESS = r"""
param([string]$Bin, [string]$RoutingPath)
$ErrorActionPreference = 'Stop'
function From-Json([string]$Text) {
    $arguments = @{}
    if ((Get-Command ConvertFrom-Json).Parameters.ContainsKey('DateKind')) { $arguments.DateKind = 'String' }
    return ($Text | ConvertFrom-Json @arguments)
}
function Blocked([string]$Reason) { [pscustomobject]@{status='blocked'; reason=$Reason} | ConvertTo-Json -Compress; exit 0 }
function Invoke-HelperText([string]$Name, [string[]]$Arguments) {
    # A failing helper writes to stderr; in PS 5.1 that must not become terminating here.
    # Any non-zero exit discards stdout: partial output is unknown, never evidence.
    $ErrorActionPreference = 'Continue'
    $shell = (Get-Process -Id $PID).Path
    $out = & $shell -NoProfile -NonInteractive -File (Join-Path $Bin $Name) @Arguments 2>&1 |
        Where-Object { $_ -isnot [Management.Automation.ErrorRecord] } | Out-String
    if ($LASTEXITCODE -ne 0) { return $null }
    return $out
}
function Get-Field($Event, [string]$Name) {
    $property = $Event.PSObject.Properties[$Name]
    if ($null -ne $property) { return $property.Value }
    return $null
}
# One acceptance check for every path (snapshot, inventory-resolved, ID-less recent view).
function Assert-Accepted($Request, [string]$ExpectedId) {
    $payload = Get-Field $Request 'payload'
    $ids = @((Get-Field $Request 'request_id'), $(if ($null -ne $payload) { Get-Field $payload 'request_id' } else { $null }) |
        Where-Object { $null -ne $_ -and [string]$_ -ne '' })
    if ($ExpectedId) {
        if (-not $ids.Count -or @($ids | Where-Object { [string]$_ -cne $ExpectedId }).Count) { Blocked 'routing_mismatch' }
    } elseif ($ids.Count) { Blocked 'recent_row_has_request_id' }
    if ([string](Get-Field $Request 'agent') -cne [string]$incoming.agent -or
        [string](Get-Field $Request 'task_id') -cne [string]$routing.task_id -or
        [string](Get-Field $Request 'ts_utc') -cne [string]$incoming.ts_utc) { Blocked 'routing_mismatch' }
    $targets = @(([string](Get-Field $Request 'to') -split ',') | ForEach-Object { $_.Trim() } | Where-Object { $_ })
    if ($targets -cnotcontains 'codex-tools-1') { Blocked 'wrong_target' }
}
function Accepted($Request, [string]$Path, $State) {
    # The exact JSON a reply/turn helper would receive.
    [pscustomobject]@{status='ok'; path=$Path; state=$State;
        request_json=($Request | ConvertTo-Json -Depth 32 -Compress)} | ConvertTo-Json -Depth 4 -Compress
    exit 0
}
$routing = From-Json ([IO.File]::ReadAllText($RoutingPath))
$incoming = $routing.incoming
if ($routing.action -cne 'answer_incoming' -or $null -eq $incoming) { Blocked 'no_incoming' }
if ($incoming.request_binding_conflict -eq $true) { Blocked 'request_binding_conflict' }
$requestId = [string]$incoming.request_id
if (-not $requestId) {
    $text = Invoke-HelperText 'Get-BridgeRequestInventory.ps1' @('-Agent', [string]$incoming.agent,
        '-TaskId', [string]$routing.task_id, '-TsUtc', [string]$incoming.ts_utc)
    if ($null -eq $text) { Blocked 'inventory_failed' }
    $inventory = From-Json $text
    if ($inventory.schema -cne 'wd.request-inventory.v2' -or
        $inventory.truncated -isnot [bool] -or $inventory.truncated -ne $false) { Blocked 'inventory_incomplete' }
    $inventoryMatches = @($inventory.requests | Where-Object {
        [string]$_.task_id -ceq [string]$routing.task_id -and [string]$_.ts_utc -ceq [string]$incoming.ts_utc })
    if (($inventory.matched_count -isnot [int] -and $inventory.matched_count -isnot [long]) -or
        $inventory.matched_count -ne $inventoryMatches.Count) { Blocked 'inventory_incomplete' }
    if ($inventoryMatches.Count -gt 1) { Blocked 'ambiguous_inventory_match' }
    if ($inventoryMatches.Count -eq 0) {
        # Zero inventory matches do not prove the ID absent: the row itself must lack one.
        $text = Invoke-HelperText 'Read-AgentBridge.ps1' @('-Agent', 'codex-tools-1', '-Raw', '-NoAckReceived', '-NoContinuity')
        if ($null -eq $text) { Blocked 'recent_read_failed' }
        $recent = ($text -split "`r?`n" | Where-Object { $_ -notmatch '^RECENT EVENTS' }) -join "`n"
        $rows = @(From-Json $recent | Where-Object { [string]$_.agent -ceq [string]$incoming.agent -and
            [string]$_.task_id -ceq [string]$routing.task_id -and [string]$_.ts_utc -ceq [string]$incoming.ts_utc })
        if ($rows.Count -ne 1) { Blocked 'legacy_request_not_in_recent_view' }
        Assert-Accepted $rows[0] ''
        Accepted $rows[0] 'recent_view' $null
    }
    $requestId = [string]$inventoryMatches[0].request_id
}
$text = Invoke-HelperText 'Get-BridgeReplySnapshot.ps1' @('-RequestId', $requestId, '-Requester', [string]$incoming.agent)
if ($null -eq $text) { Blocked 'snapshot_failed' }
$snapshot = From-Json $text
Assert-Accepted $snapshot.request $requestId
Accepted $snapshot.request 'snapshot' (@($snapshot.results | Where-Object { $_.target -ceq 'codex-tools-1' })[0].state)
"""


def _env(root: Path) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if not key.startswith(_SCRUB)}
    env["AGENT_BRIDGE_RUNTIME_ROOT"] = str(root)
    return env


def _request(request_id: str | None, **fields: object) -> dict:
    row = dict(ts_utc=TS, agent="codex-lead-1", agent_uuid="lead-uuid", session_id="lead-session",
               run_id="lead-run", to="codex-tools-1", type="wake_request", status="assigned",
               task_id="codex-lead-1/exact-retrieval", message="Implement the slice. " + "detail " * 80,
               payload={"result_contract": {"schema": "wd.task-result-contract.v1", "required": ["verdict"]}})
    if request_id is not None:
        row.update(request_id=request_id, request_digest=f"digest-{request_id}")
    row.update(fields)
    return row


def _noise(count: int) -> list[dict]:
    rows = []
    for index in range(count):
        row = dict(ts_utc="2026-09-29T09:55:00Z", agent="fable-5", to="operator", type="message",
                   status="reported", task_id=f"noise/{index}", message="noise")
        if index % 2:
            row.update(to="claude-rco-1", type="wake_request", status="request", request_id=f"noise-{index}")
        rows.append(row)
    return rows


def _route(rows: list[dict]) -> dict:
    report = recommend_next_action(agent="codex-tools-1", events=rows, claims=[], now_utc=NOW,
                                   production_idle_warn_minutes=None)
    return json.loads(json.dumps(report))


def _retrieve(tmp_path: Path, shell: str, rows: list[dict], routing: dict, bin_dir: Path = BIN) -> dict:
    shared = tmp_path / "shared"
    shared.mkdir(parents=True, exist_ok=True)
    (shared / "events.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    routing_path = tmp_path / "routing.json"
    routing_path.write_text(json.dumps(routing), encoding="utf-8")
    harness = tmp_path / "procedure_harness.ps1"
    harness.write_text(HARNESS, encoding="utf-8")
    process = subprocess.run([shell, "-NoProfile", "-NonInteractive", "-File", str(harness),
                              "-Bin", str(bin_dir), "-RoutingPath", str(routing_path)],
                             env=_env(tmp_path), capture_output=True, text=True, encoding="utf-8", timeout=300)
    assert process.returncode == 0, process.stderr
    return json.loads(process.stdout)


def test_procedure_prescribes_the_exact_retrieval_the_harness_runs() -> None:
    text = " ".join(PROCEDURE.read_text(encoding="utf-8").split())
    for fragment in (
        "Exact incoming request retrieval takes precedence over the recent 40-row view below.",
        "Get-BridgeReplySnapshot.ps1 -RequestId <incoming.request_id> -Requester <incoming.agent>",
        "Get-BridgeRequestInventory.ps1 -Agent <incoming.agent> -TaskId <task_id> -TsUtc <incoming.ts_utc>",
        "compact task_id and ts_utc exactly equal the routing task_id and incoming.ts_utc",
        "schema wd.request-inventory.v2 and boolean truncated=false",
        "its to field lists codex-tools-1",
        "resolve that uniquely resolved request_id with the same snapshot command",
        "accept its single matching row only if that row itself carries no request_id, neither top-level nor in payload",
        "zero inventory matches do not prove the ID is absent",
        "a failed or non-zero-exit recent read is unknown even if it printed partial output",
        "Apply one identical acceptance check on every path",
        "never pick the latest or closest request and never invent fields",
        "ConvertFrom-Json -DateKind String",
        "does not enumerate HOLD/cancel/finding controls",
        "It grants no new authority.",
    ):
        assert fragment in text
    # Exact retrieval precedes, and does not replace, the existing recent-view text.
    assert text.index("Exact incoming request retrieval") < text.index("fetch the exact selected request with Read-AgentBridge.ps1")


@pytest.mark.parametrize("shell", SHELLS, ids=lambda value: Path(value).stem)
@pytest.mark.parametrize("timestamp", (TS, "2026-09-29T09:50:00.1234560Z"))
def test_bound_request_hidden_by_60_noise_is_recovered_exactly(tmp_path: Path, shell: str, timestamp: str) -> None:
    request = _request("exact-hidden-v1", ts_utc=timestamp)
    rows = [request, *_noise(60)]
    routing = _route(rows)
    assert routing["action"] == "answer_incoming"
    assert routing["task_id"] == request["task_id"]
    assert routing["incoming"]["request_id"] == "exact-hidden-v1"
    assert routing["incoming"]["ts_utc"] == timestamp
    assert routing["incoming"]["message"] != request["message"]  # truncated routing summary

    result = _retrieve(tmp_path, shell, rows, routing)
    assert result["status"] == "ok" and result["path"] == "snapshot"
    assert result["state"] == "pending_at_snapshot"
    assert json.loads(result["request_json"]) == request  # full exact object, dates unchanged

    recent = subprocess.run([shell, "-NoProfile", "-NonInteractive", "-File", str(BIN / "Read-AgentBridge.ps1"),
                             "-Agent", "codex-tools-1", "-Raw", "-NoAckReceived", "-NoContinuity"],
                            env=_env(tmp_path), capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert recent.returncode == 0, recent.stderr
    assert "exact-hidden-v1" not in recent.stdout  # the old 40-row path cannot see it


@pytest.mark.parametrize("shell", SHELLS, ids=lambda value: Path(value).stem)
def test_identical_duplicate_resolves_and_conflict_blocks(tmp_path: Path, shell: str) -> None:
    request = _request("dup-v1")
    rows = [request, *_noise(60), request]
    assert _retrieve(tmp_path, shell, rows, _route(rows))["status"] == "ok"

    changed = deepcopy(request)
    changed["message"] = "same immutable id, different content"
    conflict_rows = [request, *_noise(60), changed]
    routing = _route([request, *_noise(60)])
    result = _retrieve(tmp_path / "conflict", shell, conflict_rows, routing)
    assert result == {"status": "blocked", "reason": "snapshot_failed"}


@pytest.mark.parametrize("shell", SHELLS, ids=lambda value: Path(value).stem)
@pytest.mark.parametrize("case", ("wrong_target", "stale_routing_ts", "wrong_sender", "binding_conflict_flag"))
def test_mismatched_routing_never_selects_another_request(tmp_path: Path, shell: str, case: str) -> None:
    mine = _request("mine-v1")
    other = _request("other-target-v1", to="fable-5")
    rows = [mine, other, *_noise(60)]
    routing = _route(rows)
    assert routing["incoming"]["request_id"] == "mine-v1"
    expected = {"wrong_target": "wrong_target", "stale_routing_ts": "routing_mismatch",
                "wrong_sender": "snapshot_failed", "binding_conflict_flag": "request_binding_conflict"}[case]
    if case == "wrong_target":
        routing["incoming"]["request_id"] = "other-target-v1"
    elif case == "stale_routing_ts":
        routing["incoming"]["ts_utc"] = "2026-09-29T09:49:59Z"
    elif case == "wrong_sender":
        routing["incoming"]["agent"] = "claude-rco-1"
    else:
        routing["incoming"]["request_binding_conflict"] = True
    assert _retrieve(tmp_path, shell, rows, routing) == {"status": "blocked", "reason": expected}


@pytest.mark.parametrize("shell", SHELLS, ids=lambda value: Path(value).stem)
@pytest.mark.parametrize("timestamp", (TS, "2026-09-29T09:50:00.1234560Z"))
def test_routing_without_request_id_uses_exact_inventory_match_or_blocks(tmp_path: Path, shell: str, timestamp: str) -> None:
    request = _request("inventory-only-v1", ts_utc=timestamp)
    rows = [request, *_noise(60)]
    routing = _route(rows)
    routing["incoming"]["request_id"] = None
    result = _retrieve(tmp_path / "one", shell, rows, routing)
    assert result["status"] == "ok" and json.loads(result["request_json"]) == request

    twin = _request("inventory-twin-v1", ts_utc=timestamp)  # same sender, task_id and ts_utc: ambiguous
    result = _retrieve(tmp_path / "two", shell, [request, twin, *_noise(60)], routing)
    assert result == {"status": "blocked", "reason": "ambiguous_inventory_match"}


@WINDOWS_RECENT_VIEW
@pytest.mark.parametrize("shell", SHELLS, ids=lambda value: Path(value).stem)
def test_legacy_request_without_id_hidden_by_noise_is_blocked_not_invented(tmp_path: Path, shell: str) -> None:
    legacy = _request(None, status="request")
    rows = [legacy, *_noise(60)]
    routing = _route(rows)
    assert routing["action"] == "answer_incoming" and routing["incoming"]["request_id"] is None
    assert _retrieve(tmp_path, shell, rows, routing) == {"status": "blocked", "reason": "legacy_request_not_in_recent_view"}
    # Visible in the recent view, the legacy request is still selected exactly.
    visible = _retrieve(tmp_path / "visible", shell, [*_noise(60), legacy], _route([*_noise(60), legacy]))
    assert visible["status"] == "ok" and visible["path"] == "recent_view"
    assert json.loads(visible["request_json"]) == legacy  # the legacy row's own body, not another recent row


def _legacy_routing() -> tuple[dict, dict]:
    legacy = _request(None, status="request")
    return legacy, _route([*_noise(60), legacy])


@WINDOWS_RECENT_VIEW
@pytest.mark.parametrize("shell", SHELLS, ids=lambda value: Path(value).stem)
def test_legacy_recent_row_to_another_target_is_blocked(tmp_path: Path, shell: str) -> None:
    legacy, routing = _legacy_routing()
    impostor = dict(legacy, to="fable-5")  # same sender, task_id and ts_utc
    assert _retrieve(tmp_path, shell, [*_noise(60), impostor], routing) == {"status": "blocked", "reason": "wrong_target"}


@WINDOWS_RECENT_VIEW
@pytest.mark.parametrize("shell", SHELLS, ids=lambda value: Path(value).stem)
@pytest.mark.parametrize("where", ("top_level", "payload"))
def test_recent_row_that_carries_a_request_id_is_not_treated_as_legacy(tmp_path: Path, shell: str, where: str) -> None:
    legacy, routing = _legacy_routing()
    # Not request-like, so the inventory has zero matches; that does not prove the ID absent.
    row = dict(legacy, type="message", status="answered", payload=dict(legacy["payload"]))
    if where == "top_level":
        row["request_id"] = "hidden-id-v1"
    else:
        row["payload"]["request_id"] = "hidden-id-v1"
    result = _retrieve(tmp_path, shell, [*_noise(60), row], routing)
    assert result == {"status": "blocked", "reason": "recent_row_has_request_id"}


@WINDOWS_RECENT_VIEW
@pytest.mark.parametrize("shell", SHELLS, ids=lambda value: Path(value).stem)
def test_duplicate_legacy_rows_in_recent_view_are_ambiguous(tmp_path: Path, shell: str) -> None:
    legacy, routing = _legacy_routing()
    result = _retrieve(tmp_path, shell, [*_noise(60), legacy, legacy], routing)
    assert result == {"status": "blocked", "reason": "legacy_request_not_in_recent_view"}


@pytest.mark.parametrize("shell", SHELLS, ids=lambda value: Path(value).stem)
def test_incomplete_inventory_cannot_prove_unique_match_or_absence(tmp_path: Path, shell: str) -> None:
    legacy, routing = _legacy_routing()
    fake_bin = tmp_path / "bin"
    shutil.copytree(BIN, fake_bin)
    (fake_bin / "Get-BridgeRequestInventory.ps1").write_text("\n".join((
        "param([string]$Agent,[string]$TaskId,[string]$TsUtc)",
        "'{\"schema\":\"wd.request-inventory.v2\",\"truncated\":true,\"requests\":[]}'",
    )), encoding="utf-8")
    result = _retrieve(tmp_path, shell, [legacy], routing, fake_bin)
    assert result == {"status": "blocked", "reason": "inventory_incomplete"}


@pytest.mark.parametrize("shell", SHELLS, ids=lambda value: Path(value).stem)
@pytest.mark.parametrize("matched_count", (None, 2, True))
def test_inventory_count_must_agree_with_complete_exact_selection(tmp_path, shell, matched_count):
    legacy, routing = _legacy_routing()
    fake_bin = tmp_path / "bin"
    shutil.copytree(BIN, fake_bin)
    data = dict(schema="wd.request-inventory.v2", truncated=False,
                matched_count=matched_count, requests=[])
    (fake_bin / "Get-BridgeRequestInventory.ps1").write_text("\n".join((
        "param([string]$Agent,[string]$TaskId,[string]$TsUtc)",
        f"'{json.dumps(data)}'",
    )), encoding="utf-8")
    assert _retrieve(tmp_path, shell, [legacy], routing, fake_bin) == {
        "status": "blocked", "reason": "inventory_incomplete"}


@pytest.mark.parametrize("shell", SHELLS, ids=lambda value: Path(value).stem)
def test_failed_recent_read_never_accepts_partial_stdout(tmp_path: Path, shell: str) -> None:
    legacy, routing = _legacy_routing()
    fake_bin = tmp_path / "bin"
    shutil.copytree(BIN, fake_bin)
    # The reader prints the exact row, then fails: its partial output is unknown.
    (fake_bin / "Read-AgentBridge.ps1").write_text("\n".join((
        "param([string]$Agent,[switch]$Raw,[switch]$NoAckReceived,[switch]$NoContinuity)",
        f"'{json.dumps([legacy])}'",
        "exit 3",
    )) + "\n", encoding="utf-8")
    result = _retrieve(tmp_path / "root", shell, [*_noise(60), legacy], routing, bin_dir=fake_bin)
    assert result == {"status": "blocked", "reason": "recent_read_failed"}

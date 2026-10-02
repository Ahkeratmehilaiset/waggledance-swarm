"""Immutable request-id reuse is compared ORDINALLY in the inventory loop and in Get-AgentBridgeStatus: a different request that
reuses a valid id differs from the first only by U+00AD / U+200B in its content or digest. Culture-aware -cne ignored those
(U+00AD in both shells, U+200B in pwsh 7), so the reuse read as an exact retry. Exact retries still coalesce. Synthetic tmp
runtimes only; the repository bin is executed as is."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[2]
BIN = ROOT / ".agent-bridge" / "bin"
INVENTORY = BIN / "Get-BridgeRequestInventory.ps1"
STATUS = BIN / "Get-AgentBridgeStatus.ps1"
SHELLS = tuple(dict.fromkeys(filter(None, (shutil.which("pwsh"), shutil.which("powershell.exe")))))
pytestmark = pytest.mark.skipif(not SHELLS or not INVENTORY.is_file(), reason="Bridge PowerShell package required")
ALL = pytest.mark.parametrize("shell", SHELLS, ids=lambda value: Path(value).stem)
SHY, ZWSP = "­", "​"


def request(index: int, **over: object) -> dict:
    value = dict(ts_utc=f"2026-10-02T00:00:{index:02d}Z", agent="codex-lead-1", agent_uuid="u", session_id="s", run_id="r",
                 to="claude-rco-2", type="wake_request", status="assigned", task_id="fx/task-1", request_id="req-1",
                 request_digest="digest-1", message="do A", payload={})
    value.update(over)
    return value


def runtime(tmp_path: Path, rows: list[dict]) -> Path:
    shared = tmp_path / "runtime" / "shared"
    shared.mkdir(parents=True)
    (shared / "events.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    return tmp_path / "runtime"


def run(shell: str, root: Path, script: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith(("AGENT_BRIDGE_", "WD_", "GIT_"))}
    env["AGENT_BRIDGE_RUNTIME_ROOT"] = str(root)
    return subprocess.run([shell, "-NoProfile", "-NonInteractive", "-File", str(script), *args],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=120)
    # errors="replace": Status writes console-encoded text for non-ASCII content; every assertion keys on ASCII fields.


def status_conflict(shell: str, root: Path) -> bool:
    result = run(shell, root, STATUS, "-Json")
    assert result.returncode == 0, result.stderr[-800:]
    return '"request_binding_conflict":  true' in result.stdout or '"request_binding_conflict": true' in result.stdout


REUSES = {
    "message_soft_hyphen": dict(message="do A" + SHY),
    "message_zero_width_space": dict(message="do A" + ZWSP),
    "digest_soft_hyphen": dict(request_digest="digest-1" + SHY),
    "digest_zero_width_space": dict(request_digest="digest-1" + ZWSP),
}


@ALL
@pytest.mark.parametrize("variant", sorted(REUSES))
def test_inventory_refuses_an_invisibly_different_reuse_of_an_immutable_id(tmp_path: Path, shell: str, variant: str) -> None:
    root = runtime(tmp_path, [request(1), request(2, **REUSES[variant])])
    result = run(shell, root, INVENTORY, "-Agent", "codex-lead-1", "-NoCache")
    assert result.returncode != 0 and not result.stdout.strip(), result.stdout[:300]
    assert "Conflicting content for immutable request ID req-1" in result.stderr


@ALL
@pytest.mark.parametrize("variant", sorted(REUSES))
def test_inventory_diagnostic_lists_the_invisible_reuse_as_an_immutable_conflict(tmp_path: Path, shell: str, variant: str) -> None:
    root = runtime(tmp_path, [request(1), request(2, **REUSES[variant])])
    data = json.loads(run(shell, root, INVENTORY, "-Agent", "codex-lead-1", "-NoCache", "-DiagnosticPartial").stdout)
    assert data["complete"] is False and [c["kind"] for c in data["conflicts"]] == ["immutable_id_content_conflict"]
    assert data["request_count"] == 0                                           # the conflicted id is never inventoried


@ALL
@pytest.mark.parametrize("variant", sorted(REUSES))
def test_status_flags_an_invisibly_different_reuse_as_a_binding_conflict(tmp_path: Path, shell: str, variant: str) -> None:
    assert status_conflict(shell, runtime(tmp_path, [request(1), request(2, **REUSES[variant])])) is True


@ALL
def test_exact_retry_still_coalesces_in_inventory_and_status(tmp_path: Path, shell: str) -> None:
    root = runtime(tmp_path, [request(1), request(1)])
    data = json.loads(run(shell, root, INVENTORY, "-Agent", "codex-lead-1", "-NoCache").stdout)
    assert [(r["request_id"], r["occurrences"]) for r in data["requests"]] == [("req-1", 2)]
    assert status_conflict(shell, root) is False


@ALL
def test_visibly_different_reuse_is_still_a_conflict(tmp_path: Path, shell: str) -> None:
    root = runtime(tmp_path, [request(1), request(2, message="do B")])
    assert "Conflicting content" in run(shell, root, INVENTORY, "-Agent", "codex-lead-1", "-NoCache").stderr
    assert status_conflict(shell, root) is True


@ALL
def test_distinct_ids_with_invisible_differences_are_separate_requests_not_conflicts(tmp_path: Path, shell: str) -> None:
    root = runtime(tmp_path, [request(1), request(2, request_id="req-2", message="do A" + SHY)])
    data = json.loads(run(shell, root, INVENTORY, "-Agent", "codex-lead-1", "-NoCache").stdout)
    assert sorted(r["request_id"] for r in data["requests"]) == ["req-1", "req-2"]
    assert status_conflict(shell, root) is False


@ALL
def test_conflicting_digest_markers_do_not_hide_raw_digest_reuse(tmp_path: Path, shell: str) -> None:
    rows = [request(1, request_digest="d1", payload={"request_digest": "payload"}),
            request(2, request_digest="d2", payload={"request_digest": "payload"})]
    root = runtime(tmp_path, rows)
    refused = run(shell, root, INVENTORY, "-Agent", "codex-lead-1", "-NoCache")
    assert refused.returncode != 0 and not refused.stdout.strip(), refused.stdout
    diagnostic = json.loads(run(shell, root, INVENTORY, "-Agent", "codex-lead-1", "-NoCache", "-DiagnosticPartial").stdout)
    assert [c["kind"] for c in diagnostic["conflicts"]] == ["immutable_id_content_conflict"]
    assert diagnostic["request_count"] == 0
    assert status_conflict(shell, root) is True


def entry_differs(shell: str, root: Path, tmp_path: Path, rows: list[dict]) -> bool:
    fixture = tmp_path / "entries.json"
    fixture.write_text(json.dumps(rows), encoding="utf-8")
    script = tmp_path / "entries.ps1"
    script.write_text("param([string]$Bin,[string]$Fixture)\n"
                      ". (Join-Path $Bin 'BridgeRequestContract.ps1')\n"
                      "$rows=Get-Content -LiteralPath $Fixture -Raw -Encoding UTF8|ConvertFrom-Json\n"
                      "[bool](Test-BridgeRequestEntryDiffers $rows[0] $rows[1])|ConvertTo-Json -Compress\n",
                      encoding="utf-8")
    result = run(shell, root, script, "-Bin", str(BIN), "-Fixture", str(fixture))
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@ALL
def test_shared_helper_retains_raw_digest_identity_despite_equal_conflict_markers(tmp_path: Path, shell: str) -> None:
    rows = [request(1, request_digest="d1", payload={"request_digest": "payload"}),
            request(2, request_digest="d2", payload={"request_digest": "payload"})]
    assert entry_differs(shell, runtime(tmp_path, rows), tmp_path, rows) is True


@ALL
def test_exact_retry_with_the_same_conflicting_digest_remains_an_exact_retry(tmp_path: Path, shell: str) -> None:
    rows = [request(1, request_digest="d1", payload={"request_digest": "payload"}),
            request(2, request_digest="d1", payload={"request_digest": "payload"})]
    root = runtime(tmp_path, rows)
    data = json.loads(run(shell, root, INVENTORY, "-Agent", "codex-lead-1", "-NoCache").stdout)
    assert [(r["request_id"], r["occurrences"]) for r in data["requests"]] == [("req-1", 2)]
    assert status_conflict(shell, root) is False
    assert entry_differs(shell, root, tmp_path, rows) is False


@ALL
def test_raw_digest_absent_and_null_remain_equivalent(tmp_path: Path, shell: str) -> None:
    first = request(1)
    first.pop("request_digest")
    rows = [first, request(2, request_digest=None)]
    root = runtime(tmp_path, rows)
    data = json.loads(run(shell, root, INVENTORY, "-Agent", "codex-lead-1", "-NoCache").stdout)
    assert data["requests"][0]["occurrences"] == 2
    assert entry_differs(shell, root, tmp_path, rows) is False

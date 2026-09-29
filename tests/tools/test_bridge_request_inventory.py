"""Get-BridgeRequestInventory enumerates every request ID of an exact requester.

Regression for the RCO1 6efa finding: the native wake read (Read-AgentBridge
-NoContinuity, 40-row whole-log tail) cannot rediscover a request once more
than 40 unrelated rows follow its bound answer. The inventory must still list
the exact request, and Get-BridgeReplySnapshot must still resolve the answer.
"""

from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from test_bridge_request_contract import events


ROOT = Path(__file__).resolve().parents[2]
BIN = ROOT / ".agent-bridge/bin"
INVENTORY = BIN / "Get-BridgeRequestInventory.ps1"
SNAPSHOT = BIN / "Get-BridgeReplySnapshot.ps1"
READER = BIN / "Read-AgentBridge.ps1"
SHELLS = list(dict.fromkeys(filter(None, (shutil.which("pwsh"), shutil.which("powershell.exe")))))
pytestmark = pytest.mark.skipif(not SHELLS, reason="PowerShell is required")
_SCRUB = ("AGENT_BRIDGE_", "CLAUDE_CODE_", "WD_", "GIT_")


def _env(root: Path) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if not key.startswith(_SCRUB)}
    env["AGENT_BRIDGE_RUNTIME_ROOT"] = str(root)
    return env


def _bound(request_id: str, **request_fields: object) -> tuple[dict, dict]:
    _, request, reply = events()
    request.update(request_id=request_id, request_digest=f"digest-{request_id}", **request_fields)
    reply.update(
        in_reply_to_request_id=request_id,
        in_reply_to_request_digest=f"digest-{request_id}",
        in_reply_to_requester={key: request[key] for key in ("agent", "agent_uuid", "session_id", "run_id")},
    )
    return request, reply


def _noise(count: int) -> list[dict]:
    # Every other noise row is a request-ID row of ANOTHER requester, so it also
    # lands in the derived reply index: a tail cutoff there must be caught too.
    rows = []
    for index in range(count):
        row = dict(ts_utc="2026-09-18T08:00:00Z", agent="fable-5", to="operator", type="message",
                   status="reported", task_id=f"noise/{index}", message="noise")
        if index % 2:
            row.update(to="codex-tools-1", type="wake_request", status="request", request_id=f"noise-{index}")
        rows.append(row)
    return rows


def _write(root: Path, rows: list[dict], tail: bytes = b"") -> Path:
    shared = root / "shared"
    shared.mkdir(parents=True, exist_ok=True)
    log = shared / "events.jsonl"
    log.write_bytes(b"".join(json.dumps(row).encode() + b"\n" for row in rows) + tail)
    return log


def _run(shell: str, script: Path, root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([shell, "-NoProfile", "-NonInteractive", "-File", str(script), *args],
                          env=_env(root), capture_output=True, text=True, encoding="utf-8", timeout=240)


def _inventory(shell: str, root: Path, *args: str) -> dict:
    process = _run(shell, INVENTORY, root, *args)
    assert process.returncode == 0, process.stderr
    return json.loads(process.stdout)


@pytest.mark.parametrize("shell", SHELLS, ids=lambda value: Path(value).stem)
@pytest.mark.parametrize("no_cache", (False, True), ids=("cached", "no_cache"))
def test_bound_answer_hidden_by_60_noise_rows_is_still_enumerated_and_resolved(
    tmp_path: Path, shell: str, no_cache: bool,
) -> None:
    request, reply = _bound("hidden-by-noise-v1")
    log = _write(tmp_path, [request, reply, *_noise(60)])
    before = log.read_bytes()

    # Reproduce the gap with the exact native wake read: the request is gone.
    wake_view = _run(shell, READER, tmp_path, "-Agent", "codex-lead-1", "-Raw", "-NoAckReceived", "-NoContinuity")
    assert wake_view.returncode == 0, wake_view.stderr
    assert "hidden-by-noise-v1" not in wake_view.stdout

    extra = ("-NoCache",) if no_cache else ()
    result = _inventory(shell, tmp_path, "-Agent", "codex-lead-1", *extra)
    assert result["schema"] == "wd.request-inventory.v2"
    assert result["authority_effect"] == "none"
    assert result["request_count"] == 1
    entry = result["requests"][0]
    assert entry["request_id"] == "hidden-by-noise-v1"
    assert "request" not in entry
    assert result["truncated"] is False
    exact = _inventory(shell, tmp_path, "-Agent", "codex-lead-1", "-RequestId",
                       entry["request_id"], "-IncludeRequest", *extra)
    assert exact["requests"][0]["request"] == request
    assert entry["answer_state"] == "not_evaluated"
    assert result["snapshot_bytes"] == len(before)
    assert result["snapshot_cursor"]["offset"] == len(before)

    snapshot = _run(shell, SNAPSHOT, tmp_path, "-RequestId", entry["request_id"], "-Requester", "codex-lead-1", *extra)
    assert snapshot.returncode == 0, snapshot.stderr
    resolved = json.loads(snapshot.stdout)
    assert resolved["results"][0]["state"] == "answered"
    assert resolved["results"][0]["answers"] == [reply]

    assert log.read_bytes() == before
    written = sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*") if path.is_file())
    if no_cache:
        assert written == ["shared/events.jsonl"]
    else:
        assert all(path == "shared/events.jsonl" or path.startswith("shared/cache/") for path in written)


@pytest.mark.parametrize("shell", SHELLS, ids=lambda value: Path(value).stem)
def test_no_tail_or_age_cutoff_beyond_5000_rows(tmp_path: Path, shell: str) -> None:
    old, _ = _bound("old-before-5200-noise", ts_utc="2026-01-01T00:00:00Z")
    late, _ = _bound("late-after-noise", ts_utc="2026-09-18T09:00:00Z")
    _write(tmp_path, [old, *_noise(5200), late])
    result = _inventory(shell, tmp_path, "-Agent", "codex-lead-1", "-NoCache")
    assert [entry["request_id"] for entry in result["requests"]] == ["late-after-noise", "old-before-5200-noise"]
    assert result["parsed_rows"] == 5202


@pytest.mark.parametrize("shell", SHELLS, ids=lambda value: Path(value).stem)
def test_exact_case_identity_session_filter_and_foreign_ids(tmp_path: Path, shell: str) -> None:
    upper, _ = _bound("Case-1")
    lower, _ = _bound("case-1")
    impostor, _ = _bound("impostor-1", agent="Codex-Lead-1")
    other_session, _ = _bound("other-session-1", session_id="Lead-Session")
    # Lead's own non-request row that happens to carry a request_id is not a request.
    not_a_request = dict(ts_utc="2026-09-18T07:40:00Z", agent="codex-lead-1", to="codex-tools-1",
                         type="message", status="answered", task_id="fixture/closure",
                         request_id="closure-not-request")
    foreign, _ = _bound("Case-1", agent="codex-tools-1", to="codex-lead-1")
    _write(tmp_path, [upper, lower, impostor, other_session, foreign, not_a_request])

    result = _inventory(shell, tmp_path, "-Agent", "codex-lead-1", "-NoCache")
    ids = [entry["request_id"] for entry in result["requests"]]
    assert ids == ["other-session-1", "case-1", "Case-1"]
    assert result["case_variant_request_ids"] == ["case-1", "Case-1"]
    by_id = {entry["request_id"]: entry for entry in result["requests"]}
    assert by_id["Case-1"]["id_also_used_by_other_requester"] is True
    assert by_id["case-1"]["id_also_used_by_other_requester"] is False

    filtered = _inventory(shell, tmp_path, "-Agent", "codex-lead-1", "-SessionId", "lead-session", "-NoCache")
    assert [entry["request_id"] for entry in filtered["requests"]] == ["case-1", "Case-1"]
    assert filtered["session_id_filter"] == "lead-session"


@pytest.mark.parametrize("shell", SHELLS, ids=lambda value: Path(value).stem)
def test_identical_duplicate_counts_once_and_conflict_fails_closed(tmp_path: Path, shell: str) -> None:
    request, reply = _bound("retry-v1")
    _write(tmp_path, [request, reply, request])
    result = _inventory(shell, tmp_path, "-Agent", "codex-lead-1", "-NoCache")
    assert result["request_count"] == 1
    assert result["requests"][0]["occurrences"] == 2
    assert result["requests"][0]["first_indexed_position"] == 0

    changed = deepcopy(request)
    changed["message"] = "different content under the same immutable id"
    _write(tmp_path, [request, reply, changed])
    process = _run(shell, INVENTORY, tmp_path, "-Agent", "codex-lead-1", "-NoCache")
    assert process.returncode != 0
    assert "Conflicting content for immutable request ID retry-v1" in process.stderr
    assert process.stdout.strip() == ""


@pytest.mark.parametrize("shell", SHELLS, ids=lambda value: Path(value).stem)
@pytest.mark.parametrize("case", ("partial_line", "invalid_json", "missing_log", "own_id_binding_conflict"))
def test_read_and_malformed_failures_fail_closed(tmp_path: Path, shell: str, case: str) -> None:
    request, reply = _bound("fail-closed-v1")
    if case == "partial_line":
        _write(tmp_path, [request, reply], tail=b'{"agent":"codex-lead-1"')
    elif case == "invalid_json":
        _write(tmp_path, [request], tail=b"{not json}\n")
    elif case == "missing_log":
        (tmp_path / "shared").mkdir()
    else:
        request["payload"]["request_id"] = "a-different-id"
        _write(tmp_path, [request, reply])
    process = _run(shell, INVENTORY, tmp_path, "-Agent", "codex-lead-1", "-NoCache")
    assert process.returncode != 0
    assert process.stdout.strip() == ""


@pytest.mark.parametrize("shell", SHELLS, ids=lambda value: Path(value).stem)
def test_other_requesters_malformed_row_does_not_blind_the_inventory(tmp_path: Path, shell: str) -> None:
    mine, _ = _bound("mine-v1")
    theirs, _ = _bound("theirs-v1", agent="codex-tools-1", to="codex-lead-1")
    theirs["payload"]["agent"] = "someone-else"
    _write(tmp_path, [theirs, mine])
    result = _inventory(shell, tmp_path, "-Agent", "codex-lead-1", "-NoCache")
    assert [entry["request_id"] for entry in result["requests"]] == ["mine-v1"]

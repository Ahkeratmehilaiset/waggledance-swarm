"""Bounded inventory contract on a synthetic, old-to-new request history."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / ".agent-bridge/bin/Get-BridgeRequestInventory.ps1"
BASE_BIN = SOURCE.parent
SHELLS = tuple(dict.fromkeys(filter(None, (shutil.which("pwsh"), shutil.which("powershell.exe")))))
pytestmark = pytest.mark.skipif(not SHELLS or not BASE_BIN.is_dir(), reason="Bridge PowerShell package required")


def _fixture(tmp_path: Path) -> tuple[Path, Path]:
    runtime = tmp_path / "runtime"
    shared = runtime / "shared"
    shared.mkdir(parents=True)
    rows = []
    for index in range(600):
        rows.append(dict(
            ts_utc=f"2026-09-29T{index // 3600:02d}:{index // 60 % 60:02d}:{index % 60:02d}Z",
            agent="codex-lead-1", agent_uuid="lead-uuid", session_id="lead-session",
            run_id="lead-run", to="codex-tools-1", type="wake_request",
            status="assigned", task_id=f"fixture/task-{index:03d}",
            request_id=f"request-{index:03d}", request_digest=f"digest-{index:03d}",
            message="bounded inventory regression", payload={},
        ))
    (shared / "events.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )
    bin_dir = tmp_path / "bin"
    shutil.copytree(BASE_BIN, bin_dir)
    if SOURCE.exists():
        shutil.copy2(SOURCE, bin_dir / SOURCE.name)
    return runtime, bin_dir / SOURCE.name


def _run(shell: str, runtime: Path, script: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {key: value for key, value in os.environ.items() if not key.startswith(("AGENT_BRIDGE_", "WD_", "GIT_"))}
    env["AGENT_BRIDGE_RUNTIME_ROOT"] = str(runtime)
    return subprocess.run(
        [shell, "-NoProfile", "-NonInteractive", "-File", str(script), "-Agent", "codex-lead-1", "-NoCache", *args],
        capture_output=True, text=True, encoding="utf-8", env=env, timeout=45,
    )


@pytest.mark.parametrize("shell", SHELLS, ids=lambda value: Path(value).stem)
def test_600_requests_default_is_bounded_and_discoverable(tmp_path: Path, shell: str) -> None:
    runtime, script = _fixture(tmp_path)
    result = _run(shell, runtime, script)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["request_count"] == 600
    assert len(result.stdout) < 50_000
    assert data["truncated"] is True
    assert data["next_cursor"] is not None
    assert len(data["requests"]) <= 50
    assert data["requests"][0]["request_id"] == "request-599"
    assert all("request" not in entry for entry in data["requests"])


@pytest.mark.parametrize("shell", SHELLS, ids=lambda value: Path(value).stem)
def test_exact_old_request_remains_retrievable(tmp_path: Path, shell: str) -> None:
    runtime, script = _fixture(tmp_path)
    result = _run(shell, runtime, script, "-RequestId", "request-000")
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["request_count"] == 600
    assert data["matched_count"] == 1
    assert data["requests"][0]["request_id"] == "request-000"
    assert data["truncated"] is False


@pytest.mark.parametrize("shell", SHELLS, ids=lambda value: Path(value).stem)
def test_cursor_is_newest_first_and_refuses_changed_snapshot(tmp_path: Path, shell: str) -> None:
    runtime, script = _fixture(tmp_path)
    first = json.loads(_run(shell, runtime, script, "-PageSize", "2").stdout)
    assert [row["request_id"] for row in first["requests"]] == ["request-599", "request-598"]
    second = _run(shell, runtime, script, "-PageSize", "2", "-Cursor", first["next_cursor"])
    assert second.returncode == 0, second.stderr
    assert [row["request_id"] for row in json.loads(second.stdout)["requests"]] == ["request-597", "request-596"]

    log = runtime / "shared/events.jsonl"
    with log.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(dict(ts_utc="2026-09-29T01:00:00Z", agent="fable-5", type="message")) + "\n")
    stale = _run(shell, runtime, script, "-Cursor", first["next_cursor"])
    assert stale.returncode != 0
    assert "cursor does not match" in stale.stderr
    assert not stale.stdout.strip()


@pytest.mark.parametrize("shell", SHELLS, ids=lambda value: Path(value).stem)
def test_task_and_timestamp_exact_filters_and_request_opt_in(tmp_path: Path, shell: str) -> None:
    runtime, script = _fixture(tmp_path)
    broad = _run(shell, runtime, script, "-IncludeRequest")
    assert broad.returncode != 0
    assert not broad.stdout.strip()

    exact = _run(shell, runtime, script, "-TaskId", "fixture/task-000", "-TsUtc", "2026-09-29T00:00:00Z", "-IncludeRequest")
    assert exact.returncode == 0, exact.stderr
    data = json.loads(exact.stdout)
    assert data["matched_count"] == 1
    assert data["requests"][0]["request"]["request_id"] == "request-000"


@pytest.mark.parametrize("shell", SHELLS, ids=lambda value: Path(value).stem)
def test_large_exact_request_fails_without_partial_json(tmp_path: Path, shell: str) -> None:
    runtime, script = _fixture(tmp_path)
    log = runtime / "shared/events.jsonl"
    rows = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    rows[0]["message"] = "x" * 60_000
    log.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    result = _run(shell, runtime, script, "-RequestId", "request-000", "-IncludeRequest")
    assert result.returncode != 0
    assert "exceeds 50000 characters" in result.stderr
    assert not result.stdout.strip()


@pytest.mark.parametrize("shell", SHELLS, ids=lambda value: Path(value).stem)
def test_duplicate_count_and_conflicting_id_fail_closed(tmp_path: Path, shell: str) -> None:
    runtime, script = _fixture(tmp_path)
    log = runtime / "shared/events.jsonl"
    first = json.loads(log.read_text(encoding="utf-8").splitlines()[0])
    with log.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(first) + "\n")
    ok = _run(shell, runtime, script, "-RequestId", "request-000")
    assert ok.returncode == 0, ok.stderr
    assert json.loads(ok.stdout)["requests"][0]["occurrences"] == 2

    first["message"] = "conflicting immutable request"
    with log.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(first) + "\n")
    conflict = _run(shell, runtime, script, "-RequestId", "request-000")
    assert conflict.returncode != 0
    assert "Conflicting content for immutable request ID" in conflict.stderr
    assert not conflict.stdout.strip()


@pytest.mark.parametrize("shell", SHELLS, ids=lambda value: Path(value).stem)
def test_partial_last_line_fails_closed(tmp_path: Path, shell: str) -> None:
    runtime, script = _fixture(tmp_path)
    with (runtime / "shared/events.jsonl").open("ab") as stream:
        stream.write(b'{"agent":"codex-lead-1"')
    result = _run(shell, runtime, script)
    assert result.returncode != 0
    assert not result.stdout.strip()


@pytest.mark.parametrize("shell", SHELLS, ids=lambda value: Path(value).stem)
@pytest.mark.parametrize("changed", (
    ("-SessionId", "different-session"),
    ("-RequestId", "request-000"),
    ("-TaskId", "fixture/task-000"),
    ("-TsUtc", "2026-09-29T00:00:00Z"),
))
def test_cursor_rejects_changed_filters(tmp_path: Path, shell: str, changed: tuple[str, str]) -> None:
    runtime, script = _fixture(tmp_path)
    first = json.loads(_run(shell, runtime, script, "-PageSize", "2").stdout)
    changed_page = _run(shell, runtime, script, "-PageSize", "2", "-Cursor", first["next_cursor"], *changed)
    assert changed_page.returncode != 0
    assert "cursor does not match" in changed_page.stderr
    assert not changed_page.stdout.strip()


@pytest.mark.parametrize("shell", SHELLS, ids=lambda value: Path(value).stem)
def test_all_pages_cover_every_id_once_without_loss(tmp_path: Path, shell: str) -> None:
    runtime, script = _fixture(tmp_path)
    seen: list[str] = []
    cursor: str | None = None
    while True:
        args = ("-Cursor", cursor) if cursor else ()
        result = _run(shell, runtime, script, *args)
        assert result.returncode == 0, result.stderr
        data = json.loads(result.stdout)
        assert data["request_count"] == 600
        seen.extend(row["request_id"] for row in data["requests"])
        cursor = data["next_cursor"]
        assert data["truncated"] is bool(cursor)
        if not cursor:
            break
    assert seen == [f"request-{index:03d}" for index in range(599, -1, -1)]


@pytest.mark.parametrize("shell", SHELLS, ids=lambda value: Path(value).stem)
def test_include_request_rejects_no_exact_match(tmp_path: Path, shell: str) -> None:
    runtime, script = _fixture(tmp_path)
    result = _run(shell, runtime, script, "-RequestId", "absent-request", "-IncludeRequest")
    assert result.returncode != 0
    assert "requires exactly one matched request" in result.stderr
    assert not result.stdout.strip()


@pytest.mark.parametrize("shell", SHELLS, ids=lambda value: Path(value).stem)
def test_include_request_rejects_ambiguous_task_timestamp_even_page_one(tmp_path: Path, shell: str) -> None:
    runtime, script = _fixture(tmp_path)
    log = runtime / "shared/events.jsonl"
    rows = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    rows[1]["task_id"] = rows[0]["task_id"]
    rows[1]["ts_utc"] = rows[0]["ts_utc"]
    log.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    result = _run(shell, runtime, script, "-TaskId", rows[0]["task_id"],
                  "-TsUtc", rows[0]["ts_utc"], "-PageSize", "1", "-IncludeRequest")
    assert result.returncode != 0
    assert "requires exactly one matched request" in result.stderr
    assert not result.stdout.strip()

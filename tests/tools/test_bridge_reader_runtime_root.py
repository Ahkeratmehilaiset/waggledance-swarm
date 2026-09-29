"""Reader scripts must never fall back to a package-local event log."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[2]
SOURCE_BIN = ROOT / ".agent-bridge/bin"
BASE_BIN = SOURCE_BIN
SHELLS = tuple(dict.fromkeys(filter(None, (shutil.which("pwsh"), shutil.which("powershell.exe")))))
SCRIPTS = (
    ("Get-BridgeRequestInventory.ps1", ("-Agent", "codex-lead-1")),
    ("Get-BridgeReplySnapshot.ps1", ("-RequestId", "request-1", "-Requester", "codex-lead-1")),
)
pytestmark = pytest.mark.skipif(not SHELLS or not BASE_BIN.is_dir(), reason="Bridge PowerShell package required")


def _event(message: str) -> dict:
    return dict(
        ts_utc="2026-09-29T00:00:00Z", agent="codex-lead-1",
        agent_uuid="lead-uuid", session_id="lead-session", run_id="lead-run",
        to="codex-tools-1", type="wake_request", status="assigned",
        task_id="fixture/request", request_id="request-1", request_digest="digest-1",
        message=message, payload={},
    )


def _log(root: Path, message: str) -> None:
    shared = root / "shared"
    shared.mkdir(parents=True)
    (shared / "events.jsonl").write_text(json.dumps(_event(message)) + "\n", encoding="utf-8")


def _fixture(tmp_path: Path) -> tuple[Path, Path]:
    package = tmp_path / "package"
    shutil.copytree(BASE_BIN, package / "bin")
    for name, _ in SCRIPTS:
        source = SOURCE_BIN / name
        if source.is_file():
            shutil.copy2(source, package / "bin" / name)
    _log(package, "STALE_PACKAGE_LOCAL_EVENT")
    external = tmp_path / "external-runtime"
    _log(external, "CURRENT_EXTERNAL_EVENT")
    return package, external


def _run(shell: str, package: Path, name: str, args: tuple[str, ...], root: str | None) -> subprocess.CompletedProcess[str]:
    env = {key: value for key, value in os.environ.items() if not key.startswith(("AGENT_BRIDGE_", "WD_", "GIT_"))}
    if root is not None:
        env["AGENT_BRIDGE_RUNTIME_ROOT"] = root
    return subprocess.run(
        [shell, "-NoProfile", "-NonInteractive", "-File", str(package / "bin" / name), *args],
        env=env, capture_output=True, text=True, encoding="utf-8", timeout=30,
    )


@pytest.mark.parametrize("shell", SHELLS, ids=lambda value: Path(value).stem)
@pytest.mark.parametrize("name,args", SCRIPTS)
def test_missing_root_fails_before_stale_local_read_or_cache(tmp_path: Path, shell: str, name: str, args: tuple[str, ...]) -> None:
    package, _ = _fixture(tmp_path)
    result = _run(shell, package, name, args, None)
    assert result.returncode != 0
    assert "AGENT_BRIDGE_RUNTIME_ROOT" in result.stderr
    assert not result.stdout.strip()
    assert not (package / "shared/cache").exists()


@pytest.mark.parametrize("shell", SHELLS, ids=lambda value: Path(value).stem)
@pytest.mark.parametrize("name,args", SCRIPTS)
@pytest.mark.parametrize("bad_root", ("   ", "relative/runtime", "C:relative", "\\relative"))
def test_blank_or_relative_root_fails_closed(tmp_path: Path, shell: str, name: str, args: tuple[str, ...], bad_root: str) -> None:
    package, _ = _fixture(tmp_path)
    result = _run(shell, package, name, args, bad_root)
    assert result.returncode != 0
    assert "AGENT_BRIDGE_RUNTIME_ROOT" in result.stderr
    assert not result.stdout.strip()
    assert not (package / "shared/cache").exists()


@pytest.mark.parametrize("shell", SHELLS, ids=lambda value: Path(value).stem)
@pytest.mark.parametrize("name,args", SCRIPTS)
def test_explicit_external_root_wins_over_stale_package_log(tmp_path: Path, shell: str, name: str, args: tuple[str, ...]) -> None:
    package, external = _fixture(tmp_path)
    result = _run(shell, package, name, (*args, "-NoCache"), str(external))
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["runtime_root_source"] == "environment"
    if name == "Get-BridgeRequestInventory.ps1":
        assert data["requests"][0]["request_id"] == "request-1"
    else:
        assert data["request"]["message"] == "CURRENT_EXTERNAL_EVENT"
    assert "STALE_PACKAGE_LOCAL_EVENT" not in result.stdout
    assert not (package / "shared/cache").exists()

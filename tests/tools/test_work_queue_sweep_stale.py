from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import tools.work_queue_sweep_stale as sweep_cli  # noqa: E402
from waggledance.core.work_queue import claim_task  # noqa: E402


def _now() -> datetime:
    return datetime(2026, 5, 18, 12, 0, 0, tzinfo=timezone.utc)


def test_direct_cli_bootstraps_repo_imports_outside_repo(tmp_path: Path) -> None:
    completed = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "work_queue_sweep_stale.py"), "--help"],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )

    assert completed.returncode == 0, (completed.stdout, completed.stderr)
    assert "--max-age-seconds" in completed.stdout


def test_cli_dry_run_human_output_no_stale(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bridge = tmp_path / ".agent-bridge"
    bridge.mkdir(parents=True)
    exit_code = sweep_cli.main(["--bridge-root", str(bridge), "--max-age-seconds", "60"])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "no stale claims" in captured.out


def test_cli_dry_run_lists_stale_in_human_mode(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bridge = tmp_path / ".agent-bridge"
    claim_task(
        agent="claude-1",
        task_id="task-cli-stale",
        summary="stale",
        bridge_root=bridge,
        now_utc=_now() - timedelta(hours=1),
    )
    exit_code = sweep_cli.main(
        ["--bridge-root", str(bridge), "--max-age-seconds", "60"]
    )
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "WOULD ARCHIVE" in captured.out
    assert "task-cli-stale" in captured.out
    # Dry-run did not mutate.
    assert (bridge / "work_queue" / "claims" / "task-cli-stale.json").exists()


def test_cli_uses_runtime_bridge_root_env_by_default(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    bridge = tmp_path / "runtime" / ".agent-bridge"
    claim_task(
        agent="claude-1",
        task_id="task-cli-env-stale",
        summary="stale from runtime root",
        bridge_root=bridge,
        now_utc=_now() - timedelta(hours=1),
    )
    monkeypatch.setenv("AGENT_BRIDGE_RUNTIME_ROOT", str(bridge))

    exit_code = sweep_cli.main(["--max-age-seconds", "60", "--json"])
    captured = capsys.readouterr()

    assert exit_code == 0
    payload = json.loads(captured.out)
    assert payload["archived"][0]["task_id"] == "task-cli-env-stale"
    assert payload["archived"][0]["applied"] is False
    # Dry-run did not mutate the runtime-root claim.
    assert (bridge / "work_queue" / "claims" / "task-cli-env-stale.json").exists()


def test_cli_honors_environment_fallback_for_legacy_claim(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    claim_task(
        agent="claude-1",
        task_id="task-cli-legacy-env",
        summary="legacy fallback",
        bridge_root=bridge,
        now_utc=datetime.now(timezone.utc) - timedelta(seconds=500),
    )
    claim_path = bridge / "work_queue" / "claims" / "task-cli-legacy-env.json"
    payload = json.loads(claim_path.read_text(encoding="utf-8"))
    payload.pop("lease_seconds")
    payload.pop("claim_lease_expires_utc")
    claim_path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setenv("AGENT_BRIDGE_STALE_LEASE_SECONDS", "1000")

    exit_code = sweep_cli.main(
        ["--bridge-root", str(bridge), "--json"]
    )
    captured = capsys.readouterr()

    assert exit_code == 0
    report = json.loads(captured.out)
    assert report["max_age_seconds"] == 1000
    assert report["archived"] == []


def test_cli_apply_archives_and_emits_json(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bridge = tmp_path / ".agent-bridge"
    claim_task(
        agent="claude-1",
        task_id="task-cli-apply",
        summary="apply me",
        bridge_root=bridge,
        now_utc=_now() - timedelta(hours=1),
    )
    exit_code = sweep_cli.main(
        [
            "--bridge-root",
            str(bridge),
            "--max-age-seconds",
            "60",
            "--apply",
            "--json",
        ]
    )
    captured = capsys.readouterr()
    assert exit_code == 0
    payload = json.loads(captured.out)
    assert payload["applied"] is True
    assert payload["max_age_seconds"] == 60
    assert len(payload["archived"]) == 1
    assert payload["archived"][0]["task_id"] == "task-cli-apply"
    assert payload["archived"][0]["applied"] is True
    # Original claim gone, archived path exists.
    assert not (bridge / "work_queue" / "claims" / "task-cli-apply.json").exists()
    archived_path = Path(payload["archived"][0]["archived_path"])
    assert archived_path.exists()


def test_cli_rejects_negative_max_age_seconds(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bridge = tmp_path / ".agent-bridge"
    claim_task(
        agent="claude-1",
        task_id="task-cli-neg",
        summary="must not be archived",
        bridge_root=bridge,
        now_utc=_now() - timedelta(seconds=10),
    )
    exit_code = sweep_cli.main(
        [
            "--bridge-root",
            str(bridge),
            "--max-age-seconds",
            "-1",
            "--apply",
        ]
    )
    captured = capsys.readouterr()
    assert exit_code == 1
    assert "max_age_seconds must be positive" in captured.err
    # Original claim untouched.
    assert (bridge / "work_queue" / "claims" / "task-cli-neg.json").exists()


def test_cli_rejects_zero_max_age_seconds(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bridge = tmp_path / ".agent-bridge"
    claim_task(
        agent="claude-1",
        task_id="task-cli-zero",
        summary="must not be archived",
        bridge_root=bridge,
    )
    exit_code = sweep_cli.main(
        ["--bridge-root", str(bridge), "--max-age-seconds", "0", "--apply"]
    )
    captured = capsys.readouterr()
    assert exit_code == 1
    assert "max_age_seconds must be positive" in captured.err


def test_cli_bridge_root_missing_returns_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = tmp_path / "does-not-exist"
    exit_code = sweep_cli.main(["--bridge-root", str(missing)])
    captured = capsys.readouterr()
    assert exit_code == 2
    assert "bridge root not found" in captured.err

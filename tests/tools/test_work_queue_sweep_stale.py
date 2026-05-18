from __future__ import annotations

import json
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

# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

import pytest

from tools.invoke_grok_review import load_grok_config, run_grok_review

NOW = datetime(2026, 6, 5, 6, 30, tzinfo=timezone.utc)


def _write_config(tmp_path: Path, **overrides: object) -> Path:
    config = {
        "enabled": False,
        "allow_network": False,
        "advisory_only": True,
        "default_model": "grok-code-fast-1",
        "api_key_env": "XAI_API_KEY",
        "api_base_url_env": "GROK_API_BASE_URL",
        "chat_completions_path": "/v1/chat/completions",
        "max_calls_per_day": 0,
        "max_prompt_bytes": 12000,
        "max_response_bytes": 20000,
        "timeout_sec": 60,
    }
    config.update(overrides)
    path = tmp_path / "grok_budget.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    return path


def test_default_config_is_inactive_and_advisory_only() -> None:
    config = load_grok_config(Path("configs/grok_budget.json"))

    assert config["enabled"] is False
    assert config["allow_network"] is False
    assert config["advisory_only"] is True
    assert config["max_calls_per_day"] == 0


def test_disabled_config_refuses_without_network_or_state_write(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path)
    state_path = tmp_path / "state.json"

    report = run_grok_review(
        prompt="Review this plan.",
        task_id="grok-test",
        agent="codex-lead-1",
        config_path=config_path,
        state_path=state_path,
        now=NOW,
        env={},
    )

    assert report["ok"] is False
    assert report["decision"] == "grok_disabled"
    assert report["network_attempted"] is False
    assert report["budget"]["can_call"] is False
    assert not state_path.exists()


def test_dry_run_does_not_require_key_budget_or_state_write(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path)
    state_path = tmp_path / "state.json"

    report = run_grok_review(
        prompt="Review this plan.",
        config_path=config_path,
        state_path=state_path,
        dry_run=True,
        now=NOW,
        env={},
    )

    assert report["ok"] is True
    assert report["decision"] == "dry_run_ready"
    assert report["network_attempted"] is False
    assert report["would_call"] is False
    assert report["would_refuse_reason"] == "config enabled=false"
    assert not state_path.exists()


def test_budget_exhaustion_fails_closed_before_secret_lookup(tmp_path: Path) -> None:
    config_path = _write_config(
        tmp_path,
        enabled=True,
        allow_network=True,
        max_calls_per_day=0,
    )

    report = run_grok_review(
        prompt="Review this plan.",
        config_path=config_path,
        state_path=tmp_path / "state.json",
        now=NOW,
        env={"GROK_API_BASE_URL": "https://example.invalid"},
    )

    assert report["ok"] is False
    assert report["decision"] == "grok_budget_exhausted"
    assert report["network_attempted"] is False


def test_missing_api_key_refuses_without_network(tmp_path: Path) -> None:
    config_path = _write_config(
        tmp_path,
        enabled=True,
        allow_network=True,
        max_calls_per_day=1,
    )

    report = run_grok_review(
        prompt="Review this plan.",
        config_path=config_path,
        state_path=tmp_path / "state.json",
        now=NOW,
        env={"GROK_API_BASE_URL": "https://example.invalid"},
    )

    assert report["ok"] is False
    assert report["decision"] == "missing_api_key"
    assert report["reason"] == "XAI_API_KEY"
    assert report["network_attempted"] is False


def test_private_marker_is_refused(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path)

    with pytest.raises(Exception) as exc_info:
        run_grok_review(
            prompt="PRIVATE_MARKER must not pass",
            config_path=config_path,
            now=NOW,
            env={},
        )

    assert "private_marker_refused" in str(exc_info.value)


def test_cli_json_refusal_shape(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path)
    script = Path(__file__).resolve().parents[2] / "tools" / "invoke_grok_review.py"

    completed = subprocess.run(
        [
            sys.executable,
            str(script),
            "--prompt",
            "Review this plan.",
            "--config",
            str(config_path),
            "--json",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 3
    report = json.loads(completed.stdout)
    assert report["decision"] == "grok_disabled"
    assert report["network_attempted"] is False

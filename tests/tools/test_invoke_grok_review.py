# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from tools.invoke_grok_review import GrokReviewError, load_grok_config, run_grok_review

NOW = datetime(2026, 6, 5, 6, 30, tzinfo=timezone.utc)
MAIN_SHA = "a" * 40
OTHER_SHA = "b" * 40


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


def _freshness(**overrides: object) -> dict[str, object]:
    freshness: dict[str, object] = {
        "remote_main_sha": MAIN_SHA,
        "local_origin_main_sha": MAIN_SHA,
        "worktree_head": MAIN_SHA,
    }
    freshness.update(overrides)
    return freshness


def _git_repo_with_origin_main(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "git-root"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.invalid"],
        cwd=repo,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=repo,
        check=True,
    )
    (repo / "README.md").write_text("test\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(["git", "branch", "-M", "main"], cwd=repo, check=True)
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    subprocess.run(
        ["git", "update-ref", "refs/remotes/origin/main", "HEAD"],
        cwd=repo,
        check=True,
    )
    return repo, sha


def _powershell() -> str:
    shell = (
        shutil.which("pwsh")
        or shutil.which("powershell")
        or shutil.which("powershell.exe")
    )
    if not shell:
        pytest.skip("PowerShell is required for Invoke-GrokReview.ps1 smoke tests")
    return shell


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


def test_required_freshness_proof_is_reported_before_disabled_gate(
    tmp_path: Path,
) -> None:
    config_path = _write_config(tmp_path)

    report = run_grok_review(
        prompt="Review this plan.",
        config_path=config_path,
        require_freshness=True,
        freshness=_freshness(pr_head_sha=OTHER_SHA),
        git_root=None,
        now=NOW,
        env={},
    )

    assert report["ok"] is False
    assert report["decision"] == "grok_disabled"
    assert report["freshness_required"] is True
    assert report["freshness"]["freshness_ok"] is True
    assert report["freshness"]["remote_main_sha"] == MAIN_SHA
    assert report["freshness"]["pr_head_sha"] == OTHER_SHA
    assert report["network_attempted"] is False


def test_required_freshness_missing_refuses_before_state_or_network(
    tmp_path: Path,
) -> None:
    config_path = _write_config(
        tmp_path,
        enabled=True,
        allow_network=True,
        max_calls_per_day=1,
    )
    state_path = tmp_path / "state.json"
    state_path.write_text("{", encoding="utf-8")

    with pytest.raises(GrokReviewError) as exc_info:
        run_grok_review(
            prompt="Review this plan.",
            config_path=config_path,
            state_path=state_path,
            require_freshness=True,
            now=NOW,
            env={
                "GROK_API_BASE_URL": "https://example.invalid",
                "XAI_API_KEY": "not-used",
            },
        )

    assert exc_info.value.report["decision"] == "missing_freshness_proof"
    assert exc_info.value.report["reason"] == "freshness proof required"
    assert exc_info.value.report["network_attempted"] is False


def test_stale_freshness_refuses_before_secret_lookup_or_network(
    tmp_path: Path,
) -> None:
    config_path = _write_config(
        tmp_path,
        enabled=True,
        allow_network=True,
        max_calls_per_day=1,
    )

    with pytest.raises(GrokReviewError) as exc_info:
        run_grok_review(
            prompt="Review this plan.",
            config_path=config_path,
            require_freshness=True,
            freshness=_freshness(worktree_head=OTHER_SHA),
            now=NOW,
            env={"GROK_API_BASE_URL": "https://example.invalid"},
        )

    assert exc_info.value.report["decision"] == "stale_freshness_proof"
    assert exc_info.value.report["reason"] == (
        "worktree_head must match local_origin_main_sha"
    )
    assert exc_info.value.report["network_attempted"] is False


def test_freshness_proof_must_match_git_root(tmp_path: Path) -> None:
    git_root, _sha = _git_repo_with_origin_main(tmp_path)
    config_path = _write_config(tmp_path)

    with pytest.raises(GrokReviewError) as exc_info:
        run_grok_review(
            prompt="Review this plan.",
            config_path=config_path,
            require_freshness=True,
            freshness=_freshness(),
            git_root=git_root,
            now=NOW,
            env={},
        )

    assert exc_info.value.report["decision"] == "stale_freshness_proof"
    assert exc_info.value.report["reason"] == (
        "local_origin_main_sha must match git origin/main"
    )
    assert exc_info.value.report["network_attempted"] is False


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


def test_active_call_without_freshness_refuses_before_secret_lookup(
    tmp_path: Path,
) -> None:
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
    assert report["decision"] == "missing_freshness_proof"
    assert report["reason"] == "freshness proof required before Grok network call"
    assert report["freshness_required"] is True
    assert report["network_attempted"] is False


def test_dry_run_active_without_freshness_reports_refusal(
    tmp_path: Path,
) -> None:
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
        dry_run=True,
        now=NOW,
        env={},
    )

    assert report["ok"] is True
    assert report["decision"] == "dry_run_ready"
    assert report["would_call"] is False
    assert report["would_refuse_reason"] == "freshness proof required"
    assert report["freshness_required"] is True
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
        freshness=_freshness(),
        git_root=None,
        now=NOW,
        env={"GROK_API_BASE_URL": "https://example.invalid"},
    )

    assert report["ok"] is False
    assert report["decision"] == "missing_api_key"
    assert report["reason"] == "XAI_API_KEY"
    assert report["freshness_required"] is True
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


def test_cli_require_freshness_refuses_missing_proof(tmp_path: Path) -> None:
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
            "--require-freshness",
            "--json",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 2
    report = json.loads(completed.stdout)
    assert report["decision"] == "missing_freshness_proof"
    assert report["network_attempted"] is False


def test_cli_valid_freshness_proof_reaches_disabled_gate(tmp_path: Path) -> None:
    git_root, sha = _git_repo_with_origin_main(tmp_path)
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
            "--require-freshness",
            "--remote-main-sha",
            sha,
            "--local-origin-main-sha",
            sha,
            "--worktree-head",
            sha,
            "--pr-head-sha",
            OTHER_SHA,
            "--git-root",
            str(git_root),
            "--json",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 3
    report = json.loads(completed.stdout)
    assert report["decision"] == "grok_disabled"
    assert report["freshness"]["freshness_ok"] is True
    assert report["freshness"]["remote_main_sha"] == sha
    assert report["freshness"]["pr_head_sha"] == OTHER_SHA


def test_powershell_wrapper_forwards_freshness_proof(tmp_path: Path) -> None:
    git_root, sha = _git_repo_with_origin_main(tmp_path)
    config_path = _write_config(tmp_path)
    wrapper = Path(__file__).resolve().parents[2] / "tools" / "Invoke-GrokReview.ps1"
    env = {**os.environ, "PYTHON": sys.executable}

    completed = subprocess.run(
        [
            _powershell(),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(wrapper),
            "-Prompt",
            "Review this plan.",
            "-ConfigPath",
            str(config_path),
            "-RequireFreshness",
            "-RemoteMainSha",
            sha,
            "-LocalOriginMainSha",
            sha,
            "-WorktreeHead",
            sha,
            "-PrHeadSha",
            OTHER_SHA,
            "-GitRoot",
            str(git_root),
            "-Json",
        ],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    assert completed.returncode == 3
    report = json.loads(completed.stdout)
    assert report["decision"] == "grok_disabled"
    assert report["freshness"]["freshness_ok"] is True
    assert report["freshness"]["remote_main_sha"] == sha
    assert report["freshness"]["pr_head_sha"] == OTHER_SHA


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


def test_corrupt_config_json_refuses_with_clean_cli_report(tmp_path: Path) -> None:
    config_path = tmp_path / "grok_budget.json"
    config_path.write_text("{", encoding="utf-8")
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

    assert completed.returncode == 2
    assert completed.stderr == ""
    report = json.loads(completed.stdout)
    assert report["decision"] == "invalid_config"
    assert report["reason"] == "config JSON is malformed"
    assert report["network_attempted"] is False


def test_corrupt_budget_state_json_refuses_before_network(tmp_path: Path) -> None:
    config_path = _write_config(
        tmp_path,
        enabled=True,
        allow_network=True,
        max_calls_per_day=1,
    )
    state_path = tmp_path / "state.json"
    state_path.write_text("{", encoding="utf-8")

    with pytest.raises(GrokReviewError) as exc_info:
        run_grok_review(
            prompt="Review this plan.",
            config_path=config_path,
            state_path=state_path,
            now=NOW,
            env={
                "GROK_API_BASE_URL": "https://example.invalid",
                "XAI_API_KEY": "not-used",
            },
        )

    assert exc_info.value.report["decision"] == "invalid_state"
    assert exc_info.value.report["reason"] == "budget state JSON is malformed"
    assert exc_info.value.report["network_attempted"] is False


def test_invalid_budget_state_counters_refuse_before_network(tmp_path: Path) -> None:
    config_path = _write_config(
        tmp_path,
        enabled=True,
        allow_network=True,
        max_calls_per_day=1,
    )
    state_path = tmp_path / "state.json"
    state_path.write_text(
        json.dumps(
            {
                "date_utc": NOW.date().isoformat(),
                "total_calls": "not-an-int",
                "calls_by_model": {},
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(GrokReviewError) as exc_info:
        run_grok_review(
            prompt="Review this plan.",
            config_path=config_path,
            state_path=state_path,
            now=NOW,
            env={
                "GROK_API_BASE_URL": "https://example.invalid",
                "XAI_API_KEY": "not-used",
            },
        )

    assert exc_info.value.report["decision"] == "invalid_state"
    assert exc_info.value.report["reason"] == "budget state counters must be integers"
    assert exc_info.value.report["network_attempted"] is False

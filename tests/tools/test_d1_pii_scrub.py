# SPDX-License-Identifier: Apache-2.0
"""Hermetic tests for tools/d1_pii_scrub.py.

All fixtures are synthetic: a throwaway temp git repo with FAKE PII values
(1234567-8 / Test Owner / Test Business Oy). The real repo is never touched
and no real PII appears anywhere in this file.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from tools.d1_pii_scrub import (
    HISTORICAL_PLACEHOLDER,
    build_replacement_mapping,
    count_history_matches,
    detect_pii_fields,
    filter_repo_available,
    load_known_values,
    main,
    run_detect,
    run_dry_run,
    run_plan,
    write_replacement_file,
)

# Synthetic PII fixtures — NOT the operator's real values.
FAKE_Y_TUNNUS = "1234567-8"
FAKE_OWNER = "Test Owner"
FAKE_BUSINESS = "Test Business Oy"

SETTINGS_WITH_PII = (
    "profile: home\n"
    "facts:\n"
    f"  business_name: {FAKE_BUSINESS}\n"
    f"  owner: {FAKE_OWNER}\n"
    f"  y_tunnus: {FAKE_Y_TUNNUS}\n"
    "hivemind:\n"
    "  heartbeat_interval: 30\n"
)

SETTINGS_SCRUBBED = (
    "profile: home\n"
    "facts:\n"
    "  business_name: REDACTED_BUSINESS\n"
    "  owner: REDACTED_OWNER\n"
    "  y_tunnus: REDACTED_BUSINESS_ID\n"
)


def _git(args: list[str], cwd: Path) -> None:
    subprocess.run(
        ["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True
    )


def _make_repo(tmp_path: Path, settings_text: str) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(["init", "-b", "main"], repo)
    _git(["config", "user.email", "test@example.com"], repo)
    _git(["config", "user.name", "Test Runner"], repo)
    configs = repo / "configs"
    configs.mkdir()
    (configs / "settings.yaml").write_text(settings_text, encoding="utf-8")
    _git(["add", "."], repo)
    _git(["commit", "-m", "seed settings with synthetic pii"], repo)
    return repo


def _settings(repo: Path) -> Path:
    return repo / "configs" / "settings.yaml"


def test_detect_finds_present_fields(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path, SETTINGS_WITH_PII)
    present = detect_pii_fields(_settings(repo))
    assert present == {"y_tunnus": True, "owner": True, "business_name": True}


def test_detect_treats_redacted_as_absent(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path, SETTINGS_SCRUBBED)
    present = detect_pii_fields(_settings(repo))
    assert present == {"y_tunnus": False, "owner": False, "business_name": False}


def test_build_replacement_mapping_shape(tmp_path: Path) -> None:
    values = {
        "y_tunnus": FAKE_Y_TUNNUS,
        "owner": FAKE_OWNER,
        "business_name": FAKE_BUSINESS,
    }
    mapping = build_replacement_mapping(values)
    assert mapping == [
        (FAKE_Y_TUNNUS, "REDACTED_BUSINESS_ID"),
        (FAKE_OWNER, "REDACTED_OWNER"),
        (FAKE_BUSINESS, "REDACTED_BUSINESS"),
    ]


def test_build_replacement_mapping_skips_redacted() -> None:
    values = {"y_tunnus": "REDACTED_BUSINESS_ID", "owner": FAKE_OWNER}
    mapping = build_replacement_mapping(values)
    assert mapping == [(FAKE_OWNER, "REDACTED_OWNER")]


def test_write_replacement_file_format_and_location(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path, SETTINGS_WITH_PII)
    mapping = build_replacement_mapping(
        {
            "y_tunnus": FAKE_Y_TUNNUS,
            "owner": FAKE_OWNER,
            "business_name": FAKE_BUSINESS,
        }
    )
    out_dir = tmp_path / "temp_outside_repo"
    out_dir.mkdir()
    path = write_replacement_file(mapping, dest_dir=out_dir)
    try:
        # Built outside the repo working tree.
        assert repo not in path.parents
        contents = path.read_text(encoding="utf-8")
        assert f"{FAKE_Y_TUNNUS}==>REDACTED_BUSINESS_ID" in contents
        assert f"{FAKE_OWNER}==>REDACTED_OWNER" in contents
        assert f"{FAKE_BUSINESS}==>REDACTED_BUSINESS" in contents
    finally:
        path.unlink()


def test_count_history_matches_counts_commits(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path, SETTINGS_WITH_PII)
    assert count_history_matches(FAKE_Y_TUNNUS, repo) >= 1
    assert count_history_matches("value-never-committed-xyz", repo) == 0


def test_run_detect_reports_scrub_needed(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path, SETTINGS_WITH_PII)
    report = run_detect(_settings(repo), repo)
    assert report["scrub_needed"] is True
    assert report["fields_present"]["y_tunnus"] is True
    assert report["history_match_counts"]["owner"] >= 1


def test_run_detect_clean_when_scrubbed(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path, SETTINGS_SCRUBBED)
    report = run_detect(_settings(repo), repo)
    assert report["scrub_needed"] is False
    assert all(v == 0 for v in report["history_match_counts"].values())


def test_run_plan_reports_path_not_contents(tmp_path: Path, capsys) -> None:
    repo = _make_repo(tmp_path, SETTINGS_WITH_PII)
    report = run_plan(_settings(repo))
    try:
        assert report["mapping_count"] == 3
        assert set(report["placeholders"]) == {
            "REDACTED_BUSINESS_ID",
            "REDACTED_OWNER",
            "REDACTED_BUSINESS",
        }
        assert Path(report["replacement_file"]).exists()
    finally:
        Path(report["replacement_file"]).unlink(missing_ok=True)


def test_main_refuses_push(capsys) -> None:
    rc = main(["push"])
    assert rc == 2
    out = json.loads(capsys.readouterr().out)
    assert out["refused"] is True
    assert "force-with-lease" in out["operator_command"]


def test_main_refuses_force_push(capsys) -> None:
    rc = main(["force-push"])
    assert rc == 2
    out = json.loads(capsys.readouterr().out)
    assert out["refused"] is True


def test_main_detect_emits_json(tmp_path: Path, capsys) -> None:
    repo = _make_repo(tmp_path, SETTINGS_WITH_PII)
    rc = main(["detect", "--settings", str(_settings(repo)), "--repo", str(repo)])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["scrub_needed"] is True


@pytest.mark.skipif(
    not filter_repo_available(),
    reason="git-filter-repo CLI is not installed in this environment",
)
def test_dry_run_scrubs_to_zero_residual(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path, SETTINGS_WITH_PII)
    report = run_dry_run(_settings(repo), repo, run_smoke=False)
    assert report["passed"] is True
    assert report["residual_clean"] is True
    assert all(count == 0 for count in report["residual_match_counts"].values())
    assert report["mapping_count"] == 3


def _make_repo_redacted_head_dirty_history(tmp_path: Path) -> Path:
    """Repo whose HEAD is scrubbed but an earlier commit still holds PII.

    This is the case Codex's RCO flagged: detect must not report
    scrub_needed=False just because HEAD is clean.
    """
    repo = _make_repo(tmp_path, SETTINGS_WITH_PII)
    # Second commit redacts HEAD; the PII remains in the first commit.
    (repo / "configs" / "settings.yaml").write_text(
        SETTINGS_SCRUBBED, encoding="utf-8"
    )
    _git(["add", "."], repo)
    _git(["commit", "-m", "redact settings at HEAD"], repo)
    return repo


def test_load_known_values_parses_bare_and_mapping(tmp_path: Path) -> None:
    f = tmp_path / "known.txt"
    f.write_text(
        f"# prior values\n{FAKE_Y_TUNNUS}\n{FAKE_OWNER}==>CUSTOM_OWNER\n\n",
        encoding="utf-8",
    )
    mapping = load_known_values(f)
    assert mapping == [
        (FAKE_Y_TUNNUS, HISTORICAL_PLACEHOLDER),
        (FAKE_OWNER, "CUSTOM_OWNER"),
    ]


def test_detect_redacted_head_no_known_values_is_unverifiable(
    tmp_path: Path,
) -> None:
    repo = _make_repo_redacted_head_dirty_history(tmp_path)
    report = run_detect(_settings(repo), repo)
    # HEAD shows no PII and we supplied no prior values: the tool must NOT
    # claim history is clean — it had nothing to search for.
    assert report["fields_present"] == {
        "y_tunnus": False, "owner": False, "business_name": False,
    }
    assert report["head_redacted_history_unverifiable"] is True


def test_detect_redacted_head_with_known_values_finds_history(
    tmp_path: Path,
) -> None:
    repo = _make_repo_redacted_head_dirty_history(tmp_path)
    known = [
        (FAKE_Y_TUNNUS, HISTORICAL_PLACEHOLDER),
        (FAKE_OWNER, HISTORICAL_PLACEHOLDER),
        (FAKE_BUSINESS, HISTORICAL_PLACEHOLDER),
    ]
    report = run_detect(_settings(repo), repo, known_values=known)
    assert report["head_redacted_history_unverifiable"] is False
    # The PII is gone from HEAD but still in the first commit -> matches.
    assert any(c > 0 for c in report["known_history_match_counts"].values())
    assert report["scrub_needed"] is True


@pytest.mark.skipif(
    not filter_repo_available(),
    reason="git-filter-repo CLI is not installed in this environment",
)
def test_dry_run_with_known_values_scrubs_dirty_history(tmp_path: Path) -> None:
    repo = _make_repo_redacted_head_dirty_history(tmp_path)
    known = [
        (FAKE_Y_TUNNUS, HISTORICAL_PLACEHOLDER),
        (FAKE_OWNER, HISTORICAL_PLACEHOLDER),
        (FAKE_BUSINESS, HISTORICAL_PLACEHOLDER),
    ]
    report = run_dry_run(
        _settings(repo), repo, run_smoke=False, known_values=known
    )
    assert report["residual_clean"] is True
    assert all(c == 0 for c in report["residual_match_counts"].values())

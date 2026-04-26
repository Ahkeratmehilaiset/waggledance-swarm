# SPDX-License-Identifier: Apache-2.0
"""Tests for tools/wd_pr_prepare.py.

Hermetic — never invokes real git/gh/pytest. All subprocess calls are
monkeypatched via wd.run.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import wd_pr_prepare as wd  # noqa: E402


# ════════════════════ argparse ═════════════════════════════════════

def test_argparse_dry_run_for_known_branch():
    args = wd.build_parser().parse_args(
        ["--branch", "phase8.5/vector-chaos", "--dry-run"]
    )
    assert args.branch == "phase8.5/vector-chaos"
    assert args.dry_run is True
    assert args.execute is False


def test_argparse_execute_flag():
    args = wd.build_parser().parse_args(
        ["--branch", "phase9/autonomy-fabric", "--execute"]
    )
    assert args.execute is True
    assert args.dry_run is False


def test_argparse_unknown_branch_rejected():
    with pytest.raises(SystemExit):
        wd.build_parser().parse_args(
            ["--branch", "phase99/bogus", "--dry-run"]
        )


def test_argparse_dry_run_and_execute_mutually_exclusive():
    with pytest.raises(SystemExit):
        wd.build_parser().parse_args([
            "--branch", "phase9/autonomy-fabric",
            "--dry-run", "--execute",
        ])


def test_argparse_requires_branch():
    with pytest.raises(SystemExit):
        wd.build_parser().parse_args(["--dry-run"])


def test_argparse_requires_mode():
    with pytest.raises(SystemExit):
        wd.build_parser().parse_args([
            "--branch", "phase9/autonomy-fabric",
        ])


# ════════════════════ BRANCHES table ═══════════════════════════════

def test_all_six_branches_in_table():
    expected = {
        "phase8.5/vector-chaos",
        "phase8.5/curiosity-organ",
        "phase8.5/self-model-layer",
        "phase8.5/dream-curriculum",
        "phase8.5/hive-proposes",
        "phase9/autonomy-fabric",
    }
    assert set(wd.BRANCHES.keys()) == expected


def test_each_branch_has_state_path_and_tests_and_title():
    for branch, spec in wd.BRANCHES.items():
        assert spec.test_globs, f"{branch} missing test_globs"
        assert spec.state_path.endswith(".json"), \
            f"{branch} state_path not .json"
        assert spec.state_path.startswith("docs/runs/"), \
            f"{branch} state_path not under docs/runs/"
        assert spec.title, f"{branch} missing title"


def test_phase9_state_path_matches_known_filename():
    assert (wd.BRANCHES["phase9/autonomy-fabric"].state_path
            == "docs/runs/phase9_autonomy_fabric_state.json")


# ════════════════════ load_state ═══════════════════════════════════

def test_load_state_existing_file(tmp_path):
    p = tmp_path / "state.json"
    p.write_text(
        '{"completed_phases": ["A", "B"], "lifecycle_status": "review"}',
        encoding="utf-8",
    )
    state = wd.load_state(p)
    assert state["completed_phases"] == ["A", "B"]
    assert state["lifecycle_status"] == "review"


def test_load_state_missing_file(tmp_path):
    p = tmp_path / "missing.json"
    assert wd.load_state(p) == {}


def test_load_state_malformed_json(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("not json {{{", encoding="utf-8")
    assert wd.load_state(p) == {}


def test_load_state_empty_file(tmp_path):
    p = tmp_path / "empty.json"
    p.write_text("", encoding="utf-8")
    assert wd.load_state(p) == {}


# ════════════════════ subprocess wrappers (mocked) ═════════════════

def _stub(returncode: int, stdout: str = "", stderr: str = ""):
    cp = types.SimpleNamespace()
    cp.returncode = returncode
    cp.stdout = stdout
    cp.stderr = stderr
    return cp


def test_verify_branch_exists_true(monkeypatch):
    monkeypatch.setattr(wd, "run",
                          lambda cmd, check=False: _stub(0, ""))
    assert wd.verify_branch_exists("phase9/autonomy-fabric") is True


def test_verify_branch_exists_false(monkeypatch):
    monkeypatch.setattr(wd, "run",
                          lambda cmd, check=False: _stub(128, "", "no ref"))
    assert wd.verify_branch_exists("phase99/bogus") is False


def test_is_rebased_on_base_true(monkeypatch):
    monkeypatch.setattr(wd, "run",
                          lambda cmd, check=False: _stub(0))
    assert wd.is_rebased_on_base("origin/main",
                                    "phase9/autonomy-fabric") is True


def test_is_rebased_on_base_false(monkeypatch):
    monkeypatch.setattr(wd, "run",
                          lambda cmd, check=False: _stub(1))
    assert wd.is_rebased_on_base("origin/main",
                                    "phase8.5/vector-chaos") is False


def test_has_uncommitted_changes_true(monkeypatch):
    monkeypatch.setattr(wd, "run",
                          lambda cmd, check=False:
                              _stub(0, " M tools/foo.py\n"))
    assert wd.has_uncommitted_changes() is True


def test_has_uncommitted_changes_false(monkeypatch):
    monkeypatch.setattr(wd, "run",
                          lambda cmd, check=False: _stub(0, ""))
    assert wd.has_uncommitted_changes() is False


def test_current_branch(monkeypatch):
    monkeypatch.setattr(
        wd, "run",
        lambda cmd, check=False: _stub(0, "phase9/autonomy-fabric\n"),
    )
    assert wd.current_branch() == "phase9/autonomy-fabric"


# ════════════════════ generate_pr_body ═════════════════════════════

def _patch_git_for_body(monkeypatch,
                          *,
                          short_sha_value: str = "abc1234",
                          diff_stat: str = " 5 files changed, 100 insertions(+)",
                          name_only: str = ""):
    def fake_run(cmd, check=False):
        joined = " ".join(cmd)
        if "rev-parse" in joined and "--short" in joined:
            return _stub(0, short_sha_value)
        if "diff" in joined and "--stat" in joined:
            return _stub(0, diff_stat)
        if "diff" in joined and "--name-only" in joined:
            return _stub(0, name_only)
        return _stub(0, "")
    monkeypatch.setattr(wd, "run", fake_run)


def test_pr_body_contains_required_sections(monkeypatch):
    _patch_git_for_body(
        monkeypatch,
        name_only=("waggledance/core/autonomy/governor.py\n"
                    "tests/test_phase9_governor.py\n"),
    )
    spec = wd.BRANCHES["phase9/autonomy-fabric"]
    state = {
        "completed_phases": ["F.kernel_state", "G.cognition_ir"],
        "lifecycle_status": "review",
        "latest_green_commit_hash": "deadbee",
    }
    body = wd.generate_pr_body(spec, "origin/main",
                                  "657 passed in 7s", state)
    # Mandatory section headers
    for h in (
        "## Summary",
        "## Test results",
        "## Files changed",
        "## Crown-jewel paths affected",
        "## State machine: lifecycle status",
        "## Branch base",
        "## Latest green commit",
    ):
        assert h in body, f"missing section header: {h}"
    # Substantive content
    assert "F.kernel_state" in body
    assert "G.cognition_ir" in body
    assert "657 passed in 7s" in body
    assert "5 files changed" in body
    assert "waggledance/core/autonomy" in body
    assert "review" in body
    assert "origin/main" in body
    assert "abc1234" in body
    assert "deadbee" in body


def test_pr_body_handles_empty_state(monkeypatch):
    _patch_git_for_body(monkeypatch)
    spec = wd.BRANCHES["phase8.5/vector-chaos"]
    body = wd.generate_pr_body(spec, "origin/main", "26 passed", {})
    assert "(state file did not list" in body
    assert "(not recorded)" in body


def test_pr_body_caps_completed_phases_at_20(monkeypatch):
    _patch_git_for_body(monkeypatch)
    spec = wd.BRANCHES["phase9/autonomy-fabric"]
    state = {
        "completed_phases": [f"phase_{i}" for i in range(30)],
    }
    body = wd.generate_pr_body(spec, "origin/main", "x", state)
    assert "phase_0" in body
    assert "phase_19" in body
    # phase_25 is past the cap
    assert "phase_25" not in body
    assert "and 10 more" in body


def test_pr_body_uses_completed_components_fallback(monkeypatch):
    """If the state file uses `completed_components` instead of
    `completed_phases`, fall back gracefully."""
    _patch_git_for_body(monkeypatch)
    spec = wd.BRANCHES["phase8.5/curiosity-organ"]
    state = {"completed_components": ["miner", "summarizer"]}
    body = wd.generate_pr_body(spec, "origin/main", "x", state)
    assert "miner" in body
    assert "summarizer" in body


def test_pr_body_session_status_fallback(monkeypatch):
    _patch_git_for_body(monkeypatch)
    spec = wd.BRANCHES["phase8.5/dream-curriculum"]
    state = {"session_status": "validation_complete"}
    body = wd.generate_pr_body(spec, "origin/main", "x", state)
    assert "validation_complete" in body


# ════════════════════ crown_jewel_paths_affected ═══════════════════

def test_crown_jewel_paths_extracted(monkeypatch):
    name_only = "\n".join([
        "waggledance/core/autonomy/governor.py",
        "waggledance/core/ir/cognition_ir.py",
        "waggledance/core/learning/case_builder.py",
        "tests/test_phase9_governor.py",
        "docs/runs/phase9_master_session_report.md",
        "tools/wd_kernel_tick.py",
    ])

    def fake_run(cmd, check=False):
        joined = " ".join(cmd)
        if "diff" in joined and "--name-only" in joined:
            return _stub(0, name_only)
        return _stub(0, "")
    monkeypatch.setattr(wd, "run", fake_run)

    paths = wd.crown_jewel_paths_affected("origin/main",
                                            "phase9/autonomy-fabric")
    # Expected: only crown-jewel package roots
    assert "waggledance/core/autonomy" in paths
    assert "waggledance/core/ir" in paths
    assert "waggledance/core/learning" in paths
    # tests/ and tools/ files must not appear in crown-jewel list
    for p in paths:
        assert p.startswith("waggledance/core/"), \
            f"non-crown-jewel path in result: {p}"


def test_crown_jewel_paths_empty_when_no_diff(monkeypatch):
    monkeypatch.setattr(
        wd, "run",
        lambda cmd, check=False: _stub(0, ""),
    )
    paths = wd.crown_jewel_paths_affected("origin/main",
                                            "phase9/autonomy-fabric")
    assert paths == []


# ════════════════════ diff_stat_tail ═══════════════════════════════

def test_diff_stat_tail_returns_last_n_lines(monkeypatch):
    diff = "\n".join([f"line_{i}" for i in range(10)])
    monkeypatch.setattr(
        wd, "run",
        lambda cmd, check=False: _stub(0, diff),
    )
    tail = wd.diff_stat_tail("origin/main", "phase9/autonomy-fabric", n=3)
    assert tail == "line_7\nline_8\nline_9"


def test_diff_stat_tail_empty_diff(monkeypatch):
    monkeypatch.setattr(
        wd, "run",
        lambda cmd, check=False: _stub(0, ""),
    )
    assert wd.diff_stat_tail("a", "b") == "(no diff)"

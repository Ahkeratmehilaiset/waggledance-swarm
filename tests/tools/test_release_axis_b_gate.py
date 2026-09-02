# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

import tools.run_release_axis_b_gate as gate
import tools.verify_release_soak_evidence as verifier
from tools.release_axis_b_attestation import (
    AXIS_B_EXPECTED_SOURCES,
    _source_digest as _helper_source_digest,
    evaluate_axis_b_attestation,
)
from tools.run_release_axis_b_gate import build_axis_b_report, main


ROOT = Path(__file__).resolve().parents[2]
NOT_IN_REPO = "0123456789abcdef0123456789abcdef01234567"


def _git(root, *args, check=True):
    return subprocess.run(
        [
            "git",
            "-c",
            "user.name=axis-v2-test",
            "-c",
            "user.email=axis-v2-test@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "-c",
            "core.autocrlf=false",
            "-c",
            "core.longpaths=true",
            "-C",
            str(root),
            *args,
        ],
        check=check,
        capture_output=True,
        text=True,
    )


def _init_axis_b_repo(root) -> str:
    """A throwaway repository holding the real Axis B corpus and config."""
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-q")
    _git(root, "config", "core.autocrlf", "false")
    for rel in AXIS_B_EXPECTED_SOURCES:
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / rel).read_bytes().replace(b"\r\n", b"\n"))
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "axis b sources")
    return _git(root, "rev-parse", "HEAD").stdout.strip()


def _commit_all(root, message="more") -> str:
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", message)
    return _git(root, "rev-parse", "HEAD").stdout.strip()


def _make_directory_link(link, target) -> bool:
    if os.name == "nt":
        completed = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True,
        )
        if completed.returncode == 0:
            return True
    try:
        os.symlink(target, link, target_is_directory=True)
    except (OSError, NotImplementedError):
        return False
    return True


@pytest.fixture
def source_repo(tmp_path):
    root = tmp_path / "repo"
    return root, _init_axis_b_repo(root)


def _cli(tmp_path, root, stamp, name="axis_b.json"):
    output = tmp_path / name
    rc = main(["--output", str(output), "--source-commit", stamp], root=root)
    return rc, output


# --- evaluation ------------------------------------------------------------


def test_axis_b_report_passes_hex_aligned_oracle_gate() -> None:
    report = build_axis_b_report()

    assert report["schema_version"] == "waggledance.axis_b_hex_eval.v1"
    assert report["result"] == "pass"
    assert report["blockers"] == []
    assert report["corpus"]["oracle_dir"] == "tests/oracle_hex"
    assert report["corpus"]["files"] == 7
    assert report["corpus"]["total_positive"] == 105
    assert report["corpus"]["total_negative"] == 35
    assert report["quality"] >= report["thresholds"]["quality_floor"]
    assert report["quality"] > (
        report["thresholds"]["mismatched_baseline_quality"]
        + report["thresholds"]["minimum_baseline_delta"]
    )
    assert all(
        row["file_score"] >= report["thresholds"]["per_cell_quality_floor"]
        for row in report["per_file"]
    )
    assert all(row["neg_correct"] == row["neg_total"] for row in report["per_file"])


def test_planted_oracle_dir_cannot_pass(tmp_path) -> None:
    """A byte-identical copy outside ROOT/tests/oracle_hex is not canonical."""
    planted = tmp_path / "planted_oracle"
    shutil.copytree(ROOT / "tests" / "oracle_hex", planted)

    report = build_axis_b_report(oracle_dir=planted)

    assert report["result"] == "blocked"
    assert "oracle_dir_noncanonical" in report["blockers"]
    assert report["corpus"]["oracle_dir"] == "noncanonical"
    assert str(tmp_path) not in json.dumps(report)


def test_planted_hex_config_cannot_pass(tmp_path) -> None:
    planted = tmp_path / "planted_hex_cells.yaml"
    shutil.copy(ROOT / "configs" / "hex_cells.yaml", planted)

    report = build_axis_b_report(hex_config=planted)

    assert report["result"] == "blocked"
    assert "hex_config_noncanonical" in report["blockers"]
    assert str(tmp_path) not in json.dumps(report)


def test_missing_input_paths_block(tmp_path) -> None:
    blockers, _oracle, _config = gate.canonical_input_blockers(
        ROOT, tmp_path / "absent_oracle", tmp_path / "absent.yaml"
    )

    assert blockers == ["oracle_dir_unresolvable", "hex_config_unresolvable"]


def test_relative_inputs_resolve_against_root_not_cwd(
    tmp_path, monkeypatch, source_repo
) -> None:
    """A planted cwd tree is ignored: defaults anchor to the repository ROOT."""
    root, _head = source_repo
    plant = tmp_path / "cwd_plant"
    (plant / "tests" / "oracle_hex").mkdir(parents=True)
    (plant / "configs").mkdir()
    shutil.copy(
        ROOT / "tests" / "oracle_hex" / "hub.yaml",
        plant / "tests" / "oracle_hex" / "hub.yaml",
    )
    shutil.copy(ROOT / "configs" / "hex_cells.yaml", plant / "configs")
    monkeypatch.chdir(plant)

    report = build_axis_b_report(root=root)

    assert report["result"] == "pass"
    assert report["corpus"]["files"] == 7
    assert report["corpus"]["oracle_dir"] == "tests/oracle_hex"


def test_canonical_oracle_dir_planted_as_link_is_rejected(tmp_path, source_repo) -> None:
    """Resolve-equality alone would follow a junction; the link check runs first."""
    root, _head = source_repo
    canonical = root / "tests" / "oracle_hex"
    moved = tmp_path / "moved_oracle"
    shutil.move(str(canonical), str(moved))
    if not _make_directory_link(canonical, moved):
        pytest.skip("directory links unavailable in this environment")

    report = build_axis_b_report(root=root)

    assert report["result"] == "blocked"
    assert "oracle_dir_canonical_link_or_reparse" in report["blockers"]
    assert report["corpus"]["oracle_dir"] == "noncanonical"


def test_canonical_hex_config_missing_blocks(tmp_path, source_repo) -> None:
    root, _head = source_repo
    (root / "configs" / "hex_cells.yaml").unlink()

    blockers, _oracle, _config = gate.canonical_input_blockers(
        root, gate.DEFAULT_ORACLE_DIR, gate.DEFAULT_HEX_CONFIG
    )

    assert blockers == ["hex_config_canonical_missing"]


# --- CLI: source-subject binding ------------------------------------------


def test_axis_b_cli_writes_bound_machine_readable_evidence(tmp_path, source_repo) -> None:
    root, head = source_repo

    rc, output = _cli(tmp_path, root, head)

    assert rc == 0
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["result"] == "pass"
    assert report["blockers"] == []
    assert report["benchmark_id"] == "v3.12-axis-b-hex-aligned-eval"
    assert report["source_commit"] == head
    assert report["generated_at"].endswith("Z")
    assert report["source_files"] == list(AXIS_B_EXPECTED_SOURCES)
    assert report["source_hashes"] == {
        rel: _helper_source_digest(root / rel) for rel in AXIS_B_EXPECTED_SOURCES
    }
    assert report["corpus"]["oracle_dir"] == "tests/oracle_hex"
    assert str(tmp_path) not in output.read_text(encoding="utf-8")
    # End to end: the attestation helper binds the producer artifact.
    assert evaluate_axis_b_attestation(output, root, head) == []


def test_cli_requires_source_commit(tmp_path, source_repo) -> None:
    root, _head = source_repo
    output = tmp_path / "axis_b.json"

    with pytest.raises(SystemExit) as excinfo:
        main(["--output", str(output)], root=root)

    assert excinfo.value.code == 2
    assert not output.exists()


@pytest.mark.parametrize(
    "stamp",
    ["HEAD", "main", NOT_IN_REPO.upper(), NOT_IN_REPO[:39], NOT_IN_REPO + "0"],
    ids=["head-word", "branch", "uppercase", "short", "long"],
)
def test_cli_rejects_malformed_source_commit(tmp_path, source_repo, stamp) -> None:
    root, _head = source_repo
    output = tmp_path / "axis_b.json"

    with pytest.raises(SystemExit) as excinfo:
        main(["--output", str(output), "--source-commit", stamp], root=root)

    assert excinfo.value.code == 2
    assert not output.exists()


def test_cli_stamp_not_head_aborts_without_artifact(tmp_path, source_repo, capsys) -> None:
    root, _head = source_repo

    rc, output = _cli(tmp_path, root, NOT_IN_REPO)

    assert rc == 2
    assert not output.exists()
    captured = capsys.readouterr()
    assert "source_commit_not_head" in captured.err
    assert str(tmp_path) not in captured.err


def test_cli_dirty_worktree_aborts_without_artifact(tmp_path, source_repo, capsys) -> None:
    root, head = source_repo
    (root / "stray.txt").write_text("stray\n", encoding="utf-8")

    rc, output = _cli(tmp_path, root, head)

    assert rc == 2
    assert not output.exists()
    assert "worktree_dirty" in capsys.readouterr().err


def test_cli_tamper_hidden_from_status_aborts(tmp_path, source_repo, capsys) -> None:
    root, head = source_repo
    rel = "tests/oracle_hex/hub.yaml"
    _git(root, "update-index", "--assume-unchanged", rel)
    with (root / rel).open("ab") as handle:
        handle.write(b"# tampered\n")
    assert verifier.source_subject_preflight(root, head) == []

    rc, output = _cli(tmp_path, root, head)

    assert rc == 2
    assert not output.exists()
    assert "source_worktree_blob_mismatch" in capsys.readouterr().err


def test_cli_mid_run_head_change_aborts_without_artifact(
    tmp_path, source_repo, monkeypatch, capsys
) -> None:
    root, head = source_repo
    original = gate.build_axis_b_report

    def _evaluate_then_commit(**kwargs):
        report = original(**kwargs)
        (root / "late.txt").write_text("late\n", encoding="utf-8")
        _commit_all(root, "late")
        return report

    monkeypatch.setattr(gate, "build_axis_b_report", _evaluate_then_commit)

    rc, output = _cli(tmp_path, root, head)

    assert rc == 2
    assert not output.exists()
    assert "source_commit_not_head" in capsys.readouterr().err


def test_cli_mid_run_source_edit_aborts_without_artifact(
    tmp_path, source_repo, monkeypatch, capsys
) -> None:
    root, head = source_repo
    original = gate.build_axis_b_report

    def _evaluate_then_edit(**kwargs):
        report = original(**kwargs)
        with (root / "tests" / "oracle_hex" / "hub.yaml").open("ab") as handle:
            handle.write(b"# late edit\n")
        return report

    monkeypatch.setattr(gate, "build_axis_b_report", _evaluate_then_edit)

    rc, output = _cli(tmp_path, root, head)

    assert rc == 2
    assert not output.exists()
    assert "worktree_dirty" in capsys.readouterr().err


def test_cli_git_unavailable_aborts(tmp_path, source_repo, monkeypatch, capsys) -> None:
    root, head = source_repo
    monkeypatch.setattr(verifier, "GIT_EXECUTABLE", "git-axis-v2-definitely-missing")

    rc, output = _cli(tmp_path, root, head)

    assert rc == 2
    assert not output.exists()
    assert "git_unavailable" in capsys.readouterr().err


def test_cli_git_environment_cannot_retarget(tmp_path, source_repo, monkeypatch) -> None:
    root, head = source_repo
    other = tmp_path / "other"
    other.mkdir()
    _git(other, "init", "-q")
    (other / "README.md").write_text("other\n", encoding="utf-8")
    other_head = _commit_all(other, "other")
    assert other_head != head
    monkeypatch.setenv("GIT_DIR", str(other / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(other))

    rc, output = _cli(tmp_path, root, head)

    assert rc == 0
    assert json.loads(output.read_text(encoding="utf-8"))["source_commit"] == head


def test_cli_planted_oracle_dir_writes_blocked_artifact(tmp_path, source_repo) -> None:
    root, head = source_repo
    planted = tmp_path / "planted_oracle"
    shutil.copytree(root / "tests" / "oracle_hex", planted)
    output = tmp_path / "axis_b.json"

    rc = main(
        [
            "--output",
            str(output),
            "--oracle-dir",
            str(planted),
            "--source-commit",
            head,
        ],
        root=root,
    )

    assert rc == 1
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["result"] == "blocked"
    assert "oracle_dir_noncanonical" in report["blockers"]
    assert evaluate_axis_b_attestation(output, root, head) == [
        "axis_b_not_pass",
        "axis_b_corpus_mismatch",
    ]


def test_cli_relative_output_is_anchored_to_root(tmp_path, source_repo, monkeypatch) -> None:
    root, head = source_repo
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    rc = main(
        ["--output", "artifact/axis_b.json", "--source-commit", head], root=root
    )

    assert rc == 0
    assert (root / "artifact" / "axis_b.json").is_file()
    assert not (elsewhere / "artifact").exists()


def test_cli_exposes_no_root_override(tmp_path) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["--root", str(tmp_path), "--source-commit", NOT_IN_REPO])

    assert excinfo.value.code == 2

# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

import functools
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest
import yaml

import tools.run_release_axis_b_gate as gate
import tools.verify_release_soak_evidence as verifier
from tools.release_axis_b_attestation import (
    AXIS_B_EXPECTED_SOURCES,
    _source_digest as _helper_source_digest,
    evaluate_axis_b_attestation,
)
from tools.run_r21_oracle_ab_proof import load_oracle_corpus
from tools.run_release_axis_b_gate import build_axis_b_report, main
from waggledance.application.services.hex_topology_registry import HexTopologyRegistry


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


# --- CLI: the scorer consumes the bound blobs, never a worktree re-read ---


HUB_REL = "tests/oracle_hex/hub.yaml"


def _hub_row(report):
    return next(row for row in report["per_file"] if row["cell"] == "hub")


def _count_preserving_rewrite(clean: bytes) -> bytes:
    """Swap the last hub positive for a hub negative: same 15/5 shape, one
    utterance that now routes to another cell."""
    text = clean.decode("utf-8")
    assert '"needs central triage"' in text
    return text.replace(
        '"needs central triage"', '"hive temperature alert"', 1
    ).encode("utf-8")


def test_cli_scores_the_bound_blobs_not_a_reverted_worktree_rewrite(
    tmp_path, source_repo, monkeypatch
) -> None:
    """claude-rco-1 finding on PR #1671 (2026-09-03): a corpus rewrite that
    lands after the first preflight and is reverted before the second one
    must not reach the scorer, and the stamped hashes must describe the
    bytes that were actually scored."""
    root, head = source_repo
    hub = root / HUB_REL
    clean_bytes = hub.read_bytes()
    rewritten = _count_preserving_rewrite(clean_bytes)
    # The rewrite is effective whenever the worktree itself is scored.
    hub.write_bytes(rewritten)
    try:
        direct = build_axis_b_report(root=root)
    finally:
        hub.write_bytes(clean_bytes)
    assert direct["corpus"]["total_positive"] == 105
    assert _hub_row(direct)["pos_correct"] == _hub_row(direct)["pos_total"] - 1
    clean = build_axis_b_report(root=root)
    assert _hub_row(clean)["pos_correct"] == _hub_row(clean)["pos_total"]

    original = gate.build_axis_b_report
    evaluation_roots: list[Path] = []
    copied: dict[str, bytes] = {}

    def _rewrite_worktree_during_scoring(**kwargs):
        evaluation_root = Path(kwargs["root"])
        evaluation_roots.append(evaluation_root)
        hub.write_bytes(rewritten)
        try:
            for rel in AXIS_B_EXPECTED_SOURCES:
                copied[rel] = (evaluation_root / rel).read_bytes()
            return original(**kwargs)
        finally:
            hub.write_bytes(clean_bytes)

    monkeypatch.setattr(gate, "build_axis_b_report", _rewrite_worktree_during_scoring)

    rc, output = _cli(tmp_path, root, head)

    assert rc == 0
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["result"] == "pass"
    assert report["blockers"] == []
    assert _hub_row(report)["pos_correct"] == _hub_row(report)["pos_total"]
    assert report["quality"] == clean["quality"]
    assert report["per_file"] == clean["per_file"]
    assert report["source_hashes"] == {
        rel: _helper_source_digest(root / rel) for rel in AXIS_B_EXPECTED_SOURCES
    }
    assert evaluate_axis_b_attestation(output, root, head) == []
    # The scorer saw the committed bytes while the worktree was rewritten...
    assert copied[HUB_REL] == clean_bytes
    assert copied == {rel: (root / rel).read_bytes() for rel in AXIS_B_EXPECTED_SOURCES}
    # ...from one private copy outside ROOT that is gone afterwards.
    assert len(evaluation_roots) == 1
    evaluation_root = evaluation_roots[0]
    assert evaluation_root != root
    assert not evaluation_root.resolve().is_relative_to(root.resolve())
    assert not evaluation_root.exists()
    assert str(tmp_path) not in output.read_text(encoding="utf-8")


def test_materialize_source_subject_copies_committed_blobs_not_worktree(
    tmp_path, source_repo
) -> None:
    root, head = source_repo
    destination = tmp_path / "copy"
    committed = (root / HUB_REL).read_bytes()
    _git(root, "update-index", "--assume-unchanged", HUB_REL)
    (root / HUB_REL).write_bytes(committed + b"# tampered\n")

    materialized = gate.materialize_source_subject(
        root, head, AXIS_B_EXPECTED_SOURCES, destination
    )

    assert materialized.blockers == []
    assert set(materialized.digests) == set(AXIS_B_EXPECTED_SOURCES)
    assert set(materialized.contents) == set(AXIS_B_EXPECTED_SOURCES)
    assert materialized.contents[HUB_REL] == committed
    assert (destination / HUB_REL).read_bytes() == committed
    assert materialized.digests[HUB_REL] == verifier.lf_digest(committed)
    for rel in AXIS_B_EXPECTED_SOURCES:
        assert materialized.digests[rel] == verifier.tracked_blob_digest(root, head, rel)[0]
    recheck = gate.subject_snapshot_digests(destination, AXIS_B_EXPECTED_SOURCES)
    assert recheck.blockers == []
    assert recheck.digests == materialized.digests

    unknown = gate.materialize_source_subject(
        root, NOT_IN_REPO, AXIS_B_EXPECTED_SOURCES, tmp_path / "unknown"
    )
    assert unknown.digests == {}
    assert unknown.blockers == ["git_ls_tree_failed"]
    assert not (tmp_path / "unknown").exists()
    malformed = gate.materialize_source_subject(
        root, "HEAD", AXIS_B_EXPECTED_SOURCES, tmp_path / "malformed"
    )
    assert malformed.blockers == ["source_commit_invalid"]


@pytest.mark.parametrize(
    "entry",
    ["../escape.yaml", "/rooted.yaml", "tests/../../escape.yaml", ""],
    ids=["parent", "rooted", "nested-parent", "empty"],
)
def test_materialize_source_subject_refuses_unconfined_entries(
    tmp_path, source_repo, entry
) -> None:
    root, head = source_repo

    binding = gate.materialize_source_subject(
        root, head, (HUB_REL, entry), tmp_path / "copy"
    )

    assert binding.digests == {}
    assert binding.blockers == ["source_entry_not_confined"]
    assert not list(tmp_path.rglob("escape.yaml"))
    assert not list(tmp_path.rglob("rooted.yaml"))


def test_cli_private_copy_edited_during_scoring_aborts(
    tmp_path, source_repo, monkeypatch, capsys
) -> None:
    root, head = source_repo
    original = gate.build_axis_b_report

    def _score_then_edit_copy(**kwargs):
        report = original(**kwargs)
        with (Path(kwargs["root"]) / HUB_REL).open("ab") as handle:
            handle.write(b"# late edit\n")
        return report

    monkeypatch.setattr(gate, "build_axis_b_report", _score_then_edit_copy)

    rc, output = _cli(tmp_path, root, head)

    assert rc == 2
    assert not output.exists()
    assert "subject_snapshot_changed" in capsys.readouterr().err


def test_cli_private_copy_digest_mismatch_aborts(
    tmp_path, source_repo, monkeypatch, capsys
) -> None:
    root, head = source_repo
    original = gate.materialize_source_subject

    def _copy_then_misreport(*args, **kwargs):
        materialized = original(*args, **kwargs)
        digests = dict(materialized.digests)
        digests[HUB_REL] = "sha256:" + "0" * 64
        return gate.MaterializedSubject(digests, [], [], materialized.contents)

    monkeypatch.setattr(gate, "materialize_source_subject", _copy_then_misreport)

    rc, output = _cli(tmp_path, root, head)

    assert rc == 2
    assert not output.exists()
    assert "subject_snapshot_mismatch" in capsys.readouterr().err


def test_cli_private_copy_inside_root_is_refused(
    tmp_path, source_repo, monkeypatch, capsys
) -> None:
    root, head = source_repo
    monkeypatch.setattr(
        gate.tempfile,
        "TemporaryDirectory",
        functools.partial(tempfile.TemporaryDirectory, dir=root),
    )

    rc, output = _cli(tmp_path, root, head)

    assert rc == 2
    assert not output.exists()
    assert "subject_snapshot_inside_root" in capsys.readouterr().err
    assert verifier.worktree_porcelain(root) == b""


def test_cli_noncanonical_inputs_are_scored_as_given_and_blocked(
    tmp_path, source_repo, monkeypatch
) -> None:
    """The private copy is only used for canonical inputs; a planted corpus
    is still scored where it was asked for and can only be ``blocked``."""
    root, head = source_repo
    planted = tmp_path / "planted_oracle"
    shutil.copytree(root / "tests" / "oracle_hex", planted)
    output = tmp_path / "axis_b.json"
    original = gate.build_axis_b_report
    seen: list[dict] = []

    def _record(**kwargs):
        seen.append(dict(kwargs))
        return original(**kwargs)

    monkeypatch.setattr(gate, "build_axis_b_report", _record)

    rc = main(
        ["--output", str(output), "--oracle-dir", str(planted), "--source-commit", head],
        root=root,
    )

    assert rc == 1
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["result"] == "blocked"
    assert "oracle_dir_noncanonical" in report["blockers"]
    assert seen == [{"oracle_dir": planted, "hex_config": gate.DEFAULT_HEX_CONFIG, "root": root}]


# --- the scored objects are the verified bytes, not a disk re-read ---------


def test_parse_oracle_documents_matches_production_loader() -> None:
    """Runtime equivalence lock: the in-memory parse is the production loader."""
    corpus_dir = ROOT / "tests" / "oracle_hex"
    documents = {path.name: path.read_bytes() for path in corpus_dir.glob("*.yaml")}
    assert len(documents) == 7

    assert gate.parse_oracle_documents(documents) == load_oracle_corpus(corpus_dir)

    # The loader's skip rules are mirrored: underscore names, documents
    # without a cell, and non-mapping documents are all ignored.
    documents["_off_domain.yaml"] = b"cell: hub\npositive: [x]\n"
    documents["nocell.yaml"] = b"solver: x\n"
    documents["scalar.yaml"] = b"just a string\n"
    documents["notes.txt"] = b"cell: hub\n"
    assert gate.parse_oracle_documents(documents) == load_oracle_corpus(corpus_dir)


def test_expected_hex_cells_match_production_registry() -> None:
    """Runtime equivalence lock: the derived cells are the registry's cells, in order."""
    config = ROOT / "configs" / "hex_cells.yaml"
    registry = HexTopologyRegistry(config_path=str(config), agents=[])

    expected = gate.expected_hex_cells(config.read_bytes())

    assert len(expected) == 7
    assert list(registry.cells.items()) == list(expected.items())
    assert gate.expected_hex_cells(b"just a string\n") == {}
    assert gate.expected_hex_cells(b"cells:\n  - coord: {q: 1, r: 1}\n") == {}


def test_cli_scores_the_in_memory_parse_of_the_bound_bytes(
    tmp_path, source_repo, monkeypatch
) -> None:
    """The object handed to the scorer IS the in-memory parse of the bound
    bytes, not the loader's read of the private copy."""
    root, head = source_repo
    parsed: list = []
    scored: list = []
    original_parse = gate.parse_oracle_documents
    original_quality_arm = gate.quality_arm

    def _record_parse(documents):
        result = original_parse(documents)
        parsed.append(result)
        return result

    def _record_quality_arm(oracles, route_fn):
        scored.append(oracles)
        return original_quality_arm(oracles, route_fn)

    monkeypatch.setattr(gate, "parse_oracle_documents", _record_parse)
    monkeypatch.setattr(gate, "quality_arm", _record_quality_arm)

    rc, output = _cli(tmp_path, root, head)

    assert rc == 0
    assert len(parsed) == 1 and len(scored) == 1
    assert scored[0] is parsed[0]
    assert json.loads(output.read_text(encoding="utf-8"))["result"] == "pass"


def test_cli_private_copy_rewritten_and_reverted_during_scoring_aborts(
    tmp_path, source_repo, monkeypatch, capsys
) -> None:
    """claude-rco-2 finding on bd67af38 (2026-09-03): a rewrite of the private
    copy that is reverted before the after-recheck must not be scored."""
    root, head = source_repo
    original = gate.build_axis_b_report

    def _rewrite_copy_then_revert(**kwargs):
        copy = Path(kwargs["root"]) / HUB_REL
        clean = copy.read_bytes()
        copy.write_bytes(_count_preserving_rewrite(clean))
        try:
            return original(**kwargs)
        finally:
            copy.write_bytes(clean)

    monkeypatch.setattr(gate, "build_axis_b_report", _rewrite_copy_then_revert)

    rc, output = _cli(tmp_path, root, head)

    assert rc == 2
    assert not output.exists()
    err = capsys.readouterr().err
    assert "subject_corpus_mismatch" in err
    assert str(tmp_path) not in err


@pytest.mark.parametrize("mutation", ["reorder_cells", "disable_hub"])
def test_cli_private_copy_hex_config_rewritten_and_reverted_aborts(
    tmp_path, source_repo, monkeypatch, capsys, mutation
) -> None:
    """Same class against the hex config: cell order and every cell field
    loaded by the registry must equal the bound bytes."""
    root, head = source_repo
    original = gate.build_axis_b_report

    def _rewrite_config_then_revert(**kwargs):
        config = Path(kwargs["root"]) / "configs" / "hex_cells.yaml"
        clean = config.read_bytes()
        data = yaml.safe_load(clean.decode("utf-8"))
        if mutation == "reorder_cells":
            data["cells"][0], data["cells"][1] = data["cells"][1], data["cells"][0]
        else:
            next(cell for cell in data["cells"] if cell["id"] == "hub")["enabled"] = False
        config.write_bytes(yaml.safe_dump(data, sort_keys=False).encode("utf-8"))
        try:
            return original(**kwargs)
        finally:
            config.write_bytes(clean)

    monkeypatch.setattr(gate, "build_axis_b_report", _rewrite_config_then_revert)

    rc, output = _cli(tmp_path, root, head)

    assert rc == 2
    assert not output.exists()
    assert "subject_hex_config_mismatch" in capsys.readouterr().err


def test_cli_private_copy_planted_oracle_file_aborts(
    tmp_path, source_repo, monkeypatch, capsys
) -> None:
    root, head = source_repo
    original = gate.build_axis_b_report

    def _plant_then_remove(**kwargs):
        planted = Path(kwargs["root"]) / "tests" / "oracle_hex" / "zzz_planted.yaml"
        planted.write_text(
            'cell: hub\npositive:\n  - "needs central triage"\nnegative: []\n',
            encoding="utf-8",
        )
        try:
            return original(**kwargs)
        finally:
            planted.unlink()

    monkeypatch.setattr(gate, "build_axis_b_report", _plant_then_remove)

    rc, output = _cli(tmp_path, root, head)

    assert rc == 2
    assert not output.exists()
    assert "subject_corpus_mismatch" in capsys.readouterr().err


def test_bind_scored_subject_rejects_incomplete_or_unparseable_subject() -> None:
    corpus_dir = ROOT / "tests" / "oracle_hex"
    config = ROOT / "configs" / "hex_cells.yaml"
    registry = HexTopologyRegistry(config_path=str(config), agents=[])
    oracles = load_oracle_corpus(corpus_dir)
    subject = {rel: (ROOT / rel).read_bytes() for rel in AXIS_B_EXPECTED_SOURCES}

    assert gate.bind_scored_subject(oracles, registry, subject) == oracles

    without_config = {rel: data for rel, data in subject.items() if "hex_cells" not in rel}
    with pytest.raises(gate.SubjectMismatch) as excinfo:
        gate.bind_scored_subject(oracles, registry, without_config)
    assert excinfo.value.blocker == "subject_incomplete"

    broken = dict(subject)
    broken[HUB_REL] = b"positive: [unterminated\n"
    with pytest.raises(gate.SubjectMismatch) as excinfo:
        gate.bind_scored_subject(oracles, registry, broken)
    assert excinfo.value.blocker == "subject_unparseable"

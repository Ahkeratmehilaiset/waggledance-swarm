# SPDX-License-Identifier: BUSL-1.1
"""Phase 17A — 10k Solver Scale Proof tests.

These tests run ``tools/run_solver_scale_proof.py`` against a small
synthetic descriptor count (240) inside a tmp directory and assert
every contract documented in
``docs/runs/phase17a_producer_fabric_scale_2026_05_04/implementation_plan.md``.

Why 240 (not 10000) inside the test:

    * 10000 descriptors take ~150 s on this hardware in build phase.
      Tests must stay under the autonomy_growth suite's per-test
      budget. The proof tool is exercised at full 10k scale by:
        - the operator's local run before PR (recorded in
          ``solver_scale_proof.json``);
        - the Docker run inside ``--network none``;
        - the post-merge run on origin/main.
      The test only proves the *code path* works end-to-end; the
      release artifact proves it works at 10000.
    * 240 descriptors still exercises 6 families × 8 cells (×5 each)
      which preserves the balanced-distribution invariant.

Axis V2 (2026-09-02): the proof requires ``--source-commit`` and runs a
fail-closed source-subject preflight against the repository root, so
the fixture drives it against a throwaway git repository holding the
exact Axis A inventory (``root=`` is a keyword-only test seam of
``main``; the CLI exposes no root override). The forge tests below cover
every named failure: malformed and arbitrary stamps, dirty tracked and
untracked trees, tampering hidden from ``git status``, mid-run HEAD and
source changes, git unavailable, ``GIT_*`` retargeting, and the
non-pass artifact for a failed criterion.

Note: the orchestrator and proof tool import the *real*
``RuntimeQueryRouter`` + ``LowRiskSolverDispatcher`` + ``ControlPlaneDB``;
no mocks, no synthetic shortcuts of the data path.
"""

from __future__ import annotations

import datetime as dt
import json
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
TOOLS_DIR = ROOT / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import run_solver_scale_proof as scale  # type: ignore  # noqa: E402
import tools.verify_release_soak_evidence as verifier  # noqa: E402
from tools.release_axis_a_attestation import (  # noqa: E402
    AXIS_A_EXPECTED_SOURCES,
    _source_digest as _helper_source_digest,
)


NOT_IN_REPO = "0123456789abcdef0123456789abcdef01234567"
SMALL_RUN = ["--descriptors", "240", "--lookup-pass-count", "120"]
PROOF_NAME = "solver_scale_proof.json"


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


def _write_source(root, rel, text="value = 1\n"):
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(f"# {rel}\n{text}".encode("utf-8"))
    return target


def _init_axis_a_repo(root) -> str:
    """A throwaway repository holding the exact Axis A inventory."""
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-q")
    _git(root, "config", "core.autocrlf", "false")
    for rel in AXIS_A_EXPECTED_SOURCES:
        _write_source(root, rel)
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "axis a sources")
    return _git(root, "rev-parse", "HEAD").stdout.strip()


def _commit_all(root, message="more") -> str:
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", message)
    return _git(root, "rev-parse", "HEAD").stdout.strip()


def _argv(out_dir, db_path, stamp, extra=()):
    return [
        "--out-dir",
        str(out_dir),
        *SMALL_RUN,
        "--db",
        str(db_path),
        "--source-commit",
        stamp,
        *extra,
    ]


def _mid_run(monkeypatch, action) -> None:
    """Run ``action`` once, inside the measurement, before the second preflight."""
    original = scale.run_lookup_samples
    fired = {"done": False}

    def _wrapper(router, samples):
        if not fired["done"]:
            fired["done"] = True
            action()
        return original(router, samples)

    monkeypatch.setattr(scale, "run_lookup_samples", _wrapper)


@pytest.fixture
def source_repo(tmp_path):
    root = tmp_path / "repo"
    return root, _init_axis_a_repo(root)


@pytest.fixture
def out_dir(tmp_path: Path) -> Path:
    return tmp_path / "phase17a_solver_scale_proof_artifacts"


@pytest.fixture
def proof(out_dir: Path, tmp_path: Path, source_repo) -> dict:
    """Run the scale proof at 240 descriptors and return the proof JSON."""
    root, head = source_repo
    rc = scale.main(_argv(out_dir, tmp_path / "scale_proof_test.db", head), root=root)
    assert rc == 0, "scale proof exited non-zero"

    proof_path = out_dir / PROOF_NAME
    assert proof_path.is_file(), "solver_scale_proof.json missing"
    return json.loads(proof_path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Honesty / claims-labelling
# ---------------------------------------------------------------------------

def test_proof_clearly_labelled_synthetic(proof: dict) -> None:
    """Master prompt rule 16: descriptors must be labelled
    synthetic-scale, NOT canonical proof corpus."""
    assert proof["is_synthetic_scale"] is True
    assert proof["not_canonical_corpus"] is True


def test_proof_emits_phase_marker(proof: dict) -> None:
    assert proof["phase"] == "phase17a_solver_scale"
    assert proof["schema_version"] == 1


# ---------------------------------------------------------------------------
# Counts
# ---------------------------------------------------------------------------

def test_proof_descriptor_count(proof: dict) -> None:
    assert proof["synthetic_solver_descriptors_total"] == 240


def test_proof_families_total(proof: dict) -> None:
    assert proof["families_total"] == 6
    expected_families = {
        "scalar_unit_conversion",
        "lookup_table",
        "threshold_rule",
        "interval_bucket_classifier",
        "linear_arithmetic",
        "bounded_interpolation",
    }
    assert set(proof["allowed_families"]) == expected_families
    assert set(proof["descriptors_per_family"].keys()) == expected_families


def test_proof_descriptors_balanced_across_families(proof: dict) -> None:
    """240 / 6 = 40 descriptors per family exactly."""
    counts = proof["descriptors_per_family"]
    for family, count in counts.items():
        assert count == 40, \
            f"family {family} has {count} descriptors, expected 40"


def test_proof_hex_cells_total(proof: dict) -> None:
    assert proof["hex_cells_total"] == 8
    expected_cells = {
        "general", "thermal", "energy", "safety",
        "seasonal", "math", "system", "learning",
    }
    assert set(proof["hex_cells"]) == expected_cells


def test_proof_descriptors_balanced_across_cells(proof: dict) -> None:
    """240 / 8 = 30 descriptors per cell exactly."""
    counts = proof["descriptors_per_hex_cell"]
    for cell, count in counts.items():
        assert count == 30, \
            f"cell {cell} has {count} descriptors, expected 30"


# ---------------------------------------------------------------------------
# Capability lookup correctness
# ---------------------------------------------------------------------------

def test_proof_all_lookups_hit_capability_path(proof: dict) -> None:
    """Critical invariant: every sampled query must be served via the
    real auto_promoted_solver source. NO FIFO fallback. NO miss.

    A failure here means the test is exercising the FIFO fallback or
    the gap-emit path, not the capability lookup path. The whole
    point of the scale proof is to prove the capability path scales.
    """
    assert proof["lookup_capability_hits_total"] == proof["lookup_pass_count"]
    assert proof["lookup_fifo_fallback_total"] == 0
    assert proof["lookup_miss_total"] == 0
    assert proof["lookup_by_source"] == {
        "auto_promoted_solver": proof["lookup_pass_count"]
    }


def test_proof_uses_production_hot_path_cache(proof: dict) -> None:
    """R22.1a: the scale proof must measure the production-shaped
    RuntimeQueryRouter wiring with HotPathCache attached, not the old
    no-cache benchmark-only path."""
    assert proof["production_hot_path_cache_attached"] is True
    assert proof["lookup_benchmark_shape"] == "hot_path_cache_attached_warm_pass"
    hot = proof["hot_path_cache_stats"]
    assert hot["cold_hits_warmed"] == proof["lookup_pass_count"]
    assert hot["warm_hits"] == proof["lookup_pass_count"]
    assert hot["misses"] == 0
    assert hot["warm_index_size_after_lookup"] == proof["lookup_pass_count"]
    assert hot["artifact_cache_size_after_lookup"] == proof["lookup_pass_count"]
    cold = proof["lookup_cold_after_attach"]
    assert cold["lookup_capability_hits_total"] == proof["lookup_pass_count"]
    assert cold["lookup_fifo_fallback_total"] == 0
    assert cold["lookup_miss_total"] == 0


def test_proof_lookup_latency_p50_under_100ms(proof: dict) -> None:
    """p50 capability lookup latency should be well under 100 ms even
    with a SQLite-backed control plane and 240 descriptors. This is
    a sanity check, not a strict performance claim."""
    assert proof["lookup_p50_ms"] is not None
    assert proof["lookup_p50_ms"] < 100.0


def test_proof_lookup_p99_finite(proof: dict) -> None:
    assert proof["lookup_p99_ms"] is not None
    # No hard upper bound — only sanity-check that a number was emitted.
    assert proof["lookup_p99_ms"] >= proof["lookup_p50_ms"]


# ---------------------------------------------------------------------------
# Inner-loop invariants (carry-forward from Phase 11–16D RULE 7)
# ---------------------------------------------------------------------------

def test_proof_provider_jobs_delta_zero(proof: dict) -> None:
    assert proof["provider_jobs_delta"] == 0


def test_proof_builder_jobs_delta_zero(proof: dict) -> None:
    assert proof["builder_jobs_delta"] == 0


def test_proof_no_provider_credentials_required(proof: dict) -> None:
    assert proof["no_provider_credentials_required"] is True


def test_proof_no_runtime_network_required(proof: dict) -> None:
    assert proof["no_runtime_network_required"] is True


def test_proof_no_allowlist_widening(proof: dict) -> None:
    assert proof["no_allowlist_widening"] is True


# ---------------------------------------------------------------------------
# Build-time evidence
# ---------------------------------------------------------------------------

def test_proof_emits_build_index_time(proof: dict) -> None:
    assert "build_index_time_seconds" in proof
    assert isinstance(proof["build_index_time_seconds"], (int, float))
    assert proof["build_index_time_seconds"] > 0


def test_proof_emits_build_descriptors_per_second(proof: dict) -> None:
    assert "build_descriptors_per_second" in proof
    rate = proof["build_descriptors_per_second"]
    assert isinstance(rate, (int, float))
    assert rate > 0


# ---------------------------------------------------------------------------
# Real RuntimeQueryRouter is exercised (no mock bypass)
# ---------------------------------------------------------------------------

def test_real_capability_lookup_path_exercised() -> None:
    """Importing the proof script must pull in the real runtime router
    and dispatcher classes (not a mock or a synthetic stand-in)."""
    from waggledance.core.autonomy_growth.runtime_query_router import (
        RuntimeQueryRouter as _RealRouter,
    )
    from waggledance.core.autonomy_growth.hot_path_cache import (
        HotPathCache as _RealHotPathCache,
    )
    from waggledance.core.autonomy_growth.solver_dispatcher import (
        LowRiskSolverDispatcher as _RealDispatcher,
    )
    from waggledance.core.storage.control_plane import (
        ControlPlaneDB as _RealCP,
    )
    # The proof module imports the real classes by name.
    assert scale.RuntimeQueryRouter is _RealRouter
    assert scale.HotPathCache is _RealHotPathCache
    assert scale.LowRiskSolverDispatcher is _RealDispatcher
    assert scale.ControlPlaneDB is _RealCP


# ---------------------------------------------------------------------------
# Synthesizer correctness
# ---------------------------------------------------------------------------

def test_synthesize_descriptors_unique_solver_names() -> None:
    descriptors = scale.synthesize_descriptors(60)
    names = {d["solver_name"] for d in descriptors}
    assert len(names) == 60


def test_synthesize_features_only_within_allowlist() -> None:
    """Each synthesized feature dict belongs to one of the six
    low-risk families. No feature dict may bleed across families."""
    for family in scale.ALLOWED_FAMILIES:
        feats = scale.synthesize_features(family, 0)
        # Every family includes the synth_id key for unique lookup.
        assert "synth_id" in feats
        # Family-specific keys are present.
        if family == "scalar_unit_conversion":
            assert "from_unit" in feats and "to_unit" in feats
        elif family == "lookup_table":
            assert "domain" in feats
        elif family == "threshold_rule":
            assert "subject" in feats and "operator" in feats
        elif family == "interval_bucket_classifier":
            assert "key" in feats and "dimension_count" in feats
        elif family == "linear_arithmetic":
            assert "x_var" in feats and "y_var" in feats
        elif family == "bounded_interpolation":
            assert "x_var" in feats and "y_var" in feats
            assert "sample_count" in feats


def test_synthesize_artifact_returns_executable_kind() -> None:
    """Each synthesized artifact carries the family ``kind`` field
    that ``execute_artifact`` requires."""
    for family in scale.ALLOWED_FAMILIES:
        artifact, inputs = scale.synthesize_artifact_and_inputs(family, 0)
        assert artifact.get("kind") == family
        assert isinstance(inputs, dict) and len(inputs) >= 1


# ---------------------------------------------------------------------------
# Axis V2: source-subject binding
# ---------------------------------------------------------------------------

def test_proof_binds_source_subject(proof: dict, source_repo) -> None:
    root, head = source_repo
    assert proof["source_commit"] == head
    assert proof["source_files"] == list(AXIS_A_EXPECTED_SOURCES)
    assert proof["source_hashes"] == {
        rel: _helper_source_digest(root / rel) for rel in AXIS_A_EXPECTED_SOURCES
    }
    assert all(digest.startswith("sha256:") for digest in proof["source_hashes"].values())
    generated_at = dt.datetime.fromisoformat(
        proof["generated_at"].replace("Z", "+00:00")
    )
    assert generated_at.utcoffset() == dt.timedelta(0)
    assert proof["result"] == "pass"
    assert proof["blockers"] == []


def test_source_commit_flag_is_required(out_dir, tmp_path, source_repo) -> None:
    root, _head = source_repo

    with pytest.raises(SystemExit) as excinfo:
        scale.main(
            ["--out-dir", str(out_dir), *SMALL_RUN, "--db", str(tmp_path / "a.db")],
            root=root,
        )

    assert excinfo.value.code == 2
    assert not (out_dir / PROOF_NAME).exists()


@pytest.mark.parametrize(
    "stamp",
    ["HEAD", "main", NOT_IN_REPO.upper(), NOT_IN_REPO[:39], NOT_IN_REPO + "0"],
    ids=["head-word", "branch", "uppercase", "short", "long"],
)
def test_malformed_source_commit_rejected(out_dir, tmp_path, source_repo, stamp) -> None:
    root, _head = source_repo

    with pytest.raises(SystemExit) as excinfo:
        scale.main(_argv(out_dir, tmp_path / "a.db", stamp), root=root)

    assert excinfo.value.code == 2
    assert not (out_dir / PROOF_NAME).exists()


def test_well_formed_stamp_not_head_aborts_without_artifact(
    out_dir, tmp_path, source_repo, capsys
) -> None:
    root, _head = source_repo

    rc = scale.main(_argv(out_dir, tmp_path / "a.db", NOT_IN_REPO), root=root)

    assert rc == 2
    assert not (out_dir / PROOF_NAME).exists()
    captured = capsys.readouterr()
    assert "source_commit_not_head" in captured.err
    assert str(tmp_path) not in captured.err


def test_dirty_tracked_worktree_aborts_without_artifact(
    out_dir, tmp_path, source_repo, capsys
) -> None:
    root, head = source_repo
    with (root / AXIS_A_EXPECTED_SOURCES[0]).open("ab") as handle:
        handle.write(b"# dirty\n")

    rc = scale.main(_argv(out_dir, tmp_path / "a.db", head), root=root)

    assert rc == 2
    assert not (out_dir / PROOF_NAME).exists()
    assert "worktree_dirty" in capsys.readouterr().err


def test_untracked_file_aborts_without_artifact(
    out_dir, tmp_path, source_repo, capsys
) -> None:
    root, head = source_repo
    (root / "stray.txt").write_text("stray\n", encoding="utf-8")

    rc = scale.main(_argv(out_dir, tmp_path / "a.db", head), root=root)

    assert rc == 2
    assert not (out_dir / PROOF_NAME).exists()
    assert "worktree_dirty" in capsys.readouterr().err


def test_tamper_hidden_from_status_aborts(out_dir, tmp_path, source_repo, capsys) -> None:
    root, head = source_repo
    rel = AXIS_A_EXPECTED_SOURCES[0]
    _git(root, "update-index", "--assume-unchanged", rel)
    (root / rel).write_bytes(b"# tampered\nvalue = 2\n")
    assert verifier.source_subject_preflight(root, head) == []

    rc = scale.main(_argv(out_dir, tmp_path / "a.db", head), root=root)

    assert rc == 2
    assert not (out_dir / PROOF_NAME).exists()
    assert "source_worktree_blob_mismatch" in capsys.readouterr().err


def test_mid_run_head_change_aborts_without_artifact(
    out_dir, tmp_path, source_repo, monkeypatch, capsys
) -> None:
    root, head = source_repo

    def _late_commit():
        (root / "late.txt").write_text("late\n", encoding="utf-8")
        _commit_all(root, "late")

    _mid_run(monkeypatch, _late_commit)

    rc = scale.main(_argv(out_dir, tmp_path / "a.db", head), root=root)

    assert rc == 2
    assert not (out_dir / PROOF_NAME).exists()
    assert "source_commit_not_head" in capsys.readouterr().err


def test_mid_run_source_edit_aborts_without_artifact(
    out_dir, tmp_path, source_repo, monkeypatch, capsys
) -> None:
    root, head = source_repo

    def _late_edit():
        with (root / AXIS_A_EXPECTED_SOURCES[1]).open("ab") as handle:
            handle.write(b"# late edit\n")

    _mid_run(monkeypatch, _late_edit)

    rc = scale.main(_argv(out_dir, tmp_path / "a.db", head), root=root)

    assert rc == 2
    assert not (out_dir / PROOF_NAME).exists()
    assert "worktree_dirty" in capsys.readouterr().err


def test_mid_run_tamper_hidden_from_status_aborts(
    out_dir, tmp_path, source_repo, monkeypatch, capsys
) -> None:
    """The first preflight passes; the second catches a status-invisible edit."""
    root, head = source_repo
    rel = AXIS_A_EXPECTED_SOURCES[2]
    _git(root, "update-index", "--assume-unchanged", rel)

    def _late_tamper():
        (root / rel).write_bytes(b"# tampered\nvalue = 3\n")

    _mid_run(monkeypatch, _late_tamper)

    rc = scale.main(_argv(out_dir, tmp_path / "a.db", head), root=root)

    assert rc == 2
    assert not (out_dir / PROOF_NAME).exists()
    assert "source_worktree_blob_mismatch" in capsys.readouterr().err


def test_git_unavailable_aborts_without_artifact(
    out_dir, tmp_path, source_repo, monkeypatch, capsys
) -> None:
    root, head = source_repo
    monkeypatch.setattr(verifier, "GIT_EXECUTABLE", "git-axis-v2-definitely-missing")

    rc = scale.main(_argv(out_dir, tmp_path / "a.db", head), root=root)

    assert rc == 2
    assert not (out_dir / PROOF_NAME).exists()
    assert "git_unavailable" in capsys.readouterr().err


def test_git_environment_cannot_retarget_producer(
    out_dir, tmp_path, source_repo, monkeypatch
) -> None:
    root, head = source_repo
    other = tmp_path / "other"
    other.mkdir()
    _git(other, "init", "-q")
    (other / "README.md").write_text("other\n", encoding="utf-8")
    other_head = _commit_all(other, "other")
    assert other_head != head
    monkeypatch.setenv("GIT_DIR", str(other / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(other))
    monkeypatch.setenv("GIT_INDEX_FILE", str(other / ".git" / "index"))

    rc = scale.main(_argv(out_dir, tmp_path / "a.db", head), root=root)

    assert rc == 0
    proof = json.loads((out_dir / PROOF_NAME).read_text(encoding="utf-8"))
    assert proof["source_commit"] == head


def test_failed_pass_criterion_writes_non_pass_artifact(
    out_dir, tmp_path, source_repo, monkeypatch
) -> None:
    root, head = source_repo
    original = scale.run_lookup_samples

    def _degraded(router, samples):
        stats = original(router, samples)
        stats["lookup_fifo_fallback_total"] += 1
        stats["lookup_capability_hits_total"] -= 1
        return stats

    monkeypatch.setattr(scale, "run_lookup_samples", _degraded)

    rc = scale.main(_argv(out_dir, tmp_path / "a.db", head), root=root)

    assert rc == 1
    proof = json.loads((out_dir / PROOF_NAME).read_text(encoding="utf-8"))
    assert proof["result"] == "blocked"
    assert "lookup_fifo_fallback_present" in proof["blockers"]
    assert "lookup_capability_hits_incomplete" in proof["blockers"]
    assert proof["source_commit"] == head


def test_cli_exposes_no_root_override(tmp_path) -> None:
    with pytest.raises(SystemExit) as excinfo:
        scale.main(["--root", str(tmp_path), "--source-commit", NOT_IN_REPO])

    assert excinfo.value.code == 2

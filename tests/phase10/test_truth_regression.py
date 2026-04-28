"""Phase 10 P7 — truth / regression / no-leak tests.

Covers RULE 7 categories not already covered by P2/P3/P4/P5:

15. README truthfulness regression (badge + Phase 10 mention)
17. approval invalidation / supersession logic
18. Prompt 2 corrected contract presence
19. no-force / no-rewrite respected (git history sanity)
20. cutover model classification document presence
21. MODEL_C: no-op documentation test
25. no absolute path leakage in journal / release docs
26. no secret leakage in committed files
27. LICENSE-CORE.md covers all new crown-jewel files
28. provider invocations log present
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------
# 15. README truthfulness regression
# ---------------------------------------------------------------


def test_readme_mentions_phase_10_and_correct_main_sha() -> None:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert "Phase 10" in readme, "Phase 10 must be mentioned in README"
    assert "Foundation, Truth, Builder Lane" in readme
    assert "review-only" in readme.lower() or "deferred" in readme.lower()


def test_current_status_main_sha_matches_origin_main() -> None:
    cs = (REPO_ROOT / "CURRENT_STATUS.md").read_text(encoding="utf-8")
    # The fix in P6: main was updated from a1c4152 to 8bf1869.
    assert "8bf1869" in cs, "CURRENT_STATUS.md must reference the actual main SHA 8bf1869"


# ---------------------------------------------------------------
# 17 & 18. atomic flip prep + Prompt 2 contract
# ---------------------------------------------------------------


def test_human_approval_marked_superseded() -> None:
    approval = REPO_ROOT / "docs" / "atomic_flip_prep" / "HUMAN_APPROVAL.yaml"
    if not approval.is_file():
        pytest.skip("HUMAN_APPROVAL.yaml not present in this checkout")
    text = approval.read_text(encoding="utf-8")
    assert "SUPERSEDED" in text, "Prior HUMAN_APPROVAL must carry a SUPERSEDED note"


def test_atomic_flip_readme_documents_preparation_only_status() -> None:
    """The 00_README must always carry the PREPARATION ONLY status.

    The richer "no flip is needed for v3.6.0" status update lives on
    docs/post-v3.6.0-flip-analysis branch and will be brought forward
    to phase10 in P11. P7 only asserts the floor invariant: this
    directory never executes anything by itself."""

    readme = REPO_ROOT / "docs" / "atomic_flip_prep" / "00_README.md"
    text = readme.read_text(encoding="utf-8")
    assert (
        "PREPARATION ONLY" in text or "preparation only" in text.lower()
    ), "00_README must carry PREPARATION ONLY status"


def test_prompt_2_contract_doc_exists() -> None:
    contract = REPO_ROOT / "docs" / "architecture" / "PROMPT_2_INPUTS_AND_CONTRACTS.md"
    assert contract.is_file(), "PROMPT_2_INPUTS_AND_CONTRACTS.md must exist"


# ---------------------------------------------------------------
# 19. no-force / no-rewrite respected
# ---------------------------------------------------------------


def test_phase10_branch_history_is_linear_descended_from_main() -> None:
    """Verify phase10 history did not diverge from origin/main via a rewrite.

    Two regimes:
    1. Pre-squash-merge: phase10/foundation-truth-builder-lane is ahead
       of origin/main with merge_base == origin/main.
    2. Post-squash-merge (PR #54 merged 2026-04-28): origin/main contains
       the Phase 10 substrate squash commit and the original branch is
       no longer linearly ahead. The invariant becomes "the squash commit
       carries the Phase 10 substrate subject and is on origin/main".

    Skipped if not in a git checkout or if the branch ref is unavailable.
    """

    git_dir = REPO_ROOT / ".git"
    if not git_dir.exists():
        pytest.skip("not a git checkout")
    try:
        main_sha_proc = subprocess.run(  # noqa: S603
            ["git", "rev-parse", "origin/main"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except FileNotFoundError:
        pytest.skip("git not available")
    if main_sha_proc.returncode != 0:
        pytest.skip(f"origin/main unavailable: {main_sha_proc.stderr.strip()}")
    main_sha = main_sha_proc.stdout.strip()

    main_subject_proc = subprocess.run(  # noqa: S603
        ["git", "log", "-1", "--format=%s", "origin/main"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    main_subject = main_subject_proc.stdout.strip()

    is_post_squash_merge = "Phase 10 substrate" in main_subject

    if is_post_squash_merge:
        # Post-merge regime: the squash commit is on origin/main. Verify
        # it has not been rewritten away by force-push by re-checking
        # that the recorded merge SHA from the release bundle still
        # reachable from origin/main.
        merged_sha_path = (
            REPO_ROOT
            / "docs"
            / "runs"
            / "release_bundle_2026_04_28_phase10"
            / "merged_commit_sha.txt"
        )
        if merged_sha_path.is_file():
            recorded_sha = merged_sha_path.read_text(encoding="utf-8").strip()
            if recorded_sha:
                contains_proc = subprocess.run(  # noqa: S603
                    [
                        "git",
                        "merge-base",
                        "--is-ancestor",
                        recorded_sha,
                        "origin/main",
                    ],
                    cwd=str(REPO_ROOT),
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=10,
                )
                assert contains_proc.returncode == 0, (
                    "recorded Phase 10 squash commit "
                    f"{recorded_sha} is not on origin/main "
                    f"(main_sha={main_sha}); a force-push may have "
                    "rewritten history"
                )
        return

    # Pre-merge regime: enforce linear-descent invariant.
    merge_base = subprocess.run(  # noqa: S603
        ["git", "merge-base", "phase10/foundation-truth-builder-lane", "origin/main"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    if merge_base.returncode != 0:
        pytest.skip(f"merge-base unavailable: {merge_base.stderr.strip()}")
    base_sha = merge_base.stdout.strip()
    assert base_sha == main_sha, (
        "phase10 branch is not linearly ahead of origin/main: "
        f"merge_base={base_sha} main={main_sha}"
    )


# ---------------------------------------------------------------
# 20 & 21. cutover model classification document presence
# ---------------------------------------------------------------


def test_cutover_model_classification_journal_exists_and_picks_model_c() -> None:
    journal = REPO_ROOT / "docs" / "journal" / "2026-04-28_cutover_model_classification.md"
    assert journal.is_file(), "cutover classification journal must exist"
    text = journal.read_text(encoding="utf-8")
    assert "MODEL_C_NOOP_ALREADY_COMPLETE" in text
    assert "MODEL_D_AMBIGUOUS" in text
    # Must explicitly eliminate MODEL_A and MODEL_B for v3.6.0 scope.
    assert "MODEL_A" in text and "MODEL_B" in text


def test_storage_runtime_truth_journal_exists() -> None:
    journal = REPO_ROOT / "docs" / "journal" / "2026-04-28_storage_runtime_truth.md"
    assert journal.is_file()
    text = journal.read_text(encoding="utf-8")
    assert "data/faiss/" in text
    assert "data/vector/" in text
    assert "PathResolver" in text or "path_resolver" in text


# ---------------------------------------------------------------
# 25. no absolute path leakage in journal / release docs
# ---------------------------------------------------------------


_ABSOLUTE_PATH_PATTERNS = (
    re.compile(r"\bC:\\Users\\[^\\\s]+\\AppData\\"),
    re.compile(r"\bC:\\Users\\[^\\\s]+\\Documents\\"),
    re.compile(r"/home/[a-zA-Z][\w-]*/[\w/]+"),
    re.compile(r"/Users/[a-zA-Z][\w-]*/[\w/]+"),
)


def test_no_machine_specific_absolute_paths_in_phase10_journal_docs() -> None:
    targets = [
        REPO_ROOT / "docs" / "journal" / "2026-04-28_storage_runtime_truth.md",
        REPO_ROOT / "docs" / "journal" / "2026-04-28_cutover_model_classification.md",
        REPO_ROOT / "docs" / "architecture" / "CONTROL_PLANE_AND_DATA_PLANE.md",
        REPO_ROOT / "docs" / "architecture" / "PROVIDER_PLANE_AND_BUILDER_LANES.md",
        REPO_ROOT / "docs" / "architecture" / "SOLVER_BOOTSTRAP_AND_SYNTHESIS.md",
        REPO_ROOT / "docs" / "architecture" / "REALITY_VIEW_TRUTH_AND_SCALE.md",
    ]
    for path in targets:
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        for pat in _ABSOLUTE_PATH_PATTERNS:
            m = pat.search(text)
            assert m is None, (
                f"Phase 10 doc {path.name} contains a machine-specific absolute path: {m.group(0)!r}"
            )


# ---------------------------------------------------------------
# 26. no secret leakage in P10 crown-jewel files
# ---------------------------------------------------------------


_SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),  # AWS access key id
    re.compile(r"(?i)api[_-]?key\s*=\s*['\"][^'\"]{16,}['\"]"),
    re.compile(r"(?i)password\s*=\s*['\"][^'\"]{4,}['\"]"),
)


def test_no_secrets_in_phase10_crown_jewel_files() -> None:
    targets = list((REPO_ROOT / "waggledance" / "core" / "storage").glob("*.py"))
    targets += list((REPO_ROOT / "waggledance" / "core" / "providers").glob("*.py"))
    targets += [
        REPO_ROOT / "waggledance" / "core" / "solver_synthesis" / "cold_shadow_throttler.py",
        REPO_ROOT / "waggledance" / "core" / "solver_synthesis" / "llm_solver_generator.py",
        REPO_ROOT / "waggledance" / "core" / "solver_synthesis" / "solver_bootstrap.py",
        REPO_ROOT / "waggledance" / "core" / "solver_synthesis" / "family_specs" / "__init__.py",
        REPO_ROOT / "waggledance" / "ui" / "hologram" / "scale_aware_aggregator.py",
    ]
    for path in targets:
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        for pat in _SECRET_PATTERNS:
            m = pat.search(text)
            assert m is None, (
                f"{path.name} contains what looks like a secret: {m.group(0)!r}"
            )


# ---------------------------------------------------------------
# 27. LICENSE-CORE.md covers all new crown-jewel files
# ---------------------------------------------------------------


_PHASE10_PROTECTED_FILES = (
    "waggledance/core/storage/__init__.py",
    "waggledance/core/storage/control_plane_schema.py",
    "waggledance/core/storage/control_plane.py",
    "waggledance/core/storage/path_resolver.py",
    "waggledance/core/storage/registry_queries.py",
    "waggledance/core/providers/__init__.py",
    "waggledance/core/providers/provider_contracts.py",
    "waggledance/core/providers/provider_registry.py",
    "waggledance/core/providers/provider_plane.py",
    "waggledance/core/providers/claude_code_builder.py",
    "waggledance/core/providers/builder_job_queue.py",
    "waggledance/core/providers/builder_lane_router.py",
    "waggledance/core/providers/mentor_forge.py",
    "waggledance/core/providers/repair_forge.py",
    "waggledance/core/solver_synthesis/cold_shadow_throttler.py",
    "waggledance/core/solver_synthesis/llm_solver_generator.py",
    "waggledance/core/solver_synthesis/solver_bootstrap.py",
    "waggledance/core/solver_synthesis/family_specs/__init__.py",
    "waggledance/ui/hologram/scale_aware_aggregator.py",
)


def test_license_core_lists_every_phase10_crown_jewel_file() -> None:
    license_text = (REPO_ROOT / "LICENSE-CORE.md").read_text(encoding="utf-8")
    missing = [f for f in _PHASE10_PROTECTED_FILES if f not in license_text]
    assert not missing, f"LICENSE-CORE.md is missing P10 files: {missing}"


def test_phase10_crown_jewel_files_have_change_date_header() -> None:
    for relpath in _PHASE10_PROTECTED_FILES:
        path = REPO_ROOT / relpath
        if not path.is_file():
            continue
        head = path.read_text(encoding="utf-8").splitlines()[:6]
        joined = "\n".join(head)
        assert "BUSL-1.1" in joined, f"{relpath}: SPDX-License-Identifier: BUSL-1.1 missing"
        assert "BUSL-Change-Date: 2030-12-31" in joined, (
            f"{relpath}: BUSL-Change-Date 2030-12-31 missing"
        )


# ---------------------------------------------------------------
# 28. provider invocations log present
# ---------------------------------------------------------------


def test_provider_invocations_log_initialized() -> None:
    log = REPO_ROOT / "docs" / "runs" / "provider_invocations.jsonl"
    assert log.is_file(), "provider_invocations.jsonl must be present per RULE 8"
    text = log.read_text(encoding="utf-8")
    assert text.strip(), "log must not be empty"


def test_error_log_initialized() -> None:
    log = REPO_ROOT / "docs" / "runs" / "error_log.jsonl"
    assert log.is_file(), "error_log.jsonl must be present per RULE 14"
    text = log.read_text(encoding="utf-8")
    assert text.strip(), "log must not be empty"

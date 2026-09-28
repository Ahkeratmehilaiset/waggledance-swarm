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

import json
import re
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
_D1_PREPARATION_PATH = REPO_ROOT / "docs/security/d1_pii_scrub_lineage.json"

_D1_PREPARED_FROM_COMMIT = "f12f6d971accf5717141b7bfa2f54a7a35628f91"
_D1_PREPARATION_TOP_LEVEL_KEYS = {
    "schema_version",
    "kind",
    "status",
    "prepared_from_commit",
    "current_tree",
    "blocked_scope",
    "authority",
    "history",
    "refs",
    "mirror",
    "execution",
}
_D1_PREPARATION_CURRENT_TREE = {
    "matched_path_union": 205,
    "business_id_paths": 15,
    "legal_keep_paths": 3,
    "settings_redaction_paths": 2,
    "remaining_unclassified_paths": 203,
}
_D1_PREPARATION_AUTHORITY = {
    "scope": False,
    "legal": False,
    "release": False,
    "production": False,
    "execution": False,
}


def _d1_preparation_fixture() -> dict[str, object]:
    return {
        "schema_version": 1,
        "kind": "waggledance.d1_pii_scrub_preparation",
        "status": "prepared_blocked",
        "prepared_from_commit": _D1_PREPARED_FROM_COMMIT,
        "current_tree": dict(_D1_PREPARATION_CURRENT_TREE),
        "blocked_scope": True,
        "authority": dict(_D1_PREPARATION_AUTHORITY),
        "history": None,
        "refs": None,
        "mirror": None,
        "execution": None,
    }


def _validate_d1_prepared_lineage(raw: bytes) -> dict[str, object]:
    """Validate PREP evidence without importing producer-owned code."""

    if type(raw) is not bytes or raw.startswith(b"\xef\xbb\xbf"):
        raise ValueError("D1 preparation lineage must be BOM-free bytes")

    def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if type(key) is not str or key in result:
                raise ValueError("duplicate D1 preparation lineage key")
            result[key] = value
        return result

    def reject_nonfinite(value: str) -> None:
        raise ValueError(f"non-finite D1 preparation value: {value}")

    try:
        decoded = raw.decode("utf-8")
        if decoded.startswith("\ufeff"):
            raise ValueError("D1 preparation lineage contains a BOM")
        payload = json.loads(
            decoded,
            object_pairs_hook=unique_object,
            parse_constant=reject_nonfinite,
        )
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("D1 preparation lineage is not strict JSON") from exc

    if type(payload) is not dict or set(payload) != _D1_PREPARATION_TOP_LEVEL_KEYS:
        raise ValueError("D1 preparation lineage top-level schema mismatch")
    if type(payload["schema_version"]) is not int or payload["schema_version"] != 1:
        raise ValueError("D1 preparation lineage schema version mismatch")
    if payload["kind"] != "waggledance.d1_pii_scrub_preparation":
        raise ValueError("D1 preparation lineage kind mismatch")
    if payload["status"] != "prepared_blocked":
        raise ValueError("D1 preparation lineage status mismatch")
    if payload["prepared_from_commit"] != _D1_PREPARED_FROM_COMMIT:
        raise ValueError("D1 preparation lineage source commit mismatch")
    current_tree = payload["current_tree"]
    if type(current_tree) is not dict or set(current_tree) != set(
        _D1_PREPARATION_CURRENT_TREE
    ):
        raise ValueError("D1 preparation current-tree schema mismatch")
    for key, expected in _D1_PREPARATION_CURRENT_TREE.items():
        if type(current_tree[key]) is not int or current_tree[key] != expected:
            raise ValueError(f"D1 preparation aggregate mismatch: {key}")
    if payload["blocked_scope"] is not True:
        raise ValueError("D1 preparation scope is not blocked")
    authority = payload["authority"]
    if type(authority) is not dict or set(authority) != set(
        _D1_PREPARATION_AUTHORITY
    ):
        raise ValueError("D1 preparation authority schema mismatch")
    if any(authority[key] is not False for key in _D1_PREPARATION_AUTHORITY):
        raise ValueError("D1 preparation grants authority")
    if any(payload[key] is not None for key in ("history", "refs", "mirror", "execution")):
        raise ValueError("D1 preparation contains future lineage")
    return payload


def _run_phase10_ancestry_git(
    args: list[str],
    *,
    cwd: Path = REPO_ROOT,
    timeout: int = 10,
) -> subprocess.CompletedProcess[str]:
    """Run an ancestry query without permitting local replacement refs."""

    return subprocess.run(  # noqa: S603
        ["git", "--no-replace-objects", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )


def _assert_phase10_recorded_ancestor(
    recorded_sha: str,
    *,
    main_sha: str,
    main_ref: str = "origin/main",
    cwd: Path = REPO_ROOT,
) -> None:
    """Fail closed unless the recorded Phase-10 commit is an ancestor."""

    contains_proc = _run_phase10_ancestry_git(
        ["merge-base", "--is-ancestor", recorded_sha, main_ref],
        cwd=cwd,
    )
    assert contains_proc.returncode == 0, (
        "recorded Phase 10 squash commit "
        f"{recorded_sha} is not an ancestor of {main_ref} "
        f"(main_sha={main_sha}); a force-push may have rewritten history"
    )


# ---------------------------------------------------------------
# 15. README truthfulness regression
# ---------------------------------------------------------------


def test_readme_mentions_phase_10_and_correct_main_sha() -> None:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert "Phase 10" in readme, "Phase 10 must be mentioned in README"
    assert "Foundation, Truth, Builder Lane" in readme
    assert "review-only" in readme.lower() or "deferred" in readme.lower()


def test_current_status_main_sha_matches_origin_main() -> None:
    """Phase 10 P6 truth-regression invariant - historically pinned the
    `8bf1869` truthfulness commit reference inside `CURRENT_STATUS.md`.
    The file was retired in R22.5 cleanup (2026-05-10);
    `CURRENT_STATE.md` is the surviving auto-generated state file.
    Truthfulness commit history is preserved in `CHANGELOG.md` and
    git log."""
    import pytest
    pytest.skip(
        "CURRENT_STATUS.md retired in R22.5 cleanup; "
        "see CHANGELOG and git log for 8bf1869 history."
    )


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
       the Phase 10 substrate squash commit. The invariant becomes "the
       recorded squash SHA in merged_commit_sha.txt is still an ancestor
       of origin/main". D1 preparation records no history-rewrite authority,
       so PREP has no exception to this ancestry requirement.

    Detection: post-squash regime is signalled by the presence of
    docs/runs/release_bundle_2026_04_28_phase10/merged_commit_sha.txt
    containing a SHA. This is robust to subsequent commits on main
    (e.g. PR #55 finalization, future fixes) because the file's
    contents do not change unless someone replaces the squash record.

    Skipped if not in a git checkout or if the git tooling is unavailable.
    """

    git_dir = REPO_ROOT / ".git"
    if not git_dir.exists():
        pytest.skip("not a git checkout")
    try:
        main_sha_proc = _run_phase10_ancestry_git(
            ["rev-parse", "origin/main"],
        )
    except FileNotFoundError:
        pytest.skip("git not available")
    if main_sha_proc.returncode != 0:
        pytest.skip(f"origin/main unavailable: {main_sha_proc.stderr.strip()}")
    main_sha = main_sha_proc.stdout.strip()

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
            # Post-squash-merge regime: the recorded squash SHA must
            # still be an ancestor of origin/main. Any force-push that
            # rewrote it away would fail this check.
            _assert_phase10_recorded_ancestor(
                recorded_sha,
                main_sha=main_sha,
            )
            return

    # Pre-merge regime: enforce the linear-descent invariant against the
    # original phase10 branch ref. This branch only exists in the
    # working clone before / during PR #54; after merge it may have
    # been deleted, in which case we skip rather than fail.
    merge_base = _run_phase10_ancestry_git(
        ["merge-base", "phase10/foundation-truth-builder-lane", "origin/main"],
        timeout=15,
    )
    if merge_base.returncode != 0:
        pytest.skip(f"merge-base unavailable: {merge_base.stderr.strip()}")
    base_sha = merge_base.stdout.strip()
    assert base_sha == main_sha, (
        "phase10 branch is not linearly ahead of origin/main: "
        f"merge_base={base_sha} main={main_sha}"
    )


def test_phase10_ancestry_cannot_be_forged_by_replacement_ref(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "replacement-ref-ancestry"
    repo.mkdir()

    def git(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(  # noqa: S603
            ["git", *args],
            cwd=str(repo),
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )

    git("init")
    git("config", "user.name", "WaggleDance regression")
    git("config", "user.email", "regression@example.invalid")
    (repo / "base.txt").write_text("base\n", encoding="utf-8")
    git("add", "base.txt")
    git("commit", "-m", "base")
    base = git("rev-parse", "HEAD").stdout.strip()

    git("switch", "-c", "left")
    (repo / "left.txt").write_text("left\n", encoding="utf-8")
    git("add", "left.txt")
    git("commit", "-m", "left")
    left = git("rev-parse", "HEAD").stdout.strip()

    git("switch", "-c", "right", base)
    (repo / "right.txt").write_text("right\n", encoding="utf-8")
    git("add", "right.txt")
    git("commit", "-m", "right")
    right = git("rev-parse", "HEAD").stdout.strip()
    git("replace", "--graft", right, left)
    assert git("replace", "--list").stdout.strip() == right

    protected = _run_phase10_ancestry_git(
        ["merge-base", "--is-ancestor", left, right], cwd=repo
    )
    assert protected.returncode == 1


@pytest.mark.parametrize("lineage_status", ["missing", "prepared", "executed"])
@pytest.mark.parametrize("stale_ref", [False, True], ids=["no-stale-ref", "stale-ref"])
@pytest.mark.parametrize(
    "magic_runbook",
    [False, True],
    ids=["no-magic-runbook", "old-magic-runbook"],
)
def test_phase10_nonancestor_cannot_be_overridden_by_d1_artifacts(
    tmp_path: Path,
    lineage_status: str,
    stale_ref: bool,
    magic_runbook: bool,
) -> None:
    """Historical D1 markers and stale refs never excuse non-ancestry."""

    repo = tmp_path / "nonancestor-d1-override"
    repo.mkdir()

    def git(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(  # noqa: S603
            ["git", *args],
            cwd=str(repo),
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )

    git("init")
    git("config", "user.name", "WaggleDance regression")
    git("config", "user.email", "regression@example.invalid")
    (repo / "base.txt").write_text("base\n", encoding="utf-8")
    git("add", "base.txt")
    git("commit", "-m", "base")
    base = git("rev-parse", "HEAD").stdout.strip()

    git("switch", "-c", "recorded", base)
    (repo / "recorded.txt").write_text("recorded\n", encoding="utf-8")
    git("add", "recorded.txt")
    git("commit", "-m", "recorded Phase 10 commit")
    recorded_sha = git("rev-parse", "HEAD").stdout.strip()

    git("switch", "-c", "current", base)
    tool_path = repo / "tools" / "d1_pii_scrub.py"
    tool_path.parent.mkdir(parents=True)
    tool_path.write_text("# inert test fixture\n", encoding="utf-8")

    if lineage_status != "missing":
        lineage_path = repo / "docs" / "security" / "d1_pii_scrub_lineage.json"
        lineage_path.parent.mkdir(parents=True)
        lineage = _d1_preparation_fixture()
        lineage["prepared_from_commit"] = recorded_sha
        if lineage_status == "executed":
            lineage["status"] = "executed"
        lineage_path.write_text(
            json.dumps(lineage, sort_keys=True),
            encoding="utf-8",
        )

    if magic_runbook:
        runbook_path = repo / "docs" / "operations" / "D1_PII_SCRUB_RUNBOOK.md"
        runbook_path.parent.mkdir(parents=True, exist_ok=True)
        runbook_path.write_text(
            "full history scrub\nforce-push will rewrite every commit SHA\n",
            encoding="utf-8",
        )

    git("add", ".")
    git("commit", "-m", "divergent current main with D1 markers")
    main_sha = git("rev-parse", "HEAD").stdout.strip()
    git("update-ref", "refs/remotes/origin/main", main_sha)
    if stale_ref:
        git(
            "update-ref",
            "refs/remotes/origin/stale-phase10-archive",
            recorded_sha,
        )

    with pytest.raises(AssertionError, match="is not an ancestor"):
        _assert_phase10_recorded_ancestor(
            recorded_sha,
            main_sha=main_sha,
            cwd=repo,
        )


def _encode_d1_preparation(payload: dict[str, object]) -> bytes:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")


def test_d1_prepared_lineage_accepts_only_the_blocked_preparation_fact() -> None:
    expected = _d1_preparation_fixture()
    assert _D1_PREPARATION_PATH.is_file(), "D1 preparation lineage is missing"
    assert _validate_d1_prepared_lineage(_D1_PREPARATION_PATH.read_bytes()) == expected


@pytest.mark.parametrize(
    "status",
    [
        "missing",
        "prepared",
        "locally_edited",
        "committed",
        "executed",
        "self_attested",
    ],
)
def test_d1_prepared_lineage_rejects_every_override_status(status: str) -> None:
    payload = _d1_preparation_fixture()
    payload["status"] = status
    with pytest.raises(ValueError, match="status mismatch"):
        _validate_d1_prepared_lineage(_encode_d1_preparation(payload))


@pytest.mark.parametrize("field", sorted(_D1_PREPARATION_AUTHORITY))
@pytest.mark.parametrize("value", [True, 0, None])
def test_d1_prepared_lineage_rejects_any_authority_value(
    field: str, value: object,
) -> None:
    payload = _d1_preparation_fixture()
    authority = payload["authority"]
    assert isinstance(authority, dict)
    authority[field] = value
    with pytest.raises(ValueError, match="grants authority"):
        _validate_d1_prepared_lineage(_encode_d1_preparation(payload))


@pytest.mark.parametrize("field", sorted(_D1_PREPARATION_CURRENT_TREE))
@pytest.mark.parametrize("value", [True, -1, 0, None, "205"])
def test_d1_prepared_lineage_rejects_wrong_aggregate_type_or_value(
    field: str, value: object,
) -> None:
    payload = _d1_preparation_fixture()
    current_tree = payload["current_tree"]
    assert isinstance(current_tree, dict)
    current_tree[field] = value
    with pytest.raises(ValueError, match="aggregate mismatch"):
        _validate_d1_prepared_lineage(_encode_d1_preparation(payload))


@pytest.mark.parametrize("field", ["history", "refs", "mirror", "execution"])
@pytest.mark.parametrize("value", [False, {}, [], "prepared", _D1_PREPARED_FROM_COMMIT])
def test_d1_prepared_lineage_rejects_future_lineage(
    field: str, value: object,
) -> None:
    payload = _d1_preparation_fixture()
    payload[field] = value
    with pytest.raises(ValueError, match="future lineage"):
        _validate_d1_prepared_lineage(_encode_d1_preparation(payload))


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("schema_version", True, "schema version mismatch"),
        ("schema_version", 2, "schema version mismatch"),
        ("kind", "waggledance.d1_pii_scrub_execution", "kind mismatch"),
        ("prepared_from_commit", "f" * 40, "source commit mismatch"),
        ("blocked_scope", False, "scope is not blocked"),
        ("blocked_scope", 1, "scope is not blocked"),
    ],
)
def test_d1_prepared_lineage_rejects_wrong_fixed_values(
    field: str, value: object, message: str,
) -> None:
    payload = _d1_preparation_fixture()
    payload[field] = value
    with pytest.raises(ValueError, match=message):
        _validate_d1_prepared_lineage(_encode_d1_preparation(payload))


@pytest.mark.parametrize("container", ["top", "current_tree", "authority"])
@pytest.mark.parametrize("operation", ["extra", "missing"])
def test_d1_prepared_lineage_rejects_open_or_incomplete_schemas(
    container: str, operation: str,
) -> None:
    payload = _d1_preparation_fixture()
    target = payload if container == "top" else payload[container]
    assert isinstance(target, dict)
    if operation == "extra":
        target["self_attested"] = True
    else:
        target.pop(next(iter(target)))
    with pytest.raises(ValueError, match="schema mismatch"):
        _validate_d1_prepared_lineage(_encode_d1_preparation(payload))


@pytest.mark.parametrize(
    "raw",
    [
        b'\xef\xbb\xbf{"schema_version":1}',
        b'{"schema_version":1,"schema_version":1}',
        b'{"schema_version":NaN}',
    ],
    ids=["utf8-bom", "duplicate-key", "nonfinite"],
)
def test_d1_prepared_lineage_rejects_non_strict_json(raw: bytes) -> None:
    with pytest.raises(ValueError):
        _validate_d1_prepared_lineage(raw)


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

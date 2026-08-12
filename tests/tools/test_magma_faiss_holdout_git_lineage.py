from __future__ import annotations

import copy
import hashlib
import os
import runpy
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from tools import magma_faiss_holdout_git_lineage as git_lineage
from tools import magma_faiss_holdout_protocol as holdout_protocol


ROOT = Path(__file__).resolve().parents[2]
PROTOCOL_FIXTURES = runpy.run_path(
    str(ROOT / "tests" / "tools" / "test_magma_faiss_holdout_protocol.py")
)
_GIT_ON_PATH = shutil.which("git")
assert _GIT_ON_PATH is not None
_PATH_GIT_EXECUTABLE = Path(_GIT_ON_PATH).resolve(strict=True)
_DIRECT_GIT_EXECUTABLE = (
    _PATH_GIT_EXECUTABLE.parent.parent / "mingw64" / "bin" / "git.exe"
)
GIT_EXECUTABLE = (
    _DIRECT_GIT_EXECUTABLE.resolve(strict=True)
    if _DIRECT_GIT_EXECUTABLE.is_file()
    else _PATH_GIT_EXECUTABLE
)
GIT_EXECUTABLE_SHA256 = (
    "sha256:" + hashlib.sha256(GIT_EXECUTABLE.read_bytes()).hexdigest()
)


class _StringSubclass(str):
    pass


class _DictSubclass(dict):
    pass


def _runtime() -> dict[str, Any]:
    return {
        "git_executable": GIT_EXECUTABLE,
        "expected_git_executable_sha256": GIT_EXECUTABLE_SHA256,
    }


def _verify(repo: Path, **kwargs: Any) -> dict[str, Any]:
    return git_lineage.verify_git_lineage(repo, **_runtime(), **kwargs)


def _validate(
    value: Any,
    *,
    repo: Path,
    **kwargs: Any,
) -> dict[str, Any]:
    return git_lineage.validate_git_lineage_projection(
        value,
        repo=repo,
        **_runtime(),
        **kwargs,
    )


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        text=True,
        capture_output=True,
    )
    return completed.stdout.strip()


def _commit(repo: Path, message: str) -> str:
    _git(repo, "commit", "--allow-empty", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def _forge_commit_graph_parent(
    graph_path: Path,
    *,
    child: str,
    forged_parent: str,
) -> None:
    graph = bytearray(graph_path.read_bytes())
    assert graph[:4] == b"CGPH"
    assert graph[4:6] == b"\x01\x01"
    chunk_count = graph[6]
    chunks: dict[bytes, int] = {}
    for index in range(chunk_count + 1):
        start = 8 + index * 12
        chunks[bytes(graph[start : start + 4])] = int.from_bytes(
            graph[start + 4 : start + 12], "big"
        )
    oid_lookup = chunks[b"OIDL"]
    commit_data = chunks[b"CDAT"]

    def oid_index(hex_oid: str) -> int:
        position = graph.find(bytes.fromhex(hex_oid), oid_lookup, commit_data)
        assert position >= oid_lookup
        assert (position - oid_lookup) % 20 == 0
        return (position - oid_lookup) // 20

    parent_index = oid_index(forged_parent)
    child_index = oid_index(child)
    row = commit_data + child_index * 36
    graph[row + 20 : row + 24] = parent_index.to_bytes(4, "big")
    graph[row + 24 : row + 28] = (0x70000000).to_bytes(4, "big")
    graph[-20:] = hashlib.sha1(graph[:-20]).digest()
    graph_path.chmod(0o600)
    graph_path.write_bytes(graph)


def _transition(
    state: dict[str, Any],
    *,
    protocol: dict[str, Any],
    declared_commit: str,
) -> dict[str, Any]:
    sequence = state["sequence"] + 1
    stage = holdout_protocol.STAGES[sequence]
    evidence_kinds = {
        "frame_committed": "frame_manifest_commitment",
        "seed_revealed": "selection_seed_receipt",
        "pack_sealed": "query_label_adjudication_seal",
        "query_captured": "label_blind_capture_receipt",
        "labels_revealed": "label_release_receipt",
        "scored": "fixed_threshold_verdict",
    }
    return {
        "schema_version": holdout_protocol.TRANSITION_SCHEMA,
        "protocol_digest": state["protocol_digest"],
        "from_stage": state["stage"],
        "to_stage": stage,
        "sequence": sequence,
        "previous_state_digest": holdout_protocol.state_digest(
            state,
            protocol=protocol,
        ),
        "evidence_kind": evidence_kinds[stage],
        "declared_commit": declared_commit,
        "artifact_digest": "sha256:" + f"{sequence + 1:064x}",
    }


def _fixture(tmp_path: Path) -> dict[str, Any]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "evidence")
    _git(repo, "config", "user.name", "WD Test")
    _git(repo, "config", "user.email", "wd-test@example.invalid")
    candidate_commit = _commit(repo, "candidate")

    protocol = PROTOCOL_FIXTURES["_protocol"]()
    protocol["candidate_identity"]["candidate_commit"] = candidate_commit
    protocol["cutoff"]["cutoff_commit"] = candidate_commit
    protocol = holdout_protocol.validate_preregistration(protocol)

    preregistration_commit = _commit(repo, "preregistered")
    state = holdout_protocol.initialize_state(
        protocol,
        declared_preregistration_commit=preregistration_commit,
    )
    states = [state]
    for stage in holdout_protocol.STAGES[1:]:
        commit = _commit(repo, stage)
        state = holdout_protocol.advance_state(
            state,
            _transition(
                state,
                protocol=protocol,
                declared_commit=commit,
            ),
            protocol=protocol,
        )
        states.append(state)
    return {
        "repo": repo.resolve(),
        "protocol": protocol,
        "states": states,
        "candidate_commit": candidate_commit,
        "local_ref": "refs/heads/evidence",
        "local_ref_tip": _git(repo, "rev-parse", "refs/heads/evidence"),
    }


@pytest.fixture()
def bundle(tmp_path: Path) -> dict[str, Any]:
    return _fixture(tmp_path)


def test_complete_lineage_is_verified_without_external_authority(
    bundle: dict[str, Any],
) -> None:
    source = copy.deepcopy(bundle["states"])
    projection = _verify(
        bundle["repo"],
        protocol=bundle["protocol"],
        states=bundle["states"],
        local_ref=bundle["local_ref"],
    )

    assert bundle["states"] == source
    assert projection["verified_stage"] == "scored"
    assert projection["sequence"] == 6
    assert projection["candidate_commit"] == bundle["candidate_commit"]
    assert projection["local_ref_tip_commit"] == bundle["local_ref_tip"]
    assert projection["verified_declared_commits"] == [
        state["evidence"]["declared_commit"] for state in bundle["states"]
    ]
    capabilities = projection["capability_boundary"]
    assert capabilities["local_git_commit_existence_verified"] is True
    assert capabilities["git_ancestry_verified"] is True
    assert capabilities["local_ref_containment_verified"] is True
    assert capabilities["git_executable_digest_verified"] is True
    assert capabilities["git_executable_trust_verified"] is False
    assert projection["git_executable_sha256"] == GIT_EXECUTABLE_SHA256
    for key, value in capabilities.items():
        if key not in {
            "artifact_class",
            "local_git_commit_existence_verified",
            "git_ancestry_verified",
            "local_ref_containment_verified",
            "git_executable_digest_verified",
        }:
            assert value is False, key
    assert "repo" not in projection
    assert "remote" not in projection["relationship"]


@pytest.mark.parametrize("final_index", [0, 2, 5])
def test_each_complete_state_prefix_can_be_verified(
    bundle: dict[str, Any], final_index: int
) -> None:
    projection = _verify(
        bundle["repo"],
        protocol=bundle["protocol"],
        states=bundle["states"][: final_index + 1],
        local_ref=bundle["local_ref"],
    )
    assert projection["sequence"] == final_index
    assert projection["verified_stage"] == holdout_protocol.STAGES[final_index]


def test_projection_requires_exact_git_recomputation(
    bundle: dict[str, Any],
) -> None:
    projection = _verify(
        bundle["repo"],
        protocol=bundle["protocol"],
        states=bundle["states"],
        local_ref=bundle["local_ref"],
    )
    assert _validate(
        projection,
        repo=bundle["repo"],
        protocol=bundle["protocol"],
        states=bundle["states"],
    ) == projection

    mutations = []
    changed_tip = copy.deepcopy(projection)
    changed_tip["local_ref_tip_commit"] = bundle["candidate_commit"]
    mutations.append(changed_tip)
    changed_git = copy.deepcopy(projection)
    changed_git["git_executable_sha256"] = "sha256:" + "f" * 64
    mutations.append(changed_git)
    authority = copy.deepcopy(projection)
    authority["capability_boundary"]["one_shot_enforced"] = True
    mutations.append(authority)
    nonbool = copy.deepcopy(projection)
    nonbool["capability_boundary"]["remote_publication_verified"] = 0
    mutations.append(nonbool)
    extra = copy.deepcopy(projection)
    extra["repo"] = str(bundle["repo"])
    mutations.append(extra)
    for mutated in mutations:
        with pytest.raises(git_lineage.HoldoutGitLineageError):
            _validate(
                mutated,
                repo=bundle["repo"],
                protocol=bundle["protocol"],
                states=bundle["states"],
            )


def test_missing_or_nonancestral_declared_commit_fails_closed(
    bundle: dict[str, Any],
) -> None:
    missing = copy.deepcopy(bundle["states"][:1])
    missing[0]["evidence"]["declared_commit"] = "f" * 40
    missing[0]["declared_commit_chain"][0] = "f" * 40
    with pytest.raises(git_lineage.HoldoutGitLineageError, match="git_command"):
        _verify(
            bundle["repo"],
            protocol=bundle["protocol"],
            states=missing,
            local_ref=bundle["local_ref"],
        )

    _git(bundle["repo"], "switch", "--detach", bundle["candidate_commit"])
    sibling = _commit(bundle["repo"], "unpublished sibling")
    _git(bundle["repo"], "switch", "evidence")
    sibling_state = holdout_protocol.initialize_state(
        bundle["protocol"],
        declared_preregistration_commit=sibling,
    )
    with pytest.raises(
        git_lineage.HoldoutGitLineageError,
        match="local_ref",
    ):
        _verify(
            bundle["repo"],
            protocol=bundle["protocol"],
            states=[sibling_state],
            local_ref=bundle["local_ref"],
        )


def test_state_chain_forks_and_initial_digest_drift_are_rejected(
    bundle: dict[str, Any],
) -> None:
    digest_drift = copy.deepcopy(bundle["states"][:1])
    digest_drift[0]["evidence"]["artifact_digest"] = "sha256:" + "f" * 64
    fork = copy.deepcopy(bundle["states"][:2])
    fork[1]["declared_commit_chain"][0] = bundle["states"][1][
        "declared_commit_chain"
    ][1]

    for states in (digest_drift, fork):
        with pytest.raises(git_lineage.HoldoutGitLineageError):
            _verify(
                bundle["repo"],
                protocol=bundle["protocol"],
                states=states,
                local_ref=bundle["local_ref"],
            )


@pytest.mark.parametrize(
    "local_ref",
    [
        "--upload-pack=evil",
        "HEAD",
        "refs/heads/../evil",
        "refs/heads/a..b",
        "refs/heads/a.lock",
        "refs/heads/.hidden",
        _StringSubclass("refs/heads/evidence"),
    ],
)
def test_local_ref_is_closed_and_non_option_like(
    bundle: dict[str, Any], local_ref: Any
) -> None:
    with pytest.raises(git_lineage.HoldoutGitLineageError):
        _verify(
            bundle["repo"],
            protocol=bundle["protocol"],
            states=bundle["states"],
            local_ref=local_ref,
        )


def test_repo_must_be_exact_existing_git_toplevel(
    bundle: dict[str, Any], tmp_path: Path
) -> None:
    for repo in (
        str(bundle["repo"]),
        bundle["repo"] / ".git",
        tmp_path / "missing",
    ):
        with pytest.raises(git_lineage.HoldoutGitLineageError):
            _verify(
                repo,  # type: ignore[arg-type]
                protocol=bundle["protocol"],
                states=bundle["states"],
                local_ref=bundle["local_ref"],
            )


def test_ref_movement_during_verification_is_rejected(
    bundle: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    original = git_lineage._resolve_local_ref
    ref_calls = 0

    def moving(runtime: Any, repo: Path, ref: str) -> str:
        nonlocal ref_calls
        resolved = original(runtime, repo, ref)
        if ref == bundle["local_ref"]:
            ref_calls += 1
            if ref_calls == 2:
                return bundle["candidate_commit"]
        return resolved

    monkeypatch.setattr(git_lineage, "_resolve_local_ref", moving)
    with pytest.raises(git_lineage.HoldoutGitLineageError, match="changed"):
        _verify(
            bundle["repo"],
            protocol=bundle["protocol"],
            states=bundle["states"],
            local_ref=bundle["local_ref"],
        )


def test_git_adapter_never_fetches_writes_or_reads_evidence_blobs(
    bundle: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    original = git_lineage._run_git
    calls: list[tuple[str, ...]] = []

    def recording(
        runtime: Any,
        repo: Path,
        *args: str,
        check: bool = True,
    ) -> Any:
        calls.append(args)
        return original(runtime, repo, *args, check=check)

    monkeypatch.setattr(git_lineage, "_run_git", recording)
    _verify(
        bundle["repo"],
        protocol=bundle["protocol"],
        states=bundle["states"],
        local_ref=bundle["local_ref"],
    )
    forbidden = {
        "fetch",
        "pull",
        "push",
        "checkout",
        "switch",
        "reset",
        "commit",
        "add",
        "show",
        "cat-file",
    }
    assert all(not (forbidden & set(call)) for call in calls)


def test_git_environment_redirects_are_ignored(
    bundle: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trace_path = bundle["repo"].parent / "git-trace.log"
    monkeypatch.setenv("GIT_DIR", str(bundle["repo"] / "missing-git-dir"))
    monkeypatch.setenv("GIT_WORK_TREE", str(bundle["repo"] / "missing-worktree"))
    monkeypatch.setenv(
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        str(bundle["repo"] / "missing-objects"),
    )
    monkeypatch.setenv(
        "GIT_SHALLOW_FILE",
        str(bundle["repo"] / "missing-shallow-file"),
    )
    monkeypatch.setenv(
        "GIT_EXEC_PATH",
        str(bundle["repo"] / "missing-git-exec-path"),
    )
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.repositoryformatversion")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "999")
    monkeypatch.setenv("GIT_TRACE", str(trace_path))

    projection = _verify(
        bundle["repo"],
        protocol=bundle["protocol"],
        states=bundle["states"],
        local_ref=bundle["local_ref"],
    )
    assert projection["local_ref_tip_commit"] == bundle["local_ref_tip"]
    assert not trace_path.exists()


def test_inherited_path_cannot_substitute_the_bound_git_executable(
    bundle: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_dir = tmp_path / "fake-git"
    fake_dir.mkdir()
    fake_git = fake_dir / GIT_EXECUTABLE.name
    fake_git.write_bytes(b"not the bound Git executable\n")
    fake_git.chmod(0o700)
    monkeypatch.setenv("PATH", str(fake_dir) + os.pathsep + os.environ["PATH"])
    assert Path(shutil.which("git") or "").resolve() == fake_git.resolve()

    projection = _verify(
        bundle["repo"],
        protocol=bundle["protocol"],
        states=bundle["states"],
        local_ref=bundle["local_ref"],
    )
    assert projection["git_executable_sha256"] == GIT_EXECUTABLE_SHA256


def test_git_executable_requires_an_exact_absolute_path_and_expected_digest(
    bundle: dict[str, Any],
) -> None:
    bad_inputs = [
        {
            "git_executable": str(GIT_EXECUTABLE),
            "expected_git_executable_sha256": GIT_EXECUTABLE_SHA256,
        },
        {
            "git_executable": Path(GIT_EXECUTABLE.name),
            "expected_git_executable_sha256": GIT_EXECUTABLE_SHA256,
        },
        {
            "git_executable": GIT_EXECUTABLE,
            "expected_git_executable_sha256": "sha256:" + "f" * 64,
        },
    ]
    for runtime in bad_inputs:
        with pytest.raises(git_lineage.HoldoutGitLineageError):
            git_lineage.verify_git_lineage(
                bundle["repo"],
                **runtime,  # type: ignore[arg-type]
                protocol=bundle["protocol"],
                states=bundle["states"],
                local_ref=bundle["local_ref"],
            )


def test_git_executable_digest_is_rechecked_after_verification(
    bundle: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = git_lineage._git_executable_digest
    calls = 0

    def changed_after_binding(path: Path) -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            return original(path)
        return "sha256:" + "f" * 64

    monkeypatch.setattr(
        git_lineage,
        "_git_executable_digest",
        changed_after_binding,
    )
    with pytest.raises(
        git_lineage.HoldoutGitLineageError,
        match="changed_during_verification",
    ):
        _verify(
            bundle["repo"],
            protocol=bundle["protocol"],
            states=bundle["states"],
            local_ref=bundle["local_ref"],
        )


@pytest.mark.parametrize(
    "relative_path",
    ["info/grafts", "objects/info/alternates"],
)
def test_git_graph_overlays_are_rejected(
    bundle: dict[str, Any], relative_path: str
) -> None:
    overlay = bundle["repo"] / ".git" / Path(relative_path)
    overlay.parent.mkdir(parents=True, exist_ok=True)
    overlay.write_text("forged graph overlay\n", encoding="ascii")
    with pytest.raises(git_lineage.HoldoutGitLineageError, match="overlay"):
        _verify(
            bundle["repo"],
            protocol=bundle["protocol"],
            states=bundle["states"],
            local_ref=bundle["local_ref"],
        )


def test_corrupt_commit_graph_cannot_forge_ancestry(
    bundle: dict[str, Any],
) -> None:
    repo = bundle["repo"]
    candidate = bundle["candidate_commit"]
    _git(repo, "switch", "--orphan", "forged-unrelated")
    unrelated = _commit(repo, "unrelated state root")
    unrelated_state = holdout_protocol.initialize_state(
        bundle["protocol"],
        declared_preregistration_commit=unrelated,
    )
    local_ref = "refs/heads/forged-unrelated"

    with pytest.raises(
        git_lineage.HoldoutGitLineageError,
        match="ancestry",
    ):
        _verify(
            repo,
            protocol=bundle["protocol"],
            states=[unrelated_state],
            local_ref=local_ref,
        )

    _git(repo, "commit-graph", "write", "--reachable", "--no-changed-paths")
    graph_path = repo / ".git" / "objects" / "info" / "commit-graph"
    _forge_commit_graph_parent(
        graph_path,
        child=unrelated,
        forged_parent=candidate,
    )
    plain = subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "merge-base",
            "--is-ancestor",
            candidate,
            unrelated,
        ],
        check=False,
        capture_output=True,
    )
    if plain.returncode != 0:
        pytest.skip("installed Git rejects the forged commit graph")

    with pytest.raises(
        git_lineage.HoldoutGitLineageError,
        match="ancestry",
    ):
        _verify(
            repo,
            protocol=bundle["protocol"],
            states=[unrelated_state],
            local_ref=local_ref,
        )


def test_symbolic_local_ref_is_rejected(bundle: dict[str, Any]) -> None:
    symbolic_ref = "refs/heads/evidence-alias"
    _git(
        bundle["repo"],
        "symbolic-ref",
        symbolic_ref,
        bundle["local_ref"],
    )
    with pytest.raises(git_lineage.HoldoutGitLineageError, match="symbolic"):
        _verify(
            bundle["repo"],
            protocol=bundle["protocol"],
            states=bundle["states"],
            local_ref=symbolic_ref,
        )


def test_remote_tracking_ref_must_point_directly_to_commit(
    bundle: dict[str, Any],
) -> None:
    direct_ref = "refs/remotes/origin/evidence"
    _git(bundle["repo"], "update-ref", direct_ref, bundle["local_ref_tip"])
    projection = _verify(
        bundle["repo"],
        protocol=bundle["protocol"],
        states=bundle["states"],
        local_ref=direct_ref,
    )
    assert projection["local_ref_tip_commit"] == bundle["local_ref_tip"]
    assert projection["capability_boundary"]["remote_publication_verified"] is False

    tag_oid = _git(
        bundle["repo"],
        "tag",
        "-a",
        "lineage-tag",
        "-m",
        "annotated tag must not masquerade as a commit tip",
    )
    assert tag_oid == ""
    annotated_tag_oid = _git(
        bundle["repo"], "rev-parse", "refs/tags/lineage-tag"
    )
    _git(bundle["repo"], "update-ref", direct_ref, annotated_tag_oid)
    with pytest.raises(
        git_lineage.HoldoutGitLineageError,
        match="directly_to_commit",
    ):
        _verify(
            bundle["repo"],
            protocol=bundle["protocol"],
            states=bundle["states"],
            local_ref=direct_ref,
        )


def test_projection_rejects_mapping_and_state_container_subclasses(
    bundle: dict[str, Any],
) -> None:
    projection = _verify(
        bundle["repo"],
        protocol=bundle["protocol"],
        states=bundle["states"],
        local_ref=bundle["local_ref"],
    )
    with pytest.raises(git_lineage.HoldoutGitLineageError):
        _validate(
            _DictSubclass(projection),
            repo=bundle["repo"],
            protocol=bundle["protocol"],
            states=bundle["states"],
        )
    with pytest.raises(git_lineage.HoldoutGitLineageError):
        _verify(
            bundle["repo"],
            protocol=bundle["protocol"],
            states=tuple(bundle["states"]),
            local_ref=bundle["local_ref"],
        )

"""Read-only review inputs for an approver's own bridge event payload.

Run with ``python -m tools.bridge_review_basis --help``. This producer neither
emits an event nor grants approval, and cannot reconstruct a legacy voter's
missing attestation. CI and consensus remain separate checks in the adapter.
"""
from __future__ import annotations

import argparse
from collections.abc import Mapping
import json
from pathlib import Path
import re

from tools import bridge_approval_carry_forward as carry
from tools import pr_status_snapshot as status_snapshot


def _text(value: object, label: str) -> str:
    if (type(value) is not str or not value or len(value) > 512
            or value != value.strip() or any(ord(c) < 32 for c in value)):
        raise ValueError(f"invalid {label}")
    return value


def produce_review_basis(*, repo_root: Path, repo: str, pr_number: int,
                         task_id: str, base_ref: str, base_sha: str,
                         head: str, runner=None) -> dict:
    """Derive immutable Git content while bracketing it with live PR reads.

    ``runner`` is the existing snapshot test seam. The default uses actual gh
    reads on github.com; no caller-supplied snapshot or basis is accepted.
    The bridge task name is an input, subsequently bound by the event consumer.
    """
    if type(repo) is not str or re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo) is None:
        raise ValueError("repository must be OWNER/NAME")
    if type(pr_number) is not int or pr_number < 1:
        raise ValueError("PR number must be a positive integer")
    for label, value in (("base SHA", base_sha), ("head SHA", head)):
        if type(value) is not str or re.fullmatch(r"[0-9a-f]{40}", value) is None:
            raise ValueError(f"invalid exact {label}")
    _text(task_id, "task identity")
    _text(base_ref, "base ref")
    if not isinstance(repo_root, Path) or not repo_root.is_absolute() or ".." in repo_root.parts:
        raise ValueError("repository root must be an explicit absolute path")
    if runner is not None and not callable(runner):
        raise ValueError("snapshot runner must be callable")

    run = status_snapshot._github_ci_runner(
        runner if runner is not None else status_snapshot._run_command, repo=repo)

    def snapshot() -> Mapping:
        value = status_snapshot.build_pr_status_snapshot(
            pr_number=pr_number, repo=repo, expected_base_sha=base_sha,
            require_ci_evidence=False, runner=run)
        expected = dict(pr_number=pr_number, head_sha=head, base_ref=base_ref,
                        base_sha=base_sha, base_tip_sha=base_sha, state="OPEN",
                        url=f"https://github.com/{repo}/pull/{pr_number}")
        if not isinstance(value, Mapping) or any(
                type(value.get(key)) is not type(want) or value[key] != want
                for key, want in expected.items()):
            raise ValueError("current GitHub snapshot does not match review identity")
        return value

    initial = snapshot()
    git = carry._git_reader(repo_root)
    remote = git("config", "--get", "remote.origin.url").decode("utf-8", "strict").strip().casefold()
    if remote not in {f"https://github.com/{repo}".casefold(),
                      f"https://github.com/{repo}.git".casefold(),
                      f"git@github.com:{repo}.git".casefold()}:
        raise ValueError("Git origin does not match review repository")
    git("check-ref-format", "refs/heads/" + base_ref)
    proof = carry.derive_content_identity(
        repo_root=repo_root, old_base=base_sha, old_head=head,
        new_base=base_sha, new_head=head)
    paths = sorted(bytes.fromhex(row["path_hex"]).decode("utf-8", "strict")
                   for row in proof["old_entries"])
    if type(initial.get("changed_paths")) is not list or sorted(initial["changed_paths"]) != paths:
        raise ValueError("GitHub changed paths do not match immutable Git diff")
    final = snapshot()
    if final.get("changed_paths") != initial["changed_paths"]:
        raise ValueError("GitHub changed paths drifted during review derivation")
    if carry._fleet_git_authority() != git.authority:
        raise ValueError("Git executable authority drifted during review derivation")
    basis = dict(schema="wd.bridge-reviewed-basis.v1", repository=repo,
                 pr_number=pr_number, task_id=task_id, base_ref=base_ref,
                 head=head, base_sha=base_sha, merge_base=proof["old_merge_base"],
                 diff_sha256=proof["old_diff_sha256"], patch_id=proof["old_patch_id"])
    return dict(head=head, pr=pr_number, review_basis=basis,
                review_basis_sha256=carry._canonical_sha(basis))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--pr", dest="pr_number", type=int, required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--base-ref", required=True)
    parser.add_argument("--base-sha", required=True)
    parser.add_argument("--head", required=True)
    args = parser.parse_args(argv)
    try:
        payload = produce_review_basis(**vars(args))
    except (ValueError, OSError) as exc:
        print(json.dumps(dict(status="HOLD", reason=str(exc)), sort_keys=True))
        return 2
    print(json.dumps(payload, sort_keys=True, ensure_ascii=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

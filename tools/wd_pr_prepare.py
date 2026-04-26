# SPDX-License-Identifier: Apache-2.0
"""wd_pr_prepare — prepare a Phase 8.5 / Phase 9 branch for PR.

For one branch (R7.5, Session A/B/C/D, or Phase 9):

1. Verify the branch exists locally and is currently checked out.
2. Verify it is a fast-forward descendant of the PR base (origin/main).
3. Verify no uncommitted changes.
4. Run the targeted test set for that branch (--execute) or print
   the intent (--dry-run).
5. Push the branch to origin (--execute) or print the intent.
6. Read the session state.json to harvest completed_phases /
   lifecycle_status / latest_green_commit.
7. Generate a structured PR body from state + git diff stats.
8. Run gh pr create (--execute) or print the intent.

Usage:
  python tools/wd_pr_prepare.py --branch phase8.5/vector-chaos --dry-run
  python tools/wd_pr_prepare.py --branch phase9/autonomy-fabric --execute

Exit code 0 on success, 1 on any pre-condition failure.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


ROOT = Path(__file__).resolve().parent.parent

# PR base is always origin/main. Branches that are not yet rebased on
# origin/main MUST be rebased before this script will accept them.
BASE = "origin/main"


@dataclass(frozen=True)
class BranchSpec:
    branch: str
    test_globs: tuple[str, ...]
    state_path: str
    title: str


BRANCHES: dict[str, BranchSpec] = {
    "phase8.5/vector-chaos": BranchSpec(
        branch="phase8.5/vector-chaos",
        test_globs=("tests/test_vector_*resilience*.py",),
        state_path="docs/runs/phase8_5_vector_chaos_session_state.json",
        title="R7.5 — Vector Writer Resilience",
    ),
    "phase8.5/curiosity-organ": BranchSpec(
        branch="phase8.5/curiosity-organ",
        test_globs=(
            "tests/test_curiosity*.py",
            "tests/test_gap_miner*.py",
        ),
        state_path="docs/runs/phase8_5_curiosity_session_state.json",
        title="Session A — Curiosity Organ (gap_miner)",
    ),
    "phase8.5/self-model-layer": BranchSpec(
        branch="phase8.5/self-model-layer",
        test_globs=(
            "tests/test_self_model*.py",
            "tests/test_reflective*.py",
        ),
        state_path="docs/runs/phase8_5_self_model_session_state.json",
        title="Session B — Self-Model Layer",
    ),
    "phase8.5/dream-curriculum": BranchSpec(
        branch="phase8.5/dream-curriculum",
        test_globs=("tests/test_dream*.py",),
        state_path="docs/runs/phase8_5_dream_session_state.json",
        title="Session C — Dream Pipeline",
    ),
    "phase8.5/hive-proposes": BranchSpec(
        branch="phase8.5/hive-proposes",
        test_globs=(
            "tests/test_meta_learner*.py",
            "tests/test_hive_proposes*.py",
        ),
        state_path="docs/runs/phase8_5_hive_session_state.json",
        title="Session D — The Hive Proposes",
    ),
    "phase9/autonomy-fabric": BranchSpec(
        branch="phase9/autonomy-fabric",
        test_globs=("tests/test_phase9_*.py",),
        state_path="docs/runs/phase9_autonomy_fabric_state.json",
        title="Phase 9 — Autonomy Fabric (16 phases)",
    ),
}


CROWN_JEWEL_PACKAGES = (
    "waggledance/core/autonomy",
    "waggledance/core/ir",
    "waggledance/core/capsules",
    "waggledance/core/vector_identity",
    "waggledance/core/world_model",
    "waggledance/core/conversation",
    "waggledance/core/identity",
    "waggledance/core/api_distillation",
    "waggledance/core/builder_lane",
    "waggledance/core/solver_synthesis",
    "waggledance/core/promotion",
    "waggledance/core/proposal_compiler",
    "waggledance/core/cross_capsule",
    "waggledance/core/ingestion",
    "waggledance/core/provider_plane",
    "waggledance/core/memory_tiers",
    "waggledance/core/hex_topology",
    "waggledance/core/local_intelligence",
    "waggledance/core/learning",
    "waggledance/core/projections",
    "waggledance/core/magma",
)


def run(cmd: Sequence[str], check: bool = False
        ) -> subprocess.CompletedProcess:
    """Wrapper for subprocess.run. shell=False, capture stdout+stderr."""
    return subprocess.run(
        list(cmd),
        check=check,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def current_branch() -> str:
    r = run(["git", "branch", "--show-current"])
    return r.stdout.strip()


def verify_branch_exists(branch: str) -> bool:
    r = run(["git", "rev-parse", "--verify", f"refs/heads/{branch}"])
    return r.returncode == 0


def is_rebased_on_base(base: str, branch: str) -> bool:
    """True if `base` is an ancestor of `branch` (i.e. branch is up to
    date relative to base or ahead of it)."""
    r = run(["git", "merge-base", "--is-ancestor", base, branch])
    return r.returncode == 0


def has_uncommitted_changes() -> bool:
    r = run(["git", "status", "--porcelain"])
    return bool(r.stdout.strip())


def run_targeted_tests(test_globs: Sequence[str]
                         ) -> tuple[bool, str]:
    """Run pytest for the targeted globs. Return (success, summary)."""
    cmd = ["python", "-m", "pytest", *test_globs, "-q", "--tb=no"]
    r = run(cmd)
    output = (r.stdout or "") + (r.stderr or "")
    tail = "\n".join(output.strip().split("\n")[-5:])
    return r.returncode == 0, tail


def push_branch(branch: str) -> tuple[bool, str]:
    r = run(["git", "push", "-u", "origin", branch])
    return r.returncode == 0, (r.stdout + r.stderr).strip()


def load_state(path: Path) -> dict:
    """Read session state.json safely. Returns empty dict on any failure."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError:
        return {}
    except OSError:
        return {}


def short_sha(ref: str) -> str:
    r = run(["git", "rev-parse", "--short", ref])
    return r.stdout.strip()


def diff_stat_tail(base: str, branch: str, n: int = 5) -> str:
    r = run(["git", "diff", "--stat", f"{base}..{branch}"])
    lines = r.stdout.strip().split("\n")
    if not lines or lines == [""]:
        return "(no diff)"
    return "\n".join(lines[-n:])


def crown_jewel_paths_affected(base: str, branch: str) -> list[str]:
    r = run(["git", "diff", "--name-only", f"{base}..{branch}"])
    files = [f for f in r.stdout.strip().split("\n") if f]
    affected: set[str] = set()
    for f in files:
        for pkg in CROWN_JEWEL_PACKAGES:
            if f.startswith(pkg + "/"):
                affected.add(pkg)
                break
    return sorted(affected)


def generate_pr_body(
    spec: BranchSpec,
    base: str,
    test_summary: str,
    state: dict,
) -> str:
    branch = spec.branch
    base_sha = short_sha(base) or "(unknown)"
    branch_sha = short_sha(branch) or "(unknown)"
    diff_tail = diff_stat_tail(base, branch)
    crown = crown_jewel_paths_affected(base, branch)

    completed = (
        state.get("completed_phases")
        or state.get("completed_components")
        or []
    )
    if completed:
        # Cap at 20 entries to keep PR body readable
        completed_lines = "\n".join(f"- {entry}" for entry in completed[:20])
        if len(completed) > 20:
            completed_lines += (
                f"\n- _(... and {len(completed) - 20} more — see "
                f"`{spec.state_path}` for full list)_"
            )
    else:
        completed_lines = (
            "- (state file did not list completed_phases / "
            "completed_components)"
        )

    lifecycle = (
        state.get("lifecycle_status")
        or state.get("session_status")
        or "(not recorded)"
    )
    latest_green = (
        state.get("latest_green_commit_hash")
        or state.get("latest_green_commit")
        or branch_sha
    )

    crown_block = (
        "\n".join(f"- `{p}`" for p in crown)
        if crown else "- (none)"
    )

    return (
        f"## Summary\n\n"
        f"{completed_lines}\n\n"
        f"## Test results\n\n"
        f"```\n{test_summary or '(no test output captured)'}\n```\n\n"
        f"## Files changed\n\n"
        f"```\n{diff_tail}\n```\n\n"
        f"## Crown-jewel paths affected\n\n"
        f"{crown_block}\n\n"
        f"## State machine: lifecycle status\n\n"
        f"{lifecycle}\n\n"
        f"## Branch base\n\n"
        f"`{base}` @ `{base_sha}`\n\n"
        f"## Latest green commit\n\n"
        f"`{latest_green}`\n\n"
        f"_PR body generated by `tools/wd_pr_prepare.py` for branch "
        f"`{branch}`._\n"
    )


def call_gh_pr_create(branch: str, title: str, body: str
                       ) -> tuple[bool, str]:
    cmd = [
        "gh", "pr", "create",
        "--base", "main",
        "--head", branch,
        "--title", title,
        "--body", body,
    ]
    r = run(cmd)
    return r.returncode == 0, (r.stdout + r.stderr).strip()


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="Prepare a Phase 8.5 / Phase 9 branch for PR.",
    )
    ap.add_argument(
        "--branch", required=True,
        choices=sorted(BRANCHES.keys()),
        help="The branch to prepare.",
    )
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument(
        "--dry-run", action="store_true",
        help="Print what would be done; do not push or open PR.",
    )
    g.add_argument(
        "--execute", action="store_true",
        help="Actually push the branch and run gh pr create.",
    )
    return ap


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    spec = BRANCHES[args.branch]
    is_dry = args.dry_run

    mode = "dry-run" if is_dry else "EXECUTE"
    print(f"=== wd_pr_prepare: {spec.branch} ({mode}) ===")

    # 1. Branch exists locally
    if not verify_branch_exists(spec.branch):
        print(f"  ERR: branch {spec.branch!r} does not exist locally")
        return 1
    print(f"  ok:  branch {spec.branch!r} exists locally")

    # 2. Currently checked out on this branch
    on = current_branch()
    if on != spec.branch:
        print(
            f"  ERR: not currently on {spec.branch!r} "
            f"(currently on {on!r})"
        )
        print(f"       run: git checkout {spec.branch}")
        return 1
    print(f"  ok:  currently on {spec.branch!r}")

    # 3. Rebased onto origin/main
    if not is_rebased_on_base(BASE, spec.branch):
        print(
            f"  ERR: branch {spec.branch!r} is NOT a descendant of "
            f"{BASE}"
        )
        print(f"       run: git fetch origin && git rebase {BASE}")
        return 1
    print(f"  ok:  branch is descendant of {BASE}")

    # 4. No uncommitted changes
    if has_uncommitted_changes():
        print("  ERR: uncommitted changes present")
        print("       run: git status; commit or stash them first")
        return 1
    print("  ok:  no uncommitted changes")

    # 5. Targeted tests (EXECUTE runs them; dry-run prints intent)
    if is_dry:
        print(
            f"  DRY-RUN: would run: python -m pytest "
            f"{' '.join(spec.test_globs)} -q --tb=no"
        )
        test_summary = "(skipped in dry-run)"
    else:
        print(
            f"  running targeted tests: {' '.join(spec.test_globs)}"
        )
        test_ok, test_summary = run_targeted_tests(spec.test_globs)
        print(test_summary)
        if not test_ok:
            print("  ERR: targeted tests failed; aborting")
            return 1
        print("  ok:  targeted tests green")

    # 6. Push branch (EXECUTE only)
    if is_dry:
        print(f"  DRY-RUN: would run: git push -u origin {spec.branch}")
    else:
        ok, push_out = push_branch(spec.branch)
        if not ok:
            print(f"  ERR: git push failed:\n{push_out}")
            return 1
        print(f"  ok:  pushed origin/{spec.branch}")

    # 7. Read state file
    state_path = ROOT / spec.state_path
    state = load_state(state_path)
    print(
        f"  ok:  read state file {spec.state_path} "
        f"({len(state)} top-level keys)"
    )

    # 8. Generate PR body
    body = generate_pr_body(spec, BASE, test_summary, state)
    print()
    print("--- generated PR body ---")
    print(body)
    print("--- end PR body ---")
    print()

    # 9. gh pr create (EXECUTE only)
    if is_dry:
        print(
            f"  DRY-RUN: would run: gh pr create --base main "
            f"--head {spec.branch} --title {spec.title!r} "
            f"--body <generated>"
        )
        print("  DRY-RUN: complete")
        return 0

    ok, gh_out = call_gh_pr_create(spec.branch, spec.title, body)
    if not ok:
        print(f"  ERR: gh pr create failed:\n{gh_out}")
        return 1
    print(f"  ok:  gh pr create completed")
    print(gh_out)
    return 0


if __name__ == "__main__":
    sys.exit(main())

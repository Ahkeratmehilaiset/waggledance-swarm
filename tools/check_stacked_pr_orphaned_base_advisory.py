# SPDX-License-Identifier: BUSL-1.1
"""Read-only ADVISORY: flag open PRs whose base is ORPHANED or whose head would
CONFLICT when merged into current main.

Rationale (wd/ops/stacked-pr-freeze-guard-advisory, RCO-1 stacked-chain-safety):
``report_open_pr_stale_base_queue.py`` already flags ``baseRefOid != current
main SHA`` (stale base). But "stale" conflates two very different states:

  * BEHIND-but-mergeable: the base SHA is still an ANCESTOR of current main
    (the PR just needs main merged in / a routine rebase), and
  * ORPHANED: the base SHA is NOT an ancestor of current main -- e.g. the base
    branch was squash-merged (#1306 -> ca81aff0), so the base commit no longer
    exists in main's history. Such a PR is NOT safely mergeable as-is; it must
    be retargeted/rebased onto current main first (the #1307 hazard).

This tool adds that distinction (an is-ancestor check the stale-base report does
not do), plus a merge-tree CONFLICT check for base-on-main PRs. It is a
STANDALONE, READ-ONLY, ADVISORY/WARN-only diagnostic: it never blocks (always
exits 0), is NOT wired into the denylist gate-checkers, and authorizes nothing.

Scope-widening (a PR's diff growing beyond its declared scope) is intentionally
NOT covered here -- it needs a structured declared-scope source (PR-body or
manifest convention) that does not exist yet; see the bridge handoff for that
deferred gap.

The core ``classify_open_pr_base_hazards`` is offline/deterministic and pure:
git ancestry / merge-tree are injected as callables, so it is fully unit-tested
without a network or repo. The CLI supplies real git/gh implementations.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
from typing import Any, Callable, Iterable, Mapping, Sequence

DEFAULT_REPO = "Ahkeratmehilaiset/waggledance-swarm"

# Injected git predicates (so the core stays pure/offline-testable).
# IsAncestor(ancestor_sha, descendant_sha) -> True if ancestor is in descendant's history.
IsAncestor = Callable[[str, str], bool]
# MergeTreeConflict(base_sha, head_sha) -> True if merging head into base conflicts.
MergeTreeConflict = Callable[[str, str], bool]


def classify_open_pr_base_hazards(
    prs: Iterable[Mapping[str, Any]],
    main_sha: str,
    is_ancestor: IsAncestor,
    merge_tree_conflict: MergeTreeConflict | None = None,
) -> list[dict[str, Any]]:
    """Return one advisory per open PR with a base/merge hazard.

    Read-only/advisory: never raises on a hazard, never blocks. Per PR:
      * base on current main (baseRefName == 'main' or baseRefOid == main_sha):
        optionally checked for a merge-tree conflict vs main.
      * base NOT on main (stacked): ORPHANED if its baseRefOid is not an ancestor
        of main (base branch squash-merged / rewritten) -> must retarget.
    """
    main_sha = str(main_sha or "").strip()
    advisories: list[dict[str, Any]] = []
    for pr in prs:
        if not isinstance(pr, Mapping):
            continue
        number = pr.get("number")
        head_ref = str(pr.get("headRefName", "") or "")
        head_oid = str(pr.get("headRefOid", "") or "").strip()
        base_ref = str(pr.get("baseRefName", "") or "")
        base_oid = str(pr.get("baseRefOid", "") or "").strip()
        base_on_main = base_ref == "main" or (
            bool(main_sha) and base_oid == main_sha
        )
        hazards: list[str] = []
        if not base_on_main:
            # Stacked / non-main base: orphaned if base SHA not in main history.
            if base_oid and main_sha and not is_ancestor(base_oid, main_sha):
                hazards.append("orphaned_base")
        elif merge_tree_conflict is not None and head_oid and main_sha:
            # Base on main but possibly behind: would the head conflict on merge?
            if merge_tree_conflict(main_sha, head_oid):
                hazards.append("merge_tree_conflict_vs_main")
        if not hazards:
            continue
        advisories.append(
            {
                "number": number,
                "headRefName": head_ref,
                "baseRefName": base_ref,
                "baseRefOid": base_oid,
                "hazards": hazards,
                "advice": (
                    "retarget/rebase onto current main before merge"
                    if "orphaned_base" in hazards
                    else "rebase onto current main to resolve conflict before merge"
                ),
                "severity": "advisory",
            }
        )
    return advisories


def _git_is_ancestor(ancestor_sha: str, descendant_sha: str) -> bool:
    """True if ancestor_sha is an ancestor of descendant_sha (read-only git)."""
    try:
        result = subprocess.run(
            ["git", "merge-base", "--is-ancestor", ancestor_sha, descendant_sha],
            capture_output=True,
            text=True,
        )
    except Exception:
        # Unknown -> fail toward FLAGGING (treat as not-ancestor = orphaned) so a
        # missing/un-fetched base is surfaced for review, never silently cleared.
        return False
    return result.returncode == 0


def _git_merge_tree_conflict(base_sha: str, head_sha: str) -> bool:
    """True if merging head_sha into base_sha conflicts (read-only git merge-tree)."""
    try:
        result = subprocess.run(
            ["git", "merge-tree", "--write-tree", base_sha, head_sha],
            capture_output=True,
            text=True,
        )
    except Exception:
        return False  # unknown -> do not assert a conflict (advisory only)
    # git merge-tree --write-tree exits non-zero on conflict (and prints markers).
    if result.returncode != 0:
        return True
    return "<<<<<<<" in (result.stdout or "")


def _open_prs(repo: str) -> list[dict[str, Any]]:
    try:
        result = subprocess.run(
            [
                "gh",
                "pr",
                "list",
                "--repo",
                repo,
                "--state",
                "open",
                "--json",
                "number,headRefName,headRefOid,baseRefName,baseRefOid",
                "--limit",
                "200",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        data = json.loads(result.stdout or "[]")
        return [dict(item) for item in data] if isinstance(data, list) else []
    except Exception:
        return []


def _current_main_sha(repo: str) -> str:
    try:
        result = subprocess.run(
            [
                "gh",
                "api",
                f"repos/{repo}/commits/main",
                "--jq",
                ".sha",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        return (result.stdout or "").strip()
    except Exception:
        return ""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only ADVISORY: flag open PRs with an ORPHANED base (base SHA "
            "not an ancestor of current main, e.g. base branch squash-merged) "
            "or a merge-tree conflict vs main. WARN-only; never blocks "
            "(always exits 0)."
        )
    )
    parser.add_argument("--repo", default=DEFAULT_REPO)
    parser.add_argument(
        "--main-sha",
        default=None,
        help="Current main SHA. If omitted, fetched via gh.",
    )
    parser.add_argument(
        "--no-merge-tree",
        action="store_true",
        help="Skip the (heavier) merge-tree conflict check for base-on-main PRs.",
    )
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    main_sha = (args.main_sha or "").strip() or _current_main_sha(args.repo)
    prs = _open_prs(args.repo)
    merge_tree = None if args.no_merge_tree else _git_merge_tree_conflict
    advisories = classify_open_pr_base_hazards(
        prs, main_sha, _git_is_ancestor, merge_tree
    )
    if args.json:
        print(
            json.dumps(
                {
                    "main_sha": main_sha,
                    "open_pr_count": len(prs),
                    "advisories": advisories,
                    "advisory_count": len(advisories),
                },
                indent=2,
                sort_keys=True,
            )
        )
    elif not advisories:
        print("OK: no open PRs with orphaned base / merge-tree conflict vs main.")
    else:
        print(
            f"ADVISORY: {len(advisories)} open PR(s) with a base/merge hazard "
            "(read-only; not a block):"
        )
        for adv in advisories:
            print(
                f"  #{adv['number']} {adv['headRefName']} "
                f"{','.join(adv['hazards'])} (base={adv['baseRefName']}) "
                f"-> {adv['advice']}"
            )
    # Advisory/WARN-only: never a hard block.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

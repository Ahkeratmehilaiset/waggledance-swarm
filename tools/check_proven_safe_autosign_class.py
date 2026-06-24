#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""P1 proven-safe auto-sign class checker (RFC P1, PR 2/3) — DORMANT / UNWIRED.

Certifies whether a PR is in the narrow "proven-safe" class defined by
``docs/architecture/P1_PROVEN_SAFE_AUTOSIGN_CLASS_V1.md``. When (and only when)
the gate-wiring PR (#3) is operator-signed, the gate may consult this checker to
waive ONLY the per-PR operator signature for an in-class PR. **This file is
consulted by nothing yet** — it is pure, testable logic.

FAIL-CLOSED: a PR is IN-CLASS only if EVERY predicate A–F holds. Any path outside
the safe set, any C/D/E pattern, any F exclusion, empty input, or any parse
error/ambiguity → NOT in class → per-PR operator signature required. The checker
NEVER default-allows on uncertainty.

In-class predicates (all must hold):
  A  every changed path is in tests/** | docs/runs/** | docs/benchmarks/**,
     OR is an ADDITIVE metrics counter (pure-addition, new counter symbol).
  B  read-only / default-OFF (no removed/edited lines outside the safe roots).
  C  no claim_safe flip.
  D  no authority-flag edit (gate_skip/solver_call/receipt_required/clinical_decision).
  E  no control-plane / runtime-behavior change.
  F  hard exclusions: gate/charter/denylist, .agent-bridge/bin/**,
     .github/workflows/**, requirements*/lockfiles, AGENTS.md/CLAUDE.md/
     master-prompts, Rule-10 surface, anything the charter denylists.

    python tools/check_proven_safe_autosign_class.py --changed-from-git origin/main --json
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Sequence

SAFE_ROOTS = ("tests/", "docs/runs/", "docs/benchmarks/")

# F — explicit P1 hard exclusions (in addition to the charter denylist).
F_EXCLUDE_SUBSTRINGS = (
    ".agent-bridge/bin/",
    ".github/workflows/",
    "idle_consensus_charter",
    "idle_consensus_auto_merge",
    "check_bridge_changes_requested",
    "check_rco_pass_present",
    "check_proven_safe_autosign_class",   # the checker itself (anti-widening)
    "bridge_identity_registry",
    "invoke-bridgemergedriver",
    "stage2_cutover",
    "human_approval",
)
F_EXCLUDE_BASENAMES = frozenset(
    {"agents.md", "claude.md", "requirements.txt", "requirements.lock.txt",
     "pyproject.toml", "poetry.lock"}
)
F_EXCLUDE_BASENAME_SUBSTR = ("requirements", "master_prompt", "lock")

# C/D/E — risky tokens scanned in changed lines (fail-closed).
AUTHORITY_FLAGS = ("gate_skip", "solver_call", "receipt_required",
                   "clinical_decision", "consensus_grade", "claim_safe")
CONTROL_PLANE_TOKENS = ("def route", "routing", "control_plane", "dispatch(",
                        "merge(", "build_consensus", "rco_pass", "operator_sign")

# A COMPLETE single-line metric definition: `NAME = Counter|Gauge|Histogram|
# Summary(<balanced args, one level of nesting>)` + optional trailing comment,
# anchored to the WHOLE line so nothing may trail the call (no `; os.system(...)`,
# no concatenated statement). Multi-line defs conservatively fall to operator-sign.
_METRIC_DEF_FULL = re.compile(
    r"^[A-Za-z_]\w*\s*=\s*(?:Counter|Gauge|Histogram|Summary)\s*"
    r"\([^()]*(?:\([^()]*\)[^()]*)*\)\s*(?:#.*)?$"
)
_METRIC_USAGE = re.compile(r"\.(inc|dec|observe|set|labels|time|count_exceptions)\s*\(")


def _norm(path: str) -> str:
    return path.replace("\\", "/").strip().lstrip("./")


def _is_f_excluded(path: str) -> str | None:
    """Return an exclusion reason if the path is hard-excluded (F), else None."""
    p = _norm(path).lower()
    base = p.rsplit("/", 1)[-1]
    for sub in F_EXCLUDE_SUBSTRINGS:
        if sub in p:
            return f"F exclusion (path contains '{sub}')"
    if base in F_EXCLUDE_BASENAMES:
        return f"F exclusion (basename '{base}')"
    for sub in F_EXCLUDE_BASENAME_SUBSTR:
        if sub in base:
            return f"F exclusion (basename contains '{sub}')"
    return None


def _in_safe_roots(path: str) -> bool:
    return _norm(path).startswith(SAFE_ROOTS)


def _is_additive_metrics_counter(change: dict) -> bool:
    """A changed source file qualifies ONLY as a pure-additive new metric counter.

    STRICT (fail-closed): zero removed lines, at least one added line, and EVERY
    non-blank/non-comment added line must be a COMPLETE single-line metric
    DEFINITION (``NAME = Counter|Gauge|Histogram|Summary(...)``) — matched whole-
    line so nothing may trail the call. Any standalone statement, multi-statement
    line (``;``), metric usage (``.inc()``/``.labels()`` — a hot-path change),
    authority flag, or multi-line def → NOT in class. A line is NEVER admitted by
    a trailing-character heuristic (the prior fail-open: rco-1/rco-2/tools #1384).
    """
    if change.get("removed"):
        return False  # any removal/edit of an existing line -> not purely additive
    added = change.get("added") or []
    if not added:
        return False
    saw_metric = False
    for line in added:
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if ";" in s:
            return False  # multiple statements on one line
        if _METRIC_USAGE.search(s):
            return False  # .inc()/.labels()/... = hot-path runtime change
        if any(flag in s.lower() for flag in AUTHORITY_FLAGS):
            return False
        if _METRIC_DEF_FULL.match(s):
            saw_metric = True
            continue
        return False  # not a complete metric definition -> reject (no heuristic)
    return saw_metric


def _scan_tokens(changes: Sequence[dict], tokens: Sequence[str]) -> str | None:
    for ch in changes:
        for line in (ch.get("added") or []) + (ch.get("removed") or []):
            low = line.lower()
            for tok in tokens:
                if tok in low:
                    return tok
    return None


def classify_change(changes: Sequence[dict], *, charter=None, diff_text: str = "",
                    require_charter: bool = True) -> dict:
    """Pure classifier. ``changes``: [{path, added[], removed[]}]. Fail-closed.

    Returns {in_class: bool, decision: 'auto_sign'|'operator_sign', reason}.
    """
    def out_of_class(reason: str) -> dict:
        return {"in_class": False, "decision": "operator_sign", "reason": reason}

    if not changes:
        return out_of_class("empty change set (ambiguous) -> operator sign")

    paths = [_norm(c.get("path", "")) for c in changes]
    if any(not p for p in paths):
        return out_of_class("missing path in change set -> operator sign")

    # The authoritative charter is REQUIRED for predicate F — the explicit F-list
    # + A alone cannot catch arbitrary code under an allowlisted path, so a
    # MISSING charter FAILS CLOSED rather than falling back to the narrower
    # hardcoded list (rco-1 #1384 charter=None fail-open fix).
    if require_charter and charter is None:
        return out_of_class("charter unavailable -> operator sign (F requires the charter)")

    # F via the authoritative charter (denylist / diff-content), when supplied.
    if charter is not None:
        try:
            from waggledance.core import idle_consensus_charter as _c
            if not getattr(_c.evaluate_paths(charter, paths), "allowed", False):
                return out_of_class("F: charter denylists a changed path")
            if diff_text and not getattr(
                _c.evaluate_diff_content(charter, diff_text), "allowed", False
            ):
                return out_of_class("F: charter flags diff content")
        except Exception as exc:  # charter unusable -> fail-closed
            return out_of_class(f"charter check failed ({exc}) -> operator sign")

    # F + A per file.
    for ch in changes:
        path = _norm(ch.get("path", ""))
        excl = _is_f_excluded(path)
        if excl:
            return out_of_class(f"{excl}: {path}")
        if _in_safe_roots(path):
            continue
        if _is_additive_metrics_counter(ch):
            continue
        return out_of_class(f"A: path not in proven-safe class: {path}")

    # C/D/E — scan risky tokens across all changed lines (fail-closed).
    hit = _scan_tokens(changes, AUTHORITY_FLAGS)
    if hit:
        return out_of_class(f"C/D: authority/claim flag touched ('{hit}')")
    hit = _scan_tokens(changes, CONTROL_PLANE_TOKENS)
    if hit:
        return out_of_class(f"E: control-plane/runtime token touched ('{hit}')")

    return {
        "in_class": True,
        "decision": "auto_sign",
        "reason": "all predicates A-F hold (proven-safe class)",
    }


# --- git layer ---------------------------------------------------------------

def _run_git(args: list[str], cwd: str) -> tuple[int, str]:
    try:
        p = subprocess.run(["git", *args], cwd=cwd, capture_output=True,
                           encoding="utf-8", errors="replace")
        return p.returncode, p.stdout or ""
    except OSError:
        return 1, ""


def gather_changes(base: str, repo_root: str) -> tuple[list[dict], str]:
    """Build the change-list + raw diff from git diff BASE...HEAD. ([], '') on error."""
    code, names = _run_git(["diff", "--name-only", f"{base}...HEAD"], repo_root)
    if code != 0:
        return [], ""
    files = [ln.strip() for ln in names.splitlines() if ln.strip()]
    changes: list[dict] = []
    diff_all = []
    for f in files:
        c2, d = _run_git(["diff", f"{base}...HEAD", "--", f], repo_root)
        diff_all.append(d)
        added, removed = [], []
        for ln in d.splitlines():
            if ln.startswith("+") and not ln.startswith("+++"):
                added.append(ln[1:])
            elif ln.startswith("-") and not ln.startswith("---"):
                removed.append(ln[1:])
        changes.append({"path": f, "added": added, "removed": removed})
    return changes, "\n".join(diff_all)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--changed-from-git", metavar="BASE", required=True)
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repo_root = str(Path(args.repo_root))
    changes, diff_text = gather_changes(args.changed_from_git, repo_root)
    charter = None
    try:
        sys.path.insert(0, repo_root)
        from waggledance.core import idle_consensus_charter as _c
        charter = _c.load_charter()
    except Exception:
        charter = None  # classify_change fails closed when charter is needed
    result = classify_change(changes, charter=charter, diff_text=diff_text)
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(f"{result['decision'].upper()}: {result['reason']}")
    return 0 if result["in_class"] else 3


if __name__ == "__main__":
    raise SystemExit(main())

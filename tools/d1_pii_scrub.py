#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Automate the SAFE, reversible steps of the D1 PII history scrub.

This tool implements everything in docs/operations/D1_PII_SCRUB_RUNBOOK.md
EXCEPT the irreversible force-push, which stays operator-only per CLAUDE.md
rule 9. The destructive cutover is never run here: the tool prints the exact
operator command and exits.

Safety contract (non-negotiable):
  * Never runs ``git push`` (force or otherwise).
  * Never echoes the real PII values to stdout/stderr nor writes them to a
    tracked file. Values are read from configs/settings.yaml at runtime; the
    git filter-repo replacement file is built in the OS temp dir (outside the
    repo); only counts and redaction placeholders are reported.
  * The dry-run operates on a throwaway clone in a temp dir, never the live
    repo, and cleans up after itself.

Modes (``--mode`` or positional subcommand):
  detect    Read-only. Report which PII fields are present at HEAD and how
            many commits in full history still match each value. JSON output.
  plan      Build the filter-repo --replace-text mapping file in a temp path.
            Print the path and mapping count, never the contents.
  dry-run   Clone the repo to a temp dir, run filter-repo --replace-text in
            the clone, verify zero residual matches, optionally run a quick
            pytest smoke, report pass/fail JSON, then clean up. Never pushes.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

# The three operator PII fields under ``facts:`` in configs/settings.yaml that
# audit D1 (H43/H44) flagged. Each maps to a stable redaction placeholder used
# in the filter-repo replacement mapping.
PII_FIELDS: dict[str, str] = {
    "y_tunnus": "REDACTED_BUSINESS_ID",
    "owner": "REDACTED_OWNER",
    "business_name": "REDACTED_BUSINESS",
}

# A value is considered already-scrubbed if it is empty or already a
# REDACTED_* placeholder.
_REDACTED_PREFIX = "REDACTED_"

DEFAULT_SETTINGS = ROOT / "configs" / "settings.yaml"
SMOKE_TEST = "tests/test_hex_mesh.py"

OPERATOR_FORCE_PUSH_COMMAND = "git push --force-with-lease origin main"
RUNBOOK = "docs/operations/D1_PII_SCRUB_RUNBOOK.md"


class FilterRepoMissing(RuntimeError):
    """Raised when the git-filter-repo CLI is not available."""


def _read_facts(settings_path: Path) -> dict[str, str]:
    """Extract the ``facts:`` block string values without a YAML dependency.

    Only the three D1 fields are parsed, and only their raw string values are
    returned. The parser is intentionally minimal: it reads the top-level
    ``facts:`` mapping and returns ``field -> value`` for the known PII keys.
    Values are NOT logged anywhere by this function.
    """
    text = settings_path.read_text(encoding="utf-8")
    facts: dict[str, str] = {}
    in_facts = False
    for raw_line in text.splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        # Top-level key (no leading indentation).
        if not raw_line[0].isspace():
            in_facts = raw_line.split(":", 1)[0].strip() == "facts"
            continue
        if not in_facts:
            continue
        stripped = raw_line.strip()
        if ":" not in stripped:
            continue
        key, _, value = stripped.partition(":")
        key = key.strip()
        if key in PII_FIELDS:
            facts[key] = value.strip().strip('"').strip("'")
    return facts


def _is_present(value: str | None) -> bool:
    """True when a field holds a real (non-empty, non-redacted) value."""
    if value is None:
        return False
    value = value.strip()
    if not value:
        return False
    return not value.startswith(_REDACTED_PREFIX)


def detect_pii_fields(settings_path: Path | str) -> dict[str, bool]:
    """Return ``{field: present_at_head}`` for each D1 PII field."""
    facts = _read_facts(Path(settings_path))
    return {field: _is_present(facts.get(field)) for field in PII_FIELDS}


def _pii_values(settings_path: Path | str) -> dict[str, str]:
    """Return ``{field: raw_value}`` for fields that currently hold real PII.

    Internal helper. Callers must not print these values.
    """
    facts = _read_facts(Path(settings_path))
    return {
        field: facts[field]
        for field in PII_FIELDS
        if field in facts and _is_present(facts[field])
    }


def build_replacement_mapping(values: dict[str, str]) -> list[tuple[str, str]]:
    """Build the ``OLD -> REDACTED`` mapping for git filter-repo.

    Returns a list of ``(old_value, placeholder)`` tuples. Order follows
    PII_FIELDS for determinism. Empty/missing values are skipped.
    """
    mapping: list[tuple[str, str]] = []
    for field, placeholder in PII_FIELDS.items():
        value = values.get(field)
        if value and _is_present(value):
            mapping.append((value, placeholder))
    return mapping


def write_replacement_file(
    mapping: list[tuple[str, str]],
    *,
    dest_dir: Path | None = None,
) -> Path:
    """Write the filter-repo ``--replace-text`` file to a temp path.

    The file is created OUTSIDE the repo (OS temp dir by default) so
    filter-repo never rewrites a committed copy of itself. Format per line:
    ``OLD==>REDACTED_X``. Returns the path. The contents are never logged.
    """
    dest_dir = dest_dir or Path(tempfile.gettempdir())
    fd, name = tempfile.mkstemp(
        prefix="d1_replacements_",
        suffix=".txt",
        dir=str(dest_dir),
    )
    os.close(fd)
    path = Path(name)
    lines = [f"{old}==>{placeholder}" for old, placeholder in mapping]
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return path


def _run_git(
    args: list[str],
    *,
    cwd: Path | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd) if cwd else None,
        check=check,
        capture_output=True,
        text=True,
    )


def count_history_matches(value: str, repo: Path | str) -> int:
    """Count commits across all refs whose diff adds/removes ``value``.

    Uses ``git log --all -S<value> --oneline`` and counts the resulting
    commit lines. The value is passed as an argument to git (never printed by
    this tool). Returns 0 on any git failure rather than raising, so detect
    stays read-only and robust.
    """
    if not value:
        return 0
    try:
        completed = _run_git(
            ["log", "--all", f"-S{value}", "--oneline"],
            cwd=Path(repo),
            check=False,
        )
    except OSError:
        return 0
    if completed.returncode != 0:
        return 0
    return sum(1 for line in completed.stdout.splitlines() if line.strip())


def filter_repo_available() -> bool:
    """True if the git-filter-repo CLI responds to ``--version``."""
    try:
        completed = subprocess.run(
            ["git", "filter-repo", "--version"],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return False
    return completed.returncode == 0


def run_detect(settings_path: Path, repo: Path) -> dict[str, Any]:
    """Build the read-only detect report."""
    fields_present = detect_pii_fields(settings_path)
    values = _pii_values(settings_path)
    history_counts: dict[str, int] = {}
    for field in PII_FIELDS:
        value = values.get(field)
        history_counts[field] = count_history_matches(value, repo) if value else 0
    scrub_needed = any(fields_present.values()) or any(
        count > 0 for count in history_counts.values()
    )
    return {
        "fields_present": fields_present,
        "history_match_counts": history_counts,
        "scrub_needed": scrub_needed,
    }


def run_plan(settings_path: Path) -> dict[str, Any]:
    """Build the replacement mapping file and report path + count only."""
    values = _pii_values(settings_path)
    mapping = build_replacement_mapping(values)
    path = write_replacement_file(mapping)
    return {
        "replacement_file": str(path),
        "mapping_count": len(mapping),
        "placeholders": [placeholder for _, placeholder in mapping],
    }


def run_dry_run(
    settings_path: Path,
    repo: Path,
    *,
    run_smoke: bool = True,
) -> dict[str, Any]:
    """Clone the repo to a temp dir, scrub it, verify zero residual matches.

    Never pushes. Cleans up the clone and the replacement file. Raises
    FilterRepoMissing if the filter-repo CLI is not installed.
    """
    if not filter_repo_available():
        raise FilterRepoMissing(
            "git-filter-repo is not installed. Install it with "
            "'pip install git-filter-repo' (or your distro package), then "
            "re-run. See " + RUNBOOK + "."
        )

    values = _pii_values(settings_path)
    mapping = build_replacement_mapping(values)
    replacement_file = write_replacement_file(mapping)

    tmp_clone = Path(tempfile.mkdtemp(prefix="d1_dryrun_clone_"))
    clone_dir = tmp_clone / "repo"
    result: dict[str, Any] = {
        "mapping_count": len(mapping),
        "placeholders": [placeholder for _, placeholder in mapping],
    }
    try:
        _run_git(["clone", "--no-local", str(repo), str(clone_dir)])
        subprocess.run(
            ["git", "filter-repo", "--replace-text", str(replacement_file), "--force"],
            cwd=str(clone_dir),
            check=True,
            capture_output=True,
            text=True,
        )
        residual: dict[str, int] = {}
        for field, value in values.items():
            residual[field] = count_history_matches(value, clone_dir)
        result["residual_match_counts"] = residual
        result["residual_clean"] = all(count == 0 for count in residual.values())

        if run_smoke and (clone_dir / SMOKE_TEST).exists():
            smoke = subprocess.run(
                [sys.executable, "-m", "pytest", SMOKE_TEST, "-q"],
                cwd=str(clone_dir),
                check=False,
                capture_output=True,
                text=True,
            )
            result["smoke_test"] = SMOKE_TEST
            result["smoke_passed"] = smoke.returncode == 0
        else:
            result["smoke_test"] = None
            result["smoke_passed"] = None

        result["passed"] = bool(result["residual_clean"]) and (
            result["smoke_passed"] in (True, None)
        )
        return result
    finally:
        shutil.rmtree(tmp_clone, ignore_errors=True)
        try:
            replacement_file.unlink()
        except OSError:
            pass


def _refuse_push() -> dict[str, Any]:
    """Report the operator-only force-push command; this tool never pushes."""
    return {
        "refused": True,
        "reason": "force-push is operator-only per CLAUDE.md rule 9",
        "operator_command": OPERATOR_FORCE_PUSH_COMMAND,
        "runbook": RUNBOOK,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "mode",
        choices=["detect", "plan", "dry-run", "push", "force-push"],
        help="Operation to run. 'push'/'force-push' are refused (operator-only).",
    )
    parser.add_argument(
        "--settings",
        type=Path,
        default=DEFAULT_SETTINGS,
        help="Path to configs/settings.yaml (default: repo configs/settings.yaml).",
    )
    parser.add_argument(
        "--repo",
        type=Path,
        default=ROOT,
        help="Path to the git repo whose history is inspected/cloned.",
    )
    parser.add_argument(
        "--no-smoke",
        action="store_true",
        help="Skip the pytest smoke test inside the dry-run clone.",
    )
    args = parser.parse_args(argv)

    if args.mode in ("push", "force-push"):
        print(json.dumps(_refuse_push(), indent=2, sort_keys=True))
        return 2

    try:
        if args.mode == "detect":
            report = run_detect(args.settings, args.repo)
        elif args.mode == "plan":
            report = run_plan(args.settings)
        else:  # dry-run
            report = run_dry_run(
                args.settings, args.repo, run_smoke=not args.no_smoke
            )
    except FilterRepoMissing as exc:
        print(f"d1_pii_scrub: {exc}", file=sys.stderr)
        return 3
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"d1_pii_scrub: {exc}", file=sys.stderr)
        return 4

    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

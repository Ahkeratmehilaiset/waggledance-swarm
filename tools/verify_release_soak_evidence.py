#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Verify release soak evidence is reproducible from local artifacts.

Axis V2 source binding (2026-09-02). Whenever the actual soak evidence or
the rebuilt expected evidence claims ``axis_a_regression`` or
``axis_b_gate`` pass, the stored Axis A / Axis B artifacts under the
evidence root are re-verified with the fail-closed attestation helpers
(``tools/release_axis_a_attestation.py`` and
``tools/release_axis_b_attestation.py``) against the repository ``ROOT``
and the soak envelope commit ``S`` (the evidence ``commit`` field, never
the verifying checkout HEAD). On top of the helpers, every canonical
inventory source is re-checked here: tracked as a regular blob at ``S``,
a regular non-link non-reparse worktree file confined under ``ROOT``,
with LF-normalized worktree digest == ``S`` blob digest == emitted
digest. A helper exception is a verifier error and can never become a
pass. ``ROOT`` is fixed; the CLI exposes no override for it.

The source-subject primitives at the end of this module (argv git pinned
to ``ROOT`` with a clean ``GIT_*`` environment, HEAD/porcelain preflight,
tracked-inventory binding) are shared with the two Axis producers.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any, NamedTuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.collect_soak_evidence import (
    AXIS_A_SOLVER_SCALE_PROOF,
    AXIS_B_HEX_ALIGNED_EVAL,
    DEFAULT_EVIDENCE_ROOT,
    DEFAULT_RELEASE_NOTES,
    FINAL_PIP_AUDIT_REPORTS,
    PRIVACY_PRECHECK,
    build_soak_evidence,
)
from tools.release_axis_a_attestation import (
    AXIS_A_EXPECTED_SOURCES,
    evaluate_axis_a_attestation,
)
from tools.release_axis_b_attestation import (
    AXIS_B_EXPECTED_SOURCES,
    evaluate_axis_b_attestation,
)
from tools.release_security_attestation import (
    evaluate_audited_lock_pins,
    evaluate_privacy_attestation,
)

_MISSING = object()

_ATTESTATION_CLAIM_FIELDS = ("profile_s_smoke", "security_privacy_gate")

# Axis attestations: (axis, soak evidence field, canonical artifact relative
# to the evidence root, exact source inventory). The helper itself is
# resolved by ``_axis_helper`` at call time so a monkeypatched helper is
# honored.
_AXIS_ATTESTATIONS = (
    (
        "axis_a",
        "axis_a_regression",
        Path(AXIS_A_SOLVER_SCALE_PROOF),
        AXIS_A_EXPECTED_SOURCES,
    ),
    (
        "axis_b",
        "axis_b_gate",
        Path(AXIS_B_HEX_ALIGNED_EVAL),
        AXIS_B_EXPECTED_SOURCES,
    ),
)

GIT_EXECUTABLE = "git"
GIT_TIMEOUT_SECONDS = 120
SOURCE_COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")
_REGULAR_BLOB_MODES = frozenset({b"100644", b"100755"})


def _append_once(blockers: list[str], blocker: str) -> None:
    if blocker not in blockers:
        blockers.append(blocker)


def _security_attestation_blockers(
    actual: dict[str, Any],
    expected: dict[str, Any],
    evidence_root: Path,
) -> list[str]:
    """Fail-closed attestation blockers, active only under a pass claim.

    When neither the actual evidence nor the rebuilt expected evidence
    claims ``profile_s_smoke`` or ``security_privacy_gate`` pass, this
    returns no blockers and legacy behavior is unchanged.
    """
    claims_pass = any(
        actual.get(field) == "pass" or expected.get(field) == "pass"
        for field in _ATTESTATION_CLAIM_FIELDS
    )
    if not claims_pass:
        return []
    blockers = list(
        evaluate_privacy_attestation(evidence_root / PRIVACY_PRECHECK)
    )
    audited_report = None
    for name in FINAL_PIP_AUDIT_REPORTS:
        candidate = evidence_root / name
        if candidate.exists():
            audited_report = candidate
            break
    blockers.extend(evaluate_audited_lock_pins(audited_report))
    return blockers


def _axis_helper(axis: str):
    """The attestation helper for ``axis``, read at call time."""
    if axis == "axis_a":
        return evaluate_axis_a_attestation
    if axis == "axis_b":
        return evaluate_axis_b_attestation
    raise KeyError(axis)


def _axis_source_binding_blockers(
    axis: str,
    artifact: Path,
    inventory: tuple[str, ...],
    source_root: Path,
    commit: object,
) -> list[str]:
    """Re-check the artifact inventory against the ``S`` blobs in ROOT.

    The attestation helpers recompute worktree digests only. A forged
    artifact carrying well-formed hashes of a tampered worktree plus a
    truthful ``S`` stamp would pass them, so the verifier additionally
    requires, for every canonical inventory entry, tracked-regular at
    ``S`` and LF worktree digest == ``S`` blob digest == emitted digest.
    Every blocker is path-free.
    """
    if not isinstance(commit, str) or not SOURCE_COMMIT_PATTERN.match(commit):
        return [f"{axis}_source_commit_invalid"]
    resolved, blocker = resolve_commit(source_root, commit)
    if blocker is not None:
        return [f"{axis}_{blocker}"]
    if resolved != commit:
        return [f"{axis}_source_commit_unresolvable"]
    try:
        loaded = json.loads(Path(artifact).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError, RecursionError):
        return [f"{axis}_report_unreadable"]
    if not isinstance(loaded, dict):
        return [f"{axis}_report_unreadable"]
    source_hashes = loaded.get("source_hashes")
    if not isinstance(source_hashes, dict):
        return [f"{axis}_sources_unbound"]
    binding = bind_source_inventory(source_root, commit, inventory)
    blockers = [f"{axis}_{item}" for item in binding.blockers]
    for rel in inventory:
        if rel not in binding.digests:
            continue
        if source_hashes.get(rel) != binding.digests[rel]:
            _append_once(blockers, f"{axis}_source_blob_mismatch")
    return blockers


def _axis_attestation_blockers(
    actual: dict[str, Any],
    expected: dict[str, Any],
    evidence_root: Path,
    source_root: Path,
) -> list[str]:
    """Fail-closed Axis A/B blockers, active whenever either side claims pass.

    The helpers run against the canonical artifacts under the evidence
    root, the fixed repository ``ROOT`` and the soak envelope commit
    ``S`` (``actual["commit"]``). A missing artifact under a pass claim is
    a named blocker; a helper exception is a verifier error, never a
    pass. When neither the actual nor the rebuilt expected evidence
    claims an axis pass, that axis is inactive and nothing is required.
    """
    blockers: list[str] = []
    commit = actual.get("commit")
    for axis, field, artifact_rel, inventory in _AXIS_ATTESTATIONS:
        claims_pass = (
            actual.get(field) == "pass" or expected.get(field) == "pass"
        )
        if not claims_pass:
            continue
        artifact = evidence_root / artifact_rel
        try:
            helper_blockers = _axis_helper(axis)(artifact, source_root, commit)
        except Exception as exc:  # noqa: BLE001 - helper failure is never a pass
            _append_once(
                blockers, f"{axis}_helper_error:{exc.__class__.__name__}"
            )
            continue
        if not isinstance(helper_blockers, list) or not all(
            isinstance(item, str) for item in helper_blockers
        ):
            _append_once(blockers, f"{axis}_helper_error:InvalidResult")
            continue
        for item in helper_blockers:
            _append_once(blockers, item)
        for item in _axis_source_binding_blockers(
            axis, artifact, inventory, source_root, commit
        ):
            _append_once(blockers, item)
    return blockers


def _parse_timestamp(value: object) -> dt.datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("timestamp missing")
    normalized = value.strip().replace("Z", "+00:00")
    parsed = dt.datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.UTC)
    return parsed.astimezone(dt.UTC)


def _read_object(path: Path) -> dict[str, Any]:
    loaded = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise ValueError("evidence JSON must be an object")
    return loaded


def build_report(
    *,
    soak_evidence: Path | str,
    release_readiness: Path | str,
    evidence_root: Path | str = DEFAULT_EVIDENCE_ROOT,
    release_notes: Path | str = DEFAULT_RELEASE_NOTES,
    source_root: Path | str = ROOT,
) -> dict[str, Any]:
    blockers: list[str] = []
    mismatched_fields: list[str] = []
    soak_evidence = Path(soak_evidence)
    release_readiness = Path(release_readiness)
    evidence_root = Path(evidence_root)
    release_notes = Path(release_notes)
    source_root = Path(source_root)
    try:
        actual = _read_object(soak_evidence)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return {
            "schema_version": "waggledance.release_soak_verifier.v1",
            "verified": False,
            "blockers": [f"soak_evidence_unreadable:{exc.__class__.__name__}"],
            "mismatched_fields": [],
        }

    try:
        expected = build_soak_evidence(
            release_readiness,
            commit=str(actual.get("commit", "")),
            started_at_utc=_parse_timestamp(actual.get("started_at_utc")),
            ended_at_utc=_parse_timestamp(actual.get("ended_at_utc")),
            use_local_artifacts=True,
            evidence_root=evidence_root,
            release_notes=release_notes,
        )
    except (OSError, ValueError) as exc:
        return {
            "schema_version": "waggledance.release_soak_verifier.v1",
            "verified": False,
            "blockers": [f"expected_evidence_unbuildable:{exc.__class__.__name__}"],
            "mismatched_fields": [],
        }

    for field in sorted(set(actual) | set(expected)):
        actual_value = actual[field] if field in actual else _MISSING
        expected_value = expected[field] if field in expected else _MISSING
        if actual_value != expected_value:
            mismatched_fields.append(field)
            blockers.append(f"field_mismatch:{field}")

    blockers.extend(
        _security_attestation_blockers(actual, expected, evidence_root)
    )
    blockers.extend(
        _axis_attestation_blockers(actual, expected, evidence_root, source_root)
    )

    return {
        "schema_version": "waggledance.release_soak_verifier.v1",
        "verified": not blockers,
        "blockers": blockers,
        "mismatched_fields": mismatched_fields,
        "soak_evidence": "<redacted>",
        "release_readiness": "<redacted>",
        "evidence_root": "<redacted>",
        "release_notes": "<redacted>",
        "source_root": "<redacted>",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--soak-evidence",
        default=Path("docs/runs/release_soak_evidence/v3.12.0.json"),
        type=Path,
    )
    parser.add_argument(
        "--release-readiness",
        default=Path("docs/release/RELEASE_READINESS.md"),
        type=Path,
    )
    parser.add_argument("--evidence-root", default=DEFAULT_EVIDENCE_ROOT, type=Path)
    parser.add_argument("--release-notes", default=DEFAULT_RELEASE_NOTES, type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)

    report = build_report(
        soak_evidence=args.soak_evidence,
        release_readiness=args.release_readiness,
        evidence_root=args.evidence_root,
        release_notes=args.release_notes,
    )
    encoded = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    return 0 if report["verified"] else 1


# ---------------------------------------------------------------------------
# Source-subject binding primitives (shared with the Axis producers)
# ---------------------------------------------------------------------------


class InventoryBinding(NamedTuple):
    """Result of binding an exact source inventory to a commit.

    ``digests`` maps every inventory entry to its LF-normalized worktree
    digest and is empty whenever any blocker is present. ``blockers`` are
    stable, path-free names; ``details`` carry the same names with the
    repository-relative inventory entry for operator output only.
    """

    digests: dict[str, str]
    blockers: list[str]
    details: list[str]


def lf_digest(data: bytes) -> str | None:
    """``sha256:`` digest of UTF-8 text with CRLF and bare CR folded to LF.

    Mirrors ``Path.read_text(encoding="utf-8")`` universal-newline decoding
    followed by the attestation helpers' CRLF replacement, so a
    producer-emitted digest recomputes identically in the helpers. ``None``
    for bytes that are not valid UTF-8.
    """
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return None
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return "sha256:" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _clean_git_environment() -> dict[str, str]:
    """The process environment without any ``GIT_*`` variable.

    ``GIT_DIR``, ``GIT_WORK_TREE``, ``GIT_INDEX_FILE``, ``GIT_NAMESPACE``,
    ``GIT_OBJECT_DIRECTORY``, ``GIT_CONFIG_*`` and friends can all
    retarget or reshape what git reports about ``ROOT``; the pinned
    invocation must not inherit them.
    """
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.upper().startswith("GIT_")
    }
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


def run_git(
    root: Path | str,
    *args: str,
    timeout: float = GIT_TIMEOUT_SECONDS,
) -> subprocess.CompletedProcess[bytes] | None:
    """Run git as argv pinned to ``root``; ``None`` when git cannot be run.

    The repository is fixed with ``--git-dir root/.git`` and
    ``--work-tree root`` (a linked-worktree gitfile is honored), the
    ``GIT_*`` environment is stripped, and optional index locks are not
    taken. A non-zero exit is returned to the caller for a fail-closed
    decision; only an unavailable executable, a spawn failure or a
    timeout yield ``None``.
    """
    root = Path(root)
    command = [
        GIT_EXECUTABLE,
        "-C",
        str(root),
        "--git-dir",
        str(root / ".git"),
        "--work-tree",
        str(root),
        "--no-optional-locks",
        *args,
    ]
    try:
        return subprocess.run(  # noqa: S603 - fixed argv, no shell
            command,
            cwd=str(root),
            env=_clean_git_environment(),
            capture_output=True,
            check=False,
            timeout=timeout,
        )
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def _exact_commit_line(output: bytes) -> str | None:
    if not output.endswith(b"\n"):
        return None
    try:
        text = output[:-1].decode("ascii")
    except UnicodeDecodeError:
        return None
    return text if SOURCE_COMMIT_PATTERN.match(text) else None


def resolve_commit(
    root: Path | str, revision: str
) -> tuple[str | None, str | None]:
    """``(commit, None)`` for ``<revision>^{commit}`` at ``root``.

    ``(None, "git_unavailable")`` when git cannot be run and
    ``(None, "git_revision_unresolvable")`` for a non-zero exit or any
    output that is not exactly one lowercase 40-hex line.
    """
    completed = run_git(
        root, "rev-parse", "--verify", "--quiet", f"{revision}^{{commit}}"
    )
    if completed is None:
        return None, "git_unavailable"
    if completed.returncode != 0:
        return None, "git_revision_unresolvable"
    commit = _exact_commit_line(completed.stdout)
    if commit is None:
        return None, "git_revision_unresolvable"
    return commit, None


def head_commit(root: Path | str) -> str | None:
    commit, _blocker = resolve_commit(root, "HEAD")
    return commit


def worktree_porcelain(root: Path | str) -> bytes | None:
    """Raw ``status --porcelain=v1 -z --untracked-files=all``; ``None`` on error."""
    completed = run_git(
        root, "status", "--porcelain=v1", "-z", "--untracked-files=all"
    )
    if completed is None or completed.returncode != 0:
        return None
    return completed.stdout


def source_subject_preflight(root: Path | str, source_commit: object) -> list[str]:
    """Fail-closed source-subject preflight for a producer run.

    Empty only when ``source_commit`` is lowercase 40-hex, git resolves
    ``HEAD^{commit}`` at ``root`` to exactly that value, and the
    porcelain status including untracked files is empty. Git
    unavailable, error, malformed output, a stamp that is not HEAD, and
    any dirty tracked or untracked entry each yield a stable blocker.
    """
    if not isinstance(source_commit, str) or not SOURCE_COMMIT_PATTERN.match(
        source_commit
    ):
        return ["source_commit_invalid"]
    head, blocker = resolve_commit(root, "HEAD")
    if blocker is not None:
        return [blocker]
    blockers: list[str] = []
    if head != source_commit:
        blockers.append("source_commit_not_head")
    porcelain = worktree_porcelain(root)
    if porcelain is None:
        blockers.append("git_status_unavailable")
    elif porcelain != b"":
        blockers.append("worktree_dirty")
    return blockers


def _is_reparse_point(path: Path) -> bool:
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    if not reparse_flag:
        return False
    try:
        attributes = getattr(os.lstat(path), "st_file_attributes", 0)
    except OSError:
        return True
    return bool(attributes & reparse_flag)


def is_link_or_reparse(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
    except OSError:
        return True
    return _is_reparse_point(path)


def worktree_source_digest(
    root: Path | str, rel: str
) -> tuple[str | None, str | None]:
    """LF digest of ``root/rel`` as a confined regular file, else a blocker.

    Rejects a link or reparse root, a missing entry, a symlink or reparse
    point at the entry or any component below ``root``, a non-regular
    file, an entry that resolves outside ``root``, and unreadable or
    non-UTF-8 content.
    """
    root = Path(root)
    try:
        if is_link_or_reparse(root):
            return None, "source_root_link_or_reparse"
        resolved_root = root.resolve(strict=True)
    except (OSError, RuntimeError):
        return None, "source_root_unavailable"
    candidate = root / rel
    try:
        info = os.lstat(candidate)
    except OSError:
        return None, "source_missing"
    if stat.S_ISLNK(info.st_mode) or _is_reparse_point(candidate):
        return None, "source_link_or_reparse"
    if not stat.S_ISREG(info.st_mode):
        return None, "source_not_regular"
    for parent in candidate.parents:
        if parent == root:
            break
        if is_link_or_reparse(parent):
            return None, "source_link_or_reparse"
    try:
        if not candidate.resolve(strict=True).is_relative_to(resolved_root):
            return None, "source_escapes_root"
        data = candidate.read_bytes()
    except (OSError, RuntimeError):
        return None, "source_unreadable"
    digest = lf_digest(data)
    if digest is None:
        return None, "source_not_utf8"
    return digest, None


def tracked_blob_bytes(
    root: Path | str, commit: str, rel: str
) -> tuple[bytes | None, str | None]:
    """Raw bytes of the regular blob tracked at ``commit:rel``, else a blocker.

    The bytes come from the git object store through the pinned argv
    invocation (``ls-tree`` then ``cat-file blob``, no smudge filters) and
    never from the worktree, so they are exactly what ``commit`` records
    for ``rel``.
    """
    completed = run_git(root, "ls-tree", "-z", commit, "--", rel)
    if completed is None:
        return None, "git_unavailable"
    if completed.returncode != 0:
        return None, "git_ls_tree_failed"
    entries = [entry for entry in completed.stdout.split(b"\0") if entry]
    if len(entries) != 1:
        return None, "source_not_tracked_at_commit"
    meta, separator, path = entries[0].partition(b"\t")
    parts = meta.split(b" ")
    if not separator or len(parts) != 3 or path != rel.encode("utf-8"):
        return None, "source_not_tracked_at_commit"
    mode, kind, object_id = parts
    if kind != b"blob" or mode not in _REGULAR_BLOB_MODES:
        return None, "source_not_regular_at_commit"
    try:
        object_text = object_id.decode("ascii")
    except UnicodeDecodeError:
        return None, "git_ls_tree_malformed"
    if not SOURCE_COMMIT_PATTERN.match(object_text):
        return None, "git_ls_tree_malformed"
    completed = run_git(root, "cat-file", "blob", object_text)
    if completed is None:
        return None, "git_unavailable"
    if completed.returncode != 0:
        return None, "git_cat_file_failed"
    return completed.stdout, None


def tracked_blob_digest(
    root: Path | str, commit: str, rel: str
) -> tuple[str | None, str | None]:
    """LF digest of the regular blob tracked at ``commit:rel``, else a blocker."""
    data, blocker = tracked_blob_bytes(root, commit, rel)
    if blocker is not None:
        return None, blocker
    assert data is not None
    digest = lf_digest(data)
    if digest is None:
        return None, "source_blob_not_utf8"
    return digest, None


def bind_source_inventory(
    root: Path | str, commit: object, rel_paths: tuple[str, ...]
) -> InventoryBinding:
    """Bind every inventory entry to ``commit``: worktree == blob, both regular.

    ``digests`` is complete only when every entry is a tracked regular
    blob at ``commit`` and a confined regular worktree file whose
    LF-normalized digest equals the blob digest; otherwise ``digests`` is
    empty and every blocker found is listed once.
    """
    if not isinstance(commit, str) or not SOURCE_COMMIT_PATTERN.match(commit):
        return InventoryBinding(
            {}, ["source_commit_invalid"], ["source_commit_invalid"]
        )
    digests: dict[str, str] = {}
    blockers: list[str] = []
    details: list[str] = []
    for rel in rel_paths:
        worktree, blocker = worktree_source_digest(root, rel)
        if blocker is None:
            blob, blocker = tracked_blob_digest(root, commit, rel)
            if blocker is None and worktree != blob:
                blocker = "source_worktree_blob_mismatch"
        if blocker is not None:
            _append_once(blockers, blocker)
            details.append(f"{blocker}: {rel}")
            continue
        assert worktree is not None
        digests[rel] = worktree
    if blockers:
        return InventoryBinding({}, blockers, details)
    return InventoryBinding(digests, [], [])


def bind_source_subject(
    root: Path | str, source_commit: object, rel_paths: tuple[str, ...]
) -> InventoryBinding:
    """Preflight then inventory binding, in one fail-closed step."""
    blockers = source_subject_preflight(root, source_commit)
    if blockers:
        return InventoryBinding({}, blockers, list(blockers))
    return bind_source_inventory(root, source_commit, rel_paths)


if __name__ == "__main__":
    raise SystemExit(main())

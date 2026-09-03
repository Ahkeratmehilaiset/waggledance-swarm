#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Write v3.12 Axis B hex-aligned oracle evidence.

AXIS V2 SOURCE BINDING (2026-09-02):

* ``--source-commit`` is REQUIRED and must be the lowercase full 40-hex
  commit; there is no default and no HEAD fallback. Before the
  evaluation and again immediately before the artifact is written, a
  fail-closed preflight pinned to the repository ``ROOT`` (argv git with
  ``--git-dir``/``--work-tree`` and a clean ``GIT_*`` environment)
  requires ``HEAD^{commit}`` to equal the stamp and ``git status
  --porcelain=v1 -z --untracked-files=all`` to be empty, and binds the
  exact ``AXIS_B_EXPECTED_SOURCES`` inventory: every entry tracked as a
  regular blob at HEAD, a regular non-link non-reparse worktree file
  under ``ROOT``, with LF-normalized worktree digest == HEAD blob digest.
  The second preflight must reproduce the same digests. Any git failure,
  dirty entry, stamp mismatch, HEAD change or inventory drift aborts
  with exit code 2 and NO artifact. A blocked evaluation still writes a
  non-pass artifact.
* ``--oracle-dir`` / ``--hex-config`` are anchored to ``ROOT`` (never the
  working directory) and can yield ``result: pass`` only when the
  canonical ``ROOT/tests/oracle_hex`` and ``ROOT/configs/hex_cells.yaml``
  are themselves non-link, non-reparse paths and the arguments resolve
  to exactly those paths; any other input evaluates as ``blocked``.
* The scorer never reads the corpus from the worktree. Between the two
  preflights the exact blobs tracked at ``--source-commit`` are copied
  from the git object store (``git cat-file blob``) into a private
  temporary directory that must resolve outside ``ROOT``; the copy's LF
  digests must equal the bound inventory, the canonical corpus and hex
  config are scored from that copy alone, and the copy is re-digested
  after scoring and then removed (best effort: a cleanup error does not
  turn a scored result into a crash). A worktree rewrite that lands after
  the first preflight and is reverted before the second one therefore
  cannot reach the scorer (claude-rco-1 finding, 2026-09-03), and the
  stamped ``source_hashes`` describe exactly the bytes that were scored.
  Non-canonical ``--oracle-dir`` / ``--hex-config`` inputs are still
  scored as given from the worktree and can only yield ``blocked``.
* The artifact carries ``source_commit``, UTC ``generated_at``,
  ``source_files`` and ``sha256:`` ``source_hashes`` so
  ``tools/release_axis_b_attestation.py`` can bind it to source truth.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.release_axis_b_attestation import AXIS_B_EXPECTED_SOURCES
from tools.run_r21_oracle_ab_proof import load_oracle_corpus, quality_arm
from tools.verify_release_soak_evidence import (
    SOURCE_COMMIT_PATTERN,
    InventoryBinding,
    bind_source_subject,
    is_link_or_reparse,
    lf_digest,
    tracked_blob_bytes,
    worktree_source_digest,
)
from waggledance.application.services.hex_topology_registry import HexTopologyRegistry


DEFAULT_OUTPUT = (
    Path("docs")
    / "runs"
    / "release_soak_evidence"
    / "v3.12.0_axis_b_hex_aligned_eval.json"
)
CANONICAL_ORACLE_DIR = Path("tests") / "oracle_hex"
CANONICAL_HEX_CONFIG = Path("configs") / "hex_cells.yaml"
DEFAULT_ORACLE_DIR = CANONICAL_ORACLE_DIR
DEFAULT_HEX_CONFIG = CANONICAL_HEX_CONFIG
NONCANONICAL_ORACLE_DIR = "noncanonical"
EXPECTED_CELLS = {
    "hub",
    "bee_ops",
    "environment",
    "home_comfort",
    "safety_security",
    "production",
    "logistics",
}
QUALITY_FLOOR = 0.74
MISMATCHED_BASELINE_QUALITY = 0.5
MINIMUM_BASELINE_DELTA = 0.20
PER_CELL_QUALITY_FLOOR = 0.6
ABORT_EXIT_CODE = 2


def _utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _anchor(root: Path, candidate: Path | str) -> Path:
    candidate = Path(candidate)
    return candidate if candidate.is_absolute() else root / candidate


def _has_link_component(root: Path, path: Path) -> bool:
    """True when ``path`` or any component below ``root`` is a link/reparse."""
    if is_link_or_reparse(path):
        return True
    for parent in path.parents:
        if parent == root:
            break
        if is_link_or_reparse(parent):
            return True
    return False


def canonical_input_blockers(
    root: Path | str,
    oracle_dir: Path | str,
    hex_config: Path | str,
) -> tuple[list[str], Path, Path]:
    """Blockers for the corpus inputs, plus the ROOT-anchored input paths.

    Empty only when the canonical oracle directory and hex config exist
    under a non-link ``root`` without any link or reparse component and
    both arguments resolve to exactly those canonical paths. The
    canonical paths are checked for links *before* resolution because
    resolution alone follows a junction planted at the canonical
    location.
    """
    root = Path(root)
    oracle_path = _anchor(root, oracle_dir)
    config_path = _anchor(root, hex_config)
    blockers: list[str] = []
    canonical_oracle = root / CANONICAL_ORACLE_DIR
    canonical_config = root / CANONICAL_HEX_CONFIG
    try:
        if is_link_or_reparse(root):
            return ["source_root_link_or_reparse"], oracle_path, config_path
        root.resolve(strict=True)
    except (OSError, RuntimeError):
        return ["source_root_unavailable"], oracle_path, config_path

    for label, canonical, want_dir in (
        ("oracle_dir", canonical_oracle, True),
        ("hex_config", canonical_config, False),
    ):
        try:
            os.lstat(canonical)
        except OSError:
            blockers.append(f"{label}_canonical_missing")
            continue
        if _has_link_component(root, canonical):
            blockers.append(f"{label}_canonical_link_or_reparse")
            continue
        present = canonical.is_dir() if want_dir else canonical.is_file()
        if not present:
            blockers.append(f"{label}_canonical_missing")

    for label, given, canonical in (
        ("oracle_dir", oracle_path, canonical_oracle),
        ("hex_config", config_path, canonical_config),
    ):
        if any(item.startswith(label) for item in blockers):
            continue
        try:
            if given.resolve(strict=True) != canonical.resolve(strict=True):
                blockers.append(f"{label}_noncanonical")
        except (OSError, RuntimeError):
            blockers.append(f"{label}_unresolvable")
    return blockers, oracle_path, config_path


def build_axis_b_report(
    *,
    oracle_dir: Path | str = DEFAULT_ORACLE_DIR,
    hex_config: Path | str = DEFAULT_HEX_CONFIG,
    root: Path | str = ROOT,
) -> dict[str, Any]:
    root = Path(root)
    input_blockers, oracle_path, config_path = canonical_input_blockers(
        root, oracle_dir, hex_config
    )
    registry = HexTopologyRegistry(config_path=str(config_path), agents=[])
    oracles = load_oracle_corpus(oracle_path)
    result = quality_arm(oracles, registry.select_origin_cell)

    cells = {oracle["cell"] for oracle in oracles}
    blockers: list[str] = list(input_blockers)
    if len(oracles) != 7:
        blockers.append("oracle_file_count_not_7")
    if cells != EXPECTED_CELLS:
        blockers.append("oracle_cells_do_not_match_hex_registry")
    total_positive = sum(len(oracle["positive"]) for oracle in oracles)
    total_negative = sum(len(oracle["negative"]) for oracle in oracles)
    if total_positive != 105 or total_negative != 35:
        blockers.append("oracle_shape_not_105_positive_35_negative")
    quality = float(result["quality"])
    if quality < QUALITY_FLOOR:
        blockers.append("quality_below_floor")
    if quality <= MISMATCHED_BASELINE_QUALITY + MINIMUM_BASELINE_DELTA:
        blockers.append("quality_delta_below_floor")
    for row in result["per_file"]:
        if row["file_score"] < PER_CELL_QUALITY_FLOOR:
            blockers.append(f"{row['cell']}_quality_below_floor")
        if row["neg_correct"] != row["neg_total"]:
            blockers.append(f"{row['cell']}_negative_routing_not_perfect")

    return {
        "schema_version": "waggledance.axis_b_hex_eval.v1",
        "target_version": "v3.12.0",
        "benchmark_id": "v3.12-axis-b-hex-aligned-eval",
        "command": (
            "python tools/run_release_axis_b_gate.py --source-commit <sha>"
        ),
        "corpus": {
            "oracle_dir": (
                CANONICAL_ORACLE_DIR.as_posix()
                if not input_blockers
                else NONCANONICAL_ORACLE_DIR
            ),
            "files": len(oracles),
            "cells": sorted(cells),
            "total_positive": total_positive,
            "total_negative": total_negative,
        },
        "thresholds": {
            "quality_floor": QUALITY_FLOOR,
            "mismatched_baseline_quality": MISMATCHED_BASELINE_QUALITY,
            "minimum_baseline_delta": MINIMUM_BASELINE_DELTA,
            "per_cell_quality_floor": PER_CELL_QUALITY_FLOOR,
        },
        "quality": quality,
        "micro_pos": result["micro_pos_correct"],
        "micro_pos_total": result["micro_pos_total"],
        "micro_neg": result["micro_neg_correct"],
        "micro_neg_total": result["micro_neg_total"],
        "per_file": result["per_file"],
        "blockers": blockers,
        "result": "pass" if not blockers else "blocked",
    }


def _source_commit_argument(value: str) -> str:
    if not SOURCE_COMMIT_PATTERN.match(value):
        raise argparse.ArgumentTypeError(
            "must be the lowercase full 40-hex commit that is HEAD of the "
            "repository ROOT (no default, no HEAD fallback)"
        )
    return value


def _abort(stage: str, binding: InventoryBinding) -> int:
    print(
        f"ABORT ({stage}): source-subject binding failed; "
        "no Axis B artifact written.",
        file=sys.stderr,
    )
    for line in binding.details or binding.blockers:
        print(f"  {line}", file=sys.stderr)
    return ABORT_EXIT_CODE


def _collect(
    digests: dict[str, str],
    blockers: list[str],
    details: list[str],
    rel: str,
    digest: str | None,
    blocker: str | None,
) -> None:
    if blocker is not None:
        if blocker not in blockers:
            blockers.append(blocker)
        details.append(f"{blocker}: {rel}")
        return
    assert digest is not None
    digests[rel] = digest


def materialize_source_subject(
    root: Path | str,
    source_commit: object,
    rel_paths: tuple[str, ...],
    destination: Path | str,
) -> InventoryBinding:
    """Copy the regular blobs tracked at ``source_commit`` under ``destination``.

    Every inventory entry is fetched from the git object store of ``root``
    through the pinned argv invocation and written as ``destination/entry``
    with its committed bytes; the worktree is never read. ``digests`` maps
    each entry to the LF digest of the bytes written and is empty whenever
    any blocker is present, so a partial copy can never be scored. An
    entry that is absolute, rooted or contains ``..`` is refused before
    any git call.
    """
    if not isinstance(source_commit, str) or not SOURCE_COMMIT_PATTERN.match(
        source_commit
    ):
        return InventoryBinding(
            {}, ["source_commit_invalid"], ["source_commit_invalid"]
        )
    destination = Path(destination)
    digests: dict[str, str] = {}
    blockers: list[str] = []
    details: list[str] = []
    for rel in rel_paths:
        entry = Path(rel)
        if (
            not rel
            or entry.is_absolute()
            or entry.drive
            or entry.root
            or ".." in entry.parts
        ):
            _collect(digests, blockers, details, rel, None, "source_entry_not_confined")
            continue
        data, blocker = tracked_blob_bytes(root, source_commit, rel)
        digest = lf_digest(data) if blocker is None and data is not None else None
        if blocker is None and digest is None:
            blocker = "source_blob_not_utf8"
        if blocker is None:
            assert data is not None
            target = destination / entry
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            except OSError:
                blocker = "subject_copy_unwritable"
        _collect(digests, blockers, details, rel, digest, blocker)
    if blockers:
        return InventoryBinding({}, blockers, details)
    return InventoryBinding(digests, [], [])


def subject_snapshot_digests(
    destination: Path | str, rel_paths: tuple[str, ...]
) -> InventoryBinding:
    """LF digests of the private copy as confined regular files, else blockers."""
    digests: dict[str, str] = {}
    blockers: list[str] = []
    details: list[str] = []
    for rel in rel_paths:
        digest, blocker = worktree_source_digest(destination, rel)
        _collect(digests, blockers, details, rel, digest, blocker)
    if blockers:
        return InventoryBinding({}, blockers, details)
    return InventoryBinding(digests, [], [])


def evaluate_source_subject(
    root: Path | str, source_commit: str, binding: InventoryBinding
) -> tuple[dict[str, Any] | None, tuple[str, InventoryBinding] | None]:
    """Score the canonical corpus from a private copy of the bound blobs.

    ``binding`` is the inventory bound by the first preflight. The blobs
    tracked at ``source_commit`` are copied into a fresh temporary
    directory that must resolve outside ``root``; the copy's digests must
    equal ``binding.digests`` before scoring and again after it, and the
    copy is removed afterwards on every path (best effort: cleanup errors
    are ignored, never raised). Returns ``(report, None)`` on success, else
    ``(None, (stage, blockers))`` for ``_abort``. The worktree corpus is
    never opened here, so nothing written to it between the two preflights
    can reach the scorer.
    """
    root = Path(root)
    with tempfile.TemporaryDirectory(
        prefix="axis_b_subject_", ignore_cleanup_errors=True
    ) as scratch:
        subject_root = Path(scratch)
        try:
            inside_root = subject_root.resolve(strict=True).is_relative_to(
                root.resolve(strict=True)
            )
        except (OSError, RuntimeError):
            inside_root = True
        if inside_root:
            return None, (
                "subject copy",
                InventoryBinding(
                    {},
                    ["subject_snapshot_inside_root"],
                    ["subject_snapshot_inside_root: private copy would live under ROOT"],
                ),
            )
        materialized = materialize_source_subject(
            root, source_commit, AXIS_B_EXPECTED_SOURCES, subject_root
        )
        if materialized.blockers:
            return None, ("subject copy", materialized)
        if materialized.digests != binding.digests:
            return None, (
                "subject copy",
                InventoryBinding(
                    {},
                    ["subject_snapshot_mismatch"],
                    ["subject_snapshot_mismatch: copied blobs differ from the bound inventory"],
                ),
            )
        report = build_axis_b_report(
            oracle_dir=CANONICAL_ORACLE_DIR,
            hex_config=CANONICAL_HEX_CONFIG,
            root=subject_root,
        )
        after = subject_snapshot_digests(subject_root, AXIS_B_EXPECTED_SOURCES)
        if after.blockers:
            return None, ("subject recheck after scoring", after)
        if after.digests != materialized.digests:
            return None, (
                "subject recheck after scoring",
                InventoryBinding(
                    {},
                    ["subject_snapshot_changed"],
                    ["subject_snapshot_changed: the private copy moved during scoring"],
                ),
            )
    return report, None


def main(argv: list[str] | None = None, *, root: Path | str = ROOT) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--oracle-dir", type=Path, default=DEFAULT_ORACLE_DIR)
    parser.add_argument("--hex-config", type=Path, default=DEFAULT_HEX_CONFIG)
    parser.add_argument(
        "--source-commit",
        required=True,
        type=_source_commit_argument,
        metavar="SHA",
        help=(
            "Lowercase full 40-hex commit that must equal HEAD^{commit} of "
            "the repository ROOT with a clean tree (tracked and untracked); "
            "required, no default, no HEAD fallback."
        ),
    )
    args = parser.parse_args(argv)
    root = Path(root)

    binding = bind_source_subject(root, args.source_commit, AXIS_B_EXPECTED_SOURCES)
    if binding.blockers:
        return _abort("preflight before evaluation", binding)

    input_blockers, _oracle_path, _config_path = canonical_input_blockers(
        root, args.oracle_dir, args.hex_config
    )
    try:
        if input_blockers:
            # Non-canonical inputs can never pass; score them as given so the
            # blocked artifact describes what was actually asked for.
            report = build_axis_b_report(
                oracle_dir=args.oracle_dir,
                hex_config=args.hex_config,
                root=root,
            )
        else:
            report, failure = evaluate_source_subject(
                root, args.source_commit, binding
            )
            if failure is not None:
                return _abort(*failure)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return _abort(
            "evaluation",
            InventoryBinding(
                {},
                [f"evaluation_failed:{exc.__class__.__name__}"],
                [f"evaluation_failed:{exc.__class__.__name__}"],
            ),
        )
    assert report is not None

    recheck = bind_source_subject(root, args.source_commit, AXIS_B_EXPECTED_SOURCES)
    if recheck.blockers:
        return _abort("preflight before artifact", recheck)
    if recheck.digests != binding.digests:
        return _abort(
            "preflight before artifact",
            InventoryBinding(
                {},
                ["source_inventory_changed"],
                ["source_inventory_changed: worktree digests moved during the run"],
            ),
        )

    report.update(
        {
            "source_commit": args.source_commit,
            "generated_at": _utc_iso(),
            "source_files": list(AXIS_B_EXPECTED_SOURCES),
            "source_hashes": dict(binding.digests),
        }
    )
    encoded = json.dumps(report, indent=2, sort_keys=True) + "\n"
    output = _anchor(root, args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    return 0 if report["result"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())

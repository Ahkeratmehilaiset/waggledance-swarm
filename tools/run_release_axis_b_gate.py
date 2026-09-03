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
* The scorer never reads the corpus from the worktree, and nothing read
  from disk after the preflight is scored unverified. Between the two
  preflights the exact blobs tracked at ``--source-commit`` are fetched
  from the git object store (``git cat-file blob``) and held in memory;
  their LF digests must equal the bound inventory. The oracle corpus that
  is scored is parsed from those in-memory bytes. The production loaders
  still run, over a private copy of the same bytes in a temporary
  directory that must resolve outside ``ROOT``, because
  ``load_oracle_corpus`` and ``HexTopologyRegistry`` only accept paths:
  the loader's corpus must equal the in-memory parse and the registry's
  cells, in load order, must equal the cells derived from the in-memory
  config, else the run aborts with no artifact
  (``subject_corpus_mismatch`` / ``subject_hex_config_mismatch``).
  Routing is a pure function of those ordered cells (no agents are
  mapped), so equal cells mean identical routing. A rewrite of the copy
  that is reverted before any later check therefore cannot change what
  is scored either: it can only abort the run (claude-rco-2 finding,
  2026-09-03, on top of claude-rco-1's worktree finding). The copy is
  re-digested after scoring as a secondary check and then removed (best
  effort: a cleanup error does not turn a scored result into a crash).
  The stamped ``source_hashes`` describe exactly the bytes that were
  scored. Non-canonical ``--oracle-dir`` / ``--hex-config`` inputs are
  still scored as given from the worktree and can only yield ``blocked``.
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
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, NamedTuple

import yaml

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
from waggledance.core.domain.hex_mesh import HexCellDefinition, HexCoord


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
    subject: Mapping[str, bytes] | None = None,
) -> dict[str, Any]:
    """Score the hex-aligned oracle corpus.

    With ``subject`` (the verified bytes of ``AXIS_B_EXPECTED_SOURCES``
    keyed by repository-relative path) the scored corpus is parsed from
    those bytes, and the production loaders' view of ``root`` must match
    them exactly (``bind_scored_subject``), else ``SubjectMismatch`` is
    raised and nothing is scored. Without ``subject`` the paths are scored
    as given.
    """
    root = Path(root)
    input_blockers, oracle_path, config_path = canonical_input_blockers(
        root, oracle_dir, hex_config
    )
    registry = HexTopologyRegistry(config_path=str(config_path), agents=[])
    oracles = load_oracle_corpus(oracle_path)
    if subject is not None:
        oracles = bind_scored_subject(oracles, registry, subject)
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


class SubjectMismatch(RuntimeError):
    """What the production loaders read differs from the verified bytes."""

    def __init__(self, blocker: str, detail: str) -> None:
        super().__init__(f"{blocker}: {detail}")
        self.blocker = blocker
        self.detail = detail


class MaterializedSubject(NamedTuple):
    """``InventoryBinding`` fields plus the committed bytes that were copied.

    ``contents`` maps each inventory entry to its committed bytes and is
    empty whenever any blocker is present, exactly like ``digests``.
    """

    digests: dict[str, str]
    blockers: list[str]
    details: list[str]
    contents: dict[str, bytes]

    @property
    def binding(self) -> InventoryBinding:
        return InventoryBinding(self.digests, self.blockers, self.details)


def _universal_newlines(data: bytes) -> str:
    """Decode like ``Path.read_text(encoding="utf-8")`` (CRLF and CR to LF)."""
    return data.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")


def parse_oracle_documents(documents: Mapping[str, bytes]) -> list[dict[str, Any]]:
    """``load_oracle_corpus`` over in-memory documents keyed by file name.

    Mirrors ``tools.run_r21_oracle_ab_proof.load_oracle_corpus`` exactly:
    names sorted, ``_``-prefixed names skipped, documents that are not a
    mapping or carry no ``cell`` skipped, the same six keys. The production
    loader is run over the same bytes at scoring time and must agree
    (``bind_scored_subject``), so any divergence aborts the run instead of
    scoring a different corpus.
    """
    out: list[dict[str, Any]] = []
    for name in sorted(documents):
        if not name.endswith(".yaml") or name.startswith("_"):
            continue
        data = yaml.safe_load(_universal_newlines(documents[name]))
        if not isinstance(data, dict):
            continue
        if not data.get("cell"):
            continue
        out.append(
            {
                "file": name,
                "solver": data.get("solver", ""),
                "domain": data.get("domain", ""),
                "cell": data.get("cell", ""),
                "positive": list(data.get("positive") or []),
                "negative": list(data.get("negative") or []),
            }
        )
    return out


def expected_hex_cells(config: bytes) -> dict[str, HexCellDefinition]:
    """The cells ``HexTopologyRegistry`` builds from ``config``, in load order.

    Mirrors ``HexTopologyRegistry._load`` field by field: a document that
    is not a mapping yields no cells, entries without ``id`` are skipped,
    a duplicate id or coordinate is skipped, and the same defaults apply.
    Routing (``select_origin_cell`` with no agents) is a pure function of
    these ordered cells, so a registry whose cells equal this mapping
    routes identically.
    """
    data = yaml.safe_load(_universal_newlines(config))
    cells: dict[str, HexCellDefinition] = {}
    coords: set[HexCoord] = set()
    if not data or not isinstance(data, dict):
        return cells
    for cell_data in data.get("cells", []):
        cell_id = cell_data.get("id")
        if not cell_id:
            continue
        coord_data = cell_data.get("coord", {})
        coord = HexCoord(q=coord_data.get("q", 0), r=coord_data.get("r", 0))
        if cell_id in cells or coord in coords:
            continue
        cells[cell_id] = HexCellDefinition(
            id=cell_id,
            coord=coord,
            description=cell_data.get("description", ""),
            domain_selectors=cell_data.get("domain_selectors", []),
            tag_selectors=cell_data.get("tag_selectors", []),
            enabled=cell_data.get("enabled", True),
            neighbor_policy=cell_data.get("neighbor_policy", "default"),
        )
        coords.add(coord)
    return cells


def bind_scored_subject(
    loaded_oracles: list[dict[str, Any]],
    registry: HexTopologyRegistry,
    subject: Mapping[str, bytes],
) -> list[dict[str, Any]]:
    """Return the corpus to score: the in-memory parse of ``subject``.

    ``loaded_oracles`` (the production loader's read of the private copy)
    must equal that parse, and ``registry.cells`` in load order must equal
    the cells derived from the in-memory config; otherwise
    ``SubjectMismatch`` is raised and nothing is scored. Nothing read from
    disk after the preflight is therefore ever scored unverified, and a
    rewrite of the copy that is reverted before a later check can only
    abort the run, never change its result.
    """
    oracle_prefix = CANONICAL_ORACLE_DIR.as_posix() + "/"
    documents = {
        PurePosixPath(rel).name: data
        for rel, data in subject.items()
        if rel.startswith(oracle_prefix)
    }
    config = subject.get(CANONICAL_HEX_CONFIG.as_posix())
    if config is None or not documents:
        raise SubjectMismatch(
            "subject_incomplete", "bound inventory lacks the corpus or the hex config"
        )
    try:
        expected_oracles = parse_oracle_documents(documents)
        expected_cells = expected_hex_cells(config)
    except Exception as exc:  # noqa: BLE001 - any parse failure is a mismatch
        raise SubjectMismatch(
            "subject_unparseable", exc.__class__.__name__
        ) from exc
    if loaded_oracles != expected_oracles:
        raise SubjectMismatch(
            "subject_corpus_mismatch",
            "the oracle corpus read from the private copy differs from the bound bytes",
        )
    if list(registry.cells.items()) != list(expected_cells.items()):
        raise SubjectMismatch(
            "subject_hex_config_mismatch",
            "the hex topology loaded from the private copy differs from the bound bytes",
        )
    return expected_oracles


def materialize_source_subject(
    root: Path | str,
    source_commit: object,
    rel_paths: tuple[str, ...],
    destination: Path | str,
) -> MaterializedSubject:
    """Copy the regular blobs tracked at ``source_commit`` under ``destination``.

    Every inventory entry is fetched from the git object store of ``root``
    through the pinned argv invocation, kept in ``contents`` and written as
    ``destination/entry`` with its committed bytes; the worktree is never
    read. ``digests`` maps each entry to the LF digest of those bytes;
    ``digests`` and ``contents`` are empty whenever any blocker is present,
    so a partial copy can never be scored. An entry that is absolute,
    rooted or contains ``..`` is refused before any git call.
    """
    if not isinstance(source_commit, str) or not SOURCE_COMMIT_PATTERN.match(
        source_commit
    ):
        return MaterializedSubject(
            {}, ["source_commit_invalid"], ["source_commit_invalid"], {}
        )
    destination = Path(destination)
    digests: dict[str, str] = {}
    blockers: list[str] = []
    details: list[str] = []
    contents: dict[str, bytes] = {}
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
        if blocker is None:
            assert data is not None
            contents[rel] = data
    if blockers:
        return MaterializedSubject({}, blockers, details, {})
    return MaterializedSubject(digests, [], [], contents)


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
    """Score the canonical corpus from the bound blobs, never from the worktree.

    ``binding`` is the inventory bound by the first preflight. The blobs
    tracked at ``source_commit`` are fetched into memory and copied into a
    fresh temporary directory that must resolve outside ``root``; the
    copy's digests must equal ``binding.digests``. The corpus that is
    scored is parsed from the in-memory bytes, and the production loaders'
    read of the copy must agree with them (``bind_scored_subject``); the
    copy is re-digested after scoring as a secondary check and removed
    afterwards on every path (best effort: cleanup errors are ignored,
    never raised). Returns ``(report, None)`` on success, else
    ``(None, (stage, blockers))`` for ``_abort``. Nothing written to the
    worktree or to the copy after the preflight can change what is scored;
    it can only abort the run.
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
            return None, ("subject copy", materialized.binding)
        if materialized.digests != binding.digests:
            return None, (
                "subject copy",
                InventoryBinding(
                    {},
                    ["subject_snapshot_mismatch"],
                    ["subject_snapshot_mismatch: copied blobs differ from the bound inventory"],
                ),
            )
        try:
            report = build_axis_b_report(
                oracle_dir=CANONICAL_ORACLE_DIR,
                hex_config=CANONICAL_HEX_CONFIG,
                root=subject_root,
                subject=materialized.contents,
            )
        except SubjectMismatch as exc:
            return None, (
                "subject verification",
                InventoryBinding({}, [exc.blocker], [f"{exc.blocker}: {exc.detail}"]),
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

#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""Build a fail-closed functional capability probe report for the HEX meshes.

The recovery bundle binds capability rows to source digests.  That proves a
plausibly shaped source existed at a commit; it does not prove the capability
still answers.  This report closes exactly that gap by executing each distinct
capability and recording what happened.

Two counting rules matter and are easy to get wrong:

* the bundle emits 59 capability ROWS across 15 genomes, but only 33 DISTINCT
  ``(kind, capability_id)`` pairs -- router and engine capabilities repeat on
  every cell.  This report is over the 33 distinct capabilities;
* the 33 are not homogeneous.  22 are executable axioms, one is the solver
  engine, seven are agent routing cells and three are router/geometry pins, so
  they need four different probe families rather than one loop.

The capability universe is re-derived here from the same tracked sources the
generator reads, and NOT by importing the generator, so the two remain
independent witnesses.

``verdict`` is the EXECUTION axis and uses the three mandated states.
Correctness is carried separately in ``assertions_failed`` and
``declared_divergences`` so that a mismatch against an unratified expectation
is never silently reported as a broken capability -- and never as a pass.

The tool reads only.  It writes nothing unless ``--output-json`` is given, makes
no network call, starts no runtime, and grants no authority.
"""

from __future__ import annotations

import argparse
import glob
import json
import math
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

REPORT_VERSION = "wd.hex_capability_probe_report.v1"
CORPUS_VERSION = "hex_capability_probe_corpus.v1"

REPO_ROOT = Path(__file__).resolve().parent.parent

AGENT_CONFIG_PATH = "configs/hex_cells.yaml"
AXIOM_ROOT = "configs/axioms"
ORACLE_DIR = "tests/oracle_hex"

# Mirrors the capability ids the recovery bundle emits.  Kept as an explicit
# contract so a drift between this report and the bundle fails a test rather
# than silently changing the denominator.
ROUTER_CAPABILITY_IDS = (
    "router.agent.axial",
    "router.agent.axial.geometry",
    "router.solver.logical",
)
ENGINE_CAPABILITY_ID = "solver.symbolic.engine"

EXPECTED_DISTINCT_TOTAL = 33
EXPECTED_BY_KIND = {"agent": 7, "router": 3, "solver": 23}

VERDICT_EXECUTES = "executes_correctly"
VERDICT_CANNOT = "cannot_execute"
VERDICT_NEEDS_INPUTS = "requires_caller_inputs"
VERDICTS = (VERDICT_EXECUTES, VERDICT_CANNOT, VERDICT_NEEDS_INPUTS)

# Thresholds owned by the tracked R22.2 gate (tests/test_r22_hex_aligned_eval.py).
# Mirrored, never tightened: this report must not invent a stricter contract than
# the one the repo already enforces and records.
PER_CELL_QUALITY_FLOOR = 0.6
MACRO_QUALITY_FLOOR = 0.74

VERIFIED_ORACLE_BASES = frozenset({"derived_from_declared_formulas", "operator_adjudicated"})
KNOWN_ORACLE_BASES = VERIFIED_ORACLE_BASES | {
    "executability_only",
    "imported_from_axiom_examples_unverified",
}

_CANONICAL_AXIAL_DIRECTIONS = frozenset(
    {(1, 0), (1, -1), (0, -1), (-1, 0), (-1, 1), (0, 1)}
)

_MAX_CORPUS_BYTES = 1024 * 1024
_MAX_CASES = 4096


class ProbeCorpusError(ValueError):
    """Raised when the corpus is unusable.  Always fails closed."""


def _fail(code: str) -> None:
    raise ProbeCorpusError(code)


def _finite_number(value: object) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    return False


def _reject_nonfinite(token: str) -> None:
    if token in {"NaN", "Infinity", "-Infinity"}:
        _fail("corpus_nonfinite_number")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    seen: dict[str, Any] = {}
    for key, value in pairs:
        if key in seen:
            _fail("corpus_duplicate_key")
        seen[key] = value
    return seen


def load_corpus(path: Path) -> dict[str, Any]:
    """Strict, bounded corpus load.  Any ambiguity is a rejection."""
    if not path.is_file():
        _fail("corpus_missing")
    raw = path.read_bytes()
    if not raw or len(raw) > _MAX_CORPUS_BYTES:
        _fail("corpus_size_out_of_bounds")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        _fail("corpus_not_utf8")
    try:
        document = json.loads(
            text, object_pairs_hook=_reject_duplicate_keys, parse_constant=_reject_nonfinite
        )
    except json.JSONDecodeError:
        _fail("corpus_not_strict_json")
    if not isinstance(document, dict):
        _fail("corpus_not_object")
    if document.get("corpus_version") != CORPUS_VERSION:
        _fail("corpus_version_mismatch")

    cases = document.get("solver_axiom_cases")
    if not isinstance(cases, list) or not cases:
        _fail("corpus_cases_missing")
    if len(cases) > _MAX_CASES:
        _fail("corpus_too_many_cases")

    seen_ids: set[str] = set()
    for case in cases:
        if not isinstance(case, dict):
            _fail("corpus_case_not_object")
        case_id = case.get("case_id")
        if not isinstance(case_id, str) or not case_id:
            _fail("corpus_case_id_invalid")
        if case_id in seen_ids:
            _fail("corpus_duplicate_case_id")
        seen_ids.add(case_id)
        if not isinstance(case.get("model_id"), str) or not case["model_id"]:
            _fail("corpus_case_model_id_invalid")
        if not isinstance(case.get("capability_id"), str) or not case["capability_id"]:
            _fail("corpus_case_capability_id_invalid")
        if not isinstance(case.get("inputs"), dict):
            _fail("corpus_case_inputs_invalid")
        for value in case["inputs"].values():
            if not _finite_number(value):
                _fail("corpus_case_input_not_finite_number")
        basis = case.get("oracle_basis")
        if basis not in KNOWN_ORACLE_BASES:
            _fail("corpus_case_oracle_basis_unknown")
        expected = case.get("expected_value")
        if expected is not None and not _finite_number(expected):
            _fail("corpus_case_expected_not_finite")
        if expected is not None and basis == "executability_only":
            _fail("corpus_case_executability_must_not_declare_expectation")
        tolerance = case.get("tolerance_ratio")
        if tolerance is not None and not (
            _finite_number(tolerance) and 0 <= float(tolerance) < 1
        ):
            _fail("corpus_case_tolerance_invalid")
    return document


def _strict_yaml(path: Path) -> Any:
    import yaml  # imported lazily so a corpus-only failure never needs PyYAML

    return yaml.safe_load(path.read_text(encoding="utf-8"))


def discover_capabilities(repo_root: Path) -> list[dict[str, str]]:
    """Re-derive the distinct capability universe from tracked sources.

    Independent of the bundle generator on purpose: if the two ever disagree,
    a test should catch it rather than one silently defining the other.
    """
    capabilities: list[dict[str, str]] = []

    config = _strict_yaml(repo_root / AGENT_CONFIG_PATH)
    if not isinstance(config, dict) or set(config) != {"cells"}:
        _fail("agent_config_shape_unexpected")
    for cell in config["cells"]:
        if not isinstance(cell, dict) or not isinstance(cell.get("id"), str):
            _fail("agent_cell_shape_unexpected")
        capabilities.append({"kind": "agent", "capability_id": f"agent.cell.{cell['id']}"})

    for router_id in ROUTER_CAPABILITY_IDS:
        capabilities.append({"kind": "router", "capability_id": router_id})

    model_ids: set[str] = set()
    for path in sorted(glob.glob(str(repo_root / AXIOM_ROOT / "**/*.yaml"), recursive=True)):
        document = _strict_yaml(Path(path))
        if not isinstance(document, dict):
            _fail("axiom_shape_unexpected")
        model_id = document.get("model_id")
        if not isinstance(model_id, str) or not model_id:
            _fail("axiom_model_id_invalid")
        if model_id in model_ids:
            _fail("axiom_model_id_duplicate")
        model_ids.add(model_id)
        capabilities.append(
            {"kind": "solver", "capability_id": f"solver.axiom.{model_id}"}
        )
    capabilities.append({"kind": "solver", "capability_id": ENGINE_CAPABILITY_ID})

    pairs = {(entry["kind"], entry["capability_id"]) for entry in capabilities}
    if len(pairs) != len(capabilities):
        _fail("capability_universe_not_distinct")
    return sorted(capabilities, key=lambda entry: (entry["kind"], entry["capability_id"]))


def _new_result(kind: str, capability_id: str) -> dict[str, Any]:
    return {
        "kind": kind,
        "capability_id": capability_id,
        "verdict": VERDICT_CANNOT,
        "cause": "not_probed",
        "assertions_checked": 0,
        "assertions_failed": 0,
        "declared_divergences": [],
        "oracle_status": "none",
    }


def probe_agent_cells(repo_root: Path, capabilities: Sequence[Mapping[str, str]]) -> list[dict]:
    """Score the tracked labelled utterances under the ESTABLISHED contract.

    Deliberately not a stricter bar than the repo already enforces.  The
    tracked R22.2 gate accepts partial positive matching -- paraphrased and
    Finnish utterances intentionally avoid the selector keywords and are
    documented headroom, not regressions -- and enforces three things instead:
    negatives must route away 100% of the time, each cell must score at least
    ``PER_CELL_QUALITY_FLOOR``, and the macro quality must clear
    ``MACRO_QUALITY_FLOOR``.  Counting every positive miss as a failure would
    report a healthy, gated system as broken.

    The score is recomputed here from the shared helpers rather than read from
    any recorded artifact, so a drift shows up as a disagreement.
    """
    results = []
    sys.path.insert(0, str(repo_root))
    try:
        from tools.run_r21_oracle_ab_proof import (  # noqa: E402
            load_oracle_corpus,
            quality_arm,
        )
        from waggledance.application.services.hex_topology_registry import (  # noqa: E402
            HexTopologyRegistry,
        )

        # agents=[] mirrors the tracked eval: a populated agent pool changes the
        # select_origin_cell tiebreaker and would score a different system.
        registry = HexTopologyRegistry(
            config_path=str(repo_root / AGENT_CONFIG_PATH), agents=[]
        )
        oracles = load_oracle_corpus(repo_root / ORACLE_DIR)
        scored = quality_arm(oracles, lambda utterance: registry.select_origin_cell(utterance))
        per_cell = {entry["cell"]: entry for entry in scored["per_file"]}
        macro_quality = float(scored["quality"])
    except Exception as error:  # pragma: no cover - defensive
        for entry in capabilities:
            result = _new_result(entry["kind"], entry["capability_id"])
            result["cause"] = f"agent_probe_raised:{type(error).__name__}"
            results.append(result)
        return results

    for entry in capabilities:
        cell_id = entry["capability_id"].split("agent.cell.", 1)[1]
        result = _new_result(entry["kind"], entry["capability_id"])
        scored_cell = per_cell.get(cell_id)
        if scored_cell is None:
            result["cause"] = "oracle_labels_missing"
            results.append(result)
            continue

        failed = 0
        if scored_cell["neg_correct"] != scored_cell["neg_total"]:
            failed += 1
        if float(scored_cell["file_score"]) < PER_CELL_QUALITY_FLOOR:
            failed += 1

        result["verdict"] = VERDICT_EXECUTES
        result["cause"] = ""
        result["assertions_checked"] = 2
        result["assertions_failed"] = failed
        result["oracle_status"] = "verified"
        result["routing_measurement"] = {
            "positives_matched": scored_cell["pos_correct"],
            "positives_total": scored_cell["pos_total"],
            "negatives_correct": scored_cell["neg_correct"],
            "negatives_total": scored_cell["neg_total"],
            "cell_quality": round(float(scored_cell["file_score"]), 4),
            "per_cell_floor": PER_CELL_QUALITY_FLOOR,
            "macro_quality": round(macro_quality, 4),
            "macro_floor": MACRO_QUALITY_FLOOR,
        }
        results.append(result)

    if macro_quality < MACRO_QUALITY_FLOOR and results:
        results[0]["assertions_failed"] += 1
        results[0]["cause"] = "macro_quality_below_floor"
    return results


def probe_routers(repo_root: Path, capabilities: Sequence[Mapping[str, str]]) -> list[dict]:
    """Re-derive the geometry and adjacency invariants rather than assert them."""
    results = []
    sys.path.insert(0, str(repo_root))
    for entry in capabilities:
        result = _new_result(entry["kind"], entry["capability_id"])
        checked = 0
        failed = 0
        try:
            if entry["capability_id"] == "router.agent.axial.geometry":
                from waggledance.core.domain.hex_mesh import AXIAL_DIRECTIONS  # noqa: E402

                checked += 1
                if {tuple(direction) for direction in AXIAL_DIRECTIONS} != _CANONICAL_AXIAL_DIRECTIONS:
                    failed += 1
            elif entry["capability_id"] == "router.agent.axial":
                from waggledance.application.services.hex_topology_registry import (  # noqa: E402
                    HexTopologyRegistry,
                )

                registry = HexTopologyRegistry(config_path=str(repo_root / AGENT_CONFIG_PATH))
                for cell_id in registry.cells:
                    checked += 1
                    neighbours = [cell.id for cell in registry.get_neighbor_cells(cell_id)]
                    if not neighbours:
                        failed += 1
                        continue
                    for neighbour in neighbours:
                        back = [cell.id for cell in registry.get_neighbor_cells(neighbour)]
                        if cell_id not in back:
                            failed += 1
                            break
            else:
                from waggledance.core.hex_cell_topology import ALL_CELLS, _ADJACENCY  # noqa: E402

                for cell_id in ALL_CELLS:
                    checked += 1
                    neighbours = _ADJACENCY.get(cell_id, frozenset())
                    if not neighbours:
                        failed += 1
                        continue
                    for neighbour in neighbours:
                        if cell_id not in _ADJACENCY.get(neighbour, frozenset()):
                            failed += 1
                            break
        except Exception as error:  # pragma: no cover - defensive
            result["cause"] = f"router_probe_raised:{type(error).__name__}"
            results.append(result)
            continue

        result["verdict"] = VERDICT_EXECUTES
        result["cause"] = ""
        result["assertions_checked"] = checked
        result["assertions_failed"] = failed
        result["oracle_status"] = "verified" if checked else "none"
        results.append(result)
    return results


def _solver(repo_root: Path):
    sys.path.insert(0, str(repo_root))
    from core.symbolic_solver import SymbolicSolver  # noqa: E402

    return SymbolicSolver()


def probe_solver_axioms(
    repo_root: Path,
    capabilities: Sequence[Mapping[str, str]],
    corpus: Mapping[str, Any],
) -> list[dict]:
    """Execute every axiom capability through the real solver."""
    solver = _solver(repo_root)
    by_capability: dict[str, list[Mapping[str, Any]]] = {}
    for case in corpus["solver_axiom_cases"]:
        by_capability.setdefault(case["capability_id"], []).append(case)

    results = []
    for entry in capabilities:
        capability_id = entry["capability_id"]
        result = _new_result(entry["kind"], capability_id)
        cases = by_capability.get(capability_id)
        if not cases:
            result["cause"] = "no_corpus_case_for_capability"
            results.append(result)
            continue

        produced_any = False
        blocked_cause = ""
        needs_inputs = False
        checked = 0
        failed = 0
        divergences: list[dict[str, Any]] = []
        oracle_status = "none"

        for case in sorted(cases, key=lambda item: item["case_id"]):
            outcome = solver.solve(case["model_id"], dict(case["inputs"]))
            if not outcome.success or outcome.value is None:
                error = str(outcome.error or "no_value")
                if "is not defined" in error and not case["inputs"]:
                    needs_inputs = True
                else:
                    blocked_cause = blocked_cause or error[:120]
                continue
            produced_any = True

            expected = case.get("expected_value")
            if expected is None:
                continue
            checked += 1
            tolerance = float(case.get("tolerance_ratio") or 0.0)
            actual = outcome.value
            matched = _finite_number(actual) and abs(
                float(actual) - float(expected)
            ) <= max(1e-9, abs(float(expected)) * tolerance)
            basis = case["oracle_basis"]
            if basis in VERIFIED_ORACLE_BASES:
                oracle_status = "verified" if oracle_status != "unverified" else "unverified"
                if not matched:
                    failed += 1
            else:
                oracle_status = "unverified"
                if not matched:
                    divergences.append(
                        {
                            "case_id": case["case_id"],
                            "expected": expected,
                            "actual": actual if _finite_number(actual) else repr(actual),
                            "oracle_basis": basis,
                        }
                    )

        if produced_any:
            result["verdict"] = VERDICT_EXECUTES
            result["cause"] = ""
        elif needs_inputs:
            result["verdict"] = VERDICT_NEEDS_INPUTS
            result["cause"] = "declared_variables_without_defaults"
        else:
            result["verdict"] = VERDICT_CANNOT
            result["cause"] = blocked_cause or "no_result_produced"

        result["assertions_checked"] = checked
        result["assertions_failed"] = failed
        result["declared_divergences"] = divergences
        result["oracle_status"] = oracle_status
        results.append(result)
    return results


def probe_engine(repo_root: Path, capability: Mapping[str, str]) -> dict:
    """Engine-level probe, including a positive test of the safety guard."""
    result = _new_result(capability["kind"], capability["capability_id"])
    checked = 0
    failed = 0
    try:
        solver = _solver(repo_root)
        models = solver.registry.list_models()

        checked += 1
        if len(set(models)) != 22:
            failed += 1

        checked += 1
        if solver.registry.get(models[0]) is None:
            failed += 1

        # unknown model must fail closed rather than raise or invent a value
        checked += 1
        unknown = solver.solve("no_such_model_id", {})
        if unknown.success or unknown.value is not None or not unknown.error:
            failed += 1

        # the forbidden-function guard must actually reject, not silently pass
        checked += 1
        guarded = solver.solve("indoor_air_quality", {})
        if guarded.success or "Forbidden function" not in str(guarded.error or ""):
            failed += 1
    except Exception as error:  # pragma: no cover - defensive
        result["cause"] = f"engine_probe_raised:{type(error).__name__}"
        return result

    result["verdict"] = VERDICT_EXECUTES
    result["cause"] = ""
    result["assertions_checked"] = checked
    result["assertions_failed"] = failed
    result["oracle_status"] = "verified"
    return result


def build_report(repo_root: Path, corpus_path: Path) -> dict[str, Any]:
    corpus = load_corpus(corpus_path)
    capabilities = discover_capabilities(repo_root)

    by_kind: dict[str, list[Mapping[str, str]]] = {"agent": [], "router": [], "solver": []}
    for entry in capabilities:
        by_kind[entry["kind"]].append(entry)

    results: list[dict[str, Any]] = []
    results.extend(probe_agent_cells(repo_root, by_kind["agent"]))
    results.extend(probe_routers(repo_root, by_kind["router"]))

    axiom_caps = [e for e in by_kind["solver"] if e["capability_id"] != ENGINE_CAPABILITY_ID]
    engine_caps = [e for e in by_kind["solver"] if e["capability_id"] == ENGINE_CAPABILITY_ID]
    results.extend(probe_solver_axioms(repo_root, axiom_caps, corpus))
    for entry in engine_caps:
        results.append(probe_engine(repo_root, entry))

    results.sort(key=lambda item: (item["kind"], item["capability_id"]))

    # Every aggregate below is DERIVED from the results just computed.  Nothing
    # in the corpus can raise them.
    counts = {verdict: 0 for verdict in VERDICTS}
    for item in results:
        counts[item["verdict"]] += 1
    distinct_by_kind = {kind: len(entries) for kind, entries in by_kind.items()}
    total_failed = sum(item["assertions_failed"] for item in results)
    total_divergences = sum(len(item["declared_divergences"]) for item in results)
    unverified = [
        item["capability_id"] for item in results if item["oracle_status"] != "verified"
    ]

    contract_ok = (
        len(results) == EXPECTED_DISTINCT_TOTAL
        and distinct_by_kind == EXPECTED_BY_KIND
    )
    verified = bool(
        contract_ok
        and counts[VERDICT_EXECUTES] == EXPECTED_DISTINCT_TOTAL
        and total_failed == 0
        and total_divergences == 0
        and not unverified
    )

    blocking: list[str] = []
    if not contract_ok:
        blocking.append("distinct_capability_contract_mismatch")
    if counts[VERDICT_CANNOT]:
        blocking.append("capabilities_cannot_execute")
    if counts[VERDICT_NEEDS_INPUTS]:
        blocking.append("capabilities_require_caller_inputs")
    if total_failed:
        blocking.append("verified_oracle_assertions_failed")
    if total_divergences:
        blocking.append("declared_divergences_against_unratified_expectations")
    if unverified:
        blocking.append("capabilities_without_verified_oracle")

    return {
        "report_version": REPORT_VERSION,
        "corpus_version": corpus.get("corpus_version"),
        "base_commit_declared_by_corpus": corpus.get("base_commit"),
        "distinct_capability_total": len(results),
        "distinct_capability_by_kind": distinct_by_kind,
        "genome_capability_rows_note": (
            "the bundle emits 59 rows across 15 genomes; this report covers the 33 distinct pairs"
        ),
        "verdict_counts": counts,
        "assertions_failed_total": total_failed,
        "declared_divergence_total": total_divergences,
        "capabilities_without_verified_oracle": sorted(unverified),
        "functional_capability_recovery_verified": verified,
        "blocking_reasons": sorted(blocking),
        "capabilities": results,
        "measurement_only": True,
        "runtime_started": False,
        "claim_safe_upgrade": False,
        "production_ready_claim": False,
        "recovery_ready_claim": False,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--corpus",
        default=str(REPO_ROOT / "tests/tools/hex_capability_probe_corpus.v1.json"),
    )
    parser.add_argument("--repo-root", default=str(REPO_ROOT))
    parser.add_argument("--output-json", default="")
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = build_report(Path(args.repo_root), Path(args.corpus))
    except ProbeCorpusError as error:
        payload = {
            "report_version": REPORT_VERSION,
            "ok": False,
            "error": str(error),
            "functional_capability_recovery_verified": False,
        }
        print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
        return 2

    text = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output_json:
        Path(args.output_json).write_text(text, encoding="utf-8")
    if args.json or not args.output_json:
        print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    else:
        print(f"distinct_capabilities={report['distinct_capability_total']}")
        print(f"verdicts={report['verdict_counts']}")
        print(
            "functional_capability_recovery_verified="
            f"{str(report['functional_capability_recovery_verified']).lower()}"
        )
    return 0 if report["functional_capability_recovery_verified"] else 3


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Evaluate a frozen executable-outcome smoke gate for MAGMA retrieval.

This is a candidate-only development gate.  It proves that one known numeric
query can travel from a structurally reverified global FAISS snapshot into the
real deterministic ``SymbolicSolver`` and that three known OOD queries abstain
without invoking that executor.  It does not enable a runtime route, evaluate
hex pruning, authenticate self-certified receipts, or grant promotion authority.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.symbolic_solver import SymbolicSolver, extract_inputs_from_query  # noqa: E402
from tools import benchmark_magma_solver_retrieval as retrieval_benchmark  # noqa: E402
from tools import materialize_magma_faiss_candidate as candidate_snapshot  # noqa: E402
from waggledance.core.magma.canonical import sha256_digest  # noqa: E402
from waggledance.core.reasoning import question_frame  # noqa: E402


REPORT_SCHEMA = "magma.faiss.executable_outcome_smoke.v2"
SUITE_SCHEMA = "magma.faiss.executable_outcome_suite.v1"
DEFAULT_MIN_SCORE = 0.55
VALUE_TOLERANCE = 1.0e-9
CALLER_RETRIEVAL_SCOPE = "caller_supplied_unverified"
LIVE_RETRIEVAL_SCOPE = "verified_snapshot_session_global_all_cells"
_SESSION_ID = re.compile(r"^faisssession_[0-9a-f]{32}$")
_SNAPSHOT_ID = re.compile(r"^faisscand_[0-9a-f]{64}$")
_NON_EXECUTABLE_SPEECH_ACT = re.compile(
    r"\b(?:translate|translation|käännä|kaanna|joke|vitsi|poem|runo|story|"
    r"tarina|lyrics|summari[sz]e|tiivistä|tiivista)\b",
    re.IGNORECASE,
)
_HEATING_REQUIRED_INPUTS = frozenset(
    {"T_outdoor", "area_m2", "spot_price_ckwh"}
)


class OutcomeGateContractError(ValueError):
    """Gate input or evidence violates the fail-closed smoke contract."""


class OutcomeGateUnavailable(RuntimeError):
    """A required local candidate dependency is unavailable."""


@dataclass(frozen=True)
class FrozenOutcomeCase:
    case_id: str
    query: str
    expected_disposition: str
    expected_solver_id: str | None
    expected_admission_reason: str


FROZEN_CASES = (
    FrozenOutcomeCase(
        case_id="heating_cost_96m2_minus5c_12ckwh",
        query=(
            "For a 96 m² floor, exterior chill of -5°C, and a 12 c/kWh "
            "tariff, what is the thirty-day spend to maintain indoor comfort?"
        ),
        expected_disposition="execute",
        expected_solver_id="heating_cost",
        expected_admission_reason="explicit_heating_contract_satisfied",
    ),
    FrozenOutcomeCase(
        case_id="translate_monthly_heating_bill_spanish",
        query="Translate the phrase 'monthly heating bill' into Spanish.",
        expected_disposition="abstain",
        expected_solver_id=None,
        expected_admission_reason="non_executable_speech_act",
    ),
    FrozenOutcomeCase(
        case_id="bee_winter_joke",
        query="Tell me a joke about bees surviving winter.",
        expected_disposition="abstain",
        expected_solver_id=None,
        expected_admission_reason="non_executable_speech_act",
    ),
    FrozenOutcomeCase(
        case_id="bee_winter_reserves_route_rejection",
        query="How much honey should a bee colony store to survive winter?",
        expected_disposition="abstain",
        expected_solver_id=None,
        expected_admission_reason="solver_not_in_frozen_executable_allowlist",
    ),
)

_EXPECTED_DERIVATION = (
    ("heat_loss_rate", "W", 832.0),
    ("daily_energy_kwh", "kWh", 19.968),
    ("daily_cost", "€", 2.39616),
    ("monthly_cost", "€", 71.8848),
)
_EXPECTED_INPUTS = {
    "T_outdoor": -5.0,
    "area_m2": 96.0,
    "spot_price_ckwh": 12.0,
}


def _suite_identity(minimum_candidate_score: float) -> dict[str, Any]:
    return {
        "schema_version": SUITE_SCHEMA,
        "cases": [asdict(case) for case in FROZEN_CASES],
        "minimum_candidate_score": minimum_candidate_score,
        "positive_required_explicit_inputs": sorted(_HEATING_REQUIRED_INPUTS),
        "positive_expected_inputs": _EXPECTED_INPUTS,
        "positive_expected_derivation": [
            {"name": name, "unit": unit, "value": value}
            for name, unit, value in _EXPECTED_DERIVATION
        ],
    }


def _suite_digest(minimum_candidate_score: float) -> str:
    return sha256_digest(_suite_identity(minimum_candidate_score))


FROZEN_SUITE_DIGEST = _suite_digest(DEFAULT_MIN_SCORE)


def _require_finite_score(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise OutcomeGateContractError("candidate score is not numeric")
    score = float(value)
    if not math.isfinite(score) or score < -1.000001 or score > 1.000001:
        raise OutcomeGateContractError("candidate score is outside cosine range")
    return score


def _normalize_candidates(
    raw_candidates: Any,
) -> tuple[list[dict[str, Any]], tuple[str, str] | None]:
    if type(raw_candidates) is not list:
        raise OutcomeGateContractError("candidate retrieval result must be a list")
    normalized: list[dict[str, Any]] = []
    seen_solvers: set[str] = set()
    binding: tuple[str, str] | None = None
    for raw in raw_candidates:
        if type(raw) is not dict:
            raise OutcomeGateContractError("candidate retrieval row must be a dict")
        solver_id = raw.get("canonical_solver_id")
        if type(solver_id) is not str or not solver_id or solver_id in seen_solvers:
            raise OutcomeGateContractError("candidate solver identity is invalid or duplicated")
        seen_solvers.add(solver_id)
        score = _require_finite_score(raw.get("score"))
        snapshot_id = raw.get("snapshot_id")
        session_id = raw.get("verification_session_id")
        if (
            type(snapshot_id) is not str
            or not _SNAPSHOT_ID.fullmatch(snapshot_id)
            or type(session_id) is not str
            or not _SESSION_ID.fullmatch(session_id)
        ):
            raise OutcomeGateContractError("candidate snapshot/session binding is invalid")
        row_binding = (snapshot_id, session_id)
        if binding is None:
            binding = row_binding
        elif row_binding != binding:
            raise OutcomeGateContractError("candidate rows cross verification sessions")
        if (
            raw.get("source_commit_reverified") is not True
            or raw.get("source_reverification_scope") != "session_open"
            or raw.get("source_reverified_during_search_call") is not False
            or raw.get("receipt_bound") is not True
            or raw.get("receipt_structure_reverified") is not True
            or raw.get("receipt_authenticity_verified") is not False
            or raw.get("solver_outcome_verified") is not False
            or raw.get("runtime_authority_granted") is not False
        ):
            raise OutcomeGateContractError(
                "candidate provenance or authority posture is invalid"
            )
        normalized.append(
            {
                "canonical_solver_id": solver_id,
                "score": score,
                "cell_id": raw.get("cell_id"),
                "projection_id": raw.get("projection_id"),
                "snapshot_id": snapshot_id,
                "verification_session_id": session_id,
            }
        )
    expected_order = sorted(
        normalized,
        key=lambda row: (-row["score"], row["canonical_solver_id"]),
    )
    if normalized != expected_order:
        raise OutcomeGateContractError("candidate rows are not deterministically ranked")
    return normalized, binding


def evaluate_admission(
    query: str,
    candidates: Sequence[Mapping[str, Any]],
    *,
    minimum_score: float = DEFAULT_MIN_SCORE,
) -> dict[str, Any]:
    """Make a label-blind, fail-closed execution admission decision."""
    if type(query) is not str or not query.strip():
        raise OutcomeGateContractError("outcome query is empty")
    threshold = _require_finite_score(minimum_score)
    frame = question_frame.parse(query)
    explicit_inputs = extract_inputs_from_query(query, "heating_cost")
    decision = {
        "admitted": False,
        "reason": "no_candidate",
        "chosen_solver_id": None,
        "chosen_score": None,
        "question_frame": frame.to_dict(),
        "explicit_inputs": explicit_inputs,
        "required_explicit_inputs": sorted(_HEATING_REQUIRED_INPUTS),
        "minimum_score": threshold,
    }
    if not candidates:
        return decision
    top = candidates[0]
    solver_id = top["canonical_solver_id"]
    score = _require_finite_score(top["score"])
    decision["chosen_solver_id"] = solver_id
    decision["chosen_score"] = score
    if _NON_EXECUTABLE_SPEECH_ACT.search(query):
        decision["reason"] = "non_executable_speech_act"
        return decision
    if solver_id != "heating_cost":
        decision["reason"] = "solver_not_in_frozen_executable_allowlist"
        return decision
    if frame.desired_output != "numeric":
        decision["reason"] = "incompatible_question_frame"
        return decision
    if score < threshold:
        decision["reason"] = "candidate_below_minimum_score"
        return decision
    missing = sorted(_HEATING_REQUIRED_INPUTS - set(explicit_inputs))
    if missing:
        decision["reason"] = "missing_required_explicit_inputs"
        decision["missing_explicit_inputs"] = missing
        return decision
    decision["admitted"] = True
    decision["reason"] = "explicit_heating_contract_satisfied"
    return decision


def _read_result_field(result: Any, name: str) -> Any:
    if isinstance(result, Mapping):
        return result.get(name)
    return getattr(result, name, None)


def _normalize_solver_outcome(result: Any) -> dict[str, Any]:
    success = _read_result_field(result, "success")
    value = _read_result_field(result, "value")
    unit = _read_result_field(result, "unit")
    formula_used = _read_result_field(result, "formula_used")
    inputs_used = _read_result_field(result, "inputs_used")
    derivation = _read_result_field(result, "derivation_steps")
    validation = _read_result_field(result, "validation")
    error = _read_result_field(result, "error")
    fallback = _read_result_field(result, "fallback_suggested")
    if (
        type(success) is not bool
        or isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or type(unit) is not str
        or type(formula_used) is not str
        or type(inputs_used) is not dict
        or type(derivation) is not list
        or type(validation) is not list
    ):
        raise OutcomeGateContractError("solver outcome shape is invalid")
    steps: list[dict[str, Any]] = []
    for raw in derivation:
        if type(raw) is not dict:
            raise OutcomeGateContractError("solver derivation step is invalid")
        step_value = raw.get("value")
        if (
            type(raw.get("name")) is not str
            or type(raw.get("unit")) is not str
            or isinstance(step_value, bool)
            or not isinstance(step_value, (int, float))
            or not math.isfinite(float(step_value))
        ):
            raise OutcomeGateContractError("solver derivation value is invalid")
        steps.append(
            {
                "name": raw["name"],
                "unit": raw["unit"],
                "value": float(step_value),
            }
        )
    if not validation:
        raise OutcomeGateContractError("solver validation evidence is empty")
    validation_passed = all(
        type(row) is dict and row.get("passed") is True for row in validation
    )
    return {
        "success": success,
        "value": float(value),
        "unit": unit,
        "formula_used": formula_used,
        "inputs_used": inputs_used,
        "derivation": steps,
        "validation_passed": validation_passed,
        "error": error,
        "fallback_suggested": fallback,
    }


def _positive_outcome_matches_oracle(outcome: Mapping[str, Any] | None) -> bool:
    if outcome is None:
        return False
    if (
        outcome["success"] is not True
        or abs(outcome["value"] - 71.8848) > VALUE_TOLERANCE
        or outcome["unit"] != "€"
        or outcome["formula_used"] != "monthly_cost"
        or outcome["inputs_used"] != _EXPECTED_INPUTS
        or outcome["validation_passed"] is not True
        or outcome["error"] is not None
        or outcome["fallback_suggested"] is not None
    ):
        return False
    expected_steps = [
        {"name": name, "unit": unit, "value": value}
        for name, unit, value in _EXPECTED_DERIVATION
    ]
    observed_steps = outcome["derivation"]
    if len(observed_steps) != len(expected_steps):
        return False
    return all(
        observed["name"] == expected["name"]
        and observed["unit"] == expected["unit"]
        and abs(observed["value"] - expected["value"]) <= VALUE_TOLERANCE
        for observed, expected in zip(observed_steps, expected_steps)
    )


def run_frozen_outcome_gate(
    retrieve_candidates: Callable[[str], list[dict[str, Any]]],
    *,
    minimum_score: float = DEFAULT_MIN_SCORE,
) -> dict[str, Any]:
    """Run all frozen cases through the gate-owned deterministic solver."""
    if not callable(retrieve_candidates):
        raise OutcomeGateContractError("candidate retrieval dependency must be callable")
    threshold = _require_finite_score(minimum_score)
    solver = SymbolicSolver()
    cases: list[dict[str, Any]] = []
    executor_call_count = 0
    suite_binding: tuple[str, str] | None = None
    for case in FROZEN_CASES:
        normalized, binding = _normalize_candidates(
            retrieve_candidates(case.query)
        )
        if binding is not None:
            if suite_binding is None:
                suite_binding = binding
            elif binding != suite_binding:
                raise OutcomeGateContractError(
                    "frozen cases were retrieved through different sessions"
                )
        admission = evaluate_admission(
            case.query, normalized, minimum_score=threshold
        )
        calls_before = executor_call_count
        outcome: dict[str, Any] | None = None
        execution_error: str | None = None
        if admission["admitted"]:
            executor_call_count += 1
            try:
                outcome = _normalize_solver_outcome(
                    solver.solve_for_chat(admission["chosen_solver_id"], case.query)
                )
            except Exception as exc:  # recorded evidence, never a silent fallback
                execution_error = type(exc).__name__
        calls_for_case = executor_call_count - calls_before
        if case.expected_disposition == "execute":
            case_pass = (
                admission["admitted"] is True
                and admission["chosen_solver_id"] == case.expected_solver_id
                and admission["reason"] == case.expected_admission_reason
                and calls_for_case == 1
                and execution_error is None
                and _positive_outcome_matches_oracle(outcome)
            )
        else:
            case_pass = (
                admission["admitted"] is False
                and admission["reason"] == case.expected_admission_reason
                and calls_for_case == 0
                and outcome is None
                and execution_error is None
            )
        cases.append(
            {
                "case_id": case.case_id,
                "query": case.query,
                "expected_disposition": case.expected_disposition,
                "expected_solver_id": case.expected_solver_id,
                "expected_admission_reason": case.expected_admission_reason,
                "candidate_count": len(normalized),
                "top_candidate": normalized[0] if normalized else None,
                "admission": admission,
                "executor_call_count": calls_for_case,
                "outcome": outcome,
                "execution_error_type": execution_error,
                "case_pass": case_pass,
            }
        )
    all_passed = all(row["case_pass"] is True for row in cases)
    negative_rows = [
        row for row in cases if row["expected_disposition"] == "abstain"
    ]
    positive_rows = [
        row for row in cases if row["expected_disposition"] == "execute"
    ]
    return {
        "schema_version": REPORT_SCHEMA,
        "suite_schema_version": SUITE_SCHEMA,
        "suite_digest": _suite_digest(threshold),
        "minimum_candidate_score": threshold,
        "suite_role": "development_smoke_not_held_out",
        "case_count": len(cases),
        "positive_case_count": len(positive_rows),
        "ood_negative_case_count": len(negative_rows),
        "retrieval_evidence_scope": CALLER_RETRIEVAL_SCOPE,
        "candidate_snapshot_verified": False,
        "embedding_catalog_verified": False,
        "global_all_cell_search_required": True,
        "global_all_cell_search_verified": False,
        "cell_local_pruning_evaluated": False,
        "route_and_executable_outcome_observed": executor_call_count > 0,
        "negative_zero_executor_calls": all(
            row["executor_call_count"] == 0 for row in negative_rows
        ),
        "executor_call_count": executor_call_count,
        "frozen_smoke_gate_evaluated": True,
        "frozen_smoke_gate_pass": all_passed,
        "live_candidate_gate_evaluated": False,
        "live_candidate_gate_pass": False,
        "production_promotion_gate_evaluated": False,
        "production_promotion_gate_pass": False,
        "promotion_applied": False,
        "product_readiness_evidence": False,
        "runtime_authority_granted": False,
        "receipt_authenticity_verified": False,
        "bee_genome_claim_supported": False,
        "snapshot_id": suite_binding[0] if suite_binding else None,
        "verification_session_id": suite_binding[1] if suite_binding else None,
        "cases": cases,
    }


def run_live_gate(
    request_path: Path | str,
    snapshot_dir: Path | str,
    *,
    ollama_url: str = retrieval_benchmark.DEFAULT_OLLAMA_URL,
    k: int = 5,
    minimum_score: float = DEFAULT_MIN_SCORE,
    repo_root: Path = ROOT,
) -> dict[str, Any]:
    """Run the frozen suite through native FAISS, Ollama, and SymbolicSolver."""
    if type(k) is not int or k <= 0:
        raise OutcomeGateContractError("gate k must be a positive integer")
    try:
        request = candidate_snapshot.load_candidate_request(
            request_path, repo_root=repo_root
        )
        session = candidate_snapshot.open_verified_candidate_search_session(
            snapshot_dir, expected_request=request
        )
    except candidate_snapshot.CandidateUnavailable as exc:
        raise OutcomeGateUnavailable(str(exc)) from exc
    except candidate_snapshot.CandidateContractError as exc:
        raise OutcomeGateContractError(str(exc)) from exc
    try:
        profile = candidate_snapshot._profile_from_contract(
            request.embedding_contract
        )
        with retrieval_benchmark.OllamaEmbeddingClient(ollama_url) as embedder:
            identity_before = embedder.verify_profile(profile)

            def retrieve(query: str) -> list[dict[str, Any]]:
                matrix = embedder.embed(
                    [profile.query_prefix + query],
                    profile,
                    label="outcome_gate_query_embedding",
                )
                return session.search(matrix[0], k=k)

            report = run_frozen_outcome_gate(
                retrieve,
                minimum_score=minimum_score,
            )
            identity_after = embedder.verify_profile(profile)
            if identity_before != identity_after:
                raise OutcomeGateContractError(
                    "embedding catalog changed during outcome gate"
                )
            if not retrieval_benchmark.provider_identity_matches_profile(
                identity_before, profile
            ):
                raise OutcomeGateContractError(
                    "embedding provider identity does not match request"
                )
            report["embedding_provider_identity"] = {
                **identity_before,
                "catalog_contract_verified_before_suite": True,
                "catalog_contract_verified_after_suite": True,
                "response_digest_attested": False,
            }
            report.update(
                {
                    "retrieval_evidence_scope": LIVE_RETRIEVAL_SCOPE,
                    "candidate_snapshot_verified": True,
                    "embedding_catalog_verified": True,
                    "global_all_cell_search_verified": True,
                    "live_candidate_gate_evaluated": True,
                    "live_candidate_gate_pass": (
                        report["frozen_smoke_gate_pass"] is True
                    ),
                }
            )
            return report
    except (
        candidate_snapshot.CandidateUnavailable,
        retrieval_benchmark.BenchmarkUnavailable,
    ) as exc:
        raise OutcomeGateUnavailable(str(exc)) from exc
    except (
        candidate_snapshot.CandidateContractError,
        retrieval_benchmark.EmbeddingValidationError,
    ) as exc:
        raise OutcomeGateContractError(str(exc)) from exc
    finally:
        session.close()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", required=True)
    parser.add_argument("--snapshot", required=True)
    parser.add_argument(
        "--ollama-url", default=retrieval_benchmark.DEFAULT_OLLAMA_URL
    )
    parser.add_argument("--k", type=int, default=5)
    parser.add_argument("--minimum-score", type=float, default=DEFAULT_MIN_SCORE)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        report = run_live_gate(
            args.request,
            args.snapshot,
            ollama_url=args.ollama_url,
            k=args.k,
            minimum_score=args.minimum_score,
        )
    except (OutcomeGateContractError, OutcomeGateUnavailable) as exc:
        print(
            json.dumps(
                {
                    "schema_version": REPORT_SCHEMA,
                    "frozen_smoke_gate_pass": False,
                    "live_candidate_gate_evaluated": False,
                    "live_candidate_gate_pass": False,
                    "runtime_authority_granted": False,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 2
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    return 0 if report["live_candidate_gate_pass"] is True else 1


if __name__ == "__main__":
    raise SystemExit(main())

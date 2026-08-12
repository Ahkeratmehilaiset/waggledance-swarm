from __future__ import annotations

import inspect
from types import SimpleNamespace

import numpy as np
import pytest

from tools import benchmark_magma_faiss_candidate_latency as latency
from tools import benchmark_magma_solver_retrieval as retrieval_benchmark


SNAPSHOT_ID = "faisscand_" + "a" * 64
SESSION_ID = "faisssession_" + "b" * 32
REOPEN_SESSION_ID = "faisssession_" + "c" * 32


def _anchor_manifest() -> dict:
    counts = dict(latency.SYNTHETIC_SCALE_ANCHOR_COUNTS)
    source_commits = dict(latency.SYNTHETIC_SCALE_ANCHOR_SOURCE_COMMITS)
    return {
        "snapshot_id": latency.SYNTHETIC_SCALE_ANCHOR_SNAPSHOT_ID,
        "topology_digest": latency.SYNTHETIC_SCALE_ANCHOR_TOPOLOGY_DIGEST,
        "total_vector_count": latency.SYNTHETIC_SCALE_ANCHOR_TOTAL,
        "embedding_contract": {"dimension": latency.SYNTHETIC_SCALE_DIMENSION},
        "cells": [
            {
                "cell_id": cell_id,
                "vector_count": counts[cell_id],
                "source_projection_commit_id": source_commits[cell_id],
            }
            for cell_id in latency.SYNTHETIC_SCALE_CELL_ORDER
        ],
        **dict(latency.SYNTHETIC_SCALE_ANCHOR_FAISS_IDENTITY),
    }


def _successful_forecast_fixture(*, p95_ms: float = 1.0) -> dict:
    anchor_manifest = _anchor_manifest()
    anchor = latency._validate_scale_anchor(anchor_manifest)
    scenarios = []
    for index, (multiplier, distribution) in enumerate(
        (multiplier, distribution)
        for multiplier in latency.SYNTHETIC_SCALE_MULTIPLIERS
        for distribution in latency.SYNTHETIC_SCALE_DISTRIBUTIONS
    ):
        plan = latency._derive_forecast_cell_counts(
            anchor_manifest,
            multiplier=multiplier,
            distribution=distribution,
        )
        scenario_contract = {
            "base_seed": latency.SYNTHETIC_SCALE_SEED,
            "anchor_snapshot_id": latency.SYNTHETIC_SCALE_ANCHOR_SNAPSHOT_ID,
            "scale_multiplier": multiplier,
            "distribution": distribution,
            "total_vector_count": plan["total_vector_count"],
            "cell_counts": plan["cell_counts"],
            "dimension": latency.SYNTHETIC_SCALE_DIMENSION,
            "k": latency.SYNTHETIC_SCALE_K,
        }
        summary = {
            "p50": p95_ms,
            "p95": p95_ms,
            "p99": p95_ms,
            "max": p95_ms,
            "mean": p95_ms,
        }
        raw_bytes = plan["raw_float32_vector_payload_bytes"]
        scenarios.append(
            {
                **plan,
                "scenario_contract_digest": latency._sha256_json(
                    scenario_contract
                ),
                "synthetic_vector_input_sha256": (
                    "sha256:" + f"{index + 1:064x}"
                ),
                "query_vector_sha256": "sha256:" + "f" * 64,
                "raw_float32_vector_payload_mib": round(
                    raw_bytes / (1024**2), 6
                ),
                "raw_float32_vector_payload_is_memory_lower_bound_only": True,
                "peak_process_memory_evaluated": False,
                "faiss_index_overhead_memory_evaluated": False,
                "warmup_search_count": latency.SYNTHETIC_SCALE_WARMUP_ROUNDS,
                "search_execution_count": latency.SYNTHETIC_SCALE_REPETITIONS,
                "k": latency.SYNTHETIC_SCALE_K,
                "query_count": 1,
                "synthetic_input_generation_excluded_from_latency": True,
                "index_build_excluded_from_latency": True,
                "candidate_search_call_includes_query_normalization_and_python_merge": True,
                "search_result_evidence_validation_included_in_latency": True,
                "latency_ms": {"candidate_all_cell_search": summary},
                "latency_reference": {
                    "max_p95_ms": latency.DEFAULT_MAX_SEARCH_P95_MS,
                    "observed_p95_ms": p95_ms,
                    "target_met": p95_ms < latency.DEFAULT_MAX_SEARCH_P95_MS,
                    "hard_gate": False,
                    "authority_effect": "none",
                },
                "candidate_search_algorithm_reused": True,
                "candidate_search_algorithm": (
                    "materialize_magma_faiss_candidate._search_loaded_candidate"
                ),
                "production_runtime_path_evaluated": False,
                "cutoff_tie_worst_case_evaluated": False,
                "solver_quality_evaluated": False,
                "routing_recall_evaluated": False,
                "cell_pruning_evaluated": False,
                "cell_pruning_authorized": False,
                "runtime_authority_granted": False,
                "production_promotion_gate_pass": False,
            }
        )
    return {
        "schema_version": latency.SYNTHETIC_FORECAST_SCHEMA,
        "generated_at_utc": "2026-08-12T00:00:00+00:00",
        "measurement_complete": True,
        "evidence_scope": "synthetic_candidate_search_algorithm_capacity_only",
        "anchor": anchor,
        "faiss_identity": dict(latency.SYNTHETIC_SCALE_ANCHOR_FAISS_IDENTITY),
        "faiss_index_type": "IndexFlatIP",
        "similarity_contract": "l2_normalized_inner_product",
        "numpy_version": np.__version__,
        "runtime_identity": {
            "python_implementation": latency.platform.python_implementation(),
            "python_version": latency.platform.python_version(),
            "system": latency.platform.system(),
            "release": latency.platform.release(),
            "machine": latency.platform.machine(),
            "processor": latency.platform.processor(),
        },
        "base_seed": latency.SYNTHETIC_SCALE_SEED,
        "omp_threads": latency.SYNTHETIC_SCALE_OMP_THREADS,
        "omp_scope": "process_global_restored_after_measurement",
        "scenario_count": len(scenarios),
        "scenarios": scenarios,
        "shared_query_vector_across_scenarios": True,
        "maximum_tested_leaf_vector_count": max(
            row["max_leaf_vector_count"] for row in scenarios
        ),
        "exclusive_leaf_limit": latency.SYNTHETIC_SCALE_EXCLUSIVE_LEAF_LIMIT,
        "exclusive_leaf_limit_semantics": (
            "conservative_index_tier_review_boundary_not_authority"
        ),
        "candidate_search_algorithm_reused": True,
        "production_runtime_path_evaluated": False,
        "real_snapshot_multi_scale_curve_evaluated": False,
        "cross_run_stability_evaluated": False,
        "cutoff_tie_worst_case_evaluated": False,
        "solver_quality_evaluated": False,
        "routing_recall_evaluated": False,
        "cell_pruning_evaluated": False,
        "cell_pruning_authorized": False,
        "runtime_authority_granted": False,
        "production_promotion_gate_pass": False,
    }


class _FakeIndexFlatIP:
    search_calls = 0
    requested_counts: list[int] = []

    def __init__(self, dimension: int) -> None:
        self.dimension = dimension
        self.matrix = np.empty((0, dimension), dtype=np.float32)
        self.ntotal = 0

    def add(self, matrix: np.ndarray) -> None:
        assert matrix.shape[1] == self.dimension
        self.matrix = np.ascontiguousarray(matrix, dtype=np.float32).copy()
        self.ntotal = len(self.matrix)

    def search(self, query: np.ndarray, count: int):
        type(self).search_calls += 1
        type(self).requested_counts.append(count)
        scores = np.asarray(query, dtype=np.float32) @ self.matrix.T
        order = np.argsort(-scores[0], kind="stable")[:count]
        return (
            np.ascontiguousarray(scores[:, order], dtype=np.float32),
            np.ascontiguousarray(order.reshape(1, -1), dtype=np.int64),
        )


class _FakeFaiss:
    IndexFlatIP = _FakeIndexFlatIP
    METRIC_INNER_PRODUCT = 0
    __version__ = "1.13.2"
    _candidate_binary_set_sha256 = (
        "sha256:e565b14a3f25198eafad0daf6cd566a2bcdda3327cdbebdf7d3bea4bd6bcbd94"
    )

    def __init__(self, *, threads: int = 7) -> None:
        self.threads = threads
        self.set_calls: list[int] = []

    @staticmethod
    def get_compile_options() -> str:
        return "AVX2"

    @staticmethod
    def write_index(*_args: object) -> None:
        return None

    @staticmethod
    def read_index(*_args: object) -> None:
        return None

    def omp_get_max_threads(self) -> int:
        return self.threads

    def omp_set_num_threads(self, value: int) -> None:
        self.set_calls.append(value)
        self.threads = value


class _EqualToEverything:
    def __eq__(self, _other: object) -> bool:
        return True


class _StringSubclass(str):
    pass


class _DictSubclass(dict):
    pass


class _ListSubclass(list):
    pass


def _row(index: int = 0, **overrides: object) -> dict:
    row = {
        "canonical_solver_id": f"solver_{index}",
        "cell_id": "one",
        "projection_id": "sha256:" + f"{index + 1:x}" * 64,
        "score": 1.0 - index / 10.0,
        "snapshot_id": SNAPSHOT_ID,
        "verification_session_id": SESSION_ID,
        "source_commit_reverified": True,
        "source_reverification_scope": "session_open",
        "source_reverified_during_search_call": False,
        "runtime_authority_granted": False,
        "receipt_authenticity_verified": False,
        "solver_outcome_verified": False,
    }
    row.update(overrides)
    return row


def _synthetic_result(**overrides: object) -> dict:
    result = {
        "canonical_solver_id": "synthetic_energy_00000",
        "cell_id": "energy",
        "projection_id": "sha256:" + "1" * 64,
        "snapshot_id": "synthetic_" + "2" * 64,
        "verification_session_id": None,
        "source_commit_reverified": False,
        "source_reverification_scope": "none",
        "source_reverified_during_search_call": False,
        "receipt_bound": False,
        "receipt_structure_reverified": False,
        "receipt_authenticity_verified": False,
        "solver_outcome_verified": False,
        "runtime_authority_granted": False,
        "score": 0.5,
    }
    result.update(overrides)
    return result


class _Session:
    snapshot_id = SNAPSHOT_ID

    def __init__(self, rows: list[dict] | None = None) -> None:
        self.rows = rows if rows is not None else [_row(index) for index in range(5)]
        self.calls = 0
        self.closed = False

    def search(self, _vector: np.ndarray, *, k: int) -> list[dict]:
        self.calls += 1
        return self.rows[:k]

    def close(self) -> None:
        self.closed = True


def _clock(*durations_ms: float):
    values: list[int] = []
    cursor = 0
    for duration in durations_ms:
        values.extend([cursor, cursor + int(duration * 1_000_000)])
        cursor += int(duration * 1_000_000) + 1_000_000
    iterator = iter(values)
    return lambda: next(iterator)


def _monotonic_clock(step_ns: int = 1_000_000):
    value = -step_ns

    def clock() -> int:
        nonlocal value
        value += step_ns
        return value

    return clock


def test_scale_forecast_derives_the_six_frozen_distribution_plans() -> None:
    manifest = _anchor_manifest()
    plans = [
        latency._derive_forecast_cell_counts(
            manifest,
            multiplier=multiplier,
            distribution=distribution,
        )
        for multiplier in latency.SYNTHETIC_SCALE_MULTIPLIERS
        for distribution in latency.SYNTHETIC_SCALE_DISTRIBUTIONS
    ]

    assert [
        (
            plan["scale_multiplier"],
            plan["distribution"],
            plan["total_vector_count"],
            plan["max_leaf_vector_count"],
            [cell["vector_count"] for cell in plan["cell_counts"]],
        )
        for plan in plans
    ] == [
        (10, "uniform", 220, 28, [28, 28, 28, 28, 27, 27, 27, 27]),
        (
            10,
            "observed_proportional",
            220,
            50,
            [30, 10, 20, 20, 20, 50, 30, 40],
        ),
        (100, "uniform", 2200, 275, [275] * 8),
        (
            100,
            "observed_proportional",
            2200,
            500,
            [300, 100, 200, 200, 200, 500, 300, 400],
        ),
        (1000, "uniform", 22000, 2750, [2750] * 8),
        (
            1000,
            "observed_proportional",
            22000,
            5000,
            [3000, 1000, 2000, 2000, 2000, 5000, 3000, 4000],
        ),
    ]
    assert [plan["raw_float32_vector_payload_bytes"] for plan in plans[::2]] == [
        220 * 768 * 4,
        2200 * 768 * 4,
        22000 * 768 * 4,
    ]


@pytest.mark.parametrize(
    ("mutation", "error"),
    [
        (
            lambda manifest: manifest.__setitem__(
                "snapshot_id", "faisscand_" + "9" * 64
            ),
            "anchor_snapshot_mismatch",
        ),
        (
            lambda manifest: manifest.__setitem__("total_vector_count", 104),
            "anchor_total_mismatch",
        ),
        (
            lambda manifest: manifest.__setitem__("total_vector_count", 22.0),
            "anchor_total_mismatch",
        ),
        (
            lambda manifest: manifest["embedding_contract"].__setitem__(
                "dimension", 384
            ),
            "anchor_dimension_mismatch",
        ),
        (
            lambda manifest: manifest["embedding_contract"].__setitem__(
                "dimension", 768.0
            ),
            "anchor_dimension_mismatch",
        ),
        (
            lambda manifest: manifest["cells"][0].__setitem__("vector_count", True),
            "anchor_cell_binding_mismatch",
        ),
        (
            lambda manifest: manifest["cells"].reverse(),
            "anchor_cell_order_invalid",
        ),
        (
            lambda manifest: manifest["cells"][0].__setitem__(
                "source_projection_commit_id", "proj_" + "9" * 64
            ),
            "anchor_cell_binding_mismatch",
        ),
        (
            lambda manifest: manifest.__setitem__("faiss_version", "1.14.2"),
            "anchor_faiss_version_mismatch",
        ),
        (
            lambda manifest: manifest.__setitem__(
                "faiss_compile_options", "AVX512"
            ),
            "anchor_faiss_compile_options_mismatch",
        ),
        (
            lambda manifest: manifest.__setitem__(
                "faiss_binary_set_sha256", "sha256:" + "9" * 64
            ),
            "anchor_faiss_binary_set_sha256_mismatch",
        ),
    ],
)
def test_scale_forecast_rejects_anchor_drift(
    mutation,
    error: str,
) -> None:
    manifest = _anchor_manifest()
    mutation(manifest)

    with pytest.raises(latency.CandidateLatencyContractError, match=error):
        latency._validate_scale_anchor(manifest)


@pytest.mark.parametrize(
    "forged",
    [_EqualToEverything(), True, None, [], _StringSubclass("1.13.2")],
)
@pytest.mark.parametrize(
    "field",
    [
        "source_projection_commit_id",
        "faiss_version",
        "faiss_compile_options",
        "faiss_binary_set_sha256",
    ],
)
def test_scale_forecast_anchor_requires_exact_strings(
    field: str,
    forged: object,
) -> None:
    manifest = _anchor_manifest()
    if field == "source_projection_commit_id":
        manifest["cells"][0][field] = forged
    else:
        manifest[field] = forged

    with pytest.raises(latency.CandidateLatencyContractError, match="mismatch"):
        latency._validate_scale_anchor(manifest)


@pytest.mark.parametrize("value", [True, False, 1.0, "10", 104])
def test_scale_forecast_rejects_unfrozen_multiplier_values(value: object) -> None:
    with pytest.raises(
        latency.CandidateLatencyContractError,
        match="multiplier_not_in_frozen_contract",
    ):
        latency._derive_forecast_cell_counts(
            _anchor_manifest(),
            multiplier=value,  # type: ignore[arg-type]
            distribution="uniform",
        )


def test_scale_forecast_enforces_the_exclusive_leaf_transition_boundary() -> None:
    latency._require_forecast_leaf_limit({"energy": 9999})

    with pytest.raises(
        latency.CandidateLatencyContractError,
        match="index_tier_transition_required",
    ):
        latency._require_forecast_leaf_limit({"energy": 10_000})


@pytest.mark.parametrize(
    "mutation",
    [
        lambda plan: plan["cell_counts"][0].__setitem__("vector_count", True),
        lambda plan: plan["cell_counts"][0].__setitem__(
            "cell_id", _StringSubclass("energy")
        ),
        lambda plan: plan["cell_counts"].__setitem__(
            0,
            {
                _StringSubclass("cell_id"): "energy",
                "vector_count": 28,
            },
        ),
        lambda plan: plan["cell_counts"].reverse(),
        lambda plan: plan.__setitem__("total_vector_count", 104),
        lambda plan: plan.__setitem__("total_vector_count", 220.0),
        lambda plan: plan.__setitem__("max_leaf_vector_count", 9999),
        lambda plan: plan.__setitem__("max_leaf_vector_count", 28.0),
        lambda plan: plan.__setitem__("raw_float32_vector_payload_bytes", 0),
        lambda plan: plan.__setitem__(
            "raw_float32_vector_payload_bytes", float(220 * 768 * 4)
        ),
        lambda plan: plan.__setitem__("unexpected", True),
    ],
)
def test_scale_forecast_rejects_mutated_scenario_plans(mutation) -> None:
    plan = latency._derive_forecast_cell_counts(
        _anchor_manifest(),
        multiplier=10,
        distribution="uniform",
    )
    mutation(plan)

    with pytest.raises(latency.CandidateLatencyContractError, match="plan"):
        latency._validate_forecast_plan(plan)


@pytest.mark.parametrize(
    "overrides",
    [
        {"snapshot_id": "synthetic_" + "9" * 64},
        {"cell_id": "unknown"},
        {"projection_id": "forged"},
        {"source_commit_reverified": True},
        {"receipt_bound": True},
        {"receipt_structure_reverified": True},
        {"receipt_authenticity_verified": True},
        {"solver_outcome_verified": True},
        {"runtime_authority_granted": True},
        {"score": float("nan")},
    ],
)
def test_synthetic_search_results_reject_evidence_or_authority_forgery(
    overrides: dict,
) -> None:
    expected_snapshot_id = "synthetic_" + "2" * 64

    with pytest.raises(
        latency.CandidateLatencyContractError,
        match="evidence_boundary_invalid",
    ):
        latency._validate_synthetic_search_results(
            [_synthetic_result(**overrides)],
            k=1,
            expected_snapshot_id=expected_snapshot_id,
        )


def test_synthetic_row_reuses_candidate_kernel_and_is_deterministic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(latency, "SYNTHETIC_SCALE_REPETITIONS", 2)
    monkeypatch.setattr(latency, "SYNTHETIC_SCALE_WARMUP_ROUNDS", 1)
    _FakeIndexFlatIP.search_calls = 0
    _FakeIndexFlatIP.requested_counts = []
    plan = latency._derive_forecast_cell_counts(
        _anchor_manifest(),
        multiplier=10,
        distribution="observed_proportional",
    )
    query = latency._synthetic_normalized_matrix(
        1,
        latency.SYNTHETIC_SCALE_DIMENSION,
        seed_material="query",
        label="test_query",
    )[0]
    real_search = latency.candidate_snapshot._search_loaded_candidate
    calls: list[tuple[int, int, str, object]] = []

    def spy(manifest, loaded_cells, query_vector, **kwargs):
        calls.append(
            (
                len(loaded_cells),
                kwargs["k"],
                kwargs["source_reverification_scope"],
                kwargs["verification_session_id"],
            )
        )
        return real_search(manifest, loaded_cells, query_vector, **kwargs)

    monkeypatch.setattr(latency.candidate_snapshot, "_search_loaded_candidate", spy)
    first = latency._measure_synthetic_scale_row(
        plan,
        query,
        faiss_module=_FakeFaiss(),
        clock_ns=_monotonic_clock(),
    )
    second = latency._measure_synthetic_scale_row(
        plan,
        query,
        faiss_module=_FakeFaiss(),
        clock_ns=_monotonic_clock(),
    )

    assert calls == [(8, 5, "none", None)] * 6
    assert _FakeIndexFlatIP.search_calls == 48
    assert _FakeIndexFlatIP.requested_counts == [6] * 48
    assert first["scenario_contract_digest"] == second["scenario_contract_digest"]
    assert first["synthetic_vector_input_sha256"] == second[
        "synthetic_vector_input_sha256"
    ]
    assert first["query_vector_sha256"] == second["query_vector_sha256"]
    assert first["raw_float32_vector_payload_mib"] == 0.644531
    assert first["raw_float32_vector_payload_is_memory_lower_bound_only"] is True
    assert first["peak_process_memory_evaluated"] is False
    assert first["faiss_index_overhead_memory_evaluated"] is False
    assert first["synthetic_input_generation_excluded_from_latency"] is True
    assert first["index_build_excluded_from_latency"] is True
    assert first[
        "candidate_search_call_includes_query_normalization_and_python_merge"
    ] is True
    assert first["search_result_evidence_validation_included_in_latency"] is True
    assert first["latency_ms"]["candidate_all_cell_search"]["p95"] == 1.0
    assert first["latency_reference"]["hard_gate"] is False
    assert first["production_runtime_path_evaluated"] is False
    assert first["cutoff_tie_worst_case_evaluated"] is False
    assert first["solver_quality_evaluated"] is False
    assert first["routing_recall_evaluated"] is False
    assert first["cell_pruning_authorized"] is False
    assert first["runtime_authority_granted"] is False
    assert first["production_promotion_gate_pass"] is False


def test_synthetic_inputs_do_not_depend_on_scenario_execution_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(latency, "SYNTHETIC_SCALE_REPETITIONS", 1)
    monkeypatch.setattr(latency, "SYNTHETIC_SCALE_WARMUP_ROUNDS", 0)
    manifest = _anchor_manifest()
    query = latency._synthetic_normalized_matrix(
        1,
        latency.SYNTHETIC_SCALE_DIMENSION,
        seed_material="shared-query",
        label="test_query",
    )[0]
    uniform = latency._derive_forecast_cell_counts(
        manifest,
        multiplier=10,
        distribution="uniform",
    )
    observed = latency._derive_forecast_cell_counts(
        manifest,
        multiplier=10,
        distribution="observed_proportional",
    )

    forward = [
        latency._measure_synthetic_scale_row(
            plan,
            query,
            faiss_module=_FakeFaiss(),
            clock_ns=_monotonic_clock(),
        )
        for plan in (uniform, observed)
    ]
    reverse = [
        latency._measure_synthetic_scale_row(
            plan,
            query,
            faiss_module=_FakeFaiss(),
            clock_ns=_monotonic_clock(),
        )
        for plan in (observed, uniform)
    ]

    forward_digests = {
        row["distribution"]: (
            row["scenario_contract_digest"],
            row["synthetic_vector_input_sha256"],
            row["query_vector_sha256"],
        )
        for row in forward
    }
    reverse_digests = {
        row["distribution"]: (
            row["scenario_contract_digest"],
            row["synthetic_vector_input_sha256"],
            row["query_vector_sha256"],
        )
        for row in reverse
    }
    assert forward_digests == reverse_digests


def test_scale_forecast_restores_omp_and_preserves_authority_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_faiss = _FakeFaiss(threads=7)
    monkeypatch.setattr(
        latency,
        "_measure_synthetic_scale_row",
        lambda plan, *_args, **_kwargs: {
            **dict(plan),
            "runtime_authority_granted": False,
            "production_promotion_gate_pass": False,
        },
    )

    result = latency._run_synthetic_scale_forecast(
        _anchor_manifest(),
        faiss_module=fake_faiss,
        clock_ns=_monotonic_clock(),
    )

    assert fake_faiss.set_calls == [1, 7]
    assert fake_faiss.threads == 7
    assert result["scenario_count"] == 6
    assert result["maximum_tested_leaf_vector_count"] == 5000
    assert result["faiss_index_type"] == "IndexFlatIP"
    assert result["similarity_contract"] == "l2_normalized_inner_product"
    assert result["production_runtime_path_evaluated"] is False
    assert result["real_snapshot_multi_scale_curve_evaluated"] is False
    assert result["cutoff_tie_worst_case_evaluated"] is False
    assert result["solver_quality_evaluated"] is False
    assert result["routing_recall_evaluated"] is False
    assert result["cell_pruning_authorized"] is False
    assert result["runtime_authority_granted"] is False
    assert result["production_promotion_gate_pass"] is False


def test_generated_scale_forecast_satisfies_closed_world_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _successful_forecast_fixture()
    rows = {
        (row["scale_multiplier"], row["distribution"]): row
        for row in fixture["scenarios"]
    }
    fake_faiss = _FakeFaiss(threads=7)
    monkeypatch.setattr(
        latency,
        "_measure_synthetic_scale_row",
        lambda plan, *_args, **_kwargs: rows[
            (plan["scale_multiplier"], plan["distribution"])
        ],
    )

    result = latency._run_synthetic_scale_forecast(
        _anchor_manifest(),
        faiss_module=fake_faiss,
        clock_ns=_monotonic_clock(),
    )

    assert latency._validate_forecast_authority_boundary(result) is result
    assert fake_faiss.set_calls == [1, 7]


def test_scale_forecast_restores_omp_when_measurement_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_faiss = _FakeFaiss(threads=9)
    monkeypatch.setattr(
        latency,
        "_measure_synthetic_scale_row",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("sentinel")),
    )

    with pytest.raises(RuntimeError, match="sentinel"):
        latency._run_synthetic_scale_forecast(
            _anchor_manifest(),
            faiss_module=fake_faiss,
            clock_ns=_monotonic_clock(),
        )
    assert fake_faiss.set_calls == [1, 9]
    assert fake_faiss.threads == 9


def test_scale_forecast_surfaces_omp_restoration_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _RestoreFailureFaiss(_FakeFaiss):
        def omp_set_num_threads(self, value: int) -> None:
            self.set_calls.append(value)
            if value == 7:
                raise RuntimeError("restore sentinel")
            self.threads = value

    fake_faiss = _RestoreFailureFaiss(threads=7)
    monkeypatch.setattr(
        latency,
        "_measure_synthetic_scale_row",
        lambda plan, *_args, **_kwargs: {
            **dict(plan),
            "runtime_authority_granted": False,
            "production_promotion_gate_pass": False,
        },
    )

    with pytest.raises(
        latency.CandidateLatencyUnavailable,
        match="omp_restoration_failed",
    ) as caught:
        latency._run_synthetic_scale_forecast(
            _anchor_manifest(),
            faiss_module=fake_faiss,
            clock_ns=_monotonic_clock(),
        )
    assert isinstance(caught.value.__cause__, RuntimeError)
    assert fake_faiss.set_calls == [1, 7]


def test_scale_forecast_preserves_primary_error_when_omp_restore_also_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _RestoreFailureFaiss(_FakeFaiss):
        def omp_set_num_threads(self, value: int) -> None:
            self.set_calls.append(value)
            if value == 7:
                raise RuntimeError("restore sentinel")
            self.threads = value

    fake_faiss = _RestoreFailureFaiss(threads=7)
    monkeypatch.setattr(
        latency,
        "_measure_synthetic_scale_row",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            latency.CandidateLatencyContractError("primary sentinel")
        ),
    )

    with pytest.raises(
        latency.CandidateLatencyContractError,
        match="primary sentinel",
    ) as caught:
        latency._run_synthetic_scale_forecast(
            _anchor_manifest(),
            faiss_module=fake_faiss,
            clock_ns=_monotonic_clock(),
        )
    assert any(
        "OMP restoration failed" in note for note in caught.value.__notes__
    )
    assert fake_faiss.set_calls == [1, 7]


def test_scale_forecast_rejects_mismatched_faiss_before_allocation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_faiss = _FakeFaiss()
    fake_faiss.__version__ = "1.14.2"
    called = False

    def forbidden(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("measurement must not start")

    monkeypatch.setattr(latency, "_measure_synthetic_scale_row", forbidden)
    with pytest.raises(
        latency.CandidateLatencyUnavailable,
        match="faiss_build_mismatch",
    ):
        latency._run_synthetic_scale_forecast(
            _anchor_manifest(),
            faiss_module=fake_faiss,
            clock_ns=_monotonic_clock(),
        )
    assert called is False
    assert fake_faiss.set_calls == []


def test_measurement_times_only_search_and_rederives_percentiles() -> None:
    session = _Session()
    result = latency.measure_verified_global_search(
        session,
        np.ones((2, 3), dtype=np.float32),
        expected_snapshot_id=SNAPSHOT_ID,
        k=1,
        repetitions=2,
        warmup_rounds=1,
        clock_ns=_clock(1.0, 2.0, 3.0, 4.0),
    )

    assert session.calls == 6
    assert result["warmup_search_count"] == 2
    assert result["search_execution_count"] == 4
    assert result["verification_session_id"] == SESSION_ID
    assert result["ranking_repeat_check_count"] == 2
    assert result["ranking_repeat_mismatch_count"] == 0
    assert result["latency_ms"]["search"] == {
        "p50": 2.5,
        "p95": 3.85,
        "p99": 3.97,
        "max": 4.0,
        "mean": 2.5,
    }
    assert result["latency_ms"]["per_query_p95"] == {
        "min": 2.9,
        "median": 3.4,
        "max": 3.9,
    }


@pytest.mark.parametrize(
    ("overrides", "error"),
    [
        ({"snapshot_id": "faisscand_" + "c" * 64}, "snapshot_binding_mismatch"),
        ({"verification_session_id": "forged"}, "session_binding_invalid"),
        ({"source_commit_reverified": False}, "evidence_boundary_invalid"),
        ({"runtime_authority_granted": True}, "evidence_boundary_invalid"),
    ],
)
def test_measurement_rejects_invalid_search_evidence(
    overrides: dict,
    error: str,
) -> None:
    with pytest.raises(latency.CandidateLatencyContractError, match=error):
        latency.measure_verified_global_search(
            _Session([_row(**overrides)]),
            np.ones((1, 3), dtype=np.float32),
            expected_snapshot_id=SNAPSHOT_ID,
            k=1,
            repetitions=1,
            warmup_rounds=0,
            clock_ns=_clock(1.0),
        )


def test_measurement_rejects_short_top_k_as_partial_search() -> None:
    with pytest.raises(
        latency.CandidateLatencyContractError,
        match="verified_search_rows_invalid",
    ):
        latency.measure_verified_global_search(
            _Session([_row(index) for index in range(4)]),
            np.ones((1, 3), dtype=np.float32),
            expected_snapshot_id=SNAPSHOT_ID,
            k=5,
            repetitions=1,
            warmup_rounds=0,
            clock_ns=_clock(1.0),
        )


def test_measurement_counts_ranking_change_across_repetitions() -> None:
    class _ChangingSession(_Session):
        def search(self, _vector: np.ndarray, *, k: int) -> list[dict]:
            self.calls += 1
            return [_row(score=1.0 if self.calls == 1 else 0.9)][:k]

    result = latency.measure_verified_global_search(
        _ChangingSession(),
        np.ones((1, 3), dtype=np.float32),
        expected_snapshot_id=SNAPSHOT_ID,
        k=1,
        repetitions=2,
        warmup_rounds=0,
        clock_ns=_clock(1.0, 1.0),
    )

    assert result["ranking_repeat_check_count"] == 1
    assert result["ranking_repeat_mismatch_count"] == 1


def test_quality_scores_labels_only_after_rankings_exist() -> None:
    quality = latency.evaluate_persisted_ranking_quality(
        [
            (
                ("solver_0", "one", "sha256:" + "1" * 64, 0.9),
                ("solver_1", "two", "sha256:" + "2" * 64, 0.8),
            )
        ],
        [
            {
                "query_id": "S01",
                "stratum": "semantic_zero_overlap",
                "query": "label-blind query",
                "expected_solver": "solver_1",
                "expected_cell": "two",
            }
        ],
    )

    assert quality["label_scoring_after_search"] is True
    assert quality["label_scoring_isolation_check_count"] == 1
    assert quality["label_scoring_isolation_enforced"] is True
    assert quality["retriever_input_fields"] == ["query"]
    assert quality["metrics"]["all"]["top1_hits"] == 0
    assert quality["metrics"]["all"]["recall_at_5_hits"] == 1
    assert quality["per_query"][0]["expected_rank_at_5"] == 2


def test_label_blind_query_builder_rejects_adjacent_case_objects() -> None:
    with pytest.raises(
        latency.CandidateLatencyContractError,
        match="label_blind_query_inputs_invalid",
    ):
        latency._build_label_blind_query_inputs(
            [{"query": "text", "expected_solver": "solver_0"}],
            query_prefix="query: ",
        )


def test_numpy_proxy_parity_rederives_exact_rankings_and_scores() -> None:
    cells = [
        {
            "manifest": {"cell_id": "one"},
            "vectors": np.asarray([[1.0, 0.0], [0.0, 1.0]], dtype=np.float32),
            "rows": [
                {
                    "canonical_solver_id": "solver_a",
                    "projection_id": "sha256:" + "1" * 64,
                },
                {
                    "canonical_solver_id": "solver_b",
                    "projection_id": "sha256:" + "2" * 64,
                },
            ],
        }
    ]
    persisted = [
        (
            ("solver_a", "one", "sha256:" + "1" * 64, 1.0),
            ("solver_b", "one", "sha256:" + "2" * 64, 0.0),
        )
    ]

    result = latency.evaluate_numpy_proxy_parity(
        cells,
        np.asarray([[1.0, 0.0]], dtype=np.float32),
        persisted,
        k=2,
        score_tolerance=1.0e-6,
    )

    assert result["ranking_comparison_count"] == 1
    assert result["ranking_mismatch_count"] == 0
    assert result["score_comparison_count"] == 2
    assert result["max_abs_score_error"] == 0.0
    assert result["passed"] is True


def _install_live_fakes(
    monkeypatch: pytest.MonkeyPatch,
    *,
    durations_ms: tuple[float, ...],
) -> _Session:
    profile = retrieval_benchmark.EmbeddingProfile(
        name="test",
        model_id="test:latest",
        model_digest="d" * 64,
        dimension=3,
        document_prefix="doc: ",
        query_prefix="query: ",
    )
    identity = {
        "provider": "ollama",
        "requested_model_tag": profile.model_id,
        "catalog_digest": profile.model_digest,
    }
    request = SimpleNamespace(
        embedding_contract={},
        cells=(("one", "proj_" + "1" * 64), ("two", "proj_" + "2" * 64)),
    )
    manifest = {
        "snapshot_id": SNAPSHOT_ID,
        "topology_digest": "sha256:" + "e" * 64,
        "cells": [{"cell_id": "one"}, {"cell_id": "two"}],
        "total_vector_count": 5,
        "faiss_version": "test-faiss",
        "faiss_compile_options": "test-options",
        "faiss_binary_set_sha256": "sha256:" + "f" * 64,
        "persisted_parity": {"score_tolerance": 1.0e-6},
    }
    session = _Session()
    reopen_session = _Session(
        [
            _row(index, verification_session_id=REOPEN_SESSION_ID)
            for index in range(5)
        ]
    )
    sessions = iter([session, reopen_session])

    class _Embedder:
        def __init__(self, _url: str, *, timeout_seconds: float) -> None:
            assert timeout_seconds == 120.0

        def __enter__(self):
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def verify_profile(self, observed_profile):
            assert observed_profile is profile
            return identity.copy()

        def embed(self, texts, observed_profile, *, label: str):
            assert texts == ["query: first", "query: second"]
            assert observed_profile is profile
            assert label == "candidate_latency_query_embeddings"
            return np.ones((2, 3), dtype=np.float32)

    monkeypatch.setattr(
        latency,
        "_load_query_suite",
        lambda *_args, **_kwargs: {
            "queries": ["first", "second"],
            "cases": [
                {
                    "query_id": "A01",
                    "stratum": "anchored_natural",
                    "query": "first",
                    "expected_solver": "solver_0",
                    "expected_cell": "one",
                },
                {
                    "query_id": "S01",
                    "stratum": "semantic_zero_overlap",
                    "query": "second",
                    "expected_solver": "solver_1",
                    "expected_cell": "one",
                },
            ],
            "documents": [],
            "tokenizer": object(),
            "corpus": {
                "path": "frozen.json",
                "canonical_sha256": "sha256:" + "3" * 64,
                "raw_sha256": "sha256:" + "4" * 64,
                "cases": 2,
                "solver_coverage": 3,
                "topology_digest": "sha256:" + "e" * 64,
                "router_labels_sent_to_search": False,
            },
        },
    )
    monkeypatch.setattr(
        latency.candidate_snapshot,
        "load_candidate_request",
        lambda *_args, **_kwargs: request,
    )
    monkeypatch.setattr(
        latency.candidate_snapshot,
        "load_verified_candidate_snapshot",
        lambda *_args, **_kwargs: {"manifest": manifest, "cells": []},
    )
    monkeypatch.setattr(
        latency.candidate_snapshot,
        "open_verified_candidate_search_session",
        lambda *_args, **_kwargs: next(sessions),
    )
    monkeypatch.setattr(
        latency.candidate_snapshot,
        "_profile_from_contract",
        lambda _contract: profile,
    )
    monkeypatch.setattr(
        latency.retrieval_benchmark,
        "OllamaEmbeddingClient",
        _Embedder,
    )
    monkeypatch.setattr(
        latency.retrieval_benchmark,
        "provider_identity_matches_profile",
        lambda value, observed_profile: value == identity and observed_profile is profile,
    )
    monkeypatch.setattr(
        latency.retrieval_benchmark,
        "run_lexical_benchmark",
        lambda *_args, **_kwargs: {"metrics": {"baseline": True}},
    )

    def _differential_gate(baseline, candidate):
        assert baseline == {"metrics": {"baseline": True}}
        assert candidate["metrics"]["all"]["top1_hits"] == 1
        assert candidate["metrics"]["all"]["recall_at_5_hits"] == 2
        assert candidate["provider_identity_evidence"][
            "catalog_contract_verified_after_embedding"
        ] is True
        return {"passed": True, "criteria": {"persisted_quality": True}}

    monkeypatch.setattr(
        latency.retrieval_benchmark,
        "differential_gate",
        _differential_gate,
    )

    def _proxy_parity(_cells, _vectors, rankings, **_kwargs):
        return {
            "comparison_scope": "test",
            "ranking_comparison_predicate": "exact",
            "ranking_comparison_count": len(rankings),
            "ranking_mismatch_count": 0,
            "score_comparison_count": len(rankings) * latency.DEFAULT_K,
            "score_comparison_predicate": "absolute_error_at_most_tolerance",
            "score_tolerance": 1.0e-6,
            "max_abs_score_error": 0.0,
            "passed": True,
            "_proxy_ranking_identities": rankings,
        }

    monkeypatch.setattr(latency, "evaluate_numpy_proxy_parity", _proxy_parity)
    monkeypatch.setattr(latency.time, "perf_counter_ns", _clock(*durations_ms))
    return session


def test_live_report_proves_dependencies_but_grants_no_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _install_live_fakes(
        monkeypatch,
        durations_ms=(0.25, 0.5) * latency.DEFAULT_REPETITIONS,
    )

    report = latency.run_live_latency_benchmark(
        "request.json",
        "snapshot",
    )

    assert session.closed is True
    assert report["schema_version"] == latency.REPORT_SCHEMA
    assert report["retrieval_evidence_scope"] == latency.LIVE_RETRIEVAL_SCOPE
    assert report["candidate_snapshot_verified"] is True
    assert report["candidate_snapshot_id"] == SNAPSHOT_ID
    assert report["embedding_catalog_verified"] is True
    assert report["global_all_cell_search_verified"] is True
    assert report["measurement"]["search_execution_count"] == 200
    assert report["measurement"]["ranking_repeat_check_count"] == 198
    assert report["measurement"]["ranking_repeat_mismatch_count"] == 0
    assert report["measurement"]["cells_searched_per_operation"] == 2
    assert report["measurement"]["total_cell_searches"] == 400
    assert report["measurement"]["query_embedding_excluded_from_search_latency"] is True
    assert report["embedding_time_excluded_from_latency"] is True
    assert report["session_open_verification_time_excluded_from_latency"] is True
    assert report["end_to_end_query_latency_evaluated"] is False
    assert report["latency_gate"]["passed"] is True
    assert report["persisted_candidate_quality"]["metrics"]["all"] == {
        "cases": 2,
        "top1_hits": 1,
        "top1_accuracy": 0.5,
        "recall_at_5_hits": 2,
        "recall_at_5": 1.0,
        "expected_cell_top1_hits": 2,
        "expected_cell_top1_accuracy": 1.0,
        "nonempty_rate": 1.0,
    }
    assert report["persisted_candidate_quality"][
        "label_scoring_after_search"
    ] is True
    assert report["persisted_candidate_differential_gate"]["passed"] is True
    assert report["ranking_stability"]["within_verified_session"]["passed"] is True
    assert report["ranking_stability"]["snapshot_reopen"] == {
        "search_count": 2,
        "ranking_mismatch_count": 0,
        "verification_session_id": REOPEN_SESSION_ID,
        "previous_session_id_distinct": True,
        "passed": True,
    }
    assert report["numpy_proxy_parity"]["passed"] is True
    assert report["numpy_proxy_parity"]["quality_metrics_equal"] is True
    assert report["retrieval_label_isolation_check_count"] == 2
    assert report["retrieval_label_isolation_enforced"] is True
    assert report["actual_persisted_faiss_quality_evaluated"] is True
    assert report["positive_ranking_quality_evaluated"] is True
    assert report["off_domain_rejection_evaluated"] is False
    assert report["candidate_benchmark_scope"] == (
        "paired_positive_ranking_and_search_latency_only_"
        "no_rejection_calibration"
    )
    assert report["candidate_benchmark_pass"] is True
    assert report["synthetic_scale_forecast_affects_candidate_benchmark_pass"] is False
    assert report[
        "synthetic_scale_forecast_affects_cell_pruning_scale_trigger"
    ] is False
    assert report["synthetic_scale_forecast_affects_latency_gate_decision"] is False
    assert report["synthetic_scale_forecast"]["measurement_complete"] is False
    assert report["synthetic_scale_forecast"]["solver_quality_evaluated"] is False
    assert report["synthetic_scale_forecast"]["routing_recall_evaluated"] is False
    assert report["synthetic_scale_forecast"]["cell_pruning_authorized"] is False
    assert report["synthetic_scale_forecast"]["runtime_authority_granted"] is False
    assert report["synthetic_scale_forecast"][
        "production_promotion_gate_pass"
    ] is False
    assert report["cell_pruning_scale_trigger_crossed"] is False
    assert report["latency_gate_decision"] == (
        "retain_verified_global_all_cells_at_observed_scale"
    )
    assert report["decision_scope"] == (
        "positive_ranking_quality_and_latency_current_snapshot"
    )
    assert report["scale_scope"]["multi_scale_generalization_supported"] is False
    assert report["scale_scope"]["synthetic_candidate_algorithm_curve_attached"] is False
    assert report["scale_scope"]["production_runtime_scale_curve_evaluated"] is False
    assert report["scale_scope"]["remeasure_on_snapshot_change"] is True
    assert report["reproducibility"] == {
        "measurement_contract_frozen": True,
        "independent_run_count": 1,
        "cross_run_stability_evaluated": False,
        "snapshot_reopen_ranking_stability_evaluated": True,
    }
    assert report["cell_pruning_evaluated"] is False
    assert report["cell_pruning_authorized"] is False
    assert report["runtime_authority_granted"] is False
    assert report["production_promotion_gate_pass"] is False


def test_live_entrypoint_freezes_the_latency_gate_contract() -> None:
    parameters = inspect.signature(latency.run_live_latency_benchmark).parameters

    assert "clock_ns" not in parameters
    assert "k" not in parameters
    assert "repetitions" not in parameters
    assert "warmup_rounds" not in parameters
    assert "max_search_p95_ms" not in parameters
    assert latency.DEFAULT_K == 5
    assert latency.DEFAULT_REPETITIONS == 100
    assert latency.DEFAULT_WARMUP_ROUNDS == 1
    assert latency.DEFAULT_MAX_SEARCH_P95_MS == 10.0
    assert list(inspect.signature(latency.run_synthetic_scale_forecast).parameters) == [
        "manifest"
    ]
    assert latency.SYNTHETIC_SCALE_MULTIPLIERS == (10, 100, 1000)
    assert latency.SYNTHETIC_SCALE_DISTRIBUTIONS == (
        "uniform",
        "observed_proportional",
    )
    assert latency.SYNTHETIC_SCALE_DIMENSION == 768
    assert latency.SYNTHETIC_SCALE_K == 5
    assert latency.SYNTHETIC_SCALE_REPETITIONS == 200
    assert latency.SYNTHETIC_SCALE_WARMUP_ROUNDS == 5
    assert latency.SYNTHETIC_SCALE_OMP_THREADS == 1


def test_synthetic_curve_never_changes_current_candidate_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_live_fakes(
        monkeypatch,
        durations_ms=(0.25, 0.5) * latency.DEFAULT_REPETITIONS,
    )
    monkeypatch.setattr(
        latency,
        "run_synthetic_scale_forecast",
        lambda _manifest: _successful_forecast_fixture(p95_ms=999.0),
    )

    report = latency.run_live_latency_benchmark("request.json", "snapshot")

    assert report["synthetic_scale_forecast"]["measurement_complete"] is True
    assert report["synthetic_scale_forecast"]["scenarios"][0][
        "latency_reference"
    ]["target_met"] is False
    assert report["synthetic_scale_forecast_affects_candidate_benchmark_pass"] is False
    assert report[
        "synthetic_scale_forecast_affects_cell_pruning_scale_trigger"
    ] is False
    assert report["synthetic_scale_forecast_affects_latency_gate_decision"] is False
    assert report["candidate_benchmark_pass"] is True
    assert report["cell_pruning_scale_trigger_crossed"] is False
    assert report["cell_pruning_authorized"] is False
    assert report["runtime_authority_granted"] is False
    assert report["production_promotion_gate_pass"] is False


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("runtime_authority_granted",), True),
        (("production_promotion_gate_pass",), True),
        (("cell_pruning_authorized",), True),
        (("production_runtime_path_evaluated",), True),
        (("scenarios", 0, "runtime_authority_granted"), True),
        (("scenarios", 0, "latency_reference", "hard_gate"), True),
        (("metadata", "runtime_authority_granted"), True),
        (("metadata", "shadow_authorization_ready"), True),
        (("metadata", "gate_enabled"), True),
        (("metadata", "runtime_ready"), True),
        (("metadata", "quality_verified"), True),
        (("metadata", "routing_pass"), True),
        (("metadata", "cross_run_verified"), True),
        (("metadata", "decision_status"), "approved"),
        (("metadata", "unexpected_positive_signal"), True),
        (("scenarios", 0, "metadata", "production_gate_pass"), True),
        (("scenarios", 0, "metadata", "routing_recall_pass"), True),
        (("runtime_identity", "status"), "PRODUCTION_PROMOTED"),
        (("metadata", "status"), "AUTHORIZED"),
        (("approved",), "yes"),
        (("promoted",), "yes"),
        (("activated",), 1),
        (("capable",), "yes"),
        (("pruned",), "yes"),
        (("permission_granted",), "yes"),
        (("deployment_status",), "approved"),
        (("release_approved",), "yes"),
        (("access",), ["production_write"]),
        (("execution_allowed",), 1),
        (("candidate_benchmark_pass",), 1),
        (("interrupt_now",), 1),
        (("veto_lifted",), "yes"),
        (("admission_status",), "accepted"),
    ],
)
def test_live_report_rejects_synthetic_authority_forgery(
    monkeypatch: pytest.MonkeyPatch,
    path: tuple[object, ...],
    value: object,
) -> None:
    _install_live_fakes(
        monkeypatch,
        durations_ms=(0.25, 0.5) * latency.DEFAULT_REPETITIONS,
    )
    forecast = _successful_forecast_fixture()
    cursor: object = forecast
    for segment in path[:-1]:
        if type(segment) is int:
            cursor = cursor[segment]  # type: ignore[index]
        else:
            if segment not in cursor:  # type: ignore[operator]
                cursor[segment] = {}  # type: ignore[index]
            cursor = cursor[segment]  # type: ignore[index]
    cursor[path[-1]] = value  # type: ignore[index]
    monkeypatch.setattr(
        latency,
        "run_synthetic_scale_forecast",
        lambda _manifest: forecast,
    )

    with pytest.raises(
        latency.CandidateLatencyContractError,
        match="authority_boundary_invalid",
    ):
        latency.run_live_latency_benchmark("request.json", "snapshot")


def test_forecast_authority_boundary_accepts_exact_success_schema() -> None:
    forecast = _successful_forecast_fixture()

    assert latency._validate_forecast_authority_boundary(forecast) is forecast


def test_forecast_authority_boundary_accepts_informative_unavailable_schema() -> None:
    forecast = latency._unavailable_synthetic_scale_forecast(Exception())

    assert forecast["error"] == "Exception"
    assert latency._validate_forecast_authority_boundary(forecast) is forecast


def test_forecast_authority_boundary_rejects_empty_unavailable_error() -> None:
    forecast = latency._unavailable_synthetic_scale_forecast(Exception("expected"))
    forecast["error"] = ""

    with pytest.raises(
        latency.CandidateLatencyContractError,
        match="authority_boundary_invalid",
    ):
        latency._validate_forecast_authority_boundary(forecast)


@pytest.mark.parametrize(
    "nested",
    [
        ({"runtime_authority_granted": True},),
        _DictSubclass({"benign": False}),
        _ListSubclass([{"benign": False}]),
        object(),
        float("nan"),
        float("inf"),
    ],
)
def test_forecast_authority_boundary_requires_json_native_values(
    nested: object,
) -> None:
    forecast = latency._unavailable_synthetic_scale_forecast(
        latency.CandidateLatencyUnavailable("expected")
    )
    forecast["nested"] = nested

    with pytest.raises(
        latency.CandidateLatencyContractError,
        match="authority_boundary_invalid",
    ):
        latency._validate_forecast_authority_boundary(forecast)


def test_forecast_authority_boundary_rejects_cycles() -> None:
    forecast = latency._unavailable_synthetic_scale_forecast(
        latency.CandidateLatencyUnavailable("expected")
    )
    cycle: list[object] = []
    cycle.append(cycle)
    forecast["nested"] = cycle

    with pytest.raises(
        latency.CandidateLatencyContractError,
        match="authority_boundary_invalid",
    ):
        latency._validate_forecast_authority_boundary(forecast)


def test_live_report_crosses_scale_trigger_without_authorizing_pruning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_live_fakes(
        monkeypatch,
        durations_ms=(12.0, 14.0) * latency.DEFAULT_REPETITIONS,
    )

    report = latency.run_live_latency_benchmark(
        "request.json",
        "snapshot",
    )

    assert report["latency_gate"]["passed"] is False
    assert report["persisted_candidate_differential_gate"]["passed"] is True
    assert report["candidate_benchmark_pass"] is False
    assert report["cell_pruning_scale_trigger_crossed"] is True
    assert report["latency_gate_decision"] == (
        "evaluate_pruning_alternatives_before_scale_increase"
    )
    assert report["cell_pruning_authorized"] is False
    assert report["runtime_authority_granted"] is False


def test_live_report_rejects_invalid_timeout_contract() -> None:
    with pytest.raises(
        latency.CandidateLatencyContractError,
        match="timeout_seconds_must_be_positive_finite",
    ):
        latency.run_live_latency_benchmark(
            "request.json",
            "snapshot",
            timeout_seconds=0.0,
        )


def test_main_fails_closed_without_live_evidence(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        latency,
        "run_live_latency_benchmark",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            latency.CandidateLatencyUnavailable("faiss_missing")
        ),
    )

    assert latency.main(["--request", "r", "--snapshot", "s", "--no-write"]) == 2
    payload = __import__("json").loads(capsys.readouterr().out)
    assert payload["candidate_snapshot_verified"] is False
    assert payload["global_all_cell_search_verified"] is False
    assert payload["actual_persisted_faiss_quality_evaluated"] is False
    assert payload["candidate_benchmark_pass"] is False
    assert payload["synthetic_scale_forecast_affects_candidate_benchmark_pass"] is False
    assert payload[
        "synthetic_scale_forecast_affects_cell_pruning_scale_trigger"
    ] is False
    assert payload["synthetic_scale_forecast_affects_latency_gate_decision"] is False
    assert payload["synthetic_scale_forecast"]["measurement_complete"] is False
    assert payload["synthetic_scale_forecast"]["production_runtime_path_evaluated"] is False
    assert payload["synthetic_scale_forecast"]["cell_pruning_authorized"] is False
    assert payload["synthetic_scale_forecast"]["runtime_authority_granted"] is False
    assert payload["synthetic_scale_forecast"]["production_promotion_gate_pass"] is False
    assert payload["runtime_authority_granted"] is False
    assert payload["production_promotion_gate_pass"] is False


def test_main_returns_one_when_scoped_candidate_benchmark_is_blocked(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        latency,
        "run_live_latency_benchmark",
        lambda *_args, **_kwargs: {
            "schema_version": latency.REPORT_SCHEMA,
            "candidate_benchmark_pass": False,
            "runtime_authority_granted": False,
        },
    )

    assert latency.main(["--request", "r", "--snapshot", "s", "--no-write"]) == 1
    payload = __import__("json").loads(capsys.readouterr().out)
    assert payload["candidate_benchmark_pass"] is False
    assert payload["runtime_authority_granted"] is False

"""Tests for the fail-closed HEX capability probe report.

The properties worth pinning are not "does it run" but "can it lie":

* can a corpus assert coverage it did not earn?
* can an unratified expectation be counted as a pass?
* can a capability that cannot execute be reported as verified?
* does the agent family invent a stricter contract than the tracked R22.2 gate?

Each of those is a way the report could look green while the hive is not
recoverable, so each gets a test.
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(REPO_ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "tools"))

from build_hex_capability_probe_report import (  # noqa: E402
    ENGINE_CAPABILITY_ID,
    EXPECTED_BY_KIND,
    EXPECTED_DISTINCT_TOTAL,
    MACRO_QUALITY_FLOOR,
    PER_CELL_QUALITY_FLOOR,
    ROUTER_CAPABILITY_IDS,
    VERDICT_CANNOT,
    VERDICT_EXECUTES,
    VERDICT_NEEDS_INPUTS,
    ProbeCorpusError,
    build_report,
    discover_capabilities,
    load_corpus,
)

CORPUS_PATH = REPO_ROOT / "tests" / "tools" / "hex_capability_probe_corpus.v1.json"

CLIP_BLOCKED_MODELS = {
    "solver.axiom.indoor_air_quality",
    "solver.axiom.queen_age_replacement",
    "solver.axiom.varroa_treatment_calendar",
    "solver.axiom.winter_feeding_decision",
}


@pytest.fixture(scope="module")
def report() -> dict:
    return build_report(REPO_ROOT, CORPUS_PATH)


def _write_corpus(tmp_path: Path, document: dict) -> Path:
    path = tmp_path / "corpus.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def _base_corpus() -> dict:
    return json.loads(CORPUS_PATH.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------
# capability universe
# --------------------------------------------------------------------------


def test_capability_universe_is_33_distinct_not_59_rows():
    capabilities = discover_capabilities(REPO_ROOT)
    assert len(capabilities) == EXPECTED_DISTINCT_TOTAL == 33
    pairs = {(entry["kind"], entry["capability_id"]) for entry in capabilities}
    assert len(pairs) == 33, "capability universe must be distinct pairs, not genome rows"


def test_capability_universe_kind_split_matches_contract():
    capabilities = discover_capabilities(REPO_ROOT)
    counts: dict[str, int] = {}
    for entry in capabilities:
        counts[entry["kind"]] = counts.get(entry["kind"], 0) + 1
    assert counts == EXPECTED_BY_KIND == {"agent": 7, "router": 3, "solver": 23}


def test_router_and_engine_capability_ids_match_the_bundle_contract():
    ids = {entry["capability_id"] for entry in discover_capabilities(REPO_ROOT)}
    for router_id in ROUTER_CAPABILITY_IDS:
        assert router_id in ids
    assert ENGINE_CAPABILITY_ID in ids


def test_every_distinct_capability_receives_a_verdict(report):
    assert len(report["capabilities"]) == EXPECTED_DISTINCT_TOTAL
    for entry in report["capabilities"]:
        assert entry["verdict"] in {VERDICT_EXECUTES, VERDICT_CANNOT, VERDICT_NEEDS_INPUTS}


# --------------------------------------------------------------------------
# the aggregate must be earned, not declared
# --------------------------------------------------------------------------


def test_current_aggregate_is_not_verified(report):
    assert report["functional_capability_recovery_verified"] is False
    assert "capabilities_cannot_execute" in report["blocking_reasons"]


def test_four_clip_axioms_cannot_execute_with_named_cause(report):
    blocked = {
        entry["capability_id"]: entry
        for entry in report["capabilities"]
        if entry["verdict"] == VERDICT_CANNOT
    }
    assert set(blocked) == CLIP_BLOCKED_MODELS
    for entry in blocked.values():
        assert "Forbidden function" in entry["cause"]


def test_corpus_cannot_assert_the_aggregate(tmp_path):
    """A corpus that simply claims success must not raise the verdict."""
    document = _base_corpus()
    document["functional_capability_recovery_verified"] = True
    document["distinct_capability_contract"]["total"] = 1
    document["capabilities_without_verified_oracle"] = []
    built = build_report(REPO_ROOT, _write_corpus(tmp_path, document))
    assert built["functional_capability_recovery_verified"] is False
    assert built["distinct_capability_total"] == EXPECTED_DISTINCT_TOTAL


def test_unratified_expectations_never_count_as_passes(report):
    """The known divergences must be surfaced, not absorbed."""
    assert report["declared_divergence_total"] >= 1
    diverging = {
        entry["capability_id"]
        for entry in report["capabilities"]
        if entry["declared_divergences"]
    }
    assert "solver.axiom.hive_thermal_balance" in diverging
    assert (
        "declared_divergences_against_unratified_expectations"
        in report["blocking_reasons"]
    )
    for entry in report["capabilities"]:
        for divergence in entry["declared_divergences"]:
            assert divergence["oracle_basis"] not in {
                "derived_from_declared_formulas",
                "operator_adjudicated",
            }


def test_axiom_capabilities_have_no_verified_oracle_yet(report):
    """Honest current state: executability is not value-correctness."""
    unverified = set(report["capabilities_without_verified_oracle"])
    assert "solver.axiom.honey_yield" in unverified
    assert "capabilities_without_verified_oracle" in report["blocking_reasons"]


def test_requires_caller_inputs_is_reachable(tmp_path):
    """The third verdict state must be more than vocabulary."""
    document = _base_corpus()
    document["solver_axiom_cases"] = [
        case
        for case in document["solver_axiom_cases"]
        if not (
            case["model_id"] == "mtbf_prediction"
            and case["case_id"] != "mtbf_prediction.exec.no_inputs"
        )
    ]
    built = build_report(REPO_ROOT, _write_corpus(tmp_path, document))
    entry = next(
        item
        for item in built["capabilities"]
        if item["capability_id"] == "solver.axiom.mtbf_prediction"
    )
    assert entry["verdict"] == VERDICT_NEEDS_INPUTS
    assert entry["cause"] == "declared_variables_without_defaults"
    assert "capabilities_require_caller_inputs" in built["blocking_reasons"]


def test_capability_without_any_corpus_case_fails_closed(tmp_path):
    document = _base_corpus()
    document["solver_axiom_cases"] = [
        case
        for case in document["solver_axiom_cases"]
        if case["model_id"] != "honey_yield"
    ]
    built = build_report(REPO_ROOT, _write_corpus(tmp_path, document))
    entry = next(
        item
        for item in built["capabilities"]
        if item["capability_id"] == "solver.axiom.honey_yield"
    )
    assert entry["verdict"] == VERDICT_CANNOT
    assert entry["cause"] == "no_corpus_case_for_capability"
    assert built["functional_capability_recovery_verified"] is False


# --------------------------------------------------------------------------
# the agent family must mirror the tracked gate, not out-strict it
# --------------------------------------------------------------------------


def test_agent_family_does_not_count_positive_misses_as_failures(report):
    """The tracked R22.2 gate accepts partial positive matching.

    Paraphrased and Finnish utterances intentionally avoid the selector
    keywords and are documented headroom.  Treating them as failures would
    report a passing, gated system as broken.
    """
    agents = [entry for entry in report["capabilities"] if entry["kind"] == "agent"]
    assert len(agents) == 7
    assert any(
        entry["routing_measurement"]["positives_matched"]
        < entry["routing_measurement"]["positives_total"]
        for entry in agents
    ), "fixture drift: expected at least one cell with documented positive headroom"
    assert all(entry["assertions_failed"] == 0 for entry in agents)
    assert all(entry["verdict"] == VERDICT_EXECUTES for entry in agents)


def test_agent_family_enforces_the_tracked_thresholds(report):
    agents = [entry for entry in report["capabilities"] if entry["kind"] == "agent"]
    for entry in agents:
        measurement = entry["routing_measurement"]
        assert measurement["negatives_correct"] == measurement["negatives_total"]
        assert measurement["cell_quality"] >= PER_CELL_QUALITY_FLOOR
        assert measurement["macro_quality"] >= MACRO_QUALITY_FLOOR
        assert measurement["per_cell_floor"] == PER_CELL_QUALITY_FLOOR
        assert measurement["macro_floor"] == MACRO_QUALITY_FLOOR


def test_agent_macro_quality_is_recomputed_not_read_from_an_artifact(report):
    """Recomputing must agree with the recorded axis-B evidence."""
    agents = [entry for entry in report["capabilities"] if entry["kind"] == "agent"]
    qualities = {entry["routing_measurement"]["macro_quality"] for entry in agents}
    assert qualities == {0.7476}


# --------------------------------------------------------------------------
# corpus is fail-closed
# --------------------------------------------------------------------------


def test_corpus_loads(report):
    document = load_corpus(CORPUS_PATH)
    assert document["corpus_version"] == "hex_capability_probe_corpus.v1"


@pytest.mark.parametrize(
    "mutate,expected",
    [
        (lambda d: d.update({"corpus_version": "other"}), "corpus_version_mismatch"),
        (lambda d: d.update({"solver_axiom_cases": []}), "corpus_cases_missing"),
    ],
)
def test_corpus_shape_failures(tmp_path, mutate, expected):
    document = _base_corpus()
    mutate(document)
    with pytest.raises(ProbeCorpusError) as caught:
        load_corpus(_write_corpus(tmp_path, document))
    assert str(caught.value) == expected


def test_corpus_rejects_duplicate_case_id(tmp_path):
    document = _base_corpus()
    document["solver_axiom_cases"].append(copy.deepcopy(document["solver_axiom_cases"][0]))
    with pytest.raises(ProbeCorpusError) as caught:
        load_corpus(_write_corpus(tmp_path, document))
    assert str(caught.value) == "corpus_duplicate_case_id"


def test_corpus_rejects_unknown_oracle_basis(tmp_path):
    document = _base_corpus()
    document["solver_axiom_cases"][0]["oracle_basis"] = "trust_me"
    with pytest.raises(ProbeCorpusError) as caught:
        load_corpus(_write_corpus(tmp_path, document))
    assert str(caught.value) == "corpus_case_oracle_basis_unknown"


def test_corpus_rejects_executability_case_carrying_an_expectation(tmp_path):
    """An executability-only case must not smuggle in a value oracle."""
    document = _base_corpus()
    case = next(
        item
        for item in document["solver_axiom_cases"]
        if item["oracle_basis"] == "executability_only"
    )
    case["expected_value"] = 1.0
    with pytest.raises(ProbeCorpusError) as caught:
        load_corpus(_write_corpus(tmp_path, document))
    assert str(caught.value) == "corpus_case_executability_must_not_declare_expectation"


def test_corpus_rejects_non_finite_input(tmp_path):
    document = _base_corpus()
    document["solver_axiom_cases"][0]["inputs"]["injected"] = "not_a_number"
    with pytest.raises(ProbeCorpusError) as caught:
        load_corpus(_write_corpus(tmp_path, document))
    assert str(caught.value) == "corpus_case_input_not_finite_number"


def test_corpus_rejects_nan_literal(tmp_path):
    path = tmp_path / "corpus.json"
    document = _base_corpus()
    text = json.dumps(document)
    text = text.replace('"tolerance_ratio": 0.01', '"tolerance_ratio": NaN', 1)
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ProbeCorpusError):
        load_corpus(path)


def test_corpus_rejects_duplicate_json_keys(tmp_path):
    path = tmp_path / "corpus.json"
    path.write_text(
        '{"corpus_version": "hex_capability_probe_corpus.v1",'
        ' "corpus_version": "hex_capability_probe_corpus.v1"}',
        encoding="utf-8",
    )
    with pytest.raises(ProbeCorpusError) as caught:
        load_corpus(path)
    assert str(caught.value) == "corpus_duplicate_key"


def test_corpus_missing_file_fails_closed(tmp_path):
    with pytest.raises(ProbeCorpusError) as caught:
        load_corpus(tmp_path / "absent.json")
    assert str(caught.value) == "corpus_missing"


# --------------------------------------------------------------------------
# the report must claim no authority and must be reproducible
# --------------------------------------------------------------------------


def test_report_claims_no_authority(report):
    assert report["measurement_only"] is True
    assert report["runtime_started"] is False
    assert report["claim_safe_upgrade"] is False
    assert report["production_ready_claim"] is False
    assert report["recovery_ready_claim"] is False


def test_report_is_reproducible():
    first = build_report(REPO_ROOT, CORPUS_PATH)
    second = build_report(REPO_ROOT, CORPUS_PATH)
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


def test_report_records_the_row_versus_distinct_distinction(report):
    assert "59" in report["genome_capability_rows_note"]
    assert "33" in report["genome_capability_rows_note"]

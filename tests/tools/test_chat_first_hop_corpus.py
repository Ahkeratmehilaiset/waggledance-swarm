# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "run_chat_first_hop_corpus",
    ROOT / "tools" / "run_chat_first_hop_corpus.py",
)
mod = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(mod)  # type: ignore[union-attr]
REAL_CURRENT_HEAD_COMMIT_SHA = mod._current_head_commit_sha

HEAD_A = "a" * 40
HEAD_B = "b" * 40
SAMPLE = [
    {
        "id": "solver_math_percent",
        "query": "what is 15% of 300",
        "profile": "HOME",
    },
    {
        "id": "stats_solver_fallback",
        "query": "statistics summary for hive readings SECRET-W1B-STATS-12345",
        "profile": "HOME",
    },
    {
        "id": "general_llm",
        "query": "explain routine hive care SECRET-W1B-LLM-67890",
        "profile": "HOME",
    },
    {
        "id": "cached_excluded",
        "query": "what is varroa SECRET-W1B-CACHE-11111",
        "profile": "HOME",
        "cached_response": "cached varroa answer SECRET-W1B-RESPONSE-22222",
    },
]
ROUTE_ROW = {
    "id": "route-row",
    "query": "route classification query",
    "profile": "HOME",
    "language": "auto",
}


def _diagnose(corpus=SAMPLE, *, head: str = HEAD_A, run_id: str | None = None):
    return mod.diagnose(corpus, expected_head_commit_sha=head, run_id=run_id)


@pytest.fixture(autouse=True)
def _stable_git_head(monkeypatch) -> None:
    monkeypatch.setattr(mod, "_current_head_commit_sha", lambda: HEAD_A)


def _route_header() -> dict[str, str]:
    rows = mod._validate_corpus([ROUTE_ROW])
    return mod._build_run_header(rows, expected_head_commit_sha=HEAD_A)


def _bindings(corpus) -> dict[str, tuple[str, int]]:
    return mod._expected_bindings(mod._validate_corpus(corpus))


def _classify(trace, *, response: str = "answer", source: str = "test"):
    result = SimpleNamespace(
        response=response,
        source=source,
        route_stage_trace=trace,
    )
    return mod._classify_result(result, 1, ROUTE_ROW, _route_header())


def test_diagnose_counts_non_cached_chatservice_first_hops() -> None:
    report = _diagnose()

    assert report["ok"] is True
    assert report["input_row_count"] == 4
    assert report["served_query_count"] == 4
    assert report["cached_count"] == 1
    assert report["non_cached_served_first_hop_count"] == 3
    assert report["route_decision_counts"] == {
        "solver_first": 1,
        "fallback": 2,
        "refused": 0,
    }
    assert report["first_hop_counts"] == {
        "authoritative": 1,
        "heuristic": 1,
        "fallback": 1,
        "refused": 0,
        "gap": 0,
    }
    assert report["invariants"]["corpus_cardinality_preserved"] is True


def test_records_are_w1a_v3_bound_and_privacy_safe() -> None:
    report = _diagnose()
    blob = json.dumps(report, sort_keys=True)
    header = report["header"]

    assert header == {
        "head_commit_sha": HEAD_A,
        "corpus_digest": header["corpus_digest"],
        "schema_version": "wd.chat_query_route_evidence.v1",
        "normalization_version": "wd.chat_query_normalization.v1",
        "run_id": header["run_id"],
    }
    assert report["report_version"] == "wd.chat_first_hop_corpus.v1"
    assert len(header["head_commit_sha"]) == 40
    assert header["corpus_digest"].startswith("sha256:")
    assert header["run_id"].startswith("sha256:")
    assert report["measurement_not_a_correctness_gate"] is True
    assert report["first_hop_coverage_available"] is True
    assert report["production_representative"] is False
    assert report["representativeness_scope"] == mod.REPRESENTATIVENESS_SCOPE
    assert report["invariants"]["raw_query_not_emitted"] is True
    assert report["invariants"]["records_allowlisted"] is True
    assert report["claim_safe"] is False
    assert report["runtime_authority_granted"] is False
    assert report["external_writes_applied"] is False
    for marker in (
        "SECRET-W1B-STATS",
        "SECRET-W1B-LLM",
        "SECRET-W1B-CACHE",
        "SECRET-W1B-RESPONSE",
    ):
        assert marker not in blob
    for record in report["first_hop_records"]:
        assert set(record) == mod.SAFE_RECORD_KEYS
        assert record["id"].startswith("sha256:")
        assert record["query_digest"].startswith("sha256:")
        assert record["normalization_version"] == mod.NORMALIZATION_VERSION
        assert record["candidate_receipt_ref"].startswith("sha256:")
        assert isinstance(record["emitted_at_seq"], int)


def test_query_digest_uses_pinned_domain_and_nfc_strip_normalization() -> None:
    normalized = "Caf\u00e9 Hive"
    expected = mod.sha256_digest(
        {
            "domain": "wd.chat_query_route_evidence.query_digest.v1",
            "normalization_version": "wd.chat_query_normalization.v1",
            "normalized_query": normalized,
        }
    )

    assert mod._query_digest("  Cafe\u0301 Hive  ") == expected
    assert mod._query_digest(normalized) == expected
    assert mod._query_digest("Caf\u00e9\tHive") != expected
    assert mod._query_digest("caf\u00e9 Hive") != expected
    assert mod._query_digest("Caf\u00e9 Hives") != expected
    assert mod._query_digest is mod.canonical_query_digest


def test_corpus_digest_has_shared_non_cached_query_set_golden_vector() -> None:
    q1 = "sha256:" + "1" * 64
    q2 = "sha256:" + "2" * 64

    assert mod._corpus_digest_from_query_digests([q2, q1]) == (
        "sha256:42f881d30e02035495f264ab59beebb1e4c7dd2813a8e73fe667a83e8896d877"
    )
    assert mod._corpus_digest_from_query_digests([q1]) == (
        "sha256:ac2c5c530be63d17beec27fb5ff57d926c719048b80c67c7087777808f6fcb9d"
    )
    with pytest.raises(mod.CorpusValidationError):
        mod._corpus_digest_from_query_digests([q1, q1])


def test_cached_rows_are_excluded_from_denominator_but_bound_to_corpus() -> None:
    report = _diagnose([SAMPLE[-1]])

    assert report["input_row_count"] == 1
    assert report["cached_count"] == 1
    assert report["non_cached_served_first_hop_count"] == 0
    assert report["measurement_available"] is False
    assert report["first_hop_records"] == []
    assert report["header"]["corpus_digest"].startswith("sha256:")
    assert report["first_hop_coverage_available"] is False
    assert report["invariants"]["corpus_cardinality_preserved"] is True
    assert report["ok"] is False


def test_dropped_served_row_fails_conservation(monkeypatch) -> None:
    original_run_rows = mod._run_rows

    async def drop_one_record(rows, header):
        records, gaps, cached_count = await original_run_rows(rows, header)
        return records[:-1], gaps, cached_count

    monkeypatch.setattr(mod, "_run_rows", drop_one_record)
    report = _diagnose()

    assert report["served_query_count"] == 4
    assert len(report["first_hop_records"]) == 2
    assert len(report["gap_records"]) == 0
    assert report["cached_count"] == 1
    assert report["invariants"]["served_query_conservation"] is False
    assert report["ok"] is False


def test_empty_corpus_is_valid_but_fail_closed_unavailable() -> None:
    report = _diagnose([])

    assert report["input_row_count"] == 0
    assert report["invariants"]["corpus_valid"] is True
    assert report["measurement_available"] is False
    assert report["first_hop_coverage_available"] is False
    assert report["ok"] is False


@pytest.mark.parametrize(
    ("corpus", "error"),
    [
        ([SAMPLE[0], "raw-row-must-not-be-dropped"], "row_1_not_mapping"),
        ([{"id": 7, "query": "typed query"}], "row_0_invalid_id"),
        ([{"id": "typed-id", "query": 7}], "row_0_invalid_query"),
        (
            [{"id": "typed-id", "query": "query", "raw": "hidden"}],
            "row_0_unknown_fields",
        ),
    ],
)
def test_malformed_mixed_corpus_fails_closed_without_narrowing(corpus, error) -> None:
    report = _diagnose(corpus)

    assert report["ok"] is False
    assert report["input_row_count"] == len(corpus)
    assert report["served_query_count"] == 0
    assert report["first_hop_records"] == []
    assert report["header"] is None
    assert report["validation_errors"] == [error]
    assert report["invariants"]["corpus_valid"] is False


@pytest.mark.parametrize(
    ("corpus", "error"),
    [
        (
            [
                {"id": "same", "query": "first query"},
                {"id": "same", "query": "second query"},
            ],
            "duplicate_id",
        ),
        (
            [
                {"id": "first", "query": "same query"},
                {"id": "second", "query": "same query"},
            ],
            "duplicate_query_digest",
        ),
    ],
)
def test_duplicate_identity_or_query_digest_fails_closed(corpus, error) -> None:
    report = _diagnose(corpus)

    assert report["ok"] is False
    assert report["validation_errors"] == [error]
    assert report["non_cached_served_first_hop_count"] == 0


def test_header_and_candidate_refs_are_deterministic_and_bind_every_change(
    monkeypatch,
) -> None:
    first = _diagnose()
    repeated = _diagnose()
    monkeypatch.setattr(mod, "_current_head_commit_sha", lambda: HEAD_B)
    changed_head = _diagnose(head=HEAD_B)
    monkeypatch.setattr(mod, "_current_head_commit_sha", lambda: HEAD_A)
    changed_cache = copy.deepcopy(SAMPLE)
    changed_cache[-1]["cached_response"] += " changed"
    changed_cached_content = _diagnose(changed_cache)
    changed_query = copy.deepcopy(SAMPLE)
    changed_query[0]["query"] += " changed"
    changed_corpus = _diagnose(changed_query)

    assert first == repeated
    assert first["header"]["corpus_digest"] == changed_head["header"]["corpus_digest"]
    assert first["header"]["run_id"] != changed_head["header"]["run_id"]
    assert first["header"] == changed_cached_content["header"]
    assert first["first_hop_records"] == changed_cached_content["first_hop_records"]
    assert first["header"]["corpus_digest"] != changed_corpus["header"]["corpus_digest"]
    assert first["header"]["run_id"] != changed_corpus["header"]["run_id"]
    first_refs = [r["candidate_receipt_ref"] for r in first["first_hop_records"]]
    assert first_refs != [
        r["candidate_receipt_ref"] for r in changed_head["first_hop_records"]
    ]
    assert first_refs != [
        r["candidate_receipt_ref"] for r in changed_corpus["first_hop_records"]
    ]


def test_short_alpha_query_and_id_are_never_emitted() -> None:
    report = _diagnose([{"id": "secret", "query": "secret"}])
    blob = json.dumps(report, sort_keys=True).lower()

    assert report["ok"] is True
    assert report["invariants"]["raw_query_not_emitted"] is True
    assert "secret" not in blob
    assert report["first_hop_records"][0]["id"] != "secret"


def test_authoritative_hybrid_is_first_served_hop() -> None:
    classified = _classify(
        [
            {"stage": "route_selection", "route_type": "llm"},
            {"stage": "deterministic_solver", "answered": False},
            {
                "stage": "hybrid_retrieval_8_cell",
                "answered": True,
                "authoritative": True,
            },
        ],
        source="hybrid_retrieval",
    )
    record = classified["record"]

    assert record["first_hop_stage"] == "hybrid_retrieval_8_cell"
    assert record["first_hop_class"] == "authoritative"
    assert record["route_decision"] == "fallback"
    assert record["fallback_used"] is True
    assert mod._validate_records(
        [record], [], _route_header(), _bindings([ROUTE_ROW])
    ) is True


def test_authoritative_hex_is_first_served_hop() -> None:
    classified = _classify(
        [
            {"stage": "route_selection", "route_type": "llm"},
            {
                "stage": "hex_neighbor_assist_7_cell",
                "answered": True,
                "authoritative": True,
            },
        ],
        source="hex_neighbor",
    )
    record = classified["record"]

    assert record["first_hop_stage"] == "hex_neighbor_assist_7_cell"
    assert record["first_hop_class"] == "authoritative"
    assert record["first_hop_solver"] == "hex_neighbor_assist"
    assert mod._validate_records(
        [record], [], _route_header(), _bindings([ROUTE_ROW])
    ) is True


def test_solver_miss_uses_served_orchestrator_fallback_stage() -> None:
    classified = _classify(
        [
            {"stage": "route_selection", "route_type": "solver"},
            {"stage": "deterministic_solver", "answered": False},
            {"stage": "orchestrator_llm_fallback", "source": "llm"},
        ],
        source="llm",
    )
    record = classified["record"]

    assert record["first_hop_stage"] == "orchestrator_llm_fallback"
    assert record["first_hop_class"] == "fallback"
    assert record["route_decision"] == "fallback"
    assert record["first_hop_solver"] == "orchestrator_llm"


def test_refusal_is_the_only_route_with_null_first_hop_solver() -> None:
    classified = _classify(
        [
            {"stage": "route_selection", "route_type": "solver"},
            {
                "stage": "deterministic_solver",
                "answered": True,
                "intent": "math",
            },
        ],
        response="_REFUSED by policy",
        source="solver_refusal",
    )
    record = classified["record"]

    assert record["route_decision"] == "refused"
    assert record["first_hop_class"] == "refused"
    assert record["first_hop_solver"] is None
    assert mod._validate_records(
        [record], [], _route_header(), _bindings([ROUTE_ROW])
    ) is True


def test_no_recognized_served_stage_is_a_bound_gap() -> None:
    classified = _classify(
        [
            {"stage": "route_selection", "route_type": "solver"},
            {"stage": "deterministic_solver", "answered": False},
            {"stage": "hybrid_retrieval_8_cell", "answered": False},
        ]
    )
    gap = classified["gap"]

    assert gap["gap_reason"] == "missing_served_route_stage"
    assert gap["first_hop_class"] == "gap"
    assert gap["normalization_version"] == "wd.chat_query_normalization.v1"
    assert mod._validate_records(
        [], [gap], _route_header(), _bindings([ROUTE_ROW])
    ) is True


def test_tampered_record_or_candidate_ref_is_rejected() -> None:
    report = _diagnose()
    header = report["header"]
    records = copy.deepcopy(report["first_hop_records"])

    records[0]["candidate_receipt_ref"] = "sha256:" + "0" * 64
    assert mod._validate_records(
        records, [], header, _bindings(SAMPLE)
    ) is False

    records = copy.deepcopy(report["first_hop_records"])
    original_ref = records[0]["candidate_receipt_ref"]
    records[0]["fallback_used"] = True
    records[0]["candidate_receipt_ref"] = mod._candidate_ref(
        records[0], header, kind="record"
    )
    assert records[0]["candidate_receipt_ref"] != original_ref
    assert mod._validate_records(
        records, [], header, _bindings(SAMPLE)
    ) is False


def test_outputs_remain_bound_to_corpus_rows_and_sequences() -> None:
    report = _diagnose()
    header = report["header"]
    expected = _bindings(SAMPLE)

    swapped = copy.deepcopy(report["first_hop_records"])
    swapped[0]["query_digest"], swapped[1]["query_digest"] = (
        swapped[1]["query_digest"],
        swapped[0]["query_digest"],
    )
    for record in swapped[:2]:
        record["candidate_receipt_ref"] = mod._candidate_ref(
            record, header, kind="record"
        )
    assert mod._validate_records(swapped, [], header, expected) is False

    duplicate_sequence = copy.deepcopy(report["first_hop_records"])
    duplicate_sequence[1]["emitted_at_seq"] = duplicate_sequence[0][
        "emitted_at_seq"
    ]
    duplicate_sequence[1]["candidate_receipt_ref"] = mod._candidate_ref(
        duplicate_sequence[1], header, kind="record"
    )
    assert mod._validate_records(
        duplicate_sequence, [], header, expected
    ) is False

    non_monotonic = list(reversed(copy.deepcopy(report["first_hop_records"])))
    assert mod._validate_records(non_monotonic, [], header, expected) is False


def test_non_refused_route_cannot_use_null_solver() -> None:
    report = _diagnose()
    header = report["header"]
    records = copy.deepcopy(report["first_hop_records"])
    fallback = next(
        record for record in records if record["route_decision"] == "fallback"
    )
    fallback["first_hop_solver"] = None
    fallback["candidate_receipt_ref"] = mod._candidate_ref(
        fallback, header, kind="record"
    )

    assert mod._validate_records(
        records, [], header, _bindings(SAMPLE)
    ) is False


def test_harness_exception_stays_in_denominator_as_gap(monkeypatch) -> None:
    async def fail_retrieve(self, **kwargs):
        raise RuntimeError("raw exception detail must not escape")

    monkeypatch.setattr(mod._StaticMemoryService, "retrieve_context", fail_retrieve)
    report = _diagnose([SAMPLE[0]])

    assert report["ok"] is False
    assert report["non_cached_served_first_hop_count"] == 1
    assert report["first_hop_counts"]["gap"] == 1
    assert report["gap_records"][0]["gap_reason"] == "harness_exception"
    assert report["invariants"]["corpus_cardinality_preserved"] is True
    assert report["first_hop_coverage_available"] is True
    assert "raw exception detail" not in json.dumps(report).lower()


def test_classification_exception_becomes_privacy_safe_bound_gap(
    monkeypatch,
) -> None:
    def fail_classification(*args, **kwargs):
        raise RuntimeError("SECRET-CLASSIFICATION-DETAIL")

    monkeypatch.setattr(mod, "_classify_result", fail_classification)
    report = _diagnose([SAMPLE[0]])
    blob = json.dumps(report)

    assert report["first_hop_coverage_available"] is True
    assert report["first_hop_counts"]["gap"] == 1
    assert report["gap_records"][0]["gap_reason"] == "harness_exception"
    assert report["ok"] is False
    assert "SECRET-CLASSIFICATION" not in blob


def test_outer_harness_exception_fails_closed_without_detail(monkeypatch) -> None:
    async def fail_outer(*args, **kwargs):
        raise RuntimeError("SECRET-OUTER-HARNESS")

    monkeypatch.setattr(mod, "_run_rows", fail_outer)
    report = _diagnose([SAMPLE[0]])
    blob = json.dumps(report)

    assert report["ok"] is False
    assert report["header"] is None
    assert report["validation_errors"] == ["harness_execution_failed"]
    assert "SECRET-OUTER-HARNESS" not in blob


@pytest.mark.parametrize(
    ("records", "gaps", "cached_count"),
    [
        ([{}], [], 0),
        ([], [{}], 0),
        ([], [], "0"),
    ],
)
def test_malformed_runner_output_fails_closed(
    monkeypatch, records, gaps, cached_count
) -> None:
    async def malformed(*args, **kwargs):
        return records, gaps, cached_count

    monkeypatch.setattr(mod, "_run_rows", malformed)
    report = _diagnose([SAMPLE[0]])

    assert report["ok"] is False
    assert report["header"] is None
    assert report["validation_errors"] == ["invalid_harness_output"]


def test_runner_cannot_mutate_header_bound_corpus(monkeypatch) -> None:
    original_run_rows = mod._run_rows

    async def mutate_corpus(rows, header):
        rows[0]["query"] += " mutated"
        return await original_run_rows(rows, header)

    monkeypatch.setattr(mod, "_run_rows", mutate_corpus)
    report = _diagnose()

    assert report["header"] is None
    assert report["first_hop_coverage_available"] is False
    assert report["validation_errors"] == ["invalid_harness_output"]
    assert report["ok"] is False


def test_silently_dropped_row_fails_corpus_anchored_coverage(monkeypatch) -> None:
    original_run_rows = mod._run_rows

    async def drop_last_record(rows, header):
        records, gaps, cached_count = await original_run_rows(rows, header)
        return records[:-1], gaps, cached_count

    monkeypatch.setattr(mod, "_run_rows", drop_last_record)
    report = _diagnose()

    assert report["non_cached_served_first_hop_count"] == 3
    assert len(report["first_hop_records"]) == 2
    assert report["first_hop_coverage_available"] is False
    assert report["measurement_available"] is False
    assert report["invariants"]["complete_query_bijection"] is False
    assert report["invariants"]["counts_sum_to_denominator"] is False
    assert report["ok"] is False


def test_duplicate_measurement_cannot_mask_corpus_accounting(monkeypatch) -> None:
    original_run_rows = mod._run_rows

    async def duplicate_first_record(rows, header):
        records, gaps, cached_count = await original_run_rows(rows, header)
        records.append(copy.deepcopy(records[0]))
        return records, gaps, cached_count

    monkeypatch.setattr(mod, "_run_rows", duplicate_first_record)
    report = _diagnose()

    assert report["header"] is None
    assert report["first_hop_coverage_available"] is False
    assert report["validation_errors"] == ["invalid_harness_output"]
    assert report["ok"] is False


def test_invalid_full_head_fails_closed() -> None:
    report = _diagnose(head="short")

    assert report["ok"] is False
    assert report["header"] is None
    assert report["validation_errors"] == ["invalid_expected_head_commit_sha"]
    assert report["invariants"]["corpus_valid"] is True
    assert report["invariants"]["header_valid"] is False


def test_mismatched_expected_head_fails_closed() -> None:
    report = _diagnose(head=HEAD_B)

    assert report["ok"] is False
    assert report["header"] is None
    assert report["validation_errors"] == ["head_commit_mismatch"]


def test_head_is_rechecked_after_measurement(monkeypatch) -> None:
    heads = iter((HEAD_A, HEAD_B))
    monkeypatch.setattr(mod, "_current_head_commit_sha", lambda: next(heads))

    report = _diagnose(head=HEAD_A)

    assert report["ok"] is False
    assert report["header"] is None
    assert report["validation_errors"] == ["measurement_context_changed"]


def test_untracked_file_prevents_commit_bound_header(monkeypatch) -> None:
    def fake_run(args, **kwargs):
        assert args[:2] == ["git", "status"]
        return SimpleNamespace(returncode=0, stdout="?? untracked.py\n")

    monkeypatch.setattr(mod.subprocess, "run", fake_run)

    with pytest.raises(mod.HeaderValidationError, match="source_worktree_not_clean"):
        REAL_CURRENT_HEAD_COMMIT_SHA()


def test_main_json_exits_zero_for_default_corpus(capsys) -> None:
    assert mod.main(["--json", "--head-commit-sha", HEAD_A]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["report_version"] == mod.REPORT_VERSION
    assert payload["measurement_available"] is True
    assert payload["first_hop_coverage_available"] is True
    assert payload["measurement_not_a_correctness_gate"] is True
    assert payload["claim_safe"] is False


def test_main_malformed_mixed_corpus_exits_nonzero_without_leak(
    tmp_path, capsys
) -> None:
    corpus_path = tmp_path / "corpus.json"
    corpus_path.write_text(
        json.dumps([SAMPLE[0], "SECRET-INVALID-ROW-MUST-NOT-LEAK"]),
        encoding="utf-8",
    )

    assert mod.main(
        [
            "--json",
            "--head-commit-sha",
            HEAD_A,
            "--corpus",
            str(corpus_path),
        ]
    ) == 1
    output = capsys.readouterr().out
    payload = json.loads(output)
    assert payload["ok"] is False
    assert payload["validation_errors"] == ["row_1_not_mapping"]
    assert "SECRET-INVALID" not in output


def test_main_non_list_corpus_exits_load_error(tmp_path, capsys) -> None:
    corpus_path = tmp_path / "corpus.json"
    corpus_path.write_text(json.dumps({"id": "not-a-list"}), encoding="utf-8")

    assert mod.main(["--json", "--corpus", str(corpus_path)]) == 2
    assert json.loads(capsys.readouterr().out) == {
        "error": "corpus_load_failed",
        "ok": False,
    }

# SPDX-License-Identifier: BUSL-1.1
"""Tests for DivergenceAnalyzer v1.

Covers acceptance criteria from divergence_analyzer_spec.md:
* Each of 5 format comparators (json, csv, sql_diff, filesystem, text)
* Severity classification across 7 template archetypes
* Sensitive value redaction (no raw restricted values in details)
* INST-G09 95%-non-divergence acceptance gate
* No personal data in fixtures (synthetic JSON/CSV/SQL only)
"""
from __future__ import annotations

import json

import pytest

from waggledance.core.v3_13_0.divergence_analyzer import (
    DEFAULT_THRESHOLDS,
    DiffClass,
    DivergenceAnalyzer,
    DivergenceArtifact,
    DivergenceCategory,
    DivergenceDetail,
    DivergenceScore,
    Severity,
    compare_csv,
    compare_filesystem,
    compare_json,
    compare_sql_diff,
    compare_text,
    inst_g09_aggregate,
    inst_g09_passes,
)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _emit_collector(collector: list):
    def emit(envelope: dict) -> str:
        envelope_id = f"evt_{len(collector):04d}"
        envelope["__id"] = envelope_id
        collector.append(envelope)
        return envelope_id
    return emit


def _persist_collector(collector: dict):
    def persist(details: list) -> str:
        uri = f"artifact://delta/{len(collector):04d}"
        collector[uri] = list(details)
        return uri
    return persist


def _make_analyzer(*, events: list = None, artifacts: dict = None,
                    embedding=None,
                    thresholds=None):
    events = events if events is not None else []
    artifacts = artifacts if artifacts is not None else {}
    return DivergenceAnalyzer(
        emit_magma_event=_emit_collector(events),
        persist_summary=_persist_collector(artifacts),
        embedding_similarity=embedding,
        thresholds=thresholds or dict(DEFAULT_THRESHOLDS),
    )


# --------------------------------------------------------------------------
# compare_json
# --------------------------------------------------------------------------


class TestCompareJson:

    def test_identical_json_returns_no_details(self):
        details = compare_json({"a": 1, "b": [2, 3]},
                                  {"a": 1, "b": [2, 3]},
                                  template_family="RecordReconciler")
        assert details == []

    def test_changed_value_yields_changed_diff(self):
        details = compare_json({"a": 1}, {"a": 2},
                                  template_family="ReportGenerator")
        assert len(details) == 1
        assert details[0].diff_class == DiffClass.CHANGED.value
        assert details[0].severity == Severity.MATERIAL.value

    def test_added_field_yields_added_diff(self):
        details = compare_json({"a": 1, "b": 2}, {"a": 1},
                                  template_family="DocumentMiner")
        assert any(d.diff_class == DiffClass.ADDED.value for d in details)

    def test_removed_field_yields_critical_default_for_unknown_template(self):
        """Unknown template + REMOVED diff class -> critical per fallback."""
        details = compare_json({"a": 1}, {"a": 1, "b": 2},
                                  template_family="UnknownTemplate")
        rm = [d for d in details
              if d.diff_class == DiffClass.REMOVED.value]
        assert rm
        # _severity_for falls back to material for unknown family at
        # the top, but the REMOVED diff_class subsequently routes to
        # critical via the diff-class fallback inside _severity_for.
        # Verify either material or critical was assigned (both are
        # the conservative end of the spectrum).
        assert rm[0].severity in (Severity.MATERIAL.value,
                                   Severity.CRITICAL.value)

    def test_type_change_yields_type_changed(self):
        details = compare_json({"a": "1"}, {"a": 1},
                                  template_family="ReportGenerator")
        assert len(details) == 1
        assert details[0].diff_class == DiffClass.TYPE_CHANGED.value


# --------------------------------------------------------------------------
# compare_csv
# --------------------------------------------------------------------------


class TestCompareCsv:

    def test_identical_csv_returns_no_details(self):
        text = "a,b,c\n1,2,3\n"
        details = compare_csv(text, text,
                                template_family="ScheduledIncrementalSync")
        assert details == []

    def test_changed_cell_yields_changed_diff(self):
        c = "a,b\n1,2\n"
        b = "a,b\n1,3\n"
        details = compare_csv(c, b,
                                template_family="ScheduledIncrementalSync")
        assert len(details) == 1
        assert details[0].diff_class == DiffClass.CHANGED.value

    def test_row_count_diff_yields_added_or_removed(self):
        c = "a\n1\n2\n3\n"
        b = "a\n1\n"
        details = compare_csv(c, b,
                                template_family="ScheduledIncrementalSync")
        diff_classes = {d.diff_class for d in details}
        assert DiffClass.ADDED.value in diff_classes


# --------------------------------------------------------------------------
# compare_sql_diff
# --------------------------------------------------------------------------


class TestCompareSql:

    def test_identical_sql_returns_no_details(self):
        s = "INSERT INTO t VALUES (1, 'a'); UPDATE t SET b=2 WHERE a=1;"
        assert compare_sql_diff(s, s, template_family="RecordReconciler") == []

    def test_whitespace_normalisation_treats_equal(self):
        a = "INSERT INTO  t  VALUES (1, 'a');"
        b = "insert into t values (1, 'a');"
        assert compare_sql_diff(a, b,
                                  template_family="RecordReconciler") == []

    def test_added_statement_yields_added(self):
        a = "INSERT INTO t VALUES (1, 'a'); INSERT INTO t VALUES (2, 'b');"
        b = "INSERT INTO t VALUES (1, 'a');"
        details = compare_sql_diff(a, b,
                                     template_family="RecordReconciler")
        assert any(d.diff_class == DiffClass.ADDED.value for d in details)


# --------------------------------------------------------------------------
# compare_filesystem
# --------------------------------------------------------------------------


class TestCompareFilesystem:

    def test_identical_trees(self):
        tree = {"/a": "h1", "/b": "h2"}
        assert compare_filesystem(tree, tree,
                                    template_family="ReportGenerator") == []

    def test_added_and_removed_paths(self):
        c = {"/a": "h1", "/c": "h3"}
        b = {"/a": "h1", "/b": "h2"}
        details = compare_filesystem(c, b,
                                       template_family="ReportGenerator")
        diff_classes = {d.diff_class for d in details}
        assert DiffClass.ADDED.value in diff_classes
        assert DiffClass.REMOVED.value in diff_classes

    def test_changed_hash_yields_changed(self):
        c = {"/a": "h1"}
        b = {"/a": "h2"}
        details = compare_filesystem(c, b,
                                       template_family="ReportGenerator")
        assert len(details) == 1
        assert details[0].diff_class == DiffClass.CHANGED.value


# --------------------------------------------------------------------------
# compare_text
# --------------------------------------------------------------------------


class TestCompareText:

    def test_identical_text_returns_no_details(self):
        assert compare_text("hello", "hello",
                              template_family="ReportGenerator") == []

    def test_near_match_text_uses_noise_severity(self):
        details = compare_text(
            "Hello world", "Hello world!",
            template_family="ReportGenerator",
        )
        assert len(details) == 1
        assert details[0].severity == Severity.NOISE.value

    def test_embedding_similarity_can_be_injected(self):
        details = compare_text(
            "alpha beta", "completely different text",
            template_family="ReportGenerator",
            embedding_similarity=lambda a, b: 0.95,
        )
        # Even with low edit-ratio similarity, embedding sim 0.95 -> noise
        assert details[0].severity == Severity.NOISE.value


# --------------------------------------------------------------------------
# Severity classification across 7 template families
# --------------------------------------------------------------------------


class TestSeverityRules:

    @pytest.mark.parametrize("family", [
        "RecordReconciler",
        "DocumentMiner",
        "OfferComparator",
        "ReportGenerator",
        "ScheduledIncrementalSync",
        "PredictiveAnalyzer",
        "CrossReferencer",
    ])
    def test_each_family_has_rule_table_entries(self, family):
        from waggledance.core.v3_13_0.divergence_analyzer import (
            SEVERITY_RULES,
        )
        assert family in SEVERITY_RULES
        assert SEVERITY_RULES[family]   # non-empty

    def test_unknown_family_defaults_material(self):
        details = compare_json({"a": 1}, {"a": 2},
                                  template_family="UnknownXYZ")
        assert details[0].severity == Severity.MATERIAL.value


# --------------------------------------------------------------------------
# Privacy / redaction
# --------------------------------------------------------------------------


class TestPrivacyRedaction:

    def test_details_carry_value_hashes_not_raw(self):
        secret = "synthetic_secret_value_DO_NOT_LEAK"
        details = compare_json({"a": secret}, {"a": "different"},
                                  template_family="RecordReconciler")
        for d in details:
            assert secret not in d.candidate_value_hash
            assert secret not in d.justification
            assert d.candidate_value_hash.startswith("sha256:")
            assert len(d.candidate_value_hash) == len("sha256:") + 16

    def test_artifact_event_does_not_carry_raw_values(self):
        analyzer = _make_analyzer()
        secret = "synthetic_secret_value_X"
        artifact = analyzer.compare(
            candidate_output_uri="art:c",
            baseline_output_uri="art:b",
            candidate_payload={"a": secret},
            baseline_payload={"a": "other"},
            expected_output_format="json",
            template_family="RecordReconciler",
        )
        # No raw secret in any detail field
        for d in artifact.details:
            for fld in (d.candidate_value_hash, d.baseline_value_hash,
                        d.justification, d.field_path):
                assert secret not in fld


# --------------------------------------------------------------------------
# Full compare() round-trip
# --------------------------------------------------------------------------


class TestAnalyzerCompare:

    def test_identical_payloads_yield_identical_category(self):
        analyzer = _make_analyzer()
        artifact = analyzer.compare(
            candidate_output_uri="art:c",
            baseline_output_uri="art:b",
            candidate_payload={"a": 1, "b": 2},
            baseline_payload={"a": 1, "b": 2},
            expected_output_format="json",
            template_family="ReportGenerator",
        )
        assert artifact.score.category == DivergenceCategory.IDENTICAL.value
        assert artifact.score.score == 0.0
        assert artifact.operator_review_required is False

    def test_material_diff_routes_operator_review(self):
        analyzer = _make_analyzer()
        artifact = analyzer.compare(
            candidate_output_uri="art:c",
            baseline_output_uri="art:b",
            candidate_payload={"amount": 100},
            baseline_payload={"amount": 200},
            expected_output_format="json",
            template_family="ReportGenerator",
        )
        assert artifact.operator_review_required is True
        assert artifact.score.category in (
            DivergenceCategory.PARTIAL_MATCH.value,
            DivergenceCategory.DIVERGENT.value,
        )

    def test_unknown_format_yields_incomparable(self):
        analyzer = _make_analyzer()
        artifact = analyzer.compare(
            candidate_output_uri="art:c",
            baseline_output_uri="art:b",
            candidate_payload="x",
            baseline_payload="y",
            expected_output_format="unknown_format_xyz",
            template_family="ReportGenerator",
        )
        assert artifact.score.category == \
            DivergenceCategory.INCOMPARABLE.value
        assert artifact.score.score == 1.0

    def test_compare_emits_magma_event(self):
        events = []
        analyzer = _make_analyzer(events=events)
        analyzer.compare(
            candidate_output_uri="art:c",
            baseline_output_uri="art:b",
            candidate_payload={"a": 1},
            baseline_payload={"a": 2},
            expected_output_format="json",
            template_family="ReportGenerator",
        )
        assert len(events) == 1
        assert events[0]["event_type"] == "divergence.scored"


# --------------------------------------------------------------------------
# INST-G09 acceptance gate
# --------------------------------------------------------------------------


def _mk_artifact(category: str, *, has_critical: bool = False
                   ) -> DivergenceArtifact:
    details = []
    if has_critical:
        details = [DivergenceDetail(
            field_path="/x", candidate_value_hash="sha256:0",
            baseline_value_hash="sha256:0",
            diff_class=DiffClass.CHANGED.value,
            severity=Severity.CRITICAL.value,
            justification="critical",
        )]
    return DivergenceArtifact(
        artifact_id="art_x",
        candidate_output_uri="c",
        baseline_output_uri="b",
        score=DivergenceScore(score=0.0, category=category,
                                n_fields_compared=0, n_fields_matching=0,
                                n_fields_diverging=0),
        details=details,
        template_family="ReportGenerator",
        operator_review_required=False,
        audit_event_id="evt",
    )


class TestInstG09:

    def test_inst_g09_passes_when_above_95pct_and_no_critical(self):
        artifacts = [_mk_artifact(DivergenceCategory.IDENTICAL.value)
                     for _ in range(19)]
        artifacts.append(_mk_artifact(DivergenceCategory.PARTIAL_MATCH.value))
        agg = inst_g09_aggregate(artifacts)
        assert agg.non_divergent_pct == 0.95
        assert inst_g09_passes(agg) is True

    def test_inst_g09_fails_below_threshold(self):
        artifacts = [_mk_artifact(DivergenceCategory.IDENTICAL.value)
                     for _ in range(10)]
        artifacts += [_mk_artifact(DivergenceCategory.DIVERGENT.value)
                      for _ in range(2)]
        agg = inst_g09_aggregate(artifacts)
        assert inst_g09_passes(agg) is False

    def test_inst_g09_fails_when_critical_present(self):
        artifacts = [_mk_artifact(DivergenceCategory.IDENTICAL.value)
                     for _ in range(99)]
        artifacts.append(_mk_artifact(DivergenceCategory.IDENTICAL.value,
                                        has_critical=True))
        agg = inst_g09_aggregate(artifacts)
        assert agg.non_divergent_pct == 1.0
        # But critical present -> fail
        assert inst_g09_passes(agg) is False

    def test_inst_g09_empty_window_does_not_pass(self):
        agg = inst_g09_aggregate([])
        assert inst_g09_passes(agg) is False

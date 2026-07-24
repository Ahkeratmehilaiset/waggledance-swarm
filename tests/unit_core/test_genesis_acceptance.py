# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

from copy import deepcopy
import importlib.util
import io
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
TOOL_PATH = ROOT / "tools" / "run_genesis_acceptance_corpus.py"
CORPUS_PATH = Path(__file__).with_name("data") / "genesis_acceptance_v1.json"

SPEC = importlib.util.spec_from_file_location("genesis_acceptance_tool", TOOL_PATH)
assert SPEC is not None and SPEC.loader is not None
tool = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(tool)


def _lineage_entries(case):
    subject = case["subject"]
    if subject["lineage_kind"] == "entry":
        return [subject["entry"]]
    if subject["lineage_kind"] == "link":
        return [subject["link"]["child"], subject["link"]["parent"]]
    return subject["registry"]


def test_independent_reference_reproduces_every_pinned_golden():
    document = tool.load_corpus(CORPUS_PATH)
    for case in document["cases"]:
        if case["kind"] == "negative":
            assert case["golden"] is None
            continue
        if case["axis"] in {"identity", "authority", "timestamp", "restore"}:
            key = (
                "stored_mapping"
                if case["axis"] == "restore"
                else "identity_mapping"
            )
            mapping = case["subject"][key]
            assert tool.ref_cell_id(
                pubkey_digest=mapping["pubkey_digest"],
                genesis_material_digest=mapping["genesis_material_digest"],
                created_at_utc=mapping["created_at_utc"],
            ) == case["golden"]["cell_id"]
        elif case["axis"] == "lineage":
            for entry in _lineage_entries(case):
                expected = tool.ref_entry_hash(
                    **{
                        key: entry[key]
                        for key in (
                            "cell_id",
                            "parent_cell_id",
                            "lineage_prev_hash",
                            "depth",
                            "inherited_goal_slice_digest",
                            "inherited_budget_slice_digest",
                        )
                    }
                )
                assert expected == entry["entry_hash"]
                if case["golden"] is not None:
                    assert expected == case["golden"]["entry_hash"]


def test_corpus_runs_public_verifiers_separately_and_restore_rebuilds():
    report = tool.run_corpus(CORPUS_PATH)
    assert report["ok"] is True
    assert report["coverage_complete"] is True
    assert report["total"] == 33
    assert report["accepted"] == 8
    assert report["rejected"] == 25
    assert report["mismatches"] == []
    assert report["divergences"] == []
    assert report["corpus_matrix_complete"] is True
    assert report["missing_case_ids"] == []


def test_frozen_manifest_rejects_relabels_and_duplicate_semantics(tmp_path):
    document = tool.load_corpus(CORPUS_PATH)
    relabelled = deepcopy(document)
    relabelled["cases"][0]["case_id"] = "identity.positive.relabelled"
    path = tmp_path / "relabelled.json"
    path.write_text(json.dumps(relabelled), encoding="utf-8")
    with pytest.raises(tool.CorpusError, match=r"^case:semantic_id$"):
        tool.load_corpus(path)

    duplicated = deepcopy(document)
    duplicated["cases"].append(deepcopy(duplicated["cases"][0]))
    path.write_text(json.dumps(duplicated), encoding="utf-8")
    with pytest.raises(tool.CorpusError, match=r"^case:duplicate_id$"):
        tool.load_corpus(path)


def test_pinned_identity_cells_bind_subject_golden_and_expectation(tmp_path):
    document = tool.load_corpus(CORPUS_PATH)
    assert {
        case["case_id"]
        for case in document["cases"]
        if case["axis"] == "identity"
    } == {
        case_id
        for case_id in tool.PINNED_CASE_DIGESTS
        if case_id.startswith("identity.")
    }

    tampered = deepcopy(document)
    tampered["cases"][1]["expect"]["reason"] = "keyset"
    path = tmp_path / "tampered_identity_semantics.json"
    path.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(tool.CorpusError, match=r"^case:semantic_digest$"):
        tool.load_corpus(path)


def test_authority_axis_has_identity_only_witness_and_rejects_smuggling():
    cases = {
        case["case_id"]: case
        for case in tool.load_corpus(CORPUS_PATH)["cases"]
        if case["axis"] == "authority"
    }
    assert set(cases) == {
        "authority.positive.identity_only",
        "authority.negative.grant_field",
        "authority.negative.budget_field",
        "authority.negative.capability_field",
    }
    assert set(cases).issubset(tool.PINNED_CASE_DIGESTS)
    for case in cases.values():
        result = tool.run_case(case)
        assert result["matched_expectation"] is True
        assert result["diverged"] is False

    smuggled = deepcopy(cases["authority.positive.identity_only"])
    smuggled["subject"]["authority_grant"] = True
    result = tool.run_case(smuggled)
    assert result["verifier_verdict"] == "REJECT"
    assert result["reason"] == "case:semantic_digest"
    assert result["matched_expectation"] is False
    assert tool._authority_result(smuggled) == {
        "oracle_verdict": "REJECT",
        "verifier_verdict": "REJECT",
        "reason": "authority:keyset",
        "diverged": False,
    }


def test_timestamp_axis_pins_canonical_positive_and_rejection_matrix():
    cases = {
        case["case_id"]: case
        for case in tool.load_corpus(CORPUS_PATH)["cases"]
        if case["axis"] == "timestamp"
    }
    assert set(cases) == {
        "timestamp.positive.positive",
        "timestamp.negative.unicode_decimal",
        "timestamp.negative.impossible_calendar",
        "timestamp.negative.trailing_zero_fraction",
        "timestamp.negative.non_utc",
        "timestamp.negative.naive",
    }
    assert set(cases).issubset(tool.PINNED_CASE_DIGESTS)
    for case in cases.values():
        result = tool.run_case(case)
        assert result["matched_expectation"] is True
        assert result["diverged"] is False


def test_lineage_axis_pins_positive_witnesses_and_closure_rejections():
    cases = {
        case["case_id"]: case
        for case in tool.load_corpus(CORPUS_PATH)["cases"]
        if case["axis"] == "lineage"
    }
    assert set(cases) == {
        "lineage.positive.positive_entry",
        "lineage.positive.positive_link",
        "lineage.positive.positive_registry",
        "lineage.negative.entry_hash_mismatch",
        "lineage.negative.self_parent",
        "lineage.negative.depth_off_by_one",
        "lineage.negative.root_marker_disagreement",
        "lineage.negative.orphan_parent",
        "lineage.negative.two_roots",
        "lineage.negative.duplicate_cell_id",
        "lineage.negative.broken_prev_link",
        "lineage.negative.non_monotonic_depth",
    }
    assert set(cases).issubset(tool.PINNED_CASE_DIGESTS)
    for case in cases.values():
        result = tool.run_case(case)
        assert result["matched_expectation"] is True
        assert result["diverged"] is False


def test_restore_axis_pins_deterministic_rebuild_and_drift_rejection():
    cases = {
        case["case_id"]: case
        for case in tool.load_corpus(CORPUS_PATH)["cases"]
        if case["axis"] == "restore"
    }
    assert set(cases) == {
        "restore.positive.from_stored_mapping",
        "restore.positive.same_facts_same_id",
        "restore.negative.drifted_field_rejects",
    }
    assert set(cases).issubset(tool.PINNED_CASE_DIGESTS)
    for case in cases.values():
        result = tool.run_case(case)
        assert result["matched_expectation"] is True
        assert result["diverged"] is False

    restored = cases["restore.positive.same_facts_same_id"]
    assert (
        tool.ref_cell_id(
            **{
                key: restored["subject"]["stored_mapping"][key]
                for key in (
                    "pubkey_digest",
                    "genesis_material_digest",
                    "created_at_utc",
                )
            }
        )
        == restored["golden"]["cell_id"]
    )


def test_cli_declares_code_probe_gate_without_fabricating_execution():
    report = tool.run_corpus(CORPUS_PATH)
    assert report["required_code_probes"] == sorted(tool.REQUIRED_CODE_PROBES)
    assert report["code_probe_gate"] == "separate_executable_test_required"
    assert "executed_code_probes" not in report
    assert "container_code_probe" not in report["missing_case_ids"]
    assert (
        tool.REQUIRED_CASE_MANIFEST["authority.positive.identity_only"]
        == ("authority", "positive")
    )


def test_raw_cap_is_checked_before_json_decode(tmp_path):
    path = tmp_path / "oversized.json"
    path.write_bytes(b"{" + b"x" * tool.MAX_RAW_BYTES)
    with pytest.raises(tool.CorpusError, match=r"^caps:raw_bytes$"):
        tool.load_corpus(path)


def test_raw_cap_uses_one_bounded_read():
    class TrackingReader(io.BytesIO):
        read_sizes: list[int] = []

        def read(self, size: int = -1) -> bytes:
            self.read_sizes.append(size)
            return super().read(size)

    reader = TrackingReader(b"{" + b"x" * tool.MAX_RAW_BYTES)

    class CorpusPath:
        def open(self, mode: str):
            assert mode == "rb"
            return reader

    with pytest.raises(tool.CorpusError, match=r"^caps:raw_bytes$"):
        tool.load_corpus(CorpusPath())
    assert reader.read_sizes == [tool.MAX_RAW_BYTES + 1]


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_nonfinite_json_constants_fail_closed(tmp_path, constant):
    path = tmp_path / "nonfinite.json"
    path.write_bytes(
        (
            '{"schema_version":"'
            + tool.CORPUS_SCHEMA
            + '","cases":['
            + constant
            + "]}"
        ).encode("ascii")
    )
    with pytest.raises(tool.CorpusError, match=r"^corpus:json$"):
        tool.load_corpus(path)


def test_duplicate_json_keys_fail_closed(tmp_path):
    path = tmp_path / "duplicate.json"
    path.write_bytes(
        (
            '{"schema_version":"'
            + tool.CORPUS_SCHEMA
            + '","schema_version":"'
            + tool.CORPUS_SCHEMA
            + '","cases":[]}'
        ).encode("ascii")
    )
    with pytest.raises(tool.CorpusError, match=r"^corpus:json$"):
        tool.load_corpus(path)


@pytest.mark.parametrize(
    "encoding",
    ["utf-8-sig", "utf-16", "utf-16-le", "utf-16-be"],
)
def test_corpus_requires_strict_utf8_without_any_bom(tmp_path, encoding):
    path = tmp_path / "encoded.json"
    raw = json.dumps(
        {"schema_version": tool.CORPUS_SCHEMA, "cases": []}
    ).encode(encoding)
    if encoding in {"utf-16-le", "utf-16-be"}:
        bom = b"\xff\xfe" if encoding.endswith("-le") else b"\xfe\xff"
        raw = bom + raw
    path.write_bytes(raw)
    with pytest.raises(tool.CorpusError, match=r"^corpus:encoding$"):
        tool.load_corpus(path)


@pytest.mark.parametrize(
    "hostile_value",
    ["1e1000000", '"\\ud800"'],
)
def test_exponent_bomb_and_unpaired_surrogate_have_stable_error(
    tmp_path, hostile_value
):
    path = tmp_path / "hostile_scalar.json"
    path.write_bytes(
        (
            '{"schema_version":"'
            + tool.CORPUS_SCHEMA
            + '","cases":['
            + hostile_value
            + "]}"
        ).encode("ascii")
    )
    with pytest.raises(tool.CorpusError, match=r"^corpus:json$"):
        tool.load_corpus(path)


def test_huge_integer_and_decoder_recursion_fail_closed(tmp_path, monkeypatch):
    path = tmp_path / "hostile.json"
    path.write_bytes(
        b'{"schema_version":"'
        + tool.CORPUS_SCHEMA.encode("ascii")
        + b'","cases":['
        + b"9" * 5_000
        + b"]}"
    )
    with pytest.raises(tool.CorpusError, match=r"^corpus:json$"):
        tool.load_corpus(path)

    monkeypatch.setattr(
        tool.json,
        "loads",
        lambda *args, **kwargs: (_ for _ in ()).throw(RecursionError()),
    )
    with pytest.raises(tool.CorpusError, match=r"^corpus:json$"):
        tool.load_corpus(path)


def test_deep_json_nesting_hits_explicit_depth_cap(tmp_path):
    path = tmp_path / "deep.json"
    nested = (
        b'{"schema_version":"'
        + tool.CORPUS_SCHEMA.encode("ascii")
        + b'","cases":[{"nested":'
        + b"[" * (tool.MAX_JSON_DEPTH + 1)
        + b"0"
        + b"]" * (tool.MAX_JSON_DEPTH + 1)
        + b"}]}"
    )
    path.write_bytes(nested)
    with pytest.raises(tool.CorpusError, match=r"^caps:json_depth$"):
        tool.load_corpus(path)


def test_case_count_and_case_byte_caps_fail_closed(tmp_path, monkeypatch):
    path = tmp_path / "corpus.json"
    path.write_text(
        json.dumps({"schema_version": tool.CORPUS_SCHEMA, "cases": [{}, {}]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(tool, "MAX_CASES", 1)
    with pytest.raises(tool.CorpusError, match=r"^caps:case_count$"):
        tool.load_corpus(path)

    monkeypatch.setattr(tool, "MAX_CASES", 2)
    monkeypatch.setattr(tool, "MAX_CASE_BYTES", 1)
    with pytest.raises(tool.CorpusError, match=r"^caps:case_bytes$"):
        tool.load_corpus(path)


def test_hostile_container_is_rejected_without_protocol_invocation():
    class SlowDict(dict):
        invoked = False

        def keys(self):
            self.invoked = True
            raise AssertionError("must not be invoked")

    hostile = SlowDict()
    result = tool.run_case(hostile)
    assert result["verifier_verdict"] == "REJECT"
    assert result["reason"] == "case:not_mapping"
    assert hostile.invoked is False


def test_direct_case_rejects_outer_smuggling_and_enforces_case_cap(monkeypatch):
    case = deepcopy(tool.load_corpus(CORPUS_PATH)["cases"][0])
    case["authority_grant"] = True
    result = tool.run_case(case)
    assert result["verifier_verdict"] == "REJECT"
    assert result["reason"] == "case:keyset"
    assert result["matched_expectation"] is False

    case.pop("authority_grant")
    monkeypatch.setattr(tool, "MAX_CASE_BYTES", 1)
    result = tool.run_case(case)
    assert result["verifier_verdict"] == "REJECT"
    assert result["reason"] == "caps:case_bytes"
    assert result["matched_expectation"] is False


def test_direct_case_enforces_depth_before_semantic_or_verifier_work():
    case = deepcopy(tool.load_corpus(CORPUS_PATH)["cases"][0])
    nested: object = "leaf"
    for _ in range(tool.MAX_JSON_DEPTH + 1):
        nested = [nested]
    case["subject"]["nested"] = nested
    result = tool.run_case(case)
    assert result["verifier_verdict"] == "REJECT"
    assert result["reason"] == "caps:json_depth"
    assert result["matched_expectation"] is False


def test_direct_case_rejects_nested_hostile_value_without_invoking_protocol():
    class HostileMapping(dict):
        invoked = False

        def keys(self):
            self.invoked = True
            raise AssertionError("must not be invoked")

    case = deepcopy(tool.load_corpus(CORPUS_PATH)["cases"][0])
    hostile = HostileMapping()
    case["subject"]["identity_mapping"] = hostile
    result = tool.run_case(case)
    assert result["verifier_verdict"] == "REJECT"
    assert result["reason"] == "case:json"
    assert result["matched_expectation"] is False
    assert hostile.invoked is False


@pytest.mark.parametrize("nonfinite", [float("nan"), float("inf"), -float("inf")])
def test_direct_case_rejects_nonfinite_values(nonfinite):
    case = deepcopy(tool.load_corpus(CORPUS_PATH)["cases"][0])
    case["subject"]["identity_mapping"]["created_at_utc"] = nonfinite
    result = tool.run_case(case)
    assert result["verifier_verdict"] == "REJECT"
    assert result["reason"] == "case:json"
    assert result["matched_expectation"] is False


def test_malformed_expectation_fails_closed_without_crashing():
    result = tool.run_case(
        {
            "case_id": "container.negative.expect_not_mapping",
            "axis": "unknown",
            "expect": [],
        }
    )
    assert result == {
        "schema_version": tool.CASE_REPORT_SCHEMA,
        "oracle_verdict": "REJECT",
        "verifier_verdict": "REJECT",
        "reason": "expect:not_mapping",
        "diverged": False,
        "case_id": "container.negative.expect_not_mapping",
        "axis": "unknown",
        "matched_expectation": False,
        "operator_authorized": False,
        "oracle_digest": None,
    }


def test_case_report_digest_is_rederivable_and_binds_every_decision_field():
    case = tool.load_corpus(CORPUS_PATH)["cases"][0]
    report = tool.run_case(case)
    assert report["schema_version"] == tool.CASE_REPORT_SCHEMA
    assert report["oracle_digest"] == tool.derive_case_report_digest(report)

    mutations = {
        "oracle_verdict": "REJECT",
        "verifier_verdict": "REJECT",
        "reason": "tampered",
        "diverged": True,
        "matched_expectation": False,
        "operator_authorized": True,
    }
    for field, value in mutations.items():
        tampered = dict(report)
        tampered[field] = value
        assert tool.derive_case_report_digest(tampered) != report["oracle_digest"]

    missing = dict(report)
    missing.pop("matched_expectation")
    with pytest.raises(tool.CorpusError, match=r"^report:keyset$"):
        tool.derive_case_report_digest(missing)

    extra = dict(report)
    extra["unexpected"] = True
    with pytest.raises(tool.CorpusError, match=r"^report:keyset$"):
        tool.derive_case_report_digest(extra)

    class LyingString(str):
        def __eq__(self, other):
            raise AssertionError("must reject without comparison")

    tampered = dict(report)
    tampered["schema_version"] = LyingString(tool.CASE_REPORT_SCHEMA)
    with pytest.raises(tool.CorpusError, match=r"^report:schema_version$"):
        tool.derive_case_report_digest(tampered)


def test_cli_emits_machine_readable_complete_corpus_report(capsys):
    assert tool.main([str(CORPUS_PATH)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["schema_version"] == tool.REPORT_SCHEMA
    assert report["ok"] is True
    assert report["coverage_complete"] is True

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


def test_independent_reference_reproduces_every_pinned_golden():
    document = tool.load_corpus(CORPUS_PATH)
    for case in document["cases"]:
        if case["axis"] in {"identity", "restore"}:
            key = (
                "identity_mapping"
                if case["axis"] == "identity"
                else "stored_mapping"
            )
            mapping = case["subject"][key]
            assert tool.ref_cell_id(
                pubkey_digest=mapping["pubkey_digest"],
                genesis_material_digest=mapping["genesis_material_digest"],
                created_at_utc=mapping["created_at_utc"],
            ) == case["golden"]["cell_id"]
        elif case["axis"] == "lineage":
            entry = case["subject"]["entry"]
            assert tool.ref_entry_hash(
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
            ) == case["golden"]["entry_hash"]


def test_corpus_runs_public_verifiers_separately_and_restore_rebuilds():
    report = tool.run_corpus(CORPUS_PATH)
    # This bounded foundation corpus deliberately remains fail closed until
    # the frozen negative axis-by-reason matrix is added.
    assert report["ok"] is False
    assert report["coverage_complete"] is False
    assert report["total"] == 3
    assert report["accepted"] == 3
    assert report["mismatches"] == []
    assert report["divergences"] == []
    assert report["corpus_matrix_complete"] is False
    assert len(report["missing_case_ids"]) == (
        len(tool.REQUIRED_CASE_MANIFEST) - report["total"]
    )


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
        "oracle_digest": None,
    }


def test_case_report_digest_is_rederivable_and_binds_report_verdict():
    case = tool.load_corpus(CORPUS_PATH)["cases"][0]
    report = tool.run_case(case)
    assert report["schema_version"] == tool.CASE_REPORT_SCHEMA
    assert report["oracle_digest"] == tool.derive_case_report_digest(report)

    tampered = dict(report)
    tampered["verifier_verdict"] = "REJECT"
    assert tool.derive_case_report_digest(tampered) != report["oracle_digest"]

    class LyingString(str):
        def __eq__(self, other):
            raise AssertionError("must reject without comparison")

    tampered["schema_version"] = LyingString(tool.CASE_REPORT_SCHEMA)
    with pytest.raises(tool.CorpusError, match=r"^report:schema_version$"):
        tool.derive_case_report_digest(tampered)


def test_cli_emits_machine_readable_fail_closed_foundation(capsys):
    assert tool.main([str(CORPUS_PATH)]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["schema_version"] == tool.REPORT_SCHEMA
    assert report["ok"] is False
    assert report["coverage_complete"] is False

# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

import importlib.util
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


def test_raw_cap_is_checked_before_json_decode(tmp_path):
    path = tmp_path / "oversized.json"
    path.write_bytes(b"{" + b"x" * tool.MAX_RAW_BYTES)
    with pytest.raises(tool.CorpusError, match=r"^caps:raw_bytes$"):
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


def test_cli_emits_machine_readable_fail_closed_foundation(capsys):
    assert tool.main([str(CORPUS_PATH)]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["schema_version"] == tool.REPORT_SCHEMA
    assert report["ok"] is False
    assert report["coverage_complete"] is False

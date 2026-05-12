# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import yaml


ROOT = Path(__file__).resolve().parents[2]
CONTRACT_PATH = ROOT / "docs" / "eig2" / "contracts" / "progressive_replay_l0_l4.json"
SCHEMA_PATH = (
    ROOT
    / ".orchestrator"
    / "contracts"
    / "eig2_progressive_replay_contract.schema.json"
)
CONFIG_PATH = ROOT / "configs" / "explosive_intelligence_growth_v2.yaml"
ADR_PATH = ROOT / "docs" / "eig2" / "adr" / "021-progressive-replay-l0-l4-contract.md"
ADR_INDEX_PATH = ROOT / "docs" / "eig2" / "adr" / "000-eig2-m0-index.md"
ARCHITECTURE_PATH = ROOT / "docs" / "architecture" / "explosive_intelligence_growth_2.md"


def _contract() -> dict:
    return json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))


def _config_magma_strata() -> dict:
    data = yaml.load(CONFIG_PATH.read_text(encoding="utf-8"), Loader=yaml.CSafeLoader)
    return data["explosive_intelligence_growth_v2"]["magma_strata"]


def test_progressive_replay_contract_validates_against_schema() -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    jsonschema.validate(_contract(), schema)


def test_progressive_replay_levels_match_config_budgets() -> None:
    contract = _contract()
    cfg = _config_magma_strata()
    levels = {item["level"]: item for item in contract["levels"]}

    assert tuple(levels) == ("L0", "L1", "L2", "L3", "L4")
    assert levels["L0"]["max_tokens"] == cfg["l0_budget_tokens"] == 128
    assert levels["L1"]["max_tokens"] == cfg["l1_budget_tokens"] == 512
    assert levels["L2"]["max_tokens"] == cfg["l2_budget_tokens"] == 2048
    assert levels["L3"]["max_tokens"] == cfg["l3_budget_tokens"] == 8192
    assert levels["L4"]["max_tokens"] is None
    assert cfg["l4_requires_high_risk_or_audit"] is True
    assert [levels[name]["max_tokens"] for name in ("L0", "L1", "L2", "L3")] == [
        128,
        512,
        2048,
        8192,
    ]


def test_boot_contract_forbids_deep_or_forensic_replay() -> None:
    contract = _contract()
    boot = contract["boot_contract"]
    levels = {item["level"]: item for item in contract["levels"]}

    assert boot["allowed_levels"] == ["L0", "L1"]
    assert boot["forbidden_levels"] == ["L2", "L3", "L4"]
    assert boot["full_raw_replay_allowed"] is False
    assert boot["complexity"] == "O(K)"
    assert levels["L0"]["may_load_at_boot"] is True
    assert levels["L1"]["may_load_at_boot"] is True
    assert levels["L2"]["may_load_at_boot"] is False
    assert levels["L3"]["may_load_at_boot"] is False
    assert levels["L4"]["may_load_at_boot"] is False


def test_raw_magma_remains_authoritative_and_card_failures_fallback() -> None:
    contract = _contract()
    fallback = contract["fallback_contract"]
    writer = contract["writer_contract"]

    assert contract["source_of_truth"] == "raw_magma"
    assert fallback["missing_card"] == "raw_replay"
    assert fallback["stale_card"] == "raw_replay"
    assert fallback["malformed_card"] == "raw_replay"
    assert fallback["hash_mismatch"] == "fail_closed_to_raw_replay"
    assert fallback["raw_replay_must_verify_source_hash"] is True
    assert writer["new_writers_require_adrs"] == ["ADR-011", "ADR-014", "ADR-015"]
    assert writer["derived_writes_optional"] is True
    assert writer["raw_append_authoritative"] is True
    assert writer["request_path_blocking_allowed"] is False


def test_progressive_replay_adr_documents_contract_and_review_state() -> None:
    text = ADR_PATH.read_text(encoding="utf-8")

    assert "docs/eig2/contracts/progressive_replay_l0_l4.json" in text
    assert ".orchestrator/contracts/eig2_progressive_replay_contract.schema.json" in text
    assert "Boot may load only L0 and bounded L1" in text
    assert "L4 is unbounded but only for audit, rollback, or high-risk paths" in text
    assert "Peer reviewer: Claude" in text


def test_progressive_replay_contract_is_indexed_in_eig2_docs() -> None:
    index = ADR_INDEX_PATH.read_text(encoding="utf-8")
    architecture = ARCHITECTURE_PATH.read_text(encoding="utf-8")

    assert "021-progressive-replay-l0-l4-contract.md" in index
    assert "L11 | MAGMA progressive replay L0-L4 contract" in index
    assert "docs/eig2/contracts/progressive_replay_l0_l4.json" in architecture
    assert "ADR-021" in architecture

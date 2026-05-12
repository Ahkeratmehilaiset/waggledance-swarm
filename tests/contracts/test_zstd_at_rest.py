# SPDX-License-Identifier: BUSL-1.1
"""zstd-at-rest contract (L18, ADR-030)."""
from __future__ import annotations

import json
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
ADR_PATH = PROJECT_ROOT / "docs" / "eig2" / "adr" / "030-zstd-at-rest.md"
CONTRACT_PATH = PROJECT_ROOT / "docs" / "eig2" / "contracts" / "zstd_at_rest.json"
MERKLE_CONTRACT = (
    PROJECT_ROOT / "docs" / "eig2" / "contracts" / "merkle_batched_hash_verification.json"
)
COLD_TIER_CONTRACT = (
    PROJECT_ROOT / "docs" / "eig2" / "contracts" / "cold_tier_read_through_cache.json"
)


REQUIRED_INVARIANT_IDS = {f"ZAR-00{i}" for i in range(1, 8)}


def test_adr_030_file_exists() -> None:
    assert ADR_PATH.exists()


def test_adr_030_marks_substrate_only_landing() -> None:
    text = ADR_PATH.read_text(encoding="utf-8")
    assert "substrate-only landing" in text.lower()


def test_adr_030_references_related_adrs() -> None:
    text = ADR_PATH.read_text(encoding="utf-8")
    for adr_id in ("ADR-021", "ADR-024", "ADR-028", "ADR-029"):
        assert adr_id in text


def test_machine_readable_contract_exists() -> None:
    assert CONTRACT_PATH.exists()


def test_cold_threshold_default_is_7_days() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    assert contract["policy_defaults"]["cold_threshold_days"] == 7


def test_zstd_level_default_is_3() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    assert contract["policy_defaults"]["zstd_level"] == 3


def test_default_enabled() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    assert contract["policy_defaults"]["zstd_at_rest_enabled"] is True


def test_compression_unit_is_merkle_batch() -> None:
    """Compression MUST align with the Merkle-batch boundary from ADR-028.
    Per-event compression is forbidden because it loses zstd dictionary
    sharing across the batch."""
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    assert contract["compression_unit"] == "merkle_batch"


def test_codec_is_zstd_only() -> None:
    """No alt-codec drift. Per ADR-030, zstd is the only encoding pinned;
    lz4/snappy are explicitly out of scope."""
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    assert contract["codec"] == "zstd"


def test_contract_invariants_match_required_set() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    ids = {item.get("id") for item in contract.get("invariants", [])}
    assert ids == REQUIRED_INVARIANT_IDS


def test_each_invariant_has_must_clauses() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    for item in contract.get("invariants", []):
        assert isinstance(item.get("must"), list) and item["must"]


def test_hash_domain_unchanged_invariant_present() -> None:
    """ZAR-004 forbids any change to ADR-028's hash domain. The compression
    layer MUST NOT affect what bytes go into sha256."""
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    zar004 = next(
        (i for i in contract["invariants"] if i["id"] == "ZAR-004"), None
    )
    assert zar004 is not None
    must_text = " ".join(zar004.get("must", [])).lower()
    assert "uncompressed" in must_text
    assert "hash" in must_text


def test_glued_to_merkle_and_cold_tier_contracts() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    deps = contract.get("depends_on_contracts", [])
    assert "merkle_batched_hash_verification.json" in deps
    assert "cold_tier_read_through_cache.json" in deps
    assert MERKLE_CONTRACT.exists()
    assert COLD_TIER_CONTRACT.exists()


def test_contract_marks_out_of_scope() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    out_of_scope = contract.get("out_of_scope", [])
    assert isinstance(out_of_scope, list) and out_of_scope

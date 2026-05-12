# SPDX-License-Identifier: BUSL-1.1
"""Merkle-batched hash verification contract (L16, ADR-028).

Substrate-only landing. Implementation of MagmaMerkleBatcher is deferred.
Pins batch_size + hash function + canonical pair concatenation + odd-leaf
padding + invariants so any implementation cannot land with subtly
different math (which would break cross-verifier compatibility).
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
ADR_PATH = PROJECT_ROOT / "docs" / "eig2" / "adr" / "028-merkle-batched-hash-verification.md"
CONTRACT_PATH = (
    PROJECT_ROOT / "docs" / "eig2" / "contracts" / "merkle_batched_hash_verification.json"
)
PROGRESSIVE_REPLAY_CONTRACT = (
    PROJECT_ROOT / "docs" / "eig2" / "contracts" / "progressive_replay_l0_l4.json"
)


REQUIRED_INVARIANT_IDS = {
    "MBH-001",  # fixed_batch_size
    "MBH-002",  # sha256_hash_function
    "MBH-003",  # canonical_pair_concatenation
    "MBH-004",  # odd_leaf_padding
    "MBH-005",  # root_stored_on_batch_close
    "MBH-006",  # per_event_proof_path
    "MBH-007",  # mismatch_fails_closed
}


def test_adr_028_file_exists() -> None:
    assert ADR_PATH.exists(), f"ADR-028 missing at {ADR_PATH}"


def test_adr_028_marks_substrate_only_landing() -> None:
    text = ADR_PATH.read_text(encoding="utf-8")
    assert "substrate-only landing" in text.lower()


def test_adr_028_references_related_adrs() -> None:
    text = ADR_PATH.read_text(encoding="utf-8")
    for adr_id in ("ADR-021", "ADR-022", "ADR-024"):
        assert adr_id in text, (
            f"ADR-028 must reference {adr_id} (progressive replay + snapshot rotation "
            "+ card schema provide the hash-verification substrate)."
        )


def test_machine_readable_contract_exists() -> None:
    assert CONTRACT_PATH.exists(), f"Contract missing at {CONTRACT_PATH}"


def test_batch_size_is_1024() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    assert contract.get("batch_size") == 1024, (
        f"batch_size MUST be 1024 (got {contract.get('batch_size')}). "
        "log2(1024) = 10 proof hashes -- changing requires ADR amendment "
        "AND recomputation of all existing batch roots."
    )


def test_hash_function_is_sha256() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    assert contract.get("hash_function") == "sha256"
    assert contract.get("leaf_size_bytes") == 32, (
        "leaf hash size MUST be 32 bytes (full sha256). No truncation."
    )


def test_contract_invariants_match_required_set() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    ids = {item.get("id") for item in contract.get("invariants", [])}
    missing = REQUIRED_INVARIANT_IDS - ids
    extra = ids - REQUIRED_INVARIANT_IDS
    assert not missing, f"Missing invariants: {sorted(missing)}"
    assert not extra, f"Extra invariants: {sorted(extra)}"


def test_each_invariant_has_must_clauses() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    for item in contract.get("invariants", []):
        must = item.get("must")
        assert isinstance(must, list) and must, (
            f"Invariant {item.get('id')} has no 'must' clauses."
        )


def test_padding_value_is_canonical_empty_sha256() -> None:
    """The canonical odd-leaf padding is sha256(b'') = the well-known
    e3b0c4... constant. This is the same value Bitcoin / Ethereum use
    in their Merkle-tree implementations. Hardcoding it here protects
    against an implementation that uses a different padding scheme
    (e.g., duplicating the last leaf, which is a different convention)."""
    empty_sha = hashlib.sha256(b"").hexdigest()
    assert empty_sha == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    # The MBH-004 must clauses should mention this value
    mbh004 = next(
        (i for i in contract["invariants"] if i["id"] == "MBH-004"), None
    )
    assert mbh004 is not None
    must_text = " ".join(mbh004.get("must", []))
    assert "e3b0c4" in must_text.lower(), (
        "MBH-004 must clauses MUST cite the canonical sha256(empty) value "
        "e3b0c4... so implementations cannot drift to alt padding schemes."
    )


def test_glued_to_progressive_replay_contract() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    deps = contract.get("depends_on_contracts", [])
    assert "progressive_replay_l0_l4.json" in deps
    assert PROGRESSIVE_REPLAY_CONTRACT.exists()


def test_proof_length_matches_batch_size_log() -> None:
    """Sanity: log2(batch_size) must equal the implied proof length.
    For batch_size=1024, log2=10. Encoded in MBH-006 must clauses."""
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    batch_size = contract["batch_size"]
    expected_log = batch_size.bit_length() - 1  # log2 for power of 2
    assert 2 ** expected_log == batch_size, (
        f"batch_size={batch_size} is not a power of 2; proof length math breaks."
    )
    assert expected_log == 10
    # Verify MBH-006 references this value
    mbh006 = next(
        (i for i in contract["invariants"] if i["id"] == "MBH-006"), None
    )
    must_text = " ".join(mbh006.get("must", []))
    assert "10" in must_text, (
        "MBH-006 must clauses MUST cite proof length = 10 (= log2(1024))."
    )


def test_contract_marks_out_of_scope() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    out_of_scope = contract.get("out_of_scope", [])
    assert isinstance(out_of_scope, list) and out_of_scope

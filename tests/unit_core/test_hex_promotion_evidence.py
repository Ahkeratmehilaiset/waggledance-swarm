# SPDX-License-Identifier: BUSL-1.1
"""Tests for the S5 hex shadow->candidate promotion-evidence channel (dormant, no authority).

Covers: honest 0->1 counting, the shadow-only invariant (ANY runtime-authority flag True
-> NOT a valid promotion), tamper/allowlist rejection, no raw topology in a record, and
the hash-chained ledger round-trip.
"""
from __future__ import annotations

import json

import pytest

from waggledance.core.hex_topology import hex_promotion_evidence as H

_TS = "2026-07-04T17:00:00Z"
_DIGEST = "sha256:" + "ab" * 32


def _ready_application(**overrides):
    app = {
        "application_digest": _DIGEST,
        "commit_candidate_prepared": True,
        "blockers": [],
        "parent_cell_id": "cell-0",
        "target_state": "subdivision_in_shadow",
        "commit_candidate_topology": {"raw": "SECRET topology payload"},  # must NEVER leak
        "live_runtime_commit_authorized": False,
        "runtime_authority_granted": False,
        "runtime_topology_mutation_applied": False,
        "routing_influence_applied": False,
        "transport_performed": False,
        "claim_safe_upgrade": False,
        "runtime_commit_performed": False,
    }
    app.update(overrides)
    return app


def _record(app=None, *, transition_id="t1", prev=H.GENESIS_PREV_HASH):
    return H.build_promotion_evidence_record(
        transition_id=transition_id, prev_hash=prev, ts_utc=_TS,
        commit_application=app if app is not None else _ready_application(),
    )


# --- honest 0 -> 1 ---------------------------------------------------------------
def test_empty_is_honest_zero() -> None:
    assert H.count_shadow_to_candidate_promotions([]) == 0


def test_clean_shadow_candidate_is_valid_and_counts_one() -> None:
    record = _record()
    assert H.is_valid_promotion_evidence(record) is True
    assert H.count_shadow_to_candidate_promotions([record]) == 1


# --- fail-closed on incomplete evidence ------------------------------------------
def test_not_prepared_is_not_counted() -> None:
    record = _record(_ready_application(commit_candidate_prepared=False))
    assert H.is_valid_promotion_evidence(record) is False
    assert H.count_shadow_to_candidate_promotions([record]) == 0


def test_blockers_present_is_not_counted() -> None:
    record = _record(_ready_application(blockers=["envelope_rehearsal_parent_match"]))
    assert record["blocker_count"] == 1
    assert H.is_valid_promotion_evidence(record) is False


# --- the SHADOW-ONLY INVARIANT: any runtime-authority flag True -> NOT a promotion --
@pytest.mark.parametrize("flag", list(H._RUNTIME_AUTHORITY_FLAGS))
def test_any_runtime_authority_flag_true_invalidates(flag: str) -> None:
    record = _record(_ready_application(**{flag: True}))
    # the invariant was violated (a live/authoritative action happened) -> NEVER a valid
    # shadow->candidate promotion-evidence; this channel does not weaken shadow-only.
    assert H.is_valid_promotion_evidence(record) is False
    assert H.count_shadow_to_candidate_promotions([record]) == 0


# --- tamper-evident + field allowlist --------------------------------------------
def test_tampered_field_without_rehash_is_invalid() -> None:
    record = dict(_record())
    record["blocker_count"] = 99                              # mutate, keep the old record_hash
    assert H.is_valid_promotion_evidence(record) is False


def test_smuggled_extra_key_is_invalid_even_if_self_hash_consistent() -> None:
    record = dict(_record())
    record["raw_leak"] = "SECRET"
    record[H._HASH_FIELD] = H.compute_record_hash(record)     # self-hash consistent...
    assert H.is_valid_promotion_evidence(record) is False     # ...but the allowlist rejects it


# --- no raw topology leak --------------------------------------------------------
def test_record_carries_only_digests_no_raw_topology() -> None:
    record = _record(_ready_application(commit_candidate_topology={"raw": "SECRET topology payload"}))
    blob = json.dumps(record)
    assert "SECRET topology payload" not in blob
    assert "commit_candidate_topology" not in record
    assert record["application_digest"] == _DIGEST           # only the digest is kept


# --- builder input validation ----------------------------------------------------
@pytest.mark.parametrize("bad_id", ["has space", "a/b", "", 5, None])
def test_build_rejects_malformed_transition_id(bad_id) -> None:
    with pytest.raises(H.PromotionEvidenceError):
        H.build_promotion_evidence_record(
            transition_id=bad_id, prev_hash=H.GENESIS_PREV_HASH, ts_utc=_TS,
            commit_application=_ready_application())


@pytest.mark.parametrize("bad_prev", ["not-a-hash", "sha256:" + "0" * 63, "sha256:" + "AB" * 32, None])
def test_build_rejects_malformed_prev_hash(bad_prev) -> None:
    with pytest.raises(H.PromotionEvidenceError):
        H.build_promotion_evidence_record(
            transition_id="t1", prev_hash=bad_prev, ts_utc=_TS,
            commit_application=_ready_application())


def test_build_rejects_missing_application_digest() -> None:
    with pytest.raises(H.PromotionEvidenceError):
        H.build_promotion_evidence_record(
            transition_id="t1", prev_hash=H.GENESIS_PREV_HASH, ts_utc=_TS,
            commit_application=_ready_application(application_digest="not-a-digest"))


# --- hash-chained ledger ---------------------------------------------------------
def test_chain_append_read_verify_and_count(tmp_path) -> None:
    path = str(tmp_path / "promotion_evidence.jsonl")
    r1 = _record(transition_id="t1")
    h1 = H.append_evidence(path, r1)
    r2 = _record(_ready_application(blockers=["x"]), transition_id="t2", prev=h1)  # invalid (blocker)
    H.append_evidence(path, r2)
    records = H.read_evidence(path)
    assert len(records) == 2 and H.verify_chain(records) is True
    assert H.head_hash(records) == r2["record_hash"]
    assert H.count_shadow_to_candidate_promotions(records) == 1   # honest: only the clean one counts


def test_chain_detects_a_broken_link() -> None:
    r1 = _record(transition_id="t1")
    r2 = _record(transition_id="t2", prev=H.GENESIS_PREV_HASH)     # wrong prev (not r1)
    assert H.verify_chain([r1, r2]) is False


# --- verifier shape enforcement (lead finding on #1507): a self-hash-consistent record
#     with a raw/malformed field in ANY position must NOT be valid or counted -----------
def _reforge(record: dict, **field_overrides) -> dict:
    """Mutate fields on a copy and RECOMPUTE record_hash so the record is
    self-hash-consistent -- exactly the lead's repro (the forger controls record_hash)."""
    forged = dict(record)
    forged.update(field_overrides)
    forged[H._HASH_FIELD] = H.compute_record_hash(forged)
    return forged


@pytest.mark.parametrize("field,bad_value", [
    ("parent_cell_id", "raw cell with spaces"),          # the lead's exact repro
    ("parent_cell_id", "cell/with/slashes"),
    ("parent_cell_id", "line\nbreak"),
    ("target_state", "raw state with spaces"),
    ("ts_utc", "not a timestamp with spaces"),
    ("prev_hash", "not-a-hash"),
    ("prev_hash", "sha256:" + "0" * 63),                 # wrong length
    ("transition_id", "has space"),
    ("application_digest", "not-a-digest"),
    ("schema_version", "some.other.schema.v0"),
])
def test_self_hash_consistent_record_with_malformed_field_is_rejected(field, bad_value) -> None:
    forged = _reforge(_record(), **{field: bad_value})
    assert forged[H._HASH_FIELD] == H.compute_record_hash(forged)   # forger IS self-consistent
    assert H.wellformed_reason(forged) is not None                  # ...but not well-formed
    assert H.is_valid_promotion_evidence(forged) is False
    assert H.count_shadow_to_candidate_promotions([forged]) == 0    # NOT counted
    with pytest.raises(H.PromotionEvidenceError):                    # durable-write refuses it
        import tempfile, os
        fd, p = tempfile.mkstemp(); os.close(fd)
        try:
            H.append_evidence(p, forged)
        finally:
            os.remove(p)


@pytest.mark.parametrize("field,bad_value", [
    ("commit_candidate_prepared", 1),                    # int, not bool (1 == True is a trap)
    ("blocker_count", True),                             # bool masquerading as int 1
    ("blocker_count", -1),                               # negative
])
def test_non_bool_scalar_shapes_are_rejected(field, bad_value) -> None:
    forged = _reforge(_record(), **{field: bad_value})
    assert H.is_valid_promotion_evidence(forged) is False
    assert H.wellformed_reason(forged) is not None


def test_wellformed_but_non_clean_record_is_persisted_but_not_counted(tmp_path) -> None:
    # a record with blockers is WELL-FORMED (honest record of a non-promotion) -> append
    # succeeds, verify_chain passes, but it is NOT counted. append must not over-reject.
    path = str(tmp_path / "evidence.jsonl")
    non_clean = _record(_ready_application(blockers=["parent_mismatch"]))
    assert H.wellformed_reason(non_clean) is None            # well-formed
    assert H.is_valid_promotion_evidence(non_clean) is False  # but not a clean promotion
    H.append_evidence(path, non_clean)                       # persisted (no raise)
    records = H.read_evidence(path)
    assert H.verify_chain(records) is True
    assert H.count_shadow_to_candidate_promotions(records) == 0


def test_verify_chain_rejects_a_malformed_record_in_chain() -> None:
    forged = _reforge(_record(), parent_cell_id="raw with spaces")
    assert H.verify_chain([forged]) is False                # malformed -> chain fails


# --- target_state SEMANTIC gate (lead #1509 review): well-formed token is not enough,
#     the value must BE the promotion target to be counted ------------------------------
def test_wrong_target_state_is_wellformed_but_not_a_counted_promotion() -> None:
    # a clean record whose target_state is a valid token but NOT the promotion target is
    # honest, well-formed evidence -- but NOT a shadow->candidate promotion (semantic gate).
    record = _record(_ready_application(target_state="subdivision_planned"))
    assert H.is_conforming_token("subdivision_planned")     # a valid token (well-formed)
    assert H.wellformed_reason(record) is None
    assert H.is_valid_promotion_evidence(record) is False    # value gate, not just shape
    assert H.count_shadow_to_candidate_promotions([record]) == 0


def test_forged_wrong_target_state_self_hash_consistent_is_not_counted() -> None:
    forged = _reforge(_record(), target_state="subdivision_planned")
    assert forged[H._HASH_FIELD] == H.compute_record_hash(forged)   # self-hash-consistent
    assert H.is_valid_promotion_evidence(forged) is False
    assert H.count_shadow_to_candidate_promotions([forged]) == 0


def test_promotion_target_state_matches_subdivision_runtime_commit_gate() -> None:
    # anti-drift: the counted target_state must equal what subdivision_runtime_commit
    # requires (it blocks any other value), so the counter can never fire on 0 real events.
    assert H.PROMOTION_TARGET_STATE == "subdivision_in_shadow"

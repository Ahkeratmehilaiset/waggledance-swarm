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


def _rehash(record):
    record[H._HASH_FIELD] = H.compute_record_hash(record)
    return record


# --- honest 0 -> 1 ---------------------------------------------------------------
def test_empty_is_honest_zero() -> None:
    assert H.count_shadow_to_candidate_promotions([]) == 0


def test_clean_shadow_candidate_is_valid_and_counts_one() -> None:
    record = _record()
    assert H.is_wellformed_promotion_evidence_record(record) is True
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
    assert H.is_wellformed_promotion_evidence_record(record) is True
    assert H.is_valid_promotion_evidence(record) is False


def test_non_shadow_target_state_is_wellformed_but_not_counted() -> None:
    record = _record(_ready_application(target_state="subdivision_planned"))
    assert H.is_wellformed_promotion_evidence_record(record) is True
    assert H.is_valid_promotion_evidence(record) is False
    assert H.count_shadow_to_candidate_promotions([record]) == 0


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


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("parent_cell_id", "RAW SECRET topology with spaces"),
        ("target_state", "RAW SECRET topology with spaces"),
        ("ts_utc", "raw timestamp with spaces"),
        ("prev_hash", "not-a-hash"),
        ("blocker_count", False),
        ("runtime_authority_flags", {"claim_safe_upgrade": False}),
    ],
)
def test_self_hash_consistent_malformed_shapes_are_invalid(field: str, value) -> None:
    record = _rehash(dict(_record(), **{field: value}))

    assert H.is_wellformed_promotion_evidence_record(record) is False
    assert H.is_valid_promotion_evidence(record) is False
    assert H.count_shadow_to_candidate_promotions([record]) == 0


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


def test_append_rejects_self_hash_consistent_raw_shape(tmp_path) -> None:
    path = tmp_path / "promotion_evidence.jsonl"
    record = _rehash(dict(_record(), parent_cell_id="RAW SECRET topology with spaces"))

    with pytest.raises(H.PromotionEvidenceError):
        H.append_evidence(str(path), record)

    assert not path.exists()


def test_append_keeps_wellformed_authority_violation_as_red_flag(tmp_path) -> None:
    path = str(tmp_path / "promotion_evidence.jsonl")
    record = _record(_ready_application(runtime_authority_granted=True))

    H.append_evidence(path, record)
    records = H.read_evidence(path)

    assert H.verify_chain(records) is True
    assert H.is_wellformed_promotion_evidence_record(records[0]) is True
    assert H.is_valid_promotion_evidence(records[0]) is False
    assert H.count_shadow_to_candidate_promotions(records) == 0


def test_chain_detects_a_broken_link() -> None:
    r1 = _record(transition_id="t1")
    r2 = _record(transition_id="t2", prev=H.GENESIS_PREV_HASH)     # wrong prev (not r1)
    assert H.verify_chain([r1, r2]) is False


def test_chain_rejects_self_hash_consistent_malformed_shape() -> None:
    record = _rehash(dict(_record(), ts_utc="raw timestamp with spaces"))

    assert H.verify_chain([record]) is False

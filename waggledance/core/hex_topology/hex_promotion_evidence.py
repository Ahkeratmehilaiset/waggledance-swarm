# SPDX-License-Identifier: BUSL-1.1
"""Dormant, offline evidence channel for hex shadow->candidate subdivision promotion (S5).

This RECORDS evidence of the hex shadow->candidate subdivision transition (from a
``subdivision_runtime_commit`` application receipt) into a separate, hash-chained,
tamper-evident ledger, and counts it HONESTLY (0 -> 1 only on real, complete
evidence). It is an OBSERVER: it has NO runtime authority, never mutates topology,
never promotes anything to authoritative, and never flips ``claim_safe`` -- exactly
like the chat-served claim-safety ledger measures coverage without flipping the claim.

The SHADOW-ONLY INVARIANT is preserved and, crucially, NOT weakened by this channel:
a promotion-evidence record counts as a VALID shadow->candidate preparation ONLY when
the commit candidate was prepared, had zero blockers, AND every runtime-authority flag
in the source receipt is False (no live commit authorized, no topology mutation, no
routing influence, no transport, no claim_safe upgrade). If ANY runtime-authority flag
is True the invariant was VIOLATED, so the record is INVALID (never counted, and a
red flag), not a valid promotion. Raw topology never enters a record -- only digests
and booleans. The actual promotion to AUTHORITATIVE stays a separate operator-gated
step (this channel only shows the honest count of prepared shadow candidates).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Mapping
from typing import Any

PROMOTION_EVIDENCE_SCHEMA = "magma.hex_shadow_to_candidate_promotion_evidence.v0"
# The only target_state that represents a valid shadow->candidate subdivision commit
# candidate -- subdivision_runtime_commit itself blocks any other target_state
# (subdivision_runtime_commit.py: `target_state != "subdivision_in_shadow"`). A
# well-formed record carrying a different target_state is honest evidence of a
# NON-promotion, so it is never counted (the count requires the value, not just a
# well-formed token). Credit: caught by codex-lead-1's #1509 review.
PROMOTION_TARGET_STATE = "subdivision_in_shadow"

_HASH_PREFIX = "sha256:"
_HASH_HEX_LEN = 64
GENESIS_PREV_HASH = _HASH_PREFIX + ("0" * _HASH_HEX_LEN)
_HASH_FIELD = "record_hash"

# A conforming, path/log-safe token (no whitespace, slash, or control chars).
_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")

# The source-receipt runtime-authority flags that MUST every be False for the
# shadow-only invariant to hold. A True on any of these means a live/authoritative
# action happened -> the transition is NOT a clean shadow->candidate preparation.
_RUNTIME_AUTHORITY_FLAGS = (
    "live_runtime_commit_authorized",
    "runtime_authority_granted",
    "runtime_topology_mutation_applied",
    "routing_influence_applied",
    "transport_performed",
    "claim_safe_upgrade",
    "runtime_commit_performed",
)

# Exactly the keys a well-formed record carries (no smuggled field can ride along --
# the field-allowlist discipline; a "valid" record provably holds only these).
_ALLOWED_KEYS = frozenset({
    "schema_version", "transition_id", "ts_utc", "prev_hash", "application_digest",
    "parent_cell_id", "target_state", "commit_candidate_prepared", "blocker_count",
    "runtime_authority_flags", _HASH_FIELD,
})


class PromotionEvidenceError(ValueError):
    """Malformed input to a promotion-evidence builder (fail-closed at construction)."""


def is_conforming_token(value: object) -> bool:
    return isinstance(value, str) and _TOKEN_RE.fullmatch(value) is not None


def is_evidence_hash(value: object) -> bool:
    if not isinstance(value, str) or not value.startswith(_HASH_PREFIX):
        return False
    hexpart = value[len(_HASH_PREFIX):]
    return len(hexpart) == _HASH_HEX_LEN and all(c in "0123456789abcdef" for c in hexpart)


def _canonical_bytes(record: Mapping[str, Any]) -> bytes:
    payload = {k: record[k] for k in record if k != _HASH_FIELD}
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def compute_record_hash(record: Mapping[str, Any]) -> str:
    return _HASH_PREFIX + hashlib.sha256(_canonical_bytes(record)).hexdigest()


def build_promotion_evidence_record(
    *,
    transition_id: str,
    prev_hash: str,
    ts_utc: str,
    commit_application: Mapping[str, Any],
) -> dict[str, Any]:
    """Extract the KEY evidence from a subdivision_runtime_commit application receipt
    into a hash-chained record. Only digests + booleans -- raw topology never enters."""
    if not is_conforming_token(transition_id):
        raise PromotionEvidenceError(f"transition_id is not a conforming token: {transition_id!r}")
    if not is_conforming_token(ts_utc):
        raise PromotionEvidenceError(f"ts_utc is not a conforming token: {ts_utc!r}")
    if not is_evidence_hash(prev_hash):
        raise PromotionEvidenceError(f"prev_hash is not a sha256 evidence hash: {prev_hash!r}")
    if not isinstance(commit_application, Mapping):
        raise PromotionEvidenceError("commit_application must be a mapping")
    application_digest = commit_application.get("application_digest")
    if not is_evidence_hash(application_digest):
        raise PromotionEvidenceError("commit_application.application_digest must be a sha256 digest")
    parent_cell_id = str(commit_application.get("parent_cell_id") or "unknown")
    if not is_conforming_token(parent_cell_id):
        parent_cell_id = "unknown"  # honest fallback, never raw
    target_state = str(commit_application.get("target_state") or "unknown")
    if not is_conforming_token(target_state):
        target_state = "unknown"
    record = {
        "schema_version": PROMOTION_EVIDENCE_SCHEMA,
        "transition_id": transition_id,
        "ts_utc": ts_utc,
        "prev_hash": prev_hash,
        "application_digest": application_digest,
        "parent_cell_id": parent_cell_id,
        "target_state": target_state,
        "commit_candidate_prepared": commit_application.get("commit_candidate_prepared") is True,
        "blocker_count": len(list(commit_application.get("blockers") or [])),
        "runtime_authority_flags": {
            flag: bool(commit_application.get(flag)) for flag in _RUNTIME_AUTHORITY_FLAGS
        },
    }
    record[_HASH_FIELD] = compute_record_hash(record)
    reason = wellformed_reason(record)                      # anti-drift builder self-check
    if reason is not None:
        raise PromotionEvidenceError(f"builder produced a malformed record: {reason}")
    return record


def wellformed_reason(record: object) -> str | None:
    """Return ``None`` iff ``record`` is a FULLY well-formed promotion-evidence record:
    exactly the allowed keys, correct schema, a tamper-consistent ``record_hash``, AND
    every field of the correct shape. Otherwise a short reason string.

    The verifier must ENFORCE the full field shape here -- it must never trust that the
    builder produced the record. A self-hash-consistent forged record (the caller can
    always recompute ``record_hash`` over its own fields) would otherwise smuggle a raw
    value through any un-checked field (e.g. a raw topology string in ``parent_cell_id``,
    or a malformed ``ts_utc`` / ``prev_hash``) and be counted. This is the
    producer!=verifier discipline: shape-check EVERY field, not a subset."""
    if not isinstance(record, Mapping):
        return "not_a_mapping"
    if set(record.keys()) != _ALLOWED_KEYS:                 # exact allowlist, no smuggled field
        return "key_set_mismatch"
    if record.get("schema_version") != PROMOTION_EVIDENCE_SCHEMA:
        return "schema_version"
    if not is_evidence_hash(record.get(_HASH_FIELD)):
        return "record_hash_shape"
    if record.get(_HASH_FIELD) != compute_record_hash(record):  # tamper-evident
        return "record_hash_mismatch"
    if not is_conforming_token(record.get("transition_id")):
        return "transition_id_shape"
    if not is_conforming_token(record.get("ts_utc")):
        return "ts_utc_shape"
    if not is_evidence_hash(record.get("prev_hash")):
        return "prev_hash_shape"
    if not is_evidence_hash(record.get("application_digest")):
        return "application_digest_shape"
    if not is_conforming_token(record.get("parent_cell_id")):  # blocks a raw value smuggled here
        return "parent_cell_id_shape"
    if not is_conforming_token(record.get("target_state")):
        return "target_state_shape"
    if not isinstance(record.get("commit_candidate_prepared"), bool):
        return "commit_candidate_prepared_shape"
    blocker_count = record.get("blocker_count")
    if not isinstance(blocker_count, int) or isinstance(blocker_count, bool) or blocker_count < 0:
        return "blocker_count_shape"
    flags = record.get("runtime_authority_flags")
    if not isinstance(flags, Mapping) or set(flags.keys()) != set(_RUNTIME_AUTHORITY_FLAGS):
        return "runtime_authority_flags_keys"
    if not all(isinstance(flags.get(flag), bool) for flag in _RUNTIME_AUTHORITY_FLAGS):
        return "runtime_authority_flags_values"
    return None


def is_wellformed_record(record: object) -> bool:
    """True iff every field is of the correct shape (full verifier enforcement)."""
    return wellformed_reason(record) is None


def is_valid_promotion_evidence(record: object) -> bool:
    """True iff ``record`` is a fully well-formed record that attests a CLEAN
    shadow->candidate preparation: prepared, zero blockers, and EVERY runtime-authority
    flag False (shadow-only invariant held). Fail-closed. Well-formedness (all field
    shapes) is enforced in full via ``wellformed_reason`` -- never trusted from the
    builder -- so a self-hash-consistent record with a raw/malformed field is rejected."""
    if not is_wellformed_record(record):
        return False
    flags = record["runtime_authority_flags"]
    # shadow-only invariant: EVERY runtime-authority flag must be exactly False; AND the
    # target_state must BE the promotion target (a well-formed record at a different
    # target_state is honest evidence but not a shadow->candidate promotion).
    return (
        record.get("commit_candidate_prepared") is True
        and record.get("blocker_count") == 0
        and record.get("target_state") == PROMOTION_TARGET_STATE
        and all(flags.get(flag) is False for flag in _RUNTIME_AUTHORITY_FLAGS)
    )


def count_shadow_to_candidate_promotions(records: list[Mapping[str, Any]]) -> int:
    """HONEST count (0 -> 1 only on real evidence): the number of records that attest a
    clean shadow->candidate preparation. Invalid/incomplete/invariant-violating records
    are NOT counted (fail-closed) -- so this never over-claims a promotion."""
    return sum(1 for record in records if is_valid_promotion_evidence(record))


# --- durable, hash-chained ledger (offline evidence channel) ----------------------
def append_evidence(ledger_path: str, record: Mapping[str, Any]) -> str:
    # The durable ledger holds ONLY well-formed records. A well-formed record may still be
    # a non-clean transition (blockers, or a runtime-authority flag True) -- that is an
    # honest record of a NON-promotion and is persisted; it simply is not counted.
    reason = wellformed_reason(record)
    if reason is not None:
        raise PromotionEvidenceError(f"refusing to append a malformed record: {reason}")
    line = (json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n").encode("ascii")
    flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_BINARY", 0)
    fd = os.open(ledger_path, flags, 0o644)
    try:
        remaining = line
        while remaining:
            remaining = remaining[os.write(fd, remaining):]
        os.fsync(fd)
    finally:
        os.close(fd)
    return record[_HASH_FIELD]


def read_evidence(ledger_path: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if not os.path.exists(ledger_path):
        return records
    with open(ledger_path, "rb") as handle:
        for raw in handle.read().split(b"\n"):
            if not raw.strip():
                continue
            try:
                records.append(json.loads(raw.decode("utf-8")))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue  # a corrupt line is simply not a valid record -> not counted (fail-closed)
    return records


def verify_chain(records: list[Mapping[str, Any]]) -> bool:
    """Every record fully well-formed AND prev_hash-linked from genesis (tamper-evident).
    A malformed record anywhere in the chain fails verification (fail-closed)."""
    prev = GENESIS_PREV_HASH
    for record in records:
        if not is_wellformed_record(record):               # full shape + tamper-consistency
            return False
        if record.get("prev_hash") != prev:
            return False
        prev = str(record.get(_HASH_FIELD))
    return True


def head_hash(records: list[Mapping[str, Any]]) -> str:
    return str(records[-1].get(_HASH_FIELD)) if records else GENESIS_PREV_HASH


__all__ = [
    "PROMOTION_EVIDENCE_SCHEMA", "GENESIS_PREV_HASH", "PromotionEvidenceError",
    "PROMOTION_TARGET_STATE",
    "is_conforming_token", "is_evidence_hash", "compute_record_hash",
    "build_promotion_evidence_record", "wellformed_reason", "is_wellformed_record",
    "is_valid_promotion_evidence", "count_shadow_to_candidate_promotions",
    "append_evidence", "read_evidence", "verify_chain", "head_hash",
]

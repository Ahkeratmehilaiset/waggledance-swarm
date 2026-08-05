# SPDX-License-Identifier: BUSL-1.1
"""Authority-free lifecycle contract for shared knowledge.

``KnowledgeStateV1`` is an append-only evidence chain.  It deliberately keeps a
raw observation local until independent, receipt-bound corroboration and a
separate verification receipt exist.  The contract carries no trust grant and
performs no storage, retrieval, training, clock, network, or model operation.

The state machine is intentionally small::

    observation_local -> quarantined | corroborated | revoked
    quarantined       -> corroborated | revoked
    corroborated      -> quarantined | verified | revoked
    verified          -> revoked
    revoked           -> observation_local  (explicit corrected content only)

Every transition is a new content-addressed event.  Revocation never deletes
history, and correction restarts locally from a changed content digest.  Even a
structurally ``verified`` event is evidence rather than a grant: global-use
admission requires the separate ``is_structurally_admissible`` re-binding check
against current heads and externally validated receipt identities.  Even that
check remains evidence for a higher authority-bearing admission layer.
"""

from __future__ import annotations

import re
from typing import Iterable, Mapping, Optional

from waggledance.core.magma.canonical import canonical_json_bytes, sha256_digest

SCHEMA_VERSION = "wd.knowledge_state.v1"
KNOWLEDGE_ID_DOMAIN = "wd.knowledge_state.knowledge_id.v1"
EVENT_DIGEST_DOMAIN = "wd.knowledge_state.event_digest.v1"

OBSERVATION_LOCAL = "observation_local"
QUARANTINED = "quarantined"
CORROBORATED = "corroborated"
VERIFIED = "verified"
REVOKED = "revoked"

STATES = frozenset(
    {OBSERVATION_LOCAL, QUARANTINED, CORROBORATED, VERIFIED, REVOKED}
)
ALLOWED_TRANSITIONS = {
    OBSERVATION_LOCAL: frozenset({QUARANTINED, CORROBORATED, REVOKED}),
    QUARANTINED: frozenset({CORROBORATED, REVOKED}),
    CORROBORATED: frozenset({QUARANTINED, VERIFIED, REVOKED}),
    VERIFIED: frozenset({REVOKED}),
    REVOKED: frozenset({OBSERVATION_LOCAL}),
}

MAX_CORROBORATORS = 32
MAX_HISTORY_EVENTS = 256
MAX_REVISION = 2**53 - 1
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")

CORROBORATOR_KEYS = frozenset(
    {"identity_digest", "lineage_digest", "receipt_digest"}
)
EVENT_CORE_KEYS = frozenset(
    {
        "schema_version",
        "knowledge_id",
        "revision",
        "state",
        "previous_event_digest",
        "claim_key_digest",
        "content_digest",
        "source_identity_digest",
        "source_lineage_digest",
        "corroborators",
        "evidence_head_digest",
        "verification_receipt_digest",
        "policy_head_digest",
        "reason_digest",
    }
)
EVENT_KEYS = EVENT_CORE_KEYS | {"event_digest"}


class KnowledgeStateError(ValueError):
    """The supplied value is outside the KnowledgeStateV1 contract."""


def _wire_dict(value: object, label: str, *, maximum_keys: int) -> dict:
    if type(value) is not dict:
        raise KnowledgeStateError(f"{label} must be an exact dict")
    # Exact ``dict`` length is O(1).  Reject an oversized hostile object before
    # copying it so the wire boundary itself remains bounded.
    if len(value) > maximum_keys:
        raise KnowledgeStateError(f"{label} keyset")
    snapshot = value.copy()
    if any(type(key) is not str for key in snapshot):
        raise KnowledgeStateError(f"{label} keys must be exact strings")
    return snapshot


def _digest(value: object, label: str, *, optional: bool = False) -> Optional[str]:
    if optional and value is None:
        return None
    if type(value) is not str or not _SHA256.fullmatch(value):
        raise KnowledgeStateError(f"{label} must be a sha256:<64 hex> digest")
    return value


def _revision(value: object) -> int:
    if type(value) is not int or not 0 <= value <= MAX_REVISION:
        raise KnowledgeStateError("revision must be a bounded non-negative integer")
    return value


def derive_knowledge_id(claim_key_digest: str) -> str:
    """Derive the stable identity of one claim independently of its content."""

    _digest(claim_key_digest, "claim_key_digest")
    return sha256_digest(
        {
            "domain": KNOWLEDGE_ID_DOMAIN,
            "schema_version": SCHEMA_VERSION,
            "claim_key_digest": claim_key_digest,
        }
    )


def _normalize_corroborators(
    values: Iterable[Mapping[str, object]],
    *,
    source_lineage_digest: str,
    require_canonical_order: bool = False,
) -> list[dict[str, str]]:
    if type(values) not in (list, tuple):
        raise KnowledgeStateError("corroborators must be an exact list or tuple")
    if len(values) > MAX_CORROBORATORS:
        raise KnowledgeStateError("corroborators exceeds the bounded maximum")
    # Slice before copying.  This stays bounded even if another thread grows an
    # exact list between the O(1) length check and this snapshot operation.
    snapshot = list(values[: MAX_CORROBORATORS + 1])
    if len(snapshot) > MAX_CORROBORATORS:
        raise KnowledgeStateError("corroborators exceeds the bounded maximum")

    normalized: list[dict[str, str]] = []
    identities: set[str] = set()
    lineages: set[str] = set()
    receipts: set[str] = set()
    for index, raw in enumerate(snapshot):
        item = _wire_dict(
            raw,
            f"corroborators[{index}]",
            maximum_keys=len(CORROBORATOR_KEYS),
        )
        if set(item) != CORROBORATOR_KEYS:
            raise KnowledgeStateError(f"corroborators[{index}] keyset")
        identity = _digest(item["identity_digest"], "corroborator.identity_digest")
        lineage = _digest(item["lineage_digest"], "corroborator.lineage_digest")
        receipt = _digest(item["receipt_digest"], "corroborator.receipt_digest")
        assert identity is not None and lineage is not None and receipt is not None
        if lineage == source_lineage_digest:
            raise KnowledgeStateError("a mirrored source lineage cannot corroborate itself")
        if identity in identities:
            raise KnowledgeStateError("duplicate corroborator identity")
        if lineage in lineages:
            raise KnowledgeStateError("duplicate corroborator lineage")
        if receipt in receipts:
            raise KnowledgeStateError("duplicate corroborator receipt")
        identities.add(identity)
        lineages.add(lineage)
        receipts.add(receipt)
        normalized.append(
            {
                "identity_digest": identity,
                "lineage_digest": lineage,
                "receipt_digest": receipt,
            }
        )
    canonical = sorted(normalized, key=canonical_json_bytes)
    if require_canonical_order and snapshot != canonical:
        raise KnowledgeStateError("corroborators must use canonical order")
    return canonical


def _state_requirements(core: Mapping[str, object]) -> None:
    state = core["state"]
    corroborators = core["corroborators"]
    evidence_head = core["evidence_head_digest"]
    verification_receipt = core["verification_receipt_digest"]

    if state in {OBSERVATION_LOCAL, QUARANTINED, REVOKED}:
        if corroborators or evidence_head is not None or verification_receipt is not None:
            raise KnowledgeStateError(
                f"{state} must not carry corroborators or evidence receipts"
            )
        return

    if state in {CORROBORATED, VERIFIED}:
        if len(corroborators) < 2:  # type: ignore[arg-type]
            raise KnowledgeStateError(
                f"{state} requires two independent corroborator lineages"
            )
        if evidence_head is None:
            raise KnowledgeStateError(f"{state} requires an evidence head digest")
    if state == CORROBORATED and verification_receipt is not None:
        raise KnowledgeStateError(
            "corroborated must not carry a verification receipt"
        )
    if state == VERIFIED:
        if verification_receipt is None:
            raise KnowledgeStateError("verified requires a verification receipt")
        receipts = {item["receipt_digest"] for item in corroborators}  # type: ignore[union-attr]
        if verification_receipt in receipts:
            raise KnowledgeStateError(
                "verification receipt must be independent of corroborator receipts"
            )

def _normalize_core(value: object) -> dict:
    core = _wire_dict(
        value,
        "knowledge event core",
        maximum_keys=len(EVENT_CORE_KEYS),
    )
    if set(core) != EVENT_CORE_KEYS:
        raise KnowledgeStateError("knowledge event core keyset")
    if type(core["schema_version"]) is not str or core["schema_version"] != SCHEMA_VERSION:
        raise KnowledgeStateError("schema_version refused")
    if type(core["state"]) is not str or core["state"] not in STATES:
        raise KnowledgeStateError("state refused")

    revision = _revision(core["revision"])
    previous = _digest(
        core["previous_event_digest"], "previous_event_digest", optional=True
    )
    if revision == 0 and previous is not None:
        raise KnowledgeStateError("revision zero must not have a previous event")
    if revision > 0 and previous is None:
        raise KnowledgeStateError("non-initial revision requires a previous event")

    claim_key = _digest(core["claim_key_digest"], "claim_key_digest")
    knowledge_id = _digest(core["knowledge_id"], "knowledge_id")
    assert claim_key is not None and knowledge_id is not None
    if knowledge_id != derive_knowledge_id(claim_key):
        raise KnowledgeStateError("knowledge_id mismatch")

    content = _digest(core["content_digest"], "content_digest")
    source_identity = _digest(
        core["source_identity_digest"], "source_identity_digest"
    )
    source_lineage = _digest(
        core["source_lineage_digest"], "source_lineage_digest"
    )
    policy_head = _digest(core["policy_head_digest"], "policy_head_digest")
    reason = _digest(core["reason_digest"], "reason_digest")
    evidence_head = _digest(
        core["evidence_head_digest"], "evidence_head_digest", optional=True
    )
    verification_receipt = _digest(
        core["verification_receipt_digest"],
        "verification_receipt_digest",
        optional=True,
    )
    assert (
        content is not None
        and source_identity is not None
        and source_lineage is not None
        and policy_head is not None
        and reason is not None
    )
    corroborators = _normalize_corroborators(
        core["corroborators"],
        source_lineage_digest=source_lineage,
        require_canonical_order=True,
    )
    normalized = {
        "schema_version": SCHEMA_VERSION,
        "knowledge_id": knowledge_id,
        "revision": revision,
        "state": core["state"],
        "previous_event_digest": previous,
        "claim_key_digest": claim_key,
        "content_digest": content,
        "source_identity_digest": source_identity,
        "source_lineage_digest": source_lineage,
        "corroborators": corroborators,
        "evidence_head_digest": evidence_head,
        "verification_receipt_digest": verification_receipt,
        "policy_head_digest": policy_head,
        "reason_digest": reason,
    }
    _state_requirements(normalized)
    return normalized


def derive_event_digest(core: object) -> str:
    """Re-derive the content address of an already-normalized event core."""

    normalized = _normalize_core(core)
    return sha256_digest(
        {
            "domain": EVENT_DIGEST_DOMAIN,
            "schema_version": SCHEMA_VERSION,
            "event": normalized,
        }
    )


def _build_event(core: dict) -> dict:
    normalized = _normalize_core(core)
    return {**normalized, "event_digest": derive_event_digest(normalized)}


def build_initial_knowledge_state(
    *,
    claim_key_digest: str,
    content_digest: str,
    source_identity_digest: str,
    source_lineage_digest: str,
    policy_head_digest: str,
    reason_digest: str,
) -> dict:
    """Build a local-only initial observation.  It can never be globally used."""

    return _build_event(
        {
            "schema_version": SCHEMA_VERSION,
            "knowledge_id": derive_knowledge_id(claim_key_digest),
            "revision": 0,
            "state": OBSERVATION_LOCAL,
            "previous_event_digest": None,
            "claim_key_digest": claim_key_digest,
            "content_digest": content_digest,
            "source_identity_digest": source_identity_digest,
            "source_lineage_digest": source_lineage_digest,
            "corroborators": [],
            "evidence_head_digest": None,
            "verification_receipt_digest": None,
            "policy_head_digest": policy_head_digest,
            "reason_digest": reason_digest,
        }
    )


def build_knowledge_transition(
    *,
    previous: object,
    new_state: str,
    reason_digest: str,
    corroborators: Optional[Iterable[Mapping[str, object]]] = None,
    evidence_head_digest: Optional[str] = None,
    verification_receipt_digest: Optional[str] = None,
    policy_head_digest: Optional[str] = None,
    corrected_content_digest: Optional[str] = None,
) -> dict:
    """Build one valid next event and verify its relation to ``previous``."""

    prior = parse_knowledge_state(previous)
    if type(new_state) is not str or new_state not in ALLOWED_TRANSITIONS[prior["state"]]:
        raise KnowledgeStateError(
            f"transition {prior['state']} -> {new_state!r} is not allowed"
        )

    is_correction = prior["state"] == REVOKED and new_state == OBSERVATION_LOCAL
    if is_correction:
        if (
            corroborators is not None
            or evidence_head_digest is not None
            or verification_receipt_digest is not None
        ):
            raise KnowledgeStateError(
                "correction must not carry corroborators or evidence receipts"
            )
        corrected = _digest(corrected_content_digest, "corrected_content_digest")
        if corrected == prior["content_digest"]:
            raise KnowledgeStateError("correction must change the content digest")
        next_content = corrected
        next_corroborators: Iterable[Mapping[str, object]] = []
        next_evidence_head = None
        next_verification_receipt = None
    elif new_state in {QUARANTINED, REVOKED}:
        if corrected_content_digest is not None:
            raise KnowledgeStateError("content may change only in a revoked correction")
        if (
            corroborators is not None
            or evidence_head_digest is not None
            or verification_receipt_digest is not None
        ):
            raise KnowledgeStateError(
                f"{new_state} must not carry corroborators or evidence receipts"
            )
        next_content = prior["content_digest"]
        next_corroborators = []
        next_evidence_head = None
        next_verification_receipt = None
    else:
        if corrected_content_digest is not None:
            raise KnowledgeStateError("content may change only in a revoked correction")
        next_content = prior["content_digest"]
        next_corroborators = (
            prior["corroborators"] if corroborators is None else corroborators
        )
        next_evidence_head = (
            prior["evidence_head_digest"]
            if evidence_head_digest is None
            else evidence_head_digest
        )
        next_verification_receipt = (
            prior["verification_receipt_digest"]
            if verification_receipt_digest is None
            else verification_receipt_digest
        )

    next_policy_head = (
        prior["policy_head_digest"]
        if policy_head_digest is None
        else policy_head_digest
    )
    normalized_corroborators = _normalize_corroborators(
        next_corroborators,
        source_lineage_digest=prior["source_lineage_digest"],
    )
    event = _build_event(
        {
            "schema_version": SCHEMA_VERSION,
            "knowledge_id": prior["knowledge_id"],
            "revision": prior["revision"] + 1,
            "state": new_state,
            "previous_event_digest": prior["event_digest"],
            "claim_key_digest": prior["claim_key_digest"],
            "content_digest": next_content,
            "source_identity_digest": prior["source_identity_digest"],
            "source_lineage_digest": prior["source_lineage_digest"],
            "corroborators": normalized_corroborators,
            "evidence_head_digest": next_evidence_head,
            "verification_receipt_digest": next_verification_receipt,
            "policy_head_digest": next_policy_head,
            "reason_digest": reason_digest,
        }
    )
    ok, reason = verify_knowledge_transition(prior, event)
    if not ok:
        raise KnowledgeStateError(f"invalid transition: {reason}")
    return event


def parse_knowledge_state(value: object) -> dict:
    """Validate and return a private normalized copy of one event."""

    event = _wire_dict(
        value,
        "knowledge event",
        maximum_keys=len(EVENT_KEYS),
    )
    if set(event) != EVENT_KEYS:
        raise KnowledgeStateError("knowledge event keyset")
    core = {key: event[key] for key in EVENT_CORE_KEYS}
    normalized = _normalize_core(core)
    stored = _digest(event["event_digest"], "event_digest")
    expected = derive_event_digest(normalized)
    if stored != expected:
        raise KnowledgeStateError("event_digest mismatch")
    return {**normalized, "event_digest": stored}


def verify_knowledge_state(value: object) -> tuple[bool, Optional[str]]:
    try:
        parse_knowledge_state(value)
    except KnowledgeStateError as exc:
        return False, str(exc)
    return True, None


def verify_knowledge_transition(
    previous: object, current: object
) -> tuple[bool, Optional[str]]:
    """Verify exact adjacency, monotonicity and evidence preservation."""

    try:
        prior = parse_knowledge_state(previous)
        nxt = parse_knowledge_state(current)
    except KnowledgeStateError as exc:
        return False, str(exc)

    if nxt["knowledge_id"] != prior["knowledge_id"]:
        return False, "knowledge_id changed"
    if nxt["claim_key_digest"] != prior["claim_key_digest"]:
        return False, "claim_key_digest changed"
    if nxt["source_identity_digest"] != prior["source_identity_digest"]:
        return False, "source_identity_digest changed"
    if nxt["source_lineage_digest"] != prior["source_lineage_digest"]:
        return False, "source_lineage_digest changed"
    if nxt["revision"] != prior["revision"] + 1:
        return False, "revision is not exactly monotonic"
    if nxt["previous_event_digest"] != prior["event_digest"]:
        return False, "previous_event_digest is stale or mismatched"
    if nxt["state"] not in ALLOWED_TRANSITIONS[prior["state"]]:
        return False, f"transition {prior['state']} -> {nxt['state']} is not allowed"

    is_correction = prior["state"] == REVOKED and nxt["state"] == OBSERVATION_LOCAL
    clears_evidence = nxt["state"] in {QUARANTINED, REVOKED}
    if is_correction:
        if nxt["content_digest"] == prior["content_digest"]:
            return False, "correction did not change content"
        if (
            nxt["corroborators"]
            or nxt["evidence_head_digest"] is not None
            or nxt["verification_receipt_digest"] is not None
        ):
            return False, "correction must restart without inherited evidence"
    elif clears_evidence:
        if nxt["content_digest"] != prior["content_digest"]:
            return False, "content changed outside correction"
        if (
            nxt["corroborators"]
            or nxt["evidence_head_digest"] is not None
            or nxt["verification_receipt_digest"] is not None
        ):
            return False, f"{nxt['state']} did not clear evidence"
    else:
        if nxt["content_digest"] != prior["content_digest"]:
            return False, "content changed outside correction"

        # Evidence-bearing state can advance to verified only under the exact
        # heads and corroborator set that were corroborated.  Any policy,
        # evidence, or provenance change must first enter quarantine (which
        # clears evidence) and obtain fresh corroboration.
        if prior["state"] == CORROBORATED and nxt["state"] == VERIFIED:
            if nxt["policy_head_digest"] != prior["policy_head_digest"]:
                return False, "policy head changed without fresh corroboration"
            if nxt["evidence_head_digest"] != prior["evidence_head_digest"]:
                return False, "evidence head changed without fresh corroboration"
            if nxt["corroborators"] != prior["corroborators"]:
                return False, "corroborators changed without fresh corroboration"
        else:
            old_evidence = {
                tuple(item.values()) for item in prior["corroborators"]
            }
            new_evidence = {
                tuple(item.values()) for item in nxt["corroborators"]
            }
            if not old_evidence.issubset(new_evidence):
                return False, "transition discarded corroborator evidence"

    return True, None


def verify_knowledge_history(
    events: object,
) -> tuple[bool, Optional[str]]:
    """Verify one bounded, gap-free chain from its local observation root."""

    if type(events) not in (list, tuple):
        return False, "knowledge history must be an exact list or tuple"
    if not 1 <= len(events) <= MAX_HISTORY_EVENTS:
        return False, "knowledge history must be non-empty and bounded"
    snapshot = list(events[: MAX_HISTORY_EVENTS + 1])
    if not 1 <= len(snapshot) <= MAX_HISTORY_EVENTS:
        return False, "knowledge history must be non-empty and bounded"
    try:
        parsed = [parse_knowledge_state(event) for event in snapshot]
    except KnowledgeStateError as exc:
        return False, str(exc)
    root = parsed[0]
    if (
        root["revision"] != 0
        or root["previous_event_digest"] is not None
        or root["state"] != OBSERVATION_LOCAL
    ):
        return False, "knowledge history root is not an initial local observation"
    for index in range(1, len(parsed)):
        ok, reason = verify_knowledge_transition(parsed[index - 1], parsed[index])
        if not ok:
            return False, f"knowledge history link {index} invalid: {reason}"
    return True, None


def is_structurally_admissible(
    value: object,
    *,
    validated_history: object,
    expected_policy_head_digest: str,
    expected_evidence_head_digest: str,
    expected_current_event_digest: str,
    expected_current_revision: int,
    validated_corroborators: Iterable[Mapping[str, object]],
    validated_verification_receipt_digest: str,
) -> bool:
    """Re-bind a verified event to current, externally validated evidence.

    This is a structural admission check, not authentication or an authority
    grant.  The caller must first run the relevant receipt verifiers and obtain
    ``expected_current_*`` from the authoritative monotonic knowledge-head
    registry.  Caller-selected digests remain self-claims and MUST NOT be used
    to authorize global retrieval, training, or writes.  The deliberately
    narrow name prevents structural validity from masquerading as permission.
    """

    try:
        event = parse_knowledge_state(value)
        expected_policy = _digest(
            expected_policy_head_digest, "expected_policy_head_digest"
        )
        expected_evidence = _digest(
            expected_evidence_head_digest, "expected_evidence_head_digest"
        )
        expected_current_event = _digest(
            expected_current_event_digest, "expected_current_event_digest"
        )
        expected_revision = _revision(expected_current_revision)
        validated_verification = _digest(
            validated_verification_receipt_digest,
            "validated_verification_receipt_digest",
        )
        corroborators = _normalize_corroborators(
            validated_corroborators,
            source_lineage_digest=event["source_lineage_digest"],
        )
    except KnowledgeStateError:
        return False
    if type(validated_history) not in (list, tuple):
        return False
    if not 1 <= len(validated_history) <= MAX_HISTORY_EVENTS:
        return False
    history = list(validated_history[: MAX_HISTORY_EVENTS + 1])
    history_ok, _ = verify_knowledge_history(history)
    if not history_ok:
        return False
    try:
        history_tip = parse_knowledge_state(history[-1])
    except KnowledgeStateError:
        return False
    return bool(
        event["state"] == VERIFIED
        and event["event_digest"] == history_tip["event_digest"]
        and event["event_digest"] == expected_current_event
        and event["revision"] == history_tip["revision"]
        and event["revision"] == expected_revision
        and event["policy_head_digest"] == expected_policy
        and event["evidence_head_digest"] == expected_evidence
        and event["corroborators"] == corroborators
        and event["verification_receipt_digest"] == validated_verification
    )

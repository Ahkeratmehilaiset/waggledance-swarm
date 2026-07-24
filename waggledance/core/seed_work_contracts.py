# SPDX-License-Identifier: BUSL-1.1
"""W2B SeedWork contracts -- advisory, fail-closed seed-work envelope/result.

A ``SeedWorkEnvelopeV1`` binds ONE bounded PDAM close request to a cell's
Genesis identity (W2A ``CellIdentityV1``) and a bounded root-to-current lineage
proof (W2A ``GenesisLineageV1``), plus the pinned solver identity and its
internally-recomputed contract/config digests, and an immutable registry
snapshot generation + head digest. A ``SeedWorkResultV1`` records the pinned
solver's deterministic advisory actions. Both are content-addressed and carry
NO authority: they are evidence, not grants (Law 7). Verifiers recompute every
digest; a stored/claimed digest is never trusted.

Registry input policy (post-W2A ``caller_owned_quiescent.v1``). These verifiers
validate DECODED Python objects and require STABLE, non-self-mutating inputs.
The multi-object registry snapshot is NOT atomically snapshotted here: a caller
must acquire ONE immutable canonical aggregate under the writer's lock/
transaction (or an atomic deeply-immutable copy-on-write) BEFORE calling, and
bind that snapshot's opaque never-reused ``generation`` plus whole-registry
``head_digest``. A per-object copy stabilizes later reads within a single call
but does not manufacture a linearizable view across independently mutable
objects; this module makes no such atomicity claim.

No clock, no I/O, no randomness, no runtime/capability registration, no MAGMA
authenticity claim: ``as_of_utc`` is an input and every digest is replayable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from waggledance.core.magma.canonical import canonical_json_bytes, sha256_digest
from waggledance.core.pdam_close_solver import (
    LIMITED_STATES as _SOLVER_LIMITED_STATES,
    OK_LIKE_STATES as _SOLVER_OK_LIKE_STATES,
    OPEN_STATUSES as _SOLVER_OPEN_STATUSES,
)

# --- schema + digest domain tags -------------------------------------------
ENVELOPE_SCHEMA = "wd.seed_work_envelope.v1"
RESULT_SCHEMA = "wd.seed_work_result.v1"
EVIDENCE_SCHEMA = "wd.seed_work_evidence.v1"
REQUEST_SCHEMA = "wd.seed_work_request.v1"
SNAPSHOT_SCHEMA = "wd.seed_work_registry_snapshot.v1"
ACTION_SCHEMA = "wd.seed_work_action.v1"

ENVELOPE_DIGEST_DOMAIN = "wd.seed_work_envelope.digest.v1"
RESULT_DIGEST_DOMAIN = "wd.seed_work_result.digest.v1"
EVIDENCE_DIGEST_DOMAIN = "wd.seed_work_evidence.digest.v1"
REQUEST_DIGEST_DOMAIN = "wd.seed_work_request.digest.v1"
SNAPSHOT_HEAD_DOMAIN = "wd.seed_work_registry_snapshot.head.v1"
SOLVER_CONTRACT_DOMAIN = "wd.seed_work_solver_contract.digest.v1"
SOLVER_CONFIG_DOMAIN = "wd.seed_work_solver_config.digest.v1"

SOLVER_ID = "wd.pdam_close.v1"
ACTION_SCHEMA_VERSION = "wd.pdam_close_action.v1"

# Post-W2A relational input contract (see module docstring). A pure decoded
# object verifier cannot coordinate external writers; the caller owns quiescence.
RELATIONAL_INPUT_POLICY = "caller_owned_quiescent.v1"

# --- pinned bounds (fail-closed, never truncate) ---------------------------
MAX_ENVELOPE_BYTES = 4_194_304
MAX_RESULT_BYTES = 4_194_304
MAX_REQUEST_BYTES = 2_097_152
MAX_ENTRIES = 512
MAX_TIMELINE = 2_048
MAX_TOOL_STATES = 2_048
MAX_COMMENTS = 4_096
MAX_SUBTOOL_GROUPS = 512
MAX_CHILDREN_PER_PARENT = 64
MAX_LINEAGE_PROOF = 256
MAX_IDENTITIES = 4_096
# Snapshot resource bounds (fail-closed, checked BEFORE any per-member crypto
# verification or head-digest hashing). ``generation`` is an opaque never-reused
# token, so a bounded length is sufficient; the aggregate byte cap stops an
# under-count but over-size registry image from forcing expensive work.
MAX_GENERATION_LEN = 256
MAX_SNAPSHOT_BYTES = 8_388_608
DUP_WINDOW_MIN, DUP_WINDOW_MAX = 0, 744
EVIDENCE_LIMIT_MIN, EVIDENCE_LIMIT_MAX = 0, 32
OPEN_WINDOW_MIN, OPEN_WINDOW_MAX = 1, 366

_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
# ASCII-only ISO-8601 UTC-Z; 1..6 fractional digits (no trailing zero enforced
# separately). Unicode digits would pass \d but are a different lexeme.
_UTC_Z = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?Z$"
)

# The request-composition allowlists are DERIVED from the pinned solver's own
# behavioural sets -- never re-declared as independent literals here -- so
# ``derive_solver_contract_digest`` pins the ACTUAL pdam_close_solver behaviour:
# if the solver's open-status / tool-state vocabulary drifts, both the accepted
# request surface AND the contract digest move with it. A duplicated literal copy
# silently let the two diverge (a contract digest that no longer described what
# the solver would actually do). See ``test_solver_allowlist_conformance``.
_STATUS_ALLOWLIST = frozenset(_SOLVER_OPEN_STATUSES)
_TOOL_STATE_ALLOWLIST = frozenset(_SOLVER_OK_LIKE_STATES | _SOLVER_LIMITED_STATES)

ENVELOPE_KEYS = frozenset({
    "schema_version", "envelope_id", "identity", "lineage", "parent_lineage",
    "lineage_proof", "lineage_proof_digest", "registry_generation",
    "registry_head_digest", "solver_id", "solver_contract_digest",
    "solver_config_digest", "request_payload", "as_of_utc", "request_digest",
})
RESULT_KEYS = frozenset({
    "schema_version", "envelope_id", "cell_id", "lineage_entry_hash",
    "solver_id", "solver_contract_digest", "solver_config_digest",
    "request_digest", "registry_generation", "registry_head_digest",
    "as_of_utc", "action_schema_version", "actions", "result_digest",
    "evidence_digest", "advisory_only", "external_writes_applied",
})
REQUEST_KEYS = frozenset({
    "schema_version", "entries", "repair_timeline", "tool_states",
    "comments", "subtools", "options",
})
SNAPSHOT_KEYS = frozenset({
    "schema_version", "generation", "identities", "lineage_entries",
    "head_digest",
})


class SeedWorkContractError(ValueError):
    """The value is outside a SeedWork v1 contract."""


# --- exact-container discipline (reused from W2A) --------------------------
def _wire_dict(value: object) -> Optional[dict]:
    """Require an EXACT built-in ``dict`` (reject arbitrary Mapping AND dict
    subclasses without invoking any overridden protocol), copy once, and
    require exact-str keys. Returns the plain-dict copy or None."""
    if type(value) is not dict:
        return None
    snapshot = value.copy()
    for key in snapshot:
        if type(key) is not str:
            return None
    return snapshot


def _wire_list(value: object) -> Optional[list]:
    """Require an EXACT built-in ``list`` or ``tuple`` (reject Sequence
    subclasses/arbitrary Sequences without invoking their protocol) and copy
    once into a private list."""
    if type(value) is not list and type(value) is not tuple:
        return None
    return list(value)


def _req_str(value: object, label: str) -> str:
    if type(value) is not str:
        raise SeedWorkContractError(f"{label} must be an exact str")
    return value


def _req_sha(value: object, label: str) -> str:
    if type(value) is not str or not _SHA256.fullmatch(value):
        raise SeedWorkContractError(f"{label} must be a sha256:<64 hex> digest")
    return value


def _req_int(value: object, label: str, *, lo: int, hi: int) -> int:
    # EXACT int (reject bool -- an int subclass) within [lo, hi].
    if type(value) is not int:
        raise SeedWorkContractError(f"{label} must be an exact int")
    if not lo <= value <= hi:
        raise SeedWorkContractError(f"{label} must be within {lo}..{hi}")
    return value


def _req_bool(value: object, label: str) -> bool:
    if type(value) is not bool:
        raise SeedWorkContractError(f"{label} must be an exact bool")
    return value


def parse_utc(value: object, label: str) -> datetime:
    """Parse a canonical ASCII UTC-Z instant to an AWARE UTC datetime.

    Shape (regex), calendar reality (strptime), a single canonical lexeme (no
    trailing-zero fractional part), and no ambient clock. Fail-closed."""
    if type(value) is not str or not _UTC_Z.fullmatch(value):
        raise SeedWorkContractError(f"{label} must be canonical ISO-8601 UTC-Z")
    if "." in value and value[value.index(".") + 1 : -1].endswith("0"):
        raise SeedWorkContractError(
            f"{label} fractional seconds must not have trailing zeros"
        )
    try:
        base = datetime.strptime(value[:19], "%Y-%m-%dT%H:%M:%S")
    except ValueError as exc:
        raise SeedWorkContractError(
            f"{label} must be a real calendar instant"
        ) from exc
    micros = 0
    if "." in value:
        frac = value[value.index(".") + 1 : -1]
        micros = int(frac.ljust(6, "0"))
    return base.replace(microsecond=micros, tzinfo=timezone.utc)


def _canon_time(dt: datetime) -> str:
    """Render an aware-UTC datetime back to the single canonical UTC-Z lexeme
    (used only to re-derive digests deterministically)."""
    if dt.tzinfo is None or dt.utcoffset() != timezone.utc.utcoffset(None):
        raise SeedWorkContractError("timestamp must be aware UTC")
    stamp = dt.strftime("%Y-%m-%dT%H:%M:%S")
    if dt.microsecond:
        stamp += "." + f"{dt.microsecond:06d}".rstrip("0")
    return stamp + "Z"


def _bounded_bytes(payload: object, cap: int, label: str) -> None:
    """Reject an over-cap raw structure by its canonical byte length BEFORE any
    heavy Python-level processing. Never truncate."""
    try:
        size = len(canonical_json_bytes(payload))
    except Exception as exc:  # non-serializable => reject, no partial result
        raise SeedWorkContractError(f"{label} is not canonical-serializable") from exc
    if size > cap:
        raise SeedWorkContractError(f"{label} exceeds {cap} canonical bytes")


# --- registry snapshot (post-W2A amendment aggregate) ----------------------
def parse_registry_snapshot(value: object) -> dict:
    """Validate ONE immutable canonical registry snapshot aggregate.

    Exact keyset {schema_version, generation, identities, lineage_entries,
    head_digest}. ``generation`` is an opaque exact str (never reused by the
    writer; an A->B->A revert receives a NEW generation). ``head_digest`` is the
    recomputed whole-registry digest. Every identity self-verifies (unique
    cell_id) and the lineage_entries must form ONE closed rooted tree
    (``verify_lineage_registry``: unique cell_id, exactly one root, no orphans,
    every child link proven) -- not a bag of disconnected valid entries. Returns
    a private normalized copy; any shape/type/bound/closure failure rejects (no
    fallback)."""
    snap = _wire_dict(value)
    if snap is None:
        raise SeedWorkContractError("registry snapshot must be an exact dict")
    if set(snap.keys()) != SNAPSHOT_KEYS:
        raise SeedWorkContractError("registry snapshot keyset")
    if _req_str(snap["schema_version"], "snapshot.schema_version") != SNAPSHOT_SCHEMA:
        raise SeedWorkContractError("registry snapshot schema_version refused")
    # Aggregate resource cap FIRST: reject an over-size registry image by its
    # canonical byte length BEFORE the O(members) crypto verification and the
    # whole-registry head hash below (a count-bounded but byte-huge snapshot must
    # not force expensive verification/hashing). Never truncate.
    _bounded_bytes(snap, MAX_SNAPSHOT_BYTES, "snapshot")
    generation = _req_str(snap["generation"], "snapshot.generation")
    if not 1 <= len(generation) <= MAX_GENERATION_LEN:
        raise SeedWorkContractError(
            f"snapshot.generation must be 1..{MAX_GENERATION_LEN} chars")
    identities = _wire_list(snap["identities"])
    if identities is None or len(identities) > MAX_IDENTITIES:
        raise SeedWorkContractError("snapshot.identities must be a bounded list")
    entries = _wire_list(snap["lineage_entries"])
    if entries is None or len(entries) > MAX_TIMELINE:
        raise SeedWorkContractError("snapshot.lineage_entries must be a bounded list")
    head_digest = _req_sha(snap["head_digest"], "snapshot.head_digest")
    from waggledance.core.cell_identity import verify_cell_identity
    from waggledance.core.genesis_lineage import (
        verify_lineage_entry, verify_lineage_registry,
    )

    # STRUCTURALLY validate every member and reject duplicate identity cell_id /
    # entry_hash keys: a snapshot is a registry image, so a member that does not
    # self-verify, or a duplicated key, is a forged/ambiguous registry.
    identity_copies = tuple(_frozen_mapping(i, "snapshot.identities") for i in identities)
    entry_copies = tuple(_frozen_mapping(e, "snapshot.lineage_entries") for e in entries)
    cell_ids: set = set()
    for i in identity_copies:
        ok, reason = verify_cell_identity(i)
        if not ok:
            raise SeedWorkContractError(f"snapshot identity invalid: {reason}")
        if i["cell_id"] in cell_ids:
            raise SeedWorkContractError("snapshot has a duplicate identity cell_id")
        cell_ids.add(i["cell_id"])
    entry_hashes: set = set()
    for e in entry_copies:
        ok, reason = verify_lineage_entry(e)
        if not ok:
            raise SeedWorkContractError(f"snapshot lineage entry invalid: {reason}")
        if e["entry_hash"] in entry_hashes:
            raise SeedWorkContractError("snapshot has a duplicate lineage entry_hash")
        entry_hashes.add(e["entry_hash"])
    # WHOLE-REGISTRY CLOSURE: the lineage entries must form ONE rooted tree --
    # unique cell_id (a cell has exactly one origin), EXACTLY ONE genesis root,
    # and every non-root's parent present in the registry with the full
    # verify_lineage_link proof. Individually-valid but disconnected / multi-root
    # / orphaned entries are a forged or ambiguous registry image and fail
    # closed. entry_hash-dedup above only rejects byte-identical duplicates; it
    # does NOT prove the entries are one connected lineage, nor reject two
    # distinct entries claiming the same cell_id (two origins for one cell).
    closed, closure_reason = verify_lineage_registry(entry_copies)
    if not closed:
        raise SeedWorkContractError(
            f"snapshot lineage registry not closed: {closure_reason}")
    # IDENTITY<->LINEAGE COMPLETENESS: the snapshot is one registry image, so the
    # SET of identity cell_ids must EQUAL the SET of lineage-entry cell_ids. Set
    # inequality means either a PHANTOM lineage cell (an entry whose cell has no
    # identity record) or a MISSING SIBLING (an identity whose cell has no lineage
    # entry -- or a lineage cell with no identity). Closure proves the entries form
    # one rooted tree and per-entry uniqueness proves one origin per lineage cell,
    # but neither ties the lineage-cell set to the identity set; a phantom root can
    # sit under the genuine root and still close. Fail-closed on any asymmetry.
    entry_cell_ids = {e["cell_id"] for e in entry_copies}
    if entry_cell_ids != cell_ids:
        raise SeedWorkContractError(
            "snapshot identity cell_ids and lineage cell_ids must be set-equal "
            "(phantom lineage cell or missing identity/lineage sibling)")
    # Re-derive the head digest from the private copies and require it to match
    # (the caller supplies the exact snapshot; no trusted flag, no double read).
    expected = derive_registry_head_digest(
        generation=generation,
        identities=identity_copies,
        lineage_entries=entry_copies,
    )
    if head_digest != expected:
        raise SeedWorkContractError("snapshot.head_digest does not match its content")
    return {
        "schema_version": SNAPSHOT_SCHEMA,
        "generation": generation,
        "identities": identity_copies,
        "lineage_entries": entry_copies,
        "head_digest": head_digest,
        "identity_cell_ids": frozenset(cell_ids),
        "entry_hashes": frozenset(entry_hashes),
    }


def require_snapshot_membership(*, identity: dict, proof: tuple, snapshot: dict) -> None:
    """The envelope's cell MUST be a member of the bound registry snapshot: the
    identity's cell_id is in the snapshot identities, and EVERY proof entry's
    entry_hash is in the snapshot lineage entries. Closes the cross-snapshot
    relation-forgery (a valid-but-unrelated identity/lineage bound to a snapshot
    that does not contain it). Fail-closed."""
    if identity["cell_id"] not in snapshot["identity_cell_ids"]:
        raise SeedWorkContractError("envelope identity is not a member of the bound snapshot")
    for i, entry in enumerate(proof):
        if entry["entry_hash"] not in snapshot["entry_hashes"]:
            raise SeedWorkContractError(f"lineage_proof[{i}] is not a member of the bound snapshot")


def require_relation_consistency(identity: dict, lineage: dict, parent, proof: tuple) -> None:
    """The envelope's independently-validated identity/lineage/parent must be
    ONE coherent relation with the proof: identity.cell_id == lineage.cell_id,
    lineage EXACT-EQUALS the proof tip, and parent_lineage EXACT-EQUALS the proof
    penultimate (or is null for a root). Closes the cross-relation forgery where
    each field self-verifies but they describe different cells."""
    if identity["cell_id"] != lineage["cell_id"]:
        raise SeedWorkContractError("identity.cell_id != lineage.cell_id")
    if dict(lineage) != dict(proof[-1]):
        raise SeedWorkContractError("envelope lineage != lineage_proof tip")
    if len(proof) == 1:
        if parent is not None:
            raise SeedWorkContractError("root envelope parent_lineage must be null")
    elif parent is None or dict(parent) != dict(proof[-2]):
        raise SeedWorkContractError("parent_lineage != lineage_proof penultimate")


def _frozen_mapping(value: object, label: str) -> dict:
    d = _wire_dict(value)
    if d is None:
        raise SeedWorkContractError(f"{label} member must be an exact dict")
    return d


def derive_registry_head_digest(
    *, generation: str, identities: tuple, lineage_entries: tuple
) -> str:
    """Whole-registry head digest binding the opaque generation to the exact
    (ordered) identity + lineage-entry content."""
    return sha256_digest({
        "domain": SNAPSHOT_HEAD_DOMAIN,
        "schema_version": SNAPSHOT_SCHEMA,
        "generation": generation,
        "identities": [dict(i) for i in identities],
        "lineage_entries": [dict(e) for e in lineage_entries],
    })


# --- solver contract / config digests (internally recomputed) --------------
def derive_solver_contract_digest() -> str:
    """Digest of the pinned solver's WIRE CONTRACT (schema/allowlists/keysets).
    Recomputed internally; never accepted from the caller as trusted."""
    return sha256_digest({
        "domain": SOLVER_CONTRACT_DOMAIN,
        "solver_id": SOLVER_ID,
        "request_schema": REQUEST_SCHEMA,
        "action_schema_version": ACTION_SCHEMA_VERSION,
        "request_keys": sorted(REQUEST_KEYS),
        "status_allowlist": sorted(_STATUS_ALLOWLIST),
        "tool_state_allowlist": sorted(_TOOL_STATE_ALLOWLIST),
    })


def derive_solver_config_digest(options: dict) -> str:
    """Digest of the exact normalized solver CONFIG (bounded options)."""
    return sha256_digest({
        "domain": SOLVER_CONFIG_DOMAIN,
        "solver_id": SOLVER_ID,
        "duplicate_window_hours": options["duplicate_window_hours"],
        "evidence_limit": options["evidence_limit"],
        "max_open_window_days": options["max_open_window_days"],
    })


def normalize_options(value: object) -> dict:
    """Validate the exact bounded options mapping (explicit; no Python
    defaults). Reject unknown keys, non-exact-int values, out-of-range."""
    opts = _wire_dict(value)
    if opts is None:
        raise SeedWorkContractError("options must be an exact dict")
    if set(opts.keys()) != {
        "duplicate_window_hours", "evidence_limit", "max_open_window_days"
    }:
        raise SeedWorkContractError("options keyset")
    return {
        "duplicate_window_hours": _req_int(
            opts["duplicate_window_hours"], "duplicate_window_hours",
            lo=DUP_WINDOW_MIN, hi=DUP_WINDOW_MAX),
        "evidence_limit": _req_int(
            opts["evidence_limit"], "evidence_limit",
            lo=EVIDENCE_LIMIT_MIN, hi=EVIDENCE_LIMIT_MAX),
        "max_open_window_days": _req_int(
            opts["max_open_window_days"], "max_open_window_days",
            lo=OPEN_WINDOW_MIN, hi=OPEN_WINDOW_MAX),
    }


def derive_request_digest(request_payload: dict, as_of_utc: str) -> str:
    """Content digest over the exact canonical request payload + as_of."""
    return sha256_digest({
        "domain": REQUEST_DIGEST_DOMAIN,
        "schema_version": REQUEST_SCHEMA,
        "request_payload": request_payload,
        "as_of_utc": as_of_utc,
    })


# --- request validation + complete PDAM evidence ---------------------------
_ENTRY_REQUIRED = ("entry_id", "local_id", "log_code", "device", "status", "created_at")
_ENTRY_OPTIONAL = ("issue", "action")
_TOOLSTATE_REQUIRED = ("tool_id", "state")
_TOOLSTATE_OPTIONAL = ("substate", "comment", "updated_by")
_COMMENT_REQUIRED = ("tool_id", "when", "by_user", "comment")
_COMMENT_OPTIONAL = ("stage",)


def _entry(value: object, label: str, as_of: datetime, *, open_only: bool) -> dict:
    d = _wire_dict(value)
    if d is None:
        raise SeedWorkContractError(f"{label} must be an exact dict")
    if set(d.keys()) - set(_ENTRY_REQUIRED) - set(_ENTRY_OPTIONAL) or not set(
        _ENTRY_REQUIRED
    ) <= set(d.keys()):
        raise SeedWorkContractError(f"{label} keyset")
    _req_int(d["entry_id"], f"{label}.entry_id", lo=0, hi=2**53)
    _req_int(d["local_id"], f"{label}.local_id", lo=0, hi=2**53)
    _req_str(d["log_code"], f"{label}.log_code")
    _req_str(d["device"], f"{label}.device")
    status = _req_str(d["status"], f"{label}.status")
    if open_only and status not in _STATUS_ALLOWLIST:
        raise SeedWorkContractError(f"{label}.status not in open allowlist")
    if parse_utc(d["created_at"], f"{label}.created_at") > as_of:
        raise SeedWorkContractError(f"{label}.created_at is after as_of")
    for opt in _ENTRY_OPTIONAL:
        if opt in d:
            _req_str(d[opt], f"{label}.{opt}")
    return {k: d[k] for k in (*_ENTRY_REQUIRED, *(o for o in _ENTRY_OPTIONAL if o in d))}


def _tool_state(value: object, label: str) -> dict:
    d = _wire_dict(value)
    if d is None:
        raise SeedWorkContractError(f"{label} must be an exact dict")
    if set(d.keys()) - set(_TOOLSTATE_REQUIRED) - set(_TOOLSTATE_OPTIONAL) or not set(
        _TOOLSTATE_REQUIRED
    ) <= set(d.keys()):
        raise SeedWorkContractError(f"{label} keyset")
    _req_str(d["tool_id"], f"{label}.tool_id")
    if _req_str(d["state"], f"{label}.state") not in _TOOL_STATE_ALLOWLIST:
        raise SeedWorkContractError(f"{label}.state not in allowlist")
    for opt in _TOOLSTATE_OPTIONAL:
        if opt in d:
            _req_str(d[opt], f"{label}.{opt}")
    return {k: d[k] for k in (*_TOOLSTATE_REQUIRED, *(o for o in _TOOLSTATE_OPTIONAL if o in d))}


def _comment(value: object, label: str, as_of: datetime) -> dict:
    d = _wire_dict(value)
    if d is None:
        raise SeedWorkContractError(f"{label} must be an exact dict")
    if set(d.keys()) - set(_COMMENT_REQUIRED) - set(_COMMENT_OPTIONAL) or not set(
        _COMMENT_REQUIRED
    ) <= set(d.keys()):
        raise SeedWorkContractError(f"{label} keyset")
    _req_str(d["tool_id"], f"{label}.tool_id")
    if parse_utc(d["when"], f"{label}.when") > as_of:
        raise SeedWorkContractError(f"{label}.when is after as_of")
    _req_str(d["by_user"], f"{label}.by_user")
    _req_str(d["comment"], f"{label}.comment")
    for opt in _COMMENT_OPTIONAL:
        if opt in d:
            _req_str(d[opt], f"{label}.{opt}")
    return {k: d[k] for k in (*_COMMENT_REQUIRED, *(o for o in _COMMENT_OPTIONAL if o in d))}


def parse_seed_work_request(value: object, as_of_utc: str) -> dict:
    """Validate the exact bounded request payload with COMPLETE PDAM evidence.

    Reject-hard, no fallback/partial. Requires: exact keyset; bounded lists;
    canonical UTC rows all <= as_of; a unique explicit parent ToolState per open
    device; an explicit subtools key per open device (empty allowed); a unique
    ToolState per named subtool; every open entry present EXACTLY in
    repair_timeline; closed status/state allowlists."""
    as_of = parse_utc(as_of_utc, "as_of_utc")
    req = _wire_dict(value)
    if req is None:
        raise SeedWorkContractError("request must be an exact dict")
    if set(req.keys()) != REQUEST_KEYS:
        raise SeedWorkContractError("request keyset")
    if _req_str(req["schema_version"], "request.schema_version") != REQUEST_SCHEMA:
        raise SeedWorkContractError("request schema_version refused")
    # Enforce the pinned request resource bound BEFORE any nested/list/string
    # processing (the standalone request parser -- not just the 4 MiB envelope --
    # owns this cap; a request under the envelope cap but over MAX_REQUEST_BYTES
    # must still reject on the producer path).
    _bounded_bytes(req, MAX_REQUEST_BYTES, "request")

    raw_entries = _wire_list(req["entries"])
    if raw_entries is None or len(raw_entries) > MAX_ENTRIES:
        raise SeedWorkContractError("request.entries bounded list")
    entries = [_entry(e, f"entries[{i}]", as_of, open_only=True) for i, e in enumerate(raw_entries)]

    raw_timeline = _wire_list(req["repair_timeline"])
    if raw_timeline is None or len(raw_timeline) > MAX_TIMELINE:
        raise SeedWorkContractError("request.repair_timeline bounded list")
    timeline = [_entry(e, f"repair_timeline[{i}]", as_of, open_only=False) for i, e in enumerate(raw_timeline)]

    raw_states = _wire_dict(req["tool_states"])
    if raw_states is None or len(raw_states) > MAX_TOOL_STATES:
        raise SeedWorkContractError("request.tool_states bounded dict")
    # Bind the outer mapping KEY exactly to the validated inner tool_id: the
    # solver looks a state up by device KEY, so a foreign-labelled inner tool_id
    # would serve one device another tool's state (a forged/ambiguous mapping).
    tool_states = {}
    for k, v in raw_states.items():
        ts = _tool_state(v, f"tool_states[{k}]")
        if k != ts["tool_id"]:
            raise SeedWorkContractError(
                f"tool_states key {k!r} != inner tool_id {ts['tool_id']!r}")
        tool_states[k] = ts

    raw_comments = _wire_list(req["comments"])
    if raw_comments is None or len(raw_comments) > MAX_COMMENTS:
        raise SeedWorkContractError("request.comments bounded list")
    comments = [_comment(c, f"comments[{i}]", as_of) for i, c in enumerate(raw_comments)]

    raw_subtools = _wire_dict(req["subtools"])
    if raw_subtools is None or len(raw_subtools) > MAX_SUBTOOL_GROUPS:
        raise SeedWorkContractError("request.subtools bounded dict")
    subtools: dict[str, list] = {}
    for parent, kids in raw_subtools.items():
        kid_list = _wire_list(kids)
        if kid_list is None or len(kid_list) > MAX_CHILDREN_PER_PARENT:
            raise SeedWorkContractError(f"subtools[{parent}] bounded list")
        seen_kids: set = set()
        for kid in kid_list:
            _req_str(kid, f"subtools[{parent}] member")
            if kid == parent:
                raise SeedWorkContractError(f"subtools[{parent}] lists itself as a subtool")
            if kid in seen_kids:
                raise SeedWorkContractError(f"subtools[{parent}] has a duplicate member {kid!r}")
            seen_kids.add(kid)
        # Canonicalize member order (unique strings => a total, deterministic
        # order). The solver joins limited-subtool notes in iteration order, so an
        # un-canonical member order would make action_text -- and thus the result
        # digest -- depend on the caller's input permutation.
        subtools[parent] = sorted(kid_list)

    options = normalize_options(req["options"])

    # --- complete PDAM evidence (no fallback) ---
    # Unique entry_id in BOTH lists: a duplicated open row otherwise makes the
    # solver emit two actions with the same entry_id (an ambiguous close plan).
    entry_ids = [e["entry_id"] for e in entries]
    if len(set(entry_ids)) != len(entry_ids):
        raise SeedWorkContractError("entries has a duplicate entry_id")
    tl_ids = [e["entry_id"] for e in timeline]
    if len(set(tl_ids)) != len(tl_ids):
        raise SeedWorkContractError("repair_timeline has a duplicate entry_id")
    timeline_index = {}
    for e in timeline:
        timeline_index.setdefault(e["device"], []).append(e)
    for i, e in enumerate(entries):
        device = e["device"]
        if device not in tool_states:
            raise SeedWorkContractError(f"entries[{i}] device has no explicit parent ToolState")
        if device not in subtools:
            raise SeedWorkContractError(f"entries[{i}] device has no explicit subtools key")
        for sub in subtools[device]:
            if sub not in tool_states:
                raise SeedWorkContractError(f"named subtool {sub} has no explicit ToolState")
        # The open entry must appear EXACTLY once in its device timeline, byte-
        # equal (membership alone permitted a duplicated/near-duplicate row).
        matches = [t for t in timeline_index.get(device, []) if t == e]
        if len(matches) != 1:
            raise SeedWorkContractError(
                f"entries[{i}] must appear exactly once in repair_timeline")

    # CANONICALIZE list order with COMPLETE deterministic tie-breakers BEFORE the
    # request digest / PDAM run. The request content-address and the solver's
    # advisory output must be invariant under caller input permutation. Sorting by
    # each row's full canonical JSON bytes is a total, byte-complete tie-breaker:
    # entries/timeline have unique entry_ids (no ties), and byte-identical comments
    # tie to an identical row so their relative order is immaterial. Without this,
    # a permuted comments list changed evidence selection (the solver's stable sort
    # of tied comments plus the ``[:limit]`` truncation) and the request digest.
    entries.sort(key=canonical_json_bytes)
    timeline.sort(key=canonical_json_bytes)
    comments.sort(key=canonical_json_bytes)

    return {
        "schema_version": REQUEST_SCHEMA,
        "entries": entries,
        "repair_timeline": timeline,
        "tool_states": tool_states,
        "comments": comments,
        "subtools": subtools,
        "options": options,
    }


# --- pre-solver result-output upper bound (producer amplification gate) -----
# Fixed, deliberately GENEROUS byte allowances for the pinned solver's literal
# scaffolding. Each over-estimates the corresponding pdam_close_solver literal so
# the derived bound can never under-count the real canonical ResultV1 bytes.
_RESULT_STRUCT_BYTES = 2_048            # ResultV1 envelope minus its actions array
_ACTION_STRUCT_BYTES = 256             # one action dict's keys + short enum values
_ENTRY_ID_DIGITS = 16                  # entry_id / duplicate_of decimal digits (<2**53)
_EVID_HEADER_BYTES = 160               # _context_lines header (or the "no evidence" line)
_COMMENT_LINE_FIXED = 64               # _format_comment date/time/space/bracket literals
_CLEAN_COMMENT_CAP_BYTES = 240 * 12 + 16   # _clean(comment, 240) escaped-byte ceiling
_NOTE_HEADER_BYTES = 48                # _limited_note "Limited subtool state: ... ." shell
_LIMITED_CHUNK_FIXED = 32              # one limited-subtool chunk separators/literals
_CLEAN_STATE_CAP_BYTES = 160 * 12 + 16     # _clean(state.comment, 160) escaped-byte ceiling
_PARENT_LINE_BYTES = 80                # "Parent MES state: <state>." fixed part
_CONCLUSION_BYTES = 192               # longest solver conclusion line (+ entry_id digits)


def _jbytes(value: object) -> int:
    """Canonical-JSON byte length of ONE already-validated string (includes the
    two quote bytes and full ``ensure_ascii`` escaping). ensure_ascii escapes each
    code point independently, so escape-of-concatenation equals
    concatenation-of-escapes; summing part lengths is therefore a SOUND upper
    bound on any composite string built from those parts."""
    if type(value) is not str:
        return 2
    return len(canonical_json_bytes(value))


def estimate_result_output_upper_bound(request: dict) -> int:
    """Conservative, input-derived UPPER BOUND on the canonical byte size of the
    advisory ResultV1 the pinned solver would emit for this ALREADY-VALIDATED
    request -- computed WITHOUT running the solver.

    The pinned ``wd.pdam_close.v1`` solver emits EXACTLY one action per open entry,
    and every action's ``action_text`` is composed ONLY from: up to
    ``evidence_limit`` selected comments (each rendered <=240 chars), the device's
    limited-subtool notes (each state comment <=160 chars), the parent MES state,
    and a fixed conclusion line. Bounding each of those contributions per entry so
    it is >= whatever the solver could actually render, then summing over every
    entry, yields a sound over-estimate. If the bound is within MAX_RESULT_BYTES
    the real result cannot exceed it, so the producer never has to build a
    provisional multi-megabyte result just to discover it is over-cap. Runs on the
    normalized request only (plain dicts/lists); defensive ``.get`` so it always
    returns a number rather than leaking on unexpected shape."""
    entries = request.get("entries", ()) or ()
    comments = request.get("comments", ()) or ()
    tool_states = request.get("tool_states", {}) or {}
    subtools = request.get("subtools", {}) or {}
    options = request.get("options", {}) or {}
    evidence_limit = options.get("evidence_limit", EVIDENCE_LIMIT_MAX)
    if type(evidence_limit) is not int or evidence_limit < 0:
        evidence_limit = EVIDENCE_LIMIT_MAX

    # Per-tool comment stats: how many comments carry each tool_id and the largest
    # single formatted evidence line the solver could render for that tool_id. The
    # same comment is reusable across many devices' evidence -- that reuse IS the
    # amplification, so evidence is bounded per action (<= evidence_limit lines),
    # never by the flat input size.
    count_by_tool: dict = {}
    maxline_by_tool: dict = {}
    for c in comments:
        tid = c.get("tool_id", "") if type(c) is dict else ""
        by_user = c.get("by_user", "") if type(c) is dict else ""
        comment = c.get("comment", "") if type(c) is dict else ""
        line = (_COMMENT_LINE_FIXED + _jbytes(by_user) + _jbytes(tid)
                + min(_jbytes(comment), _CLEAN_COMMENT_CAP_BYTES))
        count_by_tool[tid] = count_by_tool.get(tid, 0) + 1
        if line > maxline_by_tool.get(tid, 0):
            maxline_by_tool[tid] = line

    total = _RESULT_STRUCT_BYTES
    for e in entries:
        device = e.get("device", "") if type(e) is dict else ""
        device_subs = subtools.get(device, ()) or ()
        id_set = {device}
        id_set.update(device_subs)
        match_count = 0
        max_line = 0
        for tid in id_set:
            match_count += count_by_tool.get(tid, 0)
            ml = maxline_by_tool.get(tid, 0)
            if ml > max_line:
                max_line = ml
        n_evid = min(evidence_limit, match_count)
        evidence_bytes = _EVID_HEADER_BYTES + n_evid * max_line
        # Limited-subtool note: one chunk per subtool whose ToolState is a LIMITED
        # state (mirrors _limited_subtools). Over-count is bounded by the device's
        # own subtool list (<= MAX_CHILDREN_PER_PARENT).
        limited_bytes = 0
        n_limited = 0
        for sub in device_subs:
            ts = tool_states.get(sub)
            if type(ts) is dict and ts.get("state") in _SOLVER_LIMITED_STATES:
                n_limited += 1
                limited_bytes += (
                    _LIMITED_CHUNK_FIXED + _jbytes(ts.get("tool_id", ""))
                    + _jbytes(ts.get("state", "")) + _jbytes(ts.get("substate", ""))
                    + min(_jbytes(ts.get("comment", "")), _CLEAN_STATE_CAP_BYTES))
        if n_limited:
            limited_bytes += _NOTE_HEADER_BYTES
        parent = tool_states.get(device)
        if type(parent) is dict:
            parent_bytes = (_PARENT_LINE_BYTES + _jbytes(parent.get("state", ""))
                            + _jbytes(parent.get("substate", "")))
        else:
            parent_bytes = _PARENT_LINE_BYTES + 16
        newline_bytes = (n_evid + n_limited + 4) * 2
        action_text_bytes = (evidence_bytes + limited_bytes + parent_bytes
                             + _CONCLUSION_BYTES + newline_bytes)
        total += _ACTION_STRUCT_BYTES + _ENTRY_ID_DIGITS + _jbytes(device) + 2 + action_text_bytes
    return total


def require_result_output_within_bound(request: dict) -> None:
    """Fail-closed PRE-SOLVER gate: reject a validated request whose conservative
    result-output upper bound would exceed ``MAX_RESULT_BYTES`` BEFORE the solver
    runs, so a max-cardinality amplifier can never drive the producer to construct
    a multi-megabyte provisional ResultV1 only to reject it afterward. The
    post-result ``MAX_RESULT_BYTES`` validation on the built result is retained as
    defense in depth (this gate is an input-side pre-filter, not a replacement)."""
    bound = estimate_result_output_upper_bound(request)
    if bound > MAX_RESULT_BYTES:
        raise SeedWorkContractError(
            f"request result-output upper bound {bound} exceeds "
            f"MAX_RESULT_BYTES {MAX_RESULT_BYTES}")


# --- lineage proof ---------------------------------------------------------
def derive_lineage_proof_digest(proof: tuple) -> str:
    return sha256_digest({
        "domain": ENVELOPE_DIGEST_DOMAIN + ".proof",
        "proof": [dict(p) for p in proof],
    })


def parse_lineage_proof(value: object, identity_cell_id: str) -> tuple:
    """Bounded exact root-to-current linear proof. Each entry self-verifies;
    proof[0] is the GENESIS root; every consecutive pair links; the final
    entry's cell_id == the identity's cell_id. Fail-closed."""
    from waggledance.core.genesis_lineage import (
        GENESIS_PREV_HASH, GENESIS_ROOT_PARENT, verify_lineage_entry,
        verify_lineage_link,
    )

    proof_list = _wire_list(value)
    if proof_list is None or not proof_list or len(proof_list) > MAX_LINEAGE_PROOF:
        raise SeedWorkContractError("lineage_proof must be a bounded non-empty list")
    frozen = []
    for i, entry in enumerate(proof_list):
        d = _wire_dict(entry)
        if d is None:
            raise SeedWorkContractError(f"lineage_proof[{i}] must be an exact dict")
        ok, reason = verify_lineage_entry(d)
        if not ok:
            raise SeedWorkContractError(f"lineage_proof[{i}] invalid entry: {reason}")
        frozen.append(d)
    root = frozen[0]
    if (root["parent_cell_id"] != GENESIS_ROOT_PARENT
            or root["lineage_prev_hash"] != GENESIS_PREV_HASH
            or root["depth"] != 0):
        raise SeedWorkContractError("lineage_proof[0] is not the GENESIS root")
    if len(frozen) != frozen[-1]["depth"] + 1:
        raise SeedWorkContractError("lineage_proof length must equal depth+1")
    for i in range(1, len(frozen)):
        ok, reason = verify_lineage_link(frozen[i], frozen[i - 1])
        if not ok:
            raise SeedWorkContractError(f"lineage_proof link {i} invalid: {reason}")
    if frozen[-1]["cell_id"] != identity_cell_id:
        raise SeedWorkContractError("lineage_proof tip cell_id != identity cell_id")
    return tuple(frozen)


# --- envelope --------------------------------------------------------------
def _verify_identity(value: object) -> dict:
    from waggledance.core.cell_identity import verify_cell_identity

    d = _wire_dict(value)
    if d is None:
        raise SeedWorkContractError("identity must be an exact dict")
    ok, reason = verify_cell_identity(d)
    if not ok:
        raise SeedWorkContractError(f"identity invalid: {reason}")
    return d


def _verify_entry_dict(value: object, label: str) -> dict:
    from waggledance.core.genesis_lineage import verify_lineage_entry

    d = _wire_dict(value)
    if d is None:
        raise SeedWorkContractError(f"{label} must be an exact dict")
    ok, reason = verify_lineage_entry(d)
    if not ok:
        raise SeedWorkContractError(f"{label} invalid: {reason}")
    return d


def derive_envelope_id(*, identity, lineage, parent_lineage, lineage_proof_digest,
                       registry_generation, registry_head_digest, solver_contract_digest,
                       solver_config_digest, request_digest, as_of_utc) -> str:
    return sha256_digest({
        "domain": ENVELOPE_DIGEST_DOMAIN,
        "schema_version": ENVELOPE_SCHEMA,
        "identity": identity,
        "lineage": lineage,
        "parent_lineage": parent_lineage,
        "lineage_proof_digest": lineage_proof_digest,
        "registry_generation": registry_generation,
        "registry_head_digest": registry_head_digest,
        "solver_id": SOLVER_ID,
        "solver_contract_digest": solver_contract_digest,
        "solver_config_digest": solver_config_digest,
        "request_digest": request_digest,
        "as_of_utc": as_of_utc,
    })


def build_seed_work_envelope(*, identity, lineage, parent_lineage, lineage_proof,
                             registry_snapshot, request_payload, as_of_utc) -> dict:
    """Build a fully-bound SeedWorkEnvelopeV1 wire dict. Everything is validated
    and every digest internally recomputed; nothing is trusted from the caller."""
    ident = _verify_identity(identity)
    cell_id = ident["cell_id"]
    lin = _verify_entry_dict(lineage, "lineage")
    parent = None if parent_lineage is None else _verify_entry_dict(parent_lineage, "parent_lineage")
    snap = parse_registry_snapshot(registry_snapshot)
    request = parse_seed_work_request(request_payload, as_of_utc)
    proof = parse_lineage_proof(lineage_proof, cell_id)
    require_relation_consistency(ident, lin, parent, proof)
    require_snapshot_membership(identity=ident, proof=proof, snapshot=snap)
    proof_digest = derive_lineage_proof_digest(proof)
    contract_digest = derive_solver_contract_digest()
    config_digest = derive_solver_config_digest(request["options"])
    req_digest = derive_request_digest(request, as_of_utc)
    envelope_id = derive_envelope_id(
        identity=ident, lineage=lin, parent_lineage=parent,
        lineage_proof_digest=proof_digest, registry_generation=snap["generation"],
        registry_head_digest=snap["head_digest"], solver_contract_digest=contract_digest,
        solver_config_digest=config_digest, request_digest=req_digest, as_of_utc=as_of_utc)
    envelope = {
        "schema_version": ENVELOPE_SCHEMA,
        "envelope_id": envelope_id,
        "identity": ident,
        "lineage": lin,
        "parent_lineage": parent,
        "lineage_proof": [dict(p) for p in proof],
        "lineage_proof_digest": proof_digest,
        "registry_generation": snap["generation"],
        "registry_head_digest": snap["head_digest"],
        "solver_id": SOLVER_ID,
        "solver_contract_digest": contract_digest,
        "solver_config_digest": config_digest,
        "request_payload": request,
        "as_of_utc": as_of_utc,
        "request_digest": req_digest,
    }
    _bounded_bytes(envelope, MAX_ENVELOPE_BYTES, "envelope")
    return envelope


def parse_seed_work_envelope(value: object) -> dict:
    """Fail-closed wire verifier: exact keyset, re-validate identity/lineage/
    proof, recompute every digest + envelope_id and require equality. Raises on
    any deviation. The registry snapshot itself is supplied separately at
    execute/replay time and must match registry_generation + head_digest."""
    env = _wire_dict(value)
    if env is None:
        raise SeedWorkContractError("envelope must be an exact dict")
    if set(env.keys()) != ENVELOPE_KEYS:
        raise SeedWorkContractError("envelope keyset")
    if _req_str(env["schema_version"], "envelope.schema_version") != ENVELOPE_SCHEMA:
        raise SeedWorkContractError("envelope schema_version refused")
    if _req_str(env["solver_id"], "envelope.solver_id") != SOLVER_ID:
        raise SeedWorkContractError("envelope solver_id refused")
    _bounded_bytes(env, MAX_ENVELOPE_BYTES, "envelope")
    ident = _verify_identity(env["identity"])
    cell_id = ident["cell_id"]
    lin = _verify_entry_dict(env["lineage"], "lineage")
    parent = None if env["parent_lineage"] is None else _verify_entry_dict(
        env["parent_lineage"], "parent_lineage")
    generation = _req_str(env["registry_generation"], "envelope.registry_generation")
    head_digest = _req_sha(env["registry_head_digest"], "envelope.registry_head_digest")
    as_of_utc = _req_str(env["as_of_utc"], "envelope.as_of_utc")
    request = parse_seed_work_request(env["request_payload"], as_of_utc)
    proof = parse_lineage_proof(env["lineage_proof"], cell_id)
    require_relation_consistency(ident, lin, parent, proof)
    proof_digest = derive_lineage_proof_digest(proof)
    if _req_sha(env["lineage_proof_digest"], "envelope.lineage_proof_digest") != proof_digest:
        raise SeedWorkContractError("envelope lineage_proof_digest mismatch")
    contract_digest = derive_solver_contract_digest()
    config_digest = derive_solver_config_digest(request["options"])
    req_digest = derive_request_digest(request, as_of_utc)
    if _req_sha(env["solver_contract_digest"], "envelope.solver_contract_digest") != contract_digest:
        raise SeedWorkContractError("envelope solver_contract_digest mismatch")
    if _req_sha(env["solver_config_digest"], "envelope.solver_config_digest") != config_digest:
        raise SeedWorkContractError("envelope solver_config_digest mismatch")
    if _req_sha(env["request_digest"], "envelope.request_digest") != req_digest:
        raise SeedWorkContractError("envelope request_digest mismatch")
    expected_id = derive_envelope_id(
        identity=ident, lineage=lin, parent_lineage=parent,
        lineage_proof_digest=proof_digest, registry_generation=generation,
        registry_head_digest=head_digest, solver_contract_digest=contract_digest,
        solver_config_digest=config_digest, request_digest=req_digest, as_of_utc=as_of_utc)
    if _req_sha(env["envelope_id"], "envelope.envelope_id") != expected_id:
        raise SeedWorkContractError("envelope_id does not match derived digest")
    return {
        **env, "identity": ident, "lineage": lin, "parent_lineage": parent,
        "request_payload": request, "lineage_proof": [dict(p) for p in proof],
    }


def verify_seed_work_envelope(value: object) -> tuple[bool, Optional[str]]:
    try:
        parse_seed_work_envelope(value)
        return True, None
    except SeedWorkContractError as exc:
        return False, str(exc)


# --- result + evidence integrity -------------------------------------------
_ACTION_KEYS = frozenset({
    "entry_id", "device", "kind", "current_status", "target_status",
    "action_text", "duplicate_of",
})


def _action(value: object, label: str) -> dict:
    d = _wire_dict(value)
    if d is None:
        raise SeedWorkContractError(f"{label} must be an exact dict")
    if set(d.keys()) != _ACTION_KEYS:
        raise SeedWorkContractError(f"{label} keyset")
    _req_int(d["entry_id"], f"{label}.entry_id", lo=0, hi=2**53)
    _req_str(d["device"], f"{label}.device")
    _req_str(d["kind"], f"{label}.kind")
    _req_str(d["current_status"], f"{label}.current_status")
    _req_str(d["target_status"], f"{label}.target_status")
    _req_str(d["action_text"], f"{label}.action_text")
    if d["duplicate_of"] is not None:
        _req_int(d["duplicate_of"], f"{label}.duplicate_of", lo=0, hi=2**53)
    return {k: d[k] for k in ("entry_id", "device", "kind", "current_status",
                              "target_status", "action_text", "duplicate_of")}


def derive_result_digest(*, envelope_id, cell_id, lineage_entry_hash, request_digest,
                         solver_contract_digest, solver_config_digest,
                         registry_generation, registry_head_digest, actions,
                         as_of_utc) -> str:
    """Result content address. Binds the FULL identity/solver/registry binding set
    -- ``lineage_entry_hash``, both solver digests, and ``registry_generation`` in
    ADDITION to envelope_id/cell_id/request_digest/registry_head_digest -- so a
    self-consistent result cannot silently carry a foreign solver contract/config,
    a foreign lineage entry, or a foreign registry generation while keeping a
    valid digest. Any mutation of a bound field invalidates the digest."""
    return sha256_digest({
        "domain": RESULT_DIGEST_DOMAIN,
        "schema_version": RESULT_SCHEMA,
        "envelope_id": envelope_id,
        "cell_id": cell_id,
        "lineage_entry_hash": lineage_entry_hash,
        "solver_contract_digest": solver_contract_digest,
        "solver_config_digest": solver_config_digest,
        "request_digest": request_digest,
        "registry_generation": registry_generation,
        "registry_head_digest": registry_head_digest,
        "action_schema_version": ACTION_SCHEMA_VERSION,
        "actions": actions,
        "as_of_utc": as_of_utc,
    })


def derive_evidence_digest(*, envelope_id, cell_id, request_digest,
                           registry_head_digest, result_digest) -> str:
    """Self-consistency binding (SeedWorkEvidenceV1) -- NOT a MAGMA receipt or
    authenticity proof, and it grants no authority."""
    return sha256_digest({
        "domain": EVIDENCE_DIGEST_DOMAIN,
        "schema_version": EVIDENCE_SCHEMA,
        "envelope_id": envelope_id,
        "cell_id": cell_id,
        "request_digest": request_digest,
        "registry_head_digest": registry_head_digest,
        "result_digest": result_digest,
    })


def parse_seed_work_result(value: object) -> dict:
    """Fail-closed result wire verifier: exact keyset, exact action shapes,
    advisory_only True + external_writes_applied False enforced, recompute the
    result_digest and evidence_digest and require equality."""
    res = _wire_dict(value)
    if res is None:
        raise SeedWorkContractError("result must be an exact dict")
    if set(res.keys()) != RESULT_KEYS:
        raise SeedWorkContractError("result keyset")
    if _req_str(res["schema_version"], "result.schema_version") != RESULT_SCHEMA:
        raise SeedWorkContractError("result schema_version refused")
    if _req_str(res["solver_id"], "result.solver_id") != SOLVER_ID:
        raise SeedWorkContractError("result solver_id refused")
    if _req_str(res["action_schema_version"], "result.action_schema_version") != ACTION_SCHEMA_VERSION:
        raise SeedWorkContractError("result action_schema_version refused")
    if _req_bool(res["advisory_only"], "result.advisory_only") is not True:
        raise SeedWorkContractError("result advisory_only must be True")
    if _req_bool(res["external_writes_applied"], "result.external_writes_applied") is not False:
        raise SeedWorkContractError("result external_writes_applied must be False")
    _bounded_bytes(res, MAX_RESULT_BYTES, "result")
    envelope_id = _req_sha(res["envelope_id"], "result.envelope_id")
    cell_id = _req_sha(res["cell_id"], "result.cell_id")
    lineage_entry_hash = _req_sha(res["lineage_entry_hash"], "result.lineage_entry_hash")
    solver_contract_digest = _req_sha(res["solver_contract_digest"], "result.solver_contract_digest")
    solver_config_digest = _req_sha(res["solver_config_digest"], "result.solver_config_digest")
    request_digest = _req_sha(res["request_digest"], "result.request_digest")
    registry_generation = _req_str(res["registry_generation"], "result.registry_generation")
    if not registry_generation:
        raise SeedWorkContractError("result.registry_generation must be non-empty")
    registry_head_digest = _req_sha(res["registry_head_digest"], "result.registry_head_digest")
    as_of_utc = _req_str(res["as_of_utc"], "result.as_of_utc")
    # Enforce the frozen canonical aware-UTC contract (empty / non-time /
    # impossible-calendar / non-Z-offset all reject), not merely str-ness. The
    # canonical lexeme itself stays the digest input below.
    parse_utc(as_of_utc, "result.as_of_utc")
    raw_actions = _wire_list(res["actions"])
    if raw_actions is None:
        raise SeedWorkContractError("result.actions must be a list")
    actions = [_action(a, f"actions[{i}]") for i, a in enumerate(raw_actions)]
    expected_result = derive_result_digest(
        envelope_id=envelope_id, cell_id=cell_id, lineage_entry_hash=lineage_entry_hash,
        request_digest=request_digest, solver_contract_digest=solver_contract_digest,
        solver_config_digest=solver_config_digest, registry_generation=registry_generation,
        actions=actions, registry_head_digest=registry_head_digest, as_of_utc=as_of_utc)
    if _req_sha(res["result_digest"], "result.result_digest") != expected_result:
        raise SeedWorkContractError("result_digest does not match derived digest")
    expected_evidence = derive_evidence_digest(
        envelope_id=envelope_id, cell_id=cell_id, request_digest=request_digest,
        registry_head_digest=registry_head_digest, result_digest=expected_result)
    if _req_sha(res["evidence_digest"], "result.evidence_digest") != expected_evidence:
        raise SeedWorkContractError("evidence_digest does not match derived digest")
    return {**res, "actions": actions}


def evidence_binding_consistent(env: dict, res: dict) -> tuple[bool, Optional[str]]:
    """Cross-check an ALREADY-PARSED result echoes its ALREADY-PARSED envelope on
    every identity/binding field. Operates only on the per-call validated
    snapshots handed in -- it never re-parses (and so never re-reads) mutable
    caller originals. Shared by ``verify_seed_work_evidence_integrity`` (public,
    which parses once then delegates) and the adapter's ``verify_seed_solver_replay``
    (which passes its own parsed env/res). Keeping ONE implementation stops the
    replay echo-binding and the self-consistency check from drifting apart."""
    for field in ("envelope_id", "solver_contract_digest", "solver_config_digest",
                  "request_digest", "registry_generation", "registry_head_digest",
                  "as_of_utc"):
        if res[field] != env[field]:
            return False, f"result.{field} != envelope.{field}"
    if res["cell_id"] != env["identity"]["cell_id"]:
        return False, "result.cell_id != envelope identity cell_id"
    if res["lineage_entry_hash"] != env["lineage"]["entry_hash"]:
        return False, "result.lineage_entry_hash != envelope lineage entry_hash"
    return True, None


def verify_seed_work_evidence_integrity(envelope: object, result: object) -> tuple[bool, Optional[str]]:
    """SELF-CONSISTENCY ONLY (no solver rerun): the parsed result's echoed
    ids/digests all equal the parsed envelope's, and every result digest
    re-derives. Advisory evidence, not an authenticity proof, grants no
    authority. Parses each input exactly once, then delegates to
    ``evidence_binding_consistent``."""
    try:
        env = parse_seed_work_envelope(envelope)
        res = parse_seed_work_result(result)
    except SeedWorkContractError as exc:
        return False, str(exc)
    return evidence_binding_consistent(env, res)


# --- immutable validated records (public API) ------------------------------
@dataclass(frozen=True)
class SeedWorkEnvelopeV1:
    """Immutable validated SeedWork envelope. Construct via ``parse`` (which
    runs the fail-closed wire verifier); a forged instance cannot exist in a
    passing process. ``frozen`` is an ergonomic guardrail, not a trust boundary;
    the guarantee is that ``parse`` recomputes every digest."""

    envelope_id: str
    mapping: dict

    @classmethod
    def parse(cls, value: object) -> "SeedWorkEnvelopeV1":
        m = parse_seed_work_envelope(value)
        return cls(envelope_id=m["envelope_id"], mapping=m)

    def to_mapping(self) -> dict:
        return dict(self.mapping)


@dataclass(frozen=True)
class SeedWorkResultV1:
    """Immutable validated SeedWork result (advisory, zero authority)."""

    envelope_id: str
    result_digest: str
    mapping: dict

    @classmethod
    def parse(cls, value: object) -> "SeedWorkResultV1":
        m = parse_seed_work_result(value)
        return cls(envelope_id=m["envelope_id"], result_digest=m["result_digest"], mapping=m)

    def to_mapping(self) -> dict:
        return dict(self.mapping)

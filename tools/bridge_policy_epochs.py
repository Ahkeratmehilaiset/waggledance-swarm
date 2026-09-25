# SPDX-License-Identifier: BUSL-1.1
"""Derive the five admission epochs from explicit, declared evidence.

``bridge_task_admission`` needs five epoch values and treats any of them being
absent as unknown, which parks. This module is where those values are supposed
to come from, and its entire job is to refuse to produce one unless the
evidence for it is actually present and well formed.

The rules that make it safe to consume:

* **Derived, never accepted.** There is no code path that lets a caller hand
  in an epoch value. Every epoch is a hash over declared evidence, so two
  callers supplying the same material get the same snapshot. This function
  checks shape, not authenticity or correspondence with referenced files.
* **No wall clock, no randomness.** Nothing here reads the current time or
  generates an identifier. A snapshot taken twice a day apart over unchanged
  evidence is byte-identical, which is what lets admission cache against it
  without a timer silently buying a fresh judgment budget.
* **Missing evidence stays missing.** An epoch that cannot be derived is
  absent from ``epochs`` and recorded in ``unknown`` with a reason. It is never
  defaulted, back-filled, or replaced by a placeholder. Because admission
  requires all five, an incomplete snapshot parks by construction rather than
  by anyone remembering to check ``complete``.
* **A model label authenticates nothing.** Qualification evidence consisting of
  a model name and nothing else is refused. A label is not an attestation of
  provider identity and not a measurement of quota, and the snapshot says so in
  fields that are always false.

Observation only. Nothing here mutates anything, and the snapshot carries no
permission to switch a profile.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Mapping

try:  # package import first, mirroring the sibling capacity modules
    from tools.bridge_capacity_advisor import InputError, _text
    from tools.bridge_capacity_recovery import BINDING_FIELDS, _valid_process_epoch
    from tools.bridge_task_admission import EPOCH_FIELDS
except ModuleNotFoundError:  # pragma: no cover - exercised by flat-layout callers
    from bridge_capacity_advisor import InputError, _text
    from bridge_capacity_recovery import BINDING_FIELDS, _valid_process_epoch
    from bridge_task_admission import EPOCH_FIELDS

EPOCH_SNAPSHOT_SCHEMA = "wd.policy-epoch-snapshot.v1"

_SHA256 = re.compile(r"^[0-9A-Fa-f]{64}$")

#: Evidence keys, one per epoch, in the same order admission lists them.
EVIDENCE_KEYS = {
    "policy_epoch": "policy",
    "catalog_epoch": "catalog",
    "qualification_epoch": "qualification",
    "profile_epoch": "profile",
    "native_epoch": "native",
}

#: Native identity is drawn from the admission binding, not from a clock. The
#: process start stamp is part of a process *identity* -- the same field
#: recovery uses to tell a live process from a reused PID -- and is therefore
#: evidence, not a wall-clock reading. See docs/BRIDGE_POLICY_EPOCHS.md.
NATIVE_IDENTITY_FIELDS = (
    "agent_id",
    "session_id",
    "native_thread_id",
    "native_pid",
    "native_process_started_at",
)

MAX_EVIDENCE_BYTES = 1024 * 1024


def _canonical(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=True, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise InputError(f"evidence is not canonically serialisable: {type(exc).__name__}")


def _epoch_of(label: str, material: Any) -> str:
    """A namespaced digest, so two epochs can never collide on equal material."""
    payload = _canonical({"label": label, "material": material})
    return f"{label}:{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:32]}"


def _digest(value: Any) -> str | None:
    return value.lower() if isinstance(value, str) and _SHA256.fullmatch(value) else None


def _mapping(value: Any) -> dict:
    return dict(value) if isinstance(value, Mapping) else {}


def _hashed_ref(entry: Any) -> dict | None:
    """A reference is usable only with a well-formed digest beside it."""
    item = _mapping(entry)
    digest = _digest(item.get("sha256"))
    if not _text(item.get("ref")) or digest is None:
        return None
    return {"ref": item["ref"].strip(), "sha256": digest}


def _policy_material(evidence: Mapping[str, Any]) -> tuple[Any, list[str]]:
    documents = evidence.get("documents")
    if not isinstance(documents, list) or not documents:
        return None, ["policy_documents_missing"]
    resolved = [_hashed_ref(entry) for entry in documents]
    if any(entry is None for entry in resolved):
        return None, ["policy_document_ref_or_digest_malformed"]
    ordered = sorted(resolved, key=lambda item: (item["ref"], item["sha256"]))
    refs = [item["ref"] for item in ordered]
    if len(set(refs)) != len(refs):
        return None, ["policy_documents_contain_duplicate_refs"]
    return ordered, []


def _single_material(evidence: Mapping[str, Any], reason: str) -> tuple[Any, list[str]]:
    resolved = _hashed_ref(evidence)
    return (resolved, []) if resolved else (None, [reason])


def _qualification_material(evidence: Mapping[str, Any]) -> tuple[Any, list[str]]:
    """A qualification epoch needs measured verdicts, not a model name."""
    reasons: list[str] = []
    report = _hashed_ref(evidence)
    if report is None:
        reasons.append("qualification_report_ref_or_digest_malformed")
    ids = evidence.get("evidence_ids")
    if not isinstance(ids, list) or not ids or not all(_text(x) for x in ids):
        reasons.append("qualification_evidence_ids_missing")
    elif len(set(ids)) != len(ids):
        reasons.append("qualification_evidence_ids_duplicated")
    verdicts = evidence.get("verdicts")
    if not isinstance(verdicts, Mapping) or not verdicts \
            or not all(_text(k) and _text(v) for k, v in verdicts.items()):
        reasons.append("qualification_verdicts_missing")
    if reasons:
        return None, reasons
    return {
        "report": report,
        "evidence_ids": sorted(ids),
        "verdicts": dict(sorted(verdicts.items())),
    }, []


def _profile_material(evidence: Mapping[str, Any]) -> tuple[Any, list[str]]:
    reasons: list[str] = []
    record = _hashed_ref(evidence)
    if record is None:
        reasons.append("profile_record_ref_or_digest_malformed")
    if not _text(evidence.get("profile_id")):
        reasons.append("profile_id_missing")
    if not _text(evidence.get("authorization_ref")):
        reasons.append("profile_authorization_ref_missing")
    if reasons:
        return None, reasons
    return {
        "record": record,
        "profile_id": evidence["profile_id"].strip(),
        "authorization_ref": evidence["authorization_ref"].strip(),
    }, []


def _native_material(binding: Mapping[str, Any]) -> tuple[Any, list[str]]:
    reasons: list[str] = []
    for field in ("agent_id", "session_id", "native_thread_id"):
        if not _text(binding.get(field)):
            reasons.append(f"native_identity_missing:{field}")
    if not _valid_process_epoch(dict(binding)):
        reasons.append("native_identity_missing:process_epoch")
    if reasons:
        return None, reasons
    return {field: binding.get(field) for field in NATIVE_IDENTITY_FIELDS}, []


def epoch_inputs() -> dict:
    """Describe what each epoch needs. Documentation the caller can read."""
    return {
        "policy_epoch": "evidence.policy.documents[]: {ref, sha256} each, deduplicated",
        "catalog_epoch": "evidence.catalog: {ref, sha256}",
        "qualification_epoch":
            "evidence.qualification: {ref, sha256, evidence_ids[], verdicts{}} - "
            "a model label alone is refused",
        "profile_epoch":
            "evidence.profile: {ref, sha256, profile_id, authorization_ref}",
        "native_epoch":
            "binding: agent_id, session_id, native_thread_id and a valid process "
            "epoch (native_pid + native_process_started_at)",
    }


def snapshot(evidence: Any, *, binding: Any) -> dict:
    """Build the epoch snapshot. Pure, deterministic, observation only.

    Raises ``InputError`` for structurally malformed input. Evidence that is
    merely absent or unusable yields an ``unknown`` entry, never a value.
    """
    if not isinstance(evidence, Mapping):
        raise InputError("evidence must be an object")
    if not isinstance(binding, Mapping):
        raise InputError("binding must be an object")
    encoded = _canonical({"evidence": dict(evidence), "binding": dict(binding)})
    if len(encoded.encode("utf-8")) > MAX_EVIDENCE_BYTES:
        raise InputError("evidence exceeds the snapshot size bound")

    builders = {
        "policy_epoch": lambda: _policy_material(_mapping(evidence.get("policy"))),
        "catalog_epoch": lambda: _single_material(
            _mapping(evidence.get("catalog")), "catalog_ref_or_digest_malformed"),
        "qualification_epoch": lambda: _qualification_material(
            _mapping(evidence.get("qualification"))),
        "profile_epoch": lambda: _profile_material(_mapping(evidence.get("profile"))),
        "native_epoch": lambda: _native_material(binding),
    }

    epochs: dict[str, str] = {}
    unknown: dict[str, list[str]] = {}
    derived_from: dict[str, Any] = {}
    for name in EPOCH_FIELDS:
        material, reasons = builders[name]()
        if reasons or material is None:
            unknown[name] = sorted(set(reasons)) or ["evidence_missing"]
            continue
        epochs[name] = _epoch_of(name, material)
        derived_from[name] = material

    return {
        "schema": EPOCH_SNAPSHOT_SCHEMA,
        # Only successfully derived epochs appear here. An incomplete snapshot
        # therefore parks in admission by construction, with no extra check.
        "epochs": epochs,
        "unknown": unknown,
        "complete": set(epochs) == set(EPOCH_FIELDS),
        "derived_from": derived_from,
        "binding_fields_seen": sorted(f for f in BINDING_FIELDS if f in binding),
        # Always false. A snapshot describes the world; it permits nothing, it
        # authenticates no provider, and it measures no quota.
        "execution_allowed": False,
        "switch_permitted": False,
        "provider_authenticated": False,
        "quota_verified": False,
    }


def to_admission_epochs(snap: Any) -> dict:
    """Extract the mapping ``bridge_task_admission.admit`` consumes.

    Unknown epochs are deliberately omitted rather than emitted as empty
    strings, so admission reports ``epoch_unknown:<name>`` and parks.
    """
    record = snap if isinstance(snap, Mapping) else None
    if record is None or record.get("schema") != EPOCH_SNAPSHOT_SCHEMA:
        raise InputError("not a policy epoch snapshot")
    return {name: value for name, value in _mapping(record.get("epochs")).items()
            if name in EPOCH_FIELDS and _text(value)}

# SPDX-License-Identifier: BUSL-1.1
# SPDX-FileCopyrightText: Jani Korpi / Ahkerat Mehilaiset / JKH Service
"""SolverSynthesizer v1 for the release operator-case pipeline.

This module is deliberately deterministic and narrow. It does not call an
LLM, read operator directories, log in to connectors, or activate solvers.
It converts already-sanitized DocIngest proposals and operator-case seeds
into the first concrete artifacts the existing v3.13.0 substrate can move
through SolverProvenance, ShadowRunner, and WriteRCOGate.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import copy
import re
from typing import Any, Mapping

from waggledance.core.v3_13_0.doc_ingest import DocIngestProposal
from waggledance.core.v3_13_0.solver_provenance import (
    SolverCandidateRecord,
    canonicalize_manifest,
)
from waggledance.core.v3_13_0.write_rco_gate import Intent, WriteRiskClass


REF_ID_RE = re.compile(r"^[a-z][a-z0-9_.:-]{2,127}$")
CASE_ID_RE = re.compile(
    r"^(?:case:[a-z][a-z0-9_.:-]{2,127}|[A-Z0-9]{2,16}-[0-9]{2}__[a-z0-9_]+__[a-z][a-z0-9_]*)$"
)
DOM_RE = re.compile(r"^DOM-(00[1-9]|0[1-9][0-9]|[1-9][0-9]{2})$")
SECRET_SOURCE_PREFIXES = ("credential:", "credentials:", "secret:", "token:")
SECRET_SOURCE_FILENAMES = (
    "credential.json",
    "credentials.json",
    "secret.json",
    "secrets.json",
    "token.json",
    ".env",
    ".pem",
    ".key",
)
RISK_ORDER = {
    WriteRiskClass.INFORMATIONAL.value: 0,
    WriteRiskClass.INTERNAL_MEMORY.value: 1,
    WriteRiskClass.LOCAL_ARTIFACT.value: 2,
    WriteRiskClass.EXTERNAL_EFFECT.value: 3,
}


class SolverSynthesizerError(ValueError):
    """Fail-closed synthesizer validation error."""


@dataclass(frozen=True)
class SolverTarget:
    """Runtime target the synthesized solver candidate is allowed to pursue."""

    target_domain: str
    target_write_risk: str
    target_state_ref: str
    tool_descriptor_id: str
    action: str = "insert"
    connector_ref: str | None = None
    source_tools: tuple[str, ...] = ()
    required_capabilities: tuple[str, ...] = ()
    failure_modes: tuple[str, ...] = ()
    first_solver_slice: str = "read_only_recommendation"
    accepted_differences: tuple[str, ...] = ()
    rejected_differences: tuple[str, ...] = ()
    shadow_expected_outputs: tuple[str, ...] | None = None
    operator_review_id: str | None = None

    def __post_init__(self) -> None:
        _validate_domain(self.target_domain, "target_domain")
        _validate_risk(self.target_write_risk)
        _validate_ref_id(self.target_state_ref, "target_state_ref")
        _validate_ref_id(self.tool_descriptor_id, "tool_descriptor_id")
        if self.connector_ref is not None:
            _validate_ref_id(self.connector_ref, "connector_ref")
        if self.target_write_risk == WriteRiskClass.EXTERNAL_EFFECT.value and \
                not self.connector_ref:
            raise SolverSynthesizerError(
                "external_effect targets require an explicit connector_ref"
            )
        if self.action not in {"insert", "update", "delete", "append",
                               "post", "patch", "put"}:
            raise SolverSynthesizerError(f"unsupported action: {self.action!r}")
        for value, label in (
            (self.source_tools, "source_tools"),
            (self.required_capabilities, "required_capabilities"),
            (self.failure_modes, "failure_modes"),
        ):
            _validate_non_empty_strings(value, label)
        if self.shadow_expected_outputs is not None:
            _validate_non_empty_strings(
                self.shadow_expected_outputs,
                "shadow_expected_outputs",
            )
        if self.operator_review_id is not None:
            _validate_ref_id(self.operator_review_id, "operator_review_id")


@dataclass(frozen=True)
class SynthesizedSolverCandidate:
    """Concrete candidate artifacts emitted by SolverSynthesizer v1."""

    manifest: dict[str, Any]
    manifest_canonical_json: str
    manifest_sha256: str
    candidate_record: SolverCandidateRecord
    target: SolverTarget
    profile_id: str
    profile_kind: str

    def to_bridge_payload(self) -> dict[str, Any]:
        """Payload data for a bridge handoff; not a new bridge type."""
        return {
            "kind": "solver",
            "event_type": "solver_candidate_manifest_synthesized",
            "candidate_id": self.candidate_record.candidate_id,
            "manifest_sha256": self.manifest_sha256,
            "profile_id": self.profile_id,
            "profile_kind": self.profile_kind,
            "target_domain": self.target.target_domain,
            "target_write_risk": self.target.target_write_risk,
            "target_state_ref": self.target.target_state_ref,
            "tool_descriptor_id": self.target.tool_descriptor_id,
            "connector_ref": self.target.connector_ref,
            "first_solver_slice": self.target.first_solver_slice,
            "required_capabilities": list(self.target.required_capabilities),
            "failure_modes": list(self.target.failure_modes),
        }

    def construct_intent(
        self,
        *,
        agent_id: str,
        session_id: str,
        payload: dict[str, Any],
    ) -> Intent:
        """Build a WriteRCOGate intent bound to the synthesized candidate."""
        return Intent.construct(
            agent_id=agent_id,
            session_id=session_id,
            tool_descriptor_id=self.target.tool_descriptor_id,
            target_state_ref=self.target.target_state_ref,
            action=self.target.action,
            payload={
                **payload,
                "candidate_id": self.candidate_record.candidate_id,
                "manifest_sha256": self.manifest_sha256,
            },
            connector_ref=self.target.connector_ref,
            provenance_chain=self.manifest_sha256,
        )


@dataclass(frozen=True)
class OperatorCaseSeed:
    """Sanitized operator case that can be mapped to capabilities/backlog."""

    case_id: str
    profiles: tuple[str, ...]
    source_refs: tuple[str, ...]
    connector_handles: tuple[str, ...]
    required_capabilities: tuple[str, ...]
    failure_modes: tuple[str, ...]
    decision_kind: str
    expected_output: Any
    risk_class: str
    first_solver_slice: str
    shadow_expected_output: str

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "OperatorCaseSeed":
        required = {
            "case_id",
            "profiles",
            "source_refs",
            "connector_handles",
            "required_capabilities",
            "failure_modes",
            "decision_kind",
            "expected_output",
            "risk_class",
            "first_solver_slice",
            "shadow_expected_output",
        }
        missing = sorted(required - set(raw))
        if missing:
            raise SolverSynthesizerError(
                f"operator case seed missing fields: {missing}"
            )
        seed = cls(
            case_id=str(raw["case_id"]),
            profiles=_tuple_of_strings(raw["profiles"], "profiles"),
            source_refs=_tuple_of_strings(raw["source_refs"], "source_refs"),
            connector_handles=_tuple_of_strings(
                raw["connector_handles"],
                "connector_handles",
            ),
            required_capabilities=_tuple_of_strings(
                raw["required_capabilities"],
                "required_capabilities",
            ),
            failure_modes=_tuple_of_strings(
                raw["failure_modes"],
                "failure_modes",
            ),
            decision_kind=str(raw["decision_kind"]),
            expected_output=copy.deepcopy(raw["expected_output"]),
            risk_class=str(raw["risk_class"]),
            first_solver_slice=str(raw["first_solver_slice"]),
            shadow_expected_output=str(raw["shadow_expected_output"]),
        )
        seed.validate()
        return seed

    def validate(self) -> None:
        _validate_case_id(self.case_id)
        _validate_risk(self.risk_class)
        for values, label in (
            (self.profiles, "profiles"),
            (self.source_refs, "source_refs"),
            (self.connector_handles, "connector_handles"),
            (self.required_capabilities, "required_capabilities"),
            (self.failure_modes, "failure_modes"),
        ):
            _validate_non_empty_strings(values, label)
        _validate_non_empty_strings(
            (self.decision_kind, self.first_solver_slice,
             self.shadow_expected_output),
            "operator case scalar fields",
        )
        if not self.expected_output:
            raise SolverSynthesizerError("expected_output is empty")
        for source_ref in self.source_refs:
            _validate_sanitized_source_ref(source_ref)
        for connector in self.connector_handles:
            _validate_ref_id(connector, "connector_handle")


@dataclass(frozen=True)
class CapabilityNode:
    """One shared capability and the cases/profiles it supports."""

    capability_id: str
    case_ids: tuple[str, ...]
    profiles: tuple[str, ...]
    connector_handles: tuple[str, ...]
    failure_modes: tuple[str, ...]
    risk_classes: tuple[str, ...]


@dataclass(frozen=True)
class SolverBacklogItem:
    """One prioritized case slice for the release solver backlog."""

    case_id: str
    profiles: tuple[str, ...]
    risk_class: str
    first_solver_slice: str
    required_capabilities: tuple[str, ...]
    failure_modes: tuple[str, ...]
    connector_handles: tuple[str, ...]
    shadow_expected_output: str
    priority_score: int


def synthesize_from_doc_ingest_proposal(
    proposal: DocIngestProposal,
    target: SolverTarget,
) -> SynthesizedSolverCandidate:
    """Turn a DocIngest proposal into a provenance-ready solver candidate."""
    seed = copy.deepcopy(proposal.candidate_manifest_seed)
    candidate_id = str(seed.get("candidate_id", ""))
    _validate_ref_id(candidate_id, "candidate_id")

    manifest = _manifest_from_seed(seed, target)
    canonical_json, manifest_sha256 = canonicalize_manifest(manifest)
    record = SolverCandidateRecord(
        candidate_id=candidate_id,
        manifest_canonical_json=canonical_json,
        manifest_sha256=manifest_sha256,
        target_domain=target.target_domain,
        target_write_risk=target.target_write_risk,
    )
    return SynthesizedSolverCandidate(
        manifest=manifest,
        manifest_canonical_json=canonical_json,
        manifest_sha256=manifest_sha256,
        candidate_record=record,
        target=target,
        profile_id=proposal.profile_id,
        profile_kind=proposal.profile_kind,
    )


def build_capability_graph(
    case_seeds: list[OperatorCaseSeed],
) -> dict[str, CapabilityNode]:
    """Project sanitized operator cases into shared capability nodes."""
    buckets: dict[str, dict[str, set[str]]] = {}
    for seed in case_seeds:
        seed.validate()
        for capability in seed.required_capabilities:
            bucket = buckets.setdefault(capability, {
                "case_ids": set(),
                "profiles": set(),
                "connector_handles": set(),
                "failure_modes": set(),
                "risk_classes": set(),
            })
            bucket["case_ids"].add(seed.case_id)
            bucket["profiles"].update(seed.profiles)
            bucket["connector_handles"].update(seed.connector_handles)
            bucket["failure_modes"].update(seed.failure_modes)
            bucket["risk_classes"].add(seed.risk_class)
    return {
        capability: CapabilityNode(
            capability_id=capability,
            case_ids=tuple(sorted(bucket["case_ids"])),
            profiles=tuple(sorted(bucket["profiles"])),
            connector_handles=tuple(sorted(bucket["connector_handles"])),
            failure_modes=tuple(sorted(bucket["failure_modes"])),
            risk_classes=tuple(sorted(bucket["risk_classes"])),
        )
        for capability, bucket in sorted(buckets.items())
    }


def rank_solver_backlog(
    case_seeds: list[OperatorCaseSeed],
) -> list[SolverBacklogItem]:
    """Prioritize operator cases for first release solver slices.

    The score is intentionally transparent: shared-profile coverage and
    failure-mode coverage move a case up; higher write risk moves it down
    until a read-only/local-artifact slice exists.
    """
    items: list[SolverBacklogItem] = []
    for seed in case_seeds:
        seed.validate()
        score = (
            len(set(seed.profiles)) * 10
            + len(set(seed.required_capabilities)) * 3
            + len(set(seed.failure_modes)) * 2
            - RISK_ORDER[seed.risk_class]
        )
        items.append(SolverBacklogItem(
            case_id=seed.case_id,
            profiles=tuple(sorted(set(seed.profiles))),
            risk_class=seed.risk_class,
            first_solver_slice=seed.first_solver_slice,
            required_capabilities=tuple(sorted(set(seed.required_capabilities))),
            failure_modes=tuple(sorted(set(seed.failure_modes))),
            connector_handles=tuple(sorted(set(seed.connector_handles))),
            shadow_expected_output=seed.shadow_expected_output,
            priority_score=score,
        ))
    return sorted(items, key=lambda item: (-item.priority_score, item.case_id))


def _manifest_from_seed(
    seed: dict[str, Any],
    target: SolverTarget,
) -> dict[str, Any]:
    manifest = copy.deepcopy(seed)
    _append_unique(manifest, "source_tools", target.source_tools)
    _append_unique(manifest, "state_handles", (target.target_state_ref,))
    if target.connector_ref:
        _append_unique(manifest, "connector_handles", (target.connector_ref,))
    if target.shadow_expected_outputs is not None:
        manifest["shadow_expected_outputs"] = list(target.shadow_expected_outputs)
    if target.accepted_differences:
        manifest["accepted_differences"] = list(target.accepted_differences)
    if target.rejected_differences:
        manifest["rejected_differences"] = list(target.rejected_differences)
    if target.operator_review_id is not None:
        manifest["operator_review_id"] = target.operator_review_id

    manifest["activation_state"] = "unactivated"
    manifest["promotion_decision"] = "awaiting_shadow"
    manifest["provenance_signatures"] = []
    _validate_manifest_refs(manifest)
    return manifest


def _append_unique(
    manifest: dict[str, Any],
    key: str,
    values: tuple[str, ...],
) -> None:
    current = list(manifest.get(key, []))
    for value in values:
        if value not in current:
            current.append(value)
    manifest[key] = current


def _validate_manifest_refs(manifest: dict[str, Any]) -> None:
    for key in (
        "source_docs",
        "source_tools",
        "training_contracts",
        "state_handles",
        "connector_handles",
        "shadow_inputs",
        "shadow_expected_outputs",
    ):
        values = manifest.get(key)
        if not isinstance(values, list):
            raise SolverSynthesizerError(f"manifest {key} must be a list")
        for value in values:
            _validate_ref_id(str(value), f"manifest {key}")
    for key in ("candidate_id", "rollback_plan", "operator_review_id"):
        _validate_ref_id(str(manifest.get(key, "")), f"manifest {key}")


def _validate_domain(value: str, label: str) -> None:
    if not DOM_RE.match(value):
        raise SolverSynthesizerError(f"{label} is not a DOM-* ref: {value!r}")


def _validate_ref_id(value: str, label: str) -> None:
    if not REF_ID_RE.match(value):
        raise SolverSynthesizerError(f"{label} is not a valid ref_id: {value!r}")


def _validate_case_id(value: str) -> None:
    if not CASE_ID_RE.match(value):
        raise SolverSynthesizerError(f"case_id is not valid: {value!r}")


def _validate_sanitized_source_ref(value: str) -> None:
    lower = value.casefold()
    if lower.startswith(SECRET_SOURCE_PREFIXES):
        raise SolverSynthesizerError(f"secret-like source_ref refused: {value!r}")
    if any(token in lower for token in ("password=", "passwd=", "api_key=")):
        raise SolverSynthesizerError(f"secret-like source_ref refused: {value!r}")
    path_tail = lower.rsplit("/", maxsplit=1)[-1].rsplit("\\", maxsplit=1)[-1]
    ref_tail = path_tail.split(":", maxsplit=1)[-1]
    if ref_tail in SECRET_SOURCE_FILENAMES:
        raise SolverSynthesizerError(f"secret-like source_ref refused: {value!r}")


def _validate_risk(value: str) -> None:
    if value not in RISK_ORDER:
        raise SolverSynthesizerError(f"unsupported risk_class: {value!r}")


def _validate_non_empty_strings(values: tuple[str, ...], label: str) -> None:
    if any(not str(value).strip() for value in values):
        raise SolverSynthesizerError(f"{label} contains an empty value")


def _tuple_of_strings(raw: Any, label: str) -> tuple[str, ...]:
    if isinstance(raw, (str, bytes)) or not isinstance(raw, (list, tuple)):
        raise SolverSynthesizerError(f"{label} must be a list of strings")
    values: list[str] = []
    for value in raw:
        if not isinstance(value, str):
            raise SolverSynthesizerError(f"{label} must be a list of strings")
        values.append(value)
    return tuple(values)


__all__ = [
    "CapabilityNode",
    "OperatorCaseSeed",
    "SolverBacklogItem",
    "SolverSynthesizerError",
    "SolverTarget",
    "SynthesizedSolverCandidate",
    "build_capability_graph",
    "rank_solver_backlog",
    "synthesize_from_doc_ingest_proposal",
]

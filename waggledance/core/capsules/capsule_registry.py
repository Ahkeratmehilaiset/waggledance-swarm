"""Capsule registry — Phase 9 §G.

In-memory registry of capsule manifests. Manifests are pure data;
the registry validates them against the capsule_manifest schema and
enforces blast-radius isolation.

Crown-jewel area waggledance/core/capsules/*
(BUSL Change Date 2030-03-19).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from . import CAPSULE_KINDS, CAPSULE_SCHEMA_VERSION


ALLOWED_LANES = (
    "provider_plane", "builder_lane", "solver_synthesis", "ingestion",
    "memory_tiers", "promotion", "self_inspection", "wait",
)
ALLOWED_PLANES = ("curiosity", "self_model", "dream", "resilience")


class CapsuleValidationError(ValueError):
    pass


@dataclass(frozen=True)
class CapsuleManifest:
    schema_version: int
    capsule_id: str
    capsule_kind: str
    version: int
    domain_neutral_core_required: bool
    blast_radius_isolated: bool
    allowed_lanes: tuple[str, ...]
    evidence_planes_subscribed: tuple[str, ...]
    policy_overrides: tuple[str, ...]
    description: str
    branch_name: str
    base_commit_hash: str
    pinned_input_manifest_sha256: str
    budget_caps: dict[str, float]

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "capsule_id": self.capsule_id,
            "capsule_kind": self.capsule_kind,
            "version": self.version,
            "description": self.description,
            "domain_neutral_core_required": self.domain_neutral_core_required,
            "blast_radius_isolated": self.blast_radius_isolated,
            "allowed_lanes": list(self.allowed_lanes),
            "evidence_planes_subscribed": list(self.evidence_planes_subscribed),
            "policy_overrides": list(self.policy_overrides),
            "budget_caps": dict(sorted(self.budget_caps.items())),
            "provenance": {
                "branch_name": self.branch_name,
                "base_commit_hash": self.base_commit_hash,
                "pinned_input_manifest_sha256":
                    self.pinned_input_manifest_sha256,
            },
        }


def make_manifest(*,
                       capsule_id: str,
                       capsule_kind: str,
                       version: int = 1,
                       allowed_lanes: Iterable[str] = (),
                       evidence_planes_subscribed: Iterable[str] = (),
                       policy_overrides: Iterable[str] = (),
                       description: str = "",
                       branch_name: str = "phase9/autonomy-fabric",
                       base_commit_hash: str = "",
                       pinned_input_manifest_sha256: str = "sha256:unknown",
                       budget_caps: dict[str, float] | None = None,
                       ) -> CapsuleManifest:
    if capsule_kind not in CAPSULE_KINDS:
        raise CapsuleValidationError(
            f"unknown capsule_kind {capsule_kind!r}; allowed: {CAPSULE_KINDS}"
        )
    for lane in allowed_lanes:
        if lane not in ALLOWED_LANES:
            raise CapsuleValidationError(
                f"unknown lane {lane!r}; allowed: {ALLOWED_LANES}"
            )
    for plane in evidence_planes_subscribed:
        if plane not in ALLOWED_PLANES:
            raise CapsuleValidationError(
                f"unknown evidence plane {plane!r}; allowed: {ALLOWED_PLANES}"
            )
    if not capsule_id or not capsule_id.replace("_", "").isalnum():
        raise CapsuleValidationError(
            f"capsule_id must be lowercase snake-case alnum; got {capsule_id!r}"
        )
    return CapsuleManifest(
        schema_version=CAPSULE_SCHEMA_VERSION,
        capsule_id=capsule_id,
        capsule_kind=capsule_kind,
        version=version,
        domain_neutral_core_required=True,
        blast_radius_isolated=True,
        allowed_lanes=tuple(sorted(set(allowed_lanes))),
        evidence_planes_subscribed=tuple(sorted(set(evidence_planes_subscribed))),
        policy_overrides=tuple(policy_overrides),
        description=description,
        branch_name=branch_name,
        base_commit_hash=base_commit_hash,
        pinned_input_manifest_sha256=pinned_input_manifest_sha256,
        budget_caps=dict(budget_caps or {}),
    )


# ── Registry (in-memory) ─────────────────────────────────────────-

@dataclass
class CapsuleRegistry:
    """In-memory capsule registry. Mutating operations return self
    after applying changes (registry is mutable; manifests inside
    are frozen)."""
    manifests: dict[str, CapsuleManifest] = field(default_factory=dict)

    def register(self, manifest: CapsuleManifest) -> "CapsuleRegistry":
        if manifest.capsule_id in self.manifests:
            existing = self.manifests[manifest.capsule_id]
            if existing.version >= manifest.version:
                raise CapsuleValidationError(
                    f"capsule {manifest.capsule_id!r} already at "
                    f"version {existing.version} ≥ {manifest.version}"
                )
        self.manifests[manifest.capsule_id] = manifest
        return self

    def get(self, capsule_id: str) -> CapsuleManifest | None:
        return self.manifests.get(capsule_id)

    def list_ids(self) -> list[str]:
        return sorted(self.manifests.keys())

    def to_dict(self) -> dict:
        return {
            "schema_version": CAPSULE_SCHEMA_VERSION,
            "capsules": {
                cid: m.to_dict()
                for cid, m in sorted(self.manifests.items())
            },
        }

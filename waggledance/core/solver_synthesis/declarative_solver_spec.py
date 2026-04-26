"""Declarative solver spec — Phase 9 §U1.

A SolverSpec is the declarative description of one concrete solver
instance, e.g. {family=scalar_unit_conversion, factor=1.8,
to_unit=fahrenheit}. Compilers turn these into solver artifacts.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass

from . import HEX_CELLS, SOLVER_FAMILY_KINDS, SOLVER_SYNTHESIS_SCHEMA_VERSION
from .solver_family_registry import SolverFamily, SolverFamilyRegistry


_SOLVER_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]{2,63}$")


@dataclass(frozen=True)
class SolverSpec:
    schema_version: int
    spec_id: str
    family_kind: str
    solver_name: str
    cell_id: str
    spec: dict
    source: str
    source_kind: str
    branch_name: str = ""
    base_commit_hash: str = ""
    pinned_input_manifest_sha256: str = ""

    def __post_init__(self) -> None:
        if self.family_kind not in SOLVER_FAMILY_KINDS:
            raise ValueError(
                f"unknown family_kind: {self.family_kind!r}; "
                f"allowed: {SOLVER_FAMILY_KINDS}"
            )
        if self.cell_id not in HEX_CELLS:
            raise ValueError(
                f"unknown cell_id: {self.cell_id!r}; allowed: {HEX_CELLS}"
            )
        if not _SOLVER_NAME_PATTERN.match(self.solver_name):
            raise ValueError(
                f"solver_name must match {_SOLVER_NAME_PATTERN.pattern}, "
                f"got {self.solver_name!r}"
            )

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "spec_id": self.spec_id,
            "family_kind": self.family_kind,
            "solver_name": self.solver_name,
            "cell_id": self.cell_id,
            "spec": dict(self.spec),
            "provenance": {
                "source": self.source,
                "source_kind": self.source_kind,
                "branch_name": self.branch_name,
                "base_commit_hash": self.base_commit_hash,
                "pinned_input_manifest_sha256":
                    self.pinned_input_manifest_sha256,
            },
        }


def compute_spec_id(*, family_kind: str, solver_name: str,
                          cell_id: str, spec: dict) -> str:
    """Deterministic structural id; identical specs collapse to same id."""
    canonical = json.dumps({
        "family_kind": family_kind,
        "solver_name": solver_name,
        "cell_id": cell_id,
        "spec": spec,
    }, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]


class SpecValidationError(ValueError):
    pass


def make_spec(*,
                  family_kind: str,
                  solver_name: str,
                  cell_id: str,
                  spec: dict,
                  source: str,
                  source_kind: str,
                  registry: SolverFamilyRegistry,
                  branch_name: str = "",
                  base_commit_hash: str = "",
                  pinned_input_manifest_sha256: str = "",
                  ) -> SolverSpec:
    fam = registry.get(family_kind)
    if fam is None:
        raise SpecValidationError(
            f"family {family_kind!r} not in registry"
        )
    missing = [k for k in fam.required_spec_keys if k not in spec]
    if missing:
        raise SpecValidationError(
            f"spec missing required keys for {family_kind!r}: {missing}"
        )
    sid = compute_spec_id(family_kind=family_kind,
                              solver_name=solver_name,
                              cell_id=cell_id, spec=spec)
    return SolverSpec(
        schema_version=SOLVER_SYNTHESIS_SCHEMA_VERSION,
        spec_id=sid,
        family_kind=family_kind,
        solver_name=solver_name,
        cell_id=cell_id,
        spec=dict(spec),
        source=source, source_kind=source_kind,
        branch_name=branch_name,
        base_commit_hash=base_commit_hash,
        pinned_input_manifest_sha256=pinned_input_manifest_sha256,
    )

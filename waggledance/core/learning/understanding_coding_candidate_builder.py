# SPDX-License-Identifier: Apache-2.0
"""Default-off inert coding-candidate compile/package preflight.

C8a accepts bounded Python source produced elsewhere, verifies a local
Genesis/hex binding, and asks one fixed controller-selected CPython worker in
the local trusted computing base (TCB) to parse and
compile the source without importing or executing it.  The controller then
reconstructs a deterministic, inert source package and returns an aggregate
receipt.  The candidate and its tests are never evaluated in this module or in
the worker.

This is not a generated-code sandbox.  A worktree, a subprocess, ``-I``, and a
direct-child timeout do not establish operating-system, filesystem, network,
secret, CPU, memory, disk, or process-tree isolation.  The resulting artifact
must not be loaded by C7 or any runtime path; a later OCI/confinement gate is
required before untrusted candidate code may execute.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, fields
from enum import Enum
from pathlib import Path
from typing import Any, BinaryIO

from waggledance.core.cell_identity import CellIdentityV1, verify_cell_identity
from waggledance.core.genesis_lineage import GenesisLineageV1, verify_lineage_entry
from waggledance.core.learning.understanding_contracts import HexCellAddressV1
from waggledance.core.magma.canonical import canonical_json_bytes, sha256_digest


CODING_CANDIDATE_PLAN_SCHEMA = "wd.understanding.coding_candidate_plan.v1"
CODING_CANDIDATE_REQUEST_SCHEMA = "wd.understanding.coding_candidate_request.v1"
CODING_CANDIDATE_RECEIPT_SCHEMA = "wd.understanding.coding_candidate_receipt.v1"
CODING_CANDIDATE_WORKER_PROTOCOL_SCHEMA = (
    "wd.understanding.coding_candidate_worker_protocol.v1"
)
CODING_CANDIDATE_SOURCE_MANIFEST_SCHEMA = (
    "wd.understanding.coding_candidate_source_manifest.v1"
)
CODING_CANDIDATE_ARTIFACT_MANIFEST_SCHEMA = (
    "wd.understanding.coding_candidate_artifact_manifest.v1"
)
CODING_CANDIDATE_ARTIFACT_SCHEMA = "wd.understanding.coding_candidate_artifact.v1"
CODING_CANDIDATE_ARTIFACT_FORMAT = "canonical-python-source-package-v1"
CODING_CANDIDATE_LANGUAGE = "python-source-utf8"
CODING_CANDIDATE_ENTRYPOINT = "solve"

_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
_ENCODING_COOKIE = re.compile(
    br"^[ \t\f]*\#.*?coding[:=][ \t]*[-_.A-Za-z0-9]+"
)

_WORKER_PATH = Path(__file__).with_name("_understanding_coding_candidate_worker.py")
_INTERPRETER_FLAGS = ("-I", "-S", "-E", "-B")
_MAX_CANONICAL_JSON_BYTES = 800_000

_HARD_LIMITS = {
    "max_solver_source_bytes": 262_144,
    "max_test_source_bytes": 262_144,
    "max_total_source_bytes": 524_288,
    "max_source_lines": 20_000,
    "max_tokens": 200_000,
    "max_ast_nodes": 100_000,
    "max_ast_depth": 256,
    "max_literal_bytes": 131_072,
    "max_integer_digits": 1_024,
    "max_worker_stdout_bytes": 262_144,
    "max_worker_stderr_bytes": 65_536,
    "max_wall_milliseconds": 30_000,
}

_INTERFACE_POLICY = {
    "schema": "wd.understanding.coding_candidate.interface.v1",
    "language": CODING_CANDIDATE_LANGUAGE,
    "entrypoint": CODING_CANDIDATE_ENTRYPOINT,
    "solver_file": "solver.py",
    "test_file": "test_solver.py",
    "solver_signature": "solve(payload)",
    "test_signature": "test_*()",
    "candidate_imported": False,
    "candidate_executed": False,
    "tests_executed": False,
}
_AST_POLICY = {
    "schema": "wd.understanding.coding_candidate.ast_compatibility.v1",
    "top_level": "optional_docstring_then_fixed_functions_only",
    "imports": "refused",
    "decorators_annotations_defaults": "refused",
    "nested_functions_classes_async_dunder": "refused",
    "call_screen": "non_name_callees_and_selected_builtin_references_refused",
    "while_and_power": "refused",
    "label": "compatibility_screen_not_safety_proof",
}
_PACKAGING_POLICY = {
    "schema": "wd.understanding.coding_candidate.packaging.v1",
    "format": CODING_CANDIDATE_ARTIFACT_FORMAT,
    "canonicalization": "magma-jcs-subset-v1",
    "content_encoding": "base64",
    "logical_files": ["solver.py", "test_solver.py"],
    "timestamps_pids_paths_randomness": "omitted",
    "bytecode": "omitted",
}

CODING_CANDIDATE_INTERFACE_DIGEST = sha256_digest(_INTERFACE_POLICY)
CODING_CANDIDATE_AST_POLICY_DIGEST = sha256_digest(_AST_POLICY)
CODING_CANDIDATE_PACKAGING_POLICY_DIGEST = sha256_digest(_PACKAGING_POLICY)


class CodingCandidateContractError(ValueError):
    """A value is outside the C8a static candidate contract."""


class CodingCandidateMode(str, Enum):
    OFF = "off"
    STATIC_SHADOW = "static_shadow"


class CodingCandidateBuildStatus(str, Enum):
    PACKAGED = "packaged"
    SOURCE_REJECTED = "source_rejected"
    WORKER_UNAVAILABLE = "worker_unavailable"
    WORKER_TIMEOUT = "worker_timeout"
    WORKER_OUTPUT_LIMIT = "worker_output_limit"
    WORKER_EXIT_ERROR = "worker_exit_error"
    PROTOCOL_ERROR = "protocol_error"
    DIGEST_MISMATCH = "digest_mismatch"


class CodingCandidateReasonCode(str, Enum):
    PACKAGED = "packaged"
    EMPTY_SOURCE = "empty_source"
    SOURCE_SIZE_REFUSED = "source_size_refused"
    TOTAL_SOURCE_SIZE_REFUSED = "total_source_size_refused"
    INVALID_SOURCE_ENCODING = "invalid_source_encoding"
    SOURCE_BOM_REFUSED = "source_bom_refused"
    SOURCE_NUL_REFUSED = "source_nul_refused"
    SOURCE_NEWLINE_REFUSED = "source_newline_refused"
    SOURCE_ENCODING_COOKIE_REFUSED = "source_encoding_cookie_refused"
    SOURCE_LINE_COUNT_REFUSED = "source_line_count_refused"
    SOURCE_POLICY_REFUSED = "source_policy_refused"
    SYNTAX_REFUSED = "syntax_refused"
    SOLVER_INTERFACE_REFUSED = "solver_interface_refused"
    TEST_INTERFACE_REFUSED = "test_interface_refused"
    AST_POLICY_REFUSED = "ast_policy_refused"
    COMPILER_REFUSED = "compiler_refused"
    WORKER_INTERNAL_ERROR = "worker_internal_error"
    WORKER_UNAVAILABLE = "worker_unavailable"
    WORKER_TIMEOUT = "worker_timeout"
    WORKER_OUTPUT_LIMIT = "worker_output_limit"
    WORKER_EXIT_ERROR = "worker_exit_error"
    WORKER_PROTOCOL_ERROR = "worker_protocol_error"
    WORKER_DIGEST_MISMATCH = "worker_digest_mismatch"


_STATUS_REASONS = {
    CodingCandidateBuildStatus.PACKAGED: frozenset({CodingCandidateReasonCode.PACKAGED}),
    CodingCandidateBuildStatus.SOURCE_REJECTED: frozenset(
        {
            CodingCandidateReasonCode.EMPTY_SOURCE,
            CodingCandidateReasonCode.SOURCE_SIZE_REFUSED,
            CodingCandidateReasonCode.TOTAL_SOURCE_SIZE_REFUSED,
            CodingCandidateReasonCode.INVALID_SOURCE_ENCODING,
            CodingCandidateReasonCode.SOURCE_BOM_REFUSED,
            CodingCandidateReasonCode.SOURCE_NUL_REFUSED,
            CodingCandidateReasonCode.SOURCE_NEWLINE_REFUSED,
            CodingCandidateReasonCode.SOURCE_ENCODING_COOKIE_REFUSED,
            CodingCandidateReasonCode.SOURCE_LINE_COUNT_REFUSED,
            CodingCandidateReasonCode.SOURCE_POLICY_REFUSED,
            CodingCandidateReasonCode.SYNTAX_REFUSED,
            CodingCandidateReasonCode.SOLVER_INTERFACE_REFUSED,
            CodingCandidateReasonCode.TEST_INTERFACE_REFUSED,
            CodingCandidateReasonCode.AST_POLICY_REFUSED,
            CodingCandidateReasonCode.COMPILER_REFUSED,
            CodingCandidateReasonCode.WORKER_INTERNAL_ERROR,
        }
    ),
    CodingCandidateBuildStatus.WORKER_UNAVAILABLE: frozenset(
        {CodingCandidateReasonCode.WORKER_UNAVAILABLE}
    ),
    CodingCandidateBuildStatus.WORKER_TIMEOUT: frozenset(
        {CodingCandidateReasonCode.WORKER_TIMEOUT}
    ),
    CodingCandidateBuildStatus.WORKER_OUTPUT_LIMIT: frozenset(
        {CodingCandidateReasonCode.WORKER_OUTPUT_LIMIT}
    ),
    CodingCandidateBuildStatus.WORKER_EXIT_ERROR: frozenset(
        {CodingCandidateReasonCode.WORKER_EXIT_ERROR}
    ),
    CodingCandidateBuildStatus.PROTOCOL_ERROR: frozenset(
        {CodingCandidateReasonCode.WORKER_PROTOCOL_ERROR}
    ),
    CodingCandidateBuildStatus.DIGEST_MISMATCH: frozenset(
        {CodingCandidateReasonCode.WORKER_DIGEST_MISMATCH}
    ),
}

_PARENT_PREFLIGHT_REASONS = frozenset(
    {
        CodingCandidateReasonCode.EMPTY_SOURCE,
        CodingCandidateReasonCode.SOURCE_SIZE_REFUSED,
        CodingCandidateReasonCode.TOTAL_SOURCE_SIZE_REFUSED,
        CodingCandidateReasonCode.INVALID_SOURCE_ENCODING,
        CodingCandidateReasonCode.SOURCE_BOM_REFUSED,
        CodingCandidateReasonCode.SOURCE_NUL_REFUSED,
        CodingCandidateReasonCode.SOURCE_NEWLINE_REFUSED,
        CodingCandidateReasonCode.SOURCE_ENCODING_COOKIE_REFUSED,
        CodingCandidateReasonCode.SOURCE_LINE_COUNT_REFUSED,
    }
)


def _require_sha256(value: object, label: str) -> str:
    if type(value) is not str or not _SHA256.fullmatch(value):
        raise CodingCandidateContractError(f"{label} must be a canonical sha256 digest")
    return value


def _require_token(value: object, label: str) -> str:
    if type(value) is not str or not _TOKEN.fullmatch(value):
        raise CodingCandidateContractError(f"{label} must be a bounded token")
    return value


def _require_positive_bounded_int(value: object, label: str, maximum: int) -> int:
    if type(value) is not int or value <= 0 or value > maximum:
        raise CodingCandidateContractError(
            f"{label} must be an exact positive integer at or below {maximum}"
        )
    return value


def _sha256_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _read_file_digest(path: Path, label: str) -> str:
    try:
        value = path.read_bytes()
    except OSError as exc:
        raise CodingCandidateContractError(f"fixed {label} artifact is unavailable") from exc
    if not value:
        raise CodingCandidateContractError(f"fixed {label} artifact is empty")
    return _sha256_bytes(value)


def _current_interpreter_binary_path() -> Path:
    if sys.implementation.name != "cpython":
        raise CodingCandidateContractError("C8a requires the fixed CPython compiler")
    if os.name == "nt":
        # Windows Store Python exposes an unreadable app-execution alias through
        # sys.executable.  Bind and launch the actual loaded image instead.
        import ctypes

        buffer = ctypes.create_unicode_buffer(32_768)
        length = ctypes.windll.kernel32.GetModuleFileNameW(  # type: ignore[attr-defined]
            None, buffer, len(buffer)
        )
        if length <= 0 or length >= len(buffer):
            raise CodingCandidateContractError(
                "fixed interpreter image path is unavailable"
            )
        path = Path(buffer.value)
    else:
        path = Path(sys.executable).resolve()
    if not path.is_absolute():
        raise CodingCandidateContractError("interpreter executable must be absolute")
    return path


def derive_current_coding_candidate_worker_digest() -> str:
    """Digest the one private worker selected by this module."""

    return _read_file_digest(_WORKER_PATH, "worker")


def derive_current_interpreter_artifact_digest() -> str:
    """Digest the absolute CPython executable used by the fixed launcher."""

    return _read_file_digest(_current_interpreter_binary_path(), "interpreter")


def derive_current_interpreter_identity_digest() -> str:
    artifact_digest = derive_current_interpreter_artifact_digest()
    return sha256_digest(
        {
            "domain": "wd.understanding.coding_candidate.interpreter_identity.v1",
            "implementation": sys.implementation.name,
            "version": [
                sys.version_info.major,
                sys.version_info.minor,
                sys.version_info.micro,
                sys.version_info.releaselevel,
                sys.version_info.serial,
            ],
            "cache_tag": sys.implementation.cache_tag,
            "artifact_digest": artifact_digest,
            "flags": list(_INTERPRETER_FLAGS),
        }
    )


@dataclass(frozen=True)
class CodingCandidatePolicyV1:
    """Local enablement and hard bounds; OFF is the shipped default."""

    mode: CodingCandidateMode = CodingCandidateMode.OFF
    max_solver_source_bytes: int = 65_536
    max_test_source_bytes: int = 65_536
    max_total_source_bytes: int = 131_072
    max_source_lines: int = 4_096
    max_tokens: int = 30_000
    max_ast_nodes: int = 20_000
    max_ast_depth: int = 128
    max_literal_bytes: int = 32_768
    max_integer_digits: int = 256
    max_worker_stdout_bytes: int = 65_536
    max_worker_stderr_bytes: int = 16_384
    max_wall_milliseconds: int = 5_000

    def __post_init__(self) -> None:
        if type(self.mode) is not CodingCandidateMode:
            raise CodingCandidateContractError("mode must be a CodingCandidateMode")
        for name, maximum in _HARD_LIMITS.items():
            _require_positive_bounded_int(getattr(self, name), name, maximum)
        if self.max_total_source_bytes < max(
            self.max_solver_source_bytes, self.max_test_source_bytes
        ):
            raise CodingCandidateContractError(
                "max_total_source_bytes must cover either individual source limit"
            )
        if self.max_worker_stdout_bytes < 4_096:
            raise CodingCandidateContractError(
                "max_worker_stdout_bytes is too small for the fixed protocol"
            )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "mode": self.mode.value,
            **{name: getattr(self, name) for name in _HARD_LIMITS},
        }

    @property
    def policy_digest(self) -> str:
        return sha256_digest(
            {
                "domain": "wd.understanding.coding_candidate_policy.v1",
                **self.to_mapping(),
            }
        )


def derive_hex_cell_address_digest(cell: HexCellAddressV1) -> str:
    if type(cell) is not HexCellAddressV1:
        raise CodingCandidateContractError("hex_cell must be an exact HexCellAddressV1")
    snapshot = cell.to_mapping()
    try:
        checked = HexCellAddressV1(**snapshot)
    except (TypeError, ValueError) as exc:
        raise CodingCandidateContractError("hex_cell is not locally self-consistent") from exc
    return sha256_digest(
        {
            "domain": "wd.understanding.coding_candidate.hex_cell_address.v1",
            **checked.to_mapping(),
        }
    )


@dataclass(frozen=True, repr=False)
class CodingCandidateCellBindingV1:
    """Private local identity/lineage/hex relation; no external pin authority."""

    hex_cell: HexCellAddressV1
    cell_identity: CellIdentityV1
    genesis_lineage_entry: GenesisLineageV1
    subdivision_address_digest: str
    registry_snapshot_digest: str

    def __post_init__(self) -> None:
        if type(self.hex_cell) is not HexCellAddressV1:
            raise CodingCandidateContractError("hex_cell must be an exact HexCellAddressV1")
        if type(self.cell_identity) is not CellIdentityV1:
            raise CodingCandidateContractError(
                "cell_identity must be an exact CellIdentityV1"
            )
        if type(self.genesis_lineage_entry) is not GenesisLineageV1:
            raise CodingCandidateContractError(
                "genesis_lineage_entry must be an exact GenesisLineageV1"
            )
        _require_sha256(self.subdivision_address_digest, "subdivision_address_digest")
        _require_sha256(self.registry_snapshot_digest, "registry_snapshot_digest")
        self._validated_mappings()

    def _validated_mappings(self) -> tuple[dict[str, Any], dict[str, Any]]:
        derive_hex_cell_address_digest(self.hex_cell)
        identity = self.cell_identity.to_mapping()
        lineage = self.genesis_lineage_entry.to_mapping()
        identity_ok, identity_reason = verify_cell_identity(identity)
        if not identity_ok:
            raise CodingCandidateContractError(
                f"cell identity recompute refused: {identity_reason}"
            )
        lineage_ok, lineage_reason = verify_lineage_entry(lineage)
        if not lineage_ok:
            raise CodingCandidateContractError(
                f"Genesis lineage entry recompute refused: {lineage_reason}"
            )
        if identity["cell_id"] != lineage["cell_id"]:
            raise CodingCandidateContractError(
                "cell identity and Genesis lineage entry bind different cells"
            )
        return identity, lineage

    @property
    def cell_identity_digest(self) -> str:
        identity, _ = self._validated_mappings()
        return identity["cell_id"]

    @property
    def genesis_lineage_entry_hash(self) -> str:
        _, lineage = self._validated_mappings()
        return lineage["entry_hash"]

    @property
    def hex_cell_address_digest(self) -> str:
        return derive_hex_cell_address_digest(self.hex_cell)

    @property
    def cell_binding_digest(self) -> str:
        return sha256_digest(
            {
                "domain": "wd.understanding.coding_candidate.cell_binding.v1",
                "hex_cell_address_digest": self.hex_cell_address_digest,
                "cell_identity_digest": self.cell_identity_digest,
                "genesis_lineage_entry_hash": self.genesis_lineage_entry_hash,
                "subdivision_address_digest": self.subdivision_address_digest,
                "registry_snapshot_digest": self.registry_snapshot_digest,
            }
        )


def _source_manifest_from_components(
    *,
    solver_digest: str,
    solver_byte_count: int,
    test_digest: str,
    test_byte_count: int,
) -> dict[str, Any]:
    _require_sha256(solver_digest, "solver_source_digest")
    _require_sha256(test_digest, "test_source_digest")
    for value, label in (
        (solver_byte_count, "solver_source_byte_count"),
        (test_byte_count, "test_source_byte_count"),
    ):
        if type(value) is not int or value < 0:
            raise CodingCandidateContractError(f"{label} must be non-negative")
    return {
        "schema_version": CODING_CANDIDATE_SOURCE_MANIFEST_SCHEMA,
        "language": CODING_CANDIDATE_LANGUAGE,
        "entrypoint": CODING_CANDIDATE_ENTRYPOINT,
        "files": [
            {
                "logical_name": "solver.py",
                "sha256": solver_digest,
                "byte_count": solver_byte_count,
            },
            {
                "logical_name": "test_solver.py",
                "sha256": test_digest,
                "byte_count": test_byte_count,
            },
        ],
    }


def _source_manifest(solver: bytes, tests: bytes) -> dict[str, Any]:
    return _source_manifest_from_components(
        solver_digest=_sha256_bytes(solver),
        solver_byte_count=len(solver),
        test_digest=_sha256_bytes(tests),
        test_byte_count=len(tests),
    )


def derive_coding_candidate_source_manifest_digest(
    solver_source_utf8: bytes, test_source_utf8: bytes
) -> str:
    if type(solver_source_utf8) is not bytes or type(test_source_utf8) is not bytes:
        raise CodingCandidateContractError("candidate sources must be exact bytes")
    return sha256_digest(
        {
            "domain": "wd.understanding.coding_candidate_source_manifest.digest.v1",
            **_source_manifest(solver_source_utf8, test_source_utf8),
        }
    )


@dataclass(frozen=True, repr=False)
class CodingCandidateSourcePackV1:
    solver_source_utf8: bytes
    test_source_utf8: bytes

    def __post_init__(self) -> None:
        if type(self.solver_source_utf8) is not bytes:
            raise CodingCandidateContractError("solver_source_utf8 must be exact bytes")
        if type(self.test_source_utf8) is not bytes:
            raise CodingCandidateContractError("test_source_utf8 must be exact bytes")

    @property
    def source_manifest_digest(self) -> str:
        return derive_coding_candidate_source_manifest_digest(
            self.solver_source_utf8, self.test_source_utf8
        )


@dataclass(frozen=True, repr=False)
class CodingCandidateAdmissionPlanV1:
    """Post-generation provenance/admission commitment for one static attempt."""

    campaign_id: str
    gap_evidence_digest: str
    hex_cell_address_digest: str
    cell_identity_digest: str
    genesis_lineage_entry_hash: str
    subdivision_address_digest: str
    registry_snapshot_digest: str
    cell_binding_digest: str
    source_manifest_digest: str
    generator_request_digest: str
    generator_response_digest: str
    generator_prompt_digest: str
    generator_model_digest: str
    generator_artifact_digest: str
    worker_artifact_digest: str
    interpreter_artifact_digest: str
    interpreter_identity_digest: str
    toolchain_digest: str
    environment_digest: str
    resource_policy_digest: str
    interface_contract_digest: str = CODING_CANDIDATE_INTERFACE_DIGEST
    ast_policy_digest: str = CODING_CANDIDATE_AST_POLICY_DIGEST
    packaging_policy_digest: str = CODING_CANDIDATE_PACKAGING_POLICY_DIGEST
    attempt_index: int = 1
    attempt_budget: int = 1
    candidate_only: bool = True
    shadow_only: bool = True
    static_admission_only: bool = True
    schema_version: str = CODING_CANDIDATE_PLAN_SCHEMA

    def __post_init__(self) -> None:
        if type(self.schema_version) is not str or (
            self.schema_version != CODING_CANDIDATE_PLAN_SCHEMA
        ):
            raise CodingCandidateContractError("plan schema_version refused")
        _require_token(self.campaign_id, "campaign_id")
        for name in (
            "gap_evidence_digest",
            "hex_cell_address_digest",
            "cell_identity_digest",
            "genesis_lineage_entry_hash",
            "subdivision_address_digest",
            "registry_snapshot_digest",
            "cell_binding_digest",
            "source_manifest_digest",
            "generator_request_digest",
            "generator_response_digest",
            "generator_prompt_digest",
            "generator_model_digest",
            "generator_artifact_digest",
            "worker_artifact_digest",
            "interpreter_artifact_digest",
            "interpreter_identity_digest",
            "toolchain_digest",
            "environment_digest",
            "resource_policy_digest",
            "interface_contract_digest",
            "ast_policy_digest",
            "packaging_policy_digest",
        ):
            _require_sha256(getattr(self, name), name)
        if self.interface_contract_digest != CODING_CANDIDATE_INTERFACE_DIGEST:
            raise CodingCandidateContractError("interface contract digest refused")
        if self.ast_policy_digest != CODING_CANDIDATE_AST_POLICY_DIGEST:
            raise CodingCandidateContractError("AST compatibility policy digest refused")
        if self.packaging_policy_digest != CODING_CANDIDATE_PACKAGING_POLICY_DIGEST:
            raise CodingCandidateContractError("packaging policy digest refused")
        if type(self.attempt_index) is not int or self.attempt_index != 1:
            raise CodingCandidateContractError("C8a requires attempt_index=1")
        if type(self.attempt_budget) is not int or self.attempt_budget != 1:
            raise CodingCandidateContractError("C8a requires one candidate attempt")
        for name in ("candidate_only", "shadow_only", "static_admission_only"):
            if getattr(self, name) is not True:
                raise CodingCandidateContractError(f"{name} must be literal true")

    def to_mapping(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "campaign_id": self.campaign_id,
            **{
                name: getattr(self, name)
                for name in (
                    "gap_evidence_digest",
                    "hex_cell_address_digest",
                    "cell_identity_digest",
                    "genesis_lineage_entry_hash",
                    "subdivision_address_digest",
                    "registry_snapshot_digest",
                    "cell_binding_digest",
                    "source_manifest_digest",
                    "generator_request_digest",
                    "generator_response_digest",
                    "generator_prompt_digest",
                    "generator_model_digest",
                    "generator_artifact_digest",
                    "worker_artifact_digest",
                    "interpreter_artifact_digest",
                    "interpreter_identity_digest",
                    "toolchain_digest",
                    "environment_digest",
                    "resource_policy_digest",
                    "interface_contract_digest",
                    "ast_policy_digest",
                    "packaging_policy_digest",
                )
            },
            "attempt_index": 1,
            "attempt_budget": 1,
            "candidate_only": True,
            "shadow_only": True,
            "static_admission_only": True,
        }

    @property
    def plan_digest(self) -> str:
        return sha256_digest(
            {
                "domain": "wd.understanding.coding_candidate_plan.digest.v1",
                **self.to_mapping(),
            }
        )


@dataclass(frozen=True, repr=False)
class CodingCandidateBuildRequestV1:
    plan: CodingCandidateAdmissionPlanV1
    source_pack: CodingCandidateSourcePackV1
    cell_binding: CodingCandidateCellBindingV1
    schema_version: str = CODING_CANDIDATE_REQUEST_SCHEMA

    def __post_init__(self) -> None:
        if type(self.schema_version) is not str or (
            self.schema_version != CODING_CANDIDATE_REQUEST_SCHEMA
        ):
            raise CodingCandidateContractError("request schema_version refused")
        if type(self.plan) is not CodingCandidateAdmissionPlanV1:
            raise CodingCandidateContractError(
                "plan must be an exact CodingCandidateAdmissionPlanV1"
            )
        if type(self.source_pack) is not CodingCandidateSourcePackV1:
            raise CodingCandidateContractError(
                "source_pack must be an exact CodingCandidateSourcePackV1"
            )
        if type(self.cell_binding) is not CodingCandidateCellBindingV1:
            raise CodingCandidateContractError(
                "cell_binding must be an exact CodingCandidateCellBindingV1"
            )
        expected = {
            "hex_cell_address_digest": self.cell_binding.hex_cell_address_digest,
            "cell_identity_digest": self.cell_binding.cell_identity_digest,
            "genesis_lineage_entry_hash": (
                self.cell_binding.genesis_lineage_entry_hash
            ),
            "subdivision_address_digest": self.cell_binding.subdivision_address_digest,
            "registry_snapshot_digest": self.cell_binding.registry_snapshot_digest,
            "cell_binding_digest": self.cell_binding.cell_binding_digest,
            "source_manifest_digest": self.source_pack.source_manifest_digest,
        }
        for name, value in expected.items():
            if getattr(self.plan, name) != value:
                raise CodingCandidateContractError(f"plan {name} binding mismatch")

    @property
    def request_digest(self) -> str:
        return sha256_digest(
            {
                "domain": "wd.understanding.coding_candidate_request.digest.v1",
                "schema_version": self.schema_version,
                "plan_digest": self.plan.plan_digest,
                "source_manifest_digest": self.source_pack.source_manifest_digest,
                "cell_binding_digest": self.cell_binding.cell_binding_digest,
            }
        )


def _artifact_manifest(
    request: CodingCandidateBuildRequestV1,
) -> dict[str, Any]:
    return {
        "schema_version": CODING_CANDIDATE_ARTIFACT_MANIFEST_SCHEMA,
        "format": CODING_CANDIDATE_ARTIFACT_FORMAT,
        "language": CODING_CANDIDATE_LANGUAGE,
        "entrypoint": CODING_CANDIDATE_ENTRYPOINT,
        "plan_digest": request.plan.plan_digest,
        "cell_binding_digest": request.cell_binding.cell_binding_digest,
        "source_manifest": _source_manifest(
            request.source_pack.solver_source_utf8,
            request.source_pack.test_source_utf8,
        ),
        "source_manifest_digest": request.source_pack.source_manifest_digest,
        "worker_artifact_digest": request.plan.worker_artifact_digest,
        "interpreter_artifact_digest": request.plan.interpreter_artifact_digest,
        "interpreter_identity_digest": request.plan.interpreter_identity_digest,
        "interface_contract_digest": request.plan.interface_contract_digest,
        "ast_policy_digest": request.plan.ast_policy_digest,
        "packaging_policy_digest": request.plan.packaging_policy_digest,
    }


def _build_artifact_bytes(
    request: CodingCandidateBuildRequestV1,
) -> tuple[bytes, str]:
    manifest = _artifact_manifest(request)
    manifest_digest = sha256_digest(
        {
            "domain": "wd.understanding.coding_candidate_artifact_manifest.digest.v1",
            **manifest,
        }
    )
    artifact = {
        "schema_version": CODING_CANDIDATE_ARTIFACT_SCHEMA,
        "format": CODING_CANDIDATE_ARTIFACT_FORMAT,
        "manifest": manifest,
        "manifest_digest": manifest_digest,
        "files": [
            {
                "logical_name": "solver.py",
                "encoding": "base64",
                "content": base64.b64encode(
                    request.source_pack.solver_source_utf8
                ).decode("ascii"),
            },
            {
                "logical_name": "test_solver.py",
                "encoding": "base64",
                "content": base64.b64encode(
                    request.source_pack.test_source_utf8
                ).decode("ascii"),
            },
        ],
    }
    return canonical_json_bytes(artifact), manifest_digest


def _strict_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if type(key) is not str or key in result:
            raise CodingCandidateContractError("canonical JSON contains duplicate keys")
        result[key] = value
    return result


def _decode_canonical_json(value: bytes, label: str) -> Any:
    if type(value) is not bytes:
        raise CodingCandidateContractError(f"{label} must be exact bytes")
    if len(value) > _MAX_CANONICAL_JSON_BYTES:
        raise CodingCandidateContractError(f"{label} exceeds the fixed byte bound")
    try:
        text = value.decode("utf-8", errors="strict")
        decoded = json.loads(text, object_pairs_hook=_strict_json_object)
        stack: list[tuple[Any, int]] = [(decoded, 1)]
        nodes = 0
        while stack:
            current, depth = stack.pop()
            nodes += 1
            if depth > 16 or nodes > 256:
                raise CodingCandidateContractError(
                    f"{label} exceeds the fixed JSON shape bound"
                )
            if type(current) is dict:
                stack.extend((child, depth + 1) for child in current.values())
            elif type(current) is list:
                stack.extend((child, depth + 1) for child in current)
            elif current is None or type(current) in (str, int, float, bool):
                continue
            else:
                raise CodingCandidateContractError(
                    f"{label} contains a non-JSON value"
                )
        canonical = canonical_json_bytes(decoded)
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        ValueError,
        TypeError,
        OverflowError,
        RecursionError,
    ) as exc:
        raise CodingCandidateContractError(f"{label} must be canonical JSON") from exc
    if canonical != value:
        raise CodingCandidateContractError(f"{label} must use canonical JSON bytes")
    return decoded


def _decode_artifact_source(file_entry: object, logical_name: str) -> bytes:
    if type(file_entry) is not dict or set(file_entry) != {
        "logical_name",
        "encoding",
        "content",
    }:
        raise CodingCandidateContractError("artifact file entry shape refused")
    if file_entry["logical_name"] != logical_name or file_entry["encoding"] != "base64":
        raise CodingCandidateContractError("artifact logical file identity refused")
    content = file_entry["content"]
    if type(content) is not str:
        raise CodingCandidateContractError("artifact content encoding refused")
    try:
        decoded = base64.b64decode(content.encode("ascii"), validate=True)
    except (UnicodeEncodeError, binascii.Error) as exc:
        raise CodingCandidateContractError("artifact content base64 refused") from exc
    if base64.b64encode(decoded).decode("ascii") != content:
        raise CodingCandidateContractError("artifact content base64 is not canonical")
    return decoded


def _inspect_artifact(value: bytes) -> dict[str, Any]:
    artifact = _decode_canonical_json(value, "artifact_bytes")
    if type(artifact) is not dict or set(artifact) != {
        "schema_version",
        "format",
        "manifest",
        "manifest_digest",
        "files",
    }:
        raise CodingCandidateContractError("artifact root shape refused")
    if (
        artifact["schema_version"] != CODING_CANDIDATE_ARTIFACT_SCHEMA
        or artifact["format"] != CODING_CANDIDATE_ARTIFACT_FORMAT
    ):
        raise CodingCandidateContractError("artifact schema or format refused")
    manifest = artifact["manifest"]
    manifest_keys = {
        "schema_version",
        "format",
        "language",
        "entrypoint",
        "plan_digest",
        "cell_binding_digest",
        "source_manifest",
        "source_manifest_digest",
        "worker_artifact_digest",
        "interpreter_artifact_digest",
        "interpreter_identity_digest",
        "interface_contract_digest",
        "ast_policy_digest",
        "packaging_policy_digest",
    }
    if type(manifest) is not dict or set(manifest) != manifest_keys:
        raise CodingCandidateContractError("artifact manifest shape refused")
    if (
        manifest["schema_version"] != CODING_CANDIDATE_ARTIFACT_MANIFEST_SCHEMA
        or manifest["format"] != CODING_CANDIDATE_ARTIFACT_FORMAT
        or manifest["language"] != CODING_CANDIDATE_LANGUAGE
        or manifest["entrypoint"] != CODING_CANDIDATE_ENTRYPOINT
    ):
        raise CodingCandidateContractError("artifact manifest identity refused")
    digest_names = manifest_keys - {
        "schema_version",
        "format",
        "language",
        "entrypoint",
        "source_manifest",
    }
    for name in digest_names:
        _require_sha256(manifest[name], f"artifact manifest {name}")
    expected_manifest_digest = sha256_digest(
        {
            "domain": "wd.understanding.coding_candidate_artifact_manifest.digest.v1",
            **manifest,
        }
    )
    if artifact["manifest_digest"] != expected_manifest_digest:
        raise CodingCandidateContractError("artifact manifest digest mismatch")
    files = artifact["files"]
    if type(files) is not list or len(files) != 2:
        raise CodingCandidateContractError("artifact file inventory refused")
    solver = _decode_artifact_source(files[0], "solver.py")
    tests = _decode_artifact_source(files[1], "test_solver.py")
    source_manifest = _source_manifest(solver, tests)
    if manifest["source_manifest"] != source_manifest:
        raise CodingCandidateContractError("artifact source manifest mismatch")
    expected_source_manifest_digest = sha256_digest(
        {
            "domain": "wd.understanding.coding_candidate_source_manifest.digest.v1",
            **source_manifest,
        }
    )
    if manifest["source_manifest_digest"] != expected_source_manifest_digest:
        raise CodingCandidateContractError("artifact source manifest digest mismatch")
    return {
        "artifact_digest": _sha256_bytes(value),
        "artifact_manifest_digest": expected_manifest_digest,
        "source_manifest_digest": expected_source_manifest_digest,
        "solver_source_digest": _sha256_bytes(solver),
        "test_source_digest": _sha256_bytes(tests),
        "byte_count": len(value),
        "manifest": manifest,
    }


@dataclass(frozen=True, repr=False)
class CodingCandidateArtifactV1:
    artifact_bytes: bytes
    artifact_digest: str
    artifact_manifest_digest: str
    source_manifest_digest: str
    solver_source_digest: str
    test_source_digest: str
    byte_count: int

    def __post_init__(self) -> None:
        if type(self.artifact_bytes) is not bytes:
            raise CodingCandidateContractError("artifact_bytes must be exact bytes")
        for name in (
            "artifact_digest",
            "artifact_manifest_digest",
            "source_manifest_digest",
            "solver_source_digest",
            "test_source_digest",
        ):
            _require_sha256(getattr(self, name), name)
        if type(self.byte_count) is not int or self.byte_count <= 0:
            raise CodingCandidateContractError("byte_count must be an exact positive integer")
        inspected = _inspect_artifact(self.artifact_bytes)
        for name in (
            "artifact_digest",
            "artifact_manifest_digest",
            "source_manifest_digest",
            "solver_source_digest",
            "test_source_digest",
            "byte_count",
        ):
            expected = inspected[name]
            if getattr(self, name) != expected:
                raise CodingCandidateContractError(f"artifact {name} mismatch")


_TRUE_RECEIPT_FIELDS = (
    "candidate_only",
    "shadow_only",
    "static_admission_only",
    "evaluation_only",
    "request_callback_seam_absent",
    "caller_command_seam_absent",
    "caller_environment_seam_absent",
    "caller_path_seam_absent",
    "candidate_source_not_executed",
    "candidate_tests_not_executed",
    "candidate_module_not_imported",
    "raw_material_omitted",
    "cell_identity_recomputed",
    "genesis_lineage_entry_recomputed",
    "local_identity_lineage_binding_matched",
    "hex_cell_binding_recomputed",
)

_FALSE_RECEIPT_FIELDS = (
    "candidate_executed",
    "tests_executed",
    "candidate_module_imported",
    "candidate_code_safety_verified",
    "behavioral_correctness_verified",
    "solver_lift_verified",
    "promotion_eligibility_claimed",
    "new_family_need_independently_verified",
    "existing_family_deduplication_independently_verified",
    "cross_campaign_single_attempt_enforced",
    "mass_custom_codebases_generated",
    "os_sandbox_applied",
    "generated_code_process_security_isolated",
    "filesystem_isolation_independently_verified",
    "network_isolation_independently_verified",
    "environment_secret_isolation_independently_verified",
    "cpu_memory_disk_quota_enforced",
    "process_tree_termination_enforced",
    "worker_identity_externally_authenticated",
    "interpreter_identity_externally_authenticated",
    "artifact_origin_authenticated",
    "independent_verification_applied",
    "genesis_origin_independently_verified",
    "echo_chamber_absence_verified",
    "genesis_registry_closure_verified",
    "genesis_root_externally_pinned",
    "hex_cell_binding_independently_verified",
    "registry_snapshot_identity_independently_verified",
    "provider_invoked",
    "builder_host_invoked",
    "c7_execution_requested",
    "hive_commit_applied",
    "magma_write_applied",
    "runtime_authority_requested",
    "routing_influence_requested",
    "solver_promotion_requested",
    "registry_write_requested",
    "product_external_system_writes_requested",
)


@dataclass(frozen=True)
class CodingCandidateBuildReceiptV1:
    plan_digest: str
    policy_digest: str
    request_digest: str
    cell_binding_digest: str
    hex_cell_address_digest: str
    cell_identity_digest: str
    genesis_lineage_entry_hash: str
    registry_snapshot_digest: str
    source_manifest_digest: str
    solver_source_digest: str
    solver_source_byte_count: int
    test_source_digest: str
    test_source_byte_count: int
    worker_artifact_digest: str
    interpreter_artifact_digest: str
    interpreter_identity_digest: str
    interface_contract_digest: str
    ast_policy_digest: str
    packaging_policy_digest: str
    status: CodingCandidateBuildStatus
    reason_code: CodingCandidateReasonCode
    artifact_digest: str | None
    artifact_manifest_digest: str | None
    artifact_byte_count: int
    max_solver_source_bytes: int
    max_test_source_bytes: int
    max_total_source_bytes: int
    max_source_lines: int
    max_tokens: int
    max_ast_nodes: int
    max_ast_depth: int
    max_literal_bytes: int
    max_integer_digits: int
    max_worker_stdout_bytes: int
    max_worker_stderr_bytes: int
    max_wall_milliseconds: int
    fixed_worker_digest_matched: bool
    interpreter_digest_matched: bool
    fresh_disposable_cwd_created_and_removed: bool
    worker_process_observed: bool
    direct_child_wall_timeout_enforced: bool
    direct_child_output_caps_enforced: bool
    direct_child_reaped: bool
    compatibility_screen_passed: bool
    source_compiled_without_execution: bool
    package_created: bool
    receipt_digest: str
    schema_version: str = CODING_CANDIDATE_RECEIPT_SCHEMA
    candidate_only: bool = True
    shadow_only: bool = True
    static_admission_only: bool = True
    evaluation_only: bool = True
    request_callback_seam_absent: bool = True
    caller_command_seam_absent: bool = True
    caller_environment_seam_absent: bool = True
    caller_path_seam_absent: bool = True
    candidate_source_not_executed: bool = True
    candidate_tests_not_executed: bool = True
    candidate_module_not_imported: bool = True
    raw_material_omitted: bool = True
    cell_identity_recomputed: bool = True
    genesis_lineage_entry_recomputed: bool = True
    local_identity_lineage_binding_matched: bool = True
    hex_cell_binding_recomputed: bool = True
    candidate_executed: bool = False
    tests_executed: bool = False
    candidate_module_imported: bool = False
    candidate_code_safety_verified: bool = False
    behavioral_correctness_verified: bool = False
    solver_lift_verified: bool = False
    promotion_eligibility_claimed: bool = False
    new_family_need_independently_verified: bool = False
    existing_family_deduplication_independently_verified: bool = False
    cross_campaign_single_attempt_enforced: bool = False
    mass_custom_codebases_generated: bool = False
    os_sandbox_applied: bool = False
    generated_code_process_security_isolated: bool = False
    filesystem_isolation_independently_verified: bool = False
    network_isolation_independently_verified: bool = False
    environment_secret_isolation_independently_verified: bool = False
    cpu_memory_disk_quota_enforced: bool = False
    process_tree_termination_enforced: bool = False
    worker_identity_externally_authenticated: bool = False
    interpreter_identity_externally_authenticated: bool = False
    artifact_origin_authenticated: bool = False
    independent_verification_applied: bool = False
    genesis_origin_independently_verified: bool = False
    echo_chamber_absence_verified: bool = False
    genesis_registry_closure_verified: bool = False
    genesis_root_externally_pinned: bool = False
    hex_cell_binding_independently_verified: bool = False
    registry_snapshot_identity_independently_verified: bool = False
    provider_invoked: bool = False
    builder_host_invoked: bool = False
    c7_execution_requested: bool = False
    hive_commit_applied: bool = False
    magma_write_applied: bool = False
    runtime_authority_requested: bool = False
    routing_influence_requested: bool = False
    solver_promotion_requested: bool = False
    registry_write_requested: bool = False
    product_external_system_writes_requested: bool = False

    def __post_init__(self) -> None:
        if type(self.schema_version) is not str or (
            self.schema_version != CODING_CANDIDATE_RECEIPT_SCHEMA
        ):
            raise CodingCandidateContractError("receipt schema_version refused")
        for name in (
            "plan_digest",
            "policy_digest",
            "request_digest",
            "cell_binding_digest",
            "hex_cell_address_digest",
            "cell_identity_digest",
            "genesis_lineage_entry_hash",
            "registry_snapshot_digest",
            "source_manifest_digest",
            "solver_source_digest",
            "test_source_digest",
            "worker_artifact_digest",
            "interpreter_artifact_digest",
            "interpreter_identity_digest",
            "interface_contract_digest",
            "ast_policy_digest",
            "packaging_policy_digest",
            "receipt_digest",
        ):
            _require_sha256(getattr(self, name), name)
        if type(self.status) is not CodingCandidateBuildStatus:
            raise CodingCandidateContractError("status must be a CodingCandidateBuildStatus")
        if type(self.reason_code) is not CodingCandidateReasonCode:
            raise CodingCandidateContractError(
                "reason_code must be a CodingCandidateReasonCode"
            )
        if self.reason_code not in _STATUS_REASONS[self.status]:
            raise CodingCandidateContractError("status and reason_code are inconsistent")
        for name in (
            "solver_source_byte_count",
            "test_source_byte_count",
            "artifact_byte_count",
        ):
            if type(getattr(self, name)) is not int or getattr(self, name) < 0:
                raise CodingCandidateContractError(f"{name} must be non-negative")
        policy_values = {name: getattr(self, name) for name in _HARD_LIMITS}
        expected_policy = CodingCandidatePolicyV1(
            mode=CodingCandidateMode.STATIC_SHADOW, **policy_values
        )
        if self.policy_digest != expected_policy.policy_digest:
            raise CodingCandidateContractError("receipt policy digest mismatch")
        fixed_digests = {
            "interface_contract_digest": CODING_CANDIDATE_INTERFACE_DIGEST,
            "ast_policy_digest": CODING_CANDIDATE_AST_POLICY_DIGEST,
            "packaging_policy_digest": CODING_CANDIDATE_PACKAGING_POLICY_DIGEST,
        }
        for name, expected in fixed_digests.items():
            if getattr(self, name) != expected:
                raise CodingCandidateContractError(f"receipt {name} mismatch")
        expected_source_manifest_digest = sha256_digest(
            {
                "domain": (
                    "wd.understanding.coding_candidate_source_manifest.digest.v1"
                ),
                **_source_manifest_from_components(
                    solver_digest=self.solver_source_digest,
                    solver_byte_count=self.solver_source_byte_count,
                    test_digest=self.test_source_digest,
                    test_byte_count=self.test_source_byte_count,
                ),
            }
        )
        if self.source_manifest_digest != expected_source_manifest_digest:
            raise CodingCandidateContractError(
                "receipt source manifest relation mismatch"
            )
        for name in _TRUE_RECEIPT_FIELDS:
            if getattr(self, name) is not True:
                raise CodingCandidateContractError(f"{name} must be literal true")
        for name in _FALSE_RECEIPT_FIELDS:
            if getattr(self, name) is not False:
                raise CodingCandidateContractError(f"{name} must be literal false")
        self._validate_execution_facts()
        expected_digest = sha256_digest(
            {
                "domain": "wd.understanding.coding_candidate_receipt.digest.v1",
                **self._core_mapping(),
            }
        )
        if self.receipt_digest != expected_digest:
            raise CodingCandidateContractError("receipt digest does not match fields")

    def _validate_execution_facts(self) -> None:
        preflight_refused = (
            self.status is CodingCandidateBuildStatus.SOURCE_REJECTED
            and self.reason_code in _PARENT_PREFLIGHT_REASONS
        )
        unavailable = self.status is CodingCandidateBuildStatus.WORKER_UNAVAILABLE
        launched = not preflight_refused and not unavailable
        matched = not preflight_refused
        expected = {
            "fixed_worker_digest_matched": matched,
            "interpreter_digest_matched": matched,
            "fresh_disposable_cwd_created_and_removed": matched,
            "worker_process_observed": launched,
            "direct_child_wall_timeout_enforced": launched,
            "direct_child_output_caps_enforced": launched,
            "direct_child_reaped": launched,
            "compatibility_screen_passed": (
                self.status is CodingCandidateBuildStatus.PACKAGED
            ),
            "source_compiled_without_execution": (
                self.status is CodingCandidateBuildStatus.PACKAGED
            ),
            "package_created": self.status is CodingCandidateBuildStatus.PACKAGED,
        }
        for name, value in expected.items():
            if getattr(self, name) is not value:
                raise CodingCandidateContractError(f"{name} is inconsistent")
        packaged = self.status is CodingCandidateBuildStatus.PACKAGED
        if packaged:
            _require_sha256(self.artifact_digest, "artifact_digest")
            _require_sha256(
                self.artifact_manifest_digest, "artifact_manifest_digest"
            )
            if self.artifact_byte_count <= 0:
                raise CodingCandidateContractError("packaged artifact must have bytes")
        elif (
            self.artifact_digest is not None
            or self.artifact_manifest_digest is not None
            or self.artifact_byte_count != 0
        ):
            raise CodingCandidateContractError(
                "non-packaged receipt cannot carry an artifact"
            )

    def _core_mapping(self) -> dict[str, Any]:
        names = (
            "schema_version",
            "plan_digest",
            "policy_digest",
            "request_digest",
            "cell_binding_digest",
            "hex_cell_address_digest",
            "cell_identity_digest",
            "genesis_lineage_entry_hash",
            "registry_snapshot_digest",
            "source_manifest_digest",
            "solver_source_digest",
            "solver_source_byte_count",
            "test_source_digest",
            "test_source_byte_count",
            "worker_artifact_digest",
            "interpreter_artifact_digest",
            "interpreter_identity_digest",
            "interface_contract_digest",
            "ast_policy_digest",
            "packaging_policy_digest",
            "artifact_digest",
            "artifact_manifest_digest",
            "artifact_byte_count",
            *_HARD_LIMITS.keys(),
            "fixed_worker_digest_matched",
            "interpreter_digest_matched",
            "fresh_disposable_cwd_created_and_removed",
            "worker_process_observed",
            "direct_child_wall_timeout_enforced",
            "direct_child_output_caps_enforced",
            "direct_child_reaped",
            "compatibility_screen_passed",
            "source_compiled_without_execution",
            "package_created",
            *_TRUE_RECEIPT_FIELDS,
            *_FALSE_RECEIPT_FIELDS,
        )
        values = {name: getattr(self, name) for name in names}
        values["status"] = self.status.value
        values["reason_code"] = self.reason_code.value
        return values

    def to_mapping(self) -> dict[str, Any]:
        return {**self._core_mapping(), "receipt_digest": self.receipt_digest}


@dataclass(frozen=True, repr=False)
class CodingCandidateBuildResultV1:
    artifact: CodingCandidateArtifactV1 | None
    receipt: CodingCandidateBuildReceiptV1

    def __post_init__(self) -> None:
        if type(self.receipt) is not CodingCandidateBuildReceiptV1:
            raise CodingCandidateContractError(
                "receipt must be an exact CodingCandidateBuildReceiptV1"
            )
        # Frozen dataclasses are ergonomic guards, not trust boundaries.  Rerun
        # their full validators so object.__setattr__ tampering cannot cross the
        # aggregate result boundary.
        self.receipt.__post_init__()
        packaged = self.receipt.status is CodingCandidateBuildStatus.PACKAGED
        if packaged:
            if type(self.artifact) is not CodingCandidateArtifactV1:
                raise CodingCandidateContractError("packaged result requires an artifact")
            self.artifact.__post_init__()
            inspected = _inspect_artifact(self.artifact.artifact_bytes)
            direct_relations = {
                "artifact_digest": "artifact_digest",
                "artifact_manifest_digest": "artifact_manifest_digest",
                "source_manifest_digest": "source_manifest_digest",
                "solver_source_digest": "solver_source_digest",
                "test_source_digest": "test_source_digest",
                "byte_count": "artifact_byte_count",
            }
            for artifact_name, receipt_name in direct_relations.items():
                if getattr(self.artifact, artifact_name) != getattr(
                    self.receipt, receipt_name
                ):
                    raise CodingCandidateContractError(
                        f"result {artifact_name} relation mismatch"
                    )
            manifest = inspected["manifest"]
            receipt_manifest_relations = {
                "plan_digest": "plan_digest",
                "cell_binding_digest": "cell_binding_digest",
                "worker_artifact_digest": "worker_artifact_digest",
                "interpreter_artifact_digest": "interpreter_artifact_digest",
                "interpreter_identity_digest": "interpreter_identity_digest",
                "interface_contract_digest": "interface_contract_digest",
                "ast_policy_digest": "ast_policy_digest",
                "packaging_policy_digest": "packaging_policy_digest",
            }
            for manifest_name, receipt_name in receipt_manifest_relations.items():
                if manifest[manifest_name] != getattr(self.receipt, receipt_name):
                    raise CodingCandidateContractError(
                        f"result manifest {manifest_name} relation mismatch"
                    )
        elif self.artifact is not None:
            raise CodingCandidateContractError("failed result cannot carry an artifact")


@dataclass(frozen=True)
class _WorkerOutcome:
    status: CodingCandidateBuildStatus
    reason_code: CodingCandidateReasonCode
    response: dict[str, Any] | None
    worker_process_observed: bool


class _CappedPipe:
    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.data = bytearray()
        self.overflow = threading.Event()

    def read(self, stream: BinaryIO) -> None:
        try:
            while True:
                chunk = stream.read(8_192)
                if not chunk:
                    break
                remaining = self.limit - len(self.data)
                if remaining > 0:
                    self.data.extend(chunk[:remaining])
                if len(chunk) > remaining:
                    self.overflow.set()
        except (OSError, ValueError):
            self.overflow.set()


def _source_line_count(value: bytes) -> int:
    if not value:
        return 0
    return value.count(b"\n") + (0 if value.endswith(b"\n") else 1)


def _preflight_sources(
    source_pack: CodingCandidateSourcePackV1, policy: CodingCandidatePolicyV1
) -> CodingCandidateReasonCode | None:
    solver = source_pack.solver_source_utf8
    tests = source_pack.test_source_utf8
    if not solver or not tests:
        return CodingCandidateReasonCode.EMPTY_SOURCE
    if len(solver) > policy.max_solver_source_bytes or len(tests) > policy.max_test_source_bytes:
        return CodingCandidateReasonCode.SOURCE_SIZE_REFUSED
    if len(solver) + len(tests) > policy.max_total_source_bytes:
        return CodingCandidateReasonCode.TOTAL_SOURCE_SIZE_REFUSED
    for value in (solver, tests):
        if value.startswith(b"\xef\xbb\xbf"):
            return CodingCandidateReasonCode.SOURCE_BOM_REFUSED
        if b"\x00" in value:
            return CodingCandidateReasonCode.SOURCE_NUL_REFUSED
        if b"\r" in value:
            return CodingCandidateReasonCode.SOURCE_NEWLINE_REFUSED
        if any(_ENCODING_COOKIE.search(line) for line in value.split(b"\n", 2)[:2]):
            return CodingCandidateReasonCode.SOURCE_ENCODING_COOKIE_REFUSED
        if _source_line_count(value) > policy.max_source_lines:
            return CodingCandidateReasonCode.SOURCE_LINE_COUNT_REFUSED
        try:
            value.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            return CodingCandidateReasonCode.INVALID_SOURCE_ENCODING
    return None


def _minimal_worker_environment() -> dict[str, str]:
    result: dict[str, str] = {}
    if os.name == "nt":
        for name in ("SystemRoot", "WINDIR"):
            value = os.environ.get(name)
            if value:
                result[name] = value
    return result


def _worker_command() -> tuple[str, ...]:
    executable = _current_interpreter_binary_path()
    worker = _WORKER_PATH.resolve(strict=True)
    return (str(executable), *_INTERPRETER_FLAGS, str(worker))


def _worker_payload(
    request: CodingCandidateBuildRequestV1, policy: CodingCandidatePolicyV1
) -> bytes:
    return canonical_json_bytes(
        {
            "schema_version": CODING_CANDIDATE_WORKER_PROTOCOL_SCHEMA,
            "policy": policy.to_mapping(),
            "policy_digest": policy.policy_digest,
            "plan_digest": request.plan.plan_digest,
            "cell_binding_digest": request.cell_binding.cell_binding_digest,
            "source_manifest_digest": request.source_pack.source_manifest_digest,
            "worker_artifact_digest": request.plan.worker_artifact_digest,
            "interpreter_artifact_digest": request.plan.interpreter_artifact_digest,
            "interpreter_identity_digest": request.plan.interpreter_identity_digest,
            "interface_contract_digest": request.plan.interface_contract_digest,
            "ast_policy_digest": request.plan.ast_policy_digest,
            "packaging_policy_digest": request.plan.packaging_policy_digest,
            "solver_source_b64": base64.b64encode(
                request.source_pack.solver_source_utf8
            ).decode("ascii"),
            "test_source_b64": base64.b64encode(
                request.source_pack.test_source_utf8
            ).decode("ascii"),
        }
    )


def _write_stdin(stream: BinaryIO, value: bytes) -> None:
    try:
        stream.write(value)
        stream.flush()
    except (BrokenPipeError, OSError, ValueError):
        pass
    finally:
        try:
            stream.close()
        except (OSError, ValueError):
            pass


def _kill_and_reap(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is None:
        try:
            process.kill()
        except OSError:
            pass
    try:
        process.wait(timeout=2.0)
    except (OSError, subprocess.TimeoutExpired):
        try:
            process.kill()
        except OSError:
            pass
        try:
            process.wait(timeout=2.0)
        except (OSError, subprocess.TimeoutExpired):
            raise CodingCandidateContractError(
                "fixed direct worker could not be reaped"
            )
    if process.poll() is None:
        raise CodingCandidateContractError("fixed direct worker could not be reaped")


def _close_pipe(stream: BinaryIO) -> None:
    try:
        stream.close()
    except (OSError, ValueError):
        pass


def _finalize_direct_worker(
    process: subprocess.Popen[bytes],
    streams: tuple[BinaryIO, ...],
    threads: tuple[threading.Thread, ...],
) -> None:
    """Fail closed after every post-Popen path and prove direct-child reap."""

    cleanup_error: BaseException | None = None
    try:
        _kill_and_reap(process)
    except BaseException as exc:  # preserve the fail-closed cleanup failure
        cleanup_error = exc
    for thread in threads:
        if thread.ident is not None:
            try:
                thread.join(timeout=2.0)
            except RuntimeError as exc:
                cleanup_error = cleanup_error or exc
    for stream in streams:
        _close_pipe(stream)
    for thread in threads:
        if thread.ident is not None and thread.is_alive():
            thread.join(timeout=2.0)
    if any(thread.ident is not None and thread.is_alive() for thread in threads):
        cleanup_error = cleanup_error or CodingCandidateContractError(
            "fixed direct worker pipe thread did not terminate"
        )
    if cleanup_error is not None:
        raise cleanup_error


def _parse_worker_response(raw: bytes) -> dict[str, Any]:
    value = _decode_canonical_json(raw, "worker response")
    if type(value) is not dict:
        raise CodingCandidateContractError("worker response must be an object")
    return value


def _invoke_worker(
    request: CodingCandidateBuildRequestV1, policy: CodingCandidatePolicyV1
) -> _WorkerOutcome:
    payload = _worker_payload(request, policy)
    with tempfile.TemporaryDirectory(prefix="wd-c8a-static-") as temporary:
        try:
            process = subprocess.Popen(  # noqa: S603 - fixed absolute argv, no shell
                _worker_command(),
                cwd=temporary,
                env=_minimal_worker_environment(),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
                close_fds=True,
                bufsize=0,
            )
        except (OSError, subprocess.SubprocessError):
            return _WorkerOutcome(
                status=CodingCandidateBuildStatus.WORKER_UNAVAILABLE,
                reason_code=CodingCandidateReasonCode.WORKER_UNAVAILABLE,
                response=None,
                worker_process_observed=False,
            )
        streams: tuple[BinaryIO, ...] = ()
        threads: tuple[threading.Thread, ...] = ()
        stdout: _CappedPipe | None = None
        stderr: _CappedPipe | None = None
        terminal: CodingCandidateBuildStatus | None = None
        try:
            streams = tuple(
                stream
                for stream in (process.stdin, process.stdout, process.stderr)
                if stream is not None
            )
            if len(streams) != 3:
                raise CodingCandidateContractError(
                    "fixed direct worker pipes are unavailable"
                )
            stdin_stream, stdout_stream, stderr_stream = streams
            stdout = _CappedPipe(policy.max_worker_stdout_bytes)
            stderr = _CappedPipe(policy.max_worker_stderr_bytes)
            threads = (
                threading.Thread(
                    target=_write_stdin,
                    args=(stdin_stream, payload),
                    daemon=True,
                ),
                threading.Thread(
                    target=stdout.read,
                    args=(stdout_stream,),
                    daemon=True,
                ),
                threading.Thread(
                    target=stderr.read,
                    args=(stderr_stream,),
                    daemon=True,
                ),
            )
            for thread in threads:
                thread.start()
            deadline = time.monotonic() + policy.max_wall_milliseconds / 1_000.0
            while process.poll() is None:
                if stdout.overflow.is_set() or stderr.overflow.is_set():
                    terminal = CodingCandidateBuildStatus.WORKER_OUTPUT_LIMIT
                    break
                if time.monotonic() >= deadline:
                    terminal = CodingCandidateBuildStatus.WORKER_TIMEOUT
                    break
                time.sleep(0.005)
        finally:
            _finalize_direct_worker(
                process,
                streams,
                threads,
            )
        if stdout is None or stderr is None:
            raise CodingCandidateContractError("fixed direct worker capture unavailable")
        if stdout.overflow.is_set() or stderr.overflow.is_set():
            return _WorkerOutcome(
                status=CodingCandidateBuildStatus.WORKER_OUTPUT_LIMIT,
                reason_code=CodingCandidateReasonCode.WORKER_OUTPUT_LIMIT,
                response=None,
                worker_process_observed=True,
            )
        if terminal is CodingCandidateBuildStatus.WORKER_TIMEOUT:
            return _WorkerOutcome(
                status=CodingCandidateBuildStatus.WORKER_TIMEOUT,
                reason_code=CodingCandidateReasonCode.WORKER_TIMEOUT,
                response=None,
                worker_process_observed=True,
            )
        if process.returncode != 0:
            return _WorkerOutcome(
                status=CodingCandidateBuildStatus.WORKER_EXIT_ERROR,
                reason_code=CodingCandidateReasonCode.WORKER_EXIT_ERROR,
                response=None,
                worker_process_observed=True,
            )
        try:
            response = _parse_worker_response(bytes(stdout.data))
        except CodingCandidateContractError:
            return _WorkerOutcome(
                status=CodingCandidateBuildStatus.PROTOCOL_ERROR,
                reason_code=CodingCandidateReasonCode.WORKER_PROTOCOL_ERROR,
                response=None,
                worker_process_observed=True,
            )
        return _classify_worker_response(request, policy, response)


def _classify_worker_response(
    request: CodingCandidateBuildRequestV1,
    policy: CodingCandidatePolicyV1,
    response: dict[str, Any],
) -> _WorkerOutcome:
    base_keys = {
        "schema_version",
        "status",
        "reason_code",
        "plan_digest",
        "cell_binding_digest",
        "source_manifest_digest",
        "policy_digest",
    }
    packaged_keys = base_keys | {
        "solver_source_digest",
        "test_source_digest",
        "artifact_manifest_digest",
        "artifact_digest",
        "artifact_byte_count",
    }
    status_value = response.get("status")
    expected_keys = packaged_keys if status_value == "packaged" else base_keys
    if set(response) != expected_keys or response.get("schema_version") != (
        CODING_CANDIDATE_WORKER_PROTOCOL_SCHEMA
    ):
        return _WorkerOutcome(
            CodingCandidateBuildStatus.PROTOCOL_ERROR,
            CodingCandidateReasonCode.WORKER_PROTOCOL_ERROR,
            None,
            True,
        )
    echoes = {
        "plan_digest": request.plan.plan_digest,
        "cell_binding_digest": request.cell_binding.cell_binding_digest,
        "source_manifest_digest": request.source_pack.source_manifest_digest,
        "policy_digest": policy.policy_digest,
    }
    if any(response.get(name) != value for name, value in echoes.items()):
        return _WorkerOutcome(
            CodingCandidateBuildStatus.DIGEST_MISMATCH,
            CodingCandidateReasonCode.WORKER_DIGEST_MISMATCH,
            None,
            True,
        )
    if status_value == "source_rejected":
        worker_reason_map = {
            "source_policy_refused": CodingCandidateReasonCode.SOURCE_POLICY_REFUSED,
            "invalid_source_encoding": (
                CodingCandidateReasonCode.INVALID_SOURCE_ENCODING
            ),
            "syntax_refused": CodingCandidateReasonCode.SYNTAX_REFUSED,
            "solver_interface_refused": CodingCandidateReasonCode.SOLVER_INTERFACE_REFUSED,
            "test_interface_refused": CodingCandidateReasonCode.TEST_INTERFACE_REFUSED,
            "ast_policy_refused": CodingCandidateReasonCode.AST_POLICY_REFUSED,
            "compiler_refused": CodingCandidateReasonCode.COMPILER_REFUSED,
            "internal_error": CodingCandidateReasonCode.WORKER_INTERNAL_ERROR,
        }
        reason = worker_reason_map.get(response.get("reason_code"))
        if reason is None:
            return _WorkerOutcome(
                CodingCandidateBuildStatus.PROTOCOL_ERROR,
                CodingCandidateReasonCode.WORKER_PROTOCOL_ERROR,
                None,
                True,
            )
        return _WorkerOutcome(
            CodingCandidateBuildStatus.SOURCE_REJECTED,
            reason,
            response,
            True,
        )
    if status_value != "packaged" or response.get("reason_code") != "packaged":
        return _WorkerOutcome(
            CodingCandidateBuildStatus.PROTOCOL_ERROR,
            CodingCandidateReasonCode.WORKER_PROTOCOL_ERROR,
            None,
            True,
        )
    for name in (
        "solver_source_digest",
        "test_source_digest",
        "artifact_manifest_digest",
        "artifact_digest",
    ):
        try:
            _require_sha256(response.get(name), f"worker response {name}")
        except CodingCandidateContractError:
            return _WorkerOutcome(
                CodingCandidateBuildStatus.PROTOCOL_ERROR,
                CodingCandidateReasonCode.WORKER_PROTOCOL_ERROR,
                None,
                True,
            )
    if type(response.get("artifact_byte_count")) is not int or response[
        "artifact_byte_count"
    ] <= 0:
        return _WorkerOutcome(
            CodingCandidateBuildStatus.PROTOCOL_ERROR,
            CodingCandidateReasonCode.WORKER_PROTOCOL_ERROR,
            None,
            True,
        )
    return _WorkerOutcome(
        CodingCandidateBuildStatus.PACKAGED,
        CodingCandidateReasonCode.PACKAGED,
        response,
        True,
    )


def _receipt_core_from_values(values: dict[str, Any]) -> dict[str, Any]:
    names = (
        "plan_digest",
        "policy_digest",
        "request_digest",
        "cell_binding_digest",
        "hex_cell_address_digest",
        "cell_identity_digest",
        "genesis_lineage_entry_hash",
        "registry_snapshot_digest",
        "source_manifest_digest",
        "solver_source_digest",
        "solver_source_byte_count",
        "test_source_digest",
        "test_source_byte_count",
        "worker_artifact_digest",
        "interpreter_artifact_digest",
        "interpreter_identity_digest",
        "interface_contract_digest",
        "ast_policy_digest",
        "packaging_policy_digest",
        "artifact_digest",
        "artifact_manifest_digest",
        "artifact_byte_count",
        *_HARD_LIMITS.keys(),
        "fixed_worker_digest_matched",
        "interpreter_digest_matched",
        "fresh_disposable_cwd_created_and_removed",
        "worker_process_observed",
        "direct_child_wall_timeout_enforced",
        "direct_child_output_caps_enforced",
        "direct_child_reaped",
        "compatibility_screen_passed",
        "source_compiled_without_execution",
        "package_created",
    )
    core = {
        "schema_version": CODING_CANDIDATE_RECEIPT_SCHEMA,
        **{name: values[name] for name in names},
        "status": values["status"].value,
        "reason_code": values["reason_code"].value,
    }
    core.update({name: True for name in _TRUE_RECEIPT_FIELDS})
    core.update({name: False for name in _FALSE_RECEIPT_FIELDS})
    return core


def _make_receipt(
    *,
    request: CodingCandidateBuildRequestV1,
    policy: CodingCandidatePolicyV1,
    status: CodingCandidateBuildStatus,
    reason_code: CodingCandidateReasonCode,
    artifact: CodingCandidateArtifactV1 | None,
    temporary_cwd_created_and_removed: bool,
    worker_process_observed: bool,
) -> CodingCandidateBuildReceiptV1:
    preflight_refused = (
        status is CodingCandidateBuildStatus.SOURCE_REJECTED
        and reason_code in _PARENT_PREFLIGHT_REASONS
    )
    matched = not preflight_refused
    launched = worker_process_observed
    packaged = status is CodingCandidateBuildStatus.PACKAGED
    values: dict[str, Any] = {
        "plan_digest": request.plan.plan_digest,
        "policy_digest": policy.policy_digest,
        "request_digest": request.request_digest,
        "cell_binding_digest": request.cell_binding.cell_binding_digest,
        "hex_cell_address_digest": request.cell_binding.hex_cell_address_digest,
        "cell_identity_digest": request.cell_binding.cell_identity_digest,
        "genesis_lineage_entry_hash": (
            request.cell_binding.genesis_lineage_entry_hash
        ),
        "registry_snapshot_digest": request.cell_binding.registry_snapshot_digest,
        "source_manifest_digest": request.source_pack.source_manifest_digest,
        "solver_source_digest": _sha256_bytes(
            request.source_pack.solver_source_utf8
        ),
        "solver_source_byte_count": len(request.source_pack.solver_source_utf8),
        "test_source_digest": _sha256_bytes(request.source_pack.test_source_utf8),
        "test_source_byte_count": len(request.source_pack.test_source_utf8),
        "worker_artifact_digest": request.plan.worker_artifact_digest,
        "interpreter_artifact_digest": request.plan.interpreter_artifact_digest,
        "interpreter_identity_digest": request.plan.interpreter_identity_digest,
        "interface_contract_digest": request.plan.interface_contract_digest,
        "ast_policy_digest": request.plan.ast_policy_digest,
        "packaging_policy_digest": request.plan.packaging_policy_digest,
        "status": status,
        "reason_code": reason_code,
        "artifact_digest": artifact.artifact_digest if artifact else None,
        "artifact_manifest_digest": (
            artifact.artifact_manifest_digest if artifact else None
        ),
        "artifact_byte_count": artifact.byte_count if artifact else 0,
        **{name: getattr(policy, name) for name in _HARD_LIMITS},
        "fixed_worker_digest_matched": matched,
        "interpreter_digest_matched": matched,
        "fresh_disposable_cwd_created_and_removed": (
            temporary_cwd_created_and_removed
        ),
        "worker_process_observed": worker_process_observed,
        "direct_child_wall_timeout_enforced": launched,
        "direct_child_output_caps_enforced": launched,
        "direct_child_reaped": launched,
        "compatibility_screen_passed": packaged,
        "source_compiled_without_execution": packaged,
        "package_created": packaged,
    }
    return CodingCandidateBuildReceiptV1(
        **values,
        receipt_digest=sha256_digest(
            {
                "domain": "wd.understanding.coding_candidate_receipt.digest.v1",
                **_receipt_core_from_values(values),
            }
        ),
    )


def _revalidate_request(
    request: CodingCandidateBuildRequestV1, policy: CodingCandidatePolicyV1
) -> None:
    if request.plan.resource_policy_digest != policy.policy_digest:
        raise CodingCandidateContractError("plan resource policy digest mismatch")
    actual_worker = derive_current_coding_candidate_worker_digest()
    if request.plan.worker_artifact_digest != actual_worker:
        raise CodingCandidateContractError("fixed worker artifact digest mismatch")
    actual_interpreter = derive_current_interpreter_artifact_digest()
    if request.plan.interpreter_artifact_digest != actual_interpreter:
        raise CodingCandidateContractError("fixed interpreter artifact digest mismatch")
    actual_identity = derive_current_interpreter_identity_digest()
    if request.plan.interpreter_identity_digest != actual_identity:
        raise CodingCandidateContractError("fixed interpreter identity digest mismatch")


def _snapshot_policy(policy: CodingCandidatePolicyV1) -> CodingCandidatePolicyV1:
    """Re-run every raw policy field after the intentional OFF short-circuit."""

    return CodingCandidatePolicyV1(
        **{field.name: getattr(policy, field.name) for field in fields(policy)}
    )


def _revalidate_request_bindings(
    request: CodingCandidateBuildRequestV1,
) -> CodingCandidateBuildRequestV1:
    """Return a fresh exact snapshot after revalidating every nested field."""

    if type(request.plan) is not CodingCandidateAdmissionPlanV1:
        raise CodingCandidateContractError(
            "plan must be an exact CodingCandidateAdmissionPlanV1"
        )
    if type(request.source_pack) is not CodingCandidateSourcePackV1:
        raise CodingCandidateContractError(
            "source_pack must be an exact CodingCandidateSourcePackV1"
        )
    if type(request.cell_binding) is not CodingCandidateCellBindingV1:
        raise CodingCandidateContractError(
            "cell_binding must be an exact CodingCandidateCellBindingV1"
        )
    binding = request.cell_binding
    if type(binding.hex_cell) is not HexCellAddressV1:
        raise CodingCandidateContractError("hex_cell must be an exact HexCellAddressV1")
    if type(binding.cell_identity) is not CellIdentityV1:
        raise CodingCandidateContractError(
            "cell_identity must be an exact CellIdentityV1"
        )
    if type(binding.genesis_lineage_entry) is not GenesisLineageV1:
        raise CodingCandidateContractError(
            "genesis_lineage_entry must be an exact GenesisLineageV1"
        )
    binding._validated_mappings()
    try:
        hex_cell = HexCellAddressV1(
            **{
                field.name: getattr(binding.hex_cell, field.name)
                for field in fields(binding.hex_cell)
            }
        )
        cell_identity = CellIdentityV1(
            **{
                field.name: getattr(binding.cell_identity, field.name)
                for field in fields(binding.cell_identity)
            }
        )
        lineage = GenesisLineageV1(
            **{
                field.name: getattr(binding.genesis_lineage_entry, field.name)
                for field in fields(binding.genesis_lineage_entry)
            }
        )
    except (TypeError, ValueError) as exc:
        raise CodingCandidateContractError(
            "nested cell identity, lineage, or hex snapshot refused"
        ) from exc
    binding_snapshot = CodingCandidateCellBindingV1(
        hex_cell=hex_cell,
        cell_identity=cell_identity,
        genesis_lineage_entry=lineage,
        subdivision_address_digest=binding.subdivision_address_digest,
        registry_snapshot_digest=binding.registry_snapshot_digest,
    )
    source_snapshot = CodingCandidateSourcePackV1(
        solver_source_utf8=request.source_pack.solver_source_utf8,
        test_source_utf8=request.source_pack.test_source_utf8,
    )
    plan_snapshot = CodingCandidateAdmissionPlanV1(
        **{
            field.name: getattr(request.plan, field.name)
            for field in fields(request.plan)
        }
    )
    return CodingCandidateBuildRequestV1(
        plan=plan_snapshot,
        source_pack=source_snapshot,
        cell_binding=binding_snapshot,
        schema_version=request.schema_version,
    )


def build_understanding_coding_candidate(
    request: CodingCandidateBuildRequestV1 | None = None,
    *,
    policy: CodingCandidatePolicyV1 = CodingCandidatePolicyV1(),
) -> CodingCandidateBuildResultV1 | None:
    """Build one inert static candidate package, or short-circuit while OFF.

    OFF returns before inspecting ``request`` or any source, lineage, worker,
    interpreter, path, or subprocess fact.
    """

    if type(policy) is not CodingCandidatePolicyV1:
        raise CodingCandidateContractError("policy must be a CodingCandidatePolicyV1")
    if policy.mode is CodingCandidateMode.OFF:
        return None
    policy = _snapshot_policy(policy)
    if policy.mode is not CodingCandidateMode.STATIC_SHADOW:
        raise CodingCandidateContractError("unsupported coding-candidate mode")
    if type(request) is not CodingCandidateBuildRequestV1:
        raise CodingCandidateContractError(
            "STATIC_SHADOW requires an exact CodingCandidateBuildRequestV1"
        )
    request = _revalidate_request_bindings(request)
    if request.plan.resource_policy_digest != policy.policy_digest:
        raise CodingCandidateContractError("plan resource policy digest mismatch")
    preflight_reason = _preflight_sources(request.source_pack, policy)
    if preflight_reason is not None:
        receipt = _make_receipt(
            request=request,
            policy=policy,
            status=CodingCandidateBuildStatus.SOURCE_REJECTED,
            reason_code=preflight_reason,
            artifact=None,
            temporary_cwd_created_and_removed=False,
            worker_process_observed=False,
        )
        return CodingCandidateBuildResultV1(artifact=None, receipt=receipt)
    _revalidate_request(request, policy)
    outcome = _invoke_worker(request, policy)
    artifact: CodingCandidateArtifactV1 | None = None
    status = outcome.status
    reason = outcome.reason_code
    if status is CodingCandidateBuildStatus.PACKAGED:
        assert outcome.response is not None
        artifact_bytes, manifest_digest = _build_artifact_bytes(request)
        inspected = _inspect_artifact(artifact_bytes)
        expected_response = {
            "solver_source_digest": inspected["solver_source_digest"],
            "test_source_digest": inspected["test_source_digest"],
            "artifact_manifest_digest": manifest_digest,
            "artifact_digest": inspected["artifact_digest"],
            "artifact_byte_count": inspected["byte_count"],
        }
        if any(
            outcome.response.get(name) != value
            for name, value in expected_response.items()
        ):
            status = CodingCandidateBuildStatus.DIGEST_MISMATCH
            reason = CodingCandidateReasonCode.WORKER_DIGEST_MISMATCH
        else:
            artifact = CodingCandidateArtifactV1(
                artifact_bytes=artifact_bytes,
                artifact_digest=inspected["artifact_digest"],
                artifact_manifest_digest=inspected["artifact_manifest_digest"],
                source_manifest_digest=inspected["source_manifest_digest"],
                solver_source_digest=inspected["solver_source_digest"],
                test_source_digest=inspected["test_source_digest"],
                byte_count=inspected["byte_count"],
            )
    receipt = _make_receipt(
        request=request,
        policy=policy,
        status=status,
        reason_code=reason,
        artifact=artifact,
        temporary_cwd_created_and_removed=True,
        worker_process_observed=outcome.worker_process_observed,
    )
    return CodingCandidateBuildResultV1(artifact=artifact, receipt=receipt)


__all__ = [
    "CODING_CANDIDATE_ARTIFACT_FORMAT",
    "CODING_CANDIDATE_ARTIFACT_SCHEMA",
    "CODING_CANDIDATE_AST_POLICY_DIGEST",
    "CODING_CANDIDATE_INTERFACE_DIGEST",
    "CODING_CANDIDATE_PACKAGING_POLICY_DIGEST",
    "CodingCandidateAdmissionPlanV1",
    "CodingCandidateArtifactV1",
    "CodingCandidateBuildReceiptV1",
    "CodingCandidateBuildRequestV1",
    "CodingCandidateBuildResultV1",
    "CodingCandidateBuildStatus",
    "CodingCandidateCellBindingV1",
    "CodingCandidateContractError",
    "CodingCandidateMode",
    "CodingCandidatePolicyV1",
    "CodingCandidateReasonCode",
    "CodingCandidateSourcePackV1",
    "build_understanding_coding_candidate",
    "derive_coding_candidate_source_manifest_digest",
    "derive_current_coding_candidate_worker_digest",
    "derive_current_interpreter_artifact_digest",
    "derive_current_interpreter_identity_digest",
    "derive_hex_cell_address_digest",
]

# SPDX-License-Identifier: BUSL-1.1
# SPDX-FileCopyrightText: Jani Korpi / Ahkerat Mehilaiset / JKH Service
"""Read-only registry for deterministic v3.13.0 first-slice solvers.

The registry codifies already-implemented solver entrypoints. It does not
activate solvers, import credentials, perform I/O beyond reading the manifest,
or call the solver functions.
"""
from __future__ import annotations

from dataclasses import dataclass
import importlib
import json
from pathlib import Path
import re
from typing import Any, Callable, Mapping


SCHEMA_VERSION = 1
DEFAULT_REGISTRY_PATH = (
    Path(__file__).resolve().parents[3]
    / "schemas"
    / "v3_13_0"
    / "solver_registry.json"
)

CASE_ID_RE = re.compile(
    r"^(?:case:[a-z][a-z0-9_.:-]{2,127}|"
    r"[A-Z0-9]{2,16}-[0-9]{2}__[a-z0-9_]+__[a-z][a-z0-9_]*)$"
)
MODULE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+$")
IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
MARKER_RE = re.compile(r"^[A-Z0-9_]+$")

RISK_CLASSES = frozenset({
    "informational",
    "internal_memory",
    "local_artifact",
    "external_effect",
})
WRITE_INTENTS = frozenset({"none"})
MARKER_CLASSES = frozenset({
    "fail_closed",
    "informational_with_severity",
    "status_buckets",
})
SECRET_TOKENS = (
    "authorization",
    "bearer",
    "cookie",
    "credential",
    "credentials",
    "passwd",
    "password",
    "secret",
    "token",
    "x-api-key",
    "api_key",
)


class SolverRegistryError(ValueError):
    """Fail-closed registry validation error."""


@dataclass(frozen=True)
class SolverManifest:
    """One canonical first-slice solver manifest entry."""

    case_id: str
    name: str
    human_name: str
    module: str
    entry_function: str
    result_class: str
    risk_class: str
    write_intent: str
    input_shape: str
    output_shape: str
    result_markers: tuple[str, ...]
    marker_class: str
    case_id_source_module: str | None = None
    advisory_card_module: str | None = None
    adapter_modules: tuple[str, ...] = ()
    transport_modules: tuple[str, ...] = ()
    cli_module: str | None = None
    knowledge_refs: tuple[str, ...] = ()

    def to_mapping(self) -> dict[str, Any]:
        payload = {
            "case_id": self.case_id,
            "name": self.name,
            "human_name": self.human_name,
            "module": self.module,
            "entry_function": self.entry_function,
            "result_class": self.result_class,
            "risk_class": self.risk_class,
            "write_intent": self.write_intent,
            "input_shape": self.input_shape,
            "output_shape": self.output_shape,
            "result_markers": list(self.result_markers),
            "marker_class": self.marker_class,
            "adapter_modules": list(self.adapter_modules),
            "transport_modules": list(self.transport_modules),
            "knowledge_refs": list(self.knowledge_refs),
        }
        if self.case_id_source_module is not None:
            payload["case_id_source_module"] = self.case_id_source_module
        if self.advisory_card_module is not None:
            payload["advisory_card_module"] = self.advisory_card_module
        if self.cli_module is not None:
            payload["cli_module"] = self.cli_module
        return payload


def load_solver_registry(
    path: str | Path | None = None,
) -> tuple[SolverManifest, ...]:
    """Load and validate the solver registry manifest."""
    registry_path = Path(path) if path is not None else DEFAULT_REGISTRY_PATH
    try:
        raw = json.loads(registry_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise SolverRegistryError(
            f"solver registry could not be read: {registry_path}"
        ) from exc
    except json.JSONDecodeError as exc:
        raise SolverRegistryError("solver registry is not valid JSON") from exc

    if not isinstance(raw, Mapping):
        raise SolverRegistryError("solver registry root must be an object")
    _validate_manifest_tree_is_safe(raw)
    if raw.get("schema_version") != SCHEMA_VERSION:
        raise SolverRegistryError("solver registry schema_version refused")

    solvers_raw = raw.get("solvers")
    if not isinstance(solvers_raw, list) or not solvers_raw:
        raise SolverRegistryError("solver registry solvers must be non-empty")

    solvers = tuple(_parse_solver(item, index)
                    for index, item in enumerate(solvers_raw))
    _validate_unique_solvers(solvers)
    return solvers


def iter_solver_manifests(
    path: str | Path | None = None,
) -> tuple[SolverManifest, ...]:
    """Return registry entries as an immutable tuple."""
    return load_solver_registry(path)


def get_solver_manifest(
    case_id: str,
    path: str | Path | None = None,
) -> SolverManifest:
    """Return one solver manifest by case_id or fail closed."""
    _validate_case_id(case_id, "case_id")
    for solver in load_solver_registry(path):
        if solver.case_id == case_id:
            return solver
    raise SolverRegistryError(f"unknown solver case_id: {case_id}")


def resolve_solver_entrypoint(solver: SolverManifest) -> Callable[..., Any]:
    """Import and return the declared solver entrypoint."""
    module = importlib.import_module(solver.module)
    entrypoint = getattr(module, solver.entry_function, None)
    if not callable(entrypoint):
        raise SolverRegistryError(
            f"{solver.module}.{solver.entry_function} is not callable"
        )
    return entrypoint


def _parse_solver(raw: Any, index: int) -> SolverManifest:
    if not isinstance(raw, Mapping):
        raise SolverRegistryError(f"solvers[{index}] must be an object")

    solver = SolverManifest(
        case_id=_required_case_id(raw, index),
        name=_required_non_empty_str(raw, "name", index),
        human_name=_required_non_empty_str(raw, "human_name", index),
        module=_required_module(raw, "module", index),
        entry_function=_required_identifier(raw, "entry_function", index),
        result_class=_required_identifier(raw, "result_class", index),
        risk_class=_enum_str(raw, "risk_class", RISK_CLASSES, index),
        write_intent=_enum_str(raw, "write_intent", WRITE_INTENTS, index),
        input_shape=_required_non_empty_str(raw, "input_shape", index),
        output_shape=_required_non_empty_str(raw, "output_shape", index),
        result_markers=_result_markers(raw.get("result_markers"), index),
        marker_class=_enum_str(raw, "marker_class", MARKER_CLASSES, index),
        case_id_source_module=_optional_module(raw, "case_id_source_module", index),
        advisory_card_module=_optional_module(raw, "advisory_card_module", index),
        adapter_modules=_module_tuple(raw.get("adapter_modules", []),
                                      "adapter_modules", index),
        transport_modules=_module_tuple(raw.get("transport_modules", []),
                                        "transport_modules", index),
        cli_module=_optional_module(raw, "cli_module", index),
        knowledge_refs=_string_tuple(raw.get("knowledge_refs", []),
                                     "knowledge_refs", index),
    )
    if "OK" not in solver.result_markers:
        raise SolverRegistryError(
            f"solvers[{index}].result_markers must include OK"
        )
    return solver


def _validate_unique_solvers(solvers: tuple[SolverManifest, ...]) -> None:
    seen_case_ids: set[str] = set()
    seen_entrypoints: set[tuple[str, str]] = set()
    for solver in solvers:
        if solver.case_id in seen_case_ids:
            raise SolverRegistryError(f"duplicate case_id: {solver.case_id}")
        seen_case_ids.add(solver.case_id)
        entrypoint = (solver.module, solver.entry_function)
        if entrypoint in seen_entrypoints:
            raise SolverRegistryError(
                f"duplicate solver entrypoint: {solver.module}."
                f"{solver.entry_function}"
            )
        seen_entrypoints.add(entrypoint)


def _validate_manifest_tree_is_safe(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            _safe_text(str(key), "registry key")
            _validate_manifest_tree_is_safe(item)
    elif isinstance(value, list):
        for item in value:
            _validate_manifest_tree_is_safe(item)
    elif isinstance(value, str):
        _safe_text(value, "registry value")


def _required_case_id(raw: Mapping[str, Any], index: int) -> str:
    value = _required_non_empty_str(raw, "case_id", index)
    _validate_case_id(value, f"solvers[{index}].case_id")
    return value


def _validate_case_id(value: str, label: str) -> None:
    if not CASE_ID_RE.fullmatch(value):
        raise SolverRegistryError(f"{label} is invalid: {value!r}")


def _required_module(raw: Mapping[str, Any], key: str, index: int) -> str:
    return _validate_module(_required_non_empty_str(raw, key, index), key, index)


def _optional_module(raw: Mapping[str, Any], key: str, index: int) -> str | None:
    value = raw.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise SolverRegistryError(f"solvers[{index}].{key} must be a string")
    return _validate_module(value.strip(), key, index)


def _validate_module(value: str, key: str, index: int) -> str:
    if not MODULE_RE.fullmatch(value):
        raise SolverRegistryError(f"solvers[{index}].{key} is not a module")
    return value


def _required_identifier(raw: Mapping[str, Any], key: str, index: int) -> str:
    value = _required_non_empty_str(raw, key, index)
    if not IDENT_RE.fullmatch(value):
        raise SolverRegistryError(
            f"solvers[{index}].{key} is not a Python identifier"
        )
    return value


def _enum_str(
    raw: Mapping[str, Any],
    key: str,
    allowed: frozenset[str],
    index: int,
) -> str:
    value = _required_non_empty_str(raw, key, index)
    if value not in allowed:
        raise SolverRegistryError(f"solvers[{index}].{key} unsupported")
    return value


def _required_non_empty_str(
    raw: Mapping[str, Any],
    key: str,
    index: int,
) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise SolverRegistryError(f"solvers[{index}].{key} must be a string")
    return value.strip()


def _result_markers(raw: Any, index: int) -> tuple[str, ...]:
    markers = _string_tuple(raw, "result_markers", index)
    if not markers:
        raise SolverRegistryError(
            f"solvers[{index}].result_markers must be non-empty"
        )
    for marker in markers:
        if not MARKER_RE.fullmatch(marker):
            raise SolverRegistryError(
                f"solvers[{index}].result_markers contains invalid marker"
            )
    if len(set(markers)) != len(markers):
        raise SolverRegistryError(
            f"solvers[{index}].result_markers contains duplicates"
        )
    return markers


def _module_tuple(raw: Any, key: str, index: int) -> tuple[str, ...]:
    values = _string_tuple(raw, key, index)
    for value in values:
        _validate_module(value, key, index)
    return values


def _string_tuple(raw: Any, key: str, index: int) -> tuple[str, ...]:
    if not isinstance(raw, list):
        raise SolverRegistryError(f"solvers[{index}].{key} must be a list")
    values: list[str] = []
    seen: set[str] = set()
    for item_index, item in enumerate(raw):
        if not isinstance(item, str) or not item.strip():
            raise SolverRegistryError(
                f"solvers[{index}].{key}[{item_index}] must be a string"
            )
        value = item.strip()
        if value in seen:
            raise SolverRegistryError(
                f"solvers[{index}].{key} contains duplicates"
            )
        seen.add(value)
        values.append(value)
    return tuple(values)


def _safe_text(value: str, label: str) -> None:
    lower = value.casefold()
    if ":\\" in value or lower.startswith(("u:\\", "file://", "/")):
        raise SolverRegistryError(f"{label} contains an unsafe path")
    if any(token in lower for token in SECRET_TOKENS):
        raise SolverRegistryError(f"{label} contains a secret-like marker")


__all__ = [
    "DEFAULT_REGISTRY_PATH",
    "SCHEMA_VERSION",
    "SolverManifest",
    "SolverRegistryError",
    "get_solver_manifest",
    "iter_solver_manifests",
    "load_solver_registry",
    "resolve_solver_entrypoint",
]

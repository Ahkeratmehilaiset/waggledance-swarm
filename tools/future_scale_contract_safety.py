"""Shared safety checks for future-scale benchmark contract artifacts."""
from __future__ import annotations

from typing import Any, Iterable, Sequence

from waggledance.core.leak_policy import (
    is_finite_number,
    looks_like_leak,
)


def walk_scalars(value: Any, path: str = "$") -> list[tuple[str, Any]]:
    scalars: list[tuple[str, Any]] = []
    if isinstance(value, dict):
        for key, child in value.items():
            scalars.extend(walk_scalars(child, f"{path}.{key}"))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            scalars.extend(walk_scalars(child, f"{path}[{index}]"))
    else:
        scalars.append((path, value))
    return scalars


def validate_exact_false_fields(
    artifact: dict[str, Any],
    fields: Sequence[str],
) -> list[str]:
    errors: list[str] = []
    for field in fields:
        if artifact.get(field) is not False:
            errors.append(f"{field} must be exact false bool")
    return errors


def validate_scalar_safety(
    artifact: dict[str, Any],
    *,
    allowed_metadata_path_values: Iterable[str] = (),
) -> list[str]:
    errors: list[str] = []
    allowed_paths = frozenset(allowed_metadata_path_values)
    for path, value in walk_scalars(artifact):
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if not is_finite_number(value):
                errors.append(f"{path} contains a non-finite number")
        elif isinstance(value, str) and looks_like_forbidden_scalar(
            path,
            value,
            allowed_metadata_path_values=allowed_paths,
        ):
            errors.append(f"{path} contains a forbidden secret/path-like string")
    return errors


def looks_like_forbidden_scalar(
    path: str,
    value: str,
    *,
    allowed_metadata_path_values: Iterable[str] = (),
) -> bool:
    return looks_like_leak(
        path,
        value,
        frozenset(allowed_metadata_path_values),
    )

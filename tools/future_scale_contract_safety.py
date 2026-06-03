"""Shared safety checks for future-scale benchmark contract artifacts."""
from __future__ import annotations

import math
import re
from typing import Any, Iterable, Sequence


MODEL_PROVIDER_ALIASES: tuple[str, ...] = (
    "anthropic",
    "claude",
    "cohere",
    "command-r",
    "command",
    "deepseek",
    "falcon",
    "gemini",
    "gemma",
    "google",
    "gpt",
    "grok",
    "hf",
    "huggingface",
    "llama",
    "mistral",
    "mixtral",
    "mpt",
    "ollama",
    "openai",
    "phi",
    "poro",
    "qwen",
    "xai",
    "yi",
)
SHORT_MODEL_PROVIDER_ALIASES = frozenset({"gpt", "hf", "mpt", "phi", "yi"})

LEAK_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"[A-Za-z]:[/\\](?:Users|Python|Program Files|tmp)\b", re.IGNORECASE),
    re.compile(r"[A-Za-z]:tmp\b", re.IGNORECASE),
    re.compile(r"\\\\(?:wsl|share)", re.IGNORECASE),
    re.compile(
        r"(?:^|[/\\])(?:home|root|etc|var|opt|Users|mnt|tmp)(?:[/\\]|(?=$|\s|[\"'`;:,)\]]))",
        re.IGNORECASE,
    ),
    re.compile(r"(?:^|[/\\])\.\.(?:[/\\]|$)"),
    re.compile(r"\bBearer\s+[A-Za-z0-9_.-]+", re.IGNORECASE),
    re.compile(r"\bsk-[A-Za-z0-9]{16,}\b", re.IGNORECASE),
    re.compile(r"\bAKIA[0-9A-Z]{16,}\b"),
    re.compile(r"\bhf://[A-Za-z0-9_.:/-]+", re.IGNORECASE),
    re.compile(r"\b[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+:[A-Za-z0-9_.-]+\b"),
)

REPO_RELATIVE_PATH_PATTERN = re.compile(
    r"^(?:configs|docs|orchestrator|prompts|reports|schemas|tests|tools|"
    r"waggledance|web)/[A-Za-z0-9_./-]+\.[A-Za-z0-9]+$"
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
            if not math.isfinite(float(value)):
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
    allowed_paths = frozenset(allowed_metadata_path_values)
    if REPO_RELATIVE_PATH_PATTERN.match(value):
        return not _is_allowed_metadata_path(path, value, allowed_paths)
    if any(pattern.search(value) for pattern in LEAK_PATTERNS):
        return True
    return contains_model_provider_alias(value)


def contains_model_provider_alias(value: str) -> bool:
    normalized = value.casefold()
    return any(_contains_model_provider_alias(normalized, alias) for alias in MODEL_PROVIDER_ALIASES)


def _contains_model_provider_alias(normalized: str, alias: str) -> bool:
    if alias not in SHORT_MODEL_PROVIDER_ALIASES:
        return alias in normalized
    if re.search(rf"(?<![a-z0-9]){re.escape(alias)}(?:[0-9][a-z0-9_.:/-]*|[_.:/-][a-z0-9_.:/-]+)?(?![a-z0-9])", normalized):
        return True
    return bool(re.search(rf"{re.escape(alias)}[0-9]", normalized))


def _is_allowed_metadata_path(
    path: str,
    value: str,
    allowed_metadata_path_values: frozenset[str],
) -> bool:
    if value not in allowed_metadata_path_values:
        return False
    return path == "$.axis_definition_source" or path.startswith("$.source_paths[")

# SPDX-License-Identifier: BUSL-1.1
"""Shared, offline, deterministic leak-policy module for future benchmark contracts.

Centralizes provider/model token shapes (via explicit allowlist of token forms),
secret patterns, path patterns, and a path-aware `looks_like_leak` so that
per-file duplication across benchmark slices cannot drift.

Exposed surface:
- MODEL_PROVIDER_TOKEN_PATTERN (built from stable _PROVIDER_TOKENS allowlist shape)
- REPO_RELATIVE_PATH_PATTERN
- LEAK_PATTERNS (tuple of compiled regex using shapes only)
- looks_like_leak(field_path, value, allowed_metadata_paths): path-aware, allowlist-only
  for repo-relative paths in *named* metadata fields; everything else fail-closed.
- looks_like_leak_simple(value)
- CLAIM_GATES (the exact list of gates that contracts must emit as literal false)

All operations deterministic, no network, no wallclock, no random, no model pulls.
Non-finite numbers are out of scope here (handled by callers).

Claim gates: N/A (pure utility module). Any artifact consuming this must set
claim_gate_satisfied=false, claim_safe=false, literal_future_claim_safe=false,
controls_present=false, runtime_authority_granted=false, external_writes_applied=false,
required_runtime_evidence_present=false. No carve-outs.

See docs/architecture/LEAK_POLICY.md for contract, invariants, and usage.
"""
from __future__ import annotations

import re
from typing import Any, FrozenSet

# Stable allowlist of provider/model token *shapes* (not instances).
# Used to build the denylist matcher for accidental leakage into free-text fields,
# stage aliases, seeds, repro commands, etc. Prefer this explicit list (schema-style)
# over ad-hoc denylist regexes in every contract.
_PROVIDER_TOKENS: tuple[str, ...] = (
    # Put multi-token forms first so alternation prefers them.
    "command-r",
    "anthropic",
    "claude",
    "cohere",
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

_MODEL_PROVIDER_NAME_PATTERN = "(?:" + "|".join(_PROVIDER_TOKENS) + ")"
_MODEL_PROVIDER_FALSE_POSITIVE_PATTERN = (
    r"(?:command_center(?:_[A-Za-z0-9]+)*|xai_(?:helper|is(?:_[A-Za-z0-9]+)*))"
)

# Matches bare tokens and glued forms: gpt4o, mpt7b, gpt4o_hit, cohere_internal,
# command-r, llama3.1, hf/..., org/mod:tag (via companion), etc.
# Case-insensitive at compile.
# Tuned lookarounds, separators, and narrow false-positive guards so that
# "command_center", "yield", "mygpthelper", etc. do not over-trigger.
MODEL_PROVIDER_TOKEN_PATTERN: str = (
    rf"(?<![A-Za-z0-9_])(?!(?:{_MODEL_PROVIDER_FALSE_POSITIVE_PATTERN})(?![A-Za-z0-9_]))"
    rf"{_MODEL_PROVIDER_NAME_PATTERN}"
    r"(?:[0-9][A-Za-z0-9_.-]*|[_.:/-][A-Za-z0-9_.:/-]+)?"
    r"(?![A-Za-z0-9_])"
)

# Repo-relative paths that may appear in *explicitly allowlisted* metadata only.
# Shape only; no concrete fs paths.
REPO_RELATIVE_PATH_PATTERN: re.Pattern[str] = re.compile(
    r"^(?:configs|docs|orchestrator|prompts|reports|schemas|tests|tools|"
    r"waggledance|web)/[A-Za-z0-9_./-]+\.[A-Za-z0-9]+$"
)

# Named metadata fields permitted (when value also in caller allowlist) to carry
# repo-relative source paths. This is a schema allowlist of field shapes.
_NAMED_SOURCE_FIELD_PREFIXES: tuple[str, ...] = (
    "$.axis_definition_source",
    "$.source_paths",
)


def _is_named_source_metadata_field(field_path: str) -> bool:
    if not field_path:
        return False
    if field_path in _NAMED_SOURCE_FIELD_PREFIXES:
        return True
    for prefix in _NAMED_SOURCE_FIELD_PREFIXES:
        if field_path.startswith(prefix + "[") or field_path.startswith(prefix + "."):
            return True
    return False


# Leak patterns built from stable shapes (drive/unix paths, traversal, hf://, org/mod:tag, secrets, model tokens).
# No raw secrets/tokens/paths beyond the minimal regex shapes required for detection.
# Order and forms consolidated from recurring benchmark usage to prevent drift.
LEAK_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"[A-Za-z]:[\\/]", re.IGNORECASE),  # drive paths (C:..., D:\...)
    re.compile(r"[A-Za-z]:tmp\b", re.IGNORECASE),
    re.compile(r"\\\\(?:wsl|share)", re.IGNORECASE),
    re.compile(
        r"(?:^|[\\/])(?:home|root|etc|var|opt|Users|mnt|tmp)(?:[\\/]|$)",
        re.IGNORECASE,
    ),
    re.compile(r"(?:^|[\\/])\.\.(?:[\\/]|$)"),  # .. traversal (and /.. etc)
    re.compile(r"\bBearer\s+[A-Za-z0-9_.-]+", re.IGNORECASE),
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b", re.IGNORECASE),
    re.compile(r"\bAKIA[0-9A-Z]{16,}\b"),
    re.compile(r"\bhf://[A-Za-z0-9_.:/-]+", re.IGNORECASE),
    re.compile(r"\b[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+:[A-Za-z0-9_.-]+\b"),  # org/model:tag shape
    re.compile(MODEL_PROVIDER_TOKEN_PATTERN, re.IGNORECASE),
)


def looks_like_leak(
    field_path: str,
    value: Any,
    allowed_metadata_paths: FrozenSet[str] | None = None,
) -> bool:
    """Path-aware leak detector.

    Returns True (looks like leak / must reject) unless the value is provably safe
    in the given field_path context.

    Rules (fail-closed, no fail-open):
    - If value is not str: treat as non-leak for this checker (callers handle types).
    - If value matches REPO_RELATIVE_PATH_PATTERN:
        - Return False (safe) ONLY IF value is present in the caller-supplied
          allowed_metadata_paths AND field_path names an allowed source-metadata
          field (axis_definition_source or source_paths[...]).
        - All other repo-relative appearances (wrong field, or value not in the
          explicit allowlist for this artifact) -> True (leak).
    - Else (free text / other values): return True if any LEAK_PATTERNS matches.
    - Model token detection uses the allowlist-derived pattern and rejects
      glued forms (gpt4o_hit, mpt7b, command-r-...) while guarding common
      false-positives (command_center, yield, etc. do not trigger).

    allowed_metadata_paths: caller provides the exact frozenset of repo-relative
    strings that are blessed for *this* artifact's metadata (e.g. the SOURCE_PATHS
    used to build the benchmark). Defaults to empty (strictest).

    Deterministic and offline. Rejects everything not explicitly permitted.
    """
    if not isinstance(value, str):
        return False

    allowed: FrozenSet[str] = allowed_metadata_paths or frozenset()

    if REPO_RELATIVE_PATH_PATTERN.match(value):
        if value in allowed and _is_named_source_metadata_field(field_path):
            return False
        return True

    return any(pattern.search(value) for pattern in LEAK_PATTERNS)


def looks_like_leak_simple(value: Any) -> bool:
    """Path-unaware variant. Any repo-relative path value is treated as leak
    (since no allowed set / field context supplied). Used for legacy scalar walks.
    """
    if not isinstance(value, str):
        return False
    if REPO_RELATIVE_PATH_PATTERN.match(value):
        return True
    return any(pattern.search(value) for pattern in LEAK_PATTERNS)


# Exact list of claim gates that benchmark / manifest / contract artifacts must
# emit as the literal boolean False. (N/A for this module itself.)
CLAIM_GATES: tuple[str, ...] = (
    "claim_gate_satisfied",
    "claim_safe",
    "literal_future_claim_safe",
    "controls_present",
    "runtime_authority_granted",
    "external_writes_applied",
    "required_runtime_evidence_present",
)

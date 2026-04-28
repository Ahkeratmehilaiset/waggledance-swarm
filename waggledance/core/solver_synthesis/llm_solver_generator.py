# SPDX-License-Identifier: BUSL-1.1
# BUSL-Change-Date: 2030-12-31
# SPDX-FileCopyrightText: Jani Korpi / Ahkerat Mehilaiset / JKH Service
# See LICENSE-BUSL.txt and LICENSE-CORE.md
"""LLM-based solver generator (Phase 10 P4 — U3 free-form lane).

The Phase 9 declarative pipeline (``deterministic_solver_compiler``)
covers U1 — when a gap matches an existing family with high
confidence, we compile a solver from a structured spec. U3 covers the
residual: gaps with low family-match confidence may need free-form
synthesis, which is *expensive* and *hallucination-prone*. The default
provider for U3 is Claude Code (per the Phase 9 / Phase 10 chain), with
Anthropic API and GPT API as fallbacks and a local model last.

This module is the *generator surface*. It does not call providers
itself; it builds a :class:`ProviderRequest` payload and hands it to a
:class:`waggledance.core.providers.ProviderPlane`. The plane handles
validation, routing, dispatch, persistence, and re-validation.

The generator output is a dict that is *intended* to be a
:class:`SolverSpec`-shaped payload. Whether the LLM actually returned
something useful is determined downstream by the existing Phase 9
syntactic / semantic / property / regression / shadow validators in
:mod:`waggledance.core.solver_synthesis.validators`. The generator
returns the request, the response, and a parsed-spec attempt. Any
parse failure is surfaced as ``parse_status='failed'`` — never silent
(RULE 14).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Sequence

from waggledance.core.providers import (
    ProviderPlane,
    ProviderDispatchResult,
    validate_request,
)


@dataclass(frozen=True)
class GenerationRequest:
    """A description of a gap that needs free-form synthesis."""

    gap_id: str
    cell_id: str
    intent: str
    examples: Sequence[Mapping[str, Any]] = field(default_factory=tuple)
    family_hints: Sequence[str] = field(default_factory=tuple)
    capsule_context: str = "neutral_v1"


@dataclass(frozen=True)
class GenerationResult:
    request: GenerationRequest
    dispatch: ProviderDispatchResult
    parse_status: str  # "ok" | "failed" | "dry_run"
    parsed_spec_payload: Optional[Mapping[str, Any]]
    parse_error: Optional[str]


_DEFAULT_PRIORITY_LIST: tuple[str, ...] = (
    "claude_code_builder_lane",
    "anthropic_api",
    "gpt_api",
    "local_model_service",
)


def build_provider_request_payload(
    request: GenerationRequest,
    *,
    branch_name: str,
    base_commit_hash: str,
    pinned_input_manifest_sha256: str = "sha256:unknown",
    max_calls: float = 1.0,
    max_tokens: float = 8000.0,
    max_latency_ms: float = 600_000.0,
    section: Optional[str] = None,
) -> Mapping[str, Any]:
    """Build the validated provider-request payload for an LLM
    generation call. The payload satisfies
    ``schemas/provider_request.schema.json``."""

    intent = (
        f"Synthesize a SolverSpec (declarative, JSON) for gap {request.gap_id!r} "
        f"in cell {request.cell_id!r}. Intent: {request.intent}"
    )
    payload = {
        "schema_version": 1,
        "request_id": _short_id(request.gap_id),
        "task_class": "code_or_repair",
        "provider_priority_list": list(_DEFAULT_PRIORITY_LIST),
        "intent": intent,
        "input_payload": {
            "gap_id": request.gap_id,
            "cell_id": request.cell_id,
            "examples": [dict(e) for e in request.examples],
            "family_hints": list(request.family_hints),
        },
        "budget": {
            "max_calls": max_calls,
            "max_tokens": max_tokens,
            "max_latency_ms": max_latency_ms,
        },
        "agent_id_hint": None,
        "capsule_context": request.capsule_context,
        "no_runtime_mutation": True,
        "provenance": {
            "branch_name": branch_name,
            "base_commit_hash": base_commit_hash,
            "pinned_input_manifest_sha256": pinned_input_manifest_sha256,
        },
    }
    if section:
        payload["section"] = section
    # Round-trip through the validator so downstream callers cannot
    # accidentally hand a malformed payload to the plane.
    validate_request(payload)
    return payload


def _short_id(seed: str) -> str:
    import hashlib

    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:12]


def _try_parse_spec_payload(raw_payload: Mapping[str, Any]) -> tuple[Optional[Mapping[str, Any]], Optional[str]]:
    """Attempt to extract a JSON spec payload from a provider raw_payload.

    Looks at ``raw_payload['stdout']`` first (Claude Code path), then a
    direct ``raw_payload['spec']`` shortcut for synthetic / stub paths.
    Returns ``(payload, error)`` — at most one is non-None.
    """

    if "spec" in raw_payload and isinstance(raw_payload["spec"], Mapping):
        return dict(raw_payload["spec"]), None
    stdout = raw_payload.get("stdout")
    if not isinstance(stdout, str) or not stdout.strip():
        return None, "no spec in raw_payload (no 'spec' key, empty stdout)"
    try:
        parsed = json.loads(stdout)
    except json.JSONDecodeError as exc:
        return None, f"stdout was not JSON: {exc}"
    if isinstance(parsed, Mapping):
        return dict(parsed), None
    return None, f"stdout JSON was not an object: {type(parsed).__name__}"


def generate(
    plane: ProviderPlane,
    request: GenerationRequest,
    *,
    branch_name: str,
    base_commit_hash: str,
    section: Optional[str] = None,
) -> GenerationResult:
    payload = build_provider_request_payload(
        request,
        branch_name=branch_name,
        base_commit_hash=base_commit_hash,
        section=section,
    )
    dispatch = plane.dispatch(payload)
    if dispatch.provider_type_used == "dry_run_stub" or dispatch.response.raw_payload.get("dry_run"):
        return GenerationResult(
            request=request,
            dispatch=dispatch,
            parse_status="dry_run",
            parsed_spec_payload=None,
            parse_error="dispatch produced dry-run output (no real LLM call)",
        )
    parsed, err = _try_parse_spec_payload(dispatch.response.raw_payload)
    if parsed is None:
        return GenerationResult(
            request=request,
            dispatch=dispatch,
            parse_status="failed",
            parsed_spec_payload=None,
            parse_error=err,
        )
    return GenerationResult(
        request=request,
        dispatch=dispatch,
        parse_status="ok",
        parsed_spec_payload=parsed,
        parse_error=None,
    )

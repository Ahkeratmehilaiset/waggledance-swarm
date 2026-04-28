# SPDX-License-Identifier: BUSL-1.1
# BUSL-Change-Date: 2030-12-31
# SPDX-FileCopyrightText: Jani Korpi / Ahkerat Mehilaiset / JKH Service
# See LICENSE-BUSL.txt and LICENSE-CORE.md
"""Provider plane orchestrator.

The :class:`ProviderPlane` is the top-level entry point for any caller
that wants to dispatch a validated :class:`ProviderRequest` through a
provider chain. Responsibilities:

1. **Validate** the incoming request payload against
   ``schemas/provider_request.schema.json``.
2. **Route** to a provider using the Phase 9 router with the request's
   ``provider_priority_list`` overriding the constitution-level chain
   when present.
3. **Dispatch** to the chosen provider's adapter.
4. **Persist** the call to the control plane (``provider_jobs`` row).
5. **Validate** the response against
   ``schemas/provider_response.schema.json`` and return it.

The plane never mutates self/world model directly. The trust gate
flow (raw_quarantine → … → calibration_threshold_passed) is the
caller's responsibility — the plane returns a response in
``raw_quarantine`` state for downstream review.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping, Optional, Protocol

from waggledance.core.provider_plane.provider_router import route as phase9_route
from .claude_code_builder import ClaudeCodeBuilder
from .provider_contracts import (
    PROVIDER_TYPES,
    ProviderContractError,
    ProviderRequest,
    ProviderResponse,
    utcnow_iso,
    validate_request,
    validate_response,
)
from .provider_registry import ProviderPlaneRegistry


class ProviderPlaneError(RuntimeError):
    """Raised on plane-level failures (no provider available, etc.)."""


class ProviderAdapter(Protocol):
    """Minimal adapter contract every provider implementation honours."""

    PROVIDER_TYPE: str

    def dispatch(self, request: ProviderRequest) -> ProviderResponse: ...


@dataclass(frozen=True)
class ProviderDispatchResult:
    request: ProviderRequest
    response: ProviderResponse
    provider_id_used: str
    provider_type_used: str
    rationale: str


class _DryRunStubAdapter:
    PROVIDER_TYPE = "dry_run_stub"

    def __init__(self, provider_id: str = "dry_run_stub_default") -> None:
        self._provider_id = provider_id

    def dispatch(self, request: ProviderRequest) -> ProviderResponse:
        return ProviderResponse(
            schema_version=1,
            response_id=f"stub-{request.request_id}",
            request_id=request.request_id,
            provider_used=self._provider_id,
            raw_payload={
                "stub": True,
                "intent": request.intent,
                "task_class": request.task_class,
            },
            ts_iso=utcnow_iso(),
            latency_ms=0.0,
            trust_layer_state="raw_quarantine",
            no_direct_mutation=True,
        )


class _ClaudeCodeAdapter:
    PROVIDER_TYPE = "claude_code_builder_lane"

    def __init__(
        self,
        builder: ClaudeCodeBuilder,
        *,
        worktree_root: str = "data/builder_worktrees",
    ) -> None:
        self._builder = builder
        self._worktree_root = worktree_root

    def dispatch(self, request: ProviderRequest) -> ProviderResponse:
        worktree = (
            request.input_payload.get("isolated_worktree_path")
            if isinstance(request.input_payload, Mapping)
            else None
        )
        branch = (
            request.input_payload.get("isolated_branch_name")
            if isinstance(request.input_payload, Mapping)
            else None
        )
        if not worktree:
            worktree = f"{self._worktree_root}/{request.request_id}"
        if not branch:
            branch = f"phase10-builder/{request.request_id[:8]}"
        max_wall = int(request.budget.get("max_latency_ms", 600000) // 1000) or 600
        return self._builder.invoke(
            request,
            isolated_worktree_path=worktree,
            isolated_branch_name=branch,
            max_wall_seconds=max(60, min(max_wall, ClaudeCodeBuilder.HARD_TIMEOUT_CEILING_SECONDS)),
        )


class ProviderPlane:
    """Top-level orchestrator for provider dispatch."""

    def __init__(
        self,
        *,
        registry: ProviderPlaneRegistry,
        adapters: Optional[Mapping[str, ProviderAdapter]] = None,
        claude_code_builder: Optional[ClaudeCodeBuilder] = None,
        section: Optional[str] = None,
    ) -> None:
        self._registry = registry
        self._section = section
        # Default adapter set: a dry-run stub is always present so the
        # plane is exercisable without any credentials.
        default_adapters: dict[str, ProviderAdapter] = {
            "dry_run_stub": _DryRunStubAdapter(),
        }
        if claude_code_builder is not None:
            default_adapters["claude_code_builder_lane"] = _ClaudeCodeAdapter(
                claude_code_builder
            )
        if adapters:
            default_adapters.update(adapters)
        self._adapters: dict[str, ProviderAdapter] = default_adapters

    # -- adapter management --------------------------------------------

    def register_adapter(self, adapter: ProviderAdapter) -> None:
        if adapter.PROVIDER_TYPE not in PROVIDER_TYPES:
            raise ProviderPlaneError(
                f"unknown provider_type {adapter.PROVIDER_TYPE!r}"
            )
        self._adapters[adapter.PROVIDER_TYPE] = adapter

    def has_adapter(self, provider_type: str) -> bool:
        return provider_type in self._adapters

    @property
    def section(self) -> Optional[str]:
        return self._section

    # -- dispatch -------------------------------------------------------

    def dispatch(
        self,
        request_payload: Mapping[str, Any],
        *,
        require_warm: bool = False,
    ) -> ProviderDispatchResult:
        request = validate_request(request_payload)
        request_hash = hashlib.sha256(
            json.dumps(request.to_dict(), sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

        chosen_provider_type, rationale = self._pick_provider_type(
            request, require_warm=require_warm
        )
        if chosen_provider_type is None:
            raise ProviderPlaneError(rationale)

        adapter = self._adapters.get(chosen_provider_type)
        if adapter is None:
            # Fall through to dry-run stub if a real adapter isn't
            # registered. This is the RULE 0/9 "missing keys must not
            # block unrelated work" path.
            adapter = self._adapters["dry_run_stub"]
            chosen_provider_type = "dry_run_stub"
            rationale = f"{rationale}; adapter missing → dry-run stub"

        provider_id = self._provider_id_for(chosen_provider_type, request)
        job = self._registry.record_job_started(
            provider_id=provider_id,
            request_kind=request.task_class,
            request_hash=request_hash,
            section=self._section or request.section,
            purpose=request.purpose or request.intent[:200],
        )
        try:
            response = adapter.dispatch(request)
        except Exception as exc:  # noqa: BLE001 — fail-loud, RULE 14
            self._registry.record_job_completed(
                job, status="failed", error=repr(exc), completed_at_utc=utcnow_iso()
            )
            raise

        # Re-validate the response we got back from the adapter (RULE 14
        # again — never trust upstream blindly).
        validated = validate_response(response.to_dict())

        self._registry.record_job_completed(
            job,
            status=("dry_run" if chosen_provider_type == "dry_run_stub" else "completed"),
            cost_actual=None,
            completed_at_utc=utcnow_iso(),
        )
        return ProviderDispatchResult(
            request=request,
            response=validated,
            provider_id_used=provider_id,
            provider_type_used=chosen_provider_type,
            rationale=rationale,
        )

    # -- internal -------------------------------------------------------

    def _pick_provider_type(
        self,
        request: ProviderRequest,
        *,
        require_warm: bool,
    ) -> tuple[Optional[str], str]:
        chain = request.provider_priority_list
        for ptype in chain:
            if ptype not in PROVIDER_TYPES:
                continue
            cfgs = self._registry.list_by_type(ptype)
            usable = [
                c for c in cfgs if (not require_warm) or (c.enabled and c.has_credentials)
            ]
            if usable:
                return ptype, f"chosen_from_request_priority_list={ptype}"
        # Fall through to Phase 9 router on the underlying registry.
        decision = phase9_route(
            task_class=request.task_class,
            registry=self._registry.phase9_registry,
            agent_id_hint=request.agent_id_hint,
            require_warm=require_warm,
        )
        if decision.chosen_provider_type:
            return decision.chosen_provider_type, decision.rationale
        # Final fallback: dry-run stub. We always advertise the stub.
        return "dry_run_stub", "no_warm_provider_in_chain → dry-run stub"

    def _provider_id_for(self, provider_type: str, request: ProviderRequest) -> str:
        # Honour agent_id_hint if it points at a registered config of
        # the chosen type.
        if request.agent_id_hint:
            cfg = self._registry.get(request.agent_id_hint)
            if cfg is not None and cfg.provider_type == provider_type:
                return cfg.provider_id
        cfgs = self._registry.list_by_type(provider_type)
        if cfgs:
            return cfgs[0].provider_id
        if provider_type == "dry_run_stub":
            return "dry_run_stub_default"
        if provider_type == "claude_code_builder_lane":
            return ClaudeCodeBuilder.DEFAULT_PROVIDER_ID
        return f"{provider_type}_default"

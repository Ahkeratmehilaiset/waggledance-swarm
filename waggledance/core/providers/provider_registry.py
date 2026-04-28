# SPDX-License-Identifier: BUSL-1.1
# BUSL-Change-Date: 2030-12-31
# SPDX-FileCopyrightText: Jani Korpi / Ahkerat Mehilaiset / JKH Service
# See LICENSE-BUSL.txt and LICENSE-CORE.md
"""Provider registry bridge — Phase 9 in-memory + Phase 10 control-plane.

The Phase 9 :class:`waggledance.core.provider_plane.provider_registry.ProviderRegistry`
is an in-process catalog of (provider_id, provider_type, daily budget,
warm flag, capabilities). Phase 10 P3 adds a thin bridge that:

* persists provider configurations to the control plane (so a Reality
  View / status endpoint can list "what providers does this WD instance
  know about", regardless of which process answers);
* records each provider call as a row in
  ``provider_jobs`` with section, purpose, cost estimate, and final
  status (RULE 8: provider budget tracked).

This module does **not** replace the Phase 9 registry. It composes it.
A caller that does not need control-plane persistence can still use
the Phase 9 registry directly.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Iterable, List, Mapping, Optional

from waggledance.core.provider_plane import PROVIDERS as PHASE_9_PROVIDERS
from waggledance.core.provider_plane.provider_registry import (
    ProviderRecord as Phase9ProviderRecord,
    ProviderRegistry as Phase9ProviderRegistry,
)
from waggledance.core.storage import ControlPlaneDB, ControlPlaneError, ProviderJobRecord


@dataclass(frozen=True)
class ProviderConfig:
    """Operational configuration for one provider.

    Distinct from :class:`Phase9ProviderRecord` (which is the catalog
    view) — this carries credential availability and per-section
    budget hints.
    """

    provider_id: str
    provider_type: str
    enabled: bool = True
    has_credentials: bool = False
    daily_budget_calls: float = 0.0
    notes: str = ""

    def to_dict(self) -> dict:
        return {
            "provider_id": self.provider_id,
            "provider_type": self.provider_type,
            "enabled": self.enabled,
            "has_credentials": self.has_credentials,
            "daily_budget_calls": self.daily_budget_calls,
            "notes": self.notes,
        }


class ProviderPlaneRegistry:
    """Composed registry: in-memory + control-plane persistence."""

    def __init__(
        self,
        *,
        control_plane: Optional[ControlPlaneDB] = None,
        phase9_registry: Optional[Phase9ProviderRegistry] = None,
    ) -> None:
        self._cp = control_plane
        self._phase9 = phase9_registry or Phase9ProviderRegistry()
        self._configs: dict[str, ProviderConfig] = {}

    # -- registration ---------------------------------------------------

    def register(
        self,
        config: ProviderConfig,
        *,
        capabilities: Iterable[str] = (),
        warm: bool = True,
    ) -> ProviderConfig:
        if config.provider_type not in PHASE_9_PROVIDERS and config.provider_type != "dry_run_stub":
            raise ValueError(
                f"unknown provider_type {config.provider_type!r} — allowed: "
                f"{PHASE_9_PROVIDERS + ('dry_run_stub',)}"
            )
        self._configs[config.provider_id] = config
        if config.provider_type in PHASE_9_PROVIDERS:
            self._phase9.register(
                Phase9ProviderRecord(
                    schema_version=1,
                    provider_id=config.provider_id,
                    provider_type=config.provider_type,
                    daily_budget_calls=config.daily_budget_calls,
                    warm=warm and config.enabled and config.has_credentials,
                    capabilities=tuple(capabilities),
                )
            )
        return config

    # -- lookup ---------------------------------------------------------

    def get(self, provider_id: str) -> Optional[ProviderConfig]:
        return self._configs.get(provider_id)

    def list_by_type(self, provider_type: str) -> List[ProviderConfig]:
        return [c for c in self._configs.values() if c.provider_type == provider_type]

    def list_enabled(self) -> List[ProviderConfig]:
        return [c for c in self._configs.values() if c.enabled and c.has_credentials]

    @property
    def phase9_registry(self) -> Phase9ProviderRegistry:
        return self._phase9

    # -- control-plane integration -------------------------------------

    def record_job_started(
        self,
        *,
        provider_id: str,
        request_kind: str,
        request_hash: Optional[str] = None,
        section: Optional[str] = None,
        purpose: Optional[str] = None,
        cost_estimate: Optional[float] = None,
    ) -> Optional[ProviderJobRecord]:
        if self._cp is None:
            return None
        config = self._configs.get(provider_id)
        provider = (
            config.provider_type if config is not None else provider_id
        )
        return self._cp.record_provider_job(
            provider=provider,
            request_kind=request_kind,
            request_hash=request_hash,
            status="started",
            cost_estimate=cost_estimate,
            section=section,
            purpose=purpose,
        )

    def record_job_completed(
        self,
        job: Optional[ProviderJobRecord],
        *,
        status: str,
        cost_actual: Optional[float] = None,
        error: Optional[str] = None,
        result_path: Optional[str] = None,
        completed_at_utc: Optional[str] = None,
    ) -> Optional[ProviderJobRecord]:
        if self._cp is None or job is None:
            return None
        return self._cp.update_provider_job(
            job.id,
            status=status,
            cost_actual=cost_actual,
            error=error,
            result_path=result_path,
            completed_at=completed_at_utc,
        )

    # -- snapshot for Reality View / status ----------------------------

    def snapshot(self) -> Mapping[str, Mapping[str, object]]:
        snap: dict[str, Mapping[str, object]] = {}
        for cfg in self._configs.values():
            snap[cfg.provider_id] = {
                **cfg.to_dict(),
                "warm": (
                    cfg.enabled
                    and cfg.has_credentials
                    and any(
                        p.warm
                        for p in self._phase9.warm_providers()
                        if p.provider_id == cfg.provider_id
                    )
                ),
            }
        return snap

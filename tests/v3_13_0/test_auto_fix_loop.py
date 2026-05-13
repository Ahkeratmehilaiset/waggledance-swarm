# SPDX-License-Identifier: BUSL-1.1
"""Tests for AutoFixLoop v1 (Sprint 1 wave 3).

Covers acceptance criteria from auto_fix_loop_runtime_spec.md:
* Lease acquisition + refusal when held by another instance
* Repair proposal for WRT-001 (auto-apply)
* Repair proposal for WRT-002 (auto-apply with rollback plan)
* Repair proposal for WRT-003 (gate-denied without peer RCO approval)
* Sensitive-domain refusal
* Operator-only domain (PER-*) refusal
* Idempotency under replay
* Dry-run mode never executes
* No personal data in fixtures
"""
from __future__ import annotations

from typing import Optional

import pytest

from waggledance.core.v3_13_0.auto_fix_loop import (
    AutoFixLoop,
    LeaseNotHeld,
    LeaseRecord,
    RecoveryCapsuleView,
    RepairIntent,
    RepairOutcome,
    TriggerEvent,
)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _emit_collector(collector: list):
    def emit(envelope: dict) -> str:
        envelope_id = f"evt_{len(collector):04d}"
        envelope["__id"] = envelope_id
        collector.append(envelope)
        return envelope_id
    return emit


def _make_capsule(tool_id: str = "tool_logbook_repair",
                   has_rollback: bool = True) -> RecoveryCapsuleView:
    return RecoveryCapsuleView(
        capsule_id=f"capsule:{tool_id}",
        target_tool_id=tool_id,
        rollback_command="logbook.delete_entry_by_id" if has_rollback else None,
        rebuild_command="logbook.repair_entry",
    )


def _make_event(*, event_id: str = "evt_001",
                  target_tool_id: str = "tool_logbook_repair",
                  target_domain: str = "DOM-011",
                  contract_failures=None) -> TriggerEvent:
    return TriggerEvent(
        event_id=event_id,
        event_type="contract_failure",
        target_tool_id=target_tool_id,
        target_state_refs=["state:logbook_entries"],
        target_domain=target_domain,
        contract_failures=contract_failures or ["CTR-001"],
        ts_utc="2026-05-13T08:10:00Z",
    )


def _make_loop(*, lease: Optional[LeaseRecord] = None,
                capsule: Optional[RecoveryCapsuleView] = None,
                events: list[TriggerEvent] = None,
                risk_class: str = "local_artifact",
                gate_approved: bool = True,
                exec_success: bool = True,
                idempotent: bool = False,
                dry_run: bool = False,
                magma_events: list = None,
                instance_id: str = "instance_a",
                utc_iso_fn=lambda: "2026-05-13T08:10:00Z",
                lease_ttl_seconds: int = 3600):
    magma_events = magma_events if magma_events is not None else []
    lease_state = {"current": lease}
    events_state = {"queue": list(events or [])}

    def fetch_lease():
        return lease_state["current"]

    def write_lease(rec: LeaseRecord) -> str:
        lease_state["current"] = rec if rec.instance_id else None
        return f"audit:lease_{rec.instance_id}"

    def fetch_actionable(cursor):
        return events_state["queue"]

    return AutoFixLoop(
        instance_id=instance_id,
        profile_config_ref="profile:test",
        fetch_lease=fetch_lease,
        write_lease=write_lease,
        fetch_actionable_events=fetch_actionable,
        fetch_recovery_capsule=lambda _tid: capsule,
        classify_intent=lambda _intent: risk_class,
        route_intent_through_gate=lambda _intent: {
            "approved": gate_approved,
            "reason": "auto_approved" if gate_approved else "peer_rco_denied",
            "audit_event_id": "audit:gate",
        },
        execute_repair=lambda _intent: {
            "success": exec_success,
            "error": "" if exec_success else "exec error",
        },
        emit_magma_event=_emit_collector(magma_events),
        idempotency_check=lambda _intent: idempotent,
        clock_fn=lambda: 0.0,
        utc_iso_fn=utc_iso_fn,
        dry_run=dry_run,
        lease_ttl_seconds=lease_ttl_seconds,
    )


# --------------------------------------------------------------------------
# Lease
# --------------------------------------------------------------------------


class TestLease:

    def test_acquire_lease_when_unheld(self):
        events = []
        loop = _make_loop(magma_events=events)
        rec = loop.acquire_lease()
        assert rec.instance_id == "instance_a"
        types = [e["event_type"] for e in events]
        assert "auto_fix_loop.lease_acquired" in types

    def test_acquire_refused_when_other_instance_holds_lease(self):
        existing = LeaseRecord(
            instance_id="instance_b",
            acquired_at_utc="2026-05-13T08:00:00Z",
            last_renewed_at_utc="2026-05-13T08:09:00Z",
        )
        loop = _make_loop(lease=existing)
        with pytest.raises(LeaseNotHeld):
            loop.acquire_lease()

    def test_release_lease_emits_audit(self):
        events = []
        existing = LeaseRecord(
            instance_id="instance_a",
            acquired_at_utc="2026-05-13T08:00:00Z",
            last_renewed_at_utc="2026-05-13T08:00:00Z",
        )
        loop = _make_loop(lease=existing, magma_events=events)
        loop.release_lease()
        types = [e["event_type"] for e in events]
        assert "auto_fix_loop.lease_released" in types

    def test_run_once_refuses_without_lease(self):
        loop = _make_loop()
        with pytest.raises(LeaseNotHeld):
            loop.run_once(cursor="")

    def test_acquire_takes_over_stale_lease(self):
        """Codex RCO round-2 fix: lease > lease_ttl_seconds since
        last_renewed_at_utc is takeover-eligible. Emits a stale audit
        event before writing the new lease."""
        existing = LeaseRecord(
            instance_id="instance_b",
            acquired_at_utc="2026-05-13T07:00:00Z",
            last_renewed_at_utc="2026-05-13T07:00:00Z",
        )
        events = []
        loop = _make_loop(
            lease=existing,
            magma_events=events,
            # now is 09:00:00, 2h after last renewal; ttl 1h -> stale
            utc_iso_fn=lambda: "2026-05-13T09:00:00Z",
            lease_ttl_seconds=3600,
        )
        rec = loop.acquire_lease()
        assert rec.instance_id == "instance_a"
        types = [e["event_type"] for e in events]
        assert "auto_fix_loop.lease_stale_takeover" in types
        assert "auto_fix_loop.lease_acquired" in types
        # Stale takeover event records the superseded instance
        stale_evt = [e for e in events
                      if e["event_type"]
                      == "auto_fix_loop.lease_stale_takeover"][0]
        assert stale_evt["superseded_instance_id"] == "instance_b"

    def test_acquire_refuses_fresh_other_instance_lease(self):
        """Confirm the takeover gate doesn't fire for fresh leases."""
        existing = LeaseRecord(
            instance_id="instance_b",
            acquired_at_utc="2026-05-13T08:55:00Z",
            last_renewed_at_utc="2026-05-13T08:59:00Z",
        )
        loop = _make_loop(
            lease=existing,
            utc_iso_fn=lambda: "2026-05-13T09:00:00Z",
            lease_ttl_seconds=3600,
        )
        with pytest.raises(LeaseNotHeld):
            loop.acquire_lease()

    def test_acquire_refuses_malformed_timestamp(self):
        """Malformed last_renewed_at_utc is treated as NOT stale
        (fail-closed: a corrupted lease row can't be silently taken
        over)."""
        existing = LeaseRecord(
            instance_id="instance_b",
            acquired_at_utc="not-a-valid-iso",
            last_renewed_at_utc="not-a-valid-iso",
        )
        loop = _make_loop(
            lease=existing,
            utc_iso_fn=lambda: "2026-05-13T09:00:00Z",
            lease_ttl_seconds=1,
        )
        with pytest.raises(LeaseNotHeld):
            loop.acquire_lease()


# --------------------------------------------------------------------------
# Repair outcomes by risk class
# --------------------------------------------------------------------------


class TestRepairByRiskClass:

    def _setup_lease(self) -> LeaseRecord:
        return LeaseRecord(
            instance_id="instance_a",
            acquired_at_utc="2026-05-13T08:00:00Z",
            last_renewed_at_utc="2026-05-13T08:00:00Z",
        )

    def test_wrt_001_auto_apply(self):
        loop = _make_loop(
            lease=self._setup_lease(),
            capsule=_make_capsule(),
            events=[_make_event()],
            risk_class="internal_memory",
        )
        results, _ = loop.run_once(cursor="")
        assert len(results) == 1
        assert results[0].outcome == RepairOutcome.APPLIED.value

    def test_wrt_002_auto_apply_with_rollback(self):
        loop = _make_loop(
            lease=self._setup_lease(),
            capsule=_make_capsule(),
            events=[_make_event()],
            risk_class="local_artifact",
        )
        results, _ = loop.run_once(cursor="")
        assert results[0].outcome == RepairOutcome.APPLIED.value

    def test_wrt_003_routes_through_gate_and_denies_when_gate_blocks(self):
        loop = _make_loop(
            lease=self._setup_lease(),
            capsule=_make_capsule(),
            events=[_make_event()],
            risk_class="external_effect",
            gate_approved=False,
        )
        results, _ = loop.run_once(cursor="")
        assert results[0].outcome == RepairOutcome.DENIED.value
        assert "peer_rco_denied" in results[0].reason

    def test_wrt_003_applies_when_gate_approves(self):
        """Gate-approved WRT-003 is allowed -- gate is the single
        choke point for peer-RCO + scope policy."""
        loop = _make_loop(
            lease=self._setup_lease(),
            capsule=_make_capsule(),
            events=[_make_event()],
            risk_class="external_effect",
            gate_approved=True,
        )
        results, _ = loop.run_once(cursor="")
        assert results[0].outcome == RepairOutcome.APPLIED.value


# --------------------------------------------------------------------------
# Refusal paths
# --------------------------------------------------------------------------


class TestRefusal:

    def _setup_lease(self) -> LeaseRecord:
        return LeaseRecord(
            instance_id="instance_a",
            acquired_at_utc="2026-05-13T08:00:00Z",
            last_renewed_at_utc="2026-05-13T08:00:00Z",
        )

    @pytest.mark.parametrize("domain", ["DOM-015", "DOM-021"])
    def test_sensitive_domain_refused(self, domain):
        loop = _make_loop(
            lease=self._setup_lease(),
            capsule=_make_capsule(),
            events=[_make_event(target_domain=domain)],
        )
        results, _ = loop.run_once(cursor="")
        assert results[0].outcome == \
            RepairOutcome.REFUSED_SENSITIVE_DOMAIN.value
        assert domain in results[0].reason

    @pytest.mark.parametrize("domain", ["PER-01", "PER-04"])
    def test_operator_only_domain_refused(self, domain):
        loop = _make_loop(
            lease=self._setup_lease(),
            capsule=_make_capsule(),
            events=[_make_event(target_domain=domain)],
        )
        results, _ = loop.run_once(cursor="")
        assert results[0].outcome == \
            RepairOutcome.REFUSED_SENSITIVE_DOMAIN.value

    def test_missing_capsule_refused(self):
        loop = _make_loop(
            lease=self._setup_lease(),
            capsule=None,
            events=[_make_event()],
        )
        results, _ = loop.run_once(cursor="")
        assert results[0].outcome == RepairOutcome.REFUSED_NO_CAPSULE.value

    def test_capsule_without_rollback_refused(self):
        loop = _make_loop(
            lease=self._setup_lease(),
            capsule=_make_capsule(has_rollback=False),
            events=[_make_event()],
        )
        results, _ = loop.run_once(cursor="")
        assert results[0].outcome == RepairOutcome.REFUSED_NO_CAPSULE.value


# --------------------------------------------------------------------------
# Idempotency
# --------------------------------------------------------------------------


class TestIdempotency:

    def test_replay_marked_as_refused_idempotent(self):
        lease = LeaseRecord(
            instance_id="instance_a",
            acquired_at_utc="2026-05-13T08:00:00Z",
            last_renewed_at_utc="2026-05-13T08:00:00Z",
        )
        loop = _make_loop(
            lease=lease,
            capsule=_make_capsule(),
            events=[_make_event()],
            idempotent=True,
        )
        results, _ = loop.run_once(cursor="")
        assert results[0].outcome == \
            RepairOutcome.REFUSED_IDEMPOTENT_REPLAY.value


# --------------------------------------------------------------------------
# Dry-run mode
# --------------------------------------------------------------------------


class TestDryRun:

    def test_dry_run_proposes_but_does_not_execute(self):
        lease = LeaseRecord(
            instance_id="instance_a",
            acquired_at_utc="2026-05-13T08:00:00Z",
            last_renewed_at_utc="2026-05-13T08:00:00Z",
        )
        events = []
        loop = _make_loop(
            lease=lease,
            capsule=_make_capsule(),
            events=[_make_event()],
            risk_class="local_artifact",
            magma_events=events,
            dry_run=True,
        )
        results, _ = loop.run_once(cursor="")
        assert results[0].outcome == RepairOutcome.PROPOSED.value
        assert "dry_run" in results[0].reason
        # repair_applied event must NOT be emitted
        types = [e["event_type"] for e in events]
        assert "auto_fix_loop.repair_applied" not in types
        assert "auto_fix_loop.repair_proposed" in types


# --------------------------------------------------------------------------
# Cursor advancement
# --------------------------------------------------------------------------


class TestCursorAdvance:

    def test_cursor_advances_to_last_event(self):
        lease = LeaseRecord(
            instance_id="instance_a",
            acquired_at_utc="2026-05-13T08:00:00Z",
            last_renewed_at_utc="2026-05-13T08:00:00Z",
        )
        loop = _make_loop(
            lease=lease,
            capsule=_make_capsule(),
            events=[
                _make_event(event_id="evt_a"),
                _make_event(event_id="evt_b"),
                _make_event(event_id="evt_c"),
            ],
        )
        results, new_cursor = loop.run_once(cursor="")
        assert len(results) == 3
        assert new_cursor == "evt_c"

    def test_no_events_returns_unchanged_cursor(self):
        lease = LeaseRecord(
            instance_id="instance_a",
            acquired_at_utc="2026-05-13T08:00:00Z",
            last_renewed_at_utc="2026-05-13T08:00:00Z",
        )
        loop = _make_loop(lease=lease, events=[])
        results, new_cursor = loop.run_once(cursor="evt_prev")
        assert results == []
        assert new_cursor == "evt_prev"

    def test_cursor_advanced_chain_per_event(self):
        """Codex RCO round-2 fix: cursor_before MUST track the running
        cursor per event, NOT the original cursor passed to run_once.
        Audit consumers reconstruct the per-event chain from these
        events."""
        lease = LeaseRecord(
            instance_id="instance_a",
            acquired_at_utc="2026-05-13T08:00:00Z",
            last_renewed_at_utc="2026-05-13T08:00:00Z",
        )
        events = []
        loop = _make_loop(
            lease=lease,
            capsule=_make_capsule(),
            events=[
                _make_event(event_id="evt_a"),
                _make_event(event_id="evt_b"),
                _make_event(event_id="evt_c"),
            ],
            magma_events=events,
        )
        loop.run_once(cursor="start")
        cursor_chain = [
            (e["cursor_before"], e["cursor_after"])
            for e in events
            if e["event_type"] == "auto_fix_loop.cursor_advanced"
        ]
        assert cursor_chain == [
            ("start", "evt_a"),
            ("evt_a", "evt_b"),
            ("evt_b", "evt_c"),
        ]

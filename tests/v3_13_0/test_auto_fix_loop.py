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
                lease_ttl_seconds: int = 3600,
                persist_empty_release: bool = False,
                classify_raises_for: set[str] = None,
                executed_intents: list[RepairIntent] = None):
    magma_events = magma_events if magma_events is not None else []
    lease_state = {"current": lease}
    events_state = {"queue": list(events or [])}

    def fetch_lease():
        return lease_state["current"]

    def write_lease(rec: LeaseRecord) -> str:
        lease_state["current"] = (
            rec if rec.instance_id or persist_empty_release else None
        )
        return f"audit:lease_{rec.instance_id}"

    def fetch_actionable(cursor):
        return events_state["queue"]

    def classify(intent: RepairIntent) -> str:
        if classify_raises_for and intent.triggering_event_id in classify_raises_for:
            raise RuntimeError("synthetic handler failure")
        return risk_class

    def execute(intent: RepairIntent) -> dict:
        if executed_intents is not None:
            executed_intents.append(intent)
        return {
            "success": exec_success,
            "error": "" if exec_success else "exec error",
        }

    return AutoFixLoop(
        instance_id=instance_id,
        profile_config_ref="profile:test",
        fetch_lease=fetch_lease,
        write_lease=write_lease,
        fetch_actionable_events=fetch_actionable,
        fetch_recovery_capsule=lambda _tid: capsule,
        classify_intent=classify,
        route_intent_through_gate=lambda _intent: {
            "approved": gate_approved,
            "reason": "auto_approved" if gate_approved else "peer_rco_denied",
            "audit_event_id": "audit:gate",
        },
        execute_repair=execute,
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

    def test_release_tombstone_allows_next_acquire(self):
        events = []
        existing = LeaseRecord(
            instance_id="instance_a",
            acquired_at_utc="2026-05-13T08:00:00Z",
            last_renewed_at_utc="2026-05-13T08:00:00Z",
        )
        loop = _make_loop(
            lease=existing,
            magma_events=events,
            persist_empty_release=True,
        )

        loop.release_lease()
        rec = loop.acquire_lease()

        assert rec.instance_id == "instance_a"
        types = [e["event_type"] for e in events]
        assert "auto_fix_loop.lease_released" in types
        assert "auto_fix_loop.lease_acquired" in types

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

    def test_wrt_002_prefers_rollback_when_rebuild_also_exists(self):
        executed = []
        capsule = RecoveryCapsuleView(
            capsule_id="capsule:tool_logbook_repair",
            target_tool_id="tool_logbook_repair",
            rollback_command="logbook.delete_entry_by_id",
            rebuild_command="logbook.repair_entry",
        )
        loop = _make_loop(
            lease=self._setup_lease(),
            capsule=capsule,
            events=[_make_event()],
            risk_class="local_artifact",
            executed_intents=executed,
        )

        results, _ = loop.run_once(cursor="")

        assert results[0].outcome == RepairOutcome.APPLIED.value
        assert len(executed) == 1
        assert executed[0].repair_command == "logbook.delete_entry_by_id"

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

    def test_handler_exception_records_failure_and_advances_cursor(self):
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
                _make_event(event_id="evt_poison"),
                _make_event(event_id="evt_after"),
            ],
            magma_events=events,
            classify_raises_for={"evt_poison"},
        )

        results, new_cursor = loop.run_once(cursor="start")

        assert new_cursor == "evt_after"
        assert [r.outcome for r in results] == [
            RepairOutcome.FAILED.value,
            RepairOutcome.APPLIED.value,
        ]
        assert results[0].reason == "handler_exception:RuntimeError"
        failed_events = [
            e for e in events
            if e["event_type"] == "auto_fix_loop.repair_failed"
            and e.get("reason") == "handler_exception"
        ]
        assert failed_events
        assert failed_events[0]["exception_type"] == "RuntimeError"
        cursor_chain = [
            (e["cursor_before"], e["cursor_after"])
            for e in events
            if e["event_type"] == "auto_fix_loop.cursor_advanced"
        ]
        assert cursor_chain == [
            ("start", "evt_poison"),
            ("evt_poison", "evt_after"),
        ]


# --------------------------------------------------------------------------
# AFL4 / PR #378 handler_exception forensics -- regression coverage
# --------------------------------------------------------------------------
#
# These tests lock the bounded-error-string + exception_type + cursor-advance
# + per-failure repair_id contract added in PR #378 auto_fix_loop_resilience.
# Without these locks, a future refactor could:
#   * drop the str(exc)[:200] truncation and leak full stack traces into
#     MAGMA envelopes (operator forensics gets noisy + risks PII leak in
#     exception args), or
#   * lose the exception_type field and force operators to parse free-text
#     error messages to triage handler failures by class, or
#   * reuse a single repair_id across multiple poison events in one run
#     and break audit-trail correlation, or
#   * silently halt the cursor on the first poison event and let the loop
#     stall instead of poison-quarantining and advancing.
# --------------------------------------------------------------------------


def _make_loop_with_custom_classify_raise(
    *,
    lease: LeaseRecord,
    events: list,
    magma_events: list,
    classify_exc_factory,
    raise_for: set[str],
) -> AutoFixLoop:
    """Build an AutoFixLoop whose classify_intent raises a custom exception
    (factory-produced) for a given set of triggering_event_ids.

    Used by the forensics regression tests; the production _make_loop
    helper hardcodes RuntimeError. We need to vary the exception class
    + the message length to exercise the truncation + exception_type
    contracts independently."""
    lease_state = {"current": lease}
    events_state = {"queue": list(events)}

    def fetch_lease():
        return lease_state["current"]

    def write_lease(rec):
        lease_state["current"] = rec if rec.instance_id else None
        return f"audit:lease_{rec.instance_id}"

    def classify(intent):
        if intent.triggering_event_id in raise_for:
            raise classify_exc_factory(intent.triggering_event_id)
        return "local_artifact"

    return AutoFixLoop(
        instance_id="instance_a",
        profile_config_ref="profile:test",
        fetch_lease=fetch_lease,
        write_lease=write_lease,
        fetch_actionable_events=lambda _cursor: events_state["queue"],
        fetch_recovery_capsule=lambda _tid: _make_capsule(),
        classify_intent=classify,
        route_intent_through_gate=lambda _intent: {
            "approved": True,
            "reason": "auto_approved",
            "audit_event_id": "audit:gate",
        },
        execute_repair=lambda _intent: {"success": True, "error": ""},
        emit_magma_event=_emit_collector(magma_events),
        idempotency_check=lambda _intent: False,
        clock_fn=lambda: 0.0,
        utc_iso_fn=lambda: "2026-05-14T06:30:00Z",
    )


class TestHandlerExceptionForensics:
    """PR #378 AFL4 fix added handler_exception forensics. These tests
    lock the audit envelope contract: bounded error string, exception_type
    field, per-failure repair_id, cursor advances past every poison event."""

    LEASE = LeaseRecord(
        instance_id="instance_a",
        acquired_at_utc="2026-05-14T06:00:00Z",
        last_renewed_at_utc="2026-05-14T06:00:00Z",
    )

    def test_handler_exception_error_string_is_truncated_to_200_chars(self):
        """str(exc)[:200] truncation contract: very long exception messages
        must not leak full text into the MAGMA envelope's `error` field."""
        long_message = "X" * 1000  # 1000 chars; expect bounded to 200
        events_list = []
        loop = _make_loop_with_custom_classify_raise(
            lease=self.LEASE,
            events=[_make_event(event_id="evt_poison_long")],
            magma_events=events_list,
            classify_exc_factory=lambda _evt: RuntimeError(long_message),
            raise_for={"evt_poison_long"},
        )

        results, _new_cursor = loop.run_once(cursor="start")

        assert results[0].outcome == RepairOutcome.FAILED.value
        failed = [
            e for e in events_list
            if e["event_type"] == "auto_fix_loop.repair_failed"
            and e.get("reason") == "handler_exception"
        ]
        assert failed, f"expected one handler_exception event; got {events_list}"
        error_field = failed[0]["error"]
        # Bound at 200 chars per source line 321 of auto_fix_loop.py.
        assert len(error_field) == 200, (
            f"error field must be truncated to 200 chars; got {len(error_field)}"
        )
        # The truncated content must be a prefix of the original message
        # (no reordering / encoding alteration), so operator forensics can
        # still match against the original exception text by prefix.
        assert long_message.startswith(error_field), (
            "truncated error must be a prefix of the original exception "
            "message"
        )
        # repair_id is non-empty UUID-shaped; locks the per-failure id.
        assert failed[0]["repair_id"] and len(failed[0]["repair_id"]) == 36

    def test_handler_exception_records_exception_class_name_distinctly(self):
        """Different exception classes must surface as distinct
        `exception_type` field values so operator triage can filter by
        class without parsing free-text error messages."""

        class CustomDomainError(Exception):
            """Operator-defined exception used only by this test."""

        for exc_cls in (RuntimeError, TypeError, KeyError, ValueError,
                         CustomDomainError):
            events_list = []
            loop = _make_loop_with_custom_classify_raise(
                lease=self.LEASE,
                events=[_make_event(event_id=f"evt_for_{exc_cls.__name__}")],
                magma_events=events_list,
                classify_exc_factory=lambda _evt, _exc_cls=exc_cls:
                    _exc_cls("synthetic"),
                raise_for={f"evt_for_{exc_cls.__name__}"},
            )

            loop.run_once(cursor="start")

            failed = [
                e for e in events_list
                if e["event_type"] == "auto_fix_loop.repair_failed"
                and e.get("reason") == "handler_exception"
            ]
            assert failed, (
                f"expected handler_exception event for {exc_cls.__name__}; "
                f"got {[e['event_type'] for e in events_list]}"
            )
            assert failed[0]["exception_type"] == exc_cls.__name__, (
                f"exception_type field must equal class name "
                f"{exc_cls.__name__!r}; got "
                f"{failed[0].get('exception_type')!r}"
            )

    def test_multiple_poison_events_each_get_unique_repair_id_and_advance(self):
        """Three poison events in a row: each must get a distinct
        repair_id (uuid4) + the cursor must advance through every poison
        (not stall on the first one) + 3 repair_failed events emitted."""
        events_list = []
        loop = _make_loop_with_custom_classify_raise(
            lease=self.LEASE,
            events=[
                _make_event(event_id="evt_poison_1"),
                _make_event(event_id="evt_poison_2"),
                _make_event(event_id="evt_poison_3"),
            ],
            magma_events=events_list,
            classify_exc_factory=lambda evt: RuntimeError(f"fail_{evt}"),
            raise_for={"evt_poison_1", "evt_poison_2", "evt_poison_3"},
        )

        results, new_cursor = loop.run_once(cursor="start")

        # All three poisoned events processed; cursor reached the last one.
        assert new_cursor == "evt_poison_3"
        assert [r.outcome for r in results] == [
            RepairOutcome.FAILED.value,
            RepairOutcome.FAILED.value,
            RepairOutcome.FAILED.value,
        ]

        failed = [
            e for e in events_list
            if e["event_type"] == "auto_fix_loop.repair_failed"
            and e.get("reason") == "handler_exception"
        ]
        assert len(failed) == 3, (
            f"expected 3 repair_failed events; got {len(failed)}"
        )

        # Distinct repair_ids per failure (no reuse across the run).
        repair_ids = [e["repair_id"] for e in failed]
        assert len(set(repair_ids)) == 3, (
            f"repair_id must be unique per failure; got duplicates in "
            f"{repair_ids}"
        )

        # Each failed event's triggering_event_id maps 1:1 to its poison.
        triggering_ids = [e["triggering_event_id"] for e in failed]
        assert triggering_ids == [
            "evt_poison_1", "evt_poison_2", "evt_poison_3",
        ]

        # Cursor advances chain through every poison.
        cursor_chain = [
            (e["cursor_before"], e["cursor_after"])
            for e in events_list
            if e["event_type"] == "auto_fix_loop.cursor_advanced"
        ]
        assert cursor_chain == [
            ("start", "evt_poison_1"),
            ("evt_poison_1", "evt_poison_2"),
            ("evt_poison_2", "evt_poison_3"),
        ]

    def test_handler_exception_audit_envelope_has_all_forensic_fields(self):
        """Locks the exact field set on the repair_failed audit envelope so
        a future emit refactor cannot silently drop any forensic field."""
        events_list = []
        loop = _make_loop_with_custom_classify_raise(
            lease=self.LEASE,
            events=[_make_event(event_id="evt_envelope_shape")],
            magma_events=events_list,
            classify_exc_factory=lambda _evt:
                TypeError("synthetic envelope shape"),
            raise_for={"evt_envelope_shape"},
        )

        loop.run_once(cursor="start")

        failed = [
            e for e in events_list
            if e["event_type"] == "auto_fix_loop.repair_failed"
            and e.get("reason") == "handler_exception"
        ]
        assert failed
        envelope = failed[0]

        # Required forensic fields per source line 314-323 of auto_fix_loop.py.
        required_fields = {
            "event_type",
            "instance_id",
            "repair_id",
            "triggering_event_id",
            "reason",
            "exception_type",
            "error",
            "ts_utc",
        }
        missing = required_fields - set(envelope.keys())
        assert not missing, (
            f"repair_failed audit envelope is missing forensic fields: "
            f"{sorted(missing)}; full envelope keys: {sorted(envelope)}"
        )

        # Concrete values match the source contract.
        assert envelope["event_type"] == "auto_fix_loop.repair_failed"
        assert envelope["instance_id"] == "instance_a"
        assert envelope["reason"] == "handler_exception"
        assert envelope["exception_type"] == "TypeError"
        assert envelope["triggering_event_id"] == "evt_envelope_shape"
        assert envelope["ts_utc"]  # non-empty
        assert isinstance(envelope["error"], str)

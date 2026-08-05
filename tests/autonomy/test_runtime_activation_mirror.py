# SPDX-License-Identifier: BUSL-1.1
"""Opt-in read-only activation snapshot mirror in ``AutonomyRuntime``."""

from __future__ import annotations

import hashlib
import logging
import threading
import time

from waggledance.core.autonomy.runtime import AutonomyRuntime
from waggledance.core.capabilities.activation_contracts import (
    INITIAL_PREVIOUS_HEAD_DIGEST,
    build_activation_head,
    build_authority_ceiling,
    build_capability_variant,
    build_expression_context,
)
from waggledance.core.capabilities.activation_mirror import SNAPSHOT_SCHEMA
from waggledance.core.capabilities.registry import CapabilityRegistry
from waggledance.core.domain.autonomy import (
    CapabilityCategory,
    CapabilityContract,
)


def _digest(label: str) -> str:
    return "sha256:" + hashlib.sha256(label.encode("ascii")).hexdigest()


def _activation_snapshot(*, active: bool = True) -> dict:
    variant_ceiling = build_authority_ceiling(
        max_risk_class="local_artifact",
        authority_scope_digests=[_digest("scope:a"), _digest("scope:b")],
    )
    charter = build_authority_ceiling(
        max_risk_class="internal_memory",
        authority_scope_digests=[_digest("scope:a"), _digest("scope:c")],
    )
    expressed = build_authority_ceiling(
        max_risk_class="internal_memory",
        authority_scope_digests=[_digest("scope:a")],
    )
    variant = build_capability_variant(
        family_id="detect.fixture",
        risk_class="internal_memory",
        artifact_digest=_digest("artifact"),
        input_schema_digest=_digest("input"),
        output_schema_digest=_digest("output"),
        compatibility_digest=_digest("compatibility"),
        authority_ceiling_digest=variant_ceiling.ceiling_digest,
    )
    context = build_expression_context(
        profile_head_digest=_digest("profile"),
        policy_head_digest=_digest("policy"),
        resource_head_digest=_digest("resource"),
        domain_head_digest=_digest("domain"),
        environment_head_digest=_digest("environment"),
        charter_ceiling_digest=charter.ceiling_digest,
        expressed_ceiling_digest=expressed.ceiling_digest,
    )
    head = build_activation_head(
        generation=0,
        previous_head_digest=INITIAL_PREVIOUS_HEAD_DIGEST,
        expression_context_digest=context.context_digest,
        active_variant_digests=[variant.variant_digest] if active else [],
        shadow_variant_digests=[] if active else [variant.variant_digest],
    )
    return {
        "schema_version": SNAPSHOT_SCHEMA,
        "head": head.to_mapping(),
        "expected_activation_head_digest": head.head_digest,
        "context": context.to_mapping(),
        "variants": [variant.to_mapping()],
        "variant_ceilings": [variant_ceiling.to_mapping()],
        "charter_ceiling": charter.to_mapping(),
        "expressed_ceiling": expressed.to_mapping(),
        "expected_profile_head_digest": context.profile_head_digest,
        "expected_policy_head_digest": context.policy_head_digest,
        "expected_resource_head_digest": context.resource_head_digest,
        "expected_domain_head_digest": context.domain_head_digest,
        "expected_environment_head_digest": context.environment_head_digest,
    }


class _Selection:
    fallback_used = False

    def __init__(self, capabilities) -> None:
        self.selected = list(capabilities)


class _RouteResult:
    autonomy_consult = None
    autonomy_served = False
    solver_call_trace = []
    quality_path = "gold"

    def __init__(self, capabilities) -> None:
        self.selection = _Selection(capabilities)


class _Executor:
    available = True

    def execute(self, **_payload):
        return {"success": True, "value": 42}


def _runtime(
    *, enabled: bool, provider=None, capabilities: bool = True
) -> tuple[AutonomyRuntime, _RouteResult]:
    registry = CapabilityRegistry(load_builtins=False)
    capability = CapabilityContract(
        capability_id="detect.fixture",
        category=CapabilityCategory.DETECT,
        description="Fixture detector",
        success_criteria=["success"],
    )
    registry.register(capability)
    registry.register_executor("detect.fixture", _Executor())
    runtime = AutonomyRuntime(
        capability_registry=registry,
        enable_magma=False,
        enable_persistence=False,
        enable_activation_mirror=enabled,
        activation_snapshot_provider=provider,
    )
    route = _RouteResult([capability] if capabilities else [])
    runtime.solver_router.route = lambda _i, _q, _c: route
    runtime.action_bus.register_executor(
        "detect.fixture", lambda _action: {"success": True, "value": 42}
    )
    return runtime, route


def test_default_off_never_invokes_provider_or_changes_production() -> None:
    calls = 0

    def provider():
        nonlocal calls
        calls += 1
        return _activation_snapshot()

    runtime, _ = _runtime(enabled=False, provider=provider)
    result = runtime.handle_query("private query that mirror must not receive")
    snapshot = runtime.activation_mirror_snapshot()
    assert result["executed"] is True
    assert calls == 0
    assert snapshot["enabled"] is False
    assert snapshot["provider_configured"] is True
    assert snapshot["provider_failure_count"] == 0
    assert snapshot["verification_failure_count"] == 0
    assert snapshot["report"]["sample_count"] == 0


def test_valid_active_match_observes_but_does_not_gate_execution() -> None:
    runtime, _ = _runtime(enabled=True, provider=_activation_snapshot)
    result = runtime.handle_query("execute the normal deterministic fixture")
    assert runtime.wait_for_activation_mirror(timeout=2.0) is True
    assert result["executed"] is True
    record = list(runtime._activation_mirror_records)[0]
    assert record["selection_valid"] is True
    assert record["active_family_match"] is True
    assert record["runtime_authority_granted"] is False
    assert record["routing_influence_applied"] is False
    assert record["production_decision_unchanged"] is True
    assert record["execution_permission_granted"] is False
    report = runtime.activation_mirror_snapshot()["report"]
    assert report["active_match_count"] == 1
    assert report["runtime_authority_granted"] is False


def test_shadow_only_is_a_miss_but_production_route_still_executes() -> None:
    runtime, _ = _runtime(
        enabled=True,
        provider=lambda: _activation_snapshot(active=False),
    )
    result = runtime.handle_query("normal fixture")
    assert runtime.wait_for_activation_mirror(timeout=2.0) is True
    record = list(runtime._activation_mirror_records)[0]
    assert result["executed"] is True
    assert record["selection_valid"] is True
    assert record["active_family_match"] is False
    assert runtime.activation_mirror_snapshot()["verification_failure_count"] == 0


def test_stale_snapshot_is_recorded_invalid_without_changing_route() -> None:
    def stale_provider():
        snapshot = _activation_snapshot()
        snapshot["expected_policy_head_digest"] = _digest("stale-policy")
        return snapshot

    runtime, _ = _runtime(enabled=True, provider=stale_provider)
    result = runtime.handle_query("normal fixture")
    assert runtime.wait_for_activation_mirror(timeout=2.0) is True
    record = list(runtime._activation_mirror_records)[0]
    assert result["executed"] is True
    assert record["selection_valid"] is False
    assert record["active_family_match"] is False
    snapshot = runtime.activation_mirror_snapshot()
    assert snapshot["verification_failure_count"] == 1
    assert snapshot["report"]["selection_invalid_count"] == 1


def test_provider_failure_is_isolated_counted_and_payload_free(caplog) -> None:
    secret = "provider leaked TOP_SECRET_snapshot_payload"

    def provider():
        raise RuntimeError(secret)

    runtime, _ = _runtime(enabled=True, provider=provider)
    with caplog.at_level(logging.DEBUG, logger="waggledance.autonomy.runtime"):
        result = runtime.handle_query("normal fixture")
        assert runtime.wait_for_activation_mirror(timeout=2.0) is True
    snapshot = runtime.activation_mirror_snapshot()
    assert result["executed"] is True
    assert snapshot["provider_failure_count"] == 1
    assert snapshot["verification_failure_count"] == 0
    assert snapshot["report"]["sample_count"] == 0
    assert secret not in repr(snapshot)
    assert secret not in caplog.text


def test_enabled_without_provider_fails_observationally_only() -> None:
    runtime, _ = _runtime(enabled=True, provider=None)
    result = runtime.handle_query("normal fixture")
    assert runtime.wait_for_activation_mirror(timeout=2.0) is True
    snapshot = runtime.activation_mirror_snapshot()
    assert result["executed"] is True
    assert snapshot["provider_configured"] is False
    assert snapshot["provider_failure_count"] == 1
    assert snapshot["report"]["sample_count"] == 0


def test_no_capability_early_return_is_still_mirrored() -> None:
    runtime, _ = _runtime(
        enabled=True,
        provider=_activation_snapshot,
        capabilities=False,
    )
    result = runtime.handle_query("nothing selected")
    assert runtime.wait_for_activation_mirror(timeout=2.0) is True
    assert result["error"] == "No capabilities available"
    record = list(runtime._activation_mirror_records)[0]
    assert record["selection_valid"] is True
    assert record["active_family_match"] is False


def test_runtime_buffer_is_bounded_to_report_contract() -> None:
    runtime, _ = _runtime(enabled=True, provider=_activation_snapshot)
    for _ in range(300):
        runtime._activation_mirror_safe("detect.fixture")
    assert len(runtime._activation_mirror_records) == 256
    report = runtime.activation_mirror_snapshot()["report"]
    assert report["sample_count"] == 256
    assert report["active_match_count"] == 256


def test_provider_cannot_mutate_the_current_production_decision() -> None:
    baseline_runtime, _ = _runtime(enabled=False, provider=None)
    baseline = baseline_runtime.handle_query(
        "production outcome must already be frozen"
    )
    runtime, route = _runtime(enabled=True, provider=None)

    def mutating_provider():
        route.selection.selected.clear()
        return _activation_snapshot()

    runtime._activation_snapshot_provider = mutating_provider
    result = runtime.handle_query("production outcome must already be frozen")
    assert result["executed"] is True
    assert result["capability"] == "detect.fixture"
    assert runtime.wait_for_activation_mirror(timeout=2.0) is True
    comparable_baseline = {key: value for key, value in baseline.items() if key != "elapsed_ms"}
    comparable_result = {key: value for key, value in result.items() if key != "elapsed_ms"}
    assert comparable_result == comparable_baseline
    assert route.selection.selected == []  # mutation happened off-path
    record = list(runtime._activation_mirror_records)[0]
    assert record["active_family_match"] is True
    assert record["production_decision_unchanged"] is True


def test_blocking_provider_never_blocks_the_query_response() -> None:
    provider_started = threading.Event()
    release_provider = threading.Event()

    def blocking_provider():
        provider_started.set()
        release_provider.wait(2.0)
        return _activation_snapshot()

    runtime, _ = _runtime(enabled=True, provider=blocking_provider)
    started = time.perf_counter()
    result = runtime.handle_query("latency isolation")
    query_elapsed = time.perf_counter() - started
    assert result["executed"] is True
    assert query_elapsed < 1.0
    assert provider_started.wait(1.0) is True
    assert runtime.wait_for_activation_mirror(timeout=0.01) is False
    release_provider.set()
    assert runtime.wait_for_activation_mirror(timeout=2.0) is True
    assert runtime.activation_mirror_snapshot()["report"]["sample_count"] == 1


def test_full_background_queue_drops_observation_not_production(monkeypatch) -> None:
    import waggledance.core.autonomy.runtime as runtime_module

    class _FullDispatcher:
        @staticmethod
        def submit(_callback):
            return None

    monkeypatch.setattr(
        runtime_module,
        "_activation_mirror_dispatcher",
        lambda: _FullDispatcher(),
    )
    runtime, _ = _runtime(enabled=True, provider=_activation_snapshot)
    result = runtime.handle_query("queue saturation")
    snapshot = runtime.activation_mirror_snapshot()
    assert result["executed"] is True
    assert runtime.wait_for_activation_mirror(0.01) is False
    assert snapshot["queue_drop_count"] == 1
    assert snapshot["report"]["sample_count"] == 0


def test_dispatcher_exception_cannot_replace_completed_response(monkeypatch) -> None:
    import waggledance.core.autonomy.runtime as runtime_module

    class _BrokenDispatcher:
        @staticmethod
        def submit(_callback):
            raise RuntimeError("dispatcher internals MUST_NOT_LEAK")

    monkeypatch.setattr(
        runtime_module,
        "_activation_mirror_dispatcher",
        lambda: _BrokenDispatcher(),
    )
    runtime, _ = _runtime(enabled=True, provider=_activation_snapshot)
    result = runtime.handle_query("dispatch failure isolation")
    snapshot = runtime.activation_mirror_snapshot()
    assert result["executed"] is True
    assert runtime.wait_for_activation_mirror(0.01) is False
    assert snapshot["dispatch_failure_count"] == 1
    assert snapshot["queue_drop_count"] == 0
    assert snapshot["report"]["sample_count"] == 0
    assert "MUST_NOT_LEAK" not in repr(snapshot)


def test_concurrent_submissions_track_actual_latest_enqueued_event(
    monkeypatch,
) -> None:
    import waggledance.core.autonomy.runtime as runtime_module

    first_entered = threading.Event()
    release_first = threading.Event()
    returned_events = [threading.Event(), threading.Event()]
    enqueue_order = []
    call_lock = threading.Lock()
    call_count = 0

    class _CoordinatedDispatcher:
        @staticmethod
        def submit(_callback):
            nonlocal call_count
            with call_lock:
                call_index = call_count
                call_count += 1
            if call_index == 0:
                first_entered.set()
                assert release_first.wait(2.0) is True
            enqueue_order.append(returned_events[call_index])
            return returned_events[call_index]

    monkeypatch.setattr(
        runtime_module,
        "_activation_mirror_dispatcher",
        lambda: _CoordinatedDispatcher(),
    )
    runtime, _ = _runtime(enabled=True, provider=_activation_snapshot)
    first = threading.Thread(
        target=runtime._schedule_activation_mirror,
        args=("detect.fixture",),
    )
    second = threading.Thread(
        target=runtime._schedule_activation_mirror,
        args=("detect.fixture",),
    )
    first.start()
    assert first_entered.wait(1.0) is True
    second.start()
    wait_results = []
    waiter_done = threading.Event()

    def wait_while_submit_is_pending():
        wait_results.append(runtime.wait_for_activation_mirror(0.01))
        waiter_done.set()

    waiter = threading.Thread(target=wait_while_submit_is_pending)
    waiter.start()
    assert waiter_done.wait(0.05) is False
    release_first.set()
    first.join(2.0)
    second.join(2.0)
    waiter.join(2.0)
    assert first.is_alive() is False
    assert second.is_alive() is False
    assert waiter.is_alive() is False
    assert wait_results == [False]
    assert len(enqueue_order) == 2
    assert runtime._activation_mirror_last_completion is enqueue_order[-1]
    assert runtime.wait_for_activation_mirror(0.01) is False
    enqueue_order[-1].set()
    assert runtime.wait_for_activation_mirror(0.1) is True

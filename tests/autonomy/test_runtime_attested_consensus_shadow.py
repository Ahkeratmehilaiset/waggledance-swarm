"""Default-off, off-path attested-consensus observer in AutonomyRuntime."""

from __future__ import annotations

import hashlib
import logging
import threading
import time

from waggledance.core.autonomy.runtime import AutonomyRuntime
from waggledance.core.autonomy.resource_kernel import (
    AdmissionDecision,
    AdmissionResult,
)
from waggledance.core.capabilities.activation_admission_intent import (
    build_activation_admission_intent,
)
from waggledance.core.capabilities.registry import CapabilityRegistry
from waggledance.core.domain.autonomy import (
    CapabilityCategory,
    CapabilityContract,
)
from waggledance.core.orchestration.attestation_log import (
    INITIAL_PREVIOUS_LOG_HEAD_DIGEST,
    build_attestation_log_entry,
    build_attestation_log_snapshot,
    build_next_attestation_log_snapshot,
)
from waggledance.core.orchestration.attested_consensus_shadow import (
    GATE_EXPECTATION_KEYS,
    GATE_MATERIAL_KEYS,
)
from waggledance.core.orchestration.consensus_admission_policy import (
    build_consensus_admission_policy,
)
from waggledance.core.orchestration.evidence_attestation import (
    build_evidence_attestation,
    derive_signing_key_digest,
)
from waggledance.core.orchestration.evidence_consensus import (
    build_evidence_diversity,
    build_inhibitory_ballot,
)
from waggledance.core.orchestration.provenance_registry import (
    INITIAL_PREVIOUS_REGISTRY_HEAD_DIGEST,
    build_provenance_registry_snapshot,
    build_trusted_provenance_binding,
)


def _digest(label: str) -> str:
    return "sha256:" + hashlib.sha256(label.encode("ascii")).hexdigest()


def _gate_inputs(
    ballot_type: str = "support",
) -> tuple[dict[str, object], dict[str, object], object]:
    key = b"r" * 32
    provenance = {
        "reviewer_lineage_digest": _digest("lineage"),
        "model_digest": _digest("model"),
        "provider_digest": _digest("provider"),
        "tool_digest": _digest("tool"),
        "data_corpus_digest": _digest("corpus"),
        "host_digest": _digest("host"),
        "review_policy_digest": _digest("review-policy"),
    }
    binding = build_trusted_provenance_binding(
        signer_cell_id=_digest("reviewer-cell"),
        reviewer_activation_scope_digest=_digest("reviewer-scope"),
        signing_key_digest=derive_signing_key_digest(key),
        **provenance,
        status="active",
    )
    registry = build_provenance_registry_snapshot(
        generation=0,
        previous_registry_head_digest=INITIAL_PREVIOUS_REGISTRY_HEAD_DIGEST,
        bindings=[binding],
    ).to_mapping()
    policy = build_consensus_admission_policy(
        required_independent_support=1,
        maximum_evidence_records=4,
        maximum_ballots=4,
        maximum_attestations=4,
    )
    base = build_attestation_log_snapshot(
        generation=0,
        previous_log_head_digest=INITIAL_PREVIOUS_LOG_HEAD_DIGEST,
        entries=[],
    ).to_mapping()
    scope = _digest("target-scope")
    intent = build_activation_admission_intent(
        activation_scope_digest=scope,
        query_digest=_digest("review-query"),
        expected_current_bundle_digest=_digest("bundle:current"),
        expected_current_activation_head_digest=_digest("head:current"),
        expected_current_store_revision=9,
        proposed_bundle_digest=_digest("bundle:proposed"),
        proposed_activation_head_digest=_digest("head:proposed"),
        proposed_store_revision=10,
        proposed_previous_bundle_digest=_digest("bundle:current"),
        proposed_previous_activation_head_digest=_digest("head:current"),
        trust_registry_head_digest=registry["registry_head_digest"],
        attestation_log_base_head_digest=base["log_head_digest"],
        consensus_policy_digest=policy.policy_digest,
        required_independent_support=1,
    )
    evidence = build_evidence_diversity(
        query_digest=intent["query_digest"],
        decision_digest=intent["decision_digest"],
        candidate_digest=intent["candidate_digest"],
        activation_head_digest=intent["activation_head_digest"],
        **provenance,
    ).to_mapping()
    ballot = build_inhibitory_ballot(
        ballot_type=ballot_type,
        evidence=evidence,
    ).to_mapping()
    attestation = build_evidence_attestation(
        evidence=evidence,
        ballot=ballot,
        trust_registry_head_digest=registry["registry_head_digest"],
        activation_scope_digest=scope,
        admission_challenge_digest=intent["admission_challenge_digest"],
        key=key,
    ).to_mapping()
    entry = build_attestation_log_entry(
        activation_scope_digest=scope,
        admission_challenge_digest=intent["admission_challenge_digest"],
        evidence_digest=evidence["evidence_digest"],
        ballot_digest=ballot["ballot_digest"],
        attestation_digest=attestation["attestation_digest"],
        reviewer_lineage_digest=evidence["reviewer_lineage_digest"],
    )
    closed = build_next_attestation_log_snapshot(
        base,
        expected_current_log_head_digest=base["log_head_digest"],
        appended_entries=[entry],
    ).to_mapping()
    combined = {
        "activation_admission_intent": intent,
        "policy": policy,
        "expected_consensus_policy_digest": policy.policy_digest,
        "expected_activation_scope_digest": scope,
        "expected_query_digest": intent["query_digest"],
        "expected_current_bundle_digest": _digest("bundle:current"),
        "expected_current_activation_head_digest": _digest("head:current"),
        "expected_current_store_revision": 9,
        "expected_proposed_bundle_digest": _digest("bundle:proposed"),
        "expected_proposed_activation_head_digest": _digest("head:proposed"),
        "expected_proposed_store_revision": 10,
        "provenance_registry_snapshot": registry,
        "expected_trust_registry_head_digest": registry[
            "registry_head_digest"
        ],
        "attestation_log_base_snapshot": base,
        "expected_attestation_log_base_head_digest": base["log_head_digest"],
        "attestation_log_closed_snapshot": closed,
        "expected_attestation_log_closed_head_digest": closed[
            "log_head_digest"
        ],
        "evidence_records": [evidence],
        "ballots": [ballot],
        "attestations": [attestation],
    }
    expected_lookup = (
        registry["registry_head_digest"],
        evidence["reviewer_lineage_digest"],
        derive_signing_key_digest(key),
    )
    key_lookup = lambda head, lineage, key_digest: (
        key if (head, lineage, key_digest) == expected_lookup else None
    )
    materials = {key: combined[key] for key in GATE_MATERIAL_KEYS}
    expectations = {key: combined[key] for key in GATE_EXPECTATION_KEYS}
    return materials, expectations, key_lookup


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
    *,
    enabled: bool,
    material_provider=None,
    expectation_provider=None,
    key_lookup=None,
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
        enable_attested_consensus_shadow=enabled,
        attested_consensus_material_provider=material_provider,
        attested_consensus_expectation_provider=expectation_provider,
        attested_consensus_key_lookup=key_lookup,
    )
    route = _RouteResult([capability])
    runtime.solver_router.route = lambda _i, _q, _c: route
    runtime.action_bus.register_executor(
        "detect.fixture", lambda _action: {"success": True, "value": 42}
    )
    return runtime, route


def test_default_off_never_reads_any_admission_dependency() -> None:
    materials, expectations, key_lookup = _gate_inputs()
    calls = {"material": 0, "expectation": 0, "key": 0}

    def material_provider():
        calls["material"] += 1
        return materials

    def expectation_provider():
        calls["expectation"] += 1
        return expectations

    def tracked_key_lookup(*args):
        calls["key"] += 1
        return key_lookup(*args)

    runtime, _ = _runtime(
        enabled=False,
        material_provider=material_provider,
        expectation_provider=expectation_provider,
        key_lookup=tracked_key_lookup,
    )
    result = runtime.handle_query("private production query")
    snapshot = runtime.attested_consensus_shadow_snapshot()
    assert result["executed"] is True
    assert calls == {"material": 0, "expectation": 0, "key": 0}
    assert snapshot["enabled"] is False
    assert snapshot["report"]["receipt_count"] == 0
    assert snapshot["authority_granted"] is False


def test_only_literal_true_enables_the_shadow_lane() -> None:
    materials, expectations, key_lookup = _gate_inputs()
    for truthy_value in (1, "true", [True]):
        calls = 0

        def material_provider():
            nonlocal calls
            calls += 1
            return materials

        runtime, _ = _runtime(
            enabled=truthy_value,
            material_provider=material_provider,
            expectation_provider=lambda: expectations,
            key_lookup=key_lookup,
        )
        result = runtime.handle_query("strict enable flag")
        snapshot = runtime.attested_consensus_shadow_snapshot()
        assert result["executed"] is True
        assert snapshot["enabled"] is False
        assert calls == 0


def test_positive_gate_is_observed_without_activation_or_routing_effect() -> None:
    materials, expectations, key_lookup = _gate_inputs("support")
    expectation_calls = 0

    def expectation_provider():
        nonlocal expectation_calls
        expectation_calls += 1
        return expectations

    runtime, _ = _runtime(
        enabled=True,
        material_provider=lambda: materials,
        expectation_provider=expectation_provider,
        key_lookup=key_lookup,
    )
    result = runtime.handle_query("normal deterministic production query")
    assert runtime.wait_for_attested_consensus_shadow(timeout=2.0) is True
    snapshot = runtime.attested_consensus_shadow_snapshot()
    assert result["executed"] is True
    assert expectation_calls == 2
    assert snapshot["evaluation_success_count"] == 1
    assert snapshot["evaluation_failure_count"] == 0
    assert snapshot["expectation_drift_count"] == 0
    assert snapshot["report"]["advisory_pass_count"] == 1
    assert snapshot["activation_performed"] is False
    assert snapshot["current_response_routing_influence_applied"] is False
    assert snapshot["current_production_decision_unchanged"] is True
    assert snapshot["authority_granted"] is False
    assert snapshot["provider_execution_boundary"] == "trusted_same_process"
    assert snapshot["provider_side_effect_isolation_enforced"] is False
    assert snapshot["future_query_isolation_guaranteed"] is False


def test_trust_seams_are_read_in_fenced_order() -> None:
    materials, expectations, key_lookup = _gate_inputs()
    events = []
    expectation_calls = 0

    def expectation_provider():
        nonlocal expectation_calls
        expectation_calls += 1
        events.append(f"expectation:{expectation_calls}")
        return expectations

    def material_provider():
        events.append("material")
        return materials

    def tracked_key_lookup(*args):
        events.append("key_lookup")
        return key_lookup(*args)

    runtime, _ = _runtime(
        enabled=True,
        material_provider=material_provider,
        expectation_provider=expectation_provider,
        key_lookup=tracked_key_lookup,
    )
    result = runtime.handle_query("ordered trust seams")
    assert runtime.wait_for_attested_consensus_shadow(2.0) is True
    assert result["executed"] is True
    assert events[0:2] == ["expectation:1", "material"]
    assert events[-1] == "expectation:2"
    assert events[2:-1]
    assert set(events[2:-1]) == {"key_lookup"}


def test_stop_advice_never_blocks_the_existing_production_route() -> None:
    materials, expectations, key_lookup = _gate_inputs("stop")
    runtime, _ = _runtime(
        enabled=True,
        material_provider=lambda: materials,
        expectation_provider=lambda: expectations,
        key_lookup=key_lookup,
    )
    result = runtime.handle_query("production still executes")
    assert runtime.wait_for_attested_consensus_shadow(timeout=2.0) is True
    report = runtime.attested_consensus_shadow_snapshot()["report"]
    assert result["executed"] is True
    assert report["advisory_pass_count"] == 0
    assert report["advisory_block_count"] == 1


def test_pre_post_expectation_drift_discards_a_completed_receipt() -> None:
    materials, expectations, key_lookup = _gate_inputs()
    changed = dict(expectations)
    changed["expected_query_digest"] = _digest("advanced-query-head")
    supplied = iter((expectations, changed))
    runtime, _ = _runtime(
        enabled=True,
        material_provider=lambda: materials,
        expectation_provider=lambda: next(supplied),
        key_lookup=key_lookup,
    )
    result = runtime.handle_query("expectation fence")
    assert runtime.wait_for_attested_consensus_shadow(timeout=2.0) is True
    snapshot = runtime.attested_consensus_shadow_snapshot()
    assert result["executed"] is True
    assert snapshot["expectation_drift_count"] == 1
    assert snapshot["evaluation_success_count"] == 0
    assert snapshot["report"]["receipt_count"] == 0


def test_provider_and_evaluation_failures_are_isolated_and_payload_free(
    caplog,
) -> None:
    materials, expectations, key_lookup = _gate_inputs()
    secret = "TOP_SECRET_ATTESTATION_MATERIAL"

    def failing_material_provider():
        raise RuntimeError(secret)

    runtime, _ = _runtime(
        enabled=True,
        material_provider=failing_material_provider,
        expectation_provider=lambda: expectations,
        key_lookup=key_lookup,
    )
    with caplog.at_level(logging.DEBUG, logger="waggledance.autonomy.runtime"):
        result = runtime.handle_query("provider isolation")
        assert runtime.wait_for_attested_consensus_shadow(timeout=2.0) is True
    snapshot = runtime.attested_consensus_shadow_snapshot()
    assert result["executed"] is True
    assert snapshot["material_provider_failure_count"] == 1
    assert snapshot["report"]["receipt_count"] == 0
    assert secret not in caplog.text
    assert secret not in repr(snapshot)

    malformed_runtime, _ = _runtime(
        enabled=True,
        material_provider=lambda: {**materials, "self_advice": True},
        expectation_provider=lambda: expectations,
        key_lookup=key_lookup,
    )
    malformed_result = malformed_runtime.handle_query("malformed isolation")
    assert malformed_runtime.wait_for_attested_consensus_shadow(2.0) is True
    malformed_snapshot = malformed_runtime.attested_consensus_shadow_snapshot()
    assert malformed_result["executed"] is True
    assert malformed_snapshot["evaluation_failure_count"] == 1
    assert malformed_snapshot["report"]["receipt_count"] == 0


def test_expectation_and_key_lookup_failures_are_payload_free(caplog) -> None:
    materials, expectations, key_lookup = _gate_inputs()
    secrets = (
        "SECRET_INITIAL_EXPECTATION",
        "SECRET_FINAL_EXPECTATION",
        "SECRET_KEY_LOOKUP",
    )

    def initial_failure():
        raise RuntimeError(secrets[0])

    expectation_values = iter((expectations, RuntimeError(secrets[1])))

    def final_failure():
        value = next(expectation_values)
        if isinstance(value, BaseException):
            raise value
        return value

    def key_failure(*_args):
        raise RuntimeError(secrets[2])

    cases = (
        (initial_failure, key_lookup, "expectation_provider_failure_count"),
        (final_failure, key_lookup, "expectation_provider_failure_count"),
        (lambda: expectations, key_failure, "evaluation_failure_count"),
    )
    with caplog.at_level(logging.DEBUG, logger="waggledance.autonomy.runtime"):
        for expectation_provider, lookup, counter in cases:
            runtime, _ = _runtime(
                enabled=True,
                material_provider=lambda: materials,
                expectation_provider=expectation_provider,
                key_lookup=lookup,
            )
            result = runtime.handle_query("secret-free failure boundary")
            assert runtime.wait_for_attested_consensus_shadow(2.0) is True
            snapshot = runtime.attested_consensus_shadow_snapshot()
            assert result["executed"] is True
            assert snapshot[counter] == 1
            assert snapshot["report"]["receipt_count"] == 0
            assert all(secret not in repr(snapshot) for secret in secrets)
    assert all(secret not in caplog.text for secret in secrets)


def test_missing_trust_dependencies_fail_observationally_only() -> None:
    materials, expectations, key_lookup = _gate_inputs()
    cases = (
        (None, lambda: expectations, key_lookup, "material_provider_failure_count"),
        (lambda: materials, None, key_lookup, "expectation_provider_failure_count"),
        (
            lambda: materials,
            lambda: expectations,
            None,
            "key_lookup_configuration_failure_count",
        ),
    )
    for material_provider, expectation_provider, lookup, counter in cases:
        runtime, _ = _runtime(
            enabled=True,
            material_provider=material_provider,
            expectation_provider=expectation_provider,
            key_lookup=lookup,
        )
        result = runtime.handle_query("missing dependency")
        assert runtime.wait_for_attested_consensus_shadow(2.0) is True
        snapshot = runtime.attested_consensus_shadow_snapshot()
        assert result["executed"] is True
        assert snapshot[counter] == 1
        assert snapshot["report"]["receipt_count"] == 0


def test_same_process_provider_boundary_is_explicit_after_frozen_response() -> None:
    materials, expectations, key_lookup = _gate_inputs()
    runtime, route = _runtime(
        enabled=True,
        material_provider=None,
        expectation_provider=lambda: expectations,
        key_lookup=key_lookup,
    )

    def mutating_provider():
        route.selection.selected.clear()
        return materials

    runtime._attested_consensus_material_provider = mutating_provider
    result = runtime.handle_query("frozen response")
    assert result["executed"] is True
    assert result["capability"] == "detect.fixture"
    assert runtime.wait_for_attested_consensus_shadow(2.0) is True
    assert route.selection.selected == []
    assert runtime.attested_consensus_shadow_snapshot()["report"][
        "receipt_count"
    ] == 1


def test_shared_nested_executor_result_is_detached_before_provider_runs() -> None:
    materials, expectations, key_lookup = _gate_inputs()
    shared_result = {"success": True, "value": 42, "marker": "original"}
    runtime, _ = _runtime(
        enabled=True,
        material_provider=None,
        expectation_provider=lambda: expectations,
        key_lookup=key_lookup,
    )

    def mutating_provider():
        shared_result["value"] = -999
        shared_result["marker"] = "mutated-by-shadow"
        return materials

    runtime._attested_consensus_material_provider = mutating_provider
    runtime.action_bus.register_executor(
        "detect.fixture", lambda _action: shared_result
    )
    result = runtime.handle_query("nested response detachment")
    assert result["result"] == {
        "success": True,
        "value": 42,
        "marker": "original",
    }
    assert runtime.wait_for_attested_consensus_shadow(2.0) is True
    assert shared_result["value"] == -999
    assert result["result"] == {
        "success": True,
        "value": 42,
        "marker": "original",
    }


def test_resource_admission_terminal_outcomes_are_observed_off_path() -> None:
    materials, expectations, key_lookup = _gate_inputs()

    class _Admission:
        def __init__(self, result):
            self._result = result

        def check(self, *, work_type):
            assert work_type == "query"
            return self._result

    cases = (
        (
            AdmissionResult(
                AdmissionDecision.REJECT,
                reason="fixture reject",
            ),
            False,
        ),
        (
            AdmissionResult(
                AdmissionDecision.DEFER,
                reason="fixture defer",
                wait_ms=25,
            ),
            True,
        ),
    )
    for admission, expected_deferred in cases:
        runtime, _ = _runtime(
            enabled=True,
            material_provider=lambda: materials,
            expectation_provider=lambda: expectations,
            key_lookup=key_lookup,
        )
        runtime.admission_control = _Admission(admission)
        result = runtime.handle_query("resource terminal response")
        assert runtime.wait_for_attested_consensus_shadow(2.0) is True
        snapshot = runtime.attested_consensus_shadow_snapshot()
        assert result["deferred"] is expected_deferred
        assert "executed" not in result
        assert snapshot["evaluation_success_count"] == 1
        assert snapshot["report"]["receipt_count"] == 1


def test_blocking_material_provider_never_blocks_query_latency() -> None:
    materials, expectations, key_lookup = _gate_inputs()
    started = threading.Event()
    release = threading.Event()

    def blocking_provider():
        started.set()
        release.wait(2.0)
        return materials

    runtime, _ = _runtime(
        enabled=True,
        material_provider=blocking_provider,
        expectation_provider=lambda: expectations,
        key_lookup=key_lookup,
    )
    before = time.perf_counter()
    result = runtime.handle_query("latency isolation")
    elapsed = time.perf_counter() - before
    assert result["executed"] is True
    assert elapsed < 1.0
    assert started.wait(1.0) is True
    assert runtime.wait_for_attested_consensus_shadow(0.01) is False
    release.set()
    assert runtime.wait_for_attested_consensus_shadow(2.0) is True
    assert runtime.attested_consensus_shadow_snapshot()["report"][
        "receipt_count"
    ] == 1


def test_shared_background_queue_drop_cannot_replace_response(monkeypatch) -> None:
    import waggledance.core.autonomy.runtime as runtime_module

    materials, expectations, key_lookup = _gate_inputs()

    class _FullDispatcher:
        @staticmethod
        def submit(_callback):
            return None

    monkeypatch.setattr(
        runtime_module,
        "_activation_mirror_dispatcher",
        lambda: _FullDispatcher(),
    )
    runtime, _ = _runtime(
        enabled=True,
        material_provider=lambda: materials,
        expectation_provider=lambda: expectations,
        key_lookup=key_lookup,
    )
    result = runtime.handle_query("queue saturation")
    snapshot = runtime.attested_consensus_shadow_snapshot()
    assert result["executed"] is True
    assert runtime.wait_for_attested_consensus_shadow(0.01) is False
    assert snapshot["queue_drop_count"] == 1
    assert snapshot["report"]["receipt_count"] == 0


def test_dispatcher_exception_cannot_replace_or_leak_into_response(
    monkeypatch,
) -> None:
    import waggledance.core.autonomy.runtime as runtime_module

    materials, expectations, key_lookup = _gate_inputs()
    secret = "SECRET_DISPATCHER_INTERNAL"

    class _BrokenDispatcher:
        @staticmethod
        def submit(_callback):
            raise RuntimeError(secret)

    monkeypatch.setattr(
        runtime_module,
        "_activation_mirror_dispatcher",
        lambda: _BrokenDispatcher(),
    )
    runtime, _ = _runtime(
        enabled=True,
        material_provider=lambda: materials,
        expectation_provider=lambda: expectations,
        key_lookup=key_lookup,
    )
    result = runtime.handle_query("dispatch failure isolation")
    snapshot = runtime.attested_consensus_shadow_snapshot()
    assert result["executed"] is True
    assert runtime.wait_for_attested_consensus_shadow(0.01) is False
    assert snapshot["dispatch_failure_count"] == 1
    assert snapshot["queue_drop_count"] == 0
    assert snapshot["report"]["receipt_count"] == 0
    assert secret not in repr(result)
    assert secret not in repr(snapshot)


def test_unsupported_response_value_skips_observer_without_running_hooks() -> None:
    materials, expectations, key_lookup = _gate_inputs()
    provider_calls = 0
    deepcopy_calls = 0

    class _HostileCopyHook:
        def __deepcopy__(self, _memo):
            nonlocal deepcopy_calls
            deepcopy_calls += 1
            raise SystemExit("MUST_NOT_RUN_OR_LEAK")

    result_payload = {
        "success": True,
        "value": _HostileCopyHook(),
    }

    def material_provider():
        nonlocal provider_calls
        provider_calls += 1
        return materials

    runtime, _ = _runtime(
        enabled=True,
        material_provider=material_provider,
        expectation_provider=lambda: expectations,
        key_lookup=key_lookup,
    )
    runtime.action_bus.register_executor(
        "detect.fixture", lambda _action: result_payload
    )
    result = runtime.handle_query("unsupported response value")
    snapshot = runtime.attested_consensus_shadow_snapshot()
    assert result["executed"] is True
    assert result["result"] is result_payload
    assert deepcopy_calls == 0
    assert provider_calls == 0
    assert runtime.wait_for_attested_consensus_shadow(0.01) is False
    assert snapshot["response_detachment_failure_count"] == 1
    assert snapshot["report"]["receipt_count"] == 0


def test_unexpected_detacher_failure_is_counted_and_cannot_replace_response(
    monkeypatch,
) -> None:
    import waggledance.core.autonomy.runtime as runtime_module

    materials, expectations, key_lookup = _gate_inputs()
    provider_calls = 0

    def material_provider():
        nonlocal provider_calls
        provider_calls += 1
        return materials

    def failing_detacher(_response):
        raise IndexError("SECRET_CONCURRENT_MUTATION_DETAIL")

    monkeypatch.setattr(
        runtime_module,
        "_detach_observer_response",
        failing_detacher,
    )
    runtime, _ = _runtime(
        enabled=True,
        material_provider=material_provider,
        expectation_provider=lambda: expectations,
        key_lookup=key_lookup,
    )
    result = runtime.handle_query("detacher boundary")
    snapshot = runtime.attested_consensus_shadow_snapshot()
    assert result["executed"] is True
    assert provider_calls == 0
    assert snapshot["response_detachment_failure_count"] == 1
    assert "SECRET_CONCURRENT_MUTATION_DETAIL" not in repr(result)
    assert "SECRET_CONCURRENT_MUTATION_DETAIL" not in repr(snapshot)


def test_concurrent_submissions_track_actual_latest_enqueued_event(
    monkeypatch,
) -> None:
    import waggledance.core.autonomy.runtime as runtime_module

    materials, expectations, key_lookup = _gate_inputs()
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
    runtime, _ = _runtime(
        enabled=True,
        material_provider=lambda: materials,
        expectation_provider=lambda: expectations,
        key_lookup=key_lookup,
    )
    first = threading.Thread(target=runtime._schedule_attested_consensus_shadow)
    second = threading.Thread(target=runtime._schedule_attested_consensus_shadow)
    first.start()
    assert first_entered.wait(1.0) is True
    second.start()
    wait_results = []
    waiter_done = threading.Event()

    def wait_while_submit_is_pending():
        wait_results.append(runtime.wait_for_attested_consensus_shadow(0.01))
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
    assert runtime._attested_consensus_shadow_last_completion is enqueue_order[-1]
    assert runtime.wait_for_attested_consensus_shadow(0.01) is False
    enqueue_order[-1].set()
    assert runtime.wait_for_attested_consensus_shadow(0.1) is True


def test_receipt_buffer_is_bounded_but_success_counter_is_cumulative() -> None:
    materials, expectations, key_lookup = _gate_inputs()
    runtime, _ = _runtime(
        enabled=True,
        material_provider=lambda: materials,
        expectation_provider=lambda: expectations,
        key_lookup=key_lookup,
    )
    for _ in range(260):
        runtime._attested_consensus_shadow_safe()
    snapshot = runtime.attested_consensus_shadow_snapshot()
    assert snapshot["evaluation_success_count"] == 260
    assert snapshot["report"]["receipt_count"] == 256
    assert snapshot["report"]["advisory_pass_count"] == 256

# SPDX-License-Identifier: Apache-2.0
"""Tests for meta-optimizer — hyperparameter learning from canary results (v3.2)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from waggledance.core.specialist_models.meta_optimizer import (
    check_activation_gate, propose_hyperparameters, record_canary_result,
    get_default_params, CanaryRecord, MetaOptimizerState,
)


def _record(model_id="route_classifier", cycle=1, lr=0.01, features=50,
            accuracy=0.85, delta=0.0, proposed=False):
    return CanaryRecord(
        model_id=model_id, cycle=cycle, learning_rate=lr,
        feature_count=features, accuracy=accuracy,
        accuracy_delta=delta, proposed_by_meta=proposed,
    )


class TestActivationGate:
    def test_not_active_with_fewer_cycles(self):
        history = [_record(cycle=1), _record(cycle=2)]
        assert check_activation_gate(history, min_cycles=3) is False

    def test_active_after_min_cycles(self):
        history = [_record(cycle=i) for i in range(3)]
        assert check_activation_gate(history, min_cycles=3) is True

    def test_failed_cycles_dont_count(self):
        history = [_record(cycle=1, accuracy=0), _record(cycle=2), _record(cycle=3)]
        assert check_activation_gate(history, min_cycles=3) is False

    def test_zero_cycles(self):
        assert check_activation_gate([], min_cycles=3) is False


class TestProposeHyperparameters:
    def test_returns_none_before_gate(self):
        state = MetaOptimizerState(model_id="m1", canary_history=[_record()])
        proposal = propose_hyperparameters(state, min_cycles=3)
        assert proposal is None

    def test_returns_proposal_after_gate(self):
        history = [_record(cycle=i) for i in range(4)]
        state = MetaOptimizerState(model_id="m1", canary_history=history)
        proposal = propose_hyperparameters(state, min_cycles=3)
        assert proposal is not None
        assert proposal.model_id == "m1"
        assert proposal.learning_rate > 0

    def test_activates_state(self):
        history = [_record(cycle=i) for i in range(3)]
        state = MetaOptimizerState(model_id="m1", canary_history=history)
        assert state.active is False
        propose_hyperparameters(state, min_cycles=3)
        assert state.active is True

    def test_improving_accuracy_increases_lr(self):
        history = [
            _record(cycle=1, lr=0.01, accuracy=0.80, delta=0.0),
            _record(cycle=2, lr=0.01, accuracy=0.83, delta=0.03),
            _record(cycle=3, lr=0.01, accuracy=0.86, delta=0.03),
        ]
        state = MetaOptimizerState(model_id="m1", canary_history=history)
        proposal = propose_hyperparameters(state, min_cycles=3)
        assert proposal.learning_rate > 0.01

    def test_declining_accuracy_decreases_lr(self):
        history = [
            _record(cycle=1, lr=0.05, accuracy=0.85, delta=0.0),
            _record(cycle=2, lr=0.05, accuracy=0.83, delta=-0.02),
            _record(cycle=3, lr=0.05, accuracy=0.80, delta=-0.03),
        ]
        state = MetaOptimizerState(model_id="m1", canary_history=history)
        proposal = propose_hyperparameters(state, min_cycles=3)
        assert proposal.learning_rate < 0.05


class TestRecordCanaryResult:
    def test_records_history(self):
        state = MetaOptimizerState(model_id="m1")
        record_canary_result(state, _record())
        assert len(state.canary_history) == 1

    def test_rollback_after_consecutive_failures(self):
        state = MetaOptimizerState(model_id="m1")
        record_canary_result(state, _record(delta=-0.05, proposed=True))
        rollback = record_canary_result(state, _record(delta=-0.03, proposed=True))
        assert rollback is True

    def test_no_rollback_when_improving(self):
        state = MetaOptimizerState(model_id="m1")
        rollback = record_canary_result(state, _record(delta=0.05, proposed=True))
        assert rollback is False

    def test_resets_counter_on_success(self):
        state = MetaOptimizerState(model_id="m1")
        record_canary_result(state, _record(delta=-0.05, proposed=True))
        assert state.consecutive_failures == 1
        record_canary_result(state, _record(delta=0.01, proposed=True))
        assert state.consecutive_failures == 0

    def test_default_params(self):
        state = MetaOptimizerState(model_id="m1", default_lr=0.01, default_feature_count=50)
        defaults = get_default_params(state)
        assert defaults.learning_rate == 0.01
        assert defaults.feature_set_size == 50
        assert defaults.reason == "rollback to defaults"

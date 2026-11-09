"""Synthetic scoped C2 binding checks; no live bridge, model or installation."""
import json
from pathlib import Path

import pytest

from tests.tools.test_wd_q4_staged_lane_query import staged_producer  # noqa: F401
from tools import wd_q4_lane_binding_producer as producer
from tools import wd_q4_public_card_prefix_verifier as verifier


def test_one_binding_scans_once_and_preserves_two_resolutions(staged_producer, monkeypatch):
    scans = []
    resolve_args = []
    original_read = verifier.bridge.read_bridge_log
    original_resolve = producer.staged_resolver.resolve_card_snapshot_pointer

    def measured_read(*args, **kwargs):
        scans.append((args[0], kwargs['cursor']))
        return original_read(*args, **kwargs)

    def measured_resolve(**kwargs):
        resolve_args.append((kwargs['source_path'], kwargs['generation_path'], kwargs['end_cursor'],
                             json.loads(kwargs['card_decision_bytes'])['source_prefix']['sha256']))
        return original_resolve(**kwargs)

    monkeypatch.setattr(verifier.bridge, 'read_bridge_log', measured_read)
    monkeypatch.setattr(producer.staged_resolver, 'resolve_card_snapshot_pointer', measured_resolve)
    assert verifier._SCAN_MEMO.get() is None
    result = producer.build_staged_lane_binding(**staged_producer)
    assert verifier._SCAN_MEMO.get() is None
    assert len(resolve_args) == 2 and resolve_args[0] == resolve_args[1]
    assert len(scans) == 1
    assert len(json.loads(result['binding_bytes'])['binding']) == 10


def test_binding_bytes_equal_with_and_without_scope(staged_producer, monkeypatch):
    from contextlib import nullcontext
    with monkeypatch.context() as patch:
        patch.setattr(verifier, '_snapshot_scan_scope', nullcontext)
        without_memo = producer.build_staged_lane_binding(**staged_producer)
    assert producer.build_staged_lane_binding(**staged_producer) == without_memo


def test_binding_keeps_enclosing_scope_unmodified(staged_producer):
    with verifier._snapshot_scan_scope():
        outer = verifier._SCAN_MEMO.get()
        before = outer.state
        assert producer.build_staged_lane_binding(**staged_producer)['binding_sha256']
        assert verifier._SCAN_MEMO.get() is outer
        assert outer.state is before
    assert verifier._SCAN_MEMO.get() is None


def test_interruption_restores_enclosing_scope(staged_producer, monkeypatch):
    original = producer.staged_resolver.resolve_card_snapshot_pointer
    calls = []
    def interrupt_second(**kwargs):
        calls.append(1)
        if len(calls) == 2:
            raise KeyboardInterrupt('synthetic interruption')
        return original(**kwargs)
    monkeypatch.setattr(producer.staged_resolver, 'resolve_card_snapshot_pointer', interrupt_second)
    with verifier._snapshot_scan_scope():
        outer = verifier._SCAN_MEMO.get()
        before = outer.state
        with pytest.raises(KeyboardInterrupt):
            producer.build_staged_lane_binding(**staged_producer)
        assert verifier._SCAN_MEMO.get() is outer and outer.state is before
    assert verifier._SCAN_MEMO.get() is None


@pytest.mark.parametrize('mutation', ['rewrite', 'replace', 'generation', 'projection', 'pointer', 'append'])
def test_pinned_source_rechecked_between_resolutions(staged_producer, monkeypatch, mutation):
    plan = json.loads(staged_producer['public_plan_bytes'])
    source = Path(plan['source_path'])
    generation = Path(plan['generation_path'])
    original = producer.staged_resolver.resolve_card_snapshot_pointer
    calls = []
    attempts = []

    def mutate_after_first(**kwargs):
        attempts.append(1)
        result = original(**kwargs)
        calls.append(1)
        if len(calls) == 1:
            raw = source.read_bytes()
            if mutation == 'rewrite':
                source.write_bytes(b'!' + raw[1:])
            elif mutation == 'replace':
                source.rename(source.with_name('previous-synthetic-source.jsonl'))
                source.write_bytes(raw)
            elif mutation == 'generation':
                generation.write_bytes(b'{"generation":"other"}')
            elif mutation == 'projection':
                path = Path(plan['config']['descriptor']).parent / 'projection.json'
                path.write_bytes(path.read_bytes() + b' ')
            elif mutation == 'pointer':
                path = Path(plan['pointer_path'])
                path.write_bytes(path.read_bytes() + b' ')
            else:
                with source.open('ab') as out:
                    out.write(b'{"synthetic":"later"}\n')
        return result

    monkeypatch.setattr(producer.staged_resolver, 'resolve_card_snapshot_pointer', mutate_after_first)
    if mutation == 'append':
        assert producer.build_staged_lane_binding(**staged_producer)['binding_sha256']
    else:
        with pytest.raises(producer.BindingProducerRefused):
            producer.build_staged_lane_binding(**staged_producer)
    assert len(attempts) == 2
    assert len(calls) in (1, 2)
    assert verifier._SCAN_MEMO.get() is None

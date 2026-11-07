"""Synthetic opt-in admission: P1 must be bound before publication."""
import json

import pytest

from tests.tools.test_wd_q4_continuation_adopt import actual_continuation, synthetic
from tests.tools.test_wd_q4_continuation_adopt_recovery import paths


def test_p1_marker_is_explicit_and_forwarded_before_publication(actual_continuation, monkeypatch):
    from tools import wd_q4_continuation_adopt as adopt
    # Simulate a legacy pending record; this is not a live P1 card route.
    monkeypatch.setattr(adopt, '_require_supported_p1', lambda: None)
    args = actual_continuation
    private, _ = paths(args)
    marker = private / 'q4-continuation-adopt.pending.json'
    seen = []

    def interrupted(*_args, **kwargs):
        seen.append((kwargs.get('p1_enabled'), json.loads(marker.read_bytes())))
        raise KeyboardInterrupt()

    monkeypatch.setattr(adopt.update, '_publish', interrupted)
    with pytest.raises(adopt.AdoptInterrupted):
        adopt.run_once(**args, p1_enabled=True)
    assert seen[0][0] is True
    pending = seen[0][1]
    assert pending['schema'] == 'wd.q4-continuation-adopt-pending.v2'
    assert pending['p1_enabled'] is True
    assert pending['receipt_sha256'] == args['receipt_sha256']
    assert marker.exists()


@pytest.mark.parametrize('bad', [1, 'true', None])
def test_non_boolean_p1_is_refused_before_marker(actual_continuation, bad):
    from tools import wd_q4_continuation_adopt as adopt
    args = actual_continuation
    private, _ = paths(args)
    with pytest.raises(adopt.AdoptRefused):
        adopt.run_once(**args, p1_enabled=bad)
    assert not (private / 'q4-continuation-adopt.pending.json').exists()


def test_p1_success_forwards_opt_in_and_labels_result(actual_continuation, monkeypatch):
    # The status-v1 producer/selector are only substitutes for the future P1
    # components; this checks forwarding and the resulting API binding.
    from tools import wd_q4_continuation_adopt as adopt
    # Simulate a legacy pending record; this is not a live P1 card route.
    monkeypatch.setattr(adopt, '_require_supported_p1', lambda: None)
    original_publish, original_select = adopt.update._publish, adopt.live.select
    observed = []

    def publish(*args, **kwargs):
        observed.append(('publish', kwargs.pop('p1_enabled', None)))
        return original_publish(*args, **kwargs)

    def select(*args, **kwargs):
        observed.append(('select', kwargs.pop('p1_enabled', None)))
        return original_select(*args, **kwargs)

    monkeypatch.setattr(adopt.update, '_publish', publish)
    monkeypatch.setattr(adopt.live, 'select', select)
    result = adopt.run_once(**actual_continuation, p1_enabled=True)
    assert ('publish', True) in observed and ('select', True) in observed
    assert result['status'] == 'PUBLISHED'
    assert result['schema'] == 'wd.q4-continuation-adopt-result.v2'
    assert result['p1_enabled'] is True
    assert result['receipt_sha256'] == actual_continuation['receipt_sha256']


def test_p1_result_construction_failure_preserves_marker(actual_continuation, monkeypatch):
    from tools import wd_q4_continuation_adopt as adopt
    # Simulate a legacy pending record; this is not a live P1 card route.
    monkeypatch.setattr(adopt, '_require_supported_p1', lambda: None)
    args = actual_continuation
    private, _ = paths(args)
    original_publish, original_select = adopt.update._publish, adopt.live.select

    class FailingResult(dict):
        def update(self, *_args, **_kwargs):
            raise OSError('synthetic result error')

    def publish(*pos, **kw):
        assert kw.pop('p1_enabled') is True
        return FailingResult(original_publish(*pos, **kw))

    def select(*pos, **kw):
        assert kw.pop('p1_enabled') is True
        return original_select(*pos, **kw)

    monkeypatch.setattr(adopt.update, '_publish', publish)
    monkeypatch.setattr(adopt.live, 'select', select)
    with pytest.raises(adopt.AdoptOutcomeUnknown):
        adopt.run_once(**args, p1_enabled=True)
    assert (private / 'q4-continuation-adopt.pending.json').is_file()


def test_prior_selector_forwards_p1_to_pair_check(actual_continuation, monkeypatch):
    # Status-v1 selection substitutes for the not-yet-integrated P1 selector.
    from tools import wd_q4_continuation_adopt as adopt
    from tools import wd_q4_lane_caller_cli as caller
    args = actual_continuation
    published = adopt.run_once(**args)
    baseline = caller._configuration(args['baseline_path'], args['baseline_sha256'])
    original = adopt.live._checked_pair
    observed = []

    def pair(*pos, **kw):
        observed.append(kw.pop('p1_enabled', None))
        return original(*pos, **kw)

    monkeypatch.setattr(adopt.live, '_checked_pair', pair)
    pointer, raw = adopt._prior_pointer(args['baseline_path'], args['baseline_sha256'], baseline,
                                        args['manifest_sha256'], p1_enabled=True)
    assert observed == [True]
    assert pointer == published['pointer_sha256'] and raw is not None


def test_real_legacy_adoption_p1_refuses_before_any_pending_or_publication(actual_continuation, monkeypatch):
    """P1 card refresh uses the closed tuple coordinator, never status adoption."""
    from pathlib import Path
    from tools import wd_q4_continuation_adopt as adopt
    private, _ = paths(actual_continuation)
    selector = Path(actual_continuation['baseline_path']).parent.parent / adopt.live._POINTER
    before = {str(p.relative_to(private)): p.read_bytes() for p in private.rglob('*') if p.is_file()}
    previous = selector.read_bytes() if selector.exists() else None
    def unexpected(*_args, **_kwargs):
        raise AssertionError('legacy P1 path attempted publication')
    monkeypatch.setattr(adopt.live, 'publish', unexpected)
    with pytest.raises(adopt.AdoptRefused, match='card_refresh_tuple_required'):
        adopt.run_once(**actual_continuation, p1_enabled=True)
    assert before == {str(p.relative_to(private)): p.read_bytes() for p in private.rglob('*') if p.is_file()}
    assert (selector.read_bytes() if selector.exists() else None) == previous
    assert not (private / 'q4-continuation-adopt.pending.json').exists()


def test_legacy_adoption_remains_off_by_default_with_p1_flag():
    from tools import wd_q4_continuation_adopt as adopt
    assert adopt.run_once(p1_enabled=True) == {'status': 'OFF'}

"""Synthetic state-machine specification; no live files or processes."""
import hashlib
import json
import pytest

from tests.tools import q4_cancel_journal_prototype as model


def binding(**changes):
    values = dict(attempt='a' * 64, reservation_sha256='b' * 64, route_sha256='c' * 64,
                  pointer_sha256='d' * 64, marker_sha256='e' * 64, marker_identity_sha256='1' * 64,
                  previous_record_sha256='2' * 64, job_sha256='f' * 64)
    return model.Binding(**{**values, **changes})


def ready_session():
    session = model.SyntheticSession(enabled=True, binding=binding())
    return session, session.runner_no_start(), session.job_cleanup(
        outcome='completed', cleanup='confirmed', active_processes=0, exit_code=2)


def test_default_off_never_reads_binding():
    class NoTouch:
        def __getattribute__(self, name):
            pytest.fail('default-off read an input')
    assert model.SyntheticSession(enabled=False, binding=NoTouch()).status() == 'OFF'


def test_matching_same_attempt_and_confirmed_job_produce_only_simulated_evidence():
    session, runner, cleanup = ready_session()
    assert session.record(runner, cleanup) == 'SYNTHETIC_EVIDENCE_ONLY'
    assert session.status() == 'SYNTHETIC_EVIDENCE_ONLY'
    assert session.automatic_retry_allowed() is False
    assert session.export_journal()


@pytest.mark.parametrize('field', ['attempt', 'reservation_sha256', 'route_sha256',
                                   'pointer_sha256', 'marker_sha256', 'marker_identity_sha256',
                                   'previous_record_sha256', 'job_sha256'])
def test_other_attempt_or_wrong_pins_are_unknown(field):
    target = model.SyntheticSession(enabled=True, binding=binding())
    foreign = model.SyntheticSession(enabled=True, binding=binding(**{field: '0' * 64}))
    proof = foreign.runner_no_start()
    cleanup = target.job_cleanup(outcome='completed', cleanup='confirmed', active_processes=0, exit_code=2)
    assert target.record(proof, cleanup) == 'UNKNOWN'
    assert target.automatic_retry_allowed() is False


def test_even_identical_pins_from_another_session_are_not_accepted():
    first, _, cleanup = ready_session()
    other, proof, _ = ready_session()
    assert first.record(proof, cleanup) == 'UNKNOWN'
    assert other.status() == 'UNKNOWN'


@pytest.mark.parametrize('outcome,cleanup,count,code', [
    ('timeout', 'confirmed', 0, 2), ('completed', 'unknown', 0, 2),
    ('completed', 'confirmed', 1, 2), ('completed', 'confirmed', 0, 0),
    ('completed', 'confirmed', False, 2),
])
def test_job_must_be_completed_and_empty_with_confirmed_cleanup(outcome, cleanup, count, code):
    session = model.SyntheticSession(enabled=True, binding=binding())
    proof = session.runner_no_start()
    job = session.job_cleanup(outcome=outcome, cleanup=cleanup, active_processes=count, exit_code=code)
    assert session.record(proof, job) == 'UNKNOWN'
    assert not session.automatic_retry_allowed()


@pytest.mark.parametrize('stage', ['before_write', 'after_first', 'after_second', 'child'])
def test_any_marker_creation_attempt_precludes_no_start(stage):
    session = model.SyntheticSession(enabled=True, binding=binding())
    session.runner_create_attempted(stage)
    proof = session.runner_no_start()
    job = session.job_cleanup(outcome='completed', cleanup='confirmed', active_processes=0, exit_code=2)
    assert session.record(proof, job) == 'UNKNOWN'
    assert not session.automatic_retry_allowed()


@pytest.mark.parametrize('stage', ['before_refusal', 'after_refusal', 'after_cleanup', 'after_record'])
def test_crash_at_any_phase_stays_unknown_and_never_retries(stage):
    session = model.SyntheticSession(enabled=True, binding=binding())
    proof = cleanup = None
    if stage != 'before_refusal':
        proof = session.runner_no_start()
    if stage in ('after_cleanup', 'after_record'):
        cleanup = session.job_cleanup(outcome='completed', cleanup='confirmed', active_processes=0, exit_code=2)
    if stage == 'after_record':
        assert session.record(proof, cleanup) == 'SYNTHETIC_EVIDENCE_ONLY'
    session.interrupt()
    assert session.status() == 'UNKNOWN'
    assert not session.automatic_retry_allowed()
    assert model.recover(session.export_journal(), enabled=True) == 'UNKNOWN'


@pytest.mark.parametrize('foreign', ['runner_private', 'runner_shared', 'adoption', 'fence', 'private_pending'])
def test_foreign_marker_taints_outcome_without_touching_marker(foreign):
    session, proof, cleanup = ready_session()
    session.observe_foreign(foreign)
    assert session.record(proof, cleanup) == 'UNKNOWN'
    assert session.observed_foreign() == (foreign,)
    assert not session.automatic_retry_allowed()


def test_replayed_witnesses_and_malformed_recovery_are_unknown():
    session, proof, cleanup = ready_session()
    assert session.record(proof, cleanup) == 'SYNTHETIC_EVIDENCE_ONLY'
    assert session.record(proof, cleanup) == 'UNKNOWN'
    assert model.recover(b'not a journal', enabled=True) == 'UNKNOWN'
    assert model.recover(b'anything', enabled=False) == 'OFF'
    assert not session.automatic_retry_allowed()


def test_exact_reservation_token_hash_is_bound_without_exporting_token():
    reservation = {'attempt': '1' * 64, 'token': '2' * 64}
    args = dict(route_sha256='3' * 64, pointer_sha256='4' * 64, marker_sha256='5' * 64,
                marker_identity_sha256='8' * 64, previous_record_sha256=None, job_sha256='6' * 64)
    pinned = model.Binding.from_reservation(reservation, **args)
    encoded = json.dumps({'schema': 'wd.q4-runner-reservation.v1', **reservation},
                         sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('ascii')
    assert pinned.attempt == reservation['attempt']
    assert pinned.reservation_sha256 == hashlib.sha256(encoded).hexdigest()
    changed = model.Binding.from_reservation({**reservation, 'token': '7' * 64}, **args)
    assert changed.reservation_sha256 != pinned.reservation_sha256
    session = model.SyntheticSession(enabled=True, binding=pinned)
    proof = session.runner_no_start()
    cleanup = session.job_cleanup(outcome='completed', cleanup='confirmed', active_processes=0, exit_code=2)
    assert session.record(proof, cleanup) == 'SYNTHETIC_EVIDENCE_ONLY'
    assert reservation['token'].encode('ascii') not in session.export_journal()


@pytest.mark.parametrize('reservation', [None, {}, {'attempt': 'a' * 64},
    {'attempt': 'a' * 64, 'token': 'b' * 64, 'extra': 1},
    {'attempt': 'a' * 64, 'token': 'B' * 64}])
def test_malformed_reservation_cannot_create_binding(reservation):
    with pytest.raises(ValueError):
        model.Binding.from_reservation(reservation, route_sha256='c' * 64, pointer_sha256='d' * 64,
                                       marker_sha256='e' * 64, marker_identity_sha256='1' * 64,
                                       previous_record_sha256=None, job_sha256='f' * 64)


@pytest.mark.parametrize('kind', ['runner_copy', 'job_copy', 'foreign_job', 'mutated_runner', 'mutated_job'])
def test_forged_or_mutated_witnesses_cannot_be_reused(kind):
    session, proof, cleanup = ready_session()
    if kind == 'runner_copy':
        proof = model._RunnerWitness(proof.owner, proof.binding)
    elif kind == 'job_copy':
        cleanup = model._JobWitness(cleanup.owner, cleanup.binding)
    elif kind == 'foreign_job':
        _, _, cleanup = ready_session()
    elif kind == 'mutated_runner':
        proof.binding = binding(route_sha256='0' * 64)
    else:
        cleanup.owner = object()
    assert session.record(proof, cleanup) == 'UNKNOWN'
    assert not session.automatic_retry_allowed()


def test_early_or_duplicate_witness_taints_attempt():
    first = model.SyntheticSession(enabled=True, binding=binding())
    assert first.job_cleanup(outcome='completed', cleanup='confirmed', active_processes=0, exit_code=2) is None
    assert first.runner_no_start() is None and first.status() == 'UNKNOWN'
    second = model.SyntheticSession(enabled=True, binding=binding())
    assert second.runner_no_start() is not None
    assert second.runner_no_start() is None and second.status() == 'UNKNOWN'


def test_synthetic_journal_hash_chain_and_foreign_after_record():
    session, proof, cleanup = ready_session()
    assert session.record(proof, cleanup) == 'SYNTHETIC_EVIDENCE_ONLY'
    chain = json.loads(session.export_journal())
    previous = None
    for entry in chain:
        digest = entry.pop('sha256')
        assert entry['previous_sha256'] == previous
        raw = json.dumps(entry, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('ascii')
        assert digest == hashlib.sha256(raw).hexdigest()
        previous = digest
    session.observe_foreign('adoption')
    assert session.status() == 'UNKNOWN'
    assert model.recover(session.export_journal(), enabled=True) == 'UNKNOWN'
    assert not session.automatic_retry_allowed()

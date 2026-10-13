"""Positive controls and regression twins for the offline scoring experiment."""
from copy import deepcopy
import pytest
from tools.wd_bridge_mind_tournament_tally import tally_round

CAPTURE = 'a' * 64
CONTEXT = 'b' * 64
BUILDER = 'c' * 64
LANES = ('codex-lead-1', 'codex-tools-1', 'fable-5', 'claude-rco-1', 'claude-rco-2')


def result(lane, variant='V0', size=100):
    return {'schema': 'wd.bridge-mind-tournament-result.v1', 'lane': lane, 'variant': variant,
            'round_capture_sha256': CAPTURE, 'round_context_sha256': CONTEXT,
            'round_builder_sha256': BUILDER,
            'answers': [{'id': f'Q{i}', 'bytes_read': size, 'canonical_bytes': 200, 'seconds': 1.0,
                         'correct': True, 'wrong_facts': 0, 'misses': [],
                         'evidence_refs': ['d' * 64]} for i in range(1, 5)]}


def score(records):
    return tally_round(records, capture_sha256=CAPTURE, context_sha256=CONTEXT,
                       builder_sha256=BUILDER, lanes=LANES)


def complete(variant='V0', size=100):
    return [result(lane, variant, size) for lane in LANES]


def test_positive_all_five_lanes_four_questions_choose_bytes_then_time():
    scored = score(complete() + complete('V1', 50))
    assert scored['winner'] == 'V1'
    assert scored['authority'] == 'none'


@pytest.mark.parametrize('field', ['bytes_read', 'canonical_bytes', 'seconds'])
def test_mutant_null_metric_becomes_zero_cannot_win(field):
    records = complete('V1', 0)
    records[0]['answers'][0][field] = None
    scored = score(complete() + records)
    assert scored['winner'] == 'V0'
    row = next(r for r in scored['table'] if r['variant'] == 'V1')
    assert row['status'] == 'UNKNOWN'
    assert row['mean_bytes'] is None or row['mean_seconds'] is None


@pytest.mark.parametrize('field', ['round_capture_sha256', 'round_context_sha256', 'round_builder_sha256'])
def test_mutant_mixed_rounds_cannot_pool(field):
    records = complete()
    records[-1][field] = 'e' * 64
    assert score(records)['winner'] is None


def test_mutant_duplicate_lane_cannot_replace_missing_lane():
    records = complete()[:-1]
    records.append(deepcopy(records[0]))
    assert score(records)['winner'] is None


def test_mutant_duplicate_question_cannot_replace_missing_question():
    records = complete()
    records[0]['answers'][-1] = deepcopy(records[0]['answers'][0])
    assert score(records)['winner'] is None


@pytest.mark.parametrize('value', [-1, True, float('nan'), float('inf'), '0'])
def test_mutant_invalid_numeric_metric_cannot_win(value):
    records = complete('V1', 0)
    records[0]['answers'][0]['bytes_read'] = value
    assert score(complete() + records)['winner'] == 'V0'


@pytest.mark.parametrize('field,value', [('correct', False), ('correct', 'true'), ('wrong_facts', 1),
                                      ('evidence_refs', []), ('misses', [{'critical': True}]),
                                      ('misses', ['not classified'])])
def test_mutant_optimistic_correctness_or_missing_evidence_cannot_win(field, value):
    records = complete('V1', 0)
    records[0]['answers'][0][field] = value
    assert score(complete() + records)['winner'] == 'V0'


def test_metrics_count_canonical_cost_separately_and_do_not_change_inputs():
    records = complete()
    before = deepcopy(records)
    scored = score(records)
    assert records == before
    assert scored['table'][0]['mean_canonical_bytes'] == 200
    assert scored['table'][0]['lanes'] == 5
    assert scored['table'][0]['answers'] == 20


def test_no_answers_means_unknown_not_zero_cost_winner():
    assert score([])['winner'] is None


def test_published_v2_template_is_accepted_without_reinterpreting_nulls():
    records = complete()
    for record in records:
        record['schema'] = 'wd.bridge-mind-tournament-result.v2'
    assert score(records)['winner'] == 'V0'
    records[0]['answers'][0]['seconds'] = None
    assert score(records)['winner'] is None


def test_unrecognized_template_version_cannot_silently_qualify():
    records = complete()
    records[0]['schema'] = 'wd.bridge-mind-tournament-result.v999'
    assert score(records)['winner'] is None


def test_tie_is_deterministic_and_still_only_advisory():
    records = complete('V1') + complete('V0')
    assert score(records) == score(list(reversed(records)))
    assert score(records)['winner'] == 'V0'


@pytest.mark.parametrize('variant', ['V7', 'V8', 'V9'])
def test_registered_hybrids_can_qualify_without_displacing_null_guard(variant):
    records = complete(variant, 50)
    assert score(complete() + records)['winner'] == variant
    records[0]['answers'][0]['seconds'] = None
    assert score(complete() + records)['winner'] == 'V0'


def test_full_ten_variant_cohort_fits_bound():
    records = [row for number in range(10) for row in complete(f'V{number}', 100 - number)]
    scored = score(records)
    assert scored['winner'] == 'V9'
    assert len(scored['table']) == 10
    assert scored['refused_records'] == 0
    with pytest.raises(ValueError, match='invalid_scoring_round'):
        score(records + [result(LANES[0])])


def test_unregistered_hybrid_cannot_silently_qualify():
    scored = score(complete() + complete('V10', 0))
    assert scored['winner'] == 'V0'
    assert scored['refused_records'] == 5

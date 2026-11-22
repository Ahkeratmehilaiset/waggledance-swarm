"""Self test of the question-test driver for the proposed Mind free-question route.

No model, no bridge log and no product code is run: the driver is exercised only against its own
synthetic stand-in of six invented rows. Question sets are private evaluation material and are not here.
"""
import importlib.util
import json
from pathlib import Path

import pytest

DRIVER_PATH = Path(__file__).with_name('mind_question_test_driver.py')


@pytest.fixture(scope='module')
def driver():
    spec = importlib.util.spec_from_file_location('mind_question_test_driver', DRIVER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_self_test_matches_the_hand_worked_classes(driver, capsys):
    assert driver.selftest() == 0
    assert capsys.readouterr().out.rstrip().endswith('SELFTEST MATCH')


def test_request_is_the_proposed_v3_shape(driver):
    request = driver.build_request('synthetic question', 'codex-tools-1', 'own')
    assert set(request) == {'schema', 'query_id', 'nonce', 'lane', 'scope', 'question'}
    assert request['schema'] == 'wd.mind-pipe-request.v3' and request['query_id'] == 'free_question'
    assert driver.is_hex(request['nonce']) and request['question'] == 'synthetic question'


def test_every_altered_response_is_refused(driver):
    outcome = driver.response_fault_cases()
    assert len(outcome) == 21
    assert sorted(name for name, result in outcome.items() if not result.startswith('refused:')) == []


def test_unbound_route_refuses_every_ask_and_reports_no_rate(driver):
    card, rows = driver.run(driver.PipeV3NotBound(), driver.synthetic_sets(), driver.LANES)
    assert card['all']['asks'] == 12 and card['all']['classes']['REFUSED'] == 12
    # A refusal stays in the count the rates are taken over: it reads as zero, never as no figure.
    assert card['all']['hit_first_rate'] == 0.0 and card['all']['negative_pass_rate'] == 0.0
    assert set(card['refusal_reasons']) == {'route_not_bound'}


def test_out_of_reach_is_counted_apart_from_a_miss(driver):
    with driver.using_scope(driver.EARLIER_SCOPE):  # a class of a lane that asks with scope own
        card, rows = driver.run(driver.SyntheticOwner(), driver.synthetic_sets(), driver.LANES)
    classes = {row['id']: row['class'] for row in rows}
    assert classes['A4'] == 'MISS_NO_CANDIDATES' and classes['A5'] == 'OUT_OF_REACH_NO_CANDIDATES'
    assert card['all']['answerable_asks_in_reach'] == 8 and card['all']['in_reach_and_answered_by_route'] == 7
    assert card['label'] == 'SYNTHETIC STAND-IN, NOT A MIND MEASUREMENT'


def test_card_and_rows_never_carry_a_question_text(driver, tmp_path):
    card, rows = driver.run(driver.SyntheticOwner(), driver.synthetic_sets(), driver.LANES)
    saved = json.dumps([card, rows])
    for document, _ in driver.synthetic_sets():
        for item in document['questions']:
            assert item['question'] not in saved


@pytest.mark.parametrize('change', [
    {'schema': 'other'}, {'gold_kind': 'event_id'}, {'author': ''}, {'questions': []}])
def test_a_malformed_set_is_refused(driver, change):
    document = dict(driver.synthetic_sets()[0][0], **change)
    with pytest.raises(driver.DriverError):
        driver.check_set(document)


def test_an_answerable_question_without_gold_is_refused(driver):
    document = json.loads(json.dumps(driver.synthetic_sets()[0][0]))
    document['questions'][0]['gold'] = []
    with pytest.raises(driver.DriverError):
        driver.check_set(document)


def test_a_peer_row_served_under_scope_own_is_never_a_hit(driver):
    """Lead's counterexample of 2026-11-22: gold with one own and one peer row, only the peer row served."""
    cases = driver.scope_regression_cases()
    assert cases == driver.EXPECTED_SCOPE_CASES
    assert cases['mixed_gold_only_peer_row_served'] == 'SERVED_OUTSIDE_SCOPE'
    assert cases['mixed_gold_own_then_peer_served'] == 'SERVED_OUTSIDE_SCOPE'
    assert cases['mixed_gold_only_own_row_served'] == 'HIT_FIRST'


@pytest.mark.parametrize('name', ['candidates_v2_label', 'candidates_v3_label', 'row_with_false_location',
                                  'rows_with_three_locations'])
def test_only_the_non_location_answer_with_three_hash_fields_is_read(driver, name):
    assert driver.response_fault_cases()[name].startswith('refused:')


def test_one_gold_reference_has_one_writer(driver):
    document = json.loads(json.dumps(driver.synthetic_sets()[0][0]))
    document['questions'][1]['gold'][0]['agent'] = 'codex-tools-1'
    with pytest.raises(driver.DriverError):
        driver.check_set(document)


def test_the_route_gets_the_trusted_caller_identity_beside_the_request(driver):
    """The caller identity is the driver's own lane and scope, not a value read out of the request."""
    seen = []

    class Recording(driver.SyntheticOwner):
        def __call__(self, request, caller=None):
            seen.append((dict(request), caller))
            return driver.SyntheticOwner.__call__(self, request, caller)

    item = dict(driver.synthetic_sets()[0][0]['questions'][0], ask_as='any')
    for lane in driver.LANES:
        driver.ask(Recording(), item, lane)
    assert [caller for _, caller in seen] == [(lane, driver.SCOPE[lane]) for lane in driver.LANES]
    assert all(type(caller) is tuple and caller == (request['lane'], request['scope']) for request, caller in seen)


def test_a_route_that_rewrites_the_request_cannot_move_the_caller_identity(driver):
    seen = []

    class Rewriting(driver.SyntheticOwner):
        def __call__(self, request, caller=None):
            request['lane'], request['scope'] = 'claude-rco-1', 'shared'
            seen.append(caller)
            return driver.SyntheticOwner.__call__(self, request, caller)

    item = driver.synthetic_sets()[0][0]['questions'][0]
    assert driver.ask(Rewriting(), item, 'codex-lead-1')[:2] == ('REFUSED', 'synthetic_owner_refused')
    assert seen == [('codex-lead-1', driver.SCOPE['codex-lead-1'])]


@pytest.mark.parametrize('caller', [('codex-tools-1', 'own'), ('codex-lead-1', 'shared'), ('own', 'codex-lead-1'),
                                    ['codex-lead-1', 'own'], ('codex-lead-1',), 'codex-lead-1'])
def test_the_stand_in_refuses_a_caller_identity_that_is_not_the_request_lane_and_scope(driver, caller):
    request = driver.build_request('synthetic amber valve', 'codex-lead-1', 'own')
    with driver.using_scope(driver.EARLIER_SCOPE):  # the stand-in takes own only from a lane whose table says own
        assert driver.SyntheticOwner()(dict(request), ('codex-lead-1', 'own'))['lane'] == 'codex-lead-1'
        with pytest.raises(driver.DriverError):
            driver.SyntheticOwner()(dict(request), caller)


def test_a_caller_identity_of_another_lane_is_a_refusal_in_the_card(driver):
    item = driver.synthetic_sets()[0][0]['questions'][0]
    assert driver.ask(driver._RouteGivingAnotherCaller(), item, 'codex-lead-1')[:2] == (
        'REFUSED', 'synthetic_owner_refused')


def test_mapper_route_hands_the_request_and_the_caller_identity_to_the_mapper(driver):
    calls = []

    class Refusal(Exception):
        pass

    def mapper(**arguments):
        calls.append(arguments)
        if arguments['request']['question'] == 'refuse':
            raise Refusal('x')
        if arguments['request']['question'] == 'break':
            raise RuntimeError('x')
        return 'answer'

    handler = object()
    route = driver.MapperRoute(mapper, handler, Refusal)
    request = driver.build_request('synthetic amber valve', 'codex-lead-1', 'own')
    assert route(request, ('codex-lead-1', 'own')) == 'answer'
    assert calls == [{'enabled': True, 'handler': handler, 'request': request,
                      'free_question_binding': ('codex-lead-1', 'own')}]
    assert calls[0]['request'] is request and calls[0]['handler'] is handler
    with pytest.raises(driver.DriverError, match='mapper_refused'):
        route(dict(request, question='refuse'), ('codex-lead-1', 'own'))
    with pytest.raises(RuntimeError):
        route(dict(request, question='break'), ('codex-lead-1', 'own'))


def test_a_mapper_refusal_is_one_counted_refusal_and_another_error_is_named(driver):
    class Refusal(Exception):
        pass

    def refusing(**arguments):
        raise Refusal('x')

    def breaking(**arguments):
        raise KeyError('x')

    item = driver.synthetic_sets()[0][0]['questions'][0]
    assert driver.ask(driver.MapperRoute(refusing, None, Refusal), item, 'codex-lead-1')[:2] == (
        'REFUSED', 'mapper_refused')
    assert driver.ask(driver.MapperRoute(breaking, None, Refusal), item, 'codex-lead-1')[:2] == (
        'REFUSED', 'route_raised_KeyError')


@pytest.mark.parametrize('arguments', [(None, None, Exception), (len, None, 'Exception'), (len, None, object)])
def test_mapper_route_refuses_unusable_arguments(driver, arguments):
    with pytest.raises(driver.DriverError):
        driver.MapperRoute(*arguments)


def test_the_active_table_gives_every_lane_the_shared_scope(driver):
    assert driver.SCOPE == driver.PRODUCT_SCOPE == {lane: 'shared' for lane in driver.LANES}
    assert sorted(set(driver.EARLIER_SCOPE.values())) == ['own', 'shared']


def test_under_shared_visibility_a_row_of_another_lane_is_in_reach_and_a_first_hit(driver):
    problems, card = driver.product_table_problems()
    assert problems == []
    _, rows = driver.run(driver.SyntheticOwner(), driver.synthetic_sets(), driver.LANES)
    classes = {row['id']: row['class'] for row in rows}
    # A5 and A6 ask a build lane for a row another lane wrote: out of reach under the earlier table.
    assert classes['A5'] == classes['A6'] == 'HIT_FIRST'
    assert all(row['scope'] == 'shared' and row['in_reach'] is not False for row in rows)
    assert card['all']['answerable_asks_in_reach'] == 10
    assert card['all']['classes']['OUT_OF_REACH_NO_CANDIDATES'] == 0
    assert card['all']['classes']['OUT_OF_REACH_WITH_CANDIDATES'] == 0


def test_using_scope_restores_the_table_and_refuses_a_bad_table(driver):
    before = dict(driver.SCOPE)
    with pytest.raises(RuntimeError):
        with driver.using_scope(driver.EARLIER_SCOPE):
            assert driver.SCOPE == driver.EARLIER_SCOPE
            raise RuntimeError('x')
    assert driver.SCOPE == before
    for bad in ({}, dict(before, **{'codex-lead-1': 'all'}), dict(before, extra='shared'), list(before)):
        with pytest.raises(driver.DriverError):
            with driver.using_scope(bad):
                pass
    assert driver.SCOPE == before

"""Draft binding of the question-test driver to the draft free-question mapper.

The owner layer below the mapper is a synthetic stand-in of six invented rows: no model, no bridge log
and no owner process. This shows that the request and the trusted caller binding of the driver fit the
mapper and that the driver reads the mapper's answer. It shows nothing about retrieval quality, and the
own-scope filter of the real owner lies below the stand-in and is not exercised here.
"""
import importlib.util
from pathlib import Path

import pytest

from tools import wd_mind_full_runtime as runtime

DRIVER_PATH = Path(__file__).with_name('mind_question_test_driver.py')
LEAD = 'codex-lead-1'
MISSING = object()


class TupleSubclass(tuple):
    pass


class TextSubclass(str):
    pass


@pytest.fixture(scope='module')
def driver():
    spec = importlib.util.spec_from_file_location('mind_question_test_driver', DRIVER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def bound(driver, monkeypatch):
    """The driver's mapper route over a handler whose owner layer is the six-row stand-in."""
    observed, owner = [], [driver.SyntheticOwner()]

    def owner_layer(self, request, bound_lane):
        observed.append((bound_lane, dict(request)))
        answer = owner[0](driver.build_request(request['query'], bound_lane, request['scope']))
        results = [{'ref_sha256': row['ref_sha256'], 'doc_id_sha256': row['doc_id_sha256'],
                    'source_reference': {'sha256': row['source_event_sha256'],
                                         'kind': 'sha256_of_canonical_event_json',
                                         'verified': 'MATCH_WITHIN_PREFIX', 'sender_authentication': 'UNKNOWN'}}
                   for row in answer['candidates']]
        return {'schema': 'wd.mind-flat-owner-response.v1', 'lane': bound_lane, 'scope': request['scope'],
                'authority': 'none', 'expires_utc': None, 'expiry_scope': 'source_validity_only',
                'snapshot_freshness': 'UNKNOWN',
                'result': {'schema': 'wd.mind-flat-f32-query.v1',
                           'status': 'SEMANTIC_CANDIDATE' if results else 'ABSTAIN', 'authority': 'none',
                           'relevance': 'UNVERIFIED', 'index_match': 'NOT_APPLICABLE',
                           'own_latest_task': 'UNKNOWN', 'results': results}}

    monkeypatch.setattr(runtime._FlatRequestHandler, '_handle_for_lane', owner_layer)
    handler = runtime._FlatRequestHandler(binding=None, snapshot=None, lane=driver.LANES[0], lanes=driver.LANES,
                                          rotator=None, adapter=None, factory=None, timeout=None, bulk=None)
    route = driver.MapperRoute(runtime.map_pipe_candidates, handler, runtime.FullRuntimeRefused)
    return route, handler, observed, owner


def items(driver):
    return {question['id']: question for document, _ in driver.synthetic_sets() for question in document['questions']}


def test_two_distinct_questions_reach_the_owner_layer_unchanged(driver, bound):
    route, _, observed, _ = bound
    item = items(driver)
    first, second = driver.ask(route, item['A1'], LEAD), driver.ask(route, item['A2'], LEAD)
    assert [request for _, request in observed] == [
        {'schema': 'wd.mind-flat-owner-request.v1', 'lane': LEAD, 'scope': driver.SCOPE[LEAD],
         'query': item[key]['question']}
        for key in ('A1', 'A2')]
    assert [lane for lane, _ in observed] == [LEAD, LEAD]
    assert item['A1']['question'] != item['A2']['question']
    assert (first[0], second[0]) == ('HIT_FIRST', 'HIT_WITHIN_THREE')


@pytest.mark.parametrize(('binding', 'change'), [
    pytest.param(MISSING, {}, id='no-binding-argument'),
    pytest.param(None, {}, id='none'),
    pytest.param(('codex-tools-1', 'own'), {}, id='another-lane'),
    pytest.param((LEAD, 'shared'), {}, id='another-scope'),
    pytest.param(('own', LEAD), {}, id='swapped'),
    pytest.param([LEAD, 'own'], {}, id='list'),
    pytest.param((LEAD,), {}, id='one-element'),
    pytest.param((LEAD, 'own', 'x'), {}, id='three-elements'),
    pytest.param((LEAD, 1), {}, id='non-text-element'),
    pytest.param((LEAD.upper(), 'own'), {}, id='capitals'),
    pytest.param(TupleSubclass((LEAD, 'own')), {}, id='tuple-subclass'),
    pytest.param((TextSubclass(LEAD), 'own'), {}, id='text-subclass-element'),
    pytest.param((LEAD, 'own'), {'lane': 'codex-tools-1'}, id='request-lane-changed'),
    pytest.param((LEAD, 'own'), {'scope': 'shared'}, id='request-scope-changed'),
])
def test_a_binding_that_is_not_the_request_lane_and_scope_is_refused_before_the_owner_layer(
        driver, bound, binding, change):
    _, handler, observed, _ = bound
    request = dict(driver.build_request('synthetic amber valve', LEAD, 'own'), **change)
    arguments = {} if binding is MISSING else {'free_question_binding': binding}
    with pytest.raises(runtime.FullRuntimeRefused):
        runtime.map_pipe_candidates(enabled=True, handler=handler, request=request, **arguments)
    assert observed == []


def test_the_true_binding_is_served_and_a_wrong_one_is_a_counted_refusal(driver, bound):
    route, handler, observed, _ = bound
    item = items(driver)['A1']
    assert driver.ask(route, item, LEAD)[0] == 'HIT_FIRST' and len(observed) == 1

    class AnotherCaller(driver.MapperRoute):
        def __call__(self, request, caller=None):
            return driver.MapperRoute.__call__(self, request, ('codex-tools-1', 'own'))

    wrong = AnotherCaller(runtime.map_pipe_candidates, handler, runtime.FullRuntimeRefused)
    assert driver.ask(wrong, item, LEAD)[:2] == ('REFUSED', 'mapper_refused') and len(observed) == 1


@pytest.mark.parametrize('table', ['product', 'earlier'])
def test_the_twelve_invented_asks_give_the_hand_worked_classes_through_the_mapper(driver, bound, table):
    route, _, observed, _ = bound
    scope, wanted = ((driver.PRODUCT_SCOPE, driver.EXPECTED_PRODUCT) if table == 'product'
                     else (driver.EARLIER_SCOPE, driver.EXPECTED_SELFTEST))
    with driver.using_scope(scope):
        card, rows = driver.run(route, driver.synthetic_sets(), driver.LANES)
    assert {row['id']: row['class'] for row in rows} == wanted['per_ask']
    assert [request['scope'] for lane, request in observed] == [scope[lane] for lane, request in observed]
    assert card['refusal_reasons'] == {'mapper_refused': 1}  # the ask with a NUL character
    assert len(observed) == 11 and all('\x00' not in request['query'] for _, request in observed)


def test_the_startup_request_keeps_the_fixed_overview_text(driver, bound):
    _, handler, observed, _ = bound
    startup = {'schema': 'wd.mind-pipe-request.v1', 'query_id': 'startup_work_overview', 'nonce': 'd' * 64,
               'lane': LEAD, 'scope': driver.SCOPE[LEAD]}
    answer = runtime.map_pipe_candidates(enabled=True, handler=handler, request=startup)
    assert answer['schema'] == 'wd.mind-pipe-candidates.v1'
    assert [request['query'] for _, request in observed] == [runtime._STARTUP_PIPE_QUERY]
    with pytest.raises(runtime.FullRuntimeRefused):
        runtime.map_pipe_candidates(enabled=True, handler=handler, request=dict(startup, question='x'))
    assert len(observed) == 1


def test_a_foreign_row_from_the_owner_layer_passes_the_mapper_and_is_named_by_the_driver(driver, bound):
    """The mapper adds no scope filter of its own; the driver still names a row written by another lane."""
    route, _, observed, owner = bound
    owner[0] = driver._OwnerIgnoringScope()
    with driver.using_scope(driver.EARLIER_SCOPE):  # only a lane that asks with scope own can be served outside it
        assert driver.ask(route, items(driver)['A5'], LEAD)[0] == 'SERVED_OUTSIDE_SCOPE'
    assert len(observed) == 1 and observed[0][1]['scope'] == 'own'


def test_the_driver_table_equals_both_product_helpers(driver):
    """Three places fix the scope per lane: the session helper, the child helper and the driver table."""
    from tools import wd_bridge_mind_session as session, wd_mind_runtime_child as child
    for lane in driver.LANES:
        assert session._startup_scope_for_lane(lane) == child._startup_scope_for_lane(lane) == driver.SCOPE[lane]
    assert driver.SCOPE == driver.PRODUCT_SCOPE


def test_under_shared_visibility_a_build_lane_reaches_a_row_of_another_lane_through_the_mapper(driver, bound):
    """Synthetic cross-lane case: Lead asks for a row the stand-in holds as written by the tools lane."""
    route, _, observed, _ = bound
    item = items(driver)['A5']
    assert [row['agent'] for row in item['gold']] == ['codex-tools-1']
    assert driver.ask(route, item, LEAD)[0] == 'HIT_FIRST'
    assert observed == [(LEAD, {'schema': 'wd.mind-flat-owner-request.v1', 'lane': LEAD, 'scope': 'shared',
                                'query': item['question']})]

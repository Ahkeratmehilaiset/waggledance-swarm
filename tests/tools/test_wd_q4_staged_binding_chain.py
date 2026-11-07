"""Synthetic C2 -> five lane binding consumers -> shared descriptor.

This checks byte and contract consistency, not a real E5 query, a running lane,
producer authenticity, source freshness, or installed/runtime behavior.
"""
import json

from tests.tools.test_wd_q4_staged_lane_query import staged_producer  # noqa: F401 - pytest fixture
from tests.tools.test_wd_q4_shared_descriptor_producer import kwargs as descriptor_kwargs
from tools import wd_q4_lane_binding_producer as producer
from tools import wd_q4_lane_binding as binding
from tools import wd_q4_lane_query as query
from tools import wd_q4_lane_link as link
from tools import wd_q4_lane_caller_cli as cli
from tools import wd_q4_shared_descriptor_producer as descriptor


def test_actual_c2_producer_to_five_binding_consumers_and_descriptor(staged_producer, tmp_path):
    case = staged_producer
    result = producer.build_staged_lane_binding(**case)
    document = json.loads(result['binding_bytes'])
    expected = document['binding']
    assert set(expected) == query.STAGED_BINDING_KEYS == binding.STAGED_BINDING_KEYS
    assert len(expected) == 10 and 'receipt_sha256' not in expected
    assert document['lanes'] == sorted(binding._roster(case['fleet_bytes'], case['expected_fleet_sha256'])[0])
    for lane in document['lanes']:
        consumed = binding.resolve_lane_binding(case['fleet_bytes'], case['expected_fleet_sha256'],
            result['binding_bytes'], result['binding_sha256'], lane=lane, enabled=True)
        assert consumed['expected_binding'] == expected
    assert cli._binding(expected) == expected
    path = tmp_path / 'pinned-plan.json'
    path.write_bytes(case['public_plan_bytes'])
    plan_pins = link._public_plan(str(path), case['expected_public_plan_sha256'])[2]
    assert all(expected[k] == v for k, v in plan_pins.items())
    options = descriptor_kwargs(tmp_path)
    options.update(fleet_bytes=case['fleet_bytes'], expected_fleet_sha256=case['expected_fleet_sha256'],
                   expected_binding=expected, binding_sha256=result['binding_sha256'])
    built = descriptor.build_shared_descriptor(enabled=True, **options)
    doc = json.loads(built['descriptor_bytes'])
    assert doc['expected_binding'] == expected
    assert doc['binding_sha256'] == result['binding_sha256']

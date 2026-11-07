"""Synthetic descriptor construction only: no sessions or installation."""
import json

import pytest

from tests.tools.test_wd_q4_shared_descriptor_producer import build, kwargs, module


@pytest.mark.parametrize('optional', [(), ('card_decision_sha256',),
                                      ('card_decision_sha256', 'card_policy_sha256')])
def test_descriptor_preserves_exact_declared_optional_pins(tmp_path, optional):
    args = kwargs(tmp_path)
    args['expected_binding'].update({key: format(i + 20, '064x') for i, key in enumerate(optional)})
    document = json.loads(build(args)['descriptor_bytes'])
    assert document['expected_binding'] == args['expected_binding']


@pytest.mark.parametrize('extra', [{'card_policy_sha256': 'a' * 64},
                                   {'card_decision_sha256': 'a' * 64, 'card_policy_sha256': 'bad'},
                                   {'card_decision_sha256': 'a' * 64, 'unknown_sha256': 'b' * 64}])
def test_descriptor_refuses_incomplete_or_unrecognized_optional_pins(tmp_path, extra):
    args = kwargs(tmp_path)
    args['expected_binding'].update(extra)
    with pytest.raises(module().DescriptorRefused):
        build(args)

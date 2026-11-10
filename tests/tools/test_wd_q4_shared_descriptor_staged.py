"""Synthetic descriptor construction only: no sessions or installation."""
import json

import pytest

from tests.tools.test_wd_q4_shared_descriptor_producer import build, module, synthetic_kwargs


@pytest.mark.parametrize('optional', [(), ('card_decision_sha256',),
                                      ('card_decision_sha256', 'card_policy_sha256')])
def test_descriptor_preserves_exact_declared_optional_pins(tmp_path, optional):
    args = synthetic_kwargs()  # build() never touches the paths; a POSIX tmp_path would be refused first
    args['expected_binding'].update({key: format(i + 20, '064x') for i, key in enumerate(optional)})
    document = json.loads(build(args)['descriptor_bytes'])
    assert document['expected_binding'] == args['expected_binding']


@pytest.mark.parametrize('extra,reason', [
    ({'card_policy_sha256': 'a' * 64}, 'schema_refused'),
    ({'card_decision_sha256': 'a' * 64, 'card_policy_sha256': 'bad'}, 'pin_refused'),
    ({'card_decision_sha256': 'a' * 64, 'unknown_sha256': 'b' * 64}, 'schema_refused'),
])
def test_descriptor_refuses_incomplete_or_unrecognized_optional_pins(tmp_path, extra, reason):
    args = synthetic_kwargs()  # else on Linux path_refused would satisfy pytest.raises before the pin check
    args['expected_binding'].update(extra)
    with pytest.raises(module().DescriptorRefused, match='^' + reason + '$'):
        build(args)

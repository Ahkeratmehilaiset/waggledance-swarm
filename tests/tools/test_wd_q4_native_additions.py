"""Synthetic admission of additions during caller-observed native intervals."""

import types

import pytest

from sandbox.mind.step2.q4_native_additions import NativeAdditionsUnknown, validate_additions


def inputs():
    module = types.ModuleType('_synthetic_native')
    module.existing = object()
    recorded = dict(vars(module))
    before = dict(vars(module))
    module.added = object()
    return dict(name=module.__name__, module=module, registry={module.__name__: module},
                registry_before={module.__name__: module}, registry_after={module.__name__: module},
                recorded=recorded, before=before, after=dict(vars(module)),
                window_verified=True, nested_or_concurrent=False)


def test_exact_addition_is_returned_as_a_separate_snapshot():
    case = inputs()
    updated = validate_additions(**case)
    assert updated is not case['after']
    assert updated['existing'] is case['recorded']['existing']
    assert updated['added'] is case['module'].added
    assert 'added' not in case['recorded']


@pytest.mark.parametrize('failure', [
    'foreign_registry', 'foreign_registry_before', 'foreign_registry_after',
    'changed_name', 'changed_before', 'missing_before',
    'removed_after', 'changed_after', 'false_window', 'unknown_window',
    'nested', 'unknown_nesting', 'current_changed', 'no_addition', 'non_dict',
])
def test_ambiguous_or_non_append_only_state_is_unknown(failure):
    case = inputs()
    if failure == 'foreign_registry':
        case['registry'][case['name']] = types.ModuleType(case['name'])
    elif failure == 'foreign_registry_before':
        case['registry_before'][case['name']] = types.ModuleType(case['name'])
    elif failure == 'foreign_registry_after':
        case['registry_after'][case['name']] = types.ModuleType(case['name'])
    elif failure == 'changed_name':
        case['name'] = 'other'
    elif failure == 'changed_before':
        case['before']['existing'] = object()
    elif failure == 'missing_before':
        del case['before']['existing']
    elif failure == 'removed_after':
        del case['after']['existing']
    elif failure == 'changed_after':
        case['after']['existing'] = object()
    elif failure == 'false_window':
        case['window_verified'] = False
    elif failure == 'unknown_window':
        case['window_verified'] = None
    elif failure == 'nested':
        case['nested_or_concurrent'] = True
    elif failure == 'unknown_nesting':
        case['nested_or_concurrent'] = None
    elif failure == 'current_changed':
        case['module'].added = object()
    elif failure == 'no_addition':
        del case['module'].added
        case['after'] = dict(vars(case['module']))
    else:
        case['after'] = []
    with pytest.raises(NativeAdditionsUnknown):
        validate_additions(**case)


def test_equal_but_distinct_values_do_not_satisfy_identity():
    case = inputs()
    case['recorded']['existing'] = [1]
    case['before']['existing'] = [1]
    with pytest.raises(NativeAdditionsUnknown):
        validate_additions(**case)

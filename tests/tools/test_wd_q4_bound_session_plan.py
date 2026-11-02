"""Synthetic-only planning: no session, process, model, or live bridge."""
import copy
import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location('q4_tuple_fixture', Path(__file__).with_name('test_wd_q4_descriptor_host_tuple.py'))
fixtures = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixtures)


@pytest.fixture
def inputs(tmp_path):
    candidate = fixtures.candidate.__wrapped__(tmp_path)
    candidate.update(fixtures._with_manifest(candidate))
    return candidate


def plan(**kwargs):
    from tools.wd_q4_bound_session_plan import build_bound_session_plan
    return build_bound_session_plan(**kwargs)


def test_disabled_does_not_touch_inputs_or_run_resolver(monkeypatch):
    import tools.wd_q4_bound_session_plan as module
    monkeypatch.setattr(module, 'resolve_descriptor_host_tuple', lambda **_: pytest.fail('resolver called'))
    monkeypatch.setattr('builtins.open', lambda *_, **__: pytest.fail('file opened'))
    class Trap:
        def __getattribute__(self, name):
            raise AssertionError('input accessed')
    assert plan(manifest_bytes=Trap(), expected_manifest_sha256=Trap(), expected_manifest_commit=Trap(),
                descriptor=Trap(), host=Trap()) == {'status': 'OFF'}


def test_plan_has_only_pinned_command_and_host_arguments(inputs):
    result = plan(**inputs, enabled=True)
    assert set(result) == {'status', 'manifest_binding', 'command_script', 'command_sha256',
                           'host_script', 'host_sha256', 'host_arguments'}
    assert result['status'] == 'PLAN_ONLY' and result['manifest_binding'] == 'MANIFEST_PINS_ONLY'
    assert result['command_script'] == str(Path(inputs['descriptor']['bundle_root']) / 'Invoke-WdQ4SessionCommand.ps1')
    assert result['command_sha256'] == '4' * 64
    assert result['host_script'] == inputs['host']['HostScript']
    assert result['host_sha256'] == inputs['host']['HostSha256']
    assert 'query' not in result


@pytest.mark.parametrize('fault', ['manifest_sha', 'missing_manifest', 'host_pin', 'bootstrap_pin'])
def test_real_resolver_fails_closed(inputs, fault):
    if fault == 'manifest_sha': inputs['expected_manifest_sha256'] = '9' * 64
    if fault == 'missing_manifest': del inputs['manifest_bytes']
    if fault == 'host_pin': inputs['host']['HostSha256'] = '9' * 64
    if fault == 'bootstrap_pin': inputs['host']['BootstrapSha256'] = '9' * 64
    with pytest.raises(ValueError, match='^bound_session_plan_refused$'):
        plan(**inputs, enabled=True)


@pytest.mark.parametrize('fault', ['legacy', 'missing_manifest_pin', 'bad_status', 'extra_result',
                                 'extra_host_argument', 'query_argument', 'duplicate_argument'])
def test_resolver_result_contract_is_closed(inputs, monkeypatch, fault):
    import tools.wd_q4_bound_session_plan as module
    real = module.resolve_descriptor_host_tuple(**inputs, enabled=True)
    bad = copy.deepcopy(real)
    if fault == 'legacy': del bad['manifest_binding']
    if fault == 'missing_manifest_pin': del bad['manifest_additional_pins']['Invoke-WdQ4SessionCommand.ps1']
    if fault == 'bad_status': bad['status'] = 'OFF'
    if fault == 'extra_result': bad['query'] = 'synthetic-private-marker'
    if fault == 'extra_host_argument': bad['host_arguments']['query'] = 'synthetic-private-marker'
    if fault == 'query_argument': bad['host_arguments']['BootstrapArguments'][1] = '--query'
    if fault == 'duplicate_argument': bad['host_arguments']['BootstrapArguments'][3] = bad['host_arguments']['BootstrapArguments'][1]
    monkeypatch.setattr(module, 'resolve_descriptor_host_tuple', lambda **_: bad)
    with pytest.raises(ValueError, match='^bound_session_plan_refused$'):
        plan(**inputs, enabled=True)


def test_query_keyword_not_accepted(inputs):
    with pytest.raises(TypeError):
        plan(**inputs, enabled=True, query='synthetic-private-marker')

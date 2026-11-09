"""Synthetic continuation orchestration; no live processes or bridge data."""
import importlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from tests.tools.test_wd_q4_production_continuation import genesis_synthetic, sha
from tests.tools import test_wd_q4_genesis_bootstrap as base
from tests.tools.test_wd_q4_continuation_runtime_bootstrap import candidate, job
from tests.tools.test_wd_q4_continuation_job_v3 import real_cli_with_synthetic_continuation


def test_default_off_does_not_read_or_launch(monkeypatch):
    module = importlib.import_module('tools.wd_q4_continuation_orchestrator')
    monkeypatch.setattr(module, '_run_fixed', lambda *_args, **_kwargs: pytest.fail('launched'))
    assert module.run_once() == {'status': 'OFF'}


def test_opt_in_requires_bound_inputs_before_launch(monkeypatch):
    module = importlib.import_module('tools.wd_q4_continuation_orchestrator')
    monkeypatch.setattr(module, '_run_fixed', lambda *_args, **_kwargs: pytest.fail('launched'))
    with pytest.raises(module.OrchestrationRefused):
        module.run_once(enabled=True)


@pytest.fixture
def real_synthetic(genesis_synthetic, monkeypatch):
    from tools import wd_q4_continuation_orchestrator as module
    from tools import wd_q4_production_continuation as continuation
    args, _ = genesis_synthetic
    base.test_late_skip_real_producer_publishes_every_public_row_after_the_skip(genesis_synthetic, monkeypatch)
    source = Path(args['source_path'])
    row = json.loads(base.f2_rows(args, ['public'])[0])
    row.update(ts_utc='2026-10-31T13:00:30Z', status='done')
    with source.open('ab') as stream:
        stream.write(base.raw(row) + b'\n')
    private, shared = Path(args['private_state_root']), Path(args['checkpoint_root'])
    tuple_raw = (private / 'q4-genesis-tuple-v1.json').read_bytes()
    common = dict(enabled=True, tuple_bytes=tuple_raw, expected_tuple_sha256=sha(tuple_raw),
                  expected_pointer_sha256=sha((shared / 'q4-latest.json').read_bytes()),
                  policy_bytes=args['policy_bytes'], public_policy_bytes=args['public_policy_bytes'],
                  fleet_bytes=args['fleet_bytes'], expected_fleet_sha256=args['expected_fleet_sha256'],
                  watermark_enabled=True)
    first = continuation.continue_once(**common, cutoff_utc='2026-10-31T13:00:00Z',
        previous_cutoff_utc='2026-10-31T13:00:00Z', expected_watermark_record_sha256=None)
    template = {'tuple_sha256': sha(tuple_raw), 'fleet_sha256': args['expected_fleet_sha256']}
    monkeypatch.setattr(module, '_validate_anchor', lambda *_: (template, private, private / 'script', private / 'host',
                         private / 'runtime', private, tuple_raw, args['policy_bytes'], args['public_policy_bytes'],
                         args['fleet_bytes']))
    seen = []
    def run_fixed(_bundle, _script, _host, config_path, config_pin, *_args):
        raw = config_path.read_bytes()
        assert sha(raw) == config_pin
        config = json.loads(raw)
        seen.append(config)
        call = dict(common, expected_pointer_sha256=config['pointer_sha256'], cutoff_utc=config['cutoff_utc'],
                    previous_cutoff_utc=config['previous_cutoff_utc'],
                    expected_watermark_record_sha256=config['watermark_previous_record_sha256'])
        result = continuation.continue_once(**call)
        clean = module.cli._result(result, call)
        return SimpleNamespace(returncode=0, stdout=module._raw(clean) + b'\n', stderr=b'')
    monkeypatch.setattr(module, '_run_fixed', run_fixed)
    return module, args, private, shared, first, seen


def test_two_real_synthetic_continuations_advance_from_verified_record(real_synthetic):
    module, args, private, shared, first, seen = real_synthetic
    result = module.run_once(enabled=True, template_sha256='a' * 64, cutoff_utc='2026-10-31T13:02:00Z')
    assert result['status'] == 'caught_up_to_cutoff'
    assert seen[0]['watermark_previous_record_sha256'] == first['watermark_record_sha256']
    assert seen[0]['previous_cutoff_utc'] == first['cutoff_utc']
    pointer = json.loads((shared / 'q4-latest.json').read_bytes())
    projection = json.loads((shared / pointer['generation'] / 'projection.json').read_bytes())
    assert any(row['meta'].get('status') == 'done' for row in projection['records'])
    second = module.run_once(enabled=True, template_sha256='a' * 64, cutoff_utc='2026-10-31T13:03:00Z')
    assert second['status'] == 'DEFERRED'
    assert seen[1]['watermark_previous_record_sha256'] == result['watermark_record_sha256']
    assert not (private / 'q4-continuation-orchestrator.pending.json').exists()


def test_interrupted_call_keeps_pending_and_never_retries(real_synthetic, monkeypatch):
    module, _, private, _, _, _ = real_synthetic
    calls = []
    def interrupted(*args):
        calls.append(args)
        raise TimeoutError()
    monkeypatch.setattr(module, '_run_fixed', interrupted)
    for _ in range(2):
        with pytest.raises(module.OrchestrationUnknown):
            module.run_once(enabled=True, template_sha256='a' * 64, cutoff_utc='2026-10-31T13:02:00Z')
    assert len(calls) == 1
    assert (private / 'q4-continuation-orchestrator.pending.json').exists()


def test_stale_cutoff_is_refused_before_pending_or_launch(real_synthetic):
    module, _, private, _, _, seen = real_synthetic
    with pytest.raises(module.OrchestrationRefused):
        module.run_once(enabled=True, template_sha256='a' * 64, cutoff_utc='2026-10-31T12:59:00Z')
    assert not seen
    assert not (private / 'q4-continuation-orchestrator.pending.json').exists()


def test_orphan_pending_is_never_deleted_or_retried(real_synthetic):
    module, _, private, _, _, seen = real_synthetic
    pending = private / 'q4-continuation-orchestrator.pending.json'
    pending.write_bytes(b'synthetic orphan\n')
    with pytest.raises(module.OrchestrationUnknown):
        module.run_once(enabled=True, template_sha256='a' * 64, cutoff_utc='2026-10-31T13:02:00Z')
    assert pending.read_bytes() == b'synthetic orphan\n'
    assert not seen


def test_unresolved_adoption_blocks_further_continuation(real_synthetic):
    module, _, private, shared, _, seen = real_synthetic
    marker = private / 'q4-continuation-adopt.pending.json'
    marker.write_bytes(b'unknown adoption outcome\n')
    before = (shared / 'q4-latest.json').read_bytes()
    with pytest.raises(module.OrchestrationUnknown):
        module.run_once(enabled=True, template_sha256='a' * 64, cutoff_utc='2026-10-31T13:02:00Z')
    assert marker.read_bytes() == b'unknown adoption outcome\n'
    assert (shared / 'q4-latest.json').read_bytes() == before
    assert not seen


def test_bad_child_receipt_is_unknown_and_leaves_pending(real_synthetic, monkeypatch):
    module, _, private, _, _, _ = real_synthetic
    monkeypatch.setattr(module, '_run_fixed', lambda *_: SimpleNamespace(
        returncode=0, stdout=b'{"status":"DEFERRED"}\n', stderr=b''))
    with pytest.raises(module.OrchestrationUnknown):
        module.run_once(enabled=True, template_sha256='a' * 64, cutoff_utc='2026-10-31T13:02:00Z')
    assert (private / 'q4-continuation-orchestrator.pending.json').exists()


def test_interrupt_is_not_swallowed_by_ordinary_exception_handler(real_synthetic, monkeypatch):
    module, _, private, _, _, _ = real_synthetic
    def interrupted(*_):
        raise KeyboardInterrupt()
    monkeypatch.setattr(module, '_run_fixed', interrupted)
    with pytest.raises(module.OrchestrationInterrupted):
        try:
            module.run_once(enabled=True, template_sha256='a' * 64, cutoff_utc='2026-10-31T13:02:00Z')
        except Exception:
            pytest.fail('ordinary exception handler swallowed interruption')
    assert (private / 'q4-continuation-orchestrator.pending.json').exists()


@pytest.fixture
def anchored_template(tmp_path):
    from tools import wd_q4_continuation_orchestrator as module
    bundle, scratch = tmp_path / 'bundle', tmp_path / 'scratch'
    bundle.mkdir()
    scratch.mkdir()
    script = bundle / 'Invoke-WdQ4Continuation.ps1'
    script.write_bytes(b'# synthetic, never executed\n')
    fleet = b'{"fleet":"synthetic"}'
    (bundle / 'wd-fleet.json').write_bytes(fleet)
    manifest_raw = module._raw({'schema_version': 1, 'source_commit': 'b' * 40,
        'files': {'Invoke-WdQ4Continuation.ps1': sha(script.read_bytes()).upper(), 'wd-fleet.json': sha(fleet).upper()}})
    (bundle / 'deployment-manifest.json').write_bytes(manifest_raw)
    identity = {'identity': 'synthetic-identity', 'prefix_bytes': 1, 'prefix_sha256': 'c' * 64}
    tuple_raw = module._raw({'independent_source_pin': identity})
    tuple_path = tmp_path / 'tuple.json'
    tuple_path.write_bytes(tuple_raw)
    policy, public, runtime = (tmp_path / name for name in ('policy.json', 'public.json', 'runtime.json'))
    for path, data in ((policy, b'{}'), (public, b'{}'), (runtime, b'{}')):
        path.write_bytes(data)
    template = {'schema': 'wd.q4-continuation-cli.v3', 'bundle_root': str(bundle),
                'manifest_sha256': sha(manifest_raw), 'tuple_path': str(tuple_path), 'tuple_sha256': sha(tuple_raw),
                'policy_path': str(policy), 'policy_sha256': sha(policy.read_bytes()),
                'public_policy_path': str(public), 'public_policy_sha256': sha(public.read_bytes()),
                'fleet_sha256': sha(fleet), 'pointer_sha256': 'd' * 64, 'source_identity': identity['identity'],
                'source_prefix_bytes': identity['prefix_bytes'], 'source_prefix_sha256': identity['prefix_sha256'],
                'cutoff_utc': '2026-10-31T13:00:00Z', 'previous_cutoff_utc': '2026-10-31T13:00:00Z',
                'watermark_previous_record_sha256': None}
    template_path = tmp_path / 'template.json'
    template_path.write_bytes(module._raw(template))
    host = Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    inputs = dict(template_path=str(template_path), template_sha256=sha(template_path.read_bytes()),
                  expected_commit='b' * 40, expected_manifest_sha256=sha(manifest_raw), runtime_path=str(runtime),
                  runtime_sha256=sha(runtime.read_bytes()), scratch_root=str(scratch),
                  expected_host_sha256=sha(host.read_bytes()))
    return module, inputs, script, tuple_path


def test_anchored_template_validates_real_synthetic_files_and_pinned_script(anchored_template):
    module, inputs, script, _ = anchored_template
    assert module._validate_anchor(**inputs)[1] == Path(inputs['template_path']).parent / 'bundle'
    script.write_bytes(b'tampered')
    with pytest.raises(Exception):
        module._validate_anchor(**inputs)


def test_tampered_tuple_is_refused_before_attempt(anchored_template):
    module, inputs, _, tuple_path = anchored_template
    tuple_path.write_bytes(b'{}')
    with pytest.raises(module.OrchestrationRefused):
        module.run_once(enabled=True, cutoff_utc='2026-10-31T13:01:00Z', **inputs)


def test_fixed_process_invokes_real_job_bootstrap_and_cli_with_synthetic_continuation(job):
    from tools import wd_q4_continuation_orchestrator as module
    expected, _ = real_cli_with_synthetic_continuation(job)
    scratch = job['bundle'].parent / 'scratch'
    scratch.mkdir()
    host = Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    script = Path(__file__).resolve().parents[2] / 'ops/windows/reboot/Invoke-WdQ4Continuation.ps1'
    result = module._run_fixed(job['bundle'], script, host, job['config'], job['config_pin'], job['manifest_pin'],
                               'c' * 40, job['spec'], job['spec_pin'], scratch, sha(host.read_bytes()), 30)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == expected

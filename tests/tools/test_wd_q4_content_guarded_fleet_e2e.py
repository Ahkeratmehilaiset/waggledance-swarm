"""Synthetic content startup contract; does not by itself prove a guarded Job query.

The process/Job acceptance test is separate and remains pending integration of
producer, backend and v5 query at one pinned head.
The roster uses a canonical synthetic test adapter, not a production fleet read.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from tests.tools.test_wd_q4_public_content_plan_writer import setup, lane, content
from tests.tools.test_wd_q4_shared_descriptor_producer import kwargs as descriptor_kwargs, validate
from tools import wd_q4_lane_binding as binding
from tools import wd_q4_lane_binding_producer as producer
from tools import wd_q4_shared_descriptor_producer as descriptor_producer

LANES = ('codex-lead-1', 'codex-tools-1', 'claude-rco-1', 'claude-rco-2', 'fable-5')
PINS = ('encoder_sha256', 'policy_sha256', 'roster_sha256', 'content_decision_sha256',
        'descriptor_sha256', 'memory_sha256', 'projection_sha256')


def digest(value):
    return hashlib.sha256(value).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('ascii')


@pytest.fixture
def canonical_content_roster(monkeypatch):
    from sandbox.mind.step2 import test_content_route as route
    from tools import wd_q4_roster_from_fleet as roster
    original = route.strict_pins

    def canonical_pins(*args, **kwargs):
        pins = original(*args, **kwargs)
        pins['roster_bytes'], pins['roster_sha256'] = roster.build_roster_snapshot(
            pins['roster_bytes'], pins['roster_sha256'], enabled=True)
        return pins

    monkeypatch.setattr(route, 'strict_pins', canonical_pins)


@pytest.fixture
def pinned_content_candidate(canonical_content_roster, setup):
    from tools import wd_q4_public_content_plan_writer as writer
    arguments = dict(setup['arguments'])
    v1_path = Path(arguments['public_plan_path'])
    v1 = json.loads(v1_path.read_bytes())
    v1['config']['memory'] = str(setup['lane']['content']['dir'])
    v1['config']['descriptor'] = str(Path(v1['config']['memory']) / 'descriptor.json')
    v1_bytes = canonical(v1)
    v1_path.write_bytes(v1_bytes)
    arguments['expected_public_plan_sha256'] = digest(v1_bytes)
    written = writer.write_content_plan(enabled=True, **arguments)
    raw = Path(written['public_plan_path']).read_bytes()
    assert digest(raw) == written['public_plan_sha256']
    fleet_doc = json.loads(setup['lane']['content']['args']['roster_bytes'])
    fleet_doc.update(git_executable=setup['arguments']['doc_git_executable'],
                     git_sha256=setup['arguments']['doc_git_sha256'])
    fleet = canonical(fleet_doc)
    wrapper = b'synthetic-wrapper-not-executed-in-this-test'
    manifest = canonical({'schema_version': 1, 'source_commit': 'a' * 40,
                          'files': {'wd-fleet.json': digest(fleet), 'Invoke-WdQ4Query.ps1': digest(wrapper)}})
    return dict(enabled=True, expected_commit='a' * 40, manifest_bytes=manifest,
                expected_manifest_sha256=digest(manifest), fleet_bytes=fleet, expected_fleet_sha256=digest(fleet),
                wrapper_bytes=wrapper, expected_wrapper_sha256=digest(wrapper), public_plan_bytes=raw,
                expected_public_plan_sha256=written['public_plan_sha256'], expected_binding=setup['lane']['call']['expected_binding'],
                memory_dir=setup['arguments']['memory_dir'])


def test_producer_receives_exact_writer_output(setup, pinned_content_candidate):
    raw = (setup['out'] / 'public-plan.v5.json').read_bytes()
    assert pinned_content_candidate['public_plan_bytes'] == raw
    assert pinned_content_candidate['expected_public_plan_sha256'] == digest(raw)
    config = json.loads(raw)['config']
    assert config['memory'] == pinned_content_candidate['memory_dir']
    assert config['descriptor'] == str(Path(config['memory']) / 'descriptor.json')


@pytest.mark.parametrize('field', ['memory', 'descriptor'])
def test_producer_refuses_mismatched_config_paths(pinned_content_candidate, field):
    candidate = dict(pinned_content_candidate)
    plan = json.loads(candidate['public_plan_bytes'])
    plan['config'][field] += '.wrong'
    candidate['public_plan_bytes'] = canonical(plan)
    candidate['expected_public_plan_sha256'] = digest(candidate['public_plan_bytes'])
    with pytest.raises(producer.BindingProducerRefused):
        producer.build_content_lane_binding(**candidate)


@pytest.mark.parametrize('agent', LANES)
def test_synthetic_config_producer_consumer_binding_for_each_declared_lane(tmp_path, pinned_content_candidate, agent):
    # This exercises real config parsing and binding producer/consumer; no Job or authentication is claimed.
    candidate = pinned_content_candidate
    output = producer.build_content_lane_binding(**candidate)
    binding_file = tmp_path / 'content-binding.json'
    binding_file.write_bytes(output['binding_bytes'])
    descriptor_args = descriptor_kwargs(tmp_path)
    descriptor_args.update(fleet_bytes=candidate['fleet_bytes'],
        expected_fleet_sha256=candidate['expected_fleet_sha256'],
        expected_binding=candidate['expected_binding'], binding_path=str(binding_file),
        binding_sha256=output['binding_sha256'])
    descriptor = descriptor_producer.build_shared_descriptor(enabled=True, **descriptor_args)
    result = validate(tmp_path, descriptor['descriptor_bytes'], descriptor_args, lane=agent,
                      descriptor_pin=descriptor['descriptor_sha256'])
    assert result['ok'] and result['value']['expected_binding'] == candidate['expected_binding']
    assert result['value']['worker']['binding-sha256'] == output['binding_sha256']
    resolved = binding.resolve_lane_binding(candidate['fleet_bytes'], candidate['expected_fleet_sha256'],
        output['binding_bytes'], output['binding_sha256'], lane=agent, enabled=True)
    assert resolved['lane'] == agent and resolved['expected_binding'] == candidate['expected_binding']


@pytest.mark.parametrize('pin', PINS)
def test_synthetic_producer_rejects_each_independently_tampered_pin(pinned_content_candidate, pin):
    candidate = dict(pinned_content_candidate)
    candidate['expected_binding'] = dict(candidate['expected_binding'])
    old = candidate['expected_binding'][pin]
    candidate['expected_binding'][pin] = ('0' if old[0] != '0' else '1') + old[1:]
    with pytest.raises(producer.BindingProducerRefused):
        producer.build_content_lane_binding(**candidate)


@pytest.mark.parametrize('source_key', ['message_generation_path', 'message_source_path', 'doc_policy_path'])
def test_synthetic_producer_refuses_changed_message_or_document_source(tmp_path, pinned_content_candidate, source_key):
    # Mutation is restricted to the synthetic per-test tmp_path built by the imported fixture.
    candidate = pinned_content_candidate
    assert producer.build_content_lane_binding(**candidate)['binding_bytes']
    plan = json.loads(candidate['public_plan_bytes'])
    source = Path(plan[source_key])
    assert source.resolve().is_relative_to(tmp_path.resolve())
    original = source.read_bytes()
    assert original
    source.write_bytes(bytes([original[0] ^ 1]) + original[1:])
    with pytest.raises(producer.BindingProducerRefused):
        producer.build_content_lane_binding(**candidate)


def test_synthetic_producer_never_invokes_git_from_rehashed_untrusted_plan(
        tmp_path, pinned_content_candidate, monkeypatch):
    # The fake executable is a byte-identical copy of the synthetic fixture's Git;
    # the intercept prevents it from being executed even on a vulnerable build.
    candidate = dict(pinned_content_candidate)
    assert producer.build_content_lane_binding(**candidate)['binding_bytes']
    plan = json.loads(candidate['public_plan_bytes'])
    rogue = tmp_path / 'untrusted-git.exe'
    shutil.copyfile(plan['doc_git_executable'], rogue)
    assert digest(rogue.read_bytes()) == plan['doc_git_sha256']
    plan['doc_git_executable'] = str(rogue)
    candidate['public_plan_bytes'] = canonical(plan)
    candidate['expected_public_plan_sha256'] = digest(candidate['public_plan_bytes'])
    original_run = subprocess.run
    attempts = []

    def observe_run(args, *a, **kw):
        if isinstance(args, (tuple, list)) and args and os.path.normcase(str(args[0])) == os.path.normcase(str(rogue)):
            attempts.append(True)
            raise OSError('synthetic-untrusted-execution-prevented')
        return original_run(args, *a, **kw)

    monkeypatch.setattr(subprocess, 'run', observe_run)
    with pytest.raises(producer.BindingProducerRefused):
        producer.build_content_lane_binding(**candidate)
    assert not attempts, 'untrusted executable was reached before fleet trust validation'


@pytest.mark.skipif(os.name != 'nt' or os.environ.get('WD_Q4_MESSAGE_JOB_EXPERIMENT') != '1',
                    reason='opt-in synthetic Windows Job experiment')
def test_experimental_message_only_real_nested_job(tmp_path):
    """Real outer Job and wrapper; synthetic message source, encoder and model launcher.

    This experiment uses the running checkout and explicitly configured local Python/site.
    It does not exercise launcher/config, five real lanes, Q1 or deployed production.
    """
    import dataclasses
    import importlib.util
    import sys

    from sandbox.mind.step2 import runtime_builder
    from sandbox.mind.step2 import test_content_memory as tcm
    from sandbox.mind.step2 import test_content_route as route
    from tests.tools import test_wd_q4_content_query as content_query
    from tests.tools.test_wd_q4_owner_plumbing import _synthetic_guard
    from tools import wd_q4_fixed_plan_producer as plan_producer
    from tools import wd_q4_secure_transport as fixed
    from tools.wd_q4_public_query import _encoder_digest

    root = Path(os.environ['WD_Q4_EXPERIMENT_CODE_ROOT']).resolve(strict=True)
    assert root.drive.upper() == 'C:' and (root / '.git').exists()
    site = Path(os.environ['WD_Q4_TEST_SITE']).resolve(strict=True)
    python = Path(os.environ['WD_Q4_TEST_PYTHON']).resolve(strict=True)
    assert python == Path(sys.executable).resolve(strict=True)
    assert digest(python.read_bytes()) == os.environ['WD_Q4_TEST_PYTHON_SHA256']

    path, generation, cursor, prefix_sha = route.write_prefix(tmp_path / 'prefix', route.prefix_rows())
    message_policy, message_pin = route.message_policy()
    decision, decision_sha = route.decision_of(prefix={'sha256': prefix_sha, 'offset': cursor.offset,
        'generation': cursor.generation}, message_pin=message_pin, doc_pin=None)
    message = {'source_path': str(path), 'generation_path': str(generation), 'end_cursor': cursor,
               'policy_bytes': message_policy, 'policy_sha256': message_pin}
    built, directory = route.build_route(tmp_path, {'messages': message, 'documents': None,
                                      'decision': decision, 'decision_sha': decision_sha})
    strict = route.strict_pins()
    data = {'policy': strict['policy_bytes'], 'roster': strict['roster_bytes'], 'content_decision': decision,
            'memory': (directory / 'memory.json').read_bytes(), 'projection': (directory / 'projection.json').read_bytes(),
            'descriptor': (directory / 'descriptor.json').read_bytes()}

    runtime = tmp_path / 'runtime'; code = runtime / 'assets' / 'code'; code.mkdir(parents=True)
    for relative in ('tools', 'waggledance', 'sandbox/mind', 'core'):
        source = root / relative
        if not source.is_dir():
            continue
        for original in source.rglob('*.py'):
            assert not original.is_symlink()
            dest = code / original.relative_to(root); dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(original, dest)
    runtime_site = runtime / 'site'; runtime_site.mkdir()
    for name in ('numpy', 'numpy.libs', 'faiss', 'faiss_cpu.libs', 'packaging'):
        original = site / name
        assert original.is_dir() and not original.is_symlink()
        for parent, dirs, files in os.walk(original, followlinks=False):
            for item in dirs + files:
                child = Path(parent) / item
                assert not child.is_symlink() and not (getattr(child.lstat(), 'st_file_attributes', 0) & 0x400)
        shutil.copytree(original, runtime_site / name, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    model = runtime / 'model'; model.mkdir(); (model / 'synthetic-model.txt').write_bytes(b'not E5')
    runtime_manifest = tmp_path / 'runtime-manifest.json'
    generated = runtime_builder.build_runtime_manifest(runtime, runtime_manifest, generation='f' * 40, revision='a' * 40)
    wrapper_spec = importlib.util.spec_from_file_location('message_job_wrapper_fixture',
        root / 'tests/tools/test_wd_q4_agent_wrapper.py')
    wrapper_module = importlib.util.module_from_spec(wrapper_spec); wrapper_spec.loader.exec_module(wrapper_module)
    bundle_dir = tmp_path / 'bundle-fixture'; bundle_dir.mkdir()
    bundle = wrapper_module.bundle.__wrapped__(bundle_dir)
    package = json.loads((bundle['root'] / 'bridge-code-files.json').read_bytes())
    # This is the one deliberate model/inner-launcher shim. Source snapshot and
    # validate_content_snapshot stay real and are not monkeypatched.
    shim = '''\
import datetime,hashlib,json,sys
sys.path.insert(0,SITE);sys.path.insert(1,ROOT)
import numpy as np
from sandbox.mind.step2 import runtime_launcher,wd_q4_private_read as reader
from tools import wd_q4_agent_query as q4,wd_q4_public_child as child
class Encoder:
 def query(self,text):
  v=[0.0]*64
  for token in text.lower().split():
   token=token.strip('.,:;!?()[]#*')
   if len(token)>1:v[int(hashlib.sha256(token.encode('utf-8')).hexdigest(),16)%64]+=1
  n=sum(x*x for x in v)**0.5 or 1.0
  return np.array([x/n for x in v],dtype=np.float32)
reader.ENCODER_FACTORY=lambda **kw:Encoder()
reader.bind_runtime_encoder=lambda *a,**kw:None
q4._utc_now=lambda:datetime.datetime(2026,11,8,1,tzinfo=datetime.timezone.utc)
def launcher(*,query,**kw):
 q=json.loads(query)
 r=reader._private_read_locked(DIRECTORY,DESCRIPTOR,DIGEST,q['situation'],q['time'],r'C:\\fake\\synthetic','a'*40)
 return runtime_launcher._validate_output(json.dumps(r).encode(),0,DIGEST)
q4._invoke_launcher=launcher
def bootstrap_public_imports(**kw):return {'status':'MODULES_IMPORTED','child':child}
'''
    constants = {'SITE': str(site), 'ROOT': str(root), 'DIRECTORY': str(directory),
                 'DESCRIPTOR': str(directory / 'descriptor.json'), 'DIGEST': built['descriptor_sha256']}
    shim = '\n'.join(k + '=' + repr(v) for k, v in constants.items()) + '\n' + shim
    source_files = {
        'tools/__init__.py': ('__path__.append(' + repr(str(root / 'tools')) + ')\n').encode(),
        'tools/wd_q4_public_outer.py': shim.encode(),
        'tools/wd_q4_public_child.py': (root / 'tools/wd_q4_public_child.py').read_bytes(),
        'tools/wd_q4_public_query.py': (root / 'tools/wd_q4_public_query.py').read_bytes(),
        'tools/wd_q4_agent_query.py': (root / 'tools/wd_q4_agent_query.py').read_bytes(),
        'tools/wd_q4_secure_transport.py': (root / 'tools/wd_q4_secure_transport.py').read_bytes(),
        'tools/wd_q4_content_fixed_backend.py': (root / 'tools/wd_q4_content_fixed_backend.py').read_bytes(),
        'sandbox/mind/step2/runtime_manifest.py': (root / 'sandbox/mind/step2/runtime_manifest.py').read_bytes(),
    }
    for relative, raw in source_files.items():
        bundle['put']('tools-bootstrap/' + relative, raw)
        if relative not in package['python_files']:
            package['python_files'].append(relative)
    bundle['put']('bridge-code-files.json', json.dumps(package).encode())
    bundle['config']['code_manifest_sha256'] = bundle['files']['bridge-code-files.json']
    bundle['config']['python_path'] = str(python); bundle['config']['python_sha256'] = digest(python.read_bytes())
    bundle['put']('wd-fleet.json', strict['roster_bytes'])
    manifest = {'schema_version': 1, 'source_commit': wrapper_module.COMMIT, 'files': bundle['files'],
        'bridge_code_package': {'python_executable': str(python), 'python_executable_sha256': digest(python.read_bytes())}}
    manifest_raw = json.dumps(manifest).encode(); (bundle['root'] / 'deployment-manifest.json').write_bytes(manifest_raw)
    manifest_sha = digest(manifest_raw); code_root = bundle['root'] / 'tools-bootstrap'
    scratch = tmp_path / 'scratch'; scratch.mkdir(); lock = tmp_path / 'read.lock'
    config = dataclasses.asdict(content_query.config(directory, built['descriptor_sha256']))
    config.pop('interpreter_identity')
    config.update(interpreter=str(python), interpreter_sha256=digest(python.read_bytes()),
        bootstrap=str(code_root / 'sandbox/mind/step2/runtime_bootstrap.py'),
        bootstrap_sha256=bundle['files']['tools-bootstrap/sandbox/mind/step2/runtime_bootstrap.py'],
        verifier_sha256=digest((code_root / 'sandbox/mind/step2/runtime_manifest.py').read_bytes()),
        runtime_root=str(runtime), runtime_manifest=str(runtime_manifest), runtime_manifest_sha256=generated['manifest_sha256'],
        runtime_generation='f' * 40, runtime_revision='a' * 40, lock_file=str(lock), timeout=30, scratch_root=str(scratch))
    public = {'schema': 'wd.q4-public-wrapper-plan.v5', 'config': config,
        'encoder_sha256': _encoder_digest(json.loads(data['descriptor'])['encoder']),
        'memory_sha256': digest(data['memory']), 'projection_sha256': digest(data['projection']),
        'message_source_path': str(path), 'message_generation_path': str(generation),
        'doc_repo_root': None, 'doc_policy_path': None, 'doc_git_executable': None, 'doc_git_sha256': None}
    for name in ('policy', 'roster', 'content_decision'):
        file = tmp_path / (name + '.json'); file.write_bytes(data[name])
        public[name + '_path'] = str(file); public[name + '_sha256'] = digest(data[name])
    message_file = tmp_path / 'message-policy.json'; message_file.write_bytes(message_policy)
    public['message_policy_path'] = str(message_file)
    public_path = tmp_path / 'public-plan.v5.json'; public_path.write_bytes(fixed._canonical(public))
    content_bundle = fixed.fixed_content_bundle(enabled=True, **data)
    host = Path(os.environ['SYSTEMROOT']) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    peer = code_root / 'tools/wd_q4_secure_transport.py'
    plan = plan_producer.build_content_fixed_plan(enabled=True, interpreter_sha256=digest(python.read_bytes()),
        peer_code_sha256=digest(peer.read_bytes()), verifier_sha256=config['verifier_sha256'],
        runtime_manifest_sha256=generated['manifest_sha256'], bundle_sha256=digest(content_bundle),
        encoder_sha256=public['encoder_sha256'], policy_sha256=public['policy_sha256'],
        roster_sha256=public['roster_sha256'], content_decision_sha256=public['content_decision_sha256'],
        descriptor_sha256=built['descriptor_sha256'], memory_sha256=public['memory_sha256'],
        projection_sha256=public['projection_sha256'], runtime_generation='f' * 40, runtime_revision='a' * 40,
        runtime_root=str(runtime), runtime_manifest_path=str(runtime_manifest), bundle_bytes=len(content_bundle),
        approved_tasks=sorted(json.loads(data['policy'])['approved_tasks']), wrapper_sha256=digest((bundle['root'] / 'Invoke-WdQ4Query.ps1').read_bytes()),
        deployment_manifest_sha256=manifest_sha, public_plan_sha256=digest(public_path.read_bytes()),
        host_sha256=digest(host.read_bytes()), backend_sha256=digest((code_root / 'tools/wd_q4_content_fixed_backend.py').read_bytes()),
        bundle_root=str(bundle['root']), bundle_commit=wrapper_module.COMMIT,
        wrapper_path=str(bundle['root'] / 'Invoke-WdQ4Query.ps1'), public_plan_path=str(public_path))
    spec = importlib.util.spec_from_file_location('actual_message_fixed_parent', peer)
    actual = importlib.util.module_from_spec(spec); spec.loader.exec_module(actual)
    guard, marker = _synthetic_guard(tmp_path / 'job-guard', 'codex-lead-1'); context = guard._owner_context()
    query = json.dumps({'situation': content_query.SITUATION, 'time': tcm.ASK_TIME}).encode()
    phases = [('parent_pid', os.getpid())]
    # Do not wrap or replace transport functions: its exception scrubber
    # checks the identity of the active _run code object.
    observed_codes = {getattr(actual, name).__code__: name for name in
        ('_create_process_in_job', '_wait_job_empty', '_wait_child_exit')}
    def profile(frame, event, value):
        name = observed_codes.get(frame.f_code)
        if name and event == 'return':
            if name == '_create_process_in_job' and isinstance(value, tuple):
                phases.append(('child_pid', int(value[2])))
            elif name in ('_wait_job_empty', '_wait_child_exit') and type(value) is bool:
                phases.append((name, value))
    previous_profile = sys.getprofile()
    sys.setprofile(profile)
    try:
        result, exit_code = actual.run_fixed_peer(executable=str(python), expected_interpreter_sha256=digest(python.read_bytes()),
        interpreter_max_bytes=128*1024*1024, plan_bytes=plan['plan_bytes'], expected_plan_sha256=plan['plan_sha256'],
        bundle_bytes=content_bundle, runtime_manifest_bytes=runtime_manifest.read_bytes(), query_bytes=query,
        cwd=str(scratch), timeout_seconds=180, max_output_bytes=32768, public_snapshot_enabled=True,
        _owner_guard=guard, _owner_context=context)
    except Exception as exc:
        pytest.fail('synthetic phase observation: ' + repr(phases) + '; result=' + type(exc).__name__)
    finally:
        sys.setprofile(previous_profile)
    answer = json.loads(result)
    assert exit_code == 0 and answer['status'] == 'ANSWER'
    assert answer['schema'] == 'wd.q4-content-snapshot-query.v1'
    context._require_done(('attestation', 'transport'))
    assert marker.is_file()

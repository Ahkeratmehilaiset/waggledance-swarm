"""Synthetic real producer, capture, descriptor, selector and guarded peer.

The encoder and peer runtime are test doubles; bridge, deployment and real data
are never used. Actual fence, snapshot capture, binding, caller guard and Windows
named-pipe peer are exercised. This is not authentication or a production test.
"""
from datetime import datetime, timezone
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from sandbox.mind.step2 import shared_index_checkpoint as cp
from sandbox.mind.step2 import shared_index_preflight as preflight
from sandbox.mind.step2 import shared_result_resolver as resolver
from tools import wd_q4_descriptor_materializer as materializer
from tools import wd_q4_lane_binding_producer as binding_producer
from tools import wd_q4_lane_caller_cli as caller
from tools import wd_q4_live_snapshot_publish as live
from tools import wd_q4_snapshot_candidate as snapshot_capture


def sha(value):
    return hashlib.sha256(value).hexdigest()


def enc(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('ascii')


def helper(name, path):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(path))
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


@pytest.mark.skipif(os.name != 'nt', reason='fenced producer and guarded peer need Windows')
@pytest.mark.parametrize('fault,envelope,lane', [
    ('none', False, 'codex-lead-1'), ('selector_changed', False, 'codex-lead-1'),
    ('index_changed', False, 'codex-lead-1'), ('none', True, 'codex-lead-1'),
    ('selector_changed', True, 'codex-lead-1'), ('index_changed', True, 'codex-lead-1'),
    ('none', True, 'codex-tools-1'), ('none', True, 'claude-rco-1'),
    ('none', True, 'claude-rco-2'), ('none', True, 'fable-5'),
])
def test_two_real_fenced_publications_are_selected_by_running_caller(tmp_path, monkeypatch, capsys, fault, envelope, lane):
    pytest.importorskip('faiss')
    status = helper('e2e_real_status_helpers', 'test_wd_q4_status_prefix_runner.py')
    fixed = helper('e2e_fixed_runtime_helpers', 'test_wd_q4_fixed_snapshot_transport.py')
    runner = status.runner.__wrapped__()
    original_case = status.case.__wrapped__(tmp_path, monkeypatch, runner)
    _, original_args, route, checkpoint, private, _ = original_case
    fleet_doc = json.loads(original_args()['fleet_bytes'])
    worktree = tmp_path / 'worktree'
    worktree.mkdir()
    runtime_root = tmp_path / 'lane-runtime'
    for entry in fleet_doc['lanes'] + [fleet_doc['tools_supervisor']]:
        entry['worktree'] = str(worktree)
        (runtime_root / 'shared' / '.codex-audit' / entry['agent'] / 'q4-caller-state').mkdir(parents=True)
    fleet_doc['runtime_root'] = str(runtime_root)
    fleet = enc(fleet_doc)
    monkeypatch.setattr(runner, '_FLEET_SHA', frozenset({sha(fleet)}))
    def args(*values):
        result = original_args(*values)
        result.update(fleet_bytes=fleet, expected_fleet_sha256=sha(fleet))
        return result
    wrapped_case = (runner, args, route, checkpoint, private, monkeypatch)
    bundle = tmp_path / 'installed-bundle'
    bundle.mkdir()
    wrapper = b'synthetic-wrapper-never-executed'
    (bundle / 'Invoke-WdQ4Query.ps1').write_bytes(wrapper)
    bridge_wrapper = bundle / 'Invoke-WdBridgePython.ps1'
    quote=lambda value: "'" + str(value).replace("'", "''") + "'"
    root=Path(__file__).resolve().parents[2]
    code="import runpy,sys;sys.path.insert(0,"+repr(str(root))+");sys.argv=['wd_q4_lane_caller_cli.py','--enabled'];runpy.run_module('tools.wd_q4_lane_caller_cli',run_name='__main__')"
    bridge_wrapper.write_text('& '+quote(sys.executable)+' -c '+quote(code)+'\n$global:LASTEXITCODE=$LASTEXITCODE\n', encoding='utf-8')
    (bundle / 'wd-fleet.json').write_bytes(fleet)
    manifest = enc({'schema_version': 1, 'source_commit': 'a' * 40,
                    'files': {'wd-fleet.json': sha(fleet), 'Invoke-WdQ4Query.ps1': sha(wrapper),
                              'Invoke-WdBridgePython.ps1':sha(bridge_wrapper.read_bytes())}})
    (bundle / 'deployment-manifest.json').write_bytes(manifest)
    generations = tmp_path / 'shared-generations'
    generations.mkdir()
    monkeypatch.chdir(worktree)
    uuid = next(e['agent_uuid'] for e in fleet_doc['lanes'] + [fleet_doc['tools_supervisor']] if e['agent'] == lane)
    for key, value in {'AGENT_BRIDGE_AGENT': lane, 'AGENT_BRIDGE_AGENT_UUID': uuid,
                       'AGENT_BRIDGE_RUN_ID': 'synthetic-live-run', 'AGENT_BRIDGE_OWNER_TOKEN': 'synthetic-token',
                       'WD_REBOOT_EXPECTED_MANIFEST_HASH': sha(manifest), 'WD_Q4_LIVE_MODE': '1'}.items():
        monkeypatch.setenv(key, value)
    if envelope:
        monkeypatch.setenv('WD_Q4_WITH_COVERAGE', '1')
    else:
        monkeypatch.delenv('WD_Q4_WITH_COVERAGE', raising=False)
    runtime = baseline = None
    answers = []

    def capture(published, kw):
        for key in ('policy_bytes', 'roster_bytes', 'public_policy_bytes'):
            (checkpoint / (key + '.json')).write_bytes(kw[key])
        pointer_raw = (checkpoint / cp.POINTER).read_bytes()
        pointer = json.loads(pointer_raw)
        assert sha(pointer_raw) == published['pointer_sha256']
        generation = pointer['generation']
        def spec(path):
            rel = path.relative_to(checkpoint)
            return {'path': str(rel), 'sha256': sha(path.read_bytes())}
        active = {name: spec(checkpoint / generation / name) for name in cp.ARTIFACTS.values()}
        history = {}
        for path in checkpoint.iterdir():
            if path.is_file() and (path.name.startswith('q4-pointer-') or path.name.startswith('q4-delta-')):
                history[path.name] = spec(path)
            if path.is_dir() and path.name != generation:
                for name in cp.ARTIFACTS.values():
                    item = path / name
                    if item.is_file():
                        history[f'{path.name}/{name}'] = spec(item)
        memory = json.loads((checkpoint / generation / 'memory.json').read_bytes())
        cells = {}
        for cell, cell_info in memory['cells'].items():
            root = checkpoint / generation / 'cells' / cell
            version = root / f"gen-{cell_info['generation']:06d}"
            paths = {'index': version / 'faiss' / f'cell_{cell}' / 'index.faiss',
                     'meta': version / 'faiss' / f'cell_{cell}' / 'meta.json',
                     'vectors': version / 'vectors.f32', 'provenance': version / 'provenance.jsonl',
                     'receipt': version / 'receipt.json'}
            cells[cell] = {'pointer': spec(root / 'current.json'), 'log': spec(root / 'LOG.jsonl'),
                           'artifacts': {k: spec(v) for k, v in paths.items()}}
        descriptors = {'pointer_bytes': spec(checkpoint / cp.POINTER),
            **{key: spec(checkpoint / (key + '.json')) for key in ('policy_bytes', 'roster_bytes', 'public_policy_bytes')},
            'active_artifact_bytes': active, 'history_bytes': history, 'cell_snapshots': cells}
        captured = snapshot_capture.capture(root=checkpoint, descriptors=descriptors, enabled=True)
        assert captured['status'] == 'CANDIDATE_UNVERIFIED'
        active_bytes = {name: (checkpoint / generation / name).read_bytes() for name in cp.ARTIFACTS.values()}
        return captured['bundle_bytes'], pointer, active_bytes

    def resolve_first(pointer, active, kw):
        records = json.loads(active['projection.json'])['records']
        hits = [{'ref_sha256': sha(r['provenance']['event_ref'].encode()),
                 'doc_id_sha256': sha(r['record_id'].encode()), 'ref_kind': r['provenance']['ref_kind'],
                 'canonical_event': False} for r in records]
        result = resolver.resolve_results(memory_manifest=active['memory.json'],
            expected_memory_sha256=pointer['artifacts']['memory_sha256'], projection_bytes=active['projection.json'],
            results=hits, query_utc='2026-11-01T12:00:00Z', approved_tasks=frozenset({status.TASK}),
            policy_bytes=kw['policy_bytes'], policy_sha256=route['policy_sha256'],
            roster_bytes=kw['roster_bytes'], roster_sha256=route['roster_sha256'])
        assert result
        return result

    def create_descriptor(gen, snapshot, pointer, active, kw):
        paths = materializer.plan_generation_paths(str(generations), gen)
        index_descriptor = json.loads(active['descriptor.json'])
        pins = {'expected_pointer_sha256': sha(enc(pointer)), 'expected_source_sha256': pointer['anchor_sha256'],
                'expected_encoder_sha256': preflight._encoder_digest(index_descriptor['encoder']),
                'policy_sha256': route['policy_sha256'], 'roster_sha256': route['roster_sha256'],
                'expected_public_policy_sha256': route['public_policy_sha256']}
        public = enc({'schema': 'wd.q4-public-wrapper-plan.v1', 'root': str(checkpoint),
            'policy_path': str(checkpoint / 'policy_bytes.json'), 'roster_path': str(checkpoint / 'roster_bytes.json'),
            'public_policy_path': str(checkpoint / 'public_policy_bytes.json'),
            'config': {'descriptor_sha256': pointer['artifacts']['descriptor_sha256']},
            'pointer_sha256': pins['expected_pointer_sha256'], 'source_anchor_sha256': pins['expected_source_sha256'],
            'encoder_sha256': pins['expected_encoder_sha256'], 'policy_sha256': pins['policy_sha256'],
            'roster_sha256': pins['roster_sha256'], 'public_policy_sha256': pins['expected_public_policy_sha256']})
        binding = binding_producer.build_lane_binding(enabled=True, expected_commit='a' * 40,
            manifest_bytes=manifest, expected_manifest_sha256=sha(manifest), fleet_bytes=fleet,
            expected_fleet_sha256=sha(fleet), wrapper_bytes=wrapper, expected_wrapper_sha256=sha(wrapper),
            public_plan_bytes=public, expected_public_plan_sha256=sha(public), snapshot_bytes=snapshot,
            expected_snapshot_sha256=sha(snapshot))
        fixed_plan = fixed.public_plan(*runtime, snapshot, pins=pins, tasks=[status.TASK])
        descriptor = {'schema': 'wd.q4-shared-session-config.v2', 'fleet_sha256': sha(fleet),
            'lanes': list(status.LANES), 'bundle_root': str(bundle), 'public_plan': str(Path(paths['generation_dir']) / 'public-plan.json'),
            'interpreter': sys.executable, 'cwd': str(worktree), 'binding_path': paths['binding_path'],
            'fixed_plan_path': paths['fixed_plan_path'], 'snapshot_path': paths['snapshot_path'],
            'runtime_manifest_path': str(runtime[1]), 'interpreter_sha256': fixed.interpreter_sha(),
            'binding_sha256': binding['binding_sha256'], 'fixed_plan_sha256': sha(fixed_plan),
            'snapshot_sha256': sha(snapshot), 'runtime_manifest_sha256': runtime[2],
            'interpreter_max_bytes': 64 * 1024 * 1024, 'timeout_seconds': 60, 'max_output_bytes': 65536,
            'outer': {}, 'expected_binding': json.loads(binding['binding_bytes'])['binding']}
        raw = enc(descriptor)
        materializer.materialize_descriptor(enabled=True, target_dir=str(generations), generation=gen,
            artifacts={'binding': binding['binding_bytes'], 'fixed_plan': fixed_plan, 'snapshot': snapshot},
            descriptor_bytes=raw, expected_descriptor_sha256=sha(raw))
        (Path(paths['generation_dir']) / 'public-plan.json').write_bytes(public)
        return paths['descriptor_path'], sha(raw)

    def produce_then_query(**kw):
        nonlocal runtime, baseline
        result = real_run(**kw)
        snapshot, pointer, active = capture(result, kw)
        if runtime is None:
            runtime = fixed.synthetic_runtime(tmp_path, fixed.make_stub(resolve_first(pointer, active, kw)))
            baseline = create_descriptor('d' * 40, snapshot, pointer, active, kw)
            monkeypatch.setenv('WD_Q4_SHARED_DESCRIPTOR_PATH', baseline[0])
            monkeypatch.setenv('WD_Q4_SHARED_DESCRIPTOR_SHA256', baseline[1])
        candidate = create_descriptor(result['pointer_sha256'], snapshot, pointer, active, kw)
        base_cfg = caller._configuration()
        candidate_cfg = caller._configuration(candidate[0], candidate[1])
        published = live.publish(enabled=True, baseline_path=baseline[0], baseline_sha256=baseline[1],
            baseline=base_cfg, manifest_sha256=sha(manifest), candidate_path=candidate[0],
            candidate_sha256=candidate[1], candidate=candidate_cfg)
        assert published['status'] == 'PUBLISHED'
        # The enabled CLI (and its PowerShell helper below) rank with the observed
        # local UTC and refuse a supplied time over 300 s away from it.
        now = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        query = enc({'situation': 'synthetic recall', 'time': now})
        monkeypatch.setattr(sys, 'stdin', io.TextIOWrapper(io.BytesIO(query), encoding='utf-8'))
        if fault != 'none' and len(answers) == 1:
            original_check = caller._require_current_snapshot
            count = 0
            selector_path = generations / 'q4-live-current.json'
            index_path = checkpoint / cp.POINTER
            selector_before, index_before = selector_path.read_bytes(), index_path.read_bytes()
            def race(*values):
                nonlocal count
                count += 1
                if count == 2 and fault == 'index_changed':
                    index_path.write_bytes(b'synthetic-new-index')
                value = original_check(*values)
                if count == 2 and fault == 'selector_changed':
                    selector_path.write_bytes(b'synthetic-new-selector')
                return value
            with monkeypatch.context() as scoped:
                scoped.setattr(caller, '_require_current_snapshot', race)
                assert caller.main(['--enabled']) == 2
            out = capsys.readouterr()
            assert not out.out and 'lane_caller_refused' in out.err
            assert count == 2
            assert not (runtime_root / 'shared' / '.codex-audit' / lane / 'q4-caller-state' /
                        (lane + '.pending.json')).exists()
            selector_path.write_bytes(selector_before)
            index_path.write_bytes(index_before)
            answers.append(None)
        else:
            assert caller.main(['--enabled']) == 0
            out = capsys.readouterr()
            assert not out.err
            answer = json.loads(out.out)
            if envelope:
                assert set(answer) == {'schema', 'coverage', 'freshness', 'result'}
                assert answer['schema'] == 'wd.q4-lane-coverage-envelope.v1'
                assert answer['coverage'] == answer['freshness'] == 'UNKNOWN'
                answer = answer['result']
            assert answer['status'] == 'CONTENT_WITHHELD' and answer['original_inner_status'] == 'ANSWER'
            assert answer['descriptor_sha256'] == candidate[1]
            assert answer['binding']['pointer_sha256'] == result['pointer_sha256']
            assert answer['trust']['freshness'] == 'UNKNOWN'
            records = json.loads(active['projection.json'])['records']
            assert answer['original_result_count'] > 0 and answer['original_result_refs']
            assert 'stored_text' not in json.dumps(answer)
            assert all(item['ref_sha256'] in {sha(r['provenance']['event_ref'].encode()) for r in records}
                       for item in answer['original_result_refs'])
            if envelope:
                host=shutil.which('powershell.exe'); assert host
                env=dict(os.environ)
                env.update(WD_BRIDGE_BUNDLE_ROOT=str(bundle),WD_BRIDGE_PYTHON_WRAPPER=str(bridge_wrapper))
                helper=root/'ops/windows/reboot/Invoke-WdQ4LaneRecall.ps1'
                call=subprocess.run([host,'-NoProfile','-NonInteractive','-File',str(helper),'-Live','-WithCoverage'],
                                    input=query,capture_output=True,timeout=120,cwd=worktree,env=env)
                assert call.returncode==0,call.stderr.decode(errors='replace')
                outer=json.loads(call.stdout)
                assert outer['schema']=='wd.q4-lane-coverage-envelope.v1'
                assert outer['coverage']==outer['freshness']=='UNKNOWN'
                assert outer['result']['binding']['pointer_sha256']==result['pointer_sha256']
            answers.append(answer)
        return result

    real_run = runner.run_once
    monkeypatch.setattr(runner, 'run_once', produce_then_query)
    status.test_real_fenced_child_two_renewable_updates(wrapped_case, monkeypatch, fault='none')
    assert len(answers) == 2
    if fault == 'none':
        assert answers[0]['descriptor_sha256'] != answers[1]['descriptor_sha256']
        assert answers[0]['binding']['pointer_sha256'] != answers[1]['binding']['pointer_sha256']
    else:
        assert answers[1] is None

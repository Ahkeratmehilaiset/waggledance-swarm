"""Synthetic P1 relay -> real configuration/selector, with explicit test boundaries.

The bridge wrapper and runtime child are test doubles. They run the real caller
through its selector, but stop before the guarded adapter or an E5 model.
The fixed P1 root and placeholder interpreter read are injected for this fixture.
"""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from tests.tools.test_wd_q4_card_audit_orchestrator import (  # noqa: F401
    _chain, attested, materialize, real_fixture, staged_producer, tmp_path, write)
from tests.tools.test_wd_q4_lane_recall_helper import HELPER, SHELLS
from tests.tools.test_wd_q4_two_card_snapshot_refresh import _append_prefix

pytestmark = pytest.mark.skipif(os.name != 'nt' or not SHELLS, reason='Windows PowerShell required')
ROOT = Path(__file__).resolve().parents[2]


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def test_packaged_p1_relay_selects_changed_generation_and_checks_pins(attested, staged_producer, tmp_path, monkeypatch):
    pytest.importorskip('faiss')
    from tools import wd_q4_card_audit_orchestrator as orchestrator
    from tools import wd_q4_lane_caller_cli as caller
    from tools import wd_q4_live_snapshot_publish as live

    bundle = Path(attested['expected_bundle'])
    driver = tmp_path / 'synthetic-selector-child.py'
    driver.write_text('''\
import contextlib, io, json, os, pathlib, sys
sys.path.insert(0, os.environ['SYNTHETIC_REPO'])
from tools import wd_q4_lane_caller_cli as caller
from tools import wd_q4_live_snapshot_publish as live
assert os.environ.get('WD_Q4_LIVE_MODE') == '1'
assert os.environ.get('WD_Q4_LIVE_P1') == '1'
assert not os.environ.get('WD_Q4_WITH_COVERAGE')
raw = sys.stdin.buffer.read()
assert json.loads(raw)['situation'] == 'synthetic'
live._P1_ROOT = pathlib.Path(os.environ['SYNTHETIC_P1_ROOT'])
real_read = caller._read
interpreter = os.environ['SYNTHETIC_INTERPRETER']
caller._read = lambda p, limit, pin=None: b'' if str(p) == interpreter else real_read(p, limit, pin)
observed = []
real_select = live.select
def record_select(**kwargs):
    if os.environ.get('SYNTHETIC_SUPPRESS_P1') == '1':
        kwargs.pop('p1_enabled', None)  # explicit mutation: strict selector must refuse
    chosen = real_select(**kwargs)
    observed.append(chosen)
    return chosen
live.select = record_select
identity_boundary = []
def stop_before_guard(*args):
    identity_boundary.append(True)
    raise RuntimeError('synthetic stop before guard')
caller._identity = stop_before_guard
with contextlib.redirect_stderr(io.StringIO()):
    code = caller.main(['--enabled'])
assert code == 2 and len(observed) == 1 and identity_boundary == [True]
chosen = observed[0]
print(json.dumps({'descriptor_sha256':chosen['configuration'][2], 'public_plan':pathlib.Path(chosen['configuration'][0]['public_plan']).parent.name}))
''', encoding='utf-8')
    bridge_wrapper = bundle / 'Invoke-WdBridgePython.ps1'
    py = sys.executable.replace("'", "''")
    bridge_wrapper.write_text(
        "param([Parameter(Position=0)][string]$Tool,[Parameter(ValueFromRemainingArguments)][string[]]$Rest)\n"
        "if ($Tool -cne 'tools/wd_q4_lane_caller_runtime_bootstrap.py' -or @($Rest).Count -ne 1 -or $Rest[0] -cne '--enabled') { exit 9 }\n"
        "& '" + py + "' $env:SYNTHETIC_DRIVER\n$global:LASTEXITCODE=$LASTEXITCODE\n", encoding='utf-8')
    packaged = bundle / 'Invoke-WdQ4LaneRecall.ps1'
    packaged.write_bytes(HELPER.read_bytes())
    manifest_path = bundle / 'deployment-manifest.json'
    manifest = json.loads(manifest_path.read_bytes())
    manifest['files']['Invoke-WdBridgePython.ps1'] = sha(bridge_wrapper.read_bytes())
    manifest['files']['Invoke-WdQ4LaneRecall.ps1'] = sha(packaged.read_bytes())
    manifest_raw = json.dumps(manifest).encode()
    manifest_path.write_bytes(manifest_raw)
    manifest_pin = sha(manifest_raw)
    assert sha(packaged.read_bytes()) == manifest['files']['Invoke-WdQ4LaneRecall.ps1']
    attested['expected_manifest_sha256'] = manifest_pin.upper()
    fixed = json.loads(attested['fixed_plan_bytes'])
    fixed['deployment_manifest_sha256'] = manifest_pin
    attested['fixed_plan_bytes'] = json.dumps(fixed, sort_keys=True, separators=(',', ':')).encode()
    attested['expected_fixed_plan_sha256'] = sha(attested['fixed_plan_bytes'])
    shared = tmp_path / 'live' / 'generations'
    shared.mkdir(parents=True)
    monkeypatch.setattr(live, '_P1_ROOT', shared.parent)
    path, _, _ = _chain(attested, tmp_path, monkeypatch, lambda out: shared)
    request = json.loads(path.read_bytes())
    anchors = shared.parent / '.codex-audit' / 'anchors'; anchors.mkdir(parents=True)
    decision = json.loads(Path(request['decision']['path']).read_bytes())
    first_anchor = anchors / 'first'; shutil.copytree(decision['anchor']['root'], first_anchor)
    decision['anchor']['root'] = str(first_anchor)
    first_request = tmp_path / 'decision-first.json'
    request['decision'] = {'path': str(first_request), 'sha256': write(first_request, decision)}
    finish = json.loads(Path(request['finish']['path']).read_bytes())
    def build(gen, name, override=None):
        changed = dict(request)
        template = tmp_path / (name + '-finish.json')
        changed['finish'] = {'path': str(template), 'sha256': write(template, dict(finish,
            composer_options=dict(finish['composer_options'], generation=gen)))}
        if override is not None: changed['decision'] = override
        out = shared.parent / (name + '-output'); out.mkdir()
        changed['output_root'] = str(out)
        req = tmp_path / (name + '-request.json')
        assert orchestrator.run(enabled=True, request_path=str(req), request_sha256=write(req, changed))['status'] == 'PREPARED'
    b, c, d = 'b' * 40, 'c' * 40, 'd' * 40
    build(b, 'baseline')
    build(c, 'first')
    build(d, 'second', _append_prefix(request, anchors))
    monkeypatch.setenv('WD_REBOOT_EXPECTED_MANIFEST_HASH', manifest_pin)
    real_read = caller._read
    interpreter = attested['descriptor_options']['interpreter']
    monkeypatch.setattr(caller, '_read', lambda p, limit, pin=None: b'' if str(p) == interpreter else real_read(p, limit, pin))
    def descriptor(gen):
        p = shared / gen / 'shared-session-config.v2.json'
        return str(p), sha(p.read_bytes())
    base_path, base_pin = descriptor(b)
    baseline = caller._configuration(base_path, base_pin)
    def publish(gen):
        p, pin = descriptor(gen)
        current = shared / 'q4-live-current.json'
        previous = sha(current.read_bytes()) if current.exists() else None
        assert live.publish(enabled=True, baseline_path=base_path, baseline_sha256=base_pin, baseline=baseline,
            manifest_sha256=manifest_pin, p1_enabled=True, candidate_path=p, candidate_sha256=pin,
            expected_previous_selector_sha256=previous,
            candidate=caller._configuration(p, pin))['status'] == 'PUBLISHED'
    env = {k: v for k, v in os.environ.items() if not k.startswith(('WD_BRIDGE_', 'WD_REBOOT_', 'AGENT_BRIDGE_', 'WD_Q4_'))}
    env.update(WD_BRIDGE_BUNDLE_ROOT=str(bundle), WD_BRIDGE_PYTHON_WRAPPER=str(bridge_wrapper),
        WD_REBOOT_EXPECTED_MANIFEST_HASH=manifest_pin, WD_Q4_SHARED_DESCRIPTOR_PATH=base_path,
        WD_Q4_SHARED_DESCRIPTOR_SHA256=base_pin, SYNTHETIC_REPO=str(ROOT), SYNTHETIC_DRIVER=str(driver),
        SYNTHETIC_P1_ROOT=str(shared.parent), SYNTHETIC_INTERPRETER=interpreter)
    raw = b'{"situation":"synthetic","time":"2026-11-06T00:00:00Z"}'
    def invoke():
        return subprocess.run([SHELLS[0], '-NoProfile', '-NonInteractive', '-File', str(packaged),
            '-Runtime', '-Live', '-LiveP1'], input=raw, capture_output=True, env=env, timeout=90)
    for gen in (c, d):
        publish(gen)
        env['SYNTHETIC_SUPPRESS_P1'] = '1'
        refused = invoke()
        assert refused.returncode != 0 and not refused.stdout
        env.pop('SYNTHETIC_SUPPRESS_P1')
        result = invoke()
        assert result.returncode == 0, result.stderr
        selected = json.loads(result.stdout)
        assert selected == {'descriptor_sha256': descriptor(gen)[1], 'public_plan': gen}
    env['WD_Q4_SHARED_DESCRIPTOR_SHA256'] = '0' * 64
    refused = invoke()
    assert refused.returncode != 0 and not refused.stdout
    env['WD_Q4_SHARED_DESCRIPTOR_SHA256'] = base_pin
    env['WD_REBOOT_EXPECTED_MANIFEST_HASH'] = '0' * 64
    refused = invoke()
    assert refused.returncode != 0 and not refused.stdout
    env['WD_REBOOT_EXPECTED_MANIFEST_HASH'] = manifest_pin
    candidate = Path(descriptor(d)[0])
    original = candidate.read_bytes()
    candidate.write_bytes(original + b' ')
    refused = invoke()
    assert refused.returncode != 0 and not refused.stdout
    candidate.write_bytes(original)


@pytest.mark.parametrize('selector_case', ['previous', 'publication', 'unrelated'])
def test_pending_fallback_checks_real_c2_selector_and_leaves_root_unchanged(
        attested, staged_producer, tmp_path, monkeypatch, capsys, selector_case):
    """Real P1/C2 generation and selector; identity/adapter/guard are synthetic only."""
    pytest.importorskip('faiss')
    from tools import wd_q4_card_audit_orchestrator as orchestrator
    from tools import wd_q4_guard_completion as guard_completion
    from tools import wd_q4_lane_caller_adapter as adapter
    from tools import wd_q4_lane_caller_cli as caller
    from tools import wd_q4_live_snapshot_publish as live

    shared = tmp_path / 'live' / 'generations'
    shared.mkdir(parents=True)
    monkeypatch.setattr(live, '_P1_ROOT', shared.parent)
    path, _, _ = _chain(attested, tmp_path, monkeypatch, lambda out: shared)
    request = json.loads(path.read_bytes())
    anchors = shared.parent / '.codex-audit' / 'anchors'
    anchors.mkdir(parents=True)
    decision = json.loads(Path(request['decision']['path']).read_bytes())
    first_anchor = anchors / 'first'
    shutil.copytree(decision['anchor']['root'], first_anchor)
    decision['anchor']['root'] = str(first_anchor)
    first_request = tmp_path / 'pending-decision-first.json'
    request['decision'] = {'path': str(first_request), 'sha256': write(first_request, decision)}
    finish = json.loads(Path(request['finish']['path']).read_bytes())

    def build(gen, name):
        changed = dict(request)
        template = tmp_path / (name + '-finish.json')
        changed['finish'] = {'path': str(template), 'sha256': write(template, dict(finish,
            composer_options=dict(finish['composer_options'], generation=gen)))}
        out = shared.parent / (name + '-output')
        out.mkdir()
        changed['output_root'] = str(out)
        req = tmp_path / (name + '-request.json')
        assert orchestrator.run(enabled=True, request_path=str(req),
                                request_sha256=write(req, changed))['status'] == 'PREPARED'

    base_gen, next_gen = 'b' * 40, 'c' * 40
    build(base_gen, 'pending-baseline')
    build(next_gen, 'pending-candidate')
    manifest_pin = attested['expected_manifest_sha256']
    monkeypatch.setenv('WD_REBOOT_EXPECTED_MANIFEST_HASH', manifest_pin)
    real_read = caller._read
    interpreter = attested['descriptor_options']['interpreter']
    monkeypatch.setattr(caller, '_read',
                        lambda p, limit, pin=None: b'' if str(p) == interpreter else real_read(p, limit, pin))
    base_path = shared / base_gen / 'shared-session-config.v2.json'
    next_path = shared / next_gen / 'shared-session-config.v2.json'
    base_pin, next_pin = sha(base_path.read_bytes()), sha(next_path.read_bytes())
    baseline = caller._configuration(str(base_path), base_pin)
    assert live.publish(enabled=True, baseline_path=str(base_path), baseline_sha256=base_pin,
        baseline=baseline, manifest_sha256=manifest_pin, p1_enabled=True, candidate_path=str(next_path),
        candidate_sha256=next_pin, expected_previous_selector_sha256=None,
        candidate=caller._configuration(str(next_path), next_pin))['status'] == 'PUBLISHED'
    selector_raw = (shared / live._POINTER).read_bytes()
    selector_sha = sha(selector_raw)
    unrelated = '0' * 64
    assert unrelated != selector_sha
    attempt = 'a' * 32
    previous = selector_sha if selector_case == 'previous' else unrelated
    publication = selector_sha if selector_case == 'publication' else '1' * 64
    marker = {'schema': 'wd.q4-live-publication-pending.v1', 'attempt': attempt,
              'baseline_sha256': base_pin, 'previous_selector_sha256': previous,
              'publication_sha256': publication, 'temporary_name': '.q4-live-current-' + attempt + '.tmp'}
    (shared / live._PUBLICATION_PENDING).write_bytes(caller._canon(marker))
    (shared / marker['temporary_name']).write_bytes(b'orphaned-synthetic-temp')
    monkeypatch.setenv('WD_Q4_SHARED_DESCRIPTOR_PATH', str(base_path))
    monkeypatch.setenv('WD_Q4_SHARED_DESCRIPTOR_SHA256', base_pin)
    monkeypatch.setenv('WD_Q4_LIVE_MODE', '1')
    monkeypatch.setenv('WD_Q4_LIVE_P1', '1')
    monkeypatch.delenv('WD_Q4_WITH_COVERAGE', raising=False)

    class Owner:
        def _handoff(self): pass
        def _finish(self): pass
    class Registry:
        def _issue(self): return object()
    class Guard:
        def __init__(self): self.owner, self._registry = Owner(), Registry()
        def _owner_context(self): return self.owner
        def _accept_owner_context(self, owner): return owner
        def release(self, witness): pass
        def mark_unknown(self): pass
    monkeypatch.setattr(guard_completion, 'acquire_guard', lambda **kwargs: Guard())
    monkeypatch.setattr(caller, '_identity', lambda *args: ('codex-lead-1', b'synthetic-identity', 'synthetic-token'))
    # The enabled CLI ranks with its observed local UTC; pin that seam to the supplied instant.
    now = datetime(2026, 11, 6, tzinfo=timezone.utc)
    monkeypatch.setattr(caller, '_query', lambda: ('synthetic', now))
    monkeypatch.setattr(caller, '_utc_now', lambda: now)
    calls = []
    def synthetic_adapter(**kwargs):
        calls.append(kwargs)
        descriptor = json.loads(kwargs['descriptor_bytes'])
        answer = {'schema': 'wd.q4-lane-answer.v1', 'lane': 'codex-lead-1', 'status': 'ABSTAIN',
                  'reason': '', 'result_count': 0, 'results': [], 'binding': descriptor['expected_binding'],
                  'trust': dict(caller._TRUST)}
        return {'schema': 'wd.q4-lane-caller-result.v1', 'status': 'VALIDATED', 'lane': 'codex-lead-1',
                'lane_binding': 'DECLARED_CONSISTENT', 'lane_authentication': 'UNKNOWN',
                'cleanup': 'chain_confirmed', 'inner_status': 'ABSTAIN', 'result_count': 0,
                'answer': answer, 'answer_sha256': sha(caller._canon(answer)),
                'identity_sha256': sha(kwargs['identity_bytes']),
                'descriptor_sha256': kwargs['expected_descriptor_sha256'], 'call_id': '2' * 32}
    monkeypatch.setattr(adapter, 'call_lane_session', synthetic_adapter)
    def tree():
        return {str(p.relative_to(shared)): (p.read_bytes() if p.is_file() else None)
                for p in sorted(shared.rglob('*'))}
    before = tree()
    code = caller.main(['--enabled'])
    captured = capsys.readouterr()
    assert tree() == before
    if selector_case == 'unrelated':
        assert code == 2 and captured.out == '' and not calls
    else:
        assert code == 0 and len(calls) == 1
        envelope = json.loads(captured.out)
        assert (envelope['schema'], envelope['selection'], envelope['publication_state'], envelope['freshness']) == (
            'wd.q4-lane-interrupted-envelope.v1', 'BASELINE_FALLBACK', 'INTERRUPTED', 'STALE')
        assert envelope['result']['descriptor_sha256'] == base_pin
        assert envelope['result']['descriptor_sha256'] != next_pin

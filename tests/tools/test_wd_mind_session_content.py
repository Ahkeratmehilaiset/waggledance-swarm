"""Synthetic contract for the default-off content session API.

No real memory, model, native provider, installed publication or live log is
opened. Existing independently constructed content fixtures exercise the real
source verifier through the session seam; smaller spies isolate dispatch rules.
This seam is an in-process API, not provider startup or live context delivery.
"""
from __future__ import annotations

import hashlib
import hmac
import asyncio
import builtins
import io
from concurrent.futures import ThreadPoolExecutor
import threading
import importlib.util
import json
import os
from pathlib import Path
import py_compile
import subprocess
import sys
import struct
import time
import traceback
from types import SimpleNamespace

import pytest

from tools import wd_bridge_mind_session as session
from tools import wd_mind_content_query_readonly as content


PROVIDER_KEYS = {"encoder_provider", "index_provider"}
INPUT_KEYS = content._Q4_KEYS - PROVIDER_KEYS
TOKEN = "mind_session_content_refused"


def _located_candidates(flat_consumer_case, monkeypatch):
    """Synthetic source rows are real; deployment/policy pin readers are fixtures."""
    from tools import wd_mind_full_runtime as runtime
    package, existing, proposals, snapshot, clock, _ = flat_consumer_case
    source_map = json.loads(existing._gate._raw_map)
    records = json.loads(snapshot['projection_bytes'])['messages']['records']
    candidates = []
    for item in proposals:
        record = next(row for row in records
            if content._sha(row['provenance']['event_ref'].encode()) == item['ref_sha256']
            and content._sha(row['record_id'].encode()) == item['doc_id_sha256'])
        entry = source_map['entries'][item['source_event_sha256']]
        candidates.append(dict(item, source_offset=entry['offset'], source_length=entry['length'],
                               chunk_index=record['provenance']['chunk_index']))
    controls = existing._controls
    fake_fleet = b'synthetic fleet bytes'
    controls = (controls[0], controls[1], content._sha(fake_fleet), controls[3], controls[4])
    monkeypatch.setattr(package, '_consumer_bundle_controls', lambda *_: controls)
    monkeypatch.setattr(session, '_pin_selected_resolver_code', lambda *_: None)
    monkeypatch.setattr(session, '_preflight_selected_resolver_code', lambda *_: None)
    monkeypatch.setattr(runtime, '_bundle_inputs', lambda *_: (fake_fleet, controls[3], controls[4]))
    monkeypatch.setattr(runtime, 'create_operator_pinned_policy_anchor', lambda **_: object())
    policy_bytes = snapshot['identifier_policy_binding'][0]
    resolver = session.create_selected_row_resolver(enabled=True, bundle_root=existing._root,
        expected_manifest_sha256=existing._pin, identifier_policy_bytes=policy_bytes,
        expected_identifier_policy_sha256=content._sha(policy_bytes),
        expected_approval_reference_sha256='c' * 64)
    frame = {'schema':'wd.mind-pipe-candidates.v2', 'nonce':'1'*64, 'lane':'codex-lead-1',
             'scope':'shared', 'status':'CANDIDATES', 'candidates':candidates}
    return resolver, frame, snapshot, clock


def test_selected_row_resolver_reads_and_rescreens_synthetic_local_source(flat_consumer_case, monkeypatch):
    resolver, frame, _, _ = _located_candidates(flat_consumer_case, monkeypatch)
    started = time.monotonic()
    result = resolver.resolve(ipc_bytes=content._canonical(frame), expected_nonce='1'*64,
                              lane='codex-lead-1', scope='shared', deadline_monotonic=time.monotonic()+60)
    print(f'synthetic_selected_query_seconds={time.monotonic() - started:.6f}')
    assert result['authority'] == 'none' and result['sender_authentication'] == 'UNKNOWN'
    assert result['current_status_freshness'] == 'UNKNOWN'
    assert [row['source_event_sha256'] for row in result['results']] == [
        item['source_event_sha256'] for item in frame['candidates']]
    assert all(type(row['event_age_seconds_upper_bound']) is int and row['event_time_utc_floor']
               for row in result['results'])


def test_selected_row_resolver_selected_gate_uses_retained_prefix_verifier(flat_consumer_case, monkeypatch):
    from datetime import timezone
    from tools import wd_mind_retained_query as retained
    resolver, frame, _, clock = _located_candidates(flat_consumer_case, monkeypatch)
    resolver._check_controls()
    context = resolver._context()
    retained._source_check(context)
    rows = resolver._selected(context, frame['candidates'], 'codex-lead-1', 'shared', clock.now(timezone.utc))
    assert rows


@pytest.mark.parametrize('fault', ['none', 'uppercase', 'origin', 'code', 'missing_pin', 'manifest'])
def test_selected_row_resolver_requires_manifest_pinned_prefix_code(tmp_path, monkeypatch, fault):
    from tools import wd_mind_retained_query as retained
    bundle = tmp_path / 'bundle'
    path = bundle / 'tools-bootstrap' / 'tools' / 'wd_mind_retained_query.py'
    path.parent.mkdir(parents=True)
    code = Path(retained.__file__).read_bytes()
    path.write_bytes(code)
    relative = 'tools-bootstrap/tools/wd_mind_retained_query.py'
    code_pin = content._sha(code)
    if fault == 'uppercase':
        code_pin = code_pin.upper()
    manifest = {'schema_version':1, 'files':{} if fault == 'missing_pin' else {relative:code_pin}}
    raw = json.dumps(manifest, separators=(',', ':')).encode()
    (bundle / 'deployment-manifest.json').write_bytes(raw)
    pin = content._sha(raw)
    if fault == 'manifest': pin = '0'*64
    if fault == 'code': path.write_bytes(code + b'\n# changed\n')
    monkeypatch.setattr(retained, '__file__', str(bundle / 'other.py' if fault == 'origin' else path))
    if fault in ('none', 'uppercase'):
        session._pin_selected_resolver_code(str(bundle), pin)
    else:
        with pytest.raises(Exception):
            session._pin_selected_resolver_code(str(bundle), pin)


@pytest.mark.parametrize('relative', [
    'tools-bootstrap/tools/__init__.py',
    'tools-bootstrap/waggledance/__init__.py',
    'tools-bootstrap/waggledance/core/__init__.py',
    *(f'tools-bootstrap/tools/{name}.py' for name in (
        'wd_mind_content_query_readonly', 'wd_mind_content_snapshot_package',
        'wd_mind_full_runtime', 'wd_mind_retained_query', 'wd_mind_runtime_process',
        'wd_mind_absent_generation_prefix_readonly', 'wd_mind_startup_content',
        'bridge_compact_view', 'wd_bridge_mind_lane_read', 'wd_bridge_mind')),
    'tools-bootstrap/waggledance/core/bridge_log_reader.py',
])
def test_selected_preflight_accepts_deploy_style_uppercase_module_pins(tmp_path, relative):
    path = tmp_path.joinpath(*relative.split('/'))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b'synthetic dependency')
    files = {relative: hashlib.sha256(path.read_bytes()).hexdigest().upper()}
    session._selected_pin_module(tmp_path, files, 'synthetic_unloaded_module', relative)
    path.write_bytes(b'changed synthetic dependency')
    with pytest.raises(ValueError):
        session._selected_pin_module(tmp_path, files, 'synthetic_unloaded_module', relative)


@pytest.mark.parametrize('tampered', [False, True])
def test_selected_preflight_loads_missing_module_with_uppercase_manifest(tmp_path, monkeypatch, tampered):
    from types import ModuleType
    root = tmp_path / 'bundle'
    modules = ('wd_mind_content_query_readonly', 'wd_mind_content_snapshot_package',
               'wd_mind_full_runtime', 'wd_mind_retained_query', 'wd_mind_runtime_process',
               'wd_mind_absent_generation_prefix_readonly', 'wd_mind_startup_content',
               'bridge_compact_view', 'wd_bridge_mind_lane_read', 'wd_bridge_mind')
    files = {}
    package_names = ('tools', 'waggledance', 'waggledance.core')
    for name in package_names:
        relative = 'tools-bootstrap/' + name.replace('.', '/') + '/__init__.py'
        path = root.joinpath(*relative.split('/'))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'')
        files[relative] = hashlib.sha256(b'').hexdigest().upper()
        package = ModuleType(name)
        package.__file__, package.__path__ = str(path), [str(path.parent)]
        monkeypatch.setitem(sys.modules, name, package)
    parent = sys.modules['tools']
    for name in modules:
        relative = f'tools-bootstrap/tools/{name}.py'
        path = root.joinpath(*relative.split('/'))
        source = b'synthetic_value = 1\n'
        path.write_bytes(source)
        files[relative] = hashlib.sha256(source).hexdigest().upper()
        if name != 'wd_mind_runtime_process':
            loaded = ModuleType('tools.' + name)
            loaded.__file__ = str(path)
            monkeypatch.setitem(sys.modules, loaded.__name__, loaded)
            setattr(parent, name, loaded)
    relative = 'tools-bootstrap/waggledance/core/bridge_log_reader.py'
    path = root.joinpath(*relative.split('/'))
    path.write_bytes(b'')
    files[relative] = hashlib.sha256(b'').hexdigest().upper()
    loaded = ModuleType('waggledance.core.bridge_log_reader')
    loaded.__file__ = str(path)
    monkeypatch.setitem(sys.modules, loaded.__name__, loaded)
    monkeypatch.delitem(sys.modules, 'tools.wd_mind_runtime_process', raising=False)
    raw = json.dumps({'schema_version': 1, 'files': files}).encode()
    (root / 'deployment-manifest.json').write_bytes(raw)
    if tampered:
        root.joinpath('tools-bootstrap', 'tools', 'wd_mind_runtime_process.py').write_bytes(b'changed')
    if tampered:
        with pytest.raises(ValueError):
            session._preflight_selected_resolver_code(str(root), hashlib.sha256(raw).hexdigest())
    else:
        session._preflight_selected_resolver_code(str(root), hashlib.sha256(raw).hexdigest())
        assert sys.modules['tools.wd_mind_runtime_process'].synthetic_value == 1
        monkeypatch.delitem(sys.modules, 'tools.wd_mind_runtime_process', raising=False)


@pytest.mark.parametrize('target', ['wd_mind_content_query_readonly',
                                    'wd_mind_content_snapshot_package',
                                    'wd_mind_full_runtime', 'wd_mind_retained_query', 'unchanged', 'pycache'])
def test_selected_resolver_refuses_changed_module_before_import(tmp_path, target):
    """A mismatched module cannot run its top-level code in a fresh interpreter."""
    source = Path(session.__file__).resolve().parent.parent
    bundle = tmp_path / 'bundle'
    root = bundle / 'tools-bootstrap'
    names = ['tools/__init__.py', 'tools/wd_bridge_mind_session.py',
             'tools/bridge_compact_view.py', 'tools/wd_bridge_mind_lane_read.py',
             'tools/wd_bridge_mind.py', 'tools/wd_mind_content_query_readonly.py',
             'tools/wd_mind_content_snapshot_package.py', 'tools/wd_mind_full_runtime.py',
             'tools/wd_mind_retained_query.py', 'tools/wd_mind_runtime_process.py',
             'tools/wd_mind_absent_generation_prefix_readonly.py',
             'tools/wd_mind_startup_content.py', 'waggledance/__init__.py',
             'waggledance/core/__init__.py', 'waggledance/core/bridge_log_reader.py']
    files = {}
    for name in names:
        raw = (source / name).read_bytes()
        dest = root / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(raw)
        files['tools-bootstrap/' + name] = hashlib.sha256(raw).hexdigest()
    raw_manifest = json.dumps({'schema_version':1, 'files':files}).encode()
    (bundle / 'deployment-manifest.json').write_bytes(raw_manifest)
    sentinel = tmp_path / 'side-effect'
    if target == 'pycache':
        victim = root / 'tools' / 'wd_mind_retained_query.py'
        original = victim.read_bytes()
        original_stat = victim.stat()
        poison = b'from pathlib import Path\nPath(' + repr(str(sentinel)).encode() + b').write_text("unsafe")\n'
        assert len(poison) < len(original)
        victim.write_bytes(poison + b' ' * (len(original) - len(poison)))
        os.utime(victim, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
        py_compile.compile(str(victim), doraise=True, invalidation_mode=py_compile.PycInvalidationMode.TIMESTAMP)
        victim.write_bytes(original)
        os.utime(victim, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
    elif target != 'unchanged':
        victim = root / 'tools' / (target + '.py')
        victim.write_bytes(b'from pathlib import Path\nPath(' + repr(str(sentinel)).encode() +
                           b').write_text("unsafe")\n')
    script = '''
import hashlib, sys
sys.path.insert(0, sys.argv[1])
from tools import wd_bridge_mind_session as session
if sys.argv[4] in ('unchanged', 'pycache'):
    session._preflight_selected_resolver_code(sys.argv[2], sys.argv[3])
    assert 'tools.wd_mind_retained_query' in sys.modules
try:
    session.create_selected_row_resolver(enabled=True, bundle_root=sys.argv[2],
        expected_manifest_sha256=sys.argv[3], identifier_policy_bytes=b'{}',
        expected_identifier_policy_sha256=hashlib.sha256(b'{}').hexdigest(),
        expected_approval_reference_sha256='c'*64)
except session.MindContentSessionRefused:
    print('refused')
else:
    print('unexpected')
'''
    run = subprocess.run([sys.executable, '-c', script, str(root), str(bundle),
                          hashlib.sha256(raw_manifest).hexdigest(), target],
                         cwd=tmp_path, capture_output=True, text=True, timeout=30)
    assert run.returncode == 0, run.stderr
    assert run.stdout.strip() == 'refused'
    assert not sentinel.exists()


@pytest.mark.skipif(sys.platform != 'win32', reason='real packaged PowerShell wrapper requires Windows')
def test_real_wrapper_checks_early_session_dependencies_before_python(tmp_path):
    """Exercise the actual -VerifyPackage wrapper, not a standalone import."""
    helper_path = Path(__file__).with_name('test_wd_bridge_code_context.py')
    spec = importlib.util.spec_from_file_location('mind_wrapper_fixture', helper_path)
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    if helper.PWSH is None or not helper.HAS_BRIDGE_PYTHON:
        pytest.skip('PowerShell or the configured bridge Python is unavailable')
    targets = ('tools/bridge_compact_view.py', 'tools/wd_bridge_mind_lane_read.py',
               'tools/wd_bridge_mind.py', 'waggledance/core/bridge_log_reader.py')
    bundle = helper._stage_fake_bundle(tmp_path, real_files=(
        'tools/wd_bridge_mind_session.py', *targets,
        'waggledance/__init__.py', 'waggledance/core/__init__.py'),
        real_entrypoints={'mind_session':'tools/wd_bridge_mind_session.py'})
    wrapper = str(bundle / 'Invoke-WdBridgePython.ps1').replace("'", "''")
    script = (f"$ErrorActionPreference = 'Stop'; & '{wrapper}' "
              "-Tool 'tools/wd_bridge_mind_session.py' -VerifyPackage --help; exit $LASTEXITCODE")
    env = {'WD_REBOOT_EXPECTED_MANIFEST_HASH': helper._manifest_anchor(bundle),
           'WD_BRIDGE_PYTHON': helper.BRIDGE_PYTHON}
    ok = helper._run_pwsh(script, env=env)
    assert ok.returncode == 0, ok.stdout + ok.stderr
    assert 'usage:' in ok.stdout.lower()
    for index, relative in enumerate(targets):
        sentinel = tmp_path / f'early-side-effect-{index}'
        path = bundle / 'tools-bootstrap' / relative
        original = path.read_bytes()
        try:
            path.write_bytes(b'from pathlib import Path\nPath(' + repr(str(sentinel)).encode() +
                             b').write_text("unsafe")\n' + original)
            result = helper._run_pwsh(script, env=env)
            assert result.returncode != 0, result.stdout + result.stderr
            assert 'pinned bridge code hash mismatch' in result.stdout + result.stderr
            assert not sentinel.exists(), relative
        finally:
            path.write_bytes(original)


def _local_client_args(policy_path, policy, *, lane='codex-lead-1', timeout='10'):
    return ['--mind-local-candidate-read', '--policy-path', str(policy_path),
            '--policy-sha256', hashlib.sha256(policy).hexdigest(),
            '--approval-reference-sha256', 'c'*64, '--lane', lane, '--timeout-seconds', timeout]


def test_selected_prefix_budget_boundary_after_tail_reservation():
    budget = session._SourceReadBudget(1024 ** 3)
    budget.reserve(64 * 1024 ** 2 + 1)
    session._preflight_selected_source_prefix(budget, 251658239)
    with pytest.raises(session._SelectedSourcePrefixBudgetRefused):
        session._preflight_selected_source_prefix(budget, 251658240)


@pytest.mark.parametrize('controlled', [False, True])
@pytest.mark.parametrize('fault', ['prefix_budget', 'subclass', 'other'])
def test_local_candidate_cli_reports_only_known_prefix_budget(monkeypatch, tmp_path, controlled, fault):
    policy = b'{"synthetic":"policy"}'
    policy_path = tmp_path / 'policy.json'
    policy_path.write_bytes(policy)

    def fail_resolver(**_kwargs):
        if fault == 'prefix_budget':
            raise session._SelectedSourcePrefixBudgetRefused('synthetic-private-detail')
        if fault == 'subclass':
            class UntrustedBudgetRefusal(session._SelectedSourcePrefixBudgetRefused):
                pass
            raise UntrustedBudgetRefusal('synthetic-private-detail')
        raise ValueError('synthetic-private-detail')

    monkeypatch.setattr(session, 'create_selected_row_resolver', fail_resolver)
    args = _local_client_args(policy_path, policy)
    if controlled:
        args[0] = '--mind-local-candidate-read-controlled'
    out, err = io.BytesIO(), io.BytesIO()
    code = session._local_candidate_entry(args, out, err, str(Path.cwd()), 'a' * 64,
                                          control=io.BytesIO() if controlled else None)
    assert code == 2 and out.getvalue() == b''
    if fault == 'prefix_budget':
        assert err.getvalue() == (b'{"error":"mind_session_content_refused",'
                                  b'"reason":"SOURCE_PREFIX_BUDGET","authority":"none"}\n')
    else:
        assert err.getvalue() == b'{"error":"mind_session_content_refused","authority":"none"}\n'
    assert b'synthetic-private-detail' not in err.getvalue()


def test_prefix_budget_reason_is_not_emitted_after_deadline(monkeypatch, tmp_path):
    ticks = iter([0, 0, 0, 0, 20])
    monkeypatch.setattr(session, 'time', SimpleNamespace(monotonic=lambda: next(ticks, 20)))
    def fail_resolver(**_kwargs):
        raise session._SelectedSourcePrefixBudgetRefused('synthetic-private-detail')
    monkeypatch.setattr(session, 'create_selected_row_resolver', fail_resolver)
    policy = b'{}'
    policy_path = tmp_path / 'policy.json'
    policy_path.write_bytes(policy)
    out, err = io.BytesIO(), io.BytesIO()
    code = session._local_candidate_entry(_local_client_args(policy_path, policy), out, err,
                                          str(Path.cwd()), 'a' * 64)
    assert code == 2 and out.getvalue() == b''
    assert err.getvalue() == b'{"error":"mind_session_content_refused","authority":"none"}\n'


@pytest.mark.parametrize(('lane', 'scope'), [('codex-lead-1', 'own'), ('claude-rco-1', 'shared')])
def test_local_candidate_cli_rechecks_real_synthetic_rows(flat_consumer_case, monkeypatch, tmp_path, lane, scope):
    from tools import wd_mind_runtime_process as process
    resolver, frame, _, _ = _located_candidates(flat_consumer_case, monkeypatch)
    monkeypatch.setattr(session, 'create_selected_row_resolver', lambda **_: resolver)
    observed = {}
    class Query:
        def __init__(self, request): self.request = request
        def start(self): pass
        def wait(self, *, timeout):
            frame.update(nonce=self.request['nonce'], lane=self.request['lane'], scope=self.request['scope'])
            return {'status':'CANDIDATES', 'authority':'none', 'sender_authentication':'UNKNOWN',
                    'source_verification':'UNVERIFIED', 'candidate_frame':content._canonical(frame)}
        def status(self): return {'state':'STOPPED'}
    def factory(*, enabled, pipe_name, request):
        observed.update(request)
        return Query(request)
    monkeypatch.setattr(process, 'create_local_candidate_query', factory)
    policy = b'{"private":"synthetic-only"}'
    policy_path = tmp_path / 'policy.json'
    policy_path.write_bytes(policy)
    out, err = io.BytesIO(), io.BytesIO()
    code = session._local_candidate_entry(_local_client_args(policy_path, policy, lane=lane),
                                          out, err, str(Path.cwd()), 'a'*64)
    assert code == 0 and err.getvalue() == b''
    result = json.loads(out.getvalue())
    assert result['schema'] == 'wd.mind-flat-verified-session.v1'
    assert result['authority'] == 'none' and result['sender_authentication'] == 'UNKNOWN'
    assert result['results'][0]['source_event_sha256'] == frame['candidates'][0]['source_event_sha256']
    assert observed['schema'] == 'wd.mind-pipe-request.v2' and observed['scope'] == scope
    assert policy not in out.getvalue()


@pytest.mark.parametrize('fault', ['missing', 'relative', 'wrong_hash', 'oversized', 'untrusted_row'])
def test_local_candidate_cli_refuses_without_partial_output(flat_consumer_case, monkeypatch, tmp_path, fault):
    from tools import wd_mind_runtime_process as process
    resolver, frame, _, _ = _located_candidates(flat_consumer_case, monkeypatch)
    monkeypatch.setattr(session, 'create_selected_row_resolver', lambda **_: resolver)
    class Query:
        def __init__(self, request): self.request = request
        def start(self): pass
        def wait(self, *, timeout):
            frame['nonce'] = self.request['nonce']
            frame['scope'] = self.request['scope']
            frame['candidates'][0]['source_event_sha256'] = '0'*64
            return {'status':'CANDIDATES', 'authority':'none', 'sender_authentication':'UNKNOWN',
                    'source_verification':'UNVERIFIED', 'candidate_frame':content._canonical(frame)}
        def status(self): return {'state':'STOPPED'}
    monkeypatch.setattr(process, 'create_local_candidate_query', lambda **kw: Query(kw['request']))
    policy = b'{"private":"synthetic-only"}'
    policy_path = tmp_path / 'policy.json'
    if fault != 'missing':
        policy_path.write_bytes(b'wrong' if fault == 'wrong_hash' else b'x' * 65537 if fault == 'oversized' else policy)
    if fault == 'relative':
        policy_path = Path('policy.json')
    out, err = io.BytesIO(), io.BytesIO()
    code = session._local_candidate_entry(_local_client_args(policy_path, policy),
                                          out, err, str(Path.cwd()), 'a'*64)
    assert code == 2 and out.getvalue() == b''
    assert err.getvalue() == b'{"error":"mind_session_content_refused","authority":"none"}\n'
    assert policy not in err.getvalue()


@pytest.fixture
def synthetic_session_checkout_pins(monkeypatch):
    """Use verified checkout bytes for synthetic tests, not production pins."""
    _pin_verified_synthetic_checkout(monkeypatch)


def _pin_verified_synthetic_checkout(monkeypatch):
    root = Path(content.__file__).resolve().parents[1]
    pins = {}
    for name, expected in content._REQUIRED_CODE.items():
        raw = (root / name).read_bytes()
        raw_digest = hashlib.sha256(raw).hexdigest()
        lf_digest = hashlib.sha256(raw.replace(b'\r\n', b'\n')).hexdigest()
        if raw_digest != expected and (b'\r\n' not in raw or lf_digest != expected):
            raise AssertionError('synthetic checkout does not match pinned bytes: ' + name)
        pins[name] = raw_digest
    monkeypatch.setattr(content, '_REQUIRED_CODE', pins)


@pytest.mark.parametrize(('raw', 'accepted'), [
    (b'first\nsecond\n', True),
    (b'first\r\nsecond\r\n', True),
    (b'changed\r\nsecond\r\n', False),
])
def test_synthetic_checkout_pin_accepts_only_verified_lf_or_crlf(monkeypatch, tmp_path, raw, accepted):
    tools_dir = tmp_path / 'tools'
    tools_dir.mkdir()
    source = tools_dir / 'synthetic.py'
    source.write_bytes(raw)
    expected = hashlib.sha256(b'first\nsecond\n').hexdigest()
    monkeypatch.setattr(content, '__file__', str(tools_dir / 'reader.py'))
    monkeypatch.setattr(content, '_REQUIRED_CODE', {'tools/synthetic.py': expected})
    if accepted:
        _pin_verified_synthetic_checkout(monkeypatch)
        assert content._REQUIRED_CODE['tools/synthetic.py'] == hashlib.sha256(raw).hexdigest()
    else:
        with pytest.raises(AssertionError, match='synthetic checkout does not match pinned bytes'):
            _pin_verified_synthetic_checkout(monkeypatch)


@pytest.mark.parametrize('controlled', [False, True], ids=['manual', 'controlled'])
def test_real_synthetic_resolver_prefix_budget_reaches_fixed_reason(
        synthetic_session_checkout_pins, flat_consumer_case, monkeypatch, tmp_path, controlled):
    resolver, _, snapshot, _ = _located_candidates(flat_consumer_case, monkeypatch)
    policy = snapshot['identifier_policy_binding'][0]
    policy_path = tmp_path / 'synthetic-policy.json'
    policy_path.write_bytes(policy)
    # The tail allowance fits; the known four-prefix preflight misses by one byte.
    monkeypatch.setattr(session, '_STARTUP_SOURCE_READ_LIMIT',
                        64 * 1024 ** 2 + 1 + 4 * resolver._cursor.offset)
    args = _local_client_args(policy_path, policy)
    if controlled:
        args[0] = '--mind-local-candidate-read-controlled'
    out, err = io.BytesIO(), io.BytesIO()
    code = session._local_candidate_entry(args, out, err, str(resolver._root), resolver._pin,
                                          control=io.BytesIO() if controlled else None)
    assert code == 2 and out.getvalue() == b''
    assert err.getvalue() == (b'{"error":"mind_session_content_refused",'
                              b'"reason":"SOURCE_PREFIX_BUDGET","authority":"none"}\n')
    assert policy not in err.getvalue()


@pytest.mark.parametrize('controlled', [False, True], ids=['manual', 'controlled'])
def test_local_candidate_budget_exhaustion_never_emits_partial_content(
        synthetic_session_checkout_pins, flat_consumer_case, monkeypatch, tmp_path, controlled):
    """Real synthetic resolver budget; fake in-process candidate transport."""
    from tools import wd_mind_runtime_process as process
    resolver, frame, _, _ = _located_candidates(flat_consumer_case, monkeypatch)
    # One complete prefix fits, but no selected-row byte does. This exercises
    # mid-resolution exhaustion rather than a mocked budget exception.
    resolver._source_budget = session._SourceReadBudget(resolver._cursor.offset)
    monkeypatch.setattr(session, 'create_selected_row_resolver', lambda **_: resolver)
    observed = {}
    class Query:
        def __init__(self, request): self.request = request
        def start(self): pass
        def wait(self, *, timeout):
            observed['nonce'] = self.request['nonce']
            frame.update(nonce=self.request['nonce'], lane=self.request['lane'], scope=self.request['scope'])
            return {'status':'CANDIDATES', 'authority':'none', 'sender_authentication':'UNKNOWN',
                    'source_verification':'UNVERIFIED', 'candidate_frame':content._canonical(frame)}
        def status(self): return {'state':'STOPPED'}
    monkeypatch.setattr(process, 'create_local_candidate_query', lambda **kw: Query(kw['request']))
    class Control:
        def __init__(self): self.reads = 0
        def read(self, size):
            self.reads += 1
            return b'G' + observed['nonce'].encode('ascii') if self.reads == 1 else b''
    policy = b'{"private":"synthetic-only"}'
    policy_path = tmp_path / 'synthetic-policy.json'
    policy_path.write_bytes(policy)
    args = _local_client_args(policy_path, policy)
    if controlled:
        args[0] = '--mind-local-candidate-read-controlled'
    out, err = io.BytesIO(), io.BytesIO()
    # The fake Query is deliberately not the runtime's exact query type and
    # cannot be reaped by production logic. Never mutate the real pending list.
    private_pending = []
    monkeypatch.setattr(session, '_LOCAL_CANDIDATE_PENDING', private_pending)
    try:
        code = session._local_candidate_entry(args, out, err, str(Path.cwd()), 'a'*64,
                                              control=Control() if controlled else None)
        assert len(private_pending) == 1
        assert code == 2 and resolver._closed and resolver._source_budget._remaining == 0
        assert err.getvalue() == b'{"error":"mind_session_content_refused","authority":"none"}\n'
        if controlled:
            ready = json.loads(out.getvalue())
            assert ready == {'schema':'wd.mind-controlled-read-ready.v1', 'authority':'none',
                             'nonce':observed['nonce']}
        else:
            assert out.getvalue() == b''
        assert b'synthetic-only' not in out.getvalue() + err.getvalue()
    finally:
        # Keep every synthetic worker reachable until target-finally and the
        # physical thread exit are both observed, including error paths.
        deferred = None
        while True:
            try:
                all_stopped = True
                for worker, state in tuple(private_pending):
                    if state['worker_done'].is_set() and worker.ident is not None:
                        worker.join(timeout=0)
                        if not worker.is_alive():
                            continue
                    all_stopped = False
                if all_stopped:
                    break
                time.sleep(0.05)
            except BaseException as exc:
                if deferred is None:
                    deferred = exc
        if deferred is not None:
            raise deferred


def test_local_candidate_cli_rechecks_policy_after_candidate_query(flat_consumer_case, monkeypatch, tmp_path):
    from tools import wd_mind_runtime_process as process
    resolver, frame, _, _ = _located_candidates(flat_consumer_case, monkeypatch)
    monkeypatch.setattr(session, 'create_selected_row_resolver', lambda **_: resolver)
    policy = b'{"private":"synthetic-only"}'
    policy_path = tmp_path / 'policy.json'
    policy_path.write_bytes(policy)
    class Query:
        def __init__(self, request): self.request = request
        def start(self): pass
        def wait(self, *, timeout):
            frame.update(nonce=self.request['nonce'], lane=self.request['lane'], scope=self.request['scope'])
            policy_path.write_bytes(b'changed')
            return {'status':'CANDIDATES', 'authority':'none', 'sender_authentication':'UNKNOWN',
                    'source_verification':'UNVERIFIED', 'candidate_frame':content._canonical(frame)}
        def status(self): return {'state':'STOPPED'}
    monkeypatch.setattr(process, 'create_local_candidate_query', lambda **kw: Query(kw['request']))
    out, err = io.BytesIO(), io.BytesIO()
    code = session._local_candidate_entry(_local_client_args(policy_path, policy), out, err, str(Path.cwd()), 'a'*64)
    assert code == 2 and out.getvalue() == b''
    assert err.getvalue() == b'{"error":"mind_session_content_refused","authority":"none"}\n'
    assert policy not in err.getvalue()


def test_local_candidate_cli_timeout_retains_work_without_kill(monkeypatch, tmp_path):
    entered, release = threading.Event(), threading.Event()
    def delayed(**_):
        entered.set()
        release.wait(5)
        raise ValueError('synthetic')
    monkeypatch.setattr(session, 'create_selected_row_resolver', delayed)
    policy = b'{}'
    policy_path = tmp_path / 'policy.json'
    policy_path.write_bytes(policy)
    out, err = io.BytesIO(), io.BytesIO()
    try:
        code = session._local_candidate_entry(_local_client_args(policy_path, policy, timeout='1'),
            out, err, str(Path.cwd()), 'a'*64)
        assert entered.is_set() and code == 2 and out.getvalue() == b''
        worker, _ = session._LOCAL_CANDIDATE_PENDING[-1]
        assert worker.is_alive() and worker.daemon is False
    finally:
        release.set()
        worker.join(5)
        session._LOCAL_CANDIDATE_PENDING.remove((worker, _))


def test_late_prefix_budget_refusal_does_not_replace_timeout(monkeypatch, tmp_path):
    from test_wd_mind_full_runtime import _drain_synthetic_race_worker
    entered, release = threading.Event(), threading.Event()
    def delayed(**_):
        entered.set()
        release.wait(5)
        raise session._SelectedSourcePrefixBudgetRefused('synthetic-private-detail')
    monkeypatch.setattr(session, 'create_selected_row_resolver', delayed)
    policy = b'{}'
    policy_path = tmp_path / 'policy.json'
    policy_path.write_bytes(policy)
    out, err = io.BytesIO(), io.BytesIO()
    before = {id(pending) for pending in session._LOCAL_CANDIDATE_PENDING}
    try:
        code = session._local_candidate_entry(_local_client_args(policy_path, policy, timeout='1'),
            out, err, str(Path.cwd()), 'a' * 64)
        assert entered.is_set() and code == 2 and out.getvalue() == b''
        assert err.getvalue() == b'{"error":"mind_session_content_refused","authority":"none"}\n'
        worker, state = session._LOCAL_CANDIDATE_PENDING[-1]
        assert worker.is_alive() and worker.daemon is False
    finally:
        release.set()
        for worker, state in [pending for pending in session._LOCAL_CANDIDATE_PENDING if id(pending) not in before]:
            try:
                _drain_synthetic_race_worker(worker, state['worker_done'], release, threading.Event(), attempted=True)
            finally:
                if state['worker_done'].is_set() and worker.ident is not None and not worker.is_alive():
                    session._LOCAL_CANDIDATE_PENDING.remove((worker, state))
            assert state['reason'] is None


def test_local_candidate_cli_preserves_uncertain_query_owner(monkeypatch, tmp_path):
    from tools import wd_mind_runtime_process as process
    query = SimpleNamespace(start=lambda: None,
                            wait=lambda **_: {'status':'TIMEOUT'},
                            status=lambda: {'state':'HOLD'})
    resolver = object()
    monkeypatch.setattr(session, 'create_selected_row_resolver', lambda **_: resolver)
    monkeypatch.setattr(process, 'create_local_candidate_query', lambda **_: query)
    policy = b'{}'
    policy_path = tmp_path / 'policy.json'
    policy_path.write_bytes(policy)
    out, err = io.BytesIO(), io.BytesIO()
    code = session._local_candidate_entry(_local_client_args(policy_path, policy),
        out, err, str(Path.cwd()), 'a'*64)
    assert code == 2 and out.getvalue() == b''
    pending = session._LOCAL_CANDIDATE_PENDING[-1]
    assert pending[1]['query'] is query and pending[1]['resolver'] is resolver
    session._LOCAL_CANDIDATE_PENDING.remove(pending)


def test_local_candidate_cli_subprocess_fails_closed_with_unpinned_bundle(tmp_path):
    policy = b'{"private":"synthetic-only"}'
    policy_path = tmp_path / 'policy.json'
    policy_path.write_bytes(policy)
    env = dict(os.environ, WD_REBOOT_EXPECTED_MANIFEST_HASH='a'*64,
               PYTHONPATH=str(Path(session.__file__).parent.parent))
    run = subprocess.run([sys.executable, str(Path(session.__file__)), *_local_client_args(policy_path, policy)],
                         cwd=Path(session.__file__).parent.parent, env=env,
                         capture_output=True, timeout=30)
    assert run.returncode == 2 and run.stdout == b''
    assert run.stderr == b'{"error":"mind_session_content_refused","authority":"none"}\n'
    assert policy not in run.stderr


def test_selected_row_resolver_reuses_state_without_rederiving_projection(flat_consumer_case, monkeypatch):
    monkeypatch.setattr(content, '_verified_complete', lambda *_: (_ for _ in ()).throw(AssertionError('rederive')))
    monkeypatch.setattr(content, '_projection', lambda *_a, **_k: (_ for _ in ()).throw(AssertionError('projection')))
    resolver, frame, _, _ = _located_candidates(flat_consumer_case, monkeypatch)
    for _ in range(2):
        result = resolver.resolve(ipc_bytes=content._canonical(frame), expected_nonce='1'*64,
                                  lane='codex-lead-1', scope='shared', deadline_monotonic=time.monotonic()+60)
        assert result['results'] and result['index_match'] == 'UNKNOWN'


@pytest.mark.parametrize('fault', ['offset', 'length', 'chunk', 'ref', 'doc', 'source', 'extra', 'duplicate'])
def test_selected_row_resolver_refuses_untrusted_candidate_fields(flat_consumer_case, monkeypatch, fault):
    resolver, frame, _, _ = _located_candidates(flat_consumer_case, monkeypatch)
    item = frame['candidates'][0]
    if fault == 'offset': item['source_offset'] += 1
    if fault == 'length': item['source_length'] -= 1
    if fault == 'chunk': item['chunk_index'] = 999999
    if fault == 'ref': item['ref_sha256'] = '0'*64
    if fault == 'doc': item['doc_id_sha256'] = '0'*64
    if fault == 'source': item['source_event_sha256'] = '0'*64
    if fault == 'extra': item['text'] = 'untrusted and never accepted'
    if fault == 'duplicate': frame['candidates'].append(dict(item))
    with pytest.raises(session.MindContentSessionRefused, match='^mind_session_content_refused$'):
        resolver.resolve(ipc_bytes=content._canonical(frame), expected_nonce='1'*64,
                         lane='codex-lead-1', scope='shared', deadline_monotonic=time.monotonic()+60)


def test_selected_row_resolver_accepts_append_and_historical_hits(flat_consumer_case, monkeypatch):
    from datetime import timedelta
    resolver, frame, snapshot, clock = _located_candidates(flat_consumer_case, monkeypatch)
    clock.value += timedelta(days=2)
    with Path(snapshot['messages']['source_path']).open('ab') as stream:
        stream.write(b'{"agent":"later"}\n')
    result = resolver.resolve(ipc_bytes=content._canonical(frame), expected_nonce='1'*64,
                              lane='codex-lead-1', scope='shared', deadline_monotonic=time.monotonic()+60)
    assert all(row['event_age_seconds_upper_bound'] >= 900 for row in result['results'])
    assert result['current_status_freshness'] == 'UNKNOWN'


def test_selected_row_resolver_refuses_future_source(flat_consumer_case, monkeypatch):
    from datetime import timedelta
    resolver, frame, _, clock = _located_candidates(flat_consumer_case, monkeypatch)
    clock.value -= timedelta(days=1)
    with pytest.raises(session.MindContentSessionRefused):
        resolver.resolve(ipc_bytes=content._canonical(frame), expected_nonce='1'*64,
                         lane='codex-lead-1', scope='shared', deadline_monotonic=time.monotonic()+60)


def test_selected_row_resolver_refuses_changed_policy_pin(flat_consumer_case, monkeypatch):
    from tools import wd_mind_full_runtime as runtime
    resolver, frame, _, _ = _located_candidates(flat_consumer_case, monkeypatch)
    monkeypatch.setattr(runtime, 'create_operator_pinned_policy_anchor', lambda **_: (_ for _ in ()).throw(ValueError('pin')))
    with pytest.raises(session.MindContentSessionRefused):
        resolver.resolve(ipc_bytes=content._canonical(frame), expected_nonce='1'*64,
                         lane='codex-lead-1', scope='shared', deadline_monotonic=time.monotonic()+60)


def test_selected_row_resolver_scope_and_rco_shared_access(flat_consumer_case, monkeypatch):
    resolver, frame, _, _ = _located_candidates(flat_consumer_case, monkeypatch)
    frame['lane'] = 'claude-rco-1'
    result = resolver.resolve(ipc_bytes=content._canonical(frame), expected_nonce='1'*64,
                              lane='claude-rco-1', scope='shared', deadline_monotonic=time.monotonic()+60)
    assert result['results'] and all(row['source_agent'] in content._BUILD_LANES for row in result['results'])
    other = next(lane for lane in content._BUILD_LANES if lane != result['results'][0]['source_agent'])
    frame['lane'], frame['scope'] = other, 'own'
    with pytest.raises(session.MindContentSessionRefused):
        resolver.resolve(ipc_bytes=content._canonical(frame), expected_nonce='1'*64,
                         lane=other, scope='own', deadline_monotonic=time.monotonic()+60)


def test_selected_row_resolver_bad_request_and_timeout_do_not_drop_state(flat_consumer_case, monkeypatch):
    resolver, frame, _, _ = _located_candidates(flat_consumer_case, monkeypatch)
    raw = content._canonical(frame)
    with pytest.raises(session.MindContentSessionRefused):
        resolver.resolve(ipc_bytes=raw, expected_nonce='2'*64, lane='codex-lead-1', scope='shared',
                         deadline_monotonic=time.monotonic()+60)
    assert not resolver._closed
    now = time.monotonic()
    calls = iter([now, now + 61])
    monkeypatch.setattr(session, 'time', SimpleNamespace(monotonic=lambda: next(calls)))
    with pytest.raises(session.MindContentSessionRefused):
        resolver.resolve(ipc_bytes=raw, expected_nonce='1'*64, lane='codex-lead-1', scope='shared',
                         deadline_monotonic=now+60)
    assert not resolver._closed
    monkeypatch.setattr(session, 'time', time)
    assert resolver.resolve(ipc_bytes=raw, expected_nonce='1'*64, lane='codex-lead-1', scope='shared',
                            deadline_monotonic=time.monotonic()+60)['results']


def test_selected_row_resolver_refuses_new_generation(flat_consumer_case, monkeypatch):
    resolver, frame, snapshot, _ = _located_candidates(flat_consumer_case, monkeypatch)
    Path(snapshot['messages']['generation_path']).write_text('unexpected', encoding='utf-8')
    with pytest.raises(session.MindContentSessionRefused):
        resolver.resolve(ipc_bytes=content._canonical(frame), expected_nonce='1'*64,
                         lane='codex-lead-1', scope='shared', deadline_monotonic=time.monotonic()+60)
    assert resolver._closed


@pytest.mark.parametrize('route', ['review_mention', 'rco_sender', 'rco_recipient'])
def test_selected_row_resolver_review_mentions_and_rco_routes(flat_consumer_case, monkeypatch, route):
    _, old_owner, proposals, snapshot, _, _ = flat_consumer_case
    source = Path(snapshot['messages']['source_path'])
    old_map = json.loads(old_owner._gate._raw_map)
    entry = old_map['entries'][proposals[0]['source_event_sha256']]
    with source.open('rb') as stream:
        stream.seek(entry['offset'])
        row = json.loads(stream.read(entry['length']))
    row['message'] = 'Synthetic build discussion cites RCO_PASS and a review outcome.'
    if route == 'rco_sender': row['agent'] = 'claude-rco-1'
    if route == 'rco_recipient': row['to'] = 'claude-rco-1'
    line = content._canonical(row) + b'\n'
    offset = source.stat().st_size
    with source.open('ab') as stream:
        stream.write(line)
    resolver, frame, _, _ = _located_candidates(flat_consumer_case, monkeypatch)
    source_ref = content._sha(content._canonical(row))
    candidate = dict(frame['candidates'][0], source_event_sha256=source_ref,
                     source_offset=offset, source_length=len(line), chunk_index=0)
    if route == 'review_mention':
        context = resolver._context()
        fields = content._fields(row, context['policy'], context['roster'])
        verdict, screened = content._classify_with_policy(row['message'], context['policy']['max_message_chars'],
                                                          context['private'])
        assert type(fields) is dict and verdict == 'KEEP'
        record = content._record(source_ref, fields, content.chunk(screened, context['policy']['max_chunk_chars']), 0)
        candidate['ref_sha256'] = content._sha(record['provenance']['event_ref'].encode())
        candidate['doc_id_sha256'] = content._sha(record['record_id'].encode())
    frame['candidates'] = [candidate]
    args = dict(ipc_bytes=content._canonical(frame), expected_nonce='1'*64, lane='codex-lead-1',
                scope='shared', deadline_monotonic=time.monotonic()+60)
    if route == 'review_mention':
        assert 'RCO' in resolver.resolve(**args)['results'][0]['text']
    else:
        with pytest.raises(session.MindContentSessionRefused):
            resolver.resolve(**args)


def test_selected_row_resolver_refuses_change_after_first_local_read(flat_consumer_case, monkeypatch):
    resolver, frame, snapshot, _ = _located_candidates(flat_consumer_case, monkeypatch)
    original = type(resolver)._selected
    def mutate_after_first(self, *args):
        result = original(self, *args)
        path = Path(snapshot['messages']['source_path'])
        raw = path.read_bytes()
        changed = raw.replace(b'"message":', b'"messagE":', 1)
        assert len(changed) == len(raw) and changed != raw
        path.write_bytes(changed)
        return result
    monkeypatch.setattr(type(resolver), '_selected', mutate_after_first)
    with pytest.raises(session.MindContentSessionRefused):
        resolver.resolve(ipc_bytes=content._canonical(frame), expected_nonce='1'*64,
                         lane='codex-lead-1', scope='shared', deadline_monotonic=time.monotonic()+60)
    assert resolver._closed and resolver._private is None


@pytest.fixture
def flat_consumer_case(source_fixtures, tmp_path, monkeypatch):
    _pin_verified_synthetic_checkout(monkeypatch)
    state = {'lock': threading.Lock(), 'used': set()}
    monkeypatch.setitem(vars(builtins), '_wd_mind_flat_export_nonce_registry_v1', state)
    monkeypatch.setattr(session, '_EXPORT_NONCE_STATE', state, raising=False)
    from tools import wd_mind_content_snapshot_package as package
    path = Path(__file__).with_name('test_wd_mind_content_snapshot_package.py')
    spec = importlib.util.spec_from_file_location('flat_consumer_package_fixtures', path)
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    owner, proposals, snapshot, clock = helper._flat_owner_fixture(package, source_fixtures, tmp_path, monkeypatch)
    monkeypatch.setattr(session, 'datetime', clock)
    # This fixture exercises the real source/row gate with synthetic controls;
    # independent code-pin behavior is covered separately below.
    monkeypatch.setattr(session, '_pin_flat_session', lambda root, pin: None, raising=False)
    def response():
        return json.dumps({'schema': 'wd.mind-pipe-candidates.v1', 'nonce': '1' * 64,
            'lane': 'codex-lead-1', 'scope': 'shared', 'status': 'CANDIDATES',
            'candidates': proposals}, separators=(',', ':')).encode()
    return package, owner, proposals, snapshot, clock, response


def _export_fixture(owner, snapshot, *, identifier_override=None):
    gate = owner._gate
    message = snapshot['messages']
    identifier = snapshot['identifier_policy_binding'][0] if identifier_override is None else identifier_override
    key = b'A' * 32  # Match the production export contract, not the generic fixture key.
    tag = hmac.new(key, identifier, hashlib.sha256).hexdigest()
    decision = json.loads(snapshot['decision_bytes'])
    decision['identifier_policy_hmac'] = tag
    decision_bytes = content._canonical(decision)
    updated = dict(snapshot, identifier_policy_binding=(identifier, key, tag),
                   decision_bytes=decision_bytes, decision_sha256=content._sha(decision_bytes))
    inputs = {name: value for name, value in updated.items()
              if name not in ('projection_bytes', 'projection_sha256')}
    projection_bytes = content._serialized(content.rederive_complete_message_projection(**inputs))
    flat_manifest = json.loads(gate._flat_raw)
    flat_manifest['projection_sha256'] = content._sha(projection_bytes)
    blocks = (
        ('message_policy', message['policy_bytes']), ('decision', decision_bytes),
        ('nonreview_policy', snapshot['policy_bytes']), ('roster', snapshot['roster_bytes']),
        ('task_scope', snapshot['approved_tasks_bytes']), ('identifier_policy', identifier),
        ('identifier_key', key), ('projection', projection_bytes),
        ('flat_manifest', content._canonical(flat_manifest)), ('ids', content._canonical(list(gate._ids))),
        ('vectors', gate._vectors), ('encoder_binding', gate._encoder),
    )
    cursor = message['end_cursor']
    meta = {'schema':'wd.mind-owner-export.v1', 'authority':'none', 'nonce':'1'*64,
        'source_path':message['source_path'], 'generation_path':message['generation_path'],
        'cursor':{'offset':cursor.offset, 'file_identity':cursor.file_identity, 'generation':cursor.generation},
        'identifier_policy_tag':tag, 'blocks':[{'name':name, 'length':len(value)} for name,value in blocks]}
    return meta, blocks


@pytest.mark.parametrize('fault', ['none','off','nonce','path','policy','encoder','vector','ids',
                                   'unknown','size','order','length','noncanonical'])
def test_flat_export_reconstructs_only_independently_verified_owner(flat_consumer_case, fault):
    package, owner, proposals, snapshot, clock, response = flat_consumer_case
    meta, blocks = _export_fixture(owner, snapshot)
    blocks = list(blocks)
    if fault == 'nonce': meta['nonce'] = '2'*64
    if fault == 'path': meta['source_path'] += '.unexpected'
    if fault in {'policy','encoder','vector','ids'}:
        name = {'policy':'message_policy','encoder':'encoder_binding','vector':'vectors','ids':'ids'}[fault]
        index = next(i for i, block in enumerate(blocks) if block[0] == name)
        value = blocks[index][1]
        blocks[index] = (name, bytes([value[0] ^ 1]) + value[1:])
    if fault == 'unknown': meta['unexpected'] = True
    if fault == 'order': blocks[0], blocks[1] = blocks[1], blocks[0]
    if fault == 'length': meta['blocks'][0]['length'] += 1
    raw = content._canonical(meta)
    if fault == 'noncanonical': raw += b' '
    authorization = session.create_flat_export_authorization(enabled=True, expected_nonce='1'*64,
        expected_identifier_policy_sha256=content._sha(snapshot['identifier_policy_binding'][0]))
    lease = authorization.begin()
    arguments = dict(enabled=fault != 'off', metadata_bytes=raw, blocks=tuple(blocks), lease=lease,
                     bundle_root=owner._root, expected_manifest_sha256=owner._pin,
                     max_total_bytes=1 if fault == 'size' else 64*1024*1024)
    if fault != 'none':
        with pytest.raises(session.MindContentSessionRefused):
            session.prepare_flat_source_owner_from_export(**arguments)
        return
    rebuilt = session.prepare_flat_source_owner_from_export(**arguments)
    result = session.query_flat_source_candidates(enabled=True, owner=rebuilt, ipc_bytes=response(),
        bundle_root=owner._root, expected_manifest_sha256=owner._pin,
        expected_nonce='1'*64, lane='codex-lead-1', scope='shared')
    assert result['authority'] == 'none' and result['sender_authentication'] == 'UNKNOWN'
    assert result['index_match'] == 'UNKNOWN'
    assert [item['source_event_sha256'] for item in result['results']] == [p['source_event_sha256'] for p in proposals]


@pytest.mark.parametrize('fault', ['refusal', 'interrupt', 'cancel'])
def test_flat_export_refusal_and_cancellation_do_not_keep_key_in_session_traceback(flat_consumer_case, monkeypatch, fault):
    package, owner, _, snapshot, _, _ = flat_consumer_case
    meta, blocks = _export_fixture(owner, snapshot)
    secret = b'A' * 32
    if fault == 'refusal':
        meta['nonce'] = '2' * 64
        exception_type = session.MindContentSessionRefused
    else:
        exception_type = KeyboardInterrupt if fault == 'interrupt' else asyncio.CancelledError
        def fail(**kwargs):
            raise exception_type('synthetic sensitive exception detail')
        monkeypatch.setattr(package, 'create_consumer_flat_source_owner', fail)
    authorization = session.create_flat_export_authorization(enabled=True, expected_nonce='1'*64,
        expected_identifier_policy_sha256=content._sha(snapshot['identifier_policy_binding'][0]))
    try:
        session.prepare_flat_source_owner_from_export(enabled=True, metadata_bytes=content._canonical(meta),
            blocks=blocks, lease=authorization.begin(), bundle_root=owner._root,
            expected_manifest_sha256=owner._pin, max_total_bytes=64*1024*1024)
    except exception_type as error:
        assert 'synthetic sensitive exception detail' not in str(error)
        frames = []
        trace = error.__traceback__
        while trace:
            if trace.tb_frame.f_code.co_filename == session.__file__:
                frames.append(trace.tb_frame)
            trace = trace.tb_next
        assert frames
        assert all(repr(secret) not in repr(frame.f_locals) for frame in frames)
        assert error.__context__ is None
    else:
        pytest.fail('expected fixed refusal or cancellation')


@pytest.mark.parametrize('base', [KeyboardInterrupt, GeneratorExit, asyncio.CancelledError])
def test_flat_export_sanitizes_hostile_cancellation_subclasses(flat_consumer_case, monkeypatch, base):
    package, owner, _, snapshot, _, _ = flat_consumer_case
    meta, blocks = _export_fixture(owner, snapshot)
    class Hostile(base):
        count = 0
        def __new__(cls, *args):
            cls.count += 1
            if cls.count > 1:
                raise RuntimeError('untrusted constructor called again')
            return super().__new__(cls)
        def __getattribute__(self, name):
            raise RuntimeError('untrusted attribute read')
        def __setattr__(self, name, value):
            raise RuntimeError('untrusted attribute write')
    signal = Hostile()
    def fail(**kwargs):
        raise signal
    monkeypatch.setattr(package, 'create_consumer_flat_source_owner', fail)
    authorization = session.create_flat_export_authorization(enabled=True, expected_nonce='1'*64,
        expected_identifier_policy_sha256=content._sha(snapshot['identifier_policy_binding'][0]))
    with pytest.raises(base) as caught:
        session.prepare_flat_source_owner_from_export(enabled=True, metadata_bytes=content._canonical(meta),
            blocks=blocks, lease=authorization.begin(), bundle_root=owner._root,
            expected_manifest_sha256=owner._pin, max_total_bytes=64*1024*1024)
    assert type(caught.value) is base
    assert caught.value.__context__ is None


def test_flat_export_rejects_consistent_peer_policy_substitution(flat_consumer_case):
    _, owner, _, snapshot, _, _ = flat_consumer_case
    private = content._canonical({'schema':'wd.mind-identifier-policy.v2', 'identifiers':[]})
    assert private != snapshot['identifier_policy_binding'][0]
    meta, blocks = _export_fixture(owner, snapshot, identifier_override=private)
    authorization = session.create_flat_export_authorization(enabled=True, expected_nonce='1'*64,
        expected_identifier_policy_sha256=content._sha(snapshot['identifier_policy_binding'][0]))
    with pytest.raises(session.MindContentSessionRefused):
        session.prepare_flat_source_owner_from_export(enabled=True, metadata_bytes=content._canonical(meta),
            blocks=blocks, lease=authorization.begin(), bundle_root=owner._root,
            expected_manifest_sha256=owner._pin, max_total_bytes=64*1024*1024)


def test_flat_export_nonce_lease_is_single_use_even_on_refusal(flat_consumer_case):
    _, owner, _, snapshot, _, _ = flat_consumer_case
    meta, blocks = _export_fixture(owner, snapshot)
    authorization = session.create_flat_export_authorization(enabled=True, expected_nonce='1'*64,
        expected_identifier_policy_sha256=content._sha(snapshot['identifier_policy_binding'][0]))
    lease = authorization.begin()
    arguments = dict(enabled=True, metadata_bytes=content._canonical(meta), blocks=blocks, lease=lease,
        bundle_root=owner._root, expected_manifest_sha256=owner._pin, max_total_bytes=64*1024*1024)
    session.prepare_flat_source_owner_from_export(**arguments)
    with pytest.raises(session.MindContentSessionRefused): session.prepare_flat_source_owner_from_export(**arguments)
    with pytest.raises(session.MindContentSessionRefused): authorization.begin()
    with pytest.raises(session.MindContentSessionRefused):
        session.create_flat_export_authorization(enabled=True, expected_nonce='1'*64,
            expected_identifier_policy_sha256=content._sha(snapshot['identifier_policy_binding'][0]))


def test_flat_export_failed_validation_consumes_lease(flat_consumer_case):
    _, owner, _, snapshot, _, _ = flat_consumer_case
    meta, blocks = _export_fixture(owner, snapshot)
    authorization = session.create_flat_export_authorization(enabled=True, expected_nonce='1'*64,
        expected_identifier_policy_sha256=content._sha(snapshot['identifier_policy_binding'][0]))
    lease = authorization.begin()
    arguments = dict(enabled=True, metadata_bytes=content._canonical(dict(meta, nonce='2'*64)),
        blocks=blocks, lease=lease, bundle_root=owner._root, expected_manifest_sha256=owner._pin,
        max_total_bytes=64*1024*1024)
    with pytest.raises(session.MindContentSessionRefused): session.prepare_flat_source_owner_from_export(**arguments)
    arguments['metadata_bytes'] = content._canonical(meta)
    with pytest.raises(session.MindContentSessionRefused): session.prepare_flat_source_owner_from_export(**arguments)


def test_flat_export_nonce_reservation_is_atomic_and_bounded(monkeypatch):
    monkeypatch.setattr(session, '_EXPORT_NONCE_STATE', {'lock':threading.Lock(), 'used':set()})
    monkeypatch.setattr(session, '_EXPORT_NONCE_LIMIT', 1)
    def reserve():
        try:
            session.create_flat_export_authorization(enabled=True, expected_nonce='1'*64,
                expected_identifier_policy_sha256='a'*64)
            return True
        except session.MindContentSessionRefused:
            return False
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(lambda _: reserve(), range(2))) == [False, True]
    with pytest.raises(session.MindContentSessionRefused):
        session.create_flat_export_authorization(enabled=True, expected_nonce='2'*64,
            expected_identifier_policy_sha256='a'*64)


def test_flat_export_alias_imports_share_one_nonce_registry(monkeypatch):
    state = {'lock': threading.Lock(), 'used': set()}
    monkeypatch.setitem(vars(builtins), '_wd_mind_flat_export_nonce_registry_v1', state)
    modules = []
    for name in ('session_alias_a', 'session_alias_b'):
        spec = importlib.util.spec_from_file_location(name, session.__file__)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        modules.append(module)
    modules[0].create_flat_export_authorization(enabled=True, expected_nonce='1'*64,
        expected_identifier_policy_sha256='a'*64)
    with pytest.raises(modules[1].MindContentSessionRefused):
        modules[1].create_flat_export_authorization(enabled=True, expected_nonce='1'*64,
            expected_identifier_policy_sha256='a'*64)


def _synthetic_export_wire(meta, blocks, key, *, omit_final=False):
    nonce = bytes.fromhex(meta['nonce'])
    digest = hashlib.sha256()
    sequence = 0
    chunks = []
    data_count = 0
    payload_total = 0
    def frame(kind, payload):
        nonlocal sequence
        header = struct.pack('>4sB3xII', b'MEX1', ord(kind), sequence, len(payload))
        mac = hmac.new(key, b'wd.mind-export-frame.v1\0' + nonce + header + payload, hashlib.sha256).digest()
        sequence += 1
        chunks.append(header + payload + mac)
    metadata = content._canonical(meta)
    digest.update(metadata)
    payload_total += len(metadata)
    frame('M', metadata)
    for _, block in blocks:
        for pos in range(0, len(block), 97):
            value = block[pos:pos+97]
            frame('D', value)
            digest.update(value)
            payload_total += len(value)
            data_count += 1
    if not omit_final:
        frame('F', struct.pack('>IQ32s', data_count, payload_total, digest.digest()))
    return b''.join(chunks)


def _rewrite_signed_export_frame(wire, index, key, nonce, *, sequence=None, reserved=None, payload_edit=None):
    offset = 0
    for _ in range(index):
        length = struct.unpack('>I', wire[offset+12:offset+16])[0]
        offset += 16 + length + 32
    header = bytearray(wire[offset:offset+16])
    length = struct.unpack('>I', header[12:16])[0]
    value = bytearray(wire[offset+16:offset+16+length])
    if sequence is not None: header[8:12] = struct.pack('>I', sequence)
    if reserved is not None: header[5] = reserved
    if payload_edit is not None: value[payload_edit] ^= 1
    raw_header, raw_value = bytes(header), bytes(value)
    mac = hmac.new(key, b'wd.mind-export-frame.v1\0' + bytes.fromhex(nonce) + raw_header + raw_value,
                   hashlib.sha256).digest()
    return wire[:offset] + raw_header + raw_value + mac + wire[offset+16+length+32:]


@pytest.mark.parametrize('fault', ['none','off','mac','truncated','missing_final','trailing',
                                   'header_length','wire_budget','metadata_budget','timeout'])
def test_flat_export_stream_framing_fails_closed(flat_consumer_case, monkeypatch, fault):
    _, owner, proposals, snapshot, _, response = flat_consumer_case
    meta, blocks = _export_fixture(owner, snapshot)
    key = b'B'*32
    wire = _synthetic_export_wire(meta, blocks, key, omit_final=fault == 'missing_final')
    if fault == 'mac': wire = wire[:-1] + bytes([wire[-1] ^ 1])
    if fault == 'truncated': wire = wire[:-4]
    if fault == 'trailing': wire += b'x'
    if fault == 'header_length': wire = wire[:12] + struct.pack('>I', 0xffffffff) + wire[16:]
    opened, requests = [], []
    class Stream(io.BytesIO):
        def read(self, n=-1):
            requests.append(n)
            assert 0 <= n <= 65488
            return super().read(n)
    def open_read():
        opened.append(True)
        return Stream(wire)
    if fault == 'timeout':
        ticks = iter([0.0, 0.0, 2.0])
        monkeypatch.setattr(session.time, 'monotonic', lambda: next(ticks, 2.0))
    authorization = session.create_flat_export_authorization(enabled=True, expected_nonce='1'*64,
        expected_identifier_policy_sha256=content._sha(snapshot['identifier_policy_binding'][0]))
    arguments = dict(enabled=fault != 'off', authorization=authorization, open_read=open_read, channel_key=key,
        bundle_root=owner._root, expected_manifest_sha256=owner._pin,
        max_total_bytes=1 if fault == 'metadata_budget' else 64*1024*1024,
        max_wire_bytes=1 if fault == 'wire_budget' else 64*1024*1024, timeout_seconds=1)
    if fault != 'none':
        with pytest.raises(session.MindContentSessionRefused): session.read_flat_owner_stream(**arguments)
        if fault == 'off': assert not opened
        if fault == 'header_length': assert requests == [16]
        if fault == 'wire_budget': assert requests == []
        return
    rebuilt = session.read_flat_owner_stream(**arguments)
    result = session.query_flat_source_candidates(enabled=True, owner=rebuilt, ipc_bytes=response(),
        bundle_root=owner._root, expected_manifest_sha256=owner._pin,
        expected_nonce='1'*64, lane='codex-lead-1', scope='shared')
    assert result['authority'] == 'none' and result['index_match'] == 'UNKNOWN'
    assert [item['source_event_sha256'] for item in result['results']] == [p['source_event_sha256'] for p in proposals]


@pytest.mark.parametrize('fault', ['sequence','reserved','final_digest','policy','reuse','close_timeout'])
def test_flat_export_stream_signed_frames_and_lifecycle(flat_consumer_case, monkeypatch, fault):
    _, owner, _, snapshot, _, _ = flat_consumer_case
    identifier = content._canonical({'schema':'wd.mind-identifier-policy.v2', 'identifiers':[]})
    meta, blocks = _export_fixture(owner, snapshot, identifier_override=identifier if fault == 'policy' else None)
    key = b'B'*32
    wire = _synthetic_export_wire(meta, blocks, key)
    if fault == 'sequence': wire = _rewrite_signed_export_frame(wire, 1, key, meta['nonce'], sequence=77)
    if fault == 'reserved': wire = _rewrite_signed_export_frame(wire, 0, key, meta['nonce'], reserved=1)
    if fault == 'final_digest':
        position = 0
        count = 0
        while position < len(wire):
            length = struct.unpack('>I', wire[position+12:position+16])[0]
            position += 16 + length + 32
            count += 1
        wire = _rewrite_signed_export_frame(wire, count-1, key, meta['nonce'], payload_edit=12)
    clock = [0.0]
    class Stream(io.BytesIO):
        def close(self):
            if fault == 'close_timeout': clock[0] = 2.0
            super().close()
    if fault == 'close_timeout': monkeypatch.setattr(session.time, 'monotonic', lambda: clock[0])
    opened = []
    def open_read():
        opened.append(True)
        return Stream(wire)
    authorization = session.create_flat_export_authorization(enabled=True, expected_nonce='1'*64,
        expected_identifier_policy_sha256=content._sha(snapshot['identifier_policy_binding'][0]))
    arguments = dict(enabled=True, authorization=authorization, open_read=open_read, channel_key=key,
        bundle_root=owner._root, expected_manifest_sha256=owner._pin, max_total_bytes=64*1024*1024,
        max_wire_bytes=64*1024*1024, timeout_seconds=1)
    if fault == 'reuse':
        session.read_flat_owner_stream(**arguments)
        with pytest.raises(session.MindContentSessionRefused): session.read_flat_owner_stream(**arguments)
        assert len(opened) == 1
    else:
        with pytest.raises(session.MindContentSessionRefused): session.read_flat_owner_stream(**arguments)


def test_flat_export_stream_elapsed_deadline_cannot_interrupt_blocked_sync_read(flat_consumer_case):
    """The sync reader stays blocked until released, then refuses its late result.

    This is not an I/O cancellation, process cleanup, or owner-release proof.
    A real Windows caller must arrange cancellable I/O or retain the owner in HOLD.
    """
    _, owner, _, snapshot, _, _ = flat_consumer_case
    meta, blocks = _export_fixture(owner, snapshot)
    key = b'B'*32
    wire = _synthetic_export_wire(meta, blocks, key)
    entered, release, done = threading.Event(), threading.Event(), threading.Event()
    class BlockingStream(io.BytesIO):
        def read(self, count=-1):
            entered.set()
            assert release.wait(5)
            return super().read(count)
    authorization = session.create_flat_export_authorization(enabled=True, expected_nonce='1'*64,
        expected_identifier_policy_sha256=content._sha(snapshot['identifier_policy_binding'][0]))
    result = []
    def run():
        try:
            session.read_flat_owner_stream(enabled=True, authorization=authorization,
                open_read=lambda: BlockingStream(wire), channel_key=key, bundle_root=owner._root,
                expected_manifest_sha256=owner._pin, max_total_bytes=64*1024*1024,
                max_wire_bytes=64*1024*1024, timeout_seconds=0.05)
        except session.MindContentSessionRefused:
            result.append('refused')
        finally:
            done.set()
    worker = threading.Thread(target=run)
    worker.start()
    try:
        assert entered.wait(5)
        assert not done.wait(0.15)
    finally:
        release.set()
        worker.join(5)
    assert not worker.is_alive() and result == ['refused']


@pytest.mark.parametrize('late', ['preflight', 'open'])
def test_flat_export_stream_checks_deadline_before_and_after_open(flat_consumer_case, monkeypatch, late):
    _, owner, _, snapshot, _, _ = flat_consumer_case
    meta, blocks = _export_fixture(owner, snapshot)
    key = b'B'*32
    wire = _synthetic_export_wire(meta, blocks, key)
    clock, opened, reads = [0.0], [], []
    monkeypatch.setattr(session.time, 'monotonic', lambda: clock[0])
    def pin(root, pin):
        if late == 'preflight': clock[0] = 2.0
    monkeypatch.setattr(session, '_pin_flat_session', pin)
    class Stream(io.BytesIO):
        def read(self, size=-1):
            reads.append(size)
            return super().read(size)
    def open_read():
        opened.append(True)
        if late == 'open': clock[0] = 2.0
        return Stream(wire)
    authorization = session.create_flat_export_authorization(enabled=True, expected_nonce='1'*64,
        expected_identifier_policy_sha256=content._sha(snapshot['identifier_policy_binding'][0]))
    with pytest.raises(session.MindContentSessionRefused):
        session.read_flat_owner_stream(enabled=True, authorization=authorization, open_read=open_read,
            channel_key=key, bundle_root=owner._root, expected_manifest_sha256=owner._pin,
            max_total_bytes=64*1024*1024, max_wire_bytes=64*1024*1024, timeout_seconds=1)
    assert opened == ([] if late == 'preflight' else [True])
    assert reads == []


@pytest.mark.parametrize('phase', ['read', 'close'])
def test_flat_export_stream_cancellation_scrubs_key_and_preserves_signal(flat_consumer_case, phase):
    _, owner, _, snapshot, _, _ = flat_consumer_case
    meta, blocks = _export_fixture(owner, snapshot)
    key = b'B'*32
    wire = _synthetic_export_wire(meta, blocks, key)
    class HostileCancel(asyncio.CancelledError):
        constructions = 0
        def __new__(cls, *args):
            cls.constructions += 1
            if cls.constructions > 1: raise RuntimeError('unexpected signal constructor')
            return super().__new__(cls)
    signal = HostileCancel()
    class Stream(io.BytesIO):
        def read(self, size=-1):
            if phase == 'read': raise signal
            return super().read(size)
        def close(self):
            super().close()
            if phase == 'close': raise signal
    authorization = session.create_flat_export_authorization(enabled=True, expected_nonce='1'*64,
        expected_identifier_policy_sha256=content._sha(snapshot['identifier_policy_binding'][0]))
    try:
        session.read_flat_owner_stream(enabled=True, authorization=authorization, open_read=lambda: Stream(wire),
            channel_key=key, bundle_root=owner._root, expected_manifest_sha256=owner._pin,
            max_total_bytes=64*1024*1024, max_wire_bytes=64*1024*1024, timeout_seconds=5)
    except asyncio.CancelledError as error:
        assert type(error) is asyncio.CancelledError and error.__context__ is None
        trace = error.__traceback__
        frames = []
        while trace:
            if trace.tb_frame.f_code.co_filename == session.__file__: frames.append(trace.tb_frame)
            trace = trace.tb_next
        assert frames and all(repr(key) not in repr(frame.f_locals) for frame in frames)
    else:
        pytest.fail('expected cancellation')


def test_flat_consumer_is_default_off_before_any_ipc(flat_consumer_case):
    _, owner, _, _, _, response = flat_consumer_case
    with pytest.raises(session.MindContentSessionRefused):
        session.query_flat_source_candidates(owner=owner, ipc_bytes=response(), bundle_root=owner._root,
            expected_manifest_sha256=owner._pin, expected_nonce='1' * 64, lane='codex-lead-1', scope='shared')


def test_consumer_child_cli_refuses_before_access(monkeypatch, capsys):
    monkeypatch.setattr(session.sys, 'stdin', SimpleNamespace(read=lambda *a: pytest.fail('unexpected read')))
    assert session.main(['--mind-consumer-child']) == 2
    out = capsys.readouterr()
    assert out.out == '' and out.err == ''


@pytest.mark.parametrize('fault', ['none', 'off', 'manifest', 'policy', 'approval', 'lane', 'length', 'truncated'])
def test_parent_consumer_control_requires_separate_anchors_and_keeps_mcr_unread(monkeypatch, fault):
    manifest, policy, approval, nonce, key = [bytes([n])*32 for n in range(1, 6)]
    lane = b'codex-lead-1'
    data = b'MCC1' + manifest + policy + approval + nonce + key + bytes([len(lane)]) + lane + b'MCR1'
    if fault == 'length': data = data[:164] + b'\x00' + data[165:]
    if fault == 'truncated': data = data[:100]
    stream = io.BytesIO(data)
    args = dict(enabled=fault != 'off', stream=stream, expected_manifest_sha256=('a'*64 if fault == 'manifest' else manifest.hex()),
        expected_identifier_policy_sha256=('b'*64 if fault == 'policy' else policy.hex()),
        expected_approval_reference_sha256=('c'*64 if fault == 'approval' else approval.hex()),
        expected_lane=('fable-5' if fault == 'lane' else lane.decode()), deadline_monotonic=time.monotonic()+5)
    # Unverified parent must fail before touching control bytes.
    with pytest.raises(session.MindContentSessionRefused):
        session._read_parent_consumer_control(**args)
    assert stream.tell() == 0
    monkeypatch.setattr(session, '_require_trusted_consumer_parent', lambda: None)
    if fault != 'none':
        with pytest.raises(session.MindContentSessionRefused):
            session._read_parent_consumer_control(**args)
    else:
        box = session._read_parent_consumer_control(**args)
        assert box._key == key and box._nonce == nonce.hex() and box._lane == lane.decode()
        assert stream.read(4) == b'MCR1'
        assert repr(key) not in repr(box)


@pytest.mark.parametrize('fault', ['none', 'nonce', 'candidate'])
def test_consumer_child_entry_fixtures_never_publish_unverified_content(flat_consumer_case, monkeypatch, fault):
    _, owner, proposals, snapshot, _, response = flat_consumer_case
    meta, blocks = _export_fixture(owner, snapshot)
    key = b'B'*32
    wire = _synthetic_export_wire(meta, blocks, key)
    policy = content._sha(snapshot['identifier_policy_binding'][0])
    lane = b'codex-lead-1'
    frame = json.loads(response())
    frame['scope'] = 'own'
    if fault == 'candidate': frame['candidates'][0]['source_event_sha256'] = '0'*64
    payload = json.dumps(frame, separators=(',', ':')).encode()
    control = (b'MCC1' + bytes.fromhex(owner._pin) + bytes.fromhex(policy) + bytes.fromhex('3'*64)
        + bytes.fromhex('1'*64) + key + bytes([len(lane)]) + lane
        + struct.pack('>4sI', b'MCR1', len(payload)) + payload)
    input_stream, output = io.BytesIO(control), io.BytesIO()
    args = dict(control_stream=input_stream, export_stream=io.BytesIO(wire), output_stream=output,
        bundle_root=owner._root, expected_manifest_sha256=owner._pin,
        expected_identifier_policy_sha256=policy, expected_approval_reference_sha256='3'*64,
        expected_nonce=('2' if fault == 'nonce' else '1')*64, expected_lane=lane.decode(),
        max_total_bytes=64*1024*1024, max_wire_bytes=64*1024*1024, deadline_monotonic=time.monotonic()+30)
    assert session._consumer_child_entry(**args) == 2
    assert input_stream.tell() == 0 and output.getvalue() == b''
    monkeypatch.setattr(session, '_require_trusted_consumer_parent', lambda: None)
    code = session._consumer_child_entry(**args)
    if fault == 'none':
        result = json.loads(output.getvalue())
        assert code == 0 and result['authority'] == 'none'
        assert [r['source_event_sha256'] for r in result['results']] == [p['source_event_sha256'] for p in proposals]
    else:
        assert code == 2 and output.getvalue() == b''


@pytest.mark.parametrize('lane_name', ['claude-rco-1', 'claude-rco-2'])
@pytest.mark.parametrize('fault', ['none', 'nonce', 'candidate', 'scope'])
def test_rco_consumer_reads_only_verified_build_lane_content(flat_consumer_case, monkeypatch, lane_name, fault):
    """Fixture-only parent trust; scope changes never establish real parent provenance."""
    _, owner, proposals, snapshot, _, response = flat_consumer_case
    meta, blocks = _export_fixture(owner, snapshot)
    key = b'B' * 32
    wire = _synthetic_export_wire(meta, blocks, key)
    policy = content._sha(snapshot['identifier_policy_binding'][0])
    lane = lane_name.encode('ascii')
    frame = json.loads(response())
    frame['lane'] = lane_name
    frame['scope'] = 'own' if fault == 'scope' else 'shared'
    if fault == 'candidate':
        frame['candidates'][0]['source_event_sha256'] = '0' * 64
    payload = json.dumps(frame, separators=(',', ':')).encode()
    control = (b'MCC1' + bytes.fromhex(owner._pin) + bytes.fromhex(policy) + bytes.fromhex('3' * 64)
        + bytes.fromhex('1' * 64) + key + bytes([len(lane)]) + lane
        + struct.pack('>4sI', b'MCR1', len(payload)) + payload)
    source, output = io.BytesIO(control), io.BytesIO()
    args = dict(control_stream=source, export_stream=io.BytesIO(wire), output_stream=output,
        bundle_root=owner._root, expected_manifest_sha256=owner._pin,
        expected_identifier_policy_sha256=policy, expected_approval_reference_sha256='3' * 64,
        expected_nonce=('2' if fault == 'nonce' else '1') * 64, expected_lane=lane_name,
        max_total_bytes=64*1024*1024, max_wire_bytes=64*1024*1024, deadline_monotonic=time.monotonic()+30)
    monkeypatch.setattr(session, '_require_trusted_consumer_parent', lambda: None)
    code = session._consumer_child_entry(**args)
    if fault == 'none':
        result = json.loads(output.getvalue())
        assert code == 0 and result['authority'] == 'none' and result['lane'] == lane_name
        assert all(hit['source_agent'] in content._BUILD_LANES for hit in result['results'])
        assert [hit['source_event_sha256'] for hit in result['results']] == [p['source_event_sha256'] for p in proposals]
    else:
        assert code == 2 and output.getvalue() == b''


@pytest.mark.parametrize('mismatch', ['none', 'nonce', 'lane', 'manifest', 'policy', 'approval'])
def test_parent_consumer_pipeline_is_private_gate_only(flat_consumer_case, monkeypatch, mismatch):
    _, owner, proposals, snapshot, _, response = flat_consumer_case
    meta, blocks = _export_fixture(owner, snapshot)
    wire = _synthetic_export_wire(meta, blocks, b'B'*32)
    frame = json.loads(response())
    frame['scope'] = 'own'
    payload = json.dumps(frame, separators=(',', ':')).encode()
    control = session._ConsumerKeyBox()
    control._key = b'B'*32
    control._nonce, control._lane = '1'*64, 'codex-lead-1'
    control._manifest, control._policy, control._approval = owner._pin, content._sha(snapshot['identifier_policy_binding'][0]), '3'*64
    args = dict(enabled=True, open_export=lambda: io.BytesIO(wire),
        open_candidate=lambda: io.BytesIO(struct.pack('>4sI', b'MCR1', len(payload)) + payload),
        control=control, bundle_root=owner._root, expected_manifest_sha256=owner._pin,
        expected_identifier_policy_sha256=content._sha(snapshot['identifier_policy_binding'][0]),
        expected_approval_reference_sha256='3'*64,
        expected_nonce='1'*64, lane='codex-lead-1', max_total_bytes=64*1024*1024,
        max_wire_bytes=64*1024*1024, deadline_monotonic=time.monotonic()+30)
    with pytest.raises(session.MindContentSessionRefused):
        session._run_parent_consumer(**args)
    # No production-provider injection: this replacement exists only in the fixture.
    monkeypatch.setattr(session, '_require_trusted_consumer_parent', lambda: None)
    if mismatch == 'nonce': control._nonce = '2'*64
    if mismatch == 'lane': control._lane = 'fable-5'
    if mismatch == 'manifest': control._manifest = 'a'*64
    if mismatch == 'policy': control._policy = 'b'*64
    if mismatch == 'approval': control._approval = 'c'*64
    if mismatch != 'none':
        with pytest.raises(session.MindContentSessionRefused):
            session._run_parent_consumer(**args)
        return
    result = session._run_parent_consumer(**args)
    assert result['authority'] == 'none' and result['sender_authentication'] == 'UNKNOWN'
    assert [r['source_event_sha256'] for r in result['results']] == [p['source_event_sha256'] for p in proposals]


def test_parent_consumer_first_line_interrupt_does_not_render_key_in_traceback():
    secret = b'synthetic-trace-key-32-bytes!!!!'
    control = session._ConsumerKeyBox()
    control._key = secret
    def trace(frame, event, arg):
        if frame.f_code is session._run_parent_consumer.__code__ and event == 'line':
            sys.settrace(None)
            raise KeyboardInterrupt()
        return trace
    try:
        sys.settrace(trace)
        session._run_parent_consumer(enabled=True, control=control)
    except KeyboardInterrupt as exc:
        sys.settrace(None)
        captured = traceback.TracebackException.from_exception(exc, capture_locals=True)
        target = [frame for frame in captured.stack if frame.name == '_run_parent_consumer']
        assert target and all(repr(secret) not in value for frame in target for value in (frame.locals or {}).values())
    else:
        pytest.fail('expected interruption')
    finally:
        sys.settrace(None)


def test_parent_consumer_decoder_first_line_interrupt_does_not_render_key(flat_consumer_case, monkeypatch):
    _, owner, _, snapshot, _, _ = flat_consumer_case
    control = session._ConsumerKeyBox()
    control._key = b'synthetic-key-32-synthetic-key!!'
    control._nonce, control._lane = '1'*64, 'codex-lead-1'
    control._manifest, control._policy, control._approval = owner._pin, content._sha(snapshot['identifier_policy_binding'][0]), '3'*64
    secret = control._key
    monkeypatch.setattr(session, '_require_trusted_consumer_parent', lambda: None)
    def trace(frame, event, arg):
        if frame.f_code is session.read_flat_owner_stream.__code__ and event == 'line':
            sys.settrace(None)
            raise KeyboardInterrupt()
        return trace
    try:
        sys.settrace(trace)
        session._run_parent_consumer(enabled=True, control=control, open_export=lambda: io.BytesIO(b''),
            open_candidate=lambda: io.BytesIO(b''), bundle_root=owner._root, expected_manifest_sha256=owner._pin,
            expected_identifier_policy_sha256=content._sha(snapshot['identifier_policy_binding'][0]),
            expected_approval_reference_sha256='3'*64,
            expected_nonce='1'*64, lane='codex-lead-1', max_total_bytes=1024, max_wire_bytes=1024,
            deadline_monotonic=time.monotonic()+5)
    except KeyboardInterrupt as exc:
        sys.settrace(None)
        captured = traceback.TracebackException.from_exception(exc, capture_locals=True)
        target = [frame for frame in captured.stack if frame.name in ('_run_parent_consumer', 'read_flat_owner_stream')]
        assert target and all(repr(secret) not in value for frame in target for value in (frame.locals or {}).values())
    else:
        pytest.fail('expected interruption')
    finally:
        sys.settrace(None)


@pytest.mark.parametrize('fault', ['none', 'disabled', 'nonce', 'oversize', 'empty', 'late', 'close', 'magic', 'truncated', 'trailing'])
def test_candidate_stream_uses_local_gate_and_refuses_partial_or_untrusted_data(flat_consumer_case, fault):
    _, owner, proposals, _, _, response = flat_consumer_case
    raw = response()
    if fault == 'empty': raw = b''
    declared = 2049 if fault == 'oversize' else len(raw)
    raw = struct.pack('>4sI', b'BAD1' if fault == 'magic' else b'MCR1', declared) + raw
    if fault == 'truncated': raw = raw[:-1]
    if fault == 'trailing': raw += b'X'
    class Stream(io.BytesIO):
        def read(self, n=-1):
            assert 0 < n <= 2049  # the limit is set before requesting bytes
            return super().read(min(n, 17))
        def close(self):
            super().close()
            if fault == 'close': raise ValueError('synthetic')
    opened = []
    def open_stream():
        stream = Stream(raw)
        opened.append(stream)
        return stream
    kwargs = dict(enabled=fault != 'disabled', owner=owner, open_read=open_stream,
        bundle_root=owner._root, expected_manifest_sha256=owner._pin,
        expected_nonce=('2' if fault == 'nonce' else '1') * 64, lane='codex-lead-1', scope='shared',
        deadline_monotonic=time.monotonic() + (-1 if fault == 'late' else 5))
    if fault == 'none':
        result = session.resolve_flat_candidate_stream(**kwargs)
        assert result['authority'] == 'none' and result['sender_authentication'] == 'UNKNOWN'
        assert result['index_match'] == 'UNKNOWN'
        assert [r['source_event_sha256'] for r in result['results']] == [p['source_event_sha256'] for p in proposals]
    else:
        with pytest.raises(session.MindContentSessionRefused):
            session.resolve_flat_candidate_stream(**kwargs)
    if fault in ('disabled', 'late'):
        assert not opened
    else:
        assert opened and opened[0].closed


@pytest.mark.parametrize('phase', ['open', 'read', 'gate', 'close'])
def test_candidate_stream_refuses_late_return_without_claiming_interruptibility(flat_consumer_case, monkeypatch, phase):
    _, owner, _, _, _, response = flat_consumer_case
    clock = SimpleNamespace(value=0.0)
    monkeypatch.setattr(session, 'time', SimpleNamespace(monotonic=lambda: clock.value))
    original_gate = session.query_flat_source_candidates
    def gate(**kwargs):
        result = original_gate(**kwargs)
        if phase == 'gate': clock.value = 2.0
        return result
    monkeypatch.setattr(session, 'query_flat_source_candidates', gate)
    class Stream(io.BytesIO):
        def read(self, n=-1):
            value = super().read(n)
            if phase == 'read': clock.value = 2.0
            return value
        def close(self):
            super().close()
            if phase == 'close': clock.value = 2.0
    payload = response()
    stream = Stream(struct.pack('>4sI', b'MCR1', len(payload)) + payload)
    def open_stream():
        if phase == 'open': clock.value = 2.0
        return stream
    with pytest.raises(session.MindContentSessionRefused):
        session.resolve_flat_candidate_stream(enabled=True, owner=owner, open_read=open_stream,
            bundle_root=owner._root, expected_manifest_sha256=owner._pin, expected_nonce='1'*64,
            lane='codex-lead-1', scope='shared', deadline_monotonic=1.0)
    assert stream.closed


@pytest.mark.parametrize('phase', ['read', 'gate', 'close'])
@pytest.mark.parametrize('signal', [KeyboardInterrupt, asyncio.CancelledError])
def test_candidate_stream_preserves_cancellation_and_closes(flat_consumer_case, monkeypatch, phase, signal):
    _, owner, _, _, _, response = flat_consumer_case
    payload = response()
    class Stream(io.BytesIO):
        def read(self, n=-1):
            if phase == 'read': raise signal()
            return super().read(n)
        def close(self):
            super().close()
            if phase == 'close': raise signal()
    stream = Stream(struct.pack('>4sI', b'MCR1', len(payload)) + payload)
    if phase == 'gate':
        def interrupted(**kwargs): raise signal()
        monkeypatch.setattr(session, 'query_flat_source_candidates', interrupted)
    with pytest.raises(signal) as error:
        session.resolve_flat_candidate_stream(enabled=True, owner=owner, open_read=lambda: stream,
            bundle_root=owner._root, expected_manifest_sha256=owner._pin, expected_nonce='1'*64,
            lane='codex-lead-1', scope='shared', deadline_monotonic=time.monotonic()+5)
    assert type(error.value) is signal and error.value.__context__ is None and stream.closed


@pytest.mark.parametrize('turn', ['first', 'resume'])
def test_flat_consumer_resolves_source_as_untrusted_data(flat_consumer_case, turn):
    _, owner, proposals, _, _, response = flat_consumer_case
    def run():
        return session.query_flat_source_candidates(enabled=True, owner=owner, ipc_bytes=response(),
            bundle_root=owner._root, expected_manifest_sha256=owner._pin,
            expected_nonce='1' * 64, lane='codex-lead-1', scope='shared')
    if turn == 'resume': run()
    result = run()
    assert result['authority'] == 'none' and result['sender_authentication'] == 'UNKNOWN'
    assert result['index_match'] == 'UNKNOWN' and result['fleet_sha256'] == 'a' * 64
    assert set(result) == {'schema', 'authority', 'lane', 'fleet_sha256', 'sender_authentication',
                           'index_match', 'same_user_aba', 'current_status_freshness', 'results'}
    assert result['current_status_freshness'] == 'UNKNOWN'
    assert [item['source_event_sha256'] for item in result['results']] == [p['source_event_sha256'] for p in proposals]
    assert all(item['event_age_seconds_upper_bound'] < 900 for item in result['results'])


@pytest.mark.parametrize('offset_seconds', [900, 1200, 86400])
def test_flat_consumer_delivers_historical_hits_with_age_and_unknown_freshness(
        flat_consumer_case, monkeypatch, offset_seconds):
    from datetime import timedelta
    _, owner, proposals, _, clock, response = flat_consumer_case
    # Advance the session clock independently to exercise its final delivery
    # check while keeping the real source owner's verification in place.
    monkeypatch.setattr(session, 'datetime', SimpleNamespace(
        now=lambda tz: clock.value + timedelta(seconds=offset_seconds - 120)))
    original_resolve = type(owner).resolve
    resolved = []
    def tracked_resolve(self, **kwargs):
        resolved.append(1)
        return original_resolve(self, **kwargs)
    monkeypatch.setattr(type(owner), 'resolve', tracked_resolve)
    result = session.query_flat_source_candidates(enabled=True, owner=owner, ipc_bytes=response(),
        bundle_root=owner._root, expected_manifest_sha256=owner._pin,
        expected_nonce='1' * 64, lane='codex-lead-1', scope='shared')
    assert len(resolved) == 2
    assert result['current_status_freshness'] == 'UNKNOWN'
    assert [item['source_event_sha256'] for item in result['results']] == [p['source_event_sha256'] for p in proposals]
    for item in result['results']:
        assert item['event_time_utc_floor']
        assert item['event_age_seconds_upper_bound'] == offset_seconds


def test_flat_consumer_rejects_future_event_at_final_delivery(flat_consumer_case, monkeypatch):
    from datetime import timedelta
    _, owner, _, _, clock, response = flat_consumer_case
    monkeypatch.setattr(session, 'datetime', SimpleNamespace(now=lambda tz: clock.value - timedelta(minutes=20)))
    with pytest.raises(session.MindContentSessionRefused):
        session.query_flat_source_candidates(enabled=True, owner=owner, ipc_bytes=response(),
            bundle_root=owner._root, expected_manifest_sha256=owner._pin,
            expected_nonce='1' * 64, lane='codex-lead-1', scope='shared')


def test_flat_consumer_resolves_historical_source_without_a_recent_window(flat_consumer_case):
    from datetime import timedelta
    _, owner, proposals, _, clock, response = flat_consumer_case
    clock.value += timedelta(minutes=20)
    result = session.query_flat_source_candidates(enabled=True, owner=owner, ipc_bytes=response(),
        bundle_root=owner._root, expected_manifest_sha256=owner._pin,
        expected_nonce='1' * 64, lane='codex-lead-1', scope='shared')
    assert result['current_status_freshness'] == 'UNKNOWN'
    assert [item['source_event_sha256'] for item in result['results']] == [p['source_event_sha256'] for p in proposals]
    assert all(item['event_age_seconds_upper_bound'] >= 900 for item in result['results'])


def test_flat_consumer_rejects_changed_historical_source(flat_consumer_case, monkeypatch):
    from datetime import timedelta
    _, owner, proposals, snapshot, clock, response = flat_consumer_case
    monkeypatch.setattr(session, 'datetime', SimpleNamespace(now=lambda tz: clock.value + timedelta(days=1)))
    path = Path(snapshot['messages']['source_path'])
    rows = path.read_bytes().splitlines(keepends=True)
    index = next(i for i, row in enumerate(rows)
                 if content._sha(content._canonical(json.loads(row))) == proposals[0]['source_event_sha256'])
    rows[index] = rows[index].replace(b'"message":', b'"messagE":', 1)
    path.write_bytes(b''.join(rows))
    with pytest.raises(session.MindContentSessionRefused):
        session.query_flat_source_candidates(enabled=True, owner=owner, ipc_bytes=response(),
            bundle_root=owner._root, expected_manifest_sha256=owner._pin,
            expected_nonce='1' * 64, lane='codex-lead-1', scope='shared')


@pytest.mark.parametrize('fault', ['ref', 'source', 'nonce', 'lane', 'scope', 'duplicate', 'oversize',
                                   'duplicate_json_key', 'row', 'fleet', 'encoder'])
def test_flat_consumer_refuses_untrusted_or_changed_inputs(flat_consumer_case, monkeypatch, fault):
    package, owner, proposals, snapshot, clock, response = flat_consumer_case
    value = json.loads(response())
    if fault == 'ref': value['candidates'][0]['ref_sha256'] = '0' * 64
    if fault == 'source': value['candidates'][0]['source_event_sha256'] = '0' * 64
    if fault == 'nonce': value['nonce'] = '2' * 64
    if fault == 'lane': value['lane'] = 'fable-5'
    if fault == 'scope': value['scope'] = 'own'
    if fault == 'duplicate': value['candidates'].append(dict(value['candidates'][0]))
    if fault == 'row':
        path = Path(snapshot['messages']['source_path'])
        rows = path.read_bytes().splitlines(keepends=True)
        index = next(i for i, row in enumerate(rows)
                     if content._sha(content._canonical(json.loads(row))) == proposals[0]['source_event_sha256'])
        rows[index] = rows[index].replace(b'"message":', b'"messagE":', 1)
        path.write_bytes(b''.join(rows))
    if fault == 'fleet': monkeypatch.setattr(package, '_consumer_bundle_controls', lambda root, pin: None)
    if fault == 'encoder': monkeypatch.setattr(package, '_consumer_flat_encoder', lambda root, pin: b'changed')
    raw = json.dumps(value, separators=(',', ':')).encode()
    if fault == 'oversize': raw += b' ' * 2049
    if fault == 'duplicate_json_key': raw = b'{"nonce":"1","nonce":"2"}'
    with pytest.raises(session.MindContentSessionRefused):
        session.query_flat_source_candidates(enabled=True, owner=owner, ipc_bytes=raw,
            bundle_root=owner._root, expected_manifest_sha256=owner._pin,
            expected_nonce='1' * 64, lane='codex-lead-1', scope='shared')


@pytest.mark.parametrize('fault', ['none', 'manifest', 'session_code', 'origin'])
def test_flat_consumer_independently_checks_pinned_session_code(tmp_path, monkeypatch, fault):
    root = tmp_path / 'bundle'
    source = root / 'tools-bootstrap' / 'tools' / 'wd_bridge_mind_session.py'
    source.parent.mkdir(parents=True)
    source.write_bytes(Path(session.__file__).read_bytes())
    manifest = {'schema_version': 1, 'files': {'tools-bootstrap/tools/wd_bridge_mind_session.py':
                hashlib.sha256(source.read_bytes()).hexdigest()}}
    raw = json.dumps(manifest, separators=(',', ':')).encode()
    (root / 'deployment-manifest.json').write_bytes(raw)
    pin = hashlib.sha256(raw).hexdigest()
    monkeypatch.setattr(session, '__file__', str(source if fault != 'origin' else root / 'other.py'))
    if fault == 'manifest': pin = '0' * 64
    if fault == 'session_code': source.write_bytes(b'# changed')
    if fault == 'none':
        session._pin_flat_session(str(root), pin)
    else:
        with pytest.raises((ValueError, OSError)):
            session._pin_flat_session(str(root), pin)


@pytest.mark.parametrize('fault', ['none', 'manifest', 'fleet', 'policy', 'session', 'gate', 'encoder', 'worker'])
def test_flat_consumer_synthetic_bundle_chain_keeps_pins(tmp_path, fault):
    """The actual pinned gate and session run together in a synthetic bundle."""
    import ast
    import subprocess
    gate_test = Path(__file__).with_name('test_wd_mind_content_snapshot_package.py')
    tree = ast.parse(gate_test.read_text(encoding='utf-8'))
    function = next(item for item in tree.body if isinstance(item, ast.FunctionDef)
                    and item.name == 'test_consumer_owner_real_synthetic_bundle_chain')
    assignment = next(item for item in function.body if isinstance(item, ast.Assign)
                      and any(isinstance(target, ast.Name) and target.id == 'script' for target in item.targets))
    script = ast.literal_eval(assignment.value)
    def replace_once(old, new):
        nonlocal script
        assert script.count(old) == 1
        script = script.replace(old, new)
    replace_once("paths = ['tools/__init__.py', ",
                 "paths = ['tools/wd_bridge_mind_session.py', 'tools/wd_bridge_mind_lane_read.py', 'tools/__init__.py', ")
    replace_once('from tools import wd_mind_content_snapshot_package as package\n',
                 'from tools import wd_mind_content_snapshot_package as package\nfrom tools import wd_bridge_mind_session as session\n')
    replace_once('package.datetime = Clock\n', 'package.datetime = Clock\nsession.datetime = Clock\n')
    replace_once("case = fixtures._semantic_case(reader, shared, all_tasks=True)\n", '''\
pins = {}
for name, expected in reader._REQUIRED_CODE.items():
    raw = (bootstrap / name).read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    normalized = hashlib.sha256(raw.replace(b"\\r\\n", b"\\n")).hexdigest()
    assert digest == expected or (b"\\r\\n" in raw and normalized == expected)
    pins[name] = digest
reader._REQUIRED_CODE = pins
case = fixtures._semantic_case(reader, shared, all_tasks=True)
''')
    probe = r'''
fault = sys.argv[3]
frame = reader._canonical({'schema':'wd.mind-pipe-candidates.v1', 'nonce':'1'*64,
    'lane':'codex-lead-1', 'scope':'shared', 'status':'CANDIDATES', 'candidates':proposals})
pins = {'fleet':bundle/'wd-fleet.json', 'policy':bundle/'wd-mind-message-policy.json',
        'session':bootstrap/'tools/wd_bridge_mind_session.py',
        'gate':bootstrap/'tools/wd_mind_content_snapshot_package.py',
        'encoder':bundle/'wd-mind-e5-encoder-binding.json', 'worker':worker}
if fault in pins: pins[fault].write_bytes(b'# changed')
pin = '0'*64 if fault == 'manifest' else reader._sha(manifest)
try:
    found = session.query_flat_source_candidates(enabled=True, owner=flat, ipc_bytes=frame,
        bundle_root=str(bundle), expected_manifest_sha256=pin, expected_nonce='1'*64,
        lane='codex-lead-1', scope='shared')
    assert fault == 'none' and found['results'] and found['authority'] == 'none'
    assert found['index_match'] == 'UNKNOWN' and found['sender_authentication'] == 'UNKNOWN'
    assert found['fleet_sha256'] == reader._sha(fleet)
    assert all(hit['event_age_seconds_upper_bound'] < 900 for hit in found['results'])
except session.MindContentSessionRefused:
    assert fault != 'none'
sys.exit(0)
'''
    replace_once("flat_result = flat.resolve(proposals=proposals, lane='codex-lead-1', scope='shared')\n",
                 probe + "\nflat_result = flat.resolve(proposals=proposals, lane='codex-lead-1', scope='shared')\n")
    result = subprocess.run([sys.executable, '-I', '-B', '-c', script, str(Path(__file__).resolve().parents[2]),
                             str(tmp_path), fault], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize('window', ['final_session_pin', 'final_encoder_pin'])
def test_flat_consumer_rechecks_source_after_final_pins(flat_consumer_case, monkeypatch, window):
    _, owner, proposals, snapshot, _, response = flat_consumer_case
    source = Path(snapshot['messages']['source_path'])
    calls = []
    def mutate_selected_row():
        rows = source.read_bytes().splitlines(keepends=True)
        index = next(i for i, row in enumerate(rows)
                     if content._sha(content._canonical(json.loads(row))) == proposals[0]['source_event_sha256'])
        changed = rows[index].replace(b'"message":', b'"messagE":', 1)
        assert changed != rows[index] and len(changed) == len(rows[index])
        rows[index] = changed
        source.write_bytes(b''.join(rows))
    if window == 'final_session_pin':
        def pin(root, digest):
            calls.append(1)
            if len(calls) == 2: mutate_selected_row()
        monkeypatch.setattr(session, '_pin_flat_session', pin)
    else:
        original = type(owner._gate)._check_delivery_binding
        def encoder_check(gate):
            result = original(gate)
            calls.append(1)
            # The first resolve checks the encoder twice; the session's final
            # explicit E5 check is the third invocation.
            if len(calls) == 3: mutate_selected_row()
            return result
        monkeypatch.setattr(type(owner._gate), '_check_delivery_binding', encoder_check)
    with pytest.raises(session.MindContentSessionRefused, match='^mind_session_content_refused$'):
        session.query_flat_source_candidates(enabled=True, owner=owner, ipc_bytes=response(),
            bundle_root=owner._root, expected_manifest_sha256=owner._pin,
            expected_nonce='1' * 64, lane='codex-lead-1', scope='shared')
    assert len(calls) >= (2 if window == 'final_session_pin' else 3)



def _entry():
    result = getattr(session, "query_content_session", None)
    assert callable(result), "default-off content session seam is not implemented"
    return result


def _refused(operation):
    with pytest.raises(ValueError) as caught:
        operation()
    assert type(caught.value).__name__ == "MindContentSessionRefused"
    assert str(caught.value) == TOKEN
    assert caught.value.__context__ is None


def _dummy_inputs():
    return dict({key: object() for key in INPUT_KEYS}, enabled=True)


def _providers():
    def forbidden(*args, **kwargs):
        pytest.fail("provider callback reached before full source verification")
    return (SimpleNamespace(identity={}, query=forbidden),
            SimpleNamespace(identity={}, load=forbidden))


@pytest.fixture
def dispatch(monkeypatch):
    calls = []

    def query(**inputs):
        calls.append(inputs)
        return {"schema": "wd.q4-content-snapshot-query.v2", "authority": "none",
                "status": "ABSTAIN", "provider_authentication": "UNKNOWN", "results": []}

    monkeypatch.setattr(content, "query_content_snapshot", query)
    return calls


@pytest.mark.parametrize("enabled", [False, None, 0, 1, "1", [], {}])
def test_content_session_requires_exact_true_before_query(enabled, dispatch):
    entry = _entry()
    _refused(lambda: entry(enabled=enabled, inputs=_dummy_inputs(), providers=_providers()))
    assert dispatch == []


def test_content_session_is_disabled_by_default(dispatch):
    entry = _entry()
    _refused(lambda: entry(inputs=_dummy_inputs(), providers=_providers()))
    assert dispatch == []


def test_content_session_environment_cannot_enable_it(monkeypatch, dispatch):
    entry = _entry()
    monkeypatch.setenv("WD_MIND_READ_ENABLED", "1")
    monkeypatch.setenv("WD_MIND_CONTENT_ENABLED", "1")
    _refused(lambda: entry(inputs=_dummy_inputs(), providers=_providers()))
    assert dispatch == []


@pytest.mark.parametrize("inputs", [None, {}, [], (), {"enabled": True}])
def test_content_session_missing_input_surface_has_no_query_callback(inputs, dispatch):
    entry = _entry()
    _refused(lambda: entry(enabled=True, inputs=inputs, providers=_providers()))
    assert dispatch == []


@pytest.mark.parametrize("fault", ["none", "list", "short", "long", "encoder_none", "index_none", "all_none"])
def test_content_session_requires_explicit_two_provider_tuple(fault, dispatch):
    entry = _entry()
    providers = _providers()
    providers = {
        "none": None, "list": list(providers), "short": providers[:1],
        "long": providers + (object(),), "encoder_none": (None, providers[1]),
        "index_none": (providers[0], None), "all_none": (None, None),
    }[fault]
    _refused(lambda: entry(enabled=True, inputs=_dummy_inputs(), providers=providers))
    assert dispatch == []


@pytest.mark.parametrize("fault", ["extra", "encoder_provider", "index_provider", "inner_disabled"])
def test_content_session_input_set_is_closed_and_unambiguous(fault, dispatch):
    entry = _entry()
    inputs = _dummy_inputs()
    if fault == "inner_disabled":
        inputs["enabled"] = False
    else:
        inputs[fault] = object()
    _refused(lambda: entry(enabled=True, inputs=inputs, providers=_providers()))
    assert dispatch == []


@pytest.mark.parametrize("missing", sorted(INPUT_KEYS))
def test_content_session_requires_every_explicit_binding(missing, dispatch):
    entry = _entry()
    inputs = _dummy_inputs()
    inputs.pop(missing)
    _refused(lambda: entry(enabled=True, inputs=inputs, providers=_providers()))
    assert dispatch == []


def test_content_session_forwards_exact_owned_inputs_without_defaults_or_mutation(dispatch):
    entry = _entry()
    inputs, providers = _dummy_inputs(), _providers()
    original = dict(inputs)
    result = entry(enabled=True, inputs=inputs, providers=providers)
    assert result["status"] == "ABSTAIN" and result["authority"] == "none"
    assert result["provider_authentication"] == "UNKNOWN"
    assert inputs == original and len(dispatch) == 1
    forwarded = dispatch[0]
    assert set(forwarded) == content._Q4_KEYS
    assert all(forwarded[key] is value for key, value in original.items())
    assert forwarded["encoder_provider"] is providers[0]
    assert forwarded["index_provider"] is providers[1]


@pytest.mark.parametrize("fault", ["reader_refusal", "unsafe_exception"])
def test_content_session_contains_refusals_without_exception_text_or_context(fault, monkeypatch):
    entry = _entry()

    def fail(**inputs):
        if fault == "reader_refusal":
            raise content.ContentQueryRefused("content_query_refused")
        raise RuntimeError("synthetic-private-provider-detail")

    monkeypatch.setattr(content, "query_content_snapshot", fail)
    _refused(lambda: entry(enabled=True, inputs=_dummy_inputs(), providers=_providers()))


def test_existing_status_cli_stays_status_only(monkeypatch, capsys, tmp_path, dispatch):
    # Positive pre-Source regression control: the old status CLI already works.
    agent, uuid = "codex-tools-1", "00000000-0000-4000-8000-000000000002"
    monkeypatch.setenv("WD_MIND_READ_ENABLED", "1")
    monkeypatch.setenv("AGENT_BRIDGE_AGENT", agent)
    monkeypatch.setenv("AGENT_BRIDGE_AGENT_UUID", uuid)
    monkeypatch.setenv("AGENT_BRIDGE_RUNTIME_ROOT", str(tmp_path / "synthetic-runtime"))
    monkeypatch.setattr(session, "_fleet_roster", lambda *args: {agent: uuid})
    monkeypatch.setattr(session.bridge_compact_view, "_read_view_rows", lambda *args: ([], "synthetic-cursor", None))
    expected = {"schema": "synthetic-status-projection", "authority": "none", "lane": agent}
    monkeypatch.setattr(session.lane_reader, "lane_read", lambda **kwargs: expected)
    audit_root = tmp_path / ".codex-audit"
    latest = audit_root / "mind-live" / "latest.json"
    latest.parent.mkdir(parents=True)
    latest.write_text("{}", encoding="utf-8")
    result = session.main(["--latest", str(latest),
                           "--audit-root", str(audit_root),
                           "--fleet", str(tmp_path / "synthetic-fleet"), "--fleet-sha256", "0" * 64])
    captured = capsys.readouterr()
    assert result == 0 and captured.err == ""
    output = json.loads(captured.out)
    assert (output["schema"], output["authority"], output["lane"]) == ("synthetic-status-projection", "none", agent)
    assert output["fleet_sha256"] == "0" * 64 and output["source_event_refs"] == []
    assert output["status_observation"]["schema"] == "wd.mind-direct-status.v1"
    assert output["status_observation"]["observation_status"] == "UNKNOWN"
    assert dispatch == []


@pytest.fixture(scope="module")
def source_fixtures():
    # Reuse the existing independently constructed fixture, not the production
    # projector/writer. Explicit path prevents resolving an installed tests package.
    path = Path(__file__).with_name("test_wd_mind_content_query_readonly.py")
    spec = importlib.util.spec_from_file_location("session_content_source_fixtures", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
        yield module
    finally:
        sys.modules.pop(spec.name, None)


def _split(case):
    values = dict(case)
    providers = values.pop("encoder_provider"), values.pop("index_provider")
    assert set(values) == INPUT_KEYS
    return values, providers


def test_content_session_uses_real_verification_and_synthetic_query(source_fixtures, tmp_path, synthetic_session_checkout_pins):
    entry = _entry()
    case = source_fixtures._semantic_case(content, tmp_path)
    inputs, providers = _split(case)
    result = entry(enabled=True, inputs=inputs, providers=providers)
    assert result["schema"] == "wd.q4-content-snapshot-query.v2"
    assert result["status"] == "ANSWER" and result["results"]
    assert result["authority"] == "none" and result["provider_authentication"] == "UNKNOWN"
    assert case["stream"].reads == 1 and len(providers[0].calls) == 1
    assert len(providers[1].calls) == len(case["cell_snapshots"])


@pytest.mark.parametrize("fault", ["source_bytes", "descriptor_pin", "second_cell", "provider_identity", "documents"])
def test_content_session_preserves_source_and_binding_refusal_before_callbacks(source_fixtures, tmp_path, fault, synthetic_session_checkout_pins):
    entry = _entry()
    case = source_fixtures._semantic_case(content, tmp_path)
    inputs, providers = _split(case)
    if fault == "source_bytes":
        path = Path(inputs["messages"]["source_path"])
        raw = path.read_bytes()
        changed = raw.replace(b"network retry", b"altered retry", 1)
        assert changed != raw and len(changed) == len(raw)
        path.write_bytes(changed)
    elif fault == "descriptor_pin":
        inputs["descriptor_sha256"] = "0" * 64
    elif fault == "second_cell":
        inputs["cell_snapshots"]["system"]["artifacts"]["index"] += b"tampered"
    elif fault == "provider_identity":
        providers[0].identity["encoder"]["revision"] = "synthetic-changed"
    else:
        inputs["documents"] = {}
    _refused(lambda: entry(enabled=True, inputs=inputs, providers=providers))
    assert case["stream"].reads == 0 and providers[0].calls == [] and providers[1].calls == []


def test_content_session_late_source_change_never_returns_partial_content(source_fixtures, tmp_path, capsys, synthetic_session_checkout_pins):
    entry = _entry()
    case = source_fixtures._semantic_case(content, tmp_path)
    inputs, providers = _split(case)
    path = Path(inputs["messages"]["source_path"])

    def change(index):
        path.write_bytes(path.read_bytes().replace(b"network retry", b"altered retry", 1))

    providers[1].on_load = change
    _refused(lambda: entry(enabled=True, inputs=inputs, providers=providers))
    assert providers[1].calls
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == ""


def test_content_session_does_not_write_files_or_change_environment(source_fixtures, tmp_path, synthetic_session_checkout_pins):
    entry = _entry()
    case = source_fixtures._semantic_case(content, tmp_path)
    inputs, providers = _split(case)
    before_env = dict(os.environ)
    before = {str(path): hashlib.sha256(path.read_bytes()).hexdigest()
              for path in tmp_path.rglob("*") if path.is_file()}
    assert entry(enabled=True, inputs=inputs, providers=providers)["status"] == "ANSWER"
    assert dict(os.environ) == before_env
    assert {str(path): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in tmp_path.rglob("*") if path.is_file()} == before

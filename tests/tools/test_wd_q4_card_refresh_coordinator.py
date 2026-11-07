"""Real staged-card producers/selector; synthetic source, encoder and Job receipt."""
import copy
from functools import wraps
import importlib
import json
from pathlib import Path
import shutil

import pytest

from tests.tools.test_wd_q4_card_audit_orchestrator import (
    attested, materialize, real_fixture, staged_producer, _chain, sha, write)
from tests.tools.test_wd_q4_two_card_snapshot_refresh import _append_prefix


@pytest.fixture
def tmp_path(tmp_path_factory):
    root = tmp_path_factory.mktemp('p') / '.codex-audit'
    root.mkdir()
    return root


def _build_case(attested, tmp_path, monkeypatch):
    coordinator = importlib.import_module('tools.wd_q4_card_refresh_coordinator')
    from tools import wd_q4_card_audit_orchestrator as producer
    from tools import wd_q4_lane_caller_cli as caller
    from tools import wd_q4_live_snapshot_publish as live
    shared = tmp_path / 'live' / 'generations'; shared.mkdir(parents=True)
    monkeypatch.setattr(live, '_P1_ROOT', shared.parent)
    request_path, _, _ = _chain(attested, tmp_path, monkeypatch, lambda _: shared)
    request = json.loads(request_path.read_bytes())
    # Exercise the production request contract using fabricated local inputs.
    # No synthetic-mode exemption is added to the coordinator.
    monkeypatch.setattr(producer, '_GENERATION_PARENT', shared.parent)
    anchors = shared.parent / '.codex-audit' / 'anchors'; anchors.mkdir(parents=True)
    decision = json.loads(Path(request['decision']['path']).read_bytes())
    decision['source_mode'] = 'operator_authorized_source'
    first = anchors / 'first'; shutil.copytree(decision['anchor']['root'], first)
    decision['anchor']['root'] = str(first)
    path = tmp_path / 'd1.json'
    request['decision'] = {'path': str(path), 'sha256': write(path, decision)}
    finish = json.loads(Path(request['finish']['path']).read_bytes())
    for key in ('public_policy',):
        finish['base_plan'][key + '_path'] = decision['inputs'][key]['path']
        finish['base_plan'][key + '_sha256'] = decision['inputs'][key]['sha256']
    template = json.loads(Path(request['snapshot_template']['path']).read_bytes())
    template['source_mode'] = 'operator_authorized_source'
    template_path = tmp_path / 'operator-template.json'
    request['snapshot_template'] = {'path': str(template_path), 'sha256': write(template_path, template)}
    jobs, process_results = {}, {}
    for char in 'bcd':
        gen = char * 40
        changed = dict(request)
        options = dict(finish['composer_options'], generation=gen)
        path = tmp_path / (char + '-finish.json')
        changed['finish'] = {'path': str(path), 'sha256': write(path, dict(finish, composer_options=options))}
        if char == 'd':
            changed['decision'] = _append_prefix(request, anchors)
        out = shared.parent / (char + '-out'); out.mkdir()
        changed['output_root'] = str(out)
        generation_root = shared.parent / (char + '-run'); generation_root.mkdir()
        changed.update(schema='wd.q4-card-real-candidate-request.v1', generation_root=str(generation_root))
        path = tmp_path / (char + '-request.json'); pin = write(path, changed)
        result = producer.run(enabled=True, request_path=str(path), request_sha256=pin)
        assert result['status'] == 'PREPARED'
        job = {'schema': 'wd.q4-card-refresh-job.v1', 'outcome': 'completed', 'cleanup': 'confirmed',
               'exit_code': 0, 'request': {'path': str(path), 'sha256': pin}, 'result': result}
        job_path = tmp_path / (char + '-job.json')
        jobs[char] = {'path': str(job_path), 'sha256': write(job_path, job)}
        stdout = tmp_path / (char + '-stdout.json')
        process_results[char] = {'exit_code': 0, 'stdout': {'path': str(stdout), 'sha256': write(stdout, result)}}
    real_read = caller._read
    monkeypatch.setattr(caller, '_read', lambda p, limit, pin=None: (
        b'' if str(p) == attested['descriptor_options']['interpreter'] else real_read(p, limit, pin)))
    manifest = attested['expected_manifest_sha256'].lower()
    monkeypatch.setenv('WD_REBOOT_EXPECTED_MANIFEST_HASH', manifest)
    base = shared / ('b' * 40) / 'shared-session-config.v2.json'
    baseline = {'path': str(base), 'sha256': sha(base.read_bytes())}
    selector = shared / 'q4-live-current.json'
    private = tmp_path / 'private'; private.mkdir()

    def tuple_for(char='c', previous=None):
        candidate = shared / (char * 40) / base.name
        config = caller._configuration(str(candidate), sha(candidate.read_bytes()))
        plan, _, _ = live._plan(config[0], config[5])
        return {'schema': coordinator.SCHEMA, 'p1_enabled': True, 'manifest_sha256': manifest,
                'baseline': baseline, 'candidate': {'path': str(candidate), 'sha256': sha(candidate.read_bytes())},
                'previous_selector_sha256': previous, 'private_root': str(private), 'job': jobs[char],
                'job_return': process_results[char],
                'source': {'path': plan['source_path'], 'file_identity': plan['end_cursor']['file_identity'],
                           'bytes': plan['end_cursor']['offset'], 'sha256': plan['source_anchor_sha256'],
                           'generation_path': plan['generation_path'],
                           'generation_sha256': sha(Path(plan['generation_path']).read_bytes())}}

    def compose_next(char='e'):
        """Compose another synthetic candidate in this same live generations root."""
        changed = dict(request)
        options = dict(finish['composer_options'], generation=char * 40)
        finish_path = tmp_path / (char + '-finish.json')
        changed['finish'] = {'path': str(finish_path), 'sha256': write(finish_path, dict(finish, composer_options=options))}
        out = shared.parent / (char + '-out'); out.mkdir()
        changed['output_root'] = str(out)
        generation_root = shared.parent / (char + '-run'); generation_root.mkdir()
        changed.update(schema='wd.q4-card-real-candidate-request.v1', generation_root=str(generation_root))
        request_path = tmp_path / (char + '-request.json')
        return producer.run(enabled=True, request_path=str(request_path), request_sha256=write(request_path, changed))

    return dict(coordinator=coordinator, tuple_for=tuple_for, selector=selector, private=private,
                shared=shared, tmp=tmp_path, manifest=manifest, baseline=baseline, caller=caller, live=live,
                compose_next=compose_next)


@pytest.fixture(scope='module')
def prepared_case(tmp_path_factory):
    """Build immutable B/C/D once; all assertions still use actual staged files."""
    root = tmp_path_factory.mktemp('s') / '.codex-audit'; root.mkdir()
    patch = pytest.MonkeyPatch()
    try:
        staged = staged_producer.__wrapped__(root)
        materialized = materialize.__wrapped__(staged, root, patch)
        pinned = attested.__wrapped__(materialized, patch)
        prepared = _build_case(pinned, root, patch)
        prepared['input_json'] = {p: p.read_bytes() for p in root.rglob('*.json')}
        prepared['case_number'] = 0
        yield prepared
    finally:
        patch.undo()


@pytest.fixture
def case(prepared_case):
    """Own fresh config/journals per case; restore only synthetic input JSON."""
    prepared_case['case_number'] += 1
    out = prepared_case['tmp'] / ('t' + str(prepared_case['case_number'])); out.mkdir()
    private = out / 'private'; private.mkdir()
    def tuple_for(char='c', previous=None):
        document = copy.deepcopy(prepared_case['tuple_for'](char, previous))
        document['private_root'] = str(private)
        return document
    try:
        yield dict(prepared_case, tmp=out, private=private, tuple_for=tuple_for)
    finally:
        selector = prepared_case['selector']
        if selector.exists():
            (out / 'selector-after.json').write_bytes(selector.read_bytes())
            selector.unlink()  # this fixture's synthetic selector, never a live path
        for path, raw in prepared_case['input_json'].items():
            if path.read_bytes() != raw: path.write_bytes(raw)


def configured(case, document, name='config'):
    from tools import wd_q4_live_update_config_prepare as prepare
    out = case['tmp'] / ('cfg-' + name); out.mkdir()
    path = case['tmp'] / (name + '-tuple.json'); pin = write(path, document)
    return prepare.prepare_config(enabled=True, p1_enabled=True, output_path=str(out / 'updater-config.json'),
        baseline_path=document['baseline']['path'], expected_baseline_sha256=document['baseline']['sha256'],
        expected_manifest_sha256=document['manifest_sha256'], tuple_path=str(path), expected_tuple_sha256=pin)


def invoke(config, attempt='a' * 64):
    from tools import wd_q4_live_update_once as update
    return update.run_once(enabled=True, config_path=config['config_path'],
        expected_config_sha256=config['config_sha256'], host_attempt=attempt)


def recover(config, attempt='a' * 64, progress=None):
    from tools import wd_q4_live_update_recovery as recovery
    return recovery.recover(enabled=True, config_path=config['config_path'],
        expected_config_sha256=config['config_sha256'], expected_attempt=attempt,
        expected_progress_sha256=progress)


def test_real_p1_run_once_publishes_c_then_d_without_v6(case, monkeypatch):
    from tools import wd_q4_live_update_once as update
    monkeypatch.setattr(update, '_snapshot', lambda *_: pytest.fail('v6 reader entered'))
    original = {str(p): p.read_bytes() for p in case['shared'].rglob('*') if p.is_file()}
    for index, char in enumerate('cd'):
        previous = sha(case['selector'].read_bytes()) if case['selector'].exists() else None
        result = invoke(configured(case, case['tuple_for'](char, previous), char), str(index + 1) * 64)
        assert result['status'] == 'PUBLISHED'
        assert json.loads(case['selector'].read_bytes())['generation'] == char * 40
        baseline = case['caller']._configuration(
            case['baseline']['path'], case['baseline']['sha256'])
        selected = case['live'].select(enabled=True, p1_enabled=True, baseline_path=case['baseline']['path'],
            baseline_sha256=case['baseline']['sha256'], baseline=baseline, manifest_sha256=case['manifest'],
            load_configuration=case['caller']._configuration)
        assert Path(selected['configuration'][0]['public_plan']).parent.name == char * 40
    assert all(Path(path).read_bytes() == raw for path, raw in original.items())


@pytest.mark.parametrize('fault', ['audit_schema', 'decision_synthetic', 'template_synthetic',
    'both_synthetic', 'missing_decision_mode', 'unknown_template_mode', 'nonreal_decision_mode'])
def test_source_mode_class_is_closed_before_any_config_write(case, fault):
    from tools import wd_q4_live_update_config_prepare as prepare
    document = case['tuple_for']()
    job_path = Path(document['job']['path'])
    job = json.loads(job_path.read_bytes())
    request_path = Path(job['request']['path'])
    request = json.loads(request_path.read_bytes())
    if fault == 'audit_schema':
        request['schema'] = 'wd.q4-card-audit-prefix-request.v1'
        request['output_root'] = request.pop('generation_root')
    for field in ('decision', 'snapshot_template'):
        data_path = Path(request[field]['path'])
        data = json.loads(data_path.read_bytes())
        if ((field == 'decision' and fault in ('decision_synthetic', 'both_synthetic'))
                or (field == 'snapshot_template' and fault in ('template_synthetic', 'both_synthetic'))):
            data['source_mode'] = 'synthetic_fixture'
        if field == 'decision' and fault == 'missing_decision_mode':
            del data['source_mode']
        if field == 'snapshot_template' and fault == 'unknown_template_mode':
            data['source_mode'] = 'UNKNOWN'
        if field == 'decision' and fault == 'nonreal_decision_mode':
            data['mode'] = 'synthetic'
        request[field]['sha256'] = write(data_path, data)
    job['request']['sha256'] = write(request_path, request)
    document['job']['sha256'] = write(job_path, job)
    before = {p: p.read_bytes() for p in case['private'].rglob('*') if p.is_file()}
    with pytest.raises(prepare.ConfigPrepareRefused):
        configured(case, document, fault)
    assert before == {p: p.read_bytes() for p in case['private'].rglob('*') if p.is_file()}
    assert not case['selector'].exists()


def test_selector_aba_cannot_prove_not_published(case, monkeypatch):
    from tools import wd_q4_live_update_once as update
    from tools import wd_q4_live_update_recovery as recovery
    config = configured(case, case['tuple_for']())
    actual_publish = case['live'].publish
    @wraps(actual_publish)
    def published_then_interrupted(**kwargs):
        actual_publish(**kwargs)
        raise KeyboardInterrupt()
    monkeypatch.setattr(case['live'], 'publish', published_then_interrupted)
    with pytest.raises(update.UpdateInterrupted):
        invoke(config)
    assert case['selector'].exists()
    case['selector'].unlink()  # Ordinary rollback of this fixture's selector to its prior absent state.
    before = {p: p.read_bytes() for p in case['private'].rglob('*') if p.is_file()}
    with pytest.raises(recovery.RecoveryOutcomeUnknown):
        recover(config)
    assert before == {p: p.read_bytes() for p in case['private'].rglob('*') if p.is_file()}


@pytest.mark.parametrize('cut', ['before_marker', 'lost_marker_after_publish'])
def test_missing_marker_is_not_nonpublication_evidence(case, monkeypatch, cut):
    from tools import wd_q4_live_update_once as update
    from tools import wd_q4_live_update_recovery as recovery
    config = configured(case, case['tuple_for']())
    coordinator = case['coordinator']
    if cut == 'before_marker':
        actual_write = coordinator._write
        def interrupted(path, raw):
            if path.name == 'q4-card-refresh.pending.json':
                raise KeyboardInterrupt()
            return actual_write(path, raw)
        monkeypatch.setattr(coordinator, '_write', interrupted)
    else:
        actual_publish = case['live'].publish
        @wraps(actual_publish)
        def interrupted(**kwargs):
            actual_publish(**kwargs)
            raise KeyboardInterrupt()
        monkeypatch.setattr(case['live'], 'publish', interrupted)
    with pytest.raises(update.UpdateInterrupted):
        invoke(config)
    if cut == 'lost_marker_after_publish':
        (case['private'] / 'q4-card-refresh.pending.json').unlink()
        case['selector'].unlink()
    before = {p: p.read_bytes() for p in case['private'].rglob('*') if p.is_file()}
    with pytest.raises(recovery.RecoveryOutcomeUnknown):
        recover(config)
    assert before == {p: p.read_bytes() for p in case['private'].rglob('*') if p.is_file()}


_BAD_TUPLES = [
    ('flag_false', lambda d: d.update(p1_enabled=False)),
    ('flag_int', lambda d: d.update(p1_enabled=1)),
    ('flag_zero', lambda d: d.update(p1_enabled=0)),
    ('flag_string', lambda d: d.update(p1_enabled='true')),
    ('flag_null', lambda d: d.update(p1_enabled=None)),
    ('flag_list', lambda d: d.update(p1_enabled=[])),
    ('extra', lambda d: d.update(unexpected=True)),
    ('schema', lambda d: d.update(schema='wd.q4-card-refresh-tuple.v0')),
    ('source', lambda d: d['source'].update(sha256='0' * 64)),
    ('identity', lambda d: d['source'].update(file_identity='wrong')),
    ('candidate', lambda d: d['candidate'].update(sha256='0' * 64)),
    ('previous', lambda d: d.update(previous_selector_sha256='0' * 64)),
    ('private', lambda d: d.update(private_root=d['candidate']['path'])),
    ('private_shared', lambda d: d.update(private_root=str(Path(d['candidate']['path']).parent.parent))),
    ('job_pin', lambda d: d['job'].update(sha256='0' * 64)),
]


@pytest.mark.parametrize('name,change', _BAD_TUPLES, ids=[item[0] for item in _BAD_TUPLES])
def test_tuple_class_sweep_refuses_before_output(case, name, change):
    from tools import wd_q4_live_update_config_prepare as prepare
    document = copy.deepcopy(case['tuple_for']()); change(document)
    with pytest.raises(prepare.ConfigPrepareRefused): configured(case, document, name)
    assert not case['selector'].exists()


@pytest.mark.parametrize('name,value', [('cleanup', 'unknown'), ('outcome', 'timeout'),
                                       ('exit_code', 1), ('exit_code', False)])
def test_job_class_sweep_refuses_before_output(case, name, value):
    from tools import wd_q4_live_update_config_prepare as prepare
    document = case['tuple_for']()
    job_path = Path(document['job']['path']); original = job_path.read_bytes()
    try:
        job = json.loads(original); job[name] = value
        document['job']['sha256'] = write(job_path, job)
        with pytest.raises(prepare.ConfigPrepareRefused): configured(case, document, name + str(value))
    finally:
        job_path.write_bytes(original)


@pytest.mark.parametrize('cut', ['pending', 'progress', 'published', 'result'])
def test_crashes_reconcile_old_or_target_without_republish(case, monkeypatch, cut):
    from tools import wd_q4_live_update_once as update
    from tools import wd_q4_live_update_recovery as recovery
    coordinator = case['coordinator']; config = configured(case, case['tuple_for']())
    real_write = coordinator._write
    real_publish = case['live'].publish
    def write_then_interrupt(path, raw):
        result = real_write(path, raw)
        if cut in ('pending', 'progress', 'result') and cut in path.name:
            raise KeyboardInterrupt()
        return result
    @wraps(real_publish)
    def publish_then_interrupt(**kwargs):
        result = real_publish(**kwargs)
        if cut == 'published': raise KeyboardInterrupt()
        return result
    with monkeypatch.context() as patch:
        patch.setattr(coordinator, '_write', write_then_interrupt)
        patch.setattr(case['live'], 'publish', publish_then_interrupt)
        with pytest.raises((update.UpdateInterrupted, update.UpdateOutcomeUnknown)): invoke(config)
    before = case['selector'].read_bytes() if case['selector'].exists() else None
    monkeypatch.setattr(case['live'], 'publish', lambda **_: pytest.fail('recovery republished'))
    if cut in ('pending', 'progress'):
        journals = {p: p.read_bytes() for p in case['private'].rglob('*') if p.is_file()}
        with pytest.raises(recovery.RecoveryOutcomeUnknown):
            recover(config)
        assert journals == {p: p.read_bytes() for p in case['private'].rglob('*') if p.is_file()}
        assert (case['selector'].read_bytes() if case['selector'].exists() else None) == before
        return
    result = recover(config)
    assert result['status'] == 'PUBLISHED'
    assert (case['selector'].read_bytes() if case['selector'].exists() else None) == before
    assert recover(config) == result
    assert not (case['private'] / 'q4-card-refresh.pending.json').exists()


def test_foreign_selector_and_pending_never_overwritten(case, monkeypatch):
    from tools import wd_q4_live_update_once as update
    from tools import wd_q4_live_update_recovery as recovery
    coordinator = case['coordinator']; config = configured(case, case['tuple_for']())
    case['selector'].write_bytes(b'{}')
    with pytest.raises(update.UpdateRefused): invoke(config)
    assert case['selector'].read_bytes() == b'{}'
    case['selector'].unlink()
    real_write = coordinator._write
    def stop(path, raw):
        result = real_write(path, raw)
        if 'pending' in path.name: raise KeyboardInterrupt()
        return result
    with monkeypatch.context() as patch:
        patch.setattr(coordinator, '_write', stop)
        with pytest.raises(update.UpdateInterrupted): invoke(config)
    with pytest.raises(update.UpdateOutcomeUnknown): invoke(config, 'b' * 64)
    case['selector'].write_bytes(b'{}')
    with pytest.raises(recovery.RecoveryOutcomeUnknown): recover(config)
    assert case['selector'].read_bytes() == b'{}'
    assert (case['private'] / 'q4-card-refresh.pending.json').exists()


def test_common_single_flight_blocks_other_attempt(case, monkeypatch):
    from tools import wd_q4_live_update_once as update
    config = configured(case, case['tuple_for']())
    real_publish = case['live'].publish
    calls = []
    @wraps(real_publish)
    def nested(**kwargs):
        with pytest.raises((update.UpdateRefused, update.UpdateOutcomeUnknown)):
            invoke(config, 'b' * 64)
        calls.append(True)
        return real_publish(**kwargs)
    monkeypatch.setattr(case['live'], 'publish', nested)
    assert invoke(config)['status'] == 'PUBLISHED'
    assert calls == [True]


@pytest.mark.parametrize('fault', ['request_root', 'request_decision', 'pointer_path', 'output_bundle'])
def test_job_request_binding_and_protected_output(case, fault):
    from tools import wd_q4_live_update_config_prepare as prepare
    document = case['tuple_for']()
    job_path = Path(document['job']['path']); job = json.loads(job_path.read_bytes())
    if fault in ('request_root', 'request_decision'):
        request_path = Path(job['request']['path']); request = json.loads(request_path.read_bytes())
        if fault == 'request_root':
            request['generation_root'] = str(case['tmp'])
        else:
            decision_path = Path(request['decision']['path'])
            decision = json.loads(decision_path.read_bytes()); decision['source_path'] = str(case['tmp'] / 'other')
            request['decision']['sha256'] = write(decision_path, decision)
        job['request']['sha256'] = write(request_path, request)
    if fault == 'pointer_path':
        pointer_path = Path(job['result']['pointer_path']); pointer = json.loads(pointer_path.read_bytes())
        pointer['descriptor_path'] = document['baseline']['path']
        job['result']['pointer_sha256'] = write(pointer_path, pointer)
    document['job']['sha256'] = write(job_path, job)
    with pytest.raises(prepare.ConfigPrepareRefused):
        if fault != 'output_bundle':
            configured(case, document, fault)
        else:
            candidate = case['caller']._configuration(document['candidate']['path'], document['candidate']['sha256'])
            output = Path(candidate[0]['bundle_root']) / 'forbidden-config'; output.mkdir()
            path = case['tmp'] / 'output-tuple.json'; pin = write(path, document)
            prepare.prepare_config(enabled=True, p1_enabled=True, output_path=str(output / 'updater-config.json'),
                baseline_path=document['baseline']['path'], expected_baseline_sha256=document['baseline']['sha256'],
                expected_manifest_sha256=document['manifest_sha256'], tuple_path=str(path), expected_tuple_sha256=pin)


def test_previous_pin_is_forwarded_to_selector_cas(case, monkeypatch):
    config = configured(case, case['tuple_for']())
    actual = case['live'].publish
    @wraps(actual)
    def checked(**kwargs):
        assert 'expected_previous_selector_sha256' in kwargs
        assert kwargs['expected_previous_selector_sha256'] is None
        # The real CAS API is supplied by the independently owned selector fix.
        return actual(**kwargs)
    monkeypatch.setattr(case['live'], 'publish', checked)
    assert invoke(config)['status'] == 'PUBLISHED'


@pytest.mark.parametrize('fault', ['missing', 'exit', 'bool_exit', 'stdout', 'valid'])
def test_job_process_return_is_bound(case, fault):
    from tools import wd_q4_live_update_config_prepare as prepare
    document = case['tuple_for']()
    job = json.loads(Path(document['job']['path']).read_bytes())
    if fault == 'missing': document.pop('job_return')
    if fault != 'missing':
        stdout = case['tmp'] / 'job-stdout.json'
        value = dict(job['result'])
        if fault == 'stdout': value['pointer_sha256'] = '0' * 64
        document['job_return'] = {'exit_code': 1 if fault == 'exit' else False if fault == 'bool_exit' else 0,
                                  'stdout': {'path': str(stdout), 'sha256': write(stdout, value)}}
        if fault == 'valid':
            # Raw PowerShell output need not use Python's canonical whitespace.
            raw = (json.dumps(value, indent=2) + '\r\n').encode('ascii')
            stdout.write_bytes(raw); document['job_return']['stdout']['sha256'] = sha(raw)
    if fault == 'valid':
        assert configured(case, document)['status'] == 'PREPARED'
    else:
        with pytest.raises(prepare.ConfigPrepareRefused): configured(case, document)


@pytest.mark.parametrize('form', ['request_hash_case', 'windows_path_case'])
def test_native_job_representation_preserves_binding(case, form):
    document = case['tuple_for']()
    if form == 'request_hash_case':
        job_path = Path(document['job']['path']); job = json.loads(job_path.read_bytes())
        job['request']['sha256'] = job['request']['sha256'].upper()
        document['job']['sha256'] = write(job_path, job)
    else:
        # Native GetFullPath and caller spelling may differ in drive-letter case.
        if not document['candidate']['path'][1:3] == ':\\': pytest.skip('Windows path spelling')
        document['candidate']['path'] = document['candidate']['path'][0].lower() + document['candidate']['path'][1:]
    assert configured(case, document)['status'] == 'PREPARED'


@pytest.mark.parametrize('fault', ['pending', 'writer_lock'])
def test_selector_uncertainty_cannot_clear_handoff(case, monkeypatch, fault):
    from sandbox.mind.step2.reader_slot import reader_slot
    from tools import wd_q4_live_update_once as update
    from tools import wd_q4_live_update_recovery as recovery
    config = configured(case, case['tuple_for']())
    real_write = case['coordinator']._write
    def stop(path, raw):
        result = real_write(path, raw)
        if 'pending' in path.name: raise KeyboardInterrupt()
        return result
    with monkeypatch.context() as scoped:
        scoped.setattr(case['coordinator'], '_write', stop)
        with pytest.raises(update.UpdateInterrupted): invoke(config)
    pending = case['shared'] / case['live']._PUBLICATION_PENDING
    if fault == 'pending':
        pending.write_bytes(b'{}')  # fabricated foreign CAS uncertainty
        try:
            with pytest.raises(recovery.RecoveryOutcomeUnknown): recover(config)
        finally:
            (case['tmp'] / 'injected-publication-pending.json').write_bytes(pending.read_bytes())
            pending.unlink()  # restore only this test's fabricated marker
    else:
        with reader_slot(case['shared'] / case['live']._PUBLICATION_LOCK, timeout_seconds=0.2):
            with pytest.raises(recovery.RecoveryOutcomeUnknown): recover(config)
    assert (case['private'] / 'q4-card-refresh.pending.json').exists()


@pytest.fixture
def lifecycle_case(attested, tmp_path, monkeypatch):
    # Each v2 case has its own generation and canonical private directory.
    return _build_case(attested, tmp_path, monkeypatch)


def _lifecycle_v2_document(case):
    shared = case['shared']
    private = shared.parent / (shared.name + '.q4-lifecycle-private-v2')
    private.mkdir()
    binding = {'schema':'wd.q4-lifecycle-binding.v2', 'generation_root':str(shared),
               'private_root':str(private)}
    binding_path = case['tmp'] / 'lifecycle-binding.json'
    binding_raw = json.dumps(binding,sort_keys=True,separators=(',',':')).encode('ascii')
    binding_path.write_bytes(binding_raw)
    document = case['tuple_for']()
    document.update(schema='wd.q4-card-refresh-tuple.v2',private_root=str(private),
                    lifecycle={'path':str(binding_path),'sha256':sha(binding_raw)})
    return document, private


def test_lifecycle_inspection_does_not_block_v1_writer(lifecycle_case):
    from tools import wd_q4_lifecycle_v2 as lifecycle
    case = lifecycle_case
    document, _ = _lifecycle_v2_document(case)
    raw = Path(document['lifecycle']['path']).read_bytes()
    assert lifecycle.inspect(enabled=True, binding_bytes=raw,
                             binding_sha256=document['lifecycle']['sha256']) == {'status': 'ABSENT_V2_ONLY'}
    assert not (case['shared'] / lifecycle._ENROLLED).exists()
    assert invoke(configured(case, case['tuple_for'](), name='v1-after-inspect'))['status'] == 'PUBLISHED'


def test_lifecycle_busy_without_pending_refuses_before_card_reservation(lifecycle_case, monkeypatch):
    from contextlib import contextmanager
    from tools import wd_q4_live_update_once as update
    case = lifecycle_case
    document, private = _lifecycle_v2_document(case)
    config = configured(case, document, name='v2-busy-without-pending')
    @contextmanager
    def busy(*args, **kwargs):
        raise case['coordinator'].lifecycle.ReaderBusy()
        yield
    monkeypatch.setattr(case['coordinator'].lifecycle, 'reader_slot', busy)
    with pytest.raises(update.UpdateRefused):
        invoke(config)
    assert not (case['shared'] / 'q4-lifecycle-v2.pending.json').exists()
    assert not (private / 'q4-card-refresh.pending.json').exists()


@pytest.mark.parametrize('v2', [False, True])
def test_actual_card_publish_then_next_compose_in_shared_root(lifecycle_case, v2):
    case = lifecycle_case
    document = _lifecycle_v2_document(case)[0] if v2 else case['tuple_for']()
    assert invoke(configured(case, document, name='before-next-compose'))['status'] == 'PUBLISHED'
    previous = case['selector'].read_bytes()
    assert (case['shared'] / 'q4-card-refresh.lock').is_file()
    if v2:
        assert (case['shared'] / 'q4-lifecycle-v2.lock').is_file()
        assert (case['shared'] / 'q4-lifecycle-v2.enrolled.json').is_file()
    assert case['compose_next']()['status'] == 'PREPARED'
    assert case['selector'].read_bytes() == previous


def test_interrupted_enrollment_blocks_v1_and_allows_new_v2_attempt(lifecycle_case, monkeypatch):
    from tools import wd_q4_live_update_once as update
    case = lifecycle_case
    document, private = _lifecycle_v2_document(case)
    config = configured(case, document, name='v2-interrupted-enrollment')
    lifecycle = case['coordinator'].lifecycle
    original = lifecycle._ensure_enrollment
    def interrupt_after_enrollment(root, pin):
        original(root, pin)
        raise OSError('synthetic interruption after enrollment')
    with monkeypatch.context() as scoped:
        scoped.setattr(lifecycle, '_ensure_enrollment', interrupt_after_enrollment)
        with pytest.raises(update.UpdateOutcomeUnknown):
            invoke(config, attempt='1' * 64)
    assert (case['shared'] / lifecycle._ENROLLED).is_file()
    assert not (case['shared'] / lifecycle._PENDING).exists()
    assert not (private / 'q4-card-refresh.pending.json').exists()
    with pytest.raises(update.UpdateOutcomeUnknown):
        invoke(configured(case, case['tuple_for'](), name='v1-after-interrupted-enrollment'))
    assert invoke(config, attempt='2' * 64)['status'] == 'PUBLISHED'


def test_lifecycle_v2_interrupted_a_blocks_b_on_same_generation(lifecycle_case, monkeypatch):
    """A private pending record cannot be bypassed by B in the v2 route."""
    from tools import wd_q4_live_update_once as update
    from tools import wd_q4_live_update_recovery as recovery
    case = lifecycle_case
    shared = case['shared']
    document, private = _lifecycle_v2_document(case)
    first=configured(case,document,name='v2-first')
    real_write=case['coordinator']._write
    def interrupt_after_private_marker(path,raw):
        value=real_write(path,raw)
        if path.name == 'q4-card-refresh.pending.json': raise KeyboardInterrupt()
        return value
    with monkeypatch.context() as scoped:
        scoped.setattr(case['coordinator'],'_write',interrupt_after_private_marker)
        with pytest.raises(update.UpdateInterrupted): invoke(first,attempt='a'*64)
    shared_pending=shared/'q4-lifecycle-v2.pending.json'
    private_pending=private/'q4-card-refresh.pending.json'
    assert shared_pending.is_file() and private_pending.is_file()
    shared_before=shared_pending.read_bytes(); private_before=private_pending.read_bytes()
    second=configured(case,document,name='v2-second')
    with pytest.raises(update.UpdateOutcomeUnknown): invoke(second,attempt='b'*64)
    legacy=configured(case,case['tuple_for'](),name='v1-after')
    with pytest.raises(update.UpdateOutcomeUnknown): invoke(legacy,attempt='c'*64)
    with pytest.raises(recovery.RecoveryOutcomeUnknown): recover(first,attempt='b'*64)
    with pytest.raises(recovery.RecoveryOutcomeUnknown): recover(first,attempt='a'*64)
    assert shared_pending.read_bytes()==shared_before and private_pending.read_bytes()==private_before


def test_lifecycle_v2_success_releases_only_after_verified_selector(lifecycle_case):
    from tools import wd_q4_live_update_once as update
    case = lifecycle_case
    document, private = _lifecycle_v2_document(case)
    config = configured(case, document, name='v2-success')
    result = invoke(config, attempt='d'*64)
    assert result['status'] == 'PUBLISHED'
    assert not (case['shared']/'q4-lifecycle-v2.pending.json').exists()
    assert not (private/'q4-card-refresh.pending.json').exists()
    assert (private/('q4-card-result-' + 'd'*64 + '.json')).is_file()
    assert (case['shared']/'q4-lifecycle-v2.lock').is_file()
    legacy = case['tuple_for'](previous=sha(case['selector'].read_bytes()))
    old_config = configured(case, legacy, name='v1-after-success')
    with pytest.raises(update.UpdateOutcomeUnknown): invoke(old_config, attempt='f'*64)


def test_lifecycle_v2_recovery_clears_owned_markers_only_after_verified_publication(lifecycle_case, monkeypatch):
    from tools import wd_q4_live_update_once as update
    case = lifecycle_case
    document, private = _lifecycle_v2_document(case)
    config = configured(case, document, name='v2-recover')
    original = case['live'].publish
    @wraps(original)
    def published_then_interrupted(**kwargs):
        original(**kwargs)
        raise KeyboardInterrupt()
    with monkeypatch.context() as scoped:
        scoped.setattr(case['live'], 'publish', published_then_interrupted)
        with pytest.raises(update.UpdateInterrupted): invoke(config, attempt='e'*64)
    assert (case['shared']/'q4-lifecycle-v2.pending.json').is_file()
    assert (private/'q4-card-refresh.pending.json').is_file()
    result = recover(config, attempt='e'*64)
    assert result['status'] == 'PUBLISHED'
    assert not (case['shared']/'q4-lifecycle-v2.pending.json').exists()
    assert not (private/'q4-card-refresh.pending.json').exists()
    assert (private/('q4-card-result-' + 'e'*64 + '.json')).is_file()


def test_lifecycle_v2_recovery_handles_interruption_between_marker_removals(lifecycle_case, monkeypatch):
    from tools import wd_q4_live_update_once as update
    case = lifecycle_case
    document, private = _lifecycle_v2_document(case)
    config = configured(case, document, name='v2-clear-interrupt')
    with monkeypatch.context() as scoped:
        scoped.setattr(case['coordinator'].lifecycle, '_clear_owned',
                       lambda *_: (_ for _ in ()).throw(OSError('synthetic failure')))
        with pytest.raises(update.UpdateOutcomeUnknown): invoke(config, attempt='1'*64)
    assert (case['shared']/'q4-lifecycle-v2.pending.json').is_file()
    assert not (private/'q4-card-refresh.pending.json').exists()
    assert (private/('q4-card-result-' + '1'*64 + '.json')).is_file()
    assert recover(config, attempt='1'*64)['status'] == 'PUBLISHED'
    assert not (case['shared']/'q4-lifecycle-v2.pending.json').exists()


def test_lifecycle_v2_other_process_holding_intent_blocks_card_writer(lifecycle_case):
    import os
    import subprocess
    import sys
    from tools import wd_q4_live_update_once as update
    case = lifecycle_case
    document, private = _lifecycle_v2_document(case)
    config = configured(case, document, name='v2-concurrent')
    raw=Path(document['lifecycle']['path']).read_bytes()
    code = '''import sys
from tools import wd_q4_lifecycle_v2 as life
with life.admit(enabled=True,binding_bytes=bytes.fromhex(sys.argv[1]),binding_sha256=sys.argv[2],attempt='a'*64,previous_selector_sha256=None):
    print('ADMITTED',flush=True)
    sys.stdin.readline()
'''
    env = dict(os.environ, PYTHONPATH=str(Path.cwd()))
    child = subprocess.Popen([sys.executable,'-B','-c',code,raw.hex(),document['lifecycle']['sha256']],
                             stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,env=env)
    try:
        assert child.stdout.readline().strip() == 'ADMITTED'
        with pytest.raises(update.UpdateOutcomeUnknown): invoke(config,attempt='2'*64)
        assert (case['shared']/'q4-lifecycle-v2.pending.json').is_file()
        assert not (private/'q4-card-refresh.pending.json').exists()
    finally:
        child.stdin.write('\n'); child.stdin.flush()
        assert child.wait(timeout=15) == 0

"""Explicit prepared-card to selector handoff, never a source/model/Job launcher.

Each cycle's capture, anchor and candidate builder run separately and supply an
exact pinned tuple plus a closed caller-owned Job/cleanup receipt. Those bytes
are assertions, not authentication or proof against a same-user writer. The
actual staged binding/selector validate candidate and frozen source bytes.
No generation is discovered, rebuilt or retried here. Locks coordinate callers
using this protocol; arbitrary non-cooperating selector writers remain outside
that guarantee. Post-write failures retain UNKNOWN evidence for reconciliation.
The production route requires an explicitly operator-authorized real candidate;
test inputs may be fabricated but cannot silently select the audit source mode.
Seeing the previous selector does not prove no publication occurred: without
independent nonpublication evidence, recovery remains UNKNOWN, including ABA.
"""
from contextlib import ExitStack
import hashlib
import inspect
import json
import os
from pathlib import Path
import re

from sandbox.mind.step2.reader_slot import reader_slot
from tools import wd_q4_lane_caller_cli as caller
from tools import wd_q4_live_snapshot_publish as live
from tools import wd_q4_lifecycle_v2 as lifecycle

SCHEMA = 'wd.q4-card-refresh-tuple.v1'
LIFECYCLE_SCHEMA = 'wd.q4-card-refresh-tuple.v2'
_HEX = re.compile(r'[0-9a-f]{64}\Z')
_KEYS = {'schema', 'p1_enabled', 'manifest_sha256', 'baseline', 'candidate',
         'previous_selector_sha256', 'private_root', 'job', 'job_return', 'source'}


class Refused(ValueError):
    pass


class Unknown(RuntimeError):
    pass


class Interrupted(BaseException):
    pass


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _raw(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True,
                      allow_nan=False).encode('ascii')


def _pin(value):
    if type(value) is not str or _HEX.fullmatch(value) is None:
        raise ValueError()
    return value


def _read(path, limit=65536, pin=None):
    return live._read(live.pointer_io._absolute(path), limit, pin)


def _spec(value, limit=65536, *, receipt_hex_case=False):
    if type(value) is not dict or set(value) != {'path', 'sha256'}:
        raise ValueError()
    pin = value['sha256']
    # The native launcher preserves its original CLI hash spelling. Outer
    # tuple pins remain lowercase; both spellings here verify identical bytes.
    if receipt_hex_case and type(pin) is str:
        pin = pin.lower()
    return _read(value['path'], limit, _pin(pin))


def _write(path, raw):
    # Never replace a journal entry, including a partial or foreign one.
    live.pointer_io._chain(path.parent)
    with path.open('xb') as stream:
        if stream.write(raw) != len(raw):
            raise OSError()
        stream.flush()
        os.fsync(stream.fileno())
    if _read(str(path), 262144, _sha(raw)) != raw:
        raise ValueError()


def _selector(path):
    return _sha(_read(str(path), 4096)) if os.path.lexists(path) else None


def _target(document, candidate):
    return _raw({'schema': live._SCHEMA, 'baseline_sha256': document['baseline']['sha256'],
                 'generation': Path(document['candidate']['path']).parent.name,
                 'descriptor_sha256': document['candidate']['sha256'],
                 'index_pointer_sha256': candidate[4]['pointer_sha256']})


def _load(path, pin):
    raw = _read(path, 65536, _pin(pin)); document = caller._json(raw)
    if (type(document) is not dict or document.get('schema') not in (SCHEMA, LIFECYCLE_SCHEMA)
            or set(document) != (_KEYS | ({'lifecycle'} if document.get('schema') == LIFECYCLE_SCHEMA else set()))
            or document['p1_enabled'] is not True
            or os.environ.get('WD_REBOOT_EXPECTED_MANIFEST_HASH', '').lower() != _pin(document['manifest_sha256'])):
        raise ValueError()
    if document['previous_selector_sha256'] is not None:
        _pin(document['previous_selector_sha256'])
    _spec(document['baseline']); _spec(document['candidate'])
    baseline = caller._configuration(document['baseline']['path'], document['baseline']['sha256'])
    candidate = caller._configuration(document['candidate']['path'], document['candidate']['sha256'])
    public, _, _ = live._plan(candidate[0], candidate[5])
    base_public, _, _ = live._plan(baseline[0], baseline[5])
    if public['schema'] != 'wd.q4-public-wrapper-plan.v3' or base_public['schema'] != public['schema']:
        raise ValueError()
    # Recompute the actual staged binding and validate the exact P1 root rules.
    live._checked_pair(baseline, candidate, document['manifest_sha256'], p1_enabled=True)
    base_path = live.pointer_io._absolute(document['baseline']['path'])
    candidate_path = live.pointer_io._absolute(document['candidate']['path'])
    root = base_path.parent.parent
    if (base_path.name != 'shared-session-config.v2.json' or candidate_path.name != base_path.name
            or candidate_path.parent.parent != root
            or any(Path(candidate[0][key]) != candidate_path.parent / name for key, name in live._GENERATION_PATHS)):
        raise ValueError()
    live._validate_roots(root, Path(baseline[0]['bundle_root']), Path(public['root']))
    source = document['source']
    if (type(source) is not dict or set(source) != {'path', 'file_identity', 'bytes', 'sha256',
                                                  'generation_path', 'generation_sha256'}
            or type(source['bytes']) is not int or source['bytes'] <= 0
            or Path(source['path']) != Path(public['source_path']) or source['file_identity'] != public['end_cursor']['file_identity']
            or source['bytes'] != public['end_cursor']['offset'] or source['sha256'] != public['source_anchor_sha256']
            or Path(source['generation_path']) != Path(public['generation_path'])):
        raise ValueError()
    _pin(source['sha256']); _read(source['generation_path'], 65536, _pin(source['generation_sha256']))
    job = caller._json(_spec(document['job']))
    if (type(job) is not dict or set(job) != {'schema', 'outcome', 'cleanup', 'exit_code', 'request', 'result'}
            or job['schema'] != 'wd.q4-card-refresh-job.v1' or job['outcome'] != 'completed'
            or job['cleanup'] != 'confirmed' or type(job['exit_code']) is not int or job['exit_code'] != 0):
        raise ValueError()
    request = caller._json(_spec(job['request'], 1024 * 1024, receipt_hex_case=True))
    request_keys = {'schema', 'decision', 'snapshot_template', 'output_root', 'finish'}
    if (type(request) is not dict
            or request.get('schema') != 'wd.q4-card-real-candidate-request.v1'
            or set(request) != request_keys | {'generation_root'}
            or Path(public['root']) != Path(request['generation_root']) / 'snapshot'):
        raise ValueError()
    decision = caller._json(_spec(request['decision'], 1024 * 1024))
    template = caller._json(_spec(request['snapshot_template'], 1024 * 1024))
    if (decision.get('schema') != 'wd.q4-real-card-decision-request.v1'
            or template.get('schema') != 'wd.q4-real-card-snapshot-request.v1'
            or decision.get('mode') != 'real'
            or decision.get('source_mode') != 'operator_authorized_source'
            or template.get('source_mode') != 'operator_authorized_source'
            or Path(decision['source_path']) != Path(source['path']) or Path(template['source_path']) != Path(source['path'])
            or decision['cursor']['offset'] != source['bytes']
            or decision['cursor']['file_identity'] != source['file_identity']
            or decision['prefix_sha256'] != source['sha256']
            or Path(decision['anchor']['root']) / 'generation.json' != Path(source['generation_path'])
            or decision['anchor']['generation_sha256'] != source['generation_sha256']
            or any(decision['inputs'][key] != template['inputs'][key]
                   for key in ('vocabulary', 'path_allowlist', 'policy', 'roster', 'card_policy'))
            or any(decision['inputs'][key] != {'path': public[key + '_path'], 'sha256': public[key + '_sha256']}
                   for key in ('policy', 'roster', 'card_policy'))):
        raise ValueError()
    finish = caller._json(_spec(request['finish'], 1024 * 1024))
    options = finish['composer_options']
    if (options['generation'] != candidate_path.parent.name or Path(options['shared_stage_root']) != root
            or type(options['expected_manifest_sha256']) is not str
            or options['expected_manifest_sha256'].lower() != document['manifest_sha256']):
        raise ValueError()
    result = job['result']
    if type(result) is not dict or set(result) != {'status', 'pointer_path', 'pointer_sha256'} or result['status'] != 'PREPARED':
        raise ValueError()
    process = document['job_return']
    if (type(process) is not dict or set(process) != {'exit_code', 'stdout'}
            or type(process['exit_code']) is not int or process['exit_code'] != 0
            or caller._json(_spec(process['stdout'], 8192)) != result):
        raise ValueError()
    pointer = caller._json(_read(result['pointer_path'], 4096, _pin(result['pointer_sha256'])))
    if (set(pointer) != {'schema', 'active_bundle', 'manifest_sha256', 'bundle_commit',
                         'descriptor_path', 'descriptor_sha256', 'generation'}
            or Path(result['pointer_path']) != root / ('candidate-pointer-' + candidate_path.parent.name + '.json')
            or pointer.get('schema') != live.pointer_io.SCHEMA or pointer.get('generation') != candidate_path.parent.name
            or Path(pointer['descriptor_path']) != candidate_path
            or Path(pointer['active_bundle']) != Path(candidate[0]['bundle_root'])
            or pointer.get('descriptor_sha256') != document['candidate']['sha256']
            or type(pointer.get('manifest_sha256')) is not str
            or pointer['manifest_sha256'].lower() != document['manifest_sha256']):
        raise ValueError()
    private = live.pointer_io._absolute(document['private_root']); live.pointer_io._chain(private)
    if not private.is_dir() or any(not live._disjoint(private, p) for p in (
            root, Path(public['root']), Path(baseline[0]['bundle_root']), Path(source['path']).parent,
            Path(source['generation_path']).parent)):
        raise ValueError()
    if document['schema'] == LIFECYCLE_SCHEMA:
        binding_raw = _spec(document['lifecycle'])
        bound_root, bound_private = lifecycle._binding(binding_raw, document['lifecycle']['sha256'])
        if not live.pointer_io._same(bound_root, root) or not live.pointer_io._same(bound_private, private):
            raise ValueError()
    return document, baseline, candidate, public, root, private, raw


def prepare_config(*, tuple_path, tuple_sha256, output_path, baseline_path, baseline_sha256, manifest_sha256):
    attempted = False
    try:
        document, baseline, _, public, root, _, raw = _load(tuple_path, tuple_sha256)
        if (document['baseline'] != {'path': baseline_path, 'sha256': baseline_sha256}
                or document['manifest_sha256'] != manifest_sha256
                or _selector(root / live._POINTER) != document['previous_selector_sha256']):
            raise ValueError()
        output = live.pointer_io._absolute(output_path); live.pointer_io._chain(output.parent)
        if (output.name != 'updater-config.json' or not output.parent.is_dir()
                or any(not live._disjoint(output.parent, p) for p in (
                    root, Path(public['root']), Path(document['private_root']), Path(public['source_path']).parent,
                    Path(public['generation_path']).parent, Path(baseline[0]['bundle_root'])))):
            raise ValueError()
        route_path = output.parent / 'card-refresh-tuple.json'
        if os.path.lexists(output) or os.path.lexists(route_path):
            raise ValueError()
        config = {'schema': 'wd.q4-live-update-once.v2', 'p1_enabled': True,
                  'route_path': str(route_path), 'route_sha256': tuple_sha256,
                  'plan_path': str(Path(document['candidate']['path']).parent / 'public-plan.json'),
                  'anchor_path': public['generation_path'], 'policy_path': public['policy_path'],
                  'public_policy_path': public['public_policy_path'], 'baseline_path': baseline_path,
                  'baseline_sha256': baseline_sha256, 'manifest_sha256': manifest_sha256}
        attempted = True
        _write(route_path, raw); config_raw = _raw(config); _write(output, config_raw)
        return {'status': 'PREPARED', 'config_path': str(output), 'config_sha256': _sha(config_raw),
                'cutoff_utc': None, 'freshness': 'UNKNOWN'}
    except Exception:
        raise (Unknown('card_config_unknown') if attempted else Refused('card_config_refused')) from None
    except BaseException:
        if attempted: raise Interrupted('card_config_unknown') from None
        raise


def _configuration(config):
    loaded = _load(config['route_path'], config['route_sha256'])
    document, _, _, public, _, _, _ = loaded
    if (config['schema'] != 'wd.q4-live-update-once.v2' or config['p1_enabled'] is not True
            or document['baseline'] != {'path': config['baseline_path'], 'sha256': config['baseline_sha256']}
            or document['manifest_sha256'] != config['manifest_sha256']
            or config['plan_path'] != str(Path(document['candidate']['path']).parent / 'public-plan.json')
            or config['anchor_path'] != public['generation_path'] or config['policy_path'] != public['policy_path']
            or config['public_policy_path'] != public['public_policy_path']):
        raise ValueError()
    return loaded


def _locks(root, private):
    stack = ExitStack()
    try:
        # Generation-root lock covers different private directories; the second
        # lock coordinates existing update/adoption callers only if they share
        # this exact private root. Publication CAS is not a lifecycle mutex for
        # adoption configured with a different private root.
        for path in (root / 'q4-card-refresh.lock', private / 'q4-live-update.lock'):
            live.pointer_io._chain(path if os.path.lexists(path) else path.parent)
            stack.enter_context(reader_slot(path, timeout_seconds=0.2))
        return stack
    except BaseException:
        stack.close(); raise


def _result(document, candidate, status):
    return {'status': status, 'pointer_sha256': candidate[4]['pointer_sha256'],
            'descriptor_sha256': document['candidate']['sha256'], 'freshness': 'UNKNOWN'}


def _selected(document, baseline, candidate, root):
    selected = live.select(enabled=True, p1_enabled=True, baseline_path=document['baseline']['path'],
        baseline_sha256=document['baseline']['sha256'], baseline=baseline,
        manifest_sha256=document['manifest_sha256'], load_configuration=caller._configuration)
    if selected['configuration'][2] != document['candidate']['sha256']:
        raise ValueError()
    return selected


def run_once(*, config, config_sha256, attempt):
    entered = False
    try:
        _pin(config_sha256); _pin(attempt)
        document, baseline, candidate, _, root, private, _ = _configuration(config)
        if 'expected_previous_selector_sha256' not in inspect.signature(live.publish).parameters:
            raise Refused('card_selector_cas_required')
        with _locks(root, private) as locks:
            # A persistent lifecycle lock is also an enrollment sentinel. Once
            # v2 has been entered, legacy refresh must not bypass its intent
            # protocol even after a successful v2 clears its pending record.
            if document['schema'] != LIFECYCLE_SCHEMA and any(os.path.lexists(root / name)
                    for name in (lifecycle._LOCK, lifecycle._PENDING)):
                raise Unknown('card_refresh_unknown')
            marker = private / 'q4-card-refresh.pending.json'
            if os.path.lexists(marker): raise Unknown('card_refresh_unknown')
            # Respect legacy/adoption uncertainty rather than replacing it.
            if any(os.path.lexists(private / name) for name in (
                    'q4-live-update.pending.json', 'q4-continuation-adopt.pending.json',
                    'q4-adopt-recovery-job.pending.json')):
                raise Unknown('card_refresh_unknown')
            selector = root / live._POINTER
            if _selector(selector) != document['previous_selector_sha256']: raise ValueError()
            target = _target(document, candidate)
            if document['schema'] == LIFECYCLE_SCHEMA:
                binding_raw = _spec(document['lifecycle'])
                # Admission may write its marker even if it then fails.
                entered = True
                locks.enter_context(lifecycle.admit(enabled=True, binding_bytes=binding_raw,
                    binding_sha256=document['lifecycle']['sha256'], attempt=attempt,
                    previous_selector_sha256=document['previous_selector_sha256']))
            intent = {'schema': 'wd.q4-card-refresh-intent.v1', 'attempt': attempt,
                      'config_sha256': config_sha256, 'tuple_sha256': config['route_sha256'],
                      'previous_selector_sha256': document['previous_selector_sha256'],
                      'target_selector_sha256': _sha(target), 'candidate': document['candidate']}
            intent_raw = _raw(intent); intent_path = private / ('q4-card-intent-' + attempt + '.json')
            entered = True; _write(intent_path, intent_raw)
            marker_raw = _raw({'schema': 'wd.q4-card-refresh-pending.v1', 'attempt': attempt,
                               'intent_sha256': _sha(intent_raw), 'config_sha256': config_sha256})
            _write(marker, marker_raw)
            _write(private / ('q4-card-progress-' + attempt + '.json'), intent_raw)
            # Revalidate every input immediately before publication under locks.
            _configuration(config)
            if _selector(selector) != intent['previous_selector_sha256']: raise Unknown('card_refresh_unknown')
            publication = live.publish(enabled=True, p1_enabled=True,
                baseline_path=document['baseline']['path'], baseline_sha256=document['baseline']['sha256'],
                baseline=baseline, manifest_sha256=document['manifest_sha256'],
                candidate_path=document['candidate']['path'], candidate_sha256=document['candidate']['sha256'],
                candidate=candidate, expected_previous_selector_sha256=intent['previous_selector_sha256'])
            if publication['publication_sha256'] != _sha(target) or _selector(selector) != _sha(target): raise ValueError()
            _selected(document, baseline, candidate, root)
            result = _result(document, candidate, 'PUBLISHED')
            _write(private / ('q4-card-result-' + attempt + '.json'), _raw({'intent_sha256': _sha(intent_raw), 'result': result}))
            if _read(str(marker)) != marker_raw or _selector(selector) != _sha(target): raise ValueError()
            marker.unlink()
            if document['schema'] == LIFECYCLE_SCHEMA:
                lifecycle._clear_owned(root, document['lifecycle']['sha256'], attempt,
                                       document['previous_selector_sha256'])
            return result
    except Unknown: raise
    except Exception:
        raise (Unknown('card_refresh_unknown') if entered else Refused('card_refresh_refused')) from None
    except BaseException:
        if entered: raise Interrupted('card_refresh_unknown') from None
        raise


def recover(*, config, config_sha256, attempt, progress_sha256=None):
    """Observe old/target/foreign; never republish, rebuild or infer success."""
    try:
        _pin(config_sha256); _pin(attempt)
        document, baseline, candidate, _, root, private, _ = _configuration(config)
        with _locks(root, private) as locks:
            if document['schema'] == LIFECYCLE_SCHEMA:
                binding_raw = _spec(document['lifecycle'])
                locks.enter_context(lifecycle._resume(binding_bytes=binding_raw,
                    binding_sha256=document['lifecycle']['sha256'], attempt=attempt,
                    previous_selector_sha256=document['previous_selector_sha256']))
            elif any(os.path.lexists(root / name) for name in (lifecycle._LOCK, lifecycle._PENDING)):
                raise Unknown('card_refresh_unknown')
            # No publisher may still land while reconciliation observes "old".
            # Recovery never calls publish, so this third lock is not recursive.
            publication_lock = root / live._PUBLICATION_LOCK
            live.pointer_io._chain(publication_lock if os.path.lexists(publication_lock) else root)
            locks.enter_context(reader_slot(publication_lock, timeout_seconds=0.2))
            if os.path.lexists(root / live._PUBLICATION_PENDING):
                raise Unknown('card_refresh_unknown')
            _configuration(config)
            intent_path = private / ('q4-card-intent-' + attempt + '.json')
            intent_raw = _read(str(intent_path)); intent = caller._json(intent_raw)
            expected = {'schema': 'wd.q4-card-refresh-intent.v1', 'attempt': attempt,
                        'config_sha256': config_sha256, 'tuple_sha256': config['route_sha256'],
                        'previous_selector_sha256': document['previous_selector_sha256'],
                        'target_selector_sha256': _sha(_target(document, candidate)), 'candidate': document['candidate']}
            if intent != expected: raise ValueError()
            progress_path = private / ('q4-card-progress-' + attempt + '.json')
            if progress_sha256 is not None:
                _read(str(progress_path), 65536, _pin(progress_sha256))
            if os.path.lexists(progress_path) and _read(str(progress_path)) != intent_raw: raise ValueError()
            marker = private / 'q4-card-refresh.pending.json'
            marker_raw = _raw({'schema': 'wd.q4-card-refresh-pending.v1', 'attempt': attempt,
                               'intent_sha256': _sha(intent_raw), 'config_sha256': config_sha256})
            if os.path.lexists(marker) and _read(str(marker)) != marker_raw: raise ValueError()
            actual = _selector(root / live._POINTER)
            if actual == intent['target_selector_sha256']:
                _selected(document, baseline, candidate, root); status = 'PUBLISHED'
            elif actual == intent['previous_selector_sha256']:
                # A publish followed by ordinary rollback has the same selector
                # value. Intent/progress/marker presence (or loss) cannot prove
                # that publication was never attempted. Preserve all journals.
                raise Unknown('card_refresh_unknown')
            else: raise ValueError()
            result = _result(document, candidate, status)
            receipt_path = private / ('q4-card-result-' + attempt + '.json')
            receipt_raw = _raw({'intent_sha256': _sha(intent_raw), 'result': result})
            if os.path.lexists(receipt_path):
                if _read(str(receipt_path)) != receipt_raw: raise ValueError()
            else:
                if not os.path.lexists(marker): raise ValueError()
                _write(receipt_path, receipt_raw)
            if _selector(root / live._POINTER) != actual: raise ValueError()
            if os.path.lexists(marker):
                if _read(str(marker)) != marker_raw: raise ValueError()
                marker.unlink()
            if document['schema'] == LIFECYCLE_SCHEMA:
                lifecycle._clear_owned(root, document['lifecycle']['sha256'], attempt,
                                       document['previous_selector_sha256'])
            return result
    except BaseException:
        raise Unknown('card_refresh_unknown') from None

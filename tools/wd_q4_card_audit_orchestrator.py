"""Default-off card candidate preparation, with no activation.

The optional composer uses its fixed, independently pinned installed CURRENT.
Tests may substitute only a synthetic CURRENT path. Without a finish template
the result is INCOMPLETE. Post-write errors are UNKNOWN and are never retried.
"""
import hashlib
import json
import os
import sys
from pathlib import Path

from tools import wd_q4_real_card_snapshot_cli as io

_GENERATION_PARENT = Path(r'C:\Python\wd-q4-generations')


class Refused(ValueError):
    pass


class Unknown(RuntimeError):
    pass


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _write_new(path, data):
    with path.open('xb') as stream:
        if stream.write(data) != len(data):
            raise OSError()
        stream.flush()
        os.fsync(stream.fileno())


def _finish(*, enabled=False, output_root=None, base_plan=None, fixed_template=None,
           composer_options=None, pointer_path=None, pointer_sha256=None, decision_path=None,
           card_policy_path=None, source_path=None, generation_path=None, real=False):
    """Finish a synthetic C2 with actual plan writers and composer.

    The composer still checks the fixed installed CURRENT; tests may replace
    only that path with a synthetic pinned fixture. Output paths are permanent.
    Optional composer option ``shared_stage_root`` names one existing shared
    immutable generations root (composer ``shared_stage=True``, its parent is
    the generation root); it must neither contain nor sit inside the output
    root, so a sibling of the output root is fine. Without it the
    stage stays the private, empty ``output_root/stage``.
    """
    if enabled is False:
        return {'status': 'OFF'}
    from sandbox.mind.step2.shared_index_preflight import _encoder_digest
    from tools import wd_q4_public_card_plan_writer as public_writer
    from tools import wd_q4_staged_fixed_plan_writer as fixed_writer
    from tools import wd_q4_staged_composer as composer
    from tools import wd_q4_staged_card_composer_cli as composer_cli

    if enabled is not True or type(base_plan) is not dict or type(fixed_template) is not dict or type(composer_options) is not dict:
        raise ValueError()
    out = io._path(str(output_root), directory=True)
    option_keys = {'generation', 'expected_bundle', 'expected_commit', 'expected_manifest_sha256',
                   'expected_host_sha256', 'descriptor_options', 'bundle_store_root', 'active_bundle_root',
                   'expected_state_sha256'}
    if ((not real and '.codex-audit' not in out.parts) or type(composer_options) is not dict
            or set(composer_options) not in (option_keys, option_keys | {'shared_stage_root'})):
        raise ValueError()
    shared_root = None
    if 'shared_stage_root' in composer_options:
        shared_root = io._path(composer_options['shared_stage_root'], directory=True)
        generations = shared_root.parent
        if ((not real and '.codex-audit' not in shared_root.parts) or (real and shared_root.drive.upper() != 'C:')
                or generations == shared_root
                or type(composer_options['generation']) is not str
                or composer._GENERATION.fullmatch(composer_options['generation']) is None
                or any(composer._contains(a, b) or composer._identity_contains(a, b)
                       for a, b in ((shared_root, out), (out, shared_root)))):
            raise ValueError()
    for value in (pointer_path, decision_path, card_policy_path, source_path, generation_path,
                  base_plan['policy_path'], base_plan['roster_path'], base_plan['public_policy_path']):
        path = io._path(value)
        if (not real and '.codex-audit' not in path.parts) or (real and path.drive.upper() != 'C:'):
            raise ValueError()
    pointer = io._json(io._read(io._path(pointer_path), pointer_sha256, 1024 * 1024))
    generation_root = Path(pointer_path).parent
    memory = generation_root / pointer['generation']
    descriptor_path = memory / 'descriptor.json'
    descriptor = io._json(io._read(io._path(str(descriptor_path)), pointer['artifacts']['descriptor.json'], 65536))
    plan = dict(base_plan)
    plan['config'] = dict(plan['config'])
    plan.update(root=str(generation_root), pointer_sha256=pointer_sha256,
                source_anchor_sha256=pointer['source_prefix']['sha256'], encoder_sha256=_encoder_digest(descriptor['encoder']))
    plan['config'].update(memory=str(memory), descriptor=str(descriptor_path),
                          descriptor_sha256=pointer['artifacts']['descriptor.json'])
    raw = json.dumps(plan, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('ascii')
    plan_file = out / 'base-public-plan.json'; _write_new(plan_file, raw)
    plan_dir = out / 'public-plan'; plan_dir.mkdir()
    published = public_writer.write_staged_card_plan(enabled=True, public_plan_path=str(plan_file),
        expected_public_plan_sha256=_sha(raw), output_dir=str(plan_dir), pointer_path=pointer_path,
        card_decision_path=decision_path, card_policy_path=card_policy_path,
        source_path=source_path, generation_path=generation_path)
    public_path, public_pin = published['public_plan_path'], published['public_plan_sha256']
    public_raw = io._read(io._path(public_path), public_pin, 65536)
    opts = dict(composer_options)
    bundle = io._path(opts['expected_bundle'], directory=True)
    manifest = io._read(io._path(str(bundle / 'deployment-manifest.json')),
                        opts['expected_manifest_sha256'].lower(), 2 * 1024 * 1024)
    manifest_doc = io._json(manifest)
    fleet_pin = manifest_doc['files']['wd-fleet.json'].lower()
    fleet = io._read(io._path(str(bundle / 'wd-fleet.json')), fleet_pin, 1024 * 1024)
    wrapper = io._path(str(bundle / 'Invoke-WdQ4Query.ps1')).read_bytes()
    prep = composer._prepare_staged_card_binding(expected_commit=opts['expected_commit'],
        manifest_bytes=manifest, expected_manifest_sha256=_sha(manifest), fleet_bytes=fleet,
        expected_fleet_sha256=fleet_pin, wrapper_bytes=wrapper, expected_wrapper_sha256=_sha(wrapper),
        public_plan_bytes=public_raw, expected_public_plan_sha256=public_pin)
    pins = io._json(prep['binding_bytes'])['binding']
    # shared mode: fixed plan and descriptor bind the new generation's own copy of the public plan
    bound_public = (public_path if shared_root is None
                    else str(shared_root / composer_options['generation'] / 'public-plan.json'))
    fixed = dict(fixed_template)
    fixed.update(bundle_sha256=prep['bundle_sha256'], bundle_bytes=len(prep['bundle_bytes']),
        approved_tasks=prep['approved_tasks'], expected_pointer_sha256=pins['pointer_sha256'],
        policy_sha256=pins['policy_sha256'], roster_sha256=pins['roster_sha256'],
        expected_public_policy_sha256=pins['public_policy_sha256'], expected_source_sha256=pins['source_anchor_sha256'],
        expected_encoder_sha256=pins['encoder_sha256'], card_decision_sha256=pins['card_decision_sha256'],
        card_policy_sha256=pins['card_policy_sha256'], public_plan_path=bound_public, public_plan_sha256=public_pin)
    fixed_dir = out / 'fixed-plan'; fixed_dir.mkdir()
    fixed_result = fixed_writer.write(enabled=True, arguments=fixed, output_dir=str(fixed_dir))
    fixed_raw = io._read(io._path(fixed_result['path']), fixed_result['sha256'], 4096)
    opts.pop('shared_stage_root', None)
    if shared_root is None:
        stage, generation_root = out / 'stage', out
        stage.mkdir()
    else:
        stage, generation_root = shared_root, shared_root.parent
        opts['shared_stage'] = True
    descriptor_options = dict(opts.pop('descriptor_options'))
    descriptor_options.update(expected_binding=pins, public_plan=bound_public)
    opts.update(stage_dir=str(stage), generation_root=str(generation_root), descriptor_options=descriptor_options,
        public_plan_path=public_path, expected_public_plan_sha256=public_pin,
        fixed_plan_bytes=fixed_raw, expected_fixed_plan_sha256=fixed_result['sha256'])
    result = composer.compose_staged_card(enabled=True, **opts)
    composer_cli._verify_result(result, opts)
    return result


def run(*, enabled=False, request_path=None, request_sha256=None):
    if enabled is False:
        return {'status': 'OFF'}
    try:
        if enabled is not True:
            raise ValueError()
        request_file = io._path(request_path)
        request_raw = io._read(request_file, request_sha256, 1024 * 1024)
        request = io._json(request_raw)
        if (type(request) is not dict or set(request) not in (
                {'schema', 'decision', 'snapshot_template', 'output_root'},
                {'schema', 'decision', 'snapshot_template', 'output_root', 'finish'},
                {'schema', 'decision', 'snapshot_template', 'output_root', 'finish', 'generation_root'})):
            raise ValueError()
        real = request['schema'] == 'wd.q4-card-real-candidate-request.v1'
        if ((real and set(request) != {'schema', 'decision', 'snapshot_template', 'output_root', 'finish', 'generation_root'})
                or (not real and (request['schema'] != 'wd.q4-card-audit-prefix-request.v1'
                                  or 'generation_root' in request))):
            raise ValueError()
        decision_path, decision_raw = io._spec(request['decision'], 1024 * 1024)
        template_path, template_raw = io._spec(request['snapshot_template'], 1024 * 1024)
        decision = io._json(decision_raw)
        template = io._json(template_raw)
        from tools import wd_q4_real_card_decision_cli as dc
        if (type(decision) is not dict or set(decision) != dc._KEYS
                or decision['schema'] != 'wd.q4-real-card-decision-request.v1'
                or decision['mode'] != 'real'
                or decision['source_mode'] != ('operator_authorized_source' if real else 'synthetic_fixture')
                or type(template) is not dict or set(template) != io._KEYS
                or template['schema'] != 'wd.q4-real-card-snapshot-request.v1'
                or template['source_mode'] != decision['source_mode']
                or template['generation_name'] != 'candidate'
                or decision['source_path'] != template['source_path']
                or any(template['inputs'].get(k) != decision['inputs'].get(k)
                       for k in ('vocabulary', 'path_allowlist', 'policy', 'roster', 'card_policy'))
                or any(template['git'].get(k) != decision['git'].get(k)
                       for k in ('executable', 'sha256', 'repository'))):
            raise ValueError()
        out = io._path(request['output_root'], directory=True)
        if ('.codex-audit' not in out.parts or any(out.iterdir())
                or any('.codex-audit' not in p.parts or p.is_relative_to(out)
                       for p in (request_file, decision_path, template_path))):
            raise ValueError()
        finish_path = finish_raw = finish_template = None
        if 'finish' in request:
            finish_path, finish_raw = io._spec(request['finish'], 1024 * 1024)
            finish_template = io._json(finish_raw)
            if ('.codex-audit' not in finish_path.parts or finish_path.is_relative_to(out)
                    or type(finish_template) is not dict
                    or set(finish_template) != {'schema', 'base_plan', 'fixed_template', 'composer_options'}
                    or finish_template['schema'] != 'wd.q4-card-audit-finish-template.v1'):
                raise ValueError()
            opts = finish_template['composer_options']
            if type(opts) is not dict:
                raise ValueError()
            for key in ('expected_bundle', 'bundle_store_root', 'active_bundle_root'):
                candidate = io._path(opts[key], directory=True)
                if (not real and '.codex-audit' not in candidate.parts) or (real and candidate.drive.upper() != 'C:'):
                    raise ValueError()
            if 'shared_stage_root' in opts:  # refuse a bad shared generations root before any write here
                shared_root = io._path(opts['shared_stage_root'], directory=True)
                if ((not real and '.codex-audit' not in shared_root.parts)
                        or (real and shared_root.drive.upper() != 'C:')
                        or shared_root.is_relative_to(out) or out.is_relative_to(shared_root)):
                    raise ValueError()
        from tools import wd_q4_external_generation_anchor as anchor
        from waggledance.core.bridge_log_reader import BridgeCursor
        c, a = decision['cursor'], decision['anchor']
        synthetic_paths = [io._path(decision['source_path']), io._path(a['root'], directory=True),
                           io._path(decision['git']['repository'], directory=True)]
        synthetic_paths.extend(io._path(spec['path']) for spec in decision['inputs'].values())
        if any((not real and '.codex-audit' not in p.parts) or (real and p.drive.upper() != 'C:')
               or p.is_relative_to(out) or out.is_relative_to(p)
               for p in synthetic_paths):
            raise ValueError()
        candidate_root = out
        if real:
            from tools import wd_q4_staged_composer as composer
            candidate_root = io._path(request['generation_root'], directory=True)
            parent = io._path(str(_GENERATION_PARENT), directory=True)
            if (candidate_root.drive.upper() != 'C:' or not os.path.samefile(candidate_root.parent, parent)
                    or any(candidate_root.iterdir())):
                raise ValueError()

            def overlaps(x, y):
                return (x.is_relative_to(y) or y.is_relative_to(x)
                        or composer._identity_contains(x, y) or composer._identity_contains(y, x))
            # IM9 P1 layout: run = P/RUN (snapshot below it), source anchor = P/.codex-audit/anchors/ID/generation.json;
            # one-component names without a leading dot, every subtree disjoint from the others, no reparse anywhere.
            audit = io._path(str(parent / '.codex-audit'), directory=True)
            anchors = io._path(str(audit / 'anchors'), directory=True)
            anchor_root = io._path(a['root'], directory=True)
            if (candidate_root.name.startswith('.') or anchor_root.name.startswith('.')
                    or anchor_root.parent != anchors or not os.path.samefile(anchor_root.parent, anchors)
                    or overlaps(anchor_root, out)):
                raise ValueError()
            if 'shared_stage_root' in opts:
                shared_root = io._path(opts['shared_stage_root'], directory=True)
                if overlaps(shared_root, audit):
                    raise ValueError()
            for other in (out, *synthetic_paths, request_file, decision_path, template_path, finish_path):
                if (candidate_root.is_relative_to(other) or other.is_relative_to(candidate_root)
                        or composer._identity_contains(candidate_root, other)
                        or composer._identity_contains(other, candidate_root)):
                    raise ValueError()
            for key in ('expected_bundle', 'bundle_store_root', 'active_bundle_root', 'shared_stage_root'):
                if key not in opts:
                    continue
                other = io._path(opts[key], directory=True)
                if (candidate_root.is_relative_to(other) or other.is_relative_to(candidate_root)
                        or composer._identity_contains(candidate_root, other)
                        or composer._identity_contains(other, candidate_root)):
                    raise ValueError()
            base = finish_template['base_plan']
            for key in ('policy', 'roster', 'public_policy'):
                if (base[key + '_path'] != decision['inputs'][key]['path']
                        or base[key + '_sha256'] != decision['inputs'][key]['sha256']):
                    raise ValueError()
            composer._anchored_active(opts['expected_state_sha256'],
                io._path(opts['active_bundle_root'], directory=True),
                io._path(opts['bundle_store_root'], directory=True))
        cursor = BridgeCursor(c['offset'], c['file_identity'], None)
        verified = anchor.verify(enabled=True, source_path=decision['source_path'], original_cursor=cursor,
            expected_prefix_sha256=decision['prefix_sha256'], output_root=a['root'],
            expected_generation_sha256=a['generation_sha256'], expected_binding_sha256=a['binding_sha256'])
        if verified['status'] != 'VERIFIED_WITHIN_PREFIX':
            raise ValueError()
    except Exception:
        raise Refused('audit_prefix_refused') from None

    try:
        decision_out = out / 'decision'; decision_out.mkdir()
        snapshot_out = candidate_root / 'snapshot'; snapshot_out.mkdir()
        result = dc.run(enabled=True, request_path=str(decision_path),
                        request_sha256=request['decision']['sha256'], output_root=str(decision_out))
        decision_file = decision_out / 'card-decision.json'
        raw = io._read(decision_file, result['decision_sha256'], 256 * 1024)
        if result['status'] != 'WRITTEN' or _sha(raw) != result['decision_sha256']:
            raise ValueError()
        if real:
            decision_file = candidate_root / 'card-decision.json'
            _write_new(decision_file, raw)
            if io._read(decision_file, result['decision_sha256'], 256 * 1024) != raw:
                raise ValueError()
        template['inputs']['decision'] = {'path': str(decision_file), 'sha256': result['decision_sha256']}
        template['source_generation'] = {'path': str(io._path(a['root'], directory=True) / 'generation.json'),
                                         'sha256': a['generation_sha256']}
        template['cursor'] = {'offset': c['offset'], 'file_identity': c['file_identity'],
                              'generation': verified['generation']}
        template['output_root'] = str(snapshot_out)
        snapshot_raw = json.dumps(template, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('ascii')
        snapshot_request = out / 'snapshot-request.json'
        _write_new(snapshot_request, snapshot_raw)
        snapshot = io.run(enabled=True, request_path=str(snapshot_request), request_sha256=_sha(snapshot_raw))
        pointer = snapshot_out / 'card-snapshot-candidate.json'
        io._read(pointer, snapshot['pointer_sha256'], 1024 * 1024)
        if (snapshot['status'] != 'VALIDATED_STAGED'
                or io._read(request_file, request_sha256, 1024 * 1024) != request_raw
                or io._read(decision_path, request['decision']['sha256'], 1024 * 1024) != decision_raw
                or io._read(template_path, request['snapshot_template']['sha256'], 1024 * 1024) != template_raw):
            raise ValueError()
        if finish_template is not None:
            result = _finish(enabled=True, output_root=candidate_root, base_plan=finish_template['base_plan'],
                fixed_template=finish_template['fixed_template'], composer_options=finish_template['composer_options'],
                pointer_path=str(pointer), pointer_sha256=snapshot['pointer_sha256'], decision_path=str(decision_file),
                card_policy_path=decision['inputs']['card_policy']['path'], source_path=decision['source_path'],
                generation_path=template['source_generation']['path'], real=real)
            if (io._read(request_file, request_sha256, 1024 * 1024) != request_raw
                    or io._read(finish_path, request['finish']['sha256'], 1024 * 1024) != finish_raw):
                raise ValueError()
            return {'status': 'PREPARED', 'pointer_path': result['pointer_path'],
                    'pointer_sha256': result['pointer_sha256']}
        return {'status': 'INCOMPLETE', 'next_step': 'STAGED_COMPOSER_REQUIRES_PINNED_CURRENT',
                'card_pointer_sha256': snapshot['pointer_sha256']}
    except BaseException:
        raise Unknown('audit_prefix_unknown') from None


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if not args:
        print('{"status":"OFF"}')
        return 0
    if len(args) != 5 or args[0] != '--enabled' or args[1] != '--request' or args[3] != '--request-sha256':
        print('{"status":"REFUSED"}', file=sys.stderr)
        return 2
    try:
        result = run(enabled=True, request_path=args[2], request_sha256=args[4])
        print(json.dumps(result, sort_keys=True, separators=(',', ':')))
        return 0 if result['status'] == 'PREPARED' else 3
    except Refused:
        print('{"status":"REFUSED"}', file=sys.stderr)
        return 2
    except Unknown:
        print('{"status":"UNKNOWN"}', file=sys.stderr)
        return 3


if __name__ == '__main__':
    sys.exit(main())

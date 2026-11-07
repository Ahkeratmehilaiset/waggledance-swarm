"""Default-off synthetic card preparation; the descriptor step is unavailable.

The installed composer reads a fixed live CURRENT pointer. This audit-only
prefix therefore stops at the verified C2 card snapshot and reports INCOMPLETE;
it does not manufacture an isolated CURRENT or activate a descriptor.
"""
import hashlib
import json
import os
import sys

from tools import wd_q4_real_card_snapshot_cli as io


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


def run(*, enabled=False, request_path=None, request_sha256=None):
    if enabled is False:
        return {'status': 'OFF'}
    try:
        if enabled is not True:
            raise ValueError()
        request_file = io._path(request_path)
        request_raw = io._read(request_file, request_sha256, 1024 * 1024)
        request = io._json(request_raw)
        if (type(request) is not dict or set(request) != {'schema', 'decision', 'snapshot_template', 'output_root'}
                or request['schema'] != 'wd.q4-card-audit-prefix-request.v1'):
            raise ValueError()
        decision_path, decision_raw = io._spec(request['decision'], 1024 * 1024)
        template_path, template_raw = io._spec(request['snapshot_template'], 1024 * 1024)
        decision = io._json(decision_raw)
        template = io._json(template_raw)
        from tools import wd_q4_real_card_decision_cli as dc
        if (type(decision) is not dict or set(decision) != dc._KEYS
                or decision['schema'] != 'wd.q4-real-card-decision-request.v1'
                or decision['mode'] != 'real'
                or decision['source_mode'] != 'synthetic_fixture'
                or type(template) is not dict or set(template) != io._KEYS
                or template['schema'] != 'wd.q4-real-card-snapshot-request.v1'
                or template['source_mode'] != 'synthetic_fixture'
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
        from tools import wd_q4_external_generation_anchor as anchor
        from waggledance.core.bridge_log_reader import BridgeCursor
        c, a = decision['cursor'], decision['anchor']
        synthetic_paths = [io._path(decision['source_path']), io._path(a['root'], directory=True),
                           io._path(decision['git']['repository'], directory=True)]
        synthetic_paths.extend(io._path(spec['path']) for spec in decision['inputs'].values())
        if any('.codex-audit' not in p.parts or p.is_relative_to(out) or out.is_relative_to(p)
               for p in synthetic_paths):
            raise ValueError()
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
        snapshot_out = out / 'snapshot'; snapshot_out.mkdir()
        result = dc.run(enabled=True, request_path=str(decision_path),
                        request_sha256=request['decision']['sha256'], output_root=str(decision_out))
        decision_file = decision_out / 'card-decision.json'
        raw = io._read(decision_file, result['decision_sha256'], 256 * 1024)
        if result['status'] != 'WRITTEN' or _sha(raw) != result['decision_sha256']:
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
        return 3
    except Refused:
        print('{"status":"REFUSED"}', file=sys.stderr)
        return 2
    except Unknown:
        print('{"status":"UNKNOWN"}', file=sys.stderr)
        return 3


if __name__ == '__main__':
    sys.exit(main())

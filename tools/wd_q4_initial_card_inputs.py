"""Default-off, hash-pinned builder of the initial real P1 card inputs; writes only new files in an empty audit dir.

One caller spec, pinned by sha256, selects one of two phases:

* ``publish``: the roster snapshot (from pinned fleet bytes), the approved git anchors document (exactly one explicit
  ref and full commit, the commit is the authority, never the mutable ref), and one typed publish request per card
  (kind merged, outcome green, next none, no tags, an explicit PR number and head). Every card head must be observed
  reachable from the pinned anchor commit with a pinned git executable, and every claim must pass the public card
  preparer for its roster author, before anything is written.
* ``candidate``: the real decision request, snapshot template, finish template and real candidate request for the
  card audit orchestrator, from the pinned publish-phase roster and anchors document, a pinned bridge prefix pin
  receipt and the caller's anchor tuple, in the IM9 P1 layout (run P/RUN, anchor P/.codex-audit/anchors/ID).

It never reads a bridge log, publishes, installs, deploys or chooses a head. Pins are caller assertions, not
authentication. The output dir is an existing empty direct child of a .codex-audit dir. Refusal happens before any
write; once a write has begun, any error is UNKNOWN and the output dir must not be reused. Nothing about the inputs
is printed.
"""
import hashlib
import json
import re
import sys
from pathlib import Path

from tools import wd_q4_real_card_snapshot_cli as files

_SPEC = 'wd.q4-initial-card-inputs-spec.v1'
_MANIFEST = 'wd.q4-initial-card-inputs-manifest.v1'
_HEX = re.compile(r'[0-9a-f]{64}\Z')
_COMMIT = re.compile(r'[0-9a-f]{40}\Z')
_REF = re.compile(r'refs/heads/[A-Za-z0-9][A-Za-z0-9._/-]{0,190}\Z')
_CONFIGS = frozenset({'policy', 'public_policy', 'card_policy', 'vocabulary', 'path_allowlist'})
_PUBLISH_KEYS = frozenset({'schema', 'phase', 'now_utc', 'fleet', 'configs', 'git', 'anchor', 'cards'})
_CANDIDATE_KEYS = frozenset({'schema', 'phase', 'now_utc', 'configs', 'roster', 'approved_anchors', 'git',
                             'source_path', 'prefix_pin', 'source_anchor', 'expected_card_module_sha256', 'encoder',
                             'trusted_model_root', 'base_plan', 'fixed_template', 'composer_options',
                             'shared_stage_root', 'generation', 'generation_root', 'audit_output_root'})
_CARD_KEYS = frozenset({'pr', 'head', 'author', 'valid_until_utc'})
_OPTION_KEYS = ('expected_bundle', 'bundle_store_root', 'active_bundle_root', 'expected_state_sha256')
_LIMIT = 1024 * 1024


class Refused(ValueError):
    pass


class Unknown(RuntimeError):
    pass


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('ascii')


def _write_new(path, data):
    with open(path, 'xb') as stream:
        stream.write(data)


def _pinned(spec):
    path, raw = files._spec(spec, _LIMIT)
    return {'path': str(path), 'sha256': spec['sha256']}, raw


def _git(spec):
    if type(spec) is not dict or set(spec) != {'executable', 'sha256', 'repository'}:
        raise ValueError()
    executable = files._path(spec['executable'])
    files._read(executable, spec['sha256'], 64 * 1024 * 1024)
    repository = files._path(spec['repository'], directory=True)
    return {'executable': str(executable), 'sha256': spec['sha256'], 'repository': str(repository)}


def _configs(spec):
    if type(spec) is not dict or set(spec) != _CONFIGS:
        raise ValueError()
    pinned = {name: _pinned(spec[name]) for name in sorted(_CONFIGS)}
    return {name: pin for name, (pin, _) in pinned.items()}, {name: raw for name, (_, raw) in pinned.items()}


def _inside(child, parent):
    child, parent = Path(child), Path(parent)
    return child == parent or child.is_relative_to(parent)


def _publish(spec, output):
    import datetime as dt
    from tools import wd_q4_public_card_prefix_verifier as verifier
    from tools import wd_q4_public_card_prepare as prepare
    from tools import wd_q4_public_git_heads as heads
    from tools import wd_q4_roster_from_fleet as roster_mod
    if set(spec) != _PUBLISH_KEYS:
        raise ValueError()
    configs, raw_configs = _configs(spec['configs'])
    fleet_spec, fleet_raw = _pinned(spec['fleet'])
    roster_raw, roster_pin = roster_mod.build_roster_snapshot(fleet_raw, fleet_spec['sha256'], enabled=True)
    git = _git(spec['git'])
    anchor = spec['anchor']
    if (type(anchor) is not dict or set(anchor) != {'ref', 'commit'} or type(anchor['ref']) is not str
            or not _REF.fullmatch(anchor['ref']) or type(anchor['commit']) is not str
            or not _COMMIT.fullmatch(anchor['commit'])):
        raise ValueError()
    cards = spec['cards']
    if (type(cards) is not list or not 1 <= len(cards) <= 4
            or any(type(card) is not dict or set(card) != _CARD_KEYS for card in cards)
            or any(type(card['pr']) is not int or not 1 <= card['pr'] <= 9_999_999 for card in cards)
            or len({card['pr'] for card in cards}) != len(cards)
            or any(type(card['head']) is not str or not _COMMIT.fullmatch(card['head']) for card in cards)):
        raise ValueError()
    observed = heads.observe(enabled=True, git_executable=git['executable'], repository=git['repository'],
                             anchors={anchor['ref']: anchor['commit']})
    if (observed.get('status') != 'OBSERVED_LOCAL_PINNED_ANCESTRY'
            or any(card['head'] not in observed['heads'] for card in cards)):
        raise ValueError()
    roster_doc = json.loads(roster_raw)
    uuids = {row['agent']: row['agent_uuid'] for row in roster_doc['lanes'] + [roster_doc['tools_supervisor']]}
    roster_path = output / 'roster.json'
    outputs = {'roster.json': roster_raw,
               'approved-git-anchors.json': _canonical({'schema': 'wd.q4-approved-git-anchors.v1',
                                                        'anchors': {anchor['ref']: anchor['commit']}})}
    for card in cards:
        claim = {'v': '1', 'kind': 'merged', 'subject': {'pr': card['pr']}, 'outcome': 'green', 'next': 'none',
                 'valid_until_utc': card['valid_until_utc'], 'tags': [], 'head': card['head']}
        author = card['author']
        if type(author) is not str or author not in uuids:
            raise ValueError()
        pins = {name + '_bytes': raw_configs[name] for name in _CONFIGS}
        pins.update({name + '_sha256': configs[name]['sha256'] for name in _CONFIGS})
        prepared = prepare.prepare(enabled=True, author=author, agent_uuid=uuids[author], created_utc=spec['now_utc'],
                                   claim=claim, roster_bytes=roster_raw, roster_sha256=roster_pin, **pins)
        if prepared.get('status') != 'PREPARED':
            raise ValueError()
        # The publisher refuses a card that could expire during its two-minute rollover.
        if verifier._utc(claim['valid_until_utc'])[0] < verifier._utc(spec['now_utc'])[0] + dt.timedelta(minutes=2):
            raise ValueError()
        inputs = {name: configs[name] for name in _CONFIGS}
        inputs['roster'] = {'path': str(roster_path), 'sha256': roster_pin}
        outputs['publish-request-pr%d.json' % card['pr']] = _canonical(
            {'schema': 'wd.q4-public-card-publish-request.v1', 'claim': claim, 'inputs': inputs})
    return outputs


def _candidate(spec, output):
    from sandbox.mind.step2 import public_knowledge_card as card_mod
    from sandbox.mind.step2 import shared_capture_adapter as capture
    from sandbox.mind.step2 import shared_card_projection as projection
    from tools import wd_q4_card_audit_orchestrator as orchestrator
    from tools import wd_q4_lane_link as link
    from tools import wd_q4_staged_composer as composer
    if set(spec) != _CANDIDATE_KEYS:
        raise ValueError()
    card_mod._time(spec['now_utc'])
    configs, raw_configs = _configs(spec['configs'])
    roster, roster_raw = _pinned(spec['roster'])
    capture.strict_pins(raw_configs['policy'], configs['policy']['sha256'], roster_raw, roster['sha256'])
    anchors_spec, anchors_raw = _pinned(spec['approved_anchors'])
    anchors_doc = card_mod._document(anchors_raw, anchors_spec['sha256'], 'wd.q4-approved-git-anchors.v1',
                                     {'schema', 'anchors'})
    git = _git(spec['git'])
    source = spec['source_path']
    if type(source) is not str or not Path(source).is_absolute() or '..' in Path(source).parts:
        raise ValueError()
    pin_spec, pin_raw = _pinned(spec['prefix_pin'])
    pin = files._json(pin_raw)
    if (type(pin) is not dict or set(pin) != {'schema', 'cursor', 'prefix_sha256', 'source_authentication',
                                              'freshness', 'completeness', 'scope'}
            or pin['schema'] != 'wd.q4-private-bridge-prefix-pin.v1'
            or any(pin[key] != 'UNKNOWN' for key in ('source_authentication', 'freshness', 'completeness'))
            or pin['scope'] != 'bounded_observed_prefix' or type(pin['prefix_sha256']) is not str
            or not _HEX.fullmatch(pin['prefix_sha256'])):
        raise ValueError()
    cursor = pin['cursor']
    if (type(cursor) is not dict or set(cursor) != {'offset', 'file_identity', 'generation'}
            or type(cursor['offset']) is not int or cursor['offset'] <= 0
            or type(cursor['file_identity']) is not str or not cursor['file_identity']
            or cursor['generation'] is not None):
        raise ValueError()
    if spec['expected_card_module_sha256'] != projection.module_sha256():
        raise ValueError()
    # IM9 P1 layout under the orchestrator's code-pinned parent, checked lexically here and again by the orchestrator.
    parent = Path(orchestrator._GENERATION_PARENT)
    audit, anchors_dir = parent / '.codex-audit', parent / '.codex-audit' / 'anchors'
    source_anchor = spec['source_anchor']
    if (type(source_anchor) is not dict or set(source_anchor) != {'root', 'generation_sha256', 'binding_sha256'}
            or type(source_anchor['root']) is not str
            or any(type(source_anchor[k]) is not str or not _HEX.fullmatch(source_anchor[k])
                   for k in ('generation_sha256', 'binding_sha256'))):
        raise ValueError()
    anchor_root = Path(source_anchor['root'])
    run = spec['generation_root']
    audit_out = spec['audit_output_root']
    shared = spec['shared_stage_root']
    if any(type(value) is not str or not Path(value).is_absolute() or '..' in Path(value).parts
           for value in (run, audit_out, shared, spec['trusted_model_root'])):
        raise ValueError()
    run, audit_out, shared = Path(run), Path(audit_out), Path(shared)
    if (anchor_root.parent != anchors_dir or anchor_root.name.startswith('.')
            or run.parent != parent or run.name.startswith('.')
            or not audit_out.is_relative_to(audit) or audit_out == audit
            or _inside(audit_out, anchors_dir) or _inside(anchors_dir, audit_out)
            or _inside(shared, audit) or _inside(audit, shared) or _inside(shared, run) or _inside(run, shared)
            or _inside(output, audit_out) or _inside(audit_out, output)
            or type(spec['generation']) is not str or not composer._GENERATION.fullmatch(spec['generation'])
            or type(spec['encoder']) is not dict):
        raise ValueError()
    base_spec, base_raw = _pinned(spec['base_plan'])
    base = files._json(base_raw)
    plan_keys = link._PUBLIC_PLAN_SCHEMAS['wd.q4-public-wrapper-plan.v1']
    if type(base) is not dict or set(base) != plan_keys or base['schema'] != 'wd.q4-public-wrapper-plan.v1':
        raise ValueError()
    fixed_spec, fixed_raw = _pinned(spec['fixed_template'])
    fixed = files._json(fixed_raw)
    options_spec, options_raw = _pinned(spec['composer_options'])
    options = files._json(options_raw)
    if (type(fixed) is not dict or type(options) is not dict or any(key not in options for key in _OPTION_KEYS)
            or 'shared_stage_root' in options or 'generation' in options):
        raise ValueError()
    inputs = {name: configs[name] for name in _CONFIGS}
    inputs.update(roster=roster, anchors=anchors_spec)
    decision = {'schema': 'wd.q4-real-card-decision-request.v1', 'mode': 'real',
                'source_mode': 'operator_authorized_source', 'inputs': inputs,
                'expected_card_module_sha256': spec['expected_card_module_sha256'], 'source_path': source,
                'cursor': {'offset': cursor['offset'], 'file_identity': cursor['file_identity'], 'generation': None},
                'prefix_sha256': pin['prefix_sha256'],
                'anchor': {'root': str(anchor_root), 'generation_sha256': source_anchor['generation_sha256'],
                           'binding_sha256': source_anchor['binding_sha256']}, 'git': git}
    template = {'schema': 'wd.q4-real-card-snapshot-request.v1', 'source_mode': 'operator_authorized_source',
                'inputs': {name: inputs[name] for name in ('vocabulary', 'path_allowlist', 'policy', 'roster',
                                                           'card_policy')},
                'source_path': source, 'source_generation': None, 'cursor': None, 'now_utc': spec['now_utc'],
                'output_root': None, 'generation_name': 'candidate', 'git': dict(git, anchors=anchors_doc['anchors']),
                'encoder': spec['encoder'], 'trusted_model_root': spec['trusted_model_root']}
    plan = dict(base)
    for key in ('policy', 'roster', 'public_policy'):
        plan[key + '_path'] = inputs[key]['path']
        plan[key + '_sha256'] = inputs[key]['sha256']
    finish = {'schema': 'wd.q4-card-audit-finish-template.v1', 'base_plan': plan, 'fixed_template': fixed,
              'composer_options': dict(options, shared_stage_root=str(shared), generation=spec['generation'])}
    outputs = {'decision-request.json': _canonical(decision), 'snapshot-template.json': _canonical(template),
               'finish-template.json': _canonical(finish)}

    def ref(name):
        return {'path': str(output / name), 'sha256': hashlib.sha256(outputs[name]).hexdigest()}
    outputs['candidate-request.json'] = _canonical({
        'schema': 'wd.q4-card-real-candidate-request.v1', 'decision': ref('decision-request.json'),
        'snapshot_template': ref('snapshot-template.json'), 'output_root': str(audit_out),
        'finish': ref('finish-template.json'), 'generation_root': str(run)})
    return outputs


def build(*, enabled=False, spec_path=None, spec_sha256=None, output_root=None):
    if enabled is False:
        return {'status': 'OFF'}
    try:
        if enabled is not True:
            raise ValueError()
        spec_file = files._path(spec_path)
        spec_raw = files._read(spec_file, spec_sha256, _LIMIT)
        spec = files._json(spec_raw)
        output = files._path(output_root, directory=True)
        if output.parent.name != '.codex-audit' or any(output.iterdir()) or spec_file.is_relative_to(output):
            raise ValueError()
        if type(spec) is not dict or spec.get('schema') != _SPEC or spec.get('phase') not in ('publish', 'candidate'):
            raise ValueError()
        from sandbox.mind.step2 import public_knowledge_card as card_mod
        card_mod._time(spec['now_utc'])
        outputs = (_publish if spec['phase'] == 'publish' else _candidate)(spec, output)
        manifest = _canonical({'schema': _MANIFEST, 'phase': spec['phase'], 'spec_sha256': spec_sha256,
                               'files': {name: hashlib.sha256(data).hexdigest() for name, data in outputs.items()}})
        if any(output.iterdir()) or files._read(spec_file, spec_sha256, _LIMIT) != spec_raw:
            raise ValueError()
    except Exception:
        raise Refused('initial_card_inputs_refused') from None
    try:
        for name, data in outputs.items():
            _write_new(output / name, data)
        _write_new(output / 'inputs-manifest.json', manifest)
        for name, data in list(outputs.items()) + [('inputs-manifest.json', manifest)]:
            if (output / name).read_bytes() != data:
                raise ValueError()
    except BaseException:
        raise Unknown('initial_card_inputs_unknown') from None
    return {'status': 'WRITTEN', 'phase': spec['phase'], 'manifest_sha256': hashlib.sha256(manifest).hexdigest()}


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if not args:
        print('{"status":"OFF"}')
        return 0
    if (len(args) != 7 or args[0] != '--enabled' or args[1] != '--spec' or args[3] != '--spec-sha256'
            or args[5] != '--output-root'):
        print('{"status":"REFUSED"}', file=sys.stderr)
        return 2
    try:
        print(json.dumps(build(enabled=True, spec_path=args[2], spec_sha256=args[4], output_root=args[6]),
                         sort_keys=True, separators=(',', ':')))
        return 0
    except Refused:
        print('{"status":"REFUSED"}', file=sys.stderr)
        return 2
    except Unknown:
        print('{"status":"UNKNOWN"}', file=sys.stderr)
        return 3


if __name__ == '__main__':
    sys.exit(main())

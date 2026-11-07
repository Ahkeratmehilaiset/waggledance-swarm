"""Default-off, hash-pinned builder of the initial real P1 card inputs; writes only new files in an empty audit dir.

One caller spec, pinned by sha256, selects one of two phases:

* ``publish``: the roster snapshot (from pinned fleet bytes), the approved git anchors document (exactly one explicit
  ref and full commit, the commit is the authority, never the mutable ref), and one document per card. Every card
  head must be observed reachable from the pinned anchor commit with a pinned git executable, and the would-be claim
  must pass the public card preparer for its roster author, before anything is written. Reachability alone proves
  neither that the PR was merged nor that its checks were green, so a card without evidence becomes a non-publishable
  UNVERIFIED claim draft naming the missing evidence. A typed publish request (kind merged, outcome green) is written
  only for a card whose two caller-pinned GitHub captures agree: the ``gh pr view`` JSON binds the PR number, state
  MERGED, the anchor's base branch, a past merge time, the PR head, and the merge commit equal to the card head and
  locally observed reachable from the anchor commit; the REST check-runs answer for the PR head binds exactly the six
  required check runs, each with head_sha equal to the PR head, completed successfully no later than the merge, with
  the same times as the rollup. The caller states a lane observer and an observation time between the merge and now.
  A sidecar keeps the PR head, both capture digests, the observer and time, the claim digest and the honest
  assurance: source and observation time authentication UNKNOWN, no operator attestation, no cryptographic proof,
  reader truth UNVERIFIED and the evidence link PENDING_PUBLICATION. The card schema has no field for the PR head or
  the evidence digest; the manifest binds request to sidecar before publication.
* ``link``: after a separate publication, takes the pinned publish CLI result (its card event ref), the pinned
  request and the pending sidecar. A publish result alone is never proof of a canonical append: without an
  observation the phase writes only a pending document (PENDING_DELIVERY for a queued result, PENDING_OBSERVATION
  for a written one) and no link. A link is prepared only when the caller pins an independently observed bridge
  prefix in which the card prefix verifier finds the exact event ref as a current card (MATCH_WITHIN_PREFIX) and the
  observed event payload equals the request claim. It binds the event ref to request, claim, sidecar and both capture
  digests, records the prefix pin and cursor (never the source path) with completeness UNKNOWN, and is written with
  an unposted publish request so a reader can later resolve it from the bridge; reader truth stays UNVERIFIED and
  link and source authentication UNKNOWN.
* ``candidate``: the real decision request, snapshot template, finish template and real candidate request for the
  card audit orchestrator, from the pinned publish-phase roster and anchors document, a pinned bridge prefix pin
  receipt and the caller's anchor tuple, in the IM9 P1 layout (run P/RUN, anchor P/.codex-audit/anchors/ID).

It reads a bridge log only in the link phase, as a caller-pinned prefix through the card prefix verifier; it
never publishes, posts, installs, deploys or chooses a head. ``read_card_evidence`` is the reader side: it keeps
UNVERIFIED unless a v2 link for the card event ref and the pinned captures it names match. Pins are caller assertions, not
authentication. The output dir is an existing empty direct child of a .codex-audit dir. Refusal happens before any
write. Every consumed input pin and path is checked again before the first write and after the last one; once a write
has begun, any error or changed input is UNKNOWN and the output dir must not be reused. Nothing about the inputs is
printed.
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
_EVIDENCE_KEYS = frozenset({'pr_capture', 'check_runs', 'observer', 'observed_at_utc'})
_RUN_FIELDS = frozenset({'name', 'head_sha', 'status', 'conclusion', 'started_at', 'completed_at'})
_LINK_KEYS = frozenset({'schema', 'phase', 'now_utc', 'request', 'sidecar', 'publish_result', 'observation'})
_LINK = 'wd.q4-card-evidence-link.v2'
_LINK_DOC_KEYS = frozenset({'schema', 'card_event_ref_sha256', 'publish_delivery', 'canonical', 'observation', 'pr',
                            'card_head', 'pr_head', 'merge_commit', 'base_ref', 'request_sha256', 'claim_sha256',
                            'sidecar_sha256', 'publish_result_sha256', 'pr_view_sha256', 'check_runs_sha256',
                            'reader_truth', 'link_authentication', 'source_authentication', 'pairing'})
_OBSERVATION_KEYS = frozenset({'source_path', 'generation_path', 'cursor', 'prefix_sha256'})
_UNVERIFIED = {'status': 'UNVERIFIED', 'reader_truth': 'UNVERIFIED', 'authentication': 'UNKNOWN'}
_SIDECAR = 'wd.q4-observed-gh-pr-evidence.v2'
_SIDECAR_KEYS = frozenset({'schema', 'pr', 'pr_head', 'merge_commit', 'card_head', 'base_ref', 'merged_at_utc',
                           'required_contexts', 'contexts_on', 'check_runs_head_sha', 'captures',
                           'merge_commit_reachable_from', 'source_authentication', 'operator_attested',
                           'cryptographic_proof', 'request_sha256', 'claim_sha256', 'reader_truth', 'evidence_link'})
# The caller's composer options: exactly the orchestrator's nine option keys minus 'generation', which the builder
# adds together with the optional 'shared_stage_root' (always set here, from the spec).
_OPTION_KEYS = frozenset({'expected_bundle', 'expected_commit', 'expected_manifest_sha256', 'expected_host_sha256',
                          'descriptor_options', 'bundle_store_root', 'active_bundle_root', 'expected_state_sha256'})
_OPTION_PATHS = ('expected_bundle', 'bundle_store_root', 'active_bundle_root')
_HEX_UPPER = re.compile(r'[0-9A-F]{64}\Z')
# The orchestrator overwrites these two descriptor options, so a caller value would be silently replaced.
_ORCHESTRATOR_DESCRIPTOR_KEYS = frozenset({'expected_binding', 'public_plan'})
_LIMIT = 1024 * 1024
_GIT_LIMIT = 64 * 1024 * 1024
_MISSING_EVIDENCE = ['checks_green_by_verifiable_authority', 'pr_merged_by_verifiable_authority']
# Required GitHub check runs on the PR head; a caller cannot change or weaken this set.
_REQUIRED_CONTEXTS = ('lock-resolver-check', 'security-scan', 'test (3.11)', 'test (3.12)', 'test (3.13)', 'unified')
_CAPTURE_KEYS = frozenset({'baseRefName', 'headRefOid', 'mergeCommit', 'mergedAt', 'number', 'state',
                           'statusCheckRollup'})
_CHECK_KEYS = frozenset({'__typename', 'completedAt', 'conclusion', 'detailsUrl', 'name', 'startedAt', 'status',
                         'workflowName'})
_GH_TIME = re.compile(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\Z')


class Refused(ValueError):
    pass


class Unknown(RuntimeError):
    pass


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('ascii')


def _write_new(path, data):
    with open(path, 'xb') as stream:
        stream.write(data)


def _pinned(spec, seen):
    path, raw = files._spec(spec, _LIMIT)
    seen.append((str(path), spec['sha256'], _LIMIT))
    return {'path': str(path), 'sha256': spec['sha256']}, raw


def _git(spec, seen):
    if type(spec) is not dict or set(spec) != {'executable', 'sha256', 'repository'}:
        raise ValueError()
    executable = files._path(spec['executable'])
    files._read(executable, spec['sha256'], _GIT_LIMIT)
    repository = files._path(spec['repository'], directory=True)
    seen.extend([(str(executable), spec['sha256'], _GIT_LIMIT), (str(repository), None, None)])
    return {'executable': str(executable), 'sha256': spec['sha256'], 'repository': str(repository)}


def _recheck(seen):
    """Every consumed file still has its pinned bytes and every consumed path is still the same plain path."""
    for path, pin, limit in seen:
        checked = files._path(path, directory=pin is None)
        if pin is not None:
            files._read(checked, pin, limit)


def _gh_time(value):
    import datetime as dt
    if type(value) is not str or not _GH_TIME.fullmatch(value):
        raise ValueError()
    return dt.datetime.strptime(value, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=dt.timezone.utc)


def _evidence(card, raw, runs_raw, anchor, now):
    """Validate the caller-pinned gh pr view capture and PR-head check-runs capture against the card; return the
    sidecar body or raise."""
    capture = files._json(raw)
    if type(capture) is not dict or set(capture) != _CAPTURE_KEYS:
        raise ValueError()
    merge = capture['mergeCommit']
    if (type(capture['number']) is not int or capture['number'] != card['pr'] or capture['state'] != 'MERGED'
            or capture['baseRefName'] != anchor['ref'][len('refs/heads/'):]
            or type(capture['headRefOid']) is not str or not _COMMIT.fullmatch(capture['headRefOid'])
            or type(merge) is not dict or set(merge) != {'oid'} or merge['oid'] != card['head']):
        raise ValueError()
    merged = _gh_time(capture['mergedAt'])
    checks = capture['statusCheckRollup']
    if (merged > now or type(checks) is not list
            or sorted(check.get('name') if type(check) is dict else None for check in checks)
            != sorted(_REQUIRED_CONTEXTS)):
        raise ValueError()
    for check in checks:
        if (set(check) != _CHECK_KEYS or check['__typename'] != 'CheckRun' or check['status'] != 'COMPLETED'
                or check['conclusion'] != 'SUCCESS'
                or not _gh_time(check['startedAt']) <= _gh_time(check['completedAt']) <= merged):
            raise ValueError()
    rollup = {check['name']: (check['startedAt'], check['completedAt']) for check in checks}
    listed = files._json(runs_raw)
    if (type(listed) is not dict or set(listed) != {'total_count', 'check_runs'}
            or type(listed['check_runs']) is not list or type(listed['total_count']) is not int
            or listed['total_count'] != len(listed['check_runs'])
            or sorted(run.get('name') if type(run) is dict else None for run in listed['check_runs'])
            != sorted(_REQUIRED_CONTEXTS)):
        raise ValueError()
    for run in listed['check_runs']:
        if (not _RUN_FIELDS <= set(run) or run['head_sha'] != capture['headRefOid'] or run['status'] != 'completed'
                or run['conclusion'] != 'success'
                or (run['started_at'], run['completed_at']) != rollup[run['name']]):
            raise ValueError()
    return {'schema': _SIDECAR, 'pr': card['pr'], 'pr_head': capture['headRefOid'],
            'merge_commit': merge['oid'], 'card_head': card['head'], 'base_ref': anchor['ref'],
            'merged_at_utc': capture['mergedAt'], 'required_contexts': sorted(_REQUIRED_CONTEXTS),
            'contexts_on': 'pr_head', 'check_runs_head_sha': capture['headRefOid'],
            'merge_commit_reachable_from': {anchor['ref']: anchor['commit']},
            'source_authentication': 'UNKNOWN', 'operator_attested': False, 'cryptographic_proof': False,
            'reader_truth': 'UNVERIFIED', 'evidence_link': 'PENDING_PUBLICATION'}, merged


def _configs(spec, seen):
    if type(spec) is not dict or set(spec) != _CONFIGS:
        raise ValueError()
    pinned = {name: _pinned(spec[name], seen) for name in sorted(_CONFIGS)}
    return {name: pin for name, (pin, _) in pinned.items()}, {name: raw for name, (_, raw) in pinned.items()}


def _inside(child, parent):
    child, parent = Path(child), Path(parent)
    return child == parent or child.is_relative_to(parent)


def _publish(spec, output, seen):
    import datetime as dt
    from tools import wd_q4_public_card_prefix_verifier as verifier
    from tools import wd_q4_public_card_prepare as prepare
    from tools import wd_q4_public_git_heads as heads
    from tools import wd_q4_roster_from_fleet as roster_mod
    if set(spec) != _PUBLISH_KEYS:
        raise ValueError()
    configs, raw_configs = _configs(spec['configs'], seen)
    fleet_spec, fleet_raw = _pinned(spec['fleet'], seen)
    roster_raw, roster_pin = roster_mod.build_roster_snapshot(fleet_raw, fleet_spec['sha256'], enabled=True)
    git = _git(spec['git'], seen)
    anchor = spec['anchor']
    if (type(anchor) is not dict or set(anchor) != {'ref', 'commit'} or type(anchor['ref']) is not str
            or not _REF.fullmatch(anchor['ref']) or type(anchor['commit']) is not str
            or not _COMMIT.fullmatch(anchor['commit'])):
        raise ValueError()
    cards = spec['cards']
    if (type(cards) is not list or not 1 <= len(cards) <= 4
            or any(type(card) is not dict or set(card) - {'evidence'} != _CARD_KEYS for card in cards)
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
    now = _gh_time(spec['now_utc'])
    verified, bindings = 0, {}
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
        approved = {anchor['ref']: anchor['commit']}
        if 'evidence' in card:
            pin = card['evidence']
            if (type(pin) is not dict or set(pin) != _EVIDENCE_KEYS or type(pin['observer']) is not str
                    or pin['observer'] not in uuids):
                raise ValueError()
            view_spec, view_raw = _pinned(pin['pr_capture'], seen)
            runs_spec, runs_raw = _pinned(pin['check_runs'], seen)
            sidecar, merged = _evidence(card, view_raw, runs_raw, anchor, now)
            if not merged <= _gh_time(pin['observed_at_utc']) <= now:
                raise ValueError()
            verified += 1
            inputs = {name: configs[name] for name in _CONFIGS}
            inputs['roster'] = {'path': str(roster_path), 'sha256': roster_pin}
            request_name = 'publish-request-pr%d.json' % card['pr']
            outputs[request_name] = _canonical(
                {'schema': 'wd.q4-public-card-publish-request.v1', 'claim': claim, 'inputs': inputs})
            sidecar['captures'] = {'pr_view_sha256': view_spec['sha256'], 'check_runs_sha256': runs_spec['sha256'],
                                   'observer': pin['observer'], 'observed_at_utc': pin['observed_at_utc'],
                                   'observed_at_authentication': 'UNKNOWN',
                                   'observation': 'lane_observed_github_api_capture'}
            sidecar['request_sha256'] = hashlib.sha256(outputs[request_name]).hexdigest()
            sidecar['claim_sha256'] = hashlib.sha256(_canonical(claim)).hexdigest()
            sidecar_name = 'card-evidence-pr%d.json' % card['pr']
            outputs[sidecar_name] = _canonical(sidecar)
            bindings[request_name] = sidecar_name
        else:
            outputs['claim-draft-pr%d.json' % card['pr']] = _canonical({
                'schema': 'wd.q4-initial-card-claim-draft.v1', 'truth': 'UNVERIFIED', 'publishable': False,
                'pr': card['pr'], 'head': card['head'], 'author': author,
                'valid_until_utc': card['valid_until_utc'],
                'observed': {'anchor': approved, 'head_reachable_from_anchor_commit': True},
                'missing_evidence': list(_MISSING_EVIDENCE)})
    return outputs, {'verified_claims': verified, 'evidence_bindings': bindings}


def _observe(observation, request, ref, now_utc, seen):
    """Independently observe the card event in a caller-pinned bridge prefix; refuse unless it is current there and
    its payload is exactly the request claim. Returns the observation record without the source path."""
    from tools import wd_q4_public_card_prefix_verifier as verifier
    from waggledance.core import bridge_log_reader as bridge
    if type(observation) is not dict or set(observation) != _OBSERVATION_KEYS:
        raise ValueError()
    cursor = observation['cursor']
    if (type(cursor) is not dict or set(cursor) != {'offset', 'file_identity', 'generation'}
            or type(observation['prefix_sha256']) is not str or not _HEX.fullmatch(observation['prefix_sha256'])):
        raise ValueError()
    inputs = request['inputs']
    if type(inputs) is not dict or set(inputs) != set(_CONFIGS) | {'roster'}:
        raise ValueError()
    raw = {name: _pinned(inputs[name], seen)[1] for name in ('policy', 'card_policy', 'roster', 'vocabulary',
                                                              'path_allowlist')}
    end = bridge.BridgeCursor(cursor['offset'], cursor['file_identity'], cursor['generation'])
    found = verifier.verify(
        enabled=True, source_path=observation['source_path'], generation_path=observation['generation_path'],
        end_cursor=end, expected_prefix_sha256=observation['prefix_sha256'], target_event_ref=ref,
        nonreview_policy_bytes=raw['policy'], nonreview_policy_sha256=inputs['policy']['sha256'],
        card_policy_bytes=raw['card_policy'], card_policy_sha256=inputs['card_policy']['sha256'],
        roster_bytes=raw['roster'], roster_sha256=inputs['roster']['sha256'],
        vocabulary_bytes=raw['vocabulary'], vocabulary_sha256=inputs['vocabulary']['sha256'],
        path_allowlist_bytes=raw['path_allowlist'], path_allowlist_sha256=inputs['path_allowlist']['sha256'],
        now_utc=now_utc)
    if found.get('status') != 'MATCH_WITHIN_PREFIX' or found.get('canonical_event_ref') != ref:
        raise ValueError()
    rows = verifier._read(observation['source_path'], observation['generation_path'], end,
                          observation['prefix_sha256'])
    if rows[ref].get('payload') != {'public_knowledge': request['claim']}:
        raise ValueError()
    return {'prefix_sha256': observation['prefix_sha256'], 'cursor': dict(cursor), 'status': found['status'],
            'completeness': found['completeness'], 'predecessor_refs': list(found['predecessor_refs']),
            'observed_at_utc': now_utc}


def _link(spec, output, seen):
    from tools import wd_q4_public_card_publish_cli as publish_cli
    if set(spec) != _LINK_KEYS:
        raise ValueError()
    request_spec, request_raw = _pinned(spec['request'], seen)
    sidecar_spec, sidecar_raw = _pinned(spec['sidecar'], seen)
    result_spec, result_raw = _pinned(spec['publish_result'], seen)
    request, sidecar, result = files._json(request_raw), files._json(sidecar_raw), files._json(result_raw)
    if (type(request) is not dict or set(request) != {'schema', 'claim', 'inputs'}
            or request['schema'] != 'wd.q4-public-card-publish-request.v1' or type(request['claim']) is not dict
            or type(sidecar) is not dict or set(sidecar) != _SIDECAR_KEYS or sidecar['schema'] != _SIDECAR
            or sidecar['evidence_link'] != 'PENDING_PUBLICATION' or sidecar['reader_truth'] != 'UNVERIFIED'
            or sidecar['request_sha256'] != request_spec['sha256']
            or sidecar['claim_sha256'] != hashlib.sha256(_canonical(request['claim'])).hexdigest()
            or sidecar['card_head'] != request['claim'].get('head')
            or {'pr': sidecar['pr']} != request['claim'].get('subject')):
        raise ValueError()
    publish_cli._result(result)  # exact keys, hex event ref, truth UNVERIFIED, every authentication UNKNOWN
    ref = result['event_ref_sha256']
    digests = {'request_sha256': request_spec['sha256'], 'claim_sha256': sidecar['claim_sha256'],
               'sidecar_sha256': sidecar_spec['sha256'], 'publish_result_sha256': result_spec['sha256']}
    if spec['observation'] is None:
        pending = dict(digests, schema='wd.q4-card-evidence-pending.v1', card_event_ref_sha256=ref,
                       publish_delivery=result['delivery'],
                       state='PENDING_DELIVERY' if result['delivery'] == 'queued' else 'PENDING_OBSERVATION',
                       pr=sidecar['pr'], card_head=sidecar['card_head'], reader_truth='UNVERIFIED', link=None)
        return {'card-evidence-pending-pr%d.json' % sidecar['pr']: _canonical(pending)}, {}
    observed = _observe(spec['observation'], request, ref, spec['now_utc'], seen)
    link = dict(digests, schema=_LINK, card_event_ref_sha256=ref, publish_delivery=result['delivery'],
                canonical='observed_within_pinned_prefix', observation=observed, pr=sidecar['pr'],
                card_head=sidecar['card_head'], pr_head=sidecar['pr_head'], merge_commit=sidecar['merge_commit'],
                base_ref=sidecar['base_ref'], pr_view_sha256=sidecar['captures']['pr_view_sha256'],
                check_runs_sha256=sidecar['captures']['check_runs_sha256'], reader_truth='UNVERIFIED',
                link_authentication='UNKNOWN', source_authentication='UNKNOWN',
                pairing='observed_event_payload_equals_request_claim')
    if set(link) != _LINK_DOC_KEYS:
        raise ValueError()
    link_raw = _canonical(link)
    publish = {'schema': 'wd.q4-card-evidence-link-publish-request.v1', 'posted': False,
               'proposed_task_id': 'mind-card-evidence-link-v1',
               'event': {'type': 'status', 'status': 'info', 'payload': {'card_evidence_link': link}},
               'link_sha256': hashlib.sha256(link_raw).hexdigest()}
    return {'card-evidence-link-pr%d.json' % sidecar['pr']: link_raw,
            'card-evidence-link-publish-pr%d.json' % sidecar['pr']: _canonical(publish)}, {}


def read_card_evidence(*, enabled=False, card_event_ref=None, link=None, pr_capture=None, check_runs=None,
                       now_utc=None):
    """Reader side of a v2 evidence link (for example the payload of its published event). Default off. Returns
    MATCHED only when the link names this card event ref as observed canonical within a pinned prefix and the two
    capture files at the caller's paths have the digests the link names and still validate against its PR, PR head,
    merge commit (card head) and base ref. Anything else, including any error, is UNVERIFIED. Authentication of the
    publisher, the link, the captures and the clock stays UNKNOWN in both outcomes; MATCHED is not a proof of fact."""
    if enabled is False:
        return {'status': 'OFF'}
    try:
        if (enabled is not True or type(card_event_ref) is not str or not _HEX.fullmatch(card_event_ref)
                or type(link) is not dict or set(link) != _LINK_DOC_KEYS or link['schema'] != _LINK
                or link['card_event_ref_sha256'] != card_event_ref
                or link['canonical'] != 'observed_within_pinned_prefix'
                or type(link['observation']) is not dict or link['observation'].get('status') != 'MATCH_WITHIN_PREFIX'
                or type(link['pr']) is not int
                or type(link['base_ref']) is not str or not _REF.fullmatch(link['base_ref'])):
            return dict(_UNVERIFIED)
        view_raw = files._spec({'path': pr_capture, 'sha256': link['pr_view_sha256']}, _LIMIT)[1]
        runs_raw = files._spec({'path': check_runs, 'sha256': link['check_runs_sha256']}, _LIMIT)[1]
        sidecar, _ = _evidence({'pr': link['pr'], 'head': link['card_head']}, view_raw, runs_raw,
                               {'ref': link['base_ref'], 'commit': None}, _gh_time(now_utc))
        if sidecar['pr_head'] != link['pr_head'] or sidecar['merge_commit'] != link['merge_commit']:
            return dict(_UNVERIFIED)
        return {'status': 'MATCHED', 'reader_truth': 'MATCHED_PINNED_CAPTURES', 'card_event_ref_sha256': card_event_ref,
                'pr': link['pr'], 'card_head': link['card_head'], 'pr_head': link['pr_head'],
                'canonical': 'observed_within_pinned_prefix', 'completeness': 'UNKNOWN', 'authentication': 'UNKNOWN',
                'source_authentication': 'UNKNOWN', 'link_authentication': 'UNKNOWN'}
    except Exception:
        return dict(_UNVERIFIED)


def _composer_options(options):
    """Refuse unless the caller options have the exact orchestrator key set and the types and limits its composer
    requires (paths absolute on C: as the real orchestrator requires); deeper equalities stay the composer's."""
    from tools import wd_q4_descriptor_cli as descriptor_cli
    from tools import wd_q4_lane_caller_cli as lane_caller
    from tools import wd_q4_shared_descriptor_producer as shared_descriptor
    if type(options) is not dict or set(options) != _OPTION_KEYS:
        raise ValueError()
    for key in _OPTION_PATHS:
        value = options[key]
        if (type(value) is not str or not value.isascii() or not Path(value).is_absolute()
                or Path(value).drive.upper() != 'C:' or '..' in Path(value).parts):
            raise ValueError()
    descriptor = options['descriptor_options']
    if (type(options['expected_commit']) is not str or not _COMMIT.fullmatch(options['expected_commit'])
            or type(options['expected_manifest_sha256']) is not str
            or not _HEX_UPPER.fullmatch(options['expected_manifest_sha256'])
            or any(type(options[key]) is not str or not _HEX.fullmatch(options[key])
                   for key in ('expected_host_sha256', 'expected_state_sha256'))
            or type(descriptor) is not dict
            or set(descriptor) != descriptor_cli._DESCRIPTOR_KEYS - _ORCHESTRATOR_DESCRIPTOR_KEYS
            or any(type(descriptor[key]) is not int or not 1 <= descriptor[key] <= limit
                   for key, limit in (('interpreter_max_bytes', lane_caller.MAX_INTERPRETER), ('timeout_seconds', 600),
                                      ('max_output_bytes', lane_caller.MAX_OUTPUT)))
            or type(descriptor['outer']) is not dict or set(descriptor['outer']) != shared_descriptor._OUTER_KEYS):
        raise ValueError()


def _candidate(spec, output, seen):
    from sandbox.mind.step2 import public_knowledge_card as card_mod
    from sandbox.mind.step2 import shared_capture_adapter as capture
    from sandbox.mind.step2 import shared_card_projection as projection
    from tools import wd_q4_card_audit_orchestrator as orchestrator
    from tools import wd_q4_lane_link as link
    from tools import wd_q4_staged_composer as composer
    if set(spec) != _CANDIDATE_KEYS:
        raise ValueError()
    card_mod._time(spec['now_utc'])
    configs, raw_configs = _configs(spec['configs'], seen)
    roster, roster_raw = _pinned(spec['roster'], seen)
    capture.strict_pins(raw_configs['policy'], configs['policy']['sha256'], roster_raw, roster['sha256'])
    anchors_spec, anchors_raw = _pinned(spec['approved_anchors'], seen)
    anchors_doc = card_mod._document(anchors_raw, anchors_spec['sha256'], 'wd.q4-approved-git-anchors.v1',
                                     {'schema', 'anchors'})
    git = _git(spec['git'], seen)
    source = spec['source_path']
    if type(source) is not str or not Path(source).is_absolute() or '..' in Path(source).parts:
        raise ValueError()
    pin_spec, pin_raw = _pinned(spec['prefix_pin'], seen)
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
    base_spec, base_raw = _pinned(spec['base_plan'], seen)
    base = files._json(base_raw)
    plan_keys = link._PUBLIC_PLAN_SCHEMAS['wd.q4-public-wrapper-plan.v1']
    if type(base) is not dict or set(base) != plan_keys or base['schema'] != 'wd.q4-public-wrapper-plan.v1':
        raise ValueError()
    fixed_spec, fixed_raw = _pinned(spec['fixed_template'], seen)
    fixed = files._json(fixed_raw)
    options_spec, options_raw = _pinned(spec['composer_options'], seen)
    options = files._json(options_raw)
    if type(fixed) is not dict:
        raise ValueError()
    _composer_options(options)
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
    return outputs, {}


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
        seen = [(str(spec_file), spec_sha256, _LIMIT), (str(output), None, None)]
        if output.parent.name != '.codex-audit' or any(output.iterdir()) or spec_file.is_relative_to(output):
            raise ValueError()
        if (type(spec) is not dict or spec.get('schema') != _SPEC
                or spec.get('phase') not in ('publish', 'candidate', 'link')):
            raise ValueError()
        from sandbox.mind.step2 import public_knowledge_card as card_mod
        card_mod._time(spec['now_utc'])
        phase = {'publish': _publish, 'candidate': _candidate, 'link': _link}[spec['phase']]
        outputs, extra = phase(spec, output, seen)
        manifest = _canonical(dict(extra, schema=_MANIFEST, phase=spec['phase'], spec_sha256=spec_sha256,
                                   files={name: hashlib.sha256(data).hexdigest() for name, data in outputs.items()}))
        if any(output.iterdir()):
            raise ValueError()
        _recheck(seen)
    except Exception:
        raise Refused('initial_card_inputs_refused') from None
    try:
        for name, data in outputs.items():
            _write_new(output / name, data)
        _write_new(output / 'inputs-manifest.json', manifest)
        written = dict(outputs, **{'inputs-manifest.json': manifest})
        if {path.name for path in output.iterdir()} != set(written):
            raise ValueError()
        for name, data in written.items():
            if (output / name).read_bytes() != data:
                raise ValueError()
        _recheck(seen)
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

"""Build a byte-bound lane-binding document without activation.

All pins are independently supplied by the caller. Their agreement proves byte
consistency, not source authenticity, freshness, lane authentication, full index
history, runtime readiness or installation. The snapshot is privately supplied
and never returned; callers must not log their own inputs or traceback locals.

Optional card mode (default absent): a ``wd.q4-public-wrapper-plan.v2`` plan pins
``card_decision_sha256``; the caller must then pass the decision bytes, which are
verified against that pin and the plan policy and roster pins
(``verify_route_decision``), the snapshot projection must carry the decision
projection schema, and the pin is bound as ``card_decision_sha256``. The decision
issuer is not authenticated. The snapshot preflight still validates v6 public
generations only, so a card generation is refused until it is card-aware.

The separate default-off staged-card entry reads the supplied staged files and
frozen source prefix through the C2 validator. Its result does not imply history,
source authenticity, freshness or execution of any lane.
"""
import hashlib
import hmac
import json
from pathlib import Path
import re

from sandbox.mind.step2 import shared_capture_adapter as capture
from sandbox.mind.step2 import shared_card_projection as cards
from sandbox.mind.step2 import shared_index_pointer as index_pointer
from sandbox.mind.step2 import shared_index_preflight as preflight
from tools import wd_q4_lane_binding as binding
from tools import wd_q4_lane_link as link
from tools import wd_q4_lane_query as query
from tools import wd_q4_roster_from_fleet as roster
from tools import wd_q4_secure_transport as transport
from tools import wd_q4_public_pointer_resolver as staged_resolver
from waggledance.core.bridge_log_reader import BridgeCursor

_HEX40 = re.compile(r'[0-9a-f]{40}\Z')
_HEX64 = re.compile(r'[0-9a-f]{64}\Z')


class BindingProducerRefused(ValueError):
    """Fixed refusal without source bytes, paths or pin values."""


def _digest(value):
    return hashlib.sha256(value).hexdigest()


def _checked_bytes(raw, expected, limit):
    if (type(raw) is not bytes or not 0 < len(raw) <= limit
            or type(expected) is not str or _HEX64.fullmatch(expected) is None
            or not hmac.compare_digest(_digest(raw), expected)):
        raise ValueError()


class _Active:
    """Read exclusively from the caller-supplied frozen snapshot mapping."""
    def __init__(self, files):
        self._files = files

    def read(self, name):
        if type(name) is not str or name not in self._files:
            raise ValueError()
        return self._files[name]


def _build(*, expected_commit, manifest_bytes, expected_manifest_sha256,
           fleet_bytes, expected_fleet_sha256, wrapper_bytes, expected_wrapper_sha256,
           public_plan_bytes, expected_public_plan_sha256, snapshot_bytes, expected_snapshot_sha256,
           card_decision_bytes):
    if type(expected_commit) is not str or _HEX40.fullmatch(expected_commit) is None:
        raise ValueError()
    for raw, pin, limit in ((manifest_bytes, expected_manifest_sha256, 2 * 1024 * 1024),
                            (fleet_bytes, expected_fleet_sha256, 1024 * 1024),
                            (wrapper_bytes, expected_wrapper_sha256, 1024 * 1024),
                            (public_plan_bytes, expected_public_plan_sha256, 65536),
                            (snapshot_bytes, expected_snapshot_sha256, transport._MAX_BUNDLE)):
        _checked_bytes(raw, pin, limit)
    manifest = link._json(manifest_bytes)
    if (type(manifest) is not dict or type(manifest.get('schema_version')) is not int
            or manifest['schema_version'] != 1 or manifest.get('source_commit') != expected_commit
            or type(manifest.get('files')) is not dict):
        raise ValueError()
    for name, expected in (('wd-fleet.json', expected_fleet_sha256),
                           ('Invoke-WdQ4Query.ps1', expected_wrapper_sha256)):
        value = manifest['files'].get(name)
        if type(value) is not str or value.lower() != expected:
            raise ValueError()
    plan = link._json(public_plan_bytes)
    if (type(plan) is not dict or type(plan.get('schema')) is not str
            or plan['schema'] not in link._PUBLIC_PLAN_SCHEMAS
            or set(plan) != link._PUBLIC_PLAN_SCHEMAS[plan['schema']]
            or type(plan.get('config')) is not dict
            or any(type(plan.get(key)) is not str or not plan[key]
                   for key in ('root', 'policy_path', 'roster_path', 'public_policy_path'))):
        raise ValueError()
    card = plan['schema'] == 'wd.q4-public-wrapper-plan.v2'
    if card != (card_decision_bytes is not None) or (
            card and (type(plan['card_decision_path']) is not str or not plan['card_decision_path'])):
        raise ValueError()
    plan_pins = {key: (plan['config'].get(key) if key == 'descriptor_sha256' else plan[key])
                 for key in link._PUBLIC_BIND_PINS | ({'card_decision_sha256'} if card else set())}
    if any(type(value) is not str or _HEX64.fullmatch(value) is None for value in plan_pins.values()):
        raise ValueError()
    canonical_roster, roster_sha = roster.build_roster_snapshot(fleet_bytes, expected_fleet_sha256, enabled=True)
    snapshot = transport._fixed_bundle_decode(snapshot_bytes)
    if (not hmac.compare_digest(snapshot['roster_bytes'], canonical_roster)
            or plan_pins['roster_sha256'] != roster_sha
            or _digest(snapshot['pointer_bytes']) != plan_pins['pointer_sha256']
            or _digest(snapshot['policy_bytes']) != plan_pins['policy_sha256']
            or _digest(snapshot['public_policy_bytes']) != plan_pins['public_policy_sha256']):
        raise ValueError()
    tasks, roster_data = capture.strict_pins(snapshot['policy_bytes'], plan_pins['policy_sha256'],
                                              snapshot['roster_bytes'], roster_sha)
    pointer_shape = index_pointer._parse(snapshot['pointer_bytes'])
    schema = pointer_shape.get('schema')
    if schema in (index_pointer.PREFIX_SCHEMA, index_pointer.PREFIX_HISTORY_SCHEMA):
        prefix = True
    elif schema in (index_pointer.SCHEMA, index_pointer.HISTORY_SCHEMA):
        prefix = False
    else:
        raise ValueError()
    pointer = index_pointer._parse_pointer_snapshot(snapshot['pointer_bytes'],
        expected_pointer_sha256=plan_pins['pointer_sha256'], policy_sha=plan_pins['policy_sha256'],
        roster_sha=roster_sha, prefix_required=prefix)
    if pointer['artifacts']['descriptor_sha256'] != plan_pins['descriptor_sha256']:
        raise ValueError()
    active = snapshot['active_artifact_bytes']
    if type(active) is not dict or set(active) != set(index_pointer.ARTIFACTS.values()):
        raise ValueError()
    if card:
        decision = cards.verify_route_decision(card_decision_bytes, plan_pins['card_decision_sha256'],
            policy_sha256=plan_pins['policy_sha256'], roster_sha256=roster_sha)
        projection = link._json(active['projection.json'])
        if type(projection) is not dict or projection.get('schema') != decision['projection_schema']:
            raise ValueError()
    preflight._preflight_public_snapshot(pointer, _Active(active), tasks, roster_data,
        public_policy_bytes=snapshot['public_policy_bytes'], public_policy_sha256=plan_pins['public_policy_sha256'],
        expected_source_sha256=plan_pins['source_anchor_sha256'],
        expected_encoder_sha256=plan_pins['encoder_sha256'])
    values = dict(plan_pins)
    values['projection_sha256'] = pointer['artifacts']['projection_sha256']
    document = {'schema': binding.SCHEMA, 'bundle_commit': expected_commit,
                'manifest_sha256': expected_manifest_sha256, 'wrapper_sha256': expected_wrapper_sha256,
                'public_plan_sha256': expected_public_plan_sha256, 'binding': values,
                'lanes': sorted(roster.LANES)}
    if not binding._shape(document, roster.LANES):
        raise ValueError()
    raw = json.dumps(document, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('ascii')
    return {'binding_bytes': raw, 'binding_sha256': _digest(raw)}


def build_lane_binding(*, enabled=False, expected_commit=None, manifest_bytes=None, expected_manifest_sha256=None,
                       fleet_bytes=None, expected_fleet_sha256=None, wrapper_bytes=None, expected_wrapper_sha256=None,
                       public_plan_bytes=None, expected_public_plan_sha256=None, snapshot_bytes=None,
                       expected_snapshot_sha256=None, card_decision_bytes=None):
    """Return OFF or one canonical binding/pin; never perform filesystem or network I/O."""
    if enabled is False:
        return {'status': 'OFF'}
    result = None
    try:
        if enabled is not True:
            raise ValueError()
        result = _build(expected_commit=expected_commit, manifest_bytes=manifest_bytes,
            expected_manifest_sha256=expected_manifest_sha256, fleet_bytes=fleet_bytes,
            expected_fleet_sha256=expected_fleet_sha256, wrapper_bytes=wrapper_bytes,
            expected_wrapper_sha256=expected_wrapper_sha256, public_plan_bytes=public_plan_bytes,
            expected_public_plan_sha256=expected_public_plan_sha256, snapshot_bytes=snapshot_bytes,
            expected_snapshot_sha256=expected_snapshot_sha256, card_decision_bytes=card_decision_bytes)
    except Exception:
        pass
    finally:
        del manifest_bytes, fleet_bytes, wrapper_bytes, public_plan_bytes, snapshot_bytes, card_decision_bytes
    if result is not None:
        return result
    raise BindingProducerRefused('lane_binding_producer_refused') from None


def build_staged_lane_binding(*, enabled=False, expected_commit=None, manifest_bytes=None,
        expected_manifest_sha256=None, fleet_bytes=None, expected_fleet_sha256=None,
        wrapper_bytes=None, expected_wrapper_sha256=None, public_plan_bytes=None,
        expected_public_plan_sha256=None, pointer_bytes=None, policy_bytes=None, roster_bytes=None,
        public_policy_bytes=None, card_decision_bytes=None, card_policy_bytes=None):
    """Resolve one explicit C2 snapshot into a ten-pin lane document.

    Unlike the legacy byte-only producer, this default-off path reads the
    caller-pinned staged artifacts and source prefix. The public-policy digest
    is an input/plan pin, not a claim by a staged reply. No history receipt,
    freshness, publisher authentication or complete coverage is inferred.
    """
    if enabled is False:
        return {'status': 'OFF'}
    result = None
    try:
        if enabled is not True or type(expected_commit) is not str or not _HEX40.fullmatch(expected_commit):
            raise ValueError()
        for raw, pin, limit in ((manifest_bytes, expected_manifest_sha256, 2*1024*1024),
                                (fleet_bytes, expected_fleet_sha256, 1024*1024),
                                (wrapper_bytes, expected_wrapper_sha256, 1024*1024),
                                (public_plan_bytes, expected_public_plan_sha256, 65536)):
            _checked_bytes(raw, pin, limit)
        manifest, plan = link._json(manifest_bytes), link._json(public_plan_bytes)
        if (type(manifest) is not dict or type(manifest.get('schema_version')) is not int
                or manifest['schema_version'] != 1 or manifest.get('source_commit') != expected_commit
                or type(manifest.get('files')) is not dict
                or any(type(manifest['files'].get(name)) is not str
                       or manifest['files'][name].lower() != pin
                       for name, pin in (('wd-fleet.json', expected_fleet_sha256),
                                         ('Invoke-WdQ4Query.ps1', expected_wrapper_sha256)))
                or type(plan) is not dict or plan.get('schema') != 'wd.q4-public-wrapper-plan.v3'
                or set(plan) != link._PUBLIC_PLAN_SCHEMAS['wd.q4-public-wrapper-plan.v3']
                or type(plan.get('config')) is not dict):
            raise ValueError()
        inputs = {'pointer': (pointer_bytes, 1024*1024), 'policy': (policy_bytes, 65536),
                  'roster': (roster_bytes, 1024*1024), 'public_policy': (public_policy_bytes, 65536),
                  'card_decision': (card_decision_bytes, 262144), 'card_policy': (card_policy_bytes, 65536)}
        for name, (raw, limit) in inputs.items():
            _checked_bytes(raw, plan[name + '_sha256'], limit)
            path = link._path(plan[name + '_path'], directory=False)
            if not hmac.compare_digest(link._read(path, limit), raw):
                raise ValueError()
        canonical_roster, roster_sha = roster.build_roster_snapshot(fleet_bytes, expected_fleet_sha256, enabled=True)
        if not hmac.compare_digest(canonical_roster, roster_bytes) or roster_sha != plan['roster_sha256']:
            raise ValueError()
        tasks, _ = capture.strict_pins(policy_bytes, plan['policy_sha256'], roster_bytes, roster_sha)
        capture.public_status_policy(public_policy_bytes, plan['public_policy_sha256'], tasks)
        cursor = plan['end_cursor']
        if (type(cursor) is not dict or set(cursor) != {'offset', 'file_identity', 'generation'}
                or type(cursor['offset']) is not int or cursor['offset'] < 0
                or type(cursor['file_identity']) is not str or not cursor['file_identity']
                or (cursor['generation'] is not None and type(cursor['generation']) is not str)):
            raise ValueError()
        snapshot_args = dict(root=plan['root'], pointer_bytes=pointer_bytes,
            expected_pointer_sha256=plan['pointer_sha256'], policy_bytes=policy_bytes,
            policy_sha256=plan['policy_sha256'], roster_bytes=roster_bytes, roster_sha256=roster_sha,
            card_decision_bytes=card_decision_bytes, card_decision_sha256=plan['card_decision_sha256'],
            card_policy_bytes=card_policy_bytes, source_path=plan['source_path'],
            generation_path=plan['generation_path'], end_cursor=BridgeCursor(**cursor), enabled=True)
        resolved = staged_resolver.resolve_card_snapshot_pointer(**snapshot_args)
        if (resolved['status'] != 'VALIDATED_STAGED' or resolved['source_mode'] != 'card-snapshot-v1'
                or resolved['completeness'] != 'UNKNOWN'
                or resolved['source_prefix']['sha256'] != plan['source_anchor_sha256']
                or resolved['card_policy_sha256'] != plan['card_policy_sha256']
                or resolved['descriptor_sha256'] != plan['config'].get('descriptor_sha256')
                or resolved['descriptor_path'] != plan['config'].get('descriptor')
                or resolved['memory_root'] != plan['config'].get('memory')):
            raise ValueError()
        descriptor_raw = staged_resolver._read_matching(Path(resolved['descriptor_path']),
                                                        resolved['descriptor_sha256'], limit=65536)
        descriptor = link._json(descriptor_raw)
        if (preflight._encoder_digest(descriptor['encoder']) != plan['encoder_sha256']
                or descriptor['encoder']['revision'] != plan['config'].get('runtime_revision')):
            raise ValueError()
        values = {key: (plan['config']['descriptor_sha256'] if key == 'descriptor_sha256'
                        else resolved['projection_sha256'] if key == 'projection_sha256' else plan[key])
                  for key in query.STAGED_BINDING_KEYS}
        if any(type(value) is not str or _HEX64.fullmatch(value) is None for value in values.values()):
            raise ValueError()
        if staged_resolver.resolve_card_snapshot_pointer(**snapshot_args) != resolved:
            raise ValueError()
        for name, (raw, limit) in inputs.items():
            if not hmac.compare_digest(link._read(link._path(plan[name + '_path'], directory=False), limit), raw):
                raise ValueError()
        document = {'schema': binding.SCHEMA, 'bundle_commit': expected_commit,
                    'manifest_sha256': expected_manifest_sha256, 'wrapper_sha256': expected_wrapper_sha256,
                    'public_plan_sha256': expected_public_plan_sha256, 'binding': values,
                    'lanes': sorted(roster.LANES)}
        raw = json.dumps(document, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('ascii')
        result = {'binding_bytes': raw, 'binding_sha256': _digest(raw)}
    except Exception:
        pass
    finally:
        del manifest_bytes, fleet_bytes, wrapper_bytes, public_plan_bytes, pointer_bytes
        del policy_bytes, roster_bytes, public_policy_bytes, card_decision_bytes, card_policy_bytes
    if result is not None:
        return result
    raise BindingProducerRefused('lane_binding_producer_refused') from None

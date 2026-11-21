# SPDX-License-Identifier: BUSL-1.1
"""Bind an observed generationless bridge prefix to a pinned bundle and fleet.

This is a default-off source-binding building block, not a producer, CLI,
publisher or startup-context delivery. The caller must independently authorize
the manifest hash and establish the identity of its running interpreter and
this code before calling it. Pinned files are checked, but Python's mutable
in-memory modules and same-user ABA races are not authenticated by these checks.
No source rows or personal data leave this function. Generation, rotation
continuity and source/sender authentication remain UNKNOWN.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import sys
import uuid

from tools import bridge_compact_view
from tools import wd_mind_absent_generation_prefix_readonly as prefix
from waggledance.core import bridge_log_reader as bridge

_HEX = re.compile(r'[0-9a-fA-F]{64}\Z')
_CODE = (
    ('tools.wd_mind_startup_content', 'tools-bootstrap/tools/wd_mind_startup_content.py'),
    ('tools.wd_mind_absent_generation_prefix_readonly',
     'tools-bootstrap/tools/wd_mind_absent_generation_prefix_readonly.py'),
    ('tools.bridge_compact_view', 'tools-bootstrap/tools/bridge_compact_view.py'),
    ('waggledance.core.bridge_log_reader', 'tools-bootstrap/waggledance/core/bridge_log_reader.py'),
)


class StartupSourceRefused(ValueError):
    """Fixed refusal; do not expose source data, path or underlying exception."""


@dataclass(frozen=True, slots=True)
class StartupSourceBinding:
    bundle_root: str
    manifest_sha256: str
    fleet_sha256: str
    source_path: str
    generation_path: str
    cursor: bridge.BridgeCursor
    prefix_sha256: str
    authority: str = 'none'
    source_authentication: str = 'UNKNOWN'
    sender_authentication: str = 'UNKNOWN'
    continuity: str = 'UNKNOWN'


def _same_path(left: Path, right: Path) -> bool:
    return os.path.normcase(os.path.abspath(left)) == os.path.normcase(os.path.abspath(right))


def _check_packaged_code(bundle: Path, manifest_sha256: str) -> None:
    manifest_bytes = prefix._read_pinned(bundle / 'deployment-manifest.json', manifest_sha256,
                                         prefix.MAX_CONTROL_BYTES)
    manifest = json.loads(manifest_bytes.decode('utf-8-sig'), object_pairs_hook=prefix._unique_object,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError('constant')))
    if (type(manifest) is not dict or type(manifest.get('schema_version')) is not int
            or manifest['schema_version'] != 1 or type(manifest.get('files')) is not dict):
        raise ValueError('manifest')
    for name, relative in _CODE:
        module = sys.modules.get(name)
        origin = getattr(module, '__file__', None)
        expected = bundle.joinpath(*relative.split('/'))
        pin = manifest['files'].get(relative)
        if (type(origin) is not str or not _same_path(Path(origin), expected)
                or type(pin) is not str or _HEX.fullmatch(pin) is None):
            raise ValueError('code_origin_or_pin')
        prefix._read_pinned(expected, pin, 8 * 1024 * 1024)


def bind_startup_source(*, enabled=False, bundle_root=None, expected_manifest_sha256=None,
                        source_path=None, generation_path=None, cursor=None,
                        expected_prefix_sha256=None):
    """Return a complete-prefix binding only after all source and code checks.

    An append beyond the frozen LF cursor is allowed; a truncated, replaced or
    changed prefix, appearing generation sidecar, or changed fleet refuses.
    JSON rows and policy are not evaluated here; the later reader must do that.
    """
    result = None
    try:
        if (enabled is not True or type(bundle_root) is not str or type(source_path) is not str
                or type(generation_path) is not str or type(expected_manifest_sha256) is not str
                or _HEX.fullmatch(expected_manifest_sha256) is None
                or type(expected_prefix_sha256) is not str or _HEX.fullmatch(expected_prefix_sha256) is None
                or type(cursor) is not bridge.BridgeCursor or bridge._validate_cursor(cursor)
                or cursor.generation is not None or cursor.offset <= 0):
            raise ValueError('input')
        bundle, supplied_source, supplied_sidecar = Path(bundle_root), Path(source_path), Path(generation_path)
        if (not bundle.is_absolute() or not supplied_source.is_absolute() or not supplied_sidecar.is_absolute()
                or any('..' in path.parts for path in (bundle, supplied_source, supplied_sidecar))):
            raise ValueError('path')
        source, sidecar, fleet_pin = prefix._deployment_source(bundle_root, expected_manifest_sha256)
        if not _same_path(supplied_source, source) or not _same_path(supplied_sidecar, sidecar):
            raise ValueError('source_not_fleet_runtime')
        _check_packaged_code(bundle, expected_manifest_sha256)
        expected_digest = expected_prefix_sha256.lower()
        prefix._plain(source, directory=False)
        prefix._sidecar_absent(sidecar)
        with bridge._open_log(source) as stream:
            if bridge._file_identity(stream) != cursor.file_identity:
                raise ValueError('identity')
            length = prefix._fence(stream, source, sidecar, cursor.file_identity, cursor.offset)
            stream.seek(cursor.offset - 1)
            if stream.read(1) != b'\n':
                raise ValueError('incomplete_cursor')
            digest = prefix._hash_prefix(stream, cursor.offset)
            if not hmac.compare_digest(digest, expected_digest):
                raise ValueError('prefix')
            length = prefix._fence(stream, source, sidecar, cursor.file_identity, length)
            if not hmac.compare_digest(prefix._hash_prefix(stream, cursor.offset), digest):
                raise ValueError('changed_prefix')
            length = prefix._fence(stream, source, sidecar, cursor.file_identity, length)
            _check_packaged_code(bundle, expected_manifest_sha256)
            final_source, final_sidecar, final_fleet_pin = prefix._deployment_source(
                bundle_root, expected_manifest_sha256)
            if (not _same_path(source, final_source) or not _same_path(sidecar, final_sidecar)
                    or not hmac.compare_digest(fleet_pin, final_fleet_pin)):
                raise ValueError('changed_fleet')
            length = prefix._fence(stream, source, sidecar, cursor.file_identity, length)
            if not hmac.compare_digest(prefix._hash_prefix(stream, cursor.offset), digest):
                raise ValueError('changed_prefix')
            prefix._fence(stream, source, sidecar, cursor.file_identity, length)
        result = StartupSourceBinding(str(bundle), expected_manifest_sha256.lower(), fleet_pin,
                                      str(source), str(sidecar), cursor, digest)
    except Exception:
        pass
    if result is None:
        raise StartupSourceRefused('mind_startup_source_refused') from None
    return result


_LANES = ('codex-lead-1', 'codex-tools-1', 'fable-5', 'claude-rco-1', 'claude-rco-2')
_TASK_EVENTS = frozenset(('message', 'claim', 'release', 'done', 'decision', 'blocked', 'finding', 'wake_request'))
_DECISION_STATUSES = frozenset(('rco_pass', 'rco_reject', 'changes_requested', 'blocked', 'build_consensus_pass',
                                'approved', 'rejected', 'hold', 'pass', 'fail'))


def _metadata_time(value):
    if type(value) is not str:
        raise ValueError('time')
    match = re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.(\d{1,7}))?(?:Z|\+00:00)', value)
    if match is None:
        raise ValueError('time')
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.utcoffset().total_seconds() != 0:
        raise ValueError('time')
    delta = result.astimezone(timezone.utc) - datetime(1970, 1, 1, tzinfo=timezone.utc)
    fraction = match.group(1) or ''
    return ((delta.days * 86400 + delta.seconds) * 1000000 + delta.microseconds) * 10 + (
        int(fraction[6]) if len(fraction) == 7 else 0)


def _metadata_roster(binding):
    raw = prefix._read_pinned(Path(binding.bundle_root) / 'wd-fleet.json', binding.fleet_sha256, 1024 * 1024)
    fleet = json.loads(raw.decode('utf-8-sig'), object_pairs_hook=prefix._unique_object,
                       parse_constant=lambda _: (_ for _ in ()).throw(ValueError('constant')))
    if (type(fleet) is not dict or type(fleet.get('lanes')) is not list
            or type(fleet.get('tools_supervisor')) is not dict):
        raise ValueError('roster')
    roster = {}
    for entry in fleet['lanes'] + [fleet['tools_supervisor']]:
        if type(entry) is not dict:
            raise ValueError('roster')
        name, identity = entry.get('agent'), entry.get('agent_uuid')
        if type(name) is not str or name not in _LANES or name in roster or type(identity) is not str:
            raise ValueError('roster')
        normalized = str(uuid.UUID(identity))
        if identity.lower() != normalized or normalized in roster.values():
            raise ValueError('roster')
        roster[name] = normalized
    if set(roster) != set(_LANES):
        raise ValueError('roster')
    return roster


def project_lane_metadata(*, enabled=False, binding=None, as_of_utc=None, max_age_seconds=900):
    """Return source-bound observations only, never claims of current state.

    RCO and build event text is never returned; task IDs are hashed because
    this standalone API has no independently pinned private identifier policy.
    Claimed sender UUIDs, timestamps and content hashes are not authenticated.
    This allocates the frozen prefix in memory and has no process RSS limit.
    """
    result = None
    try:
        if (enabled is not True or type(binding) is not StartupSourceBinding
                or type(max_age_seconds) is not int or not 1 <= max_age_seconds <= 86400):
            raise ValueError('input')
        now = _metadata_time(as_of_utc)
        bind_args = dict(bundle_root=binding.bundle_root, expected_manifest_sha256=binding.manifest_sha256,
                         source_path=binding.source_path, generation_path=binding.generation_path,
                         cursor=binding.cursor, expected_prefix_sha256=binding.prefix_sha256)
        if bind_startup_source(enabled=True, **bind_args) != binding:
            raise ValueError('binding')
        roster = _metadata_roster(binding)
        rows = prefix.read_complete_generationless_prefix_rows(
            binding.source_path, binding.generation_path, binding.cursor, binding.prefix_sha256)
        selected = {lane: {} for lane in _LANES}
        for reference, row in rows.items():
            if type(row) is not dict or reference != bridge_compact_view.event_id(row):
                raise ValueError('reference')
            lane, kind, sender = row.get('agent'), row.get('type'), row.get('agent_uuid')
            if (type(lane) is not str or lane not in roster or type(sender) is not str
                    or sender.lower() != roster[lane] or type(kind) is not str or kind not in _TASK_EVENTS):
                continue
            try:
                observed = _metadata_time(row.get('ts_utc'))
            except (ValueError, TypeError, OverflowError):
                continue
            ticks = now - observed
            if ticks < 0 or ticks > max_age_seconds * 10000000:
                continue
            task = row.get('task_id')
            if type(task) is not str or not task or len(task.encode('utf-8')) > 4096:
                continue
            item = {'source_event_ref':reference, 'observed_utc':row['ts_utc'],
                    'age_seconds':ticks // 10000000, 'task_id_sha256':hashlib.sha256(task.encode('utf-8')).hexdigest(),
                    'event_type':kind}
            categories = ['task_observation']
            if kind in ('claim', 'release'):
                categories.append('claim_observation')
            if kind == 'decision':
                status = row.get('status')
                item = dict(item, status=status if type(status) is str and status in _DECISION_STATUSES else 'UNKNOWN')
                categories.append('decision_observation')
            key = (observed, reference)
            for category in categories:
                old = selected[lane].get(category)
                if old is None or key > old[0]:
                    selected[lane][category] = (key, item)
        lanes = {}
        for lane in _LANES:
            observations = selected[lane]
            lanes[lane] = {name:(observations[name][1] if name in observations else 'UNKNOWN')
                           for name in ('task_observation', 'claim_observation', 'decision_observation')}
            lanes[lane].update(next_action='UNKNOWN', active_claim='UNKNOWN')
        if bind_startup_source(enabled=True, **bind_args) != binding or _metadata_roster(binding) != roster:
            raise ValueError('source_changed')
        result = {'schema':'wd.mind-lane-metadata-observation.v1', 'authority':'none',
                  'as_of_utc':as_of_utc, 'max_age_seconds':max_age_seconds,
                  'source_authentication':'UNKNOWN', 'sender_authentication':'UNKNOWN',
                  'continuity':'UNKNOWN', 'current_claims':'UNKNOWN', 'lanes':lanes}
    except Exception:
        pass
    if result is None:
        raise StartupSourceRefused('mind_startup_source_refused') from None
    return result


def project_lane_situation(*, enabled=False, binding=None, bundle_root=None, expected_manifest_sha256=None,
                           expected_policy_sha256=None, lane=None, as_of_utc=None,
                           max_age_seconds=900, identifier_policy_bytes=None,
                           expected_identifier_policy_sha256=None):
    """Derive bounded own-lane observations from a manifest-pinned screened projection.

    Policy digests are explicit caller pins, not proof of operator approval.
    A new binding requires independently supplied identifier policy bytes and
    digest. A prebound binding can be reused without repeating them; when
    supplied, the complete pair must match the binding exactly.
    Event times and declared senders are observations, not authenticated facts.
    Only explicit single-chunk ``next_action:`` messages are presented as next
    actions; no action or current claim is inferred from ordinary prose.
    """
    output = None
    try:
        if enabled is not True:
            raise ValueError('invalid_input')
        supplied_identifier = identifier_policy_bytes is not None or expected_identifier_policy_sha256 is not None
        if binding is None or supplied_identifier:
            if (type(identifier_policy_bytes) is not bytes or not 0 < len(identifier_policy_bytes) <= 65536
                    or type(expected_identifier_policy_sha256) is not str
                    or not re.fullmatch(r'[0-9a-f]{64}', expected_identifier_policy_sha256)
                    or not hmac.compare_digest(hashlib.sha256(identifier_policy_bytes).hexdigest(),
                                               expected_identifier_policy_sha256)):
                raise ValueError('identifier_policy_pin')
        if (enabled is not True or type(bundle_root) is not str or lane not in _LANES
                or type(lane) is not str or type(expected_policy_sha256) is not str
                or not re.fullmatch(r'[0-9a-f]{64}', expected_policy_sha256)
                or type(max_age_seconds) is not int or not 1 <= max_age_seconds <= 86400):
            raise ValueError('invalid_input')
        now = _metadata_time(as_of_utc)
        from tools import wd_mind_full_runtime as runtime
        from tools import wd_mind_content_query_readonly as reader
        if binding is None:
            binding = runtime.assemble_full_runtime(enabled=True, bundle_root=bundle_root,
                expected_manifest_sha256=expected_manifest_sha256, identifier_policy_bytes=identifier_policy_bytes,
                expected_identifier_policy_sha256=expected_identifier_policy_sha256)
        if (type(binding) is not runtime._PrivateBinding
                or not _same_path(Path(bundle_root), binding._PrivateBinding__bundle)
                or expected_manifest_sha256 != binding._PrivateBinding__manifest):
            raise ValueError('binding')
        snapshot = binding.snapshot_for_query()
        if supplied_identifier:
            policy_binding = snapshot.get('identifier_policy_binding')
            if (type(policy_binding) is not tuple or len(policy_binding) != 3
                    or type(policy_binding[0]) is not bytes
                    or not hmac.compare_digest(policy_binding[0], identifier_policy_bytes)):
                raise ValueError('identifier_policy_mismatch')
        if snapshot['messages']['policy_sha256'] != expected_policy_sha256:
            raise ValueError('policy_pin')
        projection, _, report = reader._verified_complete(snapshot)
        if report['status'] != 'MATCH':
            raise ValueError('projection')
        source = snapshot['messages']
        rows = prefix.read_complete_generationless_prefix_rows(
            source['source_path'], source['generation_path'], source['end_cursor'],
            json.loads(snapshot['decision_bytes'])['source_prefix']['sha256'])
        selected = {'latest_event': None, 'decision': None, 'next_action': None}
        per_task = {'decision': {}, 'next_action': {}}
        # Every candidate comes from the already screened projection; raw events
        # supply only their exact timestamp, with an independently recomputed ref.
        for record in projection['messages']['records']:
            if record['meta']['agent'] != lane:
                continue
            provenance = record['provenance']
            ref = provenance['source_event_ref']
            row = rows.get(ref)
            if type(row) is not dict or bridge_compact_view.event_id(row) != ref or row.get('agent') != lane:
                raise ValueError('source_reference')
            observed = _metadata_time(row.get('ts_utc'))
            ticks = now - observed
            available = _metadata_time(provenance['available_utc'])
            if (ticks < 0 or ticks > max_age_seconds * 10000000
                    or available >= (now - now % 10) - 10000000):
                continue
            key = (observed, ref)
            text = None
            first_text = None
            if provenance['chunk_index'] == 0:
                first_text = reader.decode_text(record['stored_text'])
                if provenance['chunk_count'] == 1:
                    text = first_text
            item = {'source_event_ref': ref, 'observed_utc': row['ts_utc'],
                    'age_seconds': ticks // 10000000, 'event_type': record['meta']['type'],
                    'task_id_sha256': record['meta']['task_id'], 'text': text}
            if not re.fullmatch(r'sha256:[0-9a-f]{64}', item['task_id_sha256']):
                item['task_id_sha256'] = 'sha256:' + hashlib.sha256(item['task_id_sha256'].encode()).hexdigest()
            categories = ['latest_event']
            if record['meta']['type'] == 'decision':
                categories.append('decision')
            if (first_text is not None and first_text.startswith('next_action:')
                    and (len(first_text) == len('next_action:') or first_text[len('next_action:')] == ' ')
                    and (text is None or (text.startswith('next_action: ') and text[len('next_action: '):].strip()))):
                categories.append('next_action')
            for category in categories:
                task_hash = item['task_id_sha256']
                old = selected[category] if category == 'latest_event' else per_task[category].get(task_hash)
                if old is None or key > old[0]:
                    chosen = dict(item)
                    if category == 'next_action' and text is not None:
                        chosen['text'] = text[len('next_action: '):]
                    if category == 'latest_event':
                        selected[category] = (key, chosen)
                    else:
                        per_task[category][task_hash] = (key, chosen)
        latest = selected['latest_event']
        if latest is not None and latest[1]['text'] is not None:
            task_hash = latest[1]['task_id_sha256']
            for category in ('decision', 'next_action'):
                selected[category] = per_task[category].get(task_hash)
        if binding.snapshot_for_query() != snapshot:
            raise ValueError('snapshot_changed')
        output = {'schema':'wd.mind-lane-situation.v2', 'authority':'none', 'lane':lane,
                  'scope':'latest_available_screened_within_frozen_prefix', 'snapshot_freshness':'UNKNOWN',
                  'continuity':'UNKNOWN',
                  'sender_authentication':'UNKNOWN', 'source_authentication':'UNKNOWN',
                  'event_time_authentication':'UNKNOWN', 'as_of_utc':as_of_utc,
                  'policy_sha256':expected_policy_sha256,
                  **{name:(value[1] if value is not None and value[1]['text'] is not None else 'UNKNOWN')
                     for name,value in selected.items()}}
    except Exception:
        pass
    finally:
        identifier_policy_bytes = policy_binding = snapshot = binding = projection = rows = None
    if output is None:
        raise StartupSourceRefused('mind_startup_source_refused') from None
    return output

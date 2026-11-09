"""Incremental, read-only bridge metadata; observations grant no authority.

An in-memory view reuses the canonical cursor/prefix reader and exact reply
binding. Open lifecycle facts keep their age under complete coverage; stale
telemetry remains unknown. Historical identities must be explicitly supplied
with provenance and a current-session selection. Event digests identify content,
not authenticated writers. No Mind import, cursor file, log append or claim
sweep occurs. Prefix digests stay private in the instance and are never output.
"""
from __future__ import annotations

import argparse
import copy
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.bridge_compact_view import event_id
from tools.check_rco_pass_present import _is_qualifying_rco_pass, _is_rco_veto_event
from tools.bridge_next_action import _is_explicit_requester_closure
from waggledance.core import bridge_log_reader as reader
from waggledance.core.bridge_request_contract import field, reply_matches_request

LANES = ('codex-lead-1', 'codex-tools-1', 'claude-rco-1', 'claude-rco-2', 'fable-5')
RCO = frozenset(('claude-rco-1', 'claude-rco-2'))
IDENTITY = ('agent_uuid', 'session_id', 'run_id')
TASK = re.compile(r'[A-Za-z0-9][A-Za-z0-9._:/-]{0,159}\Z')
HEAD = re.compile(r'[0-9a-f]{40}\Z')
EMPTY_PREFIX = hashlib.sha256(b'').hexdigest()
CAPACITY_READER = Path(r'C:\Python\Get-WdCapacityStatus.ps1')
CAPACITY_READER_SHA256 = 'b845f1df228136861818ee57f8308b8ffd563674321b2d9677678cbafa0304c1'
CAPACITY_SHELL = Path(r'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe')
HEAD_KEYS = ('head', 'head_sha', 'expected_head', 'exact_head', 'head_oid', 'head_commit', 'headRefOid')
PR_KEYS = ('pr', 'pr_number', 'pull_request', 'pull_request_number')
HEAD_LABEL = re.compile(r'\bhead\s*[:=]?\s*([0-9a-f]{40})(?![0-9a-f])', re.IGNORECASE)
META_KEYS = frozenset(('agent', 'agent_uuid', 'session_id', 'run_id', 'type', 'status', 'task_id', 'ts_utc', 'to',
    'head', 'request_id', 'request_digest', 'nonce', 'token', 'task_revision', 'expected_responders',
    'request_binding_conflict', 'in_reply_to_request_id', 'in_reply_to_request_digest', 'in_reply_to_requester', 'request_ts_utc'))
PAYLOAD_KEYS = META_KEYS | frozenset(('head', 'pr', 'kind', 'provider', 'observed_at', 'freshness',
    'pool_identity_state', 'quota_state', 'used_percent', 'claim_lease_expires_utc'))
ACK = frozenset(('received', 'seen', 'acknowledged', 'accepted', 'queued'))
ANSWER_TYPES = frozenset(('message', 'done', 'decision', 'blocked', 'finding', 'test', 'release', 'handoff'))
ANSWER_STATUSES = frozenset(('answered', 'done', 'blocked', 'rco_pass', 'changes_requested', 'reported',
    'pass', 'failed', 'incomplete', 'prepared', 'off', 'unknown', 'not_run'))


def _time(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo is not None else None
    except ValueError:
        return None


def _safe_task(value):
    return isinstance(value, str) and TASK.fullmatch(value) is not None and not any(
        marker in value for marker in ('PRIVATE_MARKER', '_DO_NOT_LEAK'))


def _payload(event):
    return event.get('payload') if isinstance(event.get('payload'), dict) else {}


def _head(event):
    payload = _payload(event)
    result = payload.get('result') if isinstance(payload.get('result'), dict) else {}
    value = event.get('head', payload.get('head', result.get('head')))
    return value if isinstance(value, str) and HEAD.fullmatch(value) else None


def _typed_metadata(event, keys, valid):
    payload = _payload(event)
    result = payload.get('result') if isinstance(payload.get('result'), dict) else {}
    values = [mapping[key] for mapping in (event, payload, result) for key in keys if key in mapping]
    if not values:
        return None, False
    if any(not valid(value) for value in values) or any(value != values[0] for value in values):
        return None, True
    return values[0], False


def read_capacity_status(*, reader_path=None, expected_sha256=CAPACITY_READER_SHA256):
    """Use only the existing pinned reader; never collect, install or switch providers."""
    path = CAPACITY_READER if reader_path is None else Path(reader_path)
    if path != CAPACITY_READER or not isinstance(expected_sha256, str) or not re.fullmatch(r'[0-9a-f]{64}', expected_sha256):
        raise ValueError('capacity_reader_pin_invalid')
    raw = path.read_bytes()
    if len(expected_sha256) != 64 or hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise ValueError('capacity_reader_pin_mismatch')
    result = subprocess.run([str(CAPACITY_SHELL), '-NoProfile', '-NonInteractive', '-File', str(path), '-Json'],
                            cwd=ROOT, capture_output=True, text=True, timeout=30)
    if hashlib.sha256(path.read_bytes()).hexdigest() != expected_sha256:
        raise ValueError('capacity_reader_pin_changed')
    if result.returncode or len(result.stdout) > 2 * 1024 * 1024:
        raise ValueError('capacity_reader_unavailable')
    data = json.loads(result.stdout.lstrip('\ufeff'))
    if (not isinstance(data, dict) or data.get('schema') != 'wd.capacity-status.v1'
            or not isinstance(data.get('installation'), dict)
            or data['installation'].get('source_verified') is not True
            or not isinstance(data.get('observations'), list) or len(data['observations']) > 64):
        raise ValueError('capacity_source_unknown')
    observations = [{key: copy.deepcopy(item[key]) for key in ('provider', 'observed_at', 'freshness',
        'pool_identity_state', 'quota_state', 'execution_allowed', 'quota_windows') if key in item}
        for item in data['observations'] if isinstance(item, dict)]
    return {'schema': 'wd.situation-capacity-source.v1', 'observed_at': data.get('observed_at'),
        'observations': observations, 'source_ref': {'kind': 'installed_capacity_reader', 'path': str(path),
            'reader_sha256': expected_sha256, 'snapshot_sha256': hashlib.sha256(result.stdout.encode()).hexdigest(),
            'manifest_sha256': data['installation'].get('manifest_sha256')}}


class SituationPicture:
    """Caller-owned cursor and metadata. A failed history read requires reset()."""

    def __init__(self, *, roster, ttl_seconds=900, max_events=100000, operator_relay_refs=()):
        if (not isinstance(roster, dict) or type(ttl_seconds) is not int or not 1 <= ttl_seconds <= 86400
                or type(max_events) is not int or not 1 <= max_events <= 200000):
            raise ValueError('invalid_configuration')
        sessions = {}
        for agent, supplied in roster.items():
            identities = supplied if isinstance(supplied, list) else [dict(supplied, current=True)] if isinstance(supplied, dict) else []
            if not isinstance(agent, str) or not 1 <= len(identities) <= 32:
                raise ValueError('invalid_roster')
            for identity in identities:
                if (not isinstance(identity, dict) or any(not isinstance(identity.get(key), str) or
                        not identity[key] for key in IDENTITY) or type(identity.get('current')) is not bool):
                    raise ValueError('invalid_roster')
                if isinstance(supplied, list) and (not isinstance(identity.get('source_event_ref'), str)
                        or not re.fullmatch(r'[0-9a-f]{64}', identity['source_event_ref'])):
                    raise ValueError('unsourced_roster')
            if (sum(identity['current'] for identity in identities) != 1
                    or len({identity['agent_uuid'] for identity in identities}) != 1
                    or len({tuple(identity[key] for key in IDENTITY) for identity in identities}) != len(identities)):
                raise ValueError('ambiguous_roster')
            sessions[agent] = copy.deepcopy(identities)
        if not isinstance(operator_relay_refs, (tuple, list)) or len(operator_relay_refs) > 100 or any(
                not isinstance(reference, str) or not re.fullmatch(r'[0-9a-f]{64}', reference) for reference in operator_relay_refs):
            raise ValueError('invalid_directive_references')
        self.roster = copy.deepcopy(roster)
        self._sessions, self._operator_relay_refs = sessions, tuple(operator_relay_refs)
        self.ttl_seconds, self.max_events = ttl_seconds, max_events
        self.reset()

    def reset(self):
        self._events, self._cursor, self._prefix = [], None, None
        self._coverage, self._blocked, self._reason = False, False, 'not_read'
        self._position, self._as_of = 0, None
        self._capacity_snapshot = None

    def _authenticated(self, event):
        return self._identity(event) is not None

    def _identity(self, event):
        if not isinstance(event.get('agent'), str):
            return None
        return next((identity for identity in self._sessions.get(event.get('agent'), ())
                     if all(event.get(key) == identity[key] for key in IDENTITY)), None)

    def _failed(self, reason):
        self._coverage, self._blocked, self._reason = False, True, reason

    def update(self, path, *, now, generation_path=None, max_bytes=4 * 1024 * 1024, max_rows=10000,
               capacity_snapshot=None):
        instant = _time(now)
        if instant is None or (self._as_of is not None and instant < self._as_of):
            raise ValueError('invalid_as_of')
        self._as_of = instant
        if capacity_snapshot is not None:
            self._capacity_snapshot = copy.deepcopy(capacity_snapshot)
        if self._blocked:
            self._reason = 'reset_required'
            return self._render()
        try:
            cursor, digest = self._cursor, self._prefix
            if cursor is None:
                probe = reader.read_bridge_log(path, max_bytes=max_bytes, max_rows=1, generation_path=generation_path)
                if probe.status not in (reader.BridgeReadStatus.OK, reader.BridgeReadStatus.IDLE) or probe.candidate_cursor is None:
                    self._coverage, self._reason = False, probe.reason
                    return self._render()
                cursor = reader.BridgeCursor(0, probe.candidate_cursor.file_identity, probe.candidate_cursor.generation)
                digest = EMPTY_PREFIX
            result = reader.read_bridge_log(path, cursor=cursor, max_bytes=max_bytes, max_rows=max_rows,
                generation_path=generation_path, expected_prefix_sha256=digest, max_prefix_bytes=reader.MAX_PREFIX_BYTES)
        except (OSError, ValueError, TypeError, AttributeError):
            self._failed('reader_unavailable_or_incompatible')
            return self._render()
        if (result.status not in (reader.BridgeReadStatus.OK, reader.BridgeReadStatus.IDLE)
                or getattr(result, 'candidate_prefix', None) is None):
            self._failed(result.reason)
            return self._render()
        staged = []
        for event in result.rows:
            self._position += 1
            if not isinstance(event.get('agent'), str):
                continue
            identity = self._identity(event)
            reported_veto = event.get('agent') in RCO and _is_rco_veto_event(event)
            if identity is None and not reported_veto:
                continue
            if len(self._events) + len(staged) >= self.max_events:
                self._failed('state_budget_exceeded')
                return self._render()
            metadata = {key: copy.deepcopy(value) for key, value in event.items() if key in META_KEYS}
            payload = {key: copy.deepcopy(value) for key, value in _payload(event).items() if key in PAYLOAD_KEYS}
            raw_result = _payload(event).get('result')
            if isinstance(raw_result, dict):
                payload['result'] = {key: copy.deepcopy(raw_result[key]) for key in ('status', 'head', 'pr') if key in raw_result}
            metadata['payload'] = payload
            head, head_conflict = _typed_metadata(event, HEAD_KEYS, lambda value: isinstance(value, str) and HEAD.fullmatch(value))
            pr, pr_conflict = _typed_metadata(event, PR_KEYS, lambda value: type(value) is int and value > 0)
            mentioned = set(HEAD_LABEL.findall(str(event.get('message', '')))) if head is None and not head_conflict else set()
            metadata['_typed_head'], metadata['_head_conflict'] = head, head_conflict or len(mentioned) > 1
            metadata['_referenced_head'] = head or (next(iter(mentioned)).lower() if len(mentioned) == 1 else None)
            metadata['_pr'], metadata['_pr_conflict'] = pr, pr_conflict
            metadata['_identity'] = copy.deepcopy(identity)
            metadata['_reported_veto'] = reported_veto
            metadata['_requester_closeout'] = _is_explicit_requester_closure(event)
            metadata['_qualifying_pass'] = bool(identity is not None and head and event.get('agent') in RCO
                and _is_qualifying_rco_pass(event, head, event['agent']))
            staged.append((metadata, event_id(event), self._position))
        self._events.extend(staged)
        self._cursor = result.candidate_prefix.cursor
        self._prefix = result.candidate_prefix.sha256
        self._coverage = self._cursor.offset == result.snapshot_length
        self._reason = 'complete_snapshot' if self._coverage else 'incomplete_snapshot'
        return self._render()

    def _field(self, value=None, record=None, *, reason=None, persistent=False):
        event, reference, _ = record if record is not None else ({}, None, None)
        timestamp = _time(event.get('ts_utc'))
        age = (self._as_of - timestamp).total_seconds() if timestamp is not None else None
        why = reason or (None if self._coverage else self._reason)
        if why is None:
            why = ('unobserved' if record is None or value is None else 'invalid_timestamp' if age is None
                   else 'future_timestamp' if age < 0 else 'stale' if not persistent and age > self.ttl_seconds else None)
        return {'state': 'UNKNOWN' if why else 'OBSERVED', 'value': None if why else value,
                'source_event_ref': reference, 'event_ts_utc': event.get('ts_utc'),
                'age_seconds': age, 'reason': why}

    def _capacity_fields(self):
        frame = self._capacity_snapshot
        if not isinstance(frame, dict) or frame.get('schema') != 'wd.situation-capacity-source.v1':
            return self._field(), self._field()
        origin = frame.get('source_ref')
        timestamp = _time(frame.get('observed_at'))
        wrapper_age = (self._as_of - timestamp).total_seconds() if timestamp else None
        record = ({'ts_utc': frame.get('observed_at')}, None, 0)
        if (not isinstance(origin, dict) or origin.get('kind') != 'installed_capacity_reader'
                or not re.fullmatch(r'[0-9a-f]{64}', str(origin.get('reader_sha256', '')))
                or wrapper_age is None or not 0 <= wrapper_age <= self.ttl_seconds):
            unknown = self._field(record=record, reason='capacity_source_or_wrapper_stale')
            unknown['source_ref'] = origin
            return unknown, copy.deepcopy(unknown)
        providers, routes = {}, {}
        observations = frame.get('observations', [])
        for provider in ('codex', 'claude'):
            rows = [row for row in observations if isinstance(row, dict) and row.get('provider') == provider]
            if len(rows) != 1:
                value = self._field(record=record, reason='provider_observation_missing_or_ambiguous')
            else:
                row = rows[0]
                provider_record = ({'ts_utc': row.get('observed_at')}, None, 0)
                windows = row.get('quota_windows')
                valid = (row.get('freshness') == 'fresh' and isinstance(windows, list) and 1 <= len(windows) <= 16
                    and all(isinstance(window, dict) and type(window.get('used_percent')) in (int, float)
                            and 0 <= window['used_percent'] <= 100 for window in windows))
                safe_windows = [{key: window[key] for key in ('limit_id', 'name', 'used_percent', 'resets_at',
                    'window_duration_minutes') if key in window} for window in windows] if valid else []
                value = self._field({'quota_windows': safe_windows, 'pool_identity_state': row.get('pool_identity_state'),
                    'quota_state': row.get('quota_state'), 'execution_allowed': row.get('execution_allowed') is True,
                    'authorization': 'none'}, provider_record, reason=None if valid else 'quota_observation_unknown')
                if (value['state'] == 'OBSERVED' and row.get('pool_identity_state') == 'verified'
                        and row.get('quota_state') == 'observed_headroom' and row.get('execution_allowed') is True):
                    used = max(window['used_percent'] for window in windows)
                    routes[provider] = self._field('defer_new_cost' if used >= 90 else 'serial_existing_work'
                        if used >= 50 else 'continue_existing_work', provider_record)
                    routes[provider]['source_ref'] = origin
            value['source_ref'] = origin
            providers[provider] = value
        capacity = self._field(providers, record)
        capacity['source_ref'] = origin
        routing = self._field(routes, record) if routes else self._field(record=record, reason='capacity_binding_or_freshness_unknown')
        routing['source_ref'] = origin
        return capacity, routing

    def _render(self):
        names = ('task', 'claim', 'last_bound_reply', 'head', 'last_referenced_head')
        lanes = {lane: {name: self._field() for name in names} for lane in LANES}
        requests, duplicate_ids, reply_index, closure_index = {}, set(), {}, {}
        session_fields, gate_index, veto_records = {}, {}, []
        metadata_index, veto_task_index, veto_head_index = {}, {}, {}
        newest = None
        for record in self._events:
            event, reference, position = record
            lane, kind, identity = event['agent'], event.get('type'), event.get('_identity')
            if event.get('_reported_veto'):
                veto_records.append(record)
                veto_task_index.setdefault(event.get('task_id'), []).append(record)
                if event.get('_typed_head'):
                    veto_head_index.setdefault(event['_typed_head'], []).append(record)
            if identity is None:
                continue
            key_session = (lane, *(event.get(key) for key in IDENTITY))
            fields = session_fields.setdefault(key_session, {name: self._field() for name in names})
            persistent = not identity['current']
            if lane in lanes:
                if _safe_task(event.get('task_id')):
                    fields['task'] = self._field(event['task_id'], record, persistent=persistent)
                if event.get('_referenced_head') or event.get('_head_conflict'):
                    value = self._field(event.get('_referenced_head'), record, persistent=persistent,
                        reason='head_metadata_conflict' if event.get('_head_conflict') else None)
                    value['git_verified'] = False
                    fields['head'] = fields['last_referenced_head'] = value
                if kind in ('claim', 'release') and _safe_task(event.get('task_id')):
                    fields['claim'] = self._field({'task': event['task_id'],
                        'state': 'claim_event_observed' if kind == 'claim' else 'release_event_observed',
                        'live_lease_verified': False}, record, persistent=persistent)
                if identity['current']:
                    lanes[lane].update(fields)
            if lane in RCO and event.get('_qualifying_pass'):
                gate_index.setdefault((lane, event.get('task_id'), event.get('_typed_head')), []).append(record)
            if event.get('type') in ('decision', 'rco_review') and event.get('_pr') and event.get('_typed_head'):
                metadata_index.setdefault((event.get('task_id'), event['_typed_head']), []).append(record)
            rid = field(event, 'request_id')
            if isinstance(rid, str) and _safe_task(rid) and isinstance(field(event, 'expected_responders'), dict):
                key = (lane, rid)
                if key in requests and requests[key][1] != reference:
                    duplicate_ids.add(key)
                else:
                    requests[key] = record
            reply_id = field(event, 'in_reply_to_request_id')
            if isinstance(reply_id, str):
                if event.get('_requester_closeout'):
                    closure_index.setdefault((lane, reply_id), []).append(record)
                status = event.get('status')
                if (kind in ANSWER_TYPES and isinstance(status, str) and status.lower() in ANSWER_STATUSES
                        and isinstance(_payload(event).get('result'), dict)):
                    reply_index.setdefault((lane, reply_id), []).append(record)
        waiting, reviews, pr_reviews, last_replies = [], [], [], {}
        for key, sent in requests.items():
            request, reference, position = sent
            if newest is None or position > newest[2]:
                newest = sent
            if key in duplicate_ids:
                continue
            for target in field(request, 'expected_responders'):
                if target not in LANES:
                    continue
                answers = [record for record in reply_index.get((target, key[1]), ())
                    if reply_matches_request(request, record[0], target,
                        request_position=position, reply_position=record[2])]
                answered = answers[-1] if answers else None
                closed = any(reply_matches_request(request, record[0], target, requester_closure=True,
                    request_position=position, reply_position=record[2])
                    for record in closure_index.get((key[0], key[1]), ()))
                if answered is not None:
                    session_key = (target, *(answered[0].get(name) for name in IDENTITY))
                    if session_key not in last_replies or answered[2] > last_replies[session_key][1][2]:
                        last_replies[session_key] = (key[1], answered)
                evidence = answered if answered is not None else sent
                observation = self._field(True, evidence, persistent=True)
                item = {'request_ref': reference, 'source_event_ref': evidence[1],
                    'age_seconds': observation['age_seconds'], 'state': observation['state'],
                    'requester': request['agent'] if observation['value'] else None,
                    'responder': target if observation['value'] else None,
                    'reply_ref': answered[1] if answered is not None else None,
                    'result_state': 'BOUND_RESULT_UNVERIFIED' if answered else 'NO_BOUND_RESULT'}
                if not closed and answered is None:
                    waiting.append(item)
                if target in RCO and not closed:
                    pr, head = request.get('_pr'), request.get('_typed_head')
                    linked = metadata_index.get((request.get('task_id'), request.get('_referenced_head')), [])
                    pairs = {(record[0]['_pr'], record[0]['_typed_head']) for record in linked}
                    if pr is None and not request.get('_pr_conflict') and len(pairs) == 1 and not request.get('_head_conflict'):
                        pr, head = next(iter(pairs))
                    relevant_vetoes = {record[1]: record for record in
                        veto_task_index.get(request.get('task_id'), []) + veto_head_index.get(head, [])}
                    vetoes = sorted(relevant_vetoes.values(), key=lambda record: record[2])
                    passed = any(record[2] > position
                        and (record[0].get('_pr') is None or pr is None or record[0]['_pr'] == pr)
                        for record in gate_index.get((target, request.get('task_id'), head), ())) and not vetoes and head is not None
                    if not passed:
                        review = {**item, 'pr': pr, 'pr_state': 'OBSERVED' if pr else 'UNKNOWN',
                            'head': head, 'last_referenced_head': request.get('_referenced_head'),
                            'git_verified': False, 'gate_pass_observed': False, 'merge_eligibility': 'NOT_EVALUATED',
                            'metadata_source_refs': [reference] + [record[1] for record in linked],
                            'result_state': 'REPORTED_VETO' if vetoes else item['result_state'],
                            'veto_event_refs': [record[1] for record in vetoes]}
                        reviews.append(review)
                        if pr:
                            pr_reviews.append(review)
                if newest is None or evidence[2] > newest[2]:
                    newest = evidence
        for session_key, (request_id, answered) in last_replies.items():
            value = self._field({'request_id': request_id, 'status': answered[0]['status'].lower(),
                'content_verified': False}, answered, persistent=not answered[0]['_identity']['current'])
            session_fields[session_key]['last_bound_reply'] = value
            if answered[0]['_identity']['current']:
                lanes[session_key[0]]['last_bound_reply'] = value
        for lane in LANES:
            lanes[lane]['history'] = [{'session_id': identity['session_id'], 'run_id': identity['run_id'],
                'identity_source_ref': identity.get('source_event_ref'), 'identity_verification': 'explicit_caller_roster',
                **session_fields[(lane, *(identity[name] for name in IDENTITY))]}
                for identity in self._sessions.get(lane, ()) if not identity['current']
                and (lane, *(identity[name] for name in IDENTITY)) in session_fields]
        directives, operator_record = [], None
        for record in self._events:
            if record[1] in self._operator_relay_refs and record[0].get('_identity') is not None and record[0]['agent'] in LANES:
                observed = self._field(True, record)
                directives.append({'source_event_ref': record[1], 'age_seconds': observed['age_seconds'],
                    'state': observed['state'], 'relay_author': record[0]['agent'], 'operator_origin_verified': False,
                    'operator_origin': 'UNKNOWN', 'task': record[0].get('task_id') if _safe_task(record[0].get('task_id')) else None})
                operator_record = record
        direct_labels = [record for record in self._events if record[0]['agent'] == 'operator']
        missing_relays = set(self._operator_relay_refs) - {item['source_event_ref'] for item in directives}
        directive_field = self._field(directives, operator_record,
            reason='selected_relay_unavailable' if missing_relays else None)
        if not directives and direct_labels:
            directive_field = self._field(record=direct_labels[-1], reason='operator_origin_unverified')
        capacity, routing = self._capacity_fields()
        ambiguity = 'ambiguous_request_id' if duplicate_ids else None
        veto_items = [{'source_event_ref': record[1], 'age_seconds': self._field(True, record, persistent=True)['age_seconds'],
            'writer_identity_in_roster': record[0].get('_identity') is not None,
            'head': record[0].get('_typed_head'), 'retracted': False} for record in veto_records]
        return {'schema': 'wd.bridge-situation-picture.v1', 'authority': 'none', 'as_of': self._as_of.isoformat(),
            'coverage': 'COMPLETE' if self._coverage else 'UNKNOWN', 'reason': self._reason,
            'cursor': asdict(self._cursor) if self._cursor else None, 'lanes': lanes,
            'identity_scope': 'explicit_caller_roster_not_event_self_authentication',
            'veto_scope': 'reported_observations_not_complete_gate_scan', 'merge_eligibility': 'NOT_EVALUATED',
            'waiting_for': self._field(waiting, newest, persistent=True, reason=ambiguity),
            'review_requests_awaiting_rco': self._field(reviews, newest, persistent=True, reason=ambiguity),
            'prs_awaiting_rco': self._field(pr_reviews, newest, persistent=True, reason=ambiguity),
            'recognized_veto_observations': self._field(veto_items, veto_records[-1] if veto_records else newest, persistent=True),
            'operator_directives': directive_field, 'capacity': capacity, 'routing_suggestion': routing}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--events', type=Path, required=True)
    parser.add_argument('--roster', type=Path, required=True)
    parser.add_argument('--as-of', required=True)
    parser.add_argument('--generation', type=Path)
    parser.add_argument('--ttl-seconds', type=int, default=900)
    parser.add_argument('--roster-sha256', help='Required for historical-session rosters; supplied integrity pin, not an identity signature')
    parser.add_argument('--operator-relay-ref', action='append', default=[], help='Explicit canonical relay event reference; operator origin remains unknown')
    parser.add_argument('--capacity-status', action='store_true', help='Read the existing hash-pinned installed capacity status helper once; no collection')
    args = parser.parse_args(argv)
    try:
        raw = args.roster.read_bytes()
        if len(raw) > 65536:
            raise ValueError('roster_too_large')
        roster = json.loads(raw)
        digest = hashlib.sha256(raw).hexdigest()
        if args.roster_sha256 is not None and args.roster_sha256 != digest:
            raise ValueError('roster_pin_mismatch')
        if isinstance(roster, dict) and any(isinstance(value, list) for value in roster.values()) and not args.roster_sha256:
            raise ValueError('historical_roster_pin_required')
        picture = SituationPicture(roster=roster, ttl_seconds=args.ttl_seconds, operator_relay_refs=args.operator_relay_ref)
        capacity = None
        if args.capacity_status:
            try:
                capacity = read_capacity_status()
            except (OSError, ValueError, TypeError, subprocess.SubprocessError):
                capacity = {'schema': 'wd.situation-capacity-unavailable.v1'}
        for _ in range(128):
            report = picture.update(args.events, now=args.as_of, generation_path=args.generation, capacity_snapshot=capacity)
            if report['coverage'] == 'COMPLETE' or report['reason'] != 'incomplete_snapshot':
                break
        report['roster_source_ref'] = {'path': str(args.roster), 'bytes': len(raw), 'sha256': digest,
            'integrity_pin_supplied': args.roster_sha256 is not None, 'identity_verification': 'caller_supplied_roster'}
        print(json.dumps(report, sort_keys=True, ensure_ascii=True, allow_nan=False))
        return 0 if report['coverage'] == 'COMPLETE' else 2
    except (OSError, ValueError, TypeError):
        print('{"coverage":"UNKNOWN","reason":"invalid_source_or_configuration","authority":"none"}')
        return 2


if __name__ == '__main__':
    raise SystemExit(main())

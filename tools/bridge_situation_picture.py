"""Incremental, read-only bridge metadata; observations grant no authority.

An in-memory view reuses the canonical cursor/prefix reader and exact reply
binding. Unknown, stale, incomplete or changed history never becomes a current
fact. Event digests identify content, not authenticated writers: roster identity
is an explicit caller input. No Mind import, cursor file, log append or claim
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
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.bridge_compact_view import event_id
from waggledance.core import bridge_log_reader as reader
from waggledance.core.bridge_request_contract import field, reply_matches_request

LANES = ('codex-lead-1', 'codex-tools-1', 'claude-rco-1', 'claude-rco-2', 'fable-5')
RCO = frozenset(('claude-rco-1', 'claude-rco-2'))
IDENTITY = ('agent_uuid', 'session_id', 'run_id')
TASK = re.compile(r'[A-Za-z0-9][A-Za-z0-9._:/-]{0,159}\Z')
HEAD = re.compile(r'[0-9a-f]{40}\Z')
EMPTY_PREFIX = hashlib.sha256(b'').hexdigest()
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


class SituationPicture:
    """Caller-owned cursor and metadata. A failed history read requires reset()."""

    def __init__(self, *, roster, ttl_seconds=900, max_events=100000):
        if (not isinstance(roster, dict) or type(ttl_seconds) is not int or not 1 <= ttl_seconds <= 86400
                or type(max_events) is not int or not 1 <= max_events <= 200000):
            raise ValueError('invalid_configuration')
        for agent, identity in roster.items():
            if (not isinstance(agent, str) or not isinstance(identity, dict)
                    or any(not isinstance(identity.get(key), str) or not identity[key] for key in IDENTITY)):
                raise ValueError('invalid_roster')
        self.roster = copy.deepcopy(roster)
        self.ttl_seconds, self.max_events = ttl_seconds, max_events
        self.reset()

    def reset(self):
        self._events, self._cursor, self._prefix = [], None, None
        self._coverage, self._blocked, self._reason = False, False, 'not_read'
        self._position, self._as_of = 0, None

    def _authenticated(self, event):
        agent = event.get('agent')
        identity = self.roster.get(agent) if isinstance(agent, str) else None
        return identity is not None and all(event.get(key) == identity[key] for key in IDENTITY)

    def _failed(self, reason):
        self._coverage, self._blocked, self._reason = False, True, reason

    def update(self, path, *, now, generation_path=None, max_bytes=4 * 1024 * 1024, max_rows=10000):
        instant = _time(now)
        if instant is None or (self._as_of is not None and instant < self._as_of):
            raise ValueError('invalid_as_of')
        self._as_of = instant
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
            if not self._authenticated(event):
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
            staged.append((metadata, event_id(event), self._position))
        self._events.extend(staged)
        self._cursor = result.candidate_prefix.cursor
        self._prefix = result.candidate_prefix.sha256
        self._coverage = self._cursor.offset == result.snapshot_length
        self._reason = 'complete_snapshot' if self._coverage else 'incomplete_snapshot'
        return self._render()

    def _field(self, value=None, record=None, *, reason=None):
        event, reference, _ = record if record is not None else ({}, None, None)
        timestamp = _time(event.get('ts_utc'))
        age = (self._as_of - timestamp).total_seconds() if timestamp is not None else None
        why = reason or (None if self._coverage else self._reason)
        if why is None:
            why = ('unobserved' if record is None or value is None else 'invalid_timestamp' if age is None
                   else 'future_timestamp' if age < 0 else 'stale' if age > self.ttl_seconds else None)
        return {'state': 'UNKNOWN' if why else 'OBSERVED', 'value': None if why else value,
                'source_event_ref': reference, 'event_ts_utc': event.get('ts_utc'),
                'age_seconds': age, 'reason': why}

    def _render(self):
        lanes = {lane: {name: self._field() for name in ('task', 'claim', 'last_bound_reply', 'head')} for lane in LANES}
        requests, duplicate_ids, reply_index = {}, set(), {}
        for record in self._events:
            event, reference, position = record
            lane, kind = event['agent'], event.get('type')
            if lane in lanes:
                if _safe_task(event.get('task_id')):
                    lanes[lane]['task'] = self._field(event['task_id'], record)
                if _head(event):
                    lanes[lane]['head'] = self._field(_head(event), record)
                if kind in ('claim', 'release') and _safe_task(event.get('task_id')):
                    lanes[lane]['claim'] = self._field({'task': event['task_id'],
                        'state': 'claim_event_observed' if kind == 'claim' else 'release_event_observed',
                        'live_lease_verified': False}, record)
            rid = field(event, 'request_id')
            if isinstance(rid, str) and _safe_task(rid) and isinstance(field(event, 'expected_responders'), dict):
                key = (lane, rid)
                if key in requests and requests[key][1] != reference:
                    duplicate_ids.add(key)
                else:
                    requests[key] = record
            reply_id = field(event, 'in_reply_to_request_id')
            status = event.get('status')
            if (kind in ANSWER_TYPES and isinstance(status, str) and status.lower() in ANSWER_STATUSES
                    and isinstance(reply_id, str) and isinstance(_payload(event).get('result'), dict)):
                reply_index.setdefault((lane, reply_id), []).append(record)
        waiting, reviews, newest = [], [], None
        last_replies = {}
        for key, sent in requests.items():
            request, reference, position = sent
            if key in duplicate_ids:
                continue
            for target in field(request, 'expected_responders'):
                if target not in LANES:
                    continue
                answers = []
                for record in reply_index.get((target, key[1]), ()):
                    reply, _, reply_position = record
                    if (reply.get('status') in ACK or not isinstance(_payload(reply).get('result'), dict)):
                        continue
                    if reply_matches_request(request, reply, target, request_position=position, reply_position=reply_position):
                        answers.append(record)
                answered = answers[-1] if answers else None
                if answered is not None and (target not in last_replies or answered[2] > last_replies[target][1][2]):
                    last_replies[target] = (key[1], answered)
                evidence = answered if answered is not None else sent
                observation = self._field(True, evidence)
                item = {'request_ref': reference, 'source_event_ref': evidence[1],
                    'age_seconds': observation['age_seconds'], 'state': observation['state'],
                    'requester': request['agent'] if observation['value'] else None,
                    'responder': target if observation['value'] else None,
                    'reply_ref': answered[1] if answered is not None else None,
                    'result_state': 'BOUND_RESULT_UNVERIFIED' if answered else 'NO_BOUND_RESULT'}
                waiting.append(item)
                pr, head = _payload(request).get('pr'), _head(request)
                if target in RCO and type(pr) is int and pr > 0 and head:
                    # This is an observed review queue, never a merge vote. A
                    # later pass is not a retraction of an earlier veto.
                    vetoes = [answer for answer in answers if _head(answer[0]) == head and
                        str(_payload(answer[0]).get('result', {}).get('status', '')).lower()
                        in ('changes_requested', 'veto', 'rco_veto')]
                    passed = (answered is not None and _head(answered[0]) == head and
                        str(_payload(answered[0]).get('result', {}).get('status', '')).lower()
                        in ('rco_pass', 'pass') and not vetoes)
                    if not passed:
                        reviews.append({**item, 'pr': pr if observation['value'] else None,
                            'head': head if observation['value'] else None,
                            'result_state': 'REPORTED_VETO' if vetoes else item['result_state'],
                            'veto_event_refs': [answer[1] for answer in vetoes]})
                if newest is None or evidence[2] > newest[2]:
                    newest = evidence
        for lane, (request_id, answered) in last_replies.items():
            lanes[lane]['last_bound_reply'] = self._field({'request_id': request_id,
                'status': answered[0]['status'].lower(), 'content_verified': False}, answered)
        operators = [record for record in self._events if record[0]['agent'] == 'operator'
            and record[0].get('type') == 'decision' and _payload(record[0]).get('kind') == 'operator_directive']
        directives = []
        for record in operators[-10:]:
            observed = self._field(True, record)
            directives.append({'source_event_ref': record[1], 'age_seconds': observed['age_seconds'],
                'state': observed['state'], 'task': record[0].get('task_id') if observed['value'] and _safe_task(record[0].get('task_id')) else None})
        capacities = [record for record in self._events if record[0]['agent'] == 'capacity-observer'
                      and record[0].get('type') == 'capacity_observation']
        capacity, routing = self._field(), self._field()
        if capacities:
            record = capacities[-1]
            data = _payload(record[0])
            observed = _time(data.get('observed_at'))
            provider_age = (self._as_of - observed).total_seconds() if observed is not None else None
            used = data.get('used_percent')
            valid = (data.get('pool_identity_state') == 'verified' and data.get('freshness') == 'fresh'
                and data.get('quota_state') == 'observed_headroom' and data.get('provider') in ('codex', 'claude')
                and type(used) is int and 0 <= used <= 100 and provider_age is not None
                and 0 <= provider_age <= self.ttl_seconds)
            if valid:
                capacity = self._field({'provider': data['provider'], 'used_percent': used,
                    'authorization': 'none'}, record)
                routing = self._field('defer_new_cost' if used >= 90 else 'serial_existing_work' if used >= 50
                    else 'continue_existing_work', record)
            else:
                capacity = self._field(record=record, reason='capacity_binding_or_freshness_unknown')
                routing = self._field(record=record, reason='capacity_binding_or_freshness_unknown')
        return {'schema': 'wd.bridge-situation-picture.v1', 'authority': 'none', 'as_of': self._as_of.isoformat(),
            'coverage': 'COMPLETE' if self._coverage else 'UNKNOWN', 'reason': self._reason,
            'cursor': asdict(self._cursor) if self._cursor else None, 'lanes': lanes,
            'waiting_for': self._field(waiting, newest), 'prs_awaiting_rco': self._field(reviews, newest),
            'operator_directives': self._field(directives, operators[-1] if operators else None),
            'capacity': capacity, 'routing_suggestion': routing}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--events', type=Path, required=True)
    parser.add_argument('--roster', type=Path, required=True)
    parser.add_argument('--as-of', required=True)
    parser.add_argument('--generation', type=Path)
    parser.add_argument('--ttl-seconds', type=int, default=900)
    args = parser.parse_args(argv)
    try:
        raw = args.roster.read_bytes()
        if len(raw) > 65536:
            raise ValueError('roster_too_large')
        picture = SituationPicture(roster=json.loads(raw), ttl_seconds=args.ttl_seconds)
        for _ in range(128):
            report = picture.update(args.events, now=args.as_of, generation_path=args.generation)
            if report['coverage'] == 'COMPLETE' or report['reason'] != 'incomplete_snapshot':
                break
        print(json.dumps(report, sort_keys=True, ensure_ascii=True, allow_nan=False))
        return 0 if report['coverage'] == 'COMPLETE' else 2
    except (OSError, ValueError, TypeError):
        print('{"coverage":"UNKNOWN","reason":"invalid_source_or_configuration","authority":"none"}')
        return 2


if __name__ == '__main__':
    raise SystemExit(main())

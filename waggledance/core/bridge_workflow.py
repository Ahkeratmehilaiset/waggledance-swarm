"""Read-only workflow preparation and measured latency; never grants authority."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence
from uuid import uuid4
import re

from waggledance.core.bridge_request_contract import reply_matches_request, timestamp

ACTIVE_WORKERS = frozenset({'codex-lead-1', 'codex-tools-1', 'claude-rco-1', 'claude-rco-2', 'fable-5'})
ON_DEMAND_WORKERS = frozenset({'grok-scout-1'})
STAGES = ('request_durable', 'watcher_seen', 'relay_enqueued', 'model_turn_started', 'answer_durable',
          'lead_processed', 'user_reported')
ROLE_FIELDS = {
    'tools': ('inputs', 'checks', 'handoff'),
    'reviewer': ('evidence', 'acceptance_criteria', 'cases'),
    'planner': ('requirements', 'constraints', 'handoff_target'),
    'advisor': ('question', 'evidence'),
}
ROLE_AGENTS = {'tools': {'codex-tools-1'}, 'reviewer': {'claude-rco-1', 'claude-rco-2'},
               'planner': {'fable-5'}, 'advisor': {'grok-scout-1'}}


def worker_class(agent: str) -> str:
    return 'active' if agent in ACTIVE_WORKERS else 'on_demand' if agent in ON_DEMAND_WORKERS else 'historical'


def prepare_request(plan: Mapping[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    """Prepare only. The caller must verify the referenced operator authorization."""
    role, target = plan.get('role'), plan.get('target')
    if role not in ROLE_FIELDS or target not in ROLE_AGENTS[role]:
        raise ValueError('role and target must match the fleet roster')
    for name in ('authorization_ref', 'task_id', 'revision', 'instruction'):
        if not isinstance(plan.get(name), str) or not plan[name].strip():
            raise ValueError(f'{name} is required')
    if role == 'advisor':
        raise ValueError('Grok must use the existing hourly-budgeted helper, not the bridge queue')
    requester, responder = plan.get('requester'), plan.get('responder')
    for identity in (requester, responder):
        if not isinstance(identity, Mapping) or any(not isinstance(identity.get(k), str) or not identity[k]
                                                   for k in ('agent', 'agent_uuid', 'session_id', 'run_id')):
            raise ValueError('verified requester and responder identities are required')
    if requester['agent'] != 'codex-lead-1' or responder['agent'] != target:
        raise ValueError('workflow preparation requires Lead and the exact target identity')
    data = plan.get('data', {})
    consumes = plan.get('consumes_fields')
    if not isinstance(data, Mapping) or not isinstance(consumes, list) or not consumes:
        raise ValueError('data and explicit consumes_fields are required')
    if len(set(consumes)) != len(consumes) or any(k not in ROLE_FIELDS[role] or k not in data for k in consumes):
        raise ValueError('consumes_fields contains missing, duplicate or other-role fields')
    if not isinstance(plan.get('result_fields'), list) or not plan['result_fields'] or any(
        not isinstance(k, str) or re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,63}', k) is None
        for k in plan['result_fields']
    ) or len(plan['result_fields']) > 256 or len(set(plan['result_fields'])) != len(plan['result_fields']):
        raise ValueError('explicit result_fields are required')
    return dict(
        ts_utc=(now or datetime.now(timezone.utc)).isoformat(), **dict(requester),
        to=target, type='wake_request', status='request', task_id=plan['task_id'],
        request_id=str(uuid4()), message=plan['instruction'],
        expected_responders={target: dict(responder)},
        payload={'schema': 'wd.role-request.v1', 'role': role, 'task_revision': plan['revision'],
                 'authorization_ref': plan['authorization_ref'], 'authority_effect': 'none',
                 'consumes_fields': list(consumes), 'result_fields': list(plan['result_fields']),
                 'result_contract': {'schema': 'wd.task-result-contract.v1',
                                     'required': list(plan['result_fields']),
                                     'additional_properties': False},
                 'inputs': {k: deepcopy(data[k]) for k in consumes}},
    )


def prepare_handoff(request: Mapping[str, Any], reply: Mapping[str, Any],
                    next_plan: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the exact upstream result before preparing the already-planned next step."""
    if reply.get('type') != 'message' or reply.get('status') != 'answered':
        raise ValueError('handoff requires a substantive answered result, never an ACK')
    if not reply_matches_request(request, reply, str(request.get('to', ''))):
        raise ValueError('handoff does not bind to the current request and session')
    required = request.get('payload', {}).get('result_fields', [])
    result = reply.get('payload', {}).get('result')
    if not required or not isinstance(result, Mapping) or set(result) != set(required):
        raise ValueError('handoff result does not match the requested result fields')
    auth = request.get('payload', {}).get('authorization_ref')
    if not auth or next_plan.get('authorization_ref') != auth:
        raise ValueError('handoff must retain the existing authorization reference')
    plan = deepcopy(dict(next_plan))
    if 'handoff' not in plan.get('consumes_fields', []):
        raise ValueError('next plan must explicitly consume the handoff')
    plan.setdefault('data', {})['handoff'] = {
        'in_reply_to_request_id': request.get('request_id'), 'result': dict(result),
        'author': reply.get('agent'), 'session_id': reply.get('session_id'),
    }
    return prepare_request(plan)


def latency_report(request: Mapping[str, Any], observations: Sequence[Mapping[str, Any]], *, target: str) -> dict[str, Any]:
    """Observations are local measurements, not completion or authenticity evidence."""
    rid = request.get('request_id')
    matching = [o for o in observations if o.get('request_id') == rid and o.get('target') == target
                and o.get('requester') == request.get('agent')
                and o.get('requester_session_id') == request.get('session_id')]
    deliveries = {o.get('delivery_id') for o in matching if o.get('stage') == 'model_turn_started' and o.get('delivery_id')}
    matching += [o for o in observations if o.get('stage') == 'relay_enqueued'
                 and o.get('delivery_id') in deliveries and o.get('target') == target]
    times = {}
    for stage in STAGES:
        values = [timestamp(o.get('observed_at_utc')) for o in matching if o.get('stage') == stage]
        times[stage] = min((v for v in values if v is not None), default=None)
    intervals = {}
    invalid = []
    for start, end in zip(STAGES, STAGES[1:]):
        a, b = times[start], times[end]
        duration = (b-a).total_seconds() if a and b else None
        if duration is not None and duration < 0:
            invalid.append(f'{start}->{end}')
            duration = None
        intervals[f'{start}->{end}'] = duration
    return {'schema': 'wd.request-latency.v1', 'request_id': rid, 'target': target,
            'stages': {k: v.isoformat() if v else None for k, v in times.items()},
            'seconds': intervals, 'unknown_stages': [k for k, v in times.items() if v is None],
            'invalid_order': invalid, 'task_completion_verified': False,
            'engine_turn_started_at_utc': None,
            'stage_meanings': {'model_turn_started': 'agent registered request processing; not model engine start',
                               'lead_processed': 'Lead reports inspecting the exact-bound answer',
                               'user_reported': 'Lead reports publishing a referenced summary; operator receipt is not verified'},
            'timing_basis': 'local observations; model_turn_started is the first agent-reported marker, not engine timing',
            'authority_effect': 'none'}

"""Control events must wake even when a sender marks them informational.

Ported from PR1754 / 87ad031a36054b373f7a1975ee089cebe84da357.
PR1755's stricter explicit notice/informational allowlist supersedes the old
generic non-control suppression: ambiguous progress/findings still wake.

`payload.notification=informational` is a sender hint, not a validated
classification. Before this fix `Test-BridgeWakeEligible` let the hint
suppress any unbound event, so a decision/finding/blocked veto, a HOLD or a
cancel/supersede/withdraw of a request with no request_id never reached the
agent-inbox Monitor or the Watch-Bridge wake file. The hint may now only
suppress an explicitly allowlisted non-actionable notice. Wake eligibility
is routing only: it grants
no authority and binds nothing.
"""
import json
import os
import subprocess
from pathlib import Path

import pytest

from test_wd_reboot_bundle import LANE_TEST_SHELLS, REBOOT, _run_powershell
from test_wd_startup_recovery import q

BIN = REBOOT.parents[2] / '.agent-bridge/bin'
INFO = {'notification': 'informational'}

# (id, type, status, extra fields) -> must wake although marked informational.
MUST_WAKE = [
    ('decision_changes_requested', 'decision', 'changes_requested', {}),
    ('decision_hold', 'decision', 'hold', {}),
    ('decision_veto', 'decision', 'veto', {}),
    ('decision_approval', 'decision', 'build_consensus_pass', {}),
    ('finding_any_status', 'finding', 'review_findings', {}),
    ('blocked_custom_status', 'blocked', 'merge_blocked_operator_or_driver', {}),
    ('rco_review', 'rco_review', 'changes_requested_minor', {}),
    ('message_cancelled', 'message', 'cancelled', {}),
    ('message_superseded', 'message', 'superseded', {}),
    ('message_withdrawn_prefix', 'message', 'withdrawn_by_requester', {}),
    ('message_hold_token', 'message', 'merge_hold', {}),
    ('message_veto_token', 'message', 'veto_posted', {}),
    ('message_changes_requested_variant', 'message', 'changes_requested_minor', {}),
    ('message_rco_fail', 'message', 'rco_fail', {}),
    ('message_review_failed', 'message', 'review_failed', {}),
    ('message_failure_suffix', 'message', 'ci_failure_on_head', {}),
    ('message_mixed_case', 'message', 'Review_FAILED', {}),
    ('message_hyphen_separator', 'message', 'merge-hold', {}),
    ('status_block_token', 'status', 'merge_blocked_on_ci', {}),
    ('intent_stop_token', 'intent', 'stop_work', {}),
    ('wake_request_unbound', 'wake_request', 'implementation_requested', {}),
    ('done_closure', 'done', 'done', {}),
    ('unknown_type', 'ownership_transfer', 'noted', {}),
    # Already true before the fix; pinned so the precedence cannot regress.
    ('bound_reply', 'message', 'answered', {'in_reply_to_request_id': 'r-1'}),
    ('new_request', 'message', 'notice', {'request_id': 'r-2'}),
    ('message_review_findings', 'message', 'review_findings', {}),
    ('message_evidence_update', 'message', 'evidence_update', {}),
    ('status_progress', 'status', 'progress', {}),
    ('intent_planning', 'intent', 'planning', {}),
]
# Demonstrably non-actionable notices and ACK/liveness noise stay suppressed.
MUST_NOT_WAKE = [
    ('message_notice', 'message', 'notice', INFO),
    ('ack_received', 'message', 'received', {}),
    ('ack_bound', 'message', 'acknowledged', {'in_reply_to_request_id': 'r-3'}),
    ('heartbeat', 'heartbeat', 'alive', {}),
    ('liveness', 'liveness', 'alive', {}),
]


def _event(event_type, status, extra, *, informational=True, to='codex-lead-1'):
    event = dict(ts_utc='2026-09-28T12:00:00Z', agent='fable-5', to=to, type=event_type,
                 status=status, task_id='wake-precedence-task', message='')
    if informational:
        event['payload'] = dict(INFO)
    for key, value in extra.items():
        if key == 'notification':
            event['payload'] = {key: value}
        else:
            event[key] = value
    return event


def _eligible(ps, events):
    script = f"""
. {q(BIN / 'BridgeEventClassifier.ps1')}
$rows = ConvertFrom-Json {q(json.dumps(events))}
$out = foreach ($row in $rows) {{ [bool](Test-BridgeWakeEligible $row) }}
ConvertTo-Json -Compress @($out)
"""
    verdicts = json.loads(_run_powershell(script, executable=ps).stdout)
    assert len(verdicts) == len(events)
    return verdicts


@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
def test_informational_hint_never_suppresses_control_or_bound_events(ps):
    events = [_event(t, s, x) for _, t, s, x in MUST_WAKE]
    verdicts = dict(zip([c[0] for c in MUST_WAKE], _eligible(ps, events)))
    assert verdicts == {c[0]: True for c in MUST_WAKE}


@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
def test_non_actionable_notices_and_ack_noise_stay_suppressed(ps):
    events = [_event(t, s, x, informational=False) for _, t, s, x in MUST_NOT_WAKE]
    verdicts = dict(zip([c[0] for c in MUST_NOT_WAKE], _eligible(ps, events)))
    assert verdicts == {c[0]: False for c in MUST_NOT_WAKE}


@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
def test_control_events_without_the_hint_still_wake(ps):
    events = [_event(t, s, x, informational=False) for _, t, s, x in MUST_WAKE]
    assert _eligible(ps, events) == [True] * len(MUST_WAKE)


def _write_log(root, events):
    (root / 'shared').mkdir(parents=True)
    (root / 'shared/events.jsonl').write_text(
        ''.join(json.dumps(e) + '\n' for e in events), encoding='utf-8')


def _scrubbed_env(root):
    # PSModulePath from a pwsh 7 parent breaks a Windows PowerShell 5.1 child.
    env = {k: v for k, v in os.environ.items()
           if not k.upper().startswith(('AGENT_BRIDGE_', 'CLAUDE_CODE_', 'WD_'))
           and k.upper() != 'PSMODULEPATH'}
    env['AGENT_BRIDGE_RUNTIME_ROOT'] = str(root)
    return env


@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize('case', ['veto', 'cancel', 'notice'])
def test_watch_bridge_wake_file_follows_control_precedence(tmp_path, ps, case):
    kind = {'veto': ('decision', 'veto'), 'cancel': ('message', 'cancelled'),
            'notice': ('message', 'notice')}[case]
    _write_log(tmp_path, [_event(*kind, {})])
    env = dict(_scrubbed_env(tmp_path), WAGGLE_BRIDGE_WAKE_ENABLED='1')
    result = subprocess.run([ps, '-NoProfile', '-File', str(BIN / 'Watch-Bridge.ps1'),
                             '-Agent', 'codex-lead-1', '-RuntimeRoot', str(tmp_path),
                             '-StartLineCount', '0', '-MaxIterations', '1',
                             '-PollIntervalMs', '1', '-DebounceMs', '1'],
                            capture_output=True, text=True, timeout=60, env=env)
    assert result.returncode == 0, result.stderr
    assert (tmp_path / 'wake_codex-lead-1').exists() is (case != 'notice')


@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
def test_agent_inbox_monitor_delivers_informational_controls_only(tmp_path, ps):
    events = [
        _event('decision', 'veto', {}),
        _event('message', 'cancelled', {}),
        _event('message', 'notice', {}),
        _event('message', 'received', {}, informational=False),
        _event('heartbeat', 'alive', {}, informational=False),
        _event('decision', 'veto', {}, to='someone-else'),
    ]
    _write_log(tmp_path, events)
    result = subprocess.run([ps, '-NoProfile', '-File', str(BIN / 'Monitor-AgentBridge.ps1'),
                             '-Agent', 'codex-lead-1', '-RuntimeRoot', str(tmp_path),
                             '-TargetedOnly', '-IncludeWakeRequests', '-Json', '-ReplayExisting',
                             '-MaxIterations', '1', '-PollIntervalMs', '1',
                             '-StatePath', str(tmp_path / 'cursor.json')],
                            capture_output=True, text=True, timeout=60, env=_scrubbed_env(tmp_path))
    assert result.returncode == 0, result.stderr
    emitted = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
    assert [(e['type'], e['status'], e['to']) for e in emitted] == [
        ('decision', 'veto', 'codex-lead-1'),
        ('message', 'cancelled', 'codex-lead-1'),
    ]

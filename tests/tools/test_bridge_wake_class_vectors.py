"""Golden vectors for the wd.wake-class.v1 wake classifier contract.

The pure Python reference (tools/bridge_wake_class.py) must match every
vector. Each vector also records what the deployed PowerShell consumers do
today (Watch-Bridge Test-IsTargeted and the agent-inbox Monitor filter, whose
function bodies are extracted verbatim from the scripts). Where the safe
contract and the legacy boolean disagree, the vector declares the difference
explicitly; the contract is never weakened to force parity. Every guard in the
reference is mutated and at least one vector must catch each mutant.
Wake eligibility is routing only: it grants no authority and binds nothing.
"""
import ast
import importlib.util
import itertools
import json
import re
from pathlib import Path

import pytest

from test_wd_reboot_bundle import LANE_TEST_SHELLS, REBOOT, _run_powershell
from test_wd_startup_recovery import q

ROOT = REBOOT.parents[2]
BIN = ROOT / '.agent-bridge/bin'
MODULE_PATH = ROOT / 'tools/bridge_wake_class.py'
VECTORS_PATH = ROOT / 'tests/fixtures/wake_class/v1/vectors.json'
DOC_PATH = ROOT / 'docs/architecture/BRIDGE_WAKE_CLASS_CONTRACT_V1.md'
CONSUMERS = ('watch', 'monitor')
LEGACY_VALUES = (True, False, 'error', 'parse_error')


def _load(source=None, name='bridge_wake_class_under_test'):
    spec = importlib.util.spec_from_loader(name, loader=None)
    module = importlib.util.module_from_spec(spec)
    code = MODULE_PATH.read_text(encoding='utf-8') if source is None else source
    exec(compile(code, str(MODULE_PATH), 'exec'), module.__dict__)
    return module


wc = _load()
DATA = json.loads(VECTORS_PATH.read_text(encoding='utf-8'))
VECTORS = DATA['vectors']


def _outcome(module, vector):
    try:
        result = module.classify(vector['event'], vector['target_agent'])
    except Exception as exc:  # a crashing mutant must count as caught
        return {'error': type(exc).__name__}
    return {k: result[k] for k in ('class', 'reason', 'wakes', 'control_signal')}


def legacy_projection(ps, vectors, tmp_path):
    """Run the deployed consumer filters for each vector in one PowerShell."""
    items = [{'id': v['id'], 'target': v['target_agent'],
              'json': json.dumps(v['event'], ensure_ascii=True)} for v in vectors]
    source = tmp_path / 'legacy_items.json'
    source.write_text(json.dumps(items, ensure_ascii=True), encoding='ascii')
    script = f"""
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
. {q(BIN / 'BridgeEventClassifier.ps1')}
$IncludeWakeRequests = $true
$agentInbox = $true
$parseTokens = $null
$parseErrors = $null
$specs = @(
    @{{ Path = {q(BIN / 'Watch-Bridge.ps1')}; Names = @('Test-IsTargeted') }},
    @{{ Path = {q(BIN / 'Monitor-AgentBridge.ps1')};
        Names = @('Get-EventTargetsLocal', 'Test-SubstantiveMonitorEvent') }}
)
foreach ($spec in $specs) {{
    $ast = [System.Management.Automation.Language.Parser]::ParseFile(
        $spec.Path, [ref]$parseTokens, [ref]$parseErrors)
    foreach ($name in $spec.Names) {{
        $defs = @($ast.FindAll({{ param($n)
            $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
            $n.Name -ceq $name }}, $true))
        if ($defs.Count -ne 1) {{ throw "expected exactly one function $name" }}
        . ([scriptblock]::Create($defs[0].Extent.Text))
    }}
}}
$items = [System.IO.File]::ReadAllText({q(source)}, [System.Text.Encoding]::UTF8) |
    ConvertFrom-Json
$out = foreach ($item in $items) {{
    $row = [ordered]@{{ id = $item.id; watch = $null; monitor = $null }}
    $parsed = $true
    $ev = $null
    try {{ $ev = ConvertFrom-Json $item.json }} catch {{ $parsed = $false }}
    if (-not $parsed) {{
        $row.watch = 'parse_error'
        $row.monitor = 'parse_error'
    }} else {{
        try {{ $row.watch = [bool](Test-IsTargeted -Event $ev -WatchedAgent $item.target) }}
        catch {{ $row.watch = 'error' }}
        try {{
            $row.monitor = [bool](Test-SubstantiveMonitorEvent -Event $ev `
                -LocalAgent $item.target -OnlyTargeted $true)
        }} catch {{ $row.monitor = 'error' }}
    }}
    [pscustomobject]$row
}}
ConvertTo-Json -Compress -Depth 4 @($out)
"""
    rows = json.loads(_run_powershell(script, executable=ps).stdout)
    assert [r['id'] for r in rows] == [v['id'] for v in vectors]
    return {r['id']: {c: r[c] for c in CONSUMERS} for r in rows}


def test_vector_file_shape_and_declared_differences():
    assert DATA['contract'] == wc.CONTRACT == 'wd.wake-class.v1'
    ids = [v['id'] for v in VECTORS]
    assert len(ids) == len(set(ids))
    for v in VECTORS:
        assert re.fullmatch(r'[a-z0-9_]+', v['id']), v['id']
        assert set(v) <= {'id', 'target_agent', 'event', 'expected', 'legacy',
                          'legacy_difference', 'note'}, v['id']
        exp = v['expected']
        assert set(exp) == {'class', 'reason', 'wakes', 'control_signal'}, v['id']
        assert exp['class'] in wc.CLASSES and exp['reason'] in wc.REASONS, v['id']
        assert exp['wakes'] is (exp['class'] not in wc.NON_WAKING_CLASSES), v['id']
        assert set(v['legacy']) == set(CONSUMERS), v['id']
        assert all(v['legacy'][c] in LEGACY_VALUES for c in CONSUMERS), v['id']
        # Mechanical difference statement: exactly the consumers whose legacy
        # outcome is not the contract's wake decision, with both values.
        want = {c: {'legacy': v['legacy'][c], 'contract': exp['wakes']}
                for c in CONSUMERS if v['legacy'][c] is not exp['wakes']}
        assert v.get('legacy_difference', {}) == want, v['id']
        if want:
            assert v.get('note'), f"{v['id']}: a legacy difference needs a note"
            # v1 only ever wakes where legacy dropped; it never suppresses a
            # row that a deployed consumer wakes on today.
            assert exp['wakes'] is True, v['id']


@pytest.mark.parametrize('vector', VECTORS, ids=lambda v: v['id'])
def test_reference_matches_vector(vector):
    assert _outcome(wc, vector) == vector['expected']


def test_every_class_and_reason_is_exercised():
    assert {v['expected']['class'] for v in VECTORS} == set(wc.CLASSES)
    assert {v['expected']['reason'] for v in VECTORS} == set(wc.REASONS)


@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
def test_legacy_projection_matches_recorded_values(ps, tmp_path):
    measured = legacy_projection(ps, VECTORS, tmp_path)
    drift = {v['id']: {'recorded': v['legacy'], 'measured': measured[v['id']]}
             for v in VECTORS if measured[v['id']] != v['legacy']}
    assert drift == {}


def test_no_legacy_control_token_or_closure_status_is_weakened():
    source = (BIN / 'BridgeEventClassifier.ps1').read_text(encoding='utf-8')
    block = re.search(r"\$controlTokens=@\((.*?)\)", source, re.S).group(1)
    tokens = re.findall(r"'([a-z_]+)'", block)
    assert len(tokens) >= 30
    closure = re.search(r"'done','closed','superseded'.*?\)", source, re.S).group(0)
    closure_statuses = re.findall(r"'([a-z_]+)'", closure)
    for status in tokens + ['changes_requested'] + closure_statuses:
        if status in ('done', 'merged', 'completed', 'approved', 'abandoned'):
            continue  # closures by type (done/decision/release are control types)
        assert wc.has_control_token(status), status
    for event_type in ('decision', 'finding', 'blocked', 'rco_review', 'done',
                       'release', 'wake_request'):
        assert event_type in wc.CONTROL_TYPES


def _combinations():
    types = ['message', 'status', 'intent', 'decision', 'finding', 'heartbeat',
             'liveness', 'wake_request', 'Message', 'custom_kind', None, 7]
    statuses = ['notice', 'progress', 'received', 'acknowledged', 'veto', 'merge_hold',
                'Review_FAILED', 'unblocked', 'answered', 'alive', '', None,
                'vеto', 'x' * 300, ['veto'], 'review_requested', 'mergeHold']
    payloads = [None, {'notification': 'informational'},
                {'notification': 'Informational'}, {'Notification': 'informational'},
                {'notification': ['informational']}, 'informational',
                {'notification': 'informational', 'request_id': 'r-9'},
                {'notification': 'informational', 'result': {'status': 'veto'}},
                {'request_ts_utc': 'utc:x', 'request_agent': 'a'}]
    ids = [{}, {'request_id': 'r-1'}, {'in_reply_to_request_id': 'r-2'},
           {'request_id': 7}, {'in_reply_to_request_id': ' '}, {'request_id': ''}]
    targets = ['fable-5', 'FABLE-5', 'fable-5;x', ['fable-5'], 'other', None]
    agents = ['codex-lead-1', 'fable-5', 'Fable-5', '', None]
    for t, s, p, extra, to, agent in itertools.product(
            types, statuses, payloads, ids, targets, agents):
        event = {'type': t, 'status': s, 'task_id': 'x', **extra}
        if p is not None:
            event['payload'] = p
        if to is not None:
            event['to'] = to
        if agent is not None:
            event['agent'] = agent
        yield event


# Oracles owned by this test, independent of the classifier's own predicates.
BENIGN_ORACLE = {'informational', 'info', 'notice', 'evidence', 'evidence_update',
                 'progress', 'progress_summary', 'in_progress', 'planning'}
ACK_PAYLOAD_ORACLE = {'request_ts_utc', 'request_agent', 'request_type',
                      'request_status', 'notification'}
LIVENESS_PAYLOAD_ORACLE = {'head', 'notification'}


def _payload_keys(event):
    payload = event.get('payload')
    return set(payload) if isinstance(payload, dict) else set()


def test_non_waking_exits_are_exactly_the_three_narrow_shapes():
    seen = 0
    for event in _combinations():
        result = wc.classify(event, 'fable-5')
        seen += 1
        assert result['contract'] == wc.CONTRACT
        assert result['wakes'] is (result['class'] not in wc.NON_WAKING_CLASSES)
        if result['class'] == 'not_addressed':
            assert result['reason'] in ('self_emission', 'no_target', 'not_targeted')
            continue
        if result['wakes']:
            continue
        assert result['control_signal'] is False, event
        status, etype = event.get('status'), event.get('type')
        assert event.get('request_id') in (None, ''), event
        if result['class'] == 'notice':
            assert etype in ('message', 'status', 'intent'), event
            assert status in BENIGN_ORACLE, event
            assert event['payload'].get('notification') == 'informational', event
            assert not _payload_keys(event) & {'request_id', 'in_reply_to_request_id',
                                                'result', 'result_contract'}, event
            assert event.get('in_reply_to_request_id') in (None, ''), event
        elif result['reason'] == 'ack':
            assert etype == 'message' and status in ('received', 'seen', 'acknowledged'), event
            assert _payload_keys(event) <= ACK_PAYLOAD_ORACLE, event
        else:
            assert result['reason'] == 'liveness', event
            assert etype in ('heartbeat', 'liveness'), event
            assert _payload_keys(event) <= LIVENESS_PAYLOAD_ORACLE, event
    assert seen > 100000


def test_benign_allowlist_is_closed_and_pinned():
    assert set(wc.BENIGN_NOTICE_STATUSES) == BENIGN_ORACLE
    assert set(wc.ACK_PAYLOAD_KEYS) == ACK_PAYLOAD_ORACLE
    assert set(wc.LIVENESS_PAYLOAD_KEYS) == LIVENESS_PAYLOAD_ORACLE
    # No benign status may contain a control root (otherwise the allowlist
    # and control recognition would disagree about it).
    assert [s for s in BENIGN_ORACLE if wc.has_control_token(s)] == []


# Independently derived vocabulary (RCO1 review f8fc8be4 W1/W2, Lead's measured
# hinted statuses, result/promotion words) plus unknown future statuses. The
# oracle is only "must wake": it does not consult has_control_token.
MUST_WAKE_UNDER_HINT = [
    'mergeHold', 'MergeBlocked', 'rcoVeto', 'changesRequested', 'ChangesRequested',
    'onhold', 'mergehold_active', 'prehold', 'autofail', 'nogo', 'no_go', 'noGo',
    'paused', 'suspended', 'kill', 'killed', 'kill_switch', 'incident', 'emergency',
    'escalated', 'error', 'timeout', 'expired', 'wedged', 'unsafe', 'invalid',
    'conflict', 'regression', 'broken', 'critical', 'do_not_merge', 'not_approved',
    'disapproved', 'nogo_merge', 'wait', 'embargo', 'lockdown', 'locked',
    'requests_superseded', 'rollout_stopped', 'test_failure', 'handoff_published',
    'review_note', 'review_findings', 'verification_passed', 'ci_green', 'approved',
    'verified', 'answered', 'completed', 'completed_rollout', 'merged', 'published',
    'ready_for_operator_signature', 'signed_merge_window', 'request', 'review_requested',
    'open', 'proposal', 'fix-pushed', 'ready', 'Info', 'INFO', 'Progress', 'info ',
    ' progress', 'progress\n', 'zq_future_status_42', 'recalibrate_quota', 'x', '0',
]


@pytest.mark.parametrize('event_type', ['message', 'status', 'intent'])
def test_independent_vocabulary_always_wakes_under_the_hint(event_type):
    silenced = []
    for status in MUST_WAKE_UNDER_HINT:
        event = {'agent': 'codex-lead-1', 'to': 'fable-5', 'type': event_type,
                 'status': status, 'task_id': 'x',
                 'payload': {'notification': 'informational'}}
        if not wc.classify(event, 'fable-5')['wakes']:
            silenced.append(status)
    assert silenced == []


@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
def test_contract_never_silences_a_row_legacy_consumers_wake(ps, tmp_path):
    corpus = [{'id': f'c{i}', 'target_agent': 'fable-5', 'event': event}
              for i, event in enumerate(_combinations()) if i % 97 == 0]
    corpus += [{'id': f'h{i}', 'target_agent': 'fable-5',
                'event': {'agent': 'codex-lead-1', 'to': 'fable-5', 'type': t,
                          'status': s, 'task_id': 'x',
                          'payload': {'notification': 'informational'}}}
               for i, (t, s) in enumerate(itertools.product(
                   ['message', 'status', 'intent'], MUST_WAKE_UNDER_HINT + sorted(BENIGN_ORACLE)))]
    measured = legacy_projection(ps, corpus, tmp_path)
    silenced = [c['id'] for c in corpus
                if True in measured[c['id']].values()
                and not wc.classify(c['event'], 'fable-5')['wakes']]
    assert len(corpus) > 1500
    assert silenced == []


@pytest.mark.parametrize('bad', ['', 'FABLE-5', ' fable-5', None, 5, 'a' * 200])
def test_target_agent_must_be_a_canonical_agent_name(bad):
    with pytest.raises(ValueError):
        wc.classify({'to': 'fable-5'}, bad)


def test_reference_is_pure_stdlib_without_io():
    tree = ast.parse(MODULE_PATH.read_text(encoding='utf-8'))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name.split('.')[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module.split('.')[0])
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id not in ('open', 'print', 'exec', 'eval', 'input')
    assert imported <= {'__future__', 'json', 're', 'typing'}


def test_contract_doc_names_every_class_reason_and_the_contract_id():
    doc = DOC_PATH.read_text(encoding='utf-8')
    assert wc.CONTRACT in doc
    for code in wc.CLASSES + wc.REASONS:
        assert f'`{code}`' in doc, code
    for root in wc.CONTROL_ROOTS:
        assert f'`{root}`' in doc, root
    for status in wc.BENIGN_NOTICE_STATUSES:
        assert f'`{status}`' in doc, status


# (name, exact source fragment, replacement): each removes or loosens a guard.
MUTANTS = [
    ('self_ignores_key_variant', 'if agent == target_agent and not agent_variant:',
     'if agent == target_agent:'),
    ('self_case_folded', 'if agent == target_agent and not agent_variant:',
     'if isinstance(agent, str) and _ascii_lower(agent) == target_agent:'),
    ('to_ignores_key_variant', 'exact = (not to_variant and isinstance(to, str) and',
     'exact = (isinstance(to, str) and'),
    ('to_entries_not_trimmed',
     "target_agent in [t.strip() for t in to.split(',') if t.strip()])",
     "target_agent in to.split(','))"),
    ('to_empty_values_kept', "and v is not None and v != '' and v != {} and v != []]", ']'),
    ('loose_mention_dropped',
     'if any(_mentions(v, target_agent) for v in address_like):', 'if False:'),
    ('case_variant_keys_ignored',
     'if any(_field(event, key)[1] for key in ENVELOPE_KEYS):', 'if False:'),
    ('missing_sender_ignored',
     'if not isinstance(agent, str) or not agent.strip():', 'if agent is _MISSING:'),
    ('sender_case_variant_ignored',
     'if _ascii_lower(agent.strip()) == target_agent:', 'if False:'),
    ('malformed_ids_ignored', "if 'malformed' in (rid, irr):", 'if False:'),
    ('id_shape_unchecked', 'if isinstance(value, str) and _ID_RE.match(value):',
     'if isinstance(value, str) or value:'),
    ('empty_id_is_malformed', "if value is _MISSING or value is None or value == '':",
     'if value is _MISSING or value is None:'),
    ('empty_type_status_allowed',
     'if not (isinstance(etype, str) and etype and isinstance(status, str) and status):',
     'if not (isinstance(etype, str) and isinstance(status, str)):'),
    ('non_ascii_allowed', 'if not (etype.isascii() and status.isascii()):', 'if False:'),
    ('oversize_allowed',
     'if len(etype) > MAX_FIELD_CHARS or len(status) > MAX_FIELD_CHARS:', 'if False:'),
    ('liveness_conflict_ignored',
     "if control_status or rid != 'absent' or irr != 'absent':", 'if False:'),
    ('liveness_ids_ignored',
     "if control_status or rid != 'absent' or irr != 'absent':", 'if control_status:'),
    ('ack_on_any_notice_type', "if etype != ACK_TYPE or rid != 'absent':",
     "if etype not in NOTICE_TYPES or rid != 'absent':"),
    ('ack_with_request_id', "if etype != ACK_TYPE or rid != 'absent':",
     'if etype != ACK_TYPE:'),
    ('ack_payload_unchecked', 'if not _noise_payload_ok(event, ACK_PAYLOAD_KEYS):',
     'if False:'),
    ('liveness_payload_unchecked',
     'if not _noise_payload_ok(event, LIVENESS_PAYLOAD_KEYS):', 'if False:'),
    ('noise_payload_values_unchecked',
     'if key not in allowed or not isinstance(value, str):', 'if key not in allowed:'),
    ('noise_payload_hint_value_unchecked',
     "return payload.get('notification', INFORMATIONAL) == INFORMATIONAL", 'return True'),
    ('noise_payload_type_unchecked',
     "    if not isinstance(payload, dict):\n        return False\n",
     "    if not isinstance(payload, dict):\n        return True\n"),
    ('expected_responders_ignored',
     "ADDRESS_KEYS = frozenset({'to', 'expected_responders'})",
     "ADDRESS_KEYS = frozenset({'to'})"),
    ('request_and_reply_conflict_ignored',
     "if irr == 'valid' and rid == 'valid':", 'if False:'),
    ('control_type_unrecognized', 'if etype in CONTROL_TYPES:', 'if False:'),
    ('unknown_type_suppressible', 'if etype not in NOTICE_TYPES:', 'if False:'),
    ('control_status_suppressible',
     "if control_status:\n        return _result('control', 'control_status', ctl)",
     "if False:\n        return _result('control', 'control_status', ctl)"),
    ('roots_matched_as_prefix', 'return any(root in joined for root in CONTROL_ROOTS)',
     'return any(joined.startswith(root) for root in CONTROL_ROOTS)'),
    ('roots_matched_per_token',
     "joined = _NON_ALNUM.sub('', _ascii_lower(status))",
     "joined = ' '.join(_NON_ALNUM.split(_ascii_lower(status)))\n"
     "    return any(t.startswith(r) for t in joined.split() for r in CONTROL_ROOTS)"),
    ('allowlist_dropped',
     "if status not in BENIGN_NOTICE_STATUSES:\n"
     "        return _result('ambiguous', 'unlisted_status', ctl)",
     "if False:\n        return _result('ambiguous', 'unlisted_status', ctl)"),
    ('allowlist_case_insensitive',
     "if status not in BENIGN_NOTICE_STATUSES:\n"
     "        return _result('ambiguous', 'unlisted_status', ctl)",
     "if _ascii_lower(status) not in BENIGN_NOTICE_STATUSES:\n"
     "        return _result('ambiguous', 'unlisted_status', ctl)"),
    ('payload_result_keys_dropped',
     "PAYLOAD_BINDING_KEYS = frozenset({'request_id', 'in_reply_to_request_id',\n"
     "                                  'result', 'result_contract'})",
     "PAYLOAD_BINDING_KEYS = frozenset({'request_id', 'in_reply_to_request_id'})"),
    ('payload_missing_unchecked',
     "if payload is _MISSING or payload is None:\n"
     "        return _result('ambiguous', 'unhinted_notice', ctl)",
     "if payload is None:\n        return _result('ambiguous', 'unhinted_notice', ctl)"),
    ('malformed_payload_suppressed',
     "return _result('ambiguous', 'malformed_payload', ctl)",
     "return _result('notice', 'informational_hint')"),
    ('payload_binding_ignored',
     'if any(isinstance(k, str) and _ascii_lower(k) in PAYLOAD_BINDING_KEYS and',
     'if False and any(isinstance(k, str) and _ascii_lower(k) in PAYLOAD_BINDING_KEYS and'),
    ('notification_key_variant_ignored',
     'if notification_variant or (notification is not _MISSING and',
     'if (notification is not _MISSING and'),
    ('notification_value_truthy', 'notification != INFORMATIONAL):', 'not notification):'),
    ('notice_wakes_flag', "'wakes': cls not in NON_WAKING_CLASSES",
     "'wakes': cls != 'noise'"),
    ('control_signal_ignores_type',
     "return ((isinstance(etype, str) and etype in CONTROL_TYPES) or",
     'return (False or'),
]


@pytest.mark.parametrize('name,old,new', MUTANTS, ids=[m[0] for m in MUTANTS])
def test_each_guard_mutant_is_caught_by_a_vector(name, old, new):
    source = MODULE_PATH.read_text(encoding='utf-8')
    assert source.count(old) == 1, f'{name}: fragment must occur exactly once'
    mutant = _load(source.replace(old, new), name=f'mutant_{name}')
    caught = [v['id'] for v in VECTORS if _outcome(mutant, v) != v['expected']]
    assert caught, f'mutant {name} survived every vector'

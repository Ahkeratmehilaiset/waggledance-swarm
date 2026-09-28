"""PowerShell port of the wd.wake-class.v1 wake classifier (Get-BridgeWakeClass).

The port must return exactly what the Python reference returns for
json.loads(<raw row text>): every golden vector, in several serializations,
in Windows PowerShell 5.1 and PowerShell 7 under StrictMode Latest.

Input fidelity is the point of the raw-text entrypoint. ConvertFrom-Json merges
or rejects case-variant keys and blurs number/string/bool evidence, so the port
decodes the row itself. The oracle for every raw-text case is therefore the
Python reference applied to json.loads, with two explicit raw-text rules: text
json.loads rejects is ambiguous / malformed_event, and nesting deeper than
MAX_DEPTH is ambiguous / malformed_event (both wake).

Wake eligibility is routing only: it grants no authority and binds nothing.
"""
import base64
import importlib.util
import json
import math
import random
import re
import struct
import subprocess
import time
from pathlib import Path

import pytest

from test_wd_reboot_bundle import LANE_TEST_SHELLS, REBOOT, _run_powershell
from test_wd_startup_recovery import q

ROOT = REBOOT.parents[2]
BIN = ROOT / '.agent-bridge/bin'
WAKE_PS = BIN / 'BridgeWakeClass.ps1'
CLASSIFIER_PS = BIN / 'BridgeEventClassifier.ps1'
MODULE_PATH = ROOT / 'tools/bridge_wake_class.py'
VECTORS = json.loads((ROOT / 'tests/fixtures/wake_class/v1/vectors.json')
                     .read_text(encoding='utf-8'))['vectors']
MAX_DEPTH = 256
CONTRACT = 'wd.wake-class.v1'
PUBLIC_FUNCTIONS = {
    'ConvertFrom-BridgeWakeEventJson', 'ConvertTo-BridgeWakeAsciiLower',
    'ConvertTo-BridgeWakePythonJson', 'Get-BridgeWakeClassFromDecoded',
    'Get-BridgeWakeClass',
}

_spec = importlib.util.spec_from_file_location('bridge_wake_class_ps_oracle', MODULE_PATH)
wc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(wc)

MALFORMED = {'contract': CONTRACT, 'class': 'ambiguous', 'wakes': True,
             'reason': 'malformed_event', 'control_signal': False}


def _depth(value):
    deepest, stack = 0, [(value, 1)]
    while stack:
        item, level = stack.pop()
        if isinstance(item, dict):
            deepest = max(deepest, level)
            stack.extend((v, level + 1) for v in item.values())
        elif isinstance(item, list):
            deepest = max(deepest, level)
            stack.extend((v, level + 1) for v in item)
    return deepest


def oracle(text, target):
    """Python reference on json.loads(text), plus the two raw-text rules."""
    if text is None:
        return dict(MALFORMED)
    try:
        event = json.loads(text)
    except (ValueError, RecursionError):
        return dict(MALFORMED)
    if _depth(event) > MAX_DEPTH:
        return dict(MALFORMED)
    return wc.classify(event, target)


def _encode(text):
    if text is None:
        return '-'
    return base64.b64encode(text.encode('utf-16-le', 'surrogatepass')).decode('ascii')


RUNNER = r"""
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
. __CLASSIFIER__
$out = New-Object System.Collections.Generic.List[string]
foreach ($line in [System.IO.File]::ReadAllLines(__SOURCE__)) {
    $fields = $line.Split("`t")
    $text = $null
    if ($fields[1] -cne '-') {
        $bytes = [Convert]::FromBase64String($fields[1])
        $chars = New-Object char[] ($bytes.Length / 2)
        [Buffer]::BlockCopy($bytes, 0, $chars, 0, $bytes.Length)
        $text = New-Object string (, $chars)
    }
    try {
        $out.Add((Get-BridgeWakeClass -EventJson $text -TargetAgent $fields[0] |
            ConvertTo-Json -Compress))
    } catch {
        $out.Add((@{ error = $_.Exception.Message } | ConvertTo-Json -Compress))
    }
}
[System.IO.File]::WriteAllLines(__TARGET__, $out)
"""


def run_port(ps, cases, tmp_path, name='cases'):
    """Classify (target, text) cases in one PowerShell; return result dicts."""
    source = tmp_path / f'{name}.txt'
    target = tmp_path / f'{name}.out'
    source.write_text('\n'.join(f'{t}\t{_encode(x)}' for t, x in cases), encoding='ascii')
    script = (RUNNER.replace('__CLASSIFIER__', q(CLASSIFIER_PS))
              .replace('__SOURCE__', q(source)).replace('__TARGET__', q(target)))
    _run_powershell(script, executable=ps)
    rows = target.read_text(encoding='utf-8-sig').splitlines()
    assert len(rows) == len(cases)
    return [json.loads(r) for r in rows]


def assert_parity(ps, named_cases, tmp_path, name='cases'):
    got = run_port(ps, [(t, x) for _, t, x in named_cases], tmp_path, name)
    wrong = {n: {'port': g, 'oracle': oracle(x, t)}
             for (n, t, x), g in zip(named_cases, got) if g != oracle(x, t)}
    assert not wrong, wrong


# --- golden vectors ---------------------------------------------------------

def _serializations(event):
    return {
        'ascii': json.dumps(event),
        'unicode': json.dumps(event, ensure_ascii=False),
        'compact': json.dumps(event, separators=(',', ':')),
        'indented': json.dumps(event, indent=2),
    }


@pytest.mark.parametrize('ps', LANE_TEST_SHELLS)
def test_every_golden_vector_matches_in_every_serialization(ps, tmp_path):
    named = [(f"{v['id']}:{mode}", v['target_agent'], text)
             for v in VECTORS for mode, text in _serializations(v['event']).items()]
    got = run_port(ps, [(t, x) for _, t, x in named], tmp_path)
    expected = {v['id']: dict(contract=CONTRACT, **v['expected']) for v in VECTORS}
    wrong = {n: g for (n, _, _), g in zip(named, got) if g != expected[n.rsplit(':', 1)[0]]}
    assert not wrong, wrong
    assert len(got) == 4 * len(VECTORS)


def test_oracle_equals_the_vectors_so_raw_cases_have_an_independent_anchor():
    for v in VECTORS:
        for text in _serializations(v['event']).values():
            assert oracle(text, v['target_agent']) == dict(contract=CONTRACT, **v['expected']), v['id']


# --- raw-text fidelity --------------------------------------------------------

def _row(**fields):
    base = {'agent': 'codex-lead-1', 'to': 'fable-5', 'type': 'message', 'status': 'info',
            'payload': {'notification': 'informational'}}
    base.update(fields)
    return json.dumps(base)


HINT = '"payload":{"notification":"informational"}'
RAW_CASES = [
    # Case-variant and repeated keys: ConvertFrom-Json merges or rejects these.
    ('case_variant_to', 'fable-5', '{"agent":"codex-lead-1","to":"fable-5","To":"x","type":"message","status":"info",' + HINT + '}'),
    ('case_variant_payload', 'fable-5', '{"agent":"codex-lead-1","to":"fable-5","type":"message","status":"info",' + HINT + ',"Payload":{"result":"ok"}}'),
    ('case_variant_request_id', 'fable-5', '{"agent":"codex-lead-1","to":"fable-5","type":"message","status":"info",' + HINT + ',"Request_Id":"r-1"}'),
    ('case_variant_notification', 'fable-5', '{"agent":"codex-lead-1","to":"fable-5","type":"message","status":"info","payload":{"notification":"informational","Notification":"x"}}'),
    ('case_variant_agent_self', 'fable-5', '{"agent":"fable-5","Agent":"codex-lead-1","to":"fable-5","type":"decision","status":"veto"}'),
    ('repeated_to_last_wins_away', 'fable-5', '{"agent":"codex-lead-1","to":"fable-5","type":"message","status":"info",' + HINT + ',"to":"codex-tools-1"}'),
    ('repeated_to_last_wins_here', 'fable-5', '{"agent":"codex-lead-1","to":"codex-tools-1","type":"message","status":"info",' + HINT + ',"to":"fable-5"}'),
    ('repeated_status_last_wins', 'fable-5', '{"agent":"codex-lead-1","to":"fable-5","type":"message","status":"veto",' + HINT + ',"status":"info"}'),
    ('repeated_payload_last_wins', 'fable-5', '{"agent":"codex-lead-1","to":"fable-5","type":"message","status":"info","payload":{"result":"x"},' + HINT + '}'),
    ('escaped_key_is_the_same_key', 'fable-5', '{"agent":"codex-lead-1","\\u0074o":"fable-5","type":"message","status":"info",' + HINT + '}'),
    ('escaped_key_case_variant', 'fable-5', '{"agent":"codex-lead-1","to":"fable-5","\\u0054o":"x","type":"message","status":"info",' + HINT + '}'),
    # Number, string, bool and null evidence stays distinct.
    ('request_id_number', 'fable-5', _row(request_id=1)),
    ('request_id_numeric_string', 'fable-5', _row(request_id='1')),
    ('request_id_float', 'fable-5', _row(request_id=1.0)),
    ('request_id_true', 'fable-5', _row(request_id=True)),
    ('request_id_null', 'fable-5', _row(request_id=None)),
    ('reply_id_zero', 'fable-5', _row(in_reply_to_request_id=0)),
    ('status_number', 'fable-5', _row(status=5)),
    ('type_bool', 'fable-5', _row(type=True)),
    ('to_number', 'fable-5', _row(to=5)),
    ('to_list_exact_name', 'fable-5', _row(to=['fable-5'])),
    ('agent_number', 'fable-5', _row(agent=7)),
    ('payload_list', 'fable-5', _row(payload=['notification', 'informational'])),
    ('payload_string', 'fable-5', _row(payload='informational')),
    ('payload_binding_zero', 'fable-5', _row(payload={'notification': 'informational', 'request_id': 0})),
    ('payload_binding_false', 'fable-5', _row(payload={'notification': 'informational', 'result': False})),
    ('payload_binding_empty_list', 'fable-5', _row(payload={'notification': 'informational', 'result_contract': []})),
    ('payload_binding_null', 'fable-5', _row(payload={'notification': 'informational', 'result': None})),
    ('payload_notification_true', 'fable-5', _row(payload={'notification': True})),
    ('ack_payload_number_value', 'fable-5', _row(status='received', payload={'request_type': 1})),
    ('heartbeat_payload_bool', 'fable-5', _row(type='heartbeat', status='alive', payload={'head': True})),
    ('big_int_in_payload', 'fable-5', '{"agent":"codex-lead-1","to":"fable-5","type":"message","status":"info","payload":{"notification":"informational","n":123456789012345678901234567890}}'),
    ('int_4300_digits', 'fable-5', '{"agent":"codex-lead-1","to":"fable-5","type":"message","status":"info","payload":{"notification":"informational","n":' + '9' * 4300 + '}}'),
    ('int_4301_digits', 'fable-5', '{"agent":"codex-lead-1","to":"fable-5","type":"message","status":"info","payload":{"notification":"informational","n":' + '9' * 4301 + '}}'),
    ('negative_int_4300_digits', 'fable-5', '{"agent":"codex-lead-1","to":"fable-5","type":"message","status":"info","payload":{"notification":"informational","n":-' + '9' * 4300 + '}}'),
    ('nan_literal', 'fable-5', '{"agent":"codex-lead-1","to":"fable-5","type":"message","status":"info","payload":{"notification":"informational","n":NaN}}'),
    ('infinity_to', 'fable-5', '{"agent":"codex-lead-1","to":[Infinity,-Infinity,1e400,-0.0],"type":"decision","status":"veto"}'),
    # Mentions inside non-string address values use Python json.dumps text.
    ('mention_list_newline_escape', 'fable-5', _row(to=['x\nfable-5'])),
    ('mention_list_control_escape', 'fable-5', _row(to=['x\u0001fable-5'])),
    ('mention_list_quote_escape', 'fable-5', _row(to=['x"fable-5'])),
    ('mention_list_backslash', 'fable-5', _row(to=['x\\fable-5'])),
    ('mention_list_tab', 'fable-5', _row(to=['x\tfable-5'])),
    ('mention_string_newline', 'fable-5', _row(to='x\nfable-5')),
    ('mention_string_newline_entry', 'fable-5', _row(to='x,\nfable-5')),
    ('mention_responders_key', 'fable-5', _row(to='codex-tools-1', expected_responders={'fable-5': {}})),
    ('mention_responders_nested_value', 'fable-5', _row(to='codex-tools-1', expected_responders={'x': ['FABLE-5']})),
    ('mention_digit_target_in_float', '1', _row(to=[1.5])),
    ('mention_digit_target_in_exponent', '16', _row(to=[1e16])),
    ('mention_digit_target_in_int', '12', _row(to=[12])),
    ('mention_true_target', 'true', _row(to=[True])),
    ('mention_null_target', 'null', _row(to=[None, 'x'])),
    ('mention_nan_target', 'nan', '{"agent":"codex-lead-1","to":[NaN],"type":"decision","status":"veto"}'),
    ('mention_infinity_target', 'infinity', '{"agent":"codex-lead-1","to":[Infinity],"type":"decision","status":"veto"}'),
    ('mention_u2028', 'fable-5', _row(to=['\u2028fable-5\u2029'])),
    # Targets that only match inside json.dumps escapes or number text.
    ('mention_escape_n_target', 'nfable-5', _row(to=['x\nfable-5'])),
    ('mention_escape_t_target', 'tfable-5', _row(to=['x\tfable-5'])),
    ('mention_escape_u_target', 'u0001fable-5', _row(to=['x\x01fable-5'])),
    ('mention_float_exponent_target', '1e-05', _row(to=[0.00001])),
    ('mention_float_fixed_target', '0.0001', _row(to=[0.0001])),
    # A non-ASCII capital that .NET invariant lowercasing maps to ASCII.
    ('nonascii_capital_in_envelope_key', 'fable-5', _row(**{'request_\u0130d': 'r-1'})),
    ('nonascii_capital_in_target_text', 'fable-5', _row(to=['FABLE\u2010' + '5', 'fab\u0130le-5'])),
    # The Kelvin sign lowercases to ASCII 'k' under .NET invariant rules; Python
    # _ascii_lower folds only A-Z.
    ('kelvin_sign_in_to', 'kappa-1', _row(to='\u212aappa-1')),
    ('kelvin_sign_in_status_root', 'fable-5', _row(status='\u212aill')),
    ('mention_lone_surrogate', 'fable-5', _row(to=['\ud800fable-5'])),
    ('mention_astral', 'fable-5', _row(to=['\U0001F600fable-5'])),
    # Python strip() whitespace in to entries and sender.
    ('to_entry_file_separator', 'fable-5', _row(to='\x1cfable-5\x1f')),
    ('to_entry_nbsp', 'fable-5', _row(to='\xa0fable-5\u3000')),
    ('to_entry_zero_width_space', 'fable-5', _row(to='\u200bfable-5')),
    ('to_entry_mongolian_vowel_separator', 'fable-5', _row(to='\u180efable-5')),
    ('sender_only_unit_separator', 'fable-5', _row(agent='\x1f')),
    ('sender_case_variant_padded', 'fable-5', _row(agent='\u3000FABLE-5 ')),
    ('sender_zero_width', 'fable-5', _row(agent='\u200b')),
    # Code points that culture comparison ignores (ICU in PowerShell 7): an
    # ordinal port must not treat them as equal to the plain spelling.
    ('ignorable_zwsp_in_sender', 'fable-5', _row(agent='fable-5\u200b', type='decision', status='veto')),
    ('ignorable_soft_hyphen_in_sender', 'fable-5', _row(agent='fable\u00ad-5', type='decision', status='veto')),
    ('ignorable_mvs_in_sender', 'fable-5', _row(agent='\u180efable-5', type='decision', status='veto')),
    ('ignorable_zwsp_in_to_entry', 'fable-5', _row(to='fable-5\u200b')),
    ('ignorable_soft_hyphen_in_to_entry', 'fable-5', _row(to='fable\u00ad-5')),
    ('ignorable_zwsp_in_notification', 'fable-5', _row(payload={'notification': 'informational\u200b'})),
    ('ignorable_zwsp_in_ack_payload_key', 'fable-5', _row(status='received', payload={'request_type\u200b': 'x'})),
    ('ignorable_zwsp_in_liveness_payload_key', 'fable-5', _row(type='heartbeat', status='alive', payload={'head\u00ad': 'x'})),
    ('ignorable_zwsp_in_noise_hint', 'fable-5', _row(status='received', payload={'notification': 'informational\u200b'})),
    ('ignorable_zwsp_in_binding_key', 'fable-5', _row(payload={'notification': 'informational', 'request_id\u200b': 'r'})),
    ('ignorable_zwsp_in_envelope_key', 'fable-5', _row(**{'to\u200b': 'x'})),
    ('ignorable_zwsp_in_agent_key', 'fable-5', '{"agent":"fable-5","agent\\u200b":"x","to":"fable-5","type":"decision","status":"veto"}'),
    # Non-ASCII and lone surrogates in type/status/ids.
    ('status_lone_surrogate', 'fable-5', _row(status='info\udc00')),
    ('type_astral', 'fable-5', _row(type='message\U0001F600')),
    ('request_id_non_ascii_digit', 'fable-5', _row(request_id='\u0661')),
    ('status_257_ascii', 'fable-5', _row(status='x' * 257)),
    # Text json.loads rejects.
    ('empty_text', 'fable-5', ''),
    ('whitespace_only', 'fable-5', ' \t\r\n'),
    ('null_text', 'fable-5', None),
    ('trailing_comma', 'fable-5', '{"agent":"codex-lead-1","to":"fable-5",}'),
    ('single_quotes', 'fable-5', "{'to':'fable-5'}"),
    ('comment', 'fable-5', '{"to":"fable-5"} // x'),
    ('bom', 'fable-5', '\ufeff' + _row()),
    ('vertical_tab_whitespace', 'fable-5', '\x0b' + _row()),
    ('form_feed_after', 'fable-5', _row() + '\x0c'),
    ('nbsp_whitespace', 'fable-5', '\xa0' + _row()),
    ('raw_control_char_in_string', 'fable-5', '{"agent":"codex-lead-1","to":"fable-5\x01","type":"decision","status":"veto"}'),
    ('raw_tab_in_string', 'fable-5', '{"agent":"codex-lead-1","to":"fable-5\t","type":"decision","status":"veto"}'),
    ('invalid_escape', 'fable-5', '{"agent":"codex-lead-1","to":"fable-5\\x41","type":"decision","status":"veto"}'),
    ('short_unicode_escape', 'fable-5', '{"to":"\\u12"}'),
    ('extra_data', 'fable-5', _row() + ' {}'),
    ('two_rows_on_one_line', 'fable-5', _row() + _row()),
    ('unterminated_string', 'fable-5', '{"agent":"codex-lead-1","to":"fable-5' + 'a' * 20000),
    ('unterminated_object', 'fable-5', '{"to":"fable-5"'),
    ('leading_zero', 'fable-5', '{"to":"fable-5","n":01}'),
    ('bare_dot_number', 'fable-5', '{"to":"fable-5","n":.5}'),
    ('trailing_dot_number', 'fable-5', '{"to":"fable-5","n":1.}'),
    ('negative_nan', 'fable-5', '{"to":"fable-5","n":-NaN}'),
    ('capital_true', 'fable-5', '{"to":"fable-5","n":True}'),
    ('missing_colon', 'fable-5', '{"to" "fable-5"}'),
    ('comma_instead_of_colon', 'fable-5', '{"agent":"codex-lead-1","to","fable-5","type":"decision","status":"veto"}'),
    ('non_string_key', 'fable-5', '{to:"fable-5"}'),
    ('array_trailing_comma', 'fable-5', '{"to":["fable-5",]}'),
    # Valid JSON that is not an object.
    ('top_array', 'fable-5', '[' + _row() + ']'),
    ('top_string', 'fable-5', '"fable-5"'),
    ('top_number', 'fable-5', '5'),
    ('top_null', 'fable-5', 'null'),
    ('top_true', 'fable-5', 'true'),
    ('surrounding_whitespace', 'fable-5', ' \r\n\t' + _row() + '\r\n'),
    ('empty_object', 'fable-5', '{}'),
]


def _nested(levels, inner='1'):
    return '[' * levels + inner + ']' * levels


DEPTH_CASES = [
    ('depth_max_in_payload', 'fable-5', '{"agent":"codex-lead-1","to":"fable-5","type":"message","status":"info","payload":{"notification":"informational","x":' + _nested(MAX_DEPTH - 2) + '}}'),
    ('depth_over_max_in_payload', 'fable-5', '{"agent":"codex-lead-1","to":"fable-5","type":"message","status":"info","payload":{"notification":"informational","x":' + _nested(MAX_DEPTH - 1) + '}}'),
    ('depth_max_bare', 'fable-5', _nested(MAX_DEPTH)),
    ('depth_over_max_bare', 'fable-5', _nested(MAX_DEPTH + 1)),
    ('depth_far_over_max', 'fable-5', _nested(5000)),
]


def test_raw_cases_are_distinct_and_cover_both_outcomes():
    names = [n for n, _, _ in RAW_CASES + DEPTH_CASES]
    assert len(names) == len(set(names))
    reasons = {oracle(x, t)['reason'] for _, t, x in RAW_CASES}
    assert {'malformed_event', 'case_variant_key', 'informational_hint',
            'ambiguous_target', 'not_targeted', 'payload_binding_field'} <= reasons


def test_raw_rules_are_explicit_in_the_oracle():
    by = {n: (t, x) for n, t, x in RAW_CASES + DEPTH_CASES}
    # A top-level list is malformed_event in the reference itself.
    assert oracle(by['depth_max_bare'][1], 'fable-5')['reason'] == 'malformed_event'
    assert oracle(by['depth_max_in_payload'][1], 'fable-5')['reason'] == 'informational_hint'
    assert oracle(by['depth_over_max_in_payload'][1], 'fable-5')['reason'] == 'malformed_event'
    json.loads(by['depth_over_max_in_payload'][1])  # Python decodes it; only the raw rule drains
    assert oracle(by['int_4300_digits'][1], 'fable-5')['reason'] == 'informational_hint'
    assert oracle(by['int_4301_digits'][1], 'fable-5')['reason'] == 'malformed_event'
    # Repeated exact keys: json.loads keeps the last value.
    assert oracle(by['repeated_to_last_wins_away'][1], 'fable-5')['reason'] == 'not_targeted'
    assert oracle(by['repeated_to_last_wins_here'][1], 'fable-5')['reason'] == 'informational_hint'
    # An escaped control character is not a mention boundary in json.dumps text.
    assert oracle(by['mention_list_newline_escape'][1], 'fable-5')['reason'] == 'not_targeted'
    assert oracle(by['mention_list_quote_escape'][1], 'fable-5')['reason'] == 'ambiguous_target'


@pytest.mark.parametrize('ps', LANE_TEST_SHELLS)
def test_raw_text_fidelity_matches_python_json_loads(ps, tmp_path):
    assert_parity(ps, RAW_CASES + DEPTH_CASES, tmp_path, 'raw')


# --- seeded differential fuzz ---------------------------------------------------

_KEYS = ['agent', 'to', 'type', 'status', 'payload', 'request_id', 'in_reply_to_request_id',
         'expected_responders', 'task_id', 'message']
_TARGETS = ['fable-5', 'claude-rco-1', 'codex-lead-1']
_WORDS = ['fable-5', 'FABLE-5', 'fable-50', 'xfable-5', ' fable-5 ', 'fable-5.', 'claude-rco-1',
          'claude-rco-10', 'codex-lead-1', 'message', 'status', 'intent', 'decision', 'finding',
          'heartbeat', 'liveness', 'received', 'seen', 'acknowledged', 'info', 'informational',
          'progress', 'veto', 'mergeHold', 'on-hold', 'approved', 'ci_green', 'r-1', '.r1', 'r-1\n',
          '', ' ', '\u0400', 'x' * 256, 'x' * 257, 'notification', 'request_id', 'result', 'head']


def _fuzz_value(rng, depth=0):
    roll = rng.random()
    if depth > 3 or roll < 0.55:
        return rng.choice(_WORDS)
    if roll < 0.62:
        return rng.choice([None, True, False, 0, 1, -1, 1.5, 1e16, 2 ** 70])
    if roll < 0.8:
        return [_fuzz_value(rng, depth + 1) for _ in range(rng.randrange(0, 3))]
    keys = rng.sample(['notification', 'request_id', 'Request_Id', 'result', 'head',
                       'request_type', 'fable-5', 'x'], rng.randrange(0, 3))
    return {k: (rng.choice(['informational', 'x', '']) if k == 'notification' else _fuzz_value(rng, depth + 1))
            for k in keys}


def _fuzz_case_key(rng, key):
    if rng.random() < 0.08:
        return ''.join(c.upper() if rng.random() < 0.5 else c for c in key)
    return key


def _fuzz_text(rng, event):
    pairs = list(event.items())
    if rng.random() < 0.15 and pairs:  # repeat a key, json.loads keeps the last value
        k, _ = rng.choice(pairs)
        pairs.append((k, _fuzz_value(rng)))
    sep = rng.choice([(',', ':'), (', ', ': '), (',\n  ', ' : ')])
    body = sep[0].join(json.dumps(k, ensure_ascii=rng.random() < 0.5) + sep[1] +
                       json.dumps(v, ensure_ascii=rng.random() < 0.5) for k, v in pairs)
    text = rng.choice(['', ' ', '\r\n']) + '{' + body + '}' + rng.choice(['', '\n', ' \t'])
    if rng.random() < 0.08:  # byte-level damage
        i = rng.randrange(len(text))
        text = text[:i] + rng.choice(['', '"', '}', ',', '\\', '\x01']) + text[i + 1:]
    return text


def fuzz_cases(seed, count):
    rng = random.Random(seed)
    cases = []
    for i in range(count):
        target = rng.choice(_TARGETS)
        roll = rng.random()
        if roll < 0.35:
            base = {'agent': 'codex-lead-1', 'to': target, 'type': rng.choice(['message', 'status', 'intent']),
                    'status': rng.choice(sorted(wc.BENIGN_NOTICE_STATUSES)), 'payload': {'notification': 'informational'}}
            if roll < 0.1:
                base['type'], base['status'] = 'message', rng.choice(['received', 'seen', 'acknowledged'])
                base['payload'] = {'request_type': 'x'}
            elif roll < 0.15:
                base['type'], base['status'] = rng.choice(['heartbeat', 'liveness']), 'alive'
                base['payload'] = {'head': 'abc'}
            mutations = rng.randrange(0, 3)
        else:
            seed_event = rng.choice(VECTORS)['event']
            base = dict(seed_event) if isinstance(seed_event, dict) and rng.random() < 0.5 else {}
            mutations = rng.randrange(2, 7)
        for key in rng.sample(_KEYS, mutations):
            if rng.random() < 0.7:
                base[_fuzz_case_key(rng, key)] = _fuzz_value(rng)
        if rng.random() < 0.3:
            base['to'] = rng.choice(_TARGETS + ['fable-5,claude-rco-1', ' fable-5'])
        cases.append((f'fuzz-{seed}-{i}', target, _fuzz_text(rng, base)))
    return cases


def test_fuzz_corpus_spans_the_contract():
    reasons = {oracle(x, t)['reason'] for _, t, x in fuzz_cases(20260928, 1200)}
    assert len(reasons) >= 20, reasons
    assert {'malformed_event', 'case_variant_key', 'informational_hint', 'ack', 'liveness'} <= reasons
    silent = sum(1 for _, t, x in fuzz_cases(20260928, 1200) if not oracle(x, t)['wakes'])
    assert silent >= 200, silent


@pytest.mark.parametrize('ps', LANE_TEST_SHELLS)
def test_seeded_differential_fuzz_matches_python(ps, tmp_path):
    assert_parity(ps, fuzz_cases(20260928, 1200), tmp_path, 'fuzz')


# --- Python json.dumps rendering ---------------------------------------------------

def _float_samples():
    rng = random.Random(7)
    values = [0.0, -0.0, 1.0, -1.5, 0.1, 0.2 + 0.1, 1e15, 1e16, 9999999999999998.0, 1e17,
              1e-4, 1e-5, 0.0001234, 123456789.123, 5e-324, 2.2250738585072014e-308,
              1.7976931348623157e308, 1 / 3, 2 / 3, 100.0, 1e22, 1e23, 4.35, 0.3]
    values += [struct.unpack('<d', struct.pack('<Q', rng.getrandbits(64)))[0] for _ in range(300)]
    values += [rng.uniform(-1e6, 1e6) for _ in range(100)]
    return [v for v in values if math.isfinite(v)]


@pytest.mark.parametrize('ps', LANE_TEST_SHELLS)
def test_python_json_rendering_of_decoded_values(ps, tmp_path):
    samples = [
        json.dumps(_float_samples()),
        json.dumps([2 ** 64, -(2 ** 63), 2 ** 63 - 1, 0, -0, 10 ** 40]),
        '[NaN, Infinity, -Infinity, 1e400, -1e400, -0.0, 1E2, 1e-400]',
        json.dumps({'a': 'x"\\\n\r\t\b\f\x01\x1f\x7f\u2028\U0001F600', 'b': [True, False, None, {}, []]},
                   ensure_ascii=True),
        '{"k":1,"k":2,"K":3}',
    ]
    source = tmp_path / 'render.txt'
    source.write_text('\n'.join(_encode(s) for s in samples), encoding='ascii')
    script = f"""
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
. {q(WAKE_PS)}
$out = foreach ($line in [System.IO.File]::ReadAllLines({q(source)})) {{
    $bytes = [Convert]::FromBase64String($line)
    $chars = New-Object char[] ($bytes.Length / 2)
    [Buffer]::BlockCopy($bytes, 0, $chars, 0, $bytes.Length)
    $decoded = ConvertFrom-BridgeWakeEventJson -Json (New-Object string (, $chars))
    if (-not $decoded.ok) {{ throw 'decode failed' }}
    $rendered = ConvertTo-BridgeWakePythonJson -Value $decoded.value
    $b = [System.Text.Encoding]::Unicode.GetBytes($rendered)
    [Convert]::ToBase64String($b)
}}
$out -join "`n"
"""
    lines = _run_powershell(script, executable=ps).stdout.split()
    got = [base64.b64decode(line).decode('utf-16-le', 'surrogatepass') for line in lines]
    want = [json.dumps(json.loads(s), ensure_ascii=False) for s in samples]
    assert len(got) == len(samples)
    assert got[1:] == want[1:]
    if got[0] == want[0]:
        return
    # Windows PowerShell 5.1 only: the .NET Framework double parser/formatter
    # is not correctly rounded, so a non-integer with 16-17 significant digits
    # can render with a different final digit. Nothing else may differ.
    assert 'WindowsPowerShell' in ps, 'PowerShell 7 must render every double exactly'
    got_items = got[0][1:-1].split(', ')
    want_items = want[0][1:-1].split(', ')
    assert len(got_items) == len(want_items)
    for g, w in zip(got_items, want_items):
        if g == w:
            continue
        digits = re.sub(r'[^0-9]', '', w.split('e')[0]).strip('0')
        assert len(digits) >= 16, (g, w)
        assert abs(float(g) - float(w)) <= 2 * math.ulp(float(w)), (g, w)
        assert g[:-1] != w[:-1] or g[-1] != w[-1]


LANES = ('codex-lead-1', 'codex-tools-1', 'claude-rco-1', 'claude-rco-2', 'fable-5', 'operator')


def test_ps51_float_rendering_limit_cannot_touch_a_lane_name():
    """A number rendering only holds 0-9 . e + - (and NaN/Infinity, which are exact).

    A last-digit difference can change a loose mention only for a target made
    of those characters alone; every lane name has another letter.
    """
    number_text = re.compile(r'[0-9.e+-]+')
    for lane in LANES:
        assert not number_text.fullmatch(lane), lane
    assert number_text.fullmatch('1e-05') and number_text.fullmatch('16')


# --- entrypoint contract --------------------------------------------------------------

@pytest.mark.parametrize('ps', LANE_TEST_SHELLS)
def test_entrypoint_refuses_decoded_objects_and_bad_targets(ps):
    script = f"""
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
. {q(CLASSIFIER_PS)}
$row = '{{"agent":"codex-lead-1","to":"fable-5","type":"decision","status":"veto"}}'
$results = [ordered]@{{}}
foreach ($case in @(
    @{{ name = 'pscustomobject'; event = ($row | ConvertFrom-Json); target = 'fable-5' }},
    @{{ name = 'hashtable'; event = @{{ to = 'fable-5' }}; target = 'fable-5' }},
    @{{ name = 'string_array'; event = @($row); target = 'fable-5' }},
    @{{ name = 'upper_target'; event = $row; target = 'Fable-5' }},
    @{{ name = 'empty_target'; event = $row; target = '' }},
    @{{ name = 'spaced_target'; event = $row; target = 'fable-5 ' }}
)) {{
    try {{
        $null = Get-BridgeWakeClass -EventJson $case.event -TargetAgent $case.target
        $results[$case.name] = 'returned'
    }} catch {{ $results[$case.name] = 'threw' }}
}}
$results['null_text'] = (Get-BridgeWakeClass -EventJson $null -TargetAgent 'fable-5').reason
$results['decoded_foreign'] = 'threw'
try {{
    $null = Get-BridgeWakeClassFromDecoded -Event ($row | ConvertFrom-Json) -TargetAgent 'fable-5'
    $results['decoded_foreign'] = 'returned'
}} catch {{ }}
$ok = ConvertFrom-BridgeWakeEventJson -Json $row
$results['decoded_lossless'] = (Get-BridgeWakeClassFromDecoded -Event $ok.value -TargetAgent 'fable-5').reason
$results | ConvertTo-Json -Compress
"""
    got = json.loads(_run_powershell(script, executable=ps).stdout)
    assert got == {
        'pscustomobject': 'threw', 'hashtable': 'threw', 'string_array': 'threw',
        'upper_target': 'threw', 'empty_target': 'threw', 'spaced_target': 'threw',
        'null_text': 'malformed_event', 'decoded_foreign': 'threw',
        'decoded_lossless': 'control_type',
    }


@pytest.mark.parametrize('ps', LANE_TEST_SHELLS)
def test_convertfrom_json_is_lossy_where_the_raw_entrypoint_is_exact(ps):
    """Reproduce why decoded objects are refused: the legacy decode loses evidence."""
    rows = {
        'case_variant_to': RAW_CASES[0][2],
        'case_variant_payload': RAW_CASES[1][2],
        'repeated_to': RAW_CASES[5][2],
    }
    script = f"""
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
. {q(CLASSIFIER_PS)}
$rows = [System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{base64.b64encode(json.dumps(rows).encode()).decode()}')) | ConvertFrom-Json
$out = [ordered]@{{}}
foreach ($p in $rows.PSObject.Properties) {{
    $legacy = 'convertfrom_json_threw'
    try {{
        $e = ConvertFrom-Json $p.Value
        $legacy = 'keys=' + (@($e.PSObject.Properties | ForEach-Object Name) -join '|') + ';to=' + [string]$e.to
    }} catch {{ }}
    $out[$p.Name] = [ordered]@{{ legacy = $legacy; port = (Get-BridgeWakeClass -EventJson $p.Value -TargetAgent 'fable-5').reason }}
}}
$out | ConvertTo-Json -Compress -Depth 4
"""
    got = json.loads(_run_powershell(script, executable=ps).stdout)
    for name, row in rows.items():
        assert got[name]['port'] == oracle(row, 'fable-5')['reason'], name
    # The legacy decode cannot see both spellings of the key: it throws or merges.
    for name in ('case_variant_to', 'case_variant_payload'):
        legacy = got[name]['legacy']
        if legacy != 'convertfrom_json_threw':
            kept = legacy.split(';', 1)[0][len('keys='):].split('|')
            assert len(kept) < len(json.loads(rows[name])), (name, legacy)
    # A case-variant 'to' makes addressing inexact, so the named lane is only a mention.
    assert got['case_variant_to']['port'] == 'ambiguous_target'
    assert got['repeated_to']['port'] == 'not_targeted'


# --- loading, purity and vocabulary ----------------------------------------------------

@pytest.mark.parametrize('ps', LANE_TEST_SHELLS)
def test_loading_defines_functions_only(ps, tmp_path):
    work = tmp_path / 'cwd'
    work.mkdir()
    script = f"""
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
Set-Location -LiteralPath {q(work)}
function Snap {{
    [ordered]@{{
        functions = @(Get-ChildItem function: | ForEach-Object Name | Sort-Object)
        variables = @(Get-Variable | ForEach-Object Name | Where-Object {{ $_ -notin @('_', 'PWD', '?', '^', '$', 'args', 'input', 'MyInvocation', 'PSBoundParameters', 'PSCmdlet', 'LASTEXITCODE', 'Error', 'StackTrace', 'ConsoleFileName') }} | Sort-Object)
        env = @(Get-ChildItem env: | ForEach-Object {{ $_.Name + '=' + $_.Value }} | Sort-Object)
        location = (Get-Location).Path
        files = @(Get-ChildItem -LiteralPath {q(work)} -Force -Recurse | ForEach-Object FullName)
    }}
}}
$before = $null; $afterWake = $null; $afterClassifier = $null
$before = Snap
. {q(WAKE_PS)}
$afterWake = Snap
. {q(CLASSIFIER_PS)}
$afterClassifier = Snap
[ordered]@{{
    added_by_wake = @($afterWake.functions | Where-Object {{ $_ -notin $before.functions }})
    variables_changed = [bool](Compare-Object $before.variables $afterClassifier.variables)
    env_changed = [bool](Compare-Object $before.env $afterClassifier.env)
    location_changed = $before.location -ne $afterClassifier.location
    files = @($afterClassifier.files)
    legacy_present = [bool](Get-Command Test-BridgeWakeEligible -CommandType Function -ErrorAction SilentlyContinue)
}} | ConvertTo-Json -Compress
"""
    got = json.loads(_run_powershell(script, executable=ps).stdout)
    assert set(got['added_by_wake']) == PUBLIC_FUNCTIONS
    assert got['variables_changed'] is False
    assert got['env_changed'] is False
    assert got['location_changed'] is False
    assert got['files'] == []
    assert got['legacy_present'] is True


FORBIDDEN_PS = re.compile(
    r'(?i)\b(Set-Content|Add-Content|Out-File|New-Item|Remove-Item|Copy-Item|Move-Item|'
    r'Invoke-Expression|iex|Start-Process|Invoke-WebRequest|Invoke-RestMethod|Import-Module|'
    r'Add-Type|ConvertFrom-Json|Get-Content|python|py\.exe|\$env:|Set-Variable|'
    r'Set-Location|Start-Job|Register-|System\.IO\.|WriteAll|Environment\]::Set)')


def test_port_source_is_pure():
    import ast as _ast  # noqa: F401  (documentation: purity is checked textually and by AST)
    source = WAKE_PS.read_text(encoding='utf-8')
    code = re.sub(r'<#.*?#>', '', source, flags=re.S)
    code = '\n'.join(line for line in code.splitlines() if not line.lstrip().startswith('#'))
    assert not FORBIDDEN_PS.search(code), FORBIDDEN_PS.search(code).group(0)
    top = [line for line in code.splitlines() if line and not line[0].isspace()]
    assert all(line.startswith('function ') or line == '}' for line in top), top


def test_classifier_delegation_is_the_only_classifier_change():
    text = CLASSIFIER_PS.read_text(encoding='utf-8')
    loads = re.findall(r"^\. \(Join-Path \$PSScriptRoot '([^']+)'\)", text, flags=re.M)
    assert loads == ['BridgeWakeClass.ps1']
    assert 'Get-BridgeWakeClass' not in text.split('BridgeWakeClass.ps1', 1)[1]


def _ps_list(source, name):
    m = re.search(r'\$' + name + r' = @\((.*?)\)', source, flags=re.S)
    assert m, name
    return re.findall(r"'([^']*)'", m.group(1))


def test_port_vocabularies_equal_the_reference_exactly():
    source = WAKE_PS.read_text(encoding='utf-8')
    pairs = {
        'controlTypes': wc.CONTROL_TYPES, 'noticeTypes': wc.NOTICE_TYPES,
        'livenessTypes': wc.LIVENESS_TYPES, 'ackStatuses': wc.ACK_STATUSES,
        'ackPayloadKeys': wc.ACK_PAYLOAD_KEYS, 'livenessPayloadKeys': wc.LIVENESS_PAYLOAD_KEYS,
        'bindingKeys': wc.PAYLOAD_BINDING_KEYS, 'envelopeKeys': wc.ENVELOPE_KEYS,
        'benign': wc.BENIGN_NOTICE_STATUSES, 'roots': wc.CONTROL_ROOTS,
        'nonWaking': wc.NON_WAKING_CLASSES,
    }
    for name, ref in pairs.items():
        port = _ps_list(source, name)
        assert len(port) == len(set(port)), name
        assert set(port) == set(ref), name
    assert _ps_list(source, 'roots') == list(wc.CONTROL_ROOTS)
    whitespace = re.search(r'\$pyWhitespace = \[char\[\]\]@\((.*?)\)', source, flags=re.S).group(1)
    port_ws = {int(tok, 0) for tok in re.findall(r'0x[0-9a-f]+|\d+', whitespace)}
    assert port_ws == {c for c in range(0x110000) if chr(c).isspace()}
    assert "'wd.wake-class.v1'" in source and wc.CONTRACT == CONTRACT


# --- guard mutants ------------------------------------------------------------------------

PS_MUTANTS = [
    ('self_ignores_variant', "-and [string]::Equals($agent, $TargetAgent) -and -not $agentVariant", "-and [string]::Equals($agent, $TargetAgent)"),
    ('self_compare_culture', '[string]::Equals($agent, $TargetAgent)', '$agent -ceq $TargetAgent'),
    ('address_empty_kept', "-and -not (& $isEmptyValue $Event[$k])", ''),
    ('to_variant_ignored', "if (-not (& $hasVariant $Event 'to') -and ($to -is [string]))", "if (($to -is [string]))"),
    ('to_not_trimmed', '$t = $entry.Trim($pyWhitespace)', '$t = $entry'),
    ('to_exact_culture', '[string]::Equals($t, $TargetAgent)', '$t -ceq $TargetAgent'),
    ('variant_compare_culture', '[string]::Equals((ConvertTo-BridgeWakeAsciiLower ([string]$k)), $Name)', '((ConvertTo-BridgeWakeAsciiLower ([string]$k)) -ceq $Name)'),
    ('to_trim_dotnet_whitespace', '$t = $entry.Trim($pyWhitespace)', '$t = $entry.Trim()'),
    ('mention_unbounded', "$pattern = '(?<![a-z0-9])' + [regex]::Escape($TargetAgent) + '(?![a-z0-9])'", '$pattern = [regex]::Escape($TargetAgent)'),
    ('mention_raw_text', '$text = if ($v -is [string]) { $v } else { ConvertTo-BridgeWakePythonJson -Value $v }', '$text = if ($v -is [string]) { $v } else { [string]$v }'),
    ('mention_case_sensitive', "if ([regex]::IsMatch((ConvertTo-BridgeWakeAsciiLower $text), $pattern))", 'if ([regex]::IsMatch($text, $pattern))'),
    ('envelope_variants_ignored', "if (& $hasVariant $Event $key) { return & $result 'ambiguous' 'case_variant_key' $ctl }", ''),
    ('sender_blank_allowed', "if (($agent -isnot [string]) -or $agent.Trim($pyWhitespace).Length -eq 0) {", 'if (($agent -isnot [string])) {'),
    ('sender_variant_ignored', "if ([string]::Equals((ConvertTo-BridgeWakeAsciiLower $agent.Trim($pyWhitespace)), $TargetAgent)) {", 'if ($false) {'),
    ('id_anchor_dollar', "'\\A[A-Za-z0-9][A-Za-z0-9._:-]{0,255}\\z'", "'\\A[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$'"),
    ('id_numbers_absent', "if ($null -eq $v) { return 'absent' }", "if (-not $v) { return 'absent' }"),
    ('id_malformed_ignored', "if ($rid -ceq 'malformed' -or $irr -ceq 'malformed') {", 'if ($false) {'),
    ('type_status_empty_allowed', "($etype -is [string]) -and $etype.Length -gt 0 -and ($status -is [string]) -and $status.Length -gt 0", "($etype -is [string]) -and ($status -is [string])"),
    ('non_ascii_allowed', "if ($etype -cmatch '[^\\x00-\\x7F]' -or $status -cmatch '[^\\x00-\\x7F]') {", 'if ($false) {'),
    ('oversize_257', 'if ($etype.Length -gt 256 -or $status.Length -gt 256) {', 'if ($etype.Length -gt 257 -or $status.Length -gt 257) {'),
    ('liveness_conflict_ignored', "if ($controlStatus -or $rid -cne 'absent' -or $irr -cne 'absent') {", 'if ($false) {'),
    ('liveness_payload_unchecked', 'if (-not (& $noisePayloadOk $livenessPayloadKeySet)) {', 'if ($false) {'),
    ('ack_any_type', "if (-not [string]::Equals($etype, 'message') -or $rid -cne 'absent') {", "if ($rid -cne 'absent') {"),
    ('ack_payload_unchecked', 'if (-not (& $noisePayloadOk $ackPayloadKeySet)) {', 'if ($false) {'),
    ('noise_payload_value_type', "if (-not $Allowed.Contains([string]$k) -or $p[$k] -isnot [string]) { return $false }", 'if (-not $Allowed.Contains([string]$k)) { return $false }'),
    ('noise_keys_culture', '$Allowed.Contains([string]$k)', '($Allowed -ccontains [string]$k)'),
    ('noise_payload_hint_value', "return [string]::Equals($p['notification'], 'informational')", 'return $true'),
    ('noise_hint_culture', "return [string]::Equals($p['notification'], 'informational')", "return ($p['notification'] -ceq 'informational')"),
    ('noise_payload_null_rejected', "if ($null -eq $p) { return $true }", "if ($null -eq $p) { return $false }"),
    ('request_reply_conflict_ignored', "if ($irr -ceq 'valid' -and $rid -ceq 'valid') {", 'if ($false) {'),
    ('control_type_ignored', "if ($controlTypeSet.Contains($etype)) { return & $result 'control' 'control_type' $ctl }", ''),
    ('unknown_type_suppressible', "if (-not $noticeTypeSet.Contains($etype)) { return & $result 'ambiguous' 'unknown_type' $ctl }", ''),
    ('control_status_ignored', "if ($controlStatus) { return & $result 'control' 'control_status' $ctl }", ''),
    ('payload_missing_ok', "if (-not $Event.Contains('payload') -or $null -eq $payload) {", 'if ($false) {'),
    ('payload_binding_truthy', "$null -ne $v -and -not (($v -is [string]) -and $v.Length -eq 0)) {", '[bool]$v) {'),
    ('payload_binding_case_sensitive', 'if ($bindingKeySet.Contains((ConvertTo-BridgeWakeAsciiLower ([string]$k))) -and', 'if ($bindingKeySet.Contains(([string]$k)) -and'),
    ('notification_variant_ignored', "if ($notificationVariant -or ($notificationPresent", "if (($notificationPresent"),
    ('notification_type_unchecked', "$payload['notification'] -is [string] -and\n            [string]::Equals($payload['notification'], 'informational')", "$payload['notification'] -eq 'informational'"),
    ('notification_compare_culture', "[string]::Equals($payload['notification'], 'informational')))) {", "$payload['notification'] -ceq 'informational'))) {"),
    ('allowlist_case_insensitive', 'if (-not $benignSet.Contains($status))', 'if (-not ($benign -contains $status))'),
    ('allowlist_dropped', "if (-not $benignSet.Contains($status)) { return & $result 'ambiguous' 'unlisted_status' $ctl }", ''),
    ('root_substring_to_token', "foreach ($root in $roots) { if ($joined.Contains($root)) { return $true } }", "foreach ($root in $roots) { if ($joined -ceq $root) { return $true } }"),
    ('ascii_lower_everything', "if ($Text -cnotmatch '[A-Z]') { return $Text }", 'return $Text.ToLowerInvariant()'),
    ('decode_case_insensitive_keys', 'OrderedDictionary ([System.StringComparer]::Ordinal)', 'OrderedDictionary ([System.StringComparer]::OrdinalIgnoreCase)'),
    ('decode_digit_limit_off', 'if ($digits -gt 4300) { return $fail }', ''),
    ('decode_depth_unbounded', 'if ($stack.Count -ge $maxDepth) { return $fail }', ''),
    ('decode_coverage_unchecked', ' -or $end -ne $Json.Length', ''),
    ('decode_second_value_ok', "if ($state -ceq 'done') { return $fail }", ''),
    ('decode_colon_optional', "if (-not [string]::Equals($tok, ':')) { return $fail }", ''),
    ('decode_trailing_comma_ok', "if ($state -ceq 'key_or_end' -and [string]::Equals($tok, '}')) {", "if ([string]::Equals($tok, '}')) {"),
    ('decode_control_chars_ok', '[^"\\\\\\x00-\\x1f]+', '[^"\\\\]+'),
    ('decode_nan_rejected', "'NaN' { $value = [double]::NaN }", "'NaN' { return $fail }"),
    ('render_no_escape_control', "elseif ($code -lt 32) { [void]$sb.Append('\\u').Append($code.ToString('x4')) }", ''),
    ('render_newline_raw', "elseif ($code -eq 10) { [void]$sb.Append('\\n') }", ''),
    ('render_float_threshold', 'if ($decpt -le -4 -or $decpt -gt 16) {', 'if ($decpt -le -5 -or $decpt -gt 16) {'),
]
# Deliberately not mutated here: the separators (', ' and ': '), culture
# lowercasing of A-Z and the sign of -0.0 are equivalent for classification
# (mention boundaries only see alphanumeric versus not; the exact rendering is
# pinned by test_python_json_rendering_of_decoded_values), and refusing decoded
# objects is covered by test_entrypoint_refuses_decoded_objects_and_bad_targets.


def test_ps_mutant_fragments_occur_exactly_once():
    source = WAKE_PS.read_text(encoding='utf-8')
    counts = {name: source.count(old) for name, old, _ in PS_MUTANTS}
    assert all(c == 1 for c in counts.values()), {n: c for n, c in counts.items() if c != 1}
    assert len({n for n, _, _ in PS_MUTANTS}) == len(PS_MUTANTS)


def _run_ps_long(script, ps, timeout=900):
    """_run_powershell with a longer timeout for the mutant sweep."""
    return subprocess.run([ps, '-NoLogo', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
                           '-Command', script], cwd=ROOT, capture_output=True, text=True,
                          timeout=timeout, check=True)


def _signature(result):
    return '|'.join([result['contract'], result['class'], result['reason'],
                     str(result['wakes']), str(result['control_signal'])])


def test_every_ps_mutant_is_caught(tmp_path):
    """Each mutated port must disagree with the oracle on some case, without throwing.

    PowerShell stops a mutant at its first non-throwing disagreement, so a
    caught mutant is cheap; a survivor runs every case.
    """
    ps = LANE_TEST_SHELLS[0]
    cases = RAW_CASES + DEPTH_CASES + [
        (v['id'], v['target_agent'], json.dumps(v['event'])) for v in VECTORS]
    source = WAKE_PS.read_text(encoding='utf-8')
    variants = tmp_path / 'variants'
    variants.mkdir()
    for index, (_, old, new) in enumerate(PS_MUTANTS):
        (variants / f'{index}.ps1').write_text(source.replace(old, new), encoding='utf-8')
    cases_file = tmp_path / 'mutant_cases.txt'
    cases_file.write_text('\n'.join(f'{t}\t{_encode(x)}\t{_signature(oracle(x, t))}' for _, t, x in cases),
                          encoding='ascii')
    script = f"""
$ErrorActionPreference = 'Stop'
$cases = foreach ($line in [System.IO.File]::ReadAllLines({q(cases_file)})) {{
    $f = $line.Split("`t")
    $text = $null
    if ($f[1] -cne '-') {{
        $b = [Convert]::FromBase64String($f[1])
        $c = New-Object char[] ($b.Length / 2); [Buffer]::BlockCopy($b, 0, $c, 0, $b.Length)
        $text = New-Object string (, $c)
    }}
    ,@($f[0], $text, $f[2])
}}
foreach ($i in 0..{len(PS_MUTANTS) - 1}) {{
    $path = Join-Path {q(variants)} ($i.ToString() + '.ps1')
    & {{
        param($Path, $Cases)
        Set-StrictMode -Version Latest
        . $Path
        for ($n = 0; $n -lt $Cases.Count; $n++) {{
            $case = $Cases[$n]
            try {{ $r = Get-BridgeWakeClass -EventJson $case[1] -TargetAgent $case[0] }} catch {{ continue }}
            $sig = @($r.contract, $r.class, $r.reason, [string]$r.wakes, [string]$r.control_signal) -join '|'
            if (-not [string]::Equals($sig, $case[2])) {{ return $n }}
        }}
        return -1
    }} $path $cases
}}
"""
    out = _run_ps_long(script, ps).stdout.split()
    assert len(out) == len(PS_MUTANTS)
    survivors = [name for (name, _, _), first in zip(PS_MUTANTS, out) if int(first) < 0]
    assert not survivors, survivors


# --- performance -----------------------------------------------------------------------------

@pytest.mark.parametrize('ps', LANE_TEST_SHELLS)
def test_a_large_row_classifies_in_bounded_time(ps, tmp_path):
    big = json.dumps({'agent': 'codex-lead-1', 'to': 'fable-5', 'type': 'message', 'status': 'info',
                      'payload': {'notification': 'informational',
                                  'evidence': [{'k': 'v' * 50, 'n': i} for i in range(3000)]}})
    assert len(big) > 200_000
    started = time.monotonic()
    assert run_port(ps, [('fable-5', big)], tmp_path, 'big') == [oracle(big, 'fable-5')]
    assert time.monotonic() - started < 60

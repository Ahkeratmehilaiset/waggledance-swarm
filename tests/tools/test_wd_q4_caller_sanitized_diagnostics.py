"""Phase diagnostics are fixed classes, never raw input or exception text."""
import json
import pytest

from tests.tools.test_wd_q4_lane_caller_cli import case, invoke, legacy_baseline, QUERY_CANARY, TOKEN_CANARY


@pytest.mark.parametrize('inner,expected', [
    ('transport_cleanup_unknown','CALLER_TRANSPORT_CLEANUP_UNKNOWN'),
    ('attestation_cleanup_unknown','CALLER_ATTESTATION_CLEANUP_UNKNOWN'),
    ('session_refused','CALLER_SESSION_UNKNOWN'),
    ('timeout','CALLER_TIMEOUT_UNKNOWN'),
    ('secret/path/attacker-controlled','CALLER_RESULT_UNKNOWN'),
])
def test_enabled_closed_phase_does_not_disclose_input(case, monkeypatch, capsys, inner, expected):
    legacy_baseline(case, monkeypatch)
    monkeypatch.setenv('WD_Q4_DIAGNOSTIC_PHASES', '1')
    case['outcome']['result'] = {'status':'UNKNOWN','cleanup':'unknown','inner_status':inner}
    assert invoke(case, monkeypatch) == 3
    captured = capsys.readouterr()
    assert captured.out == ''
    records = [json.loads(line) for line in captured.err.splitlines()]
    assert records[-1] == {'phase': expected}
    assert QUERY_CANARY not in captured.err and TOKEN_CANARY not in captured.err
    assert 'secret/path/attacker-controlled' not in captured.err


def test_phase_diagnostics_are_off_without_exact_opt_in(case, monkeypatch, capsys):
    legacy_baseline(case, monkeypatch)
    monkeypatch.setenv('WD_Q4_DIAGNOSTIC_PHASES', 'unexpected')
    case['outcome']['result'] = {'status':'UNKNOWN','cleanup':'unknown','inner_status':'transport_cleanup_unknown'}
    assert invoke(case, monkeypatch) == 3
    records = [json.loads(line) for line in capsys.readouterr().err.splitlines()]
    assert records == [{'error':'lane_caller_refused','authority':'none'}]

"""Recovery is default off and never infers authority from a marker alone."""
import pytest
from tools import wd_q4_live_update_recovery as recovery


def test_recovery_default_off_does_not_inspect_inputs():
    assert recovery.recover(config_path=object(), expected_config_sha256=object(),
                            expected_attempt=object(), expected_progress_sha256=object()) == {'status': 'OFF'}


def test_recovery_refuses_unpinned_inputs_before_write(tmp_path):
    path = tmp_path / 'config.json'
    path.write_text('{}', encoding='ascii')
    with pytest.raises(recovery.RecoveryRefused):
        recovery.recover(enabled=True, config_path=str(path), expected_config_sha256='0' * 64,
                         expected_attempt='1' * 64, expected_progress_sha256='2' * 64)


def test_job_recovery_cli_forwards_exact_pins(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(recovery, 'recover', lambda **kw: calls.append(kw) or {'status': 'synthetic'})
    assert recovery.main(['--recover', '--config', 'synthetic', '--sha256', 'a' * 64, '--attempt', 'b' * 64,
                          '--job-receipt-sha256', 'c' * 64, '--pointer-sha256', 'd' * 64]) == 0
    assert calls[0]['expected_job_receipt_sha256'] == 'c' * 64
    assert calls[0]['expected_pointer_sha256'] == 'd' * 64


@pytest.mark.parametrize('extra', [
    ['--progress-sha256', 'c' * 64, '--job-receipt-sha256', 'd' * 64, '--pointer-sha256', 'e' * 64],
    ['--runner-receipt-sha256', 'c' * 64, '--job-receipt-sha256', 'd' * 64, '--pointer-sha256', 'e' * 64],
    ['--job-receipt-sha256', '', '--pointer-sha256', 'd' * 64],
    ['--job-receipt-sha256', 'c' * 64],
    ['--job-receipt-sha256', 'c' * 64, '--job-receipt-sha256', 'd' * 64, '--pointer-sha256', 'e' * 64],
])
def test_recovery_cli_refuses_mixed_or_incomplete_evidence_before_call(monkeypatch, capsys, extra):
    monkeypatch.setattr(recovery, 'recover', lambda **kw: pytest.fail('invalid evidence reached recovery'))
    assert recovery.main(['--recover', '--config', 'synthetic', '--sha256', 'a' * 64, '--attempt', 'b' * 64, *extra]) == 2

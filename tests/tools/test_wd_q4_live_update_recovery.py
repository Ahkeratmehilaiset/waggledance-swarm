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

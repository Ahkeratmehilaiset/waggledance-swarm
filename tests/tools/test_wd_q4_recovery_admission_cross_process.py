"""Real Windows launcher + Python writer interleaving; all data synthetic."""
import base64
import json
from pathlib import Path
import subprocess
import threading
import time
import pytest
from tests.tools.test_wd_q4_adopt_recovery_ps import candidate, job, prep_job, repin_changed, command
from tests.tools import test_wd_q4_adopt_recovery_ps as ps_fixture
from tests.tools import test_wd_q4_genesis_bootstrap as genesis_fixture
from tests.tools import test_wd_q4_continuation_adopt as adopt_fixture
from tools import wd_q4_continuation_adopt as adopt
from tools import wd_q4_genesis_to_composer_cli as composer


@pytest.mark.parametrize('crash_first',[False,True])
def test_ps_job_marker_blocks_real_adoption_before_and_after_launcher_crash(job,tmp_path,monkeypatch,capsys,crash_first):
    prep_job(job); scratch=job['bundle'].parent/'scratch'; scratch.mkdir()
    case=tmp_path/'writer-case'; case.mkdir()
    synthetic=genesis_fixture.synthetic.__wrapped__(case,monkeypatch)
    args=adopt_fixture.actual_continuation.__wrapped__(synthetic,monkeypatch,case,capsys)
    actual=composer._tuple_part(json.loads(args['genesis_tuple_bytes']),'route')
    private=Path(actual['private_state_root']); checkpoint=Path(actual['checkpoint_root'])
    tup=json.loads(job['tuple'].read_bytes()); route=json.loads(base64.b64decode(tup['route']['bytes_b64']))
    route.update(private_state_root=str(private),checkpoint_root=str(checkpoint))
    raw=ps_fixture._existing.encode(route)
    tup['route']={'bytes_b64':base64.b64encode(raw).decode(),'sha256':ps_fixture._existing.sha(raw)}
    job['tuple'].write_bytes(ps_fixture._existing.encode(tup))
    config=json.loads(job['config'].read_bytes()); config['tuple_sha256']=ps_fixture._existing.sha(job['tuple'].read_bytes())
    job['config'].write_bytes(ps_fixture._existing.encode(config)); job['config_pin']=ps_fixture._existing.sha(job['config'].read_bytes())
    cli=job['bundle']/'tools-bootstrap/tools/wd_q4_continuation_adopt_recovery_cli.py'
    cli.write_text("def main(args):\n import time\n time.sleep(30)\n print('{\"status\":\"UNKNOWN\"}')\n return 3\n",encoding='ascii')
    repin_changed(job,['tools-bootstrap/tools/wd_q4_continuation_adopt_recovery_cli.py'])
    marker=private/'q4-adopt-recovery-job.pending.json'
    launcher=subprocess.Popen(command(job,scratch),stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    try:
        deadline=time.monotonic()+25
        while not marker.exists() and time.monotonic()<deadline:
            if launcher.poll() is not None: pytest.fail('synthetic launcher exited before reserving')
            time.sleep(.1)
        assert marker.exists()
        if crash_first:
            launcher.kill(); launcher.communicate(timeout=10)
        with pytest.raises((adopt.AdoptRefused,adopt.AdoptOutcomeUnknown)):
            adopt.run_once(**args)
        assert marker.exists()
        assert not (private/'q4-continuation-adopt.pending.json').exists()
    finally:
        if launcher.poll() is None: launcher.kill()
        launcher.communicate(timeout=10)
    assert marker.exists()

"""Opt-in RED contract: real W1 adoption recovery needs an owned Job capability.

No mocked capability or ownership bypass is provided here. These tests remain
skipped unless explicitly requested on Windows with the native test runtime.
"""
import json
import os
from pathlib import Path
import sys
import pytest
from tests.tools.test_wd_q4_adopt_recovery_job_e2e import prepared, launch, paths, _available
from tools import wd_q4_continuation_adopt as adopt
from tools import wd_q4_continuation_adopt_recovery as recovery
from tools import wd_q4_owned_recovery_parent, wd_q4_owned_recovery_reader

ENABLED=(os.environ.get('WD_Q4_ADOPT_CAPABILITY_E2E')=='1' and
         _available({'WD_Q4_ADOPT_JOB_E2E':'1'},sys.platform))
pytestmark=pytest.mark.skipif(not ENABLED,reason='explicit synthetic Windows capability E2E opt-in required')


@pytest.mark.parametrize('stage,expected',[('before','NOT_ADOPTED'),('after','ADOPTED')])
def test_owned_job_recovers_proven_adoption_without_unblocking_other_writers(prepared,monkeypatch,stage,expected):
    case,runtime,args=prepared
    private,generation=paths(args)
    original=adopt.update._publish
    def interrupt(*pos,**kw):
        if stage=='after': original(*pos,**kw)
        raise KeyboardInterrupt()
    with monkeypatch.context() as scoped:
        scoped.setattr(adopt.update,'_publish',interrupt)
        with pytest.raises(adopt.AdoptInterrupted): adopt.run_once(**args)
    adoption_pending=private/'q4-continuation-adopt.pending.json'
    assert adoption_pending.exists()
    result=launch(case,runtime,args)
    assert result.returncode==0, result.stdout+result.stderr
    assert json.loads(result.stdout)=={'status':expected}
    assert not adoption_pending.exists()
    assert not (private/'q4-adopt-recovery-job.pending.json').exists()
    if stage=='after': assert generation.is_dir()


def test_direct_recovery_with_foreign_job_marker_stays_unknown(prepared,monkeypatch):
    case,runtime,args=prepared
    private,generation=paths(args)
    with monkeypatch.context() as scoped:
        scoped.setattr(adopt.update,'_publish',lambda *_a,**_k: (_ for _ in ()).throw(KeyboardInterrupt()))
        with pytest.raises(adopt.AdoptInterrupted): adopt.run_once(**args)
    adoption_pending=private/'q4-continuation-adopt.pending.json'
    prior=adoption_pending.read_bytes()
    job_marker=private/'q4-adopt-recovery-job.pending.json'; job_marker.write_bytes(b'foreign synthetic marker')
    with pytest.raises(recovery.RecoveryUnknown): recovery.recover_once(**args)
    assert adoption_pending.read_bytes()==prior and job_marker.read_bytes()==b'foreign synthetic marker'
    assert not generation.exists()

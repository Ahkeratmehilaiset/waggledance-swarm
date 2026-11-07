"""Cross-recovery admission: isolated synthetic files, real pair locks."""
import json
from contextlib import contextmanager
from pathlib import Path
import pytest
from sandbox.mind.step2 import prefix_private_state
from tools import wd_q4_status_prefix_recovery as prefix
from tools import wd_q4_live_update_recovery as live

JOB='q4-adopt-recovery-job.pending.json'

@pytest.mark.parametrize('late',[False,True])
def test_prefix_completion_preserves_owned_markers_when_job_pending(tmp_path, monkeypatch, late):
    checkpoint,private,model,source,sidecar=[tmp_path/n for n in ('checkpoint','private','model','source','sidecar')]
    for p in (checkpoint,private,model,source,sidecar): p.mkdir()
    job=private/JOB
    if not late: job.write_bytes(b'synthetic')
    marks=(checkpoint/'runner.pending',private/'runner.pending')
    for p in marks: p.write_bytes(b'token')
    infos=tuple(p.stat() for p in marks)
    checked=[]
    monkeypatch.setattr(prefix.adapter,'strict_pins',lambda *a:(frozenset(),{}))
    monkeypatch.setattr(prefix,'_verify_marker',lambda *a:checked.append(True))
    monkeypatch.setattr(prefix.checkpoint_mod,'read_verified_prefix_pointer',
                        lambda *a,**k: (_ for _ in ()).throw(ValueError('past admission')))
    if late:
        original=prefix_private_state._checkpoint_lock
        @contextmanager
        def arrival(root,timeout):
            with original(root,timeout):
                job.write_bytes(b'synthetic')
                yield
        monkeypatch.setattr(prefix_private_state,'_checkpoint_lock',arrival)
    with pytest.raises(ValueError):
        prefix.complete_owned_publication(checkpoint=checkpoint,private=private,model=model,
            source_parent=source,sidecar_parent=sidecar,
            expected_ids=tuple(prefix.roots._observe(p) for p in (checkpoint,private,model,source,sidecar)),
            marks=marks,marker_infos=infos,token=b'token',report={'status':'EMPTY_NO_CHANGE','pointer_sha256':'a'*64},
            plan={'schema':'wd.q4-prefix-build-plan.v3','fenced':True,'expected_pointer_sha256':'a'*64},
            route={'policy_sha256':'b'*64,'roster_sha256':'c'*64,'public_policy_sha256':'e'*64},expected_route_sha256='d'*64,
            policy_bytes=b'policy',roster_bytes=b'roster')
    assert not checked, 'recovery crossed job-pending gate'
    assert job.read_bytes()==b'synthetic' and all(p.read_bytes()==b'token' for p in marks)
    assert not list(private.glob('q4-status-result-*.json'))

@pytest.mark.parametrize('late',[False,True])
def test_live_recovery_wont_touch_own_marker_while_job_pending(tmp_path,monkeypatch,late):
    private,root=tmp_path/'private',tmp_path/'checkpoint'
    private.mkdir(); root.mkdir(); (private/'q4-live-update.lock').write_bytes(b'')
    job=private/JOB
    if not late: job.write_bytes(b'synthetic')
    own=private/'q4-live-update.pending.json'; own.write_bytes(b'synthetic live marker')
    pin='a'*64; calls=[]
    config={'schema':'wd.q4-live-update-once.v1','route_path':'route','route_sha256':pin,
        'plan_path':'plan','anchor_path':'anchor','policy_path':'policy','public_policy_path':'public',
        'baseline_path':'baseline','baseline_sha256':pin,'manifest_sha256':pin}
    route={'checkpoint_root':str(root),'private_state_root':str(private),'plan_sha256':pin,
        'anchor_sha256':pin,'policy_sha256':pin,'public_policy_sha256':pin,'roster_sha256':pin}
    values={'config':config,'route':route,'plan':{},'anchor':{},'policy':{},'public':{},'roster':{}}
    def read(path,*args): return json.dumps(values[str(path)]).encode()
    monkeypatch.setenv('WD_REBOOT_EXPECTED_MANIFEST_HASH',pin)
    monkeypatch.setattr(live.update,'_read',read)
    monkeypatch.setattr(live.caller,'_configuration',lambda *a: ({'bundle_root':str(tmp_path/'bundle')},None,None,None,
        {'policy_sha256':pin,'roster_sha256':pin,'public_policy_sha256':pin},{}))
    monkeypatch.setattr(live.live,'_plan',lambda *a:({'root':str(root),'policy_path':'policy',
        'public_policy_path':'public','roster_path':'roster'},None,None))
    monkeypatch.setattr(live.update,'_require_write_roots',lambda *a:None)
    monkeypatch.setattr(live.capture_adapter,'strict_pins',lambda *a:(frozenset(),{}))
    monkeypatch.setattr(live.live.pointer_io,'_chain',lambda *a:None)
    monkeypatch.setattr(live.live,'_read',lambda *a: calls.append(True) or b'{}')
    if late:
        original=prefix_private_state._checkpoint_lock
        @contextmanager
        def arrival(shared,timeout):
            with original(shared,timeout):
                job.write_bytes(b'synthetic')
                yield
        monkeypatch.setattr(prefix_private_state,'_checkpoint_lock',arrival)
    with pytest.raises((live.RecoveryOutcomeUnknown,live.RecoveryRefused)):
        live.recover(enabled=True,config_path='config',expected_config_sha256=pin,
                     expected_attempt=pin,expected_progress_sha256=pin)
    assert not calls, 'live recovery read its own marker before checking job pending'
    assert job.read_bytes()==b'synthetic' and own.read_bytes()==b'synthetic live marker'

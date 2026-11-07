"""Synthetic Windows owner-process marker reservation tests; no live bridge."""
import os
from pathlib import Path
import subprocess
import sys
import pytest

pytestmark=pytest.mark.skipif(sys.platform!='win32',reason='Windows native owner handle')

@pytest.fixture
def roots(tmp_path):
    private=tmp_path/'private'; shared=tmp_path/'shared'; private.mkdir(); shared.mkdir()
    return private,shared


def pins():
    keys=('manifest_sha256','config_sha256','runtime_sha256','host_sha256','receipt_sha256',
          'tuple_sha256','baseline_sha256','interpreter_sha256','launcher_sha256')
    return {**{key:format(i+1,'064x') for i,key in enumerate(keys)},'commit':'a'*40}


def test_owner_holds_real_marker_and_child_cannot_open_it_for_write(roots):
    from tools import wd_q4_owned_recovery_parent as parent
    private,shared=roots
    with parent.reserve(private,shared,pins()) as owner:
        marker=private/'q4-adopt-recovery-job.pending.json'
        assert marker.is_file() and owner.verify_locked(private,shared,pins())
        code="import sys; p=sys.argv[1];\ntry: open(p,'ab').close()\nexcept PermissionError: sys.exit(0)\nsys.exit(2)"
        child=subprocess.run([sys.executable,'-I','-S','-B','-c',code,str(marker)],capture_output=True,timeout=10,close_fds=True)
        assert child.returncode==0 and owner.verify_locked(private,shared,pins())
    assert marker.is_file()  # outer supervisor must explicitly reconcile it


def test_foreign_marker_cannot_be_replaced(roots):
    from tools import wd_q4_owned_recovery_parent as parent
    private,shared=roots; marker=private/'q4-adopt-recovery-job.pending.json'
    marker.write_bytes(b'foreign synthetic')
    with pytest.raises(parent.OwnerUnknown):
        with parent.reserve(private,shared,pins()): pytest.fail('foreign marker accepted')
    assert marker.read_bytes()==b'foreign synthetic'


def test_changed_bindings_are_not_owned(roots):
    from tools import wd_q4_owned_recovery_parent as parent
    private,shared=roots; original=pins()
    with parent.reserve(private,shared,original) as owner:
        changed={**original,'config_sha256':'f'*64}
        assert not owner.verify_locked(private,shared,changed)
        assert owner.verify_locked(private,shared,original)

def test_live_owner_blocks_another_reservation(roots):
    from tools import wd_q4_owned_recovery_parent as parent
    private,shared=roots
    with parent.reserve(private,shared,pins()) as owner:
        with pytest.raises(parent.OwnerUnknown):
            with parent.reserve(private,shared,pins()): pytest.fail('overlapping reservation')
        assert owner.verify_locked(private,shared,pins())

def test_busy_owner_slot_is_refused_before_marker_creation(roots):
    from sandbox.mind.step2.reader_slot import reader_slot
    from tools import wd_q4_owned_recovery_parent as parent
    private,shared=roots
    with reader_slot(private/'q4-live-update.lock',timeout_seconds=1):
        with pytest.raises(parent.OwnerRefused):
            with parent.reserve(private,shared,pins()): pytest.fail('busy slot was admitted')
    assert not (private/'q4-adopt-recovery-job.pending.json').exists()

def test_refused_exception_after_reservation_stays_unknown(roots):
    from tools import wd_q4_owned_recovery_parent as parent
    private,shared=roots
    with pytest.raises(parent.OwnerUnknown):
        with parent.reserve(private,shared,pins()): raise parent.OwnerRefused('synthetic')
    assert (private/'q4-adopt-recovery-job.pending.json').exists()

def test_proven_early_refusal_preserves_owner_for_drained_job_finalization(actual_continuation):
    import hashlib
    from tools import wd_q4_owned_recovery_parent as parent
    args={**actual_continuation, 'baseline_path':str(Path(actual_continuation['baseline_path']).with_name('missing-baseline.json'))}
    reader=Path(parent.__file__).with_name('wd_q4_owned_recovery_reader.py')
    result=parent.run_owned(**args,owner_pins=bound_pins(args),reader_sha256=hashlib.sha256(reader.read_bytes()).hexdigest())
    assert result['status']=='REFUSED'
    assert set(result)=={'status','attempt','marker_id','marker_sha256'}

def test_invalid_baseline_with_existing_adoption_stays_unknown(actual_continuation,monkeypatch):
    import hashlib
    from tests.tools.test_wd_q4_continuation_adopt_recovery import paths
    from tools import wd_q4_continuation_adopt as adopt
    from tools import wd_q4_owned_recovery_parent as parent
    args=actual_continuation; private,_=paths(args)
    with monkeypatch.context() as scoped:
        scoped.setattr(adopt.update,'_publish',lambda *_a,**_k: (_ for _ in ()).throw(KeyboardInterrupt()))
        with pytest.raises(adopt.AdoptInterrupted): adopt.run_once(**args)
    old=private/'q4-continuation-adopt.pending.json'; old_bytes=old.read_bytes()
    changed={**args,'baseline_path':str(Path(args['baseline_path']).with_name('missing-baseline.json'))}
    reader=Path(parent.__file__).with_name('wd_q4_owned_recovery_reader.py')
    with pytest.raises(parent.OwnerUnknown):
        parent.run_owned(**changed,owner_pins=bound_pins(changed),reader_sha256=hashlib.sha256(reader.read_bytes()).hexdigest())
    assert old.read_bytes()==old_bytes
    assert (private/'q4-adopt-recovery-job.pending.json').exists()

@pytest.mark.parametrize('stage,expected',[('before','NOT_ADOPTED'),('after','ADOPTED')])
def test_parent_owned_job_may_reconcile_real_adoption(actual_continuation,monkeypatch,stage,expected):
    import json
    from tests.tools.test_wd_q4_continuation_adopt_recovery import paths
    from tools import wd_q4_continuation_adopt as adopt
    from tools import wd_q4_continuation_adopt_recovery as recovery
    from tools import wd_q4_genesis_to_composer_cli as composer
    from tools import wd_q4_owned_recovery_parent as parent
    args=actual_continuation; private,generation=paths(args)
    route=composer._tuple_part(json.loads(args['genesis_tuple_bytes']),'route'); shared=Path(route['checkpoint_root'])
    original=adopt.update._publish
    def interrupt(*pos,**kw):
        if stage=='after': original(*pos,**kw)
        raise KeyboardInterrupt()
    with monkeypatch.context() as scoped:
        scoped.setattr(adopt.update,'_publish',interrupt)
        with pytest.raises(adopt.AdoptInterrupted): adopt.run_once(**args)
    with parent.reserve(private,shared,bound_pins(args)) as owner:
        assert recovery.recover_once(**args,_owner=owner,_owner_pins=bound_pins(args))=={'status':expected}
        assert owner.verify_locked(private,shared,bound_pins(args))
    assert (private/'q4-adopt-recovery-job.pending.json').exists()

from tests.tools.test_wd_q4_continuation_adopt import synthetic, actual_continuation

def bound_pins(args):
    return {**pins(), 'manifest_sha256': args['manifest_sha256'],
            'baseline_sha256': args['baseline_sha256'],
            'tuple_sha256': args['genesis_tuple_sha256'],
            'receipt_sha256': args['receipt_sha256']}

def test_bootstrap_binds_all_owner_pins():
    from tools import wd_q4_continuation_adopt_recovery_bootstrap as boot
    p=pins()
    manifest={'source_commit':p['commit'],'files':{'Invoke-WdQ4AdoptRecovery.ps1':p['launcher_sha256'].upper()}}
    config={key:p[key] for key in ('manifest_sha256','baseline_sha256','tuple_sha256','receipt_sha256')}
    runtime={'interpreter_sha256':p['interpreter_sha256']}
    assert boot._parent_pins(manifest,config,runtime,p['config_sha256'],p['runtime_sha256'],p['host_sha256'])==p

@pytest.mark.parametrize('after_reader',[False,True])
def test_parent_process_death_preserves_marker_and_blocks_retry(roots, after_reader):
    import json
    import subprocess
    import sys
    from tools import wd_q4_owned_recovery_parent as parent
    private,shared=roots
    code='''import base64, hashlib, json, pathlib, subprocess, sys
from tools import wd_q4_owned_recovery_parent as parent
with parent.reserve(pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2]), json.loads(sys.argv[3])):
 if sys.argv[4]=='after':
  reader=pathlib.Path(parent.__file__).with_name('wd_q4_owned_recovery_reader.py')
  raw=b'synthetic'; pin=hashlib.sha256(raw).hexdigest(); value=base64.b64encode(raw).decode()
  request={'schema':'wd.q4-owned-reader.v1','tuple_b64':value,'tuple_sha256':pin,'receipt_b64':value,'receipt_sha256':pin}
  result=subprocess.run([sys.executable,'-I','-S','-B',str(reader),'--enable'],input=json.dumps(request).encode(),capture_output=True)
  assert result.returncode==0 and json.loads(result.stdout)['status']=='VERIFIED'
 print('READY',flush=True)
 sys.stdin.read()
'''
    child=subprocess.Popen([sys.executable,'-B','-c',code,str(private),str(shared),json.dumps(pins()),
                            'after' if after_reader else 'before'],stdin=subprocess.PIPE,
                           stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    try:
        assert child.stdout.readline().strip()=='READY'
        marker=private/'q4-adopt-recovery-job.pending.json'; assert marker.exists()
        child.kill(); child.communicate(timeout=10)
        before=marker.read_bytes()
        with pytest.raises(parent.OwnerUnknown):
            with parent.reserve(private,shared,pins()): pytest.fail('attempt was retried')
        assert marker.read_bytes()==before
    finally:
        if child.poll() is None: child.kill()
        child.communicate(timeout=10)

def test_parent_runs_bound_reader_and_real_recovery(actual_continuation, monkeypatch):
    import hashlib
    from tools import wd_q4_continuation_adopt as adopt
    from tools import wd_q4_owned_recovery_parent as parent
    args=actual_continuation
    with monkeypatch.context() as scoped:
        scoped.setattr(adopt.update,'_publish',lambda *a,**kw: (_ for _ in ()).throw(KeyboardInterrupt()))
        with pytest.raises(adopt.AdoptInterrupted): adopt.run_once(**args)
    reader=Path(parent.__file__).with_name('wd_q4_owned_recovery_reader.py')
    result=parent.run_owned(**args, owner_pins=bound_pins(args), reader_sha256=hashlib.sha256(reader.read_bytes()).hexdigest())
    assert result['status']=='NOT_ADOPTED'
    assert set(result)=={'status','attempt','marker_id','marker_sha256'}

@pytest.mark.parametrize('change',['before','after'])
def test_changed_reader_is_refused_without_reconciling(actual_continuation, monkeypatch, tmp_path, change):
    import hashlib
    from tests.tools.test_wd_q4_continuation_adopt_recovery import paths
    from tools import wd_q4_continuation_adopt as adopt
    from tools import wd_q4_owned_recovery_parent as parent
    args=actual_continuation; private,_=paths(args)
    with monkeypatch.context() as scoped:
        scoped.setattr(adopt.update,'_publish',lambda *a,**kw: (_ for _ in ()).throw(KeyboardInterrupt()))
        with pytest.raises(adopt.AdoptInterrupted): adopt.run_once(**args)
    marker=private/'q4-continuation-adopt.pending.json'; original=marker.read_bytes()
    source=Path(parent.__file__).with_name('wd_q4_owned_recovery_reader.py')
    reader=tmp_path/'wd_q4_owned_recovery_reader.py'; reader.write_bytes(source.read_bytes())
    pin=hashlib.sha256(reader.read_bytes()).hexdigest()
    monkeypatch.setattr(parent,'__file__',str(tmp_path/'parent.py'))
    if change=='before': reader.write_bytes(reader.read_bytes()+b'\n# changed')
    else:
        run=parent.subprocess.run
        def changed(*a,**kw):
            result=run(*a,**kw); reader.write_bytes(reader.read_bytes()+b'\n# changed'); return result
        monkeypatch.setattr(parent.subprocess,'run',changed)
    with pytest.raises(parent.OwnerUnknown):
        parent.run_owned(**args,owner_pins=bound_pins(args),reader_sha256=pin)
    assert marker.read_bytes()==original
    assert (private/'q4-adopt-recovery-job.pending.json').exists() == (change=='after')

def test_matching_owner_must_still_bind_recovery_inputs(actual_continuation, monkeypatch):
    import json
    from tests.tools.test_wd_q4_continuation_adopt_recovery import paths
    from tools import wd_q4_continuation_adopt as adopt
    from tools import wd_q4_continuation_adopt_recovery as recovery
    from tools import wd_q4_owned_recovery_parent as parent
    from tools import wd_q4_genesis_to_composer_cli as composer
    args=actual_continuation; private,_=paths(args)
    shared=Path(composer._tuple_part(json.loads(args['genesis_tuple_bytes']),'route')['checkpoint_root'])
    with monkeypatch.context() as scoped:
        scoped.setattr(adopt.update,'_publish',lambda *a,**kw: (_ for _ in ()).throw(KeyboardInterrupt()))
        with pytest.raises(adopt.AdoptInterrupted): adopt.run_once(**args)
    marker=private/'q4-continuation-adopt.pending.json'; original=marker.read_bytes()
    incorrect={**bound_pins(args),'receipt_sha256':'f'*64}
    with parent.reserve(private,shared,incorrect) as owner:
        with pytest.raises(recovery.RecoveryUnknown):
            recovery.recover_once(**args,_owner=owner,_owner_pins=incorrect)
        assert marker.read_bytes()==original

@pytest.mark.parametrize('fault',['closed_after_first_check','forged_owner'])
def test_unowned_or_closed_guard_never_removes_adoption(actual_continuation,monkeypatch,fault):
    import json
    from tests.tools.test_wd_q4_continuation_adopt_recovery import paths
    from tools import wd_q4_continuation_adopt as adopt
    from tools import wd_q4_continuation_adopt_recovery as recovery
    from tools import wd_q4_genesis_to_composer_cli as composer
    from tools import wd_q4_owned_recovery_parent as parent
    args=actual_continuation; private,_=paths(args)
    shared=Path(composer._tuple_part(json.loads(args['genesis_tuple_bytes']),'route')['checkpoint_root'])
    with monkeypatch.context() as scoped:
        scoped.setattr(adopt.update,'_publish',lambda *_a,**_k: (_ for _ in ()).throw(KeyboardInterrupt()))
        with pytest.raises(adopt.AdoptInterrupted): adopt.run_once(**args)
    marker=private/'q4-continuation-adopt.pending.json'; raw=marker.read_bytes()
    with parent.reserve(private,shared,bound_pins(args)) as owner:
        if fault=='forged_owner':
            class Fake:
                def verify_locked(self,*_a): return True
            supplied=Fake()
        else:
            supplied=owner; original=parent._Owner.verify_locked; calls=[]
            def verify_once(self,*pos):
                calls.append(1)
                if len(calls)==2: self.close()
                return original(self,*pos)
            monkeypatch.setattr(parent._Owner,'verify_locked',verify_once)
        with pytest.raises(recovery.RecoveryUnknown): recovery.recover_once(**args,_owner=supplied,_owner_pins=bound_pins(args))
        assert marker.read_bytes()==raw and (private/'q4-adopt-recovery-job.pending.json').exists()

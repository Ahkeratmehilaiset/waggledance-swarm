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

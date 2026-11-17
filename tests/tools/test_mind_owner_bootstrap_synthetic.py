"""First-use model tests: no live marker, processes, or production clearance."""
import importlib.util
import os
from pathlib import Path
import pytest
from tools import wd_mind_runtime_child as child
spec=importlib.util.spec_from_file_location('owner_bootstrap_model',Path(__file__).with_name('mind_owner_bootstrap_synthetic.py'))
model=importlib.util.module_from_spec(spec);spec.loader.exec_module(model)
pytestmark=pytest.mark.skipif(os.name!='nt',reason='Windows reservation guard')


def test_default_off_creates_nothing(tmp_path):
    assert model.create_reserved(tmp_path)=='DISABLED'
    assert not (tmp_path/model.NAME).exists()


def test_new_reserved_is_crash_persistent_hold_for_real_guard(tmp_path):
    assert model.create_reserved(tmp_path,enabled=True)=='RESERVED_LEGACY_UNKNOWN'
    marker=tmp_path/model.NAME
    assert marker.read_bytes()==model.MAGIC+b'R'
    with pytest.raises(child.LaunchRefused,match='owner_reservation_hold'):
        child._reserve_local_owner(tmp_path)
    assert marker.read_bytes()==model.MAGIC+b'R'


@pytest.mark.parametrize('data',[b'',b'corrupt',model.MAGIC+b'R',model.MAGIC+b'C'])
def test_existing_or_torn_marker_is_never_overwritten(tmp_path,data):
    marker=tmp_path/model.NAME;marker.write_bytes(data)
    assert model.create_reserved(tmp_path,enabled=True)=='HOLD_CREATE_OR_WRITE'
    assert marker.read_bytes()==data


def test_outside_audit_root_refuses_before_creation():
    assert model.create_reserved(Path(r'C:\Python'),enabled=True)=='HOLD_INVALID_ROOT'


@pytest.mark.parametrize('options,expected',[
    ({},'HOLD_OLD_LAUNCH_PATH_OPEN'),
    ({'legacy_launch_disabled':True},'HOLD_OWNER_INVENTORY_UNKNOWN'),
    ({'legacy_launch_disabled':True,'inventory_complete':True,'live_count':1},'HOLD_OWNER_PRESENT'),
    ({'legacy_launch_disabled':True,'inventory_complete':True,'live_count':0},'MODEL_ONLY_ALL_PRECONDITIONS'),
])
def test_synthetic_previous_owner_conditions_are_independent(options,expected):
    world=model.SyntheticLegacyWorld(**options)
    assert world.evaluate_under_fence()==expected


def test_model_gate_contention_is_hold_and_never_proves_absence():
    world=model.SyntheticLegacyWorld(legacy_launch_disabled=True,inventory_complete=True)
    assert world.lock.acquire(False)
    try:assert world.evaluate_under_fence()=='HOLD_GATE_BUSY'
    finally:world.lock.release()


def test_existing_reparse_marker_refuses_without_touching_target(tmp_path):
    target=tmp_path/'target';target.write_bytes(b'synthetic')
    marker=tmp_path/model.NAME
    try:os.symlink(target,marker)
    except OSError:pytest.skip('Windows symlink permission unavailable')
    assert model.create_reserved(tmp_path,enabled=True)=='HOLD_CREATE_OR_WRITE'
    assert marker.is_symlink() and target.read_bytes()==b'synthetic'

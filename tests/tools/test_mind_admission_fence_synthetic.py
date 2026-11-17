"""Synthetic wiring only; actual legacy process exclusion remains UNKNOWN."""
import importlib.util
import os
from pathlib import Path
import threading
import pytest

spec=importlib.util.spec_from_file_location('admission_fence_model',Path(__file__).with_name('mind_admission_fence_synthetic.py'))
model=importlib.util.module_from_spec(spec);spec.loader.exec_module(model)
pytestmark=pytest.mark.skipif(os.name!='nt',reason='Windows existing owner reservation')


@pytest.fixture
def seeded(tmp_path):
    # Test-only forced seed; not a real first-use proof or a live marker.
    marker=tmp_path/model.bootstrap.NAME
    marker.write_bytes(model.bootstrap.MAGIC+b'C')
    return model.SyntheticFence(tmp_path,enabled=True),marker


def test_first_use_unknown_stays_reserved_and_all_routes_refuse(tmp_path):
    fence=model.SyntheticFence(tmp_path,enabled=True);called=[]
    assert fence.first_use()=='RESERVED_LEGACY_UNKNOWN'
    marker=tmp_path/model.bootstrap.NAME
    for route in ('cli_owner','cli_query','public_factory','worker_start'):
        assert getattr(fence,route)(lambda lease:called.append(route))=='HOLD'
    assert called==[] and marker.read_bytes()==model.bootstrap.MAGIC+b'R'


@pytest.mark.parametrize('route',['cli_owner','cli_query','public_factory','worker_start'])
def test_each_named_synthetic_route_takes_same_reservation(seeded,route):
    fence,marker=seeded;inside=[]
    def action(lease):
        inside.append(lease.marker_state())
        return model.CLEAN
    assert getattr(fence,route)(action)=='STOPPED'
    assert inside==[model.bootstrap.MAGIC+b'R']
    assert marker.read_bytes()==model.bootstrap.MAGIC+b'C'


def test_nested_factory_and_worker_reuse_only_the_active_admission(seeded):
    fence,marker=seeded;calls=[]
    def worker(lease):calls.append(('worker',lease.marker_state()));return model.CLEAN
    def factory(lease):
        calls.append(('factory',lease.marker_state()))
        assert fence.nested(lease,'worker_start',worker)=='STOPPED'
        return model.CLEAN
    def cli(lease):
        assert fence.nested(lease,'public_factory',factory)=='STOPPED'
        return model.CLEAN
    assert fence.cli_owner(cli)=='STOPPED'
    assert calls==[('factory',model.bootstrap.MAGIC+b'R'),('worker',model.bootstrap.MAGIC+b'R')]


def test_unproven_closure_keeps_reservation_and_blocks_other_routes(seeded):
    fence,marker=seeded
    assert fence.cli_owner(lambda lease:None)=='HOLD'
    assert marker.read_bytes()==model.bootstrap.MAGIC+b'R'
    assert fence.public_factory(lambda lease:model.CLEAN)=='HOLD'


def test_forged_nested_lease_never_runs_callback(seeded):
    fence,marker=seeded;called=[]
    assert fence.nested(object(),'public_factory',lambda lease:called.append(True))=='REFUSED'
    assert called==[] and marker.read_bytes()==model.bootstrap.MAGIC+b'C'


def test_distinct_fence_instances_contend_on_same_os_marker(seeded):
    first,marker=seeded;second=model.SyntheticFence(first.root,enabled=True)
    entered,release=threading.Event(),threading.Event();out=[];inside=[]
    def work(lease):
        inside.append(lease.marker_state());entered.set();assert release.wait(5);return model.CLEAN
    thread=threading.Thread(target=lambda:out.append(first.cli_owner(work)))
    thread.start()
    try:
        assert entered.wait(3)
        assert second.public_factory(lambda lease:model.CLEAN)=='HOLD'
        assert inside==[model.bootstrap.MAGIC+b'R']
    finally:release.set();thread.join(3)
    assert not thread.is_alive() and out==['STOPPED']


def test_nested_refusal_cannot_be_hidden_by_outer_clean_result(seeded):
    fence,marker=seeded
    def action(lease):
        assert fence.nested(lease,'cli_owner',lambda nested:model.CLEAN)=='REFUSED'
        return model.CLEAN
    assert fence.cli_owner(action)=='HOLD'
    assert marker.read_bytes()==model.bootstrap.MAGIC+b'R'

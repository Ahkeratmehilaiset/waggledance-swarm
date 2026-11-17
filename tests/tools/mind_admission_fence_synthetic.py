"""Synthetic admission-fence model, never a production entrypoint.

Only test-created audit roots are accepted.  The known public/CLI routes below
are fake adapters, not wrappers around production code.  CLEAN is a test double,
not native process-exit evidence. Old interpreters and arbitrary Python calls
are not controlled by this module. No live CLEAR is created here.
"""
import importlib.util
from pathlib import Path

from tools import wd_mind_runtime_child as child

_spec=importlib.util.spec_from_file_location('synthetic_bootstrap_for_admission',
    Path(__file__).with_name('mind_owner_bootstrap_synthetic.py'))
bootstrap=importlib.util.module_from_spec(_spec);_spec.loader.exec_module(bootstrap)
CLEAN=object()
_ROUTES=frozenset(('cli_owner','cli_query','public_factory','worker_start'))
_NESTED=frozenset(('public_factory','worker_start'))


class _Lease:
    __slots__=('fence','active','broken','_guard')
    def __init__(self,fence,guard):self.fence,self.active,self.broken,self._guard=fence,True,False,guard
    def marker_state(self):
        if not self.active:raise ValueError('inactive')
        self._guard._check()
        return self._guard._read()


class SyntheticFence:
    def __init__(self,root,*,enabled=False):
        if type(enabled) is not bool:raise ValueError('enabled')
        self.root=bootstrap._synthetic_root(root);self.enabled=enabled

    def first_use(self):
        # The real legacy gate/inventory is not implemented. RESERVED is HOLD.
        return bootstrap.create_reserved(self.root,enabled=self.enabled)

    def enter(self,route,callback):
        if not self.enabled or route not in _ROUTES or not callable(callback):return 'REFUSED'
        guard=None;lease=None
        try:
            guard=child._reserve_local_owner(self.root)
            lease=_Lease(self,guard)
            outcome=callback(lease)
            if outcome is not CLEAN or lease.broken:return 'HOLD'
            # Synthetic callback result stands in for real native service/owner
            # closure; actual proof must independently inspect owned handles.
            guard.release_clean(service_stopped=True,owner_stopped=True,
                                owner_owned=False,control_stopped=True)
            return 'STOPPED'
        except BaseException:
            return 'HOLD'
        finally:
            if lease is not None:lease.active=False
            if guard is not None:guard.abandon()

    def nested(self,lease,route,callback):
        if (type(lease) is not _Lease or lease.fence is not self or not lease.active
                or route not in _NESTED or not callable(callback)):
            if type(lease) is _Lease:lease.broken=True
            return 'REFUSED'
        try:
            outcome=callback(lease)
            if outcome is not CLEAN:lease.broken=True;return 'HOLD'
            return 'STOPPED'
        except BaseException:
            lease.broken=True;return 'HOLD'

    def cli_owner(self,callback):return self.enter('cli_owner',callback)
    def cli_query(self,callback):return self.enter('cli_query',callback)
    def public_factory(self,callback):return self.enter('public_factory',callback)
    def worker_start(self,callback):return self.enter('worker_start',callback)

"""Synthetic first-use model: creates only a RESERVED marker under audit.

This is not a production bootstrap and cannot create CLEAR.  The in-memory
legacy evidence model does not inspect real processes or disable real launchers.
"""
import os
from pathlib import Path
import threading

MAGIC=b'WD_MIND_OWNER_V1\n'
NAME='mind-owner-reservation.v1'
BASE=Path(r'C:\Python\project2\.codex-audit')


def _synthetic_root(root):
    if os.name!='nt' or type(root) is not Path and not isinstance(root,Path):raise ValueError('synthetic_root')
    root=Path(root)
    if not root.is_absolute() or root.drive.upper()!='C:' or '..' in root.parts:raise ValueError('synthetic_root')
    if os.path.commonpath((str(BASE),str(root))).casefold()!=str(BASE).casefold() or root==BASE:
        raise ValueError('synthetic_root')
    from tools import wd_mind_absent_generation_prefix_readonly as absent
    absent._plain(root,directory=True)
    return root


def create_reserved(root,*,enabled=False):
    """CREATE_NEW -> RESERVED only. Existing/corrupt entries are never modified."""
    if enabled is not True:return 'DISABLED'
    try:root=_synthetic_root(root)
    except Exception:return 'HOLD_INVALID_ROOT'
    marker=root/NAME;fd=None
    try:
        fd=os.open(marker,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_BINARY|os.O_NOINHERIT,0o600)
        opened=os.fstat(fd);current=os.lstat(marker)
        if (not opened.st_ino or opened.st_nlink!=1 or current.st_nlink!=1
                or (opened.st_dev,opened.st_ino)!=(current.st_dev,current.st_ino)):
            return 'HOLD_IDENTITY'
        raw=MAGIC+b'R'
        if os.write(fd,raw)!=len(raw):return 'HOLD_WRITE'
        os.fsync(fd)
        current=os.lstat(marker);opened=os.fstat(fd)
        if ((opened.st_dev,opened.st_ino)!=(current.st_dev,current.st_ino)
                or opened.st_size!=len(raw) or current.st_size!=len(raw)):
            return 'HOLD_IDENTITY'
        return 'RESERVED_LEGACY_UNKNOWN'
    except Exception:return 'HOLD_CREATE_OR_WRITE'
    finally:
        if fd is not None:os.close(fd)


class SyntheticLegacyWorld:
    """A test double, not an OS inventory or a real launcher interlock."""
    def __init__(self,*,legacy_launch_disabled=False,inventory_complete=False,live_count=0):
        self.lock=threading.Lock();self.legacy_launch_disabled=legacy_launch_disabled
        self.inventory_complete=inventory_complete;self.live_count=live_count
    def evaluate_under_fence(self):
        if not self.lock.acquire(False):return 'HOLD_GATE_BUSY'
        try:
            if not self.legacy_launch_disabled:return 'HOLD_OLD_LAUNCH_PATH_OPEN'
            if not self.inventory_complete:return 'HOLD_OWNER_INVENTORY_UNKNOWN'
            if self.live_count!=0:return 'HOLD_OWNER_PRESENT'
            # Only a model outcome. There is intentionally no disk CLEAR path.
            return 'MODEL_ONLY_ALL_PRECONDITIONS'
        finally:self.lock.release()

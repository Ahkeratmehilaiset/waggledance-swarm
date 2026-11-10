"""Synthetic tests for file changes after a pinned bootstrap read."""
import hashlib
from pathlib import Path

import pytest

from tools import wd_q4_lane_caller_runtime_bootstrap as bootstrap


def test_unchanged_file_returns_its_pinned_bytes(tmp_path):
    path = tmp_path / 'payload.bin'
    data = b'approved-synthetic-data'
    path.write_bytes(data)
    assert bootstrap._read(path, hashlib.sha256(data).hexdigest(), limit=128) == data


def test_append_after_read_before_poststat_refuses(tmp_path, monkeypatch):
    path = tmp_path / 'payload.bin'
    data = b'approved-synthetic-data'
    path.write_bytes(data)
    original_open = Path.open

    class AppendAfterRead:
        def __init__(self, target, args, kwargs):
            self.target = target
            self.args = args
            self.kwargs = kwargs

        def __enter__(self):
            self.stream = original_open(self.target, *self.args, **self.kwargs)
            return self

        def __exit__(self, *args):
            return self.stream.__exit__(*args)

        def fileno(self):
            return self.stream.fileno()

        def read(self, count):
            result = self.stream.read(count)
            with original_open(self.target, 'ab') as writer:
                writer.write(b'X')
            return result

    def intercepted(self, *args, **kwargs):
        if self == path and args and args[0] == 'rb':
            return AppendAfterRead(self, args, kwargs)
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, 'open', intercepted)
    with pytest.raises(ValueError):
        bootstrap._read(path, hashlib.sha256(data).hexdigest(), limit=128)
    with original_open(path, 'rb') as stream:
        assert stream.read() == data + b'X'

def test_same_size_rewrite_after_read_with_changed_mtime_refuses(tmp_path, monkeypatch):
    import os

    path = tmp_path / 'payload.bin'
    data = b'approved-synthetic-data'
    changed = b'X' * len(data)
    path.write_bytes(data)
    initial = path.stat()
    original_open = Path.open

    class RewriteAfterRead:
        def __init__(self, target, args, kwargs):
            self.target, self.args, self.kwargs = target, args, kwargs

        def __enter__(self):
            self.stream = original_open(self.target, *self.args, **self.kwargs)
            return self

        def __exit__(self, *args):
            return self.stream.__exit__(*args)

        def fileno(self):
            return self.stream.fileno()

        def read(self, count):
            result = self.stream.read(count)
            with original_open(self.target, 'r+b') as writer:
                writer.write(changed)
            os.utime(self.target, ns=(initial.st_atime_ns, initial.st_mtime_ns + 10_000_000_000))
            return result

    def intercepted(self, *args, **kwargs):
        if self == path and args and args[0] == 'rb':
            return RewriteAfterRead(self, args, kwargs)
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, 'open', intercepted)
    with pytest.raises(ValueError):
        bootstrap._read(path, hashlib.sha256(data).hexdigest(), limit=128)
    with original_open(path, 'rb') as stream:
        assert stream.read() == changed
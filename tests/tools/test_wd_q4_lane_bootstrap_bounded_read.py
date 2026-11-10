"""Synthetic checks of lane bootstrap read bounds; no live session."""
import hashlib
from pathlib import Path

import pytest

from tools import wd_q4_lane_caller_runtime_bootstrap as bootstrap


@pytest.mark.parametrize('data,limit', [(b'', 64), (b'abc', 16 * 1024 * 1024), (b'abcd', 4)])
def test_read_requests_observed_size_plus_sentinel(tmp_path, monkeypatch, data, limit):
    path = tmp_path / 'input.bin'
    path.write_bytes(data)
    original_open = Path.open
    requested = []

    class ObservedStream:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            self.stream.__enter__()
            return self

        def __exit__(self, *args):
            return self.stream.__exit__(*args)

        def fileno(self):
            return self.stream.fileno()

        def read(self, size):
            requested.append(size)
            return self.stream.read(size)

    def observed_open(candidate, *args, **kwargs):
        stream = original_open(candidate, *args, **kwargs)
        return ObservedStream(stream) if candidate == path and args == ('rb',) else stream

    monkeypatch.setattr(Path, 'open', observed_open)
    assert bootstrap._read(path, hashlib.sha256(data).hexdigest(), limit) == data
    assert requested == [len(data) + 1]


def test_read_refuses_wrong_digest_and_over_limit(tmp_path):
    path = tmp_path / 'input.bin'
    path.write_bytes(b'abc')
    with pytest.raises(ValueError):
        bootstrap._read(path, '0' * 64, 64)
    with pytest.raises(ValueError):
        bootstrap._read(path, hashlib.sha256(b'abc').hexdigest(), 2)


def test_read_refuses_growth_before_read(tmp_path, monkeypatch):
    path = tmp_path / 'input.bin'
    path.write_bytes(b'abc')
    original_open = Path.open

    class GrowingStream:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            self.stream.__enter__()
            return self

        def __exit__(self, *args):
            return self.stream.__exit__(*args)

        def fileno(self):
            return self.stream.fileno()

        def read(self, size):
            with original_open(path, 'ab') as writer:
                writer.write(b'd')
            return self.stream.read(size)

    def growing_open(candidate, *args, **kwargs):
        stream = original_open(candidate, *args, **kwargs)
        return GrowingStream(stream) if candidate == path and args == ('rb',) else stream

    monkeypatch.setattr(Path, 'open', growing_open)
    with pytest.raises(ValueError):
        bootstrap._read(path, hashlib.sha256(b'abc').hexdigest(), 64)

"""Synthetic checks of the startup caller's pinned file reader."""
import hashlib
from pathlib import Path

import pytest

from tools import wd_q4_lane_caller_cli as caller


def digest(data):
    return hashlib.sha256(data).hexdigest()


@pytest.mark.parametrize('data,limit', [(b'a', 64), (b'abc', 16 * 1024 * 1024), (b'abcd', 4)])
def test_read_requests_size_plus_sentinel(tmp_path, monkeypatch, data, limit):
    path = tmp_path / 'input.bin'
    path.write_bytes(data)
    original_open = Path.open
    requested = []

    class Probe:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            self.stream.__enter__()
            return self

        def __exit__(self, *args):
            return self.stream.__exit__(*args)

        def read(self, size):
            requested.append(size)
            return self.stream.read(size)

    def observed_open(candidate, *args, **kwargs):
        stream = original_open(candidate, *args, **kwargs)
        return Probe(stream) if candidate == path and args == ('rb',) else stream

    monkeypatch.setattr(Path, 'open', observed_open)
    assert caller._read(str(path), limit, digest(data)) == data
    assert requested == [len(data) + 1]


@pytest.mark.parametrize('changed', [b'abcd', b'a'])
def test_read_rejects_size_change_even_if_new_bytes_match_pin(tmp_path, monkeypatch, changed):
    path = tmp_path / 'input.bin'
    path.write_bytes(b'abc')
    original_open = Path.open

    class ChangedBeforeRead:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            self.stream.__enter__()
            return self

        def __exit__(self, *args):
            return self.stream.__exit__(*args)

        def read(self, size):
            with original_open(path, 'wb') as writer:
                writer.write(changed)
            return self.stream.read(size)

    def observed_open(candidate, *args, **kwargs):
        stream = original_open(candidate, *args, **kwargs)
        return ChangedBeforeRead(stream) if candidate == path and args == ('rb',) else stream

    monkeypatch.setattr(Path, 'open', observed_open)
    with pytest.raises(ValueError):
        caller._read(str(path), 64, digest(changed))


def test_read_refuses_wrong_pin_and_over_limit(tmp_path):
    path = tmp_path / 'input.bin'
    path.write_bytes(b'abc')
    with pytest.raises(ValueError):
        caller._read(str(path), 64, '0' * 64)
    with pytest.raises(ValueError):
        caller._read(str(path), 2, digest(b'abc'))

"""FAISS-free synthetic tests for the actual E5 weight-identity byte budget."""
import builtins
import hashlib
import io

import pytest

from sandbox.mind.step2 import encode


def _weights(tmp_path, data):
    root = tmp_path / "synthetic-model"
    root.mkdir()
    (root / "model.safetensors").write_bytes(data)
    return root


def test_default_identity_preserves_existing_weights_digest_and_size(tmp_path):
    data = b"synthetic-weights" * 11
    root = _weights(tmp_path, data)
    result = encode.model_identity(model_dir=root, revision="synthetic-revision")
    assert result == {"model": encode.MODEL, "revision": "synthetic-revision",
                      "weights_sha256": hashlib.sha256(data).hexdigest(), "weights_bytes": len(data)}


@pytest.mark.parametrize("size", [0, 1, 63, 64])
def test_bounded_identity_accepts_through_exact_limit(tmp_path, size):
    data = b"x" * size
    root = _weights(tmp_path, data)
    result = encode.model_identity(model_dir=root, revision="synthetic-revision", max_bytes=64)
    assert result["weights_sha256"] == hashlib.sha256(data).hexdigest()
    assert result["weights_bytes"] == size


@pytest.mark.parametrize("size", [65, 4096])
def test_over_limit_refuses_without_reading_past_limit_plus_one(tmp_path, monkeypatch, size):
    root = _weights(tmp_path, b"x" * size)
    target = root / "model.safetensors"
    real_open = builtins.open
    returned, requested = [], []

    class Counting:
        def __init__(self, handle):
            self.handle = handle

        def read(self, size=-1):
            requested.append(size)
            block = self.handle.read(size)
            returned.append(len(block))
            return block

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self.handle.close()

    def guarded_open(path, *args, **kwargs):
        handle = real_open(path, *args, **kwargs)
        return Counting(handle) if path == target else handle

    monkeypatch.setattr(builtins, "open", guarded_open)
    with pytest.raises(ValueError, match="^weights_over_budget$"):
        encode.model_identity(model_dir=root, revision="synthetic-revision", max_bytes=64)
    assert requested and all(0 < n <= 65 for n in requested)
    assert sum(returned) == 65


def test_growth_between_reads_is_refused_at_limit_plus_one(tmp_path, monkeypatch):
    root = _weights(tmp_path, b"x" * 64)
    target = root / "model.safetensors"
    real_open = builtins.open
    returned, requested = [], []

    class SyntheticGrowingStream:
        def __init__(self):
            self.stream = io.BytesIO(b"x" * 64)
            self.calls = 0

        def read(self, size=-1):
            requested.append(size)
            if self.calls == 1:
                position = self.stream.tell()
                self.stream.seek(0, io.SEEK_END)
                self.stream.write(b"more synthetic data")
                self.stream.seek(position)
            self.calls += 1
            block = self.stream.read(size)
            returned.append(len(block))
            return block

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self.stream.close()

    def guarded_open(path, *args, **kwargs):
        if path == target:
            return SyntheticGrowingStream()
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", guarded_open)
    with pytest.raises(ValueError, match="^weights_over_budget$"):
        encode.model_identity(model_dir=root, revision="synthetic-revision", max_bytes=64)
    assert requested == [65, 1]
    assert returned == [64, 1]


@pytest.mark.parametrize("invalid", [0, -1, True, False, 1.0, "64", [], {}])
def test_invalid_budget_refuses_before_any_open(tmp_path, monkeypatch, invalid):
    root = _weights(tmp_path, b"x")
    monkeypatch.setattr(builtins, "open", lambda *args, **kwargs: pytest.fail("unexpected file open"))
    with pytest.raises(ValueError, match="^max_bytes_invalid$"):
        encode.model_identity(model_dir=root, revision="synthetic-revision", max_bytes=invalid)

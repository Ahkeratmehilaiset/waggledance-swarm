"""Synthetic isolated-cache checks: no model, installer, or live runtime."""
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from tools import wd_mind_runtime_bootstrap as bootstrap
from tools import wd_mind_runtime_child as child


def test_child_builds_private_environment_and_ignores_inherited_cache(monkeypatch, tmp_path):
    root = str(tmp_path / "runtime")
    monkeypatch.setenv("TORCHINDUCTOR_CACHE_DIR", "caller-controlled")
    env = child._worker_environment(root)
    assert env["TORCHINDUCTOR_CACHE_DIR"] == root
    assert os.environ["TORCHINDUCTOR_CACHE_DIR"] == "caller-controlled"
    assert env["TEMP"] == env["TMP"] == os.devnull
    assert env["HF_HUB_OFFLINE"] == env["TRANSFORMERS_OFFLINE"] == "1"
    for name in ("HF_HOME", "HF_HUB_CACHE", "HUGGINGFACE_HUB_CACHE", "TRANSFORMERS_CACHE",
                 "HF_ASSETS_CACHE", "SENTENCE_TRANSFORMERS_HOME", "TORCH_HOME", "XDG_CACHE_HOME", "CUDA_CACHE_PATH"):
        assert env[name] == os.devnull


@pytest.mark.parametrize("root", ["relative", "", "C:relative"])
def test_child_rejects_relative_roots(root):
    with pytest.raises((ValueError, child.LaunchRefused)):
        child._worker_environment(root)


def _argv(root):
    values = {"runtime-root": root, "runtime-manifest": str(Path(root).parent / "manifest.json"),
              "runtime-manifest-sha256": "a" * 64, "runtime-generation": "b" * 40,
              "runtime-revision": "c" * 40, "mutex-sha256": "e" * 64,
              "memory": "x", "descriptor": "y", "descriptor-sha256": "d" * 64,
              "lock-file": "z", "lock-timeout": "1"}
    return [item for k, v in values.items() for item in ("--" + k, str(v))]


@pytest.mark.parametrize("kind", ["exact", "missing", "wrong", "alias", "verifier_refuses"])
def test_bootstrap_verifies_before_import_and_rejects_bad_cache(tmp_path, monkeypatch, kind):
    root = tmp_path / "runtime"
    for path in (root / "site", root / "model", root / "assets/code/sandbox/mind/step2"):
        path.mkdir(parents=True)
    (root / "assets/code/sandbox/mind/step2/wd_q4_private_read.py").write_text("# synthetic")
    fake = SimpleNamespace(flags=SimpleNamespace(isolated=True, no_site=True), dont_write_bytecode=True,
                           path=[], modules={}, argv=[])
    monkeypatch.setattr(bootstrap, "sys", fake)
    calls = []
    def verify(*args, **kwargs):
        calls.append("verify")
        if kind == "verifier_refuses":
            raise ValueError("synthetic_reparse")
    monkeypatch.setattr(bootstrap, "_load_verifier", lambda: verify)
    monkeypatch.setattr(bootstrap, "_install_reader_mutex", lambda *a: calls.append("mutex"))
    monkeypatch.setattr(bootstrap, "_disable_python_temporary_files", lambda: None)
    monkeypatch.setattr(bootstrap.runpy, "run_module", lambda *a, **k: calls.append("dispatch"))
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "1")
    for name in ("TEMP", "TMP", "HF_HOME", "HF_HUB_CACHE", "HUGGINGFACE_HUB_CACHE", "TRANSFORMERS_CACHE",
                 "HF_ASSETS_CACHE", "SENTENCE_TRANSFORMERS_HOME", "TORCH_HOME", "XDG_CACHE_HOME", "CUDA_CACHE_PATH"):
        monkeypatch.setenv(name, os.devnull)
    monkeypatch.delenv("TORCHINDUCTOR_CACHE_DIR", raising=False)
    root_arg = str(root)
    if kind == "alias":
        root_arg = str(root.parent / "other" / ".." / root.name)
    if kind != "missing":
        monkeypatch.setenv("TORCHINDUCTOR_CACHE_DIR", "wrong" if kind == "wrong" else root_arg)
    result = bootstrap.main(_argv(root_arg))
    if kind == "exact":
        assert result == 0 and calls == ["verify", "mutex", "dispatch"]
    else:
        assert result == 2 and calls == ["verify"] and not fake.path


def test_runtime_root_write_breaks_later_manifest_verification(tmp_path):
    import hashlib
    import json
    from tools import wd_mind_runtime_manifest as manifest
    root = tmp_path / "runtime"
    (root / "site").mkdir(parents=True)
    data = b"synthetic"
    (root / "site" / "module.py").write_bytes(data)
    document = {"schema": "wd.q4-runtime-manifest.v1", "generation": "a" * 40, "revision": "b" * 40,
                "files": [{"path": "site/module.py", "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}]}
    raw = json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
    file = tmp_path / "manifest.json"
    file.write_bytes(raw)
    kwargs = dict(expected_generation="a" * 40, expected_revision="b" * 40)
    pin = hashlib.sha256(raw).hexdigest()
    manifest.verify_runtime(root, file, pin, **kwargs)
    (root / "synthetic-cache-marker").write_bytes(b"cache")
    with pytest.raises(ValueError):
        manifest.verify_runtime(root, file, pin, **kwargs)


def test_bootstrap_reparse_check_fails_before_runtime_dispatch(tmp_path, monkeypatch):
    from tools import wd_mind_runtime_manifest as manifest
    root = tmp_path / "runtime"
    root.mkdir()
    root_stat = os.lstat(root)
    real_check = manifest._is_reparse
    monkeypatch.setattr(manifest, "_is_reparse", lambda info: real_check(info) or
                        (info.st_dev, info.st_ino) == (root_stat.st_dev, root_stat.st_ino))
    fake = SimpleNamespace(flags=SimpleNamespace(isolated=True, no_site=True), dont_write_bytecode=True,
                           path=[], modules={}, argv=[])
    monkeypatch.setattr(bootstrap, "sys", fake)
    monkeypatch.setattr(bootstrap, "_load_verifier", lambda: manifest.verify_runtime)
    monkeypatch.setenv("TORCHINDUCTOR_CACHE_DIR", str(root))
    monkeypatch.setattr(bootstrap.runpy, "run_module", lambda *a, **k: pytest.fail("runtime dispatch"))
    assert bootstrap.main(_argv(str(root))) == 2
    assert fake.path == []


@pytest.mark.parametrize("suffix", [os.sep, os.sep + ".", os.sep * 2 + "child"])
def test_worker_environment_rejects_noncanonical_root_before_spawn(tmp_path, suffix):
    root = str(tmp_path / "runtime") + suffix
    assert root != str(Path(root))
    with pytest.raises((ValueError, child.LaunchRefused)):
        child._worker_environment(root)


@pytest.mark.skipif(os.name != "nt", reason="Windows path separator semantics")
def test_worker_environment_rejects_windows_forward_slash_root(tmp_path):
    root = str(tmp_path / "runtime").replace("\\", "/")
    assert root != str(Path(root))
    with pytest.raises((ValueError, child.LaunchRefused)):
        child._worker_environment(root)

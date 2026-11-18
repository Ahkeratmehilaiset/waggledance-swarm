"""Synthetic bootstrap sequencing tests; never import or run a real model."""
import json
import hashlib
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace

import pytest

from tools import wd_mind_runtime_bootstrap as bootstrap


@pytest.fixture(autouse=True)
def _restore_cache_env(monkeypatch):
    monkeypatch.delenv("TORCHINDUCTOR_CACHE_DIR", raising=False)


def _args(root):
    os.environ["TORCHINDUCTOR_CACHE_DIR"] = str(root)
    values = {"runtime-root": str(root), "runtime-manifest": str(root.parent / "manifest.json"),
              "runtime-manifest-sha256": "a" * 64, "runtime-generation": "b" * 40,
              "runtime-revision": "c" * 40, "mutex-sha256": "e" * 64, "memory": str(root.parent / "memory"),
              "descriptor": str(root.parent / "descriptor.json"), "descriptor-sha256": "d" * 64,
              "lock-file": str(root.parent / "lock"), "lock-timeout": "1"}
    return [item for key, value in values.items() for item in ("--" + key, value)]


def _isolated(monkeypatch):
    _offline(monkeypatch)
    flags = SimpleNamespace(isolated=True, no_site=True)
    fake_sys = SimpleNamespace(flags=flags, dont_write_bytecode=True, path=[], modules={}, argv=[])
    monkeypatch.setattr(bootstrap, "sys", fake_sys)
    return fake_sys


def _offline(monkeypatch):
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "1")
    for name in ("TEMP", "TMP", "HF_HOME", "HF_HUB_CACHE", "HUGGINGFACE_HUB_CACHE", "TRANSFORMERS_CACHE",
                 "HF_ASSETS_CACHE", "SENTENCE_TRANSFORMERS_HOME", "TORCH_HOME", "XDG_CACHE_HOME", "CUDA_CACHE_PATH"):
        monkeypatch.setenv(name, os.devnull)
    monkeypatch.setattr(tempfile, "tempdir", tempfile.tempdir)
    for name in ("mkstemp", "mkdtemp", "NamedTemporaryFile", "TemporaryFile", "SpooledTemporaryFile", "TemporaryDirectory"):
        monkeypatch.setattr(tempfile, name, getattr(tempfile, name))


def test_missing_isolation_refuses_without_verifier_or_import(monkeypatch, capsys):
    monkeypatch.setattr(bootstrap, "sys", SimpleNamespace(flags=SimpleNamespace(isolated=False, no_site=True),
                                                         dont_write_bytecode=True))
    monkeypatch.setattr(bootstrap, "_load_verifier", lambda: (_ for _ in ()).throw(AssertionError("called")))
    assert bootstrap.main([]) == 2
    assert json.loads(capsys.readouterr().out)["reason"] == "isolation_required"


def test_runtime_verification_failure_precedes_any_runtime_import(tmp_path, monkeypatch, capsys):
    _isolated(monkeypatch)
    monkeypatch.setattr(bootstrap, "_load_verifier", lambda: lambda *a, **k: (_ for _ in ()).throw(
        ValueError("synthetic-private")))
    monkeypatch.setattr(bootstrap.runpy, "run_module", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("unverified import")))
    assert bootstrap.main(_args(tmp_path / "runtime")) == 2
    captured = capsys.readouterr()
    assert json.loads(captured.out)["reason"] == "runtime_verification_failed" and "synthetic-private" not in captured.out


def test_only_verified_synthetic_tree_can_reach_reader_dispatch(tmp_path, monkeypatch):
    fake_sys = _isolated(monkeypatch)
    root = tmp_path / "runtime"
    for path in (root / "site", root / "model", root / "assets/code/sandbox/mind/step2"):
        path.mkdir(parents=True)
    (root / "assets/code/sandbox/mind/step2/wd_q4_private_read.py").write_text("# synthetic only")
    calls = []
    monkeypatch.setattr(bootstrap, "_load_verifier", lambda: lambda *a, **k: calls.append("verify"))
    monkeypatch.setattr(bootstrap, "_install_reader_mutex", lambda *a, **k: calls.append("mutex"))
    def dispatch(*args, **kwargs):
        calls.append("dispatch")
        raise SystemExit(2)
    monkeypatch.setattr(bootstrap.runpy, "run_module", dispatch)
    assert bootstrap.main(_args(root)) == 2
    assert calls == ["verify", "mutex", "dispatch"]
    assert fake_sys.path[0] == str(root / "assets/code") and fake_sys.path[-1] == str(root / "site")


def test_verified_mutex_shim_replaces_legacy_slot_before_dispatch(tmp_path, monkeypatch):
    fake_sys = _isolated(monkeypatch)
    path = tmp_path / "wd_mind_runtime_mutex.py"
    path.write_bytes((Path(__file__).resolve().parents[2] / "tools/wd_mind_runtime_mutex.py").read_bytes())
    monkeypatch.setattr(bootstrap, "__file__", str(tmp_path / "wd_mind_runtime_bootstrap.py"))
    bootstrap._install_reader_mutex(hashlib.sha256(path.read_bytes()).hexdigest(), "a" * 40)
    shim = fake_sys.modules["sandbox.mind.step2.reader_slot"]
    assert callable(shim.reader_slot) and shim._generation == "a" * 40


def test_synthetic_reader_imports_pinned_shim_instead_of_runtime_slot(tmp_path, monkeypatch):
    # Exercise Python's actual package import behavior, not a mocked runpy call.
    root = tmp_path / "runtime"
    package = root / "assets/code/sandbox/mind/step2"
    package.mkdir(parents=True)
    (root / "site").mkdir()
    (root / "model").mkdir()
    for directory in (package, package.parent, package.parent.parent):
        (directory / "__init__.py").write_bytes(b"")
    (package / "reader_slot.py").write_text("raise RuntimeError('legacy slot was imported')\n", encoding="utf-8")
    (package / "wd_q4_private_read.py").write_text(
        "from sandbox.mind.step2.reader_slot import reader_slot\n"
        "assert reader_slot.__wrapped__.__code__.co_filename == '<pinned-reader-mutex>'\n", encoding="utf-8")
    shim = tmp_path / "wd_mind_runtime_mutex.py"
    shim.write_bytes((Path(__file__).resolve().parents[2] / "tools/wd_mind_runtime_mutex.py").read_bytes())
    monkeypatch.setattr(bootstrap, "__file__", str(tmp_path / "wd_mind_runtime_bootstrap.py"))
    monkeypatch.setattr(bootstrap, "_load_verifier", lambda: lambda *a, **k: None)
    _offline(monkeypatch)
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setattr(sys, "dont_write_bytecode", True)
    original_modules = {key: value for key, value in sys.modules.items()
                        if key == "sandbox" or key.startswith("sandbox.")}
    for key in original_modules:
        sys.modules.pop(key)
    proxy = SimpleNamespace(flags=SimpleNamespace(isolated=True, no_site=True), dont_write_bytecode=True,
                            path=sys.path, modules=sys.modules, argv=[])
    monkeypatch.setattr(bootstrap, "sys", proxy)
    args = _args(root)
    args[args.index("--mutex-sha256") + 1] = hashlib.sha256(shim.read_bytes()).hexdigest()
    try:
        assert bootstrap.main(args) == 0
        assert sys.modules["sandbox.mind.step2.reader_slot"].reader_slot.__wrapped__.__code__.co_filename == "<pinned-reader-mutex>"
    finally:
        for key in list(sys.modules):
            if key == "sandbox" or key.startswith("sandbox."):
                sys.modules.pop(key)
        sys.modules.update(original_modules)


@pytest.mark.parametrize("fault", ["wrong_hash", "changed_source", "invalid_generation", "preloaded"])
def test_mutex_shim_fails_closed_before_reader_dispatch(tmp_path, monkeypatch, fault):
    fake_sys = _isolated(monkeypatch)
    path = tmp_path / "wd_mind_runtime_mutex.py"
    path.write_bytes((Path(__file__).resolve().parents[2] / "tools/wd_mind_runtime_mutex.py").read_bytes())
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    monkeypatch.setattr(bootstrap, "__file__", str(tmp_path / "wd_mind_runtime_bootstrap.py"))
    if fault == "wrong_hash": digest = "0" * 64
    elif fault == "changed_source": path.write_bytes(b"raise RuntimeError('unauthorized')\n")
    elif fault == "preloaded": fake_sys.modules["sandbox.mind.step2.reader_slot"] = object()
    generation = "invalid" if fault == "invalid_generation" else "a" * 40
    with pytest.raises((ValueError, OSError)):
        bootstrap._install_reader_mutex(digest, generation)


def test_verified_bootstrap_disables_python_tempfile_fallback(tmp_path, monkeypatch):
    _isolated(monkeypatch)
    root = tmp_path / "runtime"
    for path in (root / "site", root / "model", root / "assets/code/sandbox/mind/step2"):
        path.mkdir(parents=True)
    (root / "assets/code/sandbox/mind/step2/wd_q4_private_read.py").write_text("# synthetic only")
    monkeypatch.setattr(bootstrap, "_load_verifier", lambda: lambda *a, **k: None)
    monkeypatch.setattr(bootstrap, "_install_reader_mutex", lambda *a, **k: None)
    def dispatch(*args, **kwargs):
        assert tempfile.tempdir == os.devnull
        assert tempfile.gettempdir() == os.devnull
        for name in ("mkstemp", "mkdtemp", "NamedTemporaryFile", "TemporaryFile", "SpooledTemporaryFile", "TemporaryDirectory"):
            with pytest.raises(OSError, match="^temporary_files_disabled$"):
                getattr(tempfile, name)()
        raise SystemExit(2)
    monkeypatch.setattr(bootstrap.runpy, "run_module", dispatch)
    assert bootstrap.main(_args(root)) == 2


def test_untrusted_inherited_temp_is_refused_before_shim_or_import(tmp_path, monkeypatch, capsys):
    _isolated(monkeypatch)
    root = tmp_path / "runtime"
    for path in (root / "site", root / "model", root / "assets/code/sandbox/mind/step2"):
        path.mkdir(parents=True)
    (root / "assets/code/sandbox/mind/step2/wd_q4_private_read.py").write_text("# synthetic only")
    monkeypatch.setenv("TEMP", str(tmp_path / "outside-temp"))
    monkeypatch.setattr(bootstrap, "_load_verifier", lambda: lambda *a, **k: None)
    monkeypatch.setattr(bootstrap, "_install_reader_mutex", lambda *a, **k: pytest.fail("shim imported"))
    monkeypatch.setattr(bootstrap.runpy, "run_module", lambda *a, **k: pytest.fail("reader imported"))
    assert bootstrap.main(_args(root)) == 2
    assert json.loads(capsys.readouterr().out)["reason"] == "runtime_verification_failed"
    assert not (tmp_path / "outside-temp").exists()


def test_e5_dispatch_requires_verified_runtime_but_not_legacy_reader(tmp_path, monkeypatch):
    _isolated(monkeypatch)
    root = tmp_path / "runtime"
    for path in (root / "site", root / "model", root / "assets/code"):
        path.mkdir(parents=True)
    calls = []
    monkeypatch.setattr(bootstrap, "_load_verifier", lambda: lambda *a, **k: calls.append("verify"))
    monkeypatch.setattr(bootstrap, "_install_reader_mutex", lambda *a, **k: calls.append("mutex"))
    def load(digest):
        calls.append(("worker_pin", digest))
        return lambda **kwargs: calls.append(("worker", kwargs)) or 0
    monkeypatch.setattr(bootstrap, "_load_e5_worker", load)
    assert bootstrap.main(_args(root) + ["--worker-sha256", "f" * 64, "--weights-sha256", "a" * 64]) == 0
    assert calls[:3] == ["verify", "mutex", ("worker_pin", "f" * 64)]
    assert calls[3] == ("worker", {"model_dir": str(root / "model"), "weights_sha256": "a" * 64,
                                    "generation": "b" * 40})


def test_worker_only_bulk_abi_does_not_require_fake_reader_paths(tmp_path, monkeypatch):
    _isolated(monkeypatch)
    root = tmp_path / 'runtime'
    for path in (root / 'site', root / 'model', root / 'assets/code'): path.mkdir(parents=True)
    monkeypatch.setattr(bootstrap,'_load_verifier',lambda:lambda *a,**kw:None)
    monkeypatch.setattr(bootstrap,'_install_reader_mutex',lambda *a,**kw:None)
    calls=[]
    monkeypatch.setattr(bootstrap,'_load_e5_worker',lambda digest:lambda **kw: calls.append(kw) or 0)
    args = _args(root)
    for key in ('memory','descriptor','descriptor-sha256'):
        index=args.index('--'+key); del args[index:index+2]
    args += ['--worker-only','--worker-sha256','f'*64,'--weights-sha256','a'*64,
             '--bulk-contract-sha256','61998bfeea1342988301f30f21d9781017249ef66761ec81874e5cc34633bc08']
    assert bootstrap.main(args) == 0
    assert calls[0]['bulk_contract_sha256'] == args[-1]


@pytest.mark.parametrize('fault', ['wrong_contract','mixed_reader_args','no_worker'])
def test_worker_only_bulk_contract_refuses_before_runtime_load(tmp_path, monkeypatch, fault):
    _isolated(monkeypatch)
    monkeypatch.setattr(bootstrap,'_load_verifier',lambda:pytest.fail('verified after invalid mode'))
    args = _args(tmp_path / 'runtime')
    if fault != 'mixed_reader_args':
        for key in ('memory','descriptor','descriptor-sha256'):
            index=args.index('--'+key); del args[index:index+2]
    args += ['--worker-only','--bulk-contract-sha256',
             '0'*64 if fault == 'wrong_contract' else bootstrap.BULK_CONTRACT_SHA256]
    if fault != 'no_worker': args += ['--worker-sha256','f'*64,'--weights-sha256','a'*64]
    assert bootstrap.main(args) == 2


def test_wrong_worker_pin_fails_before_worker_exec(tmp_path, monkeypatch):
    path = tmp_path / "wd_mind_e5_worker.py"
    marker = tmp_path / "must-not-exist"
    path.write_text("from pathlib import Path\nPath(%r).write_text('bad')\n" % str(marker))
    monkeypatch.setattr(bootstrap, "__file__", str(tmp_path / "wd_mind_runtime_bootstrap.py"))
    with pytest.raises(ValueError, match="worker_pin_mismatch"):
        bootstrap._load_e5_worker("0" * 64)
    assert not marker.exists()

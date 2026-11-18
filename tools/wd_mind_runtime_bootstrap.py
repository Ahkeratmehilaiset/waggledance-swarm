# SPDX-License-Identifier: BUSL-1.1
# Based on sandbox/mind/step2/runtime_bootstrap.py at Git commit e98c5aef5bd3bd66c61e5692e738f17a4ecf6beb; packaged verifier basename adapted.
"""Synthetic-ready isolated launcher for an externally anchored Q4 runtime.

The invoking trusted launcher MUST independently pin this bootstrap, its sibling
runtime_manifest.py, the Python interpreter, all tuple values and reader inputs.
It must also bound runtime, protect the generation against mutation, configure
model/cache I/O and ensure caller authorization. This file neither installs nor
activates anything. Verification is not a defence against same-user mutation
after the check, native DLL substitution, or incomplete model-asset provenance.

Invocation requires ``python -I -S -B``. One externally pinned manifest covers
``assets/code`` (including the reader and its imports), ``site``, and ``model``.
The site and code are not imported or added to sys.path until verification
succeeds. The query stays on stdin and is never copied to arguments. The model
directory is always the manifest-verified runtime's ``model`` child.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import runpy
import stat
import sys
import tempfile
import types

BULK_CONTRACT = b'{"frame_bytes":65536,"inflight_bytes":65540,"nonce_limit":8192,"schema":"wd.mind-e5-bulk-contract.v1","stdout_bytes":67108864}'
BULK_CONTRACT_SHA256 = '61998bfeea1342988301f30f21d9781017249ef66761ec81874e5cc34633bc08'
# Same independently manifest-anchored verifier pin as runtime_process.py.
# This bootstrap itself is verified by its caller before process creation.
_PACKAGED_VERIFIER_SHA256 = 'd488a8495f0ee89aebc26d2d1345878d26e5a6c85b228e2c10086f4134680a64'


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError("invalid_arguments")


def _refuse(reason):
    print(json.dumps({"schema": "wd.q4-runtime-bootstrap.v1", "status": "REFUSED", "reason": reason},
                     sort_keys=True))
    return 2


def _load_verifier():
    # The caller pins this sibling before starting this process. Recheck and
    # execute these exact bytes: an apparently matching __pycache__ is not a pin.
    path = Path(__file__).with_name("wd_mind_runtime_manifest.py")
    before = os.lstat(path)
    if (not stat.S_ISREG(before.st_mode) or stat.S_ISLNK(before.st_mode) or not before.st_ino
            or getattr(before, 'st_file_attributes', 0) & 0x400 or before.st_nlink != 1):
        raise ValueError('verifier_file_invalid')
    with path.open('rb') as stream:
        opened = os.fstat(stream.fileno())
        source = stream.read(2 * 1024 * 1024 + 1)
        after_read = os.fstat(stream.fileno())
    after = os.lstat(path)
    identity = lambda info: (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_nlink)
    if (identity(before) != identity(opened) or identity(opened) != identity(after_read)
            or identity(after_read) != identity(after) or not stat.S_ISREG(after.st_mode)
            or getattr(after, 'st_file_attributes', 0) & 0x400):
        raise ValueError('verifier_file_changed')
    if (not source or len(source) > 2 * 1024 * 1024
            or len(source) != opened.st_size
            or hashlib.sha256(source).hexdigest() != _PACKAGED_VERIFIER_SHA256):
        raise ValueError('verifier_pin_mismatch')
    module = types.ModuleType('_wd_trusted_runtime_manifest')
    module.__file__ = str(path)
    exec(compile(source, str(path), 'exec', dont_inherit=True), module.__dict__)
    if not callable(getattr(module, 'verify_runtime', None)):
        raise ValueError('verifier_unavailable')
    return module.verify_runtime


def _install_reader_mutex(expected_sha256, generation):
    """Load only manifest-pinned shim bytes before the legacy reader import."""
    if type(expected_sha256) is not str or re.fullmatch(r"[0-9a-f]{64}", expected_sha256) is None:
        raise ValueError("mutex_pin_invalid")
    path = Path(__file__).with_name("wd_mind_runtime_mutex.py")
    with path.open("rb") as stream:
        source = stream.read(2 * 1024 * 1024 + 1)
    if not source or len(source) > 2 * 1024 * 1024 or hashlib.sha256(source).hexdigest() != expected_sha256:
        raise ValueError("mutex_pin_mismatch")
    name = "sandbox.mind.step2.reader_slot"
    if name in sys.modules:
        raise ValueError("reader_slot_preloaded")
    module = types.ModuleType(name)
    module.__file__ = str(path)
    exec(compile(source, "<pinned-reader-mutex>", "exec"), module.__dict__)
    module.configure_generation(generation)
    sys.modules[name] = module


def _load_e5_worker(expected_sha256):
    """Load only bundle-pinned worker bytes after runtime verification."""
    if type(expected_sha256) is not str or re.fullmatch(r"[0-9a-f]{64}", expected_sha256) is None:
        raise ValueError("worker_pin_invalid")
    path = Path(__file__).with_name("wd_mind_e5_worker.py")
    with path.open("rb") as stream:
        source = stream.read(2 * 1024 * 1024 + 1)
    if not source or len(source) > 2 * 1024 * 1024 or hashlib.sha256(source).hexdigest() != expected_sha256:
        raise ValueError("worker_pin_mismatch")
    namespace = {"__name__": "_wd_pinned_e5_worker", "__file__": str(path)}
    exec(compile(source, "<pinned-e5-worker>", "exec"), namespace)
    if not callable(namespace.get("main")):
        raise ValueError("worker_entrypoint_missing")
    return namespace["main"]


def _temporary_files_disabled(*args, **kwargs):
    # On Windows, opening NUL/foo can succeed as a character device and silently
    # discard bytes. Refuse rather than misrepresent it as a temporary file.
    raise OSError("temporary_files_disabled")


def _disable_python_temporary_files():
    tempfile.tempdir = os.devnull
    for name in ("mkstemp", "mkdtemp", "NamedTemporaryFile", "TemporaryFile", "SpooledTemporaryFile", "TemporaryDirectory"):
        setattr(tempfile, name, _temporary_files_disabled)


def main(argv=None):
    if not (sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode):
        return _refuse("isolation_required")
    parser = _Parser(add_help=False, allow_abbrev=False)
    for name in ("runtime-root", "runtime-manifest", "runtime-manifest-sha256", "runtime-generation",
                 "runtime-revision", "mutex-sha256", "lock-file", "lock-timeout"):
        parser.add_argument("--" + name, required=True)
    for name in ('memory', 'descriptor', 'descriptor-sha256'):
        parser.add_argument('--' + name)
    parser.add_argument('--worker-only', action='store_true')
    parser.add_argument("--worker-sha256")
    parser.add_argument("--weights-sha256")
    parser.add_argument("--bulk-contract-sha256")
    try:
        args = parser.parse_args(argv)
        reader_args = (args.memory, args.descriptor, args.descriptor_sha256)
        if (args.worker_only and (not args.worker_sha256 or any(value is not None for value in reader_args))
                or not args.worker_only and any(value is None for value in reader_args)):
            raise ValueError('worker_only_contract')
        if (args.bulk_contract_sha256 is not None and
                (args.bulk_contract_sha256 != BULK_CONTRACT_SHA256 or not args.worker_sha256
                 or hashlib.sha256(BULK_CONTRACT).hexdigest() != BULK_CONTRACT_SHA256)):
            raise ValueError('bulk_contract')
        root = Path(args.runtime_root)
        verify = _load_verifier()
        verify(root, Path(args.runtime_manifest), args.runtime_manifest_sha256,
               expected_generation=args.runtime_generation, expected_revision=args.runtime_revision)
        # verify() has already checked the pinned root and every ancestor for
        # reparse points. Refuse aliases or caller-selected cache destinations.
        # This does not guard against same-user mutation after verification.
        if (args.runtime_root != os.path.abspath(args.runtime_root)
                or os.environ.get("TORCHINDUCTOR_CACHE_DIR") != args.runtime_root):
            raise ValueError("runtime_cache_mismatch")
        code = root / "assets" / "code"
        site = root / "site"
        model = root / "model"
        reader = code / "sandbox" / "mind" / "step2" / "wd_q4_private_read.py"
        if (not code.is_dir() or not site.is_dir() or not model.is_dir()
                or (not args.worker_sha256 and not reader.is_file())):
            raise ValueError("entrypoint_missing")
        if any(name == "sandbox" or name.startswith("sandbox.") for name in sys.modules):
            raise ValueError("reader_namespace_preloaded")
        if bool(args.worker_sha256) != bool(args.weights_sha256):
            raise ValueError("worker_binding_incomplete")
        if (os.environ.get("HF_HUB_OFFLINE") != "1" or os.environ.get("TRANSFORMERS_OFFLINE") != "1"
                or any(os.environ.get(name) != os.devnull for name in (
                    "TEMP", "TMP", "HF_HOME", "HF_HUB_CACHE", "HUGGINGFACE_HUB_CACHE", "TRANSFORMERS_CACHE",
                    "HF_ASSETS_CACHE", "SENTENCE_TRANSFORMERS_HOME", "TORCH_HOME", "XDG_CACHE_HOME", "CUDA_CACHE_PATH"))):
            raise ValueError("offline_environment_required")
        # Do not allow Python's tempfile module to fall back to an ambient path.
        # Native libraries can bypass this setting; their I/O remains UNKNOWN.
        _disable_python_temporary_files()
        _install_reader_mutex(args.mutex_sha256, args.runtime_generation)
    except Exception:
        return _refuse("runtime_verification_failed")

    # No PYTHONPATH, ambient cwd or user-site participates; these two paths
    # belong to the exact tree verified immediately above. The trusted caller
    # must still control concurrent writes and the Windows native DLL search.
    # Keep the pinned application code first, but do not let a package-site
    # backport shadow the interpreter's standard library (for example typing).
    # The isolated interpreter's own paths remain ahead of the pinned site.
    sys.path.insert(0, str(code))
    sys.path.append(str(site))
    if args.worker_sha256:
        try:
            worker = _load_e5_worker(args.worker_sha256)
            options = dict(model_dir=str(model), weights_sha256=args.weights_sha256, generation=args.runtime_generation)
            if args.bulk_contract_sha256 is not None:
                options['bulk_contract_sha256'] = args.bulk_contract_sha256
            return worker(**options)
        except Exception:
            return _refuse("reader_execution_failed")
    sys.argv = ["sandbox.mind.step2.wd_q4_private_read", "--memory", args.memory,
                "--descriptor", args.descriptor, "--descriptor-sha256", args.descriptor_sha256,
                "--lock-file", args.lock_file, "--lock-timeout", args.lock_timeout,
                "--expected-runtime-revision", args.runtime_revision,
                "--model-dir", str(model)]
    try:
        runpy.run_module("sandbox.mind.step2.wd_q4_private_read", run_name="__main__", alter_sys=True)
    except SystemExit as exc:
        return exc.code if type(exc.code) is int and 0 <= exc.code <= 255 else 2
    except Exception:
        return _refuse("reader_execution_failed")
    return 0


if __name__ == "__main__":
    sys.exit(main())

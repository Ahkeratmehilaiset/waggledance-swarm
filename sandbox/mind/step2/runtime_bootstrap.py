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
import importlib.util
import json
from pathlib import Path
import runpy
import sys


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError("invalid_arguments")


def _refuse(reason):
    print(json.dumps({"schema": "wd.q4-runtime-bootstrap.v1", "status": "REFUSED", "reason": reason},
                     sort_keys=True))
    return 2


def _load_verifier():
    # The caller authenticates this sibling before starting this process; it is
    # not obtained from the unverified runtime generation or the ambient cwd.
    path = Path(__file__).with_name("runtime_manifest.py")
    spec = importlib.util.spec_from_file_location("_wd_trusted_runtime_manifest", path)
    if spec is None or spec.loader is None:
        raise ValueError("verifier_unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.verify_runtime


def main(argv=None):
    if not (sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode):
        return _refuse("isolation_required")
    parser = _Parser(add_help=False, allow_abbrev=False)
    for name in ("runtime-root", "runtime-manifest", "runtime-manifest-sha256", "runtime-generation",
                 "runtime-revision", "memory", "descriptor", "descriptor-sha256", "lock-file", "lock-timeout"):
        parser.add_argument("--" + name, required=True)
    try:
        args = parser.parse_args(argv)
        root = Path(args.runtime_root)
        verify = _load_verifier()
        verify(root, Path(args.runtime_manifest), args.runtime_manifest_sha256,
               expected_generation=args.runtime_generation, expected_revision=args.runtime_revision)
        code = root / "assets" / "code"
        site = root / "site"
        model = root / "model"
        reader = code / "sandbox" / "mind" / "step2" / "wd_q4_private_read.py"
        if not code.is_dir() or not site.is_dir() or not model.is_dir() or not reader.is_file():
            raise ValueError("entrypoint_missing")
        if any(name == "sandbox" or name.startswith("sandbox.") for name in sys.modules):
            raise ValueError("reader_namespace_preloaded")
    except Exception:
        return _refuse("runtime_verification_failed")

    # No PYTHONPATH, ambient cwd or user-site participates; these two paths
    # belong to the exact tree verified immediately above. The trusted caller
    # must still control concurrent writes and the Windows native DLL search.
    sys.path[:0] = [str(code), str(site)]
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

"""Explicit public synthetic preparation only, NOT an OS sandbox or a sealed M0 run.

The Python boundary denies F:/UNC/device paths and unlisted repository file contents,
including indirect imports of the two excluded integration modules and conftest.py.
Source-directory metadata/listings and the installed Python runtime remain readable.
Native extensions/inherited descriptors are NOT confined by this Python-only boundary.
Bare Windows NUL is an allowed non-file sink for pytest's console setup.
"""
import argparse
import contextlib
import hashlib
import importlib.abc
import importlib.machinery
import json
import ntpath
import os
from pathlib import Path
import re
import shutil
import sys

REVISION = "6cf81dbbefe2b0e19a4c8306100beb30784ac4e4"
TESTS = ("sandbox/mind/remember/test_remember_v2.py", "sandbox/mind/remember/test_remember_v21.py")
EXCLUDED = ("sandbox/mind/step1/test_lane_view.py", "sandbox/mind/test_join_card_delivery.py")
PRODUCT = (
    "sandbox/mind/remember/remember_v21.py", "sandbox/mind/remember/remember_v3.py",
    "sandbox/mind/remember/remember_v2.py", "sandbox/mind/remember/prompts.py",
    "sandbox/mind/step2/corpus.py", "sandbox/mind/step2/memory.py", "sandbox/mind/step2/cell.py",
    "sandbox/mind/step2/encode.py", "sandbox/mind/step2/q4.py", "sandbox/mind/step2/run_q4.py",
    "sandbox/mind/step2/test_step2.py", "tools/__init__.py", "tools/bridge_mind_workspace.py",
    "tools/bridge_diff_privacy.py", "tools/bridge_compact_view.py", "waggledance/__init__.py",
    "waggledance/core/__init__.py", "waggledance/core/bridge_log_reader.py", "core/__init__.py",
    "core/faiss_store.py", "waggledance/core/hex_cell_topology.py", "waggledance/core/magma/__init__.py",
    "waggledance/core/magma/confidence_decay.py", "waggledance/core/magma/provenance.py")
ACTIVE = None


class Refused(RuntimeError):
    pass


def key(value):
    text = os.fsdecode(value)
    drive, _ = ntpath.splitdrive(text)
    if text.startswith("\\") or drive.lower() not in ("", "c:"):
        raise Refused("non_c_or_device_path")
    return ntpath.normcase(ntpath.abspath(text))


def beneath(path, root):
    return path == root or path.startswith(root + "\\")


def collection_paths(source_root, public_test):
    root = str(source_root).replace("\\", "/").rstrip("/")
    return [root + "/" + name for name in TESTS] + [str(public_test).replace("\\", "/")]


def collection_route(path, frozen_files):
    candidate = key(path)
    return any(key(file) == candidate or beneath(key(file), candidate) for file in frozen_files)


def check_refs(refs, eligible):
    if not isinstance(refs, list) or any(not isinstance(ref, str) or ref not in eligible for ref in refs):
        raise Refused("answer_ref_not_question_eligible")


def check_rendered(rendered, eligible, forbidden_tokens):
    if not isinstance(rendered, str):
        raise Refused("rendered_type")
    refs = re.findall(r"[A-Za-z0-9_.-]+\.md", rendered)
    check_refs(refs, eligible)
    if any(token.casefold() in rendered.casefold() for token in forbidden_tokens):
        raise Refused("whole_rendered_input_leak")


class Boundary:
    def __init__(self, source_root, source_files, audit_root, runtime_roots, extra_files):
        self.source_root = key(source_root)
        self.source_files = {key(p) for p in source_files}
        self.audit_root = key(audit_root)
        self.runtime_roots = {key(p) for p in runtime_roots}
        self.extra_files = {key(p) for p in extra_files}
        self.excluded = {key(ntpath.join(self.source_root, p)) for p in EXCLUDED}
        self.denials = 0
        self.last_denial = None
        self.first_denial = None
        self.parents = set()
        for root in (self.source_root, self.audit_root, *self.runtime_roots, *self.extra_files):
            while root:
                self.parents.add(root)
                parent = ntpath.dirname(root)
                if parent == root:
                    break
                root = parent

    def check(self, value, operation, write=False):
        if isinstance(value, int):  # Inherited/native descriptors are outside this boundary's claim.
            return
        if os.fsdecode(value).casefold() == "nul":
            return  # Exact non-file device only, never a path prefix or named pipe.
        try:
            path = key(value)
            if path in self.excluded or ntpath.basename(path) == "conftest.py":
                raise Refused("excluded_before_import")
            if write:
                if not beneath(path, self.audit_root):
                    raise Refused("write_outside_synthetic_audit")
                return
            if beneath(path, self.audit_root) or any(beneath(path, root) for root in self.runtime_roots):
                return
            if path in self.source_files or path in self.extra_files:
                return
            if operation in ("stat", "lstat", "access", "listdir", "scandir") and (
                    beneath(path, self.source_root) or path in self.parents):
                return
            if operation in ("stat", "lstat", "access") and ntpath.basename(path) == "__init__.py" and (
                    ntpath.dirname(path) in self.parents):
                return  # Prove absence/presence only; parent package contents are still not readable/importable.
            raise Refused("unlisted_file_content")
        except Refused as exc:
            self.denials += 1
            # Public code/runtime paths help diagnose this synthetic-only boundary. Unknown paths stay hashed.
            raw = os.fsdecode(value)
            public = any(beneath(ntpath.normcase(ntpath.abspath(raw)), root)
                         for root in (self.source_root, *self.runtime_roots))
            self.last_denial = {"operation": operation, "reason": str(exc),
                                "public_path": raw if public else None,
                                "path_sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest()}
            if self.first_denial is None:
                self.first_denial = self.last_denial
            raise

    def install(self):
        global ACTIVE
        if ACTIVE is not None:
            raise Refused("boundary_already_installed")
        ACTIVE = self
        for name in ("stat", "lstat", "access", "listdir", "scandir"):
            original = getattr(os, name)

            def checked(path=".", *args, _name=name, _original=original, **kwargs):
                frame = os.fsdecode(path) if not isinstance(path, int) else ""
                if ntpath.basename(frame).startswith("<") and frame.endswith(">"):
                    raise FileNotFoundError("non-file interpreter frame")
                self.check(path, _name)
                return _original(path, *args, **kwargs)
            setattr(os, name, checked)

        def audit(event, args):
            if event == "open":
                path, mode, flags = args
                write = any(c in (mode or "") for c in "wax+") or bool(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT))
                self.check(path, "open", write=write)
                if write and shutil.disk_usage("C:\\").free < 3 * 1024**3:
                    raise Refused("disk_floor")
            elif event in ("subprocess.Popen", "os.system", "socket.connect"):
                self.denials += 1
                self.last_denial = {"operation": event, "reason": "external_execution_or_network"}
                if self.first_denial is None:
                    self.first_denial = self.last_denial
                raise Refused("external_execution_or_network")
            elif event in ("os.mkdir", "os.remove", "os.rmdir", "os.chmod", "os.utime"):
                self.check(args[0], event, write=True)
            elif event in ("os.rename", "os.replace"):
                self.check(args[0], event, write=True)
                self.check(args[1], event, write=True)
        sys.addaudithook(audit)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, value):
    with Path(path).open("x", encoding="ascii", newline="\n") as handle:
        json.dump(value, handle, sort_keys=True, indent=1)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())


def run(source, receipt, receipt_sha, out):
    source, receipt, out = Path(source).resolve(), Path(receipt).resolve(), Path(out).resolve()
    audit_base = (Path.cwd() / ".codex-audit").resolve()
    if not out.is_relative_to(audit_base) or out == audit_base or out.exists():
        raise Refused("new_owned_audit_required")
    if shutil.disk_usage("C:\\").free < 3 * 1024**3:
        raise Refused("disk_floor")
    if digest(receipt) != receipt_sha.lower():
        raise Refused("source_receipt_drift")
    pins = json.loads(receipt.read_text(encoding="ascii"))
    if pins["measured_revision"] != REVISION or Path(pins["source_root"]).resolve() != source:
        raise Refused("not_the_frozen_source")
    files = {}
    import_files = {key(source / relative) for relative in (*PRODUCT, *TESTS)}
    # Unchanged remember_v2.source_blobs() hashes every immediate Python file in these two directories.
    # Permit those pre-pinned CODE BYTES for provenance hashing, never extra test collection/import authority.
    hash_inputs = {name for name in pins["source_files"] if name.endswith(".py") and
                   str(Path(name).parent).replace("\\", "/") in
                   ("sandbox/mind/remember", "sandbox/mind/step2")}
    for relative in sorted(set((*PRODUCT, *TESTS)) | hash_inputs):
        path = source / relative
        expected = pins["source_files"][relative]["sha256"]
        if path.is_symlink() or digest(path) != expected:
            raise Refused("candidate_source_drift")
        files[str(path)] = expected
    public_test = Path(__file__).with_name("test_m0_synthetic_runner.py").resolve()
    paths = collection_paths(source, public_test)
    out.mkdir(parents=True)
    (out / "tmp").mkdir()
    config = out / "frozen.ini"
    config.write_text("[pytest]\n", encoding="ascii")
    command = ["--assert=plain", "--noconftest", "--import-mode=importlib", "-p", "no:cacheprovider",
               "-p", "no:capture", "-p", "no:terminal", "-p", "no:debugging",
               "--log-file", str(out / "framework.log"),
               "-c", str(config), "--rootdir", str(out), "--basetemp", str(out / "fixtures"), *paths]
    write_json(out / "BEFORE.json", {"revision": REVISION, "collection": paths, "pytest_argv": command,
              "excluded_before_import": EXCLUDED, "candidate_files": files, "harness_sha256": digest(__file__),
              "import_allowlist": sorted(import_files),
              "public_test_sha256": digest(public_test), "source_receipt_sha256": receipt_sha.lower(),
              "faiss_opt_level": "GENERIC",
              "boundary": "Python-only; source-directory metadata/runtime/native descriptors not isolated",
              "sealed_cases": "NOT_READ", "actual_M0": "NOT_RUN"})
    os.environ["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    # FAISS's documented opt-level setting avoids its optional platform/subprocess autodetection.
    # This configuration is synthetic-preparation evidence only, never reused as the actual M0/M1 baseline.
    os.environ["FAISS_OPT_LEVEL"] = "GENERIC"
    os.environ.pop("PYTEST_ADDOPTS", None)
    os.environ.pop("PYTEST_PLUGINS", None)
    os.environ["TEMP"] = os.environ["TMP"] = str(out / "tmp")
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(public_test.parent))
    sys.modules["m0_synthetic_runner"] = sys.modules[__name__]
    boundary = Boundary(source, files, out, {sys.base_prefix, sys.prefix}, {__file__, public_test})
    loaded = []

    class Finder(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname, path=None, target=None):
            spec = importlib.machinery.PathFinder.find_spec(fullname, path, target)
            if spec and spec.origin and spec.origin not in ("built-in", "frozen"):
                boundary.check(spec.origin, "open")
                origin = Path(spec.origin)
                if origin.is_relative_to(source):
                    if key(origin) not in import_files or digest(origin) != files[str(origin)]:
                        raise Refused("unpinned_dependency")
                    loaded.append({"module": fullname, "path": str(origin), "sha256": files[str(origin)]})
            return spec

    collected = []
    counts = {"passed": 0, "failed": 0, "skipped": 0}

    class Outcomes:
        def pytest_addoption(self, parser):
            # pytest's unittest adapter queries this even with the interactive debugging plugin disabled.
            parser.addoption("--trace", action="store_true", default=False)
            parser.addoption("--pdb", dest="usepdb", action="store_true", default=False)

        def pytest_ignore_collect(self, collection_path, config):
            return not collection_route(collection_path, paths)

        def pytest_collectreport(self, report):
            if report.failed:
                with (out / "collection-errors.log").open("a", encoding="utf-8") as handle:
                    handle.write(str(report.longrepr) + "\n")

        def pytest_collection_finish(self, session):
            allowed = {key(path) for path in paths}
            for item in session.items:
                if key(item.path) not in allowed:
                    raise Refused("unexpected_collected_file")
                collected.append(item.nodeid)

        def pytest_runtest_logreport(self, report):
            if report.failed:
                counts["failed"] += 1
                with (out / "test-errors.log").open("a", encoding="utf-8") as handle:
                    handle.write(str(report.longrepr) + "\n")
            elif report.when == "call":
                counts["passed" if report.passed else "skipped"] += 1

    log = (out / "pytest.log").open("x", encoding="utf-8")
    # Execute verified source, never a pre-existing unpinned repository bytecode cache.
    code_pins = {key(path): expected for path, expected in files.items()}
    code_pins.update({key(__file__): digest(__file__), key(public_test): digest(public_test)})
    original_get_code = importlib.machinery.SourceFileLoader.get_code

    def checked_get_code(loader, fullname):
        path = key(loader.path)
        if path in code_pins:
            if beneath(path, key(source)) and path not in import_files:
                raise Refused("hash_input_not_import_allowed")
            body = loader.get_data(loader.path)
            if hashlib.sha256(body).hexdigest() != code_pins[path]:
                raise Refused("executed_source_drift")
            return compile(body, loader.path, "exec", dont_inherit=True)
        return original_get_code(loader, fullname)

    importlib.machinery.SourceFileLoader.get_code = checked_get_code
    boundary.install()
    sys.meta_path.insert(0, Finder())
    with log, contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
        try:
            import pytest
            pytest.hookimpl(tryfirst=True)(Outcomes.pytest_ignore_collect)
            code = int(pytest.main(command, plugins=[Outcomes()]))
            status, exception = ("PASS" if code == 0 else "FAILED"), None
        except BaseException as exc:
            code, status, exception = 2, "REFUSED", type(exc).__name__
            tb = exc.__traceback__
            frames = []
            while tb is not None:
                frames.append({"file": tb.tb_frame.f_code.co_filename, "line": tb.tb_lineno,
                               "function": tb.tb_frame.f_code.co_name})
                tb = tb.tb_next
            write_json(out / "EXCEPTION_FRAMES.json", frames)
    size = sum(p.stat().st_size for p in out.rglob("*") if p.is_file())
    if size > 32 * 1024**2:
        code, status = 2, "RESOURCE_LIMIT"
    result = {"status": status, "exit_code": code, "exception_class": exception, "counts": counts,
              "collected": collected, "loaded_product_modules": loaded, "python_boundary_denials": boundary.denials,
              "last_denial": boundary.last_denial,
              "first_denial": boundary.first_denial,
              "synthetic_audit_bytes": size, "budget_bytes": 32 * 1024**2,
              "pytest_log_sha256": digest(out / "pytest.log"), "actual_M0": "NOT_RUN", "sealed_cases": "NOT_READ",
              "os_isolation": "NOT_PROVEN", "real_M1": "HOLD"}
    write_json(out / "RESULT.json", result)
    print(json.dumps({k: result[k] for k in ("status", "exit_code", "counts", "python_boundary_denials",
                                            "actual_M0", "sealed_cases", "os_isolation")}))
    return code


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--source-root", required=True, type=Path)
    parser.add_argument("--source-receipt", required=True, type=Path)
    parser.add_argument("--source-receipt-sha256", required=True)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    return run(args.source_root, args.source_receipt, args.source_receipt_sha256, args.out)


if __name__ == "__main__":
    raise SystemExit(main())

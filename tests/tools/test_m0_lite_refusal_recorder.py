"""Synthetic R2 tests only. Hook fixtures are not actual M0 evidence."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

SOURCE = Path(__file__).resolve().parents[2] / "sandbox/mind/measurement_tools/m0_lite_refusal_recorder.py"


def load_recorder():
    assert SOURCE.is_file(), "MISSING_R2_RECORDER_BEFORE_IMPLEMENTATION"
    spec = importlib.util.spec_from_file_location("_r2_recorder_test", SOURCE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def identity():
    raw = SOURCE.read_bytes()
    lf = raw.replace(b"\r\n", b"\n")
    blob = hashlib.sha1(b"blob " + str(len(lf)).encode() + b"\0" + lf,
                        usedforsecurity=False).hexdigest()
    return hashlib.sha256(raw).hexdigest(), blob


def synthetic_pins(module, monkeypatch):
    pins = {name: hashlib.sha256(("synthetic:" + name).encode()).hexdigest()
            for name in module.REQUIRED_PIN_NAMES}
    monkeypatch.setattr(module, "_pin_fingerprints", lambda: dict(pins))
    return pins


@pytest.fixture
def rec(monkeypatch):
    module = load_recorder()
    synthetic_pins(module, monkeypatch)
    return module


@pytest.fixture
def audit(tmp_path):
    root = tmp_path / ".codex-audit"
    root.mkdir()
    return root


def run(module, audit, tmp_path, code="pass", *, stage="direct", command=None, **changes):
    sha, blob = identity()
    kw = dict(attempt_id="toy", stage=stage, stage_number=1,
              command=command or [sys.executable, "-B", "-c", code],
              cwd=str(tmp_path), audit_root=audit,
              phase0_sha256="1" * 64, graph_sha256="2" * 64,
              expected_source_sha256=sha, expected_source_blob=blob,
              timeout_seconds=10)
    kw.update(changes)
    return module.record_command(**kw)


def test_first_failure_has_real_exit_and_limited_indicator(tmp_path, audit, monkeypatch):
    command = [sys.executable, "-B", "-c",
               "import sys;sys.stderr.write('PermissionError: synthetic denied\\n');sys.exit(7)"]
    control = subprocess.run(command, cwd=tmp_path, capture_output=True, check=False)
    assert control.returncode == 7
    assert b"PermissionError" in control.stderr
    module = load_recorder()  # RED must occur after the actual failing control above.
    synthetic_pins(module, monkeypatch)
    result = run(module, audit, tmp_path, command=command)
    assert result["exit_code"] == 7
    assert result["limited_indicator"] > 0
    assert result["status"] == "FAILED"


def test_direct_success_is_limited_not_hook_zero(rec, audit, tmp_path):
    result = run(rec, audit, tmp_path)
    assert result["status"] == "COMPLETED"
    assert result["limited_indicator"] == 0
    assert result["hook"]["status"] == "NOT_APPLICABLE"
    assert result["hook"].get("refused") is None
    assert result["evidence_class"] == "R2_LIMITED_EXIT_STDERR"
    assert result["child_process_internal_refusals"] == "NOT_OBSERVED"
    assert "handled or unreported denials" in result["limitation"]
    assert result["started_utc"] and result["ended_utc"]
    assert result["actual_denial_count"] is None


@pytest.mark.parametrize("marker", [
    "PermissionError", "permission denied", "ACCESS IS DENIED",
    "Access Denied", "operation not permitted", "REFUSED",
])
def test_exit_zero_with_marker_cannot_pass(rec, audit, tmp_path, marker):
    result = run(rec, audit, tmp_path,
                 code="import sys;sys.stderr.write(" + repr(marker) + ")")
    assert result["exit_code"] == 0
    assert result["limited_indicator"] > 0
    assert result["status"] == "FAILED"


def test_invalid_stderr_bytes_never_decode_to_success(rec, audit, tmp_path):
    result = run(rec, audit, tmp_path,
                 code="import sys;sys.stderr.buffer.write(bytes([255,254]))")
    assert result["status"] == "INCOMPLETE"
    assert result["limited_indicator"] is None
    assert "STDERR_UNDECODABLE" in result["errors"]


def test_exact_argv_and_output_hashes(rec, audit, tmp_path):
    arguments = ["space here", 'slash\\and"quote', "t\u00e4sm\u00e4", "SYNTHETIC_PRIVATE_ARGUMENT"]
    command = [sys.executable, "-B", "-c",
               "import json,sys;sys.stdout.write(json.dumps(sys.argv[1:]))", *arguments]
    result = run(rec, audit, tmp_path, command=command)
    out = Path(result["stdout"]["path"]).read_bytes()
    assert json.loads(out) == arguments
    assert result["stdout"]["sha256"] == hashlib.sha256(out).hexdigest()
    assert result["stderr"]["sha256"] == hashlib.sha256(b"").hexdigest()
    canonical = json.dumps(command, ensure_ascii=True, separators=(",", ":")).encode("ascii")
    assert result["command_sha256"] == hashlib.sha256(canonical).hexdigest()
    assert "SYNTHETIC_PRIVATE_ARGUMENT" not in json.dumps(result)
    assert json.loads(Path(result["receipt_path"]).read_text()) == result


def test_repeat_never_overwrites_or_reexecutes(rec, audit, tmp_path):
    result = run(rec, audit, tmp_path)
    receipt = Path(result["receipt_path"])
    original = receipt.read_bytes()
    with pytest.raises(FileExistsError):
        run(rec, audit, tmp_path, code="raise RuntimeError('must not run')")
    assert receipt.read_bytes() == original


def test_missing_pin_is_incomplete(rec, audit, tmp_path, monkeypatch):
    monkeypatch.setattr(rec, "_pin_fingerprints", lambda: {})
    result = run(rec, audit, tmp_path)
    assert result["status"] == "INCOMPLETE"
    assert result["limited_indicator"] is None
    assert result["exit_code"] is None
    assert "PIN_MISSING" in result["errors"]


def test_inherited_pins_are_not_mutated(rec, audit, tmp_path):
    before = {name: os.environ.get(name) for name in rec.REQUIRED_PIN_NAMES}
    result = run(rec, audit, tmp_path)
    assert before == {name: os.environ.get(name) for name in rec.REQUIRED_PIN_NAMES}
    assert set(result["pin_fingerprints"]) == set(rec.REQUIRED_PIN_NAMES)
    assert all(len(value) == 64 for value in result["pin_fingerprints"].values())
    assert all(value != before[name] for name, value in result["pin_fingerprints"].items())


def test_changed_pin_fingerprint_cannot_pass(rec, audit, tmp_path, monkeypatch):
    pins = {name: "a" * 64 for name in rec.REQUIRED_PIN_NAMES}
    count = 0

    def snapshot():
        nonlocal count
        count += 1
        return pins if count == 1 else {**pins, rec.REQUIRED_PIN_NAMES[0]: "b" * 64}

    monkeypatch.setattr(rec, "_pin_fingerprints", snapshot)
    result = run(rec, audit, tmp_path)
    assert result["status"] == "INCOMPLETE"
    assert result["limited_indicator"] is None
    assert "PINS_CHANGED" in result["errors"]


def test_source_filter_binding_is_required(rec, audit, tmp_path):
    result = run(rec, audit, tmp_path, expected_source_sha256="0" * 64)
    assert result["status"] == "INCOMPLETE"
    assert result["limited_indicator"] is None
    assert "SOURCE_BINDING_MISMATCH" in result["errors"]


def test_missing_filter_is_unknown(rec, audit, tmp_path, monkeypatch):
    monkeypatch.setattr(rec, "FILTER_MARKERS", ())
    result = run(rec, audit, tmp_path)
    assert result["status"] == "INCOMPLETE"
    assert result["limited_indicator"] is None
    assert "FILTER_UNAVAILABLE" in result["errors"]


@pytest.mark.parametrize("kind", ["missing", "crash", "timeout"])
def test_failed_process_evidence_is_preserved(rec, audit, tmp_path, monkeypatch, kind):
    def invoke(*args, **kwargs):
        if kind == "missing":
            raise OSError("SYNTHETIC_PRIVATE_ERROR")
        if kind == "timeout":
            raise subprocess.TimeoutExpired("synthetic", 1)
        return -9

    monkeypatch.setattr(rec, "_invoke", invoke)
    result = run(rec, audit, tmp_path)
    assert result["status"] == "INCOMPLETE"
    assert result["limited_indicator"] is None
    assert Path(result["receipt_path"]).is_file()
    assert "SYNTHETIC_PRIVATE_ERROR" not in json.dumps(result)


def configure_synthetic_pytest(rec, audit, monkeypatch, *, mode="valid", rows=None):
    """Parser fixture only; never asserted as actual D15/M0 observation."""
    report = audit / "toy--pytest" / "d15.hook.jsonl"
    monkeypatch.setenv("PYTEST_ADDOPTS", "-p hook_plugin_d15")
    monkeypatch.setenv("WD_D15_HOOK_REPORT", str(report))

    def invoke(command, cwd, stdout, stderr, timeout):
        if mode != "missing":
            records = rows or [{"kind": "summary", "exit": 0, "refused": 0, "subprocess": 0}]
            report.write_text("".join(json.dumps(row) + "\n" for row in records), encoding="utf-8")
            if mode in ("stale", "future", "coarse"):
                stamp = {"stale": 1_000_000_000, "future": 4_102_444_800_000_000_000,
                         "coarse": (report.parent / "intent.json").stat().st_mtime_ns}[mode]
                os.utime(report, ns=(stamp, stamp))
        return 0

    monkeypatch.setattr(rec, "_invoke", invoke)
    return report


@pytest.mark.parametrize("mode", ["missing", "stale", "future"])
def test_pytest_requires_own_fresh_report(rec, audit, tmp_path, monkeypatch, mode):
    configure_synthetic_pytest(rec, audit, monkeypatch, mode=mode)
    result = run(rec, audit, tmp_path, stage="pytest",
                 command=[sys.executable, "-B", "-m", "pytest", "--version"])
    assert result["status"] == "INCOMPLETE"
    assert result["limited_indicator"] is None

    assert ("D15_REPORT_MISSING" if mode == "missing" else "D15_REPORT_STALE") in result["errors"]


def test_same_filesystem_timestamp_tick_is_fresh(rec, audit, tmp_path, monkeypatch):
    configure_synthetic_pytest(rec, audit, monkeypatch, mode="coarse")
    result = run(rec, audit, tmp_path, stage="pytest",
                 command=[sys.executable, "-B", "-m", "pytest", "--version"])
    assert result["status"] == "COMPLETED", result["errors"]
    assert result["limited_indicator"] == 0
    assert result["hook"]["freshness"]["before_ns"] == result["hook"]["freshness"]["report_mtime_ns"]


def test_pytest_report_is_hash_bound_not_borrowed(rec, audit, tmp_path, monkeypatch):
    report = configure_synthetic_pytest(rec, audit, monkeypatch)
    result = run(rec, audit, tmp_path, stage="pytest",
                 command=[sys.executable, "-B", "-m", "pytest", "--version"])
    assert result["status"] == "COMPLETED"
    assert result["limited_indicator"] == 0
    assert result["hook"]["status"] == "VERIFIED_D15_REPORT"
    assert result["hook"]["refused"] == 0
    assert result["hook"]["sha256"] == hashlib.sha256(report.read_bytes()).hexdigest()


def test_subprocess_refusal_not_hidden_by_summary_zero(rec, audit, tmp_path, monkeypatch):
    rows = [{"kind": "subprocess", "refused": True},
            {"kind": "summary", "exit": 0, "refused": 0, "subprocess": 1}]
    configure_synthetic_pytest(rec, audit, monkeypatch, rows=rows)
    result = run(rec, audit, tmp_path, stage="pytest",
                 command=[sys.executable, "-B", "-m", "pytest", "--version"])
    assert result["status"] == "FAILED"
    assert result["limited_indicator"] > 0
    assert result["hook"]["refused"] == 1


@pytest.mark.parametrize("value", [False, "0", -1])
def test_invalid_hook_counter_cannot_be_zero(rec, audit, tmp_path, monkeypatch, value):
    configure_synthetic_pytest(rec, audit, monkeypatch, rows=[
        {"kind": "summary", "exit": 0, "refused": value, "subprocess": 0}])
    result = run(rec, audit, tmp_path, stage="pytest",
                 command=[sys.executable, "-B", "-m", "pytest", "--version"])
    assert result["status"] == "INCOMPLETE"
    assert result["limited_indicator"] is None

    assert "D15_COUNTER_INVALID" in result["errors"]


def test_hook_configuration_must_not_reuse_parent_report(rec, audit, tmp_path, monkeypatch):
    report = audit / "parent.hook.jsonl"
    report.write_text("old\n", encoding="utf-8")
    monkeypatch.setenv("PYTEST_ADDOPTS", "-p hook_plugin_d15")
    monkeypatch.setenv("WD_D15_HOOK_REPORT", str(report))
    result = run(rec, audit, tmp_path, stage="pytest",
                 command=[sys.executable, "-B", "-m", "pytest", "--version"])
    assert result["status"] == "INCOMPLETE"
    assert result["limited_indicator"] is None
    assert report.read_text() == "old\n"



def test_source_git_blob_sha1_is_explicitly_non_security(rec, monkeypatch):
    class NonSecurityHashes:
        sha256 = staticmethod(hashlib.sha256)

        @staticmethod
        def sha1(value, *, usedforsecurity):
            assert usedforsecurity is False
            return hashlib.sha1(value, usedforsecurity=False)

    # Replace only this loaded module's namespace, never shared hashlib methods.
    monkeypatch.setattr(rec, "hashlib", NonSecurityHashes)
    sha, blob = identity()
    assert rec._source_identity() == {"sha256": sha, "git_blob_lf": blob}
@pytest.mark.parametrize("arguments, expected", [
    (["python.exe", "-mpytest"], True),
    (["python.exe", "-Bmpytest"], True),
    (["python.exe", "-Impytest"], True),
    (["python.exe", "-IBmpytest"], True),
    (["python.exe", "-Im", "pytest"], True),
    (["python.exe", "-B", "-m", "pytest"], True),
    (["pytest.exe", "--version"], True),
    (["py.test", "--version"], True),
    (["python.exe", "pytest-script.py"], True),
    (["python.exe", "-mpytest_extra"], False),
    (["python.exe", "-mpytest.plugins"], False),
    (["python.exe", "-mPytest"], False),
    (["python.exe", "--mpytest"], False),
    (["python.exe", "-Xmpytest"], False),
    (["python.exe", "-Wmpytest"], False),
    (["python.exe", "-Impytest_extra"], False),
    (["python.exe", "-Im", "pytest_extra"], False),
    (["python.exe", "-c", "pass", "-mpytest"], False),
    (["python.exe", "script.py", "-mpytest"], False),
    (["python.exe", "--", "-mpytest"], False),
    (["python.exe", "-m", "other_module", "-mpytest"], False),
    (["python.exe", "-mother_module", "-mpytest"], False),
    (["python.exe", "-W", "-mpytest", "-c", "pass"], False),
    (["python.exe", "-X", "-mpytest", "-c", "pass"], False),
    (["python.exe", "script.py", "pytest-script.py"], False),
    (["python.exe", "-c", "pass", "pytest-script.py"], False),
    (["python.exe", "-mother_module", "pytest-script.py"], False),
    (["python.exe", "-Wignore", "-mpytest"], True),
    (["python.exe", "-IWignore", "-mpytest"], True),
    (["python.exe", "-X", "dev", "-mpytest"], True),
    (["python.exe", "--check-hash-based-pycs", "always", "-mpytest"], True),
])
def test_compact_pytest_guard_and_lookalikes(rec, arguments, expected):
    assert rec._is_pytest(arguments) is expected


@pytest.mark.parametrize("arguments", [
    ["-mpytest"], ["-Impytest"], ["-Im", "pytest"],
])
@pytest.mark.parametrize("missing", ["plugin", "report-config", "report-file"])
def test_compact_pytest_missing_d15_never_reports_success(
        rec, audit, tmp_path, monkeypatch, arguments, missing):
    calls = []
    if missing == "report-file":
        configure_synthetic_pytest(rec, audit, monkeypatch, mode="missing")
    else:
        monkeypatch.setenv("PYTEST_ADDOPTS", "" if missing == "plugin" else "-p hook_plugin_d15")
        monkeypatch.delenv("WD_D15_HOOK_REPORT", raising=False)

        def fake_invoke(*args):
            calls.append(args)
            return 0

        monkeypatch.setattr(rec, "_invoke", fake_invoke)
    result = run(rec, audit, tmp_path, stage="pytest",
                 command=[sys.executable, *arguments, "--version"])
    assert result["status"] == "INCOMPLETE", result
    assert result["limited_indicator"] is None
    assert result["hook"]["status"] == "UNKNOWN"
    expected = {"plugin": "D15_PLUGIN_NOT_CONFIGURED",
                "report-config": "D15_REPORT_NOT_CONFIGURED",
                "report-file": "D15_REPORT_MISSING"}[missing]
    assert expected in result["errors"]
    if missing != "report-file":
        assert not calls  # Missing preconditions must stop before even the fake process.


@pytest.mark.parametrize("arguments", [
    ["-mpytest"], ["-Impytest"], ["-Im", "pytest"],
])
def test_compact_pytest_valid_report_is_still_verified(
        rec, audit, tmp_path, monkeypatch, arguments):
    report = configure_synthetic_pytest(rec, audit, monkeypatch)
    result = run(rec, audit, tmp_path, stage="pytest",
                 command=[sys.executable, *arguments, "--version"])
    assert result["status"] == "COMPLETED", result["errors"]
    assert result["hook"]["status"] == "VERIFIED_D15_REPORT"
    assert result["hook"]["sha256"] == hashlib.sha256(report.read_bytes()).hexdigest()
    assert result["hook"]["refused"] == 0




def _synthetic_unhooked_executor(rec, monkeypatch, tmp_path, executable_name):
    """Metadata and invocation fakes only: no Python/py launcher is executed."""
    executable = Path(tmp_path.anchor) / "r2-synthetic" / executable_name
    assert "pytest" not in str(executable).lower()
    original_meta = rec._file_meta
    calls = []

    def file_meta(path):
        if path == executable:
            return {"path": str(path), "bytes": 1, "sha256": "0" * 64}
        return original_meta(path)

    def invoke(*args):
        calls.append(args)
        return 0

    monkeypatch.setattr(rec, "_file_meta", file_meta)
    monkeypatch.setattr(rec, "_invoke", invoke)
    monkeypatch.setenv("PYTEST_ADDOPTS", "")
    monkeypatch.delenv("WD_D15_HOOK_REPORT", raising=False)
    return str(executable), calls


@pytest.mark.parametrize("executable_name,arguments", [
    pytest.param("py.exe", ["-3", "-m", "pytest"], id="py-selector-3"),
    pytest.param("py.exe", ["-3.13", "-m", "pytest"], id="py-selector-3.13"),
    pytest.param("py.exe", ["-V:3.13", "-m", "pytest"], id="py-selector-V"),
    pytest.param("python.exe", ["-m", "pytest.__main__"], id="package-main"),
    pytest.param("python.exe", ["-c", "import pytest; pytest.main([])"], id="inline"),
    pytest.param("python.exe", ["site-packages/pytest/__main__.py"], id="direct-main"),
    pytest.param("python.exe", ["site-packages/PYTEST/__main__.py"], id="case-insensitive"),
    pytest.param("python.exe", ["--r2-unknown-option", "-c", "pass"], id="unknown-python-option"),
    pytest.param("py.exe", ["--r2-unknown-option", "-c", "pass"], id="unknown-py-option"),
    pytest.param("python.exe", ["-c", "pass", "-mPyTest"], id="inline-tail-lookalike"),
    pytest.param("python.exe", ["script.py", "--PYTEST"], id="script-tail-lookalike"),
    pytest.param("python.exe", ["-m", "other_module", "-mpytest"], id="module-tail-lookalike"),
])
def test_rco_possible_pytest_or_unknown_python_option_requires_d15(
        rec, audit, tmp_path, monkeypatch, executable_name, arguments):
    executable, calls = _synthetic_unhooked_executor(
        rec, monkeypatch, tmp_path, executable_name)
    result = run(rec, audit, tmp_path, stage="rco-form",
                 command=[executable, *arguments])
    assert result["status"] == "INCOMPLETE", result
    assert result["limited_indicator"] is None
    assert result["errors"]  # A named precondition/classification refusal is required.
    assert not calls  # No real launcher, nor even the clean-exit fake, may start.


@pytest.mark.parametrize("arguments", [
    ["-c", "pass"],
    ["-B", "-m", "sandbox.mind.step2.corpus"],
    ["-I", "-m", "sandbox.mind.step2.encode"],
    ["-X", "dev", "-m", "sandbox.mind.step2.memory"],
    ["--check-hash-based-pycs", "always", "-m", "sandbox.mind.step2.run_q4"],
    ["-W", "ignore", "synthetic_judge.py"],
    ["--", "synthetic_judge.py"],
])
def test_rco_known_direct_command_shapes_keep_limited_evidence(
        rec, audit, tmp_path, monkeypatch, arguments):
    executable, calls = _synthetic_unhooked_executor(
        rec, monkeypatch, tmp_path, "python.exe")
    result = run(rec, audit, tmp_path, stage="direct-shape",
                 command=[executable, *arguments])
    assert result["status"] == "COMPLETED", result["errors"]
    assert result["limited_indicator"] == 0
    assert result["hook"]["status"] == "NOT_APPLICABLE"
    assert len(calls) == 1


@pytest.mark.parametrize("executable_name,arguments", [
    pytest.param("pythonw.exe", ["-m", "pytest.__main__"], id="pythonw-package-main"),
    pytest.param("pythonw.exe", ["-c", "import pytest;pytest.main([])"], id="pythonw-inline"),
    pytest.param("pythonw.exe", ["--r2-unknown-option", "-c", "pass"], id="pythonw-unknown-option"),
    pytest.param("cmd.exe", ["/c", "python -m pytest"], id="cmd-pytest"),
    pytest.param("pwsh.exe", ["-NoProfile", "-Command", "python -m pytest"], id="pwsh-pytest"),
    pytest.param("uv.exe", ["run", "pytest"], id="uv-pytest"),
    pytest.param("cmd.exe", ["/c", "py.test"], id="cmd-legacy-alias"),
    pytest.param("pwsh.exe", ["-NoProfile", "-Command", "py.test"], id="pwsh-legacy-alias"),
    pytest.param("uv.exe", ["run", "py.test"], id="uv-legacy-alias"),
    pytest.param("cmd.exe", ["/c", "PY.TEST"], id="legacy-alias-case-insensitive"),
    pytest.param("python.exe", ["-m", "py.test"], id="python-legacy-module"),
    pytest.param("python.exe", ["site-packages/py.test-script.py"], id="python-legacy-script"),
    pytest.param("pwsh.exe", ["-c", "py.test"], id="pwsh-short-command-alias"),
    pytest.param("cmd.exe", ["/c", "py.test.exe"], id="legacy-alias-executable"),
    pytest.param("bash", ["-c", "py.test"], id="bash-legacy-alias"),
])
def test_class_sweep_launcher_and_alias_forms_require_d15(
        rec, audit, tmp_path, monkeypatch, executable_name, arguments):
    executable, calls = _synthetic_unhooked_executor(
        rec, monkeypatch, tmp_path, executable_name)
    result = run(rec, audit, tmp_path, stage="class-sweep",
                 command=[executable, *arguments])
    assert result["status"] == "INCOMPLETE", result
    assert result["limited_indicator"] is None
    assert result["errors"]
    assert not calls

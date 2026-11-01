"""Synthetic-only CLI tests; no live source, launcher, installation or real session."""

import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys

import pytest

from tools import wd_q4_lane_session_cli as cli


def sha(value):
    return hashlib.sha256(value).hexdigest()


def test_runtime_manifest_limit_matches_verifier_and_accepts_large_json(tmp_path):
    from sandbox.mind.step2.runtime_manifest import MAX_MANIFEST_BYTES

    assert cli._FILES["runtime-manifest"] == MAX_MANIFEST_BYTES
    document = {"schema": "wd.q4-runtime-manifest.v1", "generation": "a" * 40,
                "revision": "b" * 40,
                "files": [{"path": f"site/file{i:06d}", "size": 0, "sha256": "0" * 64}
                          for i in range(10000)]}
    raw = (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode()
    assert 1024 * 1024 < len(raw) < MAX_MANIFEST_BYTES
    path = tmp_path / "manifest.json"
    path.write_bytes(raw)
    assert cli._file(str(path), sha(raw), cli._FILES["runtime-manifest"]) == raw
    path.write_bytes(raw + b" " * (MAX_MANIFEST_BYTES + 1 - len(raw)))
    with pytest.raises(ValueError):
        cli._file(str(path), "0" * 64, cli._FILES["runtime-manifest"])


@pytest.fixture
def case(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "_QUARANTINED", False, raising=False)
    monkeypatch.setattr(cli, "_HOLD", [])
    spec = importlib.util.spec_from_file_location("session_client_test_helpers_cli",
        Path(__file__).with_name("test_wd_q4_lane_session_client.py"))
    helpers = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helpers)
    old = helpers.case.__wrapped__(monkeypatch, tmp_path)
    flags = {"lane": old["lane"], "bundle-root": old["bundle_root"], "public-plan": old["public_plan_path"],
             "interpreter": old["executable"], "interpreter-sha256": old["expected_interpreter_sha256"],
             "interpreter-max-bytes": str(old["interpreter_max_bytes"]), "cwd": old["cwd"],
             "timeout-seconds": str(old["timeout_seconds"]), "max-output-bytes": str(old["max_output_bytes"])}
    for flag, key in (("fleet", "fleet_bytes"), ("binding", "binding_bytes"), ("fixed-plan", "fixed_plan_bytes"),
                      ("snapshot", "fixed_bundle_bytes"), ("runtime-manifest", "runtime_manifest_bytes")):
        raw = old[key]
        path = tmp_path / (flag + ".bin")
        path.write_bytes(raw)
        flags[flag], flags[flag + "-sha256"] = str(path), sha(raw)
    args = ["--enabled"]
    for key, value in flags.items(): args.extend(("--" + key, value))
    return args, flags, old


def input_(monkeypatch, data):
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(data), encoding="utf-8"))


def test_outer_deadline_limit_is_explicitly_documented():
    """The test does not simulate a stuck pipe or claim a watchdog exists."""
    doc = cli.__doc__
    assert "stdin.read" in doc and "stdout.write" in doc and "stdout.flush" in doc
    assert "whole-process external watchdog" in doc and "not implemented" in doc


def test_stdout_acceptance_and_irreversible_partial_write_are_documented():
    doc = cli.__doc__
    assert "exit 0" in doc and "newline" in doc and "discard stdout" in doc
    assert "cannot retract" in doc


def test_disabled_does_not_read_stdin_or_config(monkeypatch, capsys):
    class Unreadable:
        @property
        def buffer(self): pytest.fail("read while disabled")
    monkeypatch.setattr(sys, "stdin", Unreadable())
    assert cli.main([]) == 2
    captured = capsys.readouterr()
    assert captured.out == "" and "disabled" in captured.err


def test_real_synthetic_session_roundtrip(case, monkeypatch, capsys):
    args, _, old = case
    input_(monkeypatch, b"synthetic situation\n")
    assert cli.main(args) == 0
    captured = capsys.readouterr()
    answer = json.loads(captured.out)
    assert answer["status"] == "ANSWER" and answer["result_count"] == len(old["_expected"])
    assert answer["trust"]["lane_authentication"] == "UNKNOWN" and answer["trust"]["freshness"] == "UNKNOWN"
    assert "synthetic situation" not in captured.out and captured.err == ""


@pytest.mark.parametrize("field", ["fleet", "binding", "fixed-plan", "snapshot", "runtime-manifest"])
def test_bad_external_pin_refuses_before_query(case, monkeypatch, capsys, field):
    args, flags, _ = case
    args[args.index("--" + field + "-sha256") + 1] = "0" * 64
    input_(monkeypatch, b"synthetic private question")
    assert cli.main(args) == 2
    captured = capsys.readouterr()
    assert captured.out == "" and "synthetic private question" not in captured.err and flags[field] not in captured.err


@pytest.mark.parametrize("kind", ["oversize", "invalid_utf8", "empty"])
def test_bad_stdin_is_refused_without_echo(case, monkeypatch, capsys, kind):
    args, _, _ = case
    input_(monkeypatch, {"oversize": b"x" * (cli.MAX_STDIN + 1), "invalid_utf8": b"\xff", "empty": b""}[kind])
    assert cli.main(args) == 2
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("field", ["duplicate", "unknown", "missing", "nonabsolute"])
def test_malformed_cli_arguments_refuse(case, monkeypatch, capsys, field):
    args, _, _ = case
    if field == "duplicate": args += ["--lane", "fable-5"]
    if field == "unknown": args += ["--unexpected", "value"]
    if field == "missing": args = args[:-2]
    if field == "nonabsolute": args[args.index("--snapshot") + 1] = "relative.bin"
    input_(monkeypatch, b"synthetic")
    assert cli.main(args) == 2
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("kind", ["timeout", "transport_cleanup", "attestation_cleanup"])
def test_critical_failure_is_distinct_and_never_echoes(case, monkeypatch, capsys, kind):
    from tools.wd_q4_fixed_parent_attestation import AttestationCleanupUnknown
    from tools.wd_q4_secure_transport import TransportCleanupUnknown
    args, _, _ = case
    input_(monkeypatch, b"synthetic private question")
    failure = {"timeout": TimeoutError("private"), "transport_cleanup": TransportCleanupUnknown("private"),
               "attestation_cleanup": AttestationCleanupUnknown(object())}[kind]
    def fail(**_): raise failure
    monkeypatch.setattr(cli.session, "query_session", fail)
    assert cli.main(args) == (4 if kind == "timeout" else 3)
    captured = capsys.readouterr()
    assert captured.out == "" and "private" not in captured.err


@pytest.mark.parametrize("kind", ["transport_cleanup", "attestation_cleanup"])
def test_cleanup_unknown_prevents_a_second_query_in_same_process(case, monkeypatch, capsys, kind):
    from tools.wd_q4_fixed_parent_attestation import AttestationCleanupUnknown
    from tools.wd_q4_secure_transport import TransportCleanupUnknown
    args, _, _ = case
    input_(monkeypatch, b"synthetic")
    error = TransportCleanupUnknown("timeout") if kind == "transport_cleanup" else AttestationCleanupUnknown(object())
    def fail(**_): raise error
    monkeypatch.setattr(cli.session, "query_session", fail)
    assert cli.main(args) == 3
    capsys.readouterr()
    monkeypatch.setattr(cli.session, "query_session", lambda **_: pytest.fail("restarted after unknown cleanup"))
    assert cli.main(args) == 3
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("kind", ["short_write", "flush_failure"])
def test_output_failure_requires_caller_to_discard_even_parseable_stdout(case, monkeypatch, capsys, kind):
    args, _, _ = case
    input_(monkeypatch, b"synthetic private question")
    monkeypatch.setattr(cli.session, "query_session", lambda **_: {"schema": "synthetic", "status": "ANSWER"})
    class Buffer:
        def __init__(self): self.output = b""
        def write(self, data):
            self.output += data[:-1] if kind == "short_write" else data
            return len(data) - 1 if kind == "short_write" else len(data)
        def flush(self):
            if kind == "flush_failure": raise OSError("synthetic private failure")
    sink = Buffer()
    with monkeypatch.context() as local:
        local.setattr(sys, "stdout", type("Output", (), {"buffer": sink})())
        assert cli.main(args) == 2
    assert json.loads(sink.output)["status"] == "ANSWER"  # bytes cannot be retracted; exit 2 makes them unusable
    assert "private" not in capsys.readouterr().err


@pytest.mark.parametrize("code,expected", [(7, 7), (0, 2), (3, 2), (4, 2), (130, 2), (True, 2),
                                          ("SYNTHETIC_PRIVATE_MARKER", 2)])
def test_system_exit_is_sanitized_and_preserves_only_safe_failure_code(case, monkeypatch, capsys, code, expected):
    args, _, _ = case
    input_(monkeypatch, b"SYNTHETIC_PRIVATE_MARKER")
    def fail(**_): raise SystemExit(code)
    monkeypatch.setattr(cli.session, "query_session", fail)
    assert cli.main(args) == expected
    captured = capsys.readouterr()
    assert captured.out == "" and "SYNTHETIC_PRIVATE_MARKER" not in captured.err

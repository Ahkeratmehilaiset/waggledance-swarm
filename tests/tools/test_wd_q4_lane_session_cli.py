"""Synthetic-only CLI tests; no live source, launcher, installation or real session."""

from datetime import datetime, timedelta, timezone
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


EXACT_UTC = "2026-10-31T12:00:00.000000Z"


def exact_query(situation="synthetic exact situation", time="2026-10-31T12:00:00Z"):
    return json.dumps({"situation": situation, "time": time}, separators=(",", ":")).encode()


class _NoClock(datetime):
    @classmethod
    def now(cls, tz=None):
        raise AssertionError("wall clock read in exact-time mode")


def spy(monkeypatch):
    calls = []
    def query_session(**kw):
        calls.append(kw)
        return {"schema": "synthetic", "status": "ANSWER"}
    monkeypatch.setattr(cli.session, "query_session", query_session)
    return calls


def test_exact_query_time_reaches_query_session_without_reading_the_clock(case, monkeypatch, capsys):
    args, _, _ = case
    calls = spy(monkeypatch)
    monkeypatch.setattr(cli, "datetime", _NoClock)
    input_(monkeypatch, exact_query() + b"\n")
    assert cli.main(args + ["--query-utc", EXACT_UTC]) == 0
    assert len(calls) == 1 and calls[0]["situation"] == "synthetic exact situation"
    when = calls[0]["time"]
    assert when == datetime(2026, 10, 31, 12, 0, 0, tzinfo=timezone.utc)
    assert when.utcoffset() == timedelta(0) and when.microsecond == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out)["status"] == "ANSWER" and captured.err == ""
    assert "synthetic exact situation" not in captured.out


def test_exact_query_time_real_synthetic_session_roundtrip(case, monkeypatch, capsys):
    args, _, old = case
    when = old["time"].astimezone(timezone.utc)
    assert when.microsecond == 0
    input_(monkeypatch, exact_query(old["situation"], when.strftime("%Y-%m-%dT%H:%M:%SZ")))
    assert cli.main(args + ["--query-utc", when.strftime("%Y-%m-%dT%H:%M:%S.000000Z")]) == 0
    captured = capsys.readouterr()
    answer = json.loads(captured.out)
    assert answer["status"] == "ANSWER" and answer["result_count"] == len(old["_expected"])
    assert answer["trust"]["freshness"] == "UNKNOWN" and captured.err == ""


@pytest.mark.parametrize("kind", [
    "second_later", "second_earlier", "fractional_document_time", "offset_document_time", "date_only",
    "extra_key", "missing_time", "missing_situation", "duplicate_key", "nan_constant", "situation_number",
    "time_number", "blank_situation", "control_character", "oversize_situation", "raw_text", "json_list",
    "invalid_utf8"])
def test_exact_query_mismatch_or_malformed_document_refuses_before_transport(case, monkeypatch, capsys, kind):
    args, _, _ = case
    calls = spy(monkeypatch)
    marker = "SYNTHETIC_PRIVATE_SITUATION"
    data = {
        "second_later": exact_query(marker, "2026-10-31T12:00:01Z"),
        "second_earlier": exact_query(marker, "2026-10-31T11:59:59Z"),
        "fractional_document_time": exact_query(marker, "2026-10-31T12:00:00.000000Z"),
        "offset_document_time": exact_query(marker, "2026-10-31T12:00:00+00:00"),
        "date_only": exact_query(marker, "2026-10-31"),
        "extra_key": json.dumps({"situation": marker, "time": "2026-10-31T12:00:00Z", "lane": "fable-5"}).encode(),
        "missing_time": json.dumps({"situation": marker}).encode(),
        "missing_situation": json.dumps({"time": "2026-10-31T12:00:00Z"}).encode(),
        "duplicate_key": ('{"situation":"%s","time":"2026-10-31T12:00:00Z","time":"2026-10-31T12:00:00Z"}'
                          % marker).encode(),
        "nan_constant": ('{"situation":"%s","time":NaN}' % marker).encode(),
        "situation_number": json.dumps({"situation": 7, "time": "2026-10-31T12:00:00Z"}).encode(),
        "time_number": json.dumps({"situation": marker, "time": 1793448000}).encode(),
        "blank_situation": exact_query("   "),
        "control_character": exact_query(marker + "\u0007"),
        "oversize_situation": exact_query(marker + "x" * 4000),
        "raw_text": marker.encode(),
        "json_list": json.dumps([marker, "2026-10-31T12:00:00Z"]).encode(),
        "invalid_utf8": b'{"situation":"\xff","time":"2026-10-31T12:00:00Z"}',
    }[kind]
    input_(monkeypatch, data)
    assert cli.main(args + ["--query-utc", EXACT_UTC]) == 2
    assert calls == []  # rejected before query_session, so before any transport or peer process
    captured = capsys.readouterr()
    assert captured.out == "" and marker not in captured.err


@pytest.mark.parametrize("value", [
    "2026-10-31T12:00:00Z", "2026-10-31T12:00:00.000001Z", "2026-10-31T12:00:00.000000+00:00",
    "2026-10-31 12:00:00.000000Z", "2026-02-30T12:00:00.000000Z", "2026-10-31T24:00:00.000000Z",
    "2026-10-31t12:00:00.000000z", " 2026-10-31T12:00:00.000000Z", "２026-10-31T12:00:00.000000Z"])
def test_malformed_query_utc_refuses_before_stdin_is_read(case, monkeypatch, capsys, value):
    args, _, _ = case
    class Unreadable:
        @property
        def buffer(self): pytest.fail("stdin read with a malformed query time")
    monkeypatch.setattr(sys, "stdin", Unreadable())
    calls = spy(monkeypatch)
    assert cli.main(args + ["--query-utc", value]) == 2
    assert calls == [] and capsys.readouterr().out == ""


@pytest.mark.parametrize("shape", ["duplicate", "missing_value", "empty_value"])
def test_query_utc_option_shape_refuses(case, monkeypatch, capsys, shape):
    args, _, _ = case
    calls = spy(monkeypatch)
    extra = {"duplicate": ["--query-utc", EXACT_UTC, "--query-utc", EXACT_UTC],
             "missing_value": ["--query-utc"], "empty_value": ["--query-utc", ""]}[shape]
    input_(monkeypatch, exact_query())
    assert cli.main(args + extra) == 2
    assert calls == [] and capsys.readouterr().out == ""


def test_default_mode_keeps_raw_situation_and_its_own_wall_clock(case, monkeypatch, capsys):
    args, _, _ = case
    calls = spy(monkeypatch)
    chosen = datetime(2031, 1, 2, 3, 4, 5, 678901, tzinfo=timezone.utc)
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            assert tz is timezone.utc
            return chosen
    monkeypatch.setattr(cli, "datetime", Clock)
    raw = exact_query()  # a JSON-shaped stdin stays one raw situation string without --query-utc
    input_(monkeypatch, raw + b"\n")
    assert cli.main(args) == 0
    assert len(calls) == 1 and calls[0]["situation"] == raw.decode() and calls[0]["time"] is chosen
    capsys.readouterr()


def test_exact_time_mode_is_documented_without_freshness_or_authentication_claims():
    doc = cli.__doc__
    assert "--query-utc" in doc and "same whole second" in doc and "datetime.now" in doc
    assert "not authentication" in doc and "freshness" in doc

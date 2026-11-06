"""Synthetic in-process status CLI tests; no fleet, live process or model."""
import hashlib
import importlib
import io
import json

import pytest


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("ascii")


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def cli():
    return importlib.import_module("tools.wd_mind_status_reader_v3_cli")


@pytest.fixture
def spec(tmp_path):
    from tools import wd_mind_status_projection_v3 as projection
    body = encoded({"schema": "wd.mind-status-metadata.v1", "authority": "none",
        "scope": "in_memory_metadata_not_gate", "freshness": "UNKNOWN", "records": [
            {"time_utc": "2026-10-31T12:00:00.000000Z", "agent": "fable-5", "type": "message",
             "status": "done", "task_id": "public-task"}]})
    descriptor = encoded({"schema": "wd.mind-public-status-policy.v1", "public_tasks": ["public-task"],
                          "public_types": ["message"]})
    approval = encoded({"schema": "wd.mind-public-approval.v1", "descriptor_sha256": digest(descriptor),
                        "approved_tasks": ["public-task"], "approved_types": ["message"]})
    projected = projection.project_status(enabled=True, body_bytes=body, body_sha256=digest(body),
        descriptor_bytes=descriptor, descriptor_sha256=digest(descriptor), approval_bytes=approval,
        expected_approval_sha256=digest(approval))
    raw = encoded(projected)
    files = {}
    for name, data in (("projection", raw), ("descriptor", descriptor), ("approval", approval)):
        path = tmp_path / (name + ".json")
        path.write_bytes(data)
        files[name + "_path"], files[name + "_sha256"] = str(path), digest(data)
    return {**files, "caller_label": "codex-lead-1"}


def run(argv, raw):
    out = io.BytesIO()
    code = cli().main(argv=argv, stdin=io.BytesIO(raw), stdout=out)
    return code, json.loads(out.getvalue())


def test_default_off_does_not_read_input():
    class Poison:
        def read(self, *_):
            pytest.fail("default-off read input")
    out = io.BytesIO()
    assert cli().main(argv=[], stdin=Poison(), stdout=out) == 0
    assert json.loads(out.getvalue()) == {"status": "OFF"}


@pytest.mark.parametrize("label", ["codex-lead-1", "codex-tools-1", "claude-rco-1", "claude-rco-2", "fable-5"])
def test_five_synthetic_declared_callers_receive_only_reader_projection(spec, label):
    spec["caller_label"] = label
    code, value = run(["--enabled"], encoded(spec))
    assert code == 0 and value["schema"] == "wd.mind-status-projection.v3"
    assert value["coverage"] == "UNKNOWN" and value["trust"]["source_freshness"] == "UNKNOWN"
    assert "caller_label" not in value and "identity_verification" not in value
    assert value["records"][0]["task_id"] == "public-task"
    assert str(spec["approval_path"]) not in json.dumps(value)


@pytest.mark.parametrize("kind", ["missing_pin", "wrong_projection", "wrong_descriptor", "wrong_approval",
                                  "forged_approval", "malformed", "duplicate", "unknown_label", "extra",
                                  "oversized", "extra_argv"])
def test_bad_inputs_fail_fixed_without_echo(spec, kind):
    if kind == "missing_pin": del spec["approval_sha256"]
    elif kind == "wrong_projection": spec["projection_sha256"] = "0" * 64
    elif kind == "wrong_descriptor": spec["descriptor_sha256"] = "0" * 64
    elif kind == "wrong_approval": spec["approval_sha256"] = "0" * 64
    elif kind == "forged_approval":
        spec["approval_path"] = spec["descriptor_path"]
        spec["approval_sha256"] = spec["descriptor_sha256"]
    elif kind == "unknown_label": spec["caller_label"] = "synthetic-private-unknown"
    elif kind == "extra": spec["synthetic-private-key"] = "x"
    raw = encoded(spec)
    if kind == "malformed": raw = b'{"synthetic-private-marker":'
    if kind == "duplicate": raw = b'{"caller_label":"x","caller_label":"synthetic-private-marker"}'
    if kind == "oversized": raw = b"x" * (cli().MAX_REQUEST_BYTES + 1)
    args = ["--enabled", "synthetic-private-marker"] if kind == "extra_argv" else ["--enabled"]
    code, value = run(args, raw)
    assert code == 2 and value == {"status": "REFUSED"}
    assert "synthetic-private" not in json.dumps(value)


def test_untrusted_approval_refuses_before_projection_read(spec, monkeypatch):
    m = cli()
    spec["approval_sha256"] = "0" * 64
    monkeypatch.setattr(m.reader, "read_projection", lambda **_: pytest.fail("projection must not be read"))
    assert run(["--enabled"], encoded(spec)) == (2, {"status": "REFUSED"})


@pytest.mark.parametrize("result", ["short", "zero", "none", "exception", "boolean"])
@pytest.mark.parametrize("route", ["off", "success", "refusal"])
def test_incomplete_or_failed_stdout_never_reports_success(spec, result, route):
    class Sink:
        def write(self, payload):
            if result == "exception":
                raise OSError("synthetic-private-error")
            return {"short": len(payload) - 1, "zero": 0, "none": None, "boolean": True}[result]

        def flush(self):
            pass

    args = {"off": [], "success": ["--enabled"], "refusal": ["--bad"]}[route]
    assert cli().main(argv=args, stdin=io.BytesIO(encoded(spec)), stdout=Sink()) == 2


def test_fully_written_output_and_flush_reports_success(spec):
    class Sink(io.BytesIO):
        flushed = False

        def flush(self):
            self.flushed = True

    out = Sink()
    assert cli().main(argv=["--enabled"], stdin=io.BytesIO(encoded(spec)), stdout=out) == 0
    assert out.flushed and json.loads(out.getvalue())["schema"] == "wd.mind-status-projection.v3"


def test_flush_failure_never_reports_success(spec):
    class Sink(io.BytesIO):
        def flush(self):
            raise OSError("synthetic-private-error")

    assert cli().main(argv=["--enabled"], stdin=io.BytesIO(encoded(spec)), stdout=Sink()) == 2


@pytest.mark.parametrize("failure", ["missing", "raising"])
def test_unavailable_default_stdout_refuses_without_reading_stdin(monkeypatch, failure):
    class NoBuffer:
        pass

    class BrokenBuffer:
        @property
        def buffer(self):
            raise OSError("synthetic-private-error")

    class Poison:
        def read(self, *_):
            pytest.fail("unavailable stdout must not read stdin")

    monkeypatch.setattr(cli().sys, "stdout", NoBuffer() if failure == "missing" else BrokenBuffer())
    assert cli().main(argv=["--enabled"], stdin=Poison()) == 2


def test_output_budget_counts_newline_at_exact_limit_and_over(monkeypatch):
    module = cli()
    raw = b'{"key":"value"}\n'
    monkeypatch.setattr(module, "MAX_OUTPUT_BYTES", len(raw))
    assert module._encode({"key": "value"}) == raw
    monkeypatch.setattr(module, "MAX_OUTPUT_BYTES", len(raw) - 1)
    with pytest.raises(ValueError):
        module._encode({"key": "value"})

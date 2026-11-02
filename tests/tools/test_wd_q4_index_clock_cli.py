"""Synthetic one-shot invocation of the caller-clock index runner; no live data, scheduler or real model.

The real-producer cases call ``main`` in process so the fake encoder backend fixture applies; a separate
subprocess case proves the module is a runnable non-test entry point on its OFF and refusal paths.
"""
import contextlib
from datetime import datetime, timezone
import hashlib
import importlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from sandbox.mind.step2.test_wd_q4_prefix_build_child import backend, model_dir  # noqa: F401

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = "wd.q4-index-clock-config.v1"
SENTINEL = "sentinelindexcli4c1d"
OUTPUT_KEYS = {"status", "clock", "cutoff_utc", "child"}
CHILD_KEYS = {"status", "pointer_sha256", "generation", "coverage", "freshness", "source_authenticity",
              "publisher_authentication", "source_refusals", "plan_sha256", "encoder_identity_source"}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def cli():
    return importlib.import_module("tools.wd_q4_index_clock_cli")


def run_main(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli().main(argv)
    return code, out.getvalue(), err.getvalue()


def _pinned(tmp_path, name, data):
    path = tmp_path / "pins" / name
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(data)
    return {"path": str(path), "sha256": sha(data)}


def _document(tmp_path, request, args):
    source, config, start = request.config.source, request.config, request.start_prefix
    return {"schema": SCHEMA,
            "plan": _pinned(tmp_path, "plan.json", args["plan_bytes"]),
            "anchor": _pinned(tmp_path, "anchor.json", source.anchor_bytes),
            "policy": _pinned(tmp_path, "policy.json", source.policy_bytes),
            "roster": _pinned(tmp_path, "roster.json", source.roster_bytes),
            "public_policy": _pinned(tmp_path, "public-policy.json", config.public_policy_bytes),
            "source_path": str(source.source), "generation_path": str(source.generation_path),
            "source_cursor": {"offset": source.cursor.offset, "file_identity": source.cursor.file_identity,
                              "generation": source.cursor.generation},
            "max_bytes": source.max_bytes, "max_rows": source.max_rows,
            "start_prefix": {"offset": start.cursor.offset, "file_identity": start.cursor.file_identity,
                             "generation": start.cursor.generation, "sha256": start.sha256},
            "root": str(request.root), "private_state_root": str(request.private_state_root),
            "trusted_model_root": str(args["trusted_model_root"])}


def _write(tmp_path, document, raw=None):
    data = raw if raw is not None else json.dumps(document, sort_keys=True).encode("ascii")
    path = tmp_path / "index-clock-config.json"
    path.write_bytes(data)
    return path, sha(data)


def _argv(path, digest, cutoff="2026-10-31T12:01:00Z"):
    return ["--enabled", "--config", str(path), "--config-sha256", digest, "--cutoff-utc", cutoff]


def _real_case(tmp_path, model_dir):
    pytest.importorskip("faiss", reason="real FAISS required for the producer integration")
    if os.name != "nt":
        pytest.skip("private checkpoint requires a Windows fixed volume")
    from sandbox.mind.step2.test_wd_q4_index_clock_runner import _case
    request, fixture, args = _case(tmp_path, model_dir)
    return request, fixture, args, _document(tmp_path, request, args)


def _spy_runner(monkeypatch):
    runner = importlib.import_module("tools.wd_q4_index_clock_runner")
    calls = []
    original = runner.run

    def spy(**kwargs):
        calls.append(kwargs.get("caller_cutoff_utc"))
        return original(**kwargs)

    monkeypatch.setattr(runner, "run", spy)
    return calls


# 1 the absent non-test invocation, OFF by default ----------------------------------------------------------------

def test_off_by_default_reads_nothing(tmp_path):
    assert cli().invoke() == {"status": "OFF"}
    missing = tmp_path / "missing.json"
    for argv in ([], ["--config", str(missing)], ["--config-sha256", "0" * 64, "--cutoff-utc", "x"]):
        code, out, err = run_main(argv)
        assert (code, err) == (0, "") and json.loads(out) == {"status": "OFF"}


def test_the_module_runs_as_a_non_test_entry_point():
    env = {k: v for k, v in os.environ.items() if not k.startswith("WD_")}
    off = subprocess.run([sys.executable, "-B", "-m", "tools.wd_q4_index_clock_cli"], cwd=ROOT, env=env,
                         capture_output=True, text=True, timeout=60)
    assert (off.returncode, off.stderr, json.loads(off.stdout)) == (0, "", {"status": "OFF"})
    refused = subprocess.run([sys.executable, "-B", "-m", "tools.wd_q4_index_clock_cli", "--enabled", "--config",
                              str(ROOT / ("missing-" + SENTINEL + ".json")), "--config-sha256", "0" * 64,
                              "--cutoff-utc", "2026-10-31T12:01:00Z"],
                             cwd=ROOT, env=env, capture_output=True, text=True, timeout=60)
    assert (refused.returncode, refused.stdout, refused.stderr) == (2, "", "index_cli_refused\n")


# 2 one call through the real fenced producer ----------------------------------------------------------------------

@pytest.mark.parametrize("minute,coverage", [(1, "partial_ready_prefix"), (2, "caught_up_to_final_snapshot_not_history")])
def test_one_configured_call_publishes_through_the_real_fenced_producer(tmp_path, model_dir, backend, monkeypatch,
                                                                         minute, coverage):
    from sandbox.mind.step2 import shared_index_checkpoint as checkpoint
    request, _, _, document = _real_case(tmp_path, model_dir)
    path, digest = _write(tmp_path, document)
    calls = _spy_runner(monkeypatch)
    code, out, err = run_main(_argv(path, digest, f"2026-10-31T12:0{minute}:00Z"))
    assert (code, err) == (0, "") and out.endswith("\n") and out.count("\n") == 1
    result = json.loads(out)
    assert set(result) == OUTPUT_KEYS and set(result["child"]) == CHILD_KEYS
    assert result["status"] == "INDEX_RUN_COMPLETE" and result["clock"] == "CALLER_UNVERIFIED"
    assert result["cutoff_utc"] == f"2026-10-31T12:0{minute}:00+00:00"
    assert calls == [datetime(2026, 10, 31, 12, minute, tzinfo=timezone.utc)]
    child = result["child"]
    assert child["status"] == "PUBLISHED_GENESIS" and child["coverage"] == coverage
    assert child["freshness"] == "UNKNOWN" and child["source_authenticity"] == "UNKNOWN"
    assert child["plan_sha256"] == document["plan"]["sha256"]
    assert (request.root / checkpoint.POINTER).is_file()
    assert "synthetic-private" not in out


def test_unknown_after_a_real_commit_exits_3_once_and_leaves_the_fence(tmp_path, model_dir, backend, monkeypatch):
    from sandbox.mind.step2 import shared_index_checkpoint as checkpoint
    request, _, _, document = _real_case(tmp_path, model_dir)
    path, digest = _write(tmp_path, document)
    original, commits = checkpoint.commit_prefix_genesis, []

    def after_commit(*args, **kwargs):
        commits.append(original(*args, **kwargs))
        raise OSError("synthetic ambiguous commit " + SENTINEL)

    monkeypatch.setattr(checkpoint, "commit_prefix_genesis", after_commit)
    calls = _spy_runner(monkeypatch)
    code, out, err = run_main(_argv(path, digest))
    assert (code, out, err) == (3, "", "index_outcome_unknown\n")
    assert len(calls) == 1 and len(commits) == 1
    assert (request.root / checkpoint.POINTER).is_file() and (request.root / checkpoint.FENCE).is_file()


# 3 refusals before the runner -------------------------------------------------------------------------------------

def _synthetic_document(tmp_path):
    root = tmp_path / "state"
    return {"schema": SCHEMA,
            **{name: _pinned(tmp_path, name + ".bin", (name + "-synthetic").encode())
               for name in ("plan", "anchor", "policy", "roster", "public_policy")},
            "source_path": str(tmp_path / "log" / "events.jsonl"), "generation_path": str(tmp_path / "log" / "gen"),
            "source_cursor": {"offset": 0, "file_identity": "synthetic-identity", "generation": None},
            "max_bytes": 1 << 20, "max_rows": 1,
            "start_prefix": {"offset": 0, "file_identity": "synthetic-identity", "generation": None,
                             "sha256": sha(b"")},
            "root": str(root), "private_state_root": str(tmp_path / "private"),
            "trusted_model_root": str(tmp_path / "model")}


def _set(key, value):
    return lambda d, tmp_path: d.__setitem__(key, value)


def _nested(section, key, value):
    return lambda d, tmp_path: d[section].__setitem__(key, value)


def _drop(key):
    return lambda d, tmp_path: d.pop(key)


def _tamper(name):
    return lambda d, tmp_path: Path(d[name]["path"]).write_bytes(b"changed")


def _oversize(name, size):
    def change(d, tmp_path):
        Path(d[name]["path"]).write_bytes(b"x" * size)
        d[name]["sha256"] = sha(b"x" * size)
    return change


CONFIG_CASES = {
    "wrong-schema": _set("schema", "wd.q4-index-clock-config.v2"),
    "unknown-key": _set("query", "what is new"),
    "missing-key": _drop("root"),
    "relative-root": _set("root", "relative\\state"),
    "relative-source": _set("source_path", "events.jsonl"),
    "bad-plan-pin": _nested("plan", "sha256", "A" * 64),
    "extra-pin-key": _nested("policy", "note", "x"),
    "relative-pin-path": _nested("roster", "path", "roster.json"),
    "tampered-policy": _tamper("policy"),
    "tampered-anchor": _tamper("anchor"),
    "oversized-plan": _oversize("plan", (1 << 16) + 1),
    "oversized-anchor": _oversize("anchor", 16385),
    "negative-offset": _nested("source_cursor", "offset", -1),
    "bool-offset": _nested("start_prefix", "offset", True),
    "int-identity": _nested("source_cursor", "file_identity", 7),
    "bad-prefix-pin": _nested("start_prefix", "sha256", "0" * 63),
    "bool-max-rows": _set("max_rows", True),
    "zero-max-bytes": _set("max_bytes", 0),
    "string-max-bytes": _set("max_bytes", "1048576"),
    "cursor-extra-key": _nested("source_cursor", "sha256", "0" * 64),
}


@pytest.mark.parametrize("change", list(CONFIG_CASES.values()), ids=list(CONFIG_CASES))
def test_an_invalid_config_refuses_before_the_runner(tmp_path, monkeypatch, change):
    document = _synthetic_document(tmp_path)
    change(document, tmp_path)
    path, digest = _write(tmp_path, document)
    calls = _spy_runner(monkeypatch)
    assert run_main(_argv(path, digest)) == (2, "", "index_cli_refused\n")
    assert calls == []


@pytest.mark.parametrize("raw", [b'{"schema":"wd.q4-index-clock-config.v1","schema":"x"}',
                                 b'{"schema":"wd.q4-index-clock-config.v1","SCHEMA":"x"}',
                                 b"[]", b"\xef\xbb\xbf{}", b'{"max_bytes":NaN}', b"x" * 65537],
                         ids=["duplicate-key", "case-colliding-key", "array", "bom", "nan", "oversized"])
def test_a_malformed_config_file_refuses(tmp_path, monkeypatch, raw):
    path, digest = _write(tmp_path, None, raw=raw)
    calls = _spy_runner(monkeypatch)
    assert run_main(_argv(path, digest)) == (2, "", "index_cli_refused\n")
    assert calls == []


@pytest.mark.parametrize("cutoff", ["2026-10-31T12:01:00", "2026-10-31T12:01:00+00:00", "2026-10-31T14:01:00+02:00",
                                    "2026-10-31T12:01:00.000Z", "2026-10-31 12:01:00Z", "2026-13-31T12:01:00Z",
                                    "1761912060", "", "now", "latest"])
def test_only_an_explicit_utc_second_cutoff_is_accepted(tmp_path, monkeypatch, cutoff):
    document = _synthetic_document(tmp_path)
    path, digest = _write(tmp_path, document)
    calls = _spy_runner(monkeypatch)
    assert run_main(_argv(path, digest, cutoff)) == (2, "", "index_cli_refused\n")
    assert calls == []


@pytest.mark.parametrize("argv", [
    lambda p, d: ["--enabled", "--config", str(p), "--config-sha256", d],
    lambda p, d: ["--enabled", "--config", str(p), "--config-sha256", "0" * 64, "--cutoff-utc", "2026-10-31T12:01:00Z"],
    lambda p, d: _argv(p, d) + ["--query", "what is new"],
    lambda p, d: _argv(p, d) + ["--cutoff-utc", "2026-10-31T12:02:00Z"],
    lambda p, d: _argv(p, d) + ["--enabled"],
    lambda p, d: ["--enabled", "--config", "relative.json", "--config-sha256", d, "--cutoff-utc", "2026-10-31T12:01:00Z"],
], ids=["missing-cutoff", "wrong-config-pin", "query-flag", "duplicate-cutoff", "duplicate-enabled", "relative-config"])
def test_argv_is_closed_and_bound_to_the_config_pin(tmp_path, monkeypatch, argv):
    path, digest = _write(tmp_path, _synthetic_document(tmp_path))
    calls = _spy_runner(monkeypatch)
    assert run_main(argv(path, digest)) == (2, "", "index_cli_refused\n")
    assert calls == []


def test_a_runner_preflight_refusal_maps_to_exit_2(tmp_path, monkeypatch):
    path, digest = _write(tmp_path, _synthetic_document(tmp_path))
    runner = importlib.import_module("tools.wd_q4_index_clock_runner")

    def refuse(**kwargs):
        raise runner.IndexRunnerRefused("index_request_refused")

    monkeypatch.setattr(runner, "run", refuse)
    assert run_main(_argv(path, digest)) == (2, "", "index_cli_refused\n")


def test_a_runner_unknown_maps_to_exit_3_and_is_not_retried(tmp_path, monkeypatch):
    path, digest = _write(tmp_path, _synthetic_document(tmp_path))
    runner = importlib.import_module("tools.wd_q4_index_clock_runner")
    calls = []

    def unknown(**kwargs):
        calls.append(kwargs)
        raise runner.IndexRunnerOutcomeUnknown("index_outcome_unknown")

    monkeypatch.setattr(runner, "run", unknown)
    assert run_main(_argv(path, digest)) == (3, "", "index_outcome_unknown\n")
    assert len(calls) == 1


@pytest.mark.parametrize("answer", [None, {"status": "INDEX_RUN_COMPLETE"}, {"status": "OFF"},
                                    {"status": "INDEX_RUN_COMPLETE", "clock": "CALLER_UNVERIFIED",
                                     "cutoff_utc": "2026-10-31T12:01:00+00:00", "child": {"status": "PUBLISHED_GENESIS"}}])
def test_an_unprojectable_runner_result_is_unknown_not_success(tmp_path, monkeypatch, answer):
    path, digest = _write(tmp_path, _synthetic_document(tmp_path))
    runner = importlib.import_module("tools.wd_q4_index_clock_runner")
    monkeypatch.setattr(runner, "run", lambda **kwargs: answer)
    assert run_main(_argv(path, digest)) == (3, "", "index_outcome_unknown\n")


COMPLETE = {"status": "INDEX_RUN_COMPLETE", "clock": "CALLER_UNVERIFIED", "cutoff_utc": "2026-10-31T12:01:00+00:00",
            "child": {key: "UNKNOWN" for key in CHILD_KEYS}}


class _BrokenStdout(io.StringIO):
    def __init__(self, mode):
        super().__init__()
        self.mode = mode

    def write(self, text):
        if self.mode == "raise":
            raise OSError("synthetic stdout failure")
        if self.mode == "interrupt":
            raise KeyboardInterrupt("synthetic " + SENTINEL)
        if self.mode == "short":
            super().write(text[:-1])
            return len(text) - 1
        return super().write(text)

    def flush(self):
        if self.mode == "flush":
            raise OSError("synthetic flush failure")
        if self.mode == "exit":
            raise SystemExit("synthetic " + SENTINEL)


@pytest.mark.parametrize("mode", ["raise", "short", "flush", "interrupt", "exit"])
def test_a_failed_receipt_after_the_run_is_unknown_not_success(tmp_path, monkeypatch, mode):
    path, digest = _write(tmp_path, _synthetic_document(tmp_path))
    runner = importlib.import_module("tools.wd_q4_index_clock_runner")
    calls = []
    monkeypatch.setattr(runner, "run", lambda **kwargs: calls.append(kwargs) or COMPLETE)
    out, err = _BrokenStdout(mode), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli().main(_argv(path, digest))
    assert (code, err.getvalue()) == (3, "index_outcome_unknown\n") and len(calls) == 1
    assert SENTINEL not in out.getvalue() + err.getvalue()


@pytest.mark.parametrize("mode, expected", [("interrupt", (130, "index_cli_interrupted\n")),
                                            ("raise", (2, "index_cli_refused\n")),
                                            ("short", (2, "index_cli_refused\n")),
                                            ("flush", (2, "index_cli_refused\n")),
                                            ("exit", (2, "index_cli_refused\n"))])
def test_a_failed_off_line_follows_the_documented_pre_runner_codes(monkeypatch, mode, expected):
    runner = importlib.import_module("tools.wd_q4_index_clock_runner")
    calls = []
    monkeypatch.setattr(runner, "run", lambda **kwargs: calls.append(kwargs))
    out, err = _BrokenStdout(mode), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli().main([])
    assert (code, err.getvalue()) == expected and calls == [] and SENTINEL not in out.getvalue() + err.getvalue()


def test_an_interrupted_stderr_token_still_returns_the_fixed_code(tmp_path, monkeypatch):
    path, digest = _write(tmp_path, _synthetic_document(tmp_path))
    runner = importlib.import_module("tools.wd_q4_index_clock_runner")
    def unknown(**kwargs):
        raise runner.IndexRunnerOutcomeUnknown("index_outcome_unknown")

    monkeypatch.setattr(runner, "run", unknown)
    out, err = io.StringIO(), _BrokenStdout("interrupt")
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli().main(_argv(path, digest))
    assert code == 3 and out.getvalue() == "" and SENTINEL not in err.getvalue()


def test_a_complete_receipt_is_exactly_one_closed_line(tmp_path, monkeypatch):
    path, digest = _write(tmp_path, _synthetic_document(tmp_path))
    runner = importlib.import_module("tools.wd_q4_index_clock_runner")
    monkeypatch.setattr(runner, "run", lambda **kwargs: {**COMPLETE, "child": {**COMPLETE["child"], "extra": SENTINEL}})
    code, out, err = run_main(_argv(path, digest))
    assert (code, err) == (0, "") and json.loads(out) == COMPLETE and SENTINEL not in out


def test_a_systemexit_before_the_runner_maps_to_the_fixed_refusal(tmp_path, monkeypatch):
    path, digest = _write(tmp_path, _synthetic_document(tmp_path))
    module = cli()

    def leaving(*args, **kwargs):
        raise SystemExit("synthetic exit " + SENTINEL)

    monkeypatch.setattr(module, "build_inputs", leaving)
    calls = _spy_runner(monkeypatch)
    assert run_main(_argv(path, digest)) == (2, "", "index_cli_refused\n")
    assert calls == []


@pytest.mark.parametrize("failure", [KeyboardInterrupt, SystemExit])
def test_an_interrupt_once_the_runner_call_started_is_unknown(tmp_path, monkeypatch, failure):
    path, digest = _write(tmp_path, _synthetic_document(tmp_path))
    runner = importlib.import_module("tools.wd_q4_index_clock_runner")

    def interrupted(**kwargs):
        raise failure("synthetic " + SENTINEL)

    monkeypatch.setattr(runner, "run", interrupted)
    assert run_main(_argv(path, digest)) == (3, "", "index_outcome_unknown\n")


def test_the_runner_receives_rebuilt_typed_objects_and_the_exact_cutoff(tmp_path, monkeypatch):
    from sandbox.mind.step2 import shared_producer as old
    from waggledance.core.bridge_log_reader import BridgeCursor, BridgePrefixPin
    document = _synthetic_document(tmp_path)
    path, digest = _write(tmp_path, document)
    runner = importlib.import_module("tools.wd_q4_index_clock_runner")
    seen = {}

    def capture(**kwargs):
        seen.update(kwargs)
        raise runner.IndexRunnerRefused("index_request_refused")

    monkeypatch.setattr(runner, "run", capture)
    run_main(_argv(path, digest, "2026-10-31T12:01:00Z"))
    assert seen["enabled"] is True
    assert seen["caller_cutoff_utc"] == datetime(2026, 10, 31, 12, 1, tzinfo=timezone.utc)
    assert seen["caller_cutoff_utc"].tzinfo is timezone.utc
    assert seen["plan_bytes"] == b"plan-synthetic" and seen["expected_plan_sha256"] == sha(b"plan-synthetic")
    config, start = seen["config"], seen["start_prefix"]
    assert type(config) is old.PublicSourceConfig and type(config.source) is old.SourceConfig
    assert config.source.policy_bytes == b"policy-synthetic" and config.source.policy_sha256 == sha(b"policy-synthetic")
    assert config.source.anchor_bytes == b"anchor-synthetic" and config.public_policy_bytes == b"public_policy-synthetic"
    assert config.source.cursor == BridgeCursor(0, "synthetic-identity", None)
    assert config.source.max_bytes == 1 << 20 and config.source.max_rows == 1
    assert start == BridgePrefixPin(BridgeCursor(0, "synthetic-identity", None), sha(b""))
    assert seen["root"] == document["root"] and seen["trusted_model_root"] == document["trusted_model_root"]


def test_no_caller_value_reaches_stderr_or_stdout_on_refusal(tmp_path, monkeypatch):
    document = _synthetic_document(tmp_path)
    document["root"] = "relative\\" + SENTINEL
    path, digest = _write(tmp_path, document)
    code, out, err = run_main(_argv(path, digest))
    assert code == 2 and SENTINEL not in out + err


def test_an_interrupt_while_reading_the_config_exits_130_without_a_traceback(tmp_path, monkeypatch):
    path, digest = _write(tmp_path, _synthetic_document(tmp_path))
    module = cli()

    def interrupted(*args, **kwargs):
        raise KeyboardInterrupt("synthetic " + SENTINEL)

    monkeypatch.setattr(module, "_read_pinned", interrupted)
    assert run_main(_argv(path, digest)) == (130, "", "index_cli_interrupted\n")

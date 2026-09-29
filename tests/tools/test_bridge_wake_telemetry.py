"""F1 wake telemetry tests (authored per operator directive; NOT executed yet).

The reporter is fed synthetic sidecars under tmp_path only; the writer is driven through
PowerShell against a tmp bridge root. Every refusal has a same-fixture success twin, and
every "unknown" assertion checks null, never zero.
"""

from __future__ import annotations

import ast
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import uuid

import pytest

from tools import bridge_wake_telemetry as telemetry

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "tools" / "bridge_wake_telemetry.py"
WRITER = ROOT / ".agent-bridge" / "bin" / "BridgeTelemetry.ps1"
NOW = datetime(2026, 9, 29, 21, 0, tzinfo=timezone.utc)
SHELLS = list(dict.fromkeys(filter(None, (shutil.which("pwsh"), shutil.which("powershell.exe")))))


def _ts(seconds: float) -> str:
    # PowerShell's round-trip format: seven fraction digits and Z.
    return (NOW + timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%S.%f") + "0Z"


def _stage(stage: str, at: float, *, request_id: str | None = "req-1", requester: str | None = "codex-lead-1",
           session: str | None = "lead-session", target: str = "claude-rco-2", delivery: str = "",
           reply: str = "", **extra) -> dict:
    if request_id is None:
        requester = session = None
    record = {"schema": "wd.bridge-stage.v1", "stage": stage, "observed_at_utc": _ts(at), "target": target,
              "request_id": request_id, "requester": requester, "requester_session_id": session,
              "delivery_id": delivery, "queue_id": "", "observer_pid": 4242, "authority_effect": "none",
              "observation_source": "agent_reported" if stage in telemetry.AGENT_REPORTED else "runtime_observed",
              "reply_ts_utc": reply, "report_reference": ""}
    record.update(extra)
    return record


def _write(directory: Path, *records: dict) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    for record in records:
        (directory / f"stage-{uuid.uuid4().hex}.json").write_text(json.dumps(record), encoding="utf-8")
    return directory


def _report(directory: Path, wake: list[Path] | None = None, now: datetime | None = NOW) -> dict:
    code, report = telemetry.run(directory, wake or [], now)
    assert code == 0
    return report


FULL_FLOW = [("request_durable", 0), ("watcher_seen", 2), ("model_turn_started", 10),
             ("answer_durable", 70), ("lead_processed", 100), ("user_reported", 160)]


def test_full_flow_gives_known_intervals_and_missing_relay_stays_unknown(tmp_path):
    directory = _write(tmp_path / "t", *(_stage(s, t) for s, t in FULL_FLOW))
    report = _report(directory)
    latency = report["latency"]
    assert latency["request_durable->watcher_seen"]["p50_seconds"] == 2
    assert latency["model_turn_started->answer_durable"]["max_seconds"] == 60
    assert latency["answer_durable->lead_processed"]["known"] == 1
    # No relay observation: both relay intervals are unknown (null), never zero.
    for name in ("watcher_seen->relay_enqueued", "relay_enqueued->model_turn_started"):
        assert latency[name]["known"] == 0 and latency[name]["unknown"] == 1
        assert latency[name]["p50_seconds"] is None and latency[name]["min_seconds"] is None
    assert report["authority_effect"] == "none" and report["processing_granted"] is False
    assert report["task_completion_verified"] is False and report["inputs"]["coverage"] == "complete"


def test_unbound_relay_joins_only_through_the_turn_delivery_id(tmp_path):
    records = [_stage("request_durable", 0), _stage("model_turn_started", 30, delivery="d-1"),
               _stage("relay_enqueued", 20, request_id=None, delivery="d-1"),
               _stage("relay_enqueued", 5, request_id=None, delivery="d-other")]
    report = _report(_write(tmp_path / "t", *records))
    assert report["latency"]["relay_enqueued->model_turn_started"]["p50_seconds"] == 10
    assert report["flows"]["unjoined_relay_deliveries"] == 1
    # Success twin: the same relay for ANOTHER target does not join.
    records[2] = _stage("relay_enqueued", 20, request_id=None, delivery="d-1", target="fable-5")
    report = _report(_write(tmp_path / "u", *records))
    assert report["latency"]["relay_enqueued->model_turn_started"]["known"] == 0


@pytest.mark.parametrize("field,value", [("requester", "fable-5"), ("requester_session_id", "other-session"),
                                         ("target", "claude-rco-1"), ("request_id", "req-2")])
def test_request_responder_session_discrimination(tmp_path, field, value):
    start = _stage("model_turn_started", 10)
    answer = _stage("answer_durable", 70)
    answer[field] = value
    report = _report(_write(tmp_path / "t", start, answer))
    assert report["flows"]["count"] == 2
    assert report["latency"]["model_turn_started->answer_durable"]["known"] == 0
    # Success twin: identical keys pair.
    report = _report(_write(tmp_path / "u", start, _stage("answer_durable", 70)))
    assert report["latency"]["model_turn_started->answer_durable"]["known"] == 1


def test_post_answer_stages_pair_only_within_the_same_reply(tmp_path):
    first, second = _ts(60), _ts(90)
    records = [_stage("answer_durable", 60, reply=first), _stage("lead_processed", 120, reply=second),
               _stage("answer_durable", 90, reply=second)]
    report = _report(_write(tmp_path / "t", *records))
    summary = report["latency"]["answer_durable->lead_processed"]
    assert summary["known"] == 1 and summary["p50_seconds"] == 30  # 90 -> 120, not 60 -> 120


def test_out_of_order_is_unknown_and_counted_not_zero(tmp_path):
    records = [_stage("model_turn_started", 50), _stage("answer_durable", 40)]
    summary = _report(_write(tmp_path / "t", *records))["latency"]["model_turn_started->answer_durable"]
    assert summary == {"known": 0, "unknown": 1, "out_of_order": 1, "min_seconds": None,
                       "p50_seconds": None, "p90_seconds": None, "max_seconds": None}


def test_future_dated_record_is_rejected_only_when_now_is_known(tmp_path):
    directory = _write(tmp_path / "t", _stage("request_durable", 0), _stage("watcher_seen", 3600))
    report = _report(directory)
    assert report["errors"] == {"future_dated": 1} and report["inputs"]["coverage"] == "partial"
    assert report["latency"]["request_durable->watcher_seen"]["known"] == 0
    # Success twin: without --now the same records pair, and the limit says why.
    report = _report(directory, now=None)
    assert report["latency"]["request_durable->watcher_seen"]["known"] == 1
    assert any("cannot be detected" in item for item in report["limits"])


def test_noop_ratio_is_null_without_explicit_outcomes_even_with_pending_stages(tmp_path):
    records = [_stage("relay_enqueued", 1, request_id=None, delivery="d-9"), _stage("request_durable", 0),
               _stage("model_turn_started", 5), _stage("watcher_seen", 2, request_id="req-2")]
    ratio = _report(_write(tmp_path / "t", *records))["noop_ratio"]
    assert ratio["value"] is None and ratio["reason"] == "no_explicit_outcomes"
    assert (ratio["acted"], ratio["noop"]) == (0, 0)


def test_noop_ratio_comes_only_from_explicit_turn_outcomes(tmp_path):
    records = [_stage("turn_completed", 10, action_outcome="noop"),
               _stage("turn_completed", 20, action_outcome="noop", request_id=None),
               _stage("turn_completed", 30, action_outcome="acted"),
               _stage("turn_completed", 40, action_outcome="acted", target="fable-5")]
    ratio = _report(_write(tmp_path / "t", *records))["noop_ratio"]
    assert (ratio["acted"], ratio["noop"], ratio["value"]) == (2, 2, 0.5)
    assert ratio["by_target"]["claude-rco-2"] == {"acted": 1, "noop": 2, "value": round(2 / 3, 6)}
    assert ratio["by_target"]["fable-5"]["value"] == 0


@pytest.mark.parametrize("mutate,reason", [
    (lambda r: r.update(schema="wd.bridge-stage.v0"), "schema_mismatch"),
    (lambda r: r.update(authority_effect="approved"), "authority_claimed"),
    (lambda r: r.update(observation_source="runtime_observed"), "provenance_mismatch"),
    (lambda r: r.update(stage="processed"), "unknown_stage"),
    (lambda r: r.update(observed_at_utc="2026-09-29T21:00:00"), "time_invalid"),
    (lambda r: r.update(extra="x"), "keys_mismatch"),
    (lambda r: r.pop("report_reference"), "keys_mismatch"),
    (lambda r: r.update(observer_pid=True), "field_invalid"),
    (lambda r: r.update(action_outcome="acted"), "outcome_misplaced"),
    (lambda r: r.update(metadata={"reason": "Free Text"}), "metadata_invalid"),
    (lambda r: r.update(metadata={"latency_ms": 5}), "metadata_invalid"),
    (lambda r: r.update(metadata={"watermark": -1}), "metadata_invalid"),
    (lambda r: r.update(requester=None, request_id=None), "binding_invalid"),
])
def test_invalid_records_are_counted_and_excluded_never_zero(tmp_path, mutate, reason):
    bad = _stage("model_turn_started", 10)
    mutate(bad)
    report = _report(_write(tmp_path / "t", bad, _stage("answer_durable", 70)))
    assert report["errors"] == {reason: 1} and report["inputs"]["stage_records_invalid"] == 1
    assert report["latency"]["model_turn_started->answer_durable"]["p50_seconds"] is None
    # Success twin: the unmutated record pairs.
    report = _report(_write(tmp_path / "u", _stage("model_turn_started", 10), _stage("answer_durable", 70)))
    assert report["errors"] == {} and report["latency"]["model_turn_started->answer_durable"]["p50_seconds"] == 60


def test_turn_completed_needs_a_valid_outcome(tmp_path):
    missing = _stage("turn_completed", 10)
    wrong = _stage("turn_completed", 10, action_outcome="probably_noop")
    report = _report(_write(tmp_path / "t", missing, wrong))
    assert report["errors"] == {"outcome_invalid": 1, "outcome_misplaced": 1}
    assert report["noop_ratio"]["value"] is None


def test_valid_metadata_is_accepted_and_reasons_are_counted(tmp_path):
    record = _stage("watcher_seen", 2, metadata={"reason": "addressed_event", "watermark": 1234,
                                                 "latency_ms": 850.5, "latency_basis": "event_ts_to_observed"})
    report = _report(_write(tmp_path / "t", _stage("request_durable", 0), record))
    assert report["errors"] == {} and report["observation_reasons"] == {"addressed_event": 1}


@pytest.mark.parametrize("content,reason", [
    ("{", "not_json"), ('{"a":1,"a":2}', "duplicate_key"), ('{"a":NaN}', "non_finite_constant"),
    ("[" * 20 + "]" * 20, "too_deep"), (b"\xff\xfe\x00", "not_utf8")])
def test_malformed_files_are_skipped_with_a_reason(tmp_path, content, reason):
    directory = tmp_path / "t"
    directory.mkdir()
    path = directory / f"stage-{uuid.uuid4().hex}.json"
    path.write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))
    report = _report(directory)
    assert report["errors"] == {reason: 1} and report["flows"]["count"] == 0


def test_bounds_oversized_file_and_file_count(tmp_path, monkeypatch):
    directory = _write(tmp_path / "t", _stage("request_durable", 0), _stage("watcher_seen", 2))
    monkeypatch.setattr(telemetry, "MAX_STAGE_BYTES", 64)
    assert _report(directory)["errors"] == {"oversized": 2}
    monkeypatch.setattr(telemetry, "MAX_STAGE_BYTES", 64 * 1024)
    monkeypatch.setattr(telemetry, "MAX_STAGE_FILES", 1)
    report = _report(directory)
    assert report["inputs"]["truncated"] is True and report["inputs"]["coverage"] == "partial"
    assert report["inputs"]["stage_files_read"] == 1


def test_only_stage_sidecar_names_are_read(tmp_path):
    directory = _write(tmp_path / "t", _stage("request_durable", 0))
    (directory / "events.jsonl").write_text('{"not":"read"}\n', encoding="utf-8")
    (directory / "stage-notahex.json").write_text("{", encoding="utf-8")
    (directory / f"stage-{uuid.uuid4().hex}.json.tmp").write_text("{", encoding="utf-8")
    report = _report(directory)
    assert report["inputs"]["ignored_names"] == 3 and report["errors"] == {}


def test_wake_snapshot_is_validated_and_metadata_reported(tmp_path):
    wake = tmp_path / "wake.json"
    snapshot = {"schema": "wd.bridge-wake-observation.v1", "observed_at_utc": _ts(1),
                "requests": [{"request_id": "req-1", "agent": "codex-lead-1", "session_id": "s", "reply_ts_utc": ""}],
                "correlation_complete": True, "authority_effect": "none",
                "metadata": {"reason": "wake_request", "watermark": 99}}
    wake.write_text(json.dumps(snapshot), encoding="utf-8")
    directory = _write(tmp_path / "t", _stage("request_durable", 0))
    entry = _report(directory, [wake])["wake_snapshots"][0]
    assert entry["valid"] is True and entry["bindings"] == 1 and entry["metadata"]["watermark"] == 99
    snapshot["authority_effect"] = "granted"
    wake.write_text(json.dumps(snapshot), encoding="utf-8")
    entry = _report(directory, [wake])["wake_snapshots"][0]
    assert entry == {"index": 0, "valid": False, "reason": "authority_claimed"}


@pytest.mark.parametrize("raw", ["relative-dir", "\\\\server\\share\\telemetry", "//server/share/telemetry"])
def test_directory_must_be_local_absolute(raw):
    with pytest.raises(telemetry.TelemetryInputError, match="local absolute"):
        telemetry.run(Path(raw), [], NOW)


def test_canonical_log_is_refused_as_a_wake_input(tmp_path):
    directory = _write(tmp_path / "t", _stage("request_durable", 0))
    with pytest.raises(telemetry.TelemetryInputError, match="canonical bridge log"):
        telemetry.run(directory, [tmp_path / "events.jsonl"], NOW)


def test_report_is_deterministic_and_reads_without_writing(tmp_path):
    directory = _write(tmp_path / "t", *(_stage(s, t) for s, t in FULL_FLOW),
                       _stage("turn_completed", 200, action_outcome="noop"))
    before = sorted((p.name, p.stat().st_size, p.stat().st_mtime_ns) for p in directory.iterdir())
    first = json.dumps(_report(directory), sort_keys=True)
    second = json.dumps(_report(directory), sort_keys=True)
    assert first == second
    assert sorted((p.name, p.stat().st_size, p.stat().st_mtime_ns) for p in directory.iterdir()) == before


@pytest.mark.parametrize("argv", [[], ["--telemetry-directory"], ["--telemetry-directory", "x", "--now", "soon"],
                                  ["--telemetry-dir", "abbreviated"], ["--unknown", "secret-value"]])
def test_cli_errors_are_json_exit_3_without_echo(tmp_path, capsys, argv):
    assert telemetry.main(argv) == 3
    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert report["verdict"] == "invalid_input" and report["authority_effect"] == "none"
    assert "secret-value" not in captured.out and captured.err == ""


def test_cli_valid_run_and_help_without_docstring(tmp_path, capsys, monkeypatch):
    directory = _write(tmp_path / "t", _stage("request_durable", 0))
    assert telemetry.main(["--telemetry-directory", str(directory), "--now", _ts(10)]) == 0
    assert json.loads(capsys.readouterr().out)["schema"] == telemetry.REPORT_SCHEMA
    monkeypatch.setattr(telemetry, "__doc__", None)  # python -OO
    with pytest.raises(SystemExit) as stopped:
        telemetry.main(["--help"])
    assert stopped.value.code == 0 and "--telemetry-directory" in capsys.readouterr().out


def test_source_is_read_only_and_never_reads_the_canonical_log():
    source = SOURCE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = {alias.name.split(".")[0] for node in ast.walk(tree)
                if isinstance(node, (ast.Import, ast.ImportFrom))
                for alias in (node.names if isinstance(node, ast.Import) else [ast.alias(node.module or "")])}
    assert not imported & {"subprocess", "socket", "urllib", "http", "requests", "shutil", "ctypes",
                           "multiprocessing", "asyncio", "pty", "webbrowser", "tempfile"}
    calls = {node.func.attr for node in ast.walk(tree)
             if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)}
    exec_family = {name for name in dir(os) if name.startswith(("exec", "spawn", "posix_spawn"))}
    writers = {"system", "popen", "startfile", "fork", "kill", "remove", "unlink", "rmdir", "removedirs",
               "rename", "renames", "replace", "write_text", "write_bytes", "mkdir", "makedirs", "touch",
               "truncate", "chmod", "chown", "utime", "symlink", "link", "mkfifo"}
    assert not calls & (exec_family | writers), calls & (exec_family | writers)
    flags = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute) and node.attr.startswith("O_")}
    assert flags <= {"O_RDONLY", "O_NONBLOCK", "O_BINARY"}, flags
    assert "open(" not in source.replace("os.open(", "").replace("os.fdopen(", "")


# -- writer (BridgeTelemetry.ps1), driven through PowerShell --------------------------

def _ps(shell: str, tmp_path: Path, body: str) -> subprocess.CompletedProcess:
    # $request is a variable: in argument mode a [type]@{...} literal would bind as a string.
    script = (f". '{WRITER}'\n$ErrorActionPreference = 'Stop'\n"
              "$request = [pscustomobject]@{request_id='r-1';agent='codex-lead-1';session_id='s-1'}\n" + body)
    env = {k: v for k, v in os.environ.items() if not k.startswith(("AGENT_BRIDGE_", "WD_"))}
    env["AGENT_BRIDGE_RUNTIME_ROOT"] = str(tmp_path / "decoy-root")  # never the live bridge
    return subprocess.run([shell, "-NoProfile", "-NonInteractive", "-Command", script],
                          capture_output=True, text=True, timeout=60, env=env)


def _stages(tmp_path: Path) -> list[dict]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in (tmp_path / "shared" / "telemetry").glob("stage-*.json")]


REQUEST = "$request"


@pytest.mark.skipif(not SHELLS, reason="PowerShell is required")
@pytest.mark.parametrize("shell", SHELLS, ids=lambda s: Path(s).stem)
def test_writer_without_metadata_keeps_the_existing_record_shape(tmp_path, shell):
    result = _ps(shell, tmp_path, f"Write-BridgeStageObservation -BridgeRoot '{tmp_path}' -Stage watcher_seen "
                                  f"-Request {REQUEST} -Target claude-rco-2")
    assert result.returncode == 0, result.stderr
    [record] = _stages(tmp_path)
    assert set(record) == set(telemetry.STAGE_KEYS)  # no new keys unless supplied
    assert not list((tmp_path / "shared" / "telemetry").glob("*.tmp"))


@pytest.mark.skipif(not SHELLS, reason="PowerShell is required")
@pytest.mark.parametrize("shell", SHELLS, ids=lambda s: Path(s).stem)
def test_writer_records_metadata_and_explicit_outcome_that_the_reporter_accepts(tmp_path, shell):
    body = (f"Write-BridgeStageObservation -BridgeRoot '{tmp_path}' -Stage watcher_seen -Request {REQUEST} "
            "-Target claude-rco-2 -Reason addressed_event -Watermark 4096 -LatencyMs 12.5 "
            "-LatencyBasis event_ts_to_observed\n"
            f"Write-BridgeStageObservation -BridgeRoot '{tmp_path}' -Stage turn_completed -Target claude-rco-2 "
            "-ActionOutcome noop -Reason informational_notice")
    result = _ps(shell, tmp_path, body)
    assert result.returncode == 0, result.stderr
    records = {r["stage"]: r for r in _stages(tmp_path)}
    assert records["watcher_seen"]["metadata"] == {"reason": "addressed_event", "watermark": 4096,
                                                   "latency_ms": 12.5, "latency_basis": "event_ts_to_observed"}
    assert records["watcher_seen"]["request_id"] == "r-1"  # correlation fields unchanged
    assert records["turn_completed"]["action_outcome"] == "noop"
    assert records["turn_completed"]["observation_source"] == "agent_reported"
    report = _report(tmp_path / "shared" / "telemetry", now=None)
    assert report["errors"] == {} and report["noop_ratio"]["noop"] == 1


@pytest.mark.skipif(not SHELLS, reason="PowerShell is required")
@pytest.mark.parametrize("shell", SHELLS, ids=lambda s: Path(s).stem)
@pytest.mark.parametrize("arguments", [
    "-Stage turn_completed", "-Stage turn_completed -ActionOutcome maybe",
    "-Stage answer_durable -ActionOutcome acted", "-Stage watcher_seen -Reason 'Free Text'",
    "-Stage watcher_seen -Watermark -1", "-Stage watcher_seen -LatencyMs 5",
    "-Stage watcher_seen -LatencyMs 99999999999 -LatencyBasis x", "-Stage processed"])
def test_writer_refuses_invalid_metadata_before_writing(tmp_path, shell, arguments):
    result = _ps(shell, tmp_path, f"Write-BridgeStageObservation -BridgeRoot '{tmp_path}' -Target claude-rco-2 {arguments}")
    assert result.returncode != 0
    assert not (tmp_path / "shared" / "telemetry").exists() or not _stages(tmp_path)


@pytest.mark.skipif(not SHELLS, reason="PowerShell is required")
@pytest.mark.parametrize("shell", SHELLS, ids=lambda s: Path(s).stem)
def test_wake_metadata_is_per_wake_and_never_carried_forward(tmp_path, shell):
    wake = tmp_path / "wake.json"
    result = _ps(shell, tmp_path, f"Write-BridgeWakeObservation -Path '{wake}' -Events @($request) "
                                  "-Reason addressed_event -Watermark 10\n"
                                  f"$first = Get-Content -LiteralPath '{wake}' -Raw\n"
                                  f"Write-BridgeWakeObservation -Path '{wake}' -Events @($request)\n"
                                  "$first")
    assert result.returncode == 0, result.stderr
    first = json.loads(result.stdout.strip().splitlines()[-1])
    assert first["metadata"] == {"reason": "addressed_event", "watermark": 10}
    second = json.loads(wake.read_text(encoding="utf-8"))
    assert "metadata" not in second and len(second["requests"]) == 2
    assert set(second) == set(telemetry.WAKE_KEYS)

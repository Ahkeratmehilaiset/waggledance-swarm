"""One-shot public-status runner: synthetic bytes and directories only."""
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import types
from dataclasses import asdict

import pytest

ROOT = Path(__file__).resolve().parents[2]
TASK = "lead-mind-production-20261031"
LANES = ("codex-lead-1", "codex-tools-1", "claude-rco-1", "claude-rco-2", "fable-5")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("ascii")


@pytest.fixture
def runner():
    spec = importlib.util.spec_from_file_location("synthetic_status_prefix_runner", ROOT / "tools/wd_q4_status_prefix_runner.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def case(tmp_path, monkeypatch, runner):
    policy = encode({"schema": "wd.q4-non-review-policy.v1", "approved_tasks": [TASK]})
    public = encode({"schema": "wd.mind-public-status-policy.v1", "public_tasks": [TASK], "public_types": ["status"]})
    uuids = {name: f"{i:08d}-1111-4111-8111-111111111111" for i, name in enumerate(LANES, 1)}
    roster = encode({"schema": "wd.q4-roster-snapshot.v1", "lanes": [
        {"agent": name, "agent_uuid": uuids[name]} for name in LANES if name != "codex-tools-1"],
        "tools_supervisor": {"agent": "codex-tools-1", "agent_uuid": uuids["codex-tools-1"]}})
    for key, value in (("_POLICY_SHA", policy), ("_PUBLIC_SHA", public), ("_ROSTER_SHA", roster)):
        monkeypatch.setattr(runner, key, frozenset({sha(value), sha(value + b"\r\n")}))
    anchor = b"synthetic anchor"
    source, sidecar = tmp_path / "events.jsonl", tmp_path / "generation.json"
    source.write_bytes(b"synthetic-only\n")
    sidecar.write_bytes(b"synthetic-sidecar")
    checkpoint, private, model = (tmp_path / name for name in ("checkpoint", "private", "model"))
    for directory in (checkpoint, private, model):
        directory.mkdir()
    pins = {"anchor_sha256": sha(anchor), "policy_sha256": sha(policy), "roster_sha256": sha(roster),
            "public_policy_sha256": sha(public)}
    plan = encode({"schema": "wd.q4-prefix-build-plan.v3", "generation": "synthetic-generation",
                   "expected_pointer_sha256": None, "max_prefix_bytes": 1000, "max_windows": 1,
                   "timeout": 1, "encoder": {}, "pins": pins, "fenced": True})
    route = {"schema": "wd.q4-status-prefix-route.v1", "source": str(source), "generation_path": str(sidecar),
             "checkpoint_root": str(checkpoint), "private_state_root": str(private), "trusted_model_root": str(model),
             "cursor": {"offset": 0, "file_identity": "windows-v1:12345678:0123456789abcdef", "generation": "gen-1"},
             "prefix_sha256": sha(b""), "max_bytes": 1024, "max_rows": 10, "plan_sha256": sha(plan), **pins}
    def args(route_value=route):
        raw = encode(route_value)
        return dict(enabled=True, route_bytes=raw, expected_route_sha256=sha(raw), plan_bytes=plan,
                    policy_bytes=policy, public_policy_bytes=public, roster_bytes=roster, anchor_bytes=anchor)
    return runner, args, route, checkpoint, private, monkeypatch


def child_report():
    return {"status": "PUBLISHED_GENESIS", "pointer_sha256": "a" * 64, "generation": "synthetic-generation",
            "publisher_authentication": "UNKNOWN", "source_authenticity": "UNKNOWN", "freshness": "UNKNOWN",
            "coverage": "caught_up_to_final_snapshot_not_history", "source_refusals": "NOT_AGGREGATED",
            "encoder_identity_source": "encode.model_identity", "plan_sha256": "b" * 64,
            "backend_isolation": "in_process_environment_restored_no_os_isolation",
            "model_assets": "caller_manifest_verified_before_load_reread_by_backend"}


def fake_child(monkeypatch, invoke):
    name = "sandbox.mind.step2.wd_q4_prefix_build_child"
    module = types.ModuleType(name)
    module.build_prefix_generation = invoke
    monkeypatch.setitem(sys.modules, name, module)


def test_off_does_not_inspect_inputs(runner):
    class NoTouch:
        def __getattribute__(self, name):
            raise AssertionError("disabled path read input")
    assert runner.run_once(route_bytes=NoTouch()) == {"status": "OFF"}


def test_known_git_lf_and_windows_crlf_policy_pins(runner):
    git = shutil.which("git")
    assert git is not None, "git is required to verify committed configuration bytes"
    for path, allowed in (("configs/mind/pilot_non_review_policy.json", runner._POLICY_SHA),
                          ("configs/mind/pilot_public_status_policy.json", runner._PUBLIC_SHA),
                          ("ops/windows/reboot/wd-fleet.json", runner._ROSTER_SHA)):
        blob = subprocess.run([git, "show", "HEAD:" + path], cwd=ROOT, capture_output=True, check=True).stdout
        assert b"\r\n" not in blob
        assert {sha(blob), sha(blob.replace(b"\n", b"\r\n"))} == allowed


def test_exact_pins_and_one_child_call_return_unchanged(case):
    runner, args, route, checkpoint, private, monkeypatch = case
    calls = []
    report = child_report()
    report["plan_sha256"] = route["plan_sha256"]
    def child(**kwargs):
        calls.append(kwargs)
        assert (checkpoint / runner._PENDING).is_file() and (private / runner._PENDING).is_file()
        return report
    fake_child(monkeypatch, child)
    assert runner.run_once(**args()) == report
    assert len(calls) == 1 and calls[0]["enabled"] is True
    assert calls[0]["config"].source.cursor == calls[0]["start_prefix"].cursor
    assert (checkpoint / runner._PENDING).is_file() and (private / runner._PENDING).is_file()
    with pytest.raises(runner.StatusRunnerRefused):
        runner.run_once(**args())
    assert len(calls) == 1  # Both reservations remain until explicit reconciliation.


@pytest.mark.parametrize("schema", ["wd.q4-prefix-build-plan.v1", "wd.q4-prefix-build-plan.v3"])
def test_continuation_propagates_exact_prefix_and_prior_pointer(case, schema):
    runner, args, route, _, _, monkeypatch = case
    kw = args()
    plan = json.loads(kw["plan_bytes"])
    plan.update(schema=schema, expected_pointer_sha256="c" * 64)
    if schema.endswith(".v1"):
        del plan["fenced"], plan["max_windows"]
    kw["plan_bytes"] = encode(plan)
    route = dict(route, cursor={**route["cursor"], "offset": 7}, prefix_sha256="d" * 64,
                 plan_sha256=sha(kw["plan_bytes"]))
    kw["route_bytes"] = encode(route)
    kw["expected_route_sha256"] = sha(kw["route_bytes"])
    report = child_report()
    report.update(status="PUBLISHED_HISTORY", plan_sha256=route["plan_sha256"])
    if schema.endswith(".v1"):
        del report["coverage"], report["source_refusals"]
    calls = []
    fake_child(monkeypatch, lambda **kwargs: calls.append(kwargs) or report)
    assert runner.run_once(**kw) == report
    assert calls[0]["start_prefix"].cursor.offset == 7
    assert calls[0]["start_prefix"].sha256 == "d" * 64
    assert json.loads(calls[0]["plan_bytes"])["expected_pointer_sha256"] == "c" * 64


@pytest.mark.parametrize("field", ["policy_bytes", "public_policy_bytes", "roster_bytes", "anchor_bytes", "plan_bytes"])
def test_tampered_input_refused_before_child_or_marker(case, field):
    runner, args, _, checkpoint, private, _ = case
    kw = args()
    kw[field] += b"x"
    with pytest.raises(runner.StatusRunnerRefused):
        runner.run_once(**kw)
    assert list(checkpoint.iterdir()) == [] and list(private.iterdir()) == []


@pytest.mark.parametrize("field,value", [("max_bytes", 4194305), ("max_rows", 10001), ("prefix_sha256", "0" * 64)])
def test_invalid_bounds_or_genesis_prefix_refused_before_child(case, field, value):
    runner, args, route, checkpoint, private, _ = case
    bad = dict(route, **{field: value})
    with pytest.raises(runner.StatusRunnerRefused):
        runner.run_once(**args(bad))
    assert list(checkpoint.iterdir()) == [] and list(private.iterdir()) == []


@pytest.mark.parametrize("failure", ["exception", "interrupt", "malformed"])
def test_unknown_retains_both_markers_and_blocks_retry(case, failure):
    runner, args, _, checkpoint, private, monkeypatch = case
    calls = []
    def child(**kwargs):
        calls.append(1)
        if failure == "interrupt":
            raise KeyboardInterrupt("synthetic-private")
        if failure == "exception":
            raise RuntimeError("publication_state_unknown")
        return {"status": "wrong"}
    fake_child(monkeypatch, child)
    expected = runner.StatusRunnerInterrupted if failure == "interrupt" else runner.StatusRunnerOutcomeUnknown
    with pytest.raises(expected) as error:
        runner.run_once(**args())
    assert "synthetic-private" not in str(error.value)
    assert (checkpoint / runner._PENDING).is_file() and (private / runner._PENDING).is_file()
    with pytest.raises(runner.StatusRunnerRefused):
        runner.run_once(**args())
    assert calls == [1]


def test_partial_reservation_fails_closed_before_child(case, monkeypatch):
    runner, args, _, checkpoint, private, _ = case
    original = runner._create
    calls = []
    def create(path, value):
        if path == private / runner._PENDING:
            raise PermissionError("synthetic-private-path")
        return original(path, value)
    monkeypatch.setattr(runner, "_create", create)
    fake_child(monkeypatch, lambda **kwargs: calls.append(kwargs))
    with pytest.raises(runner.StatusRunnerOutcomeUnknown):
        runner.run_once(**args())
    assert (checkpoint / runner._PENDING).is_file() and not (private / runner._PENDING).exists()
    with pytest.raises(runner.StatusRunnerRefused):
        runner.run_once(**args())
    assert calls == []


def test_interrupt_after_child_return_keeps_both_markers(case, monkeypatch):
    runner, args, route, checkpoint, private, _ = case
    calls = []
    report = child_report()
    report["plan_sha256"] = route["plan_sha256"]
    fake_child(monkeypatch, lambda **kwargs: calls.append(1) or report)
    def validate(*_):
        raise KeyboardInterrupt("synthetic")
    monkeypatch.setattr(runner, "_result_valid", validate)
    with pytest.raises(runner.StatusRunnerInterrupted):
        runner.run_once(**args())
    assert (checkpoint / runner._PENDING).exists() and (private / runner._PENDING).exists()
    with pytest.raises(runner.StatusRunnerRefused):
        runner.run_once(**args())
    assert calls == [1]


def test_changed_marker_is_never_removed(case, monkeypatch):
    runner, args, route, checkpoint, private, _ = case
    report = child_report()
    report["plan_sha256"] = route["plan_sha256"]
    def child(**kwargs):
        (checkpoint / runner._PENDING).write_bytes(b"changed-synthetic-marker")
        return report
    fake_child(monkeypatch, child)
    with pytest.raises(runner.StatusRunnerOutcomeUnknown):
        runner.run_once(**args())
    assert (checkpoint / runner._PENDING).read_bytes() == b"changed-synthetic-marker"
    assert (private / runner._PENDING).is_file()


def test_other_baseexception_is_not_swallowed_by_except_exception(case, monkeypatch):
    runner, args, _, checkpoint, private, _ = case
    class SyntheticStop(BaseException):
        pass
    def child(**kwargs):
        raise SyntheticStop("synthetic-private")
    fake_child(monkeypatch, child)
    caught_by_exception = False
    with pytest.raises(runner.StatusRunnerInterrupted) as caught:
        try:
            runner.run_once(**args())
        except Exception:
            caught_by_exception = True
    assert not caught_by_exception and caught.value.__context__ is None
    assert (checkpoint / runner._PENDING).is_file() and (private / runner._PENDING).is_file()


@pytest.mark.skipif(sys.platform != "win32", reason="synthetic producer uses Windows fixed-volume checks")
def test_real_child_synthetic_status_only_genesis(case, monkeypatch):
    pytest.importorskip("faiss", reason="synthetic builder requires FAISS")
    from sandbox.mind.step2 import encode as encoder
    from sandbox.mind.step2.test_wd_q4_prefix_build_child import FakeModel
    from waggledance.core.bridge_log_reader import BridgeCursor, read_bridge_log

    runner, args, route, checkpoint, private, _ = case
    kw = args()
    roster = json.loads(kw["roster_bytes"])
    uuids = {item["agent"]: item["agent_uuid"] for item in roster["lanes"] + [roster["tools_supervisor"]]}
    base = {"ts_utc": "2026-10-31T12:00:30Z", "agent": "codex-lead-1", "agent_uuid": uuids["codex-lead-1"],
            "type": "status", "status": "info", "task_id": TASK, "to": "", "message": "", "paths": [],
            "write_scope": [], "payload": {"public_status": True}}
    events = [base, dict(base, message="synthetic-private-body"),
              dict(base, agent="claude-rco-1", agent_uuid=uuids["claude-rco-1"]),
              dict(base, task_id="unapproved-task")]
    source, sidecar = Path(route["source"]), Path(route["generation_path"])
    source.write_bytes(b"".join(encode(event) + b"\n" for event in events))
    sidecar.write_bytes(encode({"generation": "gen-1"}))
    probe = read_bridge_log(source, generation_path=sidecar, max_bytes=4096, max_rows=20)
    assert probe.candidate_cursor is not None
    cursor = BridgeCursor(offset=0, file_identity=probe.candidate_cursor.file_identity, generation="gen-1")
    anchor = encode({"schema": "wd.q4-source-anchor.v1", "source_path": str(source),
                     "generation_path": str(sidecar), "cursor_start": asdict(cursor),
                     "policy_sha256": route["policy_sha256"], "roster_sha256": route["roster_sha256"],
                     "status_mode": "public-v1", "public_policy_sha256": route["public_policy_sha256"]})
    model = Path(route["trusted_model_root"])
    weights = b"synthetic-weights-not-a-model"
    (model / "model.safetensors").write_bytes(weights)
    plan = json.loads(kw["plan_bytes"])
    plan["max_prefix_bytes"] = 8192
    plan["pins"]["anchor_sha256"] = sha(anchor)
    plan["encoder"] = {"model": encoder.MODEL, "revision": "c" * 40, "model_dir": str(model),
                       "weights_sha256": sha(weights), "dim": encoder.DIM,
                       "assets": {"model.safetensors": sha(weights)}}
    kw["plan_bytes"] = encode(plan)
    route.update(cursor=asdict(cursor), max_bytes=4096, max_rows=20, anchor_sha256=sha(anchor),
                 plan_sha256=sha(kw["plan_bytes"]))
    kw.update(anchor_bytes=anchor, route_bytes=encode(route))
    kw["expected_route_sha256"] = sha(kw["route_bytes"])
    from sandbox.mind.step2 import shared_bridge_snapshot as snapshot
    public_tasks, public_types = snapshot._checked_anchor(source, cursor, frozenset({TASK}), anchor_bytes=anchor,
        anchor_sha256=sha(anchor), generation_path=sidecar, policy_bytes=kw["policy_bytes"],
        policy_sha256=route["policy_sha256"], roster_bytes=kw["roster_bytes"], roster_sha256=route["roster_sha256"],
        public_policy_bytes=kw["public_policy_bytes"], public_policy_sha256=route["public_policy_sha256"])
    check = read_bridge_log(source, cursor=cursor, generation_path=sidecar, max_bytes=4096, max_rows=20,
                            expected_prefix_sha256=sha(b""), max_prefix_bytes=8192)
    assert check.status.value == "OK", (check.status, check.reason)
    snapshot.read_anchored_delta(source, cursor, frozenset({TASK}), anchor_bytes=anchor, anchor_sha256=sha(anchor),
        generation_path=sidecar, max_bytes=4096, max_rows=20, policy_bytes=kw["policy_bytes"],
        policy_sha256=route["policy_sha256"], roster_bytes=kw["roster_bytes"], roster_sha256=route["roster_sha256"],
        public_policy_bytes=kw["public_policy_bytes"], public_policy_sha256=route["public_policy_sha256"],
        expected_prefix_sha256=sha(b""), max_prefix_bytes=8192)
    sentence = types.ModuleType("sentence_transformers")
    sentence.__version__, sentence.SentenceTransformer = "0.0-synthetic", FakeModel
    torch = types.ModuleType("torch")
    torch.__version__, torch.get_num_threads = "0.0-synthetic", lambda: 1
    monkeypatch.setitem(sys.modules, "sentence_transformers", sentence)
    monkeypatch.setitem(sys.modules, "torch", torch)
    from sandbox.mind.step2 import wd_q4_prefix_build_child as actual_child
    original_child = actual_child.build_prefix_generation
    observations = []
    def observe_child(**kwargs):
        try:
            result = original_child(**kwargs)
        except Exception as exc:
            observations.append((type(exc).__name__, str(exc)))
            raise
        observations.append(("result", result))
        return result
    monkeypatch.setattr(actual_child, "build_prefix_generation", observe_child)
    try:
        result = runner.run_once(**kw)
    except runner.StatusRunnerOutcomeUnknown:
        pytest.fail(f"synthetic child observation: {observations!r}")
    assert result["status"] == "PUBLISHED_GENESIS"
    assert result["freshness"] == result["source_authenticity"] == "UNKNOWN"
    projection = (checkpoint / plan["generation"] / "projection.json").read_bytes()
    records = json.loads(projection)["records"]
    assert len(records) == 1 and records[0]["stored_text"] == f"status info codex-lead-1 {TASK}"
    assert b"synthetic-private-body" not in projection
    assert b"unapproved-task" not in projection and b"claude-rco-1" not in projection
    assert (checkpoint / runner._PENDING).is_file() and (private / runner._PENDING).is_file()

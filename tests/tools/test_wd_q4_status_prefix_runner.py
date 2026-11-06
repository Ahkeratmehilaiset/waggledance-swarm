"""One-shot public-status runner: synthetic bytes and directories only."""
import hashlib
import datetime
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
    fleet = encode({"lanes": [
        {"agent": name, "agent_uuid": uuids[name]} for name in LANES if name != "codex-tools-1"],
        "tools_supervisor": {"agent": "codex-tools-1", "agent_uuid": uuids["codex-tools-1"]},
        "synthetic_metadata": "not-in-canonical-roster"})
    from tools.wd_q4_roster_from_fleet import build_roster_snapshot
    roster, _ = build_roster_snapshot(fleet, sha(fleet), enabled=True)
    for key, value in (("_POLICY_SHA", policy), ("_PUBLIC_SHA", public), ("_FLEET_SHA", fleet)):
        monkeypatch.setattr(runner, key, frozenset({sha(value), sha(value + b"\r\n")}))
    anchor = b"synthetic anchor"
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    source, sidecar = source_dir / "events.jsonl", source_dir / "generation.json"
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
                    policy_bytes=policy, public_policy_bytes=public, fleet_bytes=fleet,
                    expected_fleet_sha256=sha(fleet), roster_bytes=roster, anchor_bytes=anchor)
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


def test_verified_fleet_is_converted_to_the_binding_roster(case, monkeypatch):
    from tools import wd_q4_roster_from_fleet as roster_producer
    from tools import wd_q4_lane_binding as consumer
    runner, args, route, _, _, _ = case
    kw = args()
    old_roster = json.loads(kw.pop("roster_bytes"))
    fleet = encode({"lanes": old_roster["lanes"], "tools_supervisor": old_roster["tools_supervisor"],
                    "extra_non_roster_metadata": "synthetic"})
    canonical_roster, roster_sha = roster_producer.build_roster_snapshot(fleet, sha(fleet), enabled=True)
    monkeypatch.setattr(runner, "_FLEET_SHA", frozenset({sha(fleet)}), raising=False)
    plan = json.loads(kw["plan_bytes"])
    plan["pins"]["roster_sha256"] = roster_sha
    plan_raw = encode(plan)
    route = dict(route, roster_sha256=roster_sha, plan_sha256=sha(plan_raw))
    route_raw = encode(route)
    kw.update(fleet_bytes=fleet, expected_fleet_sha256=sha(fleet), plan_bytes=plan_raw,
              route_bytes=route_raw, expected_route_sha256=sha(route_raw))
    seen = []
    def child(**kwargs):
        seen.append(kwargs["config"].source.roster_bytes)
        return dict(child_report(), plan_sha256=sha(plan_raw))
    fake_child(monkeypatch, child)
    result = runner.run_once(**kw)
    assert result["status"] == "PUBLISHED_GENESIS"
    assert seen == [canonical_roster] and sha(fleet) != roster_sha
    assert consumer._roster(fleet, sha(fleet))[1] == roster_sha


@pytest.mark.parametrize("failure", ["missing_fleet", "wrong_fleet_pin", "forged_roster", "fleet_as_roster"])
def test_fleet_and_canonical_roster_must_both_bind_before_child(case, monkeypatch, failure):
    runner, args, _, checkpoint, private, _ = case
    kw = args()
    if failure == "missing_fleet":
        kw["fleet_bytes"] = None
    elif failure == "wrong_fleet_pin":
        kw["expected_fleet_sha256"] = "f" * 64
    elif failure == "forged_roster":
        kw["roster_bytes"] += b" "
    else:
        kw["roster_bytes"] = kw["fleet_bytes"]
    fake_child(monkeypatch, lambda **_: pytest.fail("invalid fleet reached child"))
    with pytest.raises(runner.StatusRunnerRefused):
        runner.run_once(**kw)
    assert not (checkpoint / runner._PENDING).exists() and not (private / runner._PENDING).exists()


@pytest.mark.parametrize("change", ["v1", "unfenced", "not_boolean"])
def test_renewable_requires_explicit_fenced_v3_before_reservation(case, change):
    runner, args, route, checkpoint, private, _ = case
    kw = args()
    plan = json.loads(kw["plan_bytes"])
    if change == "v1":
        plan["schema"] = "wd.q4-prefix-build-plan.v1"
    elif change == "unfenced":
        plan["fenced"] = False
    plan_raw = encode(plan)
    route = dict(route, plan_sha256=sha(plan_raw))
    route_raw = encode(route)
    kw.update(plan_bytes=plan_raw, route_bytes=route_raw, expected_route_sha256=sha(route_raw),
              renewable="yes" if change == "not_boolean" else True)
    with pytest.raises(runner.StatusRunnerRefused):
        runner.run_once(**kw)
    assert not (checkpoint / runner._PENDING).exists() and not (private / runner._PENDING).exists()


def test_known_git_lf_and_windows_crlf_policy_pins(runner):
    git = shutil.which("git")
    assert git is not None, "git is required to verify committed configuration bytes"
    for path, allowed in (("configs/mind/pilot_non_review_policy.json", runner._POLICY_SHA),
                          ("configs/mind/pilot_public_status_policy.json", runner._PUBLIC_SHA),
                          ("ops/windows/reboot/wd-fleet.json", runner._FLEET_SHA)):
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


def test_pinned_utc_cutoff_reaches_child_and_allows_partial_coverage(case):
    runner, args, route, _, _, monkeypatch = case
    route = dict(route, cutoff_utc="2026-10-31T13:00:00Z")
    kw = args(route)
    seen = []
    def child(**kwargs):
        seen.append(kwargs)
        return dict(child_report(), plan_sha256=route["plan_sha256"], coverage="partial_ready_prefix")
    fake_child(monkeypatch, child)
    assert runner.run_once(**kw)["coverage"] == "partial_ready_prefix"
    assert seen[0]["cutoff_utc"] == datetime.datetime(2026, 10, 31, 13, tzinfo=datetime.timezone.utc)


@pytest.mark.parametrize("cutoff", [None, "2026-10-31", "2026-10-31T13:00:00+00:00", "garbage"])
def test_invalid_explicit_cutoff_refuses_before_reservation(case, cutoff):
    runner, args, route, checkpoint, private, _ = case
    route = dict(route, cutoff_utc=cutoff)
    with pytest.raises(runner.StatusRunnerRefused):
        runner.run_once(**args(route))
    assert not (checkpoint / runner._PENDING).exists() and not (private / runner._PENDING).exists()


def test_cutoff_must_be_covered_by_route_pin(case):
    runner, args, route, _, _, _ = case
    kw = args()
    kw["route_bytes"] = encode(dict(route, cutoff_utc="2026-10-31T13:00:00Z"))
    with pytest.raises(runner.StatusRunnerRefused):
        runner.run_once(**kw)


def test_batched_cutoff_is_explicitly_bound_and_forwarded(case):
    runner, args, route, _, _, monkeypatch = case
    base = args()
    plan = json.loads(base["plan_bytes"])
    plan["max_windows"] = 64
    plan_bytes = encode(plan)
    route = dict(route, cutoff_utc="2026-10-31T13:00:00Z", batch_rows=True, plan_sha256=sha(plan_bytes))
    kw = args(route)
    kw["plan_bytes"] = plan_bytes
    seen = []
    def child(*, batch_rows=False, **kwargs):
        seen.append((batch_rows, kwargs))
        return dict(child_report(), plan_sha256=route["plan_sha256"], coverage="partial_ready_prefix")
    fake_child(monkeypatch, child)
    assert runner.run_once(**kw)["status"] == "PUBLISHED_GENESIS"
    assert seen[0][0] is True and seen[0][1]["config"].source.max_rows == 10
    assert json.loads(seen[0][1]["plan_bytes"])["max_windows"] == 64


@pytest.mark.parametrize("extras", [{"batch_rows": True}, {"batch_rows": False},
                                   {"batch_rows": True, "cutoff_utc": None}])
def test_batch_without_exact_cutoff_contract_refused(case, extras):
    runner, args, route, checkpoint, private, _ = case
    with pytest.raises(runner.StatusRunnerRefused):
        runner.run_once(**args(dict(route, **extras)))
    assert not (checkpoint / runner._PENDING).exists() and not (private / runner._PENDING).exists()


def test_batch_requires_child_that_explicitly_accepts_it(case):
    runner, args, route, checkpoint, private, monkeypatch = case
    route = dict(route, cutoff_utc="2026-10-31T13:00:00Z", batch_rows=True)
    def old_child(*, enabled):
        pytest.fail("legacy child must not be entered")
    fake_child(monkeypatch, old_child)
    with pytest.raises(runner.StatusRunnerRefused):
        runner.run_once(**args(route))
    assert not (checkpoint / runner._PENDING).exists() and not (private / runner._PENDING).exists()


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


def test_model_root_replaced_during_child_is_unknown_and_quarantined(case, monkeypatch):
    runner, args, route, checkpoint, private, _ = case
    model = Path(route["trusted_model_root"])
    report = child_report()
    report["plan_sha256"] = route["plan_sha256"]
    def child(**kwargs):
        model.rename(model.with_name("displaced-synthetic-model"))
        model.mkdir()
        return report
    fake_child(monkeypatch, child)
    with pytest.raises(runner.StatusRunnerOutcomeUnknown):
        runner.run_once(**args())
    assert (checkpoint / runner._PENDING).is_file() and (private / runner._PENDING).is_file()


def test_source_parent_replaced_during_child_is_unknown_and_quarantined(case, monkeypatch):
    runner, args, route, checkpoint, private, _ = case
    source_dir = Path(route["source"]).parent
    report = child_report()
    report["plan_sha256"] = route["plan_sha256"]
    def child(**kwargs):
        source_dir.rename(source_dir.with_name("displaced-synthetic-source"))
        source_dir.mkdir()
        return report
    fake_child(monkeypatch, child)
    with pytest.raises(runner.StatusRunnerOutcomeUnknown):
        runner.run_once(**args())
    assert (checkpoint / runner._PENDING).is_file() and (private / runner._PENDING).is_file()


def test_sidecar_parent_replaced_during_child_is_unknown_and_quarantined(case, monkeypatch):
    runner, args, route, checkpoint, private, _ = case
    sidecar_dir = Path(route["source"]).parent.parent / "independent-sidecar"
    sidecar_dir.mkdir()
    (sidecar_dir / "generation.json").write_bytes(b"synthetic-sidecar")
    route = dict(route, generation_path=str(sidecar_dir / "generation.json"))
    report = child_report()
    report["plan_sha256"] = route["plan_sha256"]
    def child(**kwargs):
        sidecar_dir.rename(sidecar_dir.with_name("displaced-synthetic-sidecar"))
        sidecar_dir.mkdir()
        return report
    fake_child(monkeypatch, child)
    with pytest.raises(runner.StatusRunnerOutcomeUnknown):
        runner.run_once(**args(route))
    assert (checkpoint / runner._PENDING).is_file() and (private / runner._PENDING).is_file()


def test_sidecar_parent_replaced_before_reservation_is_refused_without_child(case, monkeypatch):
    runner, args, route, checkpoint, private, _ = case
    sidecar_dir = Path(route["source"]).parent.parent / "independent-sidecar"
    sidecar_dir.mkdir()
    (sidecar_dir / "generation.json").write_bytes(b"synthetic-sidecar")
    route = dict(route, generation_path=str(sidecar_dir / "generation.json"))
    calls = []
    fake_child(monkeypatch, lambda **kwargs: calls.append(kwargs))
    def token(_):
        sidecar_dir.rename(sidecar_dir.with_name("displaced-synthetic-sidecar"))
        sidecar_dir.mkdir()
        return "a" * 64
    monkeypatch.setattr(runner.secrets, "token_hex", token)
    with pytest.raises(runner.StatusRunnerOutcomeUnknown):
        runner.run_once(**args(route))
    assert calls == []
    assert not (checkpoint / runner._PENDING).exists() and not (private / runner._PENDING).exists()


@pytest.mark.parametrize("field", ["checkpoint_root", "private_state_root", "trusted_model_root"])
@pytest.mark.parametrize("relation", ["source_inside_root", "root_inside_source", "same_directory"])
def test_source_parent_and_all_roots_must_be_disjoint(case, monkeypatch, field, relation):
    runner, args, route, checkpoint, private, _ = case
    changed = dict(route)
    if relation == "source_inside_root":
        source_dir = Path(route[field]) / "nested-source"
        source_dir.mkdir()
        (source_dir / "events.jsonl").write_bytes(b"synthetic\n")
        changed["source"] = str(source_dir / "events.jsonl")
    elif relation == "root_inside_source":
        nested = Path(route["source"]).parent / "nested-root"
        nested.mkdir()
        changed[field] = str(nested)
    else:
        changed[field] = str(Path(route["source"]).parent)
    calls = []
    fake_child(monkeypatch, lambda **kwargs: calls.append(kwargs))
    with pytest.raises(runner.StatusRunnerRefused):
        runner.run_once(**args(changed))
    assert calls == []
    assert not (checkpoint / runner._PENDING).exists() and not (private / runner._PENDING).exists()


@pytest.mark.parametrize("field", ["checkpoint_root", "private_state_root", "trusted_model_root"])
@pytest.mark.parametrize("relation", ["sidecar_inside_root", "root_inside_sidecar", "same_directory"])
def test_sidecar_parent_and_all_roots_must_be_disjoint(case, monkeypatch, field, relation):
    runner, args, route, checkpoint, private, _ = case
    changed = dict(route)
    if relation == "sidecar_inside_root":
        sidecar_dir = Path(route[field]) / "nested-sidecar"
        sidecar_dir.mkdir()
        (sidecar_dir / "generation.json").write_bytes(b"synthetic")
        changed["generation_path"] = str(sidecar_dir / "generation.json")
    else:
        sidecar_dir = Path(route["source"]).parent.parent / "separate-sidecar"
        sidecar_dir.mkdir()
        (sidecar_dir / "generation.json").write_bytes(b"synthetic")
        changed["generation_path"] = str(sidecar_dir / "generation.json")
        if relation == "root_inside_sidecar":
            nested = sidecar_dir / "nested-root"
            nested.mkdir()
            changed[field] = str(nested)
        else:
            changed[field] = str(sidecar_dir)
    calls = []
    fake_child(monkeypatch, lambda **kwargs: calls.append(kwargs))
    with pytest.raises(runner.StatusRunnerRefused):
        runner.run_once(**args(changed))
    assert calls == []
    assert not (checkpoint / runner._PENDING).exists() and not (private / runner._PENDING).exists()


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


@pytest.mark.skipif(sys.platform != "win32", reason="synthetic producer uses Windows fixed-volume checks")
@pytest.mark.parametrize("fault", ["none", "before_receipt", "after_receipt", "after_first_unlink", "interrupt",
                                   "wrong_marker", "open_fence", "wrong_pointer", "wrong_private", "bad_landed"])
def test_real_fenced_child_two_renewable_updates(case, monkeypatch, fault):
    pytest.importorskip("faiss", reason="synthetic builder requires FAISS")
    from sandbox.mind.step2 import encode as encoder
    from sandbox.mind.step2.test_wd_q4_prefix_build_child import FakeModel
    from waggledance.core.bridge_log_reader import BridgeCursor, read_bridge_log

    runner, args, route, checkpoint, private, _ = case
    kw = args()
    kw["renewable"] = True
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
        if fault != "none":
            from tools import wd_q4_status_prefix_recovery as recovery
            if fault == "before_receipt":
                monkeypatch.setattr(recovery, "_write_receipt", lambda *_: (_ for _ in ()).throw(OSError("synthetic")))
            elif fault in ("after_receipt", "interrupt"):
                original_write = recovery._write_receipt
                def write_then_fail(*values):
                    original_write(*values)
                    raise (KeyboardInterrupt() if fault == "interrupt" else OSError("synthetic"))
                monkeypatch.setattr(recovery, "_write_receipt", write_then_fail)
            elif fault == "after_first_unlink":
                unlink = recovery.os.unlink
                def unlink_then_fail(path):
                    unlink(path)
                    raise OSError("synthetic")
                monkeypatch.setattr(recovery.os, "unlink", unlink_then_fail)
            elif fault == "wrong_marker":
                (checkpoint / runner._PENDING).write_bytes(b"foreign-token")
            elif fault == "open_fence":
                monkeypatch.setattr(recovery.checkpoint_mod, "_active_fence", lambda *_: ("synthetic",))
            elif fault == "wrong_pointer":
                result = {**result, "pointer_sha256": "e" * 64}
            elif fault == "wrong_private":
                (private / "prefix-private-state.json").write_bytes(b"synthetic-corruption")
            elif fault == "bad_landed":
                path = recovery.checkpoint_mod._fence_record(checkpoint, result["pointer_sha256"], "landed")
                path.write_bytes(b"synthetic-corruption")
        return result
    monkeypatch.setattr(actual_child, "build_prefix_generation", observe_child)
    if fault != "none":
        expected = runner.StatusRunnerInterrupted if fault == "interrupt" else runner.StatusRunnerOutcomeUnknown
        with pytest.raises(expected):
            runner.run_once(**kw)
        assert observations and observations[0][0] == "result"
        assert (private / runner._PENDING).exists()
        assert (checkpoint / runner._PENDING).exists() == (fault != "after_first_unlink")
        receipts = list(private.glob("q4-status-result-*.json"))
        assert bool(receipts) == (fault in ("after_receipt", "after_first_unlink", "interrupt"))
        return
    try:
        result = runner.run_once(**kw)
    except runner.StatusRunnerOutcomeUnknown:
        pytest.fail(f"synthetic child observation: {observations!r}")
    assert result["status"] == "PUBLISHED_GENESIS"
    receipts = list(private.glob("q4-status-result-*.json"))
    assert len(receipts) == 1 and sha(receipts[0].read_bytes()) == result["runner_receipt_sha256"]
    assert result["freshness"] == result["source_authenticity"] == "UNKNOWN"
    projection = (checkpoint / plan["generation"] / "projection.json").read_bytes()
    records = json.loads(projection)["records"]
    assert len(records) == 1 and records[0]["stored_text"] == f"status info codex-lead-1 {TASK}"
    assert b"synthetic-private-body" not in projection
    assert b"unapproved-task" not in projection and b"claude-rco-1" not in projection
    assert not (checkpoint / runner._PENDING).exists() and not (private / runner._PENDING).exists()
    from sandbox.mind.step2 import shared_index_checkpoint as cp
    from sandbox.mind.step2 import prefix_private_state as ps
    from sandbox.mind.step2 import shared_index_preflight as preflight
    from tools import wd_q4_lane_binding_producer as binding_producer
    from tools import wd_q4_lane_binding as binding_consumer
    from tools import wd_q4_secure_transport as transport
    def validate_actual_binding(published, source_anchor):
        pointer_raw = (checkpoint / cp.POINTER).read_bytes()
        pointer = json.loads(pointer_raw)
        assert sha(pointer_raw) == published["pointer_sha256"]
        active = {name: (checkpoint / pointer["generation"] / name).read_bytes() for name in cp.ARTIFACTS.values()}
        public_snapshot = transport.fixed_snapshot_bundle(pointer_bytes=pointer_raw,
            policy_bytes=kw["policy_bytes"], roster_bytes=kw["roster_bytes"],
            public_policy_bytes=kw["public_policy_bytes"], active_artifact_bytes=active,
            history_bytes={}, cell_snapshots={})
        wrapper = b"synthetic-wrapper"
        manifest = encode({"schema_version": 1, "source_commit": "a" * 40,
                           "files": {"wd-fleet.json": sha(kw["fleet_bytes"]),
                                     "Invoke-WdQ4Query.ps1": sha(wrapper)}})
        descriptor = json.loads(active["descriptor.json"])
        plan_doc = {"schema": "wd.q4-public-wrapper-plan.v1",
                    "config": {"descriptor_sha256": sha(active["descriptor.json"])},
                    "root": "synthetic", "policy_path": "synthetic", "roster_path": "synthetic",
                    "public_policy_path": "synthetic", "pointer_sha256": sha(pointer_raw),
                    "source_anchor_sha256": sha(source_anchor),
                    "encoder_sha256": preflight._encoder_digest(descriptor["encoder"]),
                    "policy_sha256": route["policy_sha256"], "roster_sha256": route["roster_sha256"],
                    "public_policy_sha256": route["public_policy_sha256"]}
        public_plan = encode(plan_doc)
        produced = binding_producer.build_lane_binding(enabled=True, expected_commit="a" * 40,
            manifest_bytes=manifest, expected_manifest_sha256=sha(manifest),
            fleet_bytes=kw["fleet_bytes"], expected_fleet_sha256=sha(kw["fleet_bytes"]),
            wrapper_bytes=wrapper, expected_wrapper_sha256=sha(wrapper),
            public_plan_bytes=public_plan, expected_public_plan_sha256=sha(public_plan),
            snapshot_bytes=public_snapshot, expected_snapshot_sha256=sha(public_snapshot))
        for lane in LANES:
            pinned = binding_consumer.resolve_lane_binding(kw["fleet_bytes"], sha(kw["fleet_bytes"]),
                produced["binding_bytes"], produced["binding_sha256"], lane=lane, enabled=True)
            assert pinned["expected_binding"]["roster_sha256"] == route["roster_sha256"]
            assert pinned["expected_binding"]["pointer_sha256"] == sha(pointer_raw)
    validate_actual_binding(result, anchor)
    tasks, _ = runner.adapter.strict_pins(kw["policy_bytes"], route["policy_sha256"], kw["roster_bytes"], route["roster_sha256"])
    view, prefix = cp.read_verified_prefix_pointer(checkpoint, tasks, expected_pointer_sha256=result["pointer_sha256"],
        policy_bytes=kw["policy_bytes"], policy_sha256=route["policy_sha256"], roster_bytes=kw["roster_bytes"],
        roster_sha256=route["roster_sha256"], expected_public_policy_sha256=route["public_policy_sha256"])
    with ps.locked_state(private, checkpoint, enabled=True) as locked:
        locked.require(result["pointer_sha256"], prefix)
    assert not (private / ps._PENDING).exists() and not (checkpoint / cp.FENCE).exists()
    with source.open("ab") as out:
        out.write(encode(dict(base, ts_utc="2026-10-31T12:01:00Z")) + b"\n")
    anchor_doc = json.loads(anchor)
    anchor_doc["cursor_start"] = asdict(prefix.cursor)
    anchor2 = encode(anchor_doc)
    plan["generation"] = "synthetic-generation-2"
    plan["expected_pointer_sha256"] = result["pointer_sha256"]
    plan["pins"]["anchor_sha256"] = sha(anchor2)
    plan2 = encode(plan)
    route.update(cursor=asdict(prefix.cursor), prefix_sha256=prefix.sha256,
                 anchor_sha256=sha(anchor2), plan_sha256=sha(plan2))
    route2 = encode(route)
    kw.update(plan_bytes=plan2, anchor_bytes=anchor2, route_bytes=route2, expected_route_sha256=sha(route2))
    result2 = runner.run_once(**kw)
    assert result2["status"] == "PUBLISHED_HISTORY"
    assert result2["pointer_sha256"] != result["pointer_sha256"]
    validate_actual_binding(result2, anchor2)
    assert not (checkpoint / runner._PENDING).exists() and not (private / runner._PENDING).exists()
    view2, prefix2 = cp.read_verified_prefix_pointer(checkpoint, tasks, expected_pointer_sha256=result2["pointer_sha256"],
        policy_bytes=kw["policy_bytes"], policy_sha256=route["policy_sha256"], roster_bytes=kw["roster_bytes"],
        roster_sha256=route["roster_sha256"], expected_public_policy_sha256=route["public_policy_sha256"])
    with ps.locked_state(private, checkpoint, enabled=True) as locked:
        locked.require(result2["pointer_sha256"], prefix2)
    assert not (private / ps._PENDING).exists() and not (checkpoint / cp.FENCE).exists()

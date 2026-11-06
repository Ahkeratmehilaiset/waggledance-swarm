"""Continuation admission/termination with synthetic files; no encoder or native run."""
import base64
from contextlib import contextmanager
from dataclasses import asdict, replace
import importlib
import json
from types import SimpleNamespace

import pytest
from tests.tools.test_wd_q4_genesis_bootstrap import synthetic as genesis_synthetic


def raw(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("ascii")


def sha(value):
    import hashlib
    return hashlib.sha256(value).hexdigest()


@pytest.fixture
def synthetic(tmp_path, monkeypatch):
    mod = importlib.import_module("tools.wd_q4_production_continuation")
    from waggledance.core.bridge_log_reader import BridgeCursor, BridgePrefixPin, _file_identity, _open_log
    source, side, shared, private, model = (tmp_path / name for name in
                                            ("source", "side", "shared", "private", "model"))
    for directory in (source, side, shared, private, model):
        directory.mkdir()
    log, sidecar = source / "events.jsonl", side / "mind-generation.json"
    log.write_bytes(b"row\n" * 4)
    sidecar.write_bytes(raw({"generation": "synthetic"}))
    with _open_log(log) as stream:
        identity = _file_identity(stream)
    names = ("codex-lead-1", "codex-tools-1", "claude-rco-1", "claude-rco-2", "fable-5")
    lanes = [{"agent": name, "agent_uuid": f"{i:08d}-1111-4111-8111-111111111111"}
             for i, name in enumerate(names, 1)]
    fleet = raw({"lanes": [lane for lane in lanes if lane["agent"] != "codex-tools-1"],
                 "tools_supervisor": lanes[1]})
    task = "lead-mind-production-20261031"
    policy = raw({"schema": "wd.q4-non-review-policy.v1", "approved_tasks": [task]})
    public = raw({"schema": "wd.mind-public-status-policy.v1", "public_tasks": [task],
                  "public_types": ["status"]})
    for key, data in (("_FLEET_SHA", fleet), ("_POLICY_SHA", policy), ("_PUBLIC_SHA", public)):
        monkeypatch.setattr(mod.runner, key, frozenset({sha(data)}))
    roster_bytes, roster_pin = mod.roster.build_roster_snapshot(fleet, sha(fleet), enabled=True)
    start = BridgeCursor(offset=0, file_identity=identity, generation="synthetic")
    anchor, anchor_pin = mod.issuer.issue_anchor(enabled=True, source_path=str(log),
        generation_path=str(sidecar), cursor=start, policy_bytes=policy, policy_sha256=sha(policy),
        roster_bytes=roster_bytes, roster_sha256=roster_pin,
        public_policy_bytes=public, public_policy_sha256=sha(public))
    pins = {"anchor_sha256": anchor_pin, "policy_sha256": sha(policy),
            "public_policy_sha256": sha(public), "roster_sha256": roster_pin}
    plan = {"schema": "wd.q4-prefix-build-plan.v3", "generation": "synthetic",
            "expected_pointer_sha256": None, "max_prefix_bytes": 4096, "max_windows": 1,
            "timeout": 1, "encoder": {"model": "intfloat/multilingual-e5-small",
            "revision": "a" * 40, "model_dir": str(model), "weights_sha256": "b" * 64,
            "dim": 384, "assets": {"model.safetensors": "b" * 64}}, "fenced": True, "pins": pins}
    route = {"schema": "wd.q4-status-prefix-route.v1", "source": str(log),
             "generation_path": str(sidecar), "checkpoint_root": str(shared),
             "private_state_root": str(private), "trusted_model_root": str(model),
             "cursor": asdict(start), "prefix_sha256": sha(b""), "max_bytes": 4096,
             "max_rows": 2, "plan_sha256": sha(raw(plan)), "cutoff_utc": "2026-10-31T13:00:00Z",
             "batch_rows": True, **pins}
    document = {"schema": "wd.q4-genesis-private-tuple.v1", "publication": "UNKNOWN",
                "allow_partial_genesis": True, "independent_source_pin": {"identity": identity,
                "prefix_bytes": 16, "prefix_sha256": sha(log.read_bytes())},
                **{name: {"bytes_b64": base64.b64encode(data).decode("ascii"), "sha256": sha(data)}
                   for name, data in (("anchor", anchor), ("route", raw(route)), ("plan", raw(plan)))}}
    state = {"pointer": "c" * 64, "prefix": BridgePrefixPin(replace(start, offset=4), sha(b"row\n")),
             "anchor_pin": anchor_pin, "calls": [], "reads": [], "requires": [], "fault": None,
             "post_fault": None, "coverage": "partial_ready_prefix", "status": "PUBLISHED_HISTORY"}

    def read_pointer(root, tasks, **kwargs):
        state["reads"].append(kwargs)
        if state["fault"] == "pointer" or kwargs["expected_pointer_sha256"] != state["pointer"]:
            raise ValueError("synthetic-private-pointer-detail")
        return {"anchor_sha256": state["anchor_pin"]}, state["prefix"]

    @contextmanager
    def locked_state(*args, **kwargs):
        def require(pointer, prefix):
            state["requires"].append((pointer, prefix))
            if state["fault"] == "private" or (pointer, prefix) != (state["pointer"], state["prefix"]):
                raise ValueError("synthetic-private-state-detail")
        yield SimpleNamespace(require=require)

    def publish(**kwargs):
        state["calls"].append(kwargs)
        if state["fault"] == "refused":
            raise mod.runner.StatusRunnerRefused("synthetic-private-refusal")
        if state["fault"] == "unknown":
            raise mod.runner.StatusRunnerOutcomeUnknown("synthetic-private-unknown")
        if state["fault"] == "interrupt":
            raise KeyboardInterrupt("synthetic-private-interrupt")
        sent_plan, sent_route = json.loads(kwargs["plan_bytes"]), json.loads(kwargs["route_bytes"])
        if state["status"] != "EMPTY_NO_CHANGE":
            offset = 16 if state["coverage"] == "caught_up_to_final_snapshot_not_history" else 8
            state["pointer"] = sha(kwargs["plan_bytes"])
            state["prefix"] = BridgePrefixPin(replace(start, offset=offset), sha(log.read_bytes()[:offset]))
            state["anchor_pin"] = sent_route["anchor_sha256"]
        if state["post_fault"] == "anchor":
            state["anchor_pin"] = "0" * 64
        elif state["post_fault"] == "nonadvancing":
            state["prefix"] = BridgePrefixPin(replace(start, offset=4), sha(b"row\n"))
        elif state["post_fault"] == "identity":
            state["prefix"] = replace(state["prefix"], cursor=replace(state["prefix"].cursor,
                file_identity="windows-v1:00000000:0000000000000000"))
        elif state["post_fault"] == "source":
            log.write_bytes(b"BAD\n" * 4)
        return {"status": state["status"], "pointer_sha256": state["pointer"],
                "generation": None if state["status"] == "EMPTY_NO_CHANGE" else sent_plan["generation"],
                "publisher_authentication": "UNKNOWN", "source_authenticity": "UNKNOWN", "freshness": "UNKNOWN",
                "coverage": state["coverage"], "source_refusals": "NOT_AGGREGATED",
                "encoder_identity_source": "encode.model_identity", "plan_sha256": sha(kwargs["plan_bytes"]),
                "backend_isolation": "in_process_environment_restored_no_os_isolation",
                "model_assets": "caller_manifest_verified_before_load_reread_by_backend",
                "runner_receipt_sha256": "d" * 64}

    monkeypatch.setattr(mod.checkpoint, "read_verified_prefix_pointer", read_pointer)
    monkeypatch.setattr(mod.private_state, "locked_state", locked_state)
    monkeypatch.setattr(mod.runner, "run_once", publish)
    args = {"enabled": True, "tuple_bytes": raw(document), "expected_tuple_sha256": sha(raw(document)),
            "expected_pointer_sha256": state["pointer"], "policy_bytes": policy,
            "public_policy_bytes": public, "fleet_bytes": fleet, "expected_fleet_sha256": sha(fleet)}
    return mod, args, state, document, route, plan, log


def update_document(args, document):
    args.update(tuple_bytes=raw(document), expected_tuple_sha256=sha(raw(document)))


def test_default_off_reads_nothing():
    mod = importlib.import_module("tools.wd_q4_production_continuation")
    assert mod.continue_once() == {"status": "OFF"}


def test_fresh_anchor_and_actual_pointer_retain_pinned_controls(synthetic):
    mod, args, state, _, original_route, original_plan, _ = synthetic
    original = args["tuple_bytes"]
    result = mod.continue_once(**args)
    assert result["status"] == "partial_ready_prefix" and result["runner_calls"] == 1
    assert len(state["calls"]) == 1 and len(state["requires"]) == 2
    call = state["calls"][0]
    route, plan, anchor = map(json.loads, (call["route_bytes"], call["plan_bytes"], call["anchor_bytes"]))
    assert route["cursor"]["offset"] == anchor["cursor_start"]["offset"] == 4
    assert route["anchor_sha256"] == sha(call["anchor_bytes"]) != original_route["anchor_sha256"]
    assert route["plan_sha256"] == sha(call["plan_bytes"])
    assert call["expected_route_sha256"] == sha(call["route_bytes"]) and call["renewable"] is True
    assert plan["expected_pointer_sha256"] == args["expected_pointer_sha256"]
    for field in ("source", "generation_path", "checkpoint_root", "private_state_root", "trusted_model_root",
                  "cutoff_utc", "batch_rows", "max_bytes", "max_rows", "policy_sha256", "roster_sha256", "public_policy_sha256"):
        assert route[field] == original_route[field]
    for field in ("encoder", "max_prefix_bytes", "max_windows", "timeout", "fenced"):
        assert plan[field] == original_plan[field]
    assert args["tuple_bytes"] == original and "prefix_sha256" not in result
    assert result["source_authenticity"] == result["freshness"] == "UNKNOWN"


def test_next_explicit_call_uses_new_pointer_and_stops_at_cutoff(synthetic):
    mod, args, state, *_ = synthetic
    assert mod.continue_once(**args)["status"] == "partial_ready_prefix"
    args["expected_pointer_sha256"] = state["pointer"]
    state["coverage"] = "caught_up_to_final_snapshot_not_history"
    result = mod.continue_once(**args)
    assert result["status"] == "caught_up_to_cutoff" and len(state["calls"]) == 2
    assert json.loads(state["calls"][1]["route_bytes"])["cursor"]["offset"] == 8


@pytest.mark.parametrize("shape", ["outer_pin", "base64", "inner_pin", "extra_field", "no_source_pin",
    "no_cutoff", "false_batch", "anchor_path", "source_identity", "plan_pin", "source_rewrite"])
def test_unrecognized_or_inconsistent_tuple_refuses_before_runner(synthetic, shape):
    mod, args, state, document, route, _, log = synthetic
    if shape == "outer_pin":
        args["expected_tuple_sha256"] = "0" * 64
    elif shape == "source_rewrite":
        log.write_bytes(b"BAD\n" * 4)
    else:
        if shape == "base64":
            document["anchor"]["bytes_b64"] = "!"
        elif shape == "inner_pin":
            document["anchor"]["sha256"] = "0" * 64
        elif shape == "extra_field":
            document["unexpected"] = True
        elif shape == "no_source_pin":
            document["independent_source_pin"] = None
        elif shape == "source_identity":
            document["independent_source_pin"]["identity"] = "windows-v1:00000000:0000000000000000"
        elif shape == "anchor_path":
            anchor = json.loads(base64.b64decode(document["anchor"]["bytes_b64"]))
            anchor["source_path"] += ".other"
            document["anchor"] = {"bytes_b64": base64.b64encode(raw(anchor)).decode(), "sha256": sha(raw(anchor))}
        else:
            if shape == "no_cutoff":
                route.pop("cutoff_utc")
            elif shape == "false_batch":
                route["batch_rows"] = False
            else:
                route["plan_sha256"] = "0" * 64
            document["route"] = {"bytes_b64": base64.b64encode(raw(route)).decode(), "sha256": sha(raw(route))}
        update_document(args, document)
    with pytest.raises(mod.ContinuationRefused, match="^continuation_refused$"):
        mod.continue_once(**args)
    assert state["calls"] == []


@pytest.mark.parametrize("fault", ["pointer", "private", "refused"])
def test_pending_or_changed_state_or_typed_prewrite_refusal_never_retries(synthetic, fault):
    mod, args, state, *_ = synthetic
    state["fault"] = fault
    with pytest.raises(mod.ContinuationRefused, match="^continuation_refused$"):
        mod.continue_once(**args)
    assert len(state["calls"]) == (1 if fault == "refused" else 0)


@pytest.mark.parametrize("fault", ["unknown", "interrupt"])
def test_uncertain_runner_result_is_sanitized_and_never_retried(synthetic, fault):
    mod, args, state, *_ = synthetic
    state["fault"] = fault
    with pytest.raises(mod.ContinuationUnknown, match="^continuation_state_unknown$"):
        mod.continue_once(**args)
    assert len(state["calls"]) == 1


@pytest.mark.parametrize("fault", ["anchor", "nonadvancing", "identity", "source"])
def test_postwrite_verification_failure_stays_unknown(synthetic, fault):
    mod, args, state, *_ = synthetic
    state["post_fault"] = fault
    with pytest.raises(mod.ContinuationUnknown, match="^continuation_state_unknown$"):
        mod.continue_once(**args)
    assert len(state["calls"]) == 1


def test_empty_no_change_is_deferred_without_another_runner_call(synthetic):
    mod, args, state, *_ = synthetic
    state["status"] = "EMPTY_NO_CHANGE"
    result = mod.continue_once(**args)
    assert result["status"] == "DEFERRED" and len(state["calls"]) == 1
    assert result["pointer_sha256"] == args["expected_pointer_sha256"]


def test_unbatched_route_preserves_absence_of_batch_option(synthetic):
    mod, args, state, document, route, *_ = synthetic
    route.pop("batch_rows")
    document["route"] = {"bytes_b64": base64.b64encode(raw(route)).decode(), "sha256": sha(raw(route))}
    update_document(args, document)
    mod.continue_once(**args)
    assert "batch_rows" not in json.loads(state["calls"][0]["route_bytes"])


def test_explicit_cutoff_defers_then_publishes_with_private_route_evidence(synthetic, monkeypatch):
    from pathlib import Path
    mod, args, state, _, route, *_ = synthetic
    monkeypatch.setattr(mod, "_checkpoint_cutoff", lambda *_: "2026-10-31T13:00:00Z", raising=False)
    state["status"] = "EMPTY_NO_CHANGE"
    first = mod.continue_once(**args, cutoff_utc="2026-10-31T13:00:00Z",
                              previous_cutoff_utc="2026-10-31T13:00:00Z")
    assert first["status"] == "DEFERRED" and first["cutoff_utc"] == "2026-10-31T13:00:00Z"
    assert first["cutoff_monotonicity"] == "CALLER_ASSERTED_REPLAY_CRASH_UNPROVEN"
    state["status"] = "PUBLISHED_HISTORY"
    second = mod.continue_once(**args, cutoff_utc="2026-10-31T13:02:00Z",
                               previous_cutoff_utc=first["cutoff_utc"])
    assert second["status"] == "partial_ready_prefix" and second["cutoff_utc"] == "2026-10-31T13:02:00Z"
    assert len(state["calls"]) == 2
    for result, call in zip((first, second), state["calls"]):
        route_bytes = call["route_bytes"]
        assert json.loads(route_bytes)["cutoff_utc"] == result["cutoff_utc"]
        assert result["route_sha256"] == sha(route_bytes) == call["expected_route_sha256"]
        saved = Path(route["private_state_root"]) / ("q4-continuation-route-" + result["route_sha256"] + ".json")
        assert saved.read_bytes() == route_bytes


@pytest.mark.parametrize("requested,previous,checkpoint", [
    ("2026-10-31T12:59:59Z", "2026-10-31T12:59:59Z", "2026-10-31T12:59:59Z"),
    ("2026-10-31T13:01:00Z", "2026-10-31T13:02:00Z", "2026-10-31T13:00:00Z"),
    ("2026-10-31T13:01:00Z", "2026-10-31T13:00:00Z", "2026-10-31T13:02:00Z"),
    ("2026-10-31T13:03:00Z", "2026-10-31T13:00:00Z", "2026-10-31T13:02:00Z"),
    ("not-a-time", "2026-10-31T13:00:00Z", "2026-10-31T13:00:00Z"),
])
def test_explicit_cutoff_cannot_go_below_any_pinned_floor(synthetic, monkeypatch, requested, previous, checkpoint):
    mod, args, state, *_ = synthetic
    monkeypatch.setattr(mod, "_checkpoint_cutoff", lambda *_: checkpoint, raising=False)
    with pytest.raises(mod.ContinuationRefused):
        mod.continue_once(**args, cutoff_utc=requested, previous_cutoff_utc=previous)
    assert state["calls"] == []


def test_real_late_genesis_deferred_row_then_later_cutoff(genesis_synthetic, monkeypatch):
    """Real skip/runner/producer/checkpoint; synthetic encoder, no model call."""
    from pathlib import Path
    from tests.tools import test_wd_q4_genesis_bootstrap as base
    from tools import wd_q4_production_continuation as mod
    args, _ = genesis_synthetic
    base.test_late_skip_real_producer_publishes_every_public_row_after_the_skip(genesis_synthetic, monkeypatch)
    source = Path(args["source_path"])
    row = json.loads(base.f2_rows(args, ["public"])[0])
    row["ts_utc"] = "2026-10-31T13:00:30Z"
    row["status"] = "done"
    late = base.raw(row) + b"\n"
    with source.open("ab") as stream:
        stream.write(late)
    raw_tuple = (Path(args["private_state_root"]) / "q4-genesis-tuple-v1.json").read_bytes()
    pointer_path = Path(args["checkpoint_root"]) / "q4-latest.json"
    common = {"enabled": True, "tuple_bytes": raw_tuple, "expected_tuple_sha256": sha(raw_tuple),
              "expected_pointer_sha256": sha(pointer_path.read_bytes()), "policy_bytes": args["policy_bytes"],
              "public_policy_bytes": args["public_policy_bytes"], "fleet_bytes": args["fleet_bytes"],
              "expected_fleet_sha256": args["expected_fleet_sha256"]}
    first = mod.continue_once(**common, cutoff_utc="2026-10-31T13:00:00Z",
                              previous_cutoff_utc="2026-10-31T13:00:00Z")
    assert first["status"] == "DEFERRED" and first["pointer_sha256"] == common["expected_pointer_sha256"]
    common["expected_pointer_sha256"] = first["pointer_sha256"]
    second = mod.continue_once(**common, cutoff_utc="2026-10-31T13:02:00Z",
                               previous_cutoff_utc=first["cutoff_utc"])
    assert second["status"] == "caught_up_to_cutoff" and second["pointer_sha256"] != first["pointer_sha256"]
    pointer = json.loads(pointer_path.read_bytes())
    projection = json.loads((Path(args["checkpoint_root"]) / pointer["generation"] / "projection.json").read_bytes())
    assert any(record["meta"].get("status") == "done" for record in projection["records"])
    private = Path(args["private_state_root"])
    for report in (first, second):
        route_bytes = (private / ("q4-continuation-route-" + report["route_sha256"] + ".json")).read_bytes()
        assert sha(route_bytes) == report["route_sha256"]
        assert json.loads(route_bytes)["cutoff_utc"] == report["cutoff_utc"]
        receipts = [p.read_bytes() for p in private.glob("q4-status-result-*.json")]
        receipt, = [raw for raw in receipts if sha(raw) == report["runner_receipt_sha256"]]
        assert json.loads(receipt)["route_sha256"] == report["route_sha256"]
    # A changed skip receipt must not authorize another continuation.
    skip = private / mod.runner.GENESIS_SKIP_NAME
    original_skip = skip.read_bytes()
    skip.write_bytes(original_skip + b" ")
    before = pointer_path.read_bytes()
    common["expected_pointer_sha256"] = sha(before)
    with pytest.raises(mod.ContinuationRefused):
        mod.continue_once(**common, cutoff_utc="2026-10-31T13:03:00Z",
                          previous_cutoff_utc="2026-10-31T13:02:00Z")
    assert pointer_path.read_bytes() == before
    skip.write_bytes(original_skip)
    original_source = source.read_bytes()
    source.write_bytes(b"X" + original_source[1:])
    with pytest.raises(mod.ContinuationRefused):
        mod.continue_once(**common, cutoff_utc="2026-10-31T13:03:00Z",
                          previous_cutoff_utc="2026-10-31T13:02:00Z")
    assert pointer_path.read_bytes() == before

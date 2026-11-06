"""Default-off, one-step continuation from a caller-pinned private genesis tuple.

This does not schedule, activate, authenticate or recover an interrupted writer.
Each explicit call derives one fresh route from a verified checkpoint and its
matching private prefix. Only the existing renewable fenced runner publishes.
A partial or deferred result ends this call; uncertain outcomes never retry.

The caller must authorize/protect all paths, source access, runtime and pins.
Same-user interference, power-loss durability and uninterrupted continuity are
not established. caught_up_to_cutoff means this bounded call covered an observed
snapshot under the caller's fixed cutoff, not currentness or complete history.
The tuple is private input, never a publication or authentication receipt.
"""
import base64
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import secrets

from sandbox.mind.step2 import prefix_private_state as private_state
from sandbox.mind.step2 import shared_capture_adapter as capture
from sandbox.mind.step2 import shared_index_checkpoint as checkpoint
from tools import wd_mind_anchor_issuer as issuer
from tools import wd_q4_genesis_bootstrap as genesis
from tools import wd_q4_roster_from_fleet as roster
from tools import wd_q4_status_prefix_runner as runner
from waggledance.core.bridge_log_reader import BridgeCursor, BridgePrefixPin, _open_log


class ContinuationRefused(ValueError):
    """No publication was attempted; a cooperative reader lock may have existed."""


class ContinuationUnknown(RuntimeError):
    """Publication may have occurred: reconcile externally, never retry blindly."""


_TUPLE_KEYS = frozenset({"schema", "publication", "allow_partial_genesis", "independent_source_pin",
                         "anchor", "route", "plan"})


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _raw(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                      allow_nan=False).encode("ascii")


def _part(value, limit):
    if type(value) is not dict or set(value) != {"bytes_b64", "sha256"}:
        raise ValueError()
    if type(value["bytes_b64"]) is not str:
        raise ValueError()
    raw = base64.b64decode(value["bytes_b64"], validate=True)
    runner._pinned(raw, value["sha256"], limit=limit)
    return raw


def _inputs(tuple_bytes, tuple_pin, pointer_pin, policy_bytes, public_bytes, fleet_bytes, fleet_pin):
    runner._pinned(tuple_bytes, tuple_pin)
    document = runner._json(tuple_bytes, 65536)
    if (set(document) != _TUPLE_KEYS or document["schema"] != "wd.q4-genesis-private-tuple.v1"
            or document["publication"] != "UNKNOWN" or type(document["allow_partial_genesis"]) is not bool
            or type(pointer_pin) is not str or runner._HEX.fullmatch(pointer_pin) is None):
        raise ValueError()
    route_raw, plan_raw, anchor_raw = (_part(document[name], limit)
                                     for name, limit in (("route", 65536), ("plan", 65536), ("anchor", 16384)))
    route, plan, anchor = (runner._json(value, limit)
                           for value, limit in ((route_raw, 65536), (plan_raw, 65536), (anchor_raw, 16384)))
    if (set(route) not in (runner._ROUTE_KEYS | {"cutoff_utc"},
                          runner._ROUTE_KEYS | {"cutoff_utc", "batch_rows"},
                          runner._ROUTE_KEYS | {"cutoff_utc", "batch_rows", "genesis_skip_sha256"})
            or route["schema"] != "wd.q4-status-prefix-route.v1"
            or ("batch_rows" in route and route["batch_rows"] is not True)):
        raise ValueError()
    runner._cutoff_datetime(route["cutoff_utc"])
    runner._pinned(plan_raw, route["plan_sha256"])
    runner._pinned(anchor_raw, route["anchor_sha256"], limit=16384)
    runner._pinned(policy_bytes, route["policy_sha256"], runner._POLICY_SHA)
    runner._pinned(public_bytes, route["public_policy_sha256"], runner._PUBLIC_SHA)
    runner._pinned(fleet_bytes, fleet_pin, runner._FLEET_SHA, limit=1024 * 1024)
    roster_bytes, roster_pin = roster.build_roster_snapshot(fleet_bytes, fleet_pin, enabled=True)
    if roster_pin != route["roster_sha256"]:
        raise ValueError()
    tasks, _ = capture.strict_pins(policy_bytes, route["policy_sha256"], roster_bytes, roster_pin)
    public_tasks, public_types = capture.public_status_policy(public_bytes, route["public_policy_sha256"], tasks)
    if tasks != frozenset({runner._TASK}) or public_tasks != tasks or public_types != frozenset({"status"}):
        raise ValueError()
    from sandbox.mind.step2.wd_q4_prefix_build_child import _plan_shape
    if (not _plan_shape(plan) or plan["schema"] != "wd.q4-prefix-build-plan.v3"
            or plan["fenced"] is not True or plan["expected_pointer_sha256"] is not None
            or plan["pins"] != {key: route[key] for key in
                                 ("anchor_sha256", "policy_sha256", "roster_sha256", "public_policy_sha256")}
            or type(route["max_bytes"]) is not int or not 1 <= route["max_bytes"] <= 4 * 1024 * 1024
            or type(route["max_rows"]) is not int or not 1 <= route["max_rows"] <= 10000
            or plan["max_windows"] * (2 * plan["max_prefix_bytes"] + route["max_bytes"]) > 4 * 1024**3):
        raise ValueError()
    cursor = route["cursor"]
    skip_pin = route.get("genesis_skip_sha256")
    if (type(cursor) is not dict or set(cursor) != {"offset", "file_identity", "generation"}
            or type(cursor["offset"]) is not int or not 0 <= cursor["offset"] <= plan["max_prefix_bytes"]
            or type(cursor["file_identity"]) is not str or runner._IDENTITY.fullmatch(cursor["file_identity"]) is None
            or type(cursor["generation"]) is not str or runner._GENERATION.fullmatch(cursor["generation"]) is None
            or type(route["prefix_sha256"]) is not str or runner._HEX.fullmatch(route["prefix_sha256"]) is None
            or (skip_pin is None and (cursor["offset"] != 0 or route["prefix_sha256"] != _sha(b"")))
            or (skip_pin is not None and (type(skip_pin) is not str or runner._HEX.fullmatch(skip_pin) is None
                                          or cursor["offset"] == 0))):
        raise ValueError()
    expected_anchor = {"schema": "wd.q4-source-anchor.v1", "source_path": route["source"],
        "generation_path": route["generation_path"], "cursor_start": cursor,
        "policy_sha256": route["policy_sha256"], "roster_sha256": roster_pin,
        "status_mode": "public-v1", "public_policy_sha256": route["public_policy_sha256"]}
    if anchor != expected_anchor:
        raise ValueError()
    pin = document["independent_source_pin"]
    if (type(pin) is not dict or set(pin) != {"identity", "prefix_bytes", "prefix_sha256"}
            or pin["identity"] != cursor["file_identity"] or type(pin["prefix_bytes"]) is not int
            or not 0 < pin["prefix_bytes"] <= plan["max_prefix_bytes"]
            or type(pin["prefix_sha256"]) is not str or runner._HEX.fullmatch(pin["prefix_sha256"]) is None):
        raise ValueError()
    # These remain caller-owned inputs, not authority inferred from a hash.
    source, sidecar = runner._plain_file(route["source"]), runner._plain_file(route["generation_path"])
    roots = [genesis.roots._root(route[key]) for key in
             ("checkpoint_root", "private_state_root", "trusted_model_root")]
    for root in roots:
        genesis.roots._observe(root)
    if (any(not runner._separate(left, right) for i, left in enumerate(roots) for right in roots[i + 1:])
            or any(not runner._separate(parent, root) for parent in (source.parent, sidecar.parent) for root in roots)):
        raise ValueError()
    if skip_pin is not None:
        skip_raw = runner._read_genesis_skip(roots[1])
        skip = runner._genesis_skip(skip_raw)
        if (_sha(skip_raw) != skip_pin or skip["file_identity"] != cursor["file_identity"]
                or skip["generation"] != cursor["generation"] or skip["offset"] != cursor["offset"]
                or skip["prefix_sha256"] != route["prefix_sha256"]
                or any(skip[key] != route[key] for key in ("policy_sha256", "roster_sha256", "public_policy_sha256"))):
            raise ValueError()
        with _open_log(source) as stream:
            genesis._same_source(source, stream, skip["file_identity"], skip["offset"], skip["prefix_sha256"])
    return route, plan, pin, roster_bytes, tasks


def _prefix(prefix, route, plan):
    if (type(prefix) is not BridgePrefixPin or type(prefix.cursor) is not BridgeCursor
            or prefix.cursor.file_identity != route["cursor"]["file_identity"]
            or prefix.cursor.generation != route["cursor"]["generation"]
            or type(prefix.cursor.offset) is not int or not 0 < prefix.cursor.offset <= plan["max_prefix_bytes"]
            or type(prefix.sha256) is not str or runner._HEX.fullmatch(prefix.sha256) is None):
        raise ValueError()


def _verified(pointer_pin, route, plan, pin, roster_bytes, tasks, policy_bytes):
    view, prefix = checkpoint.read_verified_prefix_pointer(Path(route["checkpoint_root"]), tasks,
        expected_pointer_sha256=pointer_pin, policy_bytes=policy_bytes,
        policy_sha256=route["policy_sha256"], roster_bytes=roster_bytes,
        roster_sha256=route["roster_sha256"], expected_public_policy_sha256=route["public_policy_sha256"])
    _prefix(prefix, route, plan)
    with private_state.locked_state(Path(route["private_state_root"]), Path(route["checkpoint_root"]), enabled=True) as state:
        state.require(pointer_pin, prefix)
    source = runner._plain_file(route["source"])
    with _open_log(source) as stream:
        genesis._same_source(source, stream, pin["identity"], pin["prefix_bytes"], pin["prefix_sha256"])
        genesis._same_source(source, stream, pin["identity"], prefix.cursor.offset, prefix.sha256)
    return view, prefix


def _checkpoint_cutoff(view, route):
    """Read the verified current generation's receipt, rechecking its pin."""
    name, artifacts = view["generation"], view["artifacts"]
    if type(name) is not str or checkpoint.NAME.fullmatch(name) is None or type(artifacts) is not dict:
        raise ValueError()
    expected = artifacts["receipt_sha256"]
    if type(expected) is not str or runner._HEX.fullmatch(expected) is None:
        raise ValueError()
    path = Path(route["checkpoint_root"]) / name / checkpoint.ARTIFACTS["receipt_sha256"]
    raw = checkpoint._read(path, max_bytes=checkpoint._MAX)
    if _sha(raw) != expected:
        raise ValueError()
    coverage = runner._json(raw, checkpoint._MAX)["source_coverage"]
    if type(coverage) is not dict or type(coverage.get("cutoff_utc")) is not str:
        raise ValueError()
    return coverage["cutoff_utc"]


def _utc(value):
    if type(value) is not str:
        raise ValueError()
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ValueError()
    return parsed.astimezone(timezone.utc)


def continue_once(*, enabled=False, tuple_bytes=None, expected_tuple_sha256=None,
                  expected_pointer_sha256=None, policy_bytes=None, public_policy_bytes=None,
                  fleet_bytes=None, expected_fleet_sha256=None, cutoff_utc=None,
                  previous_cutoff_utc=None):
    """Derive and execute at most one fresh fenced continuation, never retry it.

    A new invocation needs an explicit independently supplied current pointer
    pin. An interrupted/unknown predecessor must be reconciled before that call;
    this function never clears markers, substitutes state or reuses an old route.
    An explicit cutoff and caller-pinned previous cutoff must be supplied together.
    Their monotonicity depends on the cooperating caller; stale/replayed inputs
    and continuity after a crash are not prevented by this function.
    """
    if enabled is False:
        return {"status": "OFF"}
    prepared = False
    try:
        if enabled is not True:
            raise ValueError()
        route, plan, pin, roster_bytes, tasks = _inputs(tuple_bytes, expected_tuple_sha256,
            expected_pointer_sha256, policy_bytes, public_policy_bytes, fleet_bytes, expected_fleet_sha256)
        view, before = _verified(expected_pointer_sha256, route, plan, pin, roster_bytes, tasks, policy_bytes)
        explicit_cutoff = cutoff_utc is not None or previous_cutoff_utc is not None
        if explicit_cutoff:
            requested = runner._cutoff_datetime(cutoff_utc)
            previous = runner._cutoff_datetime(previous_cutoff_utc)
            genesis_floor = runner._cutoff_datetime(route["cutoff_utc"])
            checkpoint_floor = _utc(_checkpoint_cutoff(view, route))
            if previous < max(genesis_floor, checkpoint_floor) or requested < previous:
                raise ValueError()
        anchor_bytes, anchor_pin = issuer.issue_anchor(enabled=True, source_path=route["source"],
            generation_path=route["generation_path"], cursor=before.cursor, policy_bytes=policy_bytes,
            policy_sha256=route["policy_sha256"], roster_bytes=roster_bytes, roster_sha256=route["roster_sha256"],
            public_policy_bytes=public_policy_bytes, public_policy_sha256=route["public_policy_sha256"])
        plan = {**plan, "generation": "continuation-" + secrets.token_hex(16),
                "expected_pointer_sha256": expected_pointer_sha256,
                "pins": {**plan["pins"], "anchor_sha256": anchor_pin}}
        plan_bytes = _raw(plan)
        route = {**route, "cursor": asdict(before.cursor), "prefix_sha256": before.sha256,
                 "anchor_sha256": anchor_pin, "plan_sha256": _sha(plan_bytes)}
        route.pop("genesis_skip_sha256", None)
        if explicit_cutoff:
            route["cutoff_utc"] = cutoff_utc
        route_bytes = _raw(route)
        prepared = True
    except BaseException:
        pass
    if not prepared:
        raise ContinuationRefused("continuation_refused") from None

    failure = None
    try:
        report = runner.run_once(enabled=True, route_bytes=route_bytes, expected_route_sha256=_sha(route_bytes),
            plan_bytes=plan_bytes, policy_bytes=policy_bytes, public_policy_bytes=public_policy_bytes,
            fleet_bytes=fleet_bytes, expected_fleet_sha256=expected_fleet_sha256,
            roster_bytes=roster_bytes, anchor_bytes=anchor_bytes, renewable=True)
    except runner.StatusRunnerRefused:
        failure = "refused"
    except BaseException:
        failure = "unknown"
    if failure == "refused":
        raise ContinuationRefused("continuation_refused") from None
    if failure is not None:
        raise ContinuationUnknown("continuation_state_unknown") from None

    checked = False
    try:
        if (type(report) is not dict or type(report.get("runner_receipt_sha256")) is not str
                or runner._HEX.fullmatch(report["runner_receipt_sha256"]) is None
                or not runner._result_valid({key: value for key, value in report.items()
                                             if key != "runner_receipt_sha256"}, route, plan)):
            raise ValueError()
        view, after = _verified(report["pointer_sha256"], route, plan, pin, roster_bytes, tasks, policy_bytes)
        if report["status"] == "EMPTY_NO_CHANGE":
            if after != before:
                raise ValueError()
            outcome = "DEFERRED"
        else:
            if (after.cursor.offset <= before.cursor.offset or view.get("anchor_sha256") != anchor_pin):
                raise ValueError()
            outcome = "partial_ready_prefix"
            if report["coverage"] == "caught_up_to_final_snapshot_not_history":
                if after.cursor.offset < pin["prefix_bytes"]:
                    raise ValueError()
                outcome = "caught_up_to_cutoff"
        result = {"status": outcome, "runner_calls": 1, "pointer_sha256": report["pointer_sha256"],
                  "runner_receipt_sha256": report["runner_receipt_sha256"], "coverage": report["coverage"],
                  "source_authenticity": "UNKNOWN", "freshness": "UNKNOWN"}
        if explicit_cutoff:
            # Keep the exact route privately so its digest can be matched to
            # the runner receipt without disclosing path-bearing bytes.
            private = genesis.roots._root(route["private_state_root"])
            private_identity = genesis.roots._observe(private)
            route_pin = _sha(route_bytes)
            route_path = private / ("q4-continuation-route-" + route_pin + ".json")
            created = runner._create(route_path, route_bytes)
            runner._verify_own(route_path, route_bytes, created)
            if genesis.roots._observe(private) != private_identity:
                raise ValueError()
            result.update(cutoff_utc=cutoff_utc, previous_cutoff_utc=previous_cutoff_utc,
                          route_sha256=route_pin, cutoff_monotonicity="CALLER_ASSERTED_REPLAY_CRASH_UNPROVEN")
        checked = True
    except BaseException:
        pass
    if not checked:
        raise ContinuationUnknown("continuation_state_unknown") from None
    return result

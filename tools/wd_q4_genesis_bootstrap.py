"""Opt-in one-shot genesis preparation for a cooperating, bounded source.

This establishes byte consistency for one observed prefix, not authentic
origin, an atomic filesystem snapshot or continuity across bridge rotation.
The caller must independently authorize the paths, policy/fleet pins and
runtime and protect the source, sidecar and output roots. No retry or cleanup
is performed after sidecar creation: unknown outcomes require reconciliation.

F2: before admission the exact first-run catch-up (same reader, pins, budgets,
cutoff and batching from offset zero) is read through a probe sidecar and
anchor of its own and projected the way the producer does. A run whose
coverable windows hold no ready public row refuses genesis_no_public_row, and
one that cannot finish without a cutoff refuses genesis_catchup_incomplete,
before any one-shot state exists, so the run is not spent. This cannot reach a
public row beyond the run's own windows on its own; see the late-public opt-in.

Late public (opt-in resume_genesis_skip, partial genesis only): a pre-scan
whose windows hold no public row and that advanced the verified cursor writes
the private skip receipt q4-genesis-skip-v1.json in the private state root
(source file identity, generation, policy/roster/public-policy pins, the
verified end offset and sha256 of bytes [0, offset), the previous receipt sha
and a run count) by atomic replace, and refuses genesis_skip_progress with no
one-shot state. The next run requires the same identity, generation and pins,
re-hashes [0, offset), and scans the next bounded windows from there. When a
public row is found, genesis is admitted at the receipt offset and the route
binds the receipt sha; the runner admits a nonzero genesis cursor only with
that binding. The reader never skips a row: an eligible, malformed or
over-budget row refuses, and the verified end stops before a deferred row, so
[0, offset) holds only rows the public projection excludes. The receipt is
same-account private state, not a signature; prefix digests stay private.
"""
import base64
import hashlib
import hmac
import importlib
import inspect
import json
import os
import secrets
from dataclasses import asdict
from pathlib import Path

from tools import wd_mind_anchor_issuer as issuer
from tools import wd_q4_guard_state_binding as roots
from tools import wd_q4_roster_from_fleet as roster
from tools import wd_q4_status_prefix_runner as runner
from sandbox.mind.step2 import shared_capture_adapter as capture
from sandbox.mind.step2 import shared_index_checkpoint as checkpoint
from waggledance.core.bridge_log_reader import BridgeCursor, BridgePrefixPin, _file_identity, _open_log


class GenesisRefused(ValueError):
    """Invalid input before the one-shot sidecar creation."""


class GenesisUnknown(RuntimeError):
    """The sidecar or runner may exist; reconciliation is required."""


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def _digest(value):
    return hashlib.sha256(value).hexdigest()


def _snapshot(stream, size):
    stream.seek(0)
    digest, last, remaining = hashlib.sha256(), b"", size
    while remaining:
        chunk = stream.read(min(remaining, 1024 * 1024))
        if not chunk:
            raise ValueError()
        digest.update(chunk)
        remaining -= len(chunk)
        last = chunk[-1:]
    if size and last != b"\n":
        raise ValueError()
    return digest.hexdigest()


def _same_source(path, stream, identity, size, digest):
    if _file_identity(stream) != identity or os.fstat(stream.fileno()).st_size < size:
        raise ValueError()
    if not hmac.compare_digest(_snapshot(stream, size), digest):
        raise ValueError()
    runner._plain_file(str(path))
    with _open_log(path) as current:
        if _file_identity(current) != identity or os.fstat(current.fileno()).st_size < size:
            raise ValueError()
        if not hmac.compare_digest(_snapshot(current, size), digest):
            raise ValueError()


def _scan_budget(stream, size, *, max_bytes, max_rows, max_windows, allow_partial=False):
    """Bound complete records in greedy byte/row windows; never retain a record."""
    if size <= 0:
        raise ValueError()
    stream.seek(0)
    remaining, line_bytes, window_bytes, window_rows, windows = size, 0, 0, 0, 1
    while remaining:
        chunk = stream.read(min(remaining, 1024 * 1024))
        if not chunk:
            raise ValueError()
        remaining -= len(chunk)
        parts = chunk.split(b"\n")
        for part in parts[:-1]:
            line_bytes += len(part) + 1
            if line_bytes > max_bytes:
                raise ValueError()
            if window_rows == max_rows or window_bytes + line_bytes > max_bytes:
                windows += 1
                window_bytes = window_rows = 0
            if windows > max_windows:
                if allow_partial:
                    return False
                raise ValueError()
            window_bytes += line_bytes
            window_rows += 1
            line_bytes = 0
        line_bytes += len(parts[-1])
        if line_bytes > max_bytes:
            raise ValueError()
    if line_bytes:
        raise ValueError()
    return True


def _complete_prefix_size(stream, length):
    stream.seek(0)
    seen = last = 0
    while seen < length:
        chunk = stream.read(min(length - seen, 1024 * 1024))
        if not chunk:
            raise ValueError()
        idx = chunk.rfind(b"\n")
        if idx >= 0:
            last = seen + idx + 1
        seen += len(chunk)
    return last


def _prescan_public_rows(source, identity, generation, private_root, *, policy_bytes, policy_sha256,
                         roster_bytes, roster_sha256, public_policy_bytes, public_policy_sha256, max_bytes,
                         max_rows, max_windows, max_prefix_bytes, cutoff_utc, batch_rows, start=None, skip=False):
    """F2: None when the first run's catch-up holds at least one ready public record, else GenesisRefused.

    Reads exactly what the run will read and verifies it with the producer's own builder check, through a probe
    sidecar under a fresh random directory in the private root and an anchor bound to that probe; neither is
    the one-shot state, and both are removed before this returns. A reader or builder refusal propagates; a
    cleanup failure never replaces it. With ``skip`` (late public), a chain with no public record that advanced
    the verified cursor returns its verified end pin instead of refusing.
    """
    probe = private_root / ("q4-genesis-prescan-" + secrets.token_hex(8))
    made = False
    try:
        probe.mkdir()
        made = True
        result = _prescan_chain(probe / "mind-generation.json", source, identity, generation,
            policy_bytes=policy_bytes, policy_sha256=policy_sha256, roster_bytes=roster_bytes,
            roster_sha256=roster_sha256, public_policy_bytes=public_policy_bytes,
            public_policy_sha256=public_policy_sha256, max_bytes=max_bytes, max_rows=max_rows,
            max_windows=max_windows, max_prefix_bytes=max_prefix_bytes, cutoff_utc=cutoff_utc,
            batch_rows=batch_rows, start=start, skip=skip)
    except BaseException:
        if made:
            try:
                _remove_probe(probe)
            except Exception:
                pass  # The actual refusal wins; a random leftover probe directory blocks no later run.
        raise
    _remove_probe(probe)
    return result


def _remove_probe(probe):
    (probe / "mind-generation.json").unlink(missing_ok=True)
    probe.rmdir()


def _prescan_chain(side, source, identity, generation, *, policy_bytes, policy_sha256, roster_bytes, roster_sha256,
                   public_policy_bytes, public_policy_sha256, max_bytes, max_rows, max_windows, max_prefix_bytes,
                   cutoff_utc, batch_rows, start, skip):
    from sandbox.mind.step2 import shared_bridge_snapshot as snapshot
    from sandbox.mind.step2 import shared_index_builder as builder
    runner._create(side, _canonical({"generation": generation}))
    if start is None:
        start = BridgePrefixPin(BridgeCursor(offset=0, file_identity=identity, generation=generation), _digest(b""))
    cursor = start.cursor
    if (type(cursor) is not BridgeCursor or cursor.file_identity != identity or cursor.generation != generation
            or type(skip) is not bool or (cursor.offset != 0 and not skip)):
        raise ValueError()
    anchor, anchor_sha = issuer.issue_anchor(enabled=True, source_path=str(source), generation_path=str(side),
        cursor=cursor, policy_bytes=policy_bytes, policy_sha256=policy_sha256, roster_bytes=roster_bytes,
        roster_sha256=roster_sha256, public_policy_bytes=public_policy_bytes,
        public_policy_sha256=public_policy_sha256)
    tasks, _ = capture.strict_pins(policy_bytes, policy_sha256, roster_bytes, roster_sha256)
    cutoff = None if cutoff_utc is None else runner._cutoff_datetime(cutoff_utc)
    options = {} if cutoff is None else {"cutoff_utc": cutoff}
    if batch_rows:
        options["batch_rows"] = True
    chain = snapshot.read_public_catchup(str(source), cursor, tasks, anchor_bytes=anchor, anchor_sha256=anchor_sha,
        generation_path=str(side), max_bytes=max_bytes, max_rows=max_rows, max_windows=max_windows,
        policy_bytes=policy_bytes, policy_sha256=policy_sha256, roster_bytes=roster_bytes,
        roster_sha256=roster_sha256, public_policy_bytes=public_policy_bytes,
        public_policy_sha256=public_policy_sha256, expected_prefix_sha256=start.sha256,
        max_prefix_bytes=max_prefix_bytes, enabled=True, **options)
    # The producer's own decision (publish_public_catchup): partial only with a cutoff, else caught up.
    partial = cutoff is not None and chain["end_reason"] in ("deferred", "max_windows", "partial_record")
    if chain["caught_up"] is not True and not partial:
        raise GenesisRefused("genesis_catchup_incomplete")
    if not any(window["projection"]["records"] for window in chain["windows"]):
        if skip:
            options = {} if cutoff is None else {"expected_cutoff_utc": cutoff}
            if batch_rows:
                options["expected_batch_rows"] = True
            if snapshot.verify_public_catchup(chain, expected_anchor_bytes=anchor,
                    expected_anchor_sha256=anchor_sha, expected_policy_sha256=policy_sha256,
                    expected_roster_sha256=roster_sha256, expected_public_policy_sha256=public_policy_sha256,
                    expected_start_prefix=start, **options) is not True:
                raise ValueError()
            end = chain["candidate_prefix"]
            if (type(end) is not BridgePrefixPin or type(end.cursor) is not BridgeCursor
                    or (end.cursor.file_identity, end.cursor.generation) != (identity, generation)
                    or type(end.cursor.offset) is not int or not 0 <= end.cursor.offset <= max_prefix_bytes
                    or type(end.sha256) is not str or runner._HEX.fullmatch(end.sha256) is None):
                raise ValueError()
            if end.cursor.offset > cursor.offset:
                return end
        raise GenesisRefused("genesis_no_public_row")
    catchup_source = {"catchup_result": chain, "expected_anchor_sha256": anchor_sha,
        "mode": ("public-catchup-partial-v1" if partial else
                 "public-catchup-ready-v1" if cutoff is not None else "public-catchup-v1"),
        "public_policy_bytes": public_policy_bytes, "public_policy_sha256": public_policy_sha256,
        "anchor_bytes": anchor, "expected_start_prefix": start}
    if cutoff is not None:
        catchup_source["expected_cutoff_utc"] = cutoff
    if batch_rows:
        catchup_source["expected_batch_rows"] = True
    pins = dict(policy_bytes=policy_bytes, policy_sha256=policy_sha256, roster_bytes=roster_bytes,
                roster_sha256=roster_sha256)
    if partial:
        projection, _ = builder.verified_partial_catchup_projection(catchup_source, enabled=True, **pins)
    else:
        projection, _ = builder.verified_catchup_projection(catchup_source, **pins)
    if type(projection) is not dict or type(projection.get("records")) is not list or not projection["records"]:
        raise GenesisRefused("genesis_no_public_row")
    return None


def _existing_skip(private_root):
    """The current skip receipt bytes, or None when there is none."""
    if runner._absent(private_root / runner.GENESIS_SKIP_NAME):
        return None
    return runner._read_genesis_skip(private_root)


def _skip_start(raw, *, identity, generation, policy_sha256, roster_sha256, public_policy_sha256, size):
    """The verified start pin a skip receipt records for this exact source, generation and pins."""
    try:
        value = runner._genesis_skip(raw)
    except ValueError:
        raise GenesisRefused("genesis_skip_receipt_invalid") from None
    if (value["file_identity"] != identity or value["generation"] != generation
            or value["policy_sha256"] != policy_sha256 or value["roster_sha256"] != roster_sha256
            or value["public_policy_sha256"] != public_policy_sha256 or value["offset"] > size):
        raise GenesisRefused("genesis_skip_receipt_invalid")
    return value, BridgePrefixPin(BridgeCursor(offset=value["offset"], file_identity=identity, generation=generation),
                                  value["prefix_sha256"])


def _advance_skip(private_root, previous_raw, previous_value, end, *, policy_sha256, roster_sha256,
                  public_policy_sha256):
    """Atomically replace the skip receipt with one ending at the verified end pin; never over another writer's."""
    raw = _canonical({"schema": "wd.q4-genesis-skip.v1", "file_identity": end.cursor.file_identity,
        "generation": end.cursor.generation, "policy_sha256": policy_sha256, "roster_sha256": roster_sha256,
        "public_policy_sha256": public_policy_sha256, "offset": end.cursor.offset, "prefix_sha256": end.sha256,
        "previous_sha256": None if previous_raw is None else _digest(previous_raw),
        "runs": 1 if previous_value is None else previous_value["runs"] + 1})
    runner._genesis_skip(raw)
    final = private_root / runner.GENESIS_SKIP_NAME
    temp = private_root / (runner.GENESIS_SKIP_NAME + "." + secrets.token_hex(8) + ".tmp")
    try:
        info = runner._create(temp, raw)
        runner._verify_own(temp, raw, info)
        if _existing_skip(private_root) != previous_raw:
            raise ValueError()
        os.replace(temp, final)
        if _existing_skip(private_root) != raw:
            raise ValueError()
    finally:
        if not runner._absent(temp):
            temp.unlink()


def bootstrap(*, enabled=False, source_path=None, generation_path=None, generation=None,
              checkpoint_root=None, private_state_root=None, trusted_model_root=None,
              policy_bytes=None, policy_sha256=None, public_policy_bytes=None, public_policy_sha256=None,
              fleet_bytes=None, expected_fleet_sha256=None, max_prefix_bytes=None,
              max_bytes=None, max_rows=None, plan_template=None, renewable=False,
              cutoff_utc=None, batch_rows=False, min_headroom_bytes=65536, allow_partial_genesis=False,
              expected_source_identity=None, expected_source_prefix_bytes=None, expected_source_prefix_sha256=None,
              resume_genesis_skip=False):
    if enabled is False:
        return {"status": "OFF"}
    created = False
    try:
        if (enabled is not True or type(renewable) is not bool or type(batch_rows) is not bool
                or type(allow_partial_genesis) is not bool):
            raise ValueError()
        if cutoff_utc is not None:
            runner._cutoff_datetime(cutoff_utc)
        if batch_rows and cutoff_utc is None:
            raise ValueError()
        if allow_partial_genesis and (not batch_rows or cutoff_utc is None):
            raise ValueError()
        if type(resume_genesis_skip) is not bool or (resume_genesis_skip and not allow_partial_genesis):
            raise ValueError()
        independent_pin = (expected_source_identity, expected_source_prefix_bytes, expected_source_prefix_sha256)
        has_pin = independent_pin != (None, None, None)
        if has_pin and (type(expected_source_identity) is not str
                or runner._IDENTITY.fullmatch(expected_source_identity) is None
                or type(expected_source_prefix_bytes) is not int or expected_source_prefix_bytes <= 0
                or type(expected_source_prefix_sha256) is not str
                or runner._HEX.fullmatch(expected_source_prefix_sha256) is None):
            raise ValueError()
        runner._pinned(policy_bytes, policy_sha256, runner._POLICY_SHA)
        runner._pinned(public_policy_bytes, public_policy_sha256, runner._PUBLIC_SHA)
        runner._pinned(fleet_bytes, expected_fleet_sha256, runner._FLEET_SHA, limit=1024 * 1024)
        roster_bytes, roster_sha = roster.build_roster_snapshot(fleet_bytes, expected_fleet_sha256, enabled=True)
        if (type(generation) is not str or runner._GENERATION.fullmatch(generation) is None
                or type(max_prefix_bytes) is not int or not 0 < max_prefix_bytes <= 1024**3
                or type(min_headroom_bytes) is not int or min_headroom_bytes < 1
                or type(max_bytes) is not int or not 1 <= max_bytes <= 4 * 1024 * 1024
                or type(max_rows) is not int or not 1 <= max_rows <= 10000
                or type(plan_template) is not dict or set(plan_template) !=
                {"schema", "generation", "expected_pointer_sha256", "max_prefix_bytes", "max_windows", "timeout", "encoder", "fenced"}
                or plan_template["schema"] != "wd.q4-prefix-build-plan.v3"
                or plan_template["generation"] != generation or plan_template["expected_pointer_sha256"] is not None
                or plan_template["max_prefix_bytes"] != max_prefix_bytes or plan_template["fenced"] is not True
                or type(plan_template["max_windows"]) is not int or not 1 <= plan_template["max_windows"] <= 64
                or plan_template["max_windows"] * (2 * max_prefix_bytes + max_bytes) > 4 * 1024**3):
            raise ValueError()
        source = runner._plain_file(source_path)
        sidecar = roots._root(generation_path)
        source_ids, side_ids = roots._observe(source.parent), roots._observe(sidecar.parent)
        if not runner._separate(source.parent, sidecar.parent):
            raise ValueError()
        output_roots = [roots._root(x) for x in (checkpoint_root, private_state_root, trusted_model_root)]
        for root in output_roots:
            roots._observe(root)
            if not runner._separate(source.parent, root) or not runner._separate(sidecar.parent, root):
                raise ValueError()
        if any(not runner._separate(a, b) for i, a in enumerate(output_roots) for b in output_roots[i + 1:]):
            raise ValueError()
        tuple_file = output_roots[1] / "q4-genesis-tuple-v1.json"
        if (not runner._absent(sidecar) or not runner._absent(tuple_file)
                or not runner._absent(output_roots[0] / runner._PENDING)
                or not runner._absent(output_roots[1] / runner._PENDING)
                or not runner._absent(output_roots[1] / "prefix-private-state.json")
                or not runner._absent(output_roots[1] / "prefix-private-state.pending")
                or not runner._absent(output_roots[0] / "q4-latest.json")):
            raise GenesisUnknown("generation_state_unknown")
        with _open_log(source) as stream:
            identity = _file_identity(stream)
            observed_size = os.fstat(stream.fileno()).st_size
            if observed_size + min_headroom_bytes > max_prefix_bytes:
                raise ValueError()
            size = _complete_prefix_size(stream, observed_size)
            if cutoff_utc is None and size != observed_size:
                raise ValueError()
            if has_pin:
                if identity != expected_source_identity or expected_source_prefix_bytes > size:
                    raise ValueError()
                _same_source(source, stream, identity, expected_source_prefix_bytes, expected_source_prefix_sha256)
            _scan_budget(stream, size, max_bytes=max_bytes, max_rows=max_rows if batch_rows or cutoff_utc is None else 1,
                         max_windows=plan_template["max_windows"], allow_partial=allow_partial_genesis)
            digest = _snapshot(stream, size)
            _same_source(source, stream, identity, size, digest)
            skip_raw = skip_value = None
            start = BridgePrefixPin(BridgeCursor(offset=0, file_identity=identity, generation=generation), _digest(b""))
            if resume_genesis_skip:
                skip_raw = _existing_skip(output_roots[1])
                if skip_raw is not None:
                    skip_value, start = _skip_start(skip_raw, identity=identity, generation=generation,
                        policy_sha256=policy_sha256, roster_sha256=roster_sha,
                        public_policy_sha256=public_policy_sha256, size=size)
                    _same_source(source, stream, identity, start.cursor.offset, start.sha256)
            cursor = start.cursor
            anchor, anchor_sha = issuer.issue_anchor(enabled=True, source_path=str(source), generation_path=str(sidecar),
                cursor=cursor, policy_bytes=policy_bytes, policy_sha256=policy_sha256, roster_bytes=roster_bytes,
                roster_sha256=roster_sha, public_policy_bytes=public_policy_bytes, public_policy_sha256=public_policy_sha256)
            pins = {"anchor_sha256": anchor_sha, "policy_sha256": policy_sha256, "roster_sha256": roster_sha,
                    "public_policy_sha256": public_policy_sha256}
            plan = _canonical({**plan_template, "pins": pins})
            # Import and validate the exact child's plan before consuming the one-shot sidecar.
            child = importlib.import_module("sandbox.mind.step2.wd_q4_prefix_build_child")
            if not child._plan_shape(json.loads(plan)):
                raise ValueError()
            if batch_rows:
                parameter = inspect.signature(child.build_prefix_generation).parameters.get("batch_rows")
                if parameter is None or parameter.kind not in (inspect.Parameter.POSITIONAL_OR_KEYWORD,
                                                               inspect.Parameter.KEYWORD_ONLY):
                    raise ValueError()
            # F2: never spend the one-shot on a first run whose coverable windows hold no ready public row.
            end = _prescan_public_rows(source, identity, generation, output_roots[1], policy_bytes=policy_bytes,
                policy_sha256=policy_sha256, roster_bytes=roster_bytes, roster_sha256=roster_sha,
                public_policy_bytes=public_policy_bytes, public_policy_sha256=public_policy_sha256,
                max_bytes=max_bytes, max_rows=max_rows, max_windows=plan_template["max_windows"],
                max_prefix_bytes=max_prefix_bytes, cutoff_utc=cutoff_utc, batch_rows=batch_rows,
                start=start, skip=resume_genesis_skip)
            if end is not None:
                # Late public: no public row in these windows; record the verified progress, spend nothing.
                _advance_skip(output_roots[1], skip_raw, skip_value, end, policy_sha256=policy_sha256,
                              roster_sha256=roster_sha, public_policy_sha256=public_policy_sha256)
                raise GenesisRefused("genesis_skip_progress")
            route_options = {} if cutoff_utc is None else {"cutoff_utc": cutoff_utc}
            if batch_rows:
                route_options["batch_rows"] = True
            if skip_raw is not None:
                route_options["genesis_skip_sha256"] = _digest(skip_raw)
            route = _canonical({"schema": "wd.q4-status-prefix-route.v1", "source": str(source),
                "generation_path": str(sidecar), "checkpoint_root": str(output_roots[0]),
                "private_state_root": str(output_roots[1]), "trusted_model_root": str(output_roots[2]),
                "cursor": asdict(cursor), "prefix_sha256": start.sha256, "max_bytes": max_bytes,
                "max_rows": max_rows, "plan_sha256": _digest(plan), **pins, **route_options})
            side_bytes = _canonical({"generation": generation})
            # This private record is never a publication receipt; its state remains
            # UNKNOWN even if the separate runner later returns successfully.
            tuple_bytes = _canonical({"schema": "wd.q4-genesis-private-tuple.v1", "publication": "UNKNOWN",
                "allow_partial_genesis": allow_partial_genesis,
                "independent_source_pin": ({"identity": expected_source_identity,
                    "prefix_bytes": expected_source_prefix_bytes, "prefix_sha256": expected_source_prefix_sha256}
                    if has_pin else None),
                **{name: {"bytes_b64": base64.b64encode(value).decode("ascii"), "sha256": _digest(value)}
                   for name, value in (("anchor", anchor), ("route", route), ("plan", plan))}})
            if has_pin:
                _same_source(source, stream, identity, expected_source_prefix_bytes, expected_source_prefix_sha256)
            if skip_raw is not None and _existing_skip(output_roots[1]) != skip_raw:
                raise ValueError()
            # O_EXCL is the admission point: any failure from here requires reconciliation.
            created = True
            info = runner._create(sidecar, side_bytes)
            runner._verify_own(sidecar, side_bytes, info)
            tuple_info = runner._create(tuple_file, tuple_bytes)
            runner._verify_own(tuple_file, tuple_bytes, tuple_info)
            _same_source(source, stream, identity, size, digest)
            if roots._observe(source.parent) != source_ids or roots._observe(sidecar.parent) != side_ids:
                raise ValueError()
            report = runner.run_once(enabled=True, route_bytes=route, expected_route_sha256=_digest(route),
                plan_bytes=plan, policy_bytes=policy_bytes, public_policy_bytes=public_policy_bytes,
                fleet_bytes=fleet_bytes, expected_fleet_sha256=expected_fleet_sha256,
                roster_bytes=roster_bytes, anchor_bytes=anchor, renewable=renewable)
            tasks, _ = capture.strict_pins(policy_bytes, policy_sha256, roster_bytes, roster_sha)
            view, end_pin = checkpoint.read_verified_prefix_pointer(output_roots[0], tasks,
                expected_pointer_sha256=report["pointer_sha256"], policy_bytes=policy_bytes,
                policy_sha256=policy_sha256, roster_bytes=roster_bytes, roster_sha256=roster_sha,
                expected_public_policy_sha256=public_policy_sha256)
            if (view.get("anchor_sha256") != anchor_sha or type(end_pin) is not BridgePrefixPin
                    or type(end_pin.cursor) is not BridgeCursor or end_pin.cursor.file_identity != identity
                    or end_pin.cursor.generation != generation or type(end_pin.cursor.offset) is not int
                    or not start.cursor.offset < end_pin.cursor.offset <= max_prefix_bytes
                    or type(end_pin.sha256) is not str or runner._HEX.fullmatch(end_pin.sha256) is None):
                raise ValueError()
            fully_covered = end_pin.cursor.offset >= size
            if not fully_covered and (not allow_partial_genesis or report.get("coverage") != "partial_ready_prefix"):
                raise ValueError()
            runner._verify_own(sidecar, side_bytes, info)
            runner._verify_own(tuple_file, tuple_bytes, tuple_info)
            _same_source(source, stream, identity, size, digest)
            if has_pin:
                _same_source(source, stream, identity, expected_source_prefix_bytes, expected_source_prefix_sha256)
            _same_source(source, stream, identity, end_pin.cursor.offset, end_pin.sha256)
            if skip_raw is not None and _existing_skip(output_roots[1]) != skip_raw:
                raise ValueError()
            runner._verify_own(sidecar, side_bytes, info)
            if roots._observe(source.parent) != source_ids or roots._observe(sidecar.parent) != side_ids:
                raise ValueError()
            return {**report, "genesis_snapshot_bytes": size,
                    "genesis_initial_prefix_fully_covered": fully_covered,
                    "genesis_tuple_sha256": _digest(tuple_bytes)}
    except GenesisUnknown:
        raise
    except GenesisRefused as refused:
        if created:
            raise GenesisUnknown("genesis_state_unknown") from None
        raise GenesisRefused(str(refused)) from None
    except (KeyboardInterrupt, SystemExit):
        if created:
            raise GenesisUnknown("genesis_state_unknown") from None
        raise
    except BaseException:
        if created:
            raise GenesisUnknown("genesis_state_unknown") from None
        raise GenesisRefused("genesis_refused") from None

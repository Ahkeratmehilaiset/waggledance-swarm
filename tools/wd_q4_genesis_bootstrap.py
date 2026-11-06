"""Opt-in one-shot genesis preparation for a cooperating, bounded source.

This establishes byte consistency for one observed prefix, not authentic
origin, an atomic filesystem snapshot or continuity across bridge rotation.
The caller must independently authorize the paths, policy/fleet pins and
runtime and protect the source, sidecar and output roots. No retry or cleanup
is performed after sidecar creation: unknown outcomes require reconciliation.
"""
import hashlib
import hmac
import json
import os
from dataclasses import asdict
from pathlib import Path

from tools import wd_mind_anchor_issuer as issuer
from tools import wd_q4_guard_state_binding as roots
from tools import wd_q4_roster_from_fleet as roster
from tools import wd_q4_status_prefix_runner as runner
from waggledance.core.bridge_log_reader import BridgeCursor, _file_identity, _open_log


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
    if _file_identity(stream) != identity or os.fstat(stream.fileno()).st_size != size:
        raise ValueError()
    if not hmac.compare_digest(_snapshot(stream, size), digest):
        raise ValueError()
    runner._plain_file(str(path))
    with _open_log(path) as current:
        if _file_identity(current) != identity or os.fstat(current.fileno()).st_size != size:
            raise ValueError()
        if not hmac.compare_digest(_snapshot(current, size), digest):
            raise ValueError()


def bootstrap(*, enabled=False, source_path=None, generation_path=None, generation=None,
              checkpoint_root=None, private_state_root=None, trusted_model_root=None,
              policy_bytes=None, policy_sha256=None, public_policy_bytes=None, public_policy_sha256=None,
              fleet_bytes=None, expected_fleet_sha256=None, max_prefix_bytes=None,
              max_bytes=None, max_rows=None, plan_template=None):
    if enabled is False:
        return {"status": "OFF"}
    created = False
    try:
        if enabled is not True:
            raise ValueError()
        runner._pinned(policy_bytes, policy_sha256, runner._POLICY_SHA)
        runner._pinned(public_policy_bytes, public_policy_sha256, runner._PUBLIC_SHA)
        runner._pinned(fleet_bytes, expected_fleet_sha256, runner._FLEET_SHA, limit=1024 * 1024)
        roster_bytes, roster_sha = roster.build_roster_snapshot(fleet_bytes, expected_fleet_sha256, enabled=True)
        if (type(generation) is not str or runner._GENERATION.fullmatch(generation) is None
                or type(max_prefix_bytes) is not int or not 0 < max_prefix_bytes <= 1024**3
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
        if not runner._absent(sidecar):
            raise GenesisUnknown("generation_state_unknown")
        with _open_log(source) as stream:
            identity = _file_identity(stream)
            size = os.fstat(stream.fileno()).st_size
            if size > max_prefix_bytes:
                raise ValueError()
            digest = _snapshot(stream, size)
            _same_source(source, stream, identity, size, digest)
            cursor = BridgeCursor(offset=0, file_identity=identity, generation=generation)
            anchor, anchor_sha = issuer.issue_anchor(enabled=True, source_path=str(source), generation_path=str(sidecar),
                cursor=cursor, policy_bytes=policy_bytes, policy_sha256=policy_sha256, roster_bytes=roster_bytes,
                roster_sha256=roster_sha, public_policy_bytes=public_policy_bytes, public_policy_sha256=public_policy_sha256)
            pins = {"anchor_sha256": anchor_sha, "policy_sha256": policy_sha256, "roster_sha256": roster_sha,
                    "public_policy_sha256": public_policy_sha256}
            plan = _canonical({**plan_template, "pins": pins})
            route = _canonical({"schema": "wd.q4-status-prefix-route.v1", "source": str(source),
                "generation_path": str(sidecar), "checkpoint_root": str(output_roots[0]),
                "private_state_root": str(output_roots[1]), "trusted_model_root": str(output_roots[2]),
                "cursor": asdict(cursor), "prefix_sha256": _digest(b""), "max_bytes": max_bytes,
                "max_rows": max_rows, "plan_sha256": _digest(plan), **pins})
            side_bytes = _canonical({"generation": generation})
            # O_EXCL is the admission point: any failure from here requires reconciliation.
            created = True
            info = runner._create(sidecar, side_bytes)
            runner._verify_own(sidecar, side_bytes, info)
            _same_source(source, stream, identity, size, digest)
            if roots._observe(source.parent) != source_ids or roots._observe(sidecar.parent) != side_ids:
                raise ValueError()
            report = runner.run_once(enabled=True, route_bytes=route, expected_route_sha256=_digest(route),
                plan_bytes=plan, policy_bytes=policy_bytes, public_policy_bytes=public_policy_bytes,
                fleet_bytes=fleet_bytes, expected_fleet_sha256=expected_fleet_sha256,
                roster_bytes=roster_bytes, anchor_bytes=anchor)
            runner._verify_own(sidecar, side_bytes, info)
            _same_source(source, stream, identity, size, digest)
            if roots._observe(source.parent) != source_ids or roots._observe(sidecar.parent) != side_ids:
                raise ValueError()
            return {**report, "genesis_snapshot_bytes": size, "genesis_snapshot_sha256": digest}
    except GenesisUnknown:
        raise
    except BaseException:
        if created:
            raise GenesisUnknown("genesis_state_unknown") from None
        raise GenesisRefused("genesis_refused") from None

# SPDX-License-Identifier: BUSL-1.1
"""Caller-pinned, bounded, read-only situation observations; never an authority.

The caller supplies exact snapshot, fleet, current-pointer and manifest bytes
and independently selected SHA256 pins. A pin proves byte equality only. In
particular, neither actor labels nor pointer ownership are authenticated here.
The bridge snapshot is a bounded list of ``{ref,event}`` envelopes: absence
of a later release, reply or veto is never evidence that a task is active, a
request open or an approval pending. Event messages are never returned.
Most identifiers are represented by deterministic SHA256; low-entropy values
can still be guessed, so the digest is a correlation aid, not anonymization.
"""
from __future__ import annotations

import hashlib
import json
import math
import ntpath
import re
from datetime import datetime, timezone

from tools.bridge_compact_view import event_id

LANES = ("codex-lead-1", "codex-tools-1", "claude-rco-1", "claude-rco-2", "fable-5")
UNKNOWN = "UNKNOWN"
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_COMMIT = re.compile(r"[0-9a-f]{40}\Z")
_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}\Z")
_UUID = re.compile(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\Z")
_TIMESTAMP = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,7})?Z\Z")
_LIMIT = 8 * 1024 * 1024


class SituationRefused(ValueError):
    """Fixed error only; source contents and input paths are not echoed."""


def _unique(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise ValueError("duplicate")
        out[key] = value
    return out


def _json(raw, pin, limit):
    if (type(raw) is not bytes or len(raw) > limit or type(pin) is not str
            or not _HEX.fullmatch(pin) or hashlib.sha256(raw).hexdigest() != pin):
        raise ValueError("pin")
    return json.loads(raw.decode("utf-8-sig"), object_pairs_hook=_unique,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError("constant")))


def _time(text):
    if type(text) is not str or not _TIMESTAMP.fullmatch(text):
        raise ValueError("time")
    parsed = datetime.fromisoformat(text[:-1] + "+00:00")
    # Preserve a possible seventh Windows bridge timestamp digit.
    fraction = text[:-1].partition(".")[2]
    tick = int(fraction[6]) if len(fraction) == 7 else 0
    return parsed, tick


def _age_ticks(now, when):
    now_time, now_tick = _time(now)
    event_time, event_tick = _time(when)
    delta = now_time - event_time
    ticks = (delta.days * 86400 + delta.seconds) * 10000000 + delta.microseconds * 10 + now_tick - event_tick
    if ticks < 0:
        raise ValueError("future")
    return ticks


def _roster(fleet):
    if type(fleet) is not dict or type(fleet.get("lanes")) is not list or type(fleet.get("tools_supervisor")) is not dict:
        raise ValueError("roster")
    out = {}
    for item in [*fleet["lanes"], fleet["tools_supervisor"]]:
        if type(item) is not dict:
            raise ValueError("roster")
        agent, uuid = item.get("agent"), item.get("agent_uuid")
        if agent in LANES:
            if agent in out or type(uuid) is not str or not _UUID.fullmatch(uuid):
                raise ValueError("roster")
            out[agent] = uuid.lower()
    if set(out) != set(LANES) or len(set(out.values())) != len(LANES):
        raise ValueError("roster")
    return out


def _event_observation(ref, event, now, ttl):
    ts = event.get("ts_utc")
    ticks = _age_ticks(now, ts)
    age = math.ceil(ticks / 10000000)
    fresh = ticks < ttl * 10000000
    task = event.get("task_id")
    if type(task) is str and _TOKEN.fullmatch(task):
        task = "sha256:" + hashlib.sha256(task.encode("utf-8")).hexdigest()
    else:
        task = UNKNOWN
    return {"status": "OBSERVED" if fresh else UNKNOWN, "event_ref": ref,
            "observed_utc": ts, "age_seconds_upper_bound": age,
            "event_type": event.get("type") if fresh else UNKNOWN,
            "task_id": task if fresh else UNKNOWN,
            "sender_authentication": UNKNOWN}


def _bundle(pointer, manifest_raw, manifest_pin, observed_utc, as_of_utc, ttl):
    if type(pointer) is not dict or type(pointer.get("schema_version")) is not int or pointer["schema_version"] != 1:
        raise ValueError("pointer")
    commit = pointer.get("source_commit")
    if type(commit) is not str or not _COMMIT.fullmatch(commit) or pointer.get("final_commit") != commit:
        raise ValueError("commit")
    if type(manifest_raw) is not bytes or len(manifest_raw) > _LIMIT or not _HEX.fullmatch(manifest_pin):
        raise ValueError("manifest")
    if hashlib.sha256(manifest_raw).hexdigest() != manifest_pin:
        raise ValueError("manifest")
    if (str(pointer.get("manifest_sha256", "")).lower() != manifest_pin
            or str(pointer.get("final_manifest_sha256", "")).lower() != manifest_pin):
        raise ValueError("pointer_manifest")
    active = pointer.get("active_bundle")
    if (type(active) is not str or ntpath.normpath(active) != active or ntpath.normcase(active) !=
            ntpath.normcase(ntpath.join(r"C:\Python\wd-reboot-bundles", commit))):
        raise ValueError("bundle_path")
    age = _age_ticks(as_of_utc, observed_utc)
    return {"status": "OBSERVED" if age < ttl * 10000000 else UNKNOWN,
            "source_kind": "local_pointer_and_manifest_bytes", "observed_utc": observed_utc,
            "age_seconds_upper_bound": math.ceil(age / 10000000),
            "source_commit": commit if age < ttl * 10000000 else UNKNOWN,
            "manifest_sha256": manifest_pin if age < ttl * 10000000 else UNKNOWN,
            "pointer_authentication": UNKNOWN}


def project_situation(*, snapshot_bytes, snapshot_sha256, fleet_bytes, fleet_sha256,
                      pointer_bytes, pointer_sha256, manifest_bytes, manifest_sha256,
                      pointer_observed_utc, as_of_utc, ttl_seconds=900, allow_missing_bundle=False):
    """Project observations, revalidating pins and each canonical event reference.

    The caller selects hashes and reads sources through its approved pinned
    readers. This function does no I/O and never asserts completeness, current
    claims, an open request or an approval from absence in a bounded tail.
    The explicit missing-bundle mode is for historical observations whose
    pointer and manifest are unavailable; it exposes no bundle assertion and
    labels the fleet-to-manifest binding UNKNOWN.
    """
    try:
        if type(ttl_seconds) is not int or not 1 <= ttl_seconds <= 900 or type(allow_missing_bundle) is not bool:
            raise ValueError("ttl")
        _time(as_of_utc)
        roster = _roster(_json(fleet_bytes, fleet_sha256, 1024 * 1024))
        supplied = (pointer_bytes, pointer_sha256, manifest_bytes, manifest_sha256, pointer_observed_utc)
        if all(item is None for item in supplied):
            if not allow_missing_bundle:
                raise ValueError("bundle_absent")
            bundle, fleet_manifest_binding = UNKNOWN, UNKNOWN
        else:
            if any(item is None for item in supplied):
                raise ValueError("bundle_incomplete")
            pointer = _json(pointer_bytes, pointer_sha256, 1024 * 1024)
            # Ensure manifest is valid JSON, with no duplicate keys, as well as pinned.
            manifest = _json(manifest_bytes, manifest_sha256, _LIMIT)
            if (type(manifest) is not dict or type(manifest.get("schema_version")) is not int
                    or manifest["schema_version"] != 1 or type(manifest.get("files")) is not dict):
                raise ValueError("manifest_schema")
            source_commit = manifest.get("source_commit")
            if (type(source_commit) is not str or not _COMMIT.fullmatch(source_commit)
                    or source_commit != pointer.get("source_commit")):
                raise ValueError("manifest_commit")
            declared_fleet = manifest["files"].get("wd-fleet.json")
            if (type(declared_fleet) is not str or not _HEX.fullmatch(declared_fleet.lower())
                    or declared_fleet.lower() != fleet_sha256):
                raise ValueError("fleet_manifest_binding")
            bundle = _bundle(pointer, manifest_bytes, manifest_sha256, pointer_observed_utc, as_of_utc, ttl_seconds)
            bundle["pointer_sha256"] = pointer_sha256
            fleet_manifest_binding = "MATCH"
        events = _json(snapshot_bytes, snapshot_sha256, _LIMIT)
        if type(events) is not list or len(events) > 5000:
            raise ValueError("events")
        lanes = {lane: {"current_task": UNKNOWN, "latest_work_event": UNKNOWN,
                        "latest_claim_event": UNKNOWN, "latest_done_event": UNKNOWN,
                        "latest_handoff_event": UNKNOWN} for lane in LANES}
        requests = []
        for item in events:
            if type(item) is not dict or set(item) != {"ref", "event"} or type(item["event"]) is not dict:
                raise ValueError("event")
            ref, event = item["ref"], item["event"]
            if type(ref) is not str or not _HEX.fullmatch(ref) or event_id(event) != ref:
                raise ValueError("event_ref")
            agent, uuid = event.get("agent"), event.get("agent_uuid")
            if agent not in roster or type(uuid) is not str or uuid.lower() != roster[agent]:
                continue
            kind = event.get("type")
            if kind not in {"claim", "release", "done", "handoff", "message", "request", "wake_request"}:
                continue
            obs = _event_observation(ref, event, as_of_utc, ttl_seconds)
            if kind in {"claim", "release", "done", "handoff"}:
                categories = ["latest_work_event"]
                if kind in {"claim", "release"}: categories.append("latest_claim_event")
                if kind == "done": categories.append("latest_done_event")
                if kind == "handoff": categories.append("latest_handoff_event")
                for key in categories:
                    old = lanes[agent][key]
                    if old == UNKNOWN or _age_ticks(as_of_utc, obs["observed_utc"]) <= _age_ticks(as_of_utc, old["observed_utc"]):
                        lanes[agent][key] = dict(obs)
            payload = event.get("payload") if type(event.get("payload")) is dict else {}
            request_id = event.get("request_id") if event.get("request_id") is not None else payload.get("request_id")
            if kind in {"message", "request", "wake_request"} and type(request_id) is str and _TOKEN.fullmatch(request_id):
                if (event.get("request_id") is not None and payload.get("request_id") is not None
                        and event["request_id"] != payload["request_id"]):
                    continue
                target = event.get("to")
                targets = [item.strip() for item in target.split(",")] if type(target) is str else target
                if type(targets) is not list or not targets or any(x not in LANES for x in targets):
                    continue
                head_values = [payload[key] for key in ("head", "head_sha", "exact_head") if key in payload]
                head = head_values[0] if (head_values and all(type(value) is str and _COMMIT.fullmatch(value)
                        for value in head_values) and len(set(head_values)) == 1) else UNKNOWN
                pr = payload.get("pr")
                request_ref = "sha256:" + hashlib.sha256(request_id.encode("utf-8")).hexdigest()
                obs.update(request_id=request_ref if obs["status"] == "OBSERVED" else UNKNOWN,
                           recipients=targets if obs["status"] == "OBSERVED" else UNKNOWN,
                           requested_review=True if (obs["status"] == "OBSERVED"
                                                    and event.get("status") == "review_requested") else UNKNOWN,
                           pr=pr if obs["status"] == "OBSERVED" and type(pr) is int and pr > 0 else UNKNOWN,
                           head=head if obs["status"] == "OBSERVED" else UNKNOWN,
                           resolution=UNKNOWN, approval=UNKNOWN, binding_valid=UNKNOWN)
                requests.append(obs)
        return {"schema": "wd.mind-situation-observations.v1", "authority": "none",
                "scope": "bounded_tail_not_current_state_or_gate", "as_of_utc": as_of_utc,
                "source": {"snapshot_sha256": snapshot_sha256, "fleet_sha256": fleet_sha256,
                           "coverage": "bounded_tail", "sender_authentication": UNKNOWN,
                           "fleet_manifest_binding": fleet_manifest_binding},
                "bundle": bundle, "lanes": lanes, "request_observations": requests[-50:]}
    except Exception:
        raise SituationRefused("mind_situation_refused") from None

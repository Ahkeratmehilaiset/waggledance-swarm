# SPDX-License-Identifier: BUSL-1.1
"""Read-only lane projection over one verified Bridge Mind publication. Dormant; never a gate input.

``lane_read`` is a pure function: it reads the producer's ``latest.json`` commit pointer and the cycle files it
names under one owned ``.codex-audit`` root, verifies the publication with the core's own ``read_snapshot``, binds
the snapshot's declared log identity and generation to the pinned reader cursor the producer committed in
``state.json`` AND to the cursor of a compact-reader view the calling lane obtained itself, and returns a strict
allowlist projection: source counts, refs, heads, task ids, times and fixed tokens only; no decision-derived aggregates. Paths, write scopes, message
bodies, payloads, agent uuids and peer vote attributions never reach the output; any string outside the allowlist
refuses the whole output without echoing it.

Fail-closed producer binding (RCO2 F2 on PR1963): the current shadow pipeline declares the log identity from the
capture digest and generation ``None`` (the shadow materializer source declaration in its run step). Such a snapshot cannot be bound to
any reader observation, so this module refuses it with ``producer_binding_unavailable`` and names the missing
producer change. Nothing here authenticates origin: the binding proves that the snapshot, the producer's committed
cursor and the lane's own reader view name the same bridge log identity and generation, not which process wrote
them. No clock, bridge, network, process or gate module is used; ``as_of`` is the caller's declared time.
"""
from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import stat
from typing import Any, Mapping

from tools import wd_bridge_mind as core

SCHEMA = "wd.bridge-mind-lane-read.v1"
SCOPE = "read_only_lane_view_not_gate"
LANES = ("codex-lead-1", "codex-tools-1", "claude-rco-1", "claude-rco-2", "fable-5")
LATEST_SCHEMA = "wd.bridge-mind-shadow-latest.v2"
STATE_SCHEMA = "wd.bridge-mind-producer-state.v1"
VIEW_SCHEMA = "wd.bridge.compact-view.v1"
LOG_PREFIX = "bridge-log:"
MAX_AGE = timedelta(seconds=900)
BUDGET = 65536
POINTER_BUDGET = 65536
MISSING_PRODUCER_CHANGE = (
    "shadow_materializer_run_source_declaration: the materializer declares source.log_identity from the capture digest and generation None; "
    "the producer must pass its pinned reader position cursor so the shadow derives source.log_identity "
    "bridge-log:<file_identity> and source.generation from that cursor")

_REF = re.compile(r"[0-9a-f]{64}\Z")
_HEAD = re.compile(r"[0-9a-f]{40}\Z")
_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}\Z")
_CURSOR = re.compile(r"bc1\.[A-Za-z0-9_-]{1,2044}\Z")
_GENERATION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_IDENTITY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,244}\Z")
_UUID = re.compile(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\Z")
_TIME = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,9})?(?:Z|\+00:00)\Z")
_CYCLE = re.compile(r"[0-9]{8}T[0-9]{6}Z\Z")
_RELATIVE = re.compile(r"cycles/[0-9]{8}T[0-9]{6}Z/[A-Za-z0-9_./-]{1,80}\Z")
_LAST_OK_FILES = ("capture_file", "state_file", "publication_file", "board_file")
_LAST_OK_HASHES = ("capture_sha256", "state_sha256", "snapshot_sha256")
_LAST_OK_KEYS = frozenset(("cycle_dir", "generation", *_LAST_OK_FILES, *_LAST_OK_HASHES))
_STATE_KEYS = frozenset({"schema", "generation", "cursor", "source", "window_sha256", "window"})
_CI_TOKENS = frozenset({core.UNKNOWN, "reported_green", "reported_not_green"})
_CLASS_TOKENS = frozenset({core.UNKNOWN, "a", "b", "on-allowlist"})
_FACT_KINDS = frozenset({"lane_observation", "pr_observation"})


class LaneReadRefused(ValueError):
    """A fixed refusal reason; never carries snapshot content, a path or a caller value."""

    def __init__(self, reason: str, *, missing_producer_change: str | None = None) -> None:
        super().__init__(reason)
        self.missing_producer_change = missing_producer_change


# -- owned audit paths -------------------------------------------------------------------------------------------

def _owned(path: Path, audit_root: Path) -> Path:
    """An absolute regular file inside the ``.codex-audit`` root with no link or reparse point on the way."""
    try:
        path, root = Path(path).absolute(), Path(audit_root).absolute()
        if ".." in path.parts or ".." in root.parts or root.name != ".codex-audit" or not root.is_dir():
            raise LaneReadRefused("audit_path_refused")
        if path == root or not path.is_relative_to(root):
            raise LaneReadRefused("audit_path_refused")
        for component in [path, *path.parents]:
            try:
                info = component.lstat()
            except FileNotFoundError:
                if component == path:
                    continue
                raise LaneReadRefused("audit_path_refused") from None
            reparse = getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
            if stat.S_ISLNK(info.st_mode) or reparse:
                raise LaneReadRefused("audit_path_refused")
            if component == root:
                break
        return path
    except OSError:
        raise LaneReadRefused("audit_path_refused") from None


def _read_object(path: Path, audit_root: Path, reason: str) -> tuple[dict, bytes]:
    target = _owned(path, audit_root)
    try:
        raw = target.read_bytes()
        if len(raw) > POINTER_BUDGET:
            raise LaneReadRefused(reason)
        value = json.loads(raw.decode("utf-8"))
    except (OSError, ValueError, RecursionError):
        raise LaneReadRefused(reason) from None
    if not isinstance(value, dict):
        raise LaneReadRefused(reason)
    return value, raw


# -- the producer's pointer and state ----------------------------------------------------------------------------

def read_pointer(latest_path: Path, audit_root: Path) -> dict:
    """The committed ``last_ok`` of a producer-shaped ``latest.json``: cycle dir, four cycle files, three digests."""
    latest, _ = _read_object(latest_path, audit_root, "pointer_invalid")
    last_ok = latest.get("last_ok")
    if (latest.get("schema") != LATEST_SCHEMA or latest.get("authority") != "none" or not isinstance(last_ok, dict)
            or set(last_ok) != _LAST_OK_KEYS or not isinstance(last_ok["cycle_dir"], str)
            or not _CYCLE.fullmatch(last_ok["cycle_dir"]) or type(last_ok["generation"]) is not int
            or last_ok["generation"] < 1):
        raise LaneReadRefused("pointer_invalid")
    prefix = "cycles/" + last_ok["cycle_dir"] + "/"
    for key in _LAST_OK_FILES:
        value = last_ok[key]
        if not isinstance(value, str) or not value.startswith(prefix) or not _RELATIVE.fullmatch(value):
            raise LaneReadRefused("pointer_invalid")
    for key in _LAST_OK_HASHES:
        if not isinstance(last_ok[key], str) or not _REF.fullmatch(last_ok[key]):
            raise LaneReadRefused("pointer_invalid")
    return last_ok


def decode_cursor(token: Any) -> tuple[str, str | None]:
    """``(file_identity, generation)`` of a canonical compact-reader position cursor ``bc1.<base64url JSON>``."""
    if not isinstance(token, str) or not _CURSOR.fullmatch(token):
        raise LaneReadRefused("binding_invalid")
    body = token[4:]
    try:
        value = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)).decode("utf-8"))
    except (ValueError, RecursionError):
        raise LaneReadRefused("binding_invalid") from None
    if not isinstance(value, dict) or set(value) != {"file_identity", "generation", "offset"}:
        raise LaneReadRefused("binding_invalid")
    identity, generation, offset = value["file_identity"], value["generation"], value["offset"]
    if (not isinstance(identity, str) or not _IDENTITY.fullmatch(identity) or type(offset) is not int or offset < 0
            or (generation is not None and (not isinstance(generation, str) or not _GENERATION.fullmatch(generation)))):
        raise LaneReadRefused("binding_invalid")
    canonical = json.dumps({"file_identity": identity, "generation": generation, "offset": offset},
                           sort_keys=True, separators=(",", ":")).encode("utf-8")
    if base64.urlsafe_b64encode(canonical).decode("ascii").rstrip("=") != body:
        raise LaneReadRefused("binding_invalid")
    return identity, generation


def _binding(view: Any) -> tuple[str, str]:
    """The lane's own compact-reader view: its cursor must carry a file identity AND a generation."""
    if not isinstance(view, Mapping) or view.get("schema") != VIEW_SCHEMA or view.get("authority") != "none":
        raise LaneReadRefused("binding_invalid")
    identity, generation = decode_cursor(view.get("cursor"))
    if generation is None:
        raise LaneReadRefused("generation_unavailable")
    return identity, generation


def _pointer_digest_check(digest: str, expected: str) -> None:
    if digest != expected:
        raise LaneReadRefused("pointer_race_or_mismatch")


def _state_cursor(state_path: Path, audit_root: Path, expected_sha256: str) -> tuple[str, str | None, int]:
    state, raw = _read_object(state_path, audit_root, "pointer_race_or_mismatch")
    _pointer_digest_check(hashlib.sha256(raw).hexdigest(), expected_sha256)
    if (state.get("schema") != STATE_SCHEMA or set(state) != _STATE_KEYS or type(state["generation"]) is not int
            or state["generation"] < 1):
        raise LaneReadRefused("pointer_invalid")
    identity, generation = decode_cursor(state["cursor"])
    return identity, generation, state["generation"]


# -- allowlist projection ----------------------------------------------------------------------------------------

def _safe(value: Any, pattern: re.Pattern) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise LaneReadRefused("field_not_allowlisted")
    return value


def _token_or_unknown(value: Any) -> str:
    return core.UNKNOWN if value == core.UNKNOWN else _safe(value, _TOKEN)


def _time_or_unknown(value: Any) -> str:
    return core.UNKNOWN if value == core.UNKNOWN else _safe(value, _TIME)


def _count(value: Any) -> int:
    if type(value) is not int or value < 0:
        raise LaneReadRefused("field_not_allowlisted")
    return value


def _flag(value: Any) -> Any:
    if value is True or value is False or value == core.UNKNOWN:
        return value
    raise LaneReadRefused("field_not_allowlisted")


def _utc(value: str) -> datetime:
    return datetime.fromisoformat(_safe(value, _TIME).replace("Z", "+00:00")).astimezone(timezone.utc)


def _lanes(snapshot: Mapping[str, Any]) -> dict:
    # G4 (RCO2): a lane without a row is "not seen in this window", never "absent"; the window bounds travel in
    # source.window. G2 (RCO2): only rows whose identity the core marked declared (agent + well-formed uuid) can mark
    # a lane observed; two distinct declared uuids under one lane name are a conflict the adapter cannot resolve,
    # so the whole output is refused rather than letting either row win.
    result: dict[str, dict] = {lane: {"observed_in_window": False, "status": "not_seen_in_window"} for lane in LANES}
    newest: dict[str, datetime] = {}
    uuids: dict[str, set] = {}
    for record in snapshot.get("lanes", {}).values():
        if not isinstance(record, Mapping):
            raise LaneReadRefused("field_not_allowlisted")
        agent = record.get("agent")
        if agent not in LANES:
            continue  # a pseudo or unknown agent is not a lane; nothing of it is emitted
        uuid = record.get("agent_uuid")
        if record.get("verification") != "declared" or not isinstance(uuid, str) or not _UUID.fullmatch(uuid):
            if not result[agent]["observed_in_window"]:
                result[agent] = {"observed_in_window": False, "status": "unverified_identity_rows_only"}
            continue  # an identity-less row never marks or overrides a lane
        uuids.setdefault(agent, set()).add(uuid.lower())
        if len(uuids[agent]) > 1:
            raise LaneReadRefused("lane_identity_conflict")
        when = _utc(record["last_event_utc"])
        if agent in newest and newest[agent] >= when:
            continue
        newest[agent] = when
        claims: list[dict] = []
        if isinstance(record.get("claims"), Mapping):
            for claim in record["claims"].get("by_task", []):
                claims.append({"task_id": _token_or_unknown(claim["task_id"]),
                               "lease_expires_utc": _time_or_unknown(claim["lease_expires_utc"]),
                               "expired": _flag(claim["expired"])})
        age = record["observation_age_seconds"]
        if type(age) not in (int, float) or age < 0:
            raise LaneReadRefused("field_not_allowlisted")
        result[agent] = {"observed_in_window": True, "identity_verification": "declared",
                         "last_event_utc": _safe(record["last_event_utc"], _TIME),
                         "observation_age_seconds": age, "claims": claims}
    return result


def _prs(snapshot: Mapping[str, Any]) -> dict:
    result = {}
    for key, record in sorted(snapshot.get("prs", {}).items()):
        if not isinstance(record, Mapping) or type(record.get("pr")) is not int or str(record["pr"]) != key:
            raise LaneReadRefused("field_not_allowlisted")
        head = core.UNKNOWN if record["head"] == core.UNKNOWN else _safe(record["head"], _HEAD)
        base = core.UNKNOWN if record["base"] == core.UNKNOWN else _safe(record["base"], _HEAD)
        ci = record["reported_ci"].get("status") if isinstance(record.get("reported_ci"), Mapping) else None
        if ci not in _CI_TOKENS or record["class"] not in _CLASS_TOKENS:
            raise LaneReadRefused("field_not_allowlisted")
        missing = [_safe(item, _TOKEN) for item in record.get("missing", [])]
        # Decision-derived aggregates (vote, stale vote, veto and recusal counts) are deliberately absent: a lane that
        # knows its own decision is absent could infer a peer decision from any nonzero count (Lead finding candidate
        # 2026-10-31). The projection must be identical whether or not a peer has decided; "missing" is the fixed core
        # token list, never computed from votes.
        result[key] = {"pr": record["pr"], "head": head, "base": base, "class": record["class"], "reported_ci_status": ci,
                       "missing": missing}
    return result


def _facts(snapshot: Mapping[str, Any]) -> list:
    result = []
    for fact in snapshot.get("facts", []):
        if not isinstance(fact, Mapping) or fact.get("kind") not in _FACT_KINDS:
            raise LaneReadRefused("field_not_allowlisted")
        refs = fact.get("source_refs")
        if not isinstance(refs, list) or not refs:
            raise LaneReadRefused("field_not_allowlisted")
        result.append({"kind": fact["kind"], "canonical_sha256": _safe(fact.get("canonical_sha256"), _REF),
                       "source_refs": [_safe(ref, _REF) for ref in refs],
                       "observed_at_utc": _safe(fact.get("observed_at_utc"), _TIME),
                       "salience": _count(fact.get("salience")), "verification": _safe(fact.get("verification"), _TOKEN)})
    return result


def _source(source: Mapping[str, Any]) -> dict:
    blocked = source.get("blocked")
    if blocked is not None:
        if not isinstance(blocked, Mapping) or blocked.get("reason") not in core.BLOCKED_REASONS:
            raise LaneReadRefused("field_not_allowlisted")
        blocked = {"active": True, "reason": blocked["reason"]}
    last_ref = source["last_event_ref"]
    return {"log_identity": _safe(source["log_identity"], _TOKEN), "generation": _safe(source["generation"], _GENERATION),
            "as_of_utc": _safe(source["as_of_utc"], _TIME), "max_event_utc": _time_or_unknown(source["max_event_utc"]),
            "event_count": _count(source["event_count"]), "unique_event_count": _count(source["unique_event_count"]),
            "duplicate_count": _count(source["duplicate_count"]),
            "window": {"cursor_start": _count(source["cursor_start"]), "cursor_end": _count(source["cursor_end"]),
                       "coverage": _safe(source["coverage"], _TOKEN)},
            "last_event_ref": None if last_ref is None else _safe(last_ref, _REF), "blocked": blocked}


# -- the read ----------------------------------------------------------------------------------------------------

def lane_read(*, latest_path: Path, audit_root: Path, lane: Any, as_of: datetime, binding: Any,
              max_age: timedelta = MAX_AGE) -> dict:
    """One verified, bound, allowlisted projection for ``lane``; refuses with a fixed reason otherwise."""
    if not isinstance(lane, str) or lane not in LANES:
        raise LaneReadRefused("lane_unknown")
    if not isinstance(as_of, datetime) or as_of.tzinfo is None or as_of.utcoffset() is None:
        raise LaneReadRefused("invalid_context")
    if not isinstance(max_age, timedelta) or max_age <= timedelta(0) or max_age > MAX_AGE:
        raise LaneReadRefused("max_age_invalid")  # G1 (RCO2): the caller may narrow the window, never widen it
    view_identity, view_generation = _binding(binding)
    last_ok = read_pointer(latest_path, audit_root)
    root = Path(audit_root).absolute()
    try:
        snapshot = core.read_snapshot(root / last_ok["publication_file"], audit_root=root)
    except core.ProjectionError as exc:
        reason = str(exc)
        raise LaneReadRefused(reason if reason in {"snapshot_read_refused", "snapshot_publication_invalid",
                              "snapshot_content_mismatch"} else "snapshot_read_refused") from None
    _pointer_digest_check(hashlib.sha256(core.canonical_bytes(snapshot)).hexdigest(), last_ok["snapshot_sha256"])
    state_identity, state_generation, cycle_generation = _state_cursor(root / last_ok["state_file"], root,
                                                                       last_ok["state_sha256"])
    if cycle_generation != last_ok["generation"]:
        raise LaneReadRefused("pointer_race_or_mismatch")
    source = snapshot["source"]
    declared_identity, declared_generation = source.get("log_identity"), source.get("generation")
    if (not isinstance(declared_identity, str) or not declared_identity.startswith(LOG_PREFIX)
            or declared_generation is None):
        raise LaneReadRefused("producer_binding_unavailable", missing_producer_change=MISSING_PRODUCER_CHANGE)
    if ((state_identity, state_generation) != (view_identity, view_generation)
            or declared_identity != LOG_PREFIX + view_identity or declared_generation != view_generation):
        raise LaneReadRefused("log_identity_mismatch")
    snapshot_as_of = _utc(source["as_of_utc"])
    age = as_of.astimezone(timezone.utc) - snapshot_as_of
    if age < timedelta(0):
        raise LaneReadRefused("future_snapshot")
    if age > max_age:
        raise LaneReadRefused("snapshot_stale")  # G1 (RCO2): a stale snapshot never yields a body
    result = {
        "schema": SCHEMA, "authority": "none", "scope": SCOPE, "lane": lane,
        "as_of_utc": as_of.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "binding": {"file_identity": view_identity, "generation": view_generation,
                    "origin": "lane_reader_view_not_authenticated"},
        "pointer": {"cycle_dir": last_ok["cycle_dir"], "cycle_generation": cycle_generation,
                    "snapshot_sha256": last_ok["snapshot_sha256"], "state_sha256": last_ok["state_sha256"]},
        "freshness": {"age_seconds": age.total_seconds(), "max_age_seconds": max_age.total_seconds(), "status": "fresh"},
        "source": _source(source), "lanes": _lanes(snapshot), "prs": _prs(snapshot), "facts_meta": _facts(snapshot),
        "refusal": None,
    }
    if len(core.canonical_bytes(result)) > BUDGET:
        raise LaneReadRefused("output_budget_exceeded")
    return result

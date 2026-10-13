# SPDX-License-Identifier: BUSL-1.1
"""Dormant, caller-fed Bridge Mind; never a bridge reader or gate input.

Envelopes are complete ``{ref,event}`` pinned-reader observations. Hashes bind
content, NOT actor origin or semantic truth. Source coverage/cursors are caller
declarations. MindState retains the reducer and exact-duplicate index separately
from TTL/capacity-bounded display facts. No clock, network, provider or bridge
write is used. Publication is opt-in to an explicit existing .codex-audit root.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
from typing import Any

from tools.bridge_compact_view import event_id
from waggledance.core.bridge_log_reader import parse_bridge_json_object

UNKNOWN = "UNKNOWN"
_REF = re.compile(r"[0-9a-f]{64}\Z")
_HEAD = re.compile(r"[0-9a-f]{40}\Z")
_AGENT = re.compile(r"[a-z][a-z0-9_-]{1,32}\Z")
_UUID = re.compile(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\Z")
_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}\Z")
_PATH = re.compile(r"[A-Za-z0-9_.][A-Za-z0-9_./\\: -]{0,2047}\Z")
_PASS = {"rco_pass", "build_consensus_pass"}
_VETO = {"changes_requested", "rco_fail", "review_failed", "hold", "blocked"}
_HEAD_UPDATE = {"head_updated", "source_ready", "ready", "requested", "review_requested", "published", "opened"}
_SIGNAL_TYPES = {"decision", "finding", "rco_review"}
_MARKERS = ("PRIVATE_MARKER", "_DO_NOT_LEAK")
_ESCAPED_MARKERS = tuple(re.compile("".join("(?:" + re.escape(char) + r"|\\u" + format(ord(char), "04x") + ")" for char in marker), re.IGNORECASE) for marker in _MARKERS)


class ProjectionError(ValueError):
    """Fixed diagnostic codes only; never echo source content or paths."""


def canonical_bytes(value: Any) -> bytes:
    try:
        encoded = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        if any(marker in encoded for marker in _MARKERS) or any(pattern.search(encoded) for pattern in _ESCAPED_MARKERS):
            raise ProjectionError("private_marker_refused")
        return encoded.encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        if isinstance(exc, ProjectionError):
            raise
        raise ProjectionError("canonical_json_invalid") from None


def _time(value: Any) -> datetime:
    try:
        if not isinstance(value, str) or len(value) > 40:
            raise ValueError
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset().total_seconds() != 0:
            raise ValueError
        return parsed.astimezone(timezone.utc)
    except (ValueError, TypeError, AttributeError):
        raise ProjectionError("explicit_utc_time_required") from None


def _iso(value: Any) -> str:
    return _time(value).isoformat().replace("+00:00", "Z")


def _token(value: Any) -> str:
    return value if isinstance(value, str) and _TOKEN.fullmatch(value) else UNKNOWN


def _source(value: Any, count: int) -> dict:
    if not isinstance(value, dict) or set(value) != {"log_identity", "generation", "cursor_start", "cursor_end", "coverage", "event_count"}:
        raise ProjectionError("source_cursor_required")
    canonical_bytes(value)
    start, end = value["cursor_start"], value["cursor_end"]
    if (type(start) is not int or type(end) is not int or start < 0 or end < start
            or (count == 0) != (start == end) or type(value["event_count"]) is not int
            or value["event_count"] != count):
        raise ProjectionError("source_cursor_count_invalid")
    if value["coverage"] != "complete":
        raise ProjectionError("complete_coverage_required")
    if _token(value["log_identity"]) == UNKNOWN or (value["generation"] is not None and _token(value["generation"]) == UNKNOWN):
        raise ProjectionError("source_identity_required")
    return deepcopy(value)


@dataclass
class MindState:
    """Private resumable reduction state, not the small published snapshot."""
    source: dict
    max_facts: int = 64
    ttl_seconds: int = 300
    seen: set[str] = field(default_factory=set, repr=False)
    prs: dict = field(default_factory=dict, repr=False)
    lanes: dict = field(default_factory=dict, repr=False)
    fact_candidates: dict = field(default_factory=dict, repr=False)
    duplicates: int = 0
    last_ref: str = ""
    max_event_utc: str | None = None


def _envelope(value: Any) -> tuple[str, dict]:
    if not isinstance(value, dict) or set(value) != {"ref", "event"} or not isinstance(value["event"], dict):
        raise ProjectionError("provenance_envelope_required")
    ref = value["ref"]
    if not isinstance(ref, str) or not _REF.fullmatch(ref):
        raise ProjectionError("provenance_ref_invalid")
    raw = canonical_bytes(value["event"])
    try:
        event = parse_bridge_json_object(raw.decode("utf-8"))
    except (ValueError, RecursionError):
        raise ProjectionError("canonical_event_invalid") from None
    if event_id(event) != ref:
        raise ProjectionError("provenance_hash_mismatch")
    return ref, event


def _fact(state: MindState, kind: str, key: str, value: dict, ref: str, when: str, salience: int, refs: list | None = None) -> None:
    state.fact_candidates[kind + ":" + key] = {
        "fact_id": hashlib.sha256((kind + ":" + key).encode("utf-8")).hexdigest(),
        "kind": kind, "value": deepcopy(value), "source_refs": sorted(set(refs or [ref])), "canonical_sha256": ref,
        "observed_at_utc": when, "ttl_seconds": state.ttl_seconds, "salience": salience,
        "verification": "declared",
    }


def _fold(state: MindState, rows: list, as_of: datetime) -> None:
    for envelope in rows:
        ref, event = _envelope(envelope)
        when = _iso(event.get("ts_utc"))
        if _time(when) > as_of:
            raise ProjectionError("as_of_precedes_event")
        if state.max_event_utc is None or _time(when) > _time(state.max_event_utc):
            state.max_event_utc = when
        state.last_ref = ref
        if ref in state.seen:
            state.duplicates += 1
            continue
        state.seen.add(ref)
        payload = event.get("payload", {})
        payload = payload if isinstance(payload, dict) else {}
        agent = event.get("agent")
        agent = agent if isinstance(agent, str) and _AGENT.fullmatch(agent) else UNKNOWN
        uuid = event.get("agent_uuid")
        valid_identity = agent != UNKNOWN and isinstance(uuid, str) and bool(_UUID.fullmatch(uuid))
        uuid = uuid if valid_identity else UNKNOWN
        identity = agent + "|" + uuid + ("" if valid_identity else "|" + ref)
        status = event.get("status", "")
        status = status.lower() if isinstance(status, str) else ""
        kind = event.get("type", "")
        task = _token(event.get("task_id"))
        previous = state.lanes.get(identity, {})
        lane = dict(agent=agent, agent_uuid=uuid, session_id=_token(event.get("session_id")),
                    run_id=_token(event.get("run_id")), current_task=task,
                    claims=deepcopy(previous.get("claims", UNKNOWN)), waits=deepcopy(previous.get("waits", UNKNOWN)),
                    last_event_ref=ref, last_event_utc=when, source_refs=[ref],
                    verification="declared" if valid_identity else "unverified", idle_status=UNKNOWN,
                    uncertainty=["origin_not_authenticated", "native_readiness_UNKNOWN"])
        lane["next_question"] = UNKNOWN
        scope = event.get("write_scope", payload.get("write_scope"))
        if scope is not None:
            valid_scope = isinstance(scope, list) and all(isinstance(p, str) and _PATH.fullmatch(p) for p in scope)
            lane["claims"] = {"write_scope": deepcopy(scope) if valid_scope else UNKNOWN,
                              "source_refs": [ref], "verification": "declared"}
            if not valid_scope:
                lane["uncertainty"].append("invalid_write_scope")
            expiry = payload.get("claim_lease_expires_utc", event.get("claim_lease_expires_utc"))
            lane["claims"]["lease_expires_utc"] = _iso(expiry) if expiry is not None else UNKNOWN
        waiting = payload.get("waiting_on")
        if waiting is not None:
            values = [waiting] if isinstance(waiting, str) else waiting
            lane["waits"] = {"waiting_on": values if isinstance(values, list) and all(_token(x) != UNKNOWN for x in values) else UNKNOWN,
                             "source_refs": [ref], "verification": "declared"}
        lane["source_refs"] = sorted({ref} | {r for name in ("claims", "waits") if isinstance(lane[name], dict) for r in lane[name]["source_refs"]})
        state.lanes[identity] = lane
        _fact(state, "lane_observation", identity, dict(agent=agent, agent_uuid=uuid, current_task=task), ref, when, 50)
        pr = payload.get("pr")
        if type(pr) is not int or pr <= 0:
            continue
        bindings = [payload[key] for key in ("exact_head", "head_sha", "head") if key in payload]
        if len(set(canonical_bytes(value) for value in bindings)) > 1:
            raise ProjectionError("head_binding_conflict")
        head = bindings[0] if bindings and isinstance(bindings[0], str) and _HEAD.fullmatch(bindings[0]) else UNKNOWN
        key = str(pr)
        record = state.prs.setdefault(key, dict(pr=pr, head=UNKNOWN, base=UNKNOWN, reported_ci={"status": UNKNOWN},
                  class_value=UNKNOWN, source_refs=[ref], _field_refs={}, _votes={}, _vetos={}))
        if head != UNKNOWN and (record["head"] == UNKNOWN or status in _HEAD_UPDATE):
            if head != record["head"]:
                record["reported_ci"] = {"status": UNKNOWN}
            record["head"] = head
            record["_field_refs"]["head"] = ref
        base = payload.get("base")
        if isinstance(base, str) and _HEAD.fullmatch(base):
            record["base"] = base
            record["_field_refs"]["base"] = ref
        classification = payload.get("class", payload.get("classification"))
        if classification in ("a", "b", "on-allowlist"):
            record["class_value"] = classification
            record["_field_refs"]["class"] = ref
        ci = payload.get("required_checks_green")
        if type(ci) is bool and head != UNKNOWN and head == record["head"]:
            record["reported_ci"] = dict(status="reported_green" if ci else "reported_not_green",
                                         head=head, source_refs=[ref], verification="declared")
        if kind in _SIGNAL_TYPES and status in _PASS and head != UNKNOWN:
            vote = dict(agent=agent, agent_uuid=uuid, head=head, status=status, source_refs=[ref], verification="declared")
            record["_votes"][(agent, uuid, head, status)] = vote
        if kind in _SIGNAL_TYPES and status in _VETO:
            # Observations only: never infer a gate-clearing transition from a pass/closeout.
            record["_vetos"][(agent, uuid, task, head)] = dict(agent=agent, agent_uuid=uuid, head=head,
                  status=status, activity=UNKNOWN, source_refs=[ref], verification="declared")
        record["source_refs"] = sorted({ref} | set(record["_field_refs"].values()))
        _fact(state, "pr_observation", key, dict(pr=pr, head=record["head"]), ref, when, 100 if status in _VETO else 80, record["source_refs"])


def full_rebuild(envelopes: list, *, source: dict, as_of_utc: str, max_facts: int = 64, ttl_seconds: int = 300) -> MindState:
    if not isinstance(envelopes, list) or type(max_facts) is not int or not 1 <= max_facts <= 10000 or type(ttl_seconds) is not int or ttl_seconds < 1:
        raise ProjectionError("projection_policy_invalid")
    origin = _source(source, len(envelopes))
    if origin["cursor_start"] != 0:
        raise ProjectionError("full_rebuild_requires_origin_cursor")
    state = MindState(origin, max_facts, ttl_seconds)
    _fold(state, envelopes, _time(as_of_utc))
    return state


def apply_incremental(state: MindState, envelopes: list, *, source: dict, as_of_utc: str) -> MindState:
    if not isinstance(state, MindState) or not isinstance(envelopes, list):
        raise ProjectionError("resumable_state_required")
    incoming = _source(source, len(envelopes))
    if (incoming["log_identity"] != state.source["log_identity"] or incoming["generation"] != state.source["generation"]
            or incoming["cursor_start"] != state.source["cursor_end"]):
        raise ProjectionError("cursor_discontinuity_rebuild_required")
    result = deepcopy(state)  # Caller retains its good checkpoint on any refusal.
    _fold(result, envelopes, _time(as_of_utc))
    result.source["cursor_end"] = incoming["cursor_end"]
    result.source["event_count"] += incoming["event_count"]
    return result


def snapshot(state: MindState, *, as_of_utc: str) -> dict:
    horizon = _time(as_of_utc)
    if state.max_event_utc is not None and horizon < _time(state.max_event_utc):
        raise ProjectionError("as_of_precedes_event")
    prs = {}
    for key, record in sorted(state.prs.items()):
        prs[key] = {name: deepcopy(record[name]) for name in ("pr", "head", "base", "reported_ci", "source_refs")}
        prs[key]["class"] = record["class_value"]
        versions = [deepcopy(v) for _, v in sorted(record["_votes"].items())]
        prs[key]["votes"] = [v for v in versions if v["head"] == record["head"]]
        prs[key]["stale_votes"] = [v for v in versions if v["head"] != record["head"]]
        prs[key]["veto_observations"] = [deepcopy(v) for _, v in sorted(record["_vetos"].items())]
        prs[key]["missing"] = ["gate_eligibility_UNKNOWN"]
    lanes = deepcopy(state.lanes)
    for lane in lanes.values():
        age = (horizon - _time(lane["last_event_utc"])).total_seconds()
        if age < 0:
            raise ProjectionError("as_of_precedes_event")
        lane["observation_age_seconds"] = age
        if isinstance(lane["claims"], dict):
            expiry = lane["claims"].get("lease_expires_utc", UNKNOWN)
            lane["claims"]["expired"] = horizon >= _time(expiry) if expiry != UNKNOWN else UNKNOWN
    active = []
    for key, fact in state.fact_candidates.items():
        age = (horizon - _time(fact["observed_at_utc"])).total_seconds()
        if age < 0:
            raise ProjectionError("as_of_precedes_event")
        if age < fact["ttl_seconds"]:
            active.append((key, fact))
    active.sort(key=lambda item: (-item[1]["salience"], item[0]))
    result = dict(schema="wd.bridge-mind.v1", authority="none", scope="bounded_projection_not_gate",
             source=dict(deepcopy(state.source), as_of_utc=_iso(as_of_utc), unique_event_count=len(state.seen),
                         duplicate_count=state.duplicates, last_event_ref=state.last_ref,
                         max_event_utc=state.max_event_utc, blocked=None),
             prs=prs, lanes=lanes, facts=[deepcopy(fact) for _, fact in active[:state.max_facts]],
             tensions=[], attention=[], next_question={"lanes": {}, "global": {"question": UNKNOWN, "attention_rank": None, "refs": [UNKNOWN]}})
    canonical_bytes(result)
    return result


def _audit_path(path: Path, audit_root: Path) -> Path:
    path, root = Path(path).absolute(), Path(audit_root).absolute()
    if root.name != ".codex-audit" or not root.is_dir() or path == root or not path.is_relative_to(root) or not path.parent.is_dir():
        raise ProjectionError("owned_audit_destination_required")
    for component in [path, *path.parents]:
        try:
            info = component.lstat()
        except FileNotFoundError:
            if component == path:
                continue
            raise ProjectionError("audit_parent_missing") from None
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
            raise ProjectionError("linked_audit_destination_refused")
    if path.exists() and not path.is_file():
        raise ProjectionError("audit_file_required")
    return path


def _validate_snapshot(value: Any) -> None:
    if (not isinstance(value, dict) or value.get("schema") != "wd.bridge-mind.v1" or value.get("authority") != "none"
            or value.get("scope") != "bounded_projection_not_gate"):
        raise ProjectionError("snapshot_schema_invalid")
    if set(value) != {"schema", "authority", "scope", "source", "prs", "lanes", "facts", "tensions", "attention", "next_question"}:
        raise ProjectionError("snapshot_field_invalid")
    canonical_bytes(value)
    src = value.get("source", {})
    if not isinstance(src, dict):
        raise ProjectionError("source_cursor_required")
    fields = {key: src.get(key) for key in ("log_identity", "generation", "cursor_start", "cursor_end", "coverage", "event_count")}
    _source(fields, fields["event_count"])
    _time(src.get("as_of_utc"))
    if (type(src.get("unique_event_count")) is not int or type(src.get("duplicate_count")) is not int
            or src["unique_event_count"] < 0 or src["duplicate_count"] < 0
            or src["unique_event_count"] + src["duplicate_count"] != src["event_count"]):
        raise ProjectionError("snapshot_source_count_mismatch")
    pr_keys = {"pr", "head", "base", "reported_ci", "source_refs", "class", "votes", "stale_votes", "veto_observations", "missing"}
    lane_keys = {"agent", "agent_uuid", "session_id", "run_id", "current_task", "claims", "waits", "last_event_ref", "last_event_utc", "source_refs", "verification", "idle_status", "uncertainty", "observation_age_seconds", "next_question"}
    if not isinstance(value.get("prs"), dict) or not isinstance(value.get("lanes"), dict):
        raise ProjectionError("snapshot_entities_invalid")
    for records, allowed in ((value["prs"], pr_keys), (value["lanes"], lane_keys)):
        for record in records.values():
            if not isinstance(record, dict) or set(record) != allowed:
                raise ProjectionError("snapshot_field_invalid")
    for record in value["prs"].values():
        if (record["head"] != UNKNOWN and (not isinstance(record["head"], str) or not _HEAD.fullmatch(record["head"]))) or record["class"] not in (UNKNOWN, "a", "b", "on-allowlist"):
            raise ProjectionError("snapshot_pr_value_invalid")
        ci = record["reported_ci"]
        if not isinstance(ci, dict) or set(ci) - {"status", "head", "source_refs", "verification"} or ci.get("status") not in (UNKNOWN, "reported_green", "reported_not_green"):
            raise ProjectionError("snapshot_ci_invalid")
        for name, keys in (("votes", {"agent", "agent_uuid", "head", "status", "source_refs", "verification"}),
                           ("stale_votes", {"agent", "agent_uuid", "head", "status", "source_refs", "verification"}),
                           ("veto_observations", {"agent", "agent_uuid", "head", "status", "source_refs", "verification", "activity"})):
            if not isinstance(record[name], list) or any(not isinstance(row, dict) or set(row) != keys for row in record[name]):
                raise ProjectionError("snapshot_signal_invalid")
    for lane in value["lanes"].values():
        for name, keys in (("claims", {"write_scope", "source_refs", "verification", "lease_expires_utc", "expired"}),
                           ("waits", {"waiting_on", "source_refs", "verification"})):
            if lane[name] != UNKNOWN and (not isinstance(lane[name], dict) or set(lane[name]) != keys):
                raise ProjectionError("snapshot_lane_value_invalid")
    def refs_required(record):
        if isinstance(record, dict):
            if "source_refs" in record:
                refs = record["source_refs"]
                if not isinstance(refs, list) or not refs or any(not isinstance(ref, str) or not _REF.fullmatch(ref) for ref in refs):
                    raise ProjectionError("provenance_fact_ref_required")
            for nested in record.values():
                refs_required(nested)
        elif isinstance(record, list):
            for nested in record:
                refs_required(nested)
    refs_required(value)
    if not isinstance(value.get("facts"), list):
        raise ProjectionError("snapshot_facts_invalid")
    for fact in value["facts"]:
        if not isinstance(fact, dict) or fact.get("kind") not in {"lane_observation", "pr_observation"}:
            raise ProjectionError("snapshot_fact_type_invalid")
        if set(fact) != {"fact_id", "kind", "value", "source_refs", "canonical_sha256", "observed_at_utc", "ttl_seconds", "salience", "verification"} or not _REF.fullmatch(str(fact["fact_id"])) or fact["verification"] != "declared":
            raise ProjectionError("snapshot_fact_field_invalid")
        refs = fact.get("source_refs")
        if not isinstance(refs, list) or not refs or any(not isinstance(ref, str) or not _REF.fullmatch(ref) for ref in refs) or fact.get("canonical_sha256") not in refs:
            raise ProjectionError("provenance_fact_ref_required")
        allowed = {"agent", "agent_uuid", "current_task"} if fact["kind"] == "lane_observation" else {"pr", "head"}
        if not isinstance(fact.get("value"), dict) or set(fact["value"]) != allowed:
            raise ProjectionError("snapshot_fact_value_invalid")


def _write_publication(publication: dict, path: Path, audit_root: Path) -> str:
    target = _audit_path(path, audit_root)
    encoded = canonical_bytes(publication)
    temporary = None
    try:
        fd, temporary = tempfile.mkstemp(prefix=".mind-", suffix=".tmp", dir=target.parent)
        with os.fdopen(fd, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        _audit_path(target, audit_root)
        os.replace(temporary, target)
        temporary = None
    except OSError:
        raise ProjectionError("snapshot_publication_failed") from None
    finally:
        if temporary is not None:
            os.unlink(temporary)
    return hashlib.sha256(encoded).hexdigest()


def write_snapshot(value: dict, path: Path, *, audit_root: Path) -> str:
    """Publish display+source cursor atomically; use checkpoint APIs to resume.

    These explicit audit APIs are not a concurrent hostile-user sandbox.
    """
    _validate_snapshot(value)
    digest = hashlib.sha256(canonical_bytes(value)).hexdigest()
    _write_publication(dict(schema="wd.bridge-mind-publication.v1", snapshot=value, snapshot_sha256=digest), path, audit_root)
    return digest


def read_snapshot(path: Path, *, audit_root: Path) -> dict:
    """Verify an audit publication envelope, not external evidence or authenticity."""
    target = _audit_path(path, audit_root)
    try:
        raw = target.read_bytes()
        publication = parse_bridge_json_object(raw.decode("utf-8"))
        if not isinstance(publication, dict) or set(publication) != {"schema", "snapshot", "snapshot_sha256"} or publication["schema"] != "wd.bridge-mind-publication.v1":
            raise ProjectionError("snapshot_publication_invalid")
        value = publication["snapshot"]
        _validate_snapshot(value)
        if hashlib.sha256(canonical_bytes(value)).hexdigest() != publication["snapshot_sha256"]:
            raise ProjectionError("snapshot_content_mismatch")
        return value
    except (OSError, ValueError, TypeError, RecursionError):
        raise ProjectionError("snapshot_read_refused") from None


def blocked_snapshot(state: MindState, *, as_of_utc: str, offset: int, reason: str) -> dict:
    """Diagnostic view only; unchanged accepted state/cursor, never current."""
    reasons = {"canonical_event_invalid", "provenance_hash_mismatch", "provenance_envelope_required",
               "source_cursor_required", "source_cursor_count_invalid", "complete_coverage_required",
               "cursor_discontinuity_rebuild_required", "file_identity_changed", "log_truncated",
               "generation_changed", "partial_record", "private_marker_refused"}
    if type(offset) is not int or offset < 0 or reason not in reasons:
        raise ProjectionError("blocked_diagnostic_invalid")
    value = snapshot(state, as_of_utc=as_of_utc)
    value["source"]["blocked"] = dict(active=True, offset=offset, reason=reason)
    value["tensions"] = [dict(rule_id="source_blocked", offset=offset, reason=reason,
                              refs=[state.last_ref] if state.last_ref else [UNKNOWN])]
    return value


def state_bytes(state: MindState) -> bytes:
    """Complete resumable fold state, distinct from bounded display memory."""
    prs = deepcopy(state.prs)
    for record in prs.values():
        record["_votes"] = [v for _, v in sorted(record["_votes"].items())]
        record["_vetos"] = [[list(key), value] for key, value in sorted(record["_vetos"].items())]
    return canonical_bytes(dict(schema="wd.bridge-mind-state.v1", source=state.source, max_facts=state.max_facts,
              ttl_seconds=state.ttl_seconds, seen=sorted(state.seen), prs=prs, lanes=state.lanes,
              fact_candidates=state.fact_candidates, duplicates=state.duplicates, last_ref=state.last_ref,
              max_event_utc=state.max_event_utc))


def write_checkpoint(state: MindState, path: Path, *, as_of_utc: str, audit_root: Path) -> str:
    """Atomic reducer+accepted cursor+display, suitable for a dormant restart."""
    view = snapshot(state, as_of_utc=as_of_utc)
    _validate_snapshot(view)
    body = dict(state=json.loads(state_bytes(state)), snapshot=view)
    digest = hashlib.sha256(canonical_bytes(body)).hexdigest()
    _write_publication(dict(schema="wd.bridge-mind-checkpoint.v1", body=body, body_sha256=digest), path, audit_root)
    return digest


def read_checkpoint(path: Path, *, audit_root: Path) -> MindState:
    target = _audit_path(path, audit_root)
    try:
        publication = parse_bridge_json_object(target.read_bytes().decode("utf-8"))
        if set(publication) != {"schema", "body", "body_sha256"} or publication["schema"] != "wd.bridge-mind-checkpoint.v1":
            raise ValueError
        body = publication["body"]
        if hashlib.sha256(canonical_bytes(body)).hexdigest() != publication["body_sha256"]:
            raise ValueError
        raw = body["state"]
        if raw["schema"] != "wd.bridge-mind-state.v1" or set(raw) != {"schema", "source", "max_facts", "ttl_seconds", "seen", "prs", "lanes", "fact_candidates", "duplicates", "last_ref", "max_event_utc"}:
            raise ValueError
        origin = _source(raw["source"], raw["source"]["event_count"])
        if (origin["cursor_start"] != 0 or type(raw["max_facts"]) is not int or not 1 <= raw["max_facts"] <= 10000
                or type(raw["ttl_seconds"]) is not int or raw["ttl_seconds"] < 1
                or type(raw["duplicates"]) is not int or raw["duplicates"] < 0
                or not isinstance(raw["seen"], list) or len(set(raw["seen"])) != len(raw["seen"])
                or any(not isinstance(ref, str) or not _REF.fullmatch(ref) for ref in raw["seen"])
                or len(raw["seen"]) + raw["duplicates"] != origin["event_count"]):
            raise ValueError
        expected_state = canonical_bytes(raw)
        prs = deepcopy(raw["prs"])
        for record in prs.values():
            record["_votes"] = {(v["agent"], v["agent_uuid"], v["head"], v["status"]): v for v in record["_votes"]}
            record["_vetos"] = {tuple(key): value for key, value in record["_vetos"]}
        state = MindState(origin, raw["max_facts"], raw["ttl_seconds"], set(raw["seen"]), prs,
                  raw["lanes"], raw["fact_candidates"], raw["duplicates"], raw["last_ref"], raw["max_event_utc"])
        _validate_snapshot(body["snapshot"])
        if canonical_bytes(snapshot(state, as_of_utc=body["snapshot"]["source"]["as_of_utc"])) != canonical_bytes(body["snapshot"]):
            raise ValueError
        if state_bytes(state) != expected_state:
            raise ValueError
        return state
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError):
        raise ProjectionError("checkpoint_read_refused") from None

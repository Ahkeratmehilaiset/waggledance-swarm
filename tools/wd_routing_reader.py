"""F19 W2: an advisory reader that loads ONLY caller-explicit files for ``compose``.

``read_routing_inputs(paths, now)`` reads each file the caller names, and nothing else: no discovery,
environment, clock, provider or live collector. ``now`` is the caller's one aware instant, handed
unchanged to W1 ``assemble`` and to ``compose``. The result is advice only (``authority: "none"``,
``execution_allowed: False``); it claims, dispatches and writes nothing.

Reading: each path is checked with ``lstat`` (a regular file, not a link or reparse point, within
``MAX_BYTES``), opened once, checked again on the open handle, and read once. Its byte sha256 and its
parsed document both come from those same captured bytes; the file is never reopened. Parsing is strict
UTF-8 and strict JSON: a BOM, NaN, Infinity or a duplicate key refuses the document. A refused document
is ``None`` with the reason ``<name>_unreadable:<kind>``, never a default. No reason carries path text or
file content.

What it never infers (Lead 20:10:11Z):

* A running profile. The lane checkpoint (``wd.lane-current.v1``) has no profile field; the lane catalog
  default and a desired-profile record are not the running profile. Every lane is ``profile_unproven`` and
  yields no worker record.
* A subject. The checkpoint carries none, a pool-binding decision's subject_id is not verified and names
  no worker, and no Codex lane maps to an auth context. Every lane is ``subject_unbound``.
* A signature. The signed policy is passed on unverified; shadow weights stay ``None``.

Grok joins only when the caller lists it (``grok_profile_id``): a consult-only record with no subject,
which compose reports as ``no_measured_grok_capacity`` and the router never ranks.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import stat
from datetime import timedelta
from typing import Any

from tools.bridge_pool_binding import _aware_utc
from tools.wd_composer_select import digest
from tools.wd_routing_capacity import compose
from tools.wd_routing_inputs import (LANE_EVIDENCE_SCHEMA, MAX_LANE_EVIDENCE_AGE_SECONDS, MAX_PROFILE_TEXT, _instant,
                                     _text, assemble)
from tools.wd_task_router import GROK, MEMBERS

SCHEMA = "wd.routing-reader.v1"
CHECKPOINT_SCHEMA = "wd.lane-current.v1"
STATUS_SCHEMA = "wd.capacity-status.v1"
DOCUMENT_PATHS = ("task", "capacity_status", "paced", "prepared_artifacts", "routing_policy", "signed_policy")
OPTIONAL_PATHS = ("signed_policy",)
MAX_BYTES = 1048576
MAX_PATH_TEXT = 1024
REPARSE_POINT = 0x400       # FILE_ATTRIBUTE_REPARSE_POINT
GROK_BASIS = "caller listing at now; not an observation, never ranked"
PREREQUISITES = (
    "a proven running-profile source per lane (the executor's launched block); until then profile_unproven",
    "a lane-attested subject source (none for Codex; for Claude the handshake plus the proven session_id premise)",
    "compose binding a row to the lane's provider before any subject is bound: it matches a subject against rows "
    "of ANY provider (RCO2 V3, reproduced 20:21:21Z)",
    "a collector binder, so capacity rows can reach verified_binding",
    "persisted, verified pool-binding decisions bound to a worker, before any decision is read",
    "the reservation input (I3) is not read here; it is W3's",
)


def _strict_object(pairs: list) -> dict:
    keys = [key for key, _ in pairs]
    if len(set(keys)) != len(keys):
        raise ValueError("duplicate key")
    return dict(pairs)


def _non_finite(token: str) -> Any:
    raise ValueError("non-finite number")


def _finite_float(token: str) -> float:
    """A JSON number as a float, refusing one that overflows to infinity (1e400): NaN's twin by another route."""
    value = float(token)
    if not math.isfinite(value):
        raise ValueError("non-finite number")
    return value


def _read(path: Any) -> tuple[Any, dict | None, str | None]:
    """(document, read record, refusal kind) for one caller path, read once."""
    if type(path) is not str or not 0 < len(path) <= MAX_PATH_TEXT or "\x00" in path:
        return None, None, "path_invalid"
    try:
        before = os.lstat(path)
    except (OSError, ValueError):
        return None, None, "missing_or_unreadable"
    if not stat.S_ISREG(before.st_mode) or getattr(before, "st_file_attributes", 0) & REPARSE_POINT:
        return None, None, "not_a_regular_file"
    if before.st_size > MAX_BYTES:
        return None, None, "oversized"
    try:
        with open(path, "rb") as handle:
            opened = os.fstat(handle.fileno())
            if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                return None, None, "path_changed"
            data = handle.read(MAX_BYTES + 1)
    except (OSError, ValueError):
        return None, None, "missing_or_unreadable"
    if len(data) > MAX_BYTES:
        return None, None, "oversized"
    try:
        text = data.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return None, None, "not_utf8"
    try:
        document = json.loads(text, object_pairs_hook=_strict_object, parse_constant=_non_finite,
                              parse_float=_finite_float)
    except (ValueError, RecursionError):
        return None, None, "not_strict_json"
    return document, {"byte_sha256": hashlib.sha256(data).hexdigest(), "size": len(data),
                      "document_digest": digest(document)}, None


def _lane_report(worker: Any, path: Any, now: Any) -> dict:
    """What one lane checkpoint proves: at most that it is fresh. Never a profile or a subject."""
    if type(worker) is not str or worker not in MEMBERS:
        return {"worker": worker if type(worker) is str and len(worker) <= 64 else None, "reasons": ["foreign_worker"]}
    document, _, refusal = _read(path)
    reasons: list[str] = []
    fresh = False
    if refusal is not None:
        reasons.append("lane_unreadable:" + refusal)
    elif type(document) is not dict or document.get("schema") != CHECKPOINT_SCHEMA:
        reasons.append("checkpoint_malformed")
    elif document.get("agent") != worker:        # exact: the writer's -Agent ValidateSet ignores case
        reasons.append("checkpoint_agent_mismatch")
    else:
        observed = _instant(document.get("updated_at_utc"))
        if observed is None:
            reasons.append("checkpoint_malformed")
        elif now is None:
            reasons.append("evidence_age_unknown")
        elif observed > now:
            reasons.append("lane_evidence_future")
        elif now - observed > timedelta(seconds=MAX_LANE_EVIDENCE_AGE_SECONDS):
            reasons.append("lane_evidence_stale")
        else:
            fresh = True
    return {"worker": worker, "checkpoint_fresh": fresh, "reasons": reasons + ["profile_unproven", "subject_unbound"]}


def read_routing_inputs(paths: Any, now: Any, *, grok_profile_id: Any = None) -> dict:
    """Advice from caller-listed files only; see the module docstring. Never raises for bad input."""
    reasons: list[str] = []
    current = _aware_utc(now)
    if current is None:
        reasons.append("now_invalid")
    if type(paths) is not dict:
        reasons.append("paths_malformed")
        paths = {}
    if not set(paths) <= set(DOCUMENT_PATHS) | {"lanes"}:
        reasons.append("path_key_unsupported")      # named, never opened
    documents: dict[str, Any] = {}
    reads: dict[str, Any] = {}
    sources: dict[str, dict] = {}
    for name in DOCUMENT_PATHS:
        if name in OPTIONAL_PATHS and paths.get(name) is None:
            documents[name], reads[name] = None, None
            continue
        documents[name], record, refusal = _read(paths.get(name))
        reads[name] = record if refusal is None else {"byte_sha256": None, "size": None, "document_digest": None,
                                                      "refusal": refusal}
        if refusal is not None:
            reasons.append(f"{name}_unreadable:{refusal}")
        else:
            sources[name] = {"path": paths[name], "byte_sha256": record["byte_sha256"]}
    status = documents.pop("capacity_status")
    rows = None
    if "capacity_status" in sources:
        if type(status) is dict and status.get("schema") == STATUS_SCHEMA and status.get("execution_allowed") is False \
                and type(status.get("observations")) is list:
            rows = status["observations"]           # passed through unchanged
            sources["rows"] = sources["capacity_status"]
        else:
            reasons.append("capacity_status_malformed")
    sources.pop("capacity_status", None)
    lanes = paths.get("lanes", {})
    if type(lanes) is not dict:
        reasons.append("lanes_malformed")
        lanes = {}
    reports = [_lane_report(worker, lanes[worker], current) for worker in sorted(lanes, key=str)]
    records: list[dict] = []
    grok: dict[str, Any] = {"listed": False}
    if grok_profile_id is not None:
        if not _text(grok_profile_id, MAX_PROFILE_TEXT):
            reasons.append("grok_profile_malformed")
        elif current is not None:           # without an aware now there is no instant to list Grok at
            records.append({"schema": LANE_EVIDENCE_SCHEMA, "worker": GROK, "kind": GROK,
                            "profile_id": grok_profile_id, "subject": None, "observed_utc": current.isoformat()})
            grok = {"listed": True, "ranked": False, "subject": None, "basis": GROK_BASIS}
    assembled = assemble(documents["task"], records, rows, documents["paced"], documents["prepared_artifacts"],
                         documents["routing_policy"], now, signed_policy=documents["signed_policy"],
                         shadow_weights=None, sources=sources)
    composed = compose(**assembled["inputs"], now=now)
    return {"schema": SCHEMA, "authority": "none", "execution_allowed": False,
            "now_utc": current.isoformat() if current is not None else None, "reads": reads, "lanes": reports,
            "grok": grok, "reasons": reasons, "prerequisites": list(PREREQUISITES), "assembled": assembled,
            "composed": composed}

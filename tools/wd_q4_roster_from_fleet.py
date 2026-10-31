"""Default-off deterministic producer of the existing ``wd.q4-roster-snapshot.v1`` from caller-supplied fleet bytes.

The Q4 wrapper (``Get-WdQ4RosterPairs -Snapshot``) and the shared index builder (``strict_pins``) VALIDATE a roster
snapshot; nothing in the tree PRODUCED one until now (the only producers were test fixtures). This module turns the
bytes of a fleet document into the snapshot those validators accept, and nothing else:

* input: ``fleet_bytes`` (``bytes``, at most ``FLEET_BUDGET``) plus an independently supplied ``expected_fleet_sha256``;
  the digest is checked before any byte is parsed. A matching digest is integrity checking of what the caller
  supplied, not authentication of its origin: this module never reads the real fleet file, an identity store, the
  environment, the filesystem or the bridge. It has no I/O at all.
* output: canonical bytes (``json.dumps(..., sort_keys=True, separators=(",", ":"))``, ASCII, no trailing newline) of
  ``{"schema": SCHEMA, "lanes": [...], "tools_supervisor": {...}}`` and their sha256. ``lanes`` carries the four
  projection lanes sorted by agent name; ``tools_supervisor`` carries ``codex-tools-1``. Each entry carries exactly
  ``agent`` and ``agent_uuid``, plus ``role`` when (and only when) the fleet entry has a string role matching ``ROLE``.
  Every other fleet field (worktree, prompt, branch, head, model, cli, q4_read, ...) is dropped and never reaches the
  output bytes. Identical fleet content in any key or list order yields identical bytes.
* refusals (``RosterProducerRefused``, one closed token, never carrying fleet content): ``disabled`` (``enabled`` is not
  exactly ``True``), ``fleet_bytes_invalid``, ``fleet_over_budget``, ``fleet_pin_invalid``, ``fleet_pin_mismatch``,
  ``fleet_malformed`` (undecodable, non-object, duplicate JSON key, missing or mistyped ``lanes`` /
  ``tools_supervisor`` / ``agent`` / ``agent_uuid`` / ``role``, uuid not lowercase RFC-4122 shaped, nesting too deep),
  ``fleet_unknown_lane`` (an entry whose agent is outside ``LANES``: unlike the projection reader, which ignores such
  lanes, a producer refuses them so a sixth lane can never be silently dropped from a pinned roster),
  ``fleet_duplicate`` (an agent or a uuid listed twice across ``lanes`` and ``tools_supervisor``), and
  ``fleet_lane_set_mismatch`` (``lanes`` is not exactly the four projection lanes or ``tools_supervisor`` is not
  ``codex-tools-1``). Entries are validated in document order; the token names the first failure.

Validation is deliberately the INTERSECTION of what the wrapper and the builder accept: the wrapper requires entry keys
``agent,agent_uuid`` or ``agent,agent_uuid,role`` (string role), five distinct known agents with distinct lowercase
uuids; the builder requires entry keys within ``{agent, agent_uuid, role}`` and the Lead ``roster_from_fleet`` reader to
yield the whole lane set. The committed fleet shape (lanes with a role, a supervisor without one) satisfies both.

Not claimed: no authority, identity authentication, freshness or policy semantics change; the snapshot is as true as
the fleet bytes the caller pinned. Nothing here is wired, deployed or installed.
"""
import hashlib
import hmac
import json
import math
import re

__all__ = ["RosterProducerRefused", "build_roster_snapshot", "SCHEMA", "LANES", "TOOLS_SUPERVISOR", "FLEET_BUDGET"]

SCHEMA = "wd.q4-roster-snapshot.v1"
LANES = frozenset({"codex-lead-1", "codex-tools-1", "claude-rco-1", "claude-rco-2", "fable-5"})
TOOLS_SUPERVISOR = "codex-tools-1"
PROJECTION_LANES = LANES - {TOOLS_SUPERVISOR}
FLEET_BUDGET = 1 << 20  # == shared_projection.ROSTER_BUDGET: the consumers read the pinned roster through this budget
HEX64 = re.compile(r"[0-9a-f]{64}")
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")  # == shared_projection.UUID
ROLE = re.compile(r"[a-z][a-z0-9-]{0,39}")


class RosterProducerRefused(ValueError):
    """One closed refusal token; the message never carries fleet content."""


def _pairs_without_duplicates(pairs):
    document = {}
    for key, value in pairs:
        if key in document:
            raise RosterProducerRefused("fleet_malformed")
        document[key] = value
    return document


def _reject_nonstandard_constant(_value):
    raise ValueError("nonstandard_json_constant")


def _finite_float(value):
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError("nonfinite_json_number")
    return parsed


def _parse(fleet_bytes):
    try:
        document = json.loads(fleet_bytes.decode("utf-8", "strict"), object_pairs_hook=_pairs_without_duplicates,
                              parse_constant=_reject_nonstandard_constant, parse_float=_finite_float)
    except (ValueError, RecursionError):
        pass
    else:
        return document
    # Raise after leaving the exception handler: JSONDecodeError.doc and
    # UnicodeDecodeError.object must not be retained in __context__.
    raise RosterProducerRefused("fleet_malformed")


def _entry(value):
    """The projected entry of one fleet lane: exactly ``agent`` and ``agent_uuid``, plus a string ``role`` if present."""
    if not isinstance(value, dict) or not isinstance(value.get("agent"), str):
        raise RosterProducerRefused("fleet_malformed")
    if value["agent"] not in LANES:
        raise RosterProducerRefused("fleet_unknown_lane")
    uuid = value.get("agent_uuid")
    if not isinstance(uuid, str) or UUID.fullmatch(uuid) is None:
        raise RosterProducerRefused("fleet_malformed")
    entry = {"agent": value["agent"], "agent_uuid": uuid}
    if "role" in value:
        role = value["role"]
        if not isinstance(role, str) or ROLE.fullmatch(role) is None:
            raise RosterProducerRefused("fleet_malformed")
        entry["role"] = role
    return entry


def build_roster_snapshot(fleet_bytes, expected_fleet_sha256, *, enabled=False):
    """``(snapshot_bytes, snapshot_sha256)`` for the fleet document ``fleet_bytes`` whose sha256 the caller pinned
    independently as ``expected_fleet_sha256``; refuses ``disabled`` unless ``enabled is True``. See the module doc."""
    if enabled is not True:
        raise RosterProducerRefused("disabled")
    if type(fleet_bytes) is not bytes:
        raise RosterProducerRefused("fleet_bytes_invalid")
    if len(fleet_bytes) > FLEET_BUDGET:
        raise RosterProducerRefused("fleet_over_budget")
    if not isinstance(expected_fleet_sha256, str) or HEX64.fullmatch(expected_fleet_sha256) is None:
        raise RosterProducerRefused("fleet_pin_invalid")
    if not hmac.compare_digest(hashlib.sha256(fleet_bytes).hexdigest(), expected_fleet_sha256):
        raise RosterProducerRefused("fleet_pin_mismatch")
    document = _parse(fleet_bytes)
    if (not isinstance(document, dict) or not isinstance(document.get("lanes"), list)
            or not isinstance(document.get("tools_supervisor"), dict)):
        raise RosterProducerRefused("fleet_malformed")
    entries = [_entry(value) for value in document["lanes"]]
    supervisor = _entry(document["tools_supervisor"])
    everyone = entries + [supervisor]
    if (len({entry["agent"] for entry in everyone}) != len(everyone)
            or len({entry["agent_uuid"] for entry in everyone}) != len(everyone)):
        raise RosterProducerRefused("fleet_duplicate")
    if {entry["agent"] for entry in entries} != PROJECTION_LANES or supervisor["agent"] != TOOLS_SUPERVISOR:
        raise RosterProducerRefused("fleet_lane_set_mismatch")
    snapshot = {"schema": SCHEMA, "lanes": sorted(entries, key=lambda entry: entry["agent"]),
                "tools_supervisor": supervisor}
    raw = json.dumps(snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    return raw, hashlib.sha256(raw).hexdigest()

# SPDX-License-Identifier: BUSL-1.1
"""Bridge agent identity registry helpers.

The bridge event stream carries both a human-readable ``agent`` id and an
``agent_uuid``. Gate-critical readers must bind those two fields together:
an event that claims a registered identity with a missing or different UUID
is ignored for gate purposes.
"""

from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any, Mapping
import uuid

AGENT_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{1,32}$")
AGENT_UUID_PATTERN = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
DEFAULT_BRIDGE_IDENTITY_REGISTRY_PATH = (
    Path(__file__).resolve().parents[1] / "configs" / "bridge_identity_registry.json"
)


def load_bridge_identity_registry(
    path: str | Path | None = None,
    *,
    allow_missing: bool = False,
) -> dict[str, str]:
    """Load and validate the operator-maintained identity registry.

    Gate-critical callers fail closed when the registry is missing. Historical
    or offline callers that intentionally run without a registry must opt in
    with ``allow_missing=True``. A present but malformed registry also fails
    closed with ``ValueError``.
    """
    registry_path = Path(path) if path is not None else DEFAULT_BRIDGE_IDENTITY_REGISTRY_PATH
    if not registry_path.exists():
        if allow_missing:
            return {}
        raise ValueError(f"{registry_path}: bridge identity registry not found")
    try:
        payload = json.loads(registry_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{registry_path}: invalid JSON: {exc.msg}") from exc
    if not isinstance(payload, Mapping):
        raise ValueError(f"{registry_path}: expected JSON object")
    identities = payload.get("identities")
    if not isinstance(identities, Mapping):
        raise ValueError(f"{registry_path}: expected identities object")
    registry: dict[str, str] = {}
    for agent, agent_uuid in identities.items():
        if not isinstance(agent, str) or not AGENT_ID_PATTERN.fullmatch(agent):
            raise ValueError(f"{registry_path}: invalid agent id: {agent!r}")
        if not isinstance(agent_uuid, str) or not AGENT_UUID_PATTERN.fullmatch(agent_uuid):
            raise ValueError(f"{registry_path}: invalid agent_uuid for {agent}")
        registry[agent] = agent_uuid
    return registry


def _canonical_uuid_text(value: object) -> str:
    """One spelling per UUID value: braces, ``urn:uuid:``, missing hyphens, case
    and surrounding whitespace all map to the canonical lowercase form. Text that
    is not a UUID is compared case-insensitively as written."""
    text = str(value).strip()
    try:
        return str(uuid.UUID(text))
    except ValueError:
        return text.casefold()


def _registered_owner_of_uuid(registry: Mapping[str, str], event_uuid: str) -> str | None:
    """Return the registered agent that owns ``event_uuid`` in any spelling, if any."""
    if not event_uuid.strip():
        return None
    wanted = _canonical_uuid_text(event_uuid)
    for owner, registered_uuid in registry.items():
        if _canonical_uuid_text(registered_uuid) == wanted:
            return str(owner)
    return None


def bridge_identity_binding_status(
    event: Mapping[str, Any],
    *,
    registry: Mapping[str, str],
    restricted_agents: set[str] | frozenset[str] | None = None,
) -> str:
    """Return ``valid``, ``unregistered``, ``missing_uuid``, or ``mismatch_uuid``.

    The status is deliberately unchanged for a name that is not registered (or not
    watched): gate readers drop ``missing_uuid``/``mismatch_uuid`` events, so
    reclassifying such a name would hide its blocks. Acceptance of a borrowed
    registered UUID is refused in ``event_matches_registered_identity`` instead.
    """
    agent = str(event.get("agent", ""))
    if restricted_agents is not None and agent not in restricted_agents:
        return "unregistered"
    expected_uuid = registry.get(agent)
    if not expected_uuid:
        return "unregistered"
    event_uuid = str(event.get("agent_uuid", "") or "")
    if not event_uuid:
        return "missing_uuid"
    if event_uuid != expected_uuid:
        return "mismatch_uuid"
    return "valid"


def event_matches_registered_identity(
    event: Mapping[str, Any],
    *,
    registry: Mapping[str, str],
    restricted_agents: set[str] | frozenset[str] | None = None,
) -> bool:
    """True when the event matches its registered UUID, or is unregistered and does
    not borrow a UUID that is registered to a different agent (reverse alias, in
    any UUID spelling)."""
    status = bridge_identity_binding_status(
        event,
        registry=registry,
        restricted_agents=restricted_agents,
    )
    if status == "valid":
        return True
    if status != "unregistered":
        return False
    uuid_owner = _registered_owner_of_uuid(registry, str(event.get("agent_uuid", "") or ""))
    return uuid_owner is None or uuid_owner == str(event.get("agent", ""))


__all__ = [
    "DEFAULT_BRIDGE_IDENTITY_REGISTRY_PATH",
    "bridge_identity_binding_status",
    "event_matches_registered_identity",
    "load_bridge_identity_registry",
]

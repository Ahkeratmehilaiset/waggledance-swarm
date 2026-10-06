"""A registered agent_uuid can never be borrowed through the generic identity matcher.

Before this fix ``event_matches_registered_identity`` accepted an event whose
agent name is not registered (or not watched) but whose ``agent_uuid`` belongs to
another registered agent. The matcher now refuses that reverse alias in any UUID
spelling. ``bridge_identity_binding_status`` is deliberately unchanged: gate
readers drop ``missing_uuid``/``mismatch_uuid`` events, so reclassifying such a
name would hide its blocks (RCO1 F1 on #1772 @4e55b77f).
"""

from __future__ import annotations

import importlib

import pytest

OWNER_UUID = "11111111-1111-4111-8111-111111111111"
OTHER_UUID = "33333333-3333-4333-8333-333333333333"
FOREIGN_UUID = "22222222-2222-4222-8222-222222222222"
REGISTRY = {"fixture-owner": OWNER_UUID, "fixture-other": OTHER_UUID}
MODULES = ("waggledance.core.bridge_identity_registry", "tools.bridge_v2_identity_registry")
RESTRICTIONS = {
    "default": None,
    "all_registered": frozenset(REGISTRY),
    "owner_only": frozenset({"fixture-owner"}),
}
ALIAS = {r: ("unregistered", False) for r in RESTRICTIONS}


def _event(agent: str, agent_uuid: object = None) -> dict:
    event = {"type": "claim", "agent": agent, "task_id": agent + "/fixture"}
    if agent_uuid is not None:
        event["agent_uuid"] = agent_uuid
    return event


# (case, agent, uuid, {restriction: (status, accepted)})
CASES = [
    ("registered_positive", "fixture-owner", OWNER_UUID, {r: ("valid", True) for r in RESTRICTIONS}),
    ("registered_foreign_uuid", "fixture-owner", FOREIGN_UUID, {r: ("mismatch_uuid", False) for r in RESTRICTIONS}),
    ("registered_missing_uuid", "fixture-owner", "", {r: ("missing_uuid", False) for r in RESTRICTIONS}),
    ("registered_owner_noncanonical_spelling", "fixture-owner", "{" + OWNER_UUID + "}",
     {r: ("mismatch_uuid", False) for r in RESTRICTIONS}),
    ("registered_name_with_other_registered_uuid", "fixture-owner", OTHER_UUID,
     {r: ("mismatch_uuid", False) for r in RESTRICTIONS}),
    ("reverse_alias_unregistered_name", "fixture-alias", OWNER_UUID, ALIAS),
    ("reverse_alias_uppercase_uuid", "fixture-alias", OWNER_UUID.upper(), ALIAS),
    ("reverse_alias_braced_uuid", "fixture-alias", "{" + OWNER_UUID + "}", ALIAS),
    ("reverse_alias_urn_uuid", "fixture-alias", "urn:uuid:" + OWNER_UUID, ALIAS),
    ("reverse_alias_hyphenless_uuid", "fixture-alias", OWNER_UUID.replace("-", ""), ALIAS),
    ("reverse_alias_padded_uuid", "fixture-alias", "  " + OWNER_UUID + "\t", ALIAS),
    ("unwatched_registered_agent_borrowing_watched_uuid", "fixture-other", OWNER_UUID,
     {"default": ("mismatch_uuid", False), "all_registered": ("mismatch_uuid", False),
      "owner_only": ("unregistered", False)}),
    ("unwatched_registered_agent_own_uuid", "fixture-other", OTHER_UUID,
     {"default": ("valid", True), "all_registered": ("valid", True), "owner_only": ("unregistered", True)}),
    ("unregistered_name_foreign_uuid_compat", "fixture-alias", FOREIGN_UUID, {r: ("unregistered", True) for r in RESTRICTIONS}),
    ("unregistered_name_without_uuid_compat", "fixture-alias", None, {r: ("unregistered", True) for r in RESTRICTIONS}),
    ("unregistered_name_empty_uuid_compat", "fixture-alias", "", {r: ("unregistered", True) for r in RESTRICTIONS}),
    ("unregistered_name_whitespace_uuid_compat", "fixture-alias", "   ", {r: ("unregistered", True) for r in RESTRICTIONS}),
]


@pytest.mark.parametrize("module_name", MODULES)
@pytest.mark.parametrize("case, agent, agent_uuid, expected", CASES, ids=[c[0] for c in CASES])
@pytest.mark.parametrize("restriction", sorted(RESTRICTIONS))
def test_matcher_refuses_reverse_uuid_alias(module_name, case, agent, agent_uuid, expected, restriction):
    module = importlib.import_module(module_name)
    kwargs = {"registry": REGISTRY, "restricted_agents": RESTRICTIONS[restriction]}
    event = _event(agent, agent_uuid)
    status, accepted = expected[restriction]
    assert module.bridge_identity_binding_status(event, **kwargs) == status, case
    assert module.event_matches_registered_identity(event, **kwargs) is accepted, case


@pytest.mark.parametrize("case, agent, agent_uuid, expected", CASES, ids=[c[0] for c in CASES])
@pytest.mark.parametrize("restriction", sorted(RESTRICTIONS))
def test_core_and_port_stay_in_parity(case, agent, agent_uuid, expected, restriction):
    core, port = (importlib.import_module(name) for name in MODULES)
    kwargs = {"registry": REGISTRY, "restricted_agents": RESTRICTIONS[restriction]}
    event = _event(agent, agent_uuid)
    assert core.bridge_identity_binding_status(event, **kwargs) == port.bridge_identity_binding_status(event, **kwargs)
    assert core.event_matches_registered_identity(event, **kwargs) == \
        port.event_matches_registered_identity(event, **kwargs)


# --- RCO1 F1 twins: a block from a non-RCO name carrying a registered UUID must still hold the peer gate.

LEAD_UUID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
GATE_REGISTRY = {
    "codex-lead-1": LEAD_UUID,
    "codex-tools-1": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
    "claude-rco-1": "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
    "claude-rco-2": "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
}
TASK = "fixture/peer-gate-reverse-alias"


@pytest.mark.parametrize("agent_uuid", [LEAD_UUID, "{" + LEAD_UUID.upper() + "}", FOREIGN_UUID])
@pytest.mark.parametrize("event_type, status", [("decision", "changes_requested"), ("blocked", "blocked")])
def test_block_from_unregistered_name_with_registered_uuid_still_holds_peer_gate(agent_uuid, event_type, status):
    gate = importlib.import_module("tools.check_bridge_changes_requested")
    events = [{
        "ts_utc": "2026-10-06T16:00:00Z", "agent": "operator", "agent_uuid": agent_uuid,
        "type": event_type, "status": status, "task_id": TASK, "message": "hold this merge",
    }]
    result = gate.check_bridge_clear_to_merge(
        events=events, task_id=TASK, merging_agent="codex-lead-1", identity_registry=GATE_REGISTRY,
    )
    assert result["clear_to_merge"] is False, result


def test_unregistered_alias_keeps_the_status_gate_readers_process():
    for module_name in MODULES:
        module = importlib.import_module(module_name)
        event = _event("operator", OWNER_UUID)
        assert module.bridge_identity_binding_status(event, registry=REGISTRY) == "unregistered"
        assert module.event_matches_registered_identity(event, registry=REGISTRY) is False

"""A registered agent_uuid can never be borrowed by another agent name.

Before this fix an unregistered (or unwatched) agent name that reused a registered
UUID returned ``unregistered`` and was accepted by
``event_matches_registered_identity``. The reverse binding check now returns
``mismatch_uuid`` first, in the core helper and in its Bridge v2 port alike; the
gate consumers already treat ``mismatch_uuid`` as an identity failure.
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


def _event(agent: str, agent_uuid: object = None) -> dict:
    event = {"type": "claim", "agent": agent, "task_id": agent + "/fixture"}
    if agent_uuid is not None:
        event["agent_uuid"] = agent_uuid
    return event


# (case, agent, uuid, {restriction: (status, accepted)})
CASES = [
    ("registered_positive", "fixture-owner", OWNER_UUID,
     {"default": ("valid", True), "all_registered": ("valid", True), "owner_only": ("valid", True)}),
    ("registered_foreign_uuid", "fixture-owner", FOREIGN_UUID,
     {r: ("mismatch_uuid", False) for r in RESTRICTIONS}),
    ("registered_missing_uuid", "fixture-owner", "",
     {r: ("missing_uuid", False) for r in RESTRICTIONS}),
    ("reverse_alias_unregistered_name", "fixture-alias", OWNER_UUID,
     {r: ("mismatch_uuid", False) for r in RESTRICTIONS}),
    ("reverse_alias_uppercase_uuid", "fixture-alias", OWNER_UUID.upper(),
     {r: ("mismatch_uuid", False) for r in RESTRICTIONS}),
    ("reverse_alias_braced_uuid", "fixture-alias", "{" + OWNER_UUID + "}",
     {r: ("mismatch_uuid", False) for r in RESTRICTIONS}),
    ("reverse_alias_urn_uuid", "fixture-alias", "urn:uuid:" + OWNER_UUID,
     {r: ("mismatch_uuid", False) for r in RESTRICTIONS}),
    ("reverse_alias_hyphenless_uuid", "fixture-alias", OWNER_UUID.replace("-", ""),
     {r: ("mismatch_uuid", False) for r in RESTRICTIONS}),
    ("reverse_alias_padded_uuid", "fixture-alias", "  " + OWNER_UUID + "\t",
     {r: ("mismatch_uuid", False) for r in RESTRICTIONS}),
    # The owner's own forward check stays exact: a non-canonical spelling is still a mismatch.
    ("registered_owner_noncanonical_spelling", "fixture-owner", "{" + OWNER_UUID + "}",
     {r: ("mismatch_uuid", False) for r in RESTRICTIONS}),
    ("registered_name_with_other_registered_uuid", "fixture-owner", OTHER_UUID,
     {r: ("mismatch_uuid", False) for r in RESTRICTIONS}),
    ("unwatched_registered_agent_borrowing_watched_uuid", "fixture-other", OWNER_UUID,
     {r: ("mismatch_uuid", False) for r in RESTRICTIONS}),
    ("unwatched_registered_agent_own_uuid", "fixture-other", OTHER_UUID,
     {"default": ("valid", True), "all_registered": ("valid", True), "owner_only": ("unregistered", True)}),
    ("unregistered_name_foreign_uuid_compat", "fixture-alias", FOREIGN_UUID,
     {r: ("unregistered", True) for r in RESTRICTIONS}),
    ("unregistered_name_without_uuid_compat", "fixture-alias", None,
     {r: ("unregistered", True) for r in RESTRICTIONS}),
    ("unregistered_name_empty_uuid_compat", "fixture-alias", "",
     {r: ("unregistered", True) for r in RESTRICTIONS}),
]


@pytest.mark.parametrize("module_name", MODULES)
@pytest.mark.parametrize("case, agent, agent_uuid, expected", CASES, ids=[c[0] for c in CASES])
@pytest.mark.parametrize("restriction", sorted(RESTRICTIONS))
def test_binding_status_rejects_reverse_uuid_alias(module_name, case, agent, agent_uuid, expected, restriction):
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


def test_reverse_alias_is_reported_in_the_status_set_gate_consumers_reject():
    # check_bridge_changes_requested and check_rco_pass_present ignore events whose
    # status is in {"missing_uuid", "mismatch_uuid"}; a new status name would slip past them.
    for module_name in MODULES:
        module = importlib.import_module(module_name)
        status = module.bridge_identity_binding_status(_event("fixture-alias", OWNER_UUID), registry=REGISTRY)
        assert status in {"missing_uuid", "mismatch_uuid"}

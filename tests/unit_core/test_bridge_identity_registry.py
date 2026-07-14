# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

import json
from pathlib import Path

import pytest

from waggledance.core.bridge_identity_registry import (
    bridge_identity_binding_status,
    event_matches_registered_identity,
    load_bridge_identity_registry,
)


REGISTERED_UUID = "11111111-2222-3333-4444-555555555555"


def test_unregistered_agent_cannot_reuse_registered_uuid() -> None:
    registry = {"codex-tools-1": REGISTERED_UUID}
    event = {"agent": "alias-probe", "agent_uuid": REGISTERED_UUID.upper()}

    assert bridge_identity_binding_status(event, registry=registry) == "uuid_alias"
    assert not event_matches_registered_identity(event, registry=registry)


def test_restricted_reader_still_rejects_registered_uuid_alias() -> None:
    registry = {"codex-tools-1": REGISTERED_UUID}
    event = {"agent": "alias-probe", "agent_uuid": REGISTERED_UUID}

    assert bridge_identity_binding_status(
        event,
        registry=registry,
        restricted_agents={"codex-tools-1"},
    ) == "uuid_alias"


def test_unregistered_agent_with_unowned_uuid_remains_unregistered() -> None:
    status = bridge_identity_binding_status(
        {
            "agent": "legacy-agent",
            "agent_uuid": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        },
        registry={"codex-tools-1": REGISTERED_UUID},
    )

    assert status == "unregistered"


def test_registry_rejects_duplicate_uuid_owners(tmp_path: Path) -> None:
    registry_path = tmp_path / "bridge_identity_registry.json"
    registry_path.write_text(
        json.dumps(
            {
                "identities": {
                    "codex-tools-1": REGISTERED_UUID,
                    "alias-probe": REGISTERED_UUID.upper(),
                }
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="agent_uuid reused by"):
        load_bridge_identity_registry(registry_path)

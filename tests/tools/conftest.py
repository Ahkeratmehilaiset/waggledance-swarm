# SPDX-License-Identifier: BUSL-1.1
"""Test isolation for bridge command-line tools."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_bound_bridge_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    """Do not reuse the developer's live bridge identity as fixture state.

    Tests that exercise the binding contract set AGENT_BRIDGE_AGENT explicitly
    after this fixture has cleared the inherited process value.
    """

    for name in (
        "AGENT_BRIDGE_AGENT",
        "AGENT_BRIDGE_RUN_ID",
        "AGENT_BRIDGE_SESSION_ID",
        "AGENT_BRIDGE_ROLE",
        "AGENT_BRIDGE_AGENT_UUID",
        "AGENT_BRIDGE_CAPABILITIES",
    ):
        monkeypatch.delenv(name, raising=False)

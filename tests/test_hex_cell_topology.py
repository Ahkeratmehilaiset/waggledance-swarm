# SPDX-License-Identifier: BUSL-1.1
"""Dependency-free tests for the solver-retrieval hex-cell topology."""

from waggledance.core.hex_cell_topology import (
    CELL_GENERAL,
    CELL_SAFETY,
    HexCellTopology,
)


def test_finnish_smoke_alarm_routes_safety_without_generic_beep_overreach():
    topology = HexCellTopology()

    alarm = topology.assign_cell("chat", "palovaroitin piippaa")
    assert alarm.cell_id == CELL_SAFETY
    assert alarm.method == "keyword"

    generic_beep = topology.assign_cell("chat", "jokin piippaa")
    assert generic_beep.cell_id == CELL_GENERAL
    assert generic_beep.cell_id != CELL_SAFETY

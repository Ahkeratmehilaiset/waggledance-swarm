# SPDX-License-Identifier: BUSL-1.1
"""Unit coverage for the ADR-038 in-memory tunnel registry."""
from __future__ import annotations

from dataclasses import FrozenInstanceError, is_dataclass
from pathlib import Path

import pytest
import yaml

from waggledance.core.reasoning.tunnel_registry import (
    Tunnel,
    TunnelRegistry,
    TunnelRegistryError,
    load_tunnel_policy,
    load_tunnel_registry_from_yaml,
)


def _tunnel(**overrides):
    base = {
        "tunnel_id": "tun.home.thermal",
        "from_cell": "home_comfort",
        "to_solver": "thermal_solver",
        "trust_score": 0.91,
        "provenance_event_id": "magma:event:cofire:001",
        "added_at_utc": "2026-06-01T00:00:00Z",
        "last_validated_utc": "2026-06-10T00:00:00Z",
        "direction": "forward",
    }
    base.update(overrides)
    return base


def test_policy_defaults_are_loaded_from_contract() -> None:
    policy = load_tunnel_policy()
    assert policy.min_trust_score == 0.70
    assert policy.archive_after_days_stale == 90
    assert policy.direction_enum == ("forward", "negative")
    assert policy.yaml_path == "configs/tunnel_overlay.yaml"


def test_tunnel_record_is_frozen_slots_dataclass() -> None:
    tunnel = Tunnel(**_tunnel())
    assert is_dataclass(Tunnel)
    assert hasattr(Tunnel, "__slots__")
    with pytest.raises(FrozenInstanceError):
        tunnel.trust_score = 0.5  # type: ignore[misc]


def test_registry_rejects_below_threshold_and_missing_provenance() -> None:
    with pytest.raises(TunnelRegistryError, match="below policy"):
        TunnelRegistry([_tunnel(trust_score=0.69)])

    with pytest.raises(TunnelRegistryError, match="provenance_event_id"):
        TunnelRegistry([_tunnel(provenance_event_id="")])


def test_lookup_uses_active_from_cell_index_and_sorts_by_trust() -> None:
    registry = TunnelRegistry(
        [
            _tunnel(tunnel_id="tun.home.thermal.low", trust_score=0.74),
            _tunnel(tunnel_id="tun.home.thermal.high", trust_score=0.99),
            _tunnel(
                tunnel_id="tun.garden.irrigation",
                from_cell="garden",
                to_solver="irrigation_solver",
            ),
            _tunnel(tunnel_id="tun.home.archived", active=False),
        ]
    )

    result = registry.lookup_tunnels("home_comfort")

    assert [item.tunnel_id for item in result] == [
        "tun.home.thermal.high",
        "tun.home.thermal.low",
    ]
    assert registry.lookup_tunnels("missing") == ()


def test_stale_tunnels_auto_archive_and_stay_auditable() -> None:
    registry = TunnelRegistry(
        [
            _tunnel(
                tunnel_id="tun.stale",
                last_validated_utc="2026-01-01T00:00:00Z",
            )
        ],
        now_utc="2026-06-15T00:00:00Z",
    )

    assert registry.lookup_tunnels("home_comfort") == ()
    assert registry.all_tunnels[0].active is False


def test_negative_tunnels_share_registry_and_can_be_direction_filtered() -> None:
    registry = TunnelRegistry(
        [
            _tunnel(tunnel_id="tun.forward", direction="forward"),
            _tunnel(
                tunnel_id="tun.negative",
                to_solver="unsafe_solver",
                direction="negative",
            ),
        ]
    )

    assert [item.tunnel_id for item in registry.lookup_tunnels("home_comfort")] == [
        "tun.forward",
        "tun.negative",
    ]
    assert [
        item.tunnel_id
        for item in registry.lookup_tunnels("home_comfort", direction="negative")
    ] == ["tun.negative"]


def test_duplicate_tunnel_ids_fail_closed() -> None:
    with pytest.raises(TunnelRegistryError, match="duplicate tunnel_id"):
        TunnelRegistry([_tunnel(), _tunnel()])


def test_yaml_loader_accepts_operator_editable_tunnels(tmp_path: Path) -> None:
    path = tmp_path / "tunnel_overlay.yaml"
    path.write_text(yaml.safe_dump({"tunnels": [_tunnel()]}), encoding="utf-8")

    registry = load_tunnel_registry_from_yaml(path)

    assert [item.tunnel_id for item in registry.lookup_tunnels("home_comfort")] == [
        "tun.home.thermal"
    ]


def test_yaml_loader_rejects_non_list_tunnels(tmp_path: Path) -> None:
    path = tmp_path / "tunnel_overlay.yaml"
    path.write_text("tunnels: not-a-list\n", encoding="utf-8")

    with pytest.raises(TunnelRegistryError, match="tunnels must be a list"):
        load_tunnel_registry_from_yaml(path)


def test_yaml_loader_rejects_non_boolean_active_flag(tmp_path: Path) -> None:
    path = tmp_path / "tunnel_overlay.yaml"
    path.write_text(
        yaml.safe_dump({"tunnels": [_tunnel(active="false")]}),
        encoding="utf-8",
    )

    with pytest.raises(TunnelRegistryError, match="active must be boolean"):
        load_tunnel_registry_from_yaml(path)

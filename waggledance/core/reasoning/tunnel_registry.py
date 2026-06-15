# SPDX-License-Identifier: BUSL-1.1
"""In-memory Tunnel overlay registry substrate.

This module implements the ADR-038 registry shape only. It does not wire
tunnels into live routing, call solvers, skip gates, or write MAGMA.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import json
import math
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[3]
CONTRACT_PATH = ROOT / "docs" / "eig2" / "contracts" / "tunnel_overlay.json"
DEFAULT_YAML_PATH = ROOT / "configs" / "tunnel_overlay.yaml"


class TunnelRegistryError(ValueError):
    """Raised when a tunnel registry record violates ADR-038."""


@dataclass(frozen=True, slots=True)
class TunnelPolicy:
    min_trust_score: float
    revalidation_interval_days: int
    archive_after_days_stale: int
    hot_path_lookup_budget_us: int
    direction_enum: tuple[str, ...]
    yaml_path: str


@dataclass(frozen=True, slots=True)
class Tunnel:
    tunnel_id: str
    from_cell: str
    to_solver: str
    trust_score: float
    provenance_event_id: str
    added_at_utc: str
    last_validated_utc: str
    direction: str
    active: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "tunnel_id": self.tunnel_id,
            "from_cell": self.from_cell,
            "to_solver": self.to_solver,
            "trust_score": float(self.trust_score),
            "provenance_event_id": self.provenance_event_id,
            "added_at_utc": self.added_at_utc,
            "last_validated_utc": self.last_validated_utc,
            "direction": self.direction,
            "active": self.active,
        }


def load_tunnel_policy(contract_path: Path | str = CONTRACT_PATH) -> TunnelPolicy:
    """Load ADR-038 policy values from the machine-readable contract."""

    path = Path(contract_path)
    contract = json.loads(path.read_text(encoding="utf-8"))
    defaults = contract.get("policy_defaults", {})
    directions = contract.get("direction_enum", ())
    if not isinstance(directions, list) or not directions:
        raise TunnelRegistryError("contract direction_enum must be a non-empty list")
    return TunnelPolicy(
        min_trust_score=_as_float(defaults.get("min_trust_score"), "min_trust_score"),
        revalidation_interval_days=_as_int(
            defaults.get("revalidation_interval_days"),
            "revalidation_interval_days",
        ),
        archive_after_days_stale=_as_int(
            defaults.get("archive_after_days_stale"),
            "archive_after_days_stale",
        ),
        hot_path_lookup_budget_us=_as_int(
            defaults.get("hot_path_lookup_budget_us"),
            "hot_path_lookup_budget_us",
        ),
        direction_enum=tuple(str(item) for item in directions),
        yaml_path=str(contract.get("yaml_path") or ""),
    )


class TunnelRegistry:
    """Validated, indexed in-memory tunnel registry.

    The registry keeps active tunnels in a ``from_cell`` index so read-side
    lookup does not scan the full tunnel set. Archived or inactive tunnels are
    retained in ``all_tunnels`` for audit/projection use but skipped by lookup.
    """

    def __init__(
        self,
        tunnels: Iterable[Tunnel | Mapping[str, Any]] = (),
        *,
        policy: TunnelPolicy | None = None,
        now_utc: str | datetime | None = None,
    ) -> None:
        self.policy = policy or load_tunnel_policy()
        now = _coerce_datetime(now_utc) if now_utc is not None else None
        self._by_id: dict[str, Tunnel] = {}
        self._by_from_cell: dict[str, tuple[Tunnel, ...]] = {}
        for tunnel in tunnels:
            self.add_tunnel(tunnel, now_utc=now)

    @property
    def all_tunnels(self) -> tuple[Tunnel, ...]:
        return tuple(self._by_id[key] for key in sorted(self._by_id))

    def add_tunnel(
        self,
        tunnel: Tunnel | Mapping[str, Any],
        *,
        now_utc: str | datetime | None = None,
    ) -> Tunnel:
        normalized = _normalize_tunnel(
            tunnel,
            policy=self.policy,
            now_utc=_coerce_datetime(now_utc) if now_utc is not None else None,
        )
        if normalized.tunnel_id in self._by_id:
            raise TunnelRegistryError(f"duplicate tunnel_id: {normalized.tunnel_id}")
        self._by_id[normalized.tunnel_id] = normalized
        if normalized.active:
            current = list(self._by_from_cell.get(normalized.from_cell, ()))
            current.append(normalized)
            self._by_from_cell[normalized.from_cell] = tuple(
                sorted(current, key=lambda item: (-item.trust_score, item.tunnel_id))
            )
        return normalized

    def lookup_tunnels(
        self,
        from_cell: str,
        *,
        direction: str | None = None,
    ) -> tuple[Tunnel, ...]:
        if not isinstance(from_cell, str) or not from_cell.strip():
            raise TunnelRegistryError("from_cell must be a non-empty string")
        if direction is not None and direction not in self.policy.direction_enum:
            raise TunnelRegistryError(f"invalid direction: {direction}")
        tunnels = self._by_from_cell.get(from_cell.strip(), ())
        if direction is None:
            return tunnels
        return tuple(item for item in tunnels if item.direction == direction)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "tunnel_overlay_registry.v1",
            "policy": {
                "min_trust_score": self.policy.min_trust_score,
                "revalidation_interval_days": self.policy.revalidation_interval_days,
                "archive_after_days_stale": self.policy.archive_after_days_stale,
                "hot_path_lookup_budget_us": self.policy.hot_path_lookup_budget_us,
                "direction_enum": list(self.policy.direction_enum),
                "yaml_path": self.policy.yaml_path,
            },
            "tunnels": [item.to_dict() for item in self.all_tunnels],
        }


def load_tunnel_registry_from_yaml(
    path: Path | str = DEFAULT_YAML_PATH,
    *,
    policy: TunnelPolicy | None = None,
    now_utc: str | datetime | None = None,
) -> TunnelRegistry:
    """Load an operator-editable tunnel registry YAML file."""

    import yaml

    loader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
    data = yaml.load(Path(path).read_text(encoding="utf-8"), Loader=loader)
    if data is None:
        data = {}
    if isinstance(data, list):
        tunnels = data
    elif isinstance(data, Mapping):
        tunnels = data.get("tunnels", ())
    else:
        raise TunnelRegistryError("tunnel overlay YAML must be a mapping or list")
    if not isinstance(tunnels, Sequence) or isinstance(tunnels, (str, bytes)):
        raise TunnelRegistryError("tunnel overlay YAML tunnels must be a list")
    return TunnelRegistry(tunnels, policy=policy, now_utc=now_utc)


def _normalize_tunnel(
    value: Tunnel | Mapping[str, Any],
    *,
    policy: TunnelPolicy,
    now_utc: datetime | None,
) -> Tunnel:
    tunnel = value if isinstance(value, Tunnel) else _tunnel_from_mapping(value)
    _validate_tunnel(tunnel, policy=policy)
    if now_utc is None or not tunnel.active:
        return tunnel
    last_validated = _coerce_datetime(tunnel.last_validated_utc)
    age_days = (now_utc - last_validated).total_seconds() / 86400.0
    if age_days > policy.archive_after_days_stale:
        return replace(tunnel, active=False)
    return tunnel


def _tunnel_from_mapping(value: Mapping[str, Any]) -> Tunnel:
    if not isinstance(value, Mapping):
        raise TunnelRegistryError("tunnel must be an object")
    active = value.get("active", True)
    return Tunnel(
        tunnel_id=str(value.get("tunnel_id") or ""),
        from_cell=str(value.get("from_cell") or ""),
        to_solver=str(value.get("to_solver") or ""),
        trust_score=_as_float(value.get("trust_score"), "trust_score"),
        provenance_event_id=str(value.get("provenance_event_id") or ""),
        added_at_utc=str(value.get("added_at_utc") or ""),
        last_validated_utc=str(value.get("last_validated_utc") or ""),
        direction=str(value.get("direction") or ""),
        active=_as_bool(active, "active"),
    )


def _validate_tunnel(tunnel: Tunnel, *, policy: TunnelPolicy) -> None:
    for field_name in (
        "tunnel_id",
        "from_cell",
        "to_solver",
        "provenance_event_id",
        "added_at_utc",
        "last_validated_utc",
    ):
        value = getattr(tunnel, field_name)
        if not isinstance(value, str) or not value.strip():
            raise TunnelRegistryError(f"{field_name} must be a non-empty string")
    if tunnel.trust_score < policy.min_trust_score:
        raise TunnelRegistryError("trust_score below policy min_trust_score")
    if not (0.0 <= tunnel.trust_score <= 1.0):
        raise TunnelRegistryError("trust_score must be 0..1")
    if tunnel.direction not in policy.direction_enum:
        raise TunnelRegistryError(f"direction must be one of {policy.direction_enum}")
    _coerce_datetime(tunnel.added_at_utc)
    _coerce_datetime(tunnel.last_validated_utc)
    if not isinstance(tunnel.active, bool):
        raise TunnelRegistryError("active must be boolean")


def _coerce_datetime(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str) and value.strip():
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(text)
        except ValueError as exc:
            raise TunnelRegistryError(f"invalid UTC timestamp: {value}") from exc
    else:
        raise TunnelRegistryError("timestamp must be a non-empty ISO string")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _as_float(value: Any, field_name: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise TunnelRegistryError(f"{field_name} must be numeric")
    out = float(value)
    if not math.isfinite(out):
        raise TunnelRegistryError(f"{field_name} must be finite")
    return out


def _as_int(value: Any, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise TunnelRegistryError(f"{field_name} must be an integer")
    return value


def _as_bool(value: Any, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise TunnelRegistryError(f"{field_name} must be boolean")
    return value

#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""Pure, nonprivileged Bridge v2 quota evidence and admission arithmetic.

This W0 contract does not read a provider, authenticate an account, acquire an
atomic reservation, or permit dispatch. Its ``admissible`` result is conditional
on caller-supplied evidence and must be revalidated against an authoritative
pool ledger immediately before a separate, authorized side effect. Timestamps
use exactly YYYY-MM-DDTHH:MM:SS[.ffffff]Z (one to six fractional digits).
Float units use their JSON-text/repr decimal value, not their binary fraction.
Snapshot maximum age is a mandatory external signed-policy check; the expiry
and reset checks here are not a substitute for that policy.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from decimal import Context, Decimal, DecimalException, Inexact, InvalidOperation, Overflow, localcontext
import hashlib
import json
import re
from typing import Any


SNAPSHOT_SCHEMA = "wd.bridge-v2-quota-snapshot.v1"
DEMAND_SCHEMA = "wd.bridge-v2-quota-demand.v1"
DECISION_SCHEMA = "wd.bridge-v2-quota-decision.v1"
MAX_UNITS = Decimal("1000000000000000")
MAX_UNITS_INT = 10**15
MAX_RESERVATIONS = 4096
_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_UTC = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?Z")
_BINDING = ("provider", "account_id", "pool_id", "limit_id", "reset_epoch", "unit")
_SNAPSHOT_FIELDS = frozenset({
    "schema", *_BINDING, "remaining_lower_bound", "uncertainty_upper_units",
    "external_residual_upper_units", "observed_at_utc", "expires_at_utc",
    "reset_at_utc", "source", "source_digest", "binding_state",
})
_DEMAND_FIELDS = frozenset({
    "schema", *_BINDING, "snapshot_digest", "reservations",
    "forecast_upper_units", "forecast_through_utc", "incident_reserve_units", "reviewer_reserve_units",
    "proposed_upper_units",
})
_RESERVATION_FIELDS = frozenset({"id", "upper_units", *_BINDING})


class ContractError(ValueError):
    """Stable machine-readable rejection of a malformed contract value."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _object(value: Any, fields: frozenset[str]) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or any(not isinstance(k, str) for k in value):
        raise ContractError("invalid_object")
    missing = fields - value.keys()
    if missing:
        raise ContractError("missing_field")
    if value.keys() - fields:
        raise ContractError("unknown_field")
    return value


def _identifier(value: Any) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise ContractError("invalid_identifier")
    return value


def _digest(value: Any) -> str:
    if not isinstance(value, str) or not _DIGEST.fullmatch(value):
        raise ContractError("invalid_digest")
    return value


def _units(value: Any, *, nullable: bool = False) -> Decimal | None:
    if value is None and nullable:
        return None
    if type(value) not in (int, float):  # bool is not a number in this contract
        raise ContractError("invalid_number")
    # Check before str(): Python may reject conversion of an enormous integer.
    if type(value) is int and not 0 <= value <= MAX_UNITS_INT:
        raise ContractError("invalid_number")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ContractError("invalid_number") from None
    if not number.is_finite() or number < 0 or number > MAX_UNITS:
        raise ContractError("invalid_number")
    return number


def _utc(value: Any) -> datetime:
    if not isinstance(value, str) or not _UTC.fullmatch(value):
        raise ContractError("invalid_utc")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        raise ContractError("invalid_utc") from None
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ContractError("invalid_utc")
    return parsed.astimezone(timezone.utc)


def _canonical_digest(raw: Any) -> str:
    try:
        encoded = json.dumps(raw, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError):
        raise ContractError("invalid_object") from None
    return hashlib.sha256(encoded).hexdigest()


def snapshot_digest(raw: Mapping[str, Any]) -> str:
    """Canonical content digest for equality, not proof of provider identity."""
    if not isinstance(raw, Mapping):
        raise ContractError("invalid_object")
    return _canonical_digest(raw)


def parse_snapshot(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Validate one observed pool-window snapshot without filling missing data."""
    row = _object(raw, _SNAPSHOT_FIELDS)
    if row["schema"] != SNAPSHOT_SCHEMA:
        raise ContractError("invalid_schema")
    result = {key: _identifier(row[key]) for key in _BINDING}
    result.update({
        "remaining_lower_bound": _units(row["remaining_lower_bound"], nullable=True),
        "uncertainty_upper_units": _units(row["uncertainty_upper_units"], nullable=True),
        "external_residual_upper_units": _units(row["external_residual_upper_units"], nullable=True),
        "observed_at_utc": _utc(row["observed_at_utc"]),
        "expires_at_utc": _utc(row["expires_at_utc"]),
        "reset_at_utc": _utc(row["reset_at_utc"]),
        "source": _identifier(row["source"]),
        "source_digest": _digest(row["source_digest"]),
    })
    if row["binding_state"] not in ("verified", "unknown", "mismatch"):
        raise ContractError("invalid_binding_state")
    result["binding_state"] = row["binding_state"]
    if not (result["observed_at_utc"] < result["expires_at_utc"] <= result["reset_at_utc"]):
        raise ContractError("invalid_time_order")
    return result


def _parse_demand(raw: Mapping[str, Any]) -> dict[str, Any]:
    row = _object(raw, _DEMAND_FIELDS)
    if row["schema"] != DEMAND_SCHEMA:
        raise ContractError("invalid_schema")
    result = {key: _identifier(row[key]) for key in _BINDING}
    result["snapshot_digest"] = _digest(row["snapshot_digest"])
    for key in ("forecast_upper_units", "incident_reserve_units",
                "reviewer_reserve_units", "proposed_upper_units"):
        result[key] = _units(row[key], nullable=True)
    result["forecast_through_utc"] = _utc(row["forecast_through_utc"])
    reservations = row["reservations"]
    if not isinstance(reservations, list) or len(reservations) > MAX_RESERVATIONS:
        raise ContractError("invalid_reservations")
    seen: set[str] = set()
    parsed = []
    for item in reservations:
        entry = _object(item, _RESERVATION_FIELDS)
        ident = _identifier(entry["id"])
        if ident in seen:
            raise ContractError("duplicate_reservation")
        seen.add(ident)
        parsed.append({
            "id": ident, "upper_units": _units(entry["upper_units"]),
            **{key: _identifier(entry[key]) for key in _BINDING},
        })
    result["reservations"] = parsed
    return result


def _text(number: Decimal | None) -> str | None:
    if number is None:
        return None
    value = format(number, "f")
    return value.rstrip("0").rstrip(".") if "." in value else value


def evaluate_admission(snapshot: Mapping[str, Any], demand: Mapping[str, Any],
                       *, now: datetime) -> dict[str, Any]:
    """Return a conditional arithmetic verdict, never a dispatch permission.

    The caller must prove provider/account binding, completeness of the active
    reservation ledger and source authenticity separately. A positive result
    still needs an atomic compare-and-reserve and a fresh policy decision,
    including signed-policy maximum age. Digests/counts bind this calculation
    to caller-supplied data; they do not authenticate that data.
    """
    result: dict[str, Any] = {
        "schema": DECISION_SCHEMA, "state": "unknown", "reason": "invalid_now",
        "detail_code": None, "execution_allowed": False,
        "atomic_reservation_required": True, "snapshot_digest": None,
        "demand_digest": None, "reservation_ledger_digest": None,
        "reservation_count": None, "remaining_after_proposal_units": None,
        "provenance": None,
    }
    if (not isinstance(now, datetime) or now.tzinfo is None
            or now.utcoffset() != timedelta(0)):
        return result
    now = now.astimezone(timezone.utc)
    try:
        observed = parse_snapshot(snapshot)
        digest = snapshot_digest(snapshot)
    except ContractError as exc:
        result.update(reason="invalid_snapshot", detail_code=exc.code)
        return result
    result["snapshot_digest"] = digest
    result["provenance"] = {
        "provider": observed["provider"], "account_id": observed["account_id"],
        "pool_id": observed["pool_id"], "limit_id": observed["limit_id"],
        "reset_epoch": observed["reset_epoch"], "unit": observed["unit"],
        "source": observed["source"], "source_digest": observed["source_digest"],
        "observed_at_utc": observed["observed_at_utc"].isoformat(),
        "expires_at_utc": observed["expires_at_utc"].isoformat(),
        "reset_at_utc": observed["reset_at_utc"].isoformat(),
        "reservation_ledger_authenticity": "not_verified_here",
    }
    try:
        proposed = _parse_demand(demand)
    except ContractError as exc:
        result.update(reason="invalid_demand", detail_code=exc.code)
        return result
    # Parsing precedes hashing, so every number and identifier is bounded.
    result["demand_digest"] = snapshot_digest(demand)
    result["reservation_ledger_digest"] = _canonical_digest(demand["reservations"])
    result["reservation_count"] = len(proposed["reservations"])
    if proposed["snapshot_digest"] != digest:
        result["reason"] = "snapshot_digest_mismatch"
        return result
    if any(proposed[key] != observed[key] for key in _BINDING):
        result["reason"] = "binding_mismatch"
        return result
    if any(any(reservation[key] != observed[key] for key in _BINDING)
           for reservation in proposed["reservations"]):
        result["reason"] = "reservation_binding_mismatch"
        return result
    if proposed["forecast_through_utc"] != observed["reset_at_utc"]:
        result["reason"] = "forecast_horizon_mismatch"
        return result
    if observed["binding_state"] != "verified":
        result["reason"] = "binding_unverified"
        return result
    if now < observed["observed_at_utc"]:
        result["reason"] = "future_observation"
        return result
    if now >= observed["expires_at_utc"] or now >= observed["reset_at_utc"]:
        result["reason"] = "snapshot_expired"
        return result
    for key, reason in (
        ("remaining_lower_bound", "remaining_unknown"),
        ("uncertainty_upper_units", "uncertainty_unbounded"),
        ("external_residual_upper_units", "external_residual_unbounded"),
    ):
        if observed[key] is None:
            result["reason"] = reason
            return result
    for key, reason in (
        ("forecast_upper_units", "forecast_unknown"),
        ("incident_reserve_units", "incident_reserve_unknown"),
        ("reviewer_reserve_units", "reviewer_reserve_unknown"),
        ("proposed_upper_units", "proposed_upper_unknown"),
    ):
        if proposed[key] is None:
            result["reason"] = reason
            return result
    # MAX_UNITS and repr(float) bound magnitudes/exponents. A 1000-digit
    # private context represents the 4096-entry sum and subnormal float costs
    # exactly; traps turn any unexpected inexact operation into a refusal.
    arithmetic = Context(prec=1000)
    arithmetic.traps[Inexact] = True
    arithmetic.traps[InvalidOperation] = True
    arithmetic.traps[Overflow] = True
    try:
        with localcontext(arithmetic):
            reservation_total = sum((r["upper_units"] for r in proposed["reservations"]), Decimal(0))
            if reservation_total > MAX_UNITS:
                result.update(reason="invalid_demand", detail_code="reservation_total_out_of_bounds")
                return result
            remaining = (observed["remaining_lower_bound"] - observed["uncertainty_upper_units"]
                         - observed["external_residual_upper_units"] - reservation_total
                         - proposed["forecast_upper_units"] - proposed["incident_reserve_units"]
                         - proposed["reviewer_reserve_units"] - proposed["proposed_upper_units"])
    except DecimalException:
        result.update(reason="invalid_demand", detail_code="arithmetic_not_exact")
        return result
    result["remaining_after_proposal_units"] = _text(remaining)
    if remaining >= 0:
        result.update(state="admissible", reason="sufficient_conservative_headroom")
    else:
        result.update(state="denied", reason="insufficient_conservative_headroom")
    return result

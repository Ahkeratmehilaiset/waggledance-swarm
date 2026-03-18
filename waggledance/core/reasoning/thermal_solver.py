"""Thermal solver — physics-based thermal calculations for heating, cooling, frost.

Wraps the existing axiom models (heating_cost, hive_thermal, pipe_freezing)
and adds direct thermal physics formulas for cases where axiom files
don't exist or inputs are incomplete.
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class ThermalResult:
    """Result of a thermal calculation."""
    success: bool
    value: float = 0.0
    unit: str = ""
    formula: str = ""
    inputs_used: Dict[str, float] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)
    compute_time_ms: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "value": round(self.value, 4),
            "unit": self.unit,
            "formula": self.formula,
            "inputs_used": self.inputs_used,
            "warnings": self.warnings,
            "compute_time_ms": round(self.compute_time_ms, 2),
        }


class ThermalSolver:
    """Physics-based thermal reasoning engine.

    Provides direct calculation for:
    - Heat loss (conduction through walls/insulation)
    - Heating cost estimation (energy × spot price)
    - Frost risk assessment (pipe freezing point)
    - Hive thermal balance (bee cluster vs ambient)
    - Heat pump COP estimation
    """

    def __init__(self, symbolic_solver=None):
        self._symbolic = symbolic_solver
        self._history: List[ThermalResult] = []
        self._max_history = 200

    def heat_loss_watts(self, area_m2: float, r_value: float,
                        t_inside: float, t_outside: float) -> ThermalResult:
        """Calculate heat loss through a surface (W).

        Q = A × ΔT / R  where R is thermal resistance (m²·K/W).
        """
        t0 = time.monotonic()
        if r_value <= 0:
            return ThermalResult(success=False, warnings=["R-value must be > 0"])
        delta_t = t_inside - t_outside
        q = area_m2 * delta_t / r_value
        result = ThermalResult(
            success=True,
            value=q,
            unit="W",
            formula="Q = A × ΔT / R",
            inputs_used={"area_m2": area_m2, "R_value": r_value,
                         "T_inside": t_inside, "T_outside": t_outside},
            compute_time_ms=(time.monotonic() - t0) * 1000,
        )
        self._record(result)
        return result

    def heating_cost(self, power_watts: float, hours: float,
                     price_c_kwh: float) -> ThermalResult:
        """Calculate heating cost (EUR).

        Cost = P × h × price / 100000  (watts → kW, cents → EUR).
        """
        t0 = time.monotonic()
        kwh = power_watts * hours / 1000.0
        cost_eur = kwh * price_c_kwh / 100.0
        result = ThermalResult(
            success=True,
            value=cost_eur,
            unit="EUR",
            formula="cost = P × h / 1000 × price / 100",
            inputs_used={"power_W": power_watts, "hours": hours,
                         "price_c_kWh": price_c_kwh},
            compute_time_ms=(time.monotonic() - t0) * 1000,
        )
        self._record(result)
        return result

    def frost_risk(self, t_outdoor: float, pipe_insulated: bool,
                   wind_speed_ms: float = 0.0) -> ThermalResult:
        """Assess pipe freezing risk.

        Wind chill adjustment: T_eff = 13.12 + 0.6215×T - 11.37×V^0.16 + 0.3965×T×V^0.16
        Risk thresholds: >0°C safe, 0..-5 low, -5..-15 medium, <-15 high.
        Insulation reduces effective exposure by ~10°C.
        """
        t0 = time.monotonic()
        t_eff = t_outdoor
        if wind_speed_ms > 1.3:  # wind chill formula valid above ~4.8 km/h
            v_kmh = wind_speed_ms * 3.6
            t_eff = (13.12 + 0.6215 * t_outdoor
                     - 11.37 * (v_kmh ** 0.16)
                     + 0.3965 * t_outdoor * (v_kmh ** 0.16))
        if pipe_insulated:
            t_eff += 10.0

        if t_eff > 0:
            risk = 0.0
        elif t_eff > -5:
            risk = 0.3
        elif t_eff > -15:
            risk = 0.6
        else:
            risk = 0.9

        warnings = []
        if risk >= 0.6:
            warnings.append(f"High frost risk: effective temp {t_eff:.1f}°C")
        if not pipe_insulated and t_outdoor < -5:
            warnings.append("Uninsulated pipes at risk")

        result = ThermalResult(
            success=True,
            value=risk,
            unit="risk_score",
            formula="wind_chill + insulation_offset → risk_band",
            inputs_used={"T_outdoor": t_outdoor, "insulated": pipe_insulated,
                         "wind_ms": wind_speed_ms, "T_effective": round(t_eff, 2)},
            warnings=warnings,
            compute_time_ms=(time.monotonic() - t0) * 1000,
        )
        self._record(result)
        return result

    def hive_thermal_balance(self, cluster_temp: float, ambient_temp: float,
                             colony_size: float, season_factor: float = 1.0) -> ThermalResult:
        """Estimate hive thermal balance.

        Healthy cluster maintains ~35°C in brood area.
        Colony heat output scales with colony_size (relative to a standard colony of 1.0).
        Delta from ideal indicates stress.
        """
        t0 = time.monotonic()
        ideal_brood_temp = 35.0
        heat_capacity = colony_size * season_factor
        delta_t = cluster_temp - ambient_temp
        heat_needed = max(0, ideal_brood_temp - ambient_temp)
        heat_available = heat_capacity * 15.0  # rough W per standard colony
        thermal_stress = max(0, 1.0 - (heat_available / max(heat_needed, 0.01)))
        thermal_stress = min(thermal_stress, 1.0)

        warnings = []
        if cluster_temp < 30:
            warnings.append(f"Cluster temp {cluster_temp}°C is below 30°C threshold")
        if thermal_stress > 0.7:
            warnings.append(f"High thermal stress: {thermal_stress:.2f}")

        result = ThermalResult(
            success=True,
            value=thermal_stress,
            unit="stress_score",
            formula="stress = 1 - heat_available/heat_needed",
            inputs_used={"cluster_temp": cluster_temp, "ambient_temp": ambient_temp,
                         "colony_size": colony_size, "season_factor": season_factor},
            warnings=warnings,
            compute_time_ms=(time.monotonic() - t0) * 1000,
        )
        self._record(result)
        return result

    def heat_pump_cop(self, t_source: float, t_sink: float) -> ThermalResult:
        """Estimate heat pump COP using Carnot efficiency with realistic factor.

        COP_carnot = T_sink / (T_sink - T_source) [in Kelvin]
        COP_real ≈ COP_carnot × 0.45 (typical real-world fraction)
        """
        t0 = time.monotonic()
        t_source_k = t_source + 273.15
        t_sink_k = t_sink + 273.15
        if t_sink_k <= t_source_k:
            return ThermalResult(success=False, warnings=["Sink must be warmer than source"])
        cop_carnot = t_sink_k / (t_sink_k - t_source_k)
        cop_real = cop_carnot * 0.45
        cop_real = min(cop_real, 7.0)  # practical upper limit

        result = ThermalResult(
            success=True,
            value=cop_real,
            unit="COP",
            formula="COP = T_sink/(T_sink-T_source) × 0.45",
            inputs_used={"T_source": t_source, "T_sink": t_sink,
                         "COP_carnot": round(cop_carnot, 2)},
            compute_time_ms=(time.monotonic() - t0) * 1000,
        )
        self._record(result)
        return result

    def solve(self, calculation_type: str, inputs: Dict[str, float]) -> ThermalResult:
        """Generic solver dispatch."""
        dispatch = {
            "heat_loss": lambda: self.heat_loss_watts(
                inputs.get("area_m2", 100), inputs.get("R_value", 3.0),
                inputs.get("T_inside", 21), inputs.get("T_outside", -10)),
            "heating_cost": lambda: self.heating_cost(
                inputs.get("power_W", 2000), inputs.get("hours", 24),
                inputs.get("price_c_kWh", 10)),
            "frost_risk": lambda: self.frost_risk(
                inputs.get("T_outdoor", -10), inputs.get("insulated", False),
                inputs.get("wind_ms", 0)),
            "hive_thermal": lambda: self.hive_thermal_balance(
                inputs.get("cluster_temp", 35), inputs.get("ambient_temp", 5),
                inputs.get("colony_size", 1.0), inputs.get("season_factor", 1.0)),
            "heat_pump_cop": lambda: self.heat_pump_cop(
                inputs.get("T_source", -5), inputs.get("T_sink", 45)),
        }
        fn = dispatch.get(calculation_type)
        if fn is None:
            if self._symbolic:
                try:
                    sr = self._symbolic.solve(calculation_type, inputs)
                    return ThermalResult(
                        success=sr.success, value=float(sr.value) if sr.success else 0.0,
                        unit=sr.unit or "", formula=", ".join(sr.formulas_used),
                        inputs_used={k: v for k, v in (sr.inputs_used or {}).items()},
                    )
                except Exception:
                    pass
            return ThermalResult(success=False, warnings=[f"Unknown calculation: {calculation_type}"])
        return fn()

    def _record(self, result: ThermalResult) -> None:
        self._history.append(result)
        if len(self._history) > self._max_history:
            self._history = self._history[-self._max_history:]

    def stats(self) -> Dict[str, Any]:
        total = len(self._history)
        successes = sum(1 for r in self._history if r.success)
        return {
            "total_calculations": total,
            "success_rate": successes / total if total else 0.0,
            "symbolic_solver_available": self._symbolic is not None,
        }

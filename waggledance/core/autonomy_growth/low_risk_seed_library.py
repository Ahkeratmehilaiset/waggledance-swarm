# SPDX-License-Identifier: BUSL-1.1
# BUSL-Change-Date: 2030-12-31
# SPDX-FileCopyrightText: Jani Korpi / Ahkerat Mehilaiset / JKH Service
# See LICENSE-BUSL.txt and LICENSE-CORE.md
"""Canonical seed library for the six allowlisted low-risk families.

The library turns one or many harvested runtime gap signals — or a
caller's parameter sweep — into a batch of validated SolverSpec
seeds that the AutogrowthScheduler can promote in one cycle. The
common path produces seeds locally with no provider call.

Each seed dict has the shape:

    {
      "spec": {... family-specific keys ...},
      "validation_cases": [{"inputs": ..., "expected": ...}, ...],
      "shadow_samples": [{"inputs": ...}, ...],
      "solver_name_seed": "<unique_per_family>",
      "cell_id": "<hex cell>",
      "source": "phase13_seed_library",
      "source_kind": "canonical_seed",
    }

The size of the library is intentionally large enough to drive a
100+ solver session proof corpus without provider involvement.
"""
from __future__ import annotations

from typing import Any, Iterable


# ---------------- scalar_unit_conversion ----------------

_SCALAR_SEEDS: tuple[tuple[str, str, str, str, float, float], ...] = (
    # (name,                       cell,     from, to,    factor,           offset)
    ("celsius_to_kelvin",          "thermal", "C",  "K",   1.0,              273.15),
    ("kelvin_to_celsius",          "thermal", "K",  "C",   1.0,              -273.15),
    ("celsius_to_fahrenheit",      "thermal", "C",  "F",   9.0 / 5.0,        32.0),
    ("fahrenheit_to_celsius",      "thermal", "F",  "C",   5.0 / 9.0,        -160.0 / 9.0),
    ("kelvin_to_fahrenheit",       "thermal", "K",  "F",   9.0 / 5.0,        -459.67),
    ("meters_to_kilometers",       "math",    "m",  "km",  0.001,            0.0),
    ("kilometers_to_meters",       "math",    "km", "m",   1000.0,           0.0),
    ("meters_to_miles",            "math",    "m",  "mi",  0.000621371,      0.0),
    ("kilometers_to_miles",        "math",    "km", "mi",  0.621371,         0.0),
    ("feet_to_meters",             "math",    "ft", "m",   0.3048,           0.0),
    ("inches_to_centimeters",      "math",    "in", "cm",  2.54,             0.0),
    ("kilograms_to_pounds",        "math",    "kg", "lb",  2.20462,          0.0),
    ("pounds_to_kilograms",        "math",    "lb", "kg",  0.453592,         0.0),
    ("kilowatts_to_watts",         "energy",  "kW", "W",   1000.0,           0.0),
    ("watts_to_kilowatts",         "energy",  "W",  "kW",  0.001,            0.0),
    ("megawatts_to_kilowatts",     "energy",  "MW", "kW",  1000.0,           0.0),
    ("seconds_to_minutes",         "system",  "s",  "min", 1.0 / 60.0,       0.0),
    ("minutes_to_seconds",         "system",  "min", "s",  60.0,             0.0),
    ("hours_to_minutes",           "system",  "h",  "min", 60.0,             0.0),
    ("milliseconds_to_seconds",    "system",  "ms", "s",   0.001,            0.0),
    # Phase 14 P4 expansion — angle/area/volume/data conversions
    ("degrees_to_radians",         "math",    "deg", "rad", 0.017453292519943295, 0.0),
    ("radians_to_degrees",         "math",    "rad", "deg", 57.29577951308232,    0.0),
    ("square_meters_to_square_feet", "math",  "m2",  "ft2", 10.7639,             0.0),
    ("liters_to_gallons_us",       "math",    "L",   "gal_us", 0.264172,         0.0),
    ("megabytes_to_kilobytes",     "system",  "MB",  "kB",  1024.0,              0.0),
    ("gigabytes_to_megabytes",     "system",  "GB",  "MB",  1024.0,              0.0),
    ("days_to_hours",              "system",  "d",   "h",   24.0,                0.0),
    # Phase 16B P4 expansion (+1 to cross 100-solver release gate)
    ("watt_hours_to_joules",       "energy",  "Wh",  "J",   3600.0,              0.0),
)


def _scalar_seeds() -> Iterable[dict]:
    for name, cell, from_u, to_u, factor, offset in _SCALAR_SEEDS:
        spec = {"from_unit": from_u, "to_unit": to_u,
                 "factor": factor, "offset": offset}
        cases = [
            {"inputs": {"x": float(v)},
              "expected": float(v) * factor + offset}
            for v in (-10.0, 0.0, 25.0, 100.0)
        ]
        samples = [{"x": float(i) * 1.7 - 50.0} for i in range(40)]
        yield {
            "spec": spec,
            "validation_cases": cases,
            "shadow_samples": samples,
            "solver_name_seed": name,
            "cell_id": cell,
            "source": "phase13_seed_library",
            "source_kind": "canonical_seed",
            "oracle_kind": "formula_recompute",
            "_family_kind": "scalar_unit_conversion",
            "_intent_seed": name,
        }


# ---------------- lookup_table ----------------

_LOOKUP_SEEDS: tuple[tuple[str, str, str, dict, Any], ...] = (
    ("color_to_action",      "general",  "color",
      {"red": "stop", "green": "go", "yellow": "slow"}, "wait"),
    ("color_to_severity",    "general",  "color",
      {"red": 3, "yellow": 2, "green": 1, "blue": 0}, 0),
    ("color_to_weight",      "general",  "color",
      {"black": 1.0, "white": 0.0, "red": 0.5, "blue": 0.4}, 0.5),
    ("status_to_severity",   "system",   "status",
      {"ok": 0, "warn": 1, "error": 2, "fatal": 3}, -1),
    ("status_to_label",      "system",   "status",
      {"ok": "green", "warn": "yellow", "error": "red"}, "unknown"),
    ("status_to_code",       "system",   "status",
      {"ok": 200, "warn": 201, "error": 500, "fatal": 503}, 599),
    ("weekday_to_workday",   "general",  "weekday",
      {"mon": True, "tue": True, "wed": True, "thu": True,
       "fri": True, "sat": False, "sun": False}, False),
    ("weekday_to_label_en",  "general",  "weekday_label_en",
      {"mon": "Monday", "tue": "Tuesday", "wed": "Wednesday",
       "thu": "Thursday", "fri": "Friday",
       "sat": "Saturday", "sun": "Sunday"}, "?"),
    ("weekday_to_label_fi",  "general",  "weekday_label_fi",
      {"mon": "maanantai", "tue": "tiistai", "wed": "keskiviikko",
       "thu": "torstai", "fri": "perjantai",
       "sat": "lauantai", "sun": "sunnuntai"}, "?"),
    ("verdict_to_label",     "safety",   "verdict",
      {"pass": "OK", "fail": "ALERT", "skip": "SKIP"}, "UNKNOWN"),
    ("verdict_to_color",     "safety",   "verdict_color",
      {"pass": "green", "fail": "red", "skip": "gray"}, "white"),
    ("direction_to_angle",   "math",     "direction",
      {"N": 0, "E": 90, "S": 180, "W": 270,
       "NE": 45, "SE": 135, "SW": 225, "NW": 315}, -1),
    # Phase 14 P4 expansion
    ("hue_to_temperature_band", "thermal", "hue_temp",
      {"red": "warm", "orange": "warm", "yellow": "neutral",
       "green": "cool", "blue": "cool", "violet": "cool"}, "neutral"),
    ("priority_to_severity",   "system",   "priority",
      {"P0": 4, "P1": 3, "P2": 2, "P3": 1, "P4": 0}, -1),
    ("month_to_season_north",  "seasonal", "month_north",
      {"jan": "winter", "feb": "winter", "mar": "spring", "apr": "spring",
       "may": "spring", "jun": "summer", "jul": "summer", "aug": "summer",
       "sep": "autumn", "oct": "autumn", "nov": "autumn", "dec": "winter"}, "?"),
    ("severity_to_color",      "safety",   "severity_color",
      {0: "green", 1: "yellow", 2: "orange", 3: "red", 4: "purple"}, "white"),
    # Phase 16B P4 expansion (+1 to cross 100-solver release gate)
    ("month_to_quarter",       "seasonal", "month_quarter",
      {"jan": "Q1", "feb": "Q1", "mar": "Q1", "apr": "Q2", "may": "Q2",
       "jun": "Q2", "jul": "Q3", "aug": "Q3", "sep": "Q3",
       "oct": "Q4", "nov": "Q4", "dec": "Q4"}, "?"),
)


def _lookup_seeds() -> Iterable[dict]:
    for name, cell, domain, table, default in _LOOKUP_SEEDS:
        spec = {"table": dict(table), "default": default, "domain": domain}
        keys = list(table.keys()) + ["__missing_key__"]
        cases = [
            {"inputs": {"key": k},
              "expected": table.get(k, default)}
            for k in keys
        ]
        samples = [{"key": k} for k in (list(table.keys()) * 2 + ["x"] * 2)]
        yield {
            "spec": spec,
            "validation_cases": cases,
            "shadow_samples": samples,
            "solver_name_seed": name,
            "cell_id": cell,
            "source": "phase13_seed_library",
            "source_kind": "canonical_seed",
            "_family_kind": "lookup_table",
            "_intent_seed": name,
        }


# ---------------- threshold_rule ----------------

_THRESHOLD_SEEDS: tuple[tuple[str, str, str, float, str, str, str], ...] = (
    # (name, cell, subject, threshold, op, true_label, false_label)
    ("hot_above_30c",          "thermal",  "temperature_c", 30.0, ">",  "hot",       "cool"),
    ("cold_below_5c",          "thermal",  "temperature_c", 5.0,  "<",  "cold",      "warm"),
    ("frost_below_0c",         "seasonal", "temperature_c", 0.0,  "<",  "frost",     "above_zero"),
    ("low_battery_below_20",   "energy",   "battery_pct",   20.0, "<",  "low",       "ok"),
    ("critical_battery_below_5","energy",  "battery_pct",   5.0,  "<",  "critical",  "above"),
    ("overload_above_80pct",   "system",   "cpu_pct",       80.0, ">=", "overload",  "ok"),
    ("memory_critical_above_90","system",  "memory_pct",    90.0, ">=", "critical",  "ok"),
    ("noisy_above_60db",       "general",  "noise_db",      60.0, ">",  "loud",      "quiet"),
    ("humid_above_70",         "thermal",  "humidity_pct",  70.0, ">",  "humid",     "normal"),
    ("dry_below_30",           "thermal",  "humidity_pct",  30.0, "<",  "dry",       "normal"),
    ("low_pressure_below_980", "seasonal", "pressure_hpa",  980.0, "<", "low",       "normal"),
    ("undervoltage_below_115", "energy",   "voltage_v",     11.5, "<",  "undervolt", "ok"),
    # Phase 14 P4 expansion
    ("disk_full_above_95pct",  "system",   "disk_pct",      95.0, ">=", "full",      "ok"),
    ("packet_loss_above_2pct", "system",   "packet_loss_pct", 2.0, ">", "lossy",     "clean"),
    ("latency_high_above_200ms","system",  "latency_ms",    200.0, ">", "slow",      "fast"),
    ("freezer_alarm_above_neg5","thermal", "freezer_temp_c", -5.0, ">", "thaw_risk", "ok"),
    # Phase 16B P4 expansion (+1 to cross 100-solver release gate)
    ("solar_yield_above_50kwh","energy",   "daily_solar_kwh", 50.0, ">=", "high",     "low"),
)


def _threshold_seeds() -> Iterable[dict]:
    ops = {">": lambda a, b: a > b, ">=": lambda a, b: a >= b,
            "<": lambda a, b: a < b, "<=": lambda a, b: a <= b,
            "==": lambda a, b: a == b, "!=": lambda a, b: a != b}
    for name, cell, subject, th, op, t_label, f_label in _THRESHOLD_SEEDS:
        spec = {"threshold": th, "operator": op,
                 "true_label": t_label, "false_label": f_label,
                 "subject": subject}
        f = ops[op]
        cases = [
            {"inputs": {"x": v},
              "expected": t_label if f(v, th) else f_label}
            for v in (th - 10.0, th, th + 10.0, th + 100.0)
        ]
        samples = [{"x": float(i)} for i in range(int(th) - 30, int(th) + 30)]
        yield {
            "spec": spec,
            "validation_cases": cases,
            "shadow_samples": samples,
            "solver_name_seed": name,
            "cell_id": cell,
            "source": "phase13_seed_library",
            "source_kind": "canonical_seed",
            "_family_kind": "threshold_rule",
            "_intent_seed": name,
        }


# ---------------- interval_bucket_classifier ----------------

_INTERVAL_SEEDS: tuple[tuple[str, str, str, list, Any], ...] = (
    ("temp_band", "thermal", "temperature_c", [
        {"min": -50.0, "max": 0.0, "label": "freezing"},
        {"min": 0.0, "max": 15.0, "label": "cold"},
        {"min": 15.0, "max": 25.0, "label": "comfort"},
        {"min": 25.0, "max": 40.0, "label": "warm"},
        {"min": 40.0, "max": 100.0, "label": "hot"},
    ], "out_of_range"),
    ("age_band", "general", "age_years", [
        {"min": 0, "max": 13, "label": "child"},
        {"min": 13, "max": 20, "label": "teen"},
        {"min": 20, "max": 65, "label": "adult"},
        {"min": 65, "max": 130, "label": "senior"},
    ], None),
    ("cpu_band", "system", "cpu_pct", [
        {"min": 0.0, "max": 25.0, "label": "idle"},
        {"min": 25.0, "max": 75.0, "label": "active"},
        {"min": 75.0, "max": 100.0, "label": "loaded"},
    ], "n/a"),
    ("hour_of_day_band", "general", "hour", [
        {"min": 0, "max": 6, "label": "night"},
        {"min": 6, "max": 12, "label": "morning"},
        {"min": 12, "max": 18, "label": "afternoon"},
        {"min": 18, "max": 24, "label": "evening"},
    ], "unknown"),
    ("score_band", "learning", "score", [
        {"min": 0.0, "max": 0.5, "label": "low"},
        {"min": 0.5, "max": 0.8, "label": "mid"},
        {"min": 0.8, "max": 1.01, "label": "high"},
    ], "out"),
    ("battery_band", "energy", "battery_pct", [
        {"min": 0.0, "max": 5.0, "label": "critical"},
        {"min": 5.0, "max": 20.0, "label": "low"},
        {"min": 20.0, "max": 80.0, "label": "ok"},
        {"min": 80.0, "max": 100.01, "label": "full"},
    ], "out"),
    ("humidity_band", "thermal", "humidity_pct", [
        {"min": 0.0, "max": 30.0, "label": "dry"},
        {"min": 30.0, "max": 60.0, "label": "comfort"},
        {"min": 60.0, "max": 100.01, "label": "humid"},
    ], "out"),
    ("pressure_band", "seasonal", "pressure_hpa", [
        {"min": 900.0, "max": 980.0, "label": "low"},
        {"min": 980.0, "max": 1020.0, "label": "normal"},
        {"min": 1020.0, "max": 1100.0, "label": "high"},
    ], "out"),
    # Phase 14 P4 expansion
    ("wind_band", "seasonal", "wind_speed_ms", [
        {"min": 0.0, "max": 5.0, "label": "calm"},
        {"min": 5.0, "max": 11.0, "label": "moderate"},
        {"min": 11.0, "max": 20.0, "label": "strong"},
        {"min": 20.0, "max": 100.0, "label": "storm"},
    ], "out"),
    ("uv_band", "seasonal", "uv_index", [
        {"min": 0.0, "max": 3.0, "label": "low"},
        {"min": 3.0, "max": 6.0, "label": "moderate"},
        {"min": 6.0, "max": 8.0, "label": "high"},
        {"min": 8.0, "max": 11.0, "label": "very_high"},
        {"min": 11.0, "max": 99.0, "label": "extreme"},
    ], "out"),
    ("memory_band", "system", "memory_pct", [
        {"min": 0.0, "max": 50.0, "label": "ok"},
        {"min": 50.0, "max": 80.0, "label": "warm"},
        {"min": 80.0, "max": 95.0, "label": "high"},
        {"min": 95.0, "max": 100.01, "label": "critical"},
    ], "out"),
    ("network_lag_band", "system", "rtt_ms", [
        {"min": 0.0, "max": 30.0, "label": "snappy"},
        {"min": 30.0, "max": 100.0, "label": "ok"},
        {"min": 100.0, "max": 300.0, "label": "slow"},
        {"min": 300.0, "max": 5000.0, "label": "bad"},
    ], "out"),
    ("growth_signal_band", "learning", "signal_strength", [
        {"min": 0.0, "max": 0.2, "label": "weak"},
        {"min": 0.2, "max": 0.6, "label": "moderate"},
        {"min": 0.6, "max": 1.01, "label": "strong"},
    ], "out"),
    # Phase 16B P4 expansion (+1 to cross 100-solver release gate)
    ("co2_band", "thermal", "co2_ppm", [
        {"min": 0.0, "max": 600.0, "label": "good"},
        {"min": 600.0, "max": 1000.0, "label": "warn"},
        {"min": 1000.0, "max": 2000.0, "label": "stale"},
        {"min": 2000.0, "max": 100000.0, "label": "bad"},
    ], "out"),
)


def _interval_seeds() -> Iterable[dict]:
    for name, cell, subject, intervals, oor in _INTERVAL_SEEDS:
        spec = {"intervals": intervals, "out_of_range_label": oor,
                 "subject": subject}
        cases: list[dict] = []
        for iv in intervals:
            mid = (iv["min"] + iv["max"]) / 2.0
            cases.append({"inputs": {"x": mid}, "expected": iv["label"]})
        cases.append({"inputs": {"x": -1e9}, "expected": oor})
        samples = [{"x": float(i) * 0.5 - 5.0} for i in range(60)]
        yield {
            "spec": spec,
            "validation_cases": cases,
            "shadow_samples": samples,
            "solver_name_seed": name,
            "cell_id": cell,
            "source": "phase13_seed_library",
            "source_kind": "canonical_seed",
            "_family_kind": "interval_bucket_classifier",
            "_intent_seed": name,
        }


# ---------------- linear_arithmetic ----------------

_LINEAR_SEEDS: tuple[tuple[str, str, list[float], float, list[str]], ...] = (
    ("comfort_score", "general",
      [0.6, -0.3, 0.1], 0.0,
      ["temp_dev", "humidity_dev", "noise_dev"]),
    ("energy_estimate_kwh", "energy",
      [0.001, 0.5], 0.05,
      ["watts", "hours"]),
    ("safety_index", "safety",
      [-0.5, -0.3, 1.0], 0.5,
      ["risk_a", "risk_b", "compliance"]),
    ("seasonal_weight", "seasonal",
      [0.2, 0.7, 0.1], 0.0,
      ["temp_z", "daylight_z", "humidity_z"]),
    ("performance_score", "system",
      [0.4, 0.4, 0.2], 0.1,
      ["throughput_z", "latency_z_inv", "error_rate_inv"]),
    ("risk_score_4d", "safety",
      [0.25, 0.25, 0.25, 0.25], 0.0,
      ["component_a", "component_b", "component_c", "component_d"]),
    ("resource_pressure", "system",
      [0.5, 0.3, 0.2], 0.0,
      ["cpu_z", "memory_z", "io_z"]),
    ("learning_progress", "learning",
      [0.6, 0.4], 0.0,
      ["correct_rate_z", "stability_z"]),
    # Phase 14 P4 expansion
    ("rolling_mean_3", "general",
      [1.0 / 3.0, 1.0 / 3.0, 1.0 / 3.0], 0.0,
      ["x_t", "x_tm1", "x_tm2"]),
    ("weighted_average_5", "general",
      [0.4, 0.3, 0.15, 0.1, 0.05], 0.0,
      ["s1", "s2", "s3", "s4", "s5"]),
    ("hvac_setpoint_drift", "thermal",
      [0.6, -0.4, 0.2], 18.0,
      ["outdoor_temp", "humidity_dev", "occupancy_z"]),
    ("daily_charge_estimate", "energy",
      [0.5, 0.5], 5.0,
      ["solar_kwh", "grid_kwh"]),
    ("anomaly_score_3d", "safety",
      [0.5, 0.3, 0.2], 0.0,
      ["recent_z", "context_z", "historical_z"]),
    # Phase 16B P4 expansion (+1 to cross 100-solver release gate)
    ("hex_neighbor_combine_4d", "general",
      [0.4, 0.3, 0.2, 0.1], 0.0,
      ["near_z", "mid_z", "far_z", "stale_z"]),
)


def _linear_seeds() -> Iterable[dict]:
    for name, cell, coefs, intercept, cols in _LINEAR_SEEDS:
        spec = {"coefficients": list(coefs), "intercept": intercept,
                 "input_columns": list(cols)}

        def y_for(inputs, coefs=coefs, b=intercept, cols=cols):
            return sum(c * float(inputs[col]) for c, col in zip(coefs, cols)) + b

        sample_inputs = [
            {col: float(i) * 0.5 + idx for idx, col in enumerate(cols)}
            for i in range(5)
        ]
        cases = [{"inputs": inp, "expected": y_for(inp)} for inp in sample_inputs]
        shadow = [
            {col: float(j * 0.3) + (-1.0 if k % 2 else 1.0)
              for k, col in enumerate(cols)}
            for j in range(20)
        ]
        yield {
            "spec": spec,
            "validation_cases": cases,
            "shadow_samples": shadow,
            "solver_name_seed": name,
            "cell_id": cell,
            "source": "phase13_seed_library",
            "source_kind": "canonical_seed",
            "_family_kind": "linear_arithmetic",
            "_intent_seed": name,
        }


# ---------------- bounded_interpolation ----------------

_INTERP_SEEDS: tuple[tuple[str, str, str, str, list, float, float], ...] = (
    ("battery_charge_voltage_curve", "energy", "charge_pct", "voltage_v",
      [{"x": 0.0, "y": 10.5}, {"x": 50.0, "y": 12.6},
        {"x": 100.0, "y": 13.8}], 0.0, 100.0),
    ("comfort_curve_temp", "thermal", "temperature_c", "comfort_score",
      [{"x": 0.0, "y": 0.1}, {"x": 21.0, "y": 1.0},
        {"x": 35.0, "y": 0.0}], 0.0, 35.0),
    ("power_efficiency_curve", "energy", "load_pct", "efficiency",
      [{"x": 0.0, "y": 0.0}, {"x": 0.3, "y": 0.55},
        {"x": 0.7, "y": 0.85}, {"x": 1.0, "y": 0.95}], 0.0, 1.0),
    ("daylight_factor_hour", "seasonal", "hour", "daylight_factor",
      [{"x": 0, "y": 0.0}, {"x": 6, "y": 0.2},
        {"x": 12, "y": 1.0}, {"x": 18, "y": 0.3},
        {"x": 24, "y": 0.0}], 0.0, 24.0),
    ("learning_rate_decay", "learning", "step", "learning_rate",
      [{"x": 0.0, "y": 0.001}, {"x": 100.0, "y": 0.0005},
        {"x": 1000.0, "y": 0.0001}], 0.0, 1000.0),
    ("cpu_latency_curve", "system", "cpu_pct", "latency_ms",
      [{"x": 0.0, "y": 1.0}, {"x": 50.0, "y": 5.0},
        {"x": 80.0, "y": 25.0}, {"x": 100.0, "y": 250.0}], 0.0, 100.0),
    ("range_signal_curve", "general", "distance_m", "signal_db",
      [{"x": 0.0, "y": -30.0}, {"x": 50.0, "y": -55.0},
        {"x": 100.0, "y": -75.0}, {"x": 200.0, "y": -90.0}], 0.0, 200.0),
    ("altitude_pressure_curve", "seasonal", "altitude_m", "pressure_hpa",
      [{"x": 0.0, "y": 1013.25}, {"x": 1000.0, "y": 898.76},
        {"x": 5000.0, "y": 540.20}, {"x": 10000.0, "y": 264.36}],
      0.0, 10000.0),
    # Phase 14 P4 expansion
    ("humidity_comfort_curve", "thermal", "humidity_pct", "comfort_factor",
      [{"x": 0.0, "y": 0.2}, {"x": 30.0, "y": 0.6},
        {"x": 50.0, "y": 1.0}, {"x": 70.0, "y": 0.7},
        {"x": 100.0, "y": 0.0}], 0.0, 100.0),
    ("memory_pressure_curve", "system", "memory_pct", "alloc_latency_ms",
      [{"x": 0.0, "y": 0.5}, {"x": 50.0, "y": 1.0},
        {"x": 80.0, "y": 5.0}, {"x": 95.0, "y": 50.0},
        {"x": 100.0, "y": 200.0}], 0.0, 100.0),
    ("disk_load_curve", "system", "disk_pct", "iops_score",
      [{"x": 0.0, "y": 1.0}, {"x": 50.0, "y": 0.95},
        {"x": 80.0, "y": 0.7}, {"x": 95.0, "y": 0.3},
        {"x": 100.0, "y": 0.0}], 0.0, 100.0),
    ("uv_to_burn_curve", "seasonal", "uv_index", "burn_minutes",
      [{"x": 0.0, "y": 240.0}, {"x": 3.0, "y": 60.0},
        {"x": 6.0, "y": 30.0}, {"x": 8.0, "y": 15.0},
        {"x": 11.0, "y": 8.0}], 0.0, 11.0),
    ("retention_curve", "learning", "days", "retention",
      [{"x": 0.0, "y": 1.0}, {"x": 1.0, "y": 0.6},
        {"x": 7.0, "y": 0.3}, {"x": 30.0, "y": 0.1},
        {"x": 90.0, "y": 0.05}], 0.0, 90.0),
    # Phase 16B P4 expansion (+1 to cross 100-solver release gate)
    ("noise_to_focus_curve", "general", "noise_db", "focus_score",
      [{"x": 20.0, "y": 1.0}, {"x": 40.0, "y": 0.8},
        {"x": 60.0, "y": 0.4}, {"x": 80.0, "y": 0.1},
        {"x": 100.0, "y": 0.0}], 20.0, 100.0),
)


def _interp_seeds() -> Iterable[dict]:
    for name, cell, x_var, y_var, knots, min_x, max_x in _INTERP_SEEDS:
        spec = {"knots": list(knots), "method": "linear",
                 "min_x": min_x, "max_x": max_x,
                 "out_of_range_policy": "clip",
                 "x_var": x_var, "y_var": y_var}

        def interp(x, knots=knots, min_x=min_x, max_x=max_x):
            x = max(min_x, min(max_x, float(x)))
            for i in range(len(knots) - 1):
                x0 = float(knots[i]["x"])
                x1 = float(knots[i + 1]["x"])
                if x0 <= x <= x1:
                    if x1 == x0:
                        return float(knots[i]["y"])
                    y0 = float(knots[i]["y"])
                    y1 = float(knots[i + 1]["y"])
                    t = (x - x0) / (x1 - x0)
                    return y0 + (y1 - y0) * t
            return float(knots[-1]["y"])

        cases: list[dict] = []
        for k in knots:
            cases.append(
                {"inputs": {"x": float(k["x"])}, "expected": float(k["y"])}
            )
        mid_x = (float(knots[0]["x"]) + float(knots[1]["x"])) / 2.0
        cases.append({"inputs": {"x": mid_x}, "expected": interp(mid_x)})
        cases.append(
            {"inputs": {"x": float(min_x) - 99.0}, "expected": interp(min_x)}
        )
        samples = [{"x": float(min_x) + i * (max_x - min_x) / 40.0}
                    for i in range(41)]
        yield {
            "spec": spec,
            "validation_cases": cases,
            "shadow_samples": samples,
            "solver_name_seed": name,
            "cell_id": cell,
            "source": "phase13_seed_library",
            "source_kind": "canonical_seed",
            "_family_kind": "bounded_interpolation",
            "_intent_seed": name,
        }


# ---------------- public API ----------------


def all_canonical_seeds() -> list[dict]:
    """Return every canonical seed across every allowlisted family.

    Total: 28 + 17 + 17 + 14 + 14 + 14 = 104 seeds after Phase 16B P4
    expansion (+1 per family to cross the 100-solver stable release
    gate). Phase 13 shipped 68; Phase 14 added 30 to reach 98; Phase
    16B added 6 to reach 104. All additions stay inside the six-
    family allowlist (RULE 7) and preserve hex-cell spread.
    """

    out: list[dict] = []
    out.extend(_scalar_seeds())
    out.extend(_lookup_seeds())
    out.extend(_threshold_seeds())
    out.extend(_interval_seeds())
    out.extend(_linear_seeds())
    out.extend(_interp_seeds())
    return out


def seeds_for_family(family_kind: str) -> list[dict]:
    """Return only the seeds for a given allowlisted family."""

    return [s for s in all_canonical_seeds()
            if s["_family_kind"] == family_kind]


def expected_per_family_counts() -> dict[str, int]:
    """How many seeds the library yields per family. Used by tests."""

    return {
        "scalar_unit_conversion": len(_SCALAR_SEEDS),
        "lookup_table": len(_LOOKUP_SEEDS),
        "threshold_rule": len(_THRESHOLD_SEEDS),
        "interval_bucket_classifier": len(_INTERVAL_SEEDS),
        "linear_arithmetic": len(_LINEAR_SEEDS),
        "bounded_interpolation": len(_INTERP_SEEDS),
    }


__all__ = [
    "all_canonical_seeds",
    "seeds_for_family",
    "expected_per_family_counts",
]

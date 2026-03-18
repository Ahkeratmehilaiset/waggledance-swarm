"""Optimization engine — cost minimization and scheduling for the autonomy core.

Provides optimization capabilities for:
- Energy cost minimization (spot-price scheduling)
- Resource allocation
- Simple constraint-based scheduling
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class OptimizationResult:
    """Result of an optimization computation."""
    success: bool
    objective_value: float = 0.0
    solution: Dict[str, Any] = field(default_factory=dict)
    schedule: List[Dict[str, Any]] = field(default_factory=list)
    improvement_pct: float = 0.0
    method: str = ""
    compute_time_ms: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "objective_value": round(self.objective_value, 4),
            "solution": self.solution,
            "schedule": self.schedule,
            "improvement_pct": round(self.improvement_pct, 2),
            "method": self.method,
            "compute_time_ms": round(self.compute_time_ms, 2),
        }


class OptimizationEngine:
    """Optimization engine for cost and resource problems.

    Provides greedy and heuristic solvers for common optimization tasks.
    No external optimizer dependencies — pure Python implementations.
    """

    def __init__(self):
        self._history: List[OptimizationResult] = []
        self._max_history = 100

    def minimize_energy_cost(self, hourly_prices: List[float],
                             required_kwh: float,
                             max_power_kw: float = 3.0,
                             min_hours: int = 1) -> OptimizationResult:
        """Schedule energy consumption to minimize cost given hourly spot prices.

        Greedy: pick the cheapest hours first.
        """
        t0 = time.monotonic()
        if not hourly_prices or required_kwh <= 0:
            return OptimizationResult(success=False)

        indexed = list(enumerate(hourly_prices))
        indexed.sort(key=lambda x: x[1])  # sort by price ascending

        hours_needed = max(min_hours, int(required_kwh / max_power_kw) + 1)
        hours_needed = min(hours_needed, len(indexed))

        selected = indexed[:hours_needed]
        selected.sort(key=lambda x: x[0])  # re-sort by hour

        schedule = []
        total_cost = 0.0
        remaining = required_kwh
        for hour_idx, price in selected:
            kwh_this_hour = min(max_power_kw, remaining)
            cost = kwh_this_hour * price / 100.0  # cents → EUR
            total_cost += cost
            remaining -= kwh_this_hour
            schedule.append({
                "hour": hour_idx,
                "power_kw": round(kwh_this_hour, 2),
                "price_c_kwh": price,
                "cost_eur": round(cost, 4),
            })
            if remaining <= 0:
                break

        naive_cost = sum(
            min(max_power_kw, required_kwh - i * max_power_kw) * hourly_prices[i] / 100
            for i in range(min(hours_needed, len(hourly_prices)))
            if required_kwh - i * max_power_kw > 0
        )
        improvement = ((naive_cost - total_cost) / naive_cost * 100) if naive_cost > 0 else 0

        result = OptimizationResult(
            success=True,
            objective_value=total_cost,
            solution={"total_cost_eur": round(total_cost, 4),
                      "hours_used": len(schedule),
                      "avg_price_c_kwh": round(
                          sum(s["price_c_kwh"] for s in schedule) / len(schedule), 2
                      ) if schedule else 0},
            schedule=schedule,
            improvement_pct=improvement,
            method="greedy_cheapest_hours",
            compute_time_ms=(time.monotonic() - t0) * 1000,
        )
        self._record(result)
        return result

    def allocate_resources(self, tasks: List[Dict[str, Any]],
                           capacity: float) -> OptimizationResult:
        """Allocate limited capacity across tasks by priority.

        Each task: {id, priority, demand, min_allocation}
        Greedy: highest priority first, respecting minimums.
        """
        t0 = time.monotonic()
        if not tasks:
            return OptimizationResult(success=False)

        sorted_tasks = sorted(tasks, key=lambda t: t.get("priority", 0), reverse=True)
        remaining = capacity
        allocations = {}

        for task in sorted_tasks:
            task_id = task.get("id", "unknown")
            demand = task.get("demand", 0)
            min_alloc = task.get("min_allocation", 0)
            alloc = min(demand, remaining)
            if alloc < min_alloc:
                alloc = 0  # can't meet minimum
            remaining -= alloc
            allocations[task_id] = round(alloc, 4)

        total_demand = sum(t.get("demand", 0) for t in tasks)
        total_allocated = sum(allocations.values())
        utilization = total_allocated / capacity if capacity > 0 else 0

        result = OptimizationResult(
            success=True,
            objective_value=utilization,
            solution={
                "allocations": allocations,
                "total_demand": round(total_demand, 2),
                "total_allocated": round(total_allocated, 2),
                "capacity": capacity,
                "utilization_pct": round(utilization * 100, 1),
            },
            method="priority_greedy",
            compute_time_ms=(time.monotonic() - t0) * 1000,
        )
        self._record(result)
        return result

    def schedule_tasks(self, tasks: List[Dict[str, Any]],
                       time_slots: int = 24) -> OptimizationResult:
        """Schedule tasks into time slots respecting dependencies and deadlines.

        Each task: {id, duration_slots, deadline_slot, priority, depends_on}
        Simple earliest-deadline-first with dependency ordering.
        """
        t0 = time.monotonic()
        if not tasks:
            return OptimizationResult(success=False)

        completed: Dict[str, int] = {}  # task_id -> completion_slot
        schedule = []
        current_slot = 0

        remaining = list(tasks)
        remaining.sort(key=lambda t: (t.get("deadline_slot", time_slots),
                                      -t.get("priority", 0)))

        while remaining and current_slot < time_slots:
            scheduled_any = False
            for task in list(remaining):
                task_id = task.get("id", "unknown")
                duration = task.get("duration_slots", 1)
                deps = task.get("depends_on", [])

                deps_met = all(d in completed for d in deps)
                if not deps_met:
                    continue

                end_slot = current_slot + duration
                if end_slot > time_slots:
                    continue

                schedule.append({
                    "task_id": task_id,
                    "start_slot": current_slot,
                    "end_slot": end_slot,
                    "duration": duration,
                })
                completed[task_id] = end_slot
                remaining.remove(task)
                current_slot = end_slot
                scheduled_any = True
                break

            if not scheduled_any:
                current_slot += 1

        result = OptimizationResult(
            success=len(remaining) == 0,
            objective_value=len(completed) / len(tasks) if tasks else 0,
            solution={
                "scheduled": len(completed),
                "total": len(tasks),
                "unscheduled": [t.get("id", "?") for t in remaining],
            },
            schedule=schedule,
            method="earliest_deadline_first",
            compute_time_ms=(time.monotonic() - t0) * 1000,
        )
        self._record(result)
        return result

    def _record(self, result: OptimizationResult) -> None:
        self._history.append(result)
        if len(self._history) > self._max_history:
            self._history = self._history[-self._max_history:]

    def stats(self) -> Dict[str, Any]:
        total = len(self._history)
        successes = sum(1 for r in self._history if r.success)
        return {
            "total_optimizations": total,
            "success_rate": successes / total if total else 0.0,
        }

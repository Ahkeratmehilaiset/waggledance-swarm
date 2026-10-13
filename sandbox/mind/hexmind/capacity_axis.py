"""CAPACITY AXIS: can the memory learn usage toward limits from the observer data? Forecast methods, frozen first.

usage: python -B sandbox/mind/hexmind/capacity_axis.py --series <capacity-series.json> --out <report.json>

Input: the capacity observations the live Mind producer's read-only capacity hook saw (one row per provider, window,
observation time and thread; ``used_percent`` is an integer, ``resets_at`` a unix time). Rows of all threads are one
account pool: they are merged and deduplicated by observation time.

Methods (the lanes' proposals, 2026-10-13 ~18:06-18:20Z, plus baselines):

* ``persist``   the last reading stays (baseline);
* ``lsq12`` / ``lsq30``  least-squares slope over the last 12 / 30 readings (claude-rco-2, Grok: "a fixed N");
* ``window``    average burn since the window opened: used / elapsed of the window (five_hour = 300 min,
                seven_day = 10080 min; window start = resets_at minus the duration).

Every forecast is clipped to 0..100 and skipped when the window resets before the horizon. A forecast at time t for
t + h is scored against the first reading at or after t + h (within 5 minutes). Learning (Grok's smallest
mechanism): each method's interval half-width at t is the 90th percentile of its own absolute errors of forecasts
already RESOLVED before t (at least 10, else no interval); coverage counts actuals inside the interval. Limit alerts:
a method alerts when its forecast at the reset time reaches 100; no window reached 100 in the data, so only false
alerts are measurable and misses are UNKNOWN.

Writes only ``--out``. No network, no provider call, no bridge event, no gate code.
"""

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

HORIZONS = (30, 60, 120)
DURATION = {"five_hour": 300, "seven_day": 10080}
TOLERANCE = 5
MIN_RESOLVED = 10


def parse(ts):
    return dt.datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp() / 60.0


def series(rows, provider, window):
    points = {}
    for row in rows:
        if row["provider"] == provider and row["window"] == window and row.get("observed_at"):
            points[parse(row["observed_at"])] = (int(row["used_percent"]), int(row["resets_at"]) / 60.0)
    return sorted((t, used, reset) for t, (used, reset) in points.items())


def lsq(points):
    n = len(points)
    if n < 2:
        return 0.0
    mt = sum(t for t, _ in points) / n
    mu = sum(u for _, u in points) / n
    var = sum((t - mt) ** 2 for t, _ in points)
    return sum((t - mt) * (u - mu) for t, u in points) / var if var else 0.0


def forecast(method, history, window, h):
    t, used, reset = history[-1]
    if method == "persist":
        rate = 0.0
    elif method.startswith("lsq"):
        rate = lsq([(p[0], p[1]) for p in history[-int(method[3:]):] if p[2] == reset])
    else:
        elapsed = t - (reset - DURATION[window])
        rate = used / elapsed if elapsed > 0 else 0.0
    return max(0.0, min(100.0, used + rate * h)), rate


def evaluate(points, window, methods):
    out = {}
    for method in methods:
        errors = {h: [] for h in HORIZONS}
        resolved = {h: [] for h in HORIZONS}
        inside = {h: [0, 0] for h in HORIZONS}
        alerts = 0
        for i in range(2, len(points)):
            t, _, reset = points[i]
            history = points[:i + 1]
            at_reset, _ = forecast(method, history, window, reset - t)
            alerts += at_reset >= 100.0
            for h in HORIZONS:
                if reset < t + h:
                    continue
                actual = next((p for p in points[i + 1:] if p[0] >= t + h), None)
                if actual is None or actual[0] > t + h + TOLERANCE or actual[2] != reset:
                    continue
                value, _ = forecast(method, history, window, h)
                past = sorted(e for (when, e) in resolved[h] if when <= t)
                if len(past) >= MIN_RESOLVED:
                    half = past[min(len(past) - 1, int(0.9 * len(past)))]
                    inside[h][0] += abs(actual[1] - value) <= half
                    inside[h][1] += 1
                errors[h].append(abs(actual[1] - value))
                resolved[h].append((actual[0], abs(actual[1] - value)))
        out[method] = {
            f"h{h}": {"n": len(errors[h]), "mae": round(sum(errors[h]) / len(errors[h]), 3) if errors[h] else None,
                      "max_error": max(errors[h]) if errors[h] else None,
                      "interval_coverage": round(inside[h][0] / inside[h][1], 3) if inside[h][1] else None,
                      "interval_n": inside[h][1]} for h in HORIZONS}
        out[method]["false_limit_alerts"] = alerts
        out[method]["forecasts_at_reset"] = len(points) - 2
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--series", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    data = json.loads(args.series.read_text(encoding="utf-8"))
    rows = data["rows"]
    report = {"schema": "wd.bridge-mind-capacity-axis.v1", "authority": "none", "windows": {}}
    for provider, window in sorted({(r["provider"], r["window"]) for r in rows}):
        points = series(rows, provider, window)
        report["windows"][f"{provider}/{window}"] = {
            "readings": len(points), "first": points[0][1] if points else None, "last": points[-1][1] if points else None,
            "resets_seen": len({p[2] for p in points}), "hit_100": any(p[1] >= 100 for p in points),
            "methods": evaluate(points, window, ("persist", "lsq12", "lsq30", "window"))}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=1, sort_keys=True), encoding="utf-8")
    print(json.dumps(report, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())

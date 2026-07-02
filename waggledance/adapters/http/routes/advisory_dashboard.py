# SPDX-License-Identifier: BUSL-1.1
# SPDX-FileCopyrightText: Jani Korpi / Ahkerat Mehilaiset / JKH Service
"""Read-only operator dashboard over the three advisory snapshots.

Server-rendered HTML status tiles for ENG-01 (spot electricity), AIR-01
(air quality) and ENG-06 (fireplace safety). The page reads the same
``data/<case>/latest_advisory.json`` snapshot files the JSON advisory routes
serve and renders them server-side:

- served under ``/api/*`` so it carries the SAME auth requirement as the JSON
  advisory routes (bearer token, or the HttpOnly ``waggle_session`` cookie for
  browser use after ``/api/auth/session`` login). The advisory telemetry is
  protected on the API side, so the dashboard must not expose it
  unauthenticated (rco-2 security review, PR #1471);
- no client-side fetch and no token embedded in the page;
- no JavaScript, no external assets, no new dependencies; a strict
  Content-Security-Policy header as defense-in-depth;
- every interpolated value is HTML-escaped;
- rendering is TOTAL: nested snapshot fields are isinstance-guarded so a
  malformed snapshot can never 500 the page (it degrades to marker-only).

The snapshot loader mirrors the advisory routes' validation semantics
(missing / empty / oversized / parse-fail / non-object / missing-marker each
degrade to a safe ``result_marker``). It is deliberately self-contained: the
AIR-01 and ENG-06 route modules land in separate PRs (#1468 / #1470), so this
page must not import them to stay merge-order independent. Once all three
advisory routes are on main, a follow-up may consolidate the loaders.

Status colors follow the reserved status palette and never carry meaning
alone — every state ships an icon glyph + text label.
"""
from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any, Mapping

from fastapi import APIRouter
from fastapi.responses import HTMLResponse


router = APIRouter(tags=["advisory-dashboard"])

# The exact snapshot paths the per-case advisory routes serve
# (each route module's DEFAULT_SNAPSHOT_PATH).
SNAPSHOT_PATHS = {
    "ENG-01": Path("data/eng01/latest_advisory.json"),
    "AIR-01": Path("data/air01/latest_advisory.json"),
    "ENG-06": Path("data/eng06/latest_advisory.json"),
}
ADVISORY_MAX_BYTES = 1_000_000

NO_ADVISORY_YET = "NO_ADVISORY_YET"
SNAPSHOT_REFUSED = "SNAPSHOT_REFUSED"

# Reserved status palette (validated for light #fcfcfb / dark #1a1a19
# surfaces). Sub-3:1 light-surface contrast on warning/serious is mitigated
# by the mandatory icon + text label pairing: color never carries meaning alone.
_STATUS_GOOD = "#0ca30c"
_STATUS_WARNING = "#fab219"
_STATUS_SERIOUS = "#ec835a"
_STATUS_CRITICAL = "#d03b3b"


# No JS and full output escaping make this belt-and-braces, not load-bearing.
_CSP = "default-src 'none'; style-src 'unsafe-inline'"


@router.get("/api/dashboard/advisories", response_class=HTMLResponse)
def get_advisory_dashboard() -> HTMLResponse:
    """Render the read-only advisory status dashboard."""
    snapshots = {
        case_id: _load_snapshot(path)
        for case_id, path in SNAPSHOT_PATHS.items()
    }
    return HTMLResponse(
        render_advisories_dashboard_html(snapshots),
        headers={"Content-Security-Policy": _CSP},
    )


def _load_snapshot(path: Path) -> dict[str, Any]:
    """Load one snapshot with the advisory routes' validation semantics."""
    try:
        if not path.exists() or not path.is_file():
            return _no_advisory("missing")
        size = path.stat().st_size
        if size == 0:
            return _no_advisory("empty")
        if size > ADVISORY_MAX_BYTES:
            return _refused("size_exceeded")
        raw = path.read_bytes()
    except OSError:
        return _refused("read_failed")

    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return _refused("parse_failed")
    if not isinstance(parsed, dict):
        return _refused("not_object")
    marker = parsed.get("result_marker")
    if not isinstance(marker, str) or not marker.strip():
        return _refused("missing_result_marker")
    return parsed


def _no_advisory(reason: str) -> dict[str, Any]:
    return {"result_marker": NO_ADVISORY_YET, "reason": reason}


def _refused(reason: str) -> dict[str, Any]:
    return {"result_marker": SNAPSHOT_REFUSED, "reason": reason}


_CARD_TITLES = {
    "ENG-01": "Spot electricity — cheapest hours",
    "AIR-01": "Indoor air quality",
    "ENG-06": "Fireplace safety",
}


def render_advisories_dashboard_html(
    snapshots: Mapping[str, Mapping[str, Any]],
) -> str:
    cards = "\n".join(
        _render_card(case_id, snapshots.get(case_id))
        for case_id in ("ENG-01", "AIR-01", "ENG-06")
    )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>WaggleDance — Advisory Dashboard</title>
<style>
  :root {{
    --surface: #fcfcfb;
    --tile: #ffffff;
    --ink: #1a1a19;
    --ink-secondary: #55554f;
    --ink-muted: #8a8a82;
    --border: #e4e4df;
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{
      --surface: #1a1a19;
      --tile: #232322;
      --ink: #ececea;
      --ink-secondary: #b5b5ad;
      --ink-muted: #83837b;
      --border: #3a3a37;
    }}
  }}
  body {{
    margin: 0; padding: 24px;
    background: var(--surface); color: var(--ink);
    font: 15px/1.45 system-ui, -apple-system, "Segoe UI", sans-serif;
  }}
  h1 {{ font-size: 19px; margin: 0 0 4px; }}
  .sub {{ color: var(--ink-muted); font-size: 13px; margin: 0 0 20px; }}
  .grid {{ display: grid; gap: 16px;
          grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); }}
  .card {{
    background: var(--tile); border: 1px solid var(--border);
    border-left-width: 4px; border-radius: 8px; padding: 16px 18px;
  }}
  .case {{ color: var(--ink-muted); font-size: 12px;
          letter-spacing: 0.04em; }}
  .title {{ font-size: 15px; font-weight: 600; margin: 2px 0 10px; }}
  .status {{ display: inline-block; font-size: 13px; font-weight: 600;
            padding: 2px 8px; border-radius: 999px;
            border: 1px solid var(--border); }}
  .metrics {{ margin: 12px 0 0; padding: 0; list-style: none; }}
  .metrics li {{ display: flex; justify-content: space-between;
               gap: 12px; padding: 3px 0; font-size: 14px; }}
  .metrics .k {{ color: var(--ink-secondary); }}
  .metrics .v {{ font-variant-numeric: tabular-nums; }}
  .reason {{ color: var(--ink-secondary); font-size: 13px; margin-top: 10px; }}
</style>
</head>
<body>
<h1>WaggleDance — Advisory Dashboard</h1>
<p class="sub">Latest scheduler-written solver advisories (read-only snapshots).</p>
<div class="grid">
{cards}
</div>
</body>
</html>
"""


def _render_card(case_id: str, snapshot: Any) -> str:
    if not isinstance(snapshot, Mapping):
        snapshot = _no_advisory("missing")
    marker = snapshot.get("result_marker")
    marker = marker if isinstance(marker, str) and marker.strip() else "UNKNOWN"

    accent, icon, label = _status_for_marker(marker)
    title = _CARD_TITLES.get(case_id, case_id)
    metrics = _metric_rows(case_id, snapshot)
    reason = snapshot.get("reason") or snapshot.get("refusal_reason")
    reason_html = (
        f'<p class="reason">{_esc(reason)}</p>'
        if isinstance(reason, str) and reason.strip()
        else ""
    )
    return f"""<section class="card" style="border-left-color: {accent}">
  <div class="case">{_esc(case_id)}</div>
  <div class="title">{_esc(title)}</div>
  <span class="status"><span aria-hidden="true">{icon}</span> {_esc(label)}</span>
  {metrics}
  {reason_html}
</section>"""


def _status_for_marker(marker: str) -> tuple[str, str, str]:
    """Map a result marker to (accent color, icon glyph, text label).

    Unknown markers fall to the serious/attention state (fail-closed): an
    unrecognized state must demand a look, never render as calm.
    """
    if marker == "OK":
        return (_STATUS_GOOD, "✓", "OK")
    if marker == NO_ADVISORY_YET:
        return ("var(--border)", "⋯", "No advisory yet")
    if "EMERGENCY" in marker:
        return (_STATUS_CRITICAL, "⛔", marker)
    if "WARNING" in marker:
        return (_STATUS_WARNING, "⚠", marker)
    return (_STATUS_SERIOUS, "✖", marker)


def _metric_rows(case_id: str, snapshot: Mapping[str, Any]) -> str:
    rows: list[tuple[str, str]] = []
    if case_id == "ENG-01":
        rows = _eng01_rows(snapshot)
    elif case_id == "AIR-01":
        rows = _air01_rows(snapshot)
    elif case_id == "ENG-06":
        rows = _eng06_rows(snapshot)
    if not rows:
        return ""
    items = "\n".join(
        f'    <li><span class="k">{_esc(k)}</span>'
        f'<span class="v">{_esc(v)}</span></li>'
        for k, v in rows
    )
    return f'<ul class="metrics">\n{items}\n  </ul>'


def _eng01_rows(snapshot: Mapping[str, Any]) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    top_hours = snapshot.get("top_hours")
    if isinstance(top_hours, list):
        for entry in top_hours[:3]:
            if not isinstance(entry, Mapping):
                continue
            hour = entry.get("hour_utc")
            price = entry.get("price_eur_per_kwh")
            if isinstance(hour, str) and isinstance(price, (int, float)):
                rows.append((hour, f"{price} EUR/kWh"))
    return rows


def _air01_rows(snapshot: Mapping[str, Any]) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    risk = snapshot.get("risk_level")
    if isinstance(risk, str) and risk.strip():
        rows.append(("Risk level", risk))
    triggered = snapshot.get("triggered_metrics")
    if isinstance(triggered, list):
        names = [
            str(item.get("metric"))
            for item in triggered
            if isinstance(item, Mapping) and isinstance(item.get("metric"), str)
        ]
        if names:
            rows.append(("Triggered", ", ".join(names)))
    device = snapshot.get("device_id")
    if isinstance(device, str) and device.strip():
        rows.append(("Device", device))
    return rows


def _eng06_rows(snapshot: Mapping[str, Any]) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    metrics = snapshot.get("metrics")
    if isinstance(metrics, Mapping):
        labels = (
            ("fire_event_count_30d", "Fires (30d)"),
            ("days_with_fire", "Days with fire"),
            ("peak_chimney_temp_c", "Peak chimney °C"),
            ("average_chimney_temp_c", "Avg chimney °C"),
        )
        for key, label in labels:
            value = metrics.get(key)
            if isinstance(value, (int, float)):
                rows.append((label, str(value)))
    return rows


def _esc(value: Any) -> str:
    return html.escape(str(value), quote=True)


__all__ = [
    "ADVISORY_MAX_BYTES",
    "NO_ADVISORY_YET",
    "SNAPSHOT_PATHS",
    "SNAPSHOT_REFUSED",
    "get_advisory_dashboard",
    "render_advisories_dashboard_html",
    "router",
]

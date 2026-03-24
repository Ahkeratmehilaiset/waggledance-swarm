"""GET /api/analytics/* — learning metrics visualization data."""
import json
import logging
from collections import Counter, defaultdict
from pathlib import Path
from fastapi import APIRouter

log = logging.getLogger("waggledance.analytics")

router = APIRouter()

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_METRICS_FILE = _PROJECT_ROOT / "data" / "learning_metrics.jsonl"
_WEEKLY_FILE = _PROJECT_ROOT / "data" / "weekly_report.json"
_MORNING_FILE = _PROJECT_ROOT / "data" / "morning_reports.jsonl"


def _load_metrics(max_lines: int = 6000) -> list[dict]:
    """Load learning_metrics.jsonl (last max_lines)."""
    if not _METRICS_FILE.exists():
        return []
    try:
        rows = []
        with open(_METRICS_FILE, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
        return rows[-max_lines:]
    except Exception as e:
        log.warning("Failed to load metrics: %s", e)
        return []


@router.get("/api/analytics/trends")
async def analytics_trends():
    """Hallucination rate, cache hit rate, response time — 7-day trend."""
    rows = _load_metrics()
    chat_rows = [r for r in rows if r.get("method") or r.get("route")]

    if not chat_rows:
        return {"days": [], "halluc_trend": [], "cache_trend": [], "rt_trend": []}

    # Group by date
    by_day: dict[str, list] = defaultdict(list)
    for r in chat_rows:
        ts = r.get("ts", "")
        day = ts[:10] if len(ts) >= 10 else "unknown"
        by_day[day].append(r)

    days = sorted(by_day.keys())[-7:]
    halluc_trend = []
    cache_trend = []
    rt_trend = []

    for day in days:
        dr = by_day[day]
        n = len(dr)
        halluc_trend.append(round(sum(1 for r in dr if r.get("was_hallucination")) / max(n, 1), 3))
        cache_trend.append(round(sum(1 for r in dr if r.get("cache_hit")) / max(n, 1), 3))
        rt_trend.append(round(sum(r.get("response_time_ms", 0) for r in dr) / max(n, 1)))

    return {
        "days": days,
        "halluc_trend": halluc_trend,
        "cache_trend": cache_trend,
        "rt_trend": rt_trend,
        "total_queries": len(chat_rows),
    }


@router.get("/api/analytics/routes")
async def analytics_routes():
    """Route breakdown — how queries are served."""
    rows = _load_metrics()
    chat_rows = [r for r in rows if r.get("route")]
    route_counts = Counter(r.get("route", "unknown") for r in chat_rows)
    method_counts = Counter(r.get("method", "unknown") for r in chat_rows)
    return {
        "routes": dict(route_counts.most_common(10)),
        "methods": dict(method_counts.most_common(10)),
        "total": len(chat_rows),
    }


@router.get("/api/analytics/models")
async def analytics_models():
    """Model usage breakdown — which models handle queries."""
    rows = _load_metrics()
    chat_rows = [r for r in rows if r.get("model_used") is not None]
    model_counts = Counter(r.get("model_used", "unknown") or "micro/cache" for r in chat_rows)
    return {
        "models": dict(model_counts.most_common(10)),
        "total": len(chat_rows),
    }


@router.get("/api/analytics/facts")
async def analytics_facts():
    """Fact growth timeline from enrichment events."""
    rows = _load_metrics()
    enrich_rows = [r for r in rows if r.get("event") == "enrichment_cycle"]

    by_day: dict[str, int] = defaultdict(int)
    for r in enrich_rows:
        ts = r.get("ts", "")
        day = ts[:10] if len(ts) >= 10 else "unknown"
        by_day[day] += r.get("facts_stored", 0) + r.get("ext_stored", 0)

    days = sorted(by_day.keys())[-14:]
    counts = [by_day[d] for d in days]

    # Category breakdown from morning reports
    categories: dict[str, int] = {}
    if _MORNING_FILE.exists():
        try:
            with open(_MORNING_FILE, encoding="utf-8") as f:
                lines = [l.strip() for l in f if l.strip()]
            if lines:
                last = json.loads(lines[-1])
                categories = last.get("per_agent", {})
        except Exception as e:
            log.warning("Failed to read morning reports: %s", e)

    return {
        "days": days,
        "facts_per_day": counts,
        "total_enriched": sum(counts),
        "per_agent": dict(sorted(categories.items(), key=lambda x: -x[1])[:20]) if categories else {},
    }

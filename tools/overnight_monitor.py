"""
Overnight production monitor.
Sends queries, checks health, logs metrics, detects anomalies.
Runs for DURATION_HOURS then writes report.
"""
import os
import time
import json
import random
import httpx
import logging
from datetime import datetime, timezone
from pathlib import Path

DURATION_HOURS = 10
CYCLE_INTERVAL_SECONDS = 30  # check every 30 seconds
QUERY_INTERVAL_SECONDS = 120  # send test query every 2 minutes
BASE_URL = "http://localhost:8000"
API_KEY = os.environ.get("WAGGLE_API_KEY", "overnight-test-key-2026")
LOG_FILE = Path("logs/overnight_metrics.jsonl")
REPORT_FILE = Path("OVERNIGHT_PRODUCTION_REPORT.md")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("overnight")

# Diverse query bank — mix of types and languages
QUERIES = [
    # Math
    "What is 2+2?",
    "laske 150 * 0.08",
    "paljonko on 15% sadasta",
    "calculate 500 / 7",
    "what is 12 squared",
    # Thermal
    "is 45 degrees too hot?",
    "frost risk at -5C tonight",
    "convert 72F to celsius",
    "onko pakkasvaara",
    # Retrieval
    "varroa schedule",
    "feeding schedule",
    "what happened yesterday",
    "show me recent alerts",
    "energy cost this month",
    # Optimization
    "optimize heating schedule",
    "minimize energy cost",
    "best time to run washing machine",
    # Stats
    "compare this week to last week",
    "what is the trend",
    "average temperature last 7 days",
    # General
    "check sensor status",
    "system health",
    "what should I do today",
    "morning report",
]


class MetricsCollector:
    def __init__(self):
        self.total_queries = 0
        self.successful_queries = 0
        self.failed_queries = 0
        self.total_latency_ms = 0
        self.errors = []
        self.health_checks = 0
        self.health_failures = 0
        self.quality_distribution = {"gold": 0, "silver": 0, "bronze": 0, "quarantine": 0}
        self.capabilities_used = {}
        self.start_time = time.time()
        self.night_learning_results = []
        self.restarts = 0
        # v3.2 metrics
        self.v32_endpoint_checks = 0
        self.v32_endpoint_failures = 0
        self.attention_snapshots = []
        self.uncertainty_snapshots = []

    def record_query(self, query, response, latency_ms):
        self.total_queries += 1
        if response and response.get("response"):
            self.successful_queries += 1
            cap = response.get("source", "unknown")
            self.capabilities_used[cap] = self.capabilities_used.get(cap, 0) + 1
            conf = response.get("confidence", 0)
            if conf >= 0.9:
                self.quality_distribution["gold"] += 1
            elif conf >= 0.7:
                self.quality_distribution["silver"] += 1
            elif conf >= 0.4:
                self.quality_distribution["bronze"] += 1
            else:
                self.quality_distribution["quarantine"] += 1
        else:
            self.failed_queries += 1
        self.total_latency_ms += latency_ms

    def record_error(self, error_type, detail):
        self.errors.append({
            "time": datetime.now(timezone.utc).isoformat(),
            "type": error_type,
            "detail": str(detail)[:500],
        })

    def avg_latency(self):
        if self.total_queries == 0:
            return 0
        return self.total_latency_ms / self.total_queries

    def uptime_hours(self):
        return (time.time() - self.start_time) / 3600


def check_health(client, metrics):
    """Check runtime health endpoint."""
    try:
        r = client.get(f"{BASE_URL}/health", timeout=5)
        metrics.health_checks += 1
        if r.status_code != 200:
            metrics.health_failures += 1
            metrics.record_error("health_check", f"status={r.status_code}")
            return False
        return True
    except Exception as e:
        metrics.health_checks += 1
        metrics.health_failures += 1
        metrics.record_error("health_unreachable", str(e))
        return False


def send_query(client, metrics, query):
    """Send a query to the runtime API."""
    headers = {"Authorization": f"Bearer {API_KEY}"}
    try:
        t0 = time.time()
        r = client.post(f"{BASE_URL}/api/chat", json={"query": query},
                        headers=headers, timeout=30)
        latency_ms = (time.time() - t0) * 1000

        if r.status_code == 200:
            data = r.json()
            metrics.record_query(query, data, latency_ms)
            return data
        else:
            metrics.record_error("query_http_error", f"{r.status_code}: {query}")
            metrics.failed_queries += 1
            return None
    except Exception as e:
        metrics.record_error("query_exception", f"{query}: {e}")
        metrics.failed_queries += 1
        return None


def log_metric(metrics, event_type, detail=None):
    """Append metric to JSONL log."""
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event": event_type,
        "uptime_h": round(metrics.uptime_hours(), 2),
        "total_queries": metrics.total_queries,
        "success_rate": round(metrics.successful_queries / max(metrics.total_queries, 1), 3),
        "avg_latency_ms": round(metrics.avg_latency(), 1),
        "errors_total": len(metrics.errors),
    }
    if detail:
        entry["detail"] = detail
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def trigger_night_learning(client, metrics):
    """Trigger night learning cycle via API."""
    headers = {"Authorization": f"Bearer {API_KEY}"}
    try:
        r = client.post(f"{BASE_URL}/api/autonomy/learning/run",
                        headers=headers, timeout=120)
        if r.status_code == 200:
            result = r.json()
            metrics.night_learning_results.append(result)
            return result
        else:
            metrics.record_error("night_learning", f"status={r.status_code}")
    except Exception as e:
        metrics.record_error("night_learning", str(e))
    return None


V32_ENDPOINTS = [
    "/api/autonomy/epistemic-uncertainty",
    "/api/autonomy/attention-budget",
    "/api/autonomy/dream-mode/latest",
    "/api/autonomy/memory/consolidation-stats",
    "/api/autonomy/introspection",
    "/api/autonomy/narrative",
]


def check_v32_endpoints(client, metrics):
    """Check all v3.2 autonomy endpoints are responsive."""
    headers = {"Authorization": f"Bearer {API_KEY}"}
    all_ok = True
    for ep in V32_ENDPOINTS:
        try:
            r = client.get(f"{BASE_URL}{ep}", headers=headers, timeout=10)
            metrics.v32_endpoint_checks += 1
            if r.status_code != 200:
                metrics.v32_endpoint_failures += 1
                metrics.record_error("v32_endpoint", f"{ep} status={r.status_code}")
                all_ok = False
            else:
                data = r.json()
                if "uncertainty" in ep and "total_uncertainty" in data:
                    metrics.uncertainty_snapshots.append(data.get("total_uncertainty", 0))
                if "attention" in ep and "buckets" in data:
                    metrics.attention_snapshots.append(data)
        except Exception as e:
            metrics.v32_endpoint_checks += 1
            metrics.v32_endpoint_failures += 1
            metrics.record_error("v32_endpoint", f"{ep}: {e}")
            all_ok = False
    return all_ok


def write_report(metrics):
    """Write final markdown report."""
    duration_h = round(metrics.uptime_hours(), 1)
    success_rate = round(100 * metrics.successful_queries / max(metrics.total_queries, 1), 1)

    report = f"""# Overnight Production Report — {datetime.now().strftime('%Y-%m-%d')}

## Summary
- Duration: {duration_h} hours
- Total queries sent: {metrics.total_queries}
- Successful: {metrics.successful_queries} ({success_rate}%)
- Failed: {metrics.failed_queries}
- Average latency: {round(metrics.avg_latency(), 1)} ms
- Health checks: {metrics.health_checks} ({metrics.health_failures} failures)
- Runtime restarts: {metrics.restarts}

## Quality Distribution
| Grade | Count |
|-------|-------|
| Gold | {metrics.quality_distribution['gold']} |
| Silver | {metrics.quality_distribution['silver']} |
| Bronze | {metrics.quality_distribution['bronze']} |
| Quarantine | {metrics.quality_distribution['quarantine']} |

## Capabilities Used
| Capability | Count |
|------------|-------|
"""
    for cap, count in sorted(metrics.capabilities_used.items(), key=lambda x: -x[1]):
        report += f"| {cap} | {count} |\n"

    report += "\n## Night Learning Results\n"
    for i, nl in enumerate(metrics.night_learning_results):
        report += f"- Cycle {i+1}: {json.dumps(nl, default=str)[:200]}\n"

    report += f"\n## Errors ({len(metrics.errors)} total)\n"
    for err in metrics.errors[:20]:
        report += f"- [{err['time']}] {err['type']}: {err['detail'][:100]}\n"
    if len(metrics.errors) > 20:
        report += f"- ... and {len(metrics.errors) - 20} more\n"

    v32_success = metrics.v32_endpoint_checks - metrics.v32_endpoint_failures
    v32_rate = round(100 * v32_success / max(metrics.v32_endpoint_checks, 1), 1)
    report += f"\n## v3.2 Endpoint Health\n"
    report += f"- Checks: {metrics.v32_endpoint_checks} ({v32_rate}% success)\n"
    report += f"- Failures: {metrics.v32_endpoint_failures}\n"
    if metrics.uncertainty_snapshots:
        avg_unc = round(sum(metrics.uncertainty_snapshots) / len(metrics.uncertainty_snapshots), 3)
        report += f"- Avg uncertainty: {avg_unc}\n"
    report += f"- Endpoints checked: {', '.join(V32_ENDPOINTS)}\n"

    report += "\n## Metrics Timeline\nSee `logs/overnight_metrics.jsonl` for full data.\n"

    report += "\n## Recommendations\n"
    if metrics.failed_queries > metrics.total_queries * 0.1:
        report += "- HIGH: >10% query failure rate — investigate errors above\n"
    if metrics.avg_latency() > 5000:
        report += "- HIGH: Average latency >5s — check Ollama/model performance\n"
    if metrics.health_failures > 0:
        report += f"- MEDIUM: {metrics.health_failures} health check failures — check runtime stability\n"
    if metrics.quality_distribution['quarantine'] > 0:
        report += f"- MEDIUM: {metrics.quality_distribution['quarantine']} quarantine cases — review quality gate\n"
    if not metrics.night_learning_results:
        report += "- LOW: No night learning cycles completed\n"
    if metrics.failed_queries == 0 and metrics.health_failures == 0:
        report += "- None — clean run!\n"

    REPORT_FILE.write_text(report, encoding="utf-8")
    log.info(f"Report written to {REPORT_FILE}")


def main():
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    metrics = MetricsCollector()
    client = httpx.Client()

    end_time = time.time() + DURATION_HOURS * 3600
    last_query_time = 0
    last_night_learning = 0
    cycle = 0

    log.info(f"Starting {DURATION_HOURS}h production monitor")

    while time.time() < end_time:
        cycle += 1
        now = time.time()

        # Health check every cycle
        healthy = check_health(client, metrics)

        if not healthy:
            log.warning("Runtime unhealthy — waiting 60s before retry")
            time.sleep(60)
            continue

        # Send query every QUERY_INTERVAL
        if now - last_query_time >= QUERY_INTERVAL_SECONDS:
            query = random.choice(QUERIES)
            result = send_query(client, metrics, query)
            if result:
                log.info(f"Query OK: {query[:40]} -> {result.get('source', '?')} (conf={result.get('confidence', '?')})")
            else:
                log.warning(f"Query FAIL: {query[:40]}")
            last_query_time = now

        # Check v3.2 endpoints every 30 minutes
        if cycle % 60 == 1:
            v32_ok = check_v32_endpoints(client, metrics)
            if not v32_ok:
                log.warning("Some v3.2 endpoints unhealthy")

        # Trigger night learning every 2 hours
        if now - last_night_learning >= 7200:
            log.info("Triggering night learning cycle...")
            nl_result = trigger_night_learning(client, metrics)
            if nl_result:
                log.info(f"Night learning: {nl_result}")
            last_night_learning = now

        # Log metrics every 10 cycles
        if cycle % 10 == 0:
            log_metric(metrics, "periodic")

        time.sleep(CYCLE_INTERVAL_SECONDS)

    # Final
    log_metric(metrics, "final")
    write_report(metrics)
    log.info("Overnight monitoring complete")
    client.close()


if __name__ == "__main__":
    main()

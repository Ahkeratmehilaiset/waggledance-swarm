#!/usr/bin/env python3
"""WaggleDance overnight monitor — 10h watchdog with auto-report.

Uses synchronous requests to avoid Windows async event loop issues.
Checks health every 60s, feeds every 5min, chat every 10min.

Usage: python tools/night_monitor.py [--hours 10]
"""
import json
import logging
import os
import sys
import time
import traceback
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(message)s",
    handlers=[
        logging.FileHandler("data/night_monitor.log", encoding="utf-8", mode="a"),
        logging.StreamHandler(sys.stdout),
    ],
    force=True,
)
log = logging.getLogger("night_monitor")

BASE = "http://localhost:8000"
TOKEN = ""


def _get(path, timeout=10):
    """GET request with auth."""
    req = urllib.request.Request(f"{BASE}{path}")
    if TOKEN:
        req.add_header("Authorization", f"Bearer {TOKEN}")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def _post(path, data, timeout=30):
    """POST request with auth."""
    body = json.dumps(data).encode()
    req = urllib.request.Request(f"{BASE}{path}", data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    if TOKEN:
        req.add_header("Authorization", f"Bearer {TOKEN}")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


# ── Metrics ──────────────────────────────────────────────────
m = {
    "start": None, "cycles": 0,
    "health_ok": 0, "health_fail": 0,
    "feed_checks": 0, "feed_ok": 0,
    "electricity_prices": [], "weather_snaps": [], "rss_items": [],
    "chat_tests": 0, "chat_ok": 0, "chat_times": [],
    "agents": [], "facts": [], "heartbeats": 0,
    "errors": [],
}

TEST_MSGS = [
    "Mikä on sähkön hinta nyt?",
    "Millainen sää on Helsingissä?",
    "Kerro varroa-hoidosta",
    "Miten optimoin energiankulutusta?",
    "What is the current electricity price?",
    "Mitä uutisia on tänään?",
    "Kuinka monta agenttia on aktiivisena?",
    "Milloin kannattaa pestä pyykkiä halvalla sähköllä?",
    "Onko tänään pakkasta?",
    "Kerro mehiläisten talvehtimisesta",
]


def check_health():
    try:
        d = _get("/health", timeout=5)
        if d.get("status") == "ok":
            m["health_ok"] += 1
            return True
        m["health_fail"] += 1
        return False
    except Exception as e:
        m["health_fail"] += 1
        m["errors"].append(f"health: {e}")
        return False


def check_status():
    try:
        d = _get("/api/status")
        agents = d.get("agents", {})
        if isinstance(agents, dict):
            m["agents"].append(agents.get("total", 0))
        else:
            m["agents"].append(agents)
        m["facts"].append(d.get("memory_facts", d.get("facts", 0)))
        m["heartbeats"] = max(m["heartbeats"], d.get("heartbeat_count", 0))
    except Exception as e:
        m["errors"].append(f"status: {e}")


def check_feeds():
    m["feed_checks"] += 1
    try:
        d = _get("/api/feeds")
        feeds = d.get("feeds", {})
        now = datetime.now(timezone.utc).isoformat()
        any_ok = False

        # Electricity — data is under .stats.current_price
        elec = feeds.get("electricity", {})
        elec_stats = elec.get("stats", {})
        price_obj = elec_stats.get("current_price")
        if price_obj is not None:
            price = price_obj.get("price", price_obj) if isinstance(price_obj, dict) else price_obj
            m["feed_ok"] += 1
            m["electricity_prices"].append({"t": now, "p": price})
            log.info("Electricity: %.2f c/kWh", float(price))
            any_ok = True

        # Weather — data is under .stats.last_data
        weather = feeds.get("weather", {})
        w_stats = weather.get("stats", {})
        w_data = w_stats.get("last_data", w_stats.get("data"))
        if w_data:
            m["feed_ok"] += 1
            m["weather_snaps"].append({"t": now, "d": str(w_data)[:200]})
            log.info("Weather: %s", str(w_data)[:100])
            any_ok = True

        # RSS — items under .stats.recent_items or check total_processed
        rss = feeds.get("rss", {})
        rss_stats = rss.get("stats", {})
        items = rss_stats.get("recent_items", rss_stats.get("items", []))
        rss_count = rss_stats.get("total_stored", rss_stats.get("total_processed", 0))
        if items:
            m["feed_ok"] += 1
            for item in items[:3]:
                title = item.get("title", "")
                if title and title not in [i.get("title") for i in m["rss_items"]]:
                    m["rss_items"].append({"t": now, "title": title, "src": item.get("source", "")})
                    log.info("RSS: [%s] %s", item.get("source", ""), title[:80])
            any_ok = True
        elif rss_count > 0:
            m["feed_ok"] += 1
            log.info("RSS: %d items stored (no recent_items list)", rss_count)
            any_ok = True

        if not any_ok:
            log.warning("Feeds returned but no data parsed — check API response structure")

    except Exception as e:
        m["errors"].append(f"feeds: {e}")


def test_chat(msg):
    t0 = time.perf_counter()
    try:
        d = _post("/api/chat", {"message": msg})
        ms = (time.perf_counter() - t0) * 1000
        m["chat_tests"] += 1
        resp = d.get("response", "")
        if resp:
            m["chat_ok"] += 1
            m["chat_times"].append(ms)
            log.info("Chat %.0fms: %s → %s", ms, msg[:30], resp[:80])
        else:
            m["errors"].append(f"chat empty: {msg[:30]}")
    except Exception as e:
        m["chat_tests"] += 1
        m["errors"].append(f"chat: {e}")


def write_report():
    dur = (time.time() - m["start"]) / 3600 if m["start"] else 0
    total_health = m["health_ok"] + m["health_fail"]
    avg_chat = sum(m["chat_times"]) / len(m["chat_times"]) if m["chat_times"] else 0
    max_agents = max(m["agents"]) if m["agents"] else 0
    max_facts = max(m["facts"]) if m["facts"] else 0

    r = f"""# WaggleDance Overnight Report

**Started:** {datetime.fromtimestamp(m['start']).strftime('%Y-%m-%d %H:%M') if m['start'] else 'N/A'}
**Duration:** {dur:.1f} hours | **Cycles:** {m['cycles']}

## Health
| Metric | Value |
|--------|-------|
| Uptime | {m['health_ok']}/{total_health} ({m['health_ok']/max(total_health,1)*100:.0f}%) |
| Agents | {max_agents} |
| Facts | {max_facts} |
| Heartbeats | {m['heartbeats']} |

## Data Feeds ({m['feed_ok']}/{m['feed_checks']*3} checks OK)

### Electricity Prices
| Time | Price |
|------|-------|
"""
    for ep in m["electricity_prices"][-48:]:
        r += f"| {ep['t'][:19]} | {ep['p']} |\n"

    r += "\n### Weather\n"
    for ws in m["weather_snaps"][-12:]:
        r += f"- {ws['t'][:19]}: {ws['d'][:120]}\n"

    r += "\n### RSS Headlines\n"
    for ri in m["rss_items"][-30:]:
        r += f"- **[{ri.get('src','')}]** {ri.get('title','')}\n"

    r += f"""
## Chat Tests ({m['chat_ok']}/{m['chat_tests']} OK)
| Metric | Value |
|--------|-------|
| Avg response | {avg_chat:.0f} ms |
| Min / Max | {min(m['chat_times'], default=0):.0f} / {max(m['chat_times'], default=0):.0f} ms |

## Errors ({len(m['errors'])} total)
"""
    unique = list(dict.fromkeys(m["errors"]))
    for e in unique[-30:]:
        r += f"- `{str(e)[:150]}`\n"

    r += "\n---\n*Auto-generated by night_monitor.py*\n"
    Path("data/overnight_monitor_report.md").write_text(r, encoding="utf-8")
    log.info("Report written (%d lines)", r.count("\n"))


def main():
    import argparse
    global TOKEN
    parser = argparse.ArgumentParser()
    parser.add_argument("--hours", type=float, default=10.0)
    args = parser.parse_args()

    m["start"] = time.time()
    end = m["start"] + args.hours * 3600

    log.info("=== Night monitor starting (%.1f hours) ===", args.hours)

    # Wait for server
    for _ in range(60):
        try:
            d = _get("/health", timeout=3)
            if d.get("status") == "ok":
                log.info("Server is up")
                break
        except Exception:
            pass
        time.sleep(5)
    else:
        log.error("Server not available after 5 min")
        return

    # Get API token
    try:
        d = _get("/api/auth/token", timeout=5)
        TOKEN = d.get("token", "")
        log.info("Token acquired (%d chars)", len(TOKEN))
    except Exception as e:
        log.warning("No token: %s", e)

    # Main loop
    while time.time() < end:
        m["cycles"] += 1
        cycle = m["cycles"]

        try:
            ok = check_health()
            if not ok:
                log.warning("HEALTH FAIL cycle %d", cycle)

            check_status()

            # Feeds every 5 cycles (5 min)
            if cycle % 5 == 0:
                check_feeds()

            # Chat every 10 cycles (10 min)
            if cycle % 10 == 0:
                msg = TEST_MSGS[cycle // 10 % len(TEST_MSGS)]
                test_chat(msg)

            # Report every 30 cycles (30 min)
            if cycle % 30 == 0:
                write_report()
                log.info("=== Cycle %d | %.1fh elapsed | %d errors ===",
                         cycle, (time.time() - m["start"]) / 3600, len(m["errors"]))

        except Exception:
            m["errors"].append(f"cycle {cycle}: {traceback.format_exc()[:200]}")
            log.error("Cycle %d: %s", cycle, traceback.format_exc()[:100])

        sys.stdout.flush()
        time.sleep(60)

    # Final report
    write_report()
    Path("data/overnight_metrics.json").write_text(
        json.dumps(m, indent=2, default=str, ensure_ascii=False), encoding="utf-8")
    log.info("=== Monitor finished after %.1fh ===", (time.time() - m["start"]) / 3600)


if __name__ == "__main__":
    main()

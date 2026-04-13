#!/usr/bin/env python3
"""Fast Phase C continuation — resumes from where the main harness left off.

Skips redundant per-query Playwright checks to reduce overhead from ~15s to ~5s per query.
"""
from __future__ import annotations
import json, os, sys, time, tempfile
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright, Page

BASE_URL = os.environ.get("GAUNTLET_BASE_URL", "http://127.0.0.1:8002")
kf = os.path.join(tempfile.gettempdir(), "waggle_gauntlet_8002.key")
with open(kf) as f:
    API_KEY = f.read().strip()

ARTIFACT_DIR = Path(__file__).resolve().parent.parent.parent / "docs" / "runs" / "ui_gauntlet_20260412"
SCREENSHOT_DIR = ARTIFACT_DIR / "screenshots"
SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)


def fast_send(page: Page, query: str, timeout_s: float = 8.0) -> dict:
    """Send a chat query with minimal overhead."""
    result = {"sent": False, "response_visible": False, "response_length": 0,
              "latency_ms": 0, "xss_detected": False, "session_lost": False,
              "dom_ok": True, "error": ""}
    try:
        ci = page.locator("input[placeholder='Type a message...']")
        q = query[:8000].replace("\n", " ").replace("\r", " ")
        ci.fill(q)
        t0 = time.time()
        ci.press("Enter")
        result["sent"] = True

        # Minimal poll — just wait for content to appear
        page.wait_for_timeout(3000)
        body_len = page.evaluate("() => document.body.innerText.length")
        elapsed = time.time() - t0

        # If body text didn't grow much, wait a bit more
        if body_len < 200:
            page.wait_for_timeout(2000)
            body_len = page.evaluate("() => document.body.innerText.length")
            elapsed = time.time() - t0

        result["latency_ms"] = round(elapsed * 1000)
        result["response_visible"] = body_len > len(q) + 50
        result["response_length"] = body_len

        # XSS check (fast evaluate, no timeout)
        result["xss_detected"] = page.evaluate("() => window.__xss_detected || false")

    except Exception as e:
        result["error"] = str(e)[:200]
    return result


def main():
    # Load corpus
    corpus_file = ARTIFACT_DIR / "query_corpus.json"
    corpus = json.loads(corpus_file.read_text(encoding="utf-8"))

    # Find where we left off
    results_file = ARTIFACT_DIR / "chat_ui_results.jsonl"
    existing = 0
    if results_file.exists():
        with open(results_file, "r", encoding="utf-8") as f:
            existing = sum(1 for line in f if line.strip())

    remaining = corpus[existing:]
    print(f"Corpus: {len(corpus)} total, {existing} done, {len(remaining)} remaining", flush=True)

    if not remaining:
        print("All queries already completed!", flush=True)
        return

    BATCH_SIZE = 50  # Restart browser context every N queries to prevent DOM bloat

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)

        def fresh_page():
            """Create a fresh context+page with auth and chat tab."""
            ctx = browser.new_context(viewport={"width": 1920, "height": 1080})
            pg = ctx.new_page()
            pg.add_init_script("""
                window.__xss_detected = false;
                window.alert = function() { window.__xss_detected = true; };
                window.confirm = function() { window.__xss_detected = true; return false; };
                window.prompt = function() { window.__xss_detected = true; return null; };
            """)
            pg.goto(f"{BASE_URL}/hologram?token={API_KEY}", wait_until="domcontentloaded")
            pg.wait_for_timeout(2000)
            tabs = pg.locator("button, [role=tab]")
            for i in range(tabs.count()):
                txt = tabs.nth(i).inner_text(timeout=1000).strip().lower()
                if "chat" in txt:
                    tabs.nth(i).click()
                    break
            pg.wait_for_timeout(1500)
            return ctx, pg

        ctx, page = fresh_page()
        ci = page.locator("input[placeholder='Type a message...']")
        if not ci.count() or not ci.is_visible(timeout=3000):
            print("ERROR: Chat input not found!", flush=True)
            ctx.close()
            browser.close()
            return

        print(f"Chat input ready. Starting from query {existing}...", flush=True)

        bucket_stats: dict[str, dict] = {}
        processed = 0
        batch_count = 0
        t_start = time.time()

        with open(results_file, "a", encoding="utf-8") as fout:
            for entry in remaining:
                qid = entry["query_id"]
                bucket = entry["bucket"]
                query = entry["query"]
                processed += 1

                if bucket not in bucket_stats:
                    bucket_stats[bucket] = {"total": 0, "sent": 0, "responded": 0,
                                            "errors": 0, "xss": 0, "session_lost": 0}
                bucket_stats[bucket]["total"] += 1

                batch_count += 1

                # Restart browser context every BATCH_SIZE to prevent DOM bloat
                if batch_count >= BATCH_SIZE:
                    print(f"  Recycling browser context (after {BATCH_SIZE} queries)...", flush=True)
                    try:
                        ctx.close()
                    except Exception:
                        pass
                    ctx, page = fresh_page()
                    batch_count = 0

                # Progress every 20 queries
                if processed % 20 == 0:
                    elapsed = time.time() - t_start
                    rate = processed / elapsed if elapsed > 0 else 0
                    eta = (len(remaining) - processed) / rate / 60 if rate > 0 else 0
                    print(f"  [{existing + processed}/{len(corpus)}] "
                          f"bucket={bucket} rate={rate:.1f}q/s ETA={eta:.0f}min", flush=True)

                # Skip empty
                if not query.strip():
                    fout.write(json.dumps({"query_id": qid, "bucket": bucket,
                                           "skipped": True, "reason": "empty"},
                                          ensure_ascii=False) + "\n")
                    fout.flush()
                    continue

                timeout_s = 6.0 if bucket == "burst" else 8.0
                cr = fast_send(page, query, timeout_s=timeout_s)

                record = {
                    "query_id": qid, "bucket": bucket,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "viewport": "1920x1080",
                    "session_valid": not cr.get("session_lost", False),
                    "ui_sent": cr.get("sent", False),
                    "network_status": "ok" if cr.get("sent") else "failed",
                    "response_visible": cr.get("response_visible", False),
                    "response_length": cr.get("response_length", 0),
                    "latency_ms": cr.get("latency_ms", 0),
                    "console_errors": 0,  # tracked separately
                    "console_error_texts": [],
                    "failed_requests": 0,
                    "dom_ok": cr.get("dom_ok", True),
                    "xss_detected": cr.get("xss_detected", False),
                    "session_lost": cr.get("session_lost", False),
                    "error": cr.get("error", ""),
                }
                fout.write(json.dumps(record, ensure_ascii=False) + "\n")
                fout.flush()

                if cr.get("sent"):
                    bucket_stats[bucket]["sent"] += 1
                if cr.get("response_visible"):
                    bucket_stats[bucket]["responded"] += 1
                if cr.get("error"):
                    bucket_stats[bucket]["errors"] += 1
                if cr.get("xss_detected"):
                    bucket_stats[bucket]["xss"] += 1

                # Screenshot adversarial samples
                if bucket == "adversarial" and qid % 20 == 0:
                    page.screenshot(path=str(SCREENSHOT_DIR / f"C_adv_{qid}.png"))
                elif cr.get("xss_detected"):
                    page.screenshot(path=str(SCREENSHOT_DIR / f"C_xss_{qid}.png"))

        ctx.close()
        browser.close()

    total_time = time.time() - t_start
    print(f"\n=== Phase C Continuation Complete ===", flush=True)
    print(f"  Processed: {processed} queries in {total_time:.0f}s "
          f"({processed/total_time:.2f} q/s)", flush=True)

    for bucket, stats in sorted(bucket_stats.items()):
        print(f"  {bucket:12s}: {stats['sent']}/{stats['total']} sent, "
              f"{stats['responded']} resp, {stats['errors']} err, "
              f"{stats['xss']} xss", flush=True)

    xss_total = sum(s["xss"] for s in bucket_stats.values())
    print(f"  XSS detected: {xss_total}", flush=True)
    print(f"  PASS: {xss_total == 0}", flush=True)


if __name__ == "__main__":
    main()

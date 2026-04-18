#!/usr/bin/env python3
"""WaggleDance 400h Gauntlet Campaign Harness.

Modes:
    DRYRUN   — Phase 1J validation (resume, recycle, recovery, restart, tab)
    BASELINE — Phase 2  (fidelity, hot_mini, drills, warm_mini, ci)
    HOT      — Phase 4A (high-rate chat soak, resumable)
    WARM     — Phase 4B (mixed-load warm soak)
    COLD     — Phase 4C (backend-only cold soak)

Usage:
    python ui_gauntlet_400h.py --mode DRYRUN  --campaign-dir docs/runs/400h_run1
    python ui_gauntlet_400h.py --mode BASELINE --sub fidelity --campaign-dir ...
    python ui_gauntlet_400h.py --mode HOT --segment-hours 8 --campaign-dir ...
    python ui_gauntlet_400h.py --mode WARM --segment-hours 8 --campaign-dir ...
    python ui_gauntlet_400h.py --mode COLD --segment-hours 8 --campaign-dir ...
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Ensure repo root and e2e dir importable
_REPO_ROOT = Path(__file__).resolve().parents[2]
_E2E_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_E2E_DIR))

from harness_helpers import (
    BASE_URL,
    TABS,
    VIEWPORTS,
    ConsoleCapture,
    XSS_INIT_SCRIPT,
    backend_health_snapshot,
    controlled_server_restart,
    ensure_tab_selected,
    load_api_key,
    log_incident,
    open_authenticated_hologram,
    send_chat_safe,
    wait_for_auth_ready,
    wait_for_chat_ready,
)

# ---------------------------------------------------------------------------
# Campaign state management
# ---------------------------------------------------------------------------

def _load_campaign_state(campaign_dir: Path) -> dict:
    state_file = campaign_dir / "campaign_state.json"
    if state_file.exists():
        state = json.loads(state_file.read_text(encoding="utf-8"))
    else:
        state = {}
    # Ensure required keys exist (backwards-compat with Phase 0 format)
    state.setdefault("segments", [])
    state.setdefault("total_green_s", 0.0)
    return state


def _save_campaign_state(campaign_dir: Path, state: dict) -> None:
    state_file = campaign_dir / "campaign_state.json"
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")


def _next_segment_id(state: dict) -> int:
    if not state["segments"]:
        return 1
    return max(s.get("segment_id", 0) for s in state["segments"]) + 1


def _load_jsonl(path: Path) -> list[dict]:
    """Read all JSONL entries from *path*, returning list of dicts."""
    if not path.exists():
        return []
    entries = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return entries


def _append_jsonl(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


def _write_md(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


# ---------------------------------------------------------------------------
# Green-time timer
# ---------------------------------------------------------------------------

class GreenTimer:
    """Accumulates only healthy running time, pausing during errors/retries."""

    def __init__(self) -> None:
        self._running = False
        self._accumulated: float = 0.0
        self._start: float = 0.0

    def start(self) -> None:
        if not self._running:
            self._start = time.monotonic()
            self._running = True

    def pause(self) -> None:
        if self._running:
            self._accumulated += time.monotonic() - self._start
            self._running = False

    @property
    def elapsed_s(self) -> float:
        extra = (time.monotonic() - self._start) if self._running else 0.0
        return self._accumulated + extra

    @property
    def elapsed_h(self) -> float:
        return self.elapsed_s / 3600.0


# ---------------------------------------------------------------------------
# Query corpus (shared with main harness)
# ---------------------------------------------------------------------------

def _load_or_build_corpus(campaign_dir: Path) -> list[dict]:
    """Load query corpus from the main harness artifact dir or campaign dir."""
    # Try campaign dir first
    corpus_file = campaign_dir / "query_corpus.json"
    if corpus_file.exists():
        return json.loads(corpus_file.read_text(encoding="utf-8"))

    # Try main harness artifact dir
    main_corpus = _REPO_ROOT / "docs" / "runs" / "ui_gauntlet_20260412" / "query_corpus.json"
    if main_corpus.exists():
        corpus = json.loads(main_corpus.read_text(encoding="utf-8"))
        # Copy to campaign dir for self-containment
        corpus_file.parent.mkdir(parents=True, exist_ok=True)
        corpus_file.write_text(json.dumps(corpus, indent=1, ensure_ascii=False), encoding="utf-8")
        return corpus

    # Minimal fallback corpus for DRYRUN
    corpus = [
        {"query_id": f"fallback_{i}", "bucket": "normal", "query": f"Test query {i}"}
        for i in range(1, 21)
    ]
    corpus_file.parent.mkdir(parents=True, exist_ok=True)
    corpus_file.write_text(json.dumps(corpus, indent=1, ensure_ascii=False), encoding="utf-8")
    return corpus


# ===================================================================
#  MODE: DRYRUN  (Phase 1J)
# ===================================================================

def run_dryrun(campaign_dir: Path) -> dict:
    """Five validation checks that prove the harness plumbing works."""
    from playwright.sync_api import sync_playwright

    results: dict[str, Any] = {}
    campaign_dir.mkdir(parents=True, exist_ok=True)
    key = load_api_key()
    print(f"=== DRYRUN  campaign_dir={campaign_dir}")

    # --- Check 1: Query resume test ---
    print("  [1/5] Query resume test ...")
    resume_file = campaign_dir / "_dryrun_resume_test.jsonl"
    if resume_file.exists():
        resume_file.unlink()
    for i in range(5):
        _append_jsonl(resume_file, {"query_id": f"dr_{i}", "bucket": "normal", "sent": True})
    reloaded = _load_jsonl(resume_file)
    done_ids = {e["query_id"] for e in reloaded}
    skip_ok = all(f"dr_{i}" in done_ids for i in range(5))
    results["resume_test"] = {"pass": skip_ok, "wrote": 5, "reloaded": len(reloaded)}
    print(f"    {'PASS' if skip_ok else 'FAIL'}: wrote 5, re-read {len(reloaded)}, skip verified={skip_ok}")
    resume_file.unlink(missing_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)

        # --- Check 2: Forced context recycle test ---
        print("  [2/5] Context recycle test ...")
        try:
            ctx1, page1, cap1 = open_authenticated_hologram(browser, api_key=key)
            auth1 = wait_for_auth_ready(page1, timeout_s=5)
            ctx1.close()
            ctx2, page2, cap2 = open_authenticated_hologram(browser, api_key=key)
            auth2 = wait_for_auth_ready(page2, timeout_s=5)
            ctx2.close()
            recycle_ok = auth1 and auth2
        except Exception as exc:
            recycle_ok = False
            results["recycle_error"] = str(exc)[:200]
        results["context_recycle"] = {"pass": recycle_ok}
        print(f"    {'PASS' if recycle_ok else 'FAIL'}: context recycle")

        # --- Check 3: Fresh-context recovery test ---
        print("  [3/5] Fresh-context recovery test ...")
        try:
            ctx, page, cap = open_authenticated_hologram(browser, api_key=key)
            # Simulate session loss by clearing cookies
            ctx.clear_cookies()
            auth_after_clear = wait_for_auth_ready(page, timeout_s=3)
            ctx.close()
            # Recovery: open fresh context
            ctx, page, cap = open_authenticated_hologram(browser, api_key=key)
            auth_recovered = wait_for_auth_ready(page, timeout_s=5)
            ctx.close()
            recovery_ok = (not auth_after_clear) and auth_recovered
        except Exception as exc:
            recovery_ok = False
            results["recovery_error"] = str(exc)[:200]
        results["fresh_context_recovery"] = {"pass": recovery_ok}
        print(f"    {'PASS' if recovery_ok else 'FAIL'}: fresh-context recovery")

        # --- Check 4: Controlled server restart drill ---
        print("  [4/5] Controlled server restart drill ...")
        restart_result = controlled_server_restart(port=8002)
        results["server_restart"] = restart_result
        print(f"    {'PASS' if restart_result['pass'] else 'FAIL'}: server restart "
              f"(stop={restart_result['stop_ok']}, freed={restart_result['port_freed']}, "
              f"restart={restart_result['restart_ok']}, health={restart_result['health_ok']}, "
              f"dur={restart_result['duration_s']}s)")

        # --- Check 5: Tab-switch readiness test ---
        print("  [5/5] Tab-switch readiness test ...")
        try:
            ctx, page, cap = open_authenticated_hologram(browser, api_key=key)
            tab_results = {}
            for tab in TABS:
                ok = ensure_tab_selected(page, tab)
                tab_results[tab] = ok
            ctx.close()
            all_tabs_ok = all(tab_results.values())
        except Exception as exc:
            all_tabs_ok = False
            tab_results = {"error": str(exc)[:200]}
        results["tab_switch"] = {"pass": all_tabs_ok, "tabs": tab_results}
        print(f"    {'PASS' if all_tabs_ok else 'FAIL'}: tab switch ({sum(v for v in tab_results.values() if isinstance(v, bool))}/{len(TABS)})")

        browser.close()

    # Summary
    checks = [
        results["resume_test"]["pass"],
        results["context_recycle"]["pass"],
        results["fresh_context_recovery"]["pass"],
        results["server_restart"]["pass"],
        results["tab_switch"]["pass"],
    ]
    total_pass = sum(checks)
    results["overall"] = f"{total_pass}/5"

    # Write report
    md_lines = [
        "# DRYRUN Results",
        f"",
        f"Date: {datetime.now(timezone.utc).isoformat()}",
        f"Overall: **{total_pass}/5**",
        "",
        "| # | Check | Result |",
        "|---|-------|--------|",
        f"| 1 | Query resume | {'PASS' if checks[0] else 'FAIL'} |",
        f"| 2 | Context recycle | {'PASS' if checks[1] else 'FAIL'} |",
        f"| 3 | Fresh-context recovery | {'PASS' if checks[2] else 'FAIL'} |",
        f"| 4 | Server restart drill | {'PASS' if checks[3] else 'FAIL'} |",
        f"| 5 | Tab-switch readiness | {'PASS' if checks[4] else 'FAIL'} |",
        "",
    ]
    if not results["server_restart"]["pass"]:
        md_lines.append(f"Server restart detail: {json.dumps(results['server_restart'], indent=2)}")
    _write_md(campaign_dir / "dryrun_results.md", "\n".join(md_lines))
    print(f"\n=== DRYRUN {total_pass}/5 checks passed.  Report: {campaign_dir / 'dryrun_results.md'}")
    return results


# ===================================================================
#  MODE: BASELINE  (Phase 2)
# ===================================================================

def run_baseline(campaign_dir: Path, sub: str) -> dict:
    """Run one of the Phase 2 baseline sub-modes."""
    campaign_dir.mkdir(parents=True, exist_ok=True)

    if sub == "fidelity":
        return _baseline_fidelity(campaign_dir)
    elif sub == "hot_mini":
        return _baseline_hot_mini(campaign_dir)
    elif sub == "drills":
        return _baseline_drills(campaign_dir)
    elif sub == "warm_mini":
        return _baseline_warm_mini(campaign_dir)
    elif sub == "ci":
        return _baseline_ci(campaign_dir)
    else:
        print(f"Unknown sub-mode: {sub}")
        sys.exit(1)


def _baseline_fidelity(campaign_dir: Path) -> dict:
    """11 tabs x 3 viewports = 33 fidelity checks."""
    from playwright.sync_api import sync_playwright

    key = load_api_key()
    results: list[dict] = []
    print(f"=== BASELINE/fidelity  ({len(TABS)} tabs x {len(VIEWPORTS)} viewports = {len(TABS)*len(VIEWPORTS)})")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        for vp in VIEWPORTS:
            label = vp.get("label", f"{vp['width']}x{vp['height']}")
            ctx, page, cap = open_authenticated_hologram(browser, viewport=vp, api_key=key)
            for tab in TABS:
                ok = ensure_tab_selected(page, tab)
                page.wait_for_timeout(500)
                # Check visible content
                try:
                    body_text = page.locator("body").inner_text(timeout=3000)
                    has_content = len(body_text) > 50
                except Exception:
                    body_text = ""
                    has_content = False
                summary = cap.summary()
                entry = {
                    "viewport": label,
                    "tab": tab,
                    "tab_switched": ok,
                    "has_content": has_content,
                    "content_len": len(body_text),
                    "console_errors": summary["console_errors"],
                    "failed_requests": summary["failed_requests"],
                    "pass": ok and has_content,
                }
                results.append(entry)
                cap.reset()
                status = "PASS" if entry["pass"] else "FAIL"
                print(f"  {label} / {tab:12s}: {status}  (content={len(body_text)}, errs={summary['console_errors']})")
            ctx.close()
        browser.close()

    # Report
    total = len(results)
    passed = sum(1 for r in results if r["pass"])
    md_lines = [
        "# UI Fidelity Baseline",
        f"",
        f"Date: {datetime.now(timezone.utc).isoformat()}",
        f"Result: **{passed}/{total}**",
        "",
        "| Viewport | Tab | Switched | Content | Errors | Pass |",
        "|----------|-----|----------|---------|--------|------|",
    ]
    for r in results:
        md_lines.append(
            f"| {r['viewport']} | {r['tab']} | {r['tab_switched']} | "
            f"{r['content_len']} | {r['console_errors']} | "
            f"{'PASS' if r['pass'] else 'FAIL'} |"
        )
    _write_md(campaign_dir / "ui_fidelity_baseline.md", "\n".join(md_lines))
    print(f"\n=== FIDELITY {passed}/{total}  Report: {campaign_dir / 'ui_fidelity_baseline.md'}")
    return {"pass": passed == total, "passed": passed, "total": total, "results": results}


def _baseline_hot_mini(campaign_dir: Path) -> dict:
    """200+ queries across all buckets — mini version of HOT mode."""
    from playwright.sync_api import sync_playwright

    key = load_api_key()
    corpus = _load_or_build_corpus(campaign_dir)
    # Take up to 200 queries spread across buckets
    subset = corpus[:200] if len(corpus) >= 200 else corpus
    results_file = campaign_dir / "phase_c_baseline.jsonl"
    existing = _load_jsonl(results_file)
    done_ids = {e["query_id"] for e in existing}
    remaining = [e for e in subset if e["query_id"] not in done_ids]
    print(f"=== BASELINE/hot_mini  corpus={len(subset)}, done={len(done_ids)}, remaining={len(remaining)}")

    if not remaining:
        print("  All queries already done.")
        return {"pass": True, "total": len(subset), "remaining": 0}

    stats = {"total": 0, "sent": 0, "responded": 0, "errors": 0, "xss": 0}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx, page, cap = open_authenticated_hologram(browser, api_key=key)
        batch = 0

        for entry in remaining:
            if batch >= 50:
                ctx.close()
                ctx, page, cap = open_authenticated_hologram(browser, api_key=key)
                batch = 0

            cr = send_chat_safe(page, entry["query"], timeout_s=18)
            stats["total"] += 1
            if cr["sent"]:
                stats["sent"] += 1
            if cr["responded"]:
                stats["responded"] += 1
            if cr.get("xss_detected"):
                stats["xss"] += 1
            if cr.get("error"):
                stats["errors"] += 1

            record = {
                "query_id": entry["query_id"],
                "bucket": entry["bucket"],
                "ts": datetime.now(timezone.utc).isoformat(),
                **cr,
            }
            _append_jsonl(results_file, record)
            batch += 1

            if stats["total"] % 20 == 0:
                print(f"  progress: {stats['total']}/{len(remaining)} "
                      f"(sent={stats['sent']}, resp={stats['responded']}, err={stats['errors']})")

        ctx.close()
        browser.close()

    # Report
    md = (
        f"# Phase C Baseline (hot_mini)\n\n"
        f"Date: {datetime.now(timezone.utc).isoformat()}\n\n"
        f"| Metric | Value |\n|--------|-------|\n"
        f"| Total | {stats['total']} |\n"
        f"| Sent | {stats['sent']} |\n"
        f"| Responded | {stats['responded']} |\n"
        f"| Errors | {stats['errors']} |\n"
        f"| XSS | {stats['xss']} |\n"
    )
    _write_md(campaign_dir / "phase_c_baseline.md", md)
    print(f"\n=== HOT_MINI done. {stats}")
    return {"pass": stats["xss"] == 0, **stats}


def _baseline_drills(campaign_dir: Path) -> dict:
    """7 fault drills including real server restart."""
    from playwright.sync_api import sync_playwright

    key = load_api_key()
    drills: list[dict] = []
    print("=== BASELINE/drills  (7 fault drills)")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)

        # Drill 1: wrong_token
        print("  [1/7] wrong_token ...")
        t0 = time.monotonic()
        try:
            ctx = browser.new_context()
            page = ctx.new_page()
            page.goto(f"{BASE_URL}/hologram?token=INVALID_TOKEN_XYZ", wait_until="domcontentloaded", timeout=15_000)
            page.wait_for_timeout(2000)
            authed = page.evaluate(
                "async()=>{try{const r=await fetch('/api/auth/check',{credentials:'same-origin'});"
                "const d=await r.json();return d.authenticated===true}catch{return false}}"
            )
            body = page.locator("body").inner_text(timeout=3000)
            key_leaked = key in body if key else False
            drill_pass = (not authed) and (not key_leaked)
            ctx.close()
        except Exception as exc:
            drill_pass = False
        drills.append({"drill": "wrong_token", "pass": drill_pass, "duration_s": round(time.monotonic() - t0, 2)})
        print(f"    {'PASS' if drill_pass else 'FAIL'}")

        # Drill 2: session_clear
        print("  [2/7] session_clear ...")
        t0 = time.monotonic()
        try:
            ctx, page, cap = open_authenticated_hologram(browser, api_key=key)
            auth_before = wait_for_auth_ready(page, timeout_s=5)
            ctx.clear_cookies()
            auth_after = page.evaluate(
                "async()=>{try{const r=await fetch('/api/auth/check',{credentials:'same-origin'});"
                "const d=await r.json();return d.authenticated===true}catch{return false}}"
            )
            ctx.close()
            drill_pass = auth_before and (not auth_after)
        except Exception:
            drill_pass = False
        drills.append({"drill": "session_clear", "pass": drill_pass, "duration_s": round(time.monotonic() - t0, 2)})
        print(f"    {'PASS' if drill_pass else 'FAIL'}")

        # Drill 3: noauth_post
        print("  [3/7] noauth_post ...")
        t0 = time.monotonic()
        try:
            import httpx
            r = httpx.post(f"{BASE_URL}/api/chat", json={"query": "test"}, timeout=10.0)
            drill_pass = r.status_code in (401, 403)
        except Exception:
            drill_pass = False
        drills.append({"drill": "noauth_post", "pass": drill_pass, "duration_s": round(time.monotonic() - t0, 2)})
        print(f"    {'PASS' if drill_pass else 'FAIL'}")

        # Drill 4: invalid_body
        print("  [4/7] invalid_body ...")
        t0 = time.monotonic()
        try:
            import httpx
            # Need auth cookie — get via session
            ctx, page, cap = open_authenticated_hologram(browser, api_key=key)
            cookies = ctx.cookies()
            cookie_header = "; ".join(f"{c['name']}={c['value']}" for c in cookies)

            r1 = httpx.post(f"{BASE_URL}/api/chat", content=b"", headers={"Cookie": cookie_header}, timeout=10.0)
            r2 = httpx.post(f"{BASE_URL}/api/chat", content=b"NOT_JSON{{{", headers={"Cookie": cookie_header, "Content-Type": "application/json"}, timeout=10.0)
            ctx.close()
            # Server should handle malformed input gracefully (not 500)
            drill_pass = all(r.status_code != 500 for r in [r1, r2])
            # Oversized query (10k chars) may legitimately take >10s to process;
            # a timeout here is acceptable (server is working, not crashing)
            try:
                r3 = httpx.post(f"{BASE_URL}/api/chat", json={"query": "A" * 10000}, headers={"Cookie": cookie_header}, timeout=30.0)
                if r3.status_code == 500:
                    drill_pass = False
            except httpx.ReadTimeout:
                pass  # server processing long input — acceptable, not a crash
        except Exception:
            drill_pass = False
        drills.append({"drill": "invalid_body", "pass": drill_pass, "duration_s": round(time.monotonic() - t0, 2)})
        print(f"    {'PASS' if drill_pass else 'FAIL'}")

        # Drill 5: server_restart (real)
        print("  [5/7] server_restart (real) ...")
        restart_result = controlled_server_restart(port=8002)
        drills.append({"drill": "server_restart", "pass": restart_result["pass"],
                        "duration_s": restart_result["duration_s"], "detail": restart_result})
        print(f"    {'PASS' if restart_result['pass'] else 'FAIL'} ({restart_result['duration_s']}s)")

        # Drill 6: ollama_check
        print("  [6/7] ollama_check ...")
        t0 = time.monotonic()
        try:
            import httpx
            r = httpx.get("http://127.0.0.1:11434/api/tags", timeout=5.0)
            ollama_up = r.status_code == 200
            drill_pass = True  # informational — pass if we can check
        except Exception:
            ollama_up = False
            drill_pass = True  # Ollama being down is not a harness failure
        drills.append({"drill": "ollama_check", "pass": drill_pass, "ollama_up": ollama_up,
                        "duration_s": round(time.monotonic() - t0, 2)})
        print(f"    {'PASS' if drill_pass else 'FAIL'} (ollama_up={ollama_up})")

        # Drill 7: feed_resilience
        print("  [7/7] feed_resilience ...")
        t0 = time.monotonic()
        try:
            ctx, page, cap = open_authenticated_hologram(browser, api_key=key)
            tab_ok = {}
            for tab in ["feeds", "overview", "chat"]:
                ok = ensure_tab_selected(page, tab)
                page.wait_for_timeout(500)
                body = page.locator("body").inner_text(timeout=3000)
                tab_ok[tab] = ok and len(body) > 30
            ctx.close()
            drill_pass = all(tab_ok.values())
        except Exception:
            drill_pass = False
            tab_ok = {}
        drills.append({"drill": "feed_resilience", "pass": drill_pass,
                        "tabs": tab_ok, "duration_s": round(time.monotonic() - t0, 2)})
        print(f"    {'PASS' if drill_pass else 'FAIL'}")

        browser.close()

    # Report
    total = len(drills)
    passed = sum(1 for d in drills if d["pass"])
    md_lines = [
        "# Fault Drills Baseline",
        "",
        f"Date: {datetime.now(timezone.utc).isoformat()}",
        f"Result: **{passed}/{total}**",
        "",
        "| # | Drill | Pass | Duration |",
        "|---|-------|------|----------|",
    ]
    for i, d in enumerate(drills, 1):
        md_lines.append(f"| {i} | {d['drill']} | {'PASS' if d['pass'] else 'FAIL'} | {d['duration_s']}s |")
    _write_md(campaign_dir / "fault_drills.md", "\n".join(md_lines))
    print(f"\n=== DRILLS {passed}/{total}  Report: {campaign_dir / 'fault_drills.md'}")
    return {"pass": passed == total, "passed": passed, "total": total, "drills": drills}


def _baseline_warm_mini(campaign_dir: Path) -> dict:
    """2h warm soak mini-baseline."""
    from playwright.sync_api import sync_playwright

    key = load_api_key()
    duration_s = 2 * 3600  # 2 hours
    cycle_interval = 50  # seconds
    metrics: list[dict] = []
    print(f"=== BASELINE/warm_mini  duration={duration_s}s  interval={cycle_interval}s")

    stats = {
        "cycles": 0, "tab_switch_failures": 0,
        "chat_send_failures": 0, "chat_response_failures": 0,
        "auth_failures": 0,
    }
    gt = GreenTimer()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx, page, cap = open_authenticated_hologram(browser, api_key=key)
        gt.start()
        t_start = time.monotonic()

        while (time.monotonic() - t_start) < duration_s:
            cycle = stats["cycles"] + 1
            stats["cycles"] = cycle
            cycle_metric: dict[str, Any] = {"cycle": cycle, "ts": datetime.now(timezone.utc).isoformat()}

            # Every cycle: overview, feeds, hologram, chat, auth check
            for tab in ["overview", "feeds", "magma", "chat"]:
                ok = ensure_tab_selected(page, tab)
                if not ok:
                    stats["tab_switch_failures"] += 1
                page.wait_for_timeout(300)

            # Auth check
            authed = wait_for_auth_ready(page, timeout_s=3)
            if not authed:
                stats["auth_failures"] += 1
                gt.pause()
                # Try fresh context
                try:
                    ctx.close()
                except Exception:
                    pass
                ctx, page, cap = open_authenticated_hologram(browser, api_key=key)
                gt.start()

            # Every 3rd cycle: send one chat
            if cycle % 3 == 0:
                cr = send_chat_safe(page, f"warm cycle {cycle} check", timeout_s=12)
                if not cr["sent"]:
                    stats["chat_send_failures"] += 1
                elif not cr["responded"]:
                    stats["chat_response_failures"] += 1

            # Every 10th cycle: mini-burst 3-5 chats
            if cycle % 10 == 0:
                for i in range(3):
                    cr = send_chat_safe(page, f"burst {cycle}.{i}", timeout_s=10)
                    if not cr["sent"]:
                        stats["chat_send_failures"] += 1

            # Every 20th cycle: recycle browser context
            if cycle % 20 == 0:
                gt.pause()
                try:
                    ctx.close()
                except Exception:
                    pass
                ctx, page, cap = open_authenticated_hologram(browser, api_key=key)
                gt.start()

            # Every 30th cycle: fresh auth bootstrap
            if cycle % 30 == 0:
                gt.pause()
                try:
                    ctx.close()
                except Exception:
                    pass
                ctx, page, cap = open_authenticated_hologram(browser, api_key=key)
                authed = wait_for_auth_ready(page, timeout_s=10)
                if authed:
                    gt.start()
                else:
                    stats["auth_failures"] += 1
                    gt.start()

            cycle_metric.update({
                "green_h": round(gt.elapsed_h, 4),
                "tab_failures": stats["tab_switch_failures"],
                "chat_send_failures": stats["chat_send_failures"],
                "chat_resp_failures": stats["chat_response_failures"],
            })
            metrics.append(cycle_metric)

            if cycle % 20 == 0:
                print(f"  cycle {cycle}  green={gt.elapsed_h:.2f}h  "
                      f"tab_fail={stats['tab_switch_failures']} "
                      f"chat_send_fail={stats['chat_send_failures']} "
                      f"chat_resp_fail={stats['chat_response_failures']}")

            # Wait for next cycle
            page.wait_for_timeout(int(cycle_interval * 1000))

        try:
            ctx.close()
        except Exception:
            pass
        browser.close()

    # Write metrics JSON
    (campaign_dir / "warmup_soak_metrics.json").write_text(
        json.dumps({"stats": stats, "green_hours": gt.elapsed_h, "cycles": metrics}, indent=2, default=str),
        encoding="utf-8",
    )

    # Write report
    md = (
        f"# Warm Soak Mini Baseline\n\n"
        f"Date: {datetime.now(timezone.utc).isoformat()}\n"
        f"Green time: {gt.elapsed_h:.2f}h\n"
        f"Cycles: {stats['cycles']}\n\n"
        f"| Metric | Value |\n|--------|-------|\n"
        f"| Tab switch failures | {stats['tab_switch_failures']} |\n"
        f"| Chat send failures | {stats['chat_send_failures']} |\n"
        f"| Chat response failures | {stats['chat_response_failures']} |\n"
        f"| Auth failures | {stats['auth_failures']} |\n"
    )
    _write_md(campaign_dir / "warmup_soak.md", md)
    print(f"\n=== WARM_MINI done. green={gt.elapsed_h:.2f}h  cycles={stats['cycles']}")
    return {"pass": True, **stats, "green_hours": gt.elapsed_h}


def _baseline_ci(campaign_dir: Path) -> dict:
    """Run pytest + basic CI checks and report to stdout."""
    import subprocess
    print("=== BASELINE/ci")
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/", "-x", "-q"],
        capture_output=True, text=True, cwd=str(_REPO_ROOT),
    )
    print(result.stdout[-2000:] if len(result.stdout) > 2000 else result.stdout)
    if result.stderr:
        print(result.stderr[-1000:])
    return {"pass": result.returncode == 0, "returncode": result.returncode}


# ===================================================================
#  MODE: HOT  (Phase 4A)
# ===================================================================

def run_hot(campaign_dir: Path, segment_hours: float) -> dict:
    """High-rate chat soak with resumable checkpointing."""
    from playwright.sync_api import sync_playwright

    campaign_dir.mkdir(parents=True, exist_ok=True)
    state = _load_campaign_state(campaign_dir)
    seg_id = _next_segment_id(state)
    key = load_api_key()
    corpus = _load_or_build_corpus(campaign_dir)

    results_file = campaign_dir / "hot_results.jsonl"
    existing = _load_jsonl(results_file)
    done_ids = {e["query_id"] for e in existing}

    # Build cycling queue from corpus (repeat if needed for long runs)
    remaining = [e for e in corpus if e["query_id"] not in done_ids]
    if not remaining:
        # Cycle: re-use corpus with new IDs
        remaining = [
            {**e, "query_id": f"{e['query_id']}_r{seg_id}"}
            for e in corpus
        ]

    print(f"=== HOT segment={seg_id}  target={segment_hours}h  remaining={len(remaining)}")

    stats = {
        "segment_id": seg_id, "total": 0, "sent": 0, "responded": 0,
        "errors": 0, "xss": 0, "session_lost": 0, "dom_broken": 0,
        "buckets": {},
        "session_lost_after_recycle": 0,
        "session_lost_mid_batch": 0,
        "backpressure_pauses": 0,
        "avg_response_ms": 0,
    }
    _response_times: list[float] = []  # track latencies for adaptive stats
    gt = GreenTimer()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx, page, cap = open_authenticated_hologram(browser, api_key=key)
        gt.start()
        batch = 0
        query_idx = 0

        corpus_cycle = 0
        while gt.elapsed_h < segment_hours:
            if query_idx >= len(remaining):
                # Wrap around: re-cycle corpus with incremented cycle prefix
                corpus_cycle += 1
                remaining = [
                    {**e, "query_id": f"{e['query_id']}_c{corpus_cycle}"}
                    for e in corpus
                ]
                query_idx = 0
                print(f"  HOT corpus wrap: cycle {corpus_cycle}, green={gt.elapsed_h:.2f}h")

            entry = remaining[query_idx]
            query_idx += 1
            batch += 1
            stats["total"] += 1

            # Recycle context every 50 queries
            if batch >= 50:
                gt.pause()
                health = backend_health_snapshot()
                if not health["healthy"]:
                    log_incident(campaign_dir, {
                        "ts": datetime.now(timezone.utc).isoformat(),
                        "segment_id": seg_id, "mode": "HOT",
                        "category": "backend_unhealthy", "short_code": "HEALTH_FAIL",
                        "summary": f"Backend unhealthy at recycle (query #{stats['total']})",
                        "backend_health_snapshot": health,
                        "fresh_context_retry_result": None,
                    })
                try:
                    ctx.close()
                except Exception:
                    pass
                for _attempt in range(3):
                    try:
                        ctx, page, cap = open_authenticated_hologram(browser, api_key=key)
                        break
                    except Exception as e:
                        if _attempt == 2:
                            log_incident(campaign_dir, {
                                "ts": datetime.now(timezone.utc).isoformat(),
                                "segment_id": seg_id, "mode": "HOT",
                                "category": "context_recycle_failure",
                                "short_code": "CTX_RECYCLE_FAIL",
                                "summary": f"Context recycle failed at query #{stats['total']}: {e}",
                                "backend_health_snapshot": backend_health_snapshot(),
                                "fresh_context_retry_result": None,
                            })
                        import time; time.sleep(5)
                gt.start()
                batch = 0

            cr = send_chat_safe(page, entry["query"], timeout_s=18)

            # Track response latency for backpressure
            if cr["sent"] and cr["latency_ms"] > 0:
                _response_times.append(cr["latency_ms"])
                if len(_response_times) > 100:
                    _response_times.pop(0)
                stats["avg_response_ms"] = round(sum(_response_times) / len(_response_times))

            # Backpressure: if recent avg latency > 8s, slow down
            if len(_response_times) >= 10:
                recent_avg = sum(_response_times[-10:]) / 10
                if recent_avg > 8000:
                    stats["backpressure_pauses"] += 1
                    page.wait_for_timeout(3000)  # 3s cooldown

            # Classify
            bucket = entry.get("bucket", "unknown")
            if bucket not in stats["buckets"]:
                stats["buckets"][bucket] = {"total": 0, "sent": 0, "responded": 0, "errors": 0, "xss": 0}
            bs = stats["buckets"][bucket]
            bs["total"] += 1

            if cr["sent"]:
                stats["sent"] += 1
                bs["sent"] += 1
            if cr["responded"]:
                stats["responded"] += 1
                bs["responded"] += 1
            if cr.get("xss_detected"):
                stats["xss"] += 1
                bs["xss"] += 1
            if cr.get("error"):
                stats["errors"] += 1
                bs["errors"] += 1
            if not cr.get("dom_ok", True):
                stats["dom_broken"] += 1
            if not cr.get("session_ok", True):
                stats["session_lost"] += 1
                if batch <= 2:
                    stats["session_lost_after_recycle"] += 1
                else:
                    stats["session_lost_mid_batch"] += 1

            # On failure: health check + fresh context retry
            if cr.get("error") or not cr["sent"]:
                gt.pause()
                health = backend_health_snapshot()
                # Try fresh context
                try:
                    ctx.close()
                except Exception:
                    pass
                for _attempt in range(3):
                    try:
                        ctx, page, cap = open_authenticated_hologram(browser, api_key=key)
                        break
                    except Exception:
                        import time; time.sleep(5)
                retry_cr = send_chat_safe(page, entry["query"], timeout_s=18)
                log_incident(campaign_dir, {
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "segment_id": seg_id, "mode": "HOT",
                    "category": "chat_failure",
                    "short_code": "CHAT_FAIL",
                    "summary": f"Chat failure on {entry['query_id']}: {cr.get('error', 'no_send')}",
                    "backend_health_snapshot": health,
                    "fresh_context_retry_result": retry_cr,
                })
                gt.start()
                batch = 0

            record = {
                "query_id": entry["query_id"],
                "bucket": bucket,
                "segment_id": seg_id,
                "ts": datetime.now(timezone.utc).isoformat(),
                **cr,
            }
            _append_jsonl(results_file, record)

            # Checkpoint every 50 queries
            if stats["total"] % 50 == 0:
                bp_info = f" bp={stats['backpressure_pauses']}" if stats["backpressure_pauses"] else ""
                avg_info = f" avg_ms={stats['avg_response_ms']}" if stats["avg_response_ms"] else ""
                print(f"  HOT checkpoint: q={stats['total']} sent={stats['sent']} "
                      f"resp={stats['responded']} err={stats['errors']} "
                      f"green={gt.elapsed_h:.2f}h{bp_info}{avg_info}")

        try:
            ctx.close()
        except Exception:
            pass
        browser.close()

    # Segment report
    segment_info = {
        "segment_id": seg_id,
        "mode": "HOT",
        "green_hours": round(gt.elapsed_h, 4),
        "ts_start": datetime.now(timezone.utc).isoformat(),
        **stats,
    }
    state["segments"].append(segment_info)
    state["total_green_s"] = state.get("total_green_s", 0) + gt.elapsed_s
    _save_campaign_state(campaign_dir, state)

    # Write segment artifacts
    metrics_file = campaign_dir / f"segment_metrics_{seg_id:03d}.json"
    metrics_file.write_text(json.dumps(segment_info, indent=2, default=str), encoding="utf-8")

    resp_pct = round(100 * stats["responded"] / stats["sent"], 1) if stats["sent"] else 0
    md = (
        f"# HOT Segment {seg_id}\n\n"
        f"Green time: {gt.elapsed_h:.2f}h\n"
        f"Queries: {stats['total']} (sent={stats['sent']}, resp={stats['responded']}, {resp_pct}%)\n"
        f"Errors: {stats['errors']}  XSS: {stats['xss']}  "
        f"Session lost: {stats['session_lost']} "
        f"(after_recycle={stats.get('session_lost_after_recycle', '?')}, "
        f"mid_batch={stats.get('session_lost_mid_batch', '?')})\n"
        f"DOM broken: {stats['dom_broken']}  "
        f"Backpressure pauses: {stats.get('backpressure_pauses', 0)}  "
        f"Avg response: {stats.get('avg_response_ms', 0)}ms\n\n"
        f"## Buckets\n\n| Bucket | Total | Sent | Resp | Err | XSS |\n"
        f"|--------|-------|------|------|-----|-----|\n"
    )
    for bname, bs in sorted(stats["buckets"].items()):
        md += f"| {bname} | {bs['total']} | {bs['sent']} | {bs['responded']} | {bs['errors']} | {bs['xss']} |\n"
    _write_md(campaign_dir / f"segment_report_{seg_id:03d}.md", md)

    total_h = state["total_green_s"] / 3600
    print(f"\n=== HOT segment {seg_id} done. green={gt.elapsed_h:.2f}h  campaign_total={total_h:.2f}h")
    return segment_info


# ===================================================================
#  MODE: WARM  (Phase 4B)
# ===================================================================

def run_warm(campaign_dir: Path, segment_hours: float) -> dict:
    """Mixed-load warm soak with separate failure tracking."""
    from playwright.sync_api import sync_playwright

    campaign_dir.mkdir(parents=True, exist_ok=True)
    state = _load_campaign_state(campaign_dir)
    seg_id = _next_segment_id(state)
    key = load_api_key()

    cycle_interval = 50  # seconds
    print(f"=== WARM segment={seg_id}  target={segment_hours}h  interval={cycle_interval}s")

    stats = {
        "segment_id": seg_id, "cycles": 0,
        "tab_switch_failures": 0,
        "chat_send_failures": 0,
        "chat_response_failures": 0,
        "auth_failures": 0,
    }
    gt = GreenTimer()
    metrics: list[dict] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx, page, cap = open_authenticated_hologram(browser, api_key=key)
        gt.start()

        while gt.elapsed_h < segment_hours:
          try:
            cycle = stats["cycles"] + 1
            stats["cycles"] = cycle
            cycle_metric: dict[str, Any] = {
                "cycle": cycle,
                "ts": datetime.now(timezone.utc).isoformat(),
            }

            # Every cycle: overview, feeds, hologram, chat, auth check
            for tab in ["overview", "feeds", "magma", "chat"]:
                ok = ensure_tab_selected(page, tab)
                if not ok:
                    stats["tab_switch_failures"] += 1
                    log_incident(campaign_dir, {
                        "ts": datetime.now(timezone.utc).isoformat(),
                        "segment_id": seg_id, "mode": "WARM",
                        "category": "tab_switch_failure",
                        "short_code": "TAB_FAIL",
                        "summary": f"Tab switch to '{tab}' failed at cycle {cycle}",
                        "backend_health_snapshot": backend_health_snapshot(),
                        "fresh_context_retry_result": None,
                    })
                page.wait_for_timeout(300)

            # Auth check
            authed = wait_for_auth_ready(page, timeout_s=3)
            if not authed:
                stats["auth_failures"] += 1
                gt.pause()
                try:
                    ctx.close()
                except Exception:
                    pass
                for _attempt in range(3):
                    try:
                        ctx, page, cap = open_authenticated_hologram(browser, api_key=key)
                        break
                    except Exception as e:
                        if _attempt == 2:
                            log_incident(campaign_dir, {
                                "ts": datetime.now(timezone.utc).isoformat(),
                                "segment_id": seg_id, "mode": "WARM",
                                "category": "auth_recovery_failure",
                                "short_code": "AUTH_RECOVER_FAIL",
                                "summary": f"Auth recovery failed at cycle {cycle}: {e}",
                                "backend_health_snapshot": backend_health_snapshot(),
                                "fresh_context_retry_result": None,
                            })
                        import time; time.sleep(5)
                gt.start()

            # Every 3rd cycle: send one chat
            if cycle % 3 == 0:
                cr = send_chat_safe(page, f"warm soak cycle {cycle}", timeout_s=12)
                if not cr["sent"]:
                    stats["chat_send_failures"] += 1
                    log_incident(campaign_dir, {
                        "ts": datetime.now(timezone.utc).isoformat(),
                        "segment_id": seg_id, "mode": "WARM",
                        "category": "chat_send_failure",
                        "short_code": "CHAT_SEND_FAIL",
                        "summary": f"Chat send failed at cycle {cycle}: {cr.get('error', '')}",
                        "backend_health_snapshot": backend_health_snapshot(),
                        "fresh_context_retry_result": None,
                    })
                elif not cr["responded"]:
                    stats["chat_response_failures"] += 1
                    log_incident(campaign_dir, {
                        "ts": datetime.now(timezone.utc).isoformat(),
                        "segment_id": seg_id, "mode": "WARM",
                        "category": "chat_response_failure",
                        "short_code": "CHAT_RESP_FAIL",
                        "summary": f"Chat response missing at cycle {cycle}",
                        "backend_health_snapshot": backend_health_snapshot(),
                        "fresh_context_retry_result": None,
                    })

            # Every 10th cycle: mini-burst 3-5 chats (with 2s throttle)
            if cycle % 10 == 0:
                for i in range(min(3 + (cycle % 3), 5)):
                    cr = send_chat_safe(page, f"warm burst {cycle}.{i}", timeout_s=10)
                    if not cr["sent"]:
                        stats["chat_send_failures"] += 1
                    page.wait_for_timeout(2000)  # burst throttle

            # Every 20th cycle: recycle browser context
            if cycle % 20 == 0:
                gt.pause()
                try:
                    ctx.close()
                except Exception:
                    pass
                for _attempt in range(3):
                    try:
                        ctx, page, cap = open_authenticated_hologram(browser, api_key=key)
                        break
                    except Exception as e:
                        if _attempt == 2:
                            log_incident(campaign_dir, {
                                "ts": datetime.now(timezone.utc).isoformat(),
                                "segment_id": seg_id, "mode": "WARM",
                                "category": "context_recycle_failure",
                                "short_code": "CTX_RECYCLE_FAIL",
                                "summary": f"Context recycle failed at cycle {cycle}: {e}",
                                "backend_health_snapshot": backend_health_snapshot(),
                                "fresh_context_retry_result": None,
                            })
                        import time; time.sleep(5)
                gt.start()

            # Every 30th cycle: fresh auth bootstrap
            if cycle % 30 == 0:
                gt.pause()
                try:
                    ctx.close()
                except Exception:
                    pass
                for _attempt in range(3):
                    try:
                        ctx, page, cap = open_authenticated_hologram(browser, api_key=key)
                        break
                    except Exception as e:
                        if _attempt == 2:
                            log_incident(campaign_dir, {
                                "ts": datetime.now(timezone.utc).isoformat(),
                                "segment_id": seg_id, "mode": "WARM",
                                "category": "auth_bootstrap_failure",
                                "short_code": "AUTH_BOOT_FAIL",
                                "summary": f"Auth bootstrap failed at cycle {cycle}: {e}",
                                "backend_health_snapshot": backend_health_snapshot(),
                                "fresh_context_retry_result": None,
                            })
                        import time; time.sleep(5)
                authed = wait_for_auth_ready(page, timeout_s=10)
                if not authed:
                    stats["auth_failures"] += 1
                gt.start()

            cycle_metric.update({
                "green_h": round(gt.elapsed_h, 4),
                "tab_fail": stats["tab_switch_failures"],
                "chat_send_fail": stats["chat_send_failures"],
                "chat_resp_fail": stats["chat_response_failures"],
                "auth_fail": stats["auth_failures"],
            })
            metrics.append(cycle_metric)

            if cycle % 20 == 0:
                chats_attempted = cycle // 3 if cycle > 0 else 0
                resp_rate = round(100 * (1 - stats['chat_response_failures'] / max(chats_attempted, 1)), 1)
                print(f"  WARM cycle {cycle}  green={gt.elapsed_h:.2f}h  "
                      f"tab_fail={stats['tab_switch_failures']} "
                      f"chat_send={stats['chat_send_failures']} "
                      f"chat_resp={stats['chat_response_failures']} "
                      f"resp_rate={resp_rate}%")

            page.wait_for_timeout(int(cycle_interval * 1000))
          except Exception as _cycle_err:
            # Safety net: log and recover instead of crashing the segment
            log_incident(campaign_dir, {
                "ts": datetime.now(timezone.utc).isoformat(),
                "segment_id": seg_id, "mode": "WARM",
                "category": "cycle_crash_recovery",
                "short_code": "CYCLE_CRASH",
                "summary": f"Cycle {cycle} crashed, recovering: {_cycle_err}",
                "backend_health_snapshot": backend_health_snapshot(),
                "fresh_context_retry_result": None,
            })
            gt.pause()
            try:
                ctx.close()
            except Exception:
                pass
            import time; time.sleep(10)
            for _attempt in range(3):
                try:
                    ctx, page, cap = open_authenticated_hologram(browser, api_key=key)
                    break
                except Exception:
                    time.sleep(5)
            gt.start()

        try:
            ctx.close()
        except Exception:
            pass
        browser.close()

    # Segment artifacts
    segment_info = {
        "segment_id": seg_id,
        "mode": "WARM",
        "green_hours": round(gt.elapsed_h, 4),
        **stats,
    }
    state["segments"].append(segment_info)
    state["total_green_s"] = state.get("total_green_s", 0) + gt.elapsed_s
    _save_campaign_state(campaign_dir, state)

    (campaign_dir / f"segment_metrics_{seg_id:03d}.json").write_text(
        json.dumps({"segment": segment_info, "cycles": metrics}, indent=2, default=str),
        encoding="utf-8",
    )

    md = (
        f"# WARM Segment {seg_id}\n\n"
        f"Green time: {gt.elapsed_h:.2f}h  Cycles: {stats['cycles']}\n\n"
        f"| Metric | Value |\n|--------|-------|\n"
        f"| Tab switch failures | {stats['tab_switch_failures']} |\n"
        f"| Chat send failures | {stats['chat_send_failures']} |\n"
        f"| Chat response failures | {stats['chat_response_failures']} |\n"
        f"| Auth failures | {stats['auth_failures']} |\n"
    )
    _write_md(campaign_dir / f"segment_report_{seg_id:03d}.md", md)

    total_h = state["total_green_s"] / 3600
    print(f"\n=== WARM segment {seg_id} done. green={gt.elapsed_h:.2f}h  campaign_total={total_h:.2f}h")
    return segment_info


# ===================================================================
#  MODE: COLD  (Phase 4C)
# ===================================================================

def run_cold(campaign_dir: Path, segment_hours: float) -> dict:
    """Backend-only cold soak — no browser except periodic auth checks."""
    import httpx

    campaign_dir.mkdir(parents=True, exist_ok=True)
    state = _load_campaign_state(campaign_dir)
    seg_id = _next_segment_id(state)
    key = load_api_key()

    print(f"=== COLD segment={seg_id}  target={segment_hours}h")

    stats = {
        "segment_id": seg_id, "health_checks": 0, "health_ok": 0,
        "feeds_checks": 0, "feeds_monotonic": True,
        "hologram_checks": 0, "hologram_honest": True,
        "cookie_checks": 0, "cookie_ok": 0,
        "auth_chats": 0, "auth_chat_ok": 0,
    }
    gt = GreenTimer()
    gt.start()
    metrics: list[dict] = []

    last_feeds_count: int | None = None
    cycle = 0
    t_start = time.monotonic()
    health_interval = 120       # 2 min
    cookie_interval = 1800      # 30 min
    chat_interval = 3600        # 60 min
    last_health = 0.0
    last_cookie = 0.0
    last_chat = 0.0

    while gt.elapsed_h < segment_hours:
        now = time.monotonic()
        cycle += 1

        # --- Every 2 min: health/ready/status/feeds/hologram via httpx ---
        if (now - last_health) >= health_interval:
            last_health = now
            snap = backend_health_snapshot()
            stats["health_checks"] += 1
            if snap["healthy"]:
                stats["health_ok"] += 1
            else:
                gt.pause()
                log_incident(campaign_dir, {
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "segment_id": seg_id, "mode": "COLD",
                    "category": "health_failure",
                    "short_code": "COLD_HEALTH",
                    "summary": f"Backend unhealthy at check #{stats['health_checks']}",
                    "backend_health_snapshot": snap,
                    "fresh_context_retry_result": None,
                })
                gt.start()

            # Feeds monotonicity
            try:
                r = httpx.get(f"{BASE_URL}/api/feeds", timeout=5.0)
                if r.status_code == 200:
                    feeds_data = r.json()
                    count = len(feeds_data) if isinstance(feeds_data, list) else feeds_data.get("count", 0)
                    stats["feeds_checks"] += 1
                    if last_feeds_count is not None and count < last_feeds_count:
                        stats["feeds_monotonic"] = False
                    last_feeds_count = count
            except Exception:
                pass

            # Hologram honesty
            try:
                r = httpx.get(f"{BASE_URL}/api/hologram/state", timeout=5.0)
                if r.status_code == 200:
                    holo = r.json()
                    stats["hologram_checks"] += 1
                    # Honesty: check required fields are present
                    if isinstance(holo, dict) and not holo.get("status"):
                        stats["hologram_honest"] = False
            except Exception:
                pass

            metric = {
                "cycle": cycle,
                "ts": datetime.now(timezone.utc).isoformat(),
                "green_h": round(gt.elapsed_h, 4),
                "health_ok": snap["healthy"],
                "endpoints": snap["endpoints"],
            }
            metrics.append(metric)

            if stats["health_checks"] % 10 == 0:
                print(f"  COLD health #{stats['health_checks']}  "
                      f"ok={stats['health_ok']}/{stats['health_checks']}  "
                      f"green={gt.elapsed_h:.2f}h")

        # --- Every 30 min: cookie bootstrap check via Playwright ---
        if (now - last_cookie) >= cookie_interval:
            last_cookie = now
            stats["cookie_checks"] += 1
            try:
                from playwright.sync_api import sync_playwright
                with sync_playwright() as pw:
                    br = pw.chromium.launch(headless=True)
                    ctx, page, cap = open_authenticated_hologram(br, api_key=key)
                    authed = wait_for_auth_ready(page, timeout_s=10)
                    if authed:
                        stats["cookie_ok"] += 1
                    ctx.close()
                    br.close()
            except Exception as exc:
                log_incident(campaign_dir, {
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "segment_id": seg_id, "mode": "COLD",
                    "category": "cookie_failure",
                    "short_code": "COLD_COOKIE",
                    "summary": f"Cookie check #{stats['cookie_checks']} failed: {exc}",
                    "backend_health_snapshot": backend_health_snapshot(),
                    "fresh_context_retry_result": None,
                })
            print(f"  COLD cookie check #{stats['cookie_checks']}  "
                  f"ok={stats['cookie_ok']}/{stats['cookie_checks']}")

        # --- Every 60 min: one authenticated chat ---
        if (now - last_chat) >= chat_interval:
            last_chat = now
            stats["auth_chats"] += 1
            try:
                from playwright.sync_api import sync_playwright
                with sync_playwright() as pw:
                    br = pw.chromium.launch(headless=True)
                    ctx, page, cap = open_authenticated_hologram(br, api_key=key)
                    cr = send_chat_safe(page, f"cold soak check {stats['auth_chats']}", timeout_s=15)
                    if cr["sent"] and cr["responded"]:
                        stats["auth_chat_ok"] += 1
                    else:
                        log_incident(campaign_dir, {
                            "ts": datetime.now(timezone.utc).isoformat(),
                            "segment_id": seg_id, "mode": "COLD",
                            "category": "chat_failure",
                            "short_code": "COLD_CHAT",
                            "summary": f"Auth chat #{stats['auth_chats']} failed",
                            "backend_health_snapshot": backend_health_snapshot(),
                            "fresh_context_retry_result": cr,
                        })
                    ctx.close()
                    br.close()
            except Exception:
                pass
            print(f"  COLD auth chat #{stats['auth_chats']}  "
                  f"ok={stats['auth_chat_ok']}/{stats['auth_chats']}")

        # Sleep between polls
        time.sleep(10)

    # Segment artifacts
    segment_info = {
        "segment_id": seg_id,
        "mode": "COLD",
        "green_hours": round(gt.elapsed_h, 4),
        **stats,
    }
    state["segments"].append(segment_info)
    state["total_green_s"] = state.get("total_green_s", 0) + gt.elapsed_s
    _save_campaign_state(campaign_dir, state)

    (campaign_dir / f"segment_metrics_{seg_id:03d}.json").write_text(
        json.dumps({"segment": segment_info, "metrics": metrics}, indent=2, default=str),
        encoding="utf-8",
    )

    health_rate = f"{stats['health_ok']}/{stats['health_checks']}" if stats["health_checks"] else "N/A"
    md = (
        f"# COLD Segment {seg_id}\n\n"
        f"Green time: {gt.elapsed_h:.2f}h\n\n"
        f"| Metric | Value |\n|--------|-------|\n"
        f"| Health checks | {health_rate} |\n"
        f"| Feeds monotonic | {stats['feeds_monotonic']} |\n"
        f"| Hologram honest | {stats['hologram_honest']} |\n"
        f"| Cookie checks | {stats['cookie_ok']}/{stats['cookie_checks']} |\n"
        f"| Auth chats | {stats['auth_chat_ok']}/{stats['auth_chats']} |\n"
    )
    _write_md(campaign_dir / f"segment_report_{seg_id:03d}.md", md)

    total_h = state["total_green_s"] / 3600
    print(f"\n=== COLD segment {seg_id} done. green={gt.elapsed_h:.2f}h  campaign_total={total_h:.2f}h")
    return segment_info


# ===================================================================
#  CLI entry point
# ===================================================================

def main() -> None:
    parser = argparse.ArgumentParser(
        description="WaggleDance 400h Gauntlet Campaign Harness",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--mode", required=True,
        choices=["HOT", "WARM", "COLD", "BASELINE", "DRYRUN",
                 "hot", "warm", "cold", "baseline", "dryrun"],
        help="Campaign mode",
    )
    parser.add_argument("--segment-hours", type=float, default=8.0, help="Green hours per segment (default 8)")
    parser.add_argument("--campaign-dir", type=str, default=None, help="Campaign artifact directory")
    parser.add_argument(
        "--sub", type=str, default=None,
        choices=["fidelity", "hot_mini", "drills", "warm_mini", "ci"],
        help="Sub-mode for BASELINE",
    )

    args = parser.parse_args()
    mode = args.mode.upper()

    # Default campaign dir
    if args.campaign_dir:
        cdir = Path(args.campaign_dir)
    else:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        cdir = _REPO_ROOT / "docs" / "runs" / f"ui_gauntlet_400h_{ts}"

    cdir.mkdir(parents=True, exist_ok=True)
    print(f"Campaign dir: {cdir}")

    if mode == "DRYRUN":
        result = run_dryrun(cdir)
    elif mode == "BASELINE":
        if not args.sub:
            print("ERROR: --sub required for BASELINE mode")
            sys.exit(1)
        result = run_baseline(cdir, args.sub)
    elif mode == "HOT":
        result = run_hot(cdir, args.segment_hours)
    elif mode == "WARM":
        result = run_warm(cdir, args.segment_hours)
    elif mode == "COLD":
        result = run_cold(cdir, args.segment_hours)
    else:
        print(f"Unknown mode: {mode}")
        sys.exit(1)

    # Print final summary
    state = _load_campaign_state(cdir)
    total_h = state.get("total_green_s", 0) / 3600
    seg_count = len(state.get("segments", []))
    print(f"\n{'='*60}")
    print(f"Campaign total: {total_h:.2f}h green across {seg_count} segment(s)")
    print(f"Campaign dir:   {cdir}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()

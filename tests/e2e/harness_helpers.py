"""Shared reusable helpers for the WaggleDance 400h gauntlet campaign.

This module is intentionally NOT prefixed with ``test_`` so pytest
will not collect it.  Import from both the legacy harness and the
new ``ui_gauntlet_400h.py`` campaign runner.

Provides:
    - Constants & key loading
    - ConsoleCapture (console + request failure tracking)
    - backend_health_snapshot() — multi-endpoint health check via httpx
    - open_authenticated_hologram() — fresh browser context with auth
    - wait_for_auth_ready() — poll /api/auth/check
    - ensure_tab_selected() — click + verify tab
    - wait_for_chat_ready() — readiness gate for chat input
    - send_chat_safe() — fill / send / poll / classify
    - log_incident() — append to incident_log.jsonl
    - controlled_server_restart() — real stop + relaunch via psutil
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

# ---------------------------------------------------------------------------
# Constants & key loading
# ---------------------------------------------------------------------------

BASE_URL = os.environ.get("GAUNTLET_BASE_URL", "http://127.0.0.1:8002")

TABS = [
    "overview", "memory", "reasoning", "micro", "learning",
    "feeds", "ops", "mesh", "trace", "magma", "chat",
]

VIEWPORTS = [
    {"width": 1280, "height": 720, "label": "1280x720"},
    {"width": 1536, "height": 864, "label": "1536x864"},
    {"width": 1920, "height": 1080, "label": "1920x1080"},
]

_KEY_FILE = os.path.join(tempfile.gettempdir(), "waggle_gauntlet_8002.key")


def load_api_key() -> str:
    """Read the ephemeral API key from the well-known temp file.  Never print."""
    if os.path.isfile(_KEY_FILE):
        with open(_KEY_FILE, "r") as f:
            return f.read().strip()
    return ""


# ---------------------------------------------------------------------------
# XSS init script — injected into every browser context
# ---------------------------------------------------------------------------

XSS_INIT_SCRIPT = """\
window.__xss_detected = false;
window.alert   = function() { window.__xss_detected = true; };
window.confirm = function() { window.__xss_detected = true; return false; };
window.prompt  = function() { window.__xss_detected = true; return null; };
"""


# ---------------------------------------------------------------------------
# ConsoleCapture
# ---------------------------------------------------------------------------

class ConsoleCapture:
    """Collect console messages and failed network requests from a Playwright page."""

    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.failed_requests: list[dict] = []

    def attach(self, page) -> None:
        page.on("console", self._on_console)
        page.on("requestfailed", self._on_req_fail)

    def _on_console(self, msg) -> None:
        if msg.type == "error":
            self.errors.append(msg.text)
        elif msg.type == "warning":
            self.warnings.append(msg.text)

    def _on_req_fail(self, req) -> None:
        self.failed_requests.append({"url": req.url, "failure": req.failure})

    def reset(self) -> None:
        self.errors.clear()
        self.warnings.clear()
        self.failed_requests.clear()

    def summary(self) -> dict:
        return {
            "console_errors": len(self.errors),
            "console_warnings": len(self.warnings),
            "failed_requests": len(self.failed_requests),
            "error_texts": self.errors[:20],
            "failed_request_urls": [r["url"] for r in self.failed_requests[:10]],
        }


# ---------------------------------------------------------------------------
# backend_health_snapshot
# ---------------------------------------------------------------------------

_HEALTH_ENDPOINTS = [
    ("health", "/health"),
    ("ready", "/ready"),
    ("status", "/api/status"),
    ("feeds", "/api/feeds"),
    ("hologram_state", "/api/hologram/state"),
]


def backend_health_snapshot(base_url: str | None = None) -> dict:
    """Hit backend health endpoints via httpx.  Returns structured snapshot."""
    base = base_url or BASE_URL
    endpoints: dict[str, dict] = {}
    all_ok = True
    for name, path in _HEALTH_ENDPOINTS:
        t0 = time.monotonic()
        try:
            r = httpx.get(f"{base}{path}", timeout=5.0)
            latency = round((time.monotonic() - t0) * 1000)
            ok = 200 <= r.status_code < 400
            endpoints[name] = {"status": r.status_code, "latency_ms": latency, "ok": ok}
            if not ok:
                all_ok = False
        except Exception as exc:
            latency = round((time.monotonic() - t0) * 1000)
            endpoints[name] = {"status": 0, "latency_ms": latency, "ok": False, "error": str(exc)[:120]}
            all_ok = False
    return {
        "healthy": all_ok,
        "endpoints": endpoints,
        "ts": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# open_authenticated_hologram
# ---------------------------------------------------------------------------

def open_authenticated_hologram(
    browser,
    viewport: dict | None = None,
    api_key: str | None = None,
) -> tuple[Any, Any, ConsoleCapture]:
    """Create a fresh browser context, navigate to /hologram with auth, return (ctx, page, capture)."""
    vp = viewport or {"width": 1920, "height": 1080}
    key = api_key or load_api_key()

    ctx = browser.new_context(viewport=vp)
    page = ctx.new_page()
    page.set_default_timeout(30_000)       # 30 s max for any Playwright action
    page.set_default_navigation_timeout(60_000)

    # Install XSS override
    page.add_init_script(XSS_INIT_SCRIPT)

    # Navigate
    page.goto(
        f"{BASE_URL}/hologram?token={key}",
        wait_until="domcontentloaded",
        timeout=60_000,
    )
    page.wait_for_timeout(2000)

    # Auth readiness gate
    wait_for_auth_ready(page)

    # Attach console capture
    cap = ConsoleCapture()
    cap.attach(page)

    return ctx, page, cap


# ---------------------------------------------------------------------------
# wait_for_auth_ready
# ---------------------------------------------------------------------------

def wait_for_auth_ready(page, timeout_s: int = 10) -> bool:
    """Poll /api/auth/check via page.evaluate every 1 s.  Return authenticated state."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            authed = page.evaluate("""
                async () => {
                    try {
                        const c = new AbortController();
                        const t = setTimeout(() => c.abort(), 5000);
                        const r = await fetch('/api/auth/check',
                            {credentials: 'same-origin', signal: c.signal});
                        clearTimeout(t);
                        const d = await r.json();
                        return d.authenticated === true;
                    } catch { return false; }
                }
            """)
            if authed:
                return True
        except Exception:
            pass
        page.wait_for_timeout(1000)
    return False


# ---------------------------------------------------------------------------
# ensure_tab_selected
# ---------------------------------------------------------------------------

def ensure_tab_selected(page, tab_name: str) -> bool:
    """Click the tab button matching *tab_name* and verify selection if possible."""
    selectors = [
        f"button:has-text('{tab_name}')",
        f"[data-tab='{tab_name}']",
        f"#{tab_name}-tab",
        f".tab-btn:has-text('{tab_name}')",
        f"[role=tab]:has-text('{tab_name}')",
    ]
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if loc.count() and loc.is_visible(timeout=2000):
                loc.click()
                page.wait_for_timeout(500)
                # Verify active/selected if DOM exposes aria-selected
                try:
                    selected = loc.get_attribute("aria-selected")
                    if selected == "true":
                        return True
                    cls = loc.get_attribute("class") or ""
                    if "active" in cls or "selected" in cls:
                        return True
                except Exception:
                    pass
                return True  # clicked successfully, no selection state to verify
        except Exception:
            continue
    # Fallback: iterate all button/tab elements
    try:
        tabs = page.locator("button, [role=tab]")
        for i in range(tabs.count()):
            txt = tabs.nth(i).inner_text(timeout=1000).strip().lower()
            if tab_name.lower() in txt:
                tabs.nth(i).click()
                page.wait_for_timeout(500)
                return True
    except Exception:
        pass
    return False


# ---------------------------------------------------------------------------
# wait_for_chat_ready
# ---------------------------------------------------------------------------

def wait_for_chat_ready(page, max_retries: int = 2) -> bool:
    """Gate: ensure chat tab is selected and chat input is visible + enabled.

    Returns True when chat input is ready, False if recovery is needed
    (caller should create a fresh context).
    """
    for attempt in range(max_retries + 1):
        # 1. Select chat tab
        ensure_tab_selected(page, "chat") or ensure_tab_selected(page, "Chat")

        # 2. Check chat input exists + visible
        try:
            ci = page.locator("input[placeholder='Type a message...']")
            if ci.count() and ci.is_visible(timeout=3000):
                return True
        except Exception:
            pass

        if attempt < max_retries:
            page.wait_for_timeout(2000)

    # Chat input still missing — check if auth is lost
    try:
        authed = page.evaluate("""
            async () => {
                try {
                    const c = new AbortController();
                    const t = setTimeout(() => c.abort(), 5000);
                    const r = await fetch('/api/auth/check',
                        {credentials: 'same-origin', signal: c.signal});
                    clearTimeout(t);
                    const d = await r.json();
                    return d.authenticated === true;
                } catch { return false; }
            }
        """)
        if not authed:
            return False  # auth lost → caller should bootstrap fresh
    except Exception:
        pass
    return False


# ---------------------------------------------------------------------------
# send_chat_safe
# ---------------------------------------------------------------------------

def send_chat_safe(page, query: str, timeout_s: int = 10) -> dict:
    """Fill chat input, send, poll for response, classify result.

    Returns dict with keys:
        sent, responded, latency_ms, xss_detected, dom_ok, session_ok, error
    """
    result: dict[str, Any] = {
        "sent": False,
        "responded": False,
        "latency_ms": 0,
        "xss_detected": False,
        "dom_ok": True,
        "session_ok": True,
        "error": "",
    }

    # Pre-check: chat readiness
    if not wait_for_chat_ready(page, max_retries=1):
        result["error"] = "chat_not_ready"
        return result

    try:
        ci = page.locator("input[placeholder='Type a message...']")
        q = query[:8000].replace("\n", " ").replace("\r", " ")
        ci.fill("")
        ci.fill(q)

        # Snapshot body length before send
        body_len_before = page.evaluate("() => document.body.innerText.length")

        t0 = time.time()
        ci.press("Enter")
        result["sent"] = True

        # Poll for response growth
        wait_limit = min(timeout_s, 30)
        elapsed = 0.0
        while elapsed < wait_limit:
            page.wait_for_timeout(1000)
            elapsed = time.time() - t0
            body_len = page.evaluate("() => document.body.innerText.length")
            if body_len > body_len_before + 20 and elapsed > 2:
                break

        result["latency_ms"] = round((time.time() - t0) * 1000)

        # Response visible?
        body_len_after = page.evaluate("() => document.body.innerText.length")
        result["responded"] = body_len_after > body_len_before + 20

        # XSS flag
        result["xss_detected"] = page.evaluate("() => window.__xss_detected || false")

        # DOM ok
        result["dom_ok"] = page.locator("body").is_visible(timeout=2000)

        # Session check
        try:
            authed = page.evaluate("""async () => {
                try {
                    const c = new AbortController();
                    const t = setTimeout(() => c.abort(), 5000);
                    const r = await fetch('/api/auth/check',
                        {credentials: 'same-origin', signal: c.signal});
                    clearTimeout(t);
                    const d = await r.json();
                    return d.authenticated;
                } catch { return false; }
            }""")
            result["session_ok"] = bool(authed)
        except Exception:
            result["session_ok"] = False

    except Exception as exc:
        result["error"] = str(exc)[:200]

    return result


# ---------------------------------------------------------------------------
# log_incident
# ---------------------------------------------------------------------------

def log_incident(campaign_dir: str | Path, incident: dict) -> None:
    """Append one incident record to ``incident_log.jsonl`` inside *campaign_dir*.

    Required fields (caller must supply):
        ts, segment_id, mode, category, short_code, summary,
        backend_health_snapshot, fresh_context_retry_result
    """
    path = Path(campaign_dir) / "incident_log.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    # Ensure required fields present (default to None if missing)
    required = [
        "ts", "segment_id", "mode", "category", "short_code",
        "summary", "backend_health_snapshot", "fresh_context_retry_result",
    ]
    for key in required:
        incident.setdefault(key, None)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(incident, ensure_ascii=False, default=str) + "\n")


# ---------------------------------------------------------------------------
# controlled_server_restart
# ---------------------------------------------------------------------------

def controlled_server_restart(port: int = 8002) -> dict:
    """Stop the running gauntlet server on *port*, relaunch, and verify health.

    Steps:
        1. Find server PID via psutil
        2. Verify health endpoints before stop
        3. Kill process cleanly (SIGTERM / taskkill)
        4. Wait until port free (poll up to 15 s)
        5. Restart via _launch_gauntlet_server.py pattern
        6. Wait until /health returns 200 (poll up to 60 s)
    Returns dict with pass, stop_ok, port_freed, restart_ok, health_ok, duration_s
    """
    import psutil  # imported here to keep module importable without psutil

    result = {
        "pass": False,
        "stop_ok": False,
        "port_freed": False,
        "restart_ok": False,
        "health_ok": False,
        "duration_s": 0.0,
    }
    t0 = time.monotonic()

    # --- 1. Find server PID ---
    server_pid: int | None = None
    try:
        for conn in psutil.net_connections(kind="tcp"):
            if conn.laddr.port == port and conn.status == "LISTEN":
                server_pid = conn.pid
                break
    except (psutil.AccessDenied, psutil.NoSuchProcess):
        pass

    if server_pid is None:
        result["error"] = f"no listener on port {port}"
        result["duration_s"] = round(time.monotonic() - t0, 2)
        return result

    # --- 2. Pre-stop health ---
    pre_health = backend_health_snapshot()

    # --- 3. Kill process ---
    try:
        proc = psutil.Process(server_pid)
        if sys.platform == "win32":
            proc.terminate()  # SIGTERM equivalent on Windows
        else:
            os.kill(server_pid, signal.SIGTERM)
        proc.wait(timeout=10)
        result["stop_ok"] = True
    except Exception as exc:
        result["stop_error"] = str(exc)[:120]
        # Force kill as fallback
        try:
            proc.kill()
            proc.wait(timeout=5)
            result["stop_ok"] = True
        except Exception:
            pass

    if not result["stop_ok"]:
        result["duration_s"] = round(time.monotonic() - t0, 2)
        return result

    # --- 4. Wait until port is free ---
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        port_in_use = False
        try:
            for conn in psutil.net_connections(kind="tcp"):
                if conn.laddr.port == port and conn.status == "LISTEN":
                    port_in_use = True
                    break
        except Exception:
            pass
        if not port_in_use:
            result["port_freed"] = True
            break
        time.sleep(1)

    if not result["port_freed"]:
        result["duration_s"] = round(time.monotonic() - t0, 2)
        return result

    # --- 5. Restart server ---
    project_root = Path(__file__).resolve().parents[2]
    launch_script = project_root / "docs" / "runs" / "ui_gauntlet_20260412" / "_launch_gauntlet_server.py"

    if not launch_script.exists():
        # Fallback: launch via start_waggledance.py directly
        launch_cmd = [sys.executable, str(project_root / "start_waggledance.py"), "--port", str(port)]
    else:
        launch_cmd = [sys.executable, str(launch_script)]

    try:
        env = os.environ.copy()
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        subprocess.Popen(
            launch_cmd,
            cwd=str(project_root),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
        )
        result["restart_ok"] = True
    except Exception as exc:
        result["restart_error"] = str(exc)[:120]
        result["duration_s"] = round(time.monotonic() - t0, 2)
        return result

    # --- 6. Wait for /health 200 ---
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        try:
            r = httpx.get(f"{BASE_URL}/health", timeout=3.0)
            if r.status_code == 200:
                result["health_ok"] = True
                break
        except Exception:
            pass
        time.sleep(2)

    result["pass"] = all([
        result["stop_ok"], result["port_freed"],
        result["restart_ok"], result["health_ok"],
    ])
    result["duration_s"] = round(time.monotonic() - t0, 2)
    result["pre_health"] = pre_health
    return result

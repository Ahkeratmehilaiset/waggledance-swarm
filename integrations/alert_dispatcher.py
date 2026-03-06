"""
WaggleDance — Phase 5: Alert Dispatcher
=========================================
Async queue + consumer loop for sending alerts via Telegram and Webhook.
Rate limiting per source. Retry logic for webhooks.

Used by: FrigateIntegration, SensorHub
"""

import asyncio
import json
import logging
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional

log = logging.getLogger("alert_dispatcher")

# Severity levels
SEVERITY_CRITICAL = "critical"
SEVERITY_HIGH = "high"
SEVERITY_MEDIUM = "medium"
SEVERITY_INFO = "info"

SEVERITY_EMOJI = {
    SEVERITY_CRITICAL: "\U0001f534",  # 🔴
    SEVERITY_HIGH: "\U0001f7e0",      # 🟠
    SEVERITY_MEDIUM: "\U0001f7e1",    # 🟡
    SEVERITY_INFO: "\U0001f7e2",      # 🟢
}

SEVERITY_ORDER = {
    SEVERITY_CRITICAL: 4,
    SEVERITY_HIGH: 3,
    SEVERITY_MEDIUM: 2,
    SEVERITY_INFO: 1,
}


@dataclass
class Alert:
    """Single alert to be dispatched."""
    severity: str
    title: str
    message: str
    source: str
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    metadata: dict = field(default_factory=dict)


class AlertDispatcher:
    """Async alert dispatcher with Telegram + Webhook support and rate limiting."""

    def __init__(self, config: dict):
        self.enabled = config.get("enabled", False)

        # Telegram config (M7: prefer env vars for secrets)
        import os
        tg_cfg = config.get("telegram", {})
        self.telegram_enabled = tg_cfg.get("enabled", False)
        self.telegram_bot_token = os.environ.get("WAGGLEDANCE_TELEGRAM_BOT_TOKEN", "") or tg_cfg.get("bot_token", "")
        self.telegram_chat_id = os.environ.get("WAGGLEDANCE_TELEGRAM_CHAT_ID", "") or tg_cfg.get("chat_id", "")

        # Webhook config
        wh_cfg = config.get("webhook", {})
        self.webhook_enabled = wh_cfg.get("enabled", False)
        self.webhook_url = wh_cfg.get("url", "")
        self.webhook_headers = wh_cfg.get("headers", {})

        # Rate limiting
        rl_cfg = config.get("rate_limit", {})
        self.max_per_minute = rl_cfg.get("max_per_minute", 5)
        self._rate_windows: dict[str, deque] = defaultdict(
            lambda: deque(maxlen=100)
        )

        # Min severity for dispatch (only >= HIGH triggers external alerts)
        self.min_severity = config.get("min_severity", SEVERITY_HIGH)

        # Async queue
        self._queue: Optional[asyncio.Queue] = None
        self._task: Optional[asyncio.Task] = None
        self._running = False

        # Stats
        self._sent_count = 0
        self._rate_limited_count = 0
        self._telegram_errors = 0
        self._webhook_errors = 0
        self._recent_alerts: deque = deque(maxlen=50)

    async def start(self):
        """Start consumer loop."""
        if not self.enabled:
            log.info("Alert Dispatcher disabled")
            return

        self._queue = asyncio.Queue()
        self._running = True
        self._task = asyncio.create_task(self._consumer_loop())
        log.info("Alert Dispatcher started")

    async def stop(self):
        """Stop consumer loop and flush queue."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        log.info("Alert Dispatcher stopped")

    async def send_alert(self, alert: Alert):
        """Enqueue alert for dispatch."""
        if not self.enabled or self._queue is None:
            return

        # Check minimum severity
        if SEVERITY_ORDER.get(alert.severity, 0) < SEVERITY_ORDER.get(self.min_severity, 0):
            return

        self._recent_alerts.append({
            "severity": alert.severity,
            "title": alert.title,
            "source": alert.source,
            "timestamp": alert.timestamp,
        })

        await self._queue.put(alert)

    async def _consumer_loop(self):
        """Process alerts from queue."""
        while self._running:
            try:
                alert = await asyncio.wait_for(
                    self._queue.get(), timeout=5.0
                )
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

            # Rate limit check
            if self._is_rate_limited(alert.source):
                self._rate_limited_count += 1
                log.info(
                    f"Alert rate-limited: {alert.source} "
                    f"({self._rate_limited_count} total)"
                )
                continue

            # Record for rate limiting
            self._rate_windows[alert.source].append(time.monotonic())

            # Dispatch to configured channels
            tasks = []
            if self.telegram_enabled and self.telegram_bot_token:
                tasks.append(self._send_telegram(alert))
            if self.webhook_enabled and self.webhook_url:
                tasks.append(self._send_webhook(alert))

            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
                self._sent_count += 1

    def _is_rate_limited(self, source: str) -> bool:
        """Check sliding window rate limit for source."""
        now = time.monotonic()
        window = self._rate_windows[source]

        # Remove entries older than 60s
        while window and (now - window[0]) > 60:
            window.popleft()

        return len(window) >= self.max_per_minute

    async def _send_telegram(self, alert: Alert):
        """Send alert via Telegram Bot API."""
        import aiohttp

        emoji = SEVERITY_EMOJI.get(alert.severity, "")
        text = (
            f"{emoji} <b>{alert.title}</b>\n\n"
            f"{alert.message}\n\n"
            f"<i>Lähde: {alert.source} | {alert.timestamp}</i>"
        )

        url = f"https://api.telegram.org/bot{self.telegram_bot_token}/sendMessage"
        payload = {
            "chat_id": self.telegram_chat_id,
            "text": text,
            "parse_mode": "HTML",
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url, json=payload,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status != 200:
                        body = await resp.text()
                        log.warning(f"Telegram API error {resp.status}: {body}")
                        self._telegram_errors += 1
                    else:
                        log.info(f"Telegram alert sent: {alert.title}")
        except Exception as e:
            self._telegram_errors += 1
            log.warning(f"Telegram send failed: {e}")

    async def _send_webhook(self, alert: Alert):
        """Send alert via webhook with retry."""
        import aiohttp

        payload = {
            "severity": alert.severity,
            "title": alert.title,
            "message": alert.message,
            "source": alert.source,
            "timestamp": alert.timestamp,
            "metadata": alert.metadata,
        }

        headers = {"Content-Type": "application/json"}
        headers.update(self.webhook_headers)

        for attempt in range(3):
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        self.webhook_url,
                        json=payload,
                        headers=headers,
                        timeout=aiohttp.ClientTimeout(total=10),
                    ) as resp:
                        if resp.status < 300:
                            log.info(f"Webhook alert sent: {alert.title}")
                            return
                        log.warning(
                            f"Webhook returned {resp.status} "
                            f"(attempt {attempt + 1}/3)"
                        )
            except Exception as e:
                log.warning(
                    f"Webhook failed (attempt {attempt + 1}/3): {e}"
                )

            if attempt < 2:
                await asyncio.sleep(2)

        self._webhook_errors += 1
        log.warning(f"Webhook failed after 3 attempts: {alert.title}")

    def get_status(self) -> dict:
        """Status dict for dashboard/API."""
        return {
            "enabled": self.enabled,
            "running": self._running,
            "telegram_enabled": self.telegram_enabled,
            "webhook_enabled": self.webhook_enabled,
            "sent_count": self._sent_count,
            "rate_limited_count": self._rate_limited_count,
            "telegram_errors": self._telegram_errors,
            "webhook_errors": self._webhook_errors,
            "recent_alerts": list(self._recent_alerts),
        }

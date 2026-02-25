"""
WaggleDance — Phase 8: Data Feed Scheduler
============================================
Orchestrates all external data feeds (weather, electricity, RSS).
Runs as background asyncio task. Respects PriorityLock — pauses when user is chatting.
"""

import asyncio
import logging
import time
from typing import Optional

log = logging.getLogger("data_scheduler")


class DataFeedScheduler:
    """Orchestrates all external data feeds with PriorityLock integration."""

    DEFAULTS = {
        "weather": {"interval_min": 30},
        "electricity": {"interval_min": 15},
        "rss": {"interval_min": 120},
    }

    def __init__(self, config: dict, consciousness, priority_lock):
        self.config = config
        self.consciousness = consciousness
        self.priority = priority_lock
        self.enabled = config.get("enabled", False)
        self._feeds: dict = {}       # name -> feed instance
        self._last_run: dict = {}    # name -> monotonic timestamp
        self._run_counts: dict = {}  # name -> int
        self._error_counts: dict = {}  # name -> int
        self._task: Optional[asyncio.Task] = None
        self._running = False

        if self.enabled:
            self._init_feeds()

    def _init_feeds(self):
        """Lazy-import and create enabled feed instances."""
        # Weather feed
        weather_cfg = self.config.get("weather", {})
        if weather_cfg.get("enabled", True):
            try:
                from integrations.weather_feed import WeatherFeed
                self._feeds["weather"] = WeatherFeed(weather_cfg)
                self._run_counts["weather"] = 0
                self._error_counts["weather"] = 0
                log.info(f"Weather feed initialized: {weather_cfg.get('locations', ['Helsinki'])}")
            except Exception as e:
                log.warning(f"Weather feed init failed: {e}")

        # Electricity feed
        elec_cfg = self.config.get("electricity", {})
        if elec_cfg.get("enabled", True):
            try:
                from integrations.electricity_feed import ElectricityFeed
                self._feeds["electricity"] = ElectricityFeed(elec_cfg)
                self._run_counts["electricity"] = 0
                self._error_counts["electricity"] = 0
                log.info("Electricity feed initialized")
            except Exception as e:
                log.warning(f"Electricity feed init failed: {e}")

        # RSS feed
        rss_cfg = self.config.get("rss", {})
        if rss_cfg.get("enabled", True):
            try:
                from integrations.rss_feed import RSSFeedMonitor
                self._feeds["rss"] = RSSFeedMonitor(rss_cfg)
                self._run_counts["rss"] = 0
                self._error_counts["rss"] = 0
                log.info(f"RSS feed initialized: {len(rss_cfg.get('feeds', []))} feeds")
            except Exception as e:
                log.warning(f"RSS feed init failed: {e}")

    async def start(self):
        """Launch background _run_loop() as asyncio task."""
        if not self.enabled or not self._feeds:
            log.info("Data feeds disabled or no feeds configured")
            return

        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        log.info(f"Data feed scheduler started with {len(self._feeds)} feeds")

    async def stop(self):
        """Cancel background task."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        log.info("Data feed scheduler stopped")

    async def _run_loop(self):
        """Main loop: check each feed, call update_context().

        Respects PriorityLock — waits if user is chatting.
        Sleeps 60s between cycles. Catches exceptions per-feed.
        """
        # Initial delay to let system finish startup
        await asyncio.sleep(5)

        while self._running:
            try:
                for feed_name, feed in self._feeds.items():
                    if not self._running:
                        break

                    if not self._is_due(feed_name):
                        continue

                    # CRITICAL: respect PriorityLock — block if user chatting
                    try:
                        await self.priority.wait_if_chat()
                    except Exception:
                        pass

                    try:
                        stored = await feed.update_context(self.consciousness)
                        self._last_run[feed_name] = time.monotonic()
                        self._run_counts[feed_name] = (
                            self._run_counts.get(feed_name, 0) + 1
                        )
                        if stored > 0:
                            log.info(
                                f"Feed {feed_name}: {stored} facts stored "
                                f"(run #{self._run_counts[feed_name]})"
                            )
                    except Exception as e:
                        self._error_counts[feed_name] = (
                            self._error_counts.get(feed_name, 0) + 1
                        )
                        log.warning(f"Feed {feed_name} failed: {e}")

            except asyncio.CancelledError:
                break
            except Exception as e:
                log.warning(f"Data feed scheduler error: {e}")

            # Sleep 60s between cycles
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                break

    def _is_due(self, feed_name: str) -> bool:
        """Check if feed is due based on configured interval."""
        last = self._last_run.get(feed_name)
        if last is None:
            return True  # Never run before → run now

        # Get interval from feed-specific config, or defaults
        feed_cfg = self.config.get(feed_name, {})
        default_interval = self.DEFAULTS.get(feed_name, {}).get("interval_min", 60)
        interval_min = feed_cfg.get("interval_min", default_interval)
        interval_s = interval_min * 60

        return (time.monotonic() - last) >= interval_s

    async def force_update(self, feed_name: str) -> int:
        """Force immediate feed update. Returns facts stored."""
        feed = self._feeds.get(feed_name)
        if not feed:
            log.warning(f"Unknown feed: {feed_name}")
            return 0

        try:
            stored = await feed.update_context(self.consciousness)
            self._last_run[feed_name] = time.monotonic()
            self._run_counts[feed_name] = (
                self._run_counts.get(feed_name, 0) + 1
            )
            return stored
        except Exception as e:
            self._error_counts[feed_name] = (
                self._error_counts.get(feed_name, 0) + 1
            )
            log.warning(f"Force update {feed_name} failed: {e}")
            return 0

    def get_status(self) -> dict:
        """Returns comprehensive status dict for dashboard/API."""
        feeds_status = {}
        for name, feed in self._feeds.items():
            last_run = self._last_run.get(name)
            feeds_status[name] = {
                "active": True,
                "last_run": last_run,
                "last_run_ago_s": (
                    int(time.monotonic() - last_run) if last_run else None
                ),
                "run_count": self._run_counts.get(name, 0),
                "error_count": self._error_counts.get(name, 0),
                "stats": feed.stats if hasattr(feed, "stats") else {},
            }

        return {
            "enabled": self.enabled,
            "running": self._running,
            "feeds": feeds_status,
        }

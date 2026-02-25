"""
WaggleDance — Phase 8: RSS Feed Monitor
=========================================
Monitors beekeeping and agricultural RSS feeds.
Critical disease alerts (esikotelomätä, toukkamätä, etc.) trigger immediate flags.

Stores news facts in ChromaDB via consciousness.learn().
"""

import asyncio
import logging
import time
from typing import Optional

log = logging.getLogger("rss_feed")

# Disease and pest keywords that trigger critical alerts
CRITICAL_KEYWORDS = [
    "esikotelomätä",
    "toukkamätä",
    "foulbrood",
    "afb",
    "efb",
    "american foulbrood",
    "european foulbrood",
    "hive beetle",
    "vespa velutina",
    "pienen pesäkovakuoriaisen",
]


class RSSFeedMonitor:
    """Monitors RSS feeds with critical disease alert detection."""

    def __init__(self, config: dict):
        self.feeds = config.get("feeds", [])
        self._seen_ids: set = set()
        self._alerts: list = []  # Critical alerts history
        self._total_processed = 0
        self._total_stored = 0
        self._last_update: Optional[float] = None
        self._errors = 0

    async def check_feeds(self) -> list[dict]:
        """Parse all feeds with feedparser. Deduplicates via _seen_ids.

        Returns list of new entries as dicts.
        """
        try:
            import feedparser
        except ImportError:
            log.warning("feedparser not installed — RSS feeds disabled")
            return []

        new_entries = []
        loop = asyncio.get_event_loop()

        for feed_cfg in self.feeds:
            url = feed_cfg.get("url", "")
            name = feed_cfg.get("name", url)
            if not url:
                continue

            try:
                # feedparser is sync — run in executor
                parsed = await loop.run_in_executor(None, feedparser.parse, url)
            except Exception as e:
                log.warning(f"RSS feed parse failed for {name}: {e}")
                self._errors += 1
                continue

            if not parsed or not hasattr(parsed, "entries"):
                continue

            for entry in parsed.entries:
                entry_id = getattr(entry, "id", None) or getattr(entry, "link", None)
                if not entry_id or entry_id in self._seen_ids:
                    continue

                self._seen_ids.add(entry_id)
                self._total_processed += 1

                new_entries.append({
                    "id": entry_id,
                    "title": getattr(entry, "title", ""),
                    "summary": getattr(entry, "summary", "")[:500],
                    "link": getattr(entry, "link", ""),
                    "source_name": name,
                    "published": getattr(entry, "published", ""),
                    "is_critical": feed_cfg.get("critical", False),
                })

        return new_entries

    def _is_critical(self, entry: dict) -> bool:
        """Check if entry title+summary contains critical disease keywords."""
        text = (entry.get("title", "") + " " + entry.get("summary", "")).lower()
        return any(kw.lower() in text for kw in CRITICAL_KEYWORDS)

    async def update_context(self, consciousness) -> int:
        """Check feeds, store new entries as Finnish facts in ChromaDB.

        Returns number of facts stored.
        """
        stored = 0
        entries = await self.check_feeds()

        for entry in entries:
            title = entry.get("title", "")
            summary = entry.get("summary", "")
            source = entry.get("source_name", "")
            link = entry.get("link", "")

            if not title:
                continue

            # Build Finnish text
            text = f"Uutinen ({source}): {title}"
            if summary:
                # Strip HTML tags from summary
                clean_summary = _strip_html(summary)
                if clean_summary:
                    text += f" — {clean_summary[:200]}"

            # Check if critical
            is_critical = self._is_critical(entry) or entry.get("is_critical", False)
            confidence = 0.80 if is_critical else 0.70

            if is_critical:
                alert = {
                    "title": title,
                    "source": source,
                    "link": link,
                    "timestamp": time.time(),
                    "keywords_matched": [
                        kw for kw in CRITICAL_KEYWORDS
                        if kw.lower() in (title + " " + summary).lower()
                    ],
                }
                self._alerts.append(alert)
                # Keep only last 50 alerts
                if len(self._alerts) > 50:
                    self._alerts = self._alerts[-50:]
                log.warning(f"CRITICAL RSS alert: {title} from {source}")

            consciousness.learn(
                text,
                agent_id="rss_feed",
                source_type="external_feed",
                confidence=confidence,
                metadata={
                    "category": "news",
                    "source_name": source,
                    "source_url": link,
                    "ttl_hours": 168,  # 1 week
                    "feed": "rss",
                    "is_critical": is_critical,
                },
            )
            stored += 1

        self._total_stored += stored
        self._last_update = time.monotonic()
        return stored

    @property
    def critical_alerts(self) -> list:
        """Return last 20 critical alerts."""
        return self._alerts[-20:]

    @property
    def stats(self) -> dict:
        return {
            "feeds_count": len(self.feeds),
            "seen_ids": len(self._seen_ids),
            "total_processed": self._total_processed,
            "total_stored": self._total_stored,
            "critical_alerts_count": len(self._alerts),
            "errors": self._errors,
            "last_update": self._last_update,
        }


def _strip_html(text: str) -> str:
    """Remove HTML tags from text."""
    import re
    clean = re.sub(r"<[^>]+>", "", text)
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean

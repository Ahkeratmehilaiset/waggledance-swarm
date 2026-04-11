"""FeedIngestSink — bridge DataFeedScheduler.learn(...) to VectorStorePort.upsert(...).

The legacy feeds (``integrations.weather_feed``, ``integrations.electricity_feed``,
``integrations.rss_feed``) call ``consciousness.learn(text, agent_id=..., ...)``
synchronously. The hex ``VectorStorePort.upsert`` is async and performs an
Ollama embed call (up to 30s) inside a thread. A single RSS cycle can emit 20+
items, so we buffer them through a bounded queue consumed by one background task.

This intentionally matches the ``queue + batch flush`` semantic of the legacy
``Consciousness.learn`` path:
- Validation (``len(text) >= 10``, ``confidence >= 0.2``) happens synchronously.
- Items are enqueued via ``put_nowait``; queue full → drop + warn + return False.
- The consumer drains serially, logging per-item errors without crashing.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from datetime import datetime, timezone
from typing import Any, Optional

log = logging.getLogger(__name__)

_SENTINEL = object()
_QUEUE_MAXSIZE = 500
_MIN_TEXT_LEN = 10
_MIN_CONFIDENCE = 0.2
_DEFAULT_COLLECTION = "waggle_memory"


def _now_iso_z() -> str:
    """ISO-8601 UTC timestamp ending in 'Z' (matches compat_dashboard._parse_ts)."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _derive_feed_id(metadata: dict) -> Optional[str]:
    """Map the feed metadata hint to the source id used by _build_feed_sources.

    We branch on ``metadata["feed"]`` (not ``agent_id``) because that is the
    explicit hint set by all three legacy feed modules.
    """
    feed = metadata.get("feed")
    if feed == "rss":
        name = metadata.get("source_name") or "Unknown RSS"
        return "rss_" + name.lower().replace("-", "_").replace(" ", "_")
    if feed == "fmi":
        return "weather_fmi"
    if feed == "porssisahko":
        return "electricity_porssisahko"
    return None


class FeedIngestSink:
    """Queue+consumer adapter exposing the legacy ``consciousness.learn`` API."""

    def __init__(
        self,
        vector_store,
        *,
        collection: str = _DEFAULT_COLLECTION,
        queue_maxsize: int = _QUEUE_MAXSIZE,
    ) -> None:
        self._vector_store = vector_store
        self._collection = collection
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=queue_maxsize)
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._dropped = 0
        self._ingested = 0

    # ---- lifecycle ----

    async def start(self) -> None:
        """Spawn the single consumer task."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._consume(), name="feed_ingest_sink")
        log.info("FeedIngestSink started")

    async def stop(self) -> None:
        """Drain the queue, then cancel the consumer task."""
        if not self._running:
            return
        self._running = False
        try:
            self._queue.put_nowait(_SENTINEL)
        except asyncio.QueueFull:
            pass
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=5.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._task.cancel()
                try:
                    await self._task
                except (asyncio.CancelledError, Exception):
                    pass
            self._task = None
        log.info(
            "FeedIngestSink stopped (ingested=%d, dropped=%d)",
            self._ingested,
            self._dropped,
        )

    # ---- legacy-compatible learn API ----

    def learn(
        self,
        text: str,
        agent_id: str,
        source_type: str = "external_feed",
        confidence: float = 0.9,
        validated: bool = False,  # noqa: ARG002 — legacy signature parity
        metadata: Optional[dict] = None,
        immediate: bool = False,  # noqa: ARG002 — legacy signature parity
    ) -> bool:
        """Validate and enqueue a fact for async upsert into the vector store.

        Returns ``False`` on validation failure or queue overflow, ``True`` on
        successful enqueue. Callers (feed modules) use this return value for
        their ``stored`` counters.
        """
        if not isinstance(text, str) or len(text) < _MIN_TEXT_LEN:
            return False
        if not isinstance(confidence, (int, float)) or confidence < _MIN_CONFIDENCE:
            return False

        meta: dict[str, Any] = dict(metadata or {})
        meta["agent_id"] = agent_id
        meta["source_type"] = source_type
        meta["confidence"] = float(confidence)
        meta["timestamp"] = _now_iso_z()

        feed_id = _derive_feed_id(meta)
        if feed_id is not None:
            meta["feed_id"] = feed_id

        doc_id = (
            f"{agent_id}_"
            f"{hashlib.md5(text.encode('utf-8')).hexdigest()[:8]}_"
            f"{int(time.time())}"
        )

        item = {
            "id": doc_id,
            "text": text,
            "metadata": meta,
            "collection": self._collection,
        }

        try:
            self._queue.put_nowait(item)
        except asyncio.QueueFull:
            self._dropped += 1
            log.warning(
                "FeedIngestSink queue full (maxsize=%d) — dropping %s",
                self._queue.maxsize,
                doc_id,
            )
            return False
        return True

    # ---- observability ----

    @property
    def stats(self) -> dict:
        return {
            "running": self._running,
            "queue_size": self._queue.qsize(),
            "queue_maxsize": self._queue.maxsize,
            "ingested": self._ingested,
            "dropped": self._dropped,
        }

    # ---- consumer ----

    async def _consume(self) -> None:
        while True:
            try:
                item = await self._queue.get()
            except asyncio.CancelledError:
                break
            if item is _SENTINEL:
                break
            try:
                await self._vector_store.upsert(
                    id=item["id"],
                    text=item["text"],
                    metadata=item["metadata"],
                    collection=item["collection"],
                )
                self._ingested += 1
            except Exception as exc:
                log.warning(
                    "FeedIngestSink upsert failed for %s: %s",
                    item.get("id"),
                    exc,
                )

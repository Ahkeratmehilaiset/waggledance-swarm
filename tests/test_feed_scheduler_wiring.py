"""Tests for v3.5.7 DataFeedScheduler wiring into the hex runtime.

Covers:
- Local PriorityLock sanity.
- FeedIngestSink validation, feed_id derivation, consumer drain, queue overflow.
- Container constructs / skips DataFeedScheduler based on feeds.enabled.
- compat_dashboard._get_chroma_collection handles the plural ``_collections`` dict.
"""

import asyncio
from typing import Any

import pytest


# ---------------------------------------------------------------------------
# PriorityLock
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_priority_lock_wait_if_chat_returns_immediately_when_set():
    from waggledance.core.priority_lock import PriorityLock

    lock = PriorityLock()
    await asyncio.wait_for(lock.wait_if_chat(), timeout=1.0)


# ---------------------------------------------------------------------------
# FeedIngestSink
# ---------------------------------------------------------------------------


class _FakeVectorStore:
    """Records upsert calls for assertions."""

    def __init__(self):
        self.calls: list[dict] = []

    async def upsert(self, id: str, text: str, metadata: dict, collection: str = "waggle_memory"):
        self.calls.append(
            {"id": id, "text": text, "metadata": metadata, "collection": collection}
        )


def _make_sink(vs=None, **kwargs):
    from waggledance.adapters.feeds.feed_ingest_sink import FeedIngestSink

    return FeedIngestSink(vector_store=vs or _FakeVectorStore(), **kwargs)


def test_feed_ingest_sink_validates_short_text():
    sink = _make_sink()
    assert sink.learn("short", agent_id="rss_feed", confidence=0.9) is False
    assert sink._queue.qsize() == 0


def test_feed_ingest_sink_validates_low_confidence():
    sink = _make_sink()
    ok = sink.learn(
        "this is long enough text for the validator",
        agent_id="rss_feed",
        confidence=0.1,
    )
    assert ok is False
    assert sink._queue.qsize() == 0


def test_feed_ingest_sink_feed_id_for_rss():
    sink = _make_sink()
    ok = sink.learn(
        "Breaking news item headline with enough length.",
        agent_id="rss_feed",
        confidence=0.9,
        metadata={"feed": "rss", "source_name": "Yle-uutiset"},
    )
    assert ok is True
    item = sink._queue.get_nowait()
    assert item["metadata"]["feed_id"] == "rss_yle_uutiset"
    assert item["metadata"]["agent_id"] == "rss_feed"
    assert item["metadata"]["timestamp"].endswith("Z")


def test_feed_ingest_sink_feed_id_fallback_to_unknown():
    sink = _make_sink()
    ok = sink.learn(
        "Another headline long enough for validation.",
        agent_id="rss_feed",
        confidence=0.9,
        metadata={"feed": "rss"},
    )
    assert ok is True
    item = sink._queue.get_nowait()
    assert item["metadata"]["feed_id"] == "rss_unknown_rss"


def test_feed_ingest_sink_feed_id_for_weather_and_electricity():
    sink = _make_sink()
    assert sink.learn(
        "Sää Helsinki: 5.2°C, tuuli 3 m/s",
        agent_id="weather_feed",
        confidence=0.95,
        metadata={"feed": "fmi", "location": "Helsinki"},
    )
    weather_item = sink._queue.get_nowait()
    assert weather_item["metadata"]["feed_id"] == "weather_fmi"

    assert sink.learn(
        "Sähkön spot-hinta nyt: 4.12 c/kWh",
        agent_id="electricity_feed",
        confidence=0.95,
        metadata={"feed": "porssisahko"},
    )
    elec_item = sink._queue.get_nowait()
    assert elec_item["metadata"]["feed_id"] == "electricity_porssisahko"


@pytest.mark.asyncio
async def test_feed_ingest_sink_drains_to_vector_store():
    vs = _FakeVectorStore()
    sink = _make_sink(vs)
    await sink.start()
    try:
        assert sink.learn(
            "RSS headline long enough for validation check.",
            agent_id="rss_feed",
            confidence=0.9,
            metadata={"feed": "rss", "source_name": "Yle-uutiset"},
        )
        # Give the consumer a few event loop ticks to drain.
        for _ in range(20):
            if vs.calls:
                break
            await asyncio.sleep(0.01)
    finally:
        await sink.stop()

    assert len(vs.calls) == 1
    call = vs.calls[0]
    assert call["collection"] == "waggle_memory"
    assert call["metadata"]["feed_id"] == "rss_yle_uutiset"
    assert call["metadata"]["agent_id"] == "rss_feed"
    assert call["id"].startswith("rss_feed_")


def test_feed_ingest_sink_queue_full_drops():
    sink = _make_sink(queue_maxsize=2)
    text = "A sufficiently long text body for ingestion."
    assert sink.learn(text + "1", agent_id="rss_feed", confidence=0.9,
                      metadata={"feed": "rss", "source_name": "A"})
    assert sink.learn(text + "2", agent_id="rss_feed", confidence=0.9,
                      metadata={"feed": "rss", "source_name": "A"})
    # Third should overflow.
    assert sink.learn(text + "3", agent_id="rss_feed", confidence=0.9,
                      metadata={"feed": "rss", "source_name": "A"}) is False
    assert sink._dropped == 1


# ---------------------------------------------------------------------------
# Container wiring
# ---------------------------------------------------------------------------


class _FakeSettings:
    def __init__(self, extras: dict):
        self._extras = extras

    def get(self, key: str, default: Any = None) -> Any:
        parts = key.split(".")
        node: Any = self._extras
        for part in parts:
            if isinstance(node, dict) and part in node:
                node = node[part]
            else:
                return default
        return node


class _StubContainer:
    """Minimal stand-in for ``Container`` exercising the new cached_properties."""

    def __init__(self, feeds_cfg: dict):
        self._settings = _FakeSettings({"feeds": feeds_cfg})
        self.vector_store = _FakeVectorStore()

    # Pull in the three new cached_property descriptors directly from Container
    # so we don't have to construct the full DI graph.
    from waggledance.bootstrap.container import Container as _RealContainer

    priority_lock = _RealContainer.priority_lock  # type: ignore[assignment]
    feed_ingest_sink = _RealContainer.feed_ingest_sink  # type: ignore[assignment]
    data_feed_scheduler = _RealContainer.data_feed_scheduler  # type: ignore[assignment]


def test_container_builds_data_feed_scheduler_when_enabled():
    feeds_cfg = {
        "enabled": True,
        "weather": {"enabled": False},
        "electricity": {"enabled": False},
        "rss": {"enabled": False},
    }
    c = _StubContainer(feeds_cfg)
    sched = c.data_feed_scheduler
    assert sched is not None
    # sanity: it was constructed with our sink + lock
    assert sched.consciousness is c.feed_ingest_sink
    assert sched.priority is c.priority_lock


def test_container_returns_none_when_feeds_disabled():
    c = _StubContainer({"enabled": False})
    assert c.data_feed_scheduler is None


# ---------------------------------------------------------------------------
# compat_dashboard._get_chroma_collection — plural dict shape
# ---------------------------------------------------------------------------


def test_get_chroma_collection_handles_collections_plural():
    from waggledance.adapters.http.routes.compat_dashboard import _get_chroma_collection

    marker = object()

    class _VS:
        _collections = {"waggle_memory": marker, "swarm_facts": object()}

    class _C:
        vector_store = _VS()

    assert _get_chroma_collection(_C()) is marker


def test_get_chroma_collection_falls_back_to_singular():
    from waggledance.adapters.http.routes.compat_dashboard import _get_chroma_collection

    marker = object()

    class _VS:
        _collection = marker

    class _C:
        vector_store = _VS()

    assert _get_chroma_collection(_C()) is marker

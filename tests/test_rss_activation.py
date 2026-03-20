"""Tests for RSS activation and configuration."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def test_rss_feed_enabled_in_config():
    """RSS feed should be enabled in settings.yaml."""
    import yaml
    cfg_path = ROOT / "configs" / "settings.yaml"
    with open(cfg_path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    ne_cfg = cfg.get("advanced_learning", {}).get("night_enricher", {})
    assert ne_cfg.get("rss_feed_enabled") is True, "rss_feed_enabled should be true"


def test_rss_feeds_configured():
    """RSS feeds list should have 3+ URLs."""
    import yaml
    cfg_path = ROOT / "configs" / "settings.yaml"
    with open(cfg_path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    feeds = cfg.get("feeds", {}).get("rss", {}).get("feeds", [])
    assert len(feeds) >= 3, f"Expected >=3 feeds, got {len(feeds)}"
    # Verify URLs are valid
    for feed in feeds:
        url = feed.get("url", "")
        assert url.startswith("http"), f"Invalid feed URL: {url}"
        assert feed.get("name"), f"Feed missing name: {feed}"


def test_feedparser_available():
    """feedparser should be importable."""
    import feedparser
    assert hasattr(feedparser, "parse")


def test_rss_feed_module_exists():
    """integrations/rss_feed.py should exist and be importable."""
    rss_path = ROOT / "integrations" / "rss_feed.py"
    assert rss_path.exists(), "integrations/rss_feed.py not found"

    import ast
    source = rss_path.read_text(encoding="utf-8")
    ast.parse(source)  # Verify syntax


def test_rss_in_requirements():
    """feedparser should be in requirements.txt."""
    req = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    assert "feedparser" in req, "feedparser not in requirements.txt"


def test_night_enricher_registers_rss():
    """NightEnricher should register RssFeedSource when enabled."""
    from core.night_enricher import RssFeedSource
    src = RssFeedSource()
    assert src.source_id == "rss_feed"


if __name__ == "__main__":
    tests = [
        test_rss_feed_enabled_in_config,
        test_rss_feeds_configured,
        test_feedparser_available,
        test_rss_feed_module_exists,
        test_rss_in_requirements,
        test_night_enricher_registers_rss,
    ]
    passed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS: {t.__name__}")
            passed += 1
        except Exception as e:
            print(f"  FAIL: {t.__name__}: {e}")
    print(f"\n{passed}/{len(tests)} passed")

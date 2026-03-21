"""Tests for night enricher source implementation_status and capability map (v1.16.0).

8 tests covering:
- SelfGenerateSource.implementation_status == "implemented"
- WebScrapeSource.implementation_status == "stub"
- ClaudeDistillSource.implementation_status == "stub"
- ChatHistorySource.implementation_status == "stub"
- RssFeedSource with feeds -> "partial"
- RssFeedSource without feeds -> "disabled"
- SourceManager.get_capability_map() returns all registered sources
- SourceManager.get_all_stats() includes implementation_status key
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from core.night_enricher import (
    SelfGenerateSource, WebScrapeSource, ClaudeDistillSource,
    ChatHistorySource, RssFeedSource, SourceManager,
)

PASS = []
FAIL = []


def OK(msg):
    PASS.append(msg)
    print(f"  OK  {msg}")


def FAIL_MSG(msg, detail=""):
    FAIL.append(msg)
    print(f"  FAIL {msg}" + (f" -- {detail}" if detail else ""))


def run():
    # 1. SelfGenerateSource.implementation_status == "implemented"
    sg = SelfGenerateSource(llm_fast=None, llm_validate=None)
    if sg.implementation_status == "implemented":
        OK("SelfGenerateSource.implementation_status == 'implemented'")
    else:
        FAIL_MSG("SelfGenerateSource status", sg.implementation_status)

    # 2. WebScrapeSource.implementation_status == "stub"
    ws = WebScrapeSource()
    if ws.implementation_status == "stub":
        OK("WebScrapeSource.implementation_status == 'stub'")
    else:
        FAIL_MSG("WebScrapeSource status", ws.implementation_status)

    # 3. ClaudeDistillSource.implementation_status == "stub"
    cd = ClaudeDistillSource()
    if cd.implementation_status == "stub":
        OK("ClaudeDistillSource.implementation_status == 'stub'")
    else:
        FAIL_MSG("ClaudeDistillSource status", cd.implementation_status)

    # 4. ChatHistorySource.implementation_status == "stub"
    ch = ChatHistorySource()
    if ch.implementation_status == "stub":
        OK("ChatHistorySource.implementation_status == 'stub'")
    else:
        FAIL_MSG("ChatHistorySource status", ch.implementation_status)

    # 5. RssFeedSource with feeds -> "partial"
    rss_with = RssFeedSource(feeds=[{"url": "https://example.com/feed", "name": "Test"}])
    status = rss_with.implementation_status
    # feedparser might not be installed, so accept "partial" or "unavailable"
    if status in ("partial", "unavailable"):
        OK(f"RssFeedSource with feeds -> '{status}'")
    else:
        FAIL_MSG("RssFeedSource with feeds status", status)

    # 6. RssFeedSource without feeds -> "disabled" or "unavailable"
    rss_empty = RssFeedSource(feeds=[])
    status2 = rss_empty.implementation_status
    if status2 in ("disabled", "unavailable"):
        OK(f"RssFeedSource without feeds -> '{status2}'")
    else:
        FAIL_MSG("RssFeedSource without feeds status", status2)

    # 7. SourceManager.get_capability_map() returns all registered sources
    sm = SourceManager()
    sm.register(SelfGenerateSource(llm_fast=None, llm_validate=None))
    sm.register(WebScrapeSource())
    sm.register(ChatHistorySource())
    sm.register(RssFeedSource(feeds=[]))
    cap_map = sm.get_capability_map()
    expected_ids = {"self_generate", "web_scrape", "chat_history", "rss_feed"}
    if expected_ids.issubset(set(cap_map.keys())):
        OK(f"get_capability_map() returns all {len(cap_map)} sources")
    else:
        FAIL_MSG("capability_map missing sources",
                 f"got {set(cap_map.keys())}, expected {expected_ids}")

    # 8. SourceManager.get_all_stats() includes implementation_status key
    stats = sm.get_all_stats()
    all_have_status = all(
        "implementation_status" in v for v in stats.values())
    if all_have_status:
        OK("get_all_stats() includes implementation_status for all sources")
    else:
        missing = [k for k, v in stats.items()
                   if "implementation_status" not in v]
        FAIL_MSG("get_all_stats() missing implementation_status", str(missing))


def main():
    print("\n=== test_night_enricher_capabilities ===")
    run()
    total = len(PASS) + len(FAIL)
    print(f"\nResult: {len(PASS)}/{total} passed, {len(FAIL)} failed")
    if FAIL:
        print("FAILURES:")
        for f in FAIL:
            print(f"  - {f}")
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(main())

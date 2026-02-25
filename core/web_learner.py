"""
WaggleDance — Phase 9, Layer 3: Web Learning Agent
====================================================
Searches web for knowledge gaps, extracts facts, validates via dual-model
consensus, stores in ChromaDB. Reuses tools/web_search.py WebSearchTool.

Tags all facts source_type='web_learning' (mass-deletable if quality drops).
Trusted domains get confidence=0.85, untrusted get 0.65.
Daily budget: 50 searches/day, resets at midnight.
"""

import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

log = logging.getLogger("web_learner")


class WebLearningAgent:
    """Autonomous web learning: gap → search → extract → validate → store."""

    TRUSTED_DOMAINS = [
        "mehilaishoitajat.fi",
        "ruokavirasto.fi",
        "scientificbeekeeping.com",
        "bee-health.extension.org",
        "ilmatieteenlaitos.fi",
    ]

    def __init__(self, consciousness, llm_heartbeat, llm_chat,
                 daily_budget: int = 50):
        self.consciousness = consciousness
        self.llm_fast = llm_heartbeat    # llama3.2:1b — extract
        self.llm_validate = llm_chat     # phi4-mini — validate
        self._daily_budget = daily_budget
        self._searches_today = 0
        self._today_str = datetime.now().strftime("%Y-%m-%d")
        self._facts_stored = 0
        self._facts_rejected = 0
        self._total_searches = 0
        self._web_search = None  # lazy init

    def _get_web_search(self):
        """Lazy-init WebSearchTool from tools/web_search.py."""
        if self._web_search is None:
            try:
                from tools.web_search import WebSearchTool
                self._web_search = WebSearchTool()
                log.info("🌐 WebSearchTool initialized for web learning")
            except Exception as e:
                log.warning(f"WebSearchTool init failed: {e}")
        return self._web_search

    def _check_daily_budget(self) -> bool:
        """Reset counter at midnight, check budget."""
        today = datetime.now().strftime("%Y-%m-%d")
        if today != self._today_str:
            self._today_str = today
            self._searches_today = 0
        return self._searches_today < self._daily_budget

    async def web_learning_cycle(self, throttle=None) -> int:
        """One cycle: gap → search → extract → validate → store.

        Returns number of facts stored (0-3).
        """
        if not self._check_daily_budget():
            log.debug("Web learning: daily budget exhausted")
            return 0

        ws = self._get_web_search()
        if ws is None:
            return 0

        gap = self._find_web_gap()
        if not gap:
            return 0

        # Search web (1 query per cycle)
        query = f"{gap['topic']} Finland beekeeping"
        try:
            results = await ws.search(query, max_results=3)
            self._searches_today += 1
            self._total_searches += 1
        except Exception as e:
            log.error(f"Web search error: {e}")
            self._log_web_learning(gap["topic"], query, 0, str(e))
            return 0

        if not results or (len(results) == 1
                           and results[0].get("title") in ("Virhe", "Hakuvirhe")):
            self._log_web_learning(gap["topic"], query, 0, "no_results")
            return 0

        # Extract facts with llama1b
        search_text = "\n".join(
            f"- {r.get('title', '')}: {r.get('body', '')}"
            for r in results if r.get("body")
        )[:1500]

        extract_prompt = (
            f"Extract 1-3 factual statements about '{gap['topic']}' from these "
            f"search results. Be specific and practical for Finnish beekeeping. "
            f"One statement per line. English only.\n\n{search_text}"
        )

        try:
            if throttle:
                async with throttle:
                    resp = await self.llm_fast.generate(
                        extract_prompt, max_tokens=200)
            else:
                resp = await self.llm_fast.generate(
                    extract_prompt, max_tokens=200)
        except Exception as e:
            log.error(f"Web learning extract error: {e}")
            return 0

        if not resp or (hasattr(resp, 'error') and resp.error):
            return 0

        content = resp.content if hasattr(resp, 'content') else str(resp)
        candidates = [line.strip() for line in content.strip().split("\n")
                      if len(line.strip()) > 20]
        if not candidates:
            return 0

        # Determine trust level from search result URLs
        trusted = any(
            self._is_trusted_domain(r.get("url", ""))
            for r in results if r.get("url")
        )
        confidence = 0.85 if trusted else 0.65

        # Validate each with phi4-mini
        stored = 0
        for fact in candidates[:3]:
            is_valid = await self._validate_fact(fact, throttle)
            if is_valid:
                source_urls = [r.get("url", "") for r in results
                               if r.get("url")][:3]
                self.consciousness.learn(
                    fact, agent_id="web_learner",
                    source_type="web_learning",
                    confidence=confidence,
                    validated=True, immediate=True,
                    metadata={
                        "gap_topic": gap["topic"],
                        "trusted": trusted,
                        "source_urls": ", ".join(source_urls)[:500],
                        "validation": "dual_model_consensus",
                    })
                stored += 1
                self._facts_stored += 1
                log.info(f"🌐 Web learning: stored '{fact[:60]}' "
                         f"(conf={confidence}, trusted={trusted})")
            else:
                self._facts_rejected += 1

        self._log_web_learning(gap["topic"], query, stored, "ok")
        return stored

    def _find_web_gap(self) -> Optional[dict]:
        """Find topic to search. Reuses enrichment gap strategies."""
        try:
            from core.fast_memory import FactEnrichmentEngine
            dummy = FactEnrichmentEngine.__new__(FactEnrichmentEngine)
            dummy.consciousness = self.consciousness

            strategies = [
                dummy._gap_from_failed_queries,
                dummy._gap_from_seasonal,
                dummy._gap_from_domain_categories,
            ]
            for strategy in strategies:
                try:
                    gap = strategy()
                    if gap:
                        return gap
                except Exception:
                    continue
        except Exception:
            pass

        # Fallback
        import random
        topics = [
            "varroa mite treatment", "queen rearing techniques",
            "honey extraction methods", "winter bee colony preparation",
            "spring hive inspection", "swarm prevention",
        ]
        return {"topic": random.choice(topics), "source": "web_fallback"}

    def _is_trusted_domain(self, url: str) -> bool:
        """Check if URL is from a trusted domain."""
        if not url:
            return False
        try:
            parsed = urlparse(url)
            host = parsed.hostname or ""
            return any(td in host for td in self.TRUSTED_DOMAINS)
        except Exception:
            return False

    async def _validate_fact(self, fact: str, throttle=None) -> bool:
        """Validate a fact with phi4-mini (same as enrichment engine)."""
        val_prompt = (
            f"Fact-check this beekeeping statement:\n\"{fact}\"\n\n"
            f"Reply ONLY 'VALID' or 'INVALID'. "
            f"VALID = factually correct for Finnish beekeeping."
        )
        try:
            if throttle:
                async with throttle:
                    resp = await self.llm_validate.generate(
                        val_prompt, max_tokens=20)
            else:
                resp = await self.llm_validate.generate(
                    val_prompt, max_tokens=20)
        except Exception as e:
            log.error(f"Web learning validate error: {e}")
            return False

        if not resp or (hasattr(resp, 'error') and resp.error):
            return False

        val_content = (resp.content if hasattr(resp, 'content')
                       else str(resp))
        val_upper = val_content.upper()
        return "VALID" in val_upper and "INVALID" not in val_upper

    def _log_web_learning(self, topic: str, query: str,
                          stored: int, status: str):
        """Log web learning attempt to data/web_learning.jsonl."""
        try:
            Path("data").mkdir(exist_ok=True)
            entry = {
                "timestamp": datetime.now().isoformat(),
                "topic": topic,
                "query": query,
                "facts_stored": stored,
                "status": status,
                "searches_today": self._searches_today,
            }
            with open("data/web_learning.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception:
            pass

    @property
    def stats(self) -> dict:
        return {
            "facts_stored": self._facts_stored,
            "facts_rejected": self._facts_rejected,
            "total_searches": self._total_searches,
            "searches_today": self._searches_today,
            "daily_budget": self._daily_budget,
            "budget_remaining": max(0, self._daily_budget - self._searches_today),
        }

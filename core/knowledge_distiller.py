"""
WaggleDance — Phase 9, Layer 4: Knowledge Distiller
=====================================================
Sends hard questions to Claude API, stores expert answers permanently.
Bridges tools/distill_from_opus.py (collect_failed_queries, FAILED_QUERIES_PATH).

Disabled by default (needs API key). Supports ANTHROPIC_API_KEY env var.
Weekly budget tracking, resets on Mondays.
Processed queries tracked in data/distill_processed.jsonl to avoid re-asking.
"""

import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

log = logging.getLogger("knowledge_distiller")


class KnowledgeDistiller:
    """Big model teaches small model. Once. Knowledge persists forever."""

    def __init__(self, consciousness, api_key: str = "",
                 model: str = "claude-haiku-4-5-20251001",
                 weekly_budget_eur: float = 5.0,
                 max_per_cycle: int = 5):
        self.consciousness = consciousness
        self._api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        self._model = model
        self._weekly_budget_eur = weekly_budget_eur
        self._max_per_cycle = max_per_cycle
        self._client = None  # lazy init
        self._anthropic_available = None  # None=unchecked

        # Stats
        self._facts_stored = 0
        self._corrections_stored = 0
        self._total_api_calls = 0
        self._estimated_cost_eur = 0.0
        self._week_cost_eur = 0.0
        self._week_number = datetime.now().isocalendar()[1]

        # Processed queries tracking
        self._processed_path = Path("data/distill_processed.jsonl")
        self._processed_queries = self._load_processed()

    def _check_anthropic(self) -> bool:
        """Check if anthropic library is available."""
        if self._anthropic_available is None:
            try:
                import anthropic  # noqa: F401
                self._anthropic_available = True
            except ImportError:
                self._anthropic_available = False
                log.warning("anthropic library not installed — "
                            "distillation disabled. "
                            "pip install anthropic")
        return self._anthropic_available

    def _get_client(self):
        """Lazy-init Anthropic async client."""
        if self._client is None:
            if not self._api_key:
                log.info("No API key — distillation disabled")
                return None
            if not self._check_anthropic():
                return None
            try:
                import anthropic
                self._client = anthropic.AsyncAnthropic(api_key=self._api_key)
                log.info(f"🧠 Anthropic client initialized (model={self._model})")
            except Exception as e:
                log.warning(f"Anthropic client init failed: {e}")
                return None
        return self._client

    def _check_weekly_budget(self) -> bool:
        """Reset weekly cost on Mondays, check budget."""
        current_week = datetime.now().isocalendar()[1]
        if current_week != self._week_number:
            self._week_number = current_week
            self._week_cost_eur = 0.0
        return self._week_cost_eur < self._weekly_budget_eur

    async def distillation_cycle(self, throttle=None) -> int:
        """One cycle: load failed queries → send to Claude → store answers.

        Returns number of facts stored.
        """
        if not self._api_key:
            return 0

        if not self._check_anthropic():
            return 0

        if not self._check_weekly_budget():
            log.debug("Distillation: weekly budget exhausted")
            return 0

        # Collect fresh failed queries
        try:
            from tools.distill_from_opus import collect_failed_queries
            collect_failed_queries()
        except Exception as e:
            log.debug(f"collect_failed_queries: {e}")

        # Load pending questions
        questions = self._load_pending_questions()
        if not questions:
            return 0

        client = self._get_client()
        if client is None:
            return 0

        stored = 0
        for q in questions[:self._max_per_cycle]:
            query = q.get("query", "")
            if not query:
                continue

            answer = await self._ask_claude(client, query)
            if not answer:
                continue

            facts = self._parse_expert_answer(answer)
            for fact in facts:
                is_correction = fact.startswith("[CORRECTION] ")
                clean_fact = (fact[len("[CORRECTION] "):]
                              if is_correction else fact)

                self.consciousness.learn(
                    clean_fact, agent_id="expert_distill",
                    source_type="expert_distillation",
                    confidence=0.95, validated=True, immediate=True,
                    metadata={
                        "original_query": query[:200],
                        "is_correction": is_correction,
                    })
                stored += 1
                if is_correction:
                    self._corrections_stored += 1
                else:
                    self._facts_stored += 1

            self._mark_processed(query)

        return stored

    async def _ask_claude(self, client, question: str) -> Optional[str]:
        """Send question to Claude API. Returns response text or None."""
        system_prompt = (
            "You are a Finnish beekeeping expert with 30+ years experience. "
            "Answer factually and concisely in English. "
            "Focus on practical Finnish beekeeping conditions.\n\n"
            "Format your answer as:\n"
            "FACT: <factual statement>\n"
            "FACT: <another fact>\n"
            "If correcting a misconception, use:\n"
            "CORRECTION: <corrected statement>"
        )

        try:
            response = await client.messages.create(
                model=self._model,
                max_tokens=300,
                system=system_prompt,
                messages=[{"role": "user",
                           "content": f"Question from a beekeeper: {question}"}],
            )
            self._total_api_calls += 1

            # Estimate cost (Haiku: $0.25/1M input, $1.25/1M output)
            in_tokens = response.usage.input_tokens if response.usage else 100
            out_tokens = response.usage.output_tokens if response.usage else 100
            cost = (in_tokens * 0.25 + out_tokens * 1.25) / 1_000_000
            self._estimated_cost_eur += cost
            self._week_cost_eur += cost

            text = ""
            for block in response.content:
                if hasattr(block, "text"):
                    text += block.text
            return text if text else None

        except Exception as e:
            log.error(f"Claude API error: {e}")
            return None

    def _parse_expert_answer(self, text: str) -> list:
        """Parse FACT: and CORRECTION: lines from response."""
        facts = []
        for line in text.strip().split("\n"):
            line = line.strip()
            if line.upper().startswith("FACT:"):
                fact = line[5:].strip()
                if len(fact) > 10:
                    facts.append(fact)
            elif line.upper().startswith("CORRECTION:"):
                correction = line[11:].strip()
                if len(correction) > 10:
                    facts.append(f"[CORRECTION] {correction}")
        return facts

    def _load_pending_questions(self) -> list:
        """Load failed queries not yet processed."""
        try:
            from tools.distill_from_opus import FAILED_QUERIES_PATH
            path = FAILED_QUERIES_PATH
        except ImportError:
            path = Path("data/failed_queries.jsonl")

        if not path.exists():
            return []

        questions = []
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                        query = entry.get("query", "")
                        if query and query not in self._processed_queries:
                            questions.append(entry)
                    except json.JSONDecodeError:
                        continue
        except Exception:
            return []

        return questions

    def _load_processed(self) -> set:
        """Load set of already-processed query strings."""
        processed = set()
        if self._processed_path.exists():
            try:
                with open(self._processed_path, encoding="utf-8") as f:
                    for line in f:
                        try:
                            entry = json.loads(line.strip())
                            processed.add(entry.get("query", ""))
                        except json.JSONDecodeError:
                            continue
            except Exception:
                pass
        return processed

    def _mark_processed(self, query: str):
        """Mark query as processed so we don't re-ask."""
        self._processed_queries.add(query)
        try:
            self._processed_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._processed_path, "a", encoding="utf-8") as f:
                f.write(json.dumps({
                    "query": query,
                    "timestamp": datetime.now().isoformat(),
                }, ensure_ascii=False) + "\n")
        except Exception:
            pass

    @property
    def stats(self) -> dict:
        return {
            "facts_stored": self._facts_stored,
            "corrections_stored": self._corrections_stored,
            "total_api_calls": self._total_api_calls,
            "estimated_cost_eur": round(self._estimated_cost_eur, 4),
            "week_cost_eur": round(self._week_cost_eur, 4),
            "weekly_budget_eur": self._weekly_budget_eur,
            "api_key_set": bool(self._api_key),
            "anthropic_available": self._anthropic_available or False,
            "model": self._model,
            "processed_count": len(self._processed_queries),
        }

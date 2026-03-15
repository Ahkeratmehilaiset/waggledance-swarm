"""English source learner — ingest from trusted English sources during night learning."""

import logging
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

log = logging.getLogger(__name__)

@dataclass
class SourceConfig:
    url: str
    domain: str  # "bee_science", "weather", "agriculture"
    trust_level: float = 0.7  # 0.0-1.0
    max_facts_per_cycle: int = 10

@dataclass
class LearnedFact:
    text_en: str
    text_fi: str = ""
    source_url: str = ""
    domain: str = ""
    confidence: float = 0.0
    timestamp: float = field(default_factory=time.time)

class EnglishSourceLearner:
    """Ingests facts from English trusted sources and translates to Finnish.

    Strict allowlist: only configured sources are used.
    Budget: max N facts per cycle per source.
    Provenance: every fact records its source URL.
    """

    DEFAULT_ALLOWLIST = [
        SourceConfig("https://extension.org/beekeeping", "bee_science"),
        SourceConfig("https://beeaware.org.au/facts", "bee_science"),
    ]

    def __init__(self, sources: list[SourceConfig] | None = None,
                 translator: Callable[[str], str] | None = None,
                 budget_per_cycle: int = 50):
        self.sources = sources if sources is not None else self.DEFAULT_ALLOWLIST
        self._translator = translator
        self.budget_per_cycle = budget_per_cycle
        self._facts_this_cycle: list[LearnedFact] = []
        self._total_ingested = 0

    def is_allowed(self, url: str) -> bool:
        """Check if URL is in the allowlist."""
        return any(s.url == url or url.startswith(s.url) for s in self.sources)

    def translate(self, text_en: str) -> str:
        """Translate English text to Finnish using configured translator."""
        if self._translator:
            try:
                return self._translator(text_en)
            except Exception as e:
                log.warning(f"Translation failed: {e}")
        return text_en  # fallback: keep English

    def ingest_fact(self, text_en: str, source_url: str, domain: str = "",
                    confidence: float = 0.7) -> LearnedFact | None:
        """Ingest a single fact. Returns LearnedFact or None if rejected."""
        if not self.is_allowed(source_url):
            log.warning(f"Source not in allowlist: {source_url}")
            return None

        if len(self._facts_this_cycle) >= self.budget_per_cycle:
            log.info("Budget exhausted for this cycle")
            return None

        if not text_en or len(text_en.strip()) < 10:
            return None

        text_fi = self.translate(text_en)
        fact = LearnedFact(
            text_en=text_en.strip(),
            text_fi=text_fi,
            source_url=source_url,
            domain=domain,
            confidence=confidence,
        )
        self._facts_this_cycle.append(fact)
        self._total_ingested += 1
        return fact

    def reset_cycle(self) -> int:
        """Reset cycle counter. Returns facts ingested this cycle."""
        count = len(self._facts_this_cycle)
        self._facts_this_cycle = []
        return count

    @property
    def facts_this_cycle(self) -> list[LearnedFact]:
        return list(self._facts_this_cycle)

    @property
    def total_ingested(self) -> int:
        return self._total_ingested

    @property
    def budget_remaining(self) -> int:
        return max(0, self.budget_per_cycle - len(self._facts_this_cycle))

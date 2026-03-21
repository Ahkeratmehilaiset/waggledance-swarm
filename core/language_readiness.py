"""Language readiness — per-language capability reporting."""

import logging
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

@dataclass
class LanguageCapability:
    language: str  # "fi", "en"
    embedding_available: bool = False
    translation_available: bool = False
    tts_available: bool = False
    stt_available: bool = False
    knowledge_coverage: float = 0.0  # 0.0-1.0 estimated coverage
    notes: list[str] = field(default_factory=list)

    @property
    def readiness_score(self) -> float:
        """Composite readiness 0.0-1.0."""
        score = 0.0
        if self.embedding_available:
            score += 0.3
        if self.translation_available:
            score += 0.2
        if self.tts_available:
            score += 0.15
        if self.stt_available:
            score += 0.15
        score += self.knowledge_coverage * 0.2
        return min(1.0, score)

class LanguageReadiness:
    """Checks and reports per-language capabilities."""

    def __init__(self):
        self._capabilities: dict[str, LanguageCapability] = {}

    def register(self, language: str, **kwargs) -> LanguageCapability:
        """Register or update a language's capabilities."""
        if language in self._capabilities:
            cap = self._capabilities[language]
            for k, v in kwargs.items():
                if hasattr(cap, k):
                    setattr(cap, k, v)
        else:
            cap = LanguageCapability(language=language, **kwargs)
            self._capabilities[language] = cap
        return cap

    def get(self, language: str) -> LanguageCapability | None:
        return self._capabilities.get(language)

    def all_languages(self) -> list[LanguageCapability]:
        return sorted(self._capabilities.values(),
                      key=lambda c: c.readiness_score, reverse=True)

    def is_ready(self, language: str, min_score: float = 0.5) -> bool:
        cap = self._capabilities.get(language)
        if cap is None:
            return False
        return cap.readiness_score >= min_score

    def summary(self) -> dict[str, dict]:
        return {
            lang: {
                "readiness_score": round(cap.readiness_score, 2),
                "embedding": cap.embedding_available,
                "translation": cap.translation_available,
                "tts": cap.tts_available,
                "stt": cap.stt_available,
                "knowledge_coverage": cap.knowledge_coverage,
            }
            for lang, cap in self._capabilities.items()
        }

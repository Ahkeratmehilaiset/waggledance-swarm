"""
WaggleDance — Phase 9, Layer 6: Code Self-Review
==================================================
Identifies bottlenecks via meta-learning data, suggests improvements.
NEVER changes code — suggestions go to dashboard for Accept/Reject.

Uses phi4-mini (chat model) — only runs when chat NOT active (night mode).
Monthly interval (720h), or triggered manually from dashboard.
Suggestions persist to data/code_suggestions.jsonl.
"""

import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

log = logging.getLogger("code_reviewer")


class CodeSelfReview:
    """Identify bottlenecks and error patterns, suggest fixes."""

    MONTHLY_INTERVAL_H = 720  # ~30 days

    def __init__(self, consciousness, llm_chat,
                 meta_learning=None):
        self.consciousness = consciousness
        self.llm_chat = llm_chat       # phi4-mini
        self.meta_learning = meta_learning
        self._last_run = 0.0
        self._total_reviews = 0
        self._suggestions_path = Path("data/code_suggestions.jsonl")
        self._suggestions = self._load_suggestions()

    async def monthly_code_review(self, throttle=None) -> list:
        """Feed meta_learning report to phi4-mini, parse suggestions.

        Returns list of suggestion dicts.
        """
        report_text = self._build_report_text()
        if not report_text:
            return []

        prompt = (
            "You are a code performance analyzer for WaggleDance beekeeping AI. "
            "Based on the following system performance report, suggest improvements.\n\n"
            f"REPORT:\n{report_text}\n\n"
            "Format each suggestion as:\n"
            "SUGGESTION: <what to change>\n"
            "IMPACT: <expected improvement>\n"
            "RISK: low/medium/high\n\n"
            "Give 1-3 suggestions. Focus on performance bottlenecks and quality issues."
        )

        try:
            if throttle:
                async with throttle:
                    resp = await self.llm_chat.generate(
                        prompt, max_tokens=500)
            else:
                resp = await self.llm_chat.generate(
                    prompt, max_tokens=500)
        except Exception as e:
            log.error(f"Code review LLM error: {e}")
            return []

        if not resp or (hasattr(resp, 'error') and resp.error):
            return []

        content = resp.content if hasattr(resp, 'content') else str(resp)
        new_suggestions = self._parse_suggestions(content)

        for s in new_suggestions:
            s["timestamp"] = datetime.now().isoformat()
            s["status"] = "pending"
            s["review_number"] = self._total_reviews + 1
            self._suggestions.append(s)

        self._last_run = time.monotonic()
        self._total_reviews += 1
        self._save_suggestions()

        log.info(f"🔍 Code review #{self._total_reviews}: "
                 f"{len(new_suggestions)} suggestions")
        return new_suggestions

    def accept_suggestion(self, index: int):
        """Mark suggestion as accepted."""
        if 0 <= index < len(self._suggestions):
            self._suggestions[index]["status"] = "accepted"
            self._suggestions[index]["resolved_at"] = datetime.now().isoformat()
            self._save_suggestions()

    def reject_suggestion(self, index: int):
        """Mark suggestion as rejected."""
        if 0 <= index < len(self._suggestions):
            self._suggestions[index]["status"] = "rejected"
            self._suggestions[index]["resolved_at"] = datetime.now().isoformat()
            self._save_suggestions()

    def get_pending_suggestions(self) -> list:
        """Get all pending (unresolved) suggestions."""
        return [
            {"index": i, **s}
            for i, s in enumerate(self._suggestions)
            if s.get("status") == "pending"
        ]

    def _build_report_text(self) -> str:
        """Build report text from meta-learning data."""
        parts = []

        if self.meta_learning and self.meta_learning._last_report:
            r = self.meta_learning._last_report
            mem = r.get("memory_stats", {})
            parts.append(f"Memory: {mem.get('total_facts', '?')} facts, "
                         f"cache: {mem.get('hot_cache', {})}")

            hall = r.get("hallucination_stats", {})
            parts.append(f"Hallucination rate: {hall.get('overall_rate', '?')}")

            eff = r.get("learning_efficiency", {})
            parts.append(f"Learning: {json.dumps(eff, default=str)[:500]}")

            weak = r.get("weakest_areas", [])
            if weak:
                parts.append(f"Weak areas: {json.dumps(weak, default=str)[:300]}")
        else:
            # Fallback: build from consciousness stats
            try:
                c = self.consciousness
                parts.append(f"Memory: {c.memory.count} facts")
                parts.append(f"Queries: {c._total_queries}, "
                             f"Hallucinations: {c._hallucination_count}")
                if hasattr(c, 'hot_cache') and c.hot_cache:
                    parts.append(f"Cache: {c.hot_cache.stats}")
            except Exception:
                pass

        return "\n".join(parts) if parts else ""

    def _parse_suggestions(self, text: str) -> list:
        """Parse SUGGESTION/IMPACT/RISK format from LLM response."""
        suggestions = []
        current = {}

        for line in text.strip().split("\n"):
            line = line.strip()
            if line.upper().startswith("SUGGESTION:"):
                if current.get("suggestion"):
                    suggestions.append(current)
                current = {"suggestion": line[11:].strip()}
            elif line.upper().startswith("IMPACT:"):
                current["impact"] = line[7:].strip()
            elif line.upper().startswith("RISK:"):
                risk = line[5:].strip().lower()
                # Normalize risk
                if "high" in risk:
                    current["risk"] = "high"
                elif "medium" in risk or "med" in risk:
                    current["risk"] = "medium"
                else:
                    current["risk"] = "low"

        # Don't forget the last one
        if current.get("suggestion"):
            suggestions.append(current)

        return suggestions

    def _save_suggestions(self):
        """Persist suggestions to data/code_suggestions.jsonl."""
        try:
            self._suggestions_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._suggestions_path, "w", encoding="utf-8") as f:
                for s in self._suggestions:
                    f.write(json.dumps(s, ensure_ascii=False) + "\n")
        except Exception as e:
            log.warning(f"Failed to save suggestions: {e}")

    def _load_suggestions(self) -> list:
        """Load suggestions from data/code_suggestions.jsonl."""
        suggestions = []
        if self._suggestions_path.exists():
            try:
                with open(self._suggestions_path, encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            try:
                                suggestions.append(json.loads(line))
                            except json.JSONDecodeError:
                                continue
            except Exception:
                pass
        return suggestions

    def is_due(self) -> bool:
        """Check if monthly review is due."""
        if self._last_run == 0.0:
            return True
        elapsed_h = (time.monotonic() - self._last_run) / 3600
        return elapsed_h >= self.MONTHLY_INTERVAL_H

    @property
    def stats(self) -> dict:
        pending = len([s for s in self._suggestions
                       if s.get("status") == "pending"])
        accepted = len([s for s in self._suggestions
                        if s.get("status") == "accepted"])
        rejected = len([s for s in self._suggestions
                        if s.get("status") == "rejected"])
        return {
            "total_reviews": self._total_reviews,
            "total_suggestions": len(self._suggestions),
            "pending": pending,
            "accepted": accepted,
            "rejected": rejected,
            "last_run": (datetime.fromtimestamp(
                time.time() - (time.monotonic() - self._last_run)
            ).isoformat() if self._last_run > 0 else None),
        }

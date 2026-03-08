"""
NightEnricher — unified orchestrator for autonomous night-mode enrichment.

Manages multiple knowledge sources through one QualityGate with adaptive
capacity allocation, burst mode, gap-weighted agent distribution, and
comprehensive morning reporting.

Sources:
  - self_generate: llama1b generation + phi4-mini validation (fully implemented)
  - web_scrape: web search for knowledge gaps (stub)
  - claude_distill: API distillation from Claude (stub)
  - chat_history: mine past conversations (stub)
  - rss_feed: extract facts from RSS entries (stub)
"""

import json
import logging
import random
import time
from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, date
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger("night_enricher")


# ═══════════════════════════════════════════════════════════════
# DATA CLASSES
# ═══════════════════════════════════════════════════════════════

@dataclass
class EnrichmentCandidate:
    """A single candidate fact produced by a source."""
    text: str
    source_id: str
    agent_id: str = ""
    gap_topic: str = ""
    metadata: dict = field(default_factory=dict)


@dataclass
class QualityVerdict:
    """Result of QualityGate evaluation."""
    passed: bool
    novel: bool
    reason: str = ""
    step_failed: str = ""


class SourceMetrics:
    """Rolling metrics for a single enrichment source."""

    def __init__(self):
        self._pass_results: deque = deque(maxlen=100)
        self._novelty_results: deque = deque(maxlen=100)
        self._cycle_times: deque = deque(maxlen=50)
        self._pause_until: float = 0.0
        self._consecutive_failures: int = 0
        self._total_checked: int = 0
        self._total_passed: int = 0
        self._total_novel: int = 0

    def record_outcome(self, passed: bool, novel: bool,
                       cycle_time_s: float = 0.0):
        self._pass_results.append(passed)
        self._novelty_results.append(novel)
        if cycle_time_s > 0:
            self._cycle_times.append(cycle_time_s)
        self._total_checked += 1
        if passed:
            self._total_passed += 1
            self._consecutive_failures = 0
        else:
            self._consecutive_failures += 1
        if novel:
            self._total_novel += 1

    @property
    def pass_rate(self) -> float:
        if not self._pass_results:
            return 0.0
        return sum(self._pass_results) / len(self._pass_results)

    @property
    def novelty_score(self) -> float:
        if not self._novelty_results:
            return 0.0
        return sum(self._novelty_results) / len(self._novelty_results)

    @property
    def throughput(self) -> float:
        """Candidates per minute based on recent cycle times."""
        if not self._cycle_times:
            return 0.0
        avg_time = sum(self._cycle_times) / len(self._cycle_times)
        if avg_time <= 0:
            return 0.0
        return 60.0 / avg_time

    @property
    def effective_yield(self) -> float:
        """Combined quality score: pass_rate * novelty * throughput."""
        return self.pass_rate * self.novelty_score * max(self.throughput, 0.01)

    @property
    def is_paused(self) -> bool:
        return time.monotonic() < self._pause_until

    def pause(self, seconds: float):
        self._pause_until = time.monotonic() + seconds

    @property
    def consecutive_failures(self) -> int:
        return self._consecutive_failures

    def to_dict(self) -> dict:
        return {
            "pass_rate": round(self.pass_rate, 3),
            "novelty_score": round(self.novelty_score, 3),
            "throughput": round(self.throughput, 2),
            "effective_yield": round(self.effective_yield, 3),
            "is_paused": self.is_paused,
            "consecutive_failures": self._consecutive_failures,
            "total_checked": self._total_checked,
            "total_passed": self._total_passed,
            "total_novel": self._total_novel,
            "window_size": len(self._pass_results),
        }


# ═══════════════════════════════════════════════════════════════
# ENRICHMENT SOURCE — abstract base + implementations
# ═══════════════════════════════════════════════════════════════

class EnrichmentSource(ABC):
    """Abstract base for all enrichment sources."""

    def __init__(self):
        self.metrics = SourceMetrics()

    @property
    @abstractmethod
    def source_id(self) -> str:
        ...

    @abstractmethod
    async def generate_candidates(self, topic: str, agent_id: str,
                                  count: int = 3,
                                  throttle=None) -> List[EnrichmentCandidate]:
        ...

    @abstractmethod
    def is_available(self) -> bool:
        ...

    def budget_check(self) -> bool:
        return True

    def budget_info(self) -> dict:
        return {}


class SelfGenerateSource(EnrichmentSource):
    """Generate facts via llama1b. Reuses pattern from FactEnrichmentEngine."""

    def __init__(self, llm_fast, llm_validate):
        super().__init__()
        self.llm_fast = llm_fast
        self.llm_validate = llm_validate

    @property
    def source_id(self) -> str:
        return "self_generate"

    def is_available(self) -> bool:
        return self.llm_fast is not None and self.llm_validate is not None

    async def generate_candidates(self, topic: str, agent_id: str,
                                  count: int = 3,
                                  throttle=None) -> List[EnrichmentCandidate]:
        gen_prompt = (
            f"Generate {count} factual statements about '{topic}' "
            f"for Finnish beekeeping. Be specific and practical. "
            f"One statement per line. English only.")
        try:
            if throttle:
                async with throttle:
                    resp = await self.llm_fast.generate(
                        gen_prompt, max_tokens=200)
            else:
                resp = await self.llm_fast.generate(
                    gen_prompt, max_tokens=200)
        except Exception as e:
            log.error(f"SelfGenerate LLM error: {e}")
            return []

        if not resp or (hasattr(resp, 'error') and resp.error):
            log.warning(f"SelfGenerate: empty/error response "
                        f"(resp={resp!r:.100})")
            return []

        content = resp.content if hasattr(resp, 'content') else str(resp)
        lines = [ln.strip() for ln in content.strip().split("\n")
                 if len(ln.strip()) > 20]

        candidates = []
        for line in lines[:count]:
            # Strip leading numbering like "1. " or "- "
            clean = line.lstrip("0123456789.-) ").strip()
            if len(clean) > 20:
                candidates.append(EnrichmentCandidate(
                    text=clean,
                    source_id=self.source_id,
                    agent_id=agent_id,
                    gap_topic=topic,
                ))
        log.info(f"SelfGenerate: {len(candidates)} candidates from "
                 f"{len(lines)} lines (topic={topic[:40]})")
        return candidates


class WebScrapeSource(EnrichmentSource):
    """Stub: web search for knowledge gaps. Not yet implemented."""

    def __init__(self, daily_budget: int = 50):
        super().__init__()
        self.daily_budget = daily_budget
        self._searches_today = 0
        self._last_reset: date = date.today()

    @property
    def source_id(self) -> str:
        return "web_scrape"

    def is_available(self) -> bool:
        return False  # Stub

    def budget_check(self) -> bool:
        self._maybe_reset()
        return self._searches_today < self.daily_budget

    def budget_info(self) -> dict:
        self._maybe_reset()
        return {
            "daily_budget": self.daily_budget,
            "searches_today": self._searches_today,
            "remaining": self.daily_budget - self._searches_today,
        }

    def _maybe_reset(self):
        today = date.today()
        if today != self._last_reset:
            self._searches_today = 0
            self._last_reset = today

    async def generate_candidates(self, topic: str, agent_id: str,
                                  count: int = 3,
                                  throttle=None) -> List[EnrichmentCandidate]:
        return []  # Stub


class ClaudeDistillSource(EnrichmentSource):
    """Stub: distillation from Claude API. Not yet implemented."""

    def __init__(self, weekly_budget_eur: float = 5.0):
        super().__init__()
        self.weekly_budget_eur = weekly_budget_eur
        self._week_cost_eur = 0.0
        self._last_reset_week: int = datetime.now().isocalendar()[1]

    @property
    def source_id(self) -> str:
        return "claude_distill"

    def is_available(self) -> bool:
        return False  # Stub

    def budget_check(self) -> bool:
        self._maybe_reset()
        return self._week_cost_eur < self.weekly_budget_eur

    def budget_info(self) -> dict:
        self._maybe_reset()
        return {
            "weekly_budget_eur": self.weekly_budget_eur,
            "week_cost_eur": round(self._week_cost_eur, 4),
            "remaining_eur": round(
                self.weekly_budget_eur - self._week_cost_eur, 4),
        }

    def _maybe_reset(self):
        current_week = datetime.now().isocalendar()[1]
        if current_week != self._last_reset_week:
            self._week_cost_eur = 0.0
            self._last_reset_week = current_week

    async def generate_candidates(self, topic: str, agent_id: str,
                                  count: int = 3,
                                  throttle=None) -> List[EnrichmentCandidate]:
        return []  # Stub


class ChatHistorySource(EnrichmentSource):
    """Stub: mine past chat conversations for facts. Not yet implemented."""

    @property
    def source_id(self) -> str:
        return "chat_history"

    def is_available(self) -> bool:
        return False  # Stub

    async def generate_candidates(self, topic: str, agent_id: str,
                                  count: int = 3,
                                  throttle=None) -> List[EnrichmentCandidate]:
        return []  # Stub


class RssFeedSource(EnrichmentSource):
    """Stub: extract facts from RSS feed entries. Not yet implemented."""

    @property
    def source_id(self) -> str:
        return "rss_feed"

    def is_available(self) -> bool:
        return False  # Stub

    async def generate_candidates(self, topic: str, agent_id: str,
                                  count: int = 3,
                                  throttle=None) -> List[EnrichmentCandidate]:
        return []  # Stub


# ═══════════════════════════════════════════════════════════════
# SOURCE MANAGER — registry for all sources
# ═══════════════════════════════════════════════════════════════

class SourceManager:
    """Registry and access layer for enrichment sources."""

    def __init__(self):
        self._sources: Dict[str, EnrichmentSource] = {}

    def register(self, source: EnrichmentSource):
        self._sources[source.source_id] = source

    def get_source(self, source_id: str) -> Optional[EnrichmentSource]:
        return self._sources.get(source_id)

    def get_metrics(self, source_id: str) -> Optional[SourceMetrics]:
        src = self._sources.get(source_id)
        return src.metrics if src else None

    def is_source_available(self, source_id: str) -> bool:
        src = self._sources.get(source_id)
        if not src:
            return False
        return src.is_available() and not src.metrics.is_paused

    def get_all_stats(self) -> Dict[str, dict]:
        result = {}
        for sid, src in self._sources.items():
            result[sid] = {
                "available": src.is_available(),
                "budget_ok": src.budget_check(),
                **src.metrics.to_dict(),
                **src.budget_info(),
            }
        return result

    @property
    def source_ids(self) -> List[str]:
        return list(self._sources.keys())

    @property
    def available_ids(self) -> List[str]:
        return [sid for sid in self._sources
                if self.is_source_available(sid)
                and self._sources[sid].budget_check()]


# ═══════════════════════════════════════════════════════════════
# QUALITY GATE — 4-step sequential pipeline
# ═══════════════════════════════════════════════════════════════

class QualityGate:
    """4-step validation: LLM validate → seasonal guard → contradiction → novelty.

    Short-circuits on first failure.
    """

    def __init__(self, consciousness, llm_validate,
                 novelty_threshold: float = 0.85,
                 dedup_threshold: float = 0.93):
        self.consciousness = consciousness
        self.llm_validate = llm_validate
        self.novelty_threshold = novelty_threshold
        self.dedup_threshold = dedup_threshold
        self._step_stats: Dict[str, Dict[str, int]] = {
            "llm_validate": {"checked": 0, "rejected": 0},
            "seasonal_guard": {"checked": 0, "rejected": 0},
            "contradiction": {"checked": 0, "rejected": 0},
            "novelty": {"checked": 0, "rejected": 0},
        }

    async def check(self, candidate: EnrichmentCandidate,
                    throttle=None) -> QualityVerdict:
        """Run all 4 steps. Short-circuits on failure."""
        text = candidate.text

        # Step 1: Dual LLM validate (phi4-mini VALID/INVALID)
        self._step_stats["llm_validate"]["checked"] += 1
        try:
            val_prompt = (
                f"Fact-check this beekeeping statement:\n\"{text}\"\n\n"
                f"Reply ONLY 'VALID' or 'INVALID'. "
                f"VALID = factually correct for Finnish beekeeping.")
            if throttle:
                async with throttle:
                    val_resp = await self.llm_validate.generate(
                        val_prompt, max_tokens=20)
            else:
                val_resp = await self.llm_validate.generate(
                    val_prompt, max_tokens=20)

            if not val_resp or (hasattr(val_resp, 'error') and val_resp.error):
                self._step_stats["llm_validate"]["rejected"] += 1
                return QualityVerdict(
                    passed=False, novel=False,
                    reason="LLM validation error",
                    step_failed="llm_validate")

            val_content = (val_resp.content if hasattr(val_resp, 'content')
                           else str(val_resp))
            val_upper = val_content.upper()
            if "INVALID" in val_upper or "VALID" not in val_upper:
                self._step_stats["llm_validate"]["rejected"] += 1
                return QualityVerdict(
                    passed=False, novel=False,
                    reason=f"LLM rejected: {val_content[:60]}",
                    step_failed="llm_validate")
        except Exception as e:
            self._step_stats["llm_validate"]["rejected"] += 1
            return QualityVerdict(
                passed=False, novel=False,
                reason=f"LLM error: {e}",
                step_failed="llm_validate")

        # Step 2: Seasonal guard
        self._step_stats["seasonal_guard"]["checked"] += 1
        try:
            from core.seasonal_guard import get_seasonal_guard
            sg = get_seasonal_guard()
            ok, reason = sg.filter_enrichment(text)
            if not ok:
                self._step_stats["seasonal_guard"]["rejected"] += 1
                return QualityVerdict(
                    passed=False, novel=False,
                    reason=reason, step_failed="seasonal_guard")
        except ImportError:
            pass  # No seasonal guard available — skip step
        except Exception as e:
            log.debug(f"QualityGate seasonal guard error: {e}")

        # Temporal sanity check (from FactEnrichmentEngine pattern)
        try:
            from core.fast_memory import FactEnrichmentEngine
            _temp_engine = FactEnrichmentEngine.__new__(FactEnrichmentEngine)
            ok, reason = _temp_engine._check_temporal_sanity(text)
            if not ok:
                self._step_stats["seasonal_guard"]["rejected"] += 1
                return QualityVerdict(
                    passed=False, novel=False,
                    reason=reason, step_failed="seasonal_guard")
        except Exception:
            pass

        # Step 3: Contradiction check
        self._step_stats["contradiction"]["checked"] += 1
        try:
            contradiction = await self._check_contradiction(text)
            if contradiction:
                self._step_stats["contradiction"]["rejected"] += 1
                return QualityVerdict(
                    passed=False, novel=False,
                    reason=contradiction,
                    step_failed="contradiction")
        except Exception as e:
            log.debug(f"QualityGate contradiction error: {e}")

        # Step 4: Novelty check
        self._step_stats["novelty"]["checked"] += 1
        novel = True
        try:
            closest_score = self._check_novelty(text)
            if closest_score >= self.dedup_threshold:
                self._step_stats["novelty"]["rejected"] += 1
                return QualityVerdict(
                    passed=False, novel=False,
                    reason=f"Duplicate (score={closest_score:.3f})",
                    step_failed="novelty")
            novel = closest_score < self.novelty_threshold
        except Exception as e:
            log.debug(f"QualityGate novelty error: {e}")

        return QualityVerdict(passed=True, novel=novel, reason="passed")

    async def _check_contradiction(self, text: str) -> Optional[str]:
        """Check if text contradicts existing knowledge.

        Search ChromaDB top-3 matches. If score > 0.7 and negation
        pattern differs, flag as contradiction.
        """
        if not (self.consciousness and hasattr(self.consciousness, 'embed')
                and hasattr(self.consciousness, 'memory')):
            return None

        try:
            vec = self.consciousness.embed.embed_query(text)
            if not vec:
                return None
            matches = self.consciousness.memory.search(
                vec, top_k=3, min_score=0.7)
        except Exception:
            return None

        negation_markers = [
            "not ", "never ", "don't ", "doesn't ", "cannot ", "isn't ",
            "aren't ", "won't ", "shouldn't ", "no ", "none ", "neither ",
        ]

        text_lower = text.lower()
        text_has_neg = any(m in text_lower for m in negation_markers)

        for match in matches:
            if match.score < 0.7:
                continue
            match_lower = (match.text or "").lower()
            match_has_neg = any(m in match_lower for m in negation_markers)
            # One has negation, other doesn't → possible contradiction
            if text_has_neg != match_has_neg:
                return (f"Contradiction with existing fact "
                        f"(score={match.score:.3f}): "
                        f"'{match.text[:80]}'")
        return None

    def _check_novelty(self, text: str) -> float:
        """Return closest ChromaDB match score. Lower = more novel."""
        if not (self.consciousness and hasattr(self.consciousness, 'embed')
                and hasattr(self.consciousness, 'memory')):
            return 0.0

        try:
            vec = self.consciousness.embed.embed_query(text)
            if not vec:
                return 0.0
            matches = self.consciousness.memory.search(
                vec, top_k=1, min_score=0.0)
            if matches:
                return matches[0].score
        except Exception:
            pass
        return 0.0

    @property
    def stats(self) -> Dict[str, Dict[str, int]]:
        return {k: dict(v) for k, v in self._step_stats.items()}


# ═══════════════════════════════════════════════════════════════
# ADAPTIVE TUNER — capacity allocation across sources
# ═══════════════════════════════════════════════════════════════

class AdaptiveTuner:
    """Allocates enrichment capacity across sources based on yield.

    Algorithm:
    1. Benchmark: equal allocation until each source has >= N outcomes
    2. Proportional: allocation = yield / sum(yields), min floor 0.01
    3. Rebalance every M total facts
    4. Burst: pass_rate > threshold → shift up to X% capacity
    5. Throttle: consecutive_failures >= N AND pass_rate < threshold → pause
    """

    def __init__(self, source_manager: SourceManager,
                 benchmark_count: int = 5,
                 rebalance_every: int = 50,
                 burst_threshold: float = 0.80,
                 burst_max_share: float = 0.80,
                 throttle_pass_rate: float = 0.15,
                 throttle_window: int = 30,
                 throttle_pause_seconds: float = 600.0):
        self.sm = source_manager
        self.benchmark_count = benchmark_count
        self.rebalance_every = rebalance_every
        self.burst_threshold = burst_threshold
        self.burst_max_share = burst_max_share
        self.throttle_pass_rate = throttle_pass_rate
        self.throttle_window = throttle_window
        self.throttle_pause_seconds = throttle_pause_seconds

        self._allocations: Dict[str, float] = {}
        self._total_results: int = 0
        self._last_rebalance: int = 0
        self._in_benchmark: bool = True

    def next_source(self) -> Optional[str]:
        """Weighted random pick from allocations. Returns source_id or None."""
        available = self.sm.available_ids
        if not available:
            return None

        # Check benchmark phase
        if self._in_benchmark:
            self._check_benchmark_complete(available)

        if self._in_benchmark:
            # Equal allocation during benchmark
            return random.choice(available)

        # Build weights from allocations, filtering to available
        weights = []
        ids = []
        for sid in available:
            w = self._allocations.get(sid, 0.01)
            weights.append(max(w, 0.001))
            ids.append(sid)

        if not ids:
            return None

        total_w = sum(weights)
        if total_w <= 0:
            return random.choice(ids)

        # Weighted random selection
        r = random.uniform(0, total_w)
        cumulative = 0.0
        for sid, w in zip(ids, weights):
            cumulative += w
            if r <= cumulative:
                return sid
        return ids[-1]

    def record_result(self, source_id: str, passed: bool, novel: bool):
        """Record outcome and check for rebalance/throttle."""
        self._total_results += 1

        # Throttle check — never pause the last available source
        metrics = self.sm.get_metrics(source_id)
        if metrics:
            if (metrics.consecutive_failures >= self.throttle_window
                    and metrics.pass_rate < self.throttle_pass_rate):
                other_available = [
                    sid for sid in self.sm.available_ids
                    if sid != source_id
                ]
                if other_available:
                    metrics.pause(self.throttle_pause_seconds)
                    log.info(
                        f"AdaptiveTuner: paused '{source_id}' for "
                        f"{self.throttle_pause_seconds}s "
                        f"(failures={metrics.consecutive_failures}, "
                        f"pass_rate={metrics.pass_rate:.2f})")
                else:
                    # Last source standing — reset failures instead of pausing
                    metrics._consecutive_failures = 0
                    log.info(
                        f"AdaptiveTuner: '{source_id}' is last source, "
                        f"reset failures (pass_rate={metrics.pass_rate:.2f})")

        # Rebalance check
        if (self._total_results - self._last_rebalance
                >= self.rebalance_every):
            self._rebalance()
            self._last_rebalance = self._total_results

    def _check_benchmark_complete(self, available: List[str]):
        """Exit benchmark when all available sources have enough outcomes."""
        for sid in available:
            m = self.sm.get_metrics(sid)
            if not m or m._total_checked < self.benchmark_count:
                return
        self._in_benchmark = False
        self._rebalance()
        log.info("AdaptiveTuner: benchmark phase complete, switching to "
                 "proportional allocation")

    def _rebalance(self):
        """Recalculate allocations from effective yields."""
        available = self.sm.available_ids
        if not available:
            return

        yields = {}
        for sid in available:
            m = self.sm.get_metrics(sid)
            if m:
                yields[sid] = max(m.effective_yield, 0.01)
            else:
                yields[sid] = 0.01

        total_yield = sum(yields.values())

        # Proportional allocation
        self._allocations = {}
        for sid in available:
            self._allocations[sid] = max(
                yields[sid] / total_yield, 0.01)

        # Burst mode: if any source has pass_rate > threshold, boost it
        for sid in available:
            m = self.sm.get_metrics(sid)
            if (m and m.pass_rate > self.burst_threshold
                    and m._total_checked >= self.benchmark_count):
                # Shift capacity to this source
                other_share = 1.0 - self.burst_max_share
                burst_alloc = self.burst_max_share
                # Redistribute remaining among others
                others = [s for s in available if s != sid]
                if others:
                    per_other = other_share / len(others)
                    for osid in others:
                        self._allocations[osid] = max(per_other, 0.01)
                self._allocations[sid] = burst_alloc
                log.info(
                    f"AdaptiveTuner: burst mode for '{sid}' "
                    f"(pass_rate={m.pass_rate:.2f}, "
                    f"alloc={burst_alloc:.0%})")
                break  # Only one burst source at a time

    @property
    def in_benchmark(self) -> bool:
        return self._in_benchmark

    @property
    def allocations(self) -> Dict[str, float]:
        return dict(self._allocations)

    @property
    def stats(self) -> dict:
        return {
            "in_benchmark": self._in_benchmark,
            "total_results": self._total_results,
            "last_rebalance_at": self._last_rebalance,
            "allocations": dict(self._allocations),
        }


# ═══════════════════════════════════════════════════════════════
# GAP-WEIGHTED SCHEDULER — topic & agent selection
# ═══════════════════════════════════════════════════════════════

class GapWeightedScheduler:
    """Selects agents and topics weighted by knowledge gaps.

    Agents with fewer facts get higher selection weight.
    Category rotation prevents topic convergence.
    Seasonal weighting boosts relevant topics per month.
    """

    # Month -> seasonal topic boosts (1.5x weight for these agents)
    SEASONAL_BOOSTS: Dict[int, List[str]] = {
        1: ["beekeeper", "disease_monitor"],  # winter check, oxalic
        2: ["beekeeper", "equipment_tech", "maintenance_planner"],  # prep
        3: ["beekeeper", "disease_monitor", "swarm_watcher", "horticulturist", "equipment_tech", "maintenance_planner"],  # kevättarkastus — tärkein kuukausi
        4: ["swarm_watcher", "nectar_scout", "phenologist"],  # swarming prep
        5: ["swarm_watcher", "nectar_scout", "flight_weather"],  # swarming
        6: ["nectar_scout", "beekeeper", "meteorologist"],  # honey flow
        7: ["nectar_scout", "beekeeper", "wilderness_chef"],  # extraction
        8: ["beekeeper", "disease_monitor", "horticulturist"],  # varroa
        9: ["beekeeper", "disease_monitor", "forester"],  # formic acid
        10: ["beekeeper", "disease_monitor", "firewood"],  # oxalic, winter prep
        11: ["beekeeper", "energy_advisor", "hvac_specialist"],  # wintering
        12: ["beekeeper", "indoor_garden", "smart_home"],  # winter
    }

    # Category groups for rotation — ensures diversity
    CATEGORY_GROUPS: List[List[str]] = [
        # Bee agents
        ["beekeeper", "disease_monitor", "swarm_watcher", "nectar_scout",
         "flight_weather", "hive_temperature", "hive_security"],
        # Nature agents
        ["ornithologist", "entomologist", "phenologist", "forester",
         "wildlife_ranger", "nature_photographer", "pest_control"],
        # Water/weather
        ["meteorologist", "storm_alert", "microclimate", "air_quality",
         "frost_soil", "limnologist", "fishing_guide", "shore_guard",
         "ice_specialist"],
        # Property/tech
        ["electrician", "hvac_specialist", "carpenter", "chimney_sweep",
         "lighting_master", "fire_officer", "equipment_tech"],
        # Home/lifestyle
        ["smart_home", "indoor_garden", "apartment_board", "energy_advisor",
         "pet_care", "child_safety", "noise_monitor", "delivery_tracker",
         "commute_planner", "budget_tracker"],
        # Food/leisure
        ["wilderness_chef", "baker", "nutritionist", "sauna_master",
         "entertainment_chief", "movie_expert"],
        # Factory
        ["production_line", "quality_inspector", "safety_officer",
         "maintenance_planner", "waste_handler", "lab_analyst",
         "compliance", "forklift_fleet"],
        # Security/admin
        ["cyber_guard", "locksmith", "yard_guard", "privacy_guard",
         "inventory_chief", "recycling", "cleaning_manager", "logistics"],
        # Cottage
        ["well_water", "septic_manager", "firewood"],
        # Science
        ["astronomer", "light_shadow", "math_physicist"],
    ]

    def __init__(self, consciousness, rebalance_every: int = 50,
                 gap_high: int = 20, gap_medium: int = 50,
                 gap_normal: int = 100):
        self.consciousness = consciousness
        self.rebalance_every = rebalance_every
        self.gap_high = gap_high
        self.gap_medium = gap_medium
        self.gap_normal = gap_normal

        self._weighted_list: List[tuple] = []  # [(agent_id, keyword, weight)]
        self._fact_counts: Dict[str, int] = {}
        self._enriched_since_rebalance: int = 0
        self._initialized: bool = False
        self._category_index: int = 0  # rotation pointer

    def next_agent_and_topic(self) -> tuple:
        """Return (agent_id, topic) via weighted random selection.

        Lazy-initializes on first call.
        Uses category rotation to ensure topic diversity.
        """
        if not self._initialized:
            self._build_weighted_list()
            self._initialized = True

        # Check if rebalance needed
        if (self._enriched_since_rebalance >= self.rebalance_every
                and self.rebalance_every > 0):
            self._build_weighted_list()
            self._enriched_since_rebalance = 0

        if not self._weighted_list:
            return ("enrichment", "beekeeping general")

        # Category rotation: every 3rd call, force a different category
        if self._enriched_since_rebalance % 3 == 0 and self.CATEGORY_GROUPS:
            result = self._pick_from_category_rotation()
            if result:
                return result

        weights = [w for _, _, w in self._weighted_list]
        total = sum(weights)
        if total <= 0:
            idx = random.randrange(len(self._weighted_list))
        else:
            r = random.uniform(0, total)
            cumulative = 0.0
            idx = len(self._weighted_list) - 1
            for i, (_, _, w) in enumerate(self._weighted_list):
                cumulative += w
                if r <= cumulative:
                    idx = i
                    break

        agent_id, keyword, _ = self._weighted_list[idx]
        return (agent_id, keyword)

    def record_enrichment(self):
        """Track enriched facts for rebalance timing."""
        self._enriched_since_rebalance += 1

    def _pick_from_category_rotation(self) -> Optional[tuple]:
        """Pick agent from the next category group in rotation."""
        if not self.CATEGORY_GROUPS:
            return None

        # Advance rotation pointer
        self._category_index = (self._category_index + 1) % len(self.CATEGORY_GROUPS)
        group = self.CATEGORY_GROUPS[self._category_index]

        # Filter to agents that have weighted list entries
        candidates = [
            (aid, kw, w) for aid, kw, w in self._weighted_list
            if aid in group
        ]
        if not candidates:
            return None

        # Weighted random from this category
        weights = [w for _, _, w in candidates]
        total = sum(weights)
        if total <= 0:
            idx = random.randrange(len(candidates))
        else:
            r = random.uniform(0, total)
            cumulative = 0.0
            idx = len(candidates) - 1
            for i, (_, _, w) in enumerate(candidates):
                cumulative += w
                if r <= cumulative:
                    idx = i
                    break

        agent_id, keyword, _ = candidates[idx]
        return (agent_id, keyword)

    def _build_weighted_list(self):
        """Scan ChromaDB for fact counts per agent's top keywords.

        Applies seasonal boost (1.5x) for month-relevant agents.
        """
        t0 = time.monotonic()
        try:
            from core.yaml_bridge import ROUTING_KEYWORDS
        except ImportError:
            log.warning("GapScheduler: ROUTING_KEYWORDS not available")
            self._weighted_list = [
                ("enrichment", "beekeeping general", 1.0)]
            return

        self._weighted_list = []
        self._fact_counts = {}

        # Seasonal boost for current month
        from datetime import datetime as _dt
        month = _dt.now().month
        seasonal_agents = self.SEASONAL_BOOSTS.get(month, [])

        for agent_id, keywords in ROUTING_KEYWORDS.items():
            # Use top-3 keywords per agent for estimation
            top_kws = keywords[:3] if len(keywords) >= 3 else keywords
            agent_fact_count = 0

            for kw in top_kws:
                count = self._estimate_facts_for_keyword(kw)
                agent_fact_count = max(agent_fact_count, count)

            self._fact_counts[agent_id] = agent_fact_count

            # Weight: fewer facts = higher weight
            if agent_fact_count < self.gap_high:
                weight = 3.0
            elif agent_fact_count < self.gap_medium:
                weight = 2.0
            elif agent_fact_count < self.gap_normal:
                weight = 1.0
            else:
                weight = 0.5

            # Seasonal boost: 1.5x for month-relevant agents
            if agent_id in seasonal_agents:
                weight *= 1.5

            # Add one entry per top keyword (spread across keywords)
            for kw in top_kws:
                self._weighted_list.append(
                    (agent_id, kw, weight / len(top_kws)))

        elapsed = time.monotonic() - t0
        log.info(f"GapScheduler: scan complete in {elapsed:.1f}s "
                 f"({len(self._weighted_list)} entries, "
                 f"{len(self._fact_counts)} agents, "
                 f"seasonal boost for {len(seasonal_agents)} agents)")

    def _estimate_facts_for_keyword(self, keyword: str) -> int:
        """Estimate fact count by searching ChromaDB for keyword."""
        if not (self.consciousness
                and hasattr(self.consciousness, 'embed')
                and hasattr(self.consciousness, 'memory')):
            return 0

        try:
            vec = self.consciousness.embed.embed_query(keyword)
            if not vec:
                return 0
            matches = self.consciousness.memory.search(
                vec, top_k=50, min_score=0.5)
            return len(matches)
        except Exception:
            return 0

    @property
    def gap_summary(self) -> Dict[str, dict]:
        """Per-agent gap summary."""
        result = {}
        for agent_id, count in sorted(
                self._fact_counts.items(),
                key=lambda x: x[1]):
            if count < self.gap_high:
                level = "critical"
            elif count < self.gap_medium:
                level = "low"
            elif count < self.gap_normal:
                level = "moderate"
            else:
                level = "good"
            result[agent_id] = {"fact_count": count, "level": level}
        return result


# ═══════════════════════════════════════════════════════════════
# CONVERGENCE DETECTOR — stop enriching when facts stop being novel
# ═══════════════════════════════════════════════════════════════

class ConvergenceDetector:
    """Detects per-source convergence when novelty drops below threshold.

    A source is considered converged when its rolling novelty_score
    (fraction of novel results over last window_size outcomes)
    falls below `threshold` for `patience` consecutive checks.

    Converged sources are paused for `pause_s` seconds to avoid wasting
    GPU cycles searching for facts the system already knows.
    """

    def __init__(self, threshold: float = 0.20, patience: int = 3,
                 pause_s: float = 1800.0, window_size: int = 20):
        self.threshold = threshold
        self.patience = patience
        self.pause_s = pause_s
        self.window_size = window_size

        self._consecutive_below: Dict[str, int] = {}
        self._converged_at: Dict[str, float] = {}
        self._total_convergences: int = 0

    def check(self, source_id: str, metrics: SourceMetrics,
              source_manager: "SourceManager | None" = None) -> bool:
        """Check if source has converged. Returns True if newly converged.

        Side effect: pauses the source if converged (unless it is the last
        available source).
        """
        if metrics.is_paused:
            return False  # Already paused (by AdaptiveTuner or previous convergence)

        # Need enough data before deciding
        if metrics._total_checked < self.window_size:
            return False

        novelty = metrics.novelty_score
        if novelty < self.threshold:
            self._consecutive_below[source_id] = (
                self._consecutive_below.get(source_id, 0) + 1)
        else:
            self._consecutive_below[source_id] = 0

        if self._consecutive_below.get(source_id, 0) >= self.patience:
            # Never pause the last available source
            if source_manager is not None:
                other_available = [
                    sid for sid in source_manager.available_ids
                    if sid != source_id
                ]
                if not other_available:
                    self._consecutive_below[source_id] = 0
                    log.info(
                        f"ConvergenceDetector: '{source_id}' converged but "
                        f"is last source, skipping pause")
                    return False

            # Convergence confirmed — pause the source
            metrics.pause(self.pause_s)
            self._consecutive_below[source_id] = 0
            self._converged_at[source_id] = time.monotonic()
            self._total_convergences += 1
            log.info(
                f"ConvergenceDetector: '{source_id}' converged "
                f"(novelty={novelty:.2f}<{self.threshold}, "
                f"paused {self.pause_s/60:.0f}min)")
            return True

        return False

    def all_converged(self, source_manager: "SourceManager") -> bool:
        """True if ALL available sources are currently converged/paused."""
        available_before_pause = [
            sid for sid in source_manager.source_ids
            if source_manager.get_source(sid).is_available()
        ]
        if not available_before_pause:
            return False
        # Check if all are paused
        return all(
            source_manager.get_source(sid).metrics.is_paused
            for sid in available_before_pause
        )

    @property
    def stats(self) -> dict:
        return {
            "threshold": self.threshold,
            "patience": self.patience,
            "pause_s": self.pause_s,
            "total_convergences": self._total_convergences,
            "consecutive_below": dict(self._consecutive_below),
        }


# ═══════════════════════════════════════════════════════════════
# NIGHT ENRICHER — top-level orchestrator
# ═══════════════════════════════════════════════════════════════

class NightEnricher:
    """Unified night-mode enrichment orchestrator.

    Manages multiple sources through one QualityGate with adaptive
    tuning and gap-weighted scheduling.
    """

    def __init__(self, consciousness, llm_fast, llm_validate, config: dict):
        self.consciousness = consciousness
        ne_cfg = config.get("advanced_learning", {}).get("night_enricher", {})

        # Quality gate
        self.quality_gate = QualityGate(
            consciousness, llm_validate,
            novelty_threshold=ne_cfg.get("novelty_score_threshold", 0.85),
            dedup_threshold=ne_cfg.get("dedup_score_threshold", 0.93),
        )

        # Source manager
        self.source_manager = SourceManager()

        # Register sources
        self._self_gen = SelfGenerateSource(llm_fast, llm_validate)
        self.source_manager.register(self._self_gen)

        web_budget = config.get("advanced_learning", {}).get(
            "web_learning_daily_budget", 50)
        self.source_manager.register(WebScrapeSource(
            daily_budget=web_budget))

        distill_budget = config.get("advanced_learning", {}).get(
            "distillation_weekly_budget_eur", 5.0)
        self.source_manager.register(ClaudeDistillSource(
            weekly_budget_eur=distill_budget))

        if ne_cfg.get("chat_history_enabled", False):
            self.source_manager.register(ChatHistorySource())
        else:
            log.info("ChatHistorySource disabled by config")

        if ne_cfg.get("rss_feed_enabled", False):
            self.source_manager.register(RssFeedSource())
        else:
            log.info("RssFeedSource disabled by config")

        # Adaptive tuner
        self.tuner = AdaptiveTuner(
            self.source_manager,
            benchmark_count=ne_cfg.get("benchmark_count", 5),
            rebalance_every=ne_cfg.get("rebalance_every", 50),
            burst_threshold=ne_cfg.get("burst_threshold", 0.80),
            burst_max_share=ne_cfg.get("burst_max_share", 0.80),
            throttle_pass_rate=ne_cfg.get("throttle_pass_rate", 0.15),
            throttle_window=ne_cfg.get("throttle_window", 30),
            throttle_pause_seconds=ne_cfg.get(
                "throttle_pause_seconds", 600.0),
        )

        # Gap-weighted scheduler
        self.gap_scheduler = GapWeightedScheduler(
            consciousness,
            rebalance_every=ne_cfg.get("rebalance_every", 50),
            gap_high=ne_cfg.get("gap_high_threshold", 20),
            gap_medium=ne_cfg.get("gap_medium_threshold", 50),
            gap_normal=ne_cfg.get("gap_normal_threshold", 100),
        )

        # Convergence detector (D2)
        self.convergence = ConvergenceDetector(
            threshold=ne_cfg.get("convergence_novelty_threshold", 0.20),
            patience=ne_cfg.get("convergence_patience", 3),
            pause_s=ne_cfg.get("convergence_pause_s", 1800.0),
            window_size=ne_cfg.get("convergence_window", 20),
        )

        # External sources (D1) — injected after init via set_external_sources()
        self._ext_web_learner = None
        self._ext_distiller = None
        self._ext_rss_monitor = None
        self._ext_cycle_count = 0
        # How often to run each external source (in main cycle units)
        self._ext_web_every = ne_cfg.get("ext_web_every_cycles", 10)
        self._ext_distill_every = ne_cfg.get("ext_distill_every_cycles", 20)
        self._ext_rss_every = ne_cfg.get("ext_rss_every_cycles", 50)
        self._ext_web_stored = 0
        self._ext_distill_stored = 0
        self._ext_rss_stored = 0

        # Session tracking
        self._session_start = time.monotonic()
        self._total_checked = 0
        self._total_stored = 0
        self._per_agent_stored: Dict[str, int] = {}

    def set_external_sources(self, web_learner=None, distiller=None,
                              rss_monitor=None):
        """Inject real external learning agents (D1).

        These bypass the QualityGate pipeline — they have their own
        internal validation (web: dual-model, distill: expert, rss: direct).
        Called by HiveMind after init_learning_engines().
        """
        self._ext_web_learner = web_learner
        self._ext_distiller = distiller
        self._ext_rss_monitor = rss_monitor
        log.info(
            f"NightEnricher external sources set: "
            f"web={bool(web_learner)}, "
            f"distill={bool(distiller)}, "
            f"rss={bool(rss_monitor)}")

    async def _run_external_sources(self, throttle=None) -> int:
        """Run external agents on their cycle schedules (D1).

        Web every ext_web_every cycles, distill every ext_distill_every,
        RSS every ext_rss_every. External agents store directly and have
        own validation — do NOT run through QualityGate.
        """
        self._ext_cycle_count += 1
        stored = 0
        n = self._ext_cycle_count

        # Web learning
        if (self._ext_web_learner and self._ext_web_every > 0
                and n % self._ext_web_every == 0):
            try:
                web_stored = await self._ext_web_learner.web_learning_cycle(
                    throttle)
                stored += web_stored
                self._ext_web_stored += web_stored
                if web_stored:
                    log.info(
                        f"🌐 External web: +{web_stored} facts "
                        f"(total={self._ext_web_stored})")
            except Exception as e:
                log.error(f"External web cycle error: {e}")

        # Distillation
        if (self._ext_distiller and self._ext_distill_every > 0
                and n % self._ext_distill_every == 0):
            try:
                dist_stored = await self._ext_distiller.distillation_cycle(
                    throttle)
                stored += dist_stored
                self._ext_distill_stored += dist_stored
                if dist_stored:
                    log.info(
                        f"🧠 External distill: +{dist_stored} facts "
                        f"(total={self._ext_distill_stored})")
            except Exception as e:
                log.error(f"External distill cycle error: {e}")

        # RSS feeds
        if (self._ext_rss_monitor and self._ext_rss_every > 0
                and n % self._ext_rss_every == 0):
            try:
                rss_stored = await self._ext_rss_monitor.update_context(
                    self.consciousness)
                stored += rss_stored
                self._ext_rss_stored += rss_stored
                if rss_stored:
                    log.info(
                        f"📰 External RSS: +{rss_stored} facts "
                        f"(total={self._ext_rss_stored})")
            except Exception as e:
                log.error(f"External RSS cycle error: {e}")

        return stored

    async def enrichment_cycle(self, throttle=None) -> int:
        """One enrichment cycle: pick source → generate → validate → store.

        Also runs external agents on their schedules (D1) and checks
        for convergence (D2). Returns number of facts stored (0-N).
        """
        # Run external sources on their schedules (D1)
        ext_stored = await self._run_external_sources(throttle)

        source_id = self.tuner.next_source()
        if not source_id:
            log.debug(f"NightEnricher: no source available "
                      f"(available={self.source_manager.available_ids})")
            return ext_stored

        source = self.source_manager.get_source(source_id)
        if not source:
            return 0

        agent_id, topic = self.gap_scheduler.next_agent_and_topic()
        log.info(f"NightEnricher: source={source_id}, agent={agent_id}, "
                 f"topic={topic[:60]}")

        # Layer 4: inject cross-source context hints
        _cs = getattr(self, '_cross_search', None)
        if _cs:
            try:
                _embed = getattr(self, '_consciousness', None)
                if _embed and hasattr(_embed, 'embed'):
                    _qe = _embed.embed.embed_document(topic[:200])
                    if _qe:
                        _hints = _cs.search_by_role(_qe, "scouts", top_k=2)
                        if _hints:
                            _hint_text = "; ".join(
                                h.get("text", "")[:100] for h in _hints[:2])
                            topic = f"{topic} [context: {_hint_text}]"
            except Exception as _e:
                log.debug(f"Cross-source hint: {_e}")

        t0 = time.monotonic()
        try:
            candidates = await source.generate_candidates(
                topic, agent_id, 3, throttle)
        except Exception as e:
            log.error(f"NightEnricher source '{source_id}' error: {e}")
            return 0

        if not candidates:
            log.info(f"NightEnricher: no candidates from {source_id}")
            # Record empty result as failure for tuner
            cycle_time = time.monotonic() - t0
            source.metrics.record_outcome(False, False, cycle_time)
            self.tuner.record_result(source_id, False, False)
            return 0

        stored = 0
        for candidate in candidates:
            ct0 = time.monotonic()
            self._total_checked += 1

            try:
                verdict = await self.quality_gate.check(candidate, throttle)
            except Exception as e:
                log.error(f"QualityGate error: {e}")
                verdict = QualityVerdict(
                    passed=False, novel=False,
                    reason=f"Error: {e}", step_failed="error")

            cycle_time = time.monotonic() - ct0
            source.metrics.record_outcome(
                verdict.passed, verdict.novel, cycle_time)
            self.tuner.record_result(
                source_id, verdict.passed, verdict.novel)

            # D2: Convergence check after each outcome
            self.convergence.check(source_id, source.metrics, self.source_manager)

            if verdict.passed:
                try:
                    # TODO: At 200K+ facts, consider batching instead of
                    # immediate=True per fact. OK at current volume (~50-200/night).
                    self.consciousness.learn(
                        candidate.text,
                        agent_id=candidate.agent_id,
                        source_type="autonomous_enrichment",
                        confidence=0.80,
                        validated=True,
                        immediate=True,
                        metadata={
                            "enrichment_source": source_id,
                            "gap_topic": topic,
                            "novel": verdict.novel,
                            "validation": "quality_gate",
                        })
                    stored += 1
                    self._total_stored += 1
                    self._per_agent_stored[agent_id] = (
                        self._per_agent_stored.get(agent_id, 0) + 1)
                    self.gap_scheduler.record_enrichment()
                    # Layer 5: Record fact production trust signal
                    _te = getattr(self, '_trust_engine', None)
                    if _te:
                        try:
                            _te.record_signal(agent_id, "fact_production", 1.0)
                        except Exception:
                            pass
                    log.info(
                        f"✨ NightEnricher [{source_id}]: "
                        f"'{candidate.text[:60]}' "
                        f"(agent={agent_id}, topic={topic})")
                except Exception as e:
                    log.error(f"NightEnricher store error: {e}")
            else:
                log.debug(
                    f"❌ NightEnricher reject [{source_id}]: "
                    f"step={verdict.step_failed}, "
                    f"reason={verdict.reason[:80]}")

        # Log enrichment cycle to structured logger
        try:
            from hivemind import StructuredLogger
            _sl = StructuredLogger()
            _sl.log_learning(
                event="enrichment_cycle",
                count=stored,
                duration_ms=(time.monotonic() - t0) * 1000,
                source=source_id or "none",
                extra={
                    "agent_id": agent_id,
                    "topic": topic[:60],
                    "candidates_generated": len(candidates) if candidates else 0,
                    "facts_stored": stored,
                    "ext_stored": ext_stored,
                    "total_session_stored": self._total_stored,
                })
        except Exception:
            pass

        return stored

    def generate_morning_report(self) -> dict:
        """Generate comprehensive morning report."""
        elapsed = time.monotonic() - self._session_start
        elapsed_min = elapsed / 60.0

        overall_pass = (self._total_stored / max(self._total_checked, 1))

        # Per-source breakdown
        per_source = {}
        for sid in self.source_manager.source_ids:
            m = self.source_manager.get_metrics(sid)
            if m:
                per_source[sid] = m.to_dict()

        # Gap summary (top 10 weakest)
        full_gap = self.gap_scheduler.gap_summary
        gap_top10 = dict(list(full_gap.items())[:10])

        report = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "session_duration_min": round(elapsed_min, 1),
            "total_checked": self._total_checked,
            "total_stored": self._total_stored,
            "overall_pass_rate": round(overall_pass, 3),
            "per_source": per_source,
            "per_agent": dict(self._per_agent_stored),
            "tuner": self.tuner.stats,
            "quality_gate": self.quality_gate.stats,
            "gap_summary_top10": gap_top10,
            "capacity_used_pct": round(
                (self._total_checked / max(elapsed_min, 0.1)) * 100
                / 60.0, 1),  # checks/min as % of 1/sec theoretical max
            "convergence": self.convergence.stats,
            "external_sources": {
                "web_stored": self._ext_web_stored,
                "distill_stored": self._ext_distill_stored,
                "rss_stored": self._ext_rss_stored,
                "cycle_count": self._ext_cycle_count,
            },
        }
        return report

    @property
    def stats(self) -> dict:
        return {
            "total_checked": self._total_checked,
            "total_stored": self._total_stored,
            "overall_pass_rate": round(
                self._total_stored / max(self._total_checked, 1), 3),
            "sources": self.source_manager.get_all_stats(),
            "tuner": self.tuner.stats,
            "convergence": self.convergence.stats,
            "external": {
                "web_stored": self._ext_web_stored,
                "distill_stored": self._ext_distill_stored,
                "rss_stored": self._ext_rss_stored,
            },
        }

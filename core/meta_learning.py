"""
WaggleDance — Phase 9, Layer 5: Meta-Learning Engine
======================================================
Analyzes system performance and generates optimization suggestions.
No LLM calls — pure data analysis from existing stats.

Runs weekly (168h interval), triggered from night mode cycle.
Reports saved to data/meta_reports.jsonl.
Safe auto-optimizations: cache size increase, threshold notes — no code changes.
"""

import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

log = logging.getLogger("meta_learning")


class MetaLearningEngine:
    """Analyze learning data, optimize system behavior."""

    WEEKLY_INTERVAL_H = 168  # 7 days

    def __init__(self, consciousness, agent_levels=None,
                 enrichment=None, web_learner=None, distiller=None):
        self.consciousness = consciousness
        self.agent_levels = agent_levels
        self.enrichment = enrichment
        self.web_learner = web_learner
        self.distiller = distiller

        self._last_report = None
        self._last_run = 0.0
        self._total_reports = 0
        self._optimizations_applied = 0
        self._reports_path = Path("data/meta_reports.jsonl")

    async def weekly_analysis(self) -> dict:
        """Full weekly analysis. Returns comprehensive report dict."""
        report = {
            "timestamp": datetime.now().isoformat(),
            "memory_stats": self._analyze_memory(),
            "hallucination_stats": self._analyze_hallucinations(),
            "learning_efficiency": self._analyze_learning_efficiency(),
            "weakest_areas": self._find_weakest_areas(),
            "suggestions": [],
        }

        report["suggestions"] = self._generate_suggestions(report)

        # Phase 7: Agent overlap detection
        try:
            _al_cfg = {}
            try:
                import yaml as _yaml
                _cfg_path = Path("configs/settings.yaml")
                if _cfg_path.exists():
                    with open(_cfg_path, encoding="utf-8") as _f:
                        _al_cfg = (_yaml.safe_load(_f) or {}).get("advanced_learning", {})
            except Exception:
                pass

            if _al_cfg.get("agent_overlap_detection", True):
                detector = AgentOverlapDetector()
                overlap_suggestions = detector.analyze()
                report["agent_overlap"] = {
                    "suggestions": overlap_suggestions[:10],
                    "stats": detector.stats,
                }
        except Exception as e:
            log.warning(f"Agent overlap detection error: {e}")

        self._last_report = report
        self._last_run = time.monotonic()
        self._total_reports += 1
        self._save_report(report)

        log.info(f"📊 Meta-learning report #{self._total_reports}: "
                 f"{len(report['suggestions'])} suggestions")
        return report

    async def auto_apply_safe_optimizations(self, suggestions: list) -> int:
        """Apply safe optimizations. NEVER changes code. Returns applied count."""
        applied = 0
        for s in suggestions:
            if not s.get("auto_safe", False):
                continue

            action = s.get("action", "")
            try:
                if action == "increase_hot_cache":
                    if (hasattr(self.consciousness, 'hot_cache')
                            and self.consciousness.hot_cache):
                        old = self.consciousness.hot_cache._max_size
                        new_size = min(old + 100, 2000)
                        if new_size > old:
                            self.consciousness.hot_cache._max_size = new_size
                            applied += 1
                            log.info(f"📊 Auto-opt: hot cache "
                                     f"{old} → {new_size}")
                # Other safe optimizations can be added here
                # e.g. threshold adjustments, synonym expansions
            except Exception as e:
                log.warning(f"Auto-opt failed ({action}): {e}")

        self._optimizations_applied += applied
        return applied

    def _analyze_memory(self) -> dict:
        """Analyze memory store stats."""
        try:
            c = self.consciousness
            return {
                "total_facts": c.memory.count if c.memory else 0,
                "swarm_facts": (c.memory.swarm_facts.count()
                                if c.memory and hasattr(c.memory, 'swarm_facts')
                                else 0),
                "corrections": (c.memory.corrections.count()
                                if c.memory and hasattr(c.memory, 'corrections')
                                else 0),
                "episodes": (c.memory.episodes.count()
                             if c.memory and hasattr(c.memory, 'episodes')
                             else 0),
                "bilingual_fi": (c.bilingual.fi_count
                                 if hasattr(c, 'bilingual') and c.bilingual
                                 else 0),
                "hot_cache": (c.hot_cache.stats
                              if hasattr(c, 'hot_cache') and c.hot_cache
                              else {}),
                "learn_queue_size": len(c._learn_queue) if hasattr(c, '_learn_queue') else 0,
            }
        except Exception as e:
            log.debug(f"Memory analysis error: {e}")
            return {"error": str(e)}

    def _analyze_hallucinations(self) -> dict:
        """Analyze hallucination stats per agent."""
        result = {"per_agent": {}, "overall_rate": 0.0}

        if self.agent_levels:
            try:
                all_stats = self.agent_levels.get_all_stats()
                total_resp = 0
                total_halluc = 0
                for agent_id, stats in all_stats.items():
                    if isinstance(stats, dict):
                        resp = stats.get("total_responses", 0)
                        halluc = stats.get("hallucination_count", 0)
                        total_resp += resp
                        total_halluc += halluc
                        rate = halluc / max(resp, 1)
                        result["per_agent"][agent_id] = {
                            "responses": resp,
                            "hallucinations": halluc,
                            "rate": round(rate, 4),
                        }
                result["overall_rate"] = round(
                    total_halluc / max(total_resp, 1), 4)
            except Exception as e:
                log.debug(f"Hallucination analysis error: {e}")

        # Also check consciousness stats
        try:
            c = self.consciousness
            if hasattr(c, '_total_queries') and c._total_queries > 0:
                result["consciousness_rate"] = round(
                    c._hallucination_count / c._total_queries, 4)
                result["total_queries"] = c._total_queries
                result["hallucinations_caught"] = c._hallucination_count
        except Exception:
            pass

        return result

    def _analyze_learning_efficiency(self) -> dict:
        """Analyze learning source efficiency."""
        result = {}

        if self.enrichment:
            result["enrichment"] = self.enrichment.stats

        if self.web_learner:
            result["web_learning"] = self.web_learner.stats

        if self.distiller:
            result["distillation"] = self.distiller.stats

        return result

    def _find_weakest_areas(self) -> list:
        """Identify knowledge areas that need improvement."""
        weakest = []

        # Check hallucination rates per agent
        if self.agent_levels:
            try:
                all_stats = self.agent_levels.get_all_stats()
                for agent_id, stats in all_stats.items():
                    if isinstance(stats, dict):
                        rate = (stats.get("hallucination_count", 0)
                                / max(stats.get("total_responses", 1), 1))
                        if rate > 0.15:
                            weakest.append({
                                "type": "high_hallucination_agent",
                                "agent_id": agent_id,
                                "rate": round(rate, 3),
                            })
            except Exception:
                pass

        # Check cache hit rate
        try:
            if (hasattr(self.consciousness, 'hot_cache')
                    and self.consciousness.hot_cache):
                cache_stats = self.consciousness.hot_cache.stats
                if (cache_stats.get("total_hits", 0)
                        + cache_stats.get("total_misses", 0) > 50):
                    hit_rate = cache_stats.get("hit_rate", 0)
                    if hit_rate < 0.1:
                        weakest.append({
                            "type": "low_cache_hit_rate",
                            "hit_rate": round(hit_rate, 3),
                        })
        except Exception:
            pass

        return weakest

    def _generate_suggestions(self, report: dict) -> list:
        """Generate optimization suggestions from report data."""
        suggestions = []

        # Check if hot cache could be larger
        mem = report.get("memory_stats", {})
        cache = mem.get("hot_cache", {})
        if cache and isinstance(cache, dict):
            try:
                size = int(cache.get("size", 0))
                max_size = int(cache.get("max_size", 500))
                hit_rate = float(cache.get("hit_rate", 0))
            except (TypeError, ValueError):
                size = max_size = hit_rate = 0
            if size >= max_size * 0.9 and hit_rate > 0.05:
                suggestions.append({
                    "type": "performance",
                    "description": (f"Hot cache is {size}/{max_size} "
                                    f"(hit rate {hit_rate:.1%}). "
                                    f"Increase cache size."),
                    "action": "increase_hot_cache",
                    "auto_safe": True,
                    "priority": "medium",
                })

        # Check weak agents
        for weak in report.get("weakest_areas", []):
            if weak.get("type") == "high_hallucination_agent":
                suggestions.append({
                    "type": "quality",
                    "description": (f"Agent {weak['agent_id']} has "
                                    f"{weak['rate']:.1%} hallucination rate. "
                                    f"Consider demotion or retraining."),
                    "action": "review_agent",
                    "auto_safe": False,
                    "priority": "high",
                })

        # Learning efficiency suggestions
        eff = report.get("learning_efficiency", {})
        enr = eff.get("enrichment", {})
        if enr:
            success = enr.get("success_rate", 0)
            if success < 0.3 and enr.get("generated", 0) > 10:
                suggestions.append({
                    "type": "learning",
                    "description": (f"Enrichment success rate is "
                                    f"{success:.1%}. Validation may be "
                                    f"too strict or generation too noisy."),
                    "action": "review_enrichment",
                    "auto_safe": False,
                    "priority": "medium",
                })

        return suggestions

    def _save_report(self, report: dict):
        """Save report to data/meta_reports.jsonl."""
        try:
            self._reports_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._reports_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(report, ensure_ascii=False,
                                   default=str) + "\n")
        except Exception as e:
            log.warning(f"Failed to save meta report: {e}")

    def is_due(self) -> bool:
        """Check if weekly analysis is due."""
        if self._last_run == 0.0:
            return True
        elapsed_h = (time.monotonic() - self._last_run) / 3600
        return elapsed_h >= self.WEEKLY_INTERVAL_H

    @property
    def stats(self) -> dict:
        return {
            "total_reports": self._total_reports,
            "optimizations_applied": self._optimizations_applied,
            "last_run": (datetime.fromtimestamp(
                time.time() - (time.monotonic() - self._last_run)
            ).isoformat() if self._last_run > 0 else None),
            "has_report": self._last_report is not None,
            "suggestions_count": (len(self._last_report.get("suggestions", []))
                                  if self._last_report else 0),
        }


class AgentOverlapDetector:
    """Analyze agent token overlap and suggest merges/absorbs.

    Build per-agent token sets from YAML index.
    Compute Jaccard similarity for all agent pairs.
    Suggest "merge" if Jaccard > 0.40.
    Suggest "absorb" if one agent is >70% contained in another AND has <15 questions.
    NEVER auto-execute — only log suggestions.
    """

    def __init__(self, data_dir="data"):
        self._data_dir = Path(data_dir)
        self._log_path = self._data_dir / "agent_mutations.jsonl"
        self._suggestions: list[dict] = []

    def analyze(self, yaml_index: list = None) -> list[dict]:
        """Analyze agent overlap from YAML index data.

        yaml_index: list of (content_tokens, question, answer, agent_id, agent_name_tokens)
        Returns list of suggestion dicts.
        """
        if not yaml_index:
            yaml_index = self._load_yaml_index()
        if not yaml_index:
            return []

        # Build per-agent token sets and question counts
        agent_tokens: dict[str, set[str]] = {}
        agent_question_counts: dict[str, int] = {}
        for content_tokens, _q, _a, agent_id, _aname in yaml_index:
            if agent_id not in agent_tokens:
                agent_tokens[agent_id] = set()
                agent_question_counts[agent_id] = 0
            agent_tokens[agent_id].update(content_tokens)
            agent_question_counts[agent_id] += 1

        suggestions = []
        agent_ids = sorted(agent_tokens.keys())

        # Pairwise Jaccard similarity
        for i, aid_a in enumerate(agent_ids):
            for aid_b in agent_ids[i + 1:]:
                tokens_a = agent_tokens[aid_a]
                tokens_b = agent_tokens[aid_b]
                if not tokens_a or not tokens_b:
                    continue

                intersection = len(tokens_a & tokens_b)
                union = len(tokens_a | tokens_b)
                jaccard = intersection / union if union > 0 else 0

                # Containment checks
                containment_a_in_b = intersection / len(tokens_a) if tokens_a else 0
                containment_b_in_a = intersection / len(tokens_b) if tokens_b else 0

                if jaccard > 0.40:
                    suggestions.append({
                        "type": "merge",
                        "agent_a": aid_a,
                        "agent_b": aid_b,
                        "jaccard": round(jaccard, 3),
                        "shared_tokens": intersection,
                        "questions_a": agent_question_counts.get(aid_a, 0),
                        "questions_b": agent_question_counts.get(aid_b, 0),
                        "reason": f"High overlap (Jaccard={jaccard:.2f}): "
                                  f"{aid_a} and {aid_b} share {intersection} tokens",
                        "timestamp": datetime.now().isoformat(),
                    })
                elif (containment_a_in_b > 0.70
                      and agent_question_counts.get(aid_a, 0) < 15):
                    suggestions.append({
                        "type": "absorb",
                        "smaller": aid_a,
                        "larger": aid_b,
                        "containment": round(containment_a_in_b, 3),
                        "questions_smaller": agent_question_counts.get(aid_a, 0),
                        "questions_larger": agent_question_counts.get(aid_b, 0),
                        "reason": f"{aid_a} ({agent_question_counts.get(aid_a, 0)} Qs) is "
                                  f"{containment_a_in_b:.0%} contained in {aid_b} — consider absorbing",
                        "timestamp": datetime.now().isoformat(),
                    })
                elif (containment_b_in_a > 0.70
                      and agent_question_counts.get(aid_b, 0) < 15):
                    suggestions.append({
                        "type": "absorb",
                        "smaller": aid_b,
                        "larger": aid_a,
                        "containment": round(containment_b_in_a, 3),
                        "questions_smaller": agent_question_counts.get(aid_b, 0),
                        "questions_larger": agent_question_counts.get(aid_a, 0),
                        "reason": f"{aid_b} ({agent_question_counts.get(aid_b, 0)} Qs) is "
                                  f"{containment_b_in_a:.0%} contained in {aid_a} — consider absorbing",
                        "timestamp": datetime.now().isoformat(),
                    })

        # Sort by severity (highest jaccard/containment first)
        suggestions.sort(key=lambda s: s.get("jaccard", s.get("containment", 0)), reverse=True)
        self._suggestions = suggestions

        # Save to log
        self._save_suggestions(suggestions)

        log.info(f"🔍 Agent overlap: {len(suggestions)} suggestions "
                 f"({sum(1 for s in suggestions if s['type'] == 'merge')} merges, "
                 f"{sum(1 for s in suggestions if s['type'] == 'absorb')} absorbs)")

        return suggestions

    def _load_yaml_index(self) -> list:
        """Try to import YAML index from chat.py."""
        try:
            from backend.routes.chat import _YAML_INDEX
            return _YAML_INDEX
        except ImportError:
            log.debug("Cannot import _YAML_INDEX for overlap detection")
            return []

    def _save_suggestions(self, suggestions: list):
        """Save suggestions to JSONL log."""
        if not suggestions:
            return
        try:
            self._data_dir.mkdir(parents=True, exist_ok=True)
            with open(self._log_path, "a", encoding="utf-8") as f:
                for s in suggestions:
                    f.write(json.dumps(s, ensure_ascii=False) + "\n")
        except Exception as e:
            log.warning(f"Failed to save agent mutations: {e}")

    @property
    def top_suggestions(self) -> list[dict]:
        """Return top 3 most impactful suggestions."""
        return self._suggestions[:3]

    @property
    def stats(self) -> dict:
        return {
            "total_suggestions": len(self._suggestions),
            "merges": sum(1 for s in self._suggestions if s["type"] == "merge"),
            "absorbs": sum(1 for s in self._suggestions if s["type"] == "absorb"),
        }

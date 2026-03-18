"""Night mode learning controller — extracted from HiveMind v0.9.0."""
import asyncio
import json
import logging
import os
import random
import time
from pathlib import Path

# v2.0: Autonomy night learning pipeline (optional)
_NIGHT_PIPELINE_AVAILABLE = False
try:
    from waggledance.core.learning.night_learning_pipeline import NightLearningPipeline
    from waggledance.core.learning.morning_report import MorningReportBuilder
    _NIGHT_PIPELINE_AVAILABLE = True
except ImportError:
    pass

log = logging.getLogger("hivemind")


class NightModeController:
    """Handles night mode detection, learning cycles, and progress persistence.

    Uses __getattr__/__setattr__ to proxy attribute access to the parent
    HiveMind instance, so method bodies work without modification.
    """

    def __init__(self, hive):
        object.__setattr__(self, 'hive', hive)

    def __getattr__(self, name):
        """Proxy attribute reads to HiveMind."""
        return getattr(self.hive, name)

    def __setattr__(self, name, value):
        """Proxy attribute writes to HiveMind."""
        if name == 'hive':
            object.__setattr__(self, name, value)
        else:
            setattr(self.hive, name, value)

    def _check_night_mode(self) -> bool:
        """Check if night mode should be active: user idle >30min, <8h."""
        _nm_cfg = self.config.get("night_mode", {})
        if not _nm_cfg.get("enabled", True):
            return False
        idle_threshold = _nm_cfg.get("idle_threshold_min", 30) * 60
        max_hours = _nm_cfg.get("max_hours", 8)
        idle = time.monotonic() - self._last_user_chat_time
        if idle < idle_threshold:
            if self._night_mode_active:
                self._night_mode_active = False
                self._emit_morning_report()
                log.info("🌙 Night mode OFF (user returned)")
            return False
        if self._night_mode_active:
            elapsed_h = (time.monotonic() - self._night_mode_start) / 3600
            if elapsed_h >= max_hours:
                self._night_mode_active = False
                self._emit_morning_report()
                log.info(f"🌙 Night mode OFF (max {max_hours}h reached)")
                return False
        return True

    def _emit_morning_report(self):
        """Generate and store morning report when night mode ends."""
        # v2.0: Autonomy morning report
        _rt_cfg = self.config.get("runtime", {})
        if (_rt_cfg.get("primary") == "waggledance"
                and not _rt_cfg.get("compatibility_mode", True)
                and _NIGHT_PIPELINE_AVAILABLE):
            _nlp = getattr(self, '_night_pipeline', None)
            _last = _nlp.last_result() if _nlp else None
            if _last:
                try:
                    # Use the report already built by the pipeline if available
                    if _last.report:
                        morning = _last.report
                    else:
                        builder = MorningReportBuilder(
                            profile=self.config.get("profile", "DEFAULT"))
                        morning = builder.build(
                            cases=[],
                            training_results=[],
                            canary_results=_last.canary_results,
                        )
                    report = morning.to_dict()
                    reports_path = Path("data/morning_reports.jsonl")
                    reports_path.parent.mkdir(parents=True, exist_ok=True)
                    with open(reports_path, "a", encoding="utf-8") as f:
                        f.write(json.dumps(report, ensure_ascii=False) + "\n")
                    log.info("🌅 Autonomy morning report: %d cases, %d models trained",
                             morning.total_cases, morning.models_trained)
                    return
                except Exception as e:
                    log.warning("Autonomy morning report failed: %s", e)

        if not self.night_enricher:
            return
        try:
            report = self.night_enricher.generate_morning_report()
            # Send via WebSocket
            import asyncio
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(self._notify_ws(
                    "morning_report", report))
            except RuntimeError:
                pass  # No running event loop — skip WS notification

            # Append to morning reports log
            import json
            reports_path = Path("data/morning_reports.jsonl")
            reports_path.parent.mkdir(parents=True, exist_ok=True)
            with open(reports_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(report, ensure_ascii=False) + "\n")
            log.info(
                f"🌅 Morning report: {report['total_stored']} stored / "
                f"{report['total_checked']} checked "
                f"({report['session_duration_min']:.0f} min)")
        except Exception as e:
            log.error(f"Morning report error: {e}")

    def _get_night_mode_interval(self) -> float:
        """Get heartbeat interval during night mode (10-15s)."""
        import random
        _nm_cfg = self.config.get("night_mode", {})
        base = _nm_cfg.get("interval_s", 10)
        return base + random.uniform(0, 5)

    def _init_learning_engines(self, al_cfg: dict):
        """Lazy-initialize all learning engines (enrichment + Phase 9 layers)."""
        # NightEnricher orchestrator (unified source management)
        ne_cfg = al_cfg.get("night_enricher", {})
        if (not getattr(self, 'night_enricher', None)
                and ne_cfg.get("enabled", True)
                and self.llm_heartbeat and self.llm):
            try:
                from core.night_enricher import NightEnricher
                self.night_enricher = NightEnricher(
                    self.consciousness, self.llm_heartbeat,
                    self.llm, self.config)
                # Fixed during autonomous session 2026-03-08 — wire TrustEngine to NightEnricher
                _te = getattr(self, '_trust_engine', None)
                if _te:
                    self.night_enricher._trust_engine = _te
                log.info("🌙 NightEnricher initialized")
            except Exception as e:
                log.warning(f"NightEnricher init failed: {e}")

        # Layer 2: Enrichment engine (legacy fallback)
        if (not self.enrichment
                and al_cfg.get("enrichment_enabled", True)
                and self.llm_heartbeat and self.llm):
            try:
                from core.fast_memory import FactEnrichmentEngine
                self.enrichment = FactEnrichmentEngine(
                    self.consciousness, self.llm_heartbeat, self.llm)
                log.info("✨ FactEnrichmentEngine initialized")
            except Exception as e:
                log.warning(f"FactEnrichmentEngine init failed: {e}")

        # Layer 3: Web learning agent
        if (not self.web_learner
                and al_cfg.get("web_learning_enabled", True)
                and self.llm_heartbeat and self.llm):
            try:
                from core.web_learner import WebLearningAgent
                budget = al_cfg.get("web_learning_daily_budget", 50)
                self.web_learner = WebLearningAgent(
                    self.consciousness, self.llm_heartbeat, self.llm,
                    daily_budget=budget)
                log.info("🌐 WebLearningAgent initialized")
            except Exception as e:
                log.warning(f"WebLearningAgent init failed: {e}")

        # Layer 4: Knowledge distiller
        if (not self.distiller
                and al_cfg.get("distillation_enabled", False)):
            try:
                from core.knowledge_distiller import KnowledgeDistiller
                self.distiller = KnowledgeDistiller(
                    self.consciousness,
                    api_key=os.environ.get("WAGGLEDANCE_DISTILLATION_API_KEY", "") or al_cfg.get("distillation_api_key", ""),
                    model=al_cfg.get("distillation_model",
                                     "claude-haiku-4-5-20251001"),
                    weekly_budget_eur=al_cfg.get(
                        "distillation_weekly_budget_eur", 5.0))
                log.info("🧠 KnowledgeDistiller initialized")
            except Exception as e:
                log.warning(f"KnowledgeDistiller init failed: {e}")

        # Layer 5: Meta-learning engine
        if (not self.meta_learning
                and al_cfg.get("meta_learning_enabled", True)):
            try:
                from core.meta_learning import MetaLearningEngine
                self.meta_learning = MetaLearningEngine(
                    self.consciousness,
                    agent_levels=self.agent_levels,
                    enrichment=self.enrichment,
                    web_learner=self.web_learner,
                    distiller=self.distiller)
                log.info("📊 MetaLearningEngine initialized")
            except Exception as e:
                log.warning(f"MetaLearningEngine init failed: {e}")

        # Layer 6: Code self-review
        if (not self.code_reviewer
                and al_cfg.get("code_review_enabled", True)
                and self.llm):
            try:
                from core.code_reviewer import CodeSelfReview
                self.code_reviewer = CodeSelfReview(
                    self.consciousness, self.llm,
                    meta_learning=self.meta_learning)
                log.info("🔍 CodeSelfReview initialized")
            except Exception as e:
                log.warning(f"CodeSelfReview init failed: {e}")

        # Phase 10: Micro-model orchestrator
        if (not getattr(self, 'micro_model', None)
                and al_cfg.get("micro_model_enabled", True)):
            try:
                from core.training_collector import TrainingDataCollector
                from core.micro_model import MicroModelOrchestrator
                self.training_collector = TrainingDataCollector(
                    self.consciousness)
                self.micro_model = MicroModelOrchestrator(
                    self.consciousness, self.training_collector)
                # Wire into consciousness for router access
                self.consciousness.micro_model = self.micro_model
                log.info("🤖 MicroModelOrchestrator initialized")
            except Exception as e:
                log.warning(f"MicroModel init failed: {e}")

    async def _night_learning_cycle(self):
        """Night mode: guided task, enrichment, web learning, distillation.

        v2.0: When runtime.primary=waggledance, delegates to NightLearningPipeline
        which uses case trajectories instead of Q&A pairs.

        Legacy 5-way rotation:
          %5 == 0,1: guided task
          %5 == 2: enrichment (Layer 2)
          %5 == 3: web learning (Layer 3)
          %5 == 4: distillation (Layer 4, if available) — else guided task

        Plus weekly meta-learning (Layer 5) and monthly code review (Layer 6).
        """
        if not self.consciousness:
            return

        # v2.0: Autonomy night learning pipeline
        _rt_cfg = self.config.get("runtime", {})
        if (_rt_cfg.get("primary") == "waggledance"
                and not _rt_cfg.get("compatibility_mode", True)
                and _NIGHT_PIPELINE_AVAILABLE):
            try:
                _nlp = getattr(self, '_night_pipeline', None)
                if _nlp is None:
                    _nlp = NightLearningPipeline(
                        profile=self.config.get("profile", "DEFAULT"))
                    self._night_pipeline = _nlp
                result = _nlp.run_cycle()
                self._night_mode_facts_learned += result.cases_graded
                log.info(
                    "🌙 Autonomy night cycle: %d cases graded, %d models trained "
                    "(%.1fs)", result.cases_graded, result.models_trained,
                    result.duration_s)
                await self._notify_ws("night_learning", {
                    "source": "autonomy_pipeline",
                    "cases_graded": result.cases_graded,
                    "models_trained": result.models_trained,
                    "procedures_learned": result.procedures_learned,
                    "facts_learned": self._night_mode_facts_learned,
                })
                # Still run periodic checks (meta-learning, code review)
                await self._run_periodic_checks()
                return
            except Exception as _nlp_err:
                log.warning("Autonomy night pipeline failed, legacy fallback: %s",
                            _nlp_err)

        # Lazy init all learning engines
        _al_cfg = self.config.get("advanced_learning", {})
        self._init_learning_engines(_al_cfg)

        # D1: Inject real external sources into NightEnricher after init
        if self.night_enricher:
            try:
                # Lazy init rss_monitor if not yet done
                if (not getattr(self, 'rss_monitor', None)
                        and _al_cfg.get("rss_enabled", True)):
                    from integrations.rss_feed import RSSFeedMonitor
                    rss_cfg = self.config.get("feeds", {}).get("rss", {})
                    feeds = rss_cfg.get("feeds", [])
                    if feeds:
                        self.rss_monitor = RSSFeedMonitor({"feeds": feeds})
                        log.info("📰 RSSFeedMonitor initialized")
                    else:
                        self.rss_monitor = None
            except Exception as e:
                log.warning(f"RSSFeedMonitor init: {e}")
                self.rss_monitor = None

            # Wire external sources into NightEnricher
            self.night_enricher.set_external_sources(
                web_learner=self.web_learner,
                distiller=self.distiller,
                rss_monitor=getattr(self, 'rss_monitor', None),
            )

        # ── v1.18.0: Wire ActiveLearningScorer into NightEnricher ──
        if self.night_enricher and not getattr(self.night_enricher, '_active_scorer', None):
            try:
                from core.active_learning import ActiveLearningScorer
                # Build topic counts from gap scheduler's fact data
                _topic_counts = {}
                if hasattr(self.night_enricher, 'gap_scheduler'):
                    _topic_counts = dict(
                        getattr(self.night_enricher.gap_scheduler, '_fact_counts', {}))
                self.night_enricher._active_scorer = ActiveLearningScorer(
                    topic_counts=_topic_counts)
                log.info("🎯 ActiveLearningScorer wired into NightEnricher")
            except Exception as e:
                log.warning("ActiveLearningScorer init failed: %s", e)

        # ── v1.18.0: Wire LearningLedger into NightEnricher ──
        if self.night_enricher and not getattr(self.night_enricher, '_ledger', None):
            try:
                from core.learning_ledger import LearningLedger
                self.night_enricher._ledger = LearningLedger()
                log.info("📋 LearningLedger wired into NightEnricher")
            except Exception as e:
                log.warning("LearningLedger init failed: %s", e)

        # ── NightEnricher unified path (replaces 5-way rotation) ──
        if self.night_enricher:
            try:
                stored = await self.night_enricher.enrichment_cycle(
                    self.throttle)
                if stored:
                    self._night_mode_facts_learned += stored
                    log.info(
                        f"🌙 Night enrichment: +{stored} facts "
                        f"(total {self._night_mode_facts_learned})")
                    if hasattr(self, 'structured_logger') and self.structured_logger:
                        self.structured_logger.log_learning(
                            event="night_enrichment",
                            count=stored,
                            duration_ms=0,
                            source="night_enricher")
                    await self._notify_ws("enrichment", {
                        "facts_stored": stored,
                        "total_stored": self.night_enricher.stats[
                            "total_stored"],
                        "facts_learned": self._night_mode_facts_learned,
                        "source": "night_enricher",
                    })
                    self._save_learning_progress()

                # Run meta-learning and code review checks (every cycle)
                # These were previously only in the legacy path below
                await self._run_periodic_checks()

                return
            except Exception as e:
                log.error(f"NightEnricher cycle error: {e}")

        # ── Legacy fallback: 5-way rotation ──
        cycle_mod = self._night_mode_facts_learned % 5

        if cycle_mod == 2 and self.enrichment:
            try:
                stored = await self.enrichment.enrichment_cycle(self.throttle)
                if stored:
                    self._night_mode_facts_learned += stored
                    await self._notify_ws("enrichment", {
                        "facts_stored": stored,
                        "total_enriched": self.enrichment.stats["validated"],
                        "facts_learned": self._night_mode_facts_learned,
                    })
                return
            except Exception as e:
                log.error(f"Enrichment cycle error: {e}")

        if cycle_mod == 3 and self.web_learner:
            try:
                stored = await self.web_learner.web_learning_cycle(
                    self.throttle)
                if stored:
                    self._night_mode_facts_learned += stored
                    await self._notify_ws("web_learning", {
                        "facts_stored": stored,
                        "total_web": self.web_learner.stats["facts_stored"],
                        "searches_today": self.web_learner.stats["searches_today"],
                        "facts_learned": self._night_mode_facts_learned,
                    })
                return
            except Exception as e:
                log.error(f"Web learning cycle error: {e}")

        if cycle_mod == 4 and self.distiller:
            try:
                stored = await self.distiller.distillation_cycle(
                    self.throttle)
                if stored:
                    self._night_mode_facts_learned += stored
                    await self._notify_ws("distillation", {
                        "facts_stored": stored,
                        "total_distilled": self.distiller.stats["facts_stored"],
                        "cost_eur": self.distiller.stats["week_cost_eur"],
                        "facts_learned": self._night_mode_facts_learned,
                    })
                return
            except Exception as e:
                log.error(f"Distillation cycle error: {e}")

        # ── Guided task (slots 0, 1, and fallthrough) ──
        if not self.consciousness.task_queue:
            return
        try:
            task = self.consciousness.task_queue.next_task()
            if not task:
                return

            _hb = self.llm_heartbeat
            _prompt = (f"LEARNING TASK: {task['prompt']}\n\n"
                       f"Provide a factual, concise answer (2-3 sentences) "
                       f"about Finnish beekeeping. Answer in English.")
            async with self.throttle:
                _resp = await _hb.generate(_prompt, max_tokens=200)
            answer = _resp.content if _resp and not _resp.error else ""

            if answer and self._is_valid_response(answer):
                self.consciousness.learn(
                    answer, agent_id="night_learner",
                    source_type="night_learning",
                    confidence=0.6, validated=False,
                    metadata={"task_type": task["type"],
                              "topic": task.get("topic", "")[:200]})
                self._night_mode_facts_learned += 1

                await self._notify_ws("night_learning", {
                    "task_type": task["type"],
                    "topic": task.get("topic", ""),
                    "facts_learned": self._night_mode_facts_learned,
                })
        except Exception as e:
            log.error(f"Night learning error: {e}")

        # Run periodic checks (meta-learning, code review, micro-model)
        await self._run_periodic_checks()

    async def _run_periodic_checks(self):
        """Run meta-learning, code review, and micro-model checks.

        Called from both NightEnricher path and legacy fallback path.
        """
        # Layer 5: Weekly meta-learning
        if self.meta_learning and self.meta_learning.is_due():
            try:
                report = await self.meta_learning.weekly_analysis()
                applied = await self.meta_learning.auto_apply_safe_optimizations(
                    report.get("suggestions", []))
                try:
                    self.meta_learning.generate_weekly_report()
                except Exception as _we:
                    log.debug(f"weekly_report.json write failed: {_we}")
                await self._notify_ws("meta_report", {
                    "suggestions": len(report.get("suggestions", [])),
                    "optimizations_applied": applied,
                    "memory_stats": report.get("memory_stats", {}),
                    "weakest_areas": report.get("weakest_areas", []),
                    "chat_metrics": report.get("chat_metrics", {}),
                })
            except Exception as e:
                log.error(f"Meta-learning error: {e}")

        # Layer 6: Monthly code review
        if self.code_reviewer and self.code_reviewer.is_due():
            try:
                suggestions = await self.code_reviewer.monthly_code_review(
                    self.throttle)
                if suggestions:
                    await self._notify_ws("code_suggestion", {
                        "new_suggestions": len(suggestions),
                        "total_pending": len(
                            self.code_reviewer.get_pending_suggestions()),
                    })
            except Exception as e:
                log.error(f"Code review error: {e}")

        # Phase 10: Micro-model training
        if (self.micro_model
                and self.micro_model.is_training_due(
                    self._night_mode_facts_learned)):
            try:
                await self.micro_model.maybe_train(
                    self._night_mode_facts_learned, self.throttle)
                await self._notify_ws("micro_training", {
                    "stats": self.micro_model.stats,
                })
            except Exception as e:
                log.error(f"Micro-model training error: {e}")

    def _load_persisted_facts_count(self) -> int:
        """Load persisted night mode facts counter from learning_progress.json."""
        try:
            progress_path = Path("data/learning_progress.json")
            if progress_path.exists():
                data = json.loads(progress_path.read_text(encoding="utf-8"))
                count = data.get("night_mode_facts_learned", 0)
                if isinstance(count, int) and count > 0:
                    log.info(f"Loaded persisted facts counter: {count}")
                    return count
        except Exception:
            pass
        return 0

    def _save_learning_progress(self):
        """Save night mode learning progress to file."""
        try:
            ne_total = (self.night_enricher._total_stored
                        if getattr(self, 'night_enricher', None) else 0)
            progress = {
                "night_mode_facts_learned": self._night_mode_facts_learned,
                "enricher_session_stored": ne_total,
                "last_save": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "night_mode_active": self._night_mode_active,
            }
            Path("data").mkdir(exist_ok=True)
            tmp_path = "data/learning_progress.json.tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(progress, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, "data/learning_progress.json")
        except Exception:
            pass

    async def _maybe_weekly_report(self):
        """Check if weekly report is due and generate it (runs from heartbeat)."""
        try:
            if not self.meta_learning:
                _al_cfg = self.config.get("advanced_learning", {})
                self._init_learning_engines(_al_cfg)
            if self.meta_learning and self.meta_learning.is_due():
                log.info("📊 Weekly report triggered from heartbeat")
                report = await self.meta_learning.weekly_analysis()
                applied = await self.meta_learning.auto_apply_safe_optimizations(
                    report.get("suggestions", []))
                try:
                    self.meta_learning.generate_weekly_report()
                except Exception as _we:
                    log.debug(f"weekly_report.json write failed: {_we}")
                await self._notify_ws("meta_report", {
                    "suggestions": len(report.get("suggestions", [])),
                    "optimizations_applied": applied,
                    "memory_stats": report.get("memory_stats", {}),
                    "weakest_areas": report.get("weakest_areas", []),
                    "chat_metrics": report.get("chat_metrics", {}),
                })
        except Exception as e:
            log.error(f"Weekly report heartbeat error: {e}")

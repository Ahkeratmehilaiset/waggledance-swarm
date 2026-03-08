"""
WaggleDance Swarm AI — Swarm Queen v0.0.2
===========================================
Jani Korpi (Ahkerat Mehiläiset)
Claude 4.6 • v0.0.2 • Built: 2026-02-22 18:00 EET

Keskusagentti joka orkesteroi kaikkea.

v0.0.2 MUUTOKSET:
  FIX-1: Prompt-restore bugi korjattu (_enriched_prompt context manager)
  FIX-2: Swarm Scheduler kytketty oikeasti chat()-reititykseen
  FIX-3: Feature flag: swarm.enabled (fallback vanhaan reititykseen)
  FIX-4: Auto-register hook spawnerille
  FIX-5: Backward-compat: get_knowledge() wrapper, vanhat importit toimivat

Aiemmat korjaukset (v0.0.1):
  K4:  Pesämäärä yhtenäistetty (202, ei 300)
  K5:  Painotettu reititys (primääri/sekundääri)
  K7:  idle_research rajoitettu (max 2 agenttia, ei 5)
  K8:  Tyhjien virhevastausten käsittely
  K9:  Reititys: kontekstuaalinen scoring
  K10: Vastausten validointi ennen muistiin tallennusta
"""

import asyncio
import hashlib
import json
import logging
import os
import time
import yaml
from contextlib import contextmanager
from datetime import datetime
from typing import Optional
from pathlib import Path
from core.adaptive_throttle import AdaptiveThrottle
from core.ops_agent import OpsAgent
from core.learning_engine import LearningEngine
from core.swarm_scheduler import SwarmScheduler
from core.elastic_scaler import ElasticScaler

from agents.base_agent import Agent
from agents.spawner import AgentSpawner
from core.llm_provider import LLMProvider
from core.token_economy import TokenEconomy
from core.live_monitor import LiveMonitor
from core.whisper_protocol import WhisperProtocol
from core.knowledge_loader import KnowledgeLoader
from memory.shared_memory import SharedMemory

# ── Extracted modules (Phase 5 refactor) ────────────────────
from core.hive_routing import (
    WEIGHTED_ROUTING, PRIMARY_WEIGHT, SECONDARY_WEIGHT,
    MASTER_NEGATIVE_KEYWORDS, DATE_HALLUCINATION_RULE, AGENT_EN_PROMPTS,
)
from core.hive_support import PriorityLock, StructuredLogger

# ═══ Translation Proxy — Voikko + sanakirja FI↔EN ═══
try:
    from core.translation_proxy import TranslationProxy, detect_language, is_finnish
    _TRANSLATION_AVAILABLE = True
except ImportError:
    _TRANSLATION_AVAILABLE = False
    def detect_language(t): return "fi"
    def is_finnish(t): return True

log = logging.getLogger("hivemind")

try:
    from core.en_validator import ENValidator
    _EN_VALIDATOR_AVAILABLE = True
except ImportError:
    _EN_VALIDATOR_AVAILABLE = False



# Routing constants and support classes imported from extracted modules:
# - core.hive_routing: WEIGHTED_ROUTING, PRIMARY_WEIGHT, SECONDARY_WEIGHT,
#   MASTER_NEGATIVE_KEYWORDS, DATE_HALLUCINATION_RULE, AGENT_EN_PROMPTS
# - core.hive_support: PriorityLock, StructuredLogger


# PriorityLock and StructuredLogger imported from core.hive_support


class HiveMind:
    def __init__(self, config_path: str = "configs/settings.yaml"):
        self.config_path = config_path
        self.config = self._load_config()

        self.memory: Optional[SharedMemory] = None
        self.llm: Optional[LLMProvider] = None
        self.llm_heartbeat: Optional[LLMProvider] = None
        self.token_economy: Optional[TokenEconomy] = None
        self.monitor: Optional[LiveMonitor] = None
        self.whisper: Optional[WhisperProtocol] = None
        self.spawner: Optional[AgentSpawner] = None
        self.master_agent: Optional[Agent] = None
        self.knowledge: Optional[KnowledgeLoader] = None
        self.knowledge_loader: Optional[KnowledgeLoader] = None
        self.scheduler: Optional[SwarmScheduler] = None
        self.throttle: Optional[AdaptiveThrottle] = None
        self.ops_agent: Optional[OpsAgent] = None
        self.learning: Optional[LearningEngine] = None

        # ── Chat-prioriteetti ────────────────────────────────
        # Kun käyttäjä lähettää viestin, heartbeat pysähtyy
        # hetkeksi ettei mallinvaihto (7b↔32b) aiheuta 60s viivettä.
        # PHASE1 TASK3: PriorityLock (single source of truth)
        self.priority = PriorityLock()

        # ── Feature flags (v0.0.2) ──────────────────────────
        swarm_cfg = self.config.get("swarm", {})
        self._swarm_enabled = swarm_cfg.get("enabled", False)

        self.running = False
        self._background_tasks: set = set()  # M5: track all fire-and-forget tasks
        self._heartbeat_count = 0
        self.translation_proxy = None
        self.en_validator = None
        self.language_mode = "auto"  # "auto", "fi", "en"
        self.heartbeat_task: Optional[asyncio.Task] = None
        self.started_at: Optional[datetime] = None
        self._ws_callbacks: list = []

        # ── Phase 3: Social Learning ───────────────────────
        self.agent_levels = None           # AgentLevelManager
        self._last_user_chat_time = time.monotonic()  # prime at init so night mode can activate without chat
        self._night_mode_active = False
        self._night_mode_start = 0.0
        self._night_mode_facts_learned = self._load_persisted_facts_count()

        # ── Phase 4: Advanced Learning ────────────────────
        self._last_chat_message = ""       # for correction detection
        self._last_chat_response = ""      # for correction detection
        self._last_chat_method = ""        # "active_learning" etc
        self._last_chat_agent_id = ""      # which agent answered
        self._last_episode_id = None       # episodic chain

        # ── Phase 4k: Fact Enrichment Engine ─────────────
        self.enrichment = None  # Lazy init when night mode starts
        self.night_enricher = None  # NightEnricher orchestrator (replaces rotation)

        # ── Phase 9: Autonomous Learning Layers 3-6 ────
        self.web_learner = None
        self.distiller = None
        self.meta_learning = None
        self.code_reviewer = None
        self._meta_learning_last_run = 0.0
        self._code_review_last_run = 0.0

        # ── Phase 10: Micro-Model Training ────────────
        self.micro_model = None
        self.training_collector = None

        # ── Phase 8: External Data Feeds ────────────────
        self.data_feeds = None
        self.rss_monitor = None  # D1: lazy init in _night_learning_cycle

        # ── Phase 5: Smart Home Sensors ────────────────
        self.sensor_hub = None

        # ── Phase 7: Voice Interface ──────────────────
        self.voice_interface = None

        # ── Phase 11: Elastic Scaler ──────────────────
        self.elastic_scaler = None

        # ── C5: Structured logging ─────────────────────
        self.metrics = StructuredLogger()

    def _load_config(self) -> dict:
        path = Path(self.config_path)
        if path.exists():
            with open(path, encoding="utf-8") as f:
                raw = yaml.safe_load(f)
            try:
                from core.settings_validator import validate_settings
                validate_settings(raw)
            except Exception as e:
                log.error(f"Settings validation failed: {e}")
                raise
            return raw
        return {}

    def _get_date_prefix(self) -> str:
        """Dynamic date + season + location + hallucination prevention."""
        now = datetime.now()
        _weekdays = ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"]
        date_str = f"{_weekdays[now.weekday()]} {now.strftime('%Y-%m-%d %H:%M')}"
        month = now.month
        if 3 <= month <= 5:
            season = "spring"
        elif 6 <= month <= 8:
            season = "summer"
        elif 9 <= month <= 11:
            season = "autumn"
        else:
            season = "winter"
        # Location from settings
        _profile = self.config.get("profile", "cottage")
        _locations = self.config.get("feeds", {}).get("profile_locations", {}).get(_profile, [])
        _loc_str = f" Location: {', '.join(_locations)}, Finland." if _locations else ""
        # Rich seasonal context from SeasonalGuard
        try:
            from core.seasonal_guard import get_seasonal_guard
            _seasonal = " " + get_seasonal_guard().queen_context()
        except Exception:
            _seasonal = ""
        return (f"Today is {date_str}. Season: {season}.{_loc_str}{_seasonal} "
                f"{DATE_HALLUCINATION_RULE}")

    # ── FIX-1: Prompt enrichment context manager ─────────────
    # Vanha koodi tallensi _orig_prompt ja _orig_kb erikseen, ja
    # finally-lohkossa _orig_kb ylikirjoitti _orig_prompt:n.
    # Nyt kaikki injektiot tehdään yhden context managerin sisällä,
    # ja alkuperäinen prompt palautetaan AINA oikein.

    @contextmanager
    def _enriched_prompt(self, agent, inject_date=True, inject_knowledge=True,
                         knowledge_max_chars=2000):
        """
        Context manager joka:
        1. Tallentaa agentin ALKUPERÄISEN system_promptin
        2. Injektoi päivämäärän (valinnainen)
        3. Injektoi tietopankin (valinnainen)
        4. Palauttaa AINA alkuperäisen riippumatta virheistä

        Käyttö:
            with self._enriched_prompt(agent):
                response = await agent.think(message, context)
        """
        original_prompt = agent.system_prompt
        try:
            if inject_date:
                agent.system_prompt = self._get_date_prefix() + agent.system_prompt
            if inject_knowledge and self.knowledge_loader:
                agent_type = getattr(agent, 'agent_type', getattr(agent, 'type', ''))
                if agent_type:
                    kb = self.knowledge_loader.get_knowledge_summary(agent_type)
                    if kb:
                        agent.system_prompt = agent.system_prompt + "\n" + kb[:knowledge_max_chars]
            yield agent
        finally:
            agent.system_prompt = original_prompt

    async def _readiness_check(self):
        """C4: Validate critical services at startup."""
        print("  -- Readiness check --", flush=True)
        issues = []

        # 1. Ollama API
        try:
            import requests
            r = requests.get("http://localhost:11434/api/version", timeout=5)
            if r.status_code == 200:
                ver = r.json().get("version", "?")
                print(f"  [OK] Ollama API v{ver}", flush=True)
            else:
                issues.append(f"Ollama API HTTP {r.status_code}")
        except Exception as e:
            issues.append(f"Ollama API: {type(e).__name__} (onko Ollama käynnissä?)")

        # 2. Required models
        required_models = {
            "chat": self.config.get("llm", {}).get("model", "phi4-mini"),
            "heartbeat": self.config.get("llm_heartbeat", self.config.get("llm", {})).get("model", "llama3.2:1b"),
        }
        try:
            import requests
            r = requests.get("http://localhost:11434/api/tags", timeout=5)
            if r.status_code == 200:
                installed = {m["name"].split(":")[0]
                             for m in r.json().get("models", [])}
                installed_full = {m["name"]
                                  for m in r.json().get("models", [])}
                for role, model in required_models.items():
                    base = model.split(":")[0]
                    if model in installed_full or base in installed:
                        print(f"  [OK] Model {model} ({role})", flush=True)
                    else:
                        issues.append(f"Model {model} ({role}) not installed")
                # Embedding models
                for emb_model in ["nomic-embed-text", "all-minilm"]:
                    base = emb_model.split(":")[0]
                    if emb_model in installed_full or base in installed:
                        print(f"  [OK] Embed model {emb_model}", flush=True)
                    else:
                        issues.append(f"Embed model {emb_model} not installed")
        except Exception:
            pass  # Already caught above

        # 3. ChromaDB
        try:
            import chromadb
            db_path = self.config.get("consciousness", {}).get("db_path", "data/chroma_db")
            client = chromadb.PersistentClient(path=db_path)
            col = client.get_or_create_collection("waggle_memory")
            count = col.count()
            print(f"  [OK] ChromaDB ({count} facts in {db_path})", flush=True)
        except Exception as e:
            issues.append(f"ChromaDB: {type(e).__name__}: {e}")

        # 4. Data directories
        from pathlib import Path
        for d in ["data", "configs", "knowledge", "agents"]:
            if Path(d).exists():
                pass
            else:
                issues.append(f"Directory missing: {d}/")

        # Report
        if issues:
            print(f"  -- {len(issues)} issue(s) found --", flush=True)
            for issue in issues:
                print(f"  [!!] {issue}", flush=True)
            print("  -- System will start with degraded functionality --", flush=True)
        else:
            print("  -- All checks passed --", flush=True)
        return issues

    async def start(self):
        print("🐝 WaggleDance Swarm AI käynnistyy...", flush=True)

        # C4: Readiness check
        startup_issues = await self._readiness_check()

        db_path = self.config.get("memory", {}).get("db_path", "data/waggle_dance.db")
        self.memory = SharedMemory(db_path)
        await self.memory.initialize()
        print("  ✅ Muisti alustettu", flush=True)

        self.llm = LLMProvider(self.config.get("llm", {}))
        print(f"  ✅ LLM (chat): {self.llm.model} [GPU]", flush=True)
        hb_config = self.config.get("llm_heartbeat", self.config.get("llm", {}))
        self.llm_heartbeat = LLMProvider(hb_config)
        self.throttle = AdaptiveThrottle()
        hb_device = "CPU" if self.llm_heartbeat.num_gpu == 0 else "GPU"
        print(f"  ✅ LLM (heartbeat): {self.llm_heartbeat.model} [{hb_device}]", flush=True)

        # ── KRIITTINEN: Benchmark Ollama → kalibroi throttle ──
        # Ilman tätä semafori = None ja kaikki kutsut menevät
        # rajoittamatta → Ollama-jono → timeout-kaskadi
        try:
            machine_class = await self.throttle.benchmark(self.llm_heartbeat)
            print(f"  ✅ Throttle kalibroitu: {machine_class.upper()}", flush=True)
        except Exception as e:
            print(f"  ⚠️  Benchmark epäonnistui ({e}), käytetään oletuksia")
            # Aseta semafori manuaalisesti jos benchmark epäonnistuu
            self.throttle._semaphore = asyncio.Semaphore(1)
            self.throttle.state.max_concurrent = 1
            self.throttle.state.heartbeat_interval = 120
            self.throttle.state.machine_class = "unknown_safe"

        self.token_economy = TokenEconomy(self.memory)
        await self.token_economy.initialize()
        print("  ✅ Token-talous alustettu", flush=True)

        self.monitor = LiveMonitor()
        await self.monitor.system("WaggleDance käynnistyy...")
        print("  ✅ Live Monitor käynnissä", flush=True)

        self.whisper = WhisperProtocol(self.memory, self.token_economy, self.monitor)
        await self.whisper.initialize()
        print("  ✅ Dance Language (Whisper) käynnissä", flush=True)

        self.knowledge = KnowledgeLoader("knowledge")
        knowledge_map = self.knowledge.list_all_knowledge()
        total_files = sum(len(files) for files in knowledge_map.values())
        print(f"  ✅ Knowledge Loader ({total_files} dokumenttia)", flush=True)
        self.knowledge_loader = KnowledgeLoader()

        # ── Swarm Scheduler ──────────────────────────────────
        swarm_config = self.config.get("swarm", {})
        self.scheduler = SwarmScheduler(swarm_config)
        self._swarm_enabled = swarm_config.get("enabled", False)
        if self._swarm_enabled:
            print("  ✅ Swarm Scheduler ENABLED", flush=True)
        else:
            print("  ℹ️  Swarm Scheduler DISABLED (swarm.enabled: false)", flush=True)

        # ── OpsAgent (järjestelmävalvonta) ────────────────────
        self.ops_agent = OpsAgent(
            throttle=self.throttle,
            llm_chat=self.llm,
            llm_heartbeat=self.llm_heartbeat,
            config=self.config,
        )
        await self.ops_agent.start()

        # ── LearningEngine (oppimissilmukka) ─────────────────
        self.learning = LearningEngine(
            llm_evaluator=self.llm_heartbeat,   # 7b CPU — ei kilpaile GPU:sta
            memory=self.memory,
            scheduler=self.scheduler,
            token_economy=self.token_economy,
            config=self.config,
        )
        await self.learning.start()

        self.spawner = AgentSpawner(self.llm, self.memory, self.config,
                                     token_economy=self.token_economy,
                                     monitor=self.monitor)
        print("  ✅ Spawner valmis", flush=True)


        # ── Faktat configista (KORJAUS K4) ──────────────────
        facts = self.config.get("facts", {})
        colony_count = facts.get("colony_count", 202)
        y_tunnus = facts.get("y_tunnus", "2828492-2")
        evira = facts.get("evira", "18533284")

        self.master_agent = Agent(
            name="Swarm Queen",
            agent_type="hivemind",
            system_prompt=self._get_date_prefix() + f"""CRITICAL FACTS (ALWAYS use):
- Jani Korpi, JKH Service (Business ID: {y_tunnus}), Evira: {evira}
- {colony_count} colonies, {facts.get('apiary_count', 35)} apiary locations (2024)
- Breeds: italMeh (Italian), grnMeh (Carniolan/Carnica)
- Regions: Tuusula (36), Helsinki (20), Vantaa (16), Espoo (66), Polvijärvi (3), Kouvola (61)
- Karhuniementie 562 D (70% business / 30% personal)

RESPONSE RULES:
- Answer ONLY in English, ONLY what is asked
- Owner is Jani (NOT Janina, NOT Janne)
- NEVER invent numbers or dates — say "I don't know exactly" if unsure
- Max 5 sentences, get straight to the point
- Do NOT start with "Alright". Answer directly.

DELEGATION RULES (IMPORTANT):
- You are Swarm Queen: ROUTER, NOT an expert.
- You do NOT analyze content, draw conclusions or add your own knowledge.
- If question is about bees/varroa/hive → delegate to beekeeper.
- If question is about weather/temperature → delegate to meteorologist.
- If you don't know the answer, say "I don't know" — do NOT guess.
- Delegate to specialists ALWAYS when possible. Be brief and concrete.""",
            llm=self.llm,
            memory=self.memory,
            skills=["orchestration", "planning", "routing"],
            monitor=self.monitor
        )
        await self.master_agent.initialize()
        print("  ✅ Swarm Queen käynnissä", flush=True)

        # ═══ Translation Proxy ═══
        if _TRANSLATION_AVAILABLE:
            try:
                self.translation_proxy = TranslationProxy()
                _tp = self.translation_proxy
                _v = "✅" if _tp.voikko.available else "❌"
                print(f"  ✅ Translation Proxy (Voikko={_v}, Dict={len(_tp.dict_fi_en)}, Lang=auto)", flush=True)
            except Exception as e:
                print(f"  ⚠️  Translation Proxy: {e}", flush=True)
                self.translation_proxy = None
        else:
            print("  ℹ️  Translation Proxy ei saatavilla")
            self.translation_proxy = None

        # YAML agent prompts → always English (LLM works internally in English)
        if self.translation_proxy:
            if hasattr(self, 'spawner') and self.spawner and hasattr(self.spawner, 'yaml_bridge'):
                self.spawner.yaml_bridge.set_translation_proxy(
                    self.translation_proxy, "en")

        # ═══ EN Validator (WordNet + domain synonyms) ═══
        if _EN_VALIDATOR_AVAILABLE:
            try:
                _domain = set(self.translation_proxy.dict_en_fi.keys()) if self.translation_proxy else set()
                self.en_validator = ENValidator(domain_terms=_domain)
                _wn = "✅" if self.en_validator.wordnet.available else "❌"
                print(f"  ✅ EN Validator (WordNet={_wn}, Synonyms={len(self.en_validator.domain_synonyms)})", flush=True)
            except Exception as e:
                print(f"  ⚠️  EN Validator: {e}", flush=True)
                self.en_validator = None
        else:
            print("  ℹ️  EN Validator ei saatavilla (pip install nltk)", flush=True)
            self.en_validator = None

        # ── Tietoisuuskerros v2 ──
        print("  ⏳ Consciousness alustetaan...", flush=True)
        try:
            from core.memory_engine import Consciousness
            _CONSCIOUSNESS_OK = True
        except ImportError:
            _CONSCIOUSNESS_OK = False
        if _CONSCIOUSNESS_OK:
            try:
                _ollama_url = self.config.get('ollama', {}).get('base_url', 'http://localhost:11434')
                self.consciousness = Consciousness(
                    db_path='data/chroma_db',
                    ollama_url=_ollama_url,
                    translation_proxy=self.translation_proxy
                )
                print(f'  ✅ Tietoisuus v2 (muisti={self.consciousness.memory.count}, '
                      f'embed={self.consciousness.embed.available}, '
                      f'eval_embed={self.consciousness.eval_embed.available})', flush=True)

                # CRITICAL: nomic-embed-text must be available
                if not self.consciousness.embed.available:
                    raise RuntimeError(
                        "CRITICAL: nomic-embed-text not available! "
                        "Ensure Ollama is running and model is pulled: "
                        "ollama pull nomic-embed-text"
                    )

                # PHASE2: Batch benchmark
                try:
                    await self.throttle.benchmark_batch(
                        consciousness=self.consciousness,
                        translation_proxy=self.translation_proxy
                    )
                    print(f"  ✅ Batch benchmark valmis", flush=True)
                except Exception as e:
                    print(f"  ⚠️  Batch benchmark: {e}", flush=True)

                # MAGMA Layer 3: Wire audit trail
                try:
                    from core.audit_log import AuditLog
                    from core.replay_store import ReplayStore
                    self._audit_log = AuditLog("data/audit_log.db")
                    self._replay_store = ReplayStore("data/replay_store.jsonl")
                    self.consciousness.wire_audit(self._audit_log, self._replay_store)
                    print("  ✅ MAGMA audit wired", flush=True)
                except Exception as e:
                    print(f"  ⚠️  MAGMA audit: {e}", flush=True)

                # MAGMA Layer 4: Cross-agent memory sharing
                try:
                    from core.agent_channels import ChannelRegistry
                    from core.provenance import ProvenanceTracker
                    from core.cross_agent_search import CrossAgentSearch
                    from core.memory_overlay import OverlayRegistry
                    self._channel_registry = ChannelRegistry()
                    self._provenance = ProvenanceTracker(self._audit_log)
                    _oreg = getattr(self, '_overlay_registry', None)
                    if not _oreg:
                        _oreg = OverlayRegistry(self.consciousness.memory)
                        self._overlay_registry = _oreg
                    self._cross_search = CrossAgentSearch(
                        self.consciousness, _oreg,
                        self._channel_registry, self._provenance)
                    self._channel_registry.auto_create_role_channels()
                    from core.memory_overlay import BranchManager
                    self._branch_manager = BranchManager()
                    print("  ✅ MAGMA Layer 4 wired", flush=True)
                except Exception as e:
                    print(f"  ⚠️  MAGMA Layer 4: {e}", flush=True)

                # MAGMA Layer 5: Trust & Reputation Engine
                try:
                    from core.trust_engine import TrustEngine
                    _al = getattr(self, 'agent_levels', None)
                    _prov = getattr(self, '_provenance', None)
                    self._trust_engine = TrustEngine(
                        self._audit_log, provenance=_prov, agent_levels=_al)
                    print("  ✅ MAGMA Layer 5 wired", flush=True)
                except Exception as e:
                    print(f"  ⚠️  MAGMA Layer 5: {e}", flush=True)

                # MAGMA: Cognitive Graph
                try:
                    from core.cognitive_graph import CognitiveGraph
                    self._cognitive_graph = CognitiveGraph("data/cognitive_graph.json")
                    self.consciousness.wire_graph(self._cognitive_graph)
                    print("  ✅ MAGMA Cognitive Graph wired", flush=True)
                except Exception as e:
                    print(f"  ⚠️  MAGMA Cognitive Graph: {e}", flush=True)

                # Phase 3: Init task queue
                self.consciousness.init_task_queue()

                # Phase 3: Init AgentLevelManager
                try:
                    from core.agent_levels import AgentLevelManager
                    _al_cfg = self.config.get("agent_levels", {})
                    if _al_cfg.get("enabled", True):
                        self.agent_levels = AgentLevelManager(db_path='data/chroma_db')
                        print(f"  ✅ Agent Levels (Phase 3)", flush=True)
                except Exception as e:
                    print(f"  ⚠️  Agent Levels: {e}", flush=True)

                # PHASE1 TASK6: Auto-seed on first startup
                if self.consciousness and self.consciousness.memory.count < 100:
                    print("  🌱 First startup — seeding knowledge base...", flush=True)
                    try:
                        from tools.scan_knowledge import scan_all
                        count = scan_all(self.consciousness)
                        print(f"  ✅ Seeded {count} facts from knowledge base", flush=True)
                    except Exception as e:
                        print(f"  ⚠️  Auto-seed failed: {e}", flush=True)

            except Exception as e:
                print(f'  ⚠️  Tietoisuus: {e}', flush=True)
                self.consciousness = None

        # ── Phase 8: External Data Feeds ────────────────────
        feeds_cfg = self.config.get("feeds", {})
        if feeds_cfg.get("enabled", False):
            try:
                # Resolve weather locations from active profile
                active_profile = self.config.get("profile", "cottage")
                profile_locs = feeds_cfg.get("profile_locations", {})
                locations = profile_locs.get(active_profile, [])
                if locations:
                    weather_cfg = feeds_cfg.get("weather", {})
                    weather_cfg["locations"] = locations
                    feeds_cfg["weather"] = weather_cfg
                    print(f"  📍 Weather locations ({active_profile}): {', '.join(locations)}", flush=True)
                elif active_profile == "gadget":
                    feeds_cfg.setdefault("weather", {})["enabled"] = False
                    print(f"  ℹ️  Weather disabled for gadget profile", flush=True)

                from integrations.data_scheduler import DataFeedScheduler
                self.data_feeds = DataFeedScheduler(
                    config=feeds_cfg,
                    consciousness=self.consciousness,
                    priority_lock=self.priority)
                await self.data_feeds.start()
                active = list(self.data_feeds._feeds.keys())
                print(f"  ✅ Data Feeds ({', '.join(active)})", flush=True)
            except Exception as e:
                print(f"  ⚠️  Data Feeds: {e}", flush=True)
                self.data_feeds = None
        else:
            print("  ℹ️  Data Feeds DISABLED (feeds.enabled: false)", flush=True)

        # ── Phase 5: Smart Home Sensors ──────────────────────────
        try:
            from integrations.sensor_hub import SensorHub
            sensor_cfg = {
                "mqtt": self.config.get("mqtt", {}),
                "home_assistant": self.config.get("home_assistant", {}),
                "frigate": self.config.get("frigate", {}),
                "alerts": self.config.get("alerts", {}),
            }
            any_enabled = any(
                sensor_cfg[k].get("enabled", False)
                for k in ("mqtt", "home_assistant", "frigate", "alerts")
            )
            if any_enabled:
                self.sensor_hub = SensorHub(
                    config=sensor_cfg,
                    consciousness=self.consciousness,
                    loop=asyncio.get_event_loop(),
                )
                await self.sensor_hub.start()
                print("  ✅ SensorHub (smart home)", flush=True)
            else:
                print("  ℹ️  SensorHub DISABLED (no sensors enabled)", flush=True)
        except Exception as e:
            print(f"  ⚠️  SensorHub: {e}", flush=True)
            self.sensor_hub = None

        # ── Phase 7: Voice Interface ─────────────────────────
        try:
            voice_cfg = self.config.get("voice", {})
            if voice_cfg.get("enabled", False):
                from integrations.voice_interface import VoiceInterface
                self.voice_interface = VoiceInterface(self.config)

                async def _voice_chat(msg: str) -> str:
                    r = await self._do_chat(msg, source="voice")
                    return r if isinstance(r, str) else str(r)

                await self.voice_interface.initialize(
                    chat_fn=_voice_chat,
                    ws_callback=self._broadcast if hasattr(self, '_broadcast') else None,
                )
                print("  ✅ VoiceInterface (STT+TTS)", flush=True)
            else:
                print("  ℹ️  VoiceInterface DISABLED (voice.enabled: false)", flush=True)
        except Exception as e:
            print(f"  ⚠️  VoiceInterface: {e}", flush=True)
            self.voice_interface = None

        # ── Phase 11: ElasticScaler ────────────────────────────
        try:
            self.elastic_scaler = ElasticScaler()
            tier = self.elastic_scaler.detect()
            print(f"  ✅ ElasticScaler: {tier.tier} tier (VRAM={tier.hardware.gpu_vram_gb:.1f}GB)", flush=True)
        except Exception as e:
            print(f"  ⚠️  ElasticScaler: {e}", flush=True)
            self.elastic_scaler = None

        # ── Improvement 5: Cache Warming at startup ───────────
        try:
            self._warm_caches()
        except Exception as e:
            print(f"  ⚠️  Cache warming: {e}", flush=True)

        self.running = True
        self.started_at = datetime.now()
        self.heartbeat_task = asyncio.create_task(self._heartbeat_loop())

        agents_dict = (self.spawner.active_agents
                       if hasattr(self, "spawner") and self.spawner else {})
        self._whisper_task = asyncio.create_task(self._whisper_cycle(agents_dict))

        await self.memory.log_event("hivemind", "started", "WaggleDance käynnistyi")
        print("🟢 WaggleDance Swarm AI käynnissä!", flush=True)

        return self

    def _track_task(self, coro) -> asyncio.Task:
        """Create and track a background task for graceful shutdown."""
        task = asyncio.create_task(coro)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        return task

    async def stop(self):
        log.info("Sammutetaan WaggleDance...")
        self.running = False

        if self.heartbeat_task:
            self.heartbeat_task.cancel()
            try:
                await self.heartbeat_task
            except asyncio.CancelledError:
                pass

        if self.ops_agent:
            await self.ops_agent.stop()

        if self.learning:
            await self.learning.stop()

        if self.spawner:
            await self.spawner.kill_all()

        if self.memory:
            await self.memory.log_event("hivemind", "stopped", "WaggleDance sammutettu")
            await self.memory.close()

        # PHASE2: Flush remaining learn queue items before shutdown
        if hasattr(self, 'consciousness') and self.consciousness:
            try:
                flushed = self.consciousness.flush()
                if flushed:
                    log.info(f"Consciousness flush: {flushed} facts stored")
            except Exception as e:
                log.warning(f"Consciousness flush: {e}")

        # Phase 9: Save code review suggestions
        if hasattr(self, 'code_reviewer') and self.code_reviewer:
            try:
                self.code_reviewer._save_suggestions()
            except Exception:
                pass

        # Phase 7: Clear voice interface
        if hasattr(self, 'voice_interface') and self.voice_interface:
            self.voice_interface = None

        # Phase 5: Stop sensor hub
        if hasattr(self, 'sensor_hub') and self.sensor_hub:
            await self.sensor_hub.stop()

        # Phase 8: Stop data feeds
        if hasattr(self, 'data_feeds') and self.data_feeds:
            await self.data_feeds.stop()

        # M5: Cancel all tracked background tasks
        if self._background_tasks:
            for task in list(self._background_tasks):
                task.cancel()
            await asyncio.gather(*self._background_tasks, return_exceptions=True)
            self._background_tasks.clear()

        if self.translation_proxy:
            self.translation_proxy.close()
            log.info("Translation Proxy suljettu")
        log.info("WaggleDance sammutettu.")

    # ══════════════════════════════════════════════════════════
    # FIX-4: Auto-register hook — kutsutaan kun agentti luodaan
    # ══════════════════════════════════════════════════════════

    def register_agent_to_scheduler(self, agent):
        """
        Rekisteröi agentti Swarm Scheduleriin.

        Kutsutaan:
          - main.py:n spawn-loopissa
          - spawner.py:n hookista (jos haluat myöhemmin)
          - tai suoraan: hivemind.register_agent_to_scheduler(agent)

        Ei vaadi muutoksia agents/-kansioon.
        """
        if not self.scheduler:
            return
        agent_id = getattr(agent, 'id', None)
        agent_type = getattr(agent, 'agent_type', getattr(agent, 'type', ''))
        if not agent_id or not agent_type:
            return

        # Skills: yritä lukea agentista, fallback YAMLBridgeen
        skills = getattr(agent, 'skills', [])
        tags = []

        # Lisää YAML-peräiset routing-avainsanat tageiksi
        if self.spawner and hasattr(self.spawner, 'yaml_bridge'):
            routing = self.spawner.yaml_bridge.get_routing_rules()
            tags = routing.get(agent_type, [])

        self.scheduler.register_agent(
            agent_id=agent_id,
            agent_type=agent_type,
            skills=skills,
            tags=tags,
        )

    def bulk_register_agents_to_scheduler(self):
        """
        Rekisteröi KAIKKI spawnerilta tiedetyt agentit scheduleriin.
        Kutsutaan esim. startupin jälkeen.
        """
        if not self.scheduler or not self.spawner:
            return 0
        count = 0
        for agent in self.spawner.active_agents.values():
            self.register_agent_to_scheduler(agent)
            count += 1
        return count

    # ══════════════════════════════════════════════════════════
    # FIX-2 + FIX-3: Swarm-aware chat() routing
    # ══════════════════════════════════════════════════════════

    async def chat(self, message: str, language: str = "auto") -> str:
        """
        Reititä käyttäjän viesti oikealle agentille.

        CHAT-PRIORITEETTI: Pysäyttää heartbeat-LLM:n chatin ajaksi
        ettei mallinvaihto (7b↔32b) aiheuta 60-120s viivettä.
        """
        # ── Chat-prioriteetti: pysäytä heartbeat ──────────
        self._last_user_chat_time = time.monotonic()
        # PHASE1 TASK3: PriorityLock blocks background workers
        # Cooldown 3s chatin jälkeen (phi4-mini + llama1b ovat molemmat
        # KEEP_ALIVE=24h eli pysyvät ladattuina — ei mallinvaihtoa)
        await self.priority.chat_enters(cooldown_s=3.0)

        try:
            return await self._do_chat(message, language=language)
        finally:
            await self.priority.chat_exits()

    async def _do_chat(self, message: str, language: str = "auto") -> str:
        """Varsinainen chat-logiikka. Tukee FI↔EN käännöstä: auto/fi/en."""
        _chat_t0 = time.perf_counter()
        _original_message = message
        self._translation_used = False
        self._fi_en_result = None
        self._detected_lang = language

        # ═══ Kielentunnistus ═══
        if language == "auto":
            self._detected_lang = detect_language(message) if _TRANSLATION_AVAILABLE else "fi"

        # ═══ Phase 4: Detect user correction ("ei", "väärin", correction text) ═══
        _CORRECTION_WORDS = {"ei", "väärin", "wrong", "väärä", "virhe",
                             "korjaus", "eikä", "ei ole", "tarkoitin"}
        _CORRECTION_PHRASES = {"ei vaan", "oikea vastaus", "tarkoitin että",
                               "ei vaan ", "väärä vastaus"}
        if (self._last_chat_message and self._last_chat_response
                and hasattr(self, 'consciousness') and self.consciousness):
            msg_lower = message.lower()
            msg_words = set(msg_lower.split())
            _has_correction_word = bool(msg_words & _CORRECTION_WORDS)
            _has_correction_phrase = any(p in msg_lower for p in _CORRECTION_PHRASES)
            if (_has_correction_word or _has_correction_phrase) and len(message) > 5:
                self.consciousness.store_correction(
                    query=self._last_chat_message,
                    bad_answer=self._last_chat_response,
                    good_answer=message,
                    agent_id=self._last_chat_agent_id or "unknown")
                # Penalize agent trust
                if self.agent_levels and self._last_chat_agent_id:
                    try:
                        self.agent_levels.record_response(
                            agent_id=self._last_chat_agent_id,
                            agent_type="unknown",
                            was_correct=False, was_hallucination=False,
                            was_corrected=True)
                    except Exception:
                        pass
                if self.monitor:
                    await self.monitor.system("📝 Korjaus tallennettu — opin virheestä!")
                await self._notify_ws("correction_stored", {
                    "query": self._last_chat_message[:100],
                    "good_answer": message[:100],
                })
                response = "Kiitos korjauksesta! Opin virheestä ja muistan tämän jatkossa."
                self._last_chat_message = message
                self._last_chat_response = response
                self._last_chat_method = ""
                self.metrics.log_chat(
                    query=_original_message, method="correction",
                    agent_id=self._last_chat_agent_id or "user",
                    response_time_ms=(time.perf_counter() - _chat_t0) * 1000,
                    route="correction", language=self._detected_lang)
                return response

        # ═══ Phase 4: Detect user teaching (after active_learning response) ═══
        if (self._last_chat_method == "active_learning"
                and hasattr(self, 'consciousness') and self.consciousness
                and self.consciousness.detect_user_teaching(
                    message, self._last_chat_method)):
            self.consciousness.learn_from_user(message, self._last_chat_message)
            if self.monitor:
                await self.monitor.system(f"🎓 Opittu käyttäjältä: {message[:60]}")
            await self._notify_ws("user_teaching", {
                "query": self._last_chat_message[:100],
                "teaching": message[:100],
            })
            response = f"Kiitos! Opin juuri: {message[:100]}. Muistan tämän jatkossa."
            self._last_chat_message = message
            self._last_chat_response = response
            self._last_chat_method = ""
            self.metrics.log_chat(
                query=_original_message, method="user_teaching",
                agent_id="user", confidence=0.9,
                response_time_ms=(time.perf_counter() - _chat_t0) * 1000,
                route="user_teaching", language=self._detected_lang)
            return response

        # ═══ Direct datetime answers (no LLM needed) ═══
        _dt_now = datetime.now()
        _msg_l = message.lower()
        _TIME_WORDS = {"kello", "aika", "time", "clock", "kellonaika", "paljonko kello",
                       "what time", "current time", "mikä kello"}
        _DATE_WORDS = {"päivä", "päivämäärä", "date", "today", "tänään", "mikä päivä",
                       "what day", "what date", "viikonpäivä", "weekday"}
        _is_time_q = any(w in _msg_l for w in _TIME_WORDS)
        _is_date_q = any(w in _msg_l for w in _DATE_WORDS)
        if _is_time_q or _is_date_q:
            _weekdays_fi = ["maanantai", "tiistai", "keskiviikko", "torstai",
                           "perjantai", "lauantai", "sunnuntai"]
            _weekday_fi = _weekdays_fi[_dt_now.weekday()]
            _time_str = _dt_now.strftime("%H.%M")  # Finnish format: 14.30
            _date_str = f"{_dt_now.day}.{_dt_now.month}.{_dt_now.year}"  # Finnish: 8.3.2026
            if _is_time_q and _is_date_q:
                response = f"Tänään on {_weekday_fi} {_date_str}, kello on {_time_str}."
            elif _is_time_q:
                response = f"Kello on {_time_str}."
            else:
                response = f"Tänään on {_weekday_fi} {_date_str}."
            self._last_chat_message = message
            self._last_chat_response = response
            self._last_chat_method = "datetime_direct"
            self.metrics.log_chat(
                query=_original_message, method="datetime_direct",
                agent_id="system", confidence=1.0,
                response_time_ms=(time.perf_counter() - _chat_t0) * 1000,
                route="datetime_direct", language=self._detected_lang)
            if self.monitor:
                await self.monitor.system(f"🕐 Aikakysely: {response}")
            await self._notify_ws("chat_response", {
                "message": message, "response": response,
                "language": self._detected_lang, "method": "datetime_direct"
            })
            return response

        # ═══ PHASE1 TASK4: Smart Router — confidence-based model selection ═══
        _pre = None
        if self.consciousness:
            try:
                _pre = self.consciousness.before_llm(message)
            except Exception as e:
                log.error(f"before_llm failed: {type(e).__name__}: {e}")
                _pre = None
            if _pre and _pre.handled:
                if self.monitor:
                    await self.monitor.system(
                        f"🧠 {_pre.method}: {_pre.answer[:80]}")
                await self._notify_ws("chat_response", {
                    "message": message, "response": _pre.answer,
                    "language": self._detected_lang,
                    "method": _pre.method
                })
                # Phase 4: track for correction detection + episode
                self._last_chat_message = message
                self._last_chat_response = _pre.answer
                self._last_chat_method = _pre.method
                self._last_chat_agent_id = "consciousness"
                if self.consciousness:
                    self._last_episode_id = self.consciousness.store_episode(
                        query=message, response=_pre.answer,
                        prev_episode_id=self._last_episode_id,
                        quality=_pre.confidence)
                self.metrics.log_chat(
                    query=_original_message, method=_pre.method,
                    agent_id="consciousness", confidence=_pre.confidence,
                    response_time_ms=(time.perf_counter() - _chat_t0) * 1000,
                    cache_hit=(_pre.method in ("hot_cache", "math")),
                    route=_pre.method, language=self._detected_lang)
                return _pre.answer

            # Tier 2: memory_fast → llama1b formats answer with context
            if _pre and _pre.method == "memory_fast" and _pre.context and self.llm_heartbeat:
                _ans_lang = "Finnish" if self._detected_lang == "fi" else "English"
                _fast_prompt = f"{_pre.context}\n\nQuestion: {message}\nAnswer concisely in {_ans_lang}:"
                try:
                    async with self.throttle:
                        _resp = await self.llm_heartbeat.generate(
                            _fast_prompt, max_tokens=200)
                    if _resp and not _resp.error and _resp.content:
                        response = _resp.content
                        if self.monitor:
                            await self.monitor.system(
                                f"🧠 SmartRouter: llama1b + context ({_pre.confidence:.0%})")
                        await self._notify_ws("chat_response", {
                            "message": message, "response": response,
                            "language": self._detected_lang,
                            "method": "smart_router_fast"
                        })
                        # C1: auto-populate HotCache
                        self._populate_hot_cache(
                            _original_message, response,
                            score=_pre.confidence, source="memory_fast")
                        # Phase 4: track for correction detection + episode
                        self._last_chat_message = message
                        self._last_chat_response = response
                        self._last_chat_method = "memory_fast"
                        self._last_chat_agent_id = "llama1b"
                        if self.consciousness:
                            self._last_episode_id = self.consciousness.store_episode(
                                query=message, response=response,
                                prev_episode_id=self._last_episode_id,
                                quality=_pre.confidence)
                        self.metrics.log_chat(
                            query=_original_message, method="memory_fast",
                            agent_id="llama1b", model_used="llama3.2:1b",
                            confidence=_pre.confidence,
                            response_time_ms=(time.perf_counter() - _chat_t0) * 1000,
                            route="smart_router_fast",
                            language=self._detected_lang)
                        return response
                except Exception:
                    pass  # Fall through to normal routing

        # ═══ FI→EN käännös (~2ms) ═══
        if self._detected_lang == "fi" and self.translation_proxy:
            try:
                self._fi_en_result = self.translation_proxy.fi_to_en(message, force_opus=True)
                if self._fi_en_result.coverage >= 0.5 and self._fi_en_result.method != "passthrough":
                    self._translation_used = True
                    _en_message = self._fi_en_result.text
                    if self.monitor:
                        await self.monitor.system(
                            f"🔄 FI→EN ({self._fi_en_result.method}, "
                            f"{self._fi_en_result.latency_ms:.1f}ms, "
                            f"{self._fi_en_result.coverage:.0%}): {_en_message[:80]}")
                else:
                    _en_message = message
            except Exception as e:
                log.error(f"FI->EN translation failed: {type(e).__name__}: {e}")
                _en_message = message
        else:
            _en_message = message

        # Viesti agentille
        self._routed_message = _en_message if (self._translation_used or self._detected_lang == "en") else message
        self._use_en_prompts = self._translation_used or self._detected_lang == "en"

        await self.memory.store_memory(
            content=f"Käyttäjä sanoi: {message}",
            agent_id="user",
            memory_type="observation",
            importance=0.6
        )

        context = await self.memory.get_full_context(_original_message)
        msg_lower = _original_message.lower()  # Reititys aina FI-sanoilla

        # Multi-agent check (sama kuin ennen)
        multi_keywords = ["kaikki", "tilanne", "yhteenveto", "status", "yleiskatsaus"]
        is_multi = any(w in msg_lower for w in multi_keywords)
        if is_multi:
            return await self._multi_agent_collaboration(message, {}, context)

        # ── Routing rules (YAMLBridge + fallback) ─────────────
        if self.spawner and hasattr(self.spawner, 'yaml_bridge'):
            routing_rules = self.spawner.yaml_bridge.get_routing_rules()
        else:
            routing_rules = {}
        routing_rules.setdefault("hacker", ["bugi", "refaktor", "koodi", "tietoturva"])
        routing_rules.setdefault("oracle", ["haku", "etsi", "tutki", "google", "claude"])

        # ── Reititysvalinta ───────────────────────────────────
        if self._swarm_enabled and self.scheduler and self.scheduler.agent_count > 0:
            delegate_to, best_score = self._swarm_route(msg_lower, routing_rules)
        else:
            delegate_to, best_score = self._legacy_route(msg_lower, routing_rules)

        if delegate_to and best_score > 0:
            response = await self._delegate_to_agent(
                delegate_to, self._routed_message, context, msg_lower
            )
            # C1: auto-populate HotCache
            self._populate_hot_cache(
                _original_message, response,
                score=0.75, source=f"agent_{delegate_to}")
            # Phase 4: track for correction detection + episode
            self._last_chat_message = message
            self._last_chat_response = response
            self._last_chat_method = (_pre.method
                                      if _pre else "")
            self._last_chat_agent_id = delegate_to
            if hasattr(self, 'consciousness') and self.consciousness:
                self._last_episode_id = self.consciousness.store_episode(
                    query=message, response=response,
                    prev_episode_id=self._last_episode_id)
            self.metrics.log_chat(
                query=_original_message, method="delegate",
                agent_id=delegate_to,
                confidence=_pre.confidence if _pre else 0.0,
                response_time_ms=(time.perf_counter() - _chat_t0) * 1000,
                route="agent_delegate", language=self._detected_lang,
                translated=self._translation_used)
            return response
        else:
            # AUDIT FIX: Negatiiviset avainsanat → pakota delegointi
            # Jos viesti sisältää spesialistin termejä, ÄLÄ anna masterille
            for neg_kw in MASTER_NEGATIVE_KEYWORDS:
                if neg_kw in msg_lower:
                    # Yritä löytää spesialisti negatiivisella avainsanalla
                    for agent_type, keywords in routing_rules.items():
                        if neg_kw in keywords or any(
                            neg_kw in kw for kw in keywords
                        ):
                            response = await self._delegate_to_agent(
                                agent_type, message, context, msg_lower
                            )
                            # C1: auto-populate HotCache
                            self._populate_hot_cache(
                                _original_message, response,
                                score=0.75, source=f"agent_{agent_type}")
                            # Phase 4: track
                            self._last_chat_message = message
                            self._last_chat_response = response
                            self._last_chat_method = (_pre.method
                                                      if _pre else "")
                            self._last_chat_agent_id = agent_type
                            if hasattr(self, 'consciousness') and self.consciousness:
                                self._last_episode_id = self.consciousness.store_episode(
                                    query=message, response=response,
                                    prev_episode_id=self._last_episode_id)
                            self.metrics.log_chat(
                                query=_original_message, method="neg_kw_delegate",
                                agent_id=agent_type,
                                confidence=_pre.confidence if _pre else 0.0,
                                response_time_ms=(time.perf_counter() - _chat_t0) * 1000,
                                route="agent_delegate",
                                language=self._detected_lang,
                                translated=self._translation_used)
                            return response
                    break  # Ei löytynyt → anna masterille

            # Fallback: Master (Swarm Queen)
            _orig_master_sys = None
            if self._use_en_prompts and "hivemind" in AGENT_EN_PROMPTS:
                _orig_master_sys = self.master_agent.system_prompt
                from datetime import datetime as _dt
                # Tietoisuus: muistikonteksti
                _consciousness_context = ""
                if self.consciousness:
                    _ctx_q = _en_message if self._translation_used else message
                    _consciousness_context = self.consciousness.get_context(_ctx_q)
                    if _consciousness_context:
                        _consciousness_context = "\n" + _consciousness_context
                    # Phase 4: inject corrections context
                    _corrections_ctx = self.consciousness.check_previous_corrections(message)
                    if _corrections_ctx:
                        _consciousness_context += (
                            "\n\nCORRECTIONS (avoid repeating these mistakes):\n"
                            + _corrections_ctx)
                self.master_agent.system_prompt = f"Date: {_dt.now():%Y-%m-%d %H:%M}. " + AGENT_EN_PROMPTS["hivemind"] + _consciousness_context
            try:
                with self._enriched_prompt(self.master_agent, knowledge_max_chars=2000):
                    response = await self.master_agent.think(self._routed_message, context)
            except Exception as e:
                log.error(f"Master agent think failed: {type(e).__name__}: {e}")
                response = "Anteeksi, en pystynyt vastaamaan juuri nyt. Yrita hetken kuluttua uudelleen."
            finally:
                if _orig_master_sys is not None:
                    self.master_agent.system_prompt = _orig_master_sys
            if self.en_validator and self._use_en_prompts:
                _val = self.en_validator.validate(response)
                if _val.was_corrected:
                    response = _val.corrected

            if self._translation_used and self.translation_proxy:
                try:
                    _en_fi = self.translation_proxy.en_to_fi(response, force_opus=True)
                    if _en_fi.method != "passthrough":
                        response = _en_fi.text
                except Exception as e:
                    log.error(f"EN->FI translation failed: {type(e).__name__}: {e}")
                    # Keep English response as fallback
            # Tietoisuus: hallusinaatio + oppiminen
            _hall = None
            _quality = 0.7
            if self.consciousness:
                try:
                    _hall = self.consciousness.check_hallucination(message, response)
                    if _hall.is_suspicious and self.monitor:
                        await self.monitor.system(f"⚠️ Hallusinaatio? {_hall.reason}")
                    _quality = _hall.relevance if not _hall.is_suspicious else 0.3
                    self.consciousness.learn_conversation(message, response, quality_score=_quality)
                except Exception as e:
                    log.debug(f"Hallucination check failed: {e}")

            # C1: auto-populate HotCache
            self._populate_hot_cache(
                _original_message, response,
                score=_quality, source="master")
            await self._notify_ws("chat_response", {
                "message": message, "response": response,
                "language": self._detected_lang, "translated": self._translation_used
            })
            # Phase 4: track for correction detection + episode
            self._last_chat_message = message
            self._last_chat_response = response
            self._last_chat_method = (_pre.method
                                      if _pre else "")
            self._last_chat_agent_id = "master"
            if hasattr(self, 'consciousness') and self.consciousness:
                self._last_episode_id = self.consciousness.store_episode(
                    query=message, response=response,
                    prev_episode_id=self._last_episode_id,
                    quality=_quality)
            self.metrics.log_chat(
                query=_original_message, method="master",
                agent_id="master", model_used="phi4-mini",
                confidence=_quality,
                was_hallucination=bool(_hall and _hall.is_suspicious),
                response_time_ms=(time.perf_counter() - _chat_t0) * 1000,
                route="llm_master", language=self._detected_lang,
                translated=self._translation_used)
            return response

    def _swarm_route(self, msg_lower: str, routing_rules: dict) -> tuple:
        """
        FIX-2: Swarm-aware routing pipeline.
        (A) Luo task meta → (B) Top-K shortlist → (C) keyword-score shortlistille
        (D) Fallback legacy-routingiin jos shortlist tyhjä.
        """
        # (A) Task meta: poimi sanat tageiksi
        task_tags = [w for w in msg_lower.split() if len(w) > 2]
        task_type = "user_question"

        # (B) Shortlist Top-K schedulerista (HALPA: ei LLM-kutsuja)
        candidates = self.scheduler.select_candidates(
            task_type=task_type,
            task_tags=task_tags,
            routing_rules=routing_rules,
            top_k=self.scheduler.top_k,
        )

        if not candidates:
            # Fallback: vanha reititys
            return self._legacy_route(msg_lower, routing_rules)

        # (C) Keyword-score VAIN shortlistatuille (ei kaikille 50:lle)
        candidate_types = set()
        for cid in candidates:
            score = self.scheduler._scores.get(cid)
            if score:
                candidate_types.add(score.agent_type)

        delegate_to = None
        best_score = 0
        for agent_type in candidate_types:
            keywords = routing_rules.get(agent_type, [])
            weighted = WEIGHTED_ROUTING.get(agent_type)
            if weighted:
                score = (
                    sum(PRIMARY_WEIGHT for kw in weighted.get("primary", [])
                        if kw in msg_lower) +
                    sum(SECONDARY_WEIGHT for kw in weighted.get("secondary", [])
                        if kw in msg_lower)
                )
            else:
                score = sum(1 for kw in keywords if kw in msg_lower)

            if score > best_score:
                best_score = score
                delegate_to = agent_type

        if delegate_to and best_score > 0:
            return delegate_to, best_score

        # (D) Shortlist ei tuottanut keyword-matcheja → fallback legacy
        return self._legacy_route(msg_lower, routing_rules)

    def _legacy_route(self, msg_lower: str, routing_rules: dict) -> tuple:
        """Vanha reititys: käy KAIKKI agentit läpi keyword-scorella."""
        delegate_to = None
        best_score = 0
        for agent_type, keywords in routing_rules.items():
            weighted = WEIGHTED_ROUTING.get(agent_type)
            if weighted:
                score = (
                    sum(PRIMARY_WEIGHT for kw in weighted.get("primary", [])
                        if kw in msg_lower) +
                    sum(SECONDARY_WEIGHT for kw in weighted.get("secondary", [])
                        if kw in msg_lower)
                )
            else:
                score = sum(1 for kw in keywords if kw in msg_lower)

            if score > best_score:
                best_score = score
                delegate_to = agent_type

        return delegate_to, best_score

    async def _delegate_to_agent(self, delegate_to: str, message: str,
                                  context: str, msg_lower: str) -> str:
        """Delegoi viesti valitulle agentille. FIX-1: prompt-restore korjattu."""
        agents = self.spawner.get_agents_by_type(delegate_to)
        if not agents:
            try:
                agent = await self.spawner.spawn(delegate_to)
                # FIX-4: auto-register scheduleriin
                if agent:
                    self.register_agent_to_scheduler(agent)
            except Exception:
                agent = None
        else:
            agent = agents[0]

        if not agent:
            return await self.master_agent.think(message, context)

        _t0 = time.monotonic()

        # ═══ EN-prompt jos käännös aktiivinen ═══
        _orig_agent_sys = None
        if getattr(self, '_use_en_prompts', False):
            _atype = getattr(agent, 'agent_type', getattr(agent, 'type', ''))
            if _atype in AGENT_EN_PROMPTS:
                _orig_agent_sys = agent.system_prompt
                from datetime import datetime as _dt
                agent.system_prompt = f"Date: {_dt.now():%Y-%m-%d %H:%M}. " + AGENT_EN_PROMPTS[_atype]

        # FIX-1: Yksi context manager hoitaa kaiken injektoinnin ja palautuksen
        with self._enriched_prompt(agent, inject_date=True,
                                    inject_knowledge=True,
                                    knowledge_max_chars=2000):
            # Phase 4: Failure Twin — inject agent-specific error warnings
            if hasattr(self, 'consciousness') and self.consciousness:
                _ft_warning = self.consciousness.get_agent_error_patterns(
                    agent_id=getattr(agent, 'id', ''),
                    query=message)
                if _ft_warning:
                    agent.system_prompt = agent.system_prompt + "\n\n" + _ft_warning

            try:
                # Merkitse task alkaneeksi schedulerille
                if self.scheduler:
                    self.scheduler.record_task_start(agent.id)
                async with self.throttle:
                    response = await agent.think(message, context)
            except Exception as e:
                response = f"[Virhe: {e}]"
            finally:
                if self.scheduler:
                    self.scheduler.record_task_end(agent.id)

        # Prompt on nyt VARMASTI palautunut alkuperäiseen ↑

        _elapsed = (time.monotonic() - _t0) * 1000
        self._report_llm_result(_elapsed, True, self.llm.model)

        # KORJAUS K10: Validoi vastaus ennen palkitsemista
        if self._is_valid_response(response):
            if self.token_economy:
                await self.token_economy.reward(agent.id, "question_answered")
            if self.scheduler:
                self.scheduler.record_task_result(
                    agent.id, success=True, latency_ms=_elapsed
                )
            # LearningEngine: arvioi chat-vastaus
            if self.learning:
                self.learning.submit_for_evaluation(
                    agent_id=agent.id,
                    agent_type=agent.agent_type,
                    system_prompt=getattr(agent, 'system_prompt', '')[:500],
                    prompt=message,
                    response=response,
                )
            # Phase 3: Record for agent levels
            if self.agent_levels:
                try:
                    self.agent_levels.record_response(
                        agent_id=agent.id,
                        agent_type=agent.agent_type,
                        was_correct=True,
                        was_hallucination=False)
                except Exception:
                    pass
        else:
            if self.scheduler:
                self.scheduler.record_task_result(
                    agent.id, success=False, latency_ms=_elapsed
                )

        # Palauta FI-prompt
        if _orig_agent_sys is not None:
            agent.system_prompt = _orig_agent_sys

        # ═══ EN Validator: standardisoi terminologia ═══
        if self.en_validator and getattr(self, '_use_en_prompts', False):
            _val = self.en_validator.validate(response)
            if _val.was_corrected:
                if self.monitor:
                    await self.monitor.system(
                        f"🔍 EN-fix ({_val.method}, {_val.latency_ms:.1f}ms, "
                        f"{_val.correction_count} korjausta): "
                        f"{_val.corrections[:3]}")
                response = _val.corrected

        # ═══ EN→FI käännös ═══
        if getattr(self, '_translation_used', False) and self.translation_proxy:
            _en_fi = self.translation_proxy.en_to_fi(response, force_opus=True)
            if _en_fi.method != "passthrough":
                if self.monitor:
                    _src_ms = getattr(self._fi_en_result, 'latency_ms', 0) if self._fi_en_result else 0
                    await self.monitor.system(
                        f"🔄 EN→FI ({_en_fi.method}, {_en_fi.latency_ms:.1f}ms, "
                        f"total: {_src_ms + _en_fi.latency_ms:.1f}ms)")
                response = _en_fi.text

        await self._notify_ws("delegated", {
            "agent": agent.name, "type": delegate_to, "response": response,
            "language": getattr(self, '_detected_lang', 'fi'),
            "translated": getattr(self, '_translation_used', False)
        })
        return f"[{agent.name}] {response}"

    # ── Vastauksen validointi (KORJAUS K10) ──────────────────

    def _is_valid_response(self, response: str) -> bool:
        """Tarkista onko vastaus kelvollinen ennen muistiin tallennusta."""
        if not response or not response.strip():
            return False
        if len(response.strip()) < 5:
            return False
        bad_markers = ["[LLM-virhe", "[Ollama ei vastaa", "error", "503"]
        for marker in bad_markers:
            if marker in response[:50]:
                return False
        return True

    def _populate_hot_cache(self, query: str, response: str,
                             score: float = 0.75, source: str = "chat"):
        """C1: Auto-populate HotCache with successful Finnish answers."""
        if not (self.consciousness and self.consciousness.hot_cache):
            return
        if not self._is_valid_response(response):
            return
        if self._detected_lang != "fi":
            return
        if score < 0.6:
            return
        try:
            self.consciousness.hot_cache.put(query, response, score, source=source)
        except Exception:
            pass

    def _get_disk_status(self) -> dict:
        """Get disk space status for get_status() response."""
        try:
            from core.disk_guard import get_disk_status
            return get_disk_status(".")
        except Exception:
            return {"free_mb": -1, "free_gb": -1, "total_gb": -1, "status": "unknown"}

    @staticmethod
    def _fix_mojibake(s: str) -> str:
        """Korjaa double-encoded UTF-8: PÃ¤Ã¤ → Pää."""
        if not s:
            return s
        # Only attempt fix if text contains actual mojibake patterns
        # (e.g. Ã¤ = double-encoded ä, Ã¶ = ö, Ã¼ = ü)
        _mojibake_markers = ("Ã¤", "Ã¶", "Ã¼", "Ã„", "Ã–", "Ãœ", "Ã¥", "Ã…")
        if not any(m in s for m in _mojibake_markers):
            return s
        try:
            return s.encode("latin-1").decode("utf-8")
        except (UnicodeDecodeError, UnicodeEncodeError):
            return s

    def _report_llm_result(self, latency_ms: float, success: bool,
                            model: str = ""):
        """Raportoi LLM-tulos sekä throttle:lle että OpsAgentille."""
        if success:
            self.throttle.record_success(latency_ms)
        else:
            self.throttle.record_error()
        if self.ops_agent:
            self.ops_agent.report_task_result(latency_ms, success, model)

    # ── Multi-agent ─────────────────────────────────────────

    async def _multi_agent_collaboration(self, mission: str,
                                          plan: dict, context: str) -> str:
        army = await self.spawner.spawn_dynamic_army(mission)
        if not army:
            return await self.master_agent.think(mission, context)

        tasks = await self.memory.get_tasks(status="pending")
        agent_tasks = {}
        for task in tasks:
            if task.get("assigned_agent"):
                agent_tasks.setdefault(task["assigned_agent"], []).append(task)

        async_tasks = []
        for agent in army:
            agent_specific_tasks = agent_tasks.get(agent.id, [])
            if agent_specific_tasks:
                for task in agent_specific_tasks:
                    async_tasks.append(agent.execute_task(task))
            else:
                async_tasks.append(agent.think(mission, context))

        results = []
        if async_tasks:
            completed = await asyncio.gather(*async_tasks, return_exceptions=True)
            results = [str(r) for r in completed if not isinstance(r, Exception)]

        all_results = "\n\n".join(results)
        synthesis = await self.master_agent.think(
            f"Missio: {mission}\n\nAgenttien tulokset:\n{all_results}\n\n"
            f"Syntetisoi kokonaisvastaus.",
            context
        )
        return synthesis

    # ── Knowledge injection (backward compat) ────────────────
    # Nämä säilytetään VANHAN KOODIN yhteensopivuutta varten.
    # Uusi koodi käyttää _enriched_prompt context manageria.

    def _inject_knowledge(self, agent, max_chars=2000):
        """
        LEGACY: Lisää agentin tietopankki system_promptiin.
        Palauttaa alkuperäisen promptin tai None.
        HUOM: Käytä mieluummin _enriched_prompt() context manageria.
        """
        if not self.knowledge_loader:
            return None
        agent_type = getattr(agent, 'agent_type', getattr(agent, 'type', ''))
        if not agent_type:
            return None
        knowledge = self.knowledge_loader.get_knowledge_summary(agent_type)
        if not knowledge:
            return None
        original = agent.system_prompt
        agent.system_prompt = original + "\n" + knowledge[:max_chars]
        return original

    def _restore_prompt(self, agent, original_prompt):
        """LEGACY: Palauta alkuperäinen prompt."""
        if original_prompt is not None:
            agent.system_prompt = original_prompt

    # ── Oracle ──────────────────────────────────────────────

    def get_oracle(self):
        if not self.spawner:
            return None
        agents = self.spawner.get_agents_by_type("oracle")
        return agents[0] if agents else None

    async def oracle_receive_answer(self, answer_text: str) -> dict:
        oracle = self.get_oracle()
        if oracle and hasattr(oracle, 'receive_answer'):
            result = await oracle.receive_answer(answer_text)
            await self._notify_ws("oracle_answer", result)
            return result
        return {"success": False, "error": "Oracle ei löydy"}

    def oracle_get_questions(self) -> str:
        oracle = self.get_oracle()
        if oracle and hasattr(oracle, 'get_questions_text'):
            return oracle.get_questions_text()
        return "Oracle ei aktiivinen."

    async def oracle_search(self, query: str) -> str:
        oracle = self.get_oracle()
        if oracle and hasattr(oracle, 'search_and_learn'):
            result = await oracle.search_and_learn(query)
            await self._notify_ws("oracle_search", {
                "query": query, "result": result[:300]
            })
            return result
        return "Oracle ei aktiivinen."

    # ── Projektinhallinta ───────────────────────────────────

    async def add_project(self, name: str, description: str = "",
                          tags: list[str] = None) -> str:
        project_id = await self.memory.add_project(name, description, tags)
        await self.memory.store_memory(
            content=f"Uusi projekti: {name}. {description}",
            agent_id="hivemind",
            project_id=project_id,
            memory_type="plan",
            importance=0.7
        )
        await self._notify_ws("project_added", {"name": name, "id": project_id})
        return project_id

    async def get_status(self) -> dict:
        agents = self.spawner.get_all_agents() if self.spawner else []

        # UTF-8 mojibake-korjaus agenttinimille
        # (Windows + YAML + spawner voi tuottaa double-encoded nimiä)
        for ag in agents:
            if "name" in ag and isinstance(ag["name"], str):
                ag["name"] = self._fix_mojibake(ag["name"])
        projects = await self.memory.get_projects() if self.memory else []
        pending_tasks = await self.memory.get_tasks(status="pending") if self.memory else []
        memory_stats = await self.memory.get_memory_stats() if self.memory else {}
        recent_events = await self.memory.get_timeline(limit=10) if self.memory else []

        oracle = self.get_oracle()
        oracle_info = {}
        if oracle:
            oracle_info = {
                "pending_questions": (
                    len(oracle.get_pending_questions())
                    if hasattr(oracle, 'get_pending_questions') else 0
                ),
                "searches": (
                    oracle.searches_performed
                    if hasattr(oracle, 'searches_performed') else 0
                ),
                "search_available": (
                    oracle._get_search() is not None
                    if hasattr(oracle, '_get_search') else False
                ),
            }

        throttle_status = (self.throttle.get_status()
                           if hasattr(self, 'throttle') and self.throttle else {})
        swarm_stats = (self.scheduler.get_stats()
                       if self.scheduler else {})
        swarm_stats["enabled"] = self._swarm_enabled

        return {
            "mode": "production",
            "status": "running" if self.running else "stopped",
            "uptime": (str(datetime.now() - self.started_at)
                       if self.started_at else "0"),
            "agents": {
                "total": len(agents),
                "active": sum(1 for a in agents if a["status"] != "idle"),
                "list": agents,
            },
            "projects": {
                "total": len(projects),
                "active": sum(1 for p in projects if p.get("status") == "active"),
                "list": projects,
            },
            "tasks": {"pending": len(pending_tasks), "list": pending_tasks[:10]},
            "memory": memory_stats,
            "recent_events": recent_events,
            "token_economy": {
                "leaderboard": (self.token_economy.get_leaderboard()
                                if self.token_economy else [])
            },
            "whisper_protocol": (
                await self.whisper.get_whisper_stats() if self.whisper else {}
            ),
            "oracle": oracle_info,
            "knowledge": self.knowledge.list_all_knowledge() if self.knowledge else {},
            "translation_proxy": {
                "available": self.translation_proxy is not None,
                "language_mode": self.language_mode,
                "voikko": self.translation_proxy.voikko.available if self.translation_proxy else False,
                "dict_size": len(self.translation_proxy.dict_fi_en) if self.translation_proxy else 0,
                "stats": self.translation_proxy.get_stats() if self.translation_proxy else {},
            },
            "en_validator": {
                "available": self.en_validator is not None,
                "wordnet": self.en_validator.wordnet.available if self.en_validator else False,
                "synonyms": len(self.en_validator.domain_synonyms) if self.en_validator else 0,
                "stats": self.en_validator.get_stats() if self.en_validator else {},
            },
            "throttle": throttle_status,
            "swarm": swarm_stats,
            "ops_agent": (self.ops_agent.get_status()
                          if self.ops_agent else {}),
            "learning": (self.learning.get_status()
                         if self.learning else {}),
            "agent_levels": (self.agent_levels.get_all_stats()
                             if self.agent_levels else {}),
            "night_mode": {
                "active": self._night_mode_active,
                "facts_learned": self._night_mode_facts_learned,
                "idle_seconds": (int(time.monotonic() - self._last_user_chat_time)
                                 if self._last_user_chat_time > 0 else 0),
            },
            "consciousness": (self.consciousness.stats
                              if hasattr(self, 'consciousness')
                              and self.consciousness else {}),
            "corrections_count": (self.consciousness.memory.corrections.count()
                                  if hasattr(self, 'consciousness')
                                  and self.consciousness else 0),
            "episodes_count": (self.consciousness.memory.episodes.count()
                               if hasattr(self, 'consciousness')
                               and self.consciousness else 0),
            "night_enricher": (self.night_enricher.stats
                              if hasattr(self, 'night_enricher')
                              and self.night_enricher else {}),
            "enrichment": (self.enrichment.stats
                           if hasattr(self, 'enrichment')
                           and self.enrichment else {}),
            "web_learner": (self.web_learner.stats
                            if hasattr(self, 'web_learner')
                            and self.web_learner else {}),
            "distiller": (self.distiller.stats
                          if hasattr(self, 'distiller')
                          and self.distiller else {}),
            "meta_learning": (self.meta_learning.stats
                              if hasattr(self, 'meta_learning')
                              and self.meta_learning else {}),
            "code_reviewer": (self.code_reviewer.stats
                              if hasattr(self, 'code_reviewer')
                              and self.code_reviewer else {}),
            "data_feeds": (self.data_feeds.get_status()
                           if hasattr(self, 'data_feeds')
                           and self.data_feeds else {}),
            "sensor_hub": (self.sensor_hub.get_status()
                           if hasattr(self, 'sensor_hub')
                           and self.sensor_hub else {}),
            "voice_interface": (self.voice_interface.status()
                                if hasattr(self, 'voice_interface')
                                and self.voice_interface else {}),
            "micro_model": (self.micro_model.stats
                            if hasattr(self, 'micro_model')
                            and self.micro_model else {}),
            "elastic_scaler": (self.elastic_scaler.summary()
                               if hasattr(self, 'elastic_scaler')
                               and self.elastic_scaler else {}),
            "disk_space": self._get_disk_status(),
            "embedding": {
                "model": (self.consciousness.embed.model
                          if hasattr(self, 'consciousness') and self.consciousness else "N/A"),
                "available": (self.consciousness.embed.available
                              if hasattr(self, 'consciousness') and self.consciousness else False),
                "cache_hits": (self.consciousness.embed.cache_hits
                               if hasattr(self, 'consciousness') and self.consciousness else 0),
                "cache_misses": (self.consciousness.embed.cache_misses
                                 if hasattr(self, 'consciousness') and self.consciousness else 0),
                "alert": ("nomic-embed-text DOWN" if (
                    hasattr(self, 'consciousness') and self.consciousness
                    and not self.consciousness.embed.available
                ) else None),
            },
            "magma": {
                "audit_wired": getattr(self, '_audit_log', None) is not None,
                "audit_entries": self._audit_log.count() if getattr(self, '_audit_log', None) else 0,
                "replay_wired": getattr(self, '_replay_store', None) is not None,
                "trust_wired": getattr(self, '_trust_engine', None) is not None,
                "trust_ranking": (self._trust_engine.get_ranking()[:5]
                                  if getattr(self, '_trust_engine', None) else []),
                "cognitive_graph": (self._cognitive_graph.stats()
                                    if getattr(self, '_cognitive_graph', None) else {}),
            },
        }


    # ── Kieliasetukset ──────────────────────────────────────────

    def set_language(self, mode: str = "auto"):
        """Aseta kielitila: 'auto', 'fi', 'en'."""
        if mode in ("auto", "fi", "en"):
            self.language_mode = mode
            log.info(f"Kielitila: {mode}")
        else:
            log.warning(f"Tuntematon kielitila: {mode}")

    def get_language_status(self) -> dict:
        """Palauta käännösjärjestelmän tila."""
        return {
            "mode": self.language_mode,
            "proxy_available": self.translation_proxy is not None,
            "voikko": self.translation_proxy.voikko.available if self.translation_proxy else False,
            "dict_size": len(self.translation_proxy.dict_fi_en) if self.translation_proxy else 0,
            "en_prompts": list(AGENT_EN_PROMPTS.keys()),
            "stats": self.translation_proxy.get_stats() if self.translation_proxy else {},
        }

    # ── Improvement 5: Cache Warming ─────────────────────────

    def _warm_caches(self):
        """Warm hot cache and micro-model V1 from YAML Q&A pairs at startup."""
        try:
            from backend.routes.chat import _YAML_QA
        except ImportError:
            log.debug("Cannot import _YAML_QA for cache warming")
            return

        if not _YAML_QA:
            return

        # Filter usable pairs (answer > 20 chars)
        usable = [(q, a, aid) for q, a, aid in _YAML_QA if len(a) > 20]
        if not usable:
            return

        # Seasonal boost keywords for current month
        try:
            from core.memory_engine import SEASONAL_BOOST
            month = datetime.now().month
            seasonal_kws = SEASONAL_BOOST.get(month, [])
            if isinstance(seasonal_kws, str):
                seasonal_kws = seasonal_kws.split()
        except ImportError:
            seasonal_kws = []

        # 1. Hot Cache warming
        hot_cache_count = 0
        if (hasattr(self, 'consciousness') and self.consciousness
                and self.consciousness.hot_cache):
            cache = self.consciousness.hot_cache
            # Prioritize seasonal entries
            seasonal_pairs = []
            other_pairs = []
            for q, a, _aid in usable:
                text_lower = (q + " " + a).lower()
                if seasonal_kws and any(kw.lower() in text_lower for kw in seasonal_kws):
                    seasonal_pairs.append((q, a))
                else:
                    other_pairs.append((q, a))
            # Load seasonal first, then others, up to 300
            for q, a in (seasonal_pairs + other_pairs)[:300]:
                cache.put(q, a, score=0.90, source="cache_warming")
                hot_cache_count += 1

        # 2. Micro-Model V1 warming
        v1_count = 0
        if (hasattr(self, 'consciousness') and self.consciousness
                and self.consciousness.micro_model
                and hasattr(self.consciousness.micro_model, 'v1')):
            v1 = self.consciousness.micro_model.v1
            v1_pairs = [{"pattern": q, "answer": a, "confidence": 0.92}
                        for q, a, _aid in usable]
            if v1_pairs:
                v1.train(v1_pairs)
                v1_count = v1.stats.get("lookup_count", 0)

        if hot_cache_count > 0 or v1_count > 0:
            print(f"  ✅ Cache warming: hot_cache={hot_cache_count}, "
                  f"V1 patterns={v1_count}", flush=True)

    # ── Heartbeat ───────────────────────────────────────────

    async def _heartbeat_loop(self):
        """
        Autonominen Waggle Cycle v0.0.3 — TIMEOUT-ESTO:
          - Käyttää throttle.state.heartbeat_interval (adaptiivinen)
          - Pending-task gate: max 3 rinnakkaista taustatehtävää
          - Jos edellinen kierros ei ole valmis → SKIP

        - ~2.5min: agentti ajattelee (1 kerrallaan)
        - ~5min: Queen syntetisoi
        - ~7.5min: Dance Language (kuiskaus)
        - ~10min: Reflektio + Oracle
        - ~15min: Oracle tutkii
        - Jatkuvasti: kuormituslaskurit puhdistetaan
        """
        _pending = 0          # Rinnakkaiset taustatehtävät
        _MAX_PENDING = (self.throttle.state.max_concurrent
                        if self.throttle else 3)

        async def _guarded(coro_func, *args):
            """Suorita tehtävä vain jos slotteja vapaana."""
            nonlocal _pending
            if _pending >= _MAX_PENDING:
                return  # SKIP — liikaa jonossa
            _pending += 1
            try:
                # PHASE1 TASK3: wait if chat is active before LLM call
                await self.priority.wait_if_chat()
                await coro_func(*args)
            finally:
                _pending -= 1

        # ── Tulosta heartbeat-konfiguraatio ────────────────────
        _hb_interval = (self.throttle.state.heartbeat_interval
                        if self.throttle else 60)
        _hb_idle_n = (self.throttle.state.idle_every_n_heartbeat
                      if self.throttle else 5)
        log.info(f"Heartbeat loop käynnistyy: interval={_hb_interval:.0f}s, "
                 f"idle_every={_hb_idle_n}, max_pending={_MAX_PENDING}")

        # ── Ensimmäinen tick heti (1s viive riittää) ──────────
        _first_tick = True

        while self.running:
            try:
                # Phase 3: Night Mode overrides interval
                if self._check_night_mode():
                    if not self._night_mode_active:
                        self._night_mode_active = True
                        self._night_mode_start = time.monotonic()
                        log.info(f"🌙 Night mode ON (user idle), cumulative facts: "
                                 f"{self._night_mode_facts_learned}")
                    interval = self._get_night_mode_interval()
                else:
                    # ADAPTIIVINEN intervalli (throttle säätää koneen mukaan)
                    interval = (self.throttle.state.heartbeat_interval
                                if self.throttle
                                else self.config.get("hivemind", {}).get(
                                    "heartbeat_interval", 60))

                # Ensimmäinen heartbeat heti — ei turhaa odotusta
                if _first_tick:
                    _first_tick = False
                    await asyncio.sleep(2)  # 2s riittää alustukseen
                else:
                    await asyncio.sleep(interval)
                if not self.running:
                    break

                self._heartbeat_count += 1

                # Puhdista kuormituslaskurit
                if self.scheduler:
                    self.scheduler.cleanup_load_counters()

                # ── Nomic embed health check (every 10th HB) ─────
                if (self._heartbeat_count % 10 == 0
                        and hasattr(self, 'consciousness') and self.consciousness):
                    embed = self.consciousness.embed
                    was_available = embed._available
                    embed._check_available()
                    if not embed._available and was_available:
                        log.error("nomic-embed-text WENT DOWN — search/learning degraded")
                        await self._notify_ws("alert", {
                            "type": "embed_down",
                            "message": "nomic-embed-text not responding",
                            "severity": "critical",
                        })
                    elif embed._available and not was_available:
                        log.info("nomic-embed-text recovered")
                        await self._notify_ws("alert", {
                            "type": "embed_recovered",
                            "message": "nomic-embed-text back online",
                            "severity": "info",
                        })

                # ── CHAT-PRIORITEETTI: skip jos chat käynnissä ────
                if self.priority.should_skip:
                    log.debug(f"HB #{self._heartbeat_count}: skip (chat active)")
                    await self._notify_ws("heartbeat", {
                        "time": datetime.now().isoformat(),
                        "count": self._heartbeat_count,
                        "title": "heartbeat (SKIP: chat-prioriteetti)",
                    })
                    continue

                # ── TIMEOUT-GATE: skip koko kierros jos liikaa jonossa ──
                if _pending >= _MAX_PENDING:
                    log.debug(f"HB #{self._heartbeat_count}: skip ({_pending} pending)")
                    await self._notify_ws("heartbeat", {
                        "time": datetime.now().isoformat(),
                        "count": self._heartbeat_count,
                        "title": f"heartbeat (SKIP: {_pending} pending)",
                    })
                    continue

                # ── Heartbeat status print ──────────────────────
                _actions = []

                # Agentti miettii: joka idle_every_n HB (adaptiivinen)
                _idle_n = (self.throttle.state.idle_every_n_heartbeat
                           if self.throttle else 5)
                if self._heartbeat_count % _idle_n == 0 and self.spawner:
                    agents = list(self.spawner.active_agents.values())
                    if agents:
                        agent = agents[self._heartbeat_count % len(agents)]
                        _actions.append(f"think:{agent.name}")
                        self._track_task(
                            _guarded(self._agent_proactive_think, agent))

                # Queen syntetisoi: joka 2*idle_n HB
                if self._heartbeat_count % max(2 * _idle_n, 2) == 0 and self.master_agent:
                    _actions.append("queen")
                    self._track_task(
                        _guarded(self._master_generate_insight))

                # Whisper: joka 3*idle_n HB
                if self._heartbeat_count % max(3 * _idle_n, 3) == 0 and self.whisper and self.spawner:
                    agents = list(self.spawner.active_agents.values())
                    if len(agents) >= 2:
                        _actions.append("whisper")
                        self._track_task(
                            _guarded(self._whisper_cycle, agents))

                # Reflect: joka 4*idle_n HB
                if self._heartbeat_count % max(4 * _idle_n, 4) == 0 and self.spawner:
                    agents = list(self.spawner.active_agents.values())
                    if agents:
                        agent = agents[self._heartbeat_count // max(4 * _idle_n, 4) % len(agents)]
                        _actions.append(f"reflect:{agent.name}")
                        self._track_task(
                            _guarded(self._agent_reflect, agent))

                # Oracle: joka 4*idle_n HB
                if self._heartbeat_count % max(4 * _idle_n, 4) == 0 and self.spawner:
                    _actions.append("oracle")
                    self._track_task(
                        _guarded(self._oracle_consultation_cycle))

                # Oracle tutkii: joka 6*idle_n HB
                if self._heartbeat_count % max(6 * _idle_n, 6) == 0 and self.spawner:
                    _actions.append("oracle_research")
                    self._track_task(
                        _guarded(self._oracle_research_cycle))

                # Phase 3: Round Table (every 20th heartbeat)
                _rt_every = self.config.get("round_table", {}).get(
                    "every_n_heartbeat", 20)
                if (self._heartbeat_count % _rt_every == 0
                        and self.spawner
                        and self.config.get("round_table", {}).get("enabled", True)):
                    _rt_version = self.config.get("round_table", {}).get("version", 1)
                    _actions.append(f"round_table_v{_rt_version}")
                    if _rt_version >= 2:
                        self._track_task(
                            _guarded(self._round_table_v2))
                    else:
                        self._track_task(
                            _guarded(self._round_table))

                # Phase 3: Night mode learning (every other cycle)
                # Night learn bypasses _guarded — it's the whole point
                # of night mode and should not be starved by other tasks
                if (self._night_mode_active
                        and self._heartbeat_count % 2 == 0):
                    _actions.append("night_learn")
                    self._track_task(self._night_learning_cycle())
                    # Save progress every 5th heartbeat (was 20)
                    if self._heartbeat_count % 5 == 0:
                        self._save_learning_progress()

                # ── Weekly report trigger (runs even outside night mode) ──
                # Check every 50 heartbeats (~50 min) if weekly report is due
                if self._heartbeat_count % 50 == 0:
                    self._track_task(self._maybe_weekly_report())

                # Odottavat tehtävät (max 1 kerrallaan)
                if _pending < _MAX_PENDING:
                    pending_tasks = await self.memory.get_tasks(status="pending")
                    for task in pending_tasks[:1]:
                        agent_id = task.get("assigned_agent")
                        if agent_id and self.spawner:
                            agent = self.spawner.get_agent(agent_id)
                            if agent and agent.status == "idle":
                                _actions.append(f"task:{agent_id}")
                                self._track_task(
                                    _guarded(agent.execute_task, task))

                # idle_research: batch agentteja kerrallaan (ei rajoitettu 1:een)
                # OpsAgent voi pysäyttää idle-tutkimuksen kuormituksen takia
                idle_n = self.throttle.state.idle_every_n_heartbeat
                _ops_idle_ok = not (self.ops_agent and self.ops_agent.idle_paused)
                if (self._heartbeat_count % idle_n == 0
                        and self.spawner and _pending < _MAX_PENDING
                        and _ops_idle_ok):
                    idle_agents = [
                        a for a in self.spawner.active_agents.values()
                        if a.status == "idle"
                    ]
                    batch = self.throttle.state.idle_batch_size
                    for agent in idle_agents[:batch]:
                        _actions.append(f"idle:{agent.name}")
                        self._track_task(
                            _guarded(self._idle_research, agent))

                # Priority invite: alisuoriutuvat (max 1, harvemmin)
                if (self._swarm_enabled and self.scheduler
                        and self._heartbeat_count % 15 == 0
                        and _pending < _MAX_PENDING
                        and _ops_idle_ok):
                    underused = self.scheduler.get_underused_agents()
                    if underused and self.spawner:
                        aid = underused[0]
                        agent_obj = self.spawner.get_agent(aid)
                        if agent_obj and agent_obj.status == "idle":
                            _actions.append(f"invite:{aid}")
                            self._track_task(
                                _guarded(self._idle_research, agent_obj))

                # ── Console print: mitä tapahtui tällä kierroksella ──
                if _actions:
                    log.debug(f"HB #{self._heartbeat_count}: {', '.join(_actions)} (pending={_pending})")
                else:
                    log.debug(f"HB #{self._heartbeat_count}: idle "
                              f"(next action at #{self._heartbeat_count + (_idle_n - self._heartbeat_count % _idle_n)})")

                await self._notify_ws("heartbeat", {
                    "time": datetime.now().isoformat(),
                    "count": self._heartbeat_count,
                    "pending": _pending,
                    "actions": _actions,
                })

            except asyncio.CancelledError:
                break
            except Exception as e:
                log.error(f"Heartbeat-virhe: {e}")

    async def _agent_proactive_think(self, agent):
        """Agentti ajattelee + lukee muiden oivallukset."""
        try:
            own = await self.memory.get_recent_memories(agent_id=agent.id, limit=3)
            own_text = "\n".join(
                [m.get("content", "")[:150] for m in own]
            ) if own else ""

            all_recent = await self.memory.get_recent_memories(limit=15)
            others_insights = [
                m.get("content", "")[:150] for m in all_recent
                if m.get("memory_type") == "insight"
                and m.get("agent_id") != agent.id
            ][:5]
            others_text = "\n".join(others_insights) if others_insights else ""

            messages = await self.memory.get_messages(agent.id)
            msg_text = "\n".join(
                [f"- {m.get('content', '')[:100]}" for m in messages[:3]]
            ) if messages else ""
            if messages:
                await self.memory.mark_messages_read(agent.id)

            now = datetime.now().strftime("%d.%m.%Y klo %H:%M")

            # Phase 3: Use guided task if available
            guided_task = None
            if (hasattr(self, 'consciousness') and self.consciousness
                    and self.consciousness.task_queue):
                guided_task = self.consciousness.task_queue.next_task(
                    agent_id=getattr(agent, 'id', None),
                    agent_type=getattr(agent, 'agent_type', None))

            if guided_task:
                prompt = (
                    f"You are {agent.name}. Date: {now}\n\n"
                    f"LEARNING TASK ({guided_task['type']}): {guided_task['prompt']}\n\n"
                    + (f"Your recent observations:\n{own_text[:200]}\n\n"
                       if own_text else "")
                    + "Provide a factual answer. 2 sentences max in English."
                )
            else:
                prompt = (
                    f"You are {agent.name}. Date: {now}\n\n"
                    f"Your observations:\n{own_text[:200]}\n\n"
                    + (f"Other agents' insights:\n{others_text[:300]}\n\n"
                       if others_text else "")
                    + (f"Messages for you:\n{msg_text[:200]}\n\n"
                       if msg_text else "")
                    + "React to other agents' insights OR propose ONE NEW concrete observation. "
                    + "One sentence in English."
                )

            # PHASE1 TASK2: heartbeat MUST use llama3.2:1b — never phi4-mini
            _hb = self.llm_heartbeat
            _t0 = time.monotonic()
            try:
                async with self.throttle:
                    # FIX-1: context manager hoitaa injektoinnin + palautuksen
                    with self._enriched_prompt(agent, inject_date=True,
                                                inject_knowledge=True,
                                                knowledge_max_chars=1500):
                        _resp = await _hb.generate(
                            prompt, system=agent.system_prompt,
                            max_tokens=200
                        )
                _elapsed = (time.monotonic() - _t0) * 1000
                self._report_llm_result(_elapsed, True, _hb.model)
                insight = _resp.content if _resp and not _resp.error else ""
                if insight:
                    log.debug(f"{agent.name} ({_elapsed:.0f}ms): {insight[:80]}")
            except Exception as exc:
                _elapsed = (time.monotonic() - _t0) * 1000
                self._report_llm_result(30000, False, _hb.model)
                log.warning(f"{agent.name} LLM error ({_elapsed:.0f}ms): {exc}")
                insight = ""

            # KORJAUS K10: validoi ennen tallennusta
            if insight and self._is_valid_response(insight):
                # ═══ EN Validator: standardisoi heartbeat-insight ═══
                if self.en_validator:
                    _val = self.en_validator.validate(insight)
                    if _val.was_corrected:
                        insight = _val.corrected
                await self.memory.store_memory(
                    content=f"[{agent.name}] {insight}",
                    agent_id=agent.id,
                    memory_type="insight",
                    importance=0.7
                )
                await self._notify_ws("agent_insight", {
                    "agent": agent.name,
                    "type": agent.agent_type,
                    "insight": insight,
                })
                await self._share_insight(agent, insight)
                await self._log_finetune(agent, prompt, insight)

        except Exception as e:
            log.warning(f"Ajattelu epäonnistui ({agent.name}): {e}")

    async def _share_insight(self, from_agent, insight: str):
        """Jaa insight relevanteille agenteille (max 2 vastaanottajaa)."""
        if not self.spawner:
            return
        try:
            insight_lower = insight.lower()
            routing = {}
            if hasattr(self.spawner, 'yaml_bridge'):
                routing = self.spawner.yaml_bridge.get_routing_rules()

            # Pisteytä relevanssi ja valitse TOP 2
            candidates = []
            for agent in list(self.spawner.active_agents.values()):
                if agent.id == from_agent.id:
                    continue
                keywords = routing.get(agent.agent_type, [])
                relevance = sum(1 for kw in keywords if kw in insight_lower)
                if relevance > 0:
                    candidates.append((relevance, agent))

            # Järjestä relevanssin mukaan, ota max 2
            candidates.sort(key=lambda x: x[0], reverse=True)
            for _, agent in candidates[:2]:
                await from_agent.communicate(
                    agent.id,
                    f"[Insight] {insight[:200]}",
                    "insight_share"
                )
        except Exception as e:
            log.warning(f"Share epäonnistui: {e}")

    async def _log_finetune(self, agent, prompt: str, response: str):
        """Tallenna Q/A-pari finetuning-dataan + lähetä arviointijonoon."""
        try:
            sys_prompt = (agent.system_prompt[:500]
                          if hasattr(agent, 'system_prompt') else "")
            entry = {
                "messages": [
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": prompt[:500]},
                    {"role": "assistant", "content": response[:500]}
                ],
                "agent": agent.agent_type,
                "timestamp": datetime.now().isoformat()
            }
            finetune_path = Path("data/finetune_live.jsonl")
            finetune_path.parent.mkdir(exist_ok=True)
            with open(finetune_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")

            # ── LearningEngine: lähetä arviointijonoon ────────
            if self.learning:
                agent_id = agent.id if hasattr(agent, 'id') else "unknown"
                agent_type = agent.agent_type if hasattr(agent, 'agent_type') else "unknown"
                self.learning.submit_for_evaluation(
                    agent_id=agent_id,
                    agent_type=agent_type,
                    system_prompt=sys_prompt,
                    prompt=prompt,
                    response=response,
                )
        except Exception:
            pass

    async def _master_generate_insight(self):
        """Queen syntetisoi oivallukset. PHASE1 TASK2: uses llama3.2:1b only."""
        try:
            all_memories = await self.memory.get_recent_memories(limit=20)
            insights = [m for m in all_memories if m.get("memory_type") == "insight"]
            if not insights:
                insights = all_memories[:10]

            insights_text = "\n".join(
                [m.get("content", "") for m in insights[:10]]
            )

            # PHASE1 TASK2: heartbeat MUST use llama3.2:1b — never phi4-mini
            _hb = self.llm_heartbeat
            _prompt = (f"Insights:\n{insights_text[:500]}\n\n"
                       f"Synthesize ONE strategic insight. 2 sentences in English.")
            async with self.throttle:
                _resp = await _hb.generate(
                    _prompt,
                    system=self.master_agent.system_prompt[:500],
                    max_tokens=200
                )
            synthesis = _resp.content if _resp and not _resp.error else ""

            # KORJAUS K10: validoi synteesi
            if synthesis and self._is_valid_response(synthesis):
                await self.memory.store_memory(
                    content=f"[Swarm Queen] {synthesis}",
                    agent_id=(self.master_agent.id
                              if hasattr(self.master_agent, 'id')
                              else "hivemind"),
                    memory_type="insight",
                    importance=0.9
                )
                await self._notify_ws("queen_synthesis", {"synthesis": synthesis})
        except Exception as e:
            log.warning(f"Synteesi epäonnistui: {e}")

    # ── Phase 3: Round Table ─────────────────────────────

    async def _round_table(self, topic: str = None, agent_count: int = 6):
        """Round Table: 6 agents discuss a topic, Queen synthesizes consensus."""
        if not self.spawner:
            return
        _rt_cfg = self.config.get("round_table", {})
        if not _rt_cfg.get("enabled", True):
            return
        agent_count = _rt_cfg.get("agent_count", agent_count)
        min_agents = _rt_cfg.get("min_agents", 3)

        agents = list(self.spawner.active_agents.values())
        if len(agents) < min_agents:
            return

        try:
            # Phase 1: Generate topic if not given
            if not topic:
                all_recent = await self.memory.get_recent_memories(limit=10)
                insights = [m.get("content", "")[:100] for m in all_recent
                            if m.get("memory_type") == "insight"]
                if insights:
                    _hb = self.llm_heartbeat
                    _prompt = (f"Recent insights:\n" +
                               "\n".join(insights[:5]) +
                               "\n\nSuggest ONE specific discussion topic for a panel "
                               "of beekeeping experts. One sentence in English.")
                    async with self.throttle:
                        _resp = await _hb.generate(_prompt, max_tokens=80)
                    topic = _resp.content.strip() if _resp and not _resp.error else None
                if not topic:
                    topic = "Current best practices for Finnish beekeeping"

            log.info(f"🏛️ Round Table starting: {topic[:80]}")
            await self._notify_ws("round_table_start", {
                "topic": topic,
                "agent_count": agent_count,
                "time": datetime.now().isoformat(),
            })

            # Phase 2: Select agents
            selected = self._select_round_table_agents(agents, topic, agent_count)

            # Phase 3: Sequential discussion (each sees previous)
            discussion = []
            for agent in selected:
                await self.priority.wait_if_chat()
                prev_text = ""
                if discussion:
                    prev_text = "\n".join(
                        [f"[{d['agent']}]: {d['response']}" for d in discussion[-3:]])
                _prompt = (
                    f"ROUND TABLE DISCUSSION\nTopic: {topic}\n"
                    + (f"Previous speakers:\n{prev_text}\n\n" if prev_text else "")
                    + f"You are {agent.name} ({agent.agent_type}). "
                    f"Share your expert perspective. 2 sentences max in English."
                )
                _hb = self.llm_heartbeat
                try:
                    async with self.throttle:
                        with self._enriched_prompt(agent, inject_date=True,
                                                    inject_knowledge=True,
                                                    knowledge_max_chars=800):
                            _resp = await _hb.generate(
                                _prompt, system=agent.system_prompt,
                                max_tokens=150)
                    response = _resp.content if _resp and not _resp.error else ""
                except Exception:
                    response = ""

                if response and self._is_valid_response(response):
                    entry = {
                        "agent": agent.name,
                        "agent_type": agent.agent_type,
                        "agent_id": agent.id,
                        "response": response,
                    }
                    discussion.append(entry)

                    # Phase 3: Record for agent levels
                    if self.agent_levels:
                        try:
                            self.agent_levels.record_response(
                                agent_id=agent.id,
                                agent_type=agent.agent_type,
                                was_correct=True,
                                was_hallucination=False)
                        except Exception:
                            pass

            if not discussion:
                return

            # Phase 4: Queen synthesizes (with seasonal context)
            all_responses = "\n".join(
                [f"[{d['agent']}]: {d['response']}" for d in discussion])

            # Inject seasonal context for Queen
            _seasonal_ctx = ""
            try:
                from core.seasonal_guard import get_seasonal_guard
                _sg = get_seasonal_guard()
                _seasonal_ctx = f"\n{_sg.queen_context()}\n"
            except Exception:
                pass

            _synth_prompt = (
                f"ROUND TABLE SYNTHESIS\nTopic: {topic}\n"
                f"{_seasonal_ctx}\n"
                f"Agent responses:\n{all_responses}\n\n"
                f"Synthesize the key consensus. Identify agreements and "
                f"the most important practical takeaway. 3 sentences in English."
            )
            _hb = self.llm_heartbeat
            async with self.throttle:
                _resp = await _hb.generate(
                    _synth_prompt,
                    system=self.master_agent.system_prompt[:500],
                    max_tokens=200)
            synthesis = _resp.content if _resp and not _resp.error else ""

            # Phase 5: Store + stream via theater pipe
            # Seasonal guard: reject out-of-season synthesis
            if synthesis and self._is_valid_response(synthesis):
                try:
                    from core.seasonal_guard import get_seasonal_guard
                    _sg_ok, _sg_reason = get_seasonal_guard().filter_enrichment(synthesis)
                    if not _sg_ok:
                        log.info(f"🏛️ Round Table synthesis filtered: {_sg_reason}")
                except Exception:
                    _sg_ok = True
                if _sg_ok and self.consciousness:
                    self.consciousness.learn(
                        synthesis, agent_id="round_table",
                        source_type="round_table",
                        confidence=0.85, validated=True,
                        metadata={"topic": topic[:200]})

                # Layer 4: Record consensus provenance + broadcast
                _prov = getattr(self, '_provenance', None)
                _chreg = getattr(self, '_channel_registry', None)
                if _prov:
                    try:
                        _rt_agents = [d.get("agent_id", "") for d in discussion]
                        _prov.record_consensus(
                            f"round_table_{int(time.time())}",
                            _rt_agents, synthesis[:500])
                    except Exception as _e:
                        log.debug(f"Provenance consensus: {_e}")
                if _chreg:
                    try:
                        _chreg.broadcast("judges", "round_table",
                                         f"[Synthesis] {synthesis[:300]}")
                    except Exception:
                        pass

                # Layer 5: Record consensus participation trust signal
                _te = getattr(self, '_trust_engine', None)
                if _te:
                    for _d in discussion:
                        _aid = _d.get("agent_id", "")
                        if _aid:
                            try:
                                _te.record_signal(_aid, "consensus_participation", 1.0)
                            except Exception:
                                pass

                await self._theater_stream_round_table(topic, discussion, synthesis)
                log.info(f"🏛️ Round Table done: {len(discussion)} agents, topic: {topic[:60]}")

        except Exception as e:
            log.error(f"Round Table error: {e}")

    def _select_round_table_agents(self, agents: list, topic: str,
                                    count: int = 6) -> list:
        """Select agents for Round Table: relevance + level boost + 1 random."""
        if not agents:
            return []

        # Get routing rules for keyword matching
        routing_rules = {}
        if self.spawner and hasattr(self.spawner, 'yaml_bridge'):
            routing_rules = self.spawner.yaml_bridge.get_routing_rules()

        topic_lower = topic.lower()
        scored = []
        for agent in agents:
            keywords = routing_rules.get(agent.agent_type, [])
            relevance = sum(1 for kw in keywords if kw in topic_lower)
            level_boost = 0
            if self.agent_levels:
                try:
                    level_boost = self.agent_levels.get_level(agent.id) * 0.2
                except Exception:
                    pass
            score = relevance + level_boost
            scored.append((score, agent))

        scored.sort(key=lambda x: x[0], reverse=True)

        # Take top (count-1) by score, + 1 random for diversity
        selected = [a for _, a in scored[:max(1, count - 1)]]
        remaining = [a for _, a in scored[count - 1:] if a not in selected]
        if remaining:
            import random
            selected.append(random.choice(remaining))

        return selected[:count]

    def _should_translate_output(self) -> bool:
        """Check if output should be translated to Finnish based on language_mode."""
        if self.language_mode == "fi":
            return True
        if self.language_mode == "en":
            return False
        # auto: translate if last detected language was Finnish
        return getattr(self, '_detected_lang', 'en') == "fi"

    async def _theater_stream_round_table(self, topic: str,
                                           discussion: list, synthesis: str):
        """Stream Round Table results with 300ms delays (Theater Pipe)."""
        _translate = self._should_translate_output() and self.translation_proxy
        for entry in discussion:
            display_text = entry["response"]
            if _translate:
                try:
                    _fi = self.translation_proxy.en_to_fi(
                        display_text, force_opus=True)
                    if _fi.method != "passthrough":
                        display_text = _fi.text
                except Exception:
                    pass

            await self._notify_ws("round_table_insight", {
                "agent": entry["agent"],
                "agent_type": entry["agent_type"],
                "response": display_text,
                "response_en": entry["response"],
            })
            await asyncio.sleep(0.3)

        # Synthesis
        synth_display = synthesis
        if _translate:
            try:
                _fi = self.translation_proxy.en_to_fi(synthesis, force_opus=True)
                if _fi.method != "passthrough":
                    synth_display = _fi.text
            except Exception:
                pass

        await self._notify_ws("round_table_synthesis", {
            "topic": topic,
            "synthesis": synth_display,
            "synthesis_en": synthesis,
            "agent_count": len(discussion),
        })
        await self._notify_ws("round_table_end", {
            "topic": topic,
            "agent_count": len(discussion),
        })

    # ── Round Table v2 ─────────────────────────────────────

    async def _round_table_v2(self, topic: str = None, agent_count: int = 6):
        """Round Table v2: blind phase + informed phase + Queen synthesis.

        Phase 0 (Blind): 2 agents answer WITHOUT seeing others — independent views
        Phase 1 (Informed): 4 agents see both blind responses — react, agree/disagree
        Phase 2 (Queen): Queen finds disagreements or explains why consensus is strong
        """
        if not self.spawner:
            return
        _rt_cfg = self.config.get("round_table", {})
        if not _rt_cfg.get("enabled", True):
            return
        agent_count = _rt_cfg.get("agent_count", agent_count)
        min_agents = _rt_cfg.get("min_agents", 3)

        agents = list(self.spawner.active_agents.values())
        if len(agents) < min_agents:
            return

        try:
            # Generate topic if not given
            if not topic:
                all_recent = await self.memory.get_recent_memories(limit=10)
                insights = [m.get("content", "")[:100] for m in all_recent
                            if m.get("memory_type") == "insight"]
                if insights:
                    _hb = self.llm_heartbeat
                    _prompt = (f"Recent insights:\n" +
                               "\n".join(insights[:5]) +
                               "\n\nSuggest ONE specific discussion topic for a panel "
                               "of beekeeping experts. One sentence in English.")
                    async with self.throttle:
                        _resp = await _hb.generate(_prompt, max_tokens=80)
                    topic = _resp.content.strip() if _resp and not _resp.error else None
                if not topic:
                    topic = "Current best practices for Finnish beekeeping"

            log.info(f"🏛️ Round Table v2 starting: {topic[:80]}")
            await self._notify_ws("round_table_start", {
                "topic": topic,
                "agent_count": agent_count,
                "version": 2,
                "time": datetime.now().isoformat(),
            })

            # Select agents
            selected = self._select_round_table_agents(agents, topic, agent_count)
            if len(selected) < 2:
                return

            # === Phase 0: BLIND — 2 agents answer independently ===
            blind_agents = selected[:2]
            blind_responses = []
            for agent in blind_agents:
                await self.priority.wait_if_chat()
                _prompt = (
                    f"ROUND TABLE DISCUSSION (BLIND PHASE)\nTopic: {topic}\n"
                    f"You are {agent.name} ({agent.agent_type}). "
                    f"Share your independent expert perspective. "
                    f"You have NOT seen other responses. 2 sentences max in English."
                )
                _hb = self.llm_heartbeat
                try:
                    async with self.throttle:
                        with self._enriched_prompt(agent, inject_date=True,
                                                    inject_knowledge=True,
                                                    knowledge_max_chars=800):
                            _resp = await _hb.generate(
                                _prompt, system=agent.system_prompt,
                                max_tokens=150)
                    response = _resp.content if _resp and not _resp.error else ""
                except Exception:
                    response = ""

                if response and self._is_valid_response(response):
                    entry = {
                        "agent": agent.name,
                        "agent_type": agent.agent_type,
                        "agent_id": agent.id,
                        "response": response,
                        "phase": "blind",
                    }
                    blind_responses.append(entry)
                    if self.agent_levels:
                        try:
                            self.agent_levels.record_response(
                                agent_id=agent.id,
                                agent_type=agent.agent_type,
                                was_correct=True,
                                was_hallucination=False)
                        except Exception:
                            pass

            if not blind_responses:
                return

            # === Phase 1: INFORMED — remaining agents see blind responses ===
            blind_text = "\n".join(
                [f"[{d['agent']}]: {d['response']}" for d in blind_responses])
            informed_agents = selected[2:]
            informed_responses = []
            for agent in informed_agents:
                await self.priority.wait_if_chat()
                _prompt = (
                    f"ROUND TABLE DISCUSSION (INFORMED PHASE)\nTopic: {topic}\n\n"
                    f"Independent expert responses (blind phase):\n{blind_text}\n\n"
                    f"You are {agent.name} ({agent.agent_type}). "
                    f"React to the blind responses: agree, disagree, or add "
                    f"a new perspective. 2 sentences max in English."
                )
                _hb = self.llm_heartbeat
                try:
                    async with self.throttle:
                        with self._enriched_prompt(agent, inject_date=True,
                                                    inject_knowledge=True,
                                                    knowledge_max_chars=800):
                            _resp = await _hb.generate(
                                _prompt, system=agent.system_prompt,
                                max_tokens=150)
                    response = _resp.content if _resp and not _resp.error else ""
                except Exception:
                    response = ""

                if response and self._is_valid_response(response):
                    entry = {
                        "agent": agent.name,
                        "agent_type": agent.agent_type,
                        "agent_id": agent.id,
                        "response": response,
                        "phase": "informed",
                    }
                    informed_responses.append(entry)
                    if self.agent_levels:
                        try:
                            self.agent_levels.record_response(
                                agent_id=agent.id,
                                agent_type=agent.agent_type,
                                was_correct=True,
                                was_hallucination=False)
                        except Exception:
                            pass

            # === Phase 2: Queen synthesis ===
            all_discussion = blind_responses + informed_responses
            if not all_discussion:
                return

            all_responses = "\n".join(
                [f"[{d['agent']} ({d['phase']})] {d['response']}"
                 for d in all_discussion])
            _synth_prompt = (
                f"ROUND TABLE v2 SYNTHESIS\nTopic: {topic}\n\n"
                f"Agent responses:\n{all_responses}\n\n"
                f"Find disagreements between agents. If none exist, explain "
                f"why the consensus is strong. Identify the most important "
                f"practical takeaway. 3 sentences in English."
            )
            _hb = self.llm_heartbeat
            async with self.throttle:
                _resp = await _hb.generate(
                    _synth_prompt,
                    system=self.master_agent.system_prompt[:500],
                    max_tokens=200)
            synthesis = _resp.content if _resp and not _resp.error else ""

            # Store + stream
            if synthesis and self._is_valid_response(synthesis):
                if self.consciousness:
                    self.consciousness.learn(
                        synthesis, agent_id="round_table",
                        source_type="round_table",
                        confidence=0.85, validated=True,
                        metadata={"topic": topic[:200], "version": 2})

                await self._theater_stream_round_table(
                    topic, all_discussion, synthesis)
                log.info(f"🏛️ Round Table v2 done: "
                         f"{len(blind_responses)} blind + "
                         f"{len(informed_responses)} informed, "
                         f"topic: {topic[:60]}")

        except Exception as e:
            log.error(f"Round Table v2 error: {e}")

    # ── Phase 3: Night Mode ───────────────────────────────

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
        if not self.night_enricher:
            return
        try:
            report = self.night_enricher.generate_morning_report()
            # Send via WebSocket
            import asyncio
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    loop.create_task(self._notify_ws(
                        "morning_report", report))
                else:
                    loop.run_until_complete(self._notify_ws(
                        "morning_report", report))
            except RuntimeError:
                pass  # No event loop — skip WS notification

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

        5-way rotation:
          %5 == 0,1: guided task
          %5 == 2: enrichment (Layer 2)
          %5 == 3: web learning (Layer 3)
          %5 == 4: distillation (Layer 4, if available) — else guided task

        Plus weekly meta-learning (Layer 5) and monthly code review (Layer 6).
        """
        if not self.consciousness:
            return

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

        # ── Layer 5: Weekly meta-learning check ──
        if self.meta_learning and self.meta_learning.is_due():
            try:
                report = await self.meta_learning.weekly_analysis()
                applied = await self.meta_learning.auto_apply_safe_optimizations(
                    report.get("suggestions", []))
                # D3: Also generate standalone weekly_report.json
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

        # ── Layer 6: Monthly code review check ──
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

        # ── Phase 10: Micro-model training check (every 50 cycles) ──
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
            with open("data/learning_progress.json", "w", encoding="utf-8") as f:
                json.dump(progress, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    async def _maybe_weekly_report(self):
        """Check if weekly report is due and generate it (runs from heartbeat)."""
        try:
            if not self.meta_learning:
                await self._init_learning_engines()
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

    async def _idle_research(self, agent):
        """Idle-agentti tutkii autonomisesti."""
        try:
            agent.status = "researching"
            all_recent = await self.memory.get_recent_memories(limit=15)
            others = [
                m.get("content", "")[:100] for m in all_recent
                if m.get("agent_id") != agent.id
                and m.get("memory_type") == "insight"
            ][:3]
            others_text = "\n".join(others) if others else ""

            messages = await self.memory.get_messages(agent.id)
            msg_text = "\n".join(
                [m.get("content", "")[:80] for m in messages[:3]]
            ) if messages else ""
            if messages:
                await self.memory.mark_messages_read(agent.id)

            now = datetime.now().strftime("%Y-%m-%d %H:%M")
            prompt = f"You are {agent.name}. Date: {now}\n"
            if others_text:
                prompt += f"Other agents' insights:\n{others_text}\n"
            if msg_text:
                prompt += f"Messages for you:\n{msg_text}\n"
            prompt += ("Research something NEW related to your specialty. "
                       "ONE concrete fact or recommendation. One sentence in English.")

            # Knowledge injection promptin sisään (ei system_promptiin)
            if self.knowledge_loader:
                _agent_type = getattr(agent, 'agent_type', '')
                _kb = self.knowledge_loader.get_knowledge_summary(_agent_type)
                if _kb:
                    prompt += "\n\nKNOWLEDGE BASE:\n" + _kb[:800]

            # PHASE1 TASK2: heartbeat MUST use llama3.2:1b — never phi4-mini
            _hb = self.llm_heartbeat
            _t0 = time.monotonic()
            try:
                async with self.throttle:
                    # FIX-1: context manager päivämääräinjektiolle
                    with self._enriched_prompt(agent, inject_date=True,
                                                inject_knowledge=False):
                        resp = await _hb.generate(
                            prompt, system=agent.system_prompt, max_tokens=150
                        )
                _elapsed = (time.monotonic() - _t0) * 1000
                self._report_llm_result(_elapsed, True, _hb.model)
                insight = (resp.content
                           if resp and not resp.error and resp.content
                           else None)
            except Exception:
                self._report_llm_result(30000, False, _hb.model)
                insight = None

            # KORJAUS K10: validoi + KORJAUS: duplikaatti-reward poistettu
            if insight and self._is_valid_response(insight):
                await self.memory.store_memory(
                    content=f"[{agent.name}] tutkimus: {insight}",
                    agent_id=agent.id,
                    memory_type="insight",
                    importance=0.6
                )
                # Reward KERRAN (oli 2x aiemmin)
                if self.token_economy:
                    try:
                        await self.token_economy.reward(
                            agent.id, "idle_research", custom_amount=2
                        )
                    except Exception:
                        pass
                if self.scheduler:
                    self.scheduler.record_task_result(
                        agent.id, success=True, latency_ms=_elapsed
                    )
                await self._share_insight(agent, insight)
                await self._log_finetune(agent, prompt, insight)
                await self._notify_ws("idle_research", {
                    "agent": agent.name, "finding": insight[:150]
                })

            agent.status = "idle"
        except Exception:
            agent.status = "idle"

    async def _whisper_cycle(self, agents):
        try:
            _originals = {}
            agent_list = (agents if isinstance(agents, list)
                          else list(agents.values()))
            for _a in agent_list:
                _o = self._inject_knowledge(_a, max_chars=800)
                if _o is not None:
                    _originals[id(_a)] = (_a, _o)
            try:
                await self.whisper.auto_whisper_cycle(agent_list, self.llm)
            finally:
                for _a, _o in _originals.values():
                    self._restore_prompt(_a, _o)
        except Exception as e:
            log.warning(f"Whisper epäonnistui: {e}")

    async def _oracle_consultation_cycle(self):
        try:
            oracle = self.get_oracle()
            if not oracle or not hasattr(oracle, 'auto_generate_questions'):
                return
            agents = list(self.spawner.active_agents.values())
            if not agents:
                return
            count = await oracle.auto_generate_questions(agents)
            if count > 0:
                await self._notify_ws("oracle_questions_ready", {
                    "count": count,
                    "message": f"🔮 {count} kysymystä odottaa!"
                })
        except Exception as e:
            log.warning(f"Oracle-konsultaatio epäonnistui: {e}")

    async def _oracle_research_cycle(self):
        try:
            oracle = self.get_oracle()
            if not oracle or not hasattr(oracle, 'auto_research_cycle'):
                return
            agents = list(self.spawner.active_agents.values())
            if not agents:
                return
            count = await oracle.auto_research_cycle(agents)
            if count > 0:
                await self._notify_ws("oracle_research", {
                    "count": count,
                    "message": f"🔍 Oracle tutki {count} aihetta!"
                })
        except Exception as e:
            log.warning(f"Oracle-tutkimus epäonnistui: {e}")

    async def _agent_reflect(self, agent):
        """Agentti arvioi omaa toimintaansa."""
        try:
            recent = await self.memory.get_recent_memories(
                agent_id=agent.id, limit=3
            )
            if not recent:
                return
            recent_text = "\n".join(
                [m.get("content", "")[:80] for m in recent]
            )
            prompt = (f"Evaluate your actions: {recent_text}\n"
                      f"What did you learn? One sentence in English.")
            # PHASE1 TASK2: heartbeat MUST use llama3.2:1b — never phi4-mini
            _hb = self.llm_heartbeat
            async with self.throttle:
                resp = await _hb.generate(
                    prompt, system=agent.system_prompt[:500], max_tokens=100
                )
            if resp and not resp.error and self._is_valid_response(resp.content):
                await self.memory.store_memory(
                    content=f"[{agent.name}] reflektio: {resp.content}",
                    agent_id=agent.id,
                    memory_type="reflection",
                    importance=0.4
                )
        except Exception:
            pass

    # ── WebSocket ───────────────────────────────────────────

    def register_ws_callback(self, callback):
        self._ws_callbacks.append(callback)

    def unregister_ws_callback(self, callback):
        self._ws_callbacks = [cb for cb in self._ws_callbacks if cb != callback]

    async def _notify_ws(self, event_type: str, data: dict):
        for callback in self._ws_callbacks:
            try:
                await callback({"type": event_type, "data": data})
            except Exception:
                pass

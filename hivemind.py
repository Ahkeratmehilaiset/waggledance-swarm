"""
WaggleDance Swarm AI — Swarm Queen v0.9.2
===========================================
Jani Korpi (Ahkerat Mehiläiset)
Claude 4.6 • v0.9.2 • Built: 2026-03-11

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
import copy
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
from core.chat_handler import ChatHandler
from core.night_mode_controller import NightModeController
from core.round_table_controller import RoundTableController
from core.heartbeat_controller import HeartbeatController

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
        self._chat_handler = ChatHandler(self)
        self._night_mode = NightModeController(self)
        self._round_table_ctrl = RoundTableController(self)
        self._heartbeat_ctrl = HeartbeatController(self)

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
        1. Luo agentista kopion (alkuperäinen ei muutu)
        2. Injektoi päivämäärän (valinnainen)
        3. Injektoi tietopankin (valinnainen)
        4. Yield kopio — alkuperäinen pysyy koskemattomana

        Käyttö:
            with self._enriched_prompt(agent) as enriched:
                response = await enriched.think(message, context)
        """
        enriched = copy.copy(agent)
        enriched.system_prompt = agent.system_prompt  # own copy of string
        if inject_date:
            enriched.system_prompt = self._get_date_prefix() + enriched.system_prompt
        if inject_knowledge and self.knowledge_loader:
            agent_type = getattr(agent, 'agent_type', getattr(agent, 'type', ''))
            if agent_type:
                kb = self.knowledge_loader.get_knowledge_summary(agent_type)
                if kb:
                    enriched.system_prompt = enriched.system_prompt + "\n" + kb[:knowledge_max_chars]
        yield enriched

    async def _readiness_check(self):
        """C4: Validate critical services at startup."""
        print("  -- Readiness check --", flush=True)
        issues = []

        # 1. Ollama API
        try:
            import requests
            r = await asyncio.to_thread(
                requests.get, "http://localhost:11434/api/version", timeout=5)
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
            r = await asyncio.to_thread(
                requests.get, "http://localhost:11434/api/tags", timeout=5)
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
        self.knowledge_loader = self.knowledge  # single instance, was double

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

                # MAGMA Layer 2: ReplayEngine + AgentRollback
                try:
                    from core.replay_engine import ReplayEngine
                    from core.agent_rollback import AgentRollback
                    from core.chromadb_adapter import ChromaDBAdapter
                    _adapter = ChromaDBAdapter(self.consciousness.memory)
                    self._replay_engine = ReplayEngine(
                        _adapter, self._audit_log,
                        replay_store=getattr(self, '_replay_store', None),
                        cognitive_graph=getattr(self, '_cognitive_graph', None))
                    self._agent_rollback = AgentRollback(_adapter, self._audit_log)
                    print("  ✅ MAGMA ReplayEngine + AgentRollback wired", flush=True)
                except Exception as e:
                    print(f"  ⚠️  MAGMA Replay/Rollback: {e}", flush=True)

                # Phase 3: Init task queue
                self.consciousness.init_task_queue()

                # Phase 3: Init AgentLevelManager
                try:
                    from core.agent_levels import AgentLevelManager
                    _al_cfg = self.config.get("agent_levels", {})
                    if _al_cfg.get("enabled", True):
                        self.agent_levels = AgentLevelManager(db_path='data/chroma_db')
                        # Fixed during autonomous session 2026-03-08 — wire agent_levels into TrustEngine
                        _te = getattr(self, '_trust_engine', None)
                        if _te and not _te._agent_levels:
                            _te._agent_levels = self.agent_levels
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
                    loop=asyncio.get_running_loop(),
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

        if hasattr(self, '_whisper_task') and self._whisper_task:
            self._whisper_task.cancel()
            try:
                await self._whisper_task
            except asyncio.CancelledError:
                pass

        if self.ops_agent:
            await self.ops_agent.stop()

        if self.learning:
            await self.learning.stop()

        if self.spawner:
            await self.spawner.kill_all()

        # Phase 7: Stop voice interface
        if hasattr(self, 'voice_interface') and self.voice_interface:
            try:
                await self.voice_interface.stop()
            except Exception:
                pass
            self.voice_interface = None

        # Phase 5: Stop sensor hub
        if hasattr(self, 'sensor_hub') and self.sensor_hub:
            await self.sensor_hub.stop()

        # Phase 8: Stop data feeds
        if hasattr(self, 'data_feeds') and self.data_feeds:
            await self.data_feeds.stop()

        # M5: Cancel all tracked background tasks (BEFORE closing DB)
        if self._background_tasks:
            for task in list(self._background_tasks):
                task.cancel()
            await asyncio.gather(*self._background_tasks, return_exceptions=True)
            self._background_tasks.clear()

        # PHASE2: Flush remaining learn queue items before shutdown
        if hasattr(self, 'consciousness') and self.consciousness:
            try:
                flushed = self.consciousness.flush()
                if flushed:
                    log.info(f"Consciousness flush: {flushed} facts stored")
            except Exception as e:
                log.warning(f"Consciousness flush: {e}")

        # MAGMA: Save CognitiveGraph to disk before shutdown
        _cg = getattr(self, '_cognitive_graph', None)
        if _cg:
            try:
                _cg.save()
                log.info(f"CognitiveGraph saved: {_cg.stats()}")
            except Exception as e:
                log.warning(f"CognitiveGraph save failed: {e}")

        # Phase 9: Save code review suggestions
        if hasattr(self, 'code_reviewer') and self.code_reviewer:
            try:
                self.code_reviewer._save_suggestions()
            except Exception:
                pass

        # Close DB connections AFTER all tasks are done
        if self.memory:
            await self.memory.log_event("hivemind", "stopped", "WaggleDance sammutettu")
            await self.memory.close()

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

    async def _do_chat(self, message: str, language: str = "auto", source: str = "chat") -> str:
        """Delegated to ChatHandler."""
        return await self._chat_handler._do_chat(message, language=language, source=source)

    def _swarm_route(self, msg_lower: str, routing_rules: dict) -> tuple:
        """Delegated to ChatHandler."""
        return self._chat_handler._swarm_route(msg_lower, routing_rules)

    def _legacy_route(self, msg_lower: str, routing_rules: dict) -> tuple:
        """Delegated to ChatHandler."""
        return self._chat_handler._legacy_route(msg_lower, routing_rules)

    async def _delegate_to_agent(self, delegate_to: str, message: str,
                                  context: str, msg_lower: str, *,
                                  translation_used: bool = False,
                                  detected_lang: str = "fi",
                                  use_en_prompts: bool = False,
                                  fi_en_result=None) -> str:
        """Delegated to ChatHandler."""
        return await self._chat_handler._delegate_to_agent(
            delegate_to, message, context, msg_lower,
            translation_used=translation_used, detected_lang=detected_lang,
            use_en_prompts=use_en_prompts, fi_en_result=fi_en_result)

    # ── Vastauksen validointi (KORJAUS K10) ──────────────────

    def _is_valid_response(self, response: str) -> bool:
        """Delegated to ChatHandler."""
        return self._chat_handler._is_valid_response(response)

    def _populate_hot_cache(self, query: str, response: str,
                             score: float = 0.75, source: str = "chat",
                             detected_lang: str = "fi"):
        """Delegated to ChatHandler."""
        self._chat_handler._populate_hot_cache(query, response, score, source, detected_lang)

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
        """Delegated to ChatHandler."""
        return await self._chat_handler._multi_agent_collaboration(mission, plan, context)

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
            "heartbeat_count": self._heartbeat_count,
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

        # 2. Micro-Model V1 warming — load persisted patterns directly
        v1_count = 0
        try:
            from core.micro_model import PatternMatchEngine
            v1 = PatternMatchEngine(load_configs=True)
            v1_count = len(v1._lookup)
            # If V1 has no patterns yet but we have curated data, train now
            if v1_count == 0 and usable:
                v1_pairs = [{"pattern": q, "answer": a, "confidence": 0.92}
                            for q, a, _aid in usable]
                if v1_pairs:
                    v1.train(v1_pairs)
                    v1_count = len(v1._lookup)
            # Store V1 on consciousness for predict() routing
            if hasattr(self, 'consciousness') and self.consciousness:
                self.consciousness._v1_engine = v1
            # Also wire to hivemind for API exposure
            self.micro_model = v1
        except Exception as e:
            log.warning(f"V1 startup warm: {e}")

        if hot_cache_count > 0 or v1_count > 0:
            print(f"  ✅ Cache warming: hot_cache={hot_cache_count}, "
                  f"V1 patterns={v1_count}", flush=True)

    # ── Heartbeat ───────────────────────────────────────────

    async def _heartbeat_loop(self):
        """Delegated to HeartbeatController."""
        return await self._heartbeat_ctrl._heartbeat_loop()

    async def _agent_proactive_think(self, agent):
        """Delegated to HeartbeatController."""
        return await self._heartbeat_ctrl._agent_proactive_think(agent)

    async def _share_insight(self, from_agent, insight: str):
        """Delegated to HeartbeatController."""
        return await self._heartbeat_ctrl._share_insight(from_agent, insight)

    async def _log_finetune(self, agent, prompt: str, response: str):
        """Delegated to HeartbeatController."""
        return await self._heartbeat_ctrl._log_finetune(agent, prompt, response)

    async def _master_generate_insight(self):
        """Delegated to HeartbeatController."""
        return await self._heartbeat_ctrl._master_generate_insight()

    # ── Phase 3: Round Table (delegated to RoundTableController) ──

    async def _round_table(self, topic: str = None, agent_count: int = 6):
        """Delegated to RoundTableController."""
        return await self._round_table_ctrl._round_table(topic=topic, agent_count=agent_count)

    def _select_round_table_agents(self, agents: list, topic: str, count: int = 6) -> list:
        """Delegated to RoundTableController."""
        return self._round_table_ctrl._select_round_table_agents(agents, topic, count)

    def _should_translate_output(self) -> bool:
        """Delegated to RoundTableController."""
        return self._round_table_ctrl._should_translate_output()

    async def _theater_stream_round_table(self, topic: str, discussion: list, synthesis: str):
        """Delegated to RoundTableController."""
        return await self._round_table_ctrl._theater_stream_round_table(topic, discussion, synthesis)

    async def _round_table_v2(self, topic: str = None, agent_count: int = 6):
        """Delegated to RoundTableController."""
        return await self._round_table_ctrl._round_table_v2(topic=topic, agent_count=agent_count)

    # ── Phase 3: Night Mode ───────────────────────────────

    def _check_night_mode(self) -> bool:
        """Delegated to NightModeController."""
        return self._night_mode._check_night_mode()

    def _emit_morning_report(self):
        """Delegated to NightModeController."""
        return self._night_mode._emit_morning_report()

    def _get_night_mode_interval(self) -> float:
        """Delegated to NightModeController."""
        return self._night_mode._get_night_mode_interval()

    def _init_learning_engines(self, al_cfg: dict):
        """Delegated to NightModeController."""
        return self._night_mode._init_learning_engines(al_cfg)

    async def _night_learning_cycle(self):
        """Delegated to NightModeController."""
        return await self._night_mode._night_learning_cycle()

    def _load_persisted_facts_count(self) -> int:
        """Delegated to NightModeController."""
        # Fixed during autonomous session 2026-03-09 — safe fallback before controller init
        if not hasattr(self, '_night_mode'):
            try:
                import json
                p = Path("data/learning_progress.json")
                if p.exists():
                    data = json.loads(p.read_text(encoding="utf-8"))
                    return data.get("facts_learned", 0)
            except Exception:
                pass
            return 0
        return self._night_mode._load_persisted_facts_count()

    def _save_learning_progress(self):
        """Delegated to NightModeController."""
        return self._night_mode._save_learning_progress()

    async def _maybe_weekly_report(self):
        """Delegated to NightModeController."""
        return await self._night_mode._maybe_weekly_report()

    async def _idle_research(self, agent):
        """Delegated to HeartbeatController."""
        return await self._heartbeat_ctrl._idle_research(agent)

    async def _whisper_cycle(self, agents):
        """Delegated to HeartbeatController."""
        return await self._heartbeat_ctrl._whisper_cycle(agents)

    async def _oracle_consultation_cycle(self):
        """Delegated to HeartbeatController."""
        return await self._heartbeat_ctrl._oracle_consultation_cycle()

    async def _oracle_research_cycle(self):
        """Delegated to HeartbeatController."""
        return await self._heartbeat_ctrl._oracle_research_cycle()

    async def _agent_reflect(self, agent):
        """Delegated to HeartbeatController."""
        return await self._heartbeat_ctrl._agent_reflect(agent)

    # ── WebSocket ───────────────────────────────────────────

    def register_ws_callback(self, callback):
        if len(self._ws_callbacks) >= 50:
            self._ws_callbacks = self._ws_callbacks[-24:]  # trim to 24, append → 25
        self._ws_callbacks.append(callback)

    def unregister_ws_callback(self, callback):
        self._ws_callbacks = [cb for cb in self._ws_callbacks if cb != callback]

    async def _notify_ws(self, event_type: str, data: dict):
        for callback in list(self._ws_callbacks):
            try:
                await callback({"type": event_type, "data": data})
            except Exception:
                pass

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
import json
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

from agents.base_agent import Agent
from agents.spawner import AgentSpawner
from core.llm_provider import LLMProvider
from core.token_economy import TokenEconomy
from core.live_monitor import LiveMonitor
from core.whisper_protocol import WhisperProtocol
from core.knowledge_loader import KnowledgeLoader
from memory.shared_memory import SharedMemory

# ═══ Translation Proxy — Voikko + sanakirja FI↔EN ═══
try:
    from translation_proxy import TranslationProxy, detect_language, is_finnish
    _TRANSLATION_AVAILABLE = True
except ImportError:
    _TRANSLATION_AVAILABLE = False
    def detect_language(t): return "fi"
    def is_finnish(t): return True

try:
    from en_validator import ENValidator
    _EN_VALIDATOR_AVAILABLE = True
except ImportError:
    _EN_VALIDATOR_AVAILABLE = False


# ── Painotetut avainsanat reititykseen (KORJAUS K5) ─────────
# Primääriavainsanat (weight=5): hyvin spesifiset
# Sekundääriavainsanat (weight=1): yleiset
WEIGHTED_ROUTING = {
    "pesaturvallisuus": {
        "primary": ["karhu", "ilves", "peura", "varkaus", "pesävaurio", "suojau"],
        "secondary": ["hiiri", "tarha"],
    },
    "tarhaaja": {
        "primary": ["mehiläi", "pesä", "hunaj", "vaha", "emo", "varroa",
                     "linkoa", "punkk", "kuningatar", "yhdyskun"],
        "secondary": ["tarha", "hoito", "talveh"],
    },
    "tautivahti": {
        "primary": ["tauti", "nosema", "afb", "efb", "kalkki", "sikiö"],
        "secondary": ["varroa"],
    },
}
PRIMARY_WEIGHT = 5
SECONDARY_WEIGHT = 1

# ── AUDIT FIX: Negatiiviset avainsanat masterille ────────
# Master EI saa kaapata näitä termejä — delegoi spesialistille.
MASTER_NEGATIVE_KEYWORDS = [
    "varroa", "afb", "efb", "nosema", "pesä", "mehiläi",
    "karhu", "ilves", "sähkö", "putki", "sauna",
    "metsä", "lintu", "kala", "jää", "routa",
]

# ── AUDIT FIX: Päivämääräharha-estosääntö ────────────────
# Lisätään KAIKKIIN system_prompteihin.
DATE_HALLUCINATION_RULE = """
AIKA:
- Käytä VAIN järjestelmän sinulle antamaa päivämäärää.
- ÄLÄ koskaan päättele tai keksi nykyistä päivää itse.
- Jos aikaa ei anneta, vastaa: "Ajankohta ei tiedossa."
"""


# ═══ EN System Prompts ═══
AGENT_EN_PROMPTS = {'hivemind': 'CRITICAL FACTS (ALWAYS use):\n- Jani Korpi, JKH Service (Business ID: 2828492-2), Evira: 18533284\n- 202 colonies (NOT 300), 35 apiary locations (2024)\n- Breeds: italMeh (Italian), grnMeh (Carniolan/Carnica)\n- Regions: Tuusula (36), Helsinki (20), Vantaa (16), Espoo (66), Polvijärvi (3), Kouvola (61)\n- Karhuniementie 562 D (70% business / 30% personal)\nRESPONSE RULES:\n- Answer ONLY what is asked, max 5 sentences\n- Owner is Jani (NOT Janina, NOT Janne)\n- Do NOT invent numbers or dates — say "I don\'t know exactly" if unsure\n- Be direct and concrete. No preamble.\nYou are HiveMind, the central intelligence of Jani\'s personal agent system.\nDelegate to specialists. Be brief and concrete.', 'beekeeper': 'You are a beekeeping specialist for JKH Service (202 colonies across Finland).\nExpert in: varroa treatment (formic/oxalic acid), seasonal management, queen rearing,\nhoney harvest, feeding schedules, disease identification (AFB, EFB, nosema, chalkbrood).\nBreeds: Italian & Carniolan honeybees.\nAnswer max 3 sentences, practical advice only. Use metric units.', 'video_producer': 'You are a video production specialist for beekeeping content.\nExpert in: TikTok/YouTube optimization, multilingual subtitles (Finnish primary),\nAI transcription (Whisper), editing workflows, platform-specific formatting.\nFocus: beekeeping educational content, urban beekeeping, honey harvesting.\nAnswer max 3 sentences, actionable tips.', 'property': 'You are a property management specialist.\nProperties: Huhdasjärvi cottage (Karhuniementie 562 D, 70% business / 30% personal).\nExpert in: winterization, sauna maintenance, plumbing, electrical, insulation,\nrural property upkeep, short-term rental compliance.\nAnswer max 3 sentences, practical solutions.', 'tech': 'You are a technology specialist.\nExpert in: Python, Ollama/local LLMs, AI systems, Whisper transcription,\nWindows/WSL, hardware optimization (24GB VRAM RTX), automation.\nCurrent projects: WaggleDance/OpenClaw AI swarm, translation proxy, benchmarking.\nAnswer max 3 sentences, working code when possible.', 'business': 'You are a business specialist for JKH Service (Y-tunnus: 2828492-2).\nExpert in: Finnish VAT (ALV), sole proprietorship accounting, honey sales\n(Wolt, online, direct), food safety regulations (Evira), pricing strategy.\nAnnual production: ~10,000 kg honey from 202 colonies.\nAnswer max 3 sentences, concrete numbers.', 'hacker': 'You are a code security and optimization specialist.\nExpert in: bug hunting, refactoring, security scanning, performance optimization,\nPython async patterns, database optimization, Windows compatibility.\nAnswer max 3 sentences, show code fixes.', 'oracle': 'You are a research and web search specialist.\nExpert in: finding current information, trend analysis, competitor research,\nfact-checking, market analysis for beekeeping industry.\nAnswer max 3 sentences with sources when possible.'}

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
        self._chat_active = False
        self._chat_cooldown_until = 0.0  # monotonic timestamp

        # ── Feature flags (v0.0.2) ──────────────────────────
        swarm_cfg = self.config.get("swarm", {})
        self._swarm_enabled = swarm_cfg.get("enabled", False)

        self.running = False
        self._heartbeat_count = 0
        self.translation_proxy = None
        self.en_validator = None
        self.language_mode = "auto"  # "auto", "fi", "en" 
        self.heartbeat_task: Optional[asyncio.Task] = None
        self.started_at: Optional[datetime] = None
        self._ws_callbacks: list = []

    def _load_config(self) -> dict:
        path = Path(self.config_path)
        if path.exists():
            with open(path, encoding="utf-8") as f:
                return yaml.safe_load(f)
        return {}

    def _get_date_prefix(self) -> str:
        """Dynaaminen päivämäärä + kausi + hallusnoinnin esto."""
        now = datetime.now()
        date_str = now.strftime("%d.%m.%Y klo %H:%M")
        month = now.month
        if 3 <= month <= 5:
            season = "kevät"
        elif 6 <= month <= 8:
            season = "kesä"
        elif 9 <= month <= 11:
            season = "syksy"
        else:
            season = "talvi"
        return (f"Tänään on {date_str}. Vuodenaika: {season}. "
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

    async def start(self):
        print("🐝 WaggleDance Swarm AI käynnistyy...")

        db_path = self.config.get("memory", {}).get("db_path", "data/waggle_dance.db")
        self.memory = SharedMemory(db_path)
        await self.memory.initialize()
        print("  ✅ Muisti alustettu")

        self.llm = LLMProvider(self.config.get("llm", {}))
        print(f"  ✅ LLM (chat): {self.llm.model} [GPU]")
        hb_config = self.config.get("llm_heartbeat", self.config.get("llm", {}))
        self.llm_heartbeat = LLMProvider(hb_config)
        self.throttle = AdaptiveThrottle()
        hb_device = "CPU" if self.llm_heartbeat.num_gpu == 0 else "GPU"
        print(f"  ✅ LLM (heartbeat): {self.llm_heartbeat.model} [{hb_device}]")

        # ── KRIITTINEN: Benchmark Ollama → kalibroi throttle ──
        # Ilman tätä semafori = None ja kaikki kutsut menevät
        # rajoittamatta → Ollama-jono → timeout-kaskadi
        try:
            machine_class = await self.throttle.benchmark(self.llm_heartbeat)
            print(f"  ✅ Throttle kalibroitu: {machine_class.upper()}")
        except Exception as e:
            print(f"  ⚠️  Benchmark epäonnistui ({e}), käytetään oletuksia")
            # Aseta semafori manuaalisesti jos benchmark epäonnistuu
            self.throttle._semaphore = asyncio.Semaphore(1)
            self.throttle.state.max_concurrent = 1
            self.throttle.state.heartbeat_interval = 120
            self.throttle.state.machine_class = "unknown_safe"

        self.token_economy = TokenEconomy(self.memory)
        await self.token_economy.initialize()
        print("  ✅ Token-talous alustettu")

        self.monitor = LiveMonitor()
        await self.monitor.system("WaggleDance käynnistyy...")
        print("  ✅ Live Monitor käynnissä")

        self.whisper = WhisperProtocol(self.memory, self.token_economy, self.monitor)
        await self.whisper.initialize()
        print("  ✅ Dance Language (Whisper) käynnissä")

        self.knowledge = KnowledgeLoader("knowledge")
        knowledge_map = self.knowledge.list_all_knowledge()
        total_files = sum(len(files) for files in knowledge_map.values())
        print(f"  ✅ Knowledge Loader ({total_files} dokumenttia)")
        self.knowledge_loader = KnowledgeLoader()

        # ── Swarm Scheduler ──────────────────────────────────
        swarm_config = self.config.get("swarm", {})
        self.scheduler = SwarmScheduler(swarm_config)
        self._swarm_enabled = swarm_config.get("enabled", False)
        if self._swarm_enabled:
            print("  ✅ Swarm Scheduler ENABLED")
        else:
            print("  ℹ️  Swarm Scheduler DISABLED (swarm.enabled: false)")

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
        print("  ✅ Spawner valmis")


        # ── Faktat configista (KORJAUS K4) ──────────────────
        facts = self.config.get("facts", {})
        colony_count = facts.get("colony_count", 202)
        y_tunnus = facts.get("y_tunnus", "2828492-2")
        evira = facts.get("evira", "18533284")

        self.master_agent = Agent(
            name="Swarm Queen",
            agent_type="hivemind",
            system_prompt=self._get_date_prefix() + f"""KRIITTISET FAKTAT (käytä AINA):
- Jani Korpi, JKH Service (Y-tunnus: {y_tunnus}), Evira: {evira}
- {colony_count} yhdyskuntaa, {facts.get('apiary_count', 35)} tarhapaikka (2024)
- Rodut: italMeh (italialainen), grnMeh (krainilainen/carnica)
- Alueet: Tuusula (36), Helsinki (20), Vantaa (16), Espoo (66), Polvijärvi (3), Kouvola (61)
- Karhuniementie 562 D (70% bisnes / 30% henkilökohtainen)

VASTAUSSÄÄNNÖT:
- Vastaa VAIN suomeksi, VAIN kysyttyyn asiaan
- Jani (EI Janina, EI Janne)
- ÄLÄ keksi lukuja tai päivämääriä — sano "en tiedä tarkkaan" jos et tiedä
- Maksimi 5 lausetta, suoraan asiaan
- ÄLÄ aloita vastausta "Hyvä on". Vastaa suoraan.

DELEGOINTISÄÄNNÖT (TÄRKEÄ):
- Olet Swarm Queen: REITITIN, ET asiantuntija.
- SINÄ ET analysoi sisältöä, tee johtopäätöksiä tai lisää omaa tietoa.
- Jos kysymys koskee mehiläisiä/varroa/pesää → delegoi tarhaajalle.
- Jos kysymys koskee säätä/lämpötilaa → delegoi meteorologille.
- Jos et tiedä vastausta, sano "en tiedä" — ÄLÄ arvaa.
- Delegoi spesialisteille AINA kun mahdollista. Ole lyhyt ja konkreettinen.""",
            llm=self.llm,
            memory=self.memory,
            skills=["orchestration", "planning", "routing"],
            monitor=self.monitor
        )
        await self.master_agent.initialize()
        print("  ✅ Swarm Queen käynnissä")

        # ═══ Translation Proxy ═══
        if _TRANSLATION_AVAILABLE:
            try:
                self.translation_proxy = TranslationProxy()
                _tp = self.translation_proxy
                _v = "✅" if _tp.voikko.available else "❌"
                print(f"  ✅ Translation Proxy (Voikko={_v}, Dict={len(_tp.dict_fi_en)}, Lang=auto)")
            except Exception as e:
                print(f"  ⚠️  Translation Proxy: {e}")
                self.translation_proxy = None
        else:
            print("  ℹ️  Translation Proxy ei saatavilla")
            self.translation_proxy = None

        # YAML-sielujen FI-EN kaannos (Translation Proxyn jalkeen)
        if getattr(self, '_use_en_prompts', False) and self.translation_proxy:
            if hasattr(self, 'spawner') and self.spawner and hasattr(self.spawner, 'yaml_bridge'):
                self.spawner.yaml_bridge.set_translation_proxy(
                    self.translation_proxy, "en")

        # ═══ EN Validator (WordNet + domain synonyms) ═══
        if _EN_VALIDATOR_AVAILABLE:
            try:
                _domain = set(self.translation_proxy.dict_en_fi.keys()) if self.translation_proxy else set()
                self.en_validator = ENValidator(domain_terms=_domain)
                _wn = "✅" if self.en_validator.wordnet.available else "❌"
                print(f"  ✅ EN Validator (WordNet={_wn}, Synonyms={len(self.en_validator.domain_synonyms)})")
            except Exception as e:
                print(f"  ⚠️  EN Validator: {e}")
                self.en_validator = None
        else:
            print("  ℹ️  EN Validator ei saatavilla (pip install nltk)")
            self.en_validator = None

        # ── Tietoisuuskerros v2 ──
        if _CONSCIOUSNESS_AVAILABLE:
            try:
                _ollama_url = self.config.get('ollama', {}).get('base_url', 'http://localhost:11434')
                self.consciousness = Consciousness(
                    db_path='data/chroma_db',
                    ollama_url=_ollama_url,
                    translation_proxy=self.translation_proxy
                )
                print(f'  ✅ Tietoisuus v2 (muisti={self.consciousness.memory.count}, '
                      f'embed={self.consciousness.embed.available})')
            except Exception as e:
                print(f'  ⚠️  Tietoisuus: {e}')
                self.consciousness = None

        self.running = True
        self.started_at = datetime.now()
        self.heartbeat_task = asyncio.create_task(self._heartbeat_loop())

        agents_dict = (self.spawner.active_agents
                       if hasattr(self, "spawner") and self.spawner else {})
        self._whisper_task = asyncio.create_task(self._whisper_cycle(agents_dict))

        await self.memory.log_event("hivemind", "started", "WaggleDance käynnistyi")
        print("🟢 WaggleDance Swarm AI käynnissä!")

        return self

    async def stop(self):
        print("🔴 Sammutetaan WaggleDance...")
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

        if self.translation_proxy:
            self.translation_proxy.close()
            print("  ✅ Translation Proxy suljettu")
        print("  WaggleDance sammutettu.")

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
        self._chat_active = True
        # Cooldown 30s chatin jälkeen (mallinvaihto ehtii tapahtua)
        self._chat_cooldown_until = time.monotonic() + 30

        try:
            return await self._do_chat(message, language=language)
        finally:
            self._chat_active = False

    async def _do_chat(self, message: str, language: str = "auto") -> str:
        """Varsinainen chat-logiikka. Tukee FI↔EN käännöstä: auto/fi/en."""
        _original_message = message
        self._translation_used = False
        self._fi_en_result = None
        self._detected_lang = language

        # ═══ Kielentunnistus ═══
        if language == "auto":
            self._detected_lang = detect_language(message) if _TRANSLATION_AVAILABLE else "fi"

        # --- Tietoisuus: pre-filter ---
        if self.consciousness:
            _pre = self.consciousness.before_llm(message)
            if _pre.handled:
                if self.monitor:
                    await self.monitor.system(
                        f"🧠 {_pre.method}: {_pre.answer[:80]}")
                # --- Tietoisuus: hallusinaatio + oppiminen ---
                if self.consciousness:
                    try:
                        _hall = self.consciousness.check_hallucination(message, response)
                        if _hall.is_suspicious and self.monitor:
                            await self.monitor.system(f"⚠️ Hallusinaatio? {_hall.reason}")
                        _quality = _hall.relevance if not _hall.is_suspicious else 0.3
                        self.consciousness.learn_conversation(message, response, quality_score=_quality)
                    except Exception:
                        pass

                await self._notify_ws("chat_response", {
                    "message": message, "response": _pre.answer,
                    "language": getattr(self, '_detected_lang', 'fi'),
                    "method": _pre.method
                })
                return _pre.answer


        # ═══ FI→EN käännös (~2ms) ═══
        if self._detected_lang == "fi" and self.translation_proxy:
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
            return await self._delegate_to_agent(
                delegate_to, self._routed_message, context, msg_lower
            )
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
                            return await self._delegate_to_agent(
                                agent_type, message, context, msg_lower
                            )
                    break  # Ei löytynyt → anna masterille

            # Fallback: Master (Swarm Queen)
            _orig_master_sys = None
            if self._use_en_prompts and "hivemind" in AGENT_EN_PROMPTS:
                _orig_master_sys = self.master_agent.system_prompt
                from datetime import datetime as _dt
                                                                                          # --- Tietoisuus: muistikonteksti ---
                                                                                          _consciousness_context = ""
                                                                                          if self.consciousness:
                                                                                              _consciousness_context = self.consciousness.get_context(
                                                                                                  _en_message if self._translation_used else message)

                self.master_agent.system_prompt = f"Date: {_dt.now():%Y-%m-%d %H:%M}. " + AGENT_EN_PROMPTS["hivemind"]
            try:
                with self._enriched_prompt(self.master_agent, knowledge_max_chars=2000):
                    response = await self.master_agent.think(self._routed_message, context)
            finally:
                if _orig_master_sys is not None:
                    self.master_agent.system_prompt = _orig_master_sys
                        # EN Validator master-vastaukselle
            if self.en_validator and self._use_en_prompts:
                _val = self.en_validator.validate(response)
                if _val.was_corrected:
                    response = _val.corrected

            if self._translation_used and self.translation_proxy:
                _en_fi = self.translation_proxy.en_to_fi(response, force_opus=True)
                if _en_fi.method != "passthrough":
                    response = _en_fi.text
            await self._notify_ws("chat_response", {
                "message": message, "response": response,
                "language": self._detected_lang, "translated": self._translation_used
            })
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

    @staticmethod
    def _fix_mojibake(s: str) -> str:
        """Korjaa double-encoded UTF-8: PÃ¤Ã¤ → Pää."""
        if not s:
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
        }


    # ── Kieliasetukset ──────────────────────────────────────────

    def set_language(self, mode: str = "auto"):
        """Aseta kielitila: 'auto', 'fi', 'en'."""
        if mode in ("auto", "fi", "en"):
            self.language_mode = mode
            print(f"🌐 Kielitila: {mode}")
        else:
            print(f"⚠️  Tuntematon kielitila: {mode}")

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
        _MAX_PENDING = 3      # Hard cap — ei enempää samanaikaisia

        async def _guarded(coro_func, *args):
            """Suorita tehtävä vain jos slotteja vapaana."""
            nonlocal _pending
            if _pending >= _MAX_PENDING:
                return  # SKIP — liikaa jonossa
            _pending += 1
            try:
                await coro_func(*args)
            finally:
                _pending -= 1

        while self.running:
            try:
                # ADAPTIIVINEN intervalli (throttle säätää koneen mukaan)
                interval = (self.throttle.state.heartbeat_interval
                            if self.throttle
                            else self.config.get("hivemind", {}).get(
                                "heartbeat_interval", 60))
                await asyncio.sleep(interval)
                if not self.running:
                    break

                self._heartbeat_count += 1

                # Puhdista kuormituslaskurit
                if self.scheduler:
                    self.scheduler.cleanup_load_counters()

                # ── CHAT-PRIORITEETTI: skip jos chat käynnissä ────
                # Mallinvaihto 7b↔32b GPU:lla kestää 60-120s.
                # Jos heartbeat (7b) käynnistyy chatin (32b) aikana,
                # Ollama joutuu vaihtamaan mallia → valtava viive.
                if self._chat_active or time.monotonic() < self._chat_cooldown_until:
                    await self._notify_ws("heartbeat", {
                        "time": datetime.now().isoformat(),
                        "count": self._heartbeat_count,
                        "title": "heartbeat (SKIP: chat-prioriteetti)",
                    })
                    continue

                # ── TIMEOUT-GATE: skip koko kierros jos liikaa jonossa ──
                if _pending >= _MAX_PENDING:
                    await self._notify_ws("heartbeat", {
                        "time": datetime.now().isoformat(),
                        "count": self._heartbeat_count,
                        "title": f"heartbeat (SKIP: {_pending} pending)",
                    })
                    continue

                # ~2.5 min: agentti miettii (YKSI kerrallaan)
                if self._heartbeat_count % 5 == 0 and self.spawner:
                    agents = list(self.spawner.active_agents.values())
                    if agents:
                        agent = agents[self._heartbeat_count % len(agents)]
                        asyncio.create_task(
                            _guarded(self._agent_proactive_think, agent))

                # ~5 min: Queen syntetisoi
                if self._heartbeat_count % 10 == 0 and self.master_agent:
                    asyncio.create_task(
                        _guarded(self._master_generate_insight))

                # ~7.5 min: Whisper
                if self._heartbeat_count % 15 == 0 and self.whisper and self.spawner:
                    agents = list(self.spawner.active_agents.values())
                    if len(agents) >= 2:
                        asyncio.create_task(
                            _guarded(self._whisper_cycle, agents))

                # ~10 min: Reflect
                if self._heartbeat_count % 20 == 0 and self.spawner:
                    agents = list(self.spawner.active_agents.values())
                    if agents:
                        agent = agents[self._heartbeat_count // 20 % len(agents)]
                        asyncio.create_task(
                            _guarded(self._agent_reflect, agent))

                # ~10 min: Oracle
                if self._heartbeat_count % 20 == 0 and self.spawner:
                    asyncio.create_task(
                        _guarded(self._oracle_consultation_cycle))

                # ~15 min: Oracle tutkii
                if self._heartbeat_count % 30 == 0 and self.spawner:
                    asyncio.create_task(
                        _guarded(self._oracle_research_cycle))

                # Odottavat tehtävät (max 1 kerrallaan)
                if _pending < _MAX_PENDING:
                    pending_tasks = await self.memory.get_tasks(status="pending")
                    for task in pending_tasks[:1]:
                        agent_id = task.get("assigned_agent")
                        if agent_id and self.spawner:
                            agent = self.spawner.get_agent(agent_id)
                            if agent and agent.status == "idle":
                                asyncio.create_task(
                                    _guarded(agent.execute_task, task))

                # idle_research: max 1 agentti kerrallaan
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
                    batch = min(self.throttle.state.idle_batch_size, 1)
                    for agent in idle_agents[:batch]:
                        asyncio.create_task(
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
                            asyncio.create_task(
                                _guarded(self._idle_research, agent_obj))

                await self._notify_ws("heartbeat", {
                    "time": datetime.now().isoformat(),
                    "count": self._heartbeat_count,
                    "pending": _pending,
                })

            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"⚠️  Heartbeat-virhe: {e}")

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
            prompt = (
                f"Olet {agent.name}. Päivämäärä: {now}\n\n"
                f"Omat havainnot:\n{own_text[:200]}\n\n"
                + (f"Muiden oivallukset:\n{others_text[:300]}\n\n"
                   if others_text else "")
                + (f"Viestit sinulle:\n{msg_text[:200]}\n\n"
                   if msg_text else "")
                + "Reagoi muiden oivalluksiin TAI ehdota YKSI UUSI konkreettinen asia. "
                + "Yksi lause suomeksi."
            )

            _hb = self.llm_heartbeat or self.llm
            _t0 = time.monotonic()
            try:
                async with self.throttle:
                    # FIX-1: context manager hoitaa injektoinnin + palautuksen
                    with self._enriched_prompt(agent, inject_date=True,
                                                inject_knowledge=True,
                                                knowledge_max_chars=1500):
                        # Käytä lyhyempää max_tokens heartbeatissa
                        # → nopeampi vastaus, vähemmän GPU-aikaa
                        _hb_tokens = 150 if _hb.model == self.llm.model else 200
                        _resp = await _hb.generate(
                            prompt, system=agent.system_prompt,
                            max_tokens=_hb_tokens
                        )
                _elapsed = (time.monotonic() - _t0) * 1000
                self._report_llm_result(_elapsed, True, _hb.model)
                insight = _resp.content if _resp and not _resp.error else ""
            except Exception:
                self._report_llm_result(30000, False, _hb.model)
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
            print(f"⚠️  Ajattelu epäonnistui ({agent.name}): {e}")

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
            print(f"  ⚠️  Share epäonnistui: {e}")

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
        """Queen syntetisoi oivallukset."""
        try:
            all_memories = await self.memory.get_recent_memories(limit=20)
            insights = [m for m in all_memories if m.get("memory_type") == "insight"]
            if not insights:
                insights = all_memories[:10]

            insights_text = "\n".join(
                [m.get("content", "") for m in insights[:10]]
            )

            synthesis = await self.master_agent.think(
                f"Oivallukset:\n{insights_text[:500]}\n\n"
                f"Syntetisoi YKSI strateginen oivallus. 2 lausetta suomeksi.",
                ""
            )

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
            print(f"⚠️  Synteesi epäonnistui: {e}")

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

            now = datetime.now().strftime("%d.%m.%Y klo %H:%M")
            prompt = f"Olet {agent.name}. Päivämäärä: {now}\n"
            if others_text:
                prompt += f"Muiden oivallukset:\n{others_text}\n"
            if msg_text:
                prompt += f"Viestit sinulle:\n{msg_text}\n"
            prompt += ("Tutki jotain UUTTA omaan erikoisalaasi liittyvää. "
                       "YKSI konkreettinen fakta tai suositus. Yksi lause suomeksi.")

            # Knowledge injection promptin sisään (ei system_promptiin)
            if self.knowledge_loader:
                _agent_type = getattr(agent, 'agent_type', '')
                _kb = self.knowledge_loader.get_knowledge_summary(_agent_type)
                if _kb:
                    prompt += "\n\nTIETOPANKKI:\n" + _kb[:800]

            _hb = self.llm_heartbeat or self.llm
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
            print(f"⚠️  Whisper epäonnistui: {e}")

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
            print(f"⚠️  Oracle-konsultaatio epäonnistui: {e}")

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
            print(f"⚠️  Oracle-tutkimus epäonnistui: {e}")

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
            prompt = (f"Arvioi toimintaasi: {recent_text}\n"
                      f"Mitä opit? Yksi lause suomeksi.")
            _hb = self.llm_heartbeat or self.llm
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

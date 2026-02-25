"""
OpenClaw Agent Spawner
======================
Luo ja hallinnoi agenttiarmeijoja.
Jokainen agentti saa huippuluokan system promptin
joka kohdistuu suoraan perustehtävään.
"""

import asyncio
import yaml
from typing import Optional
from pathlib import Path

from agents.base_agent import Agent
from core.yaml_bridge import YAMLBridge
from core.yaml_bridge import YAMLBridge
from agents.hacker_agent import HackerAgent
from agents.oracle_agent import OracleAgent
from core.llm_provider import LLMProvider
from memory.shared_memory import SharedMemory


# ══════════════════════════════════════════════════════════════
# AGENTTIEN ERIKOISTUNEET TEMPLATEIT
# Jokainen on räätälöity Janin tarpeisiin ja agenttityyppiin.
# ══════════════════════════════════════════════════════════════

DEFAULT_TEMPLATES = {

    # ── 🐝 MEHILÄISAGENTTI ──────────────────────────────────
    "beekeeper": {
        "name": "MehiläisAgentti",
        "system_prompt": """Olet MehiläisAgentti — Suomen kokenein mehiläishoitokonsultti.

YDINTEHTÄVÄ: Optimoi JKH Servicen 300 pesän mehiläistarhaus.

OSAAMISALUEESI:
- Pesien vuosikierto: kevättarkastus → kasvukausi → sadonkorjuu → talvehtiminen
- Emohoito: emon ikä, vaihtosykli, parveilu-ennakointi
- Taudit: nosema, varroa, esikotelomätä — tunnistus ja hoito
- Sadonkorjuu: kehysvalinta, linkoaminen, kosteuspitoisuus
- Talvihoito: ruokintamäärät, eristys, tuuletus, hävikit
- Tuotteet: kukkaishunaja, tattarihunaja, vadelma, kennohunaja

JANIN OPERAATIO:
- 202 yhdyskuntaa (35 tarhaa): Helsinki/metro + Kouvola/Huhdasjärvi
- Tuotanto ~10 000 kg/vuosi
- Myyntikanavat: Wolt, R-kioski, suoramyynti
- Liikkuva lingotus (siirrettävä kalusto)
- Sisällöntuotanto: mehiläisvideot TikTok/YouTube

VASTAA AINA:
- Suomeksi, konkreettisesti, max 5 lausetta
- Anna tarkat määrät (kg, kpl, päivät, lämpötilat)
- Huomioi Suomen ilmasto ja kausi
- Ehdota proaktiivisesti kausitoimenpiteitä""",
        "skills": ["mehiläishoito", "sadonkorjuu", "tautihoito", "talvihoito", "tuoteoptimointi"],
        "auto_spawn": True,
    },

    # ── 🎬 VIDEOAGENTTI ─────────────────────────────────────
    "video_producer": {
        "name": "VideoAgentti",
        "system_prompt": """Olet VideoAgentti — sosiaalisen median ja videosisällön strategi.

YDINTEHTÄVÄ: Kasvata Janin mehiläisaiheista some-läsnäoloa.

OSAAMISALUEESI:
- TikTok-algoritmi: hook 3s, retention, trending äänet, julkaisuajat
- YouTube: SEO, thumbnailit, kappaleet, end screenst, playlists
- Editointi: leikkaus, tekstitys, äänisuunnittelu, värimäärittely
- Whisper-transkriptio → monikieliset tekstitykset
- Sisältökalenteri ja julkaisustrategia
- Yhteisön rakentaminen ja vuorovaikutus

JANIN KANAVAT:
- TikTok: lyhyet mehiläisvideot (15-60s), behind the scenes
- YouTube: pidempi opetussisältö, dokumentointi
- Erikoisuus: aito suomalainen mehiläishoito, luonto, vuodenajat
- Tekniikka: Whisper-transkriptiot, AI-tekstitykset suomi→englanti→muut

VASTAA AINA:
- Suomeksi, konkreettiset toimenpiteet
- Anna tarkat metriikat (julkaisuaika, pituus, hashtagit)
- Ehdota sisältöideoita kauteen sopien
- Huomioi mehiläishoitokalenteri sisällössä""",
        "skills": ["tiktok", "youtube", "editointi", "tekstitys", "some-strategia", "whisper"],
        "auto_spawn": True,
    },

    # ── 🏡 KIINTEISTÖAGENTTI ────────────────────────────────
    "property": {
        "name": "KiinteistöAgentti",
        "system_prompt": """Olet KiinteistöAgentti — kiinteistöverotuksen ja -hallinnan asiantuntija.

YDINTEHTÄVÄ: Optimoi Korvenrannan kiinteistön verotus ja hallinta.

OSAAMISALUEESI:
- Kiinteistöverotus: verotusarvon muodostuminen, oikaisut, valitukset
- Hallintaoikeus: pidätetty käyttöoikeus, lahjavero, sukupolvenvaihdos
- Yritys/yksityiskäyttö: ALV-vähennykset, käyttösuhde, kirjanpito
- Rakennusluvat, kaavoitus, rantarakentaminen
- Vakuutukset: yhdistetty yritys+vapaa-aika
- Arviointi: ammattiarvioitsijat, verottajan arvot vs. käypä arvo

JANIN TILANNE:
- Korvenranta: rantakiinteistö, ostettu vanhemmilta 05/2025
- Pidätetty hallintaoikeus (vanhemmilla käyttöoikeus)
- Yritys-/yksityiskäyttösuhde optimoitava (ALV, sähkö, vakuutus)
- Tehdyt remontit: metallikatto, ulkoverhous, turvasystem
- Yrityssähkösopimus hankittu

VASTAA AINA:
- Suomeksi, lakipykälät ja euromäärät mukaan
- Viittaa Suomen verolakeihin kun relevanttia
- Huomioi deadlinet (verotuspäätökset, oikaisuvaatimukset)
- Ehdota konkreettisia säästötoimia""",
        "skills": ["kiinteistöverotus", "hallintaoikeus", "ALV-optimointi", "vakuutukset", "rakentaminen"],
        "auto_spawn": True,
    },

    # ── 🔧 TECHAGENTTI ──────────────────────────────────────
    "tech": {
        "name": "TechAgentti",
        "system_prompt": """Olet TechAgentti — tekninen asiantuntija ja järjestelmäintegraattori.

YDINTEHTÄVÄ: Optimoi Janin tekninen infrastruktuuri ja uudet projektit.

OSAAMISALUEESI:
- Tesla Model Y: V2L (vehicle-to-load), lataus, OTA-päivitykset
- AI/ML: Whisper-transkriptio, Ollama, paikallinen LLM-ajaminen
- Video pipeline: FFmpeg, automaattinen tekstitys, kääntäminen
- Tietokoneet: watercooling, GPU (GTX 980 x2), VRAM-optimointi
- Sähköjärjestelmät: V2L mehiläistarhalla, akut, invertterit
- IoT: lämpötilaseuranta pesille, etämonitorointi

JANIN TECH-STACK:
- PC: 64GB RAM, dual GTX 980 (4GB VRAM), vesijäähdytys
- Software: Python, FastAPI, Ollama (Gemma3:4b), Whisper
- Tulossa: Tesla Model Y (02-03/2026), V2L-adapteri
- Projektit: OpenClaw, video-pipelinet, mehiläismonitorointi
- OS: Windows (kehitys), Ubuntu (palvelimet)

VASTAA AINA:
- Suomeksi, tekniset spesifikaatiot mukaan
- VRAM-rajoitukset huomioiden (4GB per GPU)
- Konkreettiset konfiguraatiot ja komennot
- Kustannusarviot mukaan""",
        "skills": ["tesla", "v2l", "whisper", "ffmpeg", "ollama", "hardware", "iot"],
        "auto_spawn": True,
    },

    # ── 💰 BISNISAGENTTI ────────────────────────────────────
    "business": {
        "name": "BisnisAgentti",
        "system_prompt": """Olet BisnisAgentti — pienyrittäjän liiketoimintakonsultti.

YDINTEHTÄVÄ: Kasvata JKH Servicen kannattavuutta ja myyntiä.

OSAAMISALUEESI:
- Hinnoittelu: hunajan hinnoittelustrategia, katteen optimointi
- Myyntikanavat: Wolt, R-kioski, suoramyynti, verkkokauppa, torit
- Kirjanpito: ALV, veroilmoitukset, kuittien hallinta
- Markkinointi: brändi, pakkaukset, erottautuminen
- Asiakashallinta: kanta-asiakkaat, tilaussyklit, lahjakorit
- Tuotekehitys: uudet tuotteet, jalostus, sesonkituotteet

JANIN YRITYS:
- JKH Service (Y-tunnus: 2828492-2)
- Tuotteet: kukkaishunaja, tattari, vadelma, kennohunaja
- ~10 000 kg tuotanto/vuosi, 202 yhdyskuntaa (35 tarhaa)
- Myyntikanavat: Wolt (toimitus), R-kioski, suora
- Sisältömarkkinointi: TikTok/YouTube

VASTAA AINA:
- Suomeksi, euroina ja prosentteina
- Konkreettiset toimenpiteet, ei yleistyksiä
- Huomioi sesongit (joulu, kesä, pääsiäinen)
- ROI-laskelma mukaan kun mahdollista""",
        "skills": ["hinnoittelu", "myynti", "kirjanpito", "markkinointi", "tuotekehitys"],
        "auto_spawn": True,
    },

    # ── 🛡️ HACKERAGENTTI ────────────────────────────────────
    "hacker": {
        "name": "HackerAgent",
        "special_class": "hacker",
        "system_prompt": "",  # HackerAgent käyttää omaa HACKER_SYSTEM_PROMPT
        "skills": ["coding", "debugging", "security", "refactoring"],
        "auto_spawn": True,
    },

    # ── 🔮 ORACLEAGENTTI ────────────────────────────────────
    "oracle": {
        "name": "OracleAgent",
        "special_class": "oracle",
        "system_prompt": "",  # OracleAgent käyttää omaa ORACLE_SYSTEM_PROMPT
        "skills": ["web_search", "research", "consultation"],
        "auto_spawn": True,
    },
}


class AgentSpawner:
    """
    Agenttien tehdas ja hallintajärjestelmä.
    """

    def __init__(self, llm: LLMProvider, memory: SharedMemory, config: dict,
                 token_economy=None, monitor=None):
        self.llm = llm
        self.memory = memory
        self.config = config
        self.token_economy = token_economy
        self.monitor = monitor
        self.active_agents: dict[str, Agent] = {}
        # Config yliajaa oletuspohjat
        # ═══ YAML Bridge: lataa 50 agentin templateit ═══
        self.yaml_bridge = YAMLBridge(
            config.get("yaml_bridge", {}).get("agents_dir", "agents")
        )
        yaml_templates = self.yaml_bridge.get_spawner_templates()
        self.agent_templates = {
            **yaml_templates,
            **DEFAULT_TEMPLATES,  # Runtime-erikoiset (hacker, oracle) ylikirjoittavat
            **config.get("agent_templates", {})
        }
        print(f"  📚 Spawner: {len(self.agent_templates)} templateia")
        self.max_agents = config.get("hivemind", {}).get("max_concurrent_agents", 20)

    async def spawn(self, template_name: str, custom_name: str = None,
                    custom_prompt: str = None) -> Agent:
        """Luo uusi agentti pohjasta."""

        # Duplikaattien esto: älä luo jos sama tyyppi on jo aktiivinen
        if template_name in self.active_agents:
            existing = [a for a in self.active_agents.values()
                       if getattr(a, 'agent_type', '') == template_name]
            if existing and not custom_name:
                return existing[0]  # Palauta olemassaoleva

        if len(self.active_agents) >= self.max_agents:
            await self._recycle_oldest_idle()

        template = self.agent_templates.get(template_name)
        if not template:
            raise ValueError(f"Tuntematon pohja: {template_name}. "
                           f"Saatavilla: {list(self.agent_templates.keys())}")

        # Erikoisluokat
        if template.get("special_class") == "hacker":
            agent = HackerAgent(
                llm=self.llm,
                memory=self.memory,
                name=custom_name or template["name"]
            )
            agent.monitor = self.monitor
        elif template.get("special_class") == "oracle" and self.token_economy:
            agent = OracleAgent(
                llm=self.llm,
                memory=self.memory,
                token_economy=self.token_economy,
                name=custom_name or template["name"]
            )
            agent.monitor = self.monitor
        else:

            # ═══ Knowledge injection: yhdistä YAML-tietopankki system_promptiin ═══
            _enriched_prompt = custom_prompt or template["system_prompt"]
            if hasattr(self, 'yaml_bridge') and self.yaml_bridge:
                # Lisää YAML-data (core.yaml metriikat, kausisäännöt, vikatilat)
                _yaml_prompt = self.yaml_bridge.build_system_prompt(template_name)
                if _yaml_prompt and len(_yaml_prompt) > len(template_name) + 20:
                    _enriched_prompt = _yaml_prompt

                # Lisää knowledge-tiedostot (bee_biology, flora, apiaries)
                try:
                    from core.knowledge_loader import KnowledgeLoader
                    _kl = KnowledgeLoader()
                    _kb = _kl.get_knowledge_summary(template_name)
                    if _kb:
                        _enriched_prompt += "\n" + _kb[:2000]
                except Exception:
                    pass

            agent = Agent(
                name=custom_name or template["name"],
                agent_type=template_name,
                system_prompt=_enriched_prompt,
                llm=self.llm,
                memory=self.memory,
                skills=template.get("skills", []),
                config=template,
                monitor=self.monitor
            )

        await agent.initialize()
        self.active_agents[agent.id] = agent

        if self.monitor:
            await self.monitor.agent_spawned(agent.id, agent.name, template_name)

        if self.token_economy:
            welcome = self.config.get("token_economy", {}).get("welcome_bonus", 5)
            await self.token_economy.reward(agent.id, "task_completed",
                                             custom_amount=welcome)

        await self.memory.log_event(
            "spawner", "agent_spawned",
            f"Luotu: {agent.name} ({agent.id})",
            data={"template": template_name}
        )

        return agent

    async def spawn_army(self, template_name: str, count: int = 3,
                         task_descriptions: list[str] = None) -> list[Agent]:
        """Luo agenttiarmeija samasta pohjasta."""
        army = []
        for i in range(count):
            template = self.agent_templates.get(template_name, {})
            base_name = template.get("name", template_name)
            agent = await self.spawn(template_name, custom_name=f"{base_name} #{i+1}")
            if task_descriptions and i < len(task_descriptions):
                await self.memory.add_task(
                    title=task_descriptions[i],
                    assigned_agent=agent.id,
                    priority=3
                )
            army.append(agent)
        return army

    async def spawn_custom(self, name: str, purpose: str,
                           skills: list[str] = None) -> Agent:
        """Luo räätälöity agentti kuvauksen perusteella."""
        prompt_response = await self.llm.generate(
            prompt=f"""Luo system prompt agentille:
Nimi: {name}
Tarkoitus: {purpose}
Taidot: {', '.join(skills or [])}

Vastaa VAIN system promptilla suomeksi."""
        )

        agent = Agent(
            name=name, agent_type="custom",
            system_prompt=prompt_response.content,
            llm=self.llm, memory=self.memory,
            skills=skills or [],
            config={"purpose": purpose, "auto_generated": True}
        )
        await agent.initialize()
        self.active_agents[agent.id] = agent
        return agent

    async def spawn_dynamic_army(self, mission: str, max_agents: int = 5) -> list[Agent]:
        """LLM päättää mitä agentteja tarvitaan."""
        available = list(self.agent_templates.keys())

        response = await self.llm.generate_structured(
            prompt=f"""Missio: {mission}
Pohjat: {available}
Vastaa JSON: {{"agents": [{{"template": "nimi", "name": "nimi", "task": "tehtävä"}}]}}""",
            schema_description="Array of agents"
        )

        army = []
        for spec in response.get("agents", [])[:max_agents]:
            template = spec.get("template", "custom")
            if template in self.agent_templates:
                agent = await self.spawn(template, custom_name=spec.get("name"))
            else:
                continue
            if spec.get("task"):
                await self.memory.add_task(
                    title=spec["task"], assigned_agent=agent.id, priority=5
                )
            army.append(agent)
        return army

    async def kill(self, agent_id: str):
        agent = self.active_agents.pop(agent_id, None)
        if agent:
            await self.memory.update_agent_status(agent_id, "terminated")
            await self.memory.log_event(agent_id, "terminated", f"{agent.name} sammutettu")

    async def kill_all(self):
        for agent_id in list(self.active_agents.keys()):
            await self.kill(agent_id)

    async def _recycle_oldest_idle(self):
        idle = [(aid, a) for aid, a in self.active_agents.items() if a.status == "idle"]
        if idle:
            idle.sort(key=lambda x: x[1].last_active or x[1].created_at)
            await self.kill(idle[0][0])

    def get_agent(self, agent_id: str) -> Optional[Agent]:
        return self.active_agents.get(agent_id)

    def get_all_agents(self) -> list[dict]:
        return [agent.get_stats() for agent in self.active_agents.values()]

    def get_agents_by_type(self, agent_type: str) -> list[Agent]:
        return [a for a in self.active_agents.values() if a.agent_type == agent_type]

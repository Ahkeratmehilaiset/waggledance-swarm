"""
OpenClaw YAML Bridge v1.0
==========================
Yhdistää 50 agentin YAML-tietopohjan runtime-moottoriin.

Lukee agents/*/core.yaml → generoi:
  1. Spawner-templateit (system_prompt + skills + routing keywords)
  2. Whisper-glyyfit (agent_type → emoji)
  3. Reitityssäännöt (avainsanat → agent_type)
  4. Knowledge injection (YAML → system_prompt lisäosa)

Käyttö:
    bridge = YAMLBridge("agents")
    templates = bridge.get_spawner_templates()
    routing = bridge.get_routing_rules()
    glyphs = bridge.get_agent_glyphs()
"""

import yaml
import os
from pathlib import Path
from typing import Optional


# ── Agentti → emoji-glyyfikartta ─────────────────────────────
# Ryhmitelty kategorioittain

AGENT_GLYPH_MAP = {
    # Ydin
    "core_dispatcher": "🧠",
    # Luonto & ympäristö
    "ornitologi": "🦅", "entomologi": "🪲", "fenologi": "🌸",
    "hortonomi": "🌿", "metsanhoitaja": "🌲", "riistanvartija": "🦌",
    "luontokuvaaja": "📸", "pienelain_tuholais": "🐭",
    # Mehiläiset
    "tarhaaja": "🐝", "lentosaa": "🌤️", "parveiluvahti": "🔔",
    "pesalampo": "🌡️", "nektari_informaatikko": "🍯",
    "tautivahti": "🦠", "pesaturvallisuus": "🐻",
    # Vesi & sää
    "limnologi": "🏊", "kalastusopas": "🎣", "kalantunnistaja": "🐟",
    "rantavahti": "🏖️", "jaaasiantuntija": "🧊",
    "meteorologi": "⛅", "myrskyvaroittaja": "⛈️",
    "mikroilmasto": "🌡️", "ilmanlaatu": "💨",
    "routa_maapera": "🪨",
    # Kiinteistö & tekniikka
    "sahkoasentaja": "⚡", "lvi_asiantuntija": "🔧",
    "timpuri": "🪵", "nuohooja": "🔥", "valaistusmestari": "💡",
    "paloesimies": "🚒", "laitehuoltaja": "🔩",
    # Turvallisuus
    "kybervahti": "🛡️", "lukkoseppa": "🔐",
    "pihavahti": "👁️", "privaattisuus": "🕶️",
    # Ruoka & vapaa-aika
    "erakokki": "🍳", "leipuri": "🍞", "ravintoterapeutti": "🥗",
    "saunamajuri": "♨️", "viihdepaallikko": "🎮",
    "elokuva_asiantuntija": "🎬",
    # Hallinto & logistiikka
    "inventaariopaallikko": "📦", "kierratys_jate": "♻️",
    "siivousvastaava": "🧹", "logistikko": "🚛",
    # Tiede
    "tahtitieteilija": "🔭", "valo_varjo": "☀️",
    "matemaatikko_fyysikko": "📐",
    # Runtime-erikoiset (säilytetään)
    "hacker": "⚙️", "oracle": "🔮", "hivemind": "🧠",
}

# ── Avainsanaryhmät reititykseen ──────────────────────────────
# Kukin agentti → lista suomenkielisistä avainsanoista

ROUTING_KEYWORDS = {
    "tarhaaja": ["mehiläi", "pesä", "hunaja", "vaha", "emo", "parvi", "tarha", "hoito", "talveh", "varroa", "linkoa"],
    "lentosaa": ["lentosää", "lämpötila", "tuuli", "sade", "lennätys", "sääennuste"],
    "parveiluvahti": ["parveil", "kuningatar", "emottom"],
    "pesalampo": ["pesälämpö", "kosteus", "lämpötila pesä", "anturi"],
    "nektari_informaatikko": ["satokausi", "nektar", "kukinta", "paino", "linkous"],
    "tautivahti": ["tauti", "nosema", "varroa", "afb", "efb", "kalkki", "sikiö"],
    "pesaturvallisuus": ["karhu", "hiiri", "varkaus", "pesävaurio", "suojau"],

    "ornitologi": ["lintu", "pesintä", "muutto", "laji", "bongaus"],
    "entomologi": ["hyönteis", "pölyttäj", "tuholai", "kuoriai", "perhos"],
    "fenologi": ["fenolog", "kukinta", "lehti", "kasvukausi", "vuodenaik"],
    "hortonomi": ["puutarha", "kasvi", "istutus", "lannoitu", "leikkaus", "kasvihuone"],
    "metsanhoitaja": ["metsä", "harvennus", "taimi", "hakkuu", "puu", "puusto"],
    "riistanvartija": ["riista", "hirvi", "peura", "kettu", "metsästy"],
    "luontokuvaaja": ["kamera", "valokuvau", "ptz", "kuvakulma", "videointi"],
    "pienelain_tuholais": ["myyrä", "hiiri", "rotta", "kärppä", "tuholais"],

    "limnologi": ["järvi", "veden laatu", "happi", "levä", "vesinäyte"],
    "kalastusopas": ["kalastus", "onkimi", "viehekalastus", "verkko", "hauki", "ahven"],
    "kalantunnistaja": ["kalatunnistus", "kalalaji", "alamitt"],
    "rantavahti": ["ranta", "veden korkeus", "tulva", "vesiraja"],
    "jaaasiantuntija": ["jää", "jääpeite", "kantavuus", "avanto", "jäätyminen"],
    "meteorologi": ["sää", "ennuste", "lämpötila", "pilvi", "ilmanpaine", "uv"],
    "myrskyvaroittaja": ["myrsky", "ukkon", "varoitus", "tuulenpuuska"],
    "mikroilmasto": ["mikroilmasto", "paikallinen sää", "lämpösaareke"],
    "ilmanlaatu": ["ilmanlaatu", "hiukkaspitoisuus", "pöly", "pm2.5"],
    "routa_maapera": ["routa", "maaperä", "routaraja", "sulami"],

    "sahkoasentaja": ["sähkö", "sulake", "pistorasia", "rcd", "sähköasennus"],
    "lvi_asiantuntija": ["putki", "vesijohto", "viemäri", "lämmitys", "vesipaine"],
    "timpuri": ["rakenn", "lauta", "hirsi", "sahaus", "terassi", "perustus"],
    "nuohooja": ["nuohous", "savuhormi", "piippu", "tuhka"],
    "valaistusmestari": ["valaistus", "lamppu", "led", "valosuunnittelu"],
    "paloesimies": ["paloturva", "palovaroitin", "häkä", "sammutus", "tulipalo"],
    "laitehuoltaja": ["laitehuolto", "iot", "akku", "verkko", "antenni"],

    "kybervahti": ["tietoturva", "hakkeri", "salasana", "palomuuri", "haavoittuv"],
    "lukkoseppa": ["lukko", "älylukko", "hälytys", "kulunvalvonta"],
    "pihavahti": ["piha", "liiketunnistin", "kameravartiointi", "ihmishavainto"],
    "privaattisuus": ["privaattisuus", "yksityisyys", "gdpr", "kameratallenne"],

    "erakokki": ["ruoka", "resepti", "grillaus", "nuotio", "ruuanlaitto"],
    "leipuri": ["leivonta", "leipä", "kakku", "taikina", "uuni"],
    "ravintoterapeutti": ["ravinto", "kaloreim", "vitamiini", "ruokavalio"],
    "saunamajuri": ["sauna", "löyly", "kiuas", "lauteeet"],
    "viihdepaallikko": ["peli", "lautapeli", "playstation", "ps5", "viihde"],
    "elokuva_asiantuntija": ["elokuva", "leffa", "sarja", "netflix", "yle", "imdb"],

    "inventaariopaallikko": ["varasto", "inventaario", "tarvike", "tilaus"],
    "kierratys_jate": ["kierrätys", "jäte", "kompost", "lajittelu"],
    "siivousvastaava": ["siivous", "puhdistus", "pesu", "desinfiointi"],
    "logistikko": ["reitti", "matka", "kuljetus", "ajoaik", "kilomet"],

    "tahtitieteilija": ["tähti", "revontuli", "planeetta", "tähtitaivas"],
    "valo_varjo": ["varjo", "auringon kulma", "valoisa aika", "paneeli"],
    "matemaatikko_fyysikko": ["laske", "kaava", "tilasto", "fysiikka", "matematiikka"],

    "core_dispatcher": ["tilanne", "yhteenveto", "kaikki", "status", "yleiskatsaus"],
}


class YAMLBridge:
    """
    Yhdistää YAML-tietopohjan runtime-moottoriin.
    Lukee agents/*/core.yaml ja generoi runtime-konfiguraatiot.
    """

    def __init__(self, agents_dir: str = "agents"):
        self.agents_dir = Path(agents_dir)
        self._agents: dict = {}
        self._loaded = False

    def _ensure_loaded(self):
        """Lataa kaikki YAML-agentit (lazy)."""
        if self._loaded:
            return
        if not self.agents_dir.exists():
            print(f"⚠️  Agentit-hakemistoa ei löydy: {self.agents_dir}")
            self._loaded = True
            return

        for d in sorted(os.listdir(str(self.agents_dir))):
            core_path = self.agents_dir / d / "core.yaml"
            if core_path.exists():
                try:
                    with open(core_path, encoding="utf-8") as f:
                        self._agents[d] = yaml.safe_load(f)
                except Exception as e:
                    print(f"⚠️  Virhe ladattaessa {d}: {e}")

        self._loaded = True
        print(f"📚 YAMLBridge: {len(self._agents)} agenttia ladattu")

    # ── System Prompt Generator ───────────────────────────────

    def build_system_prompt(self, agent_id: str) -> str:
        """
        Generoi system_prompt YAML-tiedosta.
        Yhdistää: ASSUMPTIONS + DECISION_METRICS + SEASONAL_RULES + FAILURE_MODES
        """
        self._ensure_loaded()
        agent = self._agents.get(agent_id)
        if not agent:
            return f"Olet {agent_id}-agentti."

        header = agent.get("header", {})
        name = header.get("agent_name", agent_id)
        role = header.get("role", "")
        desc = header.get("description", "")

        parts = [
            f"Olet {name} — {role}.",
            f"\n{desc}" if desc else "",
        ]

        # ASSUMPTIONS → konteksti
        assumptions = agent.get("ASSUMPTIONS", {})
        if assumptions:
            parts.append("\n## OLETUKSET JA KONTEKSTI")
            if isinstance(assumptions, dict):
                for k, v in assumptions.items():
                    parts.append(f"- {k}: {v}")
            elif isinstance(assumptions, list):
                for item in assumptions:
                    if isinstance(item, dict):
                        for k, v in item.items():
                            parts.append(f"- {k}: {v}")
                    else:
                        parts.append(f"- {item}")

        # DECISION_METRICS → konkreettiset kynnysarvot
        metrics = agent.get("DECISION_METRICS_AND_THRESHOLDS", {})
        if metrics:
            parts.append("\n## PÄÄTÖSMETRIIKAT JA KYNNYSARVOT")
            for k, v in metrics.items():
                if isinstance(v, dict):
                    val = v.get("value", "")
                    action = v.get("action", "")
                    src = v.get("source", "")
                    line = f"- **{k}**: {val}"
                    if action:
                        line += f" → TOIMENPIDE: {action}"
                    if src:
                        line += f" [{src}]"
                    parts.append(line)
                else:
                    parts.append(f"- {k}: {v}")

        # SEASONAL_RULES → vuosikello
        seasons = agent.get("SEASONAL_RULES", [])
        if seasons:
            parts.append("\n## VUOSIKELLO")
            for s in seasons:
                season = s.get("season", "?")
                action = s.get("action", s.get("focus", ""))
                parts.append(f"- **{season}**: {action}")

        # FAILURE_MODES → vikatilat
        failures = agent.get("FAILURE_MODES", [])
        if failures:
            parts.append("\n## VIKATILAT")
            for fm in failures:
                mode = fm.get("mode", "?")
                detection = fm.get("detection", "")
                action = fm.get("action", "")
                priority = fm.get("priority", "")
                parts.append(f"- **{mode}**: {detection} → {action} (P{priority})")

        # Compliance
        legal = agent.get("COMPLIANCE_AND_LEGAL", {})
        if legal:
            parts.append("\n## LAKISÄÄTEISET")
            if isinstance(legal, dict):
                for k, v in legal.items():
                    parts.append(f"- {k}: {v}")
            elif isinstance(legal, list):
                for item in legal:
                    if isinstance(item, dict):
                        for k, v in item.items():
                            parts.append(f"- {k}: {v}")
                    else:
                        parts.append(f"- {item}")

        parts.append("\n## VASTAUSOHJEET")
        parts.append("- Vastaa AINA suomeksi")
        parts.append("- Ole konkreettinen: anna numerot, määrät, päivämäärät")
        parts.append("- Viittaa kynnysarvoihin päätöksissä")
        parts.append("- Max 5 lausetta ellei kysytä enemmän")

        return "\n".join(parts)

    # ── Spawner Templates ─────────────────────────────────────

    def get_spawner_templates(self) -> dict:
        """
        Generoi spawner-yhteensopivat templateit kaikille 50 agentille.
        Palautetaan dict joka voidaan suoraan mergeta spawner.agent_templates:iin.
        """
        self._ensure_loaded()
        templates = {}

        for agent_id, agent in self._agents.items():
            header = agent.get("header", {})
            name = header.get("agent_name", agent_id)

            # Skills from YAML
            skills = []
            metrics = agent.get("DECISION_METRICS_AND_THRESHOLDS", {})
            for k in metrics:
                skills.append(k.replace("_", " ")[:30])

            templates[agent_id] = {
                "name": name,
                "system_prompt": self.build_system_prompt(agent_id),
                "skills": skills[:8],  # Max 8 skills
                "auto_spawn": agent_id in (
                    "tarhaaja", "core_dispatcher", "meteorologi",
                    "lentosaa", "pesaturvallisuus"
                ),
                "yaml_source": True,
            }

        return templates

    # ── Routing Rules ─────────────────────────────────────────

    def get_routing_rules(self) -> dict:
        """
        Reitityssäännöt: avainsanat → agent_id.
        Yhteensopiva hivemind.py:n routing_rules-formaatin kanssa.
        """
        return ROUTING_KEYWORDS.copy()

    # ── Whisper Glyphs ────────────────────────────────────────

    def get_agent_glyphs(self) -> dict:
        """Agentti → emoji kartta whisper_protocolille."""
        return AGENT_GLYPH_MAP.copy()

    # ── Knowledge Summary ─────────────────────────────────────

    def get_knowledge_summary(self, agent_id: str, max_chars: int = 2000) -> str:
        """
        YAML-tietopohjan tiivistelmä agentin kontekstiin.
        Käytetään base_agent.py:n _build_context:ssa.
        """
        self._ensure_loaded()
        agent = self._agents.get(agent_id)
        if not agent:
            return ""

        header = agent.get("header", {})
        metrics = agent.get("DECISION_METRICS_AND_THRESHOLDS", {})
        seasons = agent.get("SEASONAL_RULES", [])

        parts = [f"\n## Tietopankki: {header.get('agent_name', agent_id)}"]

        # Top metrics with actions
        for k, v in list(metrics.items())[:5]:
            if isinstance(v, dict) and v.get("action"):
                parts.append(f"  📏 {k}: {v['value']} → {v['action']}")

        # Current season hint
        from datetime import datetime
        month = datetime.now().month
        if 3 <= month <= 5:
            season_name = "Kevät"
        elif 6 <= month <= 8:
            season_name = "Kesä"
        elif 9 <= month <= 11:
            season_name = "Syksy"
        else:
            season_name = "Talvi"

        for s in seasons:
            if s.get("season", "").lower() == season_name.lower():
                parts.append(f"  🗓️ NYT ({season_name}): {s.get('action', '')}")

        result = "\n".join(parts)
        return result[:max_chars]

    # ── Stats ─────────────────────────────────────────────────

    def get_stats(self) -> dict:
        self._ensure_loaded()
        return {
            "total_agents": len(self._agents),
            "agent_ids": list(self._agents.keys()),
            "total_metrics": sum(
                len(a.get("DECISION_METRICS_AND_THRESHOLDS", {}))
                for a in self._agents.values()
            ),
            "total_questions": sum(
                len(a.get("eval_questions", []))
                for a in self._agents.values()
            ),
        }

# WaggleDance Swarm AI • v0.0.1 • Built: 2026-02-22 14:37 EET
# Jani Korpi (Ahkerat Mehiläiset)
# KORJAUS K5: Painotettu reititys (primääri weight=5 vs sekundääri weight=1)
# KORJAUS: Kausilogiikka — vain nykyinen kausi system_promptiin
"""
WaggleDance Swarm AI YAML Bridge v1.0
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
        "tarhaaja": ["mehiläi", "pesä", "hunaja", "vaha", "emo", "parvi", "tarha", "hoito", "talveh", "varroa", "linkoa", "punkk", "linko", "hunaj", "kuningatar", "silm", "siipi", "kammi", "sydän", "toukk", "sikiö", "propolis", "siitepöly", "ruokin", "nektar", "pölyt", "apila", "kehä", "pesiä", "yhdyskun", "kannu", "rotu", "carnica", "buckfast"],
    "lentosaa": ["lentosää", "lämpötila", "tuuli", "sade", "lennätys", "sääennuste"],
    "parveiluvahti": ["parveil", "kuningatar", "emottom"],
    "pesalampo": ["pesälämpö", "kosteus", "lämpötila pesä", "anturi"],
    "nektari_informaatikko": ["satokausi", "nektar", "kukinta", "paino", "linkous"],
    "tautivahti": ["tauti", "nosema", "varroa", "afb", "efb", "kalkki", "sikiö"],
    "pesaturvallisuus": ["karhu", "hiiri", "varkaus", "pesävaurio", "suojau", "peura", "ilves"],

    "ornitologi": ["lintu", "pesintä", "muutto", "laji", "bongaus", "muuttolintu", "pesimä", "birdnet"],
    "entomologi": ["hyönteis", "pölyttäj", "tuholai", "kuoriai", "perhos"],
    "fenologi": ["fenolog", "kukinta", "lehti", "kasvukausi", "vuodenaik"],
        "hortonomi": ["puutarha", "kasvi", "istutus", "lannoitu", "leikkaus", "kasvihuone", "kastel", "lupiini", "vieraslaji", "nurmikko", "kukk", "kukkii", "vadelma", "omena", "apila", "kasvukausi", "siemen"],
    "metsanhoitaja": ["metsä", "harvennus", "taimi", "hakkuu", "puu", "puusto", "myrsky", "tuulituho", "tykky", "oksa"],
    "riistanvartija": ["riista", "hirvi", "peura", "kettu", "metsästy", "susi", "petovaroitus"],
    "luontokuvaaja": ["kamera", "valokuvau", "ptz", "kuvakulma", "videointi", "frigate", "tallenne"],
    "pienelain_tuholais": ["myyrä", "hiiri", "rotta", "kärppä", "tuholais"],

    "limnologi": ["järvi", "veden laatu", "happi", "levä", "vesinäyte"],
    "kalastusopas": ["kalastus", "onkimi", "viehekalastus", "verkko", "hauki", "ahven"],
    "kalantunnistaja": ["kalatunnistus", "kalalaji", "alamitt"],
    "rantavahti": ["ranta", "veden korkeus", "tulva", "vesiraja"],
    "jaaasiantuntija": ["jää", "jääpeite", "kantavuus", "avanto", "jäätyminen", "kanta", "pilkki"],
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

    "tahtitieteilija": ["tähti", "revontuli", "planeetta", "tähtitaivas", "revontul", "aurora"],
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
        self._agents_en: dict = {}  # EN-käännös välimuistissa
        self._loaded = False
        self._translation_proxy = None
        self._language = "fi"  # fi tai en

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
                    # Yritä UTF-8 ensin, sitten UTF-8-BOM, sitten cp1252 fallback
                    raw = None
                    for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
                        try:
                            with open(core_path, encoding=enc) as f:
                                raw = yaml.safe_load(f)
                            break
                        except (UnicodeDecodeError, UnicodeError):
                            continue

                    if raw:
                        # Korjaa mahdollinen double-encoding kaikissa string-arvoissa
                        self._agents[d] = self._fix_encoding_deep(raw)
                except Exception as e:
                    print(f"⚠️  Virhe ladattaessa {d}: {e}")

        self._loaded = True
        print(f"📚 YAMLBridge: {len(self._agents)} agenttia ladattu (lang={self._language})")

    def set_translation_proxy(self, proxy, language: str = "en"):
        """
        Aseta translation proxy ja käännä YAML-agentit tarvittaessa.
        Tunnistaa automaattisesti onko YAML jo kohdekielellä.
        Kutsutaan kerran startissa — käännös tallennetaan välimuistiin.
        """
        self._translation_proxy = proxy
        self._language = language
        if language == "en" and proxy:
            self._ensure_loaded()
            import time
            t0 = time.monotonic()
            translated = 0
            skipped = 0
            for agent_id, agent_data in self._agents.items():
                yaml_lang = self._detect_yaml_language(agent_data)
                if yaml_lang == "en":
                    # YAML on jo englanniksi → käytä sellaisenaan
                    self._agents_en[agent_id] = agent_data
                    skipped += 1
                else:
                    # YAML on suomeksi/muulla → käännä
                    self._agents_en[agent_id] = self._translate_deep(agent_data, proxy)
                    translated += 1
            elapsed = (time.monotonic() - t0) * 1000
            if skipped > 0:
                print(f"  🌐 YAMLBridge: {translated} käännetty EN, {skipped} jo EN ({elapsed:.0f}ms)")
            else:
                print(f"  🌐 YAMLBridge: {translated} agenttia käännetty EN ({elapsed:.0f}ms)")

    @staticmethod
    def _detect_yaml_language(agent_data: dict) -> str:
        """
        Tunnista onko YAML jo englanniksi vai suomeksi.
        Tarkistaa:
          1. Eksplisiittinen 'language: en' kenttä
          2. Header-kentän agent_name kieli
          3. Osion otsikoissa suomenkieliset sanat
        Palauttaa 'en' tai 'fi'.
        """
        # 1. Eksplisiittinen merkintä (paras tapa)
        if agent_data.get("language") == "en":
            return "en"
        header = agent_data.get("header", {})
        if header.get("language") == "en":
            return "en"

        # 2. Kerää kaikki stringit näytteeksi
        sample_strings = []
        # Header
        for key in ("agent_name", "role", "description"):
            v = header.get(key, "")
            if v:
                sample_strings.append(str(v))
        # Assumptions
        assumptions = agent_data.get("ASSUMPTIONS", [])
        if isinstance(assumptions, list):
            for item in assumptions[:3]:
                sample_strings.append(str(item))
        # Seasonal rules
        for rule in agent_data.get("SEASONAL_RULES", [])[:2]:
            if isinstance(rule, dict):
                sample_strings.append(str(rule.get("action", "")))

        if not sample_strings:
            return "fi"  # Oletus: suomi

        text = " ".join(sample_strings).lower()

        # 3. Suomen kielen tunnusmerkit
        fi_markers = ["ä", "ö", "yhdyskunt", "mehiläi", "pesä", "hoito",
                       "tarkist", "ruokint", "talveh", "linkoa", "vuosik",
                       "vastaa aina", "olet "]
        en_markers = ["colony", "colonies", "hive", "treatment", "inspect",
                       "feeding", "winter", "extract", "you are", "always respond"]

        fi_score = sum(1 for m in fi_markers if m in text)
        en_score = sum(1 for m in en_markers if m in text)

        return "en" if en_score > fi_score else "fi"

    def set_language(self, language: str):
        """Vaihda kieli lennossa (fi/en)."""
        self._language = language

    @classmethod
    def _translate_deep(cls, obj, proxy):
        """
        Rekursiivinen FI→EN käännös koko YAML-puulle.
        Käyttää translation_proxy.fi_to_en() jokaiselle stringille.
        """
        if isinstance(obj, str):
            if len(obj) < 3 or obj.startswith("http") or obj.startswith("src:"):
                return obj  # Ohita URL:t, lähdeviitteet, lyhyet
            # Tarkista onko suomea
            try:
                result = proxy.fi_to_en(obj)
                if result and hasattr(result, 'text'):
                    return result.text
            except Exception:
                pass
            return obj
        elif isinstance(obj, dict):
            return {cls._translate_deep(k, proxy) if isinstance(k, str) and len(k) > 20 else k:
                    cls._translate_deep(v, proxy) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [cls._translate_deep(item, proxy) for item in obj]
        return obj

    @staticmethod
    def _fix_mojibake(s: str) -> str:
        """
        Korjaa double-encoded UTF-8 (mojibake).
        "PÃ¤Ã¤mehilÃ¤ishoitaja" → "Päämehiläishoitaja"

        Toimii: jos merkkijono on double-encoded, korjaa.
        Ei riko: jos merkkijono on jo oikein, palauttaa sellaisenaan.
        """
        if not s or not isinstance(s, str):
            return s
        try:
            # Yritä: encode latin-1 → decode utf-8
            # Onnistuu VAIN jos merkkijono on double-encoded
            fixed = s.encode("latin-1").decode("utf-8")
            # Tarkista että tulos on erilainen ja sisältää suomen kirjaimia
            if fixed != s:
                return fixed
        except (UnicodeDecodeError, UnicodeEncodeError):
            pass
        return s

    @classmethod
    def _fix_encoding_deep(cls, obj):
        """Rekursiivinen mojibake-korjaus koko YAML-puulle."""
        if isinstance(obj, str):
            return cls._fix_mojibake(obj)
        elif isinstance(obj, dict):
            return {cls._fix_mojibake(k) if isinstance(k, str) else k:
                    cls._fix_encoding_deep(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [cls._fix_encoding_deep(item) for item in obj]
        return obj

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

        # ── Valitse kieli ja lähde ──
        _en = self._language == "en"
        _src = self._agents_en.get(agent_id, agent) if _en else agent

        if _en:
            _header = _src.get("header", {})
            _name = _header.get("agent_name", name)
            _role = _header.get("role", role)
            _desc = _header.get("description", "")
            parts = [
                f"You are {_name} — {_role}.",
                f"\n{_desc}" if _desc else "",
            ]
        else:
            parts = [
                f"Olet {name} — {role}.",
                f"\n{desc}" if desc else "",
            ]

        # ASSUMPTIONS → konteksti
        assumptions = _src.get("ASSUMPTIONS", {}) if _en else agent.get("ASSUMPTIONS", {})
        if assumptions:
            parts.append("\n## ASSUMPTIONS AND CONTEXT" if _en else "\n## OLETUKSET JA KONTEKSTI")
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
        metrics = _src.get("DECISION_METRICS_AND_THRESHOLDS", {}) if _en else agent.get("DECISION_METRICS_AND_THRESHOLDS", {})
        if metrics:
            parts.append("\n## DECISION METRICS AND THRESHOLDS" if _en else "\n## PÄÄTÖSMETRIIKAT JA KYNNYSARVOT")
            for k, v in metrics.items():
                if isinstance(v, dict):
                    val = v.get("value", "")
                    action = v.get("action", "")
                    src = v.get("source", "")
                    line = f"- **{k}**: {val}"
                    if action:
                        line += f" → {'ACTION' if _en else 'TOIMENPIDE'}: {action}"
                    if src:
                        line += f" [{src}]"
                    parts.append(line)
                else:
                    parts.append(f"- {k}: {v}")

        # SEASONAL_RULES → vuosikello
        seasons = _src.get("SEASONAL_RULES", []) if _en else agent.get("SEASONAL_RULES", [])
        if seasons:
            parts.append("\n## SEASONAL CALENDAR" if _en else "\n## VUOSIKELLO")
            for s in seasons:
                season = s.get("season", "?")
                action = s.get("action", s.get("focus", ""))
                parts.append(f"- **{season}**: {action}")

        # FAILURE_MODES → vikatilat
        failures = _src.get("FAILURE_MODES", []) if _en else agent.get("FAILURE_MODES", [])
        if failures:
            parts.append("\n## FAILURE MODES" if _en else "\n## VIKATILAT")
            for fm in failures:
                mode = fm.get("mode", "?")
                detection = fm.get("detection", "")
                action = fm.get("action", "")
                priority = fm.get("priority", "")
                parts.append(f"- **{mode}**: {detection} → {action} (P{priority})")

        # Compliance
        legal = _src.get("COMPLIANCE_AND_LEGAL", {}) if _en else agent.get("COMPLIANCE_AND_LEGAL", {})
        if legal:
            parts.append("\n## LEGAL AND COMPLIANCE" if _en else "\n## LAKISÄÄTEISET")
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

        if _en:
            parts.append("\n## RESPONSE RULES")
            parts.append("- Always respond in ENGLISH")
            parts.append("- Be concrete: give numbers, quantities, dates")
            parts.append("- Reference thresholds in decisions")
            parts.append("- Max 5 sentences unless asked for more")
            parts.append("- Use exact domain terminology (varroa, AFB, queen, brood)")
        else:
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

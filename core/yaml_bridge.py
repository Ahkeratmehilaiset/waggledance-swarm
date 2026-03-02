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
    # Core
    "core_dispatcher": "🧠",
    # Nature & environment
    "ornithologist": "🦅", "entomologist": "🪲", "phenologist": "🌸",
    "horticulturist": "🌿", "forester": "🌲", "wildlife_ranger": "🦌",
    "nature_photographer": "📸", "pest_control": "🐭",
    # Bees
    "beekeeper": "🐝", "flight_weather": "🌤️", "swarm_watcher": "🔔",
    "hive_temperature": "🌡️", "nectar_scout": "🍯",
    "disease_monitor": "🦠", "hive_security": "🐻",
    # Water & weather
    "limnologist": "🏊", "fishing_guide": "🎣", "fish_identifier": "🐟",
    "shore_guard": "🏖️", "ice_specialist": "🧊",
    "meteorologist": "⛅", "storm_alert": "⛈️",
    "microclimate": "🌦️", "air_quality": "💨",
    "frost_soil": "🪨",
    # Property & technical
    "electrician": "⚡", "hvac_specialist": "🔧",
    "carpenter": "🪵", "chimney_sweep": "🔥", "lighting_master": "💡",
    "fire_officer": "🚒", "equipment_tech": "🔩",
    # Security
    "cyber_guard": "🛡️", "locksmith": "🔐",
    "yard_guard": "👁️", "privacy_guard": "🕶️",
    # Food & leisure
    "wilderness_chef": "🍳", "baker": "🍞", "nutritionist": "🥗",
    "sauna_master": "♨️", "entertainment_chief": "🎮",
    "movie_expert": "🎬",
    # Administration & logistics
    "inventory_chief": "📦", "recycling": "♻️",
    "cleaning_manager": "🧹", "logistics": "🚛",
    # Science
    "astronomer": "🔭", "light_shadow": "☀️",
    "math_physicist": "📐",
    # Runtime-special (retained)
    "hacker": "⚙️", "oracle": "🔮", "hivemind": "🧠",
    # Phase 3: Cottage agents
    "well_water": "🚰", "septic_manager": "🪠", "firewood": "🪓",
    # Phase 3: Home agents
    "energy_advisor": "🔋", "smart_home": "🏠", "indoor_garden": "🌱",
    "apartment_board": "🏢", "delivery_tracker": "📬", "commute_planner": "🚇",
    "noise_monitor": "🔇", "child_safety": "👶", "pet_care": "🐾",
    "budget_tracker": "💰",
    # Phase 3: Factory agents
    "production_line": "🏭", "quality_inspector": "🔍", "shift_manager": "⏰",
    "safety_officer": "🦺", "maintenance_planner": "🛠️", "supply_chain": "🔗",
    "energy_manager": "🔌", "waste_handler": "🗑️", "lab_analyst": "🔬",
    "compliance": "📋", "forklift_fleet": "🚜", "hvac_industrial": "🌬️",
}

# ── Avainsanaryhmät reititykseen ──────────────────────────────
# Kukin agentti → lista suomenkielisistä avainsanoista

ROUTING_KEYWORDS = {
    # ── Mehiläiset (Bees) ──────────────────────────────────────
    "beekeeper": ["mehiläi", "pesä", "hunaj", "vaha", "emo", "varroa",
                   "linkous", "punkki", "kuningatar", "yhdyskun", "hoitokeh",
                   "tarhaaj", "propolis", "siitepöly", "ruokint", "sikiö",
                   "carnica", "buckfast", "kehä", "hoitokenh", "satokenh"],
    "flight_weather": ["lentosää", "lentokeli", "lennätys", "lentolämpö"],
    "swarm_watcher": ["parveilu", "parveil", "emokoppa", "parveilumerkk",
                       "parviloukku", "emontilanne"],
    "hive_temperature": ["pesälämpö", "pesän lämpö", "lämpötila-anturi",
                          "pesäkosteu"],
    "nectar_scout": ["satokau", "nektari", "kukinta", "satopaino",
                      "satokenh", "mehiläislaidun"],
    "disease_monitor": ["tauti", "nosema", "afb", "efb", "kalkki",
                         "sikiötauti", "toukkamätä", "esikotelomätä",
                         "mehiläistauti"],
    "hive_security": ["karhu", "pesävaurio", "suojau", "ilves",
                       "pesävarkau", "eläinvahinko"],

    # ── Luonto ja ympäristö (Nature & environment) ─────────────
    "ornithologist": ["lintu", "pesint", "muuttolintu", "lintulaji",
                       "lintubongau", "lintulauta", "birdnet",
                       "lintuhavainto"],
    "entomologist": ["hyöntei", "pölyttäj", "kovakuoriai", "perhos",
                      "hyönteislaji", "niveljalk"],
    "phenologist": ["fenologi", "lehtien puhkeami", "kasvukau",
                     "vuodenaik", "silmu", "kukinnan alku"],
    "horticulturist": ["puutarh", "kasvi", "istutus", "lannoitu",
                        "pistokas", "kasvihuone", "kastelu", "lupiini",
                        "vadelm", "omena", "apila", "nurmi", "siemen",
                        "kukka", "nurmikko"],
    "forester": ["metsä", "harvennus", "taimi", "hakkuu", "tukkipuu",
                  "myrskytuho", "tuulituho", "kaarnakuoriai", "oksa",
                  "metsänhoito"],
    "wildlife_ranger": ["riista", "hirvi", "kettu", "metsästy", "susi",
                         "petoeläin", "riistakamera"],
    "nature_photographer": ["valokuv", "ptz", "kamerakulma", "nauhoitu",
                             "frigate", "luontokuv"],
    "pest_control": ["myyrä", "jyrsij", "rotta", "kärppä", "tuhoeläin",
                      "hiiritorjun", "loukku"],

    # ── Vesi ja sää (Water & weather) ──────────────────────────
    "limnologist": ["järvi", "vedenlaatu", "happi", "levä", "vesinäyte"],
    "fishing_guide": ["kalastus", "koukku", "virveli", "kalaverkko", "hauki",
                       "ahven", "verkko"],
    "fish_identifier": ["kalatunnist", "kalalaji", "alamitta"],
    "shore_guard": ["ranta", "vedenpinta", "tulva", "vesiraja"],
    "ice_specialist": ["jää", "jääpeite", "kantokyky", "avanto",
                        "jäätyminen", "pilkkimi"],
    "meteorologist": ["sää", "ennuste", "lämpötila", "pilvi",
                       "ilmanpaine", "uv"],
    "storm_alert": ["myrsky", "ukkonen", "varoitus", "tuulenpuuska"],
    "microclimate": ["mikroilmasto", "paikallissää", "lämpösaareke"],
    "air_quality": ["ilmanlaatu", "hiukkaspitoisuu", "pöly", "pm2.5"],
    "frost_soil": ["routa", "maaperä", "routaraja", "sulaminen"],

    # ── Kiinteistö ja tekniikka (Property & technical) ─────────
    "electrician": ["sähkö", "sulake", "pistorasia", "vikavirtasuoja",
                     "sähköasennu"],
    "hvac_specialist": ["putki", "vesiputki", "viemäri", "lämmitys",
                         "vedenpaine"],
    "carpenter": ["rakentami", "lauta", "hirsi", "sahau", "terassi",
                   "perustus"],
    "chimney_sweep": ["nuohou", "savuhormi", "hormi", "tuhka"],
    "lighting_master": ["valaistus", "lamppu", "led", "valosuunnittelu"],
    "fire_officer": ["paloturvallisuus", "palohälytin", "häkävaroitin",
                      "sammutin", "tulipalo"],
    "equipment_tech": ["laitehuolto", "iot", "akku", "antenni"],

    # ── Turvallisuus (Security) ────────────────────────────────
    "cyber_guard": ["tietoturva", "hakkeri", "salasana", "palomuuri",
                     "haavoittuvu"],
    "locksmith": ["lukko", "älylukko", "hälytys", "kulunvalvonta"],
    "yard_guard": ["piha", "liiketunnistin", "kameravalvonta",
                    "ihmistunnist"],
    "privacy_guard": ["yksityisyy", "tietosuoj", "gdpr",
                       "kameranauhoitu"],

    # ── Ruoka ja vapaa-aika (Food & leisure) ───────────────────
    "wilderness_chef": ["ruoka", "resepti", "grillau", "nuotio",
                         "kokkau"],
    "baker": ["leivonta", "leipä", "kakku", "taikina", "uuni"],
    "nutritionist": ["ravitsemu", "kalori", "vitamiini", "dieetti"],
    "sauna_master": ["sauna", "löyly", "kiuas", "laude"],
    "entertainment_chief": ["peli", "lautapeli", "playstation", "ps5",
                             "viihde"],
    "movie_expert": ["elokuva", "leffa", "sarja", "netflix", "yle",
                      "imdb"],

    # ── Hallinto ja logistiikka (Admin & logistics) ────────────
    "inventory_chief": ["inventaari", "tarvike", "tilaus", "varastoseuranta"],
    "recycling": ["kierräty", "komposti", "lajittelu", "pullonpalautu"],
    "cleaning_manager": ["siivou", "pesu", "puhdistus", "desinfiointi"],
    "logistics": ["reitti", "matka", "kuljetus", "ajoaika", "kilometri"],

    # ── Tiede (Science) ────────────────────────────────────────
    "astronomer": ["tähti", "revontulet", "planeetta", "tähtitaivas",
                    "aurora"],
    "light_shadow": ["varjo", "aurinkokulma", "päivänvalo", "paneeli"],
    "math_physicist": ["laskenta", "kaava", "tilasto", "fysiikka",
                        "matematiikka"],

    "core_dispatcher": ["tilanne", "yhteenveto", "kaikki", "status",
                         "yleiskatsaus"],

    # ── Phase 3: Mökki (Cottage) ──────────────────────────────
    "well_water": ["kaivo", "kaivovesi", "rauta", "koliformi",
                    "suodatin", "veden ph"],
    "septic_manager": ["jätevesi", "saostuskaivo", "umpikaivo",
                        "viemäröinti"],
    "firewood": ["polttopuu", "halko", "pilkkominen", "kuivatus",
                  "klapiteline"],
    # ── Phase 3: Koti (Home) ──────────────────────────────────
    "energy_advisor": ["sähköhinta", "spot", "aurinkopaneeli",
                        "lämpöpumppu", "energiansäästö"],
    "smart_home": ["zigbee", "matter", "home assistant", "shelly",
                    "automaatio", "kotiautomaatio"],
    "indoor_garden": ["huonekasvi", "kasvilamppu", "sisäpuutarha",
                       "kasvuvalo"],
    "apartment_board": ["taloyhtiö", "vastike", "putkiremontti",
                         "yhtiökokous"],
    "delivery_tracker": ["paketti", "toimitus", "seuranta", "lähetys",
                          "tulli", "posti"],
    "commute_planner": ["työmatkaliikenne", "hsl", "juna", "pyöräily",
                          "liikenne"],
    "noise_monitor": ["melu", "desibeli", "hiljaisuusaika",
                       "äänieristys"],
    "child_safety": ["lapsi", "vauva", "turvaport", "myrkky",
                      "lapsilukko"],
    "pet_care": ["lemmikki", "koira", "kissa", "eläinlääkäri",
                  "rokotus"],
    "budget_tracker": ["budjetti", "meno", "säästö", "vero", "tuki",
                        "kela"],
    # ── Phase 3: Tehdas (Factory) ─────────────────────────────
    "production_line": ["oee", "sykliaika", "pullonkaula", "tuotanto",
                         "lean"],
    "quality_inspector": ["spc", "cpk", "fmea", "8d", "virhe", "laatu"],
    "shift_manager": ["vuoro", "ylityö", "työvuoro", "vuoronvaihto"],
    "safety_officer": ["työturvallisuus", "riskinarviointi", "suojain",
                        "läheltäpiti"],
    "maintenance_planner": ["kunnossapito", "mtbf", "varaosa",
                             "ennakkohuolto"],
    "supply_chain": ["toimitusketju", "varastosaldo", "toimittaja",
                      "hankinta"],
    "energy_manager": ["tehokerroin", "kompressori", "paineilma",
                        "huipputeho"],
    "waste_handler": ["jätehuolto", "ongelmajäte", "vaarallinen jäte",
                       "ewc"],
    "lab_analyst": ["kalibrointi", "mittaus", "puhdastila", "gmp",
                     "laboratorio"],
    "compliance": ["iso", "auditointi", "sertifiointi", "standardi"],
    "forklift_fleet": ["trukki", "lava", "kuormaus", "lastaus"],
    "hvac_industrial": ["ilmastointi", "hepa", "tuloilma",
                         "puhdastilailmastointi"],
}


class YAMLBridge:
    """
    Yhdistää YAML-tietopohjan runtime-moottoriin.
    Lukee agents/*/core.yaml ja generoi runtime-konfiguraatiot.
    """

    def __init__(self, agents_dir: str = "agents", active_profile: str = None):
        self.agents_dir = Path(agents_dir)
        self._agents: dict = {}
        self._agents_en: dict = {}  # EN-käännös välimuistissa
        self._loaded = False
        self._translation_proxy = None
        self._language = "fi"  # fi tai en
        self._active_profile = active_profile  # gadget|cottage|home|factory or None

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
        Generoi spawner-yhteensopivat templateit agenteille.
        Jos active_profile on asetettu, palauttaa vain profiiliin kuuluvat agentit.
        """
        self._ensure_loaded()
        templates = {}

        for agent_id, agent in self._agents.items():
            # Profile filtering: skip agents not in active profile
            if self._active_profile:
                agent_profiles = agent.get("profiles", [])
                if agent_profiles and self._active_profile not in agent_profiles:
                    continue

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
                    "beekeeper", "core_dispatcher", "meteorologist",
                    "flight_weather", "hive_security"
                ),
                "yaml_source": True,
                "profiles": agent.get("profiles", []),
                "priority": agent.get("priority", "medium"),
            }

        return templates

    def get_profile_stats(self) -> dict:
        """Return agent counts per profile."""
        self._ensure_loaded()
        stats = {"gadget": 0, "cottage": 0, "home": 0, "factory": 0}
        for agent in self._agents.values():
            for p in agent.get("profiles", []):
                if p in stats:
                    stats[p] += 1
        stats["active_profile"] = self._active_profile
        stats["total_agents"] = len(self._agents)
        return stats

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

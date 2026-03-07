"""
Routing constants and data for HiveMind query routing.
Extracted from hivemind.py to reduce file size.

Contains:
- WEIGHTED_ROUTING: Finnish keyword weights per agent (72 agents)
- PRIMARY_WEIGHT / SECONDARY_WEIGHT: scoring constants
- MASTER_NEGATIVE_KEYWORDS: terms master must delegate
- DATE_HALLUCINATION_RULE: injected into all agent prompts
- AGENT_EN_PROMPTS: English system prompts for key agents
"""

# ── Painotetut avainsanat reititykseen (KORJAUS K5) ─────────
# Primääriavainsanat (weight=5): hyvin spesifiset
# Sekundääriavainsanat (weight=1): yleiset
WEIGHTED_ROUTING = {
    "hive_security": {
        "primary": ["karhu", "ilves", "pesävaurio", "pesävarkau", "eläinvahinko"],
        "secondary": ["suojau", "tarha"],
    },
    "beekeeper": {
        "primary": ["mehiläi", "pesä", "hunaj", "vaha", "emo", "varroa",
                     "linkous", "punkk", "kuningatar", "yhdyskun"],
        "secondary": ["tarha", "hoito", "talveh", "carnica", "buckfast"],
    },
    "disease_monitor": {
        "primary": ["tauti", "nosema", "afb", "efb", "kalkki", "sikiötauti",
                     "toukkamätä", "esikotelomätä", "mehiläistauti"],
        "secondary": ["varroa"],
    },
    "swarm_watcher": {
        "primary": ["parveilu", "parveil", "emokoppa", "parviloukku"],
        "secondary": ["emontilanne"],
    },
    "meteorologist": {
        "primary": ["sää", "ennuste", "ilmanpaine"],
        "secondary": ["lämpötila", "pilvi", "uv"],
    },
    "ornithologist": {
        "primary": ["lintu", "lintulaji", "lintubongau", "lintuhavainto"],
        "secondary": ["pesint", "muuttolintu", "birdnet"],
    },
    "forester": {
        "primary": ["metsä", "metsänhoito", "hakkuu", "harvennus"],
        "secondary": ["taimi", "tukkipuu", "myrskytuho"],
    },
    "sauna_master": {
        "primary": ["sauna", "löyly", "kiuas"],
        "secondary": ["laude"],
    },
    "electrician": {
        "primary": ["sähkö", "sulake", "vikavirtasuoja", "sähköasennu"],
        "secondary": ["pistorasia"],
    },
    "horticulturist": {
        "primary": ["puutarh", "istutus", "kasvihuone", "lannoitu"],
        "secondary": ["kasvi", "kastelu", "siemen", "nurmi"],
    },
    "wilderness_chef": {
        "primary": ["ruoka", "resepti", "grillau", "kokkau"],
        "secondary": ["nuotio"],
    },
    "nectar_scout": {
        "primary": ["satokau", "nektari", "satopaino", "mehiläislaidun"],
        "secondary": ["kukinta", "satokenh"],
    },
    "flight_weather": {
        "primary": ["lentosää", "lentokeli"],
        "secondary": ["lennätys", "lentolämpö"],
    },
    "hive_temperature": {
        "primary": ["pesälämpö", "pesän lämpö"],
        "secondary": ["pesäkosteu", "lämpötila-anturi"],
    },
    # ── Nature & Environment ──
    "entomologist": {
        "primary": ["hyöntei", "pölyttäj", "perhos", "kovakuoriai"],
        "secondary": ["niveljalk", "hyönteislaji"],
    },
    "phenologist": {
        "primary": ["fenologi", "kasvukau", "kukinnan alku"],
        "secondary": ["vuodenaik", "silmu"],
    },
    "wildlife_ranger": {
        "primary": ["riista", "hirvi", "kettu", "susi", "petoeläin"],
        "secondary": ["metsästy", "riistakamera"],
    },
    "nature_photographer": {
        "primary": ["valokuv", "luontokuv", "kamerakulma"],
        "secondary": ["nauhoitu", "ptz"],
    },
    "pest_control": {
        "primary": ["myyrä", "jyrsij", "rotta", "tuhoeläin"],
        "secondary": ["hiiritorjun", "loukku", "kärppä"],
    },
    # ── Water & Weather ──
    "limnologist": {
        "primary": ["järvi", "vedenlaatu", "levä", "vesinäyte"],
        "secondary": ["happi"],
    },
    "fishing_guide": {
        "primary": ["kalastus", "virveli", "hauki", "kalaverkko"],
        "secondary": ["koukku", "ahven", "verkko"],
    },
    "shore_guard": {
        "primary": ["ranta", "vedenpinta", "tulva"],
        "secondary": ["vesiraja"],
    },
    "ice_specialist": {
        "primary": ["jää", "jääpeite", "kantokyky", "avanto"],
        "secondary": ["jäätyminen", "pilkkimi"],
    },
    "storm_alert": {
        "primary": ["myrsky", "ukkonen", "tuulenpuuska"],
        "secondary": ["varoitus"],
    },
    "microclimate": {
        "primary": ["mikroilmasto", "paikallissää"],
        "secondary": ["lämpösaareke"],
    },
    "air_quality": {
        "primary": ["ilmanlaatu", "hiukkaspitoisuu", "pm2.5"],
        "secondary": ["pöly"],
    },
    "frost_soil": {
        "primary": ["routa", "routaraja", "maaperä"],
        "secondary": ["sulaminen"],
    },
    # ── Property & Technical ──
    "hvac_specialist": {
        "primary": ["putki", "vesiputki", "viemäri", "vedenpaine"],
        "secondary": ["lämmitys"],
    },
    "carpenter": {
        "primary": ["rakentami", "hirsi", "terassi", "perustus"],
        "secondary": ["lauta", "sahau"],
    },
    "chimney_sweep": {
        "primary": ["nuohou", "savuhormi", "hormi"],
        "secondary": ["tuhka"],
    },
    "lighting_master": {
        "primary": ["valaistus", "led", "valosuunnittelu"],
        "secondary": ["lamppu"],
    },
    "fire_officer": {
        "primary": ["paloturvallisuus", "palohälytin", "sammutin"],
        "secondary": ["häkävaroitin", "tulipalo"],
    },
    "equipment_tech": {
        "primary": ["laitehuolto", "iot", "antenni"],
        "secondary": ["akku"],
    },
    # ── Security ──
    "cyber_guard": {
        "primary": ["tietoturva", "hakkeri", "salasana", "palomuuri"],
        "secondary": ["haavoittuvu"],
    },
    "locksmith": {
        "primary": ["lukko", "älylukko", "kulunvalvonta"],
        "secondary": ["hälytys"],
    },
    "yard_guard": {
        "primary": ["piha", "liiketunnistin", "kameravalvonta"],
        "secondary": ["ihmistunnist"],
    },
    "privacy_guard": {
        "primary": ["yksityisyy", "tietosuoj", "gdpr"],
        "secondary": ["kameranauhoitu"],
    },
    # ── Home & Lifestyle ──
    "apartment_board": {
        "primary": ["taloyhtiö", "vastike", "putkiremontti", "yhtiökokous"],
        "secondary": ["remontti-ilmoitu"],
    },
    "smart_home": {
        "primary": ["zigbee", "matter", "home assistant", "shelly"],
        "secondary": ["automaatio", "kotiautomaatio"],
    },
    "indoor_garden": {
        "primary": ["huonekasvi", "kasvilamppu", "sisäpuutarha"],
        "secondary": ["kasvuvalo"],
    },
    "child_safety": {
        "primary": ["lapsi", "vauva", "turvaport", "lapsilukko"],
        "secondary": ["myrkky"],
    },
    "pet_care": {
        "primary": ["lemmikki", "koira", "kissa", "eläinlääkäri"],
        "secondary": ["rokotus"],
    },
    "delivery_tracker": {
        "primary": ["paketti", "toimitus", "seuranta", "lähetys"],
        "secondary": ["tulli", "posti"],
    },
    "commute_planner": {
        "primary": ["työmatkaliikenne", "hsl", "juna"],
        "secondary": ["pyöräily", "liikenne"],
    },
    "noise_monitor": {
        "primary": ["melu", "desibeli", "hiljaisuusaika"],
        "secondary": ["äänieristys"],
    },
    "budget_tracker": {
        "primary": ["budjetti", "meno", "säästö"],
        "secondary": ["vero", "tuki", "kela"],
    },
    "energy_advisor": {
        "primary": ["sähköhinta", "spot", "aurinkopaneeli", "lämpöpumppu"],
        "secondary": ["energiansäästö"],
    },
    # ── Food & Leisure ──
    "baker": {
        "primary": ["leivonta", "leipä", "kakku", "taikina"],
        "secondary": ["uuni"],
    },
    "nutritionist": {
        "primary": ["ravitsemu", "kalori", "vitamiini"],
        "secondary": ["dieetti"],
    },
    "entertainment_chief": {
        "primary": ["peli", "lautapeli", "playstation", "ps5"],
        "secondary": ["viihde"],
    },
    "movie_expert": {
        "primary": ["elokuva", "leffa", "sarja", "netflix"],
        "secondary": ["yle", "imdb"],
    },
    # ── Factory ──
    "production_line": {
        "primary": ["oee", "sykliaika", "pullonkaula", "tuotanto"],
        "secondary": ["lean"],
    },
    "quality_inspector": {
        "primary": ["spc", "cpk", "fmea", "8d", "laatu"],
        "secondary": ["virhe"],
    },
    "shift_manager": {
        "primary": ["vuoro", "ylityö", "työvuoro"],
        "secondary": ["vuoronvaihto"],
    },
    "safety_officer": {
        "primary": ["työturvallisuus", "riskinarviointi", "suojain"],
        "secondary": ["läheltäpiti"],
    },
    "maintenance_planner": {
        "primary": ["kunnossapito", "mtbf", "varaosa"],
        "secondary": ["ennakkohuolto"],
    },
    "supply_chain": {
        "primary": ["toimitusketju", "varastosaldo", "toimittaja"],
        "secondary": ["hankinta"],
    },
    "energy_manager": {
        "primary": ["tehokerroin", "kompressori", "paineilma"],
        "secondary": ["huipputeho"],
    },
    "waste_handler": {
        "primary": ["jätehuolto", "ongelmajäte", "vaarallinen jäte"],
        "secondary": ["ewc"],
    },
    "lab_analyst": {
        "primary": ["kalibrointi", "mittaus", "puhdastila", "gmp"],
        "secondary": ["laboratorio"],
    },
    "compliance": {
        "primary": ["iso", "auditointi", "sertifiointi"],
        "secondary": ["standardi"],
    },
    "forklift_fleet": {
        "primary": ["trukki", "lava", "kuormaus"],
        "secondary": ["lastaus"],
    },
    # ── Cottage ──
    "well_water": {
        "primary": ["kaivo", "kaivovesi", "suodatin"],
        "secondary": ["rauta", "koliformi", "veden ph"],
    },
    "septic_manager": {
        "primary": ["jätevesi", "saostuskaivo", "umpikaivo"],
        "secondary": ["viemäröinti"],
    },
    "firewood": {
        "primary": ["polttopuu", "halko", "pilkkominen"],
        "secondary": ["kuivatus", "klapiteline"],
    },
    # ── Admin & Logistics ──
    "inventory_chief": {
        "primary": ["inventaari", "tarvike", "tilaus"],
        "secondary": ["varastoseuranta"],
    },
    "recycling": {
        "primary": ["kierräty", "komposti", "lajittelu"],
        "secondary": ["pullonpalautu"],
    },
    "cleaning_manager": {
        "primary": ["siivou", "pesu", "puhdistus"],
        "secondary": ["desinfiointi"],
    },
    "logistics": {
        "primary": ["reitti", "matka", "kuljetus"],
        "secondary": ["ajoaika", "kilometri"],
    },
    # ── Science ──
    "astronomer": {
        "primary": ["tähti", "revontulet", "planeetta"],
        "secondary": ["tähtitaivas", "aurora"],
    },
    "light_shadow": {
        "primary": ["varjo", "aurinkokulma", "päivänvalo"],
        "secondary": ["paneeli"],
    },
    "math_physicist": {
        "primary": ["laskenta", "kaava", "tilasto"],
        "secondary": ["fysiikka", "matematiikka"],
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
    "trukki", "kalibrointi", "zigbee", "taloyhtiö", "lemmikki",
    "melu", "jätehuolto", "iso", "auditointi", "kaivo",
    "polttopuu", "gdpr", "nuohou", "paloturvallisuus",
]

# ── AUDIT FIX: Päivämääräharha-estosääntö ────────────────
# Lisätään KAIKKIIN system_prompteihin.
DATE_HALLUCINATION_RULE = """
TIME:
- Use ONLY the date provided by the system.
- NEVER guess or infer the current date yourself.
- If no time is given, respond: "Time unknown."
"""


# ═══ EN System Prompts ═══
AGENT_EN_PROMPTS = {
    'hivemind': (
        'CRITICAL FACTS (ALWAYS use):\n'
        '- Jani Korpi, JKH Service (Business ID: 2828492-2), Evira: 18533284\n'
        '- 202 colonies (NOT 300), 35 apiary locations (2024)\n'
        '- Breeds: italMeh (Italian), grnMeh (Carniolan/Carnica)\n'
        '- Regions: Tuusula (36), Helsinki (20), Vantaa (16), Espoo (66), Polvijärvi (3), Kouvola (61)\n'
        '- Karhuniementie 562 D (70% business / 30% personal)\n'
        'RESPONSE RULES:\n'
        '- Answer ONLY what is asked, max 5 sentences\n'
        "- Owner is Jani (NOT Janina, NOT Janne)\n"
        '- Do NOT invent numbers or dates — say "I don\'t know exactly" if unsure\n'
        '- Be direct and concrete. No preamble.\n'
        "You are HiveMind, the central intelligence of Jani's personal agent system.\n"
        'Delegate to specialists. Be brief and concrete.'
    ),
    'beekeeper': (
        'You are a beekeeping specialist for JKH Service (202 colonies across Finland).\n'
        'Expert in: varroa treatment (formic/oxalic acid), seasonal management, queen rearing,\n'
        'honey harvest, feeding schedules, disease identification (AFB, EFB, nosema, chalkbrood).\n'
        'Breeds: Italian & Carniolan honeybees.\n'
        'Answer max 3 sentences, practical advice only. Use metric units.'
    ),
    'video_producer': (
        'You are a video production specialist for beekeeping content.\n'
        'Expert in: TikTok/YouTube optimization, multilingual subtitles (Finnish primary),\n'
        'AI transcription (Whisper), editing workflows, platform-specific formatting.\n'
        'Focus: beekeeping educational content, urban beekeeping, honey harvesting.\n'
        'Answer max 3 sentences, actionable tips.'
    ),
    'property': (
        'You are a property management specialist.\n'
        'Properties: Huhdasjärvi cottage (Karhuniementie 562 D, 70% business / 30% personal).\n'
        'Expert in: winterization, sauna maintenance, plumbing, electrical, insulation,\n'
        'rural property upkeep, short-term rental compliance.\n'
        'Answer max 3 sentences, practical solutions.'
    ),
    'tech': (
        'You are a technology specialist.\n'
        'Expert in: Python, Ollama/local LLMs, AI systems, Whisper transcription,\n'
        'Windows/WSL, hardware optimization (24GB VRAM RTX), automation.\n'
        'Current projects: WaggleDance/OpenClaw AI swarm, translation proxy, benchmarking.\n'
        'Answer max 3 sentences, working code when possible.'
    ),
    'business': (
        'You are a business specialist for JKH Service (Y-tunnus: 2828492-2).\n'
        'Expert in: Finnish VAT (ALV), sole proprietorship accounting, honey sales\n'
        '(Wolt, online, direct), food safety regulations (Evira), pricing strategy.\n'
        'Annual production: ~10,000 kg honey from 202 colonies.\n'
        'Answer max 3 sentences, concrete numbers.'
    ),
    'hacker': (
        'You are a code security and optimization specialist.\n'
        'Expert in: bug hunting, refactoring, security scanning, performance optimization,\n'
        'Python async patterns, database optimization, Windows compatibility.\n'
        'Answer max 3 sentences, show code fixes.'
    ),
    'oracle': (
        'You are a research and web search specialist.\n'
        'Expert in: finding current information, trend analysis, competitor research,\n'
        'fact-checking, market analysis for beekeeping industry.\n'
        'Answer max 3 sentences with sources when possible.'
    ),
}

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
        "primary": ["karhu", "ilves", "pesävaurio", "pesävarkau", "eläinvahinko",
                     "bear attack", "badger", "mäyrä", "predator", "hive security",
                     "sähköaita", "electric fence"],
        "secondary": ["suojau", "tarha", "apiary protect", "bear"],
    },
    "beekeeper": {
        "primary": ["mehiläi", "hunaj", "vaha", "varroa",
                     "linkous", "punkk", "kuningatar", "yhdyskun",
                     "beehive", "honeybee", "beekeeper", "apiary",
                     "queen bee", "wax foundation", "honey extract",
                     "queen breed", "winterize", "emoro"],
        "secondary": ["tarha", "hoito", "talveh", "carnica", "buckfast",
                       "colony", "brood"],
    },
    "disease_monitor": {
        "primary": ["tauti", "nosema", "afb", "efb", "kalkki", "sikiötauti",
                     "toukkamätä", "esikotelomätä", "mehiläistauti",
                     "nosemosis", "nosemoosi", "foulbrood", "chalkbrood",
                     "bee disease", "oksaalihappo", "oxalic acid"],
        "secondary": ["varroa", "sairau", "oire", "mite treatment",
                       "disease", "treatment"],
    },
    "swarm_watcher": {
        "primary": ["parveilu", "parveil", "emokoppa", "parviloukku",
                     "emokoppi",
                     "swarming", "queen cell", "swarm trap"],
        "secondary": ["emontilanne", "swarm sign"],
    },
    "meteorologist": {
        "primary": ["sääennuste", "säätila", "ilmanpaine",
                     "weather", "forecast", "barometer",
                     "millainen sää", "huomenna sää", "sää huomenna"],
        "secondary": ["pilvi", "uv", "sataako", "sataa",
                       "pakkas", "rain", "sää "],
    },
    "ornithologist": {
        "primary": ["lintu", "lintulaji", "lintubongau", "lintuhavainto",
                     "käen", "pöllöhavaint",
                     "bird", "bird species", "birdwatch",
                     "birds of prey", "owl sighting"],
        "secondary": ["pesint", "muuttolintu", "birdnet", "pöllö", "käki",
                       "petolint", "owl", "cuckoo"],
    },
    "forester": {
        "primary": ["metsänhoito", "hakkuu", "harvennus", "taimikon",
                     "forestry", "logging", "thinning", "tree planting",
                     "forest sustainab", "forest manage"],
        "secondary": ["taimi", "tukkipuu", "myrskytuho", "metsä",
                       "kuusi", "mänty", "spruce", "pine", "seedling"],
    },
    "sauna_master": {
        "primary": ["sauna", "löyly", "kiuas"],
        "secondary": ["laude", "saunavihta", "sauna whisk", "steam"],
    },
    "electrician": {
        "primary": ["sulake", "vikavirtasuoja", "sähköasennu",
                     "sähkövika", "sähkötarkast",
                     "circuit breaker", "electrical", "wiring", "outlet"],
        "secondary": ["pistorasia", "aurinkopaneeli kytkentä",
                       "solar panel wiring", "sähkö"],
    },
    "horticulturist": {
        "primary": ["puutarh", "kasvihuone", "lannoitu",
                     "greenhouse", "garden", "fertiliz", "pruning"],
        "secondary": ["kasvi", "kastelu", "siemen", "nurmi",
                       "omenapuu", "apple tree", "plant disease"],
    },
    "wilderness_chef": {
        "primary": ["eräruoka", "grillau", "kokkau", "savustus",
                     "camp food", "campfire", "smoking food", "outdoor cook",
                     "outdoor smoking", "grill over", "berry picking",
                     "mushroom picking"],
        "secondary": ["nuotio", "retkikeitto", "marjastus"],
    },
    "nectar_scout": {
        "primary": ["satokau", "nektari", "satopaino", "mehiläislaidun",
                     "mesikas", "siitepöly", "satoennuste",
                     "nectar", "pollen", "forage", "honey flow",
                     "harvest forecast"],
        "secondary": ["kukinta", "satokenh", "flowering"],
    },
    "flight_weather": {
        "primary": ["lentosää", "lentokeli", "lentolämpö",
                     "flight weather", "bee flight",
                     "flight temperature", "wind too strong"],
        "secondary": ["lennätys", "lennolle"],
    },
    "hive_temperature": {
        "primary": ["pesälämpö", "pesän lämpö", "pesän kosteu",
                     "siipilämpö",
                     "hive temperature", "hive humidity",
                     "wing thermometer"],
        "secondary": ["pesäkosteu", "lämpötila-anturi",
                       "brood temperature", "cluster temp"],
    },
    # ── Nature & Environment ──
    "entomologist": {
        "primary": ["hyöntei", "pölyttäj", "perhos", "kovakuoriai",
                     "insect", "pollinator", "butterfly", "beetle"],
        "secondary": ["niveljalk", "hyönteislaji", "entomolog"],
    },
    "phenologist": {
        "primary": ["fenologi", "kasvukau", "kukinnan alku",
                     "phenolog", "growing season"],
        "secondary": ["vuodenaik", "silmu", "luonnon herääminen",
                       "nature awaken"],
    },
    "wildlife_ranger": {
        "primary": ["riista", "hirvi", "kettu", "susi", "petoeläin",
                     "wolf", "lynx", "moose", "wildlife", "trail camera"],
        "secondary": ["metsästy", "riistakamera", "hunting",
                       "ilves"],
    },
    "nature_photographer": {
        "primary": ["valokuv", "luontokuv", "kamerakulma",
                     "nature photo", "camera angle"],
        "secondary": ["nauhoitu", "ptz", "exposure", "valotus"],
    },
    "pest_control": {
        "primary": ["myyrä", "jyrsij", "rotta", "tuhoeläin", "hiir",
                     "mouse", "mice", "rat", "pest control",
                     "muurahai", "tuholaist"],
        "secondary": ["hiiritorjun", "loukku", "kärppä", "ants"],
    },
    # ── Water & Weather ──
    "limnologist": {
        "primary": ["järvi", "vedenlaatu", "levä", "vesinäyte",
                     "lake", "algae", "water quality", "sinilev"],
        "secondary": ["happi", "ph", "acidity"],
    },
    "fishing_guide": {
        "primary": ["kalastus", "virveli", "hauki", "kalaverkko",
                     "fishing", "pike", "lure", "viehe", "onki",
                     "trout", "taimen", "spinning rod", "float rod"],
        "secondary": ["koukku", "ahven", "verkko", "perch",
                       "fishing spot"],
    },
    "shore_guard": {
        "primary": ["ranta", "vedenpinta", "tulva", "laituri",
                     "beach", "dock", "water level", "shore",
                     "beach safety"],
        "secondary": ["vesiraja", "uinti", "swimming"],
    },
    "ice_specialist": {
        "primary": ["jääpeite", "kantokyky", "avanto",
                     "jään paksu", "jäätilanne",
                     "ice thickness", "ice bearing", "ice swim",
                     "ice condition"],
        "secondary": ["jäätyminen", "pilkkimi", "jää"],
    },
    "storm_alert": {
        "primary": ["myrsky", "ukkonen", "tuulenpuuska",
                     "storm", "thunder", "lightning"],
        "secondary": ["varoitus", "sähkökatkos", "power outage"],
    },
    "microclimate": {
        "primary": ["mikroilmasto", "paikallissää",
                     "microclimate"],
        "secondary": ["lämpösaareke", "tuulensuoja", "windbreak"],
    },
    "air_quality": {
        "primary": ["ilmanlaatu", "hiukkaspitoisuu", "pm2.5",
                     "air quality", "co2", "hiilidioksidi"],
        "secondary": ["pöly", "hengitysilma", "indoor air"],
    },
    "frost_soil": {
        "primary": ["routa", "routaraja", "maaperä",
                     "frost", "soil", "permafrost"],
        "secondary": ["sulaminen", "maan kosteu", "thaw"],
    },
    # ── Property & Technical ──
    "hvac_specialist": {
        "primary": ["putki", "vesiputki", "viemäri", "vedenpaine",
                     "ilmanvaihto", "lattialämmitys", "ilmalämpöpumppu",
                     "putkivuoto",
                     "pipe", "plumbing", "ventilation", "heat pump",
                     "pipe leak", "underfloor heating"],
        "secondary": ["lämmitys", "underfloor"],
    },
    "carpenter": {
        "primary": ["rakentami", "hirsi", "terassi", "perustus",
                     "deck", "log wall", "timber", "carpent"],
        "secondary": ["lauta", "sahau", "floor board"],
    },
    "chimney_sweep": {
        "primary": ["nuohou", "savuhormi", "hormi",
                     "chimney", "sweep", "flue"],
        "secondary": ["tuhka", "palovaroitin", "smoke detector"],
    },
    "lighting_master": {
        "primary": ["valaistus", "valosuunnittelu",
                     "lighting", "light design"],
        "secondary": ["lamppu", "led", "ulkovalaistus", "outdoor light"],
    },
    "fire_officer": {
        "primary": ["paloturvallisuus", "palohälytin", "sammutin",
                     "häkävaroitin", "häkä",
                     "fire safety", "carbon monoxide", "fire extinguish"],
        "secondary": ["tulipalo", "sammutuspeite", "fire blanket"],
    },
    "equipment_tech": {
        "primary": ["laitehuolto", "iot", "antenni",
                     "iot sensor", "wifi", "device"],
        "secondary": ["akku", "battery", "network range"],
    },
    # ── Security ──
    "cyber_guard": {
        "primary": ["tietoturva", "hakkeri", "salasana", "palomuuri",
                     "cybersecurity", "password", "firewall", "phishing",
                     "vpn", "tietojenkalastelu"],
        "secondary": ["haavoittuvu", "network threat"],
    },
    "locksmith": {
        "primary": ["lukko", "älylukko", "kulunvalvonta", "lukitu",
                     "lukon vaih",
                     "lock", "smart lock", "deadbolt",
                     "lock replacement", "burglar protect"],
        "secondary": ["hälytys", "burglar", "murtosuojaus", "avain"],
    },
    "yard_guard": {
        "primary": ["liiketunnistin", "kameravalvonta", "tunkeilija",
                     "valvontakamera",
                     "surveillance", "motion sensor", "intruder",
                     "surveillance camera"],
        "secondary": ["ihmistunnist", "piha-alue", "yard"],
    },
    "privacy_guard": {
        "primary": ["yksityisyy", "tietosuoj", "gdpr",
                     "privacy", "data protection"],
        "secondary": ["kameranauhoitu", "henkilötie", "personal data"],
    },
    # ── Home & Lifestyle ──
    "apartment_board": {
        "primary": ["taloyhtiö", "vastike", "putkiremontti", "yhtiökokous",
                     "housing cooperative", "pipe renovation",
                     "cooperative meeting"],
        "secondary": ["remontti-ilmoitu"],
    },
    "smart_home": {
        "primary": ["zigbee", "matter", "home assistant", "shelly",
                     "kotiautomaatio", "älytermostaatti",
                     "smart thermostat", "home automation"],
        "secondary": ["automaatio", "kodin automaatio"],
    },
    "indoor_garden": {
        "primary": ["huonekasvi", "kasvilamppu", "sisäpuutarha", "sisäviljely",
                     "houseplant", "indoor garden", "indoor grow",
                     "houseplant care", "plant watering"],
        "secondary": ["kasvuvalo", "led-valo", "led-valot"],
    },
    "child_safety": {
        "primary": ["lapsi", "vauva", "turvaport", "lapsilukko",
                     "lapsiport",
                     "child safety", "baby gate", "childproof",
                     "child gate", "home safety with children"],
        "secondary": ["myrkky", "lasten turva"],
    },
    "pet_care": {
        "primary": ["lemmikki", "koira", "kissa", "eläinlääkäri",
                     "dog", "cat", "pet", "veterinar"],
        "secondary": ["rokotus", "vaccination"],
    },
    "delivery_tracker": {
        "primary": ["paketti", "pakettiseuran", "lähetys",
                     "package", "delivery", "tracking", "shipment",
                     "order arrival", "tilauksen saapum"],
        "secondary": ["tulli", "posti", "toimitus", "seuranta"],
    },
    "commute_planner": {
        "primary": ["työmatkaliikenne", "hsl", "juna", "työmatka",
                     "commute", "train schedule", "public transport"],
        "secondary": ["pyöräily", "liikenne"],
    },
    "noise_monitor": {
        "primary": ["melu", "desibeli", "hiljaisuusaika",
                     "noise", "decibel"],
        "secondary": ["äänieristys", "noise level"],
    },
    "budget_tracker": {
        "primary": ["budjetti", "meno", "säästö",
                     "budget", "expense", "savings"],
        "secondary": ["vero", "tuki", "kela"],
    },
    "energy_advisor": {
        "primary": ["sähköhinta", "spot", "aurinkopaneeli", "lämpöpumppu",
                     "pörssisähkö", "sähkönkulutu", "sähkölasku",
                     "energiatehokkuu",
                     "electricity price", "energy efficiency", "yösähkö",
                     "electricity consumption", "electricity bill",
                     "energy saving"],
        "secondary": ["energiansäästö", "night tariff", "night pricing"],
    },
    # ── Food & Leisure ──
    "baker": {
        "primary": ["leivonta", "leipä", "kakku", "taikina",
                     "baking", "bread", "cake", "dough"],
        "secondary": ["uuni"],
    },
    "nutritionist": {
        "primary": ["ravitsemu", "kalori", "vitamiini", "ruokavalio",
                     "proteiini", "nutrition", "diet", "vitamin",
                     "protein", "mineral"],
        "secondary": ["dieetti", "healthy diet"],
    },
    "entertainment_chief": {
        "primary": ["peli", "lautapeli", "playstation", "ps5",
                     "board game", "game recommend"],
        "secondary": ["viihde", "entertainment"],
    },
    "movie_expert": {
        "primary": ["elokuva", "leffa", "sarja", "netflix",
                     "kaurismäki", "movie", "film", "finnish cinema"],
        "secondary": ["yle", "imdb"],
    },
    # ── Specialty ──
    "compliance": {
        "primary": ["vaatimustenmukaisuus", "evira", "sertifioint",
                     "auditointi", "iso",
                     "compliance", "regulation", "certification",
                     "food safety", "product approval"],
        "secondary": ["säädös", "tuotehyväksyntä", "standardi"],
    },
    "lab_analyst": {
        "primary": ["laboratorio", "analyysi", "näyte", "kosteuspitoisuu",
                     "kalibrointi", "puhdastila", "gmp",
                     "laboratory", "analysis", "sample", "moisture"],
        "secondary": ["mittaus"],
    },
    "math_physicist": {
        "primary": ["lask", "pinta-ala", "yhtälö", "fysiikka",
                     "differentiaal", "painovoim", "tilasto",
                     "calculate", "equation", "physics", "math",
                     "gravity", "area", "triangle", "differential"],
        "secondary": ["kaava", "formula", "matematiikka"],
    },
    "astronomer": {
        "primary": ["tähti", "tähtikuvi", "planeett", "täysikuu",
                     "revontulet",
                     "star", "constellation", "planet", "full moon",
                     "telescope", "star map", "aurora"],
        "secondary": ["astronomi", "tähtitaivas"],
    },
    "fish_identifier": {
        "primary": ["kalantunnist", "mikä kala",
                     "fish identif", "what fish"],
        "secondary": ["kalalaji", "fish species"],
    },
    "logistics": {
        "primary": ["reitti", "ajoaika", "logistiikka", "kuljetus",
                     "route", "driving time", "logistics"],
        "secondary": ["liikenne", "traffic", "kilometri"],
    },
    "cleaning_manager": {
        "primary": ["siivous", "siivou", "puhdistus",
                     "cleaning", "washing", "detergent",
                     "floor wash", "cleaning schedule"],
        "secondary": ["pesuaine", "allergia", "desinfiointi", "pesu"],
    },
    "septic_manager": {
        "primary": ["jätevesi", "saostuskaiv", "septitankk", "umpikaivo",
                     "septic", "wastewater", "grey water",
                     "septic system", "septic tank"],
        "secondary": ["harmaavesi", "puhdistamo", "viemäröinti"],
    },
    "well_water": {
        "primary": ["kaivovesi", "kaivovede", "rautapitoisuu",
                     "well water", "iron content", "well disinfect"],
        "secondary": ["vesianalyysi", "suodatin", "koliformi", "kaivon"],
    },
    "recycling": {
        "primary": ["kierrätys", "kierräty", "lajittelu", "jättei", "komposti",
                     "recycling", "waste sort", "compost",
                     "plastic recycling", "organic waste"],
        "secondary": ["biojäte", "muovin kierrätys", "pullonpalautu"],
    },
    "firewood": {
        "primary": ["polttopuu", "halko", "klapi", "pilkkom",
                     "firewood", "log splitting", "woodpile",
                     "birch log", "splitting"],
        "secondary": ["kuivaus", "kuivatus", "klapiteline"],
    },
    "light_shadow": {
        "primary": ["valo ja varjo", "varjoanalyysi", "auringonvalo",
                     "aurinkokulma", "päivänvalo",
                     "light and shadow", "sunlight direction",
                     "sunlight", "shadow", "light effect"],
        "secondary": ["varjo"],
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
        "primary": ["kunnossapito", "mtbf", "varaosa", "huoltosuunnitelma",
                     "maintenance plan", "roof inspection", "maintenance"],
        "secondary": ["ennakkohuolto", "katon tarkist"],
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
    "forklift_fleet": {
        "primary": ["trukki", "kuormalava", "kuormaus"],
        "secondary": ["lastaus"],
    },
    "inventory_chief": {
        "primary": ["inventaari", "tarvike", "tilaus"],
        "secondary": ["varastoseuranta"],
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

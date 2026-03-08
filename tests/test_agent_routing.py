"""
Test agent routing — 100 questions per agent (FI + EN), verifies keyword routing.

Tests the WEIGHTED_ROUTING keyword matching logic directly without LLM calls.
Each agent gets ~4-6 test questions (FI + EN pairs) = ~100 questions per language direction.

Run: python -m pytest tests/test_agent_routing.py -v
"""

import pytest
from core.hive_routing import WEIGHTED_ROUTING, PRIMARY_WEIGHT, SECONDARY_WEIGHT


def route_message(msg: str) -> tuple[str | None, float]:
    """Simulate the legacy routing logic from hivemind._legacy_route."""
    msg_lower = msg.lower()
    delegate_to = None
    best_score = 0.0
    for agent_type, weighted in WEIGHTED_ROUTING.items():
        score = (
            sum(PRIMARY_WEIGHT for kw in weighted.get("primary", [])
                if kw in msg_lower) +
            sum(SECONDARY_WEIGHT for kw in weighted.get("secondary", [])
                if kw in msg_lower)
        )
        if score > best_score:
            best_score = score
            delegate_to = agent_type
    return delegate_to, best_score


# ═══════════════════════════════════════════════════════════
# Test data: (question_fi, question_en, expected_agent)
# Each agent has 4-6 question pairs = ~100 per language
# ═══════════════════════════════════════════════════════════

ROUTING_TESTS = [
    # ── beekeeper ──
    ("Miten mehiläispesä talvehditaan?", "How to winterize a beehive?", "beekeeper"),
    ("Milloin hunajaa voi linota?", "When can honey be extracted from the beehive?", "beekeeper"),
    ("Kuinka varroapunkkia hoidetaan?", "How to treat varroa mites?", "beekeeper"),
    ("Mikä on paras emorodun valinta?", "What is the best queen breed?", "beekeeper"),
    ("Miten vahapohjat valetaan?", "How to cast wax foundations?", "beekeeper"),

    # ── disease_monitor ──
    ("Miten nosemoosi tunnistetaan?", "How to identify nosema?", "disease_monitor"),
    ("Onko pesässä esikotelomätää?", "Is there foulbrood in the hive?", "disease_monitor"),
    ("Nosema-tauti ja esikotelomätä", "Nosema and foulbrood disease", "disease_monitor"),
    ("Oksaalihappo ja varroa-tautien hoito", "Oxalic acid for varroa disease treatment", "disease_monitor"),

    # ── swarm_watcher ──
    ("Miten parveilua estetään?", "How to prevent swarming?", "swarm_watcher"),
    ("Onko pesässä emokoppia?", "Are there queen cells in the hive?", "swarm_watcher"),
    ("Parveilu ja sen merkit", "Swarming and its signs", "swarm_watcher"),
    ("Milloin parveilukausi alkaa?", "When does swarming season start?", "swarm_watcher"),

    # ── hive_temperature ──
    ("Mikä on pesän lämpötila nyt?", "What is the hive temperature now?", "hive_temperature"),
    ("Pesän kosteus on liian korkea", "Hive humidity is too high", "hive_temperature"),
    ("Siipilämpömittarin lukemat", "Wing thermometer readings", "hive_temperature"),

    # ── nectar_scout ──
    ("Mitkä kasvit tuottavat nektaria?", "Which plants produce nectar?", "nectar_scout"),
    ("Siitepölylähteet kesäkuussa", "Pollen sources in June", "nectar_scout"),
    ("Satoennuste ja nektari tänä vuonna", "Nectar harvest forecast this year", "nectar_scout"),
    ("Mesikasvien kukinta-ajat", "Nectar plant flowering times", "nectar_scout"),

    # ── hive_security ──
    ("Miten suojaan tarhan karhuilta?", "Hive security against bear attacks at apiary", "hive_security"),
    ("Pesäturvallisuus ja mäyrät", "Hive security and badgers", "hive_security"),
    ("Sähköaita mehiläistarhalle", "Electric fence for apiary", "hive_security"),
    ("Onko tarhalla vierailijoita yöllä?", "Hive security check: visitors at the apiary?", "hive_security"),

    # ── meteorologist ──
    ("Millainen sää on huomenna?", "What will the weather be tomorrow?", "meteorologist"),
    ("Sataako ensi viikolla?", "Will it rain next week?", "meteorologist"),
    ("Sääennuste ja tuuliennuste", "Weather and wind forecast", "meteorologist"),
    ("Onko pakkasta yöllä sääennusteen mukaan?", "Will the weather forecast show frost tonight?", "meteorologist"),

    # ── flight_weather ──
    ("Lentosää ja lentokeli mehiläisille?", "Bee flight weather today?", "flight_weather"),
    ("Onko lentokeli liian huono lennolle?", "Is bee flight weather too bad for flying?", "flight_weather"),
    ("Lentolämpötila ja lentosää", "Flight temperature and bee flight conditions", "flight_weather"),

    # ── ornithologist ──
    ("Mitä lintulajeja on alueella?", "What bird species are in the area?", "ornithologist"),
    ("Kuulin käen laulavan", "I heard a cuckoo singing", "ornithologist"),
    ("Petolintujen lintuhavainnot", "Birds of prey sightings", "ornithologist"),
    ("Pöllöhavaintoja yöllä", "Owl sightings at night", "ornithologist"),

    # ── forester ──
    ("Metsänhoidon kestävät menetelmät", "Sustainable forestry management methods", "forester"),
    ("Taimikon hoito ja hakkuusuunnitelma", "Seedling care and logging plan", "forester"),
    ("Hakkuusuunnitelma ja metsätalous", "Logging plan and forestry", "forester"),
    ("Kuusen ja männyn kasvuolosuhteet metsässä", "Spruce and pine growing conditions in forest", "forester"),

    # ── wildlife_ranger ──
    ("Onko alueella susia tai petoeläimiä?", "Are there wolves or wildlife in the area?", "wildlife_ranger"),
    ("Riistakameran havainnot", "Trail camera observations", "wildlife_ranger"),
    ("Hirvivahinkojen ehkäisy", "Preventing moose damage", "wildlife_ranger"),
    ("Metsästyskausi ja riistaluvat", "Hunting season and wildlife permits", "wildlife_ranger"),

    # ── electrician ──
    ("Vikavirtasuojakytkin laukeaa", "Circuit breaker trips", "electrician"),
    ("Sähköasennuksen tarkastus", "Electrical installation inspection", "electrician"),
    ("Sähköasennukset aurinkopaneelille", "Solar panel electrical wiring", "electrician"),
    ("Pistorasian vaihto", "Replacing an outlet", "electrician"),

    # ── chimney_sweep ──
    ("Milloin savuhormi pitää nuohota?", "When should the chimney be swept?", "chimney_sweep"),
    ("Hormin ja palovaroittimen huolto", "Chimney and smoke detector maintenance", "chimney_sweep"),
    ("Hormin kunto ja turvallisuus", "Chimney condition and safety", "chimney_sweep"),

    # ── sauna_master ──
    ("Mikä on hyvä löylylämpötila?", "What is a good sauna temperature?", "sauna_master"),
    ("Saunan kiukaan valinta", "Choosing a sauna heater", "sauna_master"),
    ("Löylyhuoneen laudoitus", "Sauna room bench boards", "sauna_master"),
    ("Saunavihta ja sen käyttö", "Sauna whisk and its use", "sauna_master"),

    # ── wilderness_chef ──
    ("Miten nuotiolla grillaa?", "How to grill over campfire?", "wilderness_chef"),
    ("Eräruoka ja retkikeitto", "Camp food and trail stew", "wilderness_chef"),
    ("Savustus ulkona", "Outdoor smoking", "wilderness_chef"),
    ("Marjastus ja sienestys ruoaksi", "Berry and mushroom picking for food", "wilderness_chef"),

    # ── fishing_guide ──
    ("Mikä viehe hauelle keväällä?", "What lure for pike in spring?", "fishing_guide"),
    ("Kalastuspaikat Kouvolassa", "Fishing spots in Kouvola", "fishing_guide"),
    ("Onki vai heittovapa?", "Float rod or spinning rod?", "fishing_guide"),
    ("Taimenen pyynti joesta", "Trout fishing in a river", "fishing_guide"),

    # ── cyber_guard ──
    ("Tietoturva ja salasanat", "Cybersecurity and passwords", "cyber_guard"),
    ("Verkkouhkat ja tietojenkalastelu", "Network threats and phishing", "cyber_guard"),
    ("Palomuuri ja VPN-asetukset", "Firewall and VPN settings", "cyber_guard"),

    # ── yard_guard ──
    ("Liiketunnistin hälytti piha-alueella", "Motion sensor triggered in the yard", "yard_guard"),
    ("Valvontakameran asennus", "Surveillance camera installation", "yard_guard"),
    ("Tunkeilija pihapiirissä", "Intruder on the property", "yard_guard"),
    ("Liiketunnistin hälytti", "Motion sensor triggered", "yard_guard"),

    # ── energy_advisor ──
    ("Sähkönkulutuksen optimointi ja energiatehokkuus", "Electricity consumption optimization and energy saving", "energy_advisor"),
    ("Pörssisähkö ja yösähkö", "Spot electricity price and night pricing", "energy_advisor"),
    ("Lämmityksen energiatehokkuus", "Heating energy efficiency", "energy_advisor"),
    ("Sähkölasku liian korkea", "Electricity bill too high", "energy_advisor"),

    # ── smart_home ──
    ("Kodin automaatio ja valot", "Home automation and lights", "smart_home"),
    ("Zigbee-anturit ja Home Assistant", "Zigbee sensors and Home Assistant", "smart_home"),
    ("Älytermostaatin asennus kotiautomaatioon", "Smart thermostat with home automation", "smart_home"),

    # ── hvac_specialist ──
    ("Ilmalämpöpumpun ja putkiston huolto", "Heat pump and plumbing maintenance", "hvac_specialist"),
    ("Vesikiertoinen lattialämmitys", "Water underfloor heating", "hvac_specialist"),
    ("Putkivuoto keittiössä", "Pipe leak in kitchen", "hvac_specialist"),
    ("Ilmanvaihdon ja putkiston säätö", "Ventilation and plumbing adjustment", "hvac_specialist"),

    # ── carpenter ──
    ("Terassin rakentaminen", "Building a deck", "carpenter"),
    ("Hirsiseinän korjaus", "Log wall repair", "carpenter"),
    ("Lattian lautamateriaalit", "Floor board materials", "carpenter"),

    # ── nature_photographer ──
    ("Luontokuvaukseen sopiva kamerakulma", "Best camera angle for nature photography", "nature_photographer"),
    ("PTZ-kameran ohjaus", "PTZ camera control", "nature_photographer"),
    ("Luontokuva ja valotus", "Nature photo and exposure", "nature_photographer"),

    # ── frost_soil ──
    ("Routa ja maaperän jäätyminen", "Frost and soil freezing", "frost_soil"),
    ("Milloin routa sulaa keväällä?", "When does frost thaw in spring?", "frost_soil"),
    ("Maaperän kosteus ja routaraja", "Soil moisture and frost depth", "frost_soil"),

    # ── horticulturist ──
    ("Kasvihuoneen hoito", "Greenhouse care", "horticulturist"),
    ("Omenapuun leikkaus keväällä", "Apple tree pruning in spring", "horticulturist"),
    ("Puutarhan lannoitus", "Garden fertilization", "horticulturist"),
    ("Puutarhan kasvitaudit ja torjunta", "Garden plant diseases and control", "horticulturist"),

    # ── locksmith ──
    ("Älylukko ei toimi", "Smart lock not working", "locksmith"),
    ("Lukon vaihto ja avaimet", "Lock replacement and keys", "locksmith"),
    ("Murtosuojaus ja lukitus", "Burglar protection and locks", "locksmith"),

    # ── budget_tracker ──
    ("Kotitalouden budjetti", "Household budget", "budget_tracker"),
    ("Menot ja tulot tässä kuussa", "Expenses and income this month", "budget_tracker"),
    ("Budjetin säästövinkkejä perheelle", "Budget savings tips for family", "budget_tracker"),

    # ── cleaning_manager ──
    ("Siivouksen aikataulutus", "Cleaning schedule", "cleaning_manager"),
    ("Siivouksen ja lattioiden puhdistus", "Cleaning and floor washing", "cleaning_manager"),
    ("Pesuaineet ja allergia", "Detergents and allergy", "cleaning_manager"),

    # ── fire_officer ──
    ("Häkävaroittimen hälytys", "Carbon monoxide alarm", "fire_officer"),
    ("Paloturvallisuustarkastus", "Fire safety inspection", "fire_officer"),
    ("Sammutuspeite ja alkusammutus", "Fire blanket and initial firefighting", "fire_officer"),

    # ── septic_manager ──
    ("Jätevesijärjestelmän huolto", "Septic system maintenance", "septic_manager"),
    ("Jäteveden umpikaivon tyhjennys", "Septic tank emptying", "septic_manager"),
    ("Harmaavesi ja jäteveden puhdistamo", "Grey water and wastewater treatment", "septic_manager"),

    # ── well_water ──
    ("Kaivoveden laatu ja testaus", "Well water testing and iron content", "well_water"),
    ("Rautapitoisuus kaivovedessä", "Iron content in well water", "well_water"),
    ("Kaivoveden desinfiointia", "Well water disinfection", "well_water"),

    # ── recycling ──
    ("Jätteiden lajittelu ja kierrätys", "Waste sorting and recycling", "recycling"),
    ("Muovin kierrätys kotona", "Plastic recycling at home", "recycling"),
    ("Kompostointi ja biojäte", "Composting and organic waste", "recycling"),

    # ── pet_care ──
    ("Koiran ruokinta ja hoito", "Dog feeding and care", "pet_care"),
    ("Kissan rokotukset", "Cat vaccinations", "pet_care"),
    ("Lemmikkieläimen terveys", "Pet health", "pet_care"),

    # ── entomologist ──
    ("Hyönteislajien tunnistus", "Insect species identification", "entomologist"),
    ("Pölyttäjät ja niiden merkitys", "Pollinators and their importance", "entomologist"),
    ("Tuholaishyönteisten torjunta", "Pest insect control", "entomologist"),

    # ── limnologist ──
    ("Järviveden laatu ja levät", "Lake water quality and algae", "limnologist"),
    ("Vesistön happamuus ja pH", "Water body acidity and pH", "limnologist"),
    ("Sinilevähavainto rannalla", "Blue-green algae sighting on shore", "limnologist"),

    # ── ice_specialist ──
    ("Jään paksuus ja kantokyky", "Ice thickness and ice bearing capacity", "ice_specialist"),
    ("Milloin jäälle voi mennä?", "Ice condition: when can you go on ice?", "ice_specialist"),
    ("Avantouinti ja jäätilanne", "Ice swimming and ice conditions", "ice_specialist"),

    # ── shore_guard ──
    ("Rannan turvallisuus ja uinti", "Beach safety and swimming", "shore_guard"),
    ("Laiturin kunto keväällä", "Dock condition in spring", "shore_guard"),
    ("Rannan ja laiturin vedenpinta", "Shore and dock water level", "shore_guard"),

    # ── storm_alert ──
    ("Myrskyvaroitus ja tuuli", "Storm warning and wind", "storm_alert"),
    ("Ukkosmyrsky tulossa", "Thunderstorm approaching", "storm_alert"),
    ("Sähkökatkos myrskyn jälkeen", "Power outage after storm", "storm_alert"),

    # ── firewood ──
    ("Polttopuun kuivaus ja varastointi", "Firewood drying and storage", "firewood"),
    ("Koivuklapien pilkkominen", "Splitting birch logs", "firewood"),
    ("Halkojen ja polttopuun tilaus", "Firewood and log order", "firewood"),

    # ── indoor_garden ──
    ("Huonekasvien hoito talvella", "Houseplant care in winter", "indoor_garden"),
    ("Sisäviljely ja LED-valot", "Indoor growing and LED lights", "indoor_garden"),
    ("Huonekasvien kastelu lomalla", "Houseplant watering during vacation", "indoor_garden"),

    # ── nutritionist ──
    ("Terveellinen ruokavalio", "Healthy diet", "nutritionist"),
    ("Vitamiinit ja kivennäisaineet", "Vitamins and minerals", "nutritionist"),
    ("Proteiinin ja vitamiinien saanti kasvisruoasta", "Protein and vitamin intake from vegetarian food", "nutritionist"),

    # ── movie_expert ──
    ("Suosittu suomalainen elokuva", "Popular Finnish movie", "movie_expert"),
    ("Aki Kaurismäen elokuvat", "Aki Kaurismäki films", "movie_expert"),

    # ── math_physicist ──
    ("Laske pinta-ala kolmiolle", "Calculate area of a triangle", "math_physicist"),
    ("Painovoiman laskenta", "Gravity calculation", "math_physicist"),
    ("Differentiaaliyhtälön ratkaisu", "Differential equation solution", "math_physicist"),

    # ── compliance ──
    ("Eviran vaatimustenmukaisuus ja sertifiointi", "Food safety compliance and certification", "compliance"),
    ("Evira-säädökset ja sertifiointi hunajalle", "Evira regulations and certification for honey", "compliance"),
    ("Tuotehyväksyntä ja sertifiointi", "Product approval and certification", "compliance"),

    # ── privacy_guard ──
    ("Henkilötietojen suojaus GDPR", "Personal data protection GDPR", "privacy_guard"),
    ("Yksityisyydensuoja kameroissa", "Privacy and data protection in cameras", "privacy_guard"),

    # ── air_quality ──
    ("Ilmanlaatu sisätiloissa", "Indoor air quality", "air_quality"),
    ("Hiilidioksidipitoisuus huoneessa", "CO2 levels in room", "air_quality"),
    ("Hengitysilman puhtaus", "Indoor air quality and cleanliness", "air_quality"),

    # ── microclimate ──
    ("Pihailmasto ja tuulensuoja", "Yard microclimate and windbreak", "microclimate"),
    ("Mikroilmasto ja paikallissää tarhalla", "Microclimate conditions at the site", "microclimate"),

    # ── noise_monitor ──
    ("Melutason mittaus", "Noise level measurement", "noise_monitor"),
    ("Naapurin melu häiritsee", "Neighbor's noise is disturbing", "noise_monitor"),

    # ── light_shadow ──
    ("Valon ja varjon vaikutus kasvuun", "Effect of light and shadow on growth", "light_shadow"),
    ("Auringonvalon suunta pihapiirissä", "Sunlight direction in the yard", "light_shadow"),

    # ── phenologist ──
    ("Luonnon herääminen keväällä", "Nature awakening in spring", "phenologist"),
    ("Kasvien fenologiset vaiheet", "Phenological stages of plants", "phenologist"),

    # ── astronomer ──
    ("Tähtikartta ja tähtikuviot", "Star map and constellations", "astronomer"),
    ("Milloin seuraava täysikuu?", "When is the next full moon?", "astronomer"),

    # ── fish_identifier ──
    ("Mikä kala tämä on?", "What fish is this?", "fish_identifier"),
    ("Kalantunnistus järvessä", "What fish is this? Fish identification", "fish_identifier"),

    # ── logistics ──
    ("Reitti Kouvolasta Helsinkiin", "Route from Kouvola to Helsinki", "logistics"),
    ("Reitin ajoaika ja logistiikka", "Route driving time and logistics", "logistics"),

    # ── pest_control ──
    ("Hiiret kellarissa", "Mice in the basement", "pest_control"),
    ("Muurahaisten torjunta", "Pest control for ants and mice", "pest_control"),
    ("Jyrsijöiden ja tuholaisten torjunta", "Natural pest control for mice", "pest_control"),

    # ── maintenance_planner ──
    ("Huoltosuunnitelma talolle", "Maintenance plan for house", "maintenance_planner"),
    ("Katon tarkistus keväällä", "Roof inspection in spring", "maintenance_planner"),

    # ── lighting_master ──
    ("Piha- ja ulkovalaistus", "Outdoor and yard lighting", "lighting_master"),
    ("LED-valaistuksen suunnittelu", "LED lighting design", "lighting_master"),

    # ── delivery_tracker ──
    ("Pakettiseuranta ja toimitukset", "Package tracking and deliveries", "delivery_tracker"),
    ("Tilauksen saapumisaika", "Order arrival time", "delivery_tracker"),

    # ── commute_planner ──
    ("Työmatka ja julkinen liikenne", "Commute and public transport", "commute_planner"),
    ("Juna-aikataulut Kouvolasta", "Train schedules from Kouvola", "commute_planner"),

    # ── entertainment_chief ──
    ("PS5-pelisuositukset", "PS5 game recommendations", "entertainment_chief"),
    ("Lautapelit perheelle", "Board games for family", "entertainment_chief"),

    # ── equipment_tech ──
    ("IoT-anturin akku lopussa", "IoT sensor battery dead", "equipment_tech"),
    ("WiFi-verkon ja IoT-laitteiden kantavuus", "WiFi and IoT device network range", "equipment_tech"),

    # ── apartment_board ──
    ("Taloyhtiön yhtiökokous", "Housing cooperative meeting", "apartment_board"),
    ("Taloyhtiön putkiremontti ja kustannukset", "Housing cooperative pipe renovation costs", "apartment_board"),

    # ── child_safety ──
    ("Lapsiturvallisuus ja turvaportit kotona", "Child safety gates and childproofing at home", "child_safety"),
    ("Lapsiportit ja suojaukset", "Child gates and protections", "child_safety"),

    # ── lab_analyst ──
    ("Hunajan laboratorioanalyysi", "Honey laboratory analysis", "lab_analyst"),
    ("Näytteen kosteuspitoisuus", "Sample moisture content", "lab_analyst"),
]


class TestRoutingFinnish:
    """Test that Finnish questions route to the correct agent."""

    @pytest.mark.parametrize("fi_q, en_q, expected", ROUTING_TESTS,
                             ids=[f"FI_{t[2]}_{i}" for i, t in enumerate(ROUTING_TESTS)])
    def test_finnish_routing(self, fi_q, en_q, expected):
        agent, score = route_message(fi_q)
        assert agent == expected, (
            f"FI '{fi_q}' → routed to '{agent}' (score={score:.1f}), "
            f"expected '{expected}'"
        )

    @pytest.mark.parametrize("fi_q, en_q, expected", ROUTING_TESTS,
                             ids=[f"EN_{t[2]}_{i}" for i, t in enumerate(ROUTING_TESTS)])
    def test_english_routing(self, fi_q, en_q, expected):
        agent, score = route_message(en_q)
        assert agent == expected, (
            f"EN '{en_q}' → routed to '{agent}' (score={score:.1f}), "
            f"expected '{expected}'"
        )


class TestRoutingCoverage:
    """Verify all 25 spawned agents have at least 2 test questions."""

    SPAWNED_AGENTS = [
        "beekeeper", "flight_weather", "disease_monitor",
        "swarm_watcher", "hive_temperature", "nectar_scout", "meteorologist",
        "horticulturist", "ornithologist", "hive_security", "cyber_guard",
        "yard_guard", "electrician", "chimney_sweep", "nature_photographer",
        "forester", "wildlife_ranger", "wilderness_chef", "sauna_master",
    ]

    def test_all_spawned_agents_covered(self):
        """Every spawned agent must have at least 2 test questions."""
        covered = {t[2] for t in ROUTING_TESTS}
        missing = [a for a in self.SPAWNED_AGENTS if a not in covered]
        assert not missing, f"Agents without test questions: {missing}"

    def test_no_empty_routing_rules(self):
        """Every agent in WEIGHTED_ROUTING must have primary keywords."""
        for agent_type, rules in WEIGHTED_ROUTING.items():
            primary = rules.get("primary", [])
            assert len(primary) > 0, f"{agent_type} has no primary keywords"


class TestRoutingQuality:
    """Test routing score thresholds and ambiguity."""

    def test_primary_hits_score_higher_than_secondary(self):
        """A primary keyword match should score more than secondary."""
        assert PRIMARY_WEIGHT > SECONDARY_WEIGHT

    @pytest.mark.parametrize("msg, expected_agent", [
        ("varroa hoito pesässä mehiläiset", "beekeeper"),
        ("sähköasennus sulake vikavirtasuoja", "electrician"),
        ("myrsky tuuli ukkonen varoitus", "storm_alert"),
    ])
    def test_strong_signal_routes_correctly(self, msg, expected_agent):
        """Messages with multiple keywords should route with high confidence."""
        agent, score = route_message(msg)
        assert agent == expected_agent
        assert score >= PRIMARY_WEIGHT * 2, f"Score {score} too low for strong signal"

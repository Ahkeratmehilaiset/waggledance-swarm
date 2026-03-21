#!/usr/bin/env python3
"""Fix 7: Enrich thin Phase 3 agent knowledge files.

Adds 10-20 domain-specific Finnish facts to each Phase 3 agent's
supplementary YAML files. Focus on Finnish regulations, practical
measurements, and actionable thresholds.
"""

import yaml
from pathlib import Path

PROJECT_DIR = Path(__file__).parent.parent
KNOWLEDGE_DIR = PROJECT_DIR / "knowledge"

# Additional facts for each Phase 3 agent's supplementary YAML files
# Format: {agent: {filename: {section: [facts]}}}
ENRICHMENT_DATA = {
    "well_water": {
        "veden_laatu.yaml": {
            "WATER_QUALITY_THRESHOLDS": {
                "manganese_mg_l": {"value": 0.05, "action": ">0.05 mg/l → värimuutos, maku, pyykinpesun tahrat", "source": "STM 1352/2015"},
                "nitrate_mg_l": {"value": 50, "action": ">50 mg/l → terveysriski erityisesti vauvoille", "source": "STM 1352/2015"},
                "fluoride_mg_l": {"value": 1.5, "action": ">1.5 mg/l → hammasfluoroosi, luustovaikutukset", "source": "STM 1352/2015"},
                "radon_bq_l": {"value": 300, "action": ">300 Bq/l → ilmanvaihdon tehostaminen, aktiivihiilisuodatus", "source": "STUK"},
                "alkalinity_mmol_l": {"value": 0.6, "action": "<0.6 mmol/l → vesi syövyttävää, putkien korroosio", "source": "THL"},
                "hardness_dh": {"value": "4-8", "action": "Ihanne 4-8°dH. <2°dH pehmeä, >15°dH kova → kalkinpoisto", "source": "THL"},
                "e_coli_cfu_100ml": {"value": 0, "action": "Yksikin E. coli → keittokehotus, desinfiointi, uusintanäyte", "source": "STM 1352/2015"},
                "sampling_frequency": {"value": "3 vuoden välein", "action": "Vähintään joka 3. vuosi, rankkasateen jälkeen ylimääräinen", "source": "THL"},
            },
        },
        "suodattimet.yaml": {
            "FILTER_TYPES": [
                {"type": "Aktiivihiilisuodatin", "removes": "haju, maku, orgaaniset yhdisteet, radon", "maintenance": "Vaihto 1-2 v", "cost_eur": "200-500"},
                {"type": "Rautasuodatin", "removes": "rauta, mangaani", "maintenance": "Huuhtelu viikoittain, massa 5-10 v", "cost_eur": "800-2000"},
                {"type": "UV-desinfiointi", "removes": "bakteerit, virukset", "maintenance": "Lamppu 1 v, kvartsilasi puhdistus", "cost_eur": "500-1000"},
                {"type": "Käänteisosmoosi (RO)", "removes": "lähes kaikki epäpuhtaudet", "maintenance": "Kalvo 2-3 v", "cost_eur": "300-800"},
                {"type": "Kalkkineutralointi", "removes": "happamuus (nostaa pH)", "maintenance": "Massa 1-2 v", "cost_eur": "500-1500"},
            ],
        },
    },
    "septic_manager": {
        "saadokset.yaml": {
            "FINNISH_REGULATIONS": [
                {"law": "Ympäristönsuojelulaki 527/2014", "requirement": "Jätevesien puhdistusvaatimus haja-asutusalueilla", "source": "YSL"},
                {"law": "Valtioneuvoston asetus 157/2017", "requirement": "Orgaaninen aines -80%, fosfori -70%, typpi -30%", "source": "VNA"},
                {"law": "Siirtymäaika", "requirement": "Ennen 2004 rakennetut: kunnostus 31.10.2019 mennessä (poikkeukset yli 68v)", "source": "VNA"},
                {"law": "Ranta-alue 100m", "requirement": "Tiukemmat vaatimukset vesistön rannalla (<100m)", "source": "YSL"},
            ],
            "SYSTEM_COSTS_EUR": {
                "umpikaivo_emptying": "150-300 €/tyhjennys",
                "biofilter_installation": "5000-8000 €",
                "pienpuhdistamo": "8000-15000 € + huolto 300-500 €/v",
                "maasuodattamo": "5000-10000 € + hoitotarkastus 200 €/v",
            },
        },
        "huolto.yaml": {
            "MAINTENANCE_SCHEDULE": [
                {"task": "Saostuskaivon tyhjennys", "interval": "1-2 kertaa vuodessa", "season": "Kevät ja syksy"},
                {"task": "Umpikaivon tyhjennys", "interval": "Kun 2/3 täynnä, tyypillisesti 3-6 krt/v", "note": "Seuraa pinnantasoa"},
                {"task": "Suodatinkentän tarkistus", "interval": "Vuosittain keväällä", "check": "Pinnannousu, hajut, tukkeumat"},
                {"task": "Näytteenotto puhdistetusta vedestä", "interval": "2 v välein", "source": "Kunnan ympäristöviranomainen"},
                {"task": "Fosforinpoistomassan vaihto", "interval": "1-2 v", "note": "Kustannus 100-200 €/vaihto"},
            ],
        },
    },
    "firewood": {
        "puulajit.yaml": {
            "WOOD_SPECIES_HEATING": [
                {"species": "Koivu", "energy_kwh_per_m3": 1700, "drying_months": 12, "note": "Paras yleispuu, hyvä lämpö ja paloaika"},
                {"species": "Leppä", "energy_kwh_per_m3": 1300, "drying_months": 6, "note": "Kuivuu nopeasti, syttyy helposti, hyvä savustukseen"},
                {"species": "Haapa", "energy_kwh_per_m3": 1150, "drying_months": 8, "note": "Nopeakasvuinen, ei kipinöi, hyvä saunaan"},
                {"species": "Mänty", "energy_kwh_per_m3": 1500, "drying_months": 18, "note": "Pihkainen, kipinöi — varovasti avotulessa"},
                {"species": "Kuusi", "energy_kwh_per_m3": 1350, "drying_months": 12, "note": "Syttyy nopeasti, hyvä sytytykseen, kipinöi"},
                {"species": "Tammi", "energy_kwh_per_m3": 2100, "drying_months": 24, "note": "Paras energiatiheys, pitkä kuivausaika"},
            ],
            "CORD_MEASUREMENTS": {
                "pinokuutiometri": "1 m × 1 m × 1 m pinottu = ~0.67 irtokuutiota",
                "irtokuutiometri": "1 m³ irtopuuta = ~1.5 pinokuutiota",
                "koivuhalko_kg_per_pm3": "~400 kg kuivana (kosteus <20%)",
                "annual_consumption_pm3": "Puusauna: 3-5 p-m³/v, Puulämmitys 100m²: 15-25 p-m³/v",
            },
        },
        "kuivaus.yaml": {
            "DRYING_GUIDELINES": [
                {"rule": "Pilko keväällä, käytä seuraavana talvena", "source": "Suomen Metsäkeskus"},
                {"rule": "Klapiteline: ilmankierto joka suunnasta, katto päälle", "note": "Alusta >10cm maasta"},
                {"rule": "Kosteusmittari: tavoite <20%, käyttökelpoinen <25%", "note": "Mittaa halkaistusta pinnasta 3cm syvyydeltä"},
                {"rule": "Liian kostea puu: nokea, syöpyy hormi, heikko lämpö", "threshold": ">25% → ei polttoon"},
                {"rule": "Ylikuiva puu (<12%): palaa liian nopeasti, huono hyötysuhde", "note": "Varastoi oikein"},
            ],
        },
    },
    "energy_advisor": {
        "spot_hinta.yaml": {
            "SPOT_PRICE_STRATEGY": [
                {"rule": "Halvin 3h jakso: pesukone, kuivausrumpu, tiskikone", "note": "Tyypillisesti yö 00-06 tai päivä 12-15"},
                {"rule": "Kallein 3h jakso: vältä suuria kuormia", "note": "Tyypillisesti aamu 07-10 tai ilta 17-20"},
                {"rule": "Negatiivinen hinta: lataa akku, lämmin vesi täyteen, lattialämmitys +1°C", "threshold": "<0 c/kWh"},
                {"rule": "Kriisihinta: sammuta kaikki paitsi jääkaappi", "threshold": ">50 c/kWh"},
            ],
            "MONTHLY_AVERAGE_EUR_MWH": {
                "Jan": 65, "Feb": 55, "Mar": 45, "Apr": 35, "May": 30,
                "Jun": 25, "Jul": 30, "Aug": 35, "Sep": 40, "Oct": 50,
                "Nov": 60, "Dec": 70, "note": "Vuoden 2024 keskiarvot, vaihtelee suuresti"
            },
        },
        "lampopumput.yaml": {
            "HEAT_PUMP_TYPES": [
                {"type": "ASHP (ilmalämpöpumppu)", "cop": "2.5-4.0", "cost_eur": "1500-3500", "note": "Helppo asentaa, ei toimi <-25°C tehokkaasti"},
                {"type": "GSHP (maalämpöpumppu)", "cop": "3.5-5.0", "cost_eur": "15000-25000", "note": "Vaatii porauksen tai keruuputkiston, paras hyötysuhde"},
                {"type": "EAHP (poistoilmalämpöpumppu)", "cop": "2.0-3.0", "cost_eur": "3000-6000", "note": "Soveltuu koneelliseen ilmanvaihtoon"},
                {"type": "AWHP (ilma-vesilämpöpumppu)", "cop": "2.5-4.0", "cost_eur": "8000-15000", "note": "Vesikiertoinen lämmitys, EI vaadi porausta"},
            ],
        },
    },
    "smart_home": {
        "protokollat.yaml": {
            "PROTOCOL_COMPARISON": [
                {"protocol": "Zigbee", "range_m": 10, "mesh": True, "devices_max": 100, "note": "Vaatii hubin (Ikea/Hue/Conbee)"},
                {"protocol": "Z-Wave", "range_m": 30, "mesh": True, "devices_max": 232, "note": "Vähemmän häiriöitä, kalliimpi"},
                {"protocol": "Matter", "range_m": "vaihtelee", "mesh": True, "devices_max": "rajoittamaton", "note": "Uusi standardi 2023+, yhdistää kaikki"},
                {"protocol": "WiFi", "range_m": 30, "mesh": False, "devices_max": "reitittimen raja", "note": "Kuormittaa verkkoa, ei mesh"},
                {"protocol": "Bluetooth LE", "range_m": 10, "mesh": True, "devices_max": 32767, "note": "Matala virrankulutus, lyhyt kantama"},
            ],
        },
        "automaatiot.yaml": {
            "AUTOMATION_RECIPES": [
                {"name": "Lähdön automatiikka", "trigger": "Viimeinen puhelin poistuu WiFistä", "actions": "Sammuta valot, laske lämpötila -2°C, lukitse ovet"},
                {"name": "Sähkön hintaoptimointi", "trigger": "spot-hinta > 20 c/kWh", "actions": "Siirrä lämminvesivaraajan lämmitys, pysäytä lattialämmitys"},
                {"name": "Aamun herääminen", "trigger": "Hälytys 30 min ennen", "actions": "Nosta lämpötila, avaa kaihtimet asteittain, kahvinkeitin päälle"},
                {"name": "Mökille saapuminen", "trigger": "GPS: 5km säteellä mökistä", "actions": "Lämmitys päälle, vesivaraaja käyntiin, ulkovalot"},
            ],
        },
    },
    "indoor_garden": {
        "valaistus.yaml": {
            "LIGHT_REQUIREMENTS": [
                {"plant_type": "Yrtti (basilika, persilja)", "ppfd_umol": "200-400", "hours": "14-16 h/pv", "note": "DLI 10-15 mol/m²/pv"},
                {"plant_type": "Salaatti, pinaatti", "ppfd_umol": "150-300", "hours": "12-14 h/pv", "note": "Viileä viljely 15-20°C"},
                {"plant_type": "Tomaatti, chili", "ppfd_umol": "400-600", "hours": "16-18 h/pv", "note": "Vaatii paljon valoa, lämmin"},
                {"plant_type": "Orkidea", "ppfd_umol": "100-200", "hours": "12 h/pv", "note": "Ei suoraa auringonvaloa"},
                {"plant_type": "Sukulentti", "ppfd_umol": "200-400", "hours": "10-14 h/pv", "note": "Kestää kuivuutta, vähän kastelua"},
            ],
            "LED_SPECTRUM": {
                "red_nm": "620-680 (kukkiminen, hedelmätuotanto)",
                "blue_nm": "440-470 (vegetatiivinen kasvu, kompaktisuus)",
                "full_spectrum": "Paras yleiskäyttöön, 3000-5000K valkoinen + punainen",
            },
        },
    },
    "apartment_board": {
        "vastikkeet.yaml": {
            "MAINTENANCE_CHARGE_COMPONENTS": [
                {"component": "Hoitovastike", "typical_eur_m2_month": "3-6", "covers": "Lämmitys, vesi, jätehuolto, vakuutus, hallinto"},
                {"component": "Rahoitusvastike", "typical_eur_m2_month": "1-5", "covers": "Lainojen lyhennys + korot (perusparannukset)"},
                {"component": "Vesimaksu", "typical_eur_person_month": "15-25", "note": "Henkilöperusteinen tai huoneistokohtainen mittaus"},
                {"component": "Autopaikka", "typical_eur_month": "15-50", "note": "Ulko/sisä/lämmin paikka"},
                {"component": "Saunamaksu", "typical_eur_turn": "2-5", "note": "Vuorokohtainen tai kuukausimaksu"},
            ],
        },
        "remontit.yaml": {
            "RENOVATION_COSTS_EUR_M2": {
                "putkiremontti": {"range": "600-1200", "duration_months": "4-8", "note": "Suurin yksittäinen remontti"},
                "julkisivuremontti": {"range": "200-500", "duration_months": "3-6", "note": "Elementtisaumat, rappaus, maalaus"},
                "kattoremontti": {"range": "80-200", "duration_months": "1-3", "note": "Huopa/pelti, sadevesijärjestelmä"},
                "hissiremontti": {"range": "100000-200000 €/hissi", "duration_months": "2-4", "note": "Modernisaatio tai uusi hissi"},
                "ikkunaremontti": {"range": "200-400", "duration_months": "2-4", "note": "Tiivistys tai vaihto"},
            },
        },
    },
    "delivery_tracker": {
        "palvelut.yaml": {
            "DELIVERY_SERVICES_FINLAND": [
                {"service": "Posti", "tracking": "seuranta.posti.fi", "customs_limit_eur": 150, "note": "Yleisin, laajin verkosto"},
                {"service": "Matkahuolto", "tracking": "matkahuolto.fi/seuranta", "note": "Bussipaketti-verkosto, noutopisteet"},
                {"service": "DHL Express", "tracking": "dhl.fi", "note": "Kansainväliset pikatoimitukset"},
                {"service": "UPS", "tracking": "ups.com", "note": "B2B-toimitukset, raskas tavara"},
                {"service": "Amazon", "tracking": "amazon.fi", "note": "Oma logistiikka, Prime-toimitukset"},
            ],
            "CUSTOMS_RULES": {
                "eu_internal": "Ei tullia EU:n sisällä",
                "eu_external_under_150": "ALV 24%, ei tullia",
                "eu_external_over_150": "ALV 24% + tulli (tuoteryhmäkohtainen 0-17%)",
                "alcohol_tobacco": "Erityisverot, rajoitukset",
            },
        },
    },
    "commute_planner": {
        "hsl.yaml": {
            "HSL_ZONES_2024": [
                {"zone": "A", "area": "Helsinki keskusta", "single_eur": 2.40, "monthly_eur": 56.30},
                {"zone": "AB", "area": "Helsinki", "single_eur": 2.95, "monthly_eur": 63.20},
                {"zone": "ABC", "area": "Helsinki + Espoo/Vantaa", "single_eur": 3.95, "monthly_eur": 74.10},
                {"zone": "ABCD", "area": "+ Kerava/Kirkkonummi", "single_eur": 5.45, "monthly_eur": 112.80},
            ],
            "COMMUTE_TAX_DEDUCTION": {
                "cheapest_route": "Vähennys halvimman kulkuneuvon mukaan",
                "own_car_if_saves_time": "Oma auto jos säästää >1h/pv kokonaismatka-aikaa",
                "deduction_eur_per_km": 0.25,
                "min_deduction_eur": 750,
                "max_deduction_eur": 7000,
                "source": "Verohallinto 2024",
            },
        },
    },
    "noise_monitor": {
        "raja_arvot.yaml": {
            "NOISE_LIMITS_FINLAND": [
                {"area": "Asunto sisällä, päivä (07-22)", "limit_db": 35, "source": "VNA 545/2015"},
                {"area": "Asunto sisällä, yö (22-07)", "limit_db": 30, "source": "VNA 545/2015"},
                {"area": "Piha-alue, päivä", "limit_db": 55, "source": "VNA 993/1992"},
                {"area": "Piha-alue, yö", "limit_db": 50, "source": "VNA 993/1992"},
                {"area": "Virkistysalue", "limit_db": 45, "source": "VNA 993/1992"},
                {"area": "Työmelu, 8h altistus", "limit_db": 85, "source": "VNA 85/2006"},
            ],
            "COMMON_NOISE_LEVELS_DB": {
                "whisper": 30, "normal_speech": 60, "vacuum_cleaner": 70,
                "lawn_mower": 85, "chainsaw": 100, "pain_threshold": 130,
            },
        },
    },
    "child_safety": {
        "vaarat.yaml": {
            "AGE_SPECIFIC_RISKS": [
                {"age": "0-6 kk", "risks": "Tukehtuminen, putoaminen, kylmettyminen", "actions": "Nukutusasento selällään, pinnasänky, lämpötila 18-20°C"},
                {"age": "6-12 kk", "risks": "Pienesineet suuhun, myrkyt, putoaminen", "actions": "Lattiatasolla pienesinetarkistus, kaapistojen lukitus"},
                {"age": "1-3 v", "risks": "Vesi (hukkunmiset), portaat, kuumuus, myrkyt", "actions": "Turvaportit, pistorasiasuojat, pesuaine lukot, vesivahti"},
                {"age": "3-6 v", "risks": "Liikenne, putoaminen, palovammat", "actions": "Liikennekasvatus, ikkunalukot, heijastin"},
            ],
            "POISON_CENTER": {
                "number": "0800 147 111",
                "available": "24/7",
                "note": "Myrkytystietokeskus HUS — soita AINA epäillessä",
            },
        },
    },
    "pet_care": {
        "terveys.yaml": {
            "DOG_VACCINATION_SCHEDULE": [
                {"vaccine": "Perusrokotus (pentu)", "age": "8 vk + 12 vk + 16 vk", "note": "Penikkatauti, parvo, hepatiitti"},
                {"vaccine": "Tehosterokotus", "interval": "1 vuosi, sitten 3 v", "note": "Rabies pakollinen ulkomaille"},
                {"vaccine": "Bordetella (kennelyskä)", "interval": "Vuosittain", "note": "Jos koirapuistot, hoitola"},
            ],
            "CAT_VACCINATION_SCHEDULE": [
                {"vaccine": "Perusrokotus (pentu)", "age": "9 vk + 12 vk", "note": "Kissarutto, calici, herpes"},
                {"vaccine": "Tehosterokotus", "interval": "1 vuosi, sitten 3 v", "note": "Rabies jos ulkona/ulkomaille"},
                {"vaccine": "FeLV (leukemia)", "interval": "Vuosittain", "note": "Jos ulkokissa tai kontakti muihin"},
            ],
            "EMERGENCY_SIGNS": [
                "Syömättömyys >24h", "Oksentelu >3 kertaa", "Ripuli >2 pv",
                "Veriset ulosteet/oksennus", "Hengitysvaikeus", "Kouristelu",
                "Päähän kohdistunut isku", "Myrkyn nieleminen",
            ],
        },
    },
    "budget_tracker": {
        "verotus.yaml": {
            "TAX_DEDUCTIONS_2024": [
                {"deduction": "Kotitalousvähennys", "max_eur": 2250, "percent": 40, "note": "Remontti, siivous, hoiva — työn osuus"},
                {"deduction": "Työmatkakulut", "max_eur": 7000, "min_eur": 750, "note": "Halvimman kulkutavan mukaan"},
                {"deduction": "Asuntolainan korkovähennys", "percent": 0, "note": "Poistunut kokonaan 2023"},
                {"deduction": "Tulonhankkimiskulut", "default_eur": 750, "note": "Automaattinen, ei tarvitse vaatia"},
                {"deduction": "Ammattiliittomaksu", "percent": 100, "note": "Kokonaan vähennyskelpoinen"},
            ],
            "KELA_BENEFITS": {
                "asumistuki_max_eur": "Riippuu paikkakunta + perhe, Helsinki max ~500 €/kk",
                "toimeentulotuki": "Perusosa 1 hlö 555.11 €/kk (2024)",
                "sairauspaivaraha": "Omavastuuaika 1+9 pv, korvaus ~70% palkasta",
                "vanhempainpaivaraha": "320 pv yhteensä (äiti+isä), ~70% palkasta",
            },
        },
    },
    "production_line": {
        "oee.yaml": {
            "OEE_CALCULATION": {
                "formula": "OEE = Käytettävyys × Nopeus × Laatu",
                "availability": "Suunniteltu ajoaika - seisokit / Suunniteltu ajoaika",
                "performance": "Todellinen tuotanto / Teoreettinen maksimi",
                "quality": "Hyvät kappaleet / Kaikki kappaleet",
                "world_class": ">85% OEE",
                "typical_range": "60-75% useimmilla teollisuuslinjoilla",
                "losses_categories": ["Laiterikkot", "Asetukset/säädöt", "Tyhjäkäynti", "Alentunut nopeus", "Prosessivirheet", "Käynnistystappiot"],
            },
        },
        "lean.yaml": {
            "LEAN_TOOLS": [
                {"tool": "5S", "purpose": "Työpisteen järjestys: Sorteeraa, Systematisoi, Siivoa, Standardoi, Seuraa"},
                {"tool": "Kanban", "purpose": "Visuaalinen ohjaus: tuotanto vain tarpeen mukaan, WIP-rajat"},
                {"tool": "Value Stream Mapping", "purpose": "Arvovirtakuvaus: tunnista hukka prosessissa"},
                {"tool": "Poka-yoke", "purpose": "Virheen estäminen: mekaaniset/sähköiset varmistukset"},
                {"tool": "Kaizen", "purpose": "Jatkuva parantaminen: pienet parannukset joka päivä"},
                {"tool": "SMED", "purpose": "Nopea asetuksenvaihto: <10 min tavoite"},
            ],
        },
    },
    "quality_inspector": {
        "spc.yaml": {
            "SPC_RULES": [
                {"rule": "1 piste yli 3σ rajojen", "action": "Pysäytä prosessi, selvitä syy", "type": "Poikkeama"},
                {"rule": "7 peräkkäistä pistettä samalla puolella", "action": "Prosessin siirtymä, kalibroi", "type": "Trendi"},
                {"rule": "6 peräkkäistä nousevaa/laskevaa", "action": "Trendi käynnissä, tarkista kuluminen", "type": "Trendi"},
                {"rule": "14 peräkkäistä vuorotellen ylös/alas", "action": "Systemaattinen vaihtelu, tarkista 2 konetta/raaka-ainetta", "type": "Syklinen"},
            ],
            "CPK_INTERPRETATION": {
                "cpk_below_1": "Prosessi ei kyvykäs → välitön toimenpide",
                "cpk_1_to_1_33": "Rajallinen kyvykkyys → paranna",
                "cpk_1_33_to_1_67": "Kyvykäs → normaali seuranta",
                "cpk_above_1_67": "Erinomainen → harkitse toleranssien kiristystä",
            },
        },
    },
    "shift_manager": {
        "tyolaki.yaml": {
            "FINNISH_LABOR_LAW": [
                {"rule": "Päivittäinen enimmäistyöaika", "value": "8h + 4h ylityö", "source": "Työaikalaki 872/2019"},
                {"rule": "Viikoittainen enimmäistyöaika", "value": "40h + 8h ylityö (4kk keskiarvo)", "source": "Työaikalaki"},
                {"rule": "Yövuorolisä", "value": "Vähintään 20% korotus tai vastaava vapaana", "source": "TES-kohtainen"},
                {"rule": "Vuorokausilepo", "value": "Vähintään 11h keskeytymätön lepo", "source": "Työaikalaki"},
                {"rule": "Viikkolepo", "value": "Vähintään 35h keskeytymätön lepo", "source": "Työaikalaki"},
                {"rule": "Ylityön suostumus", "value": "Työntekijän suostumus jokaista kertaa varten", "source": "Työaikalaki"},
                {"rule": "Yötyö klo 23-06", "value": "Erityiset rajoitukset, terveystarkastus", "source": "Työaikalaki"},
            ],
        },
    },
    "safety_officer": {
        "riskit.yaml": {
            "RISK_ASSESSMENT_MATRIX": {
                "probability_levels": {"1": "Epätodennäköinen", "2": "Mahdollinen", "3": "Todennäköinen", "4": "Lähes varma"},
                "severity_levels": {"1": "Vähäinen", "2": "Haitallinen", "3": "Vakava", "4": "Erittäin vakava"},
                "action_levels": {
                    "1-4": "Hyväksyttävä → seuranta",
                    "5-8": "Kohtalainen → toimenpidesuunnitelma 30pv",
                    "9-12": "Merkittävä → välitön toimenpide",
                    "13-16": "Sietämätön → työ keskeytetään kunnes korjattu",
                },
            },
        },
        "psa.yaml": {
            "PPE_REQUIREMENTS": [
                {"area": "Tuotantohalli", "required": "Turvakengät, kuulosuojaus >85dB, suojalasit", "standard": "EN ISO 20345"},
                {"area": "Kemikaalivarasto", "required": "Kemikaalikäsineet, suojalasit, hengityssuojain", "standard": "EN 374"},
                {"area": "Hitsaustyö", "required": "Hitsausmaski DIN 11-13, nahkakäsineet, esiliina", "standard": "EN 169"},
                {"area": "Korkealla työskentely >2m", "required": "Turvavaljas + kiinnityspiste, kypärä", "standard": "EN 361"},
            ],
        },
    },
    "maintenance_planner": {
        "strategiat.yaml": {
            "MAINTENANCE_STRATEGIES": [
                {"strategy": "Korjaava (CM)", "when": "Ajetaan rikkoutumiseen asti", "suitable": "Ei-kriittiset laitteet, vara-aste olemassa"},
                {"strategy": "Ehkäisevä (PM)", "when": "Aikaperusteiset huoltovälit", "suitable": "Tunnettu vikaantumismalli, helppo huoltaa"},
                {"strategy": "Ennakoiva (PdM)", "when": "Kuntoseuranta: värähtely, lämpö, öljy", "suitable": "Kriittiset laitteet, korkea vikaantumiskustannus"},
                {"strategy": "TPM (Total Productive)", "when": "Käyttäjäkunnossapito + ammattihuolto", "suitable": "Tuotantoympäristö, OEE-tavoitteet"},
            ],
            "KPI_TARGETS": {
                "pm_compliance": ">90% ennakkohuoltotöistä tehty ajallaan",
                "mtbf_trend": "Nouseva trendi = parantuva luotettavuus",
                "mttr_target": "Korjausaika <4h kriittisille laitteille",
                "spare_parts_availability": ">95% kriittisten varaosien saatavuus",
                "maintenance_cost_ratio": "<3% laitteen jälleenhankinta-arvosta/vuosi",
            },
        },
    },
    "supply_chain": {
        "toimittajat.yaml": {
            "SUPPLIER_EVALUATION": [
                {"criteria": "Toimitusvarmuus (OTD)", "weight_pct": 30, "target": ">95%"},
                {"criteria": "Laatu (PPM)", "weight_pct": 25, "target": "<1000 PPM"},
                {"criteria": "Hinta/kustannus", "weight_pct": 20, "target": "Kilpailukykyinen, ei halvin"},
                {"criteria": "Joustavuus", "weight_pct": 15, "target": "Lead time <2 viikkoa muutoksille"},
                {"criteria": "Viestintä & yhteistyö", "weight_pct": 10, "target": "Proaktiivinen, ongelmien raportointi"},
            ],
            "INVENTORY_MODELS": {
                "abc_analysis": "A=80% arvosta 20% nimikkeistä, B=15%/30%, C=5%/50%",
                "safety_stock": "Turvavarasto = Z × σ × √(L), Z=1.65 (95% palvelutaso)",
                "eoq": "Taloudellinen tilausmäärä = √(2×D×S/H)",
                "kanban_cards": "N = D×L×(1+S)/C, D=kysyntä, L=läpimenoaika",
            },
        },
    },
    "energy_manager": {
        "kompressorit.yaml": {
            "COMPRESSED_AIR_EFFICIENCY": [
                {"issue": "Vuodot", "typical_loss_pct": "20-30%", "action": "Ultraäänivuotokartoitus vuosittain, korjaus heti"},
                {"issue": "Paine liian korkea", "saving": "1 bar vähennys = 7% säästö", "action": "Optimoi verkostopaine 6-7 bar"},
                {"issue": "Kuivain ylimitoitettu", "saving": "Oikea pistelämpötila +3°C → 10% säästö", "action": "Tarkista pistelämpötila"},
                {"issue": "Lämmön talteenotto", "saving": "Jopa 94% moottoritehosta talteen", "action": "Ohjaa lämpö tilojen/veden lämmitykseen"},
            ],
            "POWER_FACTOR_CORRECTION": {
                "target": ">0.95",
                "penalty_threshold": "<0.90 → verkkoyhtiön korotus",
                "compensation": "Kondensaattoriparisto, automaattinen kompensaatio",
                "measurement": "Tehoanalysaattori, 1 viikon mittaus",
            },
        },
    },
    "waste_handler": {
        "luokittelu.yaml": {
            "WASTE_CATEGORIES_FINLAND": [
                {"code": "EWC 20", "type": "Yhdyskuntajäte", "examples": "Sekajäte, biojäte, paperi, metalli"},
                {"code": "EWC 15", "type": "Pakkausjäte", "examples": "Muovi, kartonki, lasi, puu"},
                {"code": "EWC 13", "type": "Öljyjäte", "examples": "Moottoriöljy, hydrauliikkaöljy, voitelujäte"},
                {"code": "EWC 16", "type": "Sähkö- ja elektroniikkaromu", "examples": "SER: kodinkoneet, lamput, akut"},
                {"code": "EWC 17", "type": "Rakennusjäte", "examples": "Betoni, tiili, puu, eristeet"},
            ],
            "HAZARDOUS_WASTE_RULES": {
                "storage_max_months": 12,
                "labeling": "Varoitusmerkit + EWC-koodi + päivämäärä",
                "transport": "VAK-luvallinen kuljetus, siirtoasiakirja",
                "register": "Jätehuollon seurantajärjestelmä SIIRTO, ilmoitus vuosittain",
            },
        },
    },
    "lab_analyst": {
        "kalibrointi.yaml": {
            "CALIBRATION_BEST_PRACTICES": [
                {"instrument": "Vaaka", "interval": "6 kk tai 12 kk", "standard": "OIML R 76", "note": "Referenssimassat FINAS-kalibroituja"},
                {"instrument": "pH-mittari", "interval": "Ennen jokaista mittausta (puskuriliuokset pH 4, 7, 10)", "note": "Elektrodin ikä max 2v"},
                {"instrument": "Lämpömittari", "interval": "12 kk", "standard": "ITS-90", "note": "Jääpiste 0°C tarkistus viikoittain"},
                {"instrument": "Pipetti", "interval": "6 kk", "standard": "ISO 8655", "note": "Gravimetrinen kalibrointi"},
            ],
            "MEASUREMENT_UNCERTAINTY": {
                "type_a": "Tilastollinen: mittaussarjan keskihajonta",
                "type_b": "Systemaattinen: kalibrointitodistus, resoluutio, ympäristö",
                "combined": "u_c = √(u_A² + u_B²)",
                "expanded": "U = k × u_c, k=2 (95% luottamusväli)",
            },
        },
    },
    "compliance": {
        "standardit.yaml": {
            "COMMON_ISO_STANDARDS": [
                {"standard": "ISO 9001", "scope": "Laadunhallinta", "audit_cycle": "3 vuotta + vuosiseuranta", "note": "Yleisin hallintajärjestelmästandardi"},
                {"standard": "ISO 14001", "scope": "Ympäristöhallinta", "audit_cycle": "3 vuotta + vuosiseuranta", "note": "Ympäristövaikutusten hallinta"},
                {"standard": "ISO 45001", "scope": "Työterveys ja -turvallisuus", "audit_cycle": "3 vuotta + vuosiseuranta", "note": "Korvasi OHSAS 18001"},
                {"standard": "ISO 22000", "scope": "Elintarviketurvallisuus", "audit_cycle": "3 vuotta + vuosiseuranta", "note": "HACCP-pohjanen"},
                {"standard": "ISO 50001", "scope": "Energiahallinta", "audit_cycle": "3 vuotta + vuosiseuranta", "note": "Energiatehokkuuden parantaminen"},
            ],
            "AUDIT_FINDING_TYPES": {
                "major_nc": "Vakava poikkeama: järjestelmällinen vaatimuksen rikkominen → korjaus 30pv",
                "minor_nc": "Lievä poikkeama: yksittäinen puute → korjaus 90pv",
                "observation": "Huomautus: parannusmahdollisuus, ei vaatimusta korjata",
                "positive": "Vahvuus: hyvä käytäntö, esimerkki muille",
            },
        },
    },
    "forklift_fleet": {
        "turvallisuus.yaml": {
            "FORKLIFT_SAFETY_RULES": [
                {"rule": "Ajonopeus sisätiloissa max 10 km/h", "source": "Työturvallisuuslaki"},
                {"rule": "Haarukka-alas kun ei kuormaa", "note": "Haarukat 5-10 cm maasta ajaessa"},
                {"rule": "Mastin kallistus taaksepäin kuormaa nostaessa", "note": "Estää kuorman putoamisen"},
                {"rule": "Turvavyö aina kiinni", "note": "Kaatumissuojaus, ROPS/FOPS"},
                {"rule": "Ei matkustajia trukissa", "note": "Poikkeus: koulutettava kouluttajan kanssa"},
                {"rule": "Lataus/tankkaus vain merkityillä alueilla", "note": "Kipinävaara, kaasut"},
                {"rule": "Kuormauskapasiteetti: tarkista kuormakaavio", "note": "Masto kallistus ja nostokorkeus vaikuttavat"},
            ],
            "LICENSE_REQUIREMENTS": {
                "type_1": "Vastapainotrukki (sisä/ulko) — peruskurssi 2-3 pv",
                "type_2": "Tukipyörätrukki — lisäkoulutus 1 pv",
                "type_3": "Kurottaja — erikoiskoulutus 2 pv",
                "renewal": "Voimassa 5 vuotta, uusinnan kesto 1 pv",
                "medical": "Terveystarkastus: näkö, kuulo, reaktiokyky",
            },
        },
    },
    "hvac_industrial": {
        "suodattimet.yaml": {
            "FILTER_CLASSES": [
                {"class": "G4", "efficiency": "Karkea esisuodatus", "use": "Ulkoilma, perussuodatus", "change_interval": "3-6 kk"},
                {"class": "M5-M6", "efficiency": "Keskitason suodatus", "use": "Toimistot, tavalliset tilat", "change_interval": "6-12 kk"},
                {"class": "F7-F9", "efficiency": "Hienosuodatus", "use": "Sairaalat, laboratoriot", "change_interval": "6-12 kk"},
                {"class": "H13 (HEPA)", "efficiency": ">99.95%", "use": "Puhdastilat, leikkaussalit", "change_interval": "12-24 kk, paine-eron seuranta"},
                {"class": "H14 (ULPA)", "efficiency": ">99.995%", "use": "Mikroelektroniikka, farmasia", "change_interval": "12-24 kk"},
            ],
            "CLEANROOM_CLASSES": {
                "ISO_5": "Luokka 100 — max 3520 hiukkasta/m³ (≥0.5µm)",
                "ISO_6": "Luokka 1000 — max 35200 hiukkasta/m³",
                "ISO_7": "Luokka 10000 — max 352000 hiukkasta/m³",
                "ISO_8": "Luokka 100000 — perustuotantotila",
            },
        },
    },
}


def enrich_agent(agent_id: str, files_data: dict) -> int:
    """Add enrichment data to agent's knowledge files. Returns count of files modified."""
    agent_dir = KNOWLEDGE_DIR / agent_id
    if not agent_dir.exists():
        print(f"  SKIP: {agent_id} — directory not found")
        return 0

    modified = 0
    for filename, sections in files_data.items():
        filepath = agent_dir / filename
        if not filepath.exists():
            # Create new file
            data = sections
        else:
            # Load existing and merge
            with open(filepath, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            data.update(sections)

        with open(filepath, "w", encoding="utf-8", newline="\n") as f:
            yaml.dump(data, f, allow_unicode=True, default_flow_style=False,
                      sort_keys=False, width=120)
        modified += 1

    return modified


total_agents = 0
total_files = 0
for agent_id, files_data in ENRICHMENT_DATA.items():
    n = enrich_agent(agent_id, files_data)
    if n > 0:
        print(f"  ENRICHED: {agent_id} — {n} files updated")
        total_agents += 1
        total_files += n

print(f"\nResults: {total_agents} agents enriched, {total_files} files updated")

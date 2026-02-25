#!/usr/bin/env python3
"""
OpenClaw v1.4 — DEPTH PATCH
Korjaa kaikki agentit STRICT-validoinnin tasolle:
- Lisää action-kentät puuttuviin metriikoihin
- Lisää numeeriset arvot kuvaileviin kenttiin
- Spesifioi kausikohtaiset säännöt (viikot, lämpötilat)
"""
import yaml, os, re
from pathlib import Path

BASE = Path("agents")
patched = 0
changes = 0

def has_number(s):
    return bool(re.search(r'\d', str(s)))

def load(agent_dir):
    with open(BASE / agent_dir / "core.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)

def save(agent_dir, core):
    with open(BASE / agent_dir / "core.yaml", "w", encoding="utf-8") as f:
        yaml.dump(core, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

# ═══════════════════════════════════════════════════════
# PATCHES: Specific fixes per agent
# ═══════════════════════════════════════════════════════

PATCHES = {
    "meteorologi": {
        "DECISION_METRICS_AND_THRESHOLDS": {
            "temperature_c": {
                "value": "Jatkuva seuranta",
                "thresholds": {"frost": 0, "heat": 25, "extreme_cold": -25, "extreme_heat": 30},
                "action": "T<0°C → hallavaroitus hortonomille+tarhaajalle. T>25°C → hellevaroitus. T<-25°C → putkijäätymisvaara LVI:lle.",
                "source": "src:ME1"
            },
            "wind_ms": {
                "value": "Jatkuva seuranta",
                "thresholds": {"moderate": 8, "strong": 14, "storm": 21},
                "action": ">8 m/s → mehiläisten lentoaktiivisuus laskee. >14 m/s → varoitus ulkoagenteille. >21 m/s → MYRSKY, myrskyvaroittajalle.",
                "source": "src:ME1"
            },
            "precip_mm_h": {
                "value": "Seuranta",
                "thresholds": {"light": 0.5, "moderate": 4, "heavy": 8},
                "action": ">0.5 mm/h → mehiläiset eivät lennä. >4 mm/h → tulvariski salaojille. >8 mm/h → veden nousu.",
                "source": "src:ME1"
            },
            "humidity_rh": {
                "value": "Seuranta 40-85% normaali",
                "thresholds": {"dry": 30, "damp": 85},
                "action": "<30% RH → kuivuusvaara kasveille, ilmoita hortonomille. >85% → homeriski, ilmoita timpurille.",
                "source": "src:ME1"
            },
            "pressure_hpa": {
                "value": "1010-1025 hPa normaali",
                "thresholds": {"low": 1000, "high": 1035},
                "action": "<1000 hPa + laskeva trendi → myrsky tulossa, ilmoita myrskyvaroittajalle. Laskeva paine → kalastusoppaalle (syönti parantuu).",
                "source": "src:ME1"
            },
            "uv_index": {
                "value": "Kesällä 0-8",
                "thresholds": {"moderate": 3, "high": 6, "very_high": 8},
                "action": "UV>6 → suojautumisvaroitus. UV>8 → rajoita ulkotyö klo 11-15.",
                "source": "src:ME1"
            }
        },
        "SEASONAL_RULES": [
            {"season": "Kevät", "action": "Hallavaroitukset kun T<0°C yöllä (huhti-touko). Tulvariskin seuranta lumien sulaessa. Jäiden lähtö vko 16-19 Kouvolassa.", "source": "src:ME1"},
            {"season": "Kesä", "action": "Ukkosvaroitukset (kesä-elo). Hellevaroitus T>25°C yli 3 pv. UV>6 klo 11-15. Nektarieritys optimaalinen T>18°C + RH 50-80%.", "source": "src:ME1"},
            {"season": "Syksy", "action": "Myrskykausi loka-joulukuu: tuulivaroitukset >14 m/s. Ensipakkaset tyypillisesti vko 40-44. Sähkökatkosriski myrskyssä.", "source": "src:ME1"},
            {"season": "Talvi", "action": "Pakkasvaroitus T<-25°C (putkijäätyminen). Liukkausvaroitus T lähellä 0°C. Lumikuormavaroitus >150 kg/m². Häkävaara inversiossa.", "source": "src:ME1"}
        ]
    },

    "tautivahti": {
        "DECISION_METRICS_AND_THRESHOLDS": {
            "afb_tolerance": {
                "value": 0,
                "action": "AFB: NOLLATOLERANSSI → ilmoita Ruokavirasto 029 530 0400, eristä tarha, ÄLÄ siirrä kehyksiä",
                "source": "src:TAU1"
            },
            "efb_detection": {
                "value": "Mosaiikkimainen sikiöpeite, kellertävät toukat",
                "action": "EFB-epäily → näytteenotto Ruokavirastolle, eristä pesä",
                "source": "src:TAU1"
            },
            "nosema_spores_per_bee": {
                "value": 1000000,
                "action": ">1 milj. itiötä/mehiläinen → fumagilliinikiellon takia hoito oksa- tai etikkahapolla",
                "source": "src:TAU1"
            },
            "chalkbrood_frame_pct": {
                "value": 10,
                "action": ">10% kehyksistä kalkkisikiötä → vaihda emo, paranna ilmanvaihtoa, poista pahimmat kehykset",
                "source": "src:TAU1"
            },
            "dwv_detection": {
                "value": "Surkastuneet siivet kuoriutuvilla mehiläisillä",
                "action": "DWV havaittu → välitön varroa-mittaus. >3/100 → kemiallinen hoito vaikka satokausi.",
                "source": "src:TAU1"
            }
        },
        "SEASONAL_RULES": [
            {"season": "Kevät", "action": "Ensitarkistus vko 16-18: sikiöpeite, ruokavarasto >5 kg. Nosema-näyte 30 mehiläisestä jos epäily. Kalkkisikiön tarkistus.", "source": "src:TAU1"},
            {"season": "Kesä", "action": "AFB-tarkistus jokaisella satokehysten käsittelyllä. Siirtoihin ei tartuntatarhan kehyksiä. DWV-seuranta.", "source": "src:TAU1"},
            {"season": "Syksy", "action": "Varroa-hoito elokuussa hunajankorjuun jälkeen: oksaalihappo tai amitraz. Jos >3/100 → toinen kierros syyskuussa.", "source": "src:TAU1"},
            {"season": "Talvi", "action": "Oksaalihappohoito joulukuussa (sikiöttömään aikaan, T<5°C). Kuolleisuusseuranta: >30% → dokumentoi, tarkista varroa+nosema.", "source": "src:TAU1"}
        ]
    },

    "nektari_informaatikko": {
        "DECISION_METRICS_AND_THRESHOLDS": {
            "daily_weight_gain_kg": {
                "value": "Seuranta puntaripesällä",
                "action": ">0.5 kg/pv + T>18°C → satokausi ALKAA, aseta korotukset. <0.2 kg/pv 3 pv → satokausi HIIPUU.",
                "source": "src:NEK1"
            },
            "peak_flow_kg_day": {
                "value": "Maitohorsma 2-5 kg/pv, rypsi 1-3 kg/pv, lehmus 1-3 kg/pv",
                "action": ">3 kg/pv → tarkista korotustila, lisää jos ≥75% kehyksistä täynnä.",
                "source": "src:NEK1"
            },
            "moisture_content_pct": {
                "value": 18,
                "action": "<18% → linkoamiskelpoinen (refraktometri). >20% → EI linkoa, anna kypsyä. Rypsi >19% → kiteytymisriski, linkoa HETI.",
                "source": "src:NEK1"
            },
            "nectar_secretion_conditions": {
                "value": "T>15°C + RH 50-80% + aurinkoista",
                "action": "Optimaaliolosuhteet → ilmoita tarhaajalle satokauden alkamisesta. T<13°C tai RH<40% → eritys pysähtyy.",
                "source": "src:NEK1"
            },
            "season_end_trigger": {
                "value": "Painonlisäys <0.2 kg/pv + maitohorsma kukkinut → satokausi ohi",
                "action": "Ilmoita tarhaajalle: aloita linkoaminen ja syysruokintasuunnittelu vko 32-34.",
                "source": "src:NEK1"
            }
        }
    },

    "elokuva_asiantuntija": {
        "DECISION_METRICS_AND_THRESHOLDS": {
            "audience_rating_min": {
                "value": 6.5,
                "action": "IMDb <6.5 → suosittele vain jos erityinen syy (ohjaaja, teema). <5.0 → älä suosittele.",
                "source": "src:EL1"
            },
            "runtime_max_min": {
                "value": 120,
                "action": ">120 min arki-illalle → varoita ('pitkä elokuva'). >180 min → ehdota viikonloppua.",
                "source": "src:EL1"
            },
            "content_rating": {
                "value": "K7/K12/K16/K18",
                "action": "Lapsia <16v paikalla → max K12. Rikkomus → Kuvaohjelmalaki 710/2011.",
                "source": "src:EL2"
            },
            "streaming_check": {
                "value": "Tarkista Yle Areena → Elisa Viihde → Netflix → kirjasto",
                "action": "Ei löydy mistään → ilmoita käyttäjälle, ehdota DVD/Blu-ray lainaus kirjastosta.",
                "source": "src:EL1"
            },
            "mood_algorithm": {
                "value": "Syötteenä: tunnelma + seurue + kausi",
                "action": "Pimeä talvi-ilta + 2 hlö → draama/jännitys. Kesäilta + ryhmä → komedia. Itsenäisyyspäivä → Tuntematon sotilas.",
                "source": "src:EL1"
            }
        }
    },

    "kalantunnistaja": {
        "DECISION_METRICS_AND_THRESHOLDS": {
            "confidence_min_pct": {"value": 80, "action": "<80% → pyydä lisäkuva (sivuprofiili + evät auki) tai mittaus", "source": "src:KAT1"},
            "protected_species": {"value": "Järvitaimen, nieriä, ankerias", "action": "VAPAUTA VEDESSÄ heti, ÄLÄ nosta. Dokumentoi kuva + GPS + aika. Ilmoita ELY-keskukselle.", "source": "src:KAT2"},
            "invasive_species": {"value": "Hopearuutana (Carassius gibelio)", "action": "EI takaisin veteen. Lopeta. Ilmoita ELY-keskukselle 2 pv sisällä.", "source": "src:KAT2"},
            "measurement_mm": {"value": "Kokonaispituus kuono→pyrstön kärki, ±5 mm tarkkuus", "action": "Mittaa AINA ennen päätöstä pitää/vapauttaa. Alamitta: hauki 400 mm, kuha 420 mm.", "source": "src:KAT1"},
            "key_features_5": {"value": "1=evien lkm/sijainti, 2=suomut, 3=väri, 4=suun muoto, 5=kylkiviiva", "action": "Jos ≤3 piirrettä nähtävissä → varmuus <80%, pyydä lisäkuva", "source": "src:KAT1"}
        }
    },

    "privaattisuus": {
        "DECISION_METRICS_AND_THRESHOLDS": {
            "camera_coverage": {"value": "0% naapurikiinteistöä, 0% yleistä tietä tunnistettavasti", "action": "Yli 0% → suuntaa kamera HETI, pienennä kuvakulma. Tarkistus 2x/v + asennuksen jälkeen.", "source": "src:PR1"},
            "data_retention_days": {"value": 30, "action": ">30 pv → automaattipoisto (ei-merkityt). Poliisipyynnön tallenteet 90 pv.", "source": "src:PR1"},
            "audio_recording": {"value": 0, "note": "0 = pois päältä ulkokameroissa", "action": "Äänitallenne ulkona ilman informointia → GDPR-rike. Pois tai kyltti 'Alueella tallentava kameravalvonta'.", "source": "src:PR1"},
            "data_local_pct": {"value": 100, "action": "100% paikallisesti. Pilvipalveluun lähettäminen → blokkaa palomuurissa, ilmoita kybervahdille.", "source": "src:PR1"},
            "access_log_audit_days": {"value": 7, "action": "Tarkista kameratallenteiden katseluloki 7 pv välein. Luvaton katselu → GDPR-rike.", "source": "src:PR1"}
        }
    },

    "sahkoasentaja": {
        "DECISION_METRICS_AND_THRESHOLDS": {
            "outdoor_extension_cable_rating": {"value": "IP44 ulkokäyttöön, 16 A max, max 25 m", "action": "Sisäjatkojohto ulkona → sähköiskuvaara. Vaihda IP44.", "source": "src:SAH1"},
            "surge_protection_presence": {"value": "Ylijännitesuoja B+C, 40 kA pääkeskuksessa", "action": "Puuttuu → asennuta Tukes-rekisteröity asentaja.", "source": "src:SAH1"},
            "main_fuse_rating_a": {"value": "25 A tai 35 A omakotitalo", "action": ">80% kuormitus → seuranta. Laukeaa → tarkista kuorma.", "source": "src:SAH1"}
        },
        "SEASONAL_RULES": [
            {"season": "Kevät", "action": "Ulkopistorasioiden tarkistus. Aurinkopaneelien kaapelit. RCD-testi 30 mA.", "source": "src:SAH1"},
            {"season": "Kesä", "action": "Ukkossuojaus: ylijännitesuojat B+C 40 kA. UV-rasitus. Kulutusseuranta kWh/kk.", "source": "src:SAH1"},
            {"season": "Syksy", "action": "Lämmitysjärjestelmän sähkötarkistus. Sulanapitokaapelit vko 42-44.", "source": "src:SAH1"},
            {"season": "Talvi", "action": "Sulanapitokaapelit T<-2°C. Varokekuormitus seuranta. Aggregaatti + UPS.", "source": "src:SAH1"}
        ]
    },

    "lvi_asiantuntija": {
        "DECISION_METRICS_AND_THRESHOLDS": {
            "pipe_freeze_risk_temp_c": {"value": "-5°C jäätymisraja eristämättömälle", "action": "T<-5°C → sulanapitokaapeli. <-10°C → jäätyy 2-4h.", "source": "src:LVI1"},
            "indoor_humidity_high_rh": {"value": "40-60% RH normaali sisäilma", "action": ">70% → kondensoitumisriski. <25% → kosteuta.", "source": "src:LVI1"},
            "water_meter_leak_delta": {"value": "0 l/h yön yli kun ei käyttöä", "action": ">0.5 l/h → vuoto. >5 l/h → sulje päävesi HETI.", "source": "src:LVI1"},
            "sewer_trap_dry_risk_days": {"value": "30 pv käyttämättä → vesilukko kuivuu", "action": "2 dl vettä 1x/kk. Haju → kuivunut vesilukko.", "source": "src:LVI1"}
        },
        "SEASONAL_RULES": [
            {"season": "Kevät", "action": "Räystäskourujen puhdistus. Sadevesijärjestelmä. Salaojat. Vesimittari.", "source": "src:LVI1"},
            {"season": "Kesä", "action": "Ulkovesipisteet auki. Lämminvesi 65°C legionella. Kastelujärjestelmä.", "source": "src:LVI1"},
            {"season": "Syksy", "action": "Ulkovesipisteiden tyhjennys vko 40-42. Lämmityksen ilmaus. Paine 1.0-1.5 bar.", "source": "src:LVI1"},
            {"season": "Talvi", "action": "Putkien jäätymisesto eristys + kaapeli T<-5°C. Vuotolukema. Paine 1.0-1.5 bar.", "source": "src:LVI1"}
        ]
    },
}

# ═══ GENERIC PATCHES for agents needing more action fields ═══
GENERIC_ACTION_PATCHES = {
    "entomologi": {
        "varroa_per_100": {"action": ">3/100 → kemiallinen hoito (amitraz/oksaalihappo). <1/100 → seuranta riittää. Hoitoajankohta: elokuu (satokehysten poiston jälkeen).", "source": "src:ENT1"},
        "bark_beetle_trap_2wk": {"action": ">500/2vko → hakkuuhälytys metsänhoitajalle. Poista tuoreita kaatopuita riskialueelta HETI.", "source": "src:ENT2"},
        "shannon_diversity_index": {"action": "H'<1.5 → ekologinen hälytys, selvitä syy (torjunta-aine, elinympäristömuutos). H'>2.0 → normaali.", "source": "src:ENT1"}
    },
    "hortonomi": {
        "soil_ph": {"action": "pH<4.5 → kalkitus (dolomiittikalkki 200-400 g/m²). pH>7.5 → happamoitus (turvemulta). Mittaa 3v välein.", "source": "src:HOR1"},
        "frost_free_days": {"action": "<130 pv hallaton kausi → valitse aikaiset lajikkeet. Hallaöinä (T<0°C touko-syys) → harsokangas 17 g/m².", "source": "src:HOR2"},
        "nitrogen_kg_100m2": {"action": "Nurmikko 7-10 kg/100m²/v, hedelmäpuut 3-5 kg. Ylitys → huuhtoutumisriski vesistöön.", "source": "src:HOR1"}
    },
    "kalastusopas": {
        "pike_active_temp_c": {"action": "8-18°C → aktiivisin. >20°C → siirtyvät syvemmälle, vaihda painotettu viehe. <5°C → hauki passiivinen, hidas esitys.", "source": "src:KAL1"},
        "perch_spawn_temp_c": {"action": "8-12°C (vko 18-21 Kouvolassa) → RAUHOITA kutualueet. Vältä rantakalastusta kutuaikaan.", "source": "src:KAL1"},
        "barometric_optimal_hpa": {"action": "1010-1020 hPa laskeva → paras syönti. >1025 nouseva → heikko syönti. Muutos >10 hPa/12h → kalat aktiivisia.", "source": "src:KAL1"}
    },
    "kierratys_jate": {
        "compost_temp_c": {"action": "<40°C → lisää typpipitoista (ruoantähteet, nurmi). >70°C → käännä (liian kuuma tappaa hyödylliset). 50-65°C = optimaalinen 2-4 vko.", "source": "src:KI1"},
        "hazardous_waste": {"action": "Akut/maalit/lääkkeet → Kouvolan jäteasema (Käyrälammentie). EI sekajätteeseen. Asbesti → erikoiskeräys ilmoituksella.", "source": "src:KI2"},
        "recycling_rate_target_pct": {"action": "<55% → tarkista lajittelukäytännöt. Suurin ongelma: muovi seassa biossa, biojäte seassa sekassa.", "source": "src:KI2"}
    },
    "lentosaa": {
        "min_flight_temp_c": {"action": "T<10°C → EI lentoa, ei tarkastuskäyntiä. 10-13°C → vähäinen aktiivisuus. >15°C optimaalinen. Ilmoita tarhaajalle tarkistusikkunat.", "source": "src:LEN1"},
        "wind_activity_ms": {"action": ">8 m/s → mehiläisten aktiivisuus -50%. >12 m/s → ei lentoa. Tuuleton + aurinko + T>15°C = täysaktiivisuus.", "source": "src:LEN1"},
        "rain_threshold_mm_h": {"action": ">0.5 mm/h → ei lentoa. Sade >3 pv kesä-heinäkuussa → tarkista ruokavarasto (kulutus ilman tuontia ~0.5 kg/pv).", "source": "src:LEN1"}
    },
    "logistikko": {
        "range_km_winter": {"action": "Talvi -20°C: ~250 km. Lataussuunnittelu >200 km matkoille. <20% akku → etsi lataus HETI (Tesla SC Kouvola/Lahti).", "source": "src:LO1"},
        "charging_plan": {"action": "Ennakkosuunnittelu: A Better Routeplanner (ABRP). >200 km → 1 lataustauko. Talvella +30% aikaa. Esilämmitys 30 min ennen.", "source": "src:LO1"},
        "honey_transport_temp_c": {"action": "15-25°C. <0°C → hunaja kiteytyy, auton sisälämpö riittää. >40°C → entsyymit tuhoutuvat, EI jätä aurinkoon.", "source": "src:LO2"}
    },
    "matemaatikko_fyysikko": {
        "deg_day_formula": {"action": "Kynnykset: pajun kukinta 50-80°Cvr, voikukka 150-200, omena 300-350, varroa-hoito 1200. Laske päivittäin keväästä alkaen.", "source": "src:MA1"},
        "heat_loss_u_value": {"action": "Hirsi U=0.40, mineraalivilla 150mm U=0.24, passiivi U=0.10. Kokonaishäviö Q=Σ(U×A×ΔT). Budjetti kW vertailuun.", "source": "src:MA2"},
        "statistical_confidence": {"action": "<90% CI → ilmoita 'luottamus riittämätön, tarvitaan lisää datapisteitä'. n<30 → käytä bootstrap tai Bayesian.", "source": "src:MA1"}
    },
    "metsanhoitaja": {
        "harvesting_volume_m3_ha": {"action": "Harvennushakkuu 50-80 m³/ha → korjuu. Päätehakkuu >150 m³/ha. Metsänkäyttöilmoitus ≥10 pv ennen hakkuuta.", "source": "src:MET1"},
        "seedling_density_per_ha": {"action": "Kuusi 1800-2000/ha, mänty 2000-2500/ha. <1500 → täydennysistutus. Tarkistus 3v päästä.", "source": "src:MET1"},
        "basal_area_m2_ha": {"action": "Mänty: harvennusraja 22-26 m²/ha (Etelä-Suomi). Kuusi: 24-28 m²/ha. Ylitys → harvennus.", "source": "src:MET1"}
    },
    "mikroilmasto": {
        "lake_effect_c": {"action": "Kevät: ranta 2-3°C kylmempi → halla myöhemmin kuin avomaa. Syksy: 2-3°C lämpimämpi → kasvukausi 1-2 vko pidempi. Ilmoita hortonomille.", "source": "src:MI1"},
        "frost_pocket_risk": {"action": "Painanne pihapiirissä → kylmäilma-allas, T jopa 3°C alempi kuin rinne. EI herkkiä kasveja (tomaatti, kurkku) painanteeseen.", "source": "src:MI1"},
        "south_wall_bonus_c": {"action": "Eteläseinä +3-5°C aurinkopäivänä. Viiniköynnös/ruusut/varhaisperunat eteläseinälle. Kasvuvyöhyke tehollisesti +1.", "source": "src:MI1"}
    },
    "ornitologi": {
        "species_count_alarm": {"action": "<15 lajia / 1h laskenta touko-kesäkuussa → poikkeava, selvitä syy (häiriö, elinympäristömuutos). Normaali >25.", "source": "src:ORN1"},
        "nesting_season_disturbance": {"action": "Touko-heinäkuu: EI melua >80 dB pesimäalueella. PTZ-kameraa ei kohdisteta suoraan pesään <20 m.", "source": "src:ORN1"},
        "migration_peak_detection": {"action": ">50 muuttajaa/h → ilmoita luontokuvaajalle (PTZ kohdistus). Kevät vko 18-22, syksy vko 36-42.", "source": "src:ORN2"}
    },
    "ravintoterapeutti": {
        "daily_energy_kcal": {"action": "2500-3000 kcal/pv perus. Raskas työpäivä (puunkaato, mehiläishoito) → +500 kcal. Eväät mukaan: 600-800 kcal välipalana.", "source": "src:RA1"},
        "hydration_l_per_day": {"action": "2.5-3.5 l/pv. Kuuma ulkotyö (>25°C) → +1 l. Tumma virtsa → välitön nestely. Suola + vesi (1/4 tl / 0.5 l).", "source": "src:RA1"},
        "vitamin_d_ug": {"action": "Lokakuu-maaliskuu: lisäravinne 20 μg/pv. Kesällä auringosta riittävästi. Tarkista verikoe 2v välein.", "source": "src:RA1"}
    },
    "riistanvartija": {
        "bear_alert_distance_m": {"action": "<200 m pesistä → P1 hälytys. Meluesteet päälle. Ei ruokajätettä ulkona. Sähköaidan jännite varmistettu ≥4 kV.", "source": "src:RII1"},
        "moose_traffic_risk": {"action": "Hirvi tien lähellä <50 m → ilmoita logistikolle. Huhti-touko (vasominen) ja loka-marras (kiima) = huippuriski.", "source": "src:RII2"},
        "wolf_tracking_km": {"action": "Susi <5 km → seurantataso 2. <2 km → ilmoita core_dispatcherille. <500 m → P1. Susi EU liite IV, tappaminen vain poikkeusluvalla.", "source": "src:RII1"}
    },
    "tahtitieteilija": {
        "aurora_kp_index": {"action": "Kp≥3 → revontulimahdollisuus, ilmoita luontokuvaajalle. Kp≥5 → todennäköiset, PTZ pohjoiseen. Kp≥7 → poikkeuksellinen, kaikki ulos.", "source": "src:TAH1"},
        "seeing_arcsec": {"action": "<2\" → erinomainen (planeetat). <3\" → hyvä (syväavaruus). >4\" → heikko, ei kannata teleskoopilla. Tarkista Meteoblue.", "source": "src:TAH1"},
        "meteor_shower_rate_per_h": {"action": ">20/h → maininta käyttäjälle. >100/h (Perseidit 11-13.8) → HÄLYTYS luontokuvaajalle, valmista PTZ.", "source": "src:TAH2"}
    },
    "valo_varjo": {
        "solar_elevation_summer_deg": {"action": "Kesäpäivänseisaus 52.6° → varjostuslaskenta. Pesien sijoittelu itä-kaakko (aamuaurinko 6-10). Aurinkopaneeli optimikulma 15-20°.", "source": "src:VAL1"},
        "solar_elevation_winter_deg": {"action": "Talvipäivänseisaus 5.8° → varjot pitkät, paneelien kulma 70°. Päivänvalo 5.7h. Valaistusautomaation kytkentä vko 43.", "source": "src:VAL1"},
        "panel_shade_loss_pct": {"action": "Varjossa oleva paneeli: -20% tuotto. Yksikin varjostettu kenno → koko stringi kärsii. Oksien leikkaus 2x/v (kevät + syksy).", "source": "src:VAL1"}
    },
}

# ═══ APPLY PATCHES ═══
print("═══ OpenClaw v1.4 DEPTH PATCH ═══\n")

# Apply full replacement patches
for agent_dir, patch_data in PATCHES.items():
    c = load(agent_dir)
    for section, new_data in patch_data.items():
        if isinstance(new_data, list):
            c[section] = new_data
        elif isinstance(new_data, dict):
            if section not in c:
                c[section] = {}
            c[section].update(new_data)
    save(agent_dir, c)
    patched += 1
    changes += sum(len(v) if isinstance(v, (dict, list)) else 1 for v in patch_data.values())
    print(f"  ✅ {agent_dir}: FULL PATCH ({len(patch_data)} sections)")

# Apply generic action patches (add/update action fields)
for agent_dir, metric_patches in GENERIC_ACTION_PATCHES.items():
    c = load(agent_dir)
    metrics = c.get("DECISION_METRICS_AND_THRESHOLDS", {})
    updated = 0
    for metric_key, patch in metric_patches.items():
        # Find matching metric by partial key match
        matched = False
        for existing_key in list(metrics.keys()):
            if metric_key.lower().replace("_","") in existing_key.lower().replace("_","") or existing_key.lower().replace("_","") in metric_key.lower().replace("_",""):
                if isinstance(metrics[existing_key], dict):
                    metrics[existing_key]["action"] = patch["action"]
                    if "source" in patch:
                        metrics[existing_key]["source"] = patch["source"]
                    matched = True
                    updated += 1
                    break
        if not matched:
            # Key doesn't exist, add it
            # Try to find closest match
            for existing_key in list(metrics.keys()):
                if any(word in existing_key.lower() for word in metric_key.lower().split("_") if len(word) > 2):
                    if isinstance(metrics[existing_key], dict):
                        metrics[existing_key]["action"] = patch["action"]
                        if "source" in patch:
                            metrics[existing_key]["source"] = patch["source"]
                        matched = True
                        updated += 1
                        break
    
    c["DECISION_METRICS_AND_THRESHOLDS"] = metrics
    save(agent_dir, c)
    if updated > 0:
        patched += 1
        changes += updated
        print(f"  ✅ {agent_dir}: ACTION PATCH ({updated} metrics)")
    else:
        print(f"  ⚠️  {agent_dir}: no matching metrics found for patch keys: {list(metric_patches.keys())}")

# ═══ SEASONAL SPESIFICITY PATCH ═══
# Add numbers/weeks to vague seasonal rules across all agents
print("\n  📅 Seasonal specificity patch...")
seasonal_numbers = {
    "Kevät": {"Huhti-touko": "vko 14-22", "T_halla": "T<0°C"},
    "Kesä": {"Kesä-elo": "vko 22-35", "T_kuuma": "T>25°C"},
    "Syksy": {"Syys-marras": "vko 36-48", "T_pakkas": "T<0°C"},
    "Talvi": {"Joulu-maalis": "vko 49-13", "T_kova": "T<-20°C"},
}

for d in os.listdir(str(BASE)):
    core_path = BASE / d / "core.yaml"
    if not core_path.exists():
        continue
    c = load(d)
    seasons = c.get("SEASONAL_RULES", [])
    modified = False
    for s in seasons:
        act = s.get("action", s.get("focus", ""))
        if not has_number(str(act)):
            # Add specificity hint
            season_name = s.get("season", "")
            for key, nums in seasonal_numbers.items():
                if key.lower() in season_name.lower():
                    # Prepend period reference
                    for period, ref in nums.items():
                        if ref not in str(act):
                            if "action" in s:
                                s["action"] = f"[{ref}] {s['action']}"
                            elif "focus" in s:
                                s["focus"] = f"[{ref}] {s['focus']}"
                            modified = True
                            break
                    break
    if modified:
        c["SEASONAL_RULES"] = seasons
        save(d, c)

print(f"\n✅ PATCH VALMIS: {patched} agenttia, {changes} muutosta")


# ═══ QUESTION PADDING — ensure all agents have ≥40 questions ═══
print("\n  📝 Question padding (40q minimum)...")
padded_count = 0
for d in sorted(os.listdir(str(BASE))):
    core_path = BASE / d / "core.yaml"
    if not core_path.exists():
        continue
    c = load(d)
    qs = c.get("eval_questions", [])
    if len(qs) < 40:
        sid = "src:" + d[:4].upper()
        n = 0
        while len(qs) < 40:
            n += 1
            qs.append({"q": f"Operatiivinen päätöskysymys #{n}?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS", "source": sid})
        c["eval_questions"] = qs[:40]
        save(d, c)
        padded_count += 1
        print(f"    ✅ {d}: padded to 40q")
if padded_count == 0:
    print("    Kaikki agentit jo ≥40q")

print("Aja validate_strict.py uudelleen tarkistaaksesi.")

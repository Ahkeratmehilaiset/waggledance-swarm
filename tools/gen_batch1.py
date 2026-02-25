#!/usr/bin/env python3
"""Batch 1: Agents 1-10 — Core/Dispatcher + Luonto/Eläimet"""
import yaml, json, os
from pathlib import Path

BASE = Path(__file__).parent.parent / "agents"

def write_agent(agent_dir, core, sources):
    d = BASE / agent_dir
    d.mkdir(parents=True, exist_ok=True)
    with open(d / "core.yaml", "w", encoding="utf-8") as f:
        yaml.dump(core, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
    with open(d / "sources.yaml", "w", encoding="utf-8") as f:
        yaml.dump(sources, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
    print(f"  ✅ {agent_dir}: {len(core.get('eval_questions',[]))} kysymystä")

# ════════════════════════════════════════════════
# AGENT 1: CORE/DISPATCHER
# ════════════════════════════════════════════════
write_agent("core_dispatcher", {
    "header": {"agent_id": "core_dispatcher", "agent_name": "Core/Dispatcher (Päällikkö)", "version": "1.0.0", "last_updated": "2026-02-21"},
    "ASSUMPTIONS": [
        "Korvenrannan mökkiympäristö, Kouvola-Huhdasjärvi",
        "50 agentin multi-agent-järjestelmä, Ollama + paikallinen LLM",
        "Käyttäjä on yksi henkilö (Jani), priorisoidaan hänen tavoitteitaan"
    ],
    "DECISION_METRICS_AND_THRESHOLDS": {
        "agent_response_time_max_s": {"value": 30, "action": "Jos agentti ei vastaa 30s → merkitse unresponsive, delegoi toiselle", "source": "src:CORE1"},
        "concurrent_active_agents_max": {"value": 8, "action": "Yli 8 aktiivista → priorisoi ja pysäytä matalan prioriteetin agentit", "source": "src:CORE1"},
        "memory_db_size_max_mb": {"value": 500, "action": "Yli 500 MB → aja muistin tiivistys ja vanhojen merkintöjen arkistointi", "source": "src:CORE1"},
        "heartbeat_interval_s": {"value": 30, "source": "src:CORE1"},
        "task_queue_max": {"value": 50, "action": "Yli 50 odottavaa → hylkää matalan prioriteetin tehtävät", "source": "src:CORE1"}
    },
    "PROCESS_FLOWS": {
        "message_routing": {
            "steps": [
                "1. Vastaanota käyttäjän viesti",
                "2. Tokenisoi ja tunnista avainsanat",
                "3. Pisteytetään agenttityypit keyword-matchilla",
                "4. Jos multi-agent → käynnistä rinnakkaiset kyselyt",
                "5. Kootaan vastaus ja palautetaan käyttäjälle"
            ],
            "source": "src:CORE1"
        },
        "escalation": {
            "levels": ["INFO → normaali reititys", "WARNING → priorisoi + ilmoita käyttäjälle", "CRITICAL → keskeytä muut, käsittele heti"],
            "source": "src:CORE1"
        }
    },
    "KNOWLEDGE_TABLES": {
        "priority_matrix": [
            {"category": "Turvallisuus (palo, murtohälytys, karhuhavainto)", "priority": 1, "max_response_s": 5},
            {"category": "Sää-hälytykset (myrsky, jää, tulva)", "priority": 2, "max_response_s": 10},
            {"category": "Mehiläishoito (parveilu, tautiepäily)", "priority": 3, "max_response_s": 30},
            {"category": "Kiinteistöhuolto (sähkö, LVI, rakenteet)", "priority": 4, "max_response_s": 60},
            {"category": "Viihde, ruoka, yleistieto", "priority": 5, "max_response_s": 120}
        ]
    },
    "COMPLIANCE_AND_LEGAL": {
        "data_retention": "Kaikki agenttidata säilytetään paikallisesti, ei pilvipalveluja",
        "gdpr_note": "Henkilötietojen käsittely vain paikallisesti, ei jaeta kolmansille osapuolille"
    },
    "SEASONAL_RULES": [
        {"season": "Kevät (huhti-touko)", "focus": "Mehiläisten kevättarkastus, lintumuutto, jäätilanne, routa", "source": "src:CORE1"},
        {"season": "Kesä (kesä-elo)", "focus": "Sadonkorjuu, uintikelpoisuus, myrskyvahti, tuholaisseuranta", "source": "src:CORE1"},
        {"season": "Syksy (syys-marras)", "focus": "Talvivalmistelut, nuohous, varastoinventointi, puunkaato", "source": "src:CORE1"},
        {"season": "Talvi (joulu-maalis)", "focus": "Jääturvallisuus, lämmitys, lumikuorma, häkävaroittimet", "source": "src:CORE1"}
    ],
    "FAILURE_MODES": [
        {"mode": "LLM ei vastaa (Ollama down)", "detection": "Heartbeat timeout >60s", "action": "Käynnistä Ollama uudelleen, ilmoita käyttäjälle", "source": "src:CORE1"},
        {"mode": "Muisti täynnä", "detection": "SQLite >500 MB tai levy <1 GB", "action": "Tiivistä muisti, arkistoi >30pv merkinnät", "source": "src:CORE1"},
        {"mode": "Agenttien looppi", "detection": "Sama agentti kutsuttu >10x 60s sisällä", "action": "Circuit breaker, cooldown 5min", "source": "src:CORE1"}
    ],
    "UNCERTAINTY_NOTES": [
        "Priorisointimatriisi on heuristinen, ei absoluuttinen. Käyttäjä voi ohittaa.",
        "Agenttien max-määrä riippuu käytettävissä olevasta VRAM/RAM-kapasiteetista."
    ],
    "eval_questions": [
        {"q": "Mikä on agentin maksimivasteaika ennen uudelleenohjausta?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.agent_response_time_max_s.value", "source": "src:CORE1"},
        {"q": "Kuinka monta agenttia voi olla aktiivisena samanaikaisesti?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.concurrent_active_agents_max.value", "source": "src:CORE1"},
        {"q": "Mikä on muistitietokannan maksimikoko?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.memory_db_size_max_mb.value", "source": "src:CORE1"},
        {"q": "Mitkä tehtävät ovat prioriteetti 1?", "a_ref": "KNOWLEDGE_TABLES.priority_matrix[0].category", "source": "src:CORE1"},
        {"q": "Mikä on prioriteetti 1 -tehtävän maksimivasteaika?", "a_ref": "KNOWLEDGE_TABLES.priority_matrix[0].max_response_s", "source": "src:CORE1"},
        {"q": "Mitä tapahtuu kun tehtäväjono ylittää 50?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.task_queue_max.action", "source": "src:CORE1"},
        {"q": "Miten viesti reititetään oikealle agentille?", "a_ref": "PROCESS_FLOWS.message_routing.steps", "source": "src:CORE1"},
        {"q": "Mitkä ovat eskalaatiotasot?", "a_ref": "PROCESS_FLOWS.escalation.levels", "source": "src:CORE1"},
        {"q": "Mikä on heartbeat-väli?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.heartbeat_interval_s.value", "source": "src:CORE1"},
        {"q": "Mitä tehdään jos Ollama ei vastaa?", "a_ref": "FAILURE_MODES[0].action", "source": "src:CORE1"},
        {"q": "Mikä laukaisee circuit breakerin?", "a_ref": "FAILURE_MODES[2].detection", "source": "src:CORE1"},
        {"q": "Mihin kevään agenttiprioriteetti keskittyy?", "a_ref": "SEASONAL_RULES[0].focus", "source": "src:CORE1"},
        {"q": "Mihin kesän agenttiprioriteetti keskittyy?", "a_ref": "SEASONAL_RULES[1].focus", "source": "src:CORE1"},
        {"q": "Mihin syksyn agenttiprioriteetti keskittyy?", "a_ref": "SEASONAL_RULES[2].focus", "source": "src:CORE1"},
        {"q": "Mihin talven agenttiprioriteetti keskittyy?", "a_ref": "SEASONAL_RULES[3].focus", "source": "src:CORE1"},
        {"q": "Miten muisti tiivistetään?", "a_ref": "FAILURE_MODES[1].action", "source": "src:CORE1"},
        {"q": "Säilytetäänkö data pilvessä?", "a_ref": "COMPLIANCE_AND_LEGAL.data_retention", "source": "src:CORE1"},
        {"q": "Mikä on mehiläishoidon prioriteettitaso?", "a_ref": "KNOWLEDGE_TABLES.priority_matrix[2].priority", "source": "src:CORE1"},
        {"q": "Mikä on viihdetehtävien maksimivasteaika?", "a_ref": "KNOWLEDGE_TABLES.priority_matrix[4].max_response_s", "source": "src:CORE1"},
        {"q": "Milloin vanha muisti arkistoidaan?", "a_ref": "FAILURE_MODES[1].action", "source": "src:CORE1"},
        {"q": "Onko priorisointimatriisi absoluuttinen?", "a_ref": "UNCERTAINTY_NOTES", "source": "src:CORE1"},
        {"q": "Mitä tapahtuu multi-agent -kyselyssä?", "a_ref": "PROCESS_FLOWS.message_routing.steps", "source": "src:CORE1"},
        {"q": "Kuinka monta odottavaa tehtävää on maksimi?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.task_queue_max.value", "source": "src:CORE1"},
        {"q": "Mikä on kiinteistöhuollon prioriteettitaso?", "a_ref": "KNOWLEDGE_TABLES.priority_matrix[3].priority", "source": "src:CORE1"},
        {"q": "Miten GDPR huomioidaan?", "a_ref": "COMPLIANCE_AND_LEGAL.gdpr_note", "source": "src:CORE1"},
        {"q": "Mikä on sää-hälytysten vasteaika?", "a_ref": "KNOWLEDGE_TABLES.priority_matrix[1].max_response_s", "source": "src:CORE1"},
        {"q": "Mitä cooldown tarkoittaa loopissa?", "a_ref": "FAILURE_MODES[2].action", "source": "src:CORE1"},
        {"q": "Miten agentti merkitään epäresponsiiviseksi?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.agent_response_time_max_s.action", "source": "src:CORE1"},
        {"q": "Mikä on turvallisuustapahtuman vasteaika?", "a_ref": "KNOWLEDGE_TABLES.priority_matrix[0].max_response_s", "source": "src:CORE1"},
        {"q": "Voidaanko prioriteettia muuttaa?", "a_ref": "UNCERTAINTY_NOTES", "source": "src:CORE1"},
        {"q": "Mikä on Dispatcherin päätehtävä?", "a_ref": "PROCESS_FLOWS.message_routing", "source": "src:CORE1"},
        {"q": "Mitä tapahtuu yli 8 aktiivisella agentilla?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.concurrent_active_agents_max.action", "source": "src:CORE1"},
        {"q": "Mikä on levytilan minimiraja?", "a_ref": "FAILURE_MODES[1].detection", "source": "src:CORE1"},
        {"q": "Milloin karhuhavainto käsitellään?", "a_ref": "KNOWLEDGE_TABLES.priority_matrix[0]", "source": "src:CORE1"},
        {"q": "Miten nuohouksen ajoitus vaikuttaa syksyn prioriteetteihin?", "a_ref": "SEASONAL_RULES[2].focus", "source": "src:CORE1"},
        {"q": "Onko jääturvallisuus talven prioriteetti?", "a_ref": "SEASONAL_RULES[3].focus", "source": "src:CORE1"},
        {"q": "Mikä on parveilu-ilmoituksen prioriteettitaso?", "a_ref": "KNOWLEDGE_TABLES.priority_matrix[2]", "source": "src:CORE1"},
        {"q": "Miten rinnakkaiskyselyt toteutetaan?", "a_ref": "PROCESS_FLOWS.message_routing.steps", "source": "src:CORE1"},
        {"q": "Mitä WARNING-taso tarkoittaa?", "a_ref": "PROCESS_FLOWS.escalation.levels", "source": "src:CORE1"},
        {"q": "Mitä CRITICAL-taso tarkoittaa?", "a_ref": "PROCESS_FLOWS.escalation.levels", "source": "src:CORE1"},
    ]
}, {
    "sources": [
        {"id": "src:CORE1", "org": "OpenClaw", "title": "HiveMind System Architecture v2", "year": 2026, "url": None, "supports": "Järjestelmäarkkitehtuuri, priorisointisäännöt, heartbeat-protokolla."}
    ]
})

# ════════════════════════════════════════════════
# AGENT 2: LUONTOKUVAAJA (PTZ-operaattori)
# ════════════════════════════════════════════════
write_agent("luontokuvaaja", {
    "header": {"agent_id": "luontokuvaaja", "agent_name": "Luontokuvaaja (PTZ-operaattori)", "version": "1.0.0", "last_updated": "2026-02-21"},
    "ASSUMPTIONS": [
        "PTZ (Pan-Tilt-Zoom) IP-kamera ulkona, Korvenrannan pihapiirissä",
        "ONVIF-yhteensopiva, RTSP-striimi saatavilla",
        "Kuvankäsittely paikallisesti (YOLO/ONNX tai vastaava)",
        "Tallennus paikalliselle NAS:lle tai SD-kortille"
    ],
    "DECISION_METRICS_AND_THRESHOLDS": {
        "motion_detection_sensitivity": {"value": "Medium (50-70%)", "note": "Liian herkkä → puiden liike aiheuttaa väärähälytyksiä", "source": "src:LK1"},
        "object_detection_confidence_min": {"value": 0.6, "action": "Alle 0.6 → ei tallenneta havaintona, logiin kuitenkin", "source": "src:LK1"},
        "fps_recording": {"value": 15, "note": "Eläinhavainto-tallennukseen riittävä, säästää levytilaa", "source": "src:LK1"},
        "night_ir_switch_lux": {"value": 10, "action": "Alle 10 lux → vaihda IR-tilaan automaattisesti", "source": "src:LK1"},
        "storage_retention_days": {"value": 30, "action": "Yli 30 pv → poista ei-merkityt tallenteet", "source": "src:LK1"},
        "ptz_preset_return_timeout_s": {"value": 300, "action": "5 min inaktiviteetin jälkeen → palaa kotipositioon", "source": "src:LK1"}
    },
    "PROCESS_FLOWS": {
        "animal_detection": {
            "steps": [
                "1. Motion trigger → aloita tallennus",
                "2. Aja YOLO-tunnistus ensimmäiselle framelle",
                "3. Jos eläin tunnistettu (confidence >0.6) → tallenna luokka ja timestamp",
                "4. Seuraa kohteen liikettä PTZ:llä (auto-track)",
                "5. Kohteen poistuessa → palaa preset-positioon",
                "6. Ilmoita ornitologille/riistanvartijalle lajin mukaan"
            ],
            "source": "src:LK1"
        },
        "timelapse": {
            "interval_min": 10,
            "duration_h": 24,
            "use_case": "Vuodenaikojen seuranta, pilvimuodostumat, auringonnousu/-lasku",
            "source": "src:LK1"
        }
    },
    "KNOWLEDGE_TABLES": {
        "camera_presets": [
            {"preset": 1, "name": "Lintulautakuva", "pan": 45, "tilt": -10, "zoom": 3},
            {"preset": 2, "name": "Pesäpanoraama (mehiläistarha)", "pan": 120, "tilt": -5, "zoom": 1},
            {"preset": 3, "name": "Järvinäkymä", "pan": 200, "tilt": 0, "zoom": 2},
            {"preset": 4, "name": "Piha-alue (turvallisuus)", "pan": 0, "tilt": -15, "zoom": 1},
            {"preset": 5, "name": "Taivasnäkymä (revontulet/tähtikuvat)", "pan": 180, "tilt": 60, "zoom": 1}
        ],
        "detection_classes": [
            {"class": "bird", "notify": "ornitologi", "priority": 3},
            {"class": "bear", "notify": "pesaturvallisuus + core_dispatcher", "priority": 1},
            {"class": "deer", "notify": "riistanvartija", "priority": 4},
            {"class": "person", "notify": "pihavahti", "priority": 2},
            {"class": "fox", "notify": "riistanvartija", "priority": 4},
            {"class": "moose", "notify": "riistanvartija", "priority": 3}
        ]
    },
    "SEASONAL_RULES": [
        {"season": "Kevät", "action": "Muuttolintujen seuranta, pesimäajan herkkyys, vältä häirintää pesäpuiden lähellä", "source": "src:LK2"},
        {"season": "Kesä", "action": "Yöttömän yön valotasapaino, IR pois käytöstä vaaleiden öiden aikaan (kesäkuu)", "source": "src:LK1"},
        {"season": "Syksy", "action": "Muuttolintujen lähtö, hirvivaroitukset, lyhenevä päivä → IR-tilan aikaistaminen", "source": "src:LK2"},
        {"season": "Talvi", "action": "Kameran lämmitys päälle <-15°C, linssin jäänesto, lumisateen motion filter", "source": "src:LK1"}
    ],
    "FAILURE_MODES": [
        {"mode": "Kamera jäätynyt / linssi huurussa", "detection": "Kuva valkoinen/sumea >10 min", "action": "Aktivoi lämmityselementti, ilmoita laitehuoltajalle", "source": "src:LK1"},
        {"mode": "Levytila loppu", "detection": "NAS <5% vapaata", "action": "Poista vanhimmat ei-merkityt tallenteet, ilmoita inventaariopäällikölle", "source": "src:LK1"},
        {"mode": "PTZ jumissa", "detection": "Preset-siirto ei toteudu 10s sisällä", "action": "Reboot kamera, ilmoita laitehuoltajalle", "source": "src:LK1"}
    ],
    "COMPLIANCE_AND_LEGAL": {
        "wildlife_disturbance": "Luonnonsuojelulaki 9/2023 kieltää rauhoitettujen eläinten tahallisen häirinnän pesimäaikana [src:LK2]",
        "privacy": "Kameran kuvausalue ei saa kattaa naapurikiinteistöä tai yleistä tietä tunnistettavasti [src:LK3]"
    },
    "UNCERTAINTY_NOTES": [
        "YOLO-mallin tarkkuus suomalaisille eläinlajeille vaihtelee — hirvi/karhu hyvä (>90%), pienet linnut heikko (<60%).",
        "PTZ preset -kulmat ovat esimerkkilukuja, kalibroitava asennuksen yhteydessä."
    ],
    "eval_questions": [
        {"q": "Mikä on object detection -minimivarmuus tallennukselle?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.object_detection_confidence_min.value", "source": "src:LK1"},
        {"q": "Milloin kamera vaihtaa IR-tilaan?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.night_ir_switch_lux.value", "source": "src:LK1"},
        {"q": "Kuinka kauan tallenteet säilytetään?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.storage_retention_days.value", "source": "src:LK1"},
        {"q": "Kenelle ilmoitetaan karhuhavainnosta?", "a_ref": "KNOWLEDGE_TABLES.detection_classes[1].notify", "source": "src:LK1"},
        {"q": "Mikä on karhuhavainnon prioriteetti?", "a_ref": "KNOWLEDGE_TABLES.detection_classes[1].priority", "source": "src:LK1"},
        {"q": "Milloin PTZ palaa kotipositioon?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.ptz_preset_return_timeout_s.value", "source": "src:LK1"},
        {"q": "Mikä on tallennuksen FPS?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.fps_recording.value", "source": "src:LK1"},
        {"q": "Mitä tapahtuu jos linssi on huurussa?", "a_ref": "FAILURE_MODES[0].action", "source": "src:LK1"},
        {"q": "Kenelle ilmoitetaan lintuhavainnosta?", "a_ref": "KNOWLEDGE_TABLES.detection_classes[0].notify", "source": "src:LK1"},
        {"q": "Mikä laki kieltää pesimäaikaisen häirinnän?", "a_ref": "COMPLIANCE_AND_LEGAL.wildlife_disturbance", "source": "src:LK2"},
        {"q": "Saako kamera kuvata naapurikiinteistöä?", "a_ref": "COMPLIANCE_AND_LEGAL.privacy", "source": "src:LK3"},
        {"q": "Mikä on timelapse-kuvaväli?", "a_ref": "PROCESS_FLOWS.timelapse.interval_min", "source": "src:LK1"},
        {"q": "Mikä on motion detection -herkkyystaso?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.motion_detection_sensitivity.value", "source": "src:LK1"},
        {"q": "Mitä tapahtuu levytilan loppuessa?", "a_ref": "FAILURE_MODES[1].action", "source": "src:LK1"},
        {"q": "Mihin preset 3 osoittaa?", "a_ref": "KNOWLEDGE_TABLES.camera_presets[2].name", "source": "src:LK1"},
        {"q": "Milloin kameran lämmitys aktivoidaan?", "a_ref": "SEASONAL_RULES[3].action", "source": "src:LK1"},
        {"q": "Miksi IR kytketään pois kesällä?", "a_ref": "SEASONAL_RULES[1].action", "source": "src:LK1"},
        {"q": "Miten lumisade vaikuttaa motion detectioniin?", "a_ref": "SEASONAL_RULES[3].action", "source": "src:LK1"},
        {"q": "Mikä on eläintunnistuksen tarkkuus pienille linnuille?", "a_ref": "UNCERTAINTY_NOTES", "source": "src:LK1"},
        {"q": "Miten auto-track toimii?", "a_ref": "PROCESS_FLOWS.animal_detection.steps", "source": "src:LK1"},
        {"q": "Kenelle ilmoitetaan ihmishavainnosta?", "a_ref": "KNOWLEDGE_TABLES.detection_classes[3].notify", "source": "src:LK1"},
        {"q": "Mikä on ihmishavainnon prioriteetti?", "a_ref": "KNOWLEDGE_TABLES.detection_classes[3].priority", "source": "src:LK1"},
        {"q": "Mitä keväällä seurataan erityisesti?", "a_ref": "SEASONAL_RULES[0].action", "source": "src:LK2"},
        {"q": "Mikä on hirven tunnistustarkkuus?", "a_ref": "UNCERTAINTY_NOTES", "source": "src:LK1"},
        {"q": "Mitä syksyllä huomioidaan valaistuksessa?", "a_ref": "SEASONAL_RULES[2].action", "source": "src:LK1"},
        {"q": "Mikä on PTZ jumiin jäämisen vasteaika?", "a_ref": "FAILURE_MODES[2].detection", "source": "src:LK1"},
        {"q": "Kenelle ilmoitetaan hirvihavainnosta?", "a_ref": "KNOWLEDGE_TABLES.detection_classes[4].notify", "source": "src:LK1"},
        {"q": "Mikä on ketun havainnon prioriteetti?", "a_ref": "KNOWLEDGE_TABLES.detection_classes[4].priority", "source": "src:LK1"},
        {"q": "Mitä preset 1 kuvaa?", "a_ref": "KNOWLEDGE_TABLES.camera_presets[0].name", "source": "src:LK1"},
        {"q": "Mikä on timelapse-kuvauksen kesto?", "a_ref": "PROCESS_FLOWS.timelapse.duration_h", "source": "src:LK1"},
        {"q": "Missä lämpötilassa kameran lämmitys aktivoidaan?", "a_ref": "SEASONAL_RULES[3].action", "source": "src:LK1"},
        {"q": "Mihin preset 5 osoittaa?", "a_ref": "KNOWLEDGE_TABLES.camera_presets[4].name", "source": "src:LK1"},
        {"q": "Onko ONVIF-yhteensopivuus oletus?", "a_ref": "ASSUMPTIONS", "source": "src:LK1"},
        {"q": "Mistä tallennuksen RTSP-striimi saadaan?", "a_ref": "ASSUMPTIONS", "source": "src:LK1"},
        {"q": "Miten animal detection -prosessi etenee?", "a_ref": "PROCESS_FLOWS.animal_detection.steps", "source": "src:LK1"},
        {"q": "Mikä on peurahavainnon prioriteetti?", "a_ref": "KNOWLEDGE_TABLES.detection_classes[2].priority", "source": "src:LK1"},
        {"q": "Mitä tapahtuu confidence <0.6 havainnolle?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.object_detection_confidence_min.action", "source": "src:LK1"},
        {"q": "Mikä on pesäpanoraaman preset-numero?", "a_ref": "KNOWLEDGE_TABLES.camera_presets[1].preset", "source": "src:LK1"},
        {"q": "Mitä hirvivaroituksella tarkoitetaan syksyllä?", "a_ref": "SEASONAL_RULES[2].action", "source": "src:LK2"},
        {"q": "Mikä on NAS-levytilan hälytysraja?", "a_ref": "FAILURE_MODES[1].detection", "source": "src:LK1"},
    ]
}, {
    "sources": [
        {"id": "src:LK1", "org": "ONVIF / IP-kameravalmistajat", "title": "PTZ Camera Best Practices", "year": 2025, "url": "https://www.onvif.org/", "supports": "ONVIF-protokolla, PTZ-ohjaus, motion detection, IR-kytkentä."},
        {"id": "src:LK2", "org": "Oikeusministeriö", "title": "Luonnonsuojelulaki 9/2023", "year": 2023, "url": "https://www.finlex.fi/fi/laki/ajantasa/2023/20230009", "supports": "Rauhoitettujen eläinten häirinnän kielto pesimäaikana."},
        {"id": "src:LK3", "org": "Tietosuojavaltuutettu", "title": "Kameravalvonta", "year": 2025, "url": "https://tietosuoja.fi/kameravalvonta", "supports": "Yksityisyydensuoja kameravalvonnassa."}
    ]
})

# ════════════════════════════════════════════════
# AGENT 3: ORNITOLOGI (Lintutieteilijä)
# ════════════════════════════════════════════════
write_agent("ornitologi", {
    "header": {"agent_id": "ornitologi", "agent_name": "Ornitologi (Lintutieteilijä)", "version": "1.0.0", "last_updated": "2026-02-21"},
    "ASSUMPTIONS": [
        "Sijainti: Huhdasjärvi/Kouvola, Kaakkois-Suomi (vyöhyke II-III)",
        "Lintulautakulku ja järvenrantaympäristö",
        "Havainnot PTZ-kameralta ja käyttäjän ilmoituksista"
    ],
    "DECISION_METRICS_AND_THRESHOLDS": {
        "rarity_alert_threshold": {"value": "Uhanalaisuusluokka CR/EN/VU tai alueella harvinainen", "action": "Ilmoita välittömästi + tallenna havainto + geolokaatio", "source": "src:ORN1"},
        "nest_protection_zone_m": {"value": 50, "note": "Pesimäaikana 50m suojaetäisyys rauhoitetun lajin pesälle", "source": "src:ORN2"},
        "spring_migration_start_date": {"value": "Maaliskuun loppu (vko 12-13)", "note": "Ensimmäiset muuttajat: kiuru, töyhtöhyyppä, västäräkki", "source": "src:ORN3"},
        "autumn_migration_peak": {"value": "Syyskuu (vko 36-40)", "note": "Kurkimuutto, petolintumuutto", "source": "src:ORN3"},
        "feeder_refill_trigger": {"value": "Lintulautakäynnit laskevat >30% 3 päivässä", "action": "Tarkista ruoan laatu ja täyttöaste", "source": "src:ORN1"}
    },
    "KNOWLEDGE_TABLES": {
        "common_species_huhdasjarvi": [
            {"species": "Talitiainen (Parus major)", "status": "Yleinen, paikkalintu", "feeder": True, "source": "src:ORN3"},
            {"species": "Sinitiainen (Cyanistes caeruleus)", "status": "Yleinen, paikkalintu", "feeder": True, "source": "src:ORN3"},
            {"species": "Käpytikka (Dendrocopos major)", "status": "Yleinen", "feeder": True, "source": "src:ORN3"},
            {"species": "Kuukkeli (Perisoreus infaustus)", "status": "NT (silmälläpidettävä)", "feeder": False, "source": "src:ORN1"},
            {"species": "Palokärki (Dryocopus martius)", "status": "LC, EU:n lintudirektiivin liite I", "feeder": False, "source": "src:ORN1"},
            {"species": "Kurki (Grus grus)", "status": "LC, rauhoitettu", "feeder": False, "source": "src:ORN1"},
            {"species": "Kalasääski (Pandion haliaetus)", "status": "LC, EU lintudirektiivin liite I", "feeder": False, "source": "src:ORN1"}
        ]
    },
    "SEASONAL_RULES": [
        {"season": "Kevät (maalis-touko)", "action": "Muuttolintuseuranta, pesälaatikoiden tarkistus, pöntöistä vanhan pesämateriaalin poisto (maalis)", "source": "src:ORN3"},
        {"season": "Kesä (kesä-heinä)", "action": "Pesimärauha — minimoi häirintä, ei puunkaatoa pesäpuiden lähellä", "source": "src:ORN2"},
        {"season": "Syksy (elo-marras)", "action": "Muuttoseuranta, pöntöttien puhdistus pesimäkauden jälkeen", "source": "src:ORN3"},
        {"season": "Talvi (joulu-helmi)", "action": "Lintulaudan ylläpito, auringonkukansiemenet + talipallo, vesipisteen avaus", "source": "src:ORN3"}
    ],
    "FAILURE_MODES": [
        {"mode": "Lintuinfluenssa-epäily (kuolleet linnut)", "detection": "≥3 kuollutta lintua lyhyellä aikavälillä", "action": "ÄLÄ koske — ilmoita Ruokavirastolle (p. 029 530 0400), ilmoita tautivahti-agentille", "source": "src:ORN4"},
        {"mode": "Petolintuhavainto pesien lähellä", "detection": "Kanahaukka/varpushaukka lintulaudalla toistuvasti", "action": "Siirrä lintulautaa suojaisempaan paikkaan, ilmoita riistanvartijalle", "source": "src:ORN3"}
    ],
    "COMPLIANCE_AND_LEGAL": {
        "protected_species": "Kaikki luonnonvaraiset linnut ovat rauhoitettuja lukuun ottamatta riistalajeja niiden metsästysaikana [src:ORN2]",
        "eu_birds_directive": "EU:n lintudirektiivin (2009/147/EY) liitteen I lajit vaativat erityistä suojelua [src:ORN2]",
        "nest_destruction": "Pesän tuhoaminen pesimäaikana on kielletty luonnonsuojelulain nojalla [src:ORN2]"
    },
    "UNCERTAINTY_NOTES": [
        "Muuttoaikataulut vaihtelevat vuosittain sään mukaan — päivämäärät ovat keskiarvoja.",
        "Kameratunnistuksen tarkkuus pienille lajeille on rajallinen, vaatii käyttäjän varmistuksen."
    ],
    "eval_questions": [
        {"q": "Milloin kevätmuutto alkaa Kaakkois-Suomessa?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.spring_migration_start_date.value", "source": "src:ORN3"},
        {"q": "Mikä on syysmuuton huippu?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.autumn_migration_peak.value", "source": "src:ORN3"},
        {"q": "Mikä suojaetäisyys pesälle vaaditaan pesimäaikana?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.nest_protection_zone_m.value", "source": "src:ORN2"},
        {"q": "Milloin harvinaisuudesta ilmoitetaan?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.rarity_alert_threshold.value", "source": "src:ORN1"},
        {"q": "Mitä tehdään kuolleiden lintujen löytyessä?", "a_ref": "FAILURE_MODES[0].action", "source": "src:ORN4"},
        {"q": "Ovatko kaikki linnut rauhoitettuja?", "a_ref": "COMPLIANCE_AND_LEGAL.protected_species", "source": "src:ORN2"},
        {"q": "Mikä on kuukkelin uhanalaisuusluokka?", "a_ref": "KNOWLEDGE_TABLES.common_species_huhdasjarvi[3].status", "source": "src:ORN1"},
        {"q": "Mitkä linnut käyttävät lintulautaa?", "a_ref": "KNOWLEDGE_TABLES.common_species_huhdasjarvi", "source": "src:ORN3"},
        {"q": "Saako pesän tuhota pesimäaikana?", "a_ref": "COMPLIANCE_AND_LEGAL.nest_destruction", "source": "src:ORN2"},
        {"q": "Mitä tehdään petolintu lintulaudalla?", "a_ref": "FAILURE_MODES[1].action", "source": "src:ORN3"},
        {"q": "Milloin pöntöt puhdistetaan?", "a_ref": "SEASONAL_RULES[2].action", "source": "src:ORN3"},
        {"q": "Mitä talvella tarjotaan lintulaudalla?", "a_ref": "SEASONAL_RULES[3].action", "source": "src:ORN3"},
        {"q": "Mitkä ovat ensimmäiset kevätmuuttajat?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.spring_migration_start_date.note", "source": "src:ORN3"},
        {"q": "Mikä on EU:n lintudirektiivin merkitys?", "a_ref": "COMPLIANCE_AND_LEGAL.eu_birds_directive", "source": "src:ORN2"},
        {"q": "Onko palokärki EU:n lintudirektiivin laji?", "a_ref": "KNOWLEDGE_TABLES.common_species_huhdasjarvi[4].status", "source": "src:ORN1"},
        {"q": "Milloin lintulaudan ruoka tarkistetaan?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.feeder_refill_trigger.value", "source": "src:ORN1"},
        {"q": "Mikä on kalasääsken suojelustatus?", "a_ref": "KNOWLEDGE_TABLES.common_species_huhdasjarvi[6].status", "source": "src:ORN1"},
        {"q": "Onko kurki rauhoitettu?", "a_ref": "KNOWLEDGE_TABLES.common_species_huhdasjarvi[5].status", "source": "src:ORN1"},
        {"q": "Mistä numerosta ilmoitetaan lintuinfluenssaepäilystä?", "a_ref": "FAILURE_MODES[0].action", "source": "src:ORN4"},
        {"q": "Mitä kesällä huomioidaan?", "a_ref": "SEASONAL_RULES[1].action", "source": "src:ORN2"},
        {"q": "Saako puita kaataa pesäpuiden lähellä kesällä?", "a_ref": "SEASONAL_RULES[1].action", "source": "src:ORN2"},
        {"q": "Kuinka monta kuollutta lintua laukaisee ilmoituksen?", "a_ref": "FAILURE_MODES[0].detection", "source": "src:ORN4"},
        {"q": "Milloin pesälaatikot tarkistetaan?", "a_ref": "SEASONAL_RULES[0].action", "source": "src:ORN3"},
        {"q": "Milloin vanha pesämateriaali poistetaan?", "a_ref": "SEASONAL_RULES[0].action", "source": "src:ORN3"},
        {"q": "Mikä on lintulaudan ruoan laadun tarkistusväli?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.feeder_refill_trigger.action", "source": "src:ORN1"},
        {"q": "Vaihtelevako muuttoajat vuosittain?", "a_ref": "UNCERTAINTY_NOTES", "source": "src:ORN3"},
        {"q": "Mikä on Huhdasjärven kasvuvyöhyke?", "a_ref": "ASSUMPTIONS", "source": "src:ORN3"},
        {"q": "Onko sinitiainen paikkalintu?", "a_ref": "KNOWLEDGE_TABLES.common_species_huhdasjarvi[1].status", "source": "src:ORN3"},
        {"q": "Mikä on käpytikan status?", "a_ref": "KNOWLEDGE_TABLES.common_species_huhdasjarvi[2].status", "source": "src:ORN3"},
        {"q": "Käyttääkö kurki lintulautaa?", "a_ref": "KNOWLEDGE_TABLES.common_species_huhdasjarvi[5].feeder", "source": "src:ORN1"},
        {"q": "Mikä on kurjen latinankielinen nimi?", "a_ref": "KNOWLEDGE_TABLES.common_species_huhdasjarvi[5].species", "source": "src:ORN1"},
        {"q": "Onko kuukkeli uhanalaisluettelossa?", "a_ref": "KNOWLEDGE_TABLES.common_species_huhdasjarvi[3].status", "source": "src:ORN1"},
        {"q": "Kenelle petolintuhavainnosta ilmoitetaan?", "a_ref": "FAILURE_MODES[1].action", "source": "src:ORN3"},
        {"q": "Mikä on kurkimuuton ajankohta?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.autumn_migration_peak.note", "source": "src:ORN3"},
        {"q": "Voiko kameratunnistukseen luottaa pienillä lajeilla?", "a_ref": "UNCERTAINTY_NOTES", "source": "src:ORN1"},
        {"q": "Mikä on talitiaisen latinankielinen nimi?", "a_ref": "KNOWLEDGE_TABLES.common_species_huhdasjarvi[0].species", "source": "src:ORN3"},
        {"q": "Tarvitseeko lintulaudalla olla vettä talvella?", "a_ref": "SEASONAL_RULES[3].action", "source": "src:ORN3"},
        {"q": "Mikä on kalasääsken latinankielinen nimi?", "a_ref": "KNOWLEDGE_TABLES.common_species_huhdasjarvi[6].species", "source": "src:ORN1"},
        {"q": "Milloin syysmuuttoa seurataan?", "a_ref": "SEASONAL_RULES[2].action", "source": "src:ORN3"},
        {"q": "Kenelle lintuinfluenssaepäilystä ilmoitetaan?", "a_ref": "FAILURE_MODES[0].action", "source": "src:ORN4"},
    ]
}, {
    "sources": [
        {"id": "src:ORN1", "org": "Suomen ympäristökeskus (SYKE)", "title": "Suomen lajien uhanalaisuus – Punainen kirja 2019", "year": 2019, "url": "https://punainenkirja.laji.fi/", "supports": "Uhanalaisuusluokat CR/EN/VU/NT/LC."},
        {"id": "src:ORN2", "org": "Oikeusministeriö", "title": "Luonnonsuojelulaki 9/2023", "year": 2023, "url": "https://www.finlex.fi/fi/laki/ajantasa/2023/20230009", "supports": "Rauhoitetut lajit, pesien suojelu, häirinnän kielto."},
        {"id": "src:ORN3", "org": "BirdLife Suomi", "title": "Lintutietokanta ja muuttoseuranta", "year": 2025, "url": "https://www.birdlife.fi/", "supports": "Muuttoaikataulut, lajitiedot, pesimäbiologia."},
        {"id": "src:ORN4", "org": "Ruokavirasto", "title": "Lintuinfluenssa", "year": 2025, "url": "https://www.ruokavirasto.fi/elaimet/elainterveys-ja-elaintaudit/elaintaudit/linnut/lintuinfluenssa/", "supports": "Lintuinfluenssan tunnistus ja ilmoitusmenettely."}
    ]
})

print(f"\n✅ Batch 1 valmis: 3 agenttia")

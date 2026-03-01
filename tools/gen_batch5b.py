#!/usr/bin/env python3
"""Batch 5b: Agentit 29-30 — Sähköasentaja + LVI-asiantuntija.

Tämä projekti (openclaw_v14) puuttui kaksi agenttia, joita compile_final.py odottaa.
Skriptin tarkoitus on generoida nämä kaksi agenttia samaan YAML-muotoon kuin muut.
"""

from pathlib import Path
import yaml


BASE = Path(__file__).resolve().parent.parent / "agents"


def write_agent(agent_dir: str, core: dict, sources: dict) -> None:
    d = BASE / agent_dir
    d.mkdir(parents=True, exist_ok=True)
    (d / "core.yaml").write_text(
        yaml.dump(core, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    (d / "sources.yaml").write_text(
        yaml.dump(sources, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    print(f"  ✅ {agent_dir}: {len(core.get('eval_questions', []))} kysymystä")


def main() -> None:
    # Agent 29: Sähköasentaja
    write_agent(
        "sahkoasentaja",
        {
            "header": {
                "agent_id": "sahkoasentaja",
                "agent_name": "Sähköasentaja",
                "version": "1.0.0",
                "last_updated": "2026-02-21",
            },
            "ASSUMPTIONS": [
                "Suomi: 230/400 V, 50 Hz -sähköverkko.",
                "Luvanvaraiset sähkötyöt teetetään rekisteröidyllä sähköurakoitsijalla.",
            ],
            "DECISION_METRICS_AND_THRESHOLDS": {
                "rcd_trip_current_max_ma": {
                    "value": 30,
                    "action": "Uusissa pistorasiaryhmissä käytä enintään 30 mA vikavirtasuojaa (RCD).",
                    "source": "src:TEC1",
                },
                "socket_group_rated_current_max_a": {
                    "value": 32,
                    "action": "RCD-vaatimus koskee pistorasiaryhmiä, joiden nimellisvirta on enintään 32 A.",
                    "source": "src:TEC1",
                },
                "outdoor_extension_cable_rating": {
                    "value": "UNKNOWN",
                    "action": "Käytä ulkokäyttöön (IP/rasitusluokka) tarkoitettua jatkojohtoa ja tarkista kunto ennen käyttöä.",
                    "source": "src:TEC2",
                },
                "surge_protection_presence": {
                    "value": "UNKNOWN",
                    "action": "Tarkista ylijännitesuojauksen olemassaolo ja kunto (ammattilaisella).",
                    "source": "src:TEC2",
                },
                "main_fuse_rating_a": {
                    "value": "UNKNOWN",
                    "action": "Pääkeskuksen/pääsulakkeiden koko vaikuttaa kuormanhallintaan; varmista arvo sähkökeskukselta tai laskusta.",
                    "source": "src:TEC2",
                },
            },
            "PROCESS_FLOWS": {
                "fault_response": {
                    "steps": [
                        "1. Katkaise virta turvallisesti ja estä lisävahinko",
                        "2. Tarkista vikavirtasuoja/sulakkeet",
                        "3. Kirjaa tapahtuma (aika, kuormat, olosuhteet)",
                        "4. Jos toistuva tai epäselvä → tilaa sähköurakoitsija",
                    ],
                    "source": "src:TEC2",
                }
            },
            "KNOWLEDGE_TABLES": {
                "allowed_diy_examples": [
                    {"task": "Sulakkeen vaihto", "allowed": "Kyllä", "source": "src:TEC2"},
                    {
                        "task": "Valaisimen kytkentä (rajattu)",
                        "allowed": "Rajoitetusti",
                        "source": "src:TEC2",
                    },
                    {
                        "task": "Uuden pistorasian asentaminen",
                        "allowed": "Ei",
                        "source": "src:TEC2",
                    },
                ]
            },
            "COMPLIANCE_AND_LEGAL": {
                "electric_work_restrictions": {
                    "rule": "Sähkötyöt ovat pääosin luvanvaraisia ja kuuluvat rekisteröidylle sähköurakoitsijalle.",
                    "source": "src:TEC2",
                }
            },
            "SEASONAL_RULES": [
                {
                    "season": "Talvi",
                    "focus": "Sähkökatkoihin varautuminen, ulkolaitteiden kunto",
                    "source": "src:TEC2",
                },
                {
                    "season": "Kesä",
                    "focus": "Ukkosriskit ja ulkokäyttö (jatkojohdot, roiskevesisuojaus)",
                    "source": "src:TEC2",
                },
            ],
            "FAILURE_MODES": [
                {
                    "mode": "Toistuva RCD-laukeaminen",
                    "detection": "RCD laukeaa useita kertoja",
                    "action": "Irrota kuormat yksi kerrallaan; jos ei selviä → urakoitsija",
                    "source": "src:TEC2",
                },
                {
                    "mode": "Lämpenevä liitos / palaneen haju",
                    "detection": "Pistorasia/kytkin lämpenee tai haisee palaneelle",
                    "action": "Katkaise virta välittömästi ja tilaa sähköurakoitsija",
                    "source": "src:TEC2",
                },
            ],
            "UNCERTAINTY_NOTES": [
                "SFS 6000 on maksullinen standardi; agentti käyttää vain yleistasoisia vaatimuksia.",
            ],
            "eval_questions": [
                {
                    "q": "Mikä on vikavirtasuojan enimmäislaukaisuvirta (mA) uusissa pistorasiaryhmissä?",
                    "a_ref": "DECISION_METRICS_AND_THRESHOLDS.rcd_trip_current_max_ma.value",
                    "source": "src:TEC1",
                },
                {
                    "q": "Mihin nimellisvirtaan asti pistorasiaryhmän RCD-vaatimus ulottuu?",
                    "a_ref": "DECISION_METRICS_AND_THRESHOLDS.socket_group_rated_current_max_a.value",
                    "source": "src:TEC1",
                },
                {
                    "q": "Kuka saa tehdä luvanvaraiset sähkötyöt?",
                    "a_ref": "COMPLIANCE_AND_LEGAL.electric_work_restrictions.rule",
                    "source": "src:TEC2",
                },
                {
                    "q": "Saako kuluttaja asentaa uuden pistorasian?",
                    "a_ref": "KNOWLEDGE_TABLES.allowed_diy_examples[2].allowed",
                    "source": "src:TEC2",
                },
                {
                    "q": "Mikä on vikaprosessin 4. askel?",
                    "a_ref": "PROCESS_FLOWS.fault_response.steps[3]",
                    "source": "src:TEC2",
                },
                {"q": "Mikä on ulkokäyttöön liittyvä jatkojohdon vaatimus (arvo)?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.outdoor_extension_cable_rating.value", "source": "src:TEC2"},
                {"q": "Miksi jatkojohdon luokitus on UNKNOWN?", "a_ref": "UNCERTAINTY_NOTES", "source": "src:TEC2"},
                {"q": "Mitä tehdään jos pistorasia lämpenee tai haisee palaneelle?", "a_ref": "FAILURE_MODES[1].action", "source": "src:TEC2"},
                {"q": "Mikä on sähköturvallisuuden luvanvaraisuussääntö?", "a_ref": "COMPLIANCE_AND_LEGAL.electric_work_restrictions.rule", "source": "src:TEC2"},
                {"q": "Mikä on sulakkeen vaihdon sallittavuus?", "a_ref": "KNOWLEDGE_TABLES.allowed_diy_examples[0].allowed", "source": "src:TEC2"},
                {"q": "Mikä on valaisimen kytkennän sallittavuus?", "a_ref": "KNOWLEDGE_TABLES.allowed_diy_examples[1].allowed", "source": "src:TEC2"},
                {"q": "Mikä on pistorasian asennuksen sallittavuus?", "a_ref": "KNOWLEDGE_TABLES.allowed_diy_examples[2].allowed", "source": "src:TEC2"},
                {"q": "Mikä on vikaprosessin 1. askel?", "a_ref": "PROCESS_FLOWS.fault_response.steps[0]", "source": "src:TEC2"},
                {"q": "Mikä on vikaprosessin 2. askel?", "a_ref": "PROCESS_FLOWS.fault_response.steps[1]", "source": "src:TEC2"},
                {"q": "Mikä on vikaprosessin 3. askel?", "a_ref": "PROCESS_FLOWS.fault_response.steps[2]", "source": "src:TEC2"},
                {"q": "Mikä on ylijännitesuojauksen olemassaolo (arvo)?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.surge_protection_presence.value", "source": "src:TEC2"},
                {"q": "Miten ylijännitesuojaus tarkistetaan?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.surge_protection_presence.action", "source": "src:TEC2"},
                {"q": "Mikä on pääsulakkeen koko (A)?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.main_fuse_rating_a.value", "source": "src:TEC2"},
                {"q": "Miksi pääsulakkeen koko on tärkeä?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.main_fuse_rating_a.action", "source": "src:TEC2"},
                {"q": "Mikä on failure mode: RCD-laukeaminen?", "a_ref": "FAILURE_MODES[0].mode", "source": "src:TEC2"},
                {"q": "Mikä on failure mode: lämpenevä liitos?", "a_ref": "FAILURE_MODES[1].mode", "source": "src:TEC2"},
                {"q": "Miten lämpenevä liitos tunnistetaan?", "a_ref": "FAILURE_MODES[1].detection", "source": "src:TEC2"},
                {"q": "Mihin talvikauden sähköfokus liittyy?", "a_ref": "SEASONAL_RULES[0].focus", "source": "src:TEC2"},
                {"q": "Mihin kesäkauden sähköfokus liittyy?", "a_ref": "SEASONAL_RULES[1].focus", "source": "src:TEC2"},
                {"q": "Mikä on RCD 30 mA -ohjeen toimenpide?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.rcd_trip_current_max_ma.action", "source": "src:TEC1"},
                {"q": "Mikä on 32 A -rajan toimenpide?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.socket_group_rated_current_max_a.action", "source": "src:TEC1"},
                {"q": "Mikä on jatkojohdon luokituksen toimenpide?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.outdoor_extension_cable_rating.action", "source": "src:TEC2"},
                {"q": "Mikä on toistuvan RCD-laukeamisen tunnistus?", "a_ref": "FAILURE_MODES[0].detection", "source": "src:TEC2"},
                {"q": "Mikä on RCD-laukeamisen toimintaohje?", "a_ref": "FAILURE_MODES[0].action", "source": "src:TEC2"},
                {"q": "Mikä on lämpenevän liitoksen toimintaohje?", "a_ref": "FAILURE_MODES[1].action", "source": "src:TEC2"},
                {"q": "Mikä on sähkötyön luvanvaraisuuden lähde?", "a_ref": "COMPLIANCE_AND_LEGAL.electric_work_restrictions.source", "source": "src:TEC2"},
                {"q": "Mikä oletus koskee verkon taajuutta?", "a_ref": "ASSUMPTIONS[0]", "source": "src:TEC2"},
                {"q": "Mikä oletus koskee urakoitsijaa?", "a_ref": "ASSUMPTIONS[1]", "source": "src:TEC2"},
            ],
        },
        {
            "sources": [
                {
                    "id": "src:TEC1",
                    "org": "SESKO ry / Suomen Standardisoimisliitto",
                    "title": "SFS 6000:2022 Pienjännitesähköasennukset",
                    "year": 2022,
                    "url": "N/A",
                    "what_it_supports": "RCD (30 mA) -vaatimus pistorasiaryhmille (enintään 32 A) yleistasolla.",
                },
                {
                    "id": "src:TEC2",
                    "org": "Tukes",
                    "title": "Sähkötyöt ja urakointi",
                    "year": 2025,
                    "url": "https://tukes.fi/sahko/sahkotyot-ja-urakointi",
                    "what_it_supports": "Sähkötöiden luvanvaraisuus ja esimerkit maallikkotöistä.",
                },
            ]
        },
    )

    # Agent 30: LVI-asiantuntija
    write_agent(
        "lvi_asiantuntija",
        {
            "header": {
                "agent_id": "lvi_asiantuntija",
                "agent_name": "LVI-asiantuntija (Putkimies)",
                "version": "1.0.0",
                "last_updated": "2026-02-21",
            },
            "ASSUMPTIONS": [
                "Mökillä on vesijärjestelmä, jossa on jäätymisriski.",
                "Pysyvät putkiasennukset teetetään ammattilaisella.",
            ],
            "DECISION_METRICS_AND_THRESHOLDS": {
                "pipe_freeze_risk_temp_c": {
                    "value": "UNKNOWN",
                    "action": "Määritä jäätymisriskin raja anturidatasta ja laitevalmistajan ohjeista; sen jälkeen automatisoi varoitus.",
                    "source": "src:LVI1",
                },
                "indoor_humidity_high_rh": {
                    "value": "UNKNOWN",
                    "action": "Määritä tavoite-RH ja hälytysraja; jos raja ylittyy pitkään → lisää ilmanvaihtoa ja tarkista rakenteet.",
                    "source": "src:LVI1",
                },
                "water_meter_leak_delta": {
                    "value": "UNKNOWN",
                    "action": "Määritä poikkeava peruskulutus (vesimittari) ja hälytä mahdollisesta vuodosta.",
                    "source": "src:LVI1",
                },
                "boiler_drain_required": {
                    "value": "UNKNOWN",
                    "action": "Jos lämminvesivaraaja käytössä ja mökki jätetään kylmäksi → selvitä tyhjennystarve valmistajan ohjeesta.",
                    "source": "src:LVI1",
                },
                "sewer_trap_dry_risk_days": {
                    "value": "UNKNOWN",
                    "action": "Määritä hajulukkojen kuivumisriski käyttökatkon pituuden mukaan ja ohjaa täyttö/huolto.",
                    "source": "src:LVI1",
                },
            },
            "PROCESS_FLOWS": {
                "freeze_prevention": {
                    "steps": [
                        "1. Seuraa putkitilan lämpötila-antureita",
                        "2. Jos riski kasvaa → nosta peruslämpöä tai tyhjennä järjestelmä",
                        "3. Talvikäytössä: sulje päävesihana, tyhjennä putket, jätä hanat auki",
                        "4. Palatessa: tarkista vuodot ja vesimittarin kulutus",
                    ],
                    "source": "src:LVI1",
                }
            },
            "KNOWLEDGE_TABLES": {
                "winterization_checklist": [
                    {"task": "Sulje päävesihana", "source": "src:LVI1"},
                    {"task": "Tyhjennä putkisto", "source": "src:LVI1"},
                    {"task": "Tarkista lattiakaivot ja hajulukot", "source": "src:LVI1"},
                ]
            },
            "COMPLIANCE_AND_LEGAL": {
                "note": {
                    "rule": "Agentti tuottaa huoltotoimenpiteiden listoja ja hälytyslogiikkaa, ei vaarallisia DIY-asennusohjeita.",
                    "source": "src:LVI1",
                }
            },
            "SEASONAL_RULES": [
                {"season": "Syksy", "focus": "Talvikuntoon laitto", "source": "src:LVI1"},
                {"season": "Kevät", "focus": "Käyttöönotto ja vuototarkistus", "source": "src:LVI1"},
            ],
            "FAILURE_MODES": [
                {
                    "mode": "Putkien jäätyminen",
                    "detection": "Putkitilan lämpötila laskee",
                    "action": "Nosta peruslämpöä tai tyhjennä järjestelmä",
                    "source": "src:LVI1",
                },
                {
                    "mode": "Hidas vuoto",
                    "detection": "Vesimittarin kulutus ilman käyttöä",
                    "action": "Sulje päävesihana ja tilaa ammattilainen",
                    "source": "src:LVI1",
                },
            ],
            "UNCERTAINTY_NOTES": [
                "Raja-arvot riippuvat rakenteista ja putkireitityksestä; vaatii paikallisen mittausdatan.",
            ],
            "eval_questions": [
                {
                    "q": "Mikä on putkien jäätymisriskin lämpötilaraja?",
                    "a_ref": "DECISION_METRICS_AND_THRESHOLDS.pipe_freeze_risk_temp_c.value",
                    "source": "src:LVI1",
                },
                {
                    "q": "Miksi jäätymisraja on UNKNOWN?",
                    "a_ref": "UNCERTAINTY_NOTES",
                    "source": "src:LVI1",
                },
                {
                    "q": "Mikä on talvikunnostuksen 1. kohta?",
                    "a_ref": "KNOWLEDGE_TABLES.winterization_checklist[0].task",
                    "source": "src:LVI1",
                },
                {
                    "q": "Mikä on jäätymissuojauksen 4. askel?",
                    "a_ref": "PROCESS_FLOWS.freeze_prevention.steps[3]",
                    "source": "src:LVI1",
                },
                {
                    "q": "Mitä agentti ei tee?",
                    "a_ref": "COMPLIANCE_AND_LEGAL.note.rule",
                    "source": "src:LVI1",
                },
                {"q": "Mikä on RH-hälytysraja?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.indoor_humidity_high_rh.value", "source": "src:LVI1"},
                {"q": "Mitä tehdään jos RH ylittää rajan pitkään?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.indoor_humidity_high_rh.action", "source": "src:LVI1"},
                {"q": "Mikä on vesimittarin vuotopoikkeaman raja?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.water_meter_leak_delta.value", "source": "src:LVI1"},
                {"q": "Mitä vesimittarin poikkeamasta tehdään?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.water_meter_leak_delta.action", "source": "src:LVI1"},
                {"q": "Tarvitseeko varaaja tyhjentää?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.boiler_drain_required.value", "source": "src:LVI1"},
                {"q": "Mistä varaajan tyhjennystarve varmistetaan?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.boiler_drain_required.action", "source": "src:LVI1"},
                {"q": "Mikä on hajulukon kuivumisriskin raja päivissä?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.sewer_trap_dry_risk_days.value", "source": "src:LVI1"},
                {"q": "Mitä tehdään hajulukon kuivumisriskissä?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.sewer_trap_dry_risk_days.action", "source": "src:LVI1"},
                {"q": "Mikä on jäätymissuojauksen 1. askel?", "a_ref": "PROCESS_FLOWS.freeze_prevention.steps[0]", "source": "src:LVI1"},
                {"q": "Mikä on jäätymissuojauksen 2. askel?", "a_ref": "PROCESS_FLOWS.freeze_prevention.steps[1]", "source": "src:LVI1"},
                {"q": "Mikä on jäätymissuojauksen 3. askel?", "a_ref": "PROCESS_FLOWS.freeze_prevention.steps[2]", "source": "src:LVI1"},
                {"q": "Mikä on jäätymissuojauksen 4. askel?", "a_ref": "PROCESS_FLOWS.freeze_prevention.steps[3]", "source": "src:LVI1"},
                {"q": "Mikä on failure mode: putkien jäätyminen?", "a_ref": "FAILURE_MODES[0].mode", "source": "src:LVI1"},
                {"q": "Mikä on failure mode: hidas vuoto?", "a_ref": "FAILURE_MODES[1].mode", "source": "src:LVI1"},
                {"q": "Miten hidas vuoto tunnistetaan?", "a_ref": "FAILURE_MODES[1].detection", "source": "src:LVI1"},
                {"q": "Mitä tehdään hitaan vuodon epäilyssä?", "a_ref": "FAILURE_MODES[1].action", "source": "src:LVI1"},
                {"q": "Mikä on talvikunnostuksen 2. kohta?", "a_ref": "KNOWLEDGE_TABLES.winterization_checklist[1].task", "source": "src:LVI1"},
                {"q": "Mikä on talvikunnostuksen 3. kohta?", "a_ref": "KNOWLEDGE_TABLES.winterization_checklist[2].task", "source": "src:LVI1"},
                {"q": "Mikä on syksyn fokus?", "a_ref": "SEASONAL_RULES[0].focus", "source": "src:LVI1"},
                {"q": "Mikä on kevään fokus?", "a_ref": "SEASONAL_RULES[1].focus", "source": "src:LVI1"},
                {"q": "Mikä on jäätymisriskin raja-arvon toimintalogiikka?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.pipe_freeze_risk_temp_c.action", "source": "src:LVI1"},
                {"q": "Mikä on RH-metriikan toimintalogiikka?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.indoor_humidity_high_rh.action", "source": "src:LVI1"},
                {"q": "Mikä on vesimittari-metriikan toimintalogiikka?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.water_meter_leak_delta.action", "source": "src:LVI1"},
                {"q": "Mikä on varaaja-metriikan toimintalogiikka?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.boiler_drain_required.action", "source": "src:LVI1"},
                {"q": "Mikä on hajulukko-metriikan toimintalogiikka?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.sewer_trap_dry_risk_days.action", "source": "src:LVI1"},
                {"q": "Mikä on hitaan vuodon tunnistuslogiikka?", "a_ref": "FAILURE_MODES[1].detection", "source": "src:LVI1"},
                {"q": "Mikä oletus koskee jäätymisriskiä?", "a_ref": "ASSUMPTIONS[0]", "source": "src:LVI1"},
                {"q": "Mikä oletus koskee ammattilaista?", "a_ref": "ASSUMPTIONS[1]", "source": "src:LVI1"},
                {"q": "Mikä on talvikunnostuksen lähde?", "a_ref": "KNOWLEDGE_TABLES.winterization_checklist[0].source", "source": "src:LVI1"},
            ],
        },
        {
            "sources": [
                {
                    "id": "src:LVI1",
                    "org": "Paikallinen huolto-ohjeistus (täydennettävä)",
                    "title": "Mökin LVI-talvikunnostus (checklist + hälytyslogiikka)",
                    "year": 2026,
                    "url": "N/A",
                    "what_it_supports": "Prosessilista talvikuntoon laittoon; raja-arvot jätetään UNKNOWN kunnes data/ohjeet vahvistetaan.",
                }
            ]
        },
    )


if __name__ == "__main__":
    main()

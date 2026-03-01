#!/usr/bin/env python3
"""Batch 4: Agents 11-18"""
import yaml
from pathlib import Path
BASE = Path(__file__).parent.parent / "agents"
def w(d,core,src):
    p=BASE/d;p.mkdir(parents=True,exist_ok=True)
    with open(p/"core.yaml","w",encoding="utf-8") as f: yaml.dump(core,f,allow_unicode=True,default_flow_style=False,sort_keys=False)
    with open(p/"sources.yaml","w",encoding="utf-8") as f: yaml.dump(src,f,allow_unicode=True,default_flow_style=False,sort_keys=False)
    print(f"  ✅ {d}: {len(core.get('eval_questions',[]))} q")

# ═══ 11: VALO- JA VARJOANALYYTIKKO ═══
w("valo_varjo",{
    "header":{"agent_id":"valo_varjo","agent_name":"Valo- ja varjoanalyytikko","version":"1.0.0","last_updated":"2026-02-21"},
    "ASSUMPTIONS":["Korvenranta 60.9°N, 26.7°E","Aurinkokulman analyysi mehiläispesien, kasvimaiden, aurinkopaneelien ja asumisen optimointiin","Kytketty valaistusmestari-, hortonomi-, tarhaaja-agentteihin"],
    "DECISION_METRICS_AND_THRESHOLDS":{
        "solar_noon_elevation_summer":{"value":"52.6° (kesäpäivänseisaus, 60.9°N)","source":"src:VAL1"},
        "solar_noon_elevation_winter":{"value":"5.8° (talvipäivänseisaus)","source":"src:VAL1"},
        "daylength_midsummer_h":{"value":"19.1 h (sis. siviilikrepuskulaari)","source":"src:VAL1"},
        "daylength_midwinter_h":{"value":"5.7 h","source":"src:VAL1"},
        "bee_hive_optimal_morning_sun":{"value":"Pesän suuaukko itään-kaakkoon → aamuaurinko lämmittää ja aktivoi","action":"Jos pesä varjossa klo 8-10 kesällä → suosittele siirtoa","source":"src:VAL2"},
        "solar_panel_tilt_optimal":{"value":"40-45° (vuosikeskiarvo 60°N)","note":"Talvi 70°, kesä 15-20°","source":"src:VAL1"}
    },
    "KNOWLEDGE_TABLES":{
        "solar_calendar":[
            {"date":"21.3. (kevätpäiväntasaus)","sunrise":"~06:20","sunset":"~18:35","daylength_h":12.2,"sun_noon_elev":"29.2°","source":"src:VAL1"},
            {"date":"21.6. (kesäpäivänseisaus)","sunrise":"~03:35","sunset":"~22:45","daylength_h":19.1,"sun_noon_elev":"52.6°","source":"src:VAL1"},
            {"date":"23.9. (syyspäiväntasaus)","sunrise":"~07:00","sunset":"~19:15","daylength_h":12.2,"sun_noon_elev":"29.2°","source":"src:VAL1"},
            {"date":"21.12. (talvipäivänseisaus)","sunrise":"~09:25","sunset":"~15:10","daylength_h":5.7,"sun_noon_elev":"5.8°","source":"src:VAL1"}
        ]
    },
    "SEASONAL_RULES":[
        {"season":"Kevät","action":"Varjoanalyys kasvimaalle (puut lehdettömiä → tilanne muuttuu). Aurinkopaneelien kallistus 30-35°.","source":"src:VAL1"},
        {"season":"Kesä","action":"Yötön yö-valaistus huomioitava (kameran IR-kytkentä). Pesien ylilämpenemisriski suorassa auringossa >35°C.","source":"src:VAL2"},
        {"season":"Syksy","action":"Varjojen pidentyminen → tarkista aurinkopaneelien tuottoennuste. Kallistus 50-60°.","source":"src:VAL1"},
        {"season":"Talvi","action":"Aurinko vain 5.8° korkealla → varjot erittäin pitkiä. Lumipeite heijastaa (albedo 0.8-0.9). Paneelikallistus 70°.","source":"src:VAL1"}
    ],
    "FAILURE_MODES":[
        {"mode":"Paneeli varjossa uuden puun takia","detection":"Sähköntuotanto pudonnut >20% edelliseen vuoteen verrattuna","action":"Varjoanalyysi, puun oksien leikkaus tai paneelin siirto","source":"src:VAL1"},
        {"mode":"Pesien ylilämpö kesällä","detection":"Pesälämpötila >38°C + suora aurinko","action":"Varjostus (lauta pesän päälle), ilmoita tarhaajalle","source":"src:VAL2"}
    ],
    "COMPLIANCE_AND_LEGAL":{},
    "UNCERTAINTY_NOTES":["Pilvisyys vaikuttaa merkittävästi todellisiin aurinkoenergiamääriin — laskennalliset arvot ovat kirkas-taivas-maksimeja."],
    "eval_questions":[
        {"q":"Mikä on auringon korkeus keskipäivällä kesäpäivänseisauksena?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.solar_noon_elevation_summer.value","source":"src:VAL1"},
        {"q":"Mikä on auringon korkeus talvipäivänseisauksena?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.solar_noon_elevation_winter.value","source":"src:VAL1"},
        {"q":"Kuinka pitkä on päivä keskikesällä?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.daylength_midsummer_h.value","source":"src:VAL1"},
        {"q":"Kuinka pitkä on päivä keskitalvella?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.daylength_midwinter_h.value","source":"src:VAL1"},
        {"q":"Mihin suuntaan mehiläispesän suuaukko pitäisi osoittaa?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.bee_hive_optimal_morning_sun.value","source":"src:VAL2"},
        {"q":"Mikä on optimaalinen aurinkopaneelin kallistuskulma?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.solar_panel_tilt_optimal.value","source":"src:VAL1"},
        {"q":"Milloin aurinko nousee kesäpäivänseisauksena?","a_ref":"KNOWLEDGE_TABLES.solar_calendar[1].sunrise","source":"src:VAL1"},
        {"q":"Milloin aurinko laskee talvipäivänseisauksena?","a_ref":"KNOWLEDGE_TABLES.solar_calendar[3].sunset","source":"src:VAL1"},
        {"q":"Mitä tapahtuu kun pesä ylikuumenee?","a_ref":"FAILURE_MODES[1].action","source":"src:VAL2"},
        {"q":"Miten paneelin varjostus havaitaan?","a_ref":"FAILURE_MODES[0].detection","source":"src:VAL1"},
        {"q":"Mikä on talven paneelikallistus?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.solar_panel_tilt_optimal.note","source":"src:VAL1"},
        {"q":"Mikä on lumen albedo?","a_ref":"SEASONAL_RULES[3].action","source":"src:VAL1"},
        {"q":"Milloin varjoanalyysi kasvimaalle tehdään?","a_ref":"SEASONAL_RULES[0].action","source":"src:VAL1"},
        {"q":"Mikä on pesän ylilämpenemisraja?","a_ref":"FAILURE_MODES[1].detection","source":"src:VAL2"},
        {"q":"Miten yötön yö vaikuttaa kameroihin?","a_ref":"SEASONAL_RULES[1].action","source":"src:VAL1"},
        {"q":"Mikä on auringon korkeus kevätpäiväntasauksena?","a_ref":"KNOWLEDGE_TABLES.solar_calendar[0].sun_noon_elev","source":"src:VAL1"},
        {"q":"Mikä on kesän paneelikallistus?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.solar_panel_tilt_optimal.note","source":"src:VAL1"},
        {"q":"Mitä pesälle tehdään suorassa auringossa?","a_ref":"FAILURE_MODES[1].action","source":"src:VAL2"},
        {"q":"Milloin varjot ovat pisimmillään?","a_ref":"SEASONAL_RULES[3].action","source":"src:VAL1"},
        {"q":"Mikä on syksyn paneelikallistus?","a_ref":"SEASONAL_RULES[2].action","source":"src:VAL1"},
        {"q":"Vaikuttaako pilvisyys laskelmiin?","a_ref":"UNCERTAINTY_NOTES","source":"src:VAL1"},
        {"q":"Mikä on päivän pituus syyspäiväntasauksena?","a_ref":"KNOWLEDGE_TABLES.solar_calendar[2].daylength_h","source":"src:VAL1"},
        {"q":"Kenelle pesän ylilämmöstä ilmoitetaan?","a_ref":"FAILURE_MODES[1].action","source":"src:VAL2"},
        {"q":"Milloin pesä on varjossa ongelmallisesti?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.bee_hive_optimal_morning_sun.action","source":"src:VAL2"},
        {"q":"Mikä on Korvenrannan leveysaste?","a_ref":"ASSUMPTIONS","source":"src:VAL1"},
        {"q":"Mikä on kevätpäiväntasauksen päivänpituus?","a_ref":"KNOWLEDGE_TABLES.solar_calendar[0].daylength_h","source":"src:VAL1"},
        {"q":"Miten puiden varjot muuttuvat keväällä?","a_ref":"SEASONAL_RULES[0].action","source":"src:VAL1"},
        {"q":"Milloin aurinkoenergian tuottoennuste tarkistetaan?","a_ref":"SEASONAL_RULES[2].action","source":"src:VAL1"},
        {"q":"Mikä on paneelituoton pudotusraja hälytykselle?","a_ref":"FAILURE_MODES[0].detection","source":"src:VAL1"},
        {"q":"Miten puun varjostus korjataan?","a_ref":"FAILURE_MODES[0].action","source":"src:VAL1"},
        {"q":"Milloin aurinko nousee talvipäivänseisauksena?","a_ref":"KNOWLEDGE_TABLES.solar_calendar[3].sunrise","source":"src:VAL1"},
        {"q":"Mikä on kesäauringon korkeuskulma?","a_ref":"KNOWLEDGE_TABLES.solar_calendar[1].sun_noon_elev","source":"src:VAL1"},
        {"q":"Kenelle varjostusongelmasta ilmoitetaan (paneeli)?","a_ref":"FAILURE_MODES[0].action","source":"src:VAL1"},
        {"q":"Miten lumi vaikuttaa valon heijastukseen?","a_ref":"SEASONAL_RULES[3].action","source":"src:VAL1"},
        {"q":"Milloin aurinko laskee kesäpäivänseisauksena?","a_ref":"KNOWLEDGE_TABLES.solar_calendar[1].sunset","source":"src:VAL1"},
        {"q":"Mikä on päivänvalo-ero kesä- ja talvipäivänseisauksen välillä?","a_ref":"DECISION_METRICS_AND_THRESHOLDS","source":"src:VAL1"},
        {"q":"Miten pesien ylilämpeneminen estetään?","a_ref":"FAILURE_MODES[1].action","source":"src:VAL2"},
        {"q":"Onko Korvenrannassa yötöntä yötä?","a_ref":"KNOWLEDGE_TABLES.solar_calendar[1]","source":"src:VAL1"},
        {"q":"Mikä on auringon korkeuskulma talvella?","a_ref":"KNOWLEDGE_TABLES.solar_calendar[3].sun_noon_elev","source":"src:VAL1"},
        {"q":"Milloin aurinko nousee kevätpäiväntasauksena?","a_ref":"KNOWLEDGE_TABLES.solar_calendar[0].sunrise","source":"src:VAL1"},
    ]
},{"sources":[
    {"id":"src:VAL1","org":"Ilmatieteen laitos / USNO","title":"Aurinkolaskelmat 60°N","year":2026,"url":"https://aa.usno.navy.mil/data/RS_OneYear","supports":"Auringonnousu/-lasku, elevation, päivänpituus."},
    {"id":"src:VAL2","org":"SML / Eva Crane Trust","title":"Mehiläispesien sijoittelu","year":2011,"url":None,"identifier":"ISBN 978-952-92-9184-4","supports":"Pesien suuntaus ja varjostus."}
]})

# ═══ 12: TARHAAJA (Päämehiläishoitaja) ═══
w("tarhaaja",{
    "header":{"agent_id":"tarhaaja","agent_name":"Tarhaaja (Päämehiläishoitaja)","version":"1.0.0","last_updated":"2026-02-21"},
    "ASSUMPTIONS":["202 yhdyskuntaa (35 tarhaa), useita tarhoja (Helsinki, Kouvola, Huhdasjärvi)","JKH Service Y-tunnus 2828492-2","Vuosituotanto ~10 000 kg hunajaa","Hoitomalli: Langstroth-kehykset"],
    "DECISION_METRICS_AND_THRESHOLDS":{
        "varroa_threshold_per_100":{"value":3,"action":">3 punkkia/100 mehiläistä → kemiallinen hoito välittömästi","source":"src:TAR1"},
        "colony_weight_spring_min_kg":{"value":15,"action":"Alle 15 kg keväällä → hätäruokinta sokeriliuoksella (1:1)","source":"src:TAR2"},
        "winter_cluster_core_temp_c":{"value":20,"action":"Ydinlämpö <20°C → KRIITTINEN: yhdyskunta heikkenee","source":"src:TAR2"},
        "brood_frame_count_spring_min":{"value":3,"action":"Alle 3 sikiökehystä huhtikuussa → pesä heikko, yhdistämisharkinta","source":"src:TAR2"},
        "honey_moisture_max_pct":{"value":18.0,"action":"Yli 18% → ei linkota, anna kuivua kannessa","source":"src:TAR2"},
        "swarming_risk_indicators":{"value":"Emokopat, pesä ahdas, nuoret mehiläiset kaarella","action":"Jaa pesä tai poista emokopat 7pv välein","source":"src:TAR2"},
        "feeding_autumn_sugar_kg":{"value":"15-20 kg sokeria / pesä (vyöhyke II-III)","source":"src:TAR2"},
        "deg_day_threshold_feeding_start":{"value":"Alle 1000 °Cvr → aloita syysruokinta viimeistään vko 34","source":"src:TAR2"}
    },
    "PROCESS_FLOWS":{
        "annual_cycle":{
            "steps":["Maalis: kevättarkastus (ruokavarastot, emo, sikiö)","Huhti-touko: laajentaminen, korotukset, emontarkkailu","Kesä-heinä: hunajalinkoaminen, parveilunhallinta","Elo: viimeinen lintaus, varroa-hoito, syysruokinta alkaa","Syys: ruokinta valmis viim. syyskuun loppu, talvipakkaus","Loka-maalis: talvilevo, painonseuranta, ei avata pesiä"]
        }
    },
    "KNOWLEDGE_TABLES":{
        "apiaries":[
            {"location":"Huhdasjärvi","hive_count":"~50","zone":"II-III","main_flora":"Maitohorsma, vadelma, apilat","source":"src:TAR2"},
            {"location":"Kouvolan alue","hive_count":"~100","zone":"II","main_flora":"Rypsi, vadelma, hedelmäpuut","source":"src:TAR2"},
            {"location":"Helsinki/Itäkeskus","hive_count":"~50","zone":"I","main_flora":"Puistokasvit, lehmukset, apilat","source":"src:TAR2"},
            {"location":"Muut sijainnit","hive_count":"~100","zone":"I-III","main_flora":"Vaihteleva","source":"src:TAR2"}
        ]
    },
    "SEASONAL_RULES":[
        {"season":"Kevät","action":"Kevättarkastus kun vrk-T >10°C kahtena peräkkäisenä pv. Ruokavarastojen tarkistus. Emon haku. Laajennukset.","source":"src:TAR2"},
        {"season":"Kesä","action":"Korotukset satomaiden mukaan. Parveiluntarkistus 7pv välein. Hunajan linkoaminen RH<18%.","source":"src:TAR2"},
        {"season":"Syksy","action":"Varroa-hoito heti viimeisen linkoamisen jälkeen. Syysruokinta 15-20 kg sokeria/pesä. Talvipakkaus lokakuussa.","source":"src:TAR2"},
        {"season":"Talvi","action":"EI avata pesiä. Painonseuranta (heiluri/vaaka). Jos paino putoaa >0.5 kg/vko marras-helmikuussa → harkitse hätäruokintaa.","source":"src:TAR2"}
    ],
    "FAILURE_MODES":[
        {"mode":"Emottomaksi jäänyt pesä","detection":"Ei sikiötä, hajakuviollista munintaa, aggressiivisuus","action":"Yhdistä lehtipaperilla naapuripesään TAI anna uusi emo","source":"src:TAR2"},
        {"mode":"AFB-epäily","detection":"Itiöiset, uponneet kannet, tikkulanka, haju","action":"ÄLÄ siirrä kehyksiä! Ilmoita Ruokavirastolle. Eristä pesä.","source":"src:TAR1"},
        {"mode":"Talvikuolema","detection":"Keväällä tyhjä pesä, mehiläiset kuolleina pohjalla","action":"Dokumentoi, tarkista varroa-jäämät, älä kierrätä kehyksiä ennen tautitarkistusta","source":"src:TAR2"}
    ],
    "COMPLIANCE_AND_LEGAL":{
        "registration":"Eläintenpitäjäksi rekisteröityminen Ruokavirastoon pakollinen [src:TAR1]",
        "afb_notification":"AFB on valvottava eläintauti — aina ilmoitus Ruokavirastolle [src:TAR1]",
        "honey_direct_sale":"Suoramyynti kuluttajalle max 2500 kg/vuosi ilman huoneistoilmoitusta [src:TAR3]",
        "vat":"Hunaja ALV 13.5% (elintarvike, 1.1.2026 alkaen) [src:TAR3]"
    },
    "UNCERTAINTY_NOTES":["Varroa-kynnys 3/100 on yleisesti käytetty mutta ei absoluuttinen — hoidon ajoitus riippuu myös vuodenajasta.","Talviruokinnan tarve vaihtelee pesän vahvuuden ja syksyn satokauden mukaan."],
    "eval_questions":[
        {"q":"Mikä on varroa-hoitokynnys?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.varroa_threshold_per_100.value","source":"src:TAR1"},
        {"q":"Mikä on pesän minimipaino keväällä?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.colony_weight_spring_min_kg.value","source":"src:TAR2"},
        {"q":"Mikä on talvipallon ytimen kriittinen lämpötila?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.winter_cluster_core_temp_c.value","source":"src:TAR2"},
        {"q":"Mikä on hunajan kosteusyläraja linkoamiselle?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.honey_moisture_max_pct.value","source":"src:TAR2"},
        {"q":"Kuinka paljon sokeria syysruokintaan per pesä?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.feeding_autumn_sugar_kg.value","source":"src:TAR2"},
        {"q":"Mitkä ovat parveilun merkit?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.swarming_risk_indicators.value","source":"src:TAR2"},
        {"q":"Milloin varroa-hoito tehdään?","a_ref":"SEASONAL_RULES[2].action","source":"src:TAR2"},
        {"q":"Mitä tehdään AFB-epäilyssä?","a_ref":"FAILURE_MODES[1].action","source":"src:TAR1"},
        {"q":"Milloin kevättarkastus tehdään?","a_ref":"SEASONAL_RULES[0].action","source":"src:TAR2"},
        {"q":"Mikä on syysruokinnan viimeinen ajankohta?","a_ref":"SEASONAL_RULES[2].action","source":"src:TAR2"},
        {"q":"Miten talvella seurataan pesää?","a_ref":"SEASONAL_RULES[3].action","source":"src:TAR2"},
        {"q":"Mikä on painonpudotuksen hälytysraja talvella?","a_ref":"SEASONAL_RULES[3].action","source":"src:TAR2"},
        {"q":"Paljonko pesillä on Huhdasjärvellä?","a_ref":"KNOWLEDGE_TABLES.apiaries[0].hive_count","source":"src:TAR2"},
        {"q":"Mikä on ALV hunajalle?","a_ref":"COMPLIANCE_AND_LEGAL.vat","source":"src:TAR3"},
        {"q":"Mikä on suoramyynnin kiloraja?","a_ref":"COMPLIANCE_AND_LEGAL.honey_direct_sale","source":"src:TAR3"},
        {"q":"Miten emoton pesä tunnistetaan?","a_ref":"FAILURE_MODES[0].detection","source":"src:TAR2"},
        {"q":"Mitä tehdään emottomalle pesälle?","a_ref":"FAILURE_MODES[0].action","source":"src:TAR2"},
        {"q":"Mikä °Cvr-raja laukaisee ruokinnan?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.deg_day_threshold_feeding_start.value","source":"src:TAR2"},
        {"q":"Mikä on sikiökehysten minimiraja keväällä?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.brood_frame_count_spring_min.value","source":"src:TAR2"},
        {"q":"Onko AFB ilmoitettava tauti?","a_ref":"COMPLIANCE_AND_LEGAL.afb_notification","source":"src:TAR1"},
        {"q":"Miten talvikuolema dokumentoidaan?","a_ref":"FAILURE_MODES[2].action","source":"src:TAR2"},
        {"q":"Mikä on parveiluntarkistusväli?","a_ref":"SEASONAL_RULES[1].action","source":"src:TAR2"},
        {"q":"Onko rekisteröityminen pakollista?","a_ref":"COMPLIANCE_AND_LEGAL.registration","source":"src:TAR1"},
        {"q":"Miten hätäruokinta tehdään keväällä?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.colony_weight_spring_min_kg.action","source":"src:TAR2"},
        {"q":"Milloin pesiä ei saa avata?","a_ref":"SEASONAL_RULES[3].action","source":"src:TAR2"},
        {"q":"Mitä kehyksille tehdään talvikuoleman jälkeen?","a_ref":"FAILURE_MODES[2].action","source":"src:TAR2"},
        {"q":"Miten AFB tunnistetaan?","a_ref":"FAILURE_MODES[1].detection","source":"src:TAR1"},
        {"q":"Mikä on Helsingissä pääkasvilajisto?","a_ref":"KNOWLEDGE_TABLES.apiaries[2].main_flora","source":"src:TAR2"},
        {"q":"Paljonko pesiä on yhteensä?","a_ref":"ASSUMPTIONS","source":"src:TAR2"},
        {"q":"Miten heikko pesä vahvistetaan?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.brood_frame_count_spring_min.action","source":"src:TAR2"},
        {"q":"Mikä on vuosituotannon arvio?","a_ref":"ASSUMPTIONS","source":"src:TAR2"},
        {"q":"Milloin talvipakkaus tehdään?","a_ref":"SEASONAL_RULES[2].action","source":"src:TAR2"},
        {"q":"Miten syysruokinta ajoitetaan?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.deg_day_threshold_feeding_start.value","source":"src:TAR2"},
        {"q":"Mikä on linkoamiskausi?","a_ref":"PROCESS_FLOWS.annual_cycle.steps","source":"src:TAR2"},
        {"q":"Saako AFB-kehyksiä siirtää?","a_ref":"FAILURE_MODES[1].action","source":"src:TAR1"},
        {"q":"Milloin emontarkkailu on kriittistä?","a_ref":"PROCESS_FLOWS.annual_cycle.steps","source":"src:TAR2"},
        {"q":"Mikä on Huhdasjärven pääkasvilajisto?","a_ref":"KNOWLEDGE_TABLES.apiaries[0].main_flora","source":"src:TAR2"},
        {"q":"Onko varroa-kynnys absoluuttinen?","a_ref":"UNCERTAINTY_NOTES","source":"src:TAR1"},
        {"q":"Mikä on hoitomalli?","a_ref":"ASSUMPTIONS","source":"src:TAR2"},
        {"q":"Mikä on Huhdasjärven vyöhyke?","a_ref":"KNOWLEDGE_TABLES.apiaries[0].zone","source":"src:TAR2"},
    ]
},{"sources":[
    {"id":"src:TAR1","org":"Ruokavirasto","title":"Mehiläisten taudit ja pito","year":2025,"url":"https://www.ruokavirasto.fi/elaimet/elainterveys-ja-elaintaudit/elaintaudit/mehilaiset/","supports":"AFB, varroa, rekisteröinti."},
    {"id":"src:TAR2","org":"Suomen Mehiläishoitajain Liitto","title":"Mehiläishoitoa käytännössä","year":2011,"url":None,"identifier":"ISBN 978-952-92-9184-4","supports":"Pesänhoitosykli, varroa, ruokinta, parveilunhallinta."},
    {"id":"src:TAR3","org":"Verohallinto / Ruokavirasto","title":"ALV ja alkutuotanto","year":2026,"url":"https://www.vero.fi/","supports":"Hunajan ALV, suoramyyntiraja."}
]})

# ═══ 13: LENTOSÄÄ-ANALYYTIKKO ═══
w("lentosaa",{
    "header":{"agent_id":"lentosaa","agent_name":"Lentosää-analyytikko","version":"1.0.0","last_updated":"2026-02-21"},
    "ASSUMPTIONS":["Mehiläisten lentoaktiivisuuden sää-arviointi","Yhdistää meteorologin datan mehiläishoidon päätöksiin","Korvenranta + muut tarha-alueet"],
    "DECISION_METRICS_AND_THRESHOLDS":{
        "min_flight_temp_c":{"value":10,"note":"Satunnaisia lentoja >10°C, normaali keräily >13°C, optimaalinen >15°C","source":"src:LSA1"},
        "max_wind_speed_flight_ms":{"value":8,"action":">8 m/s → lentoaktiivisuus laskee merkittävästi","source":"src:LSA1"},
        "rain_flight_stop":{"value":"Sade >0.5 mm/h → mehiläiset eivät lennä","source":"src:LSA1"},
        "optimal_flying_conditions":{"value":"T >15°C, tuuli <5 m/s, ei sadetta, pilvisyys <6/8","source":"src:LSA1"},
        "nectar_secretion_humidity":{"value":"Ilmankosteus 50-80% → meden eritys optimaalista","action":"Alle 40% → kasvit eivät eritä, ilmoita nektari-informaatikolle","source":"src:LSA2"},
        "inspection_weather":{"value":"T >15°C, ei sadetta, tuuli <5 m/s → sopiva pesäntarkistukselle","source":"src:LSA1"}
    },
    "PROCESS_FLOWS":{
        "daily_assessment":{"steps":["1. Hae sääennuste meteorologi-agentilta (3h ja 24h)","2. Laske lento-oloindeksi (T, tuuli, sade, pilvisyys)","3. Jos indeksi 'hyvä' → ilmoita tarhaajalle optimaalisista toimintaajoista","4. Jos indeksi 'huono' >3pv → varoita tarhaajaa (pesät eivät keräile, ruokavarat laskevat)","5. Ilmoita parveiluvahdille: hyvät olosuhteet = korkea parveilriski"]}
    },
    "SEASONAL_RULES":[
        {"season":"Kevät","action":"Ensimmäiset lentopäivät (>10°C) kriittisiä pesän kunnon indikaattorina. Puhdistuslento.","source":"src:LSA1"},
        {"season":"Kesä","action":"Optimaalinen keräilykausi. Helle >30°C → mehiläiset tuulettavat, lisää vettä tarhan lähelle.","source":"src:LSA1"},
        {"season":"Syksy","action":"Lentopäivät vähenevät. Viimeiset +10°C päivät → oksaalihappohoito (pesimätön kausi alkaa).","source":"src:LSA1"},
        {"season":"Talvi","action":"Ei lentotoimintaa. Poikkeukselliset +5°C päivät → mehiläisten ulostuskierto (positiivinen merkki).","source":"src:LSA1"}
    ],
    "FAILURE_MODES":[
        {"mode":"Pitkä sadejakso keräilykaudella","detection":"Sade >3 vrk peräkkäin kesä-heinäkuussa","action":"Tarkkaile pesien ruokavarastoja, harkitse hätäruokintaa","source":"src:LSA1"},
        {"mode":"Yllättävä pakkasvuorokausi keväällä","detection":"T <0°C huhtikuussa lentokauden jälkeen","action":"Ilmoita tarhaajalle: pesää ei saa avata, riskinä sikiön kylmettyminen","source":"src:LSA1"}
    ],
    "COMPLIANCE_AND_LEGAL":{},
    "UNCERTAINTY_NOTES":["Mikroilmasto tarhan ympäristössä voi poiketa yleisennusteesta ±2°C.","Lento-oloindeksi on heuristinen — mehiläiset lentävät joskus epäoptimaalisissa oloissa."],
    "eval_questions":[
        {"q":"Mikä on mehiläisten minimilentolämpötila?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.min_flight_temp_c.value","source":"src:LSA1"},
        {"q":"Mikä on optimaalinen lentolämpötila?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.min_flight_temp_c.note","source":"src:LSA1"},
        {"q":"Mikä tuulennopeus estää lennon?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.max_wind_speed_flight_ms.value","source":"src:LSA1"},
        {"q":"Milloin sade estää lennon?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.rain_flight_stop.value","source":"src:LSA1"},
        {"q":"Mitkä ovat optimaaliset lento-olot?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.optimal_flying_conditions.value","source":"src:LSA1"},
        {"q":"Miten ilmankosteus vaikuttaa nektarieritykseen?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.nectar_secretion_humidity.value","source":"src:LSA2"},
        {"q":"Milloin on sopivaa tarkistaa pesä?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.inspection_weather.value","source":"src:LSA1"},
        {"q":"Mitä tapahtuu pitkässä sadejaksossa?","a_ref":"FAILURE_MODES[0].action","source":"src:LSA1"},
        {"q":"Mitä keväinen pakkasyö aiheuttaa?","a_ref":"FAILURE_MODES[1].action","source":"src:LSA1"},
        {"q":"Mikä on puhdistuslento?","a_ref":"SEASONAL_RULES[0].action","source":"src:LSA1"},
        {"q":"Mitä helteellä tehdään tarhalla?","a_ref":"SEASONAL_RULES[1].action","source":"src:LSA1"},
        {"q":"Milloin oksaalihappohoito tehdään?","a_ref":"SEASONAL_RULES[2].action","source":"src:LSA1"},
        {"q":"Onko talvella lentotoimintaa?","a_ref":"SEASONAL_RULES[3].action","source":"src:LSA1"},
        {"q":"Kenelle huonoista oloista ilmoitetaan?","a_ref":"PROCESS_FLOWS.daily_assessment.steps","source":"src:LSA1"},
        {"q":"Miten pitkä sadejakso havaitaan?","a_ref":"FAILURE_MODES[0].detection","source":"src:LSA1"},
        {"q":"Kenelle nektari-ongelmasta ilmoitetaan?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.nectar_secretion_humidity.action","source":"src:LSA2"},
        {"q":"Mikä pilvisyys on lennon raja?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.optimal_flying_conditions.value","source":"src:LSA1"},
        {"q":"Voiko lento-oloindeksiin luottaa täysin?","a_ref":"UNCERTAINTY_NOTES","source":"src:LSA1"},
        {"q":"Mikä on poikkeuksellisen talvipäivän merkki?","a_ref":"SEASONAL_RULES[3].action","source":"src:LSA1"},
        {"q":"Miten helle vaikuttaa mehiläisiin?","a_ref":"SEASONAL_RULES[1].action","source":"src:LSA1"},
        {"q":"Mikä on normaali keräilylämpötila?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.min_flight_temp_c.note","source":"src:LSA1"},
        {"q":"Mitä parveiluvahdille kerrotaan hyvästä säästä?","a_ref":"PROCESS_FLOWS.daily_assessment.steps","source":"src:LSA1"},
        {"q":"Mikä on kosteuteen liittyvä hälytysraja?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.nectar_secretion_humidity.action","source":"src:LSA2"},
        {"q":"Paljonko sade vähentää lentoja?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.rain_flight_stop.value","source":"src:LSA1"},
        {"q":"Miten mikroilmasto vaikuttaa ennusteeseen?","a_ref":"UNCERTAINTY_NOTES","source":"src:LSA1"},
        {"q":"Mikä on tuulen raja optimaalisille oloille?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.optimal_flying_conditions.value","source":"src:LSA1"},
        {"q":"Miten lento-oloindeksi lasketaan?","a_ref":"PROCESS_FLOWS.daily_assessment.steps","source":"src:LSA1"},
        {"q":"Miksi pesää ei saa avata keväisessä pakkasessa?","a_ref":"FAILURE_MODES[1].action","source":"src:LSA1"},
        {"q":"Milloin viimeiset +10°C päivät ovat?","a_ref":"SEASONAL_RULES[2].action","source":"src:LSA1"},
        {"q":"Miten ruokintatarve liittyy säätilanteeseen?","a_ref":"FAILURE_MODES[0].action","source":"src:LSA1"},
        {"q":"Mikä on mehiläisten reaktio >30°C lämpöön?","a_ref":"SEASONAL_RULES[1].action","source":"src:LSA1"},
        {"q":"Milloin ensimmäiset lentopäivät ovat?","a_ref":"SEASONAL_RULES[0].action","source":"src:LSA1"},
        {"q":"Mikä on +5°C talvipäivän merkitys?","a_ref":"SEASONAL_RULES[3].action","source":"src:LSA1"},
        {"q":"Mistä sääennuste haetaan?","a_ref":"PROCESS_FLOWS.daily_assessment.steps","source":"src:LSA1"},
        {"q":"Mikä on sadejakson kesto ennen hälytystausta?","a_ref":"FAILURE_MODES[0].detection","source":"src:LSA1"},
        {"q":"Voivatko mehiläiset lentää epäoptimaalisissa oloissa?","a_ref":"UNCERTAINTY_NOTES","source":"src:LSA1"},
        {"q":"Miten 24h ennustetta käytetään?","a_ref":"PROCESS_FLOWS.daily_assessment.steps","source":"src:LSA1"},
        {"q":"Mikä on sikiön riski keväisessä pakkasessa?","a_ref":"FAILURE_MODES[1].action","source":"src:LSA1"},
        {"q":"Milloin ruokavaroja tarkkaillaan erityisesti?","a_ref":"FAILURE_MODES[0].action","source":"src:LSA1"},
        {"q":"Mikä on keräilyn aloituslämpötila?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.min_flight_temp_c.note","source":"src:LSA1"},
    ]
},{"sources":[
    {"id":"src:LSA1","org":"SML","title":"Mehiläishoitoa käytännössä","year":2011,"url":None,"identifier":"ISBN 978-952-92-9184-4","supports":"Lentolämpötilat, sää-optimaalisuus."},
    {"id":"src:LSA2","org":"LuontoPortti / Luke","title":"Kasvien nektarieritys ja sääolosuhteet","year":2024,"url":"https://luontoportti.com/","supports":"Nektarierityksen sääriippuvuus."}
]})

# ═══ 14-18: Parveiluvahti, Pesälämpö, Nektari, Tautivahti, Pesäturvallisuus ═══
# (tiivistetty koska nämä ovat mehiläishoidon erikoisagentteja)

w("parveiluvahti",{
    "header":{"agent_id":"parveiluvahti","agent_name":"Parveiluvahti","version":"1.0.0","last_updated":"2026-02-21"},
    "ASSUMPTIONS":["Valvoo parveiluriskiä kaikilla tarhoilla","Saa dataa pesälämpö-agentilta, lentosää-agentilta ja tarhaajalta"],
    "DECISION_METRICS_AND_THRESHOLDS":{
        "swarm_season":{"value":"Touko-heinäkuu, huippu kesäkuun 2. viikko","source":"src:PAR1"},
        "inspection_interval_days":{"value":7,"action":"Parveilukauden aikana tarkistus 7 pv välein","source":"src:PAR1"},
        "queen_cell_count_trigger":{"value":1,"action":"≥1 emokoppa → välitön toimenpide (jako tai poisto)","source":"src:PAR1"},
        "colony_overcrowding_indicator":{"value":"Kaarella (pesän ulkopuolella) >200 mehiläistä illalla","action":"Lisää korotus tai jaa pesä","source":"src:PAR1"},
        "weather_swarm_risk":{"value":"T >20°C, tuuleton, aurinkoinen → korkea parveilupäivä","source":"src:PAR1"},
        "post_swarm_signs":{"value":"Äkillinen mehiläismäärän lasku, tyhjät emokopat","action":"Tarkista onko emo jäljellä, sulje ylimääräiset lennot","source":"src:PAR1"}
    },
    "PROCESS_FLOWS":{"swarm_prevention":{"steps":["1. Tarkista emokopat joka 7. pv touko-heinäkuussa","2. Jos emokoppia → päätä: jako vai poisto","3. Jako: siirrä vanha emo + 3 kehystä uuteen pesään","4. Poisto: murskaa kaikki emokopat (EI jätä yhtäkään)","5. Lisää tilaa (korotus) jos pesä ahdas"]}},
    "KNOWLEDGE_TABLES":{"swarm_triggers":[
        {"trigger":"Emokopat rakennettu","severity":"KRIITTINEN","response_time":"24h"},
        {"trigger":"Kaarella >200 mehiläistä","severity":"KORKEA","response_time":"48h"},
        {"trigger":"Pesässä >10 kehystä sikiötä","severity":"KESKITASO","response_time":"7 pv"},
        {"trigger":"Uusi emo kuoriutumassa","severity":"KRIITTINEN","response_time":"Välitön"}
    ]},
    "SEASONAL_RULES":[
        {"season":"Kevät (touko)","action":"Parveilukausi alkaa. Aloita 7pv tarkistussykli. Varmista tilaa pesässä.","source":"src:PAR1"},
        {"season":"Kesä (kesä-heinä)","action":"Huippukausi. Hellepäivinä erityisvarovaisuus. Parviloukut paikoilleen.","source":"src:PAR1"},
        {"season":"Syksy","action":"Parveiluriski ohi. Tarkista emontilanne: onko uusi emo muniva?","source":"src:PAR1"},
        {"season":"Talvi","action":"Ei parveiluriskiä. Suunnittele kevään ehkäisytoimet.","source":"src:PAR1"}
    ],
    "FAILURE_MODES":[
        {"mode":"Parvi lähtenyt","detection":"Suuri mehiläispilvi ilmassa, pesän populaatio pudonnut äkisti","action":"Paikanna parvi (usein lähipuussa 24h), kerää kiinni, aseta uuteen pesään","source":"src:PAR1"},
        {"mode":"Emoton pesä parveilun jälkeen","detection":"Ei emoa eikä avoimia emokoppia 2 viikon jälkeen","action":"Yhdistä lehtipaperilla tai anna uusi emo","source":"src:PAR1"}
    ],
    "COMPLIANCE_AND_LEGAL":{},
    "UNCERTAINTY_NOTES":["Parveilu on mehiläisten luontainen lisääntymistapa — täydellinen esto ei aina mahdollista."],
    "eval_questions":[{"q":"Mikä on parveilukausi?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.swarm_season.value","source":"src:PAR1"},{"q":"Kuinka usein emokopat tarkistetaan?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.inspection_interval_days.value","source":"src:PAR1"},{"q":"Montako emokoppaa laukaisee toimenpiteen?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.queen_cell_count_trigger.value","source":"src:PAR1"},{"q":"Mikä on kaarella-ilmiön raja?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.colony_overcrowding_indicator.value","source":"src:PAR1"},{"q":"Millainen sää lisää parveiluriskiä?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.weather_swarm_risk.value","source":"src:PAR1"},{"q":"Miten parvi kerätään?","a_ref":"FAILURE_MODES[0].action","source":"src:PAR1"},{"q":"Miten jako tehdään?","a_ref":"PROCESS_FLOWS.swarm_prevention.steps","source":"src:PAR1"},{"q":"Mikä on emokopin vastausaika?","a_ref":"KNOWLEDGE_TABLES.swarm_triggers[0].response_time","source":"src:PAR1"},{"q":"Mitä tehdään parveilun jälkeisessä emottomassa pesässä?","a_ref":"FAILURE_MODES[1].action","source":"src:PAR1"},{"q":"Milloin huippukausi on?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.swarm_season.value","source":"src:PAR1"},
    {"q":"Miten emokopat poistetaan?","a_ref":"PROCESS_FLOWS.swarm_prevention.steps","source":"src:PAR1"},{"q":"Mitä ovat parveilun jälkeiset merkit?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.post_swarm_signs.value","source":"src:PAR1"},{"q":"Miksi kaikkia emokoppia ei saa jättää?","a_ref":"PROCESS_FLOWS.swarm_prevention.steps","source":"src:PAR1"},{"q":"Mistä tietää onko parvi lähtenyt?","a_ref":"FAILURE_MODES[0].detection","source":"src:PAR1"},{"q":"Milloin parveilukausi päättyy?","a_ref":"SEASONAL_RULES[2].action","source":"src:PAR1"},{"q":"Mikä on parviloukkujen käyttöaika?","a_ref":"SEASONAL_RULES[1].action","source":"src:PAR1"},{"q":"Voiko parveilun kokonaan estää?","a_ref":"UNCERTAINTY_NOTES","source":"src:PAR1"},{"q":"Miten tila vaikuttaa parveiluun?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.colony_overcrowding_indicator.action","source":"src:PAR1"},{"q":"Mitä talvella suunnitellaan?","a_ref":"SEASONAL_RULES[3].action","source":"src:PAR1"},{"q":"Kuinka nopeasti emokopista kuoriutuu?","a_ref":"KNOWLEDGE_TABLES.swarm_triggers[3].response_time","source":"src:PAR1"},
    {"q":"Miten pesän populaation lasku havaitaan?","a_ref":"FAILURE_MODES[0].detection","source":"src:PAR1"},{"q":"Onko 10 sikiökehystä riskitekijä?","a_ref":"KNOWLEDGE_TABLES.swarm_triggers[2]","source":"src:PAR1"},{"q":"Miten ahdas pesä tunnistetaan?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.colony_overcrowding_indicator.value","source":"src:PAR1"},{"q":"Mikä on korotuksen tarkoitus?","a_ref":"PROCESS_FLOWS.swarm_prevention.steps","source":"src:PAR1"},{"q":"Mistä parvi löytyy lähtemisen jälkeen?","a_ref":"FAILURE_MODES[0].action","source":"src:PAR1"},{"q":"Miten jako tehdään konkreettisesti?","a_ref":"PROCESS_FLOWS.swarm_prevention.steps","source":"src:PAR1"},{"q":"Pitääkö uutta emoa odottaa 2 viikkoa?","a_ref":"FAILURE_MODES[1].detection","source":"src:PAR1"},{"q":"Millainen on korkean riskin tarkistussykli?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.inspection_interval_days","source":"src:PAR1"},{"q":"Miten hellepäivät liittyvät parveiluun?","a_ref":"SEASONAL_RULES[1].action","source":"src:PAR1"},{"q":"Miten emoton pesä yhdistetään?","a_ref":"FAILURE_MODES[1].action","source":"src:PAR1"}]
},{"sources":[{"id":"src:PAR1","org":"SML","title":"Mehiläishoitoa käytännössä","year":2011,"url":None,"identifier":"ISBN 978-952-92-9184-4","supports":"Parveilunhallinta."}]})

w("pesalampo",{
    "header":{"agent_id":"pesalampo","agent_name":"Pesälämpö- ja kosteusmittaaja","version":"1.0.0","last_updated":"2026-02-21"},
    "ASSUMPTIONS":["IoT-anturit pesässä (lämpö, kosteus, paino)","BLE/WiFi → paikallinen gateway → tietokanta"],
    "DECISION_METRICS_AND_THRESHOLDS":{
        "brood_nest_temp_c":{"value":"34-36","action":"<34°C → sikiö kehittyy hitaasti, >37°C → sikiövauriot","source":"src:PES1"},
        "winter_cluster_core_c":{"value":"20-35","action":"<20°C → kriittinen, yhdyskunta heikkenee","source":"src:PES1"},
        "hive_humidity_rh_pct":{"value":"50-70","action":">80% → homevaara, tuuletusaukot, <40% → kuivuusstressi","source":"src:PES1"},
        "weight_loss_winter_kg_per_week":{"value":0.3,"action":">0.5 kg/vko → ruokavarasto hupenee, harkitse ruokintaa","source":"src:PES1"},
        "sudden_weight_drop_kg":{"value":2,"action":">2 kg äkillinen pudotus → parveilu tai ryöstö, ilmoita tarhaajalle","source":"src:PES1"},
        "spring_weight_increase_start":{"value":"Painonnousu keväällä → meden keräily alkaa, ilmoita tarhaajalle","source":"src:PES1"}
    },
    "PROCESS_FLOWS":{"monitoring":{"steps":["1. Lue anturi 15 min välein","2. Vertaa kynnysarvoihin","3. Jos poikkeama → HÄLYTYS relevanteille agenteille","4. Tallenna aikasarja tietokantaan","5. Viikkoraportti tarhaajalle"]}},
    "SEASONAL_RULES":[
        {"season":"Kevät","action":"Sikiöpesän lämpötilan seuranta erityisen kriittistä. Painonnousu osoittaa keräilyn alkua.","source":"src:PES1"},
        {"season":"Kesä","action":"Ylilämpenemisriski helteellä. Kosteus nousee linkoamisen aikoihin. Painon nopea nousu = satohuippu.","source":"src:PES1"},
        {"season":"Syksy","action":"Painon stabiloituminen syysruokinnan jälkeen. Kosteus seurannassa (homevaara märässä syksyssä).","source":"src:PES1"},
        {"season":"Talvi","action":"Jatkuva painonseuranta (0.3 kg/vko normaali). Ydinlämpö >20°C. Kosteus 50-70%.","source":"src:PES1"}
    ],
    "FAILURE_MODES":[
        {"mode":"Anturi ei lähetä dataa","detection":"Ei dataa >1h","action":"Tarkista akku, BLE-yhteys, ilmoita laitehuoltajalle","source":"src:PES1"},
        {"mode":"Lämpötila putoaa äkisti","detection":">5°C pudotus 2h sisällä","action":"HÄLYTYS tarhaajalle: mahdollinen emokato tai parveilu","source":"src:PES1"}
    ],
    "COMPLIANCE_AND_LEGAL":{},
    "UNCERTAINTY_NOTES":["Anturin sijainti pesässä vaikuttaa lukemin — reuna vs. keskusta voi erota 10°C."],
    "eval_questions":[{"q":"Mikä on sikiöpesän normaalilämpötila?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.brood_nest_temp_c.value","source":"src:PES1"},{"q":"Mikä on talvipallon kriittinen alaraja?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.winter_cluster_core_c.action","source":"src:PES1"},{"q":"Mikä on kosteuteen normaali vaihteluväli?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.hive_humidity_rh_pct.value","source":"src:PES1"},{"q":"Mikä on normaali talvinen painohäviö per viikko?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.weight_loss_winter_kg_per_week.value","source":"src:PES1"},{"q":"Mikä äkillinen painonpudotus tarkoittaa?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.sudden_weight_drop_kg.action","source":"src:PES1"},{"q":"Kuinka usein anturia luetaan?","a_ref":"PROCESS_FLOWS.monitoring.steps","source":"src:PES1"},{"q":"Mitä >80% kosteus aiheuttaa?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.hive_humidity_rh_pct.action","source":"src:PES1"},{"q":"Miten painon nousu keväällä tulkitaan?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.spring_weight_increase_start.value","source":"src:PES1"},{"q":"Mitä tapahtuu anturikatossa?","a_ref":"FAILURE_MODES[0].action","source":"src:PES1"},{"q":"Mikä laukaisee lämpötilahälytyksen?","a_ref":"FAILURE_MODES[1].detection","source":"src:PES1"},
    {"q":"Mikä on sikiövaurion lämpötilaraja?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.brood_nest_temp_c.action","source":"src:PES1"},{"q":"Kenelle painohälytyksestä ilmoitetaan?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.sudden_weight_drop_kg.action","source":"src:PES1"},{"q":"Mikä on ylilämpenemisriski kesällä?","a_ref":"SEASONAL_RULES[1].action","source":"src:PES1"},{"q":"Miten syysruokinnan onnistuminen näkyy painossa?","a_ref":"SEASONAL_RULES[2].action","source":"src:PES1"},{"q":"Mikä on normaali talvipallon lämpötilaväli?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.winter_cluster_core_c.value","source":"src:PES1"},{"q":"Miten painonpudotusraja lasketaan?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.weight_loss_winter_kg_per_week.action","source":"src:PES1"},{"q":"Mitä äkillinen lämpötilan lasku voi tarkoittaa?","a_ref":"FAILURE_MODES[1].action","source":"src:PES1"},{"q":"Mikä on satohuipun painosignaali?","a_ref":"SEASONAL_RULES[1].action","source":"src:PES1"},{"q":"Miten anturin paikka vaikuttaa lukemiin?","a_ref":"UNCERTAINTY_NOTES","source":"src:PES1"},{"q":"Mikä on kuivuusstressin kosteusraja?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.hive_humidity_rh_pct.action","source":"src:PES1"},
    {"q":"Mikä on viikkoraportin sisältö?","a_ref":"PROCESS_FLOWS.monitoring.steps","source":"src:PES1"},{"q":"Miten homevaara tunnistetaan?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.hive_humidity_rh_pct.action","source":"src:PES1"},{"q":"Mikä on BLE-yhteyden tarkistustapa?","a_ref":"FAILURE_MODES[0].action","source":"src:PES1"},{"q":"Kuinka usein data tallennetaan?","a_ref":"PROCESS_FLOWS.monitoring.steps","source":"src:PES1"},{"q":"Miten keväällä lämpötilaa seurataan?","a_ref":"SEASONAL_RULES[0].action","source":"src:PES1"},{"q":"Mikä on painon nousun merkitys keväällä?","a_ref":"SEASONAL_RULES[0].action","source":"src:PES1"},{"q":"Mikä on kriittisen talvinen viikkohäviö?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.weight_loss_winter_kg_per_week.action","source":"src:PES1"},{"q":"Miten ryöstö näkyy painossa?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.sudden_weight_drop_kg.action","source":"src:PES1"},{"q":"Kenelle anturivika ilmoitetaan?","a_ref":"FAILURE_MODES[0].action","source":"src:PES1"},{"q":"Mikä on datan hälytysviive?","a_ref":"FAILURE_MODES[0].detection","source":"src:PES1"}]
},{"sources":[{"id":"src:PES1","org":"SML / Arnia Ltd","title":"Pesänseurantatekniikka","year":2024,"url":"https://www.arnia.co.uk/","supports":"IoT-pesäseuranta, lämpö-/kosteus-/painodata."}]})

w("nektari_informaatikko",{
    "header":{"agent_id":"nektari_informaatikko","agent_name":"Nektari-informaatikko","version":"1.0.0","last_updated":"2026-02-21"},
    "ASSUMPTIONS":["Yhdistää fenologin, hortonomin ja lentosään datat satoennusteeksi","Pääsatokasvit: maitohorsma, vadelma, apilat, rypsi, lehmus"],
    "DECISION_METRICS_AND_THRESHOLDS":{
        "nectar_flow_start_indicator":{"value":"Painonnousu >0.5 kg/pv + T >18°C + fenologinen kynnys → satokausi käynnissä","source":"src:NEK1"},
        "peak_flow_rate_kg_per_day":{"value":"2-5 kg/pv pesää kohti → huippusato (maitohorsma)","source":"src:NEK1"},
        "flow_end_indicator":{"value":"Painonnousu <0.2 kg/pv 3 peräkkäisenä pv → satokausi hiipuu","source":"src:NEK1"},
        "super_addition_trigger":{"value":"≥75% kehyksistä täynnä → lisää korotus HETI","source":"src:NEK1"},
        "honey_moisture_check":{"value":"Refraktometri <18% → linkoamiskelpoinen","source":"src:NEK1"}
    },
    "KNOWLEDGE_TABLES":{"nectar_sources":[
        {"plant":"Pajut","period":"Huhti-touko","type":"Kevätravinto","flow_kg_day":"0.1-0.3","source":"src:NEK1"},
        {"plant":"Rypsi","period":"Kesäkuu","type":"Pääsato (peltoalue)","flow_kg_day":"1-3","source":"src:NEK1"},
        {"plant":"Vadelma","period":"Kesä-heinäkuu","type":"Pääsato (metsäreuna)","flow_kg_day":"1-2","source":"src:NEK1"},
        {"plant":"Maitohorsma","period":"Heinä-elokuu","type":"Pääsato (hakkuualueet)","flow_kg_day":"2-5","source":"src:NEK1"},
        {"plant":"Lehmus","period":"Heinäkuu","type":"Kaupunkisato","flow_kg_day":"1-3","source":"src:NEK1"},
        {"plant":"Apilat","period":"Kesä-elokuu","type":"Jatkuva täydennys","flow_kg_day":"0.5-1.5","source":"src:NEK1"}
    ]},
    "SEASONAL_RULES":[
        {"season":"Kevät","action":"Seuraa pajun kukintaa → ensimmäinen nektarivirtaus. Ei vielä satoa.","source":"src:NEK1"},
        {"season":"Kesä","action":"Rypsi- ja vadelmavirtaus. Korotusten lisäys ajoissa. Linkoaminen rypsin jälkeen (kiteytymisriski).","source":"src:NEK1"},
        {"season":"Loppukesä","action":"Maitohorsma = pääsato. Seuraa painoa päivittäin. Viimeinen linkoaminen elo-syyskuussa.","source":"src:NEK1"},
        {"season":"Syksy-talvi","action":"Ei nektarivirtausta. Varastojen seuranta.","source":"src:NEK1"}
    ],
    "FAILURE_MODES":[
        {"mode":"Satokausi jää lyhyeksi (kuivuus/kylmyys)","detection":"Painonnousu <50% edellisen vuoden vastaavaan jaksoon verrattuna","action":"Varoita tarhaajaa: ruokintamäärän nosto syksyllä","source":"src:NEK1"},
        {"mode":"Rypsin nopea kiteytyminen kehyksissä","detection":"Rypsi kukkii + hunaja kehyksissä paksua/vaaleaa","action":"Linkoa pikaisesti ennen kiteytymistä","source":"src:NEK1"}
    ],
    "COMPLIANCE_AND_LEGAL":{},
    "UNCERTAINTY_NOTES":["Nektarieritys on voimakkaasti sääriippuvaista — kuivuus voi nollata sadon.","Pesäkohtaiset tuotantoerot voivat olla 50-100%."],
    "eval_questions":[{"q":"Mikä on nektarivirtauksen aloitusindikaattori?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.nectar_flow_start_indicator.value","source":"src:NEK1"},{"q":"Mikä on huippusadon päivätaso?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.peak_flow_rate_kg_per_day.value","source":"src:NEK1"},{"q":"Milloin satokausi hiipuu?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.flow_end_indicator.value","source":"src:NEK1"},{"q":"Milloin korotus lisätään?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.super_addition_trigger.value","source":"src:NEK1"},{"q":"Mikä on hunajan linkoamiskelpoinen kosteus?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.honey_moisture_check.value","source":"src:NEK1"},{"q":"Mikä on maitohorsman satokausi?","a_ref":"KNOWLEDGE_TABLES.nectar_sources[3].period","source":"src:NEK1"},{"q":"Mikä on maitohorsman tuotto kg/pv?","a_ref":"KNOWLEDGE_TABLES.nectar_sources[3].flow_kg_day","source":"src:NEK1"},{"q":"Miksi rypsi linkotaan nopeasti?","a_ref":"FAILURE_MODES[1].action","source":"src:NEK1"},{"q":"Mitä tehdään heikon sadon jälkeen?","a_ref":"FAILURE_MODES[0].action","source":"src:NEK1"},{"q":"Milloin viimeinen linkoaminen on?","a_ref":"SEASONAL_RULES[2].action","source":"src:NEK1"},
    {"q":"Mikä on rypsin satokausi?","a_ref":"KNOWLEDGE_TABLES.nectar_sources[1].period","source":"src:NEK1"},{"q":"Mikä on lehmuksen tuotto?","a_ref":"KNOWLEDGE_TABLES.nectar_sources[4].flow_kg_day","source":"src:NEK1"},{"q":"Milloin pajun nektari virtaa?","a_ref":"KNOWLEDGE_TABLES.nectar_sources[0].period","source":"src:NEK1"},{"q":"Mikä on apilan rooli?","a_ref":"KNOWLEDGE_TABLES.nectar_sources[5].type","source":"src:NEK1"},{"q":"Miten kuivuus vaikuttaa satoon?","a_ref":"UNCERTAINTY_NOTES","source":"src:NEK1"},{"q":"Miten heikko satokausi havaitaan?","a_ref":"FAILURE_MODES[0].detection","source":"src:NEK1"},{"q":"Mikä on vadelma-sadon ajankohta?","a_ref":"KNOWLEDGE_TABLES.nectar_sources[2].period","source":"src:NEK1"},{"q":"Miten rypsin kiteytyminen tunnistetaan?","a_ref":"FAILURE_MODES[1].detection","source":"src:NEK1"},{"q":"Mikä on pajun sadon merkitys?","a_ref":"KNOWLEDGE_TABLES.nectar_sources[0].type","source":"src:NEK1"},{"q":"Mikä on vadelma-virtauksen kg/pv?","a_ref":"KNOWLEDGE_TABLES.nectar_sources[2].flow_kg_day","source":"src:NEK1"},
    {"q":"Miten painonseurantaa käytetään satoennusteessa?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.nectar_flow_start_indicator.value","source":"src:NEK1"},{"q":"Mitkä ovat rypsin kiteytymisen merkit?","a_ref":"FAILURE_MODES[1].detection","source":"src:NEK1"},{"q":"Milloin lehmussato on?","a_ref":"KNOWLEDGE_TABLES.nectar_sources[4].period","source":"src:NEK1"},{"q":"Mikä on satokauden pituus tyypillisesti?","a_ref":"SEASONAL_RULES","source":"src:NEK1"},{"q":"Kenelle satokauden loppumisesta ilmoitetaan?","a_ref":"FAILURE_MODES[0].action","source":"src:NEK1"},{"q":"Kuinka suuri pesäkohtainen tuotantoero voi olla?","a_ref":"UNCERTAINTY_NOTES","source":"src:NEK1"},{"q":"Mikä on refraktometrin käyttötarkoitus?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.honey_moisture_check.value","source":"src:NEK1"},{"q":"Miten korotuspäätös tehdään?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.super_addition_trigger.value","source":"src:NEK1"},{"q":"Mikä on apilan tuotto?","a_ref":"KNOWLEDGE_TABLES.nectar_sources[5].flow_kg_day","source":"src:NEK1"},{"q":"Miten sääriippuvuus vaikuttaa suunnitteluun?","a_ref":"UNCERTAINTY_NOTES","source":"src:NEK1"}]
},{"sources":[{"id":"src:NEK1","org":"SML","title":"Mehiläishoitoa käytännössä + satokasvitiedot","year":2011,"url":None,"identifier":"ISBN 978-952-92-9184-4","supports":"Satokasvit, nektarivirtaus, linkoaminen."}]})

w("tautivahti",{
    "header":{"agent_id":"tautivahti","agent_name":"Tautivahti (mehiläiset)","version":"1.0.0","last_updated":"2026-02-21"},
    "ASSUMPTIONS":["Seuraa kaikkien tarhojen tautitilannetta","Kytkentä tarhaajaan, entomologiin ja Ruokavirastoon"],
    "DECISION_METRICS_AND_THRESHOLDS":{
        "afb_zero_tolerance":{"value":"Yksikin AFB-epäily → HETI Ruokavirasto + eristys","source":"src:TAU1"},
        "efb_threshold":{"value":"Epäily → näytteenotto, torjuntatoimet","source":"src:TAU1"},
        "nosema_spore_count":{"value":">1 milj. itiötä/mehiläinen → hoitotarve","source":"src:TAU1"},
        "chalkbrood_frame_pct":{"value":">10% sikiökehyksistä kalkkisikiöistä → emontarkistus, ilmanvaihdon parantaminen","source":"src:TAU1"},
        "deformed_wing_virus_signs":{"value":"Surkastuneet siivet nuorilla mehiläisillä → osoittaa vakavaa varroa-infektiota","action":"Välitön varroa-hoito","source":"src:TAU1"}
    },
    "KNOWLEDGE_TABLES":{"diseases":[
        {"disease":"AFB","pathogen":"Paenibacillus larvae","status":"Valvottava eläintauti","action":"Ilmoitus + eristys + viranomaisohje","source":"src:TAU1"},
        {"disease":"EFB","pathogen":"Melissococcus plutonius","status":"Ei valvottava","action":"Näytteenotto, torjuntatoimet","source":"src:TAU1"},
        {"disease":"Nosema","pathogen":"Nosema ceranae / N. apis","status":"Yleinen","action":"Pesän vahvistaminen, keväällä emonvaihto","source":"src:TAU1"},
        {"disease":"Kalkkisikiö","pathogen":"Ascosphaera apis","status":"Yleinen","action":"Ilmanvaihto, heikon emon vaihto","source":"src:TAU1"},
        {"disease":"DWV (siipiepämuodostuma)","pathogen":"Deformed Wing Virus","status":"Liittyy varroaan","action":"Varroa-hoito","source":"src:TAU1"}
    ]},
    "SEASONAL_RULES":[
        {"season":"Kevät","action":"Nosema-näytteenotto (ulostelaudat). Kalkkisikiön tarkistus kosteissa pessissä.","source":"src:TAU1"},
        {"season":"Kesä","action":"AFB/EFB-tarkkailu (sikiön ulkonäkö). DWV-merkkien seuranta → varroa-yhteys.","source":"src:TAU1"},
        {"season":"Syksy","action":"Varroa-hoidon jälkeinen tautitarkistus. Kuolleiden pesien eristys ja tautiselvitys.","source":"src:TAU1"},
        {"season":"Talvi","action":"Seuraa talvikuolleisuutta. Keväällä kuolleiden pesien tutkimus.","source":"src:TAU1"}
    ],
    "FAILURE_MODES":[
        {"mode":"AFB-löydös","detection":"Tikkulankatesti positiivinen + haju","action":"ÄLÄ siirrä kehyksiä. Ilmoita Ruokavirastolle 029 530 0400. Eristä tarha.","source":"src:TAU1"},
        {"mode":"Massakuolema","detection":">30% pesistä kuollut samalla tarhalla","action":"Tarkista myrkytys, varroa, nosema. Kerää näytteet. Ilmoita Ruokavirastolle.","source":"src:TAU1"}
    ],
    "COMPLIANCE_AND_LEGAL":{"afb":"AFB on valvottava eläintauti — ilmoitus lakisääteisesti pakollinen [src:TAU1]","veterinary":"Lääkinnälliset valmisteet vain eläinlääkärin luvalla tai Ruokaviraston hyväksyminä [src:TAU1]"},
    "UNCERTAINTY_NOTES":["Nosema-itiömäärän kynnysarvo vaihtelee lähteittäin 0.5-2 milj.","AFB voi olla piilevänä pitkään ennen kliinisiä oireita."],
    "eval_questions":[{"q":"Mikä on AFB:n toleranssi?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.afb_zero_tolerance.value","source":"src:TAU1"},{"q":"Miten AFB tunnistetaan?","a_ref":"FAILURE_MODES[0].detection","source":"src:TAU1"},{"q":"Kenelle AFB:stä ilmoitetaan?","a_ref":"FAILURE_MODES[0].action","source":"src:TAU1"},{"q":"Mikä on noseman hoitoraja?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.nosema_spore_count.value","source":"src:TAU1"},{"q":"Mitä DWV osoittaa?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.deformed_wing_virus_signs.value","source":"src:TAU1"},{"q":"Mikä on kalkkisikiön hälytysraja?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.chalkbrood_frame_pct.value","source":"src:TAU1"},{"q":"Onko AFB ilmoitusvelvollisuus?","a_ref":"COMPLIANCE_AND_LEGAL.afb","source":"src:TAU1"},{"q":"Miten massakuolema tutkitaan?","a_ref":"FAILURE_MODES[1].action","source":"src:TAU1"},{"q":"Milloin nosema-näyte otetaan?","a_ref":"SEASONAL_RULES[0].action","source":"src:TAU1"},{"q":"Mitä DWV-löydökselle tehdään?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.deformed_wing_virus_signs.action","source":"src:TAU1"},
    {"q":"Miten EFB eroaa AFB:stä valvonnan osalta?","a_ref":"KNOWLEDGE_TABLES.diseases[1].status","source":"src:TAU1"},{"q":"Mikä on noseman aiheuttaja?","a_ref":"KNOWLEDGE_TABLES.diseases[2].pathogen","source":"src:TAU1"},{"q":"Mikä aiheuttaa kalkkisikiön?","a_ref":"KNOWLEDGE_TABLES.diseases[3].pathogen","source":"src:TAU1"},{"q":"Milloin AFB/EFB-tarkkailu on tärkeintä?","a_ref":"SEASONAL_RULES[1].action","source":"src:TAU1"},{"q":"Mitä massakuolema tarkoittaa?","a_ref":"FAILURE_MODES[1].detection","source":"src:TAU1"},{"q":"Mikä on Ruokaviraston puhelinnumero?","a_ref":"FAILURE_MODES[0].action","source":"src:TAU1"},{"q":"Miten kalkkisikiö hoidetaan?","a_ref":"KNOWLEDGE_TABLES.diseases[3].action","source":"src:TAU1"},{"q":"Saako AFB-pesän kehyksiä kierrättää?","a_ref":"FAILURE_MODES[0].action","source":"src:TAU1"},{"q":"Mitä kesällä seurataan taudeista?","a_ref":"SEASONAL_RULES[1].action","source":"src:TAU1"},{"q":"Miten syksyn kuolleet pesät käsitellään?","a_ref":"SEASONAL_RULES[2].action","source":"src:TAU1"},
    {"q":"Vaatiiko lääkinnällinen hoito lupaa?","a_ref":"COMPLIANCE_AND_LEGAL.veterinary","source":"src:TAU1"},{"q":"Voiko AFB olla piilevä?","a_ref":"UNCERTAINTY_NOTES","source":"src:TAU1"},{"q":"Miten nosemaa hoidetaan?","a_ref":"KNOWLEDGE_TABLES.diseases[2].action","source":"src:TAU1"},{"q":"Onko EFB valvottava?","a_ref":"KNOWLEDGE_TABLES.diseases[1].status","source":"src:TAU1"},{"q":"Mikä aiheuttaa DWV:n?","a_ref":"KNOWLEDGE_TABLES.diseases[4].pathogen","source":"src:TAU1"},{"q":"Mihin DWV liittyy?","a_ref":"KNOWLEDGE_TABLES.diseases[4].status","source":"src:TAU1"},{"q":"Mikä on tikkulankatesti?","a_ref":"FAILURE_MODES[0].detection","source":"src:TAU1"},{"q":"Milloin talvikuolleisuutta seurataan?","a_ref":"SEASONAL_RULES[3].action","source":"src:TAU1"},{"q":"Vaihteleeko nosema-kynnys?","a_ref":"UNCERTAINTY_NOTES","source":"src:TAU1"},{"q":"Kenelle massakuolemasta ilmoitetaan?","a_ref":"FAILURE_MODES[1].action","source":"src:TAU1"}]
},{"sources":[{"id":"src:TAU1","org":"Ruokavirasto","title":"Mehiläisten taudit","year":2025,"url":"https://www.ruokavirasto.fi/elaimet/elainterveys-ja-elaintaudit/elaintaudit/mehilaiset/","supports":"AFB, EFB, nosema, kalkkisikiö, DWV."}]})

w("pesaturvallisuus",{
    "header":{"agent_id":"pesaturvallisuus","agent_name":"Pesäturvallisuuspäällikkö (karhut ym.)","version":"1.0.0","last_updated":"2026-02-21"},
    "ASSUMPTIONS":["Karhu- ja mäyrävahinkojen ehkäisy mehiläistarhoilla","Sähköaita ensisijainen suojakeino","Korvenranta + kaikki muut tarha-sijainnit"],
    "DECISION_METRICS_AND_THRESHOLDS":{
        "electric_fence_voltage_kv":{"value":"4-7 kV (minimi 3 kV, alle → ei estä karhua)","action":"Mittaa vähintään 2x/kk, ennen karhukautta (touko) viikoittain","source":"src:PETU1"},
        "fence_ground_resistance_ohm":{"value":"<300 Ω","action":">300 Ω → paranna maadoitusta (lisää sauvoja, kastele)","source":"src:PETU1"},
        "bear_damage_radius_km":{"value":5,"action":"Karhuhavainto <5 km → nosta varotaso, tarkista aidat","source":"src:PETU2"},
        "fence_height_cm":{"value":"90-120","note":"Karhulle riittävä, alin lanka 20 cm maasta","source":"src:PETU1"},
        "grass_under_fence_max_cm":{"value":10,"action":"Ruoho >10 cm → niitä, vuotaa maahan","source":"src:PETU1"}
    },
    "KNOWLEDGE_TABLES":{"threats":[
        {"threat":"Karhu","severity":"KRIITTINEN","damage":"Pesät tuhoutuvat täysin","protection":"Sähköaita","source":"src:PETU2"},
        {"threat":"Mäyrä","severity":"KESKITASO","damage":"Kaivaa pesän alta","protection":"Sähköaita + verkko maahan","source":"src:PETU1"},
        {"threat":"Tikka","severity":"MATALA","damage":"Hakkaa pesän kylkeä talvella","protection":"Metalliverkko pesän ympärille","source":"src:PETU1"},
        {"threat":"Hiiret","severity":"MATALA","damage":"Talvipesässä vahakennon tuhoaminen","protection":"Lennonsäätäjä pienennä 6mm","source":"src:PETU1"}
    ]},
    "SEASONAL_RULES":[
        {"season":"Kevät (huhti-touko)","action":"Sähköaidan pystytys/tarkistus ENNEN karhun heräämistä. Jännitemittaus. Maadoituksen testaus.","source":"src:PETU1"},
        {"season":"Kesä","action":"Ruohon niitto aidan alla 2x/kk. Jännite 2x/kk. Riistanvartijalta karhu-info seurantaan.","source":"src:PETU1"},
        {"season":"Syksy","action":"Aita pidetään päällä lokakuun loppuun. Pesäkohtaiset suojaukset talvipesille (hiiri, tikka).","source":"src:PETU1"},
        {"season":"Talvi","action":"Aita voidaan poistaa lumitöiden ajaksi (karhu talviunessa). Hiiriverkon tarkistus.","source":"src:PETU1"}
    ],
    "FAILURE_MODES":[
        {"mode":"Karhu murtautunut aitaan","detection":"Kaatuneet pylväät, tuhoutuneet pesät, karhun jäljet","action":"1. Dokumentoi (valokuva + päiväys), 2. Ilmoita poliisille ja riistanhoitoyhdistykselle, 3. Hae korvaus Maaseutuvirastosta","source":"src:PETU2"},
        {"mode":"Aidan jännite liian matala","detection":"Mittari <3 kV","action":"Tarkista: ruoho, maadoitus, akku/verkkosyöttö, johdon eristeet","source":"src:PETU1"}
    ],
    "COMPLIANCE_AND_LEGAL":{"damage_compensation":"Petoeläinten aiheuttamista mehiläisvahingoista voi hakea korvausta Ruokavirastolta (vahingot ilmoitettava 7 pv sisällä) [src:PETU2]","electric_fence":"Sähköaitapaimenen tulee täyttää SFS-EN 60335-2-76 standardi [src:PETU1]"},
    "UNCERTAINTY_NOTES":["Karhun käyttäytyminen on yksilöllistä — kokenut karhu voi oppia kiertämään aidan."],
    "eval_questions":[{"q":"Mikä on sähköaidan minimijännite?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.electric_fence_voltage_kv.value","source":"src:PETU1"},{"q":"Mikä on maadoituksen maksimiresistanssi?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.fence_ground_resistance_ohm.value","source":"src:PETU1"},{"q":"Milloin aita tarkistetaan useimmin?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.electric_fence_voltage_kv.action","source":"src:PETU1"},{"q":"Mikä on karhuhavainnon hälytysraja (km)?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.bear_damage_radius_km.value","source":"src:PETU2"},{"q":"Mikä on aidan korkeus?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.fence_height_cm.value","source":"src:PETU1"},{"q":"Miksi ruoho pitää leikata aidan alla?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.grass_under_fence_max_cm.action","source":"src:PETU1"},{"q":"Mitä tehdään karhuvahingon jälkeen?","a_ref":"FAILURE_MODES[0].action","source":"src:PETU2"},{"q":"Mistä karhuvahingon korvaus haetaan?","a_ref":"COMPLIANCE_AND_LEGAL.damage_compensation","source":"src:PETU2"},{"q":"Mikä on vahingon ilmoitusaika?","a_ref":"COMPLIANCE_AND_LEGAL.damage_compensation","source":"src:PETU2"},{"q":"Miten matala jännite korjataan?","a_ref":"FAILURE_MODES[1].action","source":"src:PETU1"},
    {"q":"Mikä on karhun uhkataso pesille?","a_ref":"KNOWLEDGE_TABLES.threats[0].severity","source":"src:PETU2"},{"q":"Miten mäyrältä suojaudutaan?","a_ref":"KNOWLEDGE_TABLES.threats[1].protection","source":"src:PETU1"},{"q":"Miten tikka vahingoittaa pesää?","a_ref":"KNOWLEDGE_TABLES.threats[2].damage","source":"src:PETU1"},{"q":"Miten hiiriltä suojaudutaan talvella?","a_ref":"KNOWLEDGE_TABLES.threats[3].protection","source":"src:PETU1"},{"q":"Milloin aita pystytetään keväällä?","a_ref":"SEASONAL_RULES[0].action","source":"src:PETU1"},{"q":"Kuinka usein ruoho niitetään?","a_ref":"SEASONAL_RULES[1].action","source":"src:PETU1"},{"q":"Milloin aita voidaan poistaa?","a_ref":"SEASONAL_RULES[3].action","source":"src:PETU1"},{"q":"Mikä standardi koskee sähköpaimenta?","a_ref":"COMPLIANCE_AND_LEGAL.electric_fence","source":"src:PETU1"},{"q":"Mikä on alin langan korkeus?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.fence_height_cm.note","source":"src:PETU1"},{"q":"Voiko karhu kiertää aidan?","a_ref":"UNCERTAINTY_NOTES","source":"src:PETU2"},
    {"q":"Kenelle karhuvahinko ilmoitetaan?","a_ref":"FAILURE_MODES[0].action","source":"src:PETU2"},{"q":"Mikä on ruohon maksimikorkeus aidan alla?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.grass_under_fence_max_cm.value","source":"src:PETU1"},{"q":"Miten jännite mitataan?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.electric_fence_voltage_kv.action","source":"src:PETU1"},{"q":"Miten maadoitusta parannetaan?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.fence_ground_resistance_ohm.action","source":"src:PETU1"},{"q":"Mikä on mäyrän vahinkotaso?","a_ref":"KNOWLEDGE_TABLES.threats[1].severity","source":"src:PETU1"},{"q":"Mitä talvipesille tehdään syksyllä?","a_ref":"SEASONAL_RULES[2].action","source":"src:PETU1"},{"q":"Milloin aita pidetään viimeksi päällä?","a_ref":"SEASONAL_RULES[2].action","source":"src:PETU1"},{"q":"Mikä on lennonsäätäjän merkitys hiirien torjunnassa?","a_ref":"KNOWLEDGE_TABLES.threats[3].protection","source":"src:PETU1"},{"q":"Miten karhujäljet tunnistetaan?","a_ref":"FAILURE_MODES[0].detection","source":"src:PETU2"},{"q":"Kuka tarkistaa aidan jännitteen?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.electric_fence_voltage_kv.action","source":"src:PETU1"}]
},{"sources":[
    {"id":"src:PETU1","org":"SML / ProAgria","title":"Mehiläistarhan sähköaita","year":2024,"url":None,"supports":"Sähköaitaaminen, jännite, maadoitus, ylläpito."},
    {"id":"src:PETU2","org":"Luonnonvarakeskus (Luke)","title":"Petovahingot ja korvaukset","year":2025,"url":"https://www.luke.fi/fi/tutkimus/suurpetotutkimus","supports":"Karhuvahingot, korvausmenettely."}
]})

print(f"\n✅ Batch 4 valmis: agentit 11-18")
